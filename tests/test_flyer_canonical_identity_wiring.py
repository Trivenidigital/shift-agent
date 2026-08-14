"""Canonical-identity wiring — LID<->phone convergence across the Flyer surface.

Reconstructs the 2026-06-02 identity split (F0133 keyed to the LID
`100000000000001@lid`; a stale intake session keyed to the phone-JID for the
SAME customer +15550100001) and asserts the three wiring targets converge once
the lid-cache knows the pairing:
  (1) FlyerIntakeSession keying/lookup (cf-router finder/discard + schemas store)
  (2) the B1 shadow-LLM allowlist gate
  (3) an unmapped LID still falls back to its own raw key (no false convergence)

2026-08-14 — the same convergence is now asserted for the two Flyer surfaces
that were left behind when (1) landed, both found by the session-wipe review:
  (4) FlyerOnboardingSession lookup/replace/discard. Intake converged in 2026-06;
      onboarding never did, so a principal seen LID-only and later phone-resolved
      (or the reverse) accumulated TWO onboarding_sessions rows.
  (5) `find_recent_flyer_manual_edit_project`, which compared phone strings RAW
      while every sibling lookup canonicalizes — so a row stored as
      "15550100001" never matched a caller holding "+15550100001", the 60s
      idempotent-retry never fired, and a duplicate project was created.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_WINDOWS = platform.system() == "Windows"

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM = REPO / "src" / "platform"
for _p in (str(PLATFORM), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PHONE = "+15550100001"
PHONE_JID = "15550100001@s.whatsapp.net"
LID = "100000000000001@lid"
UNMAPPED_LID = "555000111222333@lid"

# A second, genuinely different customer. Every convergence assertion below is
# paired with a non-merge assertion using this principal, so a fix that
# converges by collapsing everything onto one key fails instead of passing.
OTHER_PHONE = "+15550100002"
OTHER_PHONE_JID = "15550100002@s.whatsapp.net"


def _load_actions():
    module_name = "cf_router_actions_canonical_wiring"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(PLUGIN_DIR / "actions.py"))
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    loader.exec_module(mod)
    return mod


def _write_lid_cache(path: Path) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "pairs": [{"phone": PHONE, "lid": LID}]}),
        encoding="utf-8",
    )


def _customers_with_intake(session_chat_id: str, session_phone) -> dict:
    # Fresh updated_at so the identity-convergence assertions are TTL-agnostic
    # (the cf-router finder applies read-time TTL expiry, P0-2a).
    fresh = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "next_customer_sequence": 2,
        "next_brand_asset_sequence": 1,
        "customers": [],
        "onboarding_sessions": [],
        "intake_sessions": [
            {
                "chat_id": session_chat_id,
                "sender_phone": session_phone,
                "status": "choosing_mode",
                "source": "start_trial",
                "started_at": fresh,
                "updated_at": fresh,
            }
        ],
    }


# ── (1) cf-router finder/discard convergence ──────────────────────────────────

def test_finder_converges_phone_session_via_mapped_lid(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_intake(PHONE_JID, PHONE)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    # Query via the LID (phone unresolved by identify-sender for a customer):
    found = actions.find_flyer_intake_session_by_sender(None, LID)
    assert found is not None and found["chat_id"] == PHONE_JID


def test_finder_unmapped_lid_does_not_hijack_phone_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_intake(PHONE_JID, PHONE)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    # A DIFFERENT, unmapped LID must not resolve to this customer's session.
    assert actions.find_flyer_intake_session_by_sender(None, UNMAPPED_LID) is None


@pytest.mark.skipif(_WINDOWS, reason="discard write path imports safe_io.FileLock (fcntl — Linux only)")
def test_discard_converges_phone_session_via_mapped_lid(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_intake(PHONE_JID, PHONE)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    assert actions.discard_flyer_intake_session_by_sender(None, LID) is True
    remaining = json.loads(cust.read_text(encoding="utf-8"))["intake_sessions"]
    assert remaining == []


def test_finder_converges_lid_session_via_phone(tmp_path, monkeypatch):
    # Symmetric: session stored under the LID, queried via the resolved phone.
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_intake(LID, None)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    found = actions.find_flyer_intake_session_by_sender(PHONE, PHONE_JID)
    assert found is not None and found["chat_id"] == LID


# ── (2) B1 shadow-LLM allowlist gate ──────────────────────────────────────────

def test_allowlist_phone_entry_admits_mapped_lid(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    monkeypatch.setenv(actions.FLYER_INTENT_SHADOW_LLM_CHATS_ENV, PHONE)
    # The LID chat is admitted by the +phone allowlist entry once mapped.
    assert actions._flyer_intent_shadow_llm_allowlisted(LID) is True
    # The phone chat is admitted directly.
    assert actions._flyer_intent_shadow_llm_allowlisted(PHONE_JID) is True


def test_allowlist_unmapped_lid_not_admitted_by_phone_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    monkeypatch.setenv(actions.FLYER_INTENT_SHADOW_LLM_CHATS_ENV, PHONE)
    assert actions._flyer_intent_shadow_llm_allowlisted(UNMAPPED_LID) is False


def test_allowlist_raw_lid_entry_still_works_as_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    actions = _load_actions()
    # Operator band-aid: the LID listed verbatim still admits it.
    monkeypatch.setenv(actions.FLYER_INTENT_SHADOW_LLM_CHATS_ENV, UNMAPPED_LID)
    assert actions._flyer_intent_shadow_llm_allowlisted(UNMAPPED_LID) is True


# ── (1b) schemas FlyerCustomerStore keying ───────────────────────────────────

def _make_store(chat_id: str, sender_phone):
    import schemas
    session = schemas.FlyerIntakeSession(
        chat_id=chat_id,
        sender_phone=sender_phone,
        status="choosing_mode",
        source="start_trial",
        started_at=datetime(2026, 6, 2, 17, 50, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 2, 17, 50, tzinfo=timezone.utc),
    )
    return schemas.FlyerCustomerStore(intake_sessions=[session]), session


def test_store_find_intake_session_converges_via_mapped_lid(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    store, session = _make_store(PHONE_JID, PHONE)
    # Query via LID with no resolved phone -> same session.
    assert store.find_intake_session(LID, None) is session


def test_store_discard_intake_session_converges_via_mapped_lid(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    import schemas
    store, _session = _make_store(PHONE_JID, PHONE)
    lid_session = schemas.FlyerIntakeSession(
        chat_id=LID,
        sender_phone=None,
        status="choosing_mode",
        source="start_trial",
        started_at=datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 2, 18, 0, tzinfo=timezone.utc),
    )
    # Discarding the LID-identifier session removes the phone-keyed twin too.
    store.discard_intake_session(lid_session)
    assert store.intake_sessions == []


def test_store_replace_intake_session_dedupes_identity_twin(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    import schemas
    store, _session = _make_store(PHONE_JID, PHONE)  # existing phone-keyed session
    new_lid_session = schemas.FlyerIntakeSession(
        chat_id=LID,
        sender_phone=None,
        status="choosing_language",
        source="start_trial",
        started_at=datetime(2026, 6, 2, 18, 5, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 2, 18, 5, tzinfo=timezone.utc),
    )
    store.replace_intake_session(new_lid_session)
    # Exactly one session survives — no LID+phone duplicate for one customer.
    assert len(store.intake_sessions) == 1
    assert store.intake_sessions[0].status == "choosing_language"


# ── (1c) LID-only scoping: `None != None` must not mean "same principal" ──────
#
# Two unmapped LID prospects both carry sender_phone=None. Pre-fix the
# replace/discard predicates kept a row only when its phone was unequal to the
# incoming one, so any new LID-only session evicted every other one.

def _lid_only_session(chat_id: str, *, status="choosing_mode"):
    import schemas
    return schemas.FlyerIntakeSession(
        chat_id=chat_id,
        sender_phone=None,
        status=status,
        source="start_trial",
        started_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )


def test_store_replace_intake_session_keeps_other_lid_only_principal(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    import schemas
    other = _lid_only_session("555000111222444@lid")
    store = schemas.FlyerCustomerStore(intake_sessions=[other])

    store.replace_intake_session(_lid_only_session(UNMAPPED_LID))

    assert {s.chat_id for s in store.intake_sessions} == {UNMAPPED_LID, "555000111222444@lid"}


def test_store_discard_intake_session_keeps_other_lid_only_principal(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    import schemas
    other = _lid_only_session("555000111222444@lid")
    store = schemas.FlyerCustomerStore(
        intake_sessions=[other, _lid_only_session(UNMAPPED_LID)]
    )

    store.discard_intake_session(_lid_only_session(UNMAPPED_LID))

    assert [s.chat_id for s in store.intake_sessions] == ["555000111222444@lid"]


def test_store_replace_intake_session_still_replaces_same_chat_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")
    import schemas
    store = schemas.FlyerCustomerStore(intake_sessions=[_lid_only_session(UNMAPPED_LID)])

    store.replace_intake_session(_lid_only_session(UNMAPPED_LID, status="choosing_language"))

    assert len(store.intake_sessions) == 1
    assert store.intake_sessions[0].status == "choosing_language"
# ── (4) FlyerOnboardingSession convergence ───────────────────────────────────
#
# Intake got the canonical-key clause in 2026-06; onboarding did not. The result
# is the same split the module docstring describes, one surface over: a prospect
# first seen LID-only and later phone-resolved (or the reverse) ends up with TWO
# onboarding_sessions rows, and whichever one the reader happens to hit decides
# what the prospect is asked next.

ONBOARDING_STATUS = "collecting_business_name"
_T0 = datetime(2026, 6, 2, 17, 50, tzinfo=timezone.utc)


def _onboarding_session(chat_id: str, sender_phone, *, status: str = ONBOARDING_STATUS,
                        at: datetime = _T0):
    import schemas
    return schemas.FlyerOnboardingSession(
        chat_id=chat_id,
        sender_phone=sender_phone,
        status=status,
        started_at=at,
        updated_at=at,
    )


def _onboarding_store(*sessions):
    import schemas
    return schemas.FlyerCustomerStore(onboarding_sessions=list(sessions))


def _arm_lid_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(tmp_path / "lid-cache.json"))
    _write_lid_cache(tmp_path / "lid-cache.json")


# read side — FlyerCustomerStore.find_session

def test_store_find_session_converges_phone_session_via_mapped_lid(tmp_path, monkeypatch):
    """Session stored under the phone; the same customer comes back on their LID
    with no resolved phone. The intake twin of this already passes."""
    _arm_lid_cache(tmp_path, monkeypatch)
    session = _onboarding_session(PHONE_JID, PHONE)
    store = _onboarding_store(session)

    assert store.find_session(LID, None) is session


def test_store_find_session_converges_lid_session_via_phone(tmp_path, monkeypatch):
    """The reverse direction: stored LID-only, queried once identify-sender has
    resolved the phone. This is the live shape — a new prospect always arrives
    LID-only, and the phone appears later."""
    _arm_lid_cache(tmp_path, monkeypatch)
    session = _onboarding_session(LID, None)
    store = _onboarding_store(session)

    assert store.find_session(PHONE_JID, PHONE) is session


def test_store_find_session_unmapped_lid_does_not_hijack(tmp_path, monkeypatch):
    """NON-MERGE GUARD — an unmapped LID keys to itself, so it must not resolve
    to somebody else's phone-keyed session."""
    _arm_lid_cache(tmp_path, monkeypatch)
    store = _onboarding_store(_onboarding_session(PHONE_JID, PHONE))

    assert store.find_session(UNMAPPED_LID, None) is None


def test_store_find_session_does_not_merge_two_distinct_principals(tmp_path, monkeypatch):
    """NON-MERGE GUARD — two real customers, two real phones, no convergence."""
    _arm_lid_cache(tmp_path, monkeypatch)
    store = _onboarding_store(_onboarding_session(PHONE_JID, PHONE))

    assert store.find_session(OTHER_PHONE_JID, OTHER_PHONE) is None


# write side — onboarding._replace_session / _discard_session

def test_replace_session_dedupes_the_identity_twin(tmp_path, monkeypatch):
    """THE regression. A LID-only row exists; the same customer writes again once
    phone-resolved. Pre-fix both rows survive, because neither the chat_id nor
    the phone matches across the identifier switch."""
    _arm_lid_cache(tmp_path, monkeypatch)
    from agents.flyer.onboarding import _replace_session
    store = _onboarding_store(_onboarding_session(LID, None))

    _replace_session(store, _onboarding_session(PHONE_JID, PHONE, status="choosing_plan"))

    assert len(store.onboarding_sessions) == 1, [
        (s.chat_id, str(s.sender_phone)) for s in store.onboarding_sessions]
    assert store.onboarding_sessions[0].status == "choosing_plan"


def test_replace_session_dedupes_the_identity_twin_in_reverse(tmp_path, monkeypatch):
    """Same customer, other direction: a phone-keyed row already exists and the
    next inbound arrives LID-only (identify-sender miss, or a fresh device)."""
    _arm_lid_cache(tmp_path, monkeypatch)
    from agents.flyer.onboarding import _replace_session
    store = _onboarding_store(_onboarding_session(PHONE_JID, PHONE))

    _replace_session(store, _onboarding_session(LID, None, status="choosing_plan"))

    assert len(store.onboarding_sessions) == 1, [
        (s.chat_id, str(s.sender_phone)) for s in store.onboarding_sessions]
    assert store.onboarding_sessions[0].status == "choosing_plan"


def test_replace_session_keeps_a_genuinely_different_principal(tmp_path, monkeypatch):
    """NON-MERGE GUARD — the dedupe is scoped to ONE identity. Another customer's
    in-flight onboarding must survive, which is the failure mode #696 fixed for
    the phone-less shape and the one this change must not reintroduce."""
    _arm_lid_cache(tmp_path, monkeypatch)
    from agents.flyer.onboarding import _replace_session
    store = _onboarding_store(_onboarding_session(PHONE_JID, PHONE))

    _replace_session(store, _onboarding_session(OTHER_PHONE_JID, OTHER_PHONE))

    assert sorted(s.chat_id for s in store.onboarding_sessions) == sorted(
        [PHONE_JID, OTHER_PHONE_JID])


def test_replace_session_keeps_an_unmapped_lid_principal(tmp_path, monkeypatch):
    """NON-MERGE GUARD — an unmapped LID keys to itself, so it is its own
    principal and a phone-keyed write must not evict it."""
    _arm_lid_cache(tmp_path, monkeypatch)
    from agents.flyer.onboarding import _replace_session
    store = _onboarding_store(_onboarding_session(UNMAPPED_LID, None))

    _replace_session(store, _onboarding_session(PHONE_JID, PHONE))

    assert sorted(s.chat_id for s in store.onboarding_sessions) == sorted(
        [UNMAPPED_LID, PHONE_JID])


def test_discard_session_removes_the_identity_twin(tmp_path, monkeypatch):
    """Discard has to converge for the same reason replace does — otherwise the
    twin it failed to remove is left behind to intercept the next message."""
    _arm_lid_cache(tmp_path, monkeypatch)
    from agents.flyer.onboarding import _discard_session
    store = _onboarding_store(_onboarding_session(LID, None))

    _discard_session(store, _onboarding_session(PHONE_JID, PHONE))

    assert store.onboarding_sessions == []


def test_discard_session_keeps_a_genuinely_different_principal(tmp_path, monkeypatch):
    """NON-MERGE GUARD — a discard must never become a store-wide wipe."""
    _arm_lid_cache(tmp_path, monkeypatch)
    from agents.flyer.onboarding import _discard_session
    store = _onboarding_store(_onboarding_session(OTHER_PHONE_JID, OTHER_PHONE))

    _discard_session(store, _onboarding_session(PHONE_JID, PHONE))

    assert [s.chat_id for s in store.onboarding_sessions] == [OTHER_PHONE_JID]


# read side — the cf-router plugin's own onboarding finder
#
# Same defect, second reader. `find_flyer_onboarding_session_by_sender` sits
# directly beside `find_flyer_intake_session_by_sender`, which HAS the canonical
# clause; converging only the schemas store would leave the plugin reading the
# unconverged answer.

def _customers_with_onboarding(session_chat_id: str, session_phone) -> dict:
    return {
        "schema_version": 1,
        "next_customer_sequence": 2,
        "next_brand_asset_sequence": 1,
        "customers": [],
        "intake_sessions": [],
        "onboarding_sessions": [
            {
                "chat_id": session_chat_id,
                "sender_phone": session_phone,
                "status": ONBOARDING_STATUS,
                "started_at": _T0.isoformat(),
                "updated_at": _T0.isoformat(),
            }
        ],
    }


def test_cf_router_onboarding_finder_converges_via_mapped_lid(tmp_path, monkeypatch):
    _arm_lid_cache(tmp_path, monkeypatch)
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_onboarding(PHONE_JID, PHONE)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    found = actions.find_flyer_onboarding_session_by_sender(None, LID)
    assert found is not None and found["chat_id"] == PHONE_JID


def test_cf_router_onboarding_finder_converges_lid_session_via_phone(tmp_path, monkeypatch):
    _arm_lid_cache(tmp_path, monkeypatch)
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_onboarding(LID, None)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    found = actions.find_flyer_onboarding_session_by_sender(PHONE, PHONE_JID)
    assert found is not None and found["chat_id"] == LID


def test_cf_router_onboarding_finder_unmapped_lid_does_not_hijack(tmp_path, monkeypatch):
    """NON-MERGE GUARD, plugin side."""
    _arm_lid_cache(tmp_path, monkeypatch)
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    cust.write_text(json.dumps(_customers_with_onboarding(PHONE_JID, PHONE)), encoding="utf-8")
    actions.FLYER_CUSTOMERS_PATH = cust

    assert actions.find_flyer_onboarding_session_by_sender(None, UNMAPPED_LID) is None


# ── (5) find_recent_flyer_manual_edit_project phone canonicalization ─────────
#
# The 60s idempotent-retry guard for the SOURCE/NEW intercept. It compared
# `project["customer_phone"]` to the caller's phone as RAW STRINGS, while the
# sibling `_flyer_candidate_projects_by_sender` canonicalizes both sides through
# `_canonical_phone`. A project row persisted as bare digits therefore never
# matched a caller holding the +E.164 form, the retry guard silently missed, and
# a second manual_edit_required project was created for the same request.

def _projects_doc(*rows) -> dict:
    return {"projects": list(rows)}


def _manual_edit_row(project_id: str, customer_phone, *, age_sec: float = 5.0) -> dict:
    created = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
    return {
        "project_id": project_id,
        "customer_phone": customer_phone,
        "status": "manual_edit_required",
        "created_at": created.isoformat(),
    }


def _actions_with_projects(tmp_path, doc: dict):
    actions = _load_actions()
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    actions.FLYER_PROJECTS_PATH = path
    return actions


def test_manual_edit_finder_matches_a_raw_digits_row_from_a_canonical_caller(tmp_path):
    """THE live false-negative. The row is stored as bare digits; the caller
    holds the +E.164 form that identify-sender returns."""
    actions = _actions_with_projects(
        tmp_path, _projects_doc(_manual_edit_row("F0300", "15550100001")))

    found = actions.find_recent_flyer_manual_edit_project(PHONE)
    assert found is not None and found["project_id"] == "F0300"


def test_manual_edit_finder_matches_a_canonical_row_from_a_raw_digits_caller(tmp_path):
    """The mirror: canonicalizing only one side would pass the cell above and
    still miss here."""
    actions = _actions_with_projects(
        tmp_path, _projects_doc(_manual_edit_row("F0300", PHONE)))

    found = actions.find_recent_flyer_manual_edit_project("15550100001")
    assert found is not None and found["project_id"] == "F0300"


def test_manual_edit_finder_still_matches_an_exact_string_pair(tmp_path):
    """REGRESSION GUARD — the pre-existing exact-match behaviour is preserved."""
    actions = _actions_with_projects(
        tmp_path, _projects_doc(_manual_edit_row("F0300", PHONE)))

    assert actions.find_recent_flyer_manual_edit_project(PHONE)["project_id"] == "F0300"


@pytest.mark.parametrize("falsy", ["", None])
def test_manual_edit_finder_refuses_a_falsy_phone(tmp_path, falsy):
    """LATENT TRAP — with no phone to match on, a row carrying an empty or
    missing `customer_phone` compared equal and was handed back as this
    caller's project. There is no identity here, so there is no match."""
    actions = _actions_with_projects(tmp_path, _projects_doc(
        _manual_edit_row("F0301", ""),
        {"project_id": "F0302", "status": "manual_edit_required",
         "created_at": datetime.now(timezone.utc).isoformat()},
    ))

    assert actions.find_recent_flyer_manual_edit_project(falsy) is None


def test_manual_edit_finder_never_matches_a_different_customer(tmp_path):
    """NON-MERGE GUARD — canonicalization must not widen the match."""
    actions = _actions_with_projects(
        tmp_path, _projects_doc(_manual_edit_row("F0300", "15550100002")))

    assert actions.find_recent_flyer_manual_edit_project(PHONE) is None


def test_manual_edit_finder_still_honours_the_window_and_status(tmp_path):
    """REGRESSION GUARD — the two other filters are untouched by the phone fix."""
    actions = _actions_with_projects(tmp_path, _projects_doc(
        _manual_edit_row("F0300", "15550100001", age_sec=600.0),
        {"project_id": "F0303", "customer_phone": "15550100001",
         "status": "delivered",
         "created_at": datetime.now(timezone.utc).isoformat()},
    ))

    assert actions.find_recent_flyer_manual_edit_project(PHONE) is None
