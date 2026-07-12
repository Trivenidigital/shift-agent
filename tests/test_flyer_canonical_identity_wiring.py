"""Canonical-identity wiring — LID<->phone convergence across the Flyer surface.

Reconstructs the 2026-06-02 identity split (F0133 keyed to the LID
`201975216009469@lid`; a stale intake session keyed to the phone-JID for the
SAME customer +17329837841) and asserts the three wiring targets converge once
the lid-cache knows the pairing:
  (1) FlyerIntakeSession keying/lookup (cf-router finder/discard + schemas store)
  (2) the B1 shadow-LLM allowlist gate
  (3) an unmapped LID still falls back to its own raw key (no false convergence)
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_WINDOWS = platform.system() == "Windows"

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM = REPO / "src" / "platform"
for _p in (str(PLATFORM), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PHONE = "+17329837841"
PHONE_JID = "17329837841@s.whatsapp.net"
LID = "201975216009469@lid"
UNMAPPED_LID = "555000111222333@lid"


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
