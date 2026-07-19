"""Centralized #XXXXX approval-code pool contract (PR-R1, routing remediation).

A SMALL deterministic invariant kernel — NOT a routing framework. It is the ONE
source of truth for:

  - the four code pools and their canonical lookup order
    (menu-pending -> catering-leads -> expense -> shift),
  - each pool's eligibility filters — RESOLVE (routing, mirrors the deployed
    lookup helpers) and ENUMERATE (exclusion set); see the ADAPTER-DRIFT note,
  - cross-pool collision detection (fail-closed: a code matching >=2 pools is
    NEVER resolved to a first canonical match — it returns a CollisionResult),
  - the union of all live codes (the exclusion set every generator consults),
  - a single shared FileLock that makes generation check-and-commit atomic
    across every generator.

Why the generation lock exists (documented rationale). Code generation is NOT
provably single-writer: the Hermes gateway inbound paths and operator-invoked
scripts can overlap in time, so a naive "scan the pools, then write my code"
has a check-then-write race across pools (two writers scan, both miss the
other's in-flight code, both mint the same code). ``code_generation_lock`` /
``atomic_scan_and_commit`` hold ONE shared ``FileLock`` (``<state>/
approval-code-pools.lock``) across exclusion-scan -> generation -> the caller's
own store write, so the check and the commit are atomic. This is the smallest
existing primitive (``safe_io.FileLock``); no new coordination service.

LOCK ORDERING INVARIANT: the shared code-generation lock is acquired BEFORE any
per-pool store lock (global-code-lock BEFORE pool-store-lock), everywhere, so
converted callers cannot deadlock against each other.

Uniqueness is only GUARANTEED for a caller that performs its own store write
INSIDE ``code_generation_lock`` / ``atomic_scan_and_commit`` (i.e. the write is
covered by the shared lock). Callers that generate under the lock but write
outside it remain best-effort — do not claim guaranteed uniqueness for those.

Reads are raw ``json.loads`` (never ``load_model``), fail-open to empty on any
error: an unreadable sibling store must never quarantine-rename or raise here —
the owning script surfaces its own corruption loudly. This mirrors the inline
sibling-scan style the generators used before centralizing here.

Deployed FLAT to /opt/shift-agent/ (alongside safe_io.py / schemas.py /
flyer_identity.py) so the cf-router plugin and the flat agent scripts can
``import approval_code_pools`` directly.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Tuple, Union

# ── Pool identity + canonical order (the ONE source of order) ────────────────
POOL_MENU_PENDING = "menu-pending"
POOL_CATERING_LEADS = "catering-leads"
POOL_EXPENSE = "expense"
POOL_SHIFT = "shift"

#: Canonical pool lookup order — the dispatcher SKILL's documented contract
#: (dispatch_shift_agent/SKILL.md:189-192). The ONE source of order; F8 and the
#: SKILL both conform to this.
CODE_POOL_CANONICAL_ORDER: Tuple[str, ...] = (
    POOL_MENU_PENDING,
    POOL_CATERING_LEADS,
    POOL_EXPENSE,
    POOL_SHIFT,
)

# ── Eligibility filters ───────────────────────────────────────────────────────
# Two DISTINCT questions, two filters (see ADAPTER-DRIFT note below):
#   RESOLVE  ("is this code actionable RIGHT NOW"): mirrors the deployed
#            authoritative lookup helpers so F8/dispatcher routing is unchanged.
#            A parity test (test_routing_invariants_r1) asserts each duplicated
#            filter matches the deployed helper's behavior.
#   ENUMERATE ("is this code still in play — never re-mint it"): the broader
#            exclusion set, matching the four generators' prior inline scans.
#
# ADAPTER DRIFT: the authoritative helpers (cf-router actions.find_catering_lead
# _by_code / find_menu_pending_by_code) live in the cf-router PLUGIN, which the
# platform layer cannot import (layering; hyphenated dir name). So the RESOLVE
# filters are DUPLICATED here and each is pinned by a parity test that imports
# the deployed helper and asserts equivalence. Expense/shift have no importable
# by-code lookup helper; their filters are the SKILL's documented exclusions,
# pinned by characterization tests.
#
# catering RESOLVE  == actions.ACTIONABLE_LEAD_STATUSES (positive allowlist)
# catering ENUMERATE: exclude terminal/dead statuses (matches the generators'
#                     prior exclusion scan: {CLOSED,OWNER_REJECTED,STALE,NOT_CATERING})
# menu:     .confirmation_code == $c            (no status filter; both)
# expense:  exclude {PUSHED,REVERSED,REJECTED,EXPIRED} (SKILL; both)
# shift:    any proposal with a code            (no status filter; both)
_CATERING_ACTIONABLE = frozenset({
    "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED", "OWNER_EDITED", "OWNER_APPROVED",
})
_CATERING_LIVE_EXCLUDE = frozenset({"CLOSED", "OWNER_REJECTED", "STALE", "NOT_CATERING"})
_EXPENSE_TERMINAL = frozenset({"PUSHED", "REVERSED", "REJECTED", "EXPIRED"})

# ── Generation alphabet (matches every generator's _CODE_ALPHA + schema) ──────
_CODE_ALPHA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_GEN_LOCK_NAME = "approval-code-pools.lock"
_COLLISION_SENTINEL_NAME = "approval-code-collision-notified.json"

_DEFAULT_STATE_DIR = "/opt/shift-agent/state"
_DEFAULT_DECISIONS_LOG = "/opt/shift-agent/logs/decisions.log"


# ── Path resolution (env-overridable, resolved at CALL time for tests) ───────
def _state_dir() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_STATE_DIR", _DEFAULT_STATE_DIR))


def _menu_pending_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_MENU_PENDING_PATH")
                or (_state_dir() / "catering-menu-pending.json"))


def _catering_leads_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_CATERING_LEADS_PATH")
                or (_state_dir() / "catering-leads.json"))


def _expense_leads_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_EXPENSE_LEADS_PATH")
                or (_state_dir() / "expense-bookkeeper" / "leads.json"))


def _shift_pending_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_PENDING_PATH")
                or (_state_dir() / "pending.json"))


def _decisions_log_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH", _DEFAULT_DECISIONS_LOG))


def _code_gen_lock_path() -> Path:
    return _state_dir() / _CODE_GEN_LOCK_NAME


def _collision_sentinel_path() -> Path:
    return _state_dir() / _COLLISION_SENTINEL_NAME


def _load_json(path: Path) -> Any:
    """Raw parse, fail-open to None on any error (never quarantine, never raise)."""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Per-pool eligible-row generators (code, row) ─────────────────────────────
# `*_resolve_rows` back resolve_code (routing eligibility, mirrors the deployed
# helpers). `*_live_rows` back all_live_codes (exclusion set). They coincide for
# menu/expense/shift; catering deliberately differs (see filter note above).

def _menu_rows(doc: Any) -> Iterator[Tuple[str, dict]]:
    if isinstance(doc, dict):
        code = doc.get("confirmation_code")
        if code:
            yield code, doc


def _catering_resolve_rows(doc: Any) -> Iterator[Tuple[str, dict]]:
    """Routing-eligible catering leads — parity with actions.find_catering_lead
    _by_code (status IN ACTIONABLE_LEAD_STATUSES)."""
    if not isinstance(doc, dict):
        return
    for lead in doc.get("leads", []):
        if not isinstance(lead, dict):
            continue
        code = lead.get("owner_approval_code")
        if code and lead.get("status") in _CATERING_ACTIONABLE:
            yield code, lead


def _catering_live_rows(doc: Any) -> Iterator[Tuple[str, dict]]:
    """In-play catering codes for the exclusion set — the broader set the
    generators avoided (exclude {CLOSED,OWNER_REJECTED,STALE,NOT_CATERING})."""
    if not isinstance(doc, dict):
        return
    for lead in doc.get("leads", []):
        if not isinstance(lead, dict):
            continue
        code = lead.get("owner_approval_code")
        if code and lead.get("status") not in _CATERING_LIVE_EXCLUDE:
            yield code, lead


def _expense_rows(doc: Any) -> Iterator[Tuple[str, dict]]:
    if not isinstance(doc, dict):
        return
    for lead in doc.get("leads", []):
        if not isinstance(lead, dict):
            continue
        code = lead.get("owner_approval_code")
        if code and lead.get("status") not in _EXPENSE_TERMINAL:
            yield code, lead


def _shift_rows(doc: Any) -> Iterator[Tuple[str, dict]]:
    if not isinstance(doc, dict):
        return
    proposals = doc.get("proposals", {})
    # pending.json stores proposals as dict[str, Proposal]; iterate .values()
    # (iterating the mapping yields id keys -> collects nothing). No status
    # filter per the SKILL contract (`.proposals[] | select(.code == $c)`).
    rows = proposals.values() if isinstance(proposals, dict) else proposals
    if not isinstance(rows, (list, tuple)) and not hasattr(rows, "__iter__"):
        return
    for prop in rows:
        if isinstance(prop, dict) and prop.get("code"):
            yield prop["code"], prop


@dataclass(frozen=True)
class _Pool:
    name: str
    path_fn: Callable[[], Path]
    resolve_rows_fn: Callable[[Any], Iterator[Tuple[str, dict]]]
    live_rows_fn: Callable[[Any], Iterator[Tuple[str, dict]]]

    def _path(self, paths: Optional[dict]) -> Path:
        if paths and self.name in paths:
            return Path(paths[self.name])
        return self.path_fn()

    def lookup(self, code: str, paths: Optional[dict] = None) -> Optional[dict]:
        """First routing-eligible row whose code equals `code`, or None."""
        doc = _load_json(self._path(paths))
        for row_code, row in self.resolve_rows_fn(doc):
            if row_code == code:
                return row
        return None

    def live_codes(self, paths: Optional[dict] = None) -> set:
        doc = _load_json(self._path(paths))
        return {row_code for row_code, _ in self.live_rows_fn(doc)}


# Registry in canonical order.
_POOLS: Tuple[_Pool, ...] = (
    _Pool(POOL_MENU_PENDING, _menu_pending_path, _menu_rows, _menu_rows),
    _Pool(POOL_CATERING_LEADS, _catering_leads_path, _catering_resolve_rows, _catering_live_rows),
    _Pool(POOL_EXPENSE, _expense_leads_path, _expense_rows, _expense_rows),
    _Pool(POOL_SHIFT, _shift_pending_path, _shift_rows, _shift_rows),
)

# `paths` overrides (used by resolve_code / all_live_codes) are a partial
# mapping {pool_name: state-file Path}. Pools absent from the mapping fall back
# to env/default resolution. Lets a caller that already resolves its own
# configurable state paths (e.g. the cf-router plugin) thread them through so the
# registry reads the SAME files the caller is configured for.


def pool_paths_under(state_dir) -> dict:
    """Canonical per-pool state-file paths under `state_dir`. A caller that
    already knows its state dir (the cf-router plugin derives it from its
    configurable ``LEADS_PATH.parent``) passes ``resolve_code(code,
    paths=pool_paths_under(state_dir))`` so lookups read the caller's files —
    not the module's own env/default resolution."""
    state_dir = Path(state_dir)
    return {
        POOL_MENU_PENDING: state_dir / "catering-menu-pending.json",
        POOL_CATERING_LEADS: state_dir / "catering-leads.json",
        POOL_EXPENSE: state_dir / "expense-bookkeeper" / "leads.json",
        POOL_SHIFT: state_dir / "pending.json",
    }


@dataclass(frozen=True)
class CollisionResult:
    """A code matched >=2 pools at lookup time. Fail-closed sentinel — the
    caller MUST refuse to act (never guess a canonical match) and record the
    collision event. `pools` is canonical-ordered."""
    code: str
    pools: Tuple[str, ...]


# ── Public resolution + enumeration API ──────────────────────────────────────
def resolve_code(
    code: str, *, paths: Optional[dict] = None
) -> Union[Tuple[str, dict], CollisionResult, None]:
    """Resolve `code` against the four pools in canonical order.

    `paths` optionally overrides specific pools' state-file paths (see
    ``pool_paths_under``); pools absent from it use env/default resolution.

    Returns:
      (pool_name, row)   — exactly one pool matched.
      CollisionResult    — >=2 pools matched (NEVER first-match-wins on a
                           collision; the caller must fail closed).
      None               — no pool matched.
    """
    matched: list[Tuple[str, dict]] = []
    for pool in _POOLS:
        row = pool.lookup(code, paths)
        if row is not None:
            matched.append((pool.name, row))
    if not matched:
        return None
    if len(matched) >= 2:
        return CollisionResult(code=code, pools=tuple(name for name, _ in matched))
    return matched[0]


def all_live_codes(*, paths: Optional[dict] = None) -> set:
    """Union of live (non-terminal, code-bearing) codes across all four pools.
    The exclusion set every generator must avoid (invariant I2). `paths`
    optionally overrides specific pools' state-file paths (see
    ``pool_paths_under``)."""
    codes: set = set()
    for pool in _POOLS:
        codes |= pool.live_codes(paths)
    return codes


# ── Atomic scan-and-commit lock ──────────────────────────────────────────────
# NAMING: this is an "atomic scan-and-commit" primitive, NOT a "reservation" —
# it holds NO persistent reservation state. It serializes exclusion-scan ->
# candidate-generation -> the caller's own store write under ONE shared FileLock
# so those three steps are atomic across every generator.
#
# LOCK CONTRACT (all callers):
#   * ONE absolute, stable lock path (env-overridable, never CWD/worktree-
#     relative): SHIFT_AGENT_CODE_POOL_LOCK, else <state_dir>/approval-code-pools
#     .lock where state_dir is the caller's absolute store dir (/opt/shift-agent/
#     state in prod — every generator derives the same one).
#   * Bounded acquisition; timeout FAILS CLOSED (raises LockUnavailable BEFORE
#     the body runs, so NO code is issued).
#   * The final all-pool scan happens WHILE holding the lock; the durable pool-
#     store write happens BEFORE the lock is released (caller wraps both).
#   * Global-code-lock is acquired BEFORE any per-pool store lock, everywhere
#     (grep the four generators: every `code_generation_lock(...)` precedes the
#     `FileLock(<store>_LOCK)` on the SAME `with` line or an outer block — no
#     reverse-order acquisition exists).
#   * A pool-store write failure propagates (no code is returned/reported as
#     issued). Owner notification / outbound happens only OUTSIDE this lock
#     (collision notification is a separate call site).
_DEFAULT_LOCK_ATTEMPTS = 20
_DEFAULT_LOCK_SLEEP_SEC = 0.5


def _lock_attempts() -> int:
    return int(os.environ.get("SHIFT_AGENT_CODE_POOL_LOCK_ATTEMPTS", str(_DEFAULT_LOCK_ATTEMPTS)))


def _lock_sleep_sec() -> float:
    return float(os.environ.get("SHIFT_AGENT_CODE_POOL_LOCK_SLEEP_SEC", str(_DEFAULT_LOCK_SLEEP_SEC)))


def _default_candidate() -> str:
    return "#" + "".join(secrets.choice(_CODE_ALPHA) for _ in range(5))


def _resolve_lock_path(state_dir) -> Path:
    """Absolute, stable lock path. Precedence: explicit state_dir (co-locate with
    the caller's store) -> SHIFT_AGENT_CODE_POOL_LOCK -> <state_dir()>/lock."""
    if state_dir is not None:
        return Path(state_dir) / _CODE_GEN_LOCK_NAME
    override = os.environ.get("SHIFT_AGENT_CODE_POOL_LOCK")
    if override:
        return Path(override)
    return _state_dir() / _CODE_GEN_LOCK_NAME


@contextmanager
def code_generation_lock(*, state_dir=None, attempts: Optional[int] = None,
                         sleep_sec: Optional[float] = None) -> Iterator[None]:
    """Hold the ONE cross-pool atomic scan-and-commit FileLock (see LOCK CONTRACT
    above). Bounded acquisition — raises ``safe_io.LockUnavailable`` on timeout
    BEFORE yielding (fail-closed: the caller's generation body never runs, so no
    code is issued)."""
    from safe_io import try_acquire_filelock_with_retry  # lazy: fcntl-optional
    lock_path = _resolve_lock_path(state_dir)
    with try_acquire_filelock_with_retry(
        lock_path,
        attempts=attempts if attempts is not None else _lock_attempts(),
        sleep_sec=sleep_sec if sleep_sec is not None else _lock_sleep_sec(),
    ):
        yield


def generate_unique_code(
    *,
    exclude: Optional[set] = None,
    candidate_fn: Optional[Callable[[], str]] = None,
    max_attempts: int = 100,
    paths: Optional[dict] = None,
) -> str:
    """Return a #XXXXX code absent from every pool's live codes (+ `exclude`).

    Uniqueness against a concurrent writer only holds if the caller performs its
    own store write while STILL holding ``code_generation_lock`` (see
    ``atomic_scan_and_commit``)."""
    gen = candidate_fn or _default_candidate
    live = all_live_codes(paths=paths)
    if exclude:
        live = live | set(exclude)
    for _ in range(max_attempts):
        candidate = gen()
        if candidate not in live:
            return candidate
    raise RuntimeError(
        f"could not generate a cross-pool-unique code after {max_attempts} attempts"
    )


def atomic_scan_and_commit(
    own_pool_write_fn: Callable[[str], Any],
    *,
    candidate_fn: Optional[Callable[[], str]] = None,
    exclude: Optional[set] = None,
    max_attempts: int = 100,
    state_dir=None,
    paths: Optional[dict] = None,
) -> Tuple[str, Any]:
    """Atomically scan all pools, generate a cross-pool-unique code, AND commit
    the caller's own store write — all under ONE shared lock. No persistent
    reservation state is created.

    Holds ``code_generation_lock`` across exclusion-scan -> generation ->
    ``own_pool_write_fn(code)``. ``own_pool_write_fn`` performs the caller's own
    store write (under its own pool-store lock, nested INSIDE the shared lock —
    global-before-store ordering) and may return whatever the caller needs. If it
    raises, the exception propagates and NO code is returned (nothing is reported
    as issued). Idempotent w.r.t. retries: a re-invocation re-scans and re-writes.

    Returns ``(code, own_pool_write_fn_result)``. Raises ``safe_io.LockUnavailable``
    (fail-closed) if the lock cannot be acquired within the bounded window."""
    with code_generation_lock(state_dir=state_dir):
        code = generate_unique_code(
            exclude=exclude, candidate_fn=candidate_fn,
            max_attempts=max_attempts, paths=paths,
        )
        result = own_pool_write_fn(code)
        return code, result


# ── Collision event: audit (every time) + notify owner (once per code) ───────
def _emit_collision_audit(collision: CollisionResult, detected_by: str) -> None:
    """Append an approval_code_collision_detected row via the safe_io/ndjson
    audit chokepoint pattern. PRIVACY: code + pool names only."""
    from safe_io import FileLock, ndjson_append  # lazy
    from schemas import ApprovalCodeCollisionDetected  # lazy
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entry = ApprovalCodeCollisionDetected(
        ts=ts,
        type="approval_code_collision_detected",
        code=collision.code,
        pools=list(collision.pools),
        detected_by=detected_by,
    )
    log_path = _decisions_log_path()
    with FileLock(Path(str(log_path) + ".lock")):
        ndjson_append(log_path, entry.model_dump_json())


def _sentinel_key(collision: CollisionResult) -> str:
    """Dedup key = code + the SORTED pool set. A CHANGED collision state (the
    same code now colliding across a DIFFERENT set of pools) yields a different
    key -> a fresh owner alert. Order-independent so ('menu','catering') and
    ('catering','menu') dedup identically."""
    return collision.code + "|" + ",".join(sorted(collision.pools))


def _read_notified(sentinel: Path) -> set:
    try:
        doc = json.loads(sentinel.read_text(encoding="utf-8"))
        keys = doc.get("keys", []) if isinstance(doc, dict) else []
        return {k for k in keys if isinstance(k, str)}
    except Exception:
        return set()


def record_collision_event(collision: CollisionResult, *, detected_by: str) -> None:
    """Handle a detected cross-pool collision: emit an audit row EVERY call and
    notify the owner ONCE per (code + pool-set) sentinel key (state-dir file,
    FileLock-guarded).

    The sentinel dedup key is ``code|sorted(pools)`` (see ``_sentinel_key``): a
    repeat of the SAME collision does not re-page; a CHANGED collision (same code,
    different colliding pool set) DOES page again. No expiry — the state persists
    until an operator resolves it (a colliding code is a standing invariant
    breach, not a transient event).

    Owner notification goes through the EXISTING notify-owner chokepoint
    (``safe_io.notify_owner_with_fallback`` -> ``shift-agent-notify-owner``) —
    no direct bridge/send bypass, and it happens OUTSIDE any generation lock.
    PRIVACY: the audit row and the owner alert carry ONLY the code and the
    matching pool names — never customer phone, message text, or chat ids.
    Best-effort throughout: a failure to audit OR notify writes to stderr but
    never raises AND never changes the caller's routing outcome (the caller has
    already failed closed; a dropped alert leaves routing failed-closed)."""
    try:
        _emit_collision_audit(collision, detected_by)
    except Exception as e:  # noqa: BLE001 — audit is best-effort
        sys.stderr.write(
            f"approval_code_pools: collision audit write failed: "
            f"{type(e).__name__}: {str(e)[:200]}\n"
        )

    try:
        from safe_io import FileLock, notify_owner_with_fallback  # lazy
        sentinel = _collision_sentinel_path()
        key = _sentinel_key(collision)
        with FileLock(Path(str(sentinel) + ".lock")):
            notified = _read_notified(sentinel)
            if key in notified:
                return  # already paged for this exact collision state — don't re-notify
            pools = ", ".join(collision.pools)
            title = "Approval-code collision"
            message = (
                f"Code {collision.code} matches multiple code pools ({pools}). "
                f"Refusing to act automatically; please resolve manually."
            )
            notify_owner_with_fallback(
                title, message, priority=1, source="approval_code_pools",
            )
            notified.add(key)
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(
                json.dumps({"keys": sorted(notified)}), encoding="utf-8"
            )
    except Exception as e:  # noqa: BLE001 — notify is best-effort
        sys.stderr.write(
            f"approval_code_pools: collision owner-notify failed: "
            f"{type(e).__name__}: {str(e)[:200]}\n"
        )
