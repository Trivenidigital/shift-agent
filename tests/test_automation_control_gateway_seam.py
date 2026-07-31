"""M4/G2 — automation-control coverage on the front-brain GATEWAY seam.

`front_brain_screen_gateway_send` is the egress for Hermes free-form gateway
replies (adapter send() + edit_message()) and deliberately does NOT go through
bridge_post, so the PR-5 outbound chokepoint never saw it. The only protection
an LLM-answered conversation had was the cf-router INBOUND check — which fails
OPEN by design, so a fault there meant an opted-out customer got a full LLM
reply. These tests pin the seam's new behavior.

Mirrors test_automation_control_bridge_post's structure and fixtures.
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
for _p in (REPO / "src" / "platform", REPO / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402
import automation_control as ac  # noqa: E402

CUSTOMER = "loop@c.us"
OWNER = "owner@c.us"
COMPOSED = "a composed free-form LLM reply about your catering order"


@pytest.fixture
def safe_io_module():
    importlib.reload(safe_io)
    return safe_io


@pytest.fixture
def kernel_env(tmp_path, monkeypatch, safe_io_module):
    """Master flag on, wildcard allowlist, tmp state file."""
    state = tmp_path / "catering-automation-control.json"
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(state))
    ac._LAST_KNOWN_MODE.clear()
    yield state
    ac._LAST_KNOWN_MODE.clear()


def _audit_rows(mod) -> list:
    log = Path(mod._decisions_log_path())
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestSuppressedConversationRefused:
    @pytest.mark.parametrize("mode", ["opted_out", "paused", "takeover"])
    def test_every_suppressing_mode_refuses_the_composed_reply(
        self, mode, safe_io_module, kernel_env,
    ):
        ac.set_mode(CUSTOMER, mode, actor="customer", reason="test")
        out = safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED)
        # The seam relays its return value, so the refusal shape is substituting
        # the safe template — the composed LLM text never leaves.
        assert out != COMPOSED
        assert out == safe_io_module.FRONT_BRAIN_SAFE_GENERIC_ACK
        assert any(r["type"] == "catering_automated_send_suppressed"
                   and r["mode"] == mode
                   and r["suppressed_send_kind"] == "outbound_send"
                   for r in _audit_rows(safe_io_module))

    def test_expired_pause_lets_the_reply_through(self, safe_io_module, kernel_env):
        ac.set_mode(CUSTOMER, "paused", actor="customer", reason="p", pause_seconds=3600)
        assert safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED) != COMPOSED

        doc = json.loads(kernel_env.read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "2000-01-01T00:00:00+00:00"
        kernel_env.write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()

        assert safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED) == COMPOSED

    def test_owner_directed_send_passes_during_customer_takeover(
        self, safe_io_module, kernel_env,
    ):
        """Same property the bridge_post call site has: a send TO the owner keys
        to the owner's (unsuppressed) canonical identity."""
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        assert safe_io_module.front_brain_screen_gateway_send(OWNER, COMPOSED) == COMPOSED

    def test_active_conversation_passes(self, safe_io_module, kernel_env):
        assert safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED) == COMPOSED

    def test_caller_fallback_template_is_used_when_supplied(self, safe_io_module, kernel_env):
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="stop")
        out = safe_io_module.front_brain_screen_gateway_send(
            CUSTOMER, COMPOSED, fallback_template="Thanks - we have you noted.")
        assert out == "Thanks - we have you noted."


class TestKernelOffIsInert:
    def test_flag_off_no_state_read_reply_unchanged(self, tmp_path, safe_io_module, monkeypatch):
        """An opted_out record on disk is never consulted when the master flag is
        off — byte-identical to the pre-M4 seam."""
        state = tmp_path / "off-state.json"
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(state))
        ac._LAST_KNOWN_MODE.clear()
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="stop")
        monkeypatch.delenv("CATERING_AUTOMATION_CONTROL_ENABLED", raising=False)

        assert safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED) == COMPOSED

    def test_empty_allowlist_is_fail_closed_disabled(self, tmp_path, safe_io_module, monkeypatch):
        """ppv1/#612 semantics preserved: an empty allowlist DISABLES, it never
        means global-on."""
        state = tmp_path / "state.json"
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(state))
        ac._LAST_KNOWN_MODE.clear()
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="stop")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "")

        assert safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED) == COMPOSED


class TestFailClosedOnReadError:
    def test_enabled_read_error_refuses(self, safe_io_module, kernel_env, monkeypatch):
        """must-fix a, extended to this seam: ENABLED for the chat + a non-ok
        state read → refuse, never default-active."""
        monkeypatch.setattr(ac, "read_mode_and_status", lambda jid: ("active", "oserror:boom"))
        out = safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED)
        assert out == safe_io_module.FRONT_BRAIN_SAFE_GENERIC_ACK
        assert any(r["type"] == "catering_automated_send_suppressed"
                   and r["mode"] == "read_error"
                   for r in _audit_rows(safe_io_module))

    def test_clean_active_read_still_passes(self, safe_io_module, kernel_env, monkeypatch):
        monkeypatch.setattr(ac, "read_mode_and_status", lambda jid: ("active", "ok"))
        assert safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED) == COMPOSED


class TestFrontBrainTierDormancyPreserved:
    def test_no_review_row_when_the_tier_is_off(self, safe_io_module, kernel_env):
        """front_brain_reply_composed is the TIER's human-review surface. The
        suppression gate sits above the tier gate (it owns its own arming), so it
        must not start writing the tier's rows while the tier is off."""
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="stop")
        safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED)
        types_ = {r["type"] for r in _audit_rows(safe_io_module)}
        assert "catering_automated_send_suppressed" in types_
        assert "front_brain_reply_composed" not in types_

    def test_review_row_written_when_the_tier_is_armed(
        self, safe_io_module, kernel_env, monkeypatch,
    ):
        """With the tier armed, the "one row per sent text" invariant holds: the
        template that actually goes out is recorded."""
        monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
        monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="stop")
        out = safe_io_module.front_brain_screen_gateway_send(CUSTOMER, COMPOSED)
        rows = _audit_rows(safe_io_module)
        review = [r for r in rows if r["type"] == "front_brain_reply_composed"]
        assert len(review) == 1
        assert review[0]["reply_text"] == out
        assert review[0]["template_fallback"] is True
