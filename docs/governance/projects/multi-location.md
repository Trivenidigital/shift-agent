# Multi-Location Store Locator — Project Directive

    Version: 1.0.0
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

- Understanding the locator question and phrasing the answer — deployed in
  `skills/customer_location_query/` and `skills/multi_location_query/`.
- **Routing and geocoding are already provided** by the bundled
  `productivity/maps` skill (OSRM + Nominatim), wrapped by
  `scripts/closest-location.py`. Do not write a distance service, a geocoder,
  or a maps-provider client.

## Deterministic kernels — reuse

| Concern | Deployed owner |
|---|---|
| Location roster | `cfg.multi_location.locations[]` |
| Distance + drive time | `scripts/closest-location.py` wrapping the maps skill |
| Fallback | the script's `haversine_fallback` source when OSRM is unavailable |
| Routing to this agent | shared dispatcher store-locator regex |
| Audit | `log-decision-direct` |

## Decision boundary

**May be probabilistic:** recognizing a locator intent and phrasing the reply.

**Must remain deterministic:** the addresses, phone numbers and hours returned,
the ranking by drive time, and which `location_id` is named. The script reports
its `source` (`osrm` or `haversine_fallback`) — surface it, never suppress it.

## Presumed NO-GO

- a custom geocoder or routing engine alongside the maps skill;
- a location store parallel to `cfg.multi_location.locations`;
- a model-generated address, phone number or set of hours.

## Required vertical E2E proof

A customer message with a real address returns correctly ranked stores with
accurate contact details, and the OSRM-unavailable path still answers via the
haversine fallback.

## Escalation boundaries

A wrong address, phone number or hours reaching a customer is HIGH — it sends
real people to the wrong place.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Multi-Location directive. |
