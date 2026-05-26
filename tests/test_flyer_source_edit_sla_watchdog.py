from __future__ import annotations

import importlib.machinery
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-source-edit-sla-watchdog"


def load_module():
    loader = importlib.machinery.SourceFileLoader("flyer_source_edit_sla_watchdog", str(SCRIPT))
    return loader.load_module()


def _project(
    project_id: str,
    *,
    queued_at: str = "2026-05-21T00:00:00Z",
    status: str = "manual_edit_required",
    manual_status: str = "queued",
    reason_code: str = "source_edit_provider_unavailable",
    raw_request: str = "Edit this flyer. Secret phone +17329837841.",
) -> dict:
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": "+17329837841",
        "created_at": "2026-05-21T00:00:00Z",
        "updated_at": queued_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": raw_request,
        "manual_review": {
            "status": manual_status,
            "reason": "source edit provider unavailable",
            "reason_code": reason_code,
            "detail": "OPENAI_API_KEY missing",
            "queued_at": queued_at,
        },
        "assets": [
            {
                "asset_id": "A0001",
                "kind": "reference_image",
                "source": "whatsapp",
                "path": f"/opt/shift-agent/state/flyer/projects/{project_id}/ref.png",
                "mime_type": "image/png",
                "sha256": "a" * 64,
                "received_at": queued_at,
            }
        ],
        "reference_extractions": [],
        "qa_reports": [],
        "final_asset_ids": [],
    }


def test_stale_source_edit_queue_row_pages_operator_and_writes_alert_state(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    decisions = tmp_path / "decisions.log"
    projects.write_text(json.dumps({"projects": [_project("F9001")]}), encoding="utf-8")
    calls: list[dict] = []

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=decisions,
        now=module.parse_utc("2026-05-21T00:11:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        priority=2,
        notify_func=lambda title, message, priority, source: calls.append(
            {"title": title, "message": message, "priority": priority, "source": source}
        ) is None or True,
    )

    assert result["status"] == "alerted"
    assert result["stale_count"] == 1
    assert result["alerted_project_ids"] == ["F9001"]
    assert calls[0]["title"] == "Flyer manual queue SLA breach"
    assert calls[0]["priority"] == 2
    assert "F9001" in calls[0]["message"]
    assert "visual_qa_failed" in calls[0]["message"]
    assert "+17329837841" not in calls[0]["message"]
    assert "Secret phone" not in calls[0]["message"]

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["project_alerts"]["F9001|source_edit_provider_unavailable|2026-05-21T00:00:00+00:00"]["last_alerted_at"] == "2026-05-21T00:11:00+00:00"
    audit = json.loads(decisions.read_text(encoding="utf-8").strip())
    assert audit["type"] == "flyer_source_edit_sla_alert"
    assert sorted(audit["reason_codes"]) == ["source_edit_provider_unavailable", "visual_qa_failed"]
    assert audit["project_ids"] == ["F9001"]
    assert audit["notify_ok"] is True
    assert audit["outcome"] == "alerted"
    from pydantic import TypeAdapter
    from schemas import LogEntry
    TypeAdapter(LogEntry).validate_python(audit)


def test_fresh_source_edit_queue_row_is_advisory_ok_without_alert(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"projects": [_project("F9002")]}), encoding="utf-8")

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=tmp_path / "sla-alerts.json",
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T00:09:59Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda **_: (_ for _ in ()).throw(AssertionError("unexpected notify")),
    )

    assert result["status"] == "ok"
    assert result["stale_count"] == 0
    assert result["alerted_project_ids"] == []


def test_recently_alerted_project_is_throttled(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    projects.write_text(json.dumps({"projects": [_project("F9003")]}), encoding="utf-8")
    state.write_text(
        json.dumps({
            "version": 1,
            "project_alerts": {
                "F9003|source_edit_provider_unavailable|2026-05-21T00:00:00+00:00": {
                    "last_alerted_at": "2026-05-21T00:20:00Z",
                    "last_age_minutes": 20.0,
                    "project_id": "F9003",
                }
            },
        }),
        encoding="utf-8",
    )

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T00:30:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda **_: (_ for _ in ()).throw(AssertionError("unexpected notify")),
    )

    assert result["status"] == "throttled"
    assert result["stale_count"] == 1
    assert result["throttled_project_ids"] == ["F9003"]
    assert result["alerted_project_ids"] == []


def test_notification_failure_returns_degraded_and_keeps_alert_state_unchanged(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    decisions = tmp_path / "decisions.log"
    projects.write_text(json.dumps({"projects": [_project("F9004")]}), encoding="utf-8")

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=decisions,
        now=module.parse_utc("2026-05-21T00:30:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        customer_update_minutes=0,
        notify_func=lambda **_: False,
    )

    assert result["status"] == "alerted_notify_failed"
    assert result["exit_code"] == 0
    assert not state.exists()
    audit = json.loads(decisions.read_text(encoding="utf-8").strip())
    assert audit["notify_ok"] is False
    assert audit["outcome"] == "alerted_notify_failed"
    assert audit["project_ids"] == ["F9004"]


def test_requeued_same_project_alerts_despite_old_throttle_entry(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    projects.write_text(
        json.dumps({"projects": [_project("F9003", queued_at="2026-05-21T00:30:00Z")]}),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps({
            "version": 1,
            "project_alerts": {
                "F9003|source_edit_provider_unavailable|2026-05-21T00:00:00+00:00": {
                    "last_alerted_at": "2026-05-21T00:20:00Z",
                    "last_age_minutes": 20.0,
                    "project_id": "F9003",
                }
            },
        }),
        encoding="utf-8",
    )
    calls: list[dict] = []

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T00:45:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda title, message, priority, source: calls.append(
            {"title": title, "message": message, "priority": priority, "source": source}
        ) is None or True,
    )

    assert result["status"] == "alerted"
    assert result["alerted_project_ids"] == ["F9003"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "F9003|source_edit_provider_unavailable|2026-05-21T00:30:00+00:00" in saved["project_alerts"]


def test_watchdog_alerts_visual_qa_and_ignores_closed_or_non_manual_rows(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    calls: list[dict] = []
    projects.write_text(
        json.dumps({
            "projects": [
                _project("F9005", reason_code="visual_qa_failed"),
                _project("F9006", manual_status="closed_no_send"),
                _project("F9007", status="awaiting_final_approval"),
            ]
        }),
        encoding="utf-8",
    )

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=tmp_path / "sla-alerts.json",
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda title, message, priority, source: calls.append(
            {"title": title, "message": message, "priority": priority, "source": source}
        ) is None or True,
    )

    assert result["status"] == "alerted"
    assert result["stale_count"] == 1
    assert result["alerted_project_ids"] == ["F9005"]
    assert "visual_qa_failed" in calls[0]["message"]


def test_age_uses_manual_queued_at_before_project_created_at(tmp_path):
    module = load_module()
    project = _project("F9008", queued_at="2026-05-21T00:55:00Z")
    project["created_at"] = "2026-05-21T00:00:00Z"
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"projects": [project]}), encoding="utf-8")

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=tmp_path / "sla-alerts.json",
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda **_: (_ for _ in ()).throw(AssertionError("unexpected notify")),
    )

    assert result["status"] == "ok"
    assert result["stale_count"] == 0


def test_reason_code_override_scopes_alert_rows(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    _project("F9010", reason_code="source_edit_provider_unavailable"),
                    _project("F9011", reason_code="visual_qa_failed"),
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []
    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=tmp_path / "sla-alerts.json",
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        reason_codes=("source_edit_provider_unavailable",),
        notify_func=lambda title, message, priority, source: calls.append(
            {"title": title, "message": message, "priority": priority, "source": source}
        ) is None or True,
    )
    assert result["status"] == "alerted"
    assert result["stale_count"] == 1
    assert result["alerted_project_ids"] == ["F9010"]
    assert "Monitored reason codes: source_edit_provider_unavailable." in calls[0]["message"]


def test_watchdog_message_includes_reason_counts_status_split_and_oldest_queue_time(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    calls: list[dict] = []
    projects.write_text(
        json.dumps(
            {
                "projects": [
                    _project("F9020", queued_at="2026-05-21T00:00:00Z", reason_code="source_edit_provider_unavailable", manual_status="queued"),
                    _project("F9021", queued_at="2026-05-21T00:05:00Z", reason_code="visual_qa_failed", manual_status="in_progress"),
                    _project("F9022", queued_at="2026-05-21T00:06:00Z", reason_code="visual_qa_failed", manual_status="queued"),
                ]
            }
        ),
        encoding="utf-8",
    )
    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=tmp_path / "sla-alerts.json",
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda title, message, priority, source: calls.append(
            {"title": title, "message": message, "priority": priority, "source": source}
        ) is None or True,
    )
    assert result["status"] == "alerted"
    message = calls[0]["message"]
    assert ("Status split: queued=2, in_progress=1." in message) or (
        "Status split: in_progress=1, queued=2." in message
    )
    assert "Reason split: source_edit_provider_unavailable=1, visual_qa_failed=2." in message
    assert "Oldest queued at: 2026-05-21T00:00:00+00:00." in message


def test_watchdog_returns_customer_update_summary_counts(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    projects.write_text(json.dumps({"projects": [_project("F9030")]}), encoding="utf-8")
    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=tmp_path / "sla-alerts.json",
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda **_: True,
        customer_chat_resolver=lambda _project: ("", "missing"),
    )
    assert result["customer_updates"]["skipped_project_ids"] == ["F9030"]
    assert result["customer_updates_summary"] == {
        "sent": 0,
        "failed": 0,
        "throttled": 0,
        "skipped": 1,
    }


def test_deploy_installs_and_enables_sla_watchdog_timer():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")
    watchdog_service = (REPO / "src" / "agents" / "flyer" / "systemd" / "flyer-source-edit-sla-watchdog.service").read_text(encoding="utf-8")
    failure_service = (REPO / "src" / "agents" / "flyer" / "systemd" / "flyer-source-edit-sla-watchdog-failure.service").read_text(encoding="utf-8")

    assert "flyer-source-edit-sla-watchdog" in deploy
    assert "flyer-source-edit-sla-watchdog.timer" in deploy
    assert "flyer-source-edit-sla-watchdog.timer" in smoke
    assert "ExecStartPre=/usr/bin/test -x /usr/local/bin/flyer-source-edit-sla-watchdog" in watchdog_service
    assert "ExecStartPre=/usr/bin/test -x /usr/local/bin/shift-agent-notify-owner" not in watchdog_service
    assert "EnvironmentFile=-/opt/shift-agent/.env" in failure_service
    assert "ExecStartPre=/usr/bin/test -x /usr/local/bin/shift-agent-notify-owner" in failure_service
    assert "SuccessExitStatus=5 6" in failure_service


def test_stale_manual_queue_sends_customer_update_after_customer_threshold(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    decisions = tmp_path / "decisions.log"
    projects.write_text(json.dumps({"projects": [_project("F9012", reason_code="visual_qa_failed")]}), encoding="utf-8")
    customer_calls: list[dict] = []

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=decisions,
        customers_path=tmp_path / "customers.json",
        now=module.parse_utc("2026-05-21T00:45:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        customer_update_minutes=30,
        customer_repeat_minutes=120,
        notify_func=lambda **_: True,
        customer_chat_resolver=lambda project: ("17329837841@lid", "audit"),
        customer_notify_func=lambda chat_id, message: (customer_calls.append({"chat_id": chat_id, "message": message}) or (True, "m-customer", "")),
    )

    assert result["status"] == "alerted"
    assert result["customer_updates"]["sent_project_ids"] == ["F9012"]
    assert customer_calls[0]["chat_id"] == "17329837841@lid"
    assert "still in progress" in customer_calls[0]["message"]
    assert "visual_qa_failed" not in customer_calls[0]["message"]
    saved = json.loads(state.read_text(encoding="utf-8"))
    key = "F9012|visual_qa_failed|2026-05-21T00:00:00+00:00"
    assert saved["project_alerts"][key]["last_customer_updated_at"] == "2026-05-21T00:45:00+00:00"
    audit_rows = [json.loads(line) for line in decisions.read_text(encoding="utf-8").splitlines()]
    assert any(row.get("type") == "flyer_manual_queue_customer_update" and row.get("outcome") == "sent" for row in audit_rows)


def test_customer_update_sends_once_per_chat_for_multiple_stale_projects(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    decisions = tmp_path / "decisions.log"
    projects.write_text(
        json.dumps({
            "projects": [
                _project("F9017", reason_code="visual_qa_failed"),
                _project("F9018", reason_code="visual_qa_failed", queued_at="2026-05-21T00:05:00Z"),
            ]
        }),
        encoding="utf-8",
    )
    customer_calls: list[dict] = []

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=decisions,
        customers_path=tmp_path / "customers.json",
        now=module.parse_utc("2026-05-21T03:45:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        customer_update_minutes=30,
        customer_repeat_minutes=120,
        notify_func=lambda **_: True,
        customer_chat_resolver=lambda _project: ("17329837841@lid", "audit"),
        customer_notify_func=lambda chat_id, message: (customer_calls.append({"chat_id": chat_id, "message": message}) or (True, "m-customer", "")),
    )

    assert result["customer_updates"]["sent_project_ids"] == ["F9017"]
    assert result["customer_updates"]["throttled_project_ids"] == ["F9018"]
    assert len(customer_calls) == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    first_key = "F9017|visual_qa_failed|2026-05-21T00:00:00+00:00"
    second_key = "F9018|visual_qa_failed|2026-05-21T00:05:00+00:00"
    assert saved["project_alerts"][first_key]["last_customer_update_outcome"] == "sent"
    assert saved["project_alerts"][second_key]["last_customer_update_outcome"] == "suppressed_same_chat_update"
    assert saved["project_alerts"][second_key]["last_customer_updated_at"] == "2026-05-21T03:45:00+00:00"
    audit_rows = [json.loads(line) for line in decisions.read_text(encoding="utf-8").splitlines()]
    assert any(row.get("outcome") == "sent" and row.get("project_id") == "F9017" for row in audit_rows)
    assert any(row.get("outcome") == "suppressed_same_chat_update" and row.get("project_id") == "F9018" for row in audit_rows)


def test_recent_customer_update_is_throttled_independently_of_operator_alert(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    projects.write_text(json.dumps({"projects": [_project("F9013", reason_code="visual_qa_failed")]}), encoding="utf-8")
    state.write_text(json.dumps({
        "version": 1,
        "project_alerts": {
            "F9013|visual_qa_failed|2026-05-21T00:00:00+00:00": {
                "last_customer_updated_at": "2026-05-21T00:40:00Z",
                "project_id": "F9013",
            }
        },
    }), encoding="utf-8")

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        customer_update_minutes=30,
        customer_repeat_minutes=120,
        notify_func=lambda **_: True,
        customer_chat_resolver=lambda project: (_ for _ in ()).throw(AssertionError("unexpected chat resolver")),
        customer_notify_func=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected customer notify")),
    )

    assert result["customer_updates"]["throttled_project_ids"] == ["F9013"]


def test_watchdog_prunes_alert_state_for_rows_no_longer_in_manual_queue(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    projects.write_text(json.dumps({"projects": [_project("F9014", status="completed", manual_status="closed_no_send")]}), encoding="utf-8")
    state.write_text(json.dumps({
        "version": 1,
        "project_alerts": {
            "F9014|source_edit_provider_unavailable|2026-05-21T00:00:00+00:00": {
                "last_alerted_at": "2026-05-21T00:20:00Z",
                "project_id": "F9014",
            }
        },
    }), encoding="utf-8")

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda **_: (_ for _ in ()).throw(AssertionError("unexpected notify")),
    )

    assert result["status"] == "ok"
    assert result["pruned_alert_rows"] == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["project_alerts"] == {}


def test_watchdog_prunes_superseded_row_key_when_project_requeued(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    projects.write_text(json.dumps({"projects": [_project("F9015", queued_at="2026-05-21T00:30:00Z")]}), encoding="utf-8")
    state.write_text(json.dumps({
        "version": 1,
        "project_alerts": {
            "F9015|source_edit_provider_unavailable|2026-05-21T00:00:00+00:00": {
                "last_alerted_at": "2026-05-21T00:20:00Z",
                "project_id": "F9015",
            }
        },
    }), encoding="utf-8")

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T00:45:00Z"),
        threshold_minutes=10,
        repeat_minutes=60,
        notify_func=lambda **_: True,
    )

    assert result["status"] == "alerted"
    assert result["pruned_alert_rows"] == 1
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert "F9015|source_edit_provider_unavailable|2026-05-21T00:00:00+00:00" not in saved["project_alerts"]
    assert "F9015|source_edit_provider_unavailable|2026-05-21T00:30:00+00:00" in saved["project_alerts"]


def test_non_positive_repeat_minutes_disable_throttle(tmp_path):
    module = load_module()
    projects = tmp_path / "projects.json"
    state = tmp_path / "sla-alerts.json"
    projects.write_text(json.dumps({"projects": [_project("F9016", reason_code="visual_qa_failed")]}), encoding="utf-8")
    state.write_text(json.dumps({
        "version": 1,
        "project_alerts": {
            "F9016|visual_qa_failed|2026-05-21T00:00:00+00:00": {
                "last_alerted_at": "2026-05-21T00:58:00Z",
                "last_customer_updated_at": "2026-05-21T00:58:00Z",
                "project_id": "F9016",
            }
        },
    }), encoding="utf-8")
    customer_calls: list[str] = []

    result = module.run_watchdog(
        projects_path=projects,
        alert_state_path=state,
        decisions_log_path=tmp_path / "decisions.log",
        now=module.parse_utc("2026-05-21T01:00:00Z"),
        threshold_minutes=10,
        repeat_minutes=0,
        customer_update_minutes=10,
        customer_repeat_minutes=-1,
        notify_func=lambda **_: True,
        customer_chat_resolver=lambda _project: ("17329837841@lid", "audit"),
        customer_notify_func=lambda _chat_id, _message: (customer_calls.append("sent") or (True, "mid-1", "")),
    )

    assert result["status"] == "alerted"
    assert result["throttled_project_ids"] == []
    assert result["customer_updates"]["throttled_project_ids"] == []
    assert customer_calls == ["sent"]
