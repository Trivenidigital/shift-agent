"""Durable ledgers for the governed transport-budget evidence-enablement harness.

Three append-durable stores, all fsync-hardened and corruption-fail-closed:

  * ``AttemptLedger``          — per-attempt transport state machine. Each attempt
    record carries ``{outcome, provider_entry_started_at, provider_entry_attempt_id}``
    with ``outcome`` in ``not_started`` | ``dispatch_pending`` | ``sent`` |
    ``denied`` | ``dispatch_failed`` | ``delivery_unknown_after_send``.

    A crashed process cannot itself write ``delivery_unknown_after_send`` — that
    state is DERIVED at restart from the durable **provider-entry marker**
    (correction #2). Live ordering (each step durable + fsync):

      1. persist ``outcome=dispatch_pending`` (provider-entry markers null);
      2. IMMEDIATELY before the real provider call, durably record
         ``provider_entry_started_at`` + ``provider_entry_attempt_id`` (fsync);
      3. enter provider submission;
      4. provider rejects/fails before acceptance   -> ``dispatch_failed``;
      5. provider returns acceptance / message id    -> ``sent``;
      6. budget denies at the gate (before provider entry) -> ``denied``
         (no provider-entry marker).

    Restart reconciliation (NO automatic retransmission in ANY case):
      * stale ``dispatch_pending`` with **no** provider-entry marker  -> remains
        ``dispatch_pending`` (crashed before provider entry): reserved +
        non-retransmittable;
      * stale ``dispatch_pending`` **with** a provider-entry marker    ->
        ``delivery_unknown_after_send`` (crashed after provider entry, before
        ack): non-retransmittable.
    Restart / STATUS never retransmit ``dispatch_pending`` | ``sent`` |
    ``delivery_unknown_after_send``. There is no auto-continuation after a crash.
  * ``CommittedNonceLedger``   — the durable committed-nonce ledger. A COMMIT
    writes the nonce here (fsync) BEFORE the first send; the nonce is rejected
    forever, so a replayed / re-committed lease can never run twice.
  * ``RunLedger``              — the durable run record written at COMMIT
    ({run_id, nonce, destination, logical_turn_id, authorized_commit,
    harness_hash, state}); read-only by ``STATUS``.

Design notes
------------
* Self-contained (no ``fcntl`` / ``safe_io`` import at module load) so it imports
  + runs cross-platform in-process tests. On Linux it opportunistically takes an
  ``fcntl.flock`` around a critical append; where ``fcntl`` is absent the
  atomic-replace / append+fsync semantics still hold for the single-writer
  gateway.
* Every mutating write is ``fsync``'d and, for atomic-replace writes, the parent
  directory is ``fsync``'d after ``rename`` so the entry survives power loss.
* Any corruption (an unparseable line, an unknown outcome, a truncated record)
  raises ``LedgerCorruption`` — the caller HALTS. Evidence capture never
  silently continues on a damaged ledger.

Repository implementation only — default-OFF, never executed on the box by this
change. See tasks/audits/catering-review-part1-2026-07-26/
transport-evidence-harness-frozen-spec.md for the full contract.
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional

try:  # opportunistic advisory lock; absent on Windows test hosts
    import fcntl as _fcntl
except Exception:  # noqa: BLE001
    _fcntl = None  # type: ignore

# The singleton lock's CORRECTNESS depends on a real ``flock``, so its strategy is
# gated on the actual platform — NOT merely on ``_fcntl`` being importable. Some
# test suites install a NO-OP ``fcntl`` stub into ``sys.modules`` (``flock`` does
# nothing) to import safe_io off-Linux; on Windows that stub would make a flock
# "succeed" without ever locking. On real POSIX (the box + Linux CI) ``fcntl`` is
# always the genuine module; on Windows we always use ``O_CREAT|O_EXCL`` instead.
_SINGLETON_USES_FLOCK = (os.name == "posix") and (_fcntl is not None)

# A per-process-import random tiebreaker so the computed epoch is unique across
# restarts even where /proc is unavailable (test hosts). Fixed for the lifetime
# of one process → compute_process_epoch() is stable within a process.
_MODULE_NONCE = secrets.token_hex(4)


# ── attempt outcomes ────────────────────────────────────────────────────────
OUTCOME_NOT_STARTED = "not_started"
OUTCOME_DISPATCH_PENDING = "dispatch_pending"
OUTCOME_SENT = "sent"
OUTCOME_DENIED = "denied"
OUTCOME_DISPATCH_FAILED = "dispatch_failed"
OUTCOME_DELIVERY_UNKNOWN = "delivery_unknown_after_send"

# Outcomes a live process is allowed to WRITE (delivery_unknown is derived only).
WRITABLE_OUTCOMES = frozenset(
    {
        OUTCOME_DISPATCH_PENDING,
        OUTCOME_SENT,
        OUTCOME_DENIED,
        OUTCOME_DISPATCH_FAILED,
    }
)

# All outcomes that may appear as a RECONCILED state.
RECONCILED_OUTCOMES = frozenset(
    {
        OUTCOME_NOT_STARTED,
        OUTCOME_DISPATCH_PENDING,
        OUTCOME_SENT,
        OUTCOME_DENIED,
        OUTCOME_DISPATCH_FAILED,
        OUTCOME_DELIVERY_UNKNOWN,
    }
)

# A reconciled attempt in one of these states MUST NEVER be retransmitted after a
# restart (the provider may already hold it → a resend would duplicate).
NON_RETRANSMITTABLE = frozenset(
    {OUTCOME_DISPATCH_PENDING, OUTCOME_SENT, OUTCOME_DELIVERY_UNKNOWN}
)

# Terminal states a run oracle treats as "attempt closed".
TERMINAL_STATES = frozenset(
    {
        OUTCOME_SENT,
        OUTCOME_DENIED,
        OUTCOME_DISPATCH_FAILED,
        OUTCOME_DELIVERY_UNKNOWN,
    }
)


class LedgerCorruption(RuntimeError):
    """A durable ledger could not be parsed / validated → fail closed (HALT)."""


class LedgerError(RuntimeError):
    """A ledger write could not be completed durably → fail closed (HALT)."""


# ── process epoch (AMENDMENT 1 trigger + AMENDMENT 2 identity) ────────────────

_DEFAULT_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
_DEFAULT_STAT_PATH = "/proc/self/stat"


class EpochError(RuntimeError):
    """The process epoch is corrupt / ambiguous → FAIL CLOSED (amendment 2). A
    gateway that cannot establish an unambiguous epoch must NOT enable the
    control socket / process any run."""


def read_kernel_epoch(
    boot_id_path: str = _DEFAULT_BOOT_ID_PATH,
    stat_path: str = _DEFAULT_STAT_PATH,
) -> Optional[str]:
    """Kernel-grounded epoch ``kern:<boot_id>:<pid>:<starttime>`` (amendment 2).

    ``boot_id`` survives PID reuse across boots; ``/proc/self/stat`` field 22
    (``starttime``, ticks since boot) is fixed for a process's lifetime and
    differs after a restart. NEVER a wall-clock timestamp alone.

    Returns None when /proc is simply ABSENT (non-Linux) so the caller can fall
    back to a durable random UUID. Raises ``EpochError`` when the data is present
    but CORRUPT / AMBIGUOUS (empty boot_id, unparseable starttime, or exactly one
    of the two sources present) — fail closed, never guess."""
    boot_id: Optional[str] = None
    try:
        raw = Path(boot_id_path).read_text(encoding="utf-8").strip()
        if not raw:
            raise EpochError(f"boot_id at {boot_id_path} is empty (ambiguous epoch)")
        boot_id = raw
    except FileNotFoundError:
        boot_id = None
    except OSError as e:
        raise EpochError(f"boot_id at {boot_id_path} unreadable: {e}") from e

    starttime: Optional[str] = None
    try:
        after = Path(stat_path).read_text(encoding="utf-8").rsplit(")", 1)[-1]
        tokens = after.split()
        # field 22 overall = index 19 among tokens after the final ')'.
        val = tokens[19]
        int(val)  # must parse as an integer tick count
        starttime = val
    except FileNotFoundError:
        starttime = None
    except (OSError, IndexError, ValueError) as e:
        raise EpochError(f"starttime at {stat_path} corrupt: {e}") from e

    if boot_id is None and starttime is None:
        return None  # /proc absent → caller uses the durable-UUID path
    if boot_id is None or starttime is None:
        raise EpochError("kernel epoch sources are inconsistent (one present, one absent)")
    return f"kern:{boot_id}:{os.getpid()}:{starttime}"


def compute_process_epoch(
    boot_id_path: str = _DEFAULT_BOOT_ID_PATH,
    stat_path: str = _DEFAULT_STAT_PATH,
) -> str:
    """Kernel-grounded epoch, FAIL CLOSED. Raises ``EpochError`` if /proc is
    absent (non-Linux) — callers that must run cross-platform use
    :func:`establish_process_epoch` which adds the durable-UUID fallback."""
    ke = read_kernel_epoch(boot_id_path, stat_path)
    if ke is None:
        raise EpochError("kernel epoch unavailable (/proc absent) — use establish_process_epoch")
    return ke


def establish_process_epoch(
    state_dir: Optional[Path] = None,
    *,
    boot_id_path: str = _DEFAULT_BOOT_ID_PATH,
    stat_path: str = _DEFAULT_STAT_PATH,
) -> str:
    """Establish + durably record THIS process's epoch at startup (amendment 2).

    Prefers the kernel-grounded epoch; where /proc is absent, generates a random
    UUID epoch ``uuid:<hex>`` (never a wall-clock). CORRUPT kernel data fails
    closed (``EpochError``). When ``state_dir`` is given the established epoch is
    written + fsync'd to ``current-epoch.json`` for audit + a consistent
    same-process read."""
    ke = read_kernel_epoch(boot_id_path, stat_path)  # raises EpochError if corrupt
    epoch = ke if ke is not None else "uuid:" + secrets.token_hex(16)
    if state_dir is not None:
        record_current_epoch(Path(state_dir), epoch)
    return epoch


def record_current_epoch(state_dir: Path, epoch: str) -> None:
    """Durably record the established epoch (fsync) for audit + a consistent
    same-process read. Called at startup regardless of how the epoch was
    established (kernel / UUID / injected)."""
    _atomic_write_json_fsync(
        Path(state_dir) / "current-epoch.json",
        {"epoch": epoch, "pid": os.getpid(), "module_nonce": _MODULE_NONCE},
    )


# ── singleton ownership lock (AMENDMENT 2 — single-owner) ────────────────────


class SingletonLockError(RuntimeError):
    """Another gateway process already owns the ledger → this process must NOT
    initialize the ledger, reconcile, bind the control socket, accept inbound, or
    submit any provider op. Fail closed."""


# Fork-safety (final-head): FD_CLOEXEC drops the lock fd across ``exec`` but NOT
# across a plain ``fork`` — a forked child inherits the open lock fd and (because
# ``flock`` is on the shared open-file description) would keep the lock alive after
# the parent exits, blocking a legitimate restart. We register an
# after-fork-in-child handler that closes every held lock fd in the child, so a
# fork-only child never retains singleton ownership.
_OPEN_LOCK_FDS: set[int] = set()
_AFTER_FORK_REGISTERED = False


def _close_lock_fds_in_child() -> None:
    for fd in list(_OPEN_LOCK_FDS):
        with contextlib.suppress(OSError):
            os.close(fd)
    _OPEN_LOCK_FDS.clear()


def _ensure_after_fork_registered() -> None:
    global _AFTER_FORK_REGISTERED
    if _AFTER_FORK_REGISTERED:
        return
    reg = getattr(os, "register_at_fork", None)  # POSIX only; absent on Windows
    if reg is not None:
        reg(after_in_child=_close_lock_fds_in_child)
    _AFTER_FORK_REGISTERED = True


class SingletonLock:
    """Persistent EXCLUSIVE hold on the ledger, kept for the gateway's ENTIRE
    outbound-capable lifetime (the fd stays open; the lock releases only on
    process exit / explicit release). All five capabilities — ledger init,
    reconciliation, control-socket bind, inbound processing, provider submission
    — are gated behind holding this lock, so a competing process that cannot
    acquire it performs NONE of them.

    Linux uses ``flock(LOCK_EX|LOCK_NB)`` (auto-released by the kernel on process
    death — incl. ``kill -9`` — when the fd is dropped, so a legitimate restart
    recovers with no stale lock); hosts without ``fcntl`` (test hosts) use
    ``O_CREAT|O_EXCL`` create-and-hold.

    Lifecycle invariants (final-head):
      * the fd is opened ONCE and kept in this long-lived owner — never GC'd /
        closed until ``release`` at process exit;
      * ``FD_CLOEXEC`` (non-inheritable) is set so an ``exec``'d child does NOT
        inherit the lock (an inherited fd would keep the lock alive past the
        gateway's exit and block a legitimate restart)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    @property
    def close_on_exec(self) -> bool:
        """True iff the lock fd is non-inheritable (FD_CLOEXEC set)."""
        return self._fd is not None and not os.get_inheritable(self._fd)

    def acquire(self) -> "SingletonLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if _SINGLETON_USES_FLOCK:
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError as e:
                os.close(fd)
                raise SingletonLockError(
                    f"another owner holds {self.path}: {e}"
                ) from e
            self._fd = fd
        else:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except (FileExistsError, PermissionError) as e:
                raise SingletonLockError(f"another owner holds {self.path}") from e
            self._fd = fd
        # FD_CLOEXEC — an exec'd child must NOT inherit the lock. Python opens fds
        # non-inheritable by default (PEP 446); set it explicitly + assert it held.
        os.set_inheritable(self._fd, False)
        # Fork-safety: track the fd so the after-fork-in-child handler closes it in
        # any forked child (fork inherits the fd despite FD_CLOEXEC).
        _ensure_after_fork_registered()
        _OPEN_LOCK_FDS.add(self._fd)
        with contextlib.suppress(OSError):
            os.ftruncate(self._fd, 0)
            os.write(self._fd, str(os.getpid()).encode("ascii"))
            os.fsync(self._fd)
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        _OPEN_LOCK_FDS.discard(self._fd)
        if _SINGLETON_USES_FLOCK:
            with contextlib.suppress(OSError):
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(self._fd)
        self._fd = None
        if not _SINGLETON_USES_FLOCK:
            # O_EXCL path: the file IS the lock → remove it so a legit restart
            # can re-acquire (the flock path leaves the file; the lock is on the fd).
            with contextlib.suppress(OSError):
                os.unlink(str(self.path))

    def __enter__(self) -> "SingletonLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


# ── low-level durable primitives (no fcntl / safe_io dependency) ─────────────


def _fsync_dir(dir_path: Path) -> None:
    """fsync a directory so a create / rename within it is durable (Linux box).

    Best-effort: Windows cannot open a directory handle for fsync, and some
    filesystems disallow directory fsync — the preceding file fsync already ran,
    so a failure here is not fatal."""
    if os.name == "nt":
        return
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _append_line_fsync(path: Path, line: str) -> None:
    """Append one newline-terminated line and fsync the file descriptor.

    Uses a short-lived ``fcntl.flock`` on Linux so a second writer cannot
    interleave a partial line; on hosts without ``fcntl`` the single-writer
    gateway invariant makes the lock unnecessary.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = line if line.endswith("\n") else line + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        if _fcntl is not None:
            with contextlib.suppress(Exception):
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)
    _fsync_dir(path.parent)


def _atomic_write_json_fsync(path: Path, obj: object, mode: int = 0o600) -> None:
    """Write JSON to a temp file, fsync it, atomically rename, fsync the dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
        _fsync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


# ── committed-nonce ledger ───────────────────────────────────────────────────


class CommittedNonceLedger:
    """Append-durable set of committed nonces. Once a nonce is written it is
    rejected forever — the trust anchor that makes a replayed / re-committed
    lease unable to run a second diagnostic."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        out: set[str] = set()
        try:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                row = json.loads(raw)
                nonce = row["nonce"]
                if not isinstance(nonce, str) or not nonce:
                    raise ValueError("empty nonce")
                out.add(nonce)
        except (ValueError, KeyError, TypeError) as e:
            raise LedgerCorruption(
                f"committed-nonce ledger corrupt at {self.path}: {e}"
            ) from e
        return out

    def contains(self, nonce: str) -> bool:
        return nonce in self._load()

    def record(self, nonce: str, run_id: str, ts: str) -> None:
        """Append a committed nonce (fsync). Idempotency / race exclusion is the
        caller's responsibility (COMMIT holds the server lock + checks
        ``contains`` first); this is the durable write."""
        if not nonce:
            raise LedgerError("refusing to record empty nonce")
        _append_line_fsync(
            self.path,
            json.dumps(
                {"nonce": nonce, "run_id": run_id, "ts": ts},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )


# ── run ledger ───────────────────────────────────────────────────────────────


class RunLedger:
    """Durable per-run record written at COMMIT (before the first send) and read
    by STATUS. One JSON file per run_id under ``runs/``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _run_path(self, run_id: str) -> Path:
        safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
        if not safe or safe != run_id:
            raise LedgerError(f"illegal run_id {run_id!r}")
        return self.root / "runs" / f"{safe}.json"

    def record_committed(
        self,
        *,
        run_id: str,
        nonce: str,
        destination: str,
        logical_turn_id: str,
        authorized_commit: str,
        harness_hash: str,
        epoch: str,
        ts: str,
    ) -> None:
        _atomic_write_json_fsync(
            self._run_path(run_id),
            {
                "run_id": run_id,
                "nonce": nonce,
                "destination": destination,
                "logical_turn_id": logical_turn_id,
                "authorized_commit": authorized_commit,
                "harness_hash": harness_hash,
                "epoch": epoch,
                "state": "committed",
                "ts": ts,
            },
        )

    def load(self, run_id: str) -> Optional[dict]:
        p = self._run_path(run_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise LedgerCorruption(f"run record corrupt at {p}: {e}") from e

    def list_run_ids(self) -> list[str]:
        d = self.root / "runs"
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.json"))


# ── reconciliation snapshot store — DERIVED CACHE ONLY (AMENDMENT 2 / D2) ─────


class ReconcileLedger:
    """Derived per-run reconciliation snapshot / evidence export. NOT canonical:
    the authoritative source of reconciled state is the append-only attempt
    ledger + its reconciliation-transition rows (D2). This snapshot is a cache;
    on mismatch with the authoritative ledger it MUST be treated as corruption →
    fail closed (see :func:`assert_snapshot_consistent`)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, run_id: str) -> Path:
        safe = "".join(c for c in run_id if c.isalnum() or c in "-_")
        if not safe or safe != run_id:
            raise LedgerError(f"illegal run_id {run_id!r}")
        return self.root / "reconciled" / f"{safe}.json"

    def record_snapshot(
        self, run_id: str, reconciling_epoch: str, states: dict, ts: str
    ) -> None:
        _atomic_write_json_fsync(
            self._path(run_id),
            {
                "run_id": run_id,
                "reconciling_epoch": reconciling_epoch,
                "states": {str(k): v for k, v in states.items()},
                "ts": ts,
                "note": "DERIVED CACHE — authoritative source is the attempt ledger + transitions",
            },
        )

    def load(self, run_id: str) -> Optional[dict]:
        p = self._path(run_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise LedgerCorruption(f"reconcile snapshot corrupt at {p}: {e}") from e


def assert_snapshot_consistent(
    run_id: str,
    ledger: "AttemptLedger",
    reconcile_ledger: ReconcileLedger,
    current_epoch: Optional[str] = None,
) -> None:
    """D2 fail-closed: if a derived snapshot exists and DISAGREES with the
    authoritative attempt ledger + transitions, raise ``LedgerCorruption``. A
    missing snapshot is fine (the ledger is authoritative)."""
    snap = reconcile_ledger.load(run_id)
    if snap is None:
        return
    authoritative = {
        str(k): v for k, v in ledger.reconciled_state_map(current_epoch).items()
    }
    cached = dict(snap.get("states", {}))
    if cached != authoritative:
        raise LedgerCorruption(
            f"reconcile snapshot for {run_id} disagrees with authoritative ledger "
            f"(cache={cached} authoritative={authoritative})"
        )


def reconcile_prior_epoch(
    *,
    run_ledger: RunLedger,
    attempt_ledger_factory,
    reconcile_ledger: ReconcileLedger,
    current_epoch: str,
    ts: str = "",
) -> dict:
    """Startup reconciliation over PRIOR-epoch records (amendment 1 + 2 + D2).

    For each prior-epoch stale ``dispatch_pending`` attempt, append a durable
    reconciliation-TRANSITION event to the AUTHORITATIVE attempt ledger
    (``{attempt_id, prior_outcome, provider_entry_marker_present,
    reconciled_outcome, reconciled_from_epoch, reconciled_by_epoch,
    reconciled_at}``): a provider-entry marker present → ``delivery_unknown_after_send``;
    unmarked → an equivalent event retaining the effective outcome as reserved
    ``dispatch_pending``. Idempotent (skips attempts already carrying a transition).
    Then writes the DERIVED snapshot cache.

    STRUCTURAL NO-OUTBOUND GUARANTEE: no send / provider / bridge / dispatch seam
    in the signature → structurally incapable of an outbound op or resuming a
    sequence. A corrupt ledger raises ``LedgerCorruption`` → caller fails closed."""
    summary = {"runs": 0, "reconciled": {}, "transitions": 0}
    for run_id in run_ledger.list_run_ids():
        ledger = attempt_ledger_factory(run_id, current_epoch)
        for attempt in sorted(ledger.attempts_seen()):
            if ledger.has_reconciliation(attempt):
                continue  # already reconciled on a prior boot — idempotent
            rows = ledger._attempt_rows_for(attempt)
            last = rows[-1]
            if last["outcome"] != OUTCOME_DISPATCH_PENDING:
                continue  # terminal outcome — no transition needed
            if last["epoch"] == current_epoch:
                continue  # own epoch — an active attempt is never reconciled
            marker = ledger.has_provider_entry_marker(attempt)
            reconciled = OUTCOME_DELIVERY_UNKNOWN if marker else OUTCOME_DISPATCH_PENDING
            ledger.append_reconciliation_transition(
                attempt=attempt,
                prior_outcome=last["outcome"],
                provider_entry_marker_present=marker,
                reconciled_outcome=reconciled,
                reconciled_from_epoch=last["epoch"],
                reconciled_by_epoch=current_epoch,
                reconciled_at=ts,
            )
            summary["transitions"] += 1
        states = ledger.reconciled_state_map(current_epoch)
        reconcile_ledger.record_snapshot(run_id, current_epoch, states, ts)
        summary["runs"] += 1
        summary["reconciled"][run_id] = states
    return summary


# ── per-attempt ledger ───────────────────────────────────────────────────────


class AttemptLedger:
    """Append-durable per-attempt state machine for ONE run.

    Each transition is a fsync'd NDJSON row carrying the correction-#2 record
    shape ``{attempt, outcome, provider_entry_started_at, provider_entry_attempt_id,
    provider_id?, epoch, ts}``. The current reconciled state of an attempt derives
    from the union of its rows: the last written ``outcome`` plus whether ANY row
    for that attempt recorded a provider-entry marker — BUT (amendment 1) a
    ``dispatch_pending`` row bearing the CURRENT process epoch is never
    reclassified; only prior-(dead)-epoch rows reconcile to
    ``delivery_unknown_after_send``.

    ``epoch`` is this process's :func:`compute_process_epoch` value stamped on
    every write; reads default to ``self.epoch`` as the "current process".
    """

    def __init__(self, path: Path, epoch: str) -> None:
        self.path = Path(path)
        self.epoch = epoch

    # -- read side --
    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        try:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                row = json.loads(raw)
                kind = row.get("record", "attempt")
                idx = row["attempt"]
                if not isinstance(idx, int) or idx < 1:
                    raise ValueError(f"bad attempt index {idx!r}")
                if kind == "attempt":
                    outcome = row["outcome"]
                    if outcome not in WRITABLE_OUTCOMES:
                        # not_started / delivery_unknown are never WRITTEN as a
                        # live outcome — a row bearing one is corruption.
                        raise ValueError(f"non-writable outcome persisted: {outcome!r}")
                    if "provider_entry_started_at" not in row:
                        raise ValueError("missing provider_entry_started_at field")
                    if "provider_entry_attempt_id" not in row:
                        raise ValueError("missing provider_entry_attempt_id field")
                    if not isinstance(row.get("epoch"), str) or not row["epoch"]:
                        raise ValueError("missing/invalid epoch field")
                elif kind == "reconciliation":
                    # D2 reconciliation-transition row in the authoritative ledger.
                    if row.get("reconciled_outcome") not in RECONCILED_OUTCOMES:
                        raise ValueError(
                            f"bad reconciled_outcome {row.get('reconciled_outcome')!r}"
                        )
                    for f in (
                        "prior_outcome", "provider_entry_marker_present",
                        "reconciled_from_epoch", "reconciled_by_epoch", "reconciled_at",
                    ):
                        if f not in row:
                            raise ValueError(f"reconciliation row missing {f!r}")
                    if not isinstance(row["reconciled_by_epoch"], str) or not row["reconciled_by_epoch"]:
                        raise ValueError("reconciliation row bad reconciled_by_epoch")
                else:
                    raise ValueError(f"unknown ledger record kind {kind!r}")
                rows.append(row)
        except (ValueError, KeyError, TypeError) as e:
            raise LedgerCorruption(
                f"attempt ledger corrupt at {self.path}: {e}"
            ) from e
        return rows

    def _attempt_rows(self) -> list[dict]:
        return [r for r in self._rows() if r.get("record", "attempt") == "attempt"]

    def _recon_rows(self) -> list[dict]:
        return [r for r in self._rows() if r.get("record") == "reconciliation"]

    def _attempt_rows_for(self, attempt: int) -> list[dict]:
        return [r for r in self._attempt_rows() if r["attempt"] == attempt]

    def _recon_rows_for(self, attempt: int) -> list[dict]:
        return [r for r in self._recon_rows() if r["attempt"] == attempt]

    def attempts_seen(self) -> set[int]:
        return {r["attempt"] for r in self._attempt_rows()}

    def last_written_outcome(self, attempt: int) -> str:
        rows = self._attempt_rows_for(attempt)
        if not rows:
            return OUTCOME_NOT_STARTED
        return rows[-1]["outcome"]

    def has_provider_entry_marker(self, attempt: int) -> bool:
        return any(
            r.get("provider_entry_started_at") for r in self._attempt_rows_for(attempt)
        )

    def has_reconciliation(self, attempt: int) -> bool:
        return bool(self._recon_rows_for(attempt))

    def reconciled_state(
        self, attempt: int, current_epoch: Optional[str] = None
    ) -> str:
        """Effective state resolved from the AUTHORITATIVE ledger (D2): a
        durable reconciliation-transition row wins; otherwise correction-#2 /
        amendment-1 derivation.

        ``delivery_unknown_after_send`` is DERIVED from a durable provider-entry
        marker — never written as a live outcome. A ``dispatch_pending`` row whose
        epoch equals ``current_epoch`` (default: this process's own epoch) is NEVER
        reclassified (an actively-executing attempt); only a PRIOR (dead) epoch
        derives, and startup persists that derivation as a reconciliation
        transition so subsequent reads resolve directly from the ledger."""
        recon = self._recon_rows_for(attempt)
        if recon:
            return recon[-1]["reconciled_outcome"]  # authoritative transition wins
        current = current_epoch if current_epoch is not None else self.epoch
        rows = self._attempt_rows_for(attempt)
        if not rows:
            return OUTCOME_NOT_STARTED
        last = rows[-1]
        last_outcome = last["outcome"]
        if last_outcome != OUTCOME_DISPATCH_PENDING:
            return last_outcome  # sent / denied / dispatch_failed are terminal
        # dispatch_pending: reconcile ONLY for a prior (dead) epoch.
        if last["epoch"] == current:
            return OUTCOME_DISPATCH_PENDING  # live, own epoch — never reclassify
        if self.has_provider_entry_marker(attempt):
            return OUTCOME_DELIVERY_UNKNOWN  # crashed after provider entry
        return OUTCOME_DISPATCH_PENDING  # crashed before provider entry: reserved

    def reconciled_state_map(
        self, current_epoch: Optional[str] = None
    ) -> dict[int, str]:
        return {
            a: self.reconciled_state(a, current_epoch)
            for a in sorted(self.attempts_seen())
        }

    def retransmit_forbidden(
        self, attempt: int, current_epoch: Optional[str] = None
    ) -> bool:
        """True when this attempt must NEVER be resubmitted to the provider —
        the core no-duplicate-provider-submission guard."""
        return self.reconciled_state(attempt, current_epoch) in NON_RETRANSMITTABLE

    def started(self) -> bool:
        """True if any attempt has left ``not_started`` — used to refuse
        auto-continuation of a run after a crash (there is no resume)."""
        return bool(self.attempts_seen())

    def provider_ids(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for row in self._attempt_rows():
            if row["outcome"] == OUTCOME_SENT and row.get("provider_id"):
                out[row["attempt"]] = row["provider_id"]
        return out

    # -- write side (the six live transitions of §29-34) --
    def _append(
        self,
        attempt: int,
        outcome: str,
        *,
        provider_entry_started_at: Optional[str],
        provider_entry_attempt_id: Optional[str],
        provider_id: Optional[str],
        ts: str,
    ) -> None:
        if outcome not in WRITABLE_OUTCOMES:
            raise LedgerError(f"illegal (non-writable) outcome {outcome!r}")
        if attempt < 1:
            raise LedgerError(f"illegal attempt index {attempt!r}")
        _append_line_fsync(
            self.path,
            json.dumps(
                {
                    "record": "attempt",
                    "attempt": attempt,
                    "outcome": outcome,
                    "provider_entry_started_at": provider_entry_started_at,
                    "provider_entry_attempt_id": provider_entry_attempt_id,
                    "provider_id": provider_id,
                    "epoch": self.epoch,
                    "ts": ts,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def append_reconciliation_transition(
        self,
        *,
        attempt: int,
        prior_outcome: str,
        provider_entry_marker_present: bool,
        reconciled_outcome: str,
        reconciled_from_epoch: str,
        reconciled_by_epoch: str,
        reconciled_at: str,
    ) -> None:
        """D2: append a durable reconciliation-transition event to the SAME
        authoritative append-only ledger. Live dispatch never fabricates
        ``delivery_unknown_after_send``; only startup reconciliation writes it here
        (derived from a prior-epoch provider-entry marker)."""
        if reconciled_outcome not in RECONCILED_OUTCOMES:
            raise LedgerError(f"illegal reconciled_outcome {reconciled_outcome!r}")
        if attempt < 1:
            raise LedgerError(f"illegal attempt index {attempt!r}")
        _append_line_fsync(
            self.path,
            json.dumps(
                {
                    "record": "reconciliation",
                    "attempt": attempt,
                    "prior_outcome": prior_outcome,
                    "provider_entry_marker_present": bool(provider_entry_marker_present),
                    "reconciled_outcome": reconciled_outcome,
                    "reconciled_from_epoch": reconciled_from_epoch,
                    "reconciled_by_epoch": reconciled_by_epoch,
                    "reconciled_at": reconciled_at,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def mark_dispatch_pending(self, attempt: int, ts: str = "") -> None:
        """Step 1: reserve the attempt. Provider-entry markers null."""
        self._append(
            attempt,
            OUTCOME_DISPATCH_PENDING,
            provider_entry_started_at=None,
            provider_entry_attempt_id=None,
            provider_id=None,
            ts=ts,
        )

    def mark_provider_entry(
        self, attempt: int, provider_entry_attempt_id: str, ts: str = ""
    ) -> None:
        """Step 2: the durable provider-entry marker written IMMEDIATELY before
        the real provider call. Outcome stays ``dispatch_pending`` but the marker
        is now present, so a crash from here on reconciles to
        ``delivery_unknown_after_send``."""
        if not provider_entry_attempt_id:
            raise LedgerError("provider_entry_attempt_id required for marker")
        self._append(
            attempt,
            OUTCOME_DISPATCH_PENDING,
            provider_entry_started_at=(ts or "unknown"),
            provider_entry_attempt_id=provider_entry_attempt_id,
            provider_id=None,
            ts=ts,
        )

    def mark_sent(self, attempt: int, provider_id: str, ts: str = "") -> None:
        """Step 5: provider returned acceptance / message id."""
        if not provider_id:
            raise LedgerError("provider_id required to mark sent")
        self._append(
            attempt,
            OUTCOME_SENT,
            provider_entry_started_at=None,
            provider_entry_attempt_id=None,
            provider_id=provider_id,
            ts=ts,
        )

    def mark_denied(self, attempt: int, ts: str = "") -> None:
        """Step 6: budget denied at the gate, before provider entry."""
        self._append(
            attempt,
            OUTCOME_DENIED,
            provider_entry_started_at=None,
            provider_entry_attempt_id=None,
            provider_id=None,
            ts=ts,
        )

    def mark_dispatch_failed(self, attempt: int, ts: str = "") -> None:
        """Step 4: provider rejected / failed before acceptance (certain no
        delivery)."""
        self._append(
            attempt,
            OUTCOME_DISPATCH_FAILED,
            provider_entry_started_at=None,
            provider_entry_attempt_id=None,
            provider_id=None,
            ts=ts,
        )
