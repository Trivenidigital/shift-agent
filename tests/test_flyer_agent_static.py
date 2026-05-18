"""Static contracts for Flyer Agent SKILLs and scripts."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLYER = REPO / "src" / "agents" / "flyer"


def test_flyer_agent_files_exist():
    expected = [
        FLYER / "skills" / "flyer_dispatcher" / "SKILL.md",
        FLYER / "skills" / "flyer_intake" / "SKILL.md",
        FLYER / "skills" / "flyer_generation" / "SKILL.md",
        FLYER / "scripts" / "create-flyer-project",
        FLYER / "scripts" / "update-flyer-project",
        FLYER / "scripts" / "generate-flyer-concepts",
        FLYER / "scripts" / "finalize-flyer-assets",
        FLYER / "scripts" / "handle-flyer-onboarding",
        FLYER / "scripts" / "store-flyer-brand-asset",
        FLYER / "scripts" / "send-flyer-package",
    ]
    for path in expected:
        assert path.exists(), f"missing {path}"


def test_flyer_dispatcher_documents_state_machine_and_approval():
    skill = (FLYER / "skills" / "flyer_dispatcher" / "SKILL.md").read_text(encoding="utf-8")
    for state in [
        "intake_started",
        "collecting_required_info",
        "awaiting_assets",
        "generating_concepts",
        "awaiting_concept_selection",
        "revising_design",
        "awaiting_final_approval",
        "finalizing_assets",
        "delivered",
        "completed",
    ]:
        assert state in skill
    assert "APPROVE" in skill
    assert "bridge_send_media" in skill or "send-flyer-package" in skill


def test_flyer_generation_skill_documents_controlled_direct_generation():
    skill = (FLYER / "skills" / "flyer_generation" / "SKILL.md").read_text(encoding="utf-8")
    assert "Use controlled direct generation" in skill
    assert "structured flyer facts" in skill
    assert "complete finished" in skill and "poster" in skill
    assert "server-side compositor may still be used for deterministic fallback" in skill
    assert "Telugu" in skill
