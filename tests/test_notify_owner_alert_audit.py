"""F0-2 (§12b): shift-agent-notify-owner emits owner_alert_dispatched before the
delivery attempt and owner_alert_delivered on the channel that accepts it.

Before the fix a successful delivery wrote NO decisions.log row, so 'no rows'
was ambiguous between delivered-cleanly and never-fired. Asserts the
dispatched/delivered pair on success and dispatched-only when every channel
fails.

Runs on Windows via an fcntl stub (the script imports safe_io at module top).
All writes are routed to tmp paths.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows, write_config

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-notify-owner"


def _load(tmp_path, monkeypatch, *, pushover_ok, whatsapp_ok=False):
    write_config(tmp_path)
    log = tmp_path / "logs" / "decisions.log"
    mod = load_script("shift_agent_notify_owner_under_test", SCRIPT)
    mod.CONFIG_PATH = tmp_path / "config.yaml"
    mod.DECISIONS_LOG_PATH = log
    mod.STATE_DIR = tmp_path / "state"
    mod.NOTIFY_FAILED_LOG = tmp_path / "state" / "notify-failed.log"
    monkeypatch.setattr(mod, "pushover_send", lambda *a, **k: (pushover_ok, "ok" if pushover_ok else "boom"))
    monkeypatch.setattr(mod, "whatsapp_fallback", lambda *a, **k: (whatsapp_ok, "wa" if whatsapp_ok else "down"))
    monkeypatch.setattr(mod.sys, "argv",
                        ["shift-agent-notify-owner", "--title", "Bridge down", "--priority", "1", "the body"])
    return mod, log


def test_pushover_success_emits_dispatched_and_delivered(tmp_path, monkeypatch):
    mod, log = _load(tmp_path, monkeypatch, pushover_ok=True)
    rc = mod.main()
    assert rc == mod.EXIT_OK
    rows = read_log_rows(log)
    types = [r["type"] for r in rows]
    assert types == ["owner_alert_dispatched", "owner_alert_delivered"]
    assert rows[0]["title"] == "Bridge down" and rows[0]["message_excerpt"] == "the body"
    assert rows[0]["priority"] == 1
    assert rows[1]["channel"] == "pushover"


def test_whatsapp_fallback_delivered_channel(tmp_path, monkeypatch):
    mod, log = _load(tmp_path, monkeypatch, pushover_ok=False, whatsapp_ok=True)
    rc = mod.main()
    assert rc == mod.EXIT_OK
    rows = read_log_rows(log)
    assert [r["type"] for r in rows] == ["owner_alert_dispatched", "owner_alert_delivered"]
    assert rows[1]["channel"] == "whatsapp_fallback"


def test_all_channels_fail_emits_dispatched_only(tmp_path, monkeypatch):
    mod, log = _load(tmp_path, monkeypatch, pushover_ok=False, whatsapp_ok=False)
    rc = mod.main()
    assert rc == mod.EXIT_DEPENDENCY_DOWN
    types = [r["type"] for r in read_log_rows(log)]
    assert types == ["owner_alert_dispatched"]
    assert "owner_alert_delivered" not in types
