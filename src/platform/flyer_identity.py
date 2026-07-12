"""Canonical WhatsApp identity resolution for the Flyer surface.

A single customer can reach us under two WhatsApp identifiers — a phone-JID
(``<digits>@s.whatsapp.net``) and a LID (``<digits>@lid``) — and the two are
NOT interchangeable (a LID's digits are not a phone number). Multi-turn Flyer
state (intake sessions, the shadow-LLM allowlist, active projects) must key to
ONE stable identity per customer, or a customer who switches identifier
mid-conversation gets a second, orphaned session — the 2026-06-02
stale-intake-session hijack.

``canonical_identity_key`` collapses any identifier to a stable key:

  - a real phone (E.164 / phone-JID / an already-resolved phone) -> normalized
    ``+E164``;
  - a LID whose phone IS known (via the lid-cache maintained by
    shift-agent-lid-learn / the patched bridge.js) -> that normalized ``+E164``
    (so the LID and the phone converge on the SAME key);
  - a LID with NO known mapping -> the normalized raw LID (its own stable key).

The lid-cache lookup is cheap + mtime-cached (NOT an ``identify-sender``
subprocess per call). This module is installed FLAT to ``/opt/shift-agent/``
(alongside ``safe_io.py`` / ``schemas.py``) so BOTH the cf-router plugin and the
flat flyer modules can ``from flyer_identity import canonical_identity_key``.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

_DEFAULT_LID_CACHE_PATH = "/opt/shift-agent/state/lid-cache.json"
_LID_SUFFIX = "@lid"

# mtime-guarded in-process cache of the lid->phone map (see _load_lid_phone_map).
# cf-router runs single-process inside the gateway; the lock only guards the
# rare concurrent reload, and a stale-but-benign map is acceptable.
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"key": None, "map": {}}


def normalize_identifier(value: str) -> str:
    """Canonical comparison form for a phone/LID: strip a chat-JID suffix, a
    leading ``+``, internal phone punctuation/whitespace, and case-fold.

    Mirrors ``bare_render._normalize_sender`` /
    ``actions._normalize_flyer_intent_chat`` so the three surfaces compare
    identically. Preserves alphanumeric LID bodies (LIDs are not purely
    numeric)."""
    s = (value or "").strip()
    if "@" in s:
        s = s.split("@", 1)[0]
    s = s.lstrip("+")
    s = re.sub(r"[\s\-().]", "", s)
    return s.casefold()


def _is_lid(value: Optional[str]) -> bool:
    return (value or "").strip().lower().endswith(_LID_SUFFIX)


def _to_e164(value: Optional[str]) -> Optional[str]:
    """Return a ``+E164`` phone for a real phone string (phone-JID / E.164 /
    bare digits), or ``None``. NEVER resolves a LID — a LID's digits are not a
    phone number, so callers gate this behind ``_is_lid``."""
    if not value or _is_lid(value):
        return None
    digits = re.sub(r"\D", "", value.split("@", 1)[0])
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if 10 <= len(digits) <= 15:
        return "+" + digits
    return None


def _lid_cache_path(cache_path: Optional[str] = None) -> Path:
    return Path(
        cache_path
        or os.environ.get("SHIFT_AGENT_LID_CACHE_PATH", _DEFAULT_LID_CACHE_PATH)
    )


def _load_lid_phone_map(cache_path: Optional[str] = None) -> dict[str, str]:
    """Return ``{normalized_lid: '+E164'}`` from the lid-cache, mtime-cached.

    The lid-cache is written by the patched bridge.js as
    ``{"schema_version":1,"pairs":[{"phone":"+E164","lid":"<digits>@lid",...}]}``
    and maintained (owner/employee pairs trimmed after application) by
    shift-agent-lid-learn; flyer-customer pairs persist here unapplied.

    Any read/parse error yields an empty map — fail-open to raw-key behavior;
    identity resolution must never raise on the hot path."""
    path = _lid_cache_path(cache_path)
    try:
        st = path.stat()
        cache_key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    with _CACHE_LOCK:
        if _CACHE.get("key") == cache_key:
            return _CACHE["map"]  # type: ignore[return-value]
    mapping: dict[str, str] = {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for pair in (doc.get("pairs") or []):
            if not isinstance(pair, dict):
                continue
            lid = pair.get("lid")
            phone = _to_e164(pair.get("phone"))
            if lid and phone:
                mapping[normalize_identifier(lid)] = phone
    except (OSError, ValueError, TypeError):
        mapping = {}
    with _CACHE_LOCK:
        _CACHE["key"] = cache_key
        _CACHE["map"] = mapping
    return mapping


def lid_to_phone_from_cache(lid: str, *, cache_path: Optional[str] = None) -> Optional[str]:
    """``+E164`` for a LID whose pairing bridge.js has learned, else ``None``."""
    if not lid:
        return None
    return _load_lid_phone_map(cache_path).get(normalize_identifier(lid))


def canonical_identity_key(
    chat_id: str,
    phone: Optional[str] = None,
    *,
    cache_path: Optional[str] = None,
) -> str:
    """Stable canonical key for a chat identifier (see module docstring).

    ``phone`` is an already-resolved E.164 phone (e.g. from
    ``lid_to_phone_via_identify_sender``) when the caller has one; it always
    wins so owner/employee resolution costs no cache read."""
    # 1. An already-resolved real phone always wins.
    resolved = _to_e164(phone)
    if resolved:
        return resolved
    raw = (chat_id or "").strip()
    if not raw:
        return ""
    # 2. LID: converge to the mapped phone when known, else its own raw key.
    if _is_lid(raw):
        mapped = lid_to_phone_from_cache(raw, cache_path=cache_path)
        return mapped if mapped else normalize_identifier(raw)
    # 3. Phone-JID / E.164 / bare phone -> normalized +E164.
    resolved = _to_e164(raw)
    if resolved:
        return resolved
    # 4. Anything else -> its own normalized key.
    return normalize_identifier(raw)
