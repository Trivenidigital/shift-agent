**Drift-check tag:** extends-Hermes

# Flyer24 Batch Plan - Manual Queue Visibility (2026-05-26)

## Hermes-first checklist
1. Inbound WhatsApp/media ingestion -> **[Hermes]** existing gateway/cf-router substrate.
2. Source-edit/manual-review state persistence -> **[Hermes]** JSON state + safe IO + existing Flyer schemas/scripts.
3. Incident extraction from existing state/logs -> **[net-new]** read-only report shaping in `tools/flyer-self-evaluation.py`.
4. Operator-facing digest lines -> **[net-new]** read-only formatting in `tools/operator-brief.py`.
5. Regression protection -> **[net-new]** targeted pytest updates for reporting semantics.

Effort estimate covers only steps 3-5.

## Batch issue list (6 related fixes)
1. Manual queue line in operator brief labels all stale rows as `stale_source_edits`, mixing visual QA and other reasons.
2. Operator brief omits per-reason stale counts (`source_edit_provider_unavailable` vs `visual_qa_failed` etc.), hiding triage priority.
3. Operator brief does not show queued status mix (`queued` vs `in_progress`) for stale manual rows.
4. Self-eval stale-manual incidents do not expose `reason_family`, making brief-level aggregation brittle.
5. Self-eval stale source-edit incidents do not carry explicit `provider_config_gap` signal for missing credential/config cues.
6. No tests pin the expanded manual queue visibility contract in self-eval + operator brief.

## Scope guardrails
- No routing, account/quota, payment mutation, webhook, or provider API behavior changes.
- No customer-message copy changes in runtime handlers.
- Read-only reporting and tests only.
