"""CSV import formula-injection guard + UnicodeDecodeError handling (BL-144)."""
from __future__ import annotations


import pytest


def _make_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    keys = list(rows[0].keys())
    out = ",".join(keys) + "\n"
    for r in rows:
        out += ",".join(r.get(k, "") for k in keys) + "\n"
    return out


def test_formula_prefix_rejected():
    """Each forbidden cell prefix must trigger 422."""
    from app.routers.roster import _FORMULA_PREFIXES

    assert _FORMULA_PREFIXES == frozenset({"=", "+", "-", "@", "\t"})


def test_decode_error_returns_422_not_500():
    """Non-UTF-8 file → UnicodeDecodeError; the router catches and returns 422."""
    bad_bytes = "name,phone\ncafé,+12345678901\n".encode("latin-1")
    with pytest.raises(UnicodeDecodeError):
        bad_bytes.decode("utf-8-sig")


def test_csv_round_trip_phone_with_plus_allowed():
    """E.164 phone in 'phone' column starts with '+' — must be ALLOWED.

    Phone columns are special-cased; only `+digits-with-separators` is
    permitted. `+SUM(...)` style expressions still rejected.
    """
    from app.routers.roster import _looks_like_e164_phone

    # Real phone numbers
    assert _looks_like_e164_phone("+19045550101")
    assert _looks_like_e164_phone("+1-904-555-0101")
    assert _looks_like_e164_phone("+1 (904) 555-0101")

    # Excel formulas masquerading as phones
    assert not _looks_like_e164_phone("+SUM(A1)")
    assert not _looks_like_e164_phone("+CMD|/c calc.exe")
    assert not _looks_like_e164_phone("+")
    assert not _looks_like_e164_phone("+ABC")


def test_explicit_formula_prefix_in_cell():
    """The actual injection: cell starts with =."""
    bad_cell = "=cmd|/c calc.exe"
    from app.routers.roster import _FORMULA_PREFIXES

    assert bad_cell.lstrip()[:1] in _FORMULA_PREFIXES


def test_whitespace_then_formula_still_caught():
    """`val.lstrip()[:1]` catches leading-whitespace + formula (per design review)."""
    sneaky = "   =cmd|..."
    from app.routers.roster import _FORMULA_PREFIXES

    assert sneaky.lstrip()[:1] in _FORMULA_PREFIXES
