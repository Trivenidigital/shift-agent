"""In-process apply-expense-decision tests via importlib + attribute injection.

D-H2 + D-H3 fix: addresses the "no apply-decision in-process tests" gap from
Stage 7 PR review. Mirrors test_catering_v02_scripts.py pattern exactly:
  - importlib.util.spec_from_file_location to load the script as a module
  - mod.__name__ = sentinel to suppress __main__ block
  - module-attribute injection of CONFIG_PATH / LEADS_PATH / LOG_PATH /
    TEMPLATE_DIR / BRIDGE_URL / customer_now (frozen)
  - Stub HTTP server for bridge POSTs
  - call mod.main() directly

Linux-only via pytestmark — fcntl is not on Windows.

Edge cases (from plan §4g):
  #3  re-approval idempotency
  #4  undo within window
  #5  undo outside window
  #11 approval-code collision (regenerate)
  #14 audit-chain partial failure (orphan reconcile)

Plus reviewer-c HIGH C1, C3 + reviewer-b HIGH B2 verifications.
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="apply-expense-decision uses safe_io fcntl — Linux only",
)


APPLY_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "expense_bookkeeper" / "scripts"
    / "apply-expense-decision"
)
TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "expense_bookkeeper" / "templates"
)


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


@pytest.fixture(autouse=True)
def _opt_in_bridge_sends(monkeypatch):
    """send-path-test-harness: these in-process apply tests exercise the send
    path (owner reply) and must opt past the pytest bridge guard. No
    guard-refuse test lives in this file, so a file-scoped opt-in is safe (does
    not weaken the guard). The canonical safe_io.BRIDGE_URL is pointed at the
    per-test stub inside _load_apply; apply-expense-decision is an allowlisted
    null-context caller; stub ports (not :3000) keep the live-bridge tripwire
    dormant."""
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")


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
    state = tmp_path / "state" / "expense-bookkeeper"
    state.mkdir(parents=True)
    receipts = state / "receipts"
    receipts.mkdir(mode=0o700)
    logs = tmp_path / "logs"
    logs.mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    for f in TEMPLATES_DIR.iterdir():
        (templates / f.name).symlink_to(f.absolute())

    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net",
                  "lid": "201975216009469@lid"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {
            "enabled": True,
            "cockpit_threshold_cents": 5000,
            "qbo_client_mode": "mock",
        },
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _load_apply(env_dir, bridge_port):
    """Load apply-expense-decision as a module, inject test paths.
    Mirrors test_catering_v02_scripts.py:97-167.

    Note: tests that need frozen time use injected received_at / pushed_at
    on seeded leads — the script uses datetime.now(timezone.utc) directly
    and isn't easily freezable without deeper refactor."""
    # Uses SourceFileLoader explicitly because the script has no .py
    # extension — Python 3.12 spec_from_file_location returns None for
    # unrecognised suffixes (E2E Layer A finding 2026-05-01).
    from importlib.machinery import SourceFileLoader
    os.environ["EXPENSE_RECEIPTS_DIR"] = str(env_dir / "state" / "expense-bookkeeper" / "receipts") + "/"
    loader = SourceFileLoader("apply_expense_decision_test", str(APPLY_PATH))
    spec = importlib.util.spec_from_loader("apply_expense_decision_test", loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "apply_expense_decision_test"  # suppress __main__
    loader.exec_module(mod)
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.LEADS_PATH = env_dir / "state" / "expense-bookkeeper" / "leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "expense-bookkeeper" / "leads.json.lock"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    mod.TEMPLATE_DIR = env_dir / "templates"
    mod.BRIDGE_URL = f"http://127.0.0.1:{bridge_port}/send"
    # F2 (E2E Layer B fix): isolate the mock-qbo ledger per-test so tests
    # don't write to the production state path. In-process tests still see
    # cross-process behaviour via this file.
    mod.MOCK_QBO_STATE_PATH = env_dir / "state" / "expense-bookkeeper" / "mock-qbo-pushed.json"
    # send-path-test-harness: point the CANONICAL safe_io.BRIDGE_URL (the one
    # bridge_post_2tuple actually reads) at this test's stub. mod.BRIDGE_URL
    # above is vestigial post-PR-ε. The conftest fake-sink autouse resets
    # safe_io.BRIDGE_URL each test, so this per-test override does not leak.
    import safe_io as _safe_io
    _safe_io.BRIDGE_URL = f"http://127.0.0.1:{bridge_port}/send"
    return mod


def _seed_mock_qbo_ledger(env_dir, transaction_id, amount_cents, *, seq=1):
    """Seed the per-test mock-QBO ledger so void_transaction(transaction_id)
    finds the txn (matches MockQBOClient state schema v1). Lets the undo
    happy-path succeed without driving a full approve→push first. Mirrors the
    JSON shape MockQBOClient._save_state writes."""
    from datetime import datetime, timezone
    path = env_dir / "state" / "expense-bookkeeper" / "mock-qbo-pushed.json"
    payload = {
        "schema_version": 1,
        "seq": seq,
        "transactions": {
            transaction_id: {
                "transaction_id": transaction_id,
                "amount_cents": amount_cents,
                "pushed_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed_lead(env_dir, **overrides):
    """Write one lead to leads.json with sensible defaults."""
    leads_path = env_dir / "state" / "expense-bookkeeper" / "leads.json"
    receipts_dir = env_dir / "state" / "expense-bookkeeper" / "receipts"
    image_path = receipts_dir / "E0001.jpg"
    image_path.write_bytes(b"fake jpeg bytes")
    image_path.chmod(0o600)

    base = {
        "expense_id": "E0001",
        "original_message_id": "wa_msg_xyz",
        "sender_phone": "+19045550100",
        "received_at": "2026-04-29T12:00:00+00:00",
        "image_path": str(image_path),
        "image_phash": "a3f2c19d8b5e4067",
        "image_byte_hash": "a" * 64,
        "owner_approval_code": "#A47C2",
        "extracted_total_cents": 23450,
        "status": "AWAITING_OWNER_APPROVAL",
        "qbo_account": "COGS - Groceries",
        "extraction": {
            "vendor_name": "Patel Bros",
            "vendor_normalized": "Patel Bros",
            "line_items": [{"description": "groceries", "amount_cents": 23450}],
            "total_cents": 23450,
            "extraction_confidence": 0.92,
        },
        "classification": {
            "is_business": True,
            "confidence": 0.9,
            "rationale": "grocery for restaurant",
            "qbo_account": "COGS - Groceries",
        },
    }
    base.update(overrides)
    store = {"schema_version": 1, "leads": [base], "last_id": 1}
    leads_path.write_text(json.dumps(store), encoding="utf-8")


def _read_audit(env_dir):
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def _audit_types(env_dir):
    return [e["type"] for e in _read_audit(env_dir)]


# ───────────────────────────────────────────────────
# parser fn (in-process, fast)
# ───────────────────────────────────────────────────

def test_parser_handles_reversed_missing_decimals(env_dir, bridge_server):
    """C-H2 fix: '234 #A47C2' (reversed missing decimals) gets a friendly nudge."""
    port, _ = bridge_server
    mod = _load_apply(env_dir, port)
    parsed = mod.parse_owner_message("234 #A47C2")
    assert parsed is not None
    assert parsed["verb"] == "missing_decimals"
    assert parsed["code"] == "#A47C2"


def test_parser_handles_forward_missing_decimals(env_dir, bridge_server):
    port, _ = bridge_server
    mod = _load_apply(env_dir, port)
    parsed = mod.parse_owner_message("#A47C2 234")
    assert parsed is not None
    assert parsed["verb"] == "missing_decimals"


# ───────────────────────────────────────────────────
# Edge case #1 — wrong amount approval (UX H1 already covered;
# this is the apply-decision-level behaviour test)
# ───────────────────────────────────────────────────

def test_wrong_amount_keeps_lead_awaiting(env_dir, bridge_server):
    """Owner replies with #A47C2 100.50 when extracted is 234.50.
    Lead must stay AWAITING_OWNER_APPROVAL; mismatch nudge sent."""
    port, stub = bridge_server
    _seed_lead(env_dir)
    mod = _load_apply(env_dir, port)

    # Inject CLI args
    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C2 100.50",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    assert rc == mod.EXIT_AMOUNT_MISMATCH

    # Lead still AWAITING
    leads = json.loads((env_dir / "state" / "expense-bookkeeper" / "leads.json").read_text())
    assert leads["leads"][0]["status"] == "AWAITING_OWNER_APPROVAL"

    # Audit captures mismatch
    types = _audit_types(env_dir)
    assert "expense_owner_decision" in types

    # Owner got a mismatch reply
    assert len(stub.requests) == 1
    msg = stub.requests[0].get("message", "")
    assert "doesn't match" in msg.lower() or "100.50" in msg


# ───────────────────────────────────────────────────
# Edge case #3 — re-approval idempotency
# (re-sending same #CODE 234.50 after first push should not double-push;
#  lead is in PUSHED status by then, find_lead_by_code returns None)
# ───────────────────────────────────────────────────

def test_reapproval_after_push_returns_not_found(env_dir, bridge_server):
    port, stub = bridge_server
    _seed_lead(env_dir, status="PUSHED",
               qbo_transaction_id="MOCK-E0001-1",
               owner_confirmed_total_cents=23450)
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C2 234.50",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    # _find_lead_by_code excludes PUSHED leads (approval-flow-closed)
    assert rc == mod.EXIT_NOT_FOUND


# ───────────────────────────────────────────────────
# Edge case #4 — undo within window (B-H2 fix tested too)
# ───────────────────────────────────────────────────

def test_undo_within_window_succeeds(env_dir, bridge_server):
    port, stub = bridge_server
    pushed_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _seed_lead(env_dir, status="PUSHED",
               qbo_transaction_id="MOCK-E0001-1",
               pushed_at=pushed_at,
               owner_confirmed_total_cents=23450)
    # Seed the mock-QBO ledger so the txn is voidable. MockQBOClient (state_path
    # mode) only voids transactions present in its ledger — i.e. ones a prior
    # push_expense recorded. Seeding the ledger here mirrors that prior push so
    # the within-window undo actually succeeds (matches the test name).
    _seed_mock_qbo_ledger(env_dir, "MOCK-E0001-1", 23450)
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "undo E0001",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    assert rc == mod.EXIT_OK


def test_undo_outside_window_requires_force(env_dir, bridge_server):
    port, stub = bridge_server
    pushed_at = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    _seed_lead(env_dir, status="PUSHED",
               qbo_transaction_id="MOCK-E0001-1",
               pushed_at=pushed_at,
               owner_confirmed_total_cents=23450)
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "undo E0001",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    assert rc == mod.EXIT_OUTSIDE_WINDOW

    # Audit captured outside-window reversal request
    types = _audit_types(env_dir)
    assert "expense_reversal_requested" in types
    entries = [e for e in _read_audit(env_dir) if e["type"] == "expense_reversal_requested"]
    assert entries[0]["within_window"] is False


# ───────────────────────────────────────────────────
# B-H2 — LID-only owner undo path
# ───────────────────────────────────────────────────

def test_lid_only_owner_can_undo(env_dir, bridge_server):
    """B-H2 fix: owner sends from LID-only inbound (no sender_phone).
    Must still match cfg.owner.lid and proceed to window-check, NOT silently
    decline as non-owner."""
    port, stub = bridge_server
    pushed_at = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    _seed_lead(env_dir, status="PUSHED",
               qbo_transaction_id="MOCK-E0001-1",
               pushed_at=pushed_at,
               owner_confirmed_total_cents=23450)
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "undo E0001",
                "--sender-phone", "",  # empty phone
                "--sender-lid", "201975216009469@lid"]  # matches cfg.owner.lid
    rc = mod.main()
    # Should reach window-check (not silently decline)
    assert rc == mod.EXIT_OUTSIDE_WINDOW

    types = _audit_types(env_dir)
    assert "expense_non_owner_undo_declined" not in types, (
        "B-H2: LID-only owner was incorrectly rejected as non-owner"
    )


def test_non_owner_undo_gets_friendly_reply(env_dir, bridge_server):
    """C-H3 fix: staff member typing 'undo' on a shared phone gets a reply,
    not silence."""
    port, stub = bridge_server
    pushed_at = datetime.now(timezone.utc).isoformat()
    _seed_lead(env_dir, status="PUSHED",
               qbo_transaction_id="MOCK-E0001-1",
               pushed_at=pushed_at,
               owner_confirmed_total_cents=23450)
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "undo E0001",
                "--sender-phone", "+19045550999"]  # NOT the owner phone
    rc = mod.main()
    assert rc == mod.EXIT_INVALID_INPUT

    # Owner re-auth audit AND friendly reply BOTH present
    types = _audit_types(env_dir)
    assert "expense_non_owner_undo_declined" in types
    assert len(stub.requests) >= 1
    msg = stub.requests[-1].get("message", "")
    assert "owner" in msg.lower()


# ───────────────────────────────────────────────────
# C-H1 — silent threshold no-op fix
# ───────────────────────────────────────────────────

def test_above_threshold_approve_without_force_audited(env_dir, bridge_server):
    """C-H1 fix: approve hitting an above-threshold lead WITHOUT force must
    write expense_owner_decision(decision='force_required') AND send a
    template reply telling owner to add 'force'. NOT silent no-op."""
    port, stub = bridge_server
    _seed_lead(env_dir, extracted_total_cents=10000)  # > 5000 threshold
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C2 100.00",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    assert rc == mod.EXIT_OK  # not a failure, but action required

    # Lead still AWAITING (no premature push)
    leads = json.loads((env_dir / "state" / "expense-bookkeeper" / "leads.json").read_text())
    assert leads["leads"][0]["status"] == "AWAITING_OWNER_APPROVAL"

    # AUDITED — not silent
    entries = _read_audit(env_dir)
    decision_entries = [e for e in entries if e["type"] == "expense_owner_decision"]
    assert len(decision_entries) == 1
    assert decision_entries[0]["decision"] == "force_required"
    assert decision_entries[0]["force_context"] == "threshold"

    # Owner got a templated reply (not inline f-string) with 'force' + threshold
    assert len(stub.requests) == 1
    msg = stub.requests[-1].get("message", "")
    assert "force" in msg.lower()
    # Re-review (c) HIGH: owner-facing copy must NOT contain "cockpit" jargon
    assert "cockpit" not in msg.lower(), (
        "owner-facing message leaks internal 'cockpit' jargon"
    )
    # Templated message includes the actual threshold dollar amount
    assert "50.00" in msg


def test_dedup_only_approve_without_force_audited(env_dir, bridge_server):
    """Re-review (c) MED test gap: dedup-only branch (under threshold but
    is a duplicate) must also audit + reply with template."""
    port, stub = bridge_server
    # Under threshold ($30 < $50); marked as duplicate of E0019
    _seed_lead(env_dir, extracted_total_cents=3000, duplicate_of="E0019")
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C2 30.00",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    assert rc == mod.EXIT_OK

    entries = _read_audit(env_dir)
    decision_entries = [e for e in entries if e["type"] == "expense_owner_decision"]
    assert len(decision_entries) == 1
    assert decision_entries[0]["decision"] == "force_required"
    assert decision_entries[0]["force_context"] == "dedup"

    msg = stub.requests[-1].get("message", "")
    assert "force" in msg.lower()
    assert "duplicate" in msg.lower()
    assert "E0019" in msg


def test_threshold_and_dedup_approve_without_force_audited(env_dir, bridge_server):
    """Re-review (c) MED test gap: 'both' branch (above threshold AND
    duplicate) must use the combined template."""
    port, stub = bridge_server
    _seed_lead(env_dir, extracted_total_cents=10000, duplicate_of="E0019")
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C2 100.00",
                "--sender-phone", "+19045550100"]
    rc = mod.main()
    assert rc == mod.EXIT_OK

    entries = _read_audit(env_dir)
    decision_entries = [e for e in entries if e["type"] == "expense_owner_decision"]
    assert len(decision_entries) == 1
    assert decision_entries[0]["force_context"] == "both"

    msg = stub.requests[-1].get("message", "")
    assert "force" in msg.lower()
    assert "duplicate" in msg.lower()
    assert "E0019" in msg
    assert "high-value" in msg.lower() or "review threshold" in msg.lower()


# ───────────────────────────────────────────────────
# A-H1 — orphan persistence
# ───────────────────────────────────────────────────

def test_orphan_flag_persists_across_calls(env_dir, bridge_server):
    """A-H1 fix: when _check_orphans flags a lead (APPROVED_PENDING_PUSH > 60s
    with no completion entry), the reconcile_required flag must be persisted
    to leads.json BEFORE any early return."""
    port, stub = bridge_server
    # Seed a lead in APPROVED_PENDING_PUSH with old approval timestamp
    old_approved = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _seed_lead(env_dir,
               status="APPROVED_PENDING_PUSH",
               owner_approval_received_at=old_approved,
               owner_confirmed_total_cents=23450)
    mod = _load_apply(env_dir, port)

    # Trigger any code path that calls _check_orphans (e.g. unrelated approve)
    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C3 99.99",  # valid-format code, matches no lead (reaches _check_orphans)
                "--sender-phone", "+19045550100"]
    mod.main()

    # First call: orphan should be flagged + audited + persisted
    leads = json.loads((env_dir / "state" / "expense-bookkeeper" / "leads.json").read_text())
    assert leads["leads"][0]["reconcile_required"] is True, (
        "A-H1: reconcile_required flag was not persisted to disk"
    )

    types = _audit_types(env_dir)
    assert "expense_orphan_detected" in types

    # Second call: should NOT re-detect (already flagged → skipped)
    initial_count = sum(1 for e in _read_audit(env_dir) if e["type"] == "expense_orphan_detected")
    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C3 99.99",
                "--sender-phone", "+19045550100"]
    mod.main()
    second_count = sum(1 for e in _read_audit(env_dir) if e["type"] == "expense_orphan_detected")
    assert second_count == initial_count, (
        "A-H1: orphan was re-detected on second call (flag persisted incorrectly)"
    )


def test_in_flight_push_not_flagged_as_orphan(env_dir, bridge_server):
    """A-H2 fix: APPROVED_PENDING_PUSH leads YOUNGER than ORPHAN_STALE_SECONDS
    (60s) should NOT be flagged. They're legitimately in-flight."""
    port, stub = bridge_server
    # owner_approval_received_at = now → in-flight
    fresh_approved = datetime.now(timezone.utc).isoformat()
    _seed_lead(env_dir,
               status="APPROVED_PENDING_PUSH",
               owner_approval_received_at=fresh_approved,
               owner_confirmed_total_cents=23450,
               # Seed the model default explicitly so the not-flagged assertion
               # below is well-defined: a young (in-flight) lead is never touched
               # by _check_orphans, so the key would otherwise be absent and the
               # strict `is False` index would KeyError.
               reconcile_required=False)
    mod = _load_apply(env_dir, port)

    sys.argv = [str(APPLY_PATH),
                "--raw-message", "#A47C3 99.99",
                "--sender-phone", "+19045550100"]
    mod.main()

    leads = json.loads((env_dir / "state" / "expense-bookkeeper" / "leads.json").read_text())
    assert leads["leads"][0]["reconcile_required"] is False, (
        "A-H2: in-flight push was incorrectly flagged as orphan"
    )

    types = _audit_types(env_dir)
    assert "expense_orphan_detected" not in types
