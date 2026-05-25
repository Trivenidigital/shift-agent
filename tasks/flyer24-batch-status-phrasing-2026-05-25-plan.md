**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Status Check Phrasing Hardening (2026-05-25)

## Hermes-first checklist
1. Receive WhatsApp text -> classify intent boundary: **[Hermes]** (gateway ingress, sender block, hook dispatch already live)
2. Determine whether text is Flyer status check vs revision/new project: **[net-new]** (Flyer product policy regex in `src/plugins/cf-router/actions.py`)
3. Route status checks to deterministic project/manual queue reply: **[Hermes + Flyer]** (Hermes dispatch substrate; Flyer route choice)
4. Emit audit and customer reply: **[Hermes]** (existing audit/send primitives)
5. Regression-proof transcript phrases in tests: **[net-new]** (Flyer tests)

Net-new scope is only step 2 + 5.

## Batch issue list (6 related misses)
1. `what's the update on flyer` is not classified as status check.
2. `did you finish the flyer` is not classified as status check.
3. `can I get an update` is not classified as status check.
4. `update on F1234` is not classified as status check.
5. `status for F1234` is not classified as status check.
6. `is the update ready` is not classified as status check.

## Root-cause hypothesis
`is_flyer_project_status_request()` has narrow phrase forms (`any update`, `what is the status`, `is it ready`) but misses common operator/customer variants using `get`, `for <project_id>`, `finish`, and `update ready` phrasing.

## Implementation steps
1. Add RED coverage in `tests/test_flyer_state_reply_table.py` for the six misses.
2. Expand `is_flyer_project_status_request()` phrase patterns in `src/plugins/cf-router/actions.py` without broadening into edit instructions.
3. Re-run targeted Flyer tests and sanity checks (`py_compile`, `git diff --check`).

## Risk
Low. Read-only classifier broadening for status wording; no payment/account/quota mutation, no provider calls, no deploy-side config.
