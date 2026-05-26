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


def test_router_success_with_nonblank_ack_error_is_recovery_incident():
    row = _row(
        "project_id=F0102; sender_role=employee; ack_message_id=mid; "
        "ack_error=concept_generation_failed: exit=2 {\"visual_qa_failed\": []}",
        reason="flyer_primary_project_created",
        chat_id="201975216009469@lid",
    )
    row["subprocess_rc"] = 0

    signal = recovery.classify_decision(row, {})

    assert signal is not None
    assert signal.failure_class == "concept_generation_failed"
    assert signal.project_id == "F0102"
    assert signal.evidence_quality == "strong"


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


def test_edit_generation_failure_takes_precedence_over_trailing_ack_connect_failure():
    row = _row(
        "project_id=F0097; sender_role=unknown; ack_message_id=3EB0B09A33D1BF7008E7CC; "
        "ack_error=edit_generation_failed: exit=-15 ; access_held_for_manual_review=true; "
        "ack_error=connect_failed: URLError: [Errno 111] Connection refused",
        reason="flyer_reference_exact_edit_queued",
        chat_id="74290284261595@lid",
    )
    row["subprocess_rc"] = 3

    signal = recovery.classify_decision(row, {})

    assert signal is not None
    assert signal.failure_class == "concept_generation_failed"
    assert signal.project_id == "F0097"


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


def test_customer_visible_asset_delivery_resolves_older_project_incident_only():
    old = _incident()
    old["incident_id"] = "FRI20260525-OLD"
    old["project_id"] = "F0097"
    old["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    old["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    newer = _incident()
    newer["incident_id"] = "FRI20260525-NEW"
    newer["project_id"] = "F0097"
    newer["first_seen"] = (NOW + timedelta(minutes=1)).isoformat()
    newer["last_seen"] = (NOW + timedelta(minutes=1)).isoformat()
    refired = _incident()
    refired["incident_id"] = "FRI20260525-REFIRED"
    refired["project_id"] = "F0097"
    refired["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    refired["last_seen"] = (NOW + timedelta(minutes=1)).isoformat()
    other = _incident()
    other["incident_id"] = "FRI20260525-OTHER"
    other["project_id"] = "F0098"
    other["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    other["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    unknown_ts = _incident()
    unknown_ts["incident_id"] = "FRI20260525-UNKNOWN"
    unknown_ts["project_id"] = "F0097"
    unknown_ts["first_seen"] = "not-a-timestamp"
    unknown_ts["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    state = {"schema_version": 1, "incidents": [old, newer, refired, other, unknown_ts]}

    resolved = recovery.resolve_incidents_from_customer_visible_repairs(
        state,
        [
            {
                "type": "flyer_assets_delivered",
                "ts": NOW.isoformat(),
                "project_id": "F0097",
                "asset_ids": ["A0001"],
                "outbound_message_ids": ["mid-preview"],
            }
        ],
        NOW,
    )

    assert [item["incident_id"] for item in resolved] == ["FRI20260525-OLD"]
    assert old["status"] == "resolved"
    assert old["resolution"] == "customer_visible_success"
    assert old["resolution_detail"] == "flyer_assets_delivered"
    assert newer["status"] == "open"
    assert refired["status"] == "open"
    assert other["status"] == "open"
    assert unknown_ts["status"] == "open"


def test_outcome_repair_resolves_only_matching_chat_level_bridge_incident():
    chat_hash = recovery.sha256_text("74290284261595@lid")
    old_chat = _incident()
    old_chat["incident_id"] = "FRI20260525-CHAT"
    old_chat["project_id"] = ""
    old_chat["chat_id_hash"] = chat_hash
    old_chat["failure_class"] = "bridge_send_failed"
    old_chat["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    old_chat["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    project_scoped = _incident()
    project_scoped["incident_id"] = "FRI20260525-PROJECT"
    project_scoped["project_id"] = "F0097"
    project_scoped["chat_id_hash"] = chat_hash
    project_scoped["failure_class"] = "bridge_send_failed"
    project_scoped["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    project_scoped["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    wrong_class = _incident()
    wrong_class["incident_id"] = "FRI20260525-CLASS"
    wrong_class["project_id"] = ""
    wrong_class["chat_id_hash"] = chat_hash
    wrong_class["failure_class"] = "concept_generation_failed"
    wrong_class["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    wrong_class["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    state = {"schema_version": 1, "incidents": [old_chat, project_scoped, wrong_class]}

    resolved = recovery.resolve_incidents_from_customer_visible_repairs(
        state,
        [
            {
                "type": "flyer_recovery_outcome_repaired",
                "ts": NOW.isoformat(),
                "repair_type": "reference_scope_false_positive",
                "status": "sent",
                "chat_id_hash": chat_hash,
            }
        ],
        NOW,
    )

    assert [item["incident_id"] for item in resolved] == ["FRI20260525-CHAT"]
    assert old_chat["status"] == "resolved"
    assert project_scoped["status"] == "open"
    assert wrong_class["status"] == "open"


def test_dry_run_asset_delivery_does_not_resolve_incident():
    incident = _incident()
    incident["incident_id"] = "FRI20260525-DRYRUN"
    incident["project_id"] = "F0097"
    incident["first_seen"] = (NOW - timedelta(minutes=30)).isoformat()
    incident["last_seen"] = (NOW - timedelta(minutes=20)).isoformat()
    state = {"schema_version": 1, "incidents": [incident]}

    resolved = recovery.resolve_incidents_from_customer_visible_repairs(
        state,
        [
            {
                "type": "flyer_assets_delivered",
                "ts": NOW.isoformat(),
                "project_id": "F0097",
                "asset_ids": ["A0001"],
                "outbound_message_ids": ["dry-run:wa.png"],
            }
        ],
        NOW,
    )

    assert resolved == []
    assert incident["status"] == "open"


def test_merge_signals_refreshes_last_seen_from_audit_row_timestamp():
    first_row = _row("project_id=F0065; concept_generation_failed: exit=2 provider down")
    later_row = dict(first_row)
    later_row["ts"] = (NOW + timedelta(minutes=5)).isoformat()
    signal = recovery.classify_decision(first_row, {})
    later_signal = recovery.classify_decision(later_row, {})
    assert signal is not None
    assert later_signal is not None
    state = {"schema_version": 1, "incidents": [recovery.incident_from_signal(signal, NOW)]}

    recovery.merge_signals(state, [later_signal], NOW + timedelta(hours=1))

    assert state["incidents"][0]["last_seen"] == (NOW + timedelta(minutes=5)).isoformat()


def test_merge_signals_ignores_old_replay_after_resolution_but_opens_new_failure():
    first_row = _row("project_id=F0065; concept_generation_failed: exit=2 provider down")
    signal = recovery.classify_decision(first_row, {})
    assert signal is not None
    resolved = recovery.incident_from_signal(signal, NOW)
    resolved["status"] = "resolved"
    resolved["resolved_at"] = NOW.isoformat()
    state = {"schema_version": 1, "incidents": [resolved]}

    old_replay = dict(first_row)
    old_replay["ts"] = (NOW - timedelta(minutes=5)).isoformat()
    old_signal = recovery.classify_decision(old_replay, {})
    assert old_signal is not None
    opened = recovery.merge_signals(state, [old_signal], NOW + timedelta(hours=1))

    assert opened == 0
    assert len(state["incidents"]) == 1

    new_failure = dict(first_row)
    new_failure["ts"] = (NOW + timedelta(minutes=5)).isoformat()
    new_signal = recovery.classify_decision(new_failure, {})
    assert new_signal is not None
    opened = recovery.merge_signals(state, [new_signal], NOW + timedelta(hours=1))

    assert opened == 1
    assert len(state["incidents"]) == 2
    assert state["incidents"][0]["status"] == "resolved"
    assert state["incidents"][1]["status"] == "open"
    assert state["incidents"][1]["incident_id"] != state["incidents"][0]["incident_id"]
    assert state["incidents"][1]["last_seen"] == (NOW + timedelta(minutes=5)).isoformat()


def test_escalates_completed_repair_without_customer_visible_success_once():
    incident = _incident()
    incident["incident_id"] = "FRI20260525-NOEVIDENCE"
    incident["last_seen"] = (NOW - timedelta(hours=2)).isoformat()
    incident["codex"] = {
        "status": "completed",
        "completed_at": (NOW - timedelta(hours=1)).isoformat(),
        "bundle_path": "/tmp/bundle.json",
    }
    state = {"schema_version": 1, "incidents": [incident]}

    escalated = recovery.escalate_unrepaired_incidents(
        state,
        now=NOW,
        stale_after=timedelta(minutes=30),
    )

    assert [item["incident_id"] for item in escalated] == ["FRI20260525-NOEVIDENCE"]
    assert incident["status"] == "operator_action_required"
    assert incident["operator_action"]["reason"] == "worker_completed_no_customer_visible_success"
    assert incident["operator_action"]["required_action"] == "verify_customer_outcome_or_repair_manually"

    escalated_again = recovery.escalate_unrepaired_incidents(
        state,
        now=NOW + timedelta(minutes=5),
        stale_after=timedelta(minutes=30),
    )

    assert escalated_again == []


def test_does_not_escalate_recent_or_already_resolved_incidents():
    recent = _incident()
    recent["incident_id"] = "FRI20260525-RECENT"
    recent["codex"] = {
        "status": "completed",
        "completed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "bundle_path": "/tmp/bundle.json",
    }
    resolved = _incident(status="resolved")
    resolved["incident_id"] = "FRI20260525-RESOLVED"
    resolved["codex"] = {
        "status": "completed",
        "completed_at": (NOW - timedelta(hours=1)).isoformat(),
        "bundle_path": "/tmp/bundle.json",
    }
    state = {"schema_version": 1, "incidents": [recent, resolved]}

    escalated = recovery.escalate_unrepaired_incidents(
        state,
        now=NOW,
        stale_after=timedelta(minutes=30),
    )

    assert escalated == []
    assert recent["status"] == "open"
    assert resolved["status"] == "resolved"


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
