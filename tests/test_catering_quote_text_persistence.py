"""M3 — the exact customer-sent quote text is persisted on the lead.

Verified gap this closes: `apply-catering-owner-decision` NEVER wrote quote_text.
All three of its model_copy calls updated status/updated_at only, so the text the
customer actually received existed nowhere durable and every later "what did we
quote them?" had to be reconstructed from the transcript. The CateringLead
docstring has asserted this contract since PR-CF1 ("quote_text ... populated ONLY
by apply-catering-owner-decision on owner approve") — these cells make it true and
keep it true.

Windows-runnable: in-process via fixtures_fleet.load_script with the fcntl stub.
"""
from __future__ import annotations

import io
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
SEED_QUOTE_TEXT = "pre-approval placeholder text"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []
    fail: bool = False

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            self.__class__.requests.append(json.loads(body))
        except Exception:
            self.__class__.requests.append({})
        if self.__class__.fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server(monkeypatch):
    _BridgeStub.requests = []
    _BridgeStub.fail = False
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True, "proposal_validity_days": 14},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _seed(env_dir, *, status="CUSTOMER_FINALIZED"):
    lead = {
        "lead_id": "L0001", "status": status,
        "customer_phone": "+19045550199", "customer_name": "Asha Rao",
        "raw_inquiry": "wedding catering", "original_message_id": "msg_orig",
        "created_at": "2026-07-20T10:00:00-04:00",
        "updated_at": "2026-07-25T10:00:00-04:00",
        "extracted": {"headcount": 50, "event_date": "2026-09-15"},
        "quote_text": SEED_QUOTE_TEXT,
        "quote_total_usd": 400,
        "selected_items": [{"name": "Veg Biryani", "qty": 4, "price_usd": 100}],
        "customer_finalized_at": "2026-07-25T11:00:00-04:00",
        "owner_approval_code": "#ABCDE",
    }
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead]}), encoding="utf-8")


def _read_lead(env_dir):
    doc = json.loads((env_dir / "state" / "catering-leads.json").read_text(encoding="utf-8"))
    return doc["leads"][0]


def _sent_bodies(bridge):
    return [r.get("message", "") for r in bridge.requests]


def _run(env_dir, *argv, stdin_text=""):
    mod = load_script("acod_quote_text_mod", APPLY)
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    mod.LEDGER_PATH = env_dir / "state" / "catering-quote-ledger.json"
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = ["apply-catering-owner-decision", "--code", "#ABCDE",
                "--sender-role", "owner", *argv]
    sys.stdin = io.StringIO(stdin_text)
    try:
        return mod.main()
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin


_DRAFT = ("Hi Asha, for your event on 2026-09-15 we'll cater for 50 guests. "
          "Total $400. Reply here to confirm.")


# ── the approve path persists what was sent ─────────────────────────────────
def test_approve_persists_the_drafted_quote_on_the_lead(bridge_server, env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--decision", "approve", "--quote-text-stdin",
                stdin_text=_DRAFT) == 0
    lead = _read_lead(env_dir)
    assert lead["status"] == "SENT_TO_CUSTOMER"
    assert lead["quote_text"] != SEED_QUOTE_TEXT, "the placeholder must be replaced"
    assert "2026-09-15" in lead["quote_text"]
    assert "50 guests" in lead["quote_text"]


def test_the_persisted_text_is_exactly_what_the_customer_received(bridge_server, env_dir):
    """Modulo the bridge template_bypass header, which is transport plumbing added
    server-side after normalize — not quote content."""
    _seed(env_dir)
    _run(env_dir, "--decision", "approve", "--quote-text-stdin", stdin_text=_DRAFT)
    persisted = _read_lead(env_dir)["quote_text"]
    sent = _sent_bodies(bridge_server)[-1]
    assert persisted in sent
    assert sent.endswith(persisted), "only the prefix may differ"


def test_the_persisted_text_is_the_normalized_form(bridge_server, env_dir):
    """PR-B v3 normalize strips markdown delimiters and control characters. What is
    stored must be the post-normalize body the truth-guard actually validated, not
    the raw stdin."""
    _seed(env_dir)
    _run(env_dir, "--decision", "approve", "--quote-text-stdin",
         stdin_text="*Hi* Asha, event 2026-09-15 for 50 guests. _Total $400._")
    persisted = _read_lead(env_dir)["quote_text"]
    assert "*" not in persisted and "_" not in persisted


def test_server_rendered_quote_is_persisted_too(bridge_server, env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--decision", "approve", "--quote-from-lead-state") == 0
    persisted = _read_lead(env_dir)["quote_text"]
    assert "Veg Biryani" in persisted
    assert "L0001" in persisted


def test_the_persisted_quote_states_its_validity_period(bridge_server, env_dir):
    """A stored quote with no shelf life is the same gap on the durable side."""
    _seed(env_dir)
    _run(env_dir, "--decision", "approve", "--quote-from-lead-state")
    assert "valid until" in _read_lead(env_dir)["quote_text"]


# ── the failure and no-send paths ───────────────────────────────────────────
def test_a_failed_bridge_post_still_leaves_the_approved_text_on_the_lead(
    bridge_server, env_dir,
):
    """The lead is already OWNER_APPROVED with delivery uncertain; the operator
    reconciling it needs to see WHAT would have gone out."""
    _seed(env_dir)
    bridge_server.fail = True
    rc = _run(env_dir, "--decision", "approve", "--quote-text-stdin", stdin_text=_DRAFT)
    assert rc == 6  # EXIT_DEPENDENCY_DOWN
    lead = _read_lead(env_dir)
    assert lead["status"] == "OWNER_APPROVED"
    assert "2026-09-15" in lead["quote_text"]


def test_reject_does_not_touch_quote_text(bridge_server, env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--decision", "reject", "--reason", "no capacity") == 0
    assert _read_lead(env_dir)["quote_text"] == SEED_QUOTE_TEXT


def test_edit_does_not_overwrite_quote_text_with_the_edit_instruction(
    bridge_server, env_dir,
):
    """"--edit-text 'make it $400 cap'" is an instruction to us, not a quote to the
    customer. Storing it as quote_text would put owner shorthand in the field that
    is supposed to hold what the customer read."""
    _seed(env_dir)
    assert _run(env_dir, "--decision", "edit", "--edit-text", "make it a $400 cap") == 0
    assert _read_lead(env_dir)["quote_text"] == SEED_QUOTE_TEXT


def test_a_truth_guard_rejection_persists_nothing(bridge_server, env_dir):
    """Nothing was sent, so nothing is the customer-received text."""
    _seed(env_dir)
    rc = _run(env_dir, "--decision", "approve", "--quote-text-stdin",
              stdin_text="Thanks! We'll be in touch soon.")
    assert rc == 11  # EXIT_TRUTH_GUARD_FAILED
    assert _read_lead(env_dir)["quote_text"] == SEED_QUOTE_TEXT


# ── static: PR-B v3 constraints still hold ──────────────────────────────────
def test_apply_script_still_reads_no_menu():
    """The persistence change must not reintroduce catalog reads PR-B v3 removed."""
    src = APPLY.read_text(encoding="utf-8")
    assert "MENU_PATH" not in src
