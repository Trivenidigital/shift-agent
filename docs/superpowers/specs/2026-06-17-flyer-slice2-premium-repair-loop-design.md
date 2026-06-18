# Flyer Slice 2 — Premium Image-to-Image Repair Loop (Design)

**Drift-check tag:** `extends-Hermes` — adds a repair-render mode + recovery-ladder
wiring on top of the existing Hermes OpenRouter gateway, referee, and recovery
classification. No new external API; the image-edit call uses the same OpenRouter
gateway the integrated generation already uses.

**Status:** DESIGN ONLY — for operator review. **No implementation** until approved.

**Goal (one sentence):** When the integrated render is premium but the referee flags
a *recoverable* text defect, repair the text **in place via image-to-image edit
(preserving the composition)** so the customer receives a `gpt.png`-grade flyer,
instead of discarding it for the flat deterministic overlay.

---

## Hermes-first analysis

| Domain | Hermes / ecosystem skill found? | Decision |
|---|---|---|
| LLM/image gateway | yes — Hermes routes image gen via OpenRouter (gemini-3.1) | **use it** — the repair edit is one more call on the same gateway |
| Image-to-image edit | none (Hermes vision = read/extract, not edit) | **use OpenRouter gemini image-edit** (validated by `revise.py`, 9/9 preserved); NOT gpt-image-1 (needs absent `OPENAI_API_KEY`) |
| Vision QA / referee | yes — `visual_qa.py` (deployed) | **reuse unchanged** — referee stays authoritative |
| Corrective-instruction builder | yes — `repair.py` `build_repair_instruction` (Slice 1) | **reuse + scope to minimal edit** |
| Recovery classification | yes — `_qa_failed_exact_text_recoverable` (deployed) | **reuse unchanged** — same recoverable/dangerous split |

awesome-hermes-agent ecosystem check: no SMB flyer-repair skill exists; image-edit
is a gateway capability, not a packaged skill. **Verdict:** net-new is the *repair
render mode + ladder placement*; everything else is reused substrate.

---

## Problem & why now

`you.png` (the flat overlay) shipped instead of `gen.png` (premium) because the
integrated model dropped/misspelled items on a dense menu and the safety net fell
back to the flat overlay to guarantee correct text. The flat overlay is the
**safety net, not the destination** (operator).

The prompt experiment is concluded: on the real production function the lean prompt
landed at **21/24 = 21/24** aggregate pass@1 vs current — it traded item-drops for
brand-omission/label-leak/fabrication, no material gain. **Prompt work is no longer
the highest-leverage path.** The repair loop is, because it fixes *any* recoverable
text defect on the premium render rather than chasing prompt variants that each
surface a new defect class.

## Drift reality — what already exists (credit before building)

- **Image-to-image edit, validated:** `revise.py` `edit_image(base_png, instruction)`
  — base64 the prior render as an `image_url` message part + instruction → gemini-3.1
  via OpenRouter → edited image. Measurement battery: **9/9 revisions preserved locked
  facts, zero drift** → composition preservation is proven.
- **Corrective instructions:** `repair.py` `build_repair_instruction(blockers, locked)`
  — two-sided (re-state locked + remove fabricated). Built in Slice 1.
- **Recovery ladder** in `scripts/generate-flyer-concepts`: initial render → referee →
  legacy autorepair (text-to-image regen) → fabrication retry ×2 (text-to-image) →
  content-miss retry ×1 (text-to-image) → deterministic-overlay fallback → manual.
- **Referee + severity:** `visual_qa.py` — classifies blockers, block/warn/pass, the
  authoritative gate.
- **Recoverable/dangerous classification:** `_qa_failed_exact_text_recoverable(reports,
  locked_fact_ids)` — recoverable = missing fact / item-count / dup / `inferred item
  not rendered` / `visible text defect` (misspelling) / item-price-mismatch-with-locked-
  price; **dangerous (hard-block)** = `fabricated price/offer`, `unverified phone`.

**The single net-new idea:** the current recovery retries are **text-to-image regen**
(they throw away the premium composition and generate a new one). Slice 2 replaces the
retry mechanism for recoverable defects with **image-to-image edit of the premium
render** (keep the composition, fix only the defective text). The flat overlay remains
the floor; the safety classification is unchanged.

---

## 1. Premium render preservation

- **Mechanism:** `edit_image(premium_render_png, minimal_instruction)` via the
  OpenRouter gemini-3.1 image-edit call (the `revise.py` pattern). The prior premium
  render is the base image; the model edits it.
- **Minimal-edit instruction:** built from the referee's *specific* blockers via
  `repair.py`, scoped to ONLY the defective fields, e.g.:
  *"Edit this exact flyer. Change ONLY: add a menu item 'Vada — $7.99'; fix the
  spelling 'Uttapoo' → 'Uttapam'. Keep every other element identical — layout,
  colours, photography, fonts, all other text. Do not restyle or recompose."*
- **Why this preserves composition:** image-to-image edit conditions on the base
  image; the 9/9 revision result shows it changes the targeted text while holding
  layout/identity. Text-to-image regen does not (it re-rolls the whole design).
- **Preservation check (anti-regression):** the repaired render is re-run through the
  **full referee** — so a repair that wrecks the composition, drops a previously-good
  fact, or introduces a new defect is caught (it will fail QA → next rung).

## 2. Text-defect classes → repair strategy

Slice 2 **inherits the existing recoverable/dangerous classification verbatim** — it
only changes the *recovery action* for recoverable defects. Mapping the operator's six
classes:

| Defect class | Referee blocker (existing) | Class | Slice-2 action |
|---|---|---|---|
| Missing item | `missing required visible fact: item:N:name` / `inferred item not rendered:` | recoverable | image-to-image: add the item + locked price |
| Misspelled item | `visible text defect reported by QA: …misspelling…` | recoverable | image-to-image: fix the spelling to the locked name |
| Missing business name | `missing required visible fact: business_name` | recoverable | image-to-image: add the brand header (locked name) |
| Missing schedule | `missing required visible fact: schedule` | recoverable | image-to-image: add the schedule text |
| **Wrong phone** | `unverified phone number visible:` | **dangerous** | **HARD-BLOCK → manual (never repaired)** |
| **Fabricated offer** | `fabricated offer/price visible:` | **dangerous** | **HARD-BLOCK → manual (never repaired)** |

Note the missing-vs-wrong distinction the existing gate already encodes: a *missing*
phone/business-name (locked value known, just not drawn) is **recoverable** (the edit
adds the locked value); a *wrong/unverified* phone (a different number visible) is
**dangerous** (human verifies — the value itself is suspect). Same logic applies to a
*missing* offer (recoverable) vs a *fabricated* offer (dangerous).

## 3. Repair success criteria

- **Success** = the image-to-image repaired render **passes the full referee** (all
  required locked facts visible, no fabrication, no wrong/unverified phone) AND
  carries no NEW recoverable defect. On success → **ship as premium** (the customer
  gets the repaired premium render, not the flat overlay).
- **Retry** = the repaired render still has a *recoverable* defect (edit under- or
  partially-applied, or introduced a new recoverable defect) → re-issue an
  image-to-image edit targeting the **residual** blockers. **Bounded: ×2** (matches
  the existing fabrication-retry budget; no loop).
- **Fallback** = after the bounded repairs the render still fails QA → fall to the
  **deterministic overlay** (today's safe floor) → if the overlay also fails →
  **manual**. (Unchanged tail of the ladder.)
- **Immediate hard-block** = at ANY point a render carries a *dangerous* blocker
  (fabricated offer/price, wrong/unverified phone) — including a repair that
  *introduces* one — → **manual**, never repaired, never auto-shipped.

Ladder after Slice 2: initial render → referee → **[recoverable defect] image-to-image
repair ×≤2 → re-QA** → (pass) ship premium / (still recoverable) deterministic overlay
→ manual; **[dangerous defect] → manual** at any rung.

## 4. Safety model

- **Referee remains authoritative.** Every render — initial, each repair, and the
  overlay fallback — passes through `visual_qa.py`. Nothing ships without a referee
  pass (the only exception is the existing referee-unavailable → pure-deterministic
  path, unchanged).
- **Fabrication and wrong phone remain hard-blocks** — never repaired, never shipped;
  routed to manual. A repair that *introduces* fabrication/wrong-phone is caught on
  re-QA and hard-blocks.
- **Deterministic overlay stays the floor** when repair can't produce a clean premium
  render — no regression to the current safety guarantee.
- **Bounded** (×2 repairs) — no infinite loop; cost is at most 2 extra image-edit
  calls per defective render.
- **Flag-gated + scoped** rollout (mirrors Slice 1): `FLYER_PREMIUM_REPAIR` default
  OFF, allowlist-scopable to the test sender, instant revert by unsetting. The flat
  overlay + the entire current ladder is the byte-identical fallback when OFF.

## 5. Measurement plan

**North-star metric: premium-delivery rate** = % of flyers that ship as the
integrated *premium* render (initial-pass OR repaired-to-pass), vs flat-overlay
fallback, vs manual.

- **Baseline (current):** premium = initial-pass only; every recoverable defect →
  flat overlay. From the validation batteries the integrated pass@1 is ~85–90% and
  the flat-fallback rate on dense menus is ~12–38% per run (the gap we're closing).
- **Offline A/B battery** (same harness/cost discipline as the prompt batteries — no
  deploy, OpenRouter, watch the balance): take the set of renders that **currently
  fall back** (a recoverable defect present), run the image-to-image repair on each,
  and measure:
  1. **repair-success rate** — % of would-be-fallbacks the repair converts to a clean
     premium render (referee pass);
  2. **residual fallback rate** — % still requiring the flat overlay after ×2;
  3. **composition preservation** — visual-quality (judge) of repaired ≥ the flat
     overlay it replaces, and ≥ the original premium render (no degradation);
  4. **dangerous-leak rate** — fabricated offer / wrong phone reaching approval
     **(must be 0)**;
  5. **cost/latency** — extra edit calls per defective render.
- **Comparison:** premium-delivery rate **with** the repair loop vs the current
  fallback behavior, on F0166 (6) / T2 (8) / T3 (16) plus a brand-omission and a
  misspelling case (the lean-surfaced defects), N≥8/cell.
- **Pre-registered ship bar:** repair converts **≥60%** of would-be-fallbacks to clean
  premium **AND** dangerous-leak rate = 0 **AND** repaired visual-quality ≥ flat-overlay
  baseline. Below that → repair loop does not justify rollout; keep the flat floor.

---

## Resolved decisions (autonomous build)

1. **Additive, not replace.** Image-to-image repair is a NEW *first* recovery rung for
   recoverable defects (flag-gated). If it produces a clean premium render → ship. If it
   fails after ×2 → fall through to the **existing ladder unchanged** (legacy autorepair →
   content-miss retry → deterministic overlay → manual). This keeps the flag-OFF path
   byte-identical and the proven ladder intact as the fallback — the safest shape.
2. **Repair budget ×2** (matches the fabrication-retry budget; bounded, no loop).
3. **Missing-phone repairable, wrong/unverified phone hard-block** — inherited verbatim
   from the existing gate (`missing required visible fact: contact_phone` = recoverable;
   `unverified phone number visible:` = dangerous). No new classification.
4. **Brand-omission in scope** — `missing required visible fact: business_name` is
   recoverable → repaired (add the locked brand). Directly fixes the lean-surfaced defect.

## Deployed infra found (drift read)

`render.py` already has robust OpenRouter/gemini image-to-image edit:
`_openrouter_source_edit_bytes` (base64 image part + prompt → edited bytes, 3-retry,
full error handling) used by `render_source_edit_preview`. **Net-new shrinks to:** (a)
extract a generic `_openrouter_image_edit_bytes(base_path, prompt, …)` and add a
`render_repair_edit` that edits the **prior premium render** (NOT the customer reference)
with a scoped repair instruction and **does NOT composite the overlay** (the model's
premium text is preserved + re-verified by the referee); (b) flag-gated first-rung wiring
in `generate-flyer-concepts`; (c) the scoped minimal-edit instruction in `repair.py`.
NOTE: `render_source_edit_preview` deliberately overlays deterministic text on the edit;
the repair path must NOT — it preserves the model-rendered premium text and relies on the
referee to verify it.

## Out of scope

Source-edit via gpt-image-1 (absent key); design-session memory / preference learning
(Slice 3); any change to the referee's classification or the deterministic overlay; any
prompt change (concluded). Routing-hijack, SPECIAL-COMBO over-flag, recovery-watchdog
crash remain separate tracked follow-ups.

## Rollout

Flag-gated dormant deploy → offline battery proves the ship bar → flip
`FLYER_PREMIUM_REPAIR` scoped to +17329837841 → live-validate → fleet-wide on
measured threshold. Kill = unset the flag (instant revert to the current ladder).
