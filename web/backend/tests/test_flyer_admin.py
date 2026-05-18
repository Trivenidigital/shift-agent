from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest


def _customer(
    customer_id: str,
    *,
    business_name: str = "Lakshmis Kitchen",
    phone: str = "+17329837841",
    plan_id: str = "trial",
    status: str = "trial",
    usage_events: list[dict] | None = None,
) -> dict:
    now = datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat()
    return {
        "customer_id": customer_id,
        "business_name": business_name,
        "business_address": "90 Brybar Dr",
        "primary_chat_id": f"{phone.replace('+', '')}@s.whatsapp.net",
        "onboarded_by_phone": phone,
        "public_phone": phone,
        "business_whatsapp_number": phone,
        "authorized_request_numbers": [phone],
        "business_category": "Restaurant",
        "preferred_language": "en",
        "plan_id": plan_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "activated_at": now if status in {"trial", "active"} else None,
        "plan_started_at": now,
        "current_period_start": now,
        "usage_events": usage_events or [],
    }


def _write_json(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_flyer_summary_segments_customers_and_guest_orders(tmp_path, monkeypatch):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"

    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 4,
            "customers": [
                _customer("CUST0001", plan_id="trial", status="trial"),
                _customer("CUST0002", phone="+18479155253", plan_id="starter", status="active"),
                _customer("CUST0003", phone="+18479610344", plan_id="growth", status="payment_pending"),
            ],
        },
    )
    _write_json(
        settings.state_dir / "flyer" / "guest_orders.json",
        {
            "schema_version": 1,
            "orders": [
                {
                    "order_id": "GUEST0001",
                    "chat_id": "19802005022@s.whatsapp.net",
                    "sender_phone": "+19802005022",
                    "status": "paid",
                    "flyer_count_purchased": 1,
                    "flyer_count_used": 0,
                    "unit_price_cents": 400,
                    "currency": "USD",
                    "original_message_id": "MSG1",
                    "created_at": "2026-05-18T00:00:00Z",
                    "updated_at": "2026-05-18T00:00:00Z",
                }
            ],
        },
    )

    summary = flyer.build_summary()

    assert summary["segments"]["free_trial"] == 1
    assert summary["segments"]["paid"] == 1
    assert summary["segments"]["payment_pending"] == 1
    assert summary["segments"]["one_time"] == 1


def test_extend_trial_increases_trial_quota_limit(tmp_path):
    from app.routers import flyer
    from schemas import FlyerConfig

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001")],
        },
    )

    result = flyer.extend_trial_quota("CUST0001", extra_flyers=2, reason="manual goodwill")

    store = flyer.load_customer_store()
    customer = store.find_customer_by_id("CUST0001")
    assert result["trial_bonus_flyers"] == 2
    assert customer is not None
    assert customer.trial_bonus_flyers == 2
    assert customer.quota_remaining(FlyerConfig().plan_tiers) == 5
    assert list((settings.state_dir / "flyer").glob("customers.json.pre-admin-*"))


def test_reset_trial_quota_releases_counted_usage(tmp_path):
    from app.routers import flyer
    from schemas import FlyerConfig

    now = "2026-05-18T00:00:00Z"
    events = [
        {
            "reservation_id": "res-F0010",
            "project_id": "F0010",
            "customer_id": "CUST0001",
            "kind": "used",
            "count": 1,
            "recorded_at": now,
            "message_id": "MSG1",
        },
        {
            "reservation_id": "res-F0011",
            "project_id": "F0011",
            "customer_id": "CUST0001",
            "kind": "reserved",
            "count": 1,
            "recorded_at": now,
            "message_id": "MSG2",
        },
    ]
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001", usage_events=events)],
        },
    )

    result = flyer.reset_trial_quota("CUST0001", reason="testing reset")

    store = flyer.load_customer_store()
    customer = store.find_customer_by_id("CUST0001")
    assert result["released"] == 2
    assert customer is not None
    assert customer.usage_count_for_current_period() == 0
    assert customer.quota_remaining(FlyerConfig().plan_tiers) == 3


def test_campaign_target_parser_rejects_formula_and_dedupes():
    from app.routers.flyer import parse_campaign_targets

    parsed = parse_campaign_targets("+1 (732) 983-7841\n17329837841\n+1-847-915-5253")

    assert parsed["valid_targets"] == ["+17329837841", "+18479155253"]
    assert parsed["duplicate_count"] == 1

    with pytest.raises(ValueError, match="formula"):
        parse_campaign_targets("=cmd|/c calc.exe")


def test_campaign_send_dry_run_does_not_call_sender(monkeypatch):
    from app.routers import flyer

    calls: list[str] = []

    def fake_send(target: str, *, dry_run: bool) -> dict:
        calls.append(target)
        return {"ok": True, "target": target, "dry_run": dry_run}

    monkeypatch.setattr(flyer, "_send_campaign_target", fake_send)
    result = flyer.send_campaign_to_targets(
        ["+17329837841", "+18479155253"],
        dry_run=True,
        reason="preview",
    )

    assert calls == []
    assert result["sent"] == 0
    assert result["dry_run"] is True
    assert len(result["targets"]) == 2


def test_campaign_send_real_run_calls_sender(monkeypatch):
    from app.routers import flyer

    calls: list[tuple[str, bool]] = []

    def fake_send(target: str, *, dry_run: bool) -> dict:
        calls.append((target, dry_run))
        return {"ok": True, "target": target, "dry_run": dry_run}

    monkeypatch.setattr(flyer, "_send_campaign_target", fake_send)
    result = flyer.send_campaign_to_targets(
        ["+17329837841", "17329837841"],
        dry_run=False,
        reason="operator dashboard action",
    )

    assert calls == [("+17329837841", False)]
    assert result["sent"] == 1
    assert result["failed"] == 0
    assert result["dry_run"] is False


def test_campaign_sender_uses_allowlisted_cli_wrapper(monkeypatch):
    from app.routers import flyer

    calls = []

    class Result:
        returncode = 0
        stdout = '{"ok": true, "cta_message_id": "msg-1"}'
        stderr = ""

    def fake_run_cli(binary: str, args: list[str], *, timeout: float):
        calls.append((binary, args, timeout))
        return Result()

    monkeypatch.setattr(flyer, "run_cli", fake_run_cli)

    result = flyer._send_campaign_target("+17329837841", dry_run=False)

    assert result["ok"] is True
    assert calls == [
        (
            "/usr/local/bin/send-flyer-campaign",
            ["--jid", "17329837841@s.whatsapp.net"],
            60,
        )
    ]
