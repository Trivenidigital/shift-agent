"""PR-R2A — immutable catering-amendment capture (sidecar repository).

Closes the Branch-B suppression data-loss (hooks.py F7 follow-up-suppressed arm):
an amendment text a customer sends against an active non-terminal lead is durably
captured to a SIDECAR store BEFORE the canonical reply, so the correction is never
silently dropped. This module is DATA ONLY — load / idempotency / append / atomic
write / filesystem-contract validation. It performs no Hermes call, no lead
mutation, no approval code, no send (the send is the caller's, in hooks.py).

Design (binding: tasks/audits/pr-r2-amendment-preflight-2026-07-19.md §R2A DELTA):
  * SIDECAR store /opt/shift-agent/state/catering-amendments.json — own FileLock,
    keyed by lead_id; rollback-clean (old code never opens it; zero CateringLead
    change). Records stored as TOLERANT dicts so future R2B fields round-trip
    untouched (SEMANTIC preservation); only the appended record is strict-validated.
  * The sidecar lock is a LEAF: the lead is read locklessly upstream; this module
    holds ONLY the sidecar lock for re-check + append + write. Never held with the
    leads / code-pool / any other lock. No network/Hermes/notify under the lock.
  * Three-tier idempotency (ALL under the one lock): (1) primary
    (source_transport, lead_id, native_message_id) any status; (2) envelope
    fingerprint any status; (3) (lead_id, sender_ref, raw_text_sha256) within a 24h
    window across ALL statuses. Outside the window identical text is a new record.
  * Atomic write via safe_io.atomic_write_json (temp + fsync + rename + parent
    fsync); post-replace filesystem re-verification. ANY failure PRESERVES the last
    valid store and returns not-ok (the caller sends a deterministic retry reply).
  * R2A writer identity is shift-agent ONLY (the gateway). No root writer exists;
    atomic replacement re-establishes shift-agent:shift-agent 0640.

Deployed FLAT to /opt/shift-agent/ so the cf-router plugin can import it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

RAW_TEXT_MAX = 16384                       # bounded stored prefix
REPLAY_WINDOW = timedelta(hours=24)        # tier-3 fallback dedup window (documented constant)
DEFAULT_OWNER = "shift-agent"
DEFAULT_GROUP = "shift-agent"
_ACCEPTED_MODES = ("640", "660")
_DEFAULT_STATE_DIR = "/opt/shift-agent/state"
_DATA_NAME = "catering-amendments.json"
_DEFAULT_DECISIONS_LOG = "/opt/shift-agent/logs/decisions.log"


# ── Paths (env-overridable, resolved at CALL time for tests) ─────────────────
def _state_dir() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_STATE_DIR", _DEFAULT_STATE_DIR))


def _data_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_CATERING_AMENDMENTS_PATH")
                or (_state_dir() / _DATA_NAME))


def _lock_path(data_path: Path) -> Path:
    return Path(str(data_path) + ".lock")


def _decisions_log_path() -> Path:
    return Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH", _DEFAULT_DECISIONS_LOG))


# ── Result ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    amendment_id: Optional[str] = None
    reason: Optional[str] = None
    idempotent: bool = False


# ── Hashes / identity / fingerprint ──────────────────────────────────────────
def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _canonical_json_sha256(obj: Any) -> str:
    """Deterministic sha256 of a JSON-able object (sorted keys, compact)."""
    try:
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          default=str)
    except Exception:
        blob = "null"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sender_ref(chat_id: Optional[str], phone: Optional[str]) -> str:
    """Canonical identity key when resolvable (lid-cache convergence via the flat
    platform helper), else the raw phone/chat_id exactly as the router resolved it.
    Never raises (fail-open to the raw ref)."""
    try:
        from flyer_identity import canonical_identity_key  # type: ignore
        key = canonical_identity_key(chat_id or "", phone)
        if key:
            return key
    except Exception:
        pass
    return phone or chat_id or ""


def _envelope_fingerprint(sender_ref: str, provider_timestamp: Optional[str],
                          body_len: int, complete_sha: str) -> str:
    """Canonical transport-envelope fingerprint: sha256(stable sender id ∥ provider
    timestamp ∥ body length ∥ complete-text sha256). "" if UNDERIVABLE — i.e. when
    no provider timestamp is available (without it there is no distinguishing
    envelope element beyond the text, which tier 3 already covers)."""
    ts = (provider_timestamp or "").strip()
    if not ts:
        return ""
    material = f"{sender_ref}\x00{ts}\x00{body_len}\x00{complete_sha}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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
    """Return None if the data path + parent satisfy the §4b filesystem contract,
    else a reason code. Never mutates anything.

    Cross-platform checks (always run): parent is a real directory and not a
    symlink; path is not a symlink and, if present, is a regular file.

    POSIX-only checks (gated on os.name == "posix"; skipped on the Windows dev box
    where st_mode/uid/gid do NOT map to POSIX semantics — enforced on the box +
    Linux CI): parent not world-writable; parent + path owned expected owner:group;
    path mode in {640,660}."""
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
            aid = r.get("amendment_id")
            if isinstance(aid, str) and aid.startswith("A") and aid[1:].isdigit():
                best = max(best, int(aid[1:]))
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


def _find_duplicate(store: dict, source_transport: str, lead_id: str, native_id: str,
                    fingerprint: str, sender_ref: str, complete_sha: str,
                    now: datetime) -> Optional[str]:
    """Three-tier idempotency (all in-lock). Returns an existing amendment_id or None."""
    records = store.get("records", [])
    # Tier 1 — primary (source_transport, lead_id, native_message_id), any status.
    if native_id:
        for r in records:
            if (isinstance(r, dict) and r.get("source_transport") == source_transport
                    and r.get("lead_id") == lead_id and r.get("message_id") == native_id):
                return r.get("amendment_id")
    # Tier 2 — envelope fingerprint, any status.
    if fingerprint:
        for r in records:
            if isinstance(r, dict) and r.get("envelope_fingerprint") == fingerprint:
                return r.get("amendment_id")
    # Tier 3 — (lead_id, sender_ref, raw_text_sha256) within a 24h window, all statuses.
    # Boundary is EXPLICIT and half-open: elapsed = now - captured_at is INSIDE the
    # window iff elapsed < 24h (i.e. captured_at > now-24h). Exactly 24h elapsed is
    # OUTSIDE the window → a NEW record (identical text a full day later is a genuine
    # re-request, not a replay). Pinned by boundary tests at 24h-1s (dedup) and 24h
    # exactly (new record).
    cutoff = now - REPLAY_WINDOW
    for r in records:
        if (isinstance(r, dict) and r.get("lead_id") == lead_id
                and r.get("sender_ref") == sender_ref
                and r.get("raw_text_sha256") == complete_sha):
            ts = _parse_ts(r.get("captured_at"))
            if ts is not None and ts > cutoff:
                return r.get("amendment_id")
    return None


def _atomic_write(path: Path, store: dict) -> None:
    from safe_io import atomic_write_json  # lazy
    atomic_write_json(path, store, mode=0o640)


# ── Audit (metadata only; raw text NEVER enters a general log) ───────────────
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
    except Exception as e:  # noqa: BLE001 — audit is best-effort, never blocks capture
        sys.stderr.write(
            f"catering_amendments: audit write failed for {entry_type}: "
            f"{type(e).__name__}: {str(e)[:200]}\n"
        )


def _emit_failed(lead_id: str, reason: str, emit: bool) -> None:
    if emit:
        _emit_audit("catering_amendment_capture_failed", {"lead_id": lead_id, "reason": reason})


def _emit_captured(lead_id: str, amendment_id: str, message_id: str, source: str,
                   text_len: int, emit: bool) -> None:
    if emit:
        _emit_audit("catering_amendment_captured", {
            "lead_id": lead_id, "amendment_id": amendment_id, "message_id": message_id,
            "source": source, "text_len": text_len,
        })


# ── Public capture entry point ────────────────────────────────────────────────
def capture_branch_b_amendment(
    *,
    lead: dict,
    text: str,
    chat_id: Optional[str],
    phone: Optional[str],
    message_id: str,
    source_transport: str,
    provider_timestamp: Optional[str] = None,
    now: Optional[datetime] = None,
    data_path=None,
    lock_path=None,
    expected_owner: Optional[str] = None,
    expected_group: Optional[str] = None,
    lock_attempts: int = 20,
    lock_sleep_sec: float = 0.5,
    emit_audit: bool = True,
) -> CaptureResult:
    """Durably capture a Branch-B amendment. Returns CaptureResult(ok=...). On ANY
    failure the store is PRESERVED, a metadata-only capture_failed row is emitted,
    and ok=False (the caller sends the deterministic retry reply). Success is never
    claimed on failure. `now` is an INJECTED tz-aware clock (24h-window determinism)."""
    from safe_io import LockUnavailable, try_acquire_filelock_with_retry  # lazy

    now = now or datetime.now(timezone.utc)
    data_path = Path(data_path) if data_path else _data_path()
    lock_path = Path(lock_path) if lock_path else _lock_path(data_path)
    expected_owner = expected_owner or DEFAULT_OWNER
    expected_group = expected_group or DEFAULT_GROUP

    lead_id = (lead or {}).get("lead_id") or ""
    if not lead_id:
        _emit_failed("", "no_lead", emit_audit)
        return CaptureResult(ok=False, reason="no_lead")

    complete = text or ""
    complete_sha = _sha256_text(complete)
    orig_len = len(complete)
    truncated = orig_len > RAW_TEXT_MAX
    prefix = complete[:RAW_TEXT_MAX]
    sender_ref = _sender_ref(chat_id, phone)
    fingerprint = _envelope_fingerprint(sender_ref, provider_timestamp, orig_len, complete_sha)
    base_sha = _canonical_json_sha256((lead or {}).get("extracted") or {})
    native_id = message_id or ""

    try:
        with try_acquire_filelock_with_retry(lock_path, attempts=lock_attempts,
                                             sleep_sec=lock_sleep_sec):
            fs_reason = _validate_fs(data_path, expected_owner, expected_group)
            if fs_reason:
                _emit_failed(lead_id, "fs_" + fs_reason, emit_audit)
                return CaptureResult(ok=False, reason="fs_" + fs_reason)

            store, load_reason = _load_store(data_path)
            if load_reason:
                _emit_failed(lead_id, load_reason, emit_audit)
                return CaptureResult(ok=False, reason=load_reason)

            existing = _find_duplicate(store, source_transport, lead_id, native_id,
                                       fingerprint, sender_ref, complete_sha, now)
            if existing is not None:
                return CaptureResult(ok=True, amendment_id=existing, idempotent=True)

            seq = int(store.get("next_seq", 1))
            amendment_id = "A%04d" % seq
            try:
                from schemas import CateringAmendmentRecord  # lazy
                record = CateringAmendmentRecord(
                    amendment_id=amendment_id, lead_id=lead_id, sender_ref=sender_ref,
                    source_transport=source_transport or "", message_id=native_id,
                    envelope_fingerprint=fingerprint, raw_text=prefix,
                    raw_text_truncated=truncated, raw_text_original_length=orig_len,
                    raw_text_sha256=complete_sha, captured_at=now, source="f7_branch_b",
                    status="captured", base_extracted_sha256=base_sha,
                )
            except Exception:
                _emit_failed(lead_id, "record_validation_failed", emit_audit)
                return CaptureResult(ok=False, reason="record_validation_failed")

            store["records"].append(record.model_dump(mode="json"))
            store["next_seq"] = seq + 1
            try:
                _atomic_write(data_path, store)
            except Exception:
                _emit_failed(lead_id, "write_failed", emit_audit)
                return CaptureResult(ok=False, reason="write_failed")

            post_reason = _validate_fs(data_path, expected_owner, expected_group)
            if post_reason:
                _emit_failed(lead_id, "postwrite_" + post_reason, emit_audit)
                return CaptureResult(ok=False, reason="postwrite_" + post_reason)

            _emit_captured(lead_id, amendment_id, native_id, "f7_branch_b", orig_len, emit_audit)
            return CaptureResult(ok=True, amendment_id=amendment_id)
    except LockUnavailable:
        _emit_failed(lead_id, "lock_unavailable", emit_audit)
        return CaptureResult(ok=False, reason="lock_unavailable")
    except Exception as e:  # noqa: BLE001 — capture must never raise into the router
        sys.stderr.write(
            f"catering_amendments: capture exception: {type(e).__name__}: {str(e)[:200]}\n")
        _emit_failed(lead_id, "capture_exception", emit_audit)
        return CaptureResult(ok=False, reason="capture_exception")
