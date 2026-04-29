"""PR-D2 commit 1: yaml.safe_load → load_yaml_model migration.

Static check across all 5 catering scripts (per design v2 §3 + plan v2):
- Old pattern `yaml.safe_load(...)` is GONE.
- New pattern `load_yaml_model(...)` is present.
- `log_config_load_failed_best_effort` is wired into the except block.
- `SHIFT_AGENT_CONFIG_PATH` env-var override is honored.

Subprocess-level tests (assert EXIT_SCHEMA_VIOLATION + decisions.log row)
would need fcntl + /opt/shift-agent — defer to commit 7 alongside the
5 PR-A R3 test gaps that all need Linux VPS context.
"""
from __future__ import annotations
from pathlib import Path

import pytest


_CATERING_SCRIPTS = [
    "src/agents/catering/scripts/apply-catering-owner-decision",
    "src/agents/catering/scripts/create-catering-lead",
    "src/agents/catering/scripts/parse-menu-photo",
    "src/agents/catering/scripts/apply-menu-update",
    "src/agents/catering/scripts/lookup-prior-leads-by-phone",
]


@pytest.mark.parametrize("script_path", _CATERING_SCRIPTS)
def test_no_inline_yaml_safe_load(script_path):
    """All 5 scripts removed inline yaml.safe_load — replaced by
    load_yaml_model chokepoint."""
    text = (Path(__file__).resolve().parent.parent / script_path).read_text(encoding="utf-8")
    assert "yaml.safe_load" not in text, (
        f"{script_path} still uses yaml.safe_load — migration incomplete"
    )


@pytest.mark.parametrize("script_path", _CATERING_SCRIPTS)
def test_uses_load_yaml_model(script_path):
    """All 5 scripts import + call load_yaml_model."""
    text = (Path(__file__).resolve().parent.parent / script_path).read_text(encoding="utf-8")
    assert "load_yaml_model" in text, (
        f"{script_path} does not import load_yaml_model"
    )


@pytest.mark.parametrize("script_path", _CATERING_SCRIPTS)
def test_wires_config_load_failed_audit(script_path):
    """All 5 scripts wire log_config_load_failed_best_effort into the
    config-load except block."""
    text = (Path(__file__).resolve().parent.parent / script_path).read_text(encoding="utf-8")
    assert "log_config_load_failed_best_effort" in text, (
        f"{script_path} does not call log_config_load_failed_best_effort"
    )
    assert "from audit_helpers import" in text, (
        f"{script_path} does not import audit_helpers"
    )


@pytest.mark.parametrize("script_path", _CATERING_SCRIPTS)
def test_honors_config_path_env_override(script_path):
    """All 5 scripts use SHIFT_AGENT_CONFIG_PATH env var for test override."""
    text = (Path(__file__).resolve().parent.parent / script_path).read_text(encoding="utf-8")
    assert 'SHIFT_AGENT_CONFIG_PATH' in text, (
        f"{script_path} hardcodes CONFIG_PATH (no env-var override)"
    )
