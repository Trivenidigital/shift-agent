"""Agent #3 — the shipped config template must yield a WORKING store locator.

Why this file exists (Wave-1 falsification, 2026-08-08): `closest-location.py`
skips any location whose `latitude`/`longitude` is None (see
`compute_distances`). `LocationEntry` defaults both to None. So a template whose
documented example omits coordinates validates cleanly, deploys cleanly, and
then returns zero results for every customer "where is your nearest store?"
query — the failure is invisible until a real customer asks.

The template's commented example is the operator's copy-paste source. These
tests treat it as executable documentation: parse it, build real
`LocationEntry` objects, and assert the locator actually ranks them.

Cross-platform: no safe_io / fcntl import, so this runs on Windows too.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "platform"))

TEMPLATE_PATH = REPO_ROOT / "src" / "agents" / "shift" / "config.yaml.template"
CLOSEST_LOC_SCRIPT = (
    REPO_ROOT / "src" / "agents" / "multi_location" / "scripts" / "closest-location.py"
)


def _extract_commented_locations_example(template_text: str) -> list[dict]:
    """Pull the `# locations:` example out of the multi_location block.

    The example ships commented so the default config stays single-location.
    We strip the leading `# ` and parse what remains as YAML — which is
    exactly what an operator does by hand when they uncomment it.
    """
    lines = template_text.splitlines()
    try:
        start = next(
            i for i, ln in enumerate(lines) if re.match(r"^\s*#\s*locations:\s*$", ln)
        )
    except StopIteration:  # pragma: no cover - guarded by its own test below
        pytest.fail("multi_location template no longer documents a `locations:` example")

    block: list[str] = []
    for ln in lines[start:]:
        stripped = ln.strip()
        if not stripped:
            break
        if not stripped.startswith("#"):
            break
        # Drop the comment marker, preserving the YAML indentation that follows.
        block.append(re.sub(r"^(\s*)#\s?", r"\1", ln))

    parsed = yaml.safe_load(textwrap.dedent("\n".join(block)))
    assert isinstance(parsed, dict) and "locations" in parsed, (
        "commented example did not parse into a mapping with a `locations` key"
    )
    return parsed["locations"]


@pytest.fixture(scope="module")
def template_text() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def example_locations(template_text: str) -> list[dict]:
    return _extract_commented_locations_example(template_text)


@pytest.fixture(scope="module")
def script_mod():
    """Load closest-location.py by path (it ships without a .py extension)."""
    loader = importlib.machinery.SourceFileLoader(
        "closest_location_cfg_test", str(CLOSEST_LOC_SCRIPT)
    )
    spec = importlib.util.spec_from_loader("closest_location_cfg_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_template_example_validates_as_location_entries(example_locations):
    """Baseline: the example is at least schema-valid. This passed before the fix."""
    from schemas import LocationEntry

    entries = [LocationEntry.model_validate(loc) for loc in example_locations]
    assert len(entries) >= 2, "example should show a genuinely multi-location config"


def test_template_example_carries_coordinates(example_locations):
    """Every documented location must have lat/lon.

    Without them `compute_distances` skips the location entirely, so an
    operator who copies this example verbatim ships a locator that can never
    answer. Schema validity is NOT sufficient here — the fields are Optional.
    """
    from schemas import LocationEntry

    missing = [
        loc.get("id", "<no id>")
        for loc in example_locations
        if LocationEntry.model_validate(loc).latitude is None
        or LocationEntry.model_validate(loc).longitude is None
    ]
    assert not missing, (
        "config.yaml.template documents multi_location entries without "
        f"latitude/longitude: {missing}. closest-location.py silently skips "
        "these, so the shipped example produces a store locator that returns "
        "zero results for every customer query."
    )


def test_template_example_actually_ranks_locations(example_locations, script_mod):
    """End-to-end: template-shaped config -> ranked results, OSRM stubbed out.

    This is the behavioral assertion the coordinate test protects. It uses the
    haversine fallback (osrm_distance -> None) so it needs no network.
    """
    from unittest.mock import patch

    from schemas import LocationEntry

    locations = [
        LocationEntry.model_validate(loc).model_dump() for loc in example_locations
    ]

    # Customer somewhere in the continental US; exact point does not matter,
    # only that every documented location is reachable by the distance math.
    with patch.object(script_mod, "osrm_distance", return_value=None):
        results, source, errors = script_mod.compute_distances(
            29.76, -95.37, locations, timeout_sec=10
        )

    assert source == "haversine_fallback"
    assert len(results) == len(locations), (
        f"expected all {len(locations)} documented locations to be rankable, "
        f"got {len(results)}; errors={errors}"
    )
    drive_times = [r["drive_minutes"] for r in results]
    assert drive_times == sorted(drive_times), "results must be sorted by drive time"
