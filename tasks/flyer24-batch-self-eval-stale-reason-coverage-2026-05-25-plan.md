# Flyer24 Batch Plan - Self-Eval Stale Manual Queue Reason Coverage (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. WhatsApp ingress/routing/audit substrate: **[Hermes]**.
2. Flyer project/manual-review JSON state persistence: **[Hermes + existing Flyer]**.
3. Detect stale manual queue risk for self-eval incidents: **[net-new]** (Flyer reporting policy in `tools/flyer-self-evaluation.py`).
4. Rollout/report wording for stale manual queue risk: **[net-new]** (read-only operator reporting policy).
5. Keep customer behavior unchanged (no routing/sends/payment/account mutation): **[Hermes]**.

Net-new scope is only steps 3-4.

## Batch issue list (6)
1. Self-eval stale detector ignores stale `missing_required_facts` manual queue rows.
2. Self-eval stale detector ignores stale `reference_provider_unavailable` rows.
3. Self-eval stale detector ignores stale `reference_low_confidence` rows.
4. Self-eval stale detector ignores stale `reference_unsupported` rows.
5. Generic stale suggested-action text is hardcoded to provider timeout wording, which is misleading for non-timeout reasons.
6. CLI help still says the rollout threshold is only for `manual_source_edit_stale`, while rollout already covers generalized manual stale incidents.

## Planned implementation
- Expand stale-manual reason allow-list to include manual queue reasons that block customer delivery (`missing_required_facts`, `reference_provider_unavailable`, `reference_low_confidence`, `reference_unsupported`).
- Keep backward-compatible incident taxonomy:
  - `manual_source_edit_stale` remains only for `source_edit_provider_unavailable`.
  - all other stale reasons emit `manual_review_stale` with reason-aware suggested action.
- Add focused tests in `tests/test_flyer_self_evaluation.py` for each added reason and action wording.
- Update CLI help string in `tools/flyer-self-evaluation.py` to describe generalized stale manual queue threshold semantics.

## Risk / merge posture
- Risk: low (read-only self-eval/reporting behavior; no customer/runtime/payment/account mutations).
- Merge policy: merge/deploy eligible autonomously after review and green checks.
