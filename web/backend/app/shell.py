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
        "/usr/local/bin/send-flyer-campaign",
        # Catering Studio (M6) owner actions. Each of these owns its own
        # locking, audit row and idempotency — the cockpit never writes
        # catering state directly.
        "/usr/local/bin/apply-catering-owner-decision",
        "/usr/local/bin/set-catering-lead-hold",
        "/usr/local/bin/amend-catering-lead",
        "/usr/local/bin/import-catering-pricebook",
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
    stdin_data: str | None = None,
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
    stdin_data:
        Free-form text piped to the child's stdin. This is the ONLY safe
        channel for operator-authored prose (quote text): argv would make it
        a shell-escape surface, which is exactly why
        ``apply-catering-owner-decision`` grew ``--quote-text-stdin``.

    Raises
    ------
    ValueError:
        If binary is not on the allow-list or args fail validation.
    TypeError:
        If args contain non-string entries.
    """
    # Cheap input validations FIRST (before disk-touch). This keeps the
    # function unit-testable without /usr/local/bin/* present on the runner,
    # AND short-circuits malformed calls before they touch the filesystem.
    if binary not in ALLOWED_BINS:
        raise ValueError(f"binary not in allowlist: {binary!r}")
    if any(not isinstance(a, str) for a in args):
        raise TypeError("args must be strings")
    if user_args is not None:
        if any(not isinstance(a, str) for a in user_args):
            raise ValueError("user_args invalid: non-str entry")
        if any("\x00" in a for a in user_args):
            raise ValueError("user_args invalid: NUL byte in entry")
    if stdin_data is not None:
        if not isinstance(stdin_data, str):
            raise TypeError("stdin_data must be a string")
        if "\x00" in stdin_data:
            raise ValueError("stdin_data invalid: NUL byte")

    # Disk-touch happens last (after cheap validation passes).
    if not Path(binary).is_file():
        raise ValueError(f"binary missing on disk: {binary!r}")

    cmd: list[str] = [binary, *args]
    if user_args:
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
            input=stdin_data,
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
