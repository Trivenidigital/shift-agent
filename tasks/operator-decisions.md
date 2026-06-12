# Operator Decisions And Handoffs

**Drift-check tag:** extends-Hermes

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Daily reminders / chat surface | yes - Hermes gateway, cron, WhatsApp/Telegram delivery already operate in this repo | Use Hermes as the readout surface; do not build a new notification substrate in v1. |
| Fleet state | yes - `tools/hermes-fleet-upgrade.py` already emits fleet reports | Consume existing report output instead of probing VPSes again. |
| Task memory | yes - repo-backed `tasks/todo.md` and runbooks already hold canonical work state | Keep canonical truth in repo docs; this file only indexes decisions/blockers for summaries. |
| Durable storage | yes - git-backed Markdown is already the project convention for task docs | Use Markdown, no database. |

Awesome Hermes Agent ecosystem check: no dedicated operator-control-room skill is needed for v1 because Hermes already provides the chat/cron surface and the repo already contains the task/fleet primitives.

This file is intentionally small and operator-maintained. Hermes or Codex may read it to answer "what am I forgetting?", but canonical evidence remains in `tasks/todo.md`, PRs, git history, and fleet reports.

## Needs Your Decision

- [ ] Flyer source-contract correction pass: approve the lean P0 slice versus the broader provider/vision-client bundle.
- [ ] Hermes fleet roles: confirm VPIN remains secondary production/backup after Srilu is normalized.
- [ ] Flyer CTA resend verification: choose when to run the manual resend test for `+17329837841`.

## Waiting On You

- [ ] Production pilot proof: run the live WhatsApp smoke from `docs/runbooks/production-pilot-shift-catering-daily-brief.md`.
- [ ] Phase 2 candidate selection: choose the first low-risk customer-facing expansion loop after pilot smoke.
- [ ] Phase 3 tooling choice: decide lightweight repo/Codex role prompts versus Hermes profiles plus Kanban versus hybrid.

## Active Risks

- [ ] Flyer exact-source edit requests can still produce wrong customer outcomes until the source-contract/provider posture work lands.
- [ ] Srilu/VPIN runtime posture is red in the fleet report until normalization is complete.
- [ ] Production-quality Flyer real-model smoke remains Session 3-owned and pending PR evidence.

## Handoffs And Promises

- [ ] Keep PR #136 follow-up in mind: update the checkout used by the daily Hermes fleet check if that automation does not pull fresh per run.
- [ ] Use this file as the first source for the daily ops brief; do not duplicate long backlog history here.

## Parking Lot

- [ ] Future enhancement: have Hermes post the brief to a personal chat after the Markdown generator proves useful.
