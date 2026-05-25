from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
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
    current_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    rows = [
        {
            "type": "cf_router_intercepted",
            "ts": current_ts,
            "reason": "flyer_primary_failed",
            "chat_id": "17329837841@s.whatsapp.net",
            "message_id": "wamid.current",
            "subprocess_rc": 2,
            "detail": "project_id=F0065; concept_generation_failed: exit=2 provider down",
        },
        {
            "type": "cf_router_intercepted",
            "ts": old_ts,
            "reason": "flyer_primary_failed",
            "chat_id": "17329837841@s.whatsapp.net",
            "message_id": "wamid.old",
            "subprocess_rc": 2,
            "detail": "project_id=F0001; concept_generation_failed: exit=2 old provider down",
        },
    ]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
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


def test_watchdog_customer_ack_marks_stale_incident_suppressed(tmp_path):
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
  enabled: true
  recovery:
    mode: customer_ack
    enable_timer: true
    scan_window_minutes: 240
    ack_cooldown_minutes: 30
""".strip(),
        encoding="utf-8",
    )
    log.write_text("", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    recovery_state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incidents": [
                    {
                        "incident_id": "FRI20260525-STALE01",
                        "status": "open",
                        "failure_class": "concept_generation_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-stale",
                        "ack_dedupe_key": "ack-stale",
                        "project_id": "F1234",
                        "chat_id": "17329837841@s.whatsapp.net",
                        "chat_id_hash": "sha256:chat",
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.stale",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": old_ts,
                        "last_seen": old_ts,
                        "ack": {"status": "none"},
                        "codex": {"status": "none", "bundle_path": ""},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

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
    assert "ack_sent=0" in result.stdout
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    incident = state["incidents"][0]
    assert incident["ack"]["status"] == "suppressed"
    assert incident["ack"]["status_detail"] == "stale_incident"


def test_watchdog_customer_ack_marks_missing_chat_id_suppressed(tmp_path):
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
  enabled: true
  recovery:
    mode: customer_ack
    enable_timer: true
    scan_window_minutes: 240
    ack_cooldown_minutes: 60
""".strip(),
        encoding="utf-8",
    )
    log.write_text("", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")
    now_ts = datetime.now(timezone.utc).isoformat()
    recovery_state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incidents": [
                    {
                        "incident_id": "FRI20260525-CHAT00",
                        "status": "open",
                        "failure_class": "concept_generation_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-chat",
                        "ack_dedupe_key": "ack-chat",
                        "project_id": "F1235",
                        "chat_id": "",
                        "chat_id_hash": "sha256:chat",
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.chat",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": now_ts,
                        "last_seen": now_ts,
                        "ack": {"status": "none"},
                        "codex": {"status": "none", "bundle_path": ""},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

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
    assert "ack_sent=0" in result.stdout
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    incident = state["incidents"][0]
    assert incident["ack"]["status"] == "suppressed"
    assert incident["ack"]["status_detail"] == "missing_chat_id"


def test_watchdog_repairs_reference_scope_false_positive_customer_outcome(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    reference_scope = tmp_path / "reference_scope_pending.json"
    fake_actions = tmp_path / "fake_actions.py"
    sent_path = tmp_path / "sent.json"
    fake_actions.write_text(
        f'''
import json
from pathlib import Path

SENT_PATH = Path({str(sent_path)!r})

def trigger_check_flyer_reference_scope(*, customer, media_path, raw_request):
    assert customer["business_name"] == "Chloe hair studio"
    assert "Existing flyer" in raw_request
    return True, "scope_check_skipped_no_spend", {{
        "decision": "allow",
        "reason": "no_spend_exact_source_edit_known_account",
    }}

def send_flyer_text(chat_id, text):
    rows = json.loads(SENT_PATH.read_text(encoding="utf-8")) if SENT_PATH.exists() else []
    rows.append({{"chat_id": chat_id, "text": text}})
    SENT_PATH.write_text(json.dumps(rows), encoding="utf-8")
    return True, "mid-corrective", ""
'''.strip(),
        encoding="utf-8",
    )
    config.write_text(
        """
schema_version: 1
customer: {name: Triveni, location_id: loc_pineville_01, timezone: America/New_York}
owner: {name: Owner, phone: '+19045550000'}
limits: {}
alerting: {pushover_user_key: k, pushover_app_token: t}
backup: {gpg_recipient_email: owner@example.com}
flyer:
  enabled: true
  recovery:
    mode: observe
    enable_timer: true
    scan_window_minutes: 240
""".strip(),
        encoding="utf-8",
    )
    log.write_text("", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")
    reference_scope.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pending": [
                    {
                        "chat_id": "74290284261595@lid",
                        "sender_phone": "+19803826497",
                        "customer": {"customer_id": "CUST0004", "business_name": "Chloe hair studio"},
                        "media_path": "/tmp/chloe.jpg",
                        "raw_request": "Existing flyer add the chsnge to this flyer",
                        "original_intent": "exact_source_edit",
                        "status": "awaiting_choice",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path", str(config),
            "--log-path", str(log),
            "--project-state-path", str(projects),
            "--customer-state-path", str(customers),
            "--recovery-state-path", str(recovery_state),
            "--reference-scope-path", str(reference_scope),
            "--cf-actions-path", str(fake_actions),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "outcome_repairs=1" in result.stdout
    assert json.loads(reference_scope.read_text(encoding="utf-8"))["pending"] == []
    sent = json.loads(sent_path.read_text(encoding="utf-8"))
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "74290284261595@lid"
    assert "already" in sent[0]["text"].lower()
    assert "Chloe hair studio" in sent[0]["text"]
    assert '"type":"flyer_recovery_outcome_repaired"' in log.read_text(encoding="utf-8")

    second = subprocess.run(result.args, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert "outcome_repairs=0" in second.stdout
    assert len(json.loads(sent_path.read_text(encoding="utf-8"))) == 1