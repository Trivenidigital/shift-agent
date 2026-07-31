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

# Default TTL for QUALIFYING leads, in HOURS — a much shorter window, because the two
# statuses mean different things. AWAITING_OWNER_APPROVAL is waiting on the OWNER, who
# may legitimately take a week. QUALIFYING is waiting on the CUSTOMER, mid-conversation:
# we asked a question and they never came back. Left alone such a lead lives forever,
# invisible to a WhatsApp-only owner (who never sees the Studio's QUALIFYING counter) and
# still holding the sender's "active lead" slot, so every later message from them routes
# as an answer to a conversation that ended. Overridden per-customer by
# ``catering.qualifying_stale_after_hours``.
CATERING_QUALIFYING_STALE_HOURS = 72


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
    return _expired_in_status(leads, "AWAITING_OWNER_APPROVAL",
                              now - timedelta(days=ttl_days))


def find_expired_qualifying_leads(leads, now: datetime, stale_hours: int) -> list[str]:
    """Return the sorted ids of leads in status ``QUALIFYING`` whose ``updated_at`` is at
    least ``stale_hours`` before ``now``.

    Same contract and same idempotence as ``find_expired_awaiting_leads`` — a lead that
    answers a question leaves QUALIFYING or bumps ``updated_at``, and once swept to
    ``STALE`` it is never selected again. Separate function rather than a status
    parameter because the two windows are set by different config keys and mean
    different things (see CATERING_QUALIFYING_STALE_HOURS).
    """
    return _expired_in_status(leads, "QUALIFYING", now - timedelta(hours=stale_hours))


def _expired_in_status(leads, status: str, cutoff: datetime) -> list[str]:
    """Sorted ids of leads in ``status`` last touched at or before ``cutoff``. A lead
    missing ``updated_at`` is skipped defensively rather than crashed on."""
    expired: list[str] = []
    for lead in leads:
        if getattr(lead, "status", None) != status:
            continue
        updated_at = getattr(lead, "updated_at", None)
        if updated_at is not None and updated_at <= cutoff:
            lead_id = getattr(lead, "lead_id", None)
            if lead_id:
                expired.append(lead_id)
    return sorted(expired)
