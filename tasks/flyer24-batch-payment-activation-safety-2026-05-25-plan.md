# Flyer24 Batch Plan - Payment Activation Safety (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Receive guest-order checkout intent from WhatsApp/cf-router -> `[Hermes]`
2. Create pending guest order state row -> `[net-new: Flyer product state]`
3. Collect payment activation evidence (provider/reference/amount/currency) -> `[net-new: Flyer money gate contract]`
4. Validate idempotency and duplicate references safely -> `[net-new: Flyer money gate contract]`
5. Persist activation facts/audit-safe metadata in guest order -> `[net-new: Flyer product state]`
6. Send customer-facing payment/result copy -> `[Hermes substrate + Flyer copy policy]`

Net-new scope is only steps 2-5 plus copy correctness constraints. No custom payment API client and no live Stripe/Razorpay mutation.

## Batch issue list (6)
1. Activation dedupe currently ignores provider scope for duplicate references.
2. Paid-state idempotent replay accepts mismatched activation evidence.
3. Activation path does not verify payment amount against order amount.
4. Activation path does not verify payment currency against order currency.
5. Guest order state does not persist provider/amount/currency evidence.
6. Guest-order CLI cannot pass provider/amount/currency activation evidence.

## Root-cause evidence
- `src/agents/flyer/guest_order.py` activation currently takes only `payment_reference` and uses global duplicate-reference checks.
- Existing replay behavior for `status=paid` returns success without comparing incoming activation evidence.
- Schema fields exist for order amount/currency but activation does not enforce them.
- CLI `src/agents/flyer/scripts/manage-flyer-guest-order` does not accept provider/amount/currency flags.

## Implementation notes
- Add/extend tests first in `tests/test_flyer_guest_order.py` for each issue.
- Implement minimal logic in `guest_order.py`, CLI wiring, and schema fields in `src/platform/schemas.py`.
- Keep provider-neutral contract (`manual|stripe|razorpay|other`) and fail-closed behavior.

## Verification plan
- `python3 -m py_compile src/agents/flyer/guest_order.py src/agents/flyer/scripts/manage-flyer-guest-order src/platform/schemas.py`
- `pytest -q tests/test_flyer_guest_order.py`
- `git diff --check`

## Risk / merge policy
- Risk: money-adjacent runtime behavior change.
- Policy: open PR + self-review, leave open for operator review before merge/deploy.
