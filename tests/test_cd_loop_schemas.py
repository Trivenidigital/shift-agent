"""CD Loop — Slice 0 DORMANT schema tests.

Slice 0 adds standalone Pydantic models for the future Creative Director Loop
(plan: docs/superpowers/plans/2026-06-29-flyer-creative-director-loop.md). They
are DORMANT: no production code constructs/reads/writes them, no existing model
(FlyerProject, FlyerProjectStore, LogEntry) is modified, no LogEntry variant is
added, no model calls, no config. These tests prove the schemas exist + validate
AND that nothing about existing output behavior changed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from schemas import (
    FlyerDesignContract,
    FlyerLoopAcceptanceCriteria,
    FlyerLoopCreativeDirection,
    FlyerLoopEvaluation,
    FlyerLoopIteration,
    FlyerLoopProjectTrace,
    FlyerLoopTraceStore,
    FlyerProject,
    FlyerProjectStore,
)

_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)

# The full set of CD Loop class names added in Slice 0 — used by the dormancy scan.
_CD_LOOP_NAMES = [
    "FlyerDesignContract", "FlyerLoopAcceptanceCriteria", "FlyerLoopCreativeDirection",
    "FlyerLoopEvaluation", "FlyerLoopIteration", "FlyerLoopProjectTrace",
    "FlyerLoopTraceStore",
]


# ── the dormant models exist + validate + round-trip ────────────────────────

def test_design_contract_constructs_and_round_trips():
    c = FlyerDesignContract(
        contract_id="DC-F0190-1", project_id="F0190", source="intake",
        required_visible_facts=["business_name", "pricing_structure"],
        creative_direction=FlyerLoopCreativeDirection(
            hero_ref="item:0", marketing_hook="shared_price",
            campaign_narrative="$7.99 for anything on the menu.",
            palette_intent="high_contrast_warm"),
        channel_qr_map={"whatsapp": "qr:order"},
        acceptance_criteria=FlyerLoopAcceptanceCriteria(
            factual_gate=["no_fabricated_price"], quality_min={"contrast_legibility": 6}),
        rubric_version="cca-loop-v1", created_at=_NOW,
    )
    assert FlyerDesignContract.model_validate_json(c.model_dump_json()) == c


def test_loop_evaluation_constructs_and_round_trips():
    e = FlyerLoopEvaluation(
        factual_verdict="pass", factual_blockers=[],
        quality_scores={"professional_polish": 7, "contrast_legibility": 8},
        quality_verdict="pass", rubric_version="cca-loop-v1", evaluated_at=_NOW)
    assert FlyerLoopEvaluation.model_validate_json(e.model_dump_json()) == e


def test_loop_iteration_and_trace_round_trip():
    it = FlyerLoopIteration(
        iteration=1, contract_id="DC-F0190-1", contract_version=1,
        generation_params={"render_path": "deterministic_overlay"},
        evaluation=FlyerLoopEvaluation(evaluated_at=_NOW),
        repair_action="none", outcome="approved", created_at=_NOW)
    trace = FlyerLoopProjectTrace(project_id="F0190", iterations=[it])
    store = FlyerLoopTraceStore(traces=[trace])
    assert FlyerLoopTraceStore.model_validate_json(store.model_dump_json()) == store
    assert store.schema_version == 1


def test_loop_models_are_extra_forbid():
    # Mirrors the flyer-model convention; an unknown key must be rejected.
    with pytest.raises(Exception):
        FlyerDesignContract.model_validate(
            {"contract_id": "x", "project_id": "F0001", "created_at": _NOW, "bogus": 1})


# ── DORMANCY / behaviour-preservation proofs ────────────────────────────────

def test_flyer_project_schema_is_unchanged_no_loop_fields():
    # The central FlyerProject model must NOT have gained any CD Loop field — its
    # serialized shape is byte-identical to before this slice.
    for forbidden in ("design_contract", "loop_iterations", "loop_trace",
                      "loop_state", "loop_evaluation"):
        assert forbidden not in FlyerProject.model_fields
    # FlyerProjectStore likewise untouched.
    assert set(FlyerProjectStore.model_fields) == {"schema_version", "next_sequence", "projects"}


def test_logentry_union_not_touched_flyer_loop_types_passthrough():
    # Slice 0 must NOT register any flyer_loop_* LogEntry variant (that is the
    # emission slice). A row with such a type must downgrade to the forward-compat
    # _UnknownLogEntry passthrough — proving the audit chokepoint union is untouched.
    from pydantic import TypeAdapter
    from schemas import LogEntry, _UnknownLogEntry
    row = {"type": "flyer_loop_iteration_recorded", "ts": _NOW.isoformat(),
           "project_id": "F0190"}
    parsed = TypeAdapter(LogEntry).validate_python(row)
    assert isinstance(parsed, _UnknownLogEntry)


def test_cd_loop_models_are_not_referenced_by_production_code():
    # Dormant: no production module under src/ (other than the schema definitions
    # in schemas.py) may import or construct the CD Loop models in Slice 0.
    repo = Path(__file__).resolve().parent.parent
    src = repo / "src"
    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "schemas.py":
            continue  # the definitions live here
        text = py.read_text(encoding="utf-8", errors="replace")
        for name in _CD_LOOP_NAMES:
            if name in text:
                offenders.append(f"{py.relative_to(repo)}: {name}")
    # also scan extensionless agent scripts
    for scripts_dir in src.rglob("scripts"):
        if not scripts_dir.is_dir():
            continue
        for f in scripts_dir.iterdir():
            if f.is_file() and f.suffix == "":
                text = f.read_text(encoding="utf-8", errors="replace")
                for name in _CD_LOOP_NAMES:
                    if name in text:
                        offenders.append(f"{f.relative_to(repo)}: {name}")
    assert offenders == [], f"CD Loop models referenced by production code: {offenders}"
