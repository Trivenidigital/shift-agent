"""PR3 alert hygiene — static unit/deploy/smoke wiring.

* The smoke test's §8 enabled-units list mirrors EXACTLY the timers
  shift-agent-deploy.sh enables unconditionally (plus the proposal-sweep timer
  under the same "sweep shipped" guard). A timer deploy enables but smoke never
  checks is a silent-disable surface; a timer smoke checks but deploy leaves off
  fails every deploy.
* alert-integrity-watchdog and check-corrupt-state page the owner (P1) through
  an OnFailure= unit shaped like flyer-recovery-watchdog-failure.service,
  installed by the platform *.service wildcard and syntax-checked by smoke §9.

Text-only, so runs everywhere.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SMOKE = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"
PLATFORM_SYSTEMD = REPO / "src" / "platform" / "systemd"

# Deploy enables these only conditionally / deliberately leaves them off.
_GUARDED_IN_DEPLOY = {"shift-agent-proposal-sweep.timer", "flyer-recovery-watchdog.timer"}


def _deploy_enabled_timers() -> set[str]:
    text = DEPLOY.read_text(encoding="utf-8")
    found = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"systemctl enable --now ([\w.-]+\.timer)", stripped)
        if m:
            found.add(m.group(1))
    return found


def _smoke_enabled_units() -> tuple[set[str], str]:
    text = SMOKE.read_text(encoding="utf-8")
    m = re.search(r"smoke_enabled_units=\(\n(?P<body>.*?)\n\)", text, re.S)
    assert m, "smoke §8 smoke_enabled_units array not found"
    units = {ln.strip() for ln in m.group("body").splitlines() if ln.strip()}
    return units, text


def test_smoke_enabled_timers_mirror_deploy_unconditional_enables():
    deploy_timers = _deploy_enabled_timers()
    smoke_units, _ = _smoke_enabled_units()
    smoke_timers = {u for u in smoke_units if u.endswith(".timer")}
    assert smoke_timers == deploy_timers - _GUARDED_IN_DEPLOY


def test_smoke_never_requires_deliberately_disabled_timers():
    smoke_units, _ = _smoke_enabled_units()
    for unit in ("shift-agent-skills-audit.timer", "openrouter-balance-check.timer",
                 "catering-followup-sweep.timer", "flyer-recovery-watchdog.timer"):
        assert unit not in smoke_units


def test_smoke_checks_proposal_sweep_under_the_deploy_guard():
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "if [ -f src/platform/proposal_sweep.py ]; then\n        systemctl enable --now shift-agent-proposal-sweep.timer" in deploy
    _, smoke = _smoke_enabled_units()
    assert ("if [ -f /opt/shift-agent/proposal_sweep.py ]; then\n"
            "    smoke_enabled_units+=( shift-agent-proposal-sweep.timer )") in smoke


def test_platform_watchdogs_declare_onfailure_pagers():
    for name in ("alert-integrity-watchdog", "check-corrupt-state"):
        unit = (PLATFORM_SYSTEMD / f"{name}.service").read_text(encoding="utf-8")
        unit_section = unit.split("[Service]")[0]
        assert f"OnFailure={name}-failure.service" in unit_section


def test_failure_units_mirror_flyer_failure_shape_at_p1():
    for name in ("alert-integrity-watchdog", "check-corrupt-state"):
        text = (PLATFORM_SYSTEMD / f"{name}-failure.service").read_text(encoding="utf-8")
        assert "Type=oneshot" in text
        assert "User=shift-agent" in text
        assert "EnvironmentFile=-/opt/shift-agent/.env" in text
        assert "ExecStartPre=/usr/bin/test -x /usr/local/bin/shift-agent-notify-owner" in text
        assert "ExecStart=/usr/local/bin/shift-agent-notify-owner " in text
        assert "--priority 1 " in text and "--priority 2" not in text
        assert "SuccessExitStatus=5 6" in text
        assert "OnFailure=" not in text  # a failing pager must not recurse
        assert "\r" not in text


def test_flyer_watchdog_failure_units_page_at_p1():
    """A watchdog's own unit failure is not a customer emergency (box: the SLA
    watchdog's failure unit repeated 5x at P2)."""
    flyer = REPO / "src" / "agents" / "flyer" / "systemd"
    for name in ("flyer-source-edit-sla-watchdog-failure.service",
                 "flyer-recovery-watchdog-failure.service"):
        text = (flyer / name).read_text(encoding="utf-8")
        assert "ExecStart=/usr/local/bin/shift-agent-notify-owner " in text
        assert "--priority 1 " in text and "--priority 2" not in text


def test_deploy_installs_failure_units_via_platform_wildcard():
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "install -m 644 src/platform/systemd/*.service /etc/systemd/system/" in deploy


def test_smoke_syntax_checks_platform_watchdogs_and_pagers():
    _, smoke = _smoke_enabled_units()
    for unit in ("alert-integrity-watchdog.service", "alert-integrity-watchdog-failure.service",
                 "check-corrupt-state.service", "check-corrupt-state-failure.service"):
        assert unit in smoke
    assert 'sd_verify_units+=( "/etc/systemd/system/${_pw_unit}" )' in smoke
