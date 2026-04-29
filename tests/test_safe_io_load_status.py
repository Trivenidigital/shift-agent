"""safe_io.assert_load_status_clean — chokepoint for unhealthy load_model status.

Pure-function unit tests. Skipped on Windows because safe_io.py imports fcntl
unconditionally at module level (Linux-only); decoupling that is a separate
refactor out of scope here.
"""
from __future__ import annotations
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io imports fcntl unconditionally (Linux-only module)",
)

# Import guarded behind platform check: safe_io.py does `import fcntl` at module
# level, which fails on Windows even when the test would skip. Skip-collect-but-
# still-import is pytest's default behavior with pytestmark, so we must defer.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
if platform.system() != "Windows":
    from safe_io import LoadStatusError, assert_load_status_clean  # noqa: E402


def test_assert_clean_status_ok_returns_silently():
    assert_load_status_clean(Path("/x/y.json"), "ok", context="t")


def test_assert_clean_status_missing_passes():
    """missing/empty are healthy: caller is expected to fall back to default."""
    assert_load_status_clean(Path("/x/y.json"), "missing", context="t")


def test_assert_clean_status_empty_passes():
    assert_load_status_clean(Path("/x/y.json"), "empty", context="t")


def test_assert_clean_corrupt_raises():
    with pytest.raises(LoadStatusError) as exc:
        assert_load_status_clean(Path("/x/y.json"), "corrupt:bad json", context="my-ctx")
    msg = str(exc.value)
    assert "corrupt" in msg.lower()
    assert "my-ctx" in msg


def test_assert_clean_corrupt_unrenamed_raises():
    with pytest.raises(LoadStatusError):
        assert_load_status_clean(
            Path("/x/y.json"), "corrupt_unrenamed:eperm", context="t"
        )


def test_assert_clean_oserror_raises():
    with pytest.raises(LoadStatusError) as exc:
        assert_load_status_clean(
            Path("/x/y.json"), "oserror:permission denied", context="my-ctx"
        )
    msg = str(exc.value)
    assert "oserror" in msg.lower() or "i/o" in msg.lower() or "permission" in msg.lower()
    assert "my-ctx" in msg


def test_assert_clean_unknown_status_raises():
    """Future-proofing: novel statuses propagate as errors so future
    safe_load_json additions force a callsite review rather than silently
    passing through three different scripts."""
    with pytest.raises(LoadStatusError):
        assert_load_status_clean(Path("/x"), "future_status:weird", context="t")


def test_load_status_error_is_runtime_error():
    """Exposed type hierarchy: callers can catch LoadStatusError specifically
    OR fall back to RuntimeError; both work."""
    assert issubclass(LoadStatusError, RuntimeError)
