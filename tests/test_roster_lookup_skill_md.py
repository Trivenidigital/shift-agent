"""Structural assertions on roster_lookup/SKILL.md.

Mirrors test_catering_proposal_skill_md.py: read-only content checks that the
skill keeps its required sections, points at roster.json, and preserves its
read-only / never-invent guarantees (interpretation is observability, not
unit-tested behavior).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "src" / "agents" / "shift" / "skills" / "roster_lookup" / "SKILL.md"


def test_required_sections_present():
    text = SKILL.read_text(encoding="utf-8")
    for heading in (
        "## When to invoke",
        "## Coverage-finding logic",
        "## Restrictions",
        "## Data integrity rules",
        "## Output format",
    ):
        assert heading in text, heading


def test_references_roster_json():
    text = SKILL.read_text(encoding="utf-8")
    assert "roster.json" in text
    assert "/opt/shift-agent/roster.json" in text


def test_read_only_claims_intact():
    text = SKILL.read_text(encoding="utf-8")
    # Never fabricate / never invent
    assert "Never invent" in text or "never invent" in text
    # Never mutate the file from within the skill
    assert "Never modify the file" in text
    # Load-failure must not fabricate a response
    assert "do NOT fabricate" in text
