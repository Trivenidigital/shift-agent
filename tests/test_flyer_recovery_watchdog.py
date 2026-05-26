from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-recovery-watchdog"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "platform"))

from agents.flyer import recovery  # noqa: E402


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


def test_watchdog_bundle_queues_stale_manual_review_from_project_state(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    bundle_dir = tmp_path / "bundles"
    worker_queue_dir = tmp_path / "queue"
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
    mode: bundle
    enable_timer: true
    scan_window_minutes: 240
    manual_queue_stale_minutes: 30
""".strip(),
        encoding="utf-8",
    )
    queued_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    log.write_text("", encoding="utf-8")
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "F0102",
                        "status": "manual_edit_required",
                        "customer_phone": "+17329837841",
                        "raw_request": "Create Special Biryani flyer",
                        "manual_review": {
                            "status": "queued",
                            "reason_code": "visual_qa_failed",
                            "reason": "visual_qa_failed",
                            "detail": "missing required visible fact: item:0:name",
                            "queued_at": queued_at,
                        },
                        "qa_reports": [
                            {
                                "status": "failed",
                                "blockers": ["missing required visible fact: item:0:name"],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
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
            "--bundle-dir", str(bundle_dir),
            "--worker-queue-dir", str(worker_queue_dir),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "opened=1" in result.stdout
    assert "queued=1" in result.stdout
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    incident = state["incidents"][0]
    assert incident["project_id"] == "F0102"
    assert incident["failure_class"] == "concept_generation_failed"
    assert incident["evidence_quality"] == "weak"
    assert incident["codex"]["status"] == "queued"
    assert (bundle_dir / f"{incident['incident_id']}.json").exists()
    assert (worker_queue_dir / f"{incident['incident_id']}.json").exists()
    bundle = json.loads((bundle_dir / f"{incident['incident_id']}.json").read_text(encoding="utf-8"))
    assert bundle["project_excerpt"]["project_id"] == "F0102"
    assert bundle["project_excerpt"]["manual_review"]["reason_code"] == "visual_qa_failed"


def test_watchdog_write_repair_bundle_is_explicit_operator_action(tmp_path):
    recovery_state = tmp_path / "recovery.json"
    bundle_dir = tmp_path / "bundles"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    row = {
        "type": "cf_router_intercepted",
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": "flyer_primary_failed",
        "chat_id": "17329837841@s.whatsapp.net",
        "message_id": "wamid.current",
        "subprocess_rc": 2,
        "detail": "project_id=F0065; concept_generation_failed: exit=2 provider down",
    }
    signal = recovery.classify_decision(row, {})
    assert signal is not None
    incident = recovery.incident_from_signal(signal, datetime(2026, 5, 23, 16, 0, tzinfo=timezone.utc))
    incident["incident_id"] = "FRI20260523-0001"
    recovery_state.write_text(json.dumps({"schema_version": 1, "incidents": [incident]}), encoding="utf-8")
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "F0065",
                        "status": "manual_edit_required",
                        "customer_phone": "+17329837841",
                        "raw_request": "Create flyer with contact +17329837841",
                        "manual_review": {
                            "status": "queued",
                            "reason_code": "source_edit_generation_failed",
                            "detail": "exit=-15",
                        },
                        "assets": [
                            {
                                "asset_id": "A0001",
                                "kind": "reference_image",
                                "path": "/opt/shift-agent/state/flyer/assets/F0065-reference.jpg",
                                "delivery_status": "pending",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--recovery-state-path", str(recovery_state),
            "--bundle-dir", str(bundle_dir),
            "--log-path", str(log),
            "--project-state-path", str(projects),
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
    doc = json.loads((bundle_dir / "FRI20260523-0001.json").read_text(encoding="utf-8"))
    serialized = json.dumps(doc)
    assert "+17329837841" not in serialized
    assert "17329837841@s.whatsapp.net" not in serialized
    assert doc["audit_excerpt"][0]["chat_id_hash"].startswith("sha256:")
    assert "chat_id" not in doc["audit_excerpt"][0]
    assert doc["project_excerpt"]["project_id"] == "F0065"
    assert doc["project_excerpt"]["manual_review"]["status"] == "queued"


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
    mode: bundle
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


def test_watchdog_resolves_open_incident_after_successful_customer_outcome_repair(tmp_path):
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
    mode: observe
    enable_timer: true
    scan_window_minutes: 240
""".strip(),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    repair_ts = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    chat_hash = recovery.sha256_text("74290284261595@lid")
    other_chat_hash = recovery.sha256_text("17329837841@s.whatsapp.net")
    log.write_text(
        json.dumps(
            {
                "type": "flyer_recovery_outcome_repaired",
                "ts": repair_ts,
                "repair_type": "reference_scope_false_positive",
                "status": "sent",
                "chat_id_hash": chat_hash,
                "customer_id": "CUST0004",
                "business_name": "Chloe hair studio",
                "scope_reason": "no_spend_exact_source_edit_known_account",
                "outbound_message_id": "mid-corrective",
                "error": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")
    old_seen = (now - timedelta(minutes=30)).isoformat()
    new_seen = (now + timedelta(minutes=1)).isoformat()
    recovery_state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incidents": [
                    {
                        "incident_id": "FRI20260525-OLDCHAT",
                        "status": "open",
                        "failure_class": "bridge_send_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-old",
                        "ack_dedupe_key": "ack-old",
                        "project_id": "",
                        "chat_id": "74290284261595@lid",
                        "chat_id_hash": chat_hash,
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.old",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": old_seen,
                        "last_seen": old_seen,
                        "ack": {"status": "none"},
                        "codex": {"status": "completed", "bundle_path": "/tmp/bundle.json"},
                    },
                    {
                        "incident_id": "FRI20260525-NEWCHAT",
                        "status": "open",
                        "failure_class": "concept_generation_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-new",
                        "ack_dedupe_key": "ack-new",
                        "project_id": "F0100",
                        "chat_id": "74290284261595@lid",
                        "chat_id_hash": chat_hash,
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.new",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": new_seen,
                        "last_seen": new_seen,
                        "ack": {"status": "none"},
                        "codex": {"status": "none", "bundle_path": ""},
                    },
                    {
                        "incident_id": "FRI20260525-OTHER",
                        "status": "open",
                        "failure_class": "bridge_send_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-other",
                        "ack_dedupe_key": "ack-other",
                        "project_id": "F0098",
                        "chat_id": "17329837841@s.whatsapp.net",
                        "chat_id_hash": other_chat_hash,
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.other",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": old_seen,
                        "last_seen": old_seen,
                        "ack": {"status": "none"},
                        "codex": {"status": "completed", "bundle_path": "/tmp/bundle.json"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path",
            str(config),
            "--log-path",
            str(log),
            "--project-state-path",
            str(projects),
            "--customer-state-path",
            str(customers),
            "--recovery-state-path",
            str(recovery_state),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "resolved=1" in result.stdout
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    by_id = {item["incident_id"]: item for item in state["incidents"]}
    assert by_id["FRI20260525-OLDCHAT"]["status"] == "resolved"
    assert by_id["FRI20260525-OLDCHAT"]["resolution"] == "outcome_repaired"
    assert by_id["FRI20260525-NEWCHAT"]["status"] == "open"
    assert by_id["FRI20260525-OTHER"]["status"] == "operator_action_required"
    text = log.read_text(encoding="utf-8")
    assert '"type":"flyer_recovery_resolved"' in text
    assert '"incident_id":"FRI20260525-OLDCHAT"' in text


def test_watchdog_resolves_delivered_project_before_live_repair_queue(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    bundle_dir = tmp_path / "bundles"
    worker_queue_dir = tmp_path / "queue"
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
    mode: bundle
    enable_timer: true
    scan_window_minutes: 240
""".strip(),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    failure_ts = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    delivered_ts = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    failure_row = {
        "type": "cf_router_intercepted",
        "ts": failure_ts,
        "reason": "flyer_primary_failed",
        "chat_id": "74290284261595@lid",
        "message_id": "wamid.old",
        "subprocess_rc": 2,
        "detail": "project_id=F0097; concept_generation_failed: exit=2 provider down",
    }
    delivered_row = {
        "type": "flyer_assets_delivered",
        "ts": delivered_ts,
        "project_id": "F0097",
        "customer_phone": "+19803826497",
        "asset_ids": ["A0001"],
        "outbound_message_ids": ["wamid.delivered"],
    }
    log.write_text(json.dumps(failure_row) + "\n" + json.dumps(delivered_row) + "\n", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")
    signal = recovery.classify_decision(failure_row, {})
    assert signal is not None
    incident = recovery.incident_from_signal(signal, now)
    incident["incident_id"] = "FRI20260525-DELIVERED"
    recovery_state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incidents": [incident],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path",
            str(config),
            "--log-path",
            str(log),
            "--project-state-path",
            str(projects),
            "--customer-state-path",
            str(customers),
            "--recovery-state-path",
            str(recovery_state),
            "--bundle-dir",
            str(bundle_dir),
            "--worker-queue-dir",
            str(worker_queue_dir),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "resolved=1" in result.stdout
    assert "queued=0" in result.stdout
    assert not bundle_dir.exists() or not any(bundle_dir.iterdir())
    assert not worker_queue_dir.exists() or not any(worker_queue_dir.iterdir())
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    incident = state["incidents"][0]
    assert incident["status"] == "resolved"
    assert incident["resolution"] == "customer_visible_success"


def test_watchdog_resolves_same_run_from_project_asset_delivery_state(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    bundle_dir = tmp_path / "bundles"
    worker_queue_dir = tmp_path / "queue"
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
    mode: bundle
    enable_timer: true
    scan_window_minutes: 240
""".strip(),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    failure_ts = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    delivered_ts = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    failure_row = {
        "type": "cf_router_intercepted",
        "ts": failure_ts,
        "reason": "flyer_primary_project_created",
        "chat_id": "201975216009469@lid",
        "message_id": "wamid.old",
        "subprocess_rc": 0,
        "detail": (
            "customer_id=CUST0001; project_id=F0102; ack_error="
            "concept_generation_failed: exit=2 visual_qa_failed"
        ),
    }
    log.write_text(json.dumps(failure_row) + "\n", encoding="utf-8")
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "project_id": "F0102",
                        "status": "awaiting_final_approval",
                        "created_at": failure_ts,
                        "updated_at": delivered_ts,
                        "assets": [
                            {
                                "asset_id": "A0004",
                                "kind": "whatsapp_image",
                                "delivery_status": "sent",
                                "outbound_message_id": "3EB09BE55CA6D73AA47971",
                                "delivered_at": delivered_ts,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path",
            str(config),
            "--log-path",
            str(log),
            "--project-state-path",
            str(projects),
            "--customer-state-path",
            str(customers),
            "--recovery-state-path",
            str(recovery_state),
            "--bundle-dir",
            str(bundle_dir),
            "--worker-queue-dir",
            str(worker_queue_dir),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "opened=1" in result.stdout
    assert "resolved=1" in result.stdout
    assert "queued=0" in result.stdout
    assert not bundle_dir.exists() or not any(bundle_dir.iterdir())
    assert not worker_queue_dir.exists() or not any(worker_queue_dir.iterdir())
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    incident = state["incidents"][0]
    assert incident["status"] == "resolved"
    assert incident["resolution"] == "customer_visible_success"
    assert incident["resolution_detail"] == "flyer_assets_delivered"


def test_watchdog_escalates_completed_repair_without_visible_success_before_queue(tmp_path):
    config = tmp_path / "config.yaml"
    log = tmp_path / "decisions.log"
    projects = tmp_path / "projects.json"
    customers = tmp_path / "customers.json"
    recovery_state = tmp_path / "recovery.json"
    bundle_dir = tmp_path / "bundles"
    worker_queue_dir = tmp_path / "queue"
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
    mode: bundle
    enable_timer: true
    scan_window_minutes: 240
    operator_escalation_stale_minutes: 30
""".strip(),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    old_seen = (now - timedelta(hours=2)).isoformat()
    completed_at = (now - timedelta(hours=1)).isoformat()
    recovery_state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incidents": [
                    {
                        "incident_id": "FRI20260525-NOEVIDENCE",
                        "status": "open",
                        "failure_class": "concept_generation_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-no-evidence",
                        "ack_dedupe_key": "ack-no-evidence",
                        "project_id": "F0097",
                        "chat_id": "74290284261595@lid",
                        "chat_id_hash": recovery.sha256_text("74290284261595@lid"),
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.old",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": old_seen,
                        "last_seen": old_seen,
                        "ack": {"status": "none"},
                        "codex": {
                            "status": "completed",
                            "completed_at": completed_at,
                            "bundle_path": "/tmp/bundle.json",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    log.write_text("", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path",
            str(config),
            "--log-path",
            str(log),
            "--project-state-path",
            str(projects),
            "--customer-state-path",
            str(customers),
            "--recovery-state-path",
            str(recovery_state),
            "--bundle-dir",
            str(bundle_dir),
            "--worker-queue-dir",
            str(worker_queue_dir),
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "operator_action_required=1" in result.stdout
    assert "queued=0" in result.stdout
    assert not bundle_dir.exists() or not any(bundle_dir.iterdir())
    assert not worker_queue_dir.exists() or not any(worker_queue_dir.iterdir())
    state = json.loads(recovery_state.read_text(encoding="utf-8"))
    incident = state["incidents"][0]
    assert incident["status"] == "operator_action_required"
    assert incident["operator_action"]["reason"] == "worker_completed_no_customer_visible_success"
    text = log.read_text(encoding="utf-8")
    assert '"type":"flyer_recovery_operator_action_required"' in text
    assert '"incident_id":"FRI20260525-NOEVIDENCE"' in text


def test_watchdog_dry_run_reports_operator_escalation_without_mutation(tmp_path):
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
    mode: bundle
    enable_timer: true
    scan_window_minutes: 240
    operator_escalation_stale_minutes: 30
""".strip(),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    old_seen = (now - timedelta(hours=2)).isoformat()
    completed_at = (now - timedelta(hours=1)).isoformat()
    recovery_state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "incidents": [
                    {
                        "incident_id": "FRI20260525-DRYRUN",
                        "status": "open",
                        "failure_class": "concept_generation_failed",
                        "severity": "warning",
                        "source_fingerprint": "fp-dryrun",
                        "ack_dedupe_key": "ack-dryrun",
                        "project_id": "F0098",
                        "chat_id": "74290284261595@lid",
                        "chat_id_hash": recovery.sha256_text("74290284261595@lid"),
                        "sender_phone_hash": "",
                        "root_message_id": "wamid.old",
                        "provider_message_id_hash": "sha256:msg",
                        "evidence_quality": "strong",
                        "first_seen": old_seen,
                        "last_seen": old_seen,
                        "ack": {"status": "none"},
                        "codex": {
                            "status": "completed",
                            "completed_at": completed_at,
                            "bundle_path": "/tmp/bundle.json",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = recovery_state.read_text(encoding="utf-8")
    log.write_text("", encoding="utf-8")
    projects.write_text('{"projects":[]}', encoding="utf-8")
    customers.write_text('{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config-path",
            str(config),
            "--log-path",
            str(log),
            "--project-state-path",
            str(projects),
            "--customer-state-path",
            str(customers),
            "--recovery-state-path",
            str(recovery_state),
            "--dry-run",
            "--text",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "operator_action_required=1" in result.stdout
    assert "queued=0" in result.stdout
    assert recovery_state.read_text(encoding="utf-8") == before
    assert log.read_text(encoding="utf-8") == ""
