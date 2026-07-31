"""M1 — cf-router F7 wiring for the qualification loop + amendment application.

Loads hooks + actions as submodules of a synthetic package (the plugin dir name
`cf-router` has a hyphen), mirroring tests/test_cf_router_plugin.py's loader, and
spies the subprocess-invoking action helpers so the ROUTING decision is what is
under test — the scripts themselves are pinned by tests/test_amend_catering_lead.py
and tests/test_create_catering_lead_qualification.py.

Unlike test_cf_router_plugin.py this suite is NOT Windows-skipped: the fcntl stub
(fixtures_fleet, the same one test_catering_amendment_capture.py installs) makes
safe_io importable, and every seam that would touch the filesystem or the bridge is
spied. Nothing here depends on real advisory locking, so the routing assertions are
identical on both platforms — which matters because these are the cells that pin
the M1 routing decisions.

What is pinned:
  * a new inquiry is created WITH the qualification gate, except when a proposal
    set is about to be generated (turn arbitration: one response per inbound)
  * a reply to our own intake question reaches the lead even though it carries no
    catering signal at all, and is applied to the SAME lead — no second lead
  * a message that CONTRADICTS the lead still belongs to the ruled fresh-vs-stale
    discriminator, not to the answer arm
  * a captured amendment is APPLIED inline, and a replay is not re-applied
  * flag-off restores the pre-M1 routing exactly
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
PLUGIN_DIR = SRC / "plugins" / "cf-router"
PLATFORM_DIR = SRC / "platform"
for _p in (SRC, PLATFORM_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load_plugin_modules():
    """Load hooks + actions as submodules of a synthetic parent package so the
    relative `from . import actions` in hooks.py resolves (the plugin dir name has
    a hyphen and cannot be imported by name).

    Copied from tests/test_catering_pra_reachability.py, NOT from
    test_cf_router_plugin.py: that suite also evicts `schemas` / `safe_io` from
    sys.modules and re-inserts the platform dir at sys.path[0] on every load, which
    rebinds those modules underneath every already-imported flyer test module in the
    same process. It gets away with it only because it is Windows-skipped. Loading
    without the eviction leaves co-resident suites untouched, which is what lets
    THIS suite run everywhere.
    """
    pkg_name = "cf_router_qual_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = importlib.util.module_from_spec(pkg_spec)

    loaded = {}
    for name in ("actions", "hooks"):
        full = f"{pkg_name}.{name}"
        loader = importlib.machinery.SourceFileLoader(full, str(PLUGIN_DIR / f"{name}.py"))
        spec = importlib.util.spec_from_loader(full, loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        loader.exec_module(mod)
        loaded[name] = mod
    return loaded["hooks"], loaded["actions"]


class Spy:
    def __init__(self):
        self.creates: list[dict] = []
        self.amends: list[dict] = []
        self.audits: list[dict] = []
        self.captures: list[dict] = []
        self.canonical: list[tuple] = []


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Plugin loaded with every outbound seam spied and no lead for the sender."""
    hooks, actions = _load_plugin_modules()
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    actions.LEADS_PATH = state / "catering-leads.json"
    actions.PROPOSALS_PATH = state / "catering-proposals.json"
    actions.LOG_PATH = logs / "decisions.log"
    actions.CONFIG_PATH = tmp_path / "config.yaml"
    actions.CONFIG_PATH.write_text("owner:\n  self_chat_jid: 15550100002@s.whatsapp.net\n",
                                   encoding="utf-8")
    actions.PLATFORM_DIR = PLATFORM_DIR

    s = Spy()
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender",
                        lambda cid: ("+19045550199", "customer"))
    monkeypatch.setattr(actions, "audit_intercepted",
                        lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(actions, "trigger_create_catering_lead",
                        lambda **kw: (s.creates.append(kw), (True, '{"lead_id":"L0001"}'))[1])
    monkeypatch.setattr(actions, "invoke_amend_catering_lead",
                        lambda lead_id, **kw: (s.amends.append({"lead_id": lead_id, **kw}), 0)[1])
    monkeypatch.setattr(actions, "invoke_create_catering_proposals",
                        lambda *a: 0)
    monkeypatch.setattr(actions, "find_selectable_proposal_set", lambda lid: None)
    monkeypatch.setattr(actions, "send_canonical_followup_reply",
                        lambda *a: (s.canonical.append(a), True)[1])
    monkeypatch.setattr(actions, "find_active_catering_lead_by_sender",
                        lambda p, c: None)
    return SimpleNamespace(hooks=hooks, actions=actions, spy=s, state=state)


def _set_active_lead(wired, monkeypatch, lead):
    monkeypatch.setattr(wired.actions, "find_active_catering_lead_by_sender",
                        lambda p, c: lead)


def _capture_result(wired, monkeypatch, *, ok=True, idempotent=False):
    result = wired.hooks.catering_amendments.CaptureResult(
        ok=ok, amendment_id="A0001", idempotent=idempotent,
    )
    monkeypatch.setattr(wired.hooks.catering_amendments, "capture_branch_b_amendment",
                        lambda **kw: (wired.spy.captures.append(kw), result)[1])


def _inbound(wired, text, chat_id="19045550199@s.whatsapp.net"):
    return wired.hooks._try_f7_primary_intercept(
        text, chat_id, SimpleNamespace(text=text, chat_id=chat_id, message_id="wamid.IN"),
        signals=wired.actions.classify_catering(text)[1],
        allow_new_lead=wired.actions.classify_catering(text)[0],
    )


QUALIFYING_LEAD = {
    "lead_id": "L0001", "status": "QUALIFYING",
    "customer_phone": "+19045550199", "owner_approval_code": "#ABCDE",
    "extracted": {"headcount": 120},
    "pending_questions": ["event_date", "event_type"],
    "questions_asked": ["event_date", "event_type"],
    "created_at": "2026-08-01T12:00:00+00:00",
}


# ── Branch A: creation carries the gate ──────────────────────────────────────
def test_new_inquiry_is_created_with_the_qualification_gate(wired):
    result = _inbound(wired, "Do you do catering for 80 people next month?")

    assert result["action"] == "skip"
    assert len(wired.spy.creates) == 1
    assert wired.spy.creates[0]["qualification_gate"] is True


def test_gate_is_suppressed_when_a_proposal_set_will_be_generated(wired):
    """Turn arbitration allows the customer exactly ONE response per inbound, and a
    proposal ask already claims it — the question batch must not also fire."""
    _inbound(wired, "We need catering for 80 people, can you send me some menu options?")

    assert len(wired.spy.creates) == 1
    assert wired.spy.creates[0]["suppress_customer_ack"] is True
    assert wired.spy.creates[0]["qualification_gate"] is False


def test_flag_off_never_passes_the_gate(wired, monkeypatch):
    monkeypatch.setattr(wired.hooks, "F7_QUALIFICATION_GATE_ENABLED", False)

    _inbound(wired, "Do you do catering for 80 people next month?")

    assert wired.spy.creates[0]["qualification_gate"] is False


# ── Branch B: the slot-filling answer arm ────────────────────────────────────
def test_answer_to_an_intake_question_is_applied_to_the_same_lead(wired, monkeypatch):
    _set_active_lead(wired, monkeypatch, QUALIFYING_LEAD)
    _capture_result(wired, monkeypatch)

    result = _inbound(wired, "It's a wedding on June 15")

    assert result["action"] == "skip"
    assert wired.spy.amends == [
        {"lead_id": "L0001", "mode": "answer", "answer_text": "It's a wedding on June 15",
         "message_id": "wamid.IN"},
    ]
    assert wired.spy.creates == [], "an answer must never mint a second lead"
    assert wired.spy.captures == [], "the answer arm owns the turn; no R2A fallback"
    reasons = [a["reason"] for a in wired.spy.audits]
    assert "f7_qualification_answer" in reasons


def test_bare_venue_answer_with_no_catering_signal_still_reaches_the_lead(wired, monkeypatch):
    """"Grand Ballroom" classifies as nothing. Without the QUALIFYING admission
    widening it would go to the LLM and the loop would break at question one."""
    lead = dict(QUALIFYING_LEAD, pending_questions=["venue"], questions_asked=["venue"])
    _set_active_lead(wired, monkeypatch, lead)
    assert wired.actions.classify_catering("Grand Ballroom")[0] is False

    assert wired.hooks._sender_has_qualifying_lead("19045550199@s.whatsapp.net") is True

    result = wired.hooks._try_f7_primary_intercept(
        "Grand Ballroom", "19045550199@s.whatsapp.net",
        SimpleNamespace(text="Grand Ballroom", chat_id="19045550199@s.whatsapp.net",
                        message_id="wamid.IN"),
        signals=[], allow_new_lead=False,
    )

    assert result["action"] == "skip"
    assert wired.spy.amends[0]["answer_text"] == "Grand Ballroom"


def test_admission_check_ignores_non_qualifying_leads_and_the_owner(wired, monkeypatch):
    _set_active_lead(wired, monkeypatch, dict(QUALIFYING_LEAD, status="AWAITING_OWNER_APPROVAL"))
    assert wired.hooks._sender_has_qualifying_lead("19045550199@s.whatsapp.net") is False

    _set_active_lead(wired, monkeypatch, QUALIFYING_LEAD)
    monkeypatch.setattr(wired.actions, "lid_to_phone_via_identify_sender",
                        lambda cid: ("+19045550100", "owner"))
    assert wired.hooks._sender_has_qualifying_lead("19045550100@s.whatsapp.net") is False


def test_admission_check_never_raises(wired, monkeypatch):
    def _boom(_cid):
        raise RuntimeError("state file exploded")
    monkeypatch.setattr(wired.actions, "lid_to_phone_via_identify_sender", _boom)

    assert wired.hooks._sender_has_qualifying_lead("19045550199@s.whatsapp.net") is False


def test_contradicting_inquiry_still_belongs_to_the_ruled_discriminator(wired, monkeypatch):
    """A message contradicting a field the lead ALREADY has is a different event, not
    an answer. The ruled fresh-vs-stale discriminator must keep winning."""
    lead = dict(QUALIFYING_LEAD,
                extracted={"headcount": 120, "event_date": "2026-09-15"})
    _set_active_lead(wired, monkeypatch, lead)
    _capture_result(wired, monkeypatch)
    monkeypatch.setattr(wired.hooks, "_open_fresh_lead_over_stale",
                        lambda **kw: (wired.spy.creates.append(kw), "L0002")[1])

    result = _inbound(wired, "Need catering for 400 people on December 20 for a wedding")

    assert result["action"] == "skip"
    assert wired.spy.creates, "a contradicting inquiry opens a NEW lead"
    assert wired.spy.amends == [], "and is NOT double-handled as a slot-filling answer"


def test_amendment_phrased_message_keeps_the_r2a_capture_path(wired, monkeypatch):
    _set_active_lead(wired, monkeypatch, QUALIFYING_LEAD)
    _capture_result(wired, monkeypatch)

    result = _inbound(wired, "actually make it 280 people instead")

    assert result["action"] == "skip"
    assert wired.spy.captures, "amendment-phrased text is captured, not read as an answer"
    assert [a["mode"] for a in wired.spy.amends] == ["amendment"]


# ── Amendment application after capture ──────────────────────────────────────
def test_new_capture_is_applied_inline(wired, monkeypatch):
    """THE R2A gap: capture was write-only, so the owner approved pre-amendment
    numbers. The application step now runs on the same turn."""
    _set_active_lead(wired, monkeypatch,
                     dict(QUALIFYING_LEAD, status="AWAITING_OWNER_APPROVAL"))
    _capture_result(wired, monkeypatch, ok=True, idempotent=False)

    result = _inbound(wired, "actually make it 280 people instead")

    assert result["action"] == "skip"
    assert wired.spy.amends == [
        {"lead_id": "L0001", "mode": "amendment", "message_id": "wamid.IN"},
    ]
    assert "f7_primary_amendment_applied" in [a["reason"] for a in wired.spy.audits]
    assert wired.spy.canonical, "the customer still gets the canonical reply"


def test_replayed_capture_is_not_re_applied(wired, monkeypatch):
    _set_active_lead(wired, monkeypatch,
                     dict(QUALIFYING_LEAD, status="AWAITING_OWNER_APPROVAL"))
    _capture_result(wired, monkeypatch, ok=True, idempotent=True)

    result = _inbound(wired, "actually make it 280 people instead")

    assert result["action"] == "skip"
    assert wired.spy.amends == [], "a replay has nothing new to apply"
    assert wired.spy.canonical, "and the canonical reply is unchanged"


def test_failed_capture_is_never_applied(wired, monkeypatch):
    _set_active_lead(wired, monkeypatch,
                     dict(QUALIFYING_LEAD, status="AWAITING_OWNER_APPROVAL"))
    _capture_result(wired, monkeypatch, ok=False)

    result = _inbound(wired, "actually make it 280 people instead")

    assert result["action"] == "skip"
    assert wired.spy.amends == []
    assert "f7_primary_amendment_capture_failed" in [a["reason"] for a in wired.spy.audits]


def test_flag_off_restores_pre_m1_branch_b_routing(wired, monkeypatch):
    monkeypatch.setattr(wired.hooks, "F7_QUALIFICATION_GATE_ENABLED", False)
    _set_active_lead(wired, monkeypatch, QUALIFYING_LEAD)
    _capture_result(wired, monkeypatch)

    result = _inbound(wired, "It's a wedding on June 15")

    assert result["action"] == "skip"
    assert wired.spy.amends == [], "no answer arm and no application when flag-off"
    assert wired.spy.captures, "the inbound still lands durably in the R2A sidecar"
