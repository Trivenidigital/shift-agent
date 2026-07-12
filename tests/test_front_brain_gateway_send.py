"""Item 3 core — safe_io.front_brain_screen_gateway_send (fcntl-gated).

The Hermes gateway-adapter send wrap: the ONLY screen on LLM free-form replies
(they bypass bridge_post). Decision table:
  - flag OFF                         -> message unchanged, ZERO rows (byte-identical)
  - PASS                             -> composed text + review row (template_fallback=False)
  - FAIL (promise/claim)             -> fallback template + refusal + review(template)
  - per-chat/day budget tripped      -> fallback template + review(template), NO screen
  - compose/screen timeout           -> fallback template + review(template)
  - never blocks: always a string; fails TOWARD templates

safe_io imports fcntl (Linux only) -> Docker python:3.11-slim, not Windows.
"""
from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="safe_io uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "platform"))
sys.path.insert(0, str(REPO / "src"))

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
CHAT = "17329837841@c.us"


def _rows() -> list[dict]:
    import os
    p = Path(os.environ["SHIFT_AGENT_DECISIONS_LOG_PATH"])
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
    # per-chat/day budget -> isolated tmp store (front_brain_budget does NOT go
    # through the safe_io prod-write guard; it writes with os.replace).
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "30")
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "4.0")
    yield


def test_flag_off_byte_identical(monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    out = safe_io.front_brain_screen_gateway_send(CHAT, PROMISE_MSG, fallback_template=FALLBACK)
    assert out == PROMISE_MSG
    assert _rows() == []


def test_pass_returns_composed_and_review_row(enabled):
    out = safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG)
    assert out == CLEAN_MSG
    rows = _rows()
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert len(composed) == 1 and composed[0]["template_fallback"] is False
    assert composed[0]["reply_text"] == CLEAN_MSG
    assert not [r for r in rows if r["type"] == "front_brain_outbound_refused"]
    for r in rows:
        ADAPTER.validate_python(r)


def test_fail_uses_fallback_template_and_emits_both_rows(enabled):
    out = safe_io.front_brain_screen_gateway_send(CHAT, PROMISE_MSG, fallback_template=FALLBACK)
    assert out == FALLBACK
    rows = _rows()
    refused = [r for r in rows if r["type"] == "front_brain_outbound_refused"]
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert len(refused) == 1 and "promise_ban" in refused[0]["hit_classes"]
    assert any(c["template_fallback"] is True and c["reply_text"] == FALLBACK for c in composed)
    for r in rows:
        ADAPTER.validate_python(r)


def test_budget_trip_sends_template_without_screening(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "0")  # every turn trips
    # A CLEAN message that WOULD pass the screen is still template-substituted,
    # proving the budget gate ran BEFORE the screen (no screen side effects).
    out = safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG, fallback_template=FALLBACK)
    assert out == FALLBACK
    rows = _rows()
    composed = [r for r in rows if r["type"] == "front_brain_reply_composed"]
    assert composed and composed[0]["template_fallback"] is True
    assert composed[0]["reply_text"] == FALLBACK
    # budget trip is NOT a lint refusal
    assert not [r for r in rows if r["type"] == "front_brain_outbound_refused"]


def test_budget_exhausts_after_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "2")
    a = safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG)
    b = safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG)
    c = safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG, fallback_template=FALLBACK)
    assert a == CLEAN_MSG and b == CLEAN_MSG  # within cap -> composed sent
    assert c == FALLBACK  # 3rd turn tripped the per-chat/day cap -> template


def test_compose_timeout_sends_template(enabled, monkeypatch):
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "0.2")

    def _slow(*args, **kwargs):
        time.sleep(2.0)
        return "should never be sent"

    monkeypatch.setattr(safe_io, "_front_brain_outbound_enforce", _slow)
    out = safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG, fallback_template=FALLBACK)
    assert out == FALLBACK
    composed = [r for r in _rows() if r["type"] == "front_brain_reply_composed"]
    assert composed and composed[-1]["template_fallback"] is True


def test_never_blocks_returns_string(enabled):
    for msg in ("", PROMISE_MSG, CLEAN_MSG, "x" * 6000):
        out = safe_io.front_brain_screen_gateway_send(CHAT, msg)
        assert isinstance(out, str)


def test_verified_completion_not_clobbered(enabled):
    from schemas import ActionExecutionContext
    ctx = ActionExecutionContext(
        action_id="commerce.payment.confirm",
        is_regulated_action=True,
        verified_action_result=True,
        audit_row_id="row-9",
    )
    msg = "Your refund of $50 has been processed."
    out = safe_io.front_brain_screen_gateway_send(CHAT, msg, action_context=ctx)
    assert out == msg
    assert not [r for r in _rows() if r["type"] == "front_brain_outbound_refused"]


# ── item 2: jid-duality — screen canonicalizes LID<->phone like admission ─────

def test_lid_outbound_jid_matches_phone_allowlist(monkeypatch, tmp_path):
    # A LID-form outbound jid must be screened when the enforce allowlist holds the
    # PHONE form: front_brain_screen_gateway_send canonicalizes the jid (LID->phone
    # via the lid-cache) BEFORE the enforce check, the SAME way converse-admission
    # does. Without the fix the LID normalizes to its own digits, never matches the
    # phone allowlist, and the reply goes out UN-screened.
    cache = tmp_path / "lid-cache.json"
    cache.write_text(
        '{"schema_version":1,"pairs":[{"phone":"+17329837841","lid":"111222333444@lid"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SHIFT_AGENT_LID_CACHE_PATH", str(cache))
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "+17329837841")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "30")
    out = safe_io.front_brain_screen_gateway_send(
        "111222333444@lid", PROMISE_MSG, fallback_template=FALLBACK
    )
    assert out == FALLBACK  # screened (promise tripped) -> template
    assert [r for r in _rows() if r["type"] == "front_brain_outbound_refused"]


# ── item 7: reserve_budget=False (progressive edit drafts) bypasses budget ────

def test_reserve_budget_false_does_not_consume(monkeypatch, tmp_path):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "*")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "1")
    # progressive drafts screen but consume NO budget
    for _ in range(3):
        assert safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG, reserve_budget=False) == CLEAN_MSG
    # the finalized reply consumes the single unit
    assert safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG) == CLEAN_MSG
    # the NEXT finalized reply trips the exhausted per-chat/day cap -> template
    assert safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG, fallback_template=FALLBACK) == FALLBACK
    # a progressive draft AFTER exhaustion still screens (never budget-gated)
    assert safe_io.front_brain_screen_gateway_send(CHAT, CLEAN_MSG, reserve_budget=False) == CLEAN_MSG
