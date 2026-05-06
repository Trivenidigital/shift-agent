"""Privilege-escalation tests for owner-only catering apply scripts.

Covers test plan cases B-021, D-013, H-008 from
tasks/catering-agent-comprehensive-test-plan.md.

Threat model: an EMPLOYEE (or customer / unknown) gets hold of a `#XXXXX`
code that the owner received — via screenshot, forwarded WhatsApp message,
shared device, etc. — and replies with `#XXXXX yes` (or `approve` / `reject`)
hoping to execute the owner-only operation.

Defense-in-depth layers:
  1. Dispatcher matrix gates by sender_role (caught upstream in
     dispatch_shift_agent SKILL.md).
  2. Apply script `--sender-role` arg required + checked first thing in
     main(). Rejects with EXIT_PRIVILEGE_DENIED (12) before any state
     read/lock so an attacker-controlled --code can't probe the pending
     file.

These tests exercise layer 2 — the script-level guard. They invoke the
apply scripts directly via subprocess + sys.argv and assert exit 12 with
no state mutation when sender_role != "owner".

Trim history (2026-05-06 audit-cleanup): originally 6 test functions
including parametrize over [employee, customer, unknown] on both apply
paths plus a cross-cutting H-008 test plus an argparse-rejects test.
All non-owner roles hit the same privilege-check branch at the same line
in the script; testing one of them (employee) catches any regression
of that branch. argparse `choices=[...]` enforces enum membership for
free. Cross-cutting H-008 is the conjunction of the d013 + b021 employee
cases — no separate test needed. Trimmed to 4 tests / ~150 LOC.
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

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
APPLY_MENU = REPO / "src" / "agents" / "catering" / "scripts" / "apply-menu-update"
APPLY_DECISION = REPO / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"

EXIT_PRIVILEGE_DENIED = 12


@pytest.fixture
def env_dir(tmp_path):
    """Per-test config + state + logs dirs."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _seed_pending_menu_update(env_dir, code="#PRV01"):
    """Write a valid pending-menu-update JSON the apply script can find."""
    pending = {
        "update_id": "MU0099",
        "confirmation_code": code,
        "owner_phone": "+19045550100",
        "source_image_id": "test-priv-esc",
        "extracted_items": [
            {
                "name": "Test Item",
                "price_usd": 9.99,
                "category": "appetizer",
                "dietary_tags": ["veg"],
            }
        ],
        "extracted_at": "2026-05-06T00:00:00-04:00",
        "expires_at": "2026-05-06T04:00:00-04:00",
        "parser_notes": "test",
    }
    (env_dir / "state" / "catering-menu-pending.json").write_text(
        json.dumps(pending), encoding="utf-8"
    )


def _seed_catering_lead(env_dir, code="#PRV02"):
    """Write a valid catering-leads.json with one AWAITING_OWNER_APPROVAL lead."""
    lead = {
        "leads": [
            {
                "lead_id": "L0099",
                "customer_phone": "+19045550199",
                "customer_name": "Priya",
                "raw_inquiry": "50 ppl Saturday",
                "headcount": 50,
                "event_date": "2026-06-01",
                "status": "AWAITING_OWNER_APPROVAL",
                "owner_approval_code": code,
                "created_at": "2026-05-06T00:00:00-04:00",
                "updated_at": "2026-05-06T00:00:00-04:00",
            }
        ]
    }
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps(lead), encoding="utf-8"
    )


def _run_apply_menu(env_dir, code, decision, sender_role):
    """Invoke apply-menu-update via wrapper that overrides path constants."""
    wrapper = f"""
import sys, pathlib
import importlib.machinery, importlib.util

sys.argv = [
    "apply-menu-update",
    "--code", {code!r},
    "--decision", {decision!r},
    "--sender-role", {sender_role!r},
]
sys.path.insert(0, {str(PLATFORM_DIR)!r})
loader = importlib.machinery.SourceFileLoader("amu", {str(APPLY_MENU)!r})
spec = importlib.util.spec_from_file_location("amu", {str(APPLY_MENU)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.MENU_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-menu.json')!r})
mod.MENU_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-menu.json.lock')!r})
mod.PENDING_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-menu-pending.json')!r})
mod.PENDING_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-menu-pending.json.lock')!r})
mod.ARCHIVE_DIR = pathlib.Path({str(env_dir / 'state' / 'catering-menu-archive')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(PLATFORM_DIR)},
    )


def _run_apply_decision(env_dir, bridge_port, code, decision, sender_role):
    """Invoke apply-catering-owner-decision similarly."""
    wrapper = f"""
import sys, pathlib
import importlib.machinery, importlib.util

sys.argv = [
    "apply-catering-owner-decision",
    "--code", {code!r},
    "--decision", {decision!r},
    "--sender-role", {sender_role!r},
    "--reason", "test",
]
sys.path.insert(0, {str(PLATFORM_DIR)!r})
loader = importlib.machinery.SourceFileLoader("acod", {str(APPLY_DECISION)!r})
spec = importlib.util.spec_from_file_location("acod", {str(APPLY_DECISION)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir)!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(PLATFORM_DIR)},
    )


# ─────────────────────────────────────────────────────────────────
# D-013: employee tries to approve a menu update via #XXXXX
# ─────────────────────────────────────────────────────────────────


def test_d013_employee_cannot_apply_menu_update(env_dir):
    """Employee sender_role must be rejected with EXIT_PRIVILEGE_DENIED.

    Critical: pending file MUST still exist after the rejected call (no
    state probe / leak via timing or pending-file mutation).

    NOTE: customer/unknown roles hit the same privilege-check branch at the
    SAME line in the script; not separately tested per the audit-cleanup
    trim (2026-05-06). argparse `choices=[...]` enforces the enum membership
    so any non-owner role takes this path.
    """
    _seed_pending_menu_update(env_dir, code="#PRV01")
    pending_before = (env_dir / "state" / "catering-menu-pending.json").read_text()
    menu_before_exists = (env_dir / "state" / "catering-menu.json").exists()

    result = _run_apply_menu(
        env_dir, code="#PRV01", decision="yes", sender_role="employee",
    )

    assert result.returncode == EXIT_PRIVILEGE_DENIED, (
        f"expected EXIT_PRIVILEGE_DENIED (12), got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "privilege denied" in result.stderr.lower()
    # Pending file untouched
    pending_after = (env_dir / "state" / "catering-menu-pending.json").read_text()
    assert pending_after == pending_before
    # No menu.json was written (would indicate state mutation)
    assert (env_dir / "state" / "catering-menu.json").exists() == menu_before_exists


def test_d013_owner_role_can_still_apply_menu_update(env_dir):
    """Positive control: owner role passes the privilege gate.

    Asserts only that we don't get exit 12 — the rest of the apply path
    has its own coverage in test_catering_v02_scripts.py.
    """
    _seed_pending_menu_update(env_dir, code="#PRV01")

    result = _run_apply_menu(env_dir, code="#PRV01", decision="yes", sender_role="owner")

    assert result.returncode != EXIT_PRIVILEGE_DENIED, (
        f"owner unexpectedly hit privilege gate. stderr: {result.stderr}"
    )


def test_d013_no_pending_file_probe_on_priv_denied(env_dir):
    """When sender_role != owner, the script must reject BEFORE reading the
    pending file. Verify by passing a code that doesn't match (would normally
    return EXIT_NOT_FOUND=4) — privilege denial must short-circuit first."""
    _seed_pending_menu_update(env_dir, code="#REAL1")

    # Non-matching code from a non-owner — privilege check fires before
    # the file lookup. Result must be EXIT_PRIVILEGE_DENIED, not EXIT_NOT_FOUND.
    result = _run_apply_menu(
        env_dir, code="#FAKE2", decision="yes", sender_role="employee"
    )

    assert result.returncode == EXIT_PRIVILEGE_DENIED, (
        f"privilege check must fire BEFORE file lookup. "
        f"got {result.returncode}, stderr: {result.stderr}"
    )


# ─────────────────────────────────────────────────────────────────
# B-021: employee tries to approve an owner-only catering lead
# ─────────────────────────────────────────────────────────────────


class _BridgeStub(BaseHTTPRequestHandler):
    """Stub for apply-catering-owner-decision's bridge POST (unused in priv-denied
    paths but the script imports/configures the URL regardless)."""
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.__class__.requests.append({})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": "msg_x"}).encode())

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


def test_b021_employee_cannot_approve_catering_lead(env_dir, bridge_server):
    """Employee sender_role rejected on apply-catering-owner-decision.

    Critical: the bridge MUST NOT receive any POST (would mean a customer
    quote was sent on behalf of a non-authorized sender).

    NOTE: customer/unknown roles hit the same privilege-check branch at the
    SAME line; not separately tested per the audit-cleanup trim
    (2026-05-06). H-008 ("employee blocked at both apply paths") is also
    not separately tested — the employee case here + the d013 employee
    case above together pin the H-008 invariant.
    """
    port, stub = bridge_server
    _seed_catering_lead(env_dir, code="#PRV02")
    leads_before = (env_dir / "state" / "catering-leads.json").read_text()

    result = _run_apply_decision(
        env_dir, port, code="#PRV02", decision="reject", sender_role="employee",
    )

    assert result.returncode == EXIT_PRIVILEGE_DENIED, (
        f"expected EXIT_PRIVILEGE_DENIED (12), got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert "privilege denied" in result.stderr.lower()
    # Lead state untouched
    assert (env_dir / "state" / "catering-leads.json").read_text() == leads_before
    # Bridge never invoked — no quote sent under non-owner identity
    assert len(stub.requests) == 0
