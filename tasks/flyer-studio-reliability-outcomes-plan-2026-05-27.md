**Drift-check tag:** extends-Hermes

# Flyer Studio Reliability Outcomes Plan - 2026-05-27

## Incident Anchor

Production project `F0105` for Lakshmi's Kitchen did not return a flyer to the customer. The audited data path shows:

- `generate-flyer-concepts` failed in `draft_render_concept_previews` with `FlyerRenderError: Pillow is required for exact identity overlay`.
- Production enters Flyer scripts through `/usr/local/lib/hermes-agent/venv/bin/python`, and that venv does not have `PIL`; system Python has Pillow. `render.py` already intends system-Pillow fallback for rendering, but the exact-identity overlay path bypasses that fallback and raises directly.
- The project moved to `manual_edit_required`, but the failure was stamped as `provider_timeout` even though the real class is missing runtime dependency.
- Recovery opened incidents and worker drafts, then reached `operator_action_required`; customer-visible repair did not happen automatically.
- `FlyerProject` currently stores `customer_phone` and `original_message_id`, but not `customer_id` or `chat_id`, even though `create-flyer-project` already accepts `--chat-id` and resolves the customer record. Recovery later reads project state and therefore lacks strong origin evidence.

## New Primitives Introduced

- `dependency_missing` manual-review reason code for deterministic dependency/import failures.
- Persisted `FlyerProject.customer_id` and `FlyerProject.chat_id` fields.
- Recovery project-origin hydration from persisted project identity.
- Operator alert on recovery outcomes that require human action or suppress a customer ack for missing origin evidence.
- Deploy/smoke gate that proves the deployed render path can complete the exact-identity overlay, whether via Hermes-venv Pillow or the existing `/usr/bin/python3` system-Pillow fallback.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress, sender identity, and bridge delivery | yes - existing Hermes gateway + cf-router + bridge chokepoints | reuse; no new router |
| Runtime dependency management | no Hermes skill or repo-owned installer for mutating `/usr/local/lib/hermes-agent/venv` packages | do not add an ad hoc venv installer; restore the existing system-Pillow fallback contract and smoke the real render path |
| Flyer generation and visual QA | yes - existing Flyer scripts on Hermes substrate | keep existing flow; only classify dependency failures correctly |
| Recovery/ARE worker draft loop | yes - existing `flyer-recovery-watchdog` + worker draft states | reuse; add identity hydration and owner alert at existing failure outcome points |
| Operator notification | yes - `safe_io.notify_owner_with_fallback` / `shift-agent-notify-owner` | reuse; no custom notification transport |
| Semantic flyer brief interpretation | partial - current tree already has `flyer_semantic_brief`; Hermes LLM substrate exists | do not build a new semantic brain in this PR; record residual product architecture separately |

Awesome Hermes ecosystem check: no install-now Hermes skill replaces Flyer Studio's project-specific render dependency gate, manual-review reason mapping, project identity persistence, or recovery outcome alerting. This is narrow product reliability glue on top of Hermes substrate.

## Drift-Checked Current Tree

- `src/agents/shift/scripts/shift-agent-smoke-test.sh` imports `flyer_render`, but the existing deterministic Flyer smoke did not catch the exact-identity overlay failure.
- `src/agents/shift/scripts/shift-agent-deploy.sh` uses the Hermes venv for pre-install gates; the repo has no owned `pip install`/requirements path for `/usr/local/lib/hermes-agent/venv`, so this PR must not pretend it can safely mutate that external venv.
- `src/agents/flyer/render.py` says the module imports without Pillow and delegates to `/usr/bin/python3` where `python3-pil` can be installed, but `apply_exact_identity_overlay` currently lacks the same fallback.
- `src/agents/flyer/scripts/create-flyer-project` already accepts `--chat-id` and resolves the customer via `_find_customer_for_sender(...)`.
- `src/platform/schemas.py::FlyerProject` has no `customer_id` or `chat_id`, and `extra="forbid"` prevents state from carrying those fields ad hoc.
- `src/agents/flyer/recovery.py` suppresses customer ack when incident `chat_id` is missing or evidence is not strong.
- `src/agents/flyer/scripts/flyer-recovery-watchdog` emits typed audit rows for suppressed acks and operator action, but does not visibly alert the operator at those write sites.

## Scope

1. **Exact-identity overlay fallback and runtime gate**
   - Extend `apply_exact_identity_overlay` so it uses the same existing system-Pillow fallback contract as other render paths when Hermes-venv Pillow is absent.
   - Keep the failure fail-closed if neither Hermes-venv Pillow nor `/usr/bin/python3` system Pillow can render the overlay.
   - Add smoke verification that exercises exact-identity overlay end-to-end under the Hermes venv, proving the deployed path works without requiring `PIL` to import inside the Hermes venv.

2. **Correct failure classification**
   - Add `dependency_missing` to `FlyerManualReviewReason`.
   - Map deterministic import/dependency render failures such as `Pillow is required`, `ModuleNotFoundError`, or `No module named ...` to `dependency_missing`, not `provider_timeout`.
   - Keep provider/network failures and visual-QA failures on their existing reason codes.

3. **Persist customer origin on projects**
   - Add `customer_id: str = ""` and `chat_id: str = ""` to `FlyerProject`.
   - Populate both from the customer resolved in `create-flyer-project`; preserve backward compatibility for existing project rows.
   - Do not invent identity from message content. Use existing `FlyerCustomerStore.find_customer_by_sender`.

4. **Make recovery outcomes actionable**
   - Hydrate stale manual-review recovery signals from the persisted project `chat_id/customer_id` when available so legitimate customers are not suppressed solely because audit rows aged out.
   - At the write site for `flyer_recovery_customer_ack_suppressed` with `missing_strong_customer_origin_evidence`, and first transition to `flyer_recovery_operator_action_required`, call the existing owner alert chokepoint with a short plain-text operator message.
   - Dedupe alerts by state transition: `ack.status: none -> suppressed` alerts once; `incident.status: open -> operator_action_required` alerts once. Repeated watchdog scans must not re-alert for the same incident unless a later design explicitly adds a cooldown/repeat policy.
   - Add typed alert audit evidence for attempted/sent/failed owner alert delivery, or extend an existing Flyer recovery audit row only if schema review shows that is cleaner. Successful alerts must be visible in `decisions.log`, not only failures.
   - Cover deployed `worker_draft` mode, not only `customer_ack`, because F0105 reached `operator_action_required` from the worker-draft path.

5. **Post-deploy F0105 recovery runbook**
   - Add a runbook note for the operator-only post-merge/deploy step: after deploy, rerun or manually recover F0105, verify customer-visible outcome, or record an explicit operator handoff.
   - Keep the runbook separate from the PR's code path; this branch must not send to the customer from local/dev context.

6. **Product-stability follow-up record**
   - Add/update a scoped task note for the larger product direction: Hermes semantic brief contract, deterministic hard contracts, provider-class circuit breaker, and outcome dashboard.
   - Keep this PR focused on the reliability failures that blocked F0105 from becoming a customer-visible flyer.

## Non-Goals

- No merge or deploy without explicit operator authorization after the PR review.
- No live re-render or customer send from this PR branch.
- No ad hoc `pip install` into the Hermes venv unless a later drift check finds a repo-owned dependency install surface. This PR restores the existing system-Pillow fallback contract instead.
- No new Hermes provider/client, no new custom LLM classifier, and no replacement of the existing `flyer_semantic_brief` work.
- No broad ARE auto-PR/deploy promotion in this slice.
- No change to customer-facing copy unless required by new tests around recovery alerts.

## Verification Plan

- Unit/script tests for `dependency_missing` classification in `generate-flyer-concepts`.
- Project creation tests proving `customer_id/chat_id` persist and old rows still parse.
- Recovery tests proving stale manual project signals carry project `chat_id/customer_id` and no longer suppress solely due to missing origin when the project row has strong identity.
- Watchdog tests proving owner alert is invoked once for suppressed-ack/operator-action write sites, including deployed `worker_draft` escalation; dry-run does not alert or mutate.
- Tests proving exact identity overlay falls back to system Pillow when local `PIL` is unavailable, and classifies missing local+system Pillow as `dependency_missing`.
- Smoke/deploy syntax checks for shell scripts touched, plus a smoke path that exercises exact-identity overlay rather than only importing modules.
- Targeted pytest suite:
  - `tests/test_flyer_create_project.py`
  - `tests/test_flyer_generate_concepts.py`
  - `tests/test_flyer_recovery_watchdog.py`
  - schema/static tests around smoke/deploy if present
- `python -m py_compile` for touched Python scripts/modules.
- `git diff --check origin/main...HEAD`.

## Review Gates

- Plan review: two parallel agents, one structural/runtime-state lens and one Hermes-first/product-scope lens.
- Design review: two parallel agents, one code-path/testability lens and one silent-failure/operational lens.
- PR review: two parallel agents, one correctness/regression lens and one deployment/runtime-shape lens.

## Plan Review Fold-In

- Structural reviewer BLOCKER: no repo-owned Hermes-venv dependency install path exists. Resolution: do not add ad hoc `pip install`; repair the existing system-Pillow fallback contract in `render.py`.
- Structural reviewer MAJOR: exact identity overlay bypasses the fallback used by deterministic render. Resolution: scope item 1 now targets fallback parity and smokes the real overlay path.
- Structural reviewer MAJOR: owner alert success must be auditable. Resolution: design must add typed alert result evidence or a justified schema extension, not rely on `notify_owner_with_fallback` failure-only logs.
- Structural reviewer MAJOR: recovery tests must cover deployed `worker_draft` escalation. Resolution: verification plan explicitly covers operator-action transition in worker-draft mode.
- Hermes/product reviewer MAJOR: alerting must dedupe. Resolution: alert only on first state transition (`none -> suppressed`, `open -> operator_action_required`).
- Hermes/product reviewer MAJOR: F0105 itself needs an outcome step. Resolution: add post-deploy runbook acceptance step without sending from the PR branch.
