"""Semantic visibility policy for Flyer Studio QA.

This module is a pure view over an existing FlyerProject. It does not mutate
project state or introduce persisted schema; it only tells QA which account
identity facts are hard requirements for the current brief.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Callable, Mapping
import urllib.error
import urllib.request

from schemas import FlyerProject, FlyerRequestFields


_SAVED_BRAND_RE = re.compile(
    r"\b(?:saved|stored|registered|account)\s+(?:business\s+name|brand|logo)\b"
    r"|\buse\s+(?:the\s+)?(?:saved|stored|registered|account)\s+(?:business\s+name|brand|logo)\b",
    re.IGNORECASE,
)
_SAVED_BRAND_TOKEN_RE = re.compile(
    r"\b(?:saved\s+(?:logo|business\s+name)|brand\s+asset|use\s+(?:the\s+)?logo)\b",
    re.IGNORECASE,
)
_ORG_SUFFIX_RE = re.compile(
    r"\b(?:restaurant|kitchen|cafe|bakery|market|grocery|supermarket|bazaar|bazar|studio|salon|express|catering)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticVisibilityPolicy:
    effective_business_name: str = ""
    campaign_title: str = ""
    brand_visibility_required_exact: bool = False
    brand_visibility_preferred: bool = True
    require_contact_anchor: bool = True
    require_location_anchor: bool = True


@dataclass(frozen=True)
class FlyerSemanticOffer:
    text: str = ""


@dataclass(frozen=True)
class FlyerSemanticBrief:
    campaign_title: str = ""
    account_business: str = ""
    display_brand: str = ""
    pricing_structure: str = ""
    offers: list[FlyerSemanticOffer] = field(default_factory=list)
    schedule: str = ""
    promotion_end: str = ""
    style: str = ""
    stored_contact_policy: str = ""


SemanticBriefProvider = Callable[[FlyerRequestFields, str], FlyerSemanticBrief | Mapping[str, object] | None]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SEMANTIC_BRIEF_MODEL = os.environ.get("FLYER_SEMANTIC_BRIEF_MODEL") or os.environ.get("HERMES_DEFAULT_MODEL") or "openai/gpt-4o-mini"
SEMANTIC_BRIEF_TIMEOUT_SEC = 30


def _clean(value: str) -> str:
    return " ".join((value or "").strip().split())


def _norm(value: str) -> str:
    text = (value or "").casefold()
    for ch in ("'", "`", "’", "‘", "ʼ"):
        text = text.replace(ch, "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _source_text(fields: FlyerRequestFields, raw_request: str) -> str:
    return " ".join(
        str(value or "")
        for value in (
            raw_request,
            fields.event_or_business_name,
            fields.event_date,
            fields.event_time,
            fields.venue_or_location,
            fields.contact_info,
            fields.notes,
            fields.style_preference,
        )
    )


def _provider_grounding_text(fields: FlyerRequestFields, raw_request: str) -> str:
    return " ".join(str(value or "") for value in (raw_request, fields.notes))


def _title_case(value: str) -> str:
    small = {"and", "or", "for", "with", "the", "a", "an", "of", "to"}
    words = []
    for index, word in enumerate(_clean(value).split()):
        lowered = word.lower()
        if index and lowered in small:
            words.append(lowered)
        else:
            words.append("-".join(part.capitalize() for part in lowered.split("-")))
    return " ".join(words)


def _campaign_from_source(text: str) -> str:
    source = text or ""
    if (
        re.search(r"\bgraduation\b", source, flags=re.IGNORECASE)
        and re.search(r"\bdesserts?\b", source, flags=re.IGNORECASE)
        and re.search(r"\b(?:customi[sz]ed\s+orders?|orders?)\b", source, flags=re.IGNORECASE)
    ):
        return "Graduation Dessert Specials"
    occasion = re.search(
        r"\bon\s+the\s+occasion\s+of\s+(?P<occasion>.+?)(?=\s+(?:can\s+we\s+do|create|make|generate|design|build|need)\b|[.!?]|$)",
        source,
        flags=re.IGNORECASE,
    )
    if occasion and re.search(r"\b(?:meal\s+)?combo(?:s)?\b|\bpackage(?:s)?\b", source, flags=re.IGNORECASE):
        occasion_text = _title_case(_clean(occasion.group("occasion")).strip(" .,"))
        occasion_text = re.sub(r"\b(?:Veg\s+and\s+Non\s+Veg|Non\s+Veg\s+and\s+Veg)\b", "", occasion_text)
        occasion_text = _clean(occasion_text)
        if occasion_text:
            suffix = "Meal Combos" if re.search(r"\bmeal\s+combo", source, flags=re.IGNORECASE) else "Combos"
            return f"{occasion_text} {suffix}"
    patterns = [
        r"\b(?:create|make|generate|design|build|need)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:flyer|flier|poster|banner|creative|graphic)\s+for\s+(.+?)(?=\s*(?:[.!?]|,|\b(?:all items?|any item|free|lucky draw|monday|tuesday|wednesday|thursday|friday|saturday|sunday|with|include|featuring)\b|$))",
        r"\b(?:flyer|flier|poster|banner|creative|graphic)\s+for\s+(.+?)(?=\s*(?:[.!?]|,|\b(?:all items?|any item|free|lucky draw|monday|tuesday|wednesday|thursday|friday|saturday|sunday|with|include|featuring)\b|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = _clean(match.group(1))
        candidate = re.sub(r"\b(?:sale|specials?|promotion|promo|offer)\b.*$", lambda m: m.group(0), candidate, flags=re.IGNORECASE)
        candidate = re.sub(
            r"\s+\bon\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b$",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r"\s+\bon\b$", "", candidate, flags=re.IGNORECASE)
        if candidate and len(candidate.split()) <= 6:
            return _title_case(candidate)
    return ""


def _pricing_from_source(text: str) -> str:
    match = re.search(r"\ball\s+items?\s+(?P<discount>\d+(?:\s*-\s*\d+)?\s*%\s*off)\b", text or "", flags=re.IGNORECASE)
    if match:
        discount = re.sub(r"\s+", "", match.group("discount")).replace("%off", "% off")
        return f"All items {discount}"
    match = re.search(
        r"\bany\s+item\s+(?:priced\s+)?(?:at|for|is|=|:)?\s*\$?\s*(?P<price>\d+(?:\.\d{2})?)\b",
        text or "",
        flags=re.IGNORECASE,
    )
    if match:
        return f"Any item ${match.group('price')}"
    return ""


def _offers_from_source(text: str) -> list[FlyerSemanticOffer]:
    offers: list[FlyerSemanticOffer] = []
    lucky = re.search(r"\blucky\s+draw\s+eligible\s+with\s+purchase\s+above\s+\$?\s*(?P<amount>\d+(?:\.\d{2})?)\b", text or "", flags=re.IGNORECASE)
    if lucky:
        offers.append(FlyerSemanticOffer(f"Lucky draw eligible with purchase above ${lucky.group('amount')}"))
    free = re.search(
        r"\bfree\s+(?P<item>[A-Za-z][A-Za-z '&-]{1,40}?)\s+with\s+any\s+purchase\s+above\s+\$?\s*(?P<amount>\d+(?:\.\d{2})?)\b",
        text or "",
        flags=re.IGNORECASE,
    )
    if free:
        offers.append(FlyerSemanticOffer(f"Free {_title_case(free.group('item'))} with any purchase above ${free.group('amount')}"))
    combo_end = (
        r"(?=(?:\.\s*)?(?:and\s+)?(?:a\s+)?(?:non\s*-?\s*veg|veg)\s+combo\b"
        r"|\bon\s+the\s+occasion\b|[.!?]|$)"
    )
    combo_patterns = [
        re.compile(
            r"\b(?:prices?\s*)?\$?(?P<price>\d+(?:\.\d{1,2})?)\s+for\s+"
            r"(?P<label>non\s*-?\s*veg|veg)\s+combo\s+"
            r"(?:includes?|including)\s+(?P<items>.+?)" + combo_end,
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<label>non\s*-?\s*veg|veg)\s+combo\s+"
            r"(?:priced\s+)?(?:at|for)?\s*\$?(?P<price>\d+(?:\.\d{1,2})?)\s+"
            r"(?:includes?|including)\s+(?P<items>.+?)" + combo_end,
            re.IGNORECASE,
        ),
    ]
    seen_combo_keys: set[str] = set()
    for pattern in combo_patterns:
        for combo in pattern.finditer(text or ""):
            label_raw = combo.group("label")
            label = "Non Veg Combo" if re.search(r"\bnon\s*-?\s*veg\b", label_raw, re.IGNORECASE) else "Veg Combo"
            price = combo.group("price")
            items = _clean(combo.group("items"))
            items = re.sub(r"\s+\bon\s+the\s+occasion\b.*$", "", items, flags=re.IGNORECASE).strip(" .,")
            parts = [part.strip(" .,") for part in items.split(",") if part.strip(" .,")]
            if len(parts) == 2 and not re.search(r"\band\b", parts[1], flags=re.IGNORECASE):
                items = f"{parts[0]} and {parts[1]}"
            key = _norm(f"{label} {price} {items}")
            if not items or key in seen_combo_keys:
                continue
            seen_combo_keys.add(key)
            offers.append(FlyerSemanticOffer(f"{label}: ${price} includes {items}"))
    return offers


def _schedule_from_source(text: str) -> str:
    day = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    match = re.search(rf"\b(?P<first>{day})\s+(?:and|through|to)\s+(?P<second>{day})\b", text or "", flags=re.IGNORECASE)
    if match:
        joiner = "through" if "through" in match.group(0).lower() or " to " in match.group(0).lower() else "and"
        return f"{match.group('first').title()} {joiner} {match.group('second').title()}"
    return ""


def _promotion_end_from_source(text: str) -> str:
    match = re.search(
        r"\b(?:until|through|thru|expires?|valid\s+(?:through|until)|runs\s+until)\s+(?P<date>[A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?)\b",
        text or "",
        flags=re.IGNORECASE,
    )
    return _clean(match.group("date")) if match else ""


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


# OpenRouter key resolution is shared across reference_extract / semantic_brief /
# visual_qa (census C9). Re-bound under the historic private names so existing call
# sites (incl. flyer_context_builder's `from flyer_semantic_brief import
# _openrouter_key`) and any test monkeypatch of these names keep working.
try:
    from flyer_openrouter_env import read_key_from_env_file as _read_key_from_env_file, openrouter_key as _openrouter_key  # type: ignore  # noqa: F401
except ImportError:
    from agents.flyer.openrouter_env import read_key_from_env_file as _read_key_from_env_file, openrouter_key as _openrouter_key  # noqa: F401


def _numeric_tokens(value: str) -> list[str]:
    return [match.group(0).replace(",", "") for match in re.finditer(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])", value or "")]


def _numeric_token_present(source: str, token: str) -> bool:
    escaped = re.escape(token)
    return bool(re.search(rf"(?<![\d.]){escaped}(?![\d.])", source or ""))


def _numeric_anchors_grounded(source: str, value: str) -> bool:
    for token in _numeric_tokens(value):
        if not _numeric_token_present(source, token):
            return False
    return True


def _grounded(source: str, value: str, *, require_expiry_context: bool = False) -> bool:
    value = _clean(value)
    if not value:
        return False
    if not _numeric_anchors_grounded(source, value):
        return False
    normalized_source = _norm(source)
    normalized_value = _norm(value)
    if normalized_value and normalized_value in normalized_source:
        if not require_expiry_context:
            return True
        return bool(re.search(r"\b(?:until|through|thru|expires?|valid|runs)\b.{0,30}" + re.escape(normalized_value), normalized_source))
    tokens = [token for token in normalized_value.split() if not token.isdigit()]
    return bool(tokens) and all(token in normalized_source for token in tokens)


def _coerce_brief(value: FlyerSemanticBrief | Mapping[str, object] | None) -> FlyerSemanticBrief:
    if isinstance(value, FlyerSemanticBrief):
        return value
    if not isinstance(value, Mapping):
        return FlyerSemanticBrief()
    raw_offers = value.get("offers", [])
    offers: list[FlyerSemanticOffer] = []
    if isinstance(raw_offers, list):
        for item in raw_offers:
            if isinstance(item, FlyerSemanticOffer):
                offers.append(item)
            elif isinstance(item, Mapping):
                offers.append(FlyerSemanticOffer(text=str(item.get("text") or "")))
            elif isinstance(item, str):
                offers.append(FlyerSemanticOffer(text=item))
    return FlyerSemanticBrief(
        campaign_title=str(value.get("campaign_title") or ""),
        account_business=str(value.get("account_business") or ""),
        display_brand=str(value.get("display_brand") or ""),
        pricing_structure=str(value.get("pricing_structure") or ""),
        offers=offers,
        schedule=str(value.get("schedule") or ""),
        promotion_end=str(value.get("promotion_end") or ""),
        style=str(value.get("style") or ""),
        stored_contact_policy=str(value.get("stored_contact_policy") or ""),
    )


# A grounded offer becomes a REQUIRED, customer-visible `offer:N` locked fact
# (facts.py). Token-level grounding (`_grounded`) is necessary but NOT sufficient:
# an LLM can echo the request back verbatim — every token is "in source", so it
# passes grounding — yet the result is a request-tail paragraph or the customer
# describing/instructing the flyer, not a faithful offer span. So an offer must
# ALSO be faithful. This is an ADDITIONAL gate on top of grounding (strengthening,
# never weakening it). Two checks, content-based (not a length proxy):
#
# 1. AGENT-ASK: the value addresses the AGENT ("can we / could you / would you …")
#    — that's a request, not an offer FOR customers.
# 2. FLYER-MEDIUM DESCRIPTION: a faithful offer is a DEAL, never a description of
#    the flyer medium itself. So after removing any legitimate REDEMPTION phrase
#    (a redemption verb acting on the physical flyer/coupon — "bring this flyer for
#    10% off"), ANY remaining flyer/poster/banner NOUN means the value is describing
#    or instructing the flyer (the live "...meal combo flyer for veg and non veg..."
#    echo, or "make our new meal combo flyer") ⇒ drop. Doing it per-residual (strip
#    redemption, then scan) means a legit redemption sentence cannot launder a second
#    echo sentence in the same value (Codex round-2/3).
# Plus a RENDER-SAFETY length backstop UNDER render's 180-char `_clean_fact_text`
# hard-fail (NOT a faithfulness proxy — a legit ~100-char enumerated offer survives).
_OFFER_MAX_LEN = 160

# The customer addressing the AGENT (a request, not an offer). NOT creation verbs on
# their own ("we make combos for $20" is a legit offer) — flyer-medium description is
# handled separately below, so bare make/create are not instruction markers here.
_OFFER_AGENT_ASK_RE = re.compile(r"\b(?:can\s+(?:we|you|i)|could\s+(?:we|you)|would\s+you)\b", re.IGNORECASE)

# Any flyer-medium noun. A faithful offer is a deal; it does not name the flyer medium.
_OFFER_FLYER_NOUN_RE = re.compile(r"\b(?:flyer|flier|poster|banner)\b", re.IGNORECASE)

# A legitimate REDEMPTION phrase: a redemption verb acting on the physical flyer/coupon
# token ("Bring this flyer for 10% off", "Mention our poster for free dessert"). These
# are stripped before the flyer-noun scan so they don't trip it; everything else that
# names the flyer medium is an echo/instruction.
#
# The discriminator is the PAYOFF: a redemption REWARDS the customer (a discount/freebie),
# whereas an echo merely DESCRIBES the flyer's content ("show this flyer with veg combo
# prices"). So the strip fires ONLY for <verb> <flyer/coupon> for/to-get <PAYOFF> — a
# verb+medium WITHOUT a payoff is NOT redemption, is not stripped, and its flyer noun
# still trips the echo scan (Codex round-4). Determiner broad (a/an/our) since the
# verb+payoff carry the signal.
_OFFER_PAYOFF_RE = r"(?:\d+\s*%|\bfree\b|\$\s*\d|\bdiscount\b|\boff\b|\bbogo\b|\bcomplimentary\b)"
_OFFER_REDEMPTION_RE = re.compile(
    r"\b(?:bring|show|present|mention|redeem|scan)\s+(?:(?:a|an|the|this|that|your|our)\s+){0,2}"
    r"(?:flyer|flier|poster|banner|coupon|voucher)\s+(?:for|to\s+(?:get|claim|redeem))\s+"
    r"(?:\w+[\s,]+){0,4}?" + _OFFER_PAYOFF_RE,
    re.IGNORECASE,
)


def _offer_is_faithful(value: str) -> bool:
    """True when `value` is a faithful, render-safe offer span (safe to lock as a
    REQUIRED customer-visible fact); False when it addresses the agent, describes /
    instructs the flyer medium, or over-runs the render-safety length cap. Applied
    AFTER `_grounded`, so it only ever removes grounded-but-unfaithful values — it
    cannot admit anything ungrounded."""
    cleaned = _clean(value)
    if not cleaned or len(cleaned) > _OFFER_MAX_LEN:
        return False
    if _OFFER_AGENT_ASK_RE.search(cleaned):
        return False
    # Strip legit redemption phrases, then ANY remaining flyer-medium noun ⇒ the value
    # describes/instructs the flyer (an echo), not an offer. Per-residual, so a legit
    # redemption sentence can't launder a second echo sentence in the same value.
    residual = _OFFER_REDEMPTION_RE.sub(" ", cleaned)
    if _OFFER_FLYER_NOUN_RE.search(residual):
        return False
    return True


def _source_ground_brief(brief: FlyerSemanticBrief, source: str, *, allow_text_identity: bool) -> FlyerSemanticBrief:
    offers = [
        FlyerSemanticOffer(text=_clean(offer.text))
        for offer in brief.offers
        if _grounded(source, offer.text) and _offer_is_faithful(offer.text)
    ]
    return FlyerSemanticBrief(
        campaign_title=_clean(brief.campaign_title) if _grounded(source, brief.campaign_title) else "",
        account_business=_clean(brief.account_business) if allow_text_identity and _grounded(source, brief.account_business) else "",
        display_brand=_clean(brief.display_brand) if allow_text_identity and _grounded(source, brief.display_brand) else "",
        pricing_structure=_clean(brief.pricing_structure) if _grounded(source, brief.pricing_structure) else "",
        offers=offers,
        schedule=_clean(brief.schedule) if _grounded(source, brief.schedule) else "",
        promotion_end=_clean(brief.promotion_end) if _grounded(source, brief.promotion_end, require_expiry_context=True) else "",
        style=_clean(brief.style) if _grounded(source, brief.style) else "",
        stored_contact_policy=_clean(brief.stored_contact_policy) if _grounded(source, brief.stored_contact_policy) else "",
    )


def _fallback_brief(fields: FlyerRequestFields, raw_request: str, *, allow_text_identity: bool) -> FlyerSemanticBrief:
    source = _source_text(fields, raw_request)
    campaign = _campaign_from_source(source)
    if not campaign:
        campaign = _clean(fields.event_or_business_name or "")
    return FlyerSemanticBrief(
        campaign_title=campaign,
        account_business=_clean(fields.venue_or_location or "") if allow_text_identity else "",
        pricing_structure=_pricing_from_source(source),
        # The deterministic combo/offer parser can over-capture a request tail the same
        # way the provider can (e.g. "Non Veg Combo: $X includes Can we do … flyer for …").
        # Gate it through the SAME faithfulness primitive used for provider offers so no
        # offer source — provider or fallback parser — locks an echo as a customer fact.
        offers=[offer for offer in _offers_from_source(source) if _offer_is_faithful(offer.text)],
        schedule=_schedule_from_source(source),
        promotion_end=_promotion_end_from_source(source),
        style=_clean(fields.style_preference or ""),
        stored_contact_policy="use saved contact" if re.search(r"\b(?:saved|stored)\s+(?:address|phone|contact)\b", source, flags=re.IGNORECASE) else "",
    )


def _merge_briefs(primary: FlyerSemanticBrief, fallback: FlyerSemanticBrief) -> FlyerSemanticBrief:
    campaign_title = primary.campaign_title or fallback.campaign_title
    if (
        fallback.campaign_title
        and re.search(r"\b(?:meal\s+)?combos?\b", fallback.campaign_title, flags=re.IGNORECASE)
        and re.search(r"\b(?:veg\s+and\s+non\s+veg|non\s+veg\s+and\s+veg)\b", campaign_title, flags=re.IGNORECASE)
    ):
        campaign_title = fallback.campaign_title
    return FlyerSemanticBrief(
        campaign_title=campaign_title,
        account_business=primary.account_business or fallback.account_business,
        display_brand=primary.display_brand or fallback.display_brand,
        pricing_structure=primary.pricing_structure or fallback.pricing_structure,
        offers=primary.offers or fallback.offers,
        schedule=primary.schedule or fallback.schedule,
        promotion_end=primary.promotion_end or fallback.promotion_end,
        style=primary.style or fallback.style,
        stored_contact_policy=primary.stored_contact_policy or fallback.stored_contact_policy,
    )


def build_hermes_semantic_brief_provider() -> SemanticBriefProvider | None:
    key = _openrouter_key()
    if not key or "PLACEHOLDER" in key:
        return None

    def provider(fields: FlyerRequestFields, raw_request: str) -> FlyerSemanticBrief | Mapping[str, object] | None:
        prompt = {
            "task": "Extract a source-grounded flyer marketing brief from the customer message.",
            "customer_message": raw_request,
            "existing_fields": {
                "event_or_business_name": fields.event_or_business_name,
                "event_date": fields.event_date,
                "event_time": fields.event_time,
                "venue_or_location": fields.venue_or_location,
                "contact_info": fields.contact_info,
                "notes": fields.notes,
                "style_preference": fields.style_preference,
            },
            "schema": {
                "campaign_title": "short campaign/headline/title, not account identity",
                "account_business": "only if customer explicitly names a business identity",
                "display_brand": "only if customer explicitly asks to display a brand",
                "pricing_structure": "sale pricing rule such as Any item $7.99 or All items 5-10% off",
                "offers": [{"text": "secondary offers exactly grounded in the message"}],
                "schedule": "days/times exactly grounded in the message",
                "promotion_end": "expiration/end date exactly grounded in the message",
                "style": "style direction exactly grounded in the message",
                "stored_contact_policy": "saved/stored contact/address/logo policy if requested",
            },
            "rules": [
                "Return JSON only.",
                "Do not invent prices, dates, items, phone, address, or business identity.",
                "If ambiguous, leave the field blank instead of guessing.",
                "Keep account business separate from campaign title.",
            ],
        }
        payload = {
            "model": SEMANTIC_BRIEF_MODEL,
            "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=SEMANTIC_BRIEF_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8")
            doc = json.loads(body)
            content = doc["choices"][0]["message"]["content"]
            return json.loads(content)
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError):
            return None

    return provider


def _brief_has_content(brief: FlyerSemanticBrief) -> bool:
    """True iff the (source-grounded) provider brief contributed any non-empty
    field — i.e. the Hermes brain produced grounded content that survived
    grounding. Used only for provenance telemetry (used-vs-fell-back)."""
    return bool(
        brief.campaign_title or brief.account_business or brief.display_brand
        or brief.pricing_structure or brief.offers or brief.schedule
        or brief.promotion_end or brief.style or brief.stored_contact_policy
    )


def build_semantic_flyer_brief(
    fields: FlyerRequestFields,
    raw_request: str,
    *,
    profile_business_name: str = "",
    allow_text_identity: bool = True,
    provider: SemanticBriefProvider | None = None,
    provenance: dict | None = None,
) -> FlyerSemanticBrief:
    """Return a source-grounded flyer brief from Hermes/provider semantics.

    The provider is the intended Hermes brain seam. Deterministic extraction is
    only a conservative fallback for currently observed incident shapes.

    ``provenance`` (optional out-param): when a dict is passed, it is populated
    with ``status`` (provider_used | fell_back), ``reason`` (provider_absent |
    provider_empty), and ``provider_present`` so the caller can emit a
    semantic-brief-outcome audit row. Purely observational — does not change the
    returned brief or the control flow.
    """
    source = _source_text(fields, raw_request)
    provider_source = _provider_grounding_text(fields, raw_request)
    fallback = _fallback_brief(fields, raw_request, allow_text_identity=allow_text_identity)
    provider_brief = FlyerSemanticBrief()
    provider_contributed = False
    if provider is not None:
        provider_brief = _source_ground_brief(
            _coerce_brief(provider(fields, raw_request)),
            provider_source,
            allow_text_identity=allow_text_identity,
        )
        provider_contributed = _brief_has_content(provider_brief)
    if provenance is not None:
        if provider is None:
            provenance.update(status="fell_back", reason="provider_absent", provider_present=False)
        elif provider_contributed:
            provenance.update(status="provider_used", reason="", provider_present=True)
        else:
            provenance.update(status="fell_back", reason="provider_empty", provider_present=True)
    merged = _merge_briefs(provider_brief, fallback)
    if profile_business_name and _norm(merged.campaign_title) == _norm(profile_business_name):
        merged = FlyerSemanticBrief(
            campaign_title="",
            account_business=merged.account_business,
            display_brand=merged.display_brand,
            pricing_structure=merged.pricing_structure,
            offers=merged.offers,
            schedule=merged.schedule,
            promotion_end=merged.promotion_end,
            style=merged.style,
            stored_contact_policy=merged.stored_contact_policy,
        )
    return merged


def _norm_contains(haystack: str, needle: str) -> bool:
    hay = _norm(haystack)
    ndl = _norm(needle)
    if not hay or not ndl:
        return False
    return re.search(r"\b" + re.escape(ndl) + r"\b", hay) is not None


def fact_value(project: FlyerProject, fact_id: str) -> str:
    for fact in project.locked_facts:
        if fact.fact_id == fact_id and str(fact.value or "").strip():
            return _clean(str(fact.value))
    return ""


def _source_contract_requires_exact_brand(project: FlyerProject) -> bool:
    for extraction in project.reference_extractions or []:
        contract = getattr(extraction, "source_contract", None)
        if not contract:
            continue
        if getattr(contract, "preserve_layout", False) or getattr(contract, "preserve_unmentioned_text", False):
            return True
        if getattr(contract, "requested_replacements", None):
            return True
    return False


def _mentions_saved_brand(project: FlyerProject) -> bool:
    text = f"{project.raw_request or ''} {getattr(project.fields, 'notes', '') or ''}"
    return bool(_SAVED_BRAND_RE.search(text) or _SAVED_BRAND_TOKEN_RE.search(text))


def semantic_visibility_policy(project: FlyerProject) -> SemanticVisibilityPolicy:
    business = fact_value(project, "business_name")
    campaign = (
        fact_value(project, "campaign_title")
        or fact_value(project, "headline")
        or _clean(project.fields.event_or_business_name or "")
    )
    brand_required = _mentions_saved_brand(project) or _source_contract_requires_exact_brand(project)
    return SemanticVisibilityPolicy(
        effective_business_name=business,
        campaign_title=campaign if _norm(campaign) != _norm(business) else "",
        brand_visibility_required_exact=brand_required,
        brand_visibility_preferred=True,
        require_contact_anchor=True,
        require_location_anchor=True,
    )


def visible_wrong_brand_blockers(project: FlyerProject, extracted_text: str) -> list[str]:
    """Conservative wrong-brand checks for visible identity claims.

    This is intentionally not broad NER. It blocks explicit identity labels,
    known source-contract business names, and highly-shaped organization
    masthead lines. Campaign titles are not treated as account identity.
    """
    policy = semantic_visibility_policy(project)
    allowed = {_norm(policy.effective_business_name)}
    campaign_title = _norm(policy.campaign_title)
    for extraction in project.reference_extractions or []:
        contract = getattr(extraction, "source_contract", None)
        if contract:
            allowed.add(_norm(getattr(contract, "target_business_name", "") or ""))
    allowed.discard("")
    blockers: list[str] = []

    def allowed_identity_visible(value: str) -> bool:
        return any(_norm_contains(value, allowed_name) for allowed_name in allowed)

    def is_campaign_title(value: str) -> bool:
        return bool(campaign_title and _norm(value) == campaign_title)

    def is_requested_non_identity_label(value: str) -> bool:
        normalized = _norm(value)
        if normalized != "catering":
            return False
        source = " ".join(
            str(item or "")
            for item in (
                project.raw_request,
                getattr(project.fields, "notes", ""),
                *(fact.value for fact in project.locked_facts),
            )
        )
        return bool(
            re.search(
                r"\bcatering\s+(?:note|available|service|services|orders?|option|options|badge|badges)\b"
                r"|\binclude\s+(?:a\s+)?catering\b"
                r"|\bwe\s+cater\b",
                source,
                flags=re.IGNORECASE,
            )
        )

    def append_once(blocker: str) -> None:
        if blocker not in blockers:
            blockers.append(blocker)

    for match in re.finditer(
        r"\b(?:business|brand|company)\s*:\s*(?P<name>[A-Za-z][A-Za-z0-9 '&.-]{1,80})",
        extracted_text or "",
        flags=re.IGNORECASE,
    ):
        name = _clean(match.group("name"))
        name = re.split(r"[\n\r]| {2,}", name, maxsplit=1)[0].strip(" .,:;")
        normalized = _norm(name)
        if normalized and normalized not in allowed:
            append_once(f"visible wrong business/brand: {name}")

    for extraction in project.reference_extractions or []:
        contract = getattr(extraction, "source_contract", None)
        if not contract:
            continue
        for name in getattr(contract, "source_business_names", []) or []:
            source_name = _clean(str(name))
            if not source_name or allowed_identity_visible(source_name):
                continue
            if _norm_contains(extracted_text, source_name):
                append_once(f"visible wrong business/brand: {source_name}")

    for line in (extracted_text or "").splitlines():
        candidate = _clean(line).strip(" .,:;")
        if not candidate or len(candidate) > 80:
            continue
        if any(ch.isdigit() for ch in candidate) or "$" in candidate:
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'&.-]*", candidate)
        if len(words) > 6:
            continue
        letters = [ch for ch in candidate if ch.isalpha()]
        if len(letters) < 4:
            continue
        uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
        suffix_final = bool(2 <= len(words) <= 3 and _ORG_SUFFIX_RE.search(words[-1]))
        mixed_org_masthead = suffix_final and any(ch.isupper() for ch in candidate) and any(ch.islower() for ch in candidate)
        if uppercase_ratio < 0.8 and not candidate.istitle() and not mixed_org_masthead:
            continue
        if not _ORG_SUFFIX_RE.search(candidate):
            continue
        if is_campaign_title(candidate):
            continue
        if is_requested_non_identity_label(candidate):
            continue
        if allowed_identity_visible(candidate):
            continue
        append_once(f"visible wrong business/brand: {candidate.title()}")
    return blockers
