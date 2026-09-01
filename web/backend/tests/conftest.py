"""Pytest fixtures for cockpit tests.

- Forces COCKPIT_TEST_MODE=1 globally → Settings uses a tempdir for state paths
  instead of /opt/shift-agent.
- Prepends SME-Agents/src/ to sys.path so the agent's `safe_io` and `schemas`
  modules are importable without /opt/shift-agent existing on the runner.
- Resets the `get_settings()` lru_cache between tests.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

# Set BEFORE any app.* import in any test
os.environ.setdefault("COCKPIT_TEST_MODE", "1")
os.environ.setdefault("COCKPIT_JWT_SECRET", "0" * 64)
os.environ.setdefault("PUSHOVER_APP_TOKEN", "stub")
os.environ.setdefault("PUSHOVER_USER_KEY", "stub")

# FLYER_STATE_ROOT is read SRC-side by src/agents/flyer/manual_queue.py:441,
# which the cockpit imports. COCKPIT_TEST_MODE redirects `Settings` paths but
# has no reach into that module, so without this the manual-queue write path
# resolves to /opt/shift-agent/state/flyer and `safe_io` refuses it under
# pytest -- correctly, since it is a production path.
#
# That is the whole of the "main-inherited breakage" that has kept
# test_manual_queue_complete_* --deselect'ed from cockpit-ci since
# 2026-07-18: the production code was fine and had an env override the whole
# time; the cockpit harness simply never set it. Set here rather than in the
# two tests so the next test to touch a flyer write path cannot hit the same
# wall silently.
os.environ.setdefault(
    "FLYER_STATE_ROOT",
    str(Path(tempfile.mkdtemp(prefix="cockpit-test-flyer-")) / "flyer"),
)

# Make the agent's safe_io + schemas importable from the project's src/.
# The cockpit code does `sys.path.insert(0, "/opt/shift-agent")` at import
# time which obviously fails outside the deployment box. Prepending src/
# lets the same imports succeed against the source tree.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC = _PROJECT_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_PLATFORM = _SRC / "platform"
if _PLATFORM.is_dir() and str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

if os.name == "nt" and "fcntl" not in sys.modules:
    fcntl_stub = types.ModuleType("fcntl")
    fcntl_stub.LOCK_EX = 2
    fcntl_stub.LOCK_UN = 8
    fcntl_stub.LOCK_NB = 4
    fcntl_stub.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl_stub

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Clear cached Settings between tests so monkeypatched paths take effect."""
    from app import config as cfg_mod

    cfg_mod.get_settings.cache_clear()
    yield
    cfg_mod.get_settings.cache_clear()
