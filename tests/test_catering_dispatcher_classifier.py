"""Regression guards for the F7 catering-dispatcher-watchdog classifier.

Pure-Python tests — no fcntl, no bridge stub, no subprocess. Cross-platform.
Just import the classifier functions and assert on inputs/outputs. Catches
regression if a future SKILL.md edit or content-classifier tweak shifts
the boundary in a way that would re-break production.

Loads the watchdog script via SourceFileLoader because it has no .py
extension (deployed as /usr/local/bin/* on production VPS, source lives
in src/agents/catering/scripts/).

Originally also covered F8 (catering-owner-action-watchdog) and F9
(shift-missed-dispatch-notifier) classifiers; those were deleted in the
2026-05-04 canonical-cleanup when the cf-router Hermes plugin (PR-CF6)
took over their role. F7 stays alive — the dispatcher-watchdog catches
missed catering inquiries (lead-creation gap), which the plugin doesn't
yet replace.
"""
from __future__ import annotations

from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DISPATCHER_WATCHDOG = REPO / "src" / "agents" / "catering" / "scripts" / "catering-dispatcher-watchdog"


def _load(path: Path, mod_name: str):
    """Load a script with no .py extension into a module object."""
    return SourceFileLoader(mod_name, str(path)).load_module()


@pytest.fixture(scope="module")
def dispatcher_mod():
    return _load(DISPATCHER_WATCHDOG, "_dispatcher_watchdog_test")

class TestCateringClassifierPositive:
    """Cases that MUST be classified as catering. If any of these regress to
    False, the watchdog will silently miss real customer inquiries."""

    @pytest.mark.parametrize("text,min_signals", [
        # The canonical customer inquiry that started the overnight saga
        ("Hello This is an inquiry about catering on May 8th evening, "
         "food must be delivered by 7 PM. 120 people, 80 nonveg and 40 veg.",
         3),
        # Wedding catering with explicit headcount
        ("Cater my wedding for 200 guests next month",
         2),
        # Headcount + food + delivery
        ("Can you do delivery of food for 75 people on Saturday",
         3),
        # Catering keyword + headcount only
        ("I want catering for 100",
         2),
        # Headcount + food + event
        ("Need biryani and 50 vegetarian meals for office event Friday",
         3),
        # Anniversary + headcount + food
        ("Need food for 80 people, anniversary dinner next Saturday",
         3),
        # Reception
        ("Catering for 150 person reception next Saturday",
         2),
    ])
    def test_must_classify_positive(self, dispatcher_mod, text, min_signals):
        is_catering, signals = dispatcher_mod.classify_catering(text)
        actual_signals = [s for s in signals if not s.startswith("rejected")]
        assert is_catering, f"FALSE NEGATIVE on {text!r}; signals={signals}"
        assert len(actual_signals) >= min_signals, \
            f"Expected ≥{min_signals} signals, got {actual_signals!r}"


class TestCateringClassifierNegative:
    """Cases that MUST NOT be classified as catering. False positives create
    spurious leads that need manual cleanup."""

    @pytest.mark.parametrize("text", [
        "Boss I am sick today, can't come in",
        "Thanks for your service",
        "ok thanks",
        "100 people",                         # headcount alone — insufficient
        "menu has biryani",                    # food alone
        "Can you cover Anjali's shift tomorrow?",  # shift coverage, not catering
        "What time do you open?",              # general inquiry
        "Sorry running late",                  # employee message
        "",                                    # empty
        "ok",                                  # too short
    ])
    def test_must_classify_negative(self, dispatcher_mod, text):
        is_catering, signals = dispatcher_mod.classify_catering(text)
        assert not is_catering, f"FALSE POSITIVE on {text!r}; signals={signals}"


class TestCateringHeadcountExtraction:
    """The headcount integer must extract correctly across phrasing variants."""

    @pytest.mark.parametrize("text,expected_hc", [
        ("for 50 people",        50),
        ("100 guests",           100),
        ("80 ppl",               80),
        ("40 vegetarian",        40),
        ("50 meals",             50),
        ("75 plates",            75),
        ("for 200",              200),
        ("serving 30",           30),
        ("feed 60",              60),
    ])
    def test_extract_headcount(self, dispatcher_mod, text, expected_hc):
        _is_c, signals = dispatcher_mod.classify_catering(
            f"catering for the team — {text}, food included delivered Saturday"
        )
        hc_signals = [s for s in signals if s.startswith("headcount:")]
        assert hc_signals, f"No headcount signal for {text!r}; signals={signals}"
        extracted = int(hc_signals[0].split(":")[1])
        assert extracted == expected_hc, f"Expected {expected_hc}, got {extracted}"


# ============================================================================
# F8 — catering owner-action watchdog: parse_owner_action()
# ============================================================================

