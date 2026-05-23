"""Pydantic validation + state-machine contracts for Hermes Flyer Studio."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError, TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from schemas import (  # noqa: E402
    Config,
    FlyerAsset,
    FlyerBrandKit,
    FlyerCustomerActivated,
    FlyerCustomerProfile,
    FlyerConcept,
    FlyerConfig,
    FlyerRecoveryCustomerAckAttempted,
    FlyerRecoveryIncidentOpened,
    FlyerGuestOrder,
    FlyerGuestOrderStore,
    FlyerProject,
    FlyerProjectCreated,
    FlyerProjectStore,
    FlyerRequestFields,
    FlyerRevision,
    FlyerUsageEvent,
    FlyerWorkflowStatus,
    LogEntry,
    is_flyer_transition_allowed,
)


def _base_project_dict() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "project_id": "F0001",
        "status": "intake_started",
        "customer_phone": "+19045550123",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "wamid.flyer.1",
        "raw_request": "Need flyer for Bathukamma Oct 10",
    }


def test_flyer_config_defaults_are_safe_and_cost_bounded():
    cfg = FlyerConfig()
    assert cfg.enabled is False
    assert cfg.concept_count == 1
    assert cfg.max_revision_rounds == 6
    assert cfg.draft_image_model == "gpt-image-1-mini"
    assert cfg.draft_image_quality == "low"
    assert cfg.final_image_model == "gpt-image-1.5"
    assert cfg.final_image_quality == "medium"
    assert cfg.edit_image_model == "gpt-image-1"
    assert cfg.edit_image_quality == "medium"
    assert [(t.plan_id, t.monthly_price_usd, t.included_flyers) for t in cfg.plan_tiers] == [
        ("trial", 0.00, 3),
        ("starter", 49.99, 30),
        ("growth", 69.99, 60),
        ("unlimited", 199.0, None),
    ]
    assert cfg.payment_provider == "manual"
    assert cfg.quick_flyer_price_cents == 400
    assert cfg.quick_flyer_checkout_url_template == ""
    assert cfg.final_formats == [
        "whatsapp_image",
        "instagram_post",
        "instagram_story",
        "printable_pdf",
    ]


def test_config_includes_flyer_default_disabled():
    cfg = Config.model_validate({
        "schema_version": 1,
        "customer": {
            "name": "Triveni",
            "location_id": "loc_pineville_01",
            "timezone": "America/New_York",
        },
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
    })
    assert cfg.flyer.enabled is False
    assert cfg.flyer.recovery.mode == "off"
    assert cfg.flyer.recovery.enable_timer is False


def test_flyer_recovery_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        FlyerConfig.model_validate({
            "recovery": {
                "mode": "observe",
                "enable_timer": True,
                "unexpected": True,
            }
        })


def test_flyer_recovery_audit_variants_dispatch_through_log_entry():
    opened = {
        "type": "flyer_recovery_incident_opened",
        "ts": datetime.now(timezone.utc).isoformat(),
        "incident_id": "FRI20260523-0001",
        "failure_class": "concept_generation_failed",
        "severity": "warning",
        "project_id": "F0065",
        "source_fingerprint": "sha256:fp",
        "ack_dedupe_key": "sha256:ack",
        "chat_id_hash": "sha256:chat",
        "evidence_quality": "strong",
        "mode": "observe",
    }
    attempted = {
        "type": "flyer_recovery_customer_ack_attempted",
        "ts": datetime.now(timezone.utc).isoformat(),
        "incident_id": "FRI20260523-0001",
        "ack_attempt_id": "FRA20260523-0001",
        "ack_dedupe_key": "sha256:ack",
        "source_fingerprint": "sha256:fp",
        "chat_id_hash": "sha256:chat",
        "evidence_quality": "strong",
        "mode": "customer_ack",
        "copy_policy_template_id": "generic_tracked",
        "message_sha256": "sha256:msg",
    }

    adapter = TypeAdapter(LogEntry)
    assert isinstance(adapter.validate_python(opened), FlyerRecoveryIncidentOpened)
    assert isinstance(adapter.validate_python(attempted), FlyerRecoveryCustomerAckAttempted)


def test_guest_order_store_tracks_payment_first_one_off_order():
    now = datetime.now(timezone.utc)
    store = FlyerGuestOrderStore()
    order = store.new_order(
        sender_phone="+17329837841",
        chat_id="17329837841@s.whatsapp.net",
        message_id="cta-1",
        now=now,
        checkout_url="https://pay.example/GUEST0001",
    )
    store.orders.append(order)

    assert order.order_id == "GUEST0001"
    assert order.status == "pending_payment"
    assert order.can_create_flyer() is False
    paid = order.model_copy(update={"status": "paid", "paid_at": now, "updated_at": now})
    assert paid.can_create_flyer() is True
    assert store.find_open_order_by_sender("+17329837841", "17329837841@s.whatsapp.net").order_id == "GUEST0001"
    assert FlyerGuestOrder.model_validate(paid.model_dump()).remaining() == 1


def test_request_fields_track_required_info_and_missing_essentials():
    fields = FlyerRequestFields(
        event_or_business_name="Bathukamma",
        event_date="2026-10-10",
        preferred_language="te",
        output_formats=["whatsapp_image", "printable_pdf"],
    )
    assert fields.missing_required_fields() == [
        "event_time",
        "venue_or_location",
        "contact_info",
    ]


def test_recurring_schedule_request_does_not_require_fake_event_date():
    fields = FlyerRequestFields(
        event_or_business_name="Weekend Breakfast",
        event_time="08:00",
        venue_or_location="Triveni Pineville, 300 S Polk St, Pineville, NC 28134",
        contact_info="(704) 324-3322",
        notes="Starts from 8 AM on both Saturday and Sunday.",
    )
    assert fields.missing_required_fields() == []


def test_price_list_menu_flyer_does_not_require_event_date_time_or_venue():
    fields = FlyerRequestFields(
        event_or_business_name="Lakshmi's Kitchen",
        contact_info="+1 9802005022",
        notes=(
            "Items: Bobbatlu $2/piece; Poornalu $1/piece; "
            "Murukulu $12/lb; Gongura Pulav Full tray $170"
        ),
    )
    assert fields.missing_required_fields() == []


def test_service_list_flyer_does_not_require_event_date_time_or_venue():
    fields = FlyerRequestFields(
        event_or_business_name="Marketing Services",
        venue_or_location="101 Kavitha Palace, KPHB, Hyderabad, Telangana 500085",
        contact_info="+918985741562",
        notes=(
            "Services: Social media marketing, Performance marketing, SEO, "
            "AEO, GEO, AI Marketing, Content Creation, Paid Ads"
        ),
    )
    assert fields.missing_required_fields() == []


def test_uploaded_template_reference_can_generate_from_reference_image_only():
    fields = FlyerRequestFields(
        event_or_business_name="Thursday Dosa Night Special",
        notes="Create flyer from uploaded template/reference. Customer requested: change non-veg combo price.",
    )
    assert fields.missing_required_fields() == []


def test_request_fields_accept_llm_extras_but_reject_bad_dates():
    fields = FlyerRequestFields.model_validate({
        "event_or_business_name": "Bathukamma",
        "event_date": "2026-10-10",
        "event_time": "18:30",
        "venue_or_location": "Community Hall",
        "contact_info": "+1 904 555 0123",
        "preferred_language": "te",
        "extra_llm_guess": "ignored",
    })
    assert fields.preferred_language == "te"
    with pytest.raises(ValidationError):
        FlyerRequestFields(event_date="10/10/2026")


def test_project_state_machine_allows_only_expected_transitions():
    allowed = [
        ("intake_started", "collecting_required_info"),
        ("collecting_required_info", "awaiting_assets"),
        ("collecting_required_info", "generating_concepts"),
        ("awaiting_assets", "generating_concepts"),
        ("manual_edit_required", "generating_concepts"),
        ("manual_edit_required", "revising_design"),
        ("generating_concepts", "awaiting_concept_selection"),
        ("generating_concepts", "awaiting_final_approval"),
        ("awaiting_concept_selection", "revising_design"),
        ("revising_design", "awaiting_final_approval"),
        ("revising_design", "generating_concepts"),
        ("awaiting_final_approval", "finalizing_assets"),
        ("finalizing_assets", "delivered"),
        ("delivered", "revising_design"),
        ("delivered", "completed"),
    ]
    for from_status, to_status in allowed:
        assert is_flyer_transition_allowed(from_status, to_status)
    assert not is_flyer_transition_allowed("awaiting_final_approval", "completed")
    assert not is_flyer_transition_allowed("manual_edit_required", "delivered")
    assert not is_flyer_transition_allowed("manual_edit_required", "completed")
    assert not is_flyer_transition_allowed("completed", "revising_design")


def test_project_defaults_and_revision_cap():
    project = FlyerProject.model_validate(_base_project_dict())
    assert project.fields.missing_required_fields() == [
        "event_or_business_name",
        "event_date",
        "event_time",
        "venue_or_location",
        "contact_info",
    ]
    assert project.version == 1

    too_many = _base_project_dict()
    too_many["revisions"] = [
        {
            "revision_id": f"R{i:03d}",
            "message_id": f"wamid.r{i}",
            "requested_at": datetime.now(timezone.utc),
            "request_text": "make Telugu bigger",
            "applied": False,
        }
        for i in range(51)
    ]
    with pytest.raises(ValidationError):
        FlyerProject.model_validate(too_many)


def test_brand_kit_and_assets_validate_paths_and_hashes():
    kit = FlyerBrandKit(
        customer_phone="+19045550123",
        logos=[
            FlyerAsset(
                asset_id="A0001",
                kind="logo",
                source="whatsapp",
                path="/opt/shift-agent/state/flyer/assets/A0001.png",
                mime_type="image/png",
                sha256="a" * 64,
                original_message_id="wamid.logo.1",
                received_at=datetime.now(timezone.utc),
            )
        ],
        colors=["#FFCC00", "#008080"],
        preferred_language="te",
    )
    assert kit.logos[0].kind == "logo"
    with pytest.raises(ValidationError):
        FlyerAsset(
            asset_id="A0002",
            kind="reference_image",
            source="whatsapp",
            path="/tmp/outside.png",
            mime_type="image/png",
            sha256="b" * 64,
            original_message_id="wamid.logo.2",
            received_at=datetime.now(timezone.utc),
        )


def test_concept_and_store_shapes():
    concept = FlyerConcept(
        concept_id="C1",
        title="Festive Telugu",
        style_summary="Telangana floral theme with bold Telugu title",
        preview_asset_id="A0001",
        prompt="bright festive Bathukamma flyer",
        created_at=datetime.now(timezone.utc),
    )
    assert concept.concept_id == "C1"

    store = FlyerProjectStore(projects=[FlyerProject.model_validate(_base_project_dict())])
    assert store.schema_version == 1
    assert store.projects[0].project_id == "F0001"


def test_flyer_audit_entry_is_part_of_log_union():
    now = datetime.now(timezone.utc)
    entry = FlyerProjectCreated(
        type="flyer_project_created",
        ts=now,
        project_id="F0001",
        customer_phone="+19045550123",
        original_message_id="wamid.flyer.1",
    )
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.type == "flyer_project_created"
    activation = FlyerCustomerActivated(
        type="flyer_customer_activated",
        ts=now,
        customer_id="CUST0001",
        plan_id="starter",
        provider="stripe",
        payment_reference="pi_123",
        payment_amount_cents=4999,
    )
    parsed_activation = TypeAdapter(LogEntry).validate_python(activation.model_dump())
    assert parsed_activation.type == "flyer_customer_activated"


def test_customer_profile_defaults_and_quota_latest_state():
    now = datetime.now(timezone.utc)
    customer = FlyerCustomerProfile(
        customer_id="CUST0001",
        business_name="Triveni",
        business_address="300 S Polk",
        public_phone="+17043243322",
        business_whatsapp_number="+17043243322",
        authorized_request_numbers=["+19045550104"],
        plan_id="starter",
        status="active",
        created_at=now,
        updated_at=now,
        current_period_start=now,
        current_period_end=now.replace(year=now.year + 1),
        usage_events=[
            FlyerUsageEvent(
                reservation_id="CUST0001:F0001",
                project_id="F0001",
                customer_id="CUST0001",
                kind="reserved",
                recorded_at=now,
                message_id="m1",
            ),
            FlyerUsageEvent(
                reservation_id="CUST0001:F0001",
                project_id="F0001",
                customer_id="CUST0001",
                kind="used",
                recorded_at=now,
                message_id="m1",
            ),
            FlyerUsageEvent(
                reservation_id="CUST0001:F0002",
                project_id="F0002",
                customer_id="CUST0001",
                kind="released",
                recorded_at=now,
                message_id="m2",
            ),
        ],
    )
    assert customer.primary_chat_id == ""
    assert customer.usage_count_for_current_period() == 1
    assert customer.quota_remaining(FlyerConfig().plan_tiers) == 29
    assert customer.is_account_admin("+17043243322", "shared@g.us", "unknown") is True
    assert customer.is_account_admin("+19045550104", "shared@g.us", "unknown") is False


def test_workflow_status_literal_contains_requested_states():
    assert set(FlyerWorkflowStatus.__args__) == {
        "intake_started",
        "collecting_required_info",
        "awaiting_assets",
        "manual_edit_required",
        "generating_concepts",
        "awaiting_concept_selection",
        "revising_design",
        "awaiting_final_approval",
        "finalizing_assets",
        "delivered",
        "completed",
    }
