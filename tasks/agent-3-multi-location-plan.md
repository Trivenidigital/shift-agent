# Agent #3 Multi-Location Coordinator — Design v3

**Drift-check tag (split):**
- Schema / audit / dispatcher routing: `Hermes-native` — already deployed
- Business-logic wrapper script: `extends-Hermes` — 1 thin script wrapping the bundled `productivity/maps` skill

## TL;DR — scope reduced from v2 per design-review feedback

v2 attempted 3 use cases (closest-store customer + service-area async + owner cross-location); 2 design reviewers flagged 4 BLOCKERs + 6 HIGH that all traced back to overscoping into unverified Hermes capabilities (location_pin shape, async fire-and-forget wire format, PII in audit log). v3 ships ONLY the parts with deployed-code-verified path:

| Use case | v0.1 status | Why |
|---|---|---|
| 1. Customer "nearest store?" via TEXT (no location pin) | ✅ shipped | Text-regex routing path is well-trodden in this codebase |
| 1b. Customer location PIN (lat/lon) | ❌ deferred to PR-A3-v0.2 | Unverified whether Hermes surfaces `mediaType=location` as text-visible marker to LLM dispatcher; needs empirical srilu test first |
| 2. Service-area validation in catering flow | ❌ deferred to PR-A3-v0.2 | Async fire-and-forget mechanism, address source field on `CateringLeadExtractedFields`, owner-card wire format — all undefined; needs its own design PR |
| 3. Owner cross-location query Phase 1 | ✅ shipped | Existing `multi_location_query` SKILL.md scaffold extends to Phase 1 |
| 4. Timezone-aware Daily Brief integration | ❌ dropped (was already dropped in v2) | Daily Brief is single-location; speculative |

**Net-new effort v3:** ~80 LOC code + ~60 LOC tests + ~80 LOC SKILL.md draft = ~220 LOC. (Down from v2's ~325 LOC.)

## Hermes-first checklist (v3)

| Step | [Hermes] / [net-new] | Notes |
|---|---|---|
| Owner identity verification | [Hermes] | `identify-sender` (existing) — returns role ∈ {owner, employee, unknown, error} |
| Geocode address → lat/lon | [Hermes] | `productivity/maps search` (Nominatim) |
| Driving distance + time | [Hermes] | `productivity/maps distance` (OSRM) |
| Audit log via NDJSON | [Hermes] | `safe_io.ndjson_append` |
| WhatsApp reply | [Hermes] | Existing adapter + `⚕ *Multi-Location Agent*\n────────────\n` template prefix |
| `LocationEntry` + `MultiLocationConfig` schema | [Hermes] (already deployed) | `schemas.py:775-818` — 5 field additions, no new classes |
| `CrossLocationQuery` audit variant | [Hermes] (already deployed) | `schemas.py:2301` |
| `multi_location_query` SKILL Phase 0 scaffold | [Hermes] (already deployed) | Extends Phase 0 → Phase 1 logic |
| **`MultiLocationClosestLookup` audit variant** | [net-new] (~20 LOC) | New `_BaseEntry` subclass, type=snake_case, class=PascalCase |
| **Dispatcher amendment** for customer text queries | [net-new] (~15 LOC SKILL.md edit) | One new routing row using existing `text` shape + tightened regex (no new message-shape literal needed) |
| **`customer_location_query` SKILL** (new) | [net-new] (~80 LOC SKILL.md draft below) | Customer-facing; not owner-gated; reads only `multi_location.locations[]` |
| **`closest-location.py`** wrapper script | [net-new] (~50 LOC) | `--lat <x> --lon <y>` OR `--address "..."` → calls `maps_client.py distance` per location → returns top-3 by drive minutes |
| **Schema field additions to LocationEntry** | [net-new] (~25 LOC schema delta) | `latitude`, `longitude`, `phone` (E164Phone), `hours` (str optional), `service_radius_minutes` (default 30.0). Last field is unused by v0.1 — kept for v0.2 service-area PR which will land in same release window |

## Schema delta (NOT new classes — amend deployed `LocationEntry`)

```python
# DEPLOYED today at src/platform/schemas.py:775 (do not duplicate):
class LocationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)              # NO pattern — keep as deployed
    name: str = Field(min_length=1)
    timezone: str                              # IANA (validator already deployed)
    owner_jid: str = ""
    address_short: str = ""

    # ───── PR-Agent3-v0.1 additions (5 new optional fields) ─────
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    phone: Optional[E164Phone] = None
    hours: Optional[str] = Field(default=None, max_length=200)  # human-readable
    service_radius_minutes: float = Field(default=30.0, ge=0.0, le=240.0)
    # service_radius_minutes is UNUSED in v0.1 — included now so the v0.2
    # service-area PR doesn't require a second migration. Configs that omit
    # it get the 30.0 default; existing configs continue to validate.
```

All new fields are `Optional` (or have defaults) so existing customer configs that don't populate them continue to validate. The deployed `_unique_ids` validator on `MultiLocationConfig.locations` is preserved.

**No `DispatcherRouted` schema change needed** — v3 doesn't add a new message_shape literal. The dispatcher amendment uses the existing `text` shape with a content regex.

## Dispatcher amendment (single row addition)

**File:** `src/agents/shift/skills/dispatch_shift_agent/SKILL.md`

Insert ONE new row in the routing matrix. Per Reviewer 2, the catering-keyword row currently runs at priority N (around row 9 — `Text contains catering keyword AND cfg.catering.enabled → catering_dispatcher`). The new store-locator row goes **immediately AFTER** the catering keyword row, so a "party near me?" message that contains both signals correctly routes to catering (the more specific intent for an SMB):

```
| Text contains store-locator phrase AND sender_role == unknown | (any) | **customer_location_query** |
```

**Store-locator phrase regex** (tightened per Reviewer 2 H3 — single-word "store" was too broad):

```
(?i)\b(nearest|closest|near\s*(?:me|you|by))\b\s*\S{0,40}\b(store|location|branch|shop)\b
| (?i)\b(where\s+are\s+you\s+located|store\s+locator|find\s+(?:a\s+|the\s+)?store)\b
```

This requires BOTH a proximity word AND a location-intent word within ~40 chars, plus catches the explicit phrasings. Single-word matches like "store" or "near me" alone do NOT trigger.

**Owner-self-chat handling** (per Reviewer 2 HIGH 1): owner asking "nearest store?" routes via the EXISTING `multi_location_query` SKILL after we extend its Phase 0 scaffold to Phase 1. The existing matrix row for owner cross-location queries (multi_location_query) catches it; no new owner row needed for the closest-store intent specifically.

**Dispatcher audit row**: existing `dispatcher_routed` audit. `sender_role` Literal is owner/employee/unknown/error per `schemas.py:1516`. No new value needed; `unknown` covers the customer case.

## `customer_location_query` SKILL.md draft

```markdown
---
name: customer_location_query
description: Use this skill when a CUSTOMER (sender_role=unknown, not owner/employee) asks about the operator's store locations — "nearest store?", "where are you located?", "store locator". Reads only multi_location.locations[]; never reads roster, schedule, or pending data. Politely declines if multi-location isn't configured.
---

# Customer Location Query (Agent #3)

You handle customer-facing store-locator inquiries. Your job is narrow:

1. **Confirm intent** — the dispatcher routed here based on a regex match. The text might still be a complaint or other intent that happens to contain the trigger words. Read the message carefully; if it's NOT actually a "where is the nearest store?" intent (e.g. "I had a bad experience at your store"), reply briefly acknowledging the message and let the operator follow up — DO NOT send a store list.

2. **Identify the customer's location** (when intent is confirmed):
   - If the message contains an address or city, extract it → invoke `closest-location.py --address "<text>"`
   - If neither is present, ask the customer for their ZIP code or city (do not invoke the script with empty input)

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

4. **Audit** via `log-decision-direct` with type `multi_location_closest_lookup`.

## Hard rules

- **Customer-facing only.** Sender role MUST be `unknown` (verified upstream by dispatcher's `identify-sender` check). Owner/employee should NEVER reach this SKILL — the dispatcher gates it. If for any reason this SKILL fires for an owner sender, log a warning and exit (do not reply).
- **NO roster/schedule/pending access.** This SKILL reads only `cfg.multi_location.locations[]`. Never expose staff schedules, lead data, or any state files.
- **Empty locations → polite decline.** If `cfg.multi_location.locations == []`, reply:
  > "Sorry, store-locator info isn't available right now. Please call us at <cfg.owner.phone>."
  Audit with `n_locations_returned=0`, `detail="not_configured"`.
- **NEVER invent locations.** If the closest-location script returns 0 hits (rare — script always returns at least one if locations are configured), reply with the polite decline above.
- **Maximum 3 locations in reply.** Keep the message short.

## Decision flow

```
dispatcher_routed (sender_role=unknown, intent=closest_store) → this skill
  → confirm intent (read text; non-store-locator intent? → brief ack, exit)
  → extract address/city/zip from message
       missing → reply "what's your ZIP or city?"
       present → invoke closest-location.py --address "..."
                  → format reply with top-3
                  → log multi_location_closest_lookup
                  → exit
```

## Phase 0 (when locations are configured but operator hasn't migrated to v0.2)

If `len(cfg.multi_location.locations) >= 1` but the entries lack `latitude`/`longitude`:
- The script will fall back to `cfg.multi_location.locations[].address_short` for geocoding (one Nominatim call per location, slower)
- Performance acceptable for ≤9 locations
- v0.2 PR (or operator config update) populates `latitude`/`longitude` to skip the fallback
```

## `closest-location.py` interface (concrete)

**CLI signature:**
```
closest-location.py
  --lat <float>           # mutually exclusive with --address
  --lon <float>           # required if --lat given
  --address <str>         # mutually exclusive with --lat/--lon
  [--top-n N]             # default 3, max 10
  [--config-path <path>]  # default /opt/shift-agent/config.yaml
  [--timeout-sec N]       # default 10
```

**JSON output shape (stdout)**:
```json
{
  "source": "osrm" | "haversine_fallback",
  "results": [
    {
      "location_id": "loc_hou_01",
      "name": "Houston Galleria",
      "address_short": "Houston, TX",
      "phone": "+17135551234",
      "hours": "Mon-Sun 09:00-22:00",
      "drive_minutes": 12.5,
      "distance_km": 8.7
    }
  ],
  "customer_input": {"address": "..."} | {"lat": ..., "lon": ...},
  "n_locations_total": 9,
  "n_returned": 3,
  "errors": []
}
```

On Nominatim/OSRM failure: `source` becomes `"haversine_fallback"`; `drive_minutes` is computed as `(haversine_km * 1.3) / 0.5` (rough urban driving speed of ~30 km/h ≈ 0.5 km/min). The 1.3 multiplier is the **commonly cited mean urban detour factor** from OSM routing literature (e.g. Boeing 2017, "OSMnx"; Newell 1980 detour-distance studies). Cite this in the script docstring.

**Exit codes:**
- 0 — success (results returned, possibly degraded)
- 1 — invalid input (bad lat/lon, no address resolvable)
- 2 — config error (multi_location.locations empty)
- 3 — all upstream services unreachable (no fallback possible)

## New audit variant

```python
class MultiLocationClosestLookup(_BaseEntry):
    """Customer asked for nearest store; script returned top-N by drive time.

    Note: address is NOT stored in audit per Reviewer 2 HIGH 2 (PII concern).
    Only operationally relevant fields are persisted: customer geo (lat/lon
    only when supplied as a pin — not stored from address text), nearest
    location id, drive minutes, count of results.
    """
    type: Literal["multi_location_closest_lookup"]
    chat_id: str = Field(min_length=1, max_length=200)
    customer_lat: Optional[float] = None  # only if customer sent a location pin
    customer_lon: Optional[float] = None
    nearest_location_id: Optional[str] = Field(default=None, max_length=40)
    nearest_drive_minutes: Optional[float] = None
    n_locations_returned: int = Field(ge=0, le=50)
    source: Literal["osrm", "haversine_fallback", "not_configured"] = "osrm"
    detail: str = Field(default="", max_length=2000)
```

(The v2 design's `MultiLocationServiceAreaCheck` variant is REMOVED — that audit was for Use case 2 which is now deferred. When Use case 2 ships in PR-A3-v0.2, the variant comes back, with the address-PII concern resolved per Reviewer 2 HIGH 2.)

## `multi_location_query` SKILL.md Phase 1 extension (existing SKILL — owner-only)

Existing scaffold returns "not configured" when `multi_location.locations == []`. Phase 1 extends to:

1. Existing owner-only gate (preserved)
2. Resolve location alias against `cfg.multi_location.locations` (case-insensitive substring match against `.name`; exact match against `.id`)
3. Per-location data partitioning **degraded mode** (per Reviewer 2 M5):
   - Read `roster.json`. If employees lack `location_id` field → return ALL employees with explicit caveat:
     > *"Multi-location data partitioning not yet configured for roster — showing all employees regardless of location. Add `location_id` to each roster entry to filter."*
   - If employees have `location_id` → filter by resolved location
4. **Owner closest-store intent** (per Reviewer 2 HIGH 1): if owner text matches the store-locator regex, owner ALSO uses the underlying `closest-location.py` (same script). Reply formatted with owner-friendly template (no "⚕ *Multi-Location Agent*" prefix needed since it's self-chat).
5. Audit `cross_location_query` (existing variant)

## Risks (v3, scope-reduced)

1. **Nominatim 1 req/s rate limit** — at SMB volume, not a practical concern. Document for future scale review.
2. **OSRM public endpoint reliability** — Haversine fallback (1.3x multiplier) tested. Haversine is documented and the multiplier is sourced.
3. **Roster lacks location_id** — degraded-mode message returned (better signal than silent wrong answer).
4. **Owner accidentally triggers customer_location_query**: dispatcher gates by `sender_role`; owner is `owner`, not `unknown`. SKILL itself adds defensive role check for fail-loud (per draft hard rules).

## Test plan

- [ ] Unit: schema delta on `LocationEntry` — adding 5 fields validates; all-optional means existing configs without them still validate; `latitude=-91` rejects; `service_radius_minutes=300` rejects (>240)
- [ ] Unit: `closest-location.py` with mocked `maps_client.py distance` — given 9 fixture locations + customer point, returns correct top-3 sorted by drive minutes
- [ ] Unit: `closest-location.py` haversine fallback — mock `urllib.error.URLError` on OSRM, assert `source="haversine_fallback"` in output, drive_minutes computed via 1.3 × Haversine ÷ 0.5 km/min formula, audit row notes degraded source
- [ ] Unit: store-locator regex — positive cases ("nearest store", "where are you located", "store locator", "find the closest branch"); negative cases ("worst experience at your store", "near me but not really nearby", "address for the meeting")
- [ ] Unit: invalid IANA timezone via deployed `_valid_tz` validator — `LocationEntry(timezone="Mars/FakeZone", ...)` raises ValidationError
- [ ] Unit: location alias resolution — "Houston" matches by substring; "loc_hou_01" by exact id; "Mars" returns None + suggestion list
- [ ] Integration: `customer_location_query` SKILL with synthetic dispatcher event — `sender_role="unknown"` + matching text → SKILL invokes script → reply formatted correctly → audit row written
- [ ] Integration: `customer_location_query` defensive owner-role check — if dispatcher mis-routes an owner here, SKILL logs warning + exits without replying
- [ ] Integration: `multi_location_query` Phase 1 owner query — degraded-mode caveat string when roster lacks location_id
- [ ] Integration: `multi_location_query` owner closest-store — owner texts "nearest store?" in self-chat, gets owner-formatted reply
- [ ] Smoke: srilu post-deploy — new audit variant registers; `closest-location.py` importable via `$PY`; SKILL.md files present in `~/.hermes/skills/`

## Build sequence (with explicit dependencies)

1. **Commit 1**: Schema delta on `LocationEntry` (5 new fields) + new `MultiLocationClosestLookup` audit variant + add to LogEntry union + `__all__` export. Tests for schema.
2. **Commit 2**: `closest-location.py` script + tests. (No dependencies on later commits.)
3. **Commit 3**: New `customer_location_query/SKILL.md`. **Depends on Commit 2** (SKILL invokes the script).
4. **Commit 4**: Dispatcher amendment in `dispatch_shift_agent/SKILL.md` (new routing row + tightened regex). **Depends on Commit 3** (SKILL must exist before dispatcher routes to it).
5. **Commit 5**: `multi_location_query/SKILL.md` Phase 1 extension (degraded-mode owner query + alias resolution + owner closest-store path). **Depends on Commit 2**.
6. **Commit 6**: Deploy script integration (install `closest-location.py` to `/usr/local/bin/`); plan doc finalization + memory.

## Rollback runbook

```bash
# Option A — Disable Agent #3 entirely (revert to Phase 0 "not configured")
# Operator manually edits /opt/shift-agent/config.yaml: set
#   multi_location:
#     locations: []
# Then restart hermes-gateway. Both customer_location_query and
# multi_location_query SKILLs return polite "not configured" replies.
sudo systemctl restart hermes-gateway

# Option B — Full deploy rollback via existing tarball mechanism
# (NOT a git-tag dance — the deploy script already keeps the prior 5
# tarballs in /opt/shift-agent/deploys/ for exactly this case.)
ssh root@srilu-vps 'sudo /usr/local/bin/shift-agent-deploy list'
# Pick the deploy tag that was the last known-good (pre-Agent-3)
ssh root@srilu-vps 'sudo /usr/local/bin/shift-agent-deploy rollback deploy-<prev-tag>'
# This re-extracts the prior tarball, re-runs install_artifacts(), restarts
# services. Audit rows already written for multi_location_closest_lookup
# get _UnknownLogEntry passthrough on the older code; no schema migration.

# Option C — Disable customer_location_query routing only (keep owner path)
# Edit dispatch_shift_agent/SKILL.md: remove or comment out the new routing
# row, leaving multi_location_query owner-only. Push tarball + redeploy.
```

## Out of scope for v0.1 (deferred to PR-A3-v0.2 or later)

- **Use case 1b — WhatsApp location PIN handling.** Need empirical srilu test to confirm whether Hermes surfaces `mediaType=location` to the LLM dispatcher as a text-visible marker. If yes, add `location_pin` shape to `DispatcherRouted.message_shape` Literal + new dispatcher row. If no, design a different ingest path (e.g. WhatsApp adapter pre-hook that converts pins to `[Location: lat=X lon=Y]` text injection).
- **Use case 2 — Service-area validation in catering flow.** Requires: (a) `delivery_address` field on `CateringLeadExtractedFields`, (b) async fire-and-forget mechanism (Popen pattern decision + cleanup), (c) owner-card wire format for asynchronous service-area result, (d) `MultiLocationServiceAreaCheck` audit variant with NO address PII (drop `address` field per Reviewer 2 HIGH 2). All of these need separate design — defer to PR-A3-v0.2.
- **Use case 4 — Timezone-aware Daily Brief integration.** When Daily Brief itself goes multi-location.
- **Inter-location employee transfers** — Phase 2 per portfolio doc.
- **Per-location data partitioning** (`state/multi_location/<id>/...`) — Phase 2.
- **Geocode cache** — when scale demands it; lat/lon-only (no PII).
- **Custom service-area polygons.**
