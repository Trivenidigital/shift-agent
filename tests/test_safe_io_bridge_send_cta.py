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


@patch("urllib.request.urlopen")
def test_bridge_send_cta_posts_labels_and_reply_messages_to_send_cta(urlopen, safe_io_module, monkeypatch):
    monkeypatch.setattr(safe_io_module, "BRIDGE_URL", "http://127.0.0.1:3000/send")

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
    )

    assert ok is True
    assert mid == "wamid.cta.1"
    assert err == ""
    assert status == "sent"
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:3000/send-cta"
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
    monkeypatch.setattr(safe_io_module, "BRIDGE_URL", "http://127.0.0.1:3000/send")

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json"
    urlopen.return_value.__enter__.return_value = mock_resp

    ok, mid, err, status = safe_io_module.bridge_send_cta(
        "jid",
        body="Start Free Trial",
        buttons=[{"label": "Start Free Trial", "message": "Help me create a beautiful flyer for my business"}],
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
