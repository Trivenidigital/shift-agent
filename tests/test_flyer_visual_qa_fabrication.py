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


def test_no_price_facts_creative_latitude_no_block():
    # Deviation pin: a project with only non-price facts gives the AI creative
    # latitude on pricing, so $-prices in the OCR must NOT be flagged fabricated.
    p = _proj([("business_name", "Business", "Lakshmi's Kitchen")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Lakshmi's Kitchen\nChicken Biryani $16.99")
    assert not any(x.startswith("fabricated price visible: ") for x in b)


# --- FIX 2 (Codex HIGH): _has_offer_fact must not treat plain item prices as
# offer authorization. A priced menu with no real offer fact must still block a
# fabricated non-dollar promo banner. ---------------------------------------


def test_item_price_is_not_an_offer_fact():
    # item:N:price is a plain menu price, NOT an offer authorization.
    p = _proj([("item:0:name", "Item", "Punugulu"), ("item:0:price", "Price", "$6.99")])
    assert visual_qa._has_offer_fact(p) is False


def test_priced_menu_no_offer_fact_blocks_fabricated_promo():
    # A priced menu (item:N:price present) with NO offer fact + OCR containing a
    # fabricated promo banner must BLOCK — the plain item price must not license
    # the banner.
    p = _proj([("item:0:name", "Item", "Punugulu"), ("item:0:price", "Price", "$6.99")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Punugulu $6.99\nLimited Time Deal")
    assert any(x.startswith("fabricated offer claim visible: ") for x in b)


def test_explicit_offer_fact_passes_matching_promo():
    # An explicit offer:0 fact authorizes the matching promo phrase.
    p = _proj([("offer:0", "Offer", "Limited Time Deal on combos")])
    b = visual_qa._fabricated_offer_price_blockers(p, "Limited Time Deal")
    assert not any(x.startswith("fabricated offer claim visible: ") for x in b)


def test_pricing_structure_with_promo_term_authorizes_offer():
    # A pricing_structure fact whose value says "buy 2 get 1" carries a promo
    # term, so it authorizes the matching promo phrase.
    p = _proj([("pricing_structure", "Pricing", "buy 2 get 1 free on all dosas")])
    assert visual_qa._has_offer_fact(p) is True
    b = visual_qa._fabricated_offer_price_blockers(p, "Buy 2 Get 1 free")
    assert not any(x.startswith("fabricated offer claim visible: ") for x in b)


def test_pricing_structure_without_promo_term_is_not_offer():
    # A plain pricing_structure fact (no promo term) does NOT authorize promos.
    p = _proj([("pricing_structure", "Pricing", "per plate pricing")])
    assert visual_qa._has_offer_fact(p) is False
    b = visual_qa._fabricated_offer_price_blockers(p, "Limited Time Deal")
    assert any(x.startswith("fabricated offer claim visible: ") for x in b)


def test_promotion_end_counts_as_offer():
    p = _proj([("promotion_end", "Promotion End", "2026-12-31")])
    assert visual_qa._has_offer_fact(p) is True


def test_arbitrary_pric_substring_is_not_offer():
    # A fact_id containing the 'pric' substring (e.g. 'price_note') is NOT an
    # offer fact under the strict predicate.
    p = _proj([("price_note", "Price Note", "prices subject to change")])
    assert visual_qa._has_offer_fact(p) is False
