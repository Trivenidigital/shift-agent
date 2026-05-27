**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Manual Queue Signal Normalization (2026-05-27)

## Hermes-first checklist
1. Receive WhatsApp message and sender context -> [Hermes]
2. Route into Flyer cf-router intercepts -> [Hermes]
3. Detect whether generation/manual queue state already exists -> [net-new]
4. Choose fail-closed customer ack (manual queue vs intake/status) -> [net-new]
5. Emit audit reason/detail for operator visibility -> [Hermes]
6. Persist project/manual state mutations via existing scripts -> [Hermes]

Net-new scope only: normalize manual-queue status/reason/detail signals in Flyer policy helpers so customer copy and routing remain fail-closed and deterministic when upstream strings vary in casing/spacing/variant formats.

## Batch issues (target 5-6)
1. `flyer_project_has_manual_review_queued` requires exact `manual_review.status == "queued"`; misses case/whitespace variants.
2. Same helper ignores `in_progress` queue states, even though other code treats `in_progress` as queued-manual signal.
3. `flyer_generation_queued_manual_review` matches `reason_code=source_edit_provider_unavailable` only; misses other reason codes present in detail (`visual_qa_failed`, `reference_unsupported`, etc.).
4. Same parser misses `manual_review.status` patterns when spaces surround `=`.
5. Same parser misses JSON-ish variants (`"manual_review": {"status": "queued"}`) returned by script output/error payloads.
6. Queue/manual signal parsing is duplicated in ad-hoc patterns; centralize around normalization helper for consistency and easier test coverage.

## Verification
- Add failing tests first in `tests/test_flyer_source_edit_preflight.py` and/or `tests/test_cf_router_flyer_routing.py` for each variant.
- Implement minimal helper changes in `src/plugins/cf-router/actions.py`.
- Run focused pytest for touched behavior.
- Run `python3 -m py_compile` on touched files.
- Run `git diff --check`.

## Risk
Low. No payment/account/quota mutation. Behavior change is confined to fail-closed/manual-queue detection and customer ack selection when generation already queued manual review.
