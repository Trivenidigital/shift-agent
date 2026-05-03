"""cf-router — SMB-Agents safety-net plugin.

Replaces F8 (catering-owner-action-watchdog) and F9 (shift-missed-dispatch-notifier)
custom watchdog daemons with a single native Hermes plugin using the
`pre_gateway_dispatch` hook (verified API at gateway/run.py:4197-4231 in Hermes 0.12.0+).

Behavior:
  F8 path — owner sends `#XXXXX (approve|reject|edit|yes|no)` in their self-chat.
    Plugin extracts code + verb, looks up matching lead/menu-pending, invokes the
    deployed apply-script directly, returns {"action": "skip"} so the LLM is
    bypassed entirely. Eliminates the LLM-improvises failure mode that PR-CF3
    documented (LLM ran python3 heredoc to inspect pending file rather than
    calling apply-menu-update).

  F9 path — employee sender + sick-call regex pattern detected. Plugin fires
    Pushover P2 alert to owner so they know to check the dispatcher route within
    60s. Plugin returns {"action": "allow"} (LLM still runs); this is alert-only.

Drift-check tag: Hermes-native (uses Hermes plugin substrate as documented;
no custom infrastructure beyond the plugin itself).

PR-CF6 (2026-05-03).
"""
from __future__ import annotations

from . import hooks


def register(ctx) -> None:
    """Hermes plugin entry point. Called once at gateway startup per the
    plugin loader pattern documented in
    website/docs/guides/build-a-hermes-plugin.md.
    """
    ctx.register_hook("pre_gateway_dispatch", hooks.pre_gateway_dispatch)
