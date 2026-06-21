"""Tests for flyer_store_maintenance — durable CD v2 rollback safety.

`creative_direction` is now Field(exclude=True), so NEW writes never persist the
key. But rows written BEFORE that fix may still carry it, and old
`extra="forbid"` rollback code would reject it. The deploy-time scrub removes any
lingering `creative_direction` key from the project store. These tests pin:

- strip_creative_direction: removes the key from every project, returns the
  count removed, is idempotent (2nd call returns 0), tolerates a malformed /
  non-dict entry without raising, and leaves other keys intact.
- scrub_store_file: removes the keys + rewrites a temp store; returns 0 (and
  does NOT rewrite) on a clean file; never raises on a missing file (returns 0).

The scrub is intentionally narrow: it removes ONLY the `creative_direction` key.
With exclude=True the key is never legitimately persisted, so removing it loses
nothing (idempotent + safe).
"""
from __future__ import annotations

import json
from pathlib import Path

import flyer_store_maintenance as fsm


def _store(*projects: dict) -> dict:
    return {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": list(projects)}


def test_strip_removes_key_from_all_projects_and_returns_count():
    store = _store(
        {"project_id": "F0001", "creative_direction": {"palette": "warm"}},
        {"project_id": "F0002", "creative_direction": {"palette": "cool"}},
    )
    removed = fsm.strip_creative_direction(store)
    assert removed == 2
    assert all("creative_direction" not in p for p in store["projects"])


def test_strip_is_idempotent_second_call_returns_zero():
    store = _store({"project_id": "F0001", "creative_direction": {"x": 1}})
    assert fsm.strip_creative_direction(store) == 1
    # Second pass: nothing left to strip.
    assert fsm.strip_creative_direction(store) == 0


def test_strip_only_counts_projects_that_had_the_key():
    store = _store(
        {"project_id": "F0001", "creative_direction": {"x": 1}},
        {"project_id": "F0002"},  # no key
        {"project_id": "F0003", "creative_direction": None},  # key present (even if None)
    )
    assert fsm.strip_creative_direction(store) == 2


def test_strip_leaves_other_keys_intact():
    store = _store(
        {"project_id": "F0001", "status": "ready", "creative_direction": {"x": 1}, "version": 3},
    )
    fsm.strip_creative_direction(store)
    proj = store["projects"][0]
    assert proj["project_id"] == "F0001"
    assert proj["status"] == "ready"
    assert proj["version"] == 3
    assert "creative_direction" not in proj


def test_strip_tolerates_malformed_non_dict_entries_without_raising():
    store = {
        "schema_version": 1,
        "next_sequence": 9,
        "projects": [
            {"project_id": "F0001", "creative_direction": {"x": 1}},
            "not-a-dict",  # malformed entry
            None,  # malformed entry
            42,  # malformed entry
            {"project_id": "F0002", "creative_direction": {"y": 2}},
        ],
    }
    # Must not raise; must still strip the well-formed entries.
    removed = fsm.strip_creative_direction(store)
    assert removed == 2
    assert "creative_direction" not in store["projects"][0]
    assert "creative_direction" not in store["projects"][4]
    # Malformed entries are left untouched.
    assert store["projects"][1] == "not-a-dict"
    assert store["projects"][2] is None
    assert store["projects"][3] == 42


def test_strip_tolerates_missing_projects_key():
    assert fsm.strip_creative_direction({"schema_version": 1}) == 0
    assert fsm.strip_creative_direction({}) == 0


def test_strip_tolerates_non_list_projects():
    # Defensive: a malformed store whose "projects" isn't a list must not raise.
    assert fsm.strip_creative_direction({"projects": "oops"}) == 0
    assert fsm.strip_creative_direction({"projects": None}) == 0


def test_scrub_store_file_removes_keys_and_rewrites(tmp_path: Path):
    path = tmp_path / "projects.json"
    store = _store(
        {"project_id": "F0001", "creative_direction": {"x": 1}},
        {"project_id": "F0002", "creative_direction": {"y": 2}},
    )
    path.write_text(json.dumps(store), encoding="utf-8")

    removed = fsm.scrub_store_file(str(path))
    assert removed == 2

    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert all("creative_direction" not in p for p in rewritten["projects"])
    # Structural envelope preserved.
    assert rewritten["schema_version"] == 1
    assert rewritten["next_sequence"] == 3
    assert [p["project_id"] for p in rewritten["projects"]] == ["F0001", "F0002"]


def test_scrub_store_file_clean_file_returns_zero_and_does_not_rewrite(tmp_path: Path):
    path = tmp_path / "projects.json"
    store = _store({"project_id": "F0001", "status": "ready"})
    raw = json.dumps(store)
    path.write_text(raw, encoding="utf-8")
    mtime_before = path.stat().st_mtime_ns

    removed = fsm.scrub_store_file(str(path))
    assert removed == 0
    # No rewrite when nothing changed → byte-identical file, unchanged mtime.
    assert path.read_text(encoding="utf-8") == raw
    assert path.stat().st_mtime_ns == mtime_before


def test_scrub_store_file_missing_file_returns_zero(tmp_path: Path):
    missing = tmp_path / "does-not-exist.json"
    assert not missing.exists()
    assert fsm.scrub_store_file(str(missing)) == 0


def test_scrub_store_file_idempotent(tmp_path: Path):
    path = tmp_path / "projects.json"
    store = _store({"project_id": "F0001", "creative_direction": {"x": 1}})
    path.write_text(json.dumps(store), encoding="utf-8")
    assert fsm.scrub_store_file(str(path)) == 1
    # Second pass: already clean → 0, no rewrite.
    mtime_after_first = path.stat().st_mtime_ns
    assert fsm.scrub_store_file(str(path)) == 0
    assert path.stat().st_mtime_ns == mtime_after_first
