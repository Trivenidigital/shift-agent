"""PR-D3 absorbing-shim tests — strips v0.4 PR-B reserved keys on read.

Rationale: safe_io.atomic_write_json round-trips full models via
model_dump_json() (no exclude_defaults). Once PR-B1 ships fields like
voice_quality / quote_source / tone_profile / tone_examples with non-None
defaults, every store-write materializes them on disk. On rollback to a
PR-D3-line binary, extra="forbid" on CateringLead / CustomerConfig would
crash. The shim absorbs by stripping those keys silently on read (after a
one-shot WARN per (model, key) pair per process).

Windows-runnable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas import (
    CateringLead, CustomerConfig,
)
import schemas as schemas_mod


@pytest.fixture(autouse=True)
def _reset_pr_b_warned():
    """Each test sees a fresh once-per-process memo so WARN assertions
    are deterministic across the suite."""
    schemas_mod._PR_B_WARNED.clear()
    yield
    schemas_mod._PR_B_WARNED.clear()


def _minimum_lead_dict() -> dict:
    """Smallest valid CateringLead payload (status=NEW dodges the
    quote_text post-AWAITING requirement)."""
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "lead_id": "L0001",
        "status": "NEW",
        "customer_phone": "+19045550199",
        "raw_inquiry": "Need catering for 50 people",
        "original_message_id": "msg_meta_1",
        "created_at": now,
        "updated_at": now,
    }


def _minimum_config_dict() -> dict:
    return {
        "name": "Triveni Supermarket",
        "location_id": "test-location",
        "timezone": "America/Chicago",
    }


# ────────────────────────── CateringLead ──────────────────────────


def test_lead_strips_voice_quality_on_read(capsys):
    """Future PR-B1 binary wrote voice_quality; PR-D3 strips it cleanly."""
    raw = {**_minimum_lead_dict(), "voice_quality": "good"}
    lead = CateringLead.model_validate(raw)
    assert "voice_quality" not in lead.model_dump()
    err = capsys.readouterr().err
    assert "PR-D3 absorbing-shim stripped" in err
    assert "voice_quality" in err


def test_lead_strips_quote_source_on_read(capsys):
    raw = {**_minimum_lead_dict(), "quote_source": "llm"}
    lead = CateringLead.model_validate(raw)
    assert "quote_source" not in lead.model_dump()
    err = capsys.readouterr().err
    assert "quote_source" in err


def test_lead_strips_both_keys_on_same_read(capsys):
    """Both keys present at once — both stripped, both warned."""
    raw = {**_minimum_lead_dict(), "voice_quality": "neutral", "quote_source": "template"}
    lead = CateringLead.model_validate(raw)
    dumped = lead.model_dump()
    assert "voice_quality" not in dumped
    assert "quote_source" not in dumped
    err = capsys.readouterr().err
    assert "voice_quality" in err
    assert "quote_source" in err


def test_lead_round_trip_idempotent_no_reserved_keys(capsys):
    """Reading + writing a clean lead is byte-identical (shim is no-op)."""
    raw = _minimum_lead_dict()
    lead = CateringLead.model_validate(raw)
    dumped = lead.model_dump(mode="json")
    re_loaded = CateringLead.model_validate(dumped)
    assert re_loaded.model_dump(mode="json") == dumped
    err = capsys.readouterr().err
    # No WARN emitted for clean reads.
    assert "PR-D3 absorbing-shim stripped" not in err


def test_lead_extra_forbid_still_rejects_unknown_extras(capsys):
    """Shim strips ONLY the documented PR-B reserved keys, not arbitrary
    extras. extra='forbid' must still reject typos."""
    raw = {**_minimum_lead_dict(), "typo_field": "should_be_rejected"}
    with pytest.raises(ValidationError):
        CateringLead.model_validate(raw)


# ────────────────────────── CustomerConfig ──────────────────────────


def test_config_strips_tone_profile_on_read(capsys):
    raw = {**_minimum_config_dict(), "tone_profile": {"formality": "casual"}}
    cfg = CustomerConfig.model_validate(raw)
    assert "tone_profile" not in cfg.model_dump()
    err = capsys.readouterr().err
    assert "PR-D3 absorbing-shim stripped" in err
    assert "tone_profile" in err


def test_config_strips_tone_examples_on_read(capsys):
    raw = {**_minimum_config_dict(), "tone_examples": ["Hello!", "Thanks!"]}
    cfg = CustomerConfig.model_validate(raw)
    assert "tone_examples" not in cfg.model_dump()
    err = capsys.readouterr().err
    assert "tone_examples" in err


def test_config_round_trip_idempotent_no_reserved_keys(capsys):
    raw = _minimum_config_dict()
    cfg = CustomerConfig.model_validate(raw)
    dumped = cfg.model_dump(mode="json")
    re_loaded = CustomerConfig.model_validate(dumped)
    assert re_loaded.model_dump(mode="json") == dumped
    err = capsys.readouterr().err
    assert "PR-D3 absorbing-shim stripped" not in err


def test_config_extra_forbid_still_rejects_unknown_extras():
    raw = {**_minimum_config_dict(), "rogue_field": "x"}
    with pytest.raises(ValidationError):
        CustomerConfig.model_validate(raw)


# ─────────────────── once-per-process WARN behavior ───────────────────


def test_warn_emitted_only_once_per_key_per_process(capsys):
    """Three reads with voice_quality should emit WARN exactly once."""
    raw = {**_minimum_lead_dict(), "voice_quality": "good"}
    CateringLead.model_validate(raw)
    CateringLead.model_validate(raw)
    CateringLead.model_validate(raw)
    err = capsys.readouterr().err
    # Exactly one WARN line for voice_quality across three calls.
    assert err.count("voice_quality") == 1


def test_warn_separate_per_key(capsys):
    """voice_quality and quote_source warn separately on first occurrence."""
    raw1 = {**_minimum_lead_dict(), "voice_quality": "good"}
    raw2 = {**_minimum_lead_dict(), "quote_source": "llm"}
    CateringLead.model_validate(raw1)
    CateringLead.model_validate(raw2)
    err = capsys.readouterr().err
    assert err.count("voice_quality") == 1
    assert err.count("quote_source") == 1


def test_warn_separate_per_model(capsys):
    """CateringLead.voice_quality and CustomerConfig.tone_profile warn separately."""
    lead_raw = {**_minimum_lead_dict(), "voice_quality": "good"}
    cfg_raw = {**_minimum_config_dict(), "tone_profile": {"formality": "casual"}}
    CateringLead.model_validate(lead_raw)
    CustomerConfig.model_validate(cfg_raw)
    err = capsys.readouterr().err
    assert "CateringLead" in err
    assert "CustomerConfig" in err
