"""Agent #33 v0.1 — birthday reminder + record-customer-birthday CLI tests.

Read-side unit tests use importlib SourceFileLoader (mirrors the post-#32
fix in tests/test_lookup_prior_leads.py). Write-script tests subprocess-
invoke the CLI (mirrors test_owner_wellbeing_quiet_hours.py from #41).
Schema-validator tests pure-pytest (no subprocess, no importlib).

Plan: tasks/agent-33-birthday-v0-1-with-cli-write-plan.md
Design: tasks/agent-33-birthday-v0-1-with-cli-write-design.md

R1-B1 fix verified by tests below: when "birthdays" is NOT in
cfg.daily_brief.sections, the rendered brief contains NO birthday line.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from zoneinfo import ZoneInfo

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
SEND_BRIEF = REPO / "src" / "agents" / "daily_brief" / "scripts" / "send-daily-brief"
RECORD_BIRTHDAY = REPO / "src" / "agents" / "daily_brief" / "scripts" / "record-customer-birthday"

EXIT_OK = 0
EXIT_INVALID_INPUT = 2


# ─────────────────────────────────────────────────────────────────
# Fixtures + helpers
# ─────────────────────────────────────────────────────────────────


def _load_send_brief(env_dir: Path):
    """Load send-daily-brief as a module via importlib SourceFileLoader.
    Pre-loads schemas/safe_io/exit_codes/log_source from the test PLATFORM_DIR
    into sys.modules to bypass deployed-vs-test schema race."""
    import importlib.machinery
    import importlib.util

    sys.path.insert(0, str(PLATFORM_DIR))

    # Pre-load platform modules into sys.modules so the script's imports hit
    # the test versions (lessons from #41 + #32).
    for _modname in ("schemas", "safe_io", "exit_codes", "log_source"):
        _path = PLATFORM_DIR / f"{_modname}.py"
        if not _path.exists():
            continue
        _loader = importlib.machinery.SourceFileLoader(_modname, str(_path))
        _spec = importlib.util.spec_from_file_location(
            _modname, str(_path), loader=_loader,
        )
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_modname] = _mod
        _spec.loader.exec_module(_mod)

    loader = importlib.machinery.SourceFileLoader("send_brief", str(SEND_BRIEF))
    spec = importlib.util.spec_from_file_location(
        "send_brief", str(SEND_BRIEF), loader=loader,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BIRTHDAYS_PATH = env_dir / "state" / "customer-birthdays.json"
    return mod


def _seed_birthdays(env_dir: Path, customers: list[dict]) -> Path:
    """Write a CustomerBirthdayStore JSON file at env_dir/state/customer-birthdays.json."""
    state = env_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "customer-birthdays.json"
    path.write_text(
        json.dumps({"customers": customers, "schema_version": 1}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def env_dir(tmp_path):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test", "location_id": "loc_t",
            "timezone": "America/New_York",
        },
        "owner": {
            "name": "Owner", "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


# ─────────────────────────────────────────────────────────────────
# 1-6. Read-side helper unit tests
# ─────────────────────────────────────────────────────────────────


def test_aggregate_birthdays_match_today(env_dir):
    """Single customer with today's MM-DD → returns 1-item list."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    today_md = today.strftime("%m-%d")
    _seed_birthdays(env_dir, [
        {"customer_phone": "+15555550100",
         "display_name": "Suresh Patel", "birthday": today_md},
    ])
    mod = _load_send_brief(env_dir)
    result = mod._aggregate_birthdays(today)
    assert result == [{"phone": "+15555550100", "name": "Suresh Patel"}]


def test_aggregate_birthdays_no_match(env_dir):
    """Customers exist but none today → []."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    _seed_birthdays(env_dir, [
        {"customer_phone": "+15555550100",
         "display_name": "Suresh Patel", "birthday": "01-01"},
    ])
    mod = _load_send_brief(env_dir)
    result = mod._aggregate_birthdays(today)
    assert result == []


def test_aggregate_birthdays_multiple_today(env_dir):
    """Two customers same MM-DD → 2-item list."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    today_md = today.strftime("%m-%d")
    _seed_birthdays(env_dir, [
        {"customer_phone": "+15555550100",
         "display_name": "Suresh Patel", "birthday": today_md},
        {"customer_phone": "+15555550101",
         "display_name": "Priya Reddy", "birthday": today_md},
    ])
    mod = _load_send_brief(env_dir)
    result = mod._aggregate_birthdays(today)
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert names == {"Suresh Patel", "Priya Reddy"}


def test_aggregate_birthdays_missing_file(env_dir):
    """Falls open to [] when state file is absent."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    # No _seed_birthdays — file does not exist
    mod = _load_send_brief(env_dir)
    result = mod._aggregate_birthdays(today)
    assert result == []


def test_render_birthdays_empty():
    """Empty list → 'None today.'."""
    # Pure helper — no env needed, but we still need the module loaded.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        mod = _load_send_brief(Path(tmp))
        assert mod._render_birthdays([]) == "None today."


def test_render_birthdays_formatted():
    """Multi-customer list → comma-separated 'Name (Phone)'."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        mod = _load_send_brief(Path(tmp))
        result = mod._render_birthdays([
            {"phone": "+15555550100", "name": "Suresh Patel"},
            {"phone": "+15555550101", "name": "Priya Reddy"},
        ])
        assert result == "Suresh Patel (+15555550100), Priya Reddy (+15555550101)"


# ─────────────────────────────────────────────────────────────────
# 7-10. Write-script subprocess tests
# ─────────────────────────────────────────────────────────────────


def _run_record_birthday(
    env_dir: Path, *, phone: str, name: str, birthday: str,
) -> subprocess.CompletedProcess:
    """Subprocess-invoke record-customer-birthday with module-path overrides."""
    wrapper = f"""
import sys, pathlib
import importlib.machinery, importlib.util

sys.argv = [
    "record-customer-birthday",
    "--phone", {phone!r},
    "--name", {name!r},
    "--birthday", {birthday!r},
]
sys.path.insert(0, {str(PLATFORM_DIR)!r})

# Pre-load platform modules so the script's imports hit the test versions.
for _modname in ("schemas", "safe_io", "exit_codes"):
    _path = pathlib.Path({str(PLATFORM_DIR)!r}) / f"{{_modname}}.py"
    _loader = importlib.machinery.SourceFileLoader(_modname, str(_path))
    _spec = importlib.util.spec_from_file_location(_modname, str(_path), loader=_loader)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_modname] = _mod
    _spec.loader.exec_module(_mod)

loader = importlib.machinery.SourceFileLoader("rcb", {str(RECORD_BIRTHDAY)!r})
spec = importlib.util.spec_from_file_location("rcb", {str(RECORD_BIRTHDAY)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.BIRTHDAYS_PATH = pathlib.Path({str(env_dir / 'state' / 'customer-birthdays.json')!r})
mod.BIRTHDAYS_LOCK = pathlib.Path({str(env_dir / 'state' / 'customer-birthdays.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})

sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15,
    )


def _read_audit(env_dir: Path) -> list[dict]:
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def _read_store(env_dir: Path) -> dict:
    p = env_dir / "state" / "customer-birthdays.json"
    if not p.exists():
        return {"customers": [], "schema_version": 1}
    return json.loads(p.read_text())


def test_record_birthday_creates_store_when_missing(env_dir):
    """First-time record creates store + audit operation='created'."""
    r = _run_record_birthday(
        env_dir, phone="+15555550100", name="Suresh Patel", birthday="03-15",
    )
    assert r.returncode == EXIT_OK, f"stderr={r.stderr}"
    store = _read_store(env_dir)
    assert len(store["customers"]) == 1
    assert store["customers"][0]["customer_phone"] == "+15555550100"
    assert store["customers"][0]["birthday"] == "03-15"

    audit = _read_audit(env_dir)
    recorded = [e for e in audit if e["type"] == "customer_birthday_recorded"]
    assert len(recorded) == 1
    assert recorded[0]["operation"] == "created"
    assert recorded[0]["customer_phone"] == "+15555550100"


def test_record_birthday_updates_existing_phone(env_dir):
    """Re-recording same phone replaces (not duplicates) and audit operation='updated'."""
    r1 = _run_record_birthday(
        env_dir, phone="+15555550100", name="Suresh Patel", birthday="03-15",
    )
    assert r1.returncode == EXIT_OK
    r2 = _run_record_birthday(
        env_dir, phone="+15555550100", name="Suresh K. Patel", birthday="03-16",
    )
    assert r2.returncode == EXIT_OK
    store = _read_store(env_dir)
    assert len(store["customers"]) == 1, "duplicate phone should upsert, not append"
    assert store["customers"][0]["display_name"] == "Suresh K. Patel"
    assert store["customers"][0]["birthday"] == "03-16"

    audit = _read_audit(env_dir)
    recorded = [e for e in audit if e["type"] == "customer_birthday_recorded"]
    assert len(recorded) == 2
    assert recorded[0]["operation"] == "created"
    assert recorded[1]["operation"] == "updated"


def test_record_birthday_rejects_invalid_date(env_dir):
    """02-30 is illegal calendar date; reject with exit 2, no state mutation, no audit."""
    r = _run_record_birthday(
        env_dir, phone="+15555550100", name="Suresh Patel", birthday="02-30",
    )
    assert r.returncode == EXIT_INVALID_INPUT, f"stderr={r.stderr}"
    # No state file should be created
    assert not (env_dir / "state" / "customer-birthdays.json").exists()
    # No audit should be emitted
    assert _read_audit(env_dir) == []


def test_record_birthday_rejects_invalid_phone(env_dir):
    """Non-E.164 phone rejected; exit 2, no state mutation, no audit."""
    r = _run_record_birthday(
        env_dir, phone="not-a-phone", name="Suresh Patel", birthday="03-15",
    )
    assert r.returncode == EXIT_INVALID_INPUT, f"stderr={r.stderr}"
    assert not (env_dir / "state" / "customer-birthdays.json").exists()
    assert _read_audit(env_dir) == []


# ─────────────────────────────────────────────────────────────────
# 11-13. Schema field-validator tests (R1-M3 / R2-M4 fix)
# ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("illegal_md", [
    "02-30", "02-31", "04-31", "06-31", "09-31", "11-31",
    # R3-N1 PR review fix: month-boundary regex coverage. Pins regex
    # against future "simplification" to e.g. r"\d{2}-\d{2}".
    "00-01", "13-01", "01-00", "01-32",
])
def test_customer_birthday_rejects_invalid_calendar_date(illegal_md):
    """_validate_calendar_date + regex reject illegal MM-DDs."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import CustomerBirthday  # noqa: E402
    with pytest.raises(ValidationError):
        CustomerBirthday(
            customer_phone="+15555550100",
            display_name="Test",
            birthday=illegal_md,
        )


def test_customer_birthday_accepts_leap_day():
    """02-29 is a legitimate leap-day birthday; validator must accept (uses
    leap-year pivot 2024 in the implementation)."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import CustomerBirthday  # noqa: E402
    cb = CustomerBirthday(
        customer_phone="+15555550100",
        display_name="Test",
        birthday="02-29",
    )
    assert cb.birthday == "02-29"


# ─────────────────────────────────────────────────────────────────
# 14. R2-M2 regression guard: BriefSection default unchanged
# ─────────────────────────────────────────────────────────────────


def test_birthdays_not_in_default_sections():
    """R2-M2 fix: BriefSection Literal extension is opt-in. The DailyBriefConfig
    default factory MUST NOT include 'birthdays' — otherwise existing customers'
    briefs would silently render the new section without explicit opt-in.

    A future build-time slip that adds 'birthdays' to the default-factory list
    would break this regression guard immediately."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import DailyBriefConfig  # noqa: E402
    cfg = DailyBriefConfig()
    assert "birthdays" not in cfg.sections, (
        f"birthdays must remain opt-in; default sections={cfg.sections}"
    )


# ─────────────────────────────────────────────────────────────────
# 15. R1-M2 fix: _render_brief_text integration test for the new section
# ─────────────────────────────────────────────────────────────────


def test_render_brief_text_includes_birthdays_when_section_enabled(env_dir):
    """End-to-end check: when 'birthdays' is in cfg.daily_brief.sections AND
    today has a birthday match, the rendered brief contains the birthday line.
    Pins R1-B1 fix (conditional rendering) AND verifies wiring of the new field."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    today_md = today.strftime("%m-%d")
    _seed_birthdays(env_dir, [
        {"customer_phone": "+15555550100",
         "display_name": "Suresh Patel", "birthday": today_md},
    ])
    mod = _load_send_brief(env_dir)
    # Override TEMPLATES_DIR to repo template dir
    mod.TEMPLATES_DIR = REPO / "src" / "agents" / "daily_brief" / "templates"
    # Override _customer_now to return our fixed date
    mod._customer_now = lambda tz: today

    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import Config, DailyBriefConfig  # noqa: E402
    cfg_dict = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "daily_brief": {"sections": ["yesterday", "today_outlook", "alerts", "birthdays"]},
    }
    cfg = Config.model_validate(cfg_dict)

    # Minimal valid yesterday/today_data for render
    yesterday_counts = {
        "sick_calls": 0, "proposals_created": 0, "proposals_accepted": 0,
        "proposals_declined": 0, "proposals_no_response": 0,
        "outbound_send_failed": 0, "invariant_violations": 0,
    }
    today_data = {
        "shifts_today": [], "pending_active_count": 0,
        "pending_send_failed_count": 0,
    }

    rendered = mod._render_brief_text(
        cfg, "2026-05-10", yesterday_counts, today_data,
        degraded=False, catchup_minutes_late=0,
    )
    assert "Birthdays today:" in rendered, f"rendered:\n{rendered}"
    assert "Suresh Patel" in rendered
    assert "+15555550100" in rendered


def test_render_brief_text_skips_birthdays_when_section_not_enabled(env_dir):
    """R1-B1 fix: when 'birthdays' is NOT in sections, NO birthday line in rendered
    brief — even if state file has matching customer."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    today_md = today.strftime("%m-%d")
    _seed_birthdays(env_dir, [
        {"customer_phone": "+15555550100",
         "display_name": "Suresh Patel", "birthday": today_md},
    ])
    mod = _load_send_brief(env_dir)
    mod.TEMPLATES_DIR = REPO / "src" / "agents" / "daily_brief" / "templates"
    mod._customer_now = lambda tz: today

    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import Config  # noqa: E402
    cfg_dict = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        # daily_brief NOT specified → DailyBriefConfig() default has no "birthdays"
    }
    cfg = Config.model_validate(cfg_dict)

    yesterday_counts = {
        "sick_calls": 0, "proposals_created": 0, "proposals_accepted": 0,
        "proposals_declined": 0, "proposals_no_response": 0,
        "outbound_send_failed": 0, "invariant_violations": 0,
    }
    today_data = {
        "shifts_today": [], "pending_active_count": 0,
        "pending_send_failed_count": 0,
    }

    rendered = mod._render_brief_text(
        cfg, "2026-05-10", yesterday_counts, today_data,
        degraded=False, catchup_minutes_late=0,
    )
    assert "Birthdays today:" not in rendered, (
        f"birthdays section should NOT appear when not in sections; rendered:\n{rendered}"
    )
    assert "Suresh Patel" not in rendered
