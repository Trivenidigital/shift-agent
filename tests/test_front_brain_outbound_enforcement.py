"""P0-3a — safe_io front-brain outbound enforcement wiring (fcntl-gated).

safe_io imports fcntl (Linux only), so these run in Docker python:3.11-slim, not
on Windows. Coverage:
  - flag/allowlist admission (empty=disabled, `*` graduation, normalization)
  - flag OFF → byte-identical: message unchanged, ZERO audit rows
  - PASS → composed text unchanged + front_brain_reply_composed(template_fallback=False)
  - FAIL → safe fallback returned + refusal audit + review row(template_fallback=True)
  - caller fallback_template used verbatim; safe generic ack when none supplied
  - never blocks: always returns a sendable string
  - bridge_post integration: fallback_template kwarg + audit emission end-to-end
"""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "platform"))
sys.path.insert(0, str(REPO / "src"))

# safe_io imports fcntl at module load — guard so collection does not error on
# Windows (the whole module is skipped there via pytestmark); on Linux/Docker
# the import succeeds and the tests run.
try:
    import safe_io  # noqa: E402
    from schemas import LogEntry  # noqa: E402
    from pydantic import TypeAdapter  # noqa: E402

    ADAPTER = TypeAdapter(LogEntry)
except ModuleNotFoundError:  # pragma: no cover - Windows (no fcntl)
    safe_io = None  # type: ignore[assignment]
    ADAPTER = None  # type: ignore[assignment]

PROMISE_MSG = "We guarantee a full refund and free delivery by Friday."
CLEAN_MSG = "Happy to help with that flyer! What should it promote?"
FALLBACK = "I couldn't finish that reply — tell me what you need and I'll help."


@pytest.fixture(autouse=True)
def _rebind_safe_io_to_live_module(monkeypatch):
    """Order-determinism: test_cf_router_plugin's loader does
    ``sys.modules.pop("safe_io")`` and reloads it under a FRESH module object.
    This file's top-level ``import safe_io`` then points at a STALE object that
    the conftest fake-bridge fixture no longer patches, so a later bridge_post
    test sees the default live-bridge URL (port 3000) and trips
    LiveBridgeSendInTestError. Re-resolve ``safe_io`` from sys.modules at call
    time and (re)apply the fake sink to the live object so these tests pass
    regardless of prior module reloads."""
    global safe_io
    if safe_io is None:  # Windows (fcntl) — module skipped anyway
        yield
        return
    import sys as _sys
    live = _sys.modules.get("safe_io") or safe_io
    safe_io = live
    if hasattr(live, "BRIDGE_URL"):
        monkeypatch.setattr(
            live, "BRIDGE_URL", "http://127.0.0.1:1/__fake_test_sink__", raising=False
        )
    yield


def _read_rows(monkeypatch) -> list[dict]:
    """Rows written to the per-test isolated decisions.log (conftest autouse)."""
    import os
    log_path = Path(os.environ["SHIFT_AGENT_DECISIONS_LOG_PATH"])
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _enable(monkeypatch, allowlist="*"):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", allowlist)


# ── flag/allowlist admission ────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    assert safe_io.front_brain_outbound_enforce_enabled("17329837841@c.us") is False


def test_empty_allowlist_disables(monkeypatch):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "")
    assert safe_io.front_brain_outbound_enforce_enabled("17329837841@c.us") is False


def test_wildcard_graduates_all(monkeypatch):
    _enable(monkeypatch, "*")
    assert safe_io.front_brain_outbound_enforce_enabled("anychat@c.us") is True


def test_membership_normalized(monkeypatch):
    _enable(monkeypatch, "+1 732 983 7841")
    assert safe_io.front_brain_outbound_enforce_enabled("17329837841@c.us") is True
    assert safe_io.front_brain_outbound_enforce_enabled("19999999999@c.us") is False


# ── enforce helper: flag OFF is byte-identical ──────────────────────────────

def test_flag_off_byte_identical(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    out = safe_io._front_brain_outbound_enforce("chat@c.us", PROMISE_MSG, fallback_template=FALLBACK)
    assert out == PROMISE_MSG  # unchanged
    assert _read_rows(monkeypatch) == []  # zero side effects


# ── enforce helper: PASS ────────────────────────────────────────────────────

def test_pass_returns_composed_and_emits_review_row(monkeypatch):
    _enable(monkeypatch)
    out = safe_io._front_brain_outbound_enforce("chat@c.us", CLEAN_MSG)
    assert out == CLEAN_MSG
    rows = _read_rows(monkeypatch)
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert len(composed) == 1
    assert composed[0]["template_fallback"] is False
    assert composed[0]["reply_text"] == CLEAN_MSG
    assert composed[0]["verdict"] == "passed"
    assert not [r for r in rows if r["type"] == "front_brain_outbound_refused"]
    # every emitted row validates through the union
    for r in rows:
        ADAPTER.validate_python(r)


# ── enforce helper: FAIL → fallback + refusal + review(template_fallback) ───

def test_fail_uses_caller_fallback_and_emits_both_rows(monkeypatch):
    _enable(monkeypatch)
    out = safe_io._front_brain_outbound_enforce("chat@c.us", PROMISE_MSG, fallback_template=FALLBACK)
    assert out == FALLBACK  # composed text NOT sent; caller fallback used verbatim
    rows = _read_rows(monkeypatch)
    refused = [r for r in rows if r["type"] == "front_brain_outbound_refused"]
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert len(refused) == 1
    assert "promise_ban" in refused[0]["hit_classes"]
    assert refused[0]["message_preview"].startswith("We guarantee")
    assert len(composed) == 1
    assert composed[0]["template_fallback"] is True
    assert composed[0]["reply_text"] == FALLBACK
    for r in rows:
        ADAPTER.validate_python(r)


def test_verified_regulated_completion_not_clobbered(monkeypatch):
    # A verified regulated completion (evidence-backed) passes the content
    # classes and is sent as-is — the verified_action_result flows from the
    # action_context into the screen so front-brain does not clobber it.
    from schemas import ActionExecutionContext
    _enable(monkeypatch)
    ctx = ActionExecutionContext(
        action_id="commerce.payment.confirm",
        is_regulated_action=True,
        verified_action_result=True,
        audit_row_id="row-123",
    )
    msg = "Your refund of $50 has been processed."
    out = safe_io._front_brain_outbound_enforce("chat@c.us", msg, action_context=ctx)
    assert out == msg
    rows = _read_rows(monkeypatch)
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert composed and composed[0]["template_fallback"] is False
    assert not [r for r in rows if r["type"] == "front_brain_outbound_refused"]


def test_unverified_regulated_completion_is_screened(monkeypatch):
    # Same message WITHOUT verified evidence is screened + substituted.
    from schemas import ActionExecutionContext
    _enable(monkeypatch)
    ctx = ActionExecutionContext(
        action_id="commerce.payment.confirm",
        is_regulated_action=True,
        verified_action_result=False,
    )
    msg = "Your refund of $50 has been processed."
    out = safe_io._front_brain_outbound_enforce("chat@c.us", msg, action_context=ctx)
    assert out == safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK
    assert [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]


def test_fail_without_fallback_uses_safe_generic_ack(monkeypatch):
    _enable(monkeypatch)
    out = safe_io._front_brain_outbound_enforce("chat@c.us", PROMISE_MSG)
    assert out == safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK
    # the safe generic ack itself passes the screen (no promise / no claim).
    from agents.flyer.customer_copy_policy import enforce_free_form_text
    assert enforce_free_form_text(safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK).passed is True


def test_never_blocks_returns_string(monkeypatch):
    _enable(monkeypatch)
    for msg in ("", PROMISE_MSG, CLEAN_MSG, "x" * 5000):
        out = safe_io._front_brain_outbound_enforce("chat@c.us", msg)
        assert isinstance(out, str) and out != "" or msg == ""


# ── bridge_post integration: kwarg + wiring end-to-end ──────────────────────

def test_bridge_post_accepts_fallback_template_kwarg_and_wires_enforcement(monkeypatch):
    _enable(monkeypatch)
    # Opt into in-test send so bridge_post proceeds past the pytest guard to the
    # enforcement layer; the conftest fake sink (port 1, closed) makes the actual
    # HTTP POST fail cleanly AFTER enforcement runs (never the live bridge).
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    ok, mid, err, status = safe_io.bridge_post(
        "chat@c.us", PROMISE_MSG, fallback_template=FALLBACK,
    )
    # Send fails at the fake sink (expected) — but enforcement already ran.
    assert ok is False
    rows = _read_rows(monkeypatch)
    assert [r for r in rows if r["type"] == "front_brain_outbound_refused"]
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert composed and composed[0]["reply_text"] == FALLBACK


def test_bridge_post_flag_off_emits_no_front_brain_rows(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    safe_io.bridge_post("chat@c.us", PROMISE_MSG, fallback_template=FALLBACK)
    rows = _read_rows(monkeypatch)
    assert not [r for r in rows if r["type"].startswith("front_brain_")]
