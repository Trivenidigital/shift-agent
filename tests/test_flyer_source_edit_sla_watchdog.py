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


def test_notification_failure_returns_error_and_does_not_advance_state(tmp_path):
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
        notify_func=lambda **_: False,
    )

    assert result["status"] == "notify_failed"
    assert result["exit_code"] == 6
    assert not state.exists()
    audit = json.loads(decisions.read_text(encoding="utf-8").strip())
    assert audit["notify_ok"] is False
    assert audit["outcome"] == "notify_failed"
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


def test_deploy_installs_and_enables_sla_watchdog_timer():
    deploy = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh").read_text(encoding="utf-8")
    smoke = (REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-smoke-test.sh").read_text(encoding="utf-8")

    assert "flyer-source-edit-sla-watchdog" in deploy
    assert "flyer-source-edit-sla-watchdog.timer" in deploy
    assert "flyer-source-edit-sla-watchdog.timer" in smoke
