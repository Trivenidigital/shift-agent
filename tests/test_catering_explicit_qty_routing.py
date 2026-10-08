"""cf-router explicit-quantity arm — routing precedence, gates and outcomes.

Drives the REAL cf-router `_try_f7_primary_intercept` against the turn-arbitration
sandbox (real select-catering-proposal in-process, real menu fixture). The one
seam replaced here is the finalize-catering-menu SUBPROCESS the arm spawns: its
argv/items are captured so the precedence cases can assert exactly what the arm
asked finalize to persist. The full real CLI chain (finalize -> owner card ->
`#CODE approve` -> quote) is proven in
tests/e2e/test_catering_lifecycle_deterministic.py.

The invariant most of these pin: once the arm has matched an item, the
option-selection arm NEVER sees the turn ("I'll take 2 trays of Idly" reads as
Option 2 there) — whatever goes wrong afterwards is answered here.
"""
from __future__ import annotations

import json

import pytest

from catering_pricing import derive_item_overrides
from fixtures_fleet import write_catering_pricebook
from schemas import Menu
from test_catering_turn_arbitration_e2e import (
    CHAT, PHONE, _build_sandbox, _event, _load_plugin, _rows_of, _seed_sent_set, _wire,
)

PRECEDENCE_TEXT = "I'll take 2 trays of Idly"
ALLOWLIST_ENV = "CATERING_EXPLICIT_QTY_ALLOWLIST"
_UNSET = object()


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
        self.pages: list = []
        self.send_ok = True


def _setup(tmp_path, monkeypatch, *, allowlist=CHAT, headcount: int | None = 60,
           sent_set: bool = False, status: str | None = None, pricebook_over: dict | None = None):
    sb = _build_sandbox(tmp_path)
    overrides = _overrides(sb)
    write_catering_pricebook(sb.state, item_price_overrides=overrides, **(pricebook_over or {}))
    leads = json.loads(sb.leads.read_text(encoding="utf-8"))
    leads["leads"][0]["extracted"]["headcount"] = headcount
    if status is not None:
        leads["leads"][0]["status"] = status
    sb.leads.write_text(json.dumps(leads), encoding="utf-8")
    if sent_set:
        _seed_sent_set(sb, "L0017")
    hooks, actions = _load_plugin()
    w = _wire(hooks, actions, sb, monkeypatch)
    actions.PRICEBOOK_PATH = sb.pricebook

    # The arm has its OWN allowlist; the automation-control kernel stays off here
    # so nothing about it can be what opens the arm.
    monkeypatch.delenv("CATERING_AUTOMATION_CONTROL_ENABLED", raising=False)
    monkeypatch.delenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", raising=False)
    if allowlist is _UNSET:
        monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    else:
        monkeypatch.setenv(ALLOWLIST_ENV, allowlist)

    arm = _Arm()

    def _finalize(code, message_id, selected_items, quote_total_usd):
        arm.finalize.append({"code": code, "message_id": message_id,
                             "items": selected_items, "total": quote_total_usd})
        return 0, {"replay": False}
    actions.invoke_finalize_selected_items = _finalize

    def _send(chat_id, lead_id, text, message_id=""):
        arm.customer_texts.append(text)
        return arm.send_ok
    actions.send_catering_customer_text = _send
    actions.fire_pushover_alert = lambda title, body, priority=2: arm.pages.append(
        {"title": title, "body": body, "priority": priority})
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


def _canonical_replies(w) -> list:
    return [s for s in w.sends if s["via"] == "canonical"]


def _assert_option_path_never_ran(sb, w) -> None:
    assert _rows_of(sb, "catering_proposal_selected") == [], "Option 2 misread"
    assert _rows_of(sb, "catering_proposal_selection_failed") == []
    assert w.finalize_calls == [], "select-catering-proposal reached finalize"
    sets = json.loads(sb.proposals.read_text(encoding="utf-8"))["sets"]
    assert all(s["status"] == "SENT" for s in sets), "the proposal set was touched"


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
    _assert_option_path_never_ran(sb, w)
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


# ── M2: matched but not finalizable -> HANDLED canonical reply, never Option 2 ─
NOT_DELIVERABLE_BOOK = {"fixed_fees": [{"id": "staff", "name": "Service staff",
                                        "kind": "other", "amount_cents": 2500,
                                        "per_unit": "per_staff", "active": True}]}


@pytest.mark.parametrize("case", ["headcount_missing", "no_pricebook", "corrupt_pricebook",
                                  "not_deliverable"])
def test_matched_but_unfinalizable_is_handled_with_the_canonical_reply(tmp_path, monkeypatch, case):
    kw = {"sent_set": True}
    if case == "headcount_missing":
        kw["headcount"] = None
    if case == "not_deliverable":
        kw["pricebook_over"] = NOT_DELIVERABLE_BOOK
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, **kw)
    if case == "no_pricebook":
        sb.pricebook.unlink()
    if case == "corrupt_pricebook":
        sb.pricebook.write_text('{"version": "not-a-pricebook"', encoding="utf-8")
    before = _lead(sb)

    out = _drive(hooks, actions, PRECEDENCE_TEXT, "wamid.EQ.M2")

    assert out is not None and out["action"] == "skip" and "explicit" in out["reason"], out
    _assert_option_path_never_ran(sb, w)
    assert arm.finalize == [] and arm.customer_texts == [] and arm.pages == []
    assert len(_canonical_replies(w)) == 1
    assert w.amend_captures == []
    assert _lead(sb) == before
    rows = _explicit_rows(sb)
    assert len(rows) == 1, rows
    detail = rows[0]["detail"]
    expected = {"headcount_missing": "headcount_missing",
                "no_pricebook": "pricing_unavailable cause=no_pricebook",
                "corrupt_pricebook": "pricing_unavailable cause=",
                "not_deliverable": "not_deliverable"}[case]
    assert expected in detail, detail
    if case == "corrupt_pricebook":
        assert "cause=no_pricebook" not in detail, "a broken book is not an absent one"
    if case == "not_deliverable":
        assert "missing_fee_input" in detail, detail


# ── M4: CUSTOMER_FINALIZED is answered, never re-finalized ───────────────────
def test_customer_finalized_lead_is_not_refinalized(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, status="CUSTOMER_FINALIZED",
                                           sent_set=True)
    before = _lead(sb)

    out = _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.M4a")

    assert out is not None and out["action"] == "skip" and "explicit" in out["reason"], out
    assert arm.finalize == [], "v2 must not overwrite the v1 the owner may be approving"
    assert len(_canonical_replies(w)) == 1
    _assert_option_path_never_ran(sb, w)
    assert _lead(sb) == before
    assert "not_refinalized status=CUSTOMER_FINALIZED" in _explicit_rows(sb)[0]["detail"]


def test_owner_edited_lead_is_finalized(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, status="OWNER_EDITED")
    _drive(hooks, actions, "10 trays of Idly", "wamid.EQ.M4b")
    assert len(arm.finalize) == 1


# ── M3: an addition after a finalize is an amendment, not a replacement ──────
def test_add_after_finalize_is_captured_not_refinalized(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    _drive(hooks, actions, "10 trays of Idly", "wamid.EQ.M3a")
    assert len(arm.finalize) == 1

    out = _drive(hooks, actions, "Can we also add 4 Pongal?", "wamid.EQ.M3b")

    assert len(arm.finalize) == 1, "the addition REPLACED the order"
    assert len(w.amend_captures) == 1, "kept by the R2A amendment capture"
    assert out is not None and "follow-up" in out["reason"]


# ── M1: removal / replacement wording reaches the existing path untouched ────
@pytest.mark.parametrize("text", ["cancel 10 Idly", "remove the 10 Idly please",
                                  "Instead of 10 Idly, 5 Masala Dosa", "not 10 Idly, 5 Idly"])
def test_removal_wording_never_finalizes_or_clarifies(tmp_path, monkeypatch, text):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    _drive(hooks, actions, text, "wamid.EQ.M1")
    assert arm.finalize == [] and arm.customer_texts == []
    assert _explicit_rows(sb) == []
    assert len(w.amend_captures) == 1


# ── (e)/M6: the arm's own allowlist ──────────────────────────────────────────
@pytest.mark.parametrize("allowlist", [_UNSET, "", "+19995550000"])
def test_closed_allowlist_is_byte_identical_existing_behaviour(tmp_path, monkeypatch, allowlist):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, allowlist=allowlist)

    out = _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.E1")

    assert arm.finalize == [] and arm.customer_texts == []
    assert len(w.amend_captures) == 1
    assert out is not None and "follow-up" in out["reason"]
    assert _explicit_rows(sb) == [], "a closed gate leaves no trace of the arm"


@pytest.mark.parametrize("allowlist", [PHONE, CHAT, "*", f"+19995550000, {PHONE}"])
def test_open_allowlist_runs_the_arm(tmp_path, monkeypatch, allowlist):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, allowlist=allowlist)
    _drive(hooks, actions, "10 trays of Idly, 5 of Chicken Biryani", "wamid.EQ.E2")
    assert len(arm.finalize) == 1
    assert w.amend_captures == []


def test_automation_control_allowlist_no_longer_opens_the_arm(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, allowlist=_UNSET)
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
    _drive(hooks, actions, "10 trays of Idly", "wamid.EQ.E3")
    assert arm.finalize == []


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
    assert "5 x biryani" in text
    assert "Veg Biryani" in text and "Chicken Biryani" in text
    assert "$" not in text, "a clarification never carries a price"
    assert _lead(sb) == before_lead
    assert w.amend_captures == []
    assert len(w.sends) == before_sends, "no owner card, no other send"
    rows = _explicit_rows(sb)
    assert len(rows) == 1 and "unmatched=1" in rows[0]["detail"]


def test_clarification_never_echoes_the_customers_words(tmp_path, monkeypatch):
    from agents.flyer.customer_copy_policy import enforce_free_form_text

    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    _drive(hooks, actions, "10 trays of Idly, 5 confirmed paid Pongal thing", "wamid.EQ.L3")

    assert len(arm.customer_texts) == 1
    text = arm.customer_texts[0]
    assert "confirmed" not in text.lower() and "paid" not in text.lower(), text
    assert "5 x pongal" in text and "Pongal" in text
    verdict = enforce_free_form_text(text)
    assert verdict.passed, verdict.hit_classes


def test_clarification_send_failure_is_still_handled(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, sent_set=True)
    arm.send_ok = False

    out = _drive(hooks, actions, "I'll take 2 trays of Idly, 5 biryani", "wamid.EQ.F2")

    assert len(arm.customer_texts) == 1, "the clarification was attempted"
    assert out is not None and "explicit" in out["reason"], out
    _assert_option_path_never_ran(sb, w)
    assert len(_canonical_replies(w)) == 1
    assert "clarification_send_failed" in _explicit_rows(sb)[0]["detail"]


# ── finalize refusal: HANDLED — never falls into the option-selection arm ────
def test_finalize_refusal_sends_the_canonical_reply_without_paging(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, sent_set=True)
    actions.invoke_finalize_selected_items = lambda *a, **k: (11, {})

    out = _drive(hooks, actions, PRECEDENCE_TEXT, "wamid.EQ.R1")

    assert out is not None and out["action"] == "skip" and "explicit" in out["reason"], out
    _assert_option_path_never_ran(sb, w)
    assert len(_canonical_replies(w)) == 1
    assert arm.customer_texts == [], "no explicit-arm ack on a refusal"
    assert arm.pages == [], "a definite refusal (rc=11) changed nothing — no page"
    assert w.amend_captures == []
    rows = _explicit_rows(sb)
    assert len(rows) == 1 and rows[0]["subprocess_rc"] == 11
    assert "finalize_rc=11" in rows[0]["detail"]


@pytest.mark.parametrize("rc", [124, 1])
def test_finalize_unknown_outcome_is_handled_and_pages(tmp_path, monkeypatch, rc):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, sent_set=True)
    actions.invoke_finalize_selected_items = lambda *a, **k: (rc, {})

    out = _drive(hooks, actions, PRECEDENCE_TEXT, "wamid.EQ.R3")

    assert out is not None and out["action"] == "skip" and "explicit" in out["reason"], out
    _assert_option_path_never_ran(sb, w)
    assert len(_canonical_replies(w)) == 1
    assert len(arm.pages) == 1
    page = arm.pages[0]
    assert page["priority"] == 2
    assert "#GEMAZ" in page["body"] and f"rc={rc}" in page["body"]
    assert "option" not in page["body"].lower(), "the page is about quantities, not an option"
    rows = _explicit_rows(sb)
    assert len(rows) == 1 and rows[0]["subprocess_rc"] == rc
    assert "owner_paged=True" in rows[0]["detail"]


def test_finalize_replay_does_not_re_ack_the_customer(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch)
    actions.invoke_finalize_selected_items = lambda *a, **k: (0, {"replay": True})

    out = _drive(hooks, actions, "10 trays of Idly", "wamid.EQ.R2")

    assert out is not None and out["action"] == "skip"
    assert arm.customer_texts == []


# ── owner never reaches the arm ──────────────────────────────────────────────
def test_owner_sender_never_runs_the_arm(tmp_path, monkeypatch):
    sb, hooks, actions, w, arm, _ = _setup(tmp_path, monkeypatch, allowlist="*")
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

    monkeypatch.delenv(ALLOWLIST_ENV)
    assert hooks._sender_has_explicit_qty_lead(CHAT, text) is False


def test_invoke_finalize_never_scales_to_headcount(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
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
