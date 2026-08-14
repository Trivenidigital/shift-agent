"""safe_io JSON state files are UTF-8 on BOTH sides of the round trip.

The write side always encoded UTF-8 (`atomic_write_text` does
`os.write(fd, content.encode("utf-8"))`, and `ndjson_append` the same), but
`safe_load_json` read with a bare `path.read_text()` — the platform locale. The
asymmetry was invisible on the deployed Linux VPS, whose default IS UTF-8, and
raised UnicodeDecodeError on a Windows dev box (cp1252) for any state file
carrying a legitimately non-ASCII byte: an em-dash or smart quote in customer
copy, a rupee sign, a customer's name.

These cells run everywhere but only BITE on a platform whose default encoding is
not UTF-8. That is deliberate — the invariant is "the reader does not consult the
locale", and the only way to observe a locale-dependent reader is from a
non-UTF-8 locale. On Linux they are a cheap tautology; on Windows they are the
regression guard.
"""
from __future__ import annotations

import json
from pathlib import Path

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()  # safe_io imports fcntl at module level (Linux-only)

import safe_io  # noqa: E402

# The byte that broke it: U+201D RIGHT DOUBLE QUOTATION MARK is 0x9d in cp1252's
# undefined range. Plus an em-dash and a rupee sign — all three appear in real
# catering copy and customer names.
NON_ASCII_PAYLOAD = {
    "lead_id": "L0017",
    "quote_text": "Chef’s tasting menu — “family style” ₹3,500",
    "customer_name": "Renée Wollstonecraft-Niño",
}


def _dump(payload: dict) -> str:
    """What `atomic_write_json` actually emits for a state file.

    `ensure_ascii=False` is not a test convenience — it mirrors production. The
    pydantic branch of `atomic_write_json` writes `obj.model_dump_json(indent=2)`,
    which leaves non-ASCII characters as themselves rather than escape sequences.
    That is why a real catering-leads.json contains raw UTF-8 bytes at all, and a
    cell relying on json.dumps' escaping default would be pure ASCII on disk —
    green on every platform, and unable to reproduce the bug it exists to guard.
    """
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _write_like_atomic_write_text(path: Path, content: str) -> None:
    """Reproduce exactly what `atomic_write_text` puts on disk — UTF-8 bytes —
    without its fcntl locking or its pytest prod-write guard, neither of which is
    what these cells are about."""
    path.write_bytes(content.encode("utf-8"))


def test_safe_load_json_reads_utf8_regardless_of_platform_locale(tmp_path):
    """The regression: written UTF-8, read back with the locale codec."""
    state = tmp_path / "catering-leads.json"
    _write_like_atomic_write_text(state, _dump(NON_ASCII_PAYLOAD))

    value, status = safe_io.safe_load_json(state)

    assert status == "ok", (
        f"a UTF-8 state file must load cleanly on every platform; got {status!r}")
    assert value == NON_ASCII_PAYLOAD, "the characters must survive the round trip"


def test_safe_load_json_does_not_report_an_encoding_fault_as_corruption(tmp_path):
    """The failure mode was doubly misleading: a decode error is not a JSON syntax
    error, so it must never quarantine the file as corrupt. Before the fix the
    UnicodeDecodeError escaped `safe_load_json` entirely (it catches
    JSONDecodeError and OSError, not UnicodeDecodeError), surfacing to the caller
    as an unreadable file rather than an encoding fault."""
    state = tmp_path / "catering-leads.json"
    _write_like_atomic_write_text(state, _dump(NON_ASCII_PAYLOAD))

    _value, status = safe_io.safe_load_json(state)

    assert not status.startswith("corrupt"), status
    assert not list(tmp_path.glob("*.corrupt-*")), (
        "a perfectly valid UTF-8 file must not be quarantined")


def test_atomic_write_text_and_safe_load_json_agree_on_the_codec(tmp_path):
    """Pin the symmetry itself, so a future edit cannot re-introduce a reader and
    a writer that disagree."""
    state = tmp_path / "state.json"
    content = _dump(NON_ASCII_PAYLOAD)
    _write_like_atomic_write_text(state, content)

    assert state.read_bytes().decode("utf-8") == content, "written as UTF-8"
    value, status = safe_io.safe_load_json(state)
    assert (status, value) == ("ok", NON_ASCII_PAYLOAD), "read as UTF-8"
