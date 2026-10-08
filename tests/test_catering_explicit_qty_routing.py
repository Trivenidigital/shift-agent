"""cf-router explicit-quantity arm — routing precedence, gates and fall-throughs.

Drives the REAL cf-router `_try_f7_primary_intercept` against the turn-arbitration
sandbox (real select-catering-proposal in-process, real menu fixture). The one
seam replaced here is the finalize-catering-menu SUBPROCESS the arm spawns: its
argv/items are captured so the precedence cases can assert exactly what the arm
asked finalize to persist. The full real CLI chain (finalize -> owner card ->
`#CODE approve` -> quote) is proven in
tests/e2e/test_catering_lifecycle_deterministic.py.
"""
from __future__ import annotations

import json

import pytest

from catering_pricing import derive_item_overrides
from fixtures_fleet import write_catering_pricebook
from schemas import Menu
from test_catering_turn_arbitration_e2e import (
    CHAT, _build_sandbox, _customer_sends, _event, _load_plugin, _rows_of,
    _seed_sent_set, _wire,
)

PRECEDENCE_TEXT = "I'll take 2 trays of Idly"


def _overrides(sb) -> dict:
    """Overrides DERIVED from the copied menu — the live pricebook covers every
    menu name in cents, and this is how it was built."""
    menu = Menu.model_validate(json.loads(sb.menu.read_text(encoding="utf-8")))
    overrides, _excluded = derive_item_overrides(menu.items)
    return overrides


class _Arm:
    def __init__(self):
        self.finalize: list = []
        self.customer_texts: list = []


def _setup(tmp_path, monkeypatch, *, allowlist: str = CHAT, flag: str | None = None,
           headcount: int | None = 60, sent_set: bool = False):
    sb = _build_sandbox(tmp_path)
    overrides = _overrides(sb)
    write_catering_pricebook(sb.state, item_price_overrides=overrides)
    if headcount != 60:
        leads = json.loads(sb.leads.read_text(encoding="utf-8"))
        leads["leads"][0]["extracted"]["headcount"] = headcount
        sb.leads.write_text(json.dumps(leads), encoding="utf-8")
    if sent_set:
        _seed_sent_set(sb, "L0017")
    hooks, actions = _load_plugin()
    w = _wire(hooks, actions, sb, monkeypatch)
    actions.PRICEBOOK_PATH = sb.pricebook

    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", allowlist)
    if flag is None:
        monkeypatch.delenv("CATERING_EXPLICIT_QTY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CATERING_EXPLICIT_QTY_ENABLED", flag)

    arm = _Arm()

    def _finalize(code, message_id, selected_items, quote_total_usd):
        arm.finalize.append({"code": code, "message_id": message_id,
                             "items": selected_items, "total": quote_total_usd})
        return 0, {"replay": False}
    actions.invoke_finalize_selected_items = _finalize

    def _send(chat_id, lead_id, text, message_id=""):
        arm.customer_texts.append(text)
        return True
    actions.send_catering_customer_text = _send
    return sb, hooks, actions, w, arm, overrides


def _drive(hooks, actions, text, mid):
    _is_cat, signals = actions.classify_catering(text)
    return hooks._try_f7_primary_intercept(text, CHAT, _event(mid), signals=signals,
                                           allow_new_lead=True)


def _lead(sb) -> dict:
    return json.loads(sb.leads.read_text(encoding="utf-8"))["leads"][0]


def _explicit_rows(sb) -> list:
    return [r for r in _rows_of(sb, "cf_router_intercepted")
            if str(r.get("detail", "")).startswith("explicit_qty")]


# ── (b) precedence ───────────────────────────────────────────────────────────
def test_item_quantity_beats_option_number_with_a_sent_proposal_set(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, overrides = _setup(tmp_path, monkeypatch, sent_set=True)
    assert actions.is_proposal_selection(PRECEDENCE_TEXT), (
        "precondition: the selection regex DOES misread this as Option 2 — the hazard")

    out = _drive(hooks, actions, PRECEDENCE_TEXT, "wamid.EQ.B1")

    assert out is not None and out["action"] == "skip"
    assert "explicit" in out["reason"], out
    assert len(arm.finalize) == 1, "the explicit arm finalizes exactly once"
    call = arm.finalize[0]
    assert call["code"] == "#GEMAZ"
    assert [(i["name"], i["qty"]) for i in call["items"]] == [("Idly (3 PCS)", 2)]
    unit = overrides["Idly (3 PCS)"]
    assert call["items"][0]["price_usd"] == round(unit / 100)
    # NOT Option 2: the real selection resolver never ran.
    assert _rows_of(sb, "catering_proposal_selected") == []
    assert w.finalize_calls == []
    assert json.loads(sb.proposals.read_text(encoding="utf-8"))["sets"][0]["status"] == "SENT"
    assert len(arm.customer_texts) == 1
    assert "saved for owner approval" in arm.customer_texts[0]
    assert "Idly (3 PCS)" in arm.customer_texts[0]
    assert w.amend_captures == []
    rows = _explicit_rows(sb)
    assert len(rows) == 1 and rows[0]["reason"] == "f7_proposal_selection"
    assert "matched=1" in rows[0]["detail"] and "finalize_rc=0" in rows[0]["detail"]


def test_witness_bare_option_number_still_selects_the_option(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, sent_set=True)

    out = _drive(hooks, actions, "Option 2", "wamid.EQ.B2")

    assert out is not None and "selection" in out["reason"], out
    selected = _rows_of(sb, "catering_proposal_selected")
    assert len(selected) == 1 and selected[0]["option_id"] == "2"
    assert arm.finalize == [], "a bare option number must never reach the explicit arm"


# ── the arm's own pricing: every cent from the kernel ────────────────────────
def test_quote_total_is_the_kernel_subtotal_in_whole_dollars(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, overrides = _setup(tmp_path, monkeypatch)

    _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.P1")

    assert len(arm.finalize) == 1
    call = arm.finalize[0]
    assert [(i["name"], i["qty"]) for i in call["items"]] == [
        ("Idly (3 PCS)", 10), ("Chicken Biryani", 5)]
    cents = overrides["Idly (3 PCS)"] * 10 + overrides["Chicken Biryani"] * 5
    assert call["total"] == round(cents / 100)


# ── (d) headcount missing ────────────────────────────────────────────────────
def test_headcount_missing_does_not_finalize_and_keeps_existing_behaviour(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, headcount=None)
    before = _lead(sb)

    out = _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.D1")

    assert arm.finalize == []
    assert arm.customer_texts == []
    assert len(w.amend_captures) == 1, "fell through to the unchanged R2A capture"
    assert out is not None and "follow-up" in out["reason"]
    assert _lead(sb) == before
    rows = _explicit_rows(sb)
    assert len(rows) == 1 and "fallthrough=headcount_missing" in rows[0]["detail"]


# ── (e) gates: flag off, sender not allowlisted ──────────────────────────────
@pytest.mark.parametrize("gate", ["flag_off", "not_allowlisted", "empty_allowlist"])
def test_gate_closed_is_byte_identical_existing_behaviour(tmp_path, monkeypatch, gate):
    kw = {"flag_off": {"flag": "0"},
          "not_allowlisted": {"allowlist": "19995550000@s.whatsapp.net"},
          "empty_allowlist": {"allowlist": ""}}[gate]
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, **kw)

    out = _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.E1")

    assert arm.finalize == [] and arm.customer_texts == []
    assert len(w.amend_captures) == 1
    assert out is not None and "follow-up" in out["reason"]
    assert _explicit_rows(sb) == [], "a closed gate leaves no trace of the arm"


def test_gate_open_witness_same_text_finalizes(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, flag="1")
    _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.E2")
    assert len(arm.finalize) == 1
    assert w.amend_captures == []


# ── (f) unmatched -> one focused clarification ───────────────────────────────
def test_unmatched_phrase_sends_one_clarification_and_changes_nothing(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    before_lead = _lead(sb)
    before_sends = len(w.sends)

    out = _drive(hooks, actions, "10 trays of Idly, 5 biryani", "wamid.EQ.F1")

    assert out is not None and out["action"] == "skip"
    assert arm.finalize == [], "no state change on a partial match"
    assert len(arm.customer_texts) == 1
    text = arm.customer_texts[0]
    assert "5 biryani" in text
    assert "Veg Biryani" in text and "Chicken Biryani" in text
    assert "$" not in text, "a clarification never carries a price"
    assert _lead(sb) == before_lead
    assert w.amend_captures == []
    assert len(w.sends) == before_sends, "no owner card, no other send"
    rows = _explicit_rows(sb)
    assert len(rows) == 1 and "unmatched=1" in rows[0]["detail"]


# ── finalize refusal: nothing new to the customer, existing arm handles ──────
def test_finalize_nonzero_sends_nothing_and_falls_through(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    actions.invoke_finalize_selected_items = lambda *a, **k: (11, {})

    out = _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.R1")

    assert arm.customer_texts == []
    assert len(w.amend_captures) == 1
    assert out is not None and "follow-up" in out["reason"]
    assert "finalize_rc=11" in _explicit_rows(sb)[0]["detail"]


def test_finalize_replay_does_not_re_ack_the_customer(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    actions.invoke_finalize_selected_items = lambda *a, **k: (0, {"replay": True})

    out = _drive(hooks, actions, "10 trays of Idly", "wamid.EQ.R2")

    assert out is not None and out["action"] == "skip"
    assert arm.customer_texts == []


# ── owner never reaches the arm ──────────────────────────────────────────────
def test_owner_sender_never_runs_the_arm(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    actions.lid_to_phone_via_identify_sender = lambda cid: ("+19045550100", "owner")

    assert _drive(hooks, actions, "10 trays of Idly", "wamid.EQ.O1") is None
    assert arm.finalize == []
    assert hooks._sender_has_explicit_qty_lead(CHAT, "10 trays of Idly") is False


# ── (a) admission: no catering signal, but a finalize-eligible lead + match ──
def test_admission_admits_item_quantities_with_no_catering_signal(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    text = "10 trays of Idly, 4 Masala Dosa"
    is_cat, signals = actions.classify_catering(text)
    assert not (is_cat or hooks._has_f7_followup_signal(signals)
                or actions.is_proposal_selection(text) or actions.is_proposal_request(text)), (
        "precondition: nothing else admits this message")
    assert hooks._sender_has_explicit_qty_lead(CHAT, text) is True
    assert hooks._sender_has_explicit_qty_lead(CHAT, "see you tomorrow") is False

    monkeypatch.setenv("CATERING_EXPLICIT_QTY_ENABLED", "0")
    assert hooks._sender_has_explicit_qty_lead(CHAT, text) is False


def test_invoke_finalize_never_scales_to_headcount(tmp_path, monkeypatch):
    _sb, _hooks, actions, _w, _arm, _ = _setup(tmp_path, monkeypatch)
    # Re-load to get the REAL invoke (the _setup stub replaced it).
    _h, real_actions = _load_plugin()
    seen = {}

    class _R:
        returncode = 0
        stdout = '{"replay": false, "lead_id": "L0017"}\n'
        stderr = ""

    def _run(cmd, **kw):
        seen["cmd"] = [str(c) for c in cmd]
        return _R()
    monkeypatch.setattr(real_actions.subprocess, "run", _run)

    rc, payload = real_actions.invoke_finalize_selected_items(
        "#GEMAZ", "wamid.EQ.I1", [{"name": "Upma", "qty": 3, "price_usd": 8}], 24)

    assert rc == 0 and payload["replay"] is False
    cmd = seen["cmd"]
    assert "--scale-selected-to-headcount" not in cmd
    assert cmd[cmd.index("--code") + 1] == "#GEMAZ"
    assert cmd[cmd.index("--quote-total-usd") + 1] == "24"
    assert json.loads(cmd[cmd.index("--selected-items-json") + 1]) == [
        {"name": "Upma", "qty": 3, "price_usd": 8}]
    assert cmd[cmd.index("--logical-turn-id") + 1] == "wamid.EQ.I1"
