"""Unit tests for the Hermes bridge /send-cta helper."""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)


@pytest.fixture
def safe_io_module():
    import importlib
    import safe_io
    importlib.reload(safe_io)
    return safe_io


# send-path-test-harness 2026-05-30: a NON-live loopback bridge URL (never :3000)
# so the LiveBridgeSendInTestError tripwire stays dormant; urlopen is mocked so
# nothing is actually sent.
_TEST_BRIDGE = "http://127.0.0.1:8765/send"


def _ctx():
    """Minimal non-regulated ActionExecutionContext so the chokepoint allows the
    send (in-process caller isn't an allowlisted script basename)."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="cta-unit-test", is_regulated_action=False,
        verified_action_result=False,
    )


@patch("urllib.request.urlopen")
def test_bridge_send_cta_posts_labels_and_reply_messages_to_send_cta(urlopen, safe_io_module, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_module, "BRIDGE_URL", _TEST_BRIDGE)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"success": true, "messageId": "wamid.cta.1"}'
    urlopen.return_value.__enter__.return_value = mock_resp

    ok, mid, err, status = safe_io_module.bridge_send_cta(
        "customer@s.whatsapp.net",
        body="Create beautiful marketing material for your business.",
        buttons=[
            {
                "label": "Start Free Trial",
                "message": "Help me create a beautiful flyer for my business",
            },
            {
                "label": "Act Now! Save Time and Money",
                "message": "I want to set up Flyer Studio for my business",
            },
        ],
        media_path="/opt/shift-agent/state/flyer/marketing/Flyer.png",
        media_type="image",
        footer="Flyer Studio",
        action_context=_ctx(),
    )

    assert ok is True
    assert mid == "wamid.cta.1"
    assert err == ""
    assert status == "sent"
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8765/send-cta"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {
        "chatId": "customer@s.whatsapp.net",
        "body": "Create beautiful marketing material for your business.",
        "buttons": [
            {
                "label": "Start Free Trial",
                "message": "Help me create a beautiful flyer for my business",
            },
            {
                "label": "Act Now! Save Time and Money",
                "message": "I want to set up Flyer Studio for my business",
            },
        ],
        "mediaPath": "/opt/shift-agent/state/flyer/marketing/Flyer.png",
        "mediaType": "image",
        "footer": "Flyer Studio",
    }


@patch("urllib.request.urlopen")
def test_bridge_send_cta_unparseable_ack_is_uncertain(urlopen, safe_io_module, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_module, "BRIDGE_URL", _TEST_BRIDGE)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json"
    urlopen.return_value.__enter__.return_value = mock_resp

    ok, mid, err, status = safe_io_module.bridge_send_cta(
        "jid",
        body="Start Free Trial",
        buttons=[{"label": "Start Free Trial", "message": "Help me create a beautiful flyer for my business"}],
        action_context=_ctx(),
    )
    assert ok is False
    assert mid == ""
    assert status == "send_uncertain"
    assert "ack_parse_failed" in err


def test_bridge_send_cta_rejects_empty_buttons(safe_io_module):
    ok, mid, err, status = safe_io_module.bridge_send_cta(
        "jid",
        body="Start Free Trial",
        buttons=[],
    )
    assert ok is False
    assert mid == ""
    assert status == "invalid_payload"
    assert "at least one CTA button" in err
