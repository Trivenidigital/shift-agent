"""Tests for the FLYER_CREATIVE_DIRECTOR_V2 scoped gate (Slice B Task B2.1).

TDD: these assert on `_creative_director_v2_enabled`, which mirrors the sibling
gates (`_deterministic_first_enabled`, `_premium_overlay_enabled`) exactly:
flag == "1" AND (allowlist empty => global, else normalized customer_phone in
allowlist). Flag-off => False even for an allowlisted number => no behavior change.
"""
from datetime import datetime, timezone

import agents.flyer.render as render_module
from agents.flyer.render import _creative_director_v2_enabled
from schemas import FlyerProject


def _project(phone: str = "+17329837841") -> FlyerProject:
    return FlyerProject(
        project_id="F0250",
        status="intake_started",
        customer_phone=phone,
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        original_message_id="m",
        raw_request="Weekend Specials any item $7.99",
    )


def test_env_unset_returns_false_even_for_allowlisted(monkeypatch):
    monkeypatch.delenv("FLYER_CREATIVE_DIRECTOR_V2", raising=False)
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert _creative_director_v2_enabled(_project("+17329837841")) is False


def test_env_on_allowlisted_number_returns_true(monkeypatch):
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert _creative_director_v2_enabled(_project("+17329837841")) is True


def test_env_on_other_number_returns_false(monkeypatch):
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert _creative_director_v2_enabled(_project("+19998887777")) is False


def test_env_on_empty_allowlist_is_global(monkeypatch):
    """Flag "1" + no allowlist => global ON (mirrors sibling gates)."""
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    assert _creative_director_v2_enabled(_project("+19998887777")) is True


def test_env_const_name(monkeypatch):
    assert render_module.CREATIVE_DIRECTOR_V2_ENV == "FLYER_CREATIVE_DIRECTOR_V2"
