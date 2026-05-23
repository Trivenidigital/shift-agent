**Drift-check tag:** extends-Hermes

**New primitives introduced:** none

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Draft flyer concept rendering | In-tree `src/agents/flyer/scripts/generate-flyer-concepts` + `src/agents/flyer/render.py` | Extend existing with typed failure handling |
| Manual review triage | In-tree `FlyerManualReview` + `FlyerManualReviewReason` | Reuse existing reason codes |
| Customer messaging | In-tree `cf-router` + `agents.flyer.workflow` status copy tables | Reuse existing (no copy changes required) |

Verdict: keep all changes inside existing flyer scripts/state transitions; no new Hermes skills or external services.

## Problem

When the draft (non-source-edit) render path errors, we need deterministic recovery:
- Persist `manual_edit_required` + `manual_review` row so operators can triage.
- Avoid “silent stalls” caused by unhandled exceptions mid-script.
- Retry once when failure is plausibly transient, but do not delay manual review for deterministic failures.

## Proposed behavior

### 1) Catch draft render errors and persist manual review

In `generate-flyer-concepts` draft branch (the `render_concept_previews(...)` path):
- Wrap render in `try/except (FlyerRenderError, Exception)` so we never stall silently.
- Map exception text to reason_code:
  - `visual_qa_failed` if the error message indicates a quality check failure.
  - otherwise `provider_timeout` (conservative default for transient/provider failures).
- Persist state under the existing state lock:
  - set `status="manual_edit_required"` only if the project is in an early/render stage (avoid regressing later states)
  - `manual_review=make_manual_review(reason_code=..., detail=..., queued_at=now)`
  - keep existing fields intact (do not mutate revisions/pending confirmation).
- Return RC=2 and print JSON containing `manual_review_reason_code` for the caller.

### 2) One safe retry

Retry policy:
- Only retry when error text matches a conservative allowlist:
  - contains `timeout`, `timed out`, `502`, `503`, `bad gateway`, `connection reset`
- Retry count: 1 (two total attempts).
- No sleep/backoff for now (keeps wall time bounded).
- If retry succeeds, continue normal success path.
- If retry fails, queue manual review as above.

### 3) Idempotency

If the project already has `manual_review.status="queued"`:
- Do not overwrite it on subsequent failures (avoid downgrading a more specific reason/detail).
- Still return RC=2 and print JSON, but keep stored manual_review unchanged.

## Manual-review detail format (bounded, operator-friendly)

`FlyerManualReview.detail` remains a max-500 string. Encode a compact JSON string:

- `stage`: `draft_render_concept_previews`
- `attempts`: `1` or `2`
- `retryable_match`: `true/false`
- `error_class`: exception class name
- `error`: truncated message (no secrets)

## Tests

Add/extend `tests/test_flyer_generate_concepts.py`:
- Non-source-edit path: stub `render_concept_previews` to raise `FlyerRenderError("HTTP 502 ...")` → manual review queued, reason_code `provider_timeout`.
- Non-source-edit path: stub `render_concept_previews` to raise `FlyerRenderError("failed quality check ...")` → manual review queued, reason_code `visual_qa_failed`.
- Retry: first raise transient then return 1 spec → script succeeds and status advances; verify retry count = 2 calls.
- Idempotency: pre-seed project with queued manual_review; render raises again → manual_review preserved.
