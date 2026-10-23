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


# Loadout-conformance block (plugins + gateway toolsets) mirroring the live
# production box /root/.hermes/config.yaml as verified 2026-10-23. Appended to
# every fixture that is meant to pass, so the original shape-gate cases keep
# testing exactly the field they were written for.
_LOADOUT = """plugins:
  enabled:
  - cf-router
  - shift-agent-policy
  - shift-agent-read
platform_toolsets:
  whatsapp:
  - hermes-whatsapp
  - shift_agent_read
agent:
  disabled_toolsets:
  - delegation
  - skills
  - browser
  - clarify
  - terminal
  - code_execution
  - file
"""


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
""" + _LOADOUT)
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
# C3 — typo'd model.dafault → BOTH the missing-required AND the unknown
# subkey under model are surfaced. (After PR-review P3-F8 added model.*
# subkey enumeration.)
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
    # Also flagged as unknown subkey under model (P3-F8 enumeration)
    parents = [u["parent"] for u in env["unknown_subkeys"]]
    keys = [u["key"] for u in env["unknown_subkeys"]]
    assert "model" in parents
    assert "dafault" in keys


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
# C5 — auxiliary.vision.provider: uncommon value (advisory, NOT fail).
# Relaxed from strict-whitelist to advisory after the PR-99 post-merge
# finding that Hermes accepts 14+ providers (main, nous, codex, gemini,
# deepseek, etc.) plus custom base_url — strict whitelist would false-
# positive on valid configs.
# ─────────────────────────────────────────────────────────────────
def test_c5_vision_provider_uncommon_is_advisory(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  vision:
    provider: somerareprovider
    model: foo/bar
""" + _LOADOUT)
    r = _run(p)
    # Advisory only — gate passes
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["ok"] is True
    # But emit an advisory WARN so the operator can confirm it isn't a typo
    assert any("auxiliary.vision.provider" in w for w in env["advisory_warnings"])


# ─────────────────────────────────────────────────────────────────
# C5b — auxiliary.vision.provider: known valid value (`main`) passes silently.
# Confirms the relaxation closes the regression reported on PR #99
# (a valid `provider: main` was being rejected).
# ─────────────────────────────────────────────────────────────────
def test_c5b_vision_provider_main_is_valid(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  vision:
    provider: main
    model: openai/gpt-4o-mini
""" + _LOADOUT)
    r = _run(p)
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["ok"] is True
    # `main` is in the common-providers hint set — no advisory either
    assert not any("auxiliary.vision.provider" in w for w in env["advisory_warnings"])


# ─────────────────────────────────────────────────────────────────
# C5c — auxiliary.vision.provider: wrong TYPE still fails closed.
# Confirms the relaxation didn't dilute the fail-closed posture on
# null/int/list — those are still real shape violations Hermes can't
# silently handle.
# ─────────────────────────────────────────────────────────────────
def test_c5c_vision_provider_wrong_type_fails(tmp_path):
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
auxiliary:
  vision:
    provider: 42
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
""" + _LOADOUT)
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
""" + _LOADOUT)
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
""" + _LOADOUT)
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
# C16 — size cap: file larger than 1 MiB → exit 2 (P2-4 closure)
# ─────────────────────────────────────────────────────────────────
def test_c16_size_cap_rejects_large_file(tmp_path):
    p = tmp_path / "config.yaml"
    # Write a 1.5 MiB YAML file. Content is "key: value" repeated — valid YAML
    # in principle but exceeds the size sanity cap before parsing is attempted.
    p.write_text("# " + ("x" * 1_500_000), encoding="utf-8")
    r = _run(p)
    assert r.returncode == 2
    env = _envelope(r.stdout)
    assert env["ok"] is False
    assert "sanity cap" in env["error"].lower() or "exceeds" in env["error"].lower()


# ─────────────────────────────────────────────────────────────────
# C17 — explicit --baseline pointing at a missing file → exit 2
# (P2-1 closure: silent disable of WARN enumeration prevented)
# ─────────────────────────────────────────────────────────────────
def test_c17_explicit_missing_baseline_rejects(tmp_path):
    """Caller passed --baseline to an explicit path that doesn't exist.
    Must fail-closed exit 2 rather than silently disabling unknown-key WARN.
    """
    p = _write_yaml(tmp_path, """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
""")
    missing_baseline = tmp_path / "this-does-not-exist.txt"
    env = {**os.environ}
    r = subprocess.run(
        [sys.executable, str(HELPER), "--json",
         "--baseline", str(missing_baseline),
         str(p)],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2
    res = json.loads(r.stdout)
    assert res["ok"] is False
    assert "baseline" in res["error"].lower() and "missing" in res["error"].lower()


# ─────────────────────────────────────────────────────────────────
# Security: never echo raw config values in wrong_shape.got
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


# ─────────────────────────────────────────────────────────────────
# Loadout conformance (plugins + gateway toolset scoping).
# Source of truth: docs/runbooks/gateway-toolset-scoping.md. A missing
# plugin / toolset entry never errors inside Hermes — the hooks/tools just
# vanish — so the gate is the only place this drift surfaces.
# ─────────────────────────────────────────────────────────────────
from check_hermes_config_yaml import (  # noqa: E402  (conftest puts src/platform on sys.path)
    RECOMMENDED_DISABLED_TOOLSETS,
    REQUIRED_DISABLED_TOOLSETS,
    REQUIRED_PLUGINS,
    REQUIRED_WHATSAPP_TOOLSETS,
)

_MODEL = """\
model:
  default: openai/gpt-4o-mini
  provider: openrouter
"""

# Mirrors the live production box /root/.hermes/config.yaml loadout sections
# as verified 2026-10-23 (plugins.enabled, platform_toolsets.whatsapp,
# agent.disabled_toolsets — 7 entries, matching the runbook).
_BOX_MIRROR = _MODEL + _LOADOUT


def _without(content: str, entry: str) -> str:
    """Drop exactly one `  - <entry>` list line from a fixture."""
    line = f"  - {entry}\n"
    assert content.count(line) == 1, entry
    return content.replace(line, "")


def test_loadout_box_mirror_passes_with_zero_fail(tmp_path):
    r = _run(_write_yaml(tmp_path, _BOX_MIRROR))
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["ok"] is True
    assert env["missing_required"] == []
    assert env["wrong_shape"] == []
    assert not any("disabled_toolsets" in w for w in env["advisory_warnings"])


def test_loadout_required_sets_match_runbook_posture():
    assert set(REQUIRED_PLUGINS) == {"cf-router", "shift-agent-policy", "shift-agent-read"}
    assert REQUIRED_WHATSAPP_TOOLSETS == ("shift_agent_read",)
    assert set(REQUIRED_DISABLED_TOOLSETS) == {"terminal", "skills", "code_execution"}
    assert set(RECOMMENDED_DISABLED_TOOLSETS) == {"delegation", "browser", "clarify", "file"}
    runbook = (REPO_ROOT / "docs" / "runbooks" / "gateway-toolset-scoping.md").read_text(
        encoding="utf-8"
    )
    for toolset in REQUIRED_DISABLED_TOOLSETS + RECOMMENDED_DISABLED_TOOLSETS:
        assert f"`{toolset}`" in runbook, toolset


@pytest.mark.parametrize("plugin", REQUIRED_PLUGINS)
def test_loadout_missing_plugin_fails(tmp_path, plugin):
    r = _run(_write_yaml(tmp_path, _without(_BOX_MIRROR, plugin)))
    assert r.returncode == 1, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    field_name = f"plugins.enabled:{plugin}"
    assert env["missing_required"] == [field_name]
    assert "silently vanish" in env["missing_reasons"][field_name]
    assert field_name in r.stderr and "silently vanish" in r.stderr


def test_loadout_plugins_block_absent_fails_every_plugin(tmp_path):
    content = _BOX_MIRROR.replace(
        "plugins:\n  enabled:\n  - cf-router\n  - shift-agent-policy\n  - shift-agent-read\n", ""
    )
    assert "plugins:" not in content
    r = _run(_write_yaml(tmp_path, content))
    assert r.returncode == 1
    env = _envelope(r.stdout)
    assert env["missing_required"] == [f"plugins.enabled:{p}" for p in REQUIRED_PLUGINS]


def test_loadout_missing_shift_agent_read_toolset_fails(tmp_path):
    content = _BOX_MIRROR.replace(
        "  whatsapp:\n  - hermes-whatsapp\n  - shift_agent_read\n",
        "  whatsapp:\n  - hermes-whatsapp\n",
    )
    r = _run(_write_yaml(tmp_path, content))
    assert r.returncode == 1, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    field_name = "platform_toolsets.whatsapp:shift_agent_read"
    assert env["missing_required"] == [field_name]
    assert "all 5" in env["missing_reasons"][field_name]
    assert field_name in r.stderr


@pytest.mark.parametrize("toolset", REQUIRED_DISABLED_TOOLSETS)
def test_loadout_missing_required_disabled_toolset_fails(tmp_path, toolset):
    r = _run(_write_yaml(tmp_path, _without(_BOX_MIRROR, toolset)))
    assert r.returncode == 1, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    field_name = f"agent.disabled_toolsets:{toolset}"
    assert env["missing_required"] == [field_name]
    assert "gateway-toolset-scoping.md" in env["missing_reasons"][field_name]


@pytest.mark.parametrize("toolset", RECOMMENDED_DISABLED_TOOLSETS)
def test_loadout_missing_recommended_disabled_toolset_is_warn_only(tmp_path, toolset):
    r = _run(_write_yaml(tmp_path, _without(_BOX_MIRROR, toolset)))
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["ok"] is True
    assert env["missing_required"] == []
    assert any(
        f"'{toolset}'" in w and "docs/runbooks/gateway-toolset-scoping.md" in w
        for w in env["advisory_warnings"]
    )
    assert "ADVISORY" in r.stderr


def test_loadout_superset_passes(tmp_path):
    """Extra plugins / whatsapp toolsets / disables never fail or warn."""
    content = (_BOX_MIRROR
               .replace("  - shift-agent-read\n", "  - shift-agent-read\n  - some-future-plugin\n")
               .replace("  - shift_agent_read\n", "  - shift_agent_read\n  - another_toolset\n")
               .replace("  - file\n", "  - file\n  - web\n"))
    r = _run(_write_yaml(tmp_path, content))
    assert r.returncode == 0, f"stderr={r.stderr}"
    env = _envelope(r.stdout)
    assert env["missing_required"] == []
    assert not any("disabled_toolsets" in w for w in env["advisory_warnings"])
