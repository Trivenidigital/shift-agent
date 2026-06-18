# Flyer Fix C — Premium Deterministic Text-over-Imagery — Design

**Status:** Design for review. No implementation. Supersedes the Slice 2 premium-repair
direction (measured −9pp premium-delivery, 0/8 repair conversions — see
`2026-06-17-flyer-slice2-premium-repair-loop-design.md`).

**Drift-tag:** `extends-Hermes` — evolves the existing deterministic overlay
(`apply_critical_text_overlay` in `src/agents/flyer/render.py`) and the existing
background-only generation path. No new storage, no new substrate. Adds a typography
system, a template engine, and text-safe-zone enforcement on top of what already ships.

## Hermes-first analysis

| Step | Owner | Notes |
|---|---|---|
| Premium food imagery generation | **[Hermes/model]** | gemini-3.1-flash-image via the existing `_openrouter_image_bytes`; background-only mode already supported (`_background_only_eligible`). |
| Textless-background prompt contract | **[extend]** | extend the existing "leave upper-left/lower areas calm; composite text separately" instruction into explicit text-safe zones. |
| Deterministic text rendering | **[extend]** | `apply_critical_text_overlay` already draws all text via Pillow. Fix C upgrades fonts/templates/composition. |
| Exact-text verification (referee) | **[Hermes/existing]** | `visual_qa.run_visual_qa` + `classify_qa_severity` unchanged. |
| Fail-closed fit check | **[Hermes/existing]** | the `"critical text overlay does not fit" → FlyerRenderError → manual` path already exists. |
| Fact → text mapping | **[Hermes/existing]** | `collect_text_facts` / `_poster_copy_plan` already produce the exact locked text. |
| **Net-new** | **[net-new]** | template engine (A/B/C), premium font bundle, text-safe-zone scrims, layout solver by item-count, offer-treatment primitives, `FLYER_PREMIUM_OVERLAY` flag. |

awesome-hermes-agent / OpenClaw ecosystem check: no skill renders brand-safe marketing
flyers with deterministic typographic composition; this is per-customer design logic.
Verdict: **build on the existing overlay (extend-Hermes); net-new is layout/typography, not substrate.**

---

## 0. Why Fix C (the decision this design rests on)

Measured this session: the integrated image model is **consistently strong at imagery
and consistently weak at exact text** — 25% of renders mangle the brand (own-brand
misspellings like "Laksmi's"/"Lakshni's"), and the same renders also drop/mangle items
and prices. The premium-repair loop cannot fix this (fired 8×, converted 0). Safety
holds throughout (0 dangerous leaks). Today the system reacts by discarding the premium
render and shipping a flat deterministic overlay (~½ of delivered flyers), or holding to
manual (~½, much of it brand-mangle).

Fix C removes the model from the text path entirely: **the model owns imagery; a
deterministic layer owns every fact.** Visual viability was proven with real textless
model backgrounds + a premium type layer (the A/B/C mockups), which competed with the
F0167 reference and clearly beat the F0173 flat fallback. Fix C is therefore positioned
to **replace** the fallback renderer and ultimately the integrated-text primary path —
not merely improve them.

Three locked template roles (operator-approved):
- **A · Editorial** — default premium template.
- **B · Bold/Social** — social-first variant.
- **C · Festive** — campaign/holiday variant.

---

## 1. Architecture overview

```
brief → locked facts → [model: TEXTLESS premium background w/ text-safe zones]
      → [template engine picks A|B|C] → [layout solver sizes text by item-count]
      → [compositor: scrims + deterministic text/offer] → [visual_qa referee]
      → ship (premium) | fail-closed fit → manual
```

Key inversion vs today: **no text originates from the model.** The referee becomes a
near-formality (deterministic text is exact by construction) but is retained as
defense-in-depth against a compositor bug.

Components (each an independently testable unit):
1. **Background generator** — textless premium imagery + text-safe-zone prompt contract.
2. **Template engine** — selects A/B/C; holds the per-template composition spec.
3. **Text-safe-zone model** — where text may be drawn; how it coordinates with imagery.
4. **Typography system** — bundled premium fonts, type roles, scale, palette.
5. **Layout solver** — chooses menu presentation by item-count/price/name-length; fail-closed.
6. **Offer-treatment renderer** — seal / flash / inline-price primitives.
7. **Compositor** — background + scrims + text layers → final PNG.
8. **QA + funnel integration** — unchanged referee; funnel wiring via flag.

---

## 2. Text-safe zones (operator §1)

A flyer is divided into reserved zones; text is only ever drawn in them, and the hero
food is kept out of them.

- **Top band (~0–22%):** masthead (brand) + kicker.
- **Hero band (~22–62%):** imagery only — never covered by text.
- **Offer anchor (~floats):** a single reserved corner/center spot for the offer lockup.
- **Lower band (~62–100%):** title (if not top), menu, footer.

Two-tier robustness:
- **Tier 1 (ship first): fixed-zone templates + graduated scrims.** The background
  prompt asks the model to keep the top ~22% and bottom ~32% calmer/darker; the
  compositor lays a top and bottom **gradient scrim** (not an opaque box) so text is
  legible regardless and the food still shows through. This is deterministic and needs no
  image analysis. It replaces today's opaque header-mask (`shared_snack_poster` mask).
- **Tier 2 (later): saliency/contrast check.** After render, sample the chosen zones; if
  a zone is too busy/bright, deepen its scrim or switch to an alternate template. Deferred
  to a v2 — Tier 1 already guarantees legibility.

---

## 3. Typography system (operator §2)

- **Bundle OFL fonts** into the deploy artifact and extend the `_font` candidate list:
  Playfair Display (display serif), Cormorant Garamond (elegant serif body), Montserrat
  (geometric sans), Inter (UI sans). Keep NotoSansTelugu/Devanagari for regional content.
- **Type roles** (size as fraction of width `W`, tuned for 1080px → 300px thumbnail):
  | Role | Font | Weight | ~Size |
  |---|---|---|---|
  | Masthead (brand) | Playfair | 800 | 0.045W |
  | Kicker / label | Montserrat | 700 | 0.022W (wide tracking) |
  | Title (campaign) | Playfair 900 (A/C) / Montserrat 800 (B) | — | 0.10W |
  | Offer price | Playfair/Montserrat | 900 | 0.08–0.10W |
  | Menu item | Cormorant 600 (A) / Inter 600 (B) | — | 0.030W |
  | Footer | Montserrat | 600 | 0.022W |
- **Palette:** cream `#fff8ec`, gold `#ecc873`, accent red `#b81f2c`; text-shadow +
  scrim for contrast. Optional per-image accent extraction deferred to v2.

---

## 4. Dynamic layout rules — 2 / 6 / 16 items (operator §3)

The layout solver maps `(item_count, prices_distinct?, name_length)` → menu presentation.

| Case | Menu presentation | Offer | Template bias |
|---|---|---|---|
| **2 items (combo)** | two large styled lines, each with its price; hero imagery dominant | per-item price OR shared seal | C / A |
| **6 items, shared price** | single offer seal ("ANY ITEM $7.99") + names in 2 elegant rows (no repeated prices) | seal | **A** |
| **6 items, distinct prices** | 2-column dotted-leader / grid menu with prices | inline price column | **B** |
| **16 items** | 2-column compact menu, tighter leading, smaller (but ≥ floor) type; optional Veg/Non-Veg group headers when present; hero shrinks to a band | inline prices or seal | B |

- **Fail-closed fit check (retained from existing code):** if content cannot fit above a
  minimum legible font floor, raise `FlyerRenderError` → **manual** (never ship truncated
  or sub-legible text). This preserves the current safety property for dense menus.
- The solver is a pure function (item-count + metrics → spec) — directly unit-testable.

---

## 5. Offer treatments (operator §4)

Three primitives, chosen by offer shape (all text from locked facts; CURRENCY-validated;
never invented):
- **Seal** (A/C): circular or pill, gold-bordered, for a single shared offer
  ("ANY ITEM $7.99", "ANY 2 SNACKS $9.99").
- **Flash** (B): rotated red corner banner — high-contrast, social.
- **Inline price column** (B): per-item prices inside the menu when prices differ.

Source fields: `pricing_structure`, `offer:N`, `item:N:price`. Reuses the existing price
parsing + the currency-only validation so a price can never be fabricated or mis-rendered.

---

## 6. Mobile-first rendering (operator §5)

- Canonical output **1080×1350 (4:5)** — the existing `FINAL_FORMAT_PIXEL_SHAPES`
  WhatsApp image size. Instagram story (1080×1920) is a later variant.
- **Thumbnail-critical top third** must carry brand + title + offer so the flyer reads in
  a ~300px WhatsApp preview (today's code already prioritizes this region).
- Minimum legible font floors are tuned for the 300px preview and enforced by the fit
  check (§4).
- Acceptance check: render at preview width and OCR-verify masthead/title/offer are
  present and legible (a WhatsApp-mock acceptance test).

---

## 7. Text-overlay × imagery interaction (operator §6)

Compositing order: `background → top scrim (gradient) → bottom scrim (gradient) → text/offer`.
- **Graduated scrims**, not opaque panels, so the hero food remains visible while text
  stays legible — this is the visual difference between Fix C and today's flat overlay.
- **Text-safe zones (§2)** keep the hero centered and uncovered; text lives only in the
  scrimmed bands. The background prompt enforces the calm zones.
- Because **no text comes from the model**, there is no double-text and no garble; the
  existing `shared_snack_poster` opaque mask (used to hide copied source branding) is
  replaced by the scrim system.

---

## 8. Migration path from the current fallback renderer (operator §7)

Fix C lands as a new renderer `render_premium_overlay` that reuses `collect_text_facts`,
the fail-closed fit check, and `visual_qa`. Staged, flag-gated, reversible:

- **Stage 0 — Foundation (dormant):** add the font bundle + template engine + scrim
  compositor behind `FLYER_PREMIUM_OVERLAY` (off). No behavior change.
- **Stage 1 — Shadow/measure:** for the allowlisted sender, render both the current path
  and Fix C; compare premium-delivery + visual quality with the offline harness
  (`slice2_pilot.py` reused). No customer change.
- **Stage 2 — Replace the fallback:** Fix C becomes the deterministic fallback (replacing
  the flat overlay) when the integrated render fails QA. Immediate quality lift on the
  ~25% fallback bucket; lowest-risk swap (same slot).
- **Stage 3 — Primary path:** for food/menu briefs, render textless background → Fix C
  text as the PRIMARY customer flyer, retiring integrated-text-as-primary (text is never
  trusted from the model). Largest quality + reliability win; gated on Stage 2 metrics.
- Non-food categories (salon/tax/etc.) keep current behavior until templates exist.

---

## 9. Safety & error handling

- `visual_qa.run_visual_qa` + `classify_qa_severity` **unchanged** — still verifies exact
  text and the dangerous-blocker taxonomy. With deterministic text it should always pass
  on text; retained as defense-in-depth against compositor bugs.
- **Fail-closed fit check retained** — illegible/overflowing text → manual, never shipped.
- Wrong phone / fabricated price / wrong brand are **impossible by construction** (text is
  drawn only from locked facts) — this is the structural safety upgrade over the model
  text path.
- Background-gen failure → fall back to a premium solid/gradient background; never blocks.

## 10. Testing

- **Unit (in-process, deterministic — matches `test_catering_v02_scripts.py` style):**
  layout solver `(count,prices,len)→spec`, fit check, offer-treatment selection,
  fact→text mapping.
- **Golden-structure:** render A/B/C × {2,6,16 items} → assert structural invariants
  (all locked facts drawn, zones respected, fit) — not pixel-exact.
- **QA integration:** render → `run_visual_qa` → assert 0 blockers (text exact).
- **Premium-delivery:** reuse `slice2_pilot.py` to measure Fix C premium-delivery % vs the
  current fallback on the brief set.
- **Mobile legibility:** render at 300px → OCR-assert masthead/title/offer present.

## 11. New primitives introduced

`render_premium_overlay` (new renderer, extends `apply_critical_text_overlay`) ·
template specs A/B/C (data) · bundled OFL fonts (asset) · text-safe-zone background-prompt
contract (extends existing background-only instruction) · `FLYER_PREMIUM_OVERLAY` flag.

## Open questions for review

1. **Stage 2 vs Stage 3 ambition:** ship Fix C first as the *fallback replacement*
   (Stage 2, low risk, immediate lift on 25%) and let metrics earn the *primary* swap
   (Stage 3)? Recommended — but confirm you want the staged approach vs going straight to
   primary.
2. **Template selection signal:** how is A/B/C chosen per flyer — owner-selectable,
   inferred from campaign type (festival keyword → C; "social"/"insta" → B; else A), or
   always A to start? Recommend "always A to start; B/C by explicit campaign signal later."
3. **Background composition consistency:** acceptable to start with Tier-1 fixed zones
   only (no saliency detection), accepting that occasionally the model puts a bright
   element under a scrim? Recommend yes for v1.
