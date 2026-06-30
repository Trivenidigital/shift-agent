# Premium Poster v1 — Integration Slice (PLAN ONLY)

**Drift-check tag:** `extends-Hermes` — adds a flag-gated, allowlist-scoped render branch + observability on top of the deployed flyer render path; reuses Hermes's image/vision gateway; leaves the existing QA / visible-contract / fact-firewall / send substrate authoritative and unmodified.

**Status:** PLAN ONLY. No code, no deploy, no routing, no rollout, no flag enablement. Awaiting operator approval of the design + the open decisions in §0.

## Hermes-first capability checklist

| Step | Tag | Notes |
|---|---|---|
| 1. WhatsApp ingest / identity / brief extraction → `locked_facts` | `[Hermes]` | inbound media, dispatch, vision extraction, structured output (substrate) |
| 2. Render dispatch exists (`_render_model`) | `[Hermes]` | deployed flyer render path |
| 3. Premium-poster eligibility gate (flag + allowlist + food/grocery + required facts) | `[net-new]` | ~40 LOC + 8 tests — mirrors `_premium_overlay_enabled`/`_premium_overlay_allowlist` |
| 4. N textless background generations | `[Hermes]` | each gen = OpenRouter image gateway; the N-loop + prompt selection is wiring |
| 5. OCR/textless gate per candidate | `[Hermes]` | vision gateway (`_vision_text`); gate logic already built in the director |
| 6. Deterministic composition (`compose_premium_poster_v1`) | `[net-new]` | already merged #517–#521; zero new substrate |
| 7. Vision critique selector | `[Hermes]` | oracle = vision gateway; selection already built (`compose_best_of_n` #522) |
| 8. `render_premium_poster_v1` hook in `_render_model` + write target + fall-through | `[net-new]` | ~120 LOC + 12 tests |
| 9. Observability (10 log events) | `[net-new]` | ~30 LOC + 4 tests; reuses decisions.log/contextvar pattern |
| 10. Existing visual_qa / visible-contract / fact-firewall gates | `[Hermes]` | deployed substrate, **unchanged + authoritative** |
| 11. Existing send path (`bridge_send_media`) | `[Hermes]` | unchanged |
| 12. Fail-safe fallback to existing path | `[Hermes]` | the existing render path is the fallback |

**Net-new ≈ 190 LOC + ~24 tests — pure wiring.** All generation/OCR/composition/selection primitives are already merged (#517–#522). 3 of 12 steps net-new (25%) → no red flag. **awesome-hermes-agent ecosystem check:** no community skill renders a deterministic fact-locked marketing poster over a model-generated textless background; this is per-customer business logic. Verdict: build the wiring; reuse Hermes for every model call + the entire QA/send substrate.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/render.py` (`_render_model` at line 4282, `_apply_critical_text_overlay` at 3182, `_premium_overlay_allowlist`/`_normalize_sender` at 3466/3434, `_background_only_eligible` at 1297, `_openrouter_image_bytes` at 3229, `_FORCE_BACKGROUND_ONLY` at 183) before drafting the hook point + the eligibility/allowlist helpers — the new branch mirrors the deployed `_premium_overlay_enabled` + CD-v2 inverted-guard patterns.
- ✅ Read `src/agents/flyer/visual_qa.py` (`run_visual_qa` at 1695, `_vision_text` at 1635) before drafting the textless-OCR adapter + confirming the authoritative QA gate stays unchanged.
- ✅ Read `src/agents/flyer/bare_render.py` (`render_grounded` at 1026, the `run_visual_qa` wrapper + visible-contract ordering at 934/944, allowlist helpers) before deciding the live-path target (bare vs managed).
- ✅ Read `src/agents/flyer/premium_poster_v1_director.py` (`compose_best_of_n`, `critique_composed_poster`, `build_textless_food_prompt`) and `src/agents/flyer/premium_poster_v1.py` (`compose_premium_poster_v1` eligibility) before drafting the orchestration wiring — these primitives are already merged and reused as-is.

---

## §0 — Open decisions for operator (plan-only; please confirm before the build slice)

- **D1 — Render path target.** The live `+17329837841` flyer flow is the **bare/WhatsApp-direct path** (`bare_render.render_grounded` → `render.render_concept_previews` → `_render_model`). The managed/studio path (`generate-flyer-concepts`, operator-approved) shares `render.py`. **Recommendation:** hook in `render.py` (shared by both paths), gated by eligibility, so the live bare-path test for `+17329837841` activates it; the managed path inherits it only when a project is likewise eligible+allowlisted. Confirm: bare path first (live test), or both?
- **D2 — Hook level (best-of-N needs the generation loop).** **Recommendation: Option A** — a dedicated `render_premium_poster_v1(project, target, *, size, output_format, n)` called at the TOP of `_render_model` (render.py:4282), which owns gen→gate→compose→select→write. Option B (`_apply_critical_text_overlay` hook, render.py:3182) is simpler but only composes over the **single** already-generated background — it cannot do best-of-N and would use render.py's existing bg prompt, not the shadow-validated director prompt. See §2.
- **D3 — Background prompt.** **Recommendation:** use the director's `build_textless_food_prompt` (scene families + negative-space + no-text contract) — this is the **exact prompt validated in the C2/C3/best-of-N shadow runs** that produced the good posters. The alternative (render.py's `_image_message_content` force-background-only prompt) is unvalidated for this composer. Tradeoff: a dedicated generator (thin OpenRouter call) rather than reusing `_openrouter_image_bytes`.
- **D4 — N and latency (material).** N=2 means **2 image generations (~30–60s each) + 2 OCR + 2 critique = ~2–4 min added latency** on a live WhatsApp turn. **Recommendation:** ship behind a hard total timeout (≈90–120s) that falls back to the existing path; and consider **N=1 for the first live test** (lowest latency/cost; best-of-N's measured lift was marginal) with N as a config knob (`FLYER_PREMIUM_POSTER_V1_N`, default 1, set 2 to enable best-of-N). Confirm N for the first live test.
- **D5 — Coexistence with the existing CD branch.** `+17329837841` is already in `FLYER_CREATIVE_DIRECTOR_ALLOWLIST` / `FLYER_VISIBLE_CONTRACT_ALLOWLIST` / `FLYER_PREMIUM_OVERLAY_ALLOWLIST` (per deployment records — **§9a verify on the box before enabling**). The premium-poster-v1 branch must take **precedence** when armed, else fall through. Confirm precedence ordering.

---

## §1 — Current `render.py` dispatch flow (drift read; file:line)

**Two render paths, both into `render.py`:**

**Path A — Managed/Studio (operator-approved):** `generate-flyer-concepts::main` → `render_concept_previews` (render.py:4334) → per concept `_render_model` (render.py:4282) → `run_visual_qa` (gate) → rung ladder. On APPROVE: `render_final_package` (render.py:4482) → `send-flyer-package` (text-manifest + QA gate) → `bridge_send_media`.

**Path B — Bare/WhatsApp-direct (no operator gate):** `bare-flyer-render-and-send::main` → `bare_render.render_grounded` (bare_render.py:1026) → `_generate_poster` (or `_render_creative_director_grounded` when CD armed) → `render.render_concept_previews` → `_render_model` → `run_visual_qa` wrapper (bare_render.py:934: visible-contract gate first, then broad QA) → `(SEND|FAILCLOSED)` → `send_image` → `bridge_send_media`.

**Background-only generation:** `_render_model` (4282) → for non-deterministic models, `_openrouter_image_bytes` (3229, returns PNG bytes) using `_image_message_content` (2031); `_background_only_eligible(project)` (1297) = `not _needs_reference_extraction and not _integrated_poster_eligible`; background-only prompt forbids text (2140–2165). Context var `_FORCE_BACKGROUND_ONLY` (183). Then `_apply_critical_text_overlay` (3182) composes deterministic text over the textless bg. Model = config `flyer.draft_image_model` (= `google/gemini-3.1-flash-image-preview` on box).

**QA / firewall / referee (order + authority):**
1. **Creative-planner firewall** (`creative_firewall.py`, pre-render) — drops hard-fact-class inferred items; never a send gate.
2. **Visible-contract referee** (bare path, `bare_render.py:944`, armed by `FLYER_VISIBLE_CONTRACT` + allowlist) — concrete violation → **block**; OCR-unreadable → send-anyway + `unverified`. Runs **before** broad QA.
3. **`run_visual_qa`** (`visual_qa.py:1695`) — authoritative `FlyerVisualQAReport` (`passed`/`failed` × `block`/`warn`). Managed: `status != passed` → routing. Bare: `_qa_allows_send`.
4. **Text-manifest + QA sidecar gate** at send (`send-flyer-package`) — final delivery gate.

**Fallback rungs (managed):** brand-assets retry → premium repair (`FLYER_PREMIUM_REPAIR`) → deterministic recovery (`FLYER_DETERMINISTIC_RECOVERY`, `force_background_only=True`) → legacy autorepair → warn-tier → manual. `model="deterministic-renderer"` (Pillow-only) is the `FLYER_INTEGRATED_KILLSWITCH` panic path.

**Flags + allowlist:** allowlist mechanism = `_normalize_sender` (3434) + `_premium_overlay_allowlist` (3466) parsing comma-separated `FLYER_*_ALLOWLIST`. **CD v2's inverted guard (empty allowlist = DISABLED, render.py:3537) is the safe scoped-rollout pattern to mirror.** `FLYER_PREMIUM_POSTER_V1` exists (premium_poster_v1.py:35) but is **not referenced by render.py** (unrouted).

**Send path:** managed → `render_final_package` → `run_visual_qa` → `send-flyer-package` validate → `bridge_send_media`. Bare → `render_grounded` returns bytes → `send_image` → `bridge_send_media` (safe_io).

---

## §2 — Proposed guarded routing

**Hook (Option A, recommended):** new `render_premium_poster_v1(project, target, *, size, output_format, n)` in `render.py`, called at the top of `_render_model`:

```
# inside _render_model, before the existing deterministic/openrouter branches:
if _premium_poster_v1_armed(project) and _premium_poster_v1_eligible(project):
    outcome = render_premium_poster_v1(project, path, size=size, output_format=output_format,
                                       n=_premium_poster_v1_n())
    _PREMIUM_POSTER_V1_OUTCOME.set(outcome)         # telemetry contextvar (mirror _PREMIUM_OVERLAY_OUTCOME)
    if outcome.delivered:                           # wrote a valid poster to `path`
        return
    # any miss (ineligible at compose, all-rejected, timeout, error) -> fall through, logged
... existing _render_model logic unchanged ...
```

`render_premium_poster_v1` wires the **already-merged** primitives with real adapters and a hard timeout, then writes the winner to `target`; the **existing downstream `run_visual_qa` + visible-contract + send gates run on the result, unchanged and authoritative**:

- **eligibility** — `_premium_poster_v1_armed(project)` = `FLYER_PREMIUM_POSTER_V1=1` AND `_normalize_sender(project.customer_phone) in _premium_poster_v1_allowlist()` (CD-v2 inverted guard: **empty allowlist = DISABLED**). `_premium_poster_v1_eligible(project)` = food/grocery category AND required locked facts present (business_name + offer/price + ≥3 items — same predicate `compose_premium_poster_v1` already enforces) AND `_background_only_eligible(project)`.
- **generator** — `(prompt)->path`: call the OpenRouter image model with the **director prompt** (`build_textless_food_prompt`, D3) → write bytes to a managed temp path → return path. (Reuses the gateway + key + model exactly as the shadow runs.)
- **textless_ocr** — `(PIL)->bool`: adapter over `visual_qa._vision_text` (empty extracted_text → textless; raises → check_error).
- **critique_scorer** — `(path,brief)->dict|None`: adapter over `flyer_art_director_oracle.score_art_direction` (vision oracle).
- **select** — `compose_best_of_n(facts, generator, textless_ocr, critique_scorer, n)` → winner img; write to `target`.
- **timeout** — wall-clock budget (D4); on exceed → `delivered=False`, fall through.

**Why this hook:** `_render_model` is the single per-concept render entry shared by both paths; it owns `path`/`size`/`output_format`/`project.locked_facts`; writing `target` in-place matches the existing contract (downstream `inspect_rendered_asset` + `run_visual_qa` are unchanged). Best-of-N's generation loop lives naturally here (above the single-bg `_openrouter_image_bytes`).

---

## §3 — Hard safety rules (invariants the build slice MUST hold)

1. **No model-rendered text is trusted** — the generated image contributes **only food/background**; every visible word comes from `locked_facts` via deterministic Pillow (`compose_premium_poster_v1`).
2. **OCR/textless failure rejects the background** — a candidate whose vision read-back finds any text is excluded from selection (never composed-as-winner); on all-rejected → fall through to the existing path.
3. **The existing fact firewall + visual_qa + visible-contract remain authoritative** — premium-poster output is gated by them **after** composition, exactly like any other render. The premium path NEVER bypasses or weakens them.
4. **Critique is selector/shadow only** — it ranks candidates; it never gates customer delivery and never overrides the authoritative QA gates.
5. **The existing send path is not weakened** — no change to `send-flyer-package` / `bridge_send_media` / the text-manifest gate.
6. **Non-eligible flows are byte-identical** — flag off OR allowlist miss OR non-food OR missing facts → the existing render path runs unchanged (the new branch is not entered).
7. **Fail-safe by construction** — any failure in the premium path → fall through to the existing path; the customer always gets the same-or-better outcome they'd get today, never a worse one and never unvalidated model text.

---

## §4 — Failure behavior (exact, all → fail-safe + log + fall back; never ship unvalidated model text)

| Failure | Behavior |
|---|---|
| Image generation failure (provider/HTTP/None) | candidate recorded `generation_failed`; if all → fall through to existing path |
| Partial candidate failure (1 of N fails) | that candidate excluded; selection proceeds among the rest |
| All candidates rejected (text/error) | `delivered=False` → fall through to existing path (deterministic recovery / flat overlay) |
| OCR/textless check error (vision outage) | candidate `check_error` → excluded; if all → fall through. **Never** treat an unverifiable image as textless |
| Critique unavailable | selection falls back to first accepted candidate (still composed + gated); never blocks |
| Malformed critique | treated as unavailable → first accepted; logged |
| Composer error / ineligible at compose | candidate `compose_error`/`compose_ineligible`; if no winner → fall through |
| Visual QA failure (downstream) | **existing** rung ladder handles it exactly as today (premium path output is just another render) |
| Missing required facts | `_premium_poster_v1_eligible` returns False → branch not entered → existing path |
| Timeout (premium path exceeds budget) | abort premium path → `delivered=False` → existing path |
| Provider failure (key missing/placeholder) | generator/OCR/critique adapters fail-safe → fall through |

**Default everywhere:** fail safe · log the reason · fall back to the existing path · never ship unvalidated model text.

---

## §5 — Tests (build slice; all `test_flyer_*` since PIL-dependent)

- flag off → existing path unchanged (branch not entered; byte-identical render).
- allowlist miss → existing path unchanged.
- non-food/grocery → existing path unchanged.
- missing required facts → `_premium_poster_v1_eligible` False → existing path.
- armed + eligible + one valid candidate → premium poster written to target; downstream QA still runs.
- one rejected + one valid candidate → valid one selected + written.
- all candidates rejected → fall through to existing path (no premium poster written).
- OCR failure (vision outage) → candidate excluded; all-fail → fall through.
- critique unavailable → first accepted still selected, still gated.
- generated image never read for facts (winner `placed_text` ⊆ locked facts).
- selected poster remains fact-safe (no fabricated label; price-only no label).
- timeout → fall through.
- `_premium_poster_v1_armed`/`_allowlist` unit tests (CD-v2 inverted guard: empty allowlist = disabled).
- observability: each log event emitted on its path (eligible/generated/textless-pass/-fail/score/selected/fallback/final).
- **`send-path-ci` unaffected** — the new tests are `test_flyer_*` (excluded from `send-path-ci`; run locally + deploy smoke), and the existing non-flyer suite is untouched.

---

## §6 — Rollout plan (no broad rollout)

1. Code merged to `main` with **flag OFF** (dormant; CI green; review clean).
2. Deploy to the **test/main VPS** only (tarball; pin-gate; backup; smoke).
3. **§9a runtime-state verification on the box FIRST** — confirm `FLYER_PREMIUM_POSTER_V1` is unset, the allowlist envs' current contents, that `+17329837841`'s existing CD/visible-contract arming won't conflict, OPENROUTER key + image model present, and the deployed `render.py` matches `main` (no deploy drift).
4. Enable for **`+17329837841` only** (`FLYER_PREMIUM_POSTER_V1=1` + `FLYER_PREMIUM_POSTER_V1_ALLOWLIST=+17329837841`), N per D4.
5. Run **3–5 live WhatsApp internal tests** from `+17329837841`.
6. Compare live outputs against the shadow artifacts (`C:/Testing/bestofn-out` etc.).
7. Monitor `journalctl`/decisions.log for the §7 events + any fallback reasons.
8. **Kill switch:** any issue → set `FLYER_PREMIUM_POSTER_V1=0` (instant revert to the existing path; reversible by construction).

---

## §7 — Observability (structured log events; via decisions.log / the existing contextvar+audit pattern)

`premium_poster_v1_attempted` · `premium_poster_v1_eligible` · `premium_poster_v1_candidate_generated` · `premium_poster_v1_textless_pass` · `premium_poster_v1_textless_fail` · `premium_poster_v1_candidate_score` · `premium_poster_v1_selected` · `premium_poster_v1_fallback_reason` · `premium_poster_v1_final_pass` · `premium_poster_v1_final_fail`.

Each carries: sender (normalized), concept_id, candidate index, scene_key, textless result, composite, winner index, fallback reason, and the downstream QA verdict. Emit a dispatched/outcome pair around the premium path so "no logs" is never ambiguous (cf. CLAUDE.md §12b). The `_PREMIUM_POSTER_V1_OUTCOME` contextvar carries the structured outcome into the audit row written by the orchestrator (mirrors `_PREMIUM_OVERLAY_OUTCOME` / `FlyerCreativeDirectorRouted`).

---

## §8 — Scratch cleanup (reproducibility; do AFTER archival)

**Already archived to durable local review storage:** `C:/Testing/c2b-out` (11), `C:/Testing/c3-out` (14), `C:/Testing/bestofn-out` (25) — generated backgrounds, rendered winners/candidates, prompts, OCR results, critique scores, best-of-N logs + the C2B/C3/#522 reports (in this conversation + the per-brief JSONs).

**Reproducibility note:** the shadow scripts (`shadow_c2b.py`, `shadow_c3.py`, `shadow_bestofn.py`) + staging trees are NOT in the repo. Before cleanup, the build slice should **commit the canonical run script** (a repo-versioned shadow harness under `src/agents/flyer/scripts/` or `tasks/`) so future runs are reproducible from repo code — scratch must never be the source of truth.

**Then clean (operator go):** `/root/flyer-c2b-shadow`, `/root/flyer-c3-shadow`, `/root/flyer-bestofn-shadow` on the box (artifacts already copied out). Local `C:/Testing/*` kept as the review archive.

---

## Build sequence (net-new commits only — for the FUTURE build slice, not now)

1. `_premium_poster_v1_armed` + `_premium_poster_v1_allowlist` + `_premium_poster_v1_n` + `_premium_poster_v1_eligible` (mirror overlay helpers; CD-v2 inverted guard) — ~40 LOC + 8 tests.
2. `render_premium_poster_v1` + the real adapters (generator/OCR/critique) + timeout + the `_render_model` hook + fall-through — ~120 LOC + 12 tests.
3. Observability (10 events + contextvar outcome) — ~30 LOC + 4 tests.
4. Repo-versioned shadow/repro harness (§8) — small.

**~190 LOC + ~24 tests, flag-OFF dormant.** Each step is independently reviewable; multi-vector review at PR time (this touches the live path → structural + prod-state + scope vectors per CLAUDE.md §8). **No code until this plan is approved.**
