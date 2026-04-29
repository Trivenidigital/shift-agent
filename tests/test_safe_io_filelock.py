"""FileLock retry-acquire semantics — tests for try_acquire_filelock_with_retry.

Linux-only: depends on fcntl. Event-coordinated; no sleeps in test bodies.
"""
from __future__ import annotations
import platform
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="fcntl is Linux-only",
)

# Guarded import: safe_io imports fcntl unconditionally at module level, fails
# on Windows even when pytestmark would skip the tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
if platform.system() != "Windows":
    from safe_io import (  # noqa: E402
        FileLock, LockUnavailable, try_acquire_filelock_with_retry,
    )


def test_try_acquire_succeeds_after_holder_releases(tmp_path):
    """Retry-acquire MUST yield the lock once the contended holder releases.

    Event-coordinated: holder grabs lock, signals it holds, contender retries,
    test releases holder, contender completes. No sleeps in test body.
    """
    lock_path = tmp_path / "shared.lock"
    holder_holding = threading.Event()
    holder_release = threading.Event()
    contender_done = threading.Event()
    contender_acquired: list[bool] = []

    def holder():
        try:
            with FileLock(lock_path):
                holder_holding.set()
                holder_release.wait(5.0)
        except Exception:
            holder_holding.set()  # don't strand contender on holder failure
            raise

    def contender():
        try:
            with try_acquire_filelock_with_retry(lock_path, attempts=10, sleep_sec=0.05):
                contender_acquired.append(True)
        except LockUnavailable:
            contender_acquired.append(False)
        finally:
            contender_done.set()

    h = threading.Thread(target=holder, daemon=True)
    h.start()
    assert holder_holding.wait(2.0), "holder didn't grab lock"

    c = threading.Thread(target=contender, daemon=True)
    c.start()
    holder_release.set()  # let holder release; contender retry should succeed
    h.join(2.0)
    assert contender_done.wait(2.0), "contender didn't finish"
    assert contender_acquired == [True], contender_acquired


def test_try_acquire_raises_on_exhaustion(tmp_path):
    """Once attempts exhaust without acquisition, LockUnavailable raises (NOT
    a silent fall-through). The body inside `with` MUST NOT run."""
    lock_path = tmp_path / "contended.lock"
    holder_holding = threading.Event()
    holder_release = threading.Event()
    body_entered = []  # canary

    def holder():
        try:
            with FileLock(lock_path):
                holder_holding.set()
                holder_release.wait(5.0)
        finally:
            # Guarantee release on any exception path so the test always
            # completes deterministically rather than blocking on join().
            pass

    h = threading.Thread(target=holder, daemon=True)
    h.start()
    assert holder_holding.wait(2.0)

    try:
        with pytest.raises(LockUnavailable):
            with try_acquire_filelock_with_retry(lock_path, attempts=2, sleep_sec=0.05):
                body_entered.append(True)
        assert body_entered == [], (
            "body ran without lock — silent pass-through regression"
        )
    finally:
        holder_release.set()
        h.join(2.0)


def test_try_acquire_normal_path_unchanged(tmp_path):
    """FileLock context-manager API still works (regression check)."""
    lock_path = tmp_path / "normal.lock"
    with FileLock(lock_path):
        pass


def test_lock_unavailable_is_runtime_error():
    """Type hierarchy: callers can catch LockUnavailable specifically OR
    RuntimeError; both work."""
    assert issubclass(LockUnavailable, RuntimeError)


def test_try_acquire_zero_attempts_clamped_to_one(tmp_path):
    """Defensive: attempts=0 is clamped to 1 so the function always tries
    at least once; never an infinite no-op."""
    lock_path = tmp_path / "zero.lock"
    # No contention — single attempt should succeed
    with try_acquire_filelock_with_retry(lock_path, attempts=0, sleep_sec=0.0):
        pass
