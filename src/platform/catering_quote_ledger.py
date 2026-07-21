"""PR-B — retained immutable catering quote-version ledger (sidecar repository).

Keeps an APPEND-ONLY, per-lead-versioned record of every committed catering
quote so the owner approves a specific VERSION (never blind) and any later
"show me where we are" reply renders from committed state (never reconstructed
from the conversation transcript). This module is DATA ONLY — load / version
assignment / append / atomic write / filesystem-contract validation / pure diff
/ render-from-committed. It performs no Hermes call, no lead mutation, no send.

Design (binding: PR-B task spec; mirrors src/platform/catering_amendments.py):
  * SIDECAR store /opt/shift-agent/state/catering-quote-ledger.json — own
    FileLock, additive; the CateringLead store is never changed by this module.
    Records stored as TOLERANT dicts so future fields round-trip untouched
    (SEMANTIC preservation); only the appended record is strict-validated (via
    schemas.CateringQuoteLedgerRecord).
  * The ledger lock is a LEAF: callers commit the lead under LEADS_LOCK, RELEASE
    it, then call append_version (which holds ONLY the ledger lock). Never held
    with the leads / code-pool / any other lock. No network/Hermes under the lock.
  * IMMUTABILITY by construction: the public surface is append_version(...) plus
    read paths (latest_committed / version / history / diff / render_latest_
    committed). There is NO update or delete API. `version` is assigned as
    max(existing versions for the lead) + 1; the scan-and-commit REFUSES to write
    a duplicate (lead_id, version) — an already-committed version can never be
    rewritten.
  * Atomic write via safe_io.atomic_write_json (temp + fsync + rename + parent
    fsync); post-replace filesystem re-verification. ANY failure PRESERVES the
    last valid store and returns not-ok. Append is BEST-EFFORT for callers: a
    ledger failure NEVER rolls back the caller's already-committed lead write —
    the caller logs loudly (best-effort-with-alarm) and proceeds.
  * Whole-dollar totals (int); diff total-delta is int arithmetic — NO float math
    (mirrors deposit.py / finalize-catering-menu cents/int discipline).

Deployed FLAT to /opt/shift-agent/ so scripts + the cf-router plugin can import it.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

QUOTE_TEXT_MAX = 16384                      # bounded stored quote prefix
DEFAULT_OWNER = "shift-agent"
DEFAULT_GROUP = "shift-agent"
# Test/host affordance for the POSIX fs-owner contract. Production leaves these
# UNSET → the ledger enforces shift-agent:shift-agent (the deployed state-dir
# owner). A subprocess integration test whose tmp state dir is owned by the CI
# runner (not shift-agent) sets these to the runner's user/group so _validate_fs
# passes — mirrors how catering_amendments' subprocess/MP tests pass an explicit
# expected_owner. An explicit expected_owner= argument still wins over the env.
_OWNER_ENV = "SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER"
_GROUP_ENV = "SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP"
_ACCEPTED_MODES = ("640", "660")
_DEFAULT_STATE_DIR = "/opt/shift-agent/state"
_DATA_NAME = "catering-quote-ledger.json"
_DEFAULT_DECISIONS_LOG = "/opt/shift-agent/logs/decisions.log"

_VALID_SOURCES = ("initial_draft", "owner_edit", "customer_finalize", "amendment_applied")


# ── Paths (env-overridable, resolved at CALL time for tests) ─────────────────
def _state_dir() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_STATE_DIR", _DEFAULT_STATE_DIR))


def _data_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_CATERING_QUOTE_LEDGER_PATH")
                or (_state_dir() / _DATA_NAME))


def _lock_path(data_path: Path) -> Path:
    return Path(str(data_path) + ".lock")


def _decisions_log_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH", _DEFAULT_DECISIONS_LOG))


# ── Results ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LedgerResult:
    ok: bool
    ledger_entry_id: Optional[str] = None
    version: Optional[int] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class QuoteDiff:
    """Deterministic diff of two committed versions. `from_version` is None when
    `to_version` is the FIRST version (no predecessor). Items are compared by name
    (set difference over selected_items names); `total_delta_usd` is int dollars."""
    to_version: int
    from_version: Optional[int] = None
    items_added: Tuple[str, ...] = field(default_factory=tuple)
    items_removed: Tuple[str, ...] = field(default_factory=tuple)
    quote_text_changed: bool = False
    total_delta_usd: int = 0

    def summary_line(self) -> str:
        """One-line owner-card diff, e.g. "+2 items, -1 item, total +$120".
        Returns "" for a first version (no predecessor to diff against)."""
        if self.from_version is None:
            return ""
        parts: list[str] = []
        n_add = len(self.items_added)
        n_rem = len(self.items_removed)
        if n_add:
            parts.append(f"+{n_add} item{'s' if n_add != 1 else ''}")
        if n_rem:
            parts.append(f"-{n_rem} item{'s' if n_rem != 1 else ''}")
        sign = "+" if self.total_delta_usd >= 0 else "-"
        parts.append(f"total {sign}${abs(self.total_delta_usd)}")
        return ", ".join(parts)


# ── POSIX owner/group helpers (best-effort; skipped where pwd/grp absent) ─────
def _posix_owner_group(st: os.stat_result) -> Optional[Tuple[str, str]]:
    try:
        import grp
        import pwd
    except ImportError:
        return None  # non-POSIX (Windows) — owner/group unverifiable, checked on Linux/box
    try:
        return pwd.getpwuid(st.st_uid).pw_name, grp.getgrgid(st.st_gid).gr_name
    except Exception:
        return None


def _validate_fs(path: Path, expected_owner: str, expected_group: str) -> Optional[str]:
    """Return None if the data path + parent satisfy the filesystem contract, else
    a reason code. Never mutates anything. Mirrors catering_amendments._validate_fs:
    cross-platform checks always run (parent is a real non-symlink dir; path is not
    a symlink and, if present, a regular file); POSIX-only checks are gated on
    os.name == "posix" (parent not world-writable; owner:group match; mode in
    {640,660})."""
    parent = path.parent
    if os.path.islink(parent):
        return "parent_symlink"
    if not parent.is_dir():
        return "parent_not_dir"
    if os.path.islink(path):
        return "path_symlink"
    path_present = path.exists()
    if path_present and not path.is_file():
        return "path_not_regular"

    if os.name != "posix":
        return None  # permission-bit + ownership checks are meaningless off POSIX

    try:
        st_parent = os.stat(parent)
    except OSError:
        return "parent_stat_error"
    if st_parent.st_mode & 0o002:
        return "parent_world_writable"
    og = _posix_owner_group(st_parent)
    if og is not None and (og[0] != expected_owner or og[1] != expected_group):
        return "parent_bad_owner"
    if path_present:
        try:
            st = os.stat(path)
        except OSError:
            return "path_stat_error"
        og2 = _posix_owner_group(st)
        if og2 is not None and (og2[0] != expected_owner or og2[1] != expected_group):
            return "path_bad_owner"
        if ("%o" % (st.st_mode & 0o777)) not in _ACCEPTED_MODES:
            return "path_bad_mode"
    return None


# ── Store load (tolerant + preservation-safe; NEVER quarantine-renames) ──────
def _fresh_store() -> dict:
    return {"schema_version": 1, "next_seq": 1, "records": []}


def _derive_next_seq(records: list) -> int:
    best = 0
    for r in records:
        if isinstance(r, dict):
            eid = r.get("ledger_entry_id")
            if isinstance(eid, str) and eid.startswith("Q") and eid[1:].isdigit():
                best = max(best, int(eid[1:]))
    return best + 1


def _load_store(path: Path) -> Tuple[dict, Optional[str]]:
    """Tolerant load. (store, None) on ok/missing/empty; ({}, reason) on unsafe.
    On corrupt / unexpected shape the on-disk file is PRESERVED (never renamed or
    rewritten — unlike safe_load_json's corrupt-quarantine)."""
    if not path.exists():
        return _fresh_store(), None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}, "load_oserror"
    if not raw.strip():
        return _fresh_store(), None
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}, "corrupt_json"
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        return {}, "unexpected_shape"
    if not isinstance(doc.get("schema_version"), int):
        doc["schema_version"] = 1
    if not isinstance(doc.get("next_seq"), int) or doc["next_seq"] < 1:
        doc["next_seq"] = _derive_next_seq(doc["records"])
    return doc, None


# ── Version helpers ───────────────────────────────────────────────────────────
def _records_for_lead(records: list, lead_id: str) -> list:
    out = [r for r in records
           if isinstance(r, dict) and r.get("lead_id") == lead_id
           and isinstance(r.get("version"), int)]
    out.sort(key=lambda r: r["version"])
    return out


def _next_version_for_lead(records: list, lead_id: str) -> int:
    """Monotonic per-lead version: max(existing versions) + 1, or 1 if none."""
    best = 0
    for r in _records_for_lead(records, lead_id):
        best = max(best, int(r["version"]))
    return best + 1


def _version_exists(records: list, lead_id: str, version: int) -> bool:
    return any(int(r["version"]) == version for r in _records_for_lead(records, lead_id))


def _item_names(record: Optional[dict]) -> set:
    """Selected-item names of a committed version dict ({} → empty set)."""
    if not record:
        return set()
    names: set = set()
    for it in record.get("selected_items") or []:
        if isinstance(it, dict):
            name = it.get("name")
        else:
            name = getattr(it, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _total_of(record: Optional[dict]) -> int:
    if not record:
        return 0
    v = record.get("quote_total_usd")
    return int(v) if isinstance(v, (int, float)) else 0


def _normalize_items(items: Any) -> list:
    """Coerce a selected_items input (list of dicts OR objects with
    name/qty/price_usd) into plain {name, qty, price_usd} dicts for validation."""
    out: list = []
    for it in items or []:
        if isinstance(it, dict):
            out.append({"name": it.get("name"), "qty": it.get("qty"),
                        "price_usd": it.get("price_usd")})
        else:
            out.append({"name": getattr(it, "name", None),
                        "qty": getattr(it, "qty", None),
                        "price_usd": getattr(it, "price_usd", None)})
    return out


# ── Deterministic diff (PURE — no file IO, no LLM) ────────────────────────────
def diff_versions(older: Optional[dict], newer: dict) -> QuoteDiff:
    """Pure deterministic diff of two committed version dicts. `older` is None for
    the first version. Items added/removed are compared by name; total delta is
    int dollars (newer - older). quote_text_changed is a literal string compare."""
    old_names = _item_names(older)
    new_names = _item_names(newer)
    return QuoteDiff(
        to_version=int(newer.get("version")),
        from_version=(int(older.get("version")) if older else None),
        items_added=tuple(sorted(new_names - old_names)),
        items_removed=tuple(sorted(old_names - new_names)),
        quote_text_changed=(
            (older.get("quote_text") if older else "") != (newer.get("quote_text") or "")
            if older else False
        ),
        total_delta_usd=_total_of(newer) - _total_of(older),
    )


# ── Atomic write ──────────────────────────────────────────────────────────────
def _atomic_write(path: Path, store: dict) -> None:
    from safe_io import atomic_write_json  # lazy
    atomic_write_json(path, store, mode=0o640)


# ── Audit (metadata only; raw quote text NEVER enters a general log) ─────────
def _emit_audit(entry_type: str, fields: dict) -> None:
    try:
        from safe_io import FileLock, ndjson_append  # lazy
        from schemas import LogEntry  # lazy
        from pydantic import TypeAdapter
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        entry = TypeAdapter(LogEntry).validate_python({"type": entry_type, "ts": ts, **fields})
        log_path = _decisions_log_path()
        with FileLock(Path(str(log_path) + ".lock")):
            ndjson_append(log_path, entry.model_dump_json())
    except Exception as e:  # noqa: BLE001 — audit is best-effort, never blocks the append
        sys.stderr.write(
            f"catering_quote_ledger: audit write failed for {entry_type}: "
            f"{type(e).__name__}: {str(e)[:200]}\n"
        )


def _emit_failed(lead_id: str, source: str, reason: str, emit: bool) -> None:
    if emit:
        _emit_audit("catering_quote_ledger_append_failed",
                    {"lead_id": lead_id, "source": source, "reason": reason})


def _emit_committed(lead_id: str, ledger_entry_id: str, version: int, source: str,
                    total: int, item_count: int, emit: bool) -> None:
    if emit:
        _emit_audit("catering_quote_version_committed", {
            "lead_id": lead_id, "ledger_entry_id": ledger_entry_id, "version": version,
            "source": source, "quote_total_usd": total, "item_count": item_count,
        })


# ── Public append entry point ─────────────────────────────────────────────────
def append_version(
    *,
    lead_id: str,
    quote_text: str,
    quote_total_usd: int,
    selected_items: Any = None,
    source: str,
    source_message_id: Optional[str] = None,
    approval_code: Optional[str] = None,
    created_at: Optional[datetime] = None,
    data_path=None,
    lock_path=None,
    expected_owner: Optional[str] = None,
    expected_group: Optional[str] = None,
    lock_attempts: int = 20,
    lock_sleep_sec: float = 0.5,
    emit_audit: bool = True,
) -> LedgerResult:
    """Append ONE committed quote version to the ledger. Returns LedgerResult with
    the assigned `version` (monotonic per lead) + `ledger_entry_id` on success. On
    ANY failure the store is PRESERVED, a metadata-only append_failed row is
    emitted, and ok=False — the CALLER's lead write is never rolled back (this is a
    best-effort sidecar; the caller logs loudly and proceeds).

    `version` is assigned internally as max(existing versions for lead_id) + 1; the
    scan-and-commit REFUSES a duplicate (lead_id, version) → reason="duplicate_
    version", file unchanged. `created_at` is an INJECTED tz-aware clock for test
    determinism; defaults to now(UTC)."""
    from safe_io import LockUnavailable, try_acquire_filelock_with_retry  # lazy

    created_at = created_at or datetime.now(timezone.utc)
    data_path = Path(data_path) if data_path else _data_path()
    lock_path = Path(lock_path) if lock_path else _lock_path(data_path)
    expected_owner = expected_owner or os.environ.get(_OWNER_ENV) or DEFAULT_OWNER
    expected_group = expected_group or os.environ.get(_GROUP_ENV) or DEFAULT_GROUP

    lead_id = lead_id or ""
    if not lead_id:
        _emit_failed("", source, "no_lead", emit_audit)
        return LedgerResult(ok=False, reason="no_lead")
    if source not in _VALID_SOURCES:
        _emit_failed(lead_id, str(source), "invalid_source", emit_audit)
        return LedgerResult(ok=False, reason="invalid_source")

    norm_items = _normalize_items(selected_items)

    try:
        with try_acquire_filelock_with_retry(lock_path, attempts=lock_attempts,
                                             sleep_sec=lock_sleep_sec):
            fs_reason = _validate_fs(data_path, expected_owner, expected_group)
            if fs_reason:
                _emit_failed(lead_id, source, "fs_" + fs_reason, emit_audit)
                return LedgerResult(ok=False, reason="fs_" + fs_reason)

            store, load_reason = _load_store(data_path)
            if load_reason:
                _emit_failed(lead_id, source, load_reason, emit_audit)
                return LedgerResult(ok=False, reason=load_reason)

            records = store.get("records", [])
            version = _next_version_for_lead(records, lead_id)
            # IMMUTABILITY guard: (lead_id, version) must not already exist. With
            # version = max+1 this is normally impossible; it fires only on a
            # corrupt store OR a forced-version test — an already-committed version
            # is NEVER rewritten.
            if _version_exists(records, lead_id, version):
                _emit_failed(lead_id, source, "duplicate_version", emit_audit)
                return LedgerResult(ok=False, reason="duplicate_version")

            seq = int(store.get("next_seq", 1))
            ledger_entry_id = "Q%04d" % seq
            try:
                from schemas import CateringQuoteLedgerRecord  # lazy
                record = CateringQuoteLedgerRecord(
                    ledger_entry_id=ledger_entry_id, lead_id=lead_id, version=version,
                    quote_text=(quote_text or "")[:QUOTE_TEXT_MAX],
                    quote_total_usd=quote_total_usd, selected_items=norm_items,
                    source=source, source_message_id=source_message_id,
                    approval_code=approval_code, created_at=created_at,
                )
            except Exception:
                _emit_failed(lead_id, source, "record_validation_failed", emit_audit)
                return LedgerResult(ok=False, reason="record_validation_failed")

            store["records"].append(record.model_dump(mode="json"))
            store["next_seq"] = seq + 1
            try:
                _atomic_write(data_path, store)
            except Exception:
                _emit_failed(lead_id, source, "write_failed", emit_audit)
                return LedgerResult(ok=False, reason="write_failed")

            post_reason = _validate_fs(data_path, expected_owner, expected_group)
            if post_reason:
                _emit_failed(lead_id, source, "postwrite_" + post_reason, emit_audit)
                return LedgerResult(ok=False, reason="postwrite_" + post_reason)

            _emit_committed(lead_id, ledger_entry_id, version, source,
                            int(quote_total_usd), len(norm_items), emit_audit)
            return LedgerResult(ok=True, ledger_entry_id=ledger_entry_id, version=version)
    except LockUnavailable:
        _emit_failed(lead_id, source, "lock_unavailable", emit_audit)
        return LedgerResult(ok=False, reason="lock_unavailable")
    except Exception as e:  # noqa: BLE001 — append must never raise into the caller
        sys.stderr.write(
            f"catering_quote_ledger: append exception: {type(e).__name__}: {str(e)[:200]}\n")
        _emit_failed(lead_id, source, "append_exception", emit_audit)
        return LedgerResult(ok=False, reason="append_exception")


# ── Read paths (lockless tolerant reads; NO mutation surface) ────────────────
def _load_records(data_path=None) -> list:
    store, _reason = _load_store(Path(data_path) if data_path else _data_path())
    return store.get("records", []) if isinstance(store, dict) else []


def history(lead_id: str, *, data_path=None) -> list:
    """All committed versions for a lead, ascending by version (tolerant dicts)."""
    return _records_for_lead(_load_records(data_path), lead_id)


def latest_committed(lead_id: str, *, data_path=None) -> Optional[dict]:
    """The highest-version committed record for a lead, or None."""
    h = history(lead_id, data_path=data_path)
    return h[-1] if h else None


def version(lead_id: str, n: int, *, data_path=None) -> Optional[dict]:
    """The committed record at version n for a lead, or None."""
    for r in history(lead_id, data_path=data_path):
        if int(r["version"]) == n:
            return r
    return None


def diff(lead_id: str, n1: Optional[int], n2: int, *, data_path=None) -> Optional[QuoteDiff]:
    """Diff committed versions n1 → n2 for a lead. n1=None diffs the first version
    against nothing (from_version stays None). Returns None if n2 is absent."""
    newer = version(lead_id, n2, data_path=data_path)
    if newer is None:
        return None
    older = version(lead_id, n1, data_path=data_path) if n1 is not None else None
    return diff_versions(older, newer)


def render_latest_committed(*, lead: Any, data_path=None) -> Optional[str]:
    """Render a human-readable status of the LATEST committed quote version for a
    lead. Reads ONLY the ledger (committed versions) + the passed `lead` (for its
    status + event details) — NEVER the conversation transcript. Returns None when
    the lead has no committed version yet.

    `lead` may be a CateringLead model OR a plain dict; only lead_id, status, and
    extracted event_date/headcount are read."""
    lead_id = _lead_attr(lead, "lead_id") or ""
    if not lead_id:
        return None
    rec = latest_committed(lead_id, data_path=data_path)
    if rec is None:
        return None
    status = _lead_attr(lead, "status") or "(unknown)"
    extracted = _lead_attr(lead, "extracted") or {}
    event_date = _lead_attr(extracted, "event_date") or "(TBD)"
    headcount = _lead_attr(extracted, "headcount")
    headcount_str = str(headcount) if headcount is not None else "(TBD)"
    lines = [
        f"Quote {lead_id} — version {rec.get('version')} ({status})",
        f"Event: {event_date} | Headcount: {headcount_str}",
    ]
    items = rec.get("selected_items") or []
    if items:
        lines.append("Items:")
        for it in items:
            name = it.get("name") if isinstance(it, dict) else getattr(it, "name", "?")
            qty = it.get("qty") if isinstance(it, dict) else getattr(it, "qty", 0)
            price = it.get("price_usd") if isinstance(it, dict) else getattr(it, "price_usd", 0)
            lines.append(f"  - {name} x{qty} @ ${price}")
    else:
        lines.append("Items: (none selected)")
    lines.append(f"Total: ${_total_of(rec)}")
    return "\n".join(lines)


def _lead_attr(obj: Any, name: str) -> Any:
    """Read an attribute from a model OR a key from a dict (None if absent)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
