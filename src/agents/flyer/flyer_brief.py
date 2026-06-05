"""Creative-Director output contract for Flyer Studio — the `FlyerBrief`.

Slice-1 of the Flyer Marketing Agent (design 2026-06-05, §5). The Hermes
Creative Director skill returns ONE structured `FlyerBrief`: the *structure*
of the flyer (intent, offer structure, art direction, layout, what-not-to-add)
plus a TEXTLESS-background image prompt. It NEVER carries raw commercial values
(prices/item-names/dates/claims) as free-floating "truth" — commercial facts
appear only as a `FactRef` (a locked-fact id) or a `raw_span` (a verbatim
substring of the customer request, provenance `customer_text`).

The deterministic firewall (`flyer_brief_validator.py`) owns fact authority:
it decides which facts are *required*, validates every `FactRef`, and
materializes validated `raw_span`s into real `FlyerLockedFact`s before
anything renders. The skill may only influence grouping/emphasis/layout — not
which facts are required, and not the fact values themselves.

Pydantic v2, ``extra="forbid"`` — mirrors the style in
``src/platform/schemas.py``. This module is DORMANT in slice-1: nothing on the
live render path imports it unless ``FLYER_CREATIVE_DIRECTOR_ENABLED=1``.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# request_intent mirrors the design §5 vocabulary. "source_edit" routes to the
# verified source-edit lane (never silently recreated as a textless-background
# flyer); "new" is the textless-background path for new / source-*inspired* work.
FlyerRequestIntent = Literal["combo_offer", "menu", "event", "source_edit", "new"]

# A FactRef is EITHER a reference to an already-locked fact (provenance="locked")
# OR a verbatim span of the customer request to be materialized later
# (provenance="customer_text"). Exactly one of the two forms is set.
FactProvenance = Literal["locked", "customer_text"]


class VisualDirection(BaseModel):
    """Art-direction the skill infers from the occasion/culture/season the
    customer names. No words/text instructions here — this is *visual* taste
    only (the textless-background image prompt lives in
    ``FlyerBrief.background_brief``)."""

    model_config = ConfigDict(extra="forbid")

    theme_family: str = Field(default="", max_length=120)
    palette: list[str] = Field(default_factory=list, max_length=20)
    motifs: list[str] = Field(default_factory=list, max_length=40)
    visual_subjects: list[str] = Field(default_factory=list, max_length=40)


class FactRef(BaseModel):
    """A reference to a commercial/visible fact — never the fact's value inline.

    EXACTLY ONE of ``fact_id`` / ``raw_span`` is set, and provenance must match:
      - ``fact_id`` set  ⇒ ``provenance == "locked"``      (an existing locked fact)
      - ``raw_span`` set ⇒ ``provenance == "customer_text"`` (a verbatim request span)

    A ``raw_span`` is *validation evidence only*; deterministic code materializes
    it into a ``FlyerLockedFact(source="customer_text")`` before render. The skill
    never hands the overlay a value directly.
    """

    model_config = ConfigDict(extra="forbid")

    fact_id: Optional[str] = Field(default=None, max_length=120)
    raw_span: Optional[str] = Field(default=None, max_length=500)
    provenance: FactProvenance

    @model_validator(mode="after")
    def _exactly_one_form(self) -> "FactRef":
        has_id = bool((self.fact_id or "").strip())
        has_span = bool((self.raw_span or "").strip())
        if has_id == has_span:
            # both set OR neither set — ambiguous / empty reference
            raise ValueError("FactRef requires exactly one of fact_id or raw_span")
        if has_id and self.provenance != "locked":
            raise ValueError("fact_id requires provenance='locked'")
        if has_span and self.provenance != "customer_text":
            raise ValueError("raw_span requires provenance='customer_text'")
        return self


class OfferGroup(BaseModel):
    """ONE distinct offer/combo/item card — its structure, by locked-fact id.

    Free-text ``offer_structure`` + ``grouping`` strings let a brief reference all
    required facts yet still say "merge all offers into one panel", silently
    collapsing combo structure (invariant #4 — a wrong customer-facing structure).
    ``offer_groups`` makes the structure TYPED so the deterministic firewall can
    enforce that each locked offer maps to its OWN card.

    Every ``*_ref`` is a LOCKED FACT ID (never an inline value — no new invention
    vector); the validator rejects any ref that is not a real locked fact id. ``kind``
    is advisory ("combo" | "item" | "offer"); the firewall's pairing check is derived
    from the LOCKED FACTS, never from ``kind``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="", max_length=40)
    title_ref: Optional[str] = Field(default=None, max_length=120)
    price_ref: Optional[str] = Field(default=None, max_length=120)
    inclusion_refs: list[str] = Field(default_factory=list, max_length=50)


class FlyerBrief(BaseModel):
    """The single structured Creative-Director output for one flyer request.

    Carries NO raw commercial values: prices/item-names/dates/claims/slogans
    appear ONLY as ``fact_refs`` (by id or by verbatim request span). The
    ``background_brief`` is the TEXTLESS-background image prompt (visual subjects
    + motifs, no words / no text instructions).
    """

    model_config = ConfigDict(extra="forbid")

    request_intent: FlyerRequestIntent
    offer_structure: str = Field(default="", max_length=2000)
    visual_direction: VisualDirection
    layout_strategy: str = Field(default="", max_length=2000)
    grouping: list[str] = Field(default_factory=list, max_length=50)
    must_not_add: list[str] = Field(default_factory=list, max_length=50)
    # The TEXTLESS-background image prompt — no words/text instructions.
    background_brief: str = Field(default="", max_length=4000)
    fact_refs: list[FactRef] = Field(default_factory=list, max_length=200)
    # Typed offer structure (Codex P1): one OfferGroup per distinct combo/offer/item
    # so the firewall can reject a brief that collapses two locked offers into one card.
    offer_groups: list[OfferGroup] = Field(default_factory=list, max_length=50)
