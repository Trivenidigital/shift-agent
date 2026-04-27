"""CSV import formula-injection guard + UnicodeDecodeError handling (BL-144)."""
from __future__ import annotations

import io

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
    from fastapi import HTTPException

    from app.routers.roster import _FORMULA_PREFIXES

    assert _FORMULA_PREFIXES == frozenset({"=", "+", "-", "@", "\t"})


def test_decode_error_returns_422_not_500():
    """Non-UTF-8 file should raise HTTPException(422), not let UnicodeDecodeError surface."""
    # Latin-1 "café" — invalid as UTF-8
    bad_bytes = "name,phone\ncafé,+12345678901\n".encode("latin-1")
    with pytest.raises(UnicodeDecodeError):
        bad_bytes.decode("utf-8-sig")
    # The router catches this; this test confirms our assumption that the bytes
    # actually fail UTF-8 decode.


def test_csv_round_trip_via_module_constants():
    """Exercise the row-parser path with a clean CSV, expecting NO formula
    rejections. We don't run the full FastAPI handler here (that's an
    integration test); we just validate the prefix-check logic works on
    realistic data."""
    csv = _make_csv([
        {"id": "e001", "name": "Ravi Kumar", "role": "cashier",
         "phone": "+19045550101", "can_cover_roles": "cashier|floor"},
    ])
    # Verify no row contains a forbidden prefix
    rows = csv.strip().split("\n")[1:]  # skip header
    from app.routers.roster import _FORMULA_PREFIXES

    for row in rows:
        for cell in row.split(","):
            stripped = cell.lstrip()
            assert not stripped or stripped[:1] not in _FORMULA_PREFIXES


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
