**Drift-check tag:** extends-Hermes

# Flyer Manual-Review Detection

## New primitives introduced

- One small structured-detail parser inside the existing cf-router Flyer action helper.
- No new routing, messaging, audit, state, queue, provider, or approval substrate.

## Hermes-first analysis

Hermes already owns WhatsApp ingress, identity, runtime orchestration, bridge delivery, and audit conventions. Flyer code already owns generation subprocess handling and deterministic customer updates. This slice only corrects how cf-router interprets the existing `generate-flyer-concepts` result payload.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Subprocess result interpretation | none in Hermes Skills Hub (`https://hermes-agent.nousresearch.com/docs/skills`) | Keep in cf-router helper; this is Flyer-specific result parsing. |
| Customer messaging | Existing deterministic Flyer acks already exist | Reuse current manual-review ack; do not add prose or a new message path. |
| Manual queue state | Existing Flyer project/manual_review state | Recognize existing reason code only; no schema change. |

Awesome Hermes Agent ecosystem check: reviewed `https://github.com/0xNyk/awesome-hermes-agent`; no Flyer-specific generation-result parser applies.

## Drift check

Read first:

- `src/agents/flyer/scripts/generate-flyer-concepts` emits JSON containing `manual_review_reason_code` for source-edit and draft-generation failures.
- `src/platform/schemas.py` defines `FlyerManualReviewReason` values including `provider_timeout` and `dependency_missing`.
- `src/plugins/cf-router/actions.py::flyer_generation_queued_manual_review()` recognizes a few string patterns but misses structured `manual_review_reason_code` for several valid reasons.
- `src/plugins/cf-router/hooks.py` uses this helper to decide whether to send the deterministic manual-review customer update.

## Problem

When generation has already persisted `manual_edit_required` with a manual-review reason such as `provider_timeout` or `dependency_missing`, cf-router can misread the subprocess detail as "not queued" and suppress or downgrade the customer update. That creates stale/generic status despite the queue state being correctly written.

## Plan

- [x] Add RED tests for structured JSON `manual_review_reason_code=provider_timeout` and `dependency_missing`.
- [x] Add RED test for `exit=N {json}` wrapper from `trigger_generate_flyer_concepts`.
- [x] Implement a small parser that extracts JSON detail and recognizes any valid `FlyerManualReviewReason` except non-queued/no-op values.
- [x] Keep negative arbitrary JSON detail returning false.
- [x] Add reviewer-driven safeguards: terminal nested `manual_review.status` does not count as queued; terminal nested status overrides top-level reason code; reason codes stay synced with `FlyerManualReviewReason`.
- [x] Run focused tests and multi-vector review.
- [x] Run broad/full verification before PR.
- [ ] PR, merge, deploy.

## Verification

- RED: `python -m pytest tests/test_flyer_source_edit_preflight.py::test_generation_detail_with_json_provider_timeout_reason_counts_as_manual_review tests/test_flyer_source_edit_preflight.py::test_generation_detail_with_exit_wrapped_dependency_reason_counts_as_manual_review tests/test_flyer_source_edit_preflight.py::test_generation_detail_with_unrelated_json_does_not_count_as_manual_review -q` -> 2 failed, 1 passed.
- GREEN focused after parser: same command -> 3 passed.
- RED reviewer safeguards: terminal nested manual status still returned true; terminal nested status with top-level reason code still returned true; schema-sync helper missing.
- GREEN safeguards: 6 targeted tests -> 6 passed.
- GREEN focused suite: `python -m pytest tests/test_flyer_source_edit_preflight.py -q` -> 26 passed.
- GREEN focused suite after routing-review fix: `python -m pytest tests/test_flyer_source_edit_preflight.py -q` -> 27 passed.
- GREEN routing suite: `python -m pytest tests/test_cf_router_flyer_routing.py -q` -> 315 passed.
- GREEN whitespace: `git diff --check`.
- GREEN full suite: `python -m pytest -q` -> 2812 passed, 867 skipped, 40 warnings.

## Review result

- Claude Hermes/drift review: no blocking findings; verified schema-derived reason code path; requested this plan-doc update.
- Routing/customer-safety review: one Medium finding; fixed with terminal nested manual status overriding top-level `manual_review_reason_code`.
