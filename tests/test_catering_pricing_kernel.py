"""M2 — deterministic catering pricing kernel (src/platform/catering_pricing.py).

PURE in-process tests: no subprocess, no state files, no fcntl — these run on
Windows AND Linux. The kernel is the one place catering money is decided, so
the coverage here is deliberately exhaustive: every rounding boundary, every
refusal, and a determinism cell that pins byte-identical serialization.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_PLATFORM_DIR = Path(__file__).resolve().parent.parent / "src" / "platform"
if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

from pydantic import ValidationError

from schemas import (
    CateringDiscount,
    CateringFee,
    CateringPackage,
    CateringPricebook,
    Menu,
    MenuItem,
)

from catering_pricing import (
    MAX_LINE_QTY,
    PRICE_STATUS_PREFIX,
    PricingError,
    QuoteComputation,
    cents_to_whole_dollars,
    compute_quote,
    default_basket,
    format_cents,
    load_pricebook,
    price_status_line,
    render_quote_lines,
    usd_to_cents,
)

TS = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
EFFECTIVE = date(2026, 7, 1)


# ── builders ─────────────────────────────────────────────────────────────────
def _pricebook(**over) -> CateringPricebook:
    base = dict(
        version=4,
        effective_date=EFFECTIVE,
        updated_at=TS,
        placeholder=False,
        tax_rate_bps=0,
        per_person_packages=[
            CateringPackage(id="veg-standard", name="Standard Veg Buffet",
                            price_per_person_cents=1800, min_guests=20),
            CateringPackage(id="veg-premium", name="Premium Veg Buffet",
                            price_per_person_cents=2900, min_guests=40),
            CateringPackage(id="veg-retired", name="Retired Buffet",
                            price_per_person_cents=900, min_guests=1, active=False),
        ],
        fixed_fees=[],
        approved_discounts=[],
        item_price_overrides={},
    )
    base.update(over)
    return CateringPricebook(**base)


def _menu(**over) -> Menu:
    base = dict(
        version=7,
        updated_at=TS,
        items=[
            MenuItem(name="Samosa", price_usd=2.5, category="appetizer", serves=5),
            MenuItem(name="Gulab Jamun", price_usd=4.0, category="dessert", serves=10),
            MenuItem(name="Paneer Tikka", price_usd=12.0, category="main", serves=8),
            MenuItem(name="Seasonal Special", price_usd=9.0, category="special",
                     available=False),
            MenuItem(name="Chef's Whim", price_usd=None, category="special"),
        ],
    )
    base.update(over)
    return Menu(**base)


# ── rounding primitives ──────────────────────────────────────────────────────
def test_usd_to_cents_avoids_binary_float_error():
    assert usd_to_cents(4.35) == 435       # 4.35 is 4.34999... in binary float
    assert usd_to_cents(2.5) == 250
    assert usd_to_cents(0) == 0


def test_cents_to_whole_dollars_rounds_half_up_not_bankers():
    # Python's round(2.5) == 2 (banker's). Money must round half UP.
    assert cents_to_whole_dollars(250) == 3
    assert cents_to_whole_dollars(350) == 4
    assert cents_to_whole_dollars(249) == 2


def test_format_cents_never_renders_unknown_as_zero():
    assert format_cents(123456) == "$1,234.56"
    assert format_cents(0) == "$0.00"
    assert "0.00" not in format_cents(None)


# ── happy paths ──────────────────────────────────────────────────────────────
def test_per_person_package_scales_with_headcount():
    """The BL-CATER-03 fix: 200 guests costs 200x one guest, not qty=1."""
    qc = compute_quote(200, "veg-standard", [], None, _pricebook(), _menu())
    assert qc.per_person_subtotal_cents == 1800 * 200 == 360_000
    assert qc.total_cents == 360_000
    assert qc.price_status == "exact"
    assert qc.flags == []


def test_a_la_carte_only_uses_menu_prices_and_reads_estimated():
    qc = compute_quote(10, None, [("Samosa", 4), ("Paneer Tikka", 2)], None,
                       _pricebook(), _menu())
    assert [(ln.name, ln.unit_cents, ln.extended_cents, ln.source) for ln in qc.lines] == [
        ("Samosa", 250, 1000, "menu"),
        ("Paneer Tikka", 1200, 2400, "menu"),
    ]
    assert qc.items_subtotal_cents == 3400
    assert qc.total_cents == 3400
    # Menu prices are the culinary catalog, not the committed commercial book.
    assert qc.price_status == "estimated"


def test_package_plus_extras_sums_both_streams():
    qc = compute_quote(50, "veg-standard", [("Gulab Jamun", 6)], None,
                       _pricebook(), _menu())
    assert qc.per_person_subtotal_cents == 90_000
    assert qc.items_subtotal_cents == 2400
    assert qc.subtotal_cents == 92_400
    assert qc.total_cents == 92_400


def test_the_package_appears_as_the_first_line_of_the_itemization():
    qc = compute_quote(50, "veg-standard", [("Gulab Jamun", 6)], None,
                       _pricebook(), _menu())
    head = qc.lines[0]
    assert (head.name, head.qty, head.unit_cents, head.extended_cents, head.source) == (
        "Standard Veg Buffet", 50, 1800, 90_000, "package")
    assert [ln.source for ln in qc.lines] == ["package", "menu"]


def test_the_package_line_is_not_double_counted():
    """`items_subtotal_cents` is a-la-carte only; the package lives in
    `per_person_subtotal_cents`. Summing the lines must equal their sum, and the
    subtotal must not count the package twice."""
    qc = compute_quote(50, "veg-standard", [("Gulab Jamun", 6)], None,
                       _pricebook(), _menu())
    assert qc.items_subtotal_cents == 2400          # the dessert only
    assert qc.per_person_subtotal_cents == 90_000   # the package only
    assert sum(ln.extended_cents for ln in qc.lines) == 92_400
    assert qc.subtotal_cents == 92_400


def test_a_pure_a_la_carte_quote_has_no_package_line():
    qc = compute_quote(10, None, [("Samosa", 2)], None, _pricebook(), _menu())
    assert all(ln.source != "package" for ln in qc.lines)
    assert qc.per_person_subtotal_cents == 0


def test_render_does_not_repeat_the_package_in_the_items_block():
    qc = compute_quote(50, "veg-standard", [("Gulab Jamun", 6)], None,
                       _pricebook(), _menu())
    text = render_quote_lines(qc)
    assert text.count("Standard Veg Buffet") == 1
    assert "Package: Standard Veg Buffet" in text


def test_item_price_override_beats_the_menu_price():
    pb = _pricebook(item_price_overrides={"Gulab Jamun": 450})
    qc = compute_quote(10, None, [("Gulab Jamun", 2)], None, pb, _menu())
    assert qc.lines[0].unit_cents == 450 and qc.lines[0].source == "override"
    # Every line came from the pricebook, so the quote is exact, not estimated.
    assert qc.price_status == "exact"


def test_below_package_min_guests_flags_without_blocking():
    qc = compute_quote(10, "veg-premium", [], None, _pricebook(), _menu())
    assert "below_package_min_guests:veg-premium" in qc.flags
    assert qc.total_cents == 2900 * 10  # still priced; the owner decides


# ── fees ─────────────────────────────────────────────────────────────────────
def _fee_pricebook(**over):
    return _pricebook(fixed_fees=[
        CateringFee(id="delivery", name="Delivery", kind="delivery",
                    amount_cents=2500, per_unit="flat"),
        CateringFee(id="setup", name="Setup", kind="setup", amount_cents=5000),
        CateringFee(id="server", name="Server", kind="staffing",
                    amount_cents=12000, per_unit="per_staff"),
        CateringFee(id="retired", name="Retired fee", kind="other",
                    amount_cents=9900, active=False),
    ], **over)


def test_flat_fees_apply_and_unset_per_unit_is_treated_as_flat():
    qc = compute_quote(30, "veg-standard", [], None, _fee_pricebook(), _menu(),
                       fee_ids=["delivery", "setup"])
    assert qc.fees_subtotal_cents == 7500
    assert [f.units for f in qc.fees] == [1, 1]


def test_per_staff_fee_multiplies_by_the_supplied_staff_count():
    qc = compute_quote(30, "veg-standard", [], None, _fee_pricebook(), _menu(),
                       fee_ids=["server"], staff_count=3)
    assert qc.fees[0].extended_cents == 36000
    assert qc.fees_subtotal_cents == 36000


def test_per_staff_fee_without_staff_count_is_unpriced_not_dropped():
    """Dropping it would silently understate the total — the exact failure mode
    the never-guess rule exists to prevent."""
    qc = compute_quote(30, "veg-standard", [], None, _fee_pricebook(), _menu(),
                       fee_ids=["server"])
    assert qc.fees[0].extended_cents is None
    assert qc.fees_subtotal_cents == 0
    assert "missing_fee_input:server" in qc.flags
    assert qc.price_status == "pending_owner_review"
    assert qc.is_deliverable() is False


def test_inactive_fee_is_not_applied_and_cannot_be_requested():
    qc = compute_quote(30, "veg-standard", [], None, _fee_pricebook(), _menu())
    assert "retired" not in {f.fee_id for f in qc.fees}
    with pytest.raises(PricingError, match="retired"):
        compute_quote(30, "veg-standard", [], None, _fee_pricebook(), _menu(),
                      fee_ids=["retired"])


# ── tax ──────────────────────────────────────────────────────────────────────
def test_tax_is_basis_points_of_the_discounted_base():
    pb = _pricebook(tax_rate_bps=825)
    qc = compute_quote(100, "veg-standard", [], None, pb, _menu())
    assert qc.subtotal_cents == 180_000
    assert qc.tax_cents == 14_850          # 180000 * 0.0825
    assert qc.total_cents == 194_850


def test_tax_rounds_half_up_on_the_exact_half_cent():
    # taxable 10c at 500bps = exactly 0.5c -> 1c, not 0c (banker's would give 0).
    pb = _pricebook(tax_rate_bps=500, item_price_overrides={"Gulab Jamun": 10})
    qc = compute_quote(1, None, [("Gulab Jamun", 1)], None, pb, _menu())
    assert qc.taxable_cents == 10
    assert qc.tax_cents == 1
    assert qc.total_cents == 11


def test_tax_rounding_edge_cents_across_a_sweep():
    pb = _pricebook(tax_rate_bps=825)
    seen = []
    for cents in (1, 5, 6, 7, 12, 99, 121, 1213):
        book = pb.model_copy(update={"item_price_overrides": {"Gulab Jamun": cents}})
        qc = compute_quote(1, None, [("Gulab Jamun", 1)], None, book, _menu())
        seen.append((cents, qc.tax_cents))
    # Hand-computed half-up expectations for 8.25%.
    assert seen == [(1, 0), (5, 0), (6, 0), (7, 1), (12, 1), (99, 8), (121, 10), (1213, 100)]


def test_zero_tax_rate_adds_nothing():
    qc = compute_quote(20, "veg-standard", [], None, _pricebook(), _menu())
    assert qc.tax_cents == 0 and qc.total_cents == qc.subtotal_cents


# ── discounts ────────────────────────────────────────────────────────────────
def _discount_pricebook(**over):
    return _pricebook(approved_discounts=[
        CateringDiscount(id="repeat", name="Repeat customer", kind="percent",
                         value=1000, requires_owner_code=False),
        CateringDiscount(id="capped", name="Capped promo", kind="percent",
                         value=5000, max_amount_cents=5000, requires_owner_code=False),
        CateringDiscount(id="festival", name="Festival", kind="fixed_cents",
                         value=7500, requires_owner_code=True),
        CateringDiscount(id="expired", name="Expired", kind="percent", value=2000,
                         active=False),
    ], **over)


def test_percent_discount_is_basis_points_of_subtotal():
    qc = compute_quote(100, "veg-standard", [], "repeat", _discount_pricebook(), _menu())
    assert qc.subtotal_cents == 180_000
    assert qc.discount_cents == 18_000
    assert qc.taxable_cents == 162_000 == qc.total_cents


def test_fixed_cents_discount_subtracts_verbatim():
    qc = compute_quote(100, "veg-standard", [], "festival",
                       _discount_pricebook(), _menu())
    assert qc.discount_cents == 7500
    assert qc.total_cents == 172_500


def test_percent_discount_is_capped_by_max_amount_cents():
    qc = compute_quote(100, "veg-standard", [], "capped", _discount_pricebook(), _menu())
    assert qc.discount_cents == 5000        # 50% of $1800 capped at $50
    assert qc.total_cents == 175_000


def test_discount_never_exceeds_the_subtotal():
    pb = _discount_pricebook(item_price_overrides={"Gulab Jamun": 100})
    qc = compute_quote(1, None, [("Gulab Jamun", 1)], "festival", pb, _menu())
    assert qc.discount_cents == 100         # $75 discount clipped to a $1 subtotal
    assert qc.total_cents == 0


def test_owner_code_requirement_is_surfaced_as_a_flag():
    qc = compute_quote(100, "veg-standard", [], "festival",
                       _discount_pricebook(), _menu())
    assert "discount_requires_owner_code:festival" in qc.flags


def test_unknown_discount_id_is_a_hard_error():
    with pytest.raises(PricingError, match="unknown discount_id"):
        compute_quote(50, "veg-standard", [], "does-not-exist",
                      _discount_pricebook(), _menu())


def test_inactive_discount_id_is_a_hard_error_not_a_silent_skip():
    """Silently ignoring it overcharges; silently applying it undercharges.
    Both are wrong-money, so the kernel refuses."""
    with pytest.raises(PricingError, match="inactive"):
        compute_quote(50, "veg-standard", [], "expired",
                      _discount_pricebook(), _menu())


def test_tax_applies_after_the_discount():
    pb = _discount_pricebook(tax_rate_bps=1000)
    qc = compute_quote(100, "veg-standard", [], "repeat", pb, _menu())
    assert qc.taxable_cents == 162_000
    assert qc.tax_cents == 16_200
    assert qc.total_cents == 178_200


# ── never-guess: unresolvable prices ─────────────────────────────────────────
def test_missing_price_is_kept_unpriced_and_blocks_delivery():
    qc = compute_quote(10, None, [("Samosa", 2), ("Chef's Whim", 1)], None,
                       _pricebook(), _menu())
    whim = next(ln for ln in qc.lines if ln.name == "Chef's Whim")
    assert whim.unit_cents is None and whim.extended_cents is None and whim.source is None
    assert "missing_price:Chef's Whim" in qc.flags
    assert qc.price_status == "pending_owner_review"
    assert qc.is_deliverable() is False
    # The priced sibling still contributes; the unpriced one contributes NOTHING
    # rather than a guessed or zero-valued number.
    assert qc.items_subtotal_cents == 500


def test_item_absent_from_the_menu_entirely_is_unpriced_not_invented():
    qc = compute_quote(10, None, [("Lobster Thermidor", 3)], None,
                       _pricebook(), _menu())
    assert qc.lines[0].unit_cents is None
    assert "missing_price:Lobster Thermidor" in qc.flags
    assert qc.total_cents == 0


def test_unavailable_menu_item_is_flagged_and_unpriced():
    qc = compute_quote(10, None, [("Seasonal Special", 1)], None,
                       _pricebook(), _menu())
    assert "unavailable_item:Seasonal Special" in qc.flags
    assert "missing_price:Seasonal Special" in qc.flags
    assert qc.lines[0].unit_cents is None


def test_override_prices_an_item_the_menu_marks_unavailable():
    pb = _pricebook(item_price_overrides={"Seasonal Special": 999})
    qc = compute_quote(10, None, [("Seasonal Special", 1)], None, pb, _menu())
    assert qc.lines[0].unit_cents == 999 and qc.lines[0].source == "override"
    assert qc.price_status == "exact"


# ── placeholder pricebook ────────────────────────────────────────────────────
def test_placeholder_pricebook_forces_pending_owner_review():
    pb = _pricebook(placeholder=True)
    assert pb.is_placeholder() is True
    qc = compute_quote(100, "veg-standard", [], None, pb, _menu())
    assert "placeholder_pricebook" in qc.flags
    assert qc.price_status == "pending_owner_review"
    assert qc.is_deliverable() is False
    # The arithmetic still runs — the owner needs to SEE the shape of the quote.
    assert qc.total_cents == 180_000


def test_shipped_seed_template_is_marked_placeholder():
    tmpl = (Path(__file__).resolve().parent.parent / "src" / "agents" / "catering"
            / "templates" / "catering-pricebook-template.json")
    book = CateringPricebook.model_validate(json.loads(tmpl.read_text(encoding="utf-8")))
    assert book.is_placeholder() is True, "the seed template must never ship deliverable"
    names = [p.name for p in book.per_person_packages] + [f.name for f in book.fixed_fees]
    assert all(n.startswith("EXAMPLE") for n in names), names


# ── per-guest floor ──────────────────────────────────────────────────────────
def test_below_min_per_guest_floor_flags_the_unscaled_basket():
    """A $50 basket against 200 guests is $0.25/guest — the BL-CATER-03 shape."""
    pb = _pricebook(item_price_overrides={"Gulab Jamun": 5000})
    qc = compute_quote(200, None, [("Gulab Jamun", 1)], None, pb, _menu(),
                       min_per_guest_usd=3.0)
    assert "below_min_per_guest" in qc.flags


def test_floor_not_flagged_when_the_quote_clears_it():
    qc = compute_quote(100, "veg-standard", [], None, _pricebook(), _menu(),
                       min_per_guest_usd=3.0)
    assert "below_min_per_guest" not in qc.flags


def test_floor_is_cents_exact_at_the_boundary():
    # $3.00/guest exactly is NOT below the $3.00 floor.
    pb = _pricebook(item_price_overrides={"Gulab Jamun": 300})
    qc = compute_quote(10, None, [("Gulab Jamun", 10)], None, pb, _menu(),
                       min_per_guest_usd=3.0)
    assert qc.total_cents == 3000
    assert "below_min_per_guest" not in qc.flags
    qc_short = compute_quote(10, None, [("Gulab Jamun", 10)], None, pb, _menu(),
                             min_per_guest_usd=3.01)
    assert "below_min_per_guest" in qc_short.flags


def test_floor_check_is_skipped_when_no_minimum_is_configured():
    pb = _pricebook(item_price_overrides={"Gulab Jamun": 1})
    qc = compute_quote(200, None, [("Gulab Jamun", 1)], None, pb, _menu())
    assert "below_min_per_guest" not in qc.flags


# ── input refusals ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [0, -1, -100, True, 2.5, None, "50"])
def test_guest_count_must_be_a_positive_int(bad):
    with pytest.raises(PricingError, match="guest_count"):
        compute_quote(bad, None, [("Samosa", 1)], None, _pricebook(), _menu())


@pytest.mark.parametrize("bad_qty", [0, -3, 1.5, None, "2"])
def test_line_item_qty_must_be_a_positive_int(bad_qty):
    with pytest.raises(PricingError, match="qty"):
        compute_quote(10, None, [("Samosa", bad_qty)], None, _pricebook(), _menu())


def test_unknown_package_id_is_a_hard_error():
    with pytest.raises(PricingError, match="unknown package_id"):
        compute_quote(50, "no-such-package", [], None, _pricebook(), _menu())


def test_inactive_package_id_is_a_hard_error():
    with pytest.raises(PricingError, match="inactive"):
        compute_quote(50, "veg-retired", [], None, _pricebook(), _menu())


def test_malformed_line_item_shape_is_refused():
    with pytest.raises(PricingError):
        compute_quote(10, None, ["just-a-string"], None, _pricebook(), _menu())


def test_line_items_accept_dict_shape():
    qc = compute_quote(10, None, [{"name": "Samosa", "qty": 2}], None,
                       _pricebook(), _menu())
    assert qc.items_subtotal_cents == 500


# ── currency (binding ruling: INR must fail loud) ────────────────────────────
def test_inr_pricebook_fails_validation_with_an_explaining_message():
    with pytest.raises(ValidationError) as exc:
        _pricebook(currency="INR")
    msg = str(exc.value)
    assert "INR" in msg
    assert "USD-only" in msg, "the error must say WHY, not just 'expected USD'"


@pytest.mark.parametrize("code", ["INR", "EUR", "GBP", "inr", "usd "])
def test_only_usd_is_accepted(code):
    if code.strip().upper() == "USD":
        assert _pricebook(currency=code).currency == "USD"
        return
    with pytest.raises(ValidationError):
        _pricebook(currency=code)


def test_a_valid_pricebook_stamps_usd_on_every_computation():
    assert compute_quote(20, "veg-standard", [], None, _pricebook(), _menu()).currency == "USD"


# ── determinism ──────────────────────────────────────────────────────────────
def test_identical_inputs_serialize_identically():
    pb = _fee_pricebook(tax_rate_bps=825, item_price_overrides={"Gulab Jamun": 450},
                        approved_discounts=[
                            CateringDiscount(id="repeat", name="Repeat", kind="percent",
                                             value=1000, requires_owner_code=False)])
    args = (60, "veg-premium", [("Gulab Jamun", 8), ("Samosa", 4)], "repeat", pb, _menu())
    kwargs = dict(fee_ids=["delivery", "server"], staff_count=2, min_per_guest_usd=3.0)
    first = compute_quote(*args, **kwargs).model_dump_json()
    second = compute_quote(*args, **kwargs).model_dump_json()
    assert first == second
    # And the serialized form round-trips back to an equal model.
    assert QuoteComputation.model_validate_json(first) == QuoteComputation.model_validate_json(second)


def test_computation_carries_no_timestamp_field():
    """A clock read inside the kernel would break replay determinism."""
    fields = set(QuoteComputation.model_fields)
    assert not {f for f in fields if "_at" in f or f in {"ts", "timestamp", "now"}}


def test_provenance_versions_are_stamped():
    qc = compute_quote(20, "veg-standard", [], None, _pricebook(), _menu())
    assert qc.pricebook_version == 4
    assert qc.menu_version == 7


# ── render ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["exact", "estimated", "pending_owner_review"])
def test_price_status_line_always_carries_the_grep_prefix(status):
    assert price_status_line(status).startswith(PRICE_STATUS_PREFIX)


def test_render_always_ends_with_the_price_status_label():
    qc = compute_quote(50, "veg-standard", [("Gulab Jamun", 4)], None,
                       _pricebook(tax_rate_bps=825), _menu())
    text = render_quote_lines(qc)
    assert text.splitlines()[-1].startswith(PRICE_STATUS_PREFIX)
    assert "$900.00" in text and "Total:" in text


def test_render_states_an_unpriced_line_instead_of_showing_a_number():
    qc = compute_quote(10, None, [("Chef's Whim", 2)], None, _pricebook(), _menu())
    text = render_quote_lines(qc)
    assert "Chef's Whim × 2 — price to be confirmed by owner" in text
    assert f"{PRICE_STATUS_PREFIX} pending owner review" in text


def test_render_shows_fees_and_discount_lines():
    pb = _fee_pricebook(approved_discounts=[
        CateringDiscount(id="repeat", name="Repeat", kind="percent", value=1000,
                         requires_owner_code=False)])
    qc = compute_quote(50, "veg-standard", [], "repeat", pb, _menu(),
                       fee_ids=["delivery"])
    text = render_quote_lines(qc)
    assert "Fees:" in text and "Delivery — $25.00" in text
    assert "Discount (Repeat): -$" in text


# ── default_basket (the --auto-default fix) ──────────────────────────────────
def test_default_basket_prefers_the_cheapest_eligible_package():
    package_id, lines = default_basket(50, _pricebook(), _menu())
    assert package_id == "veg-standard"     # premium needs 40+, standard is cheaper
    assert lines == []


def test_default_basket_skips_packages_whose_min_guests_exceeds_headcount():
    package_id, lines = default_basket(10, _pricebook(), _menu())
    assert package_id is None               # both packages need 20+
    assert lines, "must fall back to scaled menu items"


def test_default_basket_ignores_inactive_packages():
    pb = _pricebook(per_person_packages=[
        CateringPackage(id="veg-retired", name="Retired", price_per_person_cents=900,
                        min_guests=1, active=False)])
    assert default_basket(50, pb, _menu())[0] is None


def test_default_basket_scales_quantities_by_headcount_and_serves():
    """Pre-M2 this returned qty=1 per item regardless of headcount."""
    _pkg, lines = default_basket(50, None, _menu())
    assert dict(lines) == {"Samosa": 10, "Gulab Jamun": 5, "Paneer Tikka": 7}


def test_default_basket_assumes_one_unit_per_guest_without_a_serves_hint():
    menu = _menu(items=[MenuItem(name="Thali", price_usd=15.0, category="main")])
    assert default_basket(40, None, menu)[1] == [("Thali", 40)]


def test_default_basket_clamps_quantity_to_the_state_schema_bound():
    menu = _menu(items=[MenuItem(name="Thali", price_usd=15.0, category="main")])
    assert default_basket(10000, None, menu)[1] == [("Thali", MAX_LINE_QTY)]


def test_default_basket_excludes_unpriced_and_unavailable_items():
    _pkg, lines = default_basket(20, None, _menu())
    names = {n for n, _q in lines}
    assert "Chef's Whim" not in names and "Seasonal Special" not in names


def test_default_basket_includes_an_override_priced_item_the_menu_leaves_unpriced():
    pb = _pricebook(per_person_packages=[], item_price_overrides={"Chef's Whim": 800})
    _pkg, lines = default_basket(20, pb, _menu())
    assert "Chef's Whim" in {n for n, _q in lines}


def test_default_basket_rejects_a_non_positive_headcount():
    with pytest.raises(PricingError, match="guest_count"):
        default_basket(0, _pricebook(), _menu())


# ── load_pricebook (the only I/O in the module) ──────────────────────────────
def test_load_pricebook_returns_none_when_absent_or_empty(tmp_path):
    assert load_pricebook(tmp_path / "nothing.json") is None
    empty = tmp_path / "empty.json"
    empty.write_text("  \n", encoding="utf-8")
    assert load_pricebook(empty) is None


def test_load_pricebook_accepts_both_on_disk_shapes(tmp_path):
    book = _pricebook()
    bare = tmp_path / "bare.json"
    bare.write_text(book.model_dump_json(), encoding="utf-8")
    assert load_pricebook(bare).version == 4

    enveloped = tmp_path / "store.json"
    enveloped.write_text(
        json.dumps({"schema_version": 1, "pricebook": json.loads(book.model_dump_json())}),
        encoding="utf-8")
    assert load_pricebook(enveloped).version == 4


def test_load_pricebook_reads_the_committed_fixture(tmp_path):
    fixture = Path(__file__).resolve().parent / "fixtures" / "catering_pricebook_valid.json"
    book = load_pricebook(fixture)
    assert book.version == 4 and book.tax_rate_bps == 825
    assert book.item_price_overrides == {"Gulab Jamun": 450}


@pytest.mark.parametrize("body", ["{{{corrupt", '["a", "list"]', '{"version": "nope"}'])
def test_load_pricebook_raises_rather_than_degrading_to_no_pricebook(tmp_path, body):
    """Returning None here would silently revert callers to pre-M2 flat pricing,
    dropping the owner's fees and tax out of a real quote."""
    p = tmp_path / "bad.json"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(PricingError):
        load_pricebook(p)


def test_load_pricebook_never_quarantines_the_operators_file(tmp_path):
    """safe_io.safe_load_json renames a corrupt file to `.corrupt-<epoch>`. That
    is right for regenerable state and wrong for a hand-maintained pricebook."""
    p = tmp_path / "catering-pricebook.json"
    p.write_text("{{{corrupt", encoding="utf-8")
    with pytest.raises(PricingError):
        load_pricebook(p)
    assert p.read_text(encoding="utf-8") == "{{{corrupt"
    assert list(tmp_path.iterdir()) == [p], "no quarantine copy may be left behind"


def test_load_pricebook_rejects_an_inr_file(tmp_path):
    doc = json.loads(_pricebook().model_dump_json())
    doc["currency"] = "INR"
    p = tmp_path / "inr.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PricingError, match="USD-only"):
        load_pricebook(p)


def test_scaled_basket_clears_the_per_guest_floor_that_the_old_basket_failed():
    """End-to-end shape of the BL-CATER-03 regression: the same menu and the
    same 200 guests, priced the old way vs the new way."""
    menu = _menu()
    old_style = compute_quote(200, None, [("Samosa", 1), ("Gulab Jamun", 1)], None,
                              _pricebook(), menu, min_per_guest_usd=3.0)
    assert "below_min_per_guest" in old_style.flags

    package_id, lines = default_basket(200, _pricebook(), menu)
    scaled = compute_quote(200, package_id, lines, None, _pricebook(), menu,
                           min_per_guest_usd=3.0)
    assert "below_min_per_guest" not in scaled.flags
    assert scaled.total_cents == 1800 * 200
