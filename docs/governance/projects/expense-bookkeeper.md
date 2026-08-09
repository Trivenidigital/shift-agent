# Expense Bookkeeper — Project Directive

    Version: 1.1.0
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

**Accuracy note — this path is not Hermes-native.** `scripts/extract-receipt`
makes its own direct OpenRouter calls (`VISION_MODEL`, default
`openai/gpt-4o-mini`, against `https://openrouter.ai/api/v1/chat/completions`)
for both extraction and classification, rather than going through the Hermes
gateway. Describe the current shape honestly as **existing cognition path +
deterministic supervised ingestion**. Reuse it as-is: utility is the priority,
and a Hermes-native migration is a separate, non-blocking piece of work.

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
- auto-posting to QBO without owner confirmation, at any amount;
- exposing the mock QBO push as a customer-visible success;
- reusing `AWAITING_OWNER_APPROVAL`, `REJECTED`, `EXPIRED` or `EXTRACTING` to
  represent a review-only draft;
- routing owner media on anything but an explicit deterministic caption trigger
  — no intent classifier, and image-only intake stays unsupported for now;
- accepting receipts from a customer or employee sender.

## Authority tiers

Two tiers. Only the first is currently authorized, because
`RealQBOClient.__init__` raises `NotImplementedError` and `qbo_client_mode`
defaults to `mock` — so no supervised accounting write can actually occur, and
the existing pushed-confirmation template would falsely claim one if used.

**DRAFT — currently authorized.** Receipt extraction, classification and owner
review only. No approval action and no external accounting write. The lead
persists at `DRAFTED`, which mints no approval code, requests no approval and
emits no `expense_owner_approval_requested`. `DRAFTED` is deliberately distinct
from `AWAITING_OWNER_APPROVAL`: with no reachable approval path, the latter
would make the durable record claim an approval was requested, and would later
make the retention timer tell the owner an approval they never received had
expired. `DRAFTED` is terminal for this tier and retention-eligible, so review
receipts are pruned normally rather than becoming immortal.

**SUPERVISED — not authorized, not implemented.** Approval reply, lead
transition, QBO push and undo. Unavailable until a real QBO client exists with
onboarded credentials; nothing in this tier may be exercised through the mock.

## Required vertical E2E proof

**DRAFT tier (current):** a real owner WhatsApp receipt with an explicit
receipt/expense caption → bounded cf-router intake → existing extraction and
classification → durable `DRAFTED` expense → deterministic review-only card →
real owner-visible egress. **No QBO write is part of DRAFT completion**, and a
passing DRAFT proof must not be described as supervised action or as QuickBooks
integration being live.

**SUPERVISED tier (future):** a real receipt photo → approval card → owner
code → QBO entry → audit rows, with the undo path exercised.

## Escalation boundaries

This agent moves money into an accounting system. Amount, idempotency,
authorization and undo findings are BLOCKER-class. External-write credentials
and scopes are HIGH or above.

---

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-08-09 | Added the bounded DRAFT authority tier (extraction/classification/review only, `DRAFTED` state, no approval action, no external write) and recorded that supervised QBO action remains unavailable because `RealQBOClient` is unimplemented. Reachability is the cf-router owner-media arm gated on owner + media + explicit receipt caption. |
| 1.0.0 | 2026-08-01 | Initial Expense Bookkeeper directive. |
