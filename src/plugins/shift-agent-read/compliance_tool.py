"""`get_compliance_deadlines` — owner-facing read over state/compliance-items.json.

Thin adapter. It owns identity, authorization, a validated store read and date
arithmetic. It owns no language: no phrasing, no prioritisation prose, no
clarification. Hermes reads the JSON and writes the answer.

THE STATE DISTINCTION IS THE POINT. Three SUCCESSFUL outcomes that must never
collapse into each other:

  missing    — no items file. No authoritative source is configured. Coverage is
               UNKNOWN, so this state carries NO counts and NO items list: zeros
               here would be absence-shaped data about something unmeasured.
  empty      — file present, zero records. Configured, nothing TRACKED.
  populated  — records exist. `in_window` may be 0, which means nothing TRACKED
               is due inside the window — not that nothing is due.

Everything else FAILS CLOSED. An unreadable store and an unresolvable customer
timezone are not empty results — they are failures, and they return `ok: false`
with no `items` and no counts.

WHY THE ZERO STATES BIND AN OUTBOUND REPLY. Getting the JSON right is necessary
and was proven insufficient: with the three states distinguished AND every field
self-scoped, the model still rendered a zero as an unqualified "you have no
compliance deadlines" in 1 of 5 byte-identical runs. So each zero state binds a
deterministic reply to the exact turn via safe_io, and the gateway egress seam
substitutes it without reading the model's wording. Binding happens BEFORE the
payload is returned, and a failure to bind suppresses the payload entirely —
there is no execution path on which an authoritative zero ships unqualified.
Positive rows bind nothing; Hermes keeps presentation ownership there.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

from .identity import fail, ok, refuse, require_owner

TOOLSET = "shift_agent_read"
TOOL_NAME = "get_compliance_deadlines"

DESCRIPTION = (
    "Read the authenticated owner's tracked compliance calendar — licence "
    "renewals, permit and tax filings, inspections and certifications — and "
    "return upcoming due dates. Use for questions about compliance deadlines, "
    "licence renewal, permits, filings, inspections, or what is due. This is "
    "separate from the general todo list.\n"
    "\n"
    "SCOPE. This tool knows only what is in the owner's TRACKED compliance "
    "calendar. It is never evidence about obligations outside that calendar.\n"
    "  - 'missing': tracking is not configured and coverage is unavailable. "
    "This does NOT establish that the owner has no deadlines or obligations.\n"
    "  - 'empty': the source is configured and holds zero TRACKED records. That "
    "means nothing is currently tracked, NOT that there are no obligations.\n"
    "  - 'populated' with zero due in the window: no TRACKED deadline falls in "
    "the checked window. Do not generalize beyond the tracked calendar.\n"
    "\n"
    "WINDOW: supply window_days ONLY when the user states a bounded timeframe "
    "such as 'next 30 days' or 'before October 1'. For generic wording such as "
    "'coming up', 'upcoming', 'soon', or 'what compliance deadlines do I have?', "
    "OMIT window_days so the deterministic 90-day default applies."
)

# Deterministic replies bound to the turn for every zero state. Bounded strings,
# never model-generated. Positive rows get none — Hermes presents real deadlines.
TPL_MISSING = (
    "Compliance tracking is not configured, so I can't determine which "
    "compliance deadlines you have coming up."
)
TPL_EMPTY = (
    "Your compliance calendar is configured, but it currently has no tracked "
    "compliance records."
)
TPL_POPULATED_ZERO = (
    "None of the compliance deadlines currently tracked in your calendar are "
    "due within the next {window_days} days."
)

# registry.register() takes the INNER function object; get_definitions() adds the
# {"type":"function","function":...} wrap. Double-wrapping silently empties
# function.description, which strips the tool's line from the deferred catalog
# and makes it undiscoverable.
SCHEMA = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    # Bounded, not free-form: the model picks a number in range, it does not get
    # to express a window in prose that Python would have to interpret.
    "parameters": {
        "type": "object",
        "properties": {
            "window_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "description": ("Look-ahead window in days. Supply ONLY for an "
                                "explicit user-stated timeframe; omit otherwise "
                                "and the deterministic 90-day default applies."),
            }
        },
        "required": [],
    },
}

DEFAULT_WINDOW_DAYS = 90
PLATFORM_DIR = str(Path(__file__).resolve().parent.parent.parent / "platform")
CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH",
                                  "/opt/shift-agent/config.yaml"))
ITEMS_PATH = Path(os.environ.get("SHIFT_AGENT_COMPLIANCE_ITEMS_PATH",
                                 "/opt/shift-agent/state/compliance-items.json"))


def _ensure_platform_path() -> None:
    """Idempotently put the platform modules on sys.path — same guarded pattern
    cf-router uses, so repeated calls don't grow sys.path."""
    for p in ("/opt/shift-agent", PLATFORM_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def _today() -> date | None:
    """Today in the CUSTOMER's timezone, or None if that cannot be established.

    No UTC fallback. `days_until` is the number the owner acts on; deriving it
    from a guessed timezone would produce an authoritative-looking answer from an
    unknown basis. Callers must fail closed on None.
    """
    override = os.environ.get("SHIFT_AGENT_NOW_OVERRIDE", "")
    if override:
        try:
            return datetime.fromisoformat(override).date()
        except ValueError:
            return None
    try:
        import yaml
        from safe_io import customer_now
        from schemas import Config
        cfg = Config.model_validate(
            yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))
        return customer_now(cfg.customer.timezone).date()
    except Exception:
        return None


def _window(args) -> int:
    raw = (args or {}).get("window_days", DEFAULT_WINDOW_DAYS)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return min(365, max(1, n))


def _bind_outbound(text: str) -> bool:
    """Bind the deterministic reply to THIS turn. Must run inside the handler.

    `HERMES_SESSION_ID` is reassigned by `AIAgent.run_conversation`, so a key
    read any earlier will not match the one the WhatsApp adapter resolves at
    egress — and that mismatch fails by letting unqualified text through. There
    is deliberately no earlier registration point.
    """
    _ensure_platform_path()
    try:
        from safe_io import register_turn_outbound_override
        return bool(register_turn_outbound_override(text))
    except Exception:
        return False


def handler(args=None, **kwargs) -> str:
    """Model arguments arrive positionally and carry no identity — see identity.py."""
    authorized, refusal = require_owner()
    if not authorized:
        return refusal

    _ensure_platform_path()
    window_days = _window(args)

    # Existence is checked BEFORE loading so `missing` can never be reported as
    # `empty`: a loader that materialises a default container would erase the
    # distinction the owner most needs.
    if not ITEMS_PATH.exists():
        # No zero-shaped fields: coverage is unknown, not zero. And the reply is
        # bound BEFORE the result is returned, so there is no path on which an
        # unqualified absence can ship.
        if not _bind_outbound(TPL_MISSING):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="missing", coverage_status="not_configured")

    from safe_io import load_model
    from schemas import ComplianceItemsFile
    try:
        store, _ = load_model(ITEMS_PATH, ComplianceItemsFile)
    except Exception:
        # Corrupt or schema-invalid store. NOT an empty result — the owner's
        # deadlines may well exist and simply be unreadable right now.
        return fail("state_unreadable")

    if not store.items:
        if not _bind_outbound(TPL_EMPTY):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="empty", window_days=window_days,
                  tracked_total=0, in_window=0, items=[])

    today = _today()
    if today is None:
        return fail("customer_timezone_unavailable")

    rows = sorted(
        ({
            "id": i.id,
            "name": i.name,
            "category": i.category,
            "renewal_date": i.renewal_date.isoformat(),
            "days_until": (i.renewal_date - today).days,
            "agency": i.agency,
            "location_id": i.location_id,
        } for i in store.items),
        key=lambda r: r["days_until"],
    )
    in_window = [r for r in rows if r["days_until"] <= window_days]
    if not in_window:
        # Tracked rows exist but none are due. This is the steady state for a
        # real customer, and the one the model most reliably over-generalized.
        if not _bind_outbound(TPL_POPULATED_ZERO.format(window_days=window_days)):
            return refuse("outbound_truthfulness_guard_unavailable")
    # Positive rows bind nothing: Hermes owns presenting real deadlines.
    return ok(source_status="populated", window_days=window_days,
              tracked_total=len(rows), in_window=len(in_window), items=in_window)
