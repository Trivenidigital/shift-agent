**Drift-check tag:** Hermes-native

# Hermes-first checklist
1. Inbound WhatsApp routing + sender normalization -> [Hermes]
2. Active Flyer project lookup and deterministic status reply path -> [Hermes]
3. Phrase-level status intent classification for Flyer customer copy -> [net-new]
4. SOURCE/NEW pending-choice status check-in handling -> [Hermes]
5. Audit + safe fail-closed behavior -> [Hermes]

Net-new scope in this batch: only step 3 phrase coverage and regression tests.

## Batch issues (related)
1. `what is the latest update` not recognized as Flyer status request.
2. `update on this flyer please` not recognized as Flyer status request.
3. `share status of flyer` not recognized as Flyer status request.
4. `status on my flyer please` not recognized as Flyer status request.
5. `status update on my flyer` not recognized as Flyer status request.
6. `give me an update on the flyer` not recognized as Flyer status request.

## Root cause hypothesis
`is_flyer_project_status_request()` has narrow phrase variants around `status on ...` and `update on ...` and misses common polite/request-wording forms used in customer follow-ups.

## Implementation
- Add failing tests for the six phrases in Flyer routing classifier coverage.
- Extend the status-request regex with constrained patterns for these phrasings.
- Keep edit-intent guard unchanged so edit requests remain non-status.

## Verification
- `python3 -m py_compile src/plugins/cf-router/actions.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "status_request_classifier_keeps_edits_separate or queued_source_edit_status_checkin_resends_source_new_clarification"`
- `git diff --check`
