# Expense Bookkeeper — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: expense-bookkeeper
    Lifecycle: pilot (gated by `cfg.expense_bookkeeper.enabled`)
    Supplements: docs/governance/engineering-directive.md

Governs `src/agents/expense_bookkeeper/**` and `src/platform/qbo_client.py`.

---

## Purpose

Owner sends a receipt photo on WhatsApp → extract → classify → owner approves
with a `#XXXXX` code → write the expense to QuickBooks Online. Receipts are
pruned on a retention timer.

## Hermes capability — reuse

Vision extraction from the receipt image, vendor/category classification, and
the wording of the approval card and owner replies. Deployed in
`skills/parse_receipt_photo/`, `skills/expense_bookkeeper_dispatcher/`,
`skills/handle_expense_owner_approval/` and `scripts/extract-receipt` (which
mirrors Catering's `parse-menu-photo` vision-call shape).

Do not add an OCR layer, a receipt parser, or a vendor classifier alongside
this.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Owner identity + authorization | shared `identify-sender` / `validate-sender-block` |
| Approval codes + `undo E####` | shared `approval_code_pools.py`, dispatcher matrix |
| Decision application | `scripts/apply-expense-decision` |
| QBO write | `src/platform/qbo_client.py` |
| Retention / pruning | `scripts/prune-and-expire-expenses.py` + timer |
| Persistence + locking | `safe_io.py` |
| Audit | `log-decision-direct` |

## Decision boundary

**May be probabilistic:** what the receipt says, which expense category it
looks like, how the approval card reads.

**Must remain deterministic:** the amount written to QBO, whether the sender is
the owner, whether the approval code is valid, whether the QBO write already
happened (idempotency), the undo window, and audit.

**Deployed invariant — do not weaken:** the *owner-confirmed* total is the
source of truth for the QBO push; the extracted total is advisory only and is
surfaced for verification. This defends against prompt injection in receipt
text. A change that pushes an extracted amount without owner confirmation is a
BLOCKER.

## Presumed NO-GO

- a second receipt-ingestion or OCR pipeline;
- an expense-local approval mechanism outside the shared code pool;
- a parallel expense store;
- a second QBO client or write path;
- auto-posting to QBO without owner confirmation, at any amount.

## Required vertical E2E proof

A real receipt photo → approval card → owner code → QBO entry → audit rows,
with the undo path exercised.

## Escalation boundaries

This agent moves money into an accounting system. Amount, idempotency,
authorization and undo findings are BLOCKER-class. External-write credentials
and scopes are HIGH or above.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Expense Bookkeeper directive. |
