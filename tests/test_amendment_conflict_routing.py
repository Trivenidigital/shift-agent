"""PR-R2B-1 — flyer/catering amendment-conflict routing gate (unit-level).

Exercises `_try_amendment_conflict_intercept` (the SINGLE hoisted gate) + the
discriminator + the clarification choice handler in isolation, with the gate's
dependencies monkeypatched so every branch, gating proof, and control-flow
obligation is deterministic and network-free. Windows-runnable via the fcntl stub
(the gate's lock paths are exercised no-op in-process). The DISPATCH-level canary
replay + flag-off byte-identical + placement proofs live in
tests/test_cf_router_plugin.py (Linux-only harness that drives pre_gateway_dispatch).

Reviewer proof obligations covered here: default-off · not-allowlisted → no call ·
zero/multiple/terminal leads → no call · exactly-one-lead → the only cell that fires ·
wildcard/empty/malformed allowlist · canonical allowlist match · timeout/malformed/
unavailable → clarify · AT MOST ONE discriminator call per inbound (every outcome) ·
capture-before-reply · capture-failure total suppression · clarify creates neither ·
privacy (no raw text / phone in audit) · source=conflict_discriminator persistence.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
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

from catering_amendments import CaptureResult  # noqa: E402

PHONE = "+17329837841"
CHAT = "17329837841@lid"


def _load_plugin():
    pkg = "cf_router_r2b1_pkg"
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


def _lead(lead_id, status="AWAITING_OWNER_APPROVAL"):
    return {"lead_id": lead_id, "status": status, "owner_approval_code": "#ABCDE",
            "customer_phone": PHONE, "created_at": f"2026-07-{int(lead_id[-2:] or 1):02d}T00:00:00+00:00"}


def _event():
    return SimpleNamespace(message_id="wamid.CANARY", timestamp="1721480400", transport="whatsapp")


class _Spies:
    def __init__(self):
        self.discriminator_calls = []
        self.capture_calls = []
        self.canonical = []
        self.retry = []
        self.audits = []
        self.clar_saved = []
        self.sent = []


def _wire(monkeypatch, hooks_mod, actions_mod, *, enabled=True, allowlisted=True,
          has_flyer=True, role="customer", leads=None,
          discriminator=None, capture=None):
    """Monkeypatch every dependency the gate touches. Returns a _Spies recorder."""
    s = _Spies()
    if leads is None:
        leads = [_lead("L0011")]
    if discriminator is None:
        discriminator = {"decision": "catering_amendment", "cause": "ok", "called": True, "latency_ms": 42}
    if capture is None:
        capture = CaptureResult(ok=True, amendment_id="A0001", idempotent=False)

    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_enabled", lambda: enabled)
    monkeypatch.setattr(actions_mod, "catering_amendment_discriminator_allowlisted", lambda cid: allowlisted)
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, role))
    monkeypatch.setattr(actions_mod, "has_non_delivered_flyer_project_by_sender", lambda p, c: has_flyer)
    monkeypatch.setattr(actions_mod, "find_all_eligible_catering_leads_by_sender", lambda p, c: list(leads))
    monkeypatch.setattr(actions_mod, "find_active_flyer_project_by_sender", lambda p, c: {"project_id": "F0001"})

    def _disc(**kw):
        s.discriminator_calls.append(kw)
        return dict(discriminator)
    monkeypatch.setattr(actions_mod, "run_catering_amendment_discriminator", _disc)

    def _cap(**kw):
        s.capture_calls.append(kw)
        return capture
    monkeypatch.setattr(hooks_mod.catering_amendments, "capture_branch_b_amendment", _cap)

    monkeypatch.setattr(actions_mod, "send_canonical_followup_reply",
                        lambda cid, lid: s.canonical.append((cid, lid)) or True)
    monkeypatch.setattr(hooks_mod, "_send_amendment_retry_reply",
                        lambda cid, lid: s.retry.append((cid, lid)) or True)
    monkeypatch.setattr(actions_mod, "audit_intercepted",
                        lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(actions_mod, "send_flyer_text",
                        lambda cid, txt, **kw: s.sent.append((cid, txt)) or (True, "mid1", ""))

    def _save(**kw):
        s.clar_saved.append(kw)
    monkeypatch.setattr(actions_mod, "save_revenue_route_clarification", _save)
    return s


def _reasons(spies):
    return [a["reason"] for a in spies.audits]


# ════════════════════════════════════════════════════════════════════════════
# GATING: the discriminator fires ONLY in the exactly-one-eligible-lead cell
# ════════════════════════════════════════════════════════════════════════════
def test_flag_off_returns_none_no_call(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, enabled=False)
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out is None
    assert s.discriminator_calls == [] and s.capture_calls == []


def test_not_allowlisted_returns_none_no_call(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, allowlisted=False)
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out is None
    assert s.discriminator_calls == []


def test_no_flyer_project_returns_none_no_call(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, has_flyer=False)
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out is None
    assert s.discriminator_calls == []


def test_zero_eligible_leads_returns_none_no_call(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, leads=[])
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out is None
    assert s.discriminator_calls == []


def test_multiple_eligible_leads_clarify_no_call(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, leads=[_lead("L0011"), _lead("L0012")])
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out["action"] == "skip" and "clarification" in out["reason"]
    assert s.discriminator_calls == [], "multiple leads must NEVER call the discriminator"
    assert s.capture_calls == [], "clarify creates no catering lead/capture"
    assert _reasons(s) == ["catering_amendment_conflict_clarify"]
    # pending stored as amendment_conflict, names BOTH lead ids (metadata only)
    assert s.clar_saved and s.clar_saved[0]["kind"] == "amendment_conflict"
    assert s.clar_saved[0]["lead_ids"] == ["L0011", "L0012"]


# ════════════════════════════════════════════════════════════════════════════
# ROUTING on discriminator result (exactly one eligible lead)
# ════════════════════════════════════════════════════════════════════════════
def test_catering_amendment_captures_before_reply_flyer_suppressed(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              discriminator={"decision": "catering_amendment", "cause": "ok", "called": True, "latency_ms": 30})
    out = hooks_mod._try_amendment_conflict_intercept("actually 60 guests not 45", CHAT, _event())
    assert out["action"] == "skip" and "captured" in out["reason"] and "flyer suppressed" in out["reason"]
    assert len(s.discriminator_calls) == 1
    assert len(s.capture_calls) == 1
    # capture is invoked with the R2B-1 route tag, and BEFORE the canonical reply
    assert s.capture_calls[0]["source"] == "conflict_discriminator"
    assert s.canonical == [(CHAT, "L0011")]
    assert s.retry == []
    assert _reasons(s) == ["catering_amendment_conflict_captured"]


def test_flyer_edit_falls_through_unchanged(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              discriminator={"decision": "flyer_edit", "cause": "ok", "called": True, "latency_ms": 25})
    out = hooks_mod._try_amendment_conflict_intercept("change the price to $45 on the flyer", CHAT, _event())
    assert out is None, "flyer_edit → None → the unchanged flyer arm runs as today"
    assert len(s.discriminator_calls) == 1
    assert s.capture_calls == [] and s.canonical == [] and s.retry == []
    assert _reasons(s) == ["catering_amendment_conflict_flyer_edit"]


def test_genuine_clarify_sends_clarification_no_capture(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              discriminator={"decision": "clarify", "cause": "ok", "called": True, "latency_ms": 20})
    out = hooks_mod._try_amendment_conflict_intercept("do the thing", CHAT, _event())
    assert out["action"] == "skip" and "clarification" in out["reason"]
    assert len(s.discriminator_calls) == 1
    assert s.capture_calls == []
    assert _reasons(s) == ["catering_amendment_conflict_clarify"]
    assert s.clar_saved[0]["kind"] == "amendment_conflict"


@pytest.mark.parametrize("cause", ["timeout", "error", "out_of_enum", "budget", "no_classifier"])
def test_discriminator_failure_maps_to_clarify_dedicated_reason(monkeypatch, cause):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              discriminator={"decision": "clarify", "cause": cause, "called": cause in ("timeout", "error", "out_of_enum"),
                             "latency_ms": 8000 if cause == "timeout" else 5})
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out["action"] == "skip" and "clarification" in out["reason"]
    assert s.capture_calls == [], "a failed discriminator must NEVER guess a route"
    assert _reasons(s) == ["catering_amendment_conflict_discriminator_failed"]
    # cause encoded in detail for Hermes-failure-frequency telemetry
    assert f"cause={cause}" in s.audits[0]["detail"]


def test_capture_failure_retry_and_total_suppression(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              discriminator={"decision": "catering_amendment", "cause": "ok", "called": True, "latency_ms": 33},
              capture=CaptureResult(ok=False, reason="fs_path_bad_mode"))
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out["action"] == "skip" and "capture failed" in out["reason"]
    assert s.retry == [(CHAT, "L0011")], "capture failure → deterministic retry"
    assert s.canonical == [], "capture failure must NOT send the canonical (implies-recorded) reply"
    assert _reasons(s) == ["catering_amendment_conflict_capture_failed"]


def test_replay_capture_sends_canonical_reply(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod,
              capture=CaptureResult(ok=True, amendment_id="A0001", idempotent=True))
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out["action"] == "skip"
    assert s.canonical == [(CHAT, "L0011")]
    assert "replayed" in s.audits[0]["detail"]


# ════════════════════════════════════════════════════════════════════════════
# Role: employee is neither authorization nor exclusion
# ════════════════════════════════════════════════════════════════════════════
def test_associated_employee_sender_still_gated(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee", leads=[_lead("L0011")])
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out["action"] == "skip" and "captured" in out["reason"]
    assert len(s.discriminator_calls) == 1, "an associated employee's lead is not excluded by role"


def test_unrelated_employee_sender_no_conflict(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, role="employee", leads=[])
    out = hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert out is None
    assert s.discriminator_calls == [], "role never grants access to an unrelated lead"


# ════════════════════════════════════════════════════════════════════════════
# One-call-max + prompt-context boundary + privacy
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("scenario", ["flag_off", "not_allowlisted", "no_flyer", "zero", "multi", "single"])
def test_at_most_one_discriminator_call_every_outcome(monkeypatch, scenario):
    hooks_mod, actions_mod = _load_plugin()
    kw = {}
    if scenario == "flag_off":
        kw = {"enabled": False}
    elif scenario == "not_allowlisted":
        kw = {"allowlisted": False}
    elif scenario == "no_flyer":
        kw = {"has_flyer": False}
    elif scenario == "zero":
        kw = {"leads": []}
    elif scenario == "multi":
        kw = {"leads": [_lead("L0011"), _lead("L0012")]}
    s = _wire(monkeypatch, hooks_mod, actions_mod, **kw)
    hooks_mod._try_amendment_conflict_intercept("make it 60", CHAT, _event())
    assert len(s.discriminator_calls) <= 1
    if scenario == "single":
        assert len(s.discriminator_calls) == 1
    else:
        assert len(s.discriminator_calls) == 0


def test_discriminator_sees_only_text_and_single_lead(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire(monkeypatch, hooks_mod, actions_mod, leads=[_lead("L0011")])
    hooks_mod._try_amendment_conflict_intercept("make it 60 guests", CHAT, _event())
    call = s.discriminator_calls[0]
    assert set(call) == {"text", "lead_id", "lead_status"}, "no other lead/customer data may reach the model"
    assert call["lead_id"] == "L0011"


def test_audit_details_leak_no_raw_text_or_phone(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    secret = "SECRET make it 60 guests vegetarian"
    for disc in ({"decision": "catering_amendment", "cause": "ok", "called": True, "latency_ms": 9},
                 {"decision": "flyer_edit", "cause": "ok", "called": True, "latency_ms": 9},
                 {"decision": "clarify", "cause": "timeout", "called": True, "latency_ms": 9}):
        hooks_mod, actions_mod = _load_plugin()
        s = _wire(monkeypatch, hooks_mod, actions_mod, discriminator=disc)
        hooks_mod._try_amendment_conflict_intercept(secret, CHAT, _event())
        blob = "\n".join(json.dumps(a) for a in s.audits)
        assert secret not in blob, f"raw text leaked for {disc['decision']}"
        assert PHONE not in blob, f"phone leaked for {disc['decision']}"


# ════════════════════════════════════════════════════════════════════════════
# Discriminator runner (bounded, single call, deterministic failure → clarify)
# ════════════════════════════════════════════════════════════════════════════
def test_runner_each_enum_one_call():
    _, actions_mod = _load_plugin()
    for dec in ("catering_amendment", "flyer_edit", "clarify"):
        n = {"c": 0}

        def cl(uc, _dec=dec):
            n["c"] += 1
            return {"decision": _dec}
        r = actions_mod.run_catering_amendment_discriminator(
            text="x", lead_id="L1", lead_status="S", classifier=cl, reserve_budget=False)
        assert r["decision"] == dec and r["cause"] == "ok" and n["c"] == 1


def test_runner_failures_map_to_clarify():
    _, actions_mod = _load_plugin()
    # raise → error
    r = actions_mod.run_catering_amendment_discriminator(
        text="x", lead_id="L1", lead_status="S",
        classifier=lambda uc: (_ for _ in ()).throw(RuntimeError("net")), reserve_budget=False)
    assert r["decision"] == "clarify" and r["cause"] == "error" and r["called"] is True
    # out of enum
    r = actions_mod.run_catering_amendment_discriminator(
        text="x", lead_id="L1", lead_status="S", classifier=lambda uc: {"decision": "banana"}, reserve_budget=False)
    assert r["decision"] == "clarify" and r["cause"] == "out_of_enum"
    # no classifier available
    r = actions_mod.run_catering_amendment_discriminator(
        text="x", lead_id="L1", lead_status="S", classifier=None, reserve_budget=False)
    assert r["decision"] == "clarify" and r["cause"] == "no_classifier" and r["called"] is False


def test_runner_timeout_is_bounded():
    import time
    _, actions_mod = _load_plugin()

    def slow(uc):
        time.sleep(2.0)
        return {"decision": "flyer_edit"}
    r = actions_mod.run_catering_amendment_discriminator(
        text="x", lead_id="L1", lead_status="S", classifier=slow, reserve_budget=False, timeout_sec=0.2)
    assert r["decision"] == "clarify" and r["cause"] == "timeout" and r["called"] is True


# ════════════════════════════════════════════════════════════════════════════
# Allowlist gating (canonical identity, empty=disabled, only "*", malformed-safe)
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("value,expect", [
    ("", False),                       # empty = disabled-for-all
    ("   ", False),                    # blank = disabled
    (",, ,", False),                   # malformed (only separators) = disabled
    ("*", True),                       # explicit wildcard graduates
    ("17329837841", True),             # raw digits entry
    ("+17329837841", True),            # phone entry admits the LID chat (normalized)
    ("19999999999", False),            # non-matching entry
])
def test_allowlist_semantics(monkeypatch, value, expect):
    _, actions_mod = _load_plugin()
    monkeypatch.setenv("CATERING_AMENDMENT_DISCRIMINATOR_CHATS", value)
    assert actions_mod.catering_amendment_discriminator_allowlisted(CHAT) is expect


def test_flag_default_off(monkeypatch):
    _, actions_mod = _load_plugin()
    monkeypatch.delenv("CATERING_AMENDMENT_DISCRIMINATOR", raising=False)
    assert actions_mod.catering_amendment_discriminator_enabled() is False
    monkeypatch.setenv("CATERING_AMENDMENT_DISCRIMINATOR", "0")
    assert actions_mod.catering_amendment_discriminator_enabled() is False
    monkeypatch.setenv("CATERING_AMENDMENT_DISCRIMINATOR", "1")
    assert actions_mod.catering_amendment_discriminator_enabled() is True


# ════════════════════════════════════════════════════════════════════════════
# Clarification choice handler (catering → capture, never create-lead)
# ════════════════════════════════════════════════════════════════════════════
def _wire_choice(monkeypatch, hooks_mod, actions_mod, *, pending, eligible, capture=None):
    s = _Spies()
    if capture is None:
        capture = CaptureResult(ok=True, amendment_id="A0002", idempotent=False)
    monkeypatch.setattr(actions_mod, "get_revenue_route_clarification", lambda cid: dict(pending))
    monkeypatch.setattr(actions_mod, "pop_revenue_route_clarification", lambda cid: dict(pending))
    monkeypatch.setattr(actions_mod, "lid_to_phone_via_identify_sender", lambda cid: (PHONE, "customer"))
    monkeypatch.setattr(actions_mod, "find_all_eligible_catering_leads_by_sender", lambda p, c: list(eligible))
    monkeypatch.setattr(actions_mod, "audit_intercepted", lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(actions_mod, "send_canonical_followup_reply",
                        lambda cid, lid: s.canonical.append((cid, lid)) or True)
    monkeypatch.setattr(actions_mod, "send_flyer_text",
                        lambda cid, txt, **kw: s.sent.append((cid, txt)) or (True, "mid", ""))
    monkeypatch.setattr(actions_mod, "save_revenue_route_clarification",
                        lambda **kw: s.clar_saved.append(kw))
    monkeypatch.setattr(hooks_mod, "_send_amendment_retry_reply",
                        lambda cid, lid: s.retry.append((cid, lid)) or True)

    def _cap(**kw):
        s.capture_calls.append(kw)
        return capture
    monkeypatch.setattr(hooks_mod.catering_amendments, "capture_branch_b_amendment", _cap)
    # trigger MUST never be reachable from an amendment candidate
    monkeypatch.setattr(hooks_mod, "_try_f7_primary_intercept",
                        lambda *a, **k: s.audits.append({"reason": "F7_CREATE_LEAD_REACHED"}))
    monkeypatch.setattr(hooks_mod, "_try_flyer_active_project_intercept",
                        lambda *a, **k: s.audits.append({"reason": "flyer_arm_reached"}) or {"action": "skip", "reason": "flyer"})
    return s


def _pending(lead_ids, text="actually 60 guests"):
    return {"kind": "amendment_conflict", "original_text": text, "message_id": "wamid.OLD",
            "lead_ids": lead_ids, "chat_id": CHAT}


def test_choice_catering_single_lead_captures_no_new_lead(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire_choice(monkeypatch, hooks_mod, actions_mod,
                     pending=_pending(["L0011"]), eligible=[_lead("L0011")])
    out = hooks_mod._try_revenue_route_clarification_choice(
        "catering", CHAT, _event(), flyer_generation_enabled=True, flyer_workflow_enabled=True)
    assert out["action"] == "skip" and "captured" in out["reason"]
    assert len(s.capture_calls) == 1 and s.capture_calls[0]["source"] == "conflict_discriminator"
    assert "F7_CREATE_LEAD_REACHED" not in _reasons(s), "amendment candidate must NEVER create a lead"
    assert s.capture_calls[0]["text"] == "actually 60 guests", "the STORED original text is captured"


def test_choice_flyer_routes_to_flyer_arm(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire_choice(monkeypatch, hooks_mod, actions_mod,
                     pending=_pending(["L0011"]), eligible=[_lead("L0011")])
    out = hooks_mod._try_revenue_route_clarification_choice(
        "flyer", CHAT, _event(), flyer_generation_enabled=True, flyer_workflow_enabled=True)
    assert out == {"action": "skip", "reason": "flyer"}
    assert s.capture_calls == [], "flyer choice does not capture"
    assert "flyer_arm_reached" in _reasons(s)


def test_choice_catering_multi_reask_never_silent(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire_choice(monkeypatch, hooks_mod, actions_mod,
                     pending=_pending(["L0011", "L0012"]),
                     eligible=[_lead("L0011"), _lead("L0012")])
    out = hooks_mod._try_revenue_route_clarification_choice(
        "catering", CHAT, _event(), flyer_generation_enabled=True, flyer_workflow_enabled=True)
    assert out["action"] == "skip" and "clarification" in out["reason"]
    assert s.capture_calls == [], "ambiguous catering choice must NOT silently pick a lead"


def test_choice_unrecognized_defers(monkeypatch):
    hooks_mod, actions_mod = _load_plugin()
    s = _wire_choice(monkeypatch, hooks_mod, actions_mod,
                     pending=_pending(["L0011"]), eligible=[_lead("L0011")])
    out = hooks_mod._try_revenue_route_clarification_choice(
        "hello there", CHAT, _event(), flyer_generation_enabled=True, flyer_workflow_enabled=True)
    assert out is None
    assert s.capture_calls == []


# ════════════════════════════════════════════════════════════════════════════
# Static placement proof — the gate is hoisted BEFORE the flyer active-project arm
# in pre_gateway_dispatch (the canary lesson: logic after the flyer terminal arm is
# ineffective). Source-scan (ast) so it runs on every platform.
# ════════════════════════════════════════════════════════════════════════════
def test_conflict_gate_call_precedes_flyer_active_project_arm():
    import ast
    src = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_pre_gateway_dispatch_impl")
    gate_line = None
    flyer_lines = []
    for node in ast.walk(dispatch):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "_try_amendment_conflict_intercept" and gate_line is None:
                gate_line = node.lineno
            if node.func.id == "_try_flyer_active_project_intercept":
                flyer_lines.append(node.lineno)
    assert gate_line is not None, "conflict gate is not wired into the dispatch"
    assert flyer_lines, "flyer active-project arm not found in the dispatch"
    # the gate must be hoisted BEFORE the flyer active-project arm it guards (a gate
    # after the flyer terminal arm is ineffective — the canary lesson).
    assert any(fl > gate_line for fl in flyer_lines), (
        f"conflict gate (line {gate_line}) MUST precede the flyer active-project arm "
        f"(lines {sorted(flyer_lines)})")
