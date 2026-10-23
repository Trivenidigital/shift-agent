"""PR3 alert hygiene — health-check run heartbeat vs. the second-tier watchdog.

Box evidence (2026-09-22..10-23): 78 "Health check itself has stopped" P2
pages while the health-check was in fact running. shift-agent-health-check.sh
wrote state/last-health-check-ts ONLY on the all-green branch, so any failing
check (already paged as P1 "Agent health issues") aged the file and
shift-agent-health-watchdog.sh paged "stopped" hourly.

Now the heartbeat is written on EVERY completed run; a run that never completes
(stopped timer, or a crash mid-run under set -e) still ages it. The watchdog
arms its throttle only after a delivered page.

Executes the REAL shipped scripts with /opt/shift-agent and /usr/local/bin
rewritten into a sandbox; systemctl / ss / notify-owner / log-decision-direct
are PATH stubs. Linux-only (bash).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "src" / "agents" / "shift" / "scripts"
HEALTH_CHECK = SCRIPTS / "shift-agent-health-check.sh"
HEALTH_WATCHDOG = SCRIPTS / "shift-agent-health-watchdog.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt",
    reason="executes the shipped bash scripts; runs in CI (ubuntu) / the Linux docker runner",
)

STOPPED_TITLE = "Health check itself has stopped"


class _Sandbox:
    def __init__(self, tmp_path: Path, *, notify_rc: int = 0):
        self.root = tmp_path / "opt-shift-agent"
        self.state = self.root / "state"
        self.state.mkdir(parents=True)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.calls = tmp_path / "notify-calls.txt"
        self.ts_file = self.state / "last-health-check-ts"
        self.throttle_file = self.state / ".watchdog-last-alert-ts"
        self._stub("shift-agent-notify-owner",
                   f'printf "%s\\n" "$*" >> "{self.calls}"\nexit {notify_rc}\n')
        self._stub("log-decision-direct", "exit 0\n")
        # Gateway + tail-logger "active"; nothing listening on :3000 -> the
        # check FAILS (bridge port + /health), which is the case under test.
        self._stub("systemctl", "exit 0\n")
        self._stub("ss", "exit 0\n")
        self.scripts = {name: self._rewrite(src) for name, src in
                        (("check", HEALTH_CHECK), ("watchdog", HEALTH_WATCHDOG))}

    def _stub(self, name: str, body: str) -> None:
        p = self.bin / name
        p.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        p.chmod(0o755)

    def _rewrite(self, src: Path) -> Path:
        text = src.read_text(encoding="utf-8")
        text = text.replace("/opt/shift-agent", str(self.root))
        text = text.replace("/usr/local/bin/", f"{self.bin}/")
        out = self.bin.parent / src.name
        out.write_text(text, encoding="utf-8")
        out.chmod(0o755)
        return out

    def run(self, which: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env.get('PATH', '/usr/bin:/bin')}"
        return subprocess.run(["bash", str(self.scripts[which])], capture_output=True,
                              text=True, timeout=120, env=env)

    def notify_calls(self) -> list[str]:
        if not self.calls.exists():
            return []
        return [ln for ln in self.calls.read_text(encoding="utf-8").splitlines() if ln]

    def stopped_pages(self) -> list[str]:
        return [c for c in self.notify_calls() if STOPPED_TITLE in c]

    def set_heartbeat_age(self, seconds: int) -> None:
        self.ts_file.write_text(f"{int(time.time()) - seconds}\n", encoding="utf-8")


def test_failing_check_still_writes_run_heartbeat_so_watchdog_stays_quiet(tmp_path):
    sb = _Sandbox(tmp_path)
    sb.set_heartbeat_age(3600)  # last GREEN run an hour ago
    r = sb.run("check")
    assert r.returncode == 1, r.stderr  # the check itself failed...
    assert any("Agent health issues" in c for c in sb.notify_calls())  # ...and paged P1
    assert int(time.time()) - int(sb.ts_file.read_text().strip()) < 60  # heartbeat fresh
    w = sb.run("watchdog")
    assert w.returncode == 0, w.stderr
    assert sb.stopped_pages() == []  # no false "stopped" P2


def test_no_completed_run_for_over_900s_pages_stopped(tmp_path):
    sb = _Sandbox(tmp_path)
    sb.set_heartbeat_age(1000)
    w = sb.run("watchdog")
    assert w.returncode == 0, w.stderr
    pages = sb.stopped_pages()
    assert len(pages) == 1 and "--priority 2" in pages[0]
    assert sb.throttle_file.exists()  # delivered -> throttle armed
    sb.run("watchdog")
    assert len(sb.stopped_pages()) == 1  # throttled within the hour


def test_failed_stopped_page_does_not_arm_throttle(tmp_path):
    sb = _Sandbox(tmp_path, notify_rc=6)
    sb.set_heartbeat_age(1000)
    w = sb.run("watchdog")
    assert w.returncode == 0, w.stderr  # exit code unchanged
    assert len(sb.stopped_pages()) == 1
    assert not sb.throttle_file.exists()
    sb.run("watchdog")
    assert len(sb.stopped_pages()) == 2  # retried next tick, not silenced for 1h
