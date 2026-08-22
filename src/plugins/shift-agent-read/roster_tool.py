"""`get_roster_capabilities` — owner-facing read over roster.json's employees.

Thin adapter, mirroring `compliance_tool` and `equipment_tool`. It owns identity,
authorization, a validated read and deterministic filtering. It owns no language
beyond the deterministic zero-state replies below.

`employees` ONLY — `schedule` IS DELIBERATELY NEVER READ. The key exists on the
model and is loaded by the validated read, but nothing here looks at it, and no
answer this tool gives is date-bound. Verified read-only on main-vps: the sole
writer of roster.json is `/usr/local/bin/shift-agent-lid-learn`, which mutates
for LID-learning only, and NO script anywhere assigns `schedule` — the live one
holds five dates ending 2026-05-04 and cannot be refreshed by any deployed path.
A schedule-derived answer would therefore be permanently, structurally stale, and
"who is working Tuesday?" is a question this tool must decline rather than answer
from a store nothing updates. The employee list has no such problem: it is not
date-bound, and lid-learn keeps its identity fields current.

THERE IS NO `disabled` STATE, AND ONE IS NOT INVENTED. Compliance and equipment
gate on `cfg.<agent>.enabled`; the roster has no such flag, because Shift is the
core agent and its roster is unconditional — `Config` has no `shift` block to
read. So this tool reads no config at all, and the fourth successful outcome its
siblings spend on `disabled` is spent here on a state that is real for THIS data:

  missing          — no roster file. Nothing is configured, so coverage is
                     UNKNOWN and this state carries NO counts and NO staff list.
  empty            — file present, zero employees. Configured, nobody recorded.
  no_active_staff  — employees exist, none with status "active". Distinct from
                     `empty` and load-bearing: "everyone on file is terminated"
                     is a different fact from "nobody is on file", and today 2 of
                     the 8 rows on the live box are terminated.
  populated        — active staff exist. `matched` may be 0, which means nobody
                     ACTIVE matches this request — not that nobody can do it.

Everything else FAILS CLOSED. An unreadable roster is not an empty roster; it
returns `ok: false` with no counts.

TERMINATED AND INACTIVE STAFF ARE NEVER RETURNED, and there is no argument to
ask for them. The question this tool answers is who can be asked to work, and a
terminated employee offered as coverage is the one wrong answer here that reaches
a real person. `roster_total` still reports the file's full row count so the
difference is visible to the owner without the rows being disclosed.

Each zero state binds a deterministic reply to the exact turn via safe_io, and a
failure to bind suppresses the payload entirely. "Nobody can cover the meat
counter" told to an owner who has a meat-counter employee is the same
over-generalization the compliance tool exists to prevent, with a shift left
uncovered at the end of it. Positive rows bind nothing; Hermes presents real
people.

Rows omit `phone`, `lid` and `phone_history`. This is not only a privacy call:
roster.json is the same file `identify-sender` resolves callers against, so those
three fields are the IDENTITY SURFACE. Putting them in a routine "who can cover?"
answer widens an authorization input into ordinary conversation.

EVERY RESULT CARRIES `roles_present`, `cover_roles_present` AND
`languages_present`. A filter argument that does not match anything is otherwise
indistinguishable from nobody being able to do the thing — and `languages` holds
ISO codes (`te`, `gu`), so a lookup for "telugu" would silently answer "nobody
speaks Telugu" about a roster where three people do. Naming what IS on the roster
turns that dead end into a retry, using only stored values and no invented
language table.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .identity import fail, ok, refuse, require_owner

TOOLSET = "shift_agent_read"
TOOL_NAME = "get_roster_capabilities"

# Mirrors schemas.Role. Duplicated into the tool SCHEMA because a JSON-Schema
# enum has to be a literal list at registration time, before the platform
# modules are importable. `test_schema_role_enum_matches_the_literal` pins it
# against the deployed Literal so it cannot drift silently.
ROLES = ["cashier", "bakery", "meat_counter", "sweets", "floor",
         "prep", "cook", "server", "dishwasher", "manager"]

DESCRIPTION = (
    "Read the authenticated owner's staff roster and return ACTIVE employees "
    "with the roles each one can cover, the languages they speak, and any "
    "recorded restrictions. Use for questions like 'who can cover the meat "
    "counter?', 'which staff speak Telugu?', 'who is on the roster?', or 'is "
    "Priya still with us?'.\n"
    "\n"
    "SCOPE. This tool knows only who is RECORDED on the roster, and only their "
    "capabilities — it does NOT know the schedule, who is working today, who is "
    "off, or who has already been asked. Never answer a shift, rota, date or "
    "availability question from this tool; say the schedule is not something you "
    "can see.\n"
    "  - 'missing': the roster is not configured and coverage is unavailable. "
    "This does NOT establish that the business has no staff.\n"
    "  - 'empty': the roster is configured and holds zero employees. That means "
    "nobody is recorded, NOT that nobody works here.\n"
    "  - 'no_active_staff': employees are on file but none is active. Say that "
    "plainly — it is NOT the same as an empty roster.\n"
    "  - 'populated' with matched 0: no ACTIVE employee matched THIS request. "
    "Before reporting that nobody can do it, check roles_present, "
    "cover_roles_present and languages_present in the result — languages are "
    "stored as ISO codes ('te' for Telugu, 'gu' for Gujarati, 'ta' for Tamil), "
    "so a miss is usually the wrong code, not an absence. Retry with a value "
    "that appears in those lists.\n"
    "\n"
    "RULES. Report only the people, roles, languages and restrictions this tool "
    "returned — never invent an employee, a capability or a language, and say "
    "someone is not on the roster rather than guessing. Only ACTIVE staff are "
    "ever returned: inactive and terminated employees are excluded and cannot be "
    "requested, so never present anyone as available who is not in the result. "
    "Always respect a returned `restrictions` value when suggesting who could "
    "cover something. Never read out or ask for a phone number — this tool "
    "deliberately returns none, and the roster's contact fields are what the "
    "system uses to identify callers. This tool is READ-ONLY: it cannot message "
    "anyone, assign a shift, arrange cover or change the roster. If the owner "
    "asks you to contact someone, say plainly that it is not wired up rather "
    "than implying it was done."
)

# Deterministic replies bound to the turn for every zero state. Bounded strings,
# never model-generated. Positive rows get none — Hermes presents real people.
TPL_MISSING = (
    "The staff roster isn't configured, so I can't tell you who is on it or "
    "what they can cover."
)
TPL_EMPTY = (
    "Your staff roster is configured, but it currently has no employees on it."
)
TPL_NO_ACTIVE = (
    "There are {roster_total} people on your roster, but none of them is "
    "currently marked active."
)
TPL_NO_MATCH = (
    "None of your {active_total} active staff match that. The roles your active "
    "staff can cover are: {cover_roles}. The languages they speak are: "
    "{languages}."
)

# registry.register() takes the INNER function object; get_definitions() adds the
# {"type":"function","function":...} wrap. Double-wrapping silently empties
# function.description, which strips the tool's line from the deferred catalog
# and makes it undiscoverable.
#
# There is deliberately NO argument for including inactive or terminated staff.
# An argument that can surface someone who no longer works here is the one wrong
# answer this tool could give that reaches a real person.
SCHEMA = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "can_cover_role": {
                "type": "string",
                "enum": ROLES,
                "description": ("Return only staff who can cover this role. Use "
                                "for 'who can cover the meat counter?'."),
            },
            "language": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
                "description": ("Language code as stored on the roster, e.g. "
                                "'te' for Telugu, 'hi' for Hindi, 'gu' for "
                                "Gujarati, 'ta' for Tamil, 'en' for English. "
                                "Check languages_present if a value returns "
                                "nothing."),
            },
            "name_contains": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": ("Name or nickname fragment, for 'is Priya on "
                                "the roster?'. Case-insensitive."),
            },
        },
        "required": [],
    },
}

# Counts are always exact; only the row list is capped. The live roster holds 8
# people, so this is a bound on pathology, not on normal use.
MAX_ROWS = 50
MAX_QUERY_LEN = 80

PLATFORM_DIR = str(Path(__file__).resolve().parent.parent.parent / "platform")
# Same env var and default the deployed writer uses
# (src/agents/shift/scripts/shift-agent-lid-learn:36), so a test override and the
# production path stay one definition apart, not two.
ROSTER_PATH = Path(os.environ.get("SHIFT_AGENT_ROSTER_PATH",
                                  "/opt/shift-agent/roster.json"))


def _ensure_platform_path() -> None:
    """Idempotently put the platform modules on sys.path — same guarded pattern
    cf-router uses, so repeated calls don't grow sys.path."""
    for p in ("/opt/shift-agent", PLATFORM_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


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


def _sorted_join(values) -> str:
    """Deterministic rendering of a value set for the bound no-match reply."""
    return ", ".join(sorted(values)) if values else "none recorded"


def handler(args=None, **kwargs) -> str:
    """Model arguments arrive positionally and carry no identity — see identity.py."""
    authorized, refusal = require_owner()
    if not authorized:
        return refusal

    args = args or {}
    cover_role = str(args.get("can_cover_role") or "").strip()
    if cover_role and cover_role not in ROLES:
        return refuse("invalid_arguments")
    language = str(args.get("language") or "").strip().casefold()
    if len(language) > 20:
        return refuse("invalid_arguments")
    name_query = str(args.get("name_contains") or "").strip().casefold()
    if len(name_query) > MAX_QUERY_LEN:
        return refuse("invalid_arguments")

    _ensure_platform_path()

    # Existence is checked BEFORE loading so `missing` can never be reported as
    # `empty`: `Roster` requires `location`, so a loader-first order would raise
    # instead — but the two states still have to be told apart in the structured
    # result, and only this ordering does that.
    if not ROSTER_PATH.exists():
        if not _bind_outbound(TPL_MISSING):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="missing", coverage_status="not_configured")

    from safe_io import load_model
    from schemas import Roster
    try:
        # The validated read loads `schedule` into the model; nothing below ever
        # touches it. See the module docstring for why that is deliberate.
        roster, _ = load_model(ROSTER_PATH, Roster)
    except Exception:
        # Corrupt or schema-invalid roster. NOT an empty roster — the staff may
        # well be recorded and simply be unreadable right now.
        return fail("state_unreadable")

    if not roster.employees:
        if not _bind_outbound(TPL_EMPTY):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="empty", roster_total=0, active_total=0,
                  matched=0, returned=0, truncated=False,
                  roles_present=[], cover_roles_present=[],
                  languages_present=[], staff=[])

    active = [e for e in roster.employees if e.status == "active"]
    if not active:
        # Rows exist but nobody can be asked to work. Distinct from `empty`, and
        # the reply says which it is.
        if not _bind_outbound(
                TPL_NO_ACTIVE.format(roster_total=len(roster.employees))):
            return refuse("outbound_truthfulness_guard_unavailable")
        return ok(source_status="no_active_staff",
                  roster_total=len(roster.employees), active_total=0,
                  matched=0, returned=0, truncated=False,
                  roles_present=[], cover_roles_present=[],
                  languages_present=[], staff=[])

    # Derived from the ACTIVE set only, so a value here is always a value the
    # owner can actually act on. These are what make a zero match self-diagnosing
    # rather than a false absence.
    roles_present = sorted({e.role for e in active})
    cover_roles_present = sorted({r for e in active for r in e.can_cover_roles})
    languages_present = sorted({str(lang) for e in active for lang in e.languages})

    rows = sorted(
        ({
            "id": e.id,
            "name": e.name,
            "nickname": e.nickname,
            "role": e.role,
            "can_cover_roles": list(e.can_cover_roles),
            "languages": list(e.languages),
            # Surfaced, not withheld: the owner wrote these, and a suggestion
            # that ignores a restriction is worse than one that names it.
            "restrictions": e.restrictions,
        } for e in active
            if (not cover_role or cover_role in e.can_cover_roles)
            and (not language
                 or any(language == str(lang).casefold() for lang in e.languages))
            and (not name_query
                 or name_query in e.name.casefold()
                 or name_query in (e.nickname or "").casefold())),
        key=lambda r: r["name"].casefold(),
    )

    common = dict(source_status="populated",
                  roster_total=len(roster.employees),
                  active_total=len(active),
                  roles_present=roles_present,
                  cover_roles_present=cover_roles_present,
                  languages_present=languages_present)

    if not rows:
        # Active staff exist, none match. The state a model most reliably
        # over-generalizes into "nobody can cover that" — with a shift left
        # uncovered at the end of it — so the reply names what IS available.
        if not _bind_outbound(TPL_NO_MATCH.format(
                active_total=len(active),
                cover_roles=_sorted_join(cover_roles_present),
                languages=_sorted_join(languages_present))):
            return refuse("outbound_truthfulness_guard_unavailable")
    # Positive rows bind nothing: Hermes owns presenting real people.
    return ok(matched=len(rows), returned=len(rows[:MAX_ROWS]),
              truncated=len(rows) > MAX_ROWS, staff=rows[:MAX_ROWS], **common)
