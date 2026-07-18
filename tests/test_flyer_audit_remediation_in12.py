"""IN-1 fact-key literal QA screen + IN-2 legacy uniform-price column screen
(E2E audit 2026-07-13)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.flyer.visual_qa import (
    _fact_key_literal_blockers,
    _uniform_price_column_blockers,
)
from schemas import FlyerProject


def _project(locked_facts, raw_request="weekend flyer"):
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    return FlyerProject.model_validate({
        "project_id": "F9001", "status": "intake_started",
        "customer_phone": "+17329837841", "created_at": now, "updated_at": now,
        "original_message_id": "m-1", "raw_request": raw_request,
        "fields": {"event_or_business_name": "Lakshmis Kitchen"},
        "locked_facts": locked_facts,
    })


def _F(fid, value, source="customer_text"):
    # Real labels are human words ("Business"/"Contact"), never the schema key —
    # using the fid as label would pollute the authorized pool with the key.
    return {"fact_id": fid, "label": "Detail", "value": value, "source": source, "required": True}


# ---------------------------------------------------------------- IN-1
@pytest.mark.parametrize(
    "leaked_text,needle",
    [
        ("fresh menu item:0:name here", "item:0:name"),
        ("weekend item:2:price special", "item:2:price"),
        ("our business_name today", "business_name"),
        ("sender_role owner flyer", "sender_role"),
        ("raw_request weekend", "raw_request"),
    ],
)
def test_in1_blocks_internal_fact_key_literals(leaked_text, needle):
    project = _project([_F("business_name", "Lakshmis Kitchen", "customer_profile")])
    blockers = _fact_key_literal_blockers(project, leaked_text)
    assert any(needle in b for b in blockers), (leaked_text, blockers)


def test_in1_passes_clean_flyer_text():
    project = _project([_F("business_name", "Lakshmis Kitchen", "customer_profile")])
    assert _fact_key_literal_blockers(project, "Weekend Special\nIdli $5.99\nDosa $6.99") == []


def test_in1_does_not_punish_literal_in_customer_brief():
    # Contrived, but the screen must never punish the customer's own words.
    project = _project(
        [_F("business_name", "Lakshmis Kitchen", "customer_profile")],
        raw_request="please print raw_request on the flyer",
    )
    assert _fact_key_literal_blockers(project, "raw_request") == []


# ---------------------------------------------------------------- IN-2 (kept as design)
def _uniform_menu(n_items):
    facts = [_F("pricing_structure", "Any item $5.99")]
    for i in range(n_items):
        facts.append(_F(f"item:{i}:name", f"Item{i}"))
        facts.append(_F(f"item:{i}:price", "$5.99"))
    return _project(facts)


def test_in2_legacy_render_stays_unscreened_by_design():
    # IN-2: on a legacy/non-typeset render (artifact_path=None) the uniform-price
    # column screen must NOT fire — there is no positional contract, so a real menu
    # can legitimately repeat the shared price more than twice. Screening it would
    # false-positive (adversarial-review lesson). The screen is marker-gated.
    project = _uniform_menu(2)
    text = " ".join(["$5.99"] * 6)
    assert _uniform_price_column_blockers(project, text, artifact_path=None) == []
