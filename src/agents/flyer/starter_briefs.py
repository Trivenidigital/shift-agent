"""Business-category starter briefs for Flyer Studio."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class StarterBrief:
    category_id: str
    label: str
    keywords: tuple[str, ...]
    body: str
    ai_body: str = ""


STARTER_BRIEF_MARKER = "Here is a starter flyer request"
STARTER_PROMPT_OPT_OUT_HINT = (
    'Tip: reply "don\'t show sample prompts" anytime to turn off future examples '
    "for this business account."
)


_RESTAURANT_IDEAS = (
    "Create a daily thali specials flyer. Include veg, chicken, and goat specials, sides, catering note, address, phone, and delivery/payment badges. Use saved address, phone, and logo.",
    "Create an evening snacks flyer from 4 PM to 7 PM, Wednesday to Saturday. Include samosa, mirchi bajji, punugulu, masala vada, and tea. Use saved address, phone, and logo.",
)

_GROCERY_IDEAS = (
    "Create a weekly grocery deals flyer. Include produce, pantry staples, sweets/snacks, rice/flour, sale dates, address, phone, and delivery/payment badges. Use saved address, phone, and logo.",
    "Create a festive supermarket sale flyer. Include 6 to 8 product deals with prices, department labels, sale end date, address, phone, and logo. Use saved address, phone, and logo.",
)

_LOCAL_BUSINESS_IDEAS = (
    "Create a WhatsApp-ready marketing material flyer. Highlight what my business offers, how customers can text requests, available formats, pricing or offer details, and a clear call to action. Use saved address, phone, and logo.",
    "Create a clean menu-board or schedule-style flyer. Show 2 to 4 active offers, times, locations or screens, and a simple call to action. Use saved address, phone, and logo.",
)

_IDEA_SHELLS = {
    "en": {
        "lead": "Pick a sample idea to start:",
        "reply": "Reply 1 or 2. I will show the final brief before generating. Reply APPROVE only after the brief looks right.",
    },
    "te": {
        "lead": "మీకు నచ్చిన ఐడియా ఎంచుకోండి:",
        "reply": "Reply 1 or 2. I will show the final brief before generating. Reply APPROVE only after the brief looks right.",
    },
    "hi": {
        "lead": "अपनी पसंद का आइडिया चुनें:",
        "reply": "Reply 1 or 2. I will show the final brief before generating. Reply APPROVE only after the brief looks right.",
    },
}


def _briefs() -> tuple[StarterBrief, ...]:
    return (
        StarterBrief(
            category_id="restaurant",
            label="Restaurant / food special",
            keywords=(
                "restaurant", "food court", "fast food", "food truck", "food special", "food specials",
                "street food", "kitchen", "cafe", "bakery", "dosa", "biryani", "menu",
            ),
            body=(
                "Create a professional flyer for my restaurant.\n\n"
                "Main heading:\nWeekend Specials\n\n"
                "Items or offers:\nAdd my dishes, combos, prices, and timings here.\n\n"
                "Style:\nWarm, appetizing, premium local food flyer with readable menu cards, bold prices, and strong food photography.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="grocery",
            label="Grocery / supermarket",
            keywords=("grocery", "supermarket", "market", "store", "retail", "produce", "weekly sale"),
            body=(
                "Create a professional flyer for my grocery store.\n\n"
                "Main heading:\nWeekly Grocery Deals\n\n"
                "Products or offers:\nAdd product names, prices, sale dates, and any special departments here.\n\n"
                "Style:\nClean retail flyer with product deal cards, bright savings callouts, readable prices, and a trustworthy supermarket feel.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="digital_marketing_agency",
            label="Digital marketing agency",
            keywords=(
                "digital marketing", "marketing agency", "seo", "social media", "paid ads",
                "performance marketing", "aeo", "geo", "ai marketing", "content creation",
            ),
            body=(
                "Create a modern professional flyer for my digital marketing agency.\n\n"
                "Main heading:\nGrow Your Business with Modern Marketing\n\n"
                "Services:\nSocial Media Marketing, Performance Marketing, SEO, AEO, GEO, AI Marketing, Content Creation, Paid Ads\n\n"
                "Style:\nClean premium agency style focused on business growth, with dark modern colors like black, navy, purple, or blue. Use analytics dashboards, growth charts, social media, ads, and automation visuals. no food or festival visuals unless I ask for them.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
            ai_body=(
                "Create a modern professional flyer for my AI marketing agency.\n\n"
                "Main heading:\nGrow Your Business with AI-Powered Marketing\n\n"
                "Services:\nSocial Media Marketing, Performance Marketing, SEO, AEO, GEO, AI Marketing, Content Creation, Paid Ads\n\n"
                "Style:\nClean premium agency style focused on business growth, with dark modern colors like black, navy, purple, or blue. Use analytics dashboards, growth charts, social media, ads, and AI/automation visuals. no food or festival visuals unless I ask for them.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="salon_beauty",
            label="Salon / beauty",
            keywords=("salon", "beauty", "hair", "spa", "makeup", "nails", "facial", "barber"),
            body=(
                "Create a stylish flyer for my salon or beauty business.\n\n"
                "Main heading:\nFresh Look, Beautiful Confidence\n\n"
                "Services or offer:\nAdd hair, beauty, spa, makeup, nails, pricing, discount, or booking details here.\n\n"
                "Style:\nElegant beauty flyer with premium colors, clean service cards, polished lifestyle visuals, and a clear booking callout.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="realtor",
            label="Realtor / real estate",
            keywords=("realtor", "real estate", "property", "home sale", "open house", "listing"),
            body=(
                "Create a professional real estate flyer.\n\n"
                "Main heading:\nFind Your Next Home\n\n"
                "Details:\nAdd property address, open house time, price, bedrooms, bathrooms, highlights, and agent notes here.\n\n"
                "Style:\nClean upscale real estate layout with property-focused visuals, contact or licensing details I provide, modern typography, and clear contact details.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="tutor_education",
            label="Tutor / education",
            keywords=("tutor", "tuition", "education", "school", "class", "course", "academy", "learning"),
            body=(
                "Create a professional flyer for my tutoring or education service.\n\n"
                "Main heading:\nLearn Better. Score Higher.\n\n"
                "Classes or details:\nAdd subjects, grades, schedule, trial class, fees, and enrollment details here.\n\n"
                "Style:\nFriendly, credible education flyer with clean sections, learning or classroom visuals, and a clear enrollment callout.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="event_planner",
            label="Event planner",
            keywords=("event planner", "events", "wedding planner", "decor", "party planner", "catering event"),
            body=(
                "Create a premium flyer for my event planning service.\n\n"
                "Main heading:\nMake Your Event Unforgettable\n\n"
                "Services or offer:\nAdd weddings, birthdays, decor, coordination, packages, dates, and booking details here.\n\n"
                "Style:\nElegant celebration flyer with premium event visuals, service highlights, polished typography, and clear booking details.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="tax_accounting",
            label="Tax / accounting",
            keywords=("tax", "accounting", "bookkeeping", "cpa", "payroll", "finance"),
            body=(
                "Create a professional flyer for my tax or accounting service.\n\n"
                "Main heading:\nTax Help You Can Trust\n\n"
                "Services:\nAdd tax filing, bookkeeping, payroll, business setup, consultation, deadlines, and appointment details here.\n\n"
                "Style:\nClean trustworthy business flyer with calm colors, organized service cards, credibility cues, and clear appointment information.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="temple_nonprofit",
            label="Temple / nonprofit event",
            keywords=("temple", "nonprofit", "non-profit", "mandir", "church", "mosque", "fundraiser", "community"),
            body=(
                "Create a respectful community event flyer.\n\n"
                "Main heading:\nCommunity Event\n\n"
                "Event offer details:\nAdd program name, date, time, venue, donation or registration details, guests, and food/prasad notes here.\n\n"
                "Style:\nWarm, respectful, community-focused design with clear schedule, venue, and call-to-action details.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="home_services",
            label="Home services",
            keywords=("home service", "cleaning", "plumbing", "hvac", "electrician", "repair", "landscaping", "painting"),
            body=(
                "Create a professional flyer for my home services business.\n\n"
                "Main heading:\nReliable Service for Your Home\n\n"
                "Services or offer:\nAdd service list, service area, discounts, emergency availability, and booking details here.\n\n"
                "Style:\nClean trustworthy local service flyer with service icons, before/after or work visuals, strong contact details, and a clear call to book.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
        StarterBrief(
            category_id="local_business",
            label="Local business",
            keywords=(),
            body=(
                "Create a professional flyer for my business.\n\n"
                "Main heading:\nSpecial Offer\n\n"
                "Details:\nAdd what I am promoting, services or products, prices, dates, and any important customer information here.\n\n"
                "Style:\nClean modern local-business flyer with strong headline, clear offer details, attractive visuals, and easy-to-read contact information.\n\n"
                "Use my saved business name, address, phone, and logo."
            ),
        ),
    )


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9+ ]+", " ", (text or "").lower()).split())


def _contains_keyword(normalized_text: str, normalized_keyword: str) -> bool:
    if not normalized_keyword:
        return False
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])",
        normalized_text,
    ))


def _looks_like_grocery_category(normalized: str) -> bool:
    return any(_contains_keyword(normalized, key) for key in ("grocery", "supermarket"))


def all_starter_briefs() -> list[StarterBrief]:
    return list(_briefs())


def starter_brief_for_category(category: str) -> StarterBrief:
    normalized = _normalize(category)
    fallback = _briefs()[-1]
    if not normalized:
        return fallback
    if _looks_like_grocery_category(normalized):
        for brief in _briefs():
            if brief.category_id == "grocery":
                return brief
    best: tuple[int, StarterBrief] = (0, fallback)
    for brief in _briefs()[:-1]:
        score = 0
        for keyword in brief.keywords:
            key = _normalize(keyword)
            if _contains_keyword(normalized, key):
                score += max(2, len(key.split()) + 1)
        if score > best[0]:
            best = (score, brief)
    return best[1]


def starter_brief_message(
    category: str,
    *,
    business_name: str = "",
    include_opt_out_hint: bool = False,
) -> str:
    brief = starter_brief_for_category(category)
    normalized = _normalize(category)
    has_ai_claim = bool(re.search(r"(?:^| )ai(?: |$)|artificial intelligence|ai marketing|ai-powered", normalized))
    body = brief.ai_body if has_ai_claim and brief.ai_body else brief.body
    name_line = f"Business: {business_name.strip()}\n" if business_name and business_name.strip() else ""
    opt_out_hint = f"{STARTER_PROMPT_OPT_OUT_HINT}\n\n" if include_opt_out_hint else ""
    return (
        "Flyer Studio\n"
        "------------\n"
        f"{STARTER_BRIEF_MARKER}.\n"
        f"{name_line}"
        "Edit anything below and send it back.\n\n"
        f"{body}\n\n"
        f"{opt_out_hint}"
        "Reply with your edited version, or replace it with your own flyer request."
    )


def starter_idea_choices(
    category: str,
    *,
    business_name: str = "",
    language: str = "en",
) -> list[str]:
    brief = starter_brief_for_category(category)
    if brief.category_id == "restaurant":
        ideas = _RESTAURANT_IDEAS
    elif brief.category_id == "grocery":
        ideas = _GROCERY_IDEAS
    else:
        ideas = _LOCAL_BUSINESS_IDEAS
    if not business_name.strip():
        return list(ideas)
    return [idea.replace("Use saved address, phone, and logo.", "Use saved address, phone, and logo.") for idea in ideas]


def starter_idea_choices_message(
    category: str,
    *,
    business_name: str = "",
    language: str = "en",
) -> str:
    shell = _IDEA_SHELLS.get(language, _IDEA_SHELLS["en"])
    name_line = f"Business: {business_name.strip()}\n" if business_name and business_name.strip() else ""
    ideas = starter_idea_choices(category, business_name=business_name, language=language)
    numbered = "\n\n".join(f"{idx}. {idea}" for idx, idea in enumerate(ideas, start=1))
    return (
        "Flyer Studio\n"
        "------------\n"
        f"{shell['lead']}\n\n"
        f"{name_line}"
        f"{numbered}\n\n"
        f"{shell['reply']}\n"
        f"{STARTER_PROMPT_OPT_OUT_HINT}"
    )
