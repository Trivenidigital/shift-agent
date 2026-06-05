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
        r"\b\d{1,3}\.\d{2}\b",                         # plain decimal price-shape "8.99" (Codex r2)
        r"\b\d{1,3}/\d{2}\b",                          # slash price-shape "12/99" (Codex r2)
        r"%",                                         # any percent sign → discount claim
        r"\bpercent\b",
        r"\b(?:off|discount|save|deal|sale|flat|bogo)\b",   # NB: bare "free" excluded so
        r"\bfrom\s*\$?\s*\d",                          # "Gluten Free" passes; "Free Delivery"
        r"^\s*free\s*$",                               # lone "Free" is a claim (Codex r2); compounds pass
        r"\b(?:rs\.?|inr|usd|cad|gbp|eur)\s*\d",       # is still caught via "delivery" below
        # context-token price claims (Codex r6). NB: we do NOT reject a bare
        # trailing number ("Idli 8") — it is indistinguishable from a real dish
        # ("Chicken 65") or brand ("7 Up"); the planner prompt forbids prices, and
        # a price needs a context token / currency word to be a claim here.
        r"\b(?:price|priced|only|just|starting\s+at)\s*:?\s*\$?\s*\d",  # "Price 8", "Only 10"
        r"\b\d+\s+for\s+\d+\b",                         # "2 for 1" (BOGO/offer)
        r"\b\d+\s*(?:usd|inr|rs|rupees?|dollars?|bucks?|cents?)\b",     # "8 dollars", "8 USD"
        # date / day / time / schedule
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day|s)?\b",
        r"\b(?:today|tonight|tomorrow|daily|weekend|this\s+week|every\s+day)\b",
        r"\b\d{1,2}\s*(?:am|pm)\b",                    # "8 am"
        r"\b\d{1,2}\s*(?:to|[:.–—-])\s*\d{1,2}\b",     # "8-11", "8:30", "8 to 11" (Codex r2)
        r"\b\d{4}-\d{1,2}-\d{1,2}\b",                  # ISO date "2026-06-01" (Codex r3/r4)
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",           # slash date "6/1", "06/01/2026" (Codex r4)
        r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b",              # dotted date "01.06.2026" (Codex r4)
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
        # NB: bare open/opens/opening are intentionally NOT here — "open" is
        # context-dependent (an "open central area" is a LAYOUT instruction, not a
        # business-hours claim). It is classified separately by `_open_is_operational`
        # and folded into `is_hard_fact_claim` below, so compositional uses pass while
        # "now open" / "open daily" / "grand opening" stay caught. closes/closing/hours
        # remain unconditional — they have no compositional sense.
        r"\b(?:closes|closing|hours)\b",
        # superlative / guarantee price claims
        r"\b(?:lowest|cheapest|unbeatable|guaranteed?|#\s*1|number\s+one)\b",
        r"\b(?:best|low|great)\s+prices?\b",           # "best price(s)", "low prices" (Codex r2)
        # service / legal / delivery claims
        r"\b(?:delivery|shipping|no\s+charge|certified|licensed|insured|warranty|refund|cashback|free\s+(?:trial|delivery|shipping))\b",
        # service-assertion claims (Codex r5): availability/fulfillment offers
        r"\b(?:order\s+(?:online|now|here)|pickup|take[\s-]?out|dine[\s-]?in|curbside|drive[\s-]?thru|catering|reservations?|book\s+(?:now|online)|walk[\s-]?ins?)\b",
        # payment claims (Codex r1: "Cash Only", "Card Accepted", "UPI Accepted")
        r"\b(?:cash|card|upi|paytm|venmo|zelle|payment|payments|accepted|checkout)\b",
        r"\b(?:we\s+accept|pay\s+(?:by|with|here|now))\b",
        # contact-ish
        r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",          # US 10-digit phone
        r"\(\d{3}\)[\s.-]*\d{3,4}",                    # "(555) 1212" / "(555) 123-4567"
        r"\b\d{3}[-.\s]\d{4}\b",                        # 7-digit local "555-1212" / "555 1212" (Codex r3)
        r"\b\d{7,}\b",                                  # bare long digit run "5551212" (phone-shape)
        r"\+\d[\d\s().-]{6,}\d",                       # international phone (Codex r1: "+91 ...")
        r"\b(?:tel|call\s+us|call\s+now|contact\s+us|whatsapp|dm\s+us|text\s+us)\b",  # contact keywords (Codex r3)
        r"\b[\w-]+\.(?:com|net|org|io|co|in|shop|store|biz|info|app|us|uk)\b",  # bare domain "shop.com"
        r"www\.|https?://|@\w",                        # url / email-ish
    )
)

# Bare numbers alone (e.g. an item that is just "$" or "2025") are suspicious;
# but legitimate item names can contain digits ("7 Up", "Item 65"). We reject
# only when a digit co-occurs with a currency/percent/price token (covered
# above), not for incidental digits.

# ── context-aware "open" classification ─────────────────────────────────────
# "open" is the one claim token that has both a business-operational sense
# ("now open", "open daily", "grand opening") AND a benign compositional sense
# ("an open central area left clear for text", "open layout"). A bare \bopen\b
# regex over-blocked the compositional use (live false positive 2026-06-05:
# the textless-background brief said "an open central area left clear for text"
# and the firewall rejected it as an operational claim). We therefore classify
# "open"/"opens"/"opening" by adjacent context instead of flagging the bare word.
#
# Classification is ANCHORED-PHRASE, default BENIGN (Codex round-3 redesign
# 2026-06-05). The earlier window + broad-marker + default-operational design
# over-blocked compositional text: bare "day"/"days" matched "Memorial Day" (so
# "open layout for Memorial Day" — the real combo brief — was wrongly rejected),
# and bare "soft"/"24"/"to N" matched "soft background"/"24 inch margin"/"to 3
# inch margin". The fixed window could also miss a far operational marker. We now
# detect operational "open" EXPLICITLY; everything else (incl. bare "open" and
# every compositional phrase) is benign — no compositional-marker list, no window.
#
# Round-4 (Codex 2026-06-05): the token matches an optional "re"/"re-" prefix so
# "reopen(ing|ed|s)" is an open token ("grand reopening", "newly reopened"); the
# anchored tails were broadened to more genuine availability claims (weekends/
# weekdays/all week/seven days, for breakfast|brunch|lunch|dinner, at|from|until
# + clock|noon|midnight, 24/7|24 hours, opening soon, opening day) — each tail is
# SPECIFIC so no new over-block appears; and the bare "until N" clause was removed
# from (B) (it over-blocked "until 2 inches" and is redundant with the open tails).
_OPEN_TOKEN_RE = re.compile(r"\b(?:re[- ]?)?open(?:ing|ed|s)?\b", re.IGNORECASE)

# (A) Anchored open-phrase: an operational marker DIRECTLY adjacent to the open
# token. These are launch / business-hours assertions that only read one way.
# `_OPEN` is the open token (with optional re/re- prefix) reused in every tail.
_OPEN = r"(?:re[- ]?)?open(?:ing|ed|s)?"
_OPEN_ANCHORED_RE = re.compile(
    # launch / status word immediately before "open": "now open", "grand opening",
    # "we are open", "newly opened", "grand reopening", "newly reopened".
    r"\b(?:now|currently|we\s+are|we['’]re|grand|soft|newly|re)\s*[- ]?\s*"
    + _OPEN + r"\b"
    # "open" immediately followed by a hours/day/launch word: "open daily",
    # "open now", "open for business", "open every day", "open monday", …
    r"|\b" + _OPEN + r"\s+(?:now|today|tonight|daily|late|"
    r"every\s+day|all\s+day|all\s+week|"
    r"weekends?|weekdays?|seven\s+days|7\s+days|"
    r"mon|tue|wed|thu|fri|sat|sun|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    # "open for breakfast/brunch/lunch/dinner/business" — meal-service / business
    # availability. NB: "open for seating/text/plating" is NOT here ⇒ stays benign.
    r"|\b" + _OPEN + r"\s+for\s+(?:breakfast|brunch|lunch|dinner|business)\b"
    # "open at/from/until/till 9 | noon | midnight": a service-hours assertion.
    # "open at the center", "open until the margin" do NOT match (need a clock /
    # noon / midnight tail, not a word).
    r"|\b" + _OPEN + r"\s+(?:at|from|until|till|til)\s+(?:\d{1,2}\b|noon\b|midnight\b)"
    # "open 9am" / "open 9:00": a clock time right after the token.
    r"|\b" + _OPEN + r"\s+\d{1,2}\s*(?:am|pm|:\d{2})"
    # "open 24/7" / "open 24 hours" / "open 24 hrs". NB: "open ... 24 inch" never
    # matches (the tail requires /7 or hour(s)/hr(s), not a unit word).
    r"|\b" + _OPEN + r"\s+(?:24\s*/\s*7|24\s*hours?|24\s*hrs?)\b"
    # launch announcements: "opening soon", "opening day", "grand opening day".
    r"|\b(?:grand\s+)?" + _OPEN + r"\s+soon\b"
    r"|\b(?:grand\s+)?" + _OPEN + r"\s+day\b",
    re.IGNORECASE,
)

# (B) A standalone business-hours / time signal ANYWHERE in the text. Combined
# with the presence of any open token, this catches operational uses where the
# time sits a few words away from "open" ("store open with clean seating, open
# 9am-9pm") WITHOUT a fragile fixed window. ONLY unambiguous CLOCK shapes remain
# (round-4): a layout margin like "3 inch"/"24 inch" or "until 2 inches" never
# matches (no am/pm/colon, and the bare "until N" clause was removed).
_TIME_SIGNAL_RE = re.compile(
    r"\b\d{1,2}\s*(?:am|pm)\b"                       # "9am", "10 pm"
    r"|\b\d{1,2}:\d{2}\b"                            # "9:00"
    r"|\b\d{1,2}\s*(?:am|pm)\s*[-–—]\s*\d{1,2}\s*(?:am|pm)\b",  # "9am-9pm"
    re.IGNORECASE,
)


def _open_is_operational(text: str) -> bool:
    """Decide whether `text` uses "open" as a business-operational claim.

    Operational (→ flag) ONLY if BOTH:
      - the text contains an "open" token, AND
      - (A) an anchored open-phrase ("now open", "open daily", "grand opening",
        "open until 10", "open 9am") OR (B) a standalone clock-time / hours signal
        anywhere ("9am-9pm", "until 10") co-occurs with that token.

    Everything else is BENIGN (default): bare "open", "open central area",
    "open layout for Memorial Day", "soft background", "24 inch margin", etc. No
    compositional-marker list and no window are needed — only explicit operational
    shapes flag, so compositional phrasing can never be mistaken for a claim."""
    if not text or not _OPEN_TOKEN_RE.search(text):
        return False
    if _OPEN_ANCHORED_RE.search(text):
        return True
    if _TIME_SIGNAL_RE.search(text):
        return True
    return False


def is_hard_fact_claim(text: str) -> bool:
    """True if `text` smuggles a hard-fact-class claim (→ must be rejected)."""
    t = (text or "").strip()
    if not t:
        return True  # empty is not a usable item name → drop
    if any(p.search(t) for p in _CLAIM_PATTERNS):
        return True
    # "open" is handled separately (anchored-phrase) because it has a benign
    # compositional sense the blanket patterns must not over-block.
    if _open_is_operational(t):
        return True
    return False


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
