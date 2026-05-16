"""Exit-code matrix tests for check-hermes-config-yaml gate.

Tests the Python helper via subprocess invocation. The bash wrapper's
override path is tested separately in test_check_hermes_config_yaml_bash.py
(requires bash; skipped if not available).

Mirrors tests/test_catering_v02_scripts.py pattern: pytest.mark.skipif(Windows)
+ subprocess.run + returncode + stderr-substring + JSON-stdout assertions.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

# Skip on Windows: the helper itself is stdlib + PyYAML and would run, but the
# wrapper script at src/platform/scripts/check-hermes-config-yaml has no .py
# extension which trips Windows subprocess invocation. CI runs on Linux.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="subprocess invocation expects POSIX shebang on the wrapper",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "src" / "platform" / "scripts" / "check-hermes-config-yaml"
BASELINE = REPO_ROOT / "tools" / "hermes-config-yaml-baseline.txt"


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _run(config_path: Path, *, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HELPER), "--json", "--baseline", str(BASELINE), str(config_path)],
        capture_output=True,
        text=True,
        env=env,
    )


def _envelope(stdout: str) -> dict:
    """Parse the JSON envelope from helper stdout."""
    return json.loads(stdout)


# ─────────────────────────────────────────────────────────────────
# C1 — clean config → exit 0
# ─────────────────────────────────────────────────────────────────
def test_c1_clean_config_passes(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  vision:
    provider: auto
    model: openai/gpt-4o-mini
""")
    r = _run(p)
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["ok"] is True
    assert env["missing_required"] == []
    assert env["wrong_shape"] == []


# ─────────────────────────────────────────────────────────────────
# C2 — missing model.default → exit 1
# ─────────────────────────────────────────────────────────────────
def test_c2_missing_model_default(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  provider: openrouter
""")
    r = _run(p)
    assert r.returncode == 1
    env = _envelope(r.stdout)
    assert "model.default" in env["missing_required"]
    assert "model.default" in r.stderr


# ─────────────────────────────────────────────────────────────────
# C3 — typo'd model.dafault: model.default still missing, "dafault" not a
# known model-subkey but we don't enumerate model subkeys (only auxiliary).
# So C3 surfaces the missing-required failure only.
# ─────────────────────────────────────────────────────────────────
def test_c3_typo_in_model_subkey(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  dafault: openai/gpt-4o-mini
  provider: openrouter
""")
    r = _run(p)
    assert r.returncode == 1
    env = _envelope(r.stdout)
    assert "model.default" in env["missing_required"]


# ─────────────────────────────────────────────────────────────────
# C4 — model.default is integer (wrong shape) → exit 1
# ─────────────────────────────────────────────────────────────────
def test_c4_model_default_wrong_type(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: 42
  provider: openrouter
""")
    r = _run(p)
    assert r.returncode == 1
    env = _envelope(r.stdout)
    fields = [w["field"] for w in env["wrong_shape"]]
    assert "model.default" in fields


# ─────────────────────────────────────────────────────────────────
# C5 — auxiliary.vision.provider: invalid value
# ─────────────────────────────────────────────────────────────────
def test_c5_vision_provider_invalid(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  vision:
    provider: invalidvendor
    model: foo/bar
""")
    r = _run(p)
    assert r.returncode == 1
    env = _envelope(r.stdout)
    fields = [w["field"] for w in env["wrong_shape"]]
    assert "auxiliary.vision.provider" in fields


# ─────────────────────────────────────────────────────────────────
# C6 — provider_routing.sort bad value: advisory only, exit 0
# ─────────────────────────────────────────────────────────────────
def test_c6_provider_routing_sort_advisory(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
provider_routing:
  sort: notavalidvalue
""")
    r = _run(p)
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["ok"] is True
    assert len(env["advisory_warnings"]) >= 1
    assert any("provider_routing.sort" in w for w in env["advisory_warnings"])


# ─────────────────────────────────────────────────────────────────
# C7 — auxiliary.vision absent entirely: silent OK (conditional)
# ─────────────────────────────────────────────────────────────────
def test_c7_auxiliary_vision_absent(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
""")
    r = _run(p)
    assert r.returncode == 0
    env = _envelope(r.stdout)
    assert env["ok"] is True


# ─────────────────────────────────────────────────────────────────
# C8 — auxiliary.visoin (typo subkey) → WARN, exit 0
# ─────────────────────────────────────────────────────────────────
def test_c8_auxiliary_subkey_typo_warn(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  visoin:
    provider: openai
""")
    r = _run(p)
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    # Unknown sub-key flagged
    parents = [u["parent"] for u in env["unknown_subkeys"]]
    keys = [u["key"] for u in env["unknown_subkeys"]]
    assert "auxiliary" in parents
    assert "visoin" in keys


# ─────────────────────────────────────────────────────────────────
# C9 — malformed YAML → exit 2
# ─────────────────────────────────────────────────────────────────
def test_c9_malformed_yaml(tmp_path):
    p = _write_yaml(tmp_path, "model: [unterminated")
    r = _run(p)
    assert r.returncode == 2
    env = _envelope(r.stdout)
    assert env["ok"] is False
    assert "could not parse YAML" in env["error"] or "parse" in env["error"].lower()


# ─────────────────────────────────────────────────────────────────
# C10 — empty file: yaml.safe_load returns None → exit 2
# ─────────────────────────────────────────────────────────────────
def test_c10_empty_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("", encoding="utf-8")
    r = _run(p)
    assert r.returncode == 2
    env = _envelope(r.stdout)
    assert env["ok"] is False
    assert "empty or non-mapping" in env["error"].lower()


# ─────────────────────────────────────────────────────────────────
# C11 — dangling symlink → exit 2
# ─────────────────────────────────────────────────────────────────
def test_c11_dangling_symlink(tmp_path):
    target = tmp_path / "missing-target.yaml"
    link = tmp_path / "config.yaml"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this filesystem")
    r = _run(link)
    assert r.returncode == 2
    env = _envelope(r.stdout)
    assert env["ok"] is False
    assert "missing" in env["error"].lower() or "unreadable" in env["error"].lower()


# ─────────────────────────────────────────────────────────────────
# Security: never echo raw config values in wrong_shape.got (D1-1)
# ─────────────────────────────────────────────────────────────────
def test_security_no_raw_value_in_wrong_shape_got(tmp_path):
    """If an operator accidentally pastes an API key into model.default,
    the JSON envelope must NOT echo it back in wrong_shape[*].got."""
    fake_secret = "sk-1234567890abcdef-FAKE"
    p = _write_yaml(tmp_path, f"""\
model:
  default: {fake_secret}
  provider: openrouter
""")
    r = _run(p)
    # The value lacks "/", so it triggers a wrong_shape entry.
    assert r.returncode == 1
    env = _envelope(r.stdout)
    fields = [w["field"] for w in env["wrong_shape"]]
    assert "model.default" in fields
    # The fake secret must NOT appear in the JSON envelope.
    assert fake_secret not in r.stdout, "raw config value leaked into JSON stdout"
    assert fake_secret not in r.stderr, "raw config value leaked into stderr"
