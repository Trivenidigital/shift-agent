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
