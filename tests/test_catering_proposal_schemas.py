from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError, TypeAdapter

from schemas import (
    CateringProposalOption,
    CateringProposalSet,
    CateringProposalStore,
    CateringProposalsGenerated,
    CateringProposalGenerationFailed,
    CateringProposalSelected,
    CateringProposalSelectionFailed,
    LogEntry,
    CfRouterIntercepted,
)


def _now():
    return datetime(2026, 5, 13, 17, 0, tzinfo=timezone.utc)


def _proposal_options():
    return [
        CateringProposalOption(
            option_id="1", style_key="classic", tier="classic",
            item_names=["Veg Biryani"],
        ),
        CateringProposalOption(
            option_id="2", style_key="balanced", tier="balanced",
            item_names=["Paneer Tikka Kebab"],
        ),
    ]


def test_proposal_option_round_trip():
    opt = CateringProposalOption(
        option_id="1",
        style_key="balanced_mixed",
        tier="balanced",
        item_names=["Chicken Biryani", "Paneer Tikka Kebab (8 PCS)"],
    )
    assert opt.option_id == "1"
    assert opt.tier == "balanced"
    assert opt.item_names == ["Chicken Biryani", "Paneer Tikka Kebab (8 PCS)"]


def test_proposal_option_rejects_empty_items():
    with pytest.raises(ValidationError):
        CateringProposalOption(
            option_id="1",
            style_key="balanced_mixed",
            tier="balanced",
            item_names=[],
        )


def test_proposal_set_sent_requires_outbound_message_id():
    with pytest.raises(ValidationError):
        CateringProposalSet(
            proposal_set_id="CPS-L0014-000001",
            lead_id="L0014",
            status="SENT",
            created_at=_now(),
            sent_at=_now(),
            outbound_message_id="",
            source_message_id="msg1",
            request_text="send two options",
            options=_proposal_options(),
        )


def test_proposal_set_rejects_single_option():
    with pytest.raises(ValidationError):
        CateringProposalSet(
            proposal_set_id="CPS-L0014-000001",
            lead_id="L0014",
            status="DRAFT",
            created_at=_now(),
            source_message_id="msg1",
            options=[
                CateringProposalOption(
                    option_id="1", style_key="classic", tier="classic",
                    item_names=["Veg Biryani"],
                )
            ],
        )


def test_proposal_set_rejects_duplicate_option_ids():
    with pytest.raises(ValidationError):
        CateringProposalSet(
            proposal_set_id="CPS-L0014-000001",
            lead_id="L0014",
            status="DRAFT",
            created_at=_now(),
            source_message_id="msg1",
            options=[
                CateringProposalOption(
                    option_id="1", style_key="classic", tier="classic",
                    item_names=["Veg Biryani"],
                ),
                CateringProposalOption(
                    option_id="1", style_key="balanced", tier="balanced",
                    item_names=["Paneer Tikka Kebab"],
                ),
            ],
        )


def test_proposal_set_rejects_selected_option_id_not_in_options():
    with pytest.raises(ValidationError):
        CateringProposalSet(
            proposal_set_id="CPS-L0014-000001",
            lead_id="L0014",
            status="SELECTED",
            created_at=_now(),
            source_message_id="msg1",
            options=[
                CateringProposalOption(
                    option_id="1", style_key="classic", tier="classic",
                    item_names=["Veg Biryani"],
                ),
                CateringProposalOption(
                    option_id="3", style_key="premium", tier="premium",
                    item_names=["Gulab Jamun"],
                ),
            ],
            selected_option_id="2",
        )


def test_proposal_store_extra_ignored_for_forward_compat():
    store = CateringProposalStore.model_validate(
        {"schema_version": 1, "next_sequence": 2, "sets": [], "future": "ok"}
    )
    assert store.next_sequence == 2


@pytest.mark.parametrize(
    "entry",
    [
        CateringProposalsGenerated(
            type="catering_proposals_generated",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            option_count=2,
            outbound_message_id="wamid.1",
        ),
        CateringProposalGenerationFailed(
            type="catering_proposal_generation_failed",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            reason="unknown_menu_item",
            detail="Bad item",
        ),
        CateringProposalSelected(
            type="catering_proposal_selected",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            option_id="2",
            customer_message_id="msg2",
            finalize_exit_code=0,
        ),
        CateringProposalSelectionFailed(
            type="catering_proposal_selection_failed",
            ts=_now(),
            lead_id="L0014",
            proposal_set_id="CPS-L0014-000001",
            reason="finalize_exit_11",
            detail="quote mismatch",
        ),
    ],
)
def test_new_audit_variants_in_log_entry_union(entry):
    parsed = TypeAdapter(LogEntry).validate_python(entry.model_dump())
    assert parsed.type == entry.type


def test_cf_router_reason_accepts_proposal_selection():
    row = CfRouterIntercepted(
        type="cf_router_intercepted",
        ts=_now(),
        reason="f7_proposal_selection",
        chat_id="123@lid",
        subprocess_rc=0,
    )
    assert row.reason == "f7_proposal_selection"


@pytest.mark.parametrize("reason", [
    "flyer_intake_started",
    "flyer_intake",
    "flyer_intake_failed",
    "flyer_onboarding",
    "flyer_onboarding_failed",
    "flyer_starter_brief",            # BUG-FLYER-QA-003a (PR #102 hooks.py:188)
    "flyer_customer_not_active",      # BUG-FLYER-QA-003a (PR #105 hooks.py:201)
    "flyer_quota_blocked",
    "flyer_brand_asset_saved",
    "flyer_brand_asset_failed",
    "flyer_reference_scope_blocked",
    "flyer_reference_exact_edit_queued",
    "flyer_location_blocked",
    "flyer_account_command",
    "flyer_account_failed",
    "flyer_guest_order_started",
    "flyer_guest_order_failed",
])
def test_cf_router_reason_accepts_flyer_intercepts(reason):
    row = CfRouterIntercepted(
        type="cf_router_intercepted",
        ts=_now(),
        reason=reason,
        chat_id="918985741562@s.whatsapp.net",
        subprocess_rc=0,
    )
    assert row.reason == reason
