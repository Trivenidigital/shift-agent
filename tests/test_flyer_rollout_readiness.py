"""Unit tests for Flyer rollout-readiness primitives.

Covers Pydantic schema strictness, source-edit posture rule, and verdict
aggregation. Replay-driven scenario coverage lives in
``tests/test_flyer_rollout_replay.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from agents.flyer.rollout_readiness import (  # noqa: E402
    SEVERITY_RANK,
    RolloutInputFixture,
    RolloutMergedNotDeployed,
    RolloutOpenPR,
    RolloutReplaySummary,
    build_rollout_section,
    compute_rollout_verdict,
    compute_source_edit_posture,
    incident_color,
    load_rollout_input,
    merge_replay_summary_override,
    render_rollout_banner,
    render_rollout_section,
)


def _green_fixture(**overrides) -> RolloutInputFixture:
    payload = dict(
        deploy_marker="deploy-test",
        bridge_status="connected",
        gateway_status="active",
        cockpit_status="healthy",
        host_supplied_source_edit_posture="configured_with_smoke",
        replay_summary={"total": 11, "passed": 11, "failed_ids": []},
    )
    payload.update(overrides)
    return RolloutInputFixture.model_validate(payload)


# --------------------------------------------------------------------------- #
# Pydantic schema strictness.
# --------------------------------------------------------------------------- #


def test_input_fixture_extra_forbid_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        RolloutInputFixture.model_validate({"unknown_field": "x"})


def test_input_fixture_open_pr_extra_forbid():
    with pytest.raises(ValidationError):
        RolloutInputFixture.model_validate(
            {
                "open_prs": [{"number": 154, "weird": "y"}],
            }
        )


def test_input_fixture_default_values():
    fixture = RolloutInputFixture()
    assert fixture.deploy_marker == ""
    assert fixture.bridge_status == "unknown"
    assert fixture.gateway_status == "unknown"
    assert fixture.cockpit_status == "unknown"
    assert fixture.open_prs == []
    assert fixture.merged_not_deployed == []
    assert fixture.host_supplied_source_edit_posture == "unset"
    assert fixture.replay_summary is None


# --------------------------------------------------------------------------- #
# Source-edit posture.
# --------------------------------------------------------------------------- #


def test_source_edit_posture_all_five_states():
    pairs = (
        ("configured_with_smoke", ""),
        ("configured_with_smoke_stale", "stale"),
        ("configured_no_smoke", "spend-gated"),
        ("manual_review", "manual_review fallback"),
        ("unset", "not supplied"),
    )
    for posture, fragment in pairs:
        if posture == "unset":
            fixture = None
        else:
            fixture = _green_fixture(host_supplied_source_edit_posture=posture)
        out_posture, reason = compute_source_edit_posture(fixture)
        assert out_posture == posture
        if fragment:
            assert fragment in reason


# --------------------------------------------------------------------------- #
# Incident-color single-sourcing.
# --------------------------------------------------------------------------- #


def test_incident_color_uses_severity_rank():
    assert incident_color([]) == "green"
    assert incident_color([{"severity": "low"}]) == "green"
    assert incident_color([{"severity": "medium"}]) == "yellow"
    assert incident_color([{"severity": "high"}]) == "red"
    assert incident_color([{"severity": "critical"}, {"severity": "low"}]) == "red"


def test_verdict_uses_shared_severity_rank():
    """Future re-introduction of a parallel severity-rank should fail CI.

    flyer-self-evaluation.py must import SEVERITY_RANK from this module
    rather than re-defining it locally. The CLI-side wiring is tested in
    test_flyer_rollout_readiness_cli_smoke; this assertion guards the
    identity at the Python-object level.
    """
    spec_path = REPO / "tools" / "flyer-self-evaluation.py"
    import importlib.util as _ilu

    spec = _ilu.spec_from_file_location("_self_eval_for_rank_check", spec_path)
    module = _ilu.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.SEVERITY_RANK is SEVERITY_RANK


# --------------------------------------------------------------------------- #
# Verdict logic — synthetic walk-throughs (matches review-prompt cases).
# --------------------------------------------------------------------------- #


def test_verdict_green_when_all_clear():
    verdict, reasons = compute_rollout_verdict(
        incidents=[], fixture=_green_fixture()
    )
    assert verdict == "green"
    assert reasons == []


def test_verdict_yellow_on_bridge_unknown():
    fixture = _green_fixture(bridge_status="unknown")
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "yellow"
    assert any("bridge status posture unknown" in r["text"] for r in reasons)


def test_verdict_yellow_when_replay_summary_missing():
    fixture = _green_fixture()
    # Force replay_summary to None
    payload = fixture.model_dump()
    payload["replay_summary"] = None
    fixture = RolloutInputFixture.model_validate(payload)
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "yellow"
    assert any("replay summary not supplied" in r["text"] for r in reasons)


def test_verdict_yellow_on_unset_source_edit_policy():
    fixture = _green_fixture(host_supplied_source_edit_posture="unset")
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "yellow"
    assert any("source-edit policy posture not supplied" in r["text"] for r in reasons)


def test_verdict_yellow_on_configured_no_smoke():
    fixture = _green_fixture(host_supplied_source_edit_posture="configured_no_smoke")
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "yellow"
    assert any("spend-gated" in r["text"] for r in reasons)


def test_verdict_yellow_on_merged_not_deployed_low_severity():
    fixture = _green_fixture(
        merged_not_deployed=[{"number": 159, "title": "x", "customer_risk_label": "none"}]
    )
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "yellow"
    assert any("merged-not-deployed" in r["text"] for r in reasons)


def test_verdict_red_on_merged_not_deployed_customer_routing_label():
    fixture = _green_fixture(
        merged_not_deployed=[
            {"number": 158, "title": "brief builder", "customer_risk_label": "lifecycle"}
        ]
    )
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "red"
    assert any("customer-risk PRs" in r["text"] for r in reasons)


def test_verdict_red_on_disconnected_bridge():
    fixture = _green_fixture(bridge_status="disconnected")
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "red"
    assert any("bridge is disconnected" in r["text"] for r in reasons)


def test_verdict_red_on_replay_failed():
    fixture = _green_fixture(
        replay_summary={
            "total": 11,
            "passed": 10,
            "failed_ids": ["rollout-text-request-intelligent-brief-approves-into-project"],
        }
    )
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=fixture)
    assert verdict == "red"
    assert any("rollout replay failed" in r["text"] for r in reasons)


def test_verdict_red_on_customer_copy_leak_active_risk():
    incidents = [
        {
            "type": "customer_copy_internal_leak",
            "severity": "high",
            "evidence_details": {"active_customer_risk": True},
        }
    ]
    verdict, reasons = compute_rollout_verdict(
        incidents=incidents, fixture=_green_fixture()
    )
    assert verdict == "red"
    assert any("customer-copy internal leak" in r["text"] for r in reasons)


def test_verdict_red_on_manual_source_edit_stale_at_30_min():
    incidents = [
        {
            "type": "manual_source_edit_stale",
            "severity": "high",
            "evidence_details": {"queued_age_minutes": 30.0, "active_customer_risk": True},
        }
    ]
    verdict, reasons = compute_rollout_verdict(
        incidents=incidents, fixture=_green_fixture()
    )
    assert verdict == "red"


def test_verdict_yellow_on_manual_source_edit_stale_at_29_min():
    incidents = [
        {
            "type": "manual_source_edit_stale",
            "severity": "medium",  # below high so severity_rank doesn't force red
            "evidence_details": {"queued_age_minutes": 29.0},
        }
    ]
    verdict, reasons = compute_rollout_verdict(
        incidents=incidents, fixture=_green_fixture()
    )
    assert verdict == "yellow"


def test_verdict_red_reasons_appear_before_yellow():
    incidents = [
        {
            "type": "customer_copy_internal_leak",
            "severity": "high",
            "evidence_details": {"active_customer_risk": True},
        }
    ]
    fixture = _green_fixture(host_supplied_source_edit_posture="manual_review")
    verdict, reasons = compute_rollout_verdict(incidents=incidents, fixture=fixture)
    assert verdict == "red"
    severities = [r["severity"] for r in reasons]
    # All red reasons appear before any yellow
    first_yellow_idx = next((i for i, s in enumerate(severities) if s == "yellow"), len(severities))
    assert all(s == "red" for s in severities[:first_yellow_idx])


def test_verdict_yellow_when_fixture_missing():
    verdict, reasons = compute_rollout_verdict(incidents=[], fixture=None)
    assert verdict == "yellow"
    assert any("readiness input fixture not supplied" in r["text"] for r in reasons)


# --------------------------------------------------------------------------- #
# Section builder + Markdown render.
# --------------------------------------------------------------------------- #


def test_build_rollout_section_shape():
    section = build_rollout_section(incidents=[], fixture=_green_fixture())
    assert section["verdict"] == "green"
    assert section["reasons"] == []
    assert section["source_edit_posture"] == "configured_with_smoke"
    assert section["source_edit_posture_reason"] == ""
    assert section["bridge_status"] == "connected"
    assert section["replay_summary"]["passed"] == 11
    assert section["replay_summary"]["total"] == 11


def test_render_rollout_banner_green_drops_reasons_suffix():
    section = build_rollout_section(incidents=[], fixture=_green_fixture())
    assert render_rollout_banner(section) == "**Rollout: GREEN**"


def test_render_rollout_banner_red_with_reasons_count():
    section = build_rollout_section(
        incidents=[],
        fixture=_green_fixture(bridge_status="disconnected"),
    )
    banner = render_rollout_banner(section)
    assert banner.startswith("**Rollout: RED")
    assert "reason" in banner


def test_render_rollout_section_includes_posture_reason():
    section = build_rollout_section(
        incidents=[],
        fixture=_green_fixture(host_supplied_source_edit_posture="manual_review"),
    )
    lines = render_rollout_section(section)
    posture_line = next(line for line in lines if line.startswith("- Source-edit posture:"))
    assert "manual_review" in posture_line
    assert "manual_review fallback" in posture_line


def test_render_rollout_section_no_reasons_emits_none():
    section = build_rollout_section(incidents=[], fixture=_green_fixture())
    lines = render_rollout_section(section)
    reasons_idx = lines.index("### Reasons (RED first)")
    assert lines[reasons_idx + 2] == "- None."


# --------------------------------------------------------------------------- #
# File loaders.
# --------------------------------------------------------------------------- #


def test_load_rollout_input_none_returns_none():
    assert load_rollout_input(None) is None
    assert load_rollout_input("") is None


def test_load_rollout_input_parses_fixture(tmp_path: Path):
    payload = _green_fixture().model_dump(mode="json")
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    fixture = load_rollout_input(str(path))
    assert fixture is not None
    assert fixture.bridge_status == "connected"
    assert fixture.replay_summary is not None
    assert fixture.replay_summary.passed == 11


def test_merge_replay_summary_override_replaces_only_summary(tmp_path: Path):
    fixture = _green_fixture()
    path = tmp_path / "summary.json"
    path.write_text(
        json.dumps(
            {"total": 11, "passed": 9, "failed_ids": ["a", "b"]}
        ),
        encoding="utf-8",
    )
    merged = merge_replay_summary_override(fixture, str(path))
    assert merged is not None
    assert merged.replay_summary is not None
    assert merged.replay_summary.failed_ids == ["a", "b"]
    assert merged.bridge_status == "connected"  # Untouched


def test_merge_replay_summary_override_handles_none_fixture(tmp_path: Path):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"total": 1, "passed": 1, "failed_ids": []}), encoding="utf-8")
    merged = merge_replay_summary_override(None, str(path))
    assert merged is not None
    assert merged.replay_summary is not None
    assert merged.replay_summary.total == 1
