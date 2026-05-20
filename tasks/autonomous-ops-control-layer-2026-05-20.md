# Autonomous Ops Control Layer v0.1

**Drift-check tag:** extends-Hermes

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Scheduled checks | yes - Hermes/app automations can call local scripts | v0.1 only creates report inputs; no new automation is installed |
| Operator brief | yes - `tools/operator-brief.py` and runbook exist | extend the brief with optional report sections |
| Fleet posture | yes - `tools/hermes-fleet-upgrade.py` already owns fleet checks | extend its normalization report offline; do not create another fleet tool |
| Flyer runtime quality | yes - PR #137 source-contract layer is now on `origin/main` | report residual risks; no Flyer runtime changes in this slice |
| Auto-merge policy | none found in Hermes skill hub or in-tree tools | build deterministic offline policy evaluator |

Awesome Hermes Agent ecosystem check: no existing Hermes skill/plugin provides Shift-Agent-specific PR policy gates or Srilu/Main/VPIN promotion-readiness contracts. This is repo-specific control-plane glue on top of Hermes reporting.

## Operating policy

- Flyer loop cadence target: every 8 hours.
- Maximum future autonomous output: 1 PR per run and 3 PRs per 24 hours.
- v0.1 is report-only: no PR creation, no merge, no deploy, no GitHub mutation, no VPS mutation, no customer/manual-queue/payment/quota/account mutation, and no campaign sends.
- Future auto-merge requires trusted commit-bound metadata, two unique non-author autonomous reviewer approvals, all checks passing on the same head SHA, no unresolved high/medium finding, allowed category/path policy, and cooldown clearance.
- Provider/model posture, payment/quota/account state, campaign sends, broad non-Flyer cf-router changes, manual queue closure, customer state repair, and VPS runtime mutation require human decision.

## Fleet sequence

1. Observe: daily fleet report and offline normalization snapshots.
2. Normalize contract: Srilu, Main, and VPIN must expose comparable gateway, bridge, env symlink, cockpit, patch gate, deploy marker, backup, skills/plugins, and checked-at evidence.
3. Controlled promotion: Srilu must be green before Main; Main and VPIN must pass the contract before production promotion.
4. Docker decision later: defer until normalization is consistently reported, one clean Srilu -> Main upgrade cycle completes, and backup/restore is proven.

## Residual Flyer risks after PR #137

- Exact source edit must never downgrade into generic reference generation.
- Source-contract QA must verify source facts, not only customer/business/contact facts.
- Customer check-ins such as "any update?" must not create new projects or re-enter clarification loops.
- Real transcript shapes should continue feeding golden fixtures.
- Generated flyers must match customer requirements, not just look polished.

## Stop conditions

- Any candidate needs product judgment.
- Any candidate touches a blocked category or unsafe path.
- Any candidate has only one reviewer, stale reviewers, author reviewers, or reviews not bound to the head SHA.
- Any verification is missing, failing, or not bound to the head SHA.
- Srilu/Main/VPIN normalization snapshots are stale, missing required hosts, or show red contract status.

## Review status

- Plan reviewed by two parallel agents: fixed report-only PR wording, offline normalization requirement, commit-bound eligibility wording, PR #137 merged-state handling, and operator-brief JSON contracts.
- Design reviewed by two parallel agents: fixed metadata provenance, changed-file policy, static no-live-operation tests, snapshot freshness, backup thresholds, landed-work de-dupe, and normalization input schema.
