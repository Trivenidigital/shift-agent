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
    assert safe_io.front_brain_outbound_enforce_enabled("15550100001@c.us") is False


def test_empty_allowlist_disables(monkeypatch):
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "")
    assert safe_io.front_brain_outbound_enforce_enabled("15550100001@c.us") is False


def test_wildcard_graduates_all(monkeypatch):
    _enable(monkeypatch, "*")
    assert safe_io.front_brain_outbound_enforce_enabled("anychat@c.us") is True


def test_membership_normalized(monkeypatch):
    _enable(monkeypatch, "+1 555 010 0001")
    assert safe_io.front_brain_outbound_enforce_enabled("15550100001@c.us") is True
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


# ── owner-directed exemption, bridge_post seam only (incident 2026-10-05..08) ─
# The owner is the control plane, not a customer: after the 2026-10-03 owner
# swap the allowlisted owner identity received the generic ack in place of the
# daily brief every morning. SCRIPTED sends (bridge_post -> exempt_owner=True)
# to a PRIMARY owner identity skip the screen. The predicate is untouched, the
# gateway LLM-reply seam stays screened for the owner, authorized_identities
# stay screened, and every non-owner admitted chat is still screened (the
# witness half of each case).

OWNER_PHONE = "+17329837841"
OWNER_SELF_JID = "17329837841@s.whatsapp.net"
OWNER_LID = "201975216009469@lid"
AUTH_PHONE = "+15550100777"
AUTH_LID = "301975216009469@lid"
CUSTOMER_JID = "15550100001@s.whatsapp.net"
BRIEF_MSG = "Good morning! 2 shifts scheduled today. Quotes sent: 3."


def _write_owner_config(tmp_path, monkeypatch, body: str | None = None) -> Path:
    cfg = tmp_path / "config.yaml"
    if body is None:
        body = (
            "owner:\n"
            "  name: Owner\n"
            f"  phone: '{OWNER_PHONE}'\n"
            f"  self_chat_jid: '{OWNER_SELF_JID}'\n"
            f"  lid: '{OWNER_LID}'\n"
            "  authorized_identities:\n"
            f"    - phone: '{AUTH_PHONE}'\n"
            f"      lid: '{AUTH_LID}'\n"
        )
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(cfg))
    return cfg


def _owner_exempt(jid, msg, **kw):
    return safe_io._front_brain_outbound_enforce(jid, msg, exempt_owner=True, **kw)


def _exempt_rows(monkeypatch) -> list[dict]:
    return [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_owner_exempt_send"]


def _assert_only_owner_exempt_row(monkeypatch, jid: str, msg: str) -> None:
    """An exempt owner send writes exactly ONE row — the positive record of the
    delivered text — and no review/refusal row (the screen did not run)."""
    rows = _read_rows(monkeypatch)
    assert [r["type"] for r in rows] == ["front_brain_owner_exempt_send"], rows
    row = rows[0]
    assert row["message_text"] == msg[:2000]
    assert row["seam"] == "bridge_post"
    assert row["exempt_reason"] == "primary_owner"
    assert row["chat_key_hash"] == safe_io._front_brain_chat_key_hash(jid)
    assert row["send_attempt_id"]
    ADAPTER.validate_python(row)


def test_owner_predicate_untouched(tmp_path, monkeypatch):
    # The admission predicate stays a pure env check: the owner IS admitted.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    assert safe_io.front_brain_outbound_enforce_enabled(OWNER_SELF_JID) is True
    _enable(monkeypatch, "*")
    assert safe_io.front_brain_outbound_enforce_enabled(OWNER_SELF_JID) is True


def test_owner_self_chat_exempt_while_customer_still_screened(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    # TARGET: owner admitted by the allowlist, yet not screened when opted in.
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK) == PROMISE_MSG
    _assert_only_owner_exempt_row(monkeypatch, OWNER_SELF_JID, PROMISE_MSG)
    # WITNESS: the same message to an allowlisted NON-owner chat is screened.
    assert _owner_exempt(CUSTOMER_JID, PROMISE_MSG, fallback_template=FALLBACK) == FALLBACK
    assert [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]
    assert len(_exempt_rows(monkeypatch)) == 1  # the screened send added none


def test_owner_screened_without_opt_in(tmp_path, monkeypatch):
    # exempt_owner defaults False: a caller that does not opt in (the gateway
    # seam, any future caller) screens the owner like anyone else.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, OWNER_PHONE)
    out = safe_io._front_brain_outbound_enforce(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK)
    assert out == FALLBACK
    assert [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]


def test_owner_daily_brief_text_reaches_owner_unchanged(tmp_path, monkeypatch):
    # The incident shape: a "scheduled" / "Quotes sent" brief to the owner.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    assert _owner_exempt(OWNER_SELF_JID, BRIEF_MSG) == BRIEF_MSG
    _assert_only_owner_exempt_row(monkeypatch, OWNER_SELF_JID, BRIEF_MSG)
    # WITNESS: the brief text itself trips the screen when sent to a customer.
    assert _owner_exempt(CUSTOMER_JID, BRIEF_MSG) == safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK


@pytest.mark.parametrize("jid", [
    OWNER_SELF_JID,
    OWNER_LID,
    OWNER_PHONE,
    "17329837841",
    "17329837841@c.us",
])
def test_primary_owner_identity_variants_exempt(tmp_path, monkeypatch, jid):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    assert _owner_exempt(jid, PROMISE_MSG, fallback_template=FALLBACK) == PROMISE_MSG
    _assert_only_owner_exempt_row(monkeypatch, jid, PROMISE_MSG)


@pytest.mark.parametrize("jid", [
    AUTH_PHONE,
    "15550100777@s.whatsapp.net",
    AUTH_LID,
])
def test_authorized_identity_still_screened(tmp_path, monkeypatch, jid):
    # authorized_identities are owner AUTHORIZATION only, never a notification
    # destination: a dual-role manager's customer conversation stays screened.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    assert _owner_exempt(jid, PROMISE_MSG, fallback_template=FALLBACK) == FALLBACK
    assert [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]


def test_wildcard_still_exempts_owner_but_screens_others(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == PROMISE_MSG
    assert _owner_exempt(CUSTOMER_JID, PROMISE_MSG) == safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK


def test_missing_config_no_exemption(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    _enable(monkeypatch, OWNER_PHONE)
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK) == FALLBACK


@pytest.mark.parametrize("body", [
    "owner: [unclosed\n",          # yaml parse error
    "- just\n- a\n- list\n",       # not a mapping
    "owner: null\n",               # owner missing
    "owner:\n  name: Owner\n",     # no identity keys
    "",                            # empty file
])
def test_unreadable_or_partial_config_no_exemption(tmp_path, monkeypatch, body):
    _write_owner_config(tmp_path, monkeypatch, body)
    _enable(monkeypatch, OWNER_PHONE)
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK) == FALLBACK


def test_config_is_directory_no_exemption(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path))
    _enable(monkeypatch, OWNER_PHONE)
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK) == FALLBACK


def test_flag_off_or_not_admitted_performs_no_config_read(tmp_path, monkeypatch):
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(tmp_path / "nonexistent.yaml"))
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == PROMISE_MSG
    # The owner lookup itself is never consulted for OFF / empty / non-admitted.
    lookups: list = []
    monkeypatch.setattr(safe_io, "_front_brain_owner_directed",
                        lambda jid: lookups.append(jid) or True)
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == PROMISE_MSG
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE", "1")
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", "")
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == PROMISE_MSG
    monkeypatch.setenv("FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST", CUSTOMER_JID)
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == PROMISE_MSG
    assert lookups == []
    # Nor when the caller does not opt in, even for an admitted chat.
    safe_io._front_brain_outbound_enforce(CUSTOMER_JID, CLEAN_MSG)
    assert lookups == []
    # WITNESS: an admitted, opted-in chat DOES consult it.
    assert _owner_exempt(CUSTOMER_JID, PROMISE_MSG) == PROMISE_MSG
    assert lookups == [CUSTOMER_JID]


def test_owner_lookup_rereads_config_after_owner_swap(tmp_path, monkeypatch):
    # No caching: an owner swap takes effect on the next send.
    cfg = _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == PROMISE_MSG
    cfg.write_text(
        "owner:\n  name: New\n  phone: '+15550100999'\n"
        "  self_chat_jid: '15550100999@s.whatsapp.net'\n",
        encoding="utf-8",
    )
    assert _owner_exempt(OWNER_SELF_JID, PROMISE_MSG) == safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK
    assert _owner_exempt("15550100999@s.whatsapp.net", PROMISE_MSG) == PROMISE_MSG


def test_gateway_seam_still_screens_owner(tmp_path, monkeypatch):
    # The owner's free-form LLM chat exits via the gateway seam, which does NOT
    # opt in: a PROMISE reply to the owner is still substituted (07-31 owner
    # false-success class).
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "30")
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "4.0")
    out = safe_io.front_brain_screen_gateway_send(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK)
    assert out == FALLBACK
    refused = [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]
    assert len(refused) == 1 and "promise_ban" in refused[0]["hit_classes"]
    # The gateway seam never opts in, so it never records an exempt send either.
    assert _exempt_rows(monkeypatch) == []


def test_gateway_seam_clean_owner_reply_emits_no_exempt_row(tmp_path, monkeypatch):
    # A PASSING owner reply on the gateway seam is screened (review row), not exempt.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    monkeypatch.setenv("FRONT_BRAIN_CHAT_BUDGET_PATH", str(tmp_path / "budget.json"))
    monkeypatch.setenv("FRONT_BRAIN_CHAT_DAILY_CAP", "30")
    monkeypatch.setenv("FRONT_BRAIN_COMPOSE_TIMEOUT_SEC", "4.0")
    assert safe_io.front_brain_screen_gateway_send(OWNER_SELF_JID, CLEAN_MSG) == CLEAN_MSG
    assert [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_reply_composed"]
    assert _exempt_rows(monkeypatch) == []


def _unregulated_ctx():
    # A non-regulated context so the PR-zeta null-context refusal does not end
    # the send before transport (the pytest caller is not on its allowlist).
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="daily_brief.send", is_regulated_action=False, verified_action_result=False,
    )


def _capture_bridge_payloads(monkeypatch) -> list[dict]:
    """Swap urlopen inside safe_io for a sink that records the JSON payload and
    acks with a message id; never touches a real bridge."""
    sent: list[dict] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"id": "fake-mid-1"}'

    def _fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    monkeypatch.setattr(safe_io.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    return sent


def test_bridge_post_to_owner_sends_composed_text(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    sent = _capture_bridge_payloads(monkeypatch)
    ok, _mid, err, status = safe_io.bridge_post(OWNER_SELF_JID, BRIEF_MSG, action_context=_unregulated_ctx())
    assert (ok, status) == (True, "sent"), err
    assert sent == [{"chatId": OWNER_SELF_JID, "message": BRIEF_MSG}]
    # Exactly one positive record of the delivered brief; no review/refusal row.
    _assert_only_owner_exempt_row(monkeypatch, OWNER_SELF_JID, BRIEF_MSG)


def test_bridge_post_owner_exempt_row_records_full_text_capped(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    sent = _capture_bridge_payloads(monkeypatch)
    long_brief = BRIEF_MSG + " " + ("detail " * 400)
    ok, _mid, err, status = safe_io.bridge_post(OWNER_SELF_JID, long_brief, action_context=_unregulated_ctx())
    assert (ok, status) == (True, "sent"), err
    assert sent[-1]["message"] == long_brief  # the send itself is never truncated
    rows = _exempt_rows(monkeypatch)
    assert len(rows) == 1 and rows[0]["message_text"] == long_brief[:2000]


def test_bridge_post_owner_flag_off_emits_no_exempt_row(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    monkeypatch.delenv("FRONT_BRAIN_OUTBOUND_ENFORCE", raising=False)
    _capture_bridge_payloads(monkeypatch)
    safe_io.bridge_post(OWNER_SELF_JID, BRIEF_MSG, action_context=_unregulated_ctx())
    assert not [r for r in _read_rows(monkeypatch) if r["type"].startswith("front_brain_")]


def test_bridge_post_to_customer_still_substituted(tmp_path, monkeypatch):
    # WITNESS for the bridge seam: same config, non-owner chat -> screened.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    sent = _capture_bridge_payloads(monkeypatch)
    safe_io.bridge_post(CUSTOMER_JID, PROMISE_MSG, fallback_template=FALLBACK,
                        action_context=_unregulated_ctx())
    assert sent and sent[-1]["message"] == FALLBACK
    assert [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]
    assert _exempt_rows(monkeypatch) == []


# ── bridge_post(exempt_owner=False): callers relaying arbitrary text opt OUT ──
# send-catering-ack sends a caller-supplied --message-text body. It must not
# inherit the owner exemption: a lint-failing body to the owner is substituted.

def test_bridge_post_exempt_owner_false_screens_owner(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    sent = _capture_bridge_payloads(monkeypatch)
    ok, _mid, err, status = safe_io.bridge_post(
        OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK,
        action_context=_unregulated_ctx(), exempt_owner=False,
    )
    assert (ok, status) == (True, "sent"), err
    assert sent == [{"chatId": OWNER_SELF_JID, "message": FALLBACK}]
    refused = [r for r in _read_rows(monkeypatch) if r["type"] == "front_brain_outbound_refused"]
    assert len(refused) == 1 and "promise_ban" in refused[0]["hit_classes"]
    assert _exempt_rows(monkeypatch) == []


def test_bridge_post_default_still_exempts_owner(tmp_path, monkeypatch):
    # WITNESS for the opt-out: the same call without exempt_owner=False is exempt.
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, f"{OWNER_PHONE},{CUSTOMER_JID}")
    sent = _capture_bridge_payloads(monkeypatch)
    safe_io.bridge_post(OWNER_SELF_JID, PROMISE_MSG, fallback_template=FALLBACK,
                        action_context=_unregulated_ctx())
    assert sent == [{"chatId": OWNER_SELF_JID, "message": PROMISE_MSG}]
    _assert_only_owner_exempt_row(monkeypatch, OWNER_SELF_JID, PROMISE_MSG)


def test_send_catering_ack_opts_out_of_owner_exemption():
    # Static: the script's only send seam is bound with exempt_owner=False, and
    # it still CALLS that seam (the name the e2e harness patches).
    script = (REPO / "src" / "agents" / "catering" / "scripts" / "send-catering-ack").read_text(encoding="utf-8")
    # The aliased import stays so the chokepoint policy scan
    # (tests/test_catering_followup_scripts.py) still sees this script.
    assert "from safe_io import bridge_post as _bridge_post_canonical" in script
    assert "_bridge_post_4tuple = functools.partial(_bridge_post_canonical, exempt_owner=False)" in script
    assert "_bridge_post_4tuple(jid, full_message)" in script
    assert "from safe_io import bridge_post as _bridge_post_4tuple" not in script


def test_bridge_post_to_authorized_identity_still_substituted(tmp_path, monkeypatch):
    _write_owner_config(tmp_path, monkeypatch)
    _enable(monkeypatch, "*")
    sent = _capture_bridge_payloads(monkeypatch)
    safe_io.bridge_post("15550100777@s.whatsapp.net", PROMISE_MSG, fallback_template=FALLBACK,
                        action_context=_unregulated_ctx())
    assert sent and sent[-1]["message"] == FALLBACK
