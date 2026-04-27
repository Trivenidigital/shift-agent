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
from pathlib import Path

# Stub env BEFORE importing the app.
os.environ.setdefault("COCKPIT_TEST_MODE", "1")
os.environ.setdefault("COCKPIT_JWT_SECRET", "0" * 64)
os.environ.setdefault("PUSHOVER_APP_TOKEN", "stub-app-token")
os.environ.setdefault("PUSHOVER_USER_KEY", "stub-user-key")

# Make `app.*` importable
HERE = Path(__file__).resolve().parent
BACKEND_ROOT = HERE.parent
sys.path.insert(0, str(BACKEND_ROOT))

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
