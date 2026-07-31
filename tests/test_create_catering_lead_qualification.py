"""M1 — create-catering-lead's --qualification-gate.

Runs the script's real main() through argparse with the deployed path constants
repointed at a per-test sandbox and the bridge captured (the in-process
patched-module pattern from tests/test_catering_turn_arbitration_e2e.py, made
Windows-runnable by the fcntl stub).

The load-bearing property is BACKWARD COMPATIBILITY: without the flag every path
must behave exactly as it did before M1 — that is what keeps the LLM SKILL path,
the rescue path and every pre-existing catering test unchanged. With the flag, an
under-qualified lead parks in QUALIFYING and the customer gets questions instead
of an owner card for an event nobody can price.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
TEMPLATES = SRC / "agents" / "catering" / "templates"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import catering_qualification as cq  # noqa: E402

FULLY_QUALIFIED = {
    "headcount": 120, "event_date": "2026-09-15", "event_type": "wedding",
    "venue": "Grand Ballroom", "service_style": "buffet",
    "dietary_restrictions": ["veg"],
}
BARELY_ANYTHING = {"headcount": 120}


class Sandbox:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.state = tmp_path / "state"
        self.logs = tmp_path / "logs"
        self.state.mkdir()
        self.logs.mkdir()
        self.leads = self.state / "catering-leads.json"
        self.log = self.logs / "decisions.log"
        self.config = tmp_path / "config.yaml"
        self.config.write_text(yaml.safe_dump({
            "schema_version": 1,
            "customer": {"name": "Test", "location_id": "loc_t", "timezone": "America/New_York"},
            "owner": {"name": "Owner", "phone": "+19045550100",
                      "self_chat_jid": "19045550100@s.whatsapp.net"},
            "limits": {},
            "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
            "backup": {"gpg_recipient_email": "x@y"},
            "catering": {"enabled": True},
        }), encoding="utf-8")
        self.sends: list[dict] = []

    def leads_list(self) -> list[dict]:
        if not self.leads.exists():
            return []
        return json.loads(self.leads.read_text(encoding="utf-8"))["leads"]

    def audit_rows(self, entry_type=None) -> list[dict]:
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if entry_type is None or r.get("type") == entry_type]

    def owner_sends(self):
        return [s for s in self.sends if s["jid"] == "19045550100@s.whatsapp.net"]

    def customer_sends(self):
        return [s for s in self.sends if s["jid"] != "19045550100@s.whatsapp.net"]


@pytest.fixture
def sb(tmp_path, monkeypatch):
    box = Sandbox(tmp_path)
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(box.config))
    monkeypatch.setenv("SHIFT_AGENT_STATE_DIR", str(box.state))
    return box


def _create(sb: Sandbox, fields: dict, *, gate=False, message_id="wamid.NEW",
            phone="+19045550199", raw="need catering", bridge_ok=True):
    mod = load_script("create_lead_under_test", SCRIPTS / "create-catering-lead")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.LOG_PATH = sb.log
    mod.TEMPLATE_DIR = TEMPLATES
    mod.MENU_PATH = sb.state / "catering-menu.json"

    def _bridge(jid, message):
        sb.sends.append({"jid": jid, "message": message})
        return (True, "wamid.OUT") if bridge_ok else (False, "connection refused")
    mod._bridge_post = _bridge

    argv = ["create-catering-lead", "--customer-phone", phone, "--customer-name", "",
            "--raw-inquiry", raw, "--message-id", message_id,
            "--fields-json", json.dumps(fields)]
    if gate:
        argv.append("--qualification-gate")

    old_argv = sys.argv
    sys.argv = argv
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
    stdout = out.getvalue().strip()
    payload = json.loads(stdout.splitlines()[-1]) if stdout else {}
    return rc, payload, err.getvalue()


# ── flag OFF: pre-M1 behaviour, byte for byte ────────────────────────────────
def test_without_the_flag_a_thin_lead_still_goes_straight_to_owner_approval(sb):
    """Backward compatibility. Every existing caller omits the flag; a lead with
    almost nothing extracted must still reach AWAITING_OWNER_APPROVAL, fire the
    owner card and send the F14 proposal, exactly as before M1."""
    rc, payload, _err = _create(sb, BARELY_ANYTHING, gate=False)

    assert rc == 0
    lead = sb.leads_list()[0]
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert lead["pending_questions"] == []
    assert lead["qualification_rounds"] == 0
    assert payload["card_sent"] is True
    assert payload["proposal_sent"] is True
    assert len(sb.owner_sends()) == 1
    assert sb.audit_rows("catering_owner_approval_requested")
    assert sb.audit_rows("catering_lead_qualification_updated") == []


# ── flag ON ──────────────────────────────────────────────────────────────────
def test_gated_thin_lead_parks_in_qualifying_and_asks_questions(sb):
    rc, payload, _err = _create(sb, BARELY_ANYTHING, gate=True)

    assert rc == 0
    lead = sb.leads_list()[0]
    assert lead["status"] == "QUALIFYING"
    assert lead["qualification_rounds"] == 1
    assert lead["pending_questions"] == lead["questions_asked"]
    assert 0 < len(lead["pending_questions"]) <= cq.MAX_QUESTIONS_PER_ROUND
    assert payload["status"] == "QUALIFYING"
    assert payload["qualification_complete"] is False
    assert "guest_count" not in payload["qualification_missing"], "headcount was supplied"


def test_gated_thin_lead_sends_exactly_one_customer_message_and_no_owner_card(sb):
    """Turn arbitration: ONE bounded response. The question batch REPLACES the F14
    sample menu — a menu for an event with no date/size/venue anchors the customer
    on a spread the owner never priced — and no owner card fires for a lead there is
    nothing to approve on yet."""
    _create(sb, BARELY_ANYTHING, gate=True)

    assert len(sb.customer_sends()) == 1
    assert sb.owner_sends() == []
    body = sb.customer_sends()[0]["message"]
    assert cq.QUESTION_TEMPLATES["event_date"] in body
    assert "$" not in body, "HARD RULES: no prices on the intake path"
    assert sb.audit_rows("catering_owner_approval_requested") == []


def test_gated_qualified_lead_behaves_exactly_like_the_ungated_path(sb):
    """The gate only diverts leads BELOW minimum qualification. A complete inquiry
    goes straight to the owner even with the flag on."""
    rc, payload, _err = _create(sb, FULLY_QUALIFIED, gate=True)

    assert rc == 0
    lead = sb.leads_list()[0]
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert lead["pending_questions"] == []
    assert payload["card_sent"] is True
    assert len(sb.owner_sends()) == 1


def test_gated_lead_emits_a_qualification_audit_row_naming_fields_only(sb):
    _create(sb, BARELY_ANYTHING, gate=True, raw="wedding for 120 at our place")

    rows = sb.audit_rows("catering_lead_qualification_updated")
    assert len(rows) == 1
    assert rows[0]["round_number"] == 1
    assert rows[0]["outcome"] == "asked"
    assert rows[0]["complete"] is False
    assert "guest_count" in rows[0]["fields_filled"]
    assert "wedding for 120 at our place" not in json.dumps(rows[0])


def test_gated_lead_status_change_row_records_the_new_status(sb):
    _create(sb, BARELY_ANYTHING, gate=True)

    rows = sb.audit_rows("catering_lead_status_change")
    assert rows[-1]["from_status"] == "NEW"
    assert rows[-1]["to_status"] == "QUALIFYING"
    assert rows[-1]["reason"] == "qualification_incomplete"


def test_gated_lead_still_reserves_an_approval_code(sb):
    """The code belongs to the lead for its whole life and approval_code_pools
    excludes only TERMINAL statuses, so a QUALIFYING lead's code must stay reserved
    against a cross-pool collision — it is simply not shown to the owner yet."""
    rc, payload, _err = _create(sb, BARELY_ANYTHING, gate=True)

    assert rc == 0
    assert payload["approval_code"].startswith("#")
    assert sb.leads_list()[0]["owner_approval_code"] == payload["approval_code"]


def test_failed_question_send_reports_dependency_down_with_the_lead_persisted(sb):
    rc, _payload, _err = _create(sb, BARELY_ANYTHING, gate=True, bridge_ok=False)

    assert rc == 6
    assert sb.leads_list()[0]["status"] == "QUALIFYING"


def test_duplicate_inbound_does_not_create_a_second_lead(sb):
    """Regression guard: the (customer_phone, message_id) idempotency check is
    inside the lock and must keep holding on the gated path."""
    first_rc, first, _e = _create(sb, BARELY_ANYTHING, gate=True, message_id="wamid.SAME")
    second_rc, second, _e2 = _create(sb, BARELY_ANYTHING, gate=True, message_id="wamid.SAME")

    assert first_rc == 0 and second_rc == 0
    assert second["idempotent_replay"] is True
    assert second["lead_id"] == first["lead_id"]
    assert len(sb.leads_list()) == 1
    assert len(sb.customer_sends()) == 1, "a replay must not re-ask the questions"


def test_gated_and_ungated_leads_coexist_in_one_store(sb):
    """Mixed-state safety: the store must load with both a QUALIFYING lead and a
    legacy-shaped AWAITING one, and lead ids must keep incrementing."""
    _create(sb, BARELY_ANYTHING, gate=True, message_id="wamid.A")
    _create(sb, FULLY_QUALIFIED, gate=False, message_id="wamid.B", phone="+19045550200")

    statuses = {l["lead_id"]: l["status"] for l in sb.leads_list()}
    assert statuses == {"L0001": "QUALIFYING", "L0002": "AWAITING_OWNER_APPROVAL"}
