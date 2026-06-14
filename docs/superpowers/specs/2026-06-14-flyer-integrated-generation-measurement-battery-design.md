# Flyer-Studio — Integrated-Generation Measurement Battery (Design)

**Date:** 2026-06-14
**Author:** Claude Code (with operator)
**Status:** DESIGN — revised per operator review; awaiting final go (T5 selection) before execution
**Revised 2026-06-14** per operator review: (1) added visual-quality scoring axis [required], (2) added revision-following mini-benchmark, (3) added T5 real historical flyers, (4) Telugu = separate reporting dimension, not a pass/fail gate.
**Drift-check tag:** `extends-Hermes` — net-new *measurement harness* (eval-only, not production code). Reuses Hermes/OpenRouter image gen + existing `visual_qa` vision OCR; touches no production render path.

## Hermes-first analysis

| Step | Hermes / existing covers it? | Decision |
|---|---|---|
| Image generation (gemini-3.1-flash-image-preview) | Yes — OpenRouter image path wired (`render.py:2521–2576`); bakeoff already used it | Reuse endpoint + `OPENROUTER_API_KEY` |
| Image-to-image edit (revision benchmark) | Yes — `edit_image_model=gpt-image-1` configured (`schemas.py:940`, `render.py:2759`) | Reuse for the edit step |
| OCR / read text back | Yes — `visual_qa._vision_text()` (`visual_qa.py:1439–1478`) | Reuse for scoring |
| Vision-as-judge aesthetic scoring | Partial — vision call exists; aesthetic rubric does not | Net-new thin scoring prompt (eval-only) |
| Per-field text scoring | Partial — `visual_qa` compares but with prod block/warn policy | Net-new thin scoring layer |
| Integrated full-flyer prompt + ground-truth briefs | **None** — all existing prompts are background-only | Net-new (this doc) |

Net-new = eval harness, integrated prompt, ground-truth briefs, aesthetic + revision rubrics. Everything else reuses substrate.

## Why this battery exists

Direction **A** (integrated generation → referee → regenerate-on-fail → revision loop) rests on assumptions that have never been measured: that a strong model can render the *whole* flyer with correct business-critical text, beautifully, and survive correction/revision. Every prior artifact (the model bakeoff; the premium branch) measured only the **background-only paradigm**. This battery measures text fidelity **and** design quality **and** first-order revisionability before we commit to a migration.

## Locked decisions (operator)

| Decision | Value |
|---|---|
| Run location | main-vps (46.62.206.192) — keys, Noto Telugu fonts, prod render env |
| Generation model under test | `gemini-3.1-flash-image-preview` (fast/cheap candidate; a winner here kills the ~170s/$0.38 gpt-5.4 retry-loop problem) |
| Edit model (revision benchmark) | `gpt-image-1` (configured prod edit model); secondary: gemini-3.1 image-edit if gateway supports |
| Telugu | Synthesized; operator sanity-checks; reported strict + relaxed, **not a gate** |
| Scope | Lean first pass; hard stop + report if spend exceeds $15 |

## What this battery DOES and DOES NOT answer

**Answers:** (Q1) Can the model render business-critical text? — per-field OCR. (Q2) Consistently? — multi-sample variance + pass@1. (Q3, first-order) Does it follow correction prompts? — retry sub-run. (Q4, first-order) Does it survive a *single* revision turn preserving locked facts? — revision mini-benchmark. (Q5) Is it beautiful? — vision-as-judge + operator eyeball. (Q6) What does *production* look like? — T5 real briefs.

**Does NOT answer (don't over-interpret):** multi-turn revision drift over 5+ turns, cross-session design memory, preference learning. Those need the full revision-loop build. A strong battery result means "A is viable enough to build the loop," **not** "the loop works at depth."

## Test matrix — generation battery

| Tier | Content | Samples | Stress dimension |
|---|---|---|---|
| T1 Simple promo | Headline + offer + price + 3 items + contact | 3 | Baseline |
| T2 Medium menu | 8 items each w/ price + contact | 3 | Price fidelity, moderate density |
| T3 Dense menu | ~16 items, 3 sections, prices | 3 | Layout-induced OCR loss / missing text |
| T4 Telugu | Telugu item names + English prices/contact | 3 | Multilingual glyph (reported, **not a gate**) |
| T5 Real customer flyers | ~10 actual historical Flyer-Studio briefs (graduation / S.Indian snacks / meat promo / bakery / Bakrid / TAGCA / combo) | 2 each | **Production reality — where surprises live** |

≈ 12 (T1–T4) + ~20 (T5) = **~32 first-pass generations** + retry sub-run + revision mini-benchmark.

## Ground-truth briefs (T1–T4)

Shared identity: **Lakshmi's Kitchen — Cafe & Bakery**, 90 Brybar Dr, St Johns, FL 32259, +1 732-983-7841. GT phone string: `17329837841`.

- **T1 Simple promo** — Headline `EVERY TUESDAY NIGHT — STREET SNACK SPECIALS`; Offer `ANY 2 SNACKS` · `$9.99`; Items `Punugulu`, `Egg Bonda`, `Aloo Bonda`.
- **T2 Medium menu** — `Masala Dosa $8.99` · `Idli (2 pc) $5.99` · `Vada (2 pc) $5.49` · `Punugulu $6.99` · `Egg Bonda $4.99` · `Aloo Bonda $4.49` · `Mirchi Bhajji $5.49` · `Onion Pakora $4.99`.
- **T3 Dense menu** — *Tiffins:* Masala Dosa $8.99 · Plain Dosa $7.49 · Onion Rava Dosa $9.49 · Idli (2 pc) $5.99 · Vada (2 pc) $5.49 · Upma $5.99 · *Snacks:* Punugulu $6.99 · Egg Bonda $4.99 · Aloo Bonda $4.49 · Mirchi Bhajji $5.49 · Onion Pakora $4.99 · Veg Lollipop $7.99 · *Sweets:* Gulab Jamun (2 pc) $3.99 · Rava Kesari $4.49 · Double Ka Meetha $4.99 · Bobbatlu $5.49.
- **T4 Telugu (SYNTHESIZED — operator confirm)** — English headline `WEEKEND TIFFIN SPECIALS`; `మసాలా దోస` (Masala Dosa) $8.99 · `ఇడ్లీ` (Idli) $5.99 · `వడ` (Vada) $5.49 · `పునుగులు` (Punugulu) $6.99 · `ఉప్మా` (Upma) $5.99.

**T5 ground truth** = each project's stored `locked_facts` (already per-field: `business_name`, `contact_phone`, `location`, `campaign_title`, `schedule`, `item:N:name`, `item:N:price`, `offer:N`). No synthesis — production facts *are* the answer key; the OCR scorer maps each `fact_id` directly to a scored field. Concrete 10 in Appendix A for operator confirmation.

## Integrated full-flyer prompt (v1 — the key tuneable)

Fixed across samples; only brief content varies. Mirrors `ref.png`/`gpt.png` and demands exact text:

> "Design a single, complete, professional promotional flyer for an Indian restaurant, 1080×1350 portrait. Rich, appetizing, magazine-quality composition: integrated food photography, warm textured background, decorative Indian street-food accents (chilis, herbs, spices), strong typographic hierarchy with a bold gradient headline and a styled offer/price banner as the visual hero. The flyer MUST render the following text **exactly as written** — do not alter, paraphrase, abbreviate, translate, or change any spelling, number, currency symbol, or punctuation. Render every listed item and price; keep all text crisp and legible. [BRAND] · [HEADLINE] · [OFFER+PRICE] · [ITEMS w/ PRICES] · [CONTACT]."

Reported as a fixed input so results attribute to the model, not prompt drift.

## Scoring — Axis 1: objective text fidelity (OCR vs ground truth)

| Field | Pass criterion |
|---|---|
| Business name / headline / offer / section headers | Normalized exact (case-insensitive, whitespace-collapsed) |
| Price | Exact currency+number (`$99` ≠ `$9.99`) |
| Phone | Digit-sequence match (formatting ignored) |
| Address | Normalized token match |
| Item name (English) | Normalized exact |
| Item name (Telugu) | **Two numbers reported:** *strict* (per-glyph exact, NFC) **and** *relaxed* (per-word edit-distance ≤1 OR transliteration-equivalent). Telugu is informational, **never gates the architecture verdict.** |

Reported as **per-field accuracy** and **all-fields-correct** rate per generation (drives pass@1 / pass@3).

## Scoring — Axis 2: design quality (vision-as-judge) [operator-required]

Each generated flyer scored 0–100 by a strong vision model on:

| Metric | Meaning |
|---|---|
| Text Legibility & Fidelity | Does text look correct, crisp, well-placed (complements objective OCR) |
| Visual Appeal | Overall design quality / professionalism |
| Social-Post Worthiness | Would an owner proudly post this to social media |
| Food Appetizing Score | Does the food look genuinely appetizing |
| Overall Marketing Score | Holistic "would this drive footfall" |

**Calibration anchors (few-shot):** judge is shown `ref.png` (target ≈90), `gpt.png` (≈85), `gen.png` (≈30) as exemplars so scores are comparable across runs. Each flyer judged twice and averaged to dampen noise. All generated images are returned to the operator — **human eyeball is the final aesthetic authority**; vision-judge is for scale + triage.

## Revision-following mini-benchmark [recommended — the moat probe]

Small, first-order test of the future revision loop, using **real customer revision instructions** pulled from the corpus (not invented).

- **Bases:** T1 + 2 real T5 flyers = 3 bases.
- **Instructions (real, from `revisions[].request_text`):** `"make the food photo bigger and Telugu title brighter"` (F0003) · `"Make Telugu text bigger and add more festive colors"` (F0003) · `"make it more premium"` (operator-canonical; a near-universal real ask). → 3 bases × 3 instructions = **9 revisions** (image-to-image via gpt-image-1).
- **Measured:**
  - **Locked-fact preservation (the real risk):** OCR base vs revised — business name, price, phone, menu items must be unchanged. Any drift = failure.
  - **Layout preservation:** vision judge 0–100 ("is overall composition preserved?").
  - **Intent achievement:** vision judge 0–100 ("did the requested change happen?").
  - **Failure tags:** price drift · menu change · wholesale regeneration (layout-preservation below threshold).

## Failure categorization (text)

Each text mismatch auto-tagged + eyeball-confirmed: **char-substitution** (edit-distance 1–2) · **missing** (absent in OCR) · **layout/crop** (present but cut off/garbled) · **multilingual-degradation** (Telugu wrong/mojibake/Latinized).

## Harness mechanics

Standalone eval script on main-vps (NOT in `src/` — eval-only, scratch under `/tmp/flyer-measure/`). Per (tier, sample): call OpenRouter images with the integrated prompt → save PNG → OCR via `visual_qa` vision → score Axis 1 → vision-judge Axis 2. Then retry sub-run, then revision mini-benchmark. Output: `results.md` (all tables) + every PNG copied back to `C:\Testing\measure\` for visual review. Driven via two-step SSH→file pattern; long runs in background.

## Cost / time estimate

~32 gens + 9 edits + ~80 vision calls, on cheap/fast gemini-flash + gpt-image-1 edits ≈ rough **$5–8** (refined after first 1–2 real calls report actual cost/latency). gemini-flash latency expected far below gpt-5.4's 170s; battery measures it. **Hard stop + report at $15.**

## Out of scope (parked)

- Multi-turn revision drift (5+ turns), preference learning, cross-session design memory — deferred to the full revision-loop design after battery data.
- "Preserve approved elements" formal definition — deferred to revision-loop design (operator-sequenced).
- Multi-generation-model comparison (gpt-5.4, gpt-image-1 as generator) — only if gemini-3.1 underperforms.
- Any production code change — measurement only.

## Open items for operator review

1. **T5 selection** — the concrete 10 historical flyers (appendix, enumerated from VPS) — confirm/swap.
2. **Telugu GT (T4)** — confirm spellings.
3. **Integrated prompt v1** — accept as baseline or shape first.
4. **Scoring** — price=exact, phone=digit-match, Telugu=strict+relaxed-non-gate, vision-judge anchored on ref/gpt/gen — agree?

## Appendix A — T5 concrete selection (from VPS enumeration of 156 projects)

Selected for production diversity ($ prices, lb-weights, % offers, freebies, multilingual, dense menus, reference-based). All `completed` with populated `locked_facts`.

| # | Project | Brief (category) | Facts | Why included |
|---|---|---|---|---|
| 1 | F0001 | Bathukamma Celebrations (Telugu festival) | te | Real multilingual production case |
| 2 | F0128 | South Indian Snacks — Gavvalu/Chekkalu/Arisalu 1lb $8.99… | 13 | Snacks w/ lb-weights + prices |
| 3 | F0104 | Special Biryani's (golden-bg style pref) | 9 | Meat promotion |
| 4 | F0051 | Fresh Meats — whole-chicken hero, reference logo | grocery | Meat/butcher + reference asset |
| 5 | F0109 | Dosa Special Night (mixed lang) | 14 | **Known render-defect case** (THURRSDAY typo) — stress |
| 6 | F0132 | Weekend Breakfast — Idlie/Medhu Vada/Kheema Dosa… | 17 | Densest menu — layout stress |
| 7 | F0106 | Diwali Sale — "5–10% off + lucky draw >$100" | 6 | **% offers** (not $), festival |
| 8 | F0107 | Evening Snacks Sale — "any item $7.99 + Free Masala Chai" | 8 | Single-price + freebie |
| 9 | F0150 | STREET SNACK SPECIALS (reference-based) | 15 | **The `ref.png`/`gpt.png` snack-poster lineage** — the exact class customers compare |
| 10 | F0090 | One Year Grand Celebration — "30% dine-in / 20% …" | 9 | Celebration + dual % offers |

**Requested-but-absent categories:** *graduation, Bakrid/Eid, TAGCA, bakery* were NOT found in the 156 projects by keyword search — likely never created (or under terms I missed). Substituted with the celebration (F0090) and festival (F0001/F0106) cases above. Operator: confirm the 10, swap any, or point me to the missing-category project IDs if they exist.

Optional swaps available: F0099 Chloe Hair Studio (non-food edge), F0120 Indo-Chinese, F0050 Fresh Meats (grocery variant), F0003 Bathukamma (44 real revisions — strong revision-benchmark base).
