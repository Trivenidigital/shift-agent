"""Pure intake-field extraction + customer-hydration helpers for Flyer Studio.

Lifted out of `scripts/create-flyer-project` so the request->fields logic is
importable and unit-testable in-process. Their only non-stdlib dependency is
`FlyerRequestFields` from the platform schemas.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from schemas import FlyerRequestFields


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

FOOD_TERMS = (
    "restaurant", "grocery", "food", "catering", "menu", "breakfast", "lunch",
    "dinner", "biryani", "dosa", "idli", "idly", "buffet", "meal", "combo",
    "kitchen", "bakery", "sweet", "sweets", "snack", "snacks",
)
SALON_TERMS = (
    "salon", "hair", "haircut", "perm", "perms", "blowdry", "beauty",
    "spa", "stylist", "barber", "nails", "makeup",
)
TAX_TERMS = ("tax", "bookkeeping", "accounting", "payroll", "filing", "cpa")
CLEANING_TERMS = ("cleaning", "cleaner", "deep clean", "move-out", "maid")
MARKETING_TERMS = ("marketing", "seo", "paid ads", "content creation", "social media")


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    for term in terms:
        if " " in term or "-" in term:
            if term in lower:
                return True
        elif re.search(rf"\b{re.escape(term)}\b", lower):
            return True
    return False


def _is_product_or_brand_promo(text: str) -> bool:
    """True when the request asks for a product/brand promo flyer.

    Pre-fix this matched bare `brand`/`branding`, which incorrectly
    poisoned exact-edit requests like `replace Triveni branding with
    Lakshmi's Kitchen branding` into grocery-product styling. Brand/
    branding edit verbs are now scoped to phrases that combine a brand
    cue with an explicit promo/product cue.
    """
    lower = text.lower()
    if re.search(
        r"\b(?:hero image|tagline|badge|badges|certified|"
        r"product|featuring|premium|organic-style|organic style|grocery aesthetic)\b",
        lower,
    ):
        return True
    # `brand-forward product promo` / `brand promo` etc. still count.
    if re.search(r"\bbrand(?:-|\s+)forward\b", lower):
        return True
    if re.search(r"\bbrand(?:ing)?\s+(?:promo|promotion|forward|focus)\b", lower):
        return True
    return False


def _style_for_request(text: str) -> str:
    if _is_product_or_brand_promo(text):
        return "premium organic-style product promotion with brand-forward grocery styling, hero product imagery, certification badges, and clean space for optional address or phone"
    if _has_any(text, SALON_TERMS):
        return "modern US salon and beauty studio promotion with clean upscale service-offer cards, hair-service photography, and category-safe styling"
    if _has_any(text, TAX_TERMS):
        return "clean professional US tax and bookkeeping services flyer with trust-forward typography, simple offer cards, and category-safe styling"
    if _has_any(text, CLEANING_TERMS):
        return "fresh local cleaning service flyer with bright home-service visuals, clear service offer cards, and category-safe styling"
    if _has_any(text, MARKETING_TERMS):
        return "modern digital marketing services flyer with crisp business visuals, clear service offer cards, and category-safe styling"
    if _has_any(text, FOOD_TERMS):
        return "professional local food menu flyer with appetizing photography, strong price readability, and brand-forward retail design"
    if "$" in text or any(term in text.lower() for term in ("price", "offer", "deal", "services")):
        return "neutral US local-business promotion with clear service offer cards, readable prices, and category-safe styling"
    return ""


def _strip_offer_from_business_name(name: str) -> str:
    name = re.sub(
        r"\s+\b(?:promoting|offering|featuring|advertising|announcing|using|with|including)\b.+$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    return name.strip(" .:-")


def _clean_extracted_label(value: str) -> str:
    value = re.sub(r"[*_`]+", "", value or "")
    value = re.sub(r"^(?:a\s+|an\s+)?(?:new\s+original|original|premium|local|professional)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+\b(?:using|with)\s+(?:the\s+)?(?:attached|uploaded|provided)?\s*(?:logo|template|reference|image).*$", "", value, flags=re.IGNORECASE)
    return value.strip(" .:-")


def _invalid_venue(value: str) -> bool:
    clean = re.sub(r"\s+", " ", value or "").strip(" .,:;").lower()
    return (
        not clean
        or "$" in clean
        or (
            re.search(r"\b\d+(?:\.\d{1,2})?\b", clean)
            and re.search(r"\b(?:and|or|item|items?|price|priced|include)\b", clean)
        )
        or clean in {"and", "or", "the bottom", "bottom", "top", "end", "customer profile"}
        or clean.startswith(("phone ", "phone number", "contact ", "address and phone"))
    )


def _schema_max_length(field_name: str) -> int | None:
    field = FlyerRequestFields.model_fields.get(field_name)
    if field is None:
        return None
    for meta in field.metadata:
        max_length = getattr(meta, "max_length", None)
        if isinstance(max_length, int):
            return max_length
    return None


def _clamp_schema_string(field_name: str, value):
    if value is None or not isinstance(value, str):
        return value
    max_length = _schema_max_length(field_name)
    if max_length is None or len(value) <= max_length:
        return value
    return value[:max_length].rstrip()


def _request_fields(**kwargs) -> FlyerRequestFields:
    for field_name in (
        "event_or_business_name",
        "event_time",
        "venue_or_location",
        "contact_info",
        "style_preference",
        "notes",
    ):
        if field_name in kwargs:
            kwargs[field_name] = _clamp_schema_string(field_name, kwargs[field_name])
    return FlyerRequestFields(**kwargs)


def _looks_like_overcaptured_name(name: str, text: str) -> bool:
    clean = re.sub(r"\s+", " ", name or "").strip()
    if not clean:
        return False
    detailish = bool(re.search(
        r"\b(?:address|all|coupon|customer|customers|draw|eligible|include|logo|"
        r"lucky|phone|purchase|purchases|saved|sale|starting)\b|"
        r"\$\s*\d|\b\d{1,2}/\d{1,2}(?:-\d{1,2}/\d{1,2})?\b",
        clean,
        flags=re.IGNORECASE,
    ))
    if len(clean) > 120:
        return True
    if detailish and len(clean) > 80:
        return True
    source = re.sub(r"\s+", " ", text or "").strip()
    return detailish and len(clean) > 60 and len(clean) >= int(max(len(source), 1) * 0.65)


def _explicit_english_only(text: str) -> bool:
    lower = (text or "").lower()
    return bool(
        re.search(r"\b(?:language\s*:\s*)?english\s+only\b", lower)
        or re.search(r"\b(?:do\s+not|don't|dont|no)\s+use\s+(?:telugu|hindi|tamil|malayalam|kannada|gujarati|marathi|punjabi|regional)", lower)
        or "no regional indian language" in lower
        or "no regional languages" in lower
    )


def _hydrate_fields_from_customer(
    fields: FlyerRequestFields,
    *,
    customer,
) -> FlyerRequestFields:
    """Fill missing flyer facts from the registered Flyer Studio account."""
    if customer is None or customer.status not in {"trial", "active"}:
        return fields
    updates: dict[str, object] = {}
    if not (fields.event_or_business_name or "").strip():
        updates["event_or_business_name"] = customer.business_name
    if not (fields.venue_or_location or "").strip():
        updates["venue_or_location"] = customer.business_address
    if not (fields.contact_info or "").strip():
        updates["contact_info"] = str(customer.public_phone)
    if fields.preferred_language == "en" and customer.preferred_language != "en" and not _explicit_english_only(fields.notes or ""):
        updates["preferred_language"] = customer.preferred_language
    if not updates:
        return fields
    return fields.model_copy(update=updates)


def _extract_fields(raw_request: str, *, now: datetime) -> FlyerRequestFields:
    text = " ".join(raw_request.split())
    event_name = ""
    date_value = None
    time_value = None
    venue = ""
    contact = ""
    language = "en"
    style = ""
    formats: list[str] = []

    event_match = re.search(
        r"\b(?:need|create|make|generate)?\s*(?:a\s+)?(?:flyer|flier|poster|banner)\s+for\s+(.+?)(?=\s+\b(?:promoting|offering|featuring|advertising|announcing)\b|\s+(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b|\.|$)",
        text,
        flags=re.IGNORECASE,
    )
    if event_match:
        event_name = _clean_extracted_label(_strip_offer_from_business_name(event_match.group(1).strip(" .")))
        event_name = re.sub(r"^(?:customer|business|client)\s+", "", event_name, flags=re.IGNORECASE).strip(" .")
    if not event_name:
        menu_match = re.search(
            r"\bcreate\s+(.+?\b(?:flyer|flier|poster|banner))\b",
            text,
            flags=re.IGNORECASE,
        )
        if menu_match:
            event_name = menu_match.group(1).strip(" .")
            event_name = re.sub(r"\s+\b(?:flyer|flier|poster|banner)\b$", "", event_name, flags=re.IGNORECASE).strip(" .")
            event_name = re.sub(r"^(?:customer|business|client)\s+", "", event_name, flags=re.IGNORECASE).strip(" .")
            event_name = _clean_extracted_label(event_name)
    if not event_name:
        offer_match = re.search(
            r"\b((?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)?\s*"
            r"[a-z0-9 '&-]{2,80}?\b(?:night\s+special|special|menu|flyer|flier|poster|banner))\b",
            text,
            flags=re.IGNORECASE,
        )
        if offer_match:
            event_name = offer_match.group(1).strip(" .")

    date_match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:,\s*(\d{4}))?",
        text,
        flags=re.IGNORECASE,
    )
    if date_match:
        month = MONTHS[date_match.group(1).lower()]
        day = int(date_match.group(2))
        year = int(date_match.group(3) or now.year)
        candidate = datetime(year, month, day, tzinfo=timezone.utc).date()
        if not date_match.group(3) and candidate < now.date():
            candidate = datetime(year + 1, month, day, tzinfo=timezone.utc).date()
        date_value = candidate.isoformat()

    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b", text, flags=re.IGNORECASE)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "00")
        meridiem = time_match.group(3).upper()
        if meridiem == "PM" and hour != 12:
            hour += 12
        if meridiem == "AM" and hour == 12:
            hour = 0
        time_value = f"{hour:02d}:{minute:02d}"

    venue_match = re.search(
        r"\bat\s+(.+?)(?=\.|,\s*contact\b|\s+contact\b|\s+telugu\b|\s+hindi\b|\s+spanish\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if venue_match:
        venue = venue_match.group(1).strip(" .")
    location_match = re.search(
        r"\blocation[:\s]+(.+?)(?=\.|,\s*(?:address|phone|contact)\b|\s+(?:address|phone|contact)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if location_match:
        venue = location_match.group(1).strip(" .")
    address_match = re.search(
        r"\baddress[:\s]+(.+?)(?=\.|,\s*(?:phone|contact)\b|\s+(?:phone|contact)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if address_match:
        address = address_match.group(1).strip(" .")
        venue = f"{venue}, {address}" if venue else address
    if re.search(r"\b(?:the\s+)?(?:bottom|top|end)\b", venue, flags=re.IGNORECASE) or _invalid_venue(venue):
        venue = ""

    contact_match = re.search(r"\bcontact[:\s]+(.+?)(?=\.|$)", text, flags=re.IGNORECASE)
    if contact_match:
        contact = contact_match.group(1).strip(" .")
    phone_match = re.search(
        r"\bphone[:\s]+((?:\+?1[\s.-]*)?\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4})\b",
        text,
        flags=re.IGNORECASE,
    )
    if phone_match:
        contact = phone_match.group(1).strip(" .")

    lower = text.lower()
    if lower.startswith("edit uploaded flyer/source artwork") and event_name.lower() in {
        "edit uploaded flyer",
        "uploaded flyer",
        "uploaded flyer template",
    }:
        event_name = ""
    if "uploaded template/reference" in lower and event_name.lower() in {"flyer", "create flyer", "uploaded flyer template"}:
        event_name = ""
    if _explicit_english_only(text):
        language = "en"
    elif "telugu" in lower:
        language = "te"
    elif "hindi" in lower:
        language = "hi"
    elif "malayalam" in lower:
        language = "ml"
    elif "tamil" in lower:
        language = "ta"
    elif "kannada" in lower:
        language = "kn"
    elif "gujarati" in lower:
        language = "gu"
    elif "marathi" in lower:
        language = "mr"
    elif "punjabi" in lower:
        language = "pa"
    elif "spanish" in lower:
        language = "es"
    elif "mixed" in lower or "multi-language" in lower or "multilingual" in lower:
        language = "mixed"
    if re.search(r"\b(?:style|look|design style|visual style)\s*:", text, flags=re.IGNORECASE):
        style_match = re.search(
            r"\bstyle\s*:\s*(.+?)(?=\.|,\s*(?:location|address|phone|contact)\b|\s+(?:location|address|phone|contact)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if not style_match:
            style_match = re.search(r"([^.]*\bstyle\b)", text, flags=re.IGNORECASE)
        if style_match:
            style = style_match.group(1).strip(" .")
    if not style:
        style = _style_for_request(text)
    if not event_name and "uploaded template/reference" in lower:
        event_name = ""
    for label, value in [
        ("whatsapp", "whatsapp_image"),
        ("instagram post", "instagram_post"),
        ("insta post", "instagram_post"),
        ("story", "instagram_story"),
        ("pdf", "printable_pdf"),
        ("print", "printable_pdf"),
    ]:
        if label in lower and value not in formats:
            formats.append(value)

    event_name = _normalize_event_name(event_name, text)
    if _looks_like_overcaptured_name(event_name, text):
        event_name = ""

    return _request_fields(
        event_or_business_name=event_name or None,
        event_date=date_value,
        event_time=time_value,
        venue_or_location=venue or None,
        contact_info=contact or None,
        preferred_language=language,
        style_preference=style,
        output_formats=formats,
        notes=text,
    )


def _normalize_event_name(event_name: str, text: str) -> str:
    name = re.sub(r"^\s*(?:a|an|the)\s+", "", (event_name or "").strip(" ."), flags=re.IGNORECASE)
    help_match = re.search(
        r"\bhelp\s+me\s+with\s+(.+?)\s+(?:flyer|flier|poster|banner)\b",
        name,
        flags=re.IGNORECASE,
    )
    if help_match:
        name = help_match.group(1).strip(" .")
    name = re.sub(
        r"^(?:i[?'’]?d\s+like\s+you\s+to\s+)?(?:help\s+me\s+with\s+)?",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip(" .")
    name = re.sub(
        r"\s+\bon\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip(" .")
    name = re.sub(r"\s+\b(?:flyer|flier|poster|banner)\b$", "", name, flags=re.IGNORECASE).strip(" .")
    lower_text = text.lower()
    lower_name = name.lower()
    has_recurring_days = bool(re.search(
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend)s?\b",
        lower_text,
    ))
    if "breakfast" in lower_text and (not name or lower_name in {"breakfast", "breakfast menu"}):
        return "Weekend Breakfast Specials" if has_recurring_days else "Breakfast Specials"
    if "menu" in lower_text and not name:
        return "Menu Specials"
    return _headline_case_label(name)


def _headline_case_label(value: str) -> str:
    value = (value or "").strip()
    if not value or value != value.lower():
        return value
    return re.sub(r"\b[a-z]", lambda match: match.group(0).upper(), value)
