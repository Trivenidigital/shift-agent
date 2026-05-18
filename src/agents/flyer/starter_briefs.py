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


def _briefs() -> tuple[StarterBrief, ...]:
    return (
        StarterBrief(
            category_id="restaurant",
            label="Restaurant / food special",
            keywords=("restaurant", "food", "kitchen", "cafe", "bakery", "dosa", "biryani", "menu"),
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


def all_starter_briefs() -> list[StarterBrief]:
    return list(_briefs())


def starter_brief_for_category(category: str) -> StarterBrief:
    normalized = _normalize(category)
    fallback = _briefs()[-1]
    if not normalized:
        return fallback
    best: tuple[int, StarterBrief] = (0, fallback)
    for brief in _briefs()[:-1]:
        score = 0
        for keyword in brief.keywords:
            key = _normalize(keyword)
            if key and key in normalized:
                score += max(2, len(key.split()) + 1)
        if score > best[0]:
            best = (score, brief)
    return best[1]


def starter_brief_message(category: str, *, business_name: str = "") -> str:
    brief = starter_brief_for_category(category)
    normalized = _normalize(category)
    has_ai_claim = bool(re.search(r"(?:^| )ai(?: |$)|artificial intelligence|ai marketing|ai-powered", normalized))
    body = brief.ai_body if has_ai_claim and brief.ai_body else brief.body
    name_line = f"Business: {business_name.strip()}\n" if business_name and business_name.strip() else ""
    return (
        "Flyer Studio\n"
        "------------\n"
        "Here is a starter flyer request.\n"
        f"{name_line}"
        "Edit anything below and send it back.\n\n"
        f"{body}\n\n"
        "Reply with your edited version, or replace it with your own flyer request."
    )
