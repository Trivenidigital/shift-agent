"""Catering multi-message journeys — CHARACTERIZATION tests, in-process variant.

Each test drives REAL catering scripts (and, where the journey starts with a
customer turn, the REAL cf-router plugin) against the sandbox that
``tests/test_catering_turn_arbitration_e2e.py`` builds, with every bridge send
captured. It pins what the code does TODAY. Where today's behaviour is a known
hazard the test is named ``test_known_hazard_*`` and its docstring says so:
changing that behaviour is an operator ruling, and the fix flips the test on
purpose rather than by drift.

Runs on Windows (fcntl stub, same code paths) — the subprocess twin of this
file is ``tests/e2e/test_catering_journeys_characterization.py``, which needs
root/Linux. No LLM, no network.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fixtures_fleet import load_script, read_log_rows, write_catering_pricebook

from test_catering_turn_arbitration_e2e import (
    CHAT, SCRIPTS, _build_sandbox, _load_plugin, _rows_of,
    _run_main, _seed_awaiting_lead, _wire, _wire_bridge,
)


def _leads(sb) -> list[dict]:
    return json.loads(sb.leads.read_text(encoding="utf-8"))["leads"]


def _lead(sb, lead_id: str) -> dict:
    return next(l for l in _leads(sb) if l["lead_id"] == lead_id)


def _run_finalize_autodefault_priced(sb, code: str, message_id: str, sends: list):
    """The arbitration file's `_run_finalize_autodefault`, plus the one path it
    leaves at the deployed default: PRICEBOOK_PATH. Without it the in-process
    finalize reads /opt/shift-agent/state/catering-pricebook.json, finds nothing,
    and freezes a pricebook-less quote — so the pricebook this journey changes
    would never have been the one finalize priced from."""
    mod = load_script("journey_fin_priced", SCRIPTS / "finalize-catering-menu")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.MENU_PATH = sb.menu
    mod.LOG_PATH = sb.log
    mod.LOG_LOCK = Path(str(sb.log) + ".lock")
    mod.PROPOSALS_PATH = sb.proposals
    mod.PROPOSALS_LOCK = Path(str(sb.proposals) + ".lock")
    mod.LEDGER_PATH = sb.state / "catering-quote-ledger.json"
    mod.TEMPLATE_DIR = sb.templates
    mod.PRICEBOOK_PATH = sb.pricebook
    _wire_bridge(mod, sends, "finalize-owner-card")
    return _run_main(mod, ["finalize-catering-menu", "--code", code,
                           "--customer-message-id", message_id,
                           "--logical-turn-id", message_id, "--auto-default"])


# ── Journey 5 ────────────────────────────────────────────────────────────────
def test_known_hazard_pricebook_change_after_finalize_quotes_frozen_price(tmp_path):
    """KNOWN HAZARD (intended-but-unreviewed): the owner changes the pricebook
    AFTER the customer finalized and BEFORE the owner approves. The approve
    sends the price frozen at finalize (`pricing_inputs`), not the current
    pricebook's — the owner approves against a card priced from the OLD book and
    nothing tells them the book has moved since. Frozen-at-finalize is the
    documented intent of `pricing_inputs`, but no ruling has reviewed the
    re-price-or-warn question. Changing this is an operator ruling."""
    sb = _build_sandbox(tmp_path)
    _seed_awaiting_lead(sb, "L0400", "#FNRZ2")

    # 1. finalize against pricebook v1 (no overrides → menu prices).
    owner_sends: list = []
    rc, _out, err = _run_finalize_autodefault_priced(sb, "#FNRZ2", "wamid.J5.FIN", owner_sends)
    assert rc == 0, f"finalize rc={rc} err={err[:400]}"
    finalized = _lead(sb, "L0400")
    assert finalized["status"] == "CUSTOMER_FINALIZED"
    frozen = finalized["pricing_inputs"]
    assert frozen["pricebook_version"] == 1
    frozen_cents = frozen["total_cents"]
    assert frozen_cents > 0 and frozen["line_items"], frozen
    assert len(owner_sends) == 1, "one owner card at finalize"

    # 2. the owner republishes the pricebook: v2, every frozen line repriced UP.
    new_overrides = {li["name"]: li["unit_cents"] + 500 for li in frozen["line_items"]}
    write_catering_pricebook(sb.state, version=2, item_price_overrides=new_overrides)
    assert json.loads(sb.pricebook.read_text(encoding="utf-8"))["version"] == 2

    # 3. owner approves (real apply-catering-owner-decision, bridge captured).
    mod_sends: list = []
    mod = load_script("journey_apply_j5", SCRIPTS / "apply-catering-owner-decision")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.LOG_PATH = sb.log
    mod.LEDGER_PATH = sb.state / "catering-quote-ledger.json"
    mod.PRICEBOOK_PATH = sb.pricebook
    _wire_bridge(mod, mod_sends, "apply-owner-decision")
    rc2, _o2, err2 = _run_main(mod, [
        "apply-catering-owner-decision", "--code", "#FNRZ2", "--decision", "approve",
        "--sender-role", "owner", "--quote-from-lead-state",
        "--logical-turn-id", "wamid.J5.APPROVE"])
    assert rc2 == 0, f"approve rc={rc2} err={err2[:400]}"

    after = _lead(sb, "L0400")
    assert after["status"] == "SENT_TO_CUSTOMER"
    assert after["pricing_inputs"] == frozen, "approve did not re-price the frozen commitment"
    assert len(mod_sends) == 1, mod_sends
    body = mod_sends[0]["message"]
    exact = f"${frozen_cents // 100:,}.{frozen_cents % 100:02d}"
    assert f"Total: {exact}" in body, body
    # Witness the book DID move: the v2 price of the first line is not quoted.
    first = frozen["line_items"][0]
    new_unit = new_overrides[first["name"]]
    assert f"@ ${new_unit // 100:,}.{new_unit % 100:02d}" not in body, body
    old_unit = first["unit_cents"]
    assert f"@ ${old_unit // 100:,}.{old_unit % 100:02d}" in body, body
    sent_rows = _rows_of(sb, "catering_quote_sent")
    assert len(sent_rows) == 1


# ── Journey 7 ────────────────────────────────────────────────────────────────
HINDI_INQUIRY = "हमें 15 जून को 50 लोगों के लिए कैटरिंग चाहिए"  # "we need catering for 50 people on June 15"
ENGLISH_TWIN = "We need catering for 50 people on June 15"


def _fresh_customer_sandbox(tmp_path):
    """The arbitration sandbox seeds an open lead (L0017) for this customer; a
    non-English FIRST inquiry needs a customer with no lead, so the store is
    emptied (no lead content is fabricated)."""
    sb = _build_sandbox(tmp_path)
    sb.leads.write_text(json.dumps({"schema_version": 1, "leads": []}), encoding="utf-8")
    return sb


def _dispatch(hooks, text: str, mid: str):
    return hooks.pre_gateway_dispatch(SimpleNamespace(
        text=text, chat_id=CHAT, message_id=mid, timestamp="1721480400",
        transport="whatsapp"))


def test_english_twin_of_the_non_english_inquiry_creates_a_lead(tmp_path, monkeypatch):
    """Positive control for the hazard below: the SAME sentence in English,
    through the SAME dispatch entry and wiring, does open a lead and send the
    owner card. Without this, "no lead" below could be a dead harness."""
    sb = _fresh_customer_sandbox(tmp_path)
    hooks, actions = _load_plugin()
    w = _wire(hooks, actions, sb, monkeypatch)

    out = _dispatch(hooks, ENGLISH_TWIN, "wamid.J7.EN")
    assert out is not None and out["action"] == "skip", out
    leads = _leads(sb)
    assert len(leads) == 1 and leads[0]["extracted"]["headcount"] == 50, leads
    assert any(s["via"] == "create-catering-lead" for s in w.sends), w.sends
    assert "f7_primary_new_inquiry" in [r.get("reason") for r in _rows_of(sb, "cf_router_intercepted")]


def test_known_hazard_non_english_catering_inquiry_falls_through_to_llm(tmp_path, monkeypatch):
    """KNOWN HAZARD: a Hindi inquiry ("we need catering for 50 people on June
    15") is not recognized by the deterministic classifier
    (`rejected:insufficient_evidence`), so cf-router returns None and the turn
    goes to the LLM: no lead, no owner card, no customer reply from the
    deterministic path. Whether the deterministic path should cover non-English
    inquiries is an operator ruling."""
    sb = _fresh_customer_sandbox(tmp_path)
    hooks, actions = _load_plugin()
    w = _wire(hooks, actions, sb, monkeypatch)

    is_cat, signals = actions.classify_catering(HINDI_INQUIRY)
    assert (is_cat, signals) == (False, ["rejected:insufficient_evidence"])

    out = _dispatch(hooks, HINDI_INQUIRY, "wamid.J7.HI")
    assert out is None, f"router claimed the non-English turn: {out}"
    assert _leads(sb) == [], "a lead was created"
    assert w.sends == [], f"something was sent: {w.sends}"
    assert w.amend_captures == []
    assert [r for r in read_log_rows(sb.log) if str(r.get("type", "")).startswith("catering_")] == []
