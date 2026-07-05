"""Raw-body diagnostic capture (Phase B, quoted-APPROVE prerequisite):
the cf-router entry records what the bridge delivers — body head, length,
populated event attrs, and quote/reply/context-shaped attr heads — so
reply-stripping is designed against real shapes. Best-effort contract:
emitter failures never raise into the dispatch flow."""
from __future__ import annotations

import importlib.util
import sys as _sys

import pytest

pytestmark = pytest.mark.skipif(
    _sys.platform == "win32",
    reason="actions.audit_raw_body imports safe_io (fcntl-only); runs on Linux CI",
)
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src" / "platform"))

spec = importlib.util.spec_from_file_location(
    "cf_actions", REPO / "src" / "plugins" / "cf-router" / "actions.py")
cf_actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cf_actions)


def _capture(monkeypatch, tmp_path, event, text="APPROVE"):
    rows = []
    import safe_io
    monkeypatch.setattr(cf_actions, "LOG_PATH", tmp_path / "decisions.log")
    real_append = safe_io.ndjson_append
    import json as _json
    monkeypatch.setattr(safe_io, "ndjson_append",
                        lambda p, e: rows.append(_json.loads(e) if isinstance(e, str) else e) or real_append(p, e))
    cf_actions.audit_raw_body(event, "123@lid", "wamid.X1", text)
    return rows


def test_quote_shaped_attrs_captured(monkeypatch, tmp_path):
    event = SimpleNamespace(
        text="APPROVE", chat_id="123@lid",
        quotedMessage="Reply APPROVE to receive final files, or reply with changes.",
        contextInfo={"stanzaId": "wamid.PREV"},
    )
    rows = _capture(monkeypatch, tmp_path, event)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "cf_router_raw_body"
    assert row["body_head"] == "APPROVE" and row["body_len"] == 7
    assert "quotedMessage" in row["quote_attrs"]
    assert "contextInfo" in row["quote_attrs"]
    assert row["quote_attrs"]["quotedMessage"].startswith("Reply APPROVE")


def test_schema_round_trip(monkeypatch, tmp_path):
    event = SimpleNamespace(text="hello", chat_id="c")
    _capture(monkeypatch, tmp_path, event, text="hello")
    from pydantic import TypeAdapter

    from schemas import LogEntry
    adapter = TypeAdapter(LogEntry)
    line = (tmp_path / "decisions.log").read_text(encoding="utf-8").strip()
    entry = adapter.validate_python(json.loads(line))
    assert entry.type == "cf_router_raw_body"
    assert entry.body_head == "hello"


def test_emitter_never_raises(monkeypatch, tmp_path):
    class Hostile:
        def __getattr__(self, name):
            raise RuntimeError("boom")
    # must not raise into the dispatch flow (best-effort contract)
    cf_actions.audit_raw_body(Hostile(), "c", "m", "t")


def test_body_head_capped(monkeypatch, tmp_path):
    rows = _capture(monkeypatch, tmp_path, SimpleNamespace(text="x"), text="A" * 999)
    assert rows[0]["body_len"] == 999 and len(rows[0]["body_head"]) == 400


def test_raw_message_structure_captured(monkeypatch, tmp_path):
    # Probe-2 evidence (2026-07-05): quote metadata rides in raw_message, not
    # a quote-named attr — the capture must record its head.
    event = SimpleNamespace(text="APPROVE",
                            raw_message={"key": {"id": "X"}, "contextInfo": {"stanzaId": "PREV"}})
    rows = _capture(monkeypatch, tmp_path, event)
    assert "raw_message" in rows[0]["quote_attrs"]
    assert "stanzaId" in rows[0]["quote_attrs"]["raw_message"]
