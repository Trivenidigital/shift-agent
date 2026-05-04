"""PR-Agent22-v0.1 — P&L Anomaly Detective scaffold schema tests."""
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


class TestPnlAnomalyConfig:
    def test_defaults(self):
        c = schemas.PnlAnomalyConfig()
        assert c.enabled is False
        assert c.margin_drop_alert_pct == 8.0
        assert c.location_underperform_alert_pct == 15.0
        assert c.trailing_window_weeks == 4
        assert c.pos_provider is None

    def test_margin_drop_pct_bounds(self):
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(margin_drop_alert_pct=0.4)
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(margin_drop_alert_pct=51)

    def test_location_underperform_pct_bounds(self):
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(location_underperform_alert_pct=0.5)
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(location_underperform_alert_pct=51)

    def test_trailing_window_weeks_bounds(self):
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(trailing_window_weeks=0)
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(trailing_window_weeks=53)

    def test_pos_provider_accepts_known_values(self):
        for prov in ("clover", "square", "toast", "other"):
            c = schemas.PnlAnomalyConfig(pos_provider=prov)
            assert c.pos_provider == prov

    def test_pos_provider_rejects_unknown(self):
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyConfig(pos_provider="lightspeed")


class TestPnlAnomalyDetected:
    NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

    def test_margin_drop(self):
        e = schemas.PnlAnomalyDetected(
            type="pnl_anomaly_detected", ts=self.NOW,
            anomaly_type="margin_drop", target_id="biriyani",
            delta_pct=-8.5, baseline_value=42.0, current_value=33.5,
        )
        assert e.anomaly_type == "margin_drop"
        assert e.detail == ""

    def test_location_underperform(self):
        e = schemas.PnlAnomalyDetected(
            type="pnl_anomaly_detected", ts=self.NOW,
            anomaly_type="location_underperform", target_id="loc_pineville",
            delta_pct=-15.2, baseline_value=12000.0, current_value=10176.0,
            detail="rolling 4-week trailing average baseline",
        )
        assert e.target_id == "loc_pineville"

    def test_bad_anomaly_type_rejected(self):
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyDetected(
                type="pnl_anomaly_detected", ts=self.NOW,
                anomaly_type="random_made_up", target_id="x",
                delta_pct=0.0, baseline_value=0, current_value=0,
            )


class TestPnlAnomalyDeclined:
    NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)

    def test_disabled_reason(self):
        e = schemas.PnlAnomalyDeclined(
            type="pnl_anomaly_declined", ts=self.NOW,
            requester_role="owner", reason="agent_disabled",
        )
        assert e.reason == "agent_disabled"

    def test_no_pos_reason(self):
        e = schemas.PnlAnomalyDeclined(
            type="pnl_anomaly_declined", ts=self.NOW,
            requester_role="owner", reason="no_pos_configured",
        )
        assert e.reason == "no_pos_configured"

    def test_bad_reason_rejected(self):
        with pytest.raises(ValidationError):
            schemas.PnlAnomalyDeclined(
                type="pnl_anomaly_declined", ts=self.NOW,
                requester_role="owner", reason="random",
            )


class TestLogEntryDispatch:
    NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)
    adapter = TypeAdapter(schemas.LogEntry)

    def test_pnl_anomaly_detected_dispatches(self):
        raw = {
            "type": "pnl_anomaly_detected", "ts": self.NOW.isoformat(),
            "anomaly_type": "margin_drop", "target_id": "x",
            "delta_pct": -10.0, "baseline_value": 100, "current_value": 90,
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.PnlAnomalyDetected)

    def test_pnl_anomaly_declined_dispatches(self):
        raw = {
            "type": "pnl_anomaly_declined", "ts": self.NOW.isoformat(),
            "requester_role": "owner", "reason": "agent_disabled",
        }
        entry = self.adapter.validate_python(raw)
        assert isinstance(entry, schemas.PnlAnomalyDeclined)

    def test_both_roundtrip(self):
        for type_str, fields in [
            ("pnl_anomaly_detected", {
                "anomaly_type": "margin_drop", "target_id": "x",
                "delta_pct": -10.0, "baseline_value": 100, "current_value": 90,
            }),
            ("pnl_anomaly_declined", {
                "requester_role": "owner", "reason": "agent_disabled",
            }),
        ]:
            raw = {"type": type_str, "ts": self.NOW.isoformat(), **fields}
            entry = self.adapter.validate_python(raw)
            j = entry.model_dump_json()
            roundtrip = self.adapter.validate_python(json.loads(j))
            assert type(roundtrip) is type(entry)


class TestConfigWiring:
    def test_pnl_anomaly_in_config(self):
        # Config().pnl_anomaly must default to PnlAnomalyConfig with enabled=False
        from pydantic import BaseModel
        # Build a minimal config dict and validate
        cfg_dict = {
            "schema_version": 1,
            "customer": {"name": "x", "location_id": "y", "timezone": "America/New_York"},
            "owner": {"name": "x", "phone": "+10000000000", "self_chat_jid": "x@s.whatsapp.net"},
            "limits": {},
            "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
            "backup": {"gpg_recipient_email": "x@y"},
        }
        cfg = schemas.Config.model_validate(cfg_dict)
        assert cfg.pnl_anomaly.enabled is False
        assert cfg.pnl_anomaly.margin_drop_alert_pct == 8.0
