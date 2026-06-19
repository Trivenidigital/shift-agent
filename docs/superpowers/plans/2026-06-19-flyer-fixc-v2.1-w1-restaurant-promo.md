# Flyer Fix C v2.1 — W1 Restaurant-Promo Hero Background — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]` tracking.

**Goal:** Rewrite ONLY the scoped W1 background directive so the model produces a Restaurant-Promo single-hero background (max appetite appeal), while the deterministic overlay/scrims/layout/safety stay unchanged.

**Architecture:** One edit in `render.py` `_poster_layout_requirements`: split the background-only `reserve` so the scoped (`_premium_overlay_enabled`) path gets the Restaurant-Promo single-hero directive and the non-scoped path keeps today's "reserve calm zones" wording verbatim → flag-off byte-identical.

**Tech Stack:** Python 3, pytest; live mockups via OpenRouter (gemini) + the unchanged overlay. File: `src/agents/flyer/render.py`; tests: `tests/test_flyer_renderer.py`. Authoritative source = `origin/main` (`ea6af2a`).

**Design doc:** `docs/superpowers/specs/2026-06-19-flyer-fixc-v2.1-w1-restaurant-promo-design.md`
**Drift-check tag:** `extends-Hermes`.
**Test command:** `PYTHONPATH="src;src/platform" python -m pytest <path> -v`

**Invariant (preserve):** overlay (`premium_overlay.py`), `compose_scrims`, layout, typography, and all safety logic UNCHANGED. Background stays textless. Scoped to `FLYER_PREMIUM_OVERLAY`; flag-off byte-identical. No schema/flag change.

---

## Task 1: W1 prompt rewrite + tests (items 1, 2, 3)

**Files:** Modify `src/agents/flyer/render.py` (`_poster_layout_requirements`, the `reserve` block ~lines 1208-1226); Test `tests/test_flyer_renderer.py`.

- [ ] **Step 1: Write the failing tests** (append to tests/test_flyer_renderer.py; reuse `_f0174_integrated_project`)
```python
def test_w1_scoped_prompt_is_restaurant_promo_single_hero(monkeypatch):
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.setenv("FLYER_PREMIUM_OVERLAY", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY_ALLOWLIST", raising=False)
    p = _f0174_integrated_project()
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    # Restaurant-Promo single-hero directives present
    assert "one single" in low and "hero dish" in low
    assert "cinematic" in low and ("warm" in low or "golden" in low)
    assert "dominates the frame" in low
    assert "spread of many separate dishes" in low      # explicit forbid
    assert "vignette" in low                             # legibility via vignette, not empty bands
    assert "no people" in low
    # text guarantee retained
    assert ("do not draw any text" in low) or ("do not render" in low)
    # old banding wording gone from the SCOPED prompt
    assert "close-up of the dish(es)" not in low
    assert "reserve visually calm" not in low

def test_w1_flagoff_prompt_byte_identical(monkeypatch):
    # Flag off: background-only prompt must be unchanged (keeps the original
    # "reserve visually calm zones" wording; no Restaurant-Promo directive).
    from agents.flyer import render as r
    monkeypatch.setenv("FLYER_ALLOW_INTEGRATED_POSTER", "1")
    monkeypatch.delenv("FLYER_PREMIUM_OVERLAY", raising=False)
    p = _f0174_integrated_project()
    tok = r._FORCE_BACKGROUND_ONLY.set(True)
    try:
        prompt = r.build_image_generation_prompt(p, concept_id="C1", output_format="concept_preview", size=(1080, 1350), force_background_only=True)
    finally:
        r._FORCE_BACKGROUND_ONLY.reset(tok)
    low = prompt.lower()
    assert "reserve visually calm" in low               # original wording retained
    assert "restaurant-promo" not in low and "hero dish" not in low and "vignette" not in low
    assert ("do not draw any text" in low) or ("do not render" in low)
```

- [ ] **Step 2: Run — expect FAIL** (Restaurant-Promo wording missing; scoped still has old wording).
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -k "w1_scoped or w1_flagoff" -v`

- [ ] **Step 3: Implement** — replace the `reserve` block in `_poster_layout_requirements` (keep the base "decorative BACKGROUND only" line; scoped branch = Restaurant-Promo; non-scoped `else` = the original "reserve calm zones" line verbatim):
```python
        reserve = (
            "- Produce a decorative BACKGROUND image only. Do NOT draw any text, headlines, "
            "menu/item cards, price tags, schedule, location, or contact — the exact text is "
            "composited afterwards into overlay panels.\n"
        )
        if _premium_overlay_enabled(project):
            # Fix C v2.1 (SCOPED): Restaurant-Promo single-hero background. Replaces the
            # reserved-zone banding (which yielded a flat multi-item spread → template) with a
            # cinematic single-hero composition; the deterministic overlay's own gradient
            # scrims provide text legibility (validated). Scoped ⇒ flag-off byte-identical.
            reserve += (
                "- Compose a RESTAURANT-PROMO hero food photograph: ONE single mouth-watering hero dish "
                "(the featured food) as the bold subject that DOMINATES the frame, with warm golden cinematic "
                "restaurant lighting, gentle steam and visible texture where appropriate, rich shallow depth of "
                "field, on a rustic dark wood or slate surface with softly-lit restaurant ambiance behind. "
                "Appetizing and vibrant, like a premium restaurant advertisement.\n"
                "- Keep it cinematic and atmospheric with naturally darker, softer top and bottom edges (a gentle "
                "vignette) so the composited title and menu stay legible — but the hero dish still fills the frame; "
                "do NOT leave empty flat bands or blank panels.\n"
                "- No people, no faces, no hands, no diners, no family scene, no buffet, and no spread of many "
                "separate dishes — ONE hero dish is the subject.\n"
            )
        else:
            reserve += (
                "- Reserve visually calm, low-detail zones in the upper-left and along the bottom for "
                "those overlay panels; keep the rich hero imagery in the center and right.\n"
            )
```
(Leave the subsequent `if _style_only_reference_requested(project):` block + everything else in the function unchanged.)

- [ ] **Step 4: Run — expect PASS** (both tests). Then full renderer file (no regression):
`PYTHONPATH="src;src/platform" python -m pytest tests/test_flyer_renderer.py -q`

- [ ] **Step 5: Commit** `feat(flyer): v2.1 W1 Restaurant-Promo single-hero background directive (scoped)`.

---

## Task 2: Pre-deploy mockup validation — the multi-item / combo question (item 5)

**Files:** throwaway validation script (not committed to src), like the W1 isolation experiment. Uses the UNCHANGED overlay + the NEW prompt path.

This answers the operator's key question **before** relying on live: *does "single hero dish" still produce premium outcomes for multi-item menus and combo offers?*

- [ ] **Step 1: Build a validation script** (run on the box: OpenRouter key + fonts + the v2.1 `render.py`/overlay). For each of the three briefs, build the project facts, generate the background by calling `build_image_generation_prompt(..., force_background_only=True)` (so it uses the NEW v2.1 directive) → OpenRouter gemini → then `render_premium_overlay` (unchanged) over it:
  - **Weekend Specials** (6 items, Any item $7.99) — regression of the F0176 case.
  - **Veg / Non-Veg Combo** (e.g., a veg combo + a non-veg combo, two prices) — the critical case: does ONE hero dish read right when the offer spans two categories?
  - **Festival Dessert** (e.g., Diwali sweets — gulab jamun / kaju katli, a few items) — does single-hero work for desserts?
- [ ] **Step 2: Render all three + pull the images.** (Spend: ~3 gemini gens; keep a hard cap of ~$1.)
- [ ] **Step 3: Judge each against the bar:** would an owner post it? hero reads as a hero? combo still makes sense with one hero dish + the full menu list? dessert appetizing? Record verdicts. If the combo case looks wrong (one hero dish misrepresents a two-category combo), flag it as a finding (candidate: a combo may need a "two-hero / combo-platter" hero variant — but DO NOT implement in this pass; report for a follow-up decision).
- [ ] **Step 4: No commit** (throwaway). Capture findings for the handoff.

---

## Task 3: Full suite + Codex review (items 2, 4)

- [ ] **Step 1: Full build suite** `PYTHONPATH="src;src/platform" python -m pytest tests/ -q` → 0 failed.
- [ ] **Step 2: Codex review** the branch diff. Focus: (a) scoped path = Restaurant-Promo single-hero; (b) **flag-off background prompt byte-identical** (non-scoped keeps the exact original "reserve calm zones" wording); (c) overlay/scrims/layout/safety untouched; (d) background still textless; no new flag/schema.
- [ ] **Step 3: Fix any BLOCKER/MAJOR; re-review to CLEAN.**

---

## Self-Review (writing-plans)
**Spec coverage:** prompt rewrite→T1; flag-off byte-identical→T1 test + T3; prompt-content tests→T1; Codex→T3; validation cases (Weekend Specials/Combo/Dessert)→T2 (pre-deploy) + live post-deploy. ✓
**Placeholder scan:** the rewrite + tests are concrete; the validation script mirrors the committed W1-isolation generator. ✓
**Type consistency:** `_premium_overlay_enabled`, `build_image_generation_prompt`, `render_premium_overlay`, `_FORCE_BACKGROUND_ONLY` consistent. ✓

## Out of scope (deferred)
Overlay/scrims/typography/layout/safety (unchanged); Editorial-Hero alternate; model-chooses-mood; combo two-hero variant (only report if Task 2 shows the single-hero combo is weak); the deferred flat-degrade/import-order finding; combo near-duplicate quirk; Slice 2 cleanup.

## Post-build (operator-gated)
PR → CI → Codex → merge → deploy (scoped flag already on for +17329837841) → live re-validation of the three briefs against the Restaurant-Promo bar. Rollback = `FLYER_PREMIUM_OVERLAY` off → flat.
