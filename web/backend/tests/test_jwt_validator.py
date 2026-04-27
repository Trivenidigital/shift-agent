"""JWT secret startup validator (BL-120 #6).

Uses an isolated reload-context fixture so each test gets a fresh
Settings module + clean env, rather than each test mutating module-level
state and relying on cleanup. Removes ordering brittleness flagged in
Reviewer Nit #3.
"""
from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest


@contextmanager
def reload_config_with(monkeypatch, **env_overrides):
    """Apply env vars + reload config; restore module to sane defaults on exit.

    `monkeypatch` itself handles env-var restoration when the test exits its
    fixture scope. This helper's only job beyond that is to reload the
    `app.config` module twice — once to pick up the test's env, and once
    after the test to put the module back in known-good state for downstream
    tests (config.py reads env at import time).
    """
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)

    from app import config as cfg_mod

    importlib.reload(cfg_mod)
    try:
        yield cfg_mod
    finally:
        monkeypatch.setenv("COCKPIT_TEST_MODE", "1")
        monkeypatch.setenv("COCKPIT_JWT_SECRET", "0" * 64)
        importlib.reload(cfg_mod)


def test_empty_secret_falls_back_to_from_env(monkeypatch):
    """Empty jwt_secret on direct construct: validator allows; from_env populates later."""
    with reload_config_with(monkeypatch, COCKPIT_TEST_MODE="1", COCKPIT_JWT_SECRET="") as cfg_mod:
        cfg_mod.get_settings.cache_clear()
        s = cfg_mod.Settings()
        assert s.jwt_secret == ""


def test_short_secret_rejected(monkeypatch):
    """Test mode off, short hex secret → validator raises."""
    # Use a tempdir for COCKPIT_TEST_MODE=0 path — config.py would otherwise
    # reject because /opt/shift-agent doesn't exist; we just want the validator.
    with reload_config_with(monkeypatch, COCKPIT_TEST_MODE="0", COCKPIT_JWT_SECRET="abc") as cfg_mod:
        with pytest.raises(ValueError, match="64\\+ hex chars"):
            cfg_mod.Settings()


def test_valid_hex_secret_accepted(monkeypatch):
    with reload_config_with(monkeypatch, COCKPIT_TEST_MODE="0", COCKPIT_JWT_SECRET="0" * 64) as cfg_mod:
        s = cfg_mod.Settings()
        assert s.jwt_secret == "0" * 64


def test_base64_secret_rejected(monkeypatch):
    """Base64 chars (=, /, +) are not in [0-9a-fA-F] — validator rejects."""
    bad = "QmFzZTY0RW5jb2RlZFN0cmluZ09mU3VmZmljaWVudExlbmd0aA=="
    with reload_config_with(monkeypatch, COCKPIT_TEST_MODE="0", COCKPIT_JWT_SECRET=bad) as cfg_mod:
        with pytest.raises(ValueError, match="hex"):
            cfg_mod.Settings()
