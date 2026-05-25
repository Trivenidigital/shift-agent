# Flyer Recovery Evidence Escalation Plan

**Drift-check tag:** extends-Hermes

**Goal:** Make the Flyer autonomous repair engine fail forward instead of leaving unrepaired incidents open forever. If customer-visible success evidence exists, resolve the incident. If the worker/repair lane has already completed but no safe customer-visible success evidence exists, mark the incident as operator-action-required with a typed audit row and summary count.

## Hermes-First Analysis

| Domain | Hermes-owned? | Decision |
|---|---|---|
| WhatsApp sender identity/chat routing | yes | Reuse existing cf-router and recovery audit rows; no identity changes. |
| Inbound/outbound audit | yes-ish | Reuse `decisions.log`, `safe_io.ndjson_append`, and `LogEntry` typed variants. |
| Recovery state storage | Flyer-specific on Hermes state | Reuse `recovery_incidents.json` and `recovery_state_lock`; no new datastore. |
| Customer-visible success evidence | Flyer-specific policy | Continue using `flyer_assets_delivered`, `flyer_closure_customer_notified`, and scoped `flyer_recovery_outcome_repaired`. |
| No-evidence escalation | Flyer-specific policy | Add deterministic operator-action-required transition; do not auto-close as success. |
| External provider/code hotfix automation | out of scope | Do not change provider routing, source-edit policy, or deploy automation. |

Hermes ecosystem check: Hermes Skills Hub has general agent/devops/codex skills, but no Flyer-specific recovery incident closure contract; build narrowly on the existing Hermes/Flyer substrate.

## Failure Analysis

- PR #243 safely resolves incidents with customer-visible success evidence.
- Remaining failure: incidents whose worker lane completed, but no matching customer-visible success row exists, can stay `open`.
- That `open` state is misleading: the engine has already exhausted the bounded repair lane and cannot prove the customer was satisfied.
- The correct lifecycle state is not `resolved`; it is `operator_action_required`, with a durable audit row and summary count.

## Acceptance

- [x] A stale open incident with `codex.status=completed` and no customer-visible success is marked `operator_action_required`.
- [x] The watchdog emits a typed `flyer_recovery_operator_action_required` audit row once.
- [x] Dry-run reports the count but does not mutate state or write audit.
- [x] Non-stale incidents stay open.
- [x] Incidents with customer-visible success still resolve before escalation.
- [x] Repair queue skips operator-action-required incidents.
- [x] Self-eval surfaces operator-action-required recovery incidents as active customer risk.
- [x] No WhatsApp sends in tests.
- [x] No provider routing/source-edit/deploy policy changes.
