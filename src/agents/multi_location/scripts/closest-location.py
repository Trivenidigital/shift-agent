#!/usr/bin/env python3
"""closest-location.py — Agent #3 nearest-store lookup (PR-Agent3-v0.1).

Wraps the bundled productivity/maps skill (maps_client.py at
/usr/local/lib/hermes-agent/skills/productivity/maps/scripts/) to compute
driving distance from a customer location to each entry in
cfg.multi_location.locations[], then returns the top-N sorted by drive
minutes.

CLI:
  closest-location.py --lat <float> --lon <float> [--top-n N]
  closest-location.py --address "<str>" [--top-n N]
  Optional: --config-path <path> (default /opt/shift-agent/config.yaml)
            --timeout-sec N      (default 10)

Output (JSON to stdout):
  {
    "source": "osrm" | "haversine_fallback",
    "results": [{"location_id", "name", "address_short", "phone", "hours",
                 "drive_minutes", "distance_km"}, ...],
    "customer_input": {"address": "..."} | {"lat": ..., "lon": ...},
    "n_locations_total": <int>,
    "n_returned": <int>,
    "errors": [<str>, ...],
  }

Exit codes:
  0 — success (results returned, possibly degraded source)
  1 — invalid input (bad lat/lon, no address resolvable)
  2 — config error (multi_location.locations is empty)
  3 — all upstream services unreachable (no fallback possible)

Haversine fallback formula (when OSRM unreachable):
  drive_minutes = (haversine_km * 1.3) / 0.5  # km/min ≈ 30 km/h urban
  The 1.3 multiplier is the commonly cited mean urban detour factor from
  OSM routing literature (Boeing 2017 OSMnx; Newell 1980 detour studies).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import Optional

# Deployed paths (mutable for tests)
CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
PLATFORM_DIR = Path("/opt/shift-agent")  # Where schemas.py lives
MAPS_CLIENT = Path(
    "/usr/local/lib/hermes-agent/skills/productivity/maps/scripts/maps_client.py"
)

# Haversine fallback constants
URBAN_DETOUR_FACTOR = 1.3  # Boeing 2017 (OSMnx); Newell 1980 detour studies
URBAN_KM_PER_MIN = 0.5     # ~30 km/h average urban driving speed

EXIT_OK = 0
EXIT_INVALID_INPUT = 1
EXIT_CONFIG_EMPTY = 2
EXIT_ALL_UPSTREAM_DOWN = 3


def load_locations(config_path: Path) -> list[dict]:
    """Load multi_location.locations[] from config.yaml.

    Returns list of dicts with keys we care about: id, name, timezone,
    address_short, latitude, longitude, phone, hours.
    """
    sys.path.insert(0, str(PLATFORM_DIR))
    import yaml  # type: ignore
    from schemas import Config  # type: ignore

    with config_path.open() as f:
        raw = yaml.safe_load(f)
    cfg = Config.model_validate(raw)
    return [loc.model_dump() for loc in cfg.multi_location.locations]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    r = 6371.0  # Earth radius km
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def haversine_drive_minutes(km: float) -> float:
    return (km * URBAN_DETOUR_FACTOR) / URBAN_KM_PER_MIN


def geocode_address(address: str, timeout_sec: int) -> Optional[tuple[float, float]]:
    """Return (lat, lon) from Nominatim via maps_client.py search, or None.

    HOTFIX 2026-05-04 (E2E-BUG-2): the actual maps_client.py CLI takes
    address as a POSITIONAL arg, not `--query`. Earlier `--query --limit 1`
    invocation never matched the CLI; mocked unit tests didn't catch it.
    Verified shape: `maps_client.py search "Times Square"` returns
    {"latitude": ..., "longitude": ..., ...} or {"error": "..."}.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(MAPS_CLIENT), "search", address],
            capture_output=True, text=True, timeout=timeout_sec,
        )
        if result.returncode != 0:
            return None
        doc = json.loads(result.stdout)
        # maps_client.search returns a single result (top match), not a list.
        # Either {"latitude": X, "longitude": Y, ...} or {"error": "..."}.
        if "error" in doc:
            return None
        lat = doc.get("latitude")
        lon = doc.get("longitude")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError, KeyError):
        return None


def osrm_distance(lat1: float, lon1: float, lat2: float, lon2: float,
                   timeout_sec: int) -> Optional[tuple[float, float]]:
    """OSRM-via-maps_client is unavailable in v0.1.

    HOTFIX 2026-05-04 (E2E-BUG-3): the actual maps_client.py distance
    subcommand takes ADDRESSES (`distance "Origin Address" --to "Dest"`),
    not lat/lon flags. Reverse-geocoding all 9 location coordinates per
    inbound query is a 9-API-call slow path with rate-limit risk
    (Nominatim caps at 1 req/s).

    For v0.1: return None unconditionally so compute_distances falls back
    to Haversine (urban-detour-factor 1.3 / urban-km-per-min 0.5). The
    fallback is plenty accurate for "which of our N stores is closest?";
    the OSRM-driving-time precision was nice-to-have.

    v0.2 path: call OSRM HTTP directly (https://router.project-osrm.org/route/v1/driving/lon1,lat1;lon2,lat2)
    or extend maps_client.py to accept lat/lon for distance.
    """
    return None  # always fall back to Haversine in v0.1


def compute_distances(customer_lat: float, customer_lon: float,
                       locations: list[dict], timeout_sec: int,
                       ) -> tuple[list[dict], str, list[str]]:
    """For each location, compute (distance_km, drive_minutes). Try OSRM
    per location, fall back to Haversine if any fails. Returns
    (results, source, errors). source='osrm' if ALL hit OSRM cleanly,
    'haversine_fallback' if any fell back."""
    results: list[dict] = []
    errors: list[str] = []
    any_fallback = False

    for loc in locations:
        loc_lat = loc.get("latitude")
        loc_lon = loc.get("longitude")
        if loc_lat is None or loc_lon is None:
            errors.append(f"location {loc['id']!r} missing latitude/longitude; skipping")
            continue

        live = osrm_distance(customer_lat, customer_lon, loc_lat, loc_lon, timeout_sec)
        if live is not None:
            km, minutes = live
        else:
            any_fallback = True
            km = haversine_km(customer_lat, customer_lon, loc_lat, loc_lon)
            minutes = haversine_drive_minutes(km)
            errors.append(f"OSRM unreachable for {loc['id']!r}; using haversine fallback")

        results.append({
            "location_id": loc["id"],
            "name": loc["name"],
            "address_short": loc.get("address_short", ""),
            "phone": loc.get("phone", ""),
            "hours": loc.get("hours", ""),
            "drive_minutes": round(minutes, 1),
            "distance_km": round(km, 2),
        })

    results.sort(key=lambda r: r["drive_minutes"])
    source = "haversine_fallback" if any_fallback else "osrm"
    return results, source, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Find nearest stores by drive time")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--lat", type=float)
    g.add_argument("--address", type=str)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--config-path", type=Path, default=CONFIG_PATH)
    ap.add_argument("--timeout-sec", type=int, default=10)
    args = ap.parse_args()

    if args.lat is not None and args.lon is None:
        sys.stderr.write("--lat requires --lon\n")
        return EXIT_INVALID_INPUT
    if args.address and (args.lat is not None or args.lon is not None):
        # argparse's mutually_exclusive_group only covers --address vs --lat;
        # --lon is a standalone optional, so this combo silently used to
        # work and ignore --lon. Reject explicitly to avoid the footgun.
        sys.stderr.write("--address cannot be combined with --lat/--lon\n")
        return EXIT_INVALID_INPUT
    top_n = max(1, min(args.top_n, 10))

    try:
        locations = load_locations(args.config_path)
    except Exception as e:
        sys.stderr.write(f"failed to load config: {e}\n")
        return EXIT_INVALID_INPUT
    if not locations:
        out = {"source": "not_configured", "results": [], "n_locations_total": 0,
                "n_returned": 0, "errors": ["multi_location.locations is empty"]}
        print(json.dumps(out))
        return EXIT_CONFIG_EMPTY

    if args.address:
        coords = geocode_address(args.address, args.timeout_sec)
        if coords is None:
            sys.stderr.write(f"could not geocode address: {args.address!r}\n")
            return EXIT_INVALID_INPUT
        customer_lat, customer_lon = coords
        # PII safety: do NOT echo the customer's address into stdout. The
        # SKILL.md instructs the LLM not to log the address to the audit
        # row, but the LLM consumes this script's stdout as context — if
        # we put the address here, the LLM may copy it into the audit row's
        # free-text `detail` field despite the SKILL.md instruction.
        customer_input = {"address_provided": True}
    else:
        customer_lat, customer_lon = args.lat, args.lon
        # Round to 2 decimal places (~1km precision) — enough to identify
        # the customer's neighborhood for nearest-store math without
        # logging fingerprinting-precision coordinates. PII reduction.
        customer_input = {
            "lat": round(args.lat, 2),
            "lon": round(args.lon, 2),
        }

    results, source, errors = compute_distances(
        customer_lat, customer_lon, locations, args.timeout_sec,
    )
    if not results:
        out = {"source": source, "results": [], "n_locations_total": len(locations),
                "n_returned": 0, "errors": errors or ["all locations unreachable"]}
        print(json.dumps(out))
        return EXIT_ALL_UPSTREAM_DOWN

    out = {
        "source": source,
        "results": results[:top_n],
        "customer_input": customer_input,
        "n_locations_total": len(locations),
        "n_returned": min(top_n, len(results)),
        "errors": errors,
    }
    print(json.dumps(out))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
