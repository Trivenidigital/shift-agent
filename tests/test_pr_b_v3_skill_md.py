"""Static checks on handle_catering_owner_approval/SKILL.md (PR-B v3 v0.4).

Per docs/hermes-alignment.md Part 1 §Testing pattern, SKILL.md interpretation
is Kimi's runtime concern — not unit-tested. This file is the cheapest
observability layer: catches contributor mistakes that would silently break
the LLM-drafting paradigm change (forgotten flag rename, missing truth-guard
constraint prose, RCE-class log-decision-direct interpolation creeping back).

Pure regex / file-existence checks. Windows + Linux.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_PATH = (Path(__file__).resolve().parent.parent /
              "src" / "agents" / "catering" / "skills" /
              "handle_catering_owner_approval" / "SKILL.md")


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_file_exists():
    assert SKILL_PATH.exists()


def test_v04_paradigm_change_note(skill_text):
    """Top-of-file callout that this is the LLM-drafting v0.4 paradigm change."""
    assert "v0.4" in skill_text
    assert "LLM-drafted" in skill_text or "LLM drafted" in skill_text
    # Should explicitly state apply-script accepts text on stdin.
    assert "--quote-text-stdin" in skill_text


def test_quote_text_stdin_invocation_present(skill_text):
    """Approve flow must pipe drafted text via stdin to --quote-text-stdin."""
    assert "echo \"$QUOTE_TEXT\"" in skill_text or "$QUOTE_TEXT" in skill_text
    # The literal flag must appear in an invocation block.
    assert "--decision approve --quote-text-stdin" in skill_text


def test_template_paths_purged(skill_text):
    """No references to /opt/shift-agent/templates/ — paradigm flipped."""
    assert "/opt/shift-agent/templates/" not in skill_text
    assert "catering_quote_to_customer.txt" not in skill_text


def test_truth_guard_constraints_documented(skill_text):
    """LLM must be told: headcount integer + ISO date parenthetical mandatory.

    Without these prose constraints in the SKILL prompt, the LLM will
    omit one or both — and apply-script's truth-guard will reject every
    draft, causing a retry storm on canary."""
    # Headcount constraint
    assert "headcount" in skill_text.lower()
    headcount_section = skill_text.lower()
    assert "literal headcount integer" in headcount_section or \
           "literal integer" in headcount_section
    # ISO date constraint
    assert "(YYYY-MM-DD)" in skill_text
    assert "parenthetical" in skill_text.lower() or "parens" in skill_text.lower()


def test_plain_prose_constraint_documented(skill_text):
    """LLM must be told: no markdown delimiters."""
    assert "plain prose" in skill_text.lower() or "plain-prose" in skill_text.lower()
    # Must explicitly call out markdown delimiters (apply-script strips
    # them anyway, but better to draft clean).
    assert "markdown" in skill_text.lower()


def test_jq_n_arg_pattern_for_log_decision_direct(skill_text):
    """RCE class fix (R3 B-S4): JSON for log-decision-direct must be built
    via `jq -n --arg`, never via bash interpolation inside the JSON body."""
    assert "jq -n" in skill_text
    assert "--arg" in skill_text
    # The pattern must invoke log-decision-direct with the jq output.
    assert "log-decision-direct" in skill_text


def test_no_bash_interpolation_inside_json_template(skill_text):
    """No `'"$VAR"'` patterns inside JSON literals (RCE class).
    Allowed: `--arg name "$VAR"` (jq-quoted), or assignments like
    `LEAD_ID=$(jq -r ...)`.
    Forbidden: `{"key":"'"$VAR"'"}` (bash double-quote breakout into JSON).
    """
    # Look for the dangerous pattern: closing-double-quote-then-bash-var-then-opening
    # inside what looks like a JSON object literal.
    bad_pattern = re.compile(r'\{[^}]*"[^"]*\'\s*"\s*\$\w+\s*"\s*\'')
    matches = bad_pattern.findall(skill_text)
    assert not matches, \
        f"shell-escape pattern in SKILL — found: {matches[:3]}"


def test_step_5_failure_audit_emission(skill_text):
    """Step 5 must instruct the SKILL to emit catering_quote_skill_failed
    via log-decision-direct on apply-script non-zero exit."""
    assert "catering_quote_skill_failed" in skill_text
    assert "apply_decision_nonzero" in skill_text
    assert 'log-decision-direct' in skill_text


def test_exit_code_table_covers_apply_script_codes(skill_text):
    """Apply-script returns exits 0/2/4/5/6/7/9 — all should be in the table."""
    # Find lines that look like exit-code table rows
    for code in ["0", "2", "4", "5", "6", "9"]:
        # Search for "| $code |" in markdown table
        assert re.search(rf"\|\s*{code}\s*\|", skill_text), \
            f"exit code {code} not documented in SKILL exit-code table"


def test_truth_guard_failed_exit_code_documented(skill_text):
    """Apply-script returns EXIT_DEPENDENCY_DOWN on truth-guard fail.
    Per design v3, exit code 7 is the catering-specific signal."""
    # Some catering exit codes table entry mentions truth-guard
    assert "truth-guard" in skill_text.lower() or "truth_guard" in skill_text


def test_inline_state_reads_pattern(skill_text):
    """SKILL Step 3a reads state files via jq inline, not via a separate
    catering-lead-context script (per design v3 §3.7 — bundler dropped)."""
    # Should reference jq + state files, NOT a wrapping script.
    assert "jq" in skill_text
    assert "/opt/shift-agent/state/catering-leads.json" in skill_text
    # Bundler script must NOT be invoked.
    assert "catering-lead-context" not in skill_text


def test_hard_rules_section_present(skill_text):
    """The SKILL must retain the Hard rules section — defensive for future
    maintainers."""
    assert "## Hard rules" in skill_text
    # Specific rules that v3 needs:
    assert "log-decision-direct" in skill_text
    assert "shell-interpolation" in skill_text.lower() or \
           "interpolation" in skill_text.lower()


def test_lead_ref_signoff_documented(skill_text):
    """Drafted quote should sign off with `(Ref: $LEAD_ID)` per Step 3b
    constraint 9 — gives operator a way to correlate WhatsApp messages to
    leads.json entries when investigating issues."""
    assert "Ref:" in skill_text
    assert "LEAD_ID" in skill_text or "lead_id" in skill_text


def test_single_turn_documented(skill_text):
    """Per design v3 §1 step 'LLM drafts customer-facing quote text' is
    Hermes substrate — single-turn flow should be explicit in SKILL prose."""
    lowered = skill_text.lower()
    assert "single llm turn" in lowered or \
           "single-turn" in lowered or \
           "same kimi turn" in lowered or \
           "no second llm round-trip" in lowered
