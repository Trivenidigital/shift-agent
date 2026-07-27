"""PR-5 automation-control — bridge_post OUTBOUND enforcement point (§5.4/§5.5).

The last line before transport drops an automated CUSTOMER send toward a
suppressed conversation (opted_out / paused-unexpired / takeover) — the
defense-in-depth twin of the cf-router inbound check that catches queued
follow-ups / the no-response sweep. EXEMPT: the deterministic ack
(automation_control_ack=True) and owner-directed sends (keyed to a non-suppressed
identity). Gated default-OFF behind CATERING_AUTOMATION_CONTROL_ENABLED —
flag-off is byte-identical to pre-kernel bridge_post.

Mirrors test_bridge_post_throttle: safe_io imports fcntl at module top, so
`ensure_fcntl_stub` makes the module importable off-Linux.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform", REPO / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402
import schemas  # noqa: E402
import automation_control as ac  # noqa: E402

CUSTOMER = "loop@c.us"
OWNER = "owner@c.us"


def _ctx():
    """Non-regulated context so the PR-ζ policy admits the send."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="automation-control-test", is_regulated_action=False,
        verified_action_result=False,
    )


@pytest.fixture
def safe_io_module():
    importlib.reload(safe_io)
    return safe_io


@pytest.fixture
def kernel_env(tmp_path, monkeypatch, safe_io_module):
    """Master flag on, wildcard allowlist, tmp state file, past the bridge guard."""
    state = tmp_path / "catering-automation-control.json"
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(state))
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    ac._LAST_KNOWN_MODE.clear()
    yield state
    ac._LAST_KNOWN_MODE.clear()


def _mock_ok_urlopen(urlopen):
    resp = MagicMock()
    resp.read.return_value = b'{"id": "wamid.OK"}'
    urlopen.return_value.__enter__.return_value = resp


class TestOutboundSuppression:
    @patch("urllib.request.urlopen")
    def test_opted_out_customer_send_dropped(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        emit = []
        monkeypatch.setattr(ac, "_emit", lambda t, f: emit.append((t, f)))
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")

        ok, mid, err, status = safe_io_module.bridge_post(
            CUSTOMER, "an automated follow-up", action_context=_ctx())
        assert ok is False and mid == ""
        assert status == "suppressed"
        assert err == "automation_suppressed:opted_out"
        assert urlopen.call_count == 0  # never reached transport
        assert any(t == "catering_automated_send_suppressed"
                   and f["mode"] == "opted_out"
                   and f["suppressed_send_kind"] == "outbound_send"
                   for t, f in emit)

    @patch("urllib.request.urlopen")
    def test_takeover_customer_send_dropped(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        ok, _, err, status = safe_io_module.bridge_post(CUSTOMER, "auto reply", action_context=_ctx())
        assert status == "suppressed" and err == "automation_suppressed:takeover"
        assert urlopen.call_count == 0

    @patch("urllib.request.urlopen")
    def test_paused_unexpired_dropped_expired_passes(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        ac.set_mode(CUSTOMER, "paused", actor="customer", reason="customer_pause", pause_seconds=3600)
        assert safe_io_module.bridge_post(CUSTOMER, "x", action_context=_ctx())[3] == "suppressed"
        # Expire the pause → the send now goes out.
        doc = json.loads(kernel_env.read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "2000-01-01T00:00:00+00:00"
        kernel_env.write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()
        assert safe_io_module.bridge_post(CUSTOMER, "x", action_context=_ctx())[3] == "sent"

    @patch("urllib.request.urlopen")
    def test_deterministic_ack_is_exempt(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        ok, mid, err, status = safe_io_module.bridge_post(
            CUSTOMER, "You won't receive further automated messages.",
            action_context=_ctx(), automation_control_ack=True)
        assert ok is True and status == "sent"
        assert urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_owner_directed_send_exempt_during_customer_takeover(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        """A send TO the owner keys to the owner's (unsuppressed) identity, so it
        goes out even while a customer is in takeover."""
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        assert safe_io_module.bridge_post(OWNER, "FYI update", action_context=_ctx())[3] == "sent"

    @patch("urllib.request.urlopen")
    def test_active_conversation_passes(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        assert safe_io_module.bridge_post(CUSTOMER, "hi", action_context=_ctx())[3] == "sent"


class TestFlagOffByteIdentical:
    @patch("urllib.request.urlopen")
    def test_flag_off_no_state_read_send_proceeds(self, urlopen, tmp_path, safe_io_module, monkeypatch):
        """Master flag unset → the kernel is inert: even an on-disk opted_out
        record is never consulted (no import, no state read) and the send goes
        out — byte-identical to pre-kernel bridge_post."""
        _mock_ok_urlopen(urlopen)
        state = tmp_path / "off-state.json"
        # Pre-seed an opted_out record, then DISABLE the master flag.
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(state))
        ac._LAST_KNOWN_MODE.clear()
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        monkeypatch.delenv("CATERING_AUTOMATION_CONTROL_ENABLED", raising=False)
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")

        ok, _, _, status = safe_io_module.bridge_post(CUSTOMER, "hi", action_context=_ctx())
        assert ok is True and status == "sent"
        assert urlopen.call_count == 1


class TestInvariantNoAutomatedSendWhileSuppressed:
    @patch("urllib.request.urlopen")
    def test_no_automated_customer_send_emitted_while_suppressed(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        """Invariant: for opted_out and takeover, EVERY non-ack automated customer
        send is dropped (none reach transport)."""
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        for mode in ("opted_out", "takeover"):
            ac.set_mode(CUSTOMER, mode, actor="customer", reason="x")
            ac._LAST_KNOWN_MODE.clear()
            for _ in range(4):
                ok, _, _, status = safe_io_module.bridge_post(
                    CUSTOMER, "auto", action_context=_ctx())
                assert ok is False and status == "suppressed"
        assert urlopen.call_count == 0


class TestOutboundFailClosedOnReadError:
    @patch("urllib.request.urlopen")
    def test_enabled_read_error_fails_closed(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        """must-fix a: ENABLED for this jid + a non-ok state read (cold subprocess
        on the sweep / owner-approval quote path) → DROP, never default-active."""
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        # No suppressed record on disk (would read 'active'), but the read faults.
        monkeypatch.setattr(ac, "read_mode_and_status", lambda jid: ("active", "oserror:boom"))
        ok, mid, err, status = safe_io_module.bridge_post(
            CUSTOMER, "no-response sweep follow-up", action_context=_ctx())
        assert ok is False and status == "suppressed"
        assert err == "automation_suppressed:read_error"
        assert urlopen.call_count == 0

    @patch("urllib.request.urlopen")
    def test_read_error_row_validates_with_read_error_mode(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "read_mode_and_status", lambda jid: ("active", "corrupt:x"))
        safe_io_module.bridge_post(CUSTOMER, "x", action_context=_ctx())
        log = Path(safe_io_module._decisions_log_path())
        rows = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
        assert any(r["type"] == "catering_automated_send_suppressed"
                   and r["mode"] == "read_error" for r in rows)

    @patch("urllib.request.urlopen")
    def test_clean_active_read_still_sends(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        """Fail-open is preserved for the inert case: a CLEAN active read sends."""
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "read_mode_and_status", lambda jid: ("active", "ok"))
        assert safe_io_module.bridge_post(CUSTOMER, "hi", action_context=_ctx())[3] == "sent"


class TestMediaCtaSymmetry:
    @patch("urllib.request.urlopen")
    def test_media_send_suppressed(self, urlopen, safe_io_module, kernel_env, monkeypatch, tmp_path):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        f = tmp_path / "flyer.png"
        f.write_bytes(b"x")
        ok, mid, err, status = safe_io_module.bridge_send_media(
            CUSTOMER, str(f), caption="hi", action_context=_ctx())
        assert ok is False and status == "suppressed"
        assert urlopen.call_count == 0

    @patch("urllib.request.urlopen")
    def test_cta_send_suppressed(self, urlopen, safe_io_module, kernel_env, monkeypatch):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(ac, "_emit", lambda t, f: None)
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        ok, mid, err, status = safe_io_module.bridge_send_cta(
            CUSTOMER, body="pick one", buttons=[{"label": "A", "message": "a"}],
            action_context=_ctx())
        assert ok is False and status == "suppressed"
        assert urlopen.call_count == 0


class TestSchemaAdapter:
    def test_suppressed_row_validates_through_real_adapter(self, safe_io_module):
        safe_io_module._try_emit_audit_row(
            "catering_automated_send_suppressed",
            {"chat_key_hash": "h", "mode": "opted_out",
             "suppressed_send_kind": "outbound_send", "logical_turn_id": ""},
        )
        log = Path(safe_io_module._decisions_log_path())
        rows = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
        assert any(r["type"] == "catering_automated_send_suppressed" for r in rows)
