"""Cockpit's own audit log — append-only, NDJSON.

Deploy-time setup (root):
    sudo touch /opt/shift-agent/logs/cockpit-audit.log
    sudo chown shift-agent:shift-agent /opt/shift-agent/logs/cockpit-audit.log
    sudo chmod 0640 /opt/shift-agent/logs/cockpit-audit.log
    sudo chattr +a /opt/shift-agent/logs/cockpit-audit.log

Rotation: see web/deploy/logrotate.conf — chattr toggle in pre/postrotate.

Runtime tamper-evidence self-check:
    `verify_append_only()` returns the current append-only state. Called by
    `app.main` lifespan startup; logs a CRITICAL warning if the audit file
    exists but is missing the +a attribute (a missed deploy step or
    rotation that didn't restore +a).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import safe_io  # noqa: E402

from .config import get_settings  # noqa: E402

settings = get_settings()
_log = logging.getLogger(__name__)


def log(
    event: str,
    *,
    actor: str = "owner",
    ip: str = "",
    ua: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Append a structured audit entry.

    `safe_io.ndjson_append` rejects embedded newlines so a row cannot be
    split. The file is `chattr +a` at deploy time so even the cockpit user
    cannot truncate or rewrite it.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "ip": ip,
        "ua": ua[:200],
        "details": details or {},
    }
    safe_io.ndjson_append(settings.cockpit_audit_log, entry)


# ─── Runtime self-check ────────────────────────────────────────────────


# Linux file attribute flag for "append-only" — defined in <ext2_fs.h>.
# Using EXT4_IOC_GETFLAGS at the FS level is cleaner than parsing `lsattr`
# output, but FS_IOC_GETFLAGS only works on ext{2,3,4} + Btrfs. For a
# best-effort check we run lsattr and parse — falls back to "unknown" on
# unsupported filesystems instead of failing-loud.
def verify_append_only() -> tuple[bool, str]:
    """Return (is_append_only, detail).

    is_append_only: True iff `chattr +a` is set on the audit log.
    detail: human-readable explanation. Never raises.
    """
    path = settings.cockpit_audit_log
    if not path.exists():
        return False, f"audit log {path} does not exist"

    # Use lsattr — works on ext{2,3,4} + Btrfs. Best-effort.
    import subprocess

    try:
        r = subprocess.run(
            ["/usr/bin/lsattr", "-d", str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
            check=False,
        )
        if r.returncode != 0:
            return False, f"lsattr returncode={r.returncode}: {r.stderr.strip()[:120]}"
        # Output: "----a---------e----- /opt/shift-agent/logs/cockpit-audit.log"
        attrs = r.stdout.split()[0] if r.stdout.split() else ""
        return ("a" in attrs), f"attrs={attrs!r}"
    except FileNotFoundError:
        return False, "lsattr binary not present (skip — non-ext FS?)"
    except Exception as e:
        return False, f"check failed: {e!r}"


def startup_self_check() -> None:
    """Run at app startup. Logs CRITICAL if audit log lacks +a in production."""
    if os.environ.get("COCKPIT_TEST_MODE", "").lower() in ("1", "true", "yes"):
        return  # tempdir paths in tests; chattr not applicable

    ok, detail = verify_append_only()
    if not ok:
        _log.critical(
            "AUDIT LOG TAMPER-EVIDENCE NOT ACTIVE: %s. "
            "Run: sudo chattr +a %s — until then, the audit log is mutable by "
            "the cockpit user, defeating the chattr protection in the design.",
            detail,
            settings.cockpit_audit_log,
        )
    else:
        _log.info("audit log append-only check passed (%s)", detail)
