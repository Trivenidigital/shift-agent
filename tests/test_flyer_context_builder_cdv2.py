"""CD v2 (Slice A, Task A3) — the Creative-Director brain PROPOSES the new
optional creative fields, parsed DEFENSIVELY.

``build_flyer_brief`` must let the Hermes brain (the gateway response) carry the
CD v2 enhancement fields — ``hero_ref``, ``supporting_refs``, ``marketing_hook``,
``offer_priority``, ``visual_direction.mood`` — into the returned ``FlyerBrief``
WITHOUT ever:
  - flipping the brief status to ``invalid``, or
  - raising / fail-closing the parse,
when the model OMITS or MALFORMS them. They are OPTIONAL enhancements: a missing
or malformed new field defaults (None / [] / None / "medium" / "") and the brief
is otherwise UNAFFECTED.

Fake provider (NO network, NO OpenRouter spend): these tests monkeypatch
``flyer_context_builder._call_gateway`` to return the model's parsed-JSON Mapping
directly — the SAME offline mechanism the existing slice-1 suite uses
(test_flyer_creative_director.py::test_build_flyer_brief_enabled_*). Path setup
mirrors that file (src/platform + src/agents/flyer on sys.path, the way the flat
VPS modules import).
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from schemas import FlyerLockedFact  # noqa: E402

import flyer_context_builder as fcb  # noqa: E402


# ── fixtures (mirror test_flyer_creative_director.py) ────────────────────────


def _identity_facts() -> list[FlyerLockedFact]:
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="contact_phone", label="Contact",
                        value="+1 732 555 0104", source="customer_profile", required=True),
    ]


def _combo_facts() -> list[FlyerLockedFact]:
    return _identity_facts() + [
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Non Veg Combo", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:0:price", label="Price",
                        value="$49.99", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:name", label="Item",
                        value="Veg Combo", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:price", label="Price",
                        value="$39.99", source="customer_text", required=True),
    ]


_COMBO_REQUEST = (
    "Make a Memorial Day flyer for our two combos — Non Veg Combo $49.99 and "
    "Veg Combo $39.99."
)


def _base_brief_json() -> dict:
    """A valid FlyerBrief candidate (mirrors test_flyer_creative_director._brief_json)
    WITHOUT any CD v2 new fields — cases below add/mutate the new fields only."""
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
        ],
        "offer_groups": [
            {"kind": "combo", "title_ref": "item:0:name", "price_ref": "item:0:price"},
            {"kind": "combo", "title_ref": "item:1:name", "price_ref": "item:1:price"},
        ],
    }


def _arm(monkeypatch, brief_json: dict) -> None:
    """Arm the flag + install the offline fake provider (no network)."""
    monkeypatch.setenv(fcb.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setattr(fcb, "_call_gateway", lambda _system, _user: brief_json)


# ── (1) model INCLUDES all CD v2 fields → parsed onto the FlyerBrief ─────────


def test_cdv2_fields_included_are_parsed_onto_brief(monkeypatch):
    brief_json = _base_brief_json()
    brief_json["hero_ref"] = {"fact_id": "item:1:name"}
    brief_json["supporting_refs"] = [{"fact_id": "item:0:name"}]
    brief_json["marketing_hook"] = {
        "text_ref": {"fact_id": "pricing_structure"},
        "prominence": "high",
    }
    brief_json["offer_priority"] = "high"
    brief_json["visual_direction"]["mood"] = "Warm Restaurant Promo"
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    brief = result.brief
    assert brief is not None

    # hero_ref: a single FactRef referencing the locked item name
    assert brief.hero_ref is not None
    assert brief.hero_ref.fact_id == "item:1:name"
    assert brief.hero_ref.provenance == "locked"

    # supporting_refs: list of FactRef
    assert [r.fact_id for r in brief.supporting_refs] == ["item:0:name"]

    # marketing_hook: a MarketingHook whose text is a FactRef (by id) + prominence
    assert brief.marketing_hook is not None
    assert brief.marketing_hook.text_ref.fact_id == "pricing_structure"
    assert brief.marketing_hook.prominence == "high"

    # offer_priority + mood
    assert brief.offer_priority == "high"
    assert brief.visual_direction.mood == "Warm Restaurant Promo"


# ── (2) model OMITS all CD v2 fields → defaults, brief unaffected ────────────


def test_cdv2_fields_omitted_default_and_brief_parses(monkeypatch):
    """A model response with NONE of the new fields parses fine; every new field
    sits at its default and the rest of the brief is intact."""
    _arm(monkeypatch, _base_brief_json())

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    brief = result.brief
    assert brief is not None

    assert brief.hero_ref is None
    assert brief.supporting_refs == []
    assert brief.marketing_hook is None
    assert brief.offer_priority == "medium"
    assert brief.visual_direction.mood == ""

    # the (unchanged) core brief still parsed correctly
    assert brief.request_intent == "combo_offer"
    assert brief.visual_direction.theme_family == "Memorial Day patriotic Americana"


# ── (3) MALFORMED new fields → default the offender, never invalid/raise ─────


def test_cdv2_malformed_hero_ref_defaults_to_none_not_invalid(monkeypatch):
    """A hero_ref with BOTH fact_id and raw_span set (FactRef's validator would
    RAISE) must NOT raise and must NOT flip the status to invalid — hero_ref
    defaults to None and the rest of the brief parses + validates → ok."""
    brief_json = _base_brief_json()
    brief_json["hero_ref"] = {"fact_id": "x", "raw_span": "y"}  # both set ⇒ FactRef raises
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert result.brief.hero_ref is None  # offender defaulted, not fatal


def test_cdv2_bad_offer_priority_enum_defaults_to_medium_not_invalid(monkeypatch):
    """An out-of-enum offer_priority ("loud") defaults to "medium" — never raises,
    never flips to invalid."""
    brief_json = _base_brief_json()
    brief_json["offer_priority"] = "loud"  # not in {high, medium, low}
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert result.brief.offer_priority == "medium"  # defaulted


def test_cdv2_malformed_marketing_hook_defaults_to_none_not_invalid(monkeypatch):
    """A marketing_hook missing its required text_ref (MarketingHook would raise)
    defaults to None — never raises, never flips to invalid."""
    brief_json = _base_brief_json()
    brief_json["marketing_hook"] = {"prominence": "high"}  # no text_ref ⇒ raises
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert result.brief.marketing_hook is None  # defaulted


def test_cdv2_malformed_field_defaults_while_other_valid_fields_parse(monkeypatch):
    """A malformed offender (hero_ref both-set) defaults to None while OTHER valid
    new fields in the SAME response still parse — defensive defaulting is per-field,
    not all-or-nothing."""
    brief_json = _base_brief_json()
    brief_json["hero_ref"] = {"fact_id": "x", "raw_span": "y"}      # malformed → None
    brief_json["offer_priority"] = "high"                            # valid → kept
    brief_json["visual_direction"]["mood"] = "Festive"               # valid → kept
    brief_json["supporting_refs"] = [{"fact_id": "item:0:name"}]     # valid → kept
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    brief = result.brief
    assert brief is not None
    assert brief.hero_ref is None                                    # offender defaulted
    assert brief.offer_priority == "high"                           # other valid kept
    assert brief.visual_direction.mood == "Festive"                # other valid kept
    assert [r.fact_id for r in brief.supporting_refs] == ["item:0:name"]


def test_cdv2_malformed_supporting_ref_entry_is_skipped(monkeypatch):
    """For supporting_refs, an INDIVIDUAL malformed entry is skipped while the valid
    entries still parse (not the whole list dropped)."""
    brief_json = _base_brief_json()
    brief_json["supporting_refs"] = [
        {"fact_id": "item:0:name"},          # valid
        {"fact_id": "x", "raw_span": "y"},   # malformed (both set) → skipped
        {"fact_id": "item:1:name"},          # valid
    ]
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert [r.fact_id for r in result.brief.supporting_refs] == ["item:0:name", "item:1:name"]


# ── (4) schema CONSTRAINTS are enforced (FIX 2 — Codex MAJOR) ────────────────


def test_cdv2_supporting_refs_over_length_is_capped_to_40(monkeypatch):
    """50 VALID supporting_refs entries must be CAPPED to the schema max_length=40 —
    the pop-then-reapply path previously bypassed the FlyerBrief constraint. Status
    stays ok and nothing raises."""
    brief_json = _base_brief_json()
    brief_json["supporting_refs"] = [{"fact_id": f"item:{i}:name"} for i in range(50)]
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert len(result.brief.supporting_refs) <= 40
    assert len(result.brief.supporting_refs) == 40  # capped, not dropped to []


def test_cdv2_mood_over_length_is_constrained_to_120(monkeypatch):
    """A mood longer than the schema max_length=120 must be enforced (capped or
    dropped) so the over-length malformed value is never accepted. Status stays ok."""
    brief_json = _base_brief_json()
    brief_json["visual_direction"]["mood"] = "warm " * 60  # 300 chars, all benign
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert len(result.brief.visual_direction.mood) <= 120


# ── (5) campaign_narrative (Slice B, Task B0.2) — proposed, defensive ────────


def test_cdv2_campaign_narrative_included_is_parsed_onto_brief(monkeypatch):
    """The model may PROPOSE a top-level campaign_narrative (a short marketing
    message); it is carried onto the brief and the status stays ok."""
    brief_json = _base_brief_json()
    brief_json["campaign_narrative"] = "South Indian Favorites at One Price"
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert result.brief.campaign_narrative == "South Indian Favorites at One Price"


def test_cdv2_campaign_narrative_omitted_defaults_to_empty(monkeypatch):
    """A model response that OMITS campaign_narrative parses fine; the field sits
    at its default ""."""
    _arm(monkeypatch, _base_brief_json())

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert result.brief.campaign_narrative == ""


def test_cdv2_campaign_narrative_over_length_is_truncated_to_200(monkeypatch):
    """A campaign_narrative longer than the schema max_length=200 must be CAPPED
    (a bare model_validate would RAISE on the over-length str). Status stays ok and
    nothing raises."""
    brief_json = _base_brief_json()
    brief_json["campaign_narrative"] = "great deal " * 30  # 300 chars, all benign
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    assert result.brief is not None
    assert len(result.brief.campaign_narrative) <= 200
    assert len(result.brief.campaign_narrative) == 200  # capped, not dropped to ""


def test_cdv2_campaign_narrative_non_string_defaults_to_empty_not_invalid(monkeypatch):
    """A non-string campaign_narrative (e.g. a dict or a number) must NOT raise and
    must NOT flip the status to invalid — it defaults to "" and the brief parses → ok."""
    for bad_value in ({"text": "nope"}, 42):
        brief_json = _base_brief_json()
        brief_json["campaign_narrative"] = bad_value
        _arm(monkeypatch, brief_json)

        result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

        assert result.status == "ok", result.errors
        assert result.brief is not None
        assert result.brief.campaign_narrative == ""  # defaulted, not fatal


# ── (6) ALL CD v2 fields at TOP LEVEL together → none dropped (root-cause) ────
# Live B3 proved the brain returned only the OLD-schema fields and NOT the CD v2
# fields, so campaign_narrative / hero_ref / marketing_hook / offer_priority came
# back empty. This asserts the FULL CD v2 set, emitted at TOP LEVEL exactly as the
# parser (``_sanitize_cdv2_fields``) reads them, round-trips with NOTHING dropped.


def test_cdv2_all_top_level_fields_propose_with_nothing_dropped(monkeypatch):
    brief_json = _base_brief_json()
    brief_json["hero_ref"] = {"fact_id": "item:1:name"}
    brief_json["supporting_refs"] = [{"fact_id": "item:0:name"}]
    brief_json["marketing_hook"] = {
        "text_ref": {"fact_id": "pricing_structure"},
        "prominence": "high",
    }
    brief_json["offer_priority"] = "high"
    brief_json["campaign_narrative"] = "South Indian Favorites at One Price"
    brief_json["visual_direction"]["mood"] = "Warm Restaurant Promo"
    _arm(monkeypatch, brief_json)

    result = fcb.build_flyer_brief(_COMBO_REQUEST, _combo_facts(), None)

    assert result.status == "ok", result.errors
    brief = result.brief
    assert brief is not None

    # EVERY CD v2 field carried — none dropped, none empty.
    assert brief.hero_ref is not None and brief.hero_ref.fact_id == "item:1:name"
    assert [r.fact_id for r in brief.supporting_refs] == ["item:0:name"]
    assert brief.marketing_hook is not None
    assert brief.marketing_hook.text_ref.fact_id == "pricing_structure"
    assert brief.marketing_hook.prominence == "high"
    assert brief.offer_priority == "high"
    assert brief.campaign_narrative == "South Indian Favorites at One Price"
    assert brief.visual_direction.mood == "Warm Restaurant Promo"


# ── (7) GUARD: SKILL.md output schema declares the CD v2 fields where the ─────
# parser reads them — TOP-LEVEL hero_ref / supporting_refs / marketing_hook /
# offer_priority / campaign_narrative, and ``mood`` inside ``visual_direction``.
# This prevents the prompt↔parser mismatch from regressing: the model is only told
# to emit the OLD-schema fields unless the schema block also lists the CD v2 ones.


def _skill_schema_block() -> str:
    """The output-schema JSON block of SKILL.md — the fenced ```json contract
    that begins the "output exactly ONE FlyerBrief" section. Falls back to the
    full body if the fence cannot be isolated."""
    text = fcb.SKILL_MD_PATH.read_text(encoding="utf-8")
    start = text.find("```json")
    if start == -1:
        return text
    end = text.find("```", start + len("```json"))
    return text[start: end if end != -1 else len(text)]


def test_skill_md_declares_cdv2_top_level_fields_in_output_schema():
    block = _skill_schema_block()
    for field_name in (
        "hero_ref",
        "supporting_refs",
        "marketing_hook",
        "offer_priority",
        "campaign_narrative",
    ):
        assert f'"{field_name}"' in block, (
            f"SKILL.md output-schema block must declare top-level {field_name!r} "
            f"so the brain emits it where _sanitize_cdv2_fields reads it"
        )


# ── (8) GUARD (FIX B): the CD v2 EMPHASIS refs are documented as a locked ─────
# fact_id ONLY — NOT a raw_span. The resolver (``flyer_creative_resolver``) only
# consumes ``fact_id`` for hero_ref / supporting_refs / marketing_hook.text_ref; a
# raw_span on these is silently dropped (``_ref_fact_id`` returns "" for a raw_span).
# So the PROMPT must NOT offer raw_span for these three — both the SKILL.md emphasis-
# ref description AND the flat CD v2 note in ``_build_user_message`` must say fact_id
# only. (campaign_narrative + visual_direction.mood are unchanged — free text, not
# refs.) This guard string-scans the SKILL.md emphasis-ref description.


def test_skill_md_emphasis_refs_are_fact_id_only_not_raw_span():
    """The SKILL.md emphasis-ref guidance must document hero_ref / supporting_refs /
    marketing_hook.text_ref as a LOCKED fact_id only — and must NOT offer a raw_span
    for them (the resolver silently drops a raw_span on these). Scan each emphasis-ref
    sentence: it must mention ``fact_id`` and must NOT pair the ref with ``raw_span``."""
    text = fcb.SKILL_MD_PATH.read_text(encoding="utf-8")
    # HARD OUTPUT RULE #4 is the canonical emphasis-ref instruction; it must (a) name
    # all three refs, (b) say fact_id, and (c) NOT offer raw_span for them.
    rule4_start = text.find("The optional emphasis fields")
    assert rule4_start != -1, "SKILL.md must keep the HARD OUTPUT RULE #4 emphasis-ref rule"
    rule4 = text[rule4_start : rule4_start + 600]
    for ref in ("hero_ref", "supporting_refs", "marketing_hook"):
        assert ref in rule4, f"emphasis-ref rule must name {ref!r}"
    assert "fact_id" in rule4, "emphasis-ref rule must say the refs point by fact_id"
    # Must NOT OFFER raw_span as an option for these refs — the resolver silently
    # drops a raw_span on hero_ref/supporting_refs/marketing_hook. A negating mention
    # ("NOT a raw_span") is fine; an OFFER ("or a raw_span", "or raw_span") is the
    # exact regression we guard against.
    import re as _re

    offer_pat = _re.compile(r"or\s+(?:a\s+)?`?raw_span`?", _re.IGNORECASE)
    assert not offer_pat.search(rule4), (
        "SKILL.md emphasis-ref rule must NOT OFFER raw_span for hero_ref/"
        "supporting_refs/marketing_hook.text_ref — the resolver silently drops it; "
        "these are a LOCKED fact_id ONLY"
    )


def test_build_user_message_cdv2_note_says_fact_id_not_span_for_emphasis_refs():
    """The flat CD v2 note in ``_build_user_message`` must describe the emphasis refs
    as pointing by fact_id and must NOT offer a raw_span ('id/span') for them — the
    resolver only consumes fact_id for hero_ref/supporting_refs/marketing_hook."""
    import json as _json

    msg = fcb._build_user_message(
        _COMBO_REQUEST, _combo_facts(), None, None, None
    )
    payload = _json.loads(msg)
    note = payload.get("optional_creative_fields_note", "")
    assert "fact_id" in note, "CD v2 note must say the emphasis refs point by fact_id"
    assert "span" not in note.lower(), (
        "CD v2 note must NOT offer a raw_span/'id/span' for the emphasis refs — the "
        "resolver silently drops a raw_span on hero_ref/supporting_refs/marketing_hook"
    )


def test_skill_md_declares_campaign_narrative_required_not_optional():
    """NARRATIVE-RELIABILITY guard: SKILL.md must declare campaign_narrative as a
    REQUIRED brief field (the brain must always craft a short grounded message), and
    must NOT mark it OPTIONAL / omittable. A non-deterministic brain that omits the
    narrative produced a headline-less message-first A render in live validation; the
    SKILL requiredness is the upstream half of the fix.

    String-scan the SKILL.md emphasis-ref rule (HARD OUTPUT RULE #4, which carries the
    campaign_narrative description): it must (a) say campaign_narrative is REQUIRED and
    (b) NOT pair campaign_narrative with optional/omit/"unsure" language."""
    text = fcb.SKILL_MD_PATH.read_text(encoding="utf-8")
    rule4_start = text.find("The optional emphasis fields")
    assert rule4_start != -1, "SKILL.md must keep the HARD OUTPUT RULE #4 emphasis-ref rule"
    rule4 = text[rule4_start: rule4_start + 700]

    cn_idx = rule4.find("campaign_narrative")
    assert cn_idx != -1, "SKILL.md rule #4 must describe campaign_narrative"
    # The campaign_narrative sentence must declare it REQUIRED.
    assert "REQUIRED" in rule4 or "required" in rule4, (
        "SKILL.md must declare campaign_narrative REQUIRED (the brain must always craft a "
        "short grounded message) — a missing narrative renders the A poster headline-less"
    )
    # The campaign_narrative description must NOT carry omittable wording. The old
    # 'OPTIONAL ... omit any you are unsure of' applied to campaign_narrative is the
    # exact regression we guard against.
    cn_window = rule4[max(0, cn_idx - 80): cn_idx + 360]
    for forbidden in ("omit if unsure", "omit any you are unsure", "OPTIONAL", "optional"):
        assert forbidden not in cn_window, (
            f"SKILL.md must NOT mark campaign_narrative {forbidden!r}-style omittable; "
            f"it is REQUIRED so the message-first headline is never empty"
        )


def test_skill_md_declares_mood_inside_visual_direction():
    """``mood`` must appear inside the ``visual_direction`` object of the schema
    block (next to ``theme_family``), since the parser reads
    ``visual_direction.mood``."""
    block = _skill_schema_block()
    vd_start = block.find('"visual_direction"')
    assert vd_start != -1, "schema block must contain visual_direction"
    # The visual_direction object ends at the next top-level key after it; the
    # mood key must appear within the object (after theme_family, before the next
    # closing of the visual_direction block). Locate the object's closing brace.
    brace_open = block.find("{", vd_start)
    assert brace_open != -1
    depth = 0
    vd_end = len(block)
    for i in range(brace_open, len(block)):
        if block[i] == "{":
            depth += 1
        elif block[i] == "}":
            depth -= 1
            if depth == 0:
                vd_end = i
                break
    vd_obj = block[brace_open:vd_end]
    assert '"mood"' in vd_obj, (
        "SKILL.md schema must declare 'mood' inside visual_direction (parser reads "
        "visual_direction.mood)"
    )
