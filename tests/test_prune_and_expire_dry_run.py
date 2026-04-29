"""prune-and-expire-expenses --dry-run flag — smoke marker contract.

Validates the smoke-test step 12 integration:
- valid config + enabled=False → exit 0 + stdout `SMOKE_OK`
- valid config + enabled=True → exit 0 + stdout `SMOKE_OK` (early-return BEFORE enabled gate)
- missing config.yaml → exit 1 + stdout `SMOKE_FAIL: FileNotFoundError: ...`
- corrupt YAML → exit 1 + stdout `SMOKE_FAIL: RuntimeError: ...`
- absence of --dry-run leaves default behavior unchanged (exit 0 silent no-op when disabled)

Linux-only via importlib + safe_io (which imports fcntl).
"""
from __future__ import annotations
import importlib.machinery
import importlib.util
import platform
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="prune script depends on safe_io which imports fcntl",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
PRUNE_SCRIPT = _REPO_ROOT / "src" / "agents" / "expense_bookkeeper" / "scripts" / "prune-and-expire-expenses.py"
PLATFORM_DIR = _REPO_ROOT / "src" / "platform"


def _make_config(tmp_path: Path, *, enabled: bool = False) -> Path:
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "L1", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": ""},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {"enabled": enabled, "qbo_client_mode": "mock"},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


def _run_dry(env_dir: Path, *, with_dry_run: bool = True):
    """Invoke prune script via importlib wrapper with overridden CONFIG_PATH."""
    config_path = env_dir / "config.yaml"
    extra_arg = '"--dry-run", ' if with_dry_run else ""
    wrapper = f"""
import sys, pathlib
import importlib.util, importlib.machinery
sys.argv = ["prune-and-expire-expenses", {extra_arg}]
loader = importlib.machinery.SourceFileLoader("prune", {str(PRUNE_SCRIPT)!r})
spec = importlib.util.spec_from_file_location("prune", {str(PRUNE_SCRIPT)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(pathlib.Path({str(PLATFORM_DIR)!r})))
spec.loader.exec_module(mod)
mod.CONFIG_PATH = pathlib.Path({str(config_path)!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'leads.json')!r})
mod.RECEIPTS_DIR = pathlib.Path({str(env_dir / 'receipts')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'decisions.log')!r})
sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=10,
    )


def test_dry_run_emits_smoke_ok_when_disabled(tmp_path):
    _make_config(tmp_path, enabled=False)
    r = _run_dry(tmp_path)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "SMOKE_OK" in r.stdout, r.stdout
    assert "SMOKE_FAIL" not in r.stdout


def test_dry_run_emits_smoke_ok_when_enabled(tmp_path):
    """--dry-run early-returns BEFORE the `enabled` gate, so enabled=True
    also yields SMOKE_OK. Regression guard: smoke must validate config-load
    on opt-in customers AND opt-in-disabled customers identically."""
    _make_config(tmp_path, enabled=True)
    r = _run_dry(tmp_path)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "SMOKE_OK" in r.stdout, r.stdout


def test_dry_run_missing_config_emits_smoke_fail(tmp_path):
    """No config.yaml file → load_yaml_model raises FileNotFoundError →
    SMOKE_FAIL marker emitted on stdout for smoke-test.sh to surface.

    Asserts LINE-START match (anchored ^SMOKE_FAIL:), not just substring —
    smoke-test.sh's grep uses `^SMOKE_FAIL:` so a future logging-prefix
    regression that breaks the line-start contract must fail this test.
    """
    r = _run_dry(tmp_path)
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "SMOKE_FAIL: FileNotFoundError" in r.stdout, r.stdout
    # Line-start invariant — matches smoke-test.sh's anchored grep
    assert any(line.startswith("SMOKE_FAIL: ") for line in r.stdout.splitlines()), (
        f"SMOKE_FAIL: must appear at line start (smoke uses ^SMOKE_FAIL: grep). "
        f"stdout: {r.stdout!r}"
    )


def test_dry_run_corrupt_yaml_emits_smoke_fail(tmp_path):
    """Malformed YAML → RuntimeError from load_yaml_model → SMOKE_FAIL
    marker. Helper does NOT rename-quarantine (PR #34 contract)."""
    p = tmp_path / "config.yaml"
    p.write_text("name: triveni\ncount: : invalid yaml :\n", encoding="utf-8")
    r = _run_dry(tmp_path)
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert "SMOKE_FAIL: RuntimeError" in r.stdout, r.stdout
    assert p.exists(), "load_yaml_model must NOT rename config.yaml on parse error"
    assert list(tmp_path.glob("config.yaml.corrupt-*")) == []


def test_no_dry_run_leaves_default_behavior_unchanged(tmp_path):
    """Without --dry-run, opt-in-disabled customers still see exit 0 silent
    no-op. Regression guard: --dry-run is purely additive, not a behavioral
    flip for the production path."""
    _make_config(tmp_path, enabled=False)
    r = _run_dry(tmp_path, with_dry_run=False)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "SMOKE_OK" not in r.stdout
    assert "SMOKE_FAIL" not in r.stdout
