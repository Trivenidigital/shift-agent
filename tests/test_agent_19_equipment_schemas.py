"""PR-Agent19-v0.1 — Equipment & Maintenance scaffold schema tests."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

import schemas  # noqa: E402
from pydantic import ValidationError, TypeAdapter  # noqa: E402


class TestEquipmentMaintenanceConfig:
    def test_defaults(self):
        c = schemas.EquipmentMaintenanceConfig()
        assert c.enabled is False
        assert c.advance_warning_days == [30, 14, 7, 3, 1]
        assert c.auto_route_to_vendor is False

    def test_advance_warning_days_validator(self):
        with pytest.raises(ValidationError):
            schemas.EquipmentMaintenanceConfig(advance_warning_days=[])
        with pytest.raises(ValidationError):
            schemas.EquipmentMaintenanceConfig(advance_warning_days=[0, 7])
        c = schemas.EquipmentMaintenanceConfig(advance_warning_days=[7, 14, 7])
        assert c.advance_warning_days == [14, 7]


class TestEquipmentIssueLogged:
    NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

    def test_minimal(self):
        e = schemas.EquipmentIssueLogged(
            type="equipment_issue_logged", ts=self.NOW,
            equipment_id="walk_in_freezer_01",
            issue_category="leaking", severity="medium",
        )
        assert e.location_id is None
        assert e.detail == ""

    def test_severity_critical(self):
        e = schemas.EquipmentIssueLogged(
            type="equipment_issue_logged", ts=self.NOW,
            equipment_id="fire_supp_main",
            location_id="loc_hou_01",
            issue_category="broken", severity="critical",
            detail="Fire suppression panel showing red fault light",
        )
        assert e.severity == "critical"

    def test_bad_category_rejected(self):
        with pytest.raises(ValidationError):
            schemas.EquipmentIssueLogged(
                type="equipment_issue_logged", ts=self.NOW,
                equipment_id="x", issue_category="random", severity="low",
            )

    def test_bad_severity_rejected(self):
        with pytest.raises(ValidationError):
            schemas.EquipmentIssueLogged(
                type="equipment_issue_logged", ts=self.NOW,
                equipment_id="x", issue_category="broken", severity="urgent",
            )


class TestEquipmentMaintenanceDeclined:
    NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

    def test_disabled(self):
        e = schemas.EquipmentMaintenanceDeclined(
            type="equipment_maintenance_declined", ts=self.NOW,
            requester_role="employee", reason="agent_disabled",
        )
        assert e.reason == "agent_disabled"


class TestLogEntryDispatch:
    NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    adapter = TypeAdapter(schemas.LogEntry)

    def test_equipment_issue_dispatches(self):
        raw = {
            "type": "equipment_issue_logged", "ts": self.NOW.isoformat(),
            "equipment_id": "x", "issue_category": "broken", "severity": "high",
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.EquipmentIssueLogged)

    def test_declined_dispatches(self):
        raw = {
            "type": "equipment_maintenance_declined", "ts": self.NOW.isoformat(),
            "requester_role": "owner", "reason": "agent_disabled",
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.EquipmentMaintenanceDeclined)

    def test_both_roundtrip(self):
        for type_str, fields in [
            ("equipment_issue_logged", {
                "equipment_id": "x", "issue_category": "broken", "severity": "high",
            }),
            ("equipment_maintenance_declined", {
                "requester_role": "owner", "reason": "agent_disabled",
            }),
        ]:
            raw = {"type": type_str, "ts": self.NOW.isoformat(), **fields}
            entry = self.adapter.validate_python(raw)
            j = entry.model_dump_json()
            roundtrip = self.adapter.validate_python(json.loads(j))
            assert type(roundtrip) is type(entry)


class TestConfigWiring:
    def test_equipment_maintenance_in_config(self):
        cfg_dict = {
            "schema_version": 1,
            "customer": {"name": "x", "location_id": "y", "timezone": "America/New_York"},
            "owner": {"name": "x", "phone": "+10000000000", "self_chat_jid": "x@s.whatsapp.net"},
            "limits": {},
            "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
            "backup": {"gpg_recipient_email": "x@y"},
        }
        cfg = schemas.Config.model_validate(cfg_dict)
        assert cfg.equipment_maintenance.enabled is False
        assert cfg.equipment_maintenance.advance_warning_days == [30, 14, 7, 3, 1]
