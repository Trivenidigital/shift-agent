"""shift-agent-backup.sh retention — count + total-size caps (census S4/C-2).

Date-only retention let backups accumulate to 15GB and fill the disk in May.
These tests drive the script's standalone `--enforce-retention <dir>` entrypoint
against a temp dir of fake dated *.tar.gz.gpg files and assert which survive.
Needs bash + GNU find/stat (Git Bash on Windows, coreutils on Linux).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "shift" / "scripts" / "shift-agent-backup.sh"
)


def _working_bash() -> str | None:
    """Return a bash that actually executes, or None. On Windows the PATH `bash`
    is often the WSL relay stub, which fails execvpe; skip rather than red on it.
    On Linux CI `bash` is real and GNU find/printf are present."""
    cand = shutil.which("bash")
    if not cand:
        return None
    try:
        r = subprocess.run([cand, "-c", "echo ok"], capture_output=True, text=True, timeout=15)
        return cand if (r.returncode == 0 and r.stdout.strip() == "ok") else None
    except Exception:
        return None


BASH = _working_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a working bash + GNU find")


def _mk_backup(dir_: Path, name: str, size_bytes: int, age_seconds: float) -> Path:
    p = dir_ / name
    p.write_bytes(b"x" * size_bytes)
    t = time.time() - age_seconds
    os.utime(p, (t, t))
    return p


def _run_retention(dir_: Path, *, retention_days=3650, max_count=None, max_bytes=None):
    env = {**os.environ}
    if max_count is not None:
        env["SHIFT_AGENT_BACKUP_MAX_COUNT"] = str(max_count)
    if max_bytes is not None:
        env["SHIFT_AGENT_BACKUP_MAX_TOTAL_BYTES"] = str(max_bytes)
    return subprocess.run(
        [BASH, str(SCRIPT), "--enforce-retention", str(dir_), str(retention_days)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _remaining(dir_: Path):
    return sorted(p.name for p in dir_.glob("*.tar.gz.gpg"))


def test_count_cap_keeps_newest_n(tmp_path):
    for i in range(6):
        _mk_backup(tmp_path, f"2026-06-0{i + 1}-0200.tar.gz.gpg", 1024, age_seconds=(6 - i) * 86400)
    r = _run_retention(tmp_path, max_count=3, max_bytes=10 ** 12)
    assert r.returncode == 0, r.stderr
    assert _remaining(tmp_path) == [
        "2026-06-04-0200.tar.gz.gpg",
        "2026-06-05-0200.tar.gz.gpg",
        "2026-06-06-0200.tar.gz.gpg",
    ]
    assert "Retention: pruned 3" in r.stdout


def test_size_cap_keeps_under_budget_deleting_oldest(tmp_path):
    # 5 x 1000 bytes; cap 2500 -> keep newest 2 (2000<=2500); the third (3000)
    # crosses the cap so it and everything older is pruned.
    for i in range(5):
        _mk_backup(tmp_path, f"2026-06-0{i + 1}-0200.tar.gz.gpg", 1000, age_seconds=(5 - i) * 86400)
    r = _run_retention(tmp_path, max_count=100, max_bytes=2500)
    assert r.returncode == 0, r.stderr
    assert _remaining(tmp_path) == [
        "2026-06-04-0200.tar.gz.gpg",
        "2026-06-05-0200.tar.gz.gpg",
    ]


def test_newest_always_kept_even_if_over_size_cap(tmp_path):
    # A single newest file exceeding the cap must NOT be deleted (never leave 0).
    _mk_backup(tmp_path, "2026-06-05-0200.tar.gz.gpg", 5000, age_seconds=86400)
    r = _run_retention(tmp_path, max_count=14, max_bytes=1000)
    assert r.returncode == 0, r.stderr
    assert _remaining(tmp_path) == ["2026-06-05-0200.tar.gz.gpg"]


def test_within_caps_prunes_nothing(tmp_path):
    for i in range(3):
        _mk_backup(tmp_path, f"2026-06-0{i + 1}-0200.tar.gz.gpg", 1000, age_seconds=(3 - i) * 86400)
    r = _run_retention(tmp_path, max_count=14, max_bytes=10 ** 12)
    assert r.returncode == 0, r.stderr
    assert len(_remaining(tmp_path)) == 3
    assert "Retention: pruned" not in r.stdout


def test_date_prune_still_applies(tmp_path):
    _mk_backup(tmp_path, "2026-01-01-0200.tar.gz.gpg", 1000, age_seconds=40 * 86400)
    _mk_backup(tmp_path, "2026-06-05-0200.tar.gz.gpg", 1000, age_seconds=1 * 86400)
    r = _run_retention(tmp_path, retention_days=30, max_count=14, max_bytes=10 ** 12)
    assert r.returncode == 0, r.stderr
    assert _remaining(tmp_path) == ["2026-06-05-0200.tar.gz.gpg"]
