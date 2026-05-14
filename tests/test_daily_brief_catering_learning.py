"""Tests for the opt-in catering learning section in send-daily-brief."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="send-daily-brief imports safe_io which depends on fcntl",
)

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
SEND_BRIEF = REPO / "src" / "agents" / "daily_brief" / "scripts" / "send-daily-brief"


def _load_send_brief(env_dir: Path):
    sys.path.insert(0, str(PLATFORM_DIR))
    for modname in ("schemas", "safe_io", "exit_codes", "log_source"):
        path = PLATFORM_DIR / f"{modname}.py"
        loader = importlib.machinery.SourceFileLoader(modname, str(path))
        spec = importlib.util.spec_from_file_location(modname, str(path), loader=loader)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)

    loader = importlib.machinery.SourceFileLoader("send_brief_catering", str(SEND_BRIEF))
    spec = importlib.util.spec_from_file_location(
        "send_brief_catering", str(SEND_BRIEF), loader=loader,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TEMPLATES_DIR = REPO / "src" / "agents" / "daily_brief" / "templates"
    mod.CATERING_LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.CATERING_LEARNING_SUMMARY_PATH = env_dir / "state" / "catering-learning-summary.json"
    return mod


def _seed_leads(env_dir: Path, now: datetime) -> None:
    path = env_dir / "state" / "catering-leads.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "leads": [{
            "lead_id": "L9001",
            "status": "AWAITING_OWNER_APPROVAL",
            "created_at": now.isoformat(),
            "owner_approval_code": "#A3F2X",
        }]
    }), encoding="utf-8")


def _seed_summary(env_dir: Path, generated_at: datetime, **overrides) -> None:
    path = env_dir / "state" / "catering-learning-summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "source": "catering-pattern-report",
        "generated_at": generated_at.isoformat(),
        "window_days": 30,
        "proposal_health": {
            "sent": 3,
            "selected": 1,
            "send_failed": 0,
            "select_failed": 1,
        },
        "off_menu_request_count": 4,
        "leads_with_off_menu_count": 2,
        "active_missing_info_count": 1,
        "menu_updated_at": generated_at.isoformat(),
        "menu_freshness_days": 5,
        "degraded_sources": [],
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")


def _cfg(*, sections: list[str]):
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import Config  # noqa: E402

    return Config.model_validate({
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
        "catering": {"enabled": True},
        "daily_brief": {"sections": sections},
    })


def _render(mod, cfg):
    yesterday_counts = {
        "sick_calls": 0, "proposals_created": 0, "proposals_accepted": 0,
        "proposals_declined": 0, "proposals_no_response": 0,
        "outbound_send_failed": 0, "invariant_violations": 0,
    }
    today_data = {
        "shifts_today": [], "pending_active_count": 0,
        "pending_send_failed_count": 0,
    }
    return mod._render_brief_text(
        cfg, "2026-05-14", yesterday_counts, today_data,
        degraded=False, catchup_minutes_late=0,
    )


def test_catering_learning_absent_by_default(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    _seed_leads(tmp_path, now)
    _seed_summary(tmp_path, now)
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["yesterday", "today_outlook", "alerts"]))

    assert "Catering pipeline:" in rendered
    assert "Off-menu asks" not in rendered


def test_catering_learning_renders_counts_when_enabled(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    _seed_leads(tmp_path, now)
    _seed_summary(tmp_path, now)
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["yesterday", "today_outlook", "alerts", "catering_learning"]))

    assert "Proposals (30d): 3 sent, 1 selected, 0 send failed, 1 select failed" in rendered
    assert "Off-menu asks: 4 request(s) across 2 lead(s)" in rendered
    forbidden_fragments = [
        "Priya", "Srini", "9876543210", "123 Main", "Pineville",
        "$2500", "35 per head", "deposit", "Venmo", "request_text",
        "raw_inquiry", "Priya special", "Butter_Chicken", "*premium*",
        "\u200b",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in rendered


def test_catering_learning_missing_summary_warns_when_enabled(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    _seed_leads(tmp_path, now)
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["catering_learning"]))

    assert "Learning summary unavailable; check catering-pattern-report.timer" in rendered


def test_catering_learning_missing_leads_still_renders_learning(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    _seed_summary(tmp_path, now)
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["catering_learning"]))

    assert "Leads file unavailable" in rendered
    assert "Proposals (30d): 3 sent, 1 selected, 0 send failed, 1 select failed" in rendered


def test_catering_learning_empty_leads_still_warns_on_missing_summary(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    path = tmp_path / "state" / "catering-leads.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"leads": []}), encoding="utf-8")
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["catering_learning"]))

    assert "No leads on file" in rendered
    assert "Learning summary unavailable; check catering-pattern-report.timer" in rendered


def test_catering_learning_unreadable_leads_still_renders_learning(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    path = tmp_path / "state" / "catering-leads.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")
    _seed_summary(tmp_path, now)
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["catering_learning"]))

    assert "leads file unreadable" in rendered
    assert "Proposals (30d): 3 sent, 1 selected, 0 send failed, 1 select failed" in rendered


def test_catering_learning_stale_summary_warns(tmp_path: Path) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    _seed_leads(tmp_path, now)
    _seed_summary(tmp_path, now - timedelta(days=3))
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["catering_learning"]))

    assert "Learning summary stale (>48h); check catering-pattern-report.timer" in rendered


def test_catering_learning_corrupt_summary_does_not_crash(tmp_path: Path, capsys) -> None:
    now = datetime(2026, 5, 14, 12, tzinfo=timezone.utc)
    _seed_leads(tmp_path, now)
    p = tmp_path / "state" / "catering-learning-summary.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json", encoding="utf-8")
    mod = _load_send_brief(tmp_path)
    mod._customer_now = lambda tz: now

    rendered = _render(mod, _cfg(sections=["catering_learning"]))

    assert "Learning summary unavailable; check catering-pattern-report.timer" in rendered
    assert "catering-learning-summary.json" in capsys.readouterr().err
