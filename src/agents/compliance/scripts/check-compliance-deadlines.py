#!/usr/bin/env python3
"""check-compliance-deadlines.py — Agent #13 daily reminder cron.

Triggered by check-compliance-deadlines.timer at 06:00 customer-local
(TZ via service Environment=TZ=...).

Idempotency (3 layers, mirror send-daily-brief):
  1. FileLock(state/compliance-check.json.lock) — concurrent timer fires
  2. Sentinel state/compliance-last-sent.json keyed by (item_id, gate_days)
  3. ComplianceReminderAttempted audit BEFORE bridge POST — orphan-without-Sent
     scan on next tick refuses to re-fire (operator manual-verify required)

Catch-up + deferral semantics:
  - If gate fired late but ≤ cfg.compliance.max_deferral_days days late:
    fire, audit with catchup_for_missed_gate=<gate>
  - If gate >max_deferral_days late: emit ComplianceReminderDeferred + Pushover;
    do NOT fire (avoid spamming for stale-gate work)

CLI:
  --dry-run               aggregate + log; no bridge POST
  --force                 bypass sentinel (test only)

Env overrides (for tests):
  SHIFT_AGENT_CONFIG_PATH, SHIFT_AGENT_COMPLIANCE_ITEMS_PATH,
  SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH, SHIFT_AGENT_COMPLIANCE_HEARTBEAT_PATH,
  SHIFT_AGENT_COMPLIANCE_LOCK_PATH, SHIFT_AGENT_DECISIONS_LOG_PATH,
  SHIFT_AGENT_NOW_OVERRIDE
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Bootstrap sys.path for both deployed (/opt/shift-agent) and dev contexts.
sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "platform"))

from safe_io import (  # noqa: E402
    FileLock, atomic_write_json, ndjson_append, customer_now,
    safe_load_json, load_model, assert_local_disk,
    notify_owner_with_fallback,
    bridge_post, BRIDGE_RETRY_DELAY_SEC,
)
from schemas import (  # noqa: E402
    Config, ComplianceItem, ComplianceItemsFile, ComplianceLastSentFile,
    ComplianceReminderAttempted, ComplianceReminderSent,
    ComplianceReminderFailed, ComplianceReminderSkipped,
    ComplianceReminderDeferred, InvariantViolation,
)
from exit_codes import (  # noqa: E402
    EXIT_OK, EXIT_GENERIC_ERROR,
)
from audit_helpers import _append_best_effort  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# Module constants (code-enforced bounds, not prose-promised)
# ─────────────────────────────────────────────────────────────────

COMPLIANCE_ATTEMPT_RECOVERY_WINDOW_MIN = 60
HEARTBEAT_STALE_THRESHOLD_HOURS = 28
DEFERRAL_PUSHOVER_PRIORITY = 1
MAX_LOG_LINE_BYTES = 64 * 1024  # mirror send-daily-brief

CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH", "/opt/shift-agent/config.yaml"))
ITEMS_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_ITEMS_PATH",
                                  "/opt/shift-agent/state/compliance-items.json"))
SENTINEL_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_SENTINEL_PATH",
                                     "/opt/shift-agent/state/compliance-last-sent.json"))
HEARTBEAT_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_HEARTBEAT_PATH",
                                      "/opt/shift-agent/state/compliance-last-cron-tick.json"))
LOCK_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_LOCK_PATH",
                                 "/opt/shift-agent/state/compliance-check.json.lock"))
DECISIONS_LOG = Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH",
                                     "/opt/shift-agent/logs/decisions.log"))
RENDER_TEMPLATE_BIN = os.environ.get("SHIFT_AGENT_RENDER_TEMPLATE_BIN",
                                      "/usr/local/bin/render-coverage-template")


# ─────────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────────

def _customer_now(tz_name: str) -> datetime:
    """Tz-aware datetime in customer tz, with SHIFT_AGENT_NOW_OVERRIDE for tests."""
    override = os.environ.get("SHIFT_AGENT_NOW_OVERRIDE", "")
    if override:
        return datetime.fromisoformat(override)
    return customer_now(tz_name)


def _today_local(tz_name: str) -> date:
    return _customer_now(tz_name).date()


# ─────────────────────────────────────────────────────────────────
# Audit helpers (best-effort with stderr + Pushover fallback)
# ─────────────────────────────────────────────────────────────────

def _log_compliance_entry(entry, log_path: Path = DECISIONS_LOG) -> None:
    """Best-effort audit append. Falls back to stderr + Pushover on failure."""
    try:
        _append_best_effort(entry.model_dump_json(), log_path)
    except Exception as e:
        sys.stderr.write(f"compliance audit log write failed: {e}\n")
        try:
            notify_owner_with_fallback(
                title="Compliance: log write failed",
                message=f"{getattr(entry, 'type', 'unknown')}: {e}",
                priority=2, source="check-compliance-deadlines",
            )
        except Exception:
            pass  # double-fault — give up; stderr already captured by journald


def _emit_invariant(check_name: str, detail: str) -> None:
    """Append InvariantViolation; never raises."""
    entry = InvariantViolation(
        type="invariant_violation",
        ts=datetime.now(timezone.utc),
        check=check_name,
        detail=detail[:500],
    )
    _log_compliance_entry(entry)


# ─────────────────────────────────────────────────────────────────
# Sentinel state IO
# ─────────────────────────────────────────────────────────────────

def _load_sentinel() -> ComplianceLastSentFile:
    """Load sentinel; return empty default if missing/corrupt."""
    try:
        obj, status = load_model(SENTINEL_PATH, ComplianceLastSentFile)
        return obj
    except Exception:
        # Corrupt sentinel — emit invariant + return empty (treat as no prior sends)
        _emit_invariant("compliance_sentinel_corrupt",
                        f"loading {SENTINEL_PATH}; treating as empty")
        return ComplianceLastSentFile()


def _save_sentinel(s: ComplianceLastSentFile) -> None:
    SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(SENTINEL_PATH, s.model_dump(mode="json"))


def _prune_sentinel(
    sentinel: ComplianceLastSentFile,
    items: list[ComplianceItem],
    advance_warning_days: list[int],
) -> tuple[ComplianceLastSentFile, int]:
    """Drop sentinel keys whose item_id no longer in items OR whose gate_days
    no longer in advance_warning_days + [0]. Returns (updated, n_dropped).

    Negative gates (overdue) are kept for any item that still exists.
    """
    valid_item_ids = {it.id for it in items}
    valid_gates = set(advance_warning_days) | {0}
    keep: dict[str, str] = {}
    dropped = 0
    for k, v in sentinel.last_sent.items():
        try:
            iid, gate_str = k.rsplit(":", 1)
            gate = int(gate_str)
        except (ValueError, IndexError):
            dropped += 1
            continue
        if iid not in valid_item_ids:
            dropped += 1
            continue
        if gate >= 0 and gate not in valid_gates:
            dropped += 1
            continue
        keep[k] = v
    return ComplianceLastSentFile(schema_version=1, last_sent=keep), dropped


# ─────────────────────────────────────────────────────────────────
# Orphan-Attempted scan (Layer 3 idempotency, mirror send-daily-brief:143-191)
# ─────────────────────────────────────────────────────────────────

def _scan_recent_compliance_attempted(
    now_local: datetime,
) -> tuple[dict[tuple[str, int], str], bool]:
    """Return (orphan_map, scan_failed).

    orphan_map = {(item_id, gate_days): attempt_id, ...} of
    Attempted-without-Sent within the recovery window. The aid value lets the
    Skipped audit reference the original orphan. scan_failed=True ⇒ caller
    treats as uncertain and refuses to fire (FAIL CLOSED).
    """
    if not DECISIONS_LOG.exists():
        return {}, False
    cutoff = now_local - timedelta(minutes=COMPLIANCE_ATTEMPT_RECOVERY_WINDOW_MIN)
    attempted: dict[str, tuple[str, int, datetime]] = {}
    sent_aids: set[str] = set()
    try:
        with open(DECISIONS_LOG, "r", encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline(MAX_LOG_LINE_BYTES + 1)
                if not line:
                    break
                if len(line.encode("utf-8", errors="replace")) > MAX_LOG_LINE_BYTES:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = e.get("type")
                if t == "compliance_reminder_attempted":
                    aid = e.get("attempt_id", "")
                    iid = e.get("item_id", "")
                    gate = e.get("gate_days")
                    ts_str = e.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts >= cutoff and aid and iid and gate is not None:
                        attempted[aid] = (iid, int(gate), ts)
                elif t == "compliance_reminder_sent":
                    sent_aids.add(e.get("attempt_id", ""))
    except OSError as e:
        sys.stderr.write(f"WARN: scan recent ComplianceReminderAttempted failed: {e}\n")
        return {}, True

    orphan_map: dict[tuple[str, int], str] = {}
    for aid, (iid, gate, _ts) in attempted.items():
        if aid not in sent_aids:
            # If multiple orphans for same (iid,gate), keep the first-seen aid.
            orphan_map.setdefault((iid, gate), aid)
    return orphan_map, False


# ─────────────────────────────────────────────────────────────────
# Candidate building
# ─────────────────────────────────────────────────────────────────

def _build_candidates(
    items: list[ComplianceItem],
    advance_warning_days: list[int],
    sentinel: ComplianceLastSentFile,
    max_deferral_days: int,
    today: date,
    force: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Returns (fire_list, defer_list). See module docstring for semantics."""
    fire: list[dict] = []
    defer: list[dict] = []
    gates = sorted(set(advance_warning_days), reverse=True) + [0]
    today_str = today.isoformat()
    for item in items:
        for gate in gates:
            ideal_fire = item.renewal_date - timedelta(days=gate)
            days_late = (today - ideal_fire).days
            if days_late < 0:
                continue
            key = f"{item.id}:{gate}"
            if not force:
                last = sentinel.last_sent.get(key)
                if last:
                    try:
                        last_d = date.fromisoformat(last)
                    except ValueError:
                        last_d = None
                    if last_d and last_d >= ideal_fire:
                        continue  # already fired for this renewal cycle
            if days_late > max_deferral_days:
                defer.append({
                    "item": item, "gate_days": gate,
                    "days_since_ideal_fire": days_late,
                })
                continue
            fire.append({
                "item": item, "gate_days": gate,
                "days_late": days_late,
                "catchup_for_missed_gate": gate if days_late > 0 else None,
            })
        # Overdue-day-of: gate_days = -days_overdue (one fire per day, bounded)
        days_overdue = (today - item.renewal_date).days
        if days_overdue > 0:
            gate = -days_overdue
            key = f"{item.id}:{gate}"
            if not force and sentinel.last_sent.get(key) == today_str:
                continue
            if days_overdue <= max_deferral_days:
                fire.append({
                    "item": item, "gate_days": gate,
                    "days_late": 0, "catchup_for_missed_gate": None,
                })
    return fire, defer


# ─────────────────────────────────────────────────────────────────
# Render + send
# ─────────────────────────────────────────────────────────────────

def _render_reminder(item: ComplianceItem, gate_days: int) -> Optional[str]:
    """Pre-render conditional fields, then call render-coverage-template
    (Python str.format — verified at render-coverage-template:91-95)."""
    if gate_days > 0:
        days_text = f"{gate_days} days"
    elif gate_days == 0:
        days_text = "DUE TODAY"
    else:
        days_text = f"OVERDUE by {-gate_days} days"
    fields = {
        "name": item.name,
        "days_until_text": days_text,
        "renewal_date": item.renewal_date.isoformat(),
        "agency": item.agency or "Not specified",
        "resource_line": f"\nLink: {item.resource_url}" if item.resource_url else "",
        "notes_line": f"\nNotes: {item.notes}" if item.notes else "",
        "item_id": item.id,
    }
    try:
        result = subprocess.run(
            [RENDER_TEMPLATE_BIN, "compliance_reminder",
             "--fields-json", json.dumps(fields)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            sys.stderr.write(f"render failed: {result.stderr[:500]}\n")
            return None
        return result.stdout
    except (subprocess.SubprocessError, OSError) as e:
        sys.stderr.write(f"render exception: {e}\n")
        return None


def _send_reminder(
    item: ComplianceItem, gate_days: int, days_until: int,
    catchup: Optional[int], cfg: Config, dry_run: bool,
) -> tuple[bool, str]:
    """Returns (success, message_id_or_error). Writes Attempted/Sent/Failed audits."""
    attempt_id = uuid.uuid4().hex
    tz = cfg.customer.timezone
    now_local = _customer_now(tz)

    _log_compliance_entry(ComplianceReminderAttempted(
        type="compliance_reminder_attempted", ts=now_local,
        item_id=item.id, item_name=item.name,
        days_until_renewal=days_until, gate_days=gate_days,
        attempt_id=attempt_id, catchup_for_missed_gate=catchup,
    ))

    if dry_run:
        return True, "dry-run"

    rendered = _render_reminder(item, gate_days)
    if rendered is None:
        _log_compliance_entry(ComplianceReminderFailed(
            type="compliance_reminder_failed", ts=now_local,
            item_id=item.id, days_until_renewal=days_until, gate_days=gate_days,
            attempt_id=attempt_id, error="template_render_failed", retry_count=0,
        ))
        return False, "render_failed"

    ok, msg_id, err, status = bridge_post(cfg.owner.self_chat_jid, rendered)
    retry_count = 0
    if not ok and status not in ("send_uncertain",):
        time.sleep(BRIDGE_RETRY_DELAY_SEC)
        ok, msg_id, err, status = bridge_post(cfg.owner.self_chat_jid, rendered)
        retry_count = 1

    if ok:
        _log_compliance_entry(ComplianceReminderSent(
            type="compliance_reminder_sent", ts=now_local,
            item_id=item.id, days_until_renewal=days_until, gate_days=gate_days,
            attempt_id=attempt_id, outbound_message_id=msg_id,
        ))
        return True, msg_id
    else:
        _log_compliance_entry(ComplianceReminderFailed(
            type="compliance_reminder_failed", ts=now_local,
            item_id=item.id, days_until_renewal=days_until, gate_days=gate_days,
            attempt_id=attempt_id, error=f"{status}: {err}", retry_count=retry_count,
        ))
        return False, err


def _emit_deferral(item: ComplianceItem, gate_days: int, days_late: int, tz: str) -> None:
    """Emit ComplianceReminderDeferred + Pushover. Both protected against raise."""
    pushover_ok = False
    try:
        pushover_ok = notify_owner_with_fallback(
            title=f"Compliance reminder DEFERRED — {item.name}",
            message=(
                f"The {gate_days}-day-out reminder for '{item.name}' "
                f"could not be sent for {days_late} days "
                f"(>cfg.compliance.max_deferral_days). Item renewal is "
                f"{item.renewal_date.isoformat()}. Verify status manually."
            ),
            priority=DEFERRAL_PUSHOVER_PRIORITY,
            source="check-compliance-deadlines",
        )
    except Exception as e:
        sys.stderr.write(f"notify_owner_with_fallback raised: {e}\n")
        pushover_ok = False

    days_until = (item.renewal_date - _today_local(tz)).days
    _log_compliance_entry(ComplianceReminderDeferred(
        type="compliance_reminder_deferred", ts=_customer_now(tz),
        item_id=item.id, days_until_renewal=days_until,
        gate_days=gate_days, days_since_ideal_fire=days_late,
        operator_pushover_sent=pushover_ok,
    ))


def _emit_skipped_orphan(item_id: str, gate_days: int, orphan_aid: str, tz: str) -> None:
    _log_compliance_entry(ComplianceReminderSkipped(
        type="compliance_reminder_skipped", ts=_customer_now(tz),
        item_id=item_id, gate_days=gate_days,
        reason="orphan_attempted_in_window", orphan_attempt_id=orphan_aid,
    ))


# ─────────────────────────────────────────────────────────────────
# Heartbeat
# ─────────────────────────────────────────────────────────────────

def _update_heartbeat(items_scanned: int, reminders_sent: int, deferrals: int) -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(HEARTBEAT_PATH, {
        "last_tick_utc": datetime.now(timezone.utc).isoformat(),
        "items_scanned": items_scanned,
        "reminders_sent_today": reminders_sent,
        "deferrals_today": deferrals,
    })


# ─────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Compliance Calendar daily reminder check")
    ap.add_argument("--dry-run", action="store_true", help="aggregate + log; no bridge POST")
    ap.add_argument("--force", action="store_true", help="bypass sentinel (test only)")
    args = ap.parse_args()

    assert_local_disk(SENTINEL_PATH.parent)
    cfg, _ = load_model(CONFIG_PATH, Config)
    if not cfg.compliance.enabled:
        _update_heartbeat(0, 0, 0)
        return EXIT_OK

    tz = cfg.customer.timezone
    now_local = _customer_now(tz)
    today = now_local.date()

    # Load items (recovery: corrupt → InvariantViolation + heartbeat-only mode)
    try:
        items_file, _ = load_model(ITEMS_PATH, ComplianceItemsFile)
    except (RuntimeError, FileNotFoundError) as e:
        _emit_invariant("compliance_items_file_load",
                        f"{ITEMS_PATH}: {type(e).__name__}: {e}")
        _update_heartbeat(0, 0, 0)
        return EXIT_GENERIC_ERROR
    except Exception as e:
        _emit_invariant("compliance_items_file_corrupt",
                        f"{ITEMS_PATH}: {type(e).__name__}: {e}")
        _update_heartbeat(0, 0, 0)
        return EXIT_GENERIC_ERROR

    # Layer 3 idempotency: scan for orphan Attempted-without-Sent.
    # FAIL CLOSED if scan unreadable.
    orphan_map, scan_failed = _scan_recent_compliance_attempted(now_local)
    if scan_failed:
        try:
            notify_owner_with_fallback(
                title="Compliance: log-scan failed",
                message=("check-compliance-deadlines cannot verify idempotency; "
                         "refusing to send. Re-run after disk-health verification."),
                priority=2, source="check-compliance-deadlines",
            )
        except Exception:
            pass
        _update_heartbeat(len(items_file.items), 0, 0)
        return EXIT_GENERIC_ERROR

    # Per-tick sentinel GC + candidate computation under lock (no network in lock)
    SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(LOCK_PATH):
        sentinel = _load_sentinel()
        sentinel, n_dropped = _prune_sentinel(
            sentinel, items_file.items, cfg.compliance.advance_warning_days,
        )
        if n_dropped:
            _save_sentinel(sentinel)
        fire_list, defer_list = _build_candidates(
            items_file.items, cfg.compliance.advance_warning_days,
            sentinel, cfg.compliance.max_deferral_days, today, force=args.force,
        )

    # Process deferrals first (no network beyond Pushover)
    for d in defer_list:
        _emit_deferral(d["item"], d["gate_days"], d["days_since_ideal_fire"], tz)

    # Process fires
    sent_count = 0
    for c in fire_list:
        item = c["item"]
        gate_days = c["gate_days"]
        days_until = (item.renewal_date - today).days
        # If this (item, gate) has an orphan Attempted, skip + emit Skipped audit
        orphan_key = (item.id, gate_days)
        if orphan_key in orphan_map:
            _emit_skipped_orphan(item.id, gate_days, orphan_map[orphan_key], tz)
            continue
        # Re-check sentinel inside per-fire lock to close the race window
        with FileLock(LOCK_PATH):
            sentinel = _load_sentinel()
            key = f"{item.id}:{gate_days}"
            if not args.force and sentinel.last_sent.get(key) == today.isoformat():
                continue  # Another writer beat us
        ok, _ = _send_reminder(
            item, gate_days, days_until,
            c["catchup_for_missed_gate"], cfg, args.dry_run,
        )
        if ok and not args.dry_run:
            with FileLock(LOCK_PATH):
                sentinel = _load_sentinel()
                sentinel.last_sent[f"{item.id}:{gate_days}"] = today.isoformat()
                _save_sentinel(sentinel)
            sent_count += 1

    _update_heartbeat(len(items_file.items), sent_count, len(defer_list))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
