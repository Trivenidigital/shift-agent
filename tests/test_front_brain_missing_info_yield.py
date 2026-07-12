"""Fix 1 (2026-07-12) — front-brain yield on the REAL vague-brief lever.

Incident (prod logs 2026-07-12): the pilot armed FRONT_BRAIN_CONVERSE but Hermes
NEVER conversed (ZERO front_brain_yielded rows). An established trial customer
WITH an active project sent "Create a flyer for Saturday" and routed:
  flyer_active_project_bypassed(fresh_flyer_intent=true)
    -> flyer_primary_project_created F0218
    -> DETERMINISTIC flyer_project_missing_info_reply ("I need a few more details").
The Phase-1 yield gated only the COLD-START intercepts (sample-prompt /
intake-followup / vague-start) which an established customer with an active
project NEVER hits. The real deterministic "ask for details" lever is
`flyer_project_missing_info_reply`, sent at four hooks.py sites. Those sites now
yield to Hermes for the CONVERSE cohort.

This file drives `_pre_gateway_dispatch_impl` end-to-end reproducing the EXACT
pilot shape (the test that would have caught the incident) plus a focused
`_try_flyer_primary_intercept` unit for the yield seam, and pins flag-off
byte-identical behavior across all four missing-info sites.

cf-router actions/hooks import safe_io (fcntl-only) -> Docker/Linux, not Windows.
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
    reason="cf-router actions/hooks import safe_io (fcntl-only); runs on Linux CI",
)

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"
CHAT = "17329837841@c.us"
PHONE = "+17329837841"
VAGUE_BRIEF = "Create a flyer for Saturday"
INCOMPLETE_PROJECT = {"project_id": "F0218", "fields": {}, "raw_request": VAGUE_BRIEF}
MISSING_INFO_SENTINEL = "MISSING_INFO_DETERMINISTIC_REPLY"


def _load():
    sys.path.insert(0, str(PLATFORM_DIR))
    pkg = "cf_router_missing_info_yield_pkg"
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
    rec = {"sends": [], "yields": []}

    def set_a(name, fn):
        monkeypatch.setattr(actions, name, fn, raising=False)

    def set_h(name, fn):
        monkeypatch.setattr(hooks, name, fn, raising=False)

    # ── inbound plumbing / identity: established trial customer ──────────────
    set_a("mark_cf_router_inbound_seen", lambda *a, **k: False)
    set_a("audit_raw_body", lambda *a, **k: None)
    set_a("is_flyer_enabled", lambda: True)               # generation + workflow enabled
    set_a("is_flyer_workflow_enabled", lambda: True)
    set_a("is_owner_chat", lambda _c: False)
    set_a("is_verified_employee_chat", lambda _c: False)
    set_a("lid_to_phone_via_identify_sender", lambda _c: (PHONE, "customer"))
    set_a("find_flyer_customer_by_sender", lambda *_a: {"customer_id": "C1", "status": "trial"})
    set_h("_is_sick_call", lambda _t: False)
    monkeypatch.setattr(hooks, "F7_ENABLED", False, raising=False)

    # ── pre-primary intercepts benign; active-project intercept BYPASSES ─────
    for name in (
        "_try_revenue_route_clarification_choice", "_try_flyer_quote_echo_choice",
        "_try_flyer_account_intercept", "_try_flyer_regulated_account_guard",
        "_try_flyer_quote_echo_guard", "_try_flyer_reference_scope_choice_intercept",
        "_try_flyer_source_vs_new_choice_intercept",
        "_try_flyer_reference_scope_authorization_intercept",
        "_try_flyer_brand_asset_intercept", "_try_flyer_existing_onboarding_intercept",
        "_try_flyer_active_project_intercept", "_try_flyer_delivery_state_guard",
        "_try_flyer_onboarding_intercept", "_try_flyer_sample_prompt_request_intercept",
        "_try_flyer_intake_intercept", "_try_flyer_campaign_cta_intercept",
        "_try_revenue_route_clarification_start",
    ):
        set_h(name, lambda *a, **k: None)

    set_a("flyer_campaign_cta_text", lambda _t: "")
    set_a("is_flyer_approval_text", lambda _t: False)
    set_a("is_flyer_send_now_intent", lambda _t: False)
    set_a("find_paid_flyer_guest_order", lambda *_a: None)
    set_a("is_registered_customer_contextual_flyer_brief", lambda _t: False)
    set_a("is_flyer_legacy_trial_link_followup", lambda _t: False)
    set_a("classify_catering", lambda _t: (False, []))
    set_a("classify_flyer_intent", lambda _t: (True, []))
    set_a("is_flyer_onboarding_intent", lambda _t: False)
    # "Create a flyer for Saturday": a fresh new-flyer intent (NOT a vague-start;
    # is_vague_flyer_start returns False for "flyer for <day>"), so it reaches
    # _try_flyer_primary_intercept(force_new=True) — exactly the pilot path.
    set_a("should_start_new_flyer_over_active", lambda _t, **k: True)
    set_a("is_vague_flyer_start", lambda _t, **k: False)
    set_a("find_flyer_intake_session_by_sender", lambda *_a: None)

    # ── _try_flyer_primary_intercept internals -> create + missing-info ─────
    set_a("flyer_business_scope_block_message", lambda *_a, **_k: "")
    set_a("flyer_location_block_message", lambda *_a, **_k: "")
    set_a("is_exact_reference_edit_request", lambda *_a, **_k: False)
    set_a("trigger_create_flyer_project", lambda **k: (True, "created", dict(INCOMPLETE_PROJECT)))
    set_a("flyer_project_has_manual_review_queued", lambda *_a: False)
    set_a("flyer_project_has_required_fields", lambda *_a: False)   # too vague
    set_a("flyer_project_missing_info_reply", lambda *_a: MISSING_INFO_SENTINEL)

    # ── recorders ───────────────────────────────────────────────────────────
    set_a("send_flyer_text", lambda _c, _m, **k: rec["sends"].append(_m) or (True, "mid", ""))
    set_a("audit_intercepted", lambda **k: None)

    def _yield(chat_id, *, intercept, message_id=""):
        rec["yields"].append(intercept)

    set_a("audit_front_brain_yielded", _yield)

    def run(admits):
        set_a("front_brain_converse_admits", lambda *_a, **_k: admits)
        ev = SimpleNamespace(text=VAGUE_BRIEF, chat_id=CHAT, message_id="m-1")
        return hooks._pre_gateway_dispatch_impl(ev)

    return hooks, actions, rec, run, set_a, set_h


# ── headline: the test that would have caught the incident ────────────────────

def test_pilot_shape_yields_missing_info_when_admitted(wired):
    hooks, actions, rec, run, _sa, _sh = wired
    result = run(admits=True)
    assert result is None                          # yielded to the LLM / Hermes
    assert rec["yields"] == ["missing_info"]        # front_brain_yielded marker
    assert MISSING_INFO_SENTINEL not in rec["sends"]  # deterministic reply NOT sent


def test_pilot_shape_deterministic_when_not_admitted(wired):
    hooks, actions, rec, run, _sa, _sh = wired
    result = run(admits=False)
    # byte-identical deterministic net: create + send the missing-info reply, skip.
    assert isinstance(result, dict) and result["action"] == "skip"
    assert rec["sends"] == [MISSING_INFO_SENTINEL]
    assert rec["yields"] == []


# ── focused seam: _try_flyer_primary_intercept fresh-create missing-info ──────

def test_primary_intercept_missing_info_yields_only_for_cohort(wired):
    hooks, actions, rec, _run, set_a, _sh = wired
    ev = SimpleNamespace(text=VAGUE_BRIEF, chat_id=CHAT, message_id="m-2")

    set_a("front_brain_converse_admits", lambda *_a, **_k: True)
    admitted = hooks._try_flyer_primary_intercept(VAGUE_BRIEF, CHAT, ev, force_new=True)
    assert admitted is None
    assert rec["yields"] == ["missing_info"]
    assert rec["sends"] == []

    rec["yields"].clear()
    set_a("front_brain_converse_admits", lambda *_a, **_k: False)
    deterministic = hooks._try_flyer_primary_intercept(VAGUE_BRIEF, CHAT, ev, force_new=True)
    assert isinstance(deterministic, dict) and deterministic["action"] == "skip"
    assert rec["sends"] == [MISSING_INFO_SENTINEL]
    assert rec["yields"] == []


# ── the yield guard itself (shared by all four missing-info sites) ────────────

def test_fb_yield_missing_info_guard_is_fail_closed(wired):
    hooks, actions, rec, _run, set_a, _sh = wired
    set_a("front_brain_converse_admits", lambda *_a, **_k: False)
    assert hooks._fb_yield_missing_info(CHAT, "m-1") is False
    assert rec["yields"] == []                      # no marker when not admitted

    set_a("front_brain_converse_admits", lambda *_a, **_k: True)
    assert hooks._fb_yield_missing_info(CHAT, "m-1") is True
    assert rec["yields"] == ["missing_info"]


# ── structural invariant: every missing-info send is guarded (all 4 sites) ────

def test_all_four_missing_info_sites_are_yield_guarded():
    source = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    # Four deterministic missing-info sends, each fronted by the yield guard.
    assert source.count("actions.flyer_project_missing_info_reply(") == 4
    assert source.count("if _fb_yield_missing_info(chat_id, message_id):") == 4
