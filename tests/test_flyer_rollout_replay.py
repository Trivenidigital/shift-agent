"""Deterministic rollout-replay scenarios for the customer-readiness gate.

These 11 scenarios cover the customer rollout paths the readiness gate
must protect:

 1.  active/trial sample idea -> brief preview -> approve -> project
 2.  new trial chooses sample before onboarding -> onboarding -> compact ideas
 3.  text request -> intelligent brief -> approve -> project
 4.  guided flow -> brief -> approve
 5.  visible text removal like duplicated HH:MM time stays revision (PR #157)
 6.  LID-only sender taps Start Free Trial -> onboarding (no phone resolution)
 7.  duplicate-phone second sender recognized as authorized requester
 8.  vague "create flyer" -> concierge choice, not blank project  (cross-ref)
 9.  small revision like "make it red" stays revision  (cross-ref)
 10. source edit / co-owner path stays manual-review / provider-gated (cross-ref)
 11. status check does not create / revise a project  (cross-ref)

Cross-ref ids are loaded from tests/fixtures/flyer_incident_replay/flyer_incidents.json
and re-run through the same harness; their assertions are identical to the
incident-replay test, plus the rollout-specific raw_request_echo guard for
fixtures whose raw text is long enough to materialise an echo (>= 8 chars
after normalize_for_copy_policy, matching customer_copy_policy.py:109).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
INCIDENT_FIXTURE = REPO / "tests" / "fixtures" / "flyer_incident_replay" / "flyer_incidents.json"
ROLLOUT_FIXTURE = REPO / "tests" / "fixtures" / "flyer_rollout_replay" / "flyer_rollout_paths.json"

sys.path.insert(0, str(SRC))
from agents.flyer.customer_copy_policy import (  # noqa: E402
    normalize_for_copy_policy,
    scan_customer_text,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _flyer_replay_helpers import (  # noqa: E402
    assert_expected_route,
    build_event,
    install_common_replay_mocks,
)


INCIDENT_REPLAY_CROSS_REFS = (
    "vague-create-flyer-enters-concierge-without-project",
    "small-revision-make-it-red-stays-revision",
    "F0063-source-choice-queues-manual-edit",
    "status-check-does-not-create-or-revise",
)


def _net_new_fixtures() -> list[dict]:
    return json.loads(ROLLOUT_FIXTURE.read_text(encoding="utf-8"))


def _cross_ref_fixtures() -> list[dict]:
    by_id = {
        item["id"]: item
        for item in json.loads(INCIDENT_FIXTURE.read_text(encoding="utf-8"))
    }
    return [by_id[fid] for fid in INCIDENT_REPLAY_CROSS_REFS]


def _all_fixtures() -> list[dict]:
    return _net_new_fixtures() + _cross_ref_fixtures()


def test_rollout_replay_fixture_set_is_at_least_eight():
    fixtures = _all_fixtures()
    ids = [item["id"] for item in fixtures]
    assert len(fixtures) >= 8
    assert len(ids) == len(set(ids))


def test_rollout_replay_includes_all_task_listed_scenarios():
    fixtures = _all_fixtures()
    ids = {item["id"] for item in fixtures}
    required = {
        "rollout-active-trial-sample-idea-approves-into-project",
        "rollout-new-trial-sample-before-onboarding-into-compact-ideas",
        "rollout-text-request-intelligent-brief-approves-into-project",
        "rollout-guided-flow-brief-approves",
        "rollout-visible-text-removal-stays-revision",
        "rollout-lid-only-start-free-trial-into-onboarding",
        "rollout-duplicate-phone-second-sender-recognized-as-authorized-requester",
        "vague-create-flyer-enters-concierge-without-project",
        "small-revision-make-it-red-stays-revision",
        "F0063-source-choice-queues-manual-edit",
        "status-check-does-not-create-or-revise",
    }
    assert required.issubset(ids), required - ids


def _install_intercept_override(monkeypatch, hooks, fixture):
    """Re-enable a specific cf-router intercept for the fixture under test.

    install_common_replay_mocks neutralises all three intercepts (intake,
    onboarding, account) so the harness reaches normal routing. Rollout
    scenarios that exercise PR #158 brief-builder, LID-only onboarding,
    or duplicate-phone recognition require the corresponding intercept
    to consume the message. The fixture's intercept_result is the
    dispatch dict returned by cf-router.
    """
    target = fixture.get("intercept")
    if not target:
        return
    result = fixture["intercept_result"]
    reply_text = fixture.get("intercept_reply_text")

    if target == "intake_with_reply":
        # Intake intercept that ALSO produces an outbound message before
        # returning the dispatch dict. Used by the fixture that exercises
        # the raw_request_echo guard against real outbound text (the
        # other intake/onboarding/account fixtures produce empty `sent`,
        # which is honest but means the echo guard runs on []).
        def intercept_with_reply(*_args, **_kwargs):
            sent_list = fixture.get("_sent_recorder")
            if sent_list is not None:
                sent_list.append(reply_text)
            return result

        monkeypatch.setattr(hooks, "_try_flyer_intake_intercept", intercept_with_reply)
        return

    attr_by_target = {
        "intake": "_try_flyer_intake_intercept",
        "onboarding": "_try_flyer_existing_onboarding_intercept",
        "account": "_try_flyer_account_intercept",
    }
    attr = attr_by_target[target]
    monkeypatch.setattr(hooks, attr, lambda *_args, **_kwargs: result)


@pytest.mark.parametrize("fixture", _all_fixtures(), ids=lambda item: item["id"])
def test_rollout_replay_fixture(fixture, tmp_path, monkeypatch):
    hooks, actions, calls, audits, sent, identity_calls = install_common_replay_mocks(
        monkeypatch, tmp_path, fixture
    )

    def fake_create(**kwargs):
        calls.append("trigger_create_flyer_project")
        project = {
            "project_id": "F9001",
            "status": "intake_started",
            "fields": {
                "event_or_business_name": "Replay Campaign",
                "contact_info": (fixture.get("resolved_identity") or {}).get("phone") or "",
            },
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

    monkeypatch.setattr(actions, "invoke_update_flyer_project", fake_update)
    monkeypatch.setattr(actions, "trigger_generate_flyer_concepts", lambda *_a, **_kw: calls.append("trigger_generate_flyer_concepts") or (False, "visual_qa_failed"))
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_a, **_kw: calls.append("finalize_and_send_flyer") or (False, "visual_qa_failed"))
    monkeypatch.setattr(actions, "flyer_source_edit_preflight", lambda *_a, **_kw: (False, "provider unavailable", "source_edit_provider_unavailable"))

    mode = fixture.get("mode")
    if mode == "reference_scope_choice":
        monkeypatch.setattr(actions, "consume_flyer_reference_scope_choice", lambda *_a, **_kw: fixture["pending"])
    elif mode == "source_new_status_check":
        monkeypatch.setattr(actions, "parse_source_vs_new_followup", lambda _text: ("", ""))
        monkeypatch.setattr(actions, "peek_flyer_source_vs_new_pending", lambda **_kw: fixture["pending"])
    elif mode == "source_choice":
        monkeypatch.setattr(actions, "parse_source_vs_new_followup", lambda _text: ("source", ""))
        monkeypatch.setattr(actions, "consume_flyer_source_vs_new_choice", lambda *_a, **_kw: fixture["pending"])
        monkeypatch.setattr(actions, "flyer_project_has_manual_review_queued", lambda _p: True)

    # Allow the intake_with_reply variant to record its outbound message
    # into the same `sent` list the harness uses, so the echo guard below
    # sees it.
    fixture_local = dict(fixture)
    fixture_local["_sent_recorder"] = sent
    _install_intercept_override(monkeypatch, hooks, fixture_local)

    result = hooks.pre_gateway_dispatch(build_event(fixture))

    # When an intercept consumes the message early in pre_gateway_dispatch,
    # identity resolution is short-circuited and identity_calls stays empty.
    # For non-intercept-consumed routes the cf-router must always call
    # lid_to_phone_via_identify_sender first.
    expect = fixture["expect"]
    if expect.get("route") not in {"intake_consumed", "onboarding", "account_intercept"}:
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

    # Existing 7-token customer-copy guard.
    for text in sent:
        assert not scan_customer_text(text, raw_request=fixture["text"]).hits, text

    # Explicit raw_request_echo guard for long-text fixtures only. The outer
    # loop must bind `text` BEFORE scan_customer_text(text, ...) is called;
    # the inverted loop order silently passes by re-using a leaked variable
    # (caught by design-review C1).
    if len(normalize_for_copy_policy(fixture["text"])) >= 8:
        echo_hits = [
            hit
            for text in sent
            for hit in scan_customer_text(text, raw_request=fixture["text"]).hits
            if hit.category == "raw_request_echo"
        ]
        assert echo_hits == [], (echo_hits, sent)
