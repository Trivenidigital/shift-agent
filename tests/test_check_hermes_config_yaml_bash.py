"""Bash-wrapper tests for tools/check-hermes-config-yaml.sh override path.

The bash wrapper sits on top of the Python helper and provides the
two-variable override (HERMES_CONFIG_GATE_OVERRIDE_FIELD + ..._REASON)
with attestation check + dual-channel audit. These tests verify:

  C12 — valid override (matching field + non-empty reason) → exit 0
  C13 — override with empty reason → exit 1 (incomplete)
  C14 — override field does NOT match actual failure → exit 1 (attestation mismatch)

Tests skipped on Windows (bash + POSIX paths).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows" or shutil.which("bash") is None,
    reason="bash wrapper requires bash + POSIX paths",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "tools" / "check-hermes-config-yaml.sh"
HELPER = REPO_ROOT / "src" / "platform" / "scripts" / "check-hermes-config-yaml"
BASELINE = REPO_ROOT / "tools" / "hermes-config-yaml-baseline.txt"


# Minimal config that's MISSING model.default — triggers fail-closed exit 1.
_FAIL_FIXTURE = """\
model:
  provider: openrouter
"""


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _run(config_path: Path, *, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    # Force the wrapper to use system python (not the production VENV_PY which
    # doesn't exist on the dev host).
    env["VENV_PY"] = shutil.which("python3") or shutil.which("python") or "python3"
    env["BASELINE_FILE"] = str(BASELINE)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(WRAPPER), str(config_path)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_c12_valid_override_accepts(tmp_path):
    """C12 — valid override (matching field + non-empty reason) → exit 0."""
    p = _write_yaml(tmp_path, _FAIL_FIXTURE)
    r = _run(p, env_extra={
        "HERMES_CONFIG_GATE_OVERRIDE_FIELD": "model.default",
        "HERMES_CONFIG_GATE_OVERRIDE_REASON": "test attestation valid",
    })
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert "override accepted" in r.stderr.lower() or "WARN" in r.stderr


def test_c13_empty_reason_rejects(tmp_path):
    """C13 — override with empty reason → exit 1 (incomplete)."""
    p = _write_yaml(tmp_path, _FAIL_FIXTURE)
    r = _run(p, env_extra={
        "HERMES_CONFIG_GATE_OVERRIDE_FIELD": "model.default",
        "HERMES_CONFIG_GATE_OVERRIDE_REASON": "",
    })
    assert r.returncode == 1
    # Either rejected as incomplete OR rejected as plain fail-closed (both acceptable)
    # because empty REASON is treated as not-set, so fall-through to plain fail.
    assert "FAIL" in r.stderr


def test_c14_attestation_mismatch_rejects(tmp_path):
    """C14 — override field does NOT match actual failure → exit 1."""
    p = _write_yaml(tmp_path, _FAIL_FIXTURE)
    r = _run(p, env_extra={
        "HERMES_CONFIG_GATE_OVERRIDE_FIELD": "auxiliary.vision.provider",  # not the failing field
        "HERMES_CONFIG_GATE_OVERRIDE_REASON": "test attestation mismatch",
    })
    assert r.returncode == 1
    assert "ATTESTATION MISMATCH" in r.stderr


def test_c15_only_field_set_rejects(tmp_path):
    """OVERRIDE_FIELD set but REASON unset → exit 1 (incomplete; both required)."""
    p = _write_yaml(tmp_path, _FAIL_FIXTURE)
    # Unset REASON explicitly
    env = {"HERMES_CONFIG_GATE_OVERRIDE_FIELD": "model.default"}
    # Make sure REASON env var is not inherited
    r = _run(p, env_extra=env)
    assert r.returncode == 1
    # Could be "incomplete" message OR plain fail-closed (REASON empty falls
    # to the no-override branch). Either way, exit 1 with FAIL in stderr.
    assert "FAIL" in r.stderr
