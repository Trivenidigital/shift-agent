"""M4/G4 — apply-catering-owner-decision refuses `approve` on a held lead.

The refusal happens BEFORE any state mutation. Refusing after the transition
would leave the lead in OWNER_APPROVED with an undelivered quote — a half-state
indistinguishable from a bridge failure, which PR-D2's retry state-machine would
re-attempt forever against a hold only an operator can lift.

Windows-runnable: the script is loaded IN-PROCESS via fixtures_fleet.load_script
with the fcntl stub (FileLock's advisory lock is a no-op in one test process),
rather than the subprocess wrapper the older apply-script suites use. Same
module-attribute path overrides, but the assertions actually run on both
platforms instead of skipping off-Linux.
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

APPLY = REPO / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except Exception:
            doc = {}
        self.__class__.requests.append(doc)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server(monkeypatch):
    """Local stub on an ephemeral port (never :3000, so the live-bridge tripwire
    stays dormant) wired into the canonical safe_io.BRIDGE_URL."""
    _BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}/send"
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
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _seed_lead(env_dir, *, on_hold=False, hold_reason=None):
    lead = {
        "lead_id": "L0001", "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199", "customer_name": "Test Customer",
        "raw_inquiry": "test inquiry", "original_message_id": "msg_orig",
        "created_at": "2026-07-30T10:00:00-04:00",
        "updated_at": "2026-07-30T10:00:00-04:00",
        "extracted": {
            "headcount": 50, "event_date": "2026-09-15",
            "event_time": None, "menu_preferences": [], "off_menu_items": [],
            "dietary_restrictions": [], "delivery_or_pickup": "delivery",
            "budget_hint_usd": None, "notes": "",
        },
        "quote_text": "proposal text Ref test",
        "quote_version": 0, "owner_approval_code": "#ABCDE",
        "customer_replied": False,
        "customer_finalized_at": "2026-07-30T11:00:00-04:00",
        "on_hold": on_hold, "hold_reason": hold_reason,
        "hold_set_at": "2026-07-30T12:00:00-04:00" if on_hold else None,
    }
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


def _good_quote():
    """Passes the truth-guard: headcount integer + ISO event_date present."""
    return (
        "Hi Test Customer, thanks for your inquiry. For your event on "
        "2026-09-15 we'll prepare a buffet for 50 guests. Reply here to "
        "confirm. (Ref: L0001)"
    )


def _run_apply(env_dir, monkeypatch, *, decision="approve", quote_text=""):
    mod = load_script("acod_hold", APPLY)
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"

    argv = ["apply-catering-owner-decision", "--code", "#ABCDE",
            "--decision", decision, "--sender-role", "owner"]
    if decision == "approve":
        argv.append("--quote-text-stdin")

    import io
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = argv
    sys.stdin = io.StringIO(quote_text)
    try:
        return mod.main()
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin


def test_held_lead_refuses_approve_before_any_state_change(
    bridge_server, env_dir, monkeypatch, capsys,
):
    _seed_lead(env_dir, on_hold=True, hold_reason="customer asked us to wait")

    rc = _run_apply(env_dir, monkeypatch, quote_text=_good_quote())

    assert rc == 14  # EXIT_LEAD_ON_HOLD
    assert bridge_server.requests == []                  # nothing sent to the customer
    lead = _read_lead(env_dir)
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"   # untouched
    assert lead["on_hold"] is True
    row = [r for r in _read_audit(env_dir)
           if r["type"] == "catering_lead_hold_blocked_send"][-1]
    assert row["lead_id"] == "L0001"
    assert row["blocked_send_kind"] == "owner_approved_quote"
    assert row["hold_reason"] == "customer asked us to wait"
    # tells the owner how to clear it rather than dead-ending
    assert "set-catering-lead-hold" in capsys.readouterr().err


def test_no_status_change_row_is_written_for_a_held_lead(
    bridge_server, env_dir, monkeypatch,
):
    """Refusing before the mutation means no lifecycle row either — the lead
    never entered OWNER_APPROVED, so nothing downstream should think it did."""
    _seed_lead(env_dir, on_hold=True, hold_reason="wait")
    _run_apply(env_dir, monkeypatch, quote_text=_good_quote())
    types_ = {r["type"] for r in _read_audit(env_dir)}
    assert "catering_lead_status_change" not in types_
    assert "catering_quote_attempted" not in types_


def test_unheld_lead_approves_and_sends(bridge_server, env_dir, monkeypatch):
    _seed_lead(env_dir, on_hold=False)

    rc = _run_apply(env_dir, monkeypatch, quote_text=_good_quote())

    assert rc == 0
    assert len(bridge_server.requests) == 1
    assert _read_lead(env_dir)["status"] == "SENT_TO_CUSTOMER"
    assert not any(r["type"] == "catering_lead_hold_blocked_send"
                   for r in _read_audit(env_dir))


def test_hold_does_not_block_reject(bridge_server, env_dir, monkeypatch):
    """A hold withholds CUSTOMER sends. Reject sends nothing to the customer, so
    the owner can still close a held lead."""
    _seed_lead(env_dir, on_hold=True, hold_reason="wait")

    rc = _run_apply(env_dir, monkeypatch, decision="reject")

    assert rc == 0
    assert _read_lead(env_dir)["status"] == "OWNER_REJECTED"


def test_clearing_the_hold_lets_approve_through(bridge_server, env_dir, monkeypatch):
    _seed_lead(env_dir, on_hold=True, hold_reason="wait")
    assert _run_apply(env_dir, monkeypatch, quote_text=_good_quote()) == 14

    path = env_dir / "state" / "catering-leads.json"
    store = json.loads(path.read_text(encoding="utf-8"))
    store["leads"][0]["on_hold"] = False
    store["leads"][0]["hold_reason"] = None
    path.write_text(json.dumps(store), encoding="utf-8")

    assert _run_apply(env_dir, monkeypatch, quote_text=_good_quote()) == 0
    assert len(bridge_server.requests) == 1
