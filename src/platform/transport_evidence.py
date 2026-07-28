"""Governed live transport-budget evidence-enablement harness — control plane.

This module holds the *testable* logic the ``tools/patch-hermes.py``
``shift-agent-transport-evidence-probe`` patch wires into the Hermes-core
gateway (exactly as the front-brain send-screen patch calls into ``safe_io``):

  * the canonical harness-manifest digest (computed identically by the CLI and
    the gateway),
  * the strict two-phase PREPARE / COMMIT protocol schemas + framing,
  * the single-use, connection-bound prep token,
  * the (no-mutation) prerequisite verification run at PREPARE and re-run before
    the first send at COMMIT,
  * ``EvidenceControlServer`` — the two-phase handler + the ``SO_PEERCRED``
    ``uid == 0`` control-socket accept loop with ``lstat``-guarded stale-socket
    cleanup.

Everything the harness actually mutates lives behind INJECTABLE SEAMS
(``PrereqSeams``) so the whole control plane is exercised in-process with pure
Python fakes — no real socket / gateway / bridge / provider send. The gateway
patch binds those seams to the live ``_is_user_authorized`` / ``.commit-hash`` /
budget / health / diagnostic-event-injection surfaces.

Default-OFF everywhere. Repository implementation only — this change never
executes on the box. Contract: tasks/audits/catering-review-part1-2026-07-26/
transport-evidence-harness-frozen-spec.md.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from transport_evidence_ledger import (
    AttemptLedger,
    CommittedNonceLedger,
    EpochError,
    LedgerCorruption,
    ReconcileLedger,
    RunLedger,
    SingletonLock,
    SingletonLockError,
    establish_process_epoch,
    reconcile_prior_epoch,
    record_current_epoch,
)

# ── configuration (all default-OFF / overridable for tests) ──────────────────
FEATURE_FLAG_ENV = "GATEWAY_TRANSPORT_EVIDENCE_ENABLED"

DEFAULT_SOCKET_PATH = "/run/shift-agent/transport-evidence.sock"
DEFAULT_STATE_DIR = "/opt/shift-agent/state/transport-evidence"
DEFAULT_COMMIT_HASH_PATH = "/opt/shift-agent/.commit-hash"

# Expected caps proven by the probe (frozen spec §11). Exactly 5 / 50.
EXPECTED_FINALIZED_CAP = 5
EXPECTED_DRAFT_CAP = 50

# The deterministic response plan.
PLAN_SEGMENTS = 28
EXPECTED_ACCEPTED = 5
EXPECTED_DENIED = PLAN_SEGMENTS - EXPECTED_ACCEPTED  # 23

# The dedicated diagnostic event type. internal=True is set only by this in-proc
# forge site; the WhatsApp ingress never deserializes internal=True.
DIAGNOSTIC_EVENT_TYPE = "shift_agent_transport_evidence_probe"

# Framing / deadlines / token lifetime.
MAX_FRAME_BYTES = 65536
READ_DEADLINE_SEC = 10.0
WRITE_DEADLINE_SEC = 10.0
PREP_TOKEN_TTL_SEC = 30.0

# The FIXED harness manifest (AMENDMENT 3 / D3) — enumerated install-relative
# paths under ONE trusted root (/opt/shift-agent). NOT a directory walk, NOT
# self-referential (the digest output is never an input), NOT basename-only.
# Normalized "/" keys; no "."/".." ; no absolute; no duplicate normalized keys;
# symlinks + escaping rejected; deterministic sort; per-entry canonical input
# "<normalized-path>\0<byte-count>\0<sha256-of-bytes>\n". CLI + gateway compute
# the identical digest over the identical installed bytes.
HARNESS_MANIFEST_ROOT_DEFAULT = "/opt/shift-agent"
HARNESS_MANIFEST_RELPATHS = (
    "transport_evidence.py",
    "transport_evidence_diagnostic.py",
    "transport_evidence_ledger.py",
    "transport_evidence_lease.py",
)


def harness_manifest_root() -> str:
    return os.environ.get("SHIFT_AGENT_TE_MANIFEST_ROOT", HARNESS_MANIFEST_ROOT_DEFAULT)


class ManifestError(RuntimeError):
    """A manifest path violated a D3 invariant (absolute / . / .. / dup / symlink
    / escapes root / missing) → fail closed. The digest is never computed over an
    ambiguous or unsafe manifest."""


class ProtocolError(RuntimeError):
    """A malformed / oversized / unknown-op control-socket frame → reject."""


class SocketSecurityError(RuntimeError):
    """A control-socket security invariant was violated → fail closed."""


# ── environment helpers ──────────────────────────────────────────────────────


def feature_enabled() -> bool:
    """True only when the feature flag == "1". Default OFF → the socket is never
    bound and every op is rejected."""
    return os.environ.get(FEATURE_FLAG_ENV, "") == "1"


def socket_path() -> str:
    return os.environ.get("SHIFT_AGENT_TE_SOCKET_PATH", DEFAULT_SOCKET_PATH)


def state_dir() -> str:
    return os.environ.get("SHIFT_AGENT_TE_STATE_DIR", DEFAULT_STATE_DIR)


def commit_hash_path() -> str:
    return os.environ.get("SHIFT_AGENT_TE_COMMIT_HASH_PATH", DEFAULT_COMMIT_HASH_PATH)


# ── canonical harness-manifest digest (shared, CLI == gateway) ───────────────


def _normalize_manifest_key(rel_path: str) -> str:
    """Normalize an install-relative manifest path to a canonical "/"-separated
    key, rejecting absolute paths and any "." / ".." component (D3)."""
    if not rel_path or not isinstance(rel_path, str):
        raise ManifestError(f"empty/invalid manifest entry {rel_path!r}")
    s = rel_path.replace("\\", "/")
    if s.startswith("/"):
        raise ManifestError(f"absolute manifest path not allowed: {rel_path!r}")
    parts = [p for p in s.split("/") if p != ""]
    if not parts:
        raise ManifestError(f"manifest path normalizes to empty: {rel_path!r}")
    for p in parts:
        if p in (".", ".."):
            raise ManifestError(f"'.'/'..' component not allowed: {rel_path!r}")
    return "/".join(parts)


def canonical_harness_manifest_digest(root: str, rel_paths: Sequence[str]) -> str:
    """Deterministic digest over a FIXED manifest of install-relative paths under
    ONE trusted ``root`` (AMENDMENT 3 / D3).

    Per entry, after normalization + safety checks: read the bytes and contribute
    the canonical line ``<normalized-path>\\0<byte-count>\\0<sha256-of-bytes>\\n``.
    Entries are sorted deterministically by normalized path; the concatenation is
    sha256'd. Rejects (fail closed) absolute / "."/".." / duplicate keys /
    symlinks / files escaping ``root``. CLI + gateway compute the identical value
    over the identical installed bytes; a single differing byte → digest changes
    → PREPARE/COMMIT refuse."""
    root_resolved = Path(root).resolve()
    seen: set[str] = set()
    entries: list[tuple[str, int, str]] = []
    for rp in rel_paths:
        key = _normalize_manifest_key(rp)
        if key in seen:
            raise ManifestError(f"duplicate normalized manifest key: {key!r}")
        seen.add(key)
        full = root_resolved / key
        # Reject a symlink at any component + a target escaping the trusted root.
        if full.is_symlink():
            raise ManifestError(f"manifest entry is a symlink: {key!r}")
        try:
            resolved = full.resolve(strict=True)
        except OSError as e:
            raise ManifestError(f"manifest entry unreadable {key!r}: {e}") from e
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise ManifestError(f"manifest entry escapes root: {key!r}")
        if resolved != full:
            # a parent component was a symlink / the path was not canonical
            raise ManifestError(f"manifest entry path not canonical (symlink?): {key!r}")
        data = resolved.read_bytes()
        entries.append((key, len(data), hashlib.sha256(data).hexdigest()))
    entries.sort(key=lambda e: e[0])
    blob = "".join(f"{key}\0{n}\0{h}\n" for key, n, h in entries).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def default_harness_manifest_digest() -> str:
    """The fixed on-box manifest digest (root = /opt/shift-agent by default)."""
    return canonical_harness_manifest_digest(
        harness_manifest_root(), HARNESS_MANIFEST_RELPATHS
    )


# ── protocol schemas (strict; unknown fields rejected) ───────────────────────


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    op: str = Field(pattern="^prepare$")
    nonce: str = Field(min_length=1, max_length=512)
    authorized_commit: str = Field(min_length=1, max_length=128)
    authorized_harness_hash: str = Field(min_length=1, max_length=128)
    normalized_destination: str = Field(min_length=1, max_length=512)
    expires_at: str = Field(min_length=1, max_length=64)
    diagnostic_event_type: str = Field(min_length=1, max_length=128)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    op: str = Field(pattern="^commit$")
    prep_token: str = Field(min_length=1, max_length=512)
    consumption_attestation: dict = Field(default_factory=dict)


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    op: str = Field(pattern="^status$")
    run_id: str = Field(min_length=1, max_length=128)


# ── single-use, connection-bound prep token ──────────────────────────────────


@dataclass
class PrepToken:
    token: str
    conn_id: str
    peer_uid: int
    nonce: str
    destination: str
    commit: str
    harness_hash: str
    diagnostic_event_type: str
    expires_at: datetime
    used: bool = False

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def matches(self, presented: str) -> bool:
        return hmac.compare_digest(self.token, presented)


# ── prerequisite verification (no mutation) ──────────────────────────────────


@dataclass
class PrereqSeams:
    """Every live prerequisite the harness reads is an injectable callable so the
    control plane is fully testable in-process. The gateway patch binds these to
    the real surfaces; tests pass fakes."""

    feature_enabled: Callable[[], bool]
    read_commit_hash: Callable[[], str]
    installed_harness_digest: Callable[[], str]
    containment_active: Callable[[], bool]
    # (platform, [alias forms]) -> authorized?  bound to the REAL _is_user_authorized
    is_destination_authorized: Callable[[str, list[str]], bool]
    # normalized_destination -> concrete alias forms (phone + LID) to check
    destination_alias_forms: Callable[[str], list[str]]
    # -> (enabled, finalized_cap, draft_cap)
    budget_state: Callable[[], tuple[bool, int, int]]
    # -> (gateway_healthy, bridge_healthy, queue_depth)
    health_state: Callable[[], tuple[bool, bool, int]]
    clock: Callable[[], datetime]


@dataclass
class PrereqResult:
    ok: bool
    reason: str = "ok"


def _parse_iso_utc(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verify_prerequisites(
    *,
    nonce: str,
    authorized_commit: str,
    authorized_harness_hash: str,
    normalized_destination: str,
    expires_at: str,
    diagnostic_event_type: str,
    seams: PrereqSeams,
    committed_ledger: CommittedNonceLedger,
) -> PrereqResult:
    """Independent, no-mutation verification of EVERY live prerequisite. Run at
    PREPARE and re-run before the first send at COMMIT; any drift → refuse.

    Order is defensive: cheap local gates first, the durable committed-nonce
    check last."""
    try:
        if not seams.feature_enabled():
            return PrereqResult(False, "feature_off")

        if diagnostic_event_type != DIAGNOSTIC_EVENT_TYPE:
            return PrereqResult(False, "event_type_invalid")

        live_commit = (seams.read_commit_hash() or "").strip()
        if not live_commit or live_commit != authorized_commit.strip():
            return PrereqResult(False, "commit_mismatch")

        live_hash = (seams.installed_harness_digest() or "").strip()
        if not live_hash or not hmac.compare_digest(live_hash, authorized_harness_hash.strip()):
            return PrereqResult(False, "harness_hash_mismatch")

        if not seams.containment_active():
            return PrereqResult(False, "containment_inactive")

        forms = seams.destination_alias_forms(normalized_destination)
        if not forms:
            return PrereqResult(False, "destination_no_alias_forms")
        if not seams.is_destination_authorized("whatsapp", list(forms)):
            return PrereqResult(False, "destination_unauthorized")

        enabled, finalized_cap, draft_cap = seams.budget_state()
        if not enabled:
            return PrereqResult(False, "budget_off")
        if finalized_cap != EXPECTED_FINALIZED_CAP or draft_cap != EXPECTED_DRAFT_CAP:
            return PrereqResult(False, "caps_unexpected")

        gw_ok, bridge_ok, queue_depth = seams.health_state()
        if not gw_ok or not bridge_ok:
            return PrereqResult(False, "unhealthy")
        if queue_depth != 0:
            return PrereqResult(False, "queue_not_drained")

        try:
            expires = _parse_iso_utc(expires_at)
        except (ValueError, TypeError):
            return PrereqResult(False, "expiry_unparseable")
        if expires <= seams.clock():
            return PrereqResult(False, "expired")

        if not nonce:
            return PrereqResult(False, "nonce_invalid")

        if committed_ledger.contains(nonce):
            return PrereqResult(False, "nonce_already_committed")
    except LedgerCorruption:
        return PrereqResult(False, "ledger_corrupt")
    except Exception as e:  # noqa: BLE001 — any verify fault → fail closed
        return PrereqResult(False, f"verify_fault:{type(e).__name__}")
    return PrereqResult(True, "ok")


# ── the two-phase control server ─────────────────────────────────────────────


@dataclass
class RunContext:
    """Everything the injected diagnostic event needs, held in-process keyed by
    run_id. The injected MessageEvent carries only the run_id + marker."""

    run_id: str
    nonce: str
    destination: str
    logical_turn_id: str
    authorized_commit: str
    harness_hash: str
    diagnostic_event_type: str
    attempt_ledger: AttemptLedger


class EvidenceControlServer:
    """Two-phase PREPARE / COMMIT handler + the peer-authenticated accept loop.

    All privileged live state is reached through ``seams``; the durable
    committed-nonce / run / attempt ledgers are the only things this class
    writes. ``inject_diagnostic`` is the seam that hands a committed ``RunContext``
    to the gateway coordinator (real ``_handle_message`` on the box; a fake in
    tests) — the server itself performs NO sends.
    """

    def __init__(
        self,
        *,
        seams: PrereqSeams,
        committed_ledger: CommittedNonceLedger,
        run_ledger: RunLedger,
        attempt_ledger_factory: Callable[[str, str], AttemptLedger],
        inject_diagnostic: Callable[[RunContext], None],
        epoch: Optional[str] = None,
        token_ttl_sec: float = PREP_TOKEN_TTL_SEC,
        run_id_factory: Optional[Callable[[], str]] = None,
        logical_turn_id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self.seams = seams
        self.committed_ledger = committed_ledger
        self.run_ledger = run_ledger
        # factory(run_id, epoch) -> AttemptLedger stamped with the process epoch.
        self.attempt_ledger_factory = attempt_ledger_factory
        self.inject_diagnostic = inject_diagnostic
        # This gateway process's epoch (amendment 1/2). Written on every
        # run/attempt record; a restart yields a different epoch → prior records
        # reconcile. establish_process_epoch is fail-closed on corrupt kernel data
        # and falls back to a durable random UUID only where /proc is absent.
        self.epoch = epoch or establish_process_epoch()
        self.token_ttl_sec = token_ttl_sec
        # AMENDMENT 2: PREPARE/COMMIT are refused until startup reconciliation has
        # completed durably. serve_forever also refuses to bind before this is set.
        self._reconciled = False
        # Shutdown gate: disabled FIRST at teardown (before the socket closes and
        # before the singleton lock releases) so the control plane is never
        # outbound-capable while the lock is being released.
        self._active = True
        # D1: the captured gateway asyncio loop the inject seam schedules onto.
        # Readiness requires it to be set (loop init complete) before PREPARE/COMMIT.
        self._loop = None
        self._run_id_factory = run_id_factory or (lambda: "run_" + secrets.token_hex(8))
        self._logical_turn_id_factory = logical_turn_id_factory or (
            lambda: "te_turn_" + secrets.token_hex(8)
        )
        # conn_id -> minted PrepToken (single-use, connection-bound).
        self._tokens: dict[str, PrepToken] = {}
        # Serializes the COMMIT critical section (nonce check + record) so two
        # racing connections cannot both win a nonce.
        self._commit_lock = threading.Lock()

    @property
    def reconciled(self) -> bool:
        return self._reconciled

    def mark_reconciled(self) -> None:
        """Called by the startup orchestrator ONLY after prior-epoch
        reconciliation has been persisted + fsync'd. Before this, PREPARE/COMMIT
        are refused and serve_forever will not bind the socket."""
        self._reconciled = True

    @property
    def active(self) -> bool:
        return self._active

    @property
    def gateway_loop(self):
        return self._loop

    def set_loop(self, loop) -> None:
        """D1: record the captured running gateway asyncio loop. Part of the
        readiness precondition — inject schedules the diagnostic onto this loop."""
        self._loop = loop

    def ready(self) -> bool:
        """D1: PREPARE/COMMIT are served only when reconciliation completed, the
        loop was captured, and the control plane is active (not shutting down)."""
        return self._reconciled and self._active and self._loop is not None

    def disable(self) -> None:
        """Disable the outbound-capable control plane (PREPARE/COMMIT refuse).
        Called at teardown BEFORE the socket closes and BEFORE the singleton lock
        releases — the lock is never released while still outbound-capable."""
        self._active = False

    # -- request-object shaping --
    @staticmethod
    def _prereq_fields_from_token(tok: PrepToken) -> dict:
        return {
            "nonce": tok.nonce,
            "authorized_commit": tok.commit,
            "authorized_harness_hash": tok.harness_hash,
            "normalized_destination": tok.destination,
            "diagnostic_event_type": tok.diagnostic_event_type,
        }

    # -- PREPARE --
    def handle_prepare(self, conn, raw: dict) -> dict:
        if getattr(conn, "peer_uid", None) != 0:
            return {"status": "error", "phase": "auth", "reason": "peer_not_root"}
        if not self._active:
            return {"status": "refused", "phase": "prepare", "reason": "shutting_down"}
        if not self._reconciled:
            return {"status": "refused", "phase": "prepare", "reason": "reconciliation_incomplete"}
        if self._loop is None:
            return {"status": "refused", "phase": "prepare", "reason": "loop_not_ready"}
        try:
            req = PrepareRequest.model_validate(raw)
        except ValidationError as e:
            return {"status": "error", "phase": "prepare", "reason": "schema", "detail": _v(e)}

        now = self.seams.clock()
        result = verify_prerequisites(
            nonce=req.nonce,
            authorized_commit=req.authorized_commit,
            authorized_harness_hash=req.authorized_harness_hash,
            normalized_destination=req.normalized_destination,
            expires_at=req.expires_at,
            diagnostic_event_type=req.diagnostic_event_type,
            seams=self.seams,
            committed_ledger=self.committed_ledger,
        )
        if not result.ok:
            return {"status": "refused", "phase": "prepare", "reason": result.reason}

        token = PrepToken(
            token=secrets.token_urlsafe(32),
            conn_id=conn.id,
            peer_uid=0,
            nonce=req.nonce,
            destination=req.normalized_destination,
            commit=req.authorized_commit,
            harness_hash=req.authorized_harness_hash,
            diagnostic_event_type=req.diagnostic_event_type,
            expires_at=now + timedelta(seconds=self.token_ttl_sec),
        )
        self._tokens[conn.id] = token
        return {
            "status": "prepared",
            "prep_token": token.token,
            "expires_at": token.expires_at.isoformat().replace("+00:00", "Z"),
        }

    # -- COMMIT --
    def handle_commit(self, conn, raw: dict) -> dict:
        if getattr(conn, "peer_uid", None) != 0:
            return {"status": "error", "phase": "auth", "reason": "peer_not_root"}
        if not self._active:
            return {"status": "refused", "phase": "commit", "reason": "shutting_down"}
        if not self._reconciled:
            return {"status": "refused", "phase": "commit", "reason": "reconciliation_incomplete"}
        if self._loop is None:
            return {"status": "refused", "phase": "commit", "reason": "loop_not_ready"}
        try:
            req = CommitRequest.model_validate(raw)
        except ValidationError as e:
            return {"status": "error", "phase": "commit", "reason": "schema", "detail": _v(e)}

        # The token is bound to THIS connection: a token minted on another
        # connection (or none at all) cannot commit here.
        token = self._tokens.get(conn.id)
        if token is None:
            return {"status": "refused", "phase": "commit", "reason": "no_prepare_on_connection"}

        # Single-use: consume the token now, before any further check, so a
        # replay (even of a refused commit) cannot reuse it.
        self._tokens.pop(conn.id, None)

        now = self.seams.clock()
        if token.used:
            return {"status": "refused", "phase": "commit", "reason": "token_replayed"}
        token.used = True
        if token.is_expired(now):
            return {"status": "refused", "phase": "commit", "reason": "token_expired"}
        if not token.matches(req.prep_token):
            return {"status": "refused", "phase": "commit", "reason": "token_mismatch"}

        # Re-verify EVERY prerequisite (containment / budget / commit / harness
        # re-checked) using the values BOUND at PREPARE — any drift fails here,
        # before the first send.
        result = verify_prerequisites(
            expires_at=token.expires_at.isoformat().replace("+00:00", "Z"),
            seams=self.seams,
            committed_ledger=self.committed_ledger,
            **self._prereq_fields_from_token(token),
        )
        if not result.ok:
            return {"status": "refused", "phase": "commit", "reason": f"drift:{result.reason}"}

        # Durable at-most-once: under the commit lock, re-check the committed
        # ledger and record {run + nonce} (fsync) BEFORE the first send. A racing
        # connection that already committed this nonce loses here.
        with self._commit_lock:
            try:
                if self.committed_ledger.contains(token.nonce):
                    return {"status": "refused", "phase": "commit", "reason": "nonce_already_committed"}
                run_id = self._run_id_factory()
                logical_turn_id = self._logical_turn_id_factory()
                ts = now.isoformat().replace("+00:00", "Z")
                self.run_ledger.record_committed(
                    run_id=run_id,
                    nonce=token.nonce,
                    destination=token.destination,
                    logical_turn_id=logical_turn_id,
                    authorized_commit=token.commit,
                    harness_hash=token.harness_hash,
                    epoch=self.epoch,
                    ts=ts,
                )
                self.committed_ledger.record(token.nonce, run_id, ts)
            except LedgerCorruption:
                return {"status": "refused", "phase": "commit", "reason": "ledger_corrupt"}
            except Exception as e:  # noqa: BLE001 — durable-record fault → HALT, no send
                return {"status": "error", "phase": "commit", "reason": f"record_fault:{type(e).__name__}"}

        # Committed + recorded. Hand the run to the coordinator (the injected
        # diagnostic event flows through _handle_message → the 28-segment plan).
        ctx = RunContext(
            run_id=run_id,
            nonce=token.nonce,
            destination=token.destination,
            logical_turn_id=logical_turn_id,
            authorized_commit=token.commit,
            harness_hash=token.harness_hash,
            diagnostic_event_type=token.diagnostic_event_type,
            attempt_ledger=self.attempt_ledger_factory(run_id, self.epoch),
        )
        try:
            self.inject_diagnostic(ctx)
        except Exception as e:  # noqa: BLE001 — injection fault is reported; run recorded
            return {
                "status": "committed",
                "run_id": run_id,
                "inject_error": f"{type(e).__name__}",
            }
        return {"status": "committed", "run_id": run_id}

    # -- STATUS (read-only; never executes; no secret exposure) --
    def handle_status(self, conn, raw: dict) -> dict:
        if getattr(conn, "peer_uid", None) != 0:
            return {"status": "error", "phase": "auth", "reason": "peer_not_root"}
        try:
            req = StatusRequest.model_validate(raw)
        except ValidationError as e:
            return {"status": "error", "phase": "status", "reason": "schema", "detail": _v(e)}
        try:
            run = self.run_ledger.load(req.run_id)
        except LedgerCorruption:
            return {"status": "error", "phase": "status", "reason": "ledger_corrupt"}
        if run is None:
            return {"status": "not_found", "run_id": req.run_id}
        # STATUS is read-only: build the attempt ledger with the CURRENT epoch so
        # a prior-epoch dispatch_pending+marker DISPLAYS as delivery_unknown while
        # a live in-flight attempt stays dispatch_pending — ZERO provider ops.
        ledger = self.attempt_ledger_factory(req.run_id, self.epoch)
        try:
            state_map = ledger.reconciled_state_map()
        except LedgerCorruption:
            return {"status": "error", "phase": "status", "reason": "attempt_ledger_corrupt"}
        counts: dict[str, int] = {}
        for st in state_map.values():
            counts[st] = counts.get(st, 0) + 1
        # NB: never expose nonce / token / lease secrets.
        return {
            "status": "ok",
            "run_id": req.run_id,
            "run_state": run.get("state"),
            "attempts_recorded": len(state_map),
            "state_counts": counts,
        }

    # -- connection dispatch --
    def dispatch(self, conn, raw: dict) -> dict:
        op = raw.get("op") if isinstance(raw, dict) else None
        if op == "prepare":
            return self.handle_prepare(conn, raw)
        if op == "commit":
            return self.handle_commit(conn, raw)
        if op == "status":
            return self.handle_status(conn, raw)
        return {"status": "error", "reason": "unknown_op"}

    def handle_connection(self, conn) -> None:
        """Serve one connection. Peer identity is checked BEFORE any privileged
        content is parsed; a non-root peer is refused without reading a frame."""
        if getattr(conn, "peer_uid", None) != 0:
            _safe_send(conn, {"status": "error", "phase": "auth", "reason": "peer_not_root"})
            return
        while True:
            try:
                raw = conn.recv_json()
            except (EOFError, ConnectionError):
                return
            if raw is None:
                return
            if not isinstance(raw, dict):
                _safe_send(conn, {"status": "error", "reason": "malformed_frame"})
                return
            _safe_send(conn, self.dispatch(conn, raw))

    def forget_connection(self, conn) -> None:
        """Drop any prep token held for a closed connection (the token dies with
        the connection — a later connection cannot commit it)."""
        self._tokens.pop(getattr(conn, "id", None), None)


def _v(e: ValidationError) -> str:
    try:
        return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}:{err['type']}" for err in e.errors())[:400]
    except Exception:  # noqa: BLE001
        return "validation_error"


def _safe_send(conn, obj: dict) -> None:
    try:
        conn.send_json(obj)
    except Exception:  # noqa: BLE001 — a broken pipe must never crash the server
        pass


# ── SO_PEERCRED socket accept loop + stale-socket cleanup (Linux) ────────────


def peer_uid_of(sock_conn: socket.socket) -> int:
    """SO_PEERCRED uid of the connected peer (Linux). Raises on platforms without
    SO_PEERCRED so the caller fails closed rather than trusting an unknown peer."""
    so_peercred = getattr(socket, "SO_PEERCRED", None)
    if so_peercred is None:
        raise SocketSecurityError("SO_PEERCRED unavailable — refusing to trust peer")
    creds = sock_conn.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def cleanup_stale_socket(
    path: str, *, expect_uid: Optional[int] = None,
) -> None:
    """Remove a stale control socket, but ONLY if the path is an actual socket
    (``lstat`` — never follow a symlink) owned as expected. A symlink or a
    regular file at the socket path is a security anomaly → refuse to unlink and
    raise (fail closed)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return  # nothing there
    if stat.S_ISLNK(st.st_mode):
        raise SocketSecurityError(
            f"refusing to unlink symlink at socket path {path}"
        )
    if not stat.S_ISSOCK(st.st_mode):
        raise SocketSecurityError(
            f"refusing to unlink non-socket ({stat.filemode(st.st_mode)}) at {path}"
        )
    if expect_uid is not None and st.st_uid != expect_uid:
        raise SocketSecurityError(
            f"socket at {path} owned by uid {st.st_uid} != expected {expect_uid}"
        )
    os.unlink(path)


class _SocketConnection:
    """Adapts a connected AF_UNIX socket to the recv_json/send_json interface the
    server expects, with a bounded, deadlined, newline-delimited single frame per
    op."""

    _ids = 0
    _ids_lock = threading.Lock()

    def __init__(self, sock_conn: socket.socket, peer_uid: int) -> None:
        self._sock = sock_conn
        self.peer_uid = peer_uid
        with _SocketConnection._ids_lock:
            _SocketConnection._ids += 1
            self.id = f"conn_{_SocketConnection._ids}_{secrets.token_hex(4)}"
        self._buf = b""

    def recv_json(self) -> Optional[dict]:
        self._sock.settimeout(READ_DEADLINE_SEC)
        while b"\n" not in self._buf:
            if len(self._buf) > MAX_FRAME_BYTES:
                raise ProtocolError("frame exceeds bound before newline")
            chunk = self._sock.recv(4096)
            if not chunk:
                if not self._buf:
                    return None
                raise ProtocolError("connection closed mid-frame")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        if len(line) > MAX_FRAME_BYTES:
            raise ProtocolError("frame exceeds bound")
        try:
            return json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ProtocolError(f"bad json frame: {e}") from e

    def send_json(self, obj: dict) -> None:
        self._sock.settimeout(WRITE_DEADLINE_SEC)
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            data = (json.dumps({"status": "error", "reason": "response_too_large"}) + "\n").encode("utf-8")
        self._sock.sendall(data)


def bind_control_socket(path: str, *, uid: int, gid: int) -> socket.socket:
    """Bind the AF_UNIX control socket at ``path`` with ``0600`` perms and the
    expected owner. The parent RuntimeDirectory (``/run/shift-agent``) is created
    ``0700`` shift-agent:shift-agent by systemd; here we only own the socket
    node. Stale-socket cleanup is done first (lstat-guarded)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    cleanup_stale_socket(path, expect_uid=uid)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Bind then tighten: create with restrictive umask so the node is 0600.
    old_umask = os.umask(0o177)
    try:
        srv.bind(path)
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o600)
    try:
        os.chown(path, uid, gid)
    except (PermissionError, OSError):
        # In the gateway the process is already shift-agent:shift-agent, so the
        # node inherits the right owner; chown may be a no-op/denied — tolerate.
        pass
    srv.listen(4)
    return srv


def _run_accept_loop(
    server: EvidenceControlServer,
    srv: socket.socket,
    *,
    stop: Optional[threading.Event],
    path: str,
    uid: int,
) -> None:
    """The peer-authenticated accept loop over an ALREADY-BOUND socket. Every
    accepted connection is peer-checked (``SO_PEERCRED.uid == 0``) BEFORE any
    frame is parsed. On exit: disable the control plane, close the socket, clean
    up the stale node (the singleton lock is released by the caller AFTER this)."""
    srv.settimeout(1.0)
    try:
        while stop is None or not stop.is_set():
            try:
                sock_conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                try:
                    puid = peer_uid_of(sock_conn)
                except SocketSecurityError:
                    _reject_raw(sock_conn, "peer_unverifiable")
                    continue
                conn = _SocketConnection(sock_conn, puid)
                if puid != 0:
                    # Reject even UID-999 peers before parsing privileged content.
                    _safe_send(conn, {"status": "error", "phase": "auth", "reason": "peer_not_root"})
                    continue
                try:
                    server.handle_connection(conn)
                finally:
                    server.forget_connection(conn)
            finally:
                try:
                    sock_conn.close()
                except OSError:
                    pass
    finally:
        try:
            server.disable()
        except Exception:  # noqa: BLE001
            pass
        try:
            srv.close()
        finally:
            try:
                cleanup_stale_socket(path, expect_uid=uid)
            except SocketSecurityError:
                pass


def serve_forever(
    server: EvidenceControlServer,
    *,
    path: Optional[str] = None,
    uid: int = 999,
    gid: int = 999,
    stop: Optional[threading.Event] = None,
) -> None:
    """Default-OFF, blocking bind+accept. Returns IMMEDIATELY (no socket bound)
    while the feature flag is OFF. Refuses to bind before reconciliation completes.
    (The async D1 path uses ``bind_control_socket`` + ``_run_accept_loop`` on a
    thread instead of this blocking helper.)"""
    if not feature_enabled():
        return  # socket absent while OFF
    if not getattr(server, "reconciled", False):
        raise SocketSecurityError("refusing to bind control socket before reconciliation completes")
    path = path or socket_path()
    srv = bind_control_socket(path, uid=uid, gid=gid)
    _run_accept_loop(server, srv, stop=stop, path=path, uid=uid)


def _reject_raw(sock_conn: socket.socket, reason: str) -> None:
    try:
        sock_conn.sendall((json.dumps({"status": "error", "reason": reason}) + "\n").encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            sock_conn.close()
        except OSError:
            pass


# ── client side (CLI) ────────────────────────────────────────────────────────


class ClientConnection:
    """Client-side framed connection to the control socket: newline-delimited,
    bounded, deadlined single JSON frame per op. Symmetric with
    ``_SocketConnection`` on the server side."""

    def __init__(self, sock_conn: socket.socket) -> None:
        self._sock = sock_conn
        self._buf = b""

    def send_json(self, obj: dict) -> None:
        self._sock.settimeout(WRITE_DEADLINE_SEC)
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise ProtocolError("request frame exceeds bound")
        self._sock.sendall(data)

    def recv_json(self) -> Optional[dict]:
        self._sock.settimeout(READ_DEADLINE_SEC)
        while b"\n" not in self._buf:
            if len(self._buf) > MAX_FRAME_BYTES:
                raise ProtocolError("response frame exceeds bound before newline")
            chunk = self._sock.recv(4096)
            if not chunk:
                if not self._buf:
                    return None
                raise ProtocolError("connection closed mid-frame")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        try:
            return json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ProtocolError(f"bad json response: {e}") from e

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def connect_client(path: Optional[str] = None) -> ClientConnection:
    """Connect to the control socket (AF_UNIX). Raises OSError if the socket is
    absent (feature OFF) or unreachable — the CLI treats that as dependency-down."""
    path = path or socket_path()
    sock_conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_conn.settimeout(READ_DEADLINE_SEC)
    sock_conn.connect(path)
    return ClientConnection(sock_conn)


# ── strict-ordered startup, single-owner (AMENDMENT 2) ───────────────────────
#
# OWNERSHIP MODEL (single-owner, whole-lifetime hold):
#   * ONE process — the gateway process that wins the ``te-owner.lock`` singleton
#     flock — owns the ledger + the control socket + the (sole) provider send
#     path for the transport-evidence harness. That fd is opened ONCE, kept in a
#     long-lived owner (the start_gateway_harness frame + the module-level
#     _ACTIVE_SINGLETON_LOCK holder), is FD_CLOEXEC (never inherited by an exec'd
#     child), and is released ONLY at process exit.
#   * There is NO separate outbound-capable worker. The 28-segment diagnostic is
#     driven on the owning gateway process's own asyncio loop through the normal
#     _handle_message_with_agent -> _send_with_retry path — it does NOT open its
#     own adapter/bridge path. Any future worker MUST be subordinate to this
#     single ownership: it may only submit a provider op via the owner's loop
#     while the owner holds the lock, never by acquiring its own outbound path.
#   * A process that cannot acquire the lock performs NONE of: ledger init,
#     reconciliation, control-socket bind, inbound processing, provider submit.
#   * Shutdown: the control plane is DISABLED (serve teardown) before the socket
#     closes, and the lock is released only after — never released while still
#     outbound-capable.

# Long-lived holder so the lock fd is never garbage-collected/closed before the
# owning start_gateway_harness call returns (belt against an unexpected frame
# unwind during init/daemonization).
_ACTIVE_SINGLETON_LOCK: Optional[SingletonLock] = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_server_for(
    *,
    seams: PrereqSeams,
    state_dir_path: str,
    inject_diagnostic: Callable[["RunContext"], None],
    epoch: str,
    run_id_factory: Optional[Callable[[], str]] = None,
    logical_turn_id_factory: Optional[Callable[[], str]] = None,
) -> "EvidenceControlServer":
    """Construct the server bound to the durable ledgers under ``state_dir_path``."""
    sd = Path(state_dir_path)
    committed = CommittedNonceLedger(sd / "committed-nonces.ndjson")
    runs = RunLedger(sd)

    def _attempt_factory(run_id: str, ep: str) -> AttemptLedger:
        return AttemptLedger(sd / "attempts" / f"{run_id}.ndjson", ep)

    return EvidenceControlServer(
        seams=seams,
        committed_ledger=committed,
        run_ledger=runs,
        attempt_ledger_factory=_attempt_factory,
        inject_diagnostic=inject_diagnostic,
        epoch=epoch,
        run_id_factory=run_id_factory,
        logical_turn_id_factory=logical_turn_id_factory,
    )


def _te_stderr(msg: str) -> None:
    try:
        sys.stderr.write("shift-agent-transport-evidence: " + msg + "\n")
    except Exception:  # noqa: BLE001
        pass


def _acquire_and_prepare(
    sd: Path,
    *,
    seams: PrereqSeams,
    inject_diagnostic: Callable[["RunContext"], None],
    epoch: Optional[str],
    run_id_factory: Optional[Callable[[], str]],
    logical_turn_id_factory: Optional[Callable[[], str]],
) -> "Optional[tuple[SingletonLock, EvidenceControlServer, str]]":
    """Strict-order startup steps shared by the sync + async entry points:

      1. acquire the singleton lock (held for the ENTIRE outbound-capable
         lifetime — a competing process cannot pass this line, so it performs
         NONE of: ledger init, reconciliation, socket bind, inbound, provider);
      2. establish the current epoch (FAIL CLOSED on corrupt);
      3. load + validate the durable ledgers (corrupt → fail closed);
      4. reconcile prior-epoch records (NO outbound capability) + persist/fsync;
      5. build the server + mark reconciliation complete.

    Returns ``(lock, server, epoch)`` or ``None`` (all failure modes fail closed).
    The caller captures the loop + binds the socket AFTER this — never before."""
    lock = SingletonLock(sd / "te-owner.lock")
    try:
        lock.acquire()
    except SingletonLockError as e:
        _te_stderr("another owner holds the ledger; refusing to start (%s)" % (e,))
        return None
    try:
        try:
            ep = epoch or establish_process_epoch(sd)
            record_current_epoch(sd, ep)
        except EpochError as e:
            _te_stderr("epoch corrupt/ambiguous; refusing to start (%s)" % (e,))
            lock.release()
            return None
        try:
            committed = CommittedNonceLedger(sd / "committed-nonces.ndjson")
            committed.contains("__startup_validation_probe__")  # forces a parse
            run_ledger = RunLedger(sd)
            reconcile_ledger = ReconcileLedger(sd)

            def _attempt_factory(run_id: str, e2: str) -> AttemptLedger:
                return AttemptLedger(sd / "attempts" / f"{run_id}.ndjson", e2)

            reconcile_prior_epoch(
                run_ledger=run_ledger,
                attempt_ledger_factory=_attempt_factory,
                reconcile_ledger=reconcile_ledger,
                current_epoch=ep,
                ts=_iso_now(),
            )
        except LedgerCorruption as e:
            _te_stderr("ledger corrupt; refusing to start (%s)" % (e,))
            lock.release()
            return None

        server = build_server_for(
            seams=seams,
            state_dir_path=str(sd),
            inject_diagnostic=inject_diagnostic,
            epoch=ep,
            run_id_factory=run_id_factory,
            logical_turn_id_factory=logical_turn_id_factory,
        )
        server.mark_reconciled()
        return lock, server, ep
    except Exception:
        lock.release()
        raise


def start_gateway_harness(
    *,
    seams: PrereqSeams,
    inject_diagnostic: Callable[["RunContext"], None],
    state_dir_path: Optional[str] = None,
    socket_path_: Optional[str] = None,
    uid: int = 999,
    gid: int = 999,
    stop: Optional[threading.Event] = None,
    epoch: Optional[str] = None,
    serve_fn: Optional[Callable[..., None]] = None,
    run_id_factory: Optional[Callable[[], str]] = None,
    logical_turn_id_factory: Optional[Callable[[], str]] = None,
) -> None:
    """Synchronous, BLOCKING strict-order startup (test convenience + the
    single-owner proof). DEFAULT-OFF. The async D1 gateway-boot path is
    :func:`start_gateway_harness_async`."""
    if not feature_enabled():
        return  # default-OFF: no lock, no init, no socket.
    sd = Path(state_dir_path or state_dir())
    serve_fn = serve_fn or serve_forever
    global _ACTIVE_SINGLETON_LOCK
    res = _acquire_and_prepare(
        sd, seams=seams, inject_diagnostic=inject_diagnostic, epoch=epoch,
        run_id_factory=run_id_factory, logical_turn_id_factory=logical_turn_id_factory,
    )
    if res is None:
        return
    lock, server, _ep = res
    _ACTIVE_SINGLETON_LOCK = lock
    try:
        serve_fn(server, path=socket_path_, uid=uid, gid=gid, stop=stop)
    finally:
        _ACTIVE_SINGLETON_LOCK = None
        lock.release()


@dataclass
class HarnessHandle:
    lock: SingletonLock
    server: "EvidenceControlServer"
    sock: socket.socket
    stop: threading.Event
    thread: threading.Thread
    path: str
    uid: int


async def start_gateway_harness_async(
    *,
    seams: PrereqSeams,
    inject_diagnostic: Callable[["RunContext"], None],
    state_dir_path: Optional[str] = None,
    socket_path_: Optional[str] = None,
    uid: int = 999,
    gid: int = 999,
    epoch: Optional[str] = None,
    gateway_loop=None,
    bind_fn: Optional[Callable[..., socket.socket]] = None,
    accept_fn: Optional[Callable[..., None]] = None,
    run_id_factory: Optional[Callable[[], str]] = None,
    logical_turn_id_factory: Optional[Callable[[], str]] = None,
) -> "Optional[HarnessHandle]":
    """D1 explicit async gateway-boot hook. DEFAULT-OFF. Strict order, NON-blocking:

      singleton lock → epoch (fail closed) → load+validate → reconcile+fsync →
      **capture the running asyncio loop** → **bind+arm the control socket**
      (accept loop on a daemon thread) → ready.

    PREPARE/COMMIT are refused until reconciled AND the loop is captured; the
    socket is bound ONLY here, AFTER reconciliation completes durably — so a
    request before readiness is rejected (connection-refused / not-ready), never
    queued. Returns None (fail closed) if OFF / competing owner / corrupt epoch /
    corrupt ledger."""
    import asyncio as _asyncio

    if not feature_enabled():
        return None
    sd = Path(state_dir_path or state_dir())
    global _ACTIVE_SINGLETON_LOCK
    res = _acquire_and_prepare(
        sd, seams=seams, inject_diagnostic=inject_diagnostic, epoch=epoch,
        run_id_factory=run_id_factory, logical_turn_id_factory=logical_turn_id_factory,
    )
    if res is None:
        return None
    lock, server, _ep = res
    try:
        loop = gateway_loop or _asyncio.get_running_loop()  # capture running loop
        server.set_loop(loop)
        path = socket_path_ or socket_path()
        _bind = bind_fn or bind_control_socket
        _accept = accept_fn or _run_accept_loop
        srv = _bind(path, uid=uid, gid=gid)                 # bind+arm AFTER reconcile
        stop = threading.Event()
        thread = threading.Thread(
            target=_accept, args=(server, srv),
            kwargs={"stop": stop, "path": path, "uid": uid},
            name="shift-te-control", daemon=True,
        )
        thread.start()
        _ACTIVE_SINGLETON_LOCK = lock
        return HarnessHandle(
            lock=lock, server=server, sock=srv, stop=stop, thread=thread,
            path=path, uid=uid,
        )
    except Exception:
        lock.release()
        raise


async def stop_gateway_harness_async(handle: "Optional[HarnessHandle]") -> None:
    """D1 shutdown ordering: DISABLE the control plane (PREPARE/COMMIT refuse)
    BEFORE inbound/provider capability, close the socket, THEN release the
    singleton lock — never released while still outbound-capable."""
    global _ACTIVE_SINGLETON_LOCK
    if handle is None:
        return
    try:
        handle.server.disable()  # (1) disable control plane FIRST
    except Exception:  # noqa: BLE001
        pass
    handle.stop.set()  # (2) signal the accept loop to stop
    try:
        handle.sock.close()  # unblock accept() immediately
    except OSError:
        pass
    try:
        handle.thread.join(timeout=5.0)  # accept loop's finally closes + cleans up
    except Exception:  # noqa: BLE001
        pass
    _ACTIVE_SINGLETON_LOCK = None
    handle.lock.release()  # (3) release the lock LAST
