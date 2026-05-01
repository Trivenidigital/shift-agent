**Drift-check tag:** `extends-Hermes` — F1 fixes a Python stdlib import pattern in test code; F2 extends MockQBOClient with optional file-persisted state via the existing `safe_io.atomic_write_json` chokepoint; F3 documents an upstream Hermes issue (no code change). Zero new infrastructure.

# Expense Bookkeeper — E2E Followups Plan

**Branch:** `fix/expense-e2e-followups`
**Source:** Layer A + B + C E2E findings 2026-05-01
**Scope:** ~80 LOC code + 1 test + 1 doc

## Read-deployed-code commitment (Part 3)

Before drafting, I read:
- `src/platform/qbo_client.py` — full file (current `MockQBOClient` shape, factory)
- `src/agents/expense_bookkeeper/scripts/apply-expense-decision:435` (push call site) + `:686` (void call site) — to see the factory invocation contract
- `tests/test_expense_bookkeeper_extract.py:38-50` — current importlib pattern that fails on Linux
- `tests/test_expense_bookkeeper_parser.py:40-47` + `_apply_decision.py:127-135` — same pattern
- `src/platform/safe_io.py` — atomic_write_json + load_yaml_model for schema-validated load patterns
- Live VPS reproduction: `spec_from_file_location("x", "/path/with-no-py-ext")` returns `None` on Python 3.12; `spec_from_loader("x", SourceFileLoader(...))` works

## Hermes-first checklist (per CLAUDE.md mandatory)

| Step | `[Hermes]` / `[net-new]` |
|---|---|
| F1: Switch importlib pattern to `SourceFileLoader` in 3 test files | `[Hermes]` (Python stdlib substrate; same pattern that the existing fixture *intends* to do) |
| F2: Add optional `state_path` to `MockQBOClient.__init__` | `[Hermes]` (Pydantic field optional + Path) |
| F2: Persist `_pushed` + `_seq` to JSON via `safe_io.atomic_write_json` | `[Hermes]` (existing chokepoint pattern from leads.json / decisions.log writes) |
| F2: Update factory `make_qbo_client` to accept optional state_path | `[Hermes]` (factory parameter extension; backwards-compat) |
| F2: Update apply-expense-decision to pass `MOCK_QBO_STATE_PATH` to factory | `[Hermes]` (existing module-level constant pattern; mirrors LEADS_PATH) |
| F2: Add cross-process persistence test | `[Hermes]` (existing pytest pattern) |
| F3: Document Hermes gateway hang in known-issues | `[Hermes]` (doc-only) |

**Net-new tally: 0.**

---

## F1 — Linux importlib SourceFileLoader fix

### Problem (E2E Layer A finding)

Test files `tests/test_expense_bookkeeper_parser.py`, `_extract.py`, `_apply_decision.py` use:

```python
spec = importlib.util.spec_from_file_location("name", str(SCRIPT_PATH))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```

On Python 3.12 Linux, `spec_from_file_location` returns **None** for files without `.py` extension (the agent scripts are no-extension executables). The fixture then dies with `'NoneType' object has no attribute 'loader'`.

These tests have never run on Linux since PR #30 — they're behind `pytestmark.skipif(Windows)` but only ever tested on Windows where they skip.

### Fix

Use explicit `SourceFileLoader`:

```python
from importlib.machinery import SourceFileLoader

loader = SourceFileLoader("name", str(SCRIPT_PATH))
spec = importlib.util.spec_from_loader("name", loader)
mod = importlib.util.module_from_spec(spec)
mod.__name__ = "name"
loader.exec_module(mod)
```

Apply to 3 files: parser, extract, apply_decision.

---

## F2 — MockQBOClient cross-process state persistence

### Problem (E2E Layer B finding)

`MockQBOClient._pushed` is instance-scoped. When apply-expense-decision is invoked twice (once for push, once for undo), each invocation creates a fresh MockQBOClient with empty `_pushed`. The void's `transaction_id not in self._pushed` check fails with `"unknown transaction_id (was it pushed by this client?)"`.

In production this would manifest as: owner approves at 11am → push works; owner sends `undo E0001` at 5pm → void fails with no useful error. Real QBO would not behave this way (it has server-side state).

### Design (Option B4 from E2E report — file persistence via safe_io chokepoint)

**`MockQBOClient.__init__`** gets optional `state_path: Optional[Path]`:
- If `None` (default): in-memory only — preserves existing unit-test behavior
- If provided: load + persist `_pushed` + `_seq` from JSON file via `safe_io.atomic_write_json`

**Persistence schema (JSON file):**
```json
{
  "schema_version": 1,
  "seq": 7,
  "transactions": {
    "MOCK-E0001-1": {"transaction_id": "MOCK-E0001-1", "amount_cents": 2345, "pushed_at": "2026-05-01T11:00:00-04:00"}
  }
}
```

**Re-load on every push/void** so cross-process visibility works (last-writer-wins via atomic write; no file lock needed because each operation is a single read + single write, racing produces last-good-state which is acceptable for a mock).

**Factory `make_qbo_client`** gains optional `state_path` param; defaults to `None` (in-memory) so existing test callers aren't affected.

**`apply-expense-decision`** declares `MOCK_QBO_STATE_PATH = Path("/opt/shift-agent/state/expense-bookkeeper/mock-qbo-pushed.json")` as a module-level constant (same pattern as `LEADS_PATH`) and passes it to both factory call sites.

### Why not just always-succeed void?

Two reasons:
1. The unknown-transaction-id check has unit-test value — verifies the wrong-transaction-id error path. Removing it makes the mock weaker as a contract simulator.
2. Real QBO returns a "transaction not found" error for unknown IDs. Persisting state and surfacing the same error mode is closer to real-QBO behavior than silent always-success.

### Schema-versioning + corruption handling

`schema_version: int = 1` field allows future migration. On read, if the file is corrupted (not valid JSON or fails Pydantic validation), the constructor raises — failing loudly is better than silently losing transaction state. Mirrors `safe_io.load_yaml_model` discipline.

### Test

New Linux-only test in `tests/test_expense_bookkeeper_qbo_mock.py`:

```python
def test_mock_void_works_across_process_boundaries(tmp_path):
    """E2E Layer B finding: MockQBOClient must support cross-process undo
    (push in process 1, void in process 2). Without state persistence, the
    void raises 'unknown transaction_id'. With state_path, two separate
    instances share the persisted ledger."""
    state = tmp_path / "mock-qbo.json"
    lead = _make_lead(expense_id="E0001", owner_confirmed_total_cents=2345)

    # Process 1: push
    client1 = MockQBOClient(state_path=state)
    result = client1.push_expense(lead)
    assert result.transaction_id == "MOCK-E0001-1"
    del client1  # simulate process exit

    # Process 2: void (fresh client, same state_path)
    client2 = MockQBOClient(state_path=state)
    client2.void_transaction("MOCK-E0001-1")  # must NOT raise

    # Process 3: void same transaction again — should fail (already voided)
    client3 = MockQBOClient(state_path=state)
    with pytest.raises(QBOPushError, match="unknown"):
        client3.void_transaction("MOCK-E0001-1")
```

Existing tests stay unchanged (they instantiate without `state_path` → in-memory).

---

## F3 — Hermes gateway hang documentation

### Problem (E2E Layer C finding)

Hermes gateway hung at `agent.auxiliary_client: Auxiliary auto-detect: using main provider openrouter (moonshotai/kimi-k2-thinking)` for 4+ minutes. Same pattern as today's 13:29 hang (320s, 11 API calls, 0 response chars). Bridge has been disconnecting with code -15 multiple times today.

### Fix

**This is upstream of our repo (Hermes-agent code, not shift-agent).** No code fix from this side.

Add a known-issues entry to `docs/hermes-alignment.md` Part 2 (operational drift checklist) so future contributors know the symptom + observed pattern + workaround (gateway restart). This is honest documentation, not a paper-over.

---

## Test plan

- `pytest tests/` clean on Windows (existing 569 + new `test_mock_void_works_across_process_boundaries` Windows-runnable since it uses tmp_path + no fcntl)
- Re-run Layer A on Linux VPS post-merge to confirm the 3 importlib-affected test files now actually run
- `tests/test_expense_bookkeeper_qbo_mock.py::test_mock_void_works_across_process_boundaries` exercises F2

## Out of scope (per audit + 6th-lens)

- F3 actual fix (upstream Hermes — out of our repo)
- Schema migration tooling for the new mock-qbo-pushed.json file (premature; v0.2 will use RealQBOClient anyway)
- Lock-based concurrency on the mock state file (atomic-write-json gives last-writer-wins which is fine for a single-tenant mock)

## Deploy plan

1. PR `fix/expense-e2e-followups` → `main`
2. 2-reviewer round (architecture + drift) — small focused scope
3. Squash-merge
4. Build tarball + scp + run `shift-agent-deploy.sh deploy`
5. Re-run Layer A on Linux VPS to confirm F1 actually unlocks the previously-skipped tests
6. **No customer impact** — agent ships disabled-by-default; F2 only affects MockQBOClient (which is non-production); F1 is test-only; F3 is doc-only

---

*Plan complete. Build phase begins.*
