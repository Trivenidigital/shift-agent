"""extract-receipt dedup primitives (Agent #21) — _hamming + _dhash_from_bytes.

These two pure functions are the perceptual-hash duplicate-detection primitive:
extract-receipt computes a dHash per receipt and flags a new receipt as
duplicate_of an existing lead when `_hamming(prior, new) <= dedup_hash_distance_
threshold`. A regression here is money-adjacent — a broken distance/hash could
let the same receipt be pushed to QBO twice, or wrongly collapse two distinct
receipts into one. The existing test_expense_bookkeeper_extract.py does not
exercise these primitives directly.

Pure-function, no vision/network/state -> strictly dormant-safe. Linux-only:
importing the script runs `from safe_io import ...` (fcntl), so the module is
loaded inside a fixture that only executes after the Windows skip.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="extract-receipt imports safe_io which imports fcntl",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACT_SCRIPT = _REPO_ROOT / "src" / "agents" / "expense_bookkeeper" / "scripts" / "extract-receipt"


@pytest.fixture(scope="module")
def er():
    """Load extract-receipt as a module (Linux-only). The script self-inserts
    src/platform onto sys.path for its safe_io/schemas imports."""
    loader = importlib.machinery.SourceFileLoader("extract_receipt_mod", str(EXTRACT_SCRIPT))
    spec = importlib.util.spec_from_file_location("extract_receipt_mod", str(EXTRACT_SCRIPT), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── _hamming: exact values ───────────────────────────────────────────────────

def test_hamming_identical_is_zero(er):
    assert er._hamming("0000000000000000", "0000000000000000") == 0
    assert er._hamming("a3f2c19d8b5e4067", "a3f2c19d8b5e4067") == 0


def test_hamming_single_bit(er):
    assert er._hamming("0000000000000000", "0000000000000001") == 1
    assert er._hamming("0000000000000002", "0000000000000000") == 1


def test_hamming_nibble_four_bits(er):
    # 0xf == 0b1111 -> 4 bits differ from 0x0
    assert er._hamming("000000000000000f", "0000000000000000") == 4


def test_hamming_all_bits_differ(er):
    # 16 hex 'f' = 64 set bits; vs all-zero -> full 64-bit distance
    assert er._hamming("ffffffffffffffff", "0000000000000000") == 64


def test_hamming_known_mid_value(er):
    # 0b0011 vs 0b0001 -> exactly one differing bit
    assert er._hamming("0000000000000003", "0000000000000001") == 1
    # 0b1010 (a) vs 0b0101 (5) -> 4 differing bits
    assert er._hamming("000000000000000a", "0000000000000005") == 4


def test_hamming_is_symmetric(er):
    a, b = "a3f2c19d8b5e4067", "a3f2c19d8b5e4060"
    assert er._hamming(a, b) == er._hamming(b, a)


def test_hamming_length_mismatch_returns_max_len(er):
    # Defensive contract: unequal lengths -> max length (a large distance that
    # will always exceed any sane dedup threshold, so it is NOT a duplicate).
    assert er._hamming("abc", "abcd") == 4
    assert er._hamming("0000000000000000", "000") == 16


# ── _dhash_from_bytes: determinism + format ──────────────────────────────────

def test_dhash_is_deterministic(er):
    data = b"\xff\xd8\xff\xe0 some receipt bytes"
    assert er._dhash_from_bytes(data) == er._dhash_from_bytes(data)


def test_dhash_format_is_16_hex_chars(er):
    h = er._dhash_from_bytes(b"anything at all")
    assert len(h) == 16
    int(h, 16)  # raises if not valid hex


def test_dhash_differs_for_different_inputs(er):
    h1 = er._dhash_from_bytes(b"receipt-A-pixels-aaaaaaaa")
    h2 = er._dhash_from_bytes(b"receipt-B-pixels-zzzzzzzz")
    assert h1 != h2


def test_exact_duplicate_image_has_zero_distance(er):
    # The core dedup invariant: identical image bytes -> identical dHash ->
    # hamming distance 0 -> within ANY non-negative threshold -> duplicate.
    data = b"\x89PNG identical receipt"
    assert er._hamming(er._dhash_from_bytes(data), er._dhash_from_bytes(data)) == 0
