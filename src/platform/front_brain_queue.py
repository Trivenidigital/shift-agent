"""Front-brain unfulfillable-request queue (Phase-1 item 5).

Durable JSON store of customer requests the front-brain cannot yet fulfill (a
theme/style change before style-transfer lands, a not-yet-built feature).
Nothing a customer asks for is silently dropped: the request is recorded here for
operator follow-up and a ``front_brain_request_queued`` audit row is emitted. The
queue resolves when the capability lands (e.g. a theme request becomes
fulfillable once style-transfer ships, per the plan's sequencing note).

Deployed-pattern notes (mirrors ``front_brain_budget`` + ``safe_io``
conventions): JSON-on-disk under ``/opt/shift-agent/state/front_brain/``, written
through ``safe_io.atomic_write_json`` — so the pytest prod-write guard fires
automatically (a test that forgets to override the path fails-open here instead
of polluting the deployed tree) — under ``safe_io.flock``. The ``safe_io`` import
is lazy so this module imports cleanly on non-POSIX (Windows CI collects it; the
write path runs on Linux/Docker like the rest of ``safe_io``). The chat key is
HASHED in both the store and the audit row (no raw identifier persisted); the
customer's own ``request_text`` IS kept (bounded) because the operator needs the
words to fulfill it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Deployed default; overridable via env for tests + operator relocation.
FRONT_BRAIN_REQUEST_QUEUE_PATH = Path(
    "/opt/shift-agent/state/front_brain/request_queue.json"
)

# Bound the store so a pathological chat cannot grow it without limit; oldest
# items are dropped first (they are the least likely to still be actionable).
MAX_QUEUE_ITEMS = 500

# Mirrors schemas.FrontBrainRequestQueued.request_kind — anything else -> "other".
REQUEST_KINDS: tuple[str, ...] = (
    "theme_change",
    "style_preference",
    "feature_request",
    "other",
)


def _queue_path() -> Path:
    return Path(
        os.environ.get("FRONT_BRAIN_REQUEST_QUEUE_PATH") or FRONT_BRAIN_REQUEST_QUEUE_PATH
    )


def _chat_key_hash(chat_key: str) -> str:
    """sha256[:32] of the chat key — mirrors safe_io._front_brain_chat_key_hash so
    the store never persists a raw chat identifier."""
    return hashlib.sha256(
        str(chat_key or "").encode("utf-8", errors="ignore")
    ).hexdigest()[:32]


def _normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    return k if k in REQUEST_KINDS else "other"


def _load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _stderr(msg: str) -> None:
    try:
        sys.stderr.write(msg + "\n")
    except Exception:
        pass


def load_queue(state_path: "Optional[os.PathLike[str] | str]" = None) -> list[dict]:
    """Return the queued items (operator surface / tests). Empty on any error."""
    path = Path(state_path) if state_path is not None else _queue_path()
    items = _load(path).get("items")
    return list(items) if isinstance(items, list) else []


def queue_unfulfillable_request(
    *,
    chat_key: str,
    request_text: str,
    request_kind: str = "other",
    detail: str = "",
    now: Optional[datetime] = None,
    state_path: "Optional[os.PathLike[str] | str]" = None,
) -> dict:
    """Append an unfulfillable customer request to the durable store and emit a
    ``front_brain_request_queued`` audit row. Returns the stored item dict.

    Fail-open on the hot path: persistence / audit errors are swallowed (logged
    to stderr) and the item dict is still returned — the caller's warm
    acknowledgment must never depend on the store being writable."""
    kind = _normalize_kind(request_kind)
    ts = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    item = {
        "queued_ts": ts,
        "chat_key_hash": _chat_key_hash(chat_key),
        "request_kind": kind,
        "request_text": str(request_text or "")[:2000],
        "detail": str(detail or "")[:500],
    }
    path = Path(state_path) if state_path is not None else _queue_path()

    queue_size = 0
    try:
        import safe_io  # type: ignore  # lazy: safe_io imports fcntl (Linux only)

        with safe_io.flock(path):
            doc = _load(path)
            items = doc.get("items")
            if not isinstance(items, list):
                items = []
            items.append(item)
            if len(items) > MAX_QUEUE_ITEMS:
                items = items[-MAX_QUEUE_ITEMS:]
            # atomic_write_json runs safe_io's prod-write guard automatically.
            safe_io.atomic_write_json(path, {"schema_version": 1, "items": items})
            queue_size = len(items)
    except Exception as e:
        _stderr(
            f"front_brain_queue: persist failed (non-fatal): "
            f"{type(e).__name__}: {str(e)[:160]}"
        )

    try:
        import safe_io  # type: ignore

        safe_io._try_emit_audit_row(
            "front_brain_request_queued",
            {
                "chat_key_hash": item["chat_key_hash"],
                "request_kind": kind,
                "request_preview": item["request_text"][:280],
                "queue_size": queue_size,
            },
        )
    except Exception as e:
        _stderr(
            f"front_brain_queue: audit failed (non-fatal): "
            f"{type(e).__name__}: {str(e)[:160]}"
        )

    return item
