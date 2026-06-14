# Flyer Architecture A — Slice 1: Integrated Generation + Hardened Referee + Retry/Fallback (Design)

**Date:** 2026-06-14 · **Status:** DESIGN — operator-approved direction; awaiting spec review before writing-plans
**Drift-check tag:** `extends-Hermes` — modifies the existing flyer agent; reuses the Hermes image gateway, the deployed `FlyerLockedFact` contract, `visual_qa`, the `FlyerProject` state machine, the deterministic renderer, and the `repair_instruction` primitive. Net-new = fabricated-offer/price detection, the retry→fallback orchestration, and observability.
**Evidence baseline:** `2026-06-14-flyer-measurement-battery-RESULTS.md` (gemini-3.1 integrated: ~31/32 pass@1, T5 real briefs 20/20, ~12s/$0.068, Telugu ~80% glyph; one failure class = fabricated offers/prices).

## Hermes-first analysis

| Step | Hermes / existing covers it? | Decision |
|---|---|---|
| Image generation (gemini-3.1) | Yes — OpenRouter image path (`render.py:2521`), model from `cfg.flyer.draft_image_model` | Reuse; point config at gemini-3.1 |
| Corrective re-render | Yes — `repair_instruction` plumbed through `_image_prompt`/`_render_model` (`render.py:1844,3357`) | Reuse for retry |
| Referee / OCR | Yes — `visual_qa.run_visual_qa` + blocker taxonomy (`visual_qa.py:1499,1191`) | Extend with fabrication check |
| Fact contract | Yes — `FlyerLockedFact` + `facts.py` extraction/merge/required (`schemas.py:1704`, `facts.py:684`) | Reuse unchanged |
| Deterministic fallback | Yes — `_render`/overlay path (`render.py:3352`) | Reuse as fallback |
| Audit/observability | Yes — `decisions.log` chokepoint + `FlyerVisualQAReport` | Reuse for metrics + QA note |

Net-new is small: fabrication detection, orchestration loop, observability. No new substrate.

## Goal

Ship the gen.png→F0150 quality jump as a **narrow production migration**: integrated gemini-3.1 generation becomes the primary draft/final path for food/grocery flyers; the deterministic renderer becomes the verified fallback. **Invariant: no flyer ships unverified or worse than today.**

## Locked decisions (operator, 2026-06-14)

| Decision | Value |
|---|---|
| Languages | **All languages integrated** (incl. Telugu); deterministic fallback is the safety net. Telugu = integrated-first, **biased to fallback when OCR confidence low / glyph validation uncertain**. Week-1 human spot-check on Telugu. |
| Retry/fallback | **Retry ×2 (corrective) → deterministic fallback → `manual_edit_required`.** |
| Rollout | **Flag ON for all food/grocery customers immediately**, behind a kill-switch. |
| Kill-switch | Must force the deterministic path **byte-identical to today**. |
| Fabricated offer/price | **Blocking**, not warning. |
| Referee failure on integrated output | **Never proceed to customer approval** → fall back to deterministic. |
| Referee-unavailable fallback | Allowed but **NOT silent** — metric + QA note `integrated_referee_unavailable_fallback`. |
| Deterministic fallback | **Still runs the referee**; if it fails → `manual_edit_required`. |
| QR/barcode | Deterministic final composite (rule in place; no-op today). |
| Out of scope | Source-edit lane, reference-extraction-pending, subjective revision loop (Slice 2), design memory (Slice 3). |

## Components (six focused changes)

### 1. Config + kill-switch
- `cfg.flyer.draft_image_model` + `final_image_model` → `google/gemini-3.1-flash-image-preview` (today `"deterministic-renderer"`).
- New env **`FLYER_INTEGRATED_KILLSWITCH`**: when set, the orchestrator **short-circuits before any integrated/eligibility logic** and renders via the existing deterministic path with the existing inputs — guaranteeing byte-identical output to today. (Tested by output-hash equality.)

### 2. Eligibility widening (`_integrated_poster_eligible`, `render.py:1102`)
- **Remove** the `len(items) > 3` cap and the `len(items) > 12` reference-menu cap (battery passed the 16-item T3 2/3 and dense real menus 20/20).
- **Remove** the regional-script exclusion (Telugu now integrated).
- **Keep excluded:** `_needs_reference_extraction` (facts not yet in `locked_facts` → unverifiable) and `_is_source_edit_project` (Slice 2+).
- Keep the `_is_food_or_grocery_project` gate (Slice 1 scope).
- Net: any food/grocery flyer whose facts are materialized in `locked_facts` → integrated primary.

### 3. Referee extension — fabricated offer/price detection (`visual_qa.py`)
- **Anchor on numbers (low false-positive):** extract every `$`-price token from OCR `extracted_text`; if a price appears that matches **no** `locked_fact` value (item price, offer, pricing_structure) after normalization → **block** (`fabricated_price`).
- **Discount/offer claims (with numbers):** extend the existing operational-claim pattern set with discount patterns (`% off`, `free <x>`, `buy … get`, `any N … $`, `starting from $`, `N offers`). If such a claim's price/number isn't backed by a locked offer/pricing fact → **block** (`fabricated_offer`).
- **Promotional claims WITHOUT a price (operator refinement):** detect promo-wording phrases that have no dollar figure but still imply an offer — e.g. `limited time`, `limited time deal`, `today only`, `special combo`, `lunch/dinner offer`, `special deal`, `grand sale`, `flat off`. If any such phrase appears in OCR and **no** offer/pricing/promotion fact exists in `locked_facts` → **block** (`fabricated_offer`). (A flyer can mislead with "Limited Time Deal" even with no `$`.)
- Wording variance is tolerated (a reworded legit offer with a *locked* price passes); only **unauthorized numbers/claims** block. All are **block-tier** in `classify_qa_severity`.
- Ensure `run_visual_qa` actually executes on the integrated path (re-enable if `FLYER_BARE_SKIP_VISUAL_QA` is set on box).

### 4. Retry/fallback orchestration (`scripts/generate-flyer-concepts`)
State machine after the integrated render:
1. `run_visual_qa` → **pass** → set `awaiting_final_approval` (existing).
2. **block & correctable** (missing fact / fabricated price / fabricated offer / wrong price) → build `repair_instruction` from blockers. The instruction is **two-sided** (operator refinement): (a) *include* the exact locked facts that are missing, AND (b) **"remove any claim, price, offer, discount, badge, or label that is not in this list: [locked facts]"** — directly targeting the battery's fabricated-banner failure mode. Re-render integrated. **Up to 2 retries.**
3. retries exhausted → **deterministic fallback** render → `run_visual_qa` → pass → approval; fail → `manual_edit_required`.
4. **Referee unavailable** on integrated (`status == provider_unavailable` / OCR error) → **do not ship integrated** → deterministic fallback render → `run_visual_qa`; AND record QA note `integrated_referee_unavailable_fallback` + metric (see §6).
5. Generation API error after `_openrouter_image_bytes`'s own 3× retry → deterministic fallback.
- Telugu nuance: for regional-script projects, treat low-confidence/uncertain glyph validation as a **correctable block** (→ retry → fallback), i.e. bias to the guaranteed-correct deterministic Telugu render. Use a stronger vision model for the regional OCR check (configurable).

### 5. QR/barcode guardrail
- If a project carries a QR/barcode fact, composite it deterministically as a final step post-generation (machine-read elements must be pixel-exact). No-op today (no such facts). Rule documented + a guard so a future QR fact can't be left to the model.

### 6. Observability + kill-switch visibility
- Structured `decisions.log` entries (existing chokepoint) per outcome: `flyer_integrated_attempted`, `_passed`, `_retried` (n), `_fell_back_deterministic` (with `reason`: `retries_exhausted` | `referee_unavailable` | `generation_error`), `_manual_review`.
- On referee-unavailable fallback: append a `FlyerVisualQAReport`-style note / project QA note **`integrated_referee_unavailable_fallback`** (operator-visible, not customer-visible) so silent quality degradation is detectable (§12a-style).

## Data flow

```
facts locked (existing)
   └─[kill-switch? → deterministic render, byte-identical, DONE]
   → render integrated (gemini-3.1)
      → referee (required present? + nothing fabricated?)
         ├ pass → awaiting_final_approval
         ├ correctable block → repair_instruction → re-render (×2)
         │     └ still block → deterministic fallback → referee → pass→approval | fail→manual_edit_required
         └ referee unavailable → [QA note + metric] → deterministic fallback → referee → pass→approval | fail→manual_edit_required
```

## Error handling
- Generation API: existing 3× retry in `_openrouter_image_bytes`; persistent failure → deterministic fallback (metric `reason=generation_error`).
- Referee unavailable: never ship integrated unverified → deterministic fallback + QA note + metric.
- Deterministic fallback itself failing referee → `manual_edit_required` (operator).
- Concurrency: existing version-snapshot guard in `generate-flyer-concepts` (refuse to overwrite newer state) is preserved.

## Testing
- **Eligibility (unit):** English >3 items now eligible; Telugu eligible; `_needs_reference_extraction` + source-edit still excluded; non-food excluded.
- **Fabrication (unit):** OCR with a `$`-price absent from `locked_facts` → block; reworded legit offer with locked price → pass; `% off`/BOGO not in facts → block; **non-dollar promo phrase** ("Limited Time Deal" / "Special Combo" / "Lunch Offer") with no offer fact → block (operator refinement); same phrase WITH a matching locked offer fact → pass.
- **Repair instruction (unit):** generated `repair_instruction` for a fabricated-banner block contains BOTH an include-clause and a remove-clause ("remove any claim/price/offer/discount/badge/label not in [locked facts]").
- **Orchestration (unit, mocked render+QA):** block×3 → deterministic invoked → `manual_edit_required`; block→pass on retry 1 → approval; referee-unavailable → deterministic + QA note present.
- **Kill-switch (regression):** output hash with `FLYER_INTEGRATED_KILLSWITCH` set == today's deterministic output (byte-identical).
- **Live smoke:** one real flyer on the test sender (+17329837841) end-to-end before relying on fleet-wide; confirm `decisions.log` metrics fire.
- Test style mirrors `tests/test_flyer_renderer.py` + subprocess-invoke pattern (`test_catering_v02_scripts.py`).

## Risks (explicit)
- **Telugu glyph error slipping past noisy OCR referee → ships wrong Telugu.** Mitigations: strict glyph match vs `locked_facts`, stronger regional vision model, bias-to-fallback on low confidence, week-1 human spot-check. Residual risk acknowledged (operator-accepted).
- **Fabrication false-positive** (OCR misreads a legit price) → unnecessary retry/fallback (safe, lower quality). Mitigated by number-anchored matching + normalization.
- **Fleet-wide, no soak:** mitigated by worst-case = today's deterministic output + kill-switch + metrics. Practical blast radius small (Lakshmi's active).
- **Per-flyer cost** ~$0.07–0.21 (1–3 gens) vs ~free deterministic; negligible at current volume, note for scale.

## Open items for operator review
1. Confirm fabrication detection anchored on price-numbers (+ discount-claim patterns) is the right precision/recall trade.
2. Confirm the stronger regional vision model choice for Telugu glyph verification (proposal: gemini-2.5-flash or gpt-4o vision for the regional check only).
3. Confirm metric names / QA-note string for your dashboards.
