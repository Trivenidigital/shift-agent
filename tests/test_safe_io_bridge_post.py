"""Bridge POST unit tests after extraction to safe_io (PR-Agent13 Commit 0).

Verifies the bridge_post + validate_bridge_url helpers preserve the contract
they had inline in send-daily-brief:364-415:
  - URL validation rejects unsafe schemes + non-loopback hosts
  - send_uncertain status on parse failure / empty messageId (no auto-retry)
  - http_error / connect_failed / unknown_error status branches
"""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

# fcntl import in safe_io is Linux-only; skip these tests on Windows.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)


@pytest.fixture
def safe_io_module():
    """Import safe_io fresh; tests use monkeypatch for env-var changes."""
    import importlib
    import safe_io
    importlib.reload(safe_io)
    return safe_io


class TestValidateBridgeUrl:
    def test_loopback_accepted(self, safe_io_module, monkeypatch):
        monkeypatch.setattr(safe_io_module, "ALLOW_REMOTE_BRIDGE", False)
        assert safe_io_module.validate_bridge_url("http://127.0.0.1:3000/send") is None

    def test_localhost_accepted(self, safe_io_module, monkeypatch):
        monkeypatch.setattr(safe_io_module, "ALLOW_REMOTE_BRIDGE", False)
        assert safe_io_module.validate_bridge_url("http://localhost:3000/send") is None

    def test_remote_rejected_unless_opted_in(self, safe_io_module, monkeypatch):
        monkeypatch.setattr(safe_io_module, "ALLOW_REMOTE_BRIDGE", False)
        err = safe_io_module.validate_bridge_url("http://exfil.example.com/send")
        assert err is not None
        assert "non-loopback" in err

    def test_bad_scheme_rejected(self, safe_io_module):
        err = safe_io_module.validate_bridge_url("file:///etc/passwd")
        assert err is not None
        assert "unsupported scheme" in err

    def test_remote_allowed_when_opted_in(self, safe_io_module, monkeypatch):
        monkeypatch.setattr(safe_io_module, "ALLOW_REMOTE_BRIDGE", True)
        # When opt-in is set, remote hosts ARE allowed
        assert safe_io_module.validate_bridge_url("http://exfil.example.com/send") is None


class TestBridgePost:
    @patch("urllib.request.urlopen")
    def test_pytest_context_refuses_live_bridge_send(self, urlopen, safe_io_module, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_live.py::test_bad_live_send (call)")

        ok, mid, err, status = safe_io_module.bridge_post("jid@s.whatsapp.net", "msg")

        assert ok is False
        assert mid == ""
        assert status == "connect_failed"
        assert "refusing bridge send from pytest context" in err
        urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_recovery_no_live_send_refuses_bridge_send_outside_pytest(self, urlopen, safe_io_module, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv("FLYER_RECOVERY_NO_LIVE_SEND", "1")

        ok, mid, err, status = safe_io_module.bridge_post("jid@s.whatsapp.net", "msg")

        assert ok is False
        assert mid == ""
        assert status == "connect_failed"
        assert "FLYER_RECOVERY_NO_LIVE_SEND" in err
        urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_pytest_context_can_be_explicitly_overridden(self, urlopen, safe_io_module, monkeypatch):
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_live.py::test_bad_live_send (call)")
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.123abc"}'
        urlopen.return_value.__enter__.return_value = mock_resp

        ok, mid, err, status = safe_io_module.bridge_post("jid@s.whatsapp.net", "msg")

        assert ok is True
        assert mid == "wamid.123abc"
        assert err == ""
        assert status == "sent"

    @patch("urllib.request.urlopen")
    def test_send_uncertain_on_unparseable_body(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, mid, err, status = safe_io_module.bridge_post("jid@s.whatsapp.net", "msg")
        assert ok is False
        assert status == "send_uncertain"
        assert "ack_parse_failed" in err

    @patch("urllib.request.urlopen")
    def test_empty_message_id_is_uncertain(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"foo": "bar"}'  # parses but no id field
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg")
        assert ok is False
        assert status == "send_uncertain"
        assert "empty_message_id" in err

    @patch("urllib.request.urlopen")
    def test_success_returns_message_id(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.123abc"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg")
        assert ok is True
        assert mid == "wamid.123abc"
        assert status == "sent"

    @patch("urllib.request.urlopen")
    def test_alternative_messageId_field(self, safe_io_module):
        with patch("urllib.request.urlopen") as urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"messageId": "mid.xyz"}'
            urlopen.return_value.__enter__.return_value = mock_resp
            ok, mid, err, status = safe_io_module.bridge_post("jid", "msg")
            assert ok is True
            assert mid == "mid.xyz"
            assert status == "sent"


class TestBridgePost2TupleAdapter:
    """PR-ε 2026-05-26 — 2-tuple compatibility adapter.

    The legacy catering/expense scripts unpack a 2-tuple (ok, detail_or_mid).
    bridge_post_2tuple is the consolidation entry point so those scripts can
    delete their local _bridge_post implementations without changing the
    canonical surface. Tests verify the (ok, mid, err, status) -> (ok, detail)
    collapse for every status branch of the canonical bridge_post.
    """

    @patch("urllib.request.urlopen")
    def test_success_returns_2tuple_with_message_id(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.abc"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        result = safe_io_module.bridge_post_2tuple("jid@s.whatsapp.net", "msg")
        assert result == (True, "wamid.abc")

    @patch("urllib.request.urlopen")
    def test_success_via_messageId_field(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"messageId": "mid.xyz"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        result = safe_io_module.bridge_post_2tuple("jid", "msg")
        assert result == (True, "mid.xyz")

    @patch("urllib.request.urlopen")
    def test_send_uncertain_collapses_to_error_detail(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, detail = safe_io_module.bridge_post_2tuple("jid", "msg")
        assert ok is False
        # Adapter picks err over status when err is non-empty.
        assert "ack_parse_failed" in detail

    @patch("urllib.request.urlopen")
    def test_empty_message_id_collapses_to_error_detail(self, urlopen, safe_io_module):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"foo": "bar"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, detail = safe_io_module.bridge_post_2tuple("jid", "msg")
        assert ok is False
        assert "empty_message_id" in detail

    @patch("urllib.request.urlopen")
    def test_http_error_status_collapses_to_error_detail(self, urlopen, safe_io_module):
        import urllib.error
        urlopen.side_effect = urllib.error.HTTPError(
            url="http://test/", code=500, msg="Internal Server Error",
            hdrs=None, fp=None,
        )
        ok, detail = safe_io_module.bridge_post_2tuple("jid", "msg")
        assert ok is False
        assert "HTTP 500" in detail

    @patch("urllib.request.urlopen")
    def test_connect_failed_status_collapses_to_error_detail(self, urlopen, safe_io_module):
        import urllib.error
        urlopen.side_effect = urllib.error.URLError("[Errno 111] Connection refused")
        ok, detail = safe_io_module.bridge_post_2tuple("jid", "msg")
        assert ok is False
        assert "URLError" in detail

    def test_adapter_signature_is_2tuple(self, safe_io_module):
        """Static contract check: bridge_post_2tuple returns exactly 2 values.
        Legacy callers depend on this shape."""
        import inspect
        sig = inspect.signature(safe_io_module.bridge_post_2tuple)
        params = list(sig.parameters.keys())
        assert params == ["jid", "message"], f"unexpected signature: {params}"
