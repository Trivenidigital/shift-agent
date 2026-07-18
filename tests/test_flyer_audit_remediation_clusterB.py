"""Cluster B flyer menu-extraction audit remediation (2026-07-13).

Five findings, each with a failing-first test then a fix in
`src/agents/flyer/facts.py` (+ one enum add in `src/platform/schemas.py`):

  BC-1  rupee / bare prices dropped   (only `$` was recognized)
  BC-2  bare name-only menu list dropped (newline / pure-comma dish lists)
  SW-2  category-suffix fabricates names ("Dosa" -> "Dosa Biryani")
  SW-3  multi-price offer fabricates a phantom item (name='also' $7.99)
  SW-4  silent same-item price conflict ("Biryani $10 Biryani $12")

The companion regression anchor `tests/test_flyer_facts.py` must stay green.
"""
from __future__ import annotations

import pytest


def _items_map(facts):
    """{name: price} from item:N facts."""
    names, prices = {}, {}
    for f in facts:
        if f.fact_id.startswith("item:") and f.fact_id.endswith(":name"):
            names[f.fact_id.split(":")[1]] = f.value
        elif f.fact_id.startswith("item:") and f.fact_id.endswith(":price"):
            prices[f.fact_id.split(":")[1]] = f.value
    return {names[i]: prices.get(i) for i in names}


# ─────────────────────────── BC-1 rupee / bare prices ───────────────────────
# Currency-preservation decision (documented): $ and BARE amounts emit "$N";
# every rupee spelling (₹ / Rs / Rs. / rs / rupees) normalizes to "₹N". A BARE
# (symbol-less) amount is a price ONLY if it carries a .NN decimal — a bare
# INTEGER is treated as a quantity/year/phone digit, never a price.

@pytest.mark.parametrize(
    ("brief", "expected_name", "expected_price"),
    [
        ("Idli 5.99", "Idli", "$5.99"),            # bare decimal, adjacency
        ("Idli - 5.99", "Idli", "$5.99"),          # bare decimal, dash sep
        ("Idli 5.99 each", "Idli", "$5.99"),       # bare decimal + 'each'
        ("Idli ₹120", "Idli", "₹120"),             # rupee symbol
        ("Idli Rs 120", "Idli", "₹120"),           # Rs -> ₹
        ("Idli Rs. 120", "Idli", "₹120"),          # Rs. -> ₹
        ("Idli rs 120", "Idli", "₹120"),           # rs -> ₹
        ("Idli 120 rupees", "Idli", "₹120"),       # trailing rupees -> ₹
    ],
)
def test_bc1_rupee_and_bare_prices_extracted(brief, expected_name, expected_price):
    from agents.flyer.facts import _item_price_facts

    m = _items_map(_item_price_facts(brief, message_id="m"))
    assert m.get(expected_name) == expected_price, m


def test_bc1_multi_item_bare_decimal_run():
    from agents.flyer.facts import _item_price_facts

    m = _items_map(_item_price_facts(
        "weekend special idli 5.99 vada 4.99 dosa 6.99", message_id="m"))
    # Item names are emitted as the customer typed them (lowercase here);
    # display-casing happens downstream at render time.
    assert m.get("vada") == "$4.99", m
    assert m.get("dosa") == "$6.99", m
    # first pair keeps its $5.99; name may carry the 'weekend special' qualifier.
    assert any(p == "$5.99" and "idli" in n.lower() for n, p in m.items()), m


@pytest.mark.parametrize("brief", [
    "Samosa 8 items",          # quantity, not a price
    "Gulab Jamun 100 count",   # quantity/count
    "Since 2020 Diwali Sale",  # year
    "Call 7329837841",         # phone digits
    "Everything 20% off today",# percent / discount
    "Menu 15.00% discount",    # decimal percent
])
def test_bc1_guards_reject_false_prices(brief):
    from agents.flyer.facts import _item_price_facts

    m = _items_map(_item_price_facts(brief, message_id="m"))
    # No numeric token from any guard case becomes an item price.
    assert m == {}, m


# ─────────────────────────── BC-2 bare name-only list ───────────────────────

def test_bc2_newline_dish_list_extracted():
    from agents.flyer.facts import _item_name_facts

    facts = _item_name_facts("Idli\nDosa\nVada\nPongal", message_id="m")
    names = [f.value for f in facts if f.fact_id.endswith(":name")]
    assert names == ["Idli", "Dosa", "Vada", "Pongal"], names


def test_bc2_pure_comma_dish_list_extracted():
    from agents.flyer.facts import _item_name_facts

    facts = _item_name_facts("Idli, Dosa, Vada, Pongal", message_id="m")
    names = [f.value for f in facts if f.fact_id.endswith(":name")]
    assert names == ["Idli", "Dosa", "Vada", "Pongal"], names


@pytest.mark.parametrize("brief", [
    "Create a weekend flyer for my restaurant",
    "make me a poster for saturday",
    "open sat and sun morning",
    "We cook fresh food fresh every single day",  # sentence with a verb
    "Please design something modern and colorful",
])
def test_bc2_instruction_text_never_becomes_items(brief):
    from agents.flyer.facts import _item_name_facts

    facts = _item_name_facts(brief, message_id="m")
    names = [f.value for f in facts if f.fact_id.endswith(":name")]
    assert names == [], names


def test_bc2_single_bare_line_is_not_enough():
    """A lone bare line is not a list — need >=2 consecutive bare dish lines."""
    from agents.flyer.facts import _item_name_facts

    facts = _item_name_facts("Idli", message_id="m")
    assert [f for f in facts if f.fact_id.endswith(":name")] == []


def test_bc2_end_to_end_no_phantom_from_brief(monkeypatch):
    """A real instruction brief through extract_text_facts must not gain items."""
    from agents.flyer import facts as facts_module
    from schemas import FlyerRequestFields

    monkeypatch.setattr(facts_module, "build_hermes_semantic_brief_provider", lambda: None)
    raw = "Create a weekend flyer for my restaurant"
    facts = facts_module.extract_text_facts(
        FlyerRequestFields(notes=raw), raw, message_id="m")
    assert [f for f in facts if f.fact_id.startswith("item:")] == []


# ─────────────────────────── SW-2 category-suffix gate ──────────────────────

def test_sw2_suffix_appended_only_to_incomplete_modifier():
    from agents.flyer.facts import _item_price_facts

    # 'biryani' anywhere sets category_suffix='Biryani'. A protein MODIFIER
    # completes to '<X> Biryani'; a COMPLETE dish name is left untouched.
    m = _items_map(_item_price_facts(
        "Biryani menu. Chicken $10, Dosa $8, Idli $5", message_id="m"))
    assert m.get("Chicken Biryani") == "$10", m
    assert m.get("Dosa") == "$8", m          # NOT 'Dosa Biryani'
    assert m.get("Idli") == "$5", m          # NOT 'Idli Biryani'
    assert "Dosa Biryani" not in m and "Idli Biryani" not in m


def test_sw2_protein_modifiers_still_complete_to_biryani():
    """Regression parity with the deployed biryani-protein behavior."""
    from agents.flyer.facts import _item_price_facts

    m = _items_map(_item_price_facts(
        "Special Biryani flyer. Chicken $16.99, Goat $18.99, Veg $12.99", message_id="m"))
    assert m.get("Chicken Biryani") == "$16.99", m
    assert m.get("Goat Biryani") == "$18.99", m
    assert m.get("Veg Biryani") == "$12.99", m


# ─────────────────────────── SW-3 multi-price offer phantom ─────────────────

def test_sw3_multi_price_offer_no_phantom_item():
    from agents.flyer.facts import _item_price_facts

    facts = _item_price_facts(
        "everything $5.99 and also $7.99 combo, idli, dosa, vada", message_id="m")
    names = [f.value.lower() for f in facts if f.fact_id.endswith(":name")]
    # The connector/offer-filler words must NEVER become priced items.
    assert "also" not in names, names
    assert "everything" not in names, names
    assert "combo" not in names, names
    # Fail-closed: no priced item is fabricated from this offer clause.
    assert names == [], names


def test_sw3_trailing_dish_names_recoverable_when_given_as_a_clean_list():
    """The dish names idli/dosa/vada ARE extractable when given as a clean list
    (proving the capability); mining them from mixed priced prose is out of the
    safe scope (see BC-2 boundary)."""
    from agents.flyer.facts import _item_name_facts

    facts = _item_name_facts("Idli\nDosa\nVada", message_id="m")
    names = [f.value for f in facts if f.fact_id.endswith(":name")]
    assert names == ["Idli", "Dosa", "Vada"], names


# ─────────────────────────── SW-4 price-conflict signal ─────────────────────

def test_sw4_enum_has_price_conflict():
    from schemas import FlyerManualReview

    mr = FlyerManualReview(reason_code="price_conflict")
    assert mr.reason_code == "price_conflict"


def test_sw4_same_name_conflicting_price_is_flagged():
    from agents.flyer.facts import price_conflict_signals

    assert price_conflict_signals("Biryani $10 Biryani $12") == ["Biryani"]


def test_sw4_no_conflict_when_prices_agree():
    from agents.flyer.facts import price_conflict_signals

    assert price_conflict_signals("Biryani $10 Biryani $10") == []
    assert price_conflict_signals("Chicken $10, Goat $12") == []
