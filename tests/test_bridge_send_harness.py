"""Regression coverage for the send-path test harness (send-path-test-harness 2026-05-30).

Proves no pytest send can reach the live WhatsApp bridge (:3000):
  - the autouse fake-sink default points BRIDGE_URL away from :3000;
  - safe_io's LiveBridgeSendInTestError tripwire RAISES on a :3000 send
    (bridge_post / bridge_send_cta / bridge_send_media) even WITH the test
    opt-in env set — so a misconfigured test fails loud instead of leaking;
  - the refuse-by-default pytest guard is unchanged (NOT weakened);
  - a legitimate ephemeral-port loopback stub still works under opt-in, using a
    real ActionExecutionContext (the in-process point-4 pattern).

Every test here is safe to run anywhere: the tripwire tests raise BEFORE any
network call, the refuse test sends nothing, and the one real send targets an
ephemeral self-stub (never the live bridge).
"""
from __future__ import annotations

import json
import platform
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)

PLATFORM_DIR = Path(__file__).resolve().parent.parent / "src" / "platform"
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

LIVE_BRIDGE_URL = "http://127.0.0.1:3000/send"


@pytest.fixture
def safe_io_mod():
    import importlib
    import safe_io
    importlib.reload(safe_io)
    return safe_io


def _ctx(*, regulated: bool = False, verified: bool = False):
    """A minimal non-regulated ActionExecutionContext (point-4 pattern)."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="send-path-harness-test",
        is_regulated_action=regulated,
        verified_action_result=verified,
    )


class _Stub(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            _Stub.requests.append(json.loads(raw.decode("utf-8")))
        except Exception:
            _Stub.requests.append({"_raw": raw.decode("utf-8", "replace")})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"id": "harness.msg.1"}')

    def log_message(self, *a):  # silence test server
        pass


@pytest.fixture
def stub_server():
    _Stub.requests = []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    port = srv.server_address[1]
    assert port != 3000  # ephemeral port must never collide with live bridge
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield port, _Stub
    finally:
        srv.shutdown()


# ── default fake sink (point 2) ─────────────────────────────────────────────

def test_default_bridge_url_is_fake_sink_not_live(safe_io_mod):
    """The autouse fixture must point BRIDGE_URL away from the live bridge."""
    assert "3000" not in safe_io_mod.BRIDGE_URL
    assert safe_io_mod._is_live_bridge_url(safe_io_mod.BRIDGE_URL) is False


# ── classifier unit ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:3000/send", True),
    ("http://127.0.0.1:3000/send-cta", True),
    ("http://localhost:3000/send-media", True),
    ("http://127.0.0.1:1/__fake_test_sink__", False),
    ("http://127.0.0.1:54321/send", False),
    (None, False),
    ("", False),
])
def test_is_live_bridge_url_classifier(safe_io_mod, url, expected):
    assert safe_io_mod._is_live_bridge_url(url) is expected


# ── tripwire: live-bridge send under opt-in RAISES (points 3, 6) ────────────

def test_tripwire_raises_on_live_bridge_post(safe_io_mod, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_mod, "BRIDGE_URL", LIVE_BRIDGE_URL)
    with pytest.raises(safe_io_mod.LiveBridgeSendInTestError):
        safe_io_mod.bridge_post(
            "19999999999@s.whatsapp.net", "x", action_context=_ctx())


def test_tripwire_raises_on_live_bridge_cta(safe_io_mod, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_mod, "BRIDGE_URL", LIVE_BRIDGE_URL)
    with pytest.raises(safe_io_mod.LiveBridgeSendInTestError):
        safe_io_mod.bridge_send_cta(
            "19999999999@s.whatsapp.net",
            body="hi",
            buttons=[{"label": "Yes", "message": "yes"}],
            action_context=_ctx())


def test_tripwire_raises_on_live_bridge_media(safe_io_mod, monkeypatch, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("data", encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_mod, "BRIDGE_URL", LIVE_BRIDGE_URL)
    with pytest.raises(safe_io_mod.LiveBridgeSendInTestError):
        safe_io_mod.bridge_send_media(
            "19999999999@s.whatsapp.net", str(f),
            caption="hi", action_context=_ctx())


# ── guard NOT weakened: refuse-by-default still holds (point 5) ─────────────

def test_guard_still_refuses_by_default(safe_io_mod, monkeypatch):
    """No opt-in env → pytest send refused (string, NOT raise), no network."""
    monkeypatch.delenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", raising=False)
    monkeypatch.setattr(safe_io_mod, "BRIDGE_URL", LIVE_BRIDGE_URL)
    ok, _mid, err, status = safe_io_mod.bridge_post(
        "19999999999@s.whatsapp.net", "x", action_context=_ctx())
    assert ok is False
    assert err == "refusing bridge send from pytest context"
    assert status == "connect_failed"


def test_guard_direct_call_no_optin_returns_string_not_raise(safe_io_mod, monkeypatch):
    monkeypatch.delenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", raising=False)
    assert safe_io_mod.bridge_send_blocked_by_test_context(LIVE_BRIDGE_URL) == \
        "refusing bridge send from pytest context"


# ── legit ephemeral-port stub permitted under opt-in (point 4 pattern) ──────

def test_legit_loopback_stub_permitted_with_action_context(safe_io_mod, monkeypatch, stub_server):
    port, stub = stub_server
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_mod, "BRIDGE_URL", f"http://127.0.0.1:{port}/send")
    ok, mid, err, status = safe_io_mod.bridge_post(
        "19999999999@s.whatsapp.net", "hello", action_context=_ctx())
    assert ok is True, f"err={err!r} status={status!r}"
    assert status == "sent"
    assert mid == "harness.msg.1"
    assert len(stub.requests) == 1
    assert stub.requests[0]["chatId"] == "19999999999@s.whatsapp.net"
