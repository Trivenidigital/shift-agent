"""Unit tests for the Hermes bridge /send-media helper."""
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


# send-path-test-harness 2026-05-30: NON-live loopback bridge URL (never :3000)
# so the LiveBridgeSendInTestError tripwire stays dormant; urlopen is mocked.
_TEST_BRIDGE = "http://127.0.0.1:8765/send"


def _ctx():
    """Minimal non-regulated ActionExecutionContext so the chokepoint allows the
    send (in-process caller isn't an allowlisted script basename)."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="media-unit-test", is_regulated_action=False,
        verified_action_result=False,
    )


@patch("urllib.request.urlopen")
def test_bridge_send_media_posts_to_send_media_endpoint(urlopen, safe_io_module, tmp_path, monkeypatch):
    asset = tmp_path / "flyer.png"
    asset.write_bytes(b"fake-png")
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_module, "BRIDGE_URL", _TEST_BRIDGE)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"success": true, "messageId": "wamid.media.1"}'
    urlopen.return_value.__enter__.return_value = mock_resp

    ok, mid, err, status = safe_io_module.bridge_send_media(
        "customer@s.whatsapp.net",
        asset,
        media_type="image",
        caption="Draft concept 1",
        file_name="concept-1.png",
        action_context=_ctx(),
    )

    assert ok is True
    assert mid == "wamid.media.1"
    assert status == "sent"
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8765/send-media"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {
        "chatId": "customer@s.whatsapp.net",
        "filePath": str(asset),
        "mediaType": "image",
        "caption": "Draft concept 1",
        "fileName": "concept-1.png",
    }


def test_bridge_send_media_rejects_missing_file(safe_io_module, tmp_path):
    ok, mid, err, status = safe_io_module.bridge_send_media(
        "customer@s.whatsapp.net",
        tmp_path / "missing.png",
    )
    assert ok is False
    assert mid == ""
    assert status == "missing_file"
    assert "missing media file" in err


@patch("urllib.request.urlopen")
def test_bridge_send_media_unparseable_ack_is_uncertain(urlopen, safe_io_module, tmp_path, monkeypatch):
    asset = tmp_path / "flyer.pdf"
    asset.write_bytes(b"%PDF fake")
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io_module, "BRIDGE_URL", _TEST_BRIDGE)

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json"
    urlopen.return_value.__enter__.return_value = mock_resp

    ok, mid, err, status = safe_io_module.bridge_send_media("jid", asset, action_context=_ctx())
    assert ok is False
    assert mid == ""
    assert status == "send_uncertain"
    assert "ack_parse_failed" in err
