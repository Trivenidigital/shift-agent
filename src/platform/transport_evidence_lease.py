"""Root-owned single-use lease for the transport-evidence harness (CLI side).

The lease is the operator's out-of-band authorization artifact. It lives in a
``root:root 0700`` directory and is validated + CONSUMED by the root-run CLI —
never by the gateway (the gateway's trust anchor is the peer-authenticated
socket + single-use prep token + the durable committed-nonce ledger, NOT the
lease). Consumption is an atomic ``rename`` + ``fsync`` (file + dir) so a lease
can be consumed at most once.

Lease schema (strict; unknown fields rejected)::

    {
      "nonce": "<>=128-bit random>",
      "authorized_commit": "<40-hex future RC commit>",
      "authorized_harness_hash": "<canonical manifest digest>",
      "normalized_destination": "<operator destination>",
      "expires_at": "<ISO-8601 UTC>",
      "max_runs": 1,
      "diagnostic_event_type": "<dedicated event type>"
    }

FS hardening on open (fail-closed on any violation):
  * ``O_NOFOLLOW`` (reject a symlink at the path),
  * regular file, expected owner uid/gid, exact permission bits,
  * hard-link count == 1, bounded size,
  * strict schema + type validation, ``max_runs == 1``,
  * ``expires_at`` in the future vs a reliable clock,
  * nonce minimum entropy.

Cross-platform note: ``O_NOFOLLOW`` and root uid/gid are Linux realities; the
validators accept ``expect_uid`` / ``expect_gid`` so tests can assert the same
code path against a test-owned file on any host. ``getattr(os, "O_NOFOLLOW", 0)``
degrades to a no-op flag off-Linux.

Repository implementation only — default-OFF, never executed on the box by this
change.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MAX_LEASE_BYTES = 4096
DEFAULT_LEASE_MODE = 0o600
DEFAULT_LEASE_DIR_MODE = 0o700
MIN_NONCE_ENTROPY_CHARS = 32  # 128-bit hex floor
_HEX = set("0123456789abcdefABCDEF")
_URLSAFE_B64 = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
)

_REQUIRED_FIELDS = {
    "nonce",
    "authorized_commit",
    "authorized_harness_hash",
    "normalized_destination",
    "expires_at",
    "max_runs",
    "diagnostic_event_type",
}


class LeaseValidationError(RuntimeError):
    """The lease failed a hardening / schema / expiry check → fail closed."""


class LeaseConsumeError(RuntimeError):
    """The lease could not be atomically consumed (already consumed / gone)."""


@dataclass(frozen=True)
class Lease:
    nonce: str
    authorized_commit: str
    authorized_harness_hash: str
    normalized_destination: str
    expires_at: str
    max_runs: int
    diagnostic_event_type: str

    def prepare_fields(self) -> dict:
        """The subset the CLI forwards to the gateway at PREPARE. The gateway
        INDEPENDENTLY verifies each against live state; it never reads the lease
        file itself."""
        return {
            "nonce": self.nonce,
            "authorized_commit": self.authorized_commit,
            "authorized_harness_hash": self.authorized_harness_hash,
            "normalized_destination": self.normalized_destination,
            "expires_at": self.expires_at,
            "diagnostic_event_type": self.diagnostic_event_type,
        }


def nonce_has_min_entropy(nonce: str, min_chars: int = MIN_NONCE_ENTROPY_CHARS) -> bool:
    """Cheap structural entropy floor: long enough, hex/urlsafe-b64 alphabet, and
    not degenerate (>= 8 distinct characters rejects ``aaaa...``)."""
    if not isinstance(nonce, str) or len(nonce) < min_chars:
        return False
    chars = set(nonce)
    if not (chars <= _HEX or chars <= _URLSAFE_B64):
        return False
    return len(chars) >= 8


def _parse_iso_utc(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _validate_schema(raw: dict) -> Lease:
    keys = set(raw.keys())
    extra = keys - _REQUIRED_FIELDS
    missing = _REQUIRED_FIELDS - keys
    if extra:
        raise LeaseValidationError(f"lease has unknown field(s): {sorted(extra)}")
    if missing:
        raise LeaseValidationError(f"lease missing field(s): {sorted(missing)}")
    for name in (
        "nonce",
        "authorized_commit",
        "authorized_harness_hash",
        "normalized_destination",
        "expires_at",
        "diagnostic_event_type",
    ):
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise LeaseValidationError(f"lease field {name!r} must be a non-empty string")
    if raw["max_runs"] != 1:
        raise LeaseValidationError("lease max_runs must be exactly 1 (single-use)")
    if not nonce_has_min_entropy(raw["nonce"]):
        raise LeaseValidationError("lease nonce below minimum entropy")
    return Lease(
        nonce=raw["nonce"],
        authorized_commit=raw["authorized_commit"],
        authorized_harness_hash=raw["authorized_harness_hash"],
        normalized_destination=raw["normalized_destination"],
        expires_at=raw["expires_at"],
        max_runs=1,
        diagnostic_event_type=raw["diagnostic_event_type"],
    )


def validate_lease_dir(
    lease_dir: Path, *, expect_uid: int = 0, expect_gid: int = 0,
    expect_mode: int = DEFAULT_LEASE_DIR_MODE,
) -> None:
    """The lease dir must be a real directory owned root:root 0700."""
    lease_dir = Path(lease_dir)
    st = os.lstat(str(lease_dir))  # lstat: a symlinked dir is itself a violation
    if not stat.S_ISDIR(st.st_mode):
        raise LeaseValidationError(f"lease dir {lease_dir} is not a directory")
    if st.st_uid != expect_uid or st.st_gid != expect_gid:
        raise LeaseValidationError(
            f"lease dir {lease_dir} owner {st.st_uid}:{st.st_gid} != "
            f"expected {expect_uid}:{expect_gid}"
        )
    # POSIX permission bits are only meaningful on the Linux box (the box always
    # enforces 0700 root:root); Windows test hosts cannot set 0700, so POSIX-gate.
    if os.name != "nt" and stat.S_IMODE(st.st_mode) != expect_mode:
        raise LeaseValidationError(
            f"lease dir {lease_dir} mode {oct(stat.S_IMODE(st.st_mode))} != "
            f"expected {oct(expect_mode)}"
        )


def open_and_validate_lease(
    lease_path: Path,
    *,
    now: Optional[datetime] = None,
    expect_uid: int = 0,
    expect_gid: int = 0,
    expect_mode: int = DEFAULT_LEASE_MODE,
    max_bytes: int = MAX_LEASE_BYTES,
) -> Lease:
    """Open (``O_NOFOLLOW``) + validate the lease WITHOUT mutating it. Raises
    ``LeaseValidationError`` on any hardening / schema / expiry violation."""
    lease_path = Path(lease_path)
    now = now or datetime.now(timezone.utc)

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(lease_path), flags)
    except OSError as e:
        # ELOOP here == a symlink was at the path (O_NOFOLLOW refused it).
        raise LeaseValidationError(
            f"cannot open lease {lease_path} (O_NOFOLLOW): {e}"
        ) from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise LeaseValidationError(f"lease {lease_path} is not a regular file")
        if st.st_nlink != 1:
            raise LeaseValidationError(
                f"lease {lease_path} hard-link count {st.st_nlink} != 1"
            )
        if st.st_uid != expect_uid or st.st_gid != expect_gid:
            raise LeaseValidationError(
                f"lease {lease_path} owner {st.st_uid}:{st.st_gid} != "
                f"expected {expect_uid}:{expect_gid}"
            )
        # POSIX permission bits are only meaningful on the Linux box; Windows test
        # hosts cannot set 0o600, so the exact-mode check is POSIX-gated. The box
        # (os.name == "posix") always enforces it.
        if os.name != "nt" and stat.S_IMODE(st.st_mode) != expect_mode:
            raise LeaseValidationError(
                f"lease {lease_path} mode {oct(stat.S_IMODE(st.st_mode))} != "
                f"expected {oct(expect_mode)}"
            )
        if st.st_size > max_bytes:
            raise LeaseValidationError(
                f"lease {lease_path} size {st.st_size} exceeds bound {max_bytes}"
            )
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise LeaseValidationError(f"lease {lease_path} exceeds bound {max_bytes}")
    finally:
        os.close(fd)

    try:
        raw = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise LeaseValidationError(f"lease {lease_path} is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise LeaseValidationError(f"lease {lease_path} must be a JSON object")

    lease = _validate_schema(raw)

    try:
        expires = _parse_iso_utc(lease.expires_at)
    except (ValueError, TypeError) as e:
        raise LeaseValidationError(f"lease expires_at unparseable: {e}") from e
    if expires <= now:
        raise LeaseValidationError(
            f"lease expired at {lease.expires_at} (now {now.isoformat()})"
        )
    return lease


def consume_lease(
    lease_path: Path,
    *,
    now: Optional[datetime] = None,
    consumed_dir: Optional[Path] = None,
) -> dict:
    """Atomically consume the lease: ``rename`` it out of the active path into a
    ``consumed/`` sibling, then ``fsync`` the source + destination dirs. The
    rename is the single-use gate — a second consume of the same path fails with
    ``LeaseConsumeError`` because the file is no longer there.

    Returns a consumption attestation the CLI forwards in COMMIT (informational
    only — the gateway does not read it as authorization)."""
    lease_path = Path(lease_path)
    now = now or datetime.now(timezone.utc)
    consumed_dir = Path(consumed_dir) if consumed_dir else lease_path.parent / "consumed"
    consumed_dir.mkdir(parents=True, exist_ok=True)

    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    dest = consumed_dir / f"{lease_path.name}.consumed-{stamp}"
    try:
        os.rename(str(lease_path), str(dest))
    except OSError as e:
        raise LeaseConsumeError(
            f"lease {lease_path} could not be consumed (already consumed / gone): {e}"
        ) from e

    # Durability: fsync both directories touched by the rename.
    for d in {consumed_dir, lease_path.parent}:
        try:
            dfd = os.open(str(d), os.O_RDONLY)
            try:
                os.fsync(dfd)
            except OSError:
                pass
            finally:
                os.close(dfd)
        except OSError:
            pass
    return {
        "consumed_path": str(dest),
        "consumed_at": now.isoformat().replace("+00:00", "Z"),
    }
