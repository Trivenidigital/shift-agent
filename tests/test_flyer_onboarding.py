"""WhatsApp-native onboarding contracts for Hermes Flyer Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.account import activate_customer, handle_account_command, reserve_quota, finalize_usage  # noqa: E402
from agents.flyer.onboarding import handle_onboarding_message, store_brand_asset  # noqa: E402
from schemas import FlyerCustomerStore, FlyerOnboardingSession, FlyerPlanTier, FlyerUsageEvent  # noqa: E402


def test_flyer_plan_tiers_are_data_driven_defaults():
    tiers = FlyerPlanTier.default_tiers()
    assert [(t.plan_id, t.monthly_price_usd, t.included_flyers) for t in tiers] == [
        ("starter", 49.99, 30),
        ("growth", 69.99, 60),
        ("unlimited", 199.00, None),
    ]
    assert all(t.currency == "USD" for t in tiers)


def test_onboarding_collects_required_business_and_plan_fields(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)

    first = handle_onboarding_message(
        state_path=state_path,
        chat_id="19045550123@s.whatsapp.net",
        sender_phone="+19045550123",
        message_id="m1",
        text="Hi",
        now=now,
    )
    assert first.handled is True
    assert first.next_status == "collecting_business_name"
    assert "$49.99" in first.reply_text

    flow = [
        ("m2", "Triveni Pineville", "collecting_business_address", "business address"),
        ("m3", "300 S Polk St, Pineville, NC 28134", "collecting_public_phone", "public business phone"),
        ("m4", "(704) 324-3322", "collecting_business_whatsapp", "business WhatsApp number"),
        ("m5", "+1 704 324 3322", "collecting_authorized_request_number", "authorized flyer request number"),
        ("m6", "+1 904 555 0104", "collecting_business_profile", "business type"),
        ("m7", "Indian grocery and food court, English", "choosing_plan", "choose a plan"),
        ("m8", "2", "confirming_summary", "confirm"),
        ("m9", "CONFIRM", "payment_pending", "payment"),
    ]
    for message_id, text, expected_status, expected_reply in flow:
        result = handle_onboarding_message(
            state_path=state_path,
            chat_id="19045550123@s.whatsapp.net",
            sender_phone="+19045550123",
            message_id=message_id,
            text=text,
            now=now,
        )
        assert result.handled is True
        assert result.next_status == expected_status
        assert expected_reply.lower() in result.reply_text.lower()

    store = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    customer = store.customers[0]
    assert customer.customer_id == "CUST0001"
    assert customer.business_name == "Triveni Pineville"
    assert customer.business_whatsapp_number == "+17043243322"
    assert customer.authorized_request_numbers == ["+19045550104"]
    assert customer.plan_id == "growth"
    assert customer.status == "payment_pending"
    assert customer.primary_chat_id == "19045550123@s.whatsapp.net"
    assert customer.onboarded_by_phone == "+19045550123"


def test_registered_authorized_number_does_not_restart_onboarding(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    ))
    store.customers[0] = store.customers[0].model_copy(update={"status": "active"})
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="19045550104@s.whatsapp.net",
        sender_phone="+19045550104",
        message_id="m9",
        text="Need flyer for Holi",
        now=now,
    )
    assert result.handled is False


def test_brand_assets_uploaded_during_onboarding_transfer_to_customer(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    media_path = tmp_path / "logo.png"
    media_path.write_bytes(b"fake logo bytes")
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)

    asset_result = store_brand_asset(
        state_path=state_path,
        chat_id="19045550123@s.whatsapp.net",
        sender_phone="+19045550123",
        message_id="logo1",
        media_path=media_path,
        text="new logo",
        now=now,
    )
    assert asset_result.handled is True
    assert "logo saved" in asset_result.reply_text.lower()

    for message_id, text in [
        ("m2", "Triveni Pineville"),
        ("m3", "300 S Polk St, Pineville, NC 28134"),
        ("m4", "(704) 324-3322"),
        ("m5", "+1 704 324 3322"),
        ("m6", "+1 904 555 0104"),
        ("m7", "Indian grocery and food court, English"),
        ("m8", "1"),
        ("m9", "CONFIRM"),
    ]:
        handle_onboarding_message(
            state_path=state_path,
            chat_id="19045550123@s.whatsapp.net",
            sender_phone="+19045550123",
            message_id=message_id,
            text=text,
            now=now,
        )

    store = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    customer = store.customers[0]
    assert len(customer.brand_assets) == 1
    assert customer.brand_assets[0].kind == "logo"
    assert customer.brand_assets[0].active is True
    assert Path(customer.brand_assets[0].path).exists()


def test_registered_customer_can_replace_logo_any_time(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    ).model_copy(update={"status": "active"}))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    first_logo = tmp_path / "logo1.png"
    first_logo.write_bytes(b"first")
    second_logo = tmp_path / "logo2.png"
    second_logo.write_bytes(b"second")

    store_brand_asset(
        state_path=state_path,
        chat_id="17043243322@s.whatsapp.net",
        sender_phone="+17043243322",
        message_id="logo1",
        media_path=first_logo,
        text="logo",
        now=now,
    )
    store_brand_asset(
        state_path=state_path,
        chat_id="17043243322@s.whatsapp.net",
        sender_phone="+17043243322",
        message_id="logo2",
        media_path=second_logo,
        text="replace logo",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    logo_assets = [asset for asset in updated.customers[0].brand_assets if asset.kind == "logo"]
    assert len(logo_assets) == 2
    assert [asset.active for asset in logo_assets] == [False, True]
    assert Path(logo_assets[-1].path).read_bytes() == b"second"


def test_non_admin_cannot_replace_saved_brand_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    ).model_copy(update={"status": "active"}))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"logo")

    denied = store_brand_asset(
        state_path=state_path,
        chat_id="19045550104@s.whatsapp.net",
        sender_phone="+19045550104",
        message_id="logo1",
        media_path=logo,
        text="replace logo",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    assert denied.next_status == "brand_asset_admin_required"
    assert updated.customers[0].brand_assets == []


def test_menu_or_price_image_upload_is_classified_as_template(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    ).model_copy(update={"status": "active"}))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    media_path = tmp_path / "dosa.png"
    media_path.write_bytes(b"dosa menu")

    store_brand_asset(
        state_path=state_path,
        chat_id="17043243322@s.whatsapp.net",
        sender_phone="+17043243322",
        message_id="template1",
        media_path=media_path,
        text="change non-veg combo price from $14.99 to $16.99",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    assert updated.customers[0].brand_assets[0].kind == "template"


def test_payment_pending_signup_thread_can_replace_logo_after_plan_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    first_logo = tmp_path / "logo1.png"
    first_logo.write_bytes(b"first")
    second_logo = tmp_path / "logo2.png"
    second_logo.write_bytes(b"second")

    store_brand_asset(
        state_path=state_path,
        chat_id="19995550999@s.whatsapp.net",
        sender_phone="+19995550999",
        message_id="logo1",
        media_path=first_logo,
        text="logo",
        now=now,
    )
    for message_id, text in [
        ("m2", "Smoke Cafe"),
        ("m3", "123 Main St"),
        ("m4", "7043243322"),
        ("m5", "7043243322"),
        ("m6", "9045550104"),
        ("m7", "restaurant, English"),
        ("m8", "1"),
        ("m9", "CONFIRM"),
    ]:
        handle_onboarding_message(
            state_path=state_path,
            chat_id="19995550999@s.whatsapp.net",
            sender_phone="+19995550999",
            message_id=message_id,
            text=text,
            now=now,
        )
    store_brand_asset(
        state_path=state_path,
        chat_id="19995550999@s.whatsapp.net",
        sender_phone="+19995550999",
        message_id="logo2",
        media_path=second_logo,
        text="replace logo",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    logo_assets = updated.customers[0].brand_assets
    assert len(logo_assets) == 2
    assert [asset.active for asset in logo_assets] == [False, True]
    assert Path(logo_assets[-1].path).read_bytes() == b"second"


def test_onboarding_script_returns_reply_json(tmp_path):
    script = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "handle-flyer-onboarding"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--chat-id",
            "19045550123@s.whatsapp.net",
            "--sender-phone",
            "+19045550123",
            "--message-id",
            "m1",
            "--text",
            "hello",
            "--state-path",
            str(tmp_path / "customers.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["handled"] is True
    assert doc["next_status"] == "collecting_business_name"
    assert "Flyer Studio" in doc["reply_text"]


def test_confirmation_summary_supports_direct_edits(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    for message_id, text in [
        ("m1", "hello"),
        ("m2", "Old Name"),
        ("m3", "123 Main St"),
        ("m4", "7043243322"),
        ("m5", "7043243322"),
        ("m6", "9045550104"),
        ("m7", "restaurant, English"),
        ("m8", "1"),
        ("m9", "EDIT NAME: New Name"),
        ("m10", "CONFIRM"),
    ]:
        result = handle_onboarding_message(
            state_path=state_path,
            chat_id="19995550000@s.whatsapp.net",
            sender_phone="+19995550000",
            message_id=message_id,
            text=text,
            now=now,
        )
    assert result.next_status == "payment_pending"
    store = FlyerCustomerStore.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
    assert store.customers[0].business_name == "New Name"


def test_account_activation_is_idempotent_and_reference_unique(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    c1 = store.new_customer(
        business_name="A",
        business_address="1 Main",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    )
    c2 = store.new_customer(
        business_name="B",
        business_address="2 Main",
        public_phone="+17043243323",
        business_whatsapp_number="+17043243323",
        authorized_request_number="+19045550105",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    )
    store.customers.extend([c1, c2])
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    first = activate_customer(
        state_path=state_path,
        customer_id="CUST0001",
        provider="stripe",
        payment_reference="pi_1",
        expected_plan="starter",
        amount_cents=4999,
        currency="USD",
        now=now,
    )
    assert first.ok is True
    replay = activate_customer(
        state_path=state_path,
        customer_id="CUST0001",
        provider="stripe",
        payment_reference="pi_1",
        expected_plan="starter",
        amount_cents=4999,
        currency="USD",
        now=now,
    )
    assert replay.ok is True
    duplicate = activate_customer(
        state_path=state_path,
        customer_id="CUST0002",
        provider="stripe",
        payment_reference="pi_1",
        expected_plan="starter",
        amount_cents=4999,
        currency="USD",
        now=now,
    )
    assert duplicate.ok is False
    assert duplicate.detail == "payment_reference_already_used"


def test_non_admin_cannot_mutate_account_but_can_status(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
        onboarded_by_phone="+17043243322",
    ).model_copy(update={"status": "active"})
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    status = handle_account_command(
        state_path=state_path,
        sender_phone="+19045550104",
        sender_role="unknown",
        chat_id="19045550104@s.whatsapp.net",
        text="STATUS",
        now=now,
    )
    assert status.ok is True
    assert "Plan: starter" in status.reply_text
    denied = handle_account_command(
        state_path=state_path,
        sender_phone="+19045550104",
        sender_role="unknown",
        chat_id="19045550104@s.whatsapp.net",
        text="ADD AUTHORIZED NUMBER +19045550199",
        now=now,
    )
    assert denied.ok is True
    assert "Only the business WhatsApp" in denied.reply_text


def test_quota_counts_latest_reservation_state_once(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 15, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
    ).model_copy(update={
        "status": "active",
        "current_period_start": now,
        "current_period_end": datetime(2026, 6, 15, tzinfo=timezone.utc),
        "usage_events": [
            FlyerUsageEvent(reservation_id="CUST0001:F0001", project_id="F0001", customer_id="CUST0001", kind="reserved", recorded_at=now, message_id="m1"),
            FlyerUsageEvent(reservation_id="CUST0001:F0001", project_id="F0001", customer_id="CUST0001", kind="used", recorded_at=now, message_id="m1"),
        ],
    })
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8")).customers[0]
    assert updated.usage_count_for_current_period() == 1
    reservation = reserve_quota(
        state_path=state_path,
        customer_phone="+19045550104",
        project_id="F0002",
        message_id="m2",
        now=now,
    )
    assert reservation.ok is True
    assert reservation.quota_allowed is True
    finalized = finalize_usage(
        state_path=state_path,
        customer_phone="+19045550104",
        project_id="F0002",
        message_id="m2",
        now=now,
    )
    assert finalized.ok is True
    final_store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert final_store.customers[0].usage_count_for_current_period() == 2


def test_onboarding_session_requires_valid_status():
    with pytest.raises(ValueError):
        FlyerOnboardingSession(
            chat_id="19045550123@s.whatsapp.net",
            sender_phone="+19045550123",
            status="unknown",
            started_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
