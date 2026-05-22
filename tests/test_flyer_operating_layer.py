from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.flyer.operating_layer import (  # noqa: E402
    BACKLOG_KEYS,
    OperatingLayerReadinessInput,
    build_operating_layer_section,
)


READY_FIXTURE = REPO / "tests" / "fixtures" / "flyer_operating_layer" / "ready.json"
PARTIAL_FIXTURE = REPO / "tests" / "fixtures" / "flyer_operating_layer" / "partial.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_brand_memory_ready_for_at_least_one_customer():
    section = build_operating_layer_section(_load(READY_FIXTURE))

    assert section["brand_memory"]["status"] == "ready_for_at_least_one_customer"
    assert section["brand_memory"]["ready_customer_count"] == 1
    assert section["brand_memory"]["total_customer_count"] == 1
    assert section["brand_memory"]["coverage_ratio"] == 1.0
    assert section["campaign_history"]["completed_campaign_count"] == 1


def test_missing_asset_campaign_or_qa_keeps_brand_memory_yellow():
    missing_asset = build_operating_layer_section(_load(PARTIAL_FIXTURE))
    assert missing_asset["brand_memory"]["status"] == "yellow"
    assert any("active brand asset" in reason for reason in missing_asset["brand_memory"]["reasons"])

    payload = _load(READY_FIXTURE)
    payload["campaigns"][0]["qa_checked_at"] = None
    missing_qa_timestamp = build_operating_layer_section(payload)
    assert missing_qa_timestamp["brand_memory"]["status"] == "yellow"
    assert any("QA timestamp" in reason for reason in missing_qa_timestamp["brand_memory"]["reasons"])


def test_rollout_input_used_when_self_eval_rollout_absent_and_conflicts_are_conservative():
    from_input = build_operating_layer_section(_load(READY_FIXTURE), rollout=None)
    assert from_input["source_edit"]["status"] == "deferred"
    assert "manual_review" in from_input["source_edit"]["reason"]

    conflicting_rollout = {
        "verdict": "green",
        "source_edit_posture": "configured_with_smoke",
        "reasons": [],
    }
    conflict = build_operating_layer_section(_load(READY_FIXTURE), rollout=conflicting_rollout)
    assert conflict["source_edit"]["status"] == "deferred"
    assert any("conflict" in reason.lower() for reason in conflict["rollout_guard"]["reasons"])


def test_platform_truthfulness_false_blocks_multiformat_export_claims():
    section = build_operating_layer_section(_load(READY_FIXTURE))

    export_item = next(item for item in section["deferred_backlog"] if item["key"] == "multi_format_export_truthfulness")
    assert export_item["status"] == "blocked"
    assert "Instagram story" in export_item["guardrail"]


def test_deferred_backlog_keys_cover_every_hermes_update_option():
    section = build_operating_layer_section(_load(READY_FIXTURE))
    keys = {item["key"] for item in section["deferred_backlog"]}

    assert keys == set(BACKLOG_KEYS)
    assert "persistent_brand_memory_activation" in keys
    assert "native_video_conversion" in keys
    assert "x_social_posting_approval" in keys
    assert "auto_kanban_operator_work" in keys


def test_fixture_schema_is_strict_and_rejects_unknown_or_negative_values():
    payload = _load(READY_FIXTURE)
    payload["extra"] = "nope"
    with pytest.raises(ValidationError):
        OperatingLayerReadinessInput.model_validate(payload)

    payload = _load(READY_FIXTURE)
    payload["customers"][0]["active_brand_assets"] = -1
    with pytest.raises(ValidationError):
        OperatingLayerReadinessInput.model_validate(payload)


def test_helper_static_guard_no_live_probe_or_mutation_paths():
    source = (REPO / "src" / "agents" / "flyer" / "operating_layer.py").read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "requests",
        "urllib",
        "socket",
        "ssh",
        "/opt/shift-agent",
        "/root/.hermes",
        "write_text",
        "atomic_write",
        "ndjson_append",
        "open(",
    )
    assert not [token for token in forbidden if token in source]


def test_self_evaluation_cli_injects_operating_layer_json(tmp_path):
    projects = tmp_path / "projects.json"
    decisions = tmp_path / "decisions.log"
    projects.write_text('{"projects":[]}\n', encoding="utf-8")
    decisions.write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "tools/flyer-self-evaluation.py",
            "--projects",
            str(projects),
            "--decisions-log",
            str(decisions),
            "--operating-layer-input",
            str(READY_FIXTURE),
            "--format",
            "json",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)

    assert payload["operating_layer"]["brand_memory"]["status"] == "ready_for_at_least_one_customer"
    assert payload["operating_layer"]["next_action"]["key"] == "source_edit_smoke_proof"
