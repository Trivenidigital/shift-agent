"""Catering multi-message journeys — CHARACTERIZATION tests on the REAL CLI chain.

Reuses ``test_catering_lifecycle_deterministic``'s harness unchanged: real
scripts as SUBPROCESSES from ``/usr/local/bin``, ``/opt/shift-agent`` symlinked
to a per-test sandbox, the REAL cf-router plugin in-process, and the WhatsApp
bridge stubbed on loopback (the only stubbed seam). Needs root/Linux; skipped on
Windows exactly like the file it imports from.

Each test pins what the code does TODAY across several turns. Where today's
behaviour is a known hazard the test is named ``test_known_hazard_*`` and its
docstring states the hazard: changing it is an operator ruling, so a future fix
flips the test deliberately rather than by drift.

Not covered here on purpose (being fixed on other branches): owner replies that
mix approve/reject words, and a headcount amended after finalize.

The in-process twin (runs on Windows) is ``tests/test_catering_journeys_inprocess.py``
(pricebook change between finalize and approve; non-English inquiry).

No LLM, no key, no network beyond loopback.
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from types import SimpleNamespace

from exit_codes import EXIT_LEAD_ON_HOLD  # noqa: E402
from fixtures_fleet import build_catering_pricebook  # noqa: E402

from test_catering_lifecycle_deterministic import (  # noqa: E402,F401 — `sandbox` is a fixture
    CUSTOMER_JID, CUSTOMER_PHONE, OWNER_JID, _BridgeStub, _base_fields, _future_date,
    _plugin_env, assert_only_stub_saw_traffic, open_lead, pytestmark, run, sandbox,
)

EXPLICIT_ORDER = "10 trays of Idly and 7 Masala Dosa please"
EXPLICIT_ITEMS = [("Idly (3 PCS)", 10), ("Masala Dosa", 7)]


# ── shared setup, copied from the lifecycle file's explicit-quantity tests ────
def _price_menu_exactly(sb) -> dict:
    """The pricebook the explicit-quantity tests install: every menu item priced
    by an override, so the explicit arm's quote is deliverable ("exact")."""
    from catering_pricing import derive_item_overrides
    from schemas import Menu

    menu = Menu.model_validate(json.loads(sb.menu_path.read_text(encoding="utf-8")))
    overrides, _excluded = derive_item_overrides(menu.items)
    (sb.state / "catering-pricebook.json").write_text(
        json.dumps(build_catering_pricebook(item_price_overrides=overrides)), encoding="utf-8")
    return overrides


def _router(sb, monkeypatch):
    from test_catering_turn_arbitration_e2e import _load_plugin

    _plugin_env(sb, monkeypatch)
    hooks, actions = _load_plugin()
    actions.lid_to_phone_via_identify_sender = (
        lambda cid: ("+19045550100", "owner") if cid == OWNER_JID else (CUSTOMER_PHONE, "customer"))
    return hooks, actions


def _customer_turn(hooks, text: str, mid: str):
    return hooks.pre_gateway_dispatch(SimpleNamespace(text=text, chat_id=CUSTOMER_JID, message_id=mid))


def _rows(sb, type_: str) -> list[dict]:
    if not sb.log_path.exists():
        return []
    rows = [json.loads(line) for line in sb.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("type") == type_]


def _expected_cents(overrides: dict) -> int:
    return sum(overrides[name] * qty for name, qty in EXPLICIT_ITEMS)


# ── Journey 1 ────────────────────────────────────────────────────────────────
def test_known_hazard_explicit_finalize_on_held_lead_changes_state_and_cards_owner(sandbox, monkeypatch):
    """KNOWN HAZARD: a lead put ON HOLD with the real `set-catering-lead-hold`
    still FINALIZES through the router when the customer sends explicit
    quantities: `selected_items` and `pricing_inputs` are written, the status
    moves to CUSTOMER_FINALIZED and the owner gets a new approval card — only
    the customer ack is withheld (send-catering-ack refuses, exit 14). The owner
    is then shown a card whose approve is refused (apply exits 14, nothing sent,
    F8 returns None so the turn falls to the LLM). A hold withholds sends, not
    state transitions, by design; whether a hold should also freeze customer
    edits / suppress the owner card is an operator ruling."""
    sb = sandbox
    overrides = _price_menu_exactly(sb)
    lead = open_lead(sb, "wamid.J1.001")
    lead_id, code = lead["lead_id"], lead["owner_approval_code"]

    proc = run(sb, "set-catering-lead-hold", "--lead-id", lead_id, "--on",
               "--reason", "customer asked us to wait")
    assert json.loads(proc.stdout.strip().splitlines()[-1])["to_hold"] is True
    held = sb.lead(lead_id)
    assert held["on_hold"] is True and held["status"] == "AWAITING_OWNER_APPROVAL"
    assert not held.get("selected_items")
    owner_before, customer_before = len(sb.to(OWNER_JID)), len(sb.to(CUSTOMER_JID))

    hooks, _actions = _router(sb, monkeypatch)
    out = _customer_turn(hooks, EXPLICIT_ORDER, "wamid.J1.002")
    assert out is not None and out["action"] == "skip" and "explicit" in out["reason"], out

    after = sb.lead(lead_id)
    assert after["on_hold"] is True, "the hold survived the finalize"
    assert after["status"] == "CUSTOMER_FINALIZED", after["status"]
    assert [(i["name"], i["qty"]) for i in after["selected_items"]] == EXPLICIT_ITEMS
    assert after["pricing_inputs"]["total_cents"] == _expected_cents(overrides)
    cards = sb.to(OWNER_JID)[owner_before:]
    assert len(cards) == 1 and f"{code} approve" in cards[0]["message"], cards
    assert sb.to(CUSTOMER_JID)[customer_before:] == [], "the held customer was messaged"
    blocked = _rows(sb, "catering_lead_hold_blocked_send")
    assert [r["blocked_send_kind"] for r in blocked] == ["customer_ack"], blocked
    explicit_rows = [r for r in _rows(sb, "cf_router_intercepted")
                     if str(r.get("detail", "")).startswith("explicit_qty")]
    assert explicit_rows and "ack_sent=False" in explicit_rows[-1]["detail"], explicit_rows

    # The owner answers the card — refused by the hold, nothing reaches anyone.
    owner_before, customer_before = len(sb.to(OWNER_JID)), len(sb.to(CUSTOMER_JID))
    approved = hooks._try_f8_intercept(f"{code} approve", OWNER_JID, "wamid.J1.003")
    assert approved is None, f"a refused approve must fall through, got {approved}"
    final = sb.lead(lead_id)
    assert final["status"] == "CUSTOMER_FINALIZED", "refused before any state mutation"
    assert final["on_hold"] is True
    assert sb.to(CUSTOMER_JID)[customer_before:] == [], "a quote reached the held customer"
    assert sb.to(OWNER_JID)[owner_before:] == [], "the refusal sent the owner a message"
    assert _rows(sb, "catering_quote_sent") == []
    blocked = _rows(sb, "catering_lead_hold_blocked_send")
    assert [r["blocked_send_kind"] for r in blocked] == ["customer_ack", "owner_approved_quote"]
    f8_rows = [r for r in _rows(sb, "cf_router_intercepted") if r.get("reason") == "f8_owner_approve"]
    assert f8_rows and f8_rows[-1]["subprocess_rc"] == EXIT_LEAD_ON_HOLD, f8_rows
    assert_only_stub_saw_traffic(sb)


# ── Journey 2 ────────────────────────────────────────────────────────────────
def test_known_hazard_explicit_quantities_bind_to_newest_of_two_open_leads(sandbox, monkeypatch):
    """KNOWN HAZARD: one customer phone with TWO open leads (two distinct
    inquiries, both AWAITING_OWNER_APPROVAL) sends explicit quantities. The
    router silently binds them to the NEWEST lead (`created_at` desc, element 0
    of find_all_eligible_catering_leads_by_sender) without asking which event
    the order is for; the older lead is untouched. Whether a multi-lead sender
    should be asked to choose is an operator ruling."""
    sb = sandbox
    overrides = _price_menu_exactly(sb)
    older = open_lead(sb, "wamid.J2.001")
    newer = open_lead(sb, "wamid.J2.002",
                      raw="Also need catering for 50 people for a second event",
                      fields=_base_fields(event_date=_future_date(60)))
    assert older["lead_id"] != newer["lead_id"]
    assert older["owner_approval_code"] != newer["owner_approval_code"]
    assert newer["created_at"] > older["created_at"]
    older_before = sb.lead(older["lead_id"])
    owner_before, customer_before = len(sb.to(OWNER_JID)), len(sb.to(CUSTOMER_JID))

    hooks, _actions = _router(sb, monkeypatch)
    out = _customer_turn(hooks, EXPLICIT_ORDER, "wamid.J2.003")
    assert out is not None and out["action"] == "skip", out
    assert newer["lead_id"] in out["reason"], out

    bound = sb.lead(newer["lead_id"])
    assert bound["status"] == "CUSTOMER_FINALIZED"
    assert [(i["name"], i["qty"]) for i in bound["selected_items"]] == EXPLICIT_ITEMS
    assert bound["pricing_inputs"]["total_cents"] == _expected_cents(overrides)
    assert sb.lead(older["lead_id"]) == older_before, "the older lead was modified"
    cards = sb.to(OWNER_JID)[owner_before:]
    assert len(cards) == 1, cards
    assert f"{newer['owner_approval_code']} approve" in cards[0]["message"]
    assert older["owner_approval_code"] not in cards[0]["message"]
    acks = sb.to(CUSTOMER_JID)[customer_before:]
    assert len(acks) == 1 and "saved for owner approval" in acks[0]["message"], acks
    assert not any("which" in m["message"].lower() and "event" in m["message"].lower()
                   for m in acks), "no which-event clarification is asked today"
    assert_only_stub_saw_traffic(sb)


# ── Journey 3 ────────────────────────────────────────────────────────────────
FRESH_INQUIRY = "Hi! Need catering for 40 people for a birthday party, South Indian please"


def test_owner_reject_then_fresh_inquiry_opens_a_new_lead(sandbox, monkeypatch):
    """Owner rejects through the REAL F8 intercept; the same customer then sends
    a fresh inquiry through the REAL router. OWNER_REJECTED is not an open
    status, so the inquiry takes Branch A: a NEW lead with a NEW code; the
    rejected lead is byte-identical afterwards. The inquiry gives no date, venue
    or service style, so the new lead parks in QUALIFYING: the customer is asked
    the intake questions and the owner is NOT carded yet."""
    sb = sandbox
    rejected = open_lead(sb, "wamid.J3.001")
    old_id, old_code = rejected["lead_id"], rejected["owner_approval_code"]
    hooks, _actions = _router(sb, monkeypatch)

    customer_before = len(sb.to(CUSTOMER_JID))
    out = hooks._try_f8_intercept(f"{old_code} reject", OWNER_JID, "wamid.J3.002")
    assert out is not None and out["action"] == "skip", out
    after_reject = sb.lead(old_id)
    assert after_reject["status"] == "OWNER_REJECTED"
    assert sb.to(CUSTOMER_JID)[customer_before:] == [], "reject messaged the customer"

    owner_before, customer_before = len(sb.to(OWNER_JID)), len(sb.to(CUSTOMER_JID))
    out = _customer_turn(hooks, FRESH_INQUIRY, "wamid.J3.003")
    assert out is not None and out["action"] == "skip", out

    fresh = [l for l in sb.leads() if l["lead_id"] != old_id]
    assert len(fresh) == 1, [l["lead_id"] for l in sb.leads()]
    new = fresh[0]
    assert new["status"] == "QUALIFYING"
    assert "event_date" in new["pending_questions"]
    assert new["owner_approval_code"] and new["owner_approval_code"] != old_code
    assert new["original_message_id"] == "wamid.J3.003"
    assert new["extracted"]["headcount"] == 40
    assert sb.lead(old_id) == after_reject, "the rejected lead was touched"
    assert sb.to(OWNER_JID)[owner_before:] == [], "owner carded for a lead still in intake"
    replies = sb.to(CUSTOMER_JID)[customer_before:]
    assert len(replies) == 1, "one bounded customer reply"
    assert new["lead_id"] in replies[0]["message"] and old_id not in replies[0]["message"]
    assert "f7_primary_new_inquiry" in [r.get("reason") for r in _rows(sb, "cf_router_intercepted")]
    assert_only_stub_saw_traffic(sb)


# ── Journey 4 (variant: first send UNCERTAIN) ────────────────────────────────
class _UncertainBridgeStub(_BridgeStub):
    """200 with no id — safe_io's `send_uncertain`: the bridge ACCEPTED the
    message, so it most likely reached the customer. Same shape as
    test_catering_money_boundary's `empty_id` mode."""

    sink: list = []

    def _respond(self, payload: dict) -> None:  # noqa: ARG002 — the id is withheld on purpose
        super()._respond({"accepted": True})


def test_reapprove_after_uncertain_quote_send_does_not_resend(sandbox, monkeypatch):
    """The plain sequential double-approve is pinned in the lifecycle file
    (test_repeat_owner_approve_does_not_send_the_quote_twice). This is the
    variant through the ROUTER where the first quote send came back
    `send_uncertain`: the lead stays OWNER_APPROVED with
    quote_delivery_status=uncertain, and a second `#CODE approve` (bridge healthy
    again) refuses to re-POST — the customer is quoted at most once. Today the
    second approve is reported to F8 as rc=0 (skip), so the owner's chat gets no
    deterministic reply saying the resend was refused (they were paged on the
    first attempt instead)."""
    sb = sandbox
    overrides = _price_menu_exactly(sb)
    lead = open_lead(sb, "wamid.J4.001")
    lead_id, code = lead["lead_id"], lead["owner_approval_code"]
    hooks, _actions = _router(sb, monkeypatch)
    out = _customer_turn(hooks, EXPLICIT_ORDER, "wamid.J4.002")
    assert out is not None and "explicit" in out["reason"], out
    assert sb.lead(lead_id)["status"] == "CUSTOMER_FINALIZED"

    _UncertainBridgeStub.sink = []
    server = HTTPServer(("127.0.0.1", 0), _UncertainBridgeStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("HERMES_BRIDGE_URL", f"http://127.0.0.1:{server.server_port}/send")
        sent_before = len(sb.sent)
        first = hooks._try_f8_intercept(f"{code} approve", OWNER_JID, "wamid.J4.003")
    finally:
        server.shutdown()
    assert first is None, f"rc=6 must fall through to the LLM, got {first}"
    uncertain_posts = [m for m in _UncertainBridgeStub.sink if m.get("chatId") == CUSTOMER_JID]
    assert len(uncertain_posts) == 1, _UncertainBridgeStub.sink
    assert f"Total: ${_expected_cents(overrides) // 100:,}." in uncertain_posts[0]["message"]
    assert len(sb.sent) == sent_before, "the healthy stub saw the uncertain send"
    after_first = sb.lead(lead_id)
    assert after_first["status"] == "OWNER_APPROVED", after_first["status"]
    assert after_first["quote_delivery_status"] == "uncertain"
    unconfirmed = _rows(sb, "catering_customer_send_unconfirmed")
    assert unconfirmed and unconfirmed[-1]["send_status"] == "send_uncertain"
    assert _rows(sb, "catering_quote_sent") == []

    monkeypatch.setenv("HERMES_BRIDGE_URL", sb.bridge_url)
    sent_before = len(sb.sent)
    second = hooks._try_f8_intercept(f"{code} approve", OWNER_JID, "wamid.J4.004")
    assert second is not None and second["action"] == "skip" and "rc=0" in second["reason"], second
    assert len(sb.sent) == sent_before, f"the re-approve sent something: {sb.sent[sent_before:]}"
    assert len(_UncertainBridgeStub.sink) == 1
    after_second = sb.lead(lead_id)
    assert after_second["status"] == "OWNER_APPROVED"
    assert after_second["quote_delivery_status"] == "uncertain"
    assert _rows(sb, "catering_quote_sent") == [], "no delivery fabricated for a send not made"
    assert_only_stub_saw_traffic(sb)


# ── Journey 6 ────────────────────────────────────────────────────────────────
def test_topic_switch_mid_flow_lead_survives_and_explicit_quantities_bind(sandbox, monkeypatch):
    """Customer with an open, non-finalized lead sends an unrelated message
    ("What time do you close today?"): cf-router does not claim it (None → LLM),
    and the lead and every send are untouched. When the customer returns with
    explicit quantities, they bind to that same lead and finalize it."""
    sb = sandbox
    overrides = _price_menu_exactly(sb)
    lead = open_lead(sb, "wamid.J6.001")
    lead_id, code = lead["lead_id"], lead["owner_approval_code"]
    hooks, _actions = _router(sb, monkeypatch)

    before_lead, sent_before = sb.lead(lead_id), len(sb.sent)
    off_topic = _customer_turn(hooks, "What time do you close today?", "wamid.J6.002")
    assert off_topic is None, off_topic
    assert sb.lead(lead_id) == before_lead, "the off-topic turn touched the lead"
    assert len(sb.sent) == sent_before, "the off-topic turn sent something"
    assert len(sb.leads()) == 1

    owner_before, customer_before = len(sb.to(OWNER_JID)), len(sb.to(CUSTOMER_JID))
    out = _customer_turn(hooks, EXPLICIT_ORDER, "wamid.J6.003")
    assert out is not None and "explicit" in out["reason"] and lead_id in out["reason"], out
    bound = sb.lead(lead_id)
    assert bound["status"] == "CUSTOMER_FINALIZED"
    assert [(i["name"], i["qty"]) for i in bound["selected_items"]] == EXPLICIT_ITEMS
    assert bound["pricing_inputs"]["total_cents"] == _expected_cents(overrides)
    assert len(sb.leads()) == 1, "the return turn minted a second lead"
    cards = sb.to(OWNER_JID)[owner_before:]
    assert len(cards) == 1 and f"{code} approve" in cards[0]["message"], cards
    acks = sb.to(CUSTOMER_JID)[customer_before:]
    assert len(acks) == 1 and "saved for owner approval" in acks[0]["message"], acks
    assert_only_stub_saw_traffic(sb)
