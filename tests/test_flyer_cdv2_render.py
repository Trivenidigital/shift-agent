"""Tests for the FLYER_CREATIVE_DIRECTOR_V2 scoped gate (Slice B Task B2.1).

TDD: these assert on `_creative_director_v2_enabled`, which mirrors the sibling
gates (`_deterministic_first_enabled`, `_premium_overlay_enabled`) exactly:
flag == "1" AND (allowlist empty => global, else normalized customer_phone in
allowlist). Flag-off => False even for an allowlisted number => no behavior change.
"""
import sys
from datetime import datetime, timezone

import agents.flyer.render as render_module
from agents.flyer.render import _creative_director_v2_enabled
from schemas import FlyerProject


def _project(phone: str = "+17329837841") -> FlyerProject:
    return FlyerProject(
        project_id="F0250",
        status="intake_started",
        customer_phone=phone,
        created_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        original_message_id="m",
        raw_request="Weekend Specials any item $7.99",
    )


def test_env_unset_returns_false_even_for_allowlisted(monkeypatch):
    monkeypatch.delenv("FLYER_CREATIVE_DIRECTOR_V2", raising=False)
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert _creative_director_v2_enabled(_project("+17329837841")) is False


def test_env_on_allowlisted_number_returns_true(monkeypatch):
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert _creative_director_v2_enabled(_project("+17329837841")) is True


def test_env_on_other_number_returns_false(monkeypatch):
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    assert _creative_director_v2_enabled(_project("+19998887777")) is False


def test_env_on_empty_allowlist_is_global(monkeypatch):
    """Flag "1" + no allowlist => global ON (mirrors sibling gates)."""
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    assert _creative_director_v2_enabled(_project("+19998887777")) is True


def test_env_const_name(monkeypatch):
    assert render_module.CREATIVE_DIRECTOR_V2_ENV == "FLYER_CREATIVE_DIRECTOR_V2"


# ── Slice B Task B2.3 — build+resolve the CD v2 brief in _render_model ────────
#
# These exercise _render_model's NEW upstream block: when the V2 gate is ON it
# PROPOSES a brief (Hermes proposes the creative fields), RESOLVES it over the
# project's EXISTING locked_facts, and stores dataclasses.asdict(resolved) on
# project.creative_direction. Flag-OFF the block is skipped entirely (carrier None,
# propose NEVER called, NO locked_facts mutation). No network / no PIL: the actual
# image gen + overlay are patched to no-ops.
from schemas import FlyerLockedFact  # noqa: E402

# IMPORTANT (dual-module identity): both a flat (``flyer_context_builder``) and a
# package (``agents.flyer.flyer_context_builder``) copy of these modules can be
# loaded depending on test ordering / sys.path, giving two DISTINCT FlyerBrief
# classes. To stay identity-safe, reference the brief classes through the SAME
# module object that ``render`` actually resolved its ``propose_creative_brief_v2``
# from — never a fixed import path — so ``isinstance`` checks line up with the
# objects production code produces.
fcb = sys.modules[render_module.propose_creative_brief_v2.__module__]  # noqa: E402
FactRef = fcb.FactRef  # noqa: E402
FlyerBrief = fcb.FlyerBrief  # noqa: E402
VisualDirection = fcb.VisualDirection  # noqa: E402


def _facts() -> list[FlyerLockedFact]:
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Masala Dosa", source="customer_text", required=True),
        FlyerLockedFact(fact_id="item:1:name", label="Item",
                        value="Idli Sambar", source="customer_text", required=True),
        FlyerLockedFact(fact_id="pricing_structure", label="Price",
                        value="any item $7.99", source="customer_text", required=True),
    ]


def _project_with_facts(phone: str = "+17329837841") -> FlyerProject:
    p = _project(phone)
    p.locked_facts = _facts()
    return p


def _proposed_brief() -> FlyerBrief:
    """A model-proposed brief whose hero is item:1 (Idli Sambar) + a campaign
    narrative + high offer_priority — resolved deterministically against the facts."""
    return FlyerBrief(
        request_intent="menu",
        visual_direction=VisualDirection(theme_family="Warm South Indian Promo"),
        hero_ref=FactRef(fact_id="item:1:name"),
        marketing_hook=None,
        offer_priority="high",
        campaign_narrative="South Indian Favorites at One Price",
    )


def _patch_render_io(monkeypatch):
    """Stub the actual image gen + overlay so _render_model touches no network/PIL.
    _openrouter_image_bytes returns bytes; _write_generated_image + the overlays are
    no-ops so the body runs to completion regardless of the render branch taken."""
    monkeypatch.setattr(render_module, "_openrouter_image_bytes",
                        lambda *a, **k: b"\x89PNG\r\n", raising=True)
    monkeypatch.setattr(render_module, "_write_generated_image",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(render_module, "_apply_critical_text_overlay",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(render_module, "apply_exact_identity_overlay",
                        lambda *a, **k: None, raising=True)
    # Force the deterministic-renderer early-return path so we never reach the
    # network branches (and patch _render too, for completeness).
    monkeypatch.setattr(render_module, "_render", lambda *a, **k: None, raising=True)


def test_render_flag_on_populates_carrier_from_resolved(monkeypatch, tmp_path):
    """Flag ON + scoped phone: after _render_model, project.creative_direction is a
    dict carrying hero_name / campaign_narrative / offer_priority from the resolved
    direction (proposed brief routed through the deterministic resolver)."""
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    monkeypatch.setattr(render_module, "propose_creative_brief_v2",
                        lambda *a, **k: _proposed_brief(), raising=True)
    _patch_render_io(monkeypatch)

    project = _project_with_facts("+17329837841")
    render_module._render_model(
        project, tmp_path / "out.png", concept_id="C1",
        output_format="concept_preview", size=(1080, 1350),
        model="deterministic-renderer", quality="low",
    )

    cd = project.creative_direction
    assert isinstance(cd, dict)
    assert cd["hero_name"] == "Idli Sambar"  # resolved from hero_ref item:1:name
    assert cd["campaign_narrative"] == "South Indian Favorites at One Price"
    assert cd["offer_priority"] == "high"


def test_render_flag_off_skips_propose_and_leaves_carrier_none(monkeypatch, tmp_path):
    """Flag OFF: creative_direction stays None, propose is NEVER called, and
    locked_facts is unchanged (no materialize_spans mutation)."""
    monkeypatch.delenv("FLYER_CREATIVE_DIRECTOR_V2", raising=False)
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    calls: list[int] = []

    def _spy(*_a, **_k):
        calls.append(1)
        return _proposed_brief()

    monkeypatch.setattr(render_module, "propose_creative_brief_v2", _spy, raising=True)
    _patch_render_io(monkeypatch)

    project = _project_with_facts("+17329837841")
    facts_before = [f.model_dump() for f in project.locked_facts]
    render_module._render_model(
        project, tmp_path / "out.png", concept_id="C1",
        output_format="concept_preview", size=(1080, 1350),
        model="deterministic-renderer", quality="low",
    )

    assert project.creative_direction is None
    assert calls == []  # propose NEVER invoked under flag-off
    assert [f.model_dump() for f in project.locked_facts] == facts_before  # no mutation


def test_render_flag_on_does_not_mutate_locked_facts(monkeypatch, tmp_path):
    """Flag ON: the V2 propose path NEVER mutates project.locked_facts (no
    materialize_spans on the V2 path). The carrier is populated, facts untouched."""
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    monkeypatch.setattr(render_module, "propose_creative_brief_v2",
                        lambda *a, **k: _proposed_brief(), raising=True)
    _patch_render_io(monkeypatch)

    project = _project_with_facts("+17329837841")
    facts_before = [f.model_dump() for f in project.locked_facts]
    render_module._render_model(
        project, tmp_path / "out.png", concept_id="C1",
        output_format="concept_preview", size=(1080, 1350),
        model="deterministic-renderer", quality="low",
    )

    assert project.creative_direction is not None
    assert [f.model_dump() for f in project.locked_facts] == facts_before  # untouched


def test_render_flag_on_propose_none_falls_back_to_empty_brief(monkeypatch, tmp_path):
    """Flag ON but propose returns None (gateway fail): the carrier is STILL populated
    from the EMPTY-brief deterministic defaults (hero_name = first item), render NOT
    blocked."""
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")
    monkeypatch.setattr(render_module, "propose_creative_brief_v2",
                        lambda *a, **k: None, raising=True)
    _patch_render_io(monkeypatch)

    project = _project_with_facts("+17329837841")
    render_module._render_model(
        project, tmp_path / "out.png", concept_id="C1",
        output_format="concept_preview", size=(1080, 1350),
        model="deterministic-renderer", quality="low",
    )

    cd = project.creative_direction
    assert isinstance(cd, dict)
    # empty brief → resolver falls back to the first item name as hero
    assert cd["hero_name"] == "Masala Dosa"


def test_render_flag_on_propose_raises_leaves_carrier_none(monkeypatch, tmp_path):
    """A truly unexpected error in propose/resolve must NOT block the render: the
    block is wrapped, leaving creative_direction None, and _render_model completes."""
    monkeypatch.setenv("FLYER_CREATIVE_DIRECTOR_V2", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", "+17329837841")

    def _boom(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(render_module, "propose_creative_brief_v2", _boom, raising=True)
    _patch_render_io(monkeypatch)

    project = _project_with_facts("+17329837841")
    render_module._render_model(
        project, tmp_path / "out.png", concept_id="C1",
        output_format="concept_preview", size=(1080, 1350),
        model="deterministic-renderer", quality="low",
    )
    assert project.creative_direction is None  # never blocked


# ── propose_creative_brief_v2 unit tests (no network) ────────────────────────


def _v2_facts() -> list[FlyerLockedFact]:
    return [
        FlyerLockedFact(fact_id="business_name", label="Business",
                        value="Lakshmi's Kitchen", source="customer_profile", required=True),
        FlyerLockedFact(fact_id="item:0:name", label="Item",
                        value="Masala Dosa", source="customer_text", required=True),
    ]


def _v2_brief_json() -> dict:
    return {
        "request_intent": "menu",
        "visual_direction": {"theme_family": "Warm South Indian Promo"},
        "hero_ref": {"fact_id": "item:0:name"},
        "campaign_narrative": "South Indian Favorites at One Price",
    }


def test_propose_v2_fake_gateway_returns_parsed_brief():
    """A fake gateway returning a brief JSON with campaign_narrative + hero_ref →
    a parsed FlyerBrief (injected gateway, no network)."""
    brief = fcb.propose_creative_brief_v2(
        "Weekend specials", _v2_facts(), None,
        gateway=lambda _s, _u: _v2_brief_json(),
    )
    assert isinstance(brief, FlyerBrief)
    assert brief.campaign_narrative == "South Indian Favorites at One Price"
    assert brief.hero_ref is not None
    assert brief.hero_ref.fact_id == "item:0:name"


def test_propose_v2_does_not_mutate_locked_facts():
    """propose_creative_brief_v2 NEVER calls materialize_spans → the passed
    locked_facts list length is unchanged."""
    facts = _v2_facts()
    before = len(facts)
    fcb.propose_creative_brief_v2(
        "Weekend specials", facts, None,
        gateway=lambda _s, _u: _v2_brief_json(),
    )
    assert len(facts) == before  # no append / no mutation


def test_propose_v2_gateway_failure_returns_none_never_raises():
    """A gateway that returns None (failure) → None, never raises."""
    brief = fcb.propose_creative_brief_v2(
        "Weekend specials", _v2_facts(), None,
        gateway=lambda _s, _u: None,
    )
    assert brief is None


def test_propose_v2_unparseable_response_returns_none(monkeypatch):
    """An off-schema gateway body (missing required request_intent) → None (the
    single model_validate fails), never raises."""
    brief = fcb.propose_creative_brief_v2(
        "Weekend specials", _v2_facts(), None,
        gateway=lambda _s, _u: {"not": "a brief"},
    )
    assert brief is None


# ── Slice B Task B2.4 — wire hero + theme + mood into the textless-bg prompt ──
#
# When the V2 carrier (project.creative_direction) holds a non-empty hero_name /
# theme_family / mood, the PREMIUM textless-background directive in
# _poster_layout_requirements must NAME the hero dish and reflect the theme/mood —
# while KEEPING the no-text / no-people / vignette clauses verbatim. When the
# carrier is None (flag off) or its fields are empty, the directive is
# BYTE-IDENTICAL to today's fixed premium string (regression guard).

# The fixed premium HERO directive (render.py ~1322-1336) as shipped today. This
# literal is the flag-off / empty-carrier expected output and is the byte-for-byte
# regression baseline. It MUST stay in sync with the production string.
_FIXED_PREMIUM_HERO_DIRECTIVE = (
    "- Compose a wordless HERO food photograph for the background: ONE single mouth-watering hero "
    "dish (the featured food) as the bold subject that DOMINATES the frame, with warm golden "
    "cinematic lighting, gentle steam and visible texture where appropriate, rich shallow depth of "
    "field, on a rustic dark wood or slate surface with softly-lit ambiance behind. Appetizing, "
    "vibrant, and atmospheric.\n"
    "- This is a PHOTOGRAPH ONLY: absolutely NO text, letters, words, numbers, captions, signage, "
    "menu boards, price tags, watermarks, or logos anywhere in the image — do not imitate an "
    "advertisement layout; the exact text is composited afterwards into overlay panels.\n"
    "- Cinematic and atmospheric, with naturally darker, softer top and bottom edges (a gentle "
    "vignette) so the composited title and menu stay legible — but the hero dish still fills the frame; "
    "do NOT leave empty flat bands or blank panels.\n"
    "- No people, no faces, no hands, no diners, no family scene, no buffet, and no spread of many "
    "separate dishes — ONE hero dish is the subject.\n"
)


def _premium_food_project(phone: str = "+17329837841") -> FlyerProject:
    """A minimal food project that reaches the PREMIUM background branch.

    No FLYER_ALLOW_INTEGRATED_POSTER ⇒ not integrated-eligible; plus we pass
    force_background_only=True at the call site for robustness ⇒ the premium
    background branch is taken (with FLYER_PREMIUM_OVERLAY=1)."""
    p = _project(phone)
    p.raw_request = "Weekend dosa special $7.99 at our South Indian restaurant"
    return p


def test_bg_prompt_flag_off_carrier_none_is_byte_identical(monkeypatch):
    """Carrier None (flag off) ⇒ the premium directive is byte-identical to today's
    fixed string (the fixed HERO directive appears verbatim, hero name NOT injected)."""
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    project = _premium_food_project()
    assert project.creative_direction is None
    out = render_module._poster_layout_requirements(project, force_background_only=True)
    assert _FIXED_PREMIUM_HERO_DIRECTIVE in out


def test_bg_prompt_empty_carrier_fields_is_byte_identical(monkeypatch):
    """Carrier present but hero/theme/mood empty ⇒ no fragments injected; the fixed
    HERO directive appears verbatim (byte-identical to flag-off)."""
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    project = _premium_food_project()
    project.creative_direction = {"hero_name": "", "theme_family": "", "mood": ""}
    out = render_module._poster_layout_requirements(project, force_background_only=True)
    assert _FIXED_PREMIUM_HERO_DIRECTIVE in out


def test_bg_prompt_empty_carrier_matches_flag_off_exactly(monkeypatch):
    """Stronger guard: empty-carrier output == flag-off output, byte-for-byte."""
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    p_off = _premium_food_project()
    out_off = render_module._poster_layout_requirements(p_off, force_background_only=True)
    p_empty = _premium_food_project()
    p_empty.creative_direction = {"hero_name": "", "theme_family": "", "mood": ""}
    out_empty = render_module._poster_layout_requirements(p_empty, force_background_only=True)
    assert out_empty == out_off


def test_bg_prompt_populated_carrier_names_hero_theme_mood(monkeypatch):
    """Carrier with non-empty hero/theme/mood ⇒ the directive NAMES the hero dish
    AND reflects the theme AND the mood — while STILL being a textless directive
    (the existing 'absolutely NO text' clause remains)."""
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    project = _premium_food_project()
    project.creative_direction = {
        "hero_name": "Dosa",
        "theme_family": "South Indian Weekend Feast",
        "mood": "Warm Restaurant Promo",
    }
    out = render_module._poster_layout_requirements(project, force_background_only=True)
    assert "Dosa" in out
    assert "South Indian Weekend Feast" in out
    assert "Warm Restaurant Promo" in out
    # Still textless: the no-text clause must remain.
    assert "absolutely NO text" in out
    # Still no-people: that clause must remain too.
    assert "no faces" in out


def test_bg_prompt_populated_carrier_differs_from_fixed(monkeypatch):
    """Sanity: a populated carrier actually CHANGES the output (otherwise the
    byte-identical guards would be vacuous)."""
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    p_off = _premium_food_project()
    out_off = render_module._poster_layout_requirements(p_off, force_background_only=True)
    p_on = _premium_food_project()
    p_on.creative_direction = {
        "hero_name": "Dosa",
        "theme_family": "South Indian Weekend Feast",
        "mood": "Warm Restaurant Promo",
    }
    out_on = render_module._poster_layout_requirements(p_on, force_background_only=True)
    assert out_on != out_off
