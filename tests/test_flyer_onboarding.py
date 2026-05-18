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
from agents.flyer.intake import handle_intake_message  # noqa: E402
from agents.flyer.onboarding import handle_onboarding_message, store_brand_asset  # noqa: E402
from schemas import FlyerCustomerStore, FlyerOnboardingSession, FlyerPlanTier, FlyerUsageEvent  # noqa: E402


def _trial_customer(*, customer_id: str, business_name: str, phone: str, now: datetime):
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name=business_name,
        business_address="90 Brybar Dr, St Johns, FL",
        public_phone=phone,
        business_whatsapp_number=phone,
        authorized_request_number=phone,
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
        primary_chat_id=f"{phone.replace('+', '')}@s.whatsapp.net",
        onboarded_by_phone=phone,
    )
    return customer.model_copy(update={
        "customer_id": customer_id,
        "status": "trial",
        "activated_at": now,
        "plan_started_at": now,
        "current_period_start": now,
        "current_period_end": now.replace(month=now.month + 1),
    })


def test_flyer_plan_tiers_are_data_driven_defaults():
    tiers = FlyerPlanTier.default_tiers()
    assert [(t.plan_id, t.monthly_price_usd, t.included_flyers) for t in tiers] == [
        ("trial", 0.00, 3),
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
    assert "$199.00 - unlimited flyers/month (Unlimited)" in first.reply_text
    assert "designer" in first.reply_text.lower()
    assert "manual edit" in first.reply_text.lower()

    flow = [
        ("m2", "Triveni Pineville", "collecting_business_address", "business address"),
        ("m3", "300 S Polk St, Pineville, NC 28134", "collecting_public_phone", "public business phone"),
        ("m4", "(704) 324-3322", "collecting_business_whatsapp", "business WhatsApp number"),
        ("m5", "+1 704 324 3322", "collecting_authorized_request_number", "authorized flyer request number"),
        ("m6", "+1 904 555 0104", "collecting_business_profile", "business type"),
        ("m7", "Indian grocery and food court, English", "choosing_plan", "choose a plan"),
        ("m8", "3", "confirming_summary", "confirm"),
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


def test_free_trial_onboarding_skips_paid_plan_choice_and_activates_trial(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 16, tzinfo=timezone.utc)

    first = handle_onboarding_message(
        state_path=state_path,
        chat_id="19045550155@s.whatsapp.net",
        sender_phone="+19045550155",
        message_id="trial-1",
        text="START FREE TRIAL - I want to try Flyer Studio",
        now=now,
    )
    assert first.handled is True
    assert first.next_status == "collecting_business_name"
    assert "3 free sample flyers" in first.reply_text
    assert "let's create a beautiful flyer for your business" in first.reply_text.lower()
    assert "set up your free trial first" in first.reply_text.lower()

    flow = [
        ("trial-2", "Lakshmi Kitchen", "collecting_business_address", "business address"),
        ("trial-3", "123 Main St, Pineville, NC", "collecting_public_phone", "public business phone"),
        ("trial-4", "+1 704 555 0199", "collecting_business_whatsapp", "business WhatsApp number"),
        ("trial-5", "+1 704 555 0199", "collecting_authorized_request_number", "authorized flyer request number"),
        ("trial-6", "+1 904 555 0188", "collecting_business_profile", "business type"),
        ("trial-7", "Indian restaurant, English", "confirming_summary", "confirm"),
        ("trial-8", "CONFIRM", "trial", "3 free sample flyers"),
    ]
    for message_id, text, expected_status, expected_reply in flow:
        result = handle_onboarding_message(
            state_path=state_path,
            chat_id="19045550155@s.whatsapp.net",
            sender_phone="+19045550155",
            message_id=message_id,
            text=text,
            now=now,
        )
        assert result.handled is True
        assert result.next_status == expected_status
        assert expected_reply.lower() in result.reply_text.lower()

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    customer = store.customers[0]
    assert customer.plan_id == "trial"
    assert customer.status == "trial"
    assert customer.quota_remaining(FlyerPlanTier.default_tiers()) == 3


def test_compound_confirm_finishes_trial_onboarding(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    chat_id = "17329837841@s.whatsapp.net"

    first = handle_onboarding_message(
        state_path=state_path,
        chat_id=chat_id,
        sender_phone="+17329837841",
        message_id="trial-start",
        text="Start Free Trial",
        now=now,
    )
    assert first.next_status == "collecting_business_name"

    for message_id, text in [
        ("trial-1", "Lakshmis Kitchn"),
        ("trial-2", "90 Brybar Dr St Johns FL"),
        ("trial-3", "7329837841"),
        ("trial-4", "7329837841"),
        ("trial-5", "7329837841"),
        ("trial-6", "Indian Restaurant, Telugu"),
    ]:
        handle_onboarding_message(
            state_path=state_path,
            chat_id=chat_id,
            sender_phone="+17329837841",
            message_id=message_id,
            text=text,
            now=now,
        )

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id=chat_id,
        sender_phone="+17329837841",
        message_id="trial-confirm",
        text=(
            "CONFIRM. Create a breakfast menu for tomorrow from 8 AM to 10 AM. "
            "Items to include in the flyer Idli - $4.99."
        ),
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "trial"
    assert "Free trial active" in result.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.customers[0].status == "trial"


def test_duplicate_confirm_for_same_sender_recovers_existing_trial_account(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    existing_customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmis Kitchn",
        phone="+17329837841",
        now=now,
    )
    store = FlyerCustomerStore(
        next_customer_sequence=2,
        customers=[existing_customer],
        onboarding_sessions=[FlyerOnboardingSession(
            chat_id="17329837841@s.whatsapp.net",
            sender_phone="+17329837841",
            status="confirming_summary",
            started_at=now,
            updated_at=now,
            last_message_id="summary",
            business_name="Lakshmi's Kitchen",
            business_address="90 Brybar FL",
            public_phone="+17329837841",
            business_whatsapp_number="+17329837841",
            authorized_request_number="+17329837841",
            business_category="English and Telugu",
            preferred_language="te",
            plan_id="trial",
        )],
    )
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="confirm-again",
        text="CONFIRM",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "trial"
    assert result.customer_id == "CUST0001"
    assert "already set up" in result.reply_text.lower()
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert len(updated.customers) == 1
    assert updated.customers[0].customer_id == "CUST0001"
    assert updated.onboarding_sessions == []


def test_duplicate_confirm_for_same_business_connects_new_sender_and_clears_session(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    existing_customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmis Kitchn",
        phone="+17329837841",
        now=now,
    )
    store = FlyerCustomerStore(
        next_customer_sequence=2,
        customers=[existing_customer],
        onboarding_sessions=[FlyerOnboardingSession(
            chat_id="201975216009469@lid",
            sender_phone="+19045550104",
            status="confirming_summary",
            started_at=now,
            updated_at=now,
            last_message_id="summary",
            business_name="Lakshmis Kitchen",
            business_address="90 Brybar Dr",
            public_phone="+17329837841",
            business_whatsapp_number="+17329837841",
            authorized_request_number="+17329837841",
            business_category="English and Telugu",
            preferred_language="te",
            plan_id="trial",
        )],
    )
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        message_id="confirm-conflict",
        text="CONFIRM",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "trial"
    assert result.customer_id == "CUST0001"
    assert "already set up" in result.reply_text.lower()
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert len(updated.customers) == 1
    assert "+19045550104" in [str(phone) for phone in updated.customers[0].authorized_request_numbers]
    assert updated.onboarding_sessions == []


def test_registered_trial_customer_with_stale_session_can_start_flyer_and_clears_session(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    existing_customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    )
    store = FlyerCustomerStore(
        next_customer_sequence=2,
        customers=[existing_customer],
        onboarding_sessions=[FlyerOnboardingSession(
            chat_id="17329837841@s.whatsapp.net",
            sender_phone="+17329837841",
            status="confirming_summary",
            started_at=now,
            updated_at=now,
            last_message_id="summary",
            business_name="Lakshmi's Kitchen",
            business_address="90 Brybar FL",
            public_phone="+17329837841",
            business_whatsapp_number="+17329837841",
            authorized_request_number="+17329837841",
            business_category="restaurant",
            preferred_language="en",
            plan_id="trial",
        )],
    )
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="flyer-after-stale-session",
        text="Create a breakfast menu flyer for tomorrow from 8 AM to 10 AM",
        now=now,
    )

    assert result.handled is False
    assert result.next_status == "trial"
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.onboarding_sessions == []


def test_duplicate_confirm_for_same_sender_recovers_existing_payment_pending_account(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr, St Johns, FL",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
        now=now,
        payment_checkout_url="https://pay.example/CUST0001",
        primary_chat_id="17329837841@s.whatsapp.net",
        onboarded_by_phone="+17329837841",
    )
    store.customers.append(customer)
    store.next_customer_sequence = 2
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar FL",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="restaurant",
        preferred_language="en",
        plan_id="starter",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="confirm-again",
        text="CONFIRM",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "payment_pending"
    assert result.customer_id == "CUST0001"
    assert "Registration saved as CUST0001" in result.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert len(updated.customers) == 1
    assert updated.onboarding_sessions == []


def test_duplicate_confirm_for_different_customer_stays_blocked(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    existing_customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Other Business",
        phone="+17329837841",
        now=now,
    )
    store = FlyerCustomerStore(
        next_customer_sequence=2,
        customers=[existing_customer],
        onboarding_sessions=[FlyerOnboardingSession(
            chat_id="19045550199@s.whatsapp.net",
            sender_phone="+19045550199",
            status="confirming_summary",
            started_at=now,
            updated_at=now,
            last_message_id="summary",
            business_name="New Business",
            business_address="100 Main St",
            public_phone="+19045550199",
            business_whatsapp_number="+17329837841",
            authorized_request_number="+19045550199",
            business_category="Retail",
            preferred_language="en",
            plan_id="trial",
        )],
    )
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="19045550199@s.whatsapp.net",
        sender_phone="+19045550199",
        message_id="confirm-conflict",
        text="CONFIRM",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "confirming_summary"
    assert "belongs to another Flyer Studio account" in result.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert len(updated.customers) == 1
    assert len(updated.onboarding_sessions) == 1


def test_reply_button_trial_phrase_starts_free_trial_onboarding(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 16, tzinfo=timezone.utc)

    first = handle_onboarding_message(
        state_path=state_path,
        chat_id="19045550156@s.whatsapp.net",
        sender_phone="+19045550156",
        message_id="trial-button-1",
        text="Help me create a beautiful flyer for my business",
        now=now,
    )

    assert first.handled is True
    assert first.next_status == "collecting_business_name"
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.onboarding_sessions[0].plan_id == "trial"


def test_campaign_cta_restarts_stale_partial_onboarding_instead_of_parsing_as_phone(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        status="collecting_public_phone",
        started_at=now,
        updated_at=now,
        last_message_id="old",
        business_name="Old Name",
        business_address="Old Address",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        message_id="trial-button-retry",
        text="Help me create a beautiful flyer for my business",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "collecting_business_name"
    assert "set up your free trial first" in result.reply_text.lower()
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.onboarding_sessions[0].status == "collecting_business_name"
    assert updated.onboarding_sessions[0].plan_id == "trial"
    assert updated.onboarding_sessions[0].business_name == ""
    assert updated.onboarding_sessions[0].business_address == ""


def test_act_now_campaign_cta_restarts_stale_partial_onboarding_without_trial_plan(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        status="collecting_public_phone",
        started_at=now,
        updated_at=now,
        last_message_id="old",
        plan_id="trial",
        business_name="Old Name",
        business_address="Old Address",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        message_id="act-now-retry",
        text="I want to set up Flyer Studio for my business",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "collecting_business_name"
    assert "what is your business name" in result.reply_text.lower()
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.onboarding_sessions[0].status == "collecting_business_name"
    assert updated.onboarding_sessions[0].plan_id == ""
    assert updated.onboarding_sessions[0].business_name == ""
    assert updated.onboarding_sessions[0].business_address == ""


def test_invalid_onboarding_field_reply_returns_prompt_instead_of_crashing(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        status="collecting_business_name",
        started_at=now,
        updated_at=now,
        last_message_id="trial-start",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="201975216009469@lid",
        sender_phone="+19045550104",
        message_id="bad-name",
        text="1",
        now=now,
    )

    assert result.handled is True
    assert result.next_status == "collecting_business_name"
    assert "please send the business name" in result.reply_text.lower()
    assert "what is your business name" in result.reply_text.lower()


def test_trial_quota_blocks_fourth_flyer_and_prompts_upgrade(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 16, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Lakshmi Kitchen",
        business_address="123 Main St",
        public_phone="+17045550199",
        business_whatsapp_number="+17045550199",
        authorized_request_number="+19045550188",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
    ).model_copy(update={
        "status": "trial",
        "current_period_start": now,
        "current_period_end": now.replace(month=6),
        "usage_events": [
            FlyerUsageEvent(
                reservation_id=f"CUST0001:F000{i}",
                project_id=f"F000{i}",
                customer_id="CUST0001",
                kind="used",
                recorded_at=now,
                message_id=f"m{i}",
            )
            for i in range(1, 4)
        ],
    })
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = reserve_quota(
        state_path=state_path,
        customer_phone="+19045550188",
        project_id="F0004",
        message_id="m4",
        now=now,
    )

    assert result.handled is True
    assert result.quota_allowed is False
    assert "free trial" in result.reply_text.lower()
    assert "upgrade" in result.reply_text.lower()


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
        ("m8", "2"),
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
        ("m8", "2"),
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
        ("m8", "2"),
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


def test_intake_language_and_text_mode_for_existing_customer(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Lakshmi's Kitchen",
        business_address="90 Brybar Dr",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
        primary_chat_id="17329837841@s.whatsapp.net",
        onboarded_by_phone="+17329837841",
    ).model_copy(update={"status": "trial"})
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(), encoding="utf-8")

    start = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="i1",
        text="Start Free Trial",
        start_source="start_trial",
        now=now,
    )
    assert start.action == "choose_language"
    assert "Malayalam" in start.reply_text
    lang = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="i2",
        text="Tamil",
        now=now,
    )
    assert lang.action == "choose_mode"
    assert "Tamil" in lang.reply_text
    ready = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="i3",
        text="2",
        now=now,
    )
    assert ready.action == "text_ready"
    assert "existing flyer" in ready.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.customers[0].preferred_language == "ta"
    assert updated.intake_sessions == []


def test_guided_intake_synthesizes_flyer_request(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Triveni",
        business_address="Pineville",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+17043243322",
        business_category="grocery",
        preferred_language="en",
        plan_id="trial",
        now=now,
    ).model_copy(update={"status": "trial"}))
    state_path.write_text(store.model_dump_json(), encoding="utf-8")

    chat_id = "17043243322@s.whatsapp.net"
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g1", text="Create flyer", start_source="new_flyer", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g2", text="English", now=now)
    first_question = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g3", text="1", now=now)
    assert first_question.action == "guided_question"
    assert "what are you promoting" in first_question.reply_text.lower()
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g4", text="Weekend breakfast specials", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g5", text="Saturday and Sunday 8 AM to 11 AM", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g6", text="Idli $4.99, Dosa $8.99", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g7", text="Use saved", now=now)
    done = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g8", text="Use logo, festive style", now=now)
    assert done.action == "create_project"
    assert "Weekend breakfast specials" in done.raw_request
    assert "Idli $4.99" in done.raw_request
    assert "Preferred flyer language: English" in done.raw_request


def test_guided_intake_preserves_attached_sample_for_project_creation(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.customers.append(store.new_customer(
        business_name="Lakshmis Kitchen",
        business_address="90 Brybar Dr St Johns FL",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="grocery",
        preferred_language="en",
        plan_id="trial",
        now=now,
    ).model_copy(update={"status": "trial"}))
    state_path.write_text(store.model_dump_json(), encoding="utf-8")

    chat_id = "17329837841@s.whatsapp.net"
    sample_path = "/opt/shift-agent/.hermes/image_cache/img_sample_di lives here.jpg"
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m1", text="Create flyer", start_source="new_flyer", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m2", text="Telugu", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m3", text="1", now=now)
    handle_intake_message(
        state_path=state_path,
        chat_id=chat_id,
        sender_phone="+17329837841",
        message_id="m4",
        text="Diwali Grocery Sale. Use items in this flyer and create one for Lakshmis Kitchen",
        media_path=sample_path,
        now=now,
    )
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m5", text="May 22 to May 25", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m6", text="Extract items and prices from the sample flyer attached", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m7", text="90 Brybar Saint Johns FL", now=now)
    done = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m8", text="SKIP", now=now)

    assert done.action == "create_project"
    assert done.reference_media_path == sample_path
    assert "Preferred flyer language: Telugu" in done.raw_request
    assert "Attached reference/sample flyer is available" in done.raw_request


def test_start_trial_intake_hands_off_to_onboarding_with_language_and_mode(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 17, tzinfo=timezone.utc)
    chat_id = "19045550155@s.whatsapp.net"
    handle_intake_message(
        state_path=state_path,
        chat_id=chat_id,
        sender_phone="+19045550155",
        message_id="s1",
        text="Start Free Trial",
        start_source="start_trial",
        now=now,
    )
    handle_intake_message(
        state_path=state_path,
        chat_id=chat_id,
        sender_phone="+19045550155",
        message_id="s2",
        text="Malayalam",
        now=now,
    )
    handoff = handle_intake_message(
        state_path=state_path,
        chat_id=chat_id,
        sender_phone="+19045550155",
        message_id="s3",
        text="1",
        now=now,
    )
    assert handoff.action == "onboarding_started"
    assert "business name" in handoff.reply_text.lower()
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.onboarding_sessions[0].preferred_language == "ml"
    assert store.onboarding_sessions[0].creation_mode == "guided"
    assert store.onboarding_sessions[0].plan_id == "trial"
