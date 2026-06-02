# Flyer Studio — Acceptance Oracle (DESIGN)

**Drift-check tag:** `extends-Hermes` — a custom offline acceptance/eval harness layered on the existing flyer modules (`extract_text_facts`, `run_visual_qa`, the Pillow overlay renderer); reuses deployed pytest + JSON-on-disk; fights no Hermes convention.

**Status:** DESIGN ONLY — no product code changes. Codex 5-lens + multi-vector review before any implementation. Topic: `flyer-acceptance-oracle`.

**Why this exists (the one-paragraph thesis).** Flyer Studio became a live integration test bed: each customer request exposes a different seam interaction (intake ↔ facts ↔ planner ↔ render ↔ QA ↔ delivery), we patch the seam, the next request breaks a different one, PR count rises but confidence doesn't. The root cause is that the autonomous repair/self-eval machinery has been optimizing against **live failures** instead of a **stable acceptance contract**. The oracle *is* that contract — the product policy expressed as **labeled examples** — so we stop rediscovering policy through production incidents, and the existing autonomy finally has a fixed target to converge on. **The oracle is not "more tests." It is the product spec as data.**

---

## Hermes-first capability checklist (per-step)

| Step | Tag | Note |
|---|---|---|
| Curate the labeled corpus (requests + expected facts/delivery) | `[net-new]` | our product policy as data; no Hermes primitive |
| Score extraction gate (drives `extract_text_facts`) | `[net-new]` | custom; the LLM call inside is the `[Hermes]` gateway, already wired |
| Score planner gate (when-to-infer / no-overwrite) | `[net-new]` | custom planner contract |
| Score render-fit gate (text fits, offline, pre-spend) | `[net-new]` | custom Pillow fit check on a blank canvas |
| Score visual-QA gate (reuse `run_visual_qa`) | `[net-new]` | custom QA |
| Score preview/final equivalence + delivery-state | `[net-new]` | custom |
| Aggregate the 3 scoreboards + promotion harness | `[net-new]` | custom; pytest is a deployed pattern |
| Paid commercial-quality sample: image render + vision OCR | `[Hermes]` | LLM/vision gateway (already wired); only the rubric is custom |

awesome-hermes-agent ecosystem check: no skill scores an app's own pipeline against labeled acceptance examples. Verdict — the harness is inherently custom; the only `[Hermes]` parts (gateway calls inside extraction + the paid render) are already wired.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/facts.py` (`extract_text_facts`, planner gate, flat-price + reconciliation) before designing the extraction/planner gates.
- ✅ Read `src/agents/flyer/visual_qa.py` (`run_visual_qa`, `_BLOCK_TIER_PATTERNS`, intent-count QA) before designing the visual-QA gate.
- ✅ Read `src/agents/flyer/render.py` (the Pillow overlay fit checks raising `FlyerRenderError("critical text facts do not fit")` at `:2697/:2716/:2721`; `_draw_flyer_pil`) before designing the render-fit gate.
- ✅ Read `src/agents/flyer/scripts/update-flyer-project` (status/preview/final transitions, `--approve-message-id`) before designing the preview/final + delivery gates.
- ✅ Read `tests/test_flyer_facts.py` + `tests/test_flyer_visual_qa.py` to mirror the deployed harness style.

---

## 1. Three scoreboards (keep the axes separate)

The 400-PR swamp came from fusing three different quality questions into one PR stream. The oracle keeps them **separate scoreboards** so a change can be judged on the axis it actually affects:

1. **Truth** — facts extracted, inferred, sourced, and rendered correctly; no invented hard facts. Deterministic. **Must be green to ship anything.**
2. **Delivery-Reliability** — a correct flyer actually ships: text fits, preview passes, final preserves the same facts, WhatsApp delivery state is clean. Deterministic. **Must be green to ship anything.**
3. **Commercial-Quality** — human/rubric-rated visual quality ("competent local designer", not "ChatGPT-grade"). Partly subjective; **does not block every live request initially** — it gates the *rendering-strategy* decision, not basic operation.

## 2. Corpus schema (the labeled contract)

Each case is a record (`tests/flyer_oracle/corpus/*.yaml` or one `corpus.jsonl`):

```yaml
id: F0132
request: "weekend breakfast flyer, include Idli, Vada, Dosa, Pongal, Upma, Poori"   # customer text
profile:                       # account/business context the agent would have
  business_name: "Lakshmi's Kitchen"
  public_phone: "+1 732 ..."
  business_address: "90 Brybar Dr"
reference_assets: []           # optional menu image / reference paths (fixtures)
config:                        # which planner categories are enabled for this case
  creative_planner: {enabled: true, enabled_categories: [restaurant, menu, ...]}
expect:
  locked_facts:                # facts that MUST be present, with source + value
    - {fact_id: "item:0:name", value: "Idli", source: customer_text}
    ...
  allowed_inferred: 0          # max planner-inferred items allowed (here: 0 — customer enumerated)
  disallowed_facts:            # facts that MUST NOT appear (anti-hallucination)
    - any hard fact not grounded in request/profile (phone/address/price/date) the customer didn't give
  delivery:
    render_fit: must_fit       # locked text must fit the overlay budget
    preview_final_equivalent: [schedule, contact_phone, item:*:name]   # facts that must survive to final
  commercial:                  # optional, paid tier only
    rubric_min: 3              # 1-5 "competent local designer" rating
```

**The labels are the product.** Writing `allowed_inferred: 0` for F0132 *is* the policy decision "if the customer enumerated their items, don't add more" — made once, as data, instead of rediscovered via the overflow incident.

## 3. The six gates (cheapest first — most failures die for free, pre-spend)

| # | Gate | Asserts | Cost | Env |
|---|---|---|---|---|
| 1 | **Extraction** | locked facts correct (id/value/source), provenance correct | offline, may call LLM gateway for the semantic brief (cheap/cacheable) | any |
| 2 | **Planner (when-to-infer)** | inferred only when `allowed_inferred>0`; count == requested; never overwrites/appends a grounded item (the reconciliation invariant) | offline (injected provider, no spend) | any |
| 3 | **Render-fit** | the locked-fact set fits the deterministic overlay **before** any image spend | offline, **free** (Pillow draw on a blank canvas; catches `FlyerRenderError("…do not fit")`) | **production fonts** (Linux Noto/DejaVu) — see §7 nuance |
| 4 | **Visual-QA** | `run_visual_qa` on a rendered/OCR fixture confirms required facts + no block-tier violations | offline against a fixture; paid only when rendering a real sample | any (fixture) / VPS (real) |
| 5 | **Preview/final equivalence** | facts that passed preview survive to the final WhatsApp format (e.g. schedule — the #440 class) | offline | any |
| 6 | **Delivery-state** | clean WhatsApp delivery state (status transitions valid; no stuck/duplicate; send-format truthful) | offline (state-machine assertion) | any |

Gates 1–3 + 5–6 are **offline and deterministic** and catch the truth + reliability failures we've been hitting — *render-fit alone would have caught the overflow incident with zero live failures and zero spend.* Gate 4 is offline against a fixture; only the **paid commercial sample** (§5) spends.

## 4. Seed cases (the live failures, promoted from anecdotes to policy)

Ship these as the first labeled set (Codex's list + the schedule case):
- **F0130** — vague "8 famous South Indian breakfast items, any item $8.99" (planner infers 8 items + flat price).
- **F0132** — explicit six-item breakfast list (`allowed_inferred: 0`; the over-infer/overflow regression guard).
- **Mixed** — "include these 2, add 6 more" (`allowed_inferred: 6`, total 8).
- **Flat price** — "all items $8.99" (price provenance `customer_text`, applied to inferred names).
- **Range/discount** — "from $5", "10% off" (no per-item price asserted; structure text only).
- **Vague** — "South Indian breakfast flyer" (no count; bounded inference).
- **Reference menu image** — extraction from an uploaded menu (fixture).
- **Revision** — a follow-up edit to an inferred item (lifecycle + `customer_confirmed`).
- **Preview/final schedule** — a flyer whose schedule must survive preview → final (#440 guard).
- **Known-good passes** — at least 3 cases that currently work, so the suite guards against regressions, not just fixes.

## 5. Paid commercial-quality tier

A small sample (e.g. 5–8 cases) actually rendered through the production-safe **overlay** path, rated 1–5 against a **"competent local designer"** rubric (layout, no ghost background text, on-brand, legible, not obviously templated). Human-rated or rubric-rated; **opt-in, not in the per-commit gate**. This is the only tier that spends. It informs the rendering-strategy decision; it does not block basic operation.

## 6. §7d integrated-rendering interlock (hard gate)

Integrated direct image rendering (image model owns the whole poster) **stays OFF** until the **§7d no-fabricated-hard-fact QA** exists and passes the Truth scoreboard. The eval already showed integrated rendering hallucinates phone/address/prices. The oracle encodes this as a hard interlock: `integrated_rendering_enabled == false` is itself an assertion until §7d is green. The current truth-safe **overlay** path stays live throughout.

## 7. Promotion rule + the one environment nuance

**Promotion rule:** any change — human or autonomous-repair — is acceptable only if it **improves or preserves** the suite (no Truth/Delivery regression; Commercial non-decreasing on the rated sample). The autonomous repair engine is re-aimed at **this corpus**, not live traffic: a proposed fix that doesn't improve-or-preserve the suite is rejected. That is what converts the engine from whack-a-mole accelerator into a convergence tool.

**Environment nuance (render-fit):** the fit check is deterministic *given the fonts*, but it uses the Linux Noto/DejaVu fonts; on a non-prod box the metrics differ. So the render-fit gate (3) and the real visual-QA/commercial tiers (4 paid, 5) must run in a **production-font environment** (the VPS or a Linux CI container with the same fonts) to be authoritative. The offline-deterministic gates 1, 2, 5, 6 run anywhere.

## 8. Where it lives / file touch list (implementation phase, not now)

| Path | Purpose |
|---|---|
| `tests/flyer_oracle/corpus/*.yaml` (or `corpus.jsonl`) | the labeled cases |
| `tests/flyer_oracle/gates.py` | the six gate runners (reuse `extract_text_facts`, `run_visual_qa`, the Pillow fit path) |
| `tests/flyer_oracle/scoreboard.py` | aggregate + print 3 scoreboards; promotion-rule entry point |
| `tests/test_flyer_oracle.py` | pytest wrapper (offline gates) for CI |
| `tools/flyer-oracle-commercial-eval.py` | opt-in paid rubric tier (separate, not in CI) |
| **NOT touched in the oracle slice** | any product module (`facts.py`/`visual_qa.py`/`render.py`/scripts) — the oracle only *observes*. Fixes come in Phase 4+, each gated by this suite. |

## 9. Build sequence (net-new only; design-first → review → code)
1. Corpus schema + 8–10 seed labeled cases (the policy decisions as data).
2. Offline gate runners (extraction, planner, render-fit, visual-QA-fixture, preview/final, delivery) reusing deployed modules.
3. Scoreboard aggregator + pytest promotion harness (CI-runnable, offline).
4. Paid commercial rubric tier (separate, opt-in).

## 10. Explicitly NOT in scope here
- No renderer change, no new rendering strategy (that's a later decision, made *against* this oracle).
- No flag flip / category change / FAMOUS_ITEM_SETS deletion.
- No §8 clarification-gate or §7d implementation in the oracle slice — the oracle *measures* the gap; building §7d is Phase 5, gated by this suite.
- The oracle only observes the pipeline; it never mutates product behavior.
