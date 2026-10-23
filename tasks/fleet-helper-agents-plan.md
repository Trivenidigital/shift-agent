# Fleet helper agents — plan (2026-10-23)

**Drift-check tag:** `extends-Hermes` — every item extends a deployed
deterministic kernel (notify-owner, alert-integrity-watchdog, the
pilot-readiness Checks pattern, check_hermes_config_yaml, audit_helpers,
tests/conftest + fixtures_fleet). Hermes owns language/tool selection; nothing
here touches that. No new store, router, scheduler, workflow engine, approval
mechanism or notification system is introduced.

**New primitives introduced:** none at the subsystem level. Two additive
LogEntry tags (`audit_emit_failed`, `fleet_readiness_report`,
(the third, `owner_alert_downgraded`, was dropped after review); old readers parse unknown types as
`_UnknownLogEntry`). One read-only module `src/platform/fleet_readiness.py`
plus CLI, timer and failure unit in the deployed script + systemd-timer shape.

## Hermes-first capability checklist

| Step | Tag + capability | net-new LOC |
|---|---|---|
| 1. Read config.yaml, state files, decisions.log (+7d archive) per agent | `[Hermes]` — per-VPS JSON state + audit chain are existing substrate; read-only | 0 |
| 2. Compute per-agent status from a declarative 18-row table | `[net-new]` — Hermes has no fleet/agent health model | ~250 + tests |
| 3. Append `fleet_readiness_report` row | `[Hermes]` — audit chain (`ndjson_append` / log-decision chokepoint), new tag only | ~25 schema |
| 4. Page owner on status regression | `[Hermes]` — dead-man/escalation via existing `notify_owner_with_fallback` (deduped) | 0 |
| 5. Dedupe + emergency-repeat downgrade for ALL notify-owner callers | `[net-new]` — policy logic inside the existing alert kernel; same ledger as safe_io | ~80 + tests |
| 6. Gate "Audit log STALE" on the health heartbeat; watch both dead-letter files | `[net-new]` — small change to the existing watchdog | ~40 + tests |
| 7. Validate relied-upon Hermes config keys (plugins.enabled, whatsapp toolset, disabled_toolsets) | `[net-new]` — Hermes validates its own shape, not our posture | ~60 + tests |
| 8. Record audit-emit failures where plugins swallow them | `[Hermes]` — audit chain; new tag + one helper | ~40 + tests |
| 9. Fleet conformance tests over every `src/agents/*` | `[net-new]` — test code | ~200 |
| 10. Docs: real execution model (cf-router arm / read tool / timer) | `[net-new]` — prose | 0 |

Red-flag check: 6 of 10 steps net-new, all deterministic infra (audit, alerting,
readiness, tests) — the categories the shared-platform directive assigns to
deterministic code, not to Hermes. No step re-implements interpretation,
extraction or tool selection.

awesome-hermes-agent / skill-hub check: no fleet-ops or alert-policy skills
apply (the hub is disabled on this loadout anyway). Verdict: thin adapters.

## Drift-rule self-checks

- ✅ Read `src/platform/safe_io.py` (`_notify_dedup_key` :533, `notify_owner_with_fallback` :641-717, delivered-only arming) before drafting the notify-owner dedupe rule.
- ✅ Read `AGENTS.md`, `docs/governance/engineering-directive.md` (§2, §4, §9) and `docs/governance/project-registry.yaml` before classifying paths (all tracks → shift-platform ops/source/tests).
- ✅ Read `tasks/audits/session-handoff-2026-09-02.md` (18-agent matrix; zero `dispatcher_routed` rows; cf-router bare except) before scoping audit integrity.
- ✅ Read (via explore maps, file:line cited) `src/agents/shift/scripts/shift-agent-notify-owner` :127-130/:286-292/:330-355, `src/platform/scripts/alert-integrity-watchdog` :67/:132-153, `src/agents/shift/scripts/pilot-readiness-check` :37-49/:269-311, `src/platform/check_hermes_config_yaml.py`, `src/plugins/cf-router/actions.py` :2369/:2447, `tests/conftest.py` :36-120, `tests/fixtures_fleet.py` :65-77.
- ✅ Verified runtime on box `a680d32f` 2026-10-23: config.yaml `plugins.enabled` 3/3, `platform_toolsets.whatsapp` ∋ `shift_agent_read`, 7 disabled toolsets incl. `file`; 0 `dispatcher_routed` and 0 `agent_declined` rows ever; alert title histogram below.

## Why (evidence from box `a680d32f`, 2026-10-23)

- Owner-alert titles in the retained audit window: 984 "Flyer manual queue SLA
  breach" (P2 with retry=60/expire=3600 → the "100s of messages every minute"),
  165 "Agent health issues", 78 "Health check itself has stopped", 6 "Audit log
  STALE" (quiet nights). Dedupe exists only in `safe_io.notify_owner_with_fallback`;
  every bash/systemd caller of the binary gets none.
- `dispatcher_routed` rows ever: 0; `agent_declined` rows ever: 0. cf-router's
  audit emit swallows `ValidationError` to stderr (actions.py:2369, :2447), so
  "no traffic" and "silently dropped" are indistinguishable.
- `pilot-readiness-check` covers config/roster/menu for 3 agents and is never
  run by deploy or a timer. Nothing reports per-agent or per-type freshness.
- `check_hermes_config_yaml.py` never validates `plugins.enabled`,
  `platform_toolsets.whatsapp`, or `agent.disabled_toolsets` — the keys that make
  3 plugins and 5 tools exist. Only cf-router gets a pre-restart compile gate.
- 9 of 18 agent dirs are skills-only; SKILL.md cannot execute on this loadout
  (`skills` + `terminal` toolsets disabled), so the documented self-decline
  never runs. `docs/hermes-alignment.md` still describes that model as current.

## Tracks (one PR each)

### PR1 — audit integrity + fleet harness (`feat/fleet-audit-integrity-1023`)
1. `schemas.py`: `AuditEmitFailed` (`audit_emit_failed`: source, intended_type,
   intended_reason|None, error_class, detail≤500), `FleetReadinessReport`
   (`fleet_readiness_report`: generated_at, agents_total, by_status, regressions,
   report_path), `OwnerAlertDowngraded` (`owner_alert_downgraded`: title,
   requested_priority, sent_priority, reason, window_minutes). All in `LogEntry`.
2. `audit_helpers.record_audit_emit_failure(...)` — never raises; called from
   cf-router actions.py:2369 and :2447 and any other proven swallow site in
   src/plugins. The call sits in its own `try/except Exception: pass` (the helper
   imports pydantic/schemas at module top) and uses a NON-blocking lock attempt
   (`try_acquire_filelock_with_retry`, attempts=1) so a contended lock can never
   stall an inbound gateway turn; on `LockUnavailable` it falls back to stderr.
   Scope: it writes to the same sink that just failed, so it surfaces
   ValidationError-class drops (the proven class), not disk/permission failures.
3. `tests/fleet_harness.py`: `run_agent_script()` + `read_audit_rows()` over
   `fixtures_fleet.load_script` / conftest isolation.
4. `tests/test_fleet_conformance.py` parametrized over every `src/agents/*`:
   SKILL.md frontmatter parses; a `Config` block exists (explicit allowlist with
   reason otherwise); every audit `type` literal in the agent's scripts is a
   known LogEntry type; every `reason=` literal passed to `audit_intercepted`
   in cf-router ∈ `CfRouterIntercepted.reason` — by GENERALIZING the existing
   scanner in tests/test_flyer_intent_layer.py (~:290-307, flyer_ prefix only)
   to all prefixes and to actions.py, not a third scanner. Each test fails on a mutant.

### PR2 — Fleet Sentinel (`feat/fleet-sentinel-1023`, after PR1)
`src/platform/fleet_readiness.py` + `src/agents/shift/scripts/fleet-readiness-report`
+ timer/service/failure unit. Declarative `FLEET` table of all 18 agents:
reachability class (`cf_router_arm|read_tool|timer|skill_only`), enabled-flag
path, state files, timers, expected audit types with freshness SLO, heartbeat.
Statuses: `active|dormant_by_config|awaiting_data|stale|unreachable_by_design|error`.
JSON + `--text`; writes `state/fleet-readiness-last.json`; one
`fleet_readiness_report` row; P1 alert (deduped) ONLY on regression vs the
previous run; counts `audit_emit_failed` rows. Conformance test: every
`src/agents/*` dir has a FLEET row.
Review-driven rules: timer DAILY (never ≤ the 120-min alert-integrity freshness
threshold — a frequent appender would keep decisions.log's mtime fresh and
disable the STALE watchdog; a test pins the interval). Ships REPORT-ONLY (no
page); graduation trigger to arm the P1 regression page = 7 consecutive daily
runs with zero spurious regressions, recorded in the deploy record. Run 1 emits
`baseline` and alerts nothing; the baseline is
the single JSON `state/fleet-readiness-last.json` beside the other watchdog
state files. 7-day window = live log + yesterday's uncompressed archive + ~5 .gz
(gzip streaming, ts cutoff; archive dir is 0750 so the timer runs as shift-agent).
"Stale" for timer agents = missing terminal row for a past business date, not
row age: `brief_skipped{already_sent,disabled}` / `eod_skipped{already_done,disabled}`
are benign, `disabled` renders as `dormant_by_config`, `{catchup_expired,
data_unavailable,dependency_down,send_uncertain}` are degraded. The report and
the audit row carry aggregate counts and agent ids only — never phones, LIDs or chat ids.

### PR3 — Alert hygiene (`feat/alert-hygiene-1023`, after PR1)
Scope after adversarial review (2026-10-23): same-message dedupe already exists
for every Python caller (`safe_io.notify_owner_with_fallback` :703-725) and the
bash callers self-throttle; PR3 does NOT add a second dedupe layer.
1. DROPPED after a box query (2026-09-22→10-23: 1075 P2 rows; identical P2
   repeats within 6h = 980 flyer SLA + 5 "Flyer source-edit SLA watchdog
   failed", nothing else). An emergency-repeat ledger would fire ~5×/month once
   item 4 lands; not worth a new ledger + tag during an alert-fatigue fix.
   Replaced by two root-cause fixes:
   1'. `shift-agent-health-check.sh` writes `state/last-health-check-ts` only on
   the all-green branch (:122-125), so any failing check makes
   `shift-agent-health-watchdog.sh` page P2 "Health check itself has stopped"
   hourly (78 pages) while the check is running. Write a run-completed heartbeat
   on EVERY completed run (a mid-run crash still leaves it stale = the true case).
   1''. Flyer `*-failure.service` units: `--priority 2` → `1` (a watchdog's own
   unit failure is not a customer emergency).
   No change to `shift-agent-notify-owner`; no `owner_alert_downgraded` tag.
2. `alert-integrity-watchdog`: do NOT gate STALE on `last-health-check-ts` (it is
   written only when ALL health checks pass, so a 403 or disk failure would hide a
   real stall). Fix the actual repeat cause: the page body embeds
   `int(age_minutes)` (:149-152) so every page has a unique dedupe key. Make the
   body stable (age only in the structured journald event) and re-page a
   persisting STALE at most every 6h via the watchdog's own `state_path`.
   Watch BOTH dead-letter files (`logs/notify-failed.log`, `state/notify-failed.log`),
   reporting growth per file (the helper and the binary can both append for one
   failed alert) with one page per run as today.
3. `shift-agent-health-watchdog.sh`: write the throttle only after a delivered page.
4. `flyer-source-edit-sla-watchdog.service`: `--priority 1 --repeat-minutes 360`.
5. `shift-agent-smoke-test.sh` enabled-units list mirrors exactly what deploy.sh
   enables (never add units deploy deliberately leaves off).
6. `OnFailure=` handlers for alert-integrity-watchdog and check-corrupt-state (P1).

### PR4 — Hermes conformance (`feat/hermes-conformance-1023`, independent)
1. `check_hermes_config_yaml.py` (superset semantics everywhere — extra entries
   pass): FAIL on a missing plugin in `plugins.enabled`; FAIL if
   `platform_toolsets.whatsapp` lacks `shift_agent_read`; `agent.disabled_toolsets`
   must contain terminal/skills/code_execution → FAIL (their absence makes SKILL.md
   and shell execution reachable again), and delegation/browser/clarify/file → WARN.
   Live read-back recorded 2026-10-23 on box `a680d32f`: plugins 3/3, whatsapp
   toolset ∋ shift_agent_read, 7 disabled incl. `file` — every FAIL check passes.
   Note: a FAIL blocks the deploy pre-install gate (deploy.sh:2718-2723) and the
   smoke (:781-794); that is intended for these keys.
2. deploy.sh pre-restart compile gate covers all three plugin dirs.
3. Docs: `docs/hermes-alignment.md` execution model; `phase0-agents.md` drift
   lines; `gateway-toolset-scoping.md` documents the `HERMES_CONFIG_GATE_OVERRIDE_*`
   escape hatch beside the plugin kill-switch (a FAIL on `plugins.enabled`
   otherwise turns the kill-switch into a deploy block).

## Deferred (operator decisions, not builds)
Close F0226/F0228 or keep; arm `catering_followup`; seed compliance items for
employee_docs/sales_tax categories; `shift-agent-skills-audit.timer` posture
(deploy disables it every run; runbook says enabled); `patch-hermes.py` stale
whatsapp.py target in budget-bootstrap mode; per-turn cost observability
(branch on HOLD).

## Capability Reuse Map — shift-platform (all four PRs)
- Requested outcome: fleet-wide helpers that make every agent's health,
  alerting, Hermes posture and audit integrity observable and testable.
- Affected projects: shift-platform (all); flyer-studio (PR3 item 4, unit file only).
- Applicable directives: engineering-directive v1.2.0 blob 31d7706d;
  shared-platform-directive v1.1.0 blob 9c6ce256; project-registry v1.0.0 blob 207ad418.
- Existing platform/model capabilities reused: none required (deterministic infra).
- Existing deterministic kernels reused: notify-owner, safe_io dedupe ledger,
  alert-integrity-watchdog, pilot-readiness Checks pattern,
  check_hermes_config_yaml, audit_helpers/log-decision chokepoint, systemd
  timer + failure-unit pattern.
- Existing stores/workflows reused: decisions.log, state/notify-dedup.json, config.yaml.
- Thin adapters: fleet_readiness.py (read-only aggregator), harness helpers.
- Custom runtime code genuinely unavoidable: FLEET table + status computation;
  emergency-repeat ledger logic.
- New subsystem: no.
- Evidence existing capabilities were insufficient: §Why (file:line in the four
  explore maps, 2026-10-23).
- Architecture exception: none.
- Shared-platform impact: every agent — additive observability only; no
  routing, identity or send-eligibility change.
- Other agents affected: all 18 (read-only observation); flyer SLA page priority.
- Vertical E2E proof: per PR — mutant-checked tests + on-box run after deploy
  (`fleet-readiness-report --text`; one deduped/downgraded alert observed in decisions.log).
