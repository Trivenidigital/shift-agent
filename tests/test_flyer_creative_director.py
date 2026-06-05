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

    # collapsing the two offers into one group is rejected.
    collapsed = _combo_brief(
        fact_refs=brief.fact_refs,
        offer_groups=[fb.OfferGroup(kind="offer", title_ref="offer:0",
                                    inclusion_refs=["offer:1"])],
    )
    bad = fbv.validate(collapsed, facts, _COMBO_REQUEST)
    assert bad.ok is False
    assert any("combo structure collapsed: offers offer:0 and offer:1" in e
               for e in bad.errors)


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
    assert fbv._is_occasion_theme_fact_id("schedule")
    assert fbv._is_occasion_theme_fact_id("theme_family")
    assert fbv._is_occasion_theme_fact_id("occasion")
    # …identity/commercial ids are NOT exempt (their values stay blocked).
    for fid in ("business_name", "contact_phone", "location", "item:0:name",
                "item:0:price", "offer:0", "pricing_structure", "tagline", "headline"):
        assert not fbv._is_occasion_theme_fact_id(fid), fid
