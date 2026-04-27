"""Pytest fixtures for cockpit tests.

Forces COCKPIT_TEST_MODE=1 globally so Settings.from_env() uses tempdir paths
instead of /opt/shift-agent. Resets the get_settings() lru_cache between tests
so per-test path overrides take effect.
"""
from __future__ import annotations

import os

# Set BEFORE any app.* import in any test
os.environ.setdefault("COCKPIT_TEST_MODE", "1")
os.environ.setdefault("COCKPIT_JWT_SECRET", "0" * 64)
os.environ.setdefault("PUSHOVER_APP_TOKEN", "stub")
os.environ.setdefault("PUSHOVER_USER_KEY", "stub")

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Clear cached Settings between tests so monkeypatched paths take effect."""
    from app import config as cfg_mod

    cfg_mod.get_settings.cache_clear()
    yield
    cfg_mod.get_settings.cache_clear()
