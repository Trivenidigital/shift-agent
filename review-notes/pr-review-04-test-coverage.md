# PR Review 4/5 — Test Coverage (pr-review-toolkit:pr-test-analyzer)

**Verdict:** Smoke test covers "it starts," not "it works correctly under stress." Not sufficient for customer-bound production.

## Proposed minimum viable test suite (~6-8h)

```
tests/
  test_schemas.py                  (~1h) — LEGAL_TRANSITIONS graph properties + code regex drift
  test_send_coverage_message.py    (~2h) — 6 scenarios, mocked bridge
  test_update_proposal_status.py   (~1h) — legal/illegal transitions + field stripping
  test_reconciler.py               (~1h) — 3-way decision with synthetic decisions.log
  test_render_template.py          (~30m) — sanitize_reason_short injection cases
  test_safe_io.py                  (~1h) — FileLock concurrency, atomic write crash, corrupt recovery
  test_e2e_happy_path.py           (~1h) — chain create → approve → send → accept
```

## Highest-consequence tests

1. **`send-coverage-message` (10/10)** — 6 scenarios mocked bridge:
   - approved → reconciling → sent (happy)
   - cap exceeded → revert, counter unchanged
   - bridge POST fails twice → send_failed + refund
   - disabled.flag present → refused
   - OutboundAttempted logged BEFORE POST (idempotency contract)
   - candidate inactive → revert

2. **`update-proposal-status` + `LEGAL_TRANSITIONS` (10/10)** — property test: every legal edge succeeds, every illegal edge returns EXIT_ILLEGAL_TRANSITION; terminals have zero outgoing.

3. **`reconcile.py` (9/10)** — golden-file test with synthetic decisions.log covering all three decision paths.

4. **`render-coverage-template` (8/10)** — sanitizer injection cases: `{`, `}`, backticks, newlines, `$()`, emoji-unicode.

## Invariants NOT caught by Pydantic

- `LEGAL_TRANSITIONS` completeness (typo dropping a status → system silently refuses all transitions)
- Code alphabet/regex agreement between `schemas.py:230` and `create-proposal:60`
- `Roster.find_by_phone` `or True` bug (now fixed in commit f1806f0 but test would prevent regression)

## Components requiring live integration (no unit test possible)

1. Bridge `/send` endpoint — staging canary with second WA number
2. Pushover — staging real alert before go-live
3. WhatsApp inbound delivery — golden-file parser test + 24h post-deploy canary

## Rollout recommendation

- Run proposed tests 1-4 (~6h)
- Manual E2E-1 on staging VPS with 2 real WA numbers before customer pair
- First 48h production: cap `max_outbound_per_day: 2` (safety net)
- Owner pre-subscribed to Pushover high-priority

**Current state (commit f1806f0):** no automated test suite. Relying on smoke-test + staging canary + in-production cap.
