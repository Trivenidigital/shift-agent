from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PLATFORM = SRC / "platform"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(PLATFORM))

from agents.flyer.intent_training import (  # noqa: E402
    FlyerIntentTrainingExample,
    example_from_intent_row,
    export_training_examples,
)


def _intent_row(**overrides):
    row = {
        "type": "flyer_hermes_intent_decision",
        "schema_version": 1,
        "mode": "shadow",
        "decision_source": "hermes_gateway_future",
        "classifier_status": "success",
        "message_id_hash": "msg-hash",
        "chat_key_hash": "chat-hash",
        "has_media": False,
        "validator_ok": True,
        "validator_reasons": [],
        "advisory_intent": "new_flyer",
        "advisory_action": "create_project",
        "confidence": 0.93,
        "would_mutate": True,
        "actual_route": "flyer_primary_project_created",
        "actual_reason": "project_created",
        "actual_action": "new_project",
        "route_sequence": ["flyer_primary_project_created"],
        "branch_return_reason": "cf-router flyer primary created",
        "selected_project_id": "F0065",
        "project_status": "awaiting_final_approval",
        "customer_status": "trial",
        "intake_status": "",
        "active_customer_risk": True,
        "risk_scope": "pre_project_customer_visible",
    }
    row.update(overrides)
    return row


def test_example_from_intent_row_is_flat_redacted_and_featured():
    example = example_from_intent_row(
        _intent_row(
            selected_project_id="F0065",
            actual_reason="created flyer project F0065 for Weekend Breakfast Specials",
        )
    )

    assert example is not None
    assert example["message_id_hash"] == "msg-hash"
    assert example["chat_key_hash"] == "chat-hash"
    assert example["intent"] == "new_flyer"
    assert example["action"] == "create_project"
    assert example["route_label"] == "new_project"
    assert example["outcome_label"] == "route_matched"
    assert example["input_features"]["has_media"] is False
    assert example["input_features"]["route_sequence"] == ["flyer_primary_project_created"]
    serialized = json.dumps(example)
    assert "F0065" not in serialized
    assert "Weekend Breakfast" not in serialized
    assert "customer_reply" not in serialized
    assert "actual_reason" not in serialized


def test_training_example_rejects_freeform_input_features():
    row = example_from_intent_row(_intent_row())
    assert row is not None
    row["input_features"]["raw_request"] = "Call +19045551234 at 123 Main St"

    with pytest.raises(ValidationError):
        FlyerIntentTrainingExample.model_validate(row)


def test_training_export_writes_idempotent_jsonl_with_composite_dedupe(tmp_path):
    decisions = tmp_path / "decisions.log"
    out = tmp_path / "training.jsonl"
    duplicate = _intent_row()
    stale_schema = _intent_row(schema_version=0, advisory_intent="revise_flyer")
    decisions.write_text(
        "\n".join(
            [
                json.dumps({"type": "cf_router_intercepted", "reason": "flyer_primary_project_created"}),
                json.dumps(duplicate),
                json.dumps(dict(duplicate, advisory_action="clarify")),
                json.dumps(stale_schema),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = export_training_examples(decisions_log=decisions, out_path=out)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert summary["written"] == 2
    assert len(rows) == 2
    assert {row["schema_version"] for row in rows} == {1, 0}
    assert rows[0]["dedupe_key"] != rows[1]["dedupe_key"]


def test_training_export_cli_smoke(tmp_path):
    decisions = tmp_path / "decisions.log"
    out = tmp_path / "training.jsonl"
    decisions.write_text(json.dumps(_intent_row()) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-intent-training-export"),
            "--decisions-log",
            str(decisions),
            "--out",
            str(out),
            "--format",
            "jsonl",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "written=1" in result.stdout
    assert out.exists()
