**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Status Phrase Gap Hardening (2026-05-27)

## Hermes-first checklist
1. Receive WhatsApp text inbound -> [Hermes]
2. Normalize sender identity/chat and route through cf-router -> [Hermes]
3. Detect status-check intent to avoid clarification/revision loops -> [net-new]
4. Emit deterministic Flyer status reply/audit -> [Hermes + existing Flyer handlers]
5. Persist account/project state -> [Hermes/Flyer existing]

Net-new scope in this batch: step 3 only (status phrase detection contract hardening).

## Batch issues (6 related)
1. `f0058 status?` is not detected as a status request.
2. `eta on my flyer` is not detected as a status request.
3. `where my flyer at` is not detected as a status request.
4. `did you complete it` is not detected as a status request.
5. `whats happening with my flyer` is not detected as a status request.
6. `what about my flyer` is not detected as a status request.

## Implementation
- Add RED tests in `tests/test_cf_router_flyer_routing.py` for these status-check phrasings.
- Patch `is_flyer_project_status_request` in `src/plugins/cf-router/actions.py` with narrowly scoped regex coverage.
- Keep edit-intent negatives intact (no broad overmatch).

## Verification
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_flyer_routing.py`
- `pytest -q tests/test_cf_router_flyer_routing.py -k "status_request_phrase_gaps or flyer_is_status_checkin"`
- `pytest -q tests/test_flyer_project_isolation.py -k "status_check"`
- `git diff --check`

## Risk
Low. No payment/account/quota mutation, no provider calls, no state schema changes. Routing intent detection only.
