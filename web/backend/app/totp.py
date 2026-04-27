"""TOTP fallback authentication.

Re-framing per design v1.1 review: TOTP is a *fallback for Pushover-OTP delivery
failures* (Pushover down, owner travelling without cell signal, account
suspended). It is NOT a defense against disk-compromise — if an attacker reads
.cockpit-jwt-secret they also read the TOTP seed.

Verify-before-commit enrollment:
  1. POST /auth/totp/enroll-start   → writes pending_path with provisional flag
  2. POST /auth/totp/enroll-verify  → owner submits a valid TOTP code from their
                                      authenticator app; on success, atomic-rename
                                      pending → secret_path
  3. POST /auth/totp/disable        → wipes secret_path (requires fresh OTP)
  4. POST /auth/verify-totp         → reads secret_path ONLY (refuses pending)

Lockout: 5 wrong codes → invalidate the secret state for 15 min (separate from
the Pushover-OTP failure store; failures don't cross-contaminate).
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import pyotp
import qrcode
from fastapi import HTTPException, status

from .config import get_settings

settings = get_settings()


_ISSUER = "Shift Agent Cockpit"


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_0600(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.chmod(0o600)
    os.replace(tmp, path)  # atomic


# ─── Enrollment ────────────────────────────────────────────────────────


def is_enrolled() -> bool:
    """True iff a non-provisional TOTP secret is committed."""
    rec = _read_json(settings.cockpit_totp_secret_path)
    return rec is not None and not rec.get("provisional", True)


def enroll_start(owner_phone: str) -> dict[str, Any]:
    """Generate a fresh TOTP secret + QR.

    Refuses if an enrollment is already committed (must `disable` first).
    Stores under cockpit_totp_pending_path with provisional=True; this file
    is NEVER consumed by verify-totp.
    """
    if is_enrolled():
        raise HTTPException(409, "TOTP already enrolled — call /auth/totp/disable first")

    secret = pyotp.random_base32()  # 32 chars, ~160 bits entropy
    label = f"shift-agent-cockpit:{owner_phone}"
    uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=_ISSUER)

    _write_json_0600(
        settings.cockpit_totp_pending_path,
        {
            "provisional": True,
            "secret": secret,
            "issued_at": time.time(),
            "issued_to": owner_phone,
        },
    )

    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return {"otpauth_uri": uri, "qr_b64": qr_b64, "secret_for_manual_entry": secret}


def enroll_verify(code: str) -> bool:
    """Promote pending → committed iff `code` matches the pending secret.

    Returns True on success. On failure, increments enrollment-failure counter
    on the pending record (3 strikes → discard pending).
    """
    pending = _read_json(settings.cockpit_totp_pending_path)
    if pending is None or not pending.get("provisional"):
        raise HTTPException(404, "no pending TOTP enrollment")

    secret = pending["secret"]
    if not pyotp.TOTP(secret).verify(code, valid_window=settings.totp_window):
        attempts = pending.get("attempts", 0) + 1
        if attempts >= 3:
            settings.cockpit_totp_pending_path.unlink(missing_ok=True)
            raise HTTPException(429, "too many failed verifications; restart enrollment")
        pending["attempts"] = attempts
        _write_json_0600(settings.cockpit_totp_pending_path, pending)
        raise HTTPException(400, "code did not match — try again")

    # Atomic-promote: write committed record FIRST, then unlink pending.
    _write_json_0600(
        settings.cockpit_totp_secret_path,
        {
            "provisional": False,
            "secret": secret,
            "enrolled_at": time.time(),
            "issued_to": pending["issued_to"],
        },
    )
    settings.cockpit_totp_pending_path.unlink(missing_ok=True)
    return True


def disable() -> None:
    """Remove all TOTP state."""
    settings.cockpit_totp_secret_path.unlink(missing_ok=True)
    settings.cockpit_totp_pending_path.unlink(missing_ok=True)
    settings.cockpit_totp_failures_path.unlink(missing_ok=True)


# ─── Verification (login path) ─────────────────────────────────────────


def verify(code: str) -> str | None:
    """Verify a TOTP code; on success return the owner phone (caller mints JWT).

    Reads ONLY cockpit_totp_secret_path. The pending file is never authoritative.
    Lockout: 5 wrong codes → invalidate the secret store for 15 min, audit-logged.
    """
    rec = _read_json(settings.cockpit_totp_secret_path)
    if rec is None or rec.get("provisional"):
        raise HTTPException(412, "TOTP not enrolled — use Pushover OTP")

    fail = _read_json(settings.cockpit_totp_failures_path) or {"attempts": 0, "locked_until": 0}
    now = time.time()
    if fail["locked_until"] > now:
        raise HTTPException(429, f"locked out, retry after {int(fail['locked_until'] - now)}s")

    if not pyotp.TOTP(rec["secret"]).verify(code, valid_window=settings.totp_window):
        fail["attempts"] = fail.get("attempts", 0) + 1
        if fail["attempts"] >= settings.totp_max_verify_attempts:
            fail["locked_until"] = now + 900  # 15 min
            fail["attempts"] = 0
        _write_json_0600(settings.cockpit_totp_failures_path, fail)
        return None

    # Success — clear failures
    settings.cockpit_totp_failures_path.unlink(missing_ok=True)
    return rec["issued_to"]
