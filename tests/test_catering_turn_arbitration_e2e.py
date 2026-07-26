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

PHONE = "+15550100001"
CHAT = "15550100001@lid"
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
    # PR-4: accepts the optional message_id (logical_turn_id) the hooks now thread.
    def _canonical(cid, lid, message_id=""):
        w.sends.append({"via": "canonical", "jid": cid, "message": f"inquiry {lid} is with the owner",
                        "logical_turn_id": message_id})
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
    return [s for s in sends if str(s["jid"]).startswith("15550100001")]


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
    # PR-4 (charter §4.3/§4.2): the proposals-generated send row carries the turn's
    # logical_turn_id (= inbound wamid.M1, the --source-message-id default) and a
    # non-empty per-send send_attempt_id.
    gen_rows = _rows_of(sb, "catering_proposals_generated")
    assert len(gen_rows) == 1 and gen_rows[0]["logical_turn_id"] == "wamid.M1"
    assert gen_rows[0]["send_attempt_id"] and gen_rows[0]["outcome"] == "sent"
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
    # PR-4 (B): the select→finalize `--logical-turn-id` FORWARDING is pinned to the
    # inbound id — dropping the arg from select-catering-proposal._finalize() would
    # fail this (the recording tests alone would still pass without it).
    fin_argv = w.finalize_calls[-1]
    assert "--logical-turn-id" in fin_argv, "select forwards --logical-turn-id into the finalize subprocess"
    assert fin_argv[fin_argv.index("--logical-turn-id") + 1] == "wamid.M2", \
        "the forwarded logical_turn_id is the inbound turn's id, so the owner card links to it"
    # The customer selection-ack is traceable: catering_customer_ack_sent with a non-empty
    # bridge outbound id + lead_id — and it is the ONE bounded customer send for the turn.
    ack_sent = _rows_of(sb, "catering_customer_ack_sent")
    assert len(ack_sent) == 1, "one selection-ack audit row"
    assert ack_sent[0]["outbound_message_id"] and ack_sent[0]["outbound_message_id"].startswith("msg_")
    assert ack_sent[0]["lead_id"] == new_lead_id
    # PR-4 (charter §4.2/§4.3): ONE inbound (wamid.M2) drives BOTH the arbitration
    # row and the customer ack — they must share ONE logical_turn_id, and every
    # send row must carry a non-empty send_attempt_id (§4.3 minimum).
    assert ack_sent[0]["logical_turn_id"] == "wamid.M2", "ack links to the msg2 inbound turn"
    assert ack_sent[0]["send_attempt_id"], "ack row carries a non-empty send_attempt_id"
    assert ack_sent[0]["outcome"] == "sent"
    assert selected_rows[0]["logical_turn_id"] == "wamid.M2"
    assert selected_rows[0]["send_attempt_id"], "selection row carries a send_attempt_id"
    assert ack_sent[0]["logical_turn_id"] == selected_rows[0]["logical_turn_id"], \
        "the selection and its customer ack link to ONE logical inbound turn (§4.2)"
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
    # PR-4: the failure row still carries internal identity + a "" provider id (no
    # fabricated outbound id), so the §4.3 invariant holds even on a failed send.
    assert ack_failed[0]["send_attempt_id"], "failed send row still carries a send_attempt_id"
    assert ack_failed[0]["logical_turn_id"] == "wamid.F2"
    assert ack_failed[0].get("provider_message_id", "") == ""
    assert _rows_of(sb, "catering_customer_ack_sent") == [], "no success row when the bridge failed"


# ── PR-4 outbound audit identity (charter §4.3 minimum + §4.2 one-logical-reply) ──
PHONE_DIGITS = "17329837841"


def _run_finalize_autodefault(sb: _Sandbox, code: str, message_id: str,
                              logical_turn_id: str, *, owner_ok: bool = True):
    """Drive the REAL finalize-catering-menu (--auto-default) in-process against the
    sandbox → produces one OWNER-facing catering_menu_finalized row. Only the owner
    bridge POST is stubbed (returns a non-empty owner-card id)."""
    mod = load_script("e2e_arb_fin", SCRIPTS / "finalize-catering-menu")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.MENU_PATH = sb.menu
    mod.LOG_PATH = sb.log
    mod.LOG_LOCK = Path(str(sb.log) + ".lock")
    mod.PROPOSALS_PATH = sb.proposals
    mod.PROPOSALS_LOCK = Path(str(sb.proposals) + ".lock")
    mod.LEDGER_PATH = sb.state / "catering-quote-ledger.json"
    mod.TEMPLATE_DIR = sb.templates
    mod._bridge_post = _capture([], "finalize-owner-card", ok=owner_ok)
    argv = ["finalize-catering-menu", "--code", code,
            "--customer-message-id", message_id, "--logical-turn-id", logical_turn_id,
            "--auto-default"]
    return _run_main(mod, argv)


def _run_send_ack(sb: _Sandbox, name: str, jid: str, logical_turn_id: str,
                  *, lead_id: str = "", ok: bool = True):
    """Drive the REAL send-catering-ack in-process (bridge POST stubbed)."""
    mod = load_script(name, SCRIPTS / "send-catering-ack")
    mod.LOG_PATH = sb.log
    mod._bridge_post = _capture([], "send-catering-ack", ok=ok)
    argv = ["send-catering-ack", "--customer-jid", jid, "--message-text", "Noted, thank you.",
            "--lead-id", lead_id, "--logical-turn-id", logical_turn_id]
    return _run_main(mod, argv)


def test_customer_and_owner_send_rows_share_one_logical_id_distinct_identity(tmp_path):
    """One inbound turn → the customer ack row (send-catering-ack) and the owner
    card row (finalize) share ONE logical_turn_id, yet carry DISTINCT identities
    (different provider ids AND different send_attempt_ids AND different row types).
    Closes the CAT-INV-09 auditability half (separate customer/owner identities)
    and the §4.2 one-logical-reply linkage across the two send classes."""
    sb = _build_sandbox(tmp_path)
    leads = json.loads(sb.leads.read_text())
    leads["leads"].append({
        "lead_id": "L0090", "status": "AWAITING_OWNER_APPROVAL", "customer_phone": "+" + PHONE_DIGITS,
        "customer_name": None, "raw_inquiry": "party of 40", "original_message_id": "wamid.SEED.L0090",
        "created_at": "2026-07-26T00:00:00+00:00", "updated_at": "2026-07-26T00:00:00+00:00",
        "extracted": {"headcount": 40, "event_date": None, "event_time": None, "menu_preferences": [],
                      "dietary_restrictions": [], "delivery_or_pickup": "unknown", "budget_hint_usd": None,
                      "notes": "", "off_menu_items": []},
        "quote_text": "pending", "owner_approval_code": "#FZNRT",
    })
    sb.leads.write_text(json.dumps(leads), encoding="utf-8")

    LTID = "wamid.OWNTURN"
    rc, _out, err = _run_finalize_autodefault(sb, "#FZNRT", LTID, LTID)
    assert rc == 0, f"finalize exit={rc}; stderr={err[:500]}"
    fin = _rows_of(sb, "catering_menu_finalized")
    assert len(fin) == 1, "one owner-card finalize row"
    owner_row = fin[0]
    assert owner_row["outcome"] == "finalized"
    assert owner_row["owner_card_outbound_id"], "owner card sent with a provider id"
    assert owner_row["logical_turn_id"] == LTID and owner_row["send_attempt_id"]

    rc2, _out2, err2 = _run_send_ack(sb, "e2e_arb_ack_share", PHONE_DIGITS + "@s.whatsapp.net", LTID,
                                     lead_id="L0090")
    assert rc2 == 0, f"send-ack exit={rc2}; stderr={err2[:500]}"
    ack = _rows_of(sb, "catering_customer_ack_sent")
    assert len(ack) == 1, "one customer ack row"
    cust_row = ack[0]
    assert cust_row["logical_turn_id"] == LTID and cust_row["send_attempt_id"]

    # (a) one inbound → the customer AND owner send rows link to ONE logical id.
    assert owner_row["logical_turn_id"] == cust_row["logical_turn_id"] == LTID
    # (b) distinct identities: different provider ids AND different send_attempt_ids.
    assert owner_row["owner_card_outbound_id"] != cust_row["outbound_message_id"]
    assert owner_row["send_attempt_id"] != cust_row["send_attempt_id"]
    # (c) distinct row TYPES (customer vs owner outbound identity, CAT-INV-09).
    assert owner_row["type"] == "catering_menu_finalized"
    assert cust_row["type"] == "catering_customer_ack_sent"


def test_transport_split_links_to_one_logical_id(tmp_path):
    """CAT-INV-10: a transport split (the SAME logical reply sent to a phone JID
    AND a LID JID) preserves ONE logical outbound identity — both send rows share
    the inbound's logical_turn_id — while each transport send has its own
    non-empty send_attempt_id and distinct destination (no unlinked double-send)."""
    sb = _build_sandbox(tmp_path)
    LTID = "wamid.SPLIT"
    r1 = _run_send_ack(sb, "e2e_arb_ack_split_phone", PHONE_DIGITS + "@s.whatsapp.net", LTID)
    r2 = _run_send_ack(sb, "e2e_arb_ack_split_lid", PHONE_DIGITS + "@lid", LTID)
    assert r1[0] == 0 and r2[0] == 0, "both transport sends succeed"
    ack = _rows_of(sb, "catering_customer_ack_sent")
    assert len(ack) == 2, "two transport sends of one logical reply"
    # Both link to ONE logical inbound id (the linkage CAT-INV-10 requires).
    assert {r["logical_turn_id"] for r in ack} == {LTID}
    # Each transport send carries its OWN non-empty, distinct send_attempt_id.
    attempts = [r["send_attempt_id"] for r in ack]
    assert all(attempts), "every transport send row carries a send_attempt_id"
    assert attempts[0] != attempts[1], "genuinely distinct sends → distinct send_attempt_ids"
    # Distinct transport destinations under one logical identity.
    assert {r["customer_jid"] for r in ack} == {PHONE_DIGITS + "@s.whatsapp.net", PHONE_DIGITS + "@lid"}


def _run_apply_approve(sb: _Sandbox, module_name: str, code: str, logical_turn_id: str,
                       *, bridge_ok: bool):
    """Drive the REAL apply-catering-owner-decision approve (--quote-from-lead-state,
    so no stdin) in-process against the sandbox; the customer quote bridge POST is
    stubbed to succeed or fail. A fresh module per call so the two invocations of a
    reconcile-retry are genuinely separate processes-of-record sharing the sandbox
    log/state (the retry reuse must survive across invocations, via the anchor)."""
    mod = load_script(module_name, SCRIPTS / "apply-catering-owner-decision")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.LOG_PATH = sb.log
    mod.LEDGER_PATH = sb.state / "catering-quote-ledger.json"
    mod._bridge_post = _capture([], "apply-owner-decision", ok=bridge_ok)
    argv = ["apply-catering-owner-decision", "--code", code, "--decision", "approve",
            "--sender-role", "owner", "--quote-from-lead-state",
            "--logical-turn-id", logical_turn_id]
    return _run_main(mod, argv)


def _seed_finalized_lead(sb: _Sandbox, lead_id: str, code: str) -> None:
    leads = json.loads(sb.leads.read_text())
    leads["leads"].append({
        "lead_id": lead_id, "status": "CUSTOMER_FINALIZED", "customer_phone": "+" + PHONE_DIGITS,
        "customer_name": "Test", "raw_inquiry": "wedding for 40 guests on 2026-08-08",
        "original_message_id": f"wamid.SEED.{lead_id}",
        "created_at": "2026-07-26T00:00:00+00:00", "updated_at": "2026-07-26T00:00:00+00:00",
        "customer_finalized_at": "2026-07-26T00:05:00+00:00",
        "extracted": {"headcount": 40, "event_date": "2026-08-08", "event_time": None,
                      "menu_preferences": [], "dietary_restrictions": [], "delivery_or_pickup": "unknown",
                      "budget_hint_usd": None, "notes": "", "off_menu_items": []},
        "selected_items": [{"name": "Idly (3 PCS)", "qty": 40, "price_usd": 6}],
        "quote_total_usd": 240,
        "quote_text": "pending", "owner_approval_code": code,
    })
    sb.leads.write_text(json.dumps(leads), encoding="utf-8")


def test_quote_delivery_carries_identity_and_retry_reuses_send_attempt_id(tmp_path):
    """apply-catering-owner-decision (the customer QUOTE delivery, money-adjacent):
    the quote-sent row carries logical_turn_id + send_attempt_id, and a reconcile-
    RETRY of the SAME logical send REUSES the same send_attempt_id (minted at the
    CateringQuoteAttempted anchor). Mutation-sensitive: if the retry minted a fresh
    id instead of reusing the anchor's, the final equality assertion fails."""
    sb = _build_sandbox(tmp_path)
    _seed_finalized_lead(sb, "L0091", "#PAYRZ")
    LTID = "wamid.APPROVE1"

    # Invocation 1: bridge FAILS → anchors written (unknown + failed), NO quote_sent.
    rc1, _o1, err1 = _run_apply_approve(sb, "e2e_arb_apply_1", "#PAYRZ", LTID, bridge_ok=False)
    assert rc1 == 6, f"first approve should exit DEPENDENCY_DOWN on bridge fail; rc={rc1} err={err1[:400]}"
    attempted_1 = _rows_of(sb, "catering_quote_attempted")
    assert attempted_1, "the ATTEMPTED anchor(s) were written on the first (failed) attempt"
    anchor_ids = {r["send_attempt_id"] for r in attempted_1}
    assert len(anchor_ids) == 1 and "" not in anchor_ids, "all first-attempt anchors share ONE non-empty send_attempt_id"
    minted_id = anchor_ids.pop()
    assert all(r["logical_turn_id"] == LTID for r in attempted_1), "anchors carry the logical_turn_id"
    assert _rows_of(sb, "catering_quote_sent") == [], "no quote delivered yet (bridge failed)"

    # Invocation 2: bridge SUCCEEDS → the retry reuses the anchor's send_attempt_id.
    rc2, _o2, err2 = _run_apply_approve(sb, "e2e_arb_apply_2", "#PAYRZ", LTID, bridge_ok=True)
    assert rc2 == 0, f"retry approve should succeed; rc={rc2} err={err2[:400]}"
    quote_sent = _rows_of(sb, "catering_quote_sent")
    assert len(quote_sent) == 1, "exactly one customer quote delivered across the retry"
    qs = quote_sent[0]
    assert qs["logical_turn_id"] == LTID and qs["send_attempt_id"], "quote-sent row carries both ids (§4.3)"
    assert qs["outcome"] == "sent"
    # THE mutation-sensitive assertion: the delivered quote reuses the SAME id minted
    # at the first attempt's anchor (retries reuse the logical send identity).
    assert qs["send_attempt_id"] == minted_id, \
        "reconcile-retry REUSES the anchor's send_attempt_id (not a fresh mint)"
    # Every anchor across both invocations shares that one id (no drift).
    assert {r["send_attempt_id"] for r in _rows_of(sb, "catering_quote_attempted")} == {minted_id}


def test_initial_inquiry_ack_carries_identity(tmp_path):
    """create-catering-lead initial-inquiry customer ack (F14 proposal send) carries
    logical_turn_id (= the inbound --message-id) + a non-empty send_attempt_id."""
    sb = _build_sandbox(tmp_path)
    sends: list = []
    trigger = _shim_trigger_create_lead(sb, sends)
    ok, _out = trigger("+" + PHONE_DIGITS, "Test", "wedding for 40 guests", "wamid.INIT",
                       extracted_fields={"headcount": 40})
    assert ok, f"create-catering-lead should succeed: {_out}"
    ack = _rows_of(sb, "catering_customer_ack_sent")
    assert len(ack) == 1, "one initial-inquiry customer ack row"
    assert ack[0]["logical_turn_id"] == "wamid.INIT", "ack links to the inbound message id"
    assert ack[0]["send_attempt_id"], "initial-ack row carries a non-empty send_attempt_id"
    assert ack[0]["outcome"] == "sent"
    # PR-4 (§4.3): the OWNER approval-card send of the SAME inbound is now audited
    # too, with DISTINCT owner identity (its own send_attempt_id) sharing the ack's
    # logical_turn_id — mirrors the customer/owner split (CAT-INV-09).
    owner_card = _rows_of(sb, "catering_owner_approval_card_sent")
    assert len(owner_card) == 1, "the owner approval-card send is now audited (was unaudited)"
    assert owner_card[0]["owner_card_outbound_id"], "owner card row records the provider id"
    assert owner_card[0]["logical_turn_id"] == "wamid.INIT", "owner card shares the inbound logical id"
    assert owner_card[0]["send_attempt_id"], "owner card carries its own send_attempt_id"
    assert owner_card[0]["send_attempt_id"] != ack[0]["send_attempt_id"], \
        "owner card and customer ack are DISTINCT sends → distinct send_attempt_ids"
    assert owner_card[0]["outcome"] == "sent"


def test_quote_sent_lead_missing_divergence_row_carries_identity(tmp_path):
    """The post-bridge divergence row (customer GOT the quote but the lead vanished
    between the two locks) IS a real customer-send record — it must carry
    logical_turn_id + the quote attempt's send_attempt_id. Drives the REAL apply
    approve where the bridge send removes the lead as a side effect, forcing the
    matched_idx=None divergence branch that emits CateringQuoteSentLeadMissing."""
    sb = _build_sandbox(tmp_path)
    _seed_finalized_lead(sb, "L0600", "#PAYRZ")
    LTID = "wamid.DIVERGE"
    mod = load_script("e2e_diverge_apply", SCRIPTS / "apply-catering-owner-decision")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.LOG_PATH = sb.log
    mod.LEDGER_PATH = sb.state / "catering-quote-ledger.json"

    def _bp_then_vanish(jid, message):
        # customer receives the quote, but the lead disappears before the re-load
        sb.leads.write_text(json.dumps({"schema_version": 1, "leads": []}), encoding="utf-8")
        return True, "msg_diverge_0001"
    mod._bridge_post = _bp_then_vanish
    argv = ["apply-catering-owner-decision", "--code", "#PAYRZ", "--decision", "approve",
            "--sender-role", "owner", "--quote-from-lead-state", "--logical-turn-id", LTID]
    rc, _out, _err = _run_main(mod, argv)

    miss = _rows_of(sb, "catering_quote_sent_lead_missing")
    assert len(miss) == 1, "the divergence audit row was written"
    assert miss[0]["outbound_message_id"] == "msg_diverge_0001", "records the delivered provider id"
    assert miss[0]["logical_turn_id"] == LTID, "divergence row links to the owner-approval inbound"
    assert miss[0]["send_attempt_id"], "divergence row carries the send identity"
    anchors = _rows_of(sb, "catering_quote_attempted")
    assert miss[0]["send_attempt_id"] in {a["send_attempt_id"] for a in anchors}, \
        "divergence row REUSES the same send_attempt_id as this quote's attempt anchors"


# ── (A) front-brain egress identity (safe_io.front_brain_screen_gateway_send) ──
def test_front_brain_reply_composed_carries_send_attempt_id(tmp_path, monkeypatch):
    """When the front-brain gateway screen ADMITS a send (flag+allowlist on), the
    emitted front_brain_reply_composed row carries a non-empty send_attempt_id, and
    its logical_turn_id is the #643 turn id when that context is present ("" when
    absent). Mutation-sensitive: removing either stamp fails an assertion. Runs
    in-process under the fcntl stub (ensure_fcntl_stub at module load)."""
    import safe_io  # noqa: E402  # importable here via the fcntl stub + PLATFORM path
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "30")
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "4.0")
    log = tmp_path / "decisions.log"
    monkeypatch.setenv("SHIFT_AGENT_DECISIONS_LOG_PATH", str(log))
    chat = "17329837841@c.us"
    clean = "Happy to help with that flyer! What should it promote?"

    def _composed():
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if r["type"] == "front_brain_reply_composed"]

    # (1) admitted send, NO #643 turn context → non-empty send_attempt_id, logical_turn_id "".
    safe_io._TURN_SEND_BUDGET.set(None)
    out = safe_io.front_brain_screen_gateway_send(chat, clean)
    assert out == clean, "a clean composed reply is admitted unchanged"
    comp = _composed()
    assert len(comp) == 1 and comp[0]["template_fallback"] is False
    assert comp[0]["send_attempt_id"], "admitted front-brain send stamps a non-empty send_attempt_id"
    assert comp[0]["logical_turn_id"] == "", "no #643 turn context → logical_turn_id is empty"

    # (2) with a #643 turn context present → logical_turn_id == that frozen turn id.
    token = safe_io._TURN_SEND_BUDGET.set(safe_io._TurnSendBudget("wamid.FBTURN", 5, 2))
    try:
        out2 = safe_io.front_brain_screen_gateway_send(chat, clean)
    finally:
        safe_io._TURN_SEND_BUDGET.reset(token)
    assert out2 == clean
    comp2 = _composed()
    assert comp2[-1]["logical_turn_id"] == "wamid.FBTURN", \
        "the #643 per-inbound turn id is stamped as the logical_turn_id"
    assert comp2[-1]["send_attempt_id"], "still stamps a non-empty send_attempt_id under a turn context"
    assert comp2[-1]["send_attempt_id"] != comp[0]["send_attempt_id"], "one send_attempt_id per screened send"


# ── (C / N1) sweeping backstop: EVERY successful customer/owner send row is stamped ──
def _seed_awaiting_lead(sb: _Sandbox, lead_id: str, code: str) -> None:
    leads = json.loads(sb.leads.read_text())
    leads["leads"].append({
        "lead_id": lead_id, "status": "AWAITING_OWNER_APPROVAL", "customer_phone": "+" + PHONE_DIGITS,
        "customer_name": "Test", "raw_inquiry": "wedding for 40 guests", "original_message_id": f"wamid.SEED.{lead_id}",
        "created_at": "2026-07-26T00:00:00+00:00", "updated_at": "2026-07-26T00:00:00+00:00",
        "extracted": {"headcount": 40, "event_date": "2026-08-08", "event_time": None, "menu_preferences": [],
                      "dietary_restrictions": [], "delivery_or_pickup": "unknown", "budget_hint_usd": None,
                      "notes": "", "off_menu_items": []},
        "quote_text": "pending", "owner_approval_code": code,
    })
    sb.leads.write_text(json.dumps(leads), encoding="utf-8")


def _seed_cross_category_sent_set(sb: _Sandbox, lead_id: str) -> None:
    """A SENT set whose option 1 holds an appetizer and option 2 holds a main, so a
    'option 1 starters + option 2 mains' request resolves to a deterministic MERGE."""
    sb.proposals.write_text(json.dumps({"schema_version": 1, "next_sequence": 10, "sets": [{
        "proposal_set_id": f"CPS-{lead_id}-000009", "lead_id": lead_id, "status": "SENT",
        "created_at": "2026-07-26T00:00:00+00:00", "sent_at": "2026-07-26T00:01:00+00:00",
        "outbound_message_id": "seed_out", "source_message_id": "seed_src", "request_text": "two ideas",
        "options": [{"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
                     "item_names": ["Idly (3 PCS)"]},
                    {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
                     "item_names": ["Poori Goat Curry"]}],
        "selected_option_id": None, "failure_reason": "",
    }]}), encoding="utf-8")


def _run_recompose_merge(sb: _Sandbox, module_name: str, lead_id: str, message_id: str):
    mod = load_script(module_name, SCRIPTS / "create-catering-proposal-options")
    mod.PROPOSALS_PATH = sb.proposals
    mod.PROPOSALS_LOCK = Path(str(sb.proposals) + ".lock")
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.MENU_PATH = sb.menu
    mod.LOG_PATH = sb.log
    mod.LOG_LOCK = Path(str(sb.log) + ".lock")
    mod._bridge_post = _capture([], "recompose")
    argv = ["create-catering-proposal-options", "--lead-id", lead_id,
            "--customer-jid", PHONE_DIGITS + "@lid", "--source-message-id", message_id,
            "--logical-turn-id", message_id, "--request-text",
            "Can you do option 1 starters and option 2 mains?", "--recompose-from-sent"]
    return _run_main(mod, argv)


def test_every_successful_catering_send_row_carries_identity(tmp_path):
    """N1 backstop (repo rule: every documented invariant gets a test that fails if
    violated). Drives one producer per successful customer/owner send row TYPE into
    isolated sandboxes, unions the resulting decisions.log rows, and asserts that
    EVERY row of an enumerated successful-send type carries a non-empty
    send_attempt_id AND logical_turn_id — plus that each enumerated type was actually
    produced (non-vacuous). Adding a new send row without identity fails this."""
    EXPECTED_SEND_TYPES = {
        "catering_customer_ack_sent",           # create-catering-lead initial ack (+ select ack)
        "catering_owner_approval_card_sent",    # create-catering-lead OWNER approval card
        "catering_proposals_generated",         # create-catering-proposal-options
        "catering_recomposed_menu_sent",        # create-catering-proposal-options --recompose-from-sent
        "catering_menu_finalized",              # finalize-catering-menu owner card (outcome=finalized)
        "catering_quote_sent",                  # apply-catering-owner-decision customer quote delivery
    }
    seen: set[str] = set()
    send_rows: list[dict] = []

    def _harvest(sb: _Sandbox) -> None:
        for r in read_log_rows(sb.log):
            t = r.get("type")
            if t not in EXPECTED_SEND_TYPES:
                continue
            if t == "catering_menu_finalized" and r.get("outcome") != "finalized":
                continue  # rejected_quote_mismatch is not a successful send
            seen.add(t)
            send_rows.append(r)

    # producer 1 — initial-inquiry ack (create-catering-lead F14 proposal send)
    sb1 = _build_sandbox(tmp_path / "p1_ack")
    ok, _o = _shim_trigger_create_lead(sb1, [])("+" + PHONE_DIGITS, "Test", "wedding for 40",
                                                "wamid.C_ACK", extracted_fields={"headcount": 40})
    assert ok, "create-catering-lead producer failed"
    _harvest(sb1)

    # producer 2 — proposals generated (deterministic auto-generate from menu)
    sb2 = _build_sandbox(tmp_path / "p2_prop")
    _seed_awaiting_lead(sb2, "L0200", "#PRPZ2")
    rc = _shim_invoke_create_proposals(sb2, [])("L0200", PHONE_DIGITS + "@lid", "wamid.C_PROP", "two sample menus")
    assert rc == 0, f"create-catering-proposal-options producer failed rc={rc}"
    _harvest(sb2)

    # producer 3 — recomposed menu (mix-and-match of already-SENT options)
    sb3 = _build_sandbox(tmp_path / "p3_reco")
    _seed_awaiting_lead(sb3, "L0300", "#RECZ2")
    _seed_cross_category_sent_set(sb3, "L0300")
    rc3, _o3, err3 = _run_recompose_merge(sb3, "e2e_sweep_reco", "L0300", "wamid.C_RECO")
    assert rc3 == 0, f"recompose producer failed rc={rc3} err={err3[:300]}"
    _harvest(sb3)

    # producer 4 — owner card finalize (auto-default)
    sb4 = _build_sandbox(tmp_path / "p4_fin")
    _seed_awaiting_lead(sb4, "L0400", "#FNRZ2")
    rc4, _o4, err4 = _run_finalize_autodefault(sb4, "#FNRZ2", "wamid.C_FIN", "wamid.C_FIN")
    assert rc4 == 0, f"finalize producer failed rc={rc4} err={err4[:300]}"
    _harvest(sb4)

    # producer 5 — customer quote delivery (apply-catering-owner-decision approve)
    sb5 = _build_sandbox(tmp_path / "p5_quote")
    _seed_finalized_lead(sb5, "L0500", "#PAYRZ")
    rc5, _o5, err5 = _run_apply_approve(sb5, "e2e_sweep_apply", "#PAYRZ", "wamid.C_QUOTE", bridge_ok=True)
    assert rc5 == 0, f"apply-approve producer failed rc={rc5} err={err5[:300]}"
    _harvest(sb5)

    # THE invariant: every successful customer/owner send row carries both ids.
    assert send_rows, "the sweep must be non-vacuous"
    for r in send_rows:
        assert r.get("send_attempt_id"), f"{r['type']} row missing a non-empty send_attempt_id"
        assert r.get("logical_turn_id"), f"{r['type']} row missing a non-empty logical_turn_id"

    # coverage backstop: every enumerated successful-send type was actually produced,
    # so a future unstamped emitter of any of these types would be caught here.
    assert EXPECTED_SEND_TYPES <= seen, f"missing producers for: {sorted(EXPECTED_SEND_TYPES - seen)}"
