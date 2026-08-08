"""The success invariant, inverted (2026-08-08).

GROUNDED FAILURE. On the live 2026-08-01 owner menu turn the reply said
"successfully recorded" and nothing had been recorded. The chokepoint did not
stop it because verification was only ever an EXEMPTION: `verified_action_result
=True` skipped the lint, and its ABSENCE merely fell through to keyword
screening. "recorded" is in no keyword list, so the claim shipped.

Adding "recorded" to the list would move the hole to the next synonym. The rule
is therefore stated positively and bound to the action context:

    claims_action_completed AND NOT verified_action_result  =>  REFUSE

No wording is consulted, so no wording can bypass it. The keyword lint stays as
defense-in-depth for sends that do not assert completion.

These cells run on Windows AND Linux via the fcntl stub — deliberately, because
tests/test_safe_io_bridge_post.py (where the sibling chokepoint cells live) is
Windows-skipped, and a locally-green run of a skipped test is not evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src", REPO / "src" / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402
from schemas import ActionExecutionContext  # noqa: E402

# The exact live wording. Deliberately absent from FORBIDDEN_COMPLETION_VERBS —
# if a future change "fixes" this by blacklisting it, the wording-independence
# cell below fails and says so.
LIVE_FALSE_SUCCESS = "Your menu has been successfully recorded."


def _ctx(**over) -> ActionExecutionContext:
    base = dict(action_id="catering.menu.update_proposed:MU0007",
                is_regulated_action=True, verified_action_result=False)
    base.update(over)
    return ActionExecutionContext(**base)


def _policy(monkeypatch, ctx, message, *, allow_fallback=True):
    """Call the chokepoint policy with audit emission captured."""
    rows: list[tuple] = []
    monkeypatch.setattr(safe_io, "_emit_audit_row",
                        lambda etype, fields: rows.append((etype, fields)))
    verdict = safe_io._enforce_action_context_policy(
        message_parts=[message], jid="15550100001@lid",
        action_context=ctx, allow_fallback=allow_fallback,
    )
    return verdict, rows


# ── The invariant itself ─────────────────────────────────────────────────────
def test_unverified_completion_claim_is_refused(monkeypatch):
    """Test 6. An action was expected, the copy reports success, the result is
    not verified — refused."""
    verdict, rows = _policy(monkeypatch, _ctx(claims_action_completed=True),
                            LIVE_FALSE_SUCCESS)

    assert verdict is not None, "an unverified completion claim must not send"
    ok, _mid, err, status = verdict
    assert ok is False and status == "refused"
    assert err == safe_io._REGULATED_LINT_FALLBACK_SENTINEL, (
        "non-money: the owner gets a safe reply rather than silence")
    assert [r[0] for r in rows] == ["regulated_send_lint_violation"]
    assert rows[0][1]["verb_hits"] == ["unverified_action_completion_claim"]
    assert rows[0][1]["action_id"] == "catering.menu.update_proposed:MU0007"


@pytest.mark.parametrize("wording", [
    LIVE_FALSE_SUCCESS,
    "Menu saved.",                      # no listed verb
    "All set — 47 items are in.",       # no listed verb, no synonym either
    "Done!",
    "मेनू सहेजा गया",                     # not English at all
    "",                                  # not even words
])
def test_the_refusal_does_not_depend_on_wording(monkeypatch, wording):
    """The load-bearing property. None of these trip a keyword list — several
    contain no completion verb in any language the list knows — and every one is
    refused, because the refusal reads the context, not the text. This is what
    "do not solve it by blacklisting 'recorded'" means operationally."""
    verdict, _rows = _policy(monkeypatch, _ctx(claims_action_completed=True), wording)
    assert verdict is not None, f"{wording!r} escaped the invariant"


def test_the_live_wording_is_still_absent_from_the_keyword_list():
    """Non-vacuity for the cell above: if someone ALSO blacklists "recorded",
    the wording-independence proof silently becomes circular. Pin that the
    keyword lint still does NOT catch the live phrase, so the invariant is what
    is doing the work."""
    from agents.flyer.customer_copy_policy import lint_no_unverified_completion
    assert not lint_no_unverified_completion(LIVE_FALSE_SUCCESS).hits, (
        "the live false-success wording is now keyword-caught — the invariant "
        "cells above no longer prove wording-independence; re-point them")


def test_verified_completion_claim_passes(monkeypatch):
    """Test 7. Same claim, same wording — with the verified result behind it."""
    verdict, rows = _policy(
        monkeypatch,
        _ctx(claims_action_completed=True, verified_action_result=True),
        LIVE_FALSE_SUCCESS,
    )
    assert verdict is None, "an evidence-backed completion claim must send"
    assert rows == []


def test_money_or_approval_claims_hard_block_instead_of_falling_back(monkeypatch):
    """Money safety is unchanged by the new rule: an unverified completion claim
    on a money/approval action refuses outright rather than substituting copy."""
    verdict, rows = _policy(
        monkeypatch,
        _ctx(action_id="catering.deposit.charge", claims_action_completed=True),
        "Your deposit is in.",
    )
    assert verdict[2] == "unverified_action_completion_claim"
    assert verdict[2] != safe_io._REGULATED_LINT_FALLBACK_SENTINEL
    assert rows[0][0] == "regulated_send_lint_violation"


def test_an_unverified_claim_is_refused_even_when_not_regulated(monkeypatch):
    """`is_regulated_action=False` is documented as the system-message escape and
    skips the lint entirely. It must NOT also become a way to assert a completion
    that did not happen, so the invariant is checked ahead of that early return."""
    verdict, _rows = _policy(
        monkeypatch,
        _ctx(is_regulated_action=False, claims_action_completed=True),
        LIVE_FALSE_SUCCESS,
    )
    assert verdict is not None


# ── Nothing else moves (test 8) ──────────────────────────────────────────────
def test_the_flag_defaults_false_so_existing_callsites_are_unchanged():
    ctx = ActionExecutionContext(action_id="x", is_regulated_action=True,
                                 verified_action_result=False)
    assert ctx.claims_action_completed is False


@pytest.mark.parametrize("message", [
    "Reply #A3F2X yes to apply this menu.",
    "Which one should be active — $12.00 or $14.00?",
    "I could not read that menu, so there is nothing staged for your approval.",
])
def test_non_claiming_regulated_sends_still_pass_unverified(monkeypatch, message):
    """Test 8. A proposal, a question, and an honest non-completion report are
    not completion claims, so an unverified context does not gate them — this is
    exactly what keeps the failure arm's truthful copy deliverable instead of
    being replaced by a generic ack."""
    verdict, rows = _policy(monkeypatch, _ctx(), message)
    assert verdict is None, f"{message!r} must still send"
    assert rows == []


def test_the_keyword_lint_still_fires_as_defense_in_depth(monkeypatch):
    """A caller that does NOT set the new flag is still screened exactly as
    before — the old wall is intact, not replaced."""
    verdict, rows = _policy(monkeypatch, _ctx(), "Your plan has been upgraded.")
    assert verdict is not None
    assert rows[0][0] == "regulated_send_lint_violation"
    assert "upgraded" in rows[0][1]["verb_hits"]


# ── The free-form (LLM-composed) seam ────────────────────────────────────────
def _screen(monkeypatch, ctx, message, *, fallback="Bounded safe reply."):
    rows: list[tuple] = []
    monkeypatch.setattr(safe_io, "_emit_audit_row",
                        lambda etype, fields: rows.append((etype, fields)))
    monkeypatch.setattr(safe_io, "front_brain_outbound_enforce_enabled",
                        lambda _jid: True)
    out = safe_io._front_brain_outbound_enforce(
        "15550100001@lid", message,
        action_context=ctx, fallback_template=fallback,
    )
    return out, rows


def test_free_form_seam_refuses_an_unverified_claim(monkeypatch):
    """The seam that screens LLM-composed text applies the same invariant, so a
    composition cannot narrate a completion the system cannot evidence."""
    out, rows = _screen(monkeypatch, _ctx(claims_action_completed=True),
                        LIVE_FALSE_SUCCESS)

    assert out == "Bounded safe reply.", "the composed claim must not go out"
    assert rows[0][0] == "front_brain_outbound_refused"
    assert rows[0][1]["hit_classes"] == ["unverified_action_completion_claim"]


def test_free_form_seam_passes_a_verified_claim(monkeypatch):
    out, _rows = _screen(
        monkeypatch,
        _ctx(claims_action_completed=True, verified_action_result=True),
        LIVE_FALSE_SUCCESS,
    )
    assert out == LIVE_FALSE_SUCCESS


def test_free_form_seam_unchanged_for_ordinary_conversation(monkeypatch):
    """Test 8 at the second seam: no claim flag, benign copy → passes through
    byte-identical, exactly as before this change."""
    body = "Happy to help — what date is the event?"
    out, _rows = _screen(monkeypatch, _ctx(), body)
    assert out == body
