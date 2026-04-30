**Drift-check tag:** `extends-Hermes` — closes 7 hygiene gaps in shipped Agent #21 v0.1. No new substrate. No new external systems. Existing patterns extended.

# Expense Bookkeeper v0.1 — Cleanup Plan

**Branch:** `cleanup/expense-bookkeeper-v01-residue` (from `main` at `179e80b`)
**Scope:** ~80 LOC + 2 doc-headers across 7 items
**Source:** `tasks/expense-bookkeeper-resume-audit.md` (2026-04-30)
**Status:** approved — proceeding to build

## Read-deployed-code commitment (Part 3)

Before drafting, I read:
- `src/platform/schemas.py:1632-1638` — `ExpenseOwnerApprovalRequested.routed_to` Literal definition
- `src/agents/expense_bookkeeper/scripts/extract-receipt:693` — only `cockpit_v01_paper` consumer
- `src/agents/expense_bookkeeper/scripts/extract-receipt:249-308` — `_collect_active_codes` + `_generate_unique_code` for collision-test fixture shape
- `tests/test_expense_bookkeeper_apply_decision.py:140-160` — `_FrozenDatetime` block context to avoid removing in-use code
- `src/agents/shift/scripts/shift-agent-deploy.sh:60-70` — existing logrotate install pattern + `install -d` placement points
- `src/agents/shift/logrotate/shift-agent` — existing logrotate config to mirror
- `src/agents/expense_bookkeeper/systemd/prune-expense-receipts.service` — log path target

## Hermes-first checklist (mandatory per CLAUDE.md)

| Step | `[Hermes]` / `[net-new]` |
|---|---|
| Drop deferred-feature placeholder enum value (`cockpit_v01_paper`) | `[Hermes]` (Pydantic Literal narrowing; no logic change) |
| Add drift-tag headers to 2 docs | `[Hermes]` (self-disclosure compliance) |
| Test: code-collision regenerate behavior | `[Hermes]` (test existing `_generate_unique_code` retry path) |
| Test: multi-receipt batch independence | `[Hermes]` (test existing per-receipt-lead architecture) |
| Remove dead `_FrozenDatetime` test class | `[Hermes]` (cleanup, not pattern change) |
| Bootstrap `/opt/shift-agent/logs` dir in deploy | `[Hermes]` (mirrors existing `install -d` pattern in deploy.sh) |
| Logrotate config for prune-expense.log | `[Hermes]` (mirrors existing `shift-agent` logrotate; same install pattern) |

**Net-new tally: 0.** All items extend existing substrate / test existing behavior / close hygiene gaps. Zero new infrastructure.

---

## 7 cleanup items

### Item 1 — Drop `cockpit_v01_paper` placeholder

**Files:**
- `src/platform/schemas.py:1636` — `routed_to: Literal["whatsapp", "cockpit_v01_paper"]` → `Literal["whatsapp"]`
- `src/agents/expense_bookkeeper/scripts/extract-receipt:693` — drop conditional, hardcode `routed_to="whatsapp"`

**Test impact:** check `tests/test_expense_bookkeeper_schemas.py` for any reference to `cockpit_v01_paper` Literal value; update if present.

**Re-add condition:** when v0.2 cockpit ships, restore the Literal with whatever the actual cockpit-routing value is.

### Item 2 — Drift-tags on 2 docs

**Files:**
- `tasks/expense-bookkeeper-v01-overnight-report.md` — top: `**Drift-check tag:** N/A — post-build status report, not a build proposal.`
- `tasks/expense-bookkeeper-v02-followups.md` — top: `**Drift-check tag:** extends-Hermes — backlog of substrate extensions; each item flagged YAGNI/DEFER/CUT in resume-audit.`

### Item 3 — Test §4g #11 — collision regenerate

**File:** `tests/test_expense_bookkeeper_guardrails.py` (new test function)

**Approach:** seed an `ExpenseLeadStore` with N leads each holding distinct `owner_approval_code`s; mock `secrets.choice` to return collision sequence then unique; assert `_generate_unique_code` does NOT collide and writes via retry.

**Surface to test:** `_generate_unique_code(store)` from `extract-receipt`. Will use `importlib.util.spec_from_file_location` to load the script as a module (matches catering test pattern).

### Item 4 — Test §4g #16 — multi-receipt batch

**File:** `tests/test_expense_bookkeeper_apply_decision.py` (new test function, Linux-only via `pytestmark`)

**Approach:** subprocess-invoke `extract-receipt` 5 times in sequence with 5 different `--source-image-id` values + 5 different image bytes; assert resulting `leads.json` has 5 entries with `expense_id` E0001..E0005 and 5 distinct `owner_approval_code`s.

**Note:** the script does the dHash + vision call. Will use canned vision response via the same fixture shape catering's batch tests use, OR fake the OpenRouter call by monkey-patching `_call_vision` post-import.

### Item 5 — Remove dead `_FrozenDatetime` block

**File:** `tests/test_expense_bookkeeper_apply_decision.py:142`

**Action:** verify the class has zero callers in the file (grep `_FrozenDatetime` within file scope), then delete the class definition + any orphan import. If it has callers, do nothing (item invalid).

### Item 6 — Bootstrap `/opt/shift-agent/logs` dir

**File:** `src/agents/shift/scripts/shift-agent-deploy.sh`

**Insertion point:** in `install_artifacts()`, near the existing `install -d ... /opt/shift-agent/state/expense-bookkeeper` block (around line 125).

**New line:**
```bash
install -d -o shift-agent -g shift-agent /opt/shift-agent/logs 2>/dev/null || true
```

(`|| true` retained for consistency with surrounding pattern; this is the project-wide convention not specific to this fix.)

### Item 7 — Prune-expense logrotate

**New file:** `src/agents/expense_bookkeeper/logrotate/prune-expense`

```
/opt/shift-agent/logs/prune-expense.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    create 0640 shift-agent shift-agent
}
```

(Mirrors `src/agents/shift/logrotate/shift-agent` shape.)

**Deploy.sh addition:** after the existing shift-agent logrotate install line:
```bash
[ -f src/agents/expense_bookkeeper/logrotate/prune-expense ] && install -m 644 src/agents/expense_bookkeeper/logrotate/prune-expense /etc/logrotate.d/
```

---

## Test plan

- Run full `pytest tests/` — all existing passes preserved; 2 new tests added (Items #3 + #4)
- Verify schema test passes after `cockpit_v01_paper` removal
- Verify `_FrozenDatetime` removal didn't break any test (item #5 includes pre-removal grep)
- Smoke test #11 unchanged (no schema-shape change visible to it; smoke #11 only asserts `enabled is False` and `qbo_client_mode == 'mock'`)

## Out of scope (deliberately, per audit)

- V02-1, V02-2, V02-5, V02-7, V02-8 — speculative / YAGNI / N/A
- V02-4, V02-6 — gated on customer + QBO sandbox creds
- Drop `|| true` masking on `systemctl enable` — project-wide pattern, not expense-specific; needs cross-cutting decision
- Tighten `test_undo_within_window_succeeds` test-bug nit — cosmetic; only-if-touched
- C-H1 no-op `atomic_write_json` micro-optimization — defensive writes are fine

## Deploy plan (post-merge)

1. PR `cleanup/expense-bookkeeper-v01-residue` → `main`
2. Squash-merge after review
3. Build tarball + scp to test VPS (when VPS config bootstrap separately resolved per overnight-report Stage 12)
4. Smoke gate verifies `enabled=False` + `qbo_client_mode=='mock'` (unchanged)

**No customer impact** — agent ships disabled-by-default; cleanup doesn't change runtime behavior for any existing deployment.

---

*Plan complete. Build phase begins.*
