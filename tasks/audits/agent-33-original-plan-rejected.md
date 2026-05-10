# Agent #33 v0.1 — birthday reminders in Daily Brief (Loyalty & Punch-Card minimum viable plan)

**Drift-check tag:** `extends-Hermes`

Adds custom infrastructure (state schema, helpers, BriefSection extension)
on top of the deployed `send-daily-brief` script. v0.1 ships ONLY birthday
reminders — punch-card / points / auto-rewards / owner WhatsApp commands
deferred to v0.2+. Mirrors the option-A discipline from #32: use the
existing Daily Brief substrate (timer + sections enum + audit chain +
self_chat send) rather than building a parallel cron + WhatsApp chokepoint.

**Portfolio reference:** `docs/portfolio.md:998` (Agent #33 spec) +
build-priority list at `:1101`.

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-33-loyalty-birthday-daily-brief-v0-1.json`
(timestamp 2026-05-10T01:44:14Z, drift-tag = extends-Hermes, 8 [Hermes] / 4 [net-new]).

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | systemd timer `send-daily-brief.timer` fires every 15 min | `[Hermes]` | Already deployed |
| 2 | `_self_gate_state` + sentinel logic | `[Hermes]` | Already deployed |
| 3 | `_aggregate_yesterday(cfg)` collects yesterday's audit signals | `[Hermes]` | Already deployed |
| 4 | `_aggregate_today(cfg)` collects today's outlook signals | `[Hermes]` | Already deployed |
| 5 | **`_aggregate_birthdays(cfg, today_local)`** reads `state/customer-birthdays.json`, filters by today's MM-DD | **`[net-new]`** | ~25 LOC |
| 6 | **`_render_birthdays(birthdays_today)`** formats the section | **`[net-new]`** | ~10 LOC |
| 7 | **`_render_brief_text` + template patch** to interpolate `birthdays_summary` | **`[net-new]`** | ~5 LOC + 1 template line |
| 8 | Brief delivery via existing `bridge_post` to owner self_chat_jid | `[Hermes]` | Already deployed |
| 9 | `BriefAttempted` / `BriefSent` audit chain | `[Hermes]` | Already deployed |
| 10 | State file hand-edited by operator (v0.1) | `[Hermes]` | safe_io.atomic_write_json convention exists; no project-side write script in v0.1 |
| 11 | **Schema additions: CustomerBirthday + CustomerBirthdayStore + extend BriefSection + plug into Config** | **`[net-new]`** | ~30 LOC |
| 12 | v0.2 punch-card / points / WhatsApp owner command | DEFERRED | Not in scope |

8/12 `[Hermes]`, 4/12 `[net-new]`. Below the 50% red-flag threshold.

**Awesome-hermes-agent ecosystem check:** N/A — per-customer birthday memory
is per-customer SMB business logic. No upstream skill provides this.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 (schema work + script extension + test work).
Files Read this session before drafting:

- ✅ Read `src/agents/daily_brief/scripts/send-daily-brief` lines 100-150 (gate logic), 288-365 (existing `_render_*` helpers + `_render_brief_text`), 444-480 implied (main flow). Confirmed:
  - `_render_yesterday`/`_render_today`/`_render_alerts` pattern at 290-336 — the helper-then-string-interpolation convention to mirror
  - `_render_brief_text` at 338-361 — the integration point: dict-build then `template.format(**fields)`
  - Each `_render_*` helper takes a small dict argument and returns a single-line string, falling back to descriptive default ("Quiet day...", "No shifts...", "Nothing — all green.")
- ✅ Read `src/agents/daily_brief/templates/daily_brief.txt` (full, 8 lines) — confirmed `{header}`, `{brief_date}`, `{catchup_note}`, `{yesterday_summary}`, `{today_outlook}`, `{alerts_summary}` interpolation fields. New `{birthdays_summary}` field will be appended in the same style.
- ✅ Read `src/platform/schemas.py` line 413 (`BriefSection = Literal["yesterday", "today_outlook", "alerts"]`) + line 475 (`sections: list[BriefSection]` field on `DailyBriefConfig`). The Literal extension point is unambiguous; section opt-in via `cfg.daily_brief.sections` is preserved.
- ✅ Read `tests/test_daily_brief_script.py` lines 1-50 — confirmed subprocess-based end-to-end test pattern with `_BridgeStub` HTTP mock. For this PR, I'll use a NEW test file with importlib unit-tests of the new helpers (faster, isolated) — mirrors the unit-test approach from `test_owner_wellbeing_quiet_hours.py`. The existing E2E test file gets one new case extending the bridge-stub assertion to confirm the new section renders.

**Deployed-pattern compliance:**
- Storage: JSON-on-disk + `safe_io` atomic-write convention for state file ✓
- Schema: pydantic v2 + `extra="forbid"` ✓ (matches `Roster`, `CateringLeadStore`, etc.)
- BriefSection Literal extension ✓ (preserves `cfg.daily_brief.sections` opt-in)
- Template field convention: `{snake_case_summary}` ✓
- Render helper convention: take small dict, return single-line string with descriptive empty-state ✓
- Audit chain: extends existing `BriefAttempted`/`BriefSent` events; no new audit variant needed (matches deployed convention) ✓
- Tests: importlib pattern with `SourceFileLoader` for the hyphen-named script (lessons from #41 + #32) ✓

---

## Scope boundary (anti-over-engineering)

### In scope (~165 LOC across 4 files + 1 new test file)

| File | Change | LOC |
|---|---|---|
| `src/platform/schemas.py` | Add `CustomerBirthday` (per-customer record with E164Phone + MM-DD birthday + display_name) + `CustomerBirthdayStore` (outer container) + extend `BriefSection` Literal with `"birthdays"` + plug into `Config`-aware path. NO new audit variant. | ~30 |
| `src/agents/daily_brief/scripts/send-daily-brief` | Add `BIRTHDAYS_PATH` constant + `_aggregate_birthdays(cfg, today_local)` helper + `_render_birthdays(birthdays_today)` helper + `_render_brief_text` patch to include `birthdays_summary` field | ~40 |
| `src/agents/daily_brief/templates/daily_brief.txt` | Append one line `*Birthdays today:* {birthdays_summary}` | 1 |
| `tests/test_daily_brief_birthdays.py` (NEW) | 5-7 importlib unit tests for the new helpers + `customer-birthdays.json` round-trip | ~95 |

### Schema shape (locked at plan time)

```python
class CustomerBirthday(BaseModel):
    """Per-customer birthday record (Agent #33 v0.1)."""
    model_config = ConfigDict(extra="forbid")
    customer_phone: E164Phone
    display_name: str = Field(min_length=1, max_length=100)
    # MM-DD only (no year — many customers don't share or won't update).
    birthday: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


class CustomerBirthdayStore(BaseModel):
    """Outer container for the JSON state file."""
    model_config = ConfigDict(extra="forbid")
    customers: list[CustomerBirthday] = Field(default_factory=list)
    schema_version: int = 1
```

**`BriefSection` extension:**

```python
BriefSection = Literal["yesterday", "today_outlook", "alerts", "birthdays"]
```

The default `sections` list on `DailyBriefConfig` stays at the existing 3 —
operators opt into birthdays via config (matches Tier-2 scaffold convention).

**Helper signatures:**

```python
def _aggregate_birthdays(cfg: Config, today_local: datetime) -> list[dict]:
    """Returns list of {phone, name} dicts for customers whose birthday matches
    today's MM-DD. Falls open on missing/corrupt file (returns [], emits stderr
    WARN). Matches _aggregate_yesterday's degraded-mode philosophy."""

def _render_birthdays(birthdays_today: list[dict]) -> str:
    """Single-line string. 'None today.' if empty; else
    'Suresh Patel (+15555550100), Priya Reddy (+15555550101)'."""
```

### Explicitly out of scope (rejected at plan time)

| Considered | Decision | Reason |
|---|---|---|
| Punch-card / points tracking | **DEFERRED to v0.2** | Requires owner WhatsApp command for "+1 punch +1xxx" + state file with point counters + reward-trigger logic. Significant scope; v0.1 ships birthday-only. |
| Owner WhatsApp command for adding birthdays | **DEFERRED to v0.2** | Requires new SKILL or dispatcher row + LLM intent classification. v0.1 = operator hand-edits state file. |
| Auto-customer-facing birthday WhatsApp greeting | **DEFERRED to v0.2** | Risk of unauthorized customer message + outbound-cap consumption + no opt-out mechanism. v0.1 surfaces the reminder TO OWNER who decides whether to send. |
| Year-aware birthday with "turning N" | **REJECTED for v0.1** | Many customers don't share birth year or share inaccurately. MM-DD is the safer contract. v0.2 can add optional year. |
| `BirthdaySent` audit variant | **REJECTED for v0.1** | The brief itself emits `BriefSent` (already deployed) which captures the section was sent. Per-customer-birthday-shown audit is observability noise for v0.1 — defer until evidence of a leak / regression. |
| New cron + dedicated birthday timer | **REJECTED** | Daily Brief already runs daily. Patching the existing brief is option-A scope discipline. |

### Deferred (separate commits if ever needed)

- v0.2: punch-card schema + owner WhatsApp command + reward-trigger logic
- v0.2: auto-customer-facing greeting (with outbound-cap accounting + opt-out flag)
- v0.3: year-aware "turning N" feature
- P1.4 follow-up: `BirthdaySectionRendered` audit variant if observability is needed

---

## Verification + commit shape

- Run on srilu: `pytest tests/test_daily_brief_birthdays.py -v` against tarballed working tree
- Pass criterion: 5-7 new tests pass; existing `tests/test_daily_brief_script.py` still 100% green (zero regression)
- Commit shape: ONE commit, message `feat(agent-33): birthday reminders in Daily Brief (v0.1, Loyalty & Punch-Card minimum viable)`, ~165 LOC across 4 files
- Deploy notes:
  - `shift-agent-deploy.sh:113-115` already runs `install -m 755 src/agents/daily_brief/scripts/* /usr/local/bin/` — script propagates
  - `schemas.py` propagates via line 42
  - Template propagates via existing daily_brief templates install (line 122-124)
  - Default config: `cfg.daily_brief.sections` defaults to `["yesterday", "today_outlook", "alerts"]` (no `"birthdays"`) — operator must explicitly add it. So deploy is a no-op until owner opts in AND state file is hand-created.
  - `Config` validation must continue to pass with `customer-birthdays.json` absent (the helper falls open).

---

## Approval needed

Plan reviewers must explicitly approve before design phase. Specific decisions
to challenge:

1. **MM-DD birthday format (no year)** vs YYYY-MM-DD with optional year. Plan defaults to MM-DD for the safer contract.
2. **Section opt-in default** — `cfg.daily_brief.sections` default unchanged. Operators add `"birthdays"` to opt in. Reviewers can flip to "include by default but render 'None today.' when section is empty."
3. **No audit variant in v0.1** — matches `lookup-prior-leads-by-phone` deployed convention. Reviewers can challenge if observability gap is judged a real concern.
4. **`display_name` as separate field** vs deriving from existing roster — birthdays may belong to non-employee customers (the actual use case for SMB), so a roster lookup wouldn't suffice. Display name on the birthday record is the right shape.
5. **Helper signature returning `list[dict]` not `list[CustomerBirthday]`** — chose dicts for the same reason `_aggregate_yesterday` returns dicts (template-friendly, no schema coupling at the render boundary). Reviewers can challenge.
