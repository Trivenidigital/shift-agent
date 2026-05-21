from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flyer_incident_replay" / "flyer_incidents.json"

sys.path.insert(0, str(SRC))
from agents.flyer.customer_copy_policy import classify_initial_ack, scan_customer_text  # noqa: E402

# Replay-harness helpers extracted to a shared module so both incident-replay
# and rollout-replay tests reuse one harness. See tests/_flyer_replay_helpers.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _flyer_replay_helpers import (  # noqa: E402
    assert_expected_route,
    build_event,
    install_common_replay_mocks,
    real_create_project,
)


def _fixtures() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_incident_replay_fixture_set_is_large_enough_and_unique():
    fixtures = _fixtures()
    ids = [item["id"] for item in fixtures]
    assert len(fixtures) >= 8
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda item: item["id"])
def test_flyer_incident_replay_fixture(fixture, tmp_path, monkeypatch):
    hooks, actions, calls, audits, sent, identity_calls = install_common_replay_mocks(monkeypatch, tmp_path, fixture)
    expect = fixture["expect"]

    def fake_create(**kwargs):
        calls.append("trigger_create_flyer_project")
        assert kwargs["chat_id"] == fixture["chat_id"]
        assert kwargs["customer_phone"] == fixture["resolved_identity"]["phone"]
        if fixture["mode"] == "fresh_new_over_active" and fixture["id"].startswith("F0065"):
            project = real_create_project(monkeypatch, tmp_path, fixture)
            facts = {fact["fact_id"]: fact for fact in project["locked_facts"]}
            assert facts["business_name"]["value"] == expect["business_name"]
            assert facts["business_name"]["source"] == "customer_profile"
            assert facts["campaign_title"]["value"] == expect["campaign_title"]
            assert facts["campaign_title"]["source"] == "customer_text"
        else:
            project = {
                "project_id": "F9001",
                "status": "intake_started",
                "fields": {"event_or_business_name": "Replay Campaign", "contact_info": fixture["resolved_identity"]["phone"]},
                "locked_facts": [],
                "concepts": [],
            }
        return True, json.dumps(project), project

    monkeypatch.setattr(actions, "trigger_create_flyer_project", fake_create)
    update_calls: list[tuple] = []

    def fake_update(*args, **_kwargs):
        calls.append("invoke_update_flyer_project")
        update_calls.append(args)
        return True, json.dumps({"revision_requires_clarification": False, "revision_patch": {}})

    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: calls.append("trigger_generate_flyer_concepts") or (False, "visual_qa_failed: missing required visible fact: business_name"))
    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_a, **_kw: calls.append("finalize_and_send_flyer") or (False, "visual_qa_failed: missing required visible fact: business_name"))
    monkeypatch.setattr(actions, "flyer_source_edit_preflight", lambda *_a, **_kw: (False, "provider unavailable", "source_edit_provider_unavailable"))

    mode = fixture["mode"]
    if mode == "reference_scope_choice":
        monkeypatch.setattr(actions, "consume_flyer_reference_scope_choice", lambda *_a, **_kw: fixture["pending"])
    elif mode == "source_new_status_check":
        monkeypatch.setattr(actions, "parse_source_vs_new_followup", lambda _text: ("", ""))
        monkeypatch.setattr(actions, "peek_flyer_source_vs_new_pending", lambda **_kw: fixture["pending"])
    elif mode == "source_choice":
        monkeypatch.setattr(actions, "parse_source_vs_new_followup", lambda _text: ("source", ""))
        monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", lambda *_a, **_kw: fixture["pending"])
        monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda _p: True)

    result = hooks.pre_gateway_dispatch(build_event(fixture))

    assert identity_calls and identity_calls[0] == fixture["chat_id"]
    assert_expected_route(fixture, expect, result, calls, audits, sent, update_calls)
    for forbidden in expect.get("forbidden_calls", []):
        assert forbidden not in calls
    for required in expect.get("must_call", []):
        assert required in calls
    audit_reasons = [row.get("reason") for row in audits]
    for required_audit in expect.get("must_audit", []):
        assert required_audit in audit_reasons, audits
    for snippet in expect.get("must_send_contains", []):
        assert any(snippet in text for text in sent), sent
    for text in sent:
        assert not scan_customer_text(text, raw_request=fixture["text"]).hits, text
    if expect.get("duplicate_initial_ack") is False:
        markers = [classify_initial_ack(text) for text in sent]
        assert not any("processing" in marker for marker in markers) or not any("intake" in marker for marker in markers)


def test_preview_approved_final_qa_replay_feeds_self_eval_active_risk(tmp_path, monkeypatch):
    fixture = next(item for item in _fixtures() if item["mode"] == "approve_final_qa_failure")
    hooks, actions, _calls, audits, _sent, _identity_calls = install_common_replay_mocks(monkeypatch, tmp_path, fixture)
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_a, **_kw: (False, "visual_qa_failed: missing required visible fact: business_name"))
    result = hooks.pre_gateway_dispatch(build_event(fixture))
    assert result is None
    assert any(
        row.get("reason") == "flyer_primary_failed"
        and "approve=true" in str(row.get("detail") or "")
        and "visual_qa_failed" in str(row.get("detail") or "")
        for row in audits
    )

    spec = importlib.util.spec_from_file_location("flyer_self_eval_replay", REPO / "tools" / "flyer-self-evaluation.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    report = module.build_report(
        projects={"projects": [fixture["active_project"]]},
        decision_entries=[
            {"type": "cf_router_intercepted", "reason": "flyer_primary_project_created", "project_id": "F0065", "detail": "project_id=F0065; ack_message_id=preview-mid"},
            *audits,
        ],
        now=module.parse_utc("2026-05-21T08:00:00Z"),
    )

    incident = next(item for item in report["incidents"] if item["type"] == "preview_approved_final_qa_failed")
    assert incident["evidence_details"]["active_customer_risk"] is True
