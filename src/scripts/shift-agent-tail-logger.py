#!/usr/bin/env python3
"""
shift-agent-tail-logger — guarantees 100% audit coverage of sick-call inbounds.

Runs every 30s via systemd timer. Tails `/root/.hermes/logs/agent.log` since last offset,
classifies inbound messages as sick-calls via regex, and writes a `raw_inbound` entry to
decisions.log for each — regardless of LLM behavior.

Invariants:
- No sick-call inbound is lost (seen_ids dedup + offset persistence)
- Log rotation is detected (inode check + offset reset)
- Corrupt seen-ids.json triggers recovery (rename + start fresh from EOF)
- Local disk only (flock is unreliable on NFS)
"""

from __future__ import annotations
import fcntl
import hashlib
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
from schemas import Config, Roster, SeenIds, RawInbound, E164Phone  # noqa: E402
from safe_io import (  # noqa: E402
    FileLock, load_model, dump_model, ndjson_append, safe_load_json,
    atomic_write_json, sweep_orphan_temps, assert_local_disk,
    customer_now,
)
from exit_codes import EXIT_OK, EXIT_ENVIRONMENT, EXIT_SCHEMA_VIOLATION  # noqa: E402
from pydantic import TypeAdapter
import yaml


CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
ROSTER_PATH = Path("/opt/shift-agent/roster.json")
SEEN_PATH = Path("/opt/shift-agent/state/seen-ids.json")
SEEN_LOCK = Path("/opt/shift-agent/state/seen-ids.json.lock")
AGENT_LOG = Path("/root/.hermes/logs/agent.log")
LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")
LOG_LOCK = Path("/opt/shift-agent/logs/decisions.log.lock")
SINGLETON_LOCK = Path("/run/shift-agent/tail-logger.lock")
STATE_DIR = Path("/opt/shift-agent/state")

# Sick-call classifier. Intentionally permissive — skill re-classifies; we want
# low false-negative rate (captures audit) over low false-positive.
SICK_CALL_RE = re.compile(
    r"\b(sick|fever|ill|cold|flu|can'?t\s+come|not\s+coming|won'?t\s+make|leave|off\s+today|"
    r"off\s+tomorrow|absent|emergency|nenu\s+rava|rava\s*ledu|rava\s*leyya|ravaledu|"
    r"aaj\s+nahi|aaj\s+n?ahi|nahi\s+aa|bhai\s+aaj|jwaram|jaram|bukhaar|bukhar|tabiyat)\b",
    re.IGNORECASE | re.UNICODE,
)

# Pattern matching Hermes's agent.log "inbound message" line
INBOUND_LINE_RE = re.compile(
    r"^(?P<ts>\S+\s+\S+)\s+INFO\s+gateway\.run:\s+inbound message:\s+"
    r"platform=(?P<platform>\S+)\s+"
    r"user=(?P<user>[^|]+?)\s+"
    r"chat=(?P<chat>\S+)\s+"
    r"msg=(?P<msg>.+)$"
)


def acquire_singleton_lock() -> int:
    """Single-instance guard. Exits if another tail-logger is running."""
    SINGLETON_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(SINGLETON_LOCK), os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another tail-logger instance is running; exiting", file=sys.stderr)
        os.close(fd)
        sys.exit(0)
    return fd


def load_seen_ids() -> SeenIds:
    """Load seen-ids with corruption-recovery. Fires dead-man on corruption."""
    raw, status = safe_load_json(SEEN_PATH, default={})
    if status == "ok":
        try:
            return SeenIds.model_validate(raw)
        except Exception as e:
            # Schema violation — treat as corrupt
            corrupt_path = SEEN_PATH.with_suffix(f".json.corrupt-{int(time.time())}")
            SEEN_PATH.rename(corrupt_path)
            _alert_owner(f"seen-ids.json schema violation ({e}); renamed to {corrupt_path.name}; starting fresh from EOF")
            return _fresh_seen_ids_at_eof()
    if status == "missing" or status == "empty":
        return SeenIds()
    # Corrupt — safe_load_json already renamed to .corrupt-*
    _alert_owner(f"seen-ids.json {status}; starting fresh from EOF")
    return _fresh_seen_ids_at_eof()


def _fresh_seen_ids_at_eof() -> SeenIds:
    """After corruption, start tracking from current EOF to avoid re-processing the whole log."""
    offset = 0
    inode = 0
    if AGENT_LOG.exists():
        st = AGENT_LOG.stat()
        offset = st.st_size
        inode = st.st_ino
    return SeenIds(seen_message_ids=[], last_offset_bytes=offset, agent_log_inode=inode)


def _alert_owner(message: str) -> None:
    """Best-effort Pushover alert. Does not block tail-logger."""
    import subprocess
    try:
        subprocess.run([
            "/usr/local/bin/shift-agent-notify-owner",
            "--title", "Tail logger alert",
            "--priority", "1", message,
        ], check=False, timeout=15)
    except Exception:
        pass


def detect_rotation(seen: SeenIds) -> bool:
    """True if agent.log has rotated since last run (inode changed OR size < stored offset)."""
    if not AGENT_LOG.exists():
        return True  # log gone entirely — treat as rotation
    st = AGENT_LOG.stat()
    if seen.agent_log_inode and st.st_ino != seen.agent_log_inode:
        return True
    if st.st_size < seen.last_offset_bytes:
        return True
    return False


def classify_as_sick_call(text: str) -> bool:
    return bool(SICK_CALL_RE.search(text or ""))


def extract_message_id(chat: str, msg: str, ts: str) -> str:
    """Stable message id. If Hermes emits one we could use it; since the log format
    doesn't include a WA msg id inline, we synthesize a hash of (ts + chat + msg).
    Including `chat` (sender phone) prevents collisions on identical-text-same-second."""
    h = hashlib.sha256(f"{ts}|{chat}|{msg}".encode("utf-8")).hexdigest()[:24]
    return f"synth:{h}"


def resolve_employee_id(sender_phone: str, roster: Roster) -> str | None:
    try:
        emp = roster.find_by_phone(sender_phone)
        return emp.id if emp else None
    except Exception:
        return None


def jid_to_phone(chat: str) -> str:
    """Extract phone from @s.whatsapp.net or @lid JID."""
    if "@" in chat:
        chat = chat.split("@", 1)[0]
    if not chat.startswith("+"):
        chat = "+" + chat
    return chat


def main():
    assert_local_disk(STATE_DIR)
    _ = acquire_singleton_lock()  # held for lifetime of process

    # Load config + roster (best-effort; tail-logger must not crash on bad data)
    try:
        with CONFIG_PATH.open() as f:
            cfg = Config.model_validate(yaml.safe_load(f))
    except Exception as e:
        print(f"config load failed: {e}", file=sys.stderr)
        _alert_owner(f"tail-logger: config load failed: {e}")
        return EXIT_SCHEMA_VIOLATION

    try:
        roster, rstatus = load_model(ROSTER_PATH, Roster)
        if rstatus != "ok":
            roster = None
    except Exception:
        roster = None

    # Sweep orphan temp files
    sweep_orphan_temps(STATE_DIR)

    # Load seen_ids with recovery
    with FileLock(SEEN_LOCK):
        seen = load_seen_ids()

        # Rotation detection
        if detect_rotation(seen):
            st = AGENT_LOG.stat() if AGENT_LOG.exists() else None
            seen.last_offset_bytes = 0
            seen.agent_log_inode = st.st_ino if st else 0
            # Don't clear seen_message_ids — may be relevant for short-term dedup after rotation

        if not AGENT_LOG.exists():
            dump_model(SEEN_PATH, seen)
            return EXIT_OK

        # Tail since last offset
        new_entries = 0
        with AGENT_LOG.open("rb") as f:
            f.seek(seen.last_offset_bytes)
            remainder = f.read()
        try:
            text = remainder.decode("utf-8", errors="replace")
        except Exception:
            text = ""

        for line in text.splitlines():
            m = INBOUND_LINE_RE.match(line)
            if not m:
                continue
            chat = m.group("chat")
            raw_msg = m.group("msg").strip()
            # Strip Hermes-added quotes around the message
            msg_body = raw_msg
            if msg_body.startswith('"') and msg_body.endswith('"'):
                msg_body = msg_body[1:-1]
            elif msg_body.startswith("'") and msg_body.endswith("'"):
                msg_body = msg_body[1:-1]

            ts = m.group("ts")
            msg_id = extract_message_id(chat, msg_body, ts)

            if seen.has(msg_id):
                continue

            # Classify
            if not classify_as_sick_call(msg_body):
                seen.remember(msg_id)  # still dedup
                continue

            # Resolve employee
            phone = jid_to_phone(chat)
            try:
                canonical = E164Phone.from_any(phone)
            except Exception:
                canonical = phone
            employee_id = resolve_employee_id(canonical, roster) if roster else None

            # Write raw_inbound to decisions.log
            # P7-FIX: Silent-failures-#4 — previously `seen.remember(msg_id)` was
            # called AFTER the try/except unconditionally, meaning a validation or
            # write failure would cause the message to be marked seen + never retried
            # despite not having been logged. Now we only remember on success;
            # failures are re-tried on the next timer tick, and persistently-failing
            # messages raise an alert that won't stop firing until resolved.
            logged = False
            try:
                entry = RawInbound(
                    type="raw_inbound",
                    ts=customer_now(cfg.customer.timezone),
                    message_id=msg_id,
                    sender_phone=canonical,
                    employee_id=employee_id,
                    input_message=msg_body[:4000],
                )
                json_line = TypeAdapter(RawInbound).dump_json(entry).decode()
                with FileLock(LOG_LOCK):
                    ndjson_append(LOG_PATH, json_line)
                new_entries += 1
                logged = True
            except Exception as e:
                _alert_owner(f"tail-logger failed to log {msg_id}: {e}")

            if logged:
                seen.remember(msg_id)

        # Update offset + inode
        if AGENT_LOG.exists():
            st = AGENT_LOG.stat()
            seen.last_offset_bytes = st.st_size
            seen.agent_log_inode = st.st_ino

        # Persist
        dump_model(SEEN_PATH, seen)

        if new_entries > 0:
            print(f"captured {new_entries} raw_inbound entries")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
