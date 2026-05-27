# Flyer24 Batch - Manual Queue Status Phrase Coverage (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Receive WhatsApp inbound text -> [Hermes]
2. Resolve sender + active project context -> [Hermes + existing Flyer state helpers]
3. Detect whether message is a status check vs revision edit -> [net-new: Flyer deterministic phrase policy]
4. Send customer-safe queue/status reply -> [Hermes transport, Flyer copy policy]
5. Emit audit intercept reason/details -> [Hermes/Flyer existing audit path]

Net-new scope in this batch: step 3 phrase coverage only, plus tests.

## Batch issue list (target 6)
1. `status for project F####` phrasing not explicitly recognized as status check.
2. `status of project F####` phrasing not explicitly recognized as status check.
3. `update on project F####` phrasing not explicitly recognized as status check.
4. `queue status for F####` phrasing not explicitly recognized as status check.
5. `progress on F####` phrasing not explicitly recognized as status check.
6. `where is update for F####` style phrasing not explicitly recognized as status check.

## Implementation
- Add RED unit tests in `tests/test_cf_router_plugin.py` for the above phrase families.
- Expand only `is_flyer_project_status_request()` regex coverage in `src/plugins/cf-router/actions.py`.
- Keep existing edit-intent guard intact to avoid misrouting true edit commands.

## Verification
- `pytest -q tests/test_cf_router_plugin.py -k flyer_project_status_request`
- `python3 -m py_compile src/plugins/cf-router/actions.py tests/test_cf_router_plugin.py`
- `git diff --check`

## Risk
- Low: deterministic phrase parsing only, no payment/account/quota mutations, no provider behavior changes.
