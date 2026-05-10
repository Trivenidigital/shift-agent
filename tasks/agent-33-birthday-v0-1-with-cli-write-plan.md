# Agent #33 v0.1 — birthday reminders + CLI write script (option C, plan v2)

**Drift-check tag:** `extends-Hermes`

**v2 plan** for Agent #33 v0.1 (Loyalty & Punch-Card minimum viable).
Option C pivot per user 2026-05-10 after plan-review BLOCKERs. The
original v1 plan (`tasks/audits/agent-33-original-plan-rejected.md`)
was read-only; R1 BLOCKER said that's dead-code-on-arrival without an
input path, R2 raised 3 BLOCKERs (Literal-pin, MM-DD validator, race).
Option C addresses all four by adding an operator-facing CLI write
script (no LLM / dispatcher integration — that's v0.2).

**Portfolio reference:** `docs/portfolio.md:998` (Agent #33 spec) +
build-priority list at `:1101`.

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-33-birthday-v0-1-with-cli-write.json`
(timestamp 2026-05-10T14:42:50Z, drift-tag = extends-Hermes, 9 [Hermes] / 6 [net-new]).

**v0.2 deferred items** (tracked in `tasks/todo.md` post-merge):
- WhatsApp owner-command for adding birthdays via dispatcher SKILL
- Punch-card / points / auto-rewards (full Loyalty agent surface)
- Auto-customer-facing greeting (with outbound-cap accounting + opt-out)
- Year-aware "turning N" extension

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | systemd timer fires (Daily Brief cron) | `[Hermes]` | Already deployed |
| 2 | `_self_gate_state` + sentinel | `[Hermes]` | Already deployed |
| 3 | `_aggregate_yesterday` + `_aggregate_today` | `[Hermes]` | Already deployed |
| 4 | **`_aggregate_birthdays(cfg, today_local)` reads `state/customer-birthdays.json`** | **`[net-new]`** | ~25 LOC, fall-open on missing/corrupt |
| 5 | **`_render_birthdays(birthdays_today)` formats output** | **`[net-new]`** | ~10 LOC |
| 6 | `_render_brief_text` interpolation + template patch | **`[net-new]`** | ~5 LOC + 1 template line |
| 7 | Brief delivery via existing `bridge_post` | `[Hermes]` | Already deployed |
| 8 | `BriefAttempted` / `BriefSent` audit | `[Hermes]` | Already deployed |
| 9 | **Operator runs `/usr/local/bin/record-customer-birthday --phone +1xxx --name Suresh --birthday 03-15`** | **`[net-new]`** | New CLI script (~80 LOC mirroring `create-catering-lead`'s argparse + lock + atomic-write pattern) |
| 10 | Script: pydantic-validate inputs, `FileLock(BIRTHDAYS_LOCK)`, `atomic_write_json(BIRTHDAYS_PATH, store)` | `[Hermes]` | All deployed safe_io helpers |
| 11 | **`CustomerBirthdayRecorded` audit variant emitted via `safe_io.ndjson_append`** | **`[net-new]`** | New `_BaseEntry` subclass + LogEntry union row (~15 LOC) |
| 12 | **Schema additions: `CustomerBirthday` + `CustomerBirthdayStore` + `LoyaltyConfig` + extend `BriefSection` + `Config` plug-in + exports** | **`[net-new]`** | ~50 LOC including `Literal[1]` pin + MM-DD field_validator |
| 13 | Tests: unit tests for read-side helpers + write-script tests via subprocess + audit-variant smoke | **`[net-new]`** | ~150 LOC |
| 14 | Deploy via existing tarball pipeline | `[Hermes]` | `shift-agent-deploy.sh` glob picks up new script |
| 15 | v0.2 WhatsApp owner-command path | DEFERRED | Tracked in tasks/todo.md |

9/15 `[Hermes]`, 6/15 `[net-new]`. Below 50% threshold.

**Awesome-hermes-agent ecosystem check:** N/A — per-customer birthday memory
is per-customer SMB business logic.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 (schema work + new script + script extension + test work).
Files Read this session before drafting:

- ✅ Read `src/agents/daily_brief/scripts/send-daily-brief` lines 100-150 (gate logic) + 288-365 (`_render_*` helpers + `_render_brief_text`) — established the helper-then-interpolation convention to mirror for `_render_birthdays`
- ✅ Read `src/agents/daily_brief/templates/daily_brief.txt` (full, 8 lines) — `str.format` style with `{snake_case_summary}` interpolation; template patch pattern locked
- ✅ Read `src/platform/schemas.py` line 413 (`BriefSection` Literal extension point) + line 475 (`sections: list[BriefSection]` opt-in) + lines 1280-1285 (`Config.schema_version: Literal[1] = 1` — the pinning convention to mirror per R2-B1)
- ✅ Read `src/agents/catering/scripts/create-catering-lead` lines 30-50 (imports), 451+ (argparse), 480 (`FileLock(LEADS_LOCK)`), 575 (`atomic_write_json(LEADS_PATH, store)`), 425 + 646-647 (`ndjson_append(LOG_PATH, adapter.dump_json(entry).decode("utf-8"))` audit pattern). This is the exact write-script-with-lock-and-atomic-write template `record-customer-birthday` mirrors.
- ✅ Read `tests/test_daily_brief_script.py` lines 1-50 — confirmed subprocess-based test pattern with `_BridgeStub` HTTP mock for the existing E2E coverage; my new tests use the importlib unit pattern from `_b1_helpers` for the helpers + `tests/test_owner_wellbeing_quiet_hours.py`-style subprocess wrapper for the write-script CLI

**Deployed-pattern compliance:**
- Write script shape: argparse + pydantic-validate + FileLock + atomic_write_json + ndjson_append audit ✓ (mirrors `create-catering-lead`)
- Read helper: fall-open on missing/corrupt (matches `_aggregate_yesterday`'s degraded-mode philosophy) ✓
- Schema convention: pydantic v2 + `extra="forbid"` + `Literal[N] = N` for `schema_version` ✓
- Audit chain: `_BaseEntry` subclass + `type: Literal[...]` + `LogEntry` union row + `ndjson_append` chokepoint ✓
- LogEntry union: explicit `Annotated[Variant, Tag("...")]` row required (lesson from #41 B1) ✓
- BriefSection Literal extension preserves opt-in via `cfg.daily_brief.sections` ✓
- Field validator on MM-DD format for legal-date enforcement (R2-B2 fix) ✓

---

## Scope boundary (anti-over-engineering)

### In scope (~330 LOC across 5 files + 1 new test file)

| File | Change | LOC |
|---|---|---|
| `src/platform/schemas.py` | Add `CustomerBirthday` (E164Phone + display_name + MM-DD birthday with `@field_validator` for valid dates) + `CustomerBirthdayStore` (with `Literal[1]` schema_version) + `LoyaltyConfig` (Tier-2 scaffold, `enabled: bool = False`) plug into `Config` + extend `BriefSection` Literal + `CustomerBirthdayRecorded` `_BaseEntry` subclass + LogEntry union row + exports | ~70 |
| `src/agents/daily_brief/scripts/send-daily-brief` | Add `BIRTHDAYS_PATH` constant + `_aggregate_birthdays` + `_render_birthdays` helpers + `_render_brief_text` patch | ~50 |
| `src/agents/daily_brief/templates/daily_brief.txt` | Append `*Birthdays today:* {birthdays_summary}` line | 1 |
| `src/agents/daily_brief/scripts/record-customer-birthday` (NEW) | Full mirror of `create-catering-lead`'s write pattern: argparse + pydantic-validate + `FileLock(BIRTHDAYS_LOCK)` + `atomic_write_json(BIRTHDAYS_PATH, store)` + `ndjson_append` audit + EXIT codes | ~80 |
| `tests/test_daily_brief_birthdays.py` (NEW) | 5-7 importlib unit tests for read-side helpers + 3-4 subprocess tests for write script + audit-variant smoke | ~130 |

### Schema shapes (locked at plan time)

```python
class CustomerBirthday(BaseModel):
    """Per-customer birthday record (Agent #33 v0.1)."""
    model_config = ConfigDict(extra="forbid")
    customer_phone: E164Phone
    display_name: str = Field(min_length=1, max_length=100)
    # MM-DD only (no year — many customers don't share or won't update).
    birthday: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @field_validator("birthday")
    @classmethod
    def _validate_calendar_date(cls, v: str) -> str:
        """R2-B2 fix: regex pattern allows 02-30, 04-31, etc. Reject illegal
        dates by attempting to parse with a leap-year pivot (2024) so 02-29
        is accepted."""
        from datetime import datetime as _dt
        _dt.strptime(f"2024-{v}", "%Y-%m-%d")
        return v


class CustomerBirthdayStore(BaseModel):
    """Outer container for state/customer-birthdays.json."""
    model_config = ConfigDict(extra="forbid")
    customers: list[CustomerBirthday] = Field(default_factory=list)
    schema_version: Literal[1] = 1   # R2-B1 fix: pinned for migration discipline


class LoyaltyConfig(BaseModel):
    """Agent #33 Tier-2 scaffold; default off. v0.1 covers birthdays only;
    v0.2 expands to punch-card / points / auto-rewards / WhatsApp owner-command."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False


# In LogEntry discriminated union:
class CustomerBirthdayRecorded(_BaseEntry):
    """Audit emitted by record-customer-birthday after successful write."""
    type: Literal["customer_birthday_recorded"]
    customer_phone: str
    display_name: str
    birthday: str
    operation: Literal["created", "updated"]


BriefSection = Literal["yesterday", "today_outlook", "alerts", "birthdays"]
```

**`Config` plug-in** (R2-M1 fix — top-level Tier-2 entry like `pnl_anomaly`):

```python
loyalty: LoyaltyConfig = Field(default_factory=LoyaltyConfig)
```

The state-file path is a module-level constant in the script
(`BIRTHDAYS_PATH = Path("/opt/shift-agent/state/customer-birthdays.json")`) —
matches the `LEADS_PATH` convention.

### Test cases (10 total)

**Read-side (importlib unit tests, 6 cases):**
1. `test_aggregate_birthdays_match_today` — single customer with today's MM-DD
2. `test_aggregate_birthdays_no_match` — store has customers but none today
3. `test_aggregate_birthdays_multiple_today` — 2+ customers same MM-DD
4. `test_aggregate_birthdays_missing_file` — fall-open returns []
5. `test_render_birthdays_empty` — "None today."
6. `test_render_birthdays_formatted` — "Suresh Patel (+15555550100), Priya Reddy (+15555550101)"

**Write script (subprocess tests, 4 cases):**
7. `test_record_birthday_creates_store_when_missing` — first record, exit 0, audit emitted
8. `test_record_birthday_updates_existing_phone` — re-record same phone updates instead of duplicating
9. `test_record_birthday_rejects_invalid_date` — `--birthday 02-30` exits non-zero, no state mutation
10. `test_record_birthday_atomic_write_via_lock` — FileLock acquired during write (verify via lock-check)

### Explicitly out of scope (DEFERRED to v0.2 — tracked in tasks/todo.md)

| Item | Reason for deferral |
|---|---|
| WhatsApp owner-command (`add birthday +1xxx 03-15 Suresh`) | Requires new SKILL + dispatcher row + LLM intent classification + curator-regression risk surface. v0.1 ships CLI-callable; v0.2 wraps it in a SKILL. |
| Punch-card / points tracking | Full Loyalty surface; needs visit-counting logic, reward-trigger thresholds, customer-facing notification. v0.2+ scope. |
| Auto-customer-facing greeting | Outbound-cap accounting + opt-out flag + LLM-generated greeting prose; v0.2+ |
| Year-aware "turning N" | v0.3 if requested. |
| Birthday-section default opt-in | v0.1 keeps `cfg.daily_brief.sections` defaults unchanged; operator opts in. |

---

## Verification + commit shape

- Run on srilu: `pytest tests/test_daily_brief_birthdays.py -v` against tarballed working tree
- Pass criterion: 10/10 new tests pass; existing `tests/test_daily_brief_script.py` still 100% green
- Commit shape: ONE commit, message `feat(agent-33): birthday reminders v0.1 — Daily Brief section + record-customer-birthday CLI (option C)`, ~330 LOC across 5 files
- Deploy notes:
  - `shift-agent-deploy.sh:113-115` glob installs new script automatically
  - `schemas.py` propagates via existing line 42
  - Template propagates via existing daily_brief templates install
  - Default config: `loyalty.enabled=False` AND `cfg.daily_brief.sections` unchanged → deploy is a no-op until owner opts in twice (enable loyalty + add "birthdays" to sections)
  - Operator initialization: SSH and run `/usr/local/bin/record-customer-birthday --phone +1xxx --name "Suresh Patel" --birthday 03-15` for each known customer

## Backlog — v0.2 items added to tasks/todo.md

Will append to P2.5 (or appropriate section) at PR time:
- v0.2 WhatsApp owner-command for `add/update birthday +1xxx ...` (new SKILL + dispatcher row)
- v0.2 punch-card / points schema + visit counter + reward triggers
- v0.2 auto-customer-facing greeting (with outbound-cap + opt-out)
- v0.3 year-aware extension
- Tracker for "section default opt-in vs explicit opt-in" decision

---

## Approval needed

Plan reviewer approval is implicit via user's option-C choice on
2026-05-10. Proceeding directly to design phase per the user-approved
pipeline (mirrors how #32's option-A pivot proceeded directly to design
after the user choice).
