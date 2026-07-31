"""M5 — controlled follow-up engine: scheduling, dedup and suppression (PURE + store).

THE single source of truth for "should this follow-up happen, and when". The
sweep, the approve CLI and the three trigger sites all call the functions here,
so the gate can never disagree with itself — the same reason
catering_qualification exists for the qualification gate.

THE SHAPE OF A FOLLOW-UP (binding):

  * DETERMINISTIC. Messages come from fixed templates in
    src/agents/catering/templates/catering_followup_*.txt. No LLM composes a
    follow-up, and no template carries a price — a nudge that quotes a number is
    a quote, and quotes go through the owner-approval path that already exists.
  * SCHEDULED. A follow-up is written with a `due_at` and does nothing until
    then. Scheduling is not sending.
  * OWNER-SUPERVISED. The default path is not a customer send at all: a due
    follow-up becomes an owner APPROVAL CARD carrying a #XXXXX code, and only an
    owner reply turns it into a message. Direct auto-send exists behind a second
    flag that ships OFF (see catering-followup-sweep).
  * NEVER COLD. Every record carries a `lead_id`. The sweep refuses to render a
    follow-up whose lead it cannot find (`lead_missing`), so there is no code
    path from this module to a person we are not already talking to.

SUPPRESSION IS TERMINAL, NEVER A RESCHEDULE. When a rule fires the record moves
to `suppressed` and stays there, with the reason attached. Retrying until a rule
happens to pass would turn "the customer opted out" into "the customer opted out
until 9am tomorrow", and the audit row would record the attempt that eventually
succeeded rather than the decision that should have stopped it.

Deployed FLAT to /opt/shift-agent/ so scripts + the cf-router plugin can import it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Tuple

from catering_paths import CATERING_FOLLOWUPS_PATH
from safe_io import FileLock, atomic_write_json, load_model
from schemas import CateringFollowup, CateringFollowupStore

# ── Scheduling offsets (deterministic; the ONE place these numbers live) ──────
# Hours after the M1 qualification loop hands a lead to the owner with gaps still
# open. 24h gives the customer a night to come back on their own before we ask
# again — the loop already asked three times.
INCOMPLETE_QUALIFICATION_HOURS = 24
# Hours after a quote reached the customer with no reply. 48h is the shortest
# interval that does not read as chasing a decision the customer is still making.
PROPOSAL_UNANSWERED_HOURS = 48
# Days BEFORE the event. -7 is the last comfortable point to change a menu;
# -3 is when a caterer has to buy against a number.
EVENT_APPROACHING_DAYS_BEFORE = 7
FINAL_HEADCOUNT_DAYS_BEFORE = 3
# Days AFTER the event, for the feedback ask.
POST_EVENT_FEEDBACK_DAYS_AFTER = 1

_OFFSETS: dict[str, timedelta] = {
    "incomplete_qualification": timedelta(hours=INCOMPLETE_QUALIFICATION_HOURS),
    "proposal_unanswered": timedelta(hours=PROPOSAL_UNANSWERED_HOURS),
    "event_approaching": timedelta(days=-EVENT_APPROACHING_DAYS_BEFORE),
    "final_headcount_due": timedelta(days=-FINAL_HEADCOUNT_DAYS_BEFORE),
    "post_event_feedback": timedelta(days=POST_EVENT_FEEDBACK_DAYS_AFTER),
    "owner_reminder": timedelta(0),
}

# Types measured from the EVENT date rather than from now.
EVENT_ANCHORED_TYPES: frozenset[str] = frozenset({
    "event_approaching", "final_headcount_due", "post_event_feedback",
})

# Owner approval cards for a due follow-up expire after this many hours, matching
# LimitsConfig.pending_proposal_ttl_hours (the interval the catering approval card
# already promises the owner). An expired card returns the record to `scheduled`
# with attempt_count + 1.
CARD_TTL_HOURS = 4
# Card attempts before the follow-up is abandoned. An owner who let two cards
# lapse has answered; a third would be the agent arguing with them.
MAX_CARD_ATTEMPTS = 2

# ── Bridge template-bypass prefix ────────────────────────────────────────────
# bridge.js drops outbound text that looks like LLM monologue unless it matches
# /^⚕ \*[A-Za-z][A-Za-z ]*\*\n[─\-]+\n/ (see the F1 note in
# apply-catering-owner-decision). The prefix is a TRANSPORT header, not content,
# so it lives here and is prepended at SEND time rather than being baked into the
# template files — that way the owner approval card shows the message body the
# customer will read, without a duplicate header stacked inside it.
BRIDGE_TEMPLATE_PREFIX = "⚕ *Catering Agent*\n────────────\n"


def with_bridge_prefix(body: str) -> str:
    """The customer-bound form of a rendered follow-up body."""
    return f"{BRIDGE_TEMPLATE_PREFIX}{body}"


# ── Template keys (one per type; the sweep reads the matching .txt) ───────────
TEMPLATE_KEYS: dict[str, str] = {
    "incomplete_qualification": "catering_followup_incomplete_qualification",
    "proposal_unanswered": "catering_followup_proposal_unanswered",
    "owner_reminder": "catering_followup_owner_reminder",
    "event_approaching": "catering_followup_event_approaching",
    "final_headcount_due": "catering_followup_final_headcount_due",
    "post_event_feedback": "catering_followup_post_event_feedback",
}

# ── Lead statuses a follow-up may act on ─────────────────────────────────────
# DEFENSIVE ALLOWLIST, not a terminal denylist. M3 (and anything after it) may
# add statuses to CateringLeadStatus; a denylist would silently treat every new
# status as safe to chase. Anything not named here suppresses, including an
# unknown string read off disk.
FOLLOWUP_ALLOWED_LEAD_STATUSES: frozenset[str] = frozenset({
    "QUALIFYING",
    "AWAITING_OWNER_APPROVAL",
    "CUSTOMER_FINALIZED",
    "OWNER_APPROVED",
    "OWNER_EDITED",
    "SENT_TO_CUSTOMER",
})

# Per-type additions to the allowlist. post_event_feedback runs AFTER the event,
# by which point a lead that actually converted is legitimately CLOSED — that is
# precisely the lead worth asking, so CLOSED is admitted for that ONE type.
FOLLOWUP_EXTRA_ALLOWED_STATUSES: dict[str, frozenset[str]] = {
    "post_event_feedback": frozenset({"CLOSED"}),
}

# Follow-up statuses that consumed a real attention budget (someone was carded or
# messaged). A suppressed / cancelled / expired record never reached anyone, so it
# does not count against the per-lead cap.
_BUDGET_CONSUMING_STATUSES: frozenset[str] = frozenset({
    "awaiting_owner_approval", "approved_sent",
})

# Follow-up statuses still in play — their approval codes must not be re-minted.
LIVE_FOLLOWUP_STATUSES: frozenset[str] = frozenset({
    "scheduled", "awaiting_owner_approval",
})

_ENV_PATH = "SHIFT_AGENT_FOLLOWUPS_PATH"


# ── Paths (env-overridable, resolved at CALL time so subprocesses agree) ──────
def followups_path() -> Path:
    return Path(os.environ.get(_ENV_PATH) or CATERING_FOLLOWUPS_PATH)


def followups_lock(path: Optional[Path] = None) -> Path:
    return Path(str(path or followups_path()) + ".lock")


# ── Store I/O ────────────────────────────────────────────────────────────────
def load_store(path: Optional[Path] = None) -> Tuple[CateringFollowupStore, str]:
    """(store, load_status). Callers treat 'ok'/'missing'/'empty' as clean and
    refuse to mutate on anything else — the same posture every other catering
    store read takes."""
    return load_model(path or followups_path(), CateringFollowupStore,
                      default=CateringFollowupStore())


def save_store(store: CateringFollowupStore, path: Optional[Path] = None) -> None:
    """Caller MUST hold the store FileLock."""
    atomic_write_json(path or followups_path(), store)


def next_followup_id(store: CateringFollowupStore) -> str:
    """FU0001, FU0002, ... from the store's own counter, floored above every id
    already present so a hand-edited store cannot mint a duplicate."""
    used = 0
    for f in store.followups:
        try:
            used = max(used, int(f.followup_id[2:]))
        except (ValueError, IndexError):
            continue
    seq = max(store.next_sequence, used + 1)
    return f"FU{seq:04d}"


# ── Dedup ────────────────────────────────────────────────────────────────────
def dedup_key(lead_id: str, followup_type: str, due_at: datetime) -> Tuple[str, str, str]:
    """(lead_id, type, due DATE). Day granularity, not instant: two triggers
    firing minutes apart for the same lead and reason are the same nudge, and an
    exact-timestamp key would let a retried send path schedule both."""
    return (lead_id, followup_type, due_at.date().isoformat())


def find_duplicate(
    store: CateringFollowupStore, lead_id: str, followup_type: str, due_at: datetime,
) -> Optional[CateringFollowup]:
    """An existing record with the same dedup key, in ANY status.

    Suppressed and cancelled records count. A trigger that re-fires after a
    suppression must not quietly produce a second copy — the suppression was a
    decision about this (lead, reason, day), and re-scheduling it would be the
    reschedule-until-it-passes behavior this module exists to prevent. An owner
    asking again explicitly (created_by="owner") is the documented override.
    """
    want = dedup_key(lead_id, followup_type, due_at)
    for f in store.followups:
        if dedup_key(f.lead_id, f.followup_type, f.due_at) == want:
            return f
    return None


# ── Due-time computation ─────────────────────────────────────────────────────
def compute_due(
    followup_type: str, *, now: datetime, event_date: Optional[str] = None,
) -> Optional[datetime]:
    """The due instant for one follow-up type, or None when it cannot be placed.

    Event-anchored types return None without an `event_date` — there is no
    sensible fallback for "7 days before an event nobody has told us the date of",
    and inventing one would schedule a nudge against a guess.

    An event-anchored due time that already lies in the past returns None too: a
    lead booked five days out has no 7-days-before moment, and firing it
    immediately would send "your event is in a week" about an event in five days.
    """
    offset = _OFFSETS.get(followup_type)
    if offset is None:
        return None
    if followup_type in EVENT_ANCHORED_TYPES:
        anchor = _parse_event_date(event_date, tzinfo=now.tzinfo)
        if anchor is None:
            return None
        due = anchor + offset
        return due if due > now else None
    return now + offset


def _parse_event_date(event_date: Optional[str], *, tzinfo) -> Optional[datetime]:
    """An ISO YYYY-MM-DD event date at 10:00 local — a mid-morning anchor, so a
    -7d offset lands in business hours rather than at midnight."""
    if not event_date:
        return None
    try:
        d = datetime.fromisoformat(str(event_date)).date()
    except (TypeError, ValueError):
        return None
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=tzinfo or timezone.utc)


# ── Scheduling ───────────────────────────────────────────────────────────────
class ScheduleResult:
    """(ok, followup, reason). `reason` is a stable code, not prose: 'duplicate',
    'no_due_time', 'store_unreadable', 'unknown_type'."""

    __slots__ = ("ok", "followup", "reason")

    def __init__(self, ok: bool, followup: Optional[CateringFollowup] = None,
                 reason: str = "") -> None:
        self.ok = ok
        self.followup = followup
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"ScheduleResult(ok={self.ok}, reason={self.reason!r})"


def schedule_followup(
    lead_id: str,
    followup_type: str,
    *,
    now: datetime,
    due_at: Optional[datetime] = None,
    event_date: Optional[str] = None,
    created_by: str = "system",
    path: Optional[Path] = None,
) -> ScheduleResult:
    """Write ONE follow-up to the store under its FileLock.

    Refuses duplicates by (lead_id, type, due-day) — including previously
    suppressed and cancelled ones — unless `created_by="owner"`, which is a human
    deliberately asking for the nudge the system decided against.
    """
    if followup_type not in TEMPLATE_KEYS:
        return ScheduleResult(False, reason="unknown_type")
    due = due_at or compute_due(followup_type, now=now, event_date=event_date)
    if due is None:
        return ScheduleResult(False, reason="no_due_time")

    path = path or followups_path()
    with FileLock(followups_lock(path)):
        store, status = load_store(path)
        if status not in ("ok", "missing", "empty"):
            return ScheduleResult(False, reason="store_unreadable")
        if created_by != "owner":
            existing = find_duplicate(store, lead_id, followup_type, due)
            if existing is not None:
                return ScheduleResult(False, followup=existing, reason="duplicate")
        followup = CateringFollowup(
            followup_id=next_followup_id(store),
            lead_id=lead_id,
            followup_type=followup_type,
            due_at=due,
            created_at=now,
            created_by=created_by,
            status="scheduled",
            message_template_key=TEMPLATE_KEYS[followup_type],
        )
        store.followups.append(followup)
        store.next_sequence = int(followup.followup_id[2:]) + 1
        save_store(store, path)
    return ScheduleResult(True, followup=followup)


_DEFAULT_DECISIONS_LOG = "/opt/shift-agent/logs/decisions.log"


def _decisions_log_path() -> Path:
    """The audit chokepoint, resolved at call time. Both env names are honoured
    because the catering scripts use SHIFT_AGENT_LOG_PATH while the platform
    modules and the pytest isolation fixture use SHIFT_AGENT_DECISIONS_LOG_PATH —
    a trigger firing inside a script must land in the same file the script's own
    rows do."""
    return Path(
        os.environ.get("SHIFT_AGENT_LOG_PATH")
        or os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH")
        or _DEFAULT_DECISIONS_LOG
    )


def schedule_best_effort(
    lead_id: str,
    followup_type: str,
    *,
    now: datetime,
    enabled: bool,
    due_at: Optional[datetime] = None,
    event_date: Optional[str] = None,
    created_by: str = "system",
    trigger: str = "",
    path: Optional[Path] = None,
) -> Optional[CateringFollowup]:
    """Schedule ONE follow-up from inside a send path, and NEVER raise.

    This is what the trigger sites call. A follow-up is a courtesy; the send path
    hosting the trigger is the product. Anything that goes wrong here — a
    duplicate, an unreadable store, a schema regression — is logged and swallowed,
    because a quote that reaches the customer and then fails because its optional
    reminder could not be written is a strictly worse outcome than a missing
    reminder.

    `enabled` is the caller's config gate (cfg.catering_followup.enabled) passed
    explicitly rather than read here, so the call site reads as gated code.
    Returns the created follow-up, or None for every no-op and failure alike.
    """
    if not enabled:
        return None
    try:
        result = schedule_followup(
            lead_id, followup_type, now=now, due_at=due_at, event_date=event_date,
            created_by=created_by, path=path,
        )
        if not result.ok or result.followup is None:
            return None
        _emit_scheduled(result.followup, trigger=trigger, now=now)
        return result.followup
    except Exception as e:  # noqa: BLE001 — a follow-up must never break its host
        _emit_schedule_failure(lead_id, followup_type, e)
        return None


def _emit_scheduled(followup: CateringFollowup, *, trigger: str, now: datetime) -> None:
    from safe_io import flock, ndjson_append  # lazy: keeps import cost off cold paths
    from schemas import CateringFollowupScheduled

    entry = CateringFollowupScheduled(
        type="catering_followup_scheduled", ts=now,
        followup_id=followup.followup_id, lead_id=followup.lead_id,
        followup_type=followup.followup_type, due_at=followup.due_at,
        created_by=followup.created_by, trigger=trigger[:100],
    )
    path = _decisions_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with flock(path):
        ndjson_append(path, entry.model_dump_json())


def _emit_schedule_failure(lead_id: str, followup_type: str, exc: BaseException) -> None:
    """A scheduling failure is a HEALTH event, not a follow-up event — there is no
    follow-up to attach a follow-up row to. Best-effort on top of best-effort: if
    even this fails, stderr is the last stop."""
    detail = (
        f"catering follow-up scheduling failed for lead={lead_id} "
        f"type={followup_type}: {type(exc).__name__}: {exc}"
    )
    try:
        from safe_io import flock, ndjson_append
        from schemas import HealthCheckFailure

        entry = HealthCheckFailure(
            type="health_check_failure", ts=datetime.now(timezone.utc),
            check="catering_followup_schedule", detail=detail[:500],
        )
        path = _decisions_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with flock(path):
            ndjson_append(path, entry.model_dump_json())
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stderr.write(detail + "\n")
    except Exception:
        pass


# ── Suppression ──────────────────────────────────────────────────────────────
def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute from a model OR key from a dict — leads reach this module both
    ways (validated CateringLead from a script, raw dict from a read-only CLI)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Is `hour` inside the customer-facing quiet window?

    Wrapping window (start > end, the 21->9 default): quiet when hour >= start OR
    hour < end. Non-wrapping (start < end): quiet when start <= hour < end.
    start == end disables the check entirely rather than meaning "always quiet" —
    a misconfiguration should not mute the agent forever.

    Boundaries: `start` is quiet, `end` is not. 20:59 sends, 21:00 does not;
    08:59 does not, 09:00 does.
    """
    if start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def _prior_budget_consumers(
    lead_followups: Iterable[CateringFollowup], this_id: str,
) -> list[CateringFollowup]:
    return [
        f for f in lead_followups
        if f.followup_id != this_id
        and f.created_by == "system"
        and f.status in _BUDGET_CONSUMING_STATUSES
    ]


def _last_touch(followup: CateringFollowup) -> datetime:
    """When this follow-up last reached someone. `last_attempt_at` when the sweep
    stamped it; `due_at` otherwise (a record with neither has not run yet)."""
    return followup.last_attempt_at or followup.due_at


def allowed_statuses_for(followup_type: str) -> frozenset:
    return FOLLOWUP_ALLOWED_LEAD_STATUSES | FOLLOWUP_EXTRA_ALLOWED_STATUSES.get(
        followup_type, frozenset()
    )


def suppression_check(
    lead: Any,
    followup: CateringFollowup,
    automation_mode: Optional[str],
    now: datetime,
    *,
    cfg: Any = None,
    lead_followups: Sequence[CateringFollowup] = (),
    kill_switch_engaged: bool = False,
) -> Tuple[bool, str]:
    """(suppressed, reason). `reason` is "" when the follow-up may proceed.

    Checks run MOST-TOTAL FIRST so the recorded reason is the most important true
    one: an opted-out customer during quiet hours records `automation_suppressed`,
    not `quiet_hours`, because the opt-out is the fact that matters and the quiet
    window is incidental.

      kill_switch          the whole agent is off
      automation_suppressed  this conversation is paused / opted_out / takeover
      lead_missing         the follow-up's lead is gone (no cold outreach, ever)
      lead_on_hold         M4/G4 per-lead hold
      lead_status_not_allowed  terminal, or a status this engine does not know
      customer_declined    the lead records a decline (M3 field, read defensively)
      frequency_cap        the lifetime budget for system follow-ups is spent
      min_interval         too soon after the previous one
      quiet_hours          outside the customer-facing window

    `automation_mode` is the conversation's effective automation-control mode, or
    None when the kernel is not armed for that chat. `cfg` is a
    CateringFollowupConfig (defaults are used when omitted).
    """
    if kill_switch_engaged:
        return True, "kill_switch"

    if automation_mode:
        # Imported lazily: this module is imported by scripts that never touch the
        # kernel, and SUPPRESSED_MODES is the ONE definition of "muted".
        try:
            from automation_control import SUPPRESSED_MODES
        except Exception:  # pragma: no cover — defensive; kernel is deployed flat
            SUPPRESSED_MODES = frozenset({"paused", "opted_out", "takeover"})
        if automation_mode in SUPPRESSED_MODES:
            return True, "automation_suppressed"

    if lead is None:
        return True, "lead_missing"

    if bool(_attr(lead, "on_hold", False)):
        return True, "lead_on_hold"

    status = _attr(lead, "status")
    if status not in allowed_statuses_for(followup.followup_type):
        return True, "lead_status_not_allowed"

    # M3 may add a customer-acceptance field; read it defensively so this gate is
    # correct the moment the field lands and inert (not broken) before it does.
    if _attr(lead, "customer_acceptance") == "declined":
        return True, "customer_declined"

    max_per_lead = int(_attr(cfg, "max_followups_per_lead", 3) or 3)
    min_hours = int(_attr(cfg, "min_hours_between", 24) or 24)
    if followup.created_by == "system":
        prior = _prior_budget_consumers(lead_followups, followup.followup_id)
        if len(prior) >= max_per_lead:
            return True, "frequency_cap"
        if prior:
            most_recent = max(_last_touch(f) for f in prior)
            if now - most_recent < timedelta(hours=min_hours):
                return True, "min_interval"

    start = int(_attr(cfg, "quiet_hours_start", 21) or 0)
    end = int(_attr(cfg, "quiet_hours_end", 9) or 0)
    if in_quiet_hours(now.hour, start, end):
        return True, "quiet_hours"

    return False, ""


# ── Selection helpers ────────────────────────────────────────────────────────
def due_followups(
    store: CateringFollowupStore, now: datetime,
) -> list[CateringFollowup]:
    """Scheduled follow-ups whose time has come, oldest-due first so a backlog
    drains in the order it accumulated."""
    return sorted(
        (f for f in store.followups if f.status == "scheduled" and f.due_at <= now),
        key=lambda f: f.due_at,
    )


def card_expired(followup: CateringFollowup, now: datetime,
                 *, ttl_hours: int = CARD_TTL_HOURS) -> bool:
    """Has an awaiting_owner_approval card outlived its approval window?"""
    if followup.status != "awaiting_owner_approval":
        return False
    stamped = followup.last_attempt_at
    if stamped is None:
        return False
    return now - stamped >= timedelta(hours=ttl_hours)


def for_lead(store: CateringFollowupStore, lead_id: str) -> list[CateringFollowup]:
    return [f for f in store.followups if f.lead_id == lead_id]


def by_code(store: CateringFollowupStore, code: str) -> Optional[CateringFollowup]:
    """The follow-up awaiting approval under this code. Only
    `awaiting_owner_approval` and `approved_sent` records are matched: the first
    is the live card, the second is what makes an owner's repeated reply an
    idempotent replay rather than a not-found error."""
    for f in store.followups:
        if f.approval_code == code and f.status in ("awaiting_owner_approval", "approved_sent"):
            return f
    return None


def live_codes(path: Optional[Path] = None) -> set:
    """Approval codes still in play — the exclusion set a code generator must
    respect. Best-effort: an unreadable store returns an empty set rather than
    raising into a mint path (the cross-pool registry is the authoritative
    exclusion; this is the follow-up store's contribution to it)."""
    try:
        store, status = load_store(path)
        if status not in ("ok", "missing", "empty"):
            return set()
        return {
            f.approval_code for f in store.followups
            if f.approval_code and f.status in LIVE_FOLLOWUP_STATUSES
        }
    except Exception as e:  # noqa: BLE001 — never raise into a caller's mint path
        try:
            sys.stderr.write(f"catering_followups.live_codes failed (non-fatal): {e}\n")
        except Exception:
            pass
        return set()


__all__ = [
    "INCOMPLETE_QUALIFICATION_HOURS", "PROPOSAL_UNANSWERED_HOURS",
    "EVENT_APPROACHING_DAYS_BEFORE", "FINAL_HEADCOUNT_DAYS_BEFORE",
    "POST_EVENT_FEEDBACK_DAYS_AFTER",
    "EVENT_ANCHORED_TYPES", "TEMPLATE_KEYS",
    "BRIDGE_TEMPLATE_PREFIX", "with_bridge_prefix",
    "CARD_TTL_HOURS", "MAX_CARD_ATTEMPTS",
    "FOLLOWUP_ALLOWED_LEAD_STATUSES", "FOLLOWUP_EXTRA_ALLOWED_STATUSES",
    "LIVE_FOLLOWUP_STATUSES",
    "followups_path", "followups_lock", "load_store", "save_store",
    "next_followup_id", "dedup_key", "find_duplicate", "compute_due",
    "ScheduleResult", "schedule_followup", "schedule_best_effort",
    "in_quiet_hours", "allowed_statuses_for", "suppression_check",
    "due_followups", "card_expired", "for_lead", "by_code", "live_codes",
]
