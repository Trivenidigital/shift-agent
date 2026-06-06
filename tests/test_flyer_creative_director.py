"""Creative Director — FlyerBrief + deterministic validator + callable tool.

Slice-1 of the Flyer Marketing Agent (design 2026-06-05). Everything here is
DORMANT behind FLYER_CREATIVE_DIRECTOR_ENABLED; these tests prove the contract
offline (the gateway/OpenRouter seam is monkeypatched — NO real network).

Path setup mirrors test_flyer_schemas.py (src/platform on sys.path) PLUS
src/agents/flyer so the new flat modules (flyer_brief / flyer_brief_validator /
flyer_context_builder) import the way they do on the VPS — see facts.py.

Fact-fixture provenance mirrors facts.py: identity facts come from
profile_locked_facts() built via _fact() whose default is required=True, so the
identity fixtures below set required=True. Item names/prices are likewise
required=True there; the validator trusts each fact's own .required flag as the
sole required-authority (a required=False fact is intentionally not required).
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

_SRC = Path(__file__).resolve().parent.parent / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from schemas import FlyerLockedFact  # noqa: E402

import flyer_brief as fb  # noqa: E402
import flyer_brief_validator as fbv  # noqa: E402
import flyer_context_builder as fcb  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────


def _identity_facts() -> list[FlyerLockedFact]:
    # required=True mirrors facts.profile_locked_facts (_fact default required=True).
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact",
                        value="+1 732 555 0104", source="customer_profile", required=True),
    ]


def _combo_facts() -> list[FlyerLockedFact]:
    facts = _identity_facts()
    facts += [
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Non Veg Combo", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:0:price", label="Price",
                        value="$49.99", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:name", label="Item",
                        value="Veg Combo", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:price", label="Price",
                        value="$39.99", source="customer_text", required=True),
    ]
    return facts


_COMBO_REQUEST = (
    "Make a Memorial Day flyer for our two combos — Non Veg Combo $49.99 and "
    "Veg Combo $39.99."
)


def _combo_offer_groups() -> list[fb.OfferGroup]:
    # One OfferGroup per locked combo (item:0 / item:1) — the structural pairing the
    # firewall now requires so two combos cannot collapse into one card.
    return [
        fb.OfferGroup(kind="combo", title_ref="item:0:name", price_ref="item:0:price"),
        fb.OfferGroup(kind="combo", title_ref="item:1:name", price_ref="item:1:price"),
    ]


def _combo_brief(**overrides) -> fb.FlyerBrief:
    data = dict(
        request_intent="combo_offer",
        visual_direction=fb.VisualDirection(
            theme_family="Memorial Day patriotic Americana",
            palette=["deep red", "navy blue", "white"],
            motifs=["stars", "bunting"],
            visual_subjects=["festive cookout spread"],
        ),
        offer_structure="Two combo cards, one per combo.",
        background_brief="A textless patriotic cookout background, central area clear.",
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            fb.FactRef(fact_id="item:0:name", provenance="locked"),
            fb.FactRef(fact_id="item:0:price", provenance="locked"),
            fb.FactRef(fact_id="item:1:name", provenance="locked"),
            fb.FactRef(fact_id="item:1:price", provenance="locked"),
        ],
        offer_groups=_combo_offer_groups(),
    )
    data.update(overrides)
    return fb.FlyerBrief(**data)


# ── (a) FactRef: exactly one of fact_id / raw_span ──────────────────────────


def test_factref_requires_exactly_one_form():
    # valid: fact_id only (locked)
    assert fb.FactRef(fact_id="business_name", provenance="locked").fact_id == "business_name"
    # valid: raw_span only (customer_text)
    assert fb.FactRef(raw_span="Non Veg Combo $49.99", provenance="customer_text").raw_span

    # invalid: both set
    with pytest.raises(ValidationError):
        fb.FactRef(fact_id="business_name", raw_span="x", provenance="locked")
    # invalid: neither set
    with pytest.raises(ValidationError):
        fb.FactRef(provenance="locked")
    # invalid: neither set, no provenance either
    with pytest.raises(ValidationError):
        fb.FactRef()


def test_factref_provenance_is_derived_not_trusted():
    """A model MISLABEL of provenance must NOT fail the brief — the form
    (fact_id vs raw_span) is the sole authority and provenance is COERCED to it
    (firewall contract: a mislabel makes the model more compliant, never fails)."""
    # fact_id + WRONG provenance="customer_text" ⇒ coerced to "locked" (not rejected).
    ref = fb.FactRef(fact_id="offer:0", provenance="customer_text")
    assert ref.provenance == "locked"
    assert ref.fact_id == "offer:0"
    # raw_span + WRONG provenance="locked" ⇒ coerced to "customer_text" (not rejected).
    ref = fb.FactRef(raw_span="Non Veg Combo", provenance="locked")
    assert ref.provenance == "customer_text"
    assert ref.raw_span == "Non Veg Combo"


def test_factref_provenance_optional_and_derived_when_omitted():
    """provenance may be OMITTED entirely (the model need not supply it) — it is
    then derived from the form."""
    assert fb.FactRef(fact_id="business_name").provenance == "locked"
    assert fb.FactRef(raw_span="Veg Combo").provenance == "customer_text"


def test_flyer_brief_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _combo_brief(unexpected_field="nope")


# ── required_fact_ids authority — LOCKED FACTS ONLY (no brief field; #1/#2) ──


def test_required_fact_ids_uses_required_flag_only():
    # business identity is required=True ⇒ required-visible; nothing else needed.
    req = fbv.required_fact_ids(_identity_facts())
    assert req == {"business_name", "contact_phone"}


def test_required_fact_ids_combo_requires_item_names_and_prices():
    req = fbv.required_fact_ids(_combo_facts())
    assert {"business_name", "contact_phone",
            "item:0:name", "item:0:price",
            "item:1:name", "item:1:price"} <= req


def test_required_fact_ids_uses_required_flag_as_sole_authority():
    # Codex #2 (re-fix): .required is the SOLE authority. A fact with required=False
    # (e.g. a planner SUGGESTION pending owner confirmation) is NOT required-visible.
    # The old supplement that forced item/offer/pricing required even at required=False
    # was removed — it would have forced advisory items to render.
    facts = _identity_facts() + [
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Veg Combo", source="customer_text", required=False),
        FlyerLockedFact(fact_id="item:0:price", label="Price",
                        value="$39.99", source="customer_text", required=False),
        FlyerLockedFact(fact_id="offer:0", label="Offer",
                        value="Lucky draw with purchase", source="customer_text", required=False),
        FlyerLockedFact(fact_id="pricing_structure", label="Pricing",
                        value="Any item special", source="customer_text", required=False),
    ]
    req = fbv.required_fact_ids(facts)
    assert {"item:0:name", "item:0:price", "offer:0", "pricing_structure"}.isdisjoint(req)
    assert req == {"business_name", "contact_phone"}


def test_required_fact_ids_signature_takes_only_locked_facts():
    # Guard the BLOCKER #1 contract: the function must not accept a brief/intent arg.
    import inspect
    params = list(inspect.signature(fbv.required_fact_ids).parameters)
    assert params == ["locked_facts"]


def test_validate_rejects_locked_value_in_free_text_field():
    # Codex NEW-BYPASS: a locked TEXTUAL value (business/item name) in a free-text
    # field would render into the background OUTSIDE the overlay. Reject it.
    brief = _combo_brief(
        background_brief="A patriotic cookout poster for Lakshmi's Kitchen, center clear.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert not result.ok
    assert any("locked value outside fact_refs" in e for e in result.errors)


def test_validate_passes_clean_free_text():
    # A well-formed brief (no fact/commercial values in free-text) passes.
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok, result.errors


# ── BLOCKER #1 — model-authored intent cannot dodge a requirement ───────────


def test_intent_new_still_requires_locked_item_facts():
    """A brief claiming request_intent='new' (or 'event') must STILL be rejected
    if it omits locked item/price facts — the required set ignores the brief."""
    facts = _combo_facts()
    for dodge_intent in ("new", "event"):
        # brief references identity only, omits the locked items, claims a
        # non-itemized intent to try to dodge the item requirement. (offer_groups
        # dropped too — the coverage check (e) is what must still catch the omission.)
        brief = _combo_brief(
            request_intent=dodge_intent,
            fact_refs=[
                fb.FactRef(fact_id="business_name", provenance="locked"),
                fb.FactRef(fact_id="contact_phone", provenance="locked"),
            ],
            offer_groups=[],
        )
        result = fbv.validate(brief, facts, _COMBO_REQUEST)
        assert result.ok is False, dodge_intent
        assert any(e == "omits required fact item:0:name" for e in result.errors)
        assert any(e == "omits required fact item:1:price" for e in result.errors)


# ── MAJOR #2 — a fact marked .required must be covered ──────────────────────


def test_validate_rejects_omitting_a_required_flagged_fact():
    """An arbitrary fact with required=True (e.g. tagline/pricing_structure that
    facts.py marks required) must be covered or the brief is rejected."""
    facts = _identity_facts() + [
        FlyerLockedFact(fact_id="tagline", label="Tagline",
                        value="Fresh and festive", source="customer_text", required=True),
    ]
    # reference identity only — tagline omitted (no item facts here ⇒ no offer_groups)
    brief = _combo_brief(
        request_intent="new",
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
        ],
        offer_groups=[],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "omits required fact tagline" for e in result.errors)


# ── (b) validate REJECTS an invented raw_span ───────────────────────────────


def test_validate_rejects_invented_span():
    brief = _combo_brief(
        fact_refs=_combo_brief().fact_refs
        + [fb.FactRef(raw_span="FREE dessert with every order", provenance="customer_text")],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented span" in e for e in result.errors)


def test_validate_accepts_grounded_span_case_and_whitespace_insensitive():
    # span differs in case + collapsed whitespace but is present in the request
    brief = _combo_brief(
        fact_refs=_combo_brief().fact_refs
        + [fb.FactRef(raw_span="non veg   combo", provenance="customer_text")],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_unknown_fact_id():
    brief = _combo_brief(
        fact_refs=_combo_brief().fact_refs
        + [fb.FactRef(fact_id="item:9:name", provenance="locked")],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("unknown fact id item:9:name" in e for e in result.errors)


# ── BLOCKER #3 — commercial values in free-text fields are rejected ─────────


def test_validate_rejects_invented_price_in_background_brief():
    brief = _combo_brief(
        background_brief="A festive cookout with a big $19.99 banner painted on.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: background_brief:")
               for e in result.errors)


def test_validate_rejects_invented_price_in_offer_structure():
    # offer_structure is NON-RENDERING (never sent to image gen), so only an INVENTED
    # commercial value is blocked. "19.99" is NOT part of any locked value ($49.99 /
    # $39.99) ⇒ invented ⇒ rejected.
    brief = _combo_brief(
        offer_structure="Two combo cards, plus a hidden 19.99 upsell line.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               for e in result.errors)


def test_validate_allows_grounded_price_in_offer_structure():
    # A GROUNDED price (substring of locked $49.99) in the NON-RENDERING
    # offer_structure planning text passes — it cannot reach pixels.
    brief = _combo_brief(
        offer_structure="Two combo cards; the 49.99 combo leads.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_discount_claim_in_layout_strategy():
    # layout_strategy is NON-RENDERING; an INVENTED discount claim (no locked fact
    # covers "BOGO free") is still rejected.
    brief = _combo_brief(
        layout_strategy="Headline band, two cards, and a BOGO free banner footer.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in layout_strategy:")
               for e in result.errors)


def test_validate_allows_grounded_phone_in_grouping():
    # grouping is NON-RENDERING; the locked contact phone (+1 732 555 0104) written
    # into the planning text is GROUNDED ⇒ passes (cannot reach pixels).
    brief = _combo_brief(
        grouping=["combo 1", "call +1 732 555 0104 now"],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_invented_phone_in_grouping():
    # An INVENTED phone run (NOT the locked number) in the non-rendering grouping
    # field is still rejected — the commercial-shape detector fires, the value is
    # not grounded.
    brief = _combo_brief(
        grouping=["combo 1", "call +1 999 888 7777 now"],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in grouping:")
               for e in result.errors)


def test_validate_rejects_percent_off_in_visual_direction():
    # visual_direction is NON-RENDERING; an INVENTED discount ("50% off", no locked
    # fact covers it) is still rejected as an invented commercial value.
    brief = _combo_brief(
        visual_direction=fb.VisualDirection(
            theme_family="Memorial Day 50% off blowout",
            palette=["red"], motifs=["stars"], visual_subjects=["cookout"],
        ),
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in visual_direction:")
               for e in result.errors)


def test_validate_allows_clean_free_text():
    # "two combos" mentions a count but no price/percent/phone — must NOT trip.
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


# ── (c) SCOPE: render-reaching strictness vs non-rendering relaxation ─────────
# Operator decision 2026-06-06 (live retest) + merge-blockers: only background_brief
# is passed to the image model (can reach pixels). The structure/planning fields
# (offer_structure/layout_strategy/grouping/visual_direction) are never sent to image
# gen, so a GROUNDED commercial value there cannot render and is allowed; an INVENTED
# one (not contained in an OVERLAY-RENDERED locked fact — referenced or required) is
# still rejected. background_brief stays FULLY strict.


def _grad_facts() -> list[FlyerLockedFact]:
    """Production-faithful locked facts for the graduation/discount request, mirroring
    bare_render._build_locked_facts shape:
      - business_name / contact_phone / location  → source=customer_profile, required
        (facts.profile_locked_facts via _fact default required=True);
      - campaign_title / pricing_structure         → source=customer_text, required
        (facts.extract_text_facts via _fact default required=True).
    Constructed (not built via the real path) because extract_text_facts calls the
    Hermes gateway (build_hermes_semantic_brief_provider) — these tests are offline."""
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact",
                        value="+1 732 555 0104", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="location", label="Location",
                        value="90 Brybar Dr", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Graduation Celebration", source="customer_text", required=True),
        FlyerLockedFact(fact_id="pricing_structure", label="Pricing",
                        value="20% off all catering orders", source="customer_text", required=True),
    ]


_GRAD_REQUEST = "Make a graduation flyer for Lakshmi's Kitchen — 20% off all catering orders."


def _grad_brief(**overrides) -> fb.FlyerBrief:
    # CLEAN planning defaults (no price/discount/phone words of their own) so the only
    # commercial value under test is the one an individual case injects. fact_refs
    # cover ALL required facts so coverage(e) passes.
    data = dict(
        request_intent="new",
        visual_direction=fb.VisualDirection(
            theme_family="Graduation celebration",
            palette=["gold", "navy", "white"],
            motifs=["graduation caps"],
            visual_subjects=["celebration table spread"],
        ),
        offer_structure="One headline card with a celebratory callout.",
        layout_strategy="Headline band on top, callout in the middle, contact footer.",
        background_brief="A textless graduation celebration background, central area clear.",
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            fb.FactRef(fact_id="location", provenance="locked"),
            fb.FactRef(fact_id="campaign_title", provenance="locked"),
            fb.FactRef(fact_id="pricing_structure", provenance="locked"),
        ],
        offer_groups=[],
    )
    data.update(overrides)
    return fb.FlyerBrief(**data)


def test_grad_baseline_clean_brief_passes():
    # Sanity: the clean graduation brief (no injected commercial value) validates.
    result = fbv.validate(_grad_brief(), _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_allows_grounded_discount_in_non_rendering_fields():
    """OPERATOR REGRESSION: the (grounded) discount is locked as pricing_structure
    "20% off ..."; the model writes "20% off" into the NON-RENDERING planning fields
    offer_structure AND layout_strategy → ok=True (cannot reach pixels)."""
    brief = _grad_brief(
        offer_structure="Lead with the 20% off headline card.",
        layout_strategy="Headline band, then a 20% off callout, contact footer.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_grounded_discount_in_background_brief():
    """The SAME grounded "20% off" in background_brief (render-reaching) → ok=False:
    a commercial value can reach pixels there even though it is grounded."""
    brief = _grad_brief(
        background_brief="A graduation celebration scene with a 20% off vibe, center clear.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: background_brief:")
               for e in result.errors), result.errors


def test_validate_rejects_invented_discount_in_non_rendering_field():
    """INVENTED still blocks: "30% off" with NO locked fact covering it, written into
    the non-rendering offer_structure → ok=False (invented commercial value)."""
    brief = _grad_brief(
        offer_structure="Lead with a bold 30% off headline card.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               for e in result.errors), result.errors


def test_validate_rejects_grounded_then_invented_currency_in_non_rendering(  # Codex BLOCKER 1
):
    """ALL-HITS: a GROUNDED "20% off" FOLLOWED by an INVENTED "$5 off" in the same
    non-rendering offer_structure → ok=False (the scan does not stop at the grounded
    first hit)."""
    brief = _grad_brief(
        offer_structure="Lead with the 20% off card and a $5 off add-on.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "$5" in e for e in result.errors), result.errors


def test_validate_rejects_grounded_then_invented_percent_in_non_rendering(  # Codex BLOCKER 1
):
    """ALL-HITS: grounded "20% off" + invented "30% off" in one non-rendering field →
    ok=False (rejected on the invented "30%")."""
    brief = _grad_brief(
        offer_structure="Lead with the 20% off card, then a 30% off blowout.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "30%" in e for e in result.errors), result.errors


def test_validate_allows_all_grounded_commercial_values_in_non_rendering():
    """ALL-grounded: both locked combo prices ($49.99 from item:0:price, $39.99 from
    item:1:price — both required) written into the non-rendering offer_structure →
    ok=True (every commercial value is grounded)."""
    brief = _combo_brief(
        offer_structure="Two cards: the $49.99 combo and the $39.99 combo.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


# ── (c) OPERATIONAL all-hits, grounded-or-rejected on NON-RENDERING fields ────
# Operator round-3: re-apply the operational-claim scan to non-rendering planning
# fields with grounded-or-rejected semantics, using the PRECISE strict detector so
# creative theme/color/motif text is never flagged (the earlier over-block class).


def _grad_facts_with_service() -> list[FlyerLockedFact]:
    """Graduation facts PLUS a locked tagline carrying a GENUINE service claim, so a
    planning-field mention of "free delivery" is GROUNDED (referenced + required)."""
    return _grad_facts() + [
        FlyerLockedFact(fact_id="tagline", label="Tagline",
                        value="Free delivery on all catering orders",
                        source="customer_text", required=True),
    ]


def _grad_brief_with_service(**overrides) -> fb.FlyerBrief:
    refs = list(_grad_brief().fact_refs) + [fb.FactRef(fact_id="tagline", provenance="locked")]
    data = dict(fact_refs=refs)
    data.update(overrides)
    return _grad_brief(**data)


def test_validate_visual_direction_exact_operator_creative_string_passes():
    """The operator's EXACT prior false-positive string must NOT be rejected by the
    re-applied operational scan — it is creative theme/color/motif text, not an
    operational claim (detector-precision requirement)."""
    brief = _grad_brief(
        visual_direction=fb.VisualDirection(
            theme_family="Graduation celebration",
            palette=["gold", "navy", "white"],
            motifs=["graduation caps"],
            visual_subjects=["confetti"],
        ),
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


@pytest.mark.parametrize(
    "theme",
    [
        "Fresh spring blossoms",          # bare "fresh" — creative, not "fresh daily"
        "Award-style gold trophy motif",  # bare "award" — creative, not "award-winning"
        "Best-of-season harvest",         # bare "best" — creative, not "best in town"
        "Grand celebration energy",       # "grand" without "opening"
        "Family gathering warmth",        # "family" without "owned"
    ],
)
def test_validate_visual_direction_creative_overlap_words_pass(theme):
    """Creative words that the AGGRESSIVE background detector flags (fresh/award/best/
    grand/family) must PASS in non-rendering visual_direction — the strict detector
    requires genuine claim context."""
    brief = _grad_brief(
        visual_direction=fb.VisualDirection(
            theme_family=theme, palette=["gold"], motifs=["pattern"],
            visual_subjects=["scene"],
        ),
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, f"{theme!r}: {result.errors}"


def test_validate_rejects_invented_operational_claim_in_offer_structure():
    """An INVENTED ungrounded operational claim ("free delivery", no locked fact) in
    the non-rendering offer_structure → ok=False."""
    brief = _grad_brief(
        offer_structure="One headline card, plus free delivery on every order.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in offer_structure:")
               and "free delivery" in e for e in result.errors), result.errors


def test_validate_allows_grounded_operational_claim_in_non_rendering():
    """A GROUNDED operational claim — "free delivery" whose value resolves through the
    referenced + required tagline "Free delivery on all catering orders" — passes in
    the non-rendering offer_structure (it renders via the overlay)."""
    brief = _grad_brief_with_service(
        offer_structure="Headline card; mention free delivery prominently.",
    )
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_grounded_op_followed_by_invented_op_all_hits():
    """ALL-HITS (operational): a GROUNDED "free delivery" FOLLOWED by an INVENTED
    "now hiring" in one non-rendering field → ok=False (rejected on "now hiring")."""
    brief = _grad_brief_with_service(
        offer_structure="Card with free delivery, and a now hiring banner.",
    )
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in offer_structure:")
               and "now hiring" in e for e in result.errors), result.errors


def test_validate_rejects_operational_claim_matching_unreferenced_nonrequired_fact():
    """TIGHT GROUNDING for operational too: a "free delivery" tagline that is BOTH
    unreferenced AND non-required would never render ⇒ a planning-field "free
    delivery" is NOT grounded ⇒ rejects."""
    facts = _grad_facts() + [
        FlyerLockedFact(fact_id="tagline", label="Tagline",
                        value="Free delivery on all catering orders",
                        source="customer_text", required=False),
    ]
    brief = _grad_brief(  # tagline NOT referenced
        offer_structure="Headline card; mention free delivery prominently.",
    )
    result = fbv.validate(brief, facts, _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in offer_structure:")
               for e in result.errors), result.errors


def test_validate_background_brief_operational_claim_hard_line_even_if_grounded():
    """background_brief is the HARD LINE: an operational claim there rejects EVEN WHEN
    grounded (it reaches pixels). "free delivery" is grounded via the referenced+
    required tagline, yet background_brief still rejects it."""
    brief = _grad_brief_with_service(
        background_brief="A celebration scene with free delivery vibes, center clear.",
    )
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is False
    assert any("operational claim in textless background: background_brief" in e
               for e in result.errors), result.errors


# ── (c) round-4 BLOCKER 2: nonnumeric discount all-hits + phrase grounding ────


def test_validate_rejects_grounded_percent_then_invented_bogo_all_hits():
    """ALL-HITS for nonnumeric discount words (BLOCKER 2): a GROUNDED "20% off"
    FOLLOWED by an INVENTED "BOGO free" → ok=False (rejected on BOGO — the discount-
    word branch is no longer skipped just because a grounded numeric token exists)."""
    brief = _grad_brief(
        offer_structure="Lead with 20% off, and a BOGO free deal too.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "BOGO" in e for e in result.errors), result.errors


def test_validate_allows_grounded_free_delivery_phrase_in_non_rendering():
    """A GROUNDED discount/offer PHRASE — "free delivery" backed by the referenced+
    required tagline "Free delivery on all catering orders" — passes in the non-
    rendering offer_structure."""
    brief = _grad_brief_with_service(
        offer_structure="Mention free delivery prominently on the card.",
    )
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_invented_offer_phrase_not_grounded_by_different_offer():
    """PHRASE grounding (BLOCKER 2): an invented "free dessert" must NOT ride a locked
    "free delivery" — the WHOLE phrase (word + object) must match. With only locked
    "Free delivery on all catering orders", "free dessert" → ok=False."""
    brief = _grad_brief_with_service(
        offer_structure="Add a free dessert to every order.",
    )
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "free dessert" in e for e in result.errors), result.errors


def test_validate_must_not_add_invented_offer_phrase_not_grounded_by_different_offer():
    """BLOCKER 2 in must_not_add: "no free dessert badge" with only locked "free
    delivery ..." → ok=False (the full phrase "free dessert" is not grounded)."""
    brief = _grad_brief_with_service(must_not_add=["no free dessert badge"])
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is False
    assert any("must_not_add invents commercial value" in e and "free dessert" in e
               for e in result.errors), result.errors


# ── (c) round-5 BLOCKER: residual invented-offer words ALL-HITS (unconditional) ─


def test_validate_rejects_grounded_percent_then_invented_cashback_residual():
    """ALL-HITS residual (round-5 BLOCKER): a GROUNDED "20% off" FOLLOWED by an
    INVENTED residual word "cashback" → ok=False. The residual scan is UNCONDITIONAL
    (no longer gated behind "no numeric token"), so the grounded "20% off" no longer
    lets "cashback" ride."""
    brief = _grad_brief(
        offer_structure="Lead with 20% off, and cashback on top.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "cashback" in e for e in result.errors), result.errors


def test_validate_rejects_grounded_phrase_then_invented_cashback_residual():
    """ALL-HITS residual: a GROUNDED offer phrase "free delivery" FOLLOWED by an
    INVENTED "cashback" → ok=False (rejected on cashback)."""
    brief = _grad_brief_with_service(
        offer_structure="Mention free delivery, and cashback too.",
    )
    result = fbv.validate(brief, _grad_facts_with_service(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "cashback" in e for e in result.errors), result.errors


def test_validate_must_not_add_invented_residual_after_grounded_percent():
    """ALL-HITS residual in must_not_add (round-5 BLOCKER): "no 20% off and cashback
    badge" → ok=False (rejected on the invented "cashback", which previously rode the
    grounded "20% off")."""
    brief = _grad_brief(must_not_add=["no 20% off and cashback badge"])
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any("must_not_add invents commercial value" in e and "cashback" in e
               for e in result.errors), result.errors


def test_validate_allows_grounded_residual_word_in_non_rendering():
    """OVER-BLOCK GUARD: a residual word that IS grounded — "discount" backed by a
    referenced+required locked tagline "Member discount for all guests" — passes."""
    facts = _grad_facts() + [
        FlyerLockedFact(fact_id="tagline", label="Tagline",
                        value="Member discount for all guests",
                        source="customer_text", required=True),
    ]
    brief = _grad_brief(
        offer_structure="Highlight the discount on the headline card.",
        fact_refs=list(_grad_brief().fact_refs)
        + [fb.FactRef(fact_id="tagline", provenance="locked")],
    )
    result = fbv.validate(brief, facts, _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_invented_residual_word_no_fact():
    """An invented residual word with NO locked fact (a "voucher" nobody locked) →
    ok=False."""
    brief = _grad_brief(
        offer_structure="Add a mystery voucher to each order.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "voucher" in e for e in result.errors), result.errors


# ── (c) round-5 PRECISION GUARD: structural combo words NOT over-blocked ──────


def test_validate_production_combo_still_ok_after_residual_change():
    """OVER-BLOCK GUARD (round-5): the production-faithful combo brief still validates
    — structural "combo"/"price" words are NOT in the residual invented-offer set."""
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_combo_price_structural_words_not_overblocked():
    """A combo offer_structure that literally says "combo price" is NOT flagged — it
    is a STRUCTURAL phrase (the real price is a grounded overlay fact), excluded from
    ``_RESIDUAL_DISCOUNT_WORD_RE`` (kept aggressive only on the background hard line)."""
    brief = _combo_brief(
        offer_structure="Two combo cards; combo price shown large on each card.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert not any("invented commercial value" in e for e in result.errors), result.errors


def test_validate_graduation_still_ok_after_residual_change():
    """OVER-BLOCK GUARD (round-5): the graduation/discount brief still validates."""
    result = fbv.validate(_grad_brief(), _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


# ── (c) 2026-06-06 PRECISION FIX: generic "discount" dropped from residual scan ─
# The live graduation/discount fail-close: the model's must_not_add carried "no prices
# other than the stated discount" — a LEGITIMATE suppression referring to the grounded
# pricing_structure ("20% off entire order") — yet the residual scan flagged the GENERIC
# word "discount" as an invented commercial value, fail-closing a valid flyer. Fix:
# remove generic "discount"/"discounted" from the NON-rendering ``_RESIDUAL_DISCOUNT_WORD_RE``
# residual scan ONLY. The background_brief HARD LINE (``_DISCOUNT_CLAIM_RE``) is untouched,
# and specific invented discounts (numeric / named offer-type / BOGO) are still caught.


def _grad_facts_entire_order() -> list[FlyerLockedFact]:
    """Production-faithful graduation facts mirroring the LIVE 2026-06-06 case:
    identity (customer_profile, required) + campaign_title "Graduation Parties 2026"
    + pricing_structure "20% off entire order" (customer_text, required). Values match
    the live model output that fail-closed (the only locked offer is the 20% discount)."""
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact",
                        value="+1 732 555 0104", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="location", label="Location",
                        value="90 Brybar Dr", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Graduation Parties 2026", source="customer_text", required=True),
        FlyerLockedFact(fact_id="pricing_structure", label="Pricing",
                        value="20% off entire order", source="customer_text", required=True),
    ]


_GRAD_ENTIRE_ORDER_REQUEST = (
    "Make a Graduation Parties 2026 flyer for Lakshmi's Kitchen — 20% off entire order."
)


def _grad_brief_entire_order(**overrides) -> fb.FlyerBrief:
    """A representative graduation brief whose locked offer is the 20% discount, with
    fact_refs covering all required facts (coverage(e) passes). Clean planning defaults
    so the only value under test is what a case injects."""
    data = dict(
        request_intent="new",
        visual_direction=fb.VisualDirection(
            theme_family="Graduation celebration",
            palette=["gold", "navy", "white"],
            motifs=["graduation caps"],
            visual_subjects=["celebration table spread"],
        ),
        offer_structure="One headline card with the 20% off entire order callout.",
        layout_strategy="Headline band on top, callout in the middle, contact footer.",
        background_brief="A textless graduation celebration background, central area clear.",
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            fb.FactRef(fact_id="location", provenance="locked"),
            fb.FactRef(fact_id="campaign_title", provenance="locked"),
            fb.FactRef(fact_id="pricing_structure", provenance="locked"),
        ],
        offer_groups=[],
    )
    data.update(overrides)
    return fb.FlyerBrief(**data)


def test_validate_must_not_add_stated_discount_reference_is_grounded():
    """LIVE REGRESSION (2026-06-06): must_not_add=["no prices other than the stated
    discount"] with locked pricing_structure="20% off entire order" (required) →
    ok=True. The entry is a LEGITIMATE suppression referring to the grounded offer; the
    generic word "discount" must NOT be flagged as an invented commercial value."""
    brief = _grad_brief_entire_order(
        must_not_add=["no prices other than the stated discount"],
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is True, result.errors
    assert not any("invents commercial value" in e for e in result.errors), result.errors


def test_validate_production_graduation_must_not_add_stated_discount_ok():
    """PRODUCTION-FAITHFUL graduation (campaign_title "Graduation Parties 2026" +
    pricing_structure "20% off entire order", required): a representative brief whose
    must_not_add includes the live "no prices other than the stated discount" entry
    alongside other plausible suppressions → ok=True."""
    brief = _grad_brief_entire_order(
        must_not_add=[
            "no unrelated events",
            "no extra offers",
            "no prices other than the stated discount",
        ],
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is True, result.errors


def test_validate_bare_generic_discount_in_non_rendering_field_ok():
    """A bare generic "discount" in a NON-rendering planning field (offer_structure),
    backed by the grounded pricing_structure, → ok=True (generic category word dropped
    from the residual scan; it refers to the stated offer)."""
    brief = _grad_brief_entire_order(
        offer_structure="Highlight the discount prominently on the headline card.",
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is True, result.errors


# ── bypass NOT reopened: specific invented offers still fail-close ────────────


def test_validate_must_not_add_invented_cashback_still_closed():
    """BYPASS GUARD: must_not_add=["no cashback badge"] with NO locked cashback fact →
    ok=False. The SPECIFIC named offer type "cashback" is still in the residual scan."""
    brief = _grad_brief_entire_order(must_not_add=["no cashback badge"])
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is False
    assert any("must_not_add invents commercial value" in e and "cashback" in e
               for e in result.errors), result.errors


def test_validate_offer_structure_invented_cashback_still_closed():
    """BYPASS GUARD: offer_structure mentioning an invented "cashback" alongside the
    grounded "20% off" → ok=False (residual scan is ALL-HITS + unconditional)."""
    brief = _grad_brief_entire_order(
        offer_structure="20% off entire order and cashback on top.",
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "cashback" in e for e in result.errors), result.errors


def test_validate_invented_numeric_discount_still_closed():
    """BYPASS GUARD: an INVENTED numeric "30% off" (only locked "20% off entire order")
    in the non-rendering offer_structure → ok=False (numeric-token scan, token-anchored
    so "30%" does not ground against the locked "20%")."""
    brief = _grad_brief_entire_order(
        offer_structure="Lead with a bold 30% off headline card.",
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               and "30%" in e for e in result.errors), result.errors


# ── background_brief HARD LINE unchanged: "discount" still rejected there ─────


def test_validate_background_brief_discount_word_still_rejected():
    """HARD LINE UNCHANGED: a bare "discount" in the render-reaching background_brief →
    ok=False via ``_DISCOUNT_CLAIM_RE`` (a "discount" reaching pixels IS a price claim;
    the precision fix touches ONLY the non-rendering residual scan)."""
    brief = _grad_brief_entire_order(
        background_brief="A graduation celebration scene with a discount vibe, center clear.",
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: background_brief:")
               for e in result.errors), result.errors


def test_validate_background_brief_percent_discount_phrase_still_rejected():
    """HARD LINE UNCHANGED: "20% discount" in background_brief → ok=False (the percent
    shape AND the discount claim both reach pixels)."""
    brief = _grad_brief_entire_order(
        background_brief="A graduation scene with a 20% discount banner, center clear.",
    )
    result = fbv.validate(brief, _grad_facts_entire_order(), _GRAD_ENTIRE_ORDER_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: background_brief:")
               for e in result.errors), result.errors


def test_validate_combo_still_ok_after_discount_precision_fix():
    """COMBO REGRESSION (unchanged): the production-faithful combo brief still validates
    after the discount-precision fix (structural combo/price words untouched)."""
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


# ── (c) round-4 MAJOR: open-claims all-hits ──────────────────────────────────


def test_validate_rejects_grounded_open_then_invented_open_all_hits():
    """ALL-HITS open (MAJOR): allowed "open daily" (grounded via referenced+required
    tagline) FOLLOWED by invented "open until 10" in one non-rendering field →
    ok=False (rejected on the second open claim, not folded to the first)."""
    facts = _grad_facts() + [
        FlyerLockedFact(fact_id="tagline", label="Tagline",
                        value="Open daily for all guests",
                        source="customer_text", required=True),
    ]
    brief = _grad_brief(
        offer_structure="Note we are open daily and open until 10.",
        fact_refs=list(_grad_brief().fact_refs)
        + [fb.FactRef(fact_id="tagline", provenance="locked")],
    )
    result = fbv.validate(brief, facts, _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in offer_structure:")
               and "open until" in e for e in result.errors), result.errors


# ── (c) round-4 MINOR: bare service claims in the strict operational detector ─


def test_validate_rejects_invented_bare_service_claim_we_deliver():
    """MINOR: an invented bare service claim ("we deliver", no locked fact) in the
    non-rendering offer_structure → ok=False."""
    brief = _grad_brief(
        offer_structure="One headline card; we deliver to your door.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in offer_structure:")
               and "we deliver" in e.lower() for e in result.errors), result.errors


def test_validate_bare_service_words_do_not_flag_creative_pickup_truck():
    """The bare-service additions must keep creative theme clean — a "pickup truck"
    creative motif is NOT a service claim (the guard) and passes."""
    brief = _grad_brief(
        visual_direction=fb.VisualDirection(
            theme_family="rustic farm celebration", palette=["green", "brown"],
            motifs=["pickup truck", "hay bales"], visual_subjects=["barn"],
        ),
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_commercial_value_matching_unreferenced_nonrequired_fact():
    """TIGHT GROUNDING (merge-blocker #1): a commercial value passes in a
    non-rendering field ONLY when it resolves through a fact that ACTUALLY renders
    (referenced by fact_refs OR required). If the only locked fact carrying "20% off"
    is BOTH unreferenced AND non-required, the planning-field "20% off" is NOT
    anchored to a rendered fact → STILL rejects."""
    facts = _grad_facts()
    # make pricing_structure unreferenced AND non-required (would never render).
    facts[-1] = FlyerLockedFact(
        fact_id="pricing_structure", label="Pricing",
        value="20% off all catering orders", source="customer_text", required=False,
    )
    brief = _grad_brief(
        offer_structure="Lead with the 20% off headline card.",
        # drop the pricing_structure ref so it is neither referenced nor required.
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            fb.FactRef(fact_id="location", provenance="locked"),
            fb.FactRef(fact_id="campaign_title", provenance="locked"),
        ],
    )
    result = fbv.validate(brief, facts, _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented commercial value in offer_structure:")
               for e in result.errors), result.errors


def test_validate_allows_grounded_commercial_value_via_referenced_only_fact():
    """The render-test is referenced-OR-required: a referenced but non-required fact
    still renders, so a planning-field value contained in it is grounded → passes."""
    facts = _grad_facts()
    facts[-1] = FlyerLockedFact(
        fact_id="pricing_structure", label="Pricing",
        value="20% off all catering orders", source="customer_text", required=False,
    )
    # pricing_structure IS referenced (default _grad_brief fact_refs include it).
    brief = _grad_brief(offer_structure="Lead with the 20% off headline card.")
    result = fbv.validate(brief, facts, _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_visual_direction_benign_creative_passes():
    """visual_direction is no longer operational-over-scanned: a benign creative
    direction validates (the spec's exact example)."""
    brief = _grad_brief(
        visual_direction=fb.VisualDirection(
            theme_family="Graduation celebration",
            palette=["gold", "navy", "white"],
            motifs=["graduation caps"],
            visual_subjects=["confetti", "diplomas"],
        ),
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


@pytest.mark.parametrize(
    "bg, why",
    [
        ("A graduation scene with a 20% off vibe, center clear.", "percent-discount"),
        ("A graduation scene with a 19.99 banner, center clear.", "bare-price"),
        ("A graduation scene, call 732 555 0104, center clear.", "phone"),
        ("A graduation scene at 90 Brybar Dr on the wall, center clear.", "address"),
        ("A graduation scene happening this Saturday at 5pm, center clear.", "date-time"),
        ("A graduation scene, we are now open, center clear.", "now-open claim"),
        ("A graduation scene, open daily, center clear.", "open-daily claim"),
        ("A graduation scene with a sign reading 'Congrats', center clear.", "text-render"),
        # operator round-4 BLOCKER 1 — INVENTED address / date shapes (overlay-owned).
        ("A graduation scene at 123 Main St, center clear.", "invented address"),
        ("A graduation scene on June 15 2026, center clear.", "invented date words"),
        ("A graduation scene on 6/15/2026, center clear.", "invented numeric date"),
        ("A graduation scene, Dec 2026 vibes, center clear.", "invented month-year"),
        ("A graduation scene at 9:00 sharp, center clear.", "invented clock time"),
    ],
)
def test_validate_background_brief_stays_fully_strict(bg, why):
    """background_brief (render-reaching) REJECTS every class — commercial values,
    locked identity (address), invented address/date-time shapes, operational claims,
    text-render instructions — all stay ok=False (the firewall is NOT weakened by the
    scope change)."""
    brief = _grad_brief(background_brief=bg)
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False, f"{why}: expected rejection, got {result.errors}"


def test_validate_background_brief_invented_address_shape_message():
    """BLOCKER 1: an invented street address in background_brief rejects via the
    dedicated address-shape detector (overlay-owned contact/address never model-
    rendered, grounded OR not)."""
    brief = _grad_brief(background_brief="A scene at 123 Main Street, center clear.")
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("address shape outside fact_refs: background_brief:")
               for e in result.errors), result.errors


def test_validate_background_brief_invented_date_shape_message():
    """BLOCKER 1: an invented date in background_brief rejects via the date/time-shape
    detector."""
    brief = _grad_brief(background_brief="A scene on June 15 2026, center clear.")
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e.startswith("date/time shape outside fact_refs: background_brief:")
               for e in result.errors), result.errors


@pytest.mark.parametrize(
    "bg",
    [
        "A textless patriotic cookout spread with bunting, central area left clear.",
        "A festive graduation celebration with confetti and gold balloons, center clear.",
        "An elegant autumn harvest table with warm tones, central area open.",
        "A vibrant Diwali rangoli with marigold and crimson, center clear.",
        "A wide open background with negative space for the overlay.",
    ],
)
def test_validate_background_brief_benign_creative_not_address_or_date(bg):
    """The address/date shape detectors must NOT false-flag legitimate creative
    backgrounds (no street suffix / date / clock shape) — they stay ok=True."""
    brief = _grad_brief(background_brief=bg)
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


def test_validate_address_date_shapes_not_scanned_on_non_rendering_fields():
    """The address/date SHAPE rejects apply ONLY to the render-reaching background_brief
    (operator: do NOT add these to non-rendering fields). A grounded location/date
    resolves via fact_refs and never reaches pixels — so the SAME address text in the
    non-rendering offer_structure (with the locked location referenced+required) is NOT
    rejected by a shape detector. (90 Brybar Dr is the locked, referenced location.)"""
    brief = _grad_brief(
        offer_structure="Footer card near 90 Brybar Dr, the venue.",
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    # no address/date-shape error on the non-rendering field.
    assert not any("address shape outside fact_refs" in e for e in result.errors), result.errors
    assert not any("date/time shape outside fact_refs" in e for e in result.errors), result.errors


def test_validate_must_not_add_unchanged_strict_invented_commercial():
    """UNCHANGED (merge-blocker #2): must_not_add is suppressive, NOT a non-rendering
    planning field — it stays STRICT. An INVENTED commercial value in must_not_add is
    still rejected via the existing d-commercial block (distinct message)."""
    brief = _grad_brief(must_not_add=["no $19.99 price badge"])
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any("must_not_add invents commercial value" in e for e in result.errors), result.errors


def test_validate_must_not_add_invented_percent_not_grounded_by_locked(  # Codex BLOCKER 2
):
    """TOKEN-ANCHORED: must_not_add=["no 30% off badge"] with locked "20% off ..." →
    ok=False. The truncated-substring grounding would have falsely grounded the "0%"
    of "30%" against the "20%" of the locked value and let the invented 30% pass."""
    brief = _grad_brief(must_not_add=["no 30% off badge"])
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any("must_not_add invents commercial value" in e and "30%" in e
               for e in result.errors), result.errors


def test_validate_must_not_add_grounded_partial_discount_not_invented():
    """A must_not_add carrying a GROUNDED commercial token ("20% off", contained in the
    locked "20% off all catering orders") is NOT a FALSE-POSITIVE invented-commercial
    rejection — token-anchored grounding recognizes it (so the fix does not over-block
    a legitimately-grounded value). (Whether a *partial* of a locked value should also
    trip the full-value CONTAINMENT check is a pre-existing, out-of-scope concern; this
    asserts only that the BLOCKER-2 fix does not newly flag a grounded value.)"""
    brief = _grad_brief(must_not_add=["no 20% off badge"])
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert not any("must_not_add invents commercial value" in e for e in result.errors), result.errors


def test_validate_must_not_add_full_locked_commercial_value_containment():
    """A must_not_add naming a COMPLETE locked commercial value ("$49.99") is rejected
    by the unchanged CONTAINMENT check (not the invented-commercial check) — proving
    the locked-value containment path is intact after the BLOCKER-2 grounding change."""
    brief = _combo_brief(must_not_add=["no $49.99 price badge"])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("must_not_add contains locked value" in e for e in result.errors), result.errors
    # $49.99 is a full locked value ⇒ grounded ⇒ NOT flagged as invented.
    assert not any("must_not_add invents commercial value" in e for e in result.errors), result.errors


def test_validate_must_not_add_unchanged_strict_locked_value():
    """UNCHANGED (merge-blocker #2): a must_not_add entry CONTAINING a locked value is
    still rejected via the existing containment block."""
    brief = _grad_brief(must_not_add=["please omit Lakshmi's Kitchen from the art"])
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any("must_not_add contains locked value" in e for e in result.errors), result.errors


def test_validate_omits_required_fact_still_blocks_with_scope_change():
    """UNCHANGED: dropping a required fact from fact_refs still fails coverage(e)
    regardless of the (c) scope change."""
    brief = _grad_brief(
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            # location, campaign_title, pricing_structure dropped.
        ],
    )
    result = fbv.validate(brief, _grad_facts(), _GRAD_REQUEST)
    assert result.ok is False
    assert any(e == "omits required fact pricing_structure" for e in result.errors), result.errors


def test_first_ungrounded_commercial_full_token_discriminates_percentages():
    """Token-anchored grounding uses WHOLE tokens, not a loose substring: "30% off"
    must NOT ground against a locked "20% off ..." (both contain "0%"). Returns "" iff
    grounded, else the offending token (the operator merge-blocker the truncated
    _commercial_value_hit alone would have missed)."""
    allowed = [fbv._norm_ws("20% off all catering orders")]
    assert fbv._first_ungrounded_commercial("Lead with 20% off card.", allowed) == ""
    assert fbv._first_ungrounded_commercial("Lead with 30% off card.", allowed) == "30%"
    assert fbv._first_ungrounded_commercial("Lead with 25% off card.", allowed) == "25%"
    # whitespace-insensitive ("20 %" == "20%").
    assert fbv._first_ungrounded_commercial("Lead with 20 % off card.", allowed) == ""


def test_first_ungrounded_commercial_prices_and_phones():
    allowed = [fbv._norm_ws("$49.99"), fbv._norm_ws("+1 732 555 0104")]
    assert fbv._first_ungrounded_commercial("the 49.99 combo", allowed) == ""
    assert fbv._first_ungrounded_commercial("the 19.99 combo", allowed) == "19.99"
    # phone grounds on digits regardless of separators.
    assert fbv._first_ungrounded_commercial("call +1 732 555 0104 now", allowed) == ""
    assert fbv._first_ungrounded_commercial("call +1 999 888 7777 now", allowed) != ""


def test_first_ungrounded_commercial_all_hits_not_just_first():
    """ALL-HITS (Codex BLOCKER 1): a GROUNDED value FOLLOWED by an invented one is
    rejected on the invented one — the scan does not stop at the first hit."""
    allowed = [fbv._norm_ws("20% off all catering orders")]
    # "20%" grounds, "$5" does not → returns the ungrounded "$5".
    assert fbv._first_ungrounded_commercial("20% off and $5 off card", allowed) == "$5"
    # "20%" grounds, "30%" does not → returns the ungrounded "30%".
    assert fbv._first_ungrounded_commercial("20% off and also 30% off", allowed) == "30%"
    # both grounded → "".
    both = [fbv._norm_ws("$49.99"), fbv._norm_ws("$39.99")]
    assert fbv._first_ungrounded_commercial("the $49.99 and $39.99 combos", both) == ""


def test_first_ungrounded_commercial_nonnumeric_discount_word_never_grounds():
    """A non-numeric discount word ("BOGO"/"free") has no digit token to anchor to a
    rendered fact ⇒ never grounded ⇒ returned as invented."""
    allowed = [fbv._norm_ws("20% off all catering orders")]
    assert fbv._first_ungrounded_commercial("a BOGO free banner", allowed) != ""


def test_first_ungrounded_commercial_clean_text_returns_empty():
    # no commercial content at all ⇒ "".
    assert fbv._first_ungrounded_commercial("two combo cards side by side", []) == ""


# ── strict operational detector (operator round-3 precision) ─────────────────


@pytest.mark.parametrize(
    "creative",
    [
        "Graduation celebration, gold navy white, graduation caps",
        "Memorial Day patriotic Americana deep red navy blue white stars bunting",
        "Fresh spring blossoms pastel pink cherry blossoms garden",
        "Award-style gold trophy motif gold trophy stage",
        "Best-of-season harvest amber wheat table",
        "Grand celebration theme gold balloons party table",
        "Family gathering warmth warm tones hearts shared meal",
        "open central area left clear for text",
        "a wide open background with negative space",
        "vibrant Diwali rangoli marigold and crimson",
        "elegant wedding florals blush and ivory",
        "bold celebratory energy",
    ],
)
def test_strict_operational_hits_ignores_creative_theme(creative):
    """The strict detector flags NO creative theme/color/motif/celebration text — the
    operator's exact false-positive class plus the fresh/best/award/open edge words."""
    assert fbv._strict_operational_hits(creative) == [], creative


@pytest.mark.parametrize(
    "claim",
    [
        "open daily", "now open", "free delivery", "delivery available",
        "reservations", "catering available", "hours: 9am-9pm", "grand opening",
        "now hiring", "dine-in available", "curbside pickup", "open today 9am",
        "fresh daily", "best in town", "award-winning", "voted best",
        "since 1998", "family-owned", "order online", "closes at 9",
        "we are now open daily 9am-9pm",
    ],
)
def test_strict_operational_hits_flags_genuine_claims(claim):
    """Every genuine service/availability/hours/credential claim is detected."""
    assert fbv._strict_operational_hits(claim), claim


def test_strict_operational_hits_all_hits_in_order():
    hits = fbv._strict_operational_hits("free delivery and now hiring and open daily")
    # all three genuine claims captured (order preserved).
    assert any("free delivery" in h for h in hits)
    assert any("hiring" in h for h in hits)
    assert any("open daily" in h or "daily" in h for h in hits)


def test_first_ungrounded_operational_grounded_vs_invented():
    allowed = [fbv._norm_ws("Free delivery on all catering orders")]
    # grounded → "".
    assert fbv._first_ungrounded_operational("mention free delivery here", allowed) == ""
    # invented (no fact) → the claim string.
    assert fbv._first_ungrounded_operational("a now hiring banner", allowed) != ""
    # all-hits: grounded then invented → returns the invented one.
    out = fbv._first_ungrounded_operational("free delivery and now hiring", allowed)
    assert out and "hiring" in out.lower()


def test_first_ungrounded_operational_clean_text_returns_empty():
    assert fbv._first_ungrounded_operational("two cards side by side, gold theme", []) == ""


# ── round-4 unit tests: address/date shapes, all-hits open, offer phrases ─────


@pytest.mark.parametrize(
    "addr",
    ["123 Main St", "456 Oak Avenue", "7 Brybar Dr", "90 Brybar Drive",
     "12 King Rd", "100 Park Blvd", "Suite 200", "#42"],
)
def test_address_shape_hit_flags_addresses(addr):
    assert fbv._address_shape_hit(f"a scene at {addr}, center clear")


@pytest.mark.parametrize(
    "clean",
    ["a textless cookout spread", "gold navy white palette", "graduation caps and confetti",
     "central area left clear", "a wide open background", "warm autumn tones"],
)
def test_address_shape_hit_ignores_creative(clean):
    assert fbv._address_shape_hit(clean) == ""


@pytest.mark.parametrize(
    "dt",
    ["June 15 2026", "Dec 2026", "December 1st", "6/15/2026", "06-15-26",
     "2026-06-15", "9:00", "9:00 pm", "9 am", "this Saturday 5pm"],
)
def test_date_time_shape_hit_flags_dates(dt):
    assert fbv._date_time_shape_hit(f"a scene on {dt}, center clear")


@pytest.mark.parametrize(
    "clean",
    ["a festive celebration", "gold and navy theme", "confetti and balloons",
     "central area open for text", "warm harvest palette"],
)
def test_date_time_shape_hit_ignores_creative(clean):
    assert fbv._date_time_shape_hit(clean) == ""


def test_all_open_claim_hits_all_hits():
    """ALL operational open phrases, each on its own local window (MAJOR)."""
    hits = fbv._all_open_claim_hits("we are open daily and open until 10 tonight")
    assert len(hits) >= 2
    assert any("daily" in h for h in hits)
    assert any("until" in h for h in hits)
    # compositional "open" contributes nothing.
    assert fbv._all_open_claim_hits("an open central area left clear") == []


def test_first_ungrounded_commercial_offer_phrase_all_hits_and_grounding():
    """BLOCKER 2: offer phrases are all-hits + whole-phrase grounded."""
    allowed = [fbv._norm_ws("Free delivery on all catering orders")]
    # grounded "free delivery" → "".
    assert fbv._first_ungrounded_commercial("mention free delivery", allowed) == ""
    # invented "free dessert" not grounded by "free delivery" → returns the phrase.
    assert fbv._first_ungrounded_commercial("offer free dessert", allowed) == "free dessert"
    # grounded numeric followed by invented BOGO → returns BOGO (all-hits, not skipped).
    pct_allowed = [fbv._norm_ws("20% off all catering orders")]
    out = fbv._first_ungrounded_commercial("20% off and BOGO free", pct_allowed)
    assert out and "bogo" in out.lower()


def test_first_ungrounded_commercial_residual_words_all_hits_unconditional():
    """ROUND-5 BLOCKER: residual invented-offer-TYPE words (cashback/voucher/rebate/
    coupon/...) are ALL-HITS and UNCONDITIONAL — a grounded numeric token does NOT skip
    them. GENERIC "discount" is EXCLUDED from this scan (2026-06-06 precision fix)."""
    pct_allowed = [fbv._norm_ws("20% off all catering orders")]
    # grounded "20% off" + invented "cashback" → returns "cashback" (not skipped).
    # (use "rewards" not an offer-word like "bonus", which scan-2 would catch first.)
    out = fbv._first_ungrounded_commercial("20% off and cashback rewards", pct_allowed)
    assert out and "cashback" in out.lower()
    # grounded "20% off" alone → "".
    assert fbv._first_ungrounded_commercial("just 20% off", pct_allowed) == ""
    # a residual word grounded in a locked value → "".
    coupon_allowed = [fbv._norm_ws("Member coupon for all guests")]
    assert fbv._first_ungrounded_commercial("the coupon card", coupon_allowed) == ""
    # invented residual word, no fact → returned.
    assert fbv._first_ungrounded_commercial("a voucher giveaway", []) == "voucher"
    # PRECISION FIX (2026-06-06): a bare generic "discount" with NO locked fact is NOT
    # flagged by the residual scan — it is a generic category word, not an invented
    # offer type (numeric/named-type/BOGO invented discounts are still caught).
    assert fbv._first_ungrounded_commercial("the discount card", []) == ""


def test_residual_discount_word_re_excludes_structural_combo_words():
    """ROUND-5 PRECISION: structural words ("combo price"/"price"/"combo") are NOT in
    the residual invented-offer set (so the combo is not over-blocked); genuine
    invented-offer-TYPE words ARE. GENERIC "discount"/"discounted" is ALSO excluded
    (2026-06-06 precision fix): it is a generic category word that legitimately refers
    to a stated/grounded offer, so the non-rendering residual scan does NOT flag it
    (specific invented discounts are still caught numerically, as named types, or as
    BOGO)."""
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("combo price layout") is None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("two combo cards") is None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("price shown large") is None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("cashback offer") is not None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("voucher code") is not None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("rebate today") is not None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("coupon inside") is not None
    # GENERIC discount/discounted dropped from the residual scan (precision fix).
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("a discount") is None
    assert fbv._RESIDUAL_DISCOUNT_WORD_RE.search("discounted bundle") is None


def test_strict_operational_hits_bare_service_words():
    """MINOR: bare service words flag; pickup-truck creative motif does not."""
    assert fbv._strict_operational_hits("we deliver fast")
    assert fbv._strict_operational_hits("curbside available")
    assert fbv._strict_operational_hits("delivery to your door")
    # creative motif guard.
    assert fbv._strict_operational_hits("a pickup truck motif on a barn") == []


# ── (d/MAJOR #4) must_not_add containment (not exact-match) ─────────────────


def test_validate_rejects_must_not_add_exact_locked_value():
    brief = _combo_brief(must_not_add=["$49.99"])  # a real locked price
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("must_not_add contains locked value" in e for e in result.errors)


def test_validate_rejects_must_not_add_containing_locked_value():
    """Codex #4: 'omit Non Veg Combo' CONTAINS the locked value 'Non Veg Combo'
    and must be rejected — exact-match would have let it through."""
    brief = _combo_brief(must_not_add=["please omit Non Veg Combo from the art"])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("must_not_add contains locked value" in e for e in result.errors)


def test_validate_allows_must_not_add_unrelated_suppression():
    # a genuine suppression that names no locked value passes.
    brief = _combo_brief(must_not_add=["no stock photos of people", "no third combo"])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_allows_clean_brief():
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert result.errors == []


# ── (e) materialize_spans → FlyerLockedFact(source="customer_text") ─────────


def test_materialize_spans_builds_customer_text_locked_facts():
    brief = _combo_brief(
        fact_refs=_combo_brief().fact_refs
        + [fb.FactRef(raw_span="Non Veg Combo", provenance="customer_text"),
           fb.FactRef(raw_span="Veg Combo", provenance="customer_text")],
    )
    materialized = fbv.materialize_spans(brief, _COMBO_REQUEST)
    assert len(materialized) == 2
    for fact in materialized:
        assert isinstance(fact, FlyerLockedFact)
        assert fact.source == "customer_text"
        assert fact.fact_id.startswith("customer_span:")
        assert fact.value  # non-empty, min_length=1 honored
    assert {f.value for f in materialized} == {"Non Veg Combo", "Veg Combo"}


def test_materialize_spans_skips_invented_spans():
    # only the grounded span materializes; the invented one is dropped
    brief = _combo_brief(
        fact_refs=_combo_brief().fact_refs
        + [fb.FactRef(raw_span="Veg Combo", provenance="customer_text"),
           fb.FactRef(raw_span="invented mango lassi", provenance="customer_text")],
    )
    materialized = fbv.materialize_spans(brief, _COMBO_REQUEST)
    assert [f.value for f in materialized] == ["Veg Combo"]


# ── (f) build_flyer_brief dormancy + enabled paths (gateway mocked) ─────────


def test_build_flyer_brief_disabled_when_flag_unset(monkeypatch):
    monkeypatch.delenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)

    # Tripwire: if dormancy is broken, the gateway would be called → fail loudly.
    def _boom(*_a, **_k):
        raise AssertionError("gateway must not be called when the flag is unset")

    monkeypatch.setattr(fcb, "_call_gateway", _boom)
    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)
    # flag off ⇒ "disabled" — the ONLY status on which a caller may use the old path.
    assert result.status == "disabled"
    assert result.brief is None
    assert result.errors == []


def test_build_flyer_brief_disabled_when_flag_not_one(monkeypatch):
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "0")
    monkeypatch.setattr(
        fcb, "_call_gateway",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)
    assert result.status == "disabled"
    assert result.brief is None


def _brief_json() -> dict:
    return {
        "request_intent": "combo_offer",
        "offer_structure": "Two combo cards.",
        "visual_direction": {
            "theme_family": "Memorial Day patriotic Americana",
            "palette": ["deep red", "navy blue", "white"],
            "motifs": ["stars", "bunting"],
            "visual_subjects": ["festive cookout spread"],
        },
        "layout_strategy": "Headline band, two cards, footer.",
        "grouping": ["combo 1", "combo 2"],
        "must_not_add": ["no third combo"],
        "background_brief": "Textless patriotic cookout background.",
        "fact_refs": [
            {"fact_id": "business_name", "provenance": "locked"},
            {"fact_id": "contact_phone", "provenance": "locked"},
            {"fact_id": "item:0:name", "provenance": "locked"},
            {"fact_id": "item:0:price", "provenance": "locked"},
            {"fact_id": "item:1:name", "provenance": "locked"},
            {"fact_id": "item:1:price", "provenance": "locked"},
            {"raw_span": "Non Veg Combo", "provenance": "customer_text"},
        ],
        "offer_groups": [
            {"kind": "combo", "title_ref": "item:0:name", "price_ref": "item:0:price"},
            {"kind": "combo", "title_ref": "item:1:name", "price_ref": "item:1:price"},
        ],
    }


def test_build_flyer_brief_enabled_parses_validates_and_materializes(monkeypatch):
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setattr(fcb, "_call_gateway", lambda _system, _user: _brief_json())

    facts = _combo_facts()
    before = len(facts)
    result = fcb.build_flyer_brief(_COMBO_REQUEST, facts, None)

    assert result.status == "ok"
    assert result.errors == []
    brief = result.brief
    assert brief is not None
    assert brief.request_intent == "combo_offer"
    assert brief.visual_direction.theme_family == "Memorial Day patriotic Americana"
    # the validated customer_text span was materialized + appended in place
    appended = facts[before:]
    assert [f.fact_id for f in appended] == ["customer_span:0"]
    assert appended[0].source == "customer_text" and appended[0].value == "Non Veg Combo"


# ── MAJOR #5 — the SKILL.md body governs the gateway (it IS the brain) ──────


def test_build_flyer_brief_sends_skill_md_body_as_system_prompt(monkeypatch):
    """The gateway SYSTEM prompt must be the flyer_generation SKILL.md body, and
    the USER message must carry only the data (request + fact IDs), with NO
    creative instructions hardcoded in Python."""
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    captured = {}

    def _capture(system_prompt, user_message):
        captured["system"] = system_prompt
        captured["user"] = user_message
        return _brief_json()

    monkeypatch.setattr(fcb, "_call_gateway", _capture)
    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)
    assert result.status == "ok"
    assert result.brief is not None

    # the actual SKILL.md body content is present in the system prompt
    skill_body = fcb._skill_body()
    assert skill_body  # readable
    assert "Creative Director" in skill_body  # sanity: it's the rewritten skill
    assert captured["system"] == skill_body
    # frontmatter stripped — the system prompt is not just the raw file
    assert not captured["system"].startswith("---")

    # USER message is data-only: contains the request + fact ids, no schema/rules
    assert _COMBO_REQUEST in captured["user"]
    assert "item:0:name" in captured["user"]
    assert "available_fact_ids" in captured["user"]


def test_build_flyer_brief_unavailable_when_skill_body_unreadable(monkeypatch):
    """No brain (SKILL.md unreadable) ⇒ "unavailable" (fail safe / retry — NEVER the
    legacy path, since the firewall is armed). The gateway must not even be called."""
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setattr(fcb, "_skill_body", lambda: "")
    monkeypatch.setattr(
        fcb, "_call_gateway",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)
    assert result.status == "unavailable"
    assert result.brief is None


def test_build_flyer_brief_enabled_invalid_on_validation_failure(monkeypatch):
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    bad = _brief_json()
    bad["fact_refs"].append({"raw_span": "FREE drinks for everyone", "provenance": "customer_text"})
    monkeypatch.setattr(fcb, "_call_gateway", lambda _system, _user: bad)

    facts = _combo_facts()
    before = len(facts)
    result = fcb.build_flyer_brief(_COMBO_REQUEST, facts, None)
    # validator REJECTED ⇒ "invalid" with errors populated, brief None — the caller
    # MUST block/clarify/manual-route, never the old path.
    assert result.status == "invalid"
    assert result.brief is None
    assert result.errors  # non-empty
    assert any("invented span" in e for e in result.errors)
    # fail-safe: nothing materialized on rejection
    assert len(facts) == before


def test_build_flyer_brief_enabled_unavailable_when_gateway_empty(monkeypatch):
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setattr(fcb, "_call_gateway", lambda _system, _user: None)
    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)
    # gateway empty / key missing ⇒ "unavailable" (fail-safe/retry, not the old path).
    assert result.status == "unavailable"
    assert result.brief is None
    assert result.errors == []


def test_build_flyer_brief_enabled_unavailable_on_unparseable_response(monkeypatch):
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    # missing required visual_direction → FlyerBrief.model_validate raises →
    # unparseable brain ⇒ "unavailable" (NOT "invalid": the firewall never ran).
    monkeypatch.setattr(fcb, "_call_gateway", lambda _system, _user: {"request_intent": "new"})
    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)
    assert result.status == "unavailable"
    assert result.brief is None


# ── Finding 2 (Codex P1) — typed offer structure: distinct OfferGroup per offer ──


def test_offer_group_forbids_extra_fields():
    with pytest.raises(ValidationError):
        fb.OfferGroup(kind="combo", title_ref="item:0:name", surprise="nope")


def test_expected_offer_keys_from_locked_facts():
    # two combos (item:0:*, item:1:*) ⇒ two distinct offers; identity adds none.
    keys = fbv.expected_offer_keys(_combo_facts())
    assert keys == {"item:0", "item:1"}
    assert fbv.expected_offer_keys(_identity_facts()) == set()


def test_validate_rejects_two_combos_collapsed_into_one_group():
    """The firewall's core P1 case: a brief that references BOTH locked combos
    (coverage passes) but merges them into ONE offer_group — collapsing combo
    structure — must be REJECTED."""
    merged = fb.OfferGroup(
        kind="combo",
        title_ref="item:0:name",
        price_ref="item:0:price",
        inclusion_refs=["item:1:name", "item:1:price"],  # second combo folded in
    )
    brief = _combo_brief(offer_groups=[merged])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("combo structure collapsed: offers item:0 and item:1")
               for e in result.errors)


def test_validate_rejects_missing_distinct_card_for_a_locked_offer():
    # only ONE group, for item:0 — item:1 has no card of its own.
    brief = _combo_brief(
        offer_groups=[fb.OfferGroup(kind="combo", title_ref="item:0:name",
                                    price_ref="item:0:price")],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "offer item:1 is not grouped into a distinct card"
               for e in result.errors)


def test_validate_passes_correctly_grouped_two_combo_brief():
    # one distinct OfferGroup per locked combo ⇒ structure preserved ⇒ passes.
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_rejects_unknown_offer_group_ref():
    # a ref inside offer_groups that is not a locked fact id is an invention vector.
    brief = _combo_brief(
        offer_groups=_combo_offer_groups()
        + [fb.OfferGroup(kind="combo", title_ref="item:9:name", price_ref="item:9:price")],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "unknown offer_group ref item:9:name" for e in result.errors)


def test_validate_passes_offer_id_grouping():
    # offer:N ids (not item:N) are also distinct offers; each needs its own card.
    facts = _identity_facts() + [
        FlyerLockedFact(fact_id="offer:0", label="Offer",
                        value="Buy one get one", source="customer_text", required=True),
        FlyerLockedFact(fact_id="offer:1", label="Offer",
                        value="Free delivery over $30", source="customer_text", required=True),
    ]
    brief = _combo_brief(
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            fb.FactRef(fact_id="offer:0", provenance="locked"),
            fb.FactRef(fact_id="offer:1", provenance="locked"),
        ],
        offer_groups=[
            fb.OfferGroup(kind="offer", title_ref="offer:0"),
            fb.OfferGroup(kind="offer", title_ref="offer:1"),
        ],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is True, result.errors

    # B2 advisory split (operator-approved Option 1): these are COARSE offer:N facts
    # (NO item:N:* slots), so offer_groups is non-authoritative. Collapsing the two
    # offers into one group is now an ADVISORY warning, NOT a blocking error — required
    # coverage via fact_refs still holds, so the brief stays ok=True.
    collapsed = _combo_brief(
        fact_refs=brief.fact_refs,
        offer_groups=[fb.OfferGroup(kind="offer", title_ref="offer:0",
                                    inclusion_refs=["offer:1"])],
    )
    advisory = fbv.validate(collapsed, facts, _COMBO_REQUEST)
    assert advisory.ok is True, advisory.errors
    assert advisory.errors == []
    assert any("combo structure collapsed: offers offer:0 and offer:1" in w
               for w in advisory.warnings)


# ── B2 advisory split (operator-approved Option 1) — offer_groups NON-authoritative
#    for the COARSE offer:N case; required-coverage stays the SOLE blocking authority,
#    item-level collapse stays HARD-rejected. Production extracts each combo as ONE
#    coarse offer:N fact (no item:N:* split); the model's offer_groups may reference
#    fine item:N:price slots that don't exist as facts. ──────────────────────────


def _coarse_offer_facts() -> list[FlyerLockedFact]:
    """Production-faithful COARSE locked facts: each combo is ONE offer:N fact (the
    whole combo description), NOT split into item:N:name / item:N:price. Plus the
    identity/title facts the overlay must render. NO item:N:* facts exist."""
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact",
                        value="+1 732 555 0104", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="location", label="Location",
                        value="90 Brybar Dr", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Memorial Day", source="customer_text", required=True),
        FlyerLockedFact(fact_id="offer:0", label="Offer",
                        value="Non Veg Combo for $49.99", source="customer_text", required=True),
        FlyerLockedFact(fact_id="offer:1", label="Offer",
                        value="Veg Combo for $39.99", source="customer_text", required=True),
    ]


def _coarse_offer_fact_refs() -> list[fb.FactRef]:
    # fact_refs correctly reference the COARSE offer:N facts (so required coverage is
    # satisfiable) — exactly what production emits.
    return [
        fb.FactRef(fact_id="business_name", provenance="locked"),
        fb.FactRef(fact_id="contact_phone", provenance="locked"),
        fb.FactRef(fact_id="location", provenance="locked"),
        fb.FactRef(fact_id="campaign_title", provenance="locked"),
        fb.FactRef(fact_id="offer:0", provenance="locked"),
        fb.FactRef(fact_id="offer:1", provenance="locked"),
    ]


def test_b2_live_combo_unknown_item_refs_are_advisory_not_blocking():
    """LIVE COMBO CASE (was rejected, must now pass): coarse offer:0/offer:1 facts,
    fact_refs cover all required facts, but offer_groups reference fine item:N:price
    slots that DON'T exist as facts. The unknown refs become advisory warnings; the
    brief validates ok=True (required coverage holds)."""
    brief = _combo_brief(
        request_intent="combo_offer",
        fact_refs=_coarse_offer_fact_refs(),
        offer_groups=[
            # model referenced fine item:N:price slots that don't exist as facts.
            fb.OfferGroup(kind="combo", title_ref="offer:0", price_ref="item:0:price"),
            fb.OfferGroup(kind="combo", title_ref="offer:1", price_ref="item:1:price"),
        ],
    )
    result = fbv.validate(brief, _coarse_offer_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert result.errors == []
    # the unknown item:N:price refs surfaced as advisory warnings, NOT errors.
    assert any("unknown offer_group ref item:0:price" in w for w in result.warnings), result.warnings
    assert any("unknown offer_group ref item:1:price" in w for w in result.warnings), result.warnings


def test_b2_group_referencing_coarse_offer_directly_is_valid():
    """A group referencing offer:N DIRECTLY (coarse) validates WITHOUT item-level
    name/price slots — offer:N is its own name and the taxonomy has no separate price
    fact, so no finding is raised at all (errors AND warnings empty)."""
    brief = _combo_brief(
        request_intent="combo_offer",
        fact_refs=_coarse_offer_fact_refs(),
        offer_groups=[
            fb.OfferGroup(kind="offer", title_ref="offer:0"),
            fb.OfferGroup(kind="offer", title_ref="offer:1"),
        ],
    )
    result = fbv.validate(brief, _coarse_offer_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert result.errors == []
    assert result.warnings == []


def test_representative_combo_brief_validates_ok_true():
    """Deliverable contract: the representative live combo request — coarse
    offer:0 / offer:1 + campaign_title + identity facts, ALL fact_refs by fact_id,
    NO commercial values in any free-text field — validates ok=True. This is the
    shape the SKILL.md HARD OUTPUT RULES steer the model toward (the previously
    failing combo case)."""
    facts = _coarse_offer_facts()
    # sanity: the fixture is exactly that fact set (coarse offers + occasion + identity)
    assert {f.fact_id for f in facts} == {
        "business_name", "contact_phone", "location",
        "campaign_title", "offer:0", "offer:1",
    }
    brief = _combo_brief(
        request_intent="combo_offer",
        offer_structure="Two combo cards side by side, each with its own name and price.",
        layout_strategy="Headline band on top, two equal cards below, contact footer.",
        grouping=["combo one card", "combo two card"],
        background_brief="A textless patriotic cookout spread with bunting, central area left clear.",
        fact_refs=_coarse_offer_fact_refs(),  # every required fact, all by fact_id
        offer_groups=[
            fb.OfferGroup(kind="combo", title_ref="offer:0"),
            fb.OfferGroup(kind="combo", title_ref="offer:1"),
        ],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert result.errors == []


def test_representative_combo_brief_validates_even_with_mislabeled_provenance():
    """The same representative combo with the model MISLABELING provenance on every
    fact_id ref (provenance="customer_text") still validates ok=True — the coerce
    in FactRef makes the brief satisfiable instead of raising at model_validate."""
    facts = _coarse_offer_facts()
    mislabeled_refs = [
        fb.FactRef(fact_id=r.fact_id, provenance="customer_text")  # WRONG label
        for r in _coarse_offer_fact_refs()
    ]
    # every ref got coerced back to "locked" (the form is authoritative).
    assert all(r.provenance == "locked" for r in mislabeled_refs)
    brief = _combo_brief(
        request_intent="combo_offer",
        fact_refs=mislabeled_refs,
        offer_groups=[
            fb.OfferGroup(kind="combo", title_ref="offer:0"),
            fb.OfferGroup(kind="combo", title_ref="offer:1"),
        ],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_b2_coarse_offer_groups_never_mask_a_missing_required_fact():
    """REQUIRED COVERAGE STILL AUTHORITATIVE: even in the coarse regime where
    offer_groups is advisory, a brief whose fact_refs do NOT cover a required fact is
    STILL rejected — offer_groups being advisory must not mask a missing required
    fact. Here offer:1 is required but never referenced."""
    refs = [r for r in _coarse_offer_fact_refs() if r.fact_id != "offer:1"]
    brief = _combo_brief(
        request_intent="combo_offer",
        fact_refs=refs,
        offer_groups=[
            fb.OfferGroup(kind="combo", title_ref="offer:0", price_ref="item:0:price"),
            fb.OfferGroup(kind="combo", title_ref="offer:1", price_ref="item:1:price"),
        ],
    )
    result = fbv.validate(brief, _coarse_offer_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "omits required fact offer:1" for e in result.errors), result.errors


def test_b2_item_level_collapse_still_hard_rejected():
    """ITEM-LEVEL COLLAPSE PRESERVED: when locked_facts DO contain item:N:name /
    item:N:price facts, a brief whose offer_groups collapse two distinct items into
    one group is STILL hard-rejected (ok=False with the collapse ERROR — not a
    warning). Only the coarse offer:N case is relaxed."""
    merged = fb.OfferGroup(
        kind="combo",
        title_ref="item:0:name",
        price_ref="item:0:price",
        inclusion_refs=["item:1:name", "item:1:price"],  # second item folded in
    )
    brief = _combo_brief(offer_groups=[merged])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("combo structure collapsed: offers item:0 and item:1")
               for e in result.errors), result.errors
    # the collapse is a blocking ERROR, never a downgraded warning, in the item regime.
    assert not any("combo structure collapsed" in w for w in result.warnings)


def test_b2_item_level_unknown_ref_still_hard_rejected():
    """When item:N:* facts exist, an unknown offer_group ref is STILL a blocking error
    (the item-level invention vector is preserved) — only the coarse case downgrades."""
    brief = _combo_brief(
        offer_groups=_combo_offer_groups()
        + [fb.OfferGroup(kind="combo", title_ref="item:9:name", price_ref="item:9:price")],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "unknown offer_group ref item:9:name" for e in result.errors), result.errors


def test_has_item_level_facts_detector():
    """The regime switch: GROUNDED item:N:name / item:N:price ⇒ True; coarse offer:N
    and pure identity ⇒ False (offer_groups advisory)."""
    assert fbv._has_item_level_facts(_combo_facts()) is True
    assert fbv._has_item_level_facts(_coarse_offer_facts()) is False
    assert fbv._has_item_level_facts(_identity_facts()) is False


# ── Codex round-2 MAJOR — only GROUNDED item facts flip the regime to blocking; a
#    hermes_inferred (planner-suggested) item:N:name must NOT re-fail-close. ───────


def _inferred_item_name_fact(index: int = 0) -> FlyerLockedFact:
    """Mirror creative_planner.materialize_inferred: a planner ASSUMPTION item name —
    source="hermes_inferred", required=False (the schema default). This is what the
    bounded creative planner adds when enabled in cfg."""
    return FlyerLockedFact(
        fact_id=f"item:{index}:name", label="Item",
        value="Garlic Naan", source="hermes_inferred", required=False,
    )


def test_has_item_level_facts_ignores_hermes_inferred_item():
    """A hermes_inferred item:N:name (planner assumption, required=False) does NOT
    count as item-level — coarse offer:N + inferred item ⇒ still advisory regime."""
    facts = _coarse_offer_facts() + [_inferred_item_name_fact(0)]
    assert fbv._has_item_level_facts(facts) is False
    # a bare inferred item with no offers is likewise not item-level.
    assert fbv._has_item_level_facts([_inferred_item_name_fact(0)]) is False


def test_has_item_level_facts_ignores_optional_item_fact():
    """Defense-in-depth second signal: an item fact that is required=False is not a
    hard structural commitment even if its source is grounded — does not flip the
    regime. (Every grounded item:N:* fact from facts.py is required=True, so this
    only guards a hypothetical future optional grounded item.)"""
    optional_grounded = FlyerLockedFact(
        fact_id="item:0:name", label="Item",
        value="Side Salad", source="customer_text", required=False,
    )
    assert fbv._has_item_level_facts([optional_grounded]) is False


def test_b2_hermes_inferred_item_does_not_flip_offer_groups_to_blocking():
    """Codex round-2 MAJOR scenario: production extracts coarse offer:0/offer:1 facts;
    the planner (enabled in cfg) ALSO adds a hermes_inferred item:0:name (required=
    False). offer_groups reference fine item:0:price slots that don't exist as facts.
    The inferred item must NOT flip the regime to blocking — the unknown refs stay
    ADVISORY warnings and the brief validates ok=True (required coverage holds)."""
    facts = _coarse_offer_facts() + [_inferred_item_name_fact(0)]
    brief = _combo_brief(
        request_intent="combo_offer",
        fact_refs=_coarse_offer_fact_refs(),  # covers offer:0/offer:1 + identity/title
        offer_groups=[
            # model referenced fine item:N:price slots that don't exist as facts.
            fb.OfferGroup(kind="combo", title_ref="offer:0", price_ref="item:0:price"),
            fb.OfferGroup(kind="combo", title_ref="offer:1", price_ref="item:1:price"),
        ],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert result.errors == []
    assert any("unknown offer_group ref item:0:price" in w for w in result.warnings), result.warnings
    assert any("unknown offer_group ref item:1:price" in w for w in result.warnings), result.warnings


def test_b2_grounded_item_alongside_coarse_offers_does_flip_to_blocking():
    """Inverse guard: when a GROUNDED item:N:* fact (source=customer_text, required=
    True) is present — even alongside coarse offer:N facts — the regime IS blocking,
    so an unknown item-level offer_group ref is a hard error. This proves the fix
    excludes ONLY hermes_inferred, not all item facts."""
    facts = _coarse_offer_facts() + [
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Mango Lassi", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:0:price", label="Price",
                        value="$4.99", source="customer_text", required=True),
    ]
    refs = _coarse_offer_fact_refs() + [
        fb.FactRef(fact_id="item:0:name", provenance="locked"),
        fb.FactRef(fact_id="item:0:price", provenance="locked"),
    ]
    brief = _combo_brief(
        request_intent="combo_offer",
        fact_refs=refs,
        offer_groups=[
            fb.OfferGroup(kind="offer", title_ref="offer:0"),
            fb.OfferGroup(kind="offer", title_ref="offer:1"),
            fb.OfferGroup(kind="combo", title_ref="item:0:name", price_ref="item:0:price"),
            # unknown item-level ref — blocking because a grounded item fact exists.
            fb.OfferGroup(kind="combo", title_ref="item:9:name", price_ref="item:9:price"),
        ],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "unknown offer_group ref item:9:name" for e in result.errors), result.errors


# ── Finding 3 (Codex P2) — occasion/theme fact values allowed in visual fields ──


def _occasion_facts() -> list[FlyerLockedFact]:
    # campaign_title is an OCCASION fact in facts.py (extract_text_facts) — its value
    # "Memorial Day" legitimately appears in visual_direction.
    return _combo_facts() + [
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Memorial Day", source="customer_text", required=True),
    ]


def _occasion_brief(**overrides) -> fb.FlyerBrief:
    # cover campaign_title too so coverage(e) passes; structure unchanged.
    refs = _combo_brief().fact_refs + [fb.FactRef(fact_id="campaign_title", provenance="locked")]
    data = dict(fact_refs=refs)
    data.update(overrides)
    return _combo_brief(**data)


def test_validate_allows_occasion_value_in_visual_direction():
    """Codex P2: an occasion fact value ("Memorial Day") in theme_family is the
    SKILL's own example and MUST pass — it is a theme, not identity/commercial."""
    brief = _occasion_brief(
        visual_direction=fb.VisualDirection(
            theme_family="Memorial Day patriotic Americana",
            palette=["deep red", "navy blue", "white"],
            motifs=["stars", "bunting"], visual_subjects=["festive cookout spread"],
        ),
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_allows_occasion_value_in_background_brief():
    brief = _occasion_brief(
        background_brief="A Memorial Day cookout scene, central area left clear. No text.",
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_still_blocks_business_name_in_background_brief_with_occasion_facts():
    # the identity scope still fires: business name in background is rejected even
    # when occasion facts are present.
    brief = _occasion_brief(
        background_brief="A Memorial Day cookout poster for Lakshmi's Kitchen, center clear.",
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("locked value outside fact_refs" in e and "lakshmi's kitchen" in e.lower()
               for e in result.errors)


def test_validate_still_blocks_item_name_in_background_brief_with_occasion_facts():
    brief = _occasion_brief(
        background_brief="A Memorial Day scene featuring the Non Veg Combo platter, center clear.",
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("locked value outside fact_refs" in e and "non veg combo" in e.lower()
               for e in result.errors)


def test_validate_still_blocks_bare_price_in_background_brief_with_occasion_facts():
    # commercial SHAPE is unconditional — a bare "$49.99" still fails even though
    # occasion values are now allowed.
    brief = _occasion_brief(
        background_brief="A Memorial Day cookout with a $49.99 banner, center clear.",
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: background_brief:")
               for e in result.errors)


def test_is_occasion_theme_fact_id_classification():
    # occasion/theme/seasonal ids are exempt from the identity-value scan…
    assert fbv._is_occasion_theme_fact_id("campaign_title")
    assert fbv._is_occasion_theme_fact_id("theme_family")
    assert fbv._is_occasion_theme_fact_id("occasion")
    # …identity/commercial AND date/time ids are NOT exempt (values stay blocked).
    # `schedule`/`promotion_end` are DATE/TIME hard facts (Codex P1) — the overlay
    # renders them, so their values must not leak into model-authored background text.
    for fid in ("business_name", "contact_phone", "location", "item:0:name",
                "item:0:price", "offer:0", "pricing_structure", "tagline", "headline",
                "schedule", "promotion_end"):
        assert not fbv._is_occasion_theme_fact_id(fid), fid


def test_validate_rejects_schedule_datetime_in_textless_background():
    # Codex P1: `schedule` is a DATE/TIME hard fact (overlay-rendered), not a theme —
    # its value must not leak into model-authored background text. campaign_title
    # ("Memorial Day", the occasion) stays allowed in visual_direction.
    facts = _combo_facts() + [
        FlyerLockedFact(fact_id="schedule", label="When", value="Saturday evening",
                        source="customer_text", required=True),
        FlyerLockedFact(fact_id="campaign_title", label="Occasion", value="Memorial Day",
                        source="customer_text", required=True),
    ]
    refs = _combo_brief().fact_refs + [
        fb.FactRef(fact_id="schedule", provenance="locked"),
        fb.FactRef(fact_id="campaign_title", provenance="locked"),
    ]
    leaked = _combo_brief(
        fact_refs=refs,
        background_brief="A patriotic dinner scene for Saturday evening, center clear.",
    )
    res = fbv.validate(leaked, facts, _COMBO_REQUEST)
    assert not res.ok
    assert any("saturday evening" in e.lower() or "schedule" in e.lower() for e in res.errors)
    # campaign_title ("Memorial Day") in theme_family is still fine.
    clean = _combo_brief(fact_refs=refs)
    assert fbv.validate(clean, facts, _COMBO_REQUEST).ok, fbv.validate(clean, facts, _COMBO_REQUEST).errors


# ── Codex Finding 1 — offer_groups must SLOT each offer's required refs ──────


def test_validate_rejects_two_combo_groups_missing_a_price_ref():
    """A two-combo brief whose OfferGroups omit a price_ref (item:1's card carries the
    name but no price) is structurally incomplete and must be REJECTED — touching the
    offer is not enough; the price fact must be slotted into its own card."""
    groups = [
        fb.OfferGroup(kind="combo", title_ref="item:0:name", price_ref="item:0:price"),
        # item:1 card: name present, price_ref MISSING.
        fb.OfferGroup(kind="combo", title_ref="item:1:name"),
    ]
    brief = _combo_brief(offer_groups=groups)
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "offer item:1 missing price_ref in its card" for e in result.errors), result.errors


def test_validate_rejects_offer_price_slotted_in_wrong_card():
    """The price ref must be in the OFFER'S OWN card. Putting item:1's price into
    item:0's card (and leaving item:1 price-less) is mis-slotting — rejected."""
    groups = [
        # item:0 card also (wrongly) carries item:1's price in inclusion_refs — so
        # item:1's price ref is NOT in item:1's own price_ref slot.
        fb.OfferGroup(kind="combo", title_ref="item:0:name", price_ref="item:0:price",
                      inclusion_refs=["item:1:price"]),
        fb.OfferGroup(kind="combo", title_ref="item:1:name"),
    ]
    brief = _combo_brief(offer_groups=groups)
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    # item:0's group spans item:1 too ⇒ collapse fires; item:1 still lacks its price.
    assert any("missing price_ref" in e for e in result.errors), result.errors


def test_validate_rejects_offer_name_not_in_title_or_inclusion_slot():
    """The NAME fact must be in title_ref OR inclusion_refs. A group that only carries
    the offer's PRICE (price_ref set, name nowhere) does not slot the name."""
    groups = [
        fb.OfferGroup(kind="combo", title_ref="item:0:name", price_ref="item:0:price"),
        # item:1 card: price slotted but the NAME ref is absent entirely.
        fb.OfferGroup(kind="combo", price_ref="item:1:price"),
    ]
    brief = _combo_brief(offer_groups=groups)
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e == "offer item:1 missing title_ref in its card" for e in result.errors), result.errors


def test_validate_passes_fully_slotted_two_combo_brief():
    """A fully-slotted brief — each combo's NAME in title_ref and PRICE in price_ref of
    its OWN distinct card — PASSES (Codex Finding 1 happy path)."""
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_passes_name_in_inclusion_ref_slot():
    """The name may legitimately live in inclusion_refs (e.g. title is a banner and
    the item name is an inclusion line). Price still in price_ref ⇒ passes."""
    groups = [
        fb.OfferGroup(kind="combo", inclusion_refs=["item:0:name"], price_ref="item:0:price"),
        fb.OfferGroup(kind="combo", inclusion_refs=["item:1:name"], price_ref="item:1:price"),
    ]
    brief = _combo_brief(offer_groups=groups)
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_passes_offer_id_grouping_has_no_price_slot_requirement():
    """An offer:N offer has no separate price fact in the taxonomy, so a card with
    only title_ref=offer:N (no price_ref) is complete — the slot check must not
    demand a non-existent price ref."""
    facts = _identity_facts() + [
        FlyerLockedFact(fact_id="offer:0", label="Offer",
                        value="Buy one get one", source="customer_text", required=True),
        FlyerLockedFact(fact_id="offer:1", label="Offer",
                        value="Free delivery over thirty", source="customer_text", required=True),
    ]
    brief = _combo_brief(
        fact_refs=[
            fb.FactRef(fact_id="business_name", provenance="locked"),
            fb.FactRef(fact_id="contact_phone", provenance="locked"),
            fb.FactRef(fact_id="offer:0", provenance="locked"),
            fb.FactRef(fact_id="offer:1", provenance="locked"),
        ],
        offer_groups=[
            fb.OfferGroup(kind="offer", title_ref="offer:0"),
            fb.OfferGroup(kind="offer", title_ref="offer:1"),
        ],
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is True, result.errors


# ── Codex Finding 2 — must_not_add cannot smuggle an invented commercial value ──


def test_validate_rejects_must_not_add_inventing_a_price():
    """'no $19.99 price badge' injects a price that is NOT a locked value — a
    commercial value smuggled via the suppression list. Must be REJECTED."""
    brief = _combo_brief(must_not_add=["no $19.99 price badge"])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("must_not_add invents commercial value" in e for e in result.errors), result.errors


def test_validate_allows_must_not_add_non_commercial_suppression():
    """'do not add extra items' carries no commercial shape ⇒ PASSES."""
    brief = _combo_brief(must_not_add=["do not add extra items"])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_must_not_add_real_locked_price_is_containment_not_invention():
    """A must_not_add naming a REAL locked price ($49.99) is the containment case
    (#4), NOT the invention case — it must trip 'contains locked value', and must
    NOT be mislabeled as an invented commercial value."""
    brief = _combo_brief(must_not_add=["$49.99"])
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("must_not_add contains locked value" in e for e in result.errors), result.errors
    assert not any("invents commercial value" in e for e in result.errors), result.errors


# ── Codex Finding 3 — textless background cannot render text / invent claims ─


def test_validate_rejects_text_render_instruction_in_background():
    """'a sign reading "Open Daily"' instructs the textless background to render
    words — must be REJECTED (the text-rendering instruction detector)."""
    brief = _combo_brief(
        background_brief="A cookout scene with a sign reading 'Open Daily' on the wall.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("text rendering instruction in textless background: background_brief:")
               for e in result.errors), result.errors


def test_validate_rejects_quoted_literal_in_background():
    """Any quoted literal (length>=3) in the textless prompt is a verbatim string the
    model is told to render — rejected even without a 'sign/banner' lead word."""
    brief = _combo_brief(
        background_brief="A festive scene, banner that says 'Grand Opening' across the top.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("text rendering instruction in textless background" in e for e in result.errors), result.errors


def test_validate_rejects_operational_claim_in_background():
    """An invented non-price operational claim ('open daily') in the textless
    background must be REJECTED (the claim detector)."""
    brief = _combo_brief(
        background_brief="A patriotic cookout, open daily vibe, central area clear.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in textless background: background_brief:")
               for e in result.errors), result.errors


def test_validate_rejects_invented_operational_claim_in_visual_direction():
    """ROUND-3 (operator): non-rendering fields are GROUNDED-OR-REJECTED for genuine
    operational claims too. An INVENTED "now hiring" in visual_direction (no locked
    fact carries it) → ok=False — but via the PRECISE strict detector (NOT the old
    over-scan), so creative theme text is unaffected (see the benign test below)."""
    brief = _combo_brief(
        visual_direction=fb.VisualDirection(
            theme_family="bold now hiring energy",
            palette=["red"], motifs=["stars"], visual_subjects=["cookout"],
        ),
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in visual_direction:")
               and "now hiring" in e for e in result.errors), result.errors


def test_validate_passes_clean_textless_background():
    """A clean textless background — visual subjects only, no words, no claims —
    PASSES (Codex Finding 3 happy path)."""
    brief = _combo_brief(
        background_brief="A textless patriotic cookout spread with bunting, central area left clear.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_validate_reuses_creative_firewall_claim_detector():
    """Finding 3b reuses creative_firewall.is_hard_fact_claim — confirm it is the
    same callable the validator imported (defense-in-depth, not a private fork)."""
    import creative_firewall as cfw
    assert fbv._is_hard_fact_claim is cfw.is_hard_fact_claim


# ── "open" precision (false positive 2026-06-05) ────────────────────────────
# A textless-background brief said "an OPEN central area left clear for text" and
# the firewall rejected the whole brief as an operational claim. "open" here is a
# LAYOUT instruction (leave negative space for the deterministic overlay), not a
# "now open" business claim. The fix makes the "open" detector context-aware:
# compositional uses pass, genuine operational claims stay caught.


def test_validate_passes_compositional_open_in_background_brief():
    """The exact live false-positive case: an "open central area left clear for
    text" must validate (no operational-claim rejection from "open")."""
    brief = _occasion_brief(
        background_brief=(
            "A festive Memorial Day cookout background with an open central area "
            "left clear for text. No words anywhere."
        ),
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_operational_claim_hit_passes_compositional_open():
    # unit-level: the validator's detector returns "" for compositional "open".
    assert fbv._operational_claim_hit("an open central area left clear for text") == ""
    assert fbv._operational_claim_hit("open layout with a wide open background") == ""


def test_operational_claim_hit_still_flags_operational_open():
    # genuine business claims still trip (non-empty hit string).
    assert fbv._operational_claim_hit("we are now open daily 9am-9pm")
    assert fbv._operational_claim_hit("grand opening this weekend")
    assert fbv._operational_claim_hit("now open")
    assert fbv._operational_claim_hit("open for business")


def test_validate_still_rejects_operational_open_in_background_brief():
    """A genuine "now open daily" operational claim in the textless background must
    STILL be rejected after the precision fix (the firewall is not weakened)."""
    brief = _occasion_brief(
        background_brief="A Memorial Day scene, we are now open daily, center clear.",
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in textless background: background_brief:")
               for e in result.errors), result.errors


def test_validate_still_rejects_grand_opening_in_background_brief():
    brief = _occasion_brief(
        background_brief="A Memorial Day cookout celebrating our grand opening, center clear.",
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in textless background: background_brief:")
               for e in result.errors), result.errors


@pytest.mark.parametrize("masking_text", [
    # Codex BLOCKER 2026-06-05: a compositional "open" must not mask a co-occurring
    # operational "open". The anchored-phrase / time-signal scan is whole-text (no
    # window), so the validator catches the operational "open" even when a benign
    # "open" appears first. Memorial Day stays allowed (campaign_title occasion fact).
    "A Memorial Day scene, an open central area, open until 10, center clear.",
    "A Memorial Day cookout, open layout, opened for business, center clear.",
    "A Memorial Day scene, open layout, store open until 10pm, center clear.",
])
def test_validate_rejects_open_co_occurrence_in_background_brief(masking_text):
    brief = _occasion_brief(background_brief=masking_text)
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented operational claim in textless background" in e
               for e in result.errors), result.errors


def test_operational_claim_hit_rejects_masked_operational_open():
    # unit-level: a benign "open" before an operational "open" no longer hides it.
    assert fbv._operational_claim_hit("an open central area, open until 10")
    assert fbv._operational_claim_hit("open layout, opened for business")


def test_validate_passes_open_layout_with_memorial_day():
    """Codex MAJOR over-block regression (live-breaking): "open layout for Memorial
    Day" must validate. The old broad markers matched "day"/"Memorial Day" and
    wrongly flagged the real combo brief; the anchored-phrase design does not."""
    brief = _occasion_brief(
        background_brief=(
            "A festive Memorial Day cookout with an open layout, central area left "
            "clear for text. No words anywhere."
        ),
    )
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_open_claim_hit_fail_closed_when_classifier_unavailable(monkeypatch):
    """If creative_firewall._open_is_operational is unavailable, the validator must
    fail closed — treat any "open" token as a claim (never weaken the textless rule
    by silently passing "open" when the classifier is missing)."""
    monkeypatch.setattr(fbv, "_cf_open_is_operational", None)
    # compositional "open" is now treated as a claim (fail-closed) — non-empty hit.
    assert fbv._open_claim_hit("an open central area left clear for text")
    # operational "open" likewise flagged.
    assert fbv._open_claim_hit("now open daily")


# ── round-4: expanded operational tails + reopen at the validator level ──────


@pytest.mark.parametrize("op_text", [
    "store open weekends, center clear",
    "open weekdays, center clear",
    "open seven days a week, center clear",
    "open for lunch, center clear",
    "open for dinner, center clear",
    "open 24/7, center clear",
    "open until midnight, center clear",
    "opens at noon, center clear",
    "open at 9, center clear",
    "open from noon, center clear",
    "opening soon, center clear",
    "opening day, center clear",
    # reopen variants
    "grand reopening, center clear",
    "newly reopened, center clear",
    "reopened for business, center clear",
    "reopens monday, center clear",
    # round-5 day-tail recall (optional preposition + full/plural weekday forms)
    "open on weekends, center clear",
    "open on weekdays, center clear",
    "opens on Saturday, center clear",
    "open Saturdays, center clear",
    "open on monday, center clear",
    "open during the weekend, center clear",
    # round-5 final: bare reopen* is operational on its own (no anchor needed).
    "we reopen, center clear",
    "reopened, center clear",
    "re-open, center clear",
])
def test_validate_rejects_expanded_open_claims_in_background(op_text):
    brief = _occasion_brief(background_brief="A Memorial Day scene, " + op_text + ".")
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented operational claim in textless background" in e
               for e in result.errors), result.errors


@pytest.mark.parametrize("benign_text", [
    # open + a NON-operational (layout) tail stays benign; no new over-block.
    "an open layout, area at the center kept open, central area left clear for text",
    "an open layout for Memorial Day, center clear, with a soft background",
    "an open layout, open for seating arrangement of the spread, center clear",
    "an open space for plating the cookout spread, center clear",
    "a clear 24 inch wide open background, central area left clear for text",
    # round-5: a NON-ADJACENT day after "open" stays benign (open+layout, not
    # open [on|during]? <day>) — the live "Saturday market" theme must validate.
    "an open layout for the Saturday market scene, central area left clear for text",
    # round-5 final \b guard: "re" inside store/more does NOT trigger the reopen
    # branch — bare non-re "open" stays benign.
    "a store open layout with more open space, central area left clear for text",
])
def test_validate_passes_benign_open_tails_in_background(benign_text):
    brief = _occasion_brief(background_brief=benign_text + ".")
    result = fbv.validate(brief, _occasion_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_operational_claim_hit_fail_closed_when_broad_classifier_none(monkeypatch):
    """Codex round-4 MAJOR: if the BROAD claim classifier is unavailable (None),
    _operational_claim_hit must FAIL CLOSED — return the non-empty sentinel so the
    field is treated as carrying a claim, not silently accepted. The text used here
    has no _OPERATIONAL_CLAIM_RE / open-token match, so the broad fallback is the
    only remaining gate."""
    monkeypatch.setattr(fbv, "_is_hard_fact_claim", None)
    hit = fbv._operational_claim_hit("a perfectly clean cookout background")
    assert hit == fbv._CLAIM_CLASSIFIER_UNAVAILABLE
    assert hit  # non-empty


def test_operational_claim_hit_fail_closed_when_broad_classifier_raises(monkeypatch):
    """Codex round-4 MAJOR: if the broad classifier RAISES, fail closed (sentinel),
    never swallow the error into an accept ("")."""
    def _boom(_text):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(fbv, "_is_hard_fact_claim", _boom)
    hit = fbv._operational_claim_hit("a perfectly clean cookout background")
    assert hit == fbv._CLAIM_CLASSIFIER_UNAVAILABLE
    assert hit  # non-empty


def test_validate_fail_closed_rejects_when_broad_classifier_unavailable(monkeypatch):
    """End-to-end: with the broad classifier unavailable, an otherwise-clean brief is
    REJECTED (the textless rule treats the field as carrying a claim) — proving the
    fail-closed posture reaches validate()."""
    monkeypatch.setattr(fbv, "_is_hard_fact_claim", None)
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(fbv._CLAIM_CLASSIFIER_UNAVAILABLE in e for e in result.errors), result.errors


def test_validate_clean_brief_still_passes_with_real_broad_classifier():
    """Guard: the fail-closed change must NOT reject clean briefs in the normal case
    (the broad classifier is importable) — only when it is unavailable/raising."""
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


# ── occasion-aware background_brief exemption (operator 2026-06-06, Option A NARROW) ──
# Live combo ~50% fail-close: the model wrote "Memorial Day weekend" in background_brief
# and the date/schedule class in creative_firewall._CLAIM_PATTERNS flagged the GROUNDED
# occasion token "weekend" on the render-reaching HARD LINE. The fix exempts a grounded
# occasion token (campaign_title="Memorial Day Weekend") in a NON-scheduling context from
# the operational scan on background_brief ONLY; every other class stays strict, and the
# exemption is grounded-occasion-only (no other fact authority is weakened).


def _mdw_facts() -> list[FlyerLockedFact]:
    """Combo facts + campaign_title="Memorial Day Weekend" (the GROUNDED occasion, the
    exemption's only source). Mirrors facts.py: campaign_title is customer_text, required."""
    return _combo_facts() + [
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Memorial Day Weekend", source="customer_text", required=True),
    ]


def _mdw_brief(**overrides) -> fb.FlyerBrief:
    # cover campaign_title so coverage(e) passes; otherwise the combo brief unchanged.
    refs = _combo_brief().fact_refs + [fb.FactRef(fact_id="campaign_title", provenance="locked")]
    data = dict(fact_refs=refs)
    data.update(overrides)
    return _combo_brief(**data)


_MDW_BG_OK = (
    "A festive Memorial Day weekend cookout scene, warm light, open central area, "
    "no words anywhere"
)


def test_mdw_grounded_occasion_weekend_in_background_brief_passes():
    """OPERATOR side 1: bg "...Memorial Day weekend cookout … open central area, no words
    anywhere" WITH grounded campaign_title="Memorial Day Weekend" → ok=True (the grounded
    occasion theme token is exempted; the layout "open central area" stays compositional)."""
    brief = _mdw_brief(background_brief=_MDW_BG_OK)
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


def test_mdw_weekend_in_background_brief_without_grounded_occasion_rejects():
    """OPERATOR side 2a: the SAME bg WITHOUT any grounded occasion (campaign_title absent)
    → ok=False — "weekend" is not grounded, so it stays a date claim."""
    brief = _combo_brief(background_brief=_MDW_BG_OK)
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented operational claim in textless background: background_brief" in e
               for e in result.errors), result.errors


def test_mdw_weekend_in_background_brief_with_unrelated_occasion_rejects():
    """OPERATOR side 2b: the SAME bg with a DIFFERENT grounded occasion
    (campaign_title="Graduation Parties 2026", no "weekend") → ok=False — "weekend" is not
    grounded by that occasion, so it stays a date claim."""
    facts = _combo_facts() + [
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Graduation Parties 2026", source="customer_text", required=True),
    ]
    refs = _combo_brief().fact_refs + [fb.FactRef(fact_id="campaign_title", provenance="locked")]
    brief = _combo_brief(background_brief=_MDW_BG_OK, fact_refs=refs)
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented operational claim in textless background: background_brief" in e
               for e in result.errors), result.errors


@pytest.mark.parametrize(
    "bg, why",
    [
        ("A Memorial Day weekend scene, open this weekend, center clear",
         "open this weekend"),
        ("A Memorial Day weekend scene, sale ends this weekend, center clear",
         "sale ends this weekend"),
        ("A Memorial Day weekend scene, available all weekend, center clear",
         "available all weekend"),
        ("A Memorial Day scene happening this weekend, center clear",
         "this weekend"),
        ("A Memorial Day scene, all weekend long, center clear",
         "all weekend"),
    ],
)
def test_mdw_scheduling_context_weekend_still_rejects_even_when_grounded(bg, why):
    """OPERATOR side 3: a scheduling/availability context is NEVER exempted, even with the
    grounded "Memorial Day Weekend" occasion — "open this weekend", "sale ends this
    weekend", "available all weekend", "this weekend", "all weekend" stay ok=False."""
    brief = _mdw_brief(background_brief=bg)
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False, f"{why}: expected rejection, got ok"
    assert any("background_brief" in e for e in result.errors), result.errors


@pytest.mark.parametrize(
    "bg, why",
    [
        ("A Memorial Day weekend scene on June 15, center clear", "month-day shape"),
        ("A Memorial Day weekend scene on 6/15, center clear", "numeric date shape"),
        ("A Memorial Day weekend scene, Friday 6 PM, center clear", "weekday + clock"),
        ("A Memorial Day weekend cookout at 9:00, center clear", "clock shape"),
    ],
)
def test_mdw_explicit_date_time_shapes_still_reject_even_with_grounded_occasion(bg, why):
    """OPERATOR side 4: explicit date/time SHAPES ("June 15", "6/15", "Friday 6 PM",
    "9:00") in background_brief stay STRICT even with the grounded occasion — the exemption
    covers ONLY the occasion token, not date shapes (which the date-shape scan owns)."""
    brief = _mdw_brief(background_brief=bg)
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False, f"{why}: expected rejection, got ok"
    assert any("background_brief" in e for e in result.errors), result.errors


@pytest.mark.parametrize(
    "bg, why",
    [
        ("A Memorial Day weekend cookout with a $49.99 banner, center clear", "price"),
        ("A Memorial Day weekend scene, call 732 555 0104, center clear", "phone"),
        ("A Memorial Day weekend scene at 123 Main St, center clear", "address"),
        ("A Memorial Day weekend scene with a 30% off vibe, center clear", "discount"),
    ],
)
def test_mdw_other_claim_classes_still_reject_in_background_brief(bg, why):
    """OPERATOR do-not-exempt guard: a price / discount / phone / address in
    background_brief still rejects with the grounded occasion present — the exemption is
    occasion-token-only and does not touch the commercial / address / phone scans."""
    brief = _mdw_brief(background_brief=bg)
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False, f"{why}: expected rejection, got ok"
    assert any("background_brief" in e for e in result.errors), result.errors


def test_mdw_real_operational_claim_still_rejects_with_grounded_occasion():
    """The exemption must not let a real operational claim ride alongside the occasion: a
    grounded "Memorial Day weekend" theme PLUS an invented "now open daily" → ok=False
    (the occasion token is neutralized, "now open daily" still fires)."""
    brief = _mdw_brief(
        background_brief="A Memorial Day weekend cookout, we are now open daily, center clear",
    )
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented operational claim in textless background: background_brief" in e
               for e in result.errors), result.errors


def test_mdw_occasion_token_not_exempted_on_non_rendering_field_unchanged():
    """Scope guard: the exemption is background_brief-only. A scheduling claim on a
    non-rendering field follows the UNCHANGED non-rendering rule. Here an invented
    "now hiring" in offer_structure (with the grounded occasion) still rejects via the
    non-rendering operational scan — proving the occasion exemption did not leak there."""
    brief = _mdw_brief(
        offer_structure="Two combo cards, plus a now hiring banner.",
    )
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in offer_structure:")
               for e in result.errors), result.errors


def test_mdw_combo_production_faithful_brief_passes():
    """OPERATOR combo production-faithful: the full combo brief with bg
    "...Memorial Day weekend..." + grounded campaign_title="Memorial Day Weekend"
    validates ok=True (the previously ~50%-failing live combo case)."""
    brief = _mdw_brief(
        offer_structure="Two combo cards, one per combo.",
        layout_strategy="Headline band on top, two equal cards below, contact footer.",
        grouping=["combo one card", "combo two card"],
        background_brief=(
            "A festive Memorial Day weekend cookout spread with bunting, central area "
            "left clear. No words or lettering anywhere."
        ),
    )
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors
    assert result.errors == []


def test_graduation_discount_production_faithful_brief_still_ok():
    """OPERATOR graduation/discount production-faithful (unchanged): the grounded-discount
    graduation brief still validates after the occasion change (no occasion "weekend"
    token; the exemption is inert here)."""
    result = fbv.validate(_grad_brief(), _grad_facts(), _GRAD_REQUEST)
    assert result.ok is True, result.errors


# ── occasion-aware exemption: unit-level helpers ─────────────────────────────


def test_occasion_claim_tokens_minimal_from_memorial_day_weekend():
    """The exempt set derived from "Memorial Day Weekend" is exactly {"weekend"} — the
    only date/schedule token inside the phrase; "memorial"/"day"/"memorial day" are not
    claim-shaped, so they are never exempted."""
    assert fbv._occasion_claim_tokens(["memorial day weekend"]) == {"weekend"}


def test_occasion_claim_tokens_empty_for_non_claim_occasion():
    """An occasion phrase with no claim-shaped token (e.g. "Graduation Celebration",
    "Graduation Parties 2026") yields {} — nothing is exempted."""
    assert fbv._occasion_claim_tokens(["graduation celebration"]) == set()
    assert fbv._occasion_claim_tokens(["graduation parties 2026"]) == set()
    assert fbv._occasion_claim_tokens([]) == set()


def test_occasion_aware_operational_claim_hit_is_identity_without_tokens():
    """With NO occasion claim-tokens, the occasion-aware wrapper is byte-for-byte
    ``_operational_claim_hit`` — the fail-closed posture and all claim classes intact."""
    text = "a Memorial Day weekend cookout, no words"
    assert (fbv._occasion_aware_operational_claim_hit(text, set())
            == fbv._operational_claim_hit(text))
    # and a real claim still fires through the wrapper even with tokens present.
    assert fbv._occasion_aware_operational_claim_hit("we are now open daily", {"weekend"})


def test_strip_grounded_occasion_claims_neutralizes_all_classed_occurrences():
    """After the field-level veto has cleared, EVERY grounded date/occasion token
    occurrence is neutralized (no per-occurrence window): both "weekend" uses below are
    gone (the field has no scheduling verb — a bare layout "open" is not one)."""
    out = fbv._strip_grounded_occasion_claims(
        "a Memorial Day weekend scene, open central area, weekend cookout vibes", {"weekend"}
    )
    assert "weekend" not in out.lower()


# ── round-2 BLOCKER 1: date/occasion-class allowlist (operational words never exempt) ──


def test_occasion_claim_tokens_excludes_operational_words_in_campaign_title():
    """BLOCKER 1: a campaign_title word is exemptable ONLY if it is a DATE/OCCASION-CLASS
    token. "Free Delivery Weekend" exempts ONLY "weekend" — "free"/"delivery" are flagged
    by the operational scan but are NOT date/occasion-class, so they are never exempted."""
    assert fbv._occasion_claim_tokens(["free delivery weekend"]) == {"weekend"}
    # a purely-operational occasion title contributes NOTHING (no date/occasion token).
    assert fbv._occasion_claim_tokens(["delivery weekend sale"]) == {"weekend"}
    assert fbv._occasion_claim_tokens(["best fresh award"]) == set()


@pytest.mark.parametrize("word", [
    "weekend", "weekends", "holiday", "saturday", "sun", "december", "dec", "may",
])
def test_date_occasion_class_allowlist_accepts_date_tokens(word):
    assert fbv._DATE_OCCASION_CLASS_RE.match(word), word


@pytest.mark.parametrize("word", [
    "delivery", "sale", "best", "award", "fresh", "free", "open", "now", "hours",
    "cashback", "catering",
])
def test_date_occasion_class_allowlist_rejects_operational_words(word):
    assert fbv._DATE_OCCASION_CLASS_RE.match(word) is None, word


def test_validate_free_delivery_weekend_still_rejects_operational_words():
    """BLOCKER 1 end-to-end: campaign_title="Free Delivery Weekend"; background_brief that
    says "free" and "delivery" → ok=False (only "weekend" would exempt, "delivery"/"free"
    still trip the scan)."""
    facts = _combo_facts() + [
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Free Delivery Weekend", source="customer_text", required=True),
    ]
    refs = _combo_brief().fact_refs + [fb.FactRef(fact_id="campaign_title", provenance="locked")]
    brief = _combo_brief(
        background_brief="A free delivery weekend cookout scene, center clear, no words",
        fact_refs=refs,
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is False
    assert any("invented operational claim in textless background: background_brief" in e
               for e in result.errors), result.errors


def test_validate_delivery_weekend_theme_word_delivery_rejects():
    """BLOCKER 1: even the bare word "delivery" used as a theme (campaign_title="Delivery
    Weekend") rejects in background_brief — "delivery" is operational, never exempt."""
    facts = _combo_facts() + [
        FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                        value="Delivery Weekend", source="customer_text", required=True),
    ]
    refs = _combo_brief().fact_refs + [fb.FactRef(fact_id="campaign_title", provenance="locked")]
    brief = _combo_brief(
        background_brief="A delivery weekend scene, center clear, no words",
        fact_refs=refs,
    )
    result = fbv.validate(brief, facts, _COMBO_REQUEST)
    assert result.ok is False
    assert any("background_brief" in e for e in result.errors), result.errors


# ── round-2 BLOCKER 2: field-level scheduling/availability laundering closed ──


@pytest.mark.parametrize("bg, why", [
    ("A scene available for Memorial Day weekend, center clear", "available for"),
    ("A scene, book a table for Memorial Day weekend, center clear", "book a table"),
    ("A scene, reserve a spot for Memorial Day weekend, center clear", "reserve a spot"),
    ("A Memorial Day weekend scene, weekend availability is open, center clear", "weekend availability"),
    ("A Memorial Day scene, weekend is available, center clear", "weekend is available"),
    ("A Memorial Day weekend scene, sale ends this weekend, center clear", "sale ends"),
    ("A Memorial Day weekend scene, open until late, center clear", "until"),
    ("A Memorial Day weekend scene, hours posted, center clear", "hours"),
])
def test_validate_scheduling_claim_laundering_rejected_with_grounded_occasion(bg, why):
    """BLOCKER 2: a grounded "Memorial Day Weekend" occasion can NOT launder a scheduling/
    availability/booking claim — the field-level detector vetoes the exemption and the
    UNCHANGED scan rejects on the ORIGINAL text. All of Codex's bypass cases → ok=False."""
    brief = _mdw_brief(background_brief=bg)
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False, f"{why}: expected rejection, got ok"
    assert any("background_brief" in e for e in result.errors), result.errors


def test_has_scheduling_claim_detector_unit():
    """The field detector flags scheduling/availability/booking verbs but NOT a bare
    layout "open" (B1 owns "open" contextually)."""
    assert fbv._has_scheduling_claim("available for Memorial Day weekend")
    assert fbv._has_scheduling_claim("book a table this weekend")
    assert fbv._has_scheduling_claim("reserve a spot")
    assert fbv._has_scheduling_claim("weekend availability is open")
    assert fbv._has_scheduling_claim("sale ends this weekend")
    assert fbv._has_scheduling_claim("open until late")
    assert fbv._has_scheduling_claim("hours posted")
    assert fbv._has_scheduling_claim("all weekend long")
    # bare layout "open" / clean occasion theme are NOT scheduling claims.
    assert not fbv._has_scheduling_claim("Memorial Day weekend cookout scene, open central area")
    assert not fbv._has_scheduling_claim("a festive weekend cookout, no words anywhere")


def test_validate_grounded_occasion_no_scheduling_verb_still_passes():
    """REGRESSION (must stay green): the grounded occasion theme with NO scheduling verb —
    bare layout "open central area" — still validates ok=True after the field veto added."""
    brief = _mdw_brief(
        background_brief="Memorial Day weekend cookout scene, open central area, no words anywhere",
    )
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


# ── round-2 MINOR: year-less numeric date shapes reject in background_brief ───


@pytest.mark.parametrize("dt", ["6/15", "6-15", "06/15", "12/25", "06-15"])
def test_date_time_shape_hit_flags_yearless_numeric_dates(dt):
    """MINOR: a numeric date WITHOUT a year ("6/15", "6-15", "06/15") is a date shape."""
    assert fbv._date_time_shape_hit(f"a scene on {dt}, center clear"), dt


@pytest.mark.parametrize("notdate", ["16/9", "21/9", "99/99", "0/0"])
def test_date_time_shape_hit_ignores_non_calendar_ratios(notdate):
    """The year-less date shape is BOUNDED (month 1-12, day 1-31) so an aspect ratio
    ("16/9") or an out-of-range fraction is NOT a false date."""
    assert fbv._date_time_shape_hit(f"a {notdate} aspect background, center clear") == "", notdate


def test_validate_yearless_date_in_background_brief_rejects():
    """MINOR end-to-end: "6/15" in background_brief → ok=False via the date-shape layer
    (overlay owns the promotion date), even with the grounded occasion present."""
    brief = _mdw_brief(background_brief="A Memorial Day weekend cookout on 6/15, center clear")
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("date/time shape outside fact_refs: background_brief:")
               for e in result.errors), result.errors


# ── round-3 MINOR 1: aspect-ratio denylist excluded from the year-less date shape ─


@pytest.mark.parametrize("ratio", ["1/1", "2/3", "3/2", "3/4", "4/3", "4/5", "5/4", "9/16",
                                   "1-1", "3-4", "4-5"])
def test_date_time_shape_hit_ignores_common_aspect_ratios(ratio):
    """MINOR 1: a common photo/screen aspect ratio (1/1, 2/3, 3/2, 3/4, 4/3, 4/5, 5/4,
    9/16 — with "/" or "-") shares the bounded month/day shape but is NOT a date and must
    NOT flag (the negative-lookahead denylist)."""
    assert fbv._date_time_shape_hit(f"a {ratio} framing, center clear") == "", ratio


@pytest.mark.parametrize("dt", ["6/15", "6-15", "06/15", "12/25", "1/15", "10/31"])
def test_date_time_shape_hit_still_flags_real_yearless_dates(dt):
    """MINOR 1 guard: real year-less dates still flag (the denylist removes ONLY the eight
    common ratios, not genuine month/day dates)."""
    assert fbv._date_time_shape_hit(f"a scene on {dt}, center clear"), dt


def test_validate_aspect_ratio_in_background_brief_passes():
    """MINOR 1 end-to-end: "4/5 framing" / "3/4 crop" in background_brief → ok=True (not a
    date shape), with the grounded occasion present."""
    for bg in (
        "A Memorial Day weekend cookout scene, 4/5 framing, center clear, no words",
        "A Memorial Day weekend cookout scene, 3/4 crop, center clear, no words",
    ):
        brief = _mdw_brief(background_brief=bg)
        result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
        assert result.ok is True, (bg, result.errors)


def test_validate_yearless_date_still_rejects_after_ratio_denylist():
    """MINOR 1 guard end-to-end: "6/15" in background_brief still rejects (the ratio
    denylist did not weaken the year-less date layer)."""
    brief = _mdw_brief(background_brief="A Memorial Day weekend cookout on 6/15, center clear")
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("date/time shape outside fact_refs: background_brief:")
               for e in result.errors), result.errors


# ── round-3 MINOR 2: narrowed "available" arm of the field scheduling detector ─


def test_validate_available_creative_phrasing_passes():
    """MINOR 2: a creative "available warm light" (bare "available" + a non-scheduling
    word) with the grounded "Memorial Day Weekend" occasion no longer field-vetoes →
    ok=True (the exemption applies; "weekend" is the grounded occasion theme)."""
    brief = _mdw_brief(
        background_brief="A Memorial Day weekend cookout scene, available warm light, no words anywhere",
    )
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


@pytest.mark.parametrize("phrase", [
    "available warm light",
    "available soft glow",
    "an available open central area",
])
def test_has_scheduling_claim_bare_available_not_flagged(phrase):
    """MINOR 2 unit: bare standalone "available" + a non-scheduling word is NOT a
    scheduling claim (dropped from the detector)."""
    assert not fbv._has_scheduling_claim(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "available for Memorial Day weekend",   # available for
    "available on Saturday",                # available on
    "available this weekend",               # available this
    "available all weekend",                # available all
    "available now",                        # available now
    "available 24/7",                       # available 24
    "weekend availability is open",         # availability (noun)
    "weekend is available",                 # is available
    "tables are available",                 # are available
    "now available for booking",            # now available
])
def test_has_scheduling_claim_narrowed_available_still_flags(phrase):
    """MINOR 2 unit: every genuine scheduling form of "available/availability/is available"
    still flags after the narrowing."""
    assert fbv._has_scheduling_claim(phrase), phrase


@pytest.mark.parametrize("bg, why", [
    ("A scene available for Memorial Day weekend, center clear", "available for"),
    ("A Memorial Day weekend scene, weekend availability is open, center clear", "availability"),
    ("A Memorial Day scene, weekend is available, center clear", "is available"),
])
def test_validate_available_scheduling_forms_still_reject_after_narrowing(bg, why):
    """MINOR 2 end-to-end guard: the three BLOCKER-2 "available/availability/is available"
    laundering cases STILL reject with the grounded occasion present (narrowing did not
    reopen BLOCKER 2)."""
    brief = _mdw_brief(background_brief=bg)
    result = fbv.validate(brief, _mdw_facts(), _COMBO_REQUEST)
    assert result.ok is False, f"{why}: expected rejection, got ok"
    assert any("background_brief" in e for e in result.errors), result.errors
