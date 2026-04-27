"""TOTP enroll/verify/disable (BL-133)."""
from __future__ import annotations

import pyotp
import pytest
from fastapi import HTTPException


def _patch_paths(monkeypatch, tmp_path):
    """Patch paths on the settings instance that `app.totp` actually uses.

    `app.totp` does `settings = get_settings()` at module import time, so
    patching the result of a fresh `get_settings()` call would create a
    *different* instance — `app.totp.settings` is still the old one.
    Solution: import `app.totp` first, then patch its module-level
    `settings` attributes directly. Same instance, observed by totp.
    """
    from app import totp as totp_mod

    s = totp_mod.settings
    monkeypatch.setattr(s, "cockpit_totp_pending_path", tmp_path / "pending.json")
    monkeypatch.setattr(s, "cockpit_totp_secret_path", tmp_path / "secret.json")
    monkeypatch.setattr(s, "cockpit_totp_failures_path", tmp_path / "failures.json")
    return s


def test_not_enrolled_initially(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    from app import totp

    assert not totp.is_enrolled()


def test_enroll_start_creates_pending(monkeypatch, tmp_path):
    s = _patch_paths(monkeypatch, tmp_path)
    from app import totp

    body = totp.enroll_start("+19045550100")
    assert "otpauth_uri" in body
    assert "qr_b64" in body
    assert "secret_for_manual_entry" in body
    assert s.cockpit_totp_pending_path.exists()
    assert not s.cockpit_totp_secret_path.exists()
    assert not totp.is_enrolled()  # provisional


def test_enroll_start_refuses_when_enrolled(monkeypatch, tmp_path):
    s = _patch_paths(monkeypatch, tmp_path)
    from app import totp

    totp.enroll_start("+19045550100")
    secret = (tmp_path / "pending.json").read_text()
    # Promote manually for this test
    import json
    rec = json.loads(secret)
    rec["provisional"] = False
    s.cockpit_totp_secret_path.write_text(json.dumps(rec))
    s.cockpit_totp_secret_path.chmod(0o600)
    s.cockpit_totp_pending_path.unlink()

    with pytest.raises(HTTPException) as exc:
        totp.enroll_start("+19045550100")
    assert exc.value.status_code == 409


def test_enroll_verify_promotes_pending(monkeypatch, tmp_path):
    s = _patch_paths(monkeypatch, tmp_path)
    from app import totp

    body = totp.enroll_start("+19045550100")
    secret = body["secret_for_manual_entry"]
    code = pyotp.TOTP(secret).now()
    assert totp.enroll_verify(code) is True
    assert s.cockpit_totp_secret_path.exists()
    assert not s.cockpit_totp_pending_path.exists()
    assert totp.is_enrolled()


def test_enroll_verify_wrong_code_increments(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    from app import totp

    totp.enroll_start("+19045550100")
    with pytest.raises(HTTPException) as exc:
        totp.enroll_verify("000000")
    assert exc.value.status_code == 400
    # Pending still exists
    assert not totp.is_enrolled()


def test_enroll_verify_3_strikes_discards_pending(monkeypatch, tmp_path):
    s = _patch_paths(monkeypatch, tmp_path)
    from app import totp

    totp.enroll_start("+19045550100")
    for _ in range(2):
        with pytest.raises(HTTPException):
            totp.enroll_verify("000000")
    # Third strike → 429 + pending discarded
    with pytest.raises(HTTPException) as exc:
        totp.enroll_verify("000000")
    assert exc.value.status_code == 429
    assert not s.cockpit_totp_pending_path.exists()


def test_verify_only_reads_committed_secret(monkeypatch, tmp_path):
    """Critical security check: verify() must NEVER consume the pending file."""
    _patch_paths(monkeypatch, tmp_path)
    from app import totp

    # Start enrollment but don't complete it
    body = totp.enroll_start("+19045550100")
    code = pyotp.TOTP(body["secret_for_manual_entry"]).now()

    # verify() must refuse — no committed secret yet
    with pytest.raises(HTTPException) as exc:
        totp.verify(code)
    assert exc.value.status_code == 412


def test_verify_accepts_valid_code_after_enrollment(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    from app import totp

    body = totp.enroll_start("+19045550100")
    secret = body["secret_for_manual_entry"]
    totp.enroll_verify(pyotp.TOTP(secret).now())

    owner = totp.verify(pyotp.TOTP(secret).now())
    assert owner == "+19045550100"


def test_verify_lockout_after_5_failures(monkeypatch, tmp_path):
    _patch_paths(monkeypatch, tmp_path)
    from app import totp

    body = totp.enroll_start("+19045550100")
    secret = body["secret_for_manual_entry"]
    totp.enroll_verify(pyotp.TOTP(secret).now())

    for _ in range(5):
        assert totp.verify("000000") is None  # failures recorded

    # Now locked out
    with pytest.raises(HTTPException) as exc:
        totp.verify(pyotp.TOTP(secret).now())  # even valid code refused
    assert exc.value.status_code == 429


def test_disable_wipes_all(monkeypatch, tmp_path):
    s = _patch_paths(monkeypatch, tmp_path)
    from app import totp

    body = totp.enroll_start("+19045550100")
    secret = body["secret_for_manual_entry"]
    totp.enroll_verify(pyotp.TOTP(secret).now())
    s.cockpit_totp_failures_path.write_text("{}")  # ensure exists

    totp.disable()
    assert not s.cockpit_totp_secret_path.exists()
    assert not s.cockpit_totp_pending_path.exists()
    assert not s.cockpit_totp_failures_path.exists()
    assert not totp.is_enrolled()
