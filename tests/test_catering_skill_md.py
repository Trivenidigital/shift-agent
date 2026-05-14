"""Static checks on parse_catering_inquiry/SKILL.md — observability layer for
SKILL prompt correctness.

SKILL.md is interpreted by Kimi at runtime; per docs/hermes-alignment.md
Part 1 §Testing pattern, SKILL interpretation gets observability + manual
smoke (no unit-test). This file is the cheapest observability layer: it
guards against the most common contributor mistake — renaming the lookup
script in code without updating the SKILL prompt that invokes it, OR adding
a new LOOKUP_STATUS_* without documenting it in the SKILL's branching table.

Pure regex / file-existence checks. Runs on Windows + Linux.
"""
from __future__ import annotations
import re
from pathlib import Path

SKILL_PATH = (Path(__file__).resolve().parent.parent /
              "src" / "agents" / "catering" / "skills" /
              "parse_catering_inquiry" / "SKILL.md")
UPDATE_MENU_SKILL = (Path(__file__).resolve().parent.parent /
                     "src" / "agents" / "catering" / "skills" /
                     "update_catering_menu" / "SKILL.md")
LOOKUP_SCRIPT = (Path(__file__).resolve().parent.parent /
                 "src" / "agents" / "catering" / "scripts" /
                 "lookup-prior-leads-by-phone")


def test_skill_invokes_lookup_script_by_canonical_path():
    """SKILL must invoke lookup-prior-leads-by-phone via the deployed bin path."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "/usr/local/bin/lookup-prior-leads-by-phone --customer-phone" in text, (
        "SKILL prompt must contain the literal subprocess invocation"
    )


def test_lookup_script_referenced_in_skill_actually_exists_in_repo():
    assert LOOKUP_SCRIPT.exists(), (
        f"SKILL references {LOOKUP_SCRIPT.name} but file not found at {LOOKUP_SCRIPT}"
    )


def test_skill_handles_all_lookup_statuses():
    """SKILL prompt must branch on every lookup_status the script can emit.

    Robustness: also asserts ≥6 LOOKUP_STATUS_* constants exist (the deployed
    count). If a contributor refactors to an Enum / alias / f-string and the
    regex misses some, the count assertion fails LOUDLY rather than the SKILL
    silently dropping a status row.
    """
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    script_text = LOOKUP_SCRIPT.read_text(encoding="utf-8")
    statuses = re.findall(r'LOOKUP_STATUS_\w+\s*=\s*"([^"]+)"', script_text)
    assert len(statuses) >= 6, (
        f"expected ≥6 LOOKUP_STATUS_* constants in {LOOKUP_SCRIPT.name}, "
        f"found {statuses}. If a refactor changed the constant style, update "
        f"the regex; do not rubber-stamp."
    )
    for s in statuses:
        assert s in skill_text, (
            f"SKILL.md missing branch for lookup_status={s!r}; the script "
            f"emits it but Kimi has no instruction for it"
        )


def test_skill_has_default_unparseable_output_row():
    """A new status the script grows without doc'ing in SKILL.md would fail the
    test above. But for runtime robustness — script crashes, returns non-zero,
    emits non-JSON — the SKILL must have a default-row instructing Kimi to
    fall through to standard new-inquiry flow. Per design-review R4 finding."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    # Heuristic: look for "any other" / "unparseable" / "stdout not parseable"
    # AND "do not retry" or similar. Either of the two phrasings is enough.
    assert "any other status" in text.lower() or "stdout not parseable" in text.lower(), (
        "SKILL Step 0 must document a default branch for unparseable output / "
        "unexpected exit. Otherwise Kimi has no instruction for the case where "
        "the script crashes or emits non-JSON."
    )


def test_skill_frontmatter_preserved():
    """Frontmatter (first 3 lines) must remain valid YAML."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---", lines[0]
    assert lines[1].startswith("name: "), lines[1]
    assert lines[2].startswith("description: "), lines[2]


def test_skill_step_0_has_must_language():
    """Reviewer R4 finding: Step 0 needs MUST-level instruction. Without it,
    Kimi may treat the preamble as decorative context."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    # Step 0 section must contain explicit hard-rule framing
    step_0_block = text.split("## Step 0")[1].split("## Step 1")[0] if "## Step 0" in text else ""
    assert step_0_block, "Step 0 section missing entirely"
    assert "Hard rule" in step_0_block or "MUST" in step_0_block.upper(), (
        "Step 0 should explicitly mark the lookup call as required (Hard rule "
        "or MUST language) — soft framing risks Kimi skipping the preamble"
    )


def test_skill_does_not_leak_prior_records_in_acknowledgment():
    """Reviewer R4 privacy finding: phones in this market segment are often
    shared between household members. The SKILL must NOT instruct Kimi to
    differentiate the customer-facing acknowledgment based on lookup_status —
    'good to hear from you again' is a continuity-of-identity assertion that
    can mis-fire on shared phones.
    """
    text = SKILL_PATH.read_text(encoding="utf-8")
    # Specific phrase that the prior plan/design draft included; v2 design
    # explicitly drops it. Pin the absence so future edits don't re-introduce.
    assert "good to hear from you again" not in text.lower(), (
        "Differential acknowledgment based on prior records is a privacy "
        "hazard on shared phones. Keep the standard ack regardless of "
        "lookup_status."
    )


def test_skill_documents_sender_phone_provenance():
    """Reviewer R4 substitution finding: SKILL must document where
    sender_phone comes from so future contributors don't re-introduce a
    path where the LLM constructs phone from message body."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "sender_phone" in text and (
        "validate-sender-block" in text or "E.164" in text or "VERBATIM" in text
    ), (
        "SKILL should document sender_phone provenance (validated by Hermes' "
        "validate-sender-block; use VERBATIM) so contributors can't drift to "
        "deriving phone from message body"
    )


def test_update_menu_skill_distinguishes_source_submitter_from_owner_apply():
    """Dispatcher allows owner OR employee menu-source upload. The SKILL must
    not contradict that, but applying the extracted menu must stay owner-only."""
    text = UPDATE_MENU_SKILL.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "owner or verified employee" in lowered
    assert "only the owner can apply" in lowered
    assert "never apply the menu without the owner's explicit yes" in lowered
    assert "only the owner can update the menu" not in lowered
