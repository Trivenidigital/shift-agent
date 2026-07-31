"""M3 — owner-side `initial_draft` quote-ledger provenance at proposal-send time.

`initial_draft` has been a declared CateringQuoteLedgerRecord.source since PR-B and
nothing ever wrote it, so the first priced artefact for a lead was whatever the
customer finalized — with no record of what the options were worth when they were
offered. create-catering-proposal-options now commits that baseline.

The load-bearing constraint: this is OWNER-SIDE ONLY. The customer-facing proposal
stays price-free (the creative_catering_proposals contract), so the cell asserting
that no price reaches the customer matters more than any of the others here.

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

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import safe_io  # noqa: E402

SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "create-catering-proposal-options"
SKILL_MD = (REPO / "src" / "agents" / "catering" / "skills"
            / "creative_catering_proposals" / "SKILL.md")


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


def _menu():
    def item(name, category, price, tags):
        return {"name": name, "category": category, "price_usd": price,
                "dietary_tags": tags, "available": True}
    return {
        "version": 3,
        "updated_at": "2026-07-01T00:00:00+00:00",
        "items": [
            item("Veg Samosa", "appetizer", 3.0, ["veg"]),
            item("Chicken Tikka", "appetizer", 6.0, ["non-veg"]),
            item("Veg Biryani", "main", 12.0, ["veg"]),
            item("Chicken Curry", "main", 14.0, ["non-veg"]),
            item("Jeera Rice", "side", 5.0, ["veg"]),
            item("Gulab Jamun", "dessert", 4.0, ["veg"]),
        ],
    }


def _pricebook(**over):
    doc = {
        "version": 4, "effective_date": "2026-07-01",
        "updated_at": "2026-07-01T00:00:00+00:00", "currency": "USD",
        "placeholder": False, "per_person_packages": [], "fixed_fees": [],
        "tax_rate_bps": 0, "approved_discounts": [],
        "item_price_overrides": {}, "notes": "",
    }
    doc.update(over)
    return doc


@pytest.fixture
def env_dir(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "catering-menu.json").write_text(
        json.dumps(_menu()), encoding="utf-8")
    (tmp_path / "state" / "catering-pricebook.json").write_text(
        json.dumps(_pricebook()), encoding="utf-8")
    return tmp_path


def _seed_lead(env_dir, *, headcount=50):
    extracted = {"event_date": "2026-09-15", "dietary_restrictions": ["veg", "non-veg"]}
    if headcount is not None:
        extracted["headcount"] = headcount
    lead = {
        "lead_id": "L0001", "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199", "customer_name": "Asha Rao",
        "raw_inquiry": "veg and non-veg wedding catering", "original_message_id": "m1",
        "created_at": "2026-07-20T10:00:00-04:00",
        "updated_at": "2026-07-25T10:00:00-04:00",
        "extracted": extracted,
        "quote_text": "placeholder", "owner_approval_code": "#ABCDE",
    }
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead]}), encoding="utf-8")


def _ledger(env_dir):
    path = env_dir / "state" / "catering-quote-ledger.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("records", [])


def _drafts(env_dir):
    return [r for r in _ledger(env_dir) if r["source"] == "initial_draft"]


def _run(env_dir, *extra):
    mod = load_script("create_proposals_draft_mod", SCRIPT)
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.PROPOSALS_PATH = env_dir / "state" / "catering-proposals.json"
    mod.PROPOSALS_LOCK = env_dir / "state" / "catering-proposals.json.lock"
    mod.MENU_PATH = env_dir / "state" / "catering-menu.json"
    mod.PRICEBOOK_PATH = env_dir / "state" / "catering-pricebook.json"
    mod.LEDGER_PATH = env_dir / "state" / "catering-quote-ledger.json"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    mod.LOG_LOCK = env_dir / "logs" / "decisions.log.lock"
    mod.CONFIG_PATH = env_dir / "no-such-config.yaml"
    old = sys.argv
    sys.argv = [
        "create-catering-proposal-options", "--lead-id", "L0001",
        "--customer-jid", "19045550199@s.whatsapp.net",
        "--source-message-id", "m-in-1",
        "--request-text", "please send two menu options",
        "--auto-generate-from-menu", *extra,
    ]
    try:
        return mod.main()
    finally:
        sys.argv = old


# ── the draft is committed ──────────────────────────────────────────────────
def test_sending_options_commits_one_initial_draft_version(bridge_server, env_dir):
    _seed_lead(env_dir)
    assert _run(env_dir) == 0
    drafts = _drafts(env_dir)
    assert len(drafts) == 1
    assert drafts[0]["lead_id"] == "L0001"
    assert drafts[0]["version"] == 1


def test_the_draft_records_every_option_not_just_the_baseline(bridge_server, env_dir):
    """The per-option arithmetic must not be thrown away — it is the answer to
    "what were the options worth when we offered them?"."""
    _seed_lead(env_dir)
    _run(env_dir)
    text = _drafts(env_dir)[0]["quote_text"]
    assert "Option 1" in text and "Option 2" in text
    assert "NOT SENT" in text, "the record must say it is owner-side only"


def test_the_draft_carries_the_catalog_and_pricebook_it_was_computed_from(
    bridge_server, env_dir,
):
    _seed_lead(env_dir)
    _run(env_dir)
    rec = _drafts(env_dir)[0]
    assert rec["pricebook_version"] == 4
    assert rec["menu_version"] == 3
    assert rec["price_status"] in ("estimated", "exact")


def test_the_draft_is_anchored_to_its_proposal_set(bridge_server, env_dir):
    _seed_lead(env_dir)
    _run(env_dir)
    rec = _drafts(env_dir)[0]
    sets = json.loads(
        (env_dir / "state" / "catering-proposals.json").read_text(encoding="utf-8"))["sets"]
    assert rec["source_message_id"] == sets[0]["proposal_set_id"]


def test_the_draft_scales_with_the_headcount(bridge_server, env_dir):
    """M2's whole point: a 50-guest event is not priced as one of each item."""
    _seed_lead(env_dir, headcount=50)
    _run(env_dir)
    assert _drafts(env_dir)[0]["quote_total_usd"] > 0


# ── THE constraint: nothing priced reaches the customer ─────────────────────
def test_no_price_reaches_the_customer(bridge_server, env_dir):
    """The creative_catering_proposals contract keeps options price-free until the
    owner approves. This ledger draft is owner-side provenance ONLY."""
    _seed_lead(env_dir)
    _run(env_dir)
    sent = json.dumps(bridge_server.requests)
    assert "$" not in sent
    for token in ("price", "cost", "total", "Price status"):
        assert token.lower() not in sent.lower()


def test_the_customer_facing_contract_is_still_declared_price_free():
    """Pin the contract this feature must not violate, at its source."""
    if not SKILL_MD.exists():
        pytest.skip("creative_catering_proposals SKILL.md not present in this tree")
    text = SKILL_MD.read_text(encoding="utf-8").lower()
    assert "price" in text, "the SKILL must say something about prices"


# ── idempotency ─────────────────────────────────────────────────────────────
def test_a_second_send_for_the_same_set_does_not_double_commit(bridge_server, env_dir):
    """Dedup is on the proposal_set_id: the ledger's own duplicate refusal is on
    (lead_id, version) and cannot see a re-run, which would just take version 2."""
    _seed_lead(env_dir)
    _run(env_dir)
    mod = load_script("create_proposals_draft_mod2", SCRIPT)
    mod.LEDGER_PATH = env_dir / "state" / "catering-quote-ledger.json"
    sets = json.loads(
        (env_dir / "state" / "catering-proposals.json").read_text(encoding="utf-8"))["sets"]
    assert mod._ledger_already_has_draft("L0001", sets[0]["proposal_set_id"]) is True
    assert mod._ledger_already_has_draft("L0001", "CPS-L0001-999999") is False


def test_a_new_proposal_set_gets_its_own_draft_version(bridge_server, env_dir):
    """A regenerated set is a NEW offer and deserves its own baseline."""
    _seed_lead(env_dir)
    _run(env_dir)
    _run(env_dir)
    drafts = _drafts(env_dir)
    assert len(drafts) == 2
    assert [d["version"] for d in drafts] == [1, 2]
    assert drafts[0]["source_message_id"] != drafts[1]["source_message_id"]


# ── never-guess: no pricebook / no headcount => no draft ────────────────────
def test_no_pricebook_means_no_draft(bridge_server, env_dir):
    _seed_lead(env_dir)
    (env_dir / "state" / "catering-pricebook.json").unlink()
    assert _run(env_dir) == 0, "the send must still succeed"
    assert _drafts(env_dir) == []


def test_unknown_headcount_means_no_draft(bridge_server, env_dir):
    """Without a guest count any total would be a guess, and never-guess beats
    provenance."""
    _seed_lead(env_dir, headcount=None)
    assert _run(env_dir) == 0
    assert _drafts(env_dir) == []


def test_a_ledger_failure_never_fails_the_send(bridge_server, env_dir, monkeypatch):
    """The customer already has the options; provenance must not look like a send
    failure."""
    _seed_lead(env_dir)
    mod = load_script("create_proposals_draft_mod3", SCRIPT)
    monkeypatch.setattr(mod, "_indicative_quotes",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    mod.LEDGER_PATH = env_dir / "state" / "catering-quote-ledger.json"
    lead = type("L", (), {"lead_id": "L0001", "owner_approval_code": "#ABCDE"})()
    mod._append_initial_draft_ledger_best_effort(lead, "CPS-L0001-000001", [])
    assert _drafts(env_dir) == []
