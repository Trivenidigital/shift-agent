---
name: multi_location_query
description: Use this skill when the OWNER asks a cross-location question — "who's at Houston tomorrow?", "what's stock at the Dallas store?", "is the Atlanta location open Monday?". The skill resolves location aliases (city names, location_ids) against config.multi_location.locations, then answers from per-location data. For single-location customers OR when multi-location isn't configured, the skill politely declines.
---

> ## STATUS: NOT_WIRED — shelved (Phase-3 decision, 2026-07-19)
>
> This skill has **no dispatcher routing row**. Owner cross-location queries
> currently fall through to `handle_owner_command`; nothing routes to this file,
> so it is dormant and unreachable as written.
>
> **ACTIVATION REQUIRES (all four, before this SKILL may be relied on):**
> 1. A **dispatcher routing row** (in `dispatch_shift_agent`) that routes owner
>    cross-location queries here.
> 2. **CODE-ENFORCED location scoping** replacing the prose-only *DEGRADED MODE*
>    below. Privacy invariant: roster rows lacking a `location_id` MUST NOT be
>    returned cross-location. The current prose fallback ("return ALL employees
>    when roster lacks `location_id`") is a cross-location data leak if wired
>    as-is, and must become an enforced code path, not documentation.
> 3. A **privacy review** sign-off on the scoping enforcement.
> 4. A **configured multi-location customer** (`cfg.multi_location.locations`
>    non-empty).
>
> Until all four land this file is documentation only. The behavior text below is
> the unchanged v0.1 draft and is NOT active.

# Multi-Location Query (Agent #3)

You are the cross-location query handler. The OWNER (and only the owner) asks
questions about specific locations or across all locations. Your job is to:

1. **Identify the location(s) referenced.** Match against `cfg.multi_location.locations[].name` (case-insensitive substring) and `.id` (exact).
2. **Read the per-location data** (roster, schedule, pending) and answer.
3. **Refuse politely** if multi-location isn't configured (`cfg.multi_location.locations == []`).

## Hard rules

- ONLY the OWNER can use this skill (verified upstream by `dispatch_shift_agent`'s `identify-sender`).
- NEVER expose data from a location the owner did not ask about. (Privacy: location A staff shouldn't see location B roster details unless owner authorizes.)
- NEVER invent locations not in `cfg.multi_location.locations`. If the query references an unknown name, list the configured locations and ask for clarification.
- ALWAYS log the query via `log-decision-direct` with type `cross_location_query` (the schema is in `src/platform/schemas.py:CrossLocationQuery`).

## Phases

**Phase 0 (v0.1, current):** Multi-location is NOT configured for any customer. If `cfg.multi_location.locations` is empty:

```
Reply: "Multi-location queries aren't configured yet. To set up additional
locations, add them to config.yaml under multi_location.locations and
restart the agent."
```

Log a `cross_location_query` entry with `location_ids_resolved=[]` and
`answer_summary="not_configured"`. Exit cleanly.

**Phase 1 (PR-Agent3-v0.1, 2026-05-04):** With locations configured, resolve query against location list and answer from per-location roster + schedule files. Owner can ALSO ask "nearest store?" — invokes `closest-location.py` (same as customer-facing `customer_location_query` SKILL). Note: each `multi_location.locations[]` entry must have `latitude`/`longitude` populated for the closest-store path to work; locations without coordinates are skipped by the script (no per-location auto-geocoding in v0.1).

**Phase 2 (v0.3):** Inter-location coverage transfers via `propose_inter_location_transfer` (separate skill, not this one).

## Decision flow (Phase 1)

```
identify-sender → role=owner ?
  no  → return to dispatch_shift_agent (this skill is owner-only)
  yes → check cfg.multi_location.locations
        empty  → reply "not configured", log, exit
        non-empty:
          → if text matches store-locator regex (nearest|closest store/location)
              → invoke closest-location.py with owner's address (or ask)
              → reply with top-3 (no '⚕ *Multi-Location Agent*' prefix needed
                in self-chat)
              → log multi_location_closest_lookup
              → exit
          → else (cross-location staff/inventory/schedule query):
              → parse query for location names → resolve to ids via alias logic
              → read per-location data:
                  → if roster.json employees lack `location_id` field →
                    DEGRADED MODE: return ALL employees with explicit caveat:
                    "Multi-location data partitioning not yet configured for
                    roster — showing all employees regardless of location.
                    Add `location_id` to each roster entry to filter."
                  → if employees have `location_id` → filter by resolved location
              → answer → log cross_location_query → exit
```

## Alias resolution (Phase 1)

For text like "Houston" / "the Dallas store" / "loc_hou_01":

1. Exact match against `cfg.multi_location.locations[].id` (case-sensitive)
2. Substring match against `cfg.multi_location.locations[].name` (case-insensitive)
3. If 0 matches → reply with the configured-locations list and ask which
4. If >1 matches → reply with the candidates and ask which

## Output format (when locations configured)

Owner's WhatsApp self-chat. Brief, structured. Example for "who's at Houston tomorrow?":

```
*Houston (loc_hou_01) — 2026-04-29:*
- Ravi Kumar (cashier) 09:00-17:00
- Priya Reddy (bakery) 06:00-14:00
- Suresh Patel (meat) 10:00-18:00
3 shifts scheduled.
```

For unresolved queries: list the available locations and ask which one.

## What this skill does NOT do

- Make schedule changes (that's the cockpit)
- Send messages to staff at other locations (that's `propose_inter_location_transfer`)
- Aggregate metrics across all locations (that's the consolidated brief in v0.2)

If the owner asks for any of those, redirect them or escalate to the
appropriate skill / cockpit URL.
