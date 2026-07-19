# Deferred debt

## Dispatcher catering status-filter drift (PR-R1)

- **Deployed code authority:** ACTIONABLE allowlist — `cf-router actions.find_catering_lead_by_code` (status ∈ {AWAITING_OWNER_APPROVAL, CUSTOMER_FINALIZED, OWNER_EDITED, OWNER_APPROVED}).
- **SKILL prose:** denylist wording — `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` pool-lookup section (`status != CLOSED and != OWNER_REJECTED and != STALE`).
- **Current behavioral authority:** the deployed code (cf-router F8 + the `approval_code_pools` resolve adapter), parity-pinned in `tests/test_routing_invariants_r1.py`.
- **Intended reconciliation:** doc-only SKILL prose fix (make the prose match the deployed ACTIONABLE allowlist).
- **Owner:** operator-approved future docs PR.
- **Gate:** must keep the PR-R1 prose-mirror ORDER test green (`tests/test_approval_code_pool_invariants.py::test_skill_md_pool_order_and_membership_match_registry`).
