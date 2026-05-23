from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-recovery-watchdog"


def test_watchdog_off_exits_without_state_write(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    config.write_text(
        """
schema_version: 1
customer: {name: Triveni, location_id: loc_pineville_01, timezone: America/New_York}
owner: {name: Owner, phone: '+19045550000'}
limits: {}
alerting: {pushover_user_key: k, pushover_app_token: t}
backup: {gpg_recipient_email: owner@example.com}
flyer:
  enabled: false
  recovery:
    mode: off
    enable_timer: false
""".strip(),
        encoding="utf-8",
    )
    log.write_text("", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path", str(config),
            "--log-path", str(log),
            "--project-state-path", str(projects),
            "--customer-state-path", str(customers),
            "--recovery-state-path", str(recovery_state),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "mode=off" in result.stdout
    assert not recovery_state.exists()


def test_watchdog_observe_opens_incident_without_customer_send(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    config.write_text(
        """
schema_version: 1
customer: {name: Triveni, location_id: loc_pineville_01, timezone: America/New_York}
owner: {name: Owner, phone: '+19045550000'}
limits: {}
alerting: {pushover_user_key: k, pushover_app_token: t}
backup: {gpg_recipient_email: owner@example.com}
flyer:
  enabled: false
  recovery:
    mode: observe
    enable_timer: true
    scan_window_minutes: 240
    """.strip(),
        encoding="utf-8",
    )
    current_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    log.write_text(
        f'{{"type":"cf_router_intercepted","ts":"{current_ts}","reason":"flyer_primary_failed","chat_id":"17329837841@s.whatsapp.net","message_id":"wamid.current","subprocess_rc":2,"detail":"project_id=F0065; concept_generation_failed: exit=2 provider down"}}\n'
        f'{{"type":"cf_router_intercepted","ts":"{old_ts}","reason":"flyer_primary_failed","chat_id":"17329837841@s.whatsapp.net","message_id":"wamid.old","subprocess_rc":2,"detail":"project_id=F0001; concept_generation_failed: exit=2 old provider down"}}\n',
        encoding="utf-8",
    )
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path", str(config),
            "--log-path", str(log),
            "--project-state-path", str(projects),
            "--customer-state-path", str(customers),
            "--recovery-state-path", str(recovery_state),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "opened=1" in result.stdout
    text = recovery_state.read_text(encoding="utf-8")
    assert "concept_generation_failed" in text
    assert "F0065" in text
    assert "F0001" not in text
    assert "ack_sent" not in text


def test_watchdog_write_repair_bundle_is_explicit_operator_action(tmp_path):
    recovery_state = tmp_path / "recovery.json"
    bundle_dir = tmp_path / "bundles"
    recovery_state.write_text(
        '{"schema_version":1,"incidents":[{"incident_id":"FRI20260523-0001","status":"open","failure_class":"concept_generation_failed","severity":"warning","source_fingerprint":"fp","ack_dedupe_key":"ak","project_id":"F0065","chat_id_hash":"sha256:chat","sender_phone_hash":"sha256:phone","root_message_id":"","evidence_quality":"weak","first_seen":"2026-05-23T16:00:00+00:00","last_seen":"2026-05-23T16:00:00+00:00","ack":{"status":"none"},"codex":{"status":"none"}}]}',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--recovery-state-path", str(recovery_state),
            "--bundle-dir", str(bundle_dir),
            "--write-repair-bundle",
            "--incident-id", "FRI20260523-0001",
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "bundle_written=" in result.stdout
    assert (bundle_dir / "FRI20260523-0001.json").exists()
