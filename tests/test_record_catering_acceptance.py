"""M3 — `record-catering-acceptance`: the customer's accept/decline write path.

Before M3 nothing advanced a lead past SENT_TO_CUSTOMER except an operator script,
so a customer saying "we accept" left no booking anywhere. These cells pin the
whole turn: transition, acceptance fields, audit rows, owner card, customer reply,
idempotency, the one-clarification rule, and the M4 hold interaction.

Windows-runnable: the script is loaded IN-PROCESS via fixtures_fleet.load_script
with the fcntl stub (advisory locking is a no-op in one test process), mirroring
tests/test_catering_apply_lead_hold.py rather than the older subprocess wrappers,
so these assertions actually run on both platforms instead of skipping off-Linux.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402

SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "record-catering-acceptance"
OWNER_JID = "19045550100@s.whatsapp.net"
CUSTOMER_JID = "19045550199@s.whatsapp.net"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []
    # P17: which safe_io.bridge_post status the next POST should produce.
    #   "ok"          -> 200 + {"id": ...}                    -> status "sent"
    #   "http_error"  -> 500                                  -> status "http_error"
    #   "empty_id"    -> 200 + {} (accepted, no id)           -> status "send_uncertain"
    #   "bad_ack"     -> 200 + non-JSON body                  -> status "send_uncertain"
    # The two uncertain shapes are both exercised because they enter safe_io on
    # different branches (empty_message_id vs ack_parse_failed).
    response_mode = "ok"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except Exception:
            doc = {}
        self.__class__.requests.append(doc)
        mode = self.__class__.response_mode
        if mode == "http_error":
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if mode == "empty_id":
            self.wfile.write(json.dumps({"ok": True}).encode())
        elif mode == "bad_ack":
            self.wfile.write(b"<html>gateway said something else</html>")
        else:
            self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture(autouse=True)
def _rebind_safe_io_to_live_module():
    """Order-determinism: an earlier suite's loader may have popped "safe_io"
    from sys.modules and re-imported a FRESH object (Linux-only loaders do).
    This file's top-level ``import safe_io`` would then hold a STALE object —
    patching BRIDGE_URL on it silently misses the object the script under test
    imports. Re-resolve the live module before every test (mirrors
    test_front_brain_outbound_enforcement.py)."""
    global safe_io
    import sys as _sys
    live = _sys.modules.get("safe_io")
    if live is not None:
        safe_io = live
    yield


@pytest.fixture
def bridge_server(monkeypatch):
    """Local stub on an ephemeral port (never :3000, so the live-bridge tripwire
    stays dormant) wired into the canonical safe_io.BRIDGE_URL."""
    _BridgeStub.requests = []
    _BridgeStub.response_mode = "ok"
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{server.server_port}/send"
    monkeypatch.setenv("HERMES_BRIDGE_URL", url)
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io, "BRIDGE_URL", url, raising=False)
    try:
        yield _BridgeStub
    finally:
        server.shutdown()


@pytest.fixture
def env_dir(tmp_path):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": OWNER_JID},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _seed_lead(env_dir, *, status="SENT_TO_CUSTOMER", on_hold=False, **over):
    lead = {
        "lead_id": "L0001", "status": status,
        "customer_phone": "+19045550199", "customer_name": "Asha Rao",
        "raw_inquiry": "catering for a wedding", "original_message_id": "msg_orig",
        "created_at": "2026-07-20T10:00:00-04:00",
        "updated_at": "2026-07-25T10:00:00-04:00",
        "extracted": {"headcount": 80, "event_date": "2026-09-15"},
        "quote_text": "Quote for L0001 ... 80 guests ... 2026-09-15",
        "quote_version": 1, "owner_approval_code": "#ABCDE",
        "on_hold": on_hold, "hold_reason": "customer asked us to wait" if on_hold else None,
        "hold_set_at": "2026-07-30T12:00:00-04:00" if on_hold else None,
    }
    lead.update(over)
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead], "next_lead_seq": 2}), encoding="utf-8")


def _read_lead(env_dir):
    store = json.loads((env_dir / "state" / "catering-leads.json").read_text(encoding="utf-8"))
    return store["leads"][0]


def _read_audit(env_dir):
    log_file = env_dir / "logs" / "decisions.log"
    if not log_file.exists():
        return []
    return [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]


def _rows(env_dir, type_):
    return [r for r in _read_audit(env_dir) if r["type"] == type_]


def _run(env_dir, text, *, message_id="wamid.1", sender_role="customer",
         notify_sink=None):
    """`notify_sink`, when given, collects the argv of every
    `shift-agent-notify-owner` invocation instead of shelling out — the owner
    page is the P17 assertion surface and the real binary is absent under test."""
    mod = load_script("record_catering_acceptance_mod", SCRIPT)
    if notify_sink is not None:
        import subprocess as _sp

        _real_run = _sp.run

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(argv, **kwargs):
            if str(argv[0]) == str(mod.NOTIFY_OWNER_BIN):
                notify_sink.append([str(part) for part in argv])
                return _Result()
            return _real_run(argv, **kwargs)

        mod.subprocess.run = _fake_run
        _restore = (mod.subprocess, _real_run)
    else:
        _restore = None
    try:
        return _run_loaded(mod, env_dir, text, message_id, sender_role)
    finally:
        if _restore is not None:
            _restore[0].run = _restore[1]


def _run_loaded(mod, env_dir, text, message_id, sender_role):
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    mod.NOTIFY_OWNER_BIN = env_dir / "no-such-notify-binary"
    old_argv = sys.argv
    sys.argv = [
        "record-catering-acceptance", "--lead-id", "L0001",
        "--customer-jid", CUSTOMER_JID, "--customer-message-id", message_id,
        "--message-text", text, "--sender-role", sender_role,
    ]
    try:
        return mod.main()
    finally:
        sys.argv = old_argv


def _sent_to(bridge, jid):
    """Bridge payloads are {"chatId": ..., "message": ...} (safe_io.bridge_post)."""
    return [r for r in bridge.requests if r.get("chatId") == jid]


# ── ACCEPT ──────────────────────────────────────────────────────────────────
def test_explicit_accept_books_the_lead(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "we accept") == 0
    lead = _read_lead(env_dir)
    assert lead["status"] == "BOOKED"
    assert lead["customer_acceptance"] == "accepted"
    assert lead["acceptance_at"]
    assert lead["acceptance_message_id"] == "wamid.1"


def test_accept_writes_both_audit_rows(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "we accept")
    change = _rows(env_dir, "catering_lead_status_change")[-1]
    assert change["from_status"] == "SENT_TO_CUSTOMER"
    assert change["to_status"] == "BOOKED"
    assert change["actor"] == "customer"
    recorded = _rows(env_dir, "catering_lead_acceptance_recorded")[-1]
    assert recorded["outcome"] == "accepted"
    assert recorded["to_status"] == "BOOKED"
    assert recorded["customer_message_id"] == "wamid.1"
    assert "accept" in recorded["matched_phrase"].lower()


def test_accept_notifies_the_owner_and_replies_to_the_customer(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "we accept")
    owner = _sent_to(bridge_server, OWNER_JID)
    assert owner, "the owner must be told their event was booked"
    assert "ACCEPTED" in json.dumps(owner)
    assert "L0001" in json.dumps(owner)
    assert _sent_to(bridge_server, CUSTOMER_JID), "the customer gets an acknowledgment"


def test_customer_reply_promises_no_price_or_payment(bridge_server, env_dir):
    """A booking acknowledgment must not imply a payment obligation nobody quoted."""
    _seed_lead(env_dir)
    _run(env_dir, "we accept")
    body = json.dumps(_sent_to(bridge_server, CUSTOMER_JID)).lower()
    for forbidden in ("deposit", "$", "payment", "invoice", "pay now"):
        assert forbidden not in body, f"customer acknowledgment leaked {forbidden!r}"


# ── DECLINE ─────────────────────────────────────────────────────────────────
def test_explicit_decline_closes_the_lead(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "we went with someone else") == 0
    lead = _read_lead(env_dir)
    assert lead["status"] == "CLOSED"
    assert lead["customer_acceptance"] == "declined"


def test_decline_reason_rides_on_the_audit_row(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "we went with someone else")
    change = _rows(env_dir, "catering_lead_status_change")[-1]
    assert change["to_status"] == "CLOSED"
    assert "went with" in change["reason"]
    recorded = _rows(env_dir, "catering_lead_acceptance_recorded")[-1]
    assert recorded["outcome"] == "declined"
    assert "someone else" in recorded["detail"]


def test_decline_notifies_the_owner(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "we decline")
    assert "DECLINED" in json.dumps(_sent_to(bridge_server, OWNER_JID))


# ── AMBIGUOUS: ask once, never book, never repeat ───────────────────────────
def test_ambiguous_reply_changes_no_state_and_asks_once(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "ok") == 0
    lead = _read_lead(env_dir)
    assert lead["status"] == "SENT_TO_CUSTOMER", "an 'ok' must never book an event"
    assert lead["customer_acceptance"] is None
    assert lead["acceptance_clarification_sent_message_id"] == "wamid.1"
    reply = json.dumps(_sent_to(bridge_server, CUSTOMER_JID))
    assert "Just to confirm" in reply


def test_ambiguous_records_a_clarification_sent_audit_row(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "sounds good")
    recorded = _rows(env_dir, "catering_lead_acceptance_recorded")[-1]
    assert recorded["outcome"] == "clarification_sent"
    assert recorded["from_status"] == recorded["to_status"] == "SENT_TO_CUSTOMER"
    assert _rows(env_dir, "catering_lead_status_change") == []


def test_clarification_is_asked_exactly_once(bridge_server, env_dir):
    """A second "shall we book?" reads as the agent nagging about money."""
    _seed_lead(env_dir)
    _run(env_dir, "ok", message_id="wamid.1")
    first = len(_sent_to(bridge_server, CUSTOMER_JID))
    assert _run(env_dir, "great", message_id="wamid.2") == 0
    assert len(_sent_to(bridge_server, CUSTOMER_JID)) == first, "asked twice"
    assert len(_rows(env_dir, "catering_lead_acceptance_recorded")) == 1


def test_accept_after_a_clarification_still_books(bridge_server, env_dir):
    """The clarification is a question, not a terminal state."""
    _seed_lead(env_dir)
    _run(env_dir, "ok", message_id="wamid.1")
    assert _run(env_dir, "yes let's book it", message_id="wamid.2") == 0
    assert _read_lead(env_dir)["status"] == "BOOKED"


# ── idempotency ─────────────────────────────────────────────────────────────
def test_redelivered_accept_is_a_noop_replay(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "we accept", message_id="wamid.1")
    owner_sends = len(_sent_to(bridge_server, OWNER_JID))
    audit_rows = len(_read_audit(env_dir))

    assert _run(env_dir, "we accept", message_id="wamid.1") == 0
    assert _read_lead(env_dir)["status"] == "BOOKED"
    assert len(_sent_to(bridge_server, OWNER_JID)) == owner_sends, "owner told twice"
    assert len(_read_audit(env_dir)) == audit_rows, "replay wrote audit rows"


def test_a_second_different_accept_does_not_overwrite_the_first(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir, "we accept", message_id="wamid.1")
    assert _run(env_dir, "we decline", message_id="wamid.2") == 0
    lead = _read_lead(env_dir)
    assert lead["status"] == "BOOKED", "a booked event is not un-booked by automation"
    assert lead["customer_acceptance"] == "accepted"


# ── eligibility + privilege ─────────────────────────────────────────────────
@pytest.mark.parametrize("status", [
    "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED", "OWNER_APPROVED",
    "OWNER_EDITED", "CLOSED", "STALE",
])
def test_acceptance_is_refused_outside_sent_to_customer(bridge_server, env_dir, status):
    _seed_lead(env_dir, status=status)
    assert _run(env_dir, "we accept") == 4  # EXIT_NOT_FOUND
    assert _read_lead(env_dir)["status"] == status


def test_owner_message_can_never_book_on_the_customers_behalf(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "we accept", sender_role="owner") == 12  # EXIT_PRIVILEGE_DENIED
    assert _read_lead(env_dir)["status"] == "SENT_TO_CUSTOMER"
    assert bridge_server.requests == []


def test_unknown_lead_is_not_found(bridge_server, env_dir):
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": []}), encoding="utf-8")
    assert _run(env_dir, "we accept") == 4


def test_text_with_no_signal_is_refused_so_the_caller_routes_normally(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "can you send the menu again") == 2  # EXIT_INVALID_INPUT
    assert _read_lead(env_dir)["status"] == "SENT_TO_CUSTOMER"
    assert bridge_server.requests == []


# ── M4 hold interaction ─────────────────────────────────────────────────────
def test_held_lead_still_records_the_acceptance_but_withholds_the_customer_reply(
    bridge_server, env_dir,
):
    """A hold withholds customer SENDS. Losing what the customer actually said
    would be a data-integrity failure a hold was never meant to cause."""
    _seed_lead(env_dir, on_hold=True)
    assert _run(env_dir, "we accept") == 0
    assert _read_lead(env_dir)["status"] == "BOOKED"
    assert _sent_to(bridge_server, CUSTOMER_JID) == []
    assert _sent_to(bridge_server, OWNER_JID), "owner-directed sends are never held"
    blocked = _rows(env_dir, "catering_lead_hold_blocked_send")[-1]
    assert blocked["blocked_send_kind"] == "customer_ack"
    assert blocked["hold_reason"] == "customer asked us to wait"


def test_held_lead_does_not_ask_the_clarification_and_does_not_burn_it(
    bridge_server, env_dir,
):
    """The clarification IS a customer send, so a hold suppresses it entirely —
    and must not mark it as asked, or the question would be lost forever."""
    _seed_lead(env_dir, on_hold=True)
    assert _run(env_dir, "ok") == 14  # EXIT_LEAD_ON_HOLD
    lead = _read_lead(env_dir)
    assert lead.get("acceptance_clarification_sent_message_id") is None
    assert _sent_to(bridge_server, CUSTOMER_JID) == []


# ── static invariants ───────────────────────────────────────────────────────
def test_script_uses_the_shared_grammar_and_the_transition_table():
    t = SCRIPT.read_text(encoding="utf-8")
    assert "detect_quote_acceptance" in t
    assert "is_catering_transition_allowed" in t
    assert "CateringLeadAcceptanceRecorded" in t
    assert "atomic_write_json" in t


def test_script_calls_no_llm():
    """This books events; the grammar must stay deterministic."""
    t = SCRIPT.read_text(encoding="utf-8").lower()
    for token in ("openrouter", "llm_call", "gateway_complete", "hermes_complete"):
        assert token not in t


# ── P17: send-status truthfulness on already-committed state ────────────────
# The BOOKED write, the acceptance audit rows and the owner card ALL land before
# the customer reply is attempted. When that reply does not land, the lead used to
# keep asserting a confirmed booking while the customer had heard nothing, and the
# only trace was a line on stderr.

def test_booked_but_failed_customer_send_marks_lead_and_pages_owner(bridge_server, env_dir):
    _seed_lead(env_dir)
    _BridgeStub.response_mode = "http_error"
    pages: list = []

    rc = _run(env_dir, "we accept", notify_sink=pages)

    lead = _read_lead(env_dir)
    assert lead["status"] == "BOOKED", "the booking itself is never rolled back by a send"
    assert lead["customer_ack_status"] == "failed"
    assert lead["customer_ack_status_at"]
    rows = _rows(env_dir, "catering_customer_send_unconfirmed")
    assert len(rows) == 1, "exactly one row for the one unconfirmed send"
    assert rows[0]["delivery_certainty"] == "failed"
    assert rows[0]["send_kind"] == "acceptance_reply"
    assert rows[0]["send_status"] == "http_error"
    assert rows[0]["script"] == "record-catering-acceptance"
    assert rows[0]["owner_paged"] is True
    body = " ".join(pages[-1])
    assert "NOT told" in body, f"the page must say the customer heard nothing: {body}"
    assert rows[0]["lead_id"] == "L0001"
    assert rc == 6


def test_failed_customer_send_does_not_retry(bridge_server, env_dir):
    """No auto-retry anywhere on this path — one attempt, then tell a human."""
    _seed_lead(env_dir)
    _BridgeStub.response_mode = "http_error"

    _run(env_dir, "we accept", notify_sink=[])

    assert len(_sent_to(bridge_server, CUSTOMER_JID)) == 1


@pytest.mark.parametrize("mode", ["empty_id", "bad_ack"])
def test_uncertain_customer_send_is_marked_uncertain_not_failed(bridge_server, env_dir, mode):
    """`send_uncertain` means the bridge ACCEPTED it, so it most likely arrived.
    Recording that as a flat failure would invite a duplicate booking message."""
    _seed_lead(env_dir)
    _BridgeStub.response_mode = mode
    pages: list = []

    _run(env_dir, "we accept", notify_sink=pages)

    lead = _read_lead(env_dir)
    assert lead["status"] == "BOOKED"
    assert lead["customer_ack_status"] == "uncertain", (
        "an accepted-but-unconfirmed send must not be recorded as a definite failure")
    rows = _rows(env_dir, "catering_customer_send_unconfirmed")
    assert rows[-1]["delivery_certainty"] == "uncertain"
    assert rows[-1]["send_status"] == "send_uncertain"
    body = " ".join(pages[-1])
    assert "do NOT resend" in body, f"the page must warn against duplicating: {body}"
    assert len(_sent_to(bridge_server, CUSTOMER_JID)) == 1, "never re-sent"


def test_successful_customer_send_records_sent_and_no_unconfirmed_row(bridge_server, env_dir):
    """Success path unchanged, plus the marker now says so durably — None must
    keep meaning 'this path never ran', not 'nobody checked'."""
    _seed_lead(env_dir)

    rc = _run(env_dir, "we accept")

    lead = _read_lead(env_dir)
    assert rc == 0
    assert lead["status"] == "BOOKED"
    assert lead["customer_ack_status"] == "sent"
    assert _rows(env_dir, "catering_customer_send_unconfirmed") == []


def test_declined_lead_also_records_the_send_disposition(bridge_server, env_dir):
    """The decline confirmation is the same class of promise as the acceptance."""
    _seed_lead(env_dir)
    _BridgeStub.response_mode = "http_error"

    _run(env_dir, "we decline", notify_sink=[])

    lead = _read_lead(env_dir)
    assert lead["status"] == "CLOSED"
    assert lead["customer_ack_status"] == "failed"
    assert _rows(env_dir, "catering_customer_send_unconfirmed")[-1]["send_kind"] == "acceptance_reply"
