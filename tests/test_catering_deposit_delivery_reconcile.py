"""catering-lead-reconcile --deposit-delivery: the supervised resolution of an
unconfirmed deposit-link send (operator ruling R1).

`catering-mint-deposit` refuses to re-mint while
`deposit_link_delivery_status == "uncertain"`, and nothing else in the tree
ever cleared that marker — so the refusal was permanent and the only exit was
hand-editing catering-leads.json on a live box. R1 requires the opposite: "an
explicit supervised reconciliation can later resolve it to a confirmed-
delivered state or authorize a fresh attempt. Do not make uncertainty
permanently irrecoverable."

Both outcomes live on the existing operator status-correction primitive rather
than a new script. Subprocess cells mirror tests/test_catering_mint_deposit_script.py
(prepared state files + config, assert on file mutations + audit rows + exit
code); the static cells run everywhere.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent
RECONCILE_PATH = (_REPO_ROOT / "src" / "agents" / "catering" / "scripts"
                  / "catering-lead-reconcile")
MINT_PATH = (_REPO_ROOT / "src" / "agents" / "catering" / "scripts"
             / "catering-mint-deposit")

TS = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)

# safe_io needs fcntl; the subprocess cells run on Linux CI / VPS smoke, same
# as the other catering script suites.
_LINUX_ONLY = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering-lead-reconcile depends on safe_io (fcntl — Linux only)",
)


@pytest.fixture
def isolated_state(tmp_path: Path) -> dict:
    state = tmp_path / "state"
    state.mkdir()
    commerce_state = state / "commerce"
    commerce_state.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()

    leads_path = state / "catering-leads.json"
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)

    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(config_path),
        "SHIFT_AGENT_LEADS_PATH": str(leads_path),
        "SHIFT_AGENT_LEADS_LOCK": str(state / "catering-leads.json.lock"),
        "SHIFT_AGENT_LOG_PATH": str(logs / "decisions.log"),
        "COMMERCE_INTENTS_PATH": str(commerce_state / "payment_intents.json"),
        "PYTHONPATH": f"{_REPO_ROOT / 'src' / 'platform'}{os.pathsep}"
                      f"{os.environ.get('PYTHONPATH', '')}",
    }
    return {
        "leads_path": leads_path,
        "log_path": logs / "decisions.log",
        "intents_path": commerce_state / "payment_intents.json",
        "env": env,
    }


def _write_config(path: Path) -> None:
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test", "location_id": "loc_test_01",
            "timezone": "America/New_York", "languages": ["en"],
        },
        "owner": {"name": "Owner", "phone": "+15551234567", "self_chat_jid": ""},
        "limits": {
            "max_outbound_per_day": 2, "max_outbound_per_minute": 30,
            "pending_proposal_ttl_hours": 4, "per_message_timeout_sec": 120,
            "send_failure_retry_count": 1,
        },
        "alerting": {
            "pushover_user_key": "test_user", "pushover_app_token": "test_token",
            "healthchecks_io_url": "", "email": "",
        },
        "backup": {
            "gpg_recipient_email": "test@example.com", "s3_bucket": "", "retention_days": 30,
        },
        "operations": {"business_hours_local": "08:00-22:00"},
        "catering": {"enabled": True, "deposit_pct": 0.25, "deposit_threshold_guests": 50},
        "commerce": {
            "enabled": False,
            "payment_checkout_url_template": "https://pay/?o={order_id}",
            "minimum_deposit_cents": 500,
        },
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _write_uncertain_lead(leads_path: Path, **overrides) -> str:
    """A lead in the state an unconfirmed deposit-link send leaves behind."""
    lead_id = overrides.pop("lead_id", "L0007")
    lead = {
        "lead_id": lead_id,
        "status": "SENT_TO_CUSTOMER",
        "customer_phone": "+15551234567",
        "customer_name": "Lakshmi",
        "raw_inquiry": "catering for 100 on 2026-06-15",
        "original_message_id": "msg_inquiry_001",
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
        "quote_text": "Draft quote (100 guests on 2026-06-15) — Total $600.00",
        "quote_version": 1,
        "quote_total_usd": 600,
        "extracted": {"headcount": 100, "event_date": "2026-06-15"},
        "deposit_required": True,
        "deposit_amount_cents": 15000,
        "deposit_commerce_order_id": "CO00001",
        "deposit_payment_intent_id": "CPI00001",
        "deposit_status": "awaiting_payment",
        "deposit_minted_at": TS.isoformat(),
        "deposit_link_delivery_status": "uncertain",
        "deposit_link_delivery_status_at": TS.isoformat(),
        **overrides,
    }
    leads_path.write_text(json.dumps({"leads": [lead]}, indent=2), encoding="utf-8")
    return lead_id


def _seed_live_intent(intents_path: Path, lead_id: str) -> None:
    intents_path.write_text(json.dumps({"intents": [{
        "intent_id": "CPI00001",
        "order_id": "CO00001",
        "originating_message_id": f"catering_deposit_{lead_id}",
        "amount_cents": 15000,
        "currency": "USD",
        "provider": "placeholder",
        "checkout_url": "https://pay/?o=CO00001",
        "status": "minted",
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
    }]}, indent=2), encoding="utf-8")


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RECONCILE_PATH), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )


def _audit_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in
            log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _lead(leads_path: Path) -> dict:
    return json.loads(leads_path.read_text(encoding="utf-8"))["leads"][0]


def _intents(intents_path: Path) -> list[dict]:
    return json.loads(intents_path.read_text(encoding="utf-8"))["intents"]


# ─────────────────────────────────────────────────────────────────
# R1 outcome 1 — the customer DID get the link
# ─────────────────────────────────────────────────────────────────


@_LINUX_ONLY
def test_confirmed_delivery_clears_the_marker_and_leaves_the_money_alone(isolated_state):
    """`sent` is the truth once the operator has read the chat. The link is the
    one the customer holds, so the binding and the live intent must survive."""
    lead_id = _write_uncertain_lead(isolated_state["leads_path"])
    _seed_live_intent(isolated_state["intents_path"], lead_id)

    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--deposit-delivery", "confirmed-delivered",
             "--reason", "customer replied quoting the link")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"

    lead = _lead(isolated_state["leads_path"])
    assert lead["deposit_link_delivery_status"] == "sent"
    assert lead["deposit_link_delivery_status_at"] != TS.isoformat()
    assert lead["deposit_payment_intent_id"] == "CPI00001"
    assert lead["deposit_commerce_order_id"] == "CO00001"
    assert lead["deposit_status"] == "awaiting_payment"
    assert lead["status"] == "SENT_TO_CUSTOMER", "lead.status is a different axis"

    assert _intents(isolated_state["intents_path"])[0]["status"] == "minted"

    rows = _audit_rows(isolated_state["log_path"])
    reconciled = [r_ for r_ in rows if r_["type"] == "catering_deposit_delivery_reconciled"]
    assert len(reconciled) == 1, [r_["type"] for r_ in rows]
    row = reconciled[0]
    assert row["lead_id"] == lead_id
    assert row["resolution"] == "confirmed_delivered"
    assert row["prior_delivery_status"] == "uncertain"
    assert row["commerce_payment_intent_id"] == "CPI00001"
    assert row["commerce_order_id"] == "CO00001"
    assert row["amount_cents"] == 15000
    assert row["intent_voided"] is False
    assert row["reason"] == "customer replied quoting the link"
    # No lead.status row — nothing about lead.status changed.
    assert "catering_lead_status_change" not in [r_["type"] for r_ in rows]


@_LINUX_ONLY
def test_confirmed_delivery_needs_a_link_to_confirm(isolated_state):
    """An unbound lead has no recorded link, so there is nothing to confirm —
    `--deposit-delivery not-delivered` is the disposition for that shape."""
    lead_id = _write_uncertain_lead(
        isolated_state["leads_path"],
        deposit_payment_intent_id="", deposit_commerce_order_id="",
        deposit_amount_cents=0, deposit_status="none",
    )
    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--deposit-delivery", "confirmed-delivered", "--reason", "x")
    assert r.returncode == 2, f"stdout={r.stdout!r}"
    assert _lead(isolated_state["leads_path"])["deposit_link_delivery_status"] == "uncertain"


# ─────────────────────────────────────────────────────────────────
# R1 outcome 2 — the customer did NOT get the link
# ─────────────────────────────────────────────────────────────────


@_LINUX_ONLY
def test_not_delivered_voids_the_intent_unbinds_the_lead_and_stops_the_lie(isolated_state):
    """The durable lie R1 names: a voided ledger intent under a lead still
    asserting `awaiting_payment`. One command has to resolve both."""
    lead_id = _write_uncertain_lead(isolated_state["leads_path"])
    _seed_live_intent(isolated_state["intents_path"], lead_id)

    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--deposit-delivery", "not-delivered",
             "--reason", "nothing in the customer's chat")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"

    lead = _lead(isolated_state["leads_path"])
    assert lead["deposit_payment_intent_id"] == ""
    assert lead["deposit_commerce_order_id"] == ""
    assert lead["deposit_status"] == "voided"
    assert lead["deposit_link_delivery_status"] == "failed", (
        "the customer received nothing — that is the definite arm's marker")

    intent = _intents(isolated_state["intents_path"])[0]
    assert intent["status"] == "voided"

    rows = _audit_rows(isolated_state["log_path"])
    types = [r_["type"] for r_ in rows]
    assert "commerce_payment_intent_voided" in types, "the ledger must record the void"
    voided_row = next(r_ for r_ in rows if r_["type"] == "commerce_payment_intent_voided")
    assert voided_row["actor"] == "operator"

    reconciled = [r_ for r_ in rows if r_["type"] == "catering_deposit_delivery_reconciled"]
    assert len(reconciled) == 1, types
    assert reconciled[0]["resolution"] == "not_delivered"
    assert reconciled[0]["intent_voided"] is True
    assert reconciled[0]["commerce_payment_intent_id"] == "CPI00001"


@_LINUX_ONLY
def test_not_delivered_on_an_unbound_lead_still_clears_the_marker(isolated_state):
    """The shape the previous release left behind: marker set, nothing bound.
    There is no intent to void, and the disposition must still work."""
    lead_id = _write_uncertain_lead(
        isolated_state["leads_path"],
        deposit_payment_intent_id="", deposit_commerce_order_id="",
        deposit_amount_cents=0, deposit_status="none",
    )
    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--deposit-delivery", "not-delivered", "--reason", "legacy lead")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"

    lead = _lead(isolated_state["leads_path"])
    assert lead["deposit_link_delivery_status"] == "failed"
    row = next(r_ for r_ in _audit_rows(isolated_state["log_path"])
               if r_["type"] == "catering_deposit_delivery_reconciled")
    assert row["intent_voided"] is False
    assert row["commerce_payment_intent_id"] == ""


# ─────────────────────────────────────────────────────────────────
# Scope of the disposition
# ─────────────────────────────────────────────────────────────────


@_LINUX_ONLY
def test_refuses_when_delivery_was_never_uncertain(isolated_state):
    """This mode resolves uncertainty. It is not a general delivery-marker
    editor — a delivered link must not be re-openable from here."""
    lead_id = _write_uncertain_lead(
        isolated_state["leads_path"], deposit_link_delivery_status="sent")
    _seed_live_intent(isolated_state["intents_path"], lead_id)

    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--deposit-delivery", "not-delivered", "--reason", "x")
    assert r.returncode == 2, f"stdout={r.stdout!r}"

    lead = _lead(isolated_state["leads_path"])
    assert lead["deposit_link_delivery_status"] == "sent"
    assert lead["deposit_payment_intent_id"] == "CPI00001"
    assert _intents(isolated_state["intents_path"])[0]["status"] == "minted"
    assert not [r_ for r_ in _audit_rows(isolated_state["log_path"])
                if r_["type"] == "catering_deposit_delivery_reconciled"]


@_LINUX_ONLY
def test_dry_run_reports_the_disposition_without_touching_anything(isolated_state):
    lead_id = _write_uncertain_lead(isolated_state["leads_path"])
    _seed_live_intent(isolated_state["intents_path"], lead_id)

    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--deposit-delivery", "not-delivered", "--reason", "x", "--dry-run")
    assert r.returncode == 0, f"stderr={r.stderr[-800:]!r}"
    assert json.loads(r.stdout.strip().splitlines()[-1])["dry_run"] is True

    lead = _lead(isolated_state["leads_path"])
    assert lead["deposit_link_delivery_status"] == "uncertain"
    assert lead["deposit_payment_intent_id"] == "CPI00001"
    assert _intents(isolated_state["intents_path"])[0]["status"] == "minted"
    assert _audit_rows(isolated_state["log_path"]) == []


@_LINUX_ONLY
def test_the_two_reconcile_axes_are_mutually_exclusive(isolated_state):
    """lead.status correction and deposit-delivery disposition are different
    axes; one invocation does exactly one of them."""
    lead_id = _write_uncertain_lead(isolated_state["leads_path"])
    both = _run(isolated_state["env"], "--lead-id", lead_id,
                "--target-status", "CLOSED",
                "--deposit-delivery", "not-delivered", "--reason", "x")
    assert both.returncode == 2, f"stdout={both.stdout!r}"
    neither = _run(isolated_state["env"], "--lead-id", lead_id, "--reason", "x")
    assert neither.returncode == 2, f"stdout={neither.stdout!r}"


@_LINUX_ONLY
def test_status_correction_still_works_untouched(isolated_state):
    """Fence: the primitive's original job is unchanged by the new mode."""
    lead_id = _write_uncertain_lead(isolated_state["leads_path"])
    r = _run(isolated_state["env"], "--lead-id", lead_id,
             "--target-status", "CLOSED", "--reason", "stale cleanup")
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"

    assert _lead(isolated_state["leads_path"])["status"] == "CLOSED"
    types = [r_["type"] for r_ in _audit_rows(isolated_state["log_path"])]
    assert "catering_lead_status_change" in types
    assert "catering_lead_manually_reconciled" in types
    assert "catering_deposit_delivery_reconciled" not in types


# ─────────────────────────────────────────────────────────────────
# Static contract — R1's "supervised" half
# ─────────────────────────────────────────────────────────────────


def test_the_disposition_is_not_reachable_by_automation():
    """R1: no timer, sweep or retry may take this state. Only an operator
    running the command by hand resolves it."""
    offenders = []
    for path in _REPO_ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "tests" in path.parts:
            continue
        if path.suffix not in {".py", ".sh", ".service", ".timer", ".md", ""}:
            continue
        if path.name in {"catering-lead-reconcile"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "--deposit-delivery" not in text:
            continue
        # Documentation and the owner page may NAME the command; nothing may
        # schedule or subprocess it.
        if path.suffix == ".md" or path.name == "catering-mint-deposit":
            continue
        offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], (
        f"--deposit-delivery is invoked outside an operator's hands: {offenders}")


def test_the_owner_page_names_the_command_that_actually_resolves_it():
    """The refusal page used to tell the operator to "confirm delivery or void
    <intent>", neither of which any command implemented — following it left the
    lead refused forever. It must name the supported resolution instead."""
    text = MINT_PATH.read_text(encoding="utf-8")
    assert "catering-lead-reconcile" in text
    assert "--deposit-delivery confirmed-delivered" in text
    assert "--deposit-delivery not-delivered" in text
    assert "either confirm delivery or void" not in text
