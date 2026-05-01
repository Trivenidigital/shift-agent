**Drift-check tag:** `extends-Hermes` — restores read-side Literal compat using existing schema substrate; mirrors PR-D3 absorbing-shim discipline already established for catering. No new infrastructure.

# Expense Bookkeeper — PR #42 Followup Plan

**Branch:** `fix/expense-bookkeeper-routed-to-rollback-compat`
**Source:** PR #42 post-merge code review (2026-05-01) MEDIUM finding
**Scope:** 1 schema line + 1 regression test (~15 LOC total)
**Status:** approved — proceeding to build

## Read-deployed-code commitment (Part 3)

Before drafting, I read:
- `src/platform/schemas.py:1631-1638` — `ExpenseOwnerApprovalRequested.routed_to` Literal definition (post-PR #42)
- `src/platform/schemas.py` `LogEntry` discriminated union + `_UnknownLogEntry` shim shape (PR-D3 pattern)
- `tests/test_log_entry_forward_compat.py:1-80` — existing forward-compat test conventions
- `src/agents/expense_bookkeeper/scripts/extract-receipt:683-696` — current writer (hardcoded `routed_to="whatsapp"`)
- Live VPS verification (2026-05-01): `grep cockpit_v01_paper /opt/shift-agent/logs/ /opt/shift-agent/state/` → 0 hits, 0 expense_owner_approval_requested entries on test VPS

## Hermes-first checklist (per CLAUDE.md mandatory)

| Step | `[Hermes]` / `[net-new]` |
|---|---|
| Re-add `cockpit_v01_paper` to `routed_to: Literal[...]` | `[Hermes]` (Pydantic Literal widening; mirrors PR-D3 read-side-tolerant discipline) |
| Add deprecation comment + removal-after-rollback-window note | `[Hermes]` (doc-in-code, project convention) |
| Add regression test asserting old log entries validate | `[Hermes]` (existing pytest forward-compat pattern) |

**Net-new tally: 0.**

## The fix

**Reviewer's Option 3 (one-line widening):** the writer is already hardcoded to `"whatsapp"` post-PR #42. Re-add `"cockpit_v01_paper"` to the read-side Literal so any historical NDJSON row containing the value validates cleanly when daily-brief / dispatcher-accuracy-report / fsck re-load `decisions.log`.

### `src/platform/schemas.py:1636`

```python
# Before (PR #42):
routed_to: Literal["whatsapp"]

# After:
# `cockpit_v01_paper` retained as a deprecated-but-readable Literal value for
# rollback-window safety per the absorbing-shim pattern (PR-D3 / catering
# precedent). The writer in extract-receipt is hardcoded to "whatsapp"
# post-PR #42; this widening exists ONLY to keep historical decisions.log
# rows readable. Remove this value once the rollback window has lapsed
# AND a grep across all live VPSes confirms zero historical entries.
routed_to: Literal["whatsapp", "cockpit_v01_paper"]
```

### `tests/test_log_entry_forward_compat.py` — new test

```python
def test_expense_owner_approval_requested_legacy_routed_to_compat():
    """Legacy decisions.log rows written before PR #42 may contain
    routed_to='cockpit_v01_paper'. The Literal must remain widened for
    rollback-window readability per PR-D3 absorbing-shim discipline.
    Removal of the legacy value follows the rollback-window-lapse + live-VPS
    grep-zero confirmation."""
    row = {
        "type": "expense_owner_approval_requested",
        "ts": "2026-04-29T12:00:00Z",
        "expense_id": "E0001",
        "owner_approval_code": "#A47C2",
        "extracted_total_cents": 23450,
        "routed_to": "cockpit_v01_paper",  # legacy value
    }
    parsed = _ADAPTER.validate_python(row)
    # Routes to the typed variant (NOT _UnknownLogEntry — type is known)
    assert parsed.type == "expense_owner_approval_requested"
    assert parsed.routed_to == "cockpit_v01_paper"
```

## Test plan

- `pytest tests/test_log_entry_forward_compat.py -v` — confirm all existing tests + the new regression pass on Windows
- `pytest tests/` — confirm 503+ existing tests still pass
- (Linux) the new test is Windows-runnable (no fcntl, no subprocess)

## Out of scope (per Hermes-first + 6th-lens analysis 2026-05-01)

The 3 LOW + NIT items from the reviewer were filtered out:
- Smoke post-condition for `/opt/shift-agent/logs` — speculative; `\|\| true` is a project-wide pattern
- Module-level env-var cleanup — speculative; existing test files use the same pattern
- Audit doc framing — cosmetic

## Deploy plan

1. PR `fix/expense-bookkeeper-routed-to-rollback-compat` → `main`
2. Lightweight review (architecture + drift angles only — schema-widening is a focused 1-line change applying explicit reviewer-recommended Option 3)
3. Squash-merge
4. Build tarball + scp + run `shift-agent-deploy.sh deploy`
5. **No customer impact** — agent ships disabled-by-default; this only affects what historical decisions.log rows are *readable*

---

*Plan complete. Build phase begins.*
