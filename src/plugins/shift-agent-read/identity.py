"""Owner authorization for the shift-agent-read tools.

Authority comes from ONE place: the gateway-bound session ContextVar, resolved
through the existing `identify-sender` kernel. There is no second identity map
and no role argument — a tool's model-supplied arguments never carry identity,
so a model cannot assert who it is.

Two properties this file exists to preserve, both proven against pinned Hermes
0.19.1 before it was written:

* `get_session_env` returns an explicitly-set ContextVar verbatim — including
  ``""`` — and only falls back to ``os.environ`` when the variable was never set
  in this context. A gateway turn always sets it, so an unbound session refuses
  even when a process-global HERMES_SESSION_USER_ID is present.
* `identify-sender` already accepts `+E164`, `<digits>@lid` and
  `<digits>@s.whatsapp.net`. WhatsApp puts the bridge senderId straight into
  SessionSource.user_id, so the session value is passed through unmodified. No
  normalization shim.
"""
from __future__ import annotations

import json
import os
import subprocess

IDENTIFY_SENDER_BIN = os.environ.get(
    "SHIFT_AGENT_IDENTIFY_SENDER_BIN", "/usr/local/bin/identify-sender"
)
IDENTIFY_TIMEOUT_SEC = 15


def ok(**fields) -> str:
    """A successful result as JSON TEXT. Handlers never return dicts."""
    return json.dumps({"ok": True, **fields})


def refuse(reason: str, **fields) -> str:
    """A refusal as JSON TEXT.

    Deliberately carries NO `items` key and no count fields. A refusal must not
    be readable as an authoritative empty result — that is the difference
    between "I could not check" and "there is nothing due".
    """
    return json.dumps({"ok": False, "refused": reason, **fields})


def session_principal() -> str:
    """The authenticated caller identifier bound by the gateway for this turn."""
    from gateway.session_context import get_session_env

    return get_session_env("HERMES_SESSION_USER_ID", "")


def resolve_identity(principal: str) -> dict | None:
    """Run the deployed identify-sender kernel. Exact argv, never a shell string."""
    try:
        proc = subprocess.run(
            [IDENTIFY_SENDER_BIN, principal],
            capture_output=True, text=True, timeout=IDENTIFY_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None


def require_owner() -> tuple[bool, str]:
    """Return (True, "") for the owner, else (False, <refusal JSON text>)."""
    principal = session_principal()
    if not principal:
        return False, refuse("unbound_session")
    identity = resolve_identity(principal)
    if identity is None:
        return False, refuse("identity_unresolved")
    if identity.get("role") != "owner":
        return False, refuse("not_owner")
    return True, ""
