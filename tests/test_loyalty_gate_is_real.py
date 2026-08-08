"""Agent #33 — `cfg.loyalty.enabled` must actually gate the birthday feature.

Why this file exists (Wave-1 falsification, 2026-08-08): `LoyaltyConfig` is
declared in `Config` and documented as "Agent #33 Loyalty v0.1 — birthday
reminders + record-customer-birthday CLI. Default enabled=False; opt-in per
customer." A repo-wide grep found ZERO production consumers of `cfg.loyalty`:
`send-daily-brief` gates only on `"birthdays" in cfg.daily_brief.sections`, and
`record-customer-birthday` reads no gate at all.

That makes `loyalty.enabled` a phantom lever. An operator who sets it to true
gets nothing; an operator who leaves it false still gets birthday output the
moment the section is listed. Either the lever is real or the claim must go —
a config field that documents a control it does not exert is how operators end
up with a wrong mental model of what the agent is doing.

These tests pin the lever as real. `record-customer-birthday` is deliberately
left ungated: recording a birthday is data capture the owner explicitly asked
for, and gating it would silently discard input. Only the OUTPUT path is gated.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="depends on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
SEND_BRIEF = REPO / "src" / "agents" / "daily_brief" / "scripts" / "send-daily-brief"


def _load_send_brief(env_dir: Path):
    sys.path.insert(0, str(PLATFORM_DIR))
    for _modname in ("schemas", "safe_io", "exit_codes", "log_source"):
        _path = PLATFORM_DIR / f"{_modname}.py"
        if not _path.exists():
            continue
        _loader = importlib.machinery.SourceFileLoader(_modname, str(_path))
        _spec = importlib.util.spec_from_file_location(
            _modname, str(_path), loader=_loader
        )
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[_modname] = _mod
        _spec.loader.exec_module(_mod)

    loader = importlib.machinery.SourceFileLoader("send_brief", str(SEND_BRIEF))
    spec = importlib.util.spec_from_file_location(
        "send_brief", str(SEND_BRIEF), loader=loader
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BIRTHDAYS_PATH = env_dir / "state" / "customer-birthdays.json"
    return mod


@pytest.fixture
def env_dir(tmp_path):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test",
            "location_id": "loc_t",
            "timezone": "America/New_York",
        },
        "owner": {
            "name": "Owner",
            "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _render(env_dir: Path, *, loyalty_enabled: bool) -> str:
    """Render a brief whose sections DO include birthdays, varying only loyalty."""
    today = datetime(2026, 5, 10, 12, tzinfo=ZoneInfo("America/New_York"))
    state = env_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "customer-birthdays.json").write_text(
        json.dumps(
            {
                "customers": [
                    {
                        "customer_phone": "+15555550100",
                        "display_name": "Suresh Patel",
                        "birthday": today.strftime("%m-%d"),
                    }
                ],
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )

    mod = _load_send_brief(env_dir)
    mod.TEMPLATES_DIR = REPO / "src" / "agents" / "daily_brief" / "templates"
    mod._customer_now = lambda tz: today

    from schemas import Config  # noqa: E402

    cfg = Config.model_validate(
        {
            "schema_version": 1,
            "customer": {
                "name": "Test",
                "location_id": "loc_t",
                "timezone": "America/New_York",
            },
            "owner": {
                "name": "Owner",
                "phone": "+19045550100",
                "self_chat_jid": "19045550100@s.whatsapp.net",
            },
            "limits": {},
            "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
            "backup": {"gpg_recipient_email": "x@y"},
            "daily_brief": {
                "sections": ["yesterday", "today_outlook", "alerts", "birthdays"]
            },
            "loyalty": {"enabled": loyalty_enabled},
        }
    )

    yesterday_counts = {
        "sick_calls": 0,
        "proposals_created": 0,
        "proposals_accepted": 0,
        "proposals_declined": 0,
        "proposals_no_response": 0,
        "outbound_send_failed": 0,
        "invariant_violations": 0,
    }
    today_data = {
        "shifts_today": [],
        "pending_active_count": 0,
        "pending_send_failed_count": 0,
    }
    return mod._render_brief_text(
        cfg,
        "2026-05-10",
        yesterday_counts,
        today_data,
        degraded=False,
        catchup_minutes_late=0,
    )


def test_loyalty_disabled_suppresses_birthdays_even_when_section_listed(env_dir):
    """THE phantom-lever test.

    Section listed, birthday matches today, but `cfg.loyalty.enabled` is False.
    Agent #33 is documented as opt-in via that flag, so nothing may render.
    """
    rendered = _render(env_dir, loyalty_enabled=False)
    assert "Birthdays today:" not in rendered, (
        "cfg.loyalty.enabled=False did not suppress the birthdays section — "
        "the documented opt-in flag controls nothing.\n"
        f"rendered:\n{rendered}"
    )
    assert "Suresh Patel" not in rendered


def test_loyalty_enabled_plus_section_renders_birthdays(env_dir):
    """The lever's positive case: both gates open -> the line appears."""
    rendered = _render(env_dir, loyalty_enabled=True)
    assert "Birthdays today:" in rendered, f"rendered:\n{rendered}"
    assert "Suresh Patel" in rendered
    assert "+15555550100" in rendered


def test_loyalty_config_has_a_production_consumer():
    """Guard against the field going inert again in a later refactor.

    Greps the shipped source for a real read of `cfg.loyalty`. A schema field
    nobody reads is indistinguishable from a lie in the config file.
    """
    src = REPO / "src"
    hits = [
        path
        for path in src.rglob("*")
        if path.is_file()
        and path.suffix != ".pyc"
        and "__pycache__" not in path.parts
        and path.name != "schemas.py"
        and "cfg.loyalty" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits, (
        "no file under src/ reads cfg.loyalty — LoyaltyConfig is declared and "
        "documented as an opt-in gate but exerts no control."
    )
