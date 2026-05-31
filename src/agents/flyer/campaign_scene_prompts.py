"""Campaign-scene prompt templates for Flyer Studio image generation.

Drift-check tag: extends-Hermes.

Hermes-first analysis:
- Hermes already owns the LLM/vision gateway, image generation, skill dispatch,
  identity, WhatsApp I/O, audit, approvals, and state conventions. None of those
  are re-implemented here.
- Net-new (allowed business logic): a flyer-business-specific render-prompt
  *content* library plus a pure-function deterministic selector over already-
  resolved ``FlyerProject`` state. The output is a single prompt block injected
  into ``render._image_prompt``.
- This is NOT a routing/intent/identity/audit/state/messaging substrate: there
  is no dispatch, no new state, no new approval/audit path. Selection is a
  deterministic function over a context string; Hermes still generates the image.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CampaignSceneTemplate:
    """A reusable image-generation scene direction.

    ``scene_block`` may contain ``{business}``, ``{offer}`` and ``{audience}``
    placeholders, all filled by :func:`render_campaign_scene_block`.
    """

    key: str
    summary: str
    scene_block: str


_FAMILY_DISCOVERY = CampaignSceneTemplate(
    key="family_discovery",
    summary="Warm family/community discovering and enjoying the offer",
    scene_block=(
        "Campaign scene direction (family discovery):\n"
        "- Show a warm, aspirational scene of a happy local family or community enjoying "
        "{business}'s offering, with the hero product/dish as the focal point.\n"
        "- Inviting, celebratory mood with natural lighting and relatable local people.\n"
        "- The scene supports the offer ({offer}) for {audience} and never covers the controlled copy."
    ),
)

_HUMAN_BILLBOARD = CampaignSceneTemplate(
    key="human_billboard",
    summary="Confident human presenter spotlighting the promotion",
    scene_block=(
        "Campaign scene direction (human billboard):\n"
        "- Feature a confident, friendly presenter or staff member spotlighting the promotion "
        "for {business}, drawing the eye straight to the headline offer.\n"
        "- High-energy, attention-grabbing composition; the person frames but never covers the "
        "controlled copy or the offer ({offer}).\n"
        "- Tuned to grab the attention of {audience}."
    ),
)

_STOREFRONT_SERVICE = CampaignSceneTemplate(
    key="storefront_service",
    summary="Clean storefront/service hero (safe default)",
    scene_block=(
        "Campaign scene direction (storefront/service):\n"
        "- Present a clean, professional storefront or service hero for {business}, with the "
        "offering as the focal point.\n"
        "- Trustworthy, polished local-business mood with category-appropriate imagery.\n"
        "- The scene complements the offer ({offer}) for {audience} and never overlaps the controlled copy."
    ),
)

CAMPAIGN_SCENE_TEMPLATES: tuple[CampaignSceneTemplate, ...] = (
    _FAMILY_DISCOVERY,
    _HUMAN_BILLBOARD,
    _STOREFRONT_SERVICE,
)

_TEMPLATES_BY_KEY: dict[str, CampaignSceneTemplate] = {t.key: t for t in CAMPAIGN_SCENE_TEMPLATES}

# Deterministic selection signal sets (lowercase). Together they cover the
# selection dimensions called for in the backlog: business type, promotion type,
# event/festival type, target audience, and conversion goal.
_FAMILY_DISCOVERY_SIGNALS: frozenset[str] = frozenset({
    # event / festival type
    "festival", "celebration", "diwali", "deepavali", "holi", "navratri", "ugadi",
    "pongal", "onam", "eid", "ramadan", "christmas", "easter", "new year",
    # target audience
    "family", "families", "kids", "children", "community", "together",
})
_HUMAN_BILLBOARD_SIGNALS: frozenset[str] = frozenset({
    # promotion type / conversion goal
    "sale", "discount", "offer", "deal", "bogo", "limited", "hurry", "today only",
    "grand opening", "launch", "announcement", "promo", "promotion", "% off", "percent off",
    "book now", "call now", "sign up", "register",
    # attention-led / service business type
    "salon", "spa", "barber", "fitness", "gym", "tutor", "class", "clinic", "service",
})


def _context_has(context: str, terms: frozenset[str]) -> bool:
    """Word-boundary-aware presence check. Multi-word / punctuation terms use
    substring; single-word terms use a ``\\bterm\\b`` match (so "spa" does not
    match "space")."""
    for term in terms:
        if " " in term or "-" in term or "%" in term:
            if term in context:
                return True
        elif re.search(rf"\b{re.escape(term)}\b", context):
            return True
    return False


def select_campaign_scene(context: str) -> CampaignSceneTemplate:
    """Deterministically choose a campaign-scene template from a context string
    (e.g. ``render._category_context(project)``, which already folds in business
    category, name, style, notes, and the raw request).

    First-match-wins tie-break order — ``family_discovery`` → ``human_billboard``
    → ``storefront_service`` fallback — so the same project state always yields
    the same template.
    """
    ctx = (context or "").lower()
    if _context_has(ctx, _FAMILY_DISCOVERY_SIGNALS):
        return _FAMILY_DISCOVERY
    if _context_has(ctx, _HUMAN_BILLBOARD_SIGNALS):
        return _HUMAN_BILLBOARD
    return _STOREFRONT_SERVICE


def render_campaign_scene_block(
    template: CampaignSceneTemplate,
    *,
    business: str = "",
    offer: str = "",
    audience: str = "",
) -> str:
    """Render the template's scene block, leaving no unresolved placeholders.
    Empty inputs fall back to safe generic phrasing."""
    return template.scene_block.format(
        business=(business or "the business").strip() or "the business",
        offer=(offer or "the featured offer").strip() or "the featured offer",
        audience=(audience or "local customers").strip() or "local customers",
    )


def campaign_scene_prompt_block(
    *,
    context: str,
    business: str = "",
    offer: str = "",
    audience: str = "",
) -> str:
    """Select + render in one call. Returns a prompt-ready campaign-scene block."""
    return render_campaign_scene_block(
        select_campaign_scene(context), business=business, offer=offer, audience=audience
    )
