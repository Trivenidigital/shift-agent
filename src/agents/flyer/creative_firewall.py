"""Hard-fact firewall for the bounded creative planner — slice 3.

Design: tasks/flyer-bounded-creative-planner-contract-design.md §6.

The planner produces inferred candidates (item names). They may become facts
ONLY by passing through this firewall (`creative_planner.materialize_inferred`
calls `clear()`). The firewall is the truth-guard: it drops any candidate whose
text smuggles a HARD-FACT-CLASS CLAIM — a price/discount, a date/schedule, a
superlative price claim, or a service/legal/payment/delivery claim — because the
business never asserted it (§6b: the #1 risk is a claim disguised as an "item
name", e.g. "Free Delivery", "Open Daily", "20% Off").

Fail-closed: anything that looks like a hard-fact-class claim is rejected, even
at the cost of dropping a borderline-but-legitimate item. Hard facts come ONLY
from the grounded extractor; the creative path can never introduce them.
"""
from __future__ import annotations

import re
from typing import Sequence

# ── hard-fact-class claim patterns (case-insensitive) ───────────────────────
# A candidate item-name matching ANY of these is rejected. These mirror the
# §6b scanner classes: money, date/time/schedule, superlative price claims,
# and service/legal/payment/delivery claims.
_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    # money / price / discount
    re.compile(p, re.IGNORECASE)
    for p in (
        # money / price / discount
        r"\$\s*\d",                                   # "$8", "$ 8.99"
        r"[₹€£¥]\s*\d",                                # non-$ currency symbols (Codex r1: "₹8.99")
        r"%",                                         # any percent sign → discount claim
        r"\bpercent\b",
        r"\b(?:off|discount|save|deal|sale|flat|bogo)\b",   # NB: bare "free" excluded so
        r"\bfrom\s*\$?\s*\d",                          # "Gluten Free" passes; "Free Delivery"
        r"\b(?:rs\.?|inr|usd|cad|gbp|eur)\s*\d",       # is still caught via "delivery" below
        # date / day / time / schedule
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day|s)?\b",
        r"\b(?:today|tonight|tomorrow|daily|weekend|this\s+week|every\s+day)\b",
        r"\b\d{1,2}\s*(?:am|pm)\b",                    # "8 am"
        r"\b\d{1,2}\s*[:.–-]\s*\d{1,2}\b",             # "8-11", "8:30"
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
        r"\b(?:open|opens|opening|closes|closing|hours)\b",
        # superlative / guarantee price claims
        r"\b(?:lowest|cheapest|best\s+price|unbeatable|guaranteed?|#\s*1|number\s+one)\b",
        # service / legal / delivery claims
        r"\b(?:delivery|shipping|no\s+charge|certified|licensed|insured|warranty|refund|cashback|free\s+(?:trial|delivery|shipping))\b",
        # payment claims (Codex r1: "Cash Only", "Card Accepted", "UPI Accepted")
        r"\b(?:cash|card|upi|paytm|venmo|zelle|payment|payments|accepted|checkout)\b",
        r"\b(?:we\s+accept|pay\s+(?:by|with|here|now))\b",
        # contact-ish
        r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",          # US phone
        r"\+\d[\d\s().-]{6,}\d",                       # international phone (Codex r1: "+91 ...")
        r"\b[\w-]+\.(?:com|net|org|io|co|in|shop|store|biz|info|app|us|uk)\b",  # bare domain "shop.com"
        r"www\.|https?://|@\w",                        # url / email-ish
    )
)

# Bare numbers alone (e.g. an item that is just "$" or "2025") are suspicious;
# but legitimate item names can contain digits ("7 Up", "Item 65"). We reject
# only when a digit co-occurs with a currency/percent/price token (covered
# above), not for incidental digits.


def is_hard_fact_claim(text: str) -> bool:
    """True if `text` smuggles a hard-fact-class claim (→ must be rejected)."""
    t = (text or "").strip()
    if not t:
        return True  # empty is not a usable item name → drop
    return any(p.search(t) for p in _CLAIM_PATTERNS)


class CreativeFirewall:
    """Clears planner candidates, dropping any that carry a hard-fact-class claim.

    The contract `materialize_inferred` relies on: `clear(candidates)` returns the
    subset that is SAFE to render as `hermes_inferred` (item names only)."""

    def clear(self, candidates: Sequence) -> list:
        cleared = []
        for cand in candidates:
            # field-rule: the creative path only carries item-name candidates;
            # anything else is out of contract and dropped.
            if getattr(cand, "kind", None) != "item":
                continue
            if is_hard_fact_claim(getattr(cand, "value", "")):
                continue
            cleared.append(cand)
        return cleared

    def rejected(self, candidates: Sequence) -> list:
        """The dropped candidates (for observability/tests)."""
        return [
            c for c in candidates
            if getattr(c, "kind", None) != "item" or is_hard_fact_claim(getattr(c, "value", ""))
        ]
