"""Tests for the read-only Hermes version-check monitor.

The monitor checks upstream Hermes against the local pin baseline and writes a
report. It MUST NEVER mutate the runtime: no git pull / hermes update / gateway
restart / baseline rewrite / skill install. These tests pin that contract.
"""
from __future__ import annotations

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "hermes-version-check"


def _load():
    # The script is extensionless; use an explicit source loader.
    loader = SourceFileLoader("hermes_version_check", str(SCRIPT))
    spec = importlib.util.spec_from_loader("hermes_version_check", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hvc = _load()

_BASELINE_014 = (
    "# Hermes upstream baseline - generated live during fleet upgrade 2026-05-22\n"
    "HERMES_COMMIT=1e71b7180e5b4e84905b9a3086cf9cecca139562\n"
    "HERMES_VERSION=0.14.0\n"
    "BRIDGE_POST_PATCH_SHA256=94a13a926798d5c2e6e69dd4f227ef940f88e3076bab60333d1b92fa55680913\n"
)


def _local():
    return hvc.parse_baseline(_BASELINE_014)


# --- parse_baseline ---------------------------------------------------------

def test_parse_baseline_extracts_version_commit_and_bridge_patch():
    b = hvc.parse_baseline(_BASELINE_014)
    assert b["version"] == "0.14.0"
    assert b["commit"] == "1e71b7180e5b4e84905b9a3086cf9cecca139562"
    assert b["has_bridge_patch"] is True


def test_parse_baseline_no_bridge_patch():
    b = hvc.parse_baseline("HERMES_COMMIT=abc\nHERMES_VERSION=0.14.0\n")
    assert b["has_bridge_patch"] is False


# --- classify_update --------------------------------------------------------

def test_classify_up_to_date_same_commit():
    local = _local()
    upstream = {"version": "0.14.0", "commit": local["commit"]}
    c = hvc.classify_update(local, upstream)
    assert c["status"] == "up_to_date"
    assert c["severity"] == "none"
    assert c["patch_port_required"] is False


def test_classify_minor_update_detected():
    c = hvc.classify_update(_local(), {"version": "0.17.0", "commit": "f" * 40})
    assert c["status"] == "update_available"
    assert c["severity"] == "minor"
    # custom bridge patch present ⇒ a port is required before any upgrade
    assert c["patch_port_required"] is True
    assert "port" in c["recommended_action"].lower() or "business" in c["recommended_action"].lower()


def test_classify_patch_update():
    c = hvc.classify_update(_local(), {"version": "0.14.1", "commit": "a" * 40})
    assert c["severity"] == "patch"
    assert c["status"] == "update_available"


def test_classify_breaking_major():
    c = hvc.classify_update(_local(), {"version": "1.0.0", "commit": "b" * 40})
    assert c["severity"] == "breaking"


def test_classify_commit_changed_same_version():
    c = hvc.classify_update(_local(), {"version": "0.14.0", "commit": "c" * 40})
    assert c["status"] == "update_available"
    assert c["severity"] == "commit"


def test_classify_no_patch_port_when_no_bridge_patch():
    local = hvc.parse_baseline("HERMES_COMMIT=abc\nHERMES_VERSION=0.14.0\n")
    c = hvc.classify_update(local, {"version": "0.17.0", "commit": "f" * 40})
    assert c["patch_port_required"] is False


# --- build_report -----------------------------------------------------------

def test_build_report_shape():
    local = _local()
    upstream = {"version": "0.17.0", "commit": "f" * 40}
    rep = hvc.build_report(local, upstream, hvc.classify_update(local, upstream), now="2026-06-27T00:00:00Z")
    for key in ("schema_version", "checked_at", "current", "latest", "status",
                "severity", "patch_port_required", "recommended_action", "release_notes_url"):
        assert key in rep
    assert rep["current"]["version"] == "0.14.0"
    assert rep["latest"]["version"] == "0.17.0"


# --- run_check: no mutation, report written, dry-run --------------------------

def _run(tmp_path, *, upstream, dry_run=False):
    baseline = tmp_path / "hermes-patch-baseline.txt"
    baseline.write_text(_BASELINE_014, encoding="utf-8")
    json_path = tmp_path / "hermes-version-check.json"
    log_path = tmp_path / "hermes-version-check.log"
    rep = hvc.run_check(
        baseline_path=baseline, json_path=json_path, log_path=log_path,
        fetch=lambda **_: dict(upstream), dry_run=dry_run, alert=False,
    )
    return rep, baseline, json_path, log_path


def test_run_writes_report_when_update_available(tmp_path):
    rep, baseline, json_path, log_path = _run(tmp_path, upstream={"version": "0.17.0", "commit": "f" * 40})
    assert rep["status"] == "update_available"
    assert json_path.exists()
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["latest"]["version"] == "0.17.0"
    assert log_path.exists() and "update_available" in log_path.read_text(encoding="utf-8")


def test_run_noop_status_when_current(tmp_path):
    local = _local()
    rep, *_ = _run(tmp_path, upstream={"version": "0.14.0", "commit": local["commit"]})
    assert rep["status"] == "up_to_date"


def test_run_does_not_rewrite_baseline(tmp_path):
    _, baseline, *_ = _run(tmp_path, upstream={"version": "0.17.0", "commit": "f" * 40})
    assert baseline.read_text(encoding="utf-8") == _BASELINE_014  # byte-identical


def test_dry_run_writes_nothing(tmp_path):
    _, _, json_path, log_path = _run(tmp_path, upstream={"version": "0.17.0", "commit": "f" * 40}, dry_run=True)
    assert not json_path.exists()
    assert not log_path.exists()


def test_run_never_raises_on_fetch_failure(tmp_path):
    baseline = tmp_path / "hermes-patch-baseline.txt"
    baseline.write_text(_BASELINE_014, encoding="utf-8")

    def _boom(**_):
        raise RuntimeError("network down")

    rep = hvc.run_check(
        baseline_path=baseline, json_path=tmp_path / "r.json", log_path=tmp_path / "r.log",
        fetch=_boom, dry_run=True, alert=False,
    )
    assert rep["status"] in ("unknown", "check_failed")


# --- static no-mutation contract --------------------------------------------

def test_script_contains_no_mutating_commands():
    src = SCRIPT.read_text(encoding="utf-8")
    forbidden = [
        "hermes update", "hermes skills install", "hermes skills update",
        "git pull", "git fetch", "git merge", "git checkout", "git reset",
        "systemctl restart", "systemctl stop", "systemctl start",
        "systemctl enable", "systemctl disable", "pip install",
    ]
    for token in forbidden:
        assert token not in src, f"version-check must not contain a mutating command: {token!r}"
    # it MUST use the read-only upstream probe
    assert "ls-remote" in src


def test_deploy_wiring_installs_script_units_and_enables_timer():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    systemd = REPO / "src" / "agents" / "shift" / "systemd"
    # script ships via the shift-scripts install glob; units via the shift-systemd globs
    assert "install -m 755 src/agents/shift/scripts/*" in deploy
    assert "install -m 644 src/agents/shift/systemd/*.service" in deploy
    assert "install -m 644 src/agents/shift/systemd/*.timer" in deploy
    # the timer is explicitly enabled
    assert "systemctl enable --now hermes-version-check.timer" in deploy
    # all three units exist and the service runs the installed script
    assert (systemd / "hermes-version-check.timer").exists()
    assert (systemd / "hermes-version-check-failure.service").exists()
    svc = (systemd / "hermes-version-check.service").read_text(encoding="utf-8")
    assert "/usr/local/bin/hermes-version-check" in svc
    assert "Type=oneshot" in svc
    # the script declares a shebang (installed -m 755, run via venv python in the unit)
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")
