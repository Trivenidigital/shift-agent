"""proposal_sweep — stale-detection for the Shift no-response escalation sweep.

A `sent` coverage proposal whose candidate never replies otherwise sits forever with no
escalation — the silent-uncovered-shift gap (owner-experience review 2026-07-10), made worse
by the owner having been told "I'll let you know when they respond." This pure helper finds
those proposals so `shift-agent-proposal-sweep` can transition them to `no_response_timeout`
(via the existing `update-proposal-status` chokepoint) and alert the owner.

stdlib-only, duck-typed on `.status`/`.sent_ts` → importable + cross-platform unit-testable
(the fcntl-gated subprocess suites skip on Windows; this one does not).
"""
from __future__ import annotations

from datetime import datetime, timedelta


def find_stale_sent_proposals(proposals, now: datetime, ttl_minutes: int) -> list[str]:
    """Return the sorted ids of proposals in status ``sent`` whose ``sent_ts`` is at least
    ``ttl_minutes`` before ``now``.

    ``proposals`` maps id -> object exposing ``.status`` (str) and ``.sent_ts`` (aware
    datetime, or None). Only ``sent`` proposals are considered — every other status
    (approved/accepted/awaiting_owner_approval/no_response_timeout/…) is skipped, so a
    candidate who has already replied is never touched. A ``sent`` proposal missing
    ``sent_ts`` is skipped defensively rather than crashed on. ``now`` and each ``sent_ts``
    must share tz-awareness (both originate from ``safe_io.customer_now``).
    """
    cutoff = now - timedelta(minutes=ttl_minutes)
    stale: list[str] = []
    for pid, prop in proposals.items():
        if getattr(prop, "status", None) != "sent":
            continue
        sent_ts = getattr(prop, "sent_ts", None)
        if sent_ts is not None and sent_ts <= cutoff:
            stale.append(pid)
    return sorted(stale)


def find_expired_awaiting_proposals(proposals, now: datetime, ttl_hours: int) -> list[str]:
    """Return the sorted ids of proposals in status ``awaiting_owner_approval`` whose
    ``created_ts`` is at least ``ttl_hours`` before ``now`` (BL-SHIFT-04).

    The owner card promises "unapproved proposals expire 4 hours after creation", but
    nothing enforced it — an unapproved proposal sat in ``awaiting_owner_approval``
    indefinitely. ``shift-agent-proposal-sweep`` uses this to transition each to the
    (legal, terminal) ``expired`` status via the ``update-proposal-status`` chokepoint
    and alert the owner. Anchored on ``created_ts`` to match the card's wording.

    ``proposals`` maps id -> object exposing ``.status`` (str) and ``.created_ts`` (aware
    datetime). Only ``awaiting_owner_approval`` proposals are considered — any proposal
    the owner already acted on has left that status and is never touched. A proposal
    missing ``created_ts`` is skipped defensively. ``now`` and ``created_ts`` must share
    tz-awareness (both originate from ``safe_io.customer_now``).
    """
    cutoff = now - timedelta(hours=ttl_hours)
    expired: list[str] = []
    for pid, prop in proposals.items():
        if getattr(prop, "status", None) != "awaiting_owner_approval":
            continue
        created_ts = getattr(prop, "created_ts", None)
        if created_ts is not None and created_ts <= cutoff:
            expired.append(pid)
    return sorted(expired)
