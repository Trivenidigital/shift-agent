**Drift-check tag:** extends-Hermes

# F0105 Post-Deploy Recovery Runbook - 2026-05-27

## Context

Project `F0105` failed during concept generation with `Pillow is required for exact identity overlay`. The reliability PR fixes future exact-identity overlay fallback, classifies dependency failures as `dependency_missing`, persists project sender origin, and alerts the operator when recovery cannot prove a customer-visible outcome.

## Boundary

This runbook is for the operator after merge and deploy authorization. The PR branch must not send a customer message or re-render from local/dev context.

## Steps

1. Deploy the merged reliability commit through the normal tarball path.
2. Treat `shift-agent-smoke-test.sh` as blocking. It must pass the exact identity overlay smoke.
3. On the VPS, inspect `F0105` in `/opt/shift-agent/state/flyer/projects.json`.
4. If the customer should still receive the flyer, rerun generation or the approved manual repair path for `F0105`.
5. Verify one customer-visible outcome exists after the recovery action:
   - `flyer_assets_delivered` for `project_id=F0105`, or
   - `flyer_closure_customer_notified` for `project_id=F0105`, or
   - an explicit operator handoff note if no flyer should be sent.
6. Confirm the matching recovery incident is resolved or has a documented operator action.

## Evidence To Preserve

- Deploy tag and source commit.
- Smoke-test output line for exact identity overlay.
- Relevant `decisions.log` rows for `F0105`.
- Final project status and any delivered asset ids.
