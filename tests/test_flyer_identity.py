"""Canonical WhatsApp identity key — LID<->phone convergence.

The 2026-06-02 stale-intake-session hijack root cause: one customer reached us
under a phone-JID AND a LID, and the two keyed to different sessions because a
flyer customer's LID is NOT in roster/config (identify-sender returns None). The
lid-cache (bridge.js) carries the pairing; `canonical_identity_key` uses it so
both identifiers collapse to the SAME key once learned, and an unmapped LID
still gets its own stable key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PLATFORM = _REPO / "src" / "platform"
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

import flyer_identity as FI  # noqa: E402

PHONE = "+17329837841"
PHONE_JID = "17329837841@s.whatsapp.net"
LID = "201975216009469@lid"
OTHER_LID = "998877665544332@lid"


@pytest.fixture()
def lid_cache(tmp_path: Path) -> Path:
    """A lid-cache.json pairing LID <-> PHONE (bridge.js shape)."""
    cache = tmp_path / "lid-cache.json"
    cache.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pairs": [
                    {"phone": PHONE, "lid": LID, "learned_ts": "2026-07-12T00:00:00Z"}
                ],
            }
        ),
        encoding="utf-8",
    )
    return cache


def test_phone_forms_all_canonicalize_equal(lid_cache: Path):
    key_e164 = FI.canonical_identity_key(PHONE, cache_path=str(lid_cache))
    key_jid = FI.canonical_identity_key(PHONE_JID, cache_path=str(lid_cache))
    assert key_e164 == key_jid == PHONE


def test_explicit_resolved_phone_wins(lid_cache: Path):
    # A LID chat_id but an already-resolved phone -> the phone.
    assert FI.canonical_identity_key(LID, phone=PHONE, cache_path=str(lid_cache)) == PHONE


def test_mapped_lid_converges_to_phone(lid_cache: Path):
    key_lid = FI.canonical_identity_key(LID, cache_path=str(lid_cache))
    key_phone = FI.canonical_identity_key(PHONE_JID, cache_path=str(lid_cache))
    assert key_lid == key_phone == PHONE


def test_unmapped_lid_falls_back_to_raw(lid_cache: Path):
    key = FI.canonical_identity_key(OTHER_LID, cache_path=str(lid_cache))
    # Its own stable normalized-raw key, NOT a phone, and distinct from the phone key.
    assert key == FI.normalize_identifier(OTHER_LID) == "998877665544332"
    assert key != FI.canonical_identity_key(PHONE, cache_path=str(lid_cache))


def test_lid_digits_never_misread_as_phone(lid_cache: Path):
    # A 15-digit LID body must not be canonicalized as +<digits> (the legacy bug).
    key = FI.canonical_identity_key(OTHER_LID, cache_path=str(lid_cache))
    assert not key.startswith("+")


def test_missing_cache_is_fail_open(tmp_path: Path):
    missing = tmp_path / "nope.json"
    # Unmapped LID -> raw key; phone still resolves. No raise.
    assert FI.canonical_identity_key(LID, cache_path=str(missing)) == FI.normalize_identifier(LID)
    assert FI.canonical_identity_key(PHONE_JID, cache_path=str(missing)) == PHONE


def test_lid_to_phone_from_cache(lid_cache: Path):
    assert FI.lid_to_phone_from_cache(LID, cache_path=str(lid_cache)) == PHONE
    assert FI.lid_to_phone_from_cache(OTHER_LID, cache_path=str(lid_cache)) is None
    assert FI.lid_to_phone_from_cache("", cache_path=str(lid_cache)) is None


def test_mtime_cache_refreshes_on_change(tmp_path: Path):
    cache = tmp_path / "lid-cache.json"
    cache.write_text(json.dumps({"schema_version": 1, "pairs": []}), encoding="utf-8")
    assert FI.lid_to_phone_from_cache(LID, cache_path=str(cache)) is None
    # Bridge learns the pairing; a changed mtime/size must invalidate the cache.
    import os
    import time
    time.sleep(0.01)
    cache.write_text(
        json.dumps({"schema_version": 1, "pairs": [{"phone": PHONE, "lid": LID}]}),
        encoding="utf-8",
    )
    os.utime(cache, None)
    assert FI.lid_to_phone_from_cache(LID, cache_path=str(cache)) == PHONE


def test_normalize_identifier_mirrors_sender_norm():
    assert FI.normalize_identifier("+1 (732) 983-7841") == "17329837841"
    assert FI.normalize_identifier(PHONE_JID) == "17329837841"
    assert FI.normalize_identifier(LID) == "201975216009469"
    assert FI.normalize_identifier("") == ""
