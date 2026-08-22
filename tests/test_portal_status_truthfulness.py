"""The public portal may not claim an agent is LIVE without runtime evidence.

The portal advertised five LIVE agents. Reconciled against the deployed box on
2026-08-22, three of the five had no usable execution path: #1's owner-approval
step falls through to a dispatcher that cannot run, #3 returns `not_configured`
because no locations are set, and #13 has never had a compliance item entered.

These tests do not re-derive reachability — that is
`tasks/audits/agent-reachability-matrix-2026-08-22.md`'s job. They pin the
claim surface so a future edit cannot quietly re-promote an agent, and so the
prose, the counters and the data array cannot drift apart from one another. A
count in prose is the thing that rots first: it is true when written and nobody
re-reads it when the data changes.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PORTAL = REPO_ROOT / "web" / "portal" / "index.html"

# Only these may carry state "live". Adding a name here is a claim that the
# agent has a reachable deployed execution path AND runtime evidence for it.
EVIDENCED_LIVE = {
    "Daily Brief",          # 30 brief_sent in 30 days
    "EOD Reconciliation",   # 30 eod_snapshot, invariant_violations: 0
}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "sixteen": 16, "seventeen": 17,
}


def _agents() -> list[dict]:
    html = PORTAL.read_text(encoding="utf-8")
    rows = re.findall(
        r'\{\s*tier:\s*(\d+),\s*num:\s*(\d+),\s*name:\s*"([^"]+)".*?state:\s*"(\w+)"',
        html, re.S,
    )
    assert rows, "no agent rows parsed from the portal — shape changed?"
    return [{"tier": int(t), "num": int(n), "name": nm, "state": st} for t, n, nm, st in rows]


def test_only_evidenced_agents_are_claimed_live():
    claimed = {a["name"] for a in _agents() if a["state"] == "live"}
    unevidenced = claimed - EVIDENCED_LIVE
    assert not unevidenced, (
        "portal claims these agents are LIVE with no runtime evidence recorded: "
        f"{sorted(unevidenced)}. Either add the evidence to the reachability matrix "
        "and this allowlist, or correct the claim."
    )


def test_every_partial_agent_explains_what_is_missing():
    """`partial` means deployed-but-not-usable. A badge alone is not honest —
    the reader needs to know whether it awaits code or data."""
    for a in _agents():
        if a["state"] != "partial":
            continue
        rows = [ln for ln in PORTAL.read_text(encoding="utf-8").splitlines()
                if f"num: {a['num']}," in ln]
        assert len(rows) == 1, f"#{a['num']} row not uniquely found"
        marker = 'state_detail: "'
        assert marker in rows[0], f"#{a['num']} has no state_detail at all"
        detail_text = rows[0].split(marker, 1)[1].rsplit('"', 1)[0]
        assert len(detail_text) > 60, (
            f"#{a['num']} {a['name']} is marked partial without a substantive "
            "state_detail explaining what is missing"
        )
        assert "Verified" in detail_text, (
            f"#{a['num']} {a['name']} partial claim carries no verification date"
        )


def test_headline_prose_counts_match_the_data():
    """The headline sentence is the most-read claim on the page and the one
    nothing recomputes. Pin it to the array."""
    html = PORTAL.read_text(encoding="utf-8")
    counts = Counter(a["state"] for a in _agents())
    headline = re.search(r"<h1>.*?</h1>", html, re.S)
    assert headline, "no <h1> found"
    text = re.sub(r"<[^>]+>", " ", headline.group(0)).lower()
    for state, word_state in (("live", "live"), ("scaffold", "scaffolded"), ("future", "horizon")):
        m = re.search(rf"(\w+)\s+(?:\w+\s+){{0,3}}?{word_state}", text)
        assert m, f"headline does not state a {word_state} count: {text.strip()}"
        claimed = NUMBER_WORDS.get(m.group(1))
        if claimed is None:
            continue
        assert claimed == counts[state], (
            f"headline says {m.group(1)} ({claimed}) {word_state} but the data array "
            f"has {counts[state]}"
        )


def test_every_state_used_by_the_data_can_render():
    """A state present in AGENTS but missing from the badge map renders as a
    crash or a blank pill — the array and the renderer must agree."""
    html = PORTAL.read_text(encoding="utf-8")
    badge_block = re.search(r"function badge\(state\)\s*\{.*?\n    \}", html, re.S)
    assert badge_block, "badge() not found"
    known = set(re.findall(r"^\s*(\w+):\s*\{", badge_block.group(0), re.M))
    used = {a["state"] for a in _agents()}
    assert used <= known, f"states used in data but not renderable: {sorted(used - known)}"
    for state in used:
        assert f'id="count-{state}"' in html, f"no counter element for state {state!r}"
