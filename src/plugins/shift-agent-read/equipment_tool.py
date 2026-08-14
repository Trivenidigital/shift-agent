"""`get_equipment_maintenance_due` — owner-facing read over state/equipment-items.json.

Thin adapter, mirroring `compliance_tool`. It owns identity, authorization, a
validated store read and date arithmetic. It owns no language beyond the
deterministic zero-state replies below.

WHY A PLUGIN TOOL AND NOT A DISPATCHER ROW. The live gateway disables the
`skills` and `terminal` toolsets (docs/runbooks/gateway-toolset-scoping.md), so
a SKILL routed from `dispatch_shift_agent` cannot be invoked in production. The
`shift_agent_read` toolset survives that suppression by name, which is what makes
this path actually reachable.

THE STATE DISTINCTION IS THE POINT — same SUCCESSFUL outcomes compliance draws,
for the same reason, plus the config gate above them:

  disabled   — cfg.equipment_maintenance.enabled is false, or the block is absent
               and defaults to false. The store is never read; the owner is told
               tracking is not switched on, which is not "nothing due".
  missing    — no items file. Nothing is configured, so coverage is UNKNOWN and
               this state carries NO counts and NO items list.
  empty      — file present, zero assets. Configured, nothing TRACKED.
  populated  — assets exist. `in_window` may be 0, which means nothing TRACKED
               is due inside the window — not that the equipment is fine.

Everything else FAILS CLOSED. An unreadable store and an unresolvable customer
timezone are not empty results — they return `ok: false` with no counts.

Each zero state binds a deterministic reply to the exact turn via safe_io, and a
failure to bind suppresses the payload entirely. Positive rows bind nothing;
Hermes keeps presentation ownership there.

Rows omit `vendor_phone` and `serial`. The owner can ask for a vendor by name and
follow up out of band; putting a phone number and an asset serial into every
routine "what's due?" answer widens what a single read discloses for no gain.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

from .identity import fail, ok, refuse, require_owner

TOOLSET = "shift_agent_read"
TOOL_NAME = "get_equipment_maintenance_due"

DESCRIPTION = (
    "Read the authenticated owner's tracked equipment list — refrigeration, "
    "cooking equipment, POS terminals, vehicles, HVAC, fire suppression — and "
    "return what is due or overdue for service. Use for questions about "
    "equipment maintenance, preventive service, when something is next "
    "serviced, or which vendor services an asset. This is separate from the "
    "compliance calendar, which covers licences, permits and filings.\n"
    "\n"
    "SCOPE. This tool knows only what is in the owner's TRACKED equipment list. "
    "It is never evidence about equipment outside that list.\n"
    "  - 'disabled': equipment tracking is switched off for this business, so "
    "nothing was checked. This is NOT a statement that nothing is due.\n"
    "  - 'missing': equipment tracking is not configured and coverage is "
    "unavailable. This does NOT establish that nothing needs service.\n"
    "  - 'empty': the source is configured and holds zero TRACKED assets. That "
    "means nothing is tracked, NOT that the equipment is up to date.\n"
    "  - 'populated' with zero due in the window: no TRACKED asset falls in the "
    "checked window. Do not generalize beyond the tracked list.\n"
    "\n"
    "RULES. Report only assets, dates and vendors this tool returned — never "
    "invent an asset or a service date, and say an asset is not tracked rather "
    "than guessing. Never contact a vendor or offer to: v0.1 is owner-mediated "
    "end to end, and naming the vendor to the owner is the most this does. "
    "Never present a service date as a safety or legal-compliance judgement — "
    "an overdue fire-suppression date is a date, not a code violation. "
    "This tool is READ-ONLY: marking service complete, rescheduling and "
    "breakdown intake are not wired up yet - if the owner asks to record or "
    "change anything, say plainly that it is not wired up yet rather than "
    "implying it was done.\n"
    "\n"
    "WINDOW: supply window_days ONLY when the user states a bounded timeframe "
    "such as 'next 60 days' or 'before October'. For generic wording such as "
    "'coming up', 'due', 'soon', or 'what maintenance is due?', OMIT window_days "
    "so the deterministic 30-day default applies."
)

# Deterministic replies bound to the turn for every zero state. Bounded strings,
# never model-generated. Positive rows get none — Hermes presents real dates.
TPL_DISABLED = (
    "Equipment maintenance tracking isn't enabled for this business yet, so I "
    "can't tell you what equipment is due for service."
)
TPL_MISSING = (
    "Equipment maintenance tracking is not configured, so I can't determine "
    "what equipment is due for service."
)
TPL_EMPTY = (
    "Your equipment list is configured, but it currently has no tracked "
    "equipment."
)
TPL_POPULATED_ZERO = (
    "None of the equipment currently tracked is due for service within the "
    "next {window_days} days."
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
                                "and the deterministic 30-day default applies."),
            }
        },
        "required": [],
    },
}

DEFAULT_WINDOW_DAYS = 30
PLATFORM_DIR = str(Path(__file__).resolve().parent.parent.parent / "platform")
CONFIG_PATH = Path(os.environ.get("SHIFT_AGENT_CONFIG_PATH",
                                  "/opt/shift-agent/config.yaml"))
ITEMS_PATH = Path(os.environ.get("SHIFT_AGENT_EQUIPMENT_ITEMS_PATH",
                                 "/opt/shift-agent/state/equipment-items.json"))


def _ensure_platform_path() -> None:
    """Idempotently put the platform modules on sys.path — same guarded pattern
    cf-router uses, so repeated calls don't grow sys.path."""
    for p in ("/opt/shift-agent", PLATFORM_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def _config():
    """The validated Config, or None when it cannot be read.

    A helper rather than an inline read (compliance_tool inlines its one use)
    because the enable gate needs config even when SHIFT_AGENT_NOW_OVERRIDE
    short-circuits the timezone lookup below.
    """
    try:
        import yaml
        from schemas import Config
        return Config.model_validate(
            yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        return None


def _today(cfg) -> date | None:
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
        from safe_io import customer_now
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
    egress — and that mismatch fails by letting unqualified text through.
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

    cfg = _config()
    if cfg is None:
        # Whether the agent is enabled could not be established. Reporting
        # "not enabled" here would be a claim about configuration we failed to
        # read, and reporting anything else would read the store without a gate.
        return fail("config_unavailable")
    if not cfg.equipment_maintenance.enabled:
        # EquipmentMaintenanceConfig.enabled defaults False and an absent block
        # validates to that default, so an unconfigured box lands here and the
        # store is never read.
        if not _bind_outbound(TPL_DISABLED):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="disabled", coverage_status="not_enabled")

    window_days = _window(args)

    # Existence is checked BEFORE loading so `missing` can never be reported as
    # `empty`: a loader that materialises a default container would erase the
    # distinction the owner most needs.
    if not ITEMS_PATH.exists():
        if not _bind_outbound(TPL_MISSING):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="missing", coverage_status="not_configured")

    from safe_io import load_model
    from schemas import EquipmentItemsFile
    try:
        store, _ = load_model(ITEMS_PATH, EquipmentItemsFile)
    except Exception:
        # Corrupt or schema-invalid store. NOT an empty result — the owner's
        # equipment may well be tracked and simply be unreadable right now.
        return fail("state_unreadable")

    if not store.items:
        if not _bind_outbound(TPL_EMPTY):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="empty", window_days=window_days,
                  tracked_total=0, in_window=0, items=[])

    today = _today(cfg)
    if today is None:
        return fail("customer_timezone_unavailable")

    rows = sorted(
        ({
            "id": i.id,
            "name": i.name,
            "category": i.category,
            "next_service_date": i.next_service_date.isoformat(),
            "days_until": (i.next_service_date - today).days,
            "vendor_name": i.vendor_name,
            "location_id": i.location_id,
        } for i in store.items),
        key=lambda r: r["days_until"],
    )
    in_window = [r for r in rows if r["days_until"] <= window_days]
    if not in_window:
        # Tracked assets exist but none are due. This is the steady state for a
        # real customer, and the one a model most reliably over-generalizes.
        if not _bind_outbound(TPL_POPULATED_ZERO.format(window_days=window_days)):
            return refuse("outbound_truthfulness_guard_unavailable")
    # Positive rows bind nothing: Hermes owns presenting real service dates.
    return ok(source_status="populated", window_days=window_days,
              tracked_total=len(rows), in_window=len(in_window), items=in_window)
