**Drift-check tag:** extends-Hermes

**New primitives introduced:** none (prefer existing flyer workflow primitives + Hermes substrate)

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp inbound routing + sender identity | In-tree `cf-router` + Hermes dispatcher | Use existing |
| Revision intent extraction (text → structured change) | In-tree `src/agents/flyer/workflow.py` | Extend existing (no new service) |
| Approval workflow (APPLY/APPROVE) | In-tree pending confirmation (`update-flyer-project`) | Use existing |
| OCR/vision to anchor visible text | None in-tree for “current preview OCR inventory” | Defer (would be net-new) |
| Manual review queue + reason codes | In-tree `FlyerManualReview` + reason-code tables | Use existing |

Awesome-Hermes-Agent ecosystem check: not required for this scope (no new external integrations); keep changes inside existing Flyer Studio primitives.

## Problem statement

We have a customer rollout in 2 days. The system must be resilient to vague WhatsApp revisions and must not “stall silently”.

Observed failure modes:
1. Draft (non-source-edit) concept generation can fail (provider/network/5xx/timeout) and leave the project without a typed `manual_review` reason, causing “I couldn’t finish this automatically…” with weak operator triage and unclear recovery.

## Scope (safe category)

Rollout-critical stabilization in Flyer Studio only:
- Ensure draft concept generation failures are caught, typed, and persisted as `manual_edit_required` with a meaningful `reason_code` and detail.
- Add one safe retry before queueing manual review when the failure looks transient (explicitly defined below).
- Add regression tests to pin the behavior.

Non-goals:
- No new provider/model posture changes.
- No deploy scripts, VPS mutation, or runtime config changes.
- No broad cf-router routing rewrites.
- No OCR/vision inventory of previews (net-new surface; defer).

## Acceptance criteria

- When draft concept rendering fails, the project persists as:
  - `status=manual_edit_required`
  - `manual_review.status=queued`
  - `manual_review.reason_code` is one of: `provider_timeout` or `visual_qa_failed` (both already exist in `src/platform/schemas.py::FlyerManualReviewReason`)
  - `manual_review.detail` includes an actionable short error summary
- A transient provider failure gets exactly one retry before manual review:
  - retry allowed only for timeout/5xx/connection reset style errors (string-matched, conservative allowlist)
  - retry forbidden for deterministic quality/validation errors
- Tests cover both success and failure paths without real provider calls.
- Idempotency: if a project already has a queued `manual_review`, a subsequent failure must not duplicate/overwrite it with less-informative data.

## Test matrix

- Unit-style script test:
  - Non-source-edit render path throws `FlyerRenderError("HTTP 502 ...")` → `provider_timeout` manual review queued.
  - Non-source-edit render path throws `FlyerRenderError("failed quality check ...")` → `visual_qa_failed` manual review queued.
  - Retry: first attempt fails transient, second succeeds → no manual review; project advances normally.
  - Retry is NOT attempted on non-transient errors; manual review is queued immediately.
  - Manual review already queued → second failure does not create duplicates or downgrade reason/detail.
