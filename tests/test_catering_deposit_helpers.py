"""Pure-function helper tests for src/agents/catering/deposit.py.

No subprocess, no state files. Just the threshold predicate + render functions.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure src/agents/catering on path so `from deposit import ...` works
_CATERING_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering"
if str(_CATERING_DIR) not in sys.path:
    sys.path.insert(0, str(_CATERING_DIR))

from schemas import (
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

from deposit import (
    _should_mint_deposit,
    _compute_deposit_amount_cents,
    _render_customer_reply,
    _render_unconfigured_reply,
    _format_pct,
    BRIDGE_PREFIX,
    UNCONFIGURED_TEMPLATE_REPLY,
)


TS = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


def _cfg(deposit_pct: float = 0.25, threshold: int = 50, min_dep_cents: int = 500) -> Config:
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
            enabled=True,
            deposit_pct=deposit_pct,
            deposit_threshold_guests=threshold,
        ),
        commerce=CommerceConfig(minimum_deposit_cents=min_dep_cents),
    )


def _lead(
    headcount: int | None = 100,
    quote_total_usd: int | None = 600,
    event_date: str | None = "2026-06-15",
    customer_name: str | None = "Lakshmi",
    deposit_payment_intent_id: str = "",
) -> CateringLead:
    return CateringLead(
        lead_id="L0007",
        status="SENT_TO_CUSTOMER",
        customer_phone="+15551234567",
        customer_name=customer_name,
        raw_inquiry="x",
        original_message_id="m",
        created_at=TS,
        updated_at=TS,
        quote_text="x",
        quote_total_usd=quote_total_usd,
        extracted=CateringLeadExtractedFields(
            headcount=headcount, event_date=event_date,
        ),
        deposit_payment_intent_id=deposit_payment_intent_id,
    )


# ─────────────────────────────────────────────────────────────────
# _should_mint_deposit truth table
# ─────────────────────────────────────────────────────────────────

def test_should_mint_happy_path():
    assert _should_mint_deposit(_cfg(), _lead()) is True


def test_should_mint_kill_switch_zero_pct():
    assert _should_mint_deposit(_cfg(deposit_pct=0), _lead()) is False


def test_should_mint_kill_switch_negative_pct_rejected_at_construction():
    """CateringConfig.deposit_pct has ge=0.0 so negative values can't even be
    constructed. The kill-switch test is `== 0` (covered above)."""
    with pytest.raises(Exception):  # ValidationError
        _cfg(deposit_pct=-0.1)


def test_should_mint_below_threshold():
    assert _should_mint_deposit(_cfg(threshold=50), _lead(headcount=49)) is False


def test_should_mint_at_threshold_inclusive():
    """Reviewer B MEDIUM-1: threshold is inclusive (headcount >= threshold triggers)."""
    assert _should_mint_deposit(_cfg(threshold=50), _lead(headcount=50)) is True


def test_should_mint_just_above_threshold():
    assert _should_mint_deposit(_cfg(threshold=50), _lead(headcount=51)) is True


def test_should_mint_headcount_none():
    assert _should_mint_deposit(_cfg(), _lead(headcount=None)) is False


def test_should_mint_quote_total_none():
    assert _should_mint_deposit(_cfg(), _lead(quote_total_usd=None)) is False


def test_should_mint_quote_total_zero():
    assert _should_mint_deposit(_cfg(), _lead(quote_total_usd=0)) is False


def test_should_mint_already_minted_is_idempotent_skip():
    """Idempotency: if deposit_payment_intent_id is non-empty, refuse to re-mint."""
    assert _should_mint_deposit(_cfg(), _lead(deposit_payment_intent_id="CPI00042")) is False


# ─────────────────────────────────────────────────────────────────
# _compute_deposit_amount_cents
# ─────────────────────────────────────────────────────────────────

def test_compute_amount_typical():
    """$600 × 25% = $150.00 = 15000 cents."""
    assert _compute_deposit_amount_cents(600, 0.25) == 15000


def test_compute_amount_fractional_cents():
    """$601 × 25% = $150.25 = 15025 cents (no rounding needed)."""
    assert _compute_deposit_amount_cents(601, 0.25) == 15025


def test_compute_amount_round_half_up():
    """$1 × 5% = $0.05 = 5 cents."""
    assert _compute_deposit_amount_cents(1, 0.05) == 5


def test_compute_amount_round_down_to_zero():
    """$1 × 0.1% = $0.001 → rounds to 0."""
    assert _compute_deposit_amount_cents(1, 0.001) == 0


def test_compute_amount_fractional_pct_125_percent():
    """PR reviewer A MEDIUM-2: lock _compute + _format both at deposit_pct=0.125.

    $800 × 12.5% = $100.00 = 10000 cents.
    """
    assert _compute_deposit_amount_cents(800, 0.125) == 10000
    assert _format_pct(0.125) == "12.5%"


# ─────────────────────────────────────────────────────────────────
# _format_pct
# ─────────────────────────────────────────────────────────────────

def test_format_pct_integer():
    assert _format_pct(0.25) == "25%"
    assert _format_pct(0.50) == "50%"
    assert _format_pct(0.05) == "5%"


def test_format_pct_fractional():
    assert _format_pct(0.125) == "12.5%"
    assert _format_pct(0.075) == "7.5%"


# ─────────────────────────────────────────────────────────────────
# _render_customer_reply — three branches
# ─────────────────────────────────────────────────────────────────

URL = "https://pay.example.com/?o=CO00042"


def test_render_event_anchor_preferred():
    lead = _lead(headcount=100, event_date="2026-06-15", customer_name="Lakshmi")
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "100-guest event on 2026-06-15" in reply
    assert "$150.00" in reply
    assert "(25% of total)" in reply
    assert URL in reply
    # PR reviewer B-LOW-1 softening: lead-in across all branches
    assert reply.startswith("Thanks")


def test_render_name_anchor_fallback_when_no_event():
    lead = _lead(headcount=None, event_date=None, customer_name="Lakshmi")
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "Thanks, Lakshmi!" in reply
    assert "100-guest" not in reply  # no event-anchor copy
    assert "$150.00" in reply
    assert URL in reply


def test_render_generic_last_resort():
    lead = _lead(headcount=None, event_date=None, customer_name=None)
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "your catering booking" in reply
    assert "100-guest" not in reply
    assert "$150.00" in reply
    assert URL in reply
    # PR reviewer B-LOW-1 softening: all branches start with "Thanks"
    assert reply.startswith("Thanks")


def test_render_event_anchor_when_only_headcount():
    """If only one of (event_date, headcount) is set, fall back to name-anchor."""
    lead = _lead(headcount=100, event_date=None, customer_name="Lakshmi")
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "100-guest" not in reply  # event-anchor needs BOTH
    assert "Thanks, Lakshmi" in reply


def test_render_customer_name_whitespace_treated_as_missing():
    lead = _lead(headcount=None, event_date=None, customer_name="   ")
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "Thanks," not in reply
    assert "your catering booking" in reply


def test_render_amount_first_pct_parenthetical():
    """Reviewer B LOW-1: amount-first; percentage as parenthetical."""
    lead = _lead()
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    # "$150.00" appears BEFORE "(25% of total)"
    assert reply.index("$150.00") < reply.index("(25% of total)")


# ─────────────────────────────────────────────────────────────────
# _render_unconfigured_reply — byte-exact
# ─────────────────────────────────────────────────────────────────

def test_render_unconfigured_byte_exact():
    lead = _lead()
    reply = _render_unconfigured_reply(lead)
    assert reply == "Payment link is not configured yet. We'll send it when it's ready."
    assert reply == UNCONFIGURED_TEMPLATE_REPLY


def test_render_unconfigured_invariant_to_lead_state():
    """Unconfigured copy doesn't depend on lead fields — same bytes for any lead."""
    reply_a = _render_unconfigured_reply(_lead(headcount=100, customer_name="Lakshmi"))
    reply_b = _render_unconfigured_reply(_lead(headcount=None, customer_name=None))
    assert reply_a == reply_b


# ─────────────────────────────────────────────────────────────────
# BRIDGE_PREFIX
# ─────────────────────────────────────────────────────────────────

def test_bridge_prefix_matches_deployed_pattern():
    """Reviewer A insertion-point evidence: must match apply-catering-owner-
    decision:719's prefix so bridge.js:133 filter lets the message through."""
    assert BRIDGE_PREFIX.startswith("⚕ *Catering Agent*\n")
    assert "─" in BRIDGE_PREFIX  # contains box-drawing horizontal
