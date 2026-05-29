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
    FlyerAutoRepairAttemptStore,
    FlyerBrandKit,
    FlyerCustomerActivated,
    FlyerCustomerProfile,
    FlyerConcept,
    FlyerConfig,
    FlyerRepairAttempt,
    FlyerRecoveryCustomerAckAttempted,
    FlyerRecoveryIncidentOpened,
    FlyerRecoveryOperatorActionRequired,
    FlyerRecoveryOutcomeRepaired,
    FlyerRecoveryResolved,
    FlyerGuestOrder,
    FlyerGuestOrderStore,
    FlyerIntakeSession,
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
    assert cfg.draft_image_model == "deterministic-renderer"
    assert cfg.draft_image_quality == "low"
    assert cfg.final_image_model == "deterministic-renderer"
    assert cfg.final_image_quality == "high"
    assert cfg.edit_image_model == "gpt-image-1"
    assert cfg.edit_image_quality == "medium"
    assert cfg.draft_provider_policy.default.provider == "local"
    assert cfg.draft_provider_policy.default.model == "deterministic-renderer"
    assert cfg.draft_provider_policy.default.quality == "low"
    assert cfg.draft_provider_policy.text_heavy.primary.model == "recraft/recraft-v4.1"
    assert cfg.draft_provider_policy.text_heavy.premium.model == "sourceful/riverflow-v2-pro"
    assert cfg.draft_provider_policy.visual_heavy.primary.model == "black-forest-labs/flux.2-pro"
    assert cfg.final_provider_policy.default.provider == "local"
    assert cfg.final_provider_policy.default.model == "deterministic-renderer"
    assert cfg.final_provider_policy.fallback.model == "openai/gpt-5.4-image-2"
    assert cfg.source_edit_provider_policy.default.provider == "openrouter"
    assert cfg.source_edit_provider_policy.default.model == "openai/gpt-5.4-image-2"
    assert cfg.source_edit_provider_policy.emergency_fallback.provider == "manual_review"
    assert cfg.resolve_draft_render_provider().provider == "local"
    assert cfg.resolve_draft_render_provider().model == "deterministic-renderer"
    assert cfg.resolve_draft_render_provider().quality == "low"
    assert cfg.resolve_final_render_provider().model == "deterministic-renderer"
    assert cfg.resolve_source_edit_render_provider().provider == "manual_review"
    assert cfg.resolve_source_edit_render_provider().model == "manual_review"
    assert [(t.plan_id, t.monthly_price_usd, t.included_flyers) for t in cfg.plan_tiers] == [
        ("trial", 0.00, 3),
        ("starter", 49.99, 30),
        ("growth", 69.99, 60),
        ("unlimited", 199.0, None),
    ]
    assert cfg.payment_provider == "manual"
    assert cfg.quick_flyer_price_cents == 400
    assert cfg.quick_flyer_checkout_url_template == ""
    assert cfg.recovery.auto_repair_enabled is True
    assert cfg.recovery.max_auto_repair_attempts == 1
    assert cfg.recovery.auto_repair_attempt_stale_minutes == 30
    assert cfg.final_formats == [
        "whatsapp_image",
        "instagram_post",
        "instagram_story",
        "printable_pdf",
    ]


def test_flyer_autorepair_attempt_store_is_separate_from_project_schema():
    now = datetime.now(timezone.utc)
    attempt = FlyerRepairAttempt(
        attempt_id="F0001-v2-a1",
        project_id="F0001",
        project_version=2,
        mode="hermes_regenerate",
        status="attempted",
        qa_blocker_hash="a" * 64,
        repair_instruction_hash="b" * 64,
        repair_instruction="Show each offer item exactly once.",
        started_at=now,
    )
    store = FlyerAutoRepairAttemptStore(attempts=[attempt])
    assert store.attempts[0].project_id == "F0001"

    project_payload = _base_project_dict()
    project_payload["repair_attempts"] = [attempt.model_dump(mode="json")]
    with pytest.raises(ValidationError):
        FlyerProject.model_validate(project_payload)


def test_flyer_autorepair_audit_rows_round_trip_through_log_entry():
    from schemas import (  # noqa: E402
        FlyerAutoRepairAttempted,
        FlyerAutoRepairExhausted,
        FlyerAutoRepairSkipped,
        FlyerAutoRepairSucceeded,
    )

    now = datetime.now(timezone.utc)
    common = {
        "ts": now,
        "attempt_id": "F0001-v1-a1",
        "project_id": "F0001",
        "project_version": 1,
        "mode": "hermes_regenerate",
        "qa_blocker_hash": "a" * 64,
        "repair_instruction_hash": "b" * 64,
        "detail": "missing required visible fact: item:2:name",
        "generated_asset_ids": ["A0001"],
    }

    for cls, expected_type in [
        (FlyerAutoRepairAttempted, "flyer_autorepair_attempted"),
        (FlyerAutoRepairSucceeded, "flyer_autorepair_succeeded"),
        (FlyerAutoRepairExhausted, "flyer_autorepair_exhausted"),
        (FlyerAutoRepairSkipped, "flyer_autorepair_skipped"),
    ]:
        entry = cls(**common)
        parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
        assert parsed.type == expected_type
        assert parsed.project_id == "F0001"


def test_flyer_config_legacy_model_fields_still_resolve_when_policy_absent():
    cfg = FlyerConfig.model_validate({
        "enabled": True,
        "draft_image_model": "deterministic-renderer",
        "draft_image_quality": "low",
        "final_image_model": "openai/gpt-5.4-image-2",
        "final_image_quality": "high",
    })

    draft = cfg.resolve_draft_render_provider()
    final = cfg.resolve_final_render_provider()

    assert draft.provider == "local"
    assert draft.model == "deterministic-renderer"
    assert draft.quality == "low"
    assert final.provider == "openrouter"
    assert final.model == "openai/gpt-5.4-image-2"
    assert final.quality == "high"


def test_flyer_config_provider_policy_overrides_legacy_model_fields():
    cfg = FlyerConfig.model_validate({
        "enabled": True,
        "draft_image_model": "deterministic-renderer",
        "draft_provider_policy": {
            "default": {
                "provider": "openrouter",
                "model": "recraft/recraft-v4.1",
                "quality": "balanced",
            }
        },
        "final_image_model": "openai/gpt-5.4-image-2",
        "final_provider_policy": {
            "default": {
                "provider": "local",
                "model": "deterministic-renderer",
                "quality": "high",
            }
        },
    })

    assert cfg.resolve_draft_render_provider().model == "recraft/recraft-v4.1"
    assert cfg.resolve_draft_render_provider().quality == "balanced"
    assert cfg.resolve_final_render_provider().model == "deterministic-renderer"


def test_flyer_config_source_edit_policy_overrides_legacy_model_fields():
    cfg = FlyerConfig.model_validate({
        "enabled": True,
        "edit_image_model": "gpt-image-1",
        "edit_image_quality": "medium",
        "source_edit_provider_policy": {
            "default": {
                "provider": "openrouter",
                "model": "openai/gpt-5.4-image-2",
                "quality": "high",
            },
            "emergency_fallback": {
                "provider": "manual_review",
                "model": "manual_review",
                "quality": "high",
            },
        },
    })

    source = cfg.resolve_source_edit_render_provider()

    assert source.provider == "openrouter"
    assert source.model == "openai/gpt-5.4-image-2"
    assert source.quality == "high"


def test_flyer_config_source_edit_legacy_fields_preserve_openai_when_policy_absent():
    cfg = FlyerConfig.model_validate({
        "enabled": True,
        "edit_image_model": "gpt-image-1",
        "edit_image_quality": "medium",
    })

    source = cfg.resolve_source_edit_render_provider()

    assert source.provider == "openai"
    assert source.model == "gpt-image-1"
    assert source.quality == "medium"


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

    outcome = {
        "type": "flyer_recovery_outcome_repaired",
        "ts": datetime.now(timezone.utc).isoformat(),
        "repair_type": "reference_scope_false_positive",
        "status": "sent",
        "chat_id_hash": "sha256:chat",
        "customer_id": "CUST0004",
        "business_name": "Chloe hair studio",
        "scope_reason": "no_spend_exact_source_edit_known_account",
        "outbound_message_id": "mid-1",
        "error": "",
    }
    resolved = {
        "type": "flyer_recovery_resolved",
        "ts": datetime.now(timezone.utc).isoformat(),
        "incident_id": "FRI20260523-0001",
        "resolution": "customer_visible_success",
    }
    operator_action = {
        "type": "flyer_recovery_operator_action_required",
        "ts": datetime.now(timezone.utc).isoformat(),
        "incident_id": "FRI20260523-0001",
        "failure_class": "concept_generation_failed",
        "project_id": "F0065",
        "reason": "worker_completed_no_customer_visible_success",
        "required_action": "verify_customer_outcome_or_repair_manually",
    }

    adapter = TypeAdapter(LogEntry)
    assert isinstance(adapter.validate_python(opened), FlyerRecoveryIncidentOpened)
    assert isinstance(adapter.validate_python(attempted), FlyerRecoveryCustomerAckAttempted)
    assert isinstance(adapter.validate_python(outcome), FlyerRecoveryOutcomeRepaired)
    assert isinstance(adapter.validate_python(resolved), FlyerRecoveryResolved)
    assert isinstance(adapter.validate_python(operator_action), FlyerRecoveryOperatorActionRequired)


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


def test_flyer_intake_session_accepts_brief_builder_statuses_and_fields():
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    for status in ("text_awaiting_brief", "choosing_sample_idea", "brief_pending_approval"):
        session = FlyerIntakeSession(
            chat_id="17329837841@s.whatsapp.net",
            sender_phone="+17329837841",
            status=status,
            source="new_flyer",
            started_at=now,
            updated_at=now,
            last_message_id="m1",
            preferred_language="en",
            creation_mode="text",
            mode_prompt_version="brief_builder_v1",
            brief_raw_request="Create an evening snacks flyer from 4 PM to 7 PM.",
            brief_display_request="Evening snacks, 4 PM to 7 PM.",
            brief_source="text",
            brief_approved_at=None,
            brief_approved_message_id="",
        )
        assert session.status == status
        assert session.brief_raw_request.startswith("Create an evening snacks")
        assert session.brief_display_request == "Evening snacks, 4 PM to 7 PM."
        assert session.brief_source == "text"


def test_flyer_intake_session_still_rejects_unknown_brief_fields():
    now = datetime(2026, 5, 21, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        FlyerIntakeSession.model_validate({
            "chat_id": "17329837841@s.whatsapp.net",
            "sender_phone": "+17329837841",
            "status": "brief_pending_approval",
            "source": "new_flyer",
            "started_at": now,
            "updated_at": now,
            "brief_raw_request": "Create flyer",
            "brief_display_request": "Create flyer",
            "brief_source": "text",
            "brief_unreviewed_extra": "must fail",
        })


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
        ("manual_edit_required", "closed_no_send"),
        ("generating_concepts", "awaiting_concept_selection"),
        ("generating_concepts", "awaiting_final_approval"),
        ("generating_concepts", "manual_edit_required"),
        ("awaiting_concept_selection", "revising_design"),
        ("revising_design", "awaiting_final_approval"),
        ("revising_design", "generating_concepts"),
        ("awaiting_final_approval", "finalizing_assets"),
        ("finalizing_assets", "manual_edit_required"),
        ("finalizing_assets", "delivered"),
        ("delivered", "revising_design"),
        ("delivered", "completed"),
    ]
    for from_status, to_status in allowed:
        assert is_flyer_transition_allowed(from_status, to_status)
    assert not is_flyer_transition_allowed("awaiting_final_approval", "completed")
    assert not is_flyer_transition_allowed("manual_edit_required", "delivered")
    assert not is_flyer_transition_allowed("manual_edit_required", "completed")
    assert not is_flyer_transition_allowed("closed_no_send", "revising_design")
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
    assert project.pending_revision_confirmation is None
    assert project.last_applied_pending_revision_id == ""

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
        "closed_no_send",
    }


# ─── F0061 source-contract additions ───────────────────────────────


def test_flyer_source_contract_rejects_unknown_fields():
    from schemas import FlyerSourceContract  # noqa: E402

    FlyerSourceContract(
        source_business_names=["Triveni Express"],
        required_headings=["Monday Thali Specials"],
        sections=[],
        requested_replacements={"Triveni Express": "Lakshmi's Kitchen"},
        preserve_layout=True,
        preserve_unmentioned_text=True,
        confidence=0.8,
    )
    with pytest.raises(ValidationError):
        FlyerSourceContract(unknown_field=1)  # type: ignore[call-arg]


def test_flyer_source_contract_section_accepts_items_without_prices():
    from schemas import FlyerSourceContract, FlyerSourceContractSection  # noqa: E402

    section = FlyerSourceContractSection(heading="Veg Thali Specials", items=["Rice", "Dal", "Pakora"])
    contract = FlyerSourceContract(sections=[section])
    assert contract.sections[0].items == ["Rice", "Dal", "Pakora"]


def test_flyer_source_contract_requested_replacements_round_trip():
    from schemas import FlyerSourceContract  # noqa: E402

    contract = FlyerSourceContract(requested_replacements={"Rice": "Jeera Rice"})
    payload = contract.model_dump_json()
    restored = FlyerSourceContract.model_validate_json(payload)
    assert restored.requested_replacements == {"Rice": "Jeera Rice"}


def test_flyer_source_contract_extracted_round_trips_through_log_entry():
    from schemas import FlyerSourceContractExtracted  # noqa: E402

    now = datetime.now(timezone.utc)
    entry = FlyerSourceContractExtracted(
        ts=now,
        project_id="F0061",
        asset_id="A0001",
        asset_sha256="a" * 64,
        role="source_edit_template",
        status="ok",
        headings_count=4,
        sections_count=3,
        replacements_count=3,
        forbidden_substrings_count=2,
        confidence=0.85,
        provider="openrouter_vision",
    )
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.type == "flyer_source_contract_extracted"
    assert parsed.headings_count == 4


def test_flyer_source_vs_new_chosen_round_trips_through_log_entry():
    from schemas import FlyerSourceVsNewChosen  # noqa: E402

    now = datetime.now(timezone.utc)
    entry = FlyerSourceVsNewChosen(
        ts=now,
        sender_phone="+17329837841",
        customer_id="CUST0001",
        original_intent="exact_source_edit",
        choice="clarification_sent",
        pending_age_sec=12,
    )
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.type == "flyer_source_vs_new_chosen"
    assert parsed.original_intent == "exact_source_edit"


def test_flyer_source_vs_new_chosen_rejects_invalid_choice():
    from schemas import FlyerSourceVsNewChosen  # noqa: E402

    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        FlyerSourceVsNewChosen(
            ts=now,
            original_intent="exact_source_edit",
            choice="nonsense",  # type: ignore[arg-type]
        )


def test_flyer_reference_extraction_supports_optional_source_contract():
    from schemas import FlyerReferenceExtraction, FlyerSourceContract  # noqa: E402

    ext = FlyerReferenceExtraction(
        asset_id="A0001",
        role="source_edit_template",
        status="ok",
        source_contract=FlyerSourceContract(preserve_layout=True),
    )
    payload = ext.model_dump_json()
    restored = FlyerReferenceExtraction.model_validate_json(payload)
    assert restored.source_contract is not None
    assert restored.source_contract.preserve_layout is True


def test_flyer_reference_extraction_source_contract_defaults_to_none():
    from schemas import FlyerReferenceExtraction  # noqa: E402

    ext = FlyerReferenceExtraction(
        asset_id="A0001",
        role="menu_reference",
        status="not_run",
    )
    assert ext.source_contract is None


# ─────────────────────────────────────────────────────────────────
# 2026-05-28 — intake-bypass audit pair (Commit 1)
# Pairs with the plan + design at
# tasks/flyer-intake-bypass-{plan,design}-2026-05-28.md.
# ─────────────────────────────────────────────────────────────────


_BYPASS_NOW = datetime(2026, 5, 28, 22, 30, tzinfo=timezone.utc)
_BYPASS_CHAT_HASH = "a" * 32  # mirrors _short_hash output shape


def test_flyer_intake_bypassed_round_trip_edit_with_media():
    from schemas import FlyerIntakeBypassed  # noqa: E402

    entry = FlyerIntakeBypassed(
        ts=_BYPASS_NOW,
        chat_id_hash=_BYPASS_CHAT_HASH,
        bypass_reason="edit_with_media",
        has_media=True,
        customer_state="",
        intake_session_status="choosing_mode",
        inbound_script="latin",
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["type"] == "flyer_intake_bypassed"
    assert dumped["bypass_reason"] == "edit_with_media"
    assert dumped["has_media"] is True
    assert dumped["customer_state"] == ""
    assert dumped["inbound_script"] == "latin"
    restored = FlyerIntakeBypassed.model_validate(dumped)
    assert restored == entry


def test_flyer_intake_bypassed_accepts_all_five_bypass_reason_literals():
    """All 5 bypass_reason Literal values round-trip — operator decision
    2026-05-28 #2 (5 values, not 3)."""
    from schemas import FlyerIntakeBypassed  # noqa: E402
    for reason in (
        "edit_with_media",
        "new_flyer_text_only",
        "new_flyer_with_media",
        "existing_active_customer_intent",
        "existing_trial_customer_intent",
    ):
        entry = FlyerIntakeBypassed(
            ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
            bypass_reason=reason, has_media=False,
        )
        assert entry.bypass_reason == reason


def test_flyer_intake_bypassed_rejects_unknown_bypass_reason():
    from schemas import FlyerIntakeBypassed  # noqa: E402
    with pytest.raises(ValidationError):
        FlyerIntakeBypassed(
            ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
            bypass_reason="not_a_real_reason", has_media=False,
        )


def test_flyer_intake_bypassed_accepts_all_four_inbound_script_literals():
    """All 4 inbound_script Literal values — operator decision #3 + reviewer 2
    regional-SMB telemetry."""
    from schemas import FlyerIntakeBypassed  # noqa: E402
    for script in ("latin", "devanagari", "tamil", "other"):
        entry = FlyerIntakeBypassed(
            ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
            bypass_reason="edit_with_media", has_media=True,
            inbound_script=script,
        )
        assert entry.inbound_script == script


def test_flyer_intake_bypassed_inbound_script_defaults_to_latin():
    from schemas import FlyerIntakeBypassed  # noqa: E402
    entry = FlyerIntakeBypassed(
        ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
        bypass_reason="edit_with_media", has_media=True,
    )
    assert entry.inbound_script == "latin"


def test_flyer_intake_bypassed_requires_non_empty_chat_id_hash():
    from schemas import FlyerIntakeBypassed  # noqa: E402
    with pytest.raises(ValidationError):
        FlyerIntakeBypassed(
            ts=_BYPASS_NOW, chat_id_hash="",  # min_length=1
            bypass_reason="edit_with_media", has_media=True,
        )


def test_flyer_intake_bypassed_rejects_extra_field():
    from schemas import FlyerIntakeBypassed  # noqa: E402
    with pytest.raises(ValidationError):
        FlyerIntakeBypassed.model_validate({
            "type": "flyer_intake_bypassed",
            "ts": _BYPASS_NOW.isoformat(),
            "chat_id_hash": _BYPASS_CHAT_HASH,
            "bypass_reason": "edit_with_media",
            "has_media": True,
            "rogue_field": "no",
        })


def test_flyer_intake_bypass_outcome_round_trip_routed_to_project():
    from schemas import FlyerIntakeBypassOutcome  # noqa: E402
    entry = FlyerIntakeBypassOutcome(
        ts=_BYPASS_NOW,
        chat_id_hash=_BYPASS_CHAT_HASH,
        outcome="routed_to_project",
        project_id="F0108",
        handler_intercept="",
        elapsed_ms=42,
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["type"] == "flyer_intake_bypass_outcome"
    assert dumped["outcome"] == "routed_to_project"
    assert dumped["project_id"] == "F0108"
    assert dumped["elapsed_ms"] == 42
    restored = FlyerIntakeBypassOutcome.model_validate(dumped)
    assert restored == entry


def test_flyer_intake_bypass_outcome_accepts_all_three_outcome_literals():
    """All 3 outcome Literal values — operator decision 2026-05-28 #5."""
    from schemas import FlyerIntakeBypassOutcome  # noqa: E402
    for outcome in ("routed_to_project", "unrouted", "intermediate_intercept_handled"):
        entry = FlyerIntakeBypassOutcome(
            ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
            outcome=outcome,
        )
        assert entry.outcome == outcome


def test_flyer_intake_bypass_outcome_rejects_unknown_outcome():
    from schemas import FlyerIntakeBypassOutcome  # noqa: E402
    with pytest.raises(ValidationError):
        FlyerIntakeBypassOutcome(
            ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
            outcome="created_a_universe",
        )


def test_flyer_intake_bypass_outcome_project_id_defaults_empty():
    """When outcome != routed_to_project, project_id is empty by default."""
    from schemas import FlyerIntakeBypassOutcome  # noqa: E402
    entry = FlyerIntakeBypassOutcome(
        ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
        outcome="unrouted",
    )
    assert entry.project_id == ""
    assert entry.handler_intercept == ""
    assert entry.elapsed_ms == 0


def test_flyer_intake_bypass_outcome_elapsed_ms_non_negative():
    from schemas import FlyerIntakeBypassOutcome  # noqa: E402
    with pytest.raises(ValidationError):
        FlyerIntakeBypassOutcome(
            ts=_BYPASS_NOW, chat_id_hash=_BYPASS_CHAT_HASH,
            outcome="routed_to_project", elapsed_ms=-1,
        )


def test_flyer_intake_bypass_outcome_rejects_extra_field():
    from schemas import FlyerIntakeBypassOutcome  # noqa: E402
    with pytest.raises(ValidationError):
        FlyerIntakeBypassOutcome.model_validate({
            "type": "flyer_intake_bypass_outcome",
            "ts": _BYPASS_NOW.isoformat(),
            "chat_id_hash": _BYPASS_CHAT_HASH,
            "outcome": "routed_to_project",
            "rogue_field": "no",
        })


def test_intake_bypass_pair_routes_via_log_entry_discriminator():
    """Both new variants register in _KNOWN_LOG_ENTRY_TYPES via introspection
    and dispatch via TypeAdapter(LogEntry) by `type` tag."""
    from schemas import (  # noqa: E402
        FlyerIntakeBypassed,
        FlyerIntakeBypassOutcome,
        LogEntry,
        _KNOWN_LOG_ENTRY_TYPES,
    )
    assert "flyer_intake_bypassed" in _KNOWN_LOG_ENTRY_TYPES
    assert "flyer_intake_bypass_outcome" in _KNOWN_LOG_ENTRY_TYPES

    adapter = TypeAdapter(LogEntry)

    decision_row = {
        "type": "flyer_intake_bypassed",
        "ts": _BYPASS_NOW.isoformat(),
        "chat_id_hash": _BYPASS_CHAT_HASH,
        "bypass_reason": "edit_with_media",
        "has_media": True,
        "customer_state": "",
        "intake_session_status": "choosing_mode",
        "inbound_script": "latin",
    }
    parsed = adapter.validate_python(decision_row)
    assert isinstance(parsed, FlyerIntakeBypassed)

    outcome_row = {
        "type": "flyer_intake_bypass_outcome",
        "ts": _BYPASS_NOW.isoformat(),
        "chat_id_hash": _BYPASS_CHAT_HASH,
        "outcome": "routed_to_project",
        "project_id": "F0108",
        "handler_intercept": "",
        "elapsed_ms": 42,
    }
    parsed = adapter.validate_python(outcome_row)
    assert isinstance(parsed, FlyerIntakeBypassOutcome)


def test_intake_bypass_pair_in_schemas_all():
    """Both names exported via __all__ — backward-compat for
    `from schemas import *` consumers."""
    import schemas  # noqa: E402
    assert "FlyerIntakeBypassed" in schemas.__all__
    assert "FlyerIntakeBypassOutcome" in schemas.__all__


# ─────────────────────────────────────────────────────────────────
# P0 #2 — severity-tiered visual QA audit variants (Commit 6)
# ─────────────────────────────────────────────────────────────────


_NOW_UTC = datetime(2026, 5, 28, 14, 30, tzinfo=timezone.utc)
_SAMPLE_SHA = "a" * 64  # valid 64-hex-char sha256 placeholder


def test_flyer_qa_severity_classified_round_trip_pass():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    entry = FlyerQASeverityClassified(
        ts=_NOW_UTC,
        project_id="F0108",
        asset_id="A0001",
        severity="pass",
        blocker_count=0,
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["type"] == "flyer_qa_severity_classified"
    assert dumped["severity"] == "pass"
    assert dumped["blocker_count"] == 0
    assert dumped["classifier_version"] == "v1"  # default
    restored = FlyerQASeverityClassified.model_validate(dumped)
    assert restored == entry


def test_flyer_qa_severity_classified_warn_with_blockers():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    entry = FlyerQASeverityClassified(
        ts=_NOW_UTC,
        project_id="F0108",
        asset_id="",
        severity="warn",
        blocker_count=1,
        classifier_version="v1",
    )
    assert entry.severity == "warn"
    assert entry.asset_id == ""  # asset_id is optional


def test_flyer_qa_severity_classified_block_severity():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    entry = FlyerQASeverityClassified(
        ts=_NOW_UTC,
        project_id="F0109",
        asset_id="A0002",
        severity="block",
        blocker_count=3,
    )
    assert entry.severity == "block"


def test_flyer_qa_severity_classified_rejects_unknown_severity():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    with pytest.raises(ValidationError):
        FlyerQASeverityClassified(
            ts=_NOW_UTC,
            project_id="F0108",
            severity="critical",  # not in pass/warn/block
            blocker_count=1,
        )


def test_flyer_qa_severity_classified_rejects_bad_project_id():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    with pytest.raises(ValidationError):
        FlyerQASeverityClassified(
            ts=_NOW_UTC,
            project_id="X0001",  # F-prefix required
            severity="warn",
            blocker_count=1,
        )


def test_flyer_qa_severity_classified_rejects_extra_field():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    with pytest.raises(ValidationError):
        FlyerQASeverityClassified.model_validate({
            "type": "flyer_qa_severity_classified",
            "ts": _NOW_UTC.isoformat(),
            "project_id": "F0108",
            "severity": "warn",
            "blocker_count": 1,
            "extra_unexpected_field": "nope",
        })


def test_flyer_qa_severity_classified_blocker_count_bounds():
    from schemas import FlyerQASeverityClassified  # noqa: E402

    # ge=0 enforced
    with pytest.raises(ValidationError):
        FlyerQASeverityClassified(
            ts=_NOW_UTC, project_id="F0108", severity="pass", blocker_count=-1,
        )
    # le=50 enforced
    with pytest.raises(ValidationError):
        FlyerQASeverityClassified(
            ts=_NOW_UTC, project_id="F0108", severity="block", blocker_count=51,
        )


def test_flyer_warn_tier_delivered_round_trip():
    from schemas import FlyerWarnTierDelivered  # noqa: E402

    entry = FlyerWarnTierDelivered(
        ts=_NOW_UTC,
        project_id="F0108",
        asset_id="A0001",
        severity="warn",
        blockers=["visible wrong business/brand: Laksmi'S Kitchen"],
        customer_text_sha256=_SAMPLE_SHA,
    )
    dumped = entry.model_dump(mode="json")
    assert dumped["type"] == "flyer_warn_tier_delivered"
    assert dumped["severity"] == "warn"
    assert dumped["blockers"] == ["visible wrong business/brand: Laksmi'S Kitchen"]
    assert dumped["customer_text_sha256"] == _SAMPLE_SHA
    restored = FlyerWarnTierDelivered.model_validate(dumped)
    assert restored == entry


def test_flyer_warn_tier_delivered_requires_warn_severity():
    from schemas import FlyerWarnTierDelivered  # noqa: E402

    # pass / block not allowed — warn-tier delivery is by definition warn
    for bad in ("pass", "block"):
        with pytest.raises(ValidationError):
            FlyerWarnTierDelivered(
                ts=_NOW_UTC,
                project_id="F0108",
                asset_id="A0001",
                severity=bad,
                blockers=[],
                customer_text_sha256=_SAMPLE_SHA,
            )


def test_flyer_warn_tier_delivered_requires_asset_id():
    from schemas import FlyerWarnTierDelivered  # noqa: E402

    # asset_id has min_length=1 — delivery must identify which asset
    with pytest.raises(ValidationError):
        FlyerWarnTierDelivered(
            ts=_NOW_UTC,
            project_id="F0108",
            asset_id="",
            severity="warn",
            blockers=[],
            customer_text_sha256=_SAMPLE_SHA,
        )


def test_flyer_warn_tier_delivered_rejects_bad_sha256():
    from schemas import FlyerWarnTierDelivered  # noqa: E402

    with pytest.raises(ValidationError):
        FlyerWarnTierDelivered(
            ts=_NOW_UTC,
            project_id="F0108",
            asset_id="A0001",
            severity="warn",
            blockers=[],
            customer_text_sha256="not-a-sha",  # fails hex pattern
        )


def test_flyer_warn_tier_delivered_rejects_extra_field():
    from schemas import FlyerWarnTierDelivered  # noqa: E402

    with pytest.raises(ValidationError):
        FlyerWarnTierDelivered.model_validate({
            "type": "flyer_warn_tier_delivered",
            "ts": _NOW_UTC.isoformat(),
            "project_id": "F0108",
            "asset_id": "A0001",
            "severity": "warn",
            "blockers": [],
            "customer_text_sha256": _SAMPLE_SHA,
            "rogue": "no",
        })


def test_new_variants_route_via_log_entry_discriminator():
    """Adding the variants to the LogEntry Union must auto-register them
    in _KNOWN_LOG_ENTRY_TYPES (built by introspection at module import)
    AND let TypeAdapter(LogEntry) dispatch the right class by `type`."""
    from schemas import (  # noqa: E402
        FlyerQASeverityClassified,
        FlyerWarnTierDelivered,
        LogEntry,
        _KNOWN_LOG_ENTRY_TYPES,
    )

    assert "flyer_qa_severity_classified" in _KNOWN_LOG_ENTRY_TYPES
    assert "flyer_warn_tier_delivered" in _KNOWN_LOG_ENTRY_TYPES

    adapter = TypeAdapter(LogEntry)

    severity_row = {
        "type": "flyer_qa_severity_classified",
        "ts": _NOW_UTC.isoformat(),
        "project_id": "F0108",
        "asset_id": "A0001",
        "severity": "warn",
        "blocker_count": 1,
        "classifier_version": "v1",
    }
    parsed = adapter.validate_python(severity_row)
    assert isinstance(parsed, FlyerQASeverityClassified)

    delivered_row = {
        "type": "flyer_warn_tier_delivered",
        "ts": _NOW_UTC.isoformat(),
        "project_id": "F0108",
        "asset_id": "A0001",
        "severity": "warn",
        "blockers": ["visible wrong business/brand: Laksmi'S Kitchen"],
        "customer_text_sha256": _SAMPLE_SHA,
    }
    parsed = adapter.validate_python(delivered_row)
    assert isinstance(parsed, FlyerWarnTierDelivered)


def test_new_variants_export_via_schemas_all():
    """Both new variants must appear in schemas.__all__ so `from schemas
    import *` reaches them — protects against silent missing-export drift."""
    import schemas  # noqa: E402

    assert "FlyerQASeverityClassified" in schemas.__all__
    assert "FlyerWarnTierDelivered" in schemas.__all__
