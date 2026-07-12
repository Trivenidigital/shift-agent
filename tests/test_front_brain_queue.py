"""Item 5 — front_brain_queue unfulfillable-request store (fcntl-gated).

Durable JSON store + front_brain_request_queued audit variant. The store writes
through safe_io.atomic_write_json, so it inherits the pytest prod-write guard;
safe_io imports fcntl -> Docker python:3.11-slim, not Windows.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="front_brain_queue writes via safe_io (fcntl only)",
)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "platform"))
sys.path.insert(0, str(REPO / "src"))

try:
    import front_brain_queue as fbq  # noqa: E402
    from schemas import LogEntry  # noqa: E402
    from pydantic import TypeAdapter  # noqa: E402

    ADAPTER = TypeAdapter(LogEntry)
except ModuleNotFoundError:  # pragma: no cover - Windows (no fcntl)
    fbq = None  # type: ignore[assignment]
    ADAPTER = None  # type: ignore[assignment]


def _rows() -> list[dict]:
    p = Path(os.environ["SHIFT_AGENT_DECISIONS_LOG_PATH"])
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def qpath(monkeypatch, tmp_path):
    p = tmp_path / "front_brain" / "request_queue.json"
    monkeypatch.setenv("FRONT_BRAIN_REQUEST_QUEUE_PATH", str(p))
    return p


def test_queue_persists_item_and_emits_audit(qpath):
    item = fbq.queue_unfulfillable_request(
        chat_key="+17329837841",
        request_text="Can you make the flyer look festive with diyas?",
        request_kind="theme_change",
    )
    assert item["request_kind"] == "theme_change"
    assert item["chat_key_hash"] and "+1732" not in item["chat_key_hash"]  # hashed
    stored = fbq.load_queue()
    assert len(stored) == 1
    assert stored[0]["request_text"].startswith("Can you make")
    rows = [r for r in _rows() if r["type"] == "front_brain_request_queued"]
    assert len(rows) == 1
    assert rows[0]["request_kind"] == "theme_change"
    assert rows[0]["queue_size"] == 1
    assert rows[0]["chat_key_hash"] == item["chat_key_hash"]
    for r in _rows():
        ADAPTER.validate_python(r)


def test_unknown_kind_normalizes_to_other(qpath):
    item = fbq.queue_unfulfillable_request(
        chat_key="c1", request_text="do a barrel roll", request_kind="wat"
    )
    assert item["request_kind"] == "other"


def test_appends_and_caps_at_max(qpath, monkeypatch):
    monkeypatch.setattr(fbq, "MAX_QUEUE_ITEMS", 3)
    for i in range(5):
        fbq.queue_unfulfillable_request(chat_key="c", request_text=f"req {i}")
    stored = fbq.load_queue()
    assert len(stored) == 3
    # oldest dropped -> newest three remain, in order
    assert [s["request_text"] for s in stored] == ["req 2", "req 3", "req 4"]


def test_request_text_capped(qpath):
    item = fbq.queue_unfulfillable_request(chat_key="c", request_text="y" * 5000)
    assert len(item["request_text"]) == 2000


def test_prod_path_fails_open_under_guard(monkeypatch):
    # No path override -> writes target the deployed tree; the safe_io prod-write
    # guard refuses it under pytest. queue_unfulfillable_request fails OPEN: no
    # raise, item still returned, store not written to the box.
    prod = Path("/opt/shift-agent/state/front_brain/request_queue.json")
    monkeypatch.setenv("FRONT_BRAIN_REQUEST_QUEUE_PATH", str(prod))
    item = fbq.queue_unfulfillable_request(chat_key="c", request_text="hi")
    assert item["request_text"] == "hi"  # caller still gets its ack material
    assert not prod.exists()  # guard blocked the deployed-tree write
