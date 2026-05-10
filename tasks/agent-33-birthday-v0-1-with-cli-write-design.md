# Agent #33 v0.1 — birthday reminders + CLI write script (design)

**Drift-check tag:** `extends-Hermes`

Design for plan v2 at `tasks/agent-33-birthday-v0-1-with-cli-write-plan.md`
(option C pivot — user-approved 2026-05-10 after plan-review found
read-only v0.1 was dead-code-on-arrival without an input path).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-33-birthday-v0-1-with-cli-write-design.json`
(timestamp 2026-05-10T14:45:40Z, drift-tag = extends-Hermes, 7 [Hermes] / 5 [net-new]).

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | systemd timer + self-gate + existing aggregators | `[Hermes]` | Already deployed |
| 2 | **`_aggregate_birthdays(cfg, today_local)`** | **`[net-new]`** | ~25 LOC; load_model + MM-DD filter + degraded-mode fallback |
| 3 | **`_render_birthdays(birthdays_today)`** | **`[net-new]`** | ~10 LOC formatting |
| 4 | **`_render_brief_text` + template patch** | **`[net-new]`** | ~5 LOC + 1 template line |
| 5 | Brief delivery + audit | `[Hermes]` | Already deployed |
| 6 | Operator CLI invocation + safe_io helpers | `[Hermes]` | All deployed |
| 7 | **`record-customer-birthday` CLI script** | **`[net-new]`** | ~80 LOC mirroring `create-catering-lead` |
| 8 | **`CustomerBirthdayRecorded` audit variant + LogEntry union row** | **`[net-new]`** | ~15 LOC |
| 9 | **Schema additions (5 items)** | **`[net-new]`** | ~70 LOC |
| 10 | Tests | `[net-new]` (case curation) | ~130 LOC |

5/10 net-new (case curation reasonably split per design granularity).

---

## Drift-rule self-checks

All required reads verified at plan v2 + design time:

- ✅ Read `src/agents/daily_brief/scripts/send-daily-brief` lines 100-150 + 288-365 (gate + render flow + helpers)
- ✅ Read `src/agents/daily_brief/templates/daily_brief.txt` (full)
- ✅ Read `src/platform/schemas.py` lines 413 (`BriefSection`), 1230-1252 (`Config` plug-in pattern), 1670-1706 (`_BaseEntry` subclass pattern + `BriefAttempted` precedent), 2640-2719 (LogEntry discriminated union — explicit `Annotated[..., Tag(...)]` row required per Agent #41 B1 lesson)
- ✅ Read `src/agents/catering/scripts/create-catering-lead` lines 30-50, 451+, 480, 575, 425, 646-647 — write-script template (argparse + FileLock + atomic_write_json + ndjson_append audit)
- ✅ Read `src/platform/safe_io.py` lines 240-254 — `atomic_write_json(path: Path, obj: Any, mode: int = 0o640)` accepts Pydantic models directly via `obj.model_dump_json(indent=2)` when `isinstance(obj, BaseModel)`
- ✅ Read `tests/test_daily_brief_script.py` lines 1-50 — subprocess + `_BridgeStub` pattern
- ✅ Read `tests/test_lookup_prior_leads.py` lines 36-43 — importlib `SourceFileLoader` pattern (post-Agent-#32 fix)

**Deployed-pattern compliance:**
- Schema: pydantic v2 + `extra="forbid"` + `Literal[1] = 1` schema_version pin ✓
- Config plug-in: top-level Tier-2 entry mirroring `equipment_maintenance` ✓
- BriefSection extension: append `"birthdays"` to existing Literal at schemas:413 ✓
- Audit variant: `_BaseEntry` subclass + explicit `Annotated[Variant, Tag("...")]` row in LogEntry union ✓
- Write script: argparse + FileLock + atomic_write_json + ndjson_append ✓
- Render helper: take dict, single-line return, empty-state default ✓
- Tests: `SourceFileLoader` for hyphen-named scripts + subprocess wrapper ✓

---

## Code-level design

### 1. `src/platform/schemas.py` — five additions

**(a) `LoyaltyConfig`** — insert near other Tier-2 configs:

```python
class LoyaltyConfig(BaseModel):
    """Agent #33 Tier-2 scaffold; default off. v0.1 covers birthday reminders
    only (Daily Brief section + record-customer-birthday CLI). v0.2 will add
    punch-card / points / WhatsApp owner-command / auto-greeting."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
```

**(b) `BriefSection` extension** — modify line 413:

```python
BriefSection = Literal["yesterday", "today_outlook", "alerts", "birthdays"]
```

**(c) `CustomerBirthday` + `CustomerBirthdayStore`** — insert near other state stores:

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
        is accepted as a legitimate leap-day birthday."""
        from datetime import datetime as _dt
        _dt.strptime(f"2024-{v}", "%Y-%m-%d")
        return v


class CustomerBirthdayStore(BaseModel):
    """Outer container for state/customer-birthdays.json."""
    model_config = ConfigDict(extra="forbid")
    customers: list[CustomerBirthday] = Field(default_factory=list)
    schema_version: Literal[1] = 1   # R2-B1 fix: pinned for migration discipline
```

**(d) Plug into `Config`** — add ONE line near `equipment_maintenance` at schemas:1252:

```python
loyalty: LoyaltyConfig = Field(default_factory=LoyaltyConfig)
```

**(e) `CustomerBirthdayRecorded` audit variant** — insert near `BriefAttempted`:

```python
class CustomerBirthdayRecorded(_BaseEntry):
    """Audit emitted by record-customer-birthday after successful upsert.
    operation: "created" if phone wasn't in store, "updated" if it was."""
    type: Literal["customer_birthday_recorded"]
    customer_phone: str
    display_name: str = Field(min_length=1, max_length=100)
    birthday: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    operation: Literal["created", "updated"]
```

**(f) `LogEntry` union row** — insert near `BriefSent`:

```python
        Annotated[CustomerBirthdayRecorded, Tag("customer_birthday_recorded")],
```

**(g) Exports** — add to `__all__`:

```python
"LoyaltyConfig",
"CustomerBirthday", "CustomerBirthdayStore",
"CustomerBirthdayRecorded",
```

### 2. `src/agents/daily_brief/scripts/send-daily-brief` — three additions

**(a) Module constant** (near other `*_PATH` constants):

```python
BIRTHDAYS_PATH = Path("/opt/shift-agent/state/customer-birthdays.json")
```

**(b) `_aggregate_birthdays` helper** (insert before `_render_yesterday`):

```python
def _aggregate_birthdays(cfg: Config, today_local: datetime) -> list[dict]:
    """Returns list of {phone, name} dicts for customers whose birthday
    matches today_local's MM-DD. Falls open on missing/corrupt file —
    returns [], emits stderr WARN. Matches _aggregate_yesterday's
    degraded-mode philosophy."""
    if not BIRTHDAYS_PATH.exists():
        return []
    try:
        store, status = load_model(
            BIRTHDAYS_PATH, CustomerBirthdayStore,
            default=CustomerBirthdayStore(),
        )
        if not status.startswith("ok"):
            sys.stderr.write(
                f"WARN: customer-birthdays.json status={status!r}; "
                f"birthdays section degraded\n"
            )
            return []
    except Exception as e:
        sys.stderr.write(f"WARN: birthday load failed: {e}; section degraded\n")
        return []
    today_md = today_local.strftime("%m-%d")
    return [
        {"phone": c.customer_phone, "name": c.display_name}
        for c in store.customers
        if c.birthday == today_md
    ]
```

**(c) `_render_birthdays` helper** (insert after `_render_alerts`):

```python
def _render_birthdays(birthdays_today: list[dict]) -> str:
    """Single-line summary. 'None today.' if empty; else
    'Suresh Patel (+15555550100), Priya Reddy (+15555550101)'."""
    if not birthdays_today:
        return "None today."
    return ", ".join(f"{b['name']} ({b['phone']})" for b in birthdays_today)
```

**(d) `_render_brief_text` patch** (around line 353, in the `fields` dict):

```python
fields = {
    ...,
    "alerts_summary": _render_alerts(yesterday_counts, today_data),
    "birthdays_summary": _render_birthdays(birthdays_today),  # NEW
}
```

The caller of `_render_brief_text` in `main()` computes `birthdays_today`
gated on `cfg.daily_brief.sections`:

```python
birthdays_today = (
    _aggregate_birthdays(cfg, _customer_now(cfg.customer.timezone))
    if "birthdays" in cfg.daily_brief.sections
    else []
)
```

### 3. `src/agents/daily_brief/templates/daily_brief.txt` — append one line

```
*Birthdays today:* {birthdays_summary}
```

### 4. `src/agents/daily_brief/scripts/record-customer-birthday` (NEW) — ~80 LOC

Mirror `create-catering-lead` write pattern — argparse + pydantic-validate +
FileLock + load → upsert → atomic_write_json + ndjson_append audit + exit codes.
Full code in plan v2 §"Schema shapes" (locked at plan time).

### 5. `tests/test_daily_brief_birthdays.py` (NEW) — 10 tests

**Importlib unit tests for read-side helpers (6 cases)**:

1. `test_aggregate_birthdays_match_today` — store has 1 customer with today's MM-DD → returns 1-item list
2. `test_aggregate_birthdays_no_match` — store has customers but none today → `[]`
3. `test_aggregate_birthdays_multiple_today` — 2 customers same MM-DD → 2-item list
4. `test_aggregate_birthdays_missing_file` — falls open to `[]`, stderr WARN
5. `test_render_birthdays_empty` — `[]` → `"None today."`
6. `test_render_birthdays_formatted` — list → `"Suresh Patel (+15555550100), Priya Reddy (+15555550101)"`

**Subprocess tests for write script (4 cases)**:

7. `test_record_birthday_creates_store_when_missing` — first record, exit 0, store written, audit `operation="created"`
8. `test_record_birthday_updates_existing_phone` — same phone re-recorded → exit 0, store has 1 entry (not 2), audit `operation="updated"`
9. `test_record_birthday_rejects_invalid_date` — `--birthday 02-30` → exit 2, no state mutation, no audit
10. `test_record_birthday_rejects_invalid_phone` — `--phone "abc"` → exit 2, no state mutation

Each test uses fixture `tmp_path` for isolated state dir + module-path overrides for `BIRTHDAYS_PATH` / `LOG_PATH` / `CONFIG_PATH`. Pre-load `schemas` / `safe_io` / `exit_codes` into `sys.modules` from the test PLATFORM_DIR before `exec_module` to bypass deployed-vs-test race (lessons from #41 + #32).

---

## Risks identified at design time

| Risk | Mitigation |
|---|---|
| `atomic_write_json` Windows portability | N/A — Linux-only target |
| Hand-edited state file mid-write race | RESOLVED by CLI script (uses FileLock) — R2-B3 closed |
| Audit-emit failure post state-write | Best-effort try/except matches `create-catering-lead` convention |
| Schema migration if v0.2 adds fields | `Literal[1] = 1` schema_version pin enables migration framework hook |
| Birthday section enabled, state file absent | Falls open to `[]` → renders "None today." |
| Operator records duplicate phone with different name | Upsert on phone matches "phone is canonical identity" convention |
| Template ordering disrupts visual flow | One-line append after existing sections is non-disruptive |

---

## Verification + commit shape

- Run on srilu: `pytest tests/test_daily_brief_birthdays.py -v`
- Pass criterion: 10/10 new tests pass; existing `tests/test_daily_brief_script.py` still 100% green
- Commit shape: ONE commit, ~335 LOC across 5 files
- Deploy: tarball + `shift-agent-deploy.sh`
- Post-merge: append v0.2 backlog items to `tasks/todo.md` P2.5

---

## Approval needed

Design reviewers must approve before build. Decisions to challenge:

1. **`record-customer-birthday` location** — placed in `src/agents/daily_brief/scripts/`. Reviewers can flip if a top-level `loyalty/` is preferred.
2. **Upsert semantics on phone match** — replace, not error. Reviewers can flip.
3. **Audit-emit failure → success-exit** — matches deployed convention.
4. **Template field placement** — appended to end. Reviewers can reorder.
5. **Phone uniqueness enforced at write-time, not schema-level `@model_validator`** — keeps hand-editable state files robust to migration.
