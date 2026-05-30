"""PR-CF1 — finalize-catering-menu E2E tests. Linux-only (fcntl).

16 cases covering the customer-finalize flow:
  Happy paths (3): clean finalize, quote within tolerance, re-finalize
  Validation (4): mismatch outside tolerance, item not in menu, malformed
                  JSON, lead in NEW status (not actionable)
  Error paths (3): lead not found, terminal state, bridge unreachable
  Idempotency (2): replay same message_id, replay within 60s suppresses card
  Concurrency (1): two parallel finalize calls serialize cleanly
  Menu drift (1): item price changed since brainstorm — uses current
  Edge (2): menu file missing, lead with null owner_approval_code

All assertions are order-agnostic where ordering is non-deterministic.
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
SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "finalize-catering-menu"
CREATE = REPO / "src" / "agents" / "catering" / "scripts" / "create-catering-lead"
TEMPLATES_DIR = REPO / "src" / "agents" / "catering" / "templates"
PLATFORM_DIR = REPO / "src" / "platform"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []
    response_mode = "ok"  # "ok" | "down"

    def do_POST(self):
        if self.__class__.response_mode == "down":
            self.send_response(500)
            self.end_headers()
            return
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
    _BridgeStub.response_mode = "ok"
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
    """Per-test config + state dir + templates + menu."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    templates = tmp_path / "templates"
    state.mkdir()
    logs.mkdir()
    templates.mkdir()
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


def _seed_menu(env_dir: Path, items: list[dict]) -> None:
    """Write a catering-menu.json fixture."""
    menu = {
        "version": 1,
        "updated_at": "2026-04-30T10:00:00-04:00",
        "updated_by": "manual",  # schema-allowed: 'photo-ocr', 'manual', or E.164
        "source_image_id": "test",
        "items": items,
    }
    (env_dir / "state" / "catering-menu.json").write_text(
        json.dumps(menu), encoding="utf-8",
    )


def _seed_lead(
    env_dir: Path, lead_id: str, code: str, status: str = "AWAITING_OWNER_APPROVAL",
    customer_phone: str = "+19045550199", customer_finalized_at=None,
    last_finalize_message_id=None, selected_items=None, quote_total_usd=None,
) -> None:
    """Write a catering-leads.json fixture with one lead."""
    lead = {
        "lead_id": lead_id, "status": status,
        "customer_phone": customer_phone, "customer_name": "Test Customer",
        "raw_inquiry": "test inquiry", "original_message_id": "msg_orig",
        "created_at": "2026-04-30T10:00:00-04:00",
        "updated_at": "2026-04-30T10:00:00-04:00",
        "extracted": {
            "headcount": 50, "event_date": "2026-06-15",
            "event_time": None, "menu_preferences": [], "off_menu_items": [],
            "dietary_restrictions": [], "delivery_or_pickup": "delivery",
            "budget_hint_usd": None, "notes": "",
        },
        "quote_text": "proposal text Ref test",
        "quote_version": 0, "owner_approval_code": code,
        "customer_replied": False,
        "selected_items": selected_items or [],
        "quote_total_usd": quote_total_usd,
        "customer_finalized_at": customer_finalized_at,
        "last_finalize_message_id": last_finalize_message_id,
    }
    store = {"leads": [lead], "next_lead_seq": 2}
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps(store), encoding="utf-8",
    )


def _run_script(env_dir, bridge_port, **kwargs):
    """Invoke finalize-catering-menu via importlib wrapper that overrides
    hardcoded paths and BRIDGE_URL.
    """
    args_dict = {
        "code": kwargs.get("code", "#ABCDE"),
        "customer_message_id": kwargs.get("customer_message_id", "msg_finalize_001"),
        "selected_items_json": kwargs.get("selected_items_json", '[{"name":"Aloo Paratha","qty":2,"price_usd":4}]'),
        "quote_total_usd": kwargs.get("quote_total_usd", 8),
    }
    sys_argv = [
        "finalize-catering-menu",
        "--code", args_dict["code"],
        "--customer-message-id", args_dict["customer_message_id"],
        "--selected-items-json", args_dict["selected_items_json"],
        "--quote-total-usd", str(args_dict["quote_total_usd"]),
    ]
    wrapper = f"""
import sys, pathlib, json, io
sys.argv = {sys_argv!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
# Script has no .py extension — use SourceFileLoader
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("fcm_test_loaded", {str(SCRIPT)!r}).load_module()
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.MENU_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-menu.json')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.LOG_LOCK = pathlib.Path({str(env_dir / 'logs' / 'decisions.log.lock')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
buf = io.StringIO()
sys.stdout = buf
rc = -99
try:
    rc = mod.main()
except SystemExit as se:
    rc = se.code if isinstance(se.code, int) else -1
finally:
    sys.stdout = sys.__stdout__
print(json.dumps({{"rc": rc, "stdout": buf.getvalue()}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15,
        # send-path-test-harness: canonical safe_io.BRIDGE_URL -> stub (via env)
        # + opt past the pytest guard. Caller resolves to the allowlisted
        # finalize-catering-menu script; stub port keeps the tripwire dormant.
        env={**os.environ,
             "HERMES_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}/send",
             "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1"},
    )
    out_lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    parsed = json.loads(out_lines[-1]) if out_lines else {"rc": -1, "stdout": ""}
    return result, parsed


def _read_lead(env_dir, code):
    """Returns the lead dict matching code, or None."""
    leads_file = env_dir / "state" / "catering-leads.json"
    if not leads_file.exists():
        return None
    store = json.loads(leads_file.read_text(encoding="utf-8"))
    for lead in store.get("leads", []):
        if lead.get("owner_approval_code") == code:
            return lead
    return None


def _read_audit(env_dir):
    """Returns list of audit dicts."""
    log_file = env_dir / "logs" / "decisions.log"
    if not log_file.exists():
        return []
    return [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]


# Standard menu fixture used across most tests.
DEFAULT_MENU = [
    {"name": "Aloo Paratha", "price_usd": 4.0, "category": "side",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Chicken Biryani", "price_usd": 15.0, "category": "main",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Gulab Jamun", "price_usd": 3.0, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
]


def test_owner_card_labels_total_as_internal_estimate():
    template = (TEMPLATES_DIR / "catering_finalized_menu_to_owner.txt").read_text(encoding="utf-8")
    assert "Internal estimate from current menu item prices" in template
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert "Internal estimate from current menu item prices" in script_text


# ============================================================================
# Happy paths
# ============================================================================

class TestHappyPath:
    def test_awaiting_to_finalized(self, bridge_server, env_dir):
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4},
                 {"name": "Gulab Jamun", "qty": 5, "price_usd": 3}]
        result, parsed = _run_script(
            env_dir, port,
            selected_items_json=json.dumps(items),
            quote_total_usd=23,  # 2*4 + 5*3 = 23
        )
        assert parsed["rc"] == 0, f"stderr: {result.stderr}"
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["status"] == "CUSTOMER_FINALIZED"
        assert lead["quote_total_usd"] == 23
        assert lead["customer_finalized_at"] is not None
        assert lead["last_finalize_message_id"] == "msg_finalize_001"
        # R3.TQ: exact round-trip of selected_items (was: only count check)
        assert lead["selected_items"] == [
            {"name": "Aloo Paratha", "qty": 2, "price_usd": 4},
            {"name": "Gulab Jamun", "qty": 5, "price_usd": 3},
        ]
        assert len(stub.requests) == 1  # owner card sent
        audit = _read_audit(env_dir)
        types = [r["type"] for r in audit]
        assert "catering_lead_status_change" in types
        assert "catering_menu_finalized" in types
        finalized = [r for r in audit if r["type"] == "catering_menu_finalized"][0]
        assert finalized["outcome"] == "finalized"
        assert finalized["server_recompute_usd"] == 23

    def test_quote_within_tolerance_persists_server_total(self, bridge_server, env_dir):
        """LLM passes total off by $20 (within $25 cap). Server total persisted."""
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Chicken Biryani", "qty": 100, "price_usd": 15}]  # server: 1500
        result, parsed = _run_script(
            env_dir, port,
            selected_items_json=json.dumps(items),
            quote_total_usd=1520,  # off by 20 (under $25 abs cap, also under 5%=$75)
        )
        assert parsed["rc"] == 0, f"stderr: {result.stderr}"
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["quote_total_usd"] == 1500  # server, NOT LLM's 1520

    def test_re_finalize_different_message_id(self, bridge_server, env_dir):
        """Different message_id while CUSTOMER_FINALIZED => state mutation, prior_* set."""
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        # First finalize
        items_a = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, parsed1 = _run_script(env_dir, port,
            customer_message_id="msg_v1",
            selected_items_json=json.dumps(items_a), quote_total_usd=8)
        assert parsed1["rc"] == 0
        # Re-finalize with different items
        items_b = [{"name": "Aloo Paratha", "qty": 5, "price_usd": 4},
                   {"name": "Gulab Jamun", "qty": 10, "price_usd": 3}]
        _, parsed2 = _run_script(env_dir, port,
            customer_message_id="msg_v2",
            selected_items_json=json.dumps(items_b), quote_total_usd=50)
        assert parsed2["rc"] == 0
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["quote_total_usd"] == 50
        assert lead["last_finalize_message_id"] == "msg_v2"
        assert len(lead["selected_items"]) == 2
        # Audit has prior_* on second
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert len(audit) == 2
        assert audit[1]["prior_total_usd"] == 8
        assert audit[1]["prior_item_count"] == 1


# ============================================================================
# Validation
# ============================================================================

class TestValidation:
    def test_quote_mismatch_outside_tolerance(self, bridge_server, env_dir):
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Chicken Biryani", "qty": 100, "price_usd": 15}]  # server: 1500
        before = json.loads((env_dir / "state" / "catering-leads.json").read_text())
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=2000)  # off by 500
        assert parsed["rc"] == 11  # EXIT_TRUTH_GUARD_FAILED
        after = json.loads((env_dir / "state" / "catering-leads.json").read_text())
        assert before == after  # NO state mutation
        assert len(stub.requests) == 0  # owner card NOT sent
        # Audit row with outcome=rejected_quote_mismatch
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert len(audit) == 1
        assert audit[0]["outcome"] == "rejected_quote_mismatch"

    def test_item_not_in_menu(self, bridge_server, env_dir):
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "GhostItem", "qty": 1, "price_usd": 99}]
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=99)
        assert parsed["rc"] == 2
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["status"] == "AWAITING_OWNER_APPROVAL"  # unchanged
        assert len(stub.requests) == 0

    def test_malformed_selected_items_json(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        result, parsed = _run_script(env_dir, port,
            selected_items_json='[{"qty":1}]',  # missing name + price_usd
            quote_total_usd=10)
        assert parsed["rc"] == 2

    def test_lead_in_NEW_status_not_actionable(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE", status="NEW")
        items = [{"name": "Aloo Paratha", "qty": 1, "price_usd": 4}]
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed["rc"] == 4


# ============================================================================
# Error paths
# ============================================================================

class TestErrors:
    def test_lead_not_found(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 1, "price_usd": 4}]
        result, parsed = _run_script(env_dir, port,
            code="#ZZZZZ",  # not in alphabet but also not in store
            selected_items_json=json.dumps(items), quote_total_usd=4)
        # #ZZZZZ doesn't match alphabet (Z not allowed) — exit 2
        # But if we use a valid alphabet code that doesn't match any lead — exit 4
        # Test actual not-found:
        result, parsed = _run_script(env_dir, port,
            code="#XYWMP",  # valid alphabet, but not in store
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed["rc"] == 4

    def test_lead_terminal_state_closed(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE", status="CLOSED")
        items = [{"name": "Aloo Paratha", "qty": 1, "price_usd": 4}]
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed["rc"] == 4

    def test_bridge_unreachable_state_persists(self, bridge_server, env_dir):
        port, stub = bridge_server
        stub.response_mode = "down"  # bridge returns 500
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 1, "price_usd": 4}]
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed["rc"] == 6  # EXIT_DEPENDENCY_DOWN
        lead = _read_lead(env_dir, "#ABCDE")
        # State IS persisted despite bridge failure
        assert lead["status"] == "CUSTOMER_FINALIZED"
        assert lead["quote_total_usd"] == 4
        # Audit row has empty owner_card_outbound_id
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert len(audit) == 1
        assert audit[0]["owner_card_outbound_id"] == ""


# ============================================================================
# Idempotency
# ============================================================================

class TestIdempotency:
    def test_replay_same_message_id_no_state_mutation(self, bridge_server, env_dir):
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 1, "price_usd": 4}]
        # First call
        _, parsed1 = _run_script(env_dir, port,
            customer_message_id="msg_replay_001",
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed1["rc"] == 0
        before = json.loads((env_dir / "state" / "catering-leads.json").read_text())
        # Replay (same message_id)
        _, parsed2 = _run_script(env_dir, port,
            customer_message_id="msg_replay_001",
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed2["rc"] == 0
        after = json.loads((env_dir / "state" / "catering-leads.json").read_text())
        # JSON-equal (no state mutation; mtime/inode irrelevant)
        assert before == after
        # Audit row 2 has replay=true
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert len(audit) == 2
        assert audit[1]["replay"] is True


# ============================================================================
# Edge cases
# ============================================================================

class TestEdgeCases:
    def test_menu_file_missing(self, bridge_server, env_dir):
        port, _ = bridge_server
        # No _seed_menu — menu file absent
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 1, "price_usd": 4}]
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=4)
        assert parsed["rc"] == 2  # EXIT_INVALID_INPUT

    def test_menu_price_drift_detected(self, bridge_server, env_dir):
        """PR-CF2: drift detection now operates on subtotals, not per-item.

        Menu has Aloo Paratha at $5; LLM passes price_usd=4 (saw stale).
        Server line subtotal = 100*5 = 500. LLM line subtotal = 100*4 = 400.
        Subtotals differ → drift=True. Args.quote_total_usd=500 matches
        server, so tolerance passes. Lead persists with current ($5) price.
        """
        port, _ = bridge_server
        menu = [{"name": "Aloo Paratha", "price_usd": 5.0, "category": "side",
                 "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None}]
        _seed_menu(env_dir, menu)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 100, "price_usd": 4}]  # stale price
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=500)
        assert parsed["rc"] == 0, f"stderr: {result.stderr}"
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["quote_total_usd"] == 500
        assert lead["selected_items"][0]["price_usd"] == 5  # current, NOT stale 4
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert audit[0]["price_drift_detected"] is True

    def test_drift_NOT_triggered_when_subtotals_match(self, bridge_server, env_dir):
        """PR-CF2 (R1.M2): no drift when LLM and server subtotals agree —
        even if individual price_usd values look numerically different.
        Eliminates the false-positive drift from $4.50 menu items.
        """
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)  # Aloo Paratha @ $4
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 10, "price_usd": 4}]  # matches menu
        result, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=40)
        assert parsed["rc"] == 0, f"stderr: {result.stderr}"
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert audit[0]["price_drift_detected"] is False


# ============================================================================
# PR-CF1 review-fix coverage (R3 BLOCKER-COVERAGE + R1.M1)
# ============================================================================

class TestToleranceBoundary:
    """R3.BC3 — boundary tests for min(5%, $25) tolerance cap."""

    def _menu_with_price(self, env_dir, price: float) -> None:
        """Single-item menu at the given price (whole dollars)."""
        _seed_menu(env_dir, [{
            "name": "Aloo Paratha", "price_usd": price, "category": "side",
            "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None,
        }])

    def test_dollar_cap_at_boundary_passes(self, bridge_server, env_dir):
        """server=$1000 (price $5 × qty 200), llm=$1025 (diff=$25, exactly at cap) => exit 0."""
        port, _ = bridge_server
        self._menu_with_price(env_dir, 5.0)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 200, "price_usd": 5}]
        _, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=1025)
        assert parsed["rc"] == 0

    def test_dollar_cap_one_over_boundary_fails(self, bridge_server, env_dir):
        """server=$1000, llm=$1026 (diff=$26, one over $25 cap) => exit 11."""
        port, _ = bridge_server
        self._menu_with_price(env_dir, 5.0)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 200, "price_usd": 5}]
        _, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=1026)
        assert parsed["rc"] == 11

    def test_pct_branch_under_cap_passes(self, bridge_server, env_dir):
        """server=$100 (price $1 × qty 100), llm=$104 (diff=$4 = 4%) => 0.

        At server=$100, min(5%=$5, $25) = $5; $4 <= $5 passes.
        """
        port, _ = bridge_server
        self._menu_with_price(env_dir, 1.0)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 100, "price_usd": 1}]
        _, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=104)
        assert parsed["rc"] == 0

    def test_pct_branch_over_cap_fails(self, bridge_server, env_dir):
        """server=$100, llm=$106 (diff=$6 = 6%, over 5% pct cap of $5) => 11.

        At server=$100, min(5%=$5, $25) = $5; $6 > $5 fails.
        """
        port, _ = bridge_server
        self._menu_with_price(env_dir, 1.0)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 100, "price_usd": 1}]
        _, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=106)
        assert parsed["rc"] == 11


class TestReplayTotalConsistency:
    """R2.B2 — on replay, owner card total MUST come from persisted
    quote_total_usd, NOT from a fresh server recompute. Otherwise the
    line items (persisted prices) and total (fresh prices) disagree
    when the menu drifts between first finalize and replay.
    """

    def test_replay_uses_persisted_total_not_server_recompute(self, bridge_server, env_dir):
        """On replay, owner card MUST use target.quote_total_usd (persisted),
        NOT a freshly recomputed server_total. Constructed scenario:
          1. First finalize at $40 (menu @ $4 × qty=10 → server=$40, persisted=$40)
          2. Manually mutate persisted state to quote_total_usd=$77
             (representing operator surgery / a bug that caused divergence)
          3. Replay with same message_id, same args (server=$40 again)
          4. Card MUST show $77 (persisted), NOT $40 (server recompute)
        Without R2.B2 fix, the card shows $40.
        """
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)  # Aloo Paratha @ $4
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 10, "price_usd": 4}]
        # First finalize at $40
        _, parsed1 = _run_script(env_dir, port,
            customer_message_id="msg_replay_total",
            selected_items_json=json.dumps(items), quote_total_usd=40)
        assert parsed1["rc"] == 0
        assert len(stub.requests) == 1

        # Mutate persisted quote_total_usd to a divergent value
        leads_path = env_dir / "state" / "catering-leads.json"
        store = json.loads(leads_path.read_text(encoding="utf-8"))
        store["leads"][0]["quote_total_usd"] = 77
        leads_path.write_text(json.dumps(store), encoding="utf-8")

        # Bypass cooldown via ts rewrite so replay actually sends a card
        log_path = env_dir / "logs" / "decisions.log"
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        rewritten = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "catering_menu_finalized":
                row["ts"] = old_ts
            rewritten.append(json.dumps(row))
        log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        # Replay
        _, parsed2 = _run_script(env_dir, port,
            customer_message_id="msg_replay_total",
            selected_items_json=json.dumps(items), quote_total_usd=40)
        assert parsed2["rc"] == 0
        assert len(stub.requests) == 2
        replay_card = stub.requests[1]["message"]
        # R2.B2 fix: replay card uses persisted $77, NOT server recompute $40
        assert "$77" in replay_card, (
            f"Replay card must use persisted total $77 (R2.B2). Got: {replay_card[:500]}"
        )


class TestReFinalizeAudit:
    """R2.B1 — re-finalize from CUSTOMER_FINALIZED MUST emit a
    catering_lead_status_change row (from=CUSTOMER_FINALIZED, to=CUSTOMER_FINALIZED,
    reason=customer_re_finalized_menu) so the audit chain captures customer
    mind-changes for routing-reliability monitoring.
    """

    def test_re_finalize_emits_status_change_audit(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items_a = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, p1 = _run_script(env_dir, port,
            customer_message_id="msg_v1",
            selected_items_json=json.dumps(items_a), quote_total_usd=8)
        assert p1["rc"] == 0
        items_b = [{"name": "Gulab Jamun", "qty": 10, "price_usd": 3}]
        _, p2 = _run_script(env_dir, port,
            customer_message_id="msg_v2",
            selected_items_json=json.dumps(items_b), quote_total_usd=30)
        assert p2["rc"] == 0
        status_changes = [r for r in _read_audit(env_dir)
                          if r["type"] == "catering_lead_status_change"]
        # Two status_change rows: one for AWAITING -> CUSTOMER_FINALIZED, one
        # for CUSTOMER_FINALIZED -> CUSTOMER_FINALIZED (re-finalize).
        assert len(status_changes) == 2
        first, second = status_changes
        assert first["from_status"] == "AWAITING_OWNER_APPROVAL"
        assert first["to_status"] == "CUSTOMER_FINALIZED"
        assert first["reason"] == "customer_finalized_menu"
        assert second["from_status"] == "CUSTOMER_FINALIZED"
        assert second["to_status"] == "CUSTOMER_FINALIZED"
        assert second["reason"] == "customer_re_finalized_menu"


class TestCooldownSuppression:
    """R3.HC — replay within 60s suppresses owner-card resend; replay outside
    cooldown sends a fresh card. Both audit rows have replay=True; suppressed
    differs.
    """

    def test_replay_within_cooldown_suppresses_card(self, bridge_server, env_dir):
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, p1 = _run_script(env_dir, port,
            customer_message_id="msg_cool",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p1["rc"] == 0
        assert len(stub.requests) == 1
        # Replay immediately (within 60s)
        _, p2 = _run_script(env_dir, port,
            customer_message_id="msg_cool",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p2["rc"] == 0
        assert len(stub.requests) == 1  # NO second card
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert len(finalized) == 2
        assert finalized[1]["replay"] is True
        assert finalized[1]["suppressed"] is True

    def test_replay_outside_cooldown_resends_card(self, bridge_server, env_dir):
        """Bypass 60s cooldown by rewriting log ts to 2h ago."""
        port, stub = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, p1 = _run_script(env_dir, port,
            customer_message_id="msg_cool",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p1["rc"] == 0
        # Rewrite finalized row ts to 2h ago
        log_path = env_dir / "logs" / "decisions.log"
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        rewritten = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "catering_menu_finalized":
                row["ts"] = old_ts
            rewritten.append(json.dumps(row))
        log_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        # Replay
        _, p2 = _run_script(env_dir, port,
            customer_message_id="msg_cool",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p2["rc"] == 0
        assert len(stub.requests) == 2  # second card sent
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert finalized[-1]["replay"] is True
        assert finalized[-1]["suppressed"] is False


class TestQuoteMismatchAuditLeadId:
    """R1.M1 / R3.OB-Blocker — quote-mismatch audit row MUST use the actual
    lead_id when the lead exists, NOT the placeholder '(quote-mismatch)'.
    """

    def test_mismatch_audit_uses_actual_lead_id(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0042", "#ABCDE")
        items = [{"name": "Chicken Biryani", "qty": 100, "price_usd": 15}]
        _, parsed = _run_script(env_dir, port,
            selected_items_json=json.dumps(items), quote_total_usd=2000)  # off by 500
        assert parsed["rc"] == 11
        audit = [r for r in _read_audit(env_dir) if r["type"] == "catering_menu_finalized"]
        assert len(audit) == 1
        assert audit[0]["outcome"] == "rejected_quote_mismatch"
        assert audit[0]["lead_id"] == "L0042"  # actual, NOT '(quote-mismatch)'


class TestConcurrency:
    """R3.BC4 — two parallel finalize calls on same code with DIFFERENT
    customer_message_ids must serialize cleanly under LEADS_LOCK. Final state
    reflects whichever finalize won the race; both audit rows emitted with
    replay=False.
    """

    def test_parallel_finalizes_serialize(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")

        results: list = []
        def run_once(mid: str, qty: int):
            items = [{"name": "Aloo Paratha", "qty": qty, "price_usd": 4}]
            _, parsed = _run_script(env_dir, port,
                customer_message_id=mid,
                selected_items_json=json.dumps(items),
                quote_total_usd=qty * 4)
            results.append((mid, parsed))

        t1 = threading.Thread(target=run_once, args=("msg_par_a", 3))
        t2 = threading.Thread(target=run_once, args=("msg_par_b", 7))
        t1.start(); t2.start()
        t1.join(timeout=15); t2.join(timeout=15)

        assert len(results) == 2
        for _, parsed in results:
            assert parsed["rc"] == 0
        # Final lead state matches one of the two
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["status"] == "CUSTOMER_FINALIZED"
        assert lead["last_finalize_message_id"] in {"msg_par_a", "msg_par_b"}
        # Two finalized audit rows; both replay=False (real finalizes, not idempotent)
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert len(finalized) == 2
        for row in finalized:
            assert row["replay"] is False


# ============================================================================
# PR-CF2 review follow-ups
# ============================================================================


class TestCustomerMessageIdInAudit:
    """PR-CF2 (R1.M1, R3.OBH) — every catering_menu_finalized row carries
    customer_message_id so ops can join audit rows ↔ raw_inbound ↔ dispatcher_routed
    without phone-based fuzzy matching.
    """

    def test_success_audit_carries_customer_message_id(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, parsed = _run_script(env_dir, port,
            customer_message_id="msg_traceability_001",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert parsed["rc"] == 0
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert finalized[0]["customer_message_id"] == "msg_traceability_001"

    def test_replay_audit_carries_customer_message_id(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, p1 = _run_script(env_dir, port,
            customer_message_id="msg_replay_traceability",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p1["rc"] == 0
        _, p2 = _run_script(env_dir, port,
            customer_message_id="msg_replay_traceability",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p2["rc"] == 0
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert len(finalized) == 2
        assert finalized[0]["customer_message_id"] == "msg_replay_traceability"
        assert finalized[1]["customer_message_id"] == "msg_replay_traceability"
        assert finalized[1]["replay"] is True

    def test_mismatch_audit_carries_customer_message_id(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Chicken Biryani", "qty": 100, "price_usd": 15}]
        _, parsed = _run_script(env_dir, port,
            customer_message_id="msg_mismatch_traceability",
            selected_items_json=json.dumps(items), quote_total_usd=2000)
        assert parsed["rc"] == 11
        rejected = [r for r in _read_audit(env_dir)
                    if r["type"] == "catering_menu_finalized"
                    and r["outcome"] == "rejected_quote_mismatch"]
        assert len(rejected) == 1
        assert rejected[0]["customer_message_id"] == "msg_mismatch_traceability"


class TestReplayWindow:
    """PR-CF2 (R1.H3) — replay short-circuit only fires within REPLAY_WINDOW_HOURS
    (24h). Past that, same message_id is treated as a fresh finalize so a
    rare bridge messageId reuse cannot suppress a real customer action.
    """

    def test_replay_within_24h_short_circuits(self, bridge_server, env_dir):
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, p1 = _run_script(env_dir, port,
            customer_message_id="msg_window_a",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p1["rc"] == 0
        _, p2 = _run_script(env_dir, port,
            customer_message_id="msg_window_a",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p2["rc"] == 0
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert finalized[1]["replay"] is True

    def test_legacy_lead_with_msgid_but_null_finalized_at_treated_as_fresh(
        self, bridge_server, env_dir,
    ):
        """PR-CF2 review-fix MEDIUM-2: a partially-written legacy row could
        have last_finalize_message_id set but customer_finalized_at=None.
        Submitting the same message_id MUST be treated as a fresh finalize
        (replay=False, state mutates, status_change row written), NOT
        suppressed-as-replay. Also confirms no TypeError from the tz-aware
        subtraction guard.
        """
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        # Seed lead with last_finalize_message_id pre-set but no finalized_at
        _seed_lead(
            env_dir, "L0001", "#ABCDE",
            last_finalize_message_id="msg_legacy_partial",
            customer_finalized_at=None,
        )
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, parsed = _run_script(env_dir, port,
            customer_message_id="msg_legacy_partial",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert parsed["rc"] == 0
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert len(finalized) == 1
        assert finalized[0]["replay"] is False  # treated as fresh
        # Status_change row written (initial transition AWAITING -> FINALIZED)
        status_changes = [r for r in _read_audit(env_dir)
                          if r["type"] == "catering_lead_status_change"]
        assert len(status_changes) == 1
        # Lead has customer_finalized_at populated now
        lead = _read_lead(env_dir, "#ABCDE")
        assert lead["customer_finalized_at"] is not None

    def test_replay_outside_24h_treated_as_fresh_finalize(self, bridge_server, env_dir):
        """Manipulate persisted customer_finalized_at to >24h ago. Replay
        with same message_id then becomes a real re-finalize (state
        mutation, status_change row, replay=False).
        """
        port, _ = bridge_server
        _seed_menu(env_dir, DEFAULT_MENU)
        _seed_lead(env_dir, "L0001", "#ABCDE")
        items = [{"name": "Aloo Paratha", "qty": 2, "price_usd": 4}]
        _, p1 = _run_script(env_dir, port,
            customer_message_id="msg_window_b",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p1["rc"] == 0
        # Rewrite customer_finalized_at to 30h ago (outside the 24h window)
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=30)).isoformat()
        leads_path = env_dir / "state" / "catering-leads.json"
        store = json.loads(leads_path.read_text(encoding="utf-8"))
        store["leads"][0]["customer_finalized_at"] = old_ts
        leads_path.write_text(json.dumps(store), encoding="utf-8")
        # Replay with same message_id — treated as fresh finalize, not replay
        _, p2 = _run_script(env_dir, port,
            customer_message_id="msg_window_b",
            selected_items_json=json.dumps(items), quote_total_usd=8)
        assert p2["rc"] == 0
        finalized = [r for r in _read_audit(env_dir)
                     if r["type"] == "catering_menu_finalized"]
        assert len(finalized) == 2
        assert finalized[1]["replay"] is False  # NOT treated as replay
        # Status change row written for the re-finalize (CUSTOMER_FINALIZED self-edge)
        status_changes = [r for r in _read_audit(env_dir)
                          if r["type"] == "catering_lead_status_change"]
        assert len(status_changes) == 2  # initial + re-finalize
