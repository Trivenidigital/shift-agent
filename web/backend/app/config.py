"""Backend settings — env-driven, single source of truth.

Loaded from /opt/shift-agent/.env via systemd EnvironmentFile.

Test/CI mode: setting COCKPIT_TEST_MODE=1 in the env relaxes strict-validation
and switches default paths to a tempdir (so dump-openapi.py + pytest can run
without /opt/shift-agent existing). Production NEVER sets this.
"""
from __future__ import annotations

import os
import re
import secrets
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


_TEST_MODE = os.environ.get("COCKPIT_TEST_MODE", "").lower() in ("1", "true", "yes")
_TEST_BASE: Path | None = None

# Security guard (Reviewer 2 R2 #1): refuse to run in test-mode if the
# production filesystem layout exists. Prevents an operator who accidentally
# exports COCKPIT_TEST_MODE=1 in the prod shell from silently bypassing the
# JWT-secret hex validator (which would let a base64 secret be accepted).
if _TEST_MODE and Path("/opt/shift-agent").exists():
    import sys as _sys
    _allowed_test = "pytest" in _sys.modules or os.environ.get("PYTEST_CURRENT_TEST")
    if not _allowed_test:
        raise RuntimeError(
            "COCKPIT_TEST_MODE=1 with /opt/shift-agent present is forbidden outside pytest. "
            "This bypass would weaken JWT-secret validation in production."
        )


def _test_base() -> Path:
    """Lazy tempdir for COCKPIT_TEST_MODE=1 paths."""
    global _TEST_BASE
    if _TEST_BASE is None:
        _TEST_BASE = Path(tempfile.mkdtemp(prefix="cockpit-test-"))
        (_TEST_BASE / "state").mkdir(exist_ok=True)
        (_TEST_BASE / "logs").mkdir(exist_ok=True)
    return _TEST_BASE


def _path(prod_path: str, test_subpath: str = "") -> Path:
    if _TEST_MODE:
        sub = test_subpath or prod_path.lstrip("/").replace("/opt/shift-agent/", "")
        return _test_base() / sub
    return Path(prod_path)


class Settings(BaseModel):
    """Runtime settings for the cockpit backend. Values come from env."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Filesystem layout (env-overrideable for test mode)
    state_dir: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state", "state"))
    config_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/config.yaml", "config.yaml"))
    roster_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/roster.json", "roster.json"))
    pending_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/pending.json", "state/pending.json"))
    decisions_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/logs/decisions.log", "logs/decisions.log"))
    disabled_flag: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/disabled.flag", "state/disabled.flag"))
    send_counter_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/send-counter.json", "state/send-counter.json"))
    pair_runtime_dir: Path = Field(default_factory=lambda: _path("/run/shift-agent", "run"))
    cockpit_audit_log: Path = Field(default_factory=lambda: _path("/opt/shift-agent/logs/cockpit-audit.log", "logs/cockpit-audit.log"))
    otp_state_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/cockpit-otp.json", "state/cockpit-otp.json"))
    cockpit_disclosures_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/cockpit-disclosures.json", "state/cockpit-disclosures.json"))
    cockpit_session_lockout_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/cockpit-otp-failures.json", "state/cockpit-otp-failures.json"))
    cockpit_totp_pending_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/cockpit-totp-pending.json", "state/cockpit-totp-pending.json"))
    cockpit_totp_secret_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/cockpit-totp-secret.json", "state/cockpit-totp-secret.json"))
    cockpit_totp_failures_path: Path = Field(default_factory=lambda: _path("/opt/shift-agent/state/cockpit-totp-failures.json", "state/cockpit-totp-failures.json"))

    # Hermes / WhatsApp
    hermes_session_dir: Path = Path("/root/.hermes/whatsapp/session")
    hermes_creds_json: Path = Path("/root/.hermes/whatsapp/session/creds.json")
    bridge_node_bin: Path = Path("/root/.hermes/node/bin/node")
    bridge_js: Path = Path("/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js")
    bridge_health_url: str = "http://127.0.0.1:3000/health"

    # Auth
    jwt_secret: str = Field(default_factory=lambda: os.environ.get("COCKPIT_JWT_SECRET", ""))
    jwt_algo: str = "HS256"
    jwt_ttl_hours: int = 24
    cookie_name: str = "hjwt"
    cookie_secure: bool = True
    otp_ttl_seconds: int = 300
    otp_max_verify_attempts: int = 5
    otp_request_per_ip: tuple[int, int] = (3, 900)
    otp_request_per_owner: tuple[int, int] = (5, 3600)
    otp_verify_min_wall_seconds: float = 0.2
    totp_max_verify_attempts: int = 5
    totp_window: int = 1  # accept ±1 step (~30 sec) clock drift
    pushover_app_token: str = Field(default_factory=lambda: os.environ.get("PUSHOVER_APP_TOKEN", ""))
    pushover_user_key: str = Field(default_factory=lambda: os.environ.get("PUSHOVER_USER_KEY", ""))

    # Sensitive config fields requiring fresh OTP for PATCH
    sensitive_config_fields: frozenset[str] = frozenset(
        {
            "owner.phone",
            "alerting.pushover_user_key",
            "alerting.pushover_app_token",
            "backup.gpg_recipient_email",
            "backup.gpg_fingerprint",
            "limits.max_outbound_per_day",
            "limits.max_outbound_per_minute",
        }
    )

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> "Settings":
        """Reject invalid JWT secrets at startup, not at signing time.

        Empty secret is allowed during model construction — `from_env()`
        populates it from the on-disk secret file or auto-generates.
        Once populated, must be 64+ hex chars (32+ bytes encoded as hex).
        Rejects base64 / mixed-encoding / short secrets.
        """
        if self.jwt_secret and not _TEST_MODE:
            if not re.match(r"^[0-9a-fA-F]{64,}$", self.jwt_secret):
                raise ValueError(
                    "COCKPIT_JWT_SECRET must be 64+ hex chars (32+ bytes). "
                    "Generate with: python3 -c 'import secrets; print(secrets.token_hex(32))'"
                )
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        if not s.jwt_secret:
            # Persistent secret on first run; preferred path (rotation script
            # writes here, not to .env — smaller blast radius).
            secret_path = s.state_dir / ".cockpit-jwt-secret"
            if secret_path.exists():
                s.jwt_secret = secret_path.read_text().strip()
            else:
                s.jwt_secret = secrets.token_hex(32)
                secret_path.parent.mkdir(parents=True, exist_ok=True)
                secret_path.write_text(s.jwt_secret)
                secret_path.chmod(0o600)
        return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
