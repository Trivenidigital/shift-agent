**Drift-check tag:** extends-Hermes

# Hermes-first checklist
1. Build self-evaluation report from Flyer state + decisions log `[Hermes]`
2. Render JSON/Markdown report output `[Hermes]`
3. Assert CLI contract in deterministic test suite `[net-new]`
4. Keep assertions stable as rollout/readiness incidents evolve `[net-new]`
5. Verify no runtime/customer mutation in this batch `[Hermes]`

Net-new scope only: harden `tests/test_flyer_self_evaluation.py` CLI assertions so they validate stable invariants instead of brittle exact incident totals.

Batch issues (6):
1. Brittle exact `incident_count == 1` assertion fails as incident taxonomy expands.
2. No invariant check that `summary.incident_count` matches `len(incidents)`.
3. No check that manual queue stale incident remains surfaced in JSON.
4. No check that markdown includes rollout section (now part of contract).
5. No check that output path parent creation remains functional when nested.
6. No check that customer-copy scanner advisory remains present after report growth.
