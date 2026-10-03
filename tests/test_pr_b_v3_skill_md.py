"""Static checks on handle_catering_owner_approval/SKILL.md (PR-B v3 v0.4).

Per docs/hermes-alignment.md Part 1 §Testing pattern, SKILL.md interpretation
is Kimi's runtime concern — not unit-tested. This file is the cheapest
observability layer: catches contributor mistakes that would silently break
the canonical quote contract (missing price/authorization gates, draft text
transport, RCE-class log-decision-direct interpolation creeping back).

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


def test_canonical_money_authority_documented(skill_text):
    """Customer money comes from committed inputs or a validated discount."""
    assert "frozen pricing inputs" in skill_text
    assert "validated discount computation" in skill_text
    assert "Models do not supply monetary prose" in skill_text
    assert "real commercial pricebook" in skill_text
    assert "deliverable pricing provenance" in skill_text



def test_approve_invocations_use_committed_quote_and_authenticated_role(skill_text):
    """Both ordinary approval and the explicit finalize override keep gates."""
    blocks = re.findall(r"```bash\n(.*?)```", skill_text, re.DOTALL)
    approvals = [block for block in blocks if "--decision approve" in block]
    assert len(approvals) == 2
    for block in approvals:
        assert "--decision approve --quote-from-lead-state" in block
        assert '--code "$CODE"' in block
        assert '--sender-role "<owner|employee|customer|unknown from sender block>"' in block
        assert "RC=$?" in block
        assert "--quote-text-stdin" not in block
    assert "role resolved by `identify-sender`" in skill_text
    assert "exit 12 (privilege denied)" in skill_text
    assert "override never bypasses pricebook/provenance gates" in skill_text



def test_template_paths_purged(skill_text):
    """No references to /opt/shift-agent/templates/ — paradigm flipped."""
    assert "/opt/shift-agent/templates/" not in skill_text
    assert "catering_quote_to_customer.txt" not in skill_text


def test_legacy_draft_checks_and_canonical_delivery_documented(skill_text):
    """Legacy callers retain sanity checks without controlling quote money."""
    assert "--quote-text-stdin" in skill_text
    assert "checks draft size, date and headcount" in skill_text
    assert "Its prose is not forwarded" in skill_text
    assert "same canonical quote is delivered" in skill_text



def test_complete_cents_and_quote_fields_preserved(skill_text):
    """The canonical quote keeps every money field and refuses oversize text."""
    normalized = " ".join(skill_text.split())
    assert "exact cents, package and item prices, the total, headcount, event date and validity deadline" in normalized
    assert "never truncates the committed quote at the legacy 600-character draft cap" in normalized
    assert "stored text limit are refused before state mutation or sending" in normalized
    assert "never use it to compose a customer price" in normalized



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


def test_lead_and_approval_code_audit_correlation_preserved(skill_text):
    """Correlation belongs to the existing state writer and structured audit."""
    assert '--code "$CODE"' in skill_text
    assert '--arg lead_id "$LEAD_ID"' in skill_text
    assert '--arg code "$CODE"' in skill_text
    assert "lead_id:$lead_id" in skill_text
    assert "code:$code" in skill_text



def test_single_existing_quote_delivery_path_documented(skill_text):
    normalized = " ".join(skill_text.split())
    assert "call the existing state writer" in normalized
    assert "Never send a separate customer quote" in normalized
    assert "NEVER send the quote directly from this SKILL" in normalized
    assert "`_bridge_post` is the only path" in normalized



# ──────── Review fixes ────────


def test_review_fix_b2_decision_var_assigned_in_step_2(skill_text):
    """Review BLOCKER B2: $DECISION must be explicitly assigned in shell so
    Step 5's audit-emission conditional has the var bound. Pre-fix, the
    var was never assigned and Step 5 was dead code."""
    # Look for `DECISION=approve` (the assignment, not just the comparison)
    import re
    assert re.search(r"DECISION=approve\b", skill_text), \
        "Regression of review BLOCKER B2: SKILL.md must assign DECISION=approve in shell"


def test_review_fix_h2_execution_order_narration_correct(skill_text):
    """Review HIGH-2: Step 4's numbered list must reflect actual apply-script
    execution order — read+normalize+truth-guard FIRST (before any state
    persistence), then atomic state write, then bridge POST.

    The pre-fix narration claimed transition happened first, which was
    wrong (and would mislead operators when truth-guard failed mid-flow)."""
    # Look for explicit phrase about the lead staying at AWAITING_OWNER_APPROVAL
    # if truth-guard fails — that's the operationally important guarantee
    # the narration must surface.
    assert "AWAITING_OWNER_APPROVAL" in skill_text
    # And specifically: must say BEFORE persisting any state change
    assert "before persisting" in skill_text.lower() or \
           "BEFORE persisting" in skill_text or \
           "stays at `AWAITING_OWNER_APPROVAL`" in skill_text


def test_review_fix_h4_lead_id_fallback(skill_text):
    """Review HIGH-4: LEAD_ID must have a fallback for the empty-LEAD_JSON
    branch so Step 5's audit emission has a non-empty value (Pydantic
    min_length=1 on the variant's lead_id field)."""
    # Look for the default assignment before the LEAD_JSON conditional
    assert "LEAD_ID=UNKNOWN" in skill_text or \
           'LEAD_ID="UNKNOWN"' in skill_text


def test_no_model_draft_transport_to_approval(skill_text):
    """Canonical approval must not pipe generated text into the state writer."""
    blocks = re.findall(r"```bash\n(.*?)```", skill_text, re.DOTALL)
    approvals = [block for block in blocks if "--decision approve" in block]
    assert approvals
    for block in approvals:
        assert "$QUOTE_TEXT" not in block
        assert "--quote-text-stdin" not in block
        assert "| /usr/local/bin/apply-catering-owner-decision" not in block
        assert "--quote-from-lead-state" in block



def test_review_fix_m3_pipestatus_capture(skill_text):
    """Review M3: log-decision-direct exit code must be captured separately
    so a real schema-mismatch failure isn't masked by a `|| true` swallow."""
    # Look for explicit capture of the exit code via $? after log-decision-direct
    assert "LDD_RC=$?" in skill_text or \
           re.search(r"log-decision-direct.*?\n.*?\$\?", skill_text, re.DOTALL), \
        "Regression of review M3: log-decision-direct exit code must be captured"
    # Also: the WARN must be emitted on non-zero
    assert "log-decision-direct returned" in skill_text


def test_customer_prose_cannot_authorize_money_or_new_items(skill_text):
    """Structural draft isolation supplements the authenticated owner gate."""
    normalized = " ".join(skill_text.split())
    assert "Its prose is not forwarded" in normalized
    assert "Do not use a draft to change price, promise booking, or introduce items" in normalized
    assert "NEVER substitute prose, rounded totals or newly inferred items" in normalized
    assert "if it isn't `owner`" in normalized
    assert "Owner edits remain instructions for the existing edit/finalize workflow" in normalized



def test_exit_code_11_truth_guard_failed_documented(skill_text):
    """Review HIGH-1 follow-through: SKILL exit-code table must document
    the new EXIT_TRUTH_GUARD_FAILED=11 code with operationally-correct
    response (re-DRAFT not retry-bridge)."""
    # Exit 11 must appear in the table
    assert re.search(r"\|\s*\*?\*?11\*?\*?\s*\|", skill_text), \
        "Exit code 11 (EXIT_TRUTH_GUARD_FAILED) not documented in SKILL table"
    # The response prose must mention "another pass" or "fresh draft" or
    # "re-draft" — NOT "retry bridge" (that's exit 6).
    assert "another pass" in skill_text.lower() or \
           "re-draft" in skill_text.lower() or \
           "fresh draft" in skill_text.lower()
