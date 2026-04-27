"""Subprocess wrapper — strict allow-list, no shell, sanitized args.

This is the cockpit backend's only path for spawning CLI binaries.
All routers MUST import `run_cli` here for any user-input-derived process call.

Defenses (per design v1.1):
- Allow-list of absolute paths. Anything else raises ValueError.
- shell=False, args is always a list.
- Args must be strings. No content evaluation.
- User-supplied positional args MUST be passed after a `--` terminator
  to prevent flag-injection (e.g., `--actor=evil`).

Documented exceptions (do NOT add to this list without review):
- `app.audit.verify_append_only`: read-only `lsattr` invocation on a fixed
  path at startup. Diagnostic only; takes no user input. Lives outside
  this module because it predates `run_cli` and the binary is OS-internal
  rather than a project CLI.
- `app.routers.whatsapp.{start_repair,unlink}`: `systemctl` calls with
  fixed args (no user input) plus the bridge `Popen` with validated
  config-derived paths. Documented in those handlers.
- `app.routers.health._gateway_active`: read-only `systemctl is-active`.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Absolute paths only. PATH lookup is bypassed.
ALLOWED_BINS: frozenset[str] = frozenset(
    {
        "/usr/local/bin/identify-sender",
        "/usr/local/bin/create-proposal",
        "/usr/local/bin/update-proposal-status",
        "/usr/local/bin/send-coverage-message",
        "/usr/local/bin/render-coverage-template",
        "/usr/local/bin/log-decision",
        "/usr/local/bin/shift-agent-disable",
        "/usr/local/bin/shift-agent-enable",
        "/usr/local/bin/shift-agent-smoke-test.sh",
        "/usr/local/bin/shift-agent-notify-owner",
    }
)


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_cli(
    binary: str,
    args: list[str],
    *,
    timeout: float = 30.0,
    user_args: list[str] | None = None,
) -> CliResult:
    """Run a vetted CLI binary.

    Parameters
    ----------
    binary:
        Absolute path; must be in ``ALLOWED_BINS``.
    args:
        Trusted, server-controlled args (flags, fixed strings).
    user_args:
        User-supplied positional args. Will be appended after ``--``.
        Each must be a non-empty string with no NUL bytes.

    Raises
    ------
    ValueError:
        If binary is not on the allow-list or args fail validation.
    TypeError:
        If args contain non-string entries.
    """
    if binary not in ALLOWED_BINS:
        raise ValueError(f"binary not in allowlist: {binary!r}")
    if not Path(binary).is_file():
        raise ValueError(f"binary missing on disk: {binary!r}")
    if any(not isinstance(a, str) for a in args):
        raise TypeError("args must be strings")

    cmd: list[str] = [binary, *args]
    if user_args:
        if any(not isinstance(a, str) or "\x00" in a for a in user_args):
            raise ValueError("user_args invalid: empty/non-str/NUL")
        cmd.append("--")
        cmd.extend(user_args)

    logger.debug("run_cli: %s", cmd)
    try:
        proc = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return CliResult(
            returncode=-1,
            stdout=e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or ""),
            timed_out=True,
        )
    return CliResult(
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
