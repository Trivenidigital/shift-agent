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
    # invalid: provenance/form mismatch (fact_id with customer_text)
    with pytest.raises(ValidationError):
        fb.FactRef(fact_id="business_name", provenance="customer_text")
    # invalid: provenance/form mismatch (raw_span with locked)
    with pytest.raises(ValidationError):
        fb.FactRef(raw_span="x", provenance="locked")


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
    brief = _combo_brief(
        offer_structure="Two combo cards, plus a hidden 49.99 upsell line.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: offer_structure:")
               for e in result.errors)


def test_validate_rejects_discount_claim_in_layout_strategy():
    brief = _combo_brief(
        layout_strategy="Headline band, two cards, and a BOGO free banner footer.",
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: layout_strategy:")
               for e in result.errors)


def test_validate_rejects_phone_run_in_grouping():
    brief = _combo_brief(
        grouping=["combo 1", "call +1 732 555 0104 now"],
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: grouping:")
               for e in result.errors)


def test_validate_rejects_percent_off_in_visual_direction():
    brief = _combo_brief(
        visual_direction=fb.VisualDirection(
            theme_family="Memorial Day 50% off blowout",
            palette=["red"], motifs=["stars"], visual_subjects=["cookout"],
        ),
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("commercial value outside fact_refs: visual_direction:")
               for e in result.errors)


def test_validate_allows_clean_free_text():
    # "two combos" mentions a count but no price/percent/phone — must NOT trip.
    result = fbv.validate(_combo_brief(), _combo_facts(), _COMBO_REQUEST)
    assert result.ok is True, result.errors


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


def test_validate_rejects_operational_claim_in_visual_direction():
    """The claim scan also covers visual_direction free text."""
    brief = _combo_brief(
        visual_direction=fb.VisualDirection(
            theme_family="bold now hiring energy",
            palette=["red"], motifs=["stars"], visual_subjects=["cookout"],
        ),
    )
    result = fbv.validate(brief, _combo_facts(), _COMBO_REQUEST)
    assert result.ok is False
    assert any(e.startswith("invented operational claim in textless background: visual_direction:")
               for e in result.errors), result.errors


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
