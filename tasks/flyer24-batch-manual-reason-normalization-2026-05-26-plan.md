**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan — Manual Reason Normalization (2026-05-26)

## Hermes-first checklist
1. Receive customer status check and project state lookup — `[Hermes]` ingress/identity/routing substrate already exists.
2. Build customer status reply from Flyer project/manual-review state — `[net-new]` Flyer policy logic.
3. Group manual queue rows by reason family/hints in operator surfaces — `[net-new]` Flyer policy logic.
4. Persist/audit unchanged state transitions — `[Hermes]` existing safe_io/state/audit substrate.

Net-new scope only: normalize manual-review reason codes/casing/whitespace and fail-closed fallback behavior in Flyer policy helpers, with regression tests.

## Batch issue list (6 related)
1. `build_project_status_reply` treats reason codes case-sensitively (`VISUAL_QA_FAILED` misses mapped copy).
2. `build_project_status_reply` does not trim + lowercase consistently for `closed_no_send` reason mapping.
3. `flyer_manual_edit_status_reply` defaults unknown reason codes to source-edit copy instead of unclassified generic manual-review copy.
4. `flyer_manual_edit_status_reply` does not normalize reason-code casing/whitespace before table lookup.
5. Manual-queue triage helpers `_reason_family` and `_operator_action_hint` are case-sensitive and misclassify legacy mixed-case reason codes.
6. Regression coverage is missing for mixed-case/whitespace/unknown reason-code variants across workflow/actions/manual-queue helpers.

## Safety
- No payment/provider mutation.
- No runtime send behavior changes beyond deterministic status-copy selection.
- No schema/state format change.
