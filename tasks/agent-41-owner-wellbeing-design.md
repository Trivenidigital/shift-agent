# Agent #41 Owner Wellbeing v0.1 — design doc

**Drift-check tag:** `extends-Hermes`

This is the design phase for the approved plan at
`tasks/agent-41-owner-wellbeing-plan.md`. Plan was reviewed by 2 parallel
agents (R1 Hermes-first scope + R2 drift-rule + design soundness); 1
BLOCKER (LogEntry union edit) and 4 MEDIUMs applied as fixups.

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-41-owner-wellbeing-design.json`
(timestamp 2026-05-09T16:12:34Z, drift-tag = extends-Hermes, 4 [Hermes] / 5 [net-new]).

The 5/9 [net-new] count is an artifact of finer-grained step decomposition
at design time vs. plan time (3/10 [net-new]); the SAME logical changes
are tagged. No additional substrate use missed — re-examined per CLAUDE.md
red-flag protocol.

---

## Hermes-first per-step checklist (design granularity)

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Caller invokes chokepoint | `[Hermes]` | Already deployed |
| 2 | argparse | `[Hermes]` | Already in script |
| 3 | `Config.model_validate` | `[Hermes]` | Pydantic v2 deployed |
| 4 | **`_apply_quiet_hours_guard(cfg)` wrapper** | **`[net-new]`** | New private function |
| 5 | **`cfg.owner_wellbeing` field added to `Config`** | **`[net-new]`** | One-line plug-in mirroring `daily_brief: DailyBriefConfig` at schemas:1236 |
| 6 | **`_in_quiet_window` pure helper** | **`[net-new]`** | Cross-midnight + same-day branches |
| 7 | **Audit emit (variant + ndjson_append call)** | **`[net-new]`** | Substrate is `safe_io.ndjson_append`; call site + variant are net-new |
| 8 | Pushover/WhatsApp send + exit codes | `[Hermes]` | Deployed |
| 9 | **Test file with 9 cases** | **`[net-new]`** | Case curation + HTTP-stub pattern mirrored from existing tests |

---

## Drift-rule self-checks

All required reads done at plan time + B1 fix; verified at design time:

- ✅ Read `src/agents/shift/scripts/shift-agent-notify-owner` (full file, 175 LOC) — chokepoint shape + `main()` flow at lines 132-171
- ✅ Read `src/platform/schemas.py` lines 416-437 (`DailyBriefConfig` precedent) + lines 1670-1706 (`HealthCheckFailure`, `BriefAttempted` `_BaseEntry` subclass pattern) + lines 2630-2719 (`LogEntry = Annotated[Union[...]]` discriminated union — every variant requires explicit `Annotated[Variant, Tag("type_literal")]` row, sibling subclasses are NOT auto-discovered)
- ✅ Read `src/platform/safe_io.py` lines 255-309 — `ndjson_append(path, entry_json)` chokepoint + `customer_now(tz_name)` helper
- ✅ Read `tests/test_notify_owner_with_fallback.py` lines 1-80 — subprocess-invoke + audit-log-content pattern
- ✅ Read `tests/_b1_helpers.py` (lines 100-340 in earlier session) — importlib `SourceFileLoader` pattern for hyphen-named scripts (no .py extension); confirmed needed because `shift-agent-notify-owner` has no extension

---

## Code-level design

### 1. `src/platform/schemas.py` — three additions

**(a) `OwnerWellbeingConfig`** — insert near `DailyBriefConfig` (around line 437):

```python
# Agent #41 Owner Wellbeing config (revived from retired #20)
OwnerWellbeingDay = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class OwnerWellbeingConfig(BaseModel):
    """Quiet-hours rule (Agent #41 v0.1). Suppresses non-critical Pushover /
    WhatsApp notifications during owner-configured quiet windows.

    v0.2 will add weekly owner-load summary as a Daily Brief section.

    Default `enabled=False` — opt-in per customer; matches Tier-2 scaffold
    convention. When False, the guard is a no-op short-circuit at line 1
    of `_apply_quiet_hours_guard`.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    quiet_start: str = Field(default="22:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(default="06:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_days: list[OwnerWellbeingDay] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        min_length=1,
    )
    # priority < threshold → suppressed; priority >= threshold → always send.
    # default 1: suppress -2/-1/0 (silent/quiet/normal); allow 1/2 (high/emergency).
    critical_priority_threshold: int = Field(default=1, ge=-2, le=2)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _validate_time_strptime(cls, v: str) -> str:
        # Belt-and-suspenders: regex catches structure, strptime catches semantics.
        from datetime import datetime as _dt
        _dt.strptime(v, "%H:%M")
        return v

    @model_validator(mode="after")
    def _reject_zero_width_window(self) -> "OwnerWellbeingConfig":
        """R1-M2 fix: quiet_start == quiet_end produces a zero-width window
        that silently never fires (same-day branch returns
        `start <= now < end` = always False; cross-midnight branch
        unreachable when start == end). Reject at validation time so the
        operator gets a clear error instead of a silent no-op."""
        if self.enabled and self.quiet_start == self.quiet_end:
            raise ValueError(
                f"quiet_start == quiet_end ({self.quiet_start!r}) is a "
                f"zero-width window — guard would never fire. Set distinct "
                f"start and end times, or set enabled=False."
            )
        return self
```

**(b) Plug into `Config`** — add ONE line near `daily_brief: DailyBriefConfig` at schemas:1236:

```python
owner_wellbeing: OwnerWellbeingConfig = Field(default_factory=OwnerWellbeingConfig)
```

**(c) `OwnerNotificationSuppressed` audit variant** — insert near `HealthCheckFailure` (around line 1675):

```python
class OwnerNotificationSuppressed(_BaseEntry):
    """Quiet-hours guard suppressed a non-critical Pushover/WhatsApp send
    (Agent #41 v0.1). Emitted by shift-agent-notify-owner before the
    Pushover call when the priority is below the threshold AND now is
    inside the configured quiet window. Exit code remains EXIT_OK
    (success-skip semantics, mirrors BriefSkipped:already_sent)."""
    type: Literal["owner_notification_suppressed"]
    title: str = Field(max_length=250)
    priority: int = Field(ge=-2, le=2)
    quiet_start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_days: list[str] = Field(min_length=1)
    suppressed_at_local: str = Field(pattern=r"^\d{2}:\d{2}:\d{2}$")
```

**(d) `LogEntry` discriminated union** — add ONE line in the union at `schemas.py:2640+`, near `Annotated[HealthCheckFailure, Tag("health_check_failure")]` (line 2654):

```python
        Annotated[OwnerNotificationSuppressed, Tag("owner_notification_suppressed")],
```

This is the **B1 fix from R2 plan review**. Without it, `_pick_log_entry_tag` routes the new tag to `_UnknownLogEntry` silently (writes succeed, reads downgrade).

**(e) Export** — add `"OwnerWellbeingConfig", "OwnerWellbeingDay", "OwnerNotificationSuppressed"` to the `__all__` block near schemas:2786 (next to `"DailyBriefConfig", "BriefSection"`).

### 2. `src/agents/shift/scripts/shift-agent-notify-owner` — guard insertion

**Imports** (at the existing import block lines 27-41):

```python
from safe_io import customer_now, FileLock, ndjson_append  # add ndjson_append
from schemas import (  # noqa: E402
    Config, OwnerNotificationSuppressed, OwnerWellbeingConfig,
)
```

**Helper functions** — insert before `def main()` (around line 130):

```python
DECISIONS_LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")


def _in_quiet_window(now_local: datetime, cfg: OwnerWellbeingConfig) -> bool:
    """Pure helper: is now_local inside the configured quiet window?

    Two branches:
    - Same-day window (start < end, e.g. 13:00-15:00): suppressed iff
      start <= now_time < end.
    - Cross-midnight window (start > end, e.g. 22:00-06:00): suppressed iff
      now_time >= start OR now_time < end.
    - start == end is rejected at config-validation time (zero-width
      window would silently never fire — see _reject_zero_width_window).

    Boundary semantics: now == start → suppressed; now == end → NOT
    suppressed (`>=` start, `<` end). Pinned by test_boundary_at_quiet_start_*.

    R1-M3 note: lexicographic string compare on "HH:MM" is correct because
    the regex `^([01]\\d|2[0-3]):[0-5]\\d$` enforces fixed-width 5-char
    zero-padded ASCII. If that regex ever loosens, this comparison will
    silently misorder times.

    DST note (R2-MEDIUM-3): wall-clock semantics are intentional. On a
    spring-forward night in America/New_York, the 02:00-03:00 hour is
    skipped entirely; on fall-back the 01:00-02:00 hour repeats. Both
    physical clocks map to the same HH:MM local string and produce the
    same suppression decision — desired behavior for owner-facing
    quiet hours.
    """
    weekday = now_local.strftime("%a").lower()  # "mon", "tue", ...
    if weekday not in cfg.quiet_days:
        return False
    now_time = now_local.strftime("%H:%M")
    start = cfg.quiet_start
    end = cfg.quiet_end
    if start < end:
        # Same-day window
        return start <= now_time < end
    # Cross-midnight window (start > end is the only remaining case;
    # start == end is rejected at config validation).
    return now_time >= start or now_time < end


def _apply_quiet_hours_guard(
    cfg: Config, title: str, priority: int,
) -> tuple[bool, OwnerNotificationSuppressed | None]:
    """Return (suppress, audit_entry_or_None).

    Short-circuit when cfg.owner_wellbeing.enabled is False (the default
    state — guard is a no-op until the owner explicitly opts in).
    """
    ow = cfg.owner_wellbeing
    if not ow.enabled:
        return (False, None)
    if priority >= ow.critical_priority_threshold:
        return (False, None)
    now_local = customer_now(cfg.customer.timezone)
    if not _in_quiet_window(now_local, ow):
        return (False, None)
    entry = OwnerNotificationSuppressed(
        type="owner_notification_suppressed",
        ts=now_local,
        title=title[:250],
        priority=priority,
        quiet_start=ow.quiet_start,
        quiet_end=ow.quiet_end,
        quiet_days=list(ow.quiet_days),
        suppressed_at_local=now_local.strftime("%H:%M:%S"),
    )
    return (True, entry)
```

**Insertion in `main()`** — between line 153 (after `results: list[tuple[str, bool, str]] = []`) and line 156 (before primary `pushover_send` call). Earlier draft said "151-153" — corrected per R1-N5:

```python
    # Agent #41 v0.1 quiet-hours guard. No-op when cfg.owner_wellbeing.enabled is False.
    suppress, audit_entry = _apply_quiet_hours_guard(cfg, args.title, args.priority)
    if suppress:
        # R2-MEDIUM-1 fix: wrap ndjson_append in flock for inter-process safety.
        # Multiple shift-agent-notify-owner processes can fire simultaneously
        # (backup + health + watchdog) — O_APPEND alone is atomic only up to
        # PIPE_BUF/4096 bytes; OwnerNotificationSuppressed rows with 250-char
        # title + quiet_days list can interleave. Mirrors send-daily-brief
        # precedent at lines 421-425.
        try:
            with FileLock(DECISIONS_LOG_PATH.with_suffix(".log.lock")):
                ndjson_append(DECISIONS_LOG_PATH, audit_entry.model_dump_json())
        except Exception as e:
            # Audit failure must NOT prevent the success-skip return path —
            # the alternative is "alert was suppressed AND lost" which is
            # worse than "suppressed but no audit row." Pattern matches
            # cf-router/actions.py:296-311 audit-emit conventions.
            print(f"WARN: audit emit failed for owner_notification_suppressed: {e}",
                  file=sys.stderr)
        return EXIT_OK
```

### 3. `tests/test_owner_wellbeing_quiet_hours.py` — new test file

**Pattern**: importlib `SourceFileLoader` (hyphen-named script — same as `_b1_helpers.run_apply`) + threaded `BaseHTTPRequestHandler` Pushover stub on ephemeral port + Pushover URL injected via `MOCK_PUSHOVER_URL` env-var override.

**Pushover URL override mechanism (R1-M5)**: The script currently hardcodes `https://api.pushover.net/1/messages.json` at line 71 inside the `urllib.request.Request(...)` call. Tests need to redirect to the local stub.

**Choosing module-level constant + post-`exec_module` monkey-patch** (matches the existing `_b1_helpers` pattern that overrides `mod.BRIDGE_URL`, `mod.LEADS_PATH`, etc.):

1. **Refactor** in `shift-agent-notify-owner` (zero behavior change in production):
   ```python
   # near the top of the file with other module-level constants
   PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
   ```
   And in `pushover_send`:
   ```python
   req = urllib.request.Request(PUSHOVER_URL, data=body, headers=...)
   ```

2. **Tests** override after `exec_module`:
   ```python
   spec.loader.exec_module(mod)
   mod.PUSHOVER_URL = f"http://127.0.0.1:{stub_port}/messages"
   ```

This avoids baking a permanent test affordance into production code (no env-var coupling) and matches the deployed pattern in `tests/_b1_helpers.py:148-153`.

**Test structure (10 functions, ~240 LOC)** — added one validation test per R2-MEDIUM-2:

The 10th test is an in-process pydantic validation test (no subprocess needed):

```python
def test_validation_rejects_empty_quiet_days():
    """R2-MEDIUM-2: Field(min_length=1) on quiet_days must reject empty
    list at config load. Operator footgun: typo'd YAML with `quiet_days: []`
    would silently turn the guard into 'always allow' — must fail loud."""
    from schemas import OwnerWellbeingConfig
    with pytest.raises(ValidationError):
        OwnerWellbeingConfig(enabled=True, quiet_days=[])

def test_validation_rejects_zero_width_window():
    """R1-M2: zero-width window (start == end) silently never fires —
    @model_validator must reject when enabled=True."""
    from schemas import OwnerWellbeingConfig
    with pytest.raises(ValidationError):
        OwnerWellbeingConfig(
            enabled=True, quiet_start="22:00", quiet_end="22:00",
        )
```

Original 9 subprocess-invoke tests follow:

```python
@pytest.fixture
def env_dir(tmp_path):
    # Builds /tmp/<test>/{config.yaml, state/, logs/} with PUSHOVER_URL pointing at stub
    ...

@pytest.fixture
def pushover_stub():
    # Threaded HTTP server, captures POSTs into a list
    ...

def _run_notify(env_dir, pushover_url, *, title, priority, message):
    # importlib SourceFileLoader on shift-agent-notify-owner;
    # override CONFIG_PATH + DECISIONS_LOG_PATH;
    # set PUSHOVER_URL env;
    # sys.argv override; sys.exit(mod.main()) returncode capture.
    ...

def _read_audit(env_dir) -> list[dict]:
    # parse /tmp/<test>/logs/decisions.log lines, return as list
    ...

# ... 9 test functions per the plan, with frozen now_local injection via
# monkeypatching mod.customer_now to a fixed-time stub
```

**Frozen-time injection**: monkeypatch `mod.customer_now = lambda tz: datetime(2026, 5, 9, 23, 0, 0, tzinfo=ZoneInfo(tz))` per test for deterministic boundary checks.

### 4. Deploy notes

- `shift-agent-deploy.sh:42` already runs `install -m 644 src/platform/schemas.py /opt/shift-agent/schemas.py` — schema changes propagate.
- `shift-agent-deploy.sh:39` runs `install -m 755 src/agents/shift/scripts/* /usr/local/bin/` — script changes propagate.
- No `deploy.sh` change needed.
- Default config has `owner_wellbeing.enabled=False`, so deploy is a no-op until the owner edits `/opt/shift-agent/config.yaml` to opt in.
- Existing `Config` validation will REJECT pre-deploy configs that don't have an `owner_wellbeing` block IF `extra="forbid"` is set on `Config`. Verify: `Config` uses `default_factory` for new fields, so missing block → instantiates default → no breakage. (Pre-flight check at build time.)

---

## Risks + mitigations identified at design time

| Risk | Mitigation |
|---|---|
| Audit-emit fails (disk full, log file rotated mid-write) and we return EXIT_OK without emitting | Wrap `ndjson_append` in try/except + stderr warning; the suppressed-no-audit fallback is correct ("alert lost AND silent" < "alert lost but operator stderr-visible") |
| Existing `Config` validation rejects configs missing `owner_wellbeing` block on srilu | Verified: `Field(default_factory=...)` on the Config plug-in line means missing block → instantiates default. Build-time check: load srilu's config.yaml against the new schema before deploy. |
| Pre-existing call sites pass `priority=0` (default) — many backup/health calls — would suddenly get suppressed when owner enables quiet hours | Documented behavior, not a bug. Owner explicitly opts in. Future R3 OpenRouter alarm proposed in audit P2.5 covers credit-burn class; owner-readable summary deferred. |
| `customer_now` returns naive datetime if `Config.customer.timezone` is misconfigured | Existing behavior; `safe_io.customer_now` returns aware datetime always (verified). |
| Cross-midnight + DST transition (e.g. spring-forward in 22:00-06:00 window) | `customer_now()` is timezone-aware via `ZoneInfo`. **Wall-clock semantics intentional** (R2-MEDIUM-3): on spring-forward, the 02:00-03:00 hour is skipped entirely; on fall-back the 01:00-02:00 hour repeats. Both physical clocks map to the same HH:MM local string and produce the same suppression decision — desired for owner-facing quiet hours. Documented in `_in_quiet_window` docstring. No DST test (R1-M4 deferred per R2 — over-engineering for v0.1). |
| Multiple `shift-agent-notify-owner` invocations in quick succession (e.g. backup + health both alarming) | Each is a separate process; stateless guard. No race condition. `ndjson_append` uses O_APPEND + fsync per `safe_io:258-277`. |

---

## Verification + commit shape

- Run on srilu: `pytest tests/test_owner_wellbeing_quiet_hours.py -v` against tarballed working tree
- Pass criterion: 9/9 pass; existing `tests/test_notify_owner_with_fallback.py` still 100% green
- Build-time pre-flight: load srilu's current `config.yaml` against the new schema (no `owner_wellbeing` block expected) — must instantiate default cleanly, not raise ValidationError
- Single commit, ~285 LOC, message: `feat(agent-41): owner wellbeing v0.1 — quiet-hours guard`

---

## Approval needed

Design reviewers must explicitly approve before build phase. Specific decisions
to challenge:

1. **PUSHOVER_URL env-var override** in `pushover_send` — minimal one-line change, but does it expand the script's blast radius (e.g. an attacker could redirect Pushover sends if they got env access)? Counter: env access already implies full agent compromise; the env-var pattern is internal-only and not in `.env` defaults.
2. **Audit-emit failure → stderr + EXIT_OK** vs. EXIT_DEPENDENCY_DOWN — per risk table. Argument for stderr+EXIT_OK is "suppression is the right behavior; audit is observability." Argument against is "silent suppress with no audit is the worst-of-both." Going with stderr+EXIT_OK; reviewers can challenge.
3. **`OwnerWellbeingDay` `Literal` with 7 string members** — could be `int` (0-6) instead. Going with strings for operator-readable `config.yaml`; matches `customer_now().strftime("%a").lower()` lookup naturally.
4. **Boundary semantics**: `now == quiet_start` → suppressed; `now == quiet_end` → NOT suppressed (`>=` start, `<` end). Pinned by test 7. Reviewers can flip if conventional.
5. **Test count**: 9 tests for ~30 LOC of new business logic. Ratio is high but matches the boundary-test discipline from prior PR reviews. R2-M2/M3 explicitly added 3 of these.
