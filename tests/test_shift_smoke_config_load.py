"""Static guards for shift-agent-smoke-test config.yaml loading.

The deploy smoke gate must use the YAML-aware safe_io.load_yaml_model
chokepoint for operator-edited config.yaml files. Inline yaml.safe_load
duplicates policy and can drift from the no-quarantine contract.
"""
from __future__ import annotations

from pathlib import Path


_SMOKE_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "agents"
    / "shift"
    / "scripts"
    / "shift-agent-smoke-test.sh"
)


def test_smoke_script_uses_yaml_load_chokepoint_for_config():
    text = _SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "load_yaml_model" in text
    assert "from safe_io import load_yaml_model" in text
    assert "yaml.safe_load" not in text
    assert "Config.model_validate(yaml.safe_load" not in text

