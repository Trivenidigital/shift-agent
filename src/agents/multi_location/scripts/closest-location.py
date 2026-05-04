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
    """Return (lat, lon) from Nominatim via maps_client.py search, or None."""
    try:
        result = subprocess.run(
            [sys.executable, str(MAPS_CLIENT), "search", "--query", address, "--limit", "1"],
            capture_output=True, text=True, timeout=timeout_sec,
        )
        if result.returncode != 0:
            return None
        doc = json.loads(result.stdout)
        if not doc.get("results"):
            return None
        first = doc["results"][0]
        return float(first["latitude"]), float(first["longitude"])
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError, KeyError):
        return None


def osrm_distance(lat1: float, lon1: float, lat2: float, lon2: float,
                   timeout_sec: int) -> Optional[tuple[float, float]]:
    """Return (distance_km, drive_minutes) via maps_client.py distance, or None."""
    try:
        result = subprocess.run(
            [sys.executable, str(MAPS_CLIENT), "distance",
             "--from-lat", str(lat1), "--from-lon", str(lon1),
             "--to-lat", str(lat2), "--to-lon", str(lon2)],
            capture_output=True, text=True, timeout=timeout_sec,
        )
        if result.returncode != 0:
            return None
        doc = json.loads(result.stdout)
        km = float(doc.get("distance_km", 0))
        minutes = float(doc.get("duration_minutes", 0))
        if km <= 0 or minutes <= 0:
            return None
        return km, minutes
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError, KeyError):
        return None


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
        customer_input = {"address": args.address}
    else:
        customer_lat, customer_lon = args.lat, args.lon
        customer_input = {"lat": args.lat, "lon": args.lon}

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
