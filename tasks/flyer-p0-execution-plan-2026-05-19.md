**Drift-check tag:** extends-Hermes

# Flyer Studio P0 Execution Plan — 2026-05-19

**Parent backlog:** `tasks/flyer-studio-production-readiness-backlog.md`
**Baseline:** `origin/main = 87442c9` (PR #114 merged, deploy tag `deploy-20260519-153821-1c859254`)
**Mission:** drive Flyer Studio from current ~85% pilot-ready to objectively 90% by closing P0-1, P0-2, P0-4..P0-8 (P0-3 reference media extraction shipped 2026-05-19).
**Authority for execution:** operator mission directive 2026-05-19 (autonomous through PR/review/merge/deploy; stop on secrets, payment movement, destructive prod state without backup, unresolvable merge conflicts, or scope expansion).

## Hermes-first analysis

Defers to parent backlog §"Hermes-First Analysis" (already reviewed). Verdict for this execution plan:

| Step | Hermes / net-new |
|---|---|
| WhatsApp/media ingress + delivery | `[Hermes]` — gateway + cf-router + bridge |
| Sender identity + role gating | `[Hermes]` — sender block + Flyer account state |
| JSON state + audit + safe_io | `[Hermes]` — already wired |
| OCR/vision providers | `[Hermes]` — `productivity/ocr-and-documents` + existing reference-extraction module |
| Image generation | `[Hermes]` — OpenRouter via Flyer renderer |
| Manual-review reason enum + transition guard | `[net-new]` — Flyer-specific schema + state-machine wiring |
| Operator triage view (CLI) | `[net-new]` — Flyer-specific UX |
| Operator triage view (Cockpit) | `[Hermes]` — existing FastAPI cockpit + React frontend; `[net-new]` — Flyer manual-queue route + section |
| Visual/OCR QA gate against rendered output | `[net-new]` — Flyer-specific schema mapping (Hermes OCR is the worker) |
| Locked-fact extraction parser | `[net-new]` — Flyer-specific business logic |
| Source-edit provider preflight | `[net-new]` — Flyer-specific readiness check (Hermes provider runtime exists) |
| State-machine status replies | `[net-new]` — Flyer-specific copy lookup table |
| Golden scenario corpus | `[net-new]` — Flyer-specific fixtures + assertions |

Awesome-Hermes-Agent ecosystem check: no production-ready WhatsApp Flyer Studio replacement; reuse Hermes substrate, build only Flyer-specific layer (matches parent backlog conclusion).

## Reconciliation: what's actually open against current main

Reading code at `87442c9`:

- **P0-1 context isolation:** partial. cf-router has active-project precedence + vague-start guards; `create-flyer-project` blocks attachment-dependent briefs without media. **Open:** no single project-isolation invariant enforced at project creation; no stale-project expiry; tests don't cover old-awaiting-approval + new-media-request as a class.
- **P0-2 locked-fact extraction:** partial. `FlyerLockedFact` schema exists; `extract_reference_facts` populates locked_facts from reference media. **Open:** typed locked-fact set is not derived from customer text + profile; `FlyerRequestFields.notes` still carries unstructured content; no missing-required-fact one-prompt loop.
- **P0-3 reference media extraction:** **closed 2026-05-19** (PR #113 + PR #114).
- **P0-4 visual/OCR QA gate:** schema present (`FlyerVisualQAReport`, `FlyerVisualQAStatus`, sidecar path via `FLYER_QA_ALLOW_SIDECAR`); deferred-reference smoke wired. **Open:** no real OCR-against-rendered-image comparison vs locked facts; placeholder `[price]`/`[phone]` detection not gating; failure → regenerate-once + manual-review path not wired end-to-end.
- **P0-5 source-edit provider readiness:** partial. F0029 work landed source-edit path + manual-edit fallback. **Open:** no startup/deploy preflight for OpenAI image-edit key/quota; "any update?" on queued edits not deterministically routed.
- **P0-6 customer-safe state replies:** partial. Each major status has some routing. **Open:** no exhaustive state→reply table; "status"/"any update?" can still hit LLM fallback for certain states; no max-attempt / timeout guards on generation/QA/revision loops.
- **P0-7 golden scenarios:** focused unit tests are strong (`test_flyer_*` totals ~250 passes); some category-specific tests exist. **Open:** no separately-bucketed golden scenario corpus with visual/OCR assertions; no spend-gated eval runner; no failure→backlog auto-link.
- **P0-8 manual-review queue:** core infrastructure present (`FlyerManualReview` schema, `manual_queue.py`, `flyer-manual-queue` CLI, cockpit backend `web/backend/app/routers/`). **Open:** transitions into `manual_edit_required` do NOT populate `manual_review.reason`/`detail` — the 6 dead-letter projects on prod prove this. No reason-code enum. No backfill for legacy entries. No CLI triage/summary view. Cockpit lacks a Flyer manual-queue page or actions.

**The dead-letter evidence (jq dump from `main-vps` 2026-05-19):** all 6 `manual_edit_required` projects have `manual_review.{status,reason,detail,queued_at}` defaults — proving every code path that sets `status="manual_edit_required"` today bypasses `manual_review` population. That's the structural P0-8 bug, not just stale data.

## PR-sized slices

Ordering rationale: operator explicitly prioritized P0-8 visibility first (dead-letter projects can't be safely triaged without it). After S1, work proceeds in dependency order: P0-1 isolation (prerequisite for clean P0-2 locked facts), P0-2, P0-4 (depends on P0-2 to know what to check), P0-5 (independent), P0-6 (depends on P0-1..P0-5 states existing), then P0-7 (validates everything), P0-8 cockpit (S2, can run in parallel with S3+).

Each slice = one branch off `origin/main`, one PR, parallel reviews, merge-commit, then either batched or per-slice hot-deploy depending on blast radius.

### S1 — P0-8 manual-queue triage visibility (FIRST)
- Branch: `codex/flyer-manual-queue-triage-visibility`
- Scope:
  - Add `FlyerManualReviewReason` `Literal` enum in `src/platform/schemas.py` (codes: `qa_blocked`, `source_edit_provider_unavailable`, `reference_extraction_failed`, `unsupported_media`, `operator_request`, `legacy_unknown`, `policy_block`, `provider_timeout`).
  - Tighten `FlyerManualReview.reason` from free-form `str` to that enum; default `"legacy_unknown"` (so empty-reason loads still validate).
  - Helper `mark_manual_review(project, *, reason, detail, ...)` that sets `status="queued"`, `reason`, `detail`, `queued_at` atomically. Every existing site that does `status="manual_edit_required"` must call it instead.
  - Audit transitions into `manual_edit_required` and wire `mark_manual_review` at each (initial set: F0029 source-edit path in `create-flyer-project`, generation-failure paths in `generate-flyer-concepts`, P0-4 future QA-fail paths get TODO marker — out of scope here).
  - New CLI `src/agents/flyer/scripts/backfill-flyer-manual-reasons` (operator-run, requires `--apply` flag; idempotent; takes state file path; classifies legacy entries by asset kinds + raw_request keywords; default-classifies as `legacy_unknown` with `detail` derived from `raw_request` first 200 chars).
  - Extend `flyer-manual-queue` with `--triage` mode (groups by customer_phone, sorts by age_hours desc, summarises reason counts).
  - Tests:
    - `tests/test_flyer_manual_queue.py` (new): triage view shape, reason enum validation, `mark_manual_review` atomicity.
    - `tests/test_flyer_backfill_manual_reasons.py` (new): idempotency, classifier heuristics, dry-run vs `--apply`.
    - Subprocess test for `backfill-flyer-manual-reasons` mirroring `test_flyer_generate_concepts.py` shape.
  - Smoke wiring: add `flyer-manual-queue --triage > /dev/null` to `shift-agent-smoke-test.sh`.
- Acceptance:
  - Every code-path transition into `manual_edit_required` sets a non-empty reason code from the enum (verified by repo-grep test).
  - Backfill applied to the 6 prod dead-letter projects classifies them deterministically with `manual_status="queued"` + a reason + detail derived from existing project metadata, audited to `decisions.log`.
  - `flyer-manual-queue --triage` returns grouped + reason-tallied JSON.
- Drift-tag: `extends-Hermes`.

### S2 — P0-8 cockpit operator escape hatch
- Branch: `codex/flyer-manual-queue-cockpit`
- Scope:
  - Backend route `GET /api/flyer/manual-queue` returning triage view.
  - Backend action `POST /api/flyer/manual-queue/{project_id}/complete` (operator-auth, audited).
  - Backend action `POST /api/flyer/manual-queue/{project_id}/break-glass-send` (operator-auth, audited, separate from complete).
  - Frontend section under existing Flyer dashboard surface: queue table, project drawer, complete-with-asset upload flow, break-glass with explicit reason.
  - Tests: backend `tests/web/test_flyer_manual_queue_router.py`; frontend smoke under existing pattern.
- Acceptance:
  - Operator can complete a queued project from cockpit without SSH; audit row written.
  - Break-glass send creates a `manual_review.status="break_glass_sent"` row + audit, no QA bypass without explicit reason.
- Drift-tag: `extends-Hermes`.

### S3 — P0-1 project context isolation invariant
- Branch: `codex/flyer-project-isolation-invariant`
- Scope:
  - Add `assert_isolation()` helper in `workflow.py`: project's prompt/locked-facts must reference only (current request text, customer profile, brand kit, attached assets for this message). Raises on violation.
  - Wire helper into `create-flyer-project` and `generate-flyer-concepts` before render.
  - Add stale-project policy: projects in `awaiting_final_approval` / `manual_edit_required` older than N hours surface a "continue or start fresh?" prompt instead of being silently re-used.
  - Regression tests covering old-awaiting-approval + new-poster, old-manual-edit + new-poster, repeated `create flyer` retries.
- Drift-tag: `extends-Hermes`.

### S4 — P0-2 typed locked facts from customer text
- Branch: `codex/flyer-locked-facts-from-text`
- Scope:
  - Extend `extract_locked_facts` (currently reference-only) to also derive facts from customer text + profile: business_name, headline, tagline, items, prices, schedule, address, phone, language.
  - Wire missing-required-fact prompt loop into `create-flyer-project` — single deterministic question; no premature render.
  - Tests covering `$20 men haircut`, `Idly $7`, `Any Item for $9.99`, missing-headline branch.
- Drift-tag: `extends-Hermes`.

### S5 — P0-4 real visual/OCR QA gate
- Branch: `codex/flyer-visual-ocr-qa-gate`
- Scope:
  - New module `visual_qa_runtime.py`: after render, run Hermes OCR on `spec.path`, compare to `locked_facts` + placeholder allowlist.
  - On failure: regenerate once with QA findings injected; on second failure → `mark_manual_review(reason="qa_blocked")`.
  - Output stored in `FlyerVisualQAReport` next to project.
  - Tests: placeholder detection (`[price]`, `[phone]`), missing-headline, fact-mismatch.
- Drift-tag: `extends-Hermes`.

### S6 — P0-5 source-edit provider preflight + queued-edit status
- Branch: `codex/flyer-source-edit-preflight`
- Scope:
  - Deploy-smoke gate: probe OpenAI image-edit endpoint with low-cost ping (or test the key+quota path via existing credential-readiness CLI). Missing-key returns "designer-assisted editing queued" copy at intake, not at render.
  - Route customer "any update?" on queued exact-edit projects through deterministic status table (not LLM).
  - Tests for both paths.
- Drift-tag: `extends-Hermes`.

### S7 — P0-6 deterministic state→reply table
- Branch: `codex/flyer-state-reply-table`
- Scope:
  - One source-of-truth dict mapping every `(FlyerWorkflowStatus, optional manual_review.status)` to customer-facing copy.
  - Wire `"status"` / `"any update?"` / `"is it ready?"` cf-router intent to look up via table.
  - Max-attempt + timeout guards on generation/QA/revision loops.
  - Tests for every state.
- Drift-tag: `extends-Hermes`.

### S8 — P0-7 golden scenario regression suite
- Branch: `codex/flyer-golden-scenarios`
- Scope:
  - New corpus `tests/golden/flyer/` with 50+ scenarios (start with 20: restaurant menu, halal meat, salon, tutor, temple, logo upload, exact template, reference recreation, price correction, language-specific, vague prompt, repeated corrections, stale separation × 2 directions).
  - Deterministic runner asserting locked-facts + visual-QA + state outcomes.
  - Spend-gated `--real-model` runner; CI runs deterministic only.
  - Failure→backlog auto-link (failing scenario writes a checklist row to `tasks/flyer-golden-failures.md`).
- Drift-tag: `extends-Hermes`.

## Per-slice review + merge protocol

Per repo pattern (PRs #102, #105, #113):
1. Open PR, request **three parallel reviews along orthogonal attack vectors** (see global CLAUDE.md §8):
   - **Code/structural reviewer** — does the change reach the lever it claims to pull?
   - **Test reviewer** — does the test suite actually exercise the new behavior + failure paths?
   - **Hermes-first reviewer** — is any of the proposed scope something Hermes already does?
2. Apply review findings as fix commits in same branch.
3. Merge with `gh pr merge --merge`.
4. Hot-deploy after S1, S5, S6 individually (operator-visible UX / provider readiness changes — want fast user feedback). Batch S2 with S1 on the same deploy. Batch S3+S4, S7 on a second deploy, S8 on a third.

## 90% readiness exit criteria (gate)

Mirrors parent backlog §"90% Readiness Exit Criteria"; closed only when:
- All eight slices merged + deployed.
- Full pytest gate green; each slice's tests passing.
- `flyer-manual-queue --triage` on prod shows zero items in `legacy_unknown` (operator has classified or cleared them).
- Spend-gated golden eval run shows zero P0 + ≤1 documented P1 failures.
- Deploy smoke + readiness CLI confirm `gateway active, WhatsApp bridge connected, source-edit provider healthy or explicitly queued`.

## Rolling status

Updates land here in reverse-chronological order after each slice merge/deploy.

### 2026-05-19 — S1 P0-8a manual-queue triage visibility: MERGED + DEPLOYED + BACKFILLED

- PR #115 merged at `e309028` (2 commits: feat `54c45b8` + review-fix `05a08fa`).
- Reviewer findings applied: structural HIGH (`--manual-edit-required` forward path was reproducing the F0052/F0053 null-fields shape) + structural LOW (`update-flyer-project` bypassed helper). Hermes-first reviewer: clean LGTM.
- Deploy tag `deploy-20260519-162052-05a08fa3` on `main-vps`; deploy smoke passed including new `Flyer manual-queue triage smoke passed`; pilot readiness `READY 16/16`; full pytest 1117 passed (+15 vs baseline).
- Backup taken: `/opt/shift-agent/state/flyer/projects.json.backup-pre-backfill-20260519T162138Z`.
- Backfill applied to 6 dead-letter projects. Final classification: `legacy_unknown: 3` (Chloe Hair Studio F0036/F0043/F0045), `source_edit_provider_unavailable: 3` (Lakshmi's Kitchen F0052/F0053/F0056).
- Post-deploy verification: gateway active, bridge child on `:3000`, Cockpit HTTP 200, final-package smoke `ok=true` 4 assets, triage view returning correct grouping/histogram.

**Open:** S2 P0-8b cockpit operator escape hatch (manual-queue dashboard surface + complete/break-glass actions). Then S3..S8 per plan.
