"""Per-conversation bridge_post send throttle (2026-07-21 incident limiter).

The throttle drops a send once a single conversation exceeds its per-window
ceiling (default 5 sends / 10 min), records a `conversation_send_throttle_breach`
suppression audit row, and pages the operator (§12b) at the breach site. It is
gated default-OFF behind BRIDGE_CONVERSATION_THROTTLE_ENABLED — flag-off is
byte-identical to pre-throttle bridge_post.

Split into two tiers:
  - Pure window-math (`conversation_throttle_decision`) — no I/O, runs anywhere.
  - bridge_post integration — exercises the FileLock-backed reserve + the drop /
    alert / audit path. safe_io imports fcntl at module top; `ensure_fcntl_stub`
    (a no-op advisory-lock stub, sound in a single test process) makes the whole
    module importable off-Linux so these assert on Windows too.
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


@pytest.fixture
def safe_io_module():
    """Import safe_io fresh; env changes are applied via monkeypatch/setenv and
    read at call time by the throttle accessors, so no per-test reload of state
    leaks across tests."""
    importlib.reload(safe_io)
    return safe_io


def _ctx():
    """Non-regulated ActionExecutionContext so the PR-ζ policy admits the send
    (an in-process test caller isn't an allowlisted script basename). Mirrors
    test_safe_io_bridge_post._ctx."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="bridge-post-throttle-test",
        is_regulated_action=False,
        verified_action_result=False,
    )


# ── Pure sliding-window math (Windows + Linux) ──────────────────────────────

class TestPureWindowDecision:
    def test_five_in_window_pass_sixth_breaches(self):
        now = 1000.0
        ts: list[float] = []
        for i in range(5):
            allowed, ts = safe_io.conversation_throttle_decision(
                ts, now + i, limit=5, window_sec=600,
            )
            assert allowed is True
        assert len(ts) == 5
        allowed, ts6 = safe_io.conversation_throttle_decision(
            ts, now + 5, limit=5, window_sec=600,
        )
        assert allowed is False
        # The breaching send is NOT recorded — the window can't ratchet.
        assert len(ts6) == 5

    def test_send_after_window_slides_passes_again(self):
        ts = [1000.0, 1001.0, 1002.0, 1003.0, 1004.0]
        # In-window → breach.
        allowed, _ = safe_io.conversation_throttle_decision(
            ts, 1005.0, limit=5, window_sec=600,
        )
        assert allowed is False
        # 601s later the whole window has slid out → a fresh send passes.
        allowed, ts_after = safe_io.conversation_throttle_decision(
            ts, 1606.0, limit=5, window_sec=600,
        )
        assert allowed is True
        assert ts_after == [1606.0]

    def test_partial_eviction_keeps_recent_only(self):
        # Two old (outside 600s), three recent (inside): count=3 < 5 → allow,
        # and the two old ones are pruned from the returned list.
        ts = [100.0, 200.0, 1400.0, 1450.0, 1490.0]
        allowed, kept = safe_io.conversation_throttle_decision(
            ts, 1500.0, limit=5, window_sec=600,
        )
        assert allowed is True
        assert kept == [1400.0, 1450.0, 1490.0, 1500.0]

    def test_timestamp_exactly_at_cutoff_is_evicted(self):
        # t == now - window_sec is OUTSIDE the trailing window (strict >).
        allowed, kept = safe_io.conversation_throttle_decision(
            [400.0], 1000.0, limit=1, window_sec=600,
        )
        assert allowed is True
        assert kept == [1000.0]


# ── bridge_post integration (drop / alert / audit / isolation) ──────────────

@pytest.fixture
def throttle_env(tmp_path, monkeypatch, safe_io_module):
    """Enable the throttle, route its state to a tmp file, and opt past the
    pytest bridge guard. Returns the tmp state-file path."""
    state = tmp_path / "throttle" / "bridge_conversation_throttle.json"
    monkeypatch.setenv("BRIDGE_CONVERSATION_THROTTLE_ENABLED", "1")
    monkeypatch.setenv("BRIDGE_CONVERSATION_THROTTLE_STATE_PATH", str(state))
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    return state


def _mock_ok_urlopen(urlopen):
    resp = MagicMock()
    resp.read.return_value = b'{"id": "wamid.OK"}'
    urlopen.return_value.__enter__.return_value = resp


class TestThrottleIntegration:
    @patch("urllib.request.urlopen")
    def test_sixth_send_dropped_with_throttled_status(
        self, urlopen, safe_io_module, throttle_env, monkeypatch
    ):
        _mock_ok_urlopen(urlopen)
        emit_calls = []
        monkeypatch.setattr(
            safe_io_module, "_emit_audit_row",
            lambda etype, fields: emit_calls.append((etype, fields)),
        )
        alerts = []
        monkeypatch.setattr(
            safe_io_module, "notify_owner_with_fallback",
            lambda *a, **k: alerts.append((a, k)) or True,
        )

        for _ in range(5):
            ok, mid, err, status = safe_io_module.bridge_post(
                "loop@c.us", "hi", action_context=_ctx(),
            )
            assert ok is True and status == "sent"
        assert urlopen.call_count == 5

        ok, mid, err, status = safe_io_module.bridge_post(
            "loop@c.us", "hi again", action_context=_ctx(),
        )
        assert ok is False
        assert mid == ""
        assert status == "throttled"
        assert err == "conversation_send_throttle_breach"
        # Dropped: the bridge was NOT hit a 6th time.
        assert urlopen.call_count == 5
        # Suppression audit row written.
        assert any(e[0] == "conversation_send_throttle_breach" for e in emit_calls)
        breach = next(f for t, f in emit_calls if t == "conversation_send_throttle_breach")
        assert breach["jid"] == "loop@c.us"
        assert breach["window_count"] == 5
        assert breach["limit"] == 5
        assert breach["message_preview"] == "hi again"
        # §12b operator page fired exactly once.
        assert len(alerts) == 1

    @patch("urllib.request.urlopen")
    def test_distinct_conversations_have_independent_budgets(
        self, urlopen, safe_io_module, throttle_env, monkeypatch
    ):
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(safe_io_module, "_emit_audit_row", lambda e, f: None)
        monkeypatch.setattr(
            safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True
        )
        # Five each to two conversations — all admitted (10 sends).
        for _ in range(5):
            assert safe_io_module.bridge_post("alice@c.us", "x", action_context=_ctx())[3] == "sent"
        for _ in range(5):
            assert safe_io_module.bridge_post("bob@c.us", "x", action_context=_ctx())[3] == "sent"
        assert urlopen.call_count == 10
        # Each conversation independently breaches on its own 6th.
        assert safe_io_module.bridge_post("alice@c.us", "x", action_context=_ctx())[3] == "throttled"
        assert safe_io_module.bridge_post("bob@c.us", "x", action_context=_ctx())[3] == "throttled"

    @patch("urllib.request.urlopen")
    def test_flag_off_is_byte_identical_no_state_touched(
        self, urlopen, tmp_path, safe_io_module, monkeypatch
    ):
        """Flag unset → the throttle is inert: >5 sends to one conversation all
        go out and the state file is never created (no counting, no lock)."""
        _mock_ok_urlopen(urlopen)
        state = tmp_path / "throttle-off.json"
        monkeypatch.delenv("BRIDGE_CONVERSATION_THROTTLE_ENABLED", raising=False)
        monkeypatch.setenv("BRIDGE_CONVERSATION_THROTTLE_STATE_PATH", str(state))
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        for _ in range(8):
            ok, _, _, status = safe_io_module.bridge_post(
                "loop@c.us", "hi", action_context=_ctx(),
            )
            assert ok is True and status == "sent"
        assert urlopen.call_count == 8
        assert not state.exists()

    @patch("urllib.request.urlopen")
    def test_throttle_state_io_error_fails_open(
        self, urlopen, safe_io_module, throttle_env, monkeypatch, capsys
    ):
        """A throttle-state write error must ALLOW the send (fail-open) and log —
        opposite of the drop-and-alert breach path, intentionally."""
        _mock_ok_urlopen(urlopen)

        def _boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(safe_io_module, "atomic_write_json", _boom)

        ok, mid, err, status = safe_io_module.bridge_post(
            "loop@c.us", "hi", action_context=_ctx(),
        )
        assert ok is True and status == "sent"
        assert urlopen.call_count == 1
        assert "failing OPEN" in capsys.readouterr().err

    @patch("urllib.request.urlopen")
    def test_regulated_refusal_does_not_consume_budget(
        self, urlopen, safe_io_module, throttle_env, monkeypatch
    ):
        """A regulated hard-refusal returns BEFORE the throttle, so it never
        touches the window: the state file stays absent and the conversation's
        full budget of 5 remains for real sends."""
        _mock_ok_urlopen(urlopen)
        monkeypatch.setattr(safe_io_module, "_emit_audit_row", lambda e, f: None)
        monkeypatch.setattr(
            safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True
        )
        from schemas import ActionExecutionContext
        money_ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True, verified_action_result=False,
            mutation_class="external_irreversible",
        )
        ok, _, err, status = safe_io_module.bridge_post(
            "loop@c.us", "Your payment was processed.", action_context=money_ctx,
        )
        assert ok is False and status == "refused"
        # The throttle never ran → no state file, budget untouched.
        assert not throttle_env.exists()
        # The full 5-send budget is still available.
        for _ in range(5):
            assert safe_io_module.bridge_post("loop@c.us", "x", action_context=_ctx())[3] == "sent"
        assert safe_io_module.bridge_post("loop@c.us", "x", action_context=_ctx())[3] == "throttled"


# ── Schema / enum-guard ─────────────────────────────────────────────────────

class TestThrottleAuditSchema:
    def test_breach_reason_literal_is_a_known_log_entry_type(self):
        assert "conversation_send_throttle_breach" in schemas._KNOWN_LOG_ENTRY_TYPES

    def test_breach_row_validates_through_the_real_adapter(self, safe_io_module):
        """_emit_audit_row builds the LogEntry via the real TypeAdapter; a bad
        literal or field shape would raise ValidationError. Routed to the tmp
        decisions.log (conftest _isolate_audit_log)."""
        safe_io_module._emit_audit_row(
            "conversation_send_throttle_breach",
            {
                "jid": "loop@c.us",
                "caller_script": "test",
                "window_count": 5,
                "limit": 5,
                "window_sec": 600,
                "message_preview": "hi",
            },
        )
        log_path = Path(safe_io_module._decisions_log_path())
        rows = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln.strip()]
        assert any(r["type"] == "conversation_send_throttle_breach" for r in rows)
