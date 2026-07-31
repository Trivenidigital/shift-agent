"""M3 — `finalize-catering-menu --package-id`.

M2 built per-person packages into the pricing kernel and into --auto-default's
basket builder, but left no way for a CALLER to name one: the manual-selection
path hard-passed package_id=None, so a finalize driven by an explicit item list
could only ever be flat basket arithmetic — the BL-CATER-03 shape (a 200-guest
event priced as one of each item).

Cross-platform: static + pure-kernel cells. The subprocess/lock behaviour of
finalize is covered by tests/test_catering_finalize_pricebook_wiring.py.
"""
from __future__ import annotations

import py_compile
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_PLATFORM_DIR = REPO / "src" / "platform"
if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

from catering_pricing import (  # noqa: E402
    FLAG_BELOW_PACKAGE_MIN, PricingError, compute_quote,
)
from schemas import CateringPricebook  # noqa: E402

FINALIZE = REPO / "src" / "agents" / "catering" / "scripts" / "finalize-catering-menu"


def _pricebook(**over):
    base = dict(
        version=4, effective_date=date(2026, 7, 1),
        updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        currency="USD", placeholder=False,
        per_person_packages=[
            {"id": "silver", "name": "Silver buffet", "price_per_person_cents": 1800,
             "min_guests": 25, "active": True},
            {"id": "retired", "name": "Old package", "price_per_person_cents": 900,
             "min_guests": 10, "active": False},
        ],
        fixed_fees=[], tax_rate_bps=0, approved_discounts=[],
        item_price_overrides={},
    )
    base.update(over)
    return CateringPricebook(**base)


# ── the flag exists and is wired ────────────────────────────────────────────
def test_finalize_compiles():
    py_compile.compile(str(FINALIZE), doraise=True)


def test_package_id_flag_is_declared_and_reaches_the_kernel():
    src = FINALIZE.read_text(encoding="utf-8")
    assert '"--package-id"' in src
    assert "explicit_package_id" in src
    # The manual-selection branch must actually bind it — a declared-but-unused
    # flag is worse than no flag, because the caller believes it took effect.
    assert "package_id = explicit_package_id or None" in src


def test_package_id_is_refused_rather_than_silently_dropped():
    """Silently ignoring it would quote a number that does not scale with the
    headcount while the caller believes it does."""
    src = FINALIZE.read_text(encoding="utf-8")
    assert "--package-id cannot be combined with --auto-default" in src
    assert "requires a pricebook" in src


def test_a_pricing_refusal_exits_cleanly_instead_of_raising():
    """The SKILL reads stderr and branches on the exit code; a traceback is not a
    branchable outcome."""
    src = FINALIZE.read_text(encoding="utf-8")
    assert "except _pricing.PricingError" in src
    assert "pricing refused this finalize" in src


# ── the kernel behaviour the flag exposes ───────────────────────────────────
def test_a_named_package_scales_with_the_headcount():
    """The whole point: 50 guests x $18 = $900, not a flat basket sum."""
    qc = compute_quote(50, "silver", [], None, _pricebook(), None)
    assert qc.package_id == "silver"
    assert qc.total_cents == 90000


def test_the_same_package_costs_more_for_more_guests():
    small = compute_quote(30, "silver", [], None, _pricebook(), None)
    large = compute_quote(300, "silver", [], None, _pricebook(), None)
    assert large.total_cents == small.total_cents * 10


def test_an_unknown_package_id_raises_rather_than_pricing_without_it():
    with pytest.raises(PricingError) as e:
        compute_quote(50, "platinum", [], None, _pricebook(), None)
    assert "platinum" in str(e.value)


def test_an_inactive_package_id_raises():
    """An unpublished package is not orderable just because it is still in the file."""
    with pytest.raises(PricingError) as e:
        compute_quote(50, "retired", [], None, _pricebook(), None)
    assert "inactive" in str(e.value)


def test_below_minimum_guests_is_flagged_not_refused():
    """The owner may still want to honour it — flag it, do not decide for them."""
    qc = compute_quote(10, "silver", [], None, _pricebook(), None)
    assert f"{FLAG_BELOW_PACKAGE_MIN}:silver" in qc.flags
    assert qc.total_cents == 18000


def test_discounts_stay_out_of_the_finalize_path():
    """Applying a discount is an owner action, wired through the owner-decision
    path — finalize must keep passing None."""
    src = FINALIZE.read_text(encoding="utf-8")
    assert "discounts are an owner action" in src
    assert "--discount-id" not in src
