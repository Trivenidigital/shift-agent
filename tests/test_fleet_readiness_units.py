"""PR2 Fleet Sentinel — systemd units + deploy/smoke wiring for fleet-readiness-report.

The cadence pin is the load-bearing test: every run appends one audit row to
decisions.log, so a timer that fires at or below the alert-integrity-watchdog's
freshness threshold would keep decisions.log's mtime fresh forever and silently
disable the "Audit log STALE" page.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSTEMD = REPO / "src" / "agents" / "shift" / "systemd"
TIMER = SYSTEMD / "fleet-readiness-report.timer"
SERVICE = SYSTEMD / "fleet-readiness-report.service"
FAILURE = SYSTEMD / "fleet-readiness-report-failure.service"
WATCHDOG = REPO / "src" / "platform" / "scripts" / "alert-integrity-watchdog"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SMOKE = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"

_DAILY_ONCALENDAR = re.compile(r"^\*-\*-\* (\d{2}):(\d{2}):(\d{2})$")


def _directives(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def _watchdog_freshness_threshold_min() -> int:
    # The watchdog is an extensionless script, not an importable module.
    m = re.search(r"^DEFAULT_FRESHNESS_THRESHOLD_MIN\s*=\s*(\d+)\s*$",
                  WATCHDOG.read_text(encoding="utf-8"), re.M)
    assert m, "DEFAULT_FRESHNESS_THRESHOLD_MIN not found in alert-integrity-watchdog"
    return int(m.group(1))


def test_timer_fires_once_daily_above_the_audit_freshness_threshold():
    d = _directives(TIMER)
    # No relative/monotonic triggers that could fire more often than daily.
    for key in ("OnUnitActiveSec", "OnUnitInactiveSec", "OnBootSec", "OnStartupSec", "OnActiveSec"):
        assert key not in d, f"{key} would change the cadence; the timer must stay daily"
    calendars = d.get("OnCalendar", [])
    assert len(calendars) == 1, f"exactly one OnCalendar expected, got {calendars}"
    assert _DAILY_ONCALENDAR.match(calendars[0]), (
        f"OnCalendar={calendars[0]!r} is not a once-per-day '*-*-* HH:MM:SS' spec")
    interval_min = 24 * 60
    assert interval_min > _watchdog_freshness_threshold_min(), (
        "fleet-readiness-report would keep decisions.log fresh and mask the STALE watchdog")
    assert d.get("Persistent") == ["true"]
    assert d.get("Unit") == ["fleet-readiness-report.service"]


def test_service_is_oneshot_hardened_with_failure_handler():
    d = _directives(SERVICE)
    assert d["Type"] == ["oneshot"]
    assert d["User"] == ["shift-agent"]
    assert d["OnFailure"] == ["fleet-readiness-report-failure.service"]
    assert d["ExecStart"] == ["/usr/local/bin/fleet-readiness-report"]
    assert "Environment" in d and "TZ=UTC" in d["Environment"]
    # Hardening copied from send-routing-accuracy-summary.service.
    ref = _directives(SYSTEMD / "send-routing-accuracy-summary.service")
    for key in ("NoNewPrivileges", "ProtectSystem", "ReadWritePaths", "ProtectHome",
                "PrivateTmp", "RuntimeDirectory"):
        assert d[key] == ref[key], key


def test_failure_unit_pages_p1_without_recursion():
    text = FAILURE.read_text(encoding="utf-8")
    d = _directives(FAILURE)
    assert "--priority 1" in text
    assert "/usr/local/bin/shift-agent-notify-owner" in text
    assert "OnFailure" not in d


def test_deploy_installs_module_and_enables_timer_beside_routing_summary():
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "install -m 644 src/platform/fleet_readiness.py /opt/shift-agent/fleet_readiness.py" in deploy
    lines = [ln.strip() for ln in deploy.splitlines()]
    routing = lines.index("systemctl enable --now send-routing-accuracy-summary.timer 2>/dev/null || true")
    fleet = lines.index("systemctl enable --now fleet-readiness-report.timer 2>/dev/null || true")
    assert 0 < fleet - routing <= 5, "enable line must sit with the routing-summary units"
    # Unit files land through the existing shift systemd globs.
    assert "install -m 644 src/agents/shift/systemd/*.timer /etc/systemd/system/" in deploy
    assert "install -m 644 src/agents/shift/systemd/*.service /etc/systemd/system/" in deploy


def test_smoke_asserts_units_exactly_where_routing_summary_is_asserted():
    smoke = SMOKE.read_text(encoding="utf-8")
    assert "    send-routing-accuracy-summary.timer \\\n    fleet-readiness-report.timer; do" in smoke
    for unit in ("fleet-readiness-report.service", "fleet-readiness-report.timer",
                 "fleet-readiness-report-failure.service"):
        assert f"/etc/systemd/system/{unit}" in smoke
