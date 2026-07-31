"""M2 — finalize-catering-menu routed through the pricing kernel.

Linux-only (safe_io needs fcntl). Driven exactly like
tests/test_catering_finalize_menu.py: a subprocess importlib wrapper rebinds
the script's hardcoded paths to a tmp tree, plus a stub bridge.

The pivot these cells guard: with a pricebook present the quote is computed by
catering_pricing.compute_quote (packages, fees, tax, overrides, headcount
scaling); with NO pricebook the arithmetic is byte-identical to pre-M2.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
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
TEMPLATES_DIR = REPO / "src" / "agents" / "catering" / "templates"
PLATFORM_DIR = REPO / "src" / "platform"
FIXTURE = REPO / "tests" / "fixtures" / "catering_pricebook_valid.json"


def _runner_ids():
    """The quote ledger enforces owner:group on POSIX; the runner's tmp tree is
    not owned by shift-agent, so the subprocess is told the runner's ids."""
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


_LEDGER_OWNER, _LEDGER_GROUP = _runner_ids()


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            self.__class__.requests.append(json.loads(body))
        except Exception:
            self.__class__.requests.append({})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"id": f"msg_{len(self.__class__.requests)}"}).encode())

    def log_message(self, *args):
        return


@pytest.fixture
def bridge():
    _BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield port, _BridgeStub
    server.shutdown()


CONFIG = {
    "schema_version": 1,
    "customer": {"name": "T", "location_id": "loc", "timezone": "America/New_York",
                 "languages": ["en"]},
    "owner": {"name": "O", "phone": "+15551234567",
              "self_chat_jid": "15551234567@s.whatsapp.net"},
    "limits": {"max_outbound_per_day": 100, "max_outbound_per_minute": 30,
               "pending_proposal_ttl_hours": 4, "per_message_timeout_sec": 120,
               "send_failure_retry_count": 1},
    "alerting": {"pushover_user_key": "k", "pushover_app_token": "t",
                 "healthchecks_io_url": "", "email": ""},
    "backup": {"gpg_recipient_email": "o@example.com", "retention_days": 7},
    "catering": {"enabled": True, "deposit_threshold_guests": 50,
                 "deposit_pct": 0.0, "min_per_guest_usd": 3.0},
}

MENU = {
    "version": 7,
    "updated_at": "2026-07-01T09:00:00Z",
    "updated_by": "manual",
    "items": [
        {"name": "Samosa", "price_usd": 2.5, "category": "appetizer", "serves": 5},
        {"name": "Gulab Jamun", "price_usd": 4.0, "category": "dessert", "serves": 10},
        {"name": "Paneer Tikka", "price_usd": 12.0, "category": "main", "serves": 8},
    ],
    "notes": "",
}


def _env(tmp_path: Path, *, pricebook=True, headcount=50, placeholder=False,
         corrupt_pricebook=False) -> Path:
    env = tmp_path / f"env{headcount}{int(pricebook)}{int(placeholder)}{int(corrupt_pricebook)}"
    (env / "state").mkdir(parents=True, exist_ok=True)
    (env / "logs").mkdir(parents=True, exist_ok=True)
    (env / "templates").mkdir(parents=True, exist_ok=True)
    for t in TEMPLATES_DIR.glob("*.txt"):
        shutil.copy2(t, env / "templates" / t.name)
    (env / "config.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    (env / "state" / "catering-menu.json").write_text(json.dumps(MENU), encoding="utf-8")

    pb_path = env / "state" / "catering-pricebook.json"
    if corrupt_pricebook:
        pb_path.write_text("{{{not json", encoding="utf-8")
    elif pricebook:
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        doc["placeholder"] = placeholder
        pb_path.write_text(json.dumps({"schema_version": 1, "pricebook": doc}),
                           encoding="utf-8")

    lead = {
        "lead_id": "CAT-0001", "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+15559998888", "customer_name": "Asha",
        "raw_inquiry": "need catering", "original_message_id": "wamid.1",
        "created_at": "2026-07-30T10:00:00Z", "updated_at": "2026-07-30T10:00:00Z",
        "owner_approval_code": "#ABCDE",
        "extracted": {"headcount": headcount, "event_date": "2026-08-15",
                      "event_time": "18:00", "dietary_restrictions": []},
    }
    (env / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead], "next_lead_seq": 2}), encoding="utf-8")
    return env


def _run(env: Path, port: int, *cli):
    argv = ["finalize-catering-menu", "--code", "#ABCDE",
            "--customer-message-id", "m1", *cli]
    state = env / "state"
    wrapper = f"""
import sys, pathlib, json, io
sys.argv = {argv!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("fcm_pb_test", {str(SCRIPT)!r}).load_module()
mod.CONFIG_PATH = pathlib.Path({str(env / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(state / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(state / 'catering-leads.json.lock')!r})
mod.MENU_PATH = pathlib.Path({str(state / 'catering-menu.json')!r})
mod.PRICEBOOK_PATH = pathlib.Path({str(state / 'catering-pricebook.json')!r})
mod.PROPOSALS_PATH = pathlib.Path({str(state / 'catering-proposals.json')!r})
mod.PROPOSALS_LOCK = pathlib.Path({str(state / 'catering-proposals.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env / 'logs' / 'decisions.log')!r})
mod.LOG_LOCK = pathlib.Path({str(env / 'logs' / 'decisions.log.lock')!r})
mod.LEDGER_PATH = pathlib.Path({str(state / 'catering-quote-ledger.json')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{port}/send"
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
    proc = subprocess.run(
        [sys.executable, "-c", wrapper], capture_output=True, text=True, timeout=30,
        env={**os.environ,
             "HERMES_BRIDGE_URL": f"http://127.0.0.1:{port}/send",
             "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1",
             "SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER": _LEDGER_OWNER,
             "SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP": _LEDGER_GROUP},
    )
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    parsed = json.loads(lines[-1]) if lines else {"rc": -1, "stdout": ""}
    return proc, parsed


def _out(parsed):
    return json.loads(parsed["stdout"].strip().splitlines()[-1])


def _lead(env: Path) -> dict:
    return json.loads(
        (env / "state" / "catering-leads.json").read_text(encoding="utf-8"))["leads"][0]


def _ledger_last(env: Path) -> dict:
    return json.loads((env / "state" / "catering-quote-ledger.json")
                      .read_text(encoding="utf-8"))["records"][-1]


def _card(stub) -> str:
    return stub.requests[-1].get("message", "")


MANUAL = ("--selected-items-json", '[{"name":"Gulab Jamun","qty":10,"price_usd":4}]',
          "--quote-total-usd", "45")


# ── backward compatibility: no pricebook ─────────────────────────────────────
def test_without_a_pricebook_the_arithmetic_is_unchanged(tmp_path, bridge):
    port, stub = bridge
    env = _env(tmp_path, pricebook=False)
    _proc, parsed = _run(
        env, port, "--selected-items-json",
        '[{"name":"Samosa","qty":4,"price_usd":2},{"name":"Paneer Tikka","qty":2,"price_usd":12}]',
        "--quote-total-usd", "32")
    assert parsed["rc"] == 0, parsed
    out = _out(parsed)
    # Pre-M2 arithmetic: int(round(2.5))=2 and int(round(12.0))=12 -> 4*2 + 2*12.
    assert out["quote_total_usd"] == 32
    assert out["price_status"] == "estimated"
    assert out["pricebook_version"] is None
    assert out["menu_version"] == 7


def test_without_a_pricebook_the_card_still_labels_the_price(tmp_path, bridge):
    port, stub = bridge
    env = _env(tmp_path, pricebook=False)
    _run(env, port, "--selected-items-json",
         '[{"name":"Samosa","qty":4,"price_usd":2}]', "--quote-total-usd", "8")
    assert "Price status: estimated" in _card(stub)


def test_without_a_pricebook_auto_default_keeps_the_legacy_basket(tmp_path, bridge):
    """Byte-identical fallback: no headcount, no scaling, qty=1."""
    port, _stub = bridge
    env = _env(tmp_path, pricebook=False, headcount=200)
    _proc, parsed = _run(env, port, "--auto-default")
    assert parsed["rc"] == 0, parsed
    assert all(i["qty"] == 1 for i in _lead(env)["selected_items"])


# ── pricebook regime ─────────────────────────────────────────────────────────
def test_pricebook_applies_overrides_fees_and_tax(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path)
    _proc, parsed = _run(env, port, *MANUAL)
    assert parsed["rc"] == 0, parsed
    out = _out(parsed)
    # override 450c x10 = 4500; delivery 2500 + setup 5000 = 7500; subtotal 12000;
    # tax 8.25% = 990 -> 12990c -> $129.90 -> $130 whole dollars.
    assert out["quote_total_usd"] == 130
    assert out["price_status"] == "exact"
    assert out["pricebook_version"] == 4


def test_persisted_unit_price_comes_from_the_override_not_the_menu(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path)
    _run(env, port, *MANUAL)
    item = _lead(env)["selected_items"][0]
    assert item["name"] == "Gulab Jamun"
    assert item["price_usd"] == 5      # 450c half-up to whole dollars, not the $4 menu price


def test_owner_card_shows_the_fee_and_tax_breakdown_and_the_label(tmp_path, bridge):
    port, stub = bridge
    env = _env(tmp_path)
    _run(env, port, *MANUAL)
    card = _card(stub)
    assert "Fee — Delivery: $25.00" in card
    assert "Tax (8.25%): $9.90" in card
    assert "Price status: exact" in card


def test_ledger_record_carries_the_provenance_stamps(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path)
    _run(env, port, *MANUAL)
    rec = _ledger_last(env)
    assert rec["menu_version"] == 7
    assert rec["pricebook_version"] == 4
    assert rec["price_status"] == "exact"


# ── truth guard ──────────────────────────────────────────────────────────────
def test_fees_and_tax_alone_do_not_trip_the_truth_guard(tmp_path, bridge):
    """The guard compares the LLM's basket sum to the SUBTOTAL. Comparing against
    the full total would fail every legitimate finalize under a pricebook."""
    port, _stub = bridge
    env = _env(tmp_path)
    _proc, parsed = _run(env, port, *MANUAL)
    assert parsed["rc"] == 0, parsed


def test_a_genuinely_wrong_llm_total_still_trips_the_truth_guard(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path)
    _proc, parsed = _run(
        env, port, "--selected-items-json",
        '[{"name":"Gulab Jamun","qty":10,"price_usd":4}]', "--quote-total-usd", "5000")
    assert parsed["rc"] == 11, parsed


# ── the BL-CATER-03 fix ──────────────────────────────────────────────────────
def test_auto_default_uses_the_package_scaled_to_headcount(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path, headcount=200)
    _proc, parsed = _run(env, port, "--auto-default")
    assert parsed["rc"] == 0, parsed
    item = _lead(env)["selected_items"][0]
    assert item["name"] == "Standard Veg Buffet"
    assert item["qty"] == 200, "the basket must scale with the guest count"
    # 1800c x200 = 360000 + 7500 fees = 367500; tax 8.25% = 30319 -> 397819c -> $3978
    assert _out(parsed)["quote_total_usd"] == 3978


def test_scaled_auto_default_clears_the_per_guest_floor(tmp_path, bridge):
    """The pre-M2 basket priced 200 guests at ~$50 and tripped the floor."""
    port, _stub = bridge
    env = _env(tmp_path, headcount=200)
    _proc, parsed = _run(env, port, "--auto-default")
    assert "below_min_per_guest" not in _out(parsed)["pricing_flags"]


def test_auto_default_scales_menu_items_when_no_package_is_eligible(tmp_path, bridge):
    """10 guests is under both packages' min_guests, so quantities scale by serves."""
    port, _stub = bridge
    env = _env(tmp_path, headcount=10)
    _proc, parsed = _run(env, port, "--auto-default")
    assert parsed["rc"] == 0, parsed
    qty = {i["name"]: i["qty"] for i in _lead(env)["selected_items"]}
    assert qty == {"Samosa": 2, "Gulab Jamun": 1, "Paneer Tikka": 2}


# ── refusals ─────────────────────────────────────────────────────────────────
def test_an_item_with_no_resolvable_price_refuses(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path)
    proc, parsed = _run(env, port, "--selected-items-json",
                        '[{"name":"Lobster Thermidor","qty":1,"price_usd":9}]',
                        "--quote-total-usd", "9")
    assert parsed["rc"] == 2
    assert "no resolvable price" in proc.stderr


def test_a_corrupt_pricebook_refuses_instead_of_reverting_to_flat_pricing(tmp_path, bridge):
    """Falling back would silently drop the owner's fees and tax from a real quote."""
    port, _stub = bridge
    env = _env(tmp_path, corrupt_pricebook=True)
    proc, parsed = _run(env, port, "--selected-items-json",
                        '[{"name":"Samosa","qty":4,"price_usd":2}]',
                        "--quote-total-usd", "8")
    assert parsed["rc"] == 2
    assert "refusing to finalize at pre-pricebook prices" in proc.stderr
    assert (env / "state" / "catering-pricebook.json").read_text(
        encoding="utf-8") == "{{{not json", "the operator's file must be left in place"


# ── placeholder pricebook blocks delivery ────────────────────────────────────
def test_placeholder_pricebook_marks_the_quote_undeliverable(tmp_path, bridge):
    port, stub = bridge
    env = _env(tmp_path, placeholder=True)
    _proc, parsed = _run(env, port, *MANUAL)
    assert parsed["rc"] == 0, parsed
    out = _out(parsed)
    assert out["price_status"] == "pending_owner_review"
    assert "placeholder_pricebook" in out["pricing_flags"]
    assert "Price status: pending owner review" in _card(stub)


def test_placeholder_status_is_recorded_on_the_committed_version(tmp_path, bridge):
    port, _stub = bridge
    env = _env(tmp_path, placeholder=True)
    _run(env, port, *MANUAL)
    assert _ledger_last(env)["price_status"] == "pending_owner_review"
