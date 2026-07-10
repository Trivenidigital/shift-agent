"""Catering deposit caller — pure helper functions.

The orchestration lives in src/agents/catering/scripts/catering-mint-deposit
(deterministic Python script invoked as a subprocess by apply-catering-owner-
decision after SENT_TO_CUSTOMER persists). This module hosts the pure-function
helpers that the script + tests use:

- _should_mint_deposit: threshold predicate (cfg + lead)
- _compute_deposit_amount_cents: rounding logic
- _render_customer_reply: configured-template customer copy
- _render_unconfigured_reply: byte-exact fail-closed copy
- _format_pct: deposit-percentage display formatting

Design: tasks/hermes-commerce-slice2-catering-deposit-caller-design.md
Slice-2 commit 2 of 3 (catering-mint-deposit script + helpers).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas import CateringLead, CommerceConfig as _, Config


# Byte-exact fail-closed copy (mirrors Flyer "Payment link is not configured yet"
# precedent at src/agents/flyer/guest_order.py:231). Tests assert byte-exact.
UNCONFIGURED_TEMPLATE_REPLY = (
    "Payment link is not configured yet. We'll send it when it's ready."
)

# Bridge-filter bypass prefix — mirrors the deployed pattern at
# apply-catering-owner-decision:719. The WhatsApp bridge filter at
# bridge.js:133 lets messages matching this prefix through without
# the outbound-chatter heuristic dropping them.
BRIDGE_PREFIX = "⚕ *Catering Agent*\n────────────\n"


def _per_guest_usd(quote_total_usd: int, headcount: int) -> float:
    """Dollars-per-guest implied by the quote. headcount must be > 0 — callers
    guarantee this after the headcount/threshold checks in _should_mint_deposit."""
    return quote_total_usd / headcount


def _should_mint_deposit(cfg, lead) -> bool:
    """Threshold predicate per design §2 'Threshold logic'.

    Returns True only when ALL of:
    - cfg.catering.deposit_pct > 0 (kill switch off)
    - lead.extracted.headcount is set
    - headcount >= cfg.catering.deposit_threshold_guests (inclusive — Reviewer B MEDIUM-1)
    - lead.quote_total_usd is set and > 0
    - per-guest spend is plausible (BL-CATER-03 — fail-closed unscaled-basket guard)
    - lead has not already been minted (idempotent skip via deposit_payment_intent_id)
    """
    if cfg.catering.deposit_pct <= 0:
        return False
    if lead.extracted is None or lead.extracted.headcount is None:
        return False
    if lead.extracted.headcount < cfg.catering.deposit_threshold_guests:
        return False
    if lead.quote_total_usd is None or lead.quote_total_usd <= 0:
        return False
    # BL-CATER-03: per-guest plausibility floor. When the total was never scaled to
    # the guest count (unscaled-basket bug), per-guest collapses toward cents; a wrong
    # deposit is worse than a missed one, so refuse. The owner card surfaces the same
    # warning so the owner can edit + re-finalize. headcount > 0 guaranteed above.
    min_per_guest = getattr(cfg.catering, "min_per_guest_usd", 3.0)
    if _per_guest_usd(lead.quote_total_usd, lead.extracted.headcount) < min_per_guest:
        return False
    if lead.deposit_payment_intent_id:
        return False
    return True


def _compute_deposit_amount_cents(quote_total_usd: int, deposit_pct: float) -> int:
    """Round-half-up cents from dollar quote × fraction.

    quote_total_usd is dollars (int). deposit_pct is 0.0..1.0 float.
    Example: $601 × 0.25 = 15025 cents = $150.25.
    Python's round() uses banker's rounding; for money we want round-half-up.
    """
    raw = quote_total_usd * 100 * deposit_pct
    # Banker's rounding correction: add 0.5 then int-truncate is the textbook
    # half-up rule but yields wrong results on negatives; quote_total_usd is
    # always >= 0 so safe.
    return int(raw + 0.5)


def _format_pct(deposit_pct: float) -> str:
    """Format deposit percentage for customer copy.

    0.25  -> "25%"
    0.125 -> "12.5%"
    0.05  -> "5%"
    0.5   -> "50%"
    """
    pct = deposit_pct * 100
    if pct == int(pct):
        return f"{int(pct)}%"
    # Strip trailing zeros / use general formatting
    return f"{pct:g}%"


def _render_customer_reply(lead, deposit_amount_cents: int, deposit_pct: float, url: str) -> str:
    """Configured-template customer copy.

    Per Reviewer B BLOCKER-1: anchor on customer-recognizable context.
    Priority:
    1. Event-anchor (event_date + headcount BOTH known)
    2. Name-anchor (customer_name known)
    3. Generic (last resort)

    NEVER includes lead_id, commerce_order_id, commerce_payment_intent_id.
    Amount-first; percentage as parenthetical (Reviewer B LOW-1).
    """
    amount_str = f"${deposit_amount_cents / 100:.2f}"
    pct_str = _format_pct(deposit_pct)

    event_date = lead.extracted.event_date if lead.extracted else None
    headcount = lead.extracted.headcount if lead.extracted else None
    customer_name = (lead.customer_name or "").strip()

    # PR reviewer B-LOW-1: softer "Thanks for confirming!" lead-in across all
    # three branches for consistent first-customer tone.
    if event_date and headcount:
        return (
            f"Thanks for confirming! To finalize your {headcount}-guest event on "
            f"{event_date}, please pay {amount_str} ({pct_str} of total): {url}"
        )
    if customer_name:
        return (
            f"Thanks, {customer_name}! To confirm your booking, "
            f"please pay {amount_str} ({pct_str} of total): {url}"
        )
    return (
        f"Thanks for confirming! To finalize your catering booking, "
        f"please pay {amount_str} ({pct_str} of total): {url}"
    )


def _render_unconfigured_reply(lead) -> str:
    """Byte-exact unconfigured-template fallback.

    Operator hasn't set cfg.commerce.payment_checkout_url_template; the
    intent was minted but checkout_url is empty. Customer-visible copy is
    fail-closed: a generic "we'll send it when ready" message, NOT a
    bare/broken URL.

    Mirrors Flyer's pattern at src/agents/flyer/guest_order.py:231.
    Tests assert byte-exact equality.
    """
    return UNCONFIGURED_TEMPLATE_REPLY
