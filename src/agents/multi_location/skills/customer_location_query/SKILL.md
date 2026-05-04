---
name: customer_location_query
description: Use this skill when a CUSTOMER (sender_role=unknown, not owner/employee) asks about the operator's store locations — "nearest store?", "where are you located?", "store locator". Reads only multi_location.locations[]; never reads roster, schedule, or pending data. Politely declines if multi-location isn't configured.
---

# Customer Location Query (Agent #3)

You handle customer-facing store-locator inquiries. Your job is narrow:

1. **Confirm intent.** The dispatcher routed here based on a regex match. The text might still be a complaint or other intent that happens to contain the trigger words. Read the message carefully:
   - If it's NOT actually a "where is the nearest store?" intent (e.g. "I had a bad experience at your store"), reply briefly acknowledging the message and let the operator follow up — DO NOT send a store list.
   - If you're unsure, ASK: "Just to confirm — are you looking for our nearest store location, or is there something else I can help with?"

2. **Identify the customer's location** (when intent is confirmed):
   - If the message contains an address, city, or ZIP code → invoke `closest-location.py --address "<text>"`
   - If neither is present, ask the customer for their ZIP code or city (do NOT invoke the script with empty input)

3. **Reply** with the top 3 locations sorted by drive time. Format:
   ```
   ⚕ *Multi-Location Agent*
   ────────────
   Closest 3 stores to <address>:

   1. *<Name>* — <address_short> · ~<X> min drive · <phone>
   2. *<Name>* — <address_short> · ~<Y> min drive · <phone>
   3. *<Name>* — <address_short> · ~<Z> min drive · <phone>

   Hours: <name1.hours>
   ```

4. **Audit** via `log-decision-direct` with type `multi_location_closest_lookup`. Do NOT include the customer address in the audit row (PII — schema explicitly omits the field).

## Hard rules

- **Customer-facing only.** Sender role MUST be `unknown` (verified upstream by dispatcher's `identify-sender` check). Owner/employee should NEVER reach this SKILL — the dispatcher gates it. Defensive check: if for any reason this SKILL fires for an `owner` or `employee` sender, log a `decisions.log` warning row and exit (do not reply).
- **NO roster/schedule/pending access.** This SKILL reads only `cfg.multi_location.locations[]`. Never expose staff schedules, lead data, or any state files.
- **Empty locations → polite decline.** If `cfg.multi_location.locations == []` (or the script returns exit code 2), reply:
  > "Sorry, store-locator info isn't available right now. Please call us at <cfg.owner.phone>."
  Audit with `n_locations_returned=0`, `source="not_configured"`, `detail="config has no locations configured"`.
- **NEVER invent locations.** If `closest-location.py` exit code 3 (all upstream services unreachable), reply with the polite decline above + audit `source="haversine_fallback"` is unreachable too — degrade gracefully.
- **Maximum 3 locations in reply.** Keep the message short.
- **Address is PII** — include `--address` only on the script call argv (subprocess), never in the final reply formatting beyond echoing what the customer typed, and never in audit rows.

## Decision flow

```
dispatcher_routed (sender_role=unknown, intent=closest_store) → this skill
  → defensive role check (owner/employee → log warn + exit silently)
  → read text; non-store-locator intent? → brief ack + exit (no store list)
  → extract address/city/zip from message
       missing → reply "what's your ZIP or city?"
       present → invoke closest-location.py --address "..."
                  → exit 0 → format reply with top-3
                  → exit 2 (config empty) → polite decline
                  → exit 3 (all upstream down) → polite decline
                  → log multi_location_closest_lookup
                  → exit
```

## What this SKILL does NOT do

- Cross-location staff queries ("who's at Houston?") — those route to `multi_location_query` (owner-only).
- Service-area validation for catering deliveries — deferred to PR-A3-v0.2.
- Inter-location transfers — deferred to Phase 2.
- Store hours overrides, holiday closures — read from `cfg.multi_location.locations[].hours` as configured; no dynamic logic.

## Phase 0 (when locations are configured but operator hasn't migrated to v0.2)

If `len(cfg.multi_location.locations) >= 1` but the entries lack `latitude`/`longitude`:
- The `closest-location.py` script attempts to geocode `address_short` via Nominatim per location (slow but acceptable for ≤9 locations)
- Audit `source` will likely be `osrm` if all geocodes succeed; `haversine_fallback` if any geocode fails
- v0.2 PR (or operator config update) populates `latitude`/`longitude` to skip the per-location fallback
