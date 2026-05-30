"""End-to-end-ish tests for the send-daily-brief script. Requires Linux (fcntl).

Pattern follows tests/test_lid_learn.py — subprocess-invocation against fixture
files via env-var-overridable paths. Mocks the bridge with a tiny HTTP server.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="send-daily-brief depends on safe_io which uses fcntl (Linux only)",
)

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "agents" / "daily_brief" / "scripts" / "send-daily-brief"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "daily_brief" / "templates"


class _BridgeStub(BaseHTTPRequestHandler):
    """Mock bridge — returns 200 with messageId by default, configurable via class state."""
    response_mode: str = "ok"  # 'ok' | 'http_500' | 'empty_id' | 'non_json' | 'connect_refused' (skip server)
    requests_received: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except Exception:
            doc = {}
        self.__class__.requests_received.append(doc)
        mode = self.__class__.response_mode
        if mode == "http_500":
            self.send_response(500)
            self.end_headers()
            return
        if mode == "non_json":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"not json")
            return
        if mode == "empty_id":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{}')
            return
        # default ok
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode("utf-8"))

    def log_message(self, format, *args):
        return  # silence


@pytest.fixture
def bridge_server():
    """Start the stub bridge on a free port, yield (port, requests_list)."""
    _BridgeStub.response_mode = "ok"
    _BridgeStub.requests_received = []
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, _BridgeStub
    finally:
        server.shutdown()


@pytest.fixture
def fixture_dir(tmp_path):
    """Build the on-disk artifacts a brief run needs."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    config = {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01", "timezone": "America/New_York"},
        "owner": {
            "name": "Owner", "phone": "+19045550100",
            "self_chat_jid": "19045550100@s.whatsapp.net",
        },
        "limits": {},
        "alerting": {"pushover_user_key": "test_k", "pushover_app_token": "test_t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "daily_brief": {"brief_time": "07:00", "catchup_window_minutes": 180},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    roster = {
        "location": {"id": "loc_jax_01", "name": "Triveni", "timezone": "America/New_York"},
        "employees": [
            {"id": "e001", "name": "Ravi", "role": "cashier", "phone": "+19045550101",
             "languages": ["en"], "can_cover_roles": ["cashier"]},
        ],
        "schedule": {},
    }
    (tmp_path / "roster.json").write_text(json.dumps(roster), encoding="utf-8")
    pending = {"proposals": []}
    (state / "pending.json").write_text(json.dumps(pending), encoding="utf-8")
    decisions = logs / "decisions.log"
    decisions.write_text("", encoding="utf-8")
    return tmp_path


def _run(fixture_dir, bridge_port=None, args=("--force",), now_override=None,
         disabled_flag=False, notify_owner_stub=None):
    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(fixture_dir / "config.yaml"),
        "SHIFT_AGENT_ROSTER_PATH": str(fixture_dir / "roster.json"),
        "SHIFT_AGENT_PENDING_PATH": str(fixture_dir / "state" / "pending.json"),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(fixture_dir / "logs" / "decisions.log"),
        "SHIFT_AGENT_BRIEF_SENTINEL_PATH": str(fixture_dir / "state" / "last-brief-sent.json"),
        "SHIFT_AGENT_DISABLED_FLAG": str(fixture_dir / "state" / "disabled.flag"),
        "SHIFT_AGENT_TEMPLATES_DIR": str(TEMPLATES_DIR),
        "SHIFT_AGENT_LOG_SOURCE_OVERRIDE": str(fixture_dir / "logs" / "decisions.log"),
        "SHIFT_AGENT_NOTIFY_FAILED_LOG": str(fixture_dir / "logs" / "notify-failed.log"),
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src" / "platform"),
    }
    if bridge_port is not None:
        env["HERMES_BRIDGE_URL"] = f"http://127.0.0.1:{bridge_port}/send"
        # send-path-test-harness: opt past the pytest bridge guard so the send
        # reaches the local stub (env inherits PYTEST_CURRENT_TEST). send-daily-brief
        # is an allowlisted null-context caller; stub port (not :3000) keeps the
        # live-bridge tripwire dormant.
        env["SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS"] = "1"
    if now_override is not None:
        env["SHIFT_AGENT_NOW_OVERRIDE"] = now_override
    if notify_owner_stub is not None:
        env["SHIFT_AGENT_NOTIFY_OWNER_BIN"] = notify_owner_stub
    else:
        # /bin/true — non-fatal "succeeded" stub
        env["SHIFT_AGENT_NOTIFY_OWNER_BIN"] = "/bin/true"
    if disabled_flag:
        (fixture_dir / "state" / "disabled.flag").write_text("disabled-for-test", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True, text=True, env=env, timeout=20,
    )


def _read_log(fixture_dir):
    log = fixture_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ─── Tests ──────────────────────────────────────────────────────────


def test_dry_run_renders_no_send(fixture_dir):
    r = _run(fixture_dir, args=("--force", "--dry-run"))
    assert r.returncode == 0, r.stderr
    assert "Daily Brief" in r.stdout
    assert "07:00" not in r.stdout  # brief_time not in body
    # No log entries written in dry-run
    assert _read_log(fixture_dir) == []


def test_force_send_happy_path(fixture_dir, bridge_server):
    port, _ = bridge_server
    r = _run(fixture_dir, bridge_port=port, args=("--force",))
    assert r.returncode == 0, r.stderr
    log = _read_log(fixture_dir)
    types = [e["type"] for e in log]
    assert "brief_attempted" in types
    assert "brief_sent" in types
    # Sentinel file written
    sentinel = fixture_dir / "state" / "last-brief-sent.json"
    assert sentinel.exists()
    sdata = json.loads(sentinel.read_text())
    assert "brief_date" in sdata


def test_idempotent_second_run_skips(fixture_dir, bridge_server):
    port, _ = bridge_server
    r1 = _run(fixture_dir, bridge_port=port, args=("--force",))
    assert r1.returncode == 0, r1.stderr
    r2 = _run(fixture_dir, bridge_port=port, args=("--force",))
    assert r2.returncode == 0
    log = _read_log(fixture_dir)
    assert sum(1 for e in log if e["type"] == "brief_sent") == 1
    assert any(e["type"] == "brief_skipped" and e["reason"] == "already_sent" for e in log)


def test_force_resend_bypasses_idempotency(fixture_dir, bridge_server):
    port, _ = bridge_server
    _run(fixture_dir, bridge_port=port, args=("--force",))
    r = _run(fixture_dir, bridge_port=port, args=("--force", "--force-resend"))
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    # Should now have 2 brief_sent entries
    assert sum(1 for e in log if e["type"] == "brief_sent") == 2


def test_force_resend_requires_force(fixture_dir, bridge_server):
    port, _ = bridge_server
    r = _run(fixture_dir, bridge_port=port, args=("--force-resend",))
    assert r.returncode != 0  # rejects without --force


def test_disabled_flag_short_circuits(fixture_dir, bridge_server):
    port, _ = bridge_server
    r = _run(fixture_dir, bridge_port=port, args=("--force",), disabled_flag=True)
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    assert any(e["type"] == "brief_skipped" and e["reason"] == "disabled" for e in log)
    assert not any(e["type"] == "brief_sent" for e in log)


def test_missing_self_chat_jid_exits_not_found(fixture_dir, bridge_server, tmp_path):
    port, _ = bridge_server
    cfg_path = fixture_dir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["owner"]["self_chat_jid"] = ""
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    r = _run(fixture_dir, bridge_port=port, args=("--force",))
    assert r.returncode == 4  # EXIT_NOT_FOUND
    log = _read_log(fixture_dir)
    assert not any(e["type"] == "brief_sent" for e in log)


def test_bridge_failure_logs_brief_send_failed(fixture_dir, bridge_server):
    port, BridgeStub = bridge_server
    BridgeStub.response_mode = "http_500"
    r = _run(fixture_dir, bridge_port=port, args=("--force",))
    assert r.returncode == 6  # EXIT_DEPENDENCY_DOWN
    log = _read_log(fixture_dir)
    types = [e["type"] for e in log]
    assert "brief_attempted" in types
    assert "brief_send_failed" in types
    assert not any(t == "brief_sent" for t in types)


def test_self_gate_before_window_silent_exit(fixture_dir, bridge_server):
    port, _ = bridge_server
    # 06:30 EDT (utc-4 offset) — well before 07:00 brief_time
    r = _run(
        fixture_dir, bridge_port=port,
        args=(),  # NO --force — let self-gate fire
        now_override="2026-04-28T06:30:00-04:00",
    )
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    # Silent exit — no log entries
    assert log == []


def test_self_gate_inside_window(fixture_dir, bridge_server):
    port, _ = bridge_server
    # 07:05 EDT — inside [07:00, 07:15) window
    r = _run(
        fixture_dir, bridge_port=port,
        args=(),  # no --force needed
        now_override="2026-04-28T07:05:00-04:00",
    )
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    assert any(e["type"] == "brief_sent" for e in log)


def test_self_gate_in_catchup_with_late_marker(fixture_dir, bridge_server):
    port, _ = bridge_server
    # 09:00 EDT — past 15-min window (07:15) but within 3h catchup (10:00)
    r = _run(
        fixture_dir, bridge_port=port,
        args=(),  # no --force
        now_override="2026-04-28T09:00:00-04:00",
    )
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    attempted = [e for e in log if e["type"] == "brief_attempted"]
    assert len(attempted) == 1
    assert attempted[0]["catchup_minutes_late"] == 120  # 9:00 - 7:00 = 120 min
    assert any(e["type"] == "brief_sent" for e in log)


def test_self_gate_past_catchup_no_send(fixture_dir, bridge_server):
    port, _ = bridge_server
    # 11:00 EDT — past 3h catchup window
    r = _run(
        fixture_dir, bridge_port=port,
        args=(),
        now_override="2026-04-28T11:00:00-04:00",
    )
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    assert any(e["type"] == "brief_skipped" and e["reason"] == "catchup_expired" for e in log)
    assert not any(e["type"] == "brief_sent" for e in log)


def test_aggregation_counts_yesterday_entries(fixture_dir, bridge_server):
    """Seed decisions.log with yesterday entries; brief should count them."""
    port, _ = bridge_server
    log_path = fixture_dir / "logs" / "decisions.log"
    yesterday = datetime(2026, 4, 27, 14, 0, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=-4)))
    entries = [
        {"type": "raw_inbound", "ts": yesterday.isoformat(), "message_id": "m1",
         "sender_phone": "+19045550101", "body": "sick"},
        {"type": "proposal_created", "ts": yesterday.isoformat(),
         "proposal_id": "p1", "candidate_employee_id": "e001", "approval_code": "#A1"},
        {"type": "outbound_sent", "ts": yesterday.isoformat(),
         "proposal_id": "p1", "to_phone": "+19045550102", "outbound_message_id": "m_out"},
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    r = _run(
        fixture_dir, bridge_port=port,
        args=("--force",),
        now_override="2026-04-28T07:05:00-04:00",
    )
    assert r.returncode == 0, r.stderr
    log = _read_log(fixture_dir)
    attempted = [e for e in log if e["type"] == "brief_attempted"]
    assert len(attempted) == 1
    # source_count = sum of all categorized counts (1 sick + 1 proposal + 1 outbound = 3)
    assert attempted[0]["source_count"] >= 3


def test_uncertain_send_after_crash_does_not_resend(fixture_dir, bridge_server):
    """Simulate crash: BriefAttempted exists but no BriefSent. Next run must NOT resend."""
    port, _ = bridge_server
    log_path = fixture_dir / "logs" / "decisions.log"
    # Write a "stranded" BriefAttempted from 5 min ago
    now = datetime(2026, 4, 28, 7, 5, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=-4)))
    five_min_ago = (now - __import__("datetime").timedelta(minutes=5)).isoformat()
    log_path.write_text(json.dumps({
        "type": "brief_attempted", "ts": five_min_ago,
        "brief_date": "2026-04-28", "attempt_id": "stranded123",
        "word_count": 50, "sections_included": ["yesterday", "today_outlook", "alerts"],
        "source_count": 0, "degraded_mode": False, "catchup_minutes_late": 0,
    }) + "\n", encoding="utf-8")
    r = _run(
        fixture_dir, bridge_port=port,
        args=("--force",),
        now_override="2026-04-28T07:05:00-04:00",
    )
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    assert any(e["type"] == "brief_skipped" and e["reason"] == "send_uncertain" for e in log)
    # Crucially: no NEW brief_sent appended
    sent = [e for e in log if e["type"] == "brief_sent"]
    assert len(sent) == 0


def test_brief_text_contains_template_sections(fixture_dir, bridge_server):
    port, BridgeStub = bridge_server
    _run(fixture_dir, bridge_port=port, args=("--force", "--dry-run"))
    # Capture stdout from --dry-run
    r = _run(fixture_dir, bridge_port=port, args=("--force", "--dry-run"))
    assert "*Yesterday:*" in r.stdout
    assert "*Today's outlook:*" in r.stdout
    assert "*Needs your attention:*" in r.stdout
    assert "Daily Brief" in r.stdout


def test_quiet_day_renders_brief_not_skips(fixture_dir, bridge_server):
    port, _ = bridge_server
    # decisions.log is empty (zero activity yesterday)
    r = _run(fixture_dir, bridge_port=port, args=("--force",))
    assert r.returncode == 0
    log = _read_log(fixture_dir)
    # Per plan v2: zero activity does NOT skip — sends a "quiet day" brief
    assert any(e["type"] == "brief_sent" for e in log)
    assert not any(e["type"] == "brief_skipped" and e["reason"] == "no_activity" for e in log)


def test_quiet_day_brief_says_quiet(fixture_dir, bridge_server):
    port, _ = bridge_server
    r = _run(fixture_dir, bridge_port=port, args=("--force", "--dry-run"))
    assert "Quiet day" in r.stdout or "0 sick calls" in r.stdout
