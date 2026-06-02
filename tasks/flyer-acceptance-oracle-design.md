# Flyer Studio — Acceptance Oracle (DESIGN, rev 2)

**Drift-check tag:** `extends-Hermes` — a custom offline acceptance/eval harness layered on existing flyer modules (`extract_text_facts`, `reference_extract`, `run_visual_qa`, the Pillow overlay renderer, the update/send scripts); reuses deployed pytest + JSON-on-disk; fights no Hermes convention.

**Status:** DESIGN ONLY — no product code changes. Rev 2 incorporates local-codex (`gpt-5.5`, xhigh) review r1 (1 BLOCKER + 6 CONCERNs, all addressed below). Topic: `flyer-acceptance-oracle`.

**Why this exists.** Flyer Studio became a live integration test bed: each request exposes a different seam interaction (intake ↔ facts ↔ planner ↔ render ↔ QA ↔ delivery), we patch the seam, the next request breaks another, PR count rises but confidence doesn't. The autonomous repair/self-eval machinery has been optimizing against **live failures** instead of a **stable acceptance contract**. The oracle *is* that contract — the product policy as **labeled examples** — so we stop rediscovering policy via incidents and the existing autonomy gets a fixed target to converge on. **The oracle is the product spec as data, not "more tests."**

---

## Hermes-first capability checklist (per-step)

| Step | Tag | Note |
|---|---|---|
| Curate the labeled corpus (requests + expected facts/delivery) | `[net-new]` | our product policy as data; no Hermes primitive |
| Score extraction gate (drives `extract_text_facts`) | `[net-new]` | custom; LLM brief call inside is `[Hermes]` gateway, already wired |
| Score reference-extraction gate (drives `reference_extract`) | `[net-new]` | custom; vision call inside is `[Hermes]` gateway |
| Score planner / render-fit / visual-QA / preview-final / revision / delivery gates | `[net-new]` | custom contract checks reusing deployed modules |
| Aggregate 3 scoreboards + persisted baseline + promotion harness | `[net-new]` | custom; pytest is a deployed pattern |
| Paid commercial sample: image render + vision OCR | `[Hermes]` | LLM/vision gateway (already wired); only the rubric is custom |

awesome-hermes-agent ecosystem check: no skill scores an app's own pipeline against labeled acceptance examples. Verdict — inherently custom; the only `[Hermes]` parts (gateway calls inside extraction/reference/paid-render) are already wired.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/facts.py` (`extract_text_facts` :481; brief provider hard-coded :577-583; `plan_creative_items` called without provider :645-647) before the extraction/planner gates.
- ✅ Read `src/agents/flyer/visual_qa.py` (`run_visual_qa` sidecar-first :1094-1115; `sha256_file(artifact)` requires a file :1122-1124) before the visual-QA gate.
- ✅ Read `src/agents/flyer/render.py` (production overlay `_apply_critical_text_overlay` fit raises at :1776-1777/:1821-1831/:1860-1861; local `_draw_flyer_pil` raises :2693-2721; integrated lever `FLYER_ALLOW_INTEGRATED_POSTER` :891-900) before the render-fit + §7d gates.
- ✅ Read `src/agents/flyer/creative_planner.py` (`plan_creative_items(..., provider=...)` :178-186) before the planner gate.
- ✅ Read `tests/test_flyer_facts.py` (monkeypatch seam :77-91) + `tests/test_flyer_visual_qa.py` (fake bytes + `.ocr.txt` :37-44) to mirror harness style.
- ⏳ **Read before Phase-2 build (newly cited by review):** `src/agents/flyer/reference_extract.py` (sidecar provider :248-255; source-contract :359-434), `src/agents/flyer/scripts/send-flyer-package` (send-format truthfulness :151-159; uncertain-retry block :204-213), `src/agents/flyer/scripts/update-flyer-project` (revisable facts :190-210; approval promotes inferred :667-698), `src/agents/flyer/scripts/create-flyer-project` + `generate-flyer-concepts` (reference extraction pending→`extract_reference`).

---

## 1. Three scoreboards + the report-mode reconciliation

Keep the axes separate (the 400-PR swamp fused them):
1. **Truth** — facts extracted/inferred/sourced/rendered correctly; no invented hard facts. Deterministic.
2. **Delivery-Reliability** — a correct flyer actually ships: fits, preview passes, final preserves facts, send-format truthful, delivery state clean. Deterministic.
3. **Commercial-Quality** — rubric/human-rated ("competent local designer", not "ChatGPT-grade"). Partly subjective; does NOT block every commit.

**Report-mode reconciliation (review fix).** The oracle **lands in baseline/report mode** — it WILL be red on the known live-failure cases; that red *is* the measured backlog, not a build failure. "Truth + Delivery must be green" is the bar for declaring the product **customer-ready**, reached over Phase 4+. The enforceable rule on every *change* is **improve-or-preserve vs the persisted baseline** (no per-case regression).

## 2. Corpus schema (labels = product policy)

`tests/flyer_oracle/corpus/*.yaml` (one per case). `disallowed_facts` is **structured assertions**, not prose (review fix):

```yaml
id: F0132
request: "weekend breakfast flyer, include Idli, Vada, Dosa, Pongal, Upma, Poori"
profile: {business_name: "Lakshmi's Kitchen", public_phone: "+1 732 ...", business_address: "90 Brybar Dr"}
config: {creative_planner: {enabled: true, enabled_categories: [restaurant, menu, ...]}}
reference_assets:                       # review fix: role + expectations
  - {path: fixtures/menu_a.png, role: menu_image,
     expected_reference_extractions: [{fact_id: "item:0:name", value: "Idli"}],
     expected_source_contract: {origin: reference_ocr|reference_vision}}
expect:
  locked_facts:
    - {fact_id: "item:0:name", value: "Idli", source: customer_text}
  allowed_inferred: 0                   # THE policy: customer enumerated ⇒ no extra items (F0132 over-infer guard)
  disallowed_facts:                     # structured anti-hallucination assertions
    - {fact_id_pattern: "contact_phone", unless_grounded_in: [profile, request]}
    - {hard_fact_class: price, max_count_not_grounded: 0}     # no ungrounded price
    - {forbidden_visible_text_regex: "\\b\\d{3}[-.\\s]\\d{4}\\b", unless_grounded: true}  # no invented phone in render
  pricing_model: flat|per_item|range|none
  delivery:
    render_fit: must_fit
    preview_final_equivalent: [schedule, contact_phone, "item:*:name"]
    send_format_truthful: true
  commercial: {rubric_min: 3}           # optional, paid tier only
```
Writing `allowed_inferred: 0` for F0132 *is* the policy decision "if the customer enumerated, don't add more" — made once, as data.

## 3. Gates (cheapest-first; reachability verified against deployed code)

| # | Gate | How it reaches the lever (review-corrected) | Cost / env |
|---|---|---|---|
| 0 | **Routing (scope note, not built)** | The oracle begins AFTER cf-router routing; routing-correctness is a separate existing concern (`dispatcher-accuracy-report`; reaching the SKILL directly is a routing miss, `flyer_dispatcher/SKILL.md:38-44`). Out of oracle scope. | — |
| 1 | **Extraction** | drive `extract_text_facts`; **monkeypatch `build_hermes_semantic_brief_provider`** (facts.py:577-583) for determinism (tests do this, test_flyer_facts.py:77-91) — NOT a public param | offline, free |
| 2 | **Reference-extraction** | drive `reference_extract` **sidecar provider** (reference_extract.py:248-255) + assert `expected_reference_extractions` + `source_contract` (:359-434) | offline, free |
| 3 | **Planner (when-to-infer)** | **monkeypatch `build_creative_planner_provider`** OR call `plan_creative_items(..., provider=...)` directly (creative_planner.py:178-186) — NOT via `extract_text_facts` (it calls the planner without a provider, facts.py:645-647). Assert: inferred only when `allowed_inferred>0`; count == requested; never overwrites/appends a grounded item (the reconciliation invariant) | offline, free |
| 4 | **Render-fit** (BLOCKER fix) | call the **production overlay** `apply_critical_text_overlay` / `_apply_critical_text_overlay` on a **blank generated canvas** at the real output sizes; catch `"critical text overlay does not fit"` (render.py:1776-1777) / `"menu overlay cannot fit all N items"` (:1821-1831, :1860-1861). `_draw_flyer_pil` ("critical text facts do not fit" :2693-2721) is a SECONDARY deterministic-renderer check | offline, **free**; **production fonts** (Linux Noto/DejaVu) to be authoritative |
| 5 | **Visual-QA** | reuse `run_visual_qa` with `allow_sidecar=True` (:1094-1115); supply a fake artifact file (`sha256_file` needs it, :1122-1124) + `.ocr.txt` sidecar (tests do this, test_flyer_visual_qa.py:37-44) | offline against fixture; paid only for a real sample |
| 6 | **Preview/final equivalence** | facts that passed preview survive to final (e.g. schedule — the #440 class) | offline |
| 7 | **Revision lifecycle** (promoted to gate) | drive `update-flyer-project` revision path: revision preserves revisable facts (:190-210), versioning correct, **no approval with unapplied revisions**, approval promotes inferred→confirmed (:667-698) | offline |
| 8 | **Delivery-state** | clean state transitions + **send-format truthfulness** + **uncertain-retry blocking** (send-flyer-package:151-159, :204-213) | offline |

Gates 1–3, 5–8 run offline anywhere; gate 4 (render-fit) is offline+free but needs production fonts to be authoritative.

## 4. Seed corpus (live failures → policy; + known-good guards)
F0130 (vague 8-item + flat price), **F0132** (explicit six-item, `allowed_inferred:0` — over-infer guard), mixed (2 given + 6 inferred = 8), flat price ($8.99 all), range/discount ("from $5", "10% off" → `pricing_model: range`, no per-item price), vague ("South Indian breakfast flyer"), **reference menu image** (reference-extraction gate), revision (lifecycle gate), preview/final schedule (#440 guard), + ≥3 known-good passes (regression guards).

## 5. Paid commercial tier
Small sample rendered through the production-safe **overlay** path, rated 1–5 vs a "competent local designer" rubric (layout, no ghost background text, on-brand, legible). Opt-in, not in the per-commit gate; informs rendering strategy, doesn't block operation.

## 6. §7d integrated-rendering interlock (real lever — review fix)
Integrated direct poster rendering stays OFF until §7d (no-fabricated-hard-fact QA) exists + passes Truth. The oracle assertion targets the **actual lever**, not a non-existent FlyerConfig flag: **env `FLYER_ALLOW_INTEGRATED_POSTER` is unset or ≠ `"1"`** (render.py:891-900) **and/or `_integrated_poster_eligible(project) is False`**. The truth-safe overlay path stays live throughout.

## 7. Promotion rule (persisted baseline — review fix)
Commit a **baseline scoreboard** (per-case gate results). Promotion of any change (human or autonomous-repair): **Truth/Delivery = no per-case regression vs baseline**; **Commercial = non-blocking in CI** unless a cached/human-rated commercial baseline exists (paid reruns stay opt-in). Autorepair is re-aimed at THIS corpus, not live traffic — a fix that doesn't improve-or-preserve the suite is rejected.

## 8. Where it lives (Phase-2 implementation, not now)
`tests/flyer_oracle/corpus/*.yaml` (cases) · `tests/flyer_oracle/gates.py` (the 8 gate runners) · `tests/flyer_oracle/scoreboard.py` (aggregate + persisted baseline + promotion entry) · `tests/test_flyer_oracle.py` (pytest, offline) · `tools/flyer-oracle-commercial-eval.py` (opt-in paid tier). **No product module is modified in the oracle slice** — it only observes.

## 9. Scope (PASS-with-clarification)
Oracle only observes; **do NOT pair product fixes with the oracle slice.** Oracle lands in report/baseline mode (known-red allowed); product fixes come in Phase 4+, each gated improve-or-preserve until the suite is fully green. No flag flip, no §8 clarification, no §7d implementation, no FAMOUS_ITEM_SETS deletion in this slice.
