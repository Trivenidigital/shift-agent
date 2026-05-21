"""Pin the customer-facing copy for the source-edit follow-up correction loop.

Follow-up to PR #140 — same outcome-only discipline applied to two
hand-written inline replies in `src/plugins/cf-router/hooks.py` that handle
"customer adds another correction while a source-edit is already queued":

1. Success branch (queued-followup confirmation): customer's correction was
   saved with the queued edit; we acknowledge and promise delivery.
2. Clarification branch: we couldn't match the correction to the queued edit
   and need the customer to specify what to change.

WhatsApp gets outcome-only copy on both. Audit/Cockpit keep the details:
`project_id`, `queued_followup=true`, `revision_requires_clarification`, the
raw update detail all still land in the `audit_intercepted` row below the
replies (verified by static-text inspection in the no-leak test).

These bodies are inline strings (not helper calls), so the test asserts on
static-text inspection of hooks.py rather than runtime capture — same shape
as the existing `test_cf_router_status_reply_dispatch_uses_source_edit_helper_only_for_source_edit_reason`
in test_flyer_state_reply_table.py.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_PATH = REPO_ROOT / "src" / "plugins" / "cf-router" / "hooks.py"

EXPECTED_SUCCESS_BODY_LINES = (
    '"Flyer Studio\\n"',
    '"------------\\n"',
    '"Got it. I\'ve added this to the careful flyer edit. "',
    '"I\'ll send the updated flyer here once it\'s ready."',
)

EXPECTED_CLARIFICATION_BODY_LINES = (
    "pending_confirmation_message.strip()",
    "reply = pending_confirmation_message.strip()",
    "else:",
    '"Flyer Studio\\n"',
    '"------------\\n"',
    'f"I need one clarification before adding that: {clarification_reason}\\n\\n"',
    '"Please send the exact text, item, price, date, or area of the flyer to change."',
)

# Phrases that previously leaked workflow internals into the customer body.
# Any regression that re-introduces them into the hooks.py queued-followup
# branch (success OR clarification) must fail this test.
FORBIDDEN_PHRASES = (
    "is already queued for a source-preserving edit",
    "queued for a source-preserving",
    "source-preserving edit",
    "I saved this additional correction with the edit request",
    "before adding that to project",
)


def _queued_correction_block() -> str:
    """Return the source text of the queued-followup if/else block, with
    Python comment lines stripped.

    Slices hooks.py around the two reply assignments and removes any line
    whose first non-whitespace character is `#`. The stripping matters
    because the comments above each `reply = (...)` legitimately mention
    the OLD copy we removed ("source-preserving edit" etc.) to explain
    what the rewrite replaced; without stripping those comment lines, the
    forbidden-phrase assertions would trip on documentation prose rather
    than customer-visible body text.

    Any future code reorganization that moves the branches out of this
    if/else needs to update the anchors below.
    """
    source = HOOKS_PATH.read_text(encoding="utf-8")
    anchor_open = "if revision_requires_clarification:"
    anchor_close = "ack_ok, mid, err = actions.send_flyer_text("
    open_idx = source.find(anchor_open)
    close_idx = source.find(anchor_close, open_idx)
    assert open_idx >= 0, f"could not find anchor: {anchor_open}"
    assert close_idx > open_idx, f"could not find anchor: {anchor_close}"
    block = source[open_idx:close_idx]
    return "\n".join(
        line for line in block.splitlines()
        if not line.lstrip().startswith("#")
    )


def test_queued_correction_success_body_matches_outcome_only_copy():
    """Pin the success branch (customer's correction was saved cleanly).

    No project ID. No "source-preserving edit" wording. No "saved this
    additional correction" workflow narration. Exact body string, line by
    line, so a reviewer changing this copy must update both this test and
    the source in the same diff.
    """
    block = _queued_correction_block()
    for line in EXPECTED_SUCCESS_BODY_LINES:
        assert line in block, (
            f"queued-correction SUCCESS body must contain literal source line:\n  {line}\n"
            f"got block:\n{block}"
        )


def test_queued_correction_clarification_body_matches_outcome_only_copy():
    """Pin the clarification branch (we couldn't match the correction).

    No project ID. clarification_reason is preserved because it IS the
    customer-useful signal. The second sentence (Please send the exact
    text/item/price/date/area) tells the customer what to do next.

    If the backend produced a pending APPLY proposal message, prefer it
    over asking the customer to restate “exact text to change” again.
    """
    block = _queued_correction_block()
    for line in EXPECTED_CLARIFICATION_BODY_LINES:
        assert line in block, (
            f"queued-correction CLARIFICATION body must contain literal source line:\n  {line}\n"
            f"got block:\n{block}"
        )


def test_queued_correction_body_does_not_leak_workflow_internals():
    """Negative assertion: forbidden phrases must not appear in either
    branch's customer body. Catches a regression that reintroduces the
    "source-preserving edit queue" or "before adding that to project
    {project_id}" wording."""
    block = _queued_correction_block()
    leaks = [phrase for phrase in FORBIDDEN_PHRASES if phrase in block]
    assert leaks == [], (
        f"queued-correction reply block must not contain workflow-internal "
        f"phrases; leaked: {leaks}"
    )


def test_queued_correction_body_does_not_echo_project_id_into_customer_reply():
    """Project ID must not reach the customer reply via either branch.

    Both branches previously used an f-string referencing `{project_id}` in
    the reply body. The audit row at the end of the block still captures
    project_id in its `detail` field — that's the operator/Cockpit surface,
    not the customer surface.

    The check inspects the if/else block delimited by `_queued_correction_block`
    only, so an unrelated f-string elsewhere in hooks.py doesn't fail this.
    """
    block = _queued_correction_block()
    # The customer-visible f-strings are the ones inside the `reply = (...)`
    # blocks for both branches. Look for `{project_id}` referenced inside
    # any line that's part of the reply assignment.
    # Conservative check: the literal "{project_id}" must not appear in the
    # block at all, because the only legitimate project_id reference in this
    # block was the customer-visible one we just removed; the audit row
    # below the block (and outside `_queued_correction_block`'s slice) still
    # uses project_id for the audit detail.
    assert "{project_id}" not in block, (
        "project_id must not be interpolated into the customer reply block; "
        "operator-side audit row outside this block can still reference it"
    )


def test_queued_correction_audit_row_still_captures_project_id():
    """Belt-and-suspenders: the audit row immediately AFTER the reply block
    must still carry project_id + queued_followup=true so operators can
    triage in the Cockpit. Hard rule: WhatsApp = outcome-only; audit =
    full details. This test verifies the audit side of that rule was not
    accidentally cleared along with the customer-visible side."""
    source = HOOKS_PATH.read_text(encoding="utf-8")
    # The audit entry is the one whose reason is flyer_reference_exact_edit_queued
    # or flyer_primary_failed. Find the detail string that follows.
    assert 'reason="flyer_reference_exact_edit_queued" if ok and ack_ok else "flyer_primary_failed"' in source
    assert "queued_followup=true" in source
    assert "project_id={project_id}" in source, (
        "audit row must still capture project_id for operator triage"
    )
