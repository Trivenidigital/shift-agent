# Flyer Recovery Resolved Incidents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Drift-check tag:** extends-Hermes

**Goal:** Close Flyer recovery incidents when a customer-visible repair or delivery has already succeeded, so the autonomous repair queue does not keep reprocessing stale failures.

**Architecture:** Reuse existing `decisions.log`, `recovery_incidents.json`, `safe_io` locking, and typed Flyer recovery audit variants. Add a deterministic resolver that matches successful customer-visible audit rows to older open incidents by hashed chat or project ID, then emits `flyer_recovery_resolved`.

**Tech Stack:** Python, Pydantic v2 schemas, JSON-on-disk state, NDJSON audit log, pytest.

---

## Hermes-First Analysis

| Domain | Hermes-owned? | Decision |
|---|---|---|
| WhatsApp delivery evidence | yes | Reuse existing Flyer/Hermes delivery audit rows such as `flyer_assets_delivered` and recovery outcome repair rows. |
| Audit emission | yes-ish | Reuse existing `decisions.log`, `safe_io.ndjson_append`, and `LogEntry` typed variants. |
| Recovery state | Flyer-specific on Hermes state | Reuse `recovery_incidents.json` and `recovery_state_lock`; do not add a new store. |
| Customer repair policy | Flyer-specific | Add only the policy for when a visible success closes an incident. |
| Code repair worker | existing recovery substrate | No change to worker runner, provider routing, PR automation, or deploy policy. |

## Acceptance

- [x] A successful `flyer_recovery_outcome_repaired` row resolves older open incidents for the same `chat_id_hash`.
- [x] A successful `flyer_assets_delivered` row resolves older open incidents for the same `project_id`.
- [x] Newer incidents in the same chat/project stay open.
- [x] Different-chat/different-project incidents stay open.
- [x] Resolver emits typed `flyer_recovery_resolved` audit rows.
- [x] No live WhatsApp sends in tests.
- [x] No provider routing, source-edit provider, or deploy policy changes.
