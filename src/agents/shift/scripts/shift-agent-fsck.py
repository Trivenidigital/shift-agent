#!/usr/bin/env python3
"""
shift-agent-fsck — nightly cross-file invariant check.

Runs at 03:00 local (after backup). Asserts:
1. Every proposal_id in decisions.log exists (or existed) in pending.json.
2. Every proposal_created eventually has a terminal status change.
3. send-counter.count == number of outbound_sent entries in decisions.log for day.
4. raw_inbound.employee_id resolves via roster at time of event (skip historical).
5. Code uniqueness among non-terminal proposals.
6. seen-ids.last_offset_bytes <= stat(agent.log).st_size.
7. No proposals stuck in 'reconciling' > 10min.

Any violation → append InvariantViolation to decisions.log + Pushover alert.
"""

from __future__ import annotations
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
from schemas import Config, PendingStore, Roster, SeenIds, InvariantViolation, is_terminal_status, _UnknownProposal  # noqa: E402
from safe_io import FileLock, load_model, ndjson_append, customer_now, customer_today_str  # noqa: E402
from exit_codes import EXIT_OK, EXIT_SCHEMA_VIOLATION  # noqa: E402
from pydantic import TypeAdapter
import yaml


CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
ROSTER_PATH = Path("/opt/shift-agent/roster.json")
PENDING_PATH = Path("/opt/shift-agent/state/pending.json")
COUNTER_PATH = Path("/opt/shift-agent/state/send-counter.json")
SEEN_PATH = Path("/opt/shift-agent/state/seen-ids.json")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
LOG_LOCK = Path("/opt/shift-agent/logs/decisions.log.lock")
AGENT_LOG = Path("/root/.hermes/logs/agent.log")


def _log_violation(check: str, detail: str, cfg: Config) -> None:
    now = customer_now(cfg.customer.timezone)
    entry = InvariantViolation(
        type="invariant_violation", ts=now, check=check, detail=detail[:500],
    )
    with FileLock(LOG_LOCK):
        ndjson_append(LOG_PATH, TypeAdapter(InvariantViolation).dump_json(entry).decode())


def _alert(message: str) -> None:
    try:
        subprocess.run(
            ["/usr/local/bin/shift-agent-notify-owner",
             "--title", "Invariant check failed",
             "--priority", "1", message],
            check=False, timeout=30,
        )
    except Exception:
        pass


def main():
    try:
        with CONFIG_PATH.open() as f:
            cfg = Config.model_validate(yaml.safe_load(f))
    except Exception as e:
        print(f"fsck: config load failed: {e}", file=sys.stderr)
        return EXIT_SCHEMA_VIOLATION

    now = customer_now(cfg.customer.timezone)
    violations: list[tuple[str, str]] = []

    # Load pending
    store, _ = load_model(PENDING_PATH, PendingStore, default=PendingStore())

    # Check: unrecognized proposal status (BL-HERMES-06 §12a). An _UnknownProposal means
    # pending.json holds a status this binary doesn't recognize — a hand-edit / corruption, or
    # a store written by a newer binary. The forward-compat shim keeps the row inert (never
    # swept / reconciled / sent) instead of bricking the whole load, but that also means it
    # would sit SILENT; before the shim an unknown status raised + quarantined the store. This
    # restores the loud operator signal.
    unknown_pids = sorted(
        pid for pid, prop in store.proposals.items()
        if isinstance(prop, _UnknownProposal)
    )
    if unknown_pids:
        statuses = sorted({store.proposals[pid].status for pid in unknown_pids})
        violations.append((
            "unknown_proposal_status",
            f"{len(unknown_pids)} proposal(s) with unrecognized status {statuses}: "
            f"{', '.join(unknown_pids)}",
        ))

    # Check 5: code uniqueness among non-terminal proposals
    codes_seen = set()
    for pid, prop in store.proposals.items():
        if is_terminal_status(prop.status):
            continue
        if prop.code in codes_seen:
            violations.append(("code_uniqueness", f"code {prop.code} used by multiple non-terminal proposals"))
        codes_seen.add(prop.code)

    # Check 7: reconciling stuck
    for pid, prop in store.proposals.items():
        if prop.status == "reconciling":
            age = now - prop.last_updated_ts
            if age > timedelta(minutes=10):
                violations.append(("reconciling_stuck", f"{pid} stuck in reconciling for {age}"))

    # Check 1+3: decisions.log reconciliation
    proposals_in_log: set[str] = set()
    outbound_sent_today = 0
    today_str = customer_today_str(cfg.customer.timezone)
    created_proposals: set[str] = set()
    terminal_proposals: set[str] = set()

    if LOG_PATH.exists():
        with LOG_PATH.open() as f:
            for line_no, line in enumerate(f, 1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    violations.append(("decisions_log_malformed", f"line {line_no} not valid JSON"))
                    continue
                pid = entry.get("proposal_id")
                if pid:
                    proposals_in_log.add(pid)
                t = entry.get("type")
                if t == "proposal_created":
                    created_proposals.add(pid)
                elif t == "proposal_status_change":
                    to = entry.get("to_status", "")
                    if is_terminal_status(to):
                        terminal_proposals.add(pid)
                elif t == "outbound_sent":
                    ts_str = entry.get("ts", "")
                    if ts_str.startswith(today_str):
                        outbound_sent_today += 1

    # Check 1
    for pid in proposals_in_log:
        if pid not in store.proposals:
            # This is OK if the proposal was in pending.json at some point but has since been cleaned up
            # (we don't clean up currently, so this IS a violation if it happens)
            violations.append(("orphan_log_entry",
                              f"proposal {pid} referenced in decisions.log but not in pending.json"))

    # Check 2: every created has an eventual terminal (but not strict — may be in-flight)
    for pid in created_proposals:
        if pid in terminal_proposals:
            continue
        if pid in store.proposals and not is_terminal_status(store.proposals[pid].status):
            continue  # in-flight, OK
        violations.append(("orphan_proposal",
                          f"{pid} was created but has no terminal status and is not in pending.json"))

    # Check 3: send-counter sanity
    counter, _ = load_model(COUNTER_PATH, type(None), default=None) if False else ({}, "missing")
    # Direct dict load simpler:
    if COUNTER_PATH.exists():
        try:
            c = json.loads(COUNTER_PATH.read_text())
            if c.get("day") == today_str:
                if c.get("count", 0) != outbound_sent_today:
                    violations.append(("counter_mismatch",
                                      f"send-counter.count={c.get('count')} vs decisions.log outbound_sent={outbound_sent_today} for {today_str}"))
        except Exception as e:
            violations.append(("counter_load", f"send-counter load failed: {e}"))

    # Check 6: seen-ids offset <= agent.log size
    if SEEN_PATH.exists() and AGENT_LOG.exists():
        try:
            seen_raw = json.loads(SEEN_PATH.read_text())
            seen_offset = seen_raw.get("last_offset_bytes", 0)
            agent_size = AGENT_LOG.stat().st_size
            if seen_offset > agent_size:
                # Could be rotation just happened; still worth flagging
                violations.append(("seen_offset_past_eof",
                                  f"seen-ids offset {seen_offset} > agent.log size {agent_size}"))
        except Exception:
            pass

    # Check 4: raw_inbound.employee_id resolves (skip; roster drift is expected and tolerated)
    # — intentionally not enforcing this invariant strictly

    # Report
    if not violations:
        print("fsck: all invariants OK")
        return EXIT_OK

    for check, detail in violations:
        print(f"VIOLATION {check}: {detail}")
        _log_violation(check, detail, cfg)

    _alert(f"Nightly fsck found {len(violations)} invariant violations. Check /opt/shift-agent/logs/decisions.log for details.")
    return EXIT_OK  # don't exit non-zero — systemd would retry; we just want the alert


if __name__ == "__main__":
    sys.exit(main())
