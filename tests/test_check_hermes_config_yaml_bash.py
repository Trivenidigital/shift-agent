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
    """C13 — override with empty reason → exit 1 with explicit incomplete message.

    Asserts the bash wrapper takes the "incomplete attestation" branch (not
    the plain fail-closed branch). Catches regressions that remove the
    explicit elif and let empty-REASON fall through to the no-override path.
    """
    p = _write_yaml(tmp_path, _FAIL_FIXTURE)
    r = _run(p, env_extra={
        "HERMES_CONFIG_GATE_OVERRIDE_FIELD": "model.default",
        "HERMES_CONFIG_GATE_OVERRIDE_REASON": "",
    })
    assert r.returncode == 1
    assert "incomplete" in r.stderr.lower(), (
        f"Expected 'incomplete' substring in stderr (incomplete-attestation branch); got: {r.stderr}"
    )


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
    """OVERRIDE_FIELD set but REASON unset → exit 1 with explicit incomplete message.

    Asserts the bash wrapper hits the "incomplete" elif branch (not the plain
    no-override fall-through). Catches regressions that drop the elif and let
    field-only requests pass through silently.
    """
    p = _write_yaml(tmp_path, _FAIL_FIXTURE)
    # Set FIELD only; REASON inherited from os.environ (unset by default in test).
    env = {"HERMES_CONFIG_GATE_OVERRIDE_FIELD": "model.default"}
    r = _run(p, env_extra=env)
    assert r.returncode == 1
    assert "incomplete" in r.stderr.lower(), (
        f"Expected 'incomplete' substring in stderr (incomplete-attestation branch); got: {r.stderr}"
    )
