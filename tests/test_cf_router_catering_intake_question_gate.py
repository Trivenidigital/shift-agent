"""Weak catering question mid flyer-intake — `_try_flyer_intake_catering_question_gate`.

"Do you do catering?" (classify_catering: NOT catering, but `primary:catering`)
from a sender with a flyer intake in progress was consumed by Flyer intake: fed
to `trigger_flyer_intake` as a guided answer, or taken by the intake-status
active-project arms (start generation / canned "request open" reply). The escape
gate only escapes STRONG catering, so it fell through to those arms.

Dispatch-level tests drive `_pre_gateway_dispatch_impl` with the REAL intake
intercept (leaf `trigger_flyer_intake` spied) and a spied active-project arm, so
deleting the call site makes them fail (the mutant check). Unit-level tests pin
every must-NOT-trigger cell to the fall-through sentinel, and the resolver tests
pin the "flyer" reply to an intake replay (never a force_new project).
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

PHONE = "+15550100001"
CHAT = "15550100001@lid"
QUESTION = "Do you do catering?"
INTAKE_CAUSE_SIGNAL = "flyer_catering_intent:intake_catering_question"
INTAKE_STATUSES = ("intake_started", "collecting_required_info", "awaiting_assets")
CLARIFIED = {"action": "skip", "reason": "cf-router flyer/catering intent clarification sent"}


def _load_plugin():
    pkg = "cf_router_intake_catq_pkg"
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
    return SimpleNamespace(message_id="wamid.CATQ", timestamp="1721480400", transport="whatsapp")


def _session(status="guided_collecting_goal"):
    return {"chat_id": CHAT, "sender_phone": PHONE, "status": status}


def _project(status="collecting_required_info", project_id="F0400"):
    # Required fields present, no concepts: the shape the generation arm takes.
    return {"project_id": project_id, "status": status, "customer_phone": PHONE,
            "business_name": "Lakshmi Kitchen", "offer": "Diwali special",
            "concepts": []}


def _lead(lead_id="L0015", status="AWAITING_OWNER_APPROVAL"):
    return {"lead_id": lead_id, "status": status, "customer_phone": PHONE}


class _Spies:
    def __init__(self):
        self.audits = []
        self.sent = []
        self.clar_saved = []
        self.intake_triggers = []
        self.generate_calls = []
        self.arm_calls = []


def _wire(monkeypatch, hooks_mod, actions_mod, *, role="customer", owner=False,
          session=None, project=None, open_lead=None):
    """Stub every dependency the gate touches (classifiers stay REAL)."""
    s = _Spies()
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, role))
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: owner)
    monkeypatch.setattr(actions_mod, "find_flyer_intake_session_by_sender", lambda p, c: session)
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda p, c: project)
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", lambda p, c: open_lead)
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(actions_mod, "send_flyer_text",
                        lambda cid, txt, **kw: s.sent.append((cid, txt)) or (True, "mid1", ""))
    monkeypatch.setattr(actions_mod, "save_revenue_route_clarification",
                        lambda **kw: s.clar_saved.append(kw))
    monkeypatch.setattr(actions_mod, "trigger_flyer_intake",
                        lambda **kw: s.intake_triggers.append(kw)
                        or (True, "", {"action": "ask", "reply_text": "next question"}))
    monkeypatch.setattr(actions_mod, "trigger_generate_flyer_concepts",
                        lambda pid: s.generate_calls.append(pid) or (True, "{}"))
    return s


def _neutralize_dispatch(monkeypatch, hooks_mod, actions_mod, s):
    """Every dispatch intercept around the new gate is a deterministic no-op EXCEPT
    the REAL intake intercept (leaf-stubbed) and a spied active-project arm."""
    monkeypatch.setattr(actions_mod, "is_flyer_enabled", lambda: True)
    monkeypatch.setattr(actions_mod, "mark_cf_router_inbound_seen", lambda *a, **k: False)
    monkeypatch.setattr(actions_mod, "audit_raw_body", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "get_revenue_route_clarification", lambda cid: None)
    monkeypatch.setattr(actions_mod, "front_brain_converse_admits", lambda cid: False)
    monkeypatch.setattr(actions_mod, "flyer_campaign_cta_text", lambda t: "")
    monkeypatch.setattr(actions_mod, "find_paid_flyer_guest_order", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_enabled", lambda: False)
    monkeypatch.setattr(actions_mod, "find_flyer_customer_by_sender",
                        lambda p, c: {"customer_id": "CUST0001", "status": "active"})
    monkeypatch.setattr(actions_mod, "should_bypass_intake_for_clear_intent", lambda **k: None)
    for fn in (
        "_try_f8_intercept",
        "_try_flyer_quote_echo_choice", "_try_flyer_account_intercept",
        "_try_flyer_sample_prompt_request_intercept", "_try_flyer_regulated_account_guard",
        "_try_flyer_quote_echo_guard",
        "_try_flyer_reference_scope_choice_intercept",
        "_try_flyer_source_vs_new_choice_intercept",
        "_try_flyer_reference_scope_authorization_intercept",
        "_try_flyer_existing_onboarding_intercept",
    ):
        monkeypatch.setattr(hooks_mod, fn, lambda *a, **k: None)
    monkeypatch.setattr(hooks_mod, "_try_flyer_active_project_intercept",
                        lambda *a, **k: s.arm_calls.append(a) or {"action": "skip", "reason": "flyer_arm"})


def _dispatch(hooks_mod, text=QUESTION):
    return hooks_mod._pre_gateway_dispatch_impl(SimpleNamespace(
        text=text, chat_id=CHAT, message_id="wamid.CATQ"))


def _reasons(s):
    return [a.get("reason") for a in s.audits]


def test_question_is_weak_catering_with_primary_signal():
    """Precondition pin: the incident shape is NOT catering yet carries the word."""
    _hooks_mod, actions_mod = _load_plugin()
    is_catering, signals = actions_mod.classify_catering(QUESTION)
    assert is_catering is False and "primary:catering" in signals


# ── 1. Customer with an intake SESSION → clarification, intake never fed ─────
@pytest.mark.parametrize("intake_status", [
    "guided_collecting_goal", "guided_collecting_schedule", "choosing_mode"])
def test_customer_intake_session_gets_clarification_not_intake_answer(monkeypatch, intake_status):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, session=_session(intake_status))
    _neutralize_dispatch(monkeypatch, hooks_mod, actions_mod, s)

    out = _dispatch(hooks_mod)

    assert out == CLARIFIED, out
    assert s.intake_triggers == [], "the catering question must NOT be fed to trigger_flyer_intake"
    assert s.arm_calls == [] and s.generate_calls == []
    assert len(s.sent) == 1 and len(s.clar_saved) == 1
    assert s.clar_saved[0]["signals"] == [INTAKE_CAUSE_SIGNAL]
    assert s.clar_saved[0]["original_text"] == QUESTION
    assert _reasons(s) == ["flyer_catering_intent_clarification"]
    assert f"status={intake_status}" in s.audits[0]["detail"]


# ── 2. Customer with an intake-status PROJECT → clarification, no generation ─
@pytest.mark.parametrize("status", INTAKE_STATUSES)
def test_customer_intake_status_project_gets_clarification_not_generation(monkeypatch, status):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, project=_project(status, "F0401"))
    _neutralize_dispatch(monkeypatch, hooks_mod, actions_mod, s)

    out = _dispatch(hooks_mod)

    assert out == CLARIFIED, out
    assert s.arm_calls == [], "the intake-status project arms must NOT be reached"
    assert s.generate_calls == [], "flyer generation must NOT start on a catering question"
    assert s.intake_triggers == []
    assert len(s.sent) == 1 and len(s.clar_saved) == 1
    assert s.clar_saved[0]["signals"] == [INTAKE_CAUSE_SIGNAL]
    assert _reasons(s) == ["flyer_catering_intent_clarification"]
    assert "project_id=F0401" in s.audits[0]["detail"]
    assert f"status={status}" in s.audits[0]["detail"]


# ── 3. Owner (dual-role, scalar "employee") → yield to the LLM, audited ──────
@pytest.mark.parametrize("state", ["session", *INTAKE_STATUSES])
def test_owner_yields_with_audit_and_sends_nothing(monkeypatch, state):
    hooks_mod, actions_mod = _load_plugin()
    kw = ({"session": _session()} if state == "session"
          else {"project": _project(state, "F0402")})
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee", owner=True, **kw)

    out = hooks_mod._try_flyer_intake_catering_question_gate(QUESTION, CHAT, _event())

    assert out is None, "owner → yield the turn to the LLM"
    assert s.sent == [] and s.clar_saved == []
    assert s.intake_triggers == [] and s.generate_calls == []
    assert _reasons(s) == ["flyer_active_project_bypassed"]
    detail = s.audits[0]["detail"]
    assert "owner_yielded=true" in detail and "yield_reason=intake_catering_question" in detail
    assert "sender_role=employee" in detail


def test_owner_yield_at_dispatch_returns_none_without_reaching_flyer(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee", owner=True,
              project=_project("collecting_required_info"))
    _neutralize_dispatch(monkeypatch, hooks_mod, actions_mod, s)

    out = _dispatch(hooks_mod)

    assert out is None
    assert s.arm_calls == [] and s.generate_calls == [] and s.sent == []
    assert "flyer_active_project_bypassed" in _reasons(s)


# ── 4. Must-NOT-trigger cells → fall-through sentinel, nothing sent/audited ──
@pytest.mark.parametrize("text, wire_kw", [
    ("I need catering for 30 people on Nov 14", {"session": _session()}),
    ("Change the flyer headline to Spice Catering", {"project": _project("collecting_required_info")}),
    ("Saturday 6pm at the community hall", {"session": _session()}),
    (QUESTION, {"session": _session(), "open_lead": _lead()}),
    (QUESTION, {"project": _project("collecting_required_info"), "open_lead": _lead()}),
    (QUESTION, {}),
    (QUESTION, {"project": _project("awaiting_final_approval")}),
], ids=["strong_catering", "flyer_edit_with_catering_word", "intake_answer_no_catering",
        "open_lead_with_session", "open_lead_with_intake_project",
        "no_session_no_project", "project_awaiting_final_approval"])
def test_must_not_trigger_falls_through(monkeypatch, text, wire_kw):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, **wire_kw)
    out = hooks_mod._try_flyer_intake_catering_question_gate(text, CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.sent == [] and s.clar_saved == [] and s.audits == []


def test_strong_catering_mid_intake_reaches_existing_path_at_dispatch(monkeypatch):
    """Strong catering is not this gate's: dispatch continues to the intake
    intercept exactly as before (no clarification from the new gate)."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, session=_session())
    _neutralize_dispatch(monkeypatch, hooks_mod, actions_mod, s)
    _dispatch(hooks_mod, text="I need catering for 30 people on Nov 14")
    assert len(s.intake_triggers) == 1
    assert s.clar_saved == []


def test_intake_answer_without_catering_still_reaches_intake_at_dispatch(monkeypatch):
    """Byte-identical existing path: a normal intake answer is fed to intake."""
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, session=_session())
    _neutralize_dispatch(monkeypatch, hooks_mod, actions_mod, s)
    _dispatch(hooks_mod, text="Saturday 6pm at the community hall")
    assert len(s.intake_triggers) == 1
    assert s.clar_saved == []
    assert "flyer_catering_intent_clarification" not in _reasons(s)


def test_owner_authority_never_consulted_on_fallthrough(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, session=_session())
    owner_calls = []
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: owner_calls.append(cid) or True)
    out = hooks_mod._try_flyer_intake_catering_question_gate(
        "Saturday 6pm at the community hall", CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH and owner_calls == [] and s.audits == []


def test_classifier_error_falls_through_and_audits_nothing(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, session=_session())

    def _boom(_t):
        raise RuntimeError("classifier exploded")
    out = hooks_mod._try_flyer_intake_catering_question_gate(
        QUESTION, CHAT, _event(), classify_fn=_boom)
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.sent == [] and s.clar_saved == [] and s.audits == []


def test_lookup_error_falls_through_and_audits_nothing(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, session=_session())

    def _boom(p, c):
        raise OSError("leads store unreadable")
    monkeypatch.setattr(actions_mod, "find_active_catering_lead_by_sender", _boom)
    out = hooks_mod._try_flyer_intake_catering_question_gate(QUESTION, CHAT, _event())
    assert out is hooks_mod._GATE_FALLTHROUGH
    assert s.sent == [] and s.audits == []


def test_emitted_reasons_are_schema_literal_members():
    from typing import get_args
    import schemas
    allowed = set(get_args(schemas.CfRouterIntercepted.model_fields["reason"].annotation))
    for reason in ("flyer_active_project_bypassed", "flyer_catering_intent_clarification",
                   "revenue_route_clarification_chosen", "revenue_route_clarification_sent"):
        assert reason in allowed, f"{reason} missing from CfRouterIntercepted.reason"


# ── 5. Resolver: "flyer" acknowledges (no replay); "catering" → F7 as before ──
def _wire_resolver(monkeypatch, hooks_mod, actions_mod, *, signals, role="customer",
                   intake_result=None):
    rec = {"intake": [], "arm": [], "primary": [], "f7": [], "audits": []}
    pending = {"chat_id": CHAT, "original_text": QUESTION, "message_id": "wamid.ORIG",
               "signals": signals, "kind": "revenue_route"}
    monkeypatch.setattr(actions_mod, "get_revenue_route_clarification", lambda cid: dict(pending))
    monkeypatch.setattr(actions_mod, "pop_revenue_route_clarification", lambda cid: dict(pending))
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: rec["audits"].append(kw))
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, role))
    monkeypatch.setattr(hooks_mod, "_try_flyer_intake_intercept",
                        lambda t, c, e, *a, **k: rec["intake"].append(t) or intake_result)
    monkeypatch.setattr(hooks_mod, "_try_flyer_active_project_intercept",
                        lambda t, c, e, *a, **k: rec["arm"].append(t) or {"action": "skip", "reason": "flyer_arm"})
    monkeypatch.setattr(hooks_mod, "_try_flyer_primary_intercept",
                        lambda t, c, e, *a, **k: rec["primary"].append((t, k))
                        or {"action": "skip", "reason": "new_project"})
    monkeypatch.setattr(hooks_mod, "_try_f7_primary_intercept",
                        lambda t, c, e, **k: rec["f7"].append((t, k)) or {"action": "skip", "reason": "f7"})
    return rec


def _choose(hooks_mod, reply, *, gen=True):
    return hooks_mod._try_revenue_route_clarification_choice(
        reply, CHAT, _event(), flyer_generation_enabled=gen, flyer_workflow_enabled=True)


@pytest.mark.parametrize("gen", [True, False])
def test_resolver_flyer_acknowledges_and_never_replays_question(monkeypatch, gen):
    """The stored text IS the catering question: replaying it into intake would
    take it as a guided answer (or start generation on an intake-ready project)
    one turn later. "flyer" only acknowledges; no intake, no arm, no new project."""
    hooks_mod, actions_mod = _load_plugin()
    rec = _wire_resolver(monkeypatch, hooks_mod, actions_mod, signals=[INTAKE_CAUSE_SIGNAL])
    sent = []
    monkeypatch.setattr(actions_mod, "send_flyer_text",
                        lambda cid, body, **k: sent.append(body) or (True, "wamid.ACK", ""))
    out = _choose(hooks_mod, "flyer", gen=gen)
    assert out == {"action": "skip", "reason": "cf-router flyer intake continue acknowledged"}
    assert len(sent) == 1 and "still open" in sent[0]
    assert rec["intake"] == [] and rec["arm"] == [] and rec["primary"] == [] and rec["f7"] == []
    assert [a["reason"] for a in rec["audits"]] == [
        "revenue_route_clarification_chosen", "revenue_route_clarification_sent"]
    assert "intake_continue_ack=true" in rec["audits"][1]["detail"]


def test_resolver_catering_routes_to_f7_as_before(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    rec = _wire_resolver(monkeypatch, hooks_mod, actions_mod, signals=[INTAKE_CAUSE_SIGNAL])
    out = _choose(hooks_mod, "catering")
    assert out == {"action": "skip", "reason": "f7"}
    assert rec["f7"] == [(QUESTION, {"signals": ["revenue_route_choice:catering"],
                                      "allow_new_lead": True})]
    assert rec["intake"] == [] and rec["primary"] == []


def test_resolver_catering_owner_yields(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    rec = _wire_resolver(monkeypatch, hooks_mod, actions_mod, signals=[INTAKE_CAUSE_SIGNAL],
                         role="owner")
    assert _choose(hooks_mod, "catering") is None
    assert rec["f7"] == []


def test_resolver_other_flyer_pending_still_force_new(monkeypatch):
    """Unchanged: a non-intake flyer/catering pending still starts a new project."""
    hooks_mod, actions_mod = _load_plugin()
    rec = _wire_resolver(monkeypatch, hooks_mod, actions_mod,
                         signals=["flyer_catering_intent:ambiguous_flyer_and_catering"])
    out = _choose(hooks_mod, "flyer")
    assert out == {"action": "skip", "reason": "new_project"}
    assert rec["primary"] == [(QUESTION, {"force_new": True})]
    assert rec["intake"] == []


# ── Static placement: the gate precedes intake and the escape gate ──────────
def test_gate_wired_before_intake_and_escape_gate():
    src = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_pre_gateway_dispatch_impl")
    lines: dict = {}
    for node in ast.walk(dispatch):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            lines.setdefault(node.func.id, node.lineno)
    gate = lines.get("_try_flyer_intake_catering_question_gate")
    assert gate is not None, "intake catering-question gate not wired into dispatch"
    assert lines["_try_flyer_quote_echo_guard"] < gate < lines["_try_flyer_intake_intercept"]
    assert gate < lines["_try_flyer_catering_escape_gate"]
