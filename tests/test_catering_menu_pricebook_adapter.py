"""Unit tests for the pure menu -> pricebook adapter (catering_pricing).

The adapter decides which menu prices are allowed to become COMMITTED
commercial prices. Everything it does is pure, so it is tested directly here:
the cents conversion, each exclusion rule, the carry-forward-verbatim property,
and determinism. The ordered transcript in
tests/test_pricebook_menu_approval_e2e.py proves it composes with the real
scripts; this file proves the rules themselves.

Runs on Linux AND Windows (nothing here touches the filesystem).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "src" / "platform"
for _p in (str(PLATFORM), str(REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import catering_pricing as cp  # noqa: E402
from schemas import CateringPricebook, Menu, MenuItem  # noqa: E402

_TS = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
_DATE = date(2026, 8, 1)


def _sync(items, active=None, update_id="MU0001"):
    return cp.sync_pricebook_from_menu_items(
        items, active, effective_date=_DATE, updated_at=_TS,
        source_menu_update_id=update_id,
    )


def _active_book(**overrides) -> CateringPricebook:
    base = dict(
        version=4, effective_date=date(2026, 7, 1), updated_at=_TS, updated_by="manual",
        placeholder=False,
        per_person_packages=[{"id": "veg", "name": "Veg Buffet",
                              "price_per_person_cents": 1800, "min_guests": 20}],
        fixed_fees=[{"id": "delivery", "name": "Delivery", "kind": "delivery",
                     "amount_cents": 2500, "per_unit": "flat"}],
        tax_rate_bps=825,
        approved_discounts=[{"id": "repeat", "name": "Repeat", "kind": "percent",
                             "value": 500}],
        item_price_overrides={"Gulab Jamun": 450},
        notes="48h lead time",
    )
    base.update(overrides)
    return CateringPricebook(**base)


# ── Decimal -> integer cents ─────────────────────────────────────────────────
@pytest.mark.parametrize("price_usd,expected_cents", [
    (12.0, 1200),
    (14.99, 1499),
    ("14.99", 1499),      # str input: Decimal(str(x)) is the same path
    (4.35, 435),          # the float that becomes 4.3499999999999996 without str()
    (0.01, 1),
    (18.5, 1850),
    (10000.0, 1_000_000),  # MenuItem.price_usd's ceiling
])
def test_menu_dollars_become_exact_integer_cents(price_usd, expected_cents):
    """No float arithmetic anywhere: the adapter reuses the kernel's own
    Decimal(str(x)) * 100, ROUND_HALF_UP conversion."""
    assert cp.usd_to_cents(price_usd) == expected_cents
    if isinstance(price_usd, str):
        # MenuItem.price_usd is Optional[float], so a string never reaches the
        # item pipeline — only the conversion above has to tolerate one.
        return
    overrides, excluded = cp.derive_item_overrides(
        [SimpleNamespace(name="X", price_usd=price_usd)])
    assert overrides == {"X": expected_cents}
    assert excluded == []


@pytest.mark.parametrize("price_usd,sub_cent", [
    (5.999, True),
    (0.005, True),
    (12.345, True),
    (5.99, False),
    ("14.99", False),
    (12.0, False),
])
def test_sub_cent_precision_detection(price_usd, sub_cent):
    assert cp.has_sub_cent_precision(price_usd) is sub_cent


# ── Exclusion rules — exactly these, and no others ───────────────────────────
@pytest.mark.parametrize("item,reason", [
    (SimpleNamespace(name="No Price", price_usd=None), cp.EXCLUDE_MISSING_PRICE),
    (SimpleNamespace(name="Comped", price_usd=0), cp.EXCLUDE_NON_POSITIVE_PRICE),
    # MenuItem.price_usd is ge=0 so a negative cannot reach here through a
    # validated menu; the guard is defense-in-depth against an unvalidated caller.
    (SimpleNamespace(name="Negative", price_usd=-5.0), cp.EXCLUDE_NON_POSITIVE_PRICE),
    (SimpleNamespace(name="OCR Artifact", price_usd=5.999), cp.EXCLUDE_SUB_CENT_PRECISION),
    (SimpleNamespace(name="   ", price_usd=8.0), cp.EXCLUDE_EMPTY_NAME),
    (SimpleNamespace(name="", price_usd=8.0), cp.EXCLUDE_EMPTY_NAME),
])
def test_each_exclusion_rule_keeps_the_item_out_with_its_reason(item, reason):
    overrides, excluded = cp.derive_item_overrides([item])
    assert overrides == {}, "an excluded item must never reach the overrides"
    assert [(e.name, e.reason, e.price_usd) for e in excluded] == [
        (item.name, reason, item.price_usd)]


def test_duplicate_names_with_conflicting_prices_exclude_both():
    """Nothing in the menu says which row is current, so pricing either one would
    be a silent wrong price — the exact failure this bridge exists to prevent."""
    overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="Paneer Tikka", price_usd=12.00),
        MenuItem(name="Paneer Tikka", price_usd=14.00),
        MenuItem(name="Samosa", price_usd=6.00),
    ])
    assert overrides == {"Samosa": 600}
    assert [(e.name, e.reason, e.price_usd) for e in excluded] == [
        ("Paneer Tikka", cp.EXCLUDE_DUPLICATE_CONFLICT, 12.0),
        ("Paneer Tikka", cp.EXCLUDE_DUPLICATE_CONFLICT, 14.0),
    ]


def test_duplicate_names_that_agree_on_price_are_one_override():
    overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="Chai", price_usd=2.50),
        MenuItem(name="Chai", price_usd=2.50),
    ])
    assert overrides == {"Chai": 250}
    assert excluded == []


def test_duplicate_where_one_row_has_no_price_is_still_a_conflict():
    """None vs a number disagree; taking the number would resurrect a price the
    owner may have deliberately cleared."""
    _overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="Chai", price_usd=2.50),
        MenuItem(name="Chai"),
    ])
    assert [e.reason for e in excluded] == [cp.EXCLUDE_DUPLICATE_CONFLICT] * 2


def test_names_are_matched_after_stripping_surrounding_whitespace():
    overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="  Samosa  ", price_usd=6.00),
        MenuItem(name="Samosa", price_usd=7.00),
    ])
    assert overrides == {}
    assert [e.reason for e in excluded] == [cp.EXCLUDE_DUPLICATE_CONFLICT] * 2


def test_one_bad_row_never_costs_the_owner_the_good_ones():
    overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="Idly", price_usd=6.00),
        MenuItem(name="Mystery", price_usd=None),
        MenuItem(name="Dosa", price_usd=10.50),
    ])
    assert overrides == {"Idly": 600, "Dosa": 1050}
    assert [e.name for e in excluded] == ["Mystery"]


def test_unavailable_items_are_excluded_so_they_cannot_quote_as_firm():
    """An override on a sold-out dish is a wrong price in effect.

    compute_quote checks `item_price_overrides` BEFORE it looks at the menu, and
    only the MENU branch raises `unavailable_item` — so an override makes the
    kernel skip the availability check and return a firm, deliverable "exact"
    price for something nobody can cook. Leaving these out keeps them on the menu
    path, where being unavailable still blocks the quote."""
    overrides, excluded = cp.derive_item_overrides(
        [MenuItem(name="Goat Curry", price_usd=18.0, available=False)])
    assert overrides == {}
    assert [(e.name, e.reason) for e in excluded] == [
        ("Goat Curry", cp.EXCLUDE_UNAVAILABLE)]


def test_one_unavailable_row_excludes_the_whole_name():
    """Promoting a price that MIGHT be sold out is the failure this rule stops,
    so any unavailable row in a same-name group excludes the name."""
    overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="Chai", price_usd=2.50, available=True),
        MenuItem(name="Chai", price_usd=2.50, available=False),
    ])
    assert overrides == {}
    assert [e.reason for e in excluded] == [cp.EXCLUDE_UNAVAILABLE]


def test_availability_is_checked_after_the_duplicate_rule():
    """A price conflict is still reported as a conflict — availability must not
    quietly resolve one by knocking out the row that disagrees."""
    _overrides, excluded = cp.derive_item_overrides([
        MenuItem(name="Chai", price_usd=2.50, available=True),
        MenuItem(name="Chai", price_usd=3.00, available=False),
    ])
    assert [e.reason for e in excluded] == [cp.EXCLUDE_DUPLICATE_CONFLICT] * 2


def test_a_sold_out_item_cannot_be_quoted_as_exact_through_the_kernel():
    """The end-to-end property the exclusion exists for, asserted against the
    REAL kernel rather than the adapter's own output."""
    items = [MenuItem(name="Goat Curry", price_usd=18.0, available=False)]
    menu = Menu(version=1, updated_at=_TS, items=items)
    sync = _sync(items, _active_book())

    quote = cp.compute_quote(10, None, [("Goat Curry", 2)], None, sync.pricebook, menu)

    assert quote.price_status != "exact"
    assert quote.price_status == "pending_owner_review"
    assert quote.is_deliverable() is False
    assert "unavailable_item:Goat Curry" in quote.flags
    assert quote.lines[0].unit_cents is None, "a sold-out line is unpriced, not firm"


# ── Carry-forward-verbatim property ──────────────────────────────────────────
def test_commercial_fields_are_carried_forward_verbatim():
    """The bridge supplies ITEM prices, never the business model. Everything the
    owner configured commercially must survive a menu approval untouched."""
    active = _active_book()
    sync = _sync([MenuItem(name="Idly", price_usd=6.00)], active)

    carried = sync.pricebook.model_dump(exclude={
        "version", "updated_at", "updated_by", "item_price_overrides",
        "source_menu_update_id",
    })
    original = active.model_dump(exclude={
        "version", "updated_at", "updated_by", "item_price_overrides",
        "source_menu_update_id",
    })
    assert carried == original
    assert sync.pricebook.item_price_overrides == {"Idly": 600}
    assert sync.pricebook.source_menu_update_id == "MU0001"
    assert sync.pricebook.updated_by == "menu_approval"


def test_a_placeholder_pricebook_stays_placeholder_through_a_menu_approval():
    """Real item prices do not make seed packages/fees real. Clearing the flag
    here would quietly make an undeliverable quote deliverable."""
    sync = _sync([MenuItem(name="Idly", price_usd=6.00)], _active_book(placeholder=True))
    assert sync.pricebook.placeholder is True


def test_with_no_active_pricebook_the_document_is_prices_and_nothing_else():
    sync = _sync([MenuItem(name="Idly", price_usd=6.00)], None)
    book = sync.pricebook
    assert book.per_person_packages == [] and book.fixed_fees == []
    assert book.approved_discounts == [] and book.tax_rate_bps == 0
    assert book.effective_date == _DATE
    assert book.item_price_overrides == {"Idly": 600}


def test_a_first_pricebook_with_no_commercial_terms_is_flagged_placeholder():
    """Item prices alone are not a quote. Without the flag, one menu photo
    silently promotes quoting from "estimated" to a firm "exact" that charges
    0% tax and no delivery fee."""
    assert _sync([MenuItem(name="Idly", price_usd=6.00)], None).pricebook.placeholder is True


def test_a_first_activation_quote_stays_pending_owner_review():
    """The end-to-end property the flag exists for, through the REAL kernel."""
    items = [MenuItem(name="Idly", price_usd=6.00)]
    menu = Menu(version=1, updated_at=_TS, items=items)
    sync = _sync(items, None)

    quote = cp.compute_quote(10, None, [("Idly", 2)], None, sync.pricebook, menu)

    assert quote.price_status == "pending_owner_review"
    assert quote.is_deliverable() is False
    assert cp.FLAG_PLACEHOLDER_PRICEBOOK in quote.flags
    # The price itself DID land — only the permission to quote it as final did not.
    assert quote.lines[0].unit_cents == 600


def test_a_real_pricebook_is_not_turned_into_a_placeholder():
    """The flag is carried, not invented: a configured pricebook keeps quoting
    firm through a menu approval."""
    assert _sync([MenuItem(name="Idly", price_usd=6.00)],
                 _active_book()).pricebook.placeholder is False


# ── Diff + announced version ─────────────────────────────────────────────────
def test_diff_reports_added_changed_and_removed_name_sorted():
    changes = cp.diff_item_overrides(
        {"Kept": 500, "Changed": 600, "Gone": 700},
        {"Kept": 500, "Changed": 650, "New": 800},
    )
    assert [(c.name, c.change, c.old_cents, c.new_cents) for c in changes] == [
        ("Changed", "changed", 600, 650),
        ("Gone", "removed", 700, None),
        ("New", "added", None, 800),
    ]


def test_an_item_that_left_the_menu_is_reported_as_removed_not_dropped_silently():
    sync = _sync([MenuItem(name="Idly", price_usd=6.00)], _active_book())
    assert ("Gulab Jamun", "removed", 450, None) in [
        (c.name, c.change, c.old_cents, c.new_cents) for c in sync.changes]
    assert "Gulab Jamun" not in sync.pricebook.item_price_overrides


def test_the_announced_version_matches_the_importers_own_rule():
    """The card promises a version number; the importer assigns
    max(prior, incoming) + 1. If these two disagree the owner is told a lie."""
    assert _sync([MenuItem(name="A", price_usd=1.0)], _active_book(version=4)).next_version == 5
    assert _sync([MenuItem(name="A", price_usd=1.0)], _active_book(version=11)).next_version == 12
    # No active pricebook: prior=0 and the document's default version is 1, so
    # max(0, 1) + 1 = 2. This is the deployed importer rule, not a new one.
    assert _sync([MenuItem(name="A", price_usd=1.0)], None).next_version == 2


# ── Purity ───────────────────────────────────────────────────────────────────
def test_the_adapter_is_pure_same_input_gives_identical_output():
    """Called once to render the owner card and again to build the document the
    importer receives. If those two disagree, the owner approved something else."""
    items = [
        MenuItem(name="Idly", price_usd=6.00),
        MenuItem(name="Chai", price_usd=5.999),
        MenuItem(name="Paneer Tikka", price_usd=12.00),
        MenuItem(name="Paneer Tikka", price_usd=14.00),
    ]
    active = _active_book()
    first = _sync(items, active)
    second = _sync(items, active)

    assert first.pricebook.model_dump_json() == second.pricebook.model_dump_json()
    assert first.excluded == second.excluded
    assert first.changes == second.changes
    assert first.next_version == second.next_version


def test_the_adapter_does_not_mutate_the_active_pricebook():
    active = _active_book()
    before = active.model_dump_json()
    _sync([MenuItem(name="Idly", price_usd=6.00)], active)
    assert active.model_dump_json() == before


# ── Owner card rendering ─────────────────────────────────────────────────────
def test_the_card_states_the_changes_the_exclusions_and_the_version():
    active = _active_book()
    sync = _sync([
        MenuItem(name="Idly", price_usd=6.00),
        MenuItem(name="Gulab Jamun", price_usd=5.00),
        MenuItem(name="Chai", price_usd=5.999),
    ], active)
    text = cp.render_pricebook_activation_section(sync, active)

    assert "+ Idly — $6.00" in text
    assert "~ Gulab Jamun — $4.50 → $5.00" in text
    assert "Chai — price has fractions of a cent" in text
    assert "activates pricebook version 5" in text


def test_the_card_says_what_happens_when_there_is_no_pricebook_yet():
    sync = _sync([MenuItem(name="Idly", price_usd=6.00)], None)
    text = cp.render_pricebook_activation_section(sync, None)
    assert "NO packages, NO fees and 0% tax" in text
    # The owner must be told the prices land but quotes do NOT go out as final.
    assert "pending owner review" in text
    assert "NOT sent as final" in text
    assert "activates pricebook version 2" in text


# ── Proposal-window fingerprint ──────────────────────────────────────────────
def test_the_fingerprint_distinguishes_no_pricebook_from_an_unchecked_proposal():
    """`"none"` is a real, comparable value. An ABSENT stamp (None) means the
    proposal predates the field and cannot be checked — the two must not be
    confusable, or a first-ever approval would look unverifiable."""
    assert cp.pricebook_fingerprint(None) == "none"
    assert cp.pricebook_fingerprint(_active_book()) != "none"


def test_the_fingerprint_changes_when_the_pricebook_does():
    base = _active_book(version=4)
    assert cp.pricebook_fingerprint(base) == cp.pricebook_fingerprint(_active_book(version=4))
    assert cp.pricebook_fingerprint(base) != cp.pricebook_fingerprint(_active_book(version=5))
    bumped = _active_book(version=4, updated_at=datetime(2026, 8, 2, tzinfo=timezone.utc))
    assert cp.pricebook_fingerprint(base) != cp.pricebook_fingerprint(bumped), (
        "a re-import at the same version still moves updated_at")
