"""End-to-end turn-arbitration transcript — REAL cf-router dispatch → REAL catering
scripts (UNMOCKED resolver), 2026-07-26 live 3-message transcript.

Unlike test_catering_pra_reachability.test_live_transcript_* (which monkeypatches
invoke_select_catering_proposal), this drives the ACTUAL select-catering-proposal script
in-process against a sandbox state dir, so message 2's "records Option 2" runs through the
REAL `_resolve_selection` (and create-catering-lead / create-catering-proposal-options run
for real too). The only stubbed seams are OUTSIDE the resolver: the WhatsApp bridge POST
(captured, returns a non-empty outbound id), the identify-sender identity subprocess, the
downstream finalize-catering-menu subprocess (captured — it has its own tests), and the R2A
amendment sidecar (captured, to count captures without the sidecar's own I/O).

Runs cross-platform via the fcntl stub — same script code paths, one interpreter (the
E2E-harness convention: "Windows/fcntl forces in-process over subprocess; same code paths").
The transcript is fully deterministic through cf-router, so NO LLM/network is involved. This
is the unmocked companion to the pra transcript unit test (which is kept as the fast twin).

End-state framing per message: msg1 opens exactly one distinct lead (NOT an amendment);
msg2 records the selection once (NOT an amendment); msg3 is ONE benign R2A follow-up —
idempotent (no re-select/finalize/set/owner-card, no quote/selection regression) AND
distinguishable from the selection (a follow-up suppression recorded exactly once, not a
catering_proposal_selected).
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLATFORM = SRC / "platform"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
TEMPLATES_SRC = SRC / "agents" / "catering" / "templates"
MENU_FIXTURE = Path(__file__).resolve().parent / "e2e" / "fixtures" / "catering-menu-e2e.json"
for _p in (str(PLATFORM), str(SRC), str(REPO / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

PHONE = "+17329837841"
CHAT = "17329837841@lid"
OWNER_JID = "19045550100@s.whatsapp.net"


# ── sandbox ──────────────────────────────────────────────────────────────────
class _Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state"
        self.logs = root / "logs"
        self.templates = root / "templates"
        self.config = root / "config.yaml"
        self.leads = self.state / "catering-leads.json"
        self.proposals = self.state / "catering-proposals.json"
        self.menu = self.state / "catering-menu.json"
        self.log = self.logs / "decisions.log"


def _build_sandbox(root: Path) -> _Sandbox:
    sb = _Sandbox(root)
    for d in (sb.state, sb.logs, sb.templates):
        d.mkdir(parents=True, exist_ok=True)
    for f in TEMPLATES_SRC.iterdir():
        if f.is_file():
            (sb.templates / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Triveni Test", "location_id": "loc_e2e", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": OWNER_JID},
        "limits": {}, "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"}, "catering": {"enabled": True},
    }
    sb.config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    # Stale open lead L0017 — materially DIFFERENT event (60 guests, June) from the incident.
    leads = {"schema_version": 1, "leads": [{
        "lead_id": "L0017", "status": "AWAITING_OWNER_APPROVAL", "customer_phone": PHONE,
        "customer_name": None, "raw_inquiry": "(seed) earlier june inquiry",
        "original_message_id": "wamid.SEED.L0017", "created_at": "2026-06-09T00:00:00+00:00",
        "updated_at": "2026-06-09T00:00:00+00:00",
        "extracted": {"headcount": 60, "event_date": "2026-06-28", "event_time": None,
                      "menu_preferences": [], "dietary_restrictions": [], "delivery_or_pickup": "unknown",
                      "budget_hint_usd": None, "notes": "seed", "off_menu_items": []},
        "quote_text": "Seed quote pending owner review.", "owner_approval_code": "#GEMAZ",
    }]}
    sb.leads.write_text(json.dumps(leads, indent=2), encoding="utf-8")
    sb.menu.write_text(MENU_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    sb.proposals.write_text(json.dumps({"schema_version": 1, "next_sequence": 1, "sets": []}), encoding="utf-8")
    sb.log.write_text("", encoding="utf-8")
    return sb


# ── in-process real-script runner ────────────────────────────────────────────
def _run_main(mod, argv):
    old_argv = sys.argv
    sys.argv = argv
    out, err = io.StringIO(), io.StringIO()
    rc = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    return rc, out.getvalue(), err.getvalue()


def _capture(sends: list, via: str, *, ok: bool = True):
    def _bp(jid: str, message: str):
        sends.append({"via": via, "jid": jid, "message": message})
        if ok:
            return True, f"msg_{via}_{len(sends):04d}"
        return False, "bridge_unreachable: connection refused"
    return _bp


def _shim_trigger_create_lead(sb: _Sandbox, sends: list):
    def _t(customer_phone, customer_name, raw_inquiry, message_id,
           extracted_fields=None, suppress_customer_ack=False):
        fields = {"headcount": None, "event_date": None, "event_time": None, "menu_preferences": [],
                  "off_menu_items": [], "dietary_restrictions": [], "delivery_or_pickup": "unknown",
                  "budget_hint_usd": None, "notes": "(e2e)"}
        if extracted_fields:
            fields.update(extracted_fields)
        mod = load_script("e2e_arb_ccl", SCRIPTS / "create-catering-lead")
        mod.CONFIG_PATH = sb.config
        mod.LEADS_PATH = sb.leads
        mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
        mod.LOG_PATH = sb.log
        mod.TEMPLATE_DIR = sb.templates
        mod.MENU_PATH = sb.menu
        mod._bridge_post = _capture(sends, "create-catering-lead")
        argv = ["create-catering-lead", "--customer-phone", customer_phone,
                "--customer-name", customer_name or "", "--raw-inquiry", raw_inquiry[:1000],
                "--message-id", message_id, "--fields-json", json.dumps(fields)]
        if suppress_customer_ack:
            argv.append("--suppress-customer-ack")
        rc, out, err = _run_main(mod, argv)
        if rc == 0:
            return True, (out.strip().splitlines()[-1] if out.strip() else "")
        return False, f"exit={rc} stderr={err[:500]}"
    return _t


def _shim_invoke_create_proposals(sb: _Sandbox, sends: list):
    def _i(lead_id, chat_id, message_id, text):
        mod = load_script("e2e_arb_cpo", SCRIPTS / "create-catering-proposal-options")
        mod.PROPOSALS_PATH = sb.proposals
        mod.PROPOSALS_LOCK = Path(str(sb.proposals) + ".lock")
        mod.LEADS_PATH = sb.leads
        mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
        mod.MENU_PATH = sb.menu
        mod.LOG_PATH = sb.log
        mod.LOG_LOCK = Path(str(sb.log) + ".lock")
        mod._bridge_post = _capture(sends, "create-catering-proposal-options")
        mod._notify_owner_generation_failed = lambda *a, **k: None
        argv = ["create-catering-proposal-options", "--lead-id", lead_id, "--customer-jid", chat_id,
                "--source-message-id", message_id, "--request-text", text, "--auto-generate-from-menu"]
        rc, _out, _err = _run_main(mod, argv)
        return rc
    return _i


def _shim_invoke_select(sb: _Sandbox, sends: list, finalize_calls: list, *, ack_ok: bool = True):
    def _s(lead_id, chat_id, message_id, text):
        mod = load_script("e2e_arb_sel", SCRIPTS / "select-catering-proposal")
        mod.PROPOSALS_PATH = sb.proposals
        mod.PROPOSALS_LOCK = Path(str(sb.proposals) + ".lock")
        mod.LEADS_PATH = sb.leads
        mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
        mod.MENU_PATH = sb.menu
        mod.LOG_PATH = sb.log
        mod.LOG_LOCK = Path(str(sb.log) + ".lock")
        mod._bridge_post = _capture(sends, "select-catering-proposal", ok=ack_ok)

        # Capture the downstream finalize-catering-menu / notify-owner subprocesses (they
        # have their own tests). Returning rc=0 makes finalize "succeed" so the real
        # selection state machine advances to SELECTED — the resolver + claim/finish +
        # ack + audit all run for real; only this one subprocess boundary is stubbed.
        def _fake_run(argv, **kwargs):
            finalize_calls.append([str(part) for part in argv])

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""
            return _R()
        # `mod.subprocess` is the shared stdlib module — restore its `run` after this
        # script's main() so the patch never leaks into other tests in the same process.
        real_run = mod.subprocess.run
        mod.subprocess.run = _fake_run
        argv = ["select-catering-proposal", "--lead-id", lead_id, "--customer-jid", chat_id,
                "--customer-message-id", message_id, "--selection-text", text]
        try:
            rc, _out, _err = _run_main(mod, argv)
        finally:
            mod.subprocess.run = real_run
        return rc
    return _s


# ── plugin loader (real hooks + actions, package trick for `from . import`) ──
def _load_plugin():
    import importlib.machinery
    import importlib.util
    pkg = "cf_router_arb_e2e_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(spec)

    def _load(name):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        sp = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(sp)
        sys.modules[full] = mod
        loader.exec_module(mod)
        return mod

    actions = _load("actions")
    hooks = _load("hooks")
    return hooks, actions


class _Wiring:
    def __init__(self):
        self.sends: list = []
        self.finalize_calls: list = []
        self.amend_captures: list = []


def _wire(hooks, actions, sb: _Sandbox, monkeypatch, *, ack_ok: bool = True) -> _Wiring:
    from catering_amendments import CaptureResult
    w = _Wiring()
    hooks.F7_PROPOSAL_BRANCH_ENABLED = True
    hooks.F7_PRIMARY_FOLLOWUP_REPLY = True

    # Real state reads point at the sandbox; real audit writes there too.
    actions.LEADS_PATH = sb.leads
    actions.PROPOSALS_PATH = sb.proposals
    actions.MENU_PATH = sb.menu
    actions.LOG_PATH = sb.log
    actions.CONFIG_PATH = sb.config

    # Identity subprocess → deterministic customer (the only external identity seam).
    actions.lid_to_phone_via_identify_sender = lambda cid: (PHONE, "customer")

    # Subprocess-boundary → REAL scripts in-process against the sandbox (resolver unmocked).
    actions.trigger_create_catering_lead = _shim_trigger_create_lead(sb, w.sends)
    actions.invoke_create_catering_proposals = _shim_invoke_create_proposals(sb, w.sends)
    actions.invoke_select_catering_proposal = _shim_invoke_select(sb, w.sends, w.finalize_calls, ack_ok=ack_ok)

    # Canonical follow-up reply (msg3 redundant path) → captured customer send.
    def _canonical(cid, lid):
        w.sends.append({"via": "canonical", "jid": cid, "message": f"inquiry {lid} is with the owner"})
        return True
    actions.send_canonical_followup_reply = _canonical

    # R2A amendment sidecar → captured (count captures without the sidecar's own I/O).
    # MUST use monkeypatch (auto-restored): catering_amendments is a SHARED module (not the
    # per-test-loaded plugin), so a raw assignment would leak this stub into every later
    # test in the process — notably test_cf_router_plugin's Linux-only branch-b tests, which
    # skip on Windows (so the leak is invisible on the dev box but red on Linux CI).
    def _capture_amend(**kw):
        w.amend_captures.append(kw)
        return CaptureResult(ok=True, amendment_id=f"A{len(w.amend_captures):04d}", idempotent=False)
    monkeypatch.setattr(hooks.catering_amendments, "capture_branch_b_amendment", _capture_amend)
    return w


def _event(mid):
    return SimpleNamespace(message_id=mid, chat_id=CHAT, timestamp="1721480400", transport="whatsapp")


def _drive(hooks, actions, text, mid):
    _is_cat, signals = actions.classify_catering(text)
    return hooks._try_f7_primary_intercept(text, CHAT, _event(mid), signals=signals, allow_new_lead=True)


def _customer_sends(sends):
    return [s for s in sends if str(s["jid"]).startswith("17329837841")]


def _rows_of(sb: _Sandbox, type_):
    return [r for r in read_log_rows(sb.log) if r.get("type") == type_]


def _cf_reasons(sb: _Sandbox):
    return [r.get("reason") for r in _rows_of(sb, "cf_router_intercepted")]


# ── the transcript ───────────────────────────────────────────────────────────
MSG1 = "Hello I have a wedding coming up for 180 guests. Please send me two sample menus so I can decide."
MSG2 = "I like Option 2. Can you send me quote and prices."
MSG3 = "Option 2"


def test_live_transcript_unmocked_end_to_end(tmp_path, monkeypatch):
    sb = _build_sandbox(tmp_path)
    hooks, actions = _load_plugin()
    w = _wire(hooks, actions, sb, monkeypatch)

    # ── Message 1: fresh distinct inquiry + proposal ask over the open stale lead ──
    n0 = len(w.sends)
    out1 = _drive(hooks, actions, MSG1, "wamid.M1")
    assert out1 is not None and out1["action"] == "skip"
    leads = json.loads(sb.leads.read_text())["leads"]
    new_leads = [l for l in leads if l["lead_id"] != "L0017"]
    assert len(new_leads) == 1, f"exactly one new lead opened over the stale one: {[l['lead_id'] for l in leads]}"
    new_lead_id = new_leads[0]["lead_id"]
    reasons1 = _cf_reasons(sb)
    assert "f7_fresh_inquiry_new_lead_over_stale" in reasons1
    assert "f7_proposal_request_deterministic_generation" in reasons1
    # A real SENT proposal set now exists for the new lead.
    sets = json.loads(sb.proposals.read_text())["sets"]
    sent = [s for s in sets if s["lead_id"] == new_lead_id and s["status"] == "SENT"]
    assert len(sent) == 1, "one real SENT proposal set generated for the new lead"
    # ONE bounded customer send (F14 suppressed → only the tiered proposal set); NO
    # cross-reference "is this a separate event?" question; not captured as an amendment.
    m1_customer = _customer_sends(w.sends[n0:])
    assert len(m1_customer) == 1, f"message 1 → exactly one customer send: {[s['via'] for s in m1_customer]}"
    assert m1_customer[0]["via"] == "create-catering-proposal-options"
    assert not any("separate event" in s["message"].lower() or "earlier inquiry" in s["message"].lower()
                   for s in w.sends[n0:]), "no cross-reference dup-warning question"
    assert w.amend_captures == [], "the fresh inquiry is NOT captured as an amendment"

    # ── Message 2: compound select+pricing through the REAL resolver ──
    n1 = len(w.sends)
    finals_before = len(w.finalize_calls)
    out2 = _drive(hooks, actions, MSG2, "wamid.M2")
    assert out2 is not None and out2["action"] == "skip" and "selection" in out2["reason"]
    # The REAL _resolve_selection recorded Option 2 exactly once.
    selected_rows = _rows_of(sb, "catering_proposal_selected")
    assert len(selected_rows) == 1, "exactly one catering_proposal_selected through the real resolver"
    assert selected_rows[0]["option_id"] == "2"
    assert selected_rows[0]["lead_id"] == new_lead_id
    sets2 = json.loads(sb.proposals.read_text())["sets"]
    lead_sets = [s for s in sets2 if s["lead_id"] == new_lead_id]
    assert len(lead_sets) == 1, "NO menu resend / duplicate proposal set on selection"
    assert lead_sets[0]["selected_option_id"] == "2" and lead_sets[0]["status"] == "SELECTED"
    # Owner-review advanced exactly once (finalize invoked once for this turn).
    assert len(w.finalize_calls) == finals_before + 1, "finalize/owner-review advances exactly once"
    # The customer selection-ack is traceable: catering_customer_ack_sent with a non-empty
    # bridge outbound id + lead_id — and it is the ONE bounded customer send for the turn.
    ack_sent = _rows_of(sb, "catering_customer_ack_sent")
    assert len(ack_sent) == 1, "one selection-ack audit row"
    assert ack_sent[0]["outbound_message_id"] and ack_sent[0]["outbound_message_id"].startswith("msg_")
    assert ack_sent[0]["lead_id"] == new_lead_id
    m2_customer = _customer_sends(w.sends[n1:])
    assert len(m2_customer) == 1 and m2_customer[0]["via"] == "select-catering-proposal"
    assert w.amend_captures == [], "the selection is NOT captured as an amendment"

    # ── Message 3: redundant "Option 2" → ONE benign R2A follow-up ──
    # Expected final state is a SINGLE benign R2A follow-up that is idempotent AND
    # distinguishable from the msg2 selection+pricing action. It must meet all of:
    #   (1) no re-select / re-finalize; (2) no second proposal set / owner card;
    #   (3) no reopen/regress of the quote or selection state; (4) recorded EXACTLY ONCE
    #   (an R2A amendment-capture); (5) a DIFFERENT action type than the selection (a
    #   follow-up suppression, not a catering_proposal_selected).
    lead_after_m2 = next(l for l in json.loads(sb.leads.read_text())["leads"] if l["lead_id"] == new_lead_id)
    set_after_m2 = next(s for s in json.loads(sb.proposals.read_text())["sets"] if s["lead_id"] == new_lead_id)
    captures_before_m3 = len(w.amend_captures)
    n2 = len(w.sends)
    finals_before3 = len(w.finalize_calls)
    out3 = _drive(hooks, actions, MSG3, "wamid.M3")

    # (1) does NOT select or finalize again.
    assert len(_rows_of(sb, "catering_proposal_selected")) == 1, "no double-select"
    assert len(w.finalize_calls) == finals_before3, "no second finalize invocation"
    # (2) no second proposal set, and no second owner card (no finalize AND no new create-lead).
    sets3 = json.loads(sb.proposals.read_text())["sets"]
    assert len([s for s in sets3 if s["lead_id"] == new_lead_id]) == 1, "no new proposal set"
    assert not any(s["via"] == "create-catering-lead" for s in w.sends[n2:]), "no second owner card"
    # (3) does NOT reopen or regress the selection/quote status — lead + selected set unchanged.
    lead_after_m3 = next(l for l in json.loads(sb.leads.read_text())["leads"] if l["lead_id"] == new_lead_id)
    assert lead_after_m3 == lead_after_m2, "lead quote/selection state unchanged by the redundant follow-up"
    set_after_m3 = next(s for s in sets3 if s["lead_id"] == new_lead_id)
    assert set_after_m3["status"] == "SELECTED" and set_after_m3["selected_option_id"] == "2", \
        "selection stays SELECTED / option 2 — not reopened or regressed"
    assert set_after_m3 == set_after_m2, "the selected proposal set is byte-identical (no regression)"
    # (4) records the follow-up EXACTLY ONCE.
    assert len(w.amend_captures) == captures_before_m3 + 1, "exactly one R2A follow-up recorded on msg3"
    # (5) DISTINGUISHABLE from the msg2 selection: a follow-up suppression (not a selection),
    # whose one bounded customer send is the canonical follow-up reply, not a select-ack.
    assert out3 is not None and "follow-up" in out3["reason"] and "selection" not in out3["reason"]
    m3_customer = _customer_sends(w.sends[n2:])
    assert len(m3_customer) == 1 and m3_customer[0]["via"] == "canonical", \
        "one bounded customer send — the benign R2A follow-up, distinct from the selection ack"

    # No flyer/commerce mutation anywhere in the arbitrated turn.
    all_types = {r.get("type") for r in read_log_rows(sb.log)}
    assert not any(t and (t.startswith("flyer_") or t.startswith("commerce_")) for t in all_types)


def _seed_sent_set(sb: _Sandbox, lead_id: str) -> None:
    """Seed one SENT proposal set (options 1+2 grounded in the real menu) for `lead_id`."""
    items = json.loads(sb.menu.read_text())["items"]
    sb.proposals.write_text(json.dumps({"schema_version": 1, "next_sequence": 10, "sets": [{
        "proposal_set_id": f"CPS-{lead_id}-000009", "lead_id": lead_id, "status": "SENT",
        "created_at": "2026-07-26T00:00:00+00:00", "sent_at": "2026-07-26T00:01:00+00:00",
        "outbound_message_id": "seed_out", "source_message_id": "seed_src", "request_text": "two ideas",
        "options": [{"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
                     "item_names": [items[0]["name"]]},
                    {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
                     "item_names": [items[1]["name"]]}],
        "selected_option_id": None, "failure_reason": "",
    }]}), encoding="utf-8")


def test_selection_ack_bridge_failure_records_ack_failed_metadata_only(tmp_path, monkeypatch):
    """A missing/malformed bridge ack on the selection is metadata-only traceable through
    the REAL script: catering_customer_ack_failed (no fabricated outbound id), and the
    selection is still recorded exactly once (no double-handling)."""
    sb = _build_sandbox(tmp_path)
    # Seed an active lead with a SENT set so the compound selection reaches the ack path.
    leads = json.loads(sb.leads.read_text())
    leads["leads"].append({
        "lead_id": "L0018", "status": "AWAITING_OWNER_APPROVAL", "customer_phone": PHONE,
        "customer_name": None, "raw_inquiry": "wedding 180", "original_message_id": "wamid.SEED.L0018",
        "created_at": "2026-07-26T00:00:00+00:00", "updated_at": "2026-07-26T00:00:00+00:00",
        "extracted": {"headcount": 180, "event_date": None, "event_time": None, "menu_preferences": [],
                      "dietary_restrictions": [], "delivery_or_pickup": "unknown", "budget_hint_usd": None,
                      "notes": "", "off_menu_items": []},
        "quote_text": "pending", "owner_approval_code": "#PREMZ",
    })
    sb.leads.write_text(json.dumps(leads), encoding="utf-8")
    _seed_sent_set(sb, "L0018")

    hooks, actions = _load_plugin()
    w = _wire(hooks, actions, sb, monkeypatch, ack_ok=False)  # bridge POST fails on the ack send

    out = _drive(hooks, actions, MSG2, "wamid.F2")
    assert out is not None and out["action"] == "skip"
    # The REAL selection state machine recorded Option 2 (state write precedes the ack send).
    selected = _rows_of(sb, "catering_proposal_selected")
    assert len(selected) == 1 and selected[0]["option_id"] == "2"
    # The ack SEND failed → metadata-only failure row, no fabricated outbound id, no success row.
    ack_failed = _rows_of(sb, "catering_customer_ack_failed")
    assert len(ack_failed) == 1, "bridge-failed selection ack is traceable as ack_failed"
    assert ack_failed[0]["reason"] == "bridge_unreachable"
    assert "outbound_message_id" not in ack_failed[0], "failure row is metadata-only (no fabricated id)"
    assert _rows_of(sb, "catering_customer_ack_sent") == [], "no success row when the bridge failed"
