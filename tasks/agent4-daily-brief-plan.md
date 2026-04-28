# Agent #4 Daily Brief — Plan v2 (post 5-review synthesis)

## Context

Agent #4 of 20 SMB-Agents (Tier 1 per `docs/portfolio.md.txt`). Purpose: every morning at owner-configured time, deliver a structured WhatsApp message to the owner's self-chat summarizing yesterday's activity + today's outlook. Read-only synthesis; no approval gate; no outbound to anyone other than owner.

**Constraints:** ~150-word cap, all numbers must trace to source log (no LLM hallucinations), customer-tz aware (DST-correct), idempotent under crash/reboot.

**v0.1 scope discipline:** template-only output (no LLM), single-customer (Triveni), single channel (WhatsApp via existing bridge), one log source (Shift Agent's `decisions.log`) but with multi-source-ready abstraction.

**Changes from v1 (5-review synthesis):**
- Snake_case dir name `daily_brief/` (PEP 8)
- `BriefAttempted` pre-log idempotency (mirrors `OutboundAttempted` in `send-coverage-message:323`)
- Sentinel file `last-brief-sent.json` instead of NDJSON-scan
- "Quiet day" brief instead of `BriefSkipped(no_activity)`
- Degraded-mode warning in WhatsApp body, not just Pushover
- Catch-up window of 3h with explicit `catchup_expired` skip
- Schema cleanups (regex bug fix, JID naming, drop YAGNI fields)
- `LogSource` abstraction in `src/platform/log_source.py` for forward-compat with Agents #2/#3/#5
- Full systemd service hardening + ConditionPathIsExecutable
- Explicit deploy.sh diff
- Cockpit Briefs route deferred to v0.1.1 follow-up

---

## Architecture

```
[systemd timer: OnUnitActiveSec=15min, Persistent=true]
    └─→ /usr/local/bin/send-daily-brief
           ├─ assert_local_disk(/opt/shift-agent/state/)
           ├─ load Config (yaml.safe_load + Config.model_validate)
           ├─ check disabled.flag → BriefSkipped(disabled), exit 0
           ├─ self-gate: customer_now(tz) ∈ [brief_time, brief_time+15min)?
           │   ├─ before window: exit 0 silently (don't log — fires 95x/day)
           │   ├─ in window: proceed
           │   └─ after window: catchup logic (see § Catch-up)
           ├─ acquire FileLock(state/last-brief-sent.json.lock)
           ├─ idempotency: read state/last-brief-sent.json — if brief_date == today, BriefSkipped(already_sent), exit 0
           ├─ aggregate yesterday from all log sources (LogSource abstraction)
           ├─ aggregate today from pending.json + roster.json
           ├─ assess data quality: any sources failed? → degraded_mode flag
           ├─ render template (with degraded_mode header if applicable)
           ├─ write BriefAttempted to decisions.log (PRE-send idempotency anchor)
           ├─ bridge_post(owner.self_chat_jid, rendered_text)  [retry once after 5s]
           ├─ on success:
           │     ├─ atomic_write_json state/last-brief-sent.json {brief_date, ts, msg_id}
           │     └─ ndjson_append BriefSent
           └─ on failure: ndjson_append BriefSendFailed + shift-agent-notify-owner Pushover
              + write notify-failed.log on Pushover-also-fails (mirrors send-coverage-message:155)
```

---

## File-by-file scope

### New files (under `src/agents/daily_brief/`)

```
src/agents/daily_brief/
├── __init__.py                               (empty)
├── scripts/
│   └── send-daily-brief                      (Python script, the entry point)
├── systemd/
│   ├── send-daily-brief.service              (oneshot, full hardening)
│   └── send-daily-brief.timer                (every 15min, self-gating, Persistent=true)
└── templates/
    └── daily_brief.txt                       (fixed-structure template + {field} interpolation)
```

### New platform files

```
src/platform/log_source.py                    (LogSource abstraction — see §Multi-source ready)
```

### Modified platform files

- `src/platform/schemas.py`:
  - Add `BriefSection` Literal alias
  - Add `DailyBriefConfig` sub-model (defaults to enabled with 7am)
  - Add `daily_brief: DailyBriefConfig = DailyBriefConfig()` field to `Config`
  - Add 4 new LogEntry types: `BriefAttempted`, `BriefSent`, `BriefSendFailed`, `BriefSkipped`
  - Drop the `BriefDraftCreated` type from v1 plan (YAGNI — `BriefAttempted` covers the same observability)
  - Extend `LogEntry` discriminated union (4 new entries → 18 total — still O(1) dispatch)
  - Extend `__all__` exports
- `src/agents/shift/scripts/shift-agent-deploy.sh`:
  - Add explicit install lines for `src/agents/daily_brief/` tree
  - Add `systemctl enable --now send-daily-brief.timer || true` after daemon-reload
- `src/agents/shift/config.yaml.template`:
  - Add commented `daily_brief:` block showing defaults + customization examples
- `src/agents/shift/logrotate/shift-agent`:
  - Add `briefs_sent.ndjson` to rotation (monthly, rotate 12)

### New tests

```
tests/
├── test_daily_brief_schemas.py               (Pydantic model validators, defaults, regex correctness)
├── test_daily_brief_aggregation.py           (window slicing, entry counting, multi-source)
├── test_daily_brief_idempotency.py           (BriefAttempted pre-log, sentinel file, lock contention)
├── test_daily_brief_self_gate.py             (window + catchup logic, DST, IST, midnight-cross)
├── test_daily_brief_template.py              (full data, zero-activity, degraded-mode, snapshot)
├── test_daily_brief_failure_modes.py         (corrupt log, missing self_chat_jid, bridge down, Pushover-also-down)
└── test_daily_brief_e2e_lifecycle.py         (skipif-not-deployed full pipeline)
```

Estimated new tests: ~32. VPS pytest baseline rises from 118 → ~150.

---

## Schema additions

### `BriefSection` alias

```python
BriefSection = Literal["yesterday", "today_outlook", "alerts"]
```

### `DailyBriefConfig`

```python
class DailyBriefConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    brief_time: str = Field(
        default="07:00",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",  # FIX: v1 regex `^[0-2]\d:[0-5]\d$` accepted 25:00
    )
    max_words: int = Field(default=150, ge=50, le=500)
    sections: list[BriefSection] = Field(
        default_factory=lambda: ["yesterday", "today_outlook", "alerts"],
        min_length=1,  # FIX: empty list previously allowed
    )
    catchup_window_minutes: int = Field(default=180, ge=15, le=720)  # 3h default

    @field_validator("brief_time")
    @classmethod
    def _validate_brief_time(cls, v: str) -> str:
        # Belt-and-suspenders: regex AND strptime round-trip
        from datetime import datetime
        datetime.strptime(v, "%H:%M")
        return v

# DROPPED from v1:
#   channel: Literal["whatsapp", "telegram"]  — telegram has zero presence, YAGNI
#   force_window_minutes — config-script tight coupling overkill for v0.1
```

Added to `Config`:
```python
daily_brief: DailyBriefConfig = Field(default_factory=DailyBriefConfig)
```

### New LogEntry types

```python
class BriefAttempted(_BaseEntry):
    """Written BEFORE bridge POST. Idempotency anchor (mirrors OutboundAttempted)."""
    type: Literal["brief_attempted"] = "brief_attempted"
    brief_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    attempt_id: str = Field(min_length=1)  # uuid4 per attempt
    word_count: int = Field(ge=0)
    sections_included: list[BriefSection]
    source_count: int = Field(ge=0)         # number of LogSource entries scanned
    degraded_mode: bool = False             # set when any data source was unavailable

class BriefSent(_BaseEntry):
    """Written AFTER bridge 200 + non-empty messageId."""
    type: Literal["brief_sent"] = "brief_sent"
    brief_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    attempt_id: str = Field(min_length=1)   # links back to BriefAttempted
    outbound_message_id: str = Field(min_length=1)  # FIX: empty was allowed
    self_chat_jid: str = Field(min_length=1)        # naming follows OwnerConfig.self_chat_jid

class BriefSendFailed(_BaseEntry):
    type: Literal["brief_send_failed"] = "brief_send_failed"
    brief_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    attempt_id: str = Field(min_length=1)
    error: str                              # FIX: drop max_length=500 (matches OutboundSendFailed.error)
    retry_count: int = Field(ge=0)

class BriefSkipped(_BaseEntry):
    type: Literal["brief_skipped"] = "brief_skipped"
    brief_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: Literal[
        "already_sent",
        "data_unavailable",
        "disabled",
        "catchup_expired",
        "dependency_down",
    ]
    # NOTE: no_activity REMOVED — we always send a "quiet day" brief instead of skipping.
    # NOTE: outside_window REMOVED — script exits 0 silently in non-window fires.
```

LogEntry discriminated union extended with these 4 types (18 total).

### `Config.schema_version`

Stays at `Literal[1]`. New optional fields with defaults are additive — no schema bump. Document rollback in plan: `jq 'del(.daily_brief)' config.yaml` strips the block before reverting code (mirrors the `lid` rollback pattern noted in `schemas.py:92-95`).

---

## Multi-source-ready: `LogSource` abstraction

```python
# src/platform/log_source.py
from pathlib import Path
from typing import Iterator
from schemas import LogEntry

class LogSource:
    """Adapter for an agent's decisions.log. v0.1 has only Shift's; future agents register theirs."""
    def __init__(self, agent_name: str, log_path: Path):
        self.agent_name = agent_name
        self.log_path = log_path

    def iter_entries(self, start_ts, end_ts) -> Iterator[LogEntry]:
        """Yield LogEntry objects whose ts falls in [start_ts, end_ts)."""
        ...

# v0.1 registry (hardcoded one source):
LOG_SOURCES = [
    LogSource("shift", Path("/opt/shift-agent/logs/decisions.log")),
]
```

Hardcoded in v0.1 because we have one agent. When Agent #2/#3/#5 ship, each registers its own source. Daily Brief iterates `LOG_SOURCES` instead of opening `decisions.log` directly. Cost today: ~30 lines. Saves a Daily Brief rewrite when the second agent lands.

---

## Self-gate + catch-up logic

```python
now = customer_now(cfg.customer.timezone)
brief_dt = now.replace(hour=H, minute=M, second=0, microsecond=0)  # H,M from cfg.daily_brief.brief_time
window_end = brief_dt + timedelta(minutes=15)
catchup_end = brief_dt + timedelta(minutes=cfg.daily_brief.catchup_window_minutes)  # default 3h

if now < brief_dt:
    sys.exit(0)  # before window: silent exit (don't log — would fire 95x/day)

if brief_dt <= now < window_end:
    # Normal window — proceed
    pass
elif window_end <= now < catchup_end and not _brief_already_sent_today():
    # Catch-up: VPS was down at brief_time, fire it now anyway
    # Brief body header includes "(catch-up: ${minutes_late} min late)"
    pass
else:
    # Past catchup window — owner gets a Pushover, not a stale brief
    log_brief_skipped(reason="catchup_expired")
    pushover("Daily brief missed today (VPS was down past catchup window)", priority=1)
    sys.exit(0)
```

**Catch-up rationale:** The portfolio doc warns "wrong-time delivery is a real problem (waking owner, missing morning routine)." A brief at 14:00 is jarring. But "owner pays for daily summary, gets nothing on a day after VPS reboot at 09:00" is worse. Compromise: 3h catch-up with explicit "catch-up — N min late" header in the brief body so the owner knows it's not a normal-time delivery.

---

## "Quiet day" brief

If `source_count == 0` (no log entries in window), DO NOT skip. Render with:

```
✓ Yesterday: quiet day — no sick calls, no proposals.
✓ Today: 4 scheduled shifts (see roster).
✓ Pending: 0 needs your attention.
```

Reason: silent days erode trust the same as broken days. Owner can't distinguish "no activity" from "system broken." Always send.

## Degraded-mode header

If any LogSource fails to load (corrupt JSON, OSError), render with prepended warning:

```
⚠ DEGRADED BRIEF — yesterday's activity log unreadable. Outlook only.
```

Lives in the WhatsApp message body, not just out-of-band Pushover (which the owner may have muted). Pushover STILL fires as backup channel.

---

## systemd service + timer (full hardening)

### `send-daily-brief.service`

```ini
[Unit]
Description=Daily Brief — owner morning summary
After=network-online.target hermes-gateway.service
Wants=network-online.target
ConditionPathIsExecutable=/usr/local/bin/render-coverage-template
ConditionPathExists=/opt/shift-agent/config.yaml

[Service]
Type=oneshot
User=shift-agent
Group=shift-agent
EnvironmentFile=/opt/shift-agent/.env
ExecStart=/usr/local/bin/send-daily-brief
StandardOutput=append:/opt/shift-agent/logs/daily-brief.log
StandardError=append:/opt/shift-agent/logs/daily-brief.log

# Security hardening (mirrors shift-agent-tail-logger.service)
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/shift-agent
ProtectHome=read-only
PrivateTmp=true
RuntimeDirectory=shift-agent
```

### `send-daily-brief.timer`

```ini
[Unit]
Description=Daily Brief timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

15-min interval matches the script's self-gate window. If interval changes, window must too — documented in script header.

---

## Deploy.sh diff (explicit)

Add to `install_artifacts()` after current shift install lines:

```bash
# Daily Brief agent
[ -d src/agents/daily_brief/scripts ] && install -m 755 src/agents/daily_brief/scripts/* /usr/local/bin/
[ -d src/agents/daily_brief/systemd ] && install -m 644 src/agents/daily_brief/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
[ -d src/agents/daily_brief/systemd ] && install -m 644 src/agents/daily_brief/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
[ -d src/agents/daily_brief/templates ] && install -m 644 src/agents/daily_brief/templates/* /opt/shift-agent/templates/

# Platform log_source module
install -m 644 src/platform/log_source.py /opt/shift-agent/log_source.py
```

After `systemctl daemon-reload`:

```bash
systemctl enable --now send-daily-brief.timer || true
```

---

## Failure modes (mitigations updated)

| Mode | Detection | Mitigation |
|---|---|---|
| `decisions.log` corrupt | `safe_load_json` returns `corrupt:...` | Set degraded_mode=True; render brief with `⚠ DEGRADED BRIEF` header; outlook still works (independent source). Pushover. Continue, don't fail. |
| Bridge 200 + body parse failure | `bridge_post` returns False | Already covered by send-coverage-message pattern (lines 111-122). Retry. **CRITICAL:** BriefAttempted is logged BEFORE bridge call; on retry, the SAME attempt_id is used, NOT a new one. |
| Bridge total failure | `bridge_post` False both attempts | `BriefSendFailed` + Pushover. Exit `EXIT_DEPENDENCY_DOWN`. |
| Pushover ALSO down | shift-agent-notify-owner non-zero exit | Append to `notify-failed.log` (mirrors send-coverage-message:155). |
| `owner.self_chat_jid` empty | startup config check | `EXIT_NOT_FOUND` + clear error. Pushover with instructions. |
| Brief sent twice (race) | BriefAttempted pre-log + sentinel last-brief-sent.json + FileLock | Three-layer defense: (1) FileLock prevents concurrent runs, (2) BriefAttempted creates an idempotency anchor before send so retry uses same attempt_id, (3) sentinel last-brief-sent.json is atomic-written after BriefSent. |
| Crash between bridge_post and BriefSent append | BriefAttempted exists in log + bridge_post may have succeeded | On next run: scan recent BriefAttempted entries; if found within last 30min and no matching BriefSent, flag as "uncertain" — DO NOT auto-resend. Pushover alert: "Brief send may have succeeded but state is uncertain — check WhatsApp manually." |
| `pending.json` corrupt | load_model returns corrupt | Outlook section degraded; yesterday section unaffected. Note in brief body. |
| `brief_time` misconfigured | Pydantic regex + strptime validator | Reject at config load time. Validate timezone with `ZoneInfo(cfg.customer.timezone)` in DailyBriefConfig validator. |
| Disabled flag set | startup check | `BriefSkipped(reason="disabled")`, exit 0. |
| VPS reboot at 09:00 (past brief_time 07:00) | Persistent=true fires timer immediately | Catch-up window logic kicks in (3h default). Brief fires with "catch-up: 2h late" header. |
| VPS down past catch-up window | now > catchup_end | `BriefSkipped(reason="catchup_expired")` + Pushover. No stale brief sent. |

---

## Test plan (32 tests)

### `test_daily_brief_schemas.py` (8 tests, no skipif)
- `test_brief_time_regex_rejects_invalid_hours` — covers 24:00, 25:00, 99:99
- `test_brief_time_regex_accepts_boundary_times` — 00:00, 23:59
- `test_daily_brief_config_defaults` — all defaults populate correctly
- `test_daily_brief_config_extra_forbid` — typo field rejected
- `test_brief_attempted_validators` — empty attempt_id rejected, negative source_count rejected
- `test_brief_sent_requires_message_id` — empty outbound_message_id rejected (was a v1 bug)
- `test_brief_sections_min_length` — empty sections list rejected
- `test_config_backward_compat_no_daily_brief` — Config loads without daily_brief block, uses defaults

### `test_daily_brief_aggregation.py` (5 tests, skipif Windows)
- `test_yesterday_window_excludes_today_2359`
- `test_yesterday_window_includes_yesterday_0000`
- `test_aggregation_groups_entry_types_correctly`
- `test_aggregation_handles_empty_log` (no entries in window)
- `test_log_source_abstraction_iterates_correctly`

### `test_daily_brief_idempotency.py` (5 tests, skipif Windows)
- `test_brief_attempted_pre_logged_before_bridge_call`
- `test_lock_contention_second_run_blocks_then_skips`
- `test_sentinel_file_prevents_resend_same_day`
- `test_crash_between_send_and_log_no_duplicate_on_rerun` ← MUST-ADD per review #4
- `test_concurrent_overlapping_runs_only_one_send`

### `test_daily_brief_self_gate.py` (6 tests)
- `test_self_gate_before_window_silent_exit`
- `test_self_gate_inside_window_proceeds`
- `test_self_gate_after_window_no_catchup_skipped`
- `test_self_gate_window_crosses_midnight_at_2345` ← MUST-ADD per review #4
- `test_self_gate_dst_spring_forward_handled`
- `test_self_gate_ist_timezone_utc_plus_530`

### `test_daily_brief_template.py` (4 tests, skipif Windows)
- `test_template_full_data_renders`
- `test_template_zero_activity_quiet_day`
- `test_template_degraded_mode_shows_warning_in_body`
- `test_template_snapshot_golden_file` (golden file in tests/snapshots/)

### `test_daily_brief_failure_modes.py` (6 tests, skipif Windows)
- `test_decisions_log_corrupt_degraded_mode`
- `test_bridge_failure_retry_then_succeed`
- `test_bridge_total_failure_pushover_called`
- `test_pushover_also_fails_writes_notify_failed_log`
- `test_missing_self_chat_jid_exit_not_found`
- `test_disabled_flag_short_circuit`

### `test_daily_brief_e2e_lifecycle.py` (1 test, skipif not deployed)
- `test_e2e_full_pipeline_dry_run` — load real fixtures, render template, mock bridge, verify ndjson append + sentinel write

Total: 8 + 5 + 5 + 6 + 4 + 6 + 1 = **35 new tests**.

VPS pytest baseline: 118 → ~153.

---

## Verification (E2E on VPS)

1. Tarball deploy (per `project_vps_deploy_state.md` pattern).
2. Confirm timer enabled + active.
3. Manual trigger: `sudo -u shift-agent /usr/local/bin/send-daily-brief --force` (force flag bypasses self-gate ONLY, not idempotency). Confirm owner receives WhatsApp message in self-chat.
4. Verify `BriefAttempted` then `BriefSent` entries in `decisions.log`.
5. Confirm `state/last-brief-sent.json` written atomically.
6. Run again immediately: confirm `BriefSkipped(reason="already_sent")` logged + no second send.
7. Wait 15+ min: confirm timer fires, self-gate skips silently outside window.
8. Run remote pytest: ~153 pass.

---

## Deferred to v0.1.1 follow-up

- Cockpit Briefs router (`web/backend/app/routers/briefs.py`) with `GET /briefs/recent` + `POST /briefs/send-now`
- Cockpit frontend Briefs section
- Disclosures append: "Shift Agent will WhatsApp you a daily summary at the time you configure"
- Per-agent disable flag (`daily-brief-disabled.flag`) — owner toggle separate from global kill switch
- Briefs SSE health endpoint for cockpit live status

## Deferred to v0.2

- LLM synthesis SKILL (`daily_brief_synthesis`) — narrative prose polish; falls back to template on Hermes/Kimi unavailable; gated by `cfg.daily_brief.llm_synthesis: bool`
- Per-section toggles via cockpit UI
- Brief content per shift role (manager-specific brief)
- Festival calendar awareness (waits for Agent 11)
- Owner engagement tracking (read receipts, response analysis)

---

## Open questions resolved by reviews

- **`--force` flag:** bypasses self-gate window ONLY. Idempotency (sentinel + BriefAttempted) still applies. To bypass idempotency for testing: `--force-resend` (separate, dangerous flag, requires `--force` too).
- **`outside_window` in BriefSkipped Literal:** REMOVED. Script exits 0 silently — logging would spam (95+ events/day).
- **`channel: telegram`:** REMOVED. YAGNI; add when there's a second bridge.
- **`llm_used` field:** REMOVED for v0.1 (consolidated into `BriefAttempted.degraded_mode` semantics). Re-add in v0.2 when LLM synthesis ships.
- **`BriefDraftCreated`:** DROPPED — `BriefAttempted` covers the same observability AND serves as idempotency anchor.
- **Snake_case dir name:** confirmed `daily_brief/` (PEP 8 + Python import-clean).
- **LogEntry union at 18 entries:** OK, no registry needed yet (defer to 25+ entries OR Phase A.5).
- **Cockpit changes in v0.1:** deferred to v0.1.1 — ship core daily-brief first.
