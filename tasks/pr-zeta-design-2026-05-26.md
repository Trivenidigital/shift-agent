# PR-ζ Design — Concrete shapes for F1-F8

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:**
- `ActionExecutionContext` Pydantic model + `_RegulatedSendMissingActionContext` + `_RegulatedSendLintViolation` LogEntry variants
- `SAFE_IO_NULL_CONTEXT_ALLOWLIST` frozenset + `_resolve_caller_script_name()` helper
- `_emit_audit_row()` helper inside `safe_io.py`
- Static-gate test `tests/test_send_chokepoint_null_context_allowlist.py`

**Baseline:** `tasks/pr-zeta-plan-2026-05-26.md` (REV 2, post-reviewer iteration). This design doc adds concrete code for each F-section. The plan is the spec; the design is the implementation contract.

**Status:** DESIGN — REV 2 (post-design-reviewer iteration). Two reviewers landed (structural/cascade-ordering + static-gate coverage); changelog below.

## REV 2 changelog (2026-05-26)

| Finding | Reviewer | Severity | Change applied |
|---|---|---|---|
| `_emit_audit_row` skips required `FileLock`; `ndjson_append` docstring at safe_io.py:263-271 explicitly requires caller-held flock on `<path>.lock` | Structural | BLOCKER | F4 helper now wraps `ndjson_append` in `FileLock(Path(str(_DECISIONS_LOG_PATH) + ".lock"))`. Mirrors `audit_helpers._append_best_effort` lock shape but WITHOUT the lockless fallback — refusal audit-row write fail-closed propagates. |
| Static gate red on first run — 5 callsites in 3 files not in allowlist (`send-flyer-package:345,428`, `send-flyer-campaign:65,75`, `safe_io.py:681` from `bridge_post_2tuple` self-call) | Both | BLOCKER | F3 adds `send-flyer-package` + `send-flyer-campaign` to allowlist (regulated flyer media-delivery paths; threading context is PR-ζ.1 work). F6 special-cases `safe_io.py` as the canonical chokepoint module (self-calls always allowed). |
| `_emit_audit_row` lazy `from schemas import LogEntry` may fail on Hermes plugin load paths where sys.path isn't pre-set | Structural | MAJOR | safe_io.py now imports `LogEntry` + `ActionExecutionContext` eagerly at module top. Verified no circular import (schemas.py:2316 only references safe_io in comments). |
| `_RegulatedSendLintViolation.verb_hits` max_length=20 → a message with >20 forbidden verbs would raise `ValidationError` mid-refusal (fail-LOUD not fail-CLOSED) | Static-gate | MINOR (real bug) | F4 caps `verb_hits[:20]` before audit row construction. |
| `bridge_post_2tuple` missing from F6 TARGET_FUNCS — a new direct call would slip past the gate | Both | MINOR | F6 adds `bridge_post_2tuple` to TARGET_FUNCS. `_bridge_post` alias-detection remains out of scope (runtime audit-row catches alias escapees). |
| F6 `test_allowlist_files_exist` doesn't enforce one-file-per-basename — silent weakening if a refactor adds a second file with the same basename | Static-gate | MINOR | F6 test now asserts `len(matches) == 1` per allowlisted basename. |
| F7 missing tests: dict-passed-as-context, message_preview truncation, >20 verbs fail-closed shape | Static-gate | MINOR | F7 adds 3 new test cases. |
| TYPE_CHECKING block recommendation conflicts with cf-router's `from __future__ import annotations` already present | Structural | MINOR | F8 step 2 dropped TYPE_CHECKING; uses bare `Optional[ActionExecutionContext]` (forward-evaluated as string under `from __future__`). Lazy import inside the function body for runtime construction in hooks.py. |
| `actions.py:1752-1762` emits "...account is cancelled..." — `cancelled` is forbidden verb; today allowlisted, but PR-ζ.1 removal exposes the pre-existing message body | Static-gate | MAJOR (forward-looking) | Added "PR-ζ.1 prerequisites" section documenting this + the `_pending_plan_reply` URL slug check. |
| Defensive note: `is_regulated_action=False` callers can emit forbidden verbs unchallenged; protection rests entirely on correct tagging | Structural | MINOR | Added docstring note + `ActionExecutionContext` reviewer-call-out to track mis-tagging risk. |
| AST visitor blind spots (`getattr`, `globals()`, `fn = bridge_post`, walrus) — none in tree today | Static-gate | MAJOR (forward-looking) | F6 scope note expanded; positive-test fixture deferred to PR-η runtime telemetry. |
| `_pending_plan_reply` URL slug could contain forbidden verbs (e.g. `/payments/processed?...`) — runtime-state verification needed | Structural | NIT | Added to runtime impact analysis (verify deployed `payment_checkout_url_template` pre-deploy). |

---

## Hermes-first capability checklist

(Identical to plan REV 2 §Hermes-first capability checklist — no new steps introduced.)

| # | Step | Tag | Rationale |
|---|---|---|---|
| 1 | Agent decides to send | `[Hermes]` | Skill dispatch routing |
| 2 | Import `bridge_post` etc. | `[Hermes]` | Existing chokepoint |
| 3 | Construct `ActionExecutionContext` | `[net-new]` | No Hermes per-action context primitive |
| 4 | Call site uses `action_context=` kwarg | `[Hermes]` | Mechanical kwarg extension |
| 5 | Pre-flight URL + test-context checks | `[Hermes]` | Deployed |
| 6 | `inspect.stack()` resolver + allowlist | `[net-new]` | New caller-introspection logic |
| 7-9 | Audit-row writes via `ndjson_append` | `[Hermes]` | Substrate |
| 10 | urllib POST | `[Hermes]` | Existing body |
| 11 | Static-gate AST scan | `[net-new]` | New regression-defense primitive |
| 12 | `LogEntry` variants | `[Hermes]` | Pattern is deployed |

3/12 = 25% net-new. Below re-check threshold.

---

## Drift-rule self-checks

- ✅ Read `src/platform/schemas.py` (for `_BaseEntry`, `LogEntry` discriminated-union, `ConfigDict(extra="forbid", frozen=True)` patterns)
- ✅ Read `src/platform/safe_io.py:580-820` (the `bridge_post` family + `validate_bridge_url` + `bridge_send_blocked_by_test_context` + `ndjson_append`)
- ✅ Read `src/agents/flyer/customer_copy_policy.py:79-205` (PR-γ lint API)
- ✅ Read `src/agents/flyer/action_registry.py:30-149` (PR-δ `mutation_class` field; `change_plan` is the only `external_irreversible` action)
- ✅ Read `src/agents/flyer/account.py:230-311` (account command dispatch; CRITICAL FINDING — line 311 omits `detail=reason` on success path, so the F8 hooks.py detection requires a 1-token fix at this line)
- ✅ Read `src/agents/flyer/scripts/manage-flyer-account:71-81` (`_emit()` JSON output shape — `detail` is the field downstream consumers read)
- ✅ Read `src/plugins/cf-router/actions.py:4013-4032` (`send_flyer_text` shape + dedupe semantics; the function this design extends with `action_context` kwarg)
- ✅ Read `src/plugins/cf-router/hooks.py:1690-1748` (the account-command dispatch site — line 1738 is the migration target)
- ✅ Read `src/agents/flyer/manual_queue.py:580-660` (confirmed the `bridge_post as _default_bridge` rebind; allowlisted)
- ✅ Read `src/agents/shift/scripts/send-coverage-message:96-127` (confirmed local `def bridge_post` shadow; static-gate-only allowlist entry)

---

## F1 — `ActionExecutionContext` Pydantic model

**Location:** `src/platform/schemas.py` (added alongside existing models; before the `LogEntry` discriminated union).

```python
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class ActionExecutionContext(BaseModel):
    """Per-action runtime context propagated through the bridge_post chokepoint.

    Carries the action being executed + verification state so the chokepoint
    can apply forbidden-completion-verb lint (PR-γ) only when an action's
    result is unverified. Frozen + extra=forbid to defend against accidental
    mutation or unexpected field drift.

    PR-ζ 2026-05-26 — see tasks/pr-zeta-plan-2026-05-26.md F1.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(..., min_length=1, max_length=200)
    is_regulated_action: bool
    verified_action_result: bool
    audit_row_id: Optional[str] = Field(default=None, max_length=200)
    mutation_class: Optional[Literal["local_reversible", "external_irreversible"]] = None
```

**LogEntry variants** (added to existing discriminated union; mirror existing `_BaseEntry` subclasses):

```python
class _RegulatedSendMissingActionContext(_BaseEntry):
    type: Literal["regulated_send_missing_action_context"]
    caller_script: str = Field(..., max_length=200)
    jid: str = Field(..., max_length=200)
    message_preview: str = Field(..., max_length=120)  # truncated

class _RegulatedSendLintViolation(_BaseEntry):
    type: Literal["regulated_send_lint_violation"]
    action_id: str = Field(..., max_length=200)
    audit_row_id: Optional[str] = Field(default=None, max_length=200)
    jid: str = Field(..., max_length=200)
    verb_hits: list[str] = Field(..., max_length=20)
    message_preview: str = Field(..., max_length=120)
```

Both variants added to the `LogEntry` Annotated union in the right position (after similar low-cardinality system variants, before high-cardinality customer-conversation rows).

---

## F2 — Chokepoint signature extension

**Location:** `src/platform/safe_io.py:624, 701, 764`.

`bridge_post`:

```python
def bridge_post(
    jid: str,
    message: str,
    *,
    action_context: Optional["ActionExecutionContext"] = None,
) -> Tuple[bool, str, str, str]:
    """POST to local Hermes bridge. Returns (success, message_id, error_str, status).

    PR-ζ extension: `action_context` is keyword-only. When None, the caller
    must be in SAFE_IO_NULL_CONTEXT_ALLOWLIST or the send is refused. When
    a regulated context is passed, the message goes through the
    lint_no_unverified_completion check.
    """
    bad = validate_bridge_url(BRIDGE_URL)
    if bad:
        return False, "", bad, "connect_failed"
    blocked = bridge_send_blocked_by_test_context()
    if blocked:
        return False, "", blocked, "connect_failed"

    # PR-ζ chokepoint discipline. See F3 + F4.
    refusal = _enforce_action_context_policy(
        message_parts=[message],
        jid=jid,
        action_context=action_context,
    )
    if refusal is not None:
        return refusal  # (False, "", reason_str, "refused")

    # ... existing urllib POST body unchanged ...
```

`bridge_send_media` and `bridge_send_cta` follow the same shape; their `message_parts=[caption, file_name]` and `message_parts=[cta_text, ...]` aggregations preserve PR #250's `_lint_bridge_customer_copy(parts, action_context=...)` shape.

**`bridge_post_2tuple`** (UNCHANGED, line 662) — 2-param shape preserved; passes `action_context=None` to canonical implicitly via positional call. Catering/expense scripts hit the null-context allowlist branch and pass through.

**Forward-compatibility:** the `Optional["ActionExecutionContext"]` annotation uses string forward-reference to avoid a circular import at safe_io.py module load time (safe_io is imported BEFORE schemas in some scripts).

---

## F3 — Null-context allowlist + caller introspection

**Location:** `src/platform/safe_io.py`, after `BRIDGE_TIMEOUT_SEC` block.

```python
# PR-ζ 2026-05-26 — SAFE_IO_NULL_CONTEXT_ALLOWLIST.
#
# Scripts in this set may legitimately call bridge_post* with action_context=None.
# Every other caller must pass a non-None ActionExecutionContext, OR the send
# is refused with a regulated_send_missing_action_context audit row.
#
# Adding a new entry requires updating tests/test_send_chokepoint_null_context_allowlist.py
# (F6 static gate) and surfacing the rationale in the PR description.

SAFE_IO_NULL_CONTEXT_ALLOWLIST: frozenset[str] = frozenset({
    # System health / observability (not regulated business actions):
    "shift-agent-health-check.sh",
    "shift-agent-notify-owner",
    "shift-agent-tail-logger.py",
    "shift-agent-fsck.py",
    # Daily / EOD owner-only digests:
    "send-daily-brief",
    "eod-reconcile",
    "check-compliance-deadlines.py",
    # Flyer recovery watchdogs (system alerts):
    "flyer-recovery-watchdog",
    "flyer-source-edit-sla-watchdog",
    # Flyer media-delivery paths (regulated; threading context is PR-ζ.1 work,
    # not PR-ζ — these scripts already use bridge_send_media for final asset
    # delivery, and migrating them requires changes to upstream callers in
    # cf-router/actions.py that PR-ζ.1 will own). Allowlisted in PR-ζ.
    # (Found by REV-1 static-gate reviewer.)
    "send-flyer-package",
    "send-flyer-campaign",
    # Flyer closure customer-notify path (injected bridge_post via rebind):
    "manual_queue.py",
    # Catering / expense — adapter callers via bridge_post_2tuple.
    "send-catering-ack",
    "apply-catering-owner-decision",
    "create-catering-lead",
    "create-catering-proposal-options",
    "finalize-catering-menu",
    "select-catering-proposal",
    "apply-expense-decision",
    # STATIC-GATE-ONLY (local bridge_post shadow; never reaches chokepoint):
    "send-coverage-message",
    # cf-router non-change_plan callsites (DEFERRED to PR-ζ.1):
    "actions.py",
    "hooks.py",
})


def _resolve_caller_script_name() -> str:
    """Walk inspect.stack() to find the first user-code frame and return its
    basename. Skips safe_io.py self-frames and frozen importlib frames.

    REV 2: uses os.path.basename() == 'safe_io.py' (not endswith) for
    robustness against .pyc / namespace-package / frozen-module paths.
    """
    for frame_info in inspect.stack()[1:]:
        path = frame_info.filename
        if not path:
            continue
        if os.path.basename(path) == "safe_io.py":
            continue
        if "<frozen" in path or "importlib" in path:
            continue
        return os.path.basename(path)
    return ""
```

`import inspect` + `import os` added to top of file if not already present (both already imported).

---

## F4 — Lint hookup + dispatch

**Location:** `src/platform/safe_io.py`, new helpers near the bridge functions.

```python
# REV 2 — module-top eager imports (replaces lazy imports per structural
# reviewer #3). Verified no circular cycle: schemas.py:2316 only mentions
# safe_io in comments; ActionExecutionContext doesn't depend on safe_io.
from schemas import LogEntry, ActionExecutionContext  # type: ignore
from pydantic import TypeAdapter

_LOG_ENTRY_ADAPTER = TypeAdapter(LogEntry)
_DECISIONS_LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
_DECISIONS_LOG_LOCK = Path(str(_DECISIONS_LOG_PATH) + ".lock")


def _emit_audit_row(entry_type: str, fields: dict) -> None:
    """Build a LogEntry of the given discriminated-union type and append to
    the canonical audit chokepoint under the conventional flock.

    REV 2: wraps `ndjson_append` in `FileLock(_DECISIONS_LOG_LOCK)` per
    ndjson_append's documented contract (safe_io.py:263-271). Mirrors
    `audit_helpers._append_best_effort` lock shape but does NOT fall back
    lockless — the chokepoint's audit-fail-closed contract requires
    failure to propagate so the send isn't allowed to succeed silently.
    (Found by REV-1 structural reviewer.)

    Raises:
        pydantic.ValidationError — if `fields` don't satisfy the variant schema.
        OSError / RuntimeError — if FileLock or ndjson_append fails (disk
          full, permission, lock-acquire-failed).
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {"type": entry_type, "ts": ts, **fields}
    entry = _LOG_ENTRY_ADAPTER.validate_python(payload)
    with FileLock(_DECISIONS_LOG_LOCK):
        ndjson_append(_DECISIONS_LOG_PATH, entry.model_dump_json())


def _enforce_action_context_policy(
    *,
    message_parts: list[str],
    jid: str,
    action_context: Optional["ActionExecutionContext"],
) -> Optional[Tuple[bool, str, str, str]]:
    """Apply PR-ζ chokepoint discipline. Returns a refusal tuple, or None
    if the send is allowed.

    Allowlist match exempts from BOTH the missing-context refusal AND the lint
    (because lint requires a verified_action_result signal, which is bound to
    the context shape).
    """
    if action_context is None:
        caller = _resolve_caller_script_name()
        if caller not in SAFE_IO_NULL_CONTEXT_ALLOWLIST:
            _emit_audit_row(
                "regulated_send_missing_action_context",
                {
                    "caller_script": caller,
                    "jid": jid,
                    "message_preview": _join_parts_for_preview(message_parts)[:120],
                },
            )
            return False, "", "missing_action_context", "refused"
        return None  # allowlisted; pass through

    if not action_context.is_regulated_action:
        return None  # non-regulated send (system messages); pass through

    # Regulated send with explicit context. Apply lint.
    # Lazy import to avoid circular dep.
    from customer_copy_policy import lint_no_unverified_completion  # type: ignore

    aggregated = _join_parts_for_preview(message_parts)
    scan = lint_no_unverified_completion(
        aggregated,
        has_verified_action_result=action_context.verified_action_result,
    )
    if scan.hits:
        # REV 2: cap verb_hits[:20] before audit row construction. The
        # _RegulatedSendLintViolation schema bounds `verb_hits` at max_length=20
        # to keep the audit row size reasonable. A pathological message
        # tripping 21+ verbs would otherwise raise ValidationError mid-refusal,
        # converting fail-CLOSED into fail-LOUD (caller crashes instead of
        # cleanly refusing the send). (Found by REV-1 static-gate reviewer.)
        verb_values = [hit.value for hit in scan.hits][:20]
        _emit_audit_row(
            "regulated_send_lint_violation",
            {
                "action_id": action_context.action_id,
                "audit_row_id": action_context.audit_row_id,
                "jid": jid,
                "verb_hits": verb_values,
                "message_preview": aggregated[:120],
            },
        )
        return False, "", "lint_violation", "refused"

    return None  # passed


def _join_parts_for_preview(parts: list[str]) -> str:
    """Aggregate the parts that are subject to lint into a single string."""
    return "\n".join(str(p or "") for p in parts if p)
```

**Import path resolution:** `from customer_copy_policy import lint_no_unverified_completion` mirrors how `intent.py:18-21` handles the same cross-module import on flat-deployed VPS layout. The deployed VPS uses `sys.path.insert(0, "/opt/shift-agent")` so `customer_copy_policy` resolves directly. **Runtime check #2 from plan REV 2 still applies** — confirm `customer_copy_policy` is on the deployed sys.path on main-vps post-deploy.

---

## F5 — LogEntry variants

(Covered in F1 above. Pydantic discriminated-union via `Annotated[..., Field(discriminator="type")]` already wired in schemas.py; add the two variants to the existing `LogEntry` Annotated union.)

---

## F6 — Static gate

**Location:** new `tests/test_send_chokepoint_null_context_allowlist.py`.

```python
"""PR-ζ static gate: every direct bridge_post* callsite either passes
action_context= OR lives in a file whose basename is in SAFE_IO_NULL_CONTEXT_ALLOWLIST.

Scope (per plan REV 2 §F6): regression defense against NEW direct callsites
only. Cannot detect indirect calls (fn = bridge_post; fn(...)), getattr
dispatch, or wrapper functions like send_flyer_text that call bridge_post
internally.

Indirect-call escapees surface at runtime as regulated_send_missing_action_context
audit rows — see PR-η for the planned audit-log freshness watchdog that
groups these by caller_script.
"""
from __future__ import annotations

import ast
import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCAN_ROOTS = [REPO / "src", REPO / "tools"]
# REV 2: includes `bridge_post_2tuple` per design reviewers. A new direct
# call to the 2-tuple adapter still reaches the chokepoint with action_context
# implicitly None; the gate forces it onto the allowlist OR forces a real
# context. `_bridge_post` ALIAS DETECTION is out of scope (deferred to PR-η
# runtime telemetry which groups regulated_send_missing_action_context audit
# rows by caller_script).
TARGET_FUNCS = {"bridge_post", "bridge_post_2tuple", "bridge_send_media", "bridge_send_cta"}
# REV 2: safe_io.py is the canonical chokepoint module — its internal calls
# (e.g. bridge_post_2tuple → bridge_post at line 681) are not customer-facing
# bypasses and would otherwise trip the gate. Special-case here.
SCAN_SKIP_FILES = {"safe_io.py"}

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts or ".pyc" in p.suffix:
                continue
            files.append(p)
    return files


def _is_executable_source(p: Path) -> bool:
    if p.suffix == ".py":
        return True
    if p.suffix == "" and "scripts" in p.parts:
        return True
    return False


def _load_allowlist() -> frozenset[str]:
    sys.path.insert(0, str(REPO / "src" / "platform"))
    try:
        import safe_io
        return safe_io.SAFE_IO_NULL_CONTEXT_ALLOWLIST
    finally:
        sys.path.pop(0)


def _scan_direct_callsites(text: str, file_basename: str) -> list[tuple[int, str, bool]]:
    """Return list of (line_no, func_name, has_action_context_kwarg) for
    direct calls to bridge_post / bridge_send_media / bridge_send_cta."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    hits: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Resolve callable name: Name or Attribute, both supported.
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        else:
            continue
        if fname not in TARGET_FUNCS:
            continue
        has_ctx = any(kw.arg == "action_context" for kw in node.keywords)
        hits.append((node.lineno, fname, has_ctx))
    return hits


def test_every_direct_callsite_passes_context_or_is_allowlisted():
    allowlist = _load_allowlist()
    offenders: list[tuple[Path, int, str]] = []
    for f in _iter_source_files():
        if not _is_executable_source(f):
            continue
        if f.name in SCAN_SKIP_FILES:
            continue  # REV 2: skip the chokepoint module itself
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, fname, has_ctx in _scan_direct_callsites(text, f.name):
            if has_ctx:
                continue  # passes context explicitly
            if f.name in allowlist:
                continue  # caller-file basename allowlisted
            offenders.append((f.relative_to(REPO), line_no, fname))

    if offenders:
        lines = [f"  {p}:{ln} → {fn}() without action_context=" for p, ln, fn in offenders]
        pytest.fail(
            "PR-ζ static gate violated: direct bridge_post* callsite(s) outside "
            "SAFE_IO_NULL_CONTEXT_ALLOWLIST and without action_context= kwarg. "
            "Either pass an ActionExecutionContext or add the file basename to "
            "the allowlist with explicit per-file justification.\n"
            + "\n".join(lines)
        )


def test_allowlist_files_exist_and_are_unique():
    """REV 2: enforce exactly-one-file-per-allowlisted-basename. A future
    refactor introducing a second `hooks.py` (e.g. in another plugin dir)
    would silently double-cover the allowlist match. (Found by REV-1
    static-gate reviewer.)"""
    allowlist = _load_allowlist()
    all_files = [
        f for root in SCAN_ROOTS if root.exists()
        for f in root.rglob("*") if f.is_file()
    ]
    by_basename: dict[str, list[Path]] = {}
    for f in all_files:
        by_basename.setdefault(f.name, []).append(f)

    missing = [name for name in allowlist if name not in by_basename]
    assert not missing, f"SAFE_IO_NULL_CONTEXT_ALLOWLIST references nonexistent files: {missing}"

    duplicates = {name: paths for name, paths in by_basename.items() if name in allowlist and len(paths) > 1}
    assert not duplicates, (
        f"SAFE_IO_NULL_CONTEXT_ALLOWLIST basename collisions (ambiguous match): "
        + ", ".join(f"{name}={[str(p) for p in paths]}" for name, paths in duplicates.items())
    )


def test_canonical_helpers_in_safe_io():
    text = (REPO / "src" / "platform" / "safe_io.py").read_text(encoding="utf-8")
    for fn in TARGET_FUNCS:
        assert f"def {fn}" in text, f"safe_io.py must define {fn}"


def test_allowlist_is_frozenset():
    allowlist = _load_allowlist()
    assert isinstance(allowlist, frozenset), "SAFE_IO_NULL_CONTEXT_ALLOWLIST must be frozen"
```

---

## F7 — Behavior tests

**Location:** extend `tests/test_safe_io_bridge_post.py` + new `tests/test_action_execution_context_schema.py`.

### `tests/test_action_execution_context_schema.py` (new file)

```python
"""Schema invariants for ActionExecutionContext. PR-ζ 2026-05-26."""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="schemas import chain requires fcntl (Linux only)",
)


def test_minimal_construction():
    from schemas import ActionExecutionContext
    ctx = ActionExecutionContext(
        action_id="flyer.billing.request_plan_change",
        is_regulated_action=True,
        verified_action_result=False,
    )
    assert ctx.action_id == "flyer.billing.request_plan_change"
    assert ctx.audit_row_id is None
    assert ctx.mutation_class is None


def test_full_construction():
    from schemas import ActionExecutionContext
    ctx = ActionExecutionContext(
        action_id="flyer.billing.request_plan_change",
        is_regulated_action=True,
        verified_action_result=False,
        audit_row_id="evt_abc123",
        mutation_class="external_irreversible",
    )
    assert ctx.mutation_class == "external_irreversible"


def test_frozen():
    from schemas import ActionExecutionContext
    from pydantic import ValidationError
    ctx = ActionExecutionContext(
        action_id="x", is_regulated_action=False, verified_action_result=False,
    )
    with pytest.raises((ValidationError, TypeError)):
        ctx.action_id = "y"  # type: ignore


def test_extra_forbidden():
    from schemas import ActionExecutionContext
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionExecutionContext(
            action_id="x", is_regulated_action=False, verified_action_result=False,
            unknown_field=True,
        )


def test_missing_required_rejected():
    from schemas import ActionExecutionContext
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionExecutionContext(is_regulated_action=True, verified_action_result=False)


def test_action_id_min_length():
    from schemas import ActionExecutionContext
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionExecutionContext(action_id="", is_regulated_action=False, verified_action_result=False)


def test_mutation_class_literal_enforcement():
    from schemas import ActionExecutionContext
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionExecutionContext(
            action_id="x", is_regulated_action=False, verified_action_result=False,
            mutation_class="unknown_class",  # type: ignore
        )


def test_audit_row_id_max_length():
    from schemas import ActionExecutionContext
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ActionExecutionContext(
            action_id="x", is_regulated_action=False, verified_action_result=False,
            audit_row_id="x" * 201,
        )
```

### `tests/test_safe_io_bridge_post.py` (extended)

Add `TestActionContextEnforcement` class after the existing `TestBridgePost2TupleAdapter`:

```python
class TestActionContextEnforcement:
    """PR-ζ 2026-05-26 — chokepoint enforces action_context policy.

    Tests pair each chokepoint branch in _enforce_action_context_policy with
    a representative caller (mocked via inspect.stack frame injection) and
    assert the refusal vs pass-through behavior + the audit row written.
    """

    def _allowlist_frame(self, safe_io_module, monkeypatch, name: str) -> None:
        """Override _resolve_caller_script_name to return `name`."""
        monkeypatch.setattr(safe_io_module, "_resolve_caller_script_name", lambda: name)

    @patch("urllib.request.urlopen")
    def test_allowlisted_caller_with_none_context_proceeds(
        self, urlopen, safe_io_module, monkeypatch
    ):
        self._allowlist_frame(safe_io_module, monkeypatch, "send-daily-brief")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp

        ok, mid, err, status = safe_io_module.bridge_post("jid", "Daily brief: ...")
        assert ok is True
        assert mid == "wamid.OK"

    @patch("safe_io.ndjson_append")
    @patch("urllib.request.urlopen")
    def test_non_allowlisted_caller_with_none_context_refused(
        self, urlopen, ndjson_append, safe_io_module, monkeypatch
    ):
        self._allowlist_frame(safe_io_module, monkeypatch, "rogue-test-script.py")
        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg")
        assert ok is False
        assert status == "refused"
        assert err == "missing_action_context"
        urlopen.assert_not_called()
        # Audit row written
        assert ndjson_append.called
        appended_json = ndjson_append.call_args[0][1]
        assert "regulated_send_missing_action_context" in appended_json
        assert "rogue-test-script.py" in appended_json

    @patch("urllib.request.urlopen")
    def test_regulated_context_verified_with_forbidden_verb_proceeds(
        self, urlopen, safe_io_module
    ):
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=True,
            mutation_class="external_irreversible",
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid", "Your plan has been upgraded.", action_context=ctx,
        )
        assert ok is True

    @patch("safe_io.ndjson_append")
    @patch("urllib.request.urlopen")
    def test_regulated_context_unverified_with_forbidden_verb_refused(
        self, urlopen, ndjson_append, safe_io_module
    ):
        from schemas import ActionExecutionContext
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid", "Your plan has been upgraded.", action_context=ctx,
        )
        assert ok is False
        assert status == "refused"
        assert err == "lint_violation"
        urlopen.assert_not_called()
        appended_json = ndjson_append.call_args[0][1]
        assert "regulated_send_lint_violation" in appended_json
        assert "upgraded" in appended_json

    @patch("urllib.request.urlopen")
    def test_regulated_context_unverified_with_clean_message_proceeds(
        self, urlopen, safe_io_module
    ):
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid",
            "Please complete payment at https://example.com/checkout",
            action_context=ctx,
        )
        assert ok is True

    @patch("urllib.request.urlopen")
    def test_non_regulated_context_with_forbidden_verb_proceeds(
        self, urlopen, safe_io_module
    ):
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="system.healthcheck",
            is_regulated_action=False,
            verified_action_result=False,
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid", "System upgraded to v2.", action_context=ctx,
        )
        assert ok is True

    @patch("safe_io.ndjson_append")
    @patch("urllib.request.urlopen")
    def test_audit_write_failure_propagates_exception(
        self, urlopen, ndjson_append, safe_io_module, monkeypatch
    ):
        """When _emit_audit_row's ndjson_append raises, the exception must
        propagate — callers crash instead of silently swallowing. Plan §A1."""
        ndjson_append.side_effect = OSError("disk full")
        self._allowlist_frame(safe_io_module, monkeypatch, "rogue-test-script.py")
        with pytest.raises(OSError, match="disk full"):
            safe_io_module.bridge_post("jid", "msg")

    @patch("safe_io.ndjson_append")
    @patch("urllib.request.urlopen")
    def test_more_than_twenty_verb_hits_fails_closed_not_loud(
        self, urlopen, ndjson_append, safe_io_module
    ):
        """REV 2: a message tripping >20 forbidden verbs must refuse cleanly
        (not raise ValidationError mid-refusal). `_RegulatedSendLintViolation`
        caps verb_hits at max_length=20; F4 must truncate before construction.
        (Found by REV-1 static-gate reviewer.)"""
        from schemas import ActionExecutionContext
        ctx = ActionExecutionContext(
            action_id="x", is_regulated_action=True, verified_action_result=False,
        )
        # Build a message containing all 16 forbidden verbs duplicated; the
        # lint dedups per-verb so the actual hit count is bounded, but if the
        # frozenset ever grows past 20, this is the guard. Use a synthetic
        # 25-verb message to exercise the cap.
        msg = " ".join([
            "processed", "completed", "upgraded", "downgraded", "changed",
            "confirmed", "sent", "approved", "paid", "posted", "pushed",
            "applied", "scheduled", "booked", "cancelled", "refunded",
            # Add 9 duplicate trips via synthetic permutations
            "PROCESSED", "Completed", "Upgraded", "Sent", "Paid",
            "Approved", "Confirmed", "Booked", "Cancelled",
        ])
        ok, mid, err, status = safe_io_module.bridge_post("jid", msg, action_context=ctx)
        assert ok is False
        assert status == "refused"
        assert err == "lint_violation"
        # Audit row written cleanly even with >20 raw matches
        appended_json = ndjson_append.call_args[0][1]
        import json as _json
        entry = _json.loads(appended_json)
        assert len(entry["verb_hits"]) <= 20

    def test_dict_passed_as_context_propagates_attribute_error(
        self, safe_io_module
    ):
        """REV 2: passing a dict instead of ActionExecutionContext raises
        AttributeError → propagates → caller crashes. This is the intended
        fail-LOUD semantic for type-misuse (caller bug, not customer-data
        condition). Design Q4 — confirmed correct shape."""
        bad_ctx = {"is_regulated_action": True, "verified_action_result": False}  # wrong shape
        with pytest.raises(AttributeError):
            safe_io_module.bridge_post("jid", "msg", action_context=bad_ctx)

    @patch("safe_io.ndjson_append")
    @patch("urllib.request.urlopen")
    def test_message_preview_truncated_at_120_chars(
        self, urlopen, ndjson_append, safe_io_module, monkeypatch
    ):
        """REV 2: long messages must truncate cleanly at 120 chars for the
        message_preview field (max_length=120). (Found by REV-1 static-gate
        reviewer.)"""
        self._allowlist_frame(safe_io_module, monkeypatch, "rogue-test-script.py")
        long_msg = "x" * 500
        ok, _, _, _ = safe_io_module.bridge_post("jid", long_msg)
        assert ok is False
        appended_json = ndjson_append.call_args[0][1]
        import json as _json
        entry = _json.loads(appended_json)
        assert len(entry["message_preview"]) <= 120

    @patch("urllib.request.urlopen")
    def test_change_plan_reply_today_passes_lint(self, urlopen, safe_io_module):
        """The current _pending_plan_reply text contains no forbidden verbs."""
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        # Sample of the deployed _pending_plan_reply shape (account.py:741):
        reply = (
            "Flyer Studio\n------------\n"
            "Plan change pending. Please complete payment at:\n"
            "https://example.com/checkout?plan=growth"
        )
        ok, _, _, _ = safe_io_module.bridge_post("jid", reply, action_context=ctx)
        assert ok is True
```

---

## F8 — `change_plan` callsite migration

Three file touches, ordered:

### Step 1 — `src/agents/flyer/account.py:311` (1-token fix)

```python
# BEFORE:
return AccountResult(True, True, reply, updated.customer_id, updated.status)
# AFTER:
return AccountResult(True, True, reply, updated.customer_id, updated.status, detail=reason)
```

Without this, `detail=""` reaches manage-flyer-account's JSON output and hooks.py can't distinguish change_plan from other writes.

**Test:** `tests/test_flyer_account.py` (existing) — add an assertion that `handle_account_command` for command `change_plan` returns `AccountResult.detail == "plan_change_requested"`.

### Step 2 — `src/plugins/cf-router/actions.py:4013` — extend `send_flyer_text`

```python
def send_flyer_text(
    chat_id: str,
    message: str,
    *,
    action_context: "Optional[object]" = None,  # ActionExecutionContext when present
) -> tuple[bool, str, str]:
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    now = time.time()
    dedupe_key = _flyer_outbound_dedupe_key(chat_id, message)
    with _dedupe_file_lock(FLYER_OUTBOUND_DEDUPE_PATH):
        dedupe_entries = _load_flyer_outbound_dedupe(now)
        existing = dedupe_entries.get(dedupe_key)
        if existing:
            mid = str(existing.get("mid") or "recent")
            return True, f"deduped:{mid}", ""
        ok, mid, err, status = bridge_post(chat_id, message, action_context=action_context)
        if ok:
            dedupe_entries[dedupe_key] = {"ts": now, "mid": mid}
            _write_flyer_outbound_dedupe(dedupe_entries)
            return True, mid, ""
    return False, mid, f"{status}: {err}"
```

**REV 2 — type annotation shape (structural reviewer #7):** cf-router/actions.py:9 already has `from __future__ import annotations`, which converts ALL annotations to strings at runtime. So `Optional[ActionExecutionContext]` works as a forward-ref WITHOUT importing the class:

```python
def send_flyer_text(
    chat_id: str,
    message: str,
    *,
    action_context: Optional[ActionExecutionContext] = None,
) -> tuple[bool, str, str]:
```

No `TYPE_CHECKING` block, no import of `ActionExecutionContext` in actions.py — the annotation evaluates as the string `"Optional[ActionExecutionContext]"` and never reaches the type system at runtime. The actual `action_context` value passes through opaque to bridge_post which validates via duck-typing on `.is_regulated_action`/`.verified_action_result` access.

### Step 3 — `src/plugins/cf-router/hooks.py:1738` — construct context for change_plan

```python
# BEFORE (line 1738):
ack_ok, mid, err = actions.send_flyer_text(chat_id, result.get("reply_text") or "")

# AFTER:
detail = result.get("detail") or ""
action_ctx = None
if "plan_change_requested" in detail:
    # PR-ζ F8: this is the only external_irreversible action in the portfolio
    # (per PR-δ action_registry). Pass real context so the chokepoint lint
    # runs on the customer-facing reply. verified_action_result=False because
    # plan_change is a REQUEST (payment pending), not a completion.
    _ensure_platform_path()
    try:
        from schemas import ActionExecutionContext  # type: ignore
        action_ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
    except Exception:
        # Defensive: if schemas can't import, fall back to None (allowlisted
        # via actions.py basename). Logs the import failure for ops.
        actions.audit_intercepted(
            reason="flyer_account_action_context_import_failed",
            chat_id=chat_id,
            subprocess_rc=0,
            detail=f"detail={detail[:200]}",
        )
        action_ctx = None
ack_ok, mid, err = actions.send_flyer_text(
    chat_id, result.get("reply_text") or "", action_context=action_ctx,
)
```

The defensive try/except handles the (rare) case where cf-router's sys.path doesn't include `src/platform/` — Hermes plugin load environments vary. Falls back to None → allowlist match → lint skipped, but never hard-fails the dispatch.

**Test (new file or extension):** `tests/test_cf_router_change_plan_callsite.py` — fault-inject manage-flyer-account stdout with `detail="plan_change_requested"`; assert `send_flyer_text` is called with `action_context` of the right shape.

---

## PR-ζ.1 prerequisites (forward-looking — surfaced by design reviewers)

When PR-ζ.1 migrates the remaining cf-router callsites, these pre-existing message-body issues will surface as lint violations and MUST be addressed first:

1. **`src/plugins/cf-router/actions.py:1752-1762`** — emits *"This Flyer Studio account is cancelled. Contact Support..."* The verb `cancelled` is in `FORBIDDEN_COMPLETION_VERBS`. Today: allowlisted (`actions.py`) so lint skipped. After ζ.1 removal: would fire `regulated_send_lint_violation`. **Fix in ζ.1 design:** either rephrase the copy to avoid the verb (e.g. *"This account is no longer active"*) OR construct context with `verified_action_result=True` (the cancellation IS a verified terminal state). Verified-result is the principled choice — the cancellation is a real action that's been confirmed.

2. **`src/agents/flyer/account.py:_pending_plan_reply`** — emits the checkout URL embedded in customer copy. If the URL slug contains forbidden verbs (e.g. `/payments/processed?id=...`), lint would fire on the slug. **Runtime-state verification required in PR-ζ deploy:** SSH to main-vps, read deployed `payment_checkout_url_template` from `/opt/shift-agent/config.yaml`, confirm no slug substring matches the verb list. Today the deployed config uses `manual` provider with a template-less URL; flag if Stripe/Razorpay templates are added later.

3. **`actions.py:_send_ack` family at lines 3651, 3675, 3711, 3729, 3748** — emits acks like *"I'll send an update here shortly."* None contain forbidden verbs today (verified manual scan). Re-verify pre-PR-ζ.1.

These are NOT blockers for PR-ζ ship (allowlist match exempts from lint). They become blockers for PR-ζ.1.

## Open questions for design reviewer pass

1. **`ActionExecutionContext` forward-reference in `bridge_post` signature:** does `Optional["ActionExecutionContext"]` as a string forward-ref in safe_io.py work cleanly with Pydantic's TypeAdapter, or does it need a `TYPE_CHECKING` block import?

2. **`_emit_audit_row` lazy schema import:** `from schemas import LogEntry` at call-time avoids circular dependency at module load. Is this acceptable, or should the import happen once at module init and be cached on a module-level attribute (perf is irrelevant; the question is clarity)?

3. **Static-gate `Attribute` AST node coverage:** F6 detects `actions.send_flyer_text(...)` via `node.func.attr == "send_flyer_text"`. But `actions.send_flyer_text` is a WRAPPER — it calls bridge_post internally. The gate scans `node.func.attr` for target names `{"bridge_post", "bridge_send_media", "bridge_send_cta"}`, NOT `send_flyer_text`. Wrappers are deliberately invisible to the gate. Is this the right semantic, or should wrapper functions in cf-router that internally call bridge_post be enumerated separately?

4. **`isinstance(action_context, ActionExecutionContext)` runtime check:** the chokepoint doesn't currently validate that the passed `action_context` is actually an ActionExecutionContext instance — it just accesses `.is_regulated_action`. If a caller passes a dict, the attribute access raises AttributeError → propagates → caller crashes. Is this acceptable fail-closed shape, or should the chokepoint validate + raise a typed error?

5. **Lint scope on `bridge_send_media` and `bridge_send_cta`:** the design aggregates `caption + file_name` for media and `cta_text + ...` for CTA. But `file_name` for media is typically a URL or path, not customer copy. Should `file_name` be excluded from the lint aggregation? Otherwise lint may fire on completion-verb-containing URLs (unlikely but possible).

---

## Build sequence (concrete)

| Commit | Files | Approx LOC src | Approx LOC test |
|---|---|---|---|
| 1. schemas | `src/platform/schemas.py` | 40 | — |
| 2. safe_io signature ext | `src/platform/safe_io.py` (signature lines only) | 15 | — |
| 3. allowlist + resolver | `src/platform/safe_io.py` | 40 | 30 |
| 4. lint hookup + dispatch | `src/platform/safe_io.py` | 50 | 80 |
| 5. cf-router F8 | `src/agents/flyer/account.py:311` (1 token), `src/plugins/cf-router/actions.py:4013-4032` (~20), `src/plugins/cf-router/hooks.py:1738` (~20) | 40 | 40 |
| 6. static gate + schema tests | new `tests/test_send_chokepoint_null_context_allowlist.py`, new `tests/test_action_execution_context_schema.py`, extend `tests/test_safe_io_bridge_post.py` | — | 200 |
| **Total** | | **~185 src** | **~350 tests** | |

Each commit green-on-tests at HEAD. Commits 1-4 land foundation; commit 5 makes PR-ζ load-bearing; commit 6 defends against regression.

End of design.
