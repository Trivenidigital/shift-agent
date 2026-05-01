"""PR-CF1 forward-compat smoke test.

Pin the BLOCKER from plan v2 reviewer R1 H3: a legacy `catering-leads.json`
row written before PR-CF1 lands MUST decode under the new schema with all
4 new fields defaulted (selected_items=[], quote_total_usd=None,
customer_finalized_at=None, last_finalize_message_id=None).

Cross-platform — no fcntl, no subprocess, no I/O. Pure Pydantic validation.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src" / "platform"))

from schemas import CateringLead, CateringLeadStore  # noqa: E402


# Pre-CF1 schema row — these are the fields a lead written by
# create-catering-lead BEFORE this PR would have. Notably absent:
# selected_items, quote_total_usd, customer_finalized_at,
# last_finalize_message_id. The new schema should accept this row
# with the new fields defaulted.
LEGACY_LEAD_PRE_CF1 = {
    "lead_id": "L0042",
    "status": "AWAITING_OWNER_APPROVAL",
    "customer_phone": "+19045550199",
    "customer_name": "Legacy Customer",
    "raw_inquiry": "Need catering for 50 people Saturday",
    "original_message_id": "3EB0XXX_legacy",
    "created_at": "2026-04-29T10:00:00-04:00",
    "updated_at": "2026-04-29T10:00:00-04:00",
    "extracted": {
        "headcount": 50,
        "event_date": "2026-06-15",
        "event_time": None,
        "menu_preferences": [],
        "off_menu_items": [],
        "dietary_restrictions": ["vegetarian"],
        "delivery_or_pickup": "delivery",
        "budget_hint_usd": None,
        "notes": "",
    },
    "quote_text": "Hi Legacy Customer, thanks for your inquiry... (Ref: L0042)",
    "quote_version": 0,
    "owner_approval_code": "#A3F2X",
    "customer_replied": False,
}


# Pre-CF1 store with multiple legacy leads, no finalize fields anywhere
LEGACY_STORE_PRE_CF1 = {
    "leads": [
        LEGACY_LEAD_PRE_CF1,
        {
            **LEGACY_LEAD_PRE_CF1,
            "lead_id": "L0043",
            "status": "OWNER_APPROVED",
            "owner_approval_code": "#B4G3Y",
        },
        {
            **LEGACY_LEAD_PRE_CF1,
            "lead_id": "L0044",
            "status": "OWNER_EDITED",
            "owner_approval_code": "#C5H4Z",
        },
    ],
    "next_lead_seq": 45,
}


class TestForwardCompat:
    """Legacy catering-leads.json rows decode cleanly under PR-CF1 schema."""

    def test_legacy_lead_decodes_with_default_finalize_fields(self):
        lead = CateringLead.model_validate(LEGACY_LEAD_PRE_CF1)
        assert lead.lead_id == "L0042"
        assert lead.status == "AWAITING_OWNER_APPROVAL"
        # All 4 new fields should be at their defaults
        assert lead.selected_items == []
        assert lead.quote_total_usd is None
        assert lead.customer_finalized_at is None
        assert lead.last_finalize_message_id is None

    def test_legacy_owner_approved_lead_decodes(self):
        legacy = {**LEGACY_LEAD_PRE_CF1, "lead_id": "L0043", "status": "OWNER_APPROVED",
                  "owner_approval_code": "#B4G3Y"}
        lead = CateringLead.model_validate(legacy)
        assert lead.status == "OWNER_APPROVED"
        assert lead.selected_items == []
        assert lead.quote_total_usd is None

    def test_legacy_owner_edited_lead_decodes(self):
        legacy = {**LEGACY_LEAD_PRE_CF1, "lead_id": "L0044", "status": "OWNER_EDITED",
                  "owner_approval_code": "#C5H4Z"}
        lead = CateringLead.model_validate(legacy)
        assert lead.status == "OWNER_EDITED"
        assert lead.customer_finalized_at is None

    def test_legacy_store_decodes(self):
        store = CateringLeadStore.model_validate(LEGACY_STORE_PRE_CF1)
        assert len(store.leads) == 3
        for lead in store.leads:
            assert lead.selected_items == []
            assert lead.quote_total_usd is None
            assert lead.customer_finalized_at is None
            assert lead.last_finalize_message_id is None

    def test_round_trip_preserves_new_field_defaults(self):
        """A legacy lead serialized after decode should NOT mutate the
        original — defaults shouldn't add unexpected keys to disk if a future
        consumer is strict."""
        lead = CateringLead.model_validate(LEGACY_LEAD_PRE_CF1)
        dumped = lead.model_dump()
        # New fields ARE present in dump (defaults serialize), but their
        # values are the documented defaults
        assert dumped["selected_items"] == []
        assert dumped["quote_total_usd"] is None
        assert dumped["customer_finalized_at"] is None
        assert dumped["last_finalize_message_id"] is None


class TestPostFinalizeStateValidates:
    """A lead in CUSTOMER_FINALIZED status with finalize fields populated
    must validate (positive control for the validators)."""

    def test_customer_finalized_lead_validates(self):
        from datetime import datetime, timezone
        finalized = {
            **LEGACY_LEAD_PRE_CF1,
            "lead_id": "L0050",
            "status": "CUSTOMER_FINALIZED",
            "selected_items": [
                {"name": "Aloo Paratha", "qty": 2, "price_usd": 4},
                {"name": "Chicken Biryani", "qty": 1, "price_usd": 15},
            ],
            "quote_total_usd": 23,
            "customer_finalized_at": "2026-05-01T20:00:00+00:00",
            "last_finalize_message_id": "3EB0CFINAL_001",
        }
        lead = CateringLead.model_validate(finalized)
        assert lead.status == "CUSTOMER_FINALIZED"
        assert len(lead.selected_items) == 2
        assert lead.quote_total_usd == 23
        assert lead.last_finalize_message_id == "3EB0CFINAL_001"
