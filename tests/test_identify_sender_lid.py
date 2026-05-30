"""Tests for identify-sender's LID-input handling. Uses temp roster + config
fixtures via SHIFT_AGENT_ROSTER_PATH / SHIFT_AGENT_CONFIG_PATH env vars so
this test runs in CI without /opt/shift-agent installed.
"""
from __future__ import annotations
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

# safe_io imports fcntl which is Linux-only. Skip these subprocess tests on
# Windows; they run in CI (Linux) and on the VPS where the script is deployed.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="identify-sender depends on safe_io which uses fcntl (Linux only)",
)


SCRIPT = Path(__file__).resolve().parent.parent / "src" / "platform" / "scripts" / "identify-sender"


@pytest.fixture
def fixture_dir(tmp_path):
    """Build a minimal valid roster + config in tmp_path."""
    roster = {
        "location": {"id": "loc_test", "name": "Test", "timezone": "America/New_York"},
        "employees": [
            {
                "id": "e004",
                "name": "Anjali Iyer",
                "role": "cashier",
                "phone": "+17329837841",
                "languages": ["en"],
                "can_cover_roles": ["cashier"],
                "status": "active",
                "lid": "201975216009469@lid",
            },
            {
                "id": "e006",
                "name": "Lakshmi Rao",
                "role": "sweets",
                "phone": "+19802005022",
                "languages": ["en"],
                "can_cover_roles": ["cashier", "sweets"],
                "status": "active",
                # e006 has no LID set yet
            },
        ],
        "schedule": {},
    }
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test Customer",
            "location_id": "loc_test",
            "timezone": "America/New_York",
            "languages": ["en"],
        },
        "owner": {
            "name": "Owner",
            "phone": "+918522041562",
            "self_chat_jid": "918522041562@s.whatsapp.net",
        },
        "limits": {
            "max_outbound_per_day": 6,
            "max_outbound_per_minute": 30,
            "pending_proposal_ttl_hours": 4,
            "per_message_timeout_sec": 120,
            "send_failure_retry_count": 1,
        },
        "alerting": {
            "pushover_user_key": "fake_user_key",
            "pushover_app_token": "fake_token",
        },
        "backup": {
            "gpg_recipient_email": "test@example.com",
            "retention_days": 30,
        },
        "operations": {"business_hours_local": "08:00-22:00"},
    }
    import yaml
    (tmp_path / "roster.json").write_text(json.dumps(roster))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


def _run(arg, fixture_dir):
    env = {
        **os.environ,
        "SHIFT_AGENT_ROSTER_PATH": str(fixture_dir / "roster.json"),
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src" / "platform"),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), arg],
        capture_output=True, text=True, env=env,
    )


def test_phone_resolves_employee_with_lid(fixture_dir):
    r = _run("+17329837841", fixture_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "employee"
    assert out["employee_id"] == "e004"
    assert out["name"] == "Anjali Iyer"
    assert out["lid"] == "201975216009469@lid"


def test_lid_input_resolves_employee(fixture_dir):
    r = _run("201975216009469@lid", fixture_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "employee"
    assert out["employee_id"] == "e004"
    assert out["lid"] == "201975216009469@lid"


def test_lid_input_unknown(fixture_dir):
    r = _run("999999999999@lid", fixture_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "unknown"
    assert out["lid"] == "999999999999@lid"
    assert out["phone_normalized"] is None


def test_phone_jid_suffix_stripped(fixture_dir):
    r = _run("17329837841@s.whatsapp.net", fixture_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "employee"
    assert out["phone_normalized"] == "+17329837841"


def test_owner_phone(fixture_dir):
    r = _run("+918522041562", fixture_dir)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "owner"
    assert out["phone_normalized"] == "+918522041562"


def test_garbage_input_exit_2(fixture_dir):
    r = _run("garbage_not_a_phone", fixture_dir)
    assert r.returncode == 2, r.stdout


def test_employee_without_lid_returns_lid_none(fixture_dir):
    r = _run("+19802005022", fixture_dir)  # Lakshmi has no lid set
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "employee"
    assert out["employee_id"] == "e006"
    assert out["lid"] is None


# ── roster-load failure must FAIL SAFE (role="error", non-zero exit, never a
# fabricated real role). The dispatcher treats role="error" as fail-closed and
# can surface the load failure to the owner; identify-sender must never invent
# an identity from an unreadable/invalid roster. +17329837841 (e004) WOULD
# resolve to an employee with a valid roster — so role="error" here proves the
# failure path wins over identity resolution.

def test_corrupt_roster_fails_safe(fixture_dir):
    (fixture_dir / "roster.json").write_text("{ this is not valid json ")
    r = _run("+17329837841", fixture_dir)
    assert r.returncode != 0, r.stdout
    out = json.loads(r.stdout)
    assert out["role"] == "error", out
    assert out["role"] not in ("employee", "owner", "unknown")


def test_schema_invalid_roster_fails_safe(fixture_dir):
    # valid JSON, invalid Roster schema (employee missing required fields)
    (fixture_dir / "roster.json").write_text(
        json.dumps({"location": {}, "employees": [{"id": "x"}], "schedule": {}})
    )
    r = _run("+17329837841", fixture_dir)
    assert r.returncode != 0, r.stdout
    out = json.loads(r.stdout)
    assert out["role"] == "error", out


def test_missing_roster_fails_safe(fixture_dir):
    (fixture_dir / "roster.json").unlink()
    r = _run("+17329837841", fixture_dir)
    assert r.returncode != 0, r.stdout
    out = json.loads(r.stdout)
    assert out["role"] == "error", out
    # even the owner phone must not resolve when the roster cannot be loaded
    r2 = _run("+918522041562", fixture_dir)  # owner phone in the fixture config
    assert r2.returncode != 0, r2.stdout
    assert json.loads(r2.stdout)["role"] == "error"
