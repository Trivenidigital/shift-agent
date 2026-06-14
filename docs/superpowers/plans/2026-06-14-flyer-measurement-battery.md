# Flyer Integrated-Generation Measurement Battery — Implementation Plan

> **For agentic workers:** eval harness, executed inline this session on main-vps. Steps use `- [ ]`. This is a throwaway measurement script (not production code), so tasks are build-units with verification checkpoints rather than per-function TDD.

**Goal:** Measure whether `gemini-3.1-flash-image-preview` can render full integrated flyers (text + art) with accurate business-critical text, beautifully, and survive one revision turn — to decide if architecture A is viable.

**Architecture:** Standalone Python harness under `/tmp/flyer-measure/` on main-vps. Raw HTTP to OpenRouter (image gen + vision OCR + vision judge) mirroring `render.py`/`visual_qa.py` auth; `gpt-image-1` for revision edits. Reads ground truth from production `locked_facts`. Self-contained; touches no production code/state (read-only on projects.json).

**Tech Stack:** Python 3, urllib/requests (match deployed), OpenRouter API, `OPENROUTER_API_KEY` + `OPENAI_API_KEY` from VPS env.

**Spec:** `docs/superpowers/specs/2026-06-14-flyer-integrated-generation-measurement-battery-design.md`

**Hard constraints:** $15 spend hard-stop · Telugu non-gating · read-only on prod state · all PNGs returned for operator eyeball.

---

### Task 1: Mirror the deployed API call shapes

**Files:** Read `src/agents/flyer/render.py:2510–2640` (`_openrouter_image_bytes`), `render.py:2750–2800` (gpt-image-1 edit), `src/agents/flyer/visual_qa.py:1430–1480` (`_vision_text`).

- [ ] Read those ranges; record exact endpoint URLs, request payload shape, model-name strings, response-parsing (where image bytes / text live), and how cost/usage is read.
- [ ] Confirm the OpenRouter image endpoint + how `gemini-3.1-flash-image-preview` is named in payload (cross-check the bakeoff used the same path).

**Verify:** I can state the exact POST URL + JSON body for (a) image gen, (b) vision OCR, (c) gpt-image-1 edit.

---

### Task 2: Briefs + ground-truth loader

**Files:** Create `/tmp/flyer-measure/briefs.py`.

- [ ] Hardcode T1–T4 briefs + ground truth from the spec (business identity, items, prices, Telugu strings).
- [ ] Load T5 (the 10 IDs: F0001,F0128,F0104,F0051,F0109,F0132,F0106,F0107,F0150,F0090) from `/opt/shift-agent/state/flyer/projects.json`; map each `locked_facts[].fact_id` → scored field (`business_name`, `contact_phone`, `location`, `campaign_title`, `item:N:name`, `item:N:price`, `offer:N`, `schedule`).
- [ ] Emit a normalized `Brief{id, fields:{business,phone,address,headline,schedule,offer,items:[{name,price}],telugu_items,lang}}` for each.

**Verify:** Print all 14 briefs' ground truth; eyeball T5 facts match the corpus probe (F0128 = Gavvalu/Chekkalu/Arisalu; F0132 = 17 facts).

---

### Task 3: Integrated generation client

**Files:** Create `/tmp/flyer-measure/gen.py`.

- [ ] `build_prompt(brief)` → the fixed integrated prompt (spec v1) with the brief's exact strings interpolated.
- [ ] `generate(brief, model) -> (png_path, latency_s, cost_usd)` via OpenRouter image endpoint (Task 1 shape). Save PNG to `/tmp/flyer-measure/out/<id>__s<N>.png`.

**Verify:** function returns a real PNG path (deferred to Task 4 dry-run).

---

### Task 4: 🚦 DRY-RUN GATE (checkpoint before any real spend)

- [ ] Run `generate(T1, "gemini-3.1-flash-image-preview")` **once**.
- [ ] Confirm: PNG saved, non-trivial size, visually a flyer (read it back).
- [ ] Record **actual** latency + cost for one call.
- [ ] Extrapolate total: `(~32 gens + 9 edits) × measured cost`. **If projected > $15 → STOP, report to operator, do not proceed to full run.**

**Verify:** one real flyer image + a concrete cost/latency number + a go/no-go on the $15 budget. **Report this to operator before Task 9 full run.**

---

### Task 5: OCR + Axis-1 text scorer

**Files:** Create `/tmp/flyer-measure/score_text.py`.

- [ ] `ocr(png) -> str` via vision model (Task 1 shape).
- [ ] `score(ocr_text, brief) -> {per_field: pass/fail, all_correct: bool, failures:[{field, type}]}`. Rules: normalized-exact (names/headline/offer), price exact (`$9.99`≠`$99`), phone digit-match, address token-match, items per-name+per-price. Telugu: compute **strict** (per-glyph NFC) AND **relaxed** (per-word edit-distance ≤1) — report both, never gate.
- [ ] Failure tags: char-substitution / missing / layout-crop / multilingual-degradation.

**Verify:** feed the dry-run PNG → printed per-field scorecard looks sane vs the image.

---

### Task 6: Axis-2 vision judge (design quality)

**Files:** Create `/tmp/flyer-measure/judge.py`.

- [ ] Upload calibration anchors (`ref.png`≈90, `gpt.png`≈85, `gen.png`≈30) in the judge prompt; request JSON: `{text_legibility, visual_appeal, social_worthiness, food_appetizing, overall_marketing}` 0–100.
- [ ] `judge(png) -> scores`; call twice, average.

**Verify:** judge `gen.png` itself → overall should land near its ~30 anchor (sanity check the judge is calibrated).

---

### Task 7: Retry loop

**Files:** add to orchestrator.

- [ ] On `all_correct == False`: regenerate with a corrective prompt naming the wrong fields (`"the price must read exactly $9.99"`), up to 3 attempts. Record pass@1, pass@3, attempts-to-pass, cumulative cost/latency.

**Verify:** a known-failing cell shows attempt history.

---

### Task 8: Revision benchmark (gpt-image-1 edits)

**Files:** Create `/tmp/flyer-measure/revise.py`.

- [ ] 3 bases (T1 + 2 T5) × 3 real instructions ("make the food photo bigger and Telugu title brighter", "Make Telugu text bigger and add more festive colors", "make it more premium"). Edit via gpt-image-1 (Task 1 edit shape).
- [ ] Measure: locked-fact preservation (OCR base vs revised — any price/phone/menu drift = fail), layout-preservation (judge 0–100), intent-achievement (judge 0–100). Tag failures: price-drift / menu-change / wholesale-regen.

**Verify:** 9 revision rows with preservation + intent numbers.

---

### Task 9: Orchestrator + cost guard + report

**Files:** Create `/tmp/flyer-measure/run_battery.py`.

- [ ] Loop tiers × samples → generate → OCR-score → judge; running cost total; **abort at $15**.
- [ ] Then retry sub-run, then revision benchmark.
- [ ] Emit `/tmp/flyer-measure/results.md`: (1) per-field accuracy, (2) accuracy by tier, (3) retry stats + cost/latency, (4) failure categories, (5) Axis-2 design scores, (6) revision benchmark.

**Verify:** results.md renders all 6 tables.

---

### Task 10: Full run + collect

- [ ] Run `run_battery.py` in background on VPS (it's slow).
- [ ] On completion: `scp` `results.md` + all PNGs to `C:\Testing\measure\`.
- [ ] Read results.md; present the 4+ tables + representative flyers to operator for side-by-side beauty/fidelity review.

**Verify:** operator has tables + images.

---

## Self-review

- **Spec coverage:** T1–T5 ✓ (Task 2), integrated prompt ✓ (T3), Axis-1 OCR ✓ (T5), Axis-2 vision ✓ (T6), retry ✓ (T7), revision benchmark w/ real feedback ✓ (T8), failure categories ✓ (T5), Telugu strict+relaxed non-gate ✓ (T5), $15 stop ✓ (T4/T9), images returned ✓ (T10).
- **Placeholders:** none — model names, endpoints (resolved in T1), IDs, instructions all concrete.
- **Consistency:** field names (`all_correct`, `per_field`, fact_id mapping) consistent across T2/T5/T7.
- **Risk:** biggest unknown = exact OpenRouter payload for gemini-3.1 image + whether it returns image bytes inline; Task 1 resolves before any spend; Task 4 gate catches cost surprises.
