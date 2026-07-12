"""Flyer intake-session TTL (P0-2a): read-time expiry, sweep, audit row.

An intake session that never reaches a terminal state must age out so it can
never intercept a later message (the mechanism behind the 2026-06-02 hijack).
TTL default 4h (operator-approved), env FLYER_INTAKE_SESSION_TTL_HOURS.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLATFORM = REPO / "src" / "platform"
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
SLA_SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-source-edit-sla-watchdog"
for _p in (str(PLATFORM), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import schemas  # noqa: E402

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _session(updated_at: datetime, *, status="choosing_mode", expires_at=None, last_activity_at=None):
    return schemas.FlyerIntakeSession(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        status=status,
        source="start_trial",
        started_at=updated_at,
        updated_at=updated_at,
        last_activity_at=last_activity_at,
        expires_at=expires_at,
    )


# ── is_expired unit ───────────────────────────────────────────────────────────

def test_is_expired_updated_at_fallback():
    fresh = _session(NOW - timedelta(hours=3))
    stale = _session(NOW - timedelta(hours=5))
    assert fresh.is_expired(NOW, 4.0) is False
    assert stale.is_expired(NOW, 4.0) is True


def test_is_expired_prefers_expires_at():
    s = _session(NOW - timedelta(hours=10), expires_at=NOW + timedelta(hours=1))
    # Even though updated_at is ancient, an unexpired expires_at wins.
    assert s.is_expired(NOW, 4.0) is False


def test_is_expired_last_activity_over_updated():
    s = _session(NOW - timedelta(hours=10), last_activity_at=NOW - timedelta(hours=1))
    assert s.is_expired(NOW, 4.0) is False


# ── TTL config ────────────────────────────────────────────────────────────────

def test_ttl_default_is_four_hours(monkeypatch):
    monkeypatch.delenv("FLYER_INTAKE_SESSION_TTL_HOURS", raising=False)
    assert schemas.flyer_intake_ttl_hours() == 4.0


def test_ttl_env_override(monkeypatch):
    monkeypatch.setenv("FLYER_INTAKE_SESSION_TTL_HOURS", "2")
    assert schemas.flyer_intake_ttl_hours() == 2.0


def test_ttl_invalid_or_nonpositive_falls_back(monkeypatch):
    monkeypatch.setenv("FLYER_INTAKE_SESSION_TTL_HOURS", "0")
    assert schemas.flyer_intake_ttl_hours() == 4.0
    monkeypatch.setenv("FLYER_INTAKE_SESSION_TTL_HOURS", "garbage")
    assert schemas.flyer_intake_ttl_hours() == 4.0


# ── replace stamps TTL bookkeeping ────────────────────────────────────────────

def test_replace_intake_session_stamps_ttl(monkeypatch):
    monkeypatch.setenv("FLYER_INTAKE_SESSION_TTL_HOURS", "4")
    store = schemas.FlyerCustomerStore()
    s = _session(NOW)
    s.last_activity_at = None
    s.expires_at = None
    store.replace_intake_session(s)
    saved = store.intake_sessions[0]
    assert saved.last_activity_at == NOW
    assert saved.expires_at == NOW + timedelta(hours=4)


# ── old rows load (additive optional) ─────────────────────────────────────────

def test_old_row_without_ttl_fields_loads():
    row = {
        "chat_id": "17329837841@s.whatsapp.net",
        "sender_phone": "+17329837841",
        "status": "choosing_mode",
        "source": "start_trial",
        "started_at": "2026-06-02T17:50:00Z",
        "updated_at": "2026-06-02T17:50:00Z",
    }
    s = schemas.FlyerIntakeSession.model_validate(row)
    assert s.last_activity_at is None and s.expires_at is None


# ── cf-router read-time expiry (treat-as-absent) ──────────────────────────────

def _load_actions():
    name = "cf_router_actions_ttl_test"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(name, str(PLUGIN_DIR / "actions.py"))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _write_customers(path: Path, updated_at: str):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "customers": [],
                "intake_sessions": [
                    {
                        "chat_id": "17329837841@s.whatsapp.net",
                        "sender_phone": "+17329837841",
                        "status": "choosing_mode",
                        "source": "start_trial",
                        "started_at": updated_at,
                        "updated_at": updated_at,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_finder_treats_expired_session_as_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_INTAKE_SESSION_TTL_HOURS", "4")
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    actions.FLYER_CUSTOMERS_PATH = cust
    # Idle 5h -> expired.
    _write_customers(cust, (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat())
    assert actions.find_flyer_intake_session_by_sender("+17329837841", "17329837841@s.whatsapp.net") is None


def test_finder_returns_fresh_session(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_INTAKE_SESSION_TTL_HOURS", "4")
    actions = _load_actions()
    cust = tmp_path / "customers.json"
    actions.FLYER_CUSTOMERS_PATH = cust
    _write_customers(cust, (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    found = actions.find_flyer_intake_session_by_sender("+17329837841", "17329837841@s.whatsapp.net")
    assert found is not None


# ── sweep discards + audits (works on Windows via the watchdog's FileLock stub) ─

def _load_sla():
    return importlib.machinery.SourceFileLoader(
        "flyer_sla_watchdog_ttl_test", str(SLA_SCRIPT)
    ).load_module()


def _customers_doc(sessions: list[dict], *, extra_key=True) -> dict:
    doc = {"schema_version": 1, "customers": [], "intake_sessions": sessions}
    if extra_key:
        # An unmodeled key must survive the raw-dict sweep rewrite.
        doc["operator_note"] = "preserve me"
    return doc


def test_sweep_discards_expired_keeps_fresh_and_audits(tmp_path):
    sla = _load_sla()
    cust = tmp_path / "customers.json"
    log = tmp_path / "decisions.log"
    stale = {
        "chat_id": "17329837841@s.whatsapp.net", "sender_phone": "+17329837841",
        "status": "choosing_mode", "source": "start_trial",
        "started_at": (NOW - timedelta(hours=6)).isoformat(),
        "updated_at": (NOW - timedelta(hours=6)).isoformat(),
    }
    fresh = {
        "chat_id": "19998887777@s.whatsapp.net", "sender_phone": "+19998887777",
        "status": "guided_collecting_goal", "source": "new_flyer",
        "started_at": (NOW - timedelta(hours=1)).isoformat(),
        "updated_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    cust.write_text(json.dumps(_customers_doc([stale, fresh])), encoding="utf-8")

    result = sla.sweep_expired_intake_sessions(
        customers_path=cust, decisions_log_path=log, now=NOW, ttl_hours=4.0
    )
    assert result["expired_count"] == 1
    assert result["statuses"] == ["choosing_mode"]

    doc = json.loads(cust.read_text(encoding="utf-8"))
    remaining = [s["chat_id"] for s in doc["intake_sessions"]]
    assert remaining == ["19998887777@s.whatsapp.net"]
    assert doc["operator_note"] == "preserve me"  # unmodeled key preserved

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    audit = rows[0]
    assert audit["type"] == "flyer_intake_session_expired"
    assert audit["expired_count"] == 1
    assert audit["statuses"] == ["choosing_mode"]
    assert audit["ttl_hours"] == 4.0
    # Validates against the LogEntry union (registered variant).
    from pydantic import TypeAdapter
    TypeAdapter(schemas.LogEntry).validate_python(audit)


def test_sweep_no_expired_is_noop(tmp_path):
    sla = _load_sla()
    cust = tmp_path / "customers.json"
    log = tmp_path / "decisions.log"
    fresh = {
        "chat_id": "17329837841@s.whatsapp.net", "sender_phone": "+17329837841",
        "status": "choosing_mode", "source": "start_trial",
        "started_at": (NOW - timedelta(hours=1)).isoformat(),
        "updated_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    cust.write_text(json.dumps(_customers_doc([fresh])), encoding="utf-8")
    result = sla.sweep_expired_intake_sessions(
        customers_path=cust, decisions_log_path=log, now=NOW, ttl_hours=4.0
    )
    assert result["expired_count"] == 0
    assert not log.exists()  # no audit row on a no-op tick
