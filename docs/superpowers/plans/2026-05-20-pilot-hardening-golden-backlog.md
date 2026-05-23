# Pilot Hardening Golden Backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add redacted real Flyer Studio message shapes to the deterministic golden suite and reconcile stale pilot-hardening backlog entries without dashboard UI work.

**Architecture:** Keep runtime behavior unchanged unless tests expose a real gap. Store redacted live samples as test fixtures, load them from `tests/test_flyer_golden_scenarios.py`, and update `tasks/todo.md` plus a small mobile-app follow-up note with evidence.

**Tech Stack:** Python pytest fixtures, existing Flyer create-project harness, existing cf-router classifier helpers, Markdown task docs.

---

**Drift-check tag:** extends-Hermes

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and real customer transcript/state | yes - deployed Hermes gateway, `cf-router`, decisions log, and Flyer JSON state | sample/redact existing state; do not add ingestion substrate |
| Golden scenario execution | yes - existing deterministic `tests/test_flyer_golden_scenarios.py` harness | extend the harness and fixtures |
| Source-edit/manual queue routing | yes - existing Flyer project state, source-edit preflight, manual-review reason table, and cf-router status helpers | pin behavior with samples; no new workflow |
| CTA idempotency routing | yes - existing cf-router campaign CTA tests for new/in-progress/payment-pending/trial/active states | document remaining live resend/manual verification only unless a test fails |
| Mobile design disposition | none needed for this cleanup | create a follow-up note; do not implement mobile app or dashboard UI |

awesome-hermes-agent ecosystem check: not applicable to this cleanup beyond the existing Hermes/Flyer substrate; no external skill replaces redacting local live samples or reconciling repository backlog. Verdict: reuse existing Hermes/Flyer primitives and add only fixtures/docs.

## Drift Check

- Test work: read `tests/test_flyer_golden_scenarios.py`, `tests/test_flyer_golden_scenarios_real_model.py`, `tests/test_flyer_state_reply_table.py`, and `tests/test_flyer_project_isolation.py`.
- Backlog work: read `tasks/todo.md`, `backlog.md`, `tasks/lessons.md`, and `docs/hermes-alignment.md`.
- Runtime state sampled with two-step SSH redirect/read from `/opt/shift-agent/state/flyer/projects.json`, `/opt/shift-agent/state/flyer/customers.json`, and `/opt/shift-agent/logs/decisions.log`.
- Storage pattern remains unchanged: fixtures are static test data; production state stays JSON-on-disk with existing safe_io writers.

## Tasks

- [x] Add redacted live Flyer message-shape fixture data for source edit/co-owner, `any update?`, and short correction/approval/ack replies.
- [x] Extend the deterministic golden suite to load those fixture rows, including a red test that requires the harness to pass `--manual-edit-required` for source-edit samples.
- [x] Update backlog entries in `tasks/todo.md` so shipped/superseded F0012, adaptive language/mode, controlled direct generation, and F0023/F0024/F0029/edit-flow items are not misleading.
- [x] Verify CTA campaign resend state: keep the already-covered code paths as shipped and add the exact remaining manual resend instruction.
- [x] Add a mobile design follow-up note that classifies the untracked mobile-app design draft as separate from Cockpit P2-6 mobile emergency view.
- [x] Run focused pytest, relevant py_compile, and `git diff --check`.

## Review

- Review-fix note: PR review kept production-quality real-model smoke open as Session 3-owned/pending PR, removed brittle customer-copy assertions from the source-edit fixture, and added live-shape coverage that verifies `co-owner` consumes pending reference-authorization state.
- Red/green: initial `python -m pytest tests/test_flyer_golden_scenarios.py -q` failed on the new live source-edit sample because the harness did not pass `--manual-edit-required`; after wiring the fixture flag, the suite passed.
- Focused tests: `python -m pytest tests/test_flyer_golden_scenarios.py tests/test_flyer_state_reply_table.py tests/test_flyer_project_isolation.py tests/test_cf_router_flyer_routing.py -q` -> 187 passed.
- Syntax: `python -m py_compile tests\test_flyer_golden_scenarios.py src\plugins\cf-router\actions.py src\plugins\cf-router\hooks.py src\agents\flyer\workflow.py src\agents\flyer\scripts\create-flyer-project` -> passed.
- Whitespace: `git diff --check` -> passed.
