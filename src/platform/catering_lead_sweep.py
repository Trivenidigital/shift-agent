"""catering_lead_sweep — stale-detection for the catering lead TTL expiry sweep (PR-A).

A catering lead that reaches ``AWAITING_OWNER_APPROVAL`` and is then never acted on sits
there indefinitely (the L0017 incident: a June lead still open weeks later, silently
capturing every later message as a follow-up). This pure helper finds those stale leads so
``catering-lead-ttl-sweep`` can transition each to the (legal, terminal) ``STALE`` status
and alert the owner — the catering analog of ``proposal_sweep.find_expired_awaiting_proposals``.

Anchored on ``updated_at`` (last lead activity) rather than ``created_at``: a lead the owner
or customer touched recently is NOT stale, so any status change / edit within the TTL window
keeps it alive. (Sidecar R2A amendments do not bump ``updated_at``; that is acceptable — this
is a coarse janitorial sweep, not a per-message freshness gate.)

stdlib-only, duck-typed on ``.status``/``.updated_at``/``.lead_id`` → importable +
cross-platform unit-testable (the fcntl-gated subprocess suites skip on Windows; this one
does not).
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Default TTL for AWAITING_OWNER_APPROVAL leads. Conservative: three weeks with no activity
# before a lead is considered abandoned. Overridable by the CLI via env for tuning.
CATERING_LEAD_TTL_DAYS = 21


def find_expired_awaiting_leads(leads, now: datetime, ttl_days: int) -> list[str]:
    """Return the sorted ids of leads in status ``AWAITING_OWNER_APPROVAL`` whose
    ``updated_at`` is at least ``ttl_days`` before ``now``.

    ``leads`` is an iterable of objects exposing ``.status`` (str), ``.updated_at`` (aware
    datetime), and ``.lead_id`` (str). Only ``AWAITING_OWNER_APPROVAL`` leads are considered —
    any lead the owner/customer already advanced has left that status and is never touched, so
    the sweep is idempotent (once a lead becomes ``STALE`` it is no longer selected). A lead
    missing ``updated_at`` is skipped defensively rather than crashed on. ``now`` and each
    ``updated_at`` must share tz-awareness (both originate from ``safe_io.customer_now``).
    """
    cutoff = now - timedelta(days=ttl_days)
    expired: list[str] = []
    for lead in leads:
        if getattr(lead, "status", None) != "AWAITING_OWNER_APPROVAL":
            continue
        updated_at = getattr(lead, "updated_at", None)
        if updated_at is not None and updated_at <= cutoff:
            lead_id = getattr(lead, "lead_id", None)
            if lead_id:
                expired.append(lead_id)
    return sorted(expired)
