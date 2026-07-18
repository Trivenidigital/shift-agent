"""Natural-language sample-idea selection (`intake._parse_sample_choice`).

Regression cover for E2E audit finding BC-6: sample-idea selection only
accepted a literal digit 1/2. A customer replying "the first one",
"second", or "option 1" fell through to `None` and the intake loop
re-prompted the SAME menu without advancing.

The parser now maps clear ordinal/cardinal/option references to the right
0-based sample index, bounded to how many samples were offered, while
genuinely ambiguous replies ("yes", unrelated words) and out-of-range
ordinals still return None so the loop legitimately re-prompts.

Pure-function; flyer-named.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from agents.flyer.intake import _parse_sample_choice  # noqa: E402


@pytest.mark.parametrize(
    "reply, expected",
    [
        # Literal digits (pre-existing behaviour).
        ("1", 0),
        ("2", 1),
        # Ordinal words.
        ("the first one", 0),
        ("first", 0),
        ("second", 1),
        ("1st", 0),
        ("2nd", 1),
        # Cardinal words.
        ("one", 0),
        ("two", 1),
        ("number two", 1),
        # "option N" phrasing.
        ("option 1", 0),
        ("option 2", 1),
    ],
)
def test_natural_selections_map_to_sample_index(reply, expected):
    assert _parse_sample_choice(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "yes",           # ambiguous confirmation, not a selection
        "restaurant",    # unrelated word
        "sure",
        "",
        "third",         # out-of-range ordinal (only 2 samples offered)
        "option 3",      # out-of-range digit
        "3",
    ],
)
def test_ambiguous_or_out_of_range_returns_none(reply):
    assert _parse_sample_choice(reply) is None


def test_bounded_to_offered_sample_count():
    # "second" is valid when 2 samples are offered but not when only 1 is.
    assert _parse_sample_choice("second", num_samples=2) == 1
    assert _parse_sample_choice("second", num_samples=1) is None
