"""Backend settings — env-driven, single source of truth.

Loaded from /opt/shift-agent/.env via systemd EnvironmentFile.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Runtime settings for the cockpit backend.

    Values come from environment. Defaults are conservative.
    """

    # Filesystem layout
    state_dir: Path = Path("/opt/shift-agent/state")
    config_path: Path = Path("/opt/shift-agent/config.yaml")
    roster_path: Path = Path("/opt/shift-agent/roster.json")
    pending_path: Path = Path("/opt/shift-agent/state/pending.json")
    decisions_path: Path = Path("/opt/shift-agent/logs/decisions.log")
    disabled_flag: Path = Path("/opt/shift-agent/state/disabled.flag")
    send_counter_path: Path = Path("/opt/shift-agent/state/send-counter.json")
    pair_runtime_dir: Path = Path("/run/shift-agent")  # mode 0700, root-created at deploy
    cockpit_audit_log: Path = Path("/opt/shift-agent/logs/cockpit-audit.log")
    otp_state_path: Path = Path("/opt/shift-agent/state/cockpit-otp.json")
    cockpit_disclosures_path: Path = Path("/opt/shift-agent/state/cockpit-disclosures.json")
    cockpit_session_lockout_path: Path = Path("/opt/shift-agent/state/cockpit-otp-failures.json")

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
    otp_request_per_ip: tuple[int, int] = (3, 900)  # 3 per 15 min
    otp_request_per_owner: tuple[int, int] = (5, 3600)  # 5 per hour
    otp_verify_min_wall_seconds: float = 0.2  # security: timing-equalize
    pushover_app_token: str = Field(default_factory=lambda: os.environ.get("PUSHOVER_APP_TOKEN", ""))
    pushover_user_key: str = Field(default_factory=lambda: os.environ.get("PUSHOVER_USER_KEY", ""))

    # Sensitive config fields requiring fresh OTP for PATCH
    sensitive_config_fields: frozenset[str] = frozenset({
        "owner.phone",
        "alerting.pushover_user_key",
        "alerting.pushover_app_token",
        "backup.gpg_recipient_email",
        "limits.max_outbound_per_day",
        "limits.max_outbound_per_minute",
    })

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        if not s.jwt_secret:
            # Generate persistent secret on first run, write to runtime dir
            secret_path = Path("/opt/shift-agent/state/.cockpit-jwt-secret")
            if secret_path.exists():
                s.jwt_secret = secret_path.read_text().strip()
            else:
                s.jwt_secret = secrets.token_hex(32)  # 256-bit
                secret_path.write_text(s.jwt_secret)
                secret_path.chmod(0o600)
        return s


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
