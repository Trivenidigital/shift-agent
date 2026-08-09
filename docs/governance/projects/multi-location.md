# Multi-Location Store Locator — Project Directive

    Version: 1.1.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: multi-location
    Supplements: docs/governance/engineering-directive.md

Governs `src/agents/multi_location/**`. Active only when
`cfg.multi_location.locations` is non-empty.

---

## Purpose

Answer "where are you located / which store is closest" for customers, and
cross-location queries for the owner.

## Hermes / ecosystem capability — reuse

- Understanding the locator question, asking for a missing city/ZIP, and
  deciding to look one up — Hermes ordinary-language understanding plus
  progressive Tool Search discovery. No router, regex or classifier.
- **Geocoding and distance are already provided** by the bundled
  `productivity/maps` skill (OSRM + Nominatim), wrapped by
  `scripts/closest-location.py`. Do not write a distance service, a geocoder,
  or a maps-provider client.
- The reachable runtime surface is the plugin tool
  `shift-agent-read/find_nearest_location`, registered under the
  `shift_agent_read` toolset.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Location roster | `cfg.multi_location.locations[]` |
| Distance + drive time | `scripts/closest-location.py` wrapping the maps skill |
| Fallback | the script's `haversine_fallback` source — **the only active path today**, see below |
| Reaching this agent | `find_nearest_location` via Hermes Tool Search |
| Audit | `log-decision-direct` |

## Decision boundary

**May be probabilistic:** recognizing a locator intent and phrasing the reply.

**Must remain deterministic:** the addresses, phone numbers and hours returned,
the ranking by drive time, which `location_id` is named, **and the customer-facing
text that states them**. A wrong address, phone number or set of hours is HIGH
(see escalation), so a successful factual reply is bound to the turn through the
existing exact-turn outbound override in `safe_io` and substituted at the gateway
egress seam — the model's wording cannot alter it. The script reports its
`source` (`osrm` or `haversine_fallback`) — surface it, never suppress it.

**OSRM is not active.** `closest-location.py:osrm_distance()` returns `None`
unconditionally (v0.1 HOTFIX 2026-05-04: `maps_client.py` takes addresses, not
lat/lon, and reverse-geocoding N locations breaks Nominatim's 1 req/s cap).
Ranked production results therefore use `haversine_fallback`, and customer
wording must present drive figures as approximate rather than as live routing.

## Presumed NO-GO

- a custom geocoder or routing engine alongside the maps skill;
- a location store parallel to `cfg.multi_location.locations`;
- a model-generated address, phone number or set of hours;
- reviving `skills/customer_location_query/` or `skills/multi_location_query/`
  routing. Both depend on `skill_view` and inline `terminal` execution, and both
  generic toolsets are disabled on the gateway; `multi_location_query` is
  additionally shelved with an unresolved cross-location privacy leak. They are
  not the active path and must not be re-wired to become one.

## Required vertical E2E proof

A customer message with a real address reaches `find_nearest_location` through
Hermes Tool Search and returns correctly ranked stores with accurate contact
details at the actual adapter egress. Because OSRM is inactive, the
haversine-fallback path is the same run — a passing test is not evidence OSRM
was exercised.

**`DELIVERED_READ` requires genuinely configured customer locations and a real
successful positive lookup.** A real empty roster proves only the
`not_configured` path and yields `ACTIVE_NO_DATA`. Never seed fabricated store
locations to advance that status.

## Escalation boundaries

A wrong address, phone number or hours reaching a customer is HIGH — it sends
real people to the wrong place.

---

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-08-09 | Reachability corrected to the `find_nearest_location` plugin tool via Hermes Tool Search; old SKILL/dispatcher routing recorded as inactive and not to be revived; successful factual replies bound through the exact-turn outbound override; OSRM recorded as inactive; `DELIVERED_READ` vs `ACTIVE_NO_DATA` defined. |
| 1.0.0 | 2026-08-01 | Initial Multi-Location directive. |
