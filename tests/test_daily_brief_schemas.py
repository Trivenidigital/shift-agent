"""Pydantic-only tests for Daily Brief schema additions. No fcntl imports — runs on Windows."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import (
    Config, DailyBriefConfig, BriefSection,
    BriefAttempted, BriefSent, BriefSendFailed, BriefSkipped,
    CateringLearningProposalHealth, CateringLearningSummary,
)
from datetime import datetime, timezone


def test_brief_time_regex_rejects_invalid_hours():
    for bad in ["24:00", "25:00", "29:30", "99:99", "7:00", "07:60", "abc", "", "07:99"]:
        with pytest.raises(ValidationError):
            DailyBriefConfig(brief_time=bad)


def test_brief_time_regex_accepts_boundary_times():
    for good in ["00:00", "07:00", "12:30", "23:59"]:
        c = DailyBriefConfig(brief_time=good)
        assert c.brief_time == good


def test_daily_brief_config_defaults():
    c = DailyBriefConfig()
    assert c.enabled is True
    assert c.brief_time == "07:00"
    assert c.max_words == 150
    assert c.sections == ["yesterday", "today_outlook", "alerts"]
    assert c.catchup_window_minutes == 180


def test_daily_brief_config_extra_forbid():
    with pytest.raises(ValidationError):
        DailyBriefConfig(typo_field=True)  # type: ignore[call-arg]


def test_daily_brief_config_max_words_bounds():
    with pytest.raises(ValidationError):
        DailyBriefConfig(max_words=49)
    with pytest.raises(ValidationError):
        DailyBriefConfig(max_words=501)
    DailyBriefConfig(max_words=50)
    DailyBriefConfig(max_words=500)


def test_daily_brief_sections_min_length_one():
    with pytest.raises(ValidationError):
        DailyBriefConfig(sections=[])
    DailyBriefConfig(sections=["alerts"])


def test_daily_brief_catering_learning_section_opt_in_only():
    cfg = DailyBriefConfig()
    assert "catering_learning" not in cfg.sections
    enabled = DailyBriefConfig(sections=["catering_learning"])
    assert enabled.sections == ["catering_learning"]


def test_daily_brief_catchup_bounds():
    with pytest.raises(ValidationError):
        DailyBriefConfig(catchup_window_minutes=14)
    with pytest.raises(ValidationError):
        DailyBriefConfig(catchup_window_minutes=721)
    DailyBriefConfig(catchup_window_minutes=15)
    DailyBriefConfig(catchup_window_minutes=720)


def test_brief_attempted_validators():
    now = datetime.now(tz=timezone.utc)
    # empty attempt_id rejected
    with pytest.raises(ValidationError):
        BriefAttempted(
            type="brief_attempted", ts=now, brief_date="2026-04-28",
            attempt_id="", word_count=10, sections_included=["yesterday"],
            source_count=0,
        )
    # negative source_count rejected
    with pytest.raises(ValidationError):
        BriefAttempted(
            type="brief_attempted", ts=now, brief_date="2026-04-28",
            attempt_id="abc", word_count=10, sections_included=["yesterday"],
            source_count=-1,
        )
    # invalid brief_date pattern
    with pytest.raises(ValidationError):
        BriefAttempted(
            type="brief_attempted", ts=now, brief_date="04-28-2026",
            attempt_id="abc", word_count=10, sections_included=["yesterday"],
            source_count=0,
        )
    # valid construction
    e = BriefAttempted(
        type="brief_attempted", ts=now, brief_date="2026-04-28",
        attempt_id="abc", word_count=42, sections_included=["yesterday", "alerts"],
        source_count=5, degraded_mode=True, catchup_minutes_late=30,
    )
    assert e.attempt_id == "abc"
    assert e.degraded_mode is True


def test_brief_sent_requires_message_id_and_jid():
    now = datetime.now(tz=timezone.utc)
    with pytest.raises(ValidationError):
        BriefSent(
            type="brief_sent", ts=now, brief_date="2026-04-28",
            attempt_id="abc", outbound_message_id="", self_chat_jid="x@s.whatsapp.net",
        )
    with pytest.raises(ValidationError):
        BriefSent(
            type="brief_sent", ts=now, brief_date="2026-04-28",
            attempt_id="abc", outbound_message_id="msg1", self_chat_jid="",
        )
    BriefSent(
        type="brief_sent", ts=now, brief_date="2026-04-28",
        attempt_id="abc", outbound_message_id="msg1", self_chat_jid="x@s.whatsapp.net",
    )


def test_brief_send_failed_no_error_length_cap():
    now = datetime.now(tz=timezone.utc)
    long_err = "x" * 5000  # 5000 chars — should be allowed (matches OutboundSendFailed)
    e = BriefSendFailed(
        type="brief_send_failed", ts=now, brief_date="2026-04-28",
        attempt_id="abc", error=long_err, retry_count=2,
    )
    assert len(e.error) == 5000


def test_brief_skipped_reason_literal():
    now = datetime.now(tz=timezone.utc)
    for reason in ["already_sent", "data_unavailable", "disabled",
                   "catchup_expired", "dependency_down", "send_uncertain"]:
        e = BriefSkipped(type="brief_skipped", ts=now, brief_date="2026-04-28", reason=reason)
        assert e.reason == reason
    # Removed reasons must be rejected
    for bad in ["no_activity", "outside_window", "random"]:
        with pytest.raises(ValidationError):
            BriefSkipped(type="brief_skipped", ts=now, brief_date="2026-04-28", reason=bad)


def test_catering_learning_summary_schema_validators():
    now = datetime.now(tz=timezone.utc)
    summary = CateringLearningSummary(
        generated_at=now,
        window_days=30,
        proposal_health=CateringLearningProposalHealth(
            sent=2, selected=1, send_failed=0, select_failed=1,
        ),
        off_menu_request_count=4,
        leads_with_off_menu_count=3,
        active_missing_info_count=1,
        menu_updated_at=now,
        menu_freshness_days=0,
        degraded_sources=["menu"],
    )
    assert summary.source == "catering-pattern-report"
    assert summary.off_menu_request_count == 4

    with pytest.raises(ValidationError):
        CateringLearningSummary(
            generated_at=now,
            window_days=30,
            off_menu_request_count=-1,
        )
    with pytest.raises(ValidationError):
        CateringLearningSummary(
            generated_at=now,
            window_days=30,
            unexpected=True,
        )


def test_config_backward_compat_no_daily_brief():
    """Old config (no daily_brief block) must still load with defaults applied."""
    old = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045551234"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }
    c = Config.model_validate(old)
    assert c.daily_brief.brief_time == "07:00"
    assert c.daily_brief.enabled is True


def test_config_with_daily_brief_overrides():
    cfg_dict = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "l", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045551234"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "daily_brief": {"brief_time": "08:30", "max_words": 200, "sections": ["alerts"]},
    }
    c = Config.model_validate(cfg_dict)
    assert c.daily_brief.brief_time == "08:30"
    assert c.daily_brief.max_words == 200
    assert c.daily_brief.sections == ["alerts"]
