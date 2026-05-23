# Flyer Autonomous Recovery Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Flyer recovery incident state, recovery classifier, crash-safe recovery ack outbox, recovery copy policy, recovery preflight, repair bundle/lane request writer, recovery watchdog systemd unit.

## Hermes-First Analysis

| Domain | Hermes/in-tree capability found? | Decision |
|---|---|---|
| WhatsApp ingress | yes - Hermes gateway plus `cf-router` pre-gateway hook | reuse; do not route recovery through a new live hook |
| WhatsApp send | yes - `safe_io.bridge_post` and `cf-router.actions.send_flyer_text` | reuse only behind recovery outbox and copy gates |
| Audit writes | yes - `safe_io.ndjson_append`, `log-decision-direct`, `LogEntry` union | add typed recovery variants and keep writer validation |
| State writes | yes - JSON-on-disk, `safe_io.FileLock`, atomic writers | use one locked recovery state file |
| Failure evidence | partial - `cf_router_intercepted.detail`, Flyer project state, `flyer_delivery_failed` | classify from existing evidence first; add narrow recovery audit rows for new actions |
| Idempotency | yes - Daily Brief attempted-before-send and Catering bridge outcome anchors | copy the attempted/reserved-before-bridge pattern |
| Codex lane | yes - live `main-vps` has `autonomous-ai-agents/codex`, `kanban-codex-lane`, `github-pr-workflow` skills | v1 writes sanitized repair bundles only from an explicit operator CLI action; no timer-generated lane requests and no runtime source mutation runner |
| Deploy smoke | yes - `shift-agent-deploy.sh` and `shift-agent-smoke-test.sh` | extend install/smoke checks conservatively |

Awesome Hermes Agent ecosystem check: no ready Flyer recovery skill found. Verdict: use Hermes primitives and build the narrow Flyer recovery layer.

## Runtime Facts

Verified on `main-vps` on 2026-05-23:

- `flyer.enabled: false`.
- `hermes-gateway`: active.
- Bridge health: `{"status":"connected","queueLength":0,...}`.
- `shift-agent-health.timer` and `shift-agent-health-watchdog.timer`: active.
- Codex/Flyer production-push timers that caused incident pressure are currently absent from active timer list.
- `hermes-gateway` includes `TimeoutStopSec=240s`.
- Installed skills include:
  - `/root/.hermes/skills/autonomous-ai-agents/codex/SKILL.md`
  - `/root/.hermes/skills/autonomous-ai-agents/kanban-codex-lane/SKILL.md`
  - `/root/.hermes/skills/github/github-pr-workflow/SKILL.md`
- Installed plugin includes `/root/.hermes/plugins/cf-router/plugin.yaml`.

Design implication: first deploy installs recovery code but leaves the timer disabled. Customer acks require `flyer.enabled=true`; v1 has no disabled-mode break-glass send path.

## File Ownership

Modify:

- `src/platform/schemas.py`
- `src/agents/flyer/recovery.py`
- `src/agents/flyer/scripts/flyer-recovery-watchdog`
- `src/agents/flyer/scripts/flyer-recovery-preflight`
- `src/agents/flyer/systemd/flyer-recovery-watchdog.service`
- `src/agents/flyer/systemd/flyer-recovery-watchdog.timer`
- `src/agents/shift/scripts/shift-agent-deploy.sh`
- `src/agents/shift/scripts/shift-agent-smoke-test.sh`
- `src/platform/safe_io.py` only if the no-live-send guard needs to be generalized beyond pytest.

Tests:

- `tests/test_flyer_recovery.py`
- `tests/test_flyer_recovery_watchdog.py`
- `tests/test_flyer_scripts_static.py`
- `tests/test_safe_io_bridge_post.py`
- `tests/test_flyer_schemas.py`
- focused additions to `tests/test_cf_router_flyer_routing.py` only if an audit detail marker changes.

## Config

Add to `FlyerConfig`:

```python
class FlyerRecoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "observe", "customer_ack"] = "off"
    enable_timer: bool = False
    scan_window_minutes: int = Field(default=30, ge=5, le=240)
    ack_cooldown_minutes: int = Field(default=60, ge=5, le=1440)
    ack_reservation_stale_minutes: int = Field(default=10, ge=1, le=120)
    max_incidents_per_run: int = Field(default=20, ge=1, le=200)
    manual_queue_stale_minutes: int = Field(default=30, ge=5, le=1440)
```

Then add:

```python
recovery: FlyerRecoveryConfig = Field(default_factory=FlyerRecoveryConfig)
```

Mode behavior:

- `off`: watchdog exits 0 without state writes.
- `observe`: opens/suppresses incidents and writes repair bundles only when manually requested by CLI flag; no customer sends.
- `customer_ack`: may send recovery ack only when `flyer.enabled=true`, preflight passes, and the exact incident has strong customer-origin evidence.

The timer is enabled only when `flyer.recovery.enable_timer=true` and mode is not `off`.

Rollback behavior:

- Old config without `flyer.recovery` loads because the field has a default.
- New config with `flyer.recovery` fails under old binaries because `FlyerConfig.extra="forbid"`; deploy must add config only after code deploy, or leave config absent and rely on defaults.
- Tests must cover absent recovery config, explicit valid recovery config, unknown recovery field rejection, and the fact that absent config does not enable the timer.

## Schemas

Add typed audit variants near current Flyer audit classes:

```python
class FlyerRecoveryIncidentOpened(_BaseEntry):
    type: Literal["flyer_recovery_incident_opened"]
    incident_id: str
    failure_class: str
    severity: Literal["info", "warning", "critical"]
    project_id: Optional[str] = None
    source_fingerprint: str
    ack_dedupe_key: str = ""
    chat_id_hash: str = ""
    evidence_quality: Literal["strong", "weak", "missing"] = "missing"
    mode: str = ""

class FlyerRecoveryCustomerAckAttempted(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_attempted"]
    incident_id: str
    ack_attempt_id: str
    ack_dedupe_key: str
    source_fingerprint: str
    chat_id_hash: str
    evidence_quality: Literal["strong", "weak", "missing"]
    mode: str
    copy_policy_template_id: str
    message_sha256: str

class FlyerRecoveryCustomerAckSent(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_sent"]
    incident_id: str
    ack_attempt_id: str
    ack_dedupe_key: str
    source_fingerprint: str
    chat_id_hash: str
    evidence_quality: Literal["strong", "weak", "missing"]
    mode: str
    outbound_message_id: str

class FlyerRecoveryCustomerAckFailed(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_failed"]
    incident_id: str
    ack_attempt_id: str
    ack_dedupe_key: str
    source_fingerprint: str
    chat_id_hash: str
    evidence_quality: Literal["strong", "weak", "missing"]
    mode: str
    status: str
    error: str = Field(default="", max_length=1000)

class FlyerRecoveryCustomerAckUncertain(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_uncertain"]
    incident_id: str
    ack_attempt_id: str
    ack_dedupe_key: str
    source_fingerprint: str
    chat_id_hash: str
    evidence_quality: Literal["strong", "weak", "missing"]
    mode: str
    status: str
    error: str = Field(default="", max_length=1000)

class FlyerRecoveryCustomerAckSuppressed(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_suppressed"]
    incident_id: str
    ack_dedupe_key: str = ""
    source_fingerprint: str = ""
    chat_id_hash: str = ""
    evidence_quality: Literal["strong", "weak", "missing"] = "missing"
    mode: str = ""
    reason: str

class FlyerRecoveryRepairBundleWritten(_BaseEntry):
    type: Literal["flyer_recovery_repair_bundle_written"]
    incident_id: str
    bundle_path: str

class FlyerRecoveryDeployGate(_BaseEntry):
    type: Literal["flyer_recovery_deploy_gate"]
    incident_id: str
    gate: str
    passed: bool
    detail: str = Field(default="", max_length=1000)

class FlyerRecoveryResolved(_BaseEntry):
    type: Literal["flyer_recovery_resolved"]
    incident_id: str
    resolution: Literal["suppressed", "customer_ack_sent", "repair_queued", "manual_required", "deployed"]
```

Add these variants to the `LogEntry` union and `__all__`.

Recovery state is not a Pydantic public schema initially; keep it local to `recovery.py` with tests. If it grows beyond this PR, promote it into `schemas.py`.

## Recovery State

Path: `/opt/shift-agent/state/flyer/recovery_incidents.json`

Shape:

```json
{
  "schema_version": 1,
  "incidents": [
    {
      "incident_id": "FRI20260523-0001",
    "status": "open",
      "failure_class": "concept_generation_failed",
      "severity": "warning",
      "source_fingerprint": "...",
      "ack_dedupe_key": "...",
      "project_id": "F0065",
      "chat_id_hash": "sha256:...",
      "sender_phone_hash": "sha256:...",
      "root_message_id": "msg-id-or-empty",
      "evidence_quality": "strong",
      "provider_message_id_hash": "sha256:...",
      "first_seen": "2026-05-23T16:00:00Z",
      "last_seen": "2026-05-23T16:00:00Z",
      "ack": {
        "status": "none",
        "attempt_id": "",
        "reserved_at": "",
        "sent_at": "",
        "outbound_message_id": "",
        "status_detail": ""
      },
      "codex": {
        "status": "none",
        "bundle_path": ""
      }
    }
  ]
}
```

All read-modify-write operations happen under `FileLock(Path(str(path) + ".lock"))`.

`source_fingerprint` must be stable across repeated audit rows. Build it from:

- failure class,
- project id or chat id hash,
- root inbound provider message id when available,
- canonical failure source such as subprocess prefix or asset id.

Do not include timestamps, full free-form details, or outbound message bodies.

`ack_dedupe_key` is:

```text
sha256(customer_or_chat_identity + "\0" + project_or_root_message_id + "\0" + failure_class + "\0" + canonical_failure_source)
```

For the same `incident_id` or `ack_dedupe_key`, `sent`, `failed`, and `uncertain` are terminal for automatic customer sends. Cooldown is only a cross-incident duplicate guard; it is never permission to resend the same incident. A new attempt requires an explicit operator state clear or a new incident fingerprint.

## Recovery Classifier

`src/agents/flyer/recovery.py` owns pure functions:

```python
def iter_recent_decisions(log_path: Path, since: datetime) -> Iterator[dict]: ...
def classify_decision(row: dict, projects: dict[str, dict]) -> RecoverySignal | None: ...
def fingerprint_signal(signal: RecoverySignal) -> str: ...
def merge_signal_into_state(state: dict, signal: RecoverySignal, now: datetime) -> MergeResult: ...
```

`RecoverySignal` includes `InboundEvidence`. Preflight may verify that evidence parsing is available, but send eligibility always uses the evidence attached to the exact incident being acknowledged.

Failure evidence mapping:

| Failure class | Existing source | Classifier rule |
|---|---|---|
| `bridge_send_failed` | `cf_router_intercepted.detail` contains `ack_error=` with nonblank error, or `flyer_delivery_failed` with non-sent status | classify if the detail belongs to a Flyer reason and status is not `send_uncertain` |
| `concept_generation_failed` | `cf_router_intercepted.reason=flyer_primary_failed` and detail contains `concept_generation_failed`, `revision_regeneration_failed`, or `regeneration_failed` | project id required |
| `preview_delivery_failed` | `send_flyer_concept_previews` failures reflected as `ack_error=` or asset `flyer_delivery_failed` | project id or asset id required |
| `provider_unavailable` | `flyer_source_edit_preflight` text in details, source-edit/manual queue reason state | classify as warning; customer ack usually suppressed if already queued |
| `state_transition_failed` | subprocess wrapper detail prefixes `exit=`, `*_json_parse_failed`, `*_status_failed`, `select_failed`, `update=` with failed status | project id required when available |
| `clarification_loop` | repeated same class of customer-visible Flyer prompt for same chat/project inside scan window | requires at least 3 prompts and customer-originated evidence |
| `replay_loop_suspected` | repeated inbound dedupe key or repeated outbound body for same chat in short window | severity critical; customer ack suppressed by default |
| `manual_queue_stale` | project status `manual_edit_required` older than threshold and no completion/closure | no ack unless customer asked for status |

The classifier must skip unknown/malformed rows. It may return suppressed signals for observability, but it must not raise out of the watchdog.

## Customer-Origin Evidence

Define:

```python
class InboundEvidence(NamedTuple):
    provider_message_id: str
    chat_id: str
    sender_hash: str
    ts: str
    message_shape: Literal["text", "media", "text_media", "unknown"]
    customer_originated: bool
    evidence_quality: Literal["strong", "weak", "missing"]
```

Strong evidence for a specific incident requires a provider/root message id plus a chat id plus either:

- raw inbound/dispatcher row that explicitly marks inbound customer traffic, or
- cf-router event metadata with non-agent source marker and not fromMe.

Weak evidence can classify incidents but cannot send customer acknowledgements.

Missing evidence suppresses acknowledgements and writes `flyer_recovery_customer_ack_suppressed` with reason `missing_customer_origin_evidence`.

Tests must include real transcript shapes:

- normal customer inbound text,
- customer media plus text,
- outbound echo/fromMe true,
- replayed inbound with same provider id,
- row with no provider id.

## Ack Outbox

The ack send algorithm:

1. Load config and run preflight if mode may send.
2. Before evaluating send eligibility, finalize stale `reserved` ack attempts older than `ack_reservation_stale_minutes` as `uncertain`; `uncertain` is terminal until operator action.
3. Under recovery state lock, find incident and compute `ack_dedupe_key`.
4. If the same incident or dedupe key has `ack.status in {"sent", "failed", "uncertain"}`, suppress automatic send permanently.
5. If any different incident has the same key with `ack.status in {"reserved", "sent", "failed", "uncertain"}` inside cooldown, suppress as cross-incident duplicate.
6. Require `incident.evidence_quality == "strong"` for that exact incident.
7. Render copy, run `lint_recovery_copy(...)`, and store `copy_policy_template_id` plus `message_sha256`.
8. If allowed, write:

```json
"ack": {
  "status": "reserved",
  "attempt_id": "FRA...",
  "reserved_at": "...",
  "message_sha256": "..."
}
```

9. Append `flyer_recovery_customer_ack_attempted`.
10. Release lock.
11. Call `actions.send_flyer_text(chat_id, message)`.
12. Reacquire lock and write:
   - `sent` if ok,
   - `uncertain` if status/detail contains `send_uncertain`,
   - `failed` otherwise.
13. Append corresponding audit row.

Crash behavior:

- Crash after reserve before send: next run sees `reserved` and older than threshold. It marks `uncertain` and suppresses automatic retry.
- Crash after send before final write: next run still sees `reserved`; it marks `uncertain` and suppresses automatic retry.
- `sent`, `failed`, and `uncertain` are terminal automatic states. No automatic recovery ack retry exists in v1. Operator can inspect and clear state if needed.

This intentionally favors under-sending over duplicate customer messages.

## Copy Policy

`recovery.py`:

```python
FORBIDDEN_CUSTOMER_TERMS = {
    "provider", "manual queue", "source-preserving", "operator", "audit",
    "stack trace", "traceback", "pytest", "codex", "hermes", "deploy",
}

def lint_recovery_copy(text: str, failure_class: str, followup_recorded: bool) -> CopyLintResult: ...
```

Rules:

- no internal terms,
- no project ids,
- no SLA or exact timing,
- no payment implication unless the failure class is payment-specific and the text is payment-safe,
- no cause diagnosis,
- no promise to follow up unless a durable incident exists and either repair queued or manual-required state exists.

Class-aware templates:

- Generic tracked incident:
  `Flyer Studio\n------------\nI have your request. I am checking it now.`
- Manual-required with status check:
  `Flyer Studio\n------------\nI have your request. I am still checking this edit.`
- Repair/manual follow-up already durable:
  `Flyer Studio\n------------\nI have your request. I am checking it now and will follow up here with the next step.`
- Replay loop suspected:
  no customer ack by default.
- Payment gate:
  no recovery ack; existing payment copy owns it.

## Preflight

`src/agents/flyer/scripts/flyer-recovery-preflight`

Inputs:

- `--json`
- `--text`
- `--config-path`
- `--state-root`
- `--require-send-safe`

Checks:

- config loads and reports `flyer.enabled`, `flyer.recovery.mode`, and `flyer.recovery.enable_timer`.
- `hermes-gateway` active.
- bridge health connected and queue length 0.
- current send emitters known: `safe_io.bridge_post`, `send_flyer_text`, `send-flyer-package`.
- fail `--require-send-safe` if a deployed Flyer send emitter is found outside the guarded allowlist.
- `safe_io` test/no-live-send guard present.
- active timers do not include known disabled incident timers unless explicitly allowed.
- `TimeoutStopSec >= 240s`.
- recovery state directory exists or can be created by `shift-agent`.
- decisions log readable and not empty.
- customer-originated evidence parser available; per-incident evidence still gates sends.

Exit codes:

- `0`: pass.
- `2`: config/input problem.
- `3`: send side effects unsafe.
- `4`: reserved for future Codex lane checks.

In `observe`, failing send-safety checks are warnings. In `customer_ack`, they block side effects.

## Watchdog CLI

`src/agents/flyer/scripts/flyer-recovery-watchdog`

Args:

- `--config-path`
- `--log-path`
- `--project-state-path`
- `--customer-state-path`
- `--recovery-state-path`
- `--bundle-dir`
- `--mode` override for tests only
- `--dry-run`
- `--write-repair-bundle`
- `--incident-id`
- `--json`
- `--text`

Algorithm:

1. Load `Config`.
2. If mode `off`, print status and exit 0.
3. Load project/customer/recovery state.
4. Scan `decisions.log` over `scan_window_minutes`.
5. Run classifier and merge incidents under lock.
6. If mode can send, run preflight with send safety. If failed, suppress ack.
7. For each open incident up to `max_incidents_per_run`:
   - decide ack eligibility,
   - reserve/send/finalize ack if allowed.
8. If `--write-repair-bundle --incident-id <id>` is provided, write exactly one repair bundle for that incident and exit.
9. Print summary.

The timer-driven watchdog must never call Codex directly and must never write Codex lane requests. Repair bundle writing is an explicit operator CLI action in v1.

## Repair Bundle

Bundle dir: `/opt/shift-agent/state/flyer/recovery_bundles/`

File name: `<incident_id>.json`

Contents:

```json
{
  "schema_version": 1,
  "incident_id": "FRI...",
  "failure_class": "concept_generation_failed",
  "severity": "warning",
  "created_at": "...",
  "sanitized_context": {
    "project_id": "F0065",
    "chat_id_hash": "sha256:...",
    "sender_phone_hash": "sha256:...",
    "root_message_id_hash": "sha256:..."
  },
  "audit_excerpt": [],
  "project_excerpt": {},
  "suspected_code_paths": [],
  "reproduction_hints": [],
  "safety_contract": [
    "Do not run live bridge sends.",
    "Use temp/copied state fixtures.",
    "Customer copy requires recovery copy lint.",
    "Production deploy requires PR review and deploy gate."
  ]
}
```

V1 does not write queue/lane request files from the timer. If a later design adds a lane request, it must be inert data only, contain no executable fields, include one request per incident fingerprint, and require explicit operator approval before any consumer acts.

## No-Live-Send Guard

The current `safe_io.bridge_send_blocked_by_test_context()` blocks pytest. Add a more general guard if needed:

```python
if os.environ.get("FLYER_RECOVERY_NO_LIVE_SEND") == "1":
    return "refusing bridge send under FLYER_RECOVERY_NO_LIVE_SEND"
```

Apply to `bridge_post`, `bridge_send_media`, and `bridge_send_cta`.

The Codex lane consumer, when designed, must set this env var and use copied state fixtures. v1 tests assert the guard works outside pytest.

## Systemd

Service:

```ini
[Unit]
Description=Flyer Studio recovery watchdog
After=hermes-gateway.service

[Service]
Type=oneshot
User=shift-agent
Group=shift-agent
Environment=HOME=/opt/shift-agent
Environment=FLYER_RECOVERY_NO_LIVE_SEND=0
ExecStart=/usr/local/bin/flyer-recovery-watchdog --text
StandardOutput=append:/opt/shift-agent/logs/flyer-recovery-watchdog.log
StandardError=append:/opt/shift-agent/logs/flyer-recovery-watchdog.log
```

Timer:

```ini
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=flyer-recovery-watchdog.service
```

Deploy behavior:

- Install service/timer files.
- Enable timer only if config loads, `flyer.recovery.enable_timer=true`, and `flyer.recovery.mode != "off"`.
- Default absent config means `off`, so deploy does not create a new recurring production actor.
- Smoke verifies unit syntax but does not require timer enabled when mode is `off`.

## Deploy And Re-Enable Gate

Deploy gate checks:

- focused tests pass,
- py_compile pass,
- `git diff --check` pass or only documented pre-existing warnings,
- `shift-agent-smoke-test.sh` pass,
- deployed tag/hash matches reviewed build,
- `safe_io` no-live-send/test guard present on VPS,
- `hermes-gateway` active after restart,
- bridge health connected and queue length 0,
- `flyer-recovery-preflight --text` passes for observe,
- 15-minute watch window:
  - zero unexpected Flyer outbound rows,
  - zero repeated inbound fingerprints,
  - zero repeated recovery acks,
  - bridge queue length remains 0.

Re-enable is separate:

- Do not change `flyer.enabled` in deploy.
- Re-enable only after the watch window passes and the operator explicitly decides to re-enable Flyer.
- If re-enabled, run a second 15-minute watch window.

## Tests

`tests/test_flyer_recovery.py`

- classify each failure class from representative rows.
- skip malformed rows.
- stable fingerprint ignores timestamps/free-form changing details.
- ack dedupe key stable across duplicate timer runs.
- copy lint blocks forbidden/internal terms.
- copy lint blocks project ids and timing promises.
- customer-origin evidence strong/weak/missing cases.
- terminal ack states (`sent`, `failed`, `uncertain`) suppress automatic sends forever for the same incident/key.
- stale `reserved` older than `ack_reservation_stale_minutes` becomes terminal `uncertain`.

`tests/test_flyer_recovery_watchdog.py`

- `off` exits without state write.
- `observe` opens incidents but sends nothing.
- `customer_ack` with `flyer.enabled=false` suppresses; v1 has no break-glass send path.
- reserve before send.
- crash-after-reserve marks/suppresses retry.
- crash-after-send-before-final-write followed by a run after cooldown expiry still sends zero messages.
- fake `send_uncertain` becomes `ack_uncertain`.
- `sent`, `failed`, `uncertain`, stale `reserved`, weak evidence, missing provider id, fromMe echo, and replayed inbound id all produce zero additional sends across repeated timer cycles with time advanced past cooldown.
- duplicate audit rows do not produce duplicate acks.
- `--write-repair-bundle --incident-id` writes one bundle for the target incident.
- duplicate timer runs do not write repair bundles or lane requests.

`tests/test_safe_io_bridge_post.py`

- `PYTEST_CURRENT_TEST` blocks text/media/CTA sends.
- `FLYER_RECOVERY_NO_LIVE_SEND=1` blocks text/media/CTA sends outside pytest.
- explicit test override remains scoped to pytest only; no override for recovery guard.

`tests/test_flyer_scripts_static.py`

- watchdog and preflight scripts installed by deploy.
- systemd unit files included and verified list extended.
- no repo-local `flyer-codex-recovery-runner` is installed in v1.
- recovery scripts import via flat deployed layout.

`tests/test_flyer_schemas.py`

- new audit variants dispatch through `LogEntry`.
- absent `flyer.recovery` config loads with defaults.
- absent `flyer.recovery` leaves mode `off` and `enable_timer=false`.
- unknown `flyer.recovery` field fails.

Focused verification:

```powershell
python -m pytest tests/test_flyer_recovery.py tests/test_flyer_recovery_watchdog.py tests/test_flyer_scripts_static.py tests/test_safe_io_bridge_post.py tests/test_flyer_schemas.py -q
python -m py_compile src\agents\flyer\recovery.py src\platform\safe_io.py src\platform\schemas.py
git diff --check
```

## Build Order

1. Add tests for schemas/config and recovery pure helpers.
2. Implement schemas/config and `recovery.py`.
3. Add safe_io no-live-send guard tests and implementation if needed.
4. Add watchdog/preflight CLI tests.
5. Implement watchdog/preflight scripts.
6. Add systemd/deploy static tests.
7. Wire deploy/smoke install.
8. Run focused verification.
9. Create PR.
10. Run two parallel PR reviews and apply fixes.

## Design Review Fixes Applied

- Removed timer-driven `codex_draft`; v1 writes repair bundles only through explicit operator CLI action.
- Removed disabled-mode break-glass customer sends from v1. `flyer.enabled=false` suppresses all customer-visible recovery acks.
- Changed recovery default to `off` and added `enable_timer=false`, so deploy installs units without creating a recurring actor by default.
- Made ack terminal states explicit: `sent`, `failed`, and `uncertain` suppress automatic retries permanently for the same incident/key.
- Added `ack_reservation_stale_minutes` and stale-reservation finalization before any send eligibility.
- Made customer-origin evidence incident-bound and persisted on the incident; preflight cannot substitute for per-incident strong evidence.
- Added copy lint as a mandatory pre-reservation step with template id and message hash.
- Removed generic follow-up promises unless a durable repair/manual follow-up path already exists.
- Added send-emitter allowlist preflight behavior.
- Added repeated-timer-cycle tests across cooldown for terminal states, stale reservations, weak/missing evidence, fromMe echo, replayed inbound, and repair bundle non-duplication.
- Added idempotency/evidence anchors to every recovery ack audit variant.

## Non-Goals For V1

- No direct Codex source edits on `main-vps` from the watchdog.
- No automatic production deploy by the customer runtime.
- No automatic re-enable of Flyer.
- No new image/provider behavior.
- No Cockpit UI beyond JSON/audit/log state.
