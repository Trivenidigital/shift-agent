"""P1-1 — fresh-intent catering escape gate (unit-level).

Exercises `_try_flyer_catering_escape_gate` in isolation with the gate's
dependencies monkeypatched, so every branch of the decision table is
deterministic and network-free. Windows-runnable via the fcntl stub.

The classifiers (`classify_catering` + the flyer-signal helpers) run REAL for
the routing branches, so the pins reflect production classification of the
exact incident message; only the exception + call-count tests wrap/replace
`classify_catering`. The DISPATCH-level wiring proof (gate hoisted BETWEEN the
R2B-1 gate and the flyer active-project arm) is a source-scan so it runs on
every platform.

Reviewer proof obligations covered here: exact-incident replay never queues a
flyer edit · fresh catering with a live project escapes · genuine flyer edit /
bare approval fall through byte-identically · ambiguous → ONE clarification,
no lead / no revision · gate exception → clarification, never a guessed route ·
classify_catering invoked exactly once · no dual (lead + revision) outcome ·
no active project → no-op before classify · F7 decline → None (LLM), not flyer
capture · R2B-1 precedence + placement.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PHONE = "+17329837841"
CHAT = "17329837841@lid"

# The verbatim 5th-reproduction production message (routing-validation P1-1).
INCIDENT = (
    "Hello I have a wedding coming up for 120 guests on August 8th, out of 120 "
    "guests 90 are non-vegetarian and 30 vegetarian. Provide me two best sample "
    "menus of yours , so that I can decide."
)
FRESH_CATERING = (
    "We are planning a birthday party for 60 people next Saturday, can you cater "
    "and give me a quote?"
)
AMBIGUOUS = "make me a flyer and cater for 120 guests at the wedding"


def _load_plugin():
    pkg = "cf_router_p11_pkg"
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

    actions_mod = _load("actions")
    hooks_mod = _load("hooks")
    return hooks_mod, actions_mod


def _event():
    return SimpleNamespace(message_id="wamid.CANARY", timestamp="1721480400", transport="whatsapp")


def _project(status="manual_edit_required", project_id="F0224"):
    return {"project_id": project_id, "status": status}


def _lead(lead_id="L0015", status="AWAITING_OWNER_APPROVAL"):
    return {"lead_id": lead_id, "status": status, "owner_approval_code": "#GEMAZ",
            "customer_phone": PHONE, "created_at": "2026-07-15T00:00:00+00:00"}


class _Spies:
    def __init__(self):
        self.audits = []
        self.sent = []
        self.clar_saved = []
        self.f7_calls = []
        self.flyer_arm_calls = []
        self.update_calls = []
        self.classify_calls = 0


_DEFAULT_PROJECT = object()  # sentinel: "use a live project" vs. explicit None (no project)


def _wire(monkeypatch, hooks_mod, actions_mod, *, role="customer",
          project=_DEFAULT_PROJECT, f7_declines=False, classify=None, open_lead=None):
    """Monkeypatch every dependency the gate touches. Returns a _Spies recorder.
    classify=None keeps the REAL classify_catering (wrapped for call counting).
    project=None means NO active flyer project (scoping no-op).
    open_lead=None means the sender has NO open catering lead (F7's canonical
    lookup is stubbed so the Windows suite never reads the deployed leads file)."""
    s = _Spies()
    if project is _DEFAULT_PROJECT:
        project = _project()

    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, role))
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda p, c: project)
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", lambda p, c: open_lead)
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(actions_mod, "send_flyer_text",
                        lambda cid, txt, **kw: s.sent.append((cid, txt)) or (True, "mid1", ""))
    monkeypatch.setattr(actions_mod, "save_revenue_route_clarification",
                        lambda **kw: s.clar_saved.append(kw))

    def _f7(text, chat_id, event, signals=None, allow_new_lead=True):
        s.f7_calls.append({"text": text, "signals": signals, "allow_new_lead": allow_new_lead})
        if f7_declines:
            return None  # owner (F8 territory) OR create-catering-lead non-zero
        return {"action": "skip", "reason": "cf-router F7 primary: new inquiry L0009"}
    monkeypatch.setattr(hooks_mod, "_try_f7_primary_intercept", _f7)

    # Sentinels — an escaping / clarifying gate must NEVER reach a flyer arm.
    monkeypatch.setattr(hooks_mod, "_try_flyer_active_project_intercept",
                        lambda *a, **k: s.flyer_arm_calls.append(a) or {"action": "skip", "reason": "flyer_arm"})
    monkeypatch.setattr(actions_mod, "invoke_update_flyer_project",
                        lambda *a, **k: s.update_calls.append(a) or (True, "{}"))

    real = actions_mod.classify_catering

    def _count(t):
        s.classify_calls += 1
        return (classify or real)(t)
    monkeypatch.setattr(actions_mod, "classify_catering", _count)
    return s


def _reasons(s):
    return [a["reason"] for a in s.audits]


# ── Escape branch ───────────────────────────────────────────────────────────
def test_incident_replay_escapes_to_catering_never_flyer_queue(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("manual_edit_required", "F0224"))
    out = hooks_mod._try_flyer_catering_escape_gate(INCIDENT, CHAT, _event())
    assert out == {"action": "skip", "reason": "cf-router F7 primary: new inquiry L0009"}
    assert len(s.f7_calls) == 1 and s.f7_calls[0]["allow_new_lead"] is True
    assert _reasons(s) == ["flyer_active_project_catering_intent_escape"]
    assert "flyer_reference_exact_edit_queued" not in _reasons(s)
    # No dual outcome: escape must not also queue a flyer edit / run a flyer arm.
    assert s.update_calls == [] and s.flyer_arm_calls == []


def test_fresh_catering_with_awaiting_approval_project_escapes(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("awaiting_final_approval", "F0231"))
    out = hooks_mod._try_flyer_catering_escape_gate(FRESH_CATERING, CHAT, _event())
    assert out["action"] == "skip" and "F7" in out["reason"]
    assert len(s.f7_calls) == 1
    assert _reasons(s) == ["flyer_active_project_catering_intent_escape"]
    assert s.update_calls == [] and s.flyer_arm_calls == []


# ── Fall-through branch (byte-identical flyer path) ─────────────────────────
def test_genuine_flyer_edit_falls_through_unchanged(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, project=_project("awaiting_final_approval"))
    out = hooks_mod._try_flyer_catering_escape_gate(
        "change the price to $8.99 on the flyer", CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.f7_calls == [] and s.sent == [] and s.audits == []


def test_bare_approval_yes_falls_through_unchanged(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, project=_project("awaiting_final_approval"))
    out = hooks_mod._try_flyer_catering_escape_gate("yes", CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.f7_calls == [] and s.sent == []


# ── Ambiguous → one clarification ───────────────────────────────────────────
def test_ambiguous_flyer_and_catering_sends_one_clarification(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod)
    out = hooks_mod._try_flyer_catering_escape_gate(AMBIGUOUS, CHAT, _event())
    assert out["action"] == "skip" and "clarification" in out["reason"]
    assert len(s.sent) == 1, "exactly one clarification message"
    assert len(s.clar_saved) == 1, "a single pending choice is parked"
    assert _reasons(s) == ["flyer_catering_intent_clarification"]
    assert s.f7_calls == [], "ambiguous must NOT create a catering lead"
    assert s.update_calls == [] and s.flyer_arm_calls == [], "ambiguous must NOT create a revision/queued edit"


# ── Exception → clarification (never a guessed route) ───────────────────────
def test_gate_exception_yields_clarification_never_a_route(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()

    def _boom(_t):
        raise RuntimeError("classifier exploded")
    s = _wire(monkeypatch, hooks_mod, actions_mod, classify=_boom)
    out = hooks_mod._try_flyer_catering_escape_gate(INCIDENT, CHAT, _event())
    assert out["action"] == "skip" and "clarification" in out["reason"]
    assert _reasons(s) == ["flyer_catering_intent_clarification"]
    assert s.f7_calls == [], "an errored gate must NOT guess a catering route"


# ── Single-invocation + scoping + F7-decline invariants ─────────────────────
def test_classify_catering_invoked_exactly_once_on_escape(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod)
    hooks_mod._try_flyer_catering_escape_gate(INCIDENT, CHAT, _event())
    assert s.classify_calls == 1


def test_no_active_flyer_project_is_a_noop_before_classify(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, project=None)
    out = hooks_mod._try_flyer_catering_escape_gate(INCIDENT, CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.f7_calls == [] and s.classify_calls == 0


def test_escape_when_f7_declines_returns_none_not_flyer_capture(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, f7_declines=True)
    out = hooks_mod._try_flyer_catering_escape_gate(INCIDENT, CHAT, _event())
    assert out is None, "F7 declined (owner / lead-create fail) → None (LLM handles)"
    assert out is not hooks_mod._GATE_FALLTHROUGH
    assert len(s.f7_calls) == 1
    assert s.flyer_arm_calls == [] and s.update_calls == [], "declined escape must NOT fall into flyer capture"


# ── Reviewer-required cell 1: intake_started (zero-asset) project + fresh catering ──
def test_intake_started_project_fresh_catering_escapes(monkeypatch):
    """Live F0218/F0220 shape: an active project in `intake_started` (zero assets)
    plus a fresh catering inquiry still escapes to F7 and never a flyer capture."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("intake_started", "F0218"))
    out = hooks_mod._try_flyer_catering_escape_gate(
        "We need catering for a graduation party of 45 people next month, "
        "can you share menu options?", CHAT, _event())
    assert out["action"] == "skip" and "F7" in out["reason"]
    assert len(s.f7_calls) == 1 and s.f7_calls[0]["allow_new_lead"] is True
    assert _reasons(s) == ["flyer_active_project_catering_intent_escape"]
    assert "flyer_reference_exact_edit_queued" not in _reasons(s)
    assert s.update_calls == [] and s.flyer_arm_calls == []


# ── Reviewer-required cell 2: genuine flyer edit creates ZERO catering leads ──
def test_genuine_flyer_edit_creates_zero_catering_leads(monkeypatch):
    """A genuine flyer price/text edit falls through AND creates zero catering
    leads. `_try_f7_primary_intercept` is NOT stubbed here — the REAL F7 lead
    writer (`trigger_create_catering_lead`) is spied as the leads store, so a
    zero count is a genuine proof the flyer-edit path cannot leak into catering."""
    hooks_mod, actions_mod = _load_plugin()
    leads_store: list = []  # stand-in for the catering-leads store
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender",
                        lambda p, c: _project("awaiting_final_approval"))
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: None)
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", lambda p, c: None)
    monkeypatch.setattr(actions_mod, "trigger_create_catering_lead",
                        lambda **kw: leads_store.append(kw) or (True, "lead_created"))
    before = len(leads_store)
    out = hooks_mod._try_flyer_catering_escape_gate(
        "change the price to $8.99 on the flyer", CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert len(leads_store) == before == 0, "a genuine flyer edit must create ZERO catering leads"


# ── Reviewer-required cell 3: R2B-1 ARMED pre-empts the escape gate (dispatch) ──
def _neutralize_pre_gate_intercepts(monkeypatch, hooks_mod, actions_mod, *, audits):
    """Make every dispatch intercept BEFORE the R2B-1 gate a deterministic no-op so
    the amendment inbound reaches the R2B-1 / escape-gate region cleanly on Windows
    (no fcntl audit path). Mirrors the isolation seam the R2B-1 canary tests use."""
    monkeypatch.setattr(actions_mod, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: False)
    monkeypatch.setattr(actions_mod, "mark_cf_router_inbound_seen", lambda *a, **k: False)
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "get_revenue_route_clarification", lambda cid: None)
    monkeypatch.setattr(actions_mod, "front_brain_converse_admits", lambda cid: False)
    monkeypatch.setattr(actions_mod, "flyer_campaign_cta_text", lambda t: "")
    monkeypatch.setattr(actions_mod, "find_paid_flyer_guest_order", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: audits.append(kw))
    for fn in (
        "_try_flyer_quote_echo_choice", "_try_flyer_account_intercept",
        "_try_flyer_sample_prompt_request_intercept", "_try_flyer_regulated_account_guard",
        "_try_flyer_quote_echo_guard", "_try_flyer_intake_intercept",
        "_try_flyer_reference_scope_choice_intercept",
        "_try_flyer_source_vs_new_choice_intercept",
        "_try_flyer_reference_scope_authorization_intercept",
        "_try_flyer_existing_onboarding_intercept",
    ):
        monkeypatch.setattr(hooks_mod, fn, lambda *a, **k: None)


def test_armed_r2b1_fires_first_and_escape_gate_never_invoked(monkeypatch):
    """With the R2B-1 amendment-conflict gate ARMED (via its own enabled/allowlist
    seam) and an amendment-conflict-eligible inbound, the R2B-1 intercept captures
    FIRST and the P1-1 escape gate is NEVER invoked — proving the gate cannot bypass
    or duplicate R2B-1 when armed, not just when dormant."""
    from catering_amendments import CaptureResult
    hooks_mod, actions_mod = _load_plugin()
    audits: list = []
    _neutralize_pre_gate_intercepts(monkeypatch, hooks_mod, actions_mod, audits=audits)
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))

    # ARM R2B-1 through its real deterministic seam (same as its own unit tests).
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_allowlisted", lambda cid: True)
    monkeypatch.setattr(actions_mod, "has_non_delivered_flyer_project_by_sender", lambda p, c: True)
    monkeypatch.setattr(actions_mod, "find_all_eligible_catering_leads_by_sender", lambda p, c: [_lead("L0015")])
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda p, c: _project("revising_design", "F0003"))
    monkeypatch.setattr(actions_mod, "run_catering_amendment_discriminator",
                        lambda **kw: {"decision": "catering_amendment", "cause": "ok", "called": True, "latency_ms": 30})
    monkeypatch.setattr(hooks_mod.catering_amendments, "capture_branch_b_amendment",
                        lambda **kw: CaptureResult(ok=True, amendment_id="A0007", idempotent=False))
    monkeypatch.setattr(actions_mod, "send_canonical_followup_reply", lambda cid, lid: True)

    # Count both the escape gate and classify_catering — BOTH must be untouched.
    escape_calls = {"n": 0}

    def _escape(*a, **k):
        escape_calls["n"] += 1
        return hooks_mod._GATE_FALLTHROUGH
    monkeypatch.setattr(hooks_mod, "_try_flyer_catering_escape_gate", _escape)
    classify_calls = {"n": 0}
    _real_classify = actions_mod.classify_catering

    def _classify(t):
        classify_calls["n"] += 1
        return _real_classify(t)
    monkeypatch.setattr(actions_mod, "classify_catering", _classify)

    result = hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text="actually make it 60 guests not 45", chat_id=CHAT, message_id="wamid.R2B1"))

    assert result["action"] == "skip" and "captured for L0015" in result["reason"], result
    assert [a["reason"] for a in audits] == ["catering_amendment_conflict_captured"]
    assert escape_calls["n"] == 0, "R2B-1 armed ⇒ escape gate MUST NOT be invoked"
    assert classify_calls["n"] == 0, "R2B-1 armed ⇒ escape gate's classify_catering MUST NOT run"


def _real_finalize_leaf_deps(monkeypatch, actions_mod, proj):
    """Stub the leaf deps of the REAL `_try_flyer_active_project_intercept` so its
    approval/send-now arm finalizes deterministically (probe-verified outcome:
    result `cf-router flyer active: finalized F0300`, audit `flyer_primary_project_created`)."""
    monkeypatch.setattr(actions_mod, "find_flyer_customer_by_sender",
                        lambda p, c: {"status": "active", "customer_id": "CUST0001"})
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda p, c: proj)
    monkeypatch.setattr(actions_mod, "resolve_flyer_binding_project",
                        lambda ap, p, c, e, t: (proj, "newest_updated"))
    monkeypatch.setattr(actions_mod, "flyer_business_scope_block_message", lambda cust, body: "")
    monkeypatch.setattr(actions_mod, "flyer_requested_business_scope", lambda body: "")
    monkeypatch.setattr(actions_mod, "should_bypass_active_flyer_project_for_fresh_request", lambda *a, **k: False)
    monkeypatch.setattr(actions_mod, "is_stale_for_new_request", lambda ap: False)
    monkeypatch.setattr(actions_mod, "find_reserved_flyer_guest_order", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "find_paid_flyer_guest_order", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "send_flyer_text", lambda cid, txt, **k: (True, "mid1", ""))
    monkeypatch.setattr(actions_mod, "invoke_update_flyer_project", lambda *a, **k: (True, "{}"))
    monkeypatch.setattr(actions_mod, "finalize_and_send_flyer", lambda *a, **k: (True, "sent"))


COMPOUND = "Send it now — also, can you cater 120 guests for a wedding August 8?"


# ── Reviewer-required cell 4 (REWRITTEN): send-now compound is now GATED ──
# The reviewer ruled the residual should be fixed. A compound "send it now +
# <fresh catering>" no longer takes the line-456 early finalize path; it falls
# through the normal ladder (R2B-1 precedence preserved) to the escape gate,
# which raises ONE flyer-vs-catering clarification — no finalization, no revision,
# no catering lead. `classify_catering` runs EXACTLY ONCE end-to-end (dispatch memo).
def test_send_now_compound_now_clarifies_no_finalization_single_classify(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    # Residual mechanism (unchanged facts): start-anchored send-now AND catering both
    # fire; the whole-message approval-text classifier does NOT.
    assert actions_mod.is_flyer_send_now_intent(COMPOUND) is True
    assert actions_mod.is_flyer_approval_text(COMPOUND) is False
    assert actions_mod.classify_catering(COMPOUND)[0] is True

    audits: list = []
    _neutralize_pre_gate_intercepts(monkeypatch, hooks_mod, actions_mod, audits=audits)
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_enabled", lambda: False)  # R2B-1 dormant
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender",
                        lambda p, c: _project("awaiting_final_approval", "F0300"))
    sent, clar_saved, update_calls, lead_writes = [], [], [], []
    monkeypatch.setattr(actions_mod, "save_revenue_route_clarification", lambda **kw: clar_saved.append(kw))
    monkeypatch.setattr(actions_mod, "send_flyer_text", lambda cid, txt, **kw: sent.append((cid, txt)) or (True, "mid1", ""))
    monkeypatch.setattr(actions_mod, "invoke_update_flyer_project", lambda *a, **k: update_calls.append(a) or (True, "{}"))
    monkeypatch.setattr(actions_mod, "trigger_create_catering_lead", lambda **kw: lead_writes.append(kw) or (True, "lead_created"))
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", lambda p, c: None)
    # Spy the finalize arm: it must NEVER be reached for the compound.
    arm_calls: list = []
    monkeypatch.setattr(hooks_mod, "_try_flyer_active_project_intercept",
                        lambda *a, **k: arm_calls.append(a) or {"action": "skip", "reason": "cf-router flyer active: finalized F0300"})
    # Count the UNDERLYING classify_catering beneath the dispatch memo.
    classify_calls = {"n": 0}
    _real = actions_mod.classify_catering

    def _count(t):
        classify_calls["n"] += 1
        return _real(t)
    monkeypatch.setattr(actions_mod, "classify_catering", _count)

    result = hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=COMPOUND, chat_id=CHAT, message_id="wamid.CMP"))

    # NEW pinned outcome: ONE flyer-vs-catering clarification; NO finalization.
    assert result == {"action": "skip", "reason": "cf-router flyer/catering intent clarification sent"}, result
    assert [a.get("reason") for a in audits] == ["flyer_catering_intent_clarification"]
    assert "flyer_primary_project_created" not in [a.get("reason") for a in audits], "must NOT finalize"
    assert len(sent) == 1 and len(clar_saved) == 1, "exactly one clarification + one parked pending"
    assert arm_calls == [], "the line-456 finalize arm is pre-empted"
    assert update_calls == [] and lead_writes == [], "no revision/queued edit, no catering lead"
    assert classify_calls["n"] == 1, "AT MOST ONE classifier call per inbound (dispatch memo shared)"


# ── Reviewer-required cell 5: pure send-now REGRESSION — finalization unchanged ──
@pytest.mark.parametrize("pure_send_now", ["Send it now", "please send my flyer now"])
def test_pure_send_now_finalization_unchanged(monkeypatch, pure_send_now):
    """A PURE send-now (no fresh catering) still takes the line-456 early path and
    finalizes byte-identically to pre-patch — the exact result + audit the old cell
    #4 pinned. The escape gate is never reached."""
    hooks_mod, actions_mod = _load_plugin()
    assert actions_mod.is_flyer_send_now_intent(pure_send_now) is True
    assert actions_mod.classify_catering(pure_send_now)[0] is False  # not catering ⇒ pure

    audits: list = []
    _neutralize_pre_gate_intercepts(monkeypatch, hooks_mod, actions_mod, audits=audits)
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))
    proj = {"project_id": "F0300", "status": "awaiting_final_approval", "customer_phone": PHONE,
            "manual_review": {}, "pending_revision_confirmation": {}, "concepts": []}
    _real_finalize_leaf_deps(monkeypatch, actions_mod, proj)  # REAL arm finalizes
    escape_calls = {"n": 0}

    def _escape(*a, **k):
        escape_calls["n"] += 1
        return hooks_mod._GATE_FALLTHROUGH
    monkeypatch.setattr(hooks_mod, "_try_flyer_catering_escape_gate", _escape)

    result = hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=pure_send_now, chat_id=CHAT, message_id="wamid.PURE"))

    assert result == {"action": "skip", "reason": "cf-router flyer active: finalized F0300"}, result
    assert "flyer_primary_project_created" in [a.get("reason") for a in audits]
    assert escape_calls["n"] == 0, "pure send-now stays on the early path; escape gate not reached"


# ── Reviewer-required cell 6: compound + classifier EXCEPTION → clarify, exactly one call ──
def test_send_now_compound_classifier_exception_clarifies_single_call(monkeypatch):
    """When the classifier RAISES on a compound send-now, both the line-456 check
    (try/except → treat as compound) and the escape gate (memo re-raises → gate
    except → clarify) resolve to ONE clarification, and the underlying
    classify_catering is invoked EXACTLY ONCE (the memo caches + re-raises)."""
    hooks_mod, actions_mod = _load_plugin()
    audits: list = []
    _neutralize_pre_gate_intercepts(monkeypatch, hooks_mod, actions_mod, audits=audits)
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_enabled", lambda: False)
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender",
                        lambda p, c: _project("awaiting_final_approval", "F0300"))
    sent, clar_saved = [], []
    monkeypatch.setattr(actions_mod, "save_revenue_route_clarification", lambda **kw: clar_saved.append(kw))
    monkeypatch.setattr(actions_mod, "send_flyer_text", lambda cid, txt, **kw: sent.append((cid, txt)) or (True, "mid1", ""))
    arm_calls: list = []
    monkeypatch.setattr(hooks_mod, "_try_flyer_active_project_intercept",
                        lambda *a, **k: arm_calls.append(a) or {"action": "skip", "reason": "cf-router flyer active: finalized F0300"})
    f7_calls: list = []
    monkeypatch.setattr(hooks_mod, "_try_f7_primary_intercept", lambda *a, **k: f7_calls.append((a, k)) or None)
    # Spy the REAL classifier BENEATH the memo — it must be invoked exactly once.
    classify_calls = {"n": 0}

    def _boom(_t):
        classify_calls["n"] += 1
        raise RuntimeError("classifier exploded")
    monkeypatch.setattr(actions_mod, "classify_catering", _boom)

    result = hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=COMPOUND, chat_id=CHAT, message_id="wamid.BOOM"))

    assert result == {"action": "skip", "reason": "cf-router flyer/catering intent clarification sent"}, result
    assert [a.get("reason") for a in audits] == ["flyer_catering_intent_clarification"]
    assert len(sent) == 1 and len(clar_saved) == 1
    assert arm_calls == [] and f7_calls == [], "classifier error must NOT finalize or guess a route"
    assert classify_calls["n"] == 1, "the underlying classifier runs exactly once even on the failure path"


# ── Design point 3: no-active-project compound stays behaviorally identical ──
def test_no_active_project_compound_reaches_delivery_state_guard(monkeypatch):
    """A compound send-now with NO active flyer project is unchanged by the patch:
    line 456 does not early-path (compound), the escape gate falls through on the
    no-project scope check, and the delivery-state guard handles the send-now as
    today — no clarification, no finalization. classify_catering runs once."""
    hooks_mod, actions_mod = _load_plugin()
    audits: list = []
    _neutralize_pre_gate_intercepts(monkeypatch, hooks_mod, actions_mod, audits=audits)
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_enabled", lambda: False)
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda p, c: None)
    monkeypatch.setattr(actions_mod, "find_flyer_customer_by_sender", lambda p, c: None)
    monkeypatch.setattr(actions_mod, "send_flyer_text", lambda cid, txt, **k: (True, "mid1", ""))
    classify_calls = {"n": 0}
    _real = actions_mod.classify_catering

    def _count(t):
        classify_calls["n"] += 1
        return _real(t)
    monkeypatch.setattr(actions_mod, "classify_catering", _count)

    result = hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=COMPOUND, chat_id=CHAT, message_id="wamid.NP"))

    assert result == {"action": "skip", "reason": "cf-router flyer delivery state guard"}, result
    assert [a.get("reason") for a in audits] == ["flyer_delivery_state_guard"]
    assert "flyer_catering_intent_clarification" not in [a.get("reason") for a in audits]
    assert classify_calls["n"] == 1


# ── P1-1 open-lead routing precedence (the live 2026-07-24 recurrence) ───────
# A customer mid-catering-conversation (OPEN lead L0019) sent menu/proposal
# follow-ups that scored classify_catering=weak; each was swallowed by the flyer
# active-project intercept (`flyer_reference_exact_edit_queued`, stale project
# F0225). The gate must route these to catering BEFORE the flyer intercept.
OPEN_LEAD_FOLLOWUPS = [
    "Can you present me two best menus to choose from.",
    "Yes propose two menus",
    "For my birthday party I want to select items from your menu",
]


@pytest.mark.parametrize("followup", OPEN_LEAD_FOLLOWUPS)
def test_open_lead_menu_followup_escapes_to_catering_over_stale_flyer(monkeypatch, followup):
    """Open catering lead + a menu/proposal follow-up escapes to catering even
    with a STALE flyer project (manual_edit_required + queued edit) present — the
    exact live scenario. classify_catering scores weak (False) on these, so this
    is the path the pre-fix gate fell through on."""
    hooks_mod, actions_mod = _load_plugin()
    # Precondition: production classification of these follow-ups is weak.
    assert actions_mod.classify_catering(followup)[0] is False
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("manual_edit_required", "F0225"),
              open_lead=_lead("L0019"))
    out = hooks_mod._try_flyer_catering_escape_gate(followup, CHAT, _event())
    assert out["action"] == "skip" and "F7" in out["reason"]
    assert len(s.f7_calls) == 1 and s.f7_calls[0]["allow_new_lead"] is True
    assert _reasons(s) == ["flyer_active_project_open_lead_catering_escape"]
    assert "flyer_reference_exact_edit_queued" not in _reasons(s)
    # No flyer capture / no dual outcome.
    assert s.update_calls == [] and s.flyer_arm_calls == []


def test_explicit_flyer_edit_with_open_lead_stays_flyer(monkeypatch):
    """An explicit flyer edit ("change the price on my flyer to $8") WHILE a
    catering lead is open must NOT be over-escaped — it falls through to the flyer
    path (the deterministic flyer-signal exclusion holds)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("manual_edit_required", "F0225"),
              open_lead=_lead("L0019"))
    out = hooks_mod._try_flyer_catering_escape_gate(
        "change the price on my flyer to $8", CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.f7_calls == [] and s.sent == [] and s.audits == []


def test_complaint_while_flyer_project_active_escalates_not_swallowed(monkeypatch):
    """"Are u crazy" while a flyer project is active routes to an escalation ack,
    NOT a flyer queued-edit. No classifier call, no lead, no revision."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("manual_edit_required", "F0225"),
              open_lead=_lead("L0019"))
    out = hooks_mod._try_flyer_catering_escape_gate("Are u crazy", CHAT, _event())
    assert out == {"action": "skip", "reason": "cf-router customer complaint escalation sent"}
    assert _reasons(s) == ["customer_complaint_escalation"]
    assert len(s.sent) == 1, "exactly one escalation ack"
    assert s.f7_calls == [] and s.flyer_arm_calls == [] and s.update_calls == []
    assert s.classify_calls == 0, "a complaint must not invoke classify_catering"
    assert "flyer_reference_exact_edit_queued" not in _reasons(s)


def test_complaint_with_open_lead_no_flyer_project_still_escalates(monkeypatch):
    """The complaint guard fires on catering-lead-OR-flyer-project: an open lead
    with no active flyer project still escalates (never reaches the fallthrough)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, project=None, open_lead=_lead("L0019"))
    out = hooks_mod._try_flyer_catering_escape_gate("this is broken", CHAT, _event())
    assert out == {"action": "skip", "reason": "cf-router customer complaint escalation sent"}
    assert _reasons(s) == ["customer_complaint_escalation"]
    assert s.classify_calls == 0


def test_no_open_lead_proposal_phrasing_unchanged(monkeypatch):
    """Regression: a proposal-phrased follow-up with NO open catering lead keeps
    the prior behavior — classify=weak ⇒ fall through to the flyer arms unchanged.
    classify_catering is invoked exactly once (the not-catering scope check)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("manual_edit_required", "F0225"), open_lead=None)
    out = hooks_mod._try_flyer_catering_escape_gate(
        "Can you present me two best menus to choose from.", CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.f7_calls == [] and s.sent == [] and s.audits == []
    assert s.classify_calls == 1


def test_open_lead_escape_invokes_classify_at_most_once(monkeypatch):
    """On the open-lead escape path the gate calls classify_catering exactly once
    (F7 is stubbed here; the single-flight memo covers the dispatch-level call)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              project=_project("manual_edit_required", "F0225"),
              open_lead=_lead("L0019"))
    hooks_mod._try_flyer_catering_escape_gate("Yes propose two menus", CHAT, _event())
    assert s.classify_calls == 1
    assert _reasons(s) == ["flyer_active_project_open_lead_catering_escape"]


def test_new_open_lead_reason_literals_are_enum_members():
    from typing import get_args
    import schemas
    allowed = set(get_args(schemas.CfRouterIntercepted.model_fields["reason"].annotation))
    for reason in (
        "flyer_active_project_open_lead_catering_escape",
        "customer_complaint_escalation",
    ):
        assert reason in allowed, f"{reason} missing from CfRouterIntercepted.reason"


# ── Static placement proof (runs on every platform) ─────────────────────────
def test_escape_gate_hoisted_between_r2b1_gate_and_flyer_arm():
    src = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_pre_gateway_dispatch_impl")
    r2b1 = escape = None
    flyer_lines = []
    for node in ast.walk(dispatch):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_try_amendment_conflict_intercept" and r2b1 is None:
                r2b1 = node.lineno
            if node.func.id == "_try_flyer_catering_escape_gate" and escape is None:
                escape = node.lineno
            if node.func.id == "_try_flyer_active_project_intercept":
                flyer_lines.append(node.lineno)
    assert r2b1 is not None, "R2B-1 amendment gate not wired into dispatch"
    assert escape is not None, "escape gate not wired into dispatch"
    assert flyer_lines, "flyer active-project arm not found in dispatch"
    assert r2b1 < escape, "R2B-1 amendment gate MUST keep precedence AHEAD of the escape gate"
    assert any(fl > escape for fl in flyer_lines), (
        f"escape gate (line {escape}) MUST precede the flyer active-project arm "
        f"(lines {sorted(flyer_lines)})")
