"""Pytest fixtures for cockpit tests.

- Forces COCKPIT_TEST_MODE=1 globally → Settings uses a tempdir for state paths
  instead of /opt/shift-agent.
- Prepends SME-Agents/src/ to sys.path so the agent's `safe_io` and `schemas`
  modules are importable without /opt/shift-agent existing on the runner.
- Resets the `get_settings()` lru_cache between tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Set BEFORE any app.* import in any test
os.environ.setdefault("COCKPIT_TEST_MODE", "1")
os.environ.setdefault("COCKPIT_JWT_SECRET", "0" * 64)
os.environ.setdefault("PUSHOVER_APP_TOKEN", "stub")
os.environ.setdefault("PUSHOVER_USER_KEY", "stub")

# Make the agent's safe_io + schemas importable from the project's src/.
# The cockpit code does `sys.path.insert(0, "/opt/shift-agent")` at import
# time which obviously fails outside the deployment box. Prepending src/
# lets the same imports succeed against the source tree.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC = _PROJECT_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Clear cached Settings between tests so monkeypatched paths take effect."""
    from app import config as cfg_mod

    cfg_mod.get_settings.cache_clear()
    yield
    cfg_mod.get_settings.cache_clear()
