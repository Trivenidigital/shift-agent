"""Customer-copy invariant lint tests (§5 of design doc).

Asserts the rendered customer copy NEVER contains:
- Internal terminology (Commerce, intent, primitive, Hermes, stripe, razorpay)
- Server-side identifiers (lead_id, commerce_order_id, commerce_payment_intent_id)

Asserts it DOES contain the customer-friendly anchors per Reviewer B BLOCKER-1.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_CATERING_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering"
if str(_CATERING_DIR) not in sys.path:
    sys.path.insert(0, str(_CATERING_DIR))

from schemas import CateringLead, CateringLeadExtractedFields
from deposit import _render_customer_reply, _render_unconfigured_reply, UNCONFIGURED_TEMPLATE_REPLY


TS = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
URL = "https://pay.example.com/?o=CO00042&intent=CPI00042"

# Forbidden substrings in customer-visible copy (case-insensitive)
_FORBIDDEN_INTERNAL = (
    "Commerce", "intent", "primitive", "Hermes",
    "stripe", "razorpay", "upi", "zelle",
)
# Forbidden internal identifiers — specific patterns that look like server-side IDs
_FORBIDDEN_ID_PATTERNS = (
    r"\bL\d{4,}\b",      # CateringLead id (L0007)
    r"\bCO\d{5,}\b",     # CommerceOrder id (CO00042)
    r"\bCPI\d{5,}\b",    # CommercePaymentIntent id (CPI00042)
)


_URL_RE = re.compile(r"https?://\S+")


def _strip_urls(reply: str) -> str:
    """Strip URL portions before checking customer-visible prose for forbidden
    terms / IDs. The URL is operator-configured (cfg.commerce.payment_checkout_url_template)
    and legitimately contains tokens like {order_id} / {intent_id}. The invariant
    applies to the prose around the URL, not the URL string itself."""
    return _URL_RE.sub("<URL>", reply)


def _check_forbidden_internal_terms(reply: str) -> None:
    """Raise AssertionError if any forbidden internal term appears in PROSE
    (URLs excluded — operator owns the URL template)."""
    prose = _strip_urls(reply)
    lower = prose.lower()
    for term in _FORBIDDEN_INTERNAL:
        if re.search(rf"\b{re.escape(term.lower())}\b", lower):
            raise AssertionError(
                f"forbidden internal term {term!r} found in customer reply prose: {prose!r}"
            )


def _check_forbidden_id_patterns(reply: str) -> None:
    """Raise AssertionError if any server-side identifier pattern appears in
    PROSE (URLs excluded — operator owns the URL template)."""
    prose = _strip_urls(reply)
    for pat in _FORBIDDEN_ID_PATTERNS:
        match = re.search(pat, prose)
        if match:
            raise AssertionError(
                f"forbidden id-pattern {pat!r} matched {match.group()!r} in reply prose: {prose!r}"
            )


def _make_lead(
    lead_id: str = "L0007",
    headcount=100,
    event_date="2026-06-15",
    customer_name="Lakshmi",
    quote_total_usd: int = 600,
) -> CateringLead:
    return CateringLead(
        lead_id=lead_id,
        status="SENT_TO_CUSTOMER",
        customer_phone="+15551234567",
        customer_name=customer_name,
        raw_inquiry="x",
        original_message_id="m",
        created_at=TS,
        updated_at=TS,
        quote_text="x",
        quote_total_usd=quote_total_usd,
        extracted=CateringLeadExtractedFields(headcount=headcount, event_date=event_date),
    )


# ─────────────────────────────────────────────────────────────────
# Configured-template copy — 5 fixture variants
# ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lead_id,headcount,event_date,customer_name,quote", [
    ("L0007", 100, "2026-06-15", "Lakshmi", 600),
    ("L0023", 50, "2026-07-01", "Anjali Iyer", 400),
    ("L0099", 200, "2026-12-31", "Suresh Patel", 1200),
    ("L1234", None, None, "Vikram", 800),          # name-anchor fallback
    ("L9999", None, None, None, 1000),             # generic last-resort
])
def test_configured_copy_excludes_internal_terms(
    lead_id, headcount, event_date, customer_name, quote,
):
    lead = _make_lead(
        lead_id=lead_id, headcount=headcount, event_date=event_date,
        customer_name=customer_name, quote_total_usd=quote,
    )
    deposit_cents = quote * 100 // 4  # 25%
    reply = _render_customer_reply(lead, deposit_cents, 0.25, URL)
    _check_forbidden_internal_terms(reply)


@pytest.mark.parametrize("lead_id,headcount,event_date,customer_name", [
    ("L0007", 100, "2026-06-15", "Lakshmi"),
    ("L0023", 50, "2026-07-01", "Anjali Iyer"),
    ("L1234", None, None, "Vikram"),
    ("L9999", None, None, None),
])
def test_configured_copy_excludes_server_side_ids(
    lead_id, headcount, event_date, customer_name,
):
    """Reviewer B BLOCKER-1: lead_id (L0007) MUST NOT appear in customer copy."""
    lead = _make_lead(
        lead_id=lead_id, headcount=headcount, event_date=event_date,
        customer_name=customer_name,
    )
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    _check_forbidden_id_patterns(reply)


def test_configured_copy_contains_dollar_amount():
    lead = _make_lead()
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "$150.00" in reply


def test_configured_copy_contains_url():
    lead = _make_lead()
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert URL in reply


def test_configured_copy_contains_percentage_parenthetical():
    lead = _make_lead()
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "(25% of total)" in reply


def test_configured_copy_event_anchor_preferred():
    lead = _make_lead(headcount=100, event_date="2026-06-15", customer_name="Lakshmi")
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "100-guest event on 2026-06-15" in reply


def test_configured_copy_name_anchor_fallback():
    lead = _make_lead(headcount=None, event_date=None, customer_name="Lakshmi")
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "Thanks, Lakshmi!" in reply
    assert "guest event" not in reply


def test_configured_copy_generic_last_resort():
    lead = _make_lead(headcount=None, event_date=None, customer_name=None)
    reply = _render_customer_reply(lead, 15000, 0.25, URL)
    assert "your catering booking" in reply
    assert "Thanks," not in reply


# ─────────────────────────────────────────────────────────────────
# Unconfigured-template copy — byte-exact
# ─────────────────────────────────────────────────────────────────

def test_unconfigured_copy_byte_exact_for_any_lead():
    lead_a = _make_lead()
    lead_b = _make_lead(lead_id="L0023", customer_name=None, event_date=None)
    assert _render_unconfigured_reply(lead_a) == UNCONFIGURED_TEMPLATE_REPLY
    assert _render_unconfigured_reply(lead_b) == UNCONFIGURED_TEMPLATE_REPLY
    assert _render_unconfigured_reply(lead_a) == _render_unconfigured_reply(lead_b)


def test_unconfigured_copy_excludes_internal_terms():
    reply = _render_unconfigured_reply(_make_lead())
    _check_forbidden_internal_terms(reply)


def test_unconfigured_copy_excludes_server_side_ids():
    reply = _render_unconfigured_reply(_make_lead())
    _check_forbidden_id_patterns(reply)


def test_unconfigured_copy_does_NOT_contain_url():
    reply = _render_unconfigured_reply(_make_lead())
    assert "://" not in reply
    assert "http" not in reply
