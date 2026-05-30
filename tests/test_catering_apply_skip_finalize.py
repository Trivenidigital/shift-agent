"""PR-CF1 — apply-catering-owner-decision owner-approve guard tests.

3 cases pinning the R3.BC1 BLOCKER coverage gap:
  1. Pre-finalize lead: approve without --skip-finalize blocks (rc=11),
     audit row written, no state mutation.
  2. Pre-finalize lead: approve with --skip-finalize bypasses guard,
     state advances to OWNER_APPROVED (or SENT_TO_CUSTOMER on success).
  3. CUSTOMER_FINALIZED lead: approve without --skip-finalize succeeds
     (guard does NOT fire — lead is already finalized).

Linux-only — apply-script transitively imports safe_io which uses fcntl.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="apply-script imports safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
APPLY = REPO / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"
PLATFORM_DIR = REPO / "src" / "platform"
TEMPLATES_DIR = REPO / "src" / "agents" / "catering" / "templates"


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
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}_{len(self.__class__.requests)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server():
    _BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, _BridgeStub
    finally:
        server.shutdown()


@pytest.fixture
def env_dir(tmp_path):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    templates = tmp_path / "templates"
    state.mkdir(); logs.mkdir(); templates.mkdir()
    for f in TEMPLATES_DIR.iterdir():
        (templates / f.name).symlink_to(f.absolute())
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


def _seed_lead(env_dir, *, status="AWAITING_OWNER_APPROVAL",
               customer_finalized_at=None, selected_items=None,
               quote_total_usd=None, last_finalize_message_id=None,
               headcount=50, event_date="2026-06-15"):
    lead = {
        "lead_id": "L0001", "status": status,
        "customer_phone": "+19045550199", "customer_name": "Test Customer",
        "raw_inquiry": "test inquiry", "original_message_id": "msg_orig",
        "created_at": "2026-04-30T10:00:00-04:00",
        "updated_at": "2026-04-30T10:00:00-04:00",
        "extracted": {
            "headcount": headcount, "event_date": event_date,
            "event_time": None, "menu_preferences": [], "off_menu_items": [],
            "dietary_restrictions": [], "delivery_or_pickup": "delivery",
            "budget_hint_usd": None, "notes": "",
        },
        "quote_text": "proposal text Ref test",
        "quote_version": 0, "owner_approval_code": "#ABCDE",
        "customer_replied": False,
        "selected_items": selected_items or [],
        "quote_total_usd": quote_total_usd,
        "customer_finalized_at": customer_finalized_at,
        "last_finalize_message_id": last_finalize_message_id,
    }
    store = {"leads": [lead], "next_lead_seq": 2}
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps(store), encoding="utf-8")


def _read_lead(env_dir):
    p = env_dir / "state" / "catering-leads.json"
    if not p.exists():
        return None
    store = json.loads(p.read_text(encoding="utf-8"))
    return store["leads"][0] if store.get("leads") else None


def _read_audit(env_dir):
    log_file = env_dir / "logs" / "decisions.log"
    if not log_file.exists():
        return []
    return [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]


def _good_quote(headcount=50, event_date="2026-06-15"):
    """Quote text passing the truth-guard (headcount + ISO date present)."""
    return (
        f"Hi Test Customer, thanks for your inquiry. For your event on "
        f"Saturday ({event_date}) we'll prepare a buffet for {headcount} "
        f"guests. Reply here to confirm. (Ref: L0001)"
    )


def _run_apply(env_dir, bridge_port, *, decision="approve", skip_finalize=False,
               quote_text=""):
    extra = []
    if skip_finalize:
        extra.append("--skip-finalize")
    if decision == "approve":
        extra.append("--quote-text-stdin")
    sys_argv = [
        "apply-catering-owner-decision",
        "--code", "#ABCDE",
        "--decision", decision,
        "--sender-role", "owner",
    ] + extra
    wrapper = f"""
import sys, pathlib
import importlib.machinery, importlib.util

sys.argv = {sys_argv!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
loader = importlib.machinery.SourceFileLoader("acod", {str(APPLY)!r})
spec = importlib.util.spec_from_file_location("acod", {str(APPLY)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        input=quote_text,
        capture_output=True, text=True, timeout=20,
        # send-path-test-harness: override the CANONICAL safe_io.BRIDGE_URL (via
        # its env source) to the local stub, and opt past the pytest bridge
        # guard. The wrapper loads the real apply-catering-owner-decision script,
        # so the caller resolves to an allowlisted basename (chokepoint passes).
        # Stub port (not :3000) so the live-bridge tripwire stays dormant.
        env={**os.environ,
             "HERMES_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}/send",
             "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1"},
    )


# ============================================================================
# R3.BC1 — apply-script PR-CF1 guard tests
# ============================================================================


def test_pre_finalize_approve_blocks_with_exit_11(bridge_server, env_dir):
    """Lead in AWAITING_OWNER_APPROVAL with customer_finalized_at=None.
    Plain approve (no --skip-finalize) MUST refuse with rc=11, audit row,
    no state mutation.
    """
    port, stub = bridge_server
    _seed_lead(env_dir, customer_finalized_at=None)
    before = json.loads((env_dir / "state" / "catering-leads.json").read_text())
    result = _run_apply(env_dir, port,
        decision="approve", quote_text=_good_quote())
    assert result.returncode == 11, f"stderr: {result.stderr}"
    after = json.loads((env_dir / "state" / "catering-leads.json").read_text())
    assert before == after  # NO state mutation
    audit = [r for r in _read_audit(env_dir)
             if r["type"] == "catering_quote_skill_failed"]
    assert len(audit) >= 1
    assert any(
        r.get("reason") == "owner_approve_without_customer_finalize"
        for r in audit
    ), f"expected guard reason in {audit}"
    # Reprompt was POSTed to bridge
    assert len(stub.requests) >= 1
    reprompt = stub.requests[0]["message"]
    # R2.H1 fix: reprompt must NOT instruct WhatsApp '--skip-finalize'
    assert "--skip-finalize" not in reprompt
    # Should mention cockpit override path
    assert "cockpit" in reprompt.lower()


def test_pre_finalize_approve_with_skip_finalize_bypasses_guard(bridge_server, env_dir):
    """--skip-finalize override allows owner to approve a non-finalized lead."""
    port, _ = bridge_server
    _seed_lead(env_dir, customer_finalized_at=None)
    result = _run_apply(env_dir, port,
        decision="approve", skip_finalize=True, quote_text=_good_quote())
    assert result.returncode == 0, f"stderr: {result.stderr}"
    lead = _read_lead(env_dir)
    # Lead advanced to OWNER_APPROVED or SENT_TO_CUSTOMER
    assert lead["status"] in {"OWNER_APPROVED", "SENT_TO_CUSTOMER"}


def test_customer_finalized_lead_approves_without_skip_flag(bridge_server, env_dir):
    """CUSTOMER_FINALIZED lead approves without --skip-finalize. Guard
    does NOT fire because customer_finalized_at is set.
    """
    port, _ = bridge_server
    _seed_lead(env_dir,
        status="CUSTOMER_FINALIZED",
        customer_finalized_at="2026-04-30T11:00:00-04:00",
        selected_items=[{"name": "Aloo Paratha", "qty": 10, "price_usd": 4}],
        quote_total_usd=40,
        last_finalize_message_id="msg_v1",
    )
    result = _run_apply(env_dir, port,
        decision="approve", quote_text=_good_quote())
    assert result.returncode == 0, f"stderr: {result.stderr}"
    lead = _read_lead(env_dir)
    assert lead["status"] in {"OWNER_APPROVED", "SENT_TO_CUSTOMER"}
    # No guard audit row — guard didn't fire
    audit = [r for r in _read_audit(env_dir)
             if r.get("reason") == "owner_approve_without_customer_finalize"]
    assert len(audit) == 0
