# Flyer24 Batch Plan - Manual Queue SLA Reason Coverage (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Read Flyer projects/manual-review state -> `[Hermes]` existing JSON state + locking substrate.
2. Detect stale manual queue rows for operator paging -> `[net-new]` Flyer policy currently scoped too narrowly to one reason.
3. Notify operator and append audit row -> `[Hermes]` notify + audit append helpers already exist.
4. Show operator exactly what stalled and why -> `[net-new]` include reason-code scope in alert message/audit.
5. Keep behavior fail-closed/no customer mutation -> `[Hermes]` existing watchdog is read-only to customer/project lifecycle.

Net-new effort is only step 2 and step 4 policy hardening.

## Batch issue list (6)
1. SLA watchdog ignores `visual_qa_failed` rows even when stale.
2. Alert title/body are source-edit-only, hiding broader manual-queue risk.
3. Alert payload does not show reason coverage used for this run.
4. Audit row does not record reason-code scope, reducing triage traceability.
5. CLI has no override for reason-code scope during ops drills.
6. Tests pin source-edit-only behavior and miss mixed-reason stale queues.

## Planned changes
- Extend watchdog eligibility to a configurable allow-list of reason codes.
- Default allow-list: `source_edit_provider_unavailable`, `visual_qa_failed`.
- Add `--reason-codes` CLI flag (comma-separated) with validation and normalization.
- Update alert title/message to "manual queue" wording and list monitored reason codes.
- Extend `FlyerSourceEditSlaAlert` schema with `reason_codes` field for audit visibility.
- Expand watchdog tests to cover mixed reason-code alerts and CLI parsing behavior.

## Risk and merge posture
- Risk: low, read-only operator alerting and audit metadata only.
- Not money-adjacent; eligible for autonomous merge/deploy after review and green checks.
