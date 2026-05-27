# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T08:30:00Z

## Current batch
- Branch: `codex/flyer24-batch-manual-queue-signal-normalization-202605270824`
- Scope: harden manual-queue signal parsing so fail-closed/manual-review acknowledgements trigger consistently across status/reason formatting variants.
- Root-cause evidence:
  - `test_generation_detail_with_reference_unsupported_reason_code_counts_as_manual_review` failed because `flyer_generation_queued_manual_review` only recognized `source_edit_provider_unavailable` reason-code text.
  - `test_generation_detail_with_spaced_manual_review_status_counts_as_manual_review` failed because parser required exact `manual_review.status=queued` without spaces.
  - `test_project_has_manual_review_queued_normalizes_status_and_accepts_in_progress` failed because helper required exact lowercase `queued` and ignored `in_progress`.
- Risk: low (no payment/account/quota mutation; only manual-queue detection and acknowledgement routing predicates).
- Hermes/MCP-first: Hermes continues to own ingress/routing/audit substrate. Net-new is Flyer policy normalization for deterministic fail-closed customer/operator paths.

## Batch issue list
1. Normalize `manual_review.status` casing/whitespace in `flyer_project_has_manual_review_queued`.
2. Treat `manual_review.status=in_progress` as queued-manual signal where helper is used.
3. Accept spaced `reason_code = ...` variants in generation failure detail parsing.
4. Accept non-source-edit manual reason codes (`visual_qa_failed`, `reference_unsupported`, `reference_provider_unavailable`, `source_edit_generation_failed`) in manual-queue detection.
5. Accept spaced `manual_review.status = queued|in_progress` variants in generation detail.
6. Accept JSON-like `"manual_review": {"status": "queued|in_progress"}` detail payloads.

## PR queue classification refresh
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - operator-review-required (money-adjacent).

## Running PR list (hackathon)
- #292 `fix(flyer): re-land payment contract fail-closed checks and MCP readiness` - open; operator-review-required.

## Verification for this batch
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_flyer_source_edit_preflight.py -k "generation_detail_with_spaced_reason_code or generation_detail_with_reference_unsupported or generation_detail_with_spaced_manual_review_status or generation_detail_with_json_manual_review_status or project_has_manual_review_queued_normalizes_status"` (pass: 5)
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_flyer_source_edit_preflight.py` (pass: 21)
- `/opt/codex-flyer-autodev-venv/bin/python -m pytest -q tests/test_cf_router_flyer_routing.py -k "manual_review_status_normalizes_source_edit_reason_code or source_edit_status_check_uses_general_status_for_non_source_edit_reason"` (pass: 2)
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_flyer_source_edit_preflight.py`
- `git diff --check`
