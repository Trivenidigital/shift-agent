"""`find_nearest_location` — customer-facing nearest-store read.

Thin adapter over `src/agents/multi_location/scripts/closest-location.py`, which
already wraps the bundled `productivity/maps` skill. Geocoding, distance,
ranking and the haversine fallback all live there and are NOT reimplemented
here.

PUBLIC BY DESIGN. Store locations, phone numbers and opening hours are
information a business publishes. This tool therefore has no owner gate and
never calls `identify-sender`. It reads only `cfg.multi_location.locations[]`
through the kernel, and its result carries nothing else — no roster, schedule,
pending proposals, catering leads or customer records.

WHY SUCCESS IS OVERRIDDEN, when compliance's success path is not. The risks are
opposite. For compliance the danger was the model over-claiming ABSENCE, so rows
that existed were safe to hand to Hermes. Here the danger is corrupting FACTS —
the multi-location directive rates a wrong address, phone number or set of hours
HIGH because it sends real people to the wrong place. So the factual success path
is exactly the one that must be deterministic. The bounded cost, accepted
deliberately: a compound question ("closest store, and do you deliver?") has its
location half answered and the other half dropped for that turn. A dropped
follow-up is recoverable; a wrong address is not.

DRIVE TIMES ARE ESTIMATES. `closest-location.py:129-147` returns None from
`osrm_distance()` unconditionally (documented v0.1 HOTFIX), so `haversine_fallback`
is the only source in production today and `drive_minutes` is a straight-line
estimate with an urban detour factor. The customer wording says so. If OSRM is
ever wired the kernel will report `source="osrm"` and the phrasing switches on
that field rather than on a hard-coded assumption.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .identity import ok, refuse

TOOLSET = "shift_agent_read"
TOOL_NAME = "find_nearest_location"

DESCRIPTION = (
    "Find the business's closest store locations to a place the customer names, "
    "ranked nearest first, and return each store's address, phone and opening "
    "hours. Use for questions like 'which store is closest to 75001', 'what's "
    "your nearest location to Cary NC', or 'where are you located'.\n"
    "\n"
    "Pass the city, ZIP or street address the customer gave. If they have not "
    "named a place yet, ask them for one before calling this — do not guess a "
    "location.\n"
    "\n"
    "top_n: use 1 when the customer asks for THE closest/nearest store, use the "
    "number they asked for when they name one (e.g. 'closest 3'), and otherwise "
    "leave it out for the small default."
)

# registry.register() takes the INNER function object; get_definitions() adds the
# {"type":"function","function":...} wrap. Double-wrapping empties
# function.description and removes the tool from the deferred catalog.
SCHEMA = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "minLength": 1,
                "maxLength": 300,
                "description": ("The city, ZIP code or street address the "
                                "customer gave."),
            },
            "top_n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": ("How many stores to return. 1 for 'the closest "
                                "store'; omit for the default."),
            },
        },
        "required": ["address"],
    },
}

DEFAULT_TOP_N = 3
MAX_ADDRESS_LEN = 300
KERNEL_TIMEOUT_SEC = 30

PLATFORM_DIR = str(Path(__file__).resolve().parent.parent.parent / "platform")
CLOSEST_LOCATION_BIN = os.environ.get(
    "SHIFT_AGENT_CLOSEST_LOCATION_BIN", "/usr/local/bin/closest-location.py"
)
PYTHON_BIN = os.environ.get("SHIFT_AGENT_PYTHON_BIN", sys.executable)

# Kernel exit codes — see closest-location.py module docstring.
_EXIT_OK, _EXIT_INVALID_INPUT, _EXIT_CONFIG_EMPTY, _EXIT_ALL_DOWN = 0, 1, 2, 3

# The audit variant's `source` Literal. A state outside this set is left
# unaudited rather than described with a source that did not happen.
_AUDITABLE_SOURCES = ("osrm", "haversine_fallback", "not_configured")

# Customer-safe wording. `not_configured` and `no_usable_locations` deliberately
# share a reply — the customer cannot act on the difference — while the
# structured result keeps them distinct, because the operator can.
TPL_UNAVAILABLE = (
    "Store-location information isn't available right now. Please contact the "
    "store directly."
)
TPL_INPUT_UNRESOLVED = (
    "I couldn't resolve that location. Please send a city, ZIP code, or street "
    "address."
)
TPL_CLOSING = "Anything else?"


def _ensure_platform_path() -> None:
    """Idempotent guarded insert, matching cf-router's pattern."""
    for p in ("/opt/shift-agent", PLATFORM_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def _bind_outbound(text: str) -> bool:
    """Bind the deterministic reply to THIS turn. Must run inside the handler:
    `AIAgent.run_conversation` reassigns HERMES_SESSION_ID, and a key read
    earlier fails OPEN, letting unvalidated store facts through."""
    _ensure_platform_path()
    try:
        from safe_io import register_turn_outbound_override
        return bool(register_turn_outbound_override(text))
    except Exception:
        return False


def _audit(source: str, results: list, detail: str = "") -> None:
    """Emit the EXISTING `multi_location_closest_lookup` variant.

    Its `source` admits only osrm / haversine_fallback / not_configured, so it is
    written only where one of those is truthful. An unresolved customer input is
    never audited as `not_configured` — inventing a source to fit the schema
    would put a false operational fact in the durable log.

    The raw address never appears here; the variant omits it by design and the
    kernel already withholds it from stdout.
    """
    _ensure_platform_path()
    try:
        from audit_helpers import _append_best_effort
        from safe_io import customer_now
        from schemas import MultiLocationClosestLookup
        nearest = results[0] if results else None
        entry = MultiLocationClosestLookup(
            type="multi_location_closest_lookup",
            ts=customer_now("UTC"),
            chat_id=_chat_id(),
            nearest_location_id=(nearest or {}).get("location_id"),
            nearest_drive_minutes=(nearest or {}).get("drive_minutes"),
            n_locations_returned=len(results),
            source=source,
            detail=detail[:200],
        )
        _append_best_effort(entry.model_dump_json(),
                            Path(os.environ.get("SHIFT_AGENT_DECISIONS_LOG_PATH",
                                                "/opt/shift-agent/logs/decisions.log")))
    except Exception:
        pass  # audit is best-effort; it must never break a customer answer


def _answer(text: str, audit_source: str = "", audit_results=(),
            audit_detail: str = "", **fields) -> str:
    """The ONLY exit that returns facts. Binding is attempted first, and nothing
    factual — not a status, not a count, not a location — is returned unless it
    succeeded. Auditing is likewise downstream of the bind, so the log never
    records a lookup whose answer was refused rather than delivered."""
    if not _bind_outbound(text):
        return refuse("outbound_truthfulness_guard_unavailable")
    if audit_source:
        _audit(audit_source, list(audit_results), audit_detail)
    return ok(**fields)


def _chat_id() -> str:
    try:
        from gateway.session_context import get_session_env
        return get_session_env("HERMES_SESSION_CHAT_ID", "") or "unknown"
    except Exception:
        return "unknown"


def _render(source: str, results: list) -> str:
    """Bounded deterministic reply. Every field is emitted verbatim; nothing is
    inferred, reformatted or filled in."""
    live_routing = source == "osrm"
    if live_routing:
        head = "Nearest stores by driving time:"
    else:
        head = ("Estimated nearest store(s) — approximate location-based "
                "ranking, not live driving routes:")
    lines = [head]
    for i, r in enumerate(results, 1):
        parts = [str(r.get("name") or "").strip()]
        for key in ("address_short", "phone", "hours"):
            val = str(r.get(key) or "").strip()
            if val:
                parts.append(val)
        mins = r.get("drive_minutes")
        if mins is not None:
            parts.append(f"{round(float(mins))} min drive"
                         if live_routing else f"approx. {round(float(mins))} min away")
        lines.append(f"{i}. " + " — ".join(parts))
    lines.append(TPL_CLOSING)
    return "\n".join(lines)


def handler(args=None, **kwargs) -> str:
    """Model arguments arrive positionally and carry no identity or chat source."""
    args = args or {}

    address = str(args.get("address") or "").strip()
    if not address or len(address) > MAX_ADDRESS_LEN:
        return refuse("invalid_arguments")

    raw_top_n = args.get("top_n", DEFAULT_TOP_N)
    try:
        top_n = int(raw_top_n)
    except (TypeError, ValueError):
        return refuse("invalid_arguments")
    if not 1 <= top_n <= 5:
        # Schema rejection, not a silent clamp: a hidden correction would answer
        # a question the customer did not ask.
        return refuse("invalid_arguments")

    try:
        proc = subprocess.run(
            [PYTHON_BIN, CLOSEST_LOCATION_BIN,
             "--address", address, "--top-n", str(top_n)],
            capture_output=True, text=True, timeout=KERNEL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return _answer(TPL_UNAVAILABLE, status="no_usable_locations")

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}

    if proc.returncode == _EXIT_CONFIG_EMPTY:
        return _answer(TPL_UNAVAILABLE, audit_source="not_configured",
                       status="not_configured", n_locations_total=0)

    if proc.returncode == _EXIT_INVALID_INPUT:
        # No audit: the variant's `source` cannot describe an unresolved input,
        # and fabricating one would log a false operational fact.
        return _answer(TPL_INPUT_UNRESOLVED, status="input_unresolved")

    source = str(payload.get("source") or "")
    results = payload.get("results") or []

    if proc.returncode != _EXIT_OK or not results:
        return _answer(
            TPL_UNAVAILABLE,
            # Audited only when the kernel named a source the variant admits.
            audit_source=source if source in _AUDITABLE_SOURCES else "",
            audit_detail="no usable ranked locations",
            status="no_usable_locations",
            source=source or None,
            n_locations_total=payload.get("n_locations_total", 0))

    return _answer(_render(source, results),
                   audit_source=source, audit_results=results,
                   status="ranked",
                   source=source,
                   estimate=source != "osrm",
                   n_locations_total=payload.get("n_locations_total",
                                                 len(results)),
                   n_returned=len(results),
                   locations=results)
