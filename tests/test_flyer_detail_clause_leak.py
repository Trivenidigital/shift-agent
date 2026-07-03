"""Prompt-leak fix — brief restatements must never become poster copy.

Labeled failure (three exhibits, 2026-07-03): F0201's crash-mat preview and
both Instagram finals painted the subhead
    "for Lakshmi's Kitchen weekend special $5.99 each ▎ Idli ▎ Medu Vada …"
Mechanism (pinned empirically on the box): the copied brief carried U+258E
blockquote-bar glyphs instead of newlines; `_detail_clauses` does not split on
them, so the ENTIRE brief survived the clause splitter as one mined "detail"
and was painted verbatim (the tofu boxes are the literal U+258E characters).

Fix contract:
1. Vertical-bar separator glyphs (U+258E/U+258F/U+2502/U+2503/U+007C) are
   clause split points, same as newlines.
2. Restatement guard: a mined clause echoing >=2 distinct locked customer_text
   fact values is the brief restated, not a new detail — dropped.
Genuine standalone details keep flowing (legacy sparse-fact briefs rely on
the mining).
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.flyer.render import _detail_clauses
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

RAW = ("Create a flyer for Lakshmi's Kitchen weekend special $5.99 each ▎ Idli ▎ "
       "Medu Vada ▎ Upma ▎ Pongal ▎ Saturday and Sunday only")


def _F(fid, value):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=True)


def _project(raw=RAW, notes=None, facts=None):
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9501", status="generating_concepts", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-leak",
        raw_request=raw, fields=FlyerRequestFields(notes=notes if notes is not None else raw),
        locked_facts=facts if facts is not None else [
            _F("campaign_title", "weekend special"),
            _F("pricing_structure", "$5.99 each"),
            _F("schedule", "Saturday and Sunday only"),
            _F("item:0:name", "Idli"), _F("item:0:price", "$5.99"),
            _F("item:1:name", "Medu Vada"), _F("item:1:price", "$5.99"),
            _F("item:2:name", "Upma"), _F("item:2:price", "$5.99"),
            _F("item:3:name", "Pongal"), _F("item:3:price", "$5.99"),
        ],
    )


def test_no_clause_carries_separator_glyphs():
    # THE three-exhibit leak: the un-split brief must not survive as a clause.
    for clause in _detail_clauses(_project()):
        assert "▎" not in clause, clause
        assert len(clause) < 100, f"whole-brief clause leaked: {clause!r}"


def test_restatement_clause_dropped():
    # After separator splitting, the fragment "for Lakshmi's Kitchen weekend
    # special $5.99 each" echoes two locked facts (title + price) — it is the
    # brief restated, not a detail, and must not reach poster copy.
    clauses = _detail_clauses(_project())
    for clause in clauses:
        assert not ("weekend special" in clause.lower() and "$5.99" in clause), clause


def test_item_fact_details_still_flow():
    # Fact-derived item lines are the poster's real copy — untouched by the fix.
    clauses = _detail_clauses(_project())
    joined = " | ".join(clauses)
    for name in ("Idli", "Medu Vada", "Upma", "Pongal"):
        assert name in joined


def test_legacy_sparse_brief_mining_survives():
    # Sparse-fact legacy briefs rely on notes mining for standalone details:
    # a genuine detail clause (echoes <2 locked fact values) keeps flowing.
    raw = "Evening snacks flyer. Free Masala Chai with any purchase above $12."
    project = _project(raw=raw, notes=raw,
                       facts=[_F("campaign_title", "Evening Snacks")])
    clauses = _detail_clauses(project)
    assert any("Masala Chai" in c for c in clauses), clauses


def test_pipe_separated_briefs_also_split():
    raw = "Create a flyer for Snack Fest | Samosa $2 | Kachori $3 | Friday only"
    project = _project(raw=raw, notes=raw, facts=[
        _F("campaign_title", "Snack Fest"),
        _F("item:0:name", "Samosa"), _F("item:0:price", "$2"),
        _F("item:1:name", "Kachori"), _F("item:1:price", "$3"),
    ])
    for clause in _detail_clauses(project):
        assert "|" not in clause, clause
