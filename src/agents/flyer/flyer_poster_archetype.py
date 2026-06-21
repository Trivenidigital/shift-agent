"""CD v2 Composition Phase 1 — poster archetype router.

A standalone, deterministic, pure component that selects a poster *archetype*
from a creative brief's ``request_intent``. The archetype tells the overlay how
to route composition (which block leads the poster). Phase-1 mapping only:

    menu / new / source_edit  -> message_first
    combo_offer               -> offer_first
    event                     -> event_first
    anything else / "" / None -> message_first  (safe default)

``offer_priority`` is accepted (so callers can pass the resolved priority) but is
NOT used for selection in Phase 1 — escalation by offer energy is deferred.

This module is pure and MUST NEVER raise: any unexpected input falls back to the
safe default (``message_first``).
"""
from __future__ import annotations

# Archetype constants (the only valid return values).
MESSAGE_FIRST = "message_first"
OFFER_FIRST = "offer_first"
EVENT_FIRST = "event_first"

# Phase-1 request_intent -> archetype mapping. Intents NOT in this table (and
# empty / None / non-string) fall back to MESSAGE_FIRST.
_INTENT_TO_ARCHETYPE = {
    "menu": MESSAGE_FIRST,
    "new": MESSAGE_FIRST,
    "source_edit": MESSAGE_FIRST,
    "combo_offer": OFFER_FIRST,
    "event": EVENT_FIRST,
}


def select_poster_archetype(request_intent: str, offer_priority: str = "medium") -> str:
    """Return the poster archetype for ``request_intent`` (Phase 1).

    Pure and never raises. ``offer_priority`` is accepted for forward
    compatibility but is unused for selection in Phase 1.
    """
    try:
        return _INTENT_TO_ARCHETYPE.get(request_intent, MESSAGE_FIRST)
    except Exception:  # noqa: BLE001 — pure/never-raises: any oddity => safe default
        return MESSAGE_FIRST
