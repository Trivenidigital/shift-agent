"""PR-D3 absorbing-shim tests — strips v0.4 PR-B reserved keys on read.

Rationale: safe_io.atomic_write_json round-trips full models via
model_dump_json() (no exclude_defaults). Once PR-B1 ships fields like
voice_quality / quote_source / tone_profile / tone_examples with non-None
defaults, every store-write materializes them on disk. On rollback to a
PR-D3-line binary, extra="forbid" on CateringLead / CustomerConfig would
crash. The shim absorbs by stripping those keys silently on read (after a
one-shot WARN per (model, key) pair per process).

Windows-runnable except the one e2e test that imports safe_io
(fcntl-dependent — Linux-only).
"""
from __future__ import annotations

import platform
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


def test_warn_once_across_different_lead_instances(capsys):
    """R2 MEDIUM: memo is keyed on (model, key), not instance identity.
    Three DIFFERENT leads each carrying voice_quality emit exactly one WARN."""
    for i in range(3):
        raw = {**_minimum_lead_dict(), "lead_id": f"L{i:04d}", "voice_quality": "good"}
        CateringLead.model_validate(raw)
    err = capsys.readouterr().err
    assert err.count("voice_quality") == 1


# ─────────────── R2 HIGH: post-strip idempotency + e2e round-trip ───────────────


def test_post_strip_idempotency_no_second_warn(capsys):
    """R2 HIGH #2: Load with reserved key (strips + WARNs), dump, re-load.
    Second load sees a clean dict — no second WARN, no second strip event."""
    raw = {**_minimum_lead_dict(), "voice_quality": "good"}
    lead = CateringLead.model_validate(raw)
    first = capsys.readouterr().err
    assert first.count("voice_quality") == 1

    dumped = lead.model_dump(mode="json")
    assert "voice_quality" not in dumped  # strip is durable in the model

    CateringLead.model_validate(dumped)
    second = capsys.readouterr().err
    # Once-per-process memo: zero new WARNs on the second load.
    assert "voice_quality" not in second


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io imports fcntl unconditionally — Linux-only",
)
def test_atomic_write_json_round_trip_drops_reserved_keys(tmp_path):
    """R2 HIGH #1: end-to-end test of the actual rollback hazard surface.
    Simulates: PR-B1 binary writes a lead with voice_quality → PR-D3 binary
    re-reads via safe_io → on-disk JSON has NO reserved keys after the
    round-trip, no ValidationError raised. This is the hazard the entire
    PR exists to mitigate."""
    from safe_io import atomic_write_json
    from schemas import CateringLeadStore

    # Write a lead via PR-D3 binary that started life with voice_quality
    # in the input dict (simulates PR-B1-written data being re-read here).
    raw_lead = {**_minimum_lead_dict(), "voice_quality": "good", "quote_source": "llm"}
    lead = CateringLead.model_validate(raw_lead)  # shim strips on read
    store = CateringLeadStore(leads=[lead])

    leads_path = tmp_path / "catering-leads.json"
    atomic_write_json(leads_path, store)

    # On-disk JSON must not contain reserved keys after the round-trip.
    disk_text = leads_path.read_text(encoding="utf-8")
    assert "voice_quality" not in disk_text
    assert "quote_source" not in disk_text

    # Re-loading via the same binary must succeed without ValidationError.
    re_loaded = CateringLeadStore.model_validate_json(disk_text)
    assert len(re_loaded.leads) == 1
    assert re_loaded.leads[0].lead_id == "L0001"


# ─────────────── R2 MEDIUM: value-shape, case, WARN format ───────────────


@pytest.mark.parametrize("bad_value", [None, "", 42, {"nested": "x"}, [1, 2]])
def test_lead_strips_voice_quality_regardless_of_value_shape(bad_value, capsys):
    """R2 MEDIUM: shim must strip the key regardless of value type.
    Pins the contract against a future 'preserve nested rogue detection'
    refactor that would re-introduce the rollback hazard."""
    raw = {**_minimum_lead_dict(), "voice_quality": bad_value}
    lead = CateringLead.model_validate(raw)
    assert "voice_quality" not in lead.model_dump()


def test_lead_strip_is_case_sensitive():
    """R2 MEDIUM: shim strips ONLY the literal key 'voice_quality'.
    Case-variant 'VoiceQuality' must still be rejected by extra='forbid'.
    Guards against a future '.lower()' helpfulness creeping in."""
    raw = {**_minimum_lead_dict(), "VoiceQuality": "good"}
    with pytest.raises(ValidationError):
        CateringLead.model_validate(raw)


def test_warn_format_is_greppable_for_soak_query(capsys):
    """R2 MEDIUM: operational soak query relies on the literal substring
    'PR-D3 absorbing-shim stripped'. Pins the message format so a future
    rewording silently breaking the soak grep is caught at test time."""
    raw = {**_minimum_lead_dict(), "voice_quality": "good"}
    CateringLead.model_validate(raw)
    captured = capsys.readouterr()
    assert "PR-D3 absorbing-shim stripped" in captured.err
    # WARN must go to stderr, not stdout — guards against print() refactor.
    assert captured.out == ""


# ─────────────── R2 LOW: fixture-drift sentinel ───────────────


def test_minimum_dicts_are_actually_valid():
    """R2 LOW: catches fixture drift if a future required field is added
    to either model. Without this, a fixture regression masks as an
    unrelated WARN/strip test failure."""
    CateringLead.model_validate(_minimum_lead_dict())
    CustomerConfig.model_validate(_minimum_config_dict())


# ────────── Review #38 MEDIUM: caller's input dict not mutated ──────────


def test_lead_validator_does_not_mutate_caller_input():
    """Review #38 MEDIUM: shim must not surprise callers by mutating
    their input dict. Pin the no-mutation contract."""
    raw = {**_minimum_lead_dict(), "voice_quality": "good", "quote_source": "llm"}
    snapshot = dict(raw)
    CateringLead.model_validate(raw)
    assert raw == snapshot, "shim mutated caller's input dict"
    assert "voice_quality" in raw
    assert "quote_source" in raw


def test_config_validator_does_not_mutate_caller_input():
    """Review #38 MEDIUM: same contract on CustomerConfig."""
    raw = {**_minimum_config_dict(), "tone_profile": {"formality": "casual"}}
    snapshot = dict(raw)
    CustomerConfig.model_validate(raw)
    assert raw == snapshot, "shim mutated caller's input dict"
    assert "tone_profile" in raw


def test_clean_input_takes_fast_path_unchanged():
    """Steady-state (no reserved keys) returns the dict unchanged
    without making a defensive copy — caller dict is naturally untouched
    because the shim short-circuits before reaching the mutation block."""
    raw = _minimum_lead_dict()
    snapshot = dict(raw)
    CateringLead.model_validate(raw)
    assert raw == snapshot
