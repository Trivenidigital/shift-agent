"""
Shift Agent — safe I/O + cross-script reusable primitives.

Filesystem / lock / atomic-IO primitives:
  - assert_local_disk: flock is unreliable on NFS; refuse to run there
  - FileLock: context-manager wrapper over fcntl.LOCK_EX
  - safe_load_json: distinguishes missing / empty / corrupt / ok
  - atomic_write_json: write+fsync+replace+fsync(dir) pattern
  - atomic_write_text: same, for plain text
  - ndjson_append: flock-protected newline-terminated append with fsync
  - sweep_orphan_temps: cleanup SIGKILL-orphaned .tmp-<pid> files
  - load_model: Pydantic-validating load of a JSON file into a Pydantic model
  - dump_model: Pydantic-safe dump of a Pydantic model to JSON (atomic)

Time helpers:
  - customer_now: always-timezone-aware datetime in customer tz
  - customer_today_str: ISO YYYY-MM-DD in customer tz
  - self_gate_window_state: 4-state cron-self-gate (before / in_window /
    in_catchup / past_catchup) — shared across send-daily-brief + eod-reconcile

Cross-script notification:
  - notify_owner_with_fallback: subprocess invocation of shift-agent-notify-owner
    with structured fallback to notify-failed.log on Pushover failure — shared
    across send-coverage-message + send-daily-brief + eod-reconcile

Module scope (drift note 2026-04-30): this module historically held only
filesystem/lock primitives. The platform-helpers consolidation expanded
the scope to include cross-script primitives that share a common
"safe-IO + operational hygiene" theme. New helpers should belong here
when they are: (a) used by 2+ deployed scripts, (b) pure functions or
narrow subprocess invocations, (c) have no schema dependencies (those
go in audit_helpers.py).
"""

from __future__ import annotations
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar, Type, Any, Optional, Tuple
from datetime import datetime, timezone
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


class LockUnavailable(RuntimeError):
    """Raised by try_acquire_filelock_with_retry when all attempts exhaust
    without acquiring the lock. Caller MUST handle — the contextmanager body
    runs ONLY when the lock is held; LockUnavailable raises BEFORE the body.

    This raise-on-exhaustion contract is deliberate: a bool-return shape would
    be a footgun (caller forgets to check; runs lockless; corrupts state).
    """


@contextmanager
def try_acquire_filelock_with_retry(lockpath: Path, *, attempts: int = 3, sleep_sec: float = 1.0):
    """Non-blocking flock with retry; raises LockUnavailable on exhaustion.

    Targets the SAME `.lock` sibling pattern that FileLock(LEADS_LOCK) writers
    use — serializes correctly with them. Use when blocking on a contended
    lock would harm UX (e.g. a customer-facing SKILL preamble that must
    complete in seconds).

    Usage:
        try:
            with try_acquire_filelock_with_retry(LEADS_LOCK, attempts=3, sleep_sec=1.0):
                # body runs only when lock is held
                ...
        except LockUnavailable:
            return _empty_result("lock_timeout")

    Caller MUST catch LockUnavailable; failing to do so is a programming bug
    (the body never runs without the lock — there's no silent pass-through).

    Implementation note: fd is opened OUTSIDE the retry loop and closed in
    finally even when no acquire succeeds. `acquired` flag guards LOCK_UN so
    we never call UN on a fd that never held the lock.
    """
    lockpath = Path(lockpath)
    lockpath.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lockpath), os.O_RDWR | os.O_CREAT, 0o640)
    acquired = False
    try:
        for attempt in range(max(1, attempts)):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if attempt < max(1, attempts) - 1:
                    time.sleep(max(0.0, sleep_sec))
        if not acquired:
            raise LockUnavailable(
                f"could not acquire {lockpath} after {attempts} attempts"
            )
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass  # fd close below releases anyway
        os.close(fd)


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


def load_yaml_model(path: Path, model_cls: Type[T]) -> T:
    """Load + Pydantic-validate a YAML file (e.g., config.yaml).

    UNLIKE load_model (which is JSON-only and rename-quarantines on parse error),
    this helper:
    - parses with yaml.safe_load (correct for YAML)
    - does NOT rename-quarantine on parse error (YAML files like config.yaml are
      operator-edited; auto-quarantine on a transient parse hiccup or syntax
      typo is wrong policy — operator should see the parse error and fix the
      file in place, NOT find their config silently moved aside)
    - raises explicitly so callers control the failure path

    Use for: config.yaml and any other YAML state file. Do NOT use load_model
    (which calls safe_load_json) for these — calling json.loads on YAML content
    raises JSONDecodeError, which safe_load_json then converts into a corrupt-
    rename. That's how the Expense Bookkeeper scripts' load_model(CONFIG_PATH)
    callsites silently quarantined customer config.yaml during PR-A deploy.

    Raises:
        FileNotFoundError: path missing.
        RuntimeError: empty/null YAML / yaml parse error / read I/O error /
                      non-UTF-8 file content. RuntimeError is the closed
                      "content-or-IO problem" exception class so callers'
                      `except (FileNotFoundError, RuntimeError, ValidationError)`
                      tuple stays complete (PR #34 reviewers R1+R3 finding).
        pydantic.ValidationError: data shape doesn't match model.
    """
    import yaml as _yaml
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise  # caller catches separately
    except OSError as e:
        raise RuntimeError(f"I/O error reading {path}: {e}") from e
    except UnicodeDecodeError as e:
        raise RuntimeError(f"non-UTF-8 content in {path}: {e}") from e
    try:
        data = _yaml.safe_load(raw)
    except _yaml.YAMLError as e:
        raise RuntimeError(f"YAML parse failed for {path}: {e}") from e
    if data is None:
        raise RuntimeError(f"YAML file is empty or null at root: {path}")
    return model_cls.model_validate(data)


class LoadStatusError(RuntimeError):
    """Raised when safe_load_json/load_model returned an unhealthy status that
    a writer cannot safely fall through (corrupt parse, OS-level I/O failure,
    rename-failure on corrupt quarantine, novel future status).
    """


_HEALTHY_LOAD_STATUSES = frozenset({"ok", "missing", "empty"})


def assert_load_status_clean(path: Path, status: str, *, context: str) -> None:
    """Raise LoadStatusError if `status` indicates an unsafe load.

    Healthy statuses (caller falls through to default): ok / missing / empty.
    All other statuses (corrupt:* / corrupt_unrenamed:* / oserror:* / future)
    raise.

    Use at the head of every writer's load_model() block. Canonical 5-line
    callsite pattern (keep identical across all writers for grep-based audits):

        store, status = load_model(LEADS_PATH, CateringLeadStore, default=...)
        try:
            assert_load_status_clean(LEADS_PATH, status, context="apply-decision read")
        except LoadStatusError as e:
            sys.stderr.write(f"{e}\n")
            return EXIT_SCHEMA_VIOLATION

    A future status addition (e.g. 'too_large:') automatically protects every
    writer rather than silently falling through three different scripts.
    """
    if status in _HEALTHY_LOAD_STATUSES:
        return
    raise LoadStatusError(
        f"unhealthy load status for {path} (context={context!r}): {status}"
    )


# Priority-1 tightening: accept parens + space (common customer input formats)
# while still rejecting shell metachars: `;&|<>$\\*?'"!^{}[]\``.
_PHONE_SAFE = re.compile(r"^[+\d@.\w\-() ]+$")


def validate_phone_input(v: str) -> str:
    """Defensive: refuse anything with shell/pipe/backtick characters before subprocess use."""
    if not _PHONE_SAFE.match(v):
        raise ValueError(f"refusing suspicious phone input: {v!r}")
    return v


# ─────────────────────────────────────────────────────────────────
# Owner notification with fallback log
# ─────────────────────────────────────────────────────────────────

# Env-var overrides for testability (callers can also pass explicit kwargs).
# SHIFT_AGENT_NOTIFY_OWNER_BIN: path to shift-agent-notify-owner (test stub override).
# SHIFT_AGENT_NOTIFY_FAILED_LOG: fallback log path. Note historical drift —
#   Shift's send-coverage-message wrote to /opt/shift-agent/state/notify-failed.log
#   while Daily Brief wrote to /opt/shift-agent/logs/notify-failed.log.
#   Default here is /logs/ (Daily Brief's path; matches operator-log convention);
#   send-coverage-message passes the /state/ path explicitly to preserve its
#   deployed location.
NOTIFY_OWNER_BIN = os.environ.get(
    "SHIFT_AGENT_NOTIFY_OWNER_BIN", "/usr/local/bin/shift-agent-notify-owner",
)
NOTIFY_FAILED_LOG = Path(os.environ.get(
    "SHIFT_AGENT_NOTIFY_FAILED_LOG", "/opt/shift-agent/logs/notify-failed.log",
))


def self_gate_window_state(
    now_local: datetime,
    target_time_str: str,
    *,
    window_min: int = 15,
    catchup_min: int,
) -> Tuple[str, int]:
    """Self-gate state for cron-driven scripts that fire repeatedly.

    Returns ('before' | 'in_window' | 'in_catchup' | 'past_catchup', minutes_late).

    The cron timer fires every `window_min` minutes; only the firing inside
    the [target_time, target_time + window_min) window should actually do
    the work. Catchup window extends eligibility past the primary window
    to allow recovery from VPS downtime.

    Used by:
    - send-daily-brief (target_time = cfg.daily_brief.brief_time)
    - eod-reconcile (target_time = cfg.eod.eod_time)

    Args:
        now_local: timezone-aware local time (caller's responsibility to
            convert via customer_now() before calling).
        target_time_str: HH:MM 24h format (parsed and matched against
            now_local on the same day).
        window_min: primary firing window in minutes after target time.
            Must match the cron OnUnitActiveSec.
        catchup_min: additional minutes after the primary window during
            which a "this-firing-is-late-but-still-counts" path runs.
    """
    from datetime import timedelta as _timedelta
    h, m = (int(x) for x in target_time_str.split(":"))
    target_dt = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    window_end = target_dt + _timedelta(minutes=window_min)
    catchup_end = target_dt + _timedelta(minutes=catchup_min)
    if now_local < target_dt:
        return "before", 0
    if now_local < window_end:
        return "in_window", 0
    minutes_late = int((now_local - target_dt).total_seconds() // 60)
    if now_local < catchup_end:
        return "in_catchup", minutes_late
    return "past_catchup", minutes_late


def notify_owner_with_fallback(
    title: str,
    message: str,
    priority: int = 1,
    *,
    source: str = "unknown",
    notify_owner_bin: str = NOTIFY_OWNER_BIN,
    notify_failed_log: Path = NOTIFY_FAILED_LOG,
) -> bool:
    """Invoke shift-agent-notify-owner subprocess. On any failure, append a
    structured entry to notify-failed.log so the nightly fsck + health-check
    can surface dropped alerts.

    Returns True on Pushover success, False on any failure path.

    Replaces the near-mirror implementations previously inlined in:
      - send-coverage-message._notify_owner + _append_notify_failed
      - send-daily-brief._pushover_alert
      - eod-reconcile._pushover_summary (subprocess call portion)

    `source` identifies the caller (e.g. "send-coverage-message",
    "send-daily-brief", "eod-reconcile") and lands in notify-failed.log
    entries for triage. Final fallback (notify-failed.log itself unwritable)
    writes to stderr so journald captures the alert-drop event.

    The notify_owner_bin and notify_failed_log kwargs are for testability;
    callers should not override them in production code.

    Default-binding note: NOTIFY_OWNER_BIN and NOTIFY_FAILED_LOG are
    captured at module-import time (which reads the env vars then). Python
    binds function defaults at function-def time, so a long-lived process
    that monkeypatches the env vars after first import would see stale
    defaults. In practice all callers are short-lived subprocess
    invocations where systemd sets the env vars before exec, so this is
    not a concern. Tests that need post-import overrides should pass
    explicit kwargs (the helper's own tests do).
    """
    import json as _json
    import subprocess as _subprocess

    err_detail = ""
    try:
        proc = _subprocess.run(
            [notify_owner_bin, "--title", title, "--priority", str(priority), message],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return True
        err_detail = f"exit={proc.returncode} stderr={proc.stderr.strip()[:200]}"
    except (_subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        err_detail = f"{type(e).__name__}: {e}"

    # Pushover-also-fails fallback: append to notify-failed.log
    try:
        notify_failed_log.parent.mkdir(parents=True, exist_ok=True)
        with notify_failed_log.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "source": source,
                "title": title[:200],
                "message": message[:500],
                "pushover_error": err_detail[:300],
            }) + "\n")
    except OSError as fallback_err:
        # Final fallback — disk full, read-only mount, etc. journald captures
        # stderr so the alert-drop event cannot vanish entirely.
        sys.stderr.write(
            f"CRITICAL: alert dropped — Pushover failed AND notify-failed.log "
            f"unwritable. source={source} title={title!r} "
            f"pushover_err={err_detail[:200]!r} "
            f"fallback_err={fallback_err!r}\n"
        )
    return False


# ─────────────────────────────────────────────────────────────────
# Hermes bridge POST (extracted from send-daily-brief 2026-05-04 PR-Agent13)
# Used by send-daily-brief, check-compliance-deadlines, and any future
# script that needs to POST a WhatsApp message to the local Hermes bridge.
# ─────────────────────────────────────────────────────────────────

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

# Bridge POST configuration — env-var-driven defaults captured at import time.
# Same pattern as NOTIFY_OWNER_BIN; long-lived processes that monkeypatch
# env vars after import would see stale defaults (not a concern for short-
# lived script invocations).
BRIDGE_URL = os.environ.get("HERMES_BRIDGE_URL", "http://127.0.0.1:3000/send")
BRIDGE_TIMEOUT_SEC = int(os.environ.get("HERMES_BRIDGE_TIMEOUT_SEC", "10"))
BRIDGE_RETRY_DELAY_SEC = 5.0
ALLOW_REMOTE_BRIDGE = os.environ.get("HERMES_BRIDGE_ALLOW_REMOTE", "0") == "1"


def validate_bridge_url(url: str) -> Optional[str]:
    """Return error string if URL is unsafe; None if OK. Defends against
    HERMES_BRIDGE_URL env var being repointed at an exfiltration target.

    Public (no leading underscore) so unit tests can import without violating
    encapsulation contract. Pure function — safe public API.
    """
    from urllib.parse import urlparse
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return f"unsupported scheme: {p.scheme!r}"
    if ALLOW_REMOTE_BRIDGE:
        return None
    host = (p.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return (
            f"refusing non-loopback bridge URL: {host!r} "
            f"(set HERMES_BRIDGE_ALLOW_REMOTE=1 to override)"
        )
    return None


def bridge_post(jid: str, message: str) -> Tuple[bool, str, str, str]:
    """POST to local Hermes bridge. Returns (success, message_id, error_str, status).

    status ∈ {'sent', 'connect_failed', 'http_error', 'send_uncertain', 'unknown_error'}

    'send_uncertain' = bridge ACCEPTED (2xx) but ack body unparseable; message
    likely was delivered. Caller MUST NOT auto-retry (would duplicate).
    """
    bad = validate_bridge_url(BRIDGE_URL)
    if bad:
        return False, "", bad, "connect_failed"
    payload = json.dumps({"chatId": jid, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        BRIDGE_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=BRIDGE_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                doc = json.loads(body)
            except json.JSONDecodeError:
                return False, "", f"ack_parse_failed: {body[:200]}", "send_uncertain"
            mid = doc.get("id") or doc.get("messageId") or ""
            if not mid:
                return False, "", f"empty_message_id: {body[:200]}", "send_uncertain"
            return True, mid, "", "sent"
    except urllib.error.HTTPError as e:
        return False, "", f"HTTP {e.code}: {e.reason}", "http_error"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason}", "connect_failed"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}", "unknown_error"


def bridge_media_url() -> str:
    """Return the companion /send-media URL for the configured text bridge."""
    if BRIDGE_URL.endswith("/send"):
        return BRIDGE_URL[:-len("/send")] + "/send-media"
    return BRIDGE_URL.rstrip("/") + "/send-media"


def bridge_cta_url() -> str:
    """Return the companion /send-cta URL for interactive CTA messages."""
    if BRIDGE_URL.endswith("/send"):
        return BRIDGE_URL[:-len("/send")] + "/send-cta"
    return BRIDGE_URL.rstrip("/") + "/send-cta"


def bridge_send_media(
    jid: str,
    file_path: Path | str,
    *,
    media_type: str = "",
    caption: str = "",
    file_name: str = "",
) -> Tuple[bool, str, str, str]:
    """POST a media file to the local Hermes bridge /send-media endpoint.

    Returns (success, message_id, error_str, status).
    status values mirror bridge_post where possible, plus missing_file.
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return False, "", f"missing media file: {path}", "missing_file"

    url = bridge_media_url()
    bad = validate_bridge_url(url)
    if bad:
        return False, "", bad, "connect_failed"

    payload_doc = {
        "chatId": jid,
        "filePath": str(path),
    }
    if media_type:
        payload_doc["mediaType"] = media_type
    if caption:
        payload_doc["caption"] = caption
    if file_name:
        payload_doc["fileName"] = file_name

    req = urllib.request.Request(
        url,
        data=json.dumps(payload_doc).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=BRIDGE_TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                doc = json.loads(body)
            except json.JSONDecodeError:
                return False, "", f"ack_parse_failed: {body[:200]}", "send_uncertain"
            mid = doc.get("id") or doc.get("messageId") or ""
            if not mid:
                return False, "", f"empty_message_id: {body[:200]}", "send_uncertain"
            if doc.get("success") is False:
                return False, "", f"bridge_send_failed: {body[:200]}", "http_error"
            return True, mid, "", "sent"
    except urllib.error.HTTPError as e:
        return False, "", f"HTTP {e.code}: {e.reason}", "http_error"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason}", "connect_failed"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}", "unknown_error"


def bridge_send_cta(
    jid: str,
    *,
    body: str,
    buttons: list[dict[str, str]],
    footer: str = "",
    media_path: Path | str | None = None,
    media_type: str = "",
) -> Tuple[bool, str, str, str]:
    """POST an interactive reply-button message to the local Hermes bridge /send-cta.

    Returns (success, message_id, error_str, status).
    status values mirror bridge_post where possible, plus invalid_payload.

    The bridge renders button labels and owns the reply payload, so callers can
    keep customer-visible text clean while still offering one-tap chat actions.
    """
    if not body.strip():
        return False, "", "CTA body is required", "invalid_payload"
    if not buttons:
        return False, "", "at least one CTA button is required", "invalid_payload"

    cleaned_buttons: list[dict[str, str]] = []
    for button in buttons:
        label = str(button.get("label", "")).strip()
        message = str(button.get("message", "")).strip()
        if not label or not message:
            return False, "", "CTA buttons require label and message", "invalid_payload"
        cleaned_buttons.append({"label": label[:60], "message": message[:300]})

    url = bridge_cta_url()
    bad = validate_bridge_url(url)
    if bad:
        return False, "", bad, "connect_failed"

    payload_doc: dict[str, Any] = {
        "chatId": jid,
        "body": body,
        "buttons": cleaned_buttons,
    }
    if footer:
        payload_doc["footer"] = footer
    if media_path is not None and str(media_path):
        payload_doc["mediaPath"] = str(media_path)
    if media_type:
        payload_doc["mediaType"] = media_type

    req = urllib.request.Request(
        url,
        data=json.dumps(payload_doc).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=BRIDGE_TIMEOUT_SEC) as resp:
            ack_body = resp.read().decode("utf-8", errors="replace")
            try:
                doc = json.loads(ack_body)
            except json.JSONDecodeError:
                return False, "", f"ack_parse_failed: {ack_body[:200]}", "send_uncertain"
            mid = doc.get("id") or doc.get("messageId") or ""
            if not mid:
                return False, "", f"empty_message_id: {ack_body[:200]}", "send_uncertain"
            if doc.get("success") is False:
                return False, "", f"bridge_send_failed: {ack_body[:200]}", "http_error"
            return True, mid, "", "sent"
    except urllib.error.HTTPError as e:
        return False, "", f"HTTP {e.code}: {e.reason}", "http_error"
    except urllib.error.URLError as e:
        return False, "", f"URLError: {e.reason}", "connect_failed"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}", "unknown_error"
