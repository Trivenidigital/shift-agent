import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "flyer-self-evaluation.py"


def load_module():
    spec = importlib.util.spec_from_file_location("flyer_self_evaluation", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _project(
    project_id: str,
    *,
    status: str = "manual_edit_required",
    raw_request: str = "Please edit this uploaded flyer. Do not change anything else.",
    updated_at: str = "2026-05-20T11:00:00Z",
    manual_review: dict | None = None,
    assets: list[dict] | None = None,
    reference_extractions: list[dict] | None = None,
    qa_reports: list[dict] | None = None,
    final_asset_ids: list[str] | None = None,
) -> dict:
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": "+19045550104",
        "created_at": "2026-05-20T10:00:00Z",
        "updated_at": updated_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": raw_request,
        "manual_review": manual_review
        or {
            "status": "queued",
            "reason": "source edit provider unavailable",
            "reason_code": "source_edit_provider_unavailable",
            "detail": "OPENAI_API_KEY missing",
            "queued_at": "2026-05-20T10:00:00Z",
        },
        "assets": assets or [],
        "reference_extractions": reference_extractions or [],
        "qa_reports": qa_reports or [],
        "final_asset_ids": final_asset_ids or [],
    }


def _reference_asset(asset_id: str = "A0001") -> dict:
    return {
        "asset_id": asset_id,
        "kind": "reference_image",
        "source": "whatsapp",
        "path": "/opt/shift-agent/state/flyer/projects/F9001/ref.png",
        "mime_type": "image/png",
        "sha256": "a" * 64,
        "received_at": "2026-05-20T10:00:00Z",
    }


def test_manual_source_edit_stale_becomes_incident():
    module = load_module()
    report = module.build_report(
        projects={"projects": [_project("F9001")]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_source_edit_stale"
    assert incident["project_id"] == "F9001"
    assert incident["severity"] == "high"
    assert "OpenRouter" in incident["suggested_action"] or "manual queue" in incident["suggested_action"]
    assert report["eval_candidates"][0]["category"] == "source_edit_provider_posture"


def test_customer_copy_internal_leak_detected_from_decisions_log():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "ts": "2026-05-20T10:05:00Z",
                "project_id": "F0063",
                "outbound_text": (
                    "Flyer Studio\n------------\n"
                    "I received your uploaded flyer and queued project F0063 for a source-preserving edit.\n"
                    "Requested edit: Authorized flyer/source artwork update.\n"
                    "Original customer request: long raw request"
                ),
            }
        ],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "customer_copy_internal_leak"
    assert incident["project_id"] == "F0063"
    assert "customer-message copy" in incident["suggested_action"]
    assert "Requested edit:" in incident["evidence"]


def test_missing_source_contract_for_exact_edit_is_reported():
    module = load_module()
    project = _project(
        "F9002",
        status="awaiting_final_approval",
        raw_request=(
            "I'd like you use this flyer. Do not change anything else, "
            "replace Triveni Express with Lakshmi's Kitchen."
        ),
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset()],
        reference_extractions=[],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert [item["type"] for item in report["incidents"]] == ["source_contract_missing"]
    assert report["eval_candidates"][0]["category"] == "source_contract_visual_qa"


def test_source_contract_without_qa_for_generated_asset_is_reported():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated"})
    project = _project(
        "F9003",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[
            {
                "asset_id": "A0001",
                "role": "source_edit_template",
                "provider": "test",
                "status": "succeeded",
                "extracted_facts": [],
                "source_contract": {
                    "required_text": ["Monday Thali Specials"],
                    "preserve_layout": True,
                    "preserve_unmentioned_text": True,
                    "confidence": 0.9,
                },
            }
        ],
        qa_reports=[],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert [item["type"] for item in report["incidents"]] == ["source_contract_qa_missing"]
    assert "visual QA" in report["incidents"][0]["suggested_action"]


def test_repeated_status_checkins_are_grouped_without_creating_projects():
    module = load_module()
    entries = [
        {
            "type": "cf_router_intercepted",
            "ts": f"2026-05-20T10:0{i}:00Z",
            "project_id": "F9004",
            "body": "any update?",
            "reason": "flyer_reference_exact_edit_status",
        }
        for i in range(4)
    ]

    report = module.build_report(
        projects={"projects": []},
        decision_entries=entries,
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert [item["type"] for item in report["incidents"]] == ["repeated_status_checkins"]
    assert report["incidents"][0]["count"] == 4


def test_clean_state_has_empty_incidents_and_green_status():
    module = load_module()
    project = _project(
        "F9005",
        status="delivered",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        raw_request="Create a grand opening flyer for Lakshmi's Kitchen.",
        assets=[],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert report["status"] == "green"
    assert report["incidents"] == []
    assert report["eval_candidates"] == []


def test_cli_writes_json_report_and_markdown(tmp_path):
    projects = tmp_path / "projects.json"
    decisions = tmp_path / "decisions.log"
    out = tmp_path / "nested" / "report.json"
    projects.write_text(json.dumps({"projects": [_project("F9006")]}), encoding="utf-8")
    decisions.write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--projects",
            str(projects),
            "--decisions-log",
            str(decisions),
            "--now",
            "2026-05-20T11:05:00Z",
            "--format",
            "json",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["incident_count"] == 1

    markdown = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--projects",
            str(projects),
            "--decisions-log",
            str(decisions),
            "--now",
            "2026-05-20T11:05:00Z",
            "--format",
            "markdown",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert markdown.returncode == 0, markdown.stderr
    assert "Flyer Self-Evaluation" in markdown.stdout
    assert "manual_source_edit_stale" in markdown.stdout


def test_static_guard_no_live_mutation_or_network_paths():
    text = MODULE_PATH.read_text(encoding="utf-8")
    banned = [
        "bridge_post(",
        "bridge_send",
        "requests.",
        "urllib.request",
        "subprocess.",
        "gh pr",
        "git merge",
        "git push",
        "flyer-manual-queue --close",
        "send-flyer-campaign",
    ]
    for needle in banned:
        assert needle not in text
