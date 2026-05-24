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

from agents.flyer.account import _phone_or_none, activate_customer, handle_account_command, reserve_quota, finalize_usage  # noqa: E402
from agents.flyer.intake import handle_intake_message  # noqa: E402
from agents.flyer.onboarding import handle_onboarding_message, store_brand_asset  # noqa: E402
from schemas import FlyerCustomerStore, FlyerIntakeSession, FlyerOnboardingSession, FlyerPlanTier, FlyerUsageEvent  # noqa: E402


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


def test_customer_store_default_keeps_starter_prompts_auto():
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(customer_id="CUST0001", business_name="Demo Salon", phone="+17329837841", now=now)
    store = FlyerCustomerStore(customers=[customer])

    assert store.starter_prompt_mode(customer.customer_id) == "auto"
    assert store.claim_starter_prompt_send(customer.customer_id) is True


def test_customer_store_claim_allows_only_one_auto_starter_prompt():
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(customer_id="CUST0001", business_name="Demo Salon", phone="+17329837841", now=now)
    store = FlyerCustomerStore(customers=[customer])

    assert store.claim_starter_prompt_send(customer.customer_id) is True
    assert store.claim_starter_prompt_send(customer.customer_id) is False


def test_customer_store_preference_top_level_is_rollback_safe():
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(customer_id="CUST0001", business_name="Demo Salon", phone="+17329837841", now=now)
    raw = json.loads(FlyerCustomerStore(customers=[customer]).model_dump_json())
    raw["starter_prompt_preferences"] = {"CUST0001": "off"}
    raw["starter_prompt_sent_counts"] = {"CUST0001": 1}

    store = FlyerCustomerStore.model_validate(raw)

    assert store.starter_prompt_mode("CUST0001") == "off"


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


def test_language_menu_pins_deployed_order_at_positions_4_through_6():
    """BUG-FLYER-QA-2026-05-19-002: pin the deployed menu order so a future
    reorder is caught at PR time, not at QA time. Workbook FS-A2-012 must
    agree with `parse_language_choice("5") == "ta"`. Pinning positions 4-6
    explicitly catches any adjacent-pair swap; a single-position pin would
    miss e.g. a 4↔5 swap."""
    from agents.flyer.intake import parse_language_choice, _language_prompt
    assert parse_language_choice("4") == "ml"
    assert parse_language_choice("5") == "ta"
    assert parse_language_choice("6") == "kn"
    prompt = _language_prompt()
    assert "4. Malayalam" in prompt
    assert "5. Tamil" in prompt
    assert "6. Kannada" in prompt


def test_trial_back_from_confirming_summary_skips_choosing_plan(tmp_path):
    """BUG-FLYER-QA-2026-05-19-001: trial sessions skip `choosing_plan` on
    the forward path. BACK from `confirming_summary` must mirror that skip,
    otherwise a trial user pressing BACK at the summary lands in the paid
    plan chooser and loses `plan_id="trial"`."""
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    chat_id = "19045550199@s.whatsapp.net"

    handle_onboarding_message(
        state_path=state_path, chat_id=chat_id, sender_phone="+19045550199",
        message_id="trial-1", text="Start Free Trial", now=now,
    )
    for message_id, text in [
        ("trial-2", "Coastal Crab Shack"),
        ("trial-3", "100 Ocean Blvd, Wilmington, NC"),
        ("trial-4", "+1 910 555 0177"),
        ("trial-5", "+1 910 555 0177"),
        ("trial-6", "+1 910 555 0177"),
        ("trial-7", "Seafood restaurant, English"),
    ]:
        handle_onboarding_message(
            state_path=state_path, chat_id=chat_id, sender_phone="+19045550199",
            message_id=message_id, text=text, now=now,
        )

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    session = next(s for s in store.onboarding_sessions if s.chat_id == chat_id)
    assert session.status == "confirming_summary"
    assert session.plan_id == "trial"

    result = handle_onboarding_message(
        state_path=state_path, chat_id=chat_id, sender_phone="+19045550199",
        message_id="trial-back", text="BACK", now=now,
    )
    assert result.handled is True
    assert result.next_status == "collecting_business_profile", (
        "trial BACK must skip choosing_plan; got " + result.next_status
    )

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    session = next(s for s in store.onboarding_sessions if s.chat_id == chat_id)
    assert session.plan_id == "trial", "trial plan_id must survive BACK from summary"
    assert session.status == "collecting_business_profile"
    assert session.business_category == ""


def test_paid_back_from_confirming_summary_returns_to_choosing_plan(tmp_path):
    """BUG-FLYER-QA-2026-05-19-001 regression guard: paid-plan BACK from
    `confirming_summary` still routes to `choosing_plan` and clears
    `plan_id`. The trial-aware branch must not break the paid path."""
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    chat_id = "19045550155@s.whatsapp.net"

    # "Hi" is the canonical paid-path opener; it does not match any trial
    # keyword (free trial / start trial / set up flyer studio / etc.).
    handle_onboarding_message(
        state_path=state_path, chat_id=chat_id, sender_phone="+19045550155",
        message_id="paid-1", text="Hi", now=now,
    )
    for message_id, text in [
        ("paid-2", "Lakshmi Kitchen"),
        ("paid-3", "123 Main St, Pineville, NC"),
        ("paid-4", "+1 704 555 0199"),
        ("paid-5", "+1 704 555 0199"),
        ("paid-6", "+1 904 555 0188"),
        ("paid-7", "Indian restaurant, English"),
        ("paid-8", "2"),  # plan 1 is "trial"; pick starter (plan 2) for paid path
    ]:
        handle_onboarding_message(
            state_path=state_path, chat_id=chat_id, sender_phone="+19045550155",
            message_id=message_id, text=text, now=now,
        )

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    session = next(s for s in store.onboarding_sessions if s.chat_id == chat_id)
    assert session.status == "confirming_summary"
    # Tighter than `!= "trial"`: position "2" must map to "starter". Catches
    # silent reordering of default_tiers() that would otherwise let the
    # test pass with a different paid plan.
    assert session.plan_id == "starter"

    result = handle_onboarding_message(
        state_path=state_path, chat_id=chat_id, sender_phone="+19045550155",
        message_id="paid-back", text="BACK", now=now,
    )
    assert result.handled is True
    assert result.next_status == "choosing_plan"

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    session = next(s for s in store.onboarding_sessions if s.chat_id == chat_id)
    assert session.status == "choosing_plan"
    assert session.plan_id == ""


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


@pytest.mark.parametrize("text", [
    "ok create flyer for weekend sale",
    "yes create flyer for weekend sale",
])
def test_alias_compound_confirm_finishes_trial_without_starter(tmp_path, text):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Demo Salon",
        business_address="90 Brybar Dr, St Johns, FL",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="salon",
        preferred_language="en",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="confirm-alias",
        text=text,
        now=now,
    )

    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert result.next_status == "trial"
    assert updated.customers[0].status == "trial"
    assert "Here is a starter flyer request" not in result.reply_text


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


def test_business_name_reply_at_address_step_stays_in_onboarding_repair_prompt(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="collecting_business_address",
        started_at=now,
        updated_at=now,
        last_message_id="business-name",
        business_name="Ram",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="address-repair",
        text="Chloe hair studio",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert result.handled is True
    assert result.next_status == "collecting_business_address"
    assert "please send the full business address" in result.reply_text.lower()
    assert "what is the business address" in result.reply_text.lower()
    assert updated.onboarding_sessions[0].business_address == ""


def test_phone_resolved_after_lid_only_start_finds_chat_bound_session(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="collecting_business_address",
        started_at=now,
        updated_at=now,
        last_message_id="business-name",
        business_name="Ram",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone="+19803826497",
        message_id="address",
        text="123 Main St, Charlotte, NC",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert result.next_status == "collecting_public_phone"
    assert updated.onboarding_sessions[0].business_address == "123 Main St, Charlotte, NC"


def test_onboarding_accepts_ok_proceed_as_confirmation(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Hisaku",
        business_address="101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
        public_phone="+918985741562",
        business_whatsapp_number="+918985741562",
        authorized_request_number="+918985741562",
        business_category="Digital Marketing",
        preferred_language="en",
        plan_id="trial",
        creation_mode="text",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="ok-proceed",
        text="Ok proceed",
        now=now,
    )

    assert result.next_status == "trial"
    assert "Text Mode is ready" in result.reply_text


def test_trial_completion_suggests_business_category_starter_brief(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Hisaku",
        business_address="101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
        public_phone="+918985741562",
        business_whatsapp_number="+918985741562",
        authorized_request_number="+918985741562",
        business_category="Digital Marketing",
        preferred_language="en",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="confirm",
        text="CONFIRM",
        now=now,
    )

    assert result.next_status == "trial"
    assert "Here is a starter flyer request" in result.reply_text
    assert "Grow Your Business with Modern Marketing" in result.reply_text
    assert "Reply with your edited version" in result.reply_text
    assert "don't show sample prompts" in result.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.claim_starter_prompt_send(result.customer_id) is False


def test_guided_trial_completion_does_not_append_full_starter_brief(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Hisaku",
        business_address="101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
        public_phone="+918985741562",
        business_whatsapp_number="+918985741562",
        authorized_request_number="+918985741562",
        business_category="Digital Marketing",
        preferred_language="en",
        plan_id="trial",
        creation_mode="guided",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="confirm-guided",
        text="CONFIRM",
        now=now,
    )

    assert result.next_status == "trial"
    assert "Guided Mode is ready" in result.reply_text
    assert "Here is a starter flyer request" not in result.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.intake_sessions[0].status == "guided_collecting_goal"
    assert updated.claim_starter_prompt_send(result.customer_id) is True


def test_sample_trial_completion_opens_compact_idea_picker(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="confirming_summary",
        started_at=now,
        updated_at=now,
        last_message_id="summary",
        business_name="Lakshmis Kitchn",
        business_address="90 Brybar Dr, St Johns, FL",
        public_phone="+17329837841",
        business_whatsapp_number="+17329837841",
        authorized_request_number="+17329837841",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        creation_mode="sample",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="confirm-sample",
        text="CONFIRM",
        now=now,
    )

    assert result.next_status == "trial"
    assert "Pick a sample idea" in result.reply_text
    assert "Reply 1 or 2" in result.reply_text
    assert "Here is a starter flyer request" not in result.reply_text
    assert result.reply_text.count("Flyer Studio") == 1
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.intake_sessions[0].status == "choosing_sample_idea"
    assert updated.intake_sessions[0].creation_mode == "sample"
    assert updated.claim_starter_prompt_send(result.customer_id) is False


def test_text_mode_ready_includes_category_starter_brief(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Spark Growth",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "digital marketing agency"})
    store = FlyerCustomerStore(
        next_customer_sequence=2,
        customers=[customer],
        intake_sessions=[],
    )
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    start = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="start",
        text="Create flyer",
        start_source="new_flyer",
        now=now,
    )
    assert start.action == "choose_language"
    language = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="language",
        text="English",
        now=now,
    )
    assert language.action == "choose_mode"
    result = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="mode",
        text="3",
        now=now,
    )

    assert result.action == "text_ready"
    assert "Here is a starter flyer request" in result.reply_text
    assert "Grow Your Business with Modern Marketing" in result.reply_text
    assert "don't show sample prompts" in result.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.claim_starter_prompt_send(customer.customer_id) is False
    assert updated.intake_sessions[0].status == "text_awaiting_brief"

    preview = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="brief",
        text="Create Flyer for breakfast specials from 8-11 AM Monday to Thursday",
        now=now,
    )
    assert preview.action == "brief_preview"
    assert "I will create this flyer" in preview.reply_text
    assert "Spark Growth" in preview.reply_text
    assert "breakfast specials" in preview.reply_text
    assert "Reply APPROVE to start" in preview.reply_text

    approved = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="approve",
        text="[shift-agent-sender v=1 role=customer]\nApprove.",
        now=now,
    )
    assert approved.action == "create_project"
    assert "Create Flyer for breakfast specials" in approved.raw_request
    assert "Preferred flyer language: English" in approved.raw_request
    assert "Use saved business name, address, phone, and logo" in approved.raw_request


def test_sample_idea_flow_previews_before_project_creation(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmis Kitchn",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant"})
    store = FlyerCustomerStore(next_customer_sequence=2, customers=[customer])
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    start = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="sample-start",
        text="Create flyer",
        start_source="sample_idea",
        now=now,
    )
    assert start.action == "choose_sample_idea"
    assert "Reply 1 or 2" in start.reply_text

    preview = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="sample-choice",
        text="option 1",
        now=now,
    )
    assert preview.action == "brief_preview"
    assert "I will create this flyer" in preview.reply_text
    assert "Lakshmis Kitchn" in preview.reply_text
    assert "Reply APPROVE to start" in preview.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.intake_sessions[0].status == "brief_pending_approval"
    assert updated.intake_sessions[0].brief_source == "sample"

    approved = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="sample-approve",
        text="looks good",
        now=now,
    )
    assert approved.action == "create_project"
    assert approved.brief_source == "sample"
    assert approved.brief_approved_message_id == "sample-approve"
    assert "Use saved business name, address, phone, and logo" in approved.raw_request


def test_returning_customer_vague_start_opens_concierge_choice(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    result = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Hey Flyer-Studio, I'd like you to help me create a flyer",
        start_source="concierge",
        now=now,
    )

    assert result.action == "concierge_choice"
    assert result.source == "new_flyer"
    assert "Welcome back, Lakshmi's Kitchen" in result.reply_text
    assert "What are we creating today?" in result.reply_text
    assert "You can tell me in one message, or I can guide you step by step." in result.reply_text
    assert "Pick a sample idea" not in result.reply_text
    for internal in ("concierge", "intake", "brief_pending", "project_id", "source", "audit", "workflow"):
        assert internal not in result.reply_text.lower()

    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "concierge_awaiting_choice"
    assert store.intake_sessions[0].source == "new_flyer"
    assert store.intake_sessions[0].creation_mode == ""


def test_returning_customer_concierge_accepts_one_message_brief(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    preview = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="brief",
        text="Create a breakfast specials flyer Saturday 8 AM to 11 AM with Idli $4.99 and Dosa $8.99",
        now=now,
    )

    assert preview.action == "brief_preview"
    assert "I will create this flyer" in preview.reply_text
    assert "breakfast specials" in preview.reply_text
    assert "Reply APPROVE to start" in preview.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].brief_source == "text"
    for internal in ("concierge", "intake", "brief_pending", "project_id", "source", "audit", "workflow"):
        assert internal not in preview.reply_text.lower()


def test_returning_customer_concierge_can_enter_guided_mode(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    guided = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="guide",
        text="guide me step by step",
        now=now,
    )

    assert guided.action == "guided_question"
    assert "First, what are you promoting?" in guided.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "guided_collecting_goal"
    assert store.intake_sessions[0].creation_mode == "guided"


@pytest.mark.parametrize(
    "reply",
    [
        "yes",
        "ok",
        "sure",
        "help me",
        "please",
        "yes help me",
        "one message",
        "text mode",
        "I'll type it",
        "I am an existing customer",
    ],
)
def test_returning_customer_concierge_still_vague_followup_asks_open_prompt(tmp_path, reply):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    followup = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="still-vague",
        text=reply,
        now=now,
    )

    assert followup.action == "concierge_choice"
    assert "What is the flyer for?" in followup.reply_text
    assert "event, offer, items/prices, date" in followup.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "concierge_awaiting_choice"
    assert store.intake_sessions[0].creation_mode == ""


@pytest.mark.parametrize("reply", ["ask questions", "you guide me", "guide me please", "walk me through it", "step by step please"])
def test_returning_customer_concierge_guided_variants_enter_guided_mode(tmp_path, reply):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmi's Kitchen",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "restaurant", "preferred_language": "en"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(indent=2), encoding="utf-8")

    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="welcome-back",
        text="Create flyer",
        start_source="concierge",
        now=now,
    )

    guided = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="guide",
        text=reply,
        now=now,
    )

    assert guided.action == "guided_question"
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.intake_sessions[0].status == "guided_collecting_goal"


def test_lid_only_active_customer_sample_idea_uses_saved_profile(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmis Kitchn",
        phone="+17329837841",
        now=now,
    ).model_copy(update={
        "primary_chat_id": "201975216009469@lid",
        "preferred_language": "hi",
        "business_category": "restaurant",
    })
    store = FlyerCustomerStore(next_customer_sequence=2, customers=[customer])
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    start = handle_intake_message(
        state_path=state_path,
        chat_id="201975216009469@lid",
        sender_phone=None,
        message_id="lid-sample-start",
        text="Create flyer",
        start_source="sample_idea",
        now=now,
    )

    assert start.action == "choose_sample_idea"
    assert "Lakshmis Kitchn" in start.reply_text
    assert "1." in start.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.intake_sessions[0].sender_phone is None
    assert updated.intake_sessions[0].chat_id == "201975216009469@lid"
    assert updated.intake_sessions[0].preferred_language == "hi"


def test_brief_preview_hides_internal_parser_scaffolding(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmis Kitchn",
        phone="+17329837841",
        now=now,
    )
    store = FlyerCustomerStore(next_customer_sequence=2, customers=[customer])
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="start",
        text="Create flyer",
        start_source="sample_idea",
        now=now,
    )

    preview = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="choice",
        text="1",
        now=now,
    )

    assert preview.action == "brief_preview"
    assert "Brief source" not in preview.reply_text
    assert "Preferred flyer language" not in preview.reply_text
    assert "Use saved business name" not in preview.reply_text
    assert "Project F" not in preview.reply_text


def test_old_mode_prompt_numbering_is_preserved_for_in_flight_sessions(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Lakshmis Kitchn",
        phone="+17329837841",
        now=now,
    )
    store = FlyerCustomerStore(next_customer_sequence=2, customers=[customer])
    store.intake_sessions.append(FlyerIntakeSession(
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        status="choosing_mode",
        source="new_flyer",
        started_at=now,
        updated_at=now,
        last_message_id="old-mode-prompt",
        preferred_language="en",
        mode_prompt_version="",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    guided = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="old-reply",
        text="1",
        now=now,
    )

    assert guided.action == "guided_question"
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.intake_sessions[0].creation_mode == "guided"


def test_text_mode_ready_respects_starter_prompt_opt_out(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Demo Salon",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"business_category": "salon"})
    store = FlyerCustomerStore(next_customer_sequence=2, customers=[customer])
    store.set_starter_prompt_mode(customer.customer_id, "off")
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    start = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="start",
        text="Create flyer",
        start_source="new_flyer",
        now=now,
    )
    assert start.action == "choose_language"
    language = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="lang",
        text="English",
        now=now,
    )
    assert language.action == "choose_mode"
    result = handle_intake_message(
        state_path=state_path,
        chat_id="17329837841@s.whatsapp.net",
        sender_phone="+17329837841",
        message_id="mode",
        text="3",
        now=now,
    )

    assert result.action == "text_ready"
    assert "Here is a starter flyer request" not in result.reply_text
    assert "Reply with your edited version" in result.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.intake_sessions[0].status == "text_awaiting_brief"


def test_business_whatsapp_can_be_skipped_with_no_business_account(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="collecting_business_whatsapp",
        started_at=now,
        updated_at=now,
        last_message_id="public-phone",
        business_name="Hisaku",
        business_address="101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
        public_phone="+918985741562",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="skip-whatsapp",
        text="No business account",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert result.next_status == "collecting_authorized_request_number"
    assert updated.onboarding_sessions[0].business_whatsapp_number == "+918985741562"


def test_profile_language_english_overrides_initial_mixed_choice(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="158024815611933@lid",
        sender_phone=None,
        status="collecting_business_profile",
        started_at=now,
        updated_at=now,
        last_message_id="authorized",
        business_name="Hisaku",
        business_address="101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
        public_phone="+918985741562",
        business_whatsapp_number="+918985741562",
        authorized_request_number="+918985741562",
        preferred_language="mixed",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="158024815611933@lid",
        sender_phone=None,
        message_id="profile",
        text="Digital Marketing, English",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert result.next_status == "confirming_summary"
    assert updated.onboarding_sessions[0].business_category == "Digital Marketing"
    assert updated.onboarding_sessions[0].preferred_language == "en"
    assert "Profile: Digital Marketing, en" in result.reply_text


def test_language_only_business_profile_reply_is_rejected(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    store.onboarding_sessions.append(FlyerOnboardingSession(
        chat_id="74290284261595@lid",
        sender_phone="+19803826497",
        status="collecting_business_profile",
        started_at=now,
        updated_at=now,
        last_message_id="authorized",
        business_name="Chloe Hair Studio",
        business_address="11111 Gainsborough Ct, Fairfax, VA 22030",
        public_phone="+19803826497",
        business_whatsapp_number="+19803826497",
        authorized_request_number="+19803826497",
        preferred_language="en",
        plan_id="trial",
    ))
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    result = handle_onboarding_message(
        state_path=state_path,
        chat_id="74290284261595@lid",
        sender_phone="+19803826497",
        message_id="profile-language-only",
        text="English",
        now=now,
    )

    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert result.handled is True
    assert result.next_status == "collecting_business_profile"
    assert "business type" in result.reply_text.lower()
    assert updated.onboarding_sessions[0].business_category == ""


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


def test_admin_can_add_second_authorized_number_but_third_is_rejected(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_number="+19045550104",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
        onboarded_by_phone="+17043243322",
    ).model_copy(update={"status": "trial"})
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    second = handle_account_command(
        state_path=state_path,
        sender_phone="+17043243322",
        sender_role="customer",
        chat_id="17043243322@s.whatsapp.net",
        text="ADD AUTHORIZED NUMBER +1 904 555 0105",
        now=now,
    )

    assert second.ok is True
    assert "Authorized request number added" in second.reply_text
    after_second = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert [str(phone) for phone in after_second.customers[0].authorized_request_numbers] == [
        "+19045550104",
        "+19045550105",
    ]

    third = handle_account_command(
        state_path=state_path,
        sender_phone="+17043243322",
        sender_role="customer",
        chat_id="17043243322@s.whatsapp.net",
        text="ADD AUTHORIZED NUMBER +1 904 555 0106",
        now=now,
    )

    assert third.ok is True
    assert "This account already has 2 authorized requester numbers" in third.reply_text
    assert "Remove one before adding another." in third.reply_text
    after_third = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert [str(phone) for phone in after_third.customers[0].authorized_request_numbers] == [
        "+19045550104",
        "+19045550105",
    ]


def test_customer_can_turn_sample_prompts_off_and_on(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(customer_id="CUST0001", business_name="Demo Salon", phone="+17329837841", now=now)
    store = FlyerCustomerStore(customers=[customer])
    assert store.claim_starter_prompt_send(customer.customer_id) is True
    state_path.write_text(store.model_dump_json(), encoding="utf-8")

    off = handle_account_command(
        state_path=state_path,
        sender_phone="+17329837841",
        sender_role="customer",
        chat_id="17329837841@s.whatsapp.net",
        text="don't show sample prompts",
        now=now,
    )

    assert off.ok is True
    assert "Sample prompts are off for this business account" in off.reply_text
    updated_store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated_store.starter_prompt_mode(customer.customer_id) == "off"

    on = handle_account_command(
        state_path=state_path,
        sender_phone="+17329837841",
        sender_role="customer",
        chat_id="17329837841@s.whatsapp.net",
        text="show sample prompts again",
        now=now,
    )

    assert on.ok is True
    updated_store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated_store.starter_prompt_mode(customer.customer_id) == "auto"
    assert updated_store.claim_starter_prompt_send(customer.customer_id) is True


def test_account_phone_normalizer_keeps_actor_phone_for_audit_and_pending_changes():
    assert _phone_or_none("+1 732 983 7841") == "+17329837841"
    assert _phone_or_none("not a phone") is None


def test_lid_only_customer_can_turn_sample_prompts_off(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 18, tzinfo=timezone.utc)
    customer = _trial_customer(
        customer_id="CUST0001",
        business_name="Demo Salon",
        phone="+17329837841",
        now=now,
    ).model_copy(update={"primary_chat_id": "201975216009469@lid"})
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(), encoding="utf-8")

    result = handle_account_command(
        state_path=state_path,
        sender_phone=None,
        sender_role="customer",
        chat_id="201975216009469@lid",
        text="[shift-agent-sender v=1 role=customer]\ndon't show sample prompts",
        now=now,
    )

    assert result.ok is True
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.starter_prompt_mode(customer.customer_id) == "off"


def test_account_admin_can_update_business_name_from_whatsapp(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 22, tzinfo=timezone.utc)
    customer = _trial_customer(customer_id="CUST0001", business_name="Lakshmis Kitchn", phone="+17329837841", now=now)
    state_path.write_text(FlyerCustomerStore(customers=[customer]).model_dump_json(), encoding="utf-8")

    result = handle_account_command(
        state_path=state_path,
        sender_phone="+17329837841",
        sender_role="customer",
        chat_id="17329837841@s.whatsapp.net",
        text="update business name to Lakshmi's Kitchen",
        now=now,
    )

    assert result.ok is True
    assert result.handled is True
    assert "Business name updated" in result.reply_text
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.customers[0].business_name == "Lakshmi's Kitchen"


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


def test_quota_is_shared_across_two_authorized_requesters(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
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
        "current_period_end": datetime(2026, 6, 19, tzinfo=timezone.utc),
        "authorized_request_numbers": ["+19045550104", "+19045550105"],
    })
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")

    first = reserve_quota(
        state_path=state_path,
        customer_phone="+19045550104",
        project_id="F0100",
        message_id="m1",
        now=now,
    )
    second = reserve_quota(
        state_path=state_path,
        customer_phone="+19045550105",
        project_id="F0101",
        message_id="m2",
        now=now,
    )

    assert first.ok is True
    assert second.ok is True
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.customers[0].usage_count_for_current_period() == 2
    assert updated.customers[0].quota_remaining(FlyerPlanTier.default_tiers()) == 28


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
        text="3",
        now=now,
    )
    assert ready.action == "text_ready"
    assert "existing flyer" in ready.reply_text
    updated = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert updated.customers[0].preferred_language == "ta"
    assert updated.intake_sessions[0].status == "text_awaiting_brief"


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
    first_question = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g3", text="2", now=now)
    assert first_question.action == "guided_question"
    assert "what are you promoting" in first_question.reply_text.lower()
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g4", text="Weekend breakfast specials", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g5", text="Saturday and Sunday 8 AM to 11 AM", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g6", text="Idli $4.99, Dosa $8.99", now=now)
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g7", text="Use saved", now=now)
    preview = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g8", text="Use logo, festive style", now=now)
    assert preview.action == "brief_preview"
    assert "I will create this flyer" in preview.reply_text
    done = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17043243322", message_id="g9", text="yes create it", now=now)
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
    handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m3", text="2", now=now)
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
    preview = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m8", text="SKIP", now=now)
    assert preview.action == "brief_preview"
    done = handle_intake_message(state_path=state_path, chat_id=chat_id, sender_phone="+17329837841", message_id="m9", text="APPROVE", now=now)

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
        text="2",
        now=now,
    )
    assert handoff.action == "onboarding_started"
    assert "business name" in handoff.reply_text.lower()
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.onboarding_sessions[0].preferred_language == "ml"
    assert store.onboarding_sessions[0].creation_mode == "guided"
    assert store.onboarding_sessions[0].plan_id == "trial"


def test_start_trial_sample_handoff_copy_promises_ideas_not_text_mode(tmp_path):
    state_path = tmp_path / "customers.json"
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
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
        text="English",
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
    assert "sample ideas" in handoff.reply_text.lower()
    assert "type your flyer request" not in handoff.reply_text.lower()
    store = FlyerCustomerStore.model_validate_json(state_path.read_text(encoding="utf-8"))
    assert store.onboarding_sessions[0].creation_mode == "sample"
