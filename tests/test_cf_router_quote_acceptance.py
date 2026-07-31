"""M3 — cf-router routing for a customer's accept/decline of a sent quote.

The routing decision is what is under test here; the writer itself is pinned by
tests/test_record_catering_acceptance.py and the grammar by
tests/test_catering_acceptance_detector.py. Every subprocess-invoking action helper
is spied.

Loader note: this uses the NON-EVICTING synthetic-package loader (copied from
tests/test_cf_router_qualification_loop.py), NOT test_cf_router_plugin.py's — that
one evicts `schemas` / `safe_io` from sys.modules and re-inserts the platform dir at
sys.path[0] on every load, rebinding those modules underneath every already-imported
suite in the same process. It gets away with it only because it is Windows-skipped.
Loading without the eviction leaves co-resident suites untouched, which is what lets
this suite run everywhere.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
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
    pkg_name = "cf_router_acceptance_pkg"
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


SENT_LEAD = {
    "lead_id": "L0007", "status": "SENT_TO_CUSTOMER",
    "customer_phone": "+19045550199", "owner_approval_code": "#ABCDE",
    "quote_text": "Quote for L0007 ...", "created_at": "2026-07-20T12:00:00+00:00",
}


class Spy:
    def __init__(self):
        self.records: list[dict] = []
        self.audits: list[dict] = []
        self.amend_captures: list[dict] = []


@pytest.fixture
def wired(tmp_path, monkeypatch):
    hooks, actions = _load_plugin_modules()
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    actions.LEADS_PATH = state / "catering-leads.json"
    actions.PROPOSALS_PATH = state / "catering-proposals.json"
    actions.LOG_PATH = logs / "decisions.log"
    actions.CONFIG_PATH = tmp_path / "config.yaml"
    actions.CONFIG_PATH.write_text(
        "owner:\n  self_chat_jid: 15550100002@s.whatsapp.net\n", encoding="utf-8")
    actions.PLATFORM_DIR = PLATFORM_DIR

    s = Spy()
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender",
                        lambda cid: ("+19045550199", "customer"))
    monkeypatch.setattr(actions, "audit_intercepted", lambda **kw: s.audits.append(kw))
    monkeypatch.setattr(
        actions, "invoke_record_catering_acceptance",
        lambda lead_id, chat_id, message_id, text, **kw: (
            s.records.append({"lead_id": lead_id, "chat_id": chat_id,
                              "message_id": message_id, "text": text, **kw}), 0)[1],
    )
    monkeypatch.setattr(actions, "has_non_delivered_flyer_project_by_sender",
                        lambda p, c: False)
    return SimpleNamespace(hooks=hooks, actions=actions, spy=s, state=state)


def _seed(wired, *leads):
    wired.actions.LEADS_PATH.write_text(
        json.dumps({"schema_version": 1, "leads": list(leads)}), encoding="utf-8")


def _inbound(wired, text, chat_id="19045550199@s.whatsapp.net", **kw):
    return wired.hooks._try_catering_acceptance_intercept(
        text, chat_id,
        SimpleNamespace(text=text, chat_id=chat_id, message_id="wamid.IN"),
        **kw,
    )


# ── the arm fires ───────────────────────────────────────────────────────────
def test_explicit_accept_reaches_the_acceptance_writer(wired):
    _seed(wired, SENT_LEAD)
    result = _inbound(wired, "we accept")

    assert result["action"] == "skip"
    assert len(wired.spy.records) == 1
    assert wired.spy.records[0]["lead_id"] == "L0007"
    assert wired.spy.records[0]["text"] == "we accept"
    assert wired.spy.records[0]["sender_role"] == "customer"
    assert "f7_quote_acceptance" in [a["reason"] for a in wired.spy.audits]


def test_explicit_decline_reaches_the_acceptance_writer(wired):
    _seed(wired, SENT_LEAD)
    assert _inbound(wired, "we went with someone else")["action"] == "skip"
    assert wired.spy.records[0]["lead_id"] == "L0007"


def test_ambiguous_reply_reaches_the_writer_which_owns_the_one_clarification(wired):
    """The router does NOT decide whether to ask — it forwards, and the writer's
    lead-side marker is what makes the question fire exactly once."""
    _seed(wired, SENT_LEAD)
    assert _inbound(wired, "ok")["action"] == "skip"
    assert wired.spy.records[0]["text"] == "ok"


def test_the_inbound_native_message_id_is_forwarded_as_the_idempotency_anchor(wired):
    _seed(wired, SENT_LEAD)
    _inbound(wired, "we accept")
    assert wired.spy.records[0]["message_id"] == "wamid.IN"


# ── the arm stands down ─────────────────────────────────────────────────────
def test_no_acceptance_signal_falls_through_untouched(wired):
    _seed(wired, SENT_LEAD)
    assert _inbound(wired, "can you send the menu again") is None
    assert wired.spy.records == []
    assert wired.spy.audits == []


def test_amendment_phrasing_keeps_its_existing_precedence(wired):
    """"actually we accept but move the date" is a CHANGE first. The R2A capture
    path is where changes are preserved; booking around it would lose the change."""
    _seed(wired, SENT_LEAD)
    assert _inbound(wired, "actually we accept, but change the date to June 20") is None
    assert wired.spy.records == []


def test_owner_inbound_never_books_on_the_customers_behalf(wired, monkeypatch):
    _seed(wired, SENT_LEAD)
    monkeypatch.setattr(wired.actions, "lid_to_phone_via_identify_sender",
                        lambda cid: ("+19045550100", "owner"))
    assert _inbound(wired, "we accept") is None
    assert wired.spy.records == []


def test_no_post_quote_lead_for_this_sender(wired):
    _seed(wired, dict(SENT_LEAD, customer_phone="+19045550111"))
    assert _inbound(wired, "we accept") is None
    assert wired.spy.records == []


@pytest.mark.parametrize("status", [
    "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED", "OWNER_APPROVED",
    "QUALIFYING", "CLOSED", "STALE", "BOOKED",
])
def test_lead_not_awaiting_a_decision_is_not_claimed(wired, status):
    _seed(wired, dict(SENT_LEAD, status=status))
    assert _inbound(wired, "we accept") is None
    assert wired.spy.records == []


def test_empty_leads_store_is_a_cheap_noop(wired):
    _seed(wired)
    assert _inbound(wired, "we accept") is None


def test_a_sender_with_a_live_flyer_project_is_left_to_existing_routing(wired, monkeypatch):
    """Mirrors the guard the F7 catering block applies, so this arm cannot re-open
    the flyer/catering conflict class R2B-1 closed."""
    _seed(wired, SENT_LEAD)
    monkeypatch.setattr(wired.actions, "has_non_delivered_flyer_project_by_sender",
                        lambda p, c: True)
    assert _inbound(wired, "we accept", flyer_generation_enabled=True) is None
    assert wired.spy.records == []


def test_flag_off_restores_the_pre_m3_routing_exactly(wired, monkeypatch):
    _seed(wired, SENT_LEAD)
    monkeypatch.setattr(wired.hooks, "F7_ACCEPTANCE_ARM_ENABLED", False)
    assert _inbound(wired, "we accept") is None
    assert wired.spy.records == []
    assert wired.spy.audits == []


# ── exit-code handling ──────────────────────────────────────────────────────
@pytest.mark.parametrize(("rc", "handled"), [
    (0, True),    # recorded / clarified / replay
    (6, True),    # recorded; the customer reply POST failed — state IS committed
    (14, True),   # on hold; deliberately did nothing, must not reach the LLM either
    (2, False),   # no signal server-side
    (4, False),   # lead not found / ineligible
    (5, False),   # schema violation
    (9, False),   # illegal transition
    (12, False),  # privilege denied
])
def test_exit_code_decides_whether_the_llm_is_bypassed(wired, monkeypatch, rc, handled):
    _seed(wired, SENT_LEAD)
    monkeypatch.setattr(wired.actions, "invoke_record_catering_acceptance",
                        lambda *a, **k: rc)
    result = _inbound(wired, "we accept")
    assert (result is not None) is handled
    # Either way the attempt is audited, so a fall-through is never silent.
    assert "f7_quote_acceptance" in [a["reason"] for a in wired.spy.audits]


# ── hot-path cost discipline ────────────────────────────────────────────────
def test_a_non_acceptance_inbound_never_resolves_sender_identity(wired, monkeypatch):
    """`identify-sender` is a SUBPROCESS. The regex must gate it, or every
    non-catering message on the box pays for a process spawn."""
    _seed(wired, SENT_LEAD)
    calls = []
    monkeypatch.setattr(wired.actions, "lid_to_phone_via_identify_sender",
                        lambda cid: (calls.append(cid), ("+19045550199", "customer"))[1])
    _inbound(wired, "what time do you open?")
    assert calls == []


def test_an_ambiguous_ok_with_no_post_quote_lead_anywhere_skips_identity_too(
    wired, monkeypatch,
):
    """"ok" is one of the most common inbounds there is; the store read must
    short-circuit before the subprocess when nobody is awaiting a decision."""
    _seed(wired, dict(SENT_LEAD, status="AWAITING_OWNER_APPROVAL"))
    calls = []
    monkeypatch.setattr(wired.actions, "lid_to_phone_via_identify_sender",
                        lambda cid: (calls.append(cid), ("+19045550199", "customer"))[1])
    assert _inbound(wired, "ok") is None
    assert calls == []


# ── dispatch-level wiring ───────────────────────────────────────────────────
def test_dispatch_runs_the_acceptance_arm_before_the_catering_admission():
    """"we accept" carries no catering signal, so the F7 admission check below
    would drop it. Source-order pin: the arm must precede classify_catering."""
    text = (PLUGIN_DIR / "hooks.py").read_text(encoding="utf-8")
    arm = text.index("acceptance_result = _try_catering_acceptance_intercept(")
    admission = text.index("is_catering, signals = actions.classify_catering(text)")
    assert arm < admission
