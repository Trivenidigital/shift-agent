# Flyer Autonomous Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two guarded autonomous recovery paths for Flyer Studio on `main-vps`:

1. Customer recovery path: Hermes detects incoming Flyer request failures, classifies the incident, sends one safe customer response when appropriate, and records a durable incident.
2. Codex repair path: Hermes packages reproducible failure evidence and queues a verified Codex implementation lane. Code fixes run outside the live WhatsApp hook, through normal tests/review/PR/deploy gates, and Hermes sends only deterministic customer-safe outcomes.

**Architecture:** Hermes remains the runtime owner: WhatsApp ingress, cf-router decisions, state, audit, mode gates, customer copy, and incident lifecycle. Codex is an implementation worker invoked only from a durable recovery incident, never from the live message hook, and never as an unbounded production state mutator. Flyer code stays the deterministic contract/safety harness for validation, copy lint, idempotency, tests, and deploy gates.

**Tech Stack:** Python 3, Pydantic v2 schemas in `src/platform/schemas.py`, JSON state via `safe_io`, existing Hermes/cf-router plugin hooks, systemd timer/service for watchdog execution, existing deploy/smoke scripts, pytest, GitHub PR workflow.

---

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Flyer recovery incident state, Flyer failure classifier, customer-safe recovery response policy, crash-safe recovery ack outbox, Codex repair bundle writer, Codex lane request gate, recovery watchdog timer/service, recovery deploy/re-enable gate.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress and replies | yes - deployed Hermes gateway, cf-router, `safe_io.bridge_post` | use existing path only after deterministic copy/dedupe gates |
| Flyer runtime judgment | partial - existing Flyer cf-router hooks and current Hermes intent backlog | reuse hooks/audit as evidence; do not add another router |
| Durable state/audit | yes - JSON state, `safe_io.FileLock`, atomic writes, NDJSON audit union | use existing state and add narrow Flyer recovery audit variants |
| Customer copy | partial - Flyer helper copy and customer-message backlog | build one shared recovery-copy policy/lint; keep internal terms out |
| Incident monitoring | partial - in-tree systemd watchdog patterns and Daily Brief orphan-attempt scan | build a Flyer-specific watchdog using those patterns |
| Codex delegation | yes - Hermes Skills Hub and live `main-vps` install include `autonomous-ai-agents/codex` and `autonomous-ai-agents/kanban-codex-lane` | v1 writes sanitized lane requests only; design must prove the consumer boundary before any automated code mutation/deploy |
| PR workflow | yes - Hermes Skills Hub and live `main-vps` install include `github/github-pr-workflow`; repo already uses PR/review cadence | use normal branch, tests, PR, reviewer gates |
| Image/source edit repair | partial - existing Flyer render/preflight/manual queue/provider paths | recovery classifies and packages evidence; it does not bypass renderer/provider gates |

Awesome Hermes Agent ecosystem check: checked `awesome-hermes-agent`; no turnkey Flyer Studio request-failure recovery skill found. Verdict: reuse Hermes/cf-router/state/delegation primitives and build only the Flyer-specific incident classifier, copy policy, and Codex lane gate.

Sources checked: Hermes Skills Hub (`codex`, `kanban-codex-lane`, `github-pr-workflow`), `awesome-hermes-agent`, and live `main-vps` installed skill/plugin inventory.

## Live Runtime Capability Check

Checked on 2026-05-23 with the required two-step SSH redirect/read pattern:

- Installed on `main-vps`: `/root/.hermes/skills/autonomous-ai-agents/codex/SKILL.md`.
- Installed on `main-vps`: `/root/.hermes/skills/autonomous-ai-agents/kanban-codex-lane/SKILL.md`.
- Installed on `main-vps`: `/root/.hermes/skills/github/github-pr-workflow/SKILL.md`.
- Installed on `main-vps`: `/root/.hermes/plugins/cf-router/plugin.yaml`.

Runtime verdict: the Hermes skills exist, but this plan still treats automated code mutation/deploy as gated design work. The first implementation must not add a repo-local `flyer-codex-recovery-runner` on the customer runtime that edits source or deploys by itself. v1 writes durable repair bundles/lane requests; the design must define and verify the isolated consumer boundary before any worker applies code.

## Existing Code Read

- `src/platform/schemas.py`: Flyer workflow/status/customer/project schemas and current Flyer audit variants.
- `src/plugins/cf-router/hooks.py`: current Flyer intercept, send, generation, revision, manual-edit, and failure sites.
- `src/plugins/cf-router/actions.py`: send helpers, source-edit preflight, subprocess wrappers, audit helpers, and dedupe guards.
- `src/agents/flyer/workflow.py`: pure state-machine and revision helper logic.
- `src/agents/shift/scripts/shift-agent-deploy.sh`: canonical tarball deploy and timer install pattern.
- `src/agents/daily_brief/scripts/send-daily-brief`: orphan-attempt/idempotency pattern to mirror for watchdog logic.
- `tasks/flyer-hermes-intent-operating-layer-backlog-2026-05-22.md`: target architecture, self-eval/operator brief goals, and "Hermes = brain; Flyer code = contract" principle.

## Additional Reads Before Design

- `src/platform/safe_io.py` audit append and bridge-send chokepoints.
- `log-decision-direct` deploy target, or the in-tree script/path that owns production audit writes.
- `src/agents/flyer/scripts/generate-flyer-concepts`.
- `src/agents/flyer/scripts/send-flyer-package`.
- `src/agents/flyer/scripts/flyer-delivery-report`.
- `src/agents/shift/scripts/shift-agent-smoke-test.sh`.
- Live `main-vps` `config.yaml`, active timers, bridge health, gateway unit drop-ins, and installed `/root/.hermes` plugin/skill paths.

## Scope Guard

In scope:

- Detect stuck/failing Flyer request incidents from audit/state/log evidence.
- Send at most one customer-safe recovery acknowledgement per incident.
- Prevent any recovery code from reintroducing WhatsApp loops or test-to-live bridge sends.
- Create reproducible repair bundles for Codex.
- Add a gated Codex repair bundle/lane request that can be consumed by a verified operator-side or isolated Hermes Codex lane.
- Specify deploy/re-enable safety gates for `main-vps`; implement only the gates that are safe in v1.
- Create PR and run two parallel PR reviews after build.

Out of scope for this first implementation:

- Full Hermes intent operating layer migration.
- New image provider selection or source-edit provider migration.
- Direct money/payment automation.
- Customer-visible SLA promises beyond "I have it and am checking it."
- Always-on unattended autodeploy as the default production mode.
- A customer-runtime script that edits source, opens worktrees, or deploys without a separately verified Codex lane boundary.

## Safety Defaults

- Production default mode is `observe`.
- `flyer.enabled=false` is a hard global kill switch for all customer-visible Flyer recovery sends. The only exception is a one-shot break-glass setting scoped to a specific incident id with TTL after `flyer-recovery-preflight --text` proves the active send path is safe.
- `flyer.enabled=false` must not be auto-reenabled without a green recovery deploy gate and a separate explicit re-enable step.
- Autonomy levels are explicit:
  - `off`: no monitoring side effects.
  - `observe`: classify/audit incidents only.
  - `customer_ack`: audit plus one safe customer acknowledgement.
  - `codex_draft`: create repair bundles and Codex lane requests, no code mutation on the customer runtime and no deploy.
  - `codex_autodeploy`: reserved for a follow-up design after the isolated lane and deploy boundary are verified; not enabled by this first PR.
- The live Hermes hook never invokes Codex. Only the watchdog/timer may create a Codex lane request from a durable incident.
- `PYTEST_CURRENT_TEST` bridge refusal in `safe_io` remains mandatory; tests must never reach the live WhatsApp bridge.
- Recovery replies use a crash-safe ack outbox plus `send_flyer_text`. No raw `bridge_post` calls from the new recovery path.
- The Codex lane must run with live bridge credentials removed or replaced by a fake sink, production state copied/read-only, and an explicit `FLYER_RECOVERY_NO_LIVE_SEND=1` guard enforced by send helpers.
- Customer copy must pass a class-aware policy plus forbidden-term lint. Forbidden terms include provider, manual queue, source-preserving, operator, audit, stack trace, traceback, pytest, Codex, Hermes, deploy, and internal project IDs unless explicitly allowlisted.
- Repair bundles redact or hash customer identifiers where possible; raw chat IDs/phone numbers stay in production state and are not copied into PR text.

## Path A: Customer Recovery

- [ ] Add `FlyerRecoveryConfig` to `FlyerConfig` with mode, scan window, ack cooldown, max incidents per run, break-glass incident allowlist, and safe defaults.
  - Include config-load tests for absent `flyer.recovery`, unknown rollback fields under `extra="forbid"`, and timer disabled/enabled behavior from current config.
- [ ] Add state file `/opt/shift-agent/state/flyer/recovery_incidents.json` managed with `safe_io.FileLock` and atomic JSON writes.
- [ ] Add schemas for recovery incident state:
  - incident id, status, source event fingerprint, stable ack dedupe key, project id, chat id hash, sender phone hash, failure class, severity, first/last seen, ack status, Codex lane status, deploy status.
  - allowed incident statuses: `open`, `ack_reserved`, `ack_sent`, `ack_failed`, `ack_uncertain`, `repair_queued`, `fix_ready`, `deployed`, `resolved`, `suppressed`, `manual_required`.
- [ ] Add audit variants:
  - `flyer_recovery_incident_opened`
  - `flyer_recovery_customer_ack_attempted`
  - `flyer_recovery_customer_ack_sent`
  - `flyer_recovery_customer_ack_failed`
  - `flyer_recovery_customer_ack_uncertain`
  - `flyer_recovery_customer_ack_suppressed`
  - `flyer_recovery_codex_lane_queued`
  - `flyer_recovery_deploy_gate`
  - `flyer_recovery_resolved`
- [ ] Implement `src/agents/flyer/recovery.py` pure helpers:
  - classify audit rows and project state into failure classes.
  - generate stable incident fingerprints and ack dedupe keys from chat/account, project or root inbound message id, failure class, and canonical failure source; never from timestamps or changing detail text.
  - validate canonical customer-originated inbound evidence: provider message id, chat id, sender identity, timestamp, text/media shape, and explicit non-agent source marker.
  - decide whether a customer ack is allowed.
  - render customer-safe ack text.
  - lint customer copy.
- [ ] Implement `flyer-recovery-preflight --text`:
  - verifies `flyer.enabled`, recovery mode source, active timers, bridge connectivity, queue length, current WhatsApp send emitters, message id/fromMe or equivalent inbound evidence availability, `safe_io` pytest guard on VPS, state ownership, gateway restart drain config, and installed Hermes Codex lane skills.
  - required before `customer_ack` or higher side effects.
  - suppresses customer ack if customer-originated evidence is incomplete.
- [ ] Implement `src/agents/flyer/scripts/flyer-recovery-watchdog`:
  - reads recent `decisions.log` and Flyer state.
  - opens/dedupes incidents.
  - in `customer_ack` and higher modes, reserves an ack under lock before sending, then records delivered/failed/uncertain after the bridge result.
  - treats crash-after-reserve and crash-after-send-before-final-write as `ack_uncertain` and suppresses retries until operator/preflight verification.
  - in `codex_draft` and higher modes, writes a repair bundle and queues a Codex lane request.
  - never crashes the timer on malformed rows; records suppressed incidents instead.
- [ ] Add `src/agents/flyer/systemd/flyer-recovery-watchdog.service` and `.timer` using Daily Brief/Shift watchdog security and logging conventions.
- [ ] Wire deploy install/start behavior into `shift-agent-deploy.sh`, but keep timer disabled unless config mode is not `off`.

Failure classes for v1:

| Failure class | Existing evidence source | Residual gap |
|---|---|---|
| `bridge_send_failed` | `cf_router_intercepted.detail` `ack_error=...`, send helper return status, `flyer_delivery_failed` for assets | add structured parsing helper; add narrow audit fields only if free-form detail is insufficient |
| `concept_generation_failed` | `cf_router_intercepted.reason=flyer_primary_failed` with `concept_generation_failed`/`regeneration_failed` detail | map exact detail markers before adding new variants |
| `preview_delivery_failed` | `send_flyer_concept_previews` return detail and `flyer_delivery_failed` rows | verify final/media delivery scripts emit enough detail |
| `provider_unavailable` | `flyer_source_edit_preflight` detail and source-edit/manual-review reasons | map source-edit unavailable states from project/manual queue state |
| `state_transition_failed` | subprocess wrapper non-zero/JSON parse detail in `cf_router_intercepted.detail` | add classifier coverage for each wrapper prefix |
| `clarification_loop` | repeated customer-visible Flyer ack rows in `cf_router_intercepted` for the same chat/project | needs stable customer-originated inbound model before any ack |
| `replay_loop_suspected` | durable inbound dedupe state, repeated outbound body/dedupe state, repeated `cf_router_intercepted` rows | no customer ack unless preflight proves inbound/outbound origin evidence |
| `manual_queue_stale` | project state `manual_edit_required`, manual queue/close state, delivery report | verify current manual queue state source before design |

Customer ack policy:

- Send only for active/trial/paid customers or open guest orders where the original message was customer-originated.
- Suppress all customer-visible recovery sends when `flyer.enabled=false`, unless the incident id is explicitly present in a break-glass allowlist with TTL and preflight has passed.
- Suppress for payment gates unless the recovery class is a delivery/runtime failure after payment.
- Suppress if any ack for the same stable ack dedupe key is `ack_reserved`, `ack_sent`, or `ack_uncertain` inside the cooldown.
- Suppress if the class-aware copy lint fails.
- Suppress if canonical customer-originated evidence is incomplete or only inferred from `fromMe`.
- Recovery copy must avoid SLA promises, internal cause, payment implication, project IDs, provider/operator/audit language, and any follow-up promise unless the incident has an active repair/follow-up path recorded.
- Example safe ack for an eligible tracked incident: `Flyer Studio\n------------\nI have your request. I am checking it now and will follow up here with the next step.`

## Path B: Codex Repair

- [ ] Add repair bundle writer:
  - incident summary.
  - sanitized recent audit rows.
  - relevant project/customer state excerpts with phone/chat redaction.
  - exact failure class and suspected code area.
  - local reproduction command suggestions.
  - safety contract: no live bridge sends in tests, no customer reply text without copy lint, no production deploy until gates pass.
- [ ] Add Codex lane request file under `/opt/shift-agent/state/flyer/recovery_codex_queue/` with sanitized bundle path, requested scope, and required gates.
- [ ] Do not add a repo-local customer-runtime runner in v1. The design must pick exactly one verified lane consumer:
  - installed Hermes `kanban-codex-lane`,
  - operator-side Codex automation,
  - or manual PR workflow using the generated bundle.
- [ ] Add deploy/re-enable gate specification and safe helper checks:
  - requires green focused tests.
  - requires py_compile for touched Python modules.
  - requires `git diff --check` with known pre-existing warnings documented.
  - requires deploy smoke/import gate.
  - requires post-restart bridge health.
  - requires deployed commit/tag to match the reviewed build.
  - requires `safe_io` bridge guard present on VPS.
  - requires a separate 15-minute no-new-Flyer-outbound watch window before re-enabling Flyer.
- [ ] Add reprocess/follow-up handoff:
  - after deploy, mark incident `deployed`.
  - if safe and applicable, send a recovery outcome or route the project back to existing generation/manual queue flow.
  - if not safe, mark `manual_required` and surface in operator logs/Cockpit follow-up.

Autodeploy guard:

- `codex_autodeploy` must remain opt-in.
- The first merge should ship only through `observe`/`customer_ack`/`codex_draft`. `codex_autodeploy` is documentation/design-only until the installed lane and operator approval boundary are proven.
- No live runtime patching as the normal path. Use branch, tests, PR, tarball deploy, smoke, and watch window.

## Test Plan

- [ ] Unit tests for `recovery.py` classification:
  - generation failure opens one incident.
  - bridge failure opens one incident.
  - duplicate audit rows dedupe.
  - repeated clarification/payment replies become `clarification_loop`.
  - repeated same outbound body becomes `replay_loop_suspected`.
- [ ] Unit tests for customer ack policy:
  - sends exactly once for eligible runtime failure.
  - suppresses payment-first blockers.
  - suppresses when copy contains forbidden internal terms.
  - suppresses under `observe`.
  - respects cooldown and incident fingerprint.
  - reserves before sending; crash-after-reserve suppresses retry.
  - crash-after-send-before-final-write becomes `ack_uncertain`.
  - duplicate timer runs and changed audit timestamps do not create duplicate acks.
- [ ] Unit tests for customer-originated evidence:
  - real WhatsApp transcript shape with provider message id.
  - outbound echo shape.
  - missing metadata shape suppresses ack.
- [ ] Unit tests for repair bundle redaction and contents.
- [ ] Unit tests proving Codex lane/smoke commands cannot call the live bridge with `FLYER_RECOVERY_NO_LIVE_SEND=1`, even outside pytest.
- [ ] CLI tests for `flyer-recovery-watchdog --dry-run`, `--mode observe`, and state mutation.
- [ ] CLI tests for `flyer-recovery-preflight --text` pass/fail cases.
- [ ] Static tests for systemd/deploy installation.
- [ ] Regression tests proving `PYTEST_CURRENT_TEST` bridge refusal still blocks live sends.
- [ ] Focused cf-router tests around failure audit sites that should be picked up by the watchdog.

Focused verification commands:

```powershell
python -m pytest tests/test_flyer_recovery.py tests/test_flyer_scripts_static.py tests/test_safe_io_bridge_post.py tests/test_cf_router_flyer_routing.py -q
python -m py_compile src\agents\flyer\recovery.py src\plugins\cf-router\hooks.py src\plugins\cf-router\actions.py src\platform\schemas.py
git diff --check
```

Production verification:

- [ ] Deploy with `flyer.recovery.mode=observe` and `flyer.enabled=false`.
- [ ] Run `flyer-recovery-preflight --text` and record every runtime assumption result.
- [ ] Run `flyer-recovery-watchdog --dry-run --text` on `main-vps`.
- [ ] Confirm no WhatsApp outbound in observe mode.
- [ ] Enable `customer_ack` only when `flyer.enabled=true` or when a one-shot break-glass incident allowlist with TTL is set after preflight.
- [ ] Confirm decisions log records incident and ack/suppression rows.
- [ ] Confirm `hermes-gateway` active, bridge connected, queue length 0.
- [ ] Confirm 15-minute watch window sees zero unexpected Flyer outbound rows, zero repeated inbound fingerprints, zero repeated recovery acks, gateway active, bridge queue length 0, and deployed tag/hash matching the reviewed build.

## Review Pipeline

- [x] Plan review by two parallel agents:
  - Reviewer A: loop/safety/runtime-state reviewer.
  - Reviewer B: Hermes-first/architecture reviewer.
- [x] Apply plan review fixes.
- [ ] Write design doc at `docs/superpowers/specs/2026-05-23-flyer-autonomous-recovery-design.md`.
- [ ] Design review by two parallel agents:
  - Reviewer A: state-machine/schema/audit reviewer.
  - Reviewer B: deployment/autonomy/customer-copy reviewer.
- [ ] Apply design review fixes.
- [ ] Build with tests first.
- [ ] Create PR.
- [ ] PR review by two parallel agents:
  - Reviewer A: code correctness/regression reviewer.
  - Reviewer B: production safety/autodeploy reviewer.
- [ ] Apply PR review fixes and rerun verification.

## Open Design Questions

- Which exact mechanism should production use to consume lane requests: installed Hermes `kanban-codex-lane`, an existing operator-side Codex timer, or manual PR workflow using generated bundles?
- Should the first PR include only `codex_draft`, leaving all automated code mutation/deploy as a follow-up after observing live repair bundle quality?
- Should recovery incidents surface in Cockpit in this PR, or only as JSON/audit/operator logs with Cockpit as follow-up?
- What is the break-glass shape, if any, for a customer acknowledgement while `flyer.enabled=false` remains the live kill switch?

## Plan Review Fixes Applied

- Reviewer A blocker: `flyer.enabled=false` now suppresses all customer-visible recovery sends except a scoped break-glass incident id with TTL and passing preflight.
- Reviewer A blocker and Reviewer B P1: added crash-safe ack outbox semantics with `ack_reserved`/attempted before bridge send, delivered/failed/uncertain states, stable dedupe keys, and crash-window tests.
- Reviewer A high: added required `flyer-recovery-preflight --text` before side effects and concrete runtime assumptions to verify.
- Reviewer A high: added canonical customer-originated inbound evidence and suppress-on-missing-metadata behavior.
- Reviewer A high and Reviewer B blocker: removed the v1 repo-local customer-runtime Codex runner; v1 writes sanitized repair bundles/lane requests and design must verify the isolated consumer boundary.
- Reviewer A medium: made re-enable a separate explicit step with a 15-minute no-new-outbound watch window and exact checks.
- Reviewer A medium: replaced forbidden-word-only lint with class-aware recovery copy policy.
- Reviewer B blocker: verified live `main-vps` Hermes Codex/kanban/GitHub skills and documented installed paths.
- Reviewer B P1: added failure-class to existing-evidence mapping before introducing new audit fields.
- Reviewer B P2: added missing code/runtime reads before design and config migration/rollback tests.

## Acceptance Criteria

- A single failed Flyer request creates at most one open recovery incident.
- Customer-visible recovery copy is short, outcome-first, and free of internal terms.
- Duplicate inbound/outbound loops do not produce repeated customer messages.
- Codex repair work starts only from a durable incident/lane request and never from the live hook.
- The customer runtime does not edit source or deploy code in v1.
- Tests cannot send to the live bridge.
- Autodeploy is impossible in v1; future autodeploy requires a separate verified lane design plus all gates passing.
- The PR includes plan/design/review evidence and focused automated tests.
