# Agent #41 Owner Wellbeing v0.1 — quiet-hours guard (plan)

**Drift-check tag:** `extends-Hermes`

Adds three pieces of custom infrastructure on top of the deployed
`shift-agent-notify-owner` chokepoint: a config block (`OwnerWellbeingConfig`),
an audit variant (`OwnerNotificationSuppressed`), and a quiet-window guard
in `main()`. No deviation from Hermes conventions; uses existing pydantic +
audit-chain + chokepoint patterns. Weekly load summary patch deferred to v0.2.

**Portfolio reference:** `docs/portfolio.md` line 1078 (Agent #41 v0.1 spec) +
build-priority list at line 1097-1099 (highest-ROI tractable next build).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-41-owner-wellbeing-v0-1.json`
(timestamp 2026-05-09T16:06:16Z, drift-tag = extends-Hermes, 7 [Hermes] / 3 [net-new]).

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Caller invokes `shift-agent-notify-owner` chokepoint | `[Hermes]` | Already deployed; 18+ call sites in backup/deploy/health/smoke/watchdog |
| 2 | Config load via `Config.model_validate` (pydantic v2 + extra="forbid") | `[Hermes]` | Already deployed pattern |
| 3 | `customer_now(tz)` from `safe_io.py` | `[Hermes]` | Already deployed at `safe_io:297` |
| 4 | **Quiet-window logic (cross-midnight aware)** | **`[net-new]`** | New helper in script (~15 LOC) |
| 5 | **`OwnerNotificationSuppressed` audit variant** | **`[net-new]`** | New `_BaseEntry` subclass (~10 LOC schema + ~5 LOC emit) |
| 6 | `pushover_send` / `whatsapp_fallback` / `append_notify_failed` | `[Hermes]` | Already in `shift-agent-notify-owner` |
| 7 | `safe_io.ndjson_append` audit-chain chokepoint | `[Hermes]` | Already deployed at `safe_io:258` |
| 8 | `EXIT_OK` / `EXIT_DEPENDENCY_DOWN` exit-code conventions | `[Hermes]` | Already in `exit_codes.py` |
| 9 | **`OwnerWellbeingConfig` pydantic model + plug into `Config`** | **`[net-new]`** | New schema block (~25 LOC including validators) |
| 10 | **Test file with 6 scenarios** | **`[net-new]`** | ~150 LOC mirroring `test_notify_owner_with_fallback.py` pattern |

7/10 `[Hermes]`, 3/10 `[net-new]`. Below the 50% red-flag threshold.

**Awesome-hermes-agent ecosystem check:** N/A — quiet-hours business logic
is per-customer SMB ops, not Hermes substrate. No upstream skill exists.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 (schema work + script work + test work). Files Read
this session before drafting:

- ✅ Read `src/agents/shift/scripts/shift-agent-notify-owner` (full file, 175 LOC) — verified the chokepoint shape, `main()` flow at lines 132-171, exit-code conventions, and the natural insertion point for the guard (after `cfg = load_config()` at line 146, before `pushover_send` at line 156)
- ✅ Read `src/platform/schemas.py` lines 416-437 (`DailyBriefConfig`) — pattern to mirror for `OwnerWellbeingConfig`: `model_config = ConfigDict(extra="forbid")` + regex-validated time fields + `_validate_*_strptime` belt-and-suspenders validators
- ✅ Read `src/platform/schemas.py` lines 1670-1706 (`HealthCheckFailure`, `BriefAttempted`) — `_BaseEntry` subclass pattern + `type: Literal["..."]` discriminator for the new `OwnerNotificationSuppressed` variant
- ✅ Read `src/platform/safe_io.py` lines 255-309 — confirmed `customer_now(tz_name)` returns timezone-aware datetime, `ndjson_append(path, entry_json)` is the durable audit-write chokepoint with O_APPEND + fsync
- ✅ Read `tests/test_notify_owner_with_fallback.py` lines 1-80 — test pattern: subprocess-invoke a shell script, assert exit code + audit-log content; will mirror this for the new tests with HTTP-stub for Pushover
- ✅ Read `src/platform/schemas.py` lines 2630-2719 — confirmed `LogEntry = Annotated[Union[...]]` discriminated union pattern. Every variant requires explicit `Annotated[Variant, Tag("type_literal")]` row; sibling subclasses are NOT auto-discovered. The B1 fix (R2 plan review) explicitly adds this row, otherwise the new variant routes to `_UnknownLogEntry` silently per the picker fallback at lines 2630-2638.

**Deployed-pattern compliance:**
- Config: pydantic v2 + `extra="forbid"` ✓ (matches `DailyBriefConfig`)
- Audit: `_BaseEntry` subclass + `type: Literal[...]` slotted into existing union ✓
- Audit-write chokepoint: `safe_io.ndjson_append` ✓ (deployed pattern)
- Time semantics: `customer_now(cfg.customer.timezone)` for owner-local time ✓
- Tests: subprocess-invoke + assert on exit + assert on audit/state mutations ✓
- Sender identity / SKILLs / dispatcher: N/A (this is a script-internal guard, not LLM-routed)

---

## Scope boundary (anti-over-engineering)

### In scope (~205 LOC across 3 files)

**Files modified:**

| File | Change | LOC |
|---|---|---|
| `src/platform/schemas.py` | Add `OwnerWellbeingConfig` + plug into `Config` + add `OwnerNotificationSuppressed` `_BaseEntry` subclass next to `HealthCheckFailure` (line 1670) + **explicitly add `Annotated[OwnerNotificationSuppressed, Tag("owner_notification_suppressed")]` to the `LogEntry` discriminated union at `schemas.py:2640+`** (without this, the variant routes to `_UnknownLogEntry` silently — verified that union does NOT auto-discover sibling subclasses) | ~45 |
| `src/agents/shift/scripts/shift-agent-notify-owner` | Add `_in_quiet_window(now, cfg)` helper + guard call in `main()` | ~20 |
| `tests/test_owner_wellbeing_quiet_hours.py` (NEW) | 6 test functions | ~150 |

**Schema shape** (locked at plan time so reviewers can challenge):

```python
class OwnerWellbeingConfig(BaseModel):
    """Quiet-hours rule + future weekly-load summary settings (Agent #41)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False                              # off by default
    quiet_start: str = Field(default="22:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(default="06:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_days: list[Literal["mon","tue","wed","thu","fri","sat","sun"]] = Field(
        default_factory=lambda: ["mon","tue","wed","thu","fri","sat","sun"],
        min_length=1,
    )
    critical_priority_threshold: int = Field(default=1, ge=-2, le=2)
    # priority < threshold → suppressed; priority >= threshold → always send.
    # default 1 means: suppress -2/-1/0 (silent/quiet/normal); allow 1/2 (high/emergency).
```

**Audit variant shape** (R2-M1: structured fields, not concatenated string — matches `BriefAttempted.sections_included` convention for replay/grep parsability):

```python
class OwnerNotificationSuppressed(_BaseEntry):
    """Quiet-hours guard suppressed a non-critical Pushover/WhatsApp send."""
    type: Literal["owner_notification_suppressed"]
    title: str
    priority: int
    quiet_start: str   # HH:MM (matches OwnerWellbeingConfig.quiet_start)
    quiet_end: str     # HH:MM
    quiet_days: list[str]  # e.g. ["mon","tue","wed","thu","fri"]
    suppressed_at_local: str  # owner-tz HH:MM:SS for traceability
```

**Test cases (9):**

1. `test_cross_midnight_window` — quiet_start=22:00, quiet_end=06:00, priority=0, now=02:00 local → suppressed (the trickiest case; place first in file so a reader sees it on open per R1-NIT-2)
2. `test_priority_below_threshold_in_window_suppressed` — quiet=enabled, priority=0, threshold=1, time inside window → no Pushover call, audit emitted, exit 0
3. `test_priority_at_threshold_in_window_sends` — quiet=enabled, priority=1, threshold=1, time inside window → Pushover called normally (R2-M3: pins the `>=` comparison operator)
4. `test_priority_above_threshold_in_window_sends` — quiet=enabled, priority=2, threshold=1, time inside window → Pushover called normally
5. `test_outside_window_sends` — quiet=enabled, priority=0, time OUTSIDE window → Pushover called normally
6. `test_same_day_window` — quiet_start=13:00, quiet_end=15:00 (NOT cross-midnight), priority=0, now=14:00 → suppressed (R2-M2: separate code path — `start <= end` branch)
7. `test_boundary_at_quiet_start_suppressed` — quiet_start=22:00, now=22:00 sharp, priority=0 → suppressed by the `now >= start` rule (R2-M2: pins boundary semantics)
8. `test_weekday_filter_excludes_weekend` — quiet_days=[mon-fri], priority=0, now=Saturday inside time-window → Pushover called normally (not in quiet days)
9. `test_disabled_short_circuits` — `enabled=False` → no time-window evaluation, no audit, Pushover called normally

### Explicitly out of scope (rejected at plan time)

| Considered | Decision | Reason |
|---|---|---|
| Weekly owner-load summary section in Daily Brief | **DEFERRED to v0.2** | portfolio.md spec lists both quiet-hours + weekly summary, but they're independent features. v0.1 = quiet-hours alone (smaller surface, sharper PR). v0.2 patches `send-daily-brief` to add a `weekly_owner_load` BriefSection. |
| Queue suppressed messages for delayed delivery | **REJECTED** | Adds state-store + cron complexity. Audit chain is the trail; senders escalate priority if it must get through. |
| Per-channel quiet hours (Pushover-quiet but WhatsApp-loud) | **REJECTED** | Both channels go through the same chokepoint; splitting requires per-channel config blocks. Owner can use Pushover's own per-app silence as a per-channel knob if needed. |
| LLM-driven "is this critical?" classifier | **REJECTED** | Vizora-pattern credit-burn risk; deterministic priority threshold is cheaper, faster, auditable. |
| Holiday calendar / vacation override | **REJECTED for v0.1** | Weekday filter is the hypothesized 95% case but unverified. R1-MEDIUM escalation trigger: **if v0.1 produces a customer ask for date-overrides within 30 days of ship, escalate to v0.2.** Don't expand v0.1 scope speculatively. |

### Deferred (separate commits if ever needed)

- v0.2: weekly owner-load summary BriefSection patch on `send-daily-brief`
- v0.3: per-customer holiday calendar override
- v0.3: queue-and-deliver-on-window-end if a customer requests it

---

## Verification + commit shape

- **Run on srilu**: `pytest tests/test_owner_wellbeing_quiet_hours.py -v` against tarballed working tree
- **Pass criterion**: 6/6 pass on first run; existing `test_notify_owner_with_fallback.py` still 100% green (no regression)
- **Commit shape**: ONE commit, message `feat(agent-41): owner wellbeing v0.1 — quiet-hours guard`, ~205 LOC
- **Deploy**: tarball + `shift-agent-deploy.sh` — script + `schemas.py` propagate via the existing `install -m 644 src/platform/schemas.py /opt/shift-agent/schemas.py` line. Default config opts out (`enabled: False`), so deploy is no-op until owner sets the config.

---

## Approval needed

Plan reviewers must explicitly approve before design phase. Specific decisions
to challenge:

1. **`critical_priority_threshold` default = 1** (suppress -2/-1/0; allow 1/2). Some operators may want default=2 (only emergencies bypass). Plan defaults are conservative; owner can tighten.
2. **`enabled: False` default** — agent ships off; owner explicitly opts in. Matches Tier-2 scaffold convention from `project_portfolio_status.md`.
3. **Quiet-window crossing midnight** — implementing as `start > end` ⇒ `now >= start OR now < end`. Edge case at exactly midnight handled by the `>=` / `<` symmetry.
4. **Audit variant placement** — under `_BaseEntry` next to `HealthCheckFailure` (line 1670 area) since both are observability/skip events, not state-mutation events.
5. **No WhatsApp fallback bypass** — when suppressed, neither Pushover nor WhatsApp fallback fires. Senders that need cross-channel reliability use priority ≥ threshold.
