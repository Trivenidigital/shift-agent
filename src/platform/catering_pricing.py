"""catering_pricing — the deterministic catering quote kernel (M2).

ONE function decides what a catering event costs: `compute_quote`. It is PURE —
no filesystem, no network, no clock, no LLM. Identical inputs always produce a
byte-identical `QuoteComputation.model_dump_json()`, which is what makes the
quote auditable and replayable (timestamps are applied by callers, never here).

Why this module exists
----------------------
Before M2 the only quote arithmetic in catering was
`finalize-catering-menu._cross_check_and_recompute` — a flat
`sum(qty * round(menu.price_usd))`. It had NO headcount multiplier, so the
`--auto-default` basket priced a 200-guest wedding as "first 5 menu items,
qty=1" (~$50 → $0.25/guest): the BL-CATER-03 unscaled-basket bug. There were
also no packages, fees, tax, discounts or gratuity anywhere in catering.

Money discipline
----------------
INTEGER CENTS end to end, `decimal.Decimal` + ROUND_HALF_UP for every rate
multiplication (mirrors deposit.py:_compute_deposit_amount_cents). No float
accumulation ever touches a total.

Never-guess discipline
----------------------
A referenced item with no resolvable price is NOT dropped, NOT zeroed and NOT
estimated. It stays on the quote with `unit_cents=None`, raises a
`missing_price:<name>` flag, and forces `price_status="pending_owner_review"`.
The same rule covers a fee whose multiplier (staff count / miles) was not
supplied. A placeholder (seed) pricebook forces the same status so proposal
delivery can hard-refuse on it.

Hard errors (raise `PricingError`) are reserved for inputs the caller got
wrong and must fix: guest_count < 1, qty < 1, and unknown/inactive package or
discount ids. A silently-ignored discount id would overcharge the customer;
a silently-applied unknown one would undercharge. Both are refusals.

Deployed FLAT to /opt/shift-agent/ (same contract as catering_quote_ledger).
"""
from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from schemas import (
    CateringDiscount,
    CateringFee,
    CateringPackage,
    CateringPriceStatus,
    CateringPricebook,
    CateringPricebookStore,
    CateringPricingInputs,
    CateringQuoteFeeInput,
    CateringQuoteLineInput,
    Menu,
)

# Customer-visible price-status labels. The PREFIX is grep-enforced by
# tests/test_catering_price_status_label.py — every price-bearing template and
# render path must emit a line starting with it, so no priced text can ever
# reach a customer without saying how firm the number is.
PRICE_STATUS_PREFIX = "Price status:"
PRICE_STATUS_LABELS: dict[str, str] = {
    "exact": "exact",
    "estimated": "estimated — pending owner validation",
    "pending_owner_review": "pending owner review — not a final quote",
}

# Flag prefixes (stable strings; callers gate on these).
FLAG_PLACEHOLDER_PRICEBOOK = "placeholder_pricebook"
FLAG_BELOW_MIN_PER_GUEST = "below_min_per_guest"
FLAG_MISSING_PRICE = "missing_price"           # missing_price:<item name>
FLAG_UNAVAILABLE_ITEM = "unavailable_item"     # unavailable_item:<item name>
FLAG_MISSING_FEE_INPUT = "missing_fee_input"   # missing_fee_input:<fee id>
FLAG_BELOW_PACKAGE_MIN = "below_package_min_guests"   # ...:<package id>
FLAG_DISCOUNT_NEEDS_CODE = "discount_requires_owner_code"  # ...:<discount id>
# Raised by finalize, not by the kernel: the headcount was unknown, so the quote
# was priced for one guest and cannot be checked against the event. Named here
# because a recompute has to know it is a block it can never clear on its own.
FLAG_UNKNOWN_GUEST_COUNT = "unknown_guest_count"

# Flags the kernel derives afresh on every call. A COMMITTED one must not be
# carried into a recompute: the owner supplying a staff count is precisely how a
# `missing_fee_input` block is meant to clear, and carrying the stale flag would
# keep the quote blocked forever. Anything NOT here (a finalize-local flag, say)
# is carried, because nothing else will re-derive it.
_KERNEL_DERIVED_FLAGS = (
    FLAG_PLACEHOLDER_PRICEBOOK,
    FLAG_BELOW_MIN_PER_GUEST,
    FLAG_MISSING_PRICE,
    FLAG_MISSING_FEE_INPUT,
    FLAG_BELOW_PACKAGE_MIN,
    FLAG_DISCOUNT_NEEDS_CODE,
)

# CateringSelectedItem.qty is bounded at 500 by the state schema; a
# headcount-scaled default basket clamps to it rather than emitting an
# unpersistable quantity.
MAX_LINE_QTY = 500


class PricingError(ValueError):
    """Caller-side input error: the quote cannot be computed as asked.

    Distinct from an UNPRICEABLE line (which is representable — see
    `missing_price` — and never raises)."""


# ── Rounding primitives (integer cents; ROUND_HALF_UP everywhere) ────────────
def _round_half_up(value: Decimal) -> int:
    """Round a Decimal to the nearest integer, halves away from zero.

    Python's built-in round() is banker's rounding; for money we want half-up
    (mirrors deposit.py:_compute_deposit_amount_cents)."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def usd_to_cents(price_usd: float | int) -> int:
    """Menu float dollars -> integer cents. `str()` first so 4.35 does not
    arrive as 4.3499999999999996."""
    return _round_half_up(Decimal(str(price_usd)) * 100)


def cents_to_whole_dollars(cents: int) -> int:
    """Integer cents -> whole dollars, half-up. Bridges the kernel back to the
    int-dollar pipeline (CateringSelectedItem.price_usd, quote_total_usd)."""
    return _round_half_up(Decimal(int(cents)) / 100)


def _apply_bps(amount_cents: int, bps: int) -> int:
    """amount * bps / 10000, half-up, integer cents."""
    return _round_half_up(Decimal(int(amount_cents)) * Decimal(int(bps)) / Decimal(10000))


def format_cents(cents: Optional[int]) -> str:
    """'$1,234.56'. None renders as an explicit unknown — never '$0.00'."""
    if cents is None:
        return "$— (not priced)"
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}${whole:,}.{frac:02d}"


# ── Computation result models ────────────────────────────────────────────────
class QuoteLine(BaseModel):
    """One priced line. `lines` is the COMPLETE itemization: a per-person package
    appears as the first line (source='package', qty=guest_count, unit=the
    per-person price), followed by à-la-carte lines.

    `unit_cents`/`extended_cents` are None ONLY when the price could not be
    resolved — the line still appears, flagged, never dropped or zeroed."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    qty: int = Field(ge=1)
    unit_cents: Optional[int] = Field(default=None, ge=0)
    extended_cents: Optional[int] = Field(default=None, ge=0)
    source: Optional[str] = Field(
        default=None,
        description="'package' | 'menu' | 'override'; None when unresolved",
    )


class QuoteFeeLine(BaseModel):
    """One applied fee. `extended_cents` is None when the multiplier the fee
    needs (staff count / miles) was not supplied."""
    model_config = ConfigDict(extra="forbid")
    fee_id: str
    name: str
    kind: str
    per_unit: Optional[str] = None
    unit_cents: int = Field(ge=0)
    units: Optional[int] = Field(default=None, ge=0)
    extended_cents: Optional[int] = Field(default=None, ge=0)


class QuoteComputation(BaseModel):
    """The complete, deterministic breakdown of one quote. Pure data: it holds
    no timestamp, so two identical computations serialize identically."""
    model_config = ConfigDict(extra="forbid")

    guest_count: int = Field(ge=1)
    # Per-person package (None for pure à-la-carte)
    package_id: Optional[str] = None
    package_name: Optional[str] = None
    price_per_person_cents: Optional[int] = Field(default=None, ge=0)
    per_person_subtotal_cents: int = Field(default=0, ge=0)
    # À-la-carte lines + fees
    lines: list[QuoteLine] = Field(default_factory=list)
    items_subtotal_cents: int = Field(default=0, ge=0)
    fees: list[QuoteFeeLine] = Field(default_factory=list)
    fees_subtotal_cents: int = Field(default=0, ge=0)
    # Money roll-up
    subtotal_cents: int = Field(default=0, ge=0)
    discount_id: Optional[str] = None
    discount_name: Optional[str] = None
    discount_cents: int = Field(default=0, ge=0)
    taxable_cents: int = Field(default=0, ge=0)
    tax_rate_bps: int = Field(default=0, ge=0, le=10000)
    tax_cents: int = Field(default=0, ge=0)
    total_cents: int = Field(default=0, ge=0)
    # Provenance + trust
    pricebook_version: Optional[int] = Field(default=None, ge=1)
    menu_version: Optional[int] = Field(default=None, ge=1)
    currency: str = "USD"
    price_status: CateringPriceStatus = "pending_owner_review"
    flags: list[str] = Field(default_factory=list)

    def is_deliverable(self) -> bool:
        """True iff this computation may be shown to a customer as pricing.

        False for a placeholder pricebook or any unresolved price — the hard
        refusal the placeholder flag exists to drive."""
        return self.price_status != "pending_owner_review"

    def total_per_guest_cents(self) -> int:
        return _round_half_up(Decimal(self.total_cents) / Decimal(self.guest_count))


# ── Resolution helpers (pure) ────────────────────────────────────────────────
def _menu_index(menu: Optional[Menu]) -> dict[str, Any]:
    return {it.name: it for it in (menu.items if menu is not None else [])}


def _resolve_package(
    pricebook: CateringPricebook, package_id: Optional[str],
) -> Optional[CateringPackage]:
    if package_id is None:
        return None
    for pkg in pricebook.per_person_packages:
        if pkg.id == package_id:
            if not pkg.active:
                raise PricingError(
                    f"package {package_id!r} is inactive in pricebook "
                    f"v{pricebook.version}; refusing to price an unpublished package"
                )
            return pkg
    raise PricingError(
        f"unknown package_id {package_id!r} (pricebook v{pricebook.version} "
        f"defines: {sorted(p.id for p in pricebook.per_person_packages)})"
    )


def resolve_discount(
    pricebook: CateringPricebook, discount_id: Optional[str],
) -> Optional[CateringDiscount]:
    """PUBLIC — the single validator for a discount id against a pricebook.

    Exposed (not `_`-private like its sibling resolvers) because callers need to
    answer "is this id one the owner may apply?" BEFORE deciding anything else
    about the lead: an id the owner just mistyped is the most actionable thing to
    tell them, and it is true regardless of what state the lead is in.
    """
    if discount_id is None:
        return None
    for disc in pricebook.approved_discounts:
        if disc.id == discount_id:
            if not disc.active:
                raise PricingError(
                    f"discount {discount_id!r} is inactive in pricebook "
                    f"v{pricebook.version}; refusing to apply it"
                )
            return disc
    raise PricingError(
        f"unknown discount_id {discount_id!r} (pricebook v{pricebook.version} "
        f"approves: {sorted(d.id for d in pricebook.approved_discounts)})"
    )


def _selected_fees(
    pricebook: CateringPricebook, fee_ids: Optional[Sequence[str]],
) -> list[CateringFee]:
    active = [f for f in pricebook.fixed_fees if f.active]
    if fee_ids is None:
        return active
    by_id = {f.id: f for f in active}
    out: list[CateringFee] = []
    for fid in fee_ids:
        fee = by_id.get(fid)
        if fee is None:
            raise PricingError(
                f"unknown or inactive fee_id {fid!r} (pricebook v{pricebook.version} "
                f"has active fees: {sorted(by_id)})"
            )
        out.append(fee)
    return out


def _normalize_line_items(line_items: Optional[Iterable[Any]]) -> list[tuple[str, int]]:
    """Accept [(name, qty), ...] as tuples/lists/dicts. Raises on bad shapes."""
    out: list[tuple[str, int]] = []
    for raw in line_items or []:
        if isinstance(raw, dict):
            name, qty = raw.get("name"), raw.get("qty")
        else:
            try:
                name, qty = raw
            except (TypeError, ValueError) as e:
                raise PricingError(f"line item {raw!r} is not a (name, qty) pair") from e
        if not isinstance(name, str) or not name:
            raise PricingError(f"line item name must be a non-empty string; got {name!r}")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            raise PricingError(f"line item {name!r}: qty must be an int >= 1; got {qty!r}")
        out.append((name, qty))
    return out


# ── THE KERNEL ───────────────────────────────────────────────────────────────
def compute_quote(
    guest_count: int,
    package_id: Optional[str],
    line_items: Optional[Sequence[Any]],
    discount_id: Optional[str],
    pricebook: CateringPricebook,
    menu: Optional[Menu] = None,
    *,
    fee_ids: Optional[Sequence[str]] = None,
    staff_count: Optional[int] = None,
    miles: Optional[int] = None,
    min_per_guest_usd: Optional[float] = None,
) -> QuoteComputation:
    """Price one catering event. PURE — deterministic, no I/O, no clock.

    Order of operations (fixed, so the arithmetic is reproducible):
      per-person subtotal -> à-la-carte lines -> fees -> subtotal
      -> discount (capped) -> tax on the discounted base -> total.

    Price resolution per line: pricebook `item_price_overrides` wins over the
    Menu's retail `price_usd`; an unavailable menu item with no override is
    UNPRICED (flagged), never guessed.
    """
    if not isinstance(guest_count, int) or isinstance(guest_count, bool) or guest_count < 1:
        raise PricingError(f"guest_count must be an int >= 1; got {guest_count!r}")

    flags: list[str] = []
    items = _normalize_line_items(line_items)
    overrides = pricebook.item_price_overrides
    index = _menu_index(menu)

    # 1. Per-person package — the first line of the itemization, and the whole
    #    point of M2: this is what makes a quote scale with the guest count.
    pkg = _resolve_package(pricebook, package_id)
    per_person_subtotal = 0
    lines: list[QuoteLine] = []
    if pkg is not None:
        per_person_subtotal = pkg.price_per_person_cents * guest_count
        lines.append(QuoteLine(
            name=pkg.name, qty=guest_count,
            unit_cents=pkg.price_per_person_cents,
            extended_cents=per_person_subtotal, source="package",
        ))
        if guest_count < pkg.min_guests:
            flags.append(f"{FLAG_BELOW_PACKAGE_MIN}:{pkg.id}")

    # 2. À-la-carte lines. `items_subtotal` counts ONLY these — the package's
    #    contribution lives in `per_person_subtotal`, so the two never overlap.
    items_subtotal = 0
    for name, qty in items:
        unit_cents: Optional[int] = None
        source: Optional[str] = None
        if name in overrides:
            unit_cents, source = int(overrides[name]), "override"
        else:
            menu_item = index.get(name)
            if menu_item is not None and menu_item.price_usd is not None and menu_item.available:
                unit_cents, source = usd_to_cents(menu_item.price_usd), "menu"
            elif menu_item is not None and not menu_item.available:
                flags.append(f"{FLAG_UNAVAILABLE_ITEM}:{name}")
        if unit_cents is None:
            flags.append(f"{FLAG_MISSING_PRICE}:{name}")
            lines.append(QuoteLine(name=name, qty=qty, unit_cents=None,
                                   extended_cents=None, source=None))
            continue
        extended = unit_cents * qty
        items_subtotal += extended
        lines.append(QuoteLine(name=name, qty=qty, unit_cents=unit_cents,
                               extended_cents=extended, source=source))

    # 3. Fees. A fee whose multiplier was not supplied stays on the quote
    #    unpriced — dropping it would understate the total silently.
    fees: list[QuoteFeeLine] = []
    fees_subtotal = 0
    for fee in _selected_fees(pricebook, fee_ids):
        per_unit = fee.per_unit or "flat"
        units: Optional[int] = 1
        if per_unit == "per_staff":
            units = staff_count
        elif per_unit == "per_mile":
            units = miles
        if units is None or units < 0:
            flags.append(f"{FLAG_MISSING_FEE_INPUT}:{fee.id}")
            fees.append(QuoteFeeLine(fee_id=fee.id, name=fee.name, kind=fee.kind,
                                     per_unit=per_unit, unit_cents=fee.amount_cents,
                                     units=None, extended_cents=None))
            continue
        extended = fee.amount_cents * units
        fees_subtotal += extended
        fees.append(QuoteFeeLine(fee_id=fee.id, name=fee.name, kind=fee.kind,
                                 per_unit=per_unit, unit_cents=fee.amount_cents,
                                 units=units, extended_cents=extended))

    subtotal = per_person_subtotal + items_subtotal + fees_subtotal

    # 4. Discount — validated against the pricebook, capped, never negative.
    disc = resolve_discount(pricebook, discount_id)
    discount_cents = 0
    if disc is not None:
        if disc.kind == "percent":
            discount_cents = _apply_bps(subtotal, disc.value)
        else:
            discount_cents = int(disc.value)
        if disc.max_amount_cents is not None:
            discount_cents = min(discount_cents, disc.max_amount_cents)
        discount_cents = min(discount_cents, subtotal)
        if disc.requires_owner_code:
            flags.append(f"{FLAG_DISCOUNT_NEEDS_CODE}:{disc.id}")

    # 5. Tax on the discounted base, then total.
    taxable = subtotal - discount_cents
    tax_cents = _apply_bps(taxable, pricebook.tax_rate_bps)
    total = taxable + tax_cents

    # 6. Trust. Placeholder pricing and unresolved prices BOTH block delivery.
    if pricebook.is_placeholder():
        flags.append(FLAG_PLACEHOLDER_PRICEBOOK)
    unresolved = any(f.startswith(FLAG_MISSING_PRICE + ":")
                     or f.startswith(FLAG_MISSING_FEE_INPUT + ":") for f in flags)
    if unresolved or pricebook.is_placeholder():
        price_status: CateringPriceStatus = "pending_owner_review"
    elif any(ln.source == "menu" for ln in lines):
        # A Menu retail price is a culinary-catalog number, not a committed
        # commercial one — honest label is "estimated".
        price_status = "estimated"
    else:
        price_status = "exact"

    # 7. Per-guest plausibility floor (BL-CATER-03 defense, cents-exact).
    if min_per_guest_usd is not None and min_per_guest_usd > 0:
        floor_cents = _round_half_up(Decimal(str(min_per_guest_usd)) * 100)
        if total < floor_cents * guest_count:
            flags.append(FLAG_BELOW_MIN_PER_GUEST)

    return QuoteComputation(
        guest_count=guest_count,
        package_id=(pkg.id if pkg else None),
        package_name=(pkg.name if pkg else None),
        price_per_person_cents=(pkg.price_per_person_cents if pkg else None),
        per_person_subtotal_cents=per_person_subtotal,
        lines=lines,
        items_subtotal_cents=items_subtotal,
        fees=fees,
        fees_subtotal_cents=fees_subtotal,
        subtotal_cents=subtotal,
        discount_id=(disc.id if disc else None),
        discount_name=(disc.name if disc else None),
        discount_cents=discount_cents,
        taxable_cents=taxable,
        tax_rate_bps=pricebook.tax_rate_bps,
        tax_cents=tax_cents,
        total_cents=total,
        pricebook_version=pricebook.version,
        menu_version=(menu.version if menu is not None else None),
        currency=pricebook.currency,
        price_status=price_status,
        flags=flags,
    )


# ── Provenance: freeze a computation, then re-price from the frozen inputs ───
# How firm each status is. A recompute is never allowed to come back FIRMER than
# the commitment it was built on: rebuilding from committed cents makes every
# line an "override", which the kernel would happily call "exact" even though the
# committed numbers were menu-derived estimates.
_PRICE_STATUS_FIRMNESS: dict[str, int] = {
    "pending_owner_review": 0,
    "estimated": 1,
    "exact": 2,
}


def weaker_price_status(a: str, b: str) -> CateringPriceStatus:
    """The LESS firm of two price statuses. Unknown values sort as least firm."""
    if _PRICE_STATUS_FIRMNESS.get(a, 0) <= _PRICE_STATUS_FIRMNESS.get(b, 0):
        return a  # type: ignore[return-value]
    return b  # type: ignore[return-value]


def pricing_inputs_from_quote(
    qc: QuoteComputation,
    pricebook: CateringPricebook,
    *,
    staff_count: Optional[int] = None,
    miles: Optional[int] = None,
) -> CateringPricingInputs:
    """Freeze a computed quote into the cents-exact provenance a lead persists.

    Every à-la-carte line must be priced: an unpriced line cannot be represented
    as a committed cent amount, and a caller that persists one is committing a
    number it never computed. Callers refuse those upstream (finalize exits 2),
    so reaching here with one is a bug, not a data condition — hence PricingError.
    """
    unpriced = [ln.name for ln in qc.lines
                if ln.source != "package" and ln.unit_cents is None]
    if unpriced:
        raise PricingError(
            f"cannot freeze pricing provenance: {unpriced!r} have no resolved "
            "unit price; a committed quote must be cents-exact on every line"
        )
    pkg_min_guests = 1
    if qc.package_id is not None:
        pkg = next((p for p in pricebook.per_person_packages if p.id == qc.package_id), None)
        if pkg is not None:
            pkg_min_guests = pkg.min_guests
    return CateringPricingInputs(
        guest_count=qc.guest_count,
        package_id=qc.package_id,
        package_name=qc.package_name,
        package_price_per_person_cents=qc.price_per_person_cents,
        package_min_guests=pkg_min_guests,
        line_items=[
            CateringQuoteLineInput(name=ln.name, qty=ln.qty, unit_cents=int(ln.unit_cents))
            for ln in qc.lines
            if ln.source != "package" and ln.unit_cents is not None
        ],
        fees=[
            CateringQuoteFeeInput(
                fee_id=fee.fee_id, name=fee.name, kind=fee.kind,
                per_unit=fee.per_unit or "flat",
                unit_cents=fee.unit_cents, units=fee.units,
            )
            for fee in qc.fees
        ],
        staff_count=staff_count,
        miles=miles,
        tax_rate_bps=qc.tax_rate_bps,
        currency=qc.currency,
        pricebook_version=qc.pricebook_version,
        menu_version=qc.menu_version,
        subtotal_cents=qc.subtotal_cents,
        total_cents=qc.total_cents,
        price_status=qc.price_status,
        flags=list(qc.flags),
    )


def pinned_pricebook(
    inputs: CateringPricingInputs, current: CateringPricebook,
) -> CateringPricebook:
    """A pricebook that reproduces the COMMITTED quote exactly.

    Every price is pinned to what was committed — the package's per-person rate,
    each line's unit cents, each fee's unit amount, the tax rate. The ONLY thing
    taken from `current` is `approved_discounts`: the discount is the term the
    owner is applying NOW, so it must be validated against the pricebook that is
    published now, while everything it applies TO stays as quoted.

    The package is pinned `active=True` on purpose: retiring a package after a
    customer was quoted it does not un-quote them.
    """
    packages: list[CateringPackage] = []
    if inputs.package_id is not None:
        packages.append(CateringPackage(
            id=inputs.package_id,
            name=inputs.package_name or inputs.package_id,
            price_per_person_cents=int(inputs.package_price_per_person_cents or 0),
            min_guests=inputs.package_min_guests,
            active=True,
        ))
    return CateringPricebook(
        version=inputs.pricebook_version or current.version,
        effective_date=current.effective_date,
        updated_at=current.updated_at,
        currency=inputs.currency,
        # Carried, not cleared: a quote computed against seed data stays flagged
        # and stays undeliverable however many times it is recomputed.
        placeholder=FLAG_PLACEHOLDER_PRICEBOOK in inputs.flags,
        per_person_packages=packages,
        fixed_fees=[
            CateringFee(id=f.fee_id, name=f.name, kind=f.kind,
                        amount_cents=f.unit_cents, per_unit=f.per_unit, active=True)
            for f in inputs.fees
        ],
        tax_rate_bps=inputs.tax_rate_bps,
        approved_discounts=list(current.approved_discounts),
        item_price_overrides={li.name: li.unit_cents for li in inputs.line_items},
    )


def recompute_from_inputs(
    inputs: CateringPricingInputs,
    *,
    pricebook: CateringPricebook,
    discount_id: Optional[str] = None,
    staff_count: Optional[int] = None,
    miles: Optional[int] = None,
    min_per_guest_usd: Optional[float] = None,
) -> QuoteComputation:
    """Re-price a COMMITTED quote — optionally with a discount — from its frozen
    inputs, through the same kernel that produced it.

    The guarantee this exists for: with `discount_id=None` and the committed
    multipliers, `total_cents` comes back byte-equal to `inputs.total_cents`. The
    discount therefore lands on the true base rather than on whatever could be
    reconstructed from whole-dollar line rows.

    `staff_count` / `miles` default to the committed values; passing them lets an
    owner resolve a fee multiplier that was missing at finalize (which legitimately
    changes the total — the fee stops being unpriced).
    """
    book = pinned_pricebook(inputs, pricebook)
    qc = compute_quote(
        inputs.guest_count,
        inputs.package_id,
        [(li.name, li.qty) for li in inputs.line_items],
        discount_id,
        book,
        None,  # no menu: every price is pinned, nothing may fall back to a catalog
        fee_ids=[f.fee_id for f in inputs.fees],
        staff_count=(inputs.staff_count if staff_count is None else staff_count),
        miles=(inputs.miles if miles is None else miles),
        min_per_guest_usd=min_per_guest_usd,
    )
    # Carry only what the kernel did NOT just re-derive. A committed
    # `missing_fee_input` that the caller has since resolved with a staff count
    # must not survive as a stale reason on a quote that is now fully priced.
    carried = [f for f in inputs.flags
               if f not in qc.flags
               and not any(f == k or f.startswith(k + ":") for k in _KERNEL_DERIVED_FLAGS)]

    # Firmness ceiling. Pinned prices make every line an "override", which the
    # kernel would happily call "exact" even when the committed numbers were
    # menu-derived estimates — so the recompute is capped at how firm the
    # commitment was. A committed "pending_owner_review" caps at "estimated"
    # rather than at itself: pending is a RESOLUTION verdict, not a statement
    # about where the prices came from, and resolution is re-derived above (the
    # pinned book carries a placeholder pricebook; fee multipliers are inputs).
    # Supplying a missing multiplier is exactly how an owner is meant to make a
    # pending quote deliverable, and capping at "pending" would forbid it.
    ceiling = ("estimated" if inputs.price_status == "pending_owner_review"
               else inputs.price_status)
    price_status = weaker_price_status(qc.price_status, ceiling)
    # The one block a recompute can never clear on its own: it prices from frozen
    # inputs, so it cannot rediscover that the headcount behind them was never
    # known. Without this, resolving an unrelated fee would quietly make an
    # unheadcounted quote deliverable.
    if FLAG_UNKNOWN_GUEST_COUNT in inputs.flags:
        price_status = "pending_owner_review"
    return qc.model_copy(update={
        "price_status": price_status,
        "flags": list(qc.flags) + carried,
    })


# ── Customer / owner facing render ───────────────────────────────────────────
def price_status_line(price_status: str) -> str:
    """The grep-enforced label line. Unknown statuses degrade to the raw value
    rather than silently rendering nothing."""
    return f"{PRICE_STATUS_PREFIX} {PRICE_STATUS_LABELS.get(price_status, price_status)}"


def render_quote_lines(qc: QuoteComputation) -> str:
    """The price block shown to the owner (and, once approved, the customer).

    ALWAYS ends with the price-status label — no priced text leaves this module
    without saying how firm the number is."""
    out: list[str] = [f"Guests: {qc.guest_count}"]
    if qc.package_name is not None:
        out.append(
            f"Package: {qc.package_name} — {format_cents(qc.price_per_person_cents)}"
            f"/guest × {qc.guest_count} = {format_cents(qc.per_person_subtotal_cents)}"
        )
    # The package already has its own headline above; don't repeat it here.
    a_la_carte = [ln for ln in qc.lines if ln.source != "package"]
    if a_la_carte:
        out.append("Items:")
        for ln in a_la_carte:
            if ln.unit_cents is None:
                out.append(f"  - {ln.name} × {ln.qty} — price to be confirmed by owner")
            else:
                out.append(
                    f"  - {ln.name} × {ln.qty} @ {format_cents(ln.unit_cents)}"
                    f" = {format_cents(ln.extended_cents)}"
                )
    if qc.fees:
        out.append("Fees:")
        for fee in qc.fees:
            if fee.extended_cents is None:
                out.append(f"  - {fee.name} — pending ({fee.per_unit} count not supplied)")
            else:
                out.append(f"  - {fee.name} — {format_cents(fee.extended_cents)}")
    out.append(f"Subtotal: {format_cents(qc.subtotal_cents)}")
    if qc.discount_cents:
        label = qc.discount_name or qc.discount_id or "Discount"
        out.append(f"Discount ({label}): -{format_cents(qc.discount_cents)}")
    if qc.tax_rate_bps:
        pct = (Decimal(qc.tax_rate_bps) / 100).normalize()
        out.append(f"Tax ({pct}%): {format_cents(qc.tax_cents)}")
    out.append(f"Total: {format_cents(qc.total_cents)}")
    out.append(price_status_line(qc.price_status))
    return "\n".join(out)


# ── Headcount-scaled default basket (BL-CATER-03 fix) ────────────────────────
def default_basket(
    guest_count: int,
    pricebook: Optional[CateringPricebook],
    menu: Menu,
    *,
    max_items: int = 5,
) -> tuple[Optional[str], list[tuple[str, int]]]:
    """Deterministic starting basket for `finalize-catering-menu --auto-default`.

    Returns `(package_id, line_items)`:
      * With a pricebook that has an eligible active package (min_guests <=
        guest_count), the basket IS that package — cheapest eligible one, ties
        broken by id so the choice is reproducible. Per-person pricing scales
        with headcount by construction.
      * Otherwise the first `max_items` available priced menu items, with qty
        SCALED to the headcount: `ceil(guest_count / serves)` when the item
        declares servings, else one unit per guest. The pre-M2 basket used
        qty=1 regardless of headcount (BL-CATER-03).

    Quantities are clamped to MAX_LINE_QTY (the state schema's per-line bound).
    """
    if not isinstance(guest_count, int) or isinstance(guest_count, bool) or guest_count < 1:
        raise PricingError(f"guest_count must be an int >= 1; got {guest_count!r}")

    if pricebook is not None:
        eligible = [p for p in pricebook.per_person_packages
                    if p.active and p.min_guests <= guest_count]
        if eligible:
            best = min(eligible, key=lambda p: (p.price_per_person_cents, p.id))
            return best.id, []

    out: list[tuple[str, int]] = []
    for item in menu.items:
        if len(out) >= max_items:
            break
        if not item.available:
            continue
        has_price = (item.name in (pricebook.item_price_overrides if pricebook else {})
                     or (item.price_usd is not None and item.price_usd > 0))
        if not has_price:
            continue
        serves = item.serves or 1
        qty = min(math.ceil(guest_count / serves), MAX_LINE_QTY)
        out.append((item.name, max(qty, 1)))
    return None, out


# ── Store loader (I/O — deliberately OUTSIDE the pure kernel) ───────────────
def load_pricebook(path: Any) -> Optional[CateringPricebook]:
    """Load the pricebook, or None when the file is absent or empty.

    Accepts BOTH on-disk shapes: the `CateringPricebookStore` envelope written
    by import-catering-pricebook, and a bare `CateringPricebook` document (what
    an operator hand-writes, and what the seed template is).

    Raises `PricingError` on a present-but-invalid file. Degrading to "no
    pricebook" would silently revert callers to pre-M2 flat pricing — dropping
    the owner's fees, tax and packages out of a real quote without anyone
    noticing.

    Deliberately does NOT use safe_io.safe_load_json / load_model: those
    rename-quarantine a corrupt file to `.corrupt-<epoch>`. That is right for
    regenerable agent state and wrong for a hand-maintained commercial source of
    truth — the operator must find their pricebook where they left it. Mirrors
    catering_quote_ledger._load_store's preservation contract.
    """
    p = Path(path)
    try:
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise PricingError(f"pricebook at {p} is unreadable: {e}") from e
    if not raw.strip():
        return None
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise PricingError(f"pricebook at {p} is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise PricingError(
            f"pricebook at {p} must be a JSON object; got {type(doc).__name__}"
        )
    try:
        if "pricebook" in doc:
            return CateringPricebookStore.model_validate(doc).pricebook
        return CateringPricebook.model_validate(doc)
    except ValidationError as e:
        raise PricingError(f"pricebook at {p} failed validation:\n{e}") from e
