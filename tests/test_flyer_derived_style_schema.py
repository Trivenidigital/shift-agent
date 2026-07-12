"""Schema contracts for the style-transfer derivation layer (Workstream A, 2026-07-11).

- `FlyerDerivedStyle` is an LLM-output shape (`extra="ignore"`): unmodelled keys
  are dropped, not rejected.
- `derived_style` is an additive optional field on `FlyerBrandAsset`; every
  pre-existing row (no `derived_style`) still validates, and `FlyerBrandAsset`
  keeps `extra="forbid"` for its own keys.
- `flyer_brand_style_derived` routes through the LogEntry discriminated union to
  the typed variant (never `_UnknownLogEntry`).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas import (
    FlyerBrandAsset,
    FlyerBrandStyleDerived,
    FlyerDerivedStyle,
    LogEntry,
)

SHA = "a" * 64


def _derived(**kw):
    base = dict(
        palette=["warm cream", "tricolor green", "saffron orange"],
        typography="brush-script-headline",
        energy="busy",
        motifs=["marigold border", "food-photo strip"],
        base_register="festive-vernacular",
        derived_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
        source_sha256=SHA,
        model="openai/gpt-4o-mini",
    )
    base.update(kw)
    return FlyerDerivedStyle(**base)


def test_derived_style_ignores_unmodelled_keys():
    ds = FlyerDerivedStyle.model_validate({
        "palette": ["cream"],
        "typography": "grotesque",
        "energy": "balanced",
        "derived_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
        "source_sha256": SHA,
        "model": "m",
        "confidence": "high",          # unmodelled — dropped, not rejected
        "notes_from_model": "whatever",
    })
    assert ds.palette == ["cream"]
    assert not hasattr(ds, "confidence")


def test_derived_style_energy_is_constrained():
    with pytest.raises(ValidationError):
        _derived(energy="loud")  # not in the Literal set


def test_derived_style_requires_valid_sha256():
    with pytest.raises(ValidationError):
        _derived(source_sha256="short")


def test_brand_asset_accepts_derived_style_and_defaults_none():
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    without = FlyerBrandAsset(
        asset_id="B0008", kind="template", path="/opt/shift-agent/state/flyer/b.png",
        mime_type="image/png", sha256=SHA, original_message_id="m1", received_at=now,
    )
    assert without.derived_style is None

    with_ds = without.model_copy(update={"derived_style": _derived()})
    assert with_ds.derived_style is not None
    assert with_ds.derived_style.base_register == "festive-vernacular"
    # round-trip through JSON preserves the nested model
    reloaded = FlyerBrandAsset.model_validate_json(with_ds.model_dump_json())
    assert reloaded.derived_style.typography == "brush-script-headline"


def test_brand_asset_still_forbids_unknown_keys():
    now = datetime(2026, 7, 11, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        FlyerBrandAsset(
            asset_id="B0008", kind="template", path="/opt/shift-agent/state/flyer/b.png",
            mime_type="image/png", sha256=SHA, original_message_id="m1", received_at=now,
            bogus_field="nope",
        )


def test_pre_existing_row_without_derived_style_validates():
    # A row persisted before this field existed (no `derived_style` key).
    row = {
        "asset_id": "B0006", "kind": "template",
        "path": "/opt/shift-agent/state/flyer/b.png", "mime_type": "image/png",
        "sha256": SHA, "original_message_id": "m1",
        "received_at": "2026-06-01T00:00:00+00:00", "active": True, "notes": "",
    }
    asset = FlyerBrandAsset.model_validate(row)
    assert asset.derived_style is None


def test_brand_style_derived_routes_through_log_entry():
    entry = FlyerBrandStyleDerived(
        ts=datetime.now(timezone.utc), asset_id="B0008", customer_id="CUST0001",
        ok=True, screen_hits=[], model="openai/gpt-4o-mini",
    )
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.__class__ is FlyerBrandStyleDerived
    assert parsed.type == "flyer_brand_style_derived"
    assert parsed.ok is True
    assert parsed.screen_hits == []


def test_brand_style_derived_carries_screen_hits_when_not_ok():
    entry = FlyerBrandStyleDerived(
        ts=datetime.now(timezone.utc), asset_id="B0008", customer_id="CUST0001",
        ok=False, screen_hits=["identity_import:kitchen", "no_fact_law:$5"], model="m",
    )
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.__class__ is FlyerBrandStyleDerived
    assert parsed.ok is False
    assert "no_fact_law:$5" in parsed.screen_hits
