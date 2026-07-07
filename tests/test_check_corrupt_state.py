"""S2-8 — corrupt-state quarantine watchdog (check-corrupt-state).

Subprocess tests mirror the repo's script-test convention; Linux-only because
the script imports safe_io (fcntl). The script alerts once per *.corrupt-*
artifact safe_io.safe_load_json leaves behind, so a silent quarantine of a
(possibly money-bearing) state file cannot go unnoticed.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="check-corrupt-state imports safe_io (fcntl — Linux only)",
)

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "platform" / "scripts" / "check-corrupt-state"
)


def _run(state_root: Path, seen_file: Path, *, dry_run: bool, notify_bin: str = "/bin/true"):
    # NOTIFY_OWNER_BIN is read at safe_io import time; point it at a harmless
    # true binary so a real run "succeeds" without touching Pushover.
    env = {**os.environ, "NOTIFY_OWNER_BIN": notify_bin}
    cmd = [sys.executable, str(SCRIPT),
           "--state-root", str(state_root), "--seen-file", str(seen_file)]
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)


def test_detects_alerts_then_dedups(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    corrupt = state / "catering-leads.json.corrupt-1700000000"
    corrupt.write_text("{bad json", encoding="utf-8")
    (state / "healthy.json").write_text("{}", encoding="utf-8")
    seen = state / ".corrupt-state-seen.json"

    # Dry-run detects but does NOT persist the seen-file.
    r = _run(state, seen, dry_run=True)
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout)
    assert str(corrupt) in out["found"]
    assert str(corrupt) in out["new"]
    assert not seen.exists()

    # Real run alerts + records the artifact.
    r = _run(state, seen, dry_run=False)
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout)
    assert str(corrupt) in out["new"]
    assert seen.exists()
    assert str(corrupt) in json.loads(seen.read_text(encoding="utf-8"))

    # Second run: same artifact deduped (found, but not new → no re-alert).
    r = _run(state, seen, dry_run=False)
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout)
    assert str(corrupt) in out["found"]
    assert out["new"] == []


def test_healthy_state_root_no_alert(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "leads.json").write_text("{}", encoding="utf-8")
    seen = state / ".corrupt-state-seen.json"
    r = _run(state, seen, dry_run=False)
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout)
    assert out["found"] == []
    assert out["new"] == []
    assert not seen.exists()


def test_seen_file_not_self_detected(tmp_path):
    """Regression: the watchdog's own `.corrupt-state-seen.json` bookkeeping
    file contains the substring `.corrupt-` but is NOT a quarantine artifact
    (safe_io names those `<name>.corrupt-<digits>`). It must never be reported
    as `new` or alerted on, across repeated runs."""
    state = tmp_path / "state"
    state.mkdir()
    corrupt = state / "leads.json.corrupt-1700000001"
    corrupt.write_text("{bad", encoding="utf-8")
    seen = state / ".corrupt-state-seen.json"

    r1 = _run(state, seen, dry_run=False)
    assert r1.returncode == 0, (r1.stdout, r1.stderr)
    # Second run must find NOTHING new — the seen-file (now present) is not a
    # quarantine artifact and the real corrupt file is already deduped.
    r2 = _run(state, seen, dry_run=False)
    out = json.loads(r2.stdout)
    assert out["new"] == [], out
    assert str(seen) not in out["found"]


def test_missing_state_root_is_noop(tmp_path):
    state = tmp_path / "nonexistent"
    seen = tmp_path / "seen.json"
    r = _run(state, seen, dry_run=False)
    assert r.returncode == 0, (r.stdout, r.stderr)
    out = json.loads(r.stdout)
    assert out == {"found": [], "new": [], "alerted": [], "dry_run": False}
