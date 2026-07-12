"""Item 2 — cf-router front-brain yield routing (fcntl-gated / Docker).

Drives _pre_gateway_dispatch_impl with the pre-site guards mocked benign so
control reaches the three conversational intercepts, and asserts:
  - admitted cohort  -> vague-start / sample-prompt / intake-followup YIELD to
    the LLM (impl returns None), a front_brain_yielded marker is written, and the
    deterministic answer (send_flyer_text / the `_try_*` intercept) does NOT run;
  - non-cohort       -> byte-identical: the deterministic intercepts run exactly
    as today (the `_try_*` helpers are called; a vague start is answered);
  - a money/state guard (delivery-state) between the yield sites STILL fires for
    an admitted chat and is NOT yielded.

actions.audit_intercepted imports safe_io (fcntl only) -> Docker, not Windows.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="actions.audit_intercepted imports safe_io (fcntl only)",
)

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"
CHAT = "17329837841@c.us"


def _load():
    sys.path.insert(0, str(PLATFORM_DIR))
    pkg = "cf_router_fb_yield_pkg"
    for m in list(sys.modules):
        if m == pkg or m.startswith(pkg + "."):
            del sys.modules[m]
    pkg_spec = importlib.machinery.ModuleSpec(pkg, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg] = importlib.util.module_from_spec(pkg_spec)
    for name in ("actions", "hooks"):
        full = f"{pkg}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
    return sys.modules[f"{pkg}.hooks"], sys.modules[f"{pkg}.actions"]


@pytest.fixture
def wired(monkeypatch):
    hooks, actions = _load()
    rec = {"sends": [], "yields": [], "sample_intercept": 0, "intake_intercept": 0}

    def set_a(name, fn):
        monkeypatch.setattr(actions, name, fn, raising=False)

    def set_h(name, fn):
        monkeypatch.setattr(hooks, name, fn, raising=False)

    # ── inbound plumbing / identity ─────────────────────────────────────────
    set_a("mark_cf_router_inbound_seen", lambda *a, **k: False)
    set_a("audit_raw_body", lambda *a, **k: None)
    set_a("is_flyer_enabled", lambda: True)
    set_a("is_flyer_workflow_enabled", lambda: True)
    set_a("is_owner_chat", lambda _c: False)
    set_a("is_verified_employee_chat", lambda _c: False)
    set_a("lid_to_phone_via_identify_sender", lambda _c: ("+17329837841", "customer"))
    set_a("find_flyer_customer_by_sender", lambda *_a: {"customer_id": "C1", "status": "trial"})
    set_h("_is_sick_call", lambda _t: False)
    monkeypatch.setattr(hooks, "F7_ENABLED", False, raising=False)

    # ── every pre-site + post-site intercept benign (None) ──────────────────
    for name in (
        "_try_revenue_route_clarification_choice", "_try_flyer_quote_echo_choice",
        "_try_flyer_account_intercept", "_try_flyer_regulated_account_guard",
        "_try_flyer_quote_echo_guard", "_try_flyer_reference_scope_choice_intercept",
        "_try_flyer_source_vs_new_choice_intercept",
        "_try_flyer_reference_scope_authorization_intercept",
        "_try_flyer_brand_asset_intercept", "_try_flyer_existing_onboarding_intercept",
        "_try_flyer_active_project_intercept", "_try_flyer_delivery_state_guard",
        "_try_flyer_primary_intercept", "_try_flyer_onboarding_intercept",
        "_try_revenue_route_clarification_start", "_try_flyer_campaign_cta_intercept",
    ):
        set_h(name, lambda *a, **k: None)

    set_a("flyer_campaign_cta_text", lambda _t: "")
    set_a("is_flyer_approval_text", lambda _t: False)
    set_a("is_flyer_send_now_intent", lambda _t: False)
    set_a("find_paid_flyer_guest_order", lambda *_a: None)
    set_a("is_registered_customer_contextual_flyer_brief", lambda _t: False)
    set_a("should_start_new_flyer_over_active", lambda _t, **k: False)
    set_a("classify_catering", lambda _t: (False, []))
    set_a("classify_flyer_intent", lambda _t: (True, []))
    set_a("is_flyer_onboarding_intent", lambda _t: False)
    # default: no vague start, no intake session, no sample-prompt match
    set_a("is_vague_flyer_start", lambda _t, **k: False)
    set_a("find_flyer_intake_session_by_sender", lambda *_a: None)

    # ── recorders for the deterministic answers we assert are NOT taken ─────
    def _sample(*a, **k):
        rec["sample_intercept"] += 1
        return None

    def _intake(*a, **k):
        rec["intake_intercept"] += 1
        return None

    set_h("_try_flyer_sample_prompt_request_intercept", _sample)
    set_h("_try_flyer_intake_intercept", _intake)
    set_a("send_flyer_text", lambda _c, _m, **k: rec["sends"].append(_m) or (True, "mid", ""))
    set_a("flyer_vague_request_clarification_reply", lambda _c: "What should it look like?")
    set_a("flyer_starter_prompts_enabled", lambda _c: False)
    set_a("is_flyer_legacy_trial_link_followup", lambda _t: False)

    def _yield(chat_id, *, intercept, message_id=""):
        rec["yields"].append(intercept)

    set_a("audit_front_brain_yielded", _yield)
    set_a("audit_intercepted", lambda **k: None)

    def run(text, admits, media=None):
        set_a("front_brain_converse_admits", lambda *_a, **_k: admits)
        ev = SimpleNamespace(text=text, chat_id=CHAT, message_id="m-1")
        if media:
            ev.image_path = media
        return hooks._pre_gateway_dispatch_impl(ev)

    return hooks, actions, rec, run, set_a, set_h


# ── vague-start ─────────────────────────────────────────────────────────────

def test_vague_start_yields_when_admitted(wired):
    hooks, actions, rec, run, set_a, _ = wired
    set_a("is_vague_flyer_start", lambda _t, **k: True)
    result = run("Create a weekend flyer", admits=True)
    assert result is None                      # yielded to the LLM
    assert rec["yields"] == ["vague_start"]     # marker recorded
    assert rec["sends"] == []                   # deterministic net did NOT answer


def test_vague_start_deterministic_when_not_admitted(wired):
    hooks, actions, rec, run, set_a, _ = wired
    set_a("is_vague_flyer_start", lambda _t, **k: True)
    result = run("Create a weekend flyer", admits=False)
    # byte-identical deterministic net: it answers (clarification sent) + skips
    assert isinstance(result, dict) and result["action"] == "skip"
    assert rec["sends"] == ["What should it look like?"]
    assert rec["yields"] == []


# ── sample-prompt ─────────────────────────────────────────────────────────────

def test_sample_prompt_yields_when_admitted(wired):
    hooks, actions, rec, run, set_a, _ = wired
    result = run("give me some flyer caption ideas", admits=True)
    assert result is None
    assert "sample_prompt" in rec["yields"]
    assert rec["sample_intercept"] == 0        # deterministic menu bypassed


def test_sample_prompt_intercept_runs_when_not_admitted(wired):
    hooks, actions, rec, run, set_a, _ = wired
    run("give me some flyer caption ideas", admits=False)
    assert rec["sample_intercept"] == 1        # byte-identical: intercept ran
    assert rec["yields"] == []


# ── intake follow-up ──────────────────────────────────────────────────────────

def test_intake_followup_yields_when_admitted_with_session(wired):
    hooks, actions, rec, run, set_a, _ = wired
    set_a("find_flyer_intake_session_by_sender", lambda *_a: {"status": "text_awaiting_brief"})
    result = run("make the header blue", admits=True)
    assert result is None
    assert "intake_followup" in rec["yields"]
    assert rec["intake_intercept"] == 0

def test_intake_intercept_runs_when_not_admitted(wired):
    hooks, actions, rec, run, set_a, _ = wired
    set_a("find_flyer_intake_session_by_sender", lambda *_a: {"status": "text_awaiting_brief"})
    run("make the header blue", admits=False)
    assert rec["intake_intercept"] == 1
    assert rec["yields"] == []


# ── guards stay for admitted chats ────────────────────────────────────────────

def test_delivery_state_guard_still_fires_for_admitted_chat(wired):
    hooks, actions, rec, run, set_a, set_h = wired
    guard = {"action": "skip", "reason": "cf-router flyer delivery state guard"}
    set_h("_try_flyer_delivery_state_guard", lambda *a, **k: guard)
    result = run("where is my order", admits=True)
    assert result == guard                      # guard fired, not yielded
    assert rec["yields"] == []


# ── the yield audit helper wiring (real audit_front_brain_yielded) ────────────

def test_audit_front_brain_yielded_emits_reason(monkeypatch):
    # Fresh load (no routing fixture) so the REAL helper is exercised: it must
    # emit a cf_router_intercepted row with reason `front_brain_yielded`.
    _hooks, actions = _load()
    calls = []
    monkeypatch.setattr(actions, "audit_intercepted", lambda **k: calls.append(k))
    actions.audit_front_brain_yielded(CHAT, intercept="vague_start", message_id="m9")
    assert calls and calls[0]["reason"] == "front_brain_yielded"
    assert "intercept=vague_start" in calls[0]["detail"]
