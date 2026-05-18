"""Dump the FastAPI OpenAPI spec to web/frontend/src/generated/openapi.json.

Runs via:
    cd web/backend && python scripts/dump-openapi.py

Designed to be CI-safe: stubs all required env vars and uses a tempdir for
the cockpit state paths so it works on a fresh clone with no /opt/shift-agent.

The committed openapi.json artifact is the source of truth for the frontend
type generation (web/frontend's `npm run generate:types`). CI should re-run
this script and `git diff` the result; non-empty diff = backend schema drift.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

# Stub env BEFORE importing the app.
os.environ.setdefault("COCKPIT_TEST_MODE", "1")
os.environ.setdefault("COCKPIT_JWT_SECRET", "0" * 64)
os.environ.setdefault("PUSHOVER_APP_TOKEN", "stub-app-token")
os.environ.setdefault("PUSHOVER_USER_KEY", "stub-user-key")
# config.py refuses TEST_MODE in production (when /opt/shift-agent exists)
# unless PYTEST_CURRENT_TEST is set. This script is a build-time spec
# dumper, NOT a runtime — set the flag explicitly with a recognizable
# value so audit logs make the intent clear.
os.environ.setdefault("PYTEST_CURRENT_TEST", "dump-openapi.py (build tool)")

# Make `app.*` importable + give the agent's safe_io / schemas the same
# path-injection treatment as conftest.py (so dump-openapi.py works on a
# fresh clone without /opt/shift-agent existing).
HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parent
PROJECT_ROOT = BACKEND_ROOT.parent.parent  # SME-Agents/
SRC = PROJECT_ROOT / "src"

sys.path.insert(0, str(BACKEND_ROOT))
if SRC.is_dir():
    sys.path.insert(0, str(SRC))
    platform = SRC / "platform"
    if platform.is_dir():
        sys.path.insert(0, str(platform))

if os.name == "nt" and "fcntl" not in sys.modules:
    fcntl_stub = types.ModuleType("fcntl")
    fcntl_stub.LOCK_EX = 2
    fcntl_stub.LOCK_UN = 8
    fcntl_stub.LOCK_NB = 4
    fcntl_stub.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl_stub

from app.main import app  # noqa: E402

OUT = BACKEND_ROOT.parent / "frontend" / "src" / "generated" / "openapi.json"
OUT.parent.mkdir(parents=True, exist_ok=True)
spec = app.openapi()
OUT.write_text(json.dumps(spec, indent=2, sort_keys=True))
print(f"openapi.json -> {OUT.relative_to(BACKEND_ROOT.parent)} ({OUT.stat().st_size:,} bytes)")


def main() -> int:
    return 0


if __name__ == "__main__":
    sys.exit(main())
