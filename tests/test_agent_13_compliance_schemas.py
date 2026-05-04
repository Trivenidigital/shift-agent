"""PR-Agent13 Commit 1 — Compliance Calendar schema tests.

Mirrors test_agent_3_multi_location.py structure. Cross-platform (no
fcntl); pure Pydantic schema validation.

Covers:
- ComplianceItem accept/reject (id pattern, recurrence_days bounds, HttpUrl)
- ComplianceItemsFile defaults + max_length=200
- ComplianceLastSentFile key-format validator
- ComplianceConfig new fields (daily_brief_section_enabled, max_deferral_days)
- 6 audit variants accept/reject + roundtrip via TypeAdapter[LogEntry]
- BriefSection extended with 'compliance' (forward-compat)
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

import schemas  # noqa: E402
from pydantic import ValidationError, TypeAdapter  # noqa: E402


# ============================================================================
# ComplianceItem schema tests
# ============================================================================

class TestComplianceItem:
    def test_minimal_valid(self):
        item = schemas.ComplianceItem(
            id="health_inspect_houston",
            name="Health Inspection — Houston Galleria",
            category="inspection",
            renewal_date=date(2026, 9, 1),
            recurrence_days=365,
        )
        assert item.id == "health_inspect_houston"
        assert item.location_id is None
        assert item.agency is None
        assert item.resource_url is None
        assert item.notes is None

    def test_full_valid(self):
        item = schemas.ComplianceItem(
            id="tax_q3_tx",
            name="Texas Sales Tax Q3",
            category="tax_filing",
            renewal_date=date(2026, 10, 20),
            recurrence_days=90,
            location_id="loc_hou_01",
            agency="Texas Comptroller",
            resource_url="https://comptroller.texas.gov/",
            notes="File electronically; payment due same day",
        )
        assert str(item.resource_url).startswith("https://")
        assert item.location_id == "loc_hou_01"

    def test_id_pattern_rejects_uppercase(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="Health_Inspect", name="x", category="inspection",
                renewal_date=date(2026, 9, 1), recurrence_days=365,
            )

    def test_id_pattern_rejects_special_chars(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="health-inspect", name="x", category="inspection",
                renewal_date=date(2026, 9, 1), recurrence_days=365,
            )

    def test_recurrence_days_zero_accepted_one_shot(self):
        item = schemas.ComplianceItem(
            id="one_shot", name="One-time", category="other",
            renewal_date=date(2026, 9, 1), recurrence_days=0,
        )
        assert item.recurrence_days == 0

    def test_recurrence_days_negative_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="x", name="y", category="other",
                renewal_date=date(2026, 9, 1), recurrence_days=-1,
            )

    def test_recurrence_days_too_large_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="x", name="y", category="other",
                renewal_date=date(2026, 9, 1), recurrence_days=3651,
            )

    def test_bad_category_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="x", name="y", category="random_made_up",
                renewal_date=date(2026, 9, 1), recurrence_days=365,
            )

    def test_bad_url_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="x", name="y", category="other",
                renewal_date=date(2026, 9, 1), recurrence_days=365,
                resource_url="not-a-url",
            )

    def test_oversize_notes_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItem(
                id="x", name="y", category="other",
                renewal_date=date(2026, 9, 1), recurrence_days=365,
                notes="x" * 501,
            )


# ============================================================================
# ComplianceItemsFile tests
# ============================================================================

class TestComplianceItemsFile:
    def test_empty_default(self):
        f = schemas.ComplianceItemsFile()
        assert f.schema_version == 1
        assert f.items == []

    def test_max_length_200_enforced(self):
        items = [
            {
                "id": f"item_{i}", "name": f"Item {i}", "category": "other",
                "renewal_date": "2026-09-01", "recurrence_days": 365,
            }
            for i in range(201)
        ]
        with pytest.raises(ValidationError):
            schemas.ComplianceItemsFile(items=items)

    def test_schema_version_must_be_1(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItemsFile(schema_version=2, items=[])


# ============================================================================
# ComplianceLastSentFile + key validator tests
# ============================================================================

class TestComplianceLastSentFile:
    def test_empty_default(self):
        f = schemas.ComplianceLastSentFile()
        assert f.last_sent == {}

    def test_valid_keys_accepted(self):
        f = schemas.ComplianceLastSentFile(last_sent={
            "health_inspect_houston:30": "2026-08-02",
            "tax_q3_tx:7": "2026-09-15",
            "x:0": "2026-09-01",
            "y:-3": "2026-09-04",  # overdue gate
        })
        assert len(f.last_sent) == 4

    def test_bad_key_no_colon_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceLastSentFile(last_sent={"badkey": "2026-09-01"})

    def test_bad_key_non_int_gate_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceLastSentFile(last_sent={"x:abc": "2026-09-01"})

    def test_empty_item_id_rejected(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceLastSentFile(last_sent={":30": "2026-09-01"})


# ============================================================================
# ComplianceConfig extended fields tests
# ============================================================================

class TestComplianceConfig:
    def test_defaults(self):
        c = schemas.ComplianceConfig()
        assert c.enabled is False
        assert c.daily_brief_section_enabled is False
        assert c.max_deferral_days == 7
        assert c.advance_warning_days == [30, 14, 7, 3, 1]

    def test_max_deferral_days_bounds(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceConfig(max_deferral_days=0)
        with pytest.raises(ValidationError):
            schemas.ComplianceConfig(max_deferral_days=31)

    def test_advance_warning_days_validator(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceConfig(advance_warning_days=[])
        with pytest.raises(ValidationError):
            schemas.ComplianceConfig(advance_warning_days=[0, 7])
        with pytest.raises(ValidationError):
            schemas.ComplianceConfig(advance_warning_days=[-1, 7])
        c = schemas.ComplianceConfig(advance_warning_days=[7, 14, 7])
        assert c.advance_warning_days == [14, 7]  # deduped + sorted desc


# ============================================================================
# Audit variant tests
# ============================================================================

class TestComplianceAuditVariants:
    NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_attempted_minimum(self):
        e = schemas.ComplianceReminderAttempted(
            type="compliance_reminder_attempted", ts=self.NOW,
            item_id="x", item_name="X",
            days_until_renewal=14, gate_days=14,
            attempt_id="abc123",
        )
        assert e.catchup_for_missed_gate is None

    def test_attempted_with_catchup(self):
        e = schemas.ComplianceReminderAttempted(
            type="compliance_reminder_attempted", ts=self.NOW,
            item_id="x", item_name="X",
            days_until_renewal=13, gate_days=14,
            attempt_id="abc", catchup_for_missed_gate=14,
        )
        assert e.catchup_for_missed_gate == 14

    def test_sent_requires_message_id(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceReminderSent(
                type="compliance_reminder_sent", ts=self.NOW,
                item_id="x", days_until_renewal=14, gate_days=14,
                attempt_id="abc", outbound_message_id="",
            )

    def test_failed_no_length_cap_on_error(self):
        e = schemas.ComplianceReminderFailed(
            type="compliance_reminder_failed", ts=self.NOW,
            item_id="x", days_until_renewal=14, gate_days=14,
            attempt_id="abc", error="x" * 5000, retry_count=1,
        )
        assert len(e.error) == 5000

    def test_skipped_orphan_reason(self):
        e = schemas.ComplianceReminderSkipped(
            type="compliance_reminder_skipped", ts=self.NOW,
            item_id="x", gate_days=14,
            reason="orphan_attempted_in_window", orphan_attempt_id="orphan123",
        )
        assert e.reason == "orphan_attempted_in_window"

    def test_deferred_carries_pushover_status(self):
        e = schemas.ComplianceReminderDeferred(
            type="compliance_reminder_deferred", ts=self.NOW,
            item_id="x", days_until_renewal=22,
            gate_days=30, days_since_ideal_fire=8,
            operator_pushover_sent=False,
        )
        assert e.operator_pushover_sent is False

    def test_marked_done_one_shot_next_is_none(self):
        e = schemas.ComplianceItemMarkedDone(
            type="compliance_item_marked_done", ts=self.NOW,
            item_id="x", completed_renewal_date=date(2026, 9, 1),
            next_renewal_date=None, actor="owner", sentinel_keys_pruned=3,
        )
        assert e.next_renewal_date is None

    def test_marked_done_actor_widened(self):
        # Reviewer B-v2 M1: actor must accept "system" (cron-driven) too
        e = schemas.ComplianceItemMarkedDone(
            type="compliance_item_marked_done", ts=self.NOW,
            item_id="x", completed_renewal_date=date(2026, 9, 1),
            actor="system", sentinel_keys_pruned=0,
        )
        assert e.actor == "system"

    def test_marked_done_actor_rejects_invalid(self):
        with pytest.raises(ValidationError):
            schemas.ComplianceItemMarkedDone(
                type="compliance_item_marked_done", ts=self.NOW,
                item_id="x", completed_renewal_date=date(2026, 9, 1),
                actor="random", sentinel_keys_pruned=0,
            )


# ============================================================================
# LogEntry discriminated-union dispatch
# ============================================================================

class TestLogEntryDispatch:
    NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    adapter = TypeAdapter(schemas.LogEntry)

    def test_compliance_reminder_attempted_dispatches(self):
        raw = {
            "type": "compliance_reminder_attempted",
            "ts": self.NOW.isoformat(),
            "item_id": "x", "item_name": "X",
            "days_until_renewal": 14, "gate_days": 14, "attempt_id": "abc",
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.ComplianceReminderAttempted)

    def test_compliance_reminder_sent_dispatches(self):
        raw = {
            "type": "compliance_reminder_sent",
            "ts": self.NOW.isoformat(),
            "item_id": "x", "days_until_renewal": 14, "gate_days": 14,
            "attempt_id": "abc", "outbound_message_id": "wamid.123",
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.ComplianceReminderSent)

    def test_compliance_item_marked_done_dispatches(self):
        raw = {
            "type": "compliance_item_marked_done",
            "ts": self.NOW.isoformat(),
            "item_id": "x", "completed_renewal_date": "2026-09-01",
            "actor": "owner", "sentinel_keys_pruned": 0,
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.ComplianceItemMarkedDone)

    def test_all_six_compliance_variants_roundtrip(self):
        variants = [
            ("compliance_reminder_attempted", {
                "item_id": "x", "item_name": "X",
                "days_until_renewal": 14, "gate_days": 14, "attempt_id": "a",
            }),
            ("compliance_reminder_sent", {
                "item_id": "x", "days_until_renewal": 14, "gate_days": 14,
                "attempt_id": "a", "outbound_message_id": "m",
            }),
            ("compliance_reminder_failed", {
                "item_id": "x", "days_until_renewal": 14, "gate_days": 14,
                "attempt_id": "a", "error": "boom", "retry_count": 1,
            }),
            ("compliance_reminder_skipped", {
                "item_id": "x", "gate_days": 14,
                "reason": "orphan_attempted_in_window", "orphan_attempt_id": "z",
            }),
            ("compliance_reminder_deferred", {
                "item_id": "x", "days_until_renewal": 22, "gate_days": 30,
                "days_since_ideal_fire": 8, "operator_pushover_sent": True,
            }),
            ("compliance_item_marked_done", {
                "item_id": "x", "completed_renewal_date": "2026-09-01",
                "actor": "owner", "sentinel_keys_pruned": 0,
            }),
        ]
        for type_str, fields in variants:
            raw = {"type": type_str, "ts": self.NOW.isoformat(), **fields}
            entry = self.adapter.validate_python(raw)
            j = entry.model_dump_json()
            roundtrip = self.adapter.validate_python(json.loads(j))
            assert type(roundtrip) is type(entry)


# ============================================================================
# BriefSection extension
# ============================================================================

class TestBriefSectionExtension:
    def test_compliance_value_accepted(self):
        # Must validate as part of cfg.daily_brief.sections list
        c = schemas.DailyBriefConfig(sections=["yesterday", "compliance"])
        assert "compliance" in c.sections

    def test_default_unchanged(self):
        # Forward-compat: default does NOT include "compliance"
        c = schemas.DailyBriefConfig()
        assert "compliance" not in c.sections
