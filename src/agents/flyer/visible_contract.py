"""flyer_visible_contract.py — deterministic post-render visible-contract referee.

Hermes is the creative brain (brief / visual_direction / intent). This module is
the *referee*: given the VISIBLE text read back from the rendered flyer plus the
locked facts, it returns a list of concrete contract violations. It NEVER judges
subjective quality (that broad QA stays off, per operator 2026-06-07); it only
proves concrete, customer-harmful misses the integrated image model made:

  - placeholder / template slots leaked into the art ("[rice]", "[price]", PENDING/TBD)
  - a visible price that matches no locked price (invented, or wrong: $39.99 -> $99)
  - the pricing qualifier flipped ("Any item ..." rendered as "Every item ...")
  - a requested price fully missing                (absence; substantive read only)
  - a requested operational badge/note missing     (absence; substantive read only)
  - an internal asset id visible ("B0002" / "A0001" / "F0200")
  - the raw medium word "flyer"/"poster" leaked into the visible text

POSITIVE-presence checks fire on ANY successful read — a visible defect is confident
even on a partial OCR. ABSENCE checks (a requested price or badge missing) fire only on
a *substantive* read — the brand name was read back, proving the OCR captured the
flyer's core (operator 2026-06-07: "...when the verifier can read enough to decide").
A missing locked price on a substantive read is treated as a real omission (prices are
prominent; OCR rarely drops one it rendered) and fails closed per the operator's
"rather not show wrong info" bias; the `unverified` metric surfaces any over-fire.

False-positive discipline (a wrong block holds a LEGIT flyer): only currency-prefixed
($/₹) or "N/-" amounts are read as prices, so bare 2-decimals — "9.00 AM",
"4.00 - 7.00 PM", "3.50/5", "v2.00", "1.50 lb", ZIPs, phones, dates — are never mistaken
for prices. The placeholder rule targets known template field words (incl. the observed
"[rice]" garble), NOT every "[...]" token, so legit labels like "[Veg]" / "[Limited
Time]" are not blocked.

Pure function: no I/O, no env, no network. The gate wiring (flag / allowlist / vision
call / unverified handling / status logging) lives in flyer_bare_render. Self-contained
on purpose (own helpers + a local copy of the operational-claim patterns) so it has no
deploy-time import coupling and is unit-testable in isolation.
"""
from __future__ import annotations

import re

_BLOCKER_PREFIX = "visible_contract: "

# Template-slot leak. We target KNOWN field words (case-insensitive), incl. common
# garbles of "price" the image model produces ("[rice]"). We deliberately do NOT block
# every "[...]" token — legit flyer copy uses "[Veg]", "[Limited Time]", "[Weekend
# Special]" — only bracketed tokens that BEGIN with a template field word.
_PLACEHOLDER_SLOT_RE = re.compile(
    r"\[\s*(?:"
    r"price|prices|prce|pric|pirce|rice|cost|amount|item|items|menu|dish|dishes|"
    r"date|time|day|phone|tel|mobile|number|address|location|"
    r"business[ _]?name|brand|tagline|headline|title|text|logo|caption|category|"
    r"insert[ _a-z]*|edit[ _a-z]*|add[ _a-z]*|enter[ _a-z]*|your[ _a-z]+|placeholder"
    r")\b[^\]]*\]",
    re.IGNORECASE,
)
_TEMPLATE_WORD_RE = re.compile(r"\b(?:PENDING|TBD)\b", re.IGNORECASE)

# Price tokens — ONLY currency-prefixed ($1,299.00 / $99 / ₹50, thousands commas allowed)
# or India "N/-". A BARE 2-decimal is deliberately NOT treated as a price: "9.00 AM",
# "4.00 - 7.00 PM", "3.50/5", "v2.00", "1.50 lb" are all bare 2-decimals, and local
# context cannot reliably separate a price from a time / rating / version / measurement
# (every attempt is whack-a-mole). The unambiguous currency/"/-" forms catch the operator's
# real cases ($39.99 -> $99; 1/-); the placeholder check (a no-price flyer leaking
# "[price]"/"[rice]") and the missing-price ABSENCE check cover the no-currency cases. This
# trades catching a rare bare invented decimal for ZERO false-holds on legitimate decimals
# (the operator's reliability bias; the `unverified` metric would surface a real
# bare-invented-price pattern if one emerges).
_PRICE_RE = re.compile(r"[$₹]\s*\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*\s*/-")
_NEGATION_BEFORE_RE = re.compile(r"\b(?:no|not|never|without|dont|don'?t|skip|exclude|remove|avoid)\b", re.IGNORECASE)

# Internal record ids that must never be drawn on a customer flyer. Brand assets are
# B\d{4,} and generated assets A\d{4,} (schemas.py); projects are F\d{4}.
_INTERNAL_ID_RE = re.compile(r"\b(?:A\d{4,}|B\d{4,}|F\d{4})\b")

# The raw medium word should never appear ON the poster ("daily thali specials flyer").
_MEDIUM_WORD_RE = re.compile(r"\b(?:flyer|flier|poster)\b", re.IGNORECASE)

# "Any item ..." must not become "Every item ...". Scoped to a small noun set so a
# stray "any"/"every" in prose does not trip it.
_ANY_QUALIFIER_RE = re.compile(r"\bany\s+(?:item|items|dish|dishes|order|orders|entree|entrees|plate|plates)\b")
_EVERY_QUALIFIER_RE = re.compile(r"\bevery\s+(?:item|items|dish|dishes|order|orders|entree|entrees|plate|plates)\b")

# Local copy of visual_qa.OPERATIONAL_CLAIM_PATTERNS (kept in sync deliberately — the
# inverse "requested badge must be VISIBLE" check lives here with its patterns).
_OPERATIONAL_CLAIM_PATTERNS = (
    ("delivery", re.compile(r"\b(?:whats\s*app|whatsapp)?\s*delivery\b|\bwe\s+deliver\b", re.IGNORECASE)),
    ("catering", re.compile(r"\bcatering\s+(?:available|orders?|service)\b|\bwe\s+cater\b", re.IGNORECASE)),
    ("payment", re.compile(r"\b(?:cash\s*app|zelle|venmo|paypal|online\s+payment|payment\s+accepted)\b", re.IGNORECASE)),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").casefold()


def _cents(amount: str) -> int | None:
    """Parse a price amount to cents. Commas are thousands separators ($1,299.00 ->
    129900); "." is the decimal point."""
    s = (amount or "").replace(",", "").strip()
    m = re.search(r"\d+(?:\.\d{1,2})?", s)
    if not m:
        return None
    whole, dot, frac = m.group(0).partition(".")
    frac = (frac + "00")[:2] if dot else "00"
    try:
        return int(whole) * 100 + int(frac)
    except ValueError:
        return None


def _price_tokens(text: str) -> list[tuple[str, int]]:
    """Currency-prefixed or "N/-" price tokens in `text` as (token, cents). Bare decimals
    (times, ratings, versions, measurements) are intentionally not prices — see _PRICE_RE."""
    out: list[tuple[str, int]] = []
    for m in _PRICE_RE.finditer(text or ""):
        c = _cents(m.group(0))
        if c is not None:
            out.append((m.group(0).strip(), c))
    return out


def _business_name(project) -> str:
    for f in getattr(project, "locked_facts", None) or []:
        if getattr(f, "fact_id", "") == "business_name":
            return _norm(getattr(f, "value", "") or "")
    return ""


def _locked_price_cents(project) -> set[int]:
    """Cents of every price stated in the locked facts (item:N:price values, plus any
    currency/decimal token inside offer / pricing_structure / price-labelled facts)."""
    cents: set[int] = set()
    for f in getattr(project, "locked_facts", None) or []:
        fid = str(getattr(f, "fact_id", "") or "")
        val = str(getattr(f, "value", "") or "")
        label = str(getattr(f, "label", "") or "").casefold()
        if fid.endswith(":price") or "price" in label:
            c = _cents(val)
            if c is not None:
                cents.add(c)
        if fid.startswith("offer") or fid == "pricing_structure":
            for _tok, c in _price_tokens(val):
                cents.add(c)
    return cents


def _requested_operational_claims(project) -> list[str]:
    """Operational notes the customer actually ASKED to show. A negated mention ("no
    delivery", "don't mention Zelle") is NOT a request — crediting it would false-hold
    a correct flyer that omits it (Codex 2026-06-07)."""
    source = " ".join(
        str(v or "")
        for v in (
            getattr(project, "raw_request", "") or "",
            getattr(getattr(project, "fields", None), "notes", "") or "",
            *(getattr(f, "value", "") or "" for f in getattr(project, "locked_facts", None) or []),
        )
    )
    out: list[str] = []
    for claim, pat in _OPERATIONAL_CLAIM_PATTERNS:
        # Require the claim if ANY mention is affirmative; only drop when EVERY mention is
        # negated. "No delivery fee. Delivery available." still requires delivery; a lone
        # "no delivery" does not (Codex 2026-06-07).
        affirmative = any(
            not _NEGATION_BEFORE_RE.search(source[max(0, m.start() - 16):m.start()])
            for m in pat.finditer(source)
        )
        if affirmative:
            out.append(claim)
    return out


def readback_is_substantive(extracted_text: str, project) -> bool:
    """True when the read is decisive enough to trust an ABSENCE conclusion: the
    business name was read back (proves the OCR captured the flyer's core). A word-count
    proxy is intentionally NOT used — a header/address/hours-only read can exceed a word
    threshold while missing the menu block (Codex 2026-06-07)."""
    norm = _norm(extracted_text)
    if not norm:
        return False
    name = _business_name(project)
    return bool(name and name in norm)


def validate_visible_contract(project, extracted_text: str) -> list[str]:
    """Return concrete visible-contract blockers. Empty list = the visible output obeys
    the brief on the dimensions this referee can prove. The caller passes a non-empty
    read here (an empty read is handled as 'unverified' upstream)."""
    text = extracted_text or ""
    norm = _norm(text)
    blockers: list[str] = []
    substantive = readback_is_substantive(text, project)

    # ---- POSITIVE-presence checks (fire on any successful read) ----
    if _PLACEHOLDER_SLOT_RE.search(text):
        blockers.append(f"{_BLOCKER_PREFIX}placeholder/garbled slot visible")
    if _TEMPLATE_WORD_RE.search(text):
        blockers.append(f"{_BLOCKER_PREFIX}template token (PENDING/TBD) visible")

    for tok in _INTERNAL_ID_RE.findall(text):
        blockers.append(f"{_BLOCKER_PREFIX}internal id visible: {tok}")

    name = _business_name(project)
    if _MEDIUM_WORD_RE.search(text) and not (name and _MEDIUM_WORD_RE.search(name)):
        blockers.append(f"{_BLOCKER_PREFIX}raw medium word (flyer/poster) visible in flyer")

    locked_cents = _locked_price_cents(project)
    visible = _price_tokens(text)
    visible_cents = {c for _t, c in visible}
    for tok, c in visible:
        if c not in locked_cents:
            if locked_cents:
                blockers.append(f"{_BLOCKER_PREFIX}unexpected price visible (not in requested prices): {tok}")
            else:
                blockers.append(f"{_BLOCKER_PREFIX}invented price visible (no prices were requested): {tok}")

    # qualifier flip: "any item ..." rendered as "every item ..."
    for f in getattr(project, "locked_facts", None) or []:
        fid = str(getattr(f, "fact_id", "") or "")
        if fid != "pricing_structure" and not fid.startswith("offer"):
            continue
        if _ANY_QUALIFIER_RE.search(_norm(getattr(f, "value", "") or "")):
            if _EVERY_QUALIFIER_RE.search(norm) and not _ANY_QUALIFIER_RE.search(norm):
                blockers.append(f"{_BLOCKER_PREFIX}pricing qualifier changed: requested 'any' rendered as 'every'")
            break

    # ---- ABSENCE checks (substantive read only) ----
    if substantive:
        # A requested price absent from a substantively-read flyer is a real miss: prices
        # are prominent, so OCR rarely drops one it actually rendered — a missing locked
        # price almost always means the flyer omitted it. Fail closed (operator: "rather
        # not show wrong info"); the unverified metric surfaces any over-fire.
        for c in sorted(locked_cents):
            if c not in visible_cents:
                blockers.append(f"{_BLOCKER_PREFIX}requested price not visible: {c // 100}.{c % 100:02d}")
        for claim in _requested_operational_claims(project):
            _, pat = next(cp for cp in _OPERATIONAL_CLAIM_PATTERNS if cp[0] == claim)
            if not pat.search(text):
                blockers.append(f"{_BLOCKER_PREFIX}requested {claim} note not visible")

    # de-dup, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.add(b)
            deduped.append(b)
    return deduped
