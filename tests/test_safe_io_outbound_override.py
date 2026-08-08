"""Turn-bound outbound override — the W1 compliance truthfulness invariant.

WHY THIS MECHANISM EXISTS. A deterministic zero-result ("no TRACKED compliance
deadline is due in this window") was repeatedly rendered by the model as an
unqualified "you have no compliance deadlines". Result-shape work reduced but
never eliminated it: with self-scoping field names, correct scoping still
dropped in 1 of 5 byte-identical runs. Wording cannot be the safety mechanism,
and inspecting wording is explicitly not the answer either. So the tool binds a
deterministic reply to the exact turn and the egress seam substitutes it without
reading a word the model wrote.

These tests pin the mechanism's structure. No model sampling here — that belongs
in falsification, not CI.
"""
from __future__ import annotations

import platform
import sys
import threading
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"

linux_only = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io imports fcntl (Linux only)",
)


def _install_session_stub(session_id: str, message_id: str) -> None:
    """Stand in for the gateway ContextVars with exact values."""
    mod = types.ModuleType("gateway.session_context")
    values = {"HERMES_SESSION_ID": session_id, "HERMES_SESSION_MESSAGE_ID": message_id}
    mod.get_session_env = lambda name, default="": values.get(name, default)
    pkg = sys.modules.get("gateway") or types.ModuleType("gateway")
    pkg.session_context = mod
    sys.modules["gateway"] = pkg
    sys.modules["gateway.session_context"] = mod


@pytest.fixture
def sio(monkeypatch):
    monkeypatch.syspath_prepend(str(PLATFORM_DIR))
    import safe_io
    safe_io._turn_overrides.clear()
    yield safe_io
    safe_io._turn_overrides.clear()


TEXT = "None of the compliance deadlines currently tracked in your calendar are due within the next 90 days."


# ── registration ───────────────────────────────────────────────────────────

@linux_only
def test_registration_succeeds_with_a_bound_turn(sio):
    _install_session_stub("sess-1", "msg-1")
    assert sio.register_turn_outbound_override(TEXT) is True
    assert sio.lookup_turn_outbound_override() == TEXT


@linux_only
@pytest.mark.parametrize("sid,mid", [("", "msg-1"), ("sess-1", ""), ("", "")])
def test_registration_refuses_an_unbound_turn(sio, sid, mid):
    """No exact key means no registration — never an invented one."""
    _install_session_stub(sid, mid)
    assert sio.register_turn_outbound_override(TEXT) is False
    assert sio.lookup_turn_outbound_override() is None


@linux_only
def test_empty_text_is_not_registrable(sio):
    _install_session_stub("sess-1", "msg-1")
    assert sio.register_turn_outbound_override("") is False


# ── lookup semantics ───────────────────────────────────────────────────────

@linux_only
def test_lookup_does_not_consume_the_override(sio):
    """A streamed draft and the finalized edit both screen the same turn."""
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    assert sio.lookup_turn_outbound_override() == TEXT
    assert sio.lookup_turn_outbound_override() == TEXT
    assert sio.lookup_turn_outbound_override() == TEXT


@linux_only
def test_next_turn_in_same_session_has_no_override(sio):
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    _install_session_stub("sess-1", "msg-2")          # next message, same session
    assert sio.lookup_turn_outbound_override() is None


@linux_only
def test_different_session_with_same_message_id_has_no_override(sio):
    """Proves the key is compound, not message-id alone."""
    _install_session_stub("sess-A", "msg-shared")
    sio.register_turn_outbound_override(TEXT)
    _install_session_stub("sess-B", "msg-shared")
    assert sio.lookup_turn_outbound_override() is None


@linux_only
def test_pre_agent_session_registration_cannot_match_post_rebind_turn(sio):
    """THE discovered invariant. `AIAgent.run_conversation` reassigns
    HERMES_SESSION_ID, so a key read before the agent starts will not match the
    one the adapter resolves — and it fails by letting unsafe text through."""
    _install_session_stub("sess-pre-agent", "msg-1")
    assert sio.register_turn_outbound_override(TEXT) is True
    _install_session_stub("sess-post-rebind", "msg-1")   # same message, new session id
    assert sio.lookup_turn_outbound_override() is None, (
        "a pre-agent registration must NOT satisfy the post-rebind turn; there "
        "is deliberately no fallback to the old session id"
    )


@linux_only
def test_concurrent_turns_do_not_leak(sio, monkeypatch):
    """Two turns genuinely in flight at once must not see each other's override.

    The first version of this test defined a worker and never started a thread —
    it was three sequential stub swaps asserting nothing about concurrency. The
    P6 pre-implementation proof WAS threaded; the committed regression was not,
    so the property was unpinned. It is pinned here.

    `_current_turn_key` is stubbed thread-locally because the module-level
    session stub is process-global and cannot represent two simultaneous turns.
    The registry itself — lock, dict, exact-key lookup — is the real thing.
    """
    local = threading.local()

    def thread_local_turn_key():
        return getattr(local, "key", None)

    monkeypatch.setattr(sio, "_current_turn_key", thread_local_turn_key)

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(label, key, text):
        try:
            local.key = key
            sio.register_turn_outbound_override(text)
            barrier.wait(timeout=5)      # both registered before either resolves
            results[label] = sio.lookup_turn_outbound_override()
        except BaseException as exc:      # noqa: BLE001 - surfaced below
            errors.append(exc)

    text_a = "A: none of your tracked deadlines are due in the next 90 days."
    text_b = "B: your compliance calendar has no tracked records."
    ta = threading.Thread(target=run, args=("A", ("sess-A", "msg-A"), text_a))
    tb = threading.Thread(target=run, args=("B", ("sess-B", "msg-B"), text_b))
    ta.start(); tb.start(); ta.join(10); tb.join(10)

    assert not errors, f"worker raised: {errors}"
    assert results["A"] == text_a
    assert results["B"] == text_b
    assert results["A"] != results["B"], "distinct turns must not share a value"
    assert len(sio._turn_overrides) == 2, "both registrations must coexist"


@linux_only
def test_registry_is_lock_protected_and_process_local(sio):
    assert isinstance(sio._turn_override_lock, type(threading.Lock()))
    assert isinstance(sio._turn_overrides, dict)


# ── TTL ────────────────────────────────────────────────────────────────────

@linux_only
def test_expired_override_is_ignored_and_swept(sio, monkeypatch):
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    assert sio.lookup_turn_outbound_override() == TEXT

    real_monotonic = sio.time.monotonic
    monkeypatch.setattr(
        sio.time, "monotonic",
        lambda: real_monotonic() + sio._TURN_OVERRIDE_TTL_SEC + 1,
    )
    assert sio.lookup_turn_outbound_override() is None
    assert sio._turn_overrides == {}, "stale entry must be swept, not merely ignored"


# ── seam behavior ──────────────────────────────────────────────────────────

@linux_only
def test_override_replaces_any_wording_identically(sio, monkeypatch):
    """Wording independence: the seam never reads the model's text."""
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    monkeypatch.setattr(sio, "front_brain_outbound_enforce_enabled", lambda jid: False)
    monkeypatch.setattr(sio, "_agent_disabled", lambda: False)
    monkeypatch.setattr(sio, "_automation_control_suppressed_mode", lambda jid: None)

    jid = "19045550100@s.whatsapp.net"
    for candidate in (
        "Currently, there are no compliance deadlines due in the next 90 days.",
        "All clear.",
        "Bananas are yellow.",
        "",
    ):
        assert sio.front_brain_screen_gateway_send(jid, candidate) == TEXT


@linux_only
def test_override_applies_while_enforce_tier_is_off(sio, monkeypatch):
    """The compliance invariant must not depend on FRONT_BRAIN_OUTBOUND_ENFORCE,
    whose default is OFF."""
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    monkeypatch.setattr(sio, "front_brain_outbound_enforce_enabled", lambda jid: False)
    monkeypatch.setattr(sio, "_agent_disabled", lambda: False)
    monkeypatch.setattr(sio, "_automation_control_suppressed_mode", lambda jid: None)
    assert sio.front_brain_screen_gateway_send(
        "19045550100@s.whatsapp.net", "unsafe") == TEXT


@linux_only
def test_unconstrained_turn_passes_through_byte_identical(sio, monkeypatch):
    _install_session_stub("sess-1", "msg-1")          # nothing registered
    monkeypatch.setattr(sio, "front_brain_outbound_enforce_enabled", lambda jid: False)
    monkeypatch.setattr(sio, "_agent_disabled", lambda: False)
    monkeypatch.setattr(sio, "_automation_control_suppressed_mode", lambda jid: None)
    original = "Health Inspection Houston is due on 20 August."
    assert sio.front_brain_screen_gateway_send(
        "19045550100@s.whatsapp.net", original) == original


@linux_only
def test_kill_switch_retains_precedence_over_the_override(sio, monkeypatch):
    """Operator authority outranks this invariant."""
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    monkeypatch.setattr(sio, "_agent_disabled", lambda: True)
    monkeypatch.setattr(sio, "_alert_agent_disabled_send", lambda kind: None)
    monkeypatch.setattr(sio, "_try_emit_audit_row", lambda *a, **k: None)
    monkeypatch.setattr(sio, "_gateway_seam_refusal_text",
                        lambda jid, tpl, reason: f"REFUSED:{reason}")
    out = sio.front_brain_screen_gateway_send("19045550100@s.whatsapp.net", "x")
    assert out == "REFUSED:agent_disabled"
    assert out != TEXT


@linux_only
def test_automation_control_retains_precedence_over_the_override(sio, monkeypatch):
    _install_session_stub("sess-1", "msg-1")
    sio.register_turn_outbound_override(TEXT)
    monkeypatch.setattr(sio, "_agent_disabled", lambda: False)
    monkeypatch.setattr(sio, "_automation_control_suppressed_mode", lambda jid: "paused")
    monkeypatch.setattr(sio, "_try_emit_audit_row", lambda *a, **k: None)
    monkeypatch.setattr(sio, "_gateway_seam_refusal_text",
                        lambda jid, tpl, reason: f"REFUSED:{reason}")
    out = sio.front_brain_screen_gateway_send("19045550100@s.whatsapp.net", "x")
    assert out.startswith("REFUSED:automation_suppressed")
    assert out != TEXT


# ── audit variant is typed, not swallowed by forward-compat ────────────────

@linux_only
def test_emitted_audit_payload_resolves_to_the_typed_variant(sio, monkeypatch):
    """The exact payload the seam emits must validate AS the typed variant.

    Before this variant existed the row still validated — `_pick_log_entry_tag`
    routes unknown tags to `_UnknownLogEntry` (extra="allow"), which is a
    deliberate forward-compat shim and worked as designed. But that path does no
    field validation, so a deterministic safety intervention that overrides
    customer-visible text was being recorded untyped. This pins that it is not.
    """
    from pydantic import TypeAdapter
    from schemas import LogEntry, OutboundTurnOverrideApplied, _UnknownLogEntry

    captured = {}
    monkeypatch.setattr(
        sio, "_try_emit_audit_row",
        lambda t, f: captured.update({"type": t, "fields": f}),
    )
    monkeypatch.setattr(sio, "front_brain_outbound_enforce_enabled", lambda jid: False)
    monkeypatch.setattr(sio, "_agent_disabled", lambda: False)
    monkeypatch.setattr(sio, "_automation_control_suppressed_mode", lambda jid: None)

    _install_session_stub("sess-audit", "msg-audit")
    sio.register_turn_outbound_override(TEXT)
    assert sio.front_brain_screen_gateway_send(
        "19045550100@s.whatsapp.net", "unsafe wording") == TEXT

    assert captured["type"] == "outbound_turn_override_applied"
    row = {"ts": "2026-08-08T18:14:41.313214Z",
           "type": captured["type"], **captured["fields"]}
    parsed = TypeAdapter(LogEntry).validate_python(row)
    assert isinstance(parsed, OutboundTurnOverrideApplied), (
        f"row fell through to {type(parsed).__name__}; the typed variant is not "
        "wired into the LogEntry union"
    )
    assert not isinstance(parsed, _UnknownLogEntry)
    assert parsed.send_kind == "gateway_send"


def test_historical_production_row_still_validates_as_the_typed_variant():
    """The row already written to production (2026-08-08T18:14:41Z) must remain
    readable after the schema addition — a typed variant that rejects the rows
    it was added for would break `shift-agent-smoke-test.sh`, which validates
    the LAST line of decisions.log and auto-rolls-back the deploy on failure."""
    from pydantic import TypeAdapter
    from schemas import LogEntry, OutboundTurnOverrideApplied

    historical = {
        "ts": "2026-08-08T18:14:41.313214Z",
        "type": "outbound_turn_override_applied",
        "chat_key_hash": "798ef6009df419526e9369c37dc00000",
        "send_kind": "gateway_send",
        "logical_turn_id": "",
    }
    parsed = TypeAdapter(LogEntry).validate_python(historical)
    assert isinstance(parsed, OutboundTurnOverrideApplied)


def test_genuinely_unknown_tags_still_reach_the_forward_compat_shim():
    """The `_UnknownLogEntry` mechanism is untouched — it is doing exactly what
    it was designed for, and typing one variant must not narrow it."""
    from pydantic import TypeAdapter
    from schemas import LogEntry, _UnknownLogEntry

    parsed = TypeAdapter(LogEntry).validate_python(
        {"ts": "2026-08-08T00:00:00Z", "type": "some_future_variant", "x": 1})
    assert isinstance(parsed, _UnknownLogEntry)
