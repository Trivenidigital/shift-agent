"""Catering automation-control kernel — STOP/pause/opt-out + human takeover.

Closes CAT-GO-09 (charter §5.4 STOP/opt-out) and CAT-GO-10 (§5.5 human
takeover). A DETERMINISTIC per-conversation state machine that owns the
outbound-suppression / takeover policy — the safety kernel, NOT model output
(the architecture boundary: a regulated safety decision must never be
model-owned). Flag-gated, DEFAULT OFF, byte-identical when off.

Two independently-activatable behaviors under one master flag:

    CATERING_AUTOMATION_CONTROL_ENABLED = "1"   master (gates BOTH enforcement
                                                points + any state read)
    CATERING_AUTOMATION_CONTROL_ALLOWLIST       comma-separated canonical chats;
                                                "*" graduates every chat, empty
                                                DISABLES (fail-closed, ppv1/#612
                                                wildcard semantics on the
                                                canonical identity key)
    CATERING_STOP_ENABLED     = "1"   customer STOP/pause/opt-out + resume
    CATERING_TAKEOVER_ENABLED = "1"   owner #XXXXX takeover / pause / release
                                                / resume (one flag; the owner
                                                command surface is indivisible)

State file `state/catering-automation-control.json` (atomic + FileLock), keyed
by ``flyer_identity.canonical_identity_key`` so a customer reaching us under a
LID and a phone-JID converges on ONE record. Per-conversation record::

    { mode: active | paused | opted_out | takeover,
      since_ts, actor, reason,
      logical_turn_id,          # the NATIVE inbound message id (PR-4 identity —
                                #   NOT the front-brain #643 turn id)
      pause_expires_ts,         # paused only; auto-expires to active at read time
      takeover_expires_ts,      # takeover only; same read-time expiry (M4/G3)
      ack_sent }                # the one-deterministic-ack guard (§5.4)

Absent record = ``active``. State persists across restart/deploy and is NOT
reset by rollback — it lives under ``state/`` which the deploy tarball never
overwrites; nothing here touches deploy/rollback.

FAIL BEHAVIOR (read error): preserve the last successfully-read mode for the
conversation (in-process cache); if never read, default ``active``. A pure
default-active would resume automation on a suppressed conversation (bad); a
pure fail-closed would mute a healthy one — the cache-last-known compromise
avoids both. Flag OFF → no state read at all (byte-identical).

Sending + the cf-router / bridge_post enforcement wiring live in their callers
(``src/plugins/cf-router/hooks.py`` and ``safe_io.bridge_post``); this module is
the pure state + policy kernel both share.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

from safe_io import (
    FileLock,
    atomic_write_json,
    notify_owner_with_fallback,
    safe_load_json,
)

# ── modes ────────────────────────────────────────────────────────────────────
MODE_ACTIVE = "active"
MODE_PAUSED = "paused"
MODE_OPTED_OUT = "opted_out"
MODE_TAKEOVER = "takeover"
ALL_MODES = frozenset({MODE_ACTIVE, MODE_PAUSED, MODE_OPTED_OUT, MODE_TAKEOVER})
# Modes that suppress automated customer handling / sends.
SUPPRESSED_MODES = frozenset({MODE_PAUSED, MODE_OPTED_OUT, MODE_TAKEOVER})
# safe_load_json statuses that are a CLEAN read (fall through to the record /
# default). Any other status (corrupt:* / oserror:* / future) is a READ ERROR:
# get_mode preserves the last-known mode; the OUTBOUND backstop fails CLOSED.
HEALTHY_LOAD_STATUSES = frozenset({"ok", "missing", "empty"})

# ── config / paths ───────────────────────────────────────────────────────────
_DEFAULT_STATE_PATH = "/opt/shift-agent/state/catering-automation-control.json"
# Default pause window (§5.4 "temporary"; auto-expires back to active). 7 days.
DEFAULT_PAUSE_SECONDS = 7 * 24 * 3600
# Default human-takeover window (M4/G3). 72h: long enough to cover a weekend the
# owner takes a lead over by hand, short enough that a takeover forgotten on a
# Friday does not mute the customer indefinitely. Re-issuing `#XXXXX takeover`
# rewrites the record and extends the window from that moment.
#
# Records written BEFORE M4 carry no takeover_expires_ts, and a missing expiry
# reads as NOT expired — so an existing takeover keeps its old never-expires
# behavior until the owner re-issues it. Deliberate: silently un-muting a
# conversation a human is handling is the failure this whole kernel exists to
# prevent.
TAKEOVER_DEFAULT_SECONDS = 72 * 3600

# ── deterministic customer-facing acks (§5.4). No completion/money claim so they
#    pass the outbound content screens; the resume instruction is inline so a
#    single ack both confirms the opt-out AND tells the customer how to re-start
#    (satisfies the one-deterministic-ack rule without a second send). ─────────
OPT_OUT_ACK = (
    "You won't receive further automated messages from us. "
    "Reply RESUME at any time to turn them back on."
)
PAUSE_ACK = (
    "Automated messages are paused. Reply RESUME to resume them sooner."
)
RESUME_ACK = (
    "You're all set — automated messages are back on. Thanks!"
)


# ── flag / allowlist gating (fail-closed) ────────────────────────────────────
def _normalize_chat_key(value: str) -> str:
    """Mirror safe_io._front_brain_normalize_chat_key: strip @-JID suffix,
    punctuation/plus, casefold. Fallback identity when canonicalization is
    unavailable."""
    v = (value or "").strip().casefold()
    if "@" in v:
        v = v.split("@", 1)[0]
    return "".join(c for c in v if c.isalnum())


def canonical_key(jid: str, phone: Optional[str] = None) -> str:
    """Stable per-conversation key (LID<->phone convergence via the shared
    flyer_identity helper). Fail-open to the raw-normalized key so the kernel
    never raises on a missing/broken cache module — same posture as
    actions.flyer_canonical_identity_key."""
    try:
        from flyer_identity import canonical_identity_key  # type: ignore
        return canonical_identity_key(jid, phone) or _normalize_chat_key(phone or jid or "")
    except Exception:
        return _normalize_chat_key(phone or jid or "")


def chat_key_hash(jid: str) -> str:
    """sha256[:32] of the CANONICAL key — the audit-row identity (never a raw
    identifier). Matches across the cf-router + bridge_post emit sites because
    both hash the canonical key."""
    return hashlib.sha256(
        canonical_key(jid).encode("utf-8", errors="ignore")
    ).hexdigest()[:32]


def enabled(jid: str) -> bool:
    """Master flag + allowlist admit this chat (ppv1/#612 wildcard semantics on
    the canonical key). Fail-CLOSED: flag off / empty allowlist / any error →
    False (byte-identical). ``*`` graduates every chat (explicit opt-in)."""
    try:
        if os.environ.get("CATERING_AUTOMATION_CONTROL_ENABLED", "") != "1":
            return False
        raw = [
            p.strip()
            for p in os.environ.get("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "").split(",")
            if p.strip()
        ]
        if "*" in raw:
            return True
        if not raw:
            return False
        want = canonical_key(jid)
        if not want:
            return False
        allow = {canonical_key(e) for e in raw}
        allow.discard("")
        return want in allow
    except Exception:
        return False


def stop_enabled() -> bool:
    """Customer STOP/pause/opt-out + resume handling active (independent of
    takeover). The master flag + allowlist are checked separately by the caller —
    this sub-flag only gates command DETECTION."""
    return os.environ.get("CATERING_STOP_ENABLED", "") == "1"


def takeover_enabled() -> bool:
    """Owner takeover/pause/release/resume handling active (independent of STOP).
    ONE flag for the whole owner command surface: arming pause without release —
    or release without pause — would leave the owner able to mute a customer with
    no way to un-mute them, or vice versa."""
    return os.environ.get("CATERING_TAKEOVER_ENABLED", "") == "1"


# ── state path ───────────────────────────────────────────────────────────────
def _state_path() -> Path:
    """Resolved at call time so a conftest fixture / operator override
    (CATERING_AUTOMATION_CONTROL_STATE_PATH) reaches both in-process and
    subprocess callers; defaults to the deployed VPS path."""
    return Path(
        os.environ.get("CATERING_AUTOMATION_CONTROL_STATE_PATH") or _DEFAULT_STATE_PATH
    )


# ── in-process last-known-mode cache (read-error fail behavior) ──────────────
_LAST_KNOWN_MODE: dict[str, str] = {}


def _is_expired(expires_ts: object) -> bool:
    """True when a suppression record's expiry is in the past. A malformed /
    missing expiry is treated as NOT expired (keep the chosen suppression) —
    failing toward the suppression the customer or owner asked for, never
    resuming on a parse hiccup. Shared by the pause and takeover windows."""
    if not expires_ts:
        return False
    try:
        exp = datetime.fromisoformat(str(expires_ts))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return datetime.now(tz=timezone.utc) >= exp


def _effective_mode(record: object) -> str:
    """Mode with pause (§5.4) and takeover (M4/G3) auto-expiry applied. Absent /
    malformed / unknown → active. Read-time expiry, so no sweeper is needed and a
    process that never restarts still sees the window close."""
    if not isinstance(record, dict):
        return MODE_ACTIVE
    mode = record.get("mode", MODE_ACTIVE)
    if mode not in ALL_MODES:
        return MODE_ACTIVE
    if mode == MODE_PAUSED and _is_expired(record.get("pause_expires_ts")):
        return MODE_ACTIVE
    if mode == MODE_TAKEOVER and _is_expired(record.get("takeover_expires_ts")):
        return MODE_ACTIVE
    return mode


def _load_conversations(path: Path) -> Tuple[dict, str]:
    """Return ({key: record}, status). Status is safe_load_json's — the caller
    decides whether an unhealthy status is a read-error (fail to last-known)."""
    doc, status = safe_load_json(path, default={})
    if not isinstance(doc, dict):
        return {}, status
    convs = doc.get("conversations")
    return (convs if isinstance(convs, dict) else {}), status


def get_record(jid: str) -> Optional[dict]:
    """Raw persisted record for this conversation (or None). Does NOT apply pause
    expiry — use get_mode for the effective mode. Best-effort: any read problem
    returns None."""
    key = canonical_key(jid)
    if not key:
        return None
    convs, status = _load_conversations(_state_path())
    if status not in ("ok", "missing", "empty"):
        return None
    rec = convs.get(key)
    return rec if isinstance(rec, dict) else None


# §12b state-corruption alert throttle. A corrupt state read is a SILENT reversal
# of customer/operator-applied state (see _alert_state_corrupt), so it must page
# the operator — but throttled so a persistent fault doesn't spam. In-process
# once/hour bound (module-global) gates the whole alert body; the notify
# chokepoint's cross-process same-message dedup (dedup_window_min below) throttles
# across fresh subprocesses too.
_STATE_ALERT_THROTTLE_SEC = 3600
_STATE_ALERT_DEDUP_MIN = 60
_last_state_alert_monotonic: float = 0.0


def _load_failure_class(status: str) -> str:
    """Classify a non-clean ``safe_load_json`` status by WHAT ACTUALLY HAPPENED to
    the file, because only one of these three quarantines anything (see
    ``safe_io.safe_load_json``):

      - ``"quarantined"`` (``corrupt:``) — invalid JSON AND the rename-aside
        SUCCEEDED. No live file remains, so every LATER read returns 'missing' →
        HEALTHY → every conversation reads 'active': suppression really is wiped.
      - ``"unrenamed"`` (``corrupt_unrenamed:``) — invalid JSON and the rename
        FAILED. The bad file is still in place, every read keeps failing, and the
        outbound backstop (``safe_io._automation_control_suppressed_mode``) fails
        CLOSED on any non-clean status — so suppression did NOT reset; the kernel
        is over-suppressing instead.
      - ``"unreadable"`` (``oserror:``) — the file was never parsed and never
        modified (permissions / mount / disk). Same fail-CLOSED consequence.

    Anything else is ``"unknown"``: a future status this function has not been
    taught, where claiming either outcome would be a guess."""
    s = str(status or "")
    if s.startswith("corrupt_unrenamed:"):
        return "unrenamed"
    if s.startswith("corrupt:"):
        return "quarantined"
    if s.startswith("oserror:"):
        return "unreadable"
    return "unknown"


# Per-class consequence, stated once so the operator page and the audit row (the
# two places this fact is reported) cannot drift apart.
_LOAD_FAILURE_AUDIT_DETAIL = {
    "quarantined": (
        "file QUARANTINED (renamed aside); no live state file remains, so later "
        "reads see 'missing' and every conversation reads active - STOP/opt-out "
        "and human-takeover suppression are wiped"
    ),
    "unrenamed": (
        "quarantine FAILED, bad file left in place; reads keep failing and the "
        "outbound backstop fails CLOSED - suppression did NOT reset, automated "
        "sends are being blocked"
    ),
    "unreadable": (
        "file not modified and not quarantined; reads keep failing and the "
        "outbound backstop fails CLOSED - suppression did NOT reset, automated "
        "sends are being blocked"
    ),
    "unknown": (
        "unrecognised failure status; whether the file was quarantined is not "
        "known here; the outbound backstop fails CLOSED meanwhile"
    ),
}

_LOAD_FAILURE_PAGE = {
    "quarantined": (
        "catering automation-control state corrupted (file quarantined)",
        "The catering automation-control state file held invalid JSON and WAS "
        "renamed aside to a .corrupt-<epoch> copy. No live state file remains, "
        "so every later read sees no file and every conversation reads active: "
        "STOP/opt-out, pause and human-takeover suppression are WIPED and "
        "automated replies can resume for customers who opted out or are under "
        "human takeover. Re-apply those modes; the renamed copy still holds the "
        "old records.",
    ),
    "unrenamed": (
        "catering automation-control state unreadable (quarantine FAILED)",
        "The catering automation-control state file holds invalid JSON AND the "
        "rename-aside failed, so nothing was quarantined - the bad file is still "
        "in place and every read will keep failing. Suppression has NOT reset to "
        "active: the outbound backstop fails CLOSED on any non-clean read, so "
        "automated sends to conversations this kernel covers are being BLOCKED, "
        "not leaked. Repair or move the file by hand; until then the kernel "
        "over-suppresses.",
    ),
    "unreadable": (
        "catering automation-control state unreadable (I/O error)",
        "The catering automation-control state file could not be read (OS error "
        "- permissions, mount or disk). Nothing was quarantined and the file was "
        "not modified, so its records are intact on disk. Suppression has NOT "
        "reset to active: the outbound backstop fails CLOSED on any non-clean "
        "read, so automated sends to conversations this kernel covers are being "
        "BLOCKED, not leaked. Fix the permissions or the state mount.",
    ),
    "unknown": (
        "catering automation-control state read failed",
        "The catering automation-control state read returned an unrecognised "
        "failure status, so whether the file was quarantined is not known from "
        "here - check the stderr line for the exact status. The outbound "
        "backstop fails CLOSED on any non-clean read, so automated sends to "
        "conversations this kernel covers are being BLOCKED meanwhile.",
    ),
}


def _alert_state_corrupt(status: str) -> None:
    """§12b: the automation-control state read FAILED. Page the operator at
    detection — but with the consequence that ACTUALLY follows from this status,
    not one story for all of them.

    Only ``corrupt:`` is the repo's silent-reversal class (safe_load_json renamed
    the file aside, so every later read is 'missing' → HEALTHY → every
    conversation reads 'active' and opt-outs / human-takeovers are SILENTLY
    WIPED). ``corrupt_unrenamed:`` and ``oserror:`` move NOTHING and keep
    failing, and the outbound backstop fails CLOSED on a non-clean read — so
    telling the operator the file "was quarantined" and suppression "may have
    reset to active" is backwards on both halves for those two: the kernel is
    over-suppressing, not leaking. See :func:`_load_failure_class`.

    Best-effort — NEVER raises into the read path. Throttled once/hour (in-process
    + the notify chokepoint's cross-process same-message dedup). The in-process
    bound is GLOBAL across classes, so a second failure of a DIFFERENT class
    inside the hour rides the stderr line instead of a second page. PLAIN TEXT
    body (no Markdown) per the §12b lesson so underscore-bearing mode names like
    ``opted_out`` are not mangled.

    DEEPER FIX (backlog): after a corruption event, REBUILD suppression state by
    replaying the ``catering_automation_control_changed`` audit rows instead of
    letting the wiped conversations default to active. Tracked as a follow-up; this
    PR ships only the cheap detection alert."""
    global _last_state_alert_monotonic
    now = time.monotonic()
    if _last_state_alert_monotonic and (now - _last_state_alert_monotonic) < _STATE_ALERT_THROTTLE_SEC:
        return
    _last_state_alert_monotonic = now
    failure_class = _load_failure_class(status)
    # Structured audit row (traceable, carries the specific status). Same
    # consequence text as the page below, so the two surfaces cannot drift.
    _emit(
        "health_check_failure",
        {
            "check": "catering_automation_control_state_corrupt",
            "detail": (
                f"automation-control state read {status}; "
                f"{_LOAD_FAILURE_AUDIT_DETAIL[failure_class]}"
            )[:500],
        },
    )
    # §12b operator page — one title+body per failure CLASS (not per status
    # string), so the once/hour same-message dedup still throttles a persistent
    # fault while a materially different failure still reads differently. The
    # exact status rides the audit row + stderr.
    title, body = _LOAD_FAILURE_PAGE[failure_class]
    try:
        sys.stderr.write(
            f"catering_automation_control_state_corrupt_alert_dispatched status={status}\n"
        )
    except Exception:
        pass
    delivered = False
    try:
        delivered = notify_owner_with_fallback(
            title, body, priority=1, source="automation_control_state_corrupt",
            dedup_window_min=_STATE_ALERT_DEDUP_MIN,
        )
    except Exception as e:  # noqa: BLE001 — alerting must never crash the read path
        try:
            sys.stderr.write(
                f"catering_automation_control_state_corrupt alert raised "
                f"({type(e).__name__}: {str(e)[:160]})\n"
            )
        except Exception:
            pass
    try:
        sys.stderr.write(
            f"catering_automation_control_state_corrupt_alert_delivered delivered={delivered}\n"
        )
    except Exception:
        pass


# §12b takeover auto-expiry. The takeover window closing is an AUTOMATED reversal
# of OPERATOR-applied state: the owner said "I am handling this customer by hand",
# and 72h later a read flips the conversation back to active and the assistant
# starts replying again — with nothing anywhere telling the owner it happened. The
# alert fires on the FIRST read that OBSERVES the transition; the marker below is
# persisted on the record so no later read re-alerts, and re-issuing takeover
# writes a fresh record (new expiry, no marker) so the NEXT expiry alerts again.
TAKEOVER_EXPIRY_ALERTED_FIELD = "takeover_expiry_alerted_for"
_TAKEOVER_EXPIRY_DEDUP_MIN = 60


def _claim_takeover_expiry_alert(key: str, expires_ts: str) -> bool:
    """Compare-and-set the alerted marker under the state lock. True EXACTLY ONCE
    per (record, expiry_ts) — the read-and-flip happens inside the lock, so two
    concurrent readers observing the same expiry cannot both page the owner.

    False (no alert) on any read/write problem, on a record that moved underneath
    us, or on an expiry already alerted for: an alerting hiccup must never fault
    the read path this sits on."""
    path = _state_path()
    try:
        with FileLock(Path(str(path) + ".lock")):
            doc, status = safe_load_json(path, default={})
            if status not in HEALTHY_LOAD_STATUSES or not isinstance(doc, dict):
                return False
            convs = doc.get("conversations")
            if not isinstance(convs, dict):
                return False
            rec = convs.get(key)
            if not isinstance(rec, dict):
                return False
            if str(rec.get("takeover_expires_ts") or "") != expires_ts:
                return False          # re-issued / released between read and claim
            if rec.get(TAKEOVER_EXPIRY_ALERTED_FIELD) == expires_ts:
                return False          # this expiry has already paged the owner
            rec[TAKEOVER_EXPIRY_ALERTED_FIELD] = expires_ts
            convs[key] = rec
            doc["conversations"] = convs
            doc.setdefault("schema_version", 1)
            atomic_write_json(path, doc)
            return True
    except Exception as e:  # noqa: BLE001 — never raise into the read path
        try:
            sys.stderr.write(
                f"catering_takeover_expiry_claim_failed ({type(e).__name__}: {str(e)[:160]})\n")
        except Exception:
            pass
        return False


def _observe_takeover_expiry(jid: str, key: str, record: dict) -> None:
    """§12b: page the owner + audit that an operator takeover auto-expired.

    Best-effort and idempotent — the persisted claim above gates everything below,
    so a second read of the same expired record does nothing at all. PLAIN TEXT
    body (no Markdown) per the §12b lesson: mode names like ``opted_out`` carry
    underscores that a Markdown parser silently eats."""
    expires_ts = str(record.get("takeover_expires_ts") or "")
    if not expires_ts or not _claim_takeover_expiry_alert(key, expires_ts):
        return
    emit_control_changed(
        jid, MODE_TAKEOVER, MODE_ACTIVE, actor="system",
        reason="takeover_auto_expiry",
    )
    key_hash = chat_key_hash(jid)
    title = "catering human takeover expired"
    body = (
        f"The human-takeover window you set on conversation {key_hash} has "
        f"expired ({expires_ts}) and automated replies to that customer have "
        f"resumed. If you are still handling them by hand, send 'takeover' with "
        f"their code again to extend it."
    )
    try:
        sys.stderr.write(
            f"catering_takeover_expiry_alert_dispatched chat={key_hash}\n")
    except Exception:
        pass
    delivered = False
    try:
        delivered = notify_owner_with_fallback(
            title, body, priority=1, source="automation_control_takeover_expiry",
            dedup_window_min=_TAKEOVER_EXPIRY_DEDUP_MIN,
        )
    except Exception as e:  # noqa: BLE001 — alerting must never crash the read path
        try:
            sys.stderr.write(
                f"catering_takeover_expiry alert raised "
                f"({type(e).__name__}: {str(e)[:160]})\n")
        except Exception:
            pass
    try:
        sys.stderr.write(
            f"catering_takeover_expiry_alert_delivered delivered={delivered}\n")
    except Exception:
        pass


def read_mode_and_status(jid: str) -> Tuple[str, str]:
    """Return ``(effective_mode, load_status)`` where load_status is
    safe_load_json's ('ok'/'missing'/'empty' are CLEAN; anything else is a read
    error). On a CLEAN read the last-known cache is updated; on a read error the
    cached (or default-active) mode is returned WITH the error status so the
    caller can choose its own fail posture — get_mode fails toward last-known,
    the OUTBOUND backstop fails CLOSED (must-fix a). A read error also PAGES the
    operator (§12b, best-effort) because it may have silently wiped suppression."""
    key = canonical_key(jid)
    if not key:
        return MODE_ACTIVE, "empty"
    convs, status = _load_conversations(_state_path())
    if status not in HEALTHY_LOAD_STATUSES:
        try:
            _alert_state_corrupt(status)  # §12b; best-effort, never raises
        except Exception:
            pass
        return _LAST_KNOWN_MODE.get(key, MODE_ACTIVE), status
    record = convs.get(key)
    mode = _effective_mode(record)
    # §12b, only on the (rare) read that actually WATCHES a takeover lapse — the
    # ordinary read path pays one dict lookup and one comparison for this.
    if (isinstance(record, dict) and record.get("mode") == MODE_TAKEOVER
            and mode == MODE_ACTIVE):
        try:
            _observe_takeover_expiry(jid, key, record)
        except Exception:  # noqa: BLE001 — alerting never faults a mode read
            pass
    _LAST_KNOWN_MODE[key] = mode
    return mode, status


def get_mode(jid: str) -> str:
    """Effective mode: active | paused | opted_out | takeover. Absent → active;
    an expired pause → active. On a READ ERROR (corrupt / oserror status),
    preserve the last successfully-read mode for this conversation; if never
    read, default active (see module fail-behavior note)."""
    return read_mode_and_status(jid)[0]


def mode_suppresses(mode: str) -> bool:
    """True when a mode suppresses automated customer handling / sends."""
    return mode in SUPPRESSED_MODES


def is_suppressed(jid: str) -> bool:
    """Convenience: the conversation's effective mode suppresses automation."""
    return mode_suppresses(get_mode(jid))


def set_mode(
    jid: str,
    to_mode: str,
    *,
    actor: str,
    reason: str,
    logical_turn_id: str = "",
    pause_seconds: Optional[int] = None,
    takeover_seconds: Optional[int] = None,
    ack_sent: Optional[bool] = None,
    ack_compare_and_set: bool = False,
) -> Tuple[str, str, bool]:
    """Atomically transition this conversation's mode under the state FileLock.
    Returns ``(from_mode, to_mode, ack_flipped)`` for the audit. Idempotent by
    construction — re-writing the same mode is a no-op transition the caller
    still audits.

    ``ack_flipped`` is True ONLY when ``ack_compare_and_set`` is set and THIS
    call flipped ``ack_sent`` False→True — the atomic one-deterministic-ack guard
    (must-fix b): the read-and-flip happens INSIDE the FileLock, so two
    concurrent distinct stop-family messages can never both observe ack_sent
    False and both send an ack. The caller sends the ack iff ``ack_flipped``.
    Otherwise it is False.

    ``ack_sent`` (used only when NOT ack_compare_and_set): when provided it is
    written verbatim; when None it is preserved for opted_out/paused and reset to
    False for active/takeover (a fresh suppression episode earns a fresh ack).

    ``pause_seconds`` / ``takeover_seconds`` override the respective default
    windows (DEFAULT_PAUSE_SECONDS / TAKEOVER_DEFAULT_SECONDS); each is written
    only on a transition INTO that mode, and both expire at read time."""
    if to_mode not in ALL_MODES:
        raise ValueError(f"unknown automation-control mode: {to_mode!r}")
    key = canonical_key(jid)
    path = _state_path()
    ack_flipped = False
    with FileLock(Path(str(path) + ".lock")):
        doc, _status = safe_load_json(path, default={})
        if not isinstance(doc, dict):
            doc = {}
        convs = doc.get("conversations")
        if not isinstance(convs, dict):
            convs = {}
        prev = convs.get(key) if isinstance(convs.get(key), dict) else {}
        from_mode = _effective_mode(prev)
        prev_ack = bool(prev.get("ack_sent", False))
        now = datetime.now(tz=timezone.utc)
        record: dict = {
            "mode": to_mode,
            "since_ts": now.isoformat(),
            "actor": actor,
            "reason": (reason or "")[:200],
            "logical_turn_id": logical_turn_id or "",
        }
        if to_mode == MODE_PAUSED:
            secs = DEFAULT_PAUSE_SECONDS if pause_seconds is None else max(1, int(pause_seconds))
            record["pause_expires_ts"] = (now + timedelta(seconds=secs)).isoformat()
        if to_mode == MODE_TAKEOVER:
            # M4/G3: bound the takeover window. Re-issuing takeover rewrites the
            # record, which extends the window from now — that IS the owner's
            # "keep it" gesture, no separate extend verb needed.
            tsecs = (
                TAKEOVER_DEFAULT_SECONDS if takeover_seconds is None
                else max(1, int(takeover_seconds))
            )
            record["takeover_expires_ts"] = (now + timedelta(seconds=tsecs)).isoformat()
        if ack_compare_and_set:
            # Atomic guard: send the ack only when it flips False→True here.
            record["ack_sent"] = True
            ack_flipped = not prev_ack
        elif ack_sent is not None:
            record["ack_sent"] = bool(ack_sent)
        elif to_mode in (MODE_OPTED_OUT, MODE_PAUSED):
            record["ack_sent"] = prev_ack
        else:
            record["ack_sent"] = False
        convs[key] = record
        doc["conversations"] = convs
        doc.setdefault("schema_version", 1)
        atomic_write_json(path, doc)
    _LAST_KNOWN_MODE[key] = to_mode
    return from_mode, to_mode, ack_flipped


# ── deterministic command detection (pure regex over customer/owner text) ────
# STOP is a structured, audit-bearing token, so regex detection is the correct
# deterministic tool here (CLAUDE.md permits regex for deterministic/audit tokens
# — not LLM-classifiable free intent).

# Owner-approval / lead code — same alphabet as ProposalCode + cf-router's
# _CODE_PATTERN (excludes I/O/0/1/L).
_CODE_RE = re.compile(r"#([A-HJKMNPQR-Z2-9]{5})")

# Anchored single-token forms (the whole message is the command) — strict so a
# sentence like "please stop by the store" never opts a customer out.
_STOP_TOKEN_RE = re.compile(r"^(stop|stopall|unsubscribe|optout|quit|end)$")
_PAUSE_TOKEN_RE = re.compile(r"^(pause|hold)$")
# BLOCKER-1b: NARROWED to unambiguous re-subscribe tokens only. The dropped words
# (start / restart / subscribe / confirm / continue) collide with catering close
# vocabulary — an ACTIVE customer typing "confirm"/"continue" must route to
# proposal-selection, never be captured as a resume. Belt-and-suspenders with the
# resume mode-gate in the orchestrator (a resume token is a NO-OP on an active
# conversation). RESUME is the token the opt-out/pause acks instruct.
_RESUME_TOKEN_RE = re.compile(r"^(resume|unstop|unpause|resubscribe)$")
# Multi-word phrases (clear intent anywhere in the message).
_STOP_PHRASE_RE = re.compile(
    r"\b(opt\s*out|unsubscribe|stop\s+messag(?:e|es|ing)|no\s+more\s+messages?|remove\s+me)\b"
)
_PAUSE_PHRASE_RE = re.compile(r"\b(hold\s*off|pause\s+messag(?:e|es|ing)?)\b")


def _normalize_command_text(text: str) -> str:
    """Lowercase, strip surrounding whitespace + trailing punctuation, collapse
    internal whitespace — the form the anchored token regexes match."""
    s = (text or "").strip().casefold()
    s = re.sub(r"[\s]+", " ", s)
    return s.strip(" .!?,;:\"'")


def detect_customer_command(text: str) -> Optional[str]:
    """Classify a customer message as a STOP-family command: ``"stop"`` |
    ``"pause"`` | ``"resume"`` | None. Resume is checked first so an explicit
    re-subscribe token is never shadowed; stop before pause."""
    norm = _normalize_command_text(text)
    if not norm:
        return None
    if _RESUME_TOKEN_RE.match(norm):
        return "resume"
    if _STOP_TOKEN_RE.match(norm) or _STOP_PHRASE_RE.search(norm):
        return "stop"
    if _PAUSE_TOKEN_RE.match(norm) or _PAUSE_PHRASE_RE.search(norm):
        return "pause"
    return None


# Owner PAUSE verbs (M4/G3). Distinct from takeover: a pause just mutes
# automation for a while (auto-expiring after DEFAULT_PAUSE_SECONDS, exactly like
# the customer-initiated pause), whereas a takeover asserts a human is now
# handling that customer. `hold` is included because it is the word owners
# actually use, and it mirrors the customer-side _PAUSE_TOKEN_RE.
_OWNER_PAUSE_RE = re.compile(r"\b(pause|hold)\b")


def detect_owner_command(text: str) -> Optional[Tuple[str, str]]:
    """Classify an owner self-chat message as an automation-control command:
    ``("takeover", code)``, ``("paused", code)`` (pause/hold — M4/G3), or
    ``("active", code)`` (release/resume), or None. A ``#XXXXX`` code is REQUIRED
    — it identifies the target lead / customer conversation (§5.5 "#XXXXX
    takeover <lead>").

    Precedence is takeover → release/resume → pause. Release is checked ahead of
    pause for the same reason the customer detector checks resume first: the
    un-suppress verb must never be shadowed, so a message carrying both reads as
    the owner un-muting."""
    if not text:
        return None
    code_match = _CODE_RE.search(text)
    if not code_match:
        return None
    code = "#" + code_match.group(1).upper()
    low = text.casefold()
    if re.search(r"\btake\s*over\b", low):
        return "takeover", code
    if re.search(r"\b(release|hand\s*back|hand\s*off|re-?enable|resume|reactivate)\b", low):
        return "active", code
    if _OWNER_PAUSE_RE.search(low):
        return MODE_PAUSED, code
    return None


def _window_days(seconds: int) -> str:
    """Render a window as a whole number of days for owner-facing text, floored
    at 1 so a sub-day window never reads as "0 days"."""
    return f"{max(1, round(seconds / 86400))} day{'s' if round(seconds / 86400) != 1 else ''}"


def owner_confirmation(to_mode: str, code: str) -> str:
    """Deterministic one-line confirmation sent back to the owner self-chat so a
    #XXXXX command is never a silent black hole. Each suppressing verb states its
    auto-expiry window, so the owner is never surprised when it lapses."""
    if to_mode == MODE_TAKEOVER:
        return (
            f"Takeover ON for {code} — automated replies to that customer are "
            f"paused until you release it, or for {_window_days(TAKEOVER_DEFAULT_SECONDS)} "
            f"if you don't. Send 'takeover {code}' again to extend."
        )
    if to_mode == MODE_PAUSED:
        return (
            f"Automation paused for {code} — no automated replies to that "
            f"customer for {_window_days(DEFAULT_PAUSE_SECONDS)}. "
            f"Send 'resume {code}' to turn them back on sooner."
        )
    return f"Automation resumed for {code} — the assistant will handle that customer again."


# ── audit emission (shared chokepoint via safe_io) ───────────────────────────
def _emit(entry_type: str, fields: dict) -> None:
    """Append a LogEntry via the canonical safe_io audit chokepoint. Best-effort
    — a broken audit pipeline must never regress the customer/owner behavior."""
    try:
        from safe_io import _try_emit_audit_row  # lazy — no import cycle at load
        _try_emit_audit_row(entry_type, fields)
    except Exception as e:  # noqa: BLE001
        try:
            sys.stderr.write(f"automation_control: audit emit failed (non-fatal): {e}\n")
        except Exception:
            pass


def emit_control_changed(
    jid: str,
    from_mode: str,
    to_mode: str,
    actor: str,
    reason: str,
    *,
    logical_turn_id: str = "",
) -> None:
    """Emit ``catering_automation_control_changed`` (metadata-only)."""
    _emit(
        "catering_automation_control_changed",
        {
            "chat_key_hash": chat_key_hash(jid),
            "from_mode": from_mode,
            "to_mode": to_mode,
            "actor": actor,
            "reason": (reason or "")[:200],
            "logical_turn_id": logical_turn_id or "",
        },
    )


def emit_send_suppressed(
    jid: str,
    mode: str,
    suppressed_send_kind: str,
    *,
    logical_turn_id: str = "",
) -> None:
    """Emit ``catering_automated_send_suppressed`` (metadata-only)."""
    _emit(
        "catering_automated_send_suppressed",
        {
            "chat_key_hash": chat_key_hash(jid),
            "mode": mode,
            "suppressed_send_kind": suppressed_send_kind,
            "logical_turn_id": logical_turn_id or "",
        },
    )
