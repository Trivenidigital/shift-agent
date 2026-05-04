# Agent #3 Multi-Location Coordinator — Plan v2

**Drift-check tag (split per Reviewer 1 finding):**
- Schema / audit / dispatcher routing: `Hermes-native` — already deployed (`LocationEntry`, `MultiLocationConfig`, `CrossLocationQuery`, `InterLocationTransferProposed` are all in `src/platform/schemas.py` already). v2 amends 5 fields onto the existing `LocationEntry`; does NOT add new classes.
- Business-logic wrapper scripts: `extends-Hermes` — 3 thin scripts that wrap the bundled `productivity/maps` skill.

(Critical correction from plan v1: my v1 proposed a duplicate `Location` class that would have collided with deployed `LocationEntry`. Verified by reading `src/platform/schemas.py:774-819` + `1129` + `2301-2410` + `2512` + `2553`. Net-new schema work is 5 field additions to the existing class, not a parallel definition.)

## TL;DR

Phase 1 of Agent #3 — extends the already-deployed `LocationEntry`/`MultiLocationConfig` schema with geo + delivery-radius fields, adds 3 thin wrapper scripts on the bundled `productivity/maps` skill, and adds a NEW `customer_location_query` SKILL (separately routed from the existing owner-only `multi_location_query`) so customers can ask "nearest store?" without a routing leak. Net-new effort: ~140 LOC of scripts + ~25 LOC schema delta + ~30 LOC dispatcher amendment + ~80 LOC tests = ~275 LOC.

## Hermes-first checklist (mandatory)

| Step | [Hermes] / [net-new] | Notes |
|---|---|---|
| Owner identity verification | [Hermes] | `identify-sender` (existing) |
| Geocode address → lat/lon | [Hermes] | `productivity/maps search` (Nominatim) |
| Reverse geocode | [Hermes] | `productivity/maps reverse` |
| Driving distance + time | [Hermes] | `productivity/maps distance` (OSRM) |
| Audit log via NDJSON | [Hermes] | `safe_io.ndjson_append` |
| WhatsApp reply | [Hermes] | Existing adapter + `⚕ *Multi-Location Agent*\n────────────\n` template prefix |
| `LocationEntry` + `MultiLocationConfig` schema | [Hermes] (already deployed) | `schemas.py:775-818` — 5 field additions, no new classes |
| `CrossLocationQuery` audit | [Hermes] (already deployed) | `schemas.py:2301` |
| Owner-only `multi_location_query` SKILL Phase 1 | [Hermes] (existing scaffold) | Extends Phase 0 logic in current SKILL.md |
| `MultiLocationClosestLookup` audit variant | [net-new] (~20 LOC) | New `_BaseEntry` subclass, type=snake_case, class=PascalCase per existing convention |
| `MultiLocationServiceAreaCheck` audit variant | [net-new] (~20 LOC) | Same |
| **Dispatcher amendment** for customer queries | [net-new] (~30 LOC SKILL.md edit) | Add `location_pin` message shape to Step 3 + new routing row `(text containing "store"/"location" near me OR location_pin) + customer/unknown → customer_location_query` |
| **`customer_location_query` SKILL** (new) | [net-new] (~80 LOC SKILL.md) | Customer-facing; not owner-gated; reads only `multi_location.locations[]` config (no roster/state) |
| **`closest-location.py`** wrapper | [net-new] (~50 LOC) | `--lat <x> --lon <y>` OR `--address "..."` → calls `maps_client.py distance` per location → returns top-3 sorted by drive minutes |
| **`validate-service-area.py`** wrapper | [net-new] (~40 LOC) | `--address "..."` → returns JSON `{"in_area": bool, "nearest_location_id": "...", "drive_minutes": N}` based on `location.service_radius_minutes` |
| **Schema field additions to LocationEntry** | [net-new] (~25 LOC schema + validators) | `latitude`, `longitude`, `phone` (E164Phone), `hours` (str optional), `service_radius_minutes` (float, default 30.0) |

**Net-new effort:** ~245 LOC code + ~80 LOC tests = ~325 LOC. Original v1 estimated ~280 LOC; v2 is slightly higher because of the new `customer_location_query` SKILL + dispatcher amendment that v1 missed.

## Why this PR (and why now)

Per the 2026-05-04 skills-roadmap audit (PR #54): `productivity/maps` is bundled and enabled in Hermes 0.12.0 on srilu. Zero-auth (no API key, no OAuth). The audit explicitly listed Agent #3 as the highest-ROI "install-now" target.

The `LocationEntry`/`MultiLocationConfig`/`CrossLocationQuery` schema scaffold is already deployed (left over from earlier portfolio scaffolding in v0.1). Phase 0 SKILL.md returns "not configured" because no customer has populated `multi_location.locations[]` yet. Phase 1 unlocks both customer-facing closest-store lookup AND owner-facing cross-location queries the moment the operator configures the locations.

## Use cases (prioritized)

### Use case 1 — Closest-location lookup (CUSTOMER-facing)

**Trigger:** Customer asks "what's your nearest store to me?" OR shares a WhatsApp location pin.

**Flow:**
1. Inbound from non-owner non-employee chat
2. Dispatcher matches NEW row: `(text matches store-locator regex OR shape == location_pin) + sender_role=customer/unknown → customer_location_query`
3. `customer_location_query` SKILL: extract address from text OR (lat,lon) from location pin → `closest-location.py` → format reply with top-3 stores by drive time + addresses + phones
4. Audit row `multi_location_closest_lookup` written
5. SKILL formats reply with `⚕ *Multi-Location Agent*` prefix

### Use case 2 — Service-area validation (catering delivery)

**Trigger:** `apply-catering-owner-decision` (or future SKILL) needs to validate "is this delivery address within ANY location's service radius?"

**Flow:**
1. `validate-service-area.py --address "..."` invoked
2. Script: geocode → for each location compute drive_minutes via OSRM → return JSON with nearest location + drive minutes + in_area boolean (drive_minutes ≤ location.service_radius_minutes)
3. Caller uses result to either accept lead or flag for owner review
4. Audit row `multi_location_service_area_check` written

**Latency budget:** geocode (~1s Nominatim) + N×500ms (OSRM per location) = ~5-6s for 9 locations. **Mitigation:** invoke OUT of the synchronous catering acknowledgment path — fire-and-forget after lead creation, audit the result, surface to owner via owner-card if outside service area. Catering customer ack returns immediately.

### Use case 3 — Cross-location owner query (OWNER-facing, Phase 1)

**Trigger:** Owner self-chat: "who's at Houston tomorrow?", "what's stock at Dallas?", etc.

**Flow:** (extends existing `multi_location_query` Phase 0 → Phase 1)
1. Owner-only gate (existing)
2. Resolve location alias against `cfg.multi_location.locations` (case-insensitive substring + exact id match)
3. **Degraded-mode behavior** (per Reviewer 2 finding M5): if `roster.json` employees lack `location_id` field, return ALL employees with explicit caveat: *"Multi-location data partitioning not yet configured for roster — showing all employees regardless of location. Add `location_id` to each roster entry to filter."*
4. Reply formatted, audit row written

## What v2 explicitly DROPS from v1

Reviewer 2 flagged these as speculative or risky:

- **Use case 4 (timezone-aware Daily Brief integration)** — DROPPED. Daily Brief is single-location today and TimeAPI.io adds an external dependency to its critical path. The static `LocationEntry.timezone` field already exists (deployed) and doesn't need a runtime call. If Daily Brief later goes multi-location, that PR adds the integration.
- **Geocode cache** — DROPPED. Customer addresses are PII; no encryption pattern exists in this codebase. At 9 locations + low catering volume, the Nominatim 1 req/s rate limit is not a practical concern. If volume becomes an issue later, a follow-up PR can add lat/lon-only caching (no address strings) with explicit `drifts-from-Hermes` tag.

## Schema delta (NOT new classes — amend deployed)

**File:** `src/platform/schemas.py`, class `LocationEntry` (line 775)

```python
# DEPLOYED today (do not duplicate):
class LocationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)              # NO pattern — keep as deployed
    name: str = Field(min_length=1)
    timezone: str                              # IANA (validator already deployed)
    owner_jid: str = ""
    address_short: str = ""

    # ───── PR-CF-AGENT3 v2 additions (5 new optional fields) ─────
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    phone: Optional[E164Phone] = None
    hours: Optional[str] = Field(default=None, max_length=200)  # human-readable
    service_radius_minutes: float = Field(default=30.0, ge=0.0, le=240.0)
    # ────────────────────────────────────────────────────────────────

    @field_validator("timezone")
    ...  # existing — unchanged
```

All new fields are `Optional` (or have defaults) so existing customer configs that don't populate them continue to validate. The deployed `_unique_ids` validator on `MultiLocationConfig.locations` is preserved.

## Dispatcher amendment

**File:** `src/agents/shift/skills/dispatch_shift_agent/SKILL.md`

Two changes:

1. **Step 3 message-shape classification** — add a new shape:
   ```
   - `location_pin` — Hermes message metadata indicates `mediaType=location` (lat/lon attached, no text body required).
   ```

2. **Routing matrix** — add a new row BEFORE the "DECLINE politely" catch-all:
   ```
   | (text contains store-locator phrase OR shape == location_pin) AND sender_role in {customer, unknown} | non-owner | **customer_location_query** |
   ```
   Where "store-locator phrase" matches `\b(nearest|closest|near me|near you|location|store|address|where are you)\b` (lenient — false positives just trigger the SKILL which then defers to the LLM if it can't find a match).

## New audit variants

```python
class MultiLocationClosestLookup(_BaseEntry):
    """Customer asked for nearest store; script returned top-N by drive time."""
    type: Literal["multi_location_closest_lookup"]
    chat_id: str = Field(min_length=1, max_length=200)
    customer_lat: Optional[float] = None
    customer_lon: Optional[float] = None
    customer_address: Optional[str] = Field(default=None, max_length=300)
    nearest_location_id: Optional[str] = Field(default=None, max_length=40)
    nearest_drive_minutes: Optional[float] = None
    n_locations_returned: int = Field(ge=0, le=50)
    detail: str = Field(default="", max_length=2000)


class MultiLocationServiceAreaCheck(_BaseEntry):
    """Catering / delivery address checked against all locations' service radii."""
    type: Literal["multi_location_service_area_check"]
    address: str = Field(min_length=1, max_length=300)
    in_area: bool
    nearest_location_id: Optional[str] = Field(default=None, max_length=40)
    nearest_drive_minutes: Optional[float] = None
    detail: str = Field(default="", max_length=2000)
```

(Both follow existing convention: `type` snake_case Literal; class PascalCase; subclass `_BaseEntry`.)

## Risks

1. **Nominatim 1 req/s rate limit** — bursts of N customers asking simultaneously would queue. **Mitigation:** at the expected SMB scale (single-customer, low daily volume), this is not practical concern. Document for future scale review.
2. **OSRM public endpoint reliability** — `router.project-osrm.org` is the public free endpoint. **Mitigation:** timeout (10s) + Haversine (great-circle) distance × 1.3 multiplier as fallback. Test path: mock `urllib.error.URLError`, assert fallback returns sensible result.
3. **No per-location data partitioning** — Phase 1 cross-location query reads from existing `roster.json`. Owner asking "who's at Houston?" with no `location_id` field on employees gets the degraded-mode message (above). Acceptable — better signal than silent wrong answer.
4. **Service-area validation latency** — if invoked synchronously in catering ack path, adds 5-6s. **Mitigation:** invoke async (post-lead-creation enrichment), surface result via owner-card.
5. **Customer location pin without dispatcher support** — if shape detection fails for some adapter version, falls back to `media_other → DECLINE`. Same failure mode as today; not a regression.

## Test plan

- [ ] Unit: schema — `LocationEntry(id="loc_x", name="x", timezone="America/Chicago", latitude=29.7, longitude=-95.4, service_radius_minutes=30.0)` validates; `latitude=-91` rejects; missing optional fields validates
- [ ] Unit: `closest-location.py` with mocked `maps_client.py distance` — given 9 fixture locations + one customer point, returns correct top-3 sorted by drive time
- [ ] Unit: `validate-service-area.py` — drive minutes ≤ radius → in_area=True; > radius → false; nearest is correctly identified
- [ ] Unit: location alias resolution — "Houston" matches "Houston" by substring; "loc_hou_01" by exact id; "Mars" returns None + suggestion list
- [ ] **Network-failure simulation** (per Reviewer 2 M7): mock `urllib.error.URLError` on Nominatim → assert Haversine fallback fires + audit row notes degraded source
- [ ] **Invalid IANA timezone** (per Reviewer 2 M7): `LocationEntry(timezone="Mars/FakeZone", ...)` raises `ValidationError` (relies on the existing `_valid_tz` validator)
- [ ] Integration: `customer_location_query` SKILL — dispatcher routing test with synthetic `location_pin` event
- [ ] Integration: degraded-mode owner query — roster without `location_id` field → returns all + caveat string
- [ ] Smoke: srilu post-deploy — new scripts importable; audit variants register

## Build sequence

1. **Commit 1**: Schema field additions (`latitude`, `longitude`, `phone`, `hours`, `service_radius_minutes`) on existing `LocationEntry`. New audit variants. Tests for schema.
2. **Commit 2**: `closest-location.py` script + tests.
3. **Commit 3**: `validate-service-area.py` script + tests.
4. **Commit 4**: New `customer_location_query` SKILL.md + dispatcher amendment in `dispatch_shift_agent/SKILL.md` (new `location_pin` shape + new routing row).
5. **Commit 5**: `multi_location_query/SKILL.md` Phase 1 extension (degraded-mode owner query) + alias resolution helper.
6. **Commit 6**: Deploy script integration (install scripts to `/usr/local/bin/`); plan doc finalization + memory.

Total estimate: ~325 LOC. ~3-4 hours including 5-agent review cycles (2 design + 3 PR).

## Rollback runbook

```bash
# Option A — Disable Agent #3 entirely (revert to Phase 0 "not configured")
# Operator manually edits /opt/shift-agent/config.yaml: set
#   multi_location:
#     locations: []
# Then restart hermes-gateway.
sudo systemctl restart hermes-gateway

# Option B — Full code rollback via backup tag
git checkout pre-agent-3-2026-05-04 -- src/agents/multi_location/ src/platform/schemas.py
# Build + deploy normally — the new audit variants are removed; LogEntry
# discriminator falls back to _UnknownLogEntry passthrough for any
# already-written multi_location_* rows.

# Option C — Disable specific use case (e.g., service-area check)
# Edit apply-catering-owner-decision (or wherever service-area is invoked)
# to skip the validate-service-area.py subprocess call.
```

(Removed v1's fragile sed pattern per Reviewer 1 finding H5 — manual YAML edit is safer.)

## Out of scope (future PRs)

- Inter-location employee transfers (Phase 2 — `propose_inter_location_transfer` SKILL)
- Per-location data partitioning (`state/multi_location/<id>/...`)
- Cross-location anomaly detection
- Custom service-area polygons (vs. radius)
- Live traffic-aware routing
- Daily Brief multi-location timezone integration (when DB itself goes multi-location)
- Geocode cache (when scale demands it; lat/lon-only, no PII)
