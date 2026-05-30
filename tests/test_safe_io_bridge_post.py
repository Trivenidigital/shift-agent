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


def _ctx():
    """Minimal non-regulated ActionExecutionContext so the action-context
    chokepoint allows a send whose in-process caller isn't an allowlisted
    script basename (send-path-test-harness). Mirrors how a real regulated
    caller threads context; here is_regulated_action=False so no lint runs."""
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="bridge-post-unit-test",
        is_regulated_action=False,
        verified_action_result=False,
    )


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

        ok, mid, err, status = safe_io_module.bridge_post("jid@s.whatsapp.net", "msg", action_context=_ctx())

        assert ok is True
        assert mid == "wamid.123abc"
        assert err == ""
        assert status == "sent"

    @patch("urllib.request.urlopen")
    def test_send_uncertain_on_unparseable_body(self, urlopen, safe_io_module, monkeypatch):
        # send-path-test-harness: per-test opt-in (this class also holds the
        # guard-refuse tests, which must NOT be opted in). Fake-sink default +
        # mocked urlopen mean nothing is sent.
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not-json"
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, mid, err, status = safe_io_module.bridge_post("jid@s.whatsapp.net", "msg", action_context=_ctx())
        assert ok is False
        assert status == "send_uncertain"
        assert "ack_parse_failed" in err

    @patch("urllib.request.urlopen")
    def test_empty_message_id_is_uncertain(self, urlopen, safe_io_module, monkeypatch):
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"foo": "bar"}'  # parses but no id field
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg", action_context=_ctx())
        assert ok is False
        assert status == "send_uncertain"
        assert "empty_message_id" in err

    @patch("urllib.request.urlopen")
    def test_success_returns_message_id(self, urlopen, safe_io_module, monkeypatch):
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.123abc"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg", action_context=_ctx())
        assert ok is True
        assert mid == "wamid.123abc"
        assert status == "sent"

    def test_alternative_messageId_field(self, safe_io_module, monkeypatch):
        # Note: this test patches urlopen via the `with` block below; the prior
        # redundant @patch decorator injected its mock into safe_io_module
        # (latent bug, surfaced once the test reached the send path).
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        with patch("urllib.request.urlopen") as urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"messageId": "mid.xyz"}'
            urlopen.return_value.__enter__.return_value = mock_resp
            ok, mid, err, status = safe_io_module.bridge_post("jid", "msg", action_context=_ctx())
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

    @pytest.fixture(autouse=True)
    def _opt_in_bridge_sends(self, safe_io_module, monkeypatch):
        """send-path-test-harness: every test in this class exercises the send
        path against a mocked urlopen, so (1) opt past the pytest bridge guard
        and (2) resolve the caller to an allowlisted production script. The
        2-tuple adapter takes no action_context arg, and in production its
        callers ARE allowlisted scripts (send-catering-ack, apply-expense-
        decision, ...), so forcing an allowlisted caller faithfully mirrors
        prod and is the reviewed-allowlist route. Conftest fake-sink default +
        mocked urlopen mean nothing is sent; the tripwire is unaffected. No
        guard-refuse test lives in this class (does not weaken the guard)."""
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
        monkeypatch.setattr(
            safe_io_module, "_resolve_caller_script_name",
            lambda: "send-catering-ack",
        )

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


class TestActionContextEnforcement:
    """PR-ζ 2026-05-26 — chokepoint enforces action_context policy.

    Tests pair each branch in _enforce_action_context_policy with a
    representative caller (resolver mocked via monkeypatch) and assert the
    refusal vs pass-through behavior + the audit row written.
    """

    @pytest.fixture(autouse=True)
    def _opt_in_bridge_sends(self, monkeypatch):
        """send-path-test-harness: every test here exercises the chokepoint,
        which runs AFTER the pytest bridge guard — so opt past the guard to
        reach the code under test. Both the pass-through and the chokepoint-
        refusal tests need the guard bypassed (the refusals they assert are
        the CHOKEPOINT's, not the guard's). The conftest fake-sink default +
        mocked urlopen mean nothing is sent. No guard-refuse test lives in this
        class, so a class-scoped opt-in is safe (does not weaken the guard)."""
        monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")

    def _force_caller(self, safe_io_module, monkeypatch, name: str) -> None:
        """Override _resolve_caller_script_name to return `name`."""
        monkeypatch.setattr(safe_io_module, "_resolve_caller_script_name", lambda: name)

    @patch("urllib.request.urlopen")
    def test_allowlisted_caller_with_none_context_proceeds(
        self, urlopen, safe_io_module, monkeypatch
    ):
        self._force_caller(safe_io_module, monkeypatch, "send-daily-brief")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp

        ok, mid, err, status = safe_io_module.bridge_post("jid", "Daily brief: ...")
        assert ok is True
        assert mid == "wamid.OK"
        assert status == "sent"

    @patch("urllib.request.urlopen")
    def test_non_allowlisted_caller_with_none_context_refused(
        self, urlopen, safe_io_module, monkeypatch
    ):
        self._force_caller(safe_io_module, monkeypatch, "rogue-test-script.py")
        emit_calls = []
        monkeypatch.setattr(
            safe_io_module, "_emit_audit_row",
            lambda etype, fields: emit_calls.append((etype, fields)),
        )

        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg")
        assert ok is False
        assert mid == ""
        assert err == "missing_action_context"
        assert status == "refused"
        urlopen.assert_not_called()
        assert len(emit_calls) == 1
        etype, fields = emit_calls[0]
        assert etype == "regulated_send_missing_action_context"
        assert fields["caller_script"] == "rogue-test-script.py"
        assert fields["jid"] == "jid"

    @patch("urllib.request.urlopen")
    def test_regulated_context_verified_with_forbidden_verb_proceeds(
        self, urlopen, safe_io_module
    ):
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=True,
            mutation_class="external_irreversible",
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid", "Your plan has been upgraded.", action_context=ctx,
        )
        assert ok is True
        assert status == "sent"

    @patch("urllib.request.urlopen")
    def test_regulated_context_unverified_with_forbidden_verb_refused(
        self, urlopen, safe_io_module, monkeypatch
    ):
        from schemas import ActionExecutionContext
        emit_calls = []
        monkeypatch.setattr(
            safe_io_module, "_emit_audit_row",
            lambda etype, fields: emit_calls.append((etype, fields)),
        )

        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid", "Your plan has been upgraded.", action_context=ctx,
        )
        assert ok is False
        assert err == "lint_violation"
        assert status == "refused"
        urlopen.assert_not_called()
        assert len(emit_calls) == 1
        etype, fields = emit_calls[0]
        assert etype == "regulated_send_lint_violation"
        assert "upgraded" in fields["verb_hits"]

    @patch("urllib.request.urlopen")
    def test_regulated_context_unverified_with_clean_message_proceeds(
        self, urlopen, safe_io_module
    ):
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid",
            "Please complete payment at https://example.com/checkout",
            action_context=ctx,
        )
        assert ok is True
        assert status == "sent"

    @patch("urllib.request.urlopen")
    def test_non_regulated_context_with_forbidden_verb_proceeds(
        self, urlopen, safe_io_module
    ):
        from schemas import ActionExecutionContext
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="system.healthcheck",
            is_regulated_action=False,
            verified_action_result=False,
        )
        ok, mid, err, status = safe_io_module.bridge_post(
            "jid", "System upgraded to v2.", action_context=ctx,
        )
        assert ok is True

    def test_audit_write_failure_returns_refusal_tuple_not_exception(
        self, safe_io_module, monkeypatch
    ):
        """REV 3 (PR security/money-flow reviewer #4): when _emit_audit_row
        raises, the chokepoint must convert the exception to a refusal
        tuple — propagating OSError out of bridge_post crashes the Hermes
        plugin handler mid-HTTP-request, leaving customers with persisted
        half-state and no reply. The tuple shape preserves fail-CLOSED
        (send doesn't proceed) while composing cleanly with HTTP. Stderr
        carries the operator-visible signal."""
        def raising_emit(etype, fields):
            raise OSError("disk full")
        monkeypatch.setattr(safe_io_module, "_emit_audit_row", raising_emit)
        monkeypatch.setattr(
            safe_io_module, "_resolve_caller_script_name",
            lambda: "rogue-test-script.py",
        )
        ok, mid, err, status = safe_io_module.bridge_post("jid", "msg")
        assert ok is False
        assert mid == ""
        assert status == "refused"
        assert "audit_write_failed" in err
        assert "OSError" in err  # exception type surfaced for ops

    @patch("urllib.request.urlopen")
    def test_change_plan_pending_reply_passes_lint(self, urlopen, safe_io_module):
        """The REAL _pending_plan_reply function output (account.py:849)
        must pass lint. Loads the actual function rather than a fabricated
        string — the prior fabricated test missed BLOCKER #1 where the
        deployed reply contained the forbidden verb 'confirmed'."""
        import sys as _sys
        from pathlib import Path as _Path
        agents_flyer = _Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer"
        _sys.path.insert(0, str(agents_flyer))
        try:
            from account import _pending_plan_reply
        finally:
            _sys.path.pop(0)
        from schemas import ActionExecutionContext

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        # Exercise the real function with the deployed shape of inputs.
        reply = _pending_plan_reply(
            plan_id="growth",
            url="https://example.com/checkout?plan=growth",
            provider="manual",
        )
        ok, _, err, status = safe_io_module.bridge_post("jid", reply, action_context=ctx)
        assert ok is True, (
            f"_pending_plan_reply output tripped chokepoint lint: "
            f"err={err!r} status={status!r} reply={reply!r}"
        )

    @patch("urllib.request.urlopen")
    def test_change_plan_no_url_reply_passes_lint(self, urlopen, safe_io_module):
        """The empty-URL branch of _pending_plan_reply (provider/checkout
        not yet configured) must also pass lint."""
        import sys as _sys
        from pathlib import Path as _Path
        agents_flyer = _Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer"
        _sys.path.insert(0, str(agents_flyer))
        try:
            from account import _pending_plan_reply
        finally:
            _sys.path.pop(0)
        from schemas import ActionExecutionContext

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = mock_resp
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            mutation_class="external_irreversible",
        )
        reply = _pending_plan_reply(plan_id="growth", url="", provider="manual")
        ok, _, err, status = safe_io_module.bridge_post("jid", reply, action_context=ctx)
        assert ok is True, (
            f"_pending_plan_reply (no-url branch) tripped lint: "
            f"err={err!r} status={status!r} reply={reply!r}"
        )

    def test_more_than_twenty_verb_hits_fails_closed_not_loud(
        self, safe_io_module, monkeypatch
    ):
        """A message tripping many forbidden verbs must refuse cleanly. The
        chokepoint caps verb_hits[:20] before audit-row construction so the
        Pydantic max_length=20 constraint doesn't raise ValidationError
        mid-refusal (which would convert fail-CLOSED into fail-LOUD)."""
        from schemas import ActionExecutionContext
        emit_calls = []
        monkeypatch.setattr(
            safe_io_module, "_emit_audit_row",
            lambda etype, fields: emit_calls.append((etype, fields)),
        )
        ctx = ActionExecutionContext(
            action_id="x", is_regulated_action=True, verified_action_result=False,
        )
        # All 16 distinct forbidden verbs from FORBIDDEN_COMPLETION_VERBS:
        msg = (
            "processed completed upgraded downgraded changed confirmed sent "
            "approved paid posted pushed applied scheduled booked cancelled refunded"
        )
        ok, mid, err, status = safe_io_module.bridge_post("jid", msg, action_context=ctx)
        assert ok is False
        assert err == "lint_violation"
        assert status == "refused"
        assert len(emit_calls) == 1
        _etype, fields = emit_calls[0]
        assert len(fields["verb_hits"]) <= 20

    def test_dict_passed_as_context_propagates_attribute_error(
        self, safe_io_module
    ):
        """Passing a dict instead of an ActionExecutionContext raises
        AttributeError → propagates → caller crashes. This is the intended
        fail-LOUD semantic for type misuse."""
        bad_ctx = {"is_regulated_action": True, "verified_action_result": False}
        with pytest.raises(AttributeError):
            safe_io_module.bridge_post("jid", "msg", action_context=bad_ctx)

    def test_message_preview_truncated_at_120_chars(
        self, safe_io_module, monkeypatch
    ):
        """Long messages must truncate cleanly at 120 chars for the
        message_preview field (max_length=120 on the audit-row schema)."""
        self._force_caller(safe_io_module, monkeypatch, "rogue-test-script.py")
        emit_calls = []
        monkeypatch.setattr(
            safe_io_module, "_emit_audit_row",
            lambda etype, fields: emit_calls.append((etype, fields)),
        )
        long_msg = "x" * 500
        ok, _, _, _ = safe_io_module.bridge_post("jid", long_msg)
        assert ok is False
        assert len(emit_calls) == 1
        _etype, fields = emit_calls[0]
        assert len(fields["message_preview"]) <= 120
