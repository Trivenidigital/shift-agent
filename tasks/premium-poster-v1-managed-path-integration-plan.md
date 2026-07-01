# Premium Poster v1 — Managed/Studio Path Integration (PLAN ONLY)

**Drift-check tag:** `extends-Hermes` — adds the premium-poster opt-in to the deployed managed/studio render path (one contextvar set around the primary render) + managed-specific observability; reuses the already-merged + deployed premium primitives and Hermes's image/vision gateway; leaves owner-review / QA / visible-contract / fact-firewall / text-manifest / send substrate authoritative and unmodified.

**Status:** PLAN ONLY. No code, no deploy, no flag enablement. Flag is currently OFF (disabled after the first live test). Awaiting operator approval.

**Why this slice:** the first live test (project F0192, 2026-06-30) proved `+17329837841`'s live flyer flow is the **managed/studio owner-review path** (`generate-flyer-concepts → render_concept_previews`), NOT the bare/WhatsApp-direct path #523 wired. The premium hook in `_render_model` is correct + deployed, but its opt-in is bare-only, so it never fires for this customer. This slice adds the managed-path opt-in so the premium poster reaches the customer's actual flow.

## Hermes-first capability checklist

| Step | Tag | Notes |
|---|---|---|
| 1. `+17329837841` sends a flyer brief on WhatsApp | `[Hermes]` | inbound media + dispatch |
| 2. cf-router → `generate-flyer-concepts` (managed/studio draft) | `[Hermes]` | deployed routing |
| 3. brief → `FlyerProject` + `locked_facts` | `[Hermes]` | extraction + structured output |
| 4. **managed opt-in** set around the primary `render_concept_previews` (918) when armed+eligible | `[net-new]` | ~25 LOC + tests — mirror the bare opt-in |
| 5. `_render_model` premium hook fires → best-of-N gen → textless gate → compose → critique-select | `[Hermes]` (gateway calls) / already-merged | `render_premium_poster_v1` is deployed |
| 6. premium poster written to the concept-preview path | `[net-new]` (already-merged) | same artifact path |
| 7. owner-review artifact created (existing) | `[Hermes]` | unchanged lifecycle |
| 8. `run_visual_qa` / visible-contract / fact-firewall on the result | `[Hermes]` | deployed, **authoritative, unchanged** |
| 9. owner approves → `finalize` → `send-flyer-package` → `bridge_send_media` | `[Hermes]` | unchanged |
| 10. on ANY premium failure → fall through to existing managed render (integrated + rung ladder) | `[Hermes]` | existing path is the fallback |
| 11. managed-specific observability events | `[net-new]` | ~20 LOC + tests |
| 12. flag OFF / not-allowlisted / non-food / source-edit → managed path byte-identical | `[Hermes]` | gate short-circuits |

**Net-new ≈ 45 LOC + ~12 tests.** All gen/OCR/compose/select primitives + `render_premium_poster_v1` + the gate helpers are **already merged (#517–#524) and deployed flag-OFF**. 3 of 12 steps net-new (25%) → no red flag. awesome-hermes-agent: no community skill renders a deterministic fact-locked poster over a model textless background — per-customer business logic. Verdict: tiny wiring on top of the deployed substrate.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/scripts/generate-flyer-concepts` (primary render `render_concept_previews` at line 918 in the 2-attempt loop; `_emit_premium_overlay_outcome` at 925; deterministic-fallback re-render at 956; QA at 1045; the rung ladder — brand-retry 1061 / premium-repair 1199 / deterministic-recovery 1388 with `force_background_only=True` / legacy 1607+) before choosing the opt-in site.
- ✅ Read `src/agents/flyer/render.py` (`_render_model` premium hook + `_PREMIUM_POSTER_V1_BARE_OPT_IN` contextvar + `premium_poster_v1_bare_path()` + `_premium_poster_v1_armed`/`_eligible` + `PremiumPosterV1Outcome`/`consume_premium_poster_v1_outcome`) — the hook already checks the opt-in; this slice only adds a managed setter + path identity.
- ✅ Read `src/agents/flyer/premium_poster_v1_director.py` (`compose_best_of_n`) — reused unchanged.

---

## §1 — Current managed/studio flow (file:line)

Entry `generate-flyer-concepts::main` (678). Per concept draft:
- **Primary render:** `render_concept_previews(project, asset_dir, model=draft_model, quality, concept_count)` at **918** (inside `for attempt in range(2)` at 916). `_emit_premium_overlay_outcome` (925) records the existing premium-overlay result. `render_concept_previews` (render.py:4334) → `_render_model` (render.py:4413, per concept) → writes the preview PNG to `asset_dir / {project_id}-{concept_id}-preview.png` + `inspect_rendered_asset` + `write_text_manifest` (the owner-review artifact + sidecars).
- **Deterministic fallback (NOT primary):** on `provider_timeout`, re-render with the deterministic renderer at 956 (a fallback, not the premium-eligible primary).
- **QA gate:** `run_visual_qa(project, spec.path, …)` at 1045 → `FlyerVisualQAReport` (authoritative).
- **Rung ladder (all re-renders, NOT premium-eligible):** brand-assets retry 1061 · premium-repair 1199 (`render_repair_edit`) · deterministic-recovery 1388 (`render_concept_previews(..., force_background_only=True)`) · legacy autorepair/fabrication/content 1607/1731/1849.
- **Owner-review + approval + send:** the preview is presented to the owner (status `awaiting_final_approval`, observed on F0192). On APPROVE → `finalize-flyer-assets` → `render_final_package` → per-format `run_visual_qa` → `send-flyer-package` (`validate_text_manifest_file` + `validate_visual_qa_report`) → `bridge_send_media`.

## §2 — Correct integration point

**Wrap ONLY the primary render at line 918** with a managed premium-poster opt-in. Design:

- **Generalize the opt-in to carry the path identity.** Replace the boolean `_PREMIUM_POSTER_V1_BARE_OPT_IN` with a contextvar holding the path (`None` | `"bare"` | `"managed"`), via two context managers `premium_poster_v1_bare_path()` (existing, sets `"bare"`) and a new `premium_poster_v1_managed_path()` (sets `"managed"`). `_premium_poster_v1_opt_in()` returns the path or `None`. The `_render_model` hook fires when the opt-in is set (either path); `render_premium_poster_v1` records the path in `PremiumPosterV1Outcome` so the observability events are path-distinguished. (Bare-path behavior is byte-identical — it still sets `"bare"`.)
- **generate-flyer-concepts wraps line 918 only:**
  ```
  with premium_poster_v1_managed_path():        # opt-in for the PRIMARY render only
      specs = render_concept_previews(project, asset_dir, model=draft_model, quality=…, concept_count=…)
  ```
  The opt-in is NOT set around the fallback (956) or any rung re-render (1061/1199/1388/1607/…). The deterministic-recovery rung (1388) additionally uses `force_background_only=True`, which the hook already skips — defense in depth.
- **Eligibility (already enforced by the deployed `_render_model` hook):** `not force_background_only AND opt-in set AND _premium_poster_v1_armed(project) AND _premium_poster_v1_eligible(project)`. `_premium_poster_v1_eligible` = food/grocery + required facts + **not** reference-extraction (which already excludes the source-edit/reference-image flow). No new eligibility logic needed.
- **Source-edit / reference-image flow:** those projects are `_needs_reference_extraction` → `_premium_poster_v1_eligible` returns False → premium branch not entered → existing path. (The managed source-edit path uses `render_source_edit_preview`, a different call we do NOT wrap.)

## §3 — Preserve existing fallback (any premium failure → existing managed path)

`render_premium_poster_v1` already **never raises** and returns `delivered=False` on: generation failure · OCR/textless rejection · OCR/vision outage · critique failure (→ first-accepted) · composer error · timeout · missing facts (ineligible at compose) · all-candidates-rejected (no food winner). On `delivered=False`, `_render_model` falls through to the existing primary render (integrated/deterministic), and the existing **QA + rung ladder** (brand-retry → premium-repair → deterministic-recovery → legacy → warn → manual) handle a QA failure of EITHER the premium poster or the fallback render — unchanged. The premium poster is just another candidate the existing gates judge.

## §4 — Preserve owner-review flow

The premium poster writes the **same** concept-preview path (`asset_dir/{project_id}-{concept_id}-preview.png`) via the same `_render_model`/`render_concept_previews` contract → `inspect_rendered_asset` + `write_text_manifest` run identically → the owner-review artifact + lifecycle are unchanged. **No bypass** of: owner review, `awaiting_final_approval`, `run_visual_qa`, visible-contract referee, fact firewall, text manifest, or send gating. Approval/send semantics are untouched.

## §5 — Tests (build slice; `test_flyer_*`)

- flag OFF → managed path unchanged (opt-in set but `armed` False → branch not entered).
- flag ON + not-allowlisted → managed path unchanged.
- allowlisted + eligible managed flow → premium poster selected (writes the preview; opt-in path == "managed").
- non-food managed flow → unchanged.
- missing required facts managed flow → fallback / existing path.
- source-edit / reference-image flow → unchanged (ineligible; opt-in not set around `render_source_edit_preview`).
- recovery / re-render flow (force_background_only) → unchanged (hook skips).
- premium failure (gen/OCR/critique/compose/timeout) → falls back to existing managed render.
- no model-rendered text trusted · all visible text from locked facts (composer fact-safety).
- owner-review state/lifecycle unchanged (preview path + `awaiting_final_approval`).
- **no bare-path regression** (the generalized opt-in still sets "bare"; existing bare tests green).
- opt-in path identity recorded in the outcome (managed vs bare).

## §6 — Observability (path-distinguished events)

`premium_poster_v1_managed_attempted` · `premium_poster_v1_managed_eligible` · `premium_poster_v1_managed_selected` · `premium_poster_v1_managed_fallback_reason` · `premium_poster_v1_managed_final_pass` / `_final_fail`. Emitted from `generate-flyer-concepts` by **consuming the `PremiumPosterV1Outcome` contextvar** (`consume_premium_poster_v1_outcome()`) right after the wrapped render (this also closes the #523 "outcome never consumed" observability gap). The outcome carries the path identity so managed vs bare is unambiguous, plus winner index / composite / fallback reason. Each event written through the existing `_audit_append` chokepoint (same as `FlyerIntegratedAttempted`/`flyer_premium_overlay_outcome`).

## §7 — Rollout (after the managed-path code is built + merged)

1. Deploy merged `main` with **flag OFF** (use the STAGING deploy-script path per the deploy gotcha; smoke now imports the premium stack). Verify managed path dormant.
2. Verify imports + the managed opt-in is wired (the bare-path no-op proof + a managed-path no-op-when-off proof).
3. Enable for **`+17329837841` only** (`FLYER_PREMIUM_POSTER_V1=1` + allowlist + N=1, restart gateway). Pre-test: flag ON, allowlist scoped, gate arms only for `+17329837841`.
4. Run **one live managed/studio WhatsApp test** from `+17329837841` (food brief, ≥5 items, no reference image).
5. Confirm `premium_poster_v1_managed_*` events fire in `decisions.log`; inspect the preview artifact visually; confirm owner-review approval flow intact (the owner still approves before send).
6. Kill switch unchanged: `FLYER_PREMIUM_POSTER_V1=0` + restart → instant revert.

**Constraints (carried):** no broad rollout · no N=2 · no customer expansion · no managed-path bypass · no change to approval/send semantics.

## Build sequence (net-new commits only — FUTURE, not now)

1. Generalize the opt-in contextvar to carry path identity + add `premium_poster_v1_managed_path()` + thread the path into `PremiumPosterV1Outcome` (render.py) — ~25 LOC + 6 tests.
2. Wrap the primary render (918) in `generate-flyer-concepts` with the managed opt-in + consume the outcome + emit the 6 managed events — ~20 LOC + 6 tests.

**~45 LOC + ~12 tests, flag-OFF dormant.** Multi-vector review at PR time (touches the live managed render path). **No code until this plan is approved.**
