"""M3 — the catering proposal-set state machine.

Proposal sets shipped with 8 statuses and NO transition table (unlike leads, which
have had CATERING_TRANSITIONS since v0.3), so every writer could put a set into any
status and nothing said which moves were legal. These cells pin the table against
what the two deployed writers actually do, and pin that both writers consult it.

Pure + static only — cross-platform. The subprocess behaviour of the two scripts is
covered by tests/test_create_catering_proposal_options.py and
tests/test_select_catering_proposal.py.
"""
from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

from schemas import (
    CATERING_PROPOSAL_SET_TRANSITIONS,
    CateringProposalStatus,
    is_proposal_set_transition_allowed,
)

try:                                    # py3.8+ typing shim, mirrors schemas' own use
    from typing import get_args
except ImportError:                     # pragma: no cover
    get_args = None                     # type: ignore

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "src" / "agents" / "catering" / "scripts"
_CREATE = _SCRIPTS / "create-catering-proposal-options"
_SELECT = _SCRIPTS / "select-catering-proposal"


# ── table shape ─────────────────────────────────────────────────────────────
def test_every_status_is_a_key():
    """No status may be missing from the table — an absent key silently reads as
    'nothing is legal from here', which is indistinguishable from a real terminal."""
    assert set(CATERING_PROPOSAL_SET_TRANSITIONS) == set(get_args(CateringProposalStatus))


def test_every_target_is_a_declared_status():
    declared = set(get_args(CateringProposalStatus))
    for src, targets in CATERING_PROPOSAL_SET_TRANSITIONS.items():
        assert targets <= declared, f"{src} points at undeclared statuses"


def test_terminal_statuses_have_no_outgoing_edges():
    for status in ("SEND_FAILED", "SUPERSEDED", "SELECTED",
                   "SELECTED_OWNER_CARD_FAILED", "SELECT_FAILED", "EXPIRED"):
        assert CATERING_PROPOSAL_SET_TRANSITIONS[status] == set()


# ── every LEGAL move (derived from the two deployed writers) ────────────────
@pytest.mark.parametrize(("frm", "to"), [
    # create-catering-proposal-options
    ("DRAFT", "SENT"),                    # _mark_sent_and_supersede, no later set
    ("DRAFT", "SEND_FAILED"),             # _mark_send_outcome, definite non-delivery
    ("DRAFT", "SEND_UNCERTAIN"),          # _mark_send_outcome, bridge accepted / ack unparseable
    ("DRAFT", "SUPERSEDED"),              # _mark_sent_and_supersede, has_later_sent
    ("SEND_UNCERTAIN", "SUPERSEDED"),     # OPERATOR-driven resolution only (see below)
    ("SENT", "SUPERSEDED"),               # older set superseded by a newer send
    # select-catering-proposal
    ("SENT", "SELECTING"),                # _claim_selection
    ("SELECTING", "SELECTED"),            # _finish_selection, finalize rc=0
    ("SELECTING", "SELECTED_OWNER_CARD_FAILED"),   # finalize rc=6
    ("SELECTING", "SELECT_FAILED"),       # bad finalize rc / superseded / exception
    # catering-proposal-expiry-sweep (M3)
    ("SENT", "EXPIRED"),
])
def test_legal_transitions(frm, to):
    assert is_proposal_set_transition_allowed(frm, to)


# ── a sample of ILLEGAL moves ───────────────────────────────────────────────
@pytest.mark.parametrize(("frm", "to"), [
    ("DRAFT", "SELECTING"),          # a set nobody has been sent cannot be selected
    ("DRAFT", "SELECTED"),
    ("DRAFT", "EXPIRED"),            # only a SENT set has a validity window
    ("SENT", "SELECTED"),            # must claim via SELECTING first (the CAS)
    ("SENT", "SEND_FAILED"),         # it already went out; failure is not retroactive
    ("SENT", "DRAFT"),
    ("SELECTING", "SENT"),           # no un-claim
    ("SELECTING", "EXPIRED"),        # a set mid-selection is not idle
    ("SELECTED", "SELECTING"),       # terminal — no re-selection of the same set
    ("SELECTED", "SUPERSEDED"),
    ("SELECT_FAILED", "SELECTING"),  # a retry mints a NEW set, never revives this one
    ("SUPERSEDED", "SENT"),
    ("SEND_FAILED", "SENT"),
    ("EXPIRED", "SENT"),             # an expired quote is never silently revived
    ("EXPIRED", "SELECTING"),        # the whole point: no selection after expiry
    ("EXPIRED", "EXPIRED"),          # no self-edge → the sweep cannot double-fire
    # P1 — SEND_UNCERTAIN is terminal to AUTOMATION.
    ("SEND_UNCERTAIN", "SENT"),      # no outbound_message_id exists to claim delivery with
    ("SEND_UNCERTAIN", "SELECTING"), # a set nobody can prove arrived is not selectable
    ("SEND_UNCERTAIN", "SEND_FAILED"),   # uncertainty never hardens into a definite failure
    ("SEND_UNCERTAIN", "EXPIRED"),   # no validity window was ever stamped on it
    ("SEND_FAILED", "SEND_UNCERTAIN"),   # nor a definite failure into uncertainty
    ("SENT", "SEND_UNCERTAIN"),      # it went out with an id; the send is not in doubt
])
def test_illegal_transitions(frm, to):
    assert not is_proposal_set_transition_allowed(frm, to)


def test_send_uncertain_is_operator_resolvable_but_terminal_to_automation():
    """P1/R1. `send_uncertain` means the bridge ACCEPTED the menu (2xx) and the ack
    body was unparseable — it most likely reached the customer. Recording that as
    SEND_FAILED asserts definite non-delivery, which is a lie the state row was
    telling while the audit row beside it told the truth.

    SUPERSEDED is the ONLY out-edge, and it is the honest one:

      * SENT is unreachable by construction, not by policy — a SENT set must carry
        an `outbound_message_id` (CateringProposalSet._validate_proposal_set), and
        the uncertain path has none to carry: the bridge returned no id in EITHER
        sub-case. An operator cannot supply evidence the transport never produced.
      * SELECTING would let a customer lock in a set nobody can show was delivered.
      * An empty out-edge set would make the row permanently irrecoverable, which
        R1 forbids.

    So the resolution an operator actually performs — "reissue the options" — is
    the one the table allows: the reissue mints a NEW set and the uncertain row is
    retired as SUPERSEDED, exactly as a stale SENT set is.

    Nothing automated can take that edge. `_mark_sent_and_supersede` selects the
    rows it supersedes with `row.status == "SENT"`, so a successful later send
    walks straight past a SEND_UNCERTAIN row — pinned behaviourally by
    tests/test_create_catering_proposal_options.py::
    test_a_later_successful_send_does_not_supersede_an_uncertain_set.
    """
    assert CATERING_PROPOSAL_SET_TRANSITIONS["SEND_UNCERTAIN"] == {"SUPERSEDED"}


def test_unknown_statuses_are_refused_not_crashed():
    assert not is_proposal_set_transition_allowed("NOPE", "SENT")
    assert not is_proposal_set_transition_allowed("SENT", "NOPE")
    assert not is_proposal_set_transition_allowed("", "")


def test_no_status_has_a_self_edge():
    """A self-edge would let a writer re-emit a transition audit row forever. The
    lead table has two deliberate ones (QUALIFYING, CUSTOMER_FINALIZED); the
    proposal-set table has none, which is what makes each writer fire at most once."""
    for status, targets in CATERING_PROPOSAL_SET_TRANSITIONS.items():
        assert status not in targets


# ── both writers consult the table (static) ─────────────────────────────────
@pytest.mark.parametrize("script", [_CREATE, _SELECT])
def test_writer_script_compiles(script):
    py_compile.compile(str(script), doraise=True)


@pytest.mark.parametrize("script", [_CREATE, _SELECT])
def test_writer_enforces_the_table_and_audits_refusals(script):
    t = script.read_text(encoding="utf-8")
    assert "is_proposal_set_transition_allowed" in t
    assert "CateringProposalTransitionRefused" in t
    assert "_emit_transition_refusals" in t


@pytest.mark.parametrize("script", [_CREATE, _SELECT])
def test_no_status_write_bypasses_the_guard(script):
    """Every `.status = "<STATUS>"` assignment in the writers must be preceded by a
    _transition_ok call. Counting is the cheap invariant: one guard per write."""
    lines = script.read_text(encoding="utf-8").splitlines()
    writes = [ln for ln in lines
              if ".status = " in ln and "==" not in ln and not ln.strip().startswith("#")]
    guards = [ln for ln in lines if "_transition_ok(" in ln and "def " not in ln]
    assert writes, "expected at least one status write in this writer"
    assert len(guards) >= len(writes), (
        f"{len(writes)} status writes but only {len(guards)} transition guards"
    )
