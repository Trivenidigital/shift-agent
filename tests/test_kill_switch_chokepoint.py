"""M4/G6 — the operator kill switch is honored at the send chokepoint.

`shift-agent-disable` touches state/disabled.flag. Before M4 that flag was read
by exactly three legacy scripts and by nothing in safe_io, so every catering /
flyer / cf-router / gateway send walked straight past it. These tests pin the
new behavior at all four send sites, plus the two properties that make a kill
switch a kill switch: NO exemptions, and the operator gets paged (once) when
traffic keeps arriving.

Mirrors test_automation_control_bridge_post: safe_io imports fcntl at module
top, so `ensure_fcntl_stub` makes the module importable off-Linux.
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

CUSTOMER = "15550100777@c.us"
OWNER = "19045550100@c.us"


def _ctx():
    """Non-regulated context so the PR-ζ policy admits the send."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="kill-switch-test", is_regulated_action=False,
        verified_action_result=False,
    )


@pytest.fixture
def safe_io_module():
    # Order-determinism: an earlier suite's loader may have popped "safe_io"
    # from sys.modules and re-imported it as a FRESH object (the cf-router
    # flyer-routing / front-brain cohort loaders do this on Linux, where this
    # file actually runs). reload() requires the module object to be the one
    # registered in sys.modules, so re-resolve the LIVE object first — the
    # same defence test_front_brain_outbound_enforcement.py uses.
    global safe_io
    live = sys.modules.get("safe_io")
    if live is None:
        live = importlib.import_module("safe_io")
    safe_io = live
    importlib.reload(live)
    return live


@pytest.fixture
def pages(monkeypatch, safe_io_module):
    """Capture operator pages instead of shelling out to the Pushover bin, and
    reset the once/hour in-process throttle so each test starts un-throttled."""
    captured: list = []
    monkeypatch.setattr(
        safe_io_module, "notify_owner_with_fallback",
        lambda title, message, **kw: (captured.append((title, message, kw)), True)[1],
    )
    safe_io_module._last_disabled_alert_monotonic = 0.0
    yield captured
    safe_io_module._last_disabled_alert_monotonic = 0.0


@pytest.fixture
def flag_dir(tmp_path, monkeypatch):
    """Point the kill-switch flag at a tmp path (the same SHIFT_AGENT_DISABLED_FLAG
    override send-daily-brief / eod-reconcile already use) and get past the
    pytest bridge guard."""
    flag = tmp_path / "state" / "disabled.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SHIFT_AGENT_DISABLED_FLAG", str(flag))
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    return flag


def _mock_ok_urlopen(urlopen):
    resp = MagicMock()
    resp.read.return_value = b'{"id": "wamid.OK"}'
    urlopen.return_value.__enter__.return_value = resp


def _audit_rows(mod) -> list:
    log = Path(mod._decisions_log_path())
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestFlagPresentDrops:
    @patch("urllib.request.urlopen")
    def test_bridge_post_dropped_with_audit_row(self, urlopen, safe_io_module, flag_dir, pages):
        _mock_ok_urlopen(urlopen)
        flag_dir.write_text("disabled-for-test", encoding="utf-8")

        ok, mid, err, status = safe_io_module.bridge_post(
            CUSTOMER, "a routine reply", action_context=_ctx())

        assert ok is False and mid == ""
        assert status == "disabled"
        assert err == "agent_disabled"
        assert urlopen.call_count == 0  # never reached transport
        rows = _audit_rows(safe_io_module)
        assert any(r["type"] == "outbound_refused_disabled"
                   and r["send_kind"] == "bridge_post"
                   and r["proposal_id"] == ""
                   and r["chat_key_hash"]
                   for r in rows)

    @patch("urllib.request.urlopen")
    def test_media_and_cta_dropped_too(self, urlopen, safe_io_module, flag_dir, pages, tmp_path):
        _mock_ok_urlopen(urlopen)
        flag_dir.write_text("x", encoding="utf-8")
        media = tmp_path / "flyer.png"
        media.write_bytes(b"x")

        assert safe_io_module.bridge_send_media(
            CUSTOMER, str(media), caption="hi", action_context=_ctx())[3] == "disabled"
        assert safe_io_module.bridge_send_cta(
            CUSTOMER, body="pick one", buttons=[{"label": "A", "message": "a"}],
            action_context=_ctx())[3] == "disabled"
        assert urlopen.call_count == 0
        kinds = {r["send_kind"] for r in _audit_rows(safe_io_module)
                 if r["type"] == "outbound_refused_disabled"}
        assert kinds == {"bridge_send_media", "bridge_send_cta"}

    @patch("urllib.request.urlopen")
    def test_no_exemptions_ack_and_owner_directed_also_drop(
        self, urlopen, safe_io_module, flag_dir, pages,
    ):
        """A kill switch some sends walk past is not a kill switch. The
        automation-control deterministic ack and owner-directed sends are both
        exempt from the SUPPRESSION gate; neither is exempt from this one."""
        _mock_ok_urlopen(urlopen)
        flag_dir.write_text("x", encoding="utf-8")

        assert safe_io_module.bridge_post(
            CUSTOMER, "opt-out ack", action_context=_ctx(),
            automation_control_ack=True)[3] == "disabled"
        assert safe_io_module.bridge_post(
            OWNER, "owner card", action_context=_ctx())[3] == "disabled"
        assert urlopen.call_count == 0


class TestFlagAbsentPasses:
    @patch("urllib.request.urlopen")
    def test_no_flag_file_send_proceeds(self, urlopen, safe_io_module, flag_dir, pages):
        _mock_ok_urlopen(urlopen)
        assert not flag_dir.exists()
        ok, _, _, status = safe_io_module.bridge_post(
            CUSTOMER, "hi", action_context=_ctx())
        assert ok is True and status == "sent"
        assert urlopen.call_count == 1
        assert pages == []
        assert not any(r["type"] == "outbound_refused_disabled"
                       for r in _audit_rows(safe_io_module))

    @patch("urllib.request.urlopen")
    def test_stat_error_fails_open(self, urlopen, safe_io_module, flag_dir, pages, monkeypatch):
        """FAIL OPEN, deliberately: the kill switch is an operator ACTION, so a
        flag we cannot stat must never silence a healthy agent. (Contrast the
        automation-control gate, which fails CLOSED once enabled because there
        the state was chosen by the customer.)"""
        _mock_ok_urlopen(urlopen)

        class _Exploding:
            def exists(self):
                raise OSError("EIO: cannot stat")

        monkeypatch.setattr(safe_io_module, "_disabled_flag_path", lambda: _Exploding())
        assert safe_io_module._agent_disabled() is False
        assert safe_io_module.bridge_post(CUSTOMER, "hi", action_context=_ctx())[3] == "sent"
        assert urlopen.call_count == 1


class TestOperatorPagedOnce:
    @patch("urllib.request.urlopen")
    def test_pages_once_per_hour_across_many_refused_sends(
        self, urlopen, safe_io_module, flag_dir, pages,
    ):
        """Traffic still arriving while disabled means something restarted the
        agent or a timer the disable never stopped is running — the operator
        must hear about it, but exactly once, not once per dropped send."""
        _mock_ok_urlopen(urlopen)
        flag_dir.write_text("x", encoding="utf-8")

        for _ in range(6):
            assert safe_io_module.bridge_post(
                CUSTOMER, "auto", action_context=_ctx())[3] == "disabled"

        assert len(pages) == 1
        title, body, kw = pages[0]
        # §12b: stable plain-text title+body so the notify chokepoint's
        # cross-process same-message dedup throttles fresh subprocesses too.
        assert "DISABLED" in title
        assert "shift-agent-enable" in body
        assert kw.get("priority") == 1
        assert kw.get("dedup_window_min") == safe_io_module._DISABLED_ALERT_DEDUP_MIN
        # every drop is still audited, even though only one paged
        assert sum(1 for r in _audit_rows(safe_io_module)
                   if r["type"] == "outbound_refused_disabled") == 6

    @patch("urllib.request.urlopen")
    def test_page_failure_never_breaks_the_drop(
        self, urlopen, safe_io_module, flag_dir, pages, monkeypatch,
    ):
        _mock_ok_urlopen(urlopen)
        flag_dir.write_text("x", encoding="utf-8")

        def _boom(*a, **k):
            raise RuntimeError("pushover down")

        monkeypatch.setattr(safe_io_module, "notify_owner_with_fallback", _boom)
        assert safe_io_module.bridge_post(CUSTOMER, "x", action_context=_ctx())[3] == "disabled"
        assert urlopen.call_count == 0


class TestGatewaySeam:
    def test_gateway_send_refused_with_template(self, safe_io_module, flag_dir, pages):
        """The seam is contractually `-> str` and the adapter relays whatever it
        returns, so the refusal shape here is substituting the safe template —
        never forwarding the composed LLM reply."""
        flag_dir.write_text("x", encoding="utf-8")
        out = safe_io_module.front_brain_screen_gateway_send(
            CUSTOMER, "a composed free-form LLM reply")
        assert out == safe_io_module.FRONT_BRAIN_SAFE_GENERIC_ACK
        assert any(r["type"] == "outbound_refused_disabled"
                   and r["send_kind"] == "gateway_send"
                   for r in _audit_rows(safe_io_module))
        assert len(pages) == 1

    def test_gateway_send_uses_caller_fallback_template(self, safe_io_module, flag_dir, pages):
        flag_dir.write_text("x", encoding="utf-8")
        out = safe_io_module.front_brain_screen_gateway_send(
            CUSTOMER, "composed", fallback_template="We'll be with you shortly.")
        assert out == "We'll be with you shortly."

    def test_gateway_send_untouched_when_flag_absent(self, safe_io_module, flag_dir, pages):
        assert not flag_dir.exists()
        out = safe_io_module.front_brain_screen_gateway_send(CUSTOMER, "composed reply")
        assert out == "composed reply"
        assert _audit_rows(safe_io_module) == []
        assert pages == []
