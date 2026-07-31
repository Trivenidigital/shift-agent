"""M3 — `mark-catering-lead-outcome`: the OWNER recording how a lead ended.

Nothing wrote a commercial outcome before M3: `catering-lead-reconcile` repairs
state-vs-outbound divergence (a different thing, with its own audit variant) and
`set-catering-lead-hold` pauses automation without changing status at all. These
cells pin the outcome writer, and specifically that it cannot force a transition
the state machine forbids.

Windows-runnable: in-process via fixtures_fleet.load_script with the fcntl stub.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "mark-catering-lead-outcome"


@pytest.fixture
def env_dir(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    cfg = {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "O", "phone": "+19045550100", "self_chat_jid": ""},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _seed(env_dir, status="SENT_TO_CUSTOMER"):
    lead = {
        "lead_id": "L0007", "status": status,
        "customer_phone": "+19045550199", "customer_name": "Asha Rao",
        "raw_inquiry": "wedding catering", "original_message_id": "msg_orig",
        "created_at": "2026-07-20T10:00:00-04:00",
        "updated_at": "2026-07-25T10:00:00-04:00",
        "extracted": {"headcount": 80, "event_date": "2026-09-15"},
        "quote_text": "Quote for L0007 ... 80 guests ... 2026-09-15",
        "owner_approval_code": "#ABCDE",
    }
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": [lead]}), encoding="utf-8")


def _read_lead(env_dir):
    doc = json.loads((env_dir / "state" / "catering-leads.json").read_text(encoding="utf-8"))
    return doc["leads"][0]


def _audit(env_dir, type_=None):
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if type_ is None or r["type"] == type_]


def _run(env_dir, *argv):
    mod = load_script("mark_catering_lead_outcome_mod", SCRIPT)
    mod.CONFIG_PATH = env_dir / "config.yaml"
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    old = sys.argv
    sys.argv = ["mark-catering-lead-outcome", "--lead-id", "L0007", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


# ── won ─────────────────────────────────────────────────────────────────────
def test_won_books_the_lead(env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--outcome", "won") == 0
    assert _read_lead(env_dir)["status"] == "BOOKED"


def test_won_lands_in_the_same_state_the_customers_own_acceptance_produces(env_dir):
    """One answer to "how many events are booked?" regardless of who said so."""
    from schemas import CATERING_TRANSITIONS
    _seed(env_dir)
    _run(env_dir, "--outcome", "won")
    assert _read_lead(env_dir)["status"] == "BOOKED"
    assert "BOOKED" in CATERING_TRANSITIONS["SENT_TO_CUSTOMER"]


def test_won_writes_both_audit_rows_with_owner_as_actor(env_dir):
    _seed(env_dir)
    _run(env_dir, "--outcome", "won", "--reason", "confirmed on the phone")
    change = _audit(env_dir, "catering_lead_status_change")[-1]
    assert change["from_status"] == "SENT_TO_CUSTOMER"
    assert change["to_status"] == "BOOKED"
    assert change["actor"] == "owner"
    marked = _audit(env_dir, "catering_lead_outcome_marked")[-1]
    assert marked["outcome"] == "won"
    assert marked["reason"] == "confirmed on the phone"


def test_won_does_not_require_a_reason(env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--outcome", "won") == 0


# ── lost ────────────────────────────────────────────────────────────────────
def test_lost_closes_the_lead_with_the_reason_recorded(env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--outcome", "lost", "--reason", "went with a cheaper caterer") == 0
    assert _read_lead(env_dir)["status"] == "CLOSED"
    marked = _audit(env_dir, "catering_lead_outcome_marked")[-1]
    assert marked["outcome"] == "lost"
    assert marked["reason"] == "went with a cheaper caterer"


def test_lost_without_a_reason_is_refused(env_dir):
    """A lost lead with no reason is a row nobody can learn from."""
    _seed(env_dir)
    assert _run(env_dir, "--outcome", "lost") == 2  # EXIT_INVALID_INPUT
    assert _read_lead(env_dir)["status"] == "SENT_TO_CUSTOMER"
    assert _audit(env_dir) == []


def test_a_booked_lead_can_still_be_marked_lost(env_dir):
    """A booking can fall through; BOOKED -> CLOSED is a legal edge."""
    _seed(env_dir, status="BOOKED")
    assert _run(env_dir, "--outcome", "lost", "--reason", "venue cancelled") == 0
    assert _read_lead(env_dir)["status"] == "CLOSED"


# ── the state machine is the authority ──────────────────────────────────────
@pytest.mark.parametrize("status", [
    "NEW", "QUALIFYING", "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED",
    "OWNER_APPROVED", "OWNER_EDITED",
])
def test_won_is_refused_before_the_customer_has_a_quote(env_dir, status):
    """"won" from a pre-quote status would claim an event was booked off a quote
    the customer never received."""
    _seed(env_dir, status=status)
    assert _run(env_dir, "--outcome", "won") == 9  # EXIT_ILLEGAL_TRANSITION
    assert _read_lead(env_dir)["status"] == status
    assert _audit(env_dir) == []


@pytest.mark.parametrize("status", ["CLOSED", "STALE", "OWNER_REJECTED", "NOT_CATERING"])
def test_terminal_leads_are_never_reopened(env_dir, status):
    _seed(env_dir, status=status)
    rc = _run(env_dir, "--outcome", "won")
    assert rc in (2, 9), "a terminal lead must be refused, not transitioned"
    assert _read_lead(env_dir)["status"] == status


def test_already_in_the_target_state_is_a_refused_noop(env_dir):
    _seed(env_dir, status="BOOKED")
    assert _run(env_dir, "--outcome", "won") == 2
    assert _audit(env_dir) == [], "no zero-delta audit churn"


def test_unknown_lead_is_not_found(env_dir):
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"leads": []}), encoding="utf-8")
    assert _run(env_dir, "--outcome", "won") == 4


def test_dry_run_validates_without_mutating(env_dir):
    _seed(env_dir)
    assert _run(env_dir, "--outcome", "won", "--dry-run") == 0
    assert _read_lead(env_dir)["status"] == "SENT_TO_CUSTOMER"
    assert _audit(env_dir) == []


# ── no overlap with the neighbouring scripts ────────────────────────────────
def test_outcome_script_does_not_duplicate_hold_or_reconcile():
    """M4's hold pauses automation without a status change; reconcile repairs
    divergence. Neither belongs here, and re-implementing either would give the
    operator two writers for one concept."""
    t = SCRIPT.read_text(encoding="utf-8")
    assert "on_hold" not in t
    assert "CateringLeadHoldChanged" not in t
    assert "CateringLeadManuallyReconciled" not in t


def test_outcome_script_consults_the_transition_table():
    t = SCRIPT.read_text(encoding="utf-8")
    assert "is_catering_transition_allowed" in t
    assert "CateringLeadOutcomeMarked" in t
