"""The money boundary: what a catering quote costs, and how long it stands.

Every cell here was reproduced against the code before the fix. The numbers in
the docstrings are measured, not illustrative:

  * a package-priced lead worth $4,027 recomputed to $117 the moment the owner
    applied a discount, because the recompute rebuilt the quote from
    `selected_items` — whole dollars, and on a package finalize holding only the
    à-la-carte add-ons;
  * a $75 flat discount on 150 units of a $2.50 item produced the UNDISCOUNTED
    total exactly, because re-deriving cents from whole dollars inflated the
    base by the same $75;
  * a $25.50/guest package for 150 guests failed finalize outright with exit 11,
    reported as an LLM quote mismatch when nothing was wrong with the LLM.

Windows-runnable: the scripts are loaded IN-PROCESS via fixtures_fleet.load_script
with the fcntl stub, mirroring tests/test_catering_owner_discount.py — advisory
locking is a no-op in one test process, and these assertions are far too
load-bearing to skip on half the developers' machines.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import catering_pricing as pricing  # noqa: E402
import safe_io  # noqa: E402

SCRIPTS = REPO / "src" / "agents" / "catering" / "scripts"
TEMPLATES = REPO / "src" / "agents" / "catering" / "templates"
FINALIZE = SCRIPTS / "finalize-catering-menu"
APPLY = SCRIPTS / "apply-catering-owner-decision"
SELECT = SCRIPTS / "select-catering-proposal"
RECORD = SCRIPTS / "record-catering-acceptance"
PRICEBOOK_FIXTURE = REPO / "tests" / "fixtures" / "catering_pricebook_valid.json"

OWNER_JID = "19045550100@s.whatsapp.net"
CUSTOMER_JID = "19045550199@s.whatsapp.net"
CODE = "#ABCDE"

# A port nothing listens on: the "bridge is down" condition, which is the whole
# point of the clarification-rollback cells.
DEAD_BRIDGE_URL = "http://127.0.0.1:9/send"


# ── harness ──────────────────────────────────────────────────────────────────
class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []
    # P17: "ok" -> 200 + {"id":...} (safe_io status "sent");
    #      "empty_id" -> 200 + no id, which is safe_io's `send_uncertain`: the
    #      bridge ACCEPTED the message, so it most likely reached the customer.
    response_mode = "ok"

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
        if self.__class__.response_mode == "empty_id":
            self.wfile.write(json.dumps({"accepted": True}).encode())
        else:
            self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge(monkeypatch):
    _BridgeStub.requests = []
    _BridgeStub.response_mode = "ok"
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/send"
    monkeypatch.setenv("HERMES_BRIDGE_URL", url)
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(safe_io, "BRIDGE_URL", url, raising=False)
    # The ledger enforces owner:group on POSIX and the runner's tmp tree is not
    # owned by shift-agent; its append is best-effort, but telling it the truth
    # keeps the WARN noise out of these runs.
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER", _runner_owner()[0])
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP", _runner_owner()[1])
    try:
        yield _BridgeStub
    finally:
        server.shutdown()


def _runner_owner():
    import os
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


MENU = {
    "version": 7, "updated_at": "2026-07-01T09:00:00Z", "updated_by": "manual",
    "items": [
        {"name": "Samosa", "price_usd": 2.5, "category": "appetizer", "serves": 5},
        {"name": "Gulab Jamun", "price_usd": 4.0, "category": "dessert", "serves": 10},
        {"name": "Paneer Tikka", "price_usd": 12.0, "category": "main", "serves": 8},
    ],
    "notes": "",
}


def _config(**catering_over):
    catering = {"enabled": True, "deposit_pct": 0.0, "min_per_guest_usd": 3.0,
                "proposal_validity_days": 14}
    catering.update(catering_over)
    return {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550100", "self_chat_jid": OWNER_JID},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": catering,
    }


@pytest.fixture
def env(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "templates").mkdir()
    for t in TEMPLATES.glob("*.txt"):
        shutil.copy2(t, tmp_path / "templates" / t.name)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(_config()), encoding="utf-8")
    (tmp_path / "state" / "catering-menu.json").write_text(
        json.dumps(MENU), encoding="utf-8")
    write_pricebook(tmp_path)
    return tmp_path


def write_config(env, **catering_over):
    (env / "config.yaml").write_text(
        yaml.safe_dump(_config(**catering_over)), encoding="utf-8")


def write_pricebook(env, mutate=None):
    doc = json.loads(PRICEBOOK_FIXTURE.read_text(encoding="utf-8"))
    if mutate:
        mutate(doc)
    (env / "state" / "catering-pricebook.json").write_text(
        json.dumps({"schema_version": 1, "pricebook": doc}), encoding="utf-8")


def seed_lead(env, *, headcount=200, status="AWAITING_OWNER_APPROVAL", **over):
    lead = {
        "lead_id": "L0001", "status": status,
        "customer_phone": "+19045550199", "customer_name": "Asha",
        "raw_inquiry": "need catering", "original_message_id": "wamid.1",
        "created_at": "2026-07-30T10:00:00Z", "updated_at": "2026-07-30T10:00:00Z",
        "owner_approval_code": CODE,
        "extracted": {"headcount": headcount, "event_date": "2026-08-15",
                      "event_time": "18:00", "dietary_restrictions": []},
    }
    lead.update(over)
    (env / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead], "next_lead_seq": 2}), encoding="utf-8")
    return lead


def read_lead(env) -> dict:
    return json.loads(
        (env / "state" / "catering-leads.json").read_text(encoding="utf-8"))["leads"][0]


def _bind_paths(mod, env):
    state = env / "state"
    for attr, value in (
        ("CONFIG_PATH", env / "config.yaml"),
        ("LEADS_PATH", state / "catering-leads.json"),
        ("LEADS_LOCK", state / "catering-leads.json.lock"),
        ("MENU_PATH", state / "catering-menu.json"),
        ("PRICEBOOK_PATH", state / "catering-pricebook.json"),
        ("PROPOSALS_PATH", state / "catering-proposals.json"),
        ("PROPOSALS_LOCK", state / "catering-proposals.json.lock"),
        ("LOG_PATH", env / "logs" / "decisions.log"),
        ("LOG_LOCK", env / "logs" / "decisions.log.lock"),
        ("LEDGER_PATH", state / "catering-quote-ledger.json"),
        ("TEMPLATE_DIR", env / "templates"),
        ("NOTIFY_OWNER_BIN", env / "no-such-notify-bin"),
    ):
        if hasattr(mod, attr):
            setattr(mod, attr, value)
    return mod


def _invoke(mod, argv, stdin_text=""):
    old_argv, old_in, old_out = sys.argv, sys.stdin, sys.stdout
    sys.argv = argv
    sys.stdin = io.StringIO(stdin_text)
    buf = io.StringIO()
    sys.stdout = buf
    try:
        rc = mod.main()
    except SystemExit as se:
        rc = se.code if isinstance(se.code, int) else -1
    finally:
        sys.argv, sys.stdin, sys.stdout = old_argv, old_in, old_out
    lines = [ln for ln in buf.getvalue().strip().splitlines() if ln.strip()]
    out = {}
    if lines:
        try:
            out = json.loads(lines[-1])
        except json.JSONDecodeError:
            out = {}
    return rc, out


def run_finalize(env, *cli, message_id="m1"):
    mod = _bind_paths(load_script("mb_finalize", FINALIZE), env)
    return _invoke(mod, ["finalize-catering-menu", "--code", CODE,
                         "--customer-message-id", message_id, *cli])


def run_apply(env, *cli, stdin_text=""):
    mod = _bind_paths(load_script("mb_apply", APPLY), env)
    return _invoke(mod, ["apply-catering-owner-decision", "--code", CODE,
                         "--sender-role", "owner", *cli], stdin_text=stdin_text)


def run_select(env, selection_text, *, message_id="sel1"):
    mod = _bind_paths(load_script("mb_select", SELECT), env)
    return _invoke(mod, ["select-catering-proposal", "--lead-id", "L0001",
                         "--customer-jid", CUSTOMER_JID,
                         "--customer-message-id", message_id,
                         "--selection-text", selection_text])


def run_record(env, message_text, *, message_id="acc1"):
    mod = _bind_paths(load_script("mb_record", RECORD), env)
    return _invoke(mod, ["record-catering-acceptance", "--lead-id", "L0001",
                         "--customer-jid", CUSTOMER_JID,
                         "--customer-message-id", message_id,
                         "--message-text", message_text,
                         "--sender-role", "customer"])


def sent_text(stub) -> str:
    return json.dumps(stub.requests)


def audit(env, row_type: str) -> list[dict]:
    return [r for r in read_log_rows(env / "logs" / "decisions.log")
            if r.get("type") == row_type]


# ── BLOCKER 1 — a package quote survives a discount ──────────────────────────
# 200 guests x $18.00 = $360.00... = 360000c, plus a $45.00 add-on (Gulab Jamun
# x10 at the pricebook's 450c override), plus $75 of fees, taxed at 8.25%:
#   360000 + 4500 + 7500 = 372000c subtotal -> tax 30690c -> 402690c = $4,026.90
# The repeat-customer discount is 10% capped at $50:
#   discount 5000c -> taxable 367000c -> tax 30278c -> 397278c = $3,972.78
PACKAGE_LEAD = ("--package-id", "veg-standard",
                "--selected-items-json", '[{"name":"Gulab Jamun","qty":10,"price_usd":4}]',
                "--quote-total-usd", "3645")


def test_a_package_finalize_prices_the_package_and_the_add_ons(bridge, env):
    """The add-ons used to be dropped from server_total entirely — priced_items
    was emptied whenever a package was in play — while the owner card still
    listed them. $3,978 measured, $4,027 correct."""
    seed_lead(env)
    rc, out = run_finalize(env, *PACKAGE_LEAD)
    assert rc == 0, out
    assert out["quote_total_cents"] == 402690
    assert out["quote_total_usd"] == 4027


def test_discounting_a_package_lead_quotes_the_package_not_the_add_ons(bridge, env):
    """THE blocker: measured $117 against a correct $3,973, a 97% collapse. The
    recompute rebuilt from selected_items, which on a package finalize holds
    only the add-on rows."""
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    rc, _ = run_apply(env, "--decision", "edit", "--edit-text", "loyalty",
                      "--discount-id", "repeat-customer")
    assert rc == 0
    assert read_lead(env)["quote_total_usd"] == 3973


def test_the_committed_provenance_carries_the_package_the_line_rows_lost(bridge, env):
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    lead = read_lead(env)
    inputs = lead["pricing_inputs"]
    assert inputs["package_id"] == "veg-standard"
    assert inputs["package_price_per_person_cents"] == 1800
    assert inputs["guest_count"] == 200
    assert inputs["total_cents"] == 402690
    assert [(li["name"], li["qty"], li["unit_cents"]) for li in inputs["line_items"]] == [
        ("Gulab Jamun", 10, 450)
    ], "the add-on keeps its exact 450c override price, not a $5 rounding of it"
    assert lead["selected_items"] == [{"name": "Gulab Jamun", "qty": 10, "price_usd": 5}], (
        "selected_items is unchanged and still whole-dollar — which is exactly "
        "why it cannot be the basis for a recompute"
    )


# ── BLOCKER 2 — real cents survive the round trip ────────────────────────────
def test_a_discount_is_not_cancelled_by_re_rounded_cents(bridge, env):
    """Samosa is $2.50 (250c) and persists as $3. Re-deriving cents from that
    dollar inflated 150 units by $75 — precisely cancelling a $75 discount, so
    the customer's promotion cost the owner nothing and the customer got
    nothing. Measured $375 against a correct $300."""
    write_pricebook(env, lambda pb: pb.update(tax_rate_bps=0, fixed_fees=[]))
    seed_lead(env, headcount=150)
    rc, out = run_finalize(
        env, "--selected-items-json", '[{"name":"Samosa","qty":150,"price_usd":3}]',
        "--quote-total-usd", "375")
    assert rc == 0, out
    assert out["quote_total_cents"] == 37500, "250c x 150, exactly"

    rc, _ = run_apply(env, "--decision", "edit", "--edit-text", "festival promo",
                      "--discount-id", "festival-flat")
    assert rc == 0
    assert read_lead(env)["quote_total_usd"] == 300, "$375.00 - $75.00"


def test_a_cents_bearing_package_price_finalizes_instead_of_failing_the_guard(
    bridge, env,
):
    """$25.50/guest x 150 = $3,825 to the kernel and $3,900 to a whole-dollar
    basket sum. The $75 gap exceeded the tolerance and exited 11 — read as LLM
    hallucination, with nothing wrong on the LLM's side."""
    write_pricebook(env, lambda pb: (
        pb["per_person_packages"][0].update(price_per_person_cents=2550),
        pb.update(tax_rate_bps=0, fixed_fees=[]),
    ))
    seed_lead(env, headcount=150)
    rc, out = run_finalize(env, "--auto-default")
    assert rc == 0, out
    assert out["quote_total_cents"] == 382500, "2550c x 150, not 2600c x 150"


def test_the_persisted_total_is_recorded_in_cents_somewhere(bridge, env):
    """Before provenance, no cents-exact total was persisted ANYWHERE: the lead,
    the ledger and the audit rows were all whole dollars, so the exact number
    could only be re-derived — which is the thing that kept going wrong."""
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    assert read_lead(env)["pricing_inputs"]["total_cents"] == 402690


@pytest.mark.parametrize("cli,headcount,mutate", [
    (PACKAGE_LEAD, 200, None),
    (("--auto-default",), 200, None),
    (("--auto-default",), 37, None),
    (("--selected-items-json", '[{"name":"Samosa","qty":150,"price_usd":3}]',
      "--quote-total-usd", "375"), 150, None),
    (("--auto-default",), 150,
     lambda pb: pb["per_person_packages"][0].update(price_per_person_cents=2550)),
])
def test_a_zero_discount_recompute_reproduces_the_finalize_total_exactly(
    bridge, env, cli, headcount, mutate,
):
    """The property the whole design rests on: rebuilding from the frozen inputs
    with no discount returns the committed total byte-for-byte. If this drifts,
    every discount lands on a base the customer was never quoted."""
    if mutate is not None:
        write_pricebook(env, mutate)
    seed_lead(env, headcount=headcount)
    rc, out = run_finalize(env, *cli)
    assert rc == 0, out

    from schemas import CateringPricingInputs
    inputs = CateringPricingInputs.model_validate(read_lead(env)["pricing_inputs"])
    book = pricing.load_pricebook(env / "state" / "catering-pricebook.json")
    again = pricing.recompute_from_inputs(inputs, pricebook=book, discount_id=None)
    assert again.total_cents == inputs.total_cents == out["quote_total_cents"]
    assert again.subtotal_cents == inputs.subtotal_cents


def test_a_lead_with_no_provenance_is_refused_rather_than_recomputed_wrong(
    bridge, env,
):
    """Leads finalized before provenance existed cannot be discounted soundly —
    that is the whole finding. Refuse and say what to do, never guess."""
    seed_lead(env, status="CUSTOMER_FINALIZED",
              quote_text="Quote for L0001 ... 200 guests ... 2026-08-15",
              quote_total_usd=4027,
              customer_finalized_at="2026-07-30T11:00:00Z",
              selected_items=[{"name": "Gulab Jamun", "qty": 10, "price_usd": 5}])
    rc, _ = run_apply(env, "--decision", "edit", "--edit-text", "loyalty",
                      "--discount-id", "repeat-customer")
    assert rc == 17, "EXIT_PRICING_PROVENANCE_MISSING"
    lead = read_lead(env)
    assert lead["status"] == "CUSTOMER_FINALIZED", "refused before any state change"
    assert lead["quote_total_usd"] == 4027


def test_an_unknown_discount_id_is_still_reported_before_the_lead_is_judged(
    bridge, env,
):
    """A mistyped id is what the owner can fix right now; it is wrong regardless
    of which lead it was aimed at."""
    seed_lead(env, status="CUSTOMER_FINALIZED",
              quote_text="Quote for L0001 ... 200 guests ... 2026-08-15",
              quote_total_usd=4027,
              customer_finalized_at="2026-07-30T11:00:00Z",
              selected_items=[{"name": "Gulab Jamun", "qty": 10, "price_usd": 5}])
    rc, _ = run_apply(env, "--decision", "edit", "--edit-text", "x",
                      "--discount-id", "no-such-discount")
    assert rc == 15, "EXIT_UNKNOWN_DISCOUNT, not the provenance refusal"


# ── BLOCKER 3 — a pending_owner_review price never reaches a customer ────────
def _placeholder(pb):
    pb["placeholder"] = True


def _active_per_staff_fee(pb):
    for fee in pb["fixed_fees"]:
        if fee["id"] == "server":
            fee["active"] = True


def test_a_placeholder_pricebook_blocks_the_customer_send(bridge, env):
    """The kernel's docstring has always said this number is not deliverable.
    Nothing enforced it: the caller read total_cents and sent."""
    write_pricebook(env, _placeholder)
    seed_lead(env)
    rc, out = run_finalize(env, *PACKAGE_LEAD)
    assert rc == 0 and out["price_status"] == "pending_owner_review"

    rc, _ = run_apply(env, "--decision", "approve", "--quote-from-lead-state")
    assert rc == 18, "EXIT_PRICE_PENDING_OWNER_REVIEW"
    assert read_lead(env)["status"] == "CUSTOMER_FINALIZED", "no state change"
    assert CUSTOMER_JID not in sent_text(bridge), "the customer must not be sent a price"


def test_the_refusal_names_the_flags_so_the_owner_knows_what_to_fix(bridge, env):
    """"pending owner review" alone does not tell anyone which of the three
    causes it was."""
    write_pricebook(env, _placeholder)
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    run_apply(env, "--decision", "approve", "--quote-from-lead-state")
    rows = [r for r in audit(env, "catering_quote_skill_failed")
            if r["reason"] == "price_pending_owner_review"]
    assert rows, "the refusal must leave an audit row"
    assert "placeholder_pricebook" in rows[-1]["detail"]


def test_an_unsupplied_per_unit_fee_multiplier_blocks_the_send_too(bridge, env):
    """Finalize passes no staff count, so an active per_staff fee commits
    UNPRICED — a live customer path, not a seed-data edge case."""
    write_pricebook(env, _active_per_staff_fee)
    seed_lead(env)
    rc, out = run_finalize(env, *PACKAGE_LEAD)
    assert rc == 0
    assert out["price_status"] == "pending_owner_review"
    assert "missing_fee_input:server" in out["pricing_flags"]

    rc, _ = run_apply(env, "--decision", "approve", "--quote-from-lead-state")
    assert rc == 18


def test_supplying_the_missing_multiplier_makes_the_quote_deliverable(bridge, env):
    """The owner knows the staff count; the finalize path never could. Two
    servers at $120 each = 24000c on top of the committed base."""
    write_pricebook(env, _active_per_staff_fee)
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    rc, _ = run_apply(env, "--decision", "approve", "--quote-from-lead-state",
                      "--discount-id", "festival-flat", "--staff-count", "2")
    assert rc == 0, "the fee resolves, so the quote is no longer pending"
    assert read_lead(env)["status"] == "SENT_TO_CUSTOMER"


def test_a_deliverable_quote_is_still_sent(bridge, env):
    """The guard must gate on the kernel's verdict, not on having a pricebook."""
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    rc, _ = run_apply(env, "--decision", "approve", "--quote-from-lead-state")
    assert rc == 0
    assert read_lead(env)["status"] == "SENT_TO_CUSTOMER"


# ── HIGH 4 — every priced customer message states how firm the price is ──────
def test_the_server_rendered_quote_carries_the_price_status_label(bridge, env):
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    assert run_apply(env, "--decision", "approve", "--quote-from-lead-state")[0] == 0
    assert "Price status: exact" in sent_text(bridge)


def test_an_owner_drafted_quote_carries_the_label_too(bridge, env):
    """The stdin path emitted a bare "Total: $X". An owner's prose is not a
    kernel computation, so it floors at "estimated" — but it is still labelled."""
    seed_lead(env, status="CUSTOMER_FINALIZED",
              quote_text="Quote for L0001", quote_total_usd=4027,
              customer_finalized_at="2026-07-30T11:00:00Z",
              selected_items=[{"name": "Gulab Jamun", "qty": 10, "price_usd": 5}])
    rc, _ = run_apply(
        env, "--decision", "approve", "--quote-text-stdin",
        stdin_text="For your event on 2026-08-15 for 200 guests. Total: $4027.")
    assert rc == 0
    assert "Price status: estimated" in sent_text(bridge)


def test_the_label_reflects_the_discounted_recompute(bridge, env):
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    assert run_apply(env, "--decision", "approve", "--quote-from-lead-state",
                     "--discount-id", "festival-flat")[0] == 0
    body = sent_text(bridge)
    assert "Price status: exact" in body
    # The discount comes off the SUBTOTAL and the tax is recomputed on what is
    # left, so it is not $75 off the taxed total:
    #   372000 - 7500 = 364500c taxable -> tax 30071c -> 394571c = $3,945.71
    assert "3946" in body


# ── MEDIUM 5 — a package cannot be quoted above the per-line quantity bound ──
@pytest.mark.parametrize("headcount,expected_rc", [
    (500, 0),      # exactly the bound: itemisable, priced, fine
    (501, 19),     # one over: the row used to clamp and diverge silently
    (2000, 19),    # far over: used to surface as exit 11 "quote mismatch"
])
def test_auto_default_refuses_a_package_above_the_line_quantity_bound(
    bridge, env, headcount, expected_rc,
):
    seed_lead(env, headcount=headcount)
    rc, _out = run_finalize(env, "--auto-default")
    assert rc == expected_rc


def test_the_bound_refusal_leaves_the_lead_untouched(bridge, env):
    seed_lead(env, headcount=501)
    assert run_finalize(env, "--auto-default")[0] == 19
    # The seed dict is written back untouched — no finalize field was ever added.
    lead = read_lead(env)
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert lead.get("quote_total_usd") is None
    assert lead.get("selected_items", []) == []
    assert lead.get("pricing_inputs") is None


def test_an_explicit_package_id_is_bounded_the_same_way(bridge, env):
    seed_lead(env, headcount=501)
    rc, _ = run_finalize(env, "--package-id", "veg-standard",
                         "--selected-items-json",
                         '[{"name":"Gulab Jamun","qty":10,"price_usd":4}]',
                         "--quote-total-usd", "9045")
    assert rc == 19


def test_at_the_bound_the_itemised_quantity_is_the_true_headcount(bridge, env):
    """500 guests must be itemised as 500, not silently clamped to something the
    total does not match."""
    seed_lead(env, headcount=500)
    rc, out = run_finalize(env, "--auto-default")
    assert rc == 0, out
    assert read_lead(env)["selected_items"][0]["qty"] == 500
    assert read_lead(env)["pricing_inputs"]["guest_count"] == 500


# ── MEDIUM 6 — the validity date binds ───────────────────────────────────────
def _proposal_set(env, *, expires_at, status="SENT"):
    row = {
        "proposal_set_id": "CPS-L0001-000001", "lead_id": "L0001", "status": status,
        "created_at": "2026-07-01T10:00:00+00:00",
        "sent_at": "2026-07-01T10:00:00+00:00",
        "expires_at": expires_at.isoformat(),
        "outbound_message_id": "wamid.sent",
        "source_message_id": "wamid.req",
        "options": [
            {"option_id": "1", "style_key": "classic_style", "tier": "classic",
             "item_names": ["Samosa"]},
            {"option_id": "2", "style_key": "premium_style", "tier": "premium",
             "item_names": ["Paneer Tikka"]},
        ],
    }
    (env / "state" / "catering-proposals.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": 2, "sets": [row]}),
        encoding="utf-8")
    return row


def _proposals(env) -> list[dict]:
    return json.loads((env / "state" / "catering-proposals.json")
                      .read_text(encoding="utf-8"))["sets"]


def test_an_expired_proposal_set_cannot_be_selected(bridge, env):
    """The "valid until <date>" line bound nothing: a set could be selected,
    finalized and put in front of the owner months after it lapsed."""
    seed_lead(env)
    _proposal_set(env, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    rc, _ = run_select(env, "option 2")
    assert rc == 4, "EXIT_NOT_FOUND — there is nothing selectable left"
    assert _proposals(env)[0]["status"] == "EXPIRED"
    assert _proposals(env)[0]["selected_option_id"] is None


def test_the_expired_customer_is_told_plainly_and_not_re_offered_the_options(
    bridge, env,
):
    seed_lead(env)
    _proposal_set(env, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    run_select(env, "option 2")
    body = sent_text(bridge)
    assert "expired" in body
    assert "Option 1" not in body, "re-listing dead options invites another pick"


def test_the_expired_selection_leaves_an_audit_row(bridge, env):
    seed_lead(env)
    _proposal_set(env, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    run_select(env, "option 2")
    rows = audit(env, "catering_proposal_selection_failed")
    assert rows and rows[-1]["reason"] == "proposal_expired"


def test_a_live_proposal_set_still_selects(bridge, env):
    """The guard must not retire sets that are still inside their window."""
    seed_lead(env)
    _proposal_set(env, expires_at=datetime.now(timezone.utc) + timedelta(days=3))
    run_select(env, "option 2")
    assert _proposals(env)[0]["status"] != "EXPIRED"


def test_a_set_with_no_expiry_stamp_is_never_expired(bridge, env):
    """Sets sent before M3 carry no deadline — nobody told those customers one,
    so retiring their options on a date they never heard would be the silent
    behaviour change the whole validity design avoids."""
    seed_lead(env)
    row = _proposal_set(env, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    row["expires_at"] = None
    (env / "state" / "catering-proposals.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": 2, "sets": [row]}),
        encoding="utf-8")
    run_select(env, "option 2")
    assert _proposals(env)[0]["status"] != "EXPIRED"


def _sent_lead(env, *, valid_until):
    return seed_lead(
        env, status="SENT_TO_CUSTOMER",
        quote_text="Quote for L0001 ... 200 guests ... 2026-08-15",
        quote_total_usd=4027,
        customer_finalized_at="2026-07-30T11:00:00Z",
        selected_items=[{"name": "Gulab Jamun", "qty": 10, "price_usd": 5}],
        quote_valid_until=(valid_until.isoformat() if valid_until else None),
    )


def test_approve_stamps_the_deadline_it_just_told_the_customer(bridge, env):
    """The promise and the thing that enforces it must name the same day."""
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0
    assert run_apply(env, "--decision", "approve", "--quote-from-lead-state")[0] == 0
    lead = read_lead(env)
    assert lead["quote_valid_until"] is not None
    stamped = datetime.fromisoformat(lead["quote_valid_until"])
    assert f"valid until {stamped.date().isoformat()}" in sent_text(bridge)


def test_accepting_an_expired_quote_does_not_book(bridge, env):
    """Booking here would hold the owner to prices and availability their own
    quote withdrew on a date the customer was shown."""
    _sent_lead(env, valid_until=datetime.now(timezone.utc) - timedelta(days=1))
    rc, out = run_record(env, "we accept")
    assert rc == 0
    assert out["quote_expired"] is True
    lead = read_lead(env)
    assert lead["status"] == "SENT_TO_CUSTOMER", "NOT BOOKED"
    assert lead["customer_acceptance"] is None


def test_the_expired_acceptance_tells_the_customer_and_pages_the_owner(bridge, env):
    _sent_lead(env, valid_until=datetime.now(timezone.utc) - timedelta(days=1))
    run_record(env, "we accept")
    body = sent_text(bridge)
    assert "passed its validity date" in body, "the customer is told, politely"
    assert "NOT booked" in body, "the owner card must be unambiguous"
    rows = audit(env, "catering_lead_acceptance_recorded")
    assert rows and rows[-1]["outcome"] == "acceptance_of_expired"


def test_accepting_on_the_expiry_date_itself_still_books(bridge, env):
    """Documented boundary: the customer was told a calendar DATE, so the quote
    stands through the END of that day in UTC. Comparing instants would retire
    it up to 23h59 early against the only promise they were given."""
    _sent_lead(env, valid_until=datetime.now(timezone.utc))
    rc, out = run_record(env, "we accept")
    assert rc == 0
    assert out.get("quote_expired") is False
    assert read_lead(env)["status"] == "BOOKED"


def test_a_lead_with_no_deadline_stamp_still_books(bridge, env):
    """Leads sent before the stamp existed were never given a deadline."""
    _sent_lead(env, valid_until=None)
    assert run_record(env, "we accept")[0] == 0
    assert read_lead(env)["status"] == "BOOKED"


def test_a_redelivered_expired_acceptance_does_not_page_the_owner_twice(bridge, env):
    _sent_lead(env, valid_until=datetime.now(timezone.utc) - timedelta(days=1))
    run_record(env, "we accept", message_id="acc1")
    first = len(audit(env, "catering_lead_acceptance_recorded"))
    rc, out = run_record(env, "we accept", message_id="acc1")
    assert rc == 0 and out["replayed"] is True
    assert len(audit(env, "catering_lead_acceptance_recorded")) == first


# ── MEDIUM 7 — an undelivered clarification is never counted as asked ────────
def test_an_undelivered_clarification_does_not_burn_the_one_ask(
    bridge, env, monkeypatch,
):
    """The anchor was written under the lock BEFORE the send. A bridge failure
    left the customer un-asked, the anchor blocking every future ask, and nobody
    told."""
    _sent_lead(env, valid_until=None)
    monkeypatch.setattr(safe_io, "BRIDGE_URL", DEAD_BRIDGE_URL, raising=False)
    rc, _ = run_record(env, "ok", message_id="amb1")
    assert rc == 6, "EXIT_DEPENDENCY_DOWN"
    assert read_lead(env)["acceptance_clarification_sent_message_id"] is None, (
        "an un-asked question must not count as asked"
    )


def test_the_lost_clarification_is_audited_and_the_owner_is_paged(bridge, env, monkeypatch):
    _sent_lead(env, valid_until=None)
    monkeypatch.setattr(safe_io, "BRIDGE_URL", DEAD_BRIDGE_URL, raising=False)
    run_record(env, "ok", message_id="amb1")
    rows = [r for r in audit(env, "catering_lead_acceptance_recorded")
            if r["outcome"] == "clarification_send_failed"]
    assert rows, "a silently lost booking question is the failure mode itself"


def test_the_next_message_asks_again_once_the_bridge_is_back(bridge, env, monkeypatch):
    """The proof the rollback is worth anything: the customer gets their one
    question after all."""
    _sent_lead(env, valid_until=None)
    live_url = safe_io.BRIDGE_URL
    monkeypatch.setattr(safe_io, "BRIDGE_URL", DEAD_BRIDGE_URL, raising=False)
    run_record(env, "ok", message_id="amb1")
    _BridgeStub.requests = []

    monkeypatch.setattr(safe_io, "BRIDGE_URL", live_url, raising=False)
    rc, out = run_record(env, "sounds good", message_id="amb2")
    assert rc == 0
    assert out["customer_reply_sent"] is True
    assert "would you like to go ahead and book" in sent_text(bridge)
    assert read_lead(env)["acceptance_clarification_sent_message_id"] == "amb2"


def test_a_delivered_clarification_still_burns_the_one_ask(bridge, env):
    """The ask-once rule is the point of the anchor; the rollback must not
    weaken it on the happy path. A customer who keeps saying "ok" is asked
    exactly once."""
    _sent_lead(env, valid_until=None)
    assert run_record(env, "ok", message_id="amb1")[0] == 0
    assert read_lead(env)["acceptance_clarification_sent_message_id"] == "amb1"
    _BridgeStub.requests = []
    rc, out = run_record(env, "sounds good", message_id="amb2")
    assert rc == 0 and out["replayed"] is True
    assert bridge.requests == [], "no second question"


# ── P17 — the approved quote's delivery status is consumed, not dropped ───────
# `bridge_post` returns a 4-tuple whose `status` distinguishes "the bridge
# accepted it and it most likely arrived" (send_uncertain) from "it definitely
# did not". The 2-tuple adapter this script used flattened both into False, so a
# send_uncertain wrote the "failed" retry anchor and the next approve re-POSTed a
# priced, owner-approved quote to a customer who probably already had it.

def _approve_ready(env):
    seed_lead(env)
    assert run_finalize(env, *PACKAGE_LEAD)[0] == 0


def test_uncertain_quote_send_is_recorded_uncertain_not_failed(bridge, env):
    _approve_ready(env)
    _BridgeStub.response_mode = "empty_id"

    rc, out = run_apply(env, "--decision", "approve", "--quote-from-lead-state")

    lead = read_lead(env)
    assert lead["quote_delivery_status"] == "uncertain", (
        "the bridge accepted the quote; recording a definite failure invites a "
        "duplicate send of a priced message")
    assert lead["quote_delivery_status_at"]
    rows = audit(env, "catering_customer_send_unconfirmed")
    assert rows and rows[-1]["delivery_certainty"] == "uncertain"
    assert rows[-1]["send_kind"] == "approved_quote"
    assert rows[-1]["send_status"] == "send_uncertain"
    assert rows[-1]["script"] == "apply-catering-owner-decision"
    assert out.get("delivery_uncertain") is True
    assert rc == 6


def test_uncertain_quote_send_writes_no_failed_retry_anchor(bridge, env):
    """The "failed" anchor is what tells the retry state-machine to POST again.
    An accepted-but-unconfirmed send must not write it."""
    _approve_ready(env)
    _BridgeStub.response_mode = "empty_id"

    run_apply(env, "--decision", "approve", "--quote-from-lead-state")

    anchors = audit(env, "catering_quote_attempted")
    assert anchors, "the pre-bridge anchor is still written"
    assert [a["bridge_post_outcome"] for a in anchors] == ["unknown"], (
        "no failed-anchor may follow an uncertain send")
    assert audit(env, "catering_quote_sent") == [], (
        "and no delivery may be claimed either — we cannot show one")


def test_replay_after_uncertain_send_does_not_double_send_the_quote(bridge, env):
    """THE pin: approve twice with the same code after an uncertain first send.
    The customer must be quoted once."""
    _approve_ready(env)
    _BridgeStub.response_mode = "empty_id"
    assert run_apply(env, "--decision", "approve", "--quote-from-lead-state")[0] == 6
    posts_after_first = len(_BridgeStub.requests)

    _BridgeStub.response_mode = "ok"          # bridge healthy again
    rc, out = run_apply(env, "--decision", "approve", "--quote-from-lead-state")

    assert rc == 0
    assert out.get("resend_refused") is True
    assert len(_BridgeStub.requests) == posts_after_first, (
        "the second approve must not POST the quote again")
    assert audit(env, "catering_quote_sent") == [], (
        "and must not fabricate a delivery row for a send it did not make")


def test_successful_quote_send_records_sent(bridge, env):
    """Success path unchanged, plus the durable marker."""
    _approve_ready(env)

    rc, _ = run_apply(env, "--decision", "approve", "--quote-from-lead-state")

    lead = read_lead(env)
    assert rc == 0
    assert lead["status"] == "SENT_TO_CUSTOMER"
    assert lead["quote_delivery_status"] == "sent"
    assert audit(env, "catering_customer_send_unconfirmed") == []
    assert audit(env, "catering_quote_sent"), "a real delivery still gets its row"


def test_definite_quote_send_failure_still_allows_retry(bridge, env, monkeypatch):
    """A DEFINITE failure keeps the old behaviour — the customer got nothing, so
    the failed anchor and a later retry are correct. Only the uncertain case
    changes."""
    _approve_ready(env)
    monkeypatch.setenv("HERMES_BRIDGE_URL", DEAD_BRIDGE_URL)
    monkeypatch.setattr(safe_io, "BRIDGE_URL", DEAD_BRIDGE_URL, raising=False)

    rc, _ = run_apply(env, "--decision", "approve", "--quote-from-lead-state")

    lead = read_lead(env)
    assert rc == 6
    assert lead["quote_delivery_status"] == "failed"
    rows = audit(env, "catering_customer_send_unconfirmed")
    assert rows[-1]["delivery_certainty"] == "failed"
    assert "failed" in [a["bridge_post_outcome"] for a in audit(env, "catering_quote_attempted")], (
        "the retry anchor a definite failure needs is still written")
