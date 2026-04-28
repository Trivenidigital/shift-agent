"""
Shift Agent — safe I/O helpers.

Every script doing filesystem/state work imports from here:
  - assert_local_disk: flock is unreliable on NFS; refuse to run there
  - FileLock: context-manager wrapper over fcntl.LOCK_EX
  - safe_load_json: distinguishes missing / empty / corrupt / ok
  - atomic_write_json: write+fsync+replace+fsync(dir) pattern
  - atomic_write_text: same, for plain text
  - ndjson_append: flock-protected newline-terminated append with fsync
  - sweep_orphan_temps: cleanup SIGKILL-orphaned .tmp-<pid> files
  - customer_now: always-timezone-aware datetime in customer tz
  - load_model: Pydantic-validating load of a JSON file into a Pydantic model
  - dump_model: Pydantic-safe dump of a Pydantic model to JSON (atomic)
"""

from __future__ import annotations
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TypeVar, Type, Any, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = None  # type: ignore

T = TypeVar("T", bound="BaseModel")  # noqa: F821

_LOCAL_FS_CHECKED: dict[str, bool] = {}


def assert_local_disk(path: Path) -> None:
    """Refuse to run if path is on NFS/CIFS/SSHFS — flock is unreliable there."""
    path = Path(path)
    target = str(path.resolve() if path.exists() else path.parent.resolve())
    if target in _LOCAL_FS_CHECKED:
        return
    try:
        fs_type = subprocess.check_output(
            ["stat", "-f", "-c", "%T", target], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        # stat not available or path missing — be conservative, don't block
        _LOCAL_FS_CHECKED[target] = True
        return
    remote_types = {"nfs", "nfs4", "cifs", "smb", "fuseblk", "fuse.sshfs", "afs"}
    if fs_type in remote_types:
        raise RuntimeError(
            f"shift-agent state path {target} is on {fs_type}; "
            f"fcntl.flock is unreliable on remote filesystems. Use local disk."
        )
    _LOCAL_FS_CHECKED[target] = True


class FileLock:
    """Context-manager advisory exclusive lock via fcntl. Lock file persists; lock is fd-scoped."""

    def __init__(self, lockpath: Path):
        self.lockpath = Path(lockpath)
        self.fd: Optional[int] = None

    def __enter__(self) -> "FileLock":
        self.lockpath.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.lockpath), os.O_RDWR | os.O_CREAT, 0o640)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


# BEGIN shift-agent-sender-id
def flock(path):
    """Convenience wrapper: hold an exclusive lock on `<path>.lock` (sibling
    lock file). Used by every script that mutates roster.json or pending.json
    so writers serialize regardless of which entry point invoked them.

    Usage:
        with flock(roster_path):
            ...
    """
    return FileLock(Path(str(path) + ".lock"))
# END shift-agent-sender-id


def safe_load_json(path: Path, default: Any = None) -> Tuple[Any, str]:
    """
    Load a JSON file with explicit failure signaling.
    Returns (value_or_default, status) where status ∈ {ok, missing, empty, corrupt:<err>, oserror:<err>}.
    On corrupt, renames the file to .corrupt-<epoch> so subsequent runs start fresh.
    """
    path = Path(path)
    try:
        if not path.exists():
            return default, "missing"
        raw = path.read_text()
        if not raw.strip():
            return default, "empty"
        return json.loads(raw), "ok"
    except json.JSONDecodeError as e:
        # P3-FIX: path.with_suffix raises ValueError on suffixes containing dots
        # (e.g. ".json.corrupt-1"). Use with_name instead.
        try:
            corrupt = path.with_name(path.name + f".corrupt-{int(time.time())}")
            path.rename(corrupt)
        except OSError as rename_err:
            # Distinct status so callers can alert vs. silently retry
            return default, f"corrupt_unrenamed:{e} (rename_err={rename_err})"
        return default, f"corrupt:{e}"
    except OSError as e:
        return default, f"oserror:{e}"


def atomic_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    """Write+fsync to temp file, then os.replace, then fsync the parent directory.

    P3-FIX: was using path.with_suffix which raises ValueError on dotted suffixes
    like `pending.json.tmp-X`. Using with_name avoids the parse.

    Security-M1 FIX: default 0o600 (not 0o640) for state files. Caller overrides.

    Durable across kernel panics on ext4/xfs with default data=ordered.
    Not guaranteed on data=writeback or nobarrier mounts.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{int(time.time()*1000)}")
    # Preserve existing mode if target was tightened manually
    if path.exists():
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    # fsync parent directory so the rename entry is durable
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def atomic_write_json(path: Path, obj: Any, mode: int = 0o640) -> None:
    """Atomic JSON write. Handles Pydantic models + dicts + lists."""
    if BaseModel is not None and isinstance(obj, BaseModel):
        content = obj.model_dump_json(indent=2)
    else:
        content = json.dumps(obj, indent=2, default=_json_default)
    atomic_write_text(path, content, mode=mode)


def _json_default(x):
    """JSON serializer for types json stdlib can't handle."""
    if isinstance(x, datetime):
        return x.isoformat()
    raise TypeError(f"object of type {type(x).__name__} is not JSON serializable")


def ndjson_append(path: Path, entry_json: str) -> None:
    """Append a single JSON-encoded line (no line-break chars inside) + \\n.
    Caller is responsible for holding an appropriate flock on `<path>.lock`
    if concurrent writers exist. Uses O_APPEND + fsync for durability.

    Comment-accuracy FIX: removed unused `lock` parameter from signature.
    Security-M3 FIX: broadened line-break check to include Unicode line separators
    (U+0085 NEL, U+2028, U+2029) that some NDJSON parsers treat as line terminators.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _LINE_BREAKERS = ("\n", "\r", "", " ", " ")
    if any(c in entry_json for c in _LINE_BREAKERS):
        raise ValueError("ndjson_append: entry_json must not contain line-break characters")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, entry_json.encode("utf-8") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def sweep_orphan_temps(state_dir: Path, max_age_sec: int = 300) -> int:
    """Remove .tmp-<pid>-<ms> files older than max_age_sec. Returns count swept."""
    state_dir = Path(state_dir)
    if not state_dir.exists():
        return 0
    now = time.time()
    swept = 0
    for p in state_dir.glob("*.tmp-*"):
        try:
            if now - p.stat().st_mtime > max_age_sec:
                p.unlink()
                swept += 1
        except OSError:
            pass
    return swept


def customer_now(tz_name: str) -> datetime:
    """Always-aware datetime in customer timezone."""
    return datetime.now(tz=ZoneInfo(tz_name))


def customer_today_str(tz_name: str) -> str:
    return customer_now(tz_name).strftime("%Y-%m-%d")


def load_model(path: Path, model_cls: Type[T], default: Optional[T] = None) -> Tuple[T, str]:
    """Load + Pydantic-validate. Returns (instance, status) where status is same as safe_load_json."""
    raw, status = safe_load_json(path, default=None)
    if status == "missing" or status == "empty":
        if default is None:
            # Construct from empty dict if model allows it
            try:
                return model_cls.model_validate({}), status
            except Exception:
                raise FileNotFoundError(f"{path} {status} and model {model_cls.__name__} has no default")
        return default, status
    if status.startswith("corrupt") or status.startswith("oserror"):
        if default is None:
            raise RuntimeError(f"cannot load {path}: {status}")
        return default, status
    # ok
    return model_cls.model_validate(raw), "ok"


def dump_model(path: Path, model: "BaseModel", mode: int = 0o640) -> None:
    """Atomic Pydantic-safe dump."""
    atomic_write_json(path, model, mode=mode)


# Priority-1 tightening: accept parens + space (common customer input formats)
# while still rejecting shell metachars: `;&|<>$\\*?'"!^{}[]\``.
_PHONE_SAFE = re.compile(r"^[+\d@.\w\-() ]+$")


def validate_phone_input(v: str) -> str:
    """Defensive: refuse anything with shell/pipe/backtick characters before subprocess use."""
    if not _PHONE_SAFE.match(v):
        raise ValueError(f"refusing suspicious phone input: {v!r}")
    return v
