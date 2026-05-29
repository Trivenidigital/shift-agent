"""Commerce audit chokepoint.

All commerce-primitive audit writes go through `emit()` here, which in turn
routes through `safe_io.ndjson_append`. No commerce module writes to
decisions.log directly — the CI grep gate in
tests/test_commerce_audit_chokepoint.py enforces this.

Mirrors the deployed pattern in docs/hermes-alignment.md Part 1:
NDJSON audit log + single chokepoint + per-VPS state isolation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ._io_shim import ndjson_append


def emit(decisions_log_path: Path, entry: Mapping[str, Any]) -> None:
    """Append one validated audit row to the NDJSON decisions log.

    Caller is responsible for constructing a dict that conforms to one of
    the commerce_* LogEntry variants in src/platform/schemas.py. We do not
    re-validate here: the dispatch chokepoint pattern keeps audit hot-path
    lean. Schema discipline is enforced at the LogEntry union read side
    (and at tests/test_commerce_logentry_variants.py write side).

    Raises ValueError if the JSON-serialized form would contain forbidden
    line-break chars (delegated to safe_io.ndjson_append).
    """
    if "ts" not in entry:
        entry = {**entry, "ts": datetime.now(timezone.utc).isoformat()}
    entry_json = json.dumps(entry, default=_json_default, separators=(",", ":"))
    ndjson_append(decisions_log_path, entry_json)


def _json_default(x: Any) -> str:
    if isinstance(x, datetime):
        return x.isoformat()
    raise TypeError(f"object of type {type(x).__name__} is not JSON serializable")
