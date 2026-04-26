"""Cockpit's own audit log — append-only, NDJSON.

Deploy-time setup (root):
    sudo touch /opt/shift-agent/logs/cockpit-audit.log
    sudo chown shift-agent:shift-agent /opt/shift-agent/logs/cockpit-audit.log
    sudo chmod 0640 /opt/shift-agent/logs/cockpit-audit.log
    sudo chattr +a /opt/shift-agent/logs/cockpit-audit.log

Rotation:
    /etc/logrotate.d/shift-agent-cockpit:
      /opt/shift-agent/logs/cockpit-audit.log {
        weekly
        rotate 26
        compress
        nocreate
        prerotate  /usr/bin/chattr -a /opt/shift-agent/logs/cockpit-audit.log; endscript
        postrotate /usr/bin/chattr +a /opt/shift-agent/logs/cockpit-audit.log; endscript
      }
"""
from __future__ import annotations

import json
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


def log(
    event: str,
    *,
    actor: str = "owner",
    ip: str = "",
    ua: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    """Append a structured audit entry.

    Uses safe_io.ndjson_append so embedded newlines are rejected before
    they can split entries. The file MUST be chattr +a'd at deploy time.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "ip": ip,
        "ua": ua[:200],  # cap UA length
        "details": details or {},
    }
    safe_io.ndjson_append(settings.cockpit_audit_log, entry)
