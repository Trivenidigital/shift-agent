# Flyer24 Batch Plan - Manual Queue Stale Incident Generalization (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Inbound capture/routing/audit substrate: **[Hermes]**
2. Flyer project/manual state persistence and queue statuses: **[Hermes + existing Flyer]**
3. Detect stale manual queue rows for operator visibility: **[net-new]** (Flyer self-eval policy in `tools/flyer-self-evaluation.py`)
4. Render grouped operator brief lines from self-eval incidents: **[net-new]** (Flyer ops reporting in `tools/operator-brief.py`)
5. Keep customer messaging/runtime behavior unchanged while improving triage/reporting: **[net-new]**

## Batch issues (6)
1. Self-eval stale detector only fires for `source_edit_provider_unavailable`, missing stale `visual_qa_failed`/`provider_timeout` manual queue pain.
2. Incident type `manual_source_edit_stale` is too narrow for generalized manual queue backlog visibility.
3. Incident evidence hardcodes `reason_code=source_edit_provider_unavailable` even when other stale reasons should be included.
4. Suggested action text is OpenRouter/source-edit specific and misleading for non-source-edit stale rows.
5. Operator brief stale summary only aggregates `manual_source_edit_stale`, so new/general stale incidents would be invisible.
6. Regression coverage is missing for generalized stale reason handling and brief aggregation compatibility.

## Implementation outline
- Add RED tests in `tests/test_flyer_self_evaluation.py` for stale incidents on non-source-edit manual reasons and reason-specific action text.
- Add RED tests in `tests/test_operator_brief.py` for stale summary aggregation that includes generalized manual stale incidents.
- Implement generalized stale incident emission in `tools/flyer-self-evaluation.py` with backward-compatible `manual_source_edit_stale` emission for source-edit reason.
- Update operator brief stale summary in `tools/operator-brief.py` to aggregate both old and new stale incident types.
- Run focused verification (`py_compile`, focused pytest, `git diff --check`).

## Risk
- Low to medium: read-only reporting and incident taxonomy changes; no customer routing/payment/account/quota/runtime state mutation.
