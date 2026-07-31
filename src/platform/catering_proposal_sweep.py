"""catering_proposal_sweep — validity/expiry helpers for catering proposal sets (M3).

Before M3 a catering proposal set had NO validity anywhere: no ``expires_at``, no
TTL, no sweep. A set sent in June stayed selectable forever, so a customer could
"pick option 2" months later against prices the owner has since changed — the
money-side twin of the L0017 never-closing-lead incident, and the reason the
customer-facing quote text now names an explicit end date.

This module is the PURE half of that fix (the ``catering-proposal-expiry-sweep``
script is the I/O half), mirroring ``proposal_sweep`` (Shift) and
``catering_lead_sweep``: stdlib-only, duck-typed on the attributes it reads, no
filesystem, no clock, no LLM — so it is importable and unit-testable on every
platform while the fcntl-gated subprocess suites skip on Windows.

ONE definition of the window lives here and BOTH sides use it: the sender stamps
``expires_at`` with :func:`expires_at_from`, the customer is told the date
:func:`quote_validity_line` renders from the same call, and the sweep expires a
set only at/after that stored instant. A set can therefore never be retired
before the date the customer was given.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

# Fallback when no config is readable. Matches CateringConfig.proposal_validity_days
# so an unreadable config degrades to the documented default rather than to "never
# expires" (silent) or "expires immediately" (destructive).
DEFAULT_PROPOSAL_VALIDITY_DAYS = 14


def expires_at_from(sent_at: datetime, validity_days: int) -> datetime:
    """The instant a set sent at ``sent_at`` stops being selectable."""
    return sent_at + timedelta(days=int(validity_days))


def validity_end_date(sent_at: datetime, validity_days: int) -> str:
    """ISO calendar date (YYYY-MM-DD) of the validity end — what the customer is told.

    Deliberately date-granular: quoting a customer a to-the-second expiry is noise,
    and the stored ``expires_at`` is the authoritative instant the sweep compares
    against. Because the sweep fires at ``expires_at`` (end of the named day at the
    same clock time), the customer's stated date is never cut short.
    """
    return expires_at_from(sent_at, validity_days).date().isoformat()


def quote_validity_line(sent_at: datetime, validity_days: int) -> str:
    """The one customer-facing sentence stating how long the quote stands.

    Rendered into the customer quote template and the server-composed quote body,
    so a customer is never quoted a price with no stated shelf life."""
    return f"This quote is valid until {validity_end_date(sent_at, validity_days)}."


def find_expired_sent_proposal_sets(proposal_sets, now: datetime) -> list[str]:
    """Return the sorted ids of proposal sets in status ``SENT`` whose ``expires_at``
    is at/before ``now``.

    ``proposal_sets`` is an iterable of objects exposing ``.status`` (str),
    ``.expires_at`` (aware datetime or None) and ``.proposal_set_id`` (str). Only
    ``SENT`` sets are considered — a set the customer already selected, or one that
    was superseded / failed / already expired, has left that status and is never
    touched, which is what makes the sweep idempotent.

    A ``SENT`` set with ``expires_at=None`` is SKIPPED, not expired: those are the
    sets sent before M3 (and any set whose stamp failed). Nobody told that customer
    a deadline, so retiring their options on a deadline they never heard would be
    the silent behavior change this whole module exists to avoid — an operator
    re-sends instead.

    ``now`` and each ``expires_at`` must share tz-awareness (both originate from
    ``safe_io.customer_now``).
    """
    expired: list[str] = []
    for row in proposal_sets:
        if getattr(row, "status", None) != "SENT":
            continue
        exp: Optional[datetime] = getattr(row, "expires_at", None)
        if exp is not None and exp <= now:
            set_id = getattr(row, "proposal_set_id", None)
            if set_id:
                expired.append(set_id)
    return sorted(expired)


__all__ = [
    "DEFAULT_PROPOSAL_VALIDITY_DAYS",
    "expires_at_from",
    "validity_end_date",
    "quote_validity_line",
    "find_expired_sent_proposal_sets",
]
