# Flyer Fix C v2 — Editorial Luxury premium template — Design

**Date:** 2026-06-19
**Status:** Design for review (operator approved direction A, 2026-06-18). No implementation until plan approved.
**Drift-check tag:** `extends-Hermes` — upgrades the existing deterministic premium overlay + the background-only image prompt; no new storage, no schema change, no Hermes-convention change. Same flag/QA/fail-closed contract as Fix C v1.

**Visual anchors (committed):**
- Target: `docs/superpowers/fixc-v2-reference/fixc-v2-A-editorial-luxury-approved.png` (approved mockup A) + `…-A-hero-bg.png` (its food-hero background).
- Baseline being replaced: `…/f0175-current-premium-baseline.png` (what F0175 actually shipped).
- Mockup generator (throwaway, for reference): `…/fixc-v2-mockup-generator.py`.

---

## 1. Problem (grounded; the architecture is settled)

Fix C v1 + deterministic-recovery routing are done and validated: safety holds, recovery works, **exact text is guaranteed**, dangerous-leak = 0 (F0175 proved it live). The remaining gap is purely **visual design quality**: F0175 is a *correct* flyer, not a *proudly-postable* one.

Verified (§9c) against the real artifact: `render_premium_overlay` **succeeded** for F0175 — it did **not** degrade to flat. So `f0175-current-premium-baseline.png` *is* the current premium output. Its weaknesses are concrete:

1. **Background is a generic "family dining" scene** (people-forward; the dish is incidental). *This is the single dominant visual problem.*
2. **Overlay executes plainly:** flat colored bands, a tiny "Any item $7.99" buried under the brand, plain caps brand (no emblem), modest menu rows, a floating title.

The approved **mockup A** ("Editorial Luxury") closes both: a moody **food-hero** (dosa close-up, no people) under an emblem brand lockup, decorative gold rules, a prominent **gold offer seal**, and a refined **dot-leader menu** — premium, and every character deterministically correct.

**The synthesis we're building:** *F0167's premium look + F0175's text guarantee.*

## 2. Goal & non-goals

**Goal:** ship **one** excellent deterministic premium template (Editorial Luxury) that consistently produces flyers a restaurant owner would proudly post, while preserving every safety guarantee.

**Non-goals (explicitly deferred):** B (Social Promo) and C (Modern Brand) templates; any template-selection / owner-style / auto-pick system; giving any text back to the model. *We prove one template first.*

## 3. Hermes-first analysis

| Step | Hermes skill found? | Decision |
|---|---|---|
| Image generation (food-hero background) | none (OpenRouter via flyer render) | reuse existing `_openrouter_image_bytes` / background-only path |
| Deterministic text rendering | none (flyer `premium_overlay.py`, PIL + OFL fonts) | reuse + upgrade |
| Vision read-back QA | none (flyer `visual_qa`) | reuse unchanged |
| Visual/typographic design of the template | n/a (creative) | net-new design content in the existing overlay module |

awesome-hermes-agent / ecosystem check: no skill renders a branded deterministic premium flyer. Verdict: net-new is **design content** inside two existing modules; no new primitives.

## 4. Two workstreams

### W1 — Food-hero background prompt (render.py, background-only contract)
The background-only / forced-recovery prompt currently yields generic "family/restaurant scene" imagery. Change the contract so the model produces a **single hero dish, close-up, appetizing, no people**, with **reserved calm zones** (top band for brand/title, bottom band for menu/footer) for the overlay.

- New background directive (background-only / `force_background_only` / `deterministic_recovery` paths only — i.e., wherever `_background_only_eligible(project)` is true): "Produce a **food-hero** background: one appetizing close-up of the dish(es), dramatic lighting, rich depth of field, an elegant surface; **no people, no hands, no faces**; leave calm low-detail zones along the top and bottom thirds for composited text; no text/words/logos."
- Keep the existing "do NOT render text" guarantee.
- The dish reference comes from the project's item facts (e.g., "masala dosa, idli, sambar") so the hero matches the menu — *for imagery relevance only* (the names-only directive from the v2 deterministic-recovery fix stays).
- Anchor the wording on `fixc-v2-A-hero-bg.png`'s prompt (in the mockup generator), adapted to the contract.

### W2 — Editorial Luxury overlay (premium_overlay.py)
Upgrade the deterministic overlay from the current plain execution to mockup A. The fonts are already vendored (Playfair Display, Cormorant Garamond, Montserrat) and `premium_overlay.py` already has `plan_premium_layout`, `compose_scrims`, `draw_offer_seal`, `_premium_font`, and the menu modes — v2 reworks their **visual execution**:

- **Brand lockup (new):** a small emblem (ring + monogram derived from the brand initials) above the brand name in **Cormorant small-caps**, centered, over a top gradient scrim. (Replaces the flat color band + plain caps.)
- **Title:** `Weekend Specials` in **Playfair** with **decorative gold rules** flanking a centered dot (replaces the floating title).
- **Offer seal (prominent):** a **gold circular seal** — `ANY ITEM` / `$7.99` / `EACH` — as a real focal element (replaces the tiny buried offer). Reuse/extend `draw_offer_seal`.
- **Menu:** a refined **2-column typographic list with gold dot leaders** (`Idli · · · · $7.99`) — **no cards, no plain flat rows**. Cormorant names, Playfair gold prices.
- **Scrims & palette:** stronger top + bottom **gradient** scrims over the food (not solid bands); ivory text + gold accents; the food shows through the middle.
- **Hierarchy:** one path — emblem/brand → title (rules) → hero food → offer seal → menu → contact footer (thin rule).

## 5. Hard constraints (unchanged from Fix C v1)
- **Deterministic text only** — the model never renders a character; the overlay owns all text. (The W1 prompt keeps "no text".)
- **Exact facts** — every value from `locked_facts`; coverage reuses the `visual_qa` matching helpers (identical to the referee, as in v1). No fabrication possible.
- **Fail-closed ladder preserved** — premium fits → ship premium; can't fit all text → degrade to the flat overlay (follow-up #1); flat can't fit → manual. v2 does **not** weaken this.
- **QA referee unchanged** — `visual_qa` still reads back and gates every send.
- **Flag/scope unchanged** — v2 renders behind the existing `FLYER_PREMIUM_OVERLAY` (currently scoped to `+17329837841`); flag off ⇒ flat overlay, byte-identical. **No new flag** (the existing flag + allowlist already scope it; we upgrade the rendering in place). Rollback = flag off (→ flat) or redeploy the prior build.

## 6. Dynamic layout rules (must handle real briefs, not just 6 items)
The menu must render cleanly for variable item counts (the `plan_premium_layout` modes already exist — v2 restyles them):
- **1–2 items / shared price:** a single large offer lockup + the dish name(s); the seal carries the price.
- **3–8 items:** 2-column dot-leader list (the mockup A case).
- **9–16 items:** 2-column compact, reduced leading + a font floor; if it still won't fit legibly → **fail-closed to flat** (never shrink below the legibility floor, never drop a fact).
- Long item names / non-Latin scripts: reuse v1's font fallback + wrapping; if a name can't fit → fail-closed.

## 7. Mobile-first
The binding view is the WhatsApp thumbnail. Minimum on-canvas type sizes, high contrast over scrims, footer kept ≥6% above the bottom edge (existing rule). Validate legibility at thumbnail scale, not just full size.

## 8. Migration / blast radius
- Changes are confined to `premium_overlay.py` (overlay execution) + the background-only prompt block in `render.py` (W1). No schema, no state, no new flag.
- Flag off ⇒ flat overlay path unchanged ⇒ byte-identical for everyone not on the allowlist.
- The current scoped number (`+17329837841`) gets the v2 rendering after deploy; that *is* the intended scoped validation surface.

## 9. Testing
- **Visual fidelity:** rendered output matches mockup A (emblem, rules, gold seal, dot-leader menu, food-hero composition) — manual visual review + a stored golden for regression.
- **Exact-text coverage:** reuse v1's `visual_qa`-matching coverage tests (every locked fact present; fail-closed when a required fact can't fit). Unchanged guarantee.
- **Dynamic counts:** 2 / 6 / 16-item briefs render legibly or fail-closed (never drop a fact, never sub-floor type).
- **Fail-closed ladder:** premium-can't-fit → flat; flat-can't-fit → manual (unchanged).
- **Flag-off byte-identical:** `FLYER_PREMIUM_OVERLAY` unset ⇒ flat overlay, no v2 code path.
- **W1 prompt:** background-only prompt contains the food-hero / no-people directive and retains "no text"; flag/eligibility-off prompts unchanged.

## 10. Validation (post-build, operator-gated)
Deploy dormant-compatible (flag already scoped) → re-send the F0175 brief and the combo/dessert briefs → judge against mockup A + the "would an owner post this?" bar → only then consider B/C or broader rollout.
