#!/usr/bin/env python3
"""
shift-agent-reconcile — boot-time recovery for crashed mid-send proposals.

Runs as a oneshot systemd unit at boot (before hermes-gateway becomes active),
after every restart. Checks pending.json for proposals that need manual attention:

- `reconciling` status older than 5 min → a send-coverage-message invocation crashed
  mid-flight. Do NOT auto-retry (safer than risking a duplicate send). Alert owner.
- `approved` status with an existing `outbound_attempted` log entry but no
  `outbound_sent` → same scenario detected another way. Alert owner.
- `approved` status with NO corresponding `outbound_attempted` → legitimate missed
  send (e.g., gateway crashed before POST). Invoke send-coverage-message.

Safe-by-default. Never sends duplicates.
"""

from __future__ import annotations
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
from schemas import Config, PendingStore  # noqa: E402
from safe_io import FileLock, load_model, customer_now  # noqa: E402
from exit_codes import EXIT_OK, EXIT_SCHEMA_VIOLATION  # noqa: E402
import yaml


CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
PENDING_PATH = Path("/opt/shift-agent/state/pending.json")
PENDING_LOCK = Path("/opt/shift-agent/state/pending.json.lock")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")

RECONCILING_MAX_AGE_MIN = 5


def _alert(message: str, priority: int = 1, title: str = "Reconciler"):
    try:
        subprocess.run([
            "/usr/local/bin/shift-agent-notify-owner",
            "--title", title, "--priority", str(priority), message,
        ], check=False, timeout=30)
    except Exception:
        pass


class AttemptLogUnreadable(RuntimeError):
    """Raised when decisions.log cannot be scanned. Reconciler MUST NOT proceed —
    returning False would misclassify previously-attempted sends as "no attempt"
    and trigger duplicate WhatsApps."""


def _proposal_has_attempt_in_log(proposal_id: str) -> bool:
    """Scan decisions.log for OutboundAttempted/OutboundSent with this proposal_id.

    Silent-failures-#11 FIX: previous version caught OSError with pass and returned
    False. If decisions.log was momentarily inaccessible at boot, reconciler would
    classify every previously-attempted send as "no prior attempt" and retry them
    — duplicate WhatsApps to candidates. Now raises AttemptLogUnreadable; caller
    must refuse to reconcile and alert owner.

    O(n) per call; main() collects attempted-pids in a single pass for efficiency.
    """
    if not LOG_PATH.exists():
        return False
    try:
        with LOG_PATH.open() as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") in ("outbound_attempted", "outbound_sent") \
                        and entry.get("proposal_id") == proposal_id:
                    return True
    except OSError as e:
        raise AttemptLogUnreadable(f"cannot scan {LOG_PATH}: {e}") from e
    return False


def main():
    # Load config
    try:
        with CONFIG_PATH.open() as f:
            cfg = Config.model_validate(yaml.safe_load(f))
    except Exception as e:
        print(f"config load failed: {e}", file=sys.stderr)
        _alert(f"Reconciler: config load failed: {e}", priority=2)
        return EXIT_SCHEMA_VIOLATION

    now = customer_now(cfg.customer.timezone)
    stale_cutoff = now - timedelta(minutes=RECONCILING_MAX_AGE_MIN)

    with FileLock(PENDING_LOCK):
        store, status = load_model(PENDING_PATH, PendingStore, default=PendingStore())
        if status not in ("ok", "empty", "missing"):
            _alert(f"Reconciler: pending.json {status}", priority=2)
            return EXIT_SCHEMA_VIOLATION

        stuck_reconciling: list[str] = []
        approved_needing_send: list[str] = []
        approved_attempted_but_uncertain: list[str] = []

        for pid, prop in store.proposals.items():
            if prop.status == "reconciling":
                age = now - prop.last_updated_ts
                if age > timedelta(minutes=RECONCILING_MAX_AGE_MIN):
                    stuck_reconciling.append(pid)
            elif prop.status == "approved":
                try:
                    has_attempt = _proposal_has_attempt_in_log(pid)
                except AttemptLogUnreadable as e:
                    # Silent-failures-#11 FIX: refuse to reconcile when we can't
                    # verify prior attempts. Safer than risking a duplicate send.
                    _alert(
                        f"Reconciler refusing to process {pid}: decisions.log unreadable ({e}). "
                        "Fix log access then manually retry or disable the proposal.",
                        priority=2,
                    )
                    print(f"SKIP {pid}: {e}")
                    continue
                if has_attempt:
                    approved_attempted_but_uncertain.append(pid)
                else:
                    approved_needing_send.append(pid)

    # Report stuck reconciling (do NOT retry)
    for pid in stuck_reconciling:
        msg = (
            f"Proposal {pid} is stuck in 'reconciling' status for >{RECONCILING_MAX_AGE_MIN}min. "
            f"A previous send likely crashed mid-flight. NOT auto-retrying. "
            f"Manual resolution: check WhatsApp to see if candidate was actually messaged, "
            f"then either `update-proposal-status {pid} sent` (if they got it) or "
            f"`update-proposal-status {pid} send_failed` (if not) + RETRY later."
        )
        print(msg)
        _alert(msg, priority=2)

    # Report approved+attempted (uncertain; do NOT retry)
    for pid in approved_attempted_but_uncertain:
        msg = (
            f"Proposal {pid} is 'approved' but has an outbound_attempted log entry "
            f"without a matching outbound_sent. An earlier send may have partially completed. "
            f"NOT auto-retrying. Check WhatsApp and resolve manually."
        )
        print(msg)
        _alert(msg, priority=2)

    # Retry approved proposals with no attempt recorded (legitimate missed sends)
    for pid in approved_needing_send:
        print(f"Reconciler: retrying send for {pid} (no prior attempt recorded)")
        try:
            result = subprocess.run(
                ["/usr/local/bin/send-coverage-message", pid],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                print(f"  → sent: {result.stdout.strip()}")
            else:
                print(f"  → failed (exit {result.returncode}): {result.stderr.strip()}")
                _alert(
                    f"Reconciler: send-coverage-message {pid} failed at boot "
                    f"(exit {result.returncode}): {result.stderr[:200]}",
                    priority=1,
                )
        except subprocess.TimeoutExpired:
            # Silent-failures-#12 FIX: transition proposal to send_failed on timeout.
            # Leaving status=approved caused a boot-loop: next boot → try → timeout
            # → alert → repeat indefinitely. Now the proposal becomes terminal
            # (send_failed) until owner sends RETRY #CODE.
            _alert(f"Reconciler: send-coverage-message {pid} timed out; marking send_failed. Owner can RETRY after investigating.", priority=2)
            try:
                subprocess.run([
                    "/usr/local/bin/update-proposal-status", pid, "send_failed",
                    "--cause", "reconciler_timeout",
                    "--actor", "reconciler",
                    "--last-error", "reconciler_subprocess_timeout_60s",
                    "--retry-count", "0",
                ], check=False, timeout=15)
            except Exception as e:
                _alert(f"Reconciler: additionally failed to mark {pid} send_failed: {e}", priority=2)

    if not (stuck_reconciling or approved_attempted_but_uncertain or approved_needing_send):
        print("Reconciler: nothing to do")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
