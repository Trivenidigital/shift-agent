import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "platform"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "agents" / "flyer"))

from schemas import FlyerProject, FlyerLockedFact
import visual_qa


def _proj(facts):
    now = datetime(2026, 6, 14, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F9001",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=now,
        updated_at=now,
        original_message_id="m-test",
        raw_request="Create a flyer for my restaurant.",
        locked_facts=[
            FlyerLockedFact(
                fact_id=f[0],
                label=f[1],
                value=f[2],
                source="customer_text",
                required=True,
            )
            for f in facts
        ],
    )


def test_unauthorized_dollar_price_blocks():
    p = _proj([("item:0:name", "Item", "Punugulu"), ("item:0:price", "Price", "$6.99")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Punugulu $6.99\n6 OFFERS $3.99 | $4.99")
    assert any(x.startswith("fabricated price visible: ") and "$3.99" in x for x in b)


def test_locked_price_does_not_block():
    p = _proj([("item:0:name", "Item", "Punugulu"), ("item:0:price", "Price", "$6.99")])
    assert visual_qa._fabricated_offer_price_blockers(p, "Punugulu $6.99") == []


def test_nondollar_promo_claim_blocks_when_no_offer():
    p = _proj([("item:0:name", "Item", "Masala Dosa")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Masala Dosa\nLimited Time Deal!\nSpecial Combo")
    assert any(x.startswith("fabricated offer claim visible: ") for x in b)


def test_nondollar_promo_passes_when_offer_fact_exists():
    p = _proj([("offer:0", "Offer", "Special Combo any 2 for $9.99")])
    assert visual_qa._fabricated_offer_price_blockers(p, "Special Combo any 2 for $9.99") == []


def test_fabrication_is_block_tier():
    p = _proj([("business_name", "Business", "Lakshmi's Kitchen")])
    assert visual_qa.classify_qa_severity(["fabricated price visible: $3.99"], project=p) == "block"
