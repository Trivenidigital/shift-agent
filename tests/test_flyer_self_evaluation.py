import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "flyer-self-evaluation.py"
ACTIONS_PATH = REPO_ROOT / "src" / "plugins" / "cf-router" / "actions.py"


def load_module():
    spec = importlib.util.spec_from_file_location("flyer_self_evaluation", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_actions():
    spec = importlib.util.spec_from_file_location("cf_router_actions_for_self_eval_test", ACTIONS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _project(
    project_id: str,
    *,
    status: str = "manual_edit_required",
    raw_request: str = "Please edit this uploaded flyer. Do not change anything else.",
    updated_at: str = "2026-05-20T10:00:00Z",
    manual_review: dict | None = None,
    assets: list[dict] | None = None,
    reference_extractions: list[dict] | None = None,
    qa_reports: list[dict] | None = None,
    locked_facts: list[dict] | None = None,
    final_asset_ids: list[str] | None = None,
    revisions: list[dict] | None = None,
    concepts: list[dict] | None = None,
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
        "locked_facts": locked_facts or [],
        "final_asset_ids": final_asset_ids or [],
        "revisions": revisions or [],
        "concepts": concepts or [],
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


def test_hermes_intent_shadow_coverage_missing_when_expected():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "reason": "flyer_primary_project_created",
                "detail": "project_id=F0065",
            }
        ],
        expected_hermes_intent_mode="shadow",
    )

    incident = next(item for item in report["incidents"] if item["type"] == "hermes_intent_shadow_coverage_missing")
    assert incident["severity"] == "high"
    assert incident["evidence_details"]["shadow_sample_count"] == 0


def test_recovery_operator_action_required_surfaces_as_active_customer_risk():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[],
        recovery_state={
            "schema_version": 1,
            "incidents": [
                {
                    "incident_id": "FRI20260525-NOEVIDENCE",
                    "status": "operator_action_required",
                    "failure_class": "concept_generation_failed",
                    "project_id": "F0097",
                    "last_seen": "2026-05-25T18:57:34Z",
                    "operator_action": {
                        "reason": "worker_completed_no_customer_visible_success",
                        "required_action": "verify_customer_outcome_or_repair_manually",
                        "marked_at": "2026-05-25T21:57:34Z",
                    },
                }
            ],
        },
    )

    incident = next(item for item in report["incidents"] if item["type"] == "recovery_operator_action_required")
    assert incident["severity"] == "high"
    assert incident["project_id"] == "F0097"
    assert incident["evidence_details"]["active_customer_risk"] is True
    assert "verify_customer_outcome_or_repair_manually" in incident["suggested_action"]


def test_hermes_intent_shadow_coverage_not_masked_by_unrelated_row():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "reason": "flyer_primary_project_created",
                "detail": "project_id=F0065",
            },
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "none",
                "validator_ok": True,
                "actual_action": "passthrough",
                "actual_route": "llm_passthrough",
                "route_sequence": [],
                "message_id_hash": "different-message",
                "risk_scope": "none",
                "active_customer_risk": False,
            },
        ],
        expected_hermes_intent_mode="shadow",
    )

    assert any(item["type"] == "hermes_intent_shadow_coverage_missing" for item in report["incidents"])


def test_hermes_intent_shadow_coverage_handles_bypass_then_create_sequence():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "reason": "flyer_active_project_bypassed",
                "detail": "project_id=F0062",
            },
            {
                "type": "cf_router_intercepted",
                "reason": "flyer_primary_project_created",
                "detail": "project_id=F0065",
            },
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "none",
                "validator_ok": True,
                "actual_action": "new_project",
                "actual_route": "flyer_primary_project_created",
                "route_sequence": ["flyer_active_project_bypassed", "flyer_primary_project_created"],
                "message_id_hash": "same-message",
                "risk_scope": "active_project",
                "active_customer_risk": True,
            },
        ],
        expected_hermes_intent_mode="shadow",
    )

    assert not any(item["type"] == "hermes_intent_shadow_coverage_missing" for item in report["incidents"])


def test_hermes_intent_incidents_do_not_flag_intentional_off_mode():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {"type": "cf_router_intercepted", "reason": "flyer_primary_project_created"}
        ],
        expected_hermes_intent_mode="off",
    )

    assert not any(item["type"] == "hermes_intent_shadow_coverage_missing" for item in report["incidents"])


def test_hermes_intent_validator_rejection_and_disagreement_surface():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "fixture",
                "validator_ok": False,
                "validator_reasons": ["customer_copy_policy_violation"],
                "advisory_action": "clarify",
                "actual_action": "new_project",
                "actual_route": "flyer_primary_project_created",
                "risk_scope": "pre_project_customer_visible",
                "active_customer_risk": True,
            }
        ],
    )

    kinds = {item["type"] for item in report["incidents"]}
    assert "hermes_intent_rejected_by_validator" in kinds
    assert "hermes_intent_would_clarify_but_router_mutated" in kinds


def test_hermes_intent_unsupported_active_mode_surfaces():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "unsupported_active_mode",
                "decision_source": "none",
                "validator_ok": True,
                "advisory_action": "observe",
                "actual_action": "passthrough",
                "actual_route": "llm_passthrough",
                "risk_scope": "pre_project_customer_visible",
                "active_customer_risk": True,
            }
        ],
    )

    assert any(item["type"] == "hermes_intent_unsupported_active_mode" for item in report["incidents"])


def test_hermes_intent_classifier_runtime_failures_surface():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "none",
                "classifier_status": "timeout",
                "classifier_latency_ms": 251,
                "classifier_error_kind": "timeout",
                "validator_ok": True,
                "validator_reasons": [],
                "advisory_action": "observe",
                "actual_route": "flyer_primary_project_created",
                "actual_action": "new_project",
                "route_sequence": ["flyer_primary_project_created"],
                "active_customer_risk": True,
                "risk_scope": "pre_project_customer_visible",
            },
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "none",
                "classifier_status": "invalid",
                "classifier_error_kind": "ValidationError",
                "validator_ok": True,
                "validator_reasons": [],
                "advisory_action": "observe",
                "actual_route": "flyer_revision_applied",
                "actual_action": "revision",
                "route_sequence": ["flyer_revision_applied"],
                "active_customer_risk": True,
                "risk_scope": "active_project",
            },
        ],
    )

    kinds = [item["type"] for item in report["incidents"]]
    assert kinds.count("hermes_intent_classifier_runtime_failure") == 2
    failure = next(item for item in report["incidents"] if item["type"] == "hermes_intent_classifier_runtime_failure")
    assert failure["severity"] == "medium"
    assert failure["evidence_details"]["classifier_status"] in {"timeout", "invalid"}


def test_flyer_intent_training_export_missing_when_expected():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "hermes_gateway_future",
                "classifier_status": "success",
                "message_id_hash": "m1",
                "chat_key_hash": "c1",
                "validator_ok": True,
                "validator_reasons": [],
                "advisory_action": "create_project",
                "actual_action": "new_project",
                "actual_route": "flyer_primary_project_created",
                "route_sequence": ["flyer_primary_project_created"],
                "risk_scope": "pre_project_customer_visible",
                "active_customer_risk": True,
            }
        ],
        expect_flyer_intent_training_export=True,
    )

    incident = next(item for item in report["incidents"] if item["type"] == "flyer_intent_training_export_missing")
    assert incident["severity"] == "medium"
    assert incident["evidence_details"]["intent_shadow_rows"] == 1


def test_flyer_intent_training_export_redaction_failure(tmp_path):
    module = load_module()
    artifact = tmp_path / "training.jsonl"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dedupe_key": "k",
                "message_id_hash": "m1",
                "chat_key_hash": "c1",
                "intent": "new_flyer",
                "action": "create_project",
                "input_features": {
                    "raw_request": "Call me +19045551234 at 123 Main St",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[],
        expect_flyer_intent_training_export=True,
        flyer_intent_training_json=artifact,
    )

    assert any(item["type"] == "flyer_intent_training_export_redaction_failed" for item in report["incidents"])


def test_flyer_intent_training_export_stale_when_expected(tmp_path):
    module = load_module()
    artifact = tmp_path / "training.jsonl"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dedupe_key": "k",
                "message_id_hash": "m1",
                "chat_key_hash": "c1",
                "decision_source": "hermes_gateway_future",
                "classifier_status": "success",
                "intent": "new_flyer",
                "action": "create_project",
                "confidence_bucket": "high",
                "validator_ok": True,
                "validator_reasons": [],
                "route_label": "new_project",
                "outcome_label": "route_matched",
                "input_features": {"has_media": False, "route_sequence": ["flyer_primary_project_created"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stale_now = module.parse_utc("2026-05-22T12:00:00Z")
    old_epoch = 946684800
    artifact.touch()
    import os

    os.utime(artifact, (old_epoch, old_epoch))
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "flyer_hermes_intent_decision",
                "mode": "shadow",
                "decision_source": "hermes_gateway_future",
                "classifier_status": "success",
                "message_id_hash": "m1",
                "chat_key_hash": "c1",
                "validator_ok": True,
                "validator_reasons": [],
                "advisory_action": "create_project",
                "actual_action": "new_project",
                "actual_route": "flyer_primary_project_created",
                "route_sequence": ["flyer_primary_project_created"],
                "risk_scope": "pre_project_customer_visible",
                "active_customer_risk": True,
            }
        ],
        now=stale_now,
        expect_flyer_intent_training_export=True,
        flyer_intent_training_json=artifact,
    )

    assert any(item["type"] == "flyer_intent_training_export_stale" for item in report["incidents"])


def _source_contract_extraction() -> dict:
    return {
        "asset_id": "A0001",
        "role": "source_edit_template",
        "provider": "test",
        "status": "succeeded",
        "extracted_facts": [],
        "source_contract": {
            "required_headings": ["Monday Thali Specials"],
            "required_text": ["Sides: salad, raita, papad"],
            "sections": [{"heading": "Veg Thali Specials", "items": ["Rice", "Dal"]}],
            "requested_replacements": {"Triveni Express": "Lakshmi's Kitchen"},
            "forbidden_substrings": ["Triveni Express"],
            "preserve_layout": True,
            "preserve_unmentioned_text": True,
            "confidence": 0.9,
        },
    }


def _source_locked_facts() -> list[dict]:
    return [
        {"fact_id": "source_heading:0", "label": "Source heading", "value": "Monday Thali Specials", "source": "reference_vision", "required": True},
        {"fact_id": "source_required_text:0", "label": "Source required text", "value": "Sides: salad, raita, papad", "source": "reference_vision", "required": True},
        {"fact_id": "source_section:0:heading", "label": "Source section", "value": "Veg Thali Specials", "source": "reference_vision", "required": True},
        {"fact_id": "source_section:0:item:0", "label": "Source item", "value": "Rice", "source": "reference_vision", "required": True},
        {"fact_id": "source_section:0:item:1", "label": "Source item", "value": "Dal", "source": "reference_vision", "required": True},
        {"fact_id": "replacement:0:new", "label": "Required replacement text", "value": "Lakshmi's Kitchen", "source": "customer_text", "required": True},
    ]


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
    assert incident["evidence_details"]["reason_family"] == "provider_readiness"
    assert incident["evidence_details"]["provider_config_gap"] is True
    assert report["eval_candidates"][0]["category"] == "source_edit_provider_posture"


def test_manual_visual_qa_stale_becomes_general_manual_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F9002",
                    manual_review={
                        "status": "queued",
                        "reason": "visual QA failed during finalization",
                        "reason_code": "visual_qa_failed",
                        "detail": "headline contrast unreadable",
                        "queued_at": "2026-05-20T10:00:00Z",
                    },
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_review_stale"
    assert incident["project_id"] == "F9002"
    assert incident["severity"] == "high"
    assert incident["eval_category"] == "manual_queue_sla"
    assert incident["evidence"].endswith("reason_code=visual_qa_failed")
    assert incident["evidence_details"]["manual_reason_code"] == "visual_qa_failed"
    assert incident["evidence_details"]["reason_family"] == "visual_quality"
    assert incident["evidence_details"]["provider_config_gap"] is False
    assert "OpenRouter" not in incident["suggested_action"]


def test_manual_provider_timeout_stale_becomes_general_manual_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F9003",
                    manual_review={
                        "status": "in_progress",
                        "reason": "provider timed out repeatedly",
                        "reason_code": "provider_timeout",
                        "detail": "generation retries exhausted",
                        "queued_at": "2026-05-20T10:00:00Z",
                    },
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_review_stale"
    assert incident["project_id"] == "F9003"
    assert incident["eval_category"] == "manual_queue_sla"
    assert incident["evidence"].endswith("reason_code=provider_timeout")
    assert incident["evidence_details"]["manual_reason_code"] == "provider_timeout"


def test_manual_missing_required_facts_stale_becomes_general_manual_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F9004",
                    manual_review={
                        "status": "queued",
                        "reason": "required locked facts missing",
                        "reason_code": "missing_required_facts",
                        "detail": "missing business_name and phone",
                        "queued_at": "2026-05-20T10:00:00Z",
                    },
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_review_stale"
    assert incident["project_id"] == "F9004"
    assert incident["eval_category"] == "manual_queue_sla"
    assert incident["evidence"].endswith("reason_code=missing_required_facts")
    assert incident["evidence_details"]["manual_reason_code"] == "missing_required_facts"
    assert "required-facts" in incident["suggested_action"]


def test_manual_reference_provider_unavailable_stale_becomes_general_manual_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F9005",
                    manual_review={
                        "status": "in_progress",
                        "reason": "reference provider unavailable",
                        "reason_code": "reference_provider_unavailable",
                        "detail": "source reference image unavailable",
                        "queued_at": "2026-05-20T10:00:00Z",
                    },
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_review_stale"
    assert incident["project_id"] == "F9005"
    assert incident["evidence"].endswith("reason_code=reference_provider_unavailable")
    assert incident["evidence_details"]["manual_reason_code"] == "reference_provider_unavailable"
    assert "reference-media" in incident["suggested_action"]


def test_manual_reference_unsupported_stale_becomes_general_manual_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F9006",
                    manual_review={
                        "status": "queued",
                        "reason": "unsupported reference media",
                        "reason_code": "reference_unsupported",
                        "detail": "PDF is not supported for source-preserving path",
                        "queued_at": "2026-05-20T10:00:00Z",
                    },
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_review_stale"
    assert incident["project_id"] == "F9006"
    assert incident["evidence"].endswith("reason_code=reference_unsupported")
    assert incident["evidence_details"]["manual_reason_code"] == "reference_unsupported"
    assert "reference-media" in incident["suggested_action"]


def test_manual_reference_low_confidence_stale_becomes_general_manual_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F9007",
                    manual_review={
                        "status": "queued",
                        "reason": "reference extraction confidence too low",
                        "reason_code": "reference_low_confidence",
                        "detail": "could not confidently parse source text blocks",
                        "queued_at": "2026-05-20T10:00:00Z",
                    },
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
    )

    incident = report["incidents"][0]
    assert incident["type"] == "manual_review_stale"
    assert incident["project_id"] == "F9007"
    assert incident["evidence"].endswith("reason_code=reference_low_confidence")
    assert incident["evidence_details"]["manual_reason_code"] == "reference_low_confidence"
    assert "reference-media" in incident["suggested_action"]


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


def test_malformed_business_name_fact_becomes_incident():
    module = load_module()
    report = module.build_report(
        projects={
            "projects": [
                _project(
                    "F0065",
                    locked_facts=[
                        {
                            "fact_id": "business_name",
                            "label": "Business",
                            "value": "d like you to help me with evening snacks flier",
                            "source": "customer_text",
                            "required": True,
                        }
                    ],
                )
            ]
        },
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    incident = next(item for item in report["incidents"] if item["type"] == "malformed_business_name_fact")
    assert incident["project_id"] == "F0065"
    assert incident["eval_category"] == "flyer_fact_contract"
    assert incident["evidence_details"]["source"] == "customer_text"


def test_duplicate_initial_ack_becomes_incident_from_outbound_audit_text():
    module = load_module()
    report = module.build_report(
        projects={"projects": []},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "ts": "2026-05-20T10:05:00Z",
                "message_id": "outbound-processing-ack",
                "project_id": "F0065",
                "outbound_text": "Flyer Studio\n------------\nGot it. I'm creating your flyer now and will send a preview here shortly.",
            },
            {
                "type": "cf_router_intercepted",
                "ts": "2026-05-20T10:06:00Z",
                "message_id": "outbound-intake-ack",
                "project_id": "F0065",
                "outbound_text": "Flyer Studio\n------------\nGot it. I have your flyer request and will send an update here shortly.",
            },
        ],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    incident = next(item for item in report["incidents"] if item["type"] == "duplicate_initial_ack")
    assert incident["project_id"] == "F0065"
    assert incident["count"] == 2
    assert incident["eval_category"] == "customer-message copy"


def test_static_source_scan_finds_current_ack_leaks_when_audit_lacks_body(tmp_path):
    module = load_module()
    source = tmp_path / "actions.py"
    source.write_text(
        """
def send_flyer_manual_edit_ack(project_id):
    body = f'''Flyer Studio
------------
I received your uploaded flyer and queued project {project_id} for a source-preserving edit.
Requested edit: Authorized flyer/source artwork update.
Original customer request: long raw request
'''
    return send_flyer_text(body)
""",
        encoding="utf-8",
    )

    report = module.build_report(
        projects={"projects": []},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
        source_files=[source],
    )

    incident = report["incidents"][0]
    assert incident["type"] == "customer_copy_static_internal_leak"
    assert incident["project_id"] == ""
    assert "actions.py" in incident["evidence"]
    assert "source-code customer ack scan" in incident["suggested_action"]


def test_static_source_scan_ignores_internal_terms_outside_customer_ack_blocks(tmp_path):
    module = load_module()
    source = tmp_path / "actions.py"
    source.write_text(
        """
PROVIDER_DEBUG = "provider reason_code operator queued project"

def unrelated_helper():
    return "Original customer request"

def send_flyer_manual_edit_ack(project_id):
    body = "Flyer Studio\\n------------\\nGot it. This needs a careful flyer edit."
    return send_flyer_text(body)
""",
        encoding="utf-8",
    )

    report = module.build_report(
        projects={"projects": []},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
        source_files=[source],
    )

    assert report["incidents"] == []


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
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "e" * 64})
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

    assert "source_contract_qa_missing" in [item["type"] for item in report["incidents"]]
    qa_missing = next(item for item in report["incidents"] if item["type"] == "source_contract_qa_missing")
    assert "visual QA" in qa_missing["suggested_action"]


def test_generic_passed_ocr_qa_does_not_count_as_source_aware_qa():
    """Regression for PR review: generic passed OCR QA is not proof that the
    source contract was verified. The report must still flag the missing
    source-aware QA condition.
    """
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "f" * 64})
    project = _project(
        "F9007",
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
        qa_reports=[
            {
                "project_id": "F9007",
                "asset_id": "A0002",
                "artifact_path": "/opt/shift-agent/state/flyer/projects/F9007/preview.png",
                "artifact_sha256": "b" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "openrouter-vision",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "extracted_text": "Lakshmi's Kitchen",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_qa_missing" in [item["type"] for item in report["incidents"]]


def test_marker_only_source_contract_qa_does_not_satisfy_source_aware_check():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "1" * 64})
    project = _project(
        "F9008",
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
                "source_contract": {"required_text": ["Monday Thali Specials"], "confidence": 0.9},
            }
        ],
        qa_reports=[
            {
                "project_id": "F9008",
                "asset_id": "A0002",
                "artifact_path": "/opt/shift-agent/state/flyer/projects/F9008/preview.png",
                "artifact_sha256": "c" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "source-contract-qa",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": ["source contract verified"],
                "extracted_text": "Monday Thali Specials",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert [item["type"] for item in report["incidents"]] == ["source_contract_qa_missing"]


def test_source_contract_qa_with_locked_fact_evidence_satisfies_source_aware_check():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "e" * 64})
    project = _project(
        "F9010",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[
            {
                "project_id": "F9010",
                "asset_id": "A0002",
                "artifact_path": "/opt/shift-agent/state/flyer/projects/F9010/preview.png",
                "artifact_sha256": "e" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "openrouter-vision",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "extracted_text": "Lakshmis Kitchen Monday Thali Specials Veg Thali Specials Rice Dal Sides: salad, raita, papad",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
        final_asset_ids=[],
    )
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert report["incidents"] == []


def test_stale_source_qa_report_does_not_satisfy_current_project_version():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "2" * 64})
    project = _project(
        "F9015",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[
            {
                "project_id": "F9015",
                "asset_id": "A0002",
                "artifact_sha256": "2" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "openrouter-vision",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "extracted_text": "Lakshmis Kitchen Monday Thali Specials Veg Thali Specials Rice Dal Sides: salad, raita, papad",
            }
        ],
    )
    project["version"] = 3
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_qa_missing" in [item["type"] for item in report["incidents"]]


def test_stale_source_qa_report_does_not_satisfy_current_project_timestamp():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "4" * 64})
    project = _project(
        "F9017",
        status="awaiting_final_approval",
        updated_at="2026-05-20T10:30:00Z",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[
            {
                "project_id": "F9017",
                "asset_id": "A0002",
                "artifact_sha256": "4" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "openrouter-vision",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "extracted_text": "Lakshmis Kitchen Monday Thali Specials Veg Thali Specials Rice Dal Sides: salad, raita, papad",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )
    project["version"] = 1
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_qa_missing" in [item["type"] for item in report["incidents"]]


def test_source_qa_report_missing_binding_fields_fails_closed():
    module = load_module()
    for missing_field in ("checked_at", "project_id", "project_version", "artifact_sha256"):
        generated = _reference_asset("A0002")
        generated.update({"kind": "concept_preview", "source": "generated", "sha256": "6" * 64})
        qa_report = {
            "project_id": f"F91{missing_field[:2]}",
            "asset_id": "A0002",
            "artifact_sha256": "6" * 64,
            "project_version": 1,
            "output_format": "concept_preview",
            "provider": "openrouter-vision",
            "qa_source": "ocr_vision",
            "status": "passed",
            "blockers": [],
            "warnings": [],
            "extracted_text": "Lakshmis Kitchen Monday Thali Specials Veg Thali Specials Rice Dal Sides: salad, raita, papad",
            "checked_at": "2026-05-20T10:10:00Z",
        }
        qa_report.pop(missing_field)
        project = _project(
            qa_report.get("project_id") or f"F91{missing_field[:2]}",
            status="awaiting_final_approval",
            manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
            assets=[_reference_asset(), generated],
            reference_extractions=[_source_contract_extraction()],
            qa_reports=[qa_report],
        )
        project["version"] = 1
        project["locked_facts"] = _source_locked_facts()

        report = module.build_report(
            projects={"projects": [project]},
            decision_entries=[],
            now=module.parse_utc("2026-05-20T11:00:00Z"),
        )

        assert "source_contract_qa_missing" in [item["type"] for item in report["incidents"]], missing_field


def test_complete_later_source_qa_report_prevents_false_fact_gap():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "3" * 64})
    partial = {
        "project_id": "F9016",
        "asset_id": "A0002",
        "artifact_sha256": "3" * 64,
        "project_version": 2,
        "output_format": "concept_preview",
        "provider": "openrouter-vision",
        "qa_source": "ocr_vision",
        "status": "passed",
        "blockers": [],
        "warnings": [],
        "extracted_text": "Monday Thali Specials",
        "checked_at": "2026-05-20T10:10:00Z",
    }
    complete = dict(partial)
    complete["extracted_text"] = "Lakshmis Kitchen Monday Thali Specials Veg Thali Specials Rice Dal Sides: salad, raita, papad"
    project = _project(
        "F9016",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[partial, complete],
    )
    project["version"] = 2
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert report["incidents"] == []


def test_missing_source_contract_locked_facts_are_reported():
    module = load_module()
    project = _project(
        "F9011",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset()],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[],
    )
    project["locked_facts"] = []

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_locked_fact_gap" in [item["type"] for item in report["incidents"]]
    gap = next(item for item in report["incidents"] if item["type"] == "source_contract_locked_fact_gap")
    assert "source_required_text:0" in gap["evidence_details"]["locked_fact_missing"]
    assert "replacement:0:new" in gap["evidence_details"]["locked_fact_missing"]


def test_source_contract_qa_fact_gap_reports_missing_required_text_and_replacement():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "f" * 64})
    project = _project(
        "F9012",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[
            {
                "project_id": "F9012",
                "asset_id": "A0002",
                "artifact_path": "/opt/shift-agent/state/flyer/projects/F9012/preview.png",
                "artifact_sha256": "f" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "openrouter-vision",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "extracted_text": "Monday Thali Specials Veg Thali Specials Rice Dal",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_qa_fact_gap" in [item["type"] for item in report["incidents"]]
    gap = next(item for item in report["incidents"] if item["type"] == "source_contract_qa_fact_gap")
    assert "source_required_text:0" in gap["evidence_details"]["qa_missing_required_text"]
    assert "replacement:0:new" in gap["evidence_details"]["qa_missing_required_text"]


def test_source_contract_forbidden_text_present_is_reported():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "1" * 64})
    project = _project(
        "F9013",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[
            {
                "project_id": "F9013",
                "asset_id": "A0002",
                "artifact_path": "/opt/shift-agent/state/flyer/projects/F9013/preview.png",
                "artifact_sha256": "1" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "openrouter-vision",
                "qa_source": "ocr_vision",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "extracted_text": "Lakshmi's Kitchen Monday Thali Specials Veg Thali Specials Rice Dal Sides: salad, raita, papad Triveni Express",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_forbidden_text_present" in [item["type"] for item in report["incidents"]]
    hit = next(item for item in report["incidents"] if item["type"] == "source_contract_forbidden_text_present")
    assert hit["evidence_details"]["forbidden_text_hits"] == ["Triveni Express"]


def test_latest_request_not_reflected_flags_fresh_request_routed_as_revision():
    module = load_module()
    latest = (
        "I'd like you to help me with evening snacks flier from 4 PM to 7 PM. "
        "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
    )
    project = _project(
        "F9301",
        status="awaiting_final_approval",
        raw_request="Old Lakshmi's Kitchen thali flyer for lunch specials.",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        revisions=[{"request_text": latest, "message_id": "live-evening-snacks", "created_at": "2026-05-20T10:05:00Z"}],
        concepts=[
            {
                "concept_id": "C1",
                "title": "Lakshmi Lunch",
                "style_summary": "Traditional thali lunch flyer",
                "prompt": "Create a Lakshmi's Kitchen lunch thali flyer with rice and dal.",
            }
        ],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    kinds = [item["type"] for item in report["incidents"]]
    assert "latest_request_not_reflected" in kinds
    assert "new_flyer_routed_as_revision" in kinds
    reflected = next(item for item in report["incidents"] if item["type"] == "latest_request_not_reflected")
    assert reflected["evidence_details"]["active_customer_risk"] is True
    assert "evening snacks" in reflected["evidence_details"]["missing_terms"]
    assert "4 pm" in reflected["evidence_details"]["missing_terms"]
    assert "wednesday" in reflected["evidence_details"]["missing_terms"]
    assert report["status"] == "red"


def test_self_eval_fresh_request_detection_matches_router_for_covered_phrase():
    module = load_module()
    actions = load_actions()
    latest = (
        "Evening snacks flier from 4 PM to 7 PM. "
        "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
    )
    project = _project(
        "F9305",
        status="awaiting_final_approval",
        raw_request="Old Lakshmi's Kitchen thali flyer for lunch specials.",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        revisions=[{"request_text": latest, "message_id": "evening-snacks-short"}],
        concepts=[
            {
                "concept_id": "C1",
                "title": "Lakshmi Lunch",
                "style_summary": "Traditional thali lunch flyer",
                "prompt": "Create a Lakshmi's Kitchen lunch thali flyer with rice and dal.",
            }
        ],
    )

    assert actions.should_start_new_flyer_over_active(latest, has_media=False) is True
    assert module.looks_like_fresh_flyer_request(latest) is True

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "new_flyer_routed_as_revision" in [item["type"] for item in report["incidents"]]


def test_new_flyer_routed_as_revision_does_not_depend_on_reflection_threshold():
    module = load_module()
    latest = "Please create Diwali poster for Friday sale 11 AM"
    project = _project(
        "F9304",
        status="awaiting_final_approval",
        raw_request="Old breakfast flyer for a weekend buffet.",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        revisions=[{"request_text": latest, "message_id": "diwali-friday-sale"}],
        concepts=[
            {
                "concept_id": "C1",
                "title": "Breakfast Buffet",
                "style_summary": "Weekend breakfast offer",
                "prompt": "Create an old breakfast buffet flyer.",
            }
        ],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    kinds = [item["type"] for item in report["incidents"]]
    assert module.looks_like_fresh_flyer_request(latest) is True
    assert module.salient_request_terms(latest) == ["11 am"]
    assert "new_flyer_routed_as_revision" in kinds
    assert "latest_request_not_reflected" not in kinds


def test_latest_request_not_reflected_does_not_flag_when_prompt_contains_latest_terms():
    module = load_module()
    latest = (
        "I'd like you to help me with evening snacks flier from 4 PM to 7 PM. "
        "Include 5 top South Indian snack items. Its Wednesday through Saturday event"
    )
    project = _project(
        "F9302",
        status="awaiting_final_approval",
        raw_request=latest,
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        revisions=[{"request_text": latest, "message_id": "live-evening-snacks"}],
        concepts=[
            {
                "concept_id": "C1",
                "title": "Evening Snacks",
                "style_summary": "South Indian snack event flyer",
                "prompt": (
                    "Create evening snacks flier from 4 PM to 7 PM, Wednesday through "
                    "Saturday, with South Indian snack items."
                ),
            }
        ],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    kinds = [item["type"] for item in report["incidents"]]
    assert "latest_request_not_reflected" not in kinds
    assert "new_flyer_routed_as_revision" in kinds


def test_preview_approved_then_final_qa_failed_is_active_customer_risk():
    module = load_module()
    project = _project(
        "F9303",
        status="manual_edit_required",
        raw_request="Create evening snacks flyer.",
        manual_review={
            "status": "queued",
            "reason": "visual QA failed during finalization",
            "reason_code": "visual_qa_failed",
            "detail": "final package QA failed after approval",
            "queued_at": "2026-05-20T10:10:00Z",
        },
        assets=[{"asset_id": "A0002", "kind": "concept_preview", "source": "generated", "sha256": "9" * 64}],
        concepts=[{"concept_id": "C1", "preview_asset_id": "A0002", "prompt": "Create evening snacks flyer."}],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "ts": "2026-05-20T10:01:00Z",
                "project_id": "F9303",
                "reason": "flyer_primary_project_created",
                "detail": "project_id=F9303; ack_message_id=preview-mid",
            },
            {
                "type": "cf_router_intercepted",
                "ts": "2026-05-20T10:03:00Z",
                "project_id": "F9303",
                "reason": "flyer_primary_failed",
                "detail": "project_id=F9303; approve=true; finalize-flyer-assets exit=1: visual_qa_failed",
            },
        ],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    kinds = [item["type"] for item in report["incidents"]]
    assert "preview_approved_final_qa_failed" in kinds
    item = next(item for item in report["incidents"] if item["type"] == "preview_approved_final_qa_failed")
    assert item["severity"] == "high"
    assert item["evidence_details"]["active_customer_risk"] is True
    assert item["project_id"] == "F9303"


def test_report_output_redacts_sensitive_values_from_json_and_markdown(tmp_path):
    module = load_module()
    source = tmp_path / "actions.py"
    source.write_text(
        """
def send_flyer_manual_edit_ack(project_id):
    body = "Original customer request: OPENAI_API_KEY=sk-testsecret123456789 +17329837841 17329837841@lid /opt/shift-agent/state/flyer/private.png"
    return send_flyer_text(body)
""",
        encoding="utf-8",
    )

    report = module.build_report(
        projects={"projects": [_project("F9014")]},
        decision_entries=[
            {
                "type": "cf_router_intercepted",
                "project_id": "F9014",
                "outbound_text": "Requested edit: Bearer verysecret987 +17329837841 17329837841@s.whatsapp.net C:\\secret\\asset.png",
            }
        ],
        now=module.parse_utc("2026-05-20T11:05:00Z"),
        source_files=[source],
    )
    blob = json.dumps(report) + module.render_markdown(report)

    assert "sk-testsecret123456789" not in blob
    assert "verysecret987" not in blob
    assert "+17329837841" not in blob
    assert "17329837841@lid" not in blob
    assert "17329837841@s.whatsapp.net" not in blob
    assert "/opt/shift-agent/state/flyer/private.png" not in blob
    assert "C:\\secret\\asset.png" not in blob
    assert "[redacted" in blob


def test_redaction_handles_secret_keys_and_formatted_phone_values():
    module = load_module()
    report = module.sanitize_report(
        {
            "type": "demo",
            "access_token": "plainsecret",
            "api_key": "anothersecret",
            "nested": {
                "message": "Call 7329837841 or (732) 983-7841 or +17329837841",
            },
        }
    )
    blob = json.dumps(report)

    assert "plainsecret" not in blob
    assert "anothersecret" not in blob
    assert "7329837841" not in blob
    assert "(732) 983-7841" not in blob
    assert "+17329837841" not in blob
    assert "[redacted" in blob


def test_operator_review_qa_satisfies_source_aware_check():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "d" * 64})
    project = _project(
        "F9009",
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
                "source_contract": {"required_text": ["Monday Thali Specials"], "confidence": 0.9},
            }
        ],
        qa_reports=[
            {
                "project_id": "F9009",
                "asset_id": "A0002",
                "artifact_path": "/opt/shift-agent/state/flyer/projects/F9009/preview.png",
                "artifact_sha256": "d" * 64,
                "project_version": 1,
                "output_format": "concept_preview",
                "provider": "operator-cockpit",
                "qa_source": "operator_review",
                "status": "passed",
                "blockers": [],
                "warnings": [],
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert report["incidents"] == []


def test_stray_operator_review_without_current_asset_does_not_satisfy_source_aware_check():
    module = load_module()
    generated = _reference_asset("A0002")
    generated.update({"kind": "concept_preview", "source": "generated", "sha256": "5" * 64})
    project = _project(
        "F9018",
        status="awaiting_final_approval",
        manual_review={"status": "none", "reason": "", "reason_code": "unclassified"},
        assets=[_reference_asset(), generated],
        reference_extractions=[_source_contract_extraction()],
        qa_reports=[
            {
                "project_id": "F9018",
                "artifact_sha256": "5" * 64,
                "project_version": 1,
                "provider": "operator-cockpit",
                "qa_source": "operator_review",
                "status": "passed",
                "checked_at": "2026-05-20T10:10:00Z",
            }
        ],
    )
    project["locked_facts"] = _source_locked_facts()

    report = module.build_report(
        projects={"projects": [project]},
        decision_entries=[],
        now=module.parse_utc("2026-05-20T11:00:00Z"),
    )

    assert "source_contract_qa_missing" in [item["type"] for item in report["incidents"]]


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
    assert report["incidents"][0]["evidence_details"]["active_customer_risk"] is True


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
    assert out.exists()
    assert payload["summary"]["incident_count"] == len(payload["incidents"])
    assert payload["summary"]["incident_count"] >= 1
    incident_types = {item.get("type") for item in payload["incidents"] if isinstance(item, dict)}
    assert "manual_source_edit_stale" in incident_types

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
    assert "Rollout Readiness" not in markdown.stdout
    assert "manual_source_edit_stale" in markdown.stdout
    assert "customer-copy log scan only sees decisions.log rows with outbound text fields" in markdown.stdout

    markdown_with_rollout = subprocess.run(
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
            "--rollout-readiness",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert markdown_with_rollout.returncode == 0, markdown_with_rollout.stderr
    assert "Rollout Readiness" in markdown_with_rollout.stdout


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
