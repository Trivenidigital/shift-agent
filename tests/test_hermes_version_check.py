"""Tests for the read-only Hermes version monitor (`hermes-version-check`).

Mirrors tests/test_flyer_recovery_watchdog.py:
  - pure helpers tested in-process via importlib
  - end-to-end behavior tested via subprocess with --text / --dry-run +
    path overrides + the SHIFT_AGENT_NOTIFY_OWNER_BIN test seam
  - POSIX-only paths (git, exec bit, perms) guarded with skipif(Windows)

The monitor is stdlib-only and importable on Windows (no fcntl/safe_io).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "platform" / "scripts" / "hermes-version-check"

# The script has no .py extension → force a SourceFileLoader so importlib can
# load it as a module for in-process unit tests of the pure helpers.
import importlib.machinery  # noqa: E402
_loader = importlib.machinery.SourceFileLoader("hvc", str(SCRIPT))
_spec = importlib.util.spec_from_loader("hvc", _loader)
hvc = importlib.util.module_from_spec(_spec)
_loader.exec_module(hvc)


# ── shared fixtures ─────────────────────────────────────────────────────────

def _fake_hermes_home(tmp_path: Path) -> Path:
    """A real git repo shaped like a patched Hermes checkout."""
    home = tmp_path / "hermes"
    (home / "gateway" / "platforms").mkdir(parents=True)
    (home / "scripts" / "whatsapp-bridge").mkdir(parents=True)
    body = "# BEGIN shift-agent-sender-id\n# END shift-agent-sender-id\n"
    (home / "gateway" / "run.py").write_text(body, encoding="utf-8")
    (home / "gateway" / "platforms" / "whatsapp.py").write_text(body, encoding="utf-8")
    (home / "scripts" / "whatsapp-bridge" / "bridge.js").write_text(body, encoding="utf-8")
    for cmd in (
        ["git", "init", "-q", str(home)],
        ["git", "-C", str(home), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
        ["git", "-C", str(home), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
    ):
        subprocess.run(cmd, check=True, capture_output=True)
    return home


def _git_head(home: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(home), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()


def _tree_digest(p: Path) -> str:
    """Content digest of all non-.git files under p (mutation detector)."""
    parts = []
    for f in sorted(p.rglob("*")):
        if f.is_file() and ".git" not in f.parts:
            parts.append(str(f.relative_to(p)) + ":" + f.read_text(encoding="utf-8", errors="replace"))
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════════════════════
# Task 1 — pure condition + signature + throttle helpers
# ════════════════════════════════════════════════════════════════════════════

def test_signature_is_order_independent():
    assert hvc.alert_signature(["b", "a"]) == hvc.alert_signature(["a", "b"])
    assert hvc.alert_signature([]) == ""


def test_new_hard_condition_notifies():
    prev = {"last_alert_signature": "", "consecutive_network_failures": 0}
    d = hvc.decide_alert(
        ["runtime_commit_drift"], ["runtime_commit_drift"], [],
        prev, now=datetime.now(timezone.utc), network_fail_after=3,
    )
    assert d["notify"] is True and d["priority"] == 1 and d["action"] == "sent"


def test_repeated_same_drift_suppressed():
    sig = hvc.alert_signature(["runtime_commit_drift"])
    prev = {"last_alert_signature": sig, "consecutive_network_failures": 0}
    d = hvc.decide_alert(
        ["runtime_commit_drift"], ["runtime_commit_drift"], [],
        prev, now=datetime.now(timezone.utc), network_fail_after=3,
    )
    assert d["notify"] is False and d["action"] == "suppressed"


def test_cleared_condition_sends_recovery():
    sig = hvc.alert_signature(["runtime_commit_drift"])
    prev = {"last_alert_signature": sig, "consecutive_network_failures": 0}
    d = hvc.decide_alert(
        [], [], [], prev, now=datetime.now(timezone.utc), network_fail_after=3,
    )
    assert d["notify"] is True and d["action"] == "recovery" and d["priority"] == 0


def test_no_conditions_no_prior_is_not_needed():
    prev = {"last_alert_signature": "", "consecutive_network_failures": 0}
    d = hvc.decide_alert([], [], [], prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is False and d["action"] == "not_needed"


def test_network_failure_throttled_until_threshold():
    # 2nd consecutive failure (prev=1, +1=2) is below the threshold of 3 → suppress
    prev = {"last_alert_signature": "", "consecutive_network_failures": 1}
    d = hvc.decide_alert(
        ["upstream_check_failed"], [], ["upstream_check_failed"],
        prev, now=datetime.now(timezone.utc), network_fail_after=3,
    )
    assert d["notify"] is False


def test_network_failure_alerts_at_threshold():
    # 3rd consecutive failure (prev=2, +1=3) reaches threshold → notify (soft)
    prev = {"last_alert_signature": "", "consecutive_network_failures": 2}
    d = hvc.decide_alert(
        ["upstream_check_failed"], [], ["upstream_check_failed"],
        prev, now=datetime.now(timezone.utc), network_fail_after=3,
    )
    assert d["notify"] is True and d["priority"] == 0


def test_read_baseline_normalizes_crlf_and_quotes(tmp_path):
    p = tmp_path / "baseline.txt"
    p.write_text(
        '# comment\r\nHERMES_COMMIT="abc123"\r\nHERMES_VERSION=unknown\r\n'
        'BRIDGE_POST_PATCH_SHA256=deadbeef\r\n',
        encoding="utf-8",
    )
    b = hvc.read_baseline(p)
    assert b == {"commit": "abc123", "version": "unknown", "bridge_sha256": "deadbeef"}


def test_read_baseline_missing_returns_none(tmp_path):
    assert hvc.read_baseline(tmp_path / "nope.txt") is None


# ════════════════════════════════════════════════════════════════════════════
# Task 2 — local runtime reads (commit / bridge sha / markers) + no-mutation
# ════════════════════════════════════════════════════════════════════════════

def test_gather_local_matches_baseline(tmp_path):
    home = _fake_hermes_home(tmp_path)
    commit = _git_head(home)
    baseline = {"commit": commit, "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    local = hvc.gather_local(home, baseline)
    assert local["runtime_status"] == "match"
    assert local["bridge_status"] == "match"
    assert local["patch_markers_status"] == "present"
    assert local["conditions"] == []


def test_gather_local_detects_commit_drift(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = {"commit": "0" * 40, "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    local = hvc.gather_local(home, baseline)
    assert local["runtime_status"] == "drift"
    assert "runtime_commit_drift" in local["conditions"]


def test_gather_local_detects_bridge_drift(tmp_path):
    home = _fake_hermes_home(tmp_path)
    commit = _git_head(home)
    baseline = {"commit": commit, "version": "unknown", "bridge_sha256": "deadbeef"}
    local = hvc.gather_local(home, baseline)
    assert local["bridge_status"] == "drift"
    assert "bridge_sha_drift" in local["conditions"]


def test_gather_local_detects_missing_markers(tmp_path):
    home = _fake_hermes_home(tmp_path)
    # Remove the marker from run.py
    (home / "gateway" / "run.py").write_text("no marker here\n", encoding="utf-8")
    commit = _git_head(home)
    baseline = {"commit": commit, "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    local = hvc.gather_local(home, baseline)
    assert local["patch_markers_status"] == "missing"
    assert "patch_markers_missing" in local["conditions"]


def test_read_helpers_do_not_mutate_home(tmp_path):
    home = _fake_hermes_home(tmp_path)
    head = _git_head(home)
    baseline = {"commit": head, "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    before = _tree_digest(home)
    hvc.gather_local(home, baseline)          # must not write anything
    hvc.gather_local(home, baseline)
    assert _tree_digest(home) == before
    assert _git_head(home) == head


# ════════════════════════════════════════════════════════════════════════════
# Task 3 — best-effort upstream check (git ls-remote, fail-safe)
# ════════════════════════════════════════════════════════════════════════════

def test_upstream_ahead_detected(tmp_path):
    up = _fake_hermes_home(tmp_path / "u")   # a real git repo == valid ls-remote target
    head = _git_head(up)
    r = hvc.upstream_check(str(up), timeout=15, pinned_commit="0" * 40, pinned_version="unknown")
    assert r["status"] == "ahead"
    assert r["head_commit"] == head
    assert r["reachable"] is True
    assert r["ahead"] is True


def test_upstream_at_pin_is_ok(tmp_path):
    up = _fake_hermes_home(tmp_path / "u")
    head = _git_head(up)
    r = hvc.upstream_check(str(up), timeout=15, pinned_commit=head, pinned_version="unknown")
    assert r["status"] == "ok"
    assert r["ahead"] is False
    assert r["reachable"] is True


def test_upstream_network_failure_is_unknown(tmp_path):
    # A path that is not a git repo → ls-remote errors → fail-safe 'unknown'
    r = hvc.upstream_check(str(tmp_path / "nope.git"), timeout=5, pinned_commit="0" * 40, pinned_version="unknown")
    assert r["status"] == "unknown"
    assert r["reachable"] is False
    assert r["ahead"] is False
