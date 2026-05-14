from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "pilot-readiness-check"
DEPLOY_SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SMOKE_SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"
CATERING_PATTERN_SERVICE = (
    REPO / "src" / "agents" / "catering" / "systemd" / "catering-pattern-report.service"
)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _write_yaml(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(obj, sort_keys=False), encoding="utf-8")


def _base_config() -> dict:
    return {
        "schema_version": 1,
        "customer": {
            "name": "Triveni Pineville",
            "location_id": "loc_pineville_01",
            "timezone": "America/New_York",
            "languages": ["en", "te", "hi"],
        },
        "owner": {
            "name": "Owner",
            "phone": "+17045550100",
            "self_chat_jid": "17045550100@s.whatsapp.net",
            "lid": "211390371475536@lid",
        },
        "limits": {},
        "alerting": {
            "pushover_user_key": "test-user",
            "pushover_app_token": "test-token",
        },
        "backup": {"gpg_recipient_email": "ops@example.com"},
        "catering": {"enabled": True},
        "daily_brief": {
            "enabled": True,
            "brief_time": "07:00",
            "sections": ["yesterday", "today_outlook", "alerts"],
        },
    }


def _base_roster() -> dict:
    return {
        "location": {
            "id": "loc_pineville_01",
            "name": "Triveni Pineville",
            "timezone": "America/New_York",
        },
        "employees": [
            {
                "id": "e001",
                "name": "Ravi Kumar",
                "role": "cashier",
                "phone": "+17045550101",
                "languages": ["en"],
                "can_cover_roles": ["cashier"],
                "status": "active",
            },
            {
                "id": "e002",
                "name": "Anjali Iyer",
                "role": "cashier",
                "phone": "+17045550102",
                "languages": ["en"],
                "can_cover_roles": ["cashier"],
                "status": "active",
            },
        ],
        "schedule": {
            "2026-05-15": [
                {"employee_id": "e001", "shift": "09:00-17:00", "role": "cashier"},
                {"employee_id": "e002", "shift": "12:00-20:00", "role": "cashier"},
            ]
        },
    }


def _base_menu() -> dict:
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "version": 1,
        "updated_at": now,
        "updated_by": "manual",
        "items": [
            {"name": "Chicken Biryani", "category": "main", "dietary_tags": []},
            {"name": "Paneer Tikka", "category": "appetizer", "dietary_tags": ["veg"]},
            {"name": "Gulab Jamun", "category": "dessert", "dietary_tags": ["veg"]},
        ],
    }


def _arrange(tmp_path: Path, *, config: dict | None = None, roster: dict | None = None,
             menu: dict | None = None) -> tuple[Path, Path, Path]:
    config_path = tmp_path / "config.yaml"
    roster_path = tmp_path / "roster.json"
    state_dir = tmp_path / "state"
    if config is not None:
        _write_yaml(config_path, config)
    if roster is not None:
        _write_json(roster_path, roster)
    if menu is not None:
        _write_json(state_dir / "catering-menu.json", menu)
    return config_path, roster_path, state_dir


def _run(config_path: Path, roster_path: Path, state_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config_path),
            "--roster",
            str(roster_path),
            "--state-dir",
            str(state_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _run_text(config_path: Path, roster_path: Path, state_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config_path),
            "--roster",
            str(roster_path),
            "--state-dir",
            str(state_dir),
            "--text",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _report(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_ready_fixture_passes_for_three_agent_pilot(tmp_path: Path):
    config_path, roster_path, state_dir = _arrange(
        tmp_path,
        config=_base_config(),
        roster=_base_roster(),
        menu=_base_menu(),
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 0
    report = _report(result)
    assert report["status"] == "ready"
    assert report["summary"]["failed"] == 0


def test_placeholder_customer_blocks_production(tmp_path: Path):
    cfg = _base_config()
    cfg["customer"]["name"] = "PLACEHOLDER - customer name"
    cfg["customer"]["location_id"] = "PLACEHOLDER_loc_id"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=cfg, roster=_base_roster(), menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "customer.name is placeholder" in messages
    assert "customer.location_id is placeholder" in messages


def test_roster_location_id_must_match_config_customer_location_id(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = "loc_jacksonville_test"
    roster["location"]["name"] = "Triveni Pineville"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert any(msg.startswith("roster.location.id does not match customer.location_id") for msg in messages)


def test_roster_location_name_must_not_contain_test_label(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["name"] = "Triveni Jacksonville (TEST)"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location contains test/placeholder label" in messages


def test_roster_location_id_must_not_contain_test_label_even_when_it_matches_config(tmp_path: Path):
    cfg = _base_config()
    cfg["customer"]["location_id"] = "loc_jacksonville_test"
    roster = _base_roster()
    roster["location"]["id"] = "loc_jacksonville_test"
    roster["location"]["name"] = "Triveni Pineville"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=cfg, roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location contains test/placeholder label" in messages


def test_roster_location_name_must_match_meaningful_location_id_token(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = "loc_pineville_01"
    roster["location"]["name"] = "Triveni Jacksonville"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.location.name does not match customer.location_id token" in messages


def test_roster_location_null_and_non_string_metadata_blocks_without_traceback(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = 123
    roster["location"]["name"] = None
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    assert result.stderr == ""
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert any(msg.startswith("roster.location.id does not match customer.location_id") for msg in messages)
    assert "roster.location contains test/placeholder label" in messages


def test_roster_location_match_not_reported_pass_when_config_invalid(tmp_path: Path):
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config={"schema_version": 1}, roster=_base_roster(), menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    checks = _report(result)["checks"]
    assert any(c["id"] == "config.schema" and c["status"] == "fail" for c in checks)
    assert any(
        c["id"] == "roster.location_id_match"
        and c["status"] == "fail"
        and c["message"] == "roster.location.id not compared because config invalid"
        for c in checks
    )


def test_text_output_includes_roster_location_failures(tmp_path: Path):
    roster = _base_roster()
    roster["location"]["id"] = "loc_jacksonville_test"
    roster["location"]["name"] = "Triveni Jacksonville (TEST)"
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=roster, menu=_base_menu()
    )

    result = _run_text(config_path, roster_path, state_dir)

    assert result.returncode == 1
    assert result.stderr == ""
    assert "FAIL roster.location_id_match: roster.location.id does not match customer.location_id" in result.stdout
    assert "FAIL roster.location_label: roster.location contains test/placeholder label" in result.stdout


def test_missing_roster_blocks_shift_agent(tmp_path: Path):
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=None, menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "roster.json missing" in messages


def test_missing_menu_blocks_catering_agent(tmp_path: Path):
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=_base_config(), roster=_base_roster(), menu=None
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "catering-menu.json missing" in messages


def test_disabled_daily_brief_blocks_control_tower(tmp_path: Path):
    cfg = _base_config()
    cfg["daily_brief"]["enabled"] = False
    config_path, roster_path, state_dir = _arrange(
        tmp_path, config=cfg, roster=_base_roster(), menu=_base_menu()
    )

    result = _run(config_path, roster_path, state_dir)

    assert result.returncode == 1
    messages = [c["message"] for c in _report(result)["checks"] if c["status"] == "fail"]
    assert "daily_brief.enabled is false" in messages


def test_deploy_removes_stale_pilot_readiness_binary_on_rollback():
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "rm -f /usr/local/bin/pilot-readiness-check" in text


def test_deploy_installs_and_enables_catering_pattern_timer():
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "src/agents/catering/systemd/*.service" in text
    assert "src/agents/catering/systemd/*.timer" in text
    assert "systemctl enable --now catering-pattern-report.timer" in text


def test_smoke_requires_catering_pattern_timer_enabled_and_valid():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "catering-pattern-report.timer" in text
    assert "/etc/systemd/system/catering-pattern-report.service" in text
    assert "/etc/systemd/system/catering-pattern-report.timer" in text


def test_catering_pattern_service_uses_hermes_python_and_runs_without_lead_condition():
    text = CATERING_PATTERN_SERVICE.read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/catering-pattern-report" in text
    assert "ConditionPathExists=/opt/shift-agent/state/catering-leads.json" not in text


def test_smoke_reports_pilot_readiness_non_blocking():
    text = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "/usr/local/bin/pilot-readiness-check --text" in text
    snippet = text[text.index("/usr/local/bin/pilot-readiness-check --text"):]
    assert "|| true" in snippet[:200]
