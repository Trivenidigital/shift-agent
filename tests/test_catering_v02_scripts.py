"""End-to-end tests for catering v0.2 scripts. Linux-only (fcntl).

Tests via subprocess + env-overridable paths, mirroring tests/test_lid_learn.py.
Mocks the WhatsApp bridge with a stub HTTP server.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)

CREATE = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "scripts" / "create-catering-lead"
APPLY = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "templates"


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
    """Build per-test config + state dir + template symlinks."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    templates = tmp_path / "templates"
    state.mkdir()
    logs.mkdir()
    templates.mkdir()
    # Symlink the real templates so the renderer can find them
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


def _env(env_dir, bridge_port):
    return {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src" / "platform"),
    }


def _patch_paths_in_script(script_text: str, env_dir: Path) -> str:
    """Scripts hardcode /opt/shift-agent/* paths. For tests we patch by overriding
    via a thin wrapper that monkeypatches those Path constants. We can't easily
    do that from a subprocess, so instead we run the script with the test
    paths injected via os.environ-readable paths."""
    return script_text  # placeholder — see _run_via_wrapper below


def _run_create(env_dir, bridge_port, fields, customer_phone="+19045550199",
                customer_name="Priya", raw="Need catering 50ppl Saturday",
                message_id="msg_1"):
    """Invoke create-catering-lead via a wrapper that overrides the hardcoded paths."""
    wrapper = f"""
import os, sys, runpy
sys.argv = [
    "create-catering-lead",
    "--customer-phone", {customer_phone!r},
    "--customer-name", {customer_name!r},
    "--raw-inquiry", {raw!r},
    "--message-id", {message_id!r},
    "--fields-json", {json.dumps(fields)!r},
]
import pathlib
# Monkey-patch hardcoded paths before exec
import importlib.util
spec = importlib.util.spec_from_file_location("ccl", {str(CREATE)!r})
mod = importlib.util.module_from_spec(spec)
mod.__name__ = "__main__"
# Pre-inject overrides
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.path.insert(0, str(pathlib.Path({str(Path(__file__).resolve().parent.parent / 'src' / 'platform')!r})))
spec.loader.exec_module(mod)
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, env=_env(env_dir, bridge_port),
        timeout=20,
    )


def _run_apply(env_dir, bridge_port, code, decision, edit_text="", reason=""):
    extra = []
    if edit_text:
        extra += ["--edit-text", edit_text]
    if reason:
        extra += ["--reason", reason]
    wrapper = f"""
import os, sys, runpy
sys.argv = [
    "apply-catering-owner-decision",
    "--code", {code!r},
    "--decision", {decision!r},
] + {extra!r}
import pathlib
import importlib.util
spec = importlib.util.spec_from_file_location("acod", {str(APPLY)!r})
mod = importlib.util.module_from_spec(spec)
mod.__name__ = "__main__"
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.path.insert(0, str(pathlib.Path({str(Path(__file__).resolve().parent.parent / 'src' / 'platform')!r})))
spec.loader.exec_module(mod)
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, env=_env(env_dir, bridge_port),
        timeout=20,
    )


def _read_leads(env_dir):
    p = env_dir / "state" / "catering-leads.json"
    if not p.exists():
        return {"leads": []}
    return json.loads(p.read_text())


def _read_log(env_dir):
    p = env_dir / "logs" / "decisions.log"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ─── Tests ─────────────────────────────────────────────


def test_create_lead_writes_state_and_sends_card(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    fields = {"headcount": 50, "event_date": "2026-06-15", "menu_preferences": ["vegetarian"]}
    r = _run_create(env_dir, port, fields)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["lead_id"] == "L0001"
    assert out["approval_code"].startswith("#") and len(out["approval_code"]) == 6
    assert out["card_sent"] is True
    # State written
    leads = _read_leads(env_dir)
    assert len(leads["leads"]) == 1
    assert leads["leads"][0]["status"] == "AWAITING_OWNER_APPROVAL"
    assert leads["leads"][0]["extracted"]["headcount"] == 50
    # Card sent to owner JID
    assert len(BridgeStub.requests) == 1
    assert BridgeStub.requests[0]["chatId"] == "19045550100@s.whatsapp.net"
    assert out["approval_code"] in BridgeStub.requests[0]["message"]


def test_create_lead_idempotent_replay(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    fields = {"headcount": 30, "event_date": "2026-07-04"}
    r1 = _run_create(env_dir, port, fields, message_id="meta_xyz")
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    r2 = _run_create(env_dir, port, fields, message_id="meta_xyz")
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out1["lead_id"] == out2["lead_id"]
    assert out2.get("idempotent_replay") is True
    assert len(_read_leads(env_dir)["leads"]) == 1
    # Card sent only once
    assert len(BridgeStub.requests) == 1


def test_create_lead_disabled_in_config(env_dir, bridge_server):
    port, _ = bridge_server
    cfg = yaml.safe_load((env_dir / "config.yaml").read_text())
    cfg["catering"]["enabled"] = False
    (env_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = _run_create(env_dir, port, {"headcount": 10})
    assert r.returncode == 2  # EXIT_DISABLED
    assert _read_leads(env_dir)["leads"] == []


def test_create_lead_invalid_fields_json(env_dir, bridge_server):
    port, _ = bridge_server
    fields = {"headcount": -5}  # negative — schema rejects
    r = _run_create(env_dir, port, fields)
    assert r.returncode == 2  # EXIT_INVALID_INPUT


def test_apply_approve_sends_quote_to_customer(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    # 1) Create
    r1 = _run_create(env_dir, port, {"headcount": 25, "event_date": "2026-08-01"},
                     customer_phone="+15551234567", customer_name="Anita")
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    code = out1["approval_code"]
    assert len(BridgeStub.requests) == 1  # owner card

    # 2) Apply approve
    r2 = _run_apply(env_dir, port, code, "approve")
    assert r2.returncode == 0, r2.stderr
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out2["new_status"] == "SENT_TO_CUSTOMER"
    assert out2["outbound_sent"] is True

    # 3) Customer received quote
    assert len(BridgeStub.requests) == 2
    customer_msg = BridgeStub.requests[1]
    assert customer_msg["chatId"] == "15551234567@s.whatsapp.net"
    assert "Anita" in customer_msg["message"] or "L0001" in customer_msg["message"]

    # 4) State machine final
    leads = _read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "SENT_TO_CUSTOMER"

    # 5) Audit trail
    log = _read_log(env_dir)
    types = [e["type"] for e in log]
    assert "catering_lead_created" in types
    assert "catering_owner_decision" in types
    assert "catering_quote_sent" in types
    # Status changes recorded
    transitions = [(e["from_status"], e["to_status"]) for e in log if e["type"] == "catering_lead_status_change"]
    assert ("NEW", "AWAITING_OWNER_APPROVAL") in transitions
    assert ("AWAITING_OWNER_APPROVAL", "OWNER_APPROVED") in transitions
    assert ("OWNER_APPROVED", "SENT_TO_CUSTOMER") in transitions


def test_apply_reject_no_customer_send(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    r1 = _run_create(env_dir, port, {"headcount": 100})
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    initial_requests = len(BridgeStub.requests)
    r2 = _run_apply(env_dir, port, out1["approval_code"], "reject", reason="too big")
    assert r2.returncode == 0, r2.stderr
    leads = _read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "OWNER_REJECTED"
    # Customer NOT messaged on reject
    assert len(BridgeStub.requests) == initial_requests


def test_apply_edit_transitions_to_owner_edited(env_dir, bridge_server):
    port, _ = bridge_server
    r1 = _run_create(env_dir, port, {"headcount": 40})
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    r2 = _run_apply(env_dir, port, out1["approval_code"], "edit",
                    edit_text="make it veg only, keep budget under $500")
    assert r2.returncode == 0, r2.stderr
    leads = _read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "OWNER_EDITED"


def test_apply_unknown_code_exits_not_found(env_dir, bridge_server):
    port, _ = bridge_server
    r = _run_apply(env_dir, port, "#XXXXX", "approve")
    assert r.returncode == 4  # EXIT_NOT_FOUND


def test_apply_invalid_code_format(env_dir, bridge_server):
    port, _ = bridge_server
    r = _run_apply(env_dir, port, "not-a-code", "approve")
    assert r.returncode == 2  # EXIT_INVALID_INPUT


def test_apply_double_approve_rejected(env_dir, bridge_server):
    """After approve + send, the lead is SENT_TO_CUSTOMER — same code can't approve again."""
    port, _ = bridge_server
    r1 = _run_create(env_dir, port, {"headcount": 12})
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    r2 = _run_apply(env_dir, port, out1["approval_code"], "approve")
    assert r2.returncode == 0
    # Second approve — code now applies to a SENT_TO_CUSTOMER lead, not AWAITING_*
    r3 = _run_apply(env_dir, port, out1["approval_code"], "approve")
    assert r3.returncode == 4  # not found in AWAITING_OWNER_APPROVAL
