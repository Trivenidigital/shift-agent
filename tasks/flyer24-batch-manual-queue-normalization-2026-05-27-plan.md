**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Manual Queue Normalization (2026-05-27)

## Hermes-first checklist
1. WhatsApp ingress, sender identity, and Flyer routing -> [Hermes]
2. Persisted Flyer project/manual-review state -> [Hermes]
3. Manual queue row shaping for Cockpit visibility -> [net-new]
4. Manual reason family/action hint normalization for operator triage -> [net-new]
5. Manual queue stale/status aggregation in triage summary -> [net-new]
6. Audit and delivery substrate -> [Hermes]

Effort estimate applies only to net-new steps 3-5.

## Batch issues (6)
1. `list_manual_queue` ignores manual rows when `manual_review.status` casing/spacing drifts from exact lowercase values.
2. `list_manual_queue` age calculation fallback omits `created_at` when `queued_at`/`updated_at` are absent.
3. `_reason_family` leaves known provider/manual reasons (`dependency_missing`) in `other` instead of triage-ready buckets.
4. `_reason_family` leaves `legacy_unknown` ungrouped, reducing backlog consistency.
5. `_operator_action_hint` lacks deterministic hints for `dependency_missing` and `legacy_unknown`.
6. `triage_summary` returns duplicated `groups` key in result payload construction (data-shape bug risk).

## Verification
- Add RED tests in `tests/test_flyer_manual_queue.py` first.
- Apply minimal fixes in `src/agents/flyer/manual_queue.py`.
- Run:
  - `python3 -m py_compile src/agents/flyer/manual_queue.py tests/test_flyer_manual_queue.py`
  - `pytest -q tests/test_flyer_manual_queue.py`
  - `git diff --check`

## Risk
Low. No payment, quota, campaign send, or provider mutation. Changes are limited to manual-queue visibility and triage shaping.
