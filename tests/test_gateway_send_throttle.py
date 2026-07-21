"""Per-conversation GATEWAY-SEND throttle (2026-07-21 incident limiter — the
seam the 28-send spiral actually ran on).

Sibling of tests/test_bridge_post_throttle.py, but the throttled seam is
`front_brain_screen_gateway_send` (the platform-adapter egress screen for LLM
free-form replies), NOT bridge_post. On breach the gateway seam SUBSTITUTES the
safe fallback template — its established suppression shape, since the seam always
relays the string it returns (see the ARCHITECTURAL NOTE in safe_io) — rather
than dropping to silence, records a DISTINCT `gateway_send_throttle_breach`
suppression audit row, and pages the operator (§12b). Gated default-OFF behind
GATEWAY_SEND_THROTTLE_ENABLED — flag-off is byte-identical (state never touched).

fcntl is stubbed (ensure_fcntl_stub) so the whole module imports off-Linux;
front_brain_screen_gateway_send additionally needs the flyer customer-copy
policy, so REPO/src is on the path (agents.flyer.customer_copy_policy).
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform", REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402
import schemas  # noqa: E402


CLEAN = "Happy to help with that flyer! What should it promote?"
FALLBACK = "I couldn't finish that reply — tell me what you need and I'll help."
CHAT = "17329837841@c.us"


@pytest.fixture
def safe_io_module():
    """Import safe_io fresh; the throttle accessors read env at call time, so no
    per-test state leaks across tests."""
    importlib.reload(safe_io)
    return safe_io


@pytest.fixture
def gw_env(tmp_path, monkeypatch, safe_io_module):
    """Admit CHAT to the front-brain enforce tier (so the screen + throttle run),
    give the per-chat/day budget generous room (so it never trips before the
    throttle under test), and enable the gateway throttle routed to a tmp state
    file. Returns the tmp state-file path."""
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "1000")
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "8.0")
    monkeypatch.setenv("GATEWAY_SEND_THROTTLE_ENABLED", "1")
    state = tmp_path / "throttle" / "gateway_send_throttle.json"
    monkeypatch.setenv("GATEWAY_SEND_THROTTLE_STATE_PATH", str(state))
    return state


# ── Pure sliding-window math is the SAME public fn as the bridge throttle ─────

class TestPureWindowReuse:
    def test_seam_drives_the_public_window_fn_no_private_copy(self):
        # The gateway throttle imports NO private window copy — it reuses the same
        # public conversation_throttle_decision as the bridge throttle (#639).
        allowed, ts = safe_io.conversation_throttle_decision(
            [1.0, 2.0, 3.0, 4.0, 5.0], 6.0, limit=5, window_sec=600,
        )
        assert allowed is False       # 5 in-window → the 6th breaches
        assert len(ts) == 5           # the breaching send is NOT recorded


# ── gateway-seam integration (substitute / alert / audit / isolation) ─────────

class TestGatewayThrottleIntegration:
    def test_sixth_send_substitutes_template_with_distinct_breach(
        self, safe_io_module, gw_env, monkeypatch
    ):
        monkeypatch.setenv("GATEWAY_SEND_THROTTLE_LIMIT", "5")
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

        # 5 clean finalized sends pass the screen and return the composed text.
        for _ in range(5):
            assert safe_io_module.front_brain_screen_gateway_send(CHAT, CLEAN) == CLEAN

        # 6th breaches: the composed text is NOT returned; the safe template is
        # (the seam's suppression shape — it always relays a string, so this is a
        # substitute, not a true drop; see the safe_io section note).
        out = safe_io_module.front_brain_screen_gateway_send(
            CHAT, CLEAN, fallback_template=FALLBACK,
        )
        assert out == FALLBACK

        # Exactly one DISTINCT breach audit row, seam-named so §12b can tell which
        # seam is spiraling — and NOT the bridge_post throttle's literal.
        breaches = [f for t, f in emit_calls if t == "gateway_send_throttle_breach"]
        assert len(breaches) == 1
        assert not any(t == "conversation_send_throttle_breach" for t, _ in emit_calls)
        breach = breaches[0]
        assert breach["jid"] and isinstance(breach["jid"], str)
        assert breach["window_count"] == 5
        assert breach["limit"] == 5
        assert breach["window_sec"] == 600
        assert breach["message_preview"] == CLEAN[:120]

        # The human-review surface's "one row per sent text" invariant holds: the
        # substituted template is recorded as a template_fallback composed row.
        composed = [f for t, f in emit_calls if t == "front_brain_reply_composed"]
        assert any(c["template_fallback"] is True and c["reply_text"] == FALLBACK for c in composed)

        # §12b operator page fired exactly once (on the breach).
        assert len(alerts) == 1

    def test_progressive_drafts_do_not_consume_budget(
        self, safe_io_module, gw_env, monkeypatch
    ):
        monkeypatch.setenv("GATEWAY_SEND_THROTTLE_LIMIT", "1")
        monkeypatch.setattr(safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True)
        # 10 progressive drafts (reserve_budget=False) screen but consume NO
        # throttle budget — a streamed reply that drafts 10x then finalizes once
        # must cost ONE (parity with the per-chat/day budget's unit).
        for _ in range(10):
            assert safe_io_module.front_brain_screen_gateway_send(
                CHAT, CLEAN, reserve_budget=False,
            ) == CLEAN
        # the single finalized send consumes the one unit
        assert safe_io_module.front_brain_screen_gateway_send(CHAT, CLEAN) == CLEAN
        # the NEXT finalized send trips the exhausted window (limit=1) → template
        assert safe_io_module.front_brain_screen_gateway_send(
            CHAT, CLEAN, fallback_template=FALLBACK,
        ) == FALLBACK
        # a progressive draft AFTER exhaustion still screens (never throttle-gated)
        assert safe_io_module.front_brain_screen_gateway_send(
            CHAT, CLEAN, reserve_budget=False,
        ) == CLEAN

    def test_distinct_conversations_have_independent_windows(
        self, safe_io_module, gw_env, monkeypatch
    ):
        monkeypatch.setenv("GATEWAY_SEND_THROTTLE_LIMIT", "3")
        monkeypatch.setattr(safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True)
        alice = "17329837841@c.us"
        bob = "19998887777@c.us"
        for _ in range(3):
            assert safe_io_module.front_brain_screen_gateway_send(alice, CLEAN) == CLEAN
        for _ in range(3):
            assert safe_io_module.front_brain_screen_gateway_send(bob, CLEAN) == CLEAN
        # Each conversation independently breaches on its own 4th (limit=3).
        assert safe_io_module.front_brain_screen_gateway_send(
            alice, CLEAN, fallback_template=FALLBACK,
        ) == FALLBACK
        assert safe_io_module.front_brain_screen_gateway_send(
            bob, CLEAN, fallback_template=FALLBACK,
        ) == FALLBACK

    def test_flag_off_is_byte_identical_no_state_touched(
        self, tmp_path, safe_io_module, monkeypatch
    ):
        """Flag unset → the throttle is inert: >5 finalized sends to one
        conversation all return the composed text and the state file is never
        created (no counting, no lock)."""
        monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
        monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
        monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
        monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "1000")
        monkeypatch.delenv("GATEWAY_SEND_THROTTLE_ENABLED", raising=False)
        state = tmp_path / "gateway-throttle-off.json"
        monkeypatch.setenv("GATEWAY_SEND_THROTTLE_STATE_PATH", str(state))
        monkeypatch.setattr(safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True)
        for _ in range(8):
            assert safe_io_module.front_brain_screen_gateway_send(CHAT, CLEAN) == CLEAN
        assert not state.exists()

    def test_throttle_state_io_error_fails_open(
        self, safe_io_module, gw_env, monkeypatch, capsys
    ):
        """A throttle-state write error must ALLOW the send (fail-open) and log
        with a seam-branded marker — the opposite asymmetry from the breach path
        (a broken counter is an infra hiccup, not a flood)."""
        monkeypatch.setenv("GATEWAY_SEND_THROTTLE_LIMIT", "1")
        monkeypatch.setattr(safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True)

        def _boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(safe_io_module, "atomic_write_json", _boom)

        # Even at limit=1, the state fault fails OPEN → the composed reply goes
        # out (NOT the template).
        assert safe_io_module.front_brain_screen_gateway_send(CHAT, CLEAN) == CLEAN
        err = capsys.readouterr().err
        assert "failing OPEN" in err
        assert "gateway_send_throttle" in err  # seam-branded fault line (§12a)

    def test_breach_returns_sendable_string_no_loop_signal(
        self, safe_io_module, gw_env, monkeypatch
    ):
        """DELTA #5 investigation, made executable. The gateway seam's contract
        is "ALWAYS return a sendable string": on breach it returns the fallback
        template (a str) — it never raises and never returns a non-string
        sentinel. The injected adapter wrapper (tools/patch-hermes.py
        _shift_front_brain_screen_outbound) assigns that string to `content` and
        relays it, and fail-opens on exception — so there is NO channel to
        propagate a throttle signal back into the agent loop. Hence
        substitute-and-alert stands; no loop signal is wired (fabricating one
        would be a phantom path). Evidence + rationale in the PR report."""
        monkeypatch.setenv("GATEWAY_SEND_THROTTLE_LIMIT", "1")
        monkeypatch.setattr(safe_io_module, "notify_owner_with_fallback", lambda *a, **k: True)
        assert safe_io_module.front_brain_screen_gateway_send(CHAT, CLEAN) == CLEAN
        out = safe_io_module.front_brain_screen_gateway_send(
            CHAT, CLEAN, fallback_template=FALLBACK,
        )
        assert isinstance(out, str) and out == FALLBACK


# ── Schema / enum-guard ──────────────────────────────────────────────────────

class TestGatewayThrottleAuditSchema:
    def test_breach_reason_literal_is_a_known_log_entry_type(self):
        assert "gateway_send_throttle_breach" in schemas._KNOWN_LOG_ENTRY_TYPES

    def test_breach_row_validates_through_the_real_adapter(self, safe_io_module):
        """_emit_audit_row builds the LogEntry via the real TypeAdapter; a bad
        literal or field shape would raise ValidationError. Routed to the tmp
        decisions.log (conftest _isolate_audit_log)."""
        safe_io_module._emit_audit_row(
            "gateway_send_throttle_breach",
            {
                "jid": CHAT,
                "caller_script": "test",
                "window_count": 5,
                "limit": 5,
                "window_sec": 600,
                "message_preview": "hi",
            },
        )
        log_path = Path(safe_io_module._decisions_log_path())
        rows = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln.strip()]
        assert any(r["type"] == "gateway_send_throttle_breach" for r in rows)
