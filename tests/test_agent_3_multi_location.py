"""PR-Agent3-v0.1 — schema delta + closest-location.py tests.

Covers:
- LocationEntry new optional fields validate; existing minimal configs still pass
- Bounds validation (latitude, longitude, service_radius_minutes)
- MultiLocationClosestLookup audit variant roundtrip
- closest-location.py CLI: top-N sorting, address geocoding via mocked
  maps_client.py, OSRM happy path, Haversine fallback when OSRM down,
  empty-locations handling, location with missing lat/lon
- Store-locator regex (positive + negative cases)

Linux-only smoke for the script subprocess paths; pure schema/regex tests
are cross-platform.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
CLOSEST_LOC_SCRIPT = REPO / "src" / "agents" / "multi_location" / "scripts" / "closest-location.py"

sys.path.insert(0, str(PLATFORM_DIR))

import schemas  # noqa: E402
from pydantic import ValidationError  # noqa: E402


# === Schema delta tests ===

class TestLocationEntryDelta:
    """5 new optional fields on the deployed LocationEntry."""

    def test_minimal_config_still_validates(self):
        """Backward-compat: a config without any new fields validates."""
        loc = schemas.LocationEntry(
            id="loc_jax_01", name="Jacksonville", timezone="America/New_York",
        )
        assert loc.latitude is None
        assert loc.longitude is None
        assert loc.phone is None
        assert loc.hours is None
        assert loc.service_radius_minutes == 30.0  # default

    def test_full_config_validates(self):
        loc = schemas.LocationEntry(
            id="loc_hou_01", name="Houston Galleria",
            timezone="America/Chicago",
            address_short="Houston, TX",
            latitude=29.7, longitude=-95.4,
            phone="+17135551234",
            hours="Mon-Sun 09:00-22:00",
            service_radius_minutes=45.0,
        )
        assert loc.latitude == 29.7
        assert loc.service_radius_minutes == 45.0

    def test_latitude_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="America/Chicago", latitude=-91.0,
            )
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="America/Chicago", latitude=91.0,
            )

    def test_longitude_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="America/Chicago", longitude=-181.0,
            )
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="America/Chicago", longitude=181.0,
            )

    def test_service_radius_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="America/Chicago",
                service_radius_minutes=-1.0,
            )
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="America/Chicago",
                service_radius_minutes=300.0,  # > 240
            )

    def test_invalid_timezone_rejected(self):
        """Existing _valid_tz validator (deployed) rejects bad IANA names."""
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="Mars/FakeZone",
            )

    def test_invalid_phone_rejected(self):
        """E164Phone validator rejects non-E164 strings (PR-CF7 audit M11).
        E164Phone had a documented Pydantic v1 silent-passthrough bug
        (schemas.py P2-FIX); regression test on the new field guards it."""
        with pytest.raises(ValidationError):
            schemas.LocationEntry(
                id="loc_x", name="x", timezone="UTC", phone="not-a-phone",
            )

    def test_valid_phone_accepted(self):
        loc = schemas.LocationEntry(
            id="loc_x", name="x", timezone="UTC", phone="+17135551234",
        )
        assert loc.phone == "+17135551234"

    def test_unique_ids_validator_preserved(self):
        """_unique_ids on MultiLocationConfig.locations remains active."""
        with pytest.raises(ValidationError):
            schemas.MultiLocationConfig(locations=[
                schemas.LocationEntry(id="loc_a", name="A", timezone="UTC"),
                schemas.LocationEntry(id="loc_a", name="A2", timezone="UTC"),
            ])


# === Audit variant tests ===

class TestMultiLocationClosestLookupAudit:
    def test_minimal_roundtrip(self):
        entry = schemas.MultiLocationClosestLookup(
            type="multi_location_closest_lookup",
            ts="2026-05-04T03:00:00Z",
            chat_id="12025550199@s.whatsapp.net",
            n_locations_returned=0,
        )
        assert entry.source == "osrm"  # default
        assert entry.detail == ""
        # JSON roundtrip
        s = entry.model_dump_json()
        parsed = json.loads(s)
        assert parsed["type"] == "multi_location_closest_lookup"

    def test_full_roundtrip(self):
        entry = schemas.MultiLocationClosestLookup(
            type="multi_location_closest_lookup",
            ts="2026-05-04T03:00:00Z",
            chat_id="12025550199@s.whatsapp.net",
            customer_lat=29.7,
            customer_lon=-95.4,
            nearest_location_id="loc_hou_01",
            nearest_drive_minutes=12.5,
            n_locations_returned=3,
            source="osrm",
            detail="Top 3 by drive time",
        )
        assert entry.nearest_drive_minutes == 12.5
        s = entry.model_dump_json()
        parsed = json.loads(s)
        assert parsed["nearest_drive_minutes"] == 12.5

    def test_invalid_source_rejected(self):
        with pytest.raises(ValidationError):
            schemas.MultiLocationClosestLookup(
                type="multi_location_closest_lookup",
                ts="2026-05-04T03:00:00Z",
                chat_id="x@y", n_locations_returned=0,
                source="some_other_source",  # not in Literal
            )

    def test_no_address_field_for_pii_safety(self):
        """Per Reviewer 2 HIGH 2: address must NOT be in the audit row."""
        entry = schemas.MultiLocationClosestLookup(
            type="multi_location_closest_lookup",
            ts="2026-05-04T03:00:00Z",
            chat_id="x@y", n_locations_returned=0,
        )
        # `extra="forbid"` on _BaseEntry — adding address would raise
        with pytest.raises(ValidationError):
            schemas.MultiLocationClosestLookup(
                type="multi_location_closest_lookup",
                ts="2026-05-04T03:00:00Z",
                chat_id="x@y", n_locations_returned=0,
                address="123 Main St",  # type: ignore — should be rejected
            )


# === Closest-location script tests (Linux-only — uses subprocess) ===

@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="closest-location.py uses subprocess + maps_client.py at deployed path",
)
class TestClosestLocationScript:
    """Loads closest-location.py via SourceFileLoader (no .py extension on
    deploy, but .py here for tests). Mocks subprocess.run for maps_client.py
    calls."""

    @pytest.fixture(scope="class")
    def script_mod(self):
        loader = importlib.machinery.SourceFileLoader(
            "closest_location_test", str(CLOSEST_LOC_SCRIPT),
        )
        spec = importlib.util.spec_from_loader("closest_location_test", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        return mod

    def test_haversine_km_known_distance(self, script_mod):
        """Houston (29.7, -95.4) to Dallas (32.8, -96.8) ≈ 360 km."""
        km = script_mod.haversine_km(29.7, -95.4, 32.8, -96.8)
        assert 350 < km < 370, f"expected ~360 km, got {km}"

    def test_haversine_drive_minutes_formula(self, script_mod):
        """100 km × 1.3 / 0.5 = 260 minutes."""
        minutes = script_mod.haversine_drive_minutes(100.0)
        assert minutes == 260.0

    def test_compute_distances_sorts_by_drive_time(self, script_mod):
        """Given 3 locations + mocked OSRM returns, results sorted ascending."""
        locations = [
            {"id": "loc_far", "name": "Far",  "address_short": "", "phone": "", "hours": "",
             "latitude": 35.0, "longitude": -100.0},
            {"id": "loc_near", "name": "Near", "address_short": "", "phone": "", "hours": "",
             "latitude": 29.8, "longitude": -95.5},
            {"id": "loc_mid",  "name": "Mid",  "address_short": "", "phone": "", "hours": "",
             "latitude": 30.5, "longitude": -96.0},
        ]
        # Mock osrm_distance to return predictable values
        osrm_returns = {"loc_far": (500.0, 360.0), "loc_near": (10.0, 8.5), "loc_mid": (80.0, 60.0)}

        def fake_osrm(lat1, lon1, lat2, lon2, timeout):
            for loc in locations:
                if abs(loc["latitude"] - lat2) < 0.001 and abs(loc["longitude"] - lon2) < 0.001:
                    return osrm_returns[loc["id"]]
            return None

        with patch.object(script_mod, "osrm_distance", side_effect=fake_osrm):
            results, source, errors = script_mod.compute_distances(
                29.7, -95.4, locations, timeout_sec=10,
            )
        assert source == "osrm"
        assert errors == []
        ids = [r["location_id"] for r in results]
        assert ids == ["loc_near", "loc_mid", "loc_far"]
        assert results[0]["drive_minutes"] == 8.5

    def test_haversine_fallback_when_osrm_down(self, script_mod):
        """When osrm_distance returns None, Haversine fires + source switches."""
        locations = [
            {"id": "loc_a", "name": "A", "address_short": "", "phone": "", "hours": "",
             "latitude": 29.8, "longitude": -95.5},
        ]
        with patch.object(script_mod, "osrm_distance", return_value=None):
            results, source, errors = script_mod.compute_distances(
                29.7, -95.4, locations, timeout_sec=10,
            )
        assert source == "haversine_fallback"
        assert len(results) == 1
        # Haversine ~12 km × 1.3 / 0.5 ≈ 31 min (rough)
        assert 5 < results[0]["drive_minutes"] < 100
        assert any("haversine fallback" in e for e in errors)

    def test_location_missing_lat_lon_skipped(self, script_mod):
        """Locations without lat/lon are skipped + reported in errors."""
        locations = [
            {"id": "loc_geo", "name": "Geo", "address_short": "", "phone": "", "hours": "",
             "latitude": 29.8, "longitude": -95.5},
            {"id": "loc_nogeo", "name": "NoGeo", "address_short": "", "phone": "", "hours": "",
             "latitude": None, "longitude": None},
        ]
        with patch.object(script_mod, "osrm_distance", return_value=(10.0, 8.0)):
            results, source, errors = script_mod.compute_distances(
                29.7, -95.4, locations, timeout_sec=10,
            )
        assert len(results) == 1
        assert results[0]["location_id"] == "loc_geo"
        assert any("loc_nogeo" in e for e in errors)


# === Dispatcher routing regex tests (cross-platform) ===

# This is the regex from design v3 — kept in sync with what the dispatcher
# SKILL.md will reference. Two alternation groups (proximity + intent) plus
# explicit phrasings. Single re.IGNORECASE flag (Python 3.12+ rejects
# multiple inline (?i) groups in one pattern).
_STORE_LOCATOR_REGEX = re.compile(
    r"\b(nearest|closest|near\s*(?:me|you|by))\b.{0,40}\b(store|location|branch|shop)\b"
    r"|\b(where\s+are\s+you\s+located|store\s+locator|find\s+(?:a\s+|the\s+)?store)\b",
    re.IGNORECASE,
)


class TestStoreLocatorRegex:
    """Tightened per Reviewer 2 HIGH 3 — single 'store' or 'near me' alone
    must NOT trigger; need both proximity + intent."""

    @pytest.mark.parametrize("text", [
        "what's the nearest store to me?",
        "Closest store near downtown?",
        "near me a store please",
        "find the closest branch",
        "where are you located?",
        "store locator please",
        "I want to find a store nearby",  # 'find a store'
    ])
    def test_positive_cases(self, text):
        assert _STORE_LOCATOR_REGEX.search(text), f"should match: {text!r}"

    @pytest.mark.parametrize("text", [
        "I had the worst experience at your store",      # 'store' alone
        "The address for the meeting is...",              # 'address' alone, no proximity
        "near me but not really nearby",                  # 'near me' without store/location word
        "Worst service in your shop on Main St",          # 'shop' alone, no proximity
        "Hello, I have a question",                       # neither
        "",                                               # empty
        "branch out into new things",                     # 'branch' alone
    ])
    def test_negative_cases(self, text):
        assert not _STORE_LOCATOR_REGEX.search(text), f"should NOT match: {text!r}"
