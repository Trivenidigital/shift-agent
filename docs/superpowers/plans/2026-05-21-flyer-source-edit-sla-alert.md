**Drift-check tag:** extends-Hermes

# Flyer Source-Edit SLA Alert

## Goal

Surface stale exact source-edit manual queue rows to the operator within 5-10 minutes so source-preserving edits cannot wait silently. This slice is advisory/operator alerting only: no customer copy changes, no project disposition changes, no provider routing changes, and no source-edit provider enablement.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Scheduled/read-only checks | Hermes/systemd timers already run health, routing summaries, briefs, and watchdogs on the VPS | Reuse the existing timer/deploy pattern |
| Operator notification | Existing `shift-agent-notify-owner` Pushover/WhatsApp fallback chokepoint | Use it; do not create a second alert channel |
| Queue/source state | Flyer owns `projects.json`, `FlyerManualReview`, and source-preservation policy | Thin alert wrapper aligned to PR #148 `manual_source_edit_stale` predicate |
| Source-edit provider routing | Covered by merged PR #147 provider policy shape | Do not modify dispatch/provider files or enable policy |
| Self-evaluation/reporting | PR #148 provides read-only incidents and operator brief data | Complement it with time-bound alerting, not duplicate full eval logic |

Awesome Hermes Agent ecosystem check: no external Hermes skill is needed for this narrow watchdog; existing in-repo Hermes/operator primitives cover scheduling and alert delivery.

## No-Mutation Boundary

- Read `projects.json`.
- Write only watchdog throttle/audit state and append typed alert audit rows.
- Throttle state writes use existing locked atomic read-modify-write patterns.
- Do not change project status, manual queue rows, customer records, source contracts, QA reports, provider policy, or customer messaging.
- Alert bodies include project IDs, reason codes, and ages only; they do not print raw customer request text or secrets.
- Systemd/deploy wiring explicitly installs `src/agents/flyer/systemd/*.service/*.timer`, enables the SLA timer, and includes a failure notification service.

## Acceptance Criteria

- Eligible rows are `project.status == "manual_edit_required"`, `manual_review.status in {"queued", "in_progress"}`, and `manual_review.reason_code == "source_edit_provider_unavailable"`.
- Age uses `manual_review.queued_at`, then `updated_at`, then `created_at`.
- A source-edit provider-unavailable manual-review row older than 10 minutes triggers an operator alert.
- Rows younger than threshold do not alert.
- The alert priority bypasses quiet-hours suppression so success means the operator was paged, not merely suppressed.
- Repeat alerts are throttled by queue-row identity (`project_id`, `reason_code`, and queue timestamp) for a default 60 minutes.
- Requeued source-edit rows on the same project alert independently.
- Notification failure returns nonzero and does not mark the project alerted.
- Fired, throttled, and notify-failed outcomes append typed audit rows.
- JSON output is deterministic and advisory/read-only.
- Deploy wiring installs the script and enables a 5-minute timer.
- Focused tests, `py_compile`, and `git diff --check` pass.

## Deferred

- Source-edit provider production smoke and operational enablement.
- Hermes vision/OCR source-contract extractor improvements.
- Source-contract facts enforced as locked facts.
- Source-aware visual QA gate before preview/delivery.
