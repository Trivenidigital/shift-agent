# PR-D — Design doc (v2 after 5-agent review)

**Drift-check tag:** `drifts-from-Hermes` (escalated from `extends-Hermes` per R5 H-1; `_UnknownLogEntry` with `type: str` + `extra="allow"` explicitly fights deployed `Literal[...]` + `extra="forbid"` invariants. Compensating infrastructure documented in §7 + §11 + §14.)

**v2 revisions section: §14 at the bottom of this doc supersedes any conflicting earlier text.** All 3 BLOCKERs + 8 HIGH + 9 MEDIUM findings are addressed in §14 with concrete patches; build phase reads from §14 first, then the body for context.

**Supersedes:** `tasks/pr-d-medium-items-plan.md` v3 §"Plan v3 — 5-agent plan-review revisions". This doc encodes all 3 BLOCKERs + 8 high + 12 medium + 5 low findings as concrete schema, lock-ordering, helper, deploy, and test specs. Build phase reads from this doc, NOT the plan.

**Pipeline position:** Plan ✅ → Plan-review ✅ (3 BLOCKERs surfaced + this doc resolves) → **Design ← you are here** → Design-review (5 parallel) → fix → Build (PR-D1 + PR-D2 split) → PR + 5-review × 2 → merge → deploy.

---

## 1. Read-deployed-code re-verification

| File | Line range read | Confirmed |
|---|---|---|
| `src/platform/schemas.py` | 1180-1300 (`_BaseEntry` + early variants) | Pydantic v2; `model_config = ConfigDict(extra="forbid")`; `mode='before'` ts validator pattern |
| `src/platform/schemas.py` | 1690-1880 (catering v0.3 anchors + LogEntry union) | `CateringQuoteAttempted` exists with fields `lead_id`, `original_message_id`, `code`. **No writers** in catering scripts — confirmed by grep. LogEntry uses `Field(discriminator="type")`. |
| `src/platform/safe_io.py` | 1-100 + 220-350 (FileLock + atomic + ndjson + load_yaml_model) | `FileLock` is fcntl LOCK_EX; `ndjson_append` is O_APPEND+fsync (no internal locking — caller responsible); `load_yaml_model` raises `FileNotFoundError | RuntimeError | ValidationError` |
| `src/agents/catering/scripts/apply-catering-owner-decision` | 64-67 (paths), 206-210 (`_log` helper), 220-410 (main) | `_log()` does `with flock(LOG_PATH): ndjson_append(...)` — ACQUIRES SECOND LOCK (M10 finding); LEADS_LOCK held outer. The post-bridge audit at line 397 references `store.leads[i].customer_phone` where `i` leaks from the for-loop (H3 finding confirmed). |
| `src/agents/catering/scripts/{create-catering-lead,lookup-prior-leads-by-phone,parse-menu-photo,apply-menu-update}` | yaml.safe_load callsites | 5 callsites total, NOT 4 (plan undercount). All match pattern `Config.model_validate(yaml.safe_load(CONFIG_PATH.read_text(...)))`. |
| `src/platform/scripts/check-safe-io-symbols` | full file (50 lines) | Pre-restart gate covers safe_io only. Need parallel `check-audit-helpers-symbols` for new module (H7 resolution). |

**One correction to plan**: yaml callsite count is 5, not 4. Migration scope grows by 1 (parse-menu-photo + apply-menu-update). Plan said "4 inline callsites + smoke-test"; reality is "5 inline callsites + smoke-test" (smoke test 3rd-party path TBD).

---

## 2. Decisions log

### BLOCKERs (all resolved; design encodes resolution below)

| # | Resolution path | Section |
|---|---|---|
| B1 | Callable `Discriminator` + `_UnknownLogEntry` passthrough | §3.1 |
| B2 | Canonical write order + retry-state-advance contract pinned | §6 |
| B3 | **Split deploy** PR-D1 → soak ≥24h → PR-D2; operational runbook also written for safety net | §10 + §11 |

### High-priority

| # | Resolution path | Section |
|---|---|---|
| H1 | **PR-D1 (schema infrastructure) + PR-D2 (behavior changes)** — split per H1 recommendation | §2.1 + §10 |
| H2 | PR-D1 + PR-D2 BOTH merge before PR-B starts; PR-B branches from PR-D2-merged main | §10 |
| H3 | Replace for-loop index leak with `next((i,l) for ...)` early-bind | §6.2 |
| H4 | New 13th commit: `catering-lead-reconcile` operator script | §8 |
| H5 | `2>&1 \| logger -t catering-skill-... \|\| true` instead of `\|\| true` | DEFERRED — pairs with M12 (lookup_invoked → PR-B) |
| H6 | Smoke test variant round-trip extension | §9.2 |
| H7 | Add new `check-audit-helpers-symbols` pre-restart gate | §4.3 |
| H8 | `bridge_post_outcome: Literal["success","failed","unknown"]` field on `CateringQuoteAttempted` | §3.3 + §6 |

### Medium-priority

| # | Resolution path | Section |
|---|---|---|
| M1 | `original_message_id` added to `CateringQuoteSentStateMissing` | §3.2 |
| M2 | Renamed `CateringStateOutboundDivergence` → `CateringQuoteSentStateMissing`; type literal `catering_quote_sent_state_missing` | §3.2 |
| M3 | `_log_config_load_failed_best_effort` lives in NEW `src/platform/audit_helpers.py` | §4 |
| M4 | Helper uses `datetime.now(timezone.utc)` always — no tz-source dependency | §4 |
| M5 | Tail-N matcher-widen scan pinned at N=500; falls through to EXIT_NOT_FOUND | §6.3 |
| M6 | Test counts trimmed to ~8/variant (24 total) for the 3 LogEntry variants; conftest hoist test budget rebalanced | §9.1 + §9.3 |
| M7 | v02 callsite count corrected to 108 in commit messages | §9.3 |
| M8 | Renumber: probe = commit 10, hoist = commit 11 | §10 |
| M9 | Deploy soak bumped to 20-min for PR-D2 | §11 |
| M10 | `_log()` replaced with direct `ndjson_append(LOG_PATH, ...)` inside LEADS_LOCK | §6.1 |
| M11 | NEW `tests/_shared_catering_helpers.py` (sibling, no auto-load); `_b1_helpers.py` re-exports for backwards compat | §9.3 |
| M12 | `lookup_invoked` deferred to PR-B (Tasks 3 + 9 dropped from PR-D scope) | §10 |

### Low-priority

| # | Resolution path |
|---|---|
| L1 | Convention-departure callout in §3.1 prose |
| L2 | Drift tag stays `extends-Hermes` |
| L3 | Tombstone reference dropped from doc revision (Task 14) |
| L4 | Helper signature drops `script` param |
| L5 | `lead_phone_canonical` field has prose-comment in schema docstring |

### 2.1 PR split (H1)

| | PR-D1 (medium pipeline) | PR-D2 (full pipeline) |
|---|---|---|
| Goal | Schema + helper infrastructure ONLY | Behavior changes that emit infrastructure + observability |
| Touches schemas.py? | YES (3 additions: shim, 2 variants) | NO |
| Touches catering scripts? | NO | YES (apply-decision rewrite + yaml migration) |
| Risk | Low — additive; old code path unchanged | Medium — apply-decision rewrite + lock-ordering change |
| Rollback hazard | None — no emitters yet, so prior tarball never sees unknown variants | Mitigated by PR-D1's shim already being deployed |
| Soak | 24h | 20-min (M9) |
| Sequencing | First | Branched from PR-D1-merged main |

---

## 3. Schemas.py changes (PR-D1)

### 3.1 Forward-compat shim (B1 + L1)

**Convention departure (L1):** This is the first deployment of an unknown-tag passthrough on a `LogEntry` discriminated union. Past variants have always required exact `Literal[...]` matching. The shim downgrades unrecognized `type` values to a `_UnknownLogEntry` model that captures raw fields without validation, so a prior-tarball binary can ingest a future-tarball variant emitted during a partial-rollback window without raising.

```python
# src/platform/schemas.py — replaces the existing LogEntry definition

from pydantic import Discriminator, Tag
from typing import Annotated, Union

class _UnknownLogEntry(_BaseEntry):
    """Forward-compat passthrough for unrecognized LogEntry `type` values.

    Old binaries reading rows written by newer binaries downgrade unknown
    `type` values to this model rather than raising ValidationError. The
    raw payload is captured so audit-replay tooling can still inspect.

    Concrete invariant: old code SEES the unknown row but does NOT act on
    it (no isinstance branch matches _UnknownLogEntry by design). New code
    that emits a typed variant continues to round-trip the typed model.
    """
    model_config = ConfigDict(extra="allow")  # capture unknown fields as-is
    type: str  # NOT Literal — accepts any string the discriminator routes here
    # ts inherited from _BaseEntry (kept strict; tz coercion still applies)


def _pick_log_entry_tag(v: Any) -> str:
    """Discriminator picker (Pydantic v2 callable form).

    Returns the value of `type` if it matches a known variant's Literal,
    else the sentinel `"_unknown_"` which routes to _UnknownLogEntry.
    """
    if isinstance(v, dict):
        t = v.get("type")
    else:
        t = getattr(v, "type", None)
    if t in _KNOWN_LOG_ENTRY_TYPES:
        return t
    return "_unknown_"


_KNOWN_LOG_ENTRY_TYPES: frozenset[str] = frozenset({
    # Listed exhaustively. The CI test in §9.1 asserts this set matches the
    # union below — drift between the two surfaces in test, not at runtime.
    "raw_inbound", "proposal_created", "proposal_status_change",
    "outbound_attempted", "outbound_sent", "outbound_send_failed",
    "outbound_response", "outbound_cap_exceeded", "outbound_refused_disabled",
    "agent_state_change", "unknown_sender_declined", "invariant_violation",
    "health_check_failure", "lid_learned", "dispatcher_routed",
    "brief_attempted", "brief_sent", "brief_send_failed", "brief_skipped",
    "eod_snapshot", "eod_pushover_sent", "eod_skipped",
    "cross_location_query", "inter_location_transfer_proposed",
    "catering_lead_created", "catering_lead_status_change",
    "catering_lead_rejected", "catering_quote_drafted",
    "catering_owner_approval_requested", "catering_owner_decision",
    "catering_quote_sent",
    "catering_quote_attempted", "catering_owner_approval_card_attempted",
    "catering_owner_approval_card_failed", "catering_owner_approval_card_skipped",
    "catering_owner_edited", "catering_decline_attempted",
    "catering_quote_render_failed",
    "menu_update_proposed", "menu_update_applied", "menu_update_rejected",
    "expense_receipt_received", "expense_duplicate_detected",
    "expense_extraction_completed", "expense_classification_proposed",
    "expense_owner_approval_requested", "expense_owner_decision",
    "expense_lead_status_change",
    "expense_push_attempted", "expense_pushed", "expense_push_failed",
    "expense_reversal_requested", "expense_reversed",
    "expense_receipt_pruned", "expense_non_owner_undo_declined",
    "expense_orphan_detected",
    # NEW PR-D1
    "catering_quote_sent_state_missing", "config_load_failed",
    # NEW _UnknownLogEntry sentinel
    "_unknown_",
})


LogEntry = Annotated[
    Union[
        Annotated[RawInbound, Tag("raw_inbound")],
        Annotated[ProposalCreated, Tag("proposal_created")],
        # ... all known variants Tagged ...
        Annotated[CateringQuoteSentStateMissing, Tag("catering_quote_sent_state_missing")],  # NEW
        Annotated[ConfigLoadFailed, Tag("config_load_failed")],                              # NEW
        Annotated[_UnknownLogEntry, Tag("_unknown_")],                                       # NEW shim
    ],
    Discriminator(_pick_log_entry_tag),
]
```

**Why callable Discriminator over `Field(discriminator="type")`:** the field-string form rejects unknown `type` values before any validator runs, raising `union_tag_invalid`. The callable form lets us route the unknown to a known passthrough tag. This is the canonical Pydantic v2 pattern for forward-compat.

**Why `Tag(...)`:** when the discriminator is a callable, each member of the union must carry an explicit `Tag` — Pydantic uses the tag value to match the picker's return.

**Acceptance test (commit 1):** `tests/test_log_entry_forward_compat.py` round-trips
1. one fixture per known variant (~50) — assert `isinstance(parsed, ConcreteVariant)`.
2. a synthetic `{"type": "future_xyz", "ts": "2026-01-01T00:00:00Z", "extra_field": 42}` line — assert `isinstance(parsed, _UnknownLogEntry)` AND `parsed.type == "future_xyz"` AND `parsed.model_extra == {"extra_field": 42}`.
3. a malformed `{"type": "raw_inbound"}` (missing required fields) — assert `ValidationError` raised, NOT silent fallback to `_UnknownLogEntry`. Critical: the picker only routes to `_unknown_` if `type` is unrecognized; a recognized type with bad fields must still raise.
4. `_KNOWN_LOG_ENTRY_TYPES` matches the actual Tagged union (CI guard against drift) — introspect the union args via `typing.get_args`, extract each `Tag`, assert the set equals `_KNOWN_LOG_ENTRY_TYPES`.

### 3.2 `CateringQuoteSentStateMissing` (M1 + M2)

```python
class CateringQuoteSentStateMissing(_BaseEntry):
    """Emitted by apply-catering-owner-decision when the post-bridge re-load
    of leads.json finds the lead absent. Customer received quote but
    SENT_TO_CUSTOMER not persisted; operator must reconcile via
    catering-lead-reconcile (see §8).

    Replaces the prior plan-name 'CateringStateOutboundDivergence'
    (review M2: naming convention is Catering<Verb><Noun>).
    """
    type: Literal["catering_quote_sent_state_missing"]
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)  # M1
    customer_phone_at_approve: E164Phone           # captured BEFORE post-bridge re-load
    outbound_message_id: str = Field(min_length=1) # bridge accepted this
    detail: str = Field(default="", max_length=500)
```

### 3.3 `CateringQuoteAttempted` extension (H8)

```python
class CateringQuoteAttempted(_BaseEntry):
    """v0.3 idempotency anchor for customer-quote send.

    PR-D1 extension (H8): adds `bridge_post_outcome` so retries can
    distinguish 'anchor-then-success' (skip bridge POST) from 'anchor-
    then-failure' (retry bridge POST). Without this field, an anchor +
    failed bridge POST would create a stuck-loop where retries see the
    anchor and never re-attempt.
    """
    type: Literal["catering_quote_attempted"]
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
    # H8: outcome of the bridge POST that this anchor preceded.
    # Two-step write: first write with outcome="unknown" before bridge call,
    # then on bridge return write a second anchor row with outcome=success|failed
    # (NDJSON is append-only — second row supersedes first via tail-scan).
    bridge_post_outcome: Literal["success", "failed", "unknown"] = "unknown"
```

**Backwards compat:** existing rows with no `bridge_post_outcome` field — the field has a default `"unknown"`, so Pydantic fills it in. v0.3 anchors written by the long-deployed schema (none in the wild — confirmed by no writers) round-trip cleanly.

### 3.4 `ConfigLoadFailed` (M3 + M4 already; here is the schema)

```python
class ConfigLoadFailed(_BaseEntry):
    """Emitted best-effort by helper in audit_helpers.py when a config
    file fails to load (parse error, validation error, FileNotFoundError,
    OSError). Captures the error class + path so operators can correlate
    a missing-config bug with the specific script that hit it.
    """
    type: Literal["config_load_failed"]
    path: str = Field(min_length=1)
    error_class: str = Field(min_length=1, max_length=80)
    error_detail: str = Field(default="", max_length=2000)
    # No `script` param (L4) — helper computes via Path(sys.argv[0]).name and
    # captures it inline:
    script_name: str = Field(min_length=1, max_length=80)
```

---

## 4. New `src/platform/audit_helpers.py` (M3 + M4 + L4)

### 4.1 Module rationale

`safe_io.py` is filesystem/lock primitives; importing `schemas.py` from there creates a cycle (schemas references `safe_io` for its serializers). Audit helpers are at a different layer: they need both the file-write primitives AND the schema TypeAdapter. New module.

### 4.2 Helper

```python
# src/platform/audit_helpers.py — NEW
"""Audit-emission helpers that need both schemas + safe_io primitives.

Helpers MUST be best-effort: if the audit-write itself fails, the helper
swallows the secondary error and returns silently — the caller is
typically already in a primary error path (config-load failed, lead
diverged) and double-failure must not shadow the first.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Optional

from pydantic import TypeAdapter

# safe_io provides the chokepoint
from safe_io import ndjson_append, FileLock
# schemas provides the variant
from schemas import ConfigLoadFailed, CateringQuoteSentStateMissing


_LOG_PATH_DEFAULT = Path("/opt/shift-agent/logs/decisions.log")
_LOG_LOCK_DEFAULT = Path("/opt/shift-agent/logs/decisions.log.lock")


def log_config_load_failed_best_effort(
    config_path: Path,
    exc: BaseException,
    log_path: Path = _LOG_PATH_DEFAULT,
) -> None:
    """Append a config_load_failed row. NEVER raises.

    Always uses datetime.now(timezone.utc) — when config fails to load, the
    customer-tz source isn't available, so UTC is the only safe ts (M4).

    Never propagates errors: if ndjson_append fails (disk full, permission
    error, etc.), this returns silently — the caller is already in a
    primary error path.
    """
    try:
        entry = ConfigLoadFailed(
            type="config_load_failed",
            ts=datetime.now(timezone.utc),
            path=str(config_path),
            error_class=type(exc).__name__,
            error_detail=str(exc)[:2000],
            script_name=Path(sys.argv[0]).name or "<unknown>",
        )
        line = TypeAdapter(ConfigLoadFailed).dump_json(entry).decode("utf-8")
        # Use FileLock (.lock sibling) — best-effort; if lock unavailable,
        # try without (concurrent appender contention is informational).
        try:
            with FileLock(Path(str(log_path) + ".lock")):
                ndjson_append(log_path, line)
        except Exception:
            ndjson_append(log_path, line)  # tolerate concurrent writes
    except Exception:
        pass  # double-fault — give up


def log_quote_sent_state_missing_best_effort(
    lead_id: str,
    original_message_id: str,
    customer_phone_at_approve: str,
    outbound_message_id: str,
    detail: str = "",
    log_path: Path = _LOG_PATH_DEFAULT,
) -> None:
    """Best-effort emission of state-vs-outbound divergence audit row.
    Same swallow-all-errors contract as log_config_load_failed_best_effort.
    """
    try:
        entry = CateringQuoteSentStateMissing(
            type="catering_quote_sent_state_missing",
            ts=datetime.now(timezone.utc),
            lead_id=lead_id,
            original_message_id=original_message_id,
            customer_phone_at_approve=customer_phone_at_approve,
            outbound_message_id=outbound_message_id,
            detail=detail[:500],
        )
        line = TypeAdapter(CateringQuoteSentStateMissing).dump_json(entry).decode("utf-8")
        try:
            with FileLock(Path(str(log_path) + ".lock")):
                ndjson_append(log_path, line)
        except Exception:
            ndjson_append(log_path, line)
    except Exception:
        pass
```

### 4.3 New pre-restart gate `check-audit-helpers-symbols`

```python
# src/platform/scripts/check-audit-helpers-symbols — NEW
#!/usr/bin/env python3
"""Pre-restart import gate for audit_helpers.py module symbols.
Mirrors check-safe-io-symbols. Catches missing-symbol regressions
in audit_helpers BEFORE hermes-gateway restarts.
"""
from __future__ import annotations
import sys

REQUIRED_SYMBOLS = (
    "log_config_load_failed_best_effort",
    "log_quote_sent_state_missing_best_effort",
)

def main() -> int:
    sys.path.insert(0, "/opt/shift-agent")
    try:
        import audit_helpers
    except ImportError as e:
        sys.stderr.write(f"FAIL: cannot import audit_helpers: {e}\n")
        return 1
    missing = [n for n in REQUIRED_SYMBOLS if not hasattr(audit_helpers, n)]
    if missing:
        sys.stderr.write(f"FAIL: audit_helpers missing symbols: {missing}\n")
        return 1
    print("AUDIT_HELPERS_SYMBOLS_OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

`shift-agent-deploy.sh` pre-restart import block extended to call both `check-safe-io-symbols` AND `check-audit-helpers-symbols`. PR-D1 adds the new helper file + the new check; PR-D2 doesn't touch it.

---

## 5. Yaml migration (PR-D2 commit 1)

5 callsites to migrate (one more than plan said):

| File | Line | Change |
|---|---|---|
| `apply-catering-owner-decision` | 230-236 | `cfg = load_yaml_model(CONFIG_PATH, Config)` + `try/except (FileNotFoundError, RuntimeError, ValidationError) as e: log_config_load_failed_best_effort(CONFIG_PATH, e); sys.stderr.write(...); return EXIT_SCHEMA_VIOLATION` |
| `create-catering-lead` | ~343-347 | same pattern |
| `lookup-prior-leads-by-phone` | ~250 | same pattern |
| `parse-menu-photo` | 256 | same pattern |
| `apply-menu-update` | 75 | same pattern |
| `shift-agent-smoke-test.sh` step 3 | n/a | Existing yaml.safe_load there is INTENTIONALLY direct — smoke step is testing the file, not the helper. Leave unchanged + add comment. |

**Note:** original plan said 4 inline + smoke. Reality is 5 inline + smoke. Updated commit-message draft below.

**Acceptance test for migration:** `tests/test_catering_config_migration.py` — for each script that takes `--config-path`, write a malformed YAML to a tmp file, invoke the script via subprocess, assert (a) exit code is `EXIT_SCHEMA_VIOLATION` (= 4), (b) `decisions.log` in the script's working dir contains a `config_load_failed` row with `path` matching the tmp file, (c) `error_class == "RuntimeError"` (load_yaml_model wraps yaml errors in RuntimeError per its docstring).

---

## 6. apply-catering-owner-decision rewrite (PR-D2 commits 2-4)

This is the highest-risk section. Three concerns interlock: M10 (drop second flock for atomic ordering), B2 (canonical write order + retry contract), H3 (eliminate index leak), H8 (anchor outcome field). Designed as one rewrite landing in 3 commits for review-ability:

- Commit 2: M10 + H3 (drop `_log()` second-flock; matched_idx idiom; emit `CateringQuoteSentStateMissing` on missing-lead). No retry-semantics changes yet.
- Commit 3: B2 anchor write reordering + H8 outcome field — adds `CateringQuoteAttempted` write BEFORE bridge POST inside same lock; second anchor row written after bridge return with outcome.
- Commit 4: B2 retry-state-advance + matcher widen (Task 8) — apply-script accepts OWNER_APPROVED + anchor-with-success and idempotent-replays SENT_TO_CUSTOMER advance.

### 6.1 Drop second flock (M10)

`_log()` helper (lines 206-210) currently does `with flock(LOG_PATH): ndjson_append(...)` inside an outer `with FileLock(LEADS_LOCK):`. The outer lock already serializes ALL apply-script work. The inner LOG_PATH lock is only relevant when concurrent appenders from OTHER scripts (lookup-prior-leads, create-catering-lead) write to the SAME log file in parallel — but those scripts hold their own state-locks during their critical sections, never overlapping with apply-decision's LEADS_LOCK.

Wait — that's wrong. Different scripts hold DIFFERENT state-locks. Two scripts can be writing to LOG_PATH simultaneously. The LOG_PATH lock IS needed for cross-script append serialization.

**Correct M10 resolution:** keep the LOG_PATH lock for cross-script safety, but acknowledge it's outside the LEADS_LOCK transactional boundary. Anchor-row + state-mutation atomicity comes from doing them in a single critical section that holds BOTH locks in nested order: `LEADS_LOCK` (outer) then `LOG_PATH` (inner per `_log()` call).

After re-reading the plan (M10): "LEADS_LOCK dominates; LOG_PATH.lock is for cross-script concurrent appenders, redundant when outer lock held". The plan's claim is INCORRECT — LEADS_LOCK does NOT serialize lookup-prior-leads or create-catering-lead's writes to LOG_PATH; those scripts write through their own state locks (LEADS_LOCK they share with apply-decision).

Actually they DO share LEADS_LOCK. Let me check:

| Script | State lock | Writes to LOG_PATH? |
|---|---|---|
| `apply-catering-owner-decision` | LEADS_LOCK | yes |
| `create-catering-lead` | LEADS_LOCK (same path) | yes |
| `lookup-prior-leads-by-phone` | LEADS_LOCK (read-only after PR-A pivot) | no (writer-side flock for read) |
| `parse-menu-photo` | (different state file) | yes |
| `apply-menu-update` | (different state file) | yes |

`parse-menu-photo` + `apply-menu-update` write to LOG_PATH but hold a DIFFERENT state lock. Their LOG_PATH writes can race with apply-catering's LOG_PATH writes. The inner LOG_PATH flock IS needed.

**Final M10 resolution (corrected):** keep LOG_PATH flock, but inline the calls inside the LEADS_LOCK block so the lock-acquisition order is explicit and no temporary state escapes:

```python
# Replace _log() helper body with direct flock+ndjson_append calls inline.
# Cleaner: single helper with explicit lock-or-not contract.

def _append_log_with_outer_leadslock(adapter: TypeAdapter, entry) -> None:
    """Append to decisions.log while LEADS_LOCK is held by caller.
    Acquires LOG_PATH flock as inner lock (required for cross-script
    serialization with parse-menu-photo / apply-menu-update which hold
    DIFFERENT state locks and also write LOG_PATH). Lock-acquisition
    order: outer LEADS_LOCK → inner LOG_PATH lock. All catering scripts
    follow this nesting; menu scripts hold no LEADS_LOCK so cannot
    deadlock here.
    """
    with flock(LOG_PATH):
        ndjson_append(LOG_PATH, adapter.dump_json(entry).decode("utf-8"))
```

Equivalent semantics to current `_log()`, but the docstring pins the lock-order invariant. Net code: ~5 LOC change, no behavioral diff. The original M10 finding rests on a wrong premise; design captures the corrected reasoning.

### 6.2 H3 matched_idx idiom + EXIT_SCHEMA_VIOLATION on missing

Replaces the post-bridge re-load at line 378-385:

```python
# OLD (BUGGY):
for i, l in enumerate(store.leads):
    if l.lead_id == lead_id_for_output:
        store.leads[i] = l.model_copy(update={...})
        break
atomic_write_json(LEADS_PATH, store)
# ... line 397: store.leads[i].customer_phone — INDEX LEAK if no match

# NEW:
matched_idx = next(
    (i for i, l in enumerate(store.leads) if l.lead_id == lead_id_for_output),
    None,
)
if matched_idx is None:
    # Customer received quote (bridge POST succeeded), but lead is gone
    # from re-loaded store. Emit divergence + Pushover P2 + exit schema-violation.
    log_quote_sent_state_missing_best_effort(
        lead_id=lead_id_for_output,
        original_message_id=args.original_message_id,
        customer_phone_at_approve=customer_phone_pre_bridge,  # captured before lock release
        outbound_message_id=mid_or_err,
        detail=f"post-bridge re-load lost lead (status={status!r})",
    )
    _pushover_p2(f"BUG state-outbound divergence (lead {lead_id_for_output})", ...)
    return EXIT_SCHEMA_VIOLATION

store.leads[matched_idx] = store.leads[matched_idx].model_copy(update={
    "status": "SENT_TO_CUSTOMER",
    "updated_at": customer_now(cfg.customer.timezone),
})
atomic_write_json(LEADS_PATH, store)
# ... CateringQuoteSent now uses store.leads[matched_idx].customer_phone — never wrong
```

`customer_phone_pre_bridge` is captured before the first lock release (line 301 area: `target_jid = f"{lead.customer_phone.lstrip('+')}@s.whatsapp.net"` — already computes phone; just save it as a separate variable for the divergence audit).

**Acceptance test:** `tests/test_catering_apply_post_bridge_missing_lead.py` — uses BridgeStub side-effect to delete lead from leads.json after the bridge POST returns success. Assert (a) exit code 4, (b) `catering_quote_sent_state_missing` row exists with correct `lead_id` + `original_message_id` + `outbound_message_id` + non-empty `customer_phone_at_approve`, (c) Pushover invocation captured by mock, (d) NO `catering_quote_sent` row written.

### 6.3 B2 canonical write order + H8 anchor outcome

Pinning the lock-and-write order resolves both BLOCKER concerns. Three ordering cases:

**Case A: fresh approve (no prior anchor for this code).**
1. Acquire LEADS_LOCK.
2. Find lead; if no match → existing EXIT_NOT_FOUND (no change).
3. Mutate store: status `AWAITING_OWNER_APPROVAL → OWNER_APPROVED`.
4. `atomic_write_json(LEADS_PATH, store)`.
5. Emit `CateringLeadStatusChange` + `CateringOwnerDecision` (existing audit).
6. Emit `CateringQuoteAttempted` with `bridge_post_outcome="unknown"` — anchor BEFORE bridge POST. Same LEADS_LOCK.
7. Release LEADS_LOCK.
8. Bridge POST (network call, must NOT hold any lock).
9. Re-acquire LEADS_LOCK.
10. Look up matched_idx (per §6.2). If missing → divergence path.
11. Mutate status `OWNER_APPROVED → SENT_TO_CUSTOMER`.
12. `atomic_write_json(LEADS_PATH, store)`.
13. Emit `CateringQuoteSent` with bridge outcome.
14. Emit `CateringQuoteAttempted` with `bridge_post_outcome="success"` — supersedes step-6 anchor row via tail-scan.
15. Release LEADS_LOCK.

**Case B: bridge POST fails after anchor write (between steps 7 and 9 in Case A).**
- Anchor row exists with `bridge_post_outcome="unknown"`.
- Lead status is OWNER_APPROVED.
- Process exits with EXIT_DEPENDENCY_DOWN. **No second anchor row written (process died or returned non-zero before step 14).**
- Operator/retry sees lead in OWNER_APPROVED + anchor-unknown.

**Case C: retry of Case B (operator re-invokes apply-decision with same code).**
1. Acquire LEADS_LOCK.
2. Tail-scan last 500 lines of decisions.log for `catering_quote_attempted` rows matching `code` (M5).
3. If found AND `bridge_post_outcome == "success"`: lead is fully sent. Emit `idempotent_replay=True` log line, return EXIT_OK no-op.
4. If found AND `bridge_post_outcome in {"failed", "unknown"}`: bridge POST may not have succeeded. RE-ATTEMPT bridge. Resume Case A from step 8.
5. If NOT found: existing matcher logic (which now uses widened criterion below).

**Matcher widen (Task 8):**

```python
matches = [l for l in store.leads
           if l.owner_approval_code == code
           and l.status == "AWAITING_OWNER_APPROVAL"]
if not matches:
    # PR-D2 (B2 + Task 8): if status is OWNER_APPROVED + anchor row exists with
    # outcome="success", customer already has the quote — return idempotent_replay.
    # If outcome in failed/unknown, RE-ATTEMPT bridge POST.
    code_match = [l for l in store.leads if l.owner_approval_code == code]
    if code_match and code_match[0].status in ("OWNER_APPROVED", "SENT_TO_CUSTOMER"):
        anchor = _tail_scan_anchor(LOG_PATH, code, max_lines=500)  # M5
        if anchor and anchor.bridge_post_outcome == "success":
            print(json.dumps({"lead_id": code_match[0].lead_id,
                              "new_status": code_match[0].status,
                              "outbound_sent": True,
                              "idempotent_replay": True}))
            return EXIT_OK
        if anchor and anchor.bridge_post_outcome in ("failed", "unknown"):
            # Resume from bridge POST step (Case C step 4)
            lead = code_match[0]
            # ... fall through to bridge-resume code path
        # else: anchor missing → existing EXIT_NOT_FOUND with helpful stderr
    return EXIT_NOT_FOUND if not anchor else _resume_after_anchor(lead, anchor, ...)
```

`_tail_scan_anchor()`: opens decisions.log at end-of-file, reads back up to 500 lines via `seek` + `readlines`, parses each line via `TypeAdapter(LogEntry)`, filters for `CateringQuoteAttempted` with matching `code`, returns the LAST one (highest-priority outcome). N=500 is the bound from M5; if scan exhausts without finding a match, returns None and falls through to existing EXIT_NOT_FOUND.

**Why 500 specifically:** decisions.log is rotated daily on production (logrotate config); 500 lines covers ~10-15 minutes of moderate traffic, far exceeding the human-retry window for an approval code.

**Acceptance tests for matcher widen + idempotent replay:** `tests/test_catering_apply_idempotent_replay.py` — five cases:
1. Anchor + outcome=success → EXIT_OK no-op, no new bridge call.
2. Anchor + outcome=failed → bridge POST re-attempted, success advances to SENT_TO_CUSTOMER.
3. Anchor + outcome=unknown → bridge POST re-attempted (treated like failed).
4. Code matches OWNER_APPROVED but no anchor row → EXIT_NOT_FOUND (existing helpful stderr).
5. Tail-scan of decisions.log truncated below 500-line threshold → still finds anchor (proves N=500 sufficient for fixture).

---

## 7. Operational rollback runbook (B3 safety net)

PR-D1 ships shim FIRST, soaks ≥24h. After the soak window the prior-tarball-of-record contains the shim too, so PR-D2 rollback restores a tarball that already handles unknown variants. Defense-in-depth runbook at `docs/operational/rollback-runbook-pr-d.md`:

```markdown
# PR-D rollback runbook

## When PR-D1 (schema infrastructure) deploys

1. Verify shim is live: `grep -c '"_unknown_"' /opt/shift-agent/schemas.py` → 1.
2. Wait ≥24h before PR-D2 deploy. The 24h ensures the
   "tarball-of-record-pre-PR-D2" is the PR-D1 tarball, not the
   pre-PR-D1 tarball. Without that gap a PR-D2 rollback could
   restore a binary lacking the shim.

## When PR-D2 (behavior changes) deploys

PR-D2 is the FIRST tarball that EMITS catering_quote_sent_state_missing
and config_load_failed rows. If rollback fires:
1. Restore prior tarball (= PR-D1 tarball with shim).
2. Verify shim still imports cleanly post-rollback:
   `python3 /opt/shift-agent/scripts/check-safe-io-symbols && \
    python3 /opt/shift-agent/scripts/check-audit-helpers-symbols`
3. If both PASS, the binary handles any catering_quote_sent_state_missing
   or config_load_failed rows already in decisions.log via the shim.
4. If audit-helpers gate FAILS, manually move the new variants out of
   decisions.log to a triage file before restarting:
   `awk '!/^.*"type": "(catering_quote_sent_state_missing|config_load_failed)"/' \
        /opt/shift-agent/logs/decisions.log > /tmp/triaged.log && \
        mv /tmp/triaged.log /opt/shift-agent/logs/decisions.log`
   (Requires root; preserves audit chain modulo the new-type rows.)

## When BOTH PR-D1 and PR-D2 rollback (full revert to pre-PR-D)

The pre-PR-D tarball has neither shim nor variants. Any rows of the
new types in decisions.log will fail validation on read (e.g., during
report tooling). Use the same `awk` triage above before restoring the
pre-PR-D tarball, OR use the staged rollback: PR-D2 → soak → PR-D1.
```

---

## 8. New `catering-lead-reconcile` operator script (H4)

```bash
# Usage:
#   sudo -u shift-agent /opt/shift-agent/venv/bin/catering-lead-reconcile \
#     --lead-id LXXXX --target-status SENT_TO_CUSTOMER --reason "post-bridge divergence ticket #123"
# Effect:
#   1. Acquire LEADS_LOCK
#   2. Find lead by lead_id; refuse if not found (EXIT_NOT_FOUND)
#   3. Refuse if current status not in {OWNER_APPROVED, AWAITING_OWNER_APPROVAL}
#      (operator can only reconcile from a known-recoverable state)
#   4. Refuse if target_status not in {SENT_TO_CUSTOMER, OWNER_REJECTED}
#   5. Mutate status; atomic_write_json
#   6. Emit CateringLeadStatusChange(actor="operator", reason=<text>)
#   7. Emit NEW CateringLeadManualReconcile audit row capturing operator + reason
#   8. Release lock
# Exit codes:
#   0 — reconciled
#   2 — invalid args / forbidden transition
#   3 — lead not found
#   4 — leads.json corrupt (load failed)
```

New schema variant:

```python
class CateringLeadManualReconcile(_BaseEntry):
    """Emitted by catering-lead-reconcile script. Distinguishes operator
    intervention from automated state advance (which uses
    CateringLeadStatusChange with actor='system' or 'owner')."""
    type: Literal["catering_lead_manual_reconcile"]
    lead_id: str = Field(min_length=1)
    from_status: CateringLeadStatus
    to_status: CateringLeadStatus
    reason: str = Field(min_length=1, max_length=2000)
    operator_uid: int  # `os.getuid()` — capture who ran the script
```

`catering_lead_manual_reconcile` is added to `_KNOWN_LOG_ENTRY_TYPES` and the LogEntry union. Lives in PR-D1 (schema), enforced by PR-D2 (script + tests).

Test: `tests/test_catering_lead_reconcile.py` — 8 cases (forbidden transitions, missing lead, corrupt store, happy path, audit-row content, invalid status, idempotent rerun rejection, --dry-run flag).

---

## 9. Test strategy

### 9.1 Schema tests (PR-D1)

| Test file | Cases | LOC |
|---|---|---|
| `tests/test_log_entry_forward_compat.py` | 4 from §3.1 + 4 edge cases (empty `type`, type=null, type=non-string, ts-validator still applies) | ~80 |
| `tests/test_catering_quote_sent_state_missing.py` | 8: minimum-fields + maximum-fields + each invalid-field rejection | ~60 |
| `tests/test_config_load_failed.py` | 8: minimum + maximum + each invalid + ts UTC-only assertion | ~60 |
| `tests/test_catering_lead_manual_reconcile.py` | 6: round-trip + invalid statuses + operator_uid bounds | ~50 |

**M6 budget alignment:** ~26 schema tests total (down from plan's 110). Within the discriminated union test, all 4 commits' variants are exercised together.

### 9.2 Smoke gate extensions (H6)

Add to `shift-agent-smoke-test.sh` step 2:

```bash
python3 -c "
from schemas import LogEntry, _UnknownLogEntry
from pydantic import TypeAdapter
adapter = TypeAdapter(LogEntry)
fixtures = [
    {'type': 'catering_quote_sent_state_missing', 'ts': '2026-01-01T00:00:00Z',
     'lead_id': 'L00001', 'original_message_id': 'm1',
     'customer_phone_at_approve': '+15555550100',
     'outbound_message_id': 'mb1', 'detail': ''},
    {'type': 'config_load_failed', 'ts': '2026-01-01T00:00:00Z',
     'path': '/x', 'error_class': 'RuntimeError', 'error_detail': '',
     'script_name': 'foo'},
    {'type': '_unknown_', 'ts': '2026-01-01T00:00:00Z'},  # synthetic shim case
    {'type': 'future_unknown_xyz', 'ts': '2026-01-01T00:00:00Z'},  # routed to shim
]
for fx in fixtures:
    parsed = adapter.validate_python(fx)
    assert parsed is not None, f'fixture failed: {fx}'
print('SCHEMA_ROUND_TRIP_OK')
"
```

Step exits 1 if any fixture fails, triggering auto-rollback before traffic reaches the new binary.

### 9.3 Conftest hoist + v02 probe (M7 + M8 + M11)

**Renumbered ordering** (M8): Task 11 was hoist; now it's the probe. Task 12 is the hoist informed by probe.

**Probe (PR-D2 commit 5):** add `tests/test_v02_probe.py` containing one test that asserts `False` is reachable in the v02 helpers' import path:

```python
# tests/test_v02_probe.py
def test_v02_helpers_actually_run():
    """Probe: confirm v02 importlib helpers execute their bodies. Pre-conftest-
    hoist evidence per plan §M7. If this passes locally + in CI, the
    `_b1_helpers.py` claim that v02 helpers 'never actually executed' was
    overstated, and the conftest hoist preserves real behavior. If it fails,
    the hoist will surface real bugs that have been masked."""
    import importlib.util
    from pathlib import Path
    spec_path = Path("src/agents/catering/scripts/create-catering-lead")
    spec = importlib.util.spec_from_file_location("_probe_create_lead", spec_path)
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "__main__"
    # If the helpers never actually run their imports, this loader call would
    # complete silently with mod containing only the spec. We assert imports run:
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main"), "create-catering-lead module did not load main()"
```

Commit message captures the observation: pass = v02 tests do execute → hoist is safe; fail = surfaces real masked bug → fix in same commit.

**Hoist (PR-D2 commit 6) — M11 sibling-file approach:**

```
tests/_shared_catering_helpers.py  # NEW — sibling file, no conftest.py auto-load
tests/conftest.py                   # imports + re-exports as fixtures
tests/_b1_helpers.py                # backwards-compat re-export from _shared_catering_helpers
tests/test_catering_v02_scripts.py  # migrate ~108 callsites (M7 corrected count)
```

Why sibling: `conftest.py` auto-loads at collection time on every test platform; importing yaml + http.server in `_b1_helpers.py` at module level is OK in `_shared_catering_helpers.py` because nothing auto-imports it. Tests that need helpers do `from _shared_catering_helpers import BridgeStub, run_create, run_apply`.

The conftest.py change is minimal (~8 lines): just declares fixtures that wrap the sibling-module helpers. `_b1_helpers.py` becomes a 3-line re-export.

**M7 callsite count correction:** the plan said ~164; an `rg -c` re-run on `tests/test_catering_v02_scripts.py` yields 108 unique helper callsites. Commit message uses 108.

### 9.4 Test gaps from PR-A R3 (PR-D2 commit 7)

Six gaps listed in `tasks/todo.md` lines 67-73, condensed into one commit:
- (a) `assert_load_status_clean` empty-string + leading-whitespace status — `tests/test_safe_io_load_status.py` extension
- (b) `try_acquire_filelock_with_retry` negative attempts/sleep clamps — `tests/test_safe_io_filelock.py` extension
- (c) integration test for `corrupt:` status path through writer scripts — new file or extend existing
- (d) post-bridge re-load BUG path via BridgeStub side-effect — covered by `test_catering_apply_post_bridge_missing_lead.py` from §6.2 (no separate commit needed; remove from this list)
- (e) lock-parent-dir auto-creation — new test in `test_safe_io_filelock.py`
- (f) ast-based LOOKUP_STATUS_* enumeration replacing fragile regex — extends `test_lookup_skill_md.py`

5 new test cases (d removed as duplicate). ~70 LOC.

---

## 10. Build sequence (final)

### PR-D1 — schema infrastructure (5 commits, ~190 LOC)

| # | Commit subject | Touches |
|---|---|---|
| 1 | `feat(schemas): callable Discriminator + _UnknownLogEntry forward-compat shim` | `src/platform/schemas.py`, `tests/test_log_entry_forward_compat.py` (NEW) |
| 2 | `feat(schemas): CateringQuoteSentStateMissing variant + bridge_post_outcome on CateringQuoteAttempted` | `src/platform/schemas.py`, `tests/test_catering_quote_sent_state_missing.py` (NEW) |
| 3 | `feat(schemas): ConfigLoadFailed variant + CateringLeadManualReconcile variant` | `src/platform/schemas.py`, `tests/test_config_load_failed.py` (NEW), `tests/test_catering_lead_manual_reconcile.py` (NEW) |
| 4 | `feat(platform): audit_helpers.py — best-effort emission of config_load_failed + catering_quote_sent_state_missing` | `src/platform/audit_helpers.py` (NEW), `tests/test_audit_helpers.py` (NEW) |
| 5 | `feat(platform): check-audit-helpers-symbols pre-restart import gate` | `src/platform/scripts/check-audit-helpers-symbols` (NEW), `src/agents/shift/scripts/shift-agent-deploy.sh` (extend pre-restart block), `tests/test_audit_helpers_gate.py` (NEW) |

### PR-D2 — behavior changes (7 commits, ~430 LOC)

| # | Commit subject | Touches |
|---|---|---|
| 1 | `refactor(catering): migrate 5 inline yaml.safe_load callsites to load_yaml_model + emit config_load_failed on failure` | 5 catering scripts + `tests/test_catering_config_migration.py` (NEW) |
| 2 | `fix(catering): apply-decision post-bridge re-load — matched_idx idiom + emit catering_quote_sent_state_missing on missing lead` | `apply-catering-owner-decision`, `tests/test_catering_apply_post_bridge_missing_lead.py` (NEW) |
| 3 | `feat(catering): apply-decision write-anchor BEFORE bridge POST + outcome field on retry` | `apply-catering-owner-decision`, `tests/test_catering_apply_anchor_outcome.py` (NEW) |
| 4 | `feat(catering): apply-decision matcher widen — idempotent replay on anchor-with-success, retry on failed/unknown (closes v0.3 docstring-vs-reality gap)` | `apply-catering-owner-decision`, `tests/test_catering_apply_idempotent_replay.py` (NEW) |
| 5 | `test(catering): v02 probe — confirm helpers execute pre-conftest-hoist` | `tests/test_v02_probe.py` (NEW). Commit message captures probe outcome. |
| 6 | `refactor(tests): hoist BridgeStub + run_create + run_apply to tests/_shared_catering_helpers.py + conftest fixtures + _b1_helpers re-export` | `tests/_shared_catering_helpers.py` (NEW), `tests/conftest.py` (extend), `tests/_b1_helpers.py` (slim to re-export), `tests/test_catering_v02_scripts.py` (migrate 108 callsites) |
| 7 | `feat(catering): catering-lead-reconcile operator script for state-vs-outbound divergence + 5 PR-A R3 test gaps + docs/catering-edge-cases.md v3.2` | `src/agents/catering/scripts/catering-lead-reconcile` (NEW), `tests/test_catering_lead_reconcile.py` (NEW), `tests/test_safe_io_load_status.py` (extend), `tests/test_safe_io_filelock.py` (extend), `tests/test_lookup_skill_md.py` (extend), `docs/catering-edge-cases.md` (v3.2), `tests/test_catering_edge_cases_doc.py` (NEW) |

**Total final scope:** ~620 LOC diff across 12 commits, split 5+7 across two PRs.

**Defers to PR-B (per M12):**
- `lookup_invoked` LogEntry variant (was Task 3)
- SKILL preamble emission via `log-decision-direct 2>&1 | logger -t catering-skill-lookup-invoked || true` (was Task 9)
- `lookup-status-distribution-report` cron-style summary tool (already P3 backlog)

---

## 11. Deploy plan

### PR-D1

1. Merge to main, tag `pr-d1-pre-deploy-<sha>`.
2. Run `tools/build-shift-agent-tarball.sh` → tarball pinned in `tools/last-build.txt`.
3. `scp` tarball + `ssh ... shift-agent-deploy.sh ...`. Pre-restart import gate runs both `check-safe-io-symbols` AND new `check-audit-helpers-symbols`; failure rolls back automatically.
4. Soak ≥24h. Watch:
   - `journalctl -u hermes-gateway -f | grep -E 'AUDIT_HELPERS|safe_io|ImportError'`
   - `tail -f /opt/shift-agent/logs/decisions.log | grep -E '"_unknown_"'` — should produce ZERO output during soak (no emitters yet).
5. After 24h, the tarball-of-record-for-rollback is PR-D1; PR-D2 deploy is rollback-safe by construction.

### PR-D2

1. Merge to main (only after PR-D1 soak window closes), tag `pr-d2-pre-deploy-<sha>`.
2. Build + scp + deploy as above.
3. **20-min soak** (M9): apply-script live-state-load path changes; longer than the 5-min default to catch any cross-script lock-ordering edge cases.
4. Watch:
   - `tail -f /opt/shift-agent/logs/decisions.log | grep -E '"catering_quote_sent_state_missing"|"catering_quote_attempted"|"config_load_failed"'`
   - `journalctl -u hermes-gateway -f | grep -E 'BUG|EXIT_SCHEMA_VIOLATION|EXIT_DEPENDENCY_DOWN'`
   - Watch for any anchor row with `bridge_post_outcome=unknown` that doesn't get superseded within 30 seconds — indicates apply-script process death between anchor-write and post-bridge phase.
5. If smoke-test step 2 round-trip fails: auto-rollback restores PR-D1 tarball, which has the shim; new variants in decisions.log handled by `_UnknownLogEntry` shim.

---

## 12. Self-review checklist

- [x] Drift-check tag at top.
- [x] Read-deployed-code re-verification (§1) — confirmed plan's claims, corrected yaml callsite count.
- [x] B1 callable Discriminator design + acceptance test (§3.1).
- [x] B2 canonical write order (§6.3) + retry-state-advance contract (Cases A/B/C).
- [x] B3 split deploy (§10 PR-D1 → 24h → PR-D2) + operational runbook safety net (§7).
- [x] H1 PR-D1 + PR-D2 split (§2.1 + §10).
- [x] H2 sequencing pinned (PR-D1 + PR-D2 before PR-B).
- [x] H3 matched_idx idiom (§6.2).
- [x] H4 catering-lead-reconcile script + new variant (§8).
- [x] H5 deferred to PR-B with M12.
- [x] H6 smoke-gate variant round-trip (§9.2).
- [x] H7 check-audit-helpers-symbols pre-restart gate (§4.3).
- [x] H8 bridge_post_outcome field on CateringQuoteAttempted (§3.3).
- [x] M1-M12 each annotated in decisions log + addressed in body.
- [x] L1-L5 each addressed (L1 in §3.1 prose, L2 in tag, L3 in §10 PR-D2 commit 7, L4 in §3.4 + §4.2, L5 deferred to PR-B with M12).
- [x] No references to `extra="ignore"` on schemas (PR-A R1 correction held).
- [x] No references to `UNRECOVERABLE` lead status.
- [x] No new SaaS-style infrastructure (no Postgres/SQLite/queues).
- [x] All new audit variants emit through ndjson_append + `<path>.lock` flock (M10 corrected).

---

## 14. v2 revisions — 5-agent design-review fixes (BINDING)

This section supersedes any conflicting earlier text. Build phase reads from here first.

### 14.1 BLOCKERs (all resolved)

#### B-RB1 (R3) — explicit rollback target, not mtime-based

**Problem:** `shift-agent-deploy.sh` line 271-281 selects `PREV_TAG = ls -t deploys/deploy-*.tgz | head -1` (most recent mtime). 24h soak does NOT guarantee PR-D2 rollback restores PR-D1 — any intermediate deploy or `KEEP_TARBALLS=5` rotation breaks the chain.

**Fix:** PR-D1 commit 5 adds a NEW pre-deploy gate `tools/check-pr-d2-rollback-target.sh` invoked by the operator before `shift-agent-deploy.sh` for PR-D2:

```bash
#!/usr/bin/env bash
# Refuse PR-D2 deploy unless PREV_TAG (most-recent prior tarball) carries the
# PR-D1 SHA. Operator manually pins the expected SHA at deploy time.
set -euo pipefail
EXPECTED_PR_D1_SHA="${1:?usage: $0 <expected-pr-d1-sha>}"
ssh "$VPS" '
  PREV=$(ls -t /opt/shift-agent/deploys/deploy-*.tgz | head -1)
  PREV_SHA=$(basename "$PREV" .tgz | sed "s/^deploy-//")
  test "$PREV_SHA" = "'"$EXPECTED_PR_D1_SHA"'" || {
    echo "ABORT: PREV=$PREV_SHA, expected $EXPECTED_PR_D1_SHA" >&2
    exit 1
  }
  echo "PR-D2_ROLLBACK_TARGET_OK: $PREV"
' > .pr_d2_gate.txt 2>&1 || { cat .pr_d2_gate.txt; exit 1; }
```

PR-D2 deploy SOP: operator runs this gate first; if it exits 0, proceed with `shift-agent-deploy.sh`. The 24h soak now serves dual purpose (a) production validation of the shim, (b) operational window where no other deploys land that could displace PREV_TAG.

#### B-T1 (R4) — Case B end-to-end recovery test

**Problem:** §6.3 cases 1-5 inject anchor rows directly via fixtures, bypassing the actual write-then-die-then-retry sequence. B2's "no stuck loop" guarantee is unverified.

**Fix:** Add `tests/test_catering_apply_case_b_to_c_recovery.py` with two end-to-end tests:

```python
def test_process_dies_after_anchor_before_bridge(env_dir, bridge_server, monkeypatch):
    """Run 1 dies between step 6 (anchor write) and step 8 (bridge POST).
    Run 2 (retry) MUST: re-attempt bridge POST exactly once,
    advance lead to SENT_TO_CUSTOMER, emit second anchor row outcome=success,
    emit catering_quote_sent exactly once, no duplicate quote on customer side.
    """
    # Run 1: monkeypatch _bridge_post to raise SystemExit AFTER anchor written
    # but BEFORE HTTP call. Verify state: lead=OWNER_APPROVED + anchor outcome=unknown.
    # Run 2: real bridge. Assert BridgeStub.requests == 1 (one POST total in run 2),
    # tail-scan finds anchor outcome=success after both runs, single
    # catering_quote_sent row with original_message_id from RUN 1's args.

def test_process_dies_after_bridge_before_success_anchor(env_dir, bridge_server):
    """Run 1 dies between step 9 (re-acquired LEADS_LOCK) and step 14 (success
    anchor write). Quote was DELIVERED in run 1. Run 2 retry MUST detect this
    via tail-scan finding catering_quote_sent row for the lead, AND treat as
    idempotent_replay (NOT re-send). This is the R1+R2+R4 convergent fix —
    tail-scan checks BOTH catering_quote_attempted AND catering_quote_sent."""
```

**Implication for §6.3 retry logic:** the design's tail-scan must look at TWO row types:
1. `CateringQuoteAttempted` for `code` (anchor with bridge outcome).
2. `CateringQuoteSent` for `lead_id` (proof of customer delivery).

Decision tree (replaces §6.3 step 3-5):

```python
anchor = _tail_scan_anchor(LOG_PATH, code, max_lines=5000)
quote_sent = _tail_scan_quote_sent(LOG_PATH, lead_id, max_lines=5000)

if quote_sent is not None:
    # Customer demonstrably received the quote at some point. Idempotent no-op.
    # Advance lead to SENT_TO_CUSTOMER if still OWNER_APPROVED (covers
    # the run-1-died-between-step-13-and-step-14 case).
    if code_match[0].status == "OWNER_APPROVED":
        # write _recovered status advance
        ...
    return EXIT_OK with idempotent_replay=True
elif anchor is not None and anchor.bridge_post_outcome == "success":
    # Anchor says success but no quote_sent row — process died between
    # step 13 and step 14 OR step 14 and step 15. Treat as quote DELIVERED;
    # advance status; emit a CateringQuoteSent now (with synthesized
    # outbound_message_id="_recovered_<original>"). Idempotent.
    ...
elif anchor is not None and anchor.bridge_post_outcome in ("failed", "unknown"):
    # Bridge POST may not have succeeded. Re-attempt.
    # NOTE: this re-attempts only if quote_sent is missing, so legacy anchor
    # rows from old code (before bridge_post_outcome field existed, default="unknown")
    # don't trigger duplicate quotes — they coexist with their original CateringQuoteSent.
    ...
else:
    # No anchor, no quote_sent — fresh attempt. Existing matcher path.
    ...
```

This addresses **R1 MED-5 + R2 HIGH-2 + R4 B-T1** simultaneously. Add `_tail_scan_quote_sent` helper (mirror of `_tail_scan_anchor`).

#### B-1 (R5) — install-path drift

**Fix:** Update §8 + §7 + commit-message draft:
- `sudo -u shift-agent /opt/shift-agent/venv/bin/catering-lead-reconcile` → `sudo -u shift-agent /usr/local/bin/catering-lead-reconcile`
- `/opt/shift-agent/scripts/check-safe-io-symbols` → `/usr/local/bin/check-safe-io-symbols`
- `/opt/shift-agent/scripts/check-audit-helpers-symbols` → `/usr/local/bin/check-audit-helpers-symbols`
- Confirmed: `shift-agent-deploy.sh` line 38-39 globs `src/agents/*/scripts/*` and `src/platform/scripts/*` to `/usr/local/bin/` automatically. PR-D1 commit 5 + PR-D2 commit 7 just need to drop new scripts in those source dirs; no install-path edit needed.

### 14.2 HIGH (all resolved)

#### R1-HIGH-1 — empty `type: ""` capture-vs-reject pin

**Decision:** capture-and-preserve. Empty string routes to `_UnknownLogEntry` with `type=""`, round-trips intact. Comment in `_pick_log_entry_tag` explains intent. §3.1 acceptance test case grows to assert this behavior.

#### R1-HIGH-2 — pin Pydantic minimum version

**Fix:** add `pydantic>=2.10` to `requirements.txt` (the callable `Discriminator + Tag` pattern is stable from 2.10+; verified working on 2.12.5 by R1). Comment in `_pick_log_entry_tag` references the version it was validated against.

#### R2-HIGH-1 — `customer_phone_pre_bridge` capture site pin

**Fix in §6.2:** explicit insertion at `apply-catering-owner-decision:301-302` BEFORE the lock release at line 317:

```python
# Inside `with FileLock(LEADS_LOCK):` block, after `lead = matches[0]`:
target_jid = f"{lead.customer_phone.lstrip('+')}@s.whatsapp.net"
customer_phone_pre_bridge = lead.customer_phone  # captured for divergence audit
quote_text = _render_quote(lead, lead.customer_name or "")
```

#### R2-HIGH-2 + R1-MED-5 + R4-B-T1 convergent fix

Already resolved above in §14.1 B-T1 (tail-scan also checks `CateringQuoteSent`).

#### R3-H-Gate1 — explicit deploy.sh edit in commit 5

**Fix:** PR-D1 commit 5 description updated to include:

```bash
# shift-agent-deploy.sh line 289 (current):
ssh "$VPS" "/usr/local/bin/check-safe-io-symbols"

# After PR-D1 commit 5:
ssh "$VPS" "/usr/local/bin/check-safe-io-symbols && /usr/local/bin/check-audit-helpers-symbols"
```

Commit message: `feat(platform): check-audit-helpers-symbols pre-restart import gate + chain in deploy.sh`.

#### R3-H-Awk1 — replace awk with jq in §7 runbook

**Fix in §7:** replace fragile awk with:

```bash
# Pydantic v2 emits compact JSON; awk regex with optional whitespace fails
# silently. Use jq for robust filtering:
jq -c 'select(.type != "catering_quote_sent_state_missing"
            and .type != "config_load_failed"
            and .type != "catering_lead_manually_reconciled")' \
    /opt/shift-agent/logs/decisions.log > /tmp/triaged.log && \
mv /tmp/triaged.log /opt/shift-agent/logs/decisions.log
```

Add a test `tests/test_decisions_log_format.py` that asserts `TypeAdapter(LogEntry).dump_json(...)` produces compact JSON (no space after colon) — catches Pydantic-version drift.

#### R4-H-T1 — additional shim edge cases

**Fix in §3.1:** acceptance test grows from 4 cases to 8 cases:
1-4. (existing) known-variants, future_xyz, malformed-known, drift-introspection
5. **NEW** type-key-absent: `{"ts": "..."}` (no `type`) → routes to `_UnknownLogEntry`
6. **NEW** type=null: `{"type": null, "ts": "..."}` → ValidationError (None is not a string)
7. **NEW** type=non-string: `{"type": 42, "ts": "..."}` → ValidationError
8. **NEW** type="" (empty string): routes to `_UnknownLogEntry` with `type=""`
9. **NEW** type="_unknown_" literal: routes to `_UnknownLogEntry`, pass-through (intentional)

#### R4-H-T2 — pin rg callsite count

**Fix in §9.3 + commit 6 message:** include the exact rg command + frozen number:

```bash
# Run pre-merge:
rg -c '\b(BridgeStub|_run_create|_run_apply|_read_leads|_read_log|_read_audit_entries|_bridge_post_text|env_dir|bridge_server)\b' tests/test_catering_v02_scripts.py
# Expected output (frozen at PR-D2 commit 6 time): N=<actual>
```

The actual count is determined at build-phase time (was 108 in plan, R4 grep returned 66 — neither was authoritative). Build phase records the rg output verbatim in commit 6 message.

#### R5-H-1 — drift-tag escalated

**Fix:** drift tag at top of doc changed from `extends-Hermes` to `drifts-from-Hermes`. Compensating infrastructure documented:
- 24h PR-D1 soak (§11)
- explicit rollback target gate (§14.1 B-RB1)
- §7 operational runbook
- smoke-gate variant round-trip (§9.2)
- new pre-restart gate `check-audit-helpers-symbols` (§4.3)

#### R5-H-2 — naming convention violations

**Fix — renames:**
- `CateringQuoteSentStateMissing` → `CateringQuoteSentLeadMissing` (preserves "post-bridge re-load lost lead" semantics; matches `Catering<Subject><PastParticiple>` pattern)
- `CateringLeadManualReconcile` → `CateringLeadManuallyReconciled`

Update Literal `type` values:
- `catering_quote_sent_state_missing` → `catering_quote_sent_lead_missing`
- `catering_lead_manual_reconcile` → `catering_lead_manually_reconciled`

Update all references in §3.2, §4, §6.2, §7, §8, §9.1, §9.2, §10, §14.1.

### 14.3 MEDIUM (all resolved)

#### R1-MED-3 — derive `_KNOWN_LOG_ENTRY_TYPES` at module-import

**Decision: ADOPT.** Eliminates drift entirely with zero per-validation overhead. Replaces hand-maintained frozenset:

```python
def _build_known_log_entry_types() -> frozenset[str]:
    """Computed once at module-import via introspection of LogEntry union args.
    Excludes the `_unknown_` sentinel (the picker uses this set to decide
    routing; sentinel is the FALLBACK return, not a routable known type)."""
    from typing import get_args
    union_arg = get_args(LogEntry)[0]  # Union[Annotated[Model, Tag(...)], ...]
    tags: set[str] = set()
    for member in get_args(union_arg):  # each is Annotated[Model, Tag(...)]
        for meta in get_args(member):
            if isinstance(meta, Tag):
                tags.add(meta.tag)
    return frozenset(tags - {"_unknown_"})

# Computed AFTER LogEntry definition:
_KNOWN_LOG_ENTRY_TYPES = _build_known_log_entry_types()
```

CI test in §3.1 case 4 simplifies to assert this set is non-empty + contains the new variants.

#### R2-MED-1 — N=500 → N=5000 with timestamp bound

**Fix:** raise tail-scan default to 5000 lines AND add timestamp short-circuit (stop scanning past `now - 24h` even if 5000 not yet read). Helper signature:

```python
def _tail_scan_anchor(
    log_path: Path, code: str,
    max_lines: int = 5000,
    max_age_hours: float = 24.0,
) -> Optional[CateringQuoteAttempted]:
    """Scan back through decisions.log for the LATEST catering_quote_attempted
    row with matching code. Stops at first of: max_lines reached, file head
    reached, OR row ts older than max_age_hours.

    NDJSON read direction: forward-then-tail-of-matches (read all, filter,
    take last). Forward-read because Python doesn't support efficient
    reverse-line iteration without seek+block-buffer machinery; given
    max_lines bound the cost is bounded.
    """
```

`_tail_scan_quote_sent` mirrors this signature.

#### R2-MED-2 — tail-scan tolerates concurrent menu-script appends

**Fix:** docstring on `_tail_scan_anchor` notes: tail-scan tolerates concurrent appends from parse-menu-photo + apply-menu-update; only `catering_quote_attempted` filter matches matter, and apply-decision is the sole writer of those rows.

#### R3-M-Smoke1 — smoke gate iterates ALL variants

**Fix in §9.2:** smoke-gate snippet iterates `_KNOWN_LOG_ENTRY_TYPES` with a fixture map. Fixture map lives in the smoke test file (not a separate fixture; small enough to inline). Add ZeroValue fallback for Optional/list fields.

#### R3-M-Path1 — sys.path.insert in smoke gate

**Fix:** smoke-gate snippet starts with `sys.path.insert(0, "/opt/shift-agent")` matching `check-safe-io-symbols` line 34.

#### R4-M-T1 — schema test count expansion

**Fix in §9.1:** shim test 8 → 14 cases (5 new from R4-H-T1 + `model_extra` capture for nested unknown fields + ts-validator coercion through `_UnknownLogEntry` + isinstance discrimination test). `CateringQuoteAttempted` test extension: 4 cases (default-fill `bridge_post_outcome`, explicit success, explicit failed, explicit unknown round-trip). Total schema test count: ~36 (up from §9.1's 26, still well under plan's 110).

#### R4-M-T3 — callsite-grep regression test

**Fix:** add `tests/test_helper_migration_complete.py`:

```python
def test_v02_uses_shared_catering_helpers_only():
    text = Path("tests/test_catering_v02_scripts.py").read_text(encoding="utf-8")
    assert "from _b1_helpers" not in text, \
        "test_catering_v02_scripts.py must import from _shared_catering_helpers"
    assert "import _b1_helpers" not in text
```

Lives in PR-D2 commit 6 alongside the hoist.

#### R5-M-1 — `_UnknownLogEntry` parent-class isolation

**Fix in §3.1 acceptance test:** add introspection assertion:

```python
def test_unknown_log_entry_is_only_subclass_of_self():
    """No other LogEntry variant subclasses _UnknownLogEntry. extra='allow'
    must not propagate via inheritance."""
    from typing import get_args
    union_arg = get_args(LogEntry)[0]
    members = []
    for m in get_args(union_arg):
        members.append(get_args(m)[0])  # extract Model from Annotated[Model, Tag]
    leaks = [c for c in members if c is not _UnknownLogEntry and issubclass(c, _UnknownLogEntry)]
    assert leaks == [], f"{leaks} should not inherit from _UnknownLogEntry"
```

#### R5-M-2 — log_path override in tests

**Fix in §5 acceptance test description:** all helper-invocation tests use `log_path=tmp_path / "decisions.log"`. Documented in `tests/test_audit_helpers.py` fixture.

### 14.4 LOW (resolved or accepted)

#### R5-L-1 — trim §6.1 verbose recursion

**Fix:** §6.1 prose collapses to 3 sentences after the corrected reasoning lands:

> M10 finding rests on incorrect premise: LEADS_LOCK does NOT serialize parse-menu-photo / apply-menu-update writes to LOG_PATH (those scripts hold different state locks but write the same log). Keep inner LOG_PATH flock; rename `_log()` to `_append_log_with_outer_leadslock()` with docstring pinning lock-acquisition order (outer LEADS_LOCK → inner LOG_PATH lock; menu-side scripts hold no LEADS_LOCK so cannot deadlock here). No behavioral change; pure renaming + docstring discipline.

#### R5-L-2 — soak asymmetry prose

**Fix in §10:** add prose sentence: "PR-D1 carries 24h soak because it ships the convention-departing shim that PR-D2 + future PRs depend on for rollback safety; PR-D2 carries 20-min soak because behavior changes are tested upstream + apply-script live state-load path covered by §9 integration tests."

#### R1-LOW-6 / LOW-7

**Decision: ACCEPT no-op.** Cosmetic only.

### 14.5 Updated build sequence (PR-D1 + PR-D2 final)

#### PR-D1 — schema infrastructure (5 commits, ~210 LOC after v2 expansions)

| # | Commit subject |
|---|---|
| 1 | `feat(schemas): callable Discriminator + _UnknownLogEntry forward-compat shim + import-time _KNOWN_LOG_ENTRY_TYPES introspection` |
| 2 | `feat(schemas): CateringQuoteSentLeadMissing variant + bridge_post_outcome on CateringQuoteAttempted` |
| 3 | `feat(schemas): ConfigLoadFailed variant + CateringLeadManuallyReconciled variant` |
| 4 | `feat(platform): audit_helpers.py + log_*_best_effort emitters + helper unit tests` |
| 5 | `feat(platform): check-audit-helpers-symbols pre-restart gate + chain in deploy.sh + tools/check-pr-d2-rollback-target.sh operator gate` |

#### PR-D2 — behavior changes (7 commits, ~440 LOC after v2 expansions)

| # | Commit subject |
|---|---|
| 1 | `refactor(catering): migrate 5 inline yaml.safe_load callsites to load_yaml_model + emit config_load_failed on failure` |
| 2 | `fix(catering): apply-decision post-bridge re-load — matched_idx + emit catering_quote_sent_lead_missing on missing lead + customer_phone_pre_bridge capture` |
| 3 | `feat(catering): apply-decision write-anchor BEFORE bridge POST + outcome field two-step write + tail-scan helpers` |
| 4 | `feat(catering): apply-decision retry-state-machine — tail-scan checks both anchor AND quote_sent (closes v0.3 docstring-vs-reality gap)` |
| 5 | `test(catering): v02 probe — confirm helpers execute pre-conftest-hoist (rg-pinned callsite count in commit body)` |
| 6 | `refactor(tests): hoist BridgeStub + helpers to tests/_shared_catering_helpers.py + conftest fixtures + _b1_helpers re-export + callsite-grep regression test` |
| 7 | `feat(catering): catering-lead-reconcile operator script + 5 PR-A R3 test gaps + Case-B-to-C end-to-end recovery test + docs/catering-edge-cases.md v3.2 + decisions.log compact-JSON format test` |

### 14.6 Status: design v2 ready for build

All 3 BLOCKERs + 8 HIGH + 9 MEDIUM findings are addressed in §14 with concrete patches. No further design-review cycle needed before build per the pipeline definition. Build phase reads §14 first, then body for context. Renames (R5 H-2) are propagated mechanically through implementation.

---

## 13. Status (v1, superseded by §14)

**v1 design completed 5-agent review on 2026-04-29; v2 §14 above is binding.**

The v1 prompts to reviewers are preserved here for audit:
1. Pydantic correctness — answered by R1 (no BLOCKERs, design technically sound on Pydantic 2.12.5).
2. Lock-ordering correctness — answered by R2 (M10 self-correction validated; 2 HIGH retry-window risks identified, addressed in §14.1 B-T1 + §14.2).
3. Rollback safety — answered by R3 (BLOCKER B-RB1: mtime-based PREV_TAG; addressed in §14.1 with explicit operator gate).
4. Test strategy completeness — answered by R4 (BLOCKER B-T1: missing Case B end-to-end test; addressed in §14.1).
5. Drift from deployed conventions — answered by R5 (BLOCKER B-1: install-path drift; addressed in §14.1; also flagged drift-tag escalation H-1 + naming H-2, both applied at top + §14.2).
