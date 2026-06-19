# Flyer Fix C v2.1 — W1 Restaurant-Promo Hero Background — Design

**Date:** 2026-06-19
**Status:** Design for review (no implementation until the plan is approved).
**Drift-check tag:** `extends-Hermes` — rewrites one prompt directive in the existing background-only render path; no new storage, no schema, no new flag, overlay/scrims/layout unchanged.

---

## 1. Problem (proven by controlled validation)

The live v2 flyer reads as a **template**, not the approved mockup A. A controlled experiment — **the v2 overlay held UNCHANGED**, applied to four backgrounds — isolated the cause:

| Background | Result |
|---|---|
| Current live (`w1-current-live-template.png`) | bright multi-item **spread** → template |
| Editorial hero | premium ≈ mockup A |
| Magazine shoot | premium |
| **Restaurant promo** (`w1-restaurant-promo-approved.png`) | **premium, highest appetite appeal** |

**Conclusion: W1 (background generation) is the dominant cause.** The deterministic overlay + scrims + layout are *not* the bottleneck — they produce premium output over any proper single-hero background. The current W1 prompt yields a *bright multi-item menu spread with dark reserved-zone banding* instead of a *single dramatic hero dish filling the frame*.

## 2. Goal & scope

**Goal:** rewrite the W1 background directive so the model produces a **Restaurant-Promo single-hero** background — maximum appetite appeal while preserving the premium deterministic overlay.

**In scope (only this):** the `_premium_overlay_enabled`-gated FOOD-HERO directive in `render.py` `_poster_layout_requirements` (currently render.py ~1215-1224).

**Out of scope (deferred / unchanged):** the deterministic overlay (`premium_overlay.py`), `compose_scrims`, layout/composition (validated as not the bottleneck); Editorial-Hero as a future alternate style; the model choosing mood per brief; combo near-duplicate quirk; Slice 2 cleanup. **No new flag** — stays behind `FLYER_PREMIUM_OVERLAY` (scoped to `+17329837841`); flag-off byte-identical.

## 3. The change (W1 directive: before → after)

**Current (produces a flat multi-item spread + reserved-zone banding):**
```
- Produce a decorative BACKGROUND image only. Do NOT draw any text … overlay panels.
- Reserve visually calm, low-detail zones in the upper-left and along the bottom for those overlay panels; keep the rich hero imagery in the center and right.
- Compose a FOOD-HERO background: one large, appetizing close-up of the dish(es) as the clear subject … No people, no faces, no hands, no diners, no family scene, no buffet, no generic restaurant scene — the food itself is the hero.
```

**Proposed (Restaurant-Promo single hero — the validated `restaurant_promo` wording, generalized):**
```
- Produce a decorative BACKGROUND image only. Do NOT draw any text, headlines, menu/item cards, price tags, schedule, location, or contact — the exact text is composited afterwards.
- Compose a RESTAURANT-PROMO hero food photograph: ONE single mouth-watering hero dish (the featured food) as the bold subject that DOMINATES the frame, with warm golden cinematic restaurant lighting, gentle steam and visible texture where appropriate, rich shallow depth of field, on a rustic dark wood / slate surface with softly-lit restaurant ambiance behind. Appetizing and vibrant, like a premium restaurant advertisement.
- Keep the composition cinematic and atmospheric, with naturally darker, softer top and bottom edges (a gentle vignette) so the composited title and menu remain legible — but the hero dish still fills the frame; do NOT leave empty flat bands or blank panels.
- No people, no faces, no hands, no diners, no family scene, no buffet, no generic spread of many separate dishes — ONE hero dish is the subject.
```

Key deltas: **(a) "ONE single hero dish" not "the dish(es)"** (kills the multi-item spread); **(b) warm cinematic + steam/texture** (appetite appeal); **(c) food DOMINATES the frame**; **(d) replace "reserve empty calm zones" with "cinematic darker/softer edges (vignette)"** (kills the flat-banding; the overlay's own scrims provide legibility — validated); **(e) explicitly forbid "a spread of many separate dishes."**

## 4. Why this is safe / preserves guarantees
- The overlay still owns ALL exact text (deterministic) — unchanged. The background remains textless ("do NOT draw any text" retained).
- Scoped to `FLYER_PREMIUM_OVERLAY` (`_premium_overlay_enabled`) — flag-off background prompt byte-identical (the directive only appends when the premium overlay is enabled for the project).
- Fail-closed / QA referee unchanged. A weak/odd background still gets the deterministic overlay + the referee gate; worst case degrades to flat → manual (never unsafe).
- No schema/state/flag change.

## 5. Residual risk
- **Per-brief model variance:** the model may occasionally still produce a non-ideal hero. The forceful "ONE single hero dish … do NOT show a spread of many dishes" wording reduces this; the overlay + fail-closed remain the backstop. (This is appetite-appeal variance, not a safety risk.)
- **Multi-item briefs (combo / 16-item):** the background shows ONE hero dish while the deterministic menu lists all items — this is the intended marketing pattern (hero photo + full menu list), validated visually. Watch the combo brief specifically.

## 6. Testing
- **Prompt content (unit):** under `FLYER_PREMIUM_OVERLAY` enabled + background-only path, the prompt contains the Restaurant-Promo directives (ONE single hero dish / warm cinematic / food dominates / vignette-not-bands / no people / no multi-dish spread) and does NOT contain the old "close-up of the dish(es)" / "reserve … calm zones" banding wording. Still contains "do NOT draw any text".
- **Flag-off byte-identical:** with `FLYER_PREMIUM_OVERLAY` unset, the background prompt is unchanged from origin/main.
- **Live validation (operator-gated, post-deploy):** re-send F0176 (+ combo + dessert) scoped to `+17329837841`; judge the live background against `w1-restaurant-promo-approved.png` and the "would an owner post this?" bar; confirm leak 0.

## 7. Visual anchors (committed)
`docs/superpowers/fixc-v2.1-reference/`: `w1-restaurant-promo-approved.png` (target — unchanged overlay over a restaurant-promo bg), `w1-editorial-hero-alt.png` (future alternate), `w1-current-live-template.png` (the template being replaced).
