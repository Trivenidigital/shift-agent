"""OpenRouter balance threshold alert — delivery-path contracts (§12b).

The checker itself is a one-shot; what matters operationally is that the
dispatched/delivered/delivery_failed event pairs are always emitted so
journalctl can distinguish "healthy", "checker broken", and "alert fired but
delivery failed" — plus the injected-failure case: a notify chokepoint that
exits non-zero MUST surface as delivery_failed + EXIT_DEPENDENCY_DOWN (which
trips the systemd OnFailure page), never as a silent success.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "agents" / "shift" / "scripts" / "check-openrouter-balance"
DEPLOY = Path(__file__).resolve().parent.parent / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SYSTEMD_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "shift" / "systemd"


def _stub_notify(tmp_path: Path, exit_code: int) -> Path:
    """Cross-platform stub for the notify-owner chokepoint."""
    if sys.platform == "win32":
        stub = tmp_path / f"notify-{exit_code}.bat"
        stub.write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="utf-8")
    else:
        stub = tmp_path / f"notify-{exit_code}.sh"
        stub.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
        stub.chmod(0o755)
    return stub


def _credits(tmp_path: Path, total_credits: float, total_usage: float) -> Path:
    doc = tmp_path / "credits.json"
    doc.write_text(json.dumps({"data": {"total_credits": total_credits, "total_usage": total_usage}}), encoding="utf-8")
    return doc


def _key(tmp_path: Path, limit, limit_remaining) -> Path:
    """GET /api/v1/auth/key response. ``limit=None`` is an uncapped key."""
    doc = tmp_path / "key.json"
    doc.write_text(json.dumps({"data": {"limit": limit, "limit_remaining": limit_remaining, "usage": 1.0}}), encoding="utf-8")
    return doc


def _run(*args: str) -> tuple[int, list[dict]]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=60,
    )
    events = [json.loads(line) for line in proc.stdout.strip().splitlines() if line.strip()]
    return proc.returncode, events


def test_healthy_balance_never_touches_the_notify_chokepoint(tmp_path):
    doc = _credits(tmp_path, 25.0, 5.0)
    key = _key(tmp_path, None, None)
    # A nonexistent notify bin proves the healthy path never invokes it.
    code, events = _run("--credits-json", str(doc), "--key-json", str(key),
                        "--threshold-usd", "5",
                        "--notify-bin", str(tmp_path / "does-not-exist"))
    assert code == 0
    assert [e["event"] for e in events] == ["openrouter_balance_ok", "openrouter_key_limit_ok"]
    assert events[0]["balance_usd"] == 20.0


def test_low_balance_dispatches_and_delivers(tmp_path):
    doc = _credits(tmp_path, 10.0, 7.5)
    key = _key(tmp_path, None, None)
    stub = _stub_notify(tmp_path, 0)
    code, events = _run("--credits-json", str(doc), "--key-json", str(key),
                        "--threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 0
    assert [e["event"] for e in events] == [
        "openrouter_balance_alert_dispatched",
        "openrouter_balance_alert_delivered",
        "openrouter_key_limit_ok",
    ]
    assert events[0]["balance_usd"] == 2.5


def test_injected_notify_failure_surfaces_as_delivery_failed_and_nonzero_exit(tmp_path):
    doc = _credits(tmp_path, 10.0, 9.0)
    key = _key(tmp_path, None, None)
    stub = _stub_notify(tmp_path, 6)  # notify-owner's "all channels failed"
    code, events = _run("--credits-json", str(doc), "--key-json", str(key),
                        "--threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 6  # EXIT_DEPENDENCY_DOWN -> systemd OnFailure page
    assert [e["event"] for e in events] == [
        "openrouter_balance_alert_dispatched",
        "openrouter_balance_alert_delivery_failed",
        "openrouter_key_limit_ok",
    ]
    assert events[1]["notify_exit_code"] == 6


def test_malformed_credits_doc_is_check_failed_not_silent(tmp_path):
    doc = tmp_path / "credits.json"
    doc.write_text('{"unexpected": true}', encoding="utf-8")
    key = _key(tmp_path, None, None)
    code, events = _run("--credits-json", str(doc), "--key-json", str(key), "--threshold-usd", "5")
    assert code == 6
    assert events[0]["event"] == "openrouter_balance_check_failed"


# --- per-key spend cap (GET /api/v1/auth/key) ------------------------------
# Production 2026-10-08: account balance alerted, but the gateway key's own
# cap (limit 30, limit_remaining 0) made every completion HTTP 403 with no
# alert at all. The key cap is checked independently of the account balance.


def test_uncapped_key_is_key_limit_ok(tmp_path):
    code, events = _run("--credits-json", str(_credits(tmp_path, 25.0, 5.0)),
                        "--key-json", str(_key(tmp_path, None, None)),
                        "--notify-bin", str(tmp_path / "does-not-exist"))
    assert code == 0
    assert [e["event"] for e in events] == ["openrouter_balance_ok", "openrouter_key_limit_ok"]
    assert events[1]["limit_usd"] is None


def test_healthy_key_cap_is_key_limit_ok(tmp_path):
    code, events = _run("--credits-json", str(_credits(tmp_path, 25.0, 5.0)),
                        "--key-json", str(_key(tmp_path, 30, 20)),
                        "--threshold-usd", "5", "--key-threshold-usd", "5",
                        "--notify-bin", str(tmp_path / "does-not-exist"))
    assert code == 0
    assert [e["event"] for e in events] == ["openrouter_balance_ok", "openrouter_key_limit_ok"]
    assert events[1]["limit_remaining_usd"] == 20.0
    assert events[1]["limit_usd"] == 30.0


def test_exhausted_key_cap_alerts_even_with_healthy_balance(tmp_path):
    stub = _stub_notify(tmp_path, 0)
    code, events = _run("--credits-json", str(_credits(tmp_path, 25.0, 5.0)),
                        "--key-json", str(_key(tmp_path, 30, 0)),
                        "--threshold-usd", "5", "--key-threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 0
    assert [e["event"] for e in events] == [
        "openrouter_balance_ok",
        "openrouter_key_limit_alert_dispatched",
        "openrouter_key_limit_alert_delivered",
    ]
    assert events[1]["limit_remaining_usd"] == 0.0
    assert events[1]["limit_usd"] == 30.0


def test_both_low_emit_both_alert_pairs(tmp_path):
    stub = _stub_notify(tmp_path, 0)
    code, events = _run("--credits-json", str(_credits(tmp_path, 10.0, 7.44)),
                        "--key-json", str(_key(tmp_path, 30, 0)),
                        "--threshold-usd", "5", "--key-threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 0
    assert [e["event"] for e in events] == [
        "openrouter_balance_alert_dispatched",
        "openrouter_balance_alert_delivered",
        "openrouter_key_limit_alert_dispatched",
        "openrouter_key_limit_alert_delivered",
    ]


def test_key_alert_notify_failure_is_delivery_failed_and_nonzero_exit(tmp_path):
    stub = _stub_notify(tmp_path, 6)
    code, events = _run("--credits-json", str(_credits(tmp_path, 25.0, 5.0)),
                        "--key-json", str(_key(tmp_path, 30, 0)),
                        "--threshold-usd", "5", "--key-threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 6
    assert [e["event"] for e in events] == [
        "openrouter_balance_ok",
        "openrouter_key_limit_alert_dispatched",
        "openrouter_key_limit_alert_delivery_failed",
    ]
    assert events[2]["notify_exit_code"] == 6


def test_unreadable_key_doc_is_check_failed_and_balance_still_evaluated(tmp_path):
    code, events = _run("--credits-json", str(_credits(tmp_path, 25.0, 5.0)),
                        "--key-json", str(tmp_path / "missing-key.json"),
                        "--threshold-usd", "5",
                        "--notify-bin", str(tmp_path / "does-not-exist"))
    assert code == 6
    assert [e["event"] for e in events] == ["openrouter_balance_ok", "openrouter_key_limit_check_failed"]


def test_key_check_failure_does_not_suppress_delivered_balance_alert(tmp_path):
    stub = _stub_notify(tmp_path, 0)
    code, events = _run("--credits-json", str(_credits(tmp_path, 10.0, 7.5)),
                        "--key-json", str(tmp_path / "missing-key.json"),
                        "--threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 6
    assert [e["event"] for e in events] == [
        "openrouter_balance_alert_dispatched",
        "openrouter_balance_alert_delivered",
        "openrouter_key_limit_check_failed",
    ]


def test_balance_check_failure_still_runs_key_check(tmp_path):
    bad = tmp_path / "credits.json"
    bad.write_text('{"unexpected": true}', encoding="utf-8")
    stub = _stub_notify(tmp_path, 0)
    code, events = _run("--credits-json", str(bad),
                        "--key-json", str(_key(tmp_path, 30, 0)),
                        "--key-threshold-usd", "5",
                        "--notify-bin", str(stub))
    assert code == 6
    assert [e["event"] for e in events] == [
        "openrouter_balance_check_failed",
        "openrouter_key_limit_alert_dispatched",
        "openrouter_key_limit_alert_delivered",
    ]


def test_units_staged_but_timer_never_auto_enabled():
    """Deploy installs the unit files via the shift systemd glob but must not
    arm the timer — enabling is a documented one-time operator step."""
    for name in ("openrouter-balance-check.service",
                 "openrouter-balance-check.timer",
                 "openrouter-balance-check-failure.service"):
        assert (SYSTEMD_DIR / name).exists(), name
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "install -m 644 src/agents/shift/systemd/*.timer" in deploy
    assert "systemctl enable --now openrouter-balance-check.timer" not in deploy.replace(
        "#   systemctl daemon-reload && systemctl enable --now openrouter-balance-check.timer", "")
    # The service pages through the failure unit on non-zero exit.
    service = (SYSTEMD_DIR / "openrouter-balance-check.service").read_text(encoding="utf-8")
    assert "OnFailure=openrouter-balance-check-failure.service" in service
