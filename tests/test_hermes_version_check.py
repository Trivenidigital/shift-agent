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


# ════════════════════════════════════════════════════════════════════════════
# Task 4 — main() orchestration: evaluate() brain (cross-platform) + e2e
# ════════════════════════════════════════════════════════════════════════════

def _baseline_file(tmp_path, commit, sha):
    p = tmp_path / "baseline.txt"
    p.write_text(
        f"HERMES_COMMIT={commit}\nHERMES_VERSION=unknown\nBRIDGE_POST_PATCH_SHA256={sha}\n",
        encoding="utf-8",
    )
    return p


def _now():
    return datetime.now(timezone.utc)


# ── evaluate() brain — runs everywhere (git reads are cross-platform) ────────

def test_evaluate_clean_no_conditions(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = {"commit": _git_head(home), "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    report, decision, state = hvc.evaluate(
        home, baseline, {}, {}, now=_now(), network_fail_after=3, skip_upstream=True)
    assert report["active_conditions"] == []
    assert report["mutation_performed"] is False
    assert report["runtime_status"] == "match"
    assert decision["action"] == "not_needed" and decision["notify"] is False


def test_evaluate_commit_drift_sent_then_suppressed(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = {"commit": "0" * 40, "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    now = _now()
    r1, d1, s1 = hvc.evaluate(home, baseline, {}, {}, now=now, network_fail_after=3, skip_upstream=True)
    assert "runtime_commit_drift" in r1["active_conditions"]
    assert d1["action"] == "sent" and d1["notify"] is True
    # evaluate() no longer advances the throttle signature itself — main() does
    # that ONLY on confirmed delivery. Simulate a delivered alert by persisting
    # the signature, then the next evaluate suppresses.
    delivered_state = dict(s1, last_alert_signature=d1["signature"])
    r2, d2, s2 = hvc.evaluate(home, baseline, {}, delivered_state, now=now, network_fail_after=3, skip_upstream=True)
    assert d2["action"] == "suppressed" and d2["notify"] is False


def test_evaluate_baseline_none_is_hard(tmp_path):
    home = _fake_hermes_home(tmp_path)
    report, decision, state = hvc.evaluate(
        home, None, {}, {}, now=_now(), network_fail_after=3, skip_upstream=True)
    assert "baseline_unreadable" in report["active_conditions"]
    assert report["baseline_status"] == "unreadable"
    assert decision["notify"] is True and decision["priority"] == 1


def test_evaluate_upstream_ahead_sets_patch_port_review(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = {"commit": _git_head(home), "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    upstream = {"status": "ahead", "head_commit": "f" * 40, "latest_tag": "v0.17", "reachable": True, "ahead": True}
    report, decision, state = hvc.evaluate(
        home, baseline, upstream, {}, now=_now(), network_fail_after=3, skip_upstream=False)
    assert report["upstream_status"] == "ahead"
    assert report["patch_port_review"] == "required"
    assert "patch_port_review_required" in report["active_conditions"]
    assert decision["priority"] == 0  # advisory/soft


def test_evaluate_upstream_unknown_increments_network_failures(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = {"commit": _git_head(home), "version": "unknown", "bridge_sha256": hvc.bridge_sha(home)}
    upstream = {"status": "unknown", "head_commit": "", "latest_tag": "", "reachable": False, "ahead": False}
    prev = {"consecutive_network_failures": 0}
    report, decision, state = hvc.evaluate(
        home, baseline, upstream, prev, now=_now(), network_fail_after=3, skip_upstream=False)
    assert report["upstream_status"] == "unknown"
    assert state["consecutive_network_failures"] == 1
    assert "upstream_check_failed" in report["active_conditions"]


# ── main() dry-run — cross-platform (no file writes, no alert dispatch) ───────

def test_main_dry_run_writes_nothing(tmp_path, capsys):
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, "0" * 40, hvc.bridge_sha(home))
    report = tmp_path / "r.json"
    state = tmp_path / "s.json"
    rc = hvc.main([
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report), "--state-path", str(state),
        "--skip-upstream", "--dry-run", "--text",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "runtime_commit_drift" in out
    assert "mutation_performed=0" in out
    assert not report.exists() and not state.exists()


# ── main() e2e via subprocess — clean + no-mutation run on Windows too (notify
#    dispatch to a bogus bin fails gracefully); the notify-capture test is POSIX.

def test_main_clean_run_writes_report_no_alert(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, _git_head(home), hvc.bridge_sha(home))
    report = tmp_path / "r.json"
    state = tmp_path / "s.json"
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report), "--state-path", str(state),
        "--log-path", str(tmp_path / "d.log"),
        "--skip-upstream", "--text",
        "--notify-owner-bin", str(tmp_path / "nonexistent-notify"),
    ], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "runtime_status=match" in r.stdout
    assert "alert_action=not_needed" in r.stdout
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["mutation_performed"] is False and rep["active_conditions"] == []


def test_run_does_not_mutate_hermes_home_or_baseline(tmp_path):
    home = _fake_hermes_home(tmp_path)
    head = _git_head(home)
    baseline = _baseline_file(tmp_path, "0" * 40, hvc.bridge_sha(home))  # pinned != live → drift
    before_tree = _tree_digest(home)
    before_base = baseline.read_bytes()
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(tmp_path / "r.json"), "--state-path", str(tmp_path / "s.json"),
        "--log-path", str(tmp_path / "d.log"),
        "--skip-upstream", "--text",
        "--notify-owner-bin", str(tmp_path / "nonexistent-notify"),
    ], capture_output=True, text=True, timeout=30)
    # exit 6: a priority-1 drift whose alert could not be delivered (bogus notify
    # bin) escalates via OnFailure — see test_undelivered_drift_alert_is_retried.
    assert r.returncode in (0, 6), r.stderr
    assert "runtime_commit_drift" in r.stdout
    # The whole point: the monitor never mutates Hermes home or the baseline.
    assert _tree_digest(home) == before_tree
    assert _git_head(home) == head
    assert baseline.read_bytes() == before_base


@pytest.mark.skipif(platform.system() == "Windows", reason="needs exec'able notify stub (POSIX)")
def test_commit_drift_alerts_once_then_suppresses(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, "0" * 40, hvc.bridge_sha(home))
    notify = tmp_path / "notify"
    out = tmp_path / "alerts.txt"
    notify.write_text(
        "#!/usr/bin/env python3\nimport sys, pathlib\n"
        "pathlib.Path(" + repr(str(out)) + ").open('a').write('|'.join(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    notify.chmod(0o755)
    report = tmp_path / "r.json"
    state = tmp_path / "s.json"
    cmd = [
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report), "--state-path", str(state),
        "--log-path", str(tmp_path / "d.log"), "--skip-upstream", "--text",
        "--notify-owner-bin", str(notify),
    ]
    a = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    b = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert "alert_action=sent" in a.stdout
    assert "alert_action=suppressed" in b.stdout
    assert out.read_text(encoding="utf-8").count("\n") == 1  # exactly one alert across two runs


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX file mode bits")
def test_unsafe_permissions_detected(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, _git_head(home), hvc.bridge_sha(home))
    state = tmp_path / "s.json"
    state.write_text("{}", encoding="utf-8")
    os.chmod(state, 0o666)  # world-writable → unsafe (a priority-1 hard condition)
    report = tmp_path / "r.json"
    # A WORKING notify stub so the priority-1 page DELIVERS (→ exit 0). With a
    # bogus bin the page would (correctly) escalate to exit 6 instead.
    notify, out = _notify_capture(tmp_path)
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report), "--state-path", str(state),
        "--skip-upstream", "--text", "--notify-owner-bin", str(notify),
    ], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "unsafe_permissions" in r.stdout
    assert out.read_text(encoding="utf-8").count("\n") == 1  # the unsafe-perms page was sent


def test_dispatch_alert_missing_bin_is_graceful():
    # Alert delivery failure must NEVER raise (would crash a read-only monitor).
    ok, detail = hvc.dispatch_alert(str(Path("/nonexistent/notify-bin-xyz")), "t", 1, "m")
    assert ok is False
    assert ("notify_error" in detail) or detail.startswith("rc=")


# ════════════════════════════════════════════════════════════════════════════
# Task 6 — static boundary guards (read the SHIPPED files; enforce read-only)
# ════════════════════════════════════════════════════════════════════════════

SYSTEMD = REPO / "src" / "platform" / "systemd"
SERVICE = SYSTEMD / "hermes-version-check.service"
TIMER = SYSTEMD / "hermes-version-check.timer"
FAILURE = SYSTEMD / "hermes-version-check-failure.service"


def test_script_uses_only_readonly_git_subcommands():
    txt = SCRIPT.read_text(encoding="utf-8")
    # Quoted tokens = actual subprocess args; descriptive prose uses bare words.
    for verb in ('"clone"', '"fetch"', '"checkout"', '"pull"', '"reset"',
                 '"push"', '"merge"'):
        assert verb not in txt, f"mutating git arg present in script: {verb}"
    assert '"rev-parse"' in txt   # the only local git read
    assert '"ls-remote"' in txt   # the only network git read


def test_script_never_invokes_systemctl_or_writes_baseline():
    txt = SCRIPT.read_text(encoding="utf-8")
    assert '"systemctl"' not in txt        # never invokes systemctl as a subprocess arg
    assert "os.system(" not in txt         # no shell-out backdoor
    assert "_atomic_write_json(args.baseline" not in txt   # never rewrites the baseline
    assert "mutation_performed" in txt      # report always carries the no-mutation flag


def test_service_is_read_only_and_wires_failure_handler():
    s = SERVICE.read_text(encoding="utf-8")
    assert "OnFailure=hermes-version-check-failure.service" in s
    assert "ProtectSystem=strict" in s
    assert "ReadWritePaths=/opt/shift-agent" in s
    assert "User=shift-agent" in s
    assert "ExecStartPost" not in s   # no post-hooks that could mutate
    assert "systemctl" not in s


def test_timer_is_daily_and_targets_the_service():
    t = TIMER.read_text(encoding="utf-8")
    assert "OnCalendar=" in t
    assert "Unit=hermes-version-check.service" in t


def test_failure_service_alerts_owner_without_recursion():
    f = FAILURE.read_text(encoding="utf-8")
    assert "/usr/local/bin/shift-agent-notify-owner" in f
    # No ACTIVE OnFailure= directive (a comment may mention it); avoid recursion.
    assert not any(line.strip().startswith("OnFailure=") for line in f.splitlines())
    assert "--priority 1" in f


# ════════════════════════════════════════════════════════════════════════════
# Task 7 — deploy + smoke wiring (static guards; idempotent, read-only)
# ════════════════════════════════════════════════════════════════════════════

DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
SMOKE = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh"


def test_deploy_wires_monitor_idempotently():
    d = DEPLOY.read_text(encoding="utf-8")
    assert "systemctl enable --now hermes-version-check.timer" in d
    assert "install -m 644 src/platform/systemd/*.timer /etc/systemd/system/" in d
    assert "install -m 644 tools/hermes-patch-baseline.txt /opt/shift-agent/hermes-patch-baseline.txt" in d


def test_deploy_never_writes_canonical_baseline():
    d = DEPLOY.read_text(encoding="utf-8")
    # The snapshot is one-directional: repo tools/ -> /opt/shift-agent/. Deploy
    # must never write back into the canonical tools/hermes-patch-baseline.txt.
    assert "/opt/shift-agent/hermes-patch-baseline.txt tools/" not in d
    assert "> tools/hermes-patch-baseline.txt" not in d


def test_smoke_checks_monitor_presence_and_dry_run():
    s = SMOKE.read_text(encoding="utf-8")
    assert "/usr/local/bin/hermes-version-check" in s
    assert "hermes-version-check.timer" in s
    assert "--dry-run" in s   # functional read-only smoke invocation


# ════════════════════════════════════════════════════════════════════════════
# Review fixes — throttle channel-split (BLOCKER 1), delivery-aware throttle
# (BLOCKER 2 / HIGH 1 / MEDIUM 1), corrupt-state safety
# ════════════════════════════════════════════════════════════════════════════

def test_persistent_hard_drift_not_repaged_by_network_blip():
    # A network blip co-occurring with an ALREADY-alerted hard drift must NOT
    # re-page (the transient network condition is excluded from the signature).
    sig = hvc.alert_signature(["runtime_commit_drift"])
    prev = {"last_alert_signature": sig, "consecutive_network_failures": 0,
            "active_conditions": ["runtime_commit_drift"]}
    d = hvc.decide_alert(
        ["runtime_commit_drift", "upstream_check_failed"],
        ["runtime_commit_drift"], ["upstream_check_failed"],
        prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is False


def test_new_hard_drift_with_network_blip_pages_only_the_drift():
    # Brand-new hard drift + first network blip: page the drift (priority 1);
    # the network condition is excluded from the signature so the threshold is
    # not defeated.
    prev = {"last_alert_signature": "", "consecutive_network_failures": 0}
    d = hvc.decide_alert(
        ["runtime_commit_drift", "upstream_check_failed"],
        ["runtime_commit_drift"], ["upstream_check_failed"],
        prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is True and d["priority"] == 1
    assert d["signature"] == hvc.alert_signature(["runtime_commit_drift"])


def test_network_recovery_after_paging_sends_one_recovery():
    prev = {"last_alert_signature": "", "consecutive_network_failures": 3}
    d = hvc.decide_alert([], [], [], prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is True and d["action"] == "recovery"


def test_network_below_threshold_then_recover_sends_nothing():
    prev = {"last_alert_signature": "", "consecutive_network_failures": 2}
    d = hvc.decide_alert([], [], [], prev, now=datetime.now(timezone.utc), network_fail_after=3)
    assert d["notify"] is False and d["action"] == "not_needed"


def test_corrupt_state_file_defaults_safely(tmp_path):
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, _git_head(home), hvc.bridge_sha(home))  # clean
    state = tmp_path / "s.json"
    state.write_text("{{{ not valid json", encoding="utf-8")
    report = tmp_path / "r.json"
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report), "--state-path", str(state),
        "--skip-upstream", "--text", "--notify-owner-bin", str(tmp_path / "none"),
    ], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr           # corrupt state → safe default, no crash
    assert "runtime_status=match" in r.stdout
    # state was rewritten cleanly
    assert json.loads(state.read_text(encoding="utf-8"))["schema_version"] == 1


def test_undelivered_drift_alert_is_retried_next_run(tmp_path):
    # Delivery to a bogus notify bin always fails → the throttle signature is NOT
    # persisted (MEDIUM 1) so the drift re-pages next run, and a priority-1
    # undelivered alert escalates via exit 6 (OnFailure).
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, "0" * 40, hvc.bridge_sha(home))  # drift
    report = tmp_path / "r.json"
    state = tmp_path / "s.json"
    cmd = [
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report), "--state-path", str(state),
        "--skip-upstream", "--text", "--notify-owner-bin", str(tmp_path / "nonexistent"),
    ]
    a = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    b = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert "alert_action=sent" in a.stdout and "alert_delivered ok=0" in a.stdout
    assert "alert_action=sent" in b.stdout          # retried (NOT suppressed) because delivery failed
    assert a.returncode == 6 and b.returncode == 6  # priority-1 undelivered → escalate


def test_report_and_state_are_written_atomically_as_a_pair(tmp_path):
    # Forcing the report path to be a DIRECTORY makes the write fail. The pair
    # writer must leave NEITHER file behind (HIGH 1) and exit 6 (OnFailure owns
    # the page — no in-process alert on a clean run: BLOCKER 2).
    home = _fake_hermes_home(tmp_path)
    baseline = _baseline_file(tmp_path, _git_head(home), hvc.bridge_sha(home))  # clean
    report_dir = tmp_path / "r.json"
    report_dir.mkdir()                              # report path is a dir → write fails
    state = tmp_path / "s.json"
    r = subprocess.run([
        sys.executable, str(SCRIPT),
        "--hermes-home", str(home), "--baseline-path", str(baseline),
        "--report-path", str(report_dir), "--state-path", str(state),
        "--skip-upstream", "--text", "--notify-owner-bin", str(tmp_path / "none"),
    ], capture_output=True, text=True, timeout=30)
    assert r.returncode == 6                         # write failure → monitor-failed (OnFailure)
    assert not state.exists()                        # neither file half-written
    assert "alert_dispatched" not in r.stdout        # clean run → no in-process alert (no double page)
