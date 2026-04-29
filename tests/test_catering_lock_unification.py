"""Lock-target unification tests (silent-failure-hunter NEW-5).

Verifies that lookup-prior-leads-by-phone now serializes against
safe_io.FileLock writer hold-time on the same .lock-sibling target that
create-catering-lead and apply-catering-owner-decision use.

Linux-only via fcntl.
"""
from __future__ import annotations
import platform
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io's fcntl path (Linux only)",
)

if platform.system() != "Windows":
    import importlib.util
    import importlib.machinery

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
    from safe_io import FileLock  # noqa: E402

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    LOOKUP = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "lookup-prior-leads-by-phone"
    APPLY = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"
    CREATE = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "create-catering-lead"


def _load_hyphen_module(name: str, path: Path):
    """SourceFileLoader pattern for hyphen-named scripts (no .py extension)."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, str(path), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lookup_serializes_against_writer_filelock(tmp_path):
    """Migration check: lookup's lock now BLOCKS while a safe_io.FileLock
    writer holds the .lock sibling. Confirms unified-lock-target pattern.

    Pre-NEW-5: lookup locked the data fd directly; writer used .lock sibling;
    they didn't serialize. Post-NEW-5: both target the .lock sibling, so a
    held writer lock causes lookup to time out → lookup_status=lock_timeout.
    """
    leads_path = tmp_path / "catering-leads.json"
    leads_path.write_text('{"schema_version":1,"leads":[]}', encoding="utf-8")
    lock_path = tmp_path / "catering-leads.json.lock"

    writer_holding = threading.Event()
    writer_release = threading.Event()

    def writer():
        try:
            with FileLock(lock_path):
                writer_holding.set()
                writer_release.wait(5.0)
        except Exception:
            writer_holding.set()
            raise

    w = threading.Thread(target=writer, daemon=True)
    w.start()
    assert writer_holding.wait(2.0), "writer didn't grab lock"

    mod = _load_hyphen_module("lookup_prior_leads_by_phone_test", LOOKUP)
    mod.LEADS_PATH = leads_path
    mod.LEADS_LOCK = lock_path
    # Tight retry budget — writer holds for ~5s; we want lookup to time out
    # well within that window. 3 attempts x 0.05s = 0.15s, well under 5s.
    # Reviewer R2 flagged 100ms as too tight on slow CI; bumping to 1s total.
    mod.LOCK_RETRY_ATTEMPTS = 3
    mod.LOCK_RETRY_SLEEP_SEC = 0.5

    result = mod.lookup_prior_leads_by_phone("+19045551234")
    assert result["lookup_status"] == "lock_timeout", result

    writer_release.set()
    w.join(2.0)


def test_lookup_serializes_acquires_after_writer_releases(tmp_path):
    """Complementary check: once writer releases, lookup acquires + reads."""
    leads_path = tmp_path / "catering-leads.json"
    leads_path.write_text('{"schema_version":1,"leads":[]}', encoding="utf-8")
    lock_path = tmp_path / "catering-leads.json.lock"

    writer_holding = threading.Event()
    writer_release = threading.Event()
    writer_done = threading.Event()

    def writer():
        try:
            with FileLock(lock_path):
                writer_holding.set()
                writer_release.wait(5.0)
        finally:
            writer_done.set()

    w = threading.Thread(target=writer, daemon=True)
    w.start()
    assert writer_holding.wait(2.0)

    mod = _load_hyphen_module("lookup_prior_leads_by_phone_test_b", LOOKUP)
    mod.LEADS_PATH = leads_path
    mod.LEADS_LOCK = lock_path
    mod.LOCK_RETRY_ATTEMPTS = 30  # ~15s total budget — generous
    mod.LOCK_RETRY_SLEEP_SEC = 0.5

    # Release writer; lookup should retry-acquire and return ok / no_match
    writer_release.set()
    assert writer_done.wait(2.0)

    result = mod.lookup_prior_leads_by_phone("+19045551234")
    # Empty leads → no_match (not lock_timeout)
    assert result["lookup_status"] == "no_match", result


def test_lookup_lock_target_constant_matches_leads_path_dot_lock():
    """Convention assertion: LEADS_LOCK == LEADS_PATH + ".lock". Drift-detector
    if a future refactor moves writers to flock(LEADS_PATH) (the convenience
    wrapper at safe_io.py:86) while leaving lookup on a different path."""
    mod = _load_hyphen_module("lookup_constants_test", LOOKUP)
    assert str(mod.LEADS_LOCK) == str(mod.LEADS_PATH) + ".lock", (
        f"lookup script: LEADS_LOCK ({mod.LEADS_LOCK}) != LEADS_PATH.lock "
        f"({mod.LEADS_PATH}.lock)"
    )


def test_apply_and_create_share_same_lock_target_as_lookup():
    """Cross-script convention assertion (per R1 design-review finding):
    all three scripts that touch catering-leads.json must lock on the SAME
    target — otherwise they don't serialize against each other regardless of
    individual correctness."""
    apply_mod = _load_hyphen_module("apply_constants_test", APPLY)
    create_mod = _load_hyphen_module("create_constants_test", CREATE)
    lookup_mod = _load_hyphen_module("lookup_constants_test_b", LOOKUP)

    assert str(apply_mod.LEADS_LOCK) == str(create_mod.LEADS_LOCK) == str(lookup_mod.LEADS_LOCK), (
        f"LEADS_LOCK drift across scripts: apply={apply_mod.LEADS_LOCK} "
        f"create={create_mod.LEADS_LOCK} lookup={lookup_mod.LEADS_LOCK}"
    )
    # And all three equal the canonical pattern
    expected = str(apply_mod.LEADS_PATH) + ".lock"
    assert str(apply_mod.LEADS_LOCK) == expected, (
        f"apply-script LEADS_LOCK ({apply_mod.LEADS_LOCK}) != canonical "
        f"LEADS_PATH+.lock ({expected})"
    )
