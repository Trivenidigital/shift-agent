"""Cluster A — cf-router routing/approval remediation (E2E audit 2026-07-13).

BC-3 approval vocabulary · AN-2 decorated approvals · BC-4 typo intent ·
BC-5 festival new-flyer routing · AN-3 echo disambiguation · AN-1 early-approve.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_actions():
    module_name = "cf_router_flyer_actions_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(PLUGIN_DIR / "actions.py"))
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    loader.exec_module(mod)
    return mod


A = _load_actions()


# ---------------------------------------------------------------- BC-3 approval vocab
@pytest.mark.parametrize(
    "reply",
    ["approve", "ok", "yes", "perfect", "looks great", "yes please", "okay",
     "love it", "that works", "ship it", "good to go", "great", "this looks good"],
)
def test_bc3_natural_approvals_approve(reply):
    assert A.is_flyer_approval_text(reply) is True, reply


@pytest.mark.parametrize(
    "reply",
    ["looks good but change the date", "yes please change the color",
     "can you make it bigger", "perfect but move the logo", "no", "not yet"],
)
def test_bc3_edit_requests_do_not_approve(reply):
    assert A.is_flyer_approval_text(reply) is False, reply


# ---------------------------------------------------------------- AN-2 decorated approvals
@pytest.mark.parametrize(
    "reply",
    ["*APPROVE*", "“APPROVE”", "looks good \U0001F44D", "\U0001F44D",
     "APPROVE.", "  approve  ", "Perfect!"],
)
def test_an2_decorated_and_emoji_approvals(reply):
    assert A.is_flyer_approval_text(reply) is True, repr(reply)


def test_an2_thumbs_down_never_approves():
    assert A.is_flyer_approval_text("looks good \U0001F44E") is False
    assert A.is_flyer_approval_text("\U0001F44E") is False


def test_f7_bare_folded_hands_never_approves():
    # F7 (2026-07-18): 🙏 reads as thanks/please in Indian-SMB usage, not "ship it".
    assert A.is_flyer_approval_text("\U0001F64F") is False
    # Alongside explicit approval text it still approves via the text path.
    assert A.is_flyer_approval_text("approve \U0001F64F") is True


# ---------------------------------------------------------------- BC-4 typo intent
@pytest.mark.parametrize(
    "brief",
    ["helo pls make me a flyr for this weekend brekfast specal",
     "make me a flyr", "can you design a postr for saturday",
     "i want a flyar for diwali", "make a flyler for my restaurant"],
)
def test_bc4_typo_flyer_requests_are_recognized(brief):
    assert A.classify_flyer_intent(brief)[0] is True, brief


@pytest.mark.parametrize(
    "brief",
    ["make me a plate of biryani please",
     "i want to order catering for 50 guests",
     "can you deliver dosa to my house"],
)
def test_bc4_does_not_steal_non_flyer_chatter(brief):
    assert A.classify_flyer_intent(brief)[0] is False, brief


# ---------------------------------------------------------------- BC-5 festival routing
@pytest.mark.parametrize(
    "brief",
    ["create a new flyer for diwali dinner",
     "make a flyer for holi celebration",
     "new flyer for pongal special",
     "flyer for eid this weekend"],
)
def test_bc5_festival_brief_is_fresh_new_work(brief):
    assert A.is_vague_flyer_start(brief) is False, f"{brief!r} wrongly vague"
    assert A.should_start_new_flyer_over_active(brief) is True, f"{brief!r} not routed as new"


# ---------------------------------------------------------------- AN-3 echo disambiguation
@pytest.mark.parametrize(
    "reply,expected",
    [("new", "new"), ("new one", "new"), ("make a new one", "new"),
     ("another one", "new"), ("redo", "new"), ("start over", "new"),
     ("give me a new one", "new"),
     ("approve", "approve"), ("perfect", "approve"), ("\U0001F44D", "approve"),
     ("the second one", None), ("1", None), ("maybe later", None)],
)
def test_an3_echo_choice(reply, expected):
    assert A.classify_flyer_quote_echo_choice(reply) == expected, reply
