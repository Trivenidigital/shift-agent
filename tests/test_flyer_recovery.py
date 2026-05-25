from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "platform"))

from agents.flyer import recovery  # noqa: E402


NOW = datetime(2026, 5, 23, 16, 0, tzinfo=timezone.utc)


def _row(detail: str, *, reason: str = "flyer_primary_failed", chat_id: str = "17329837841@s.whatsapp.net") -> dict:
    return {
        "type": "cf_router_intercepted",
        "ts": NOW.isoformat(),
        "reason": reason,
        "chat_id": chat_id,
        "subprocess_rc": 2,
        "detail": detail,
    }


def _incident(status: str = "open", ack_status: str = "none") -> dict:
    signal = recovery.classify_decision(
        _row("project_id=F0065; concept_generation_failed: exit=2 provider down"),
        {},
    )
    assert signal is not None
    incident = recovery.incident_from_signal(signal, NOW)
    incident["status"] = status
    incident["ack"]["status"] = ack_status
    return incident


def test_classifies_concept_generation_failure_with_stable_fingerprint():
    signal_a = recovery.classify_decision(
        _row("project_id=F0065; concept_generation_failed: exit=2 provider down at 16:00"),
        {},
    )
    signal_b = recovery.classify_decision(
        _row("project_id=F0065; concept_generation_failed: exit=2 provider down at 16:01"),
        {},
    )

    assert signal_a is not None
    assert signal_a.failure_class == "concept_generation_failed"
    assert signal_a.project_id == "F0065"
    assert recovery.fingerprint_signal(signal_a) == recovery.fingerprint_signal(signal_b)


def test_successful_router_rows_with_failure_words_are_not_incidents():
    row = _row(
        "project_id=F0095; source_edit_preflight_failed=source edit provider configured for manual review; reason_code=source_edit_provider_unavailable",
        reason="flyer_reference_exact_edit_queued",
    )
    row["subprocess_rc"] = 0

    assert recovery.classify_decision(row, {}) is None


def test_failing_source_edit_rows_still_classify_provider_unavailable():
    row = _row(
        "project_id=F0095; source_edit_preflight_failed=source edit provider configured for manual review; reason_code=source_edit_provider_unavailable",
        reason="flyer_reference_exact_edit_failed",
    )
    row["subprocess_rc"] = 2

    signal = recovery.classify_decision(row, {})

    assert signal is not None
    assert signal.failure_class == "provider_unavailable"
    assert signal.project_id == "F0095"


def test_empty_ack_error_does_not_create_bridge_incident():
    row = _row(
        "project_id=F0095; ack_message_id=3EB0F47BDBEB38EBCEC587; ack_error=",
        reason="flyer_reference_exact_edit_queued",
    )

    assert recovery.classify_decision(row, {}) is None


def test_customer_copy_lint_blocks_internal_terms_and_project_ids():
    bad = recovery.lint_recovery_copy("Project F0065 is in the manual queue", "manual_queue_stale", False)

    assert bad.ok is False
    assert "internal_term:manual queue" in bad.reasons
    assert "project_id" in bad.reasons


def test_terminal_ack_states_do_not_become_eligible_after_cooldown():
    for ack_status in ["sent", "failed", "uncertain"]:
        incident = _incident(ack_status=ack_status)
        decision = recovery.ack_send_decision(
            incident,
            flyer_enabled=True,
            mode="customer_ack",
            now=NOW + timedelta(hours=3),
            ack_cooldown=timedelta(minutes=60),
        )
        assert decision.allowed is False
        assert decision.reason == f"terminal_ack:{ack_status}"


def test_stale_reserved_ack_becomes_uncertain_and_stays_terminal():
    incident = _incident(ack_status="reserved")
    incident["ack"]["reserved_at"] = (NOW - timedelta(minutes=30)).isoformat()

    changed = recovery.finalize_stale_reservations(
        {"schema_version": 1, "incidents": [incident]},
        now=NOW,
        stale_after=timedelta(minutes=10),
    )

    assert changed is True
    assert incident["ack"]["status"] == "uncertain"
    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=True,
        mode="customer_ack",
        now=NOW + timedelta(hours=3),
        ack_cooldown=timedelta(minutes=60),
    )
    assert decision.allowed is False
    assert decision.reason == "terminal_ack:uncertain"


def test_incident_needs_strong_customer_origin_evidence_for_ack():
    incident = _incident()
    incident["evidence_quality"] = "weak"

    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=True,
        mode="customer_ack",
        now=NOW,
        ack_cooldown=timedelta(minutes=60),
    )

    assert decision.allowed is False
    assert decision.reason == "missing_strong_customer_origin_evidence"


def test_flyer_disabled_suppresses_all_customer_acks():
    incident = _incident()

    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=False,
        mode="customer_ack",
        now=NOW,
        ack_cooldown=timedelta(minutes=60),
    )

    assert decision.allowed is False
    assert decision.reason == "flyer_disabled"


def test_closed_incident_is_terminal_no_send():
    incident = _incident(status="resolved", ack_status="none")

    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=True,
        mode="customer_ack",
        now=NOW,
        ack_cooldown=timedelta(minutes=60),
    )

    assert decision.allowed is False
    assert decision.reason == "terminal_incident_status:resolved"


def test_missing_chat_id_is_terminal_no_send():
    incident = _incident()
    incident["chat_id"] = ""

    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=True,
        mode="customer_ack",
        now=NOW,
        ack_cooldown=timedelta(minutes=60),
    )

    assert decision.allowed is False
    assert decision.reason == "missing_chat_id"


def test_stale_incident_is_terminal_no_send():
    incident = _incident()
    incident["last_seen"] = (NOW - timedelta(hours=2)).isoformat()

    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=True,
        mode="customer_ack",
        now=NOW,
        ack_cooldown=timedelta(minutes=30),
    )

    assert decision.allowed is False
    assert decision.reason == "stale_incident"


def test_invalid_last_seen_is_terminal_no_send():
    incident = _incident()
    incident["last_seen"] = "not-a-timestamp"

    decision = recovery.ack_send_decision(
        incident,
        flyer_enabled=True,
        mode="customer_ack",
        now=NOW,
        ack_cooldown=timedelta(minutes=30),
    )

    assert decision.allowed is False
    assert decision.reason == "invalid_last_seen"


def test_repeated_timer_cycles_do_not_resend_after_first_terminal_state():
    incident = _incident(ack_status="sent")
    state = {"schema_version": 1, "incidents": [incident]}
    send_count = 0

    for minutes in [0, 5, 60, 180]:
        recovery.finalize_stale_reservations(
            state,
            now=NOW + timedelta(minutes=minutes),
            stale_after=timedelta(minutes=10),
        )
        decision = recovery.ack_send_decision(
            incident,
            flyer_enabled=True,
            mode="customer_ack",
            now=NOW + timedelta(minutes=minutes),
            ack_cooldown=timedelta(minutes=60),
        )
        if decision.allowed:
            send_count += 1

    assert send_count == 0


def test_write_repair_bundle_redacts_customer_identifiers(tmp_path):
    incident = _incident()
    incident["chat_id_hash"] = recovery.sha256_text("17329837841@s.whatsapp.net")
    incident["sender_phone_hash"] = recovery.sha256_text("+17329837841")
    bundle_path = recovery.write_repair_bundle(
        incident,
        tmp_path,
        audit_rows=[_row("project_id=F0065; concept_generation_failed: exit=2")],
        project_excerpt={"project_id": "F0065", "customer_phone": "+17329837841"},
    )

    doc = json.loads(bundle_path.read_text(encoding="utf-8"))
    serialized = json.dumps(doc)
    assert "+17329837841" not in serialized
    assert "17329837841@s.whatsapp.net" not in serialized
    assert doc["incident_id"] == incident["incident_id"]
    assert doc["sanitized_context"]["chat_id_hash"].startswith("sha256:")
