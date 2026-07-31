"""M3 — `apply-catering-owner-decision --discount-id`.

M2 built full discount support into the pricing kernel (validated, capped,
attributed, `requires_owner_code` flagged) and finalize deliberately passes
discount_id=None, because applying a discount is an OWNER action. This is that
owner action, and these cells pin the parts that cost money if they are wrong:
an unknown/inactive id is refused BEFORE any state change, the cap holds, the
discount is attributed on the committed version, and a discounted total never
diverges from the text the customer reads.

Windows-runnable: in-process via fixtures_fleet.load_script with the fcntl stub.
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
            self.__class__.requests.append(json.loads(body))
        except Exception:
            self.__class__.requests.append({})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server(monkeypatch):
    _BridgeStub.requests = []
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


def _pricebook(**over):
    """$0 tax by default so the discount arithmetic is readable in the assertions."""
    doc = {
        "version": 4,
        "effective_date": "2026-07-01",
        "updated_at": "2026-07-01T00:00:00+00:00",
        "currency": "USD",
        "placeholder": False,
        "per_person_packages": [],
        "fixed_fees": [],
        "tax_rate_bps": 0,
        "approved_discounts": [
            # 10% off, capped at $50 — the cap is what the cap cell exercises.
            {"id": "loyal10", "name": "Loyal customer 10%", "kind": "percent",
             "value": 1000, "max_amount_cents": 5000,
             "requires_owner_code": True, "active": True},
            {"id": "flat25", "name": "Flat $25 off", "kind": "fixed_cents",
             "value": 2500, "requires_owner_code": False, "active": True},
            {"id": "expired5", "name": "Retired promo", "kind": "percent",
             "value": 500, "requires_owner_code": False, "active": False},
        ],
        "item_price_overrides": {},
        "notes": "",
    }
    doc.update(over)
    return doc


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
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (tmp_path / "state" / "catering-pricebook.json").write_text(
        json.dumps(_pricebook()), encoding="utf-8")
    return tmp_path


def _pricing_inputs(items, total, *, price_status="estimated", package=None,
                    fees=None, tax_rate_bps=0, flags=None):
    """The cents-exact commitment finalize freezes onto the lead. The discount
    path rebuilds from THIS, so a seed without it is a lead that predates
    provenance — which the refusal cells below exercise deliberately."""
    lines = [{"name": it["name"], "qty": it["qty"],
              "unit_cents": it["price_usd"] * 100} for it in items]
    doc = {
        "guest_count": 50,
        "line_items": lines,
        "fees": fees or [],
        "tax_rate_bps": tax_rate_bps,
        "pricebook_version": 4,
        "subtotal_cents": sum(l["unit_cents"] * l["qty"] for l in lines),
        "total_cents": total * 100,
        "price_status": price_status,
        "flags": flags or [],
    }
    if package:
        doc.update(package)
    return doc


def _seed_lead(env_dir, *, status="CUSTOMER_FINALIZED", items=None, total=400,
               pricing_inputs=_pricing_inputs, **inputs_over):
    if items is None:
        items = [{"name": "Veg Biryani", "qty": 4, "price_usd": 100}]
    lead = {
        "lead_id": "L0001", "status": status,
        "customer_phone": "+19045550199", "customer_name": "Asha Rao",
        "raw_inquiry": "wedding catering", "original_message_id": "msg_orig",
        "created_at": "2026-07-20T10:00:00-04:00",
        "updated_at": "2026-07-25T10:00:00-04:00",
        "extracted": {"headcount": 50, "event_date": "2026-09-15"},
        "quote_text": "Quote for L0001 ... 50 guests ... 2026-09-15",
        "quote_total_usd": total,
        "selected_items": items,
        "customer_finalized_at": "2026-07-25T11:00:00-04:00",
        "owner_approval_code": "#ABCDE",
    }
    if pricing_inputs is not None and items:
        lead["pricing_inputs"] = pricing_inputs(items, total, **inputs_over)
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead]}), encoding="utf-8")


def _read_lead(env_dir):
    doc = json.loads((env_dir / "state" / "catering-leads.json").read_text(encoding="utf-8"))
    return doc["leads"][0]


def _ledger(env_dir):
    path = env_dir / "state" / "catering-quote-ledger.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("records", [])


def _run(env_dir, *argv, stdin_text=""):
    import io
    mod = load_script("acod_discount_mod", APPLY)
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    mod.LEDGER_PATH = env_dir / "state" / "catering-quote-ledger.json"
    mod.PRICEBOOK_PATH = env_dir / "state" / "catering-pricebook.json"
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = ["apply-catering-owner-decision", "--code", "#ABCDE",
                "--sender-role", "owner", *argv]
    sys.stdin = io.StringIO(stdin_text)
    try:
        return mod.main()
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin


# ── refusal: unknown / inactive ids, before any state change ────────────────
def test_unknown_discount_id_is_refused_with_its_own_exit_code(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "--decision", "edit", "--edit-text", "apply loyalty",
                "--discount-id", "does-not-exist") == 15  # EXIT_UNKNOWN_DISCOUNT


def test_inactive_discount_id_is_refused(bridge_server, env_dir):
    """A retired promo is not applicable just because it is still in the file."""
    _seed_lead(env_dir)
    assert _run(env_dir, "--decision", "edit", "--edit-text", "apply promo",
                "--discount-id", "expired5") == 15


def test_a_refused_discount_leaves_the_lead_completely_untouched(bridge_server, env_dir):
    """Refuse BEFORE the mutation: a lead parked in OWNER_EDITED with a total
    nobody agreed to is worse than a clean refusal."""
    _seed_lead(env_dir)
    _run(env_dir, "--decision", "edit", "--edit-text", "x", "--discount-id", "nope")
    lead = _read_lead(env_dir)
    assert lead["status"] == "CUSTOMER_FINALIZED"
    assert lead["quote_total_usd"] == 400
    assert _ledger(env_dir) == []


def test_missing_pricebook_refuses_rather_than_inventing_terms(bridge_server, env_dir):
    _seed_lead(env_dir)
    (env_dir / "state" / "catering-pricebook.json").unlink()
    assert _run(env_dir, "--decision", "edit", "--edit-text", "x",
                "--discount-id", "flat25") == 15
    assert _read_lead(env_dir)["status"] == "CUSTOMER_FINALIZED"


# ── the recompute ───────────────────────────────────────────────────────────
def test_fixed_discount_is_applied_to_the_total(bridge_server, env_dir):
    _seed_lead(env_dir)   # 4 x $100 = $400
    assert _run(env_dir, "--decision", "edit", "--edit-text", "apply the flat promo",
                "--discount-id", "flat25") == 0
    assert _read_lead(env_dir)["quote_total_usd"] == 375   # 400 - 25


def test_percent_discount_respects_its_cap(bridge_server, env_dir):
    """10% of $400 is $40, under the $50 cap."""
    _seed_lead(env_dir)
    _run(env_dir, "--decision", "edit", "--edit-text", "loyalty",
         "--discount-id", "loyal10")
    assert _read_lead(env_dir)["quote_total_usd"] == 360


def test_percent_discount_is_capped_when_the_raw_percentage_exceeds_it(
    bridge_server, env_dir,
):
    """10% of $2000 is $200, but the discount caps at $50 — the cap must win, or
    the owner gives away four times what they published."""
    _seed_lead(env_dir, items=[{"name": "Banquet", "qty": 2, "price_usd": 1000}],
               total=2000)
    _run(env_dir, "--decision", "edit", "--edit-text", "loyalty",
         "--discount-id", "loyal10")
    assert _read_lead(env_dir)["quote_total_usd"] == 1950   # 2000 - 50, not 1800


def test_requires_owner_code_discounts_are_allowed_on_the_owner_path(
    bridge_server, env_dir,
):
    """That flag stops AUTOMATION applying a discount. This IS the owner."""
    _seed_lead(env_dir)
    assert _run(env_dir, "--decision", "edit", "--edit-text", "loyalty",
                "--discount-id", "loyal10") == 0
    assert _read_lead(env_dir)["quote_total_usd"] == 360


# ── the committed version ───────────────────────────────────────────────────
def test_the_discount_is_attributed_on_the_committed_version(bridge_server, env_dir):
    """"why is this cheaper than the last version?" must be answerable from the
    ledger alone, without re-deriving it from the pricebook."""
    _seed_lead(env_dir)
    _run(env_dir, "--decision", "edit", "--edit-text", "loyalty",
         "--discount-id", "loyal10")
    rec = _ledger(env_dir)[-1]
    assert rec["source"] == "owner_edit"
    assert "loyal10" in rec["quote_text"]
    assert "Loyal customer 10%" in rec["quote_text"]
    assert rec["quote_total_usd"] == 360


def test_the_discounted_version_carries_the_pricebook_it_was_validated_against(
    bridge_server, env_dir,
):
    """The number DID come from that pricebook, so stamping its version is honest
    provenance — unlike a free-text owner edit, which gets none."""
    _seed_lead(env_dir)
    _run(env_dir, "--decision", "edit", "--edit-text", "loyalty",
         "--discount-id", "loyal10")
    rec = _ledger(env_dir)[-1]
    assert rec["pricebook_version"] == 4
    assert rec["menu_version"] is None, "this script still reads no menu (PR-B v3)"
    assert rec["price_status"] == "estimated", (
        "committed line prices were menu-derived estimates; an all-override "
        "recompute must not be upgraded to 'exact'"
    )


def test_an_owner_edit_without_a_discount_is_unchanged(bridge_server, env_dir):
    """The PR-B v3 no-catalog-provenance rule still holds where it applies."""
    _seed_lead(env_dir)
    assert _run(env_dir, "--decision", "edit", "--edit-text", "add more rice") == 0
    rec = _ledger(env_dir)[-1]
    assert rec["price_status"] == "estimated"
    assert rec["pricebook_version"] is None
    assert rec["menu_version"] is None
    assert _read_lead(env_dir)["quote_total_usd"] == 400, "total untouched"


# ── approve path: text and total must agree ─────────────────────────────────
def test_discount_on_approve_requires_the_server_rendered_quote(bridge_server, env_dir):
    """An LLM-drafted body was written without knowing about the discount, so the
    text the customer reads and the recomputed total would disagree."""
    _seed_lead(env_dir)
    rc = _run(env_dir, "--decision", "approve", "--quote-text-stdin",
              "--discount-id", "flat25",
              stdin_text="For your event on 2026-09-15 for 50 guests. Total $400.")
    assert rc == 2  # EXIT_INVALID_INPUT
    assert _read_lead(env_dir)["status"] == "CUSTOMER_FINALIZED"
    assert bridge_server.requests == []


def test_discount_on_approve_sends_the_recomputed_total_to_the_customer(
    bridge_server, env_dir,
):
    _seed_lead(env_dir)
    rc = _run(env_dir, "--decision", "approve", "--quote-from-lead-state",
              "--discount-id", "flat25")
    assert rc == 0
    assert _read_lead(env_dir)["status"] == "SENT_TO_CUSTOMER"
    sent = json.dumps(bridge_server.requests)
    assert "375" in sent, "the customer must read the discounted total"
    assert "$400" not in sent, "the pre-discount total must not survive in the text"


def test_approve_with_a_discount_commits_a_version(bridge_server, env_dir):
    """Otherwise the discounted number the customer just received would exist on
    the lead but in no committed version."""
    _seed_lead(env_dir)
    _run(env_dir, "--decision", "approve", "--quote-from-lead-state",
         "--discount-id", "flat25")
    rec = _ledger(env_dir)[-1]
    assert rec["source"] == "owner_edit"
    assert rec["quote_total_usd"] == 375
    assert "flat25" in rec["quote_text"]


def test_discount_is_meaningless_on_reject(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir, "--decision", "reject", "--reason", "no capacity",
                "--discount-id", "flat25") == 2
    assert _read_lead(env_dir)["status"] == "CUSTOMER_FINALIZED"


def test_a_lead_with_no_selected_items_cannot_be_discounted(bridge_server, env_dir):
    _seed_lead(env_dir, items=[])
    assert _run(env_dir, "--decision", "edit", "--edit-text", "x",
                "--discount-id", "flat25") == 15
