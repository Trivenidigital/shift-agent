"""JWT secret startup validator (BL-120 #6)."""
from __future__ import annotations

import pytest


def test_empty_secret_falls_back_to_from_env(monkeypatch):
    """Empty jwt_secret on direct construct: validator allows; from_env populates."""
    monkeypatch.setenv("COCKPIT_JWT_SECRET", "")
    from app import config as cfg_mod

    cfg_mod.get_settings.cache_clear()
    s = cfg_mod.Settings()
    # Empty is allowed; from_env() will populate from secret file
    assert s.jwt_secret == ""


def test_short_secret_rejected_when_not_test_mode(monkeypatch):
    """Disable test mode and confirm the validator fires."""
    monkeypatch.setenv("COCKPIT_TEST_MODE", "0")
    monkeypatch.setenv("COCKPIT_JWT_SECRET", "abc")
    # Reload module to pick up new TEST_MODE
    import importlib
    from app import config as cfg_mod

    importlib.reload(cfg_mod)
    with pytest.raises(ValueError, match="64\\+ hex chars"):
        cfg_mod.Settings()
    # Restore for downstream tests
    monkeypatch.setenv("COCKPIT_TEST_MODE", "1")
    importlib.reload(cfg_mod)


def test_valid_hex_secret_accepted(monkeypatch):
    monkeypatch.setenv("COCKPIT_TEST_MODE", "0")
    monkeypatch.setenv("COCKPIT_JWT_SECRET", "0" * 64)
    import importlib
    from app import config as cfg_mod

    importlib.reload(cfg_mod)
    s = cfg_mod.Settings()
    assert s.jwt_secret == "0" * 64
    monkeypatch.setenv("COCKPIT_TEST_MODE", "1")
    importlib.reload(cfg_mod)


def test_base64_secret_rejected(monkeypatch):
    """Base64 chars (=, /, +) are not in [0-9a-fA-F] — validator rejects."""
    monkeypatch.setenv("COCKPIT_TEST_MODE", "0")
    monkeypatch.setenv("COCKPIT_JWT_SECRET", "QmFzZTY0RW5jb2RlZFN0cmluZ09mU3VmZmljaWVudExlbmd0aA==")
    import importlib
    from app import config as cfg_mod

    importlib.reload(cfg_mod)
    with pytest.raises(ValueError, match="hex"):
        cfg_mod.Settings()
    monkeypatch.setenv("COCKPIT_TEST_MODE", "1")
    importlib.reload(cfg_mod)
