"""check-corrupt-state deploy wiring (census C4b).

The unit files have shipped in-repo since #579, but shift-agent-deploy.sh's
platform block installed only *.service — so the timer never landed on the box
and `systemctl cat check-corrupt-state.timer` returned not-found (the corruption
monitor never ran). These static assertions lock in the install + enable + smoke
wiring. Text-only, so runs everywhere (the script's own subprocess tests are
Linux-only via safe_io/fcntl).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SMOKE = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"
UNIT_DIR = REPO / "src" / "platform" / "systemd"


def test_units_present_in_repo():
    assert (UNIT_DIR / "check-corrupt-state.service").exists()
    assert (UNIT_DIR / "check-corrupt-state.timer").exists()


def test_deploy_installs_platform_timers_and_enables_check_corrupt_state():
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "install -m 644 src/platform/systemd/*.timer" in deploy
    assert "systemctl enable --now check-corrupt-state.timer" in deploy


def test_smoke_verifies_check_corrupt_state_timer_enabled():
    assert "check-corrupt-state.timer" in SMOKE.read_text(encoding="utf-8")
