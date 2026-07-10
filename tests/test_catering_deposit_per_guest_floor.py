"""BL-CATER-03/13 — per-guest deposit plausibility floor.

The unscaled-basket bug produces a qty=1 sample basket (~$50) regardless of
headcount, so per-guest spend collapses toward cents ($52 / 200 = $0.26/guest).
The floor refuses to mint a deposit in that regime — a wrong deposit is worse
than a missed one — while allowing legitimate budget catering above the floor.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_CATERING_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering"
if str(_CATERING_DIR) not in sys.path:
    sys.path.insert(0, str(_CATERING_DIR))

from schemas import (  # noqa: E402
    CateringLead,
    CateringLeadExtractedFields,
    CateringConfig,
    CommerceConfig,
    Config,
    CustomerConfig,
    OwnerConfig,
    LimitsConfig,
    AlertingConfig,
    BackupConfig,
)
from deposit import _should_mint_deposit, _per_guest_usd  # noqa: E402

TS = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


def _cfg(deposit_pct: float = 0.25, threshold: int = 50, min_per_guest: float = 3.0) -> Config:
    return Config(
        schema_version=1,
        customer=CustomerConfig(
            name="Test", location_id="loc_test", timezone="America/New_York", languages=["en"],
        ),
        owner=OwnerConfig(name="Owner", phone="+15551234567", self_chat_jid=""),
        limits=LimitsConfig(
            max_outbound_per_day=2, max_outbound_per_minute=30,
            pending_proposal_ttl_hours=4, per_message_timeout_sec=120,
            send_failure_retry_count=1,
        ),
        alerting=AlertingConfig(
            pushover_user_key="test_user", pushover_app_token="test_token",
            healthchecks_io_url="", email="",
        ),
        backup=BackupConfig(gpg_recipient_email="test@example.com", s3_bucket="", retention_days=30),
        catering=CateringConfig(
            enabled=True, deposit_pct=deposit_pct,
            deposit_threshold_guests=threshold, min_per_guest_usd=min_per_guest,
        ),
        commerce=CommerceConfig(minimum_deposit_cents=500),
    )


def _lead(headcount: int | None = 100, quote_total_usd: int | None = 600, intent_id: str = "") -> CateringLead:
    return CateringLead(
        lead_id="L0007", status="SENT_TO_CUSTOMER", customer_phone="+15551234567",
        customer_name="Lakshmi", raw_inquiry="x", original_message_id="m",
        created_at=TS, updated_at=TS, quote_text="x", quote_total_usd=quote_total_usd,
        extracted=CateringLeadExtractedFields(headcount=headcount, event_date="2026-06-15"),
        deposit_payment_intent_id=intent_id,
    )


def test_schema_default_floor_is_three():
    assert CateringConfig().min_per_guest_usd == 3.0


def test_per_guest_usd_math():
    assert _per_guest_usd(600, 100) == 6.0
    assert _per_guest_usd(52, 200) == 0.26


def test_refuses_unscaled_basket():
    # $52 basket for 200 guests = $0.26/guest — the unscaled-basket signature.
    assert _should_mint_deposit(_cfg(), _lead(headcount=200, quote_total_usd=52)) is False


def test_refuses_just_below_floor():
    # $299 / 100 = $2.99/guest, below the $3 floor.
    assert _should_mint_deposit(_cfg(), _lead(headcount=100, quote_total_usd=299)) is False


def test_allows_plausible_per_guest():
    # $600 / 50 = $12/guest.
    assert _should_mint_deposit(_cfg(), _lead(headcount=50, quote_total_usd=600)) is True


def test_allows_legit_budget_catering_above_floor():
    # $6/guest (the deployed happy-path fixture) stays mintable — floor is $3, not $8.
    assert _should_mint_deposit(_cfg(), _lead(headcount=100, quote_total_usd=600)) is True


def test_floor_boundary_is_allow():
    # exactly $3.00/guest ($300 / 100): `< floor` is False, so it still mints.
    assert _should_mint_deposit(_cfg(), _lead(headcount=100, quote_total_usd=300)) is True


def test_floor_is_operator_tunable():
    # operator raises the floor to $10 — the $6/guest order now refuses.
    assert _should_mint_deposit(_cfg(min_per_guest=10.0), _lead(headcount=100, quote_total_usd=600)) is False
