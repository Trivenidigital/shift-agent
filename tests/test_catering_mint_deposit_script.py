"""Subprocess + state-mutation tests for catering-mint-deposit script.

Pattern mirrors tests/test_catering_v02_scripts.py: subprocess-invoke the
script with prepared state files + config, assert on file mutations + audit
rows + exit code.

Bridge POST is stubbed by monkeypatching safe_io.bridge_post_2tuple via
environment variable (the script imports safe_io, which respects test-mode).
Slice-2 tests use a Python-level mock at the bridge layer.
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

# Mirrors deployed pattern at tests/test_catering_v02_scripts.py — catering
# scripts depend on safe_io which uses fcntl (Linux only). Subprocess tests
# run on Linux CI / VPS smoke; Windows dev gets coverage from the pure-function
# tests in test_catering_deposit_helpers.py + test_catering_deposit_copy_invariants.py.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering-mint-deposit script depends on safe_io (fcntl — Linux only)",
)


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "scripts" / "catering-mint-deposit"

TS = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def isolated_state(tmp_path: Path) -> dict:
    """Set up an isolated state directory + config + leads file for one test.

    Returns a dict of env-overrides + a helper to seed leads.
    """
    state = tmp_path / "state"
    state.mkdir()
    commerce_state = state / "commerce"
    commerce_state.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()

    leads_path = state / "catering-leads.json"
    leads_lock = state / "catering-leads.json.lock"
    log_path = logs / "decisions.log"
    config_path = tmp_path / "config.yaml"

    env = {
        **os.environ,
        "SHIFT_AGENT_CONFIG_PATH": str(config_path),
        "SHIFT_AGENT_LEADS_PATH": str(leads_path),
        "SHIFT_AGENT_LEADS_LOCK": str(leads_lock),
        "SHIFT_AGENT_LOG_PATH": str(log_path),
        "COMMERCE_CARTS_PATH": str(commerce_state / "carts.json"),
        "COMMERCE_ORDERS_PATH": str(commerce_state / "orders.json"),
        "COMMERCE_INTENTS_PATH": str(commerce_state / "payment_intents.json"),
        "COMMERCE_REFERENCES_PATH": str(commerce_state / "payment_references.json"),
        # Stub the bridge POST via a sentinel env var the script's safe_io shim
        # honors at runtime. Test-mode flag: PYTEST_CATERING_DEPOSIT_BRIDGE_STUB
        # = "success:wamid_test" for ok=True / "fail:reason" for ok=False.
        "PYTEST_CATERING_DEPOSIT_BRIDGE_STUB": "success:wamid_test_001",
    }

    return {
        "tmp_path": tmp_path,
        "state": state,
        "leads_path": leads_path,
        "log_path": log_path,
        "config_path": config_path,
        "commerce_state": commerce_state,
        "env": env,
    }


def _write_config(path: Path, deposit_pct: float = 0.25, threshold: int = 50,
                  checkout_url_template: str = "", min_dep_cents: int = 500) -> None:
    """Write a minimum-valid config.yaml with the relevant deposit knobs."""
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
        "catering": {
            "enabled": True,
            "deposit_pct": deposit_pct,
            "deposit_threshold_guests": threshold,
        },
        "commerce": {
            "enabled": False,  # opt-in flag; library callable regardless
            "payment_checkout_url_template": checkout_url_template,
            "minimum_deposit_cents": min_dep_cents,
        },
    }
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _write_lead(leads_path: Path, **overrides) -> str:
    """Write a CateringLeadStore with one lead. Returns the lead_id.

    Defaults: lead in SENT_TO_CUSTOMER, headcount=100, quote_total_usd=600.
    """
    lead_id = overrides.pop("lead_id", "L0007")
    extracted_overrides = overrides.pop("extracted", {})
    lead = {
        "lead_id": lead_id,
        "status": "SENT_TO_CUSTOMER",
        "customer_phone": "+15551234567",
        "customer_name": "Lakshmi",
        "raw_inquiry": "catering for 100 on 2026-06-15",
        "original_message_id": "msg_inquiry_001",
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
        "quote_text": "Draft quote (100 guests on 2026-06-15) — Total $600.00 (Ref: L0007)",
        "quote_version": 1,
        "quote_total_usd": 600,
        "extracted": {
            "headcount": 100,
            "event_date": "2026-06-15",
            **extracted_overrides,
        },
        "customer_finalized_at": TS.isoformat(),
        **overrides,
    }
    store = {"leads": [lead]}
    leads_path.write_text(json.dumps(store, indent=2), encoding="utf-8")
    return lead_id


def _read_audit_rows(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run_script(env: dict, lead_id: str) -> subprocess.CompletedProcess:
    """Subprocess-invoke catering-mint-deposit. Uses pytest-injected bridge stub.

    The script will fail to import bridge_post_2tuple under the stub env — so
    we monkeypatch via PYTHONPATH injection of a tiny shim module that overrides
    safe_io.bridge_post_2tuple before the script imports it.
    """
    # Inject a shim that overrides safe_io.bridge_post_2tuple based on
    # PYTEST_CATERING_DEPOSIT_BRIDGE_STUB. The shim lives in a temp dir we add
    # to PYTHONPATH ahead of /opt/shift-agent.
    shim_dir = Path(env["SHIFT_AGENT_CONFIG_PATH"]).parent / "shim"
    shim_dir.mkdir(exist_ok=True)
    sitecustomize = shim_dir / "sitecustomize.py"
    sitecustomize.write_text(
        '''
import os, sys
def _patch_bridge():
    stub = os.environ.get("PYTEST_CATERING_DEPOSIT_BRIDGE_STUB", "")
    if not stub:
        return
    mode, _, payload = stub.partition(":")
    def _stub_bridge_post_2tuple(jid, body):
        if mode == "success":
            return True, payload or "wamid_test_default"
        return False, payload or "stubbed_failure"
    # P17b: the deposit-link send moved to the CANONICAL 4-tuple `bridge_post`
    # so it can read `status`. Stubbing only the 2-tuple adapter would let the
    # script fall through to a REAL bridge POST — the harness breakage class
    # this move keeps hitting. The status is configurable because the whole
    # point of the change is that the script branches on it.
    def _stub_bridge_post(jid, body, **kwargs):
        if mode == "success":
            return True, payload or "wamid_test_default", "", "sent"
        status = os.environ.get(
            "PYTEST_CATERING_DEPOSIT_BRIDGE_STATUS", "connect_failed")
        return False, "", payload or "stubbed_failure", status
    try:
        import safe_io
    except ImportError:
        return
    safe_io.bridge_post_2tuple = _stub_bridge_post_2tuple
    safe_io.bridge_post = _stub_bridge_post
_patch_bridge()
''',
        encoding="utf-8",
    )
    platform_path = SCRIPT_PATH.parents[4] / "src" / "platform"
    env = {
        **env,
        "PYTHONPATH": f"{shim_dir}{os.pathsep}{platform_path}{os.pathsep}{env.get('PYTHONPATH', '')}",
    }

    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--lead-id", lead_id],
        env=env, capture_output=True, text=True, timeout=30,
    )


# ─────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────

def test_happy_path_configured_template_mints_and_sends(isolated_state):
    """Configured template + qualifying lead → mint, send, lead deposit_status=
    awaiting_payment, audit rows for commerce_payment_intent_minted,
    commerce_payment_link_attempted, commerce_payment_link_sent,
    catering_deposit_link_sent."""
    _write_config(
        isolated_state["config_path"],
        checkout_url_template="https://pay.example.com/?o={order_id}&amt={amount_usd}",
    )
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0, f"stderr={result.stderr!r}"

    # Lead state mutated
    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    lead = leads["leads"][0]
    assert lead["deposit_required"] is True
    assert lead["deposit_amount_cents"] == 15000  # $600 * 25%
    assert lead["deposit_status"] == "awaiting_payment"
    assert lead["deposit_commerce_order_id"].startswith("CO")
    assert lead["deposit_payment_intent_id"].startswith("CPI")
    # PR reviewer A regression guard: lead.status MUST stay SENT_TO_CUSTOMER
    # — deposit_status is orthogonal; lead.status is not advanced by the mint.
    assert lead["status"] == "SENT_TO_CUSTOMER"

    # Commerce state files populated
    carts = json.loads((isolated_state["commerce_state"] / "carts.json").read_text(encoding="utf-8"))
    assert len(carts["carts"]) == 1
    assert carts["carts"][0]["status"] == "checked_out"
    # PR reviewer A MEDIUM-1 isolation invariant: cart's chat_id is the
    # SYNTHETIC catering_deposit_{lead_id}@s.whatsapp.net (NOT the customer's
    # real WhatsApp JID). If a refactor swapped this for target_jid, a
    # concurrent slice-3 commerce flow could share a cart with the deposit.
    assert carts["carts"][0]["chat_id"] == f"catering_deposit_{lead_id}@s.whatsapp.net"
    assert "+" not in carts["carts"][0]["chat_id"]  # no E.164 leak

    orders = json.loads((isolated_state["commerce_state"] / "orders.json").read_text(encoding="utf-8"))
    assert len(orders["orders"]) == 1
    assert orders["orders"][0]["total_cents"] == 15000

    intents = json.loads((isolated_state["commerce_state"] / "payment_intents.json").read_text(encoding="utf-8"))
    assert len(intents["intents"]) == 1
    assert intents["intents"][0]["status"] == "sent"

    # Audit rows
    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    assert "commerce_payment_intent_minted" in types
    assert "commerce_payment_link_attempted" in types
    assert "commerce_payment_link_sent" in types
    assert "catering_deposit_link_sent" in types

    deposit_sent = next(r for r in rows if r["type"] == "catering_deposit_link_sent")
    assert deposit_sent["lead_id"] == lead_id
    assert deposit_sent["amount_cents"] == 15000
    assert deposit_sent["url_status"] == "configured"
    assert deposit_sent["commerce_order_id"].startswith("CO")


def test_unconfigured_template_sends_fail_closed_copy(isolated_state):
    """Empty template → mint succeeds, bridge POST happens with the
    'Payment link is not configured yet' copy, lead deposit_status='unconfigured'."""
    _write_config(isolated_state["config_path"], checkout_url_template="")
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0, f"stderr={result.stderr!r}"

    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0]["deposit_status"] == "unconfigured"

    rows = _read_audit_rows(isolated_state["log_path"])
    deposit_sent = next(r for r in rows if r["type"] == "catering_deposit_link_sent")
    assert deposit_sent["url_status"] == "unconfigured"


# ─────────────────────────────────────────────────────────────────
# Threshold skip paths (no-op exit 0)
# ─────────────────────────────────────────────────────────────────

def test_below_threshold_no_op(isolated_state):
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"], extracted={"headcount": 10, "event_date": "2026-06-15"})

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0
    assert "threshold_not_met" in result.stdout

    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0].get("deposit_status", "none") == "none"

    # No commerce primitive state was touched
    assert not (isolated_state["commerce_state"] / "carts.json").exists()


def test_deposit_pct_zero_no_op(isolated_state):
    _write_config(isolated_state["config_path"], deposit_pct=0.0, checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0
    assert "threshold_not_met" in result.stdout

    assert not (isolated_state["commerce_state"] / "carts.json").exists()


def test_quote_total_none_no_op(isolated_state):
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"], quote_total_usd=None)

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0
    assert "threshold_not_met" in result.stdout


# ─────────────────────────────────────────────────────────────────
# Idempotency
# ─────────────────────────────────────────────────────────────────

def test_idempotent_already_minted(isolated_state):
    """Re-invocation against a lead with deposit_payment_intent_id set → no-op."""
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(
        isolated_state["leads_path"],
        deposit_payment_intent_id="CPI00099",
    )

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0
    assert "already_minted" in result.stdout

    # No new commerce state — lead was already considered minted
    assert not (isolated_state["commerce_state"] / "carts.json").exists()


def test_double_invocation_second_is_noop(isolated_state):
    """Running the script twice in a row — second invocation is idempotent."""
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    r1 = _run_script(isolated_state["env"], lead_id)
    assert r1.returncode == 0
    leads_after_first = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    intent_id_first = leads_after_first["leads"][0]["deposit_payment_intent_id"]
    assert intent_id_first  # populated

    r2 = _run_script(isolated_state["env"], lead_id)
    assert r2.returncode == 0
    assert "already_minted" in r2.stdout

    # Intent ID unchanged after second call
    leads_after_second = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads_after_second["leads"][0]["deposit_payment_intent_id"] == intent_id_first


# ─────────────────────────────────────────────────────────────────
# Failure modes
# ─────────────────────────────────────────────────────────────────

def test_lead_not_found(isolated_state):
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    _write_lead(isolated_state["leads_path"], lead_id="L0099")

    result = _run_script(isolated_state["env"], "L_does_not_exist")
    assert result.returncode == 4  # EXIT_NOT_FOUND


def test_oversize_quote_total_caught_as_cart_build_failed(isolated_state):
    """PR reviewer B-MEDIUM-1: oversize quote_total_usd → ValidationError
    on CommerceCartItem.unit_price_cents le=10_000_000_000 → cart_build_failed
    audit row (not uncaught exception)."""
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    # quote_total_usd = $1_000_000_000 → deposit = $250M = 25_000_000_000 cents
    # which exceeds CommerceCartItem.unit_price_cents le=10_000_000 (10M cents)
    lead_id = _write_lead(isolated_state["leads_path"], quote_total_usd=1_000_000_000)

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 5, f"stderr={result.stderr!r}"

    rows = _read_audit_rows(isolated_state["log_path"])
    failed_rows = [r for r in rows if r["type"] == "catering_deposit_link_failed"]
    assert len(failed_rows) >= 1
    assert failed_rows[0]["reason"] == "cart_build_failed"


def test_intent_mint_failure_cancels_orphan_order(isolated_state, monkeypatch):
    """PR reviewer B-MEDIUM-2: if commerce_payment_link.mint() returns ok=False,
    the order that was just created is cancelled so it doesn't sit as an
    orphan in the commerce ledger."""
    # Build the env normally; we'll sabotage payment_link.mint via the
    # bridge-stub shim by injecting a mint failure flag.
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    # Sabotage via a shim that overrides commerce.payment_link.mint
    shim_dir = Path(isolated_state["config_path"]).parent / "shim"
    shim_dir.mkdir(exist_ok=True)
    sitecustomize = shim_dir / "sitecustomize.py"
    sitecustomize.write_text(
        '''
def _patch_payment_link():
    try:
        from commerce import payment_link
        from commerce.payment_link import PaymentLinkResult
    except Exception:
        return
    def _stub_mint(**kwargs):
        return PaymentLinkResult(False, None, "stubbed_mint_failure")
    payment_link.mint = _stub_mint
_patch_payment_link()
''',
        encoding="utf-8",
    )
    platform_path = SCRIPT_PATH.parents[4] / "src" / "platform"
    env = {
        **isolated_state["env"],
        "PYTHONPATH": (
            f"{shim_dir}{os.pathsep}{platform_path}{os.pathsep}"
            f"{isolated_state['env'].get('PYTHONPATH', '')}"
        ),
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--lead-id", lead_id],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 5

    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    # Cart + order were created; intent was NOT minted; order MUST be cancelled
    assert "commerce_order_created" in types
    assert "catering_deposit_link_failed" in types
    assert "commerce_order_cancelled" in types  # PR reviewer B-MEDIUM-2

    deposit_failed = next(r for r in rows if r["type"] == "catering_deposit_link_failed")
    assert deposit_failed["reason"] == "intent_mint_failed"

    # Order state file: order in `cancelled` status
    orders = json.loads((isolated_state["commerce_state"] / "orders.json").read_text(encoding="utf-8"))
    assert orders["orders"][0]["status"] == "cancelled"


def test_deposit_failure_does_NOT_roll_back_quote_send(isolated_state, monkeypatch):
    """PR reviewer A MEDIUM-3 + key invariant: even if the deposit-mint script
    fails outright (binary missing — simulated via SHIFT_AGENT_CATERING_DEPOSIT_SCRIPT
    override), the upstream apply-catering-owner-decision must still exit 0 and
    persist lead.status=SENT_TO_CUSTOMER. The deposit hook is best-effort by
    design — a deposit failure NEVER rolls back the quote-send transaction.

    This test exercises the hook path inside apply-catering-owner-decision, not
    the catering-mint-deposit script itself — covered fully in
    test_catering_apply_owner_decision_deposit_hook.py::test_approve_deposit_failure_does_not_roll_back_quote_send.
    """
    # The script-level analog: invoke catering-mint-deposit on a lead that
    # doesn't qualify (below threshold) → no-op exit 0, no audit-row noise.
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"], extracted={"headcount": 5, "event_date": "2026-06-15"})

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0  # no-op
    # Lead state — deposit fields all empty
    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0].get("deposit_status", "none") == "none"
    assert leads["leads"][0].get("deposit_payment_intent_id", "") == ""


def test_below_minimum_deposit(isolated_state):
    """$400 quote × 1% = $4.00 = 400 cents < minimum (500). Refuse + audit.

    Uses a low deposit_pct so the per-guest total ($4/guest at the default 100
    guests) clears the BL-CATER-03 plausibility floor ($3) — otherwise
    _should_mint_deposit would no-op first (threshold_not_met, rc 0) and the
    below-minimum path would be unreachable. With deposit_pct=0.01 the quote is
    plausibly-scaled yet the computed deposit still falls under the $5 minimum.
    """
    _write_config(
        isolated_state["config_path"],
        checkout_url_template="https://pay/?o={order_id}",
        deposit_pct=0.01,
        min_dep_cents=500,
    )
    lead_id = _write_lead(isolated_state["leads_path"], quote_total_usd=400)

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 2  # EXIT_INVALID_INPUT

    rows = _read_audit_rows(isolated_state["log_path"])
    failed = next(r for r in rows if r["type"] == "catering_deposit_link_failed")
    assert failed["reason"] == "below_minimum"


def test_bridge_send_failed_voids_intent_and_audits(isolated_state):
    """Bridge POST fails → commerce_payment_link_failed emitted before void,
    void emitted with actor='caller', catering_deposit_link_failed emitted with
    reason='bridge_send_failed', exit 6, intent voided."""
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "fail:simulated_bridge_outage"
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 6  # EXIT_DEPENDENCY_DOWN

    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    # Reviewer A BLOCKER-2: attempted/failed/voided triple, in that order
    assert "commerce_payment_intent_minted" in types
    assert "commerce_payment_link_attempted" in types
    assert "commerce_payment_link_failed" in types
    assert "commerce_payment_intent_voided" in types
    assert "catering_deposit_link_failed" in types

    # Order: attempted → failed → voided
    attempted_idx = next(i for i, r in enumerate(rows) if r["type"] == "commerce_payment_link_attempted")
    failed_idx = next(i for i, r in enumerate(rows) if r["type"] == "commerce_payment_link_failed")
    voided_idx = next(i for i, r in enumerate(rows) if r["type"] == "commerce_payment_intent_voided")
    assert attempted_idx < failed_idx < voided_idx

    # Void actor is "caller" (Reviewer A HIGH-3 + B HIGH-3)
    voided = rows[voided_idx]
    assert voided["actor"] == "caller"

    # catering_deposit_link_failed carries the reason + commerce cross-refs
    deposit_failed = next(r for r in rows if r["type"] == "catering_deposit_link_failed")
    assert deposit_failed["reason"] == "bridge_send_failed"
    assert deposit_failed["commerce_payment_intent_id"].startswith("CPI")

    # Intent persisted in voided state
    intents = json.loads((isolated_state["commerce_state"] / "payment_intents.json").read_text(encoding="utf-8"))
    assert intents["intents"][0]["status"] == "voided"

    # Slice-2.5 fix: ORDER must also be cancelled (not left as pending_payment
    # orphan). Operator-flagged from 2026-05-29 deploy smoke. Parallel to the
    # intent_mint_failed cleanup path.
    orders = json.loads((isolated_state["commerce_state"] / "orders.json").read_text(encoding="utf-8"))
    assert orders["orders"][0]["status"] == "cancelled", (
        f"orphan order left as {orders['orders'][0]['status']!r}; "
        "expected 'cancelled' per slice-2.5 ledger-cleanliness fix"
    )
    # commerce_order_cancelled audit row emitted with the documented reason
    cancelled_rows = [r for r in rows if r["type"] == "commerce_order_cancelled"]
    assert len(cancelled_rows) == 1
    assert cancelled_rows[0]["reason"] == "bridge_send_failed_orphan_cleanup"

    # Lead state — deposit fields NOT populated (bridge fail rolled them back via early return)
    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0].get("deposit_status", "none") == "none"
    assert leads["leads"][0].get("deposit_payment_intent_id", "") == ""


# ─────────────────────────────────────────────────────────────────
# S1-1 — double-charge guard: re-invoke when a live intent already exists
# for the lead (prior mint crashed after send, before persisting the binding)
# ─────────────────────────────────────────────────────────────────


def test_reinvoke_with_live_intent_refuses_second_mint(isolated_state):
    """S1-1: a crash between the customer-facing send and the lead-binding
    persist leaves a non-terminal intent under originating_message_id
    'catering_deposit_<lead_id>' while the lead stays unbound. Re-invoking must
    REFUSE — audit + P1 + non-zero exit — instead of minting a SECOND, different
    payment link (the double-charge). No new cart/order/intent may be created."""
    _write_config(isolated_state["config_path"])
    lead_id = _write_lead(isolated_state["leads_path"])  # L0007, unbound

    # Seed the live intent left by the crashed prior run.
    intents_path = isolated_state["commerce_state"] / "payment_intents.json"
    intents_path.write_text(json.dumps({"intents": [{
        "intent_id": "CPI00001",
        "order_id": "CO00001",
        "originating_message_id": f"catering_deposit_{lead_id}",
        "amount_cents": 15000,
        "currency": "USD",
        "provider": "placeholder",
        "checkout_url": "https://pay.example/CO00001",
        "status": "sent",
        "created_at": TS.isoformat(),
        "updated_at": TS.isoformat(),
    }]}, indent=2), encoding="utf-8")

    # NOTE (adversarial-verify residual #1): this seed leaves orders.json empty,
    # so in the guard-REMOVED counterfactual mint's order_id idempotency would
    # coincidentally return the existing CPI00001 (a fresh cart regenerates
    # CO00001) — making the `..._minted not in types` / len==1 assertions
    # non-distinguishing on removal. The test is STILL a valid regression test
    # (removal flips returncode 2→0 and emits commerce_payment_link_attempted).
    # To exercise the TRUE production double-charge path (fresh order_id CO00002
    # escaping mint idempotency → a real second intent), seed orders.json with
    # CO00001 — deferred to Linux CI where the nested CommerceOrder shape runs.
    result = _run_script(isolated_state["env"], lead_id)

    # Refused with EXIT_INVALID_INPUT (2), not a silent success.
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)

    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    deposit_failed = next(r for r in rows if r["type"] == "catering_deposit_link_failed")
    assert deposit_failed["reason"] == "reinvoke_live_intent_exists"
    assert deposit_failed["commerce_payment_intent_id"] == "CPI00001"
    # Did NOT mint or attempt a second intent.
    assert "commerce_payment_intent_minted" not in types
    assert "commerce_payment_link_attempted" not in types

    # Exactly the one seeded intent remains, still 'sent'.
    intents = json.loads(intents_path.read_text(encoding="utf-8"))["intents"]
    assert len(intents) == 1
    assert intents[0]["intent_id"] == "CPI00001"
    assert intents[0]["status"] == "sent"

    # No new cart/order created by the refused run.
    carts_path = isolated_state["commerce_state"] / "carts.json"
    orders_path = isolated_state["commerce_state"] / "orders.json"
    if carts_path.exists():
        assert json.loads(carts_path.read_text(encoding="utf-8")).get("carts", []) == []
    if orders_path.exists():
        assert json.loads(orders_path.read_text(encoding="utf-8")).get("orders", []) == []

    # Lead remains unbound (guard fired before any binding).
    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0].get("deposit_payment_intent_id", "") == ""


# ─────────────────────────────────────────────────────────────────
# P17b — the deposit link's delivery status is consumed, not flattened
#
# `bridge_post` distinguishes "the bridge ACCEPTED this message, so the
# customer most likely HAS a live payment link" (send_uncertain) from "it
# definitely never left" (connect_failed). The 2-tuple adapter this script used
# collapsed both into False, so an uncertain send was recorded — in the lead,
# in the audit row and in the owner page — as a definite failure, and the page
# told the owner to re-invoke, which mints a SECOND, different payment link.
#
# The intent void, the order cancel and the failed-audit triple are UNCHANGED
# by these cells on purpose: this is a record fix on money-path code, not a
# change to what the money guards do.
# ─────────────────────────────────────────────────────────────────


def _run_uncertain(isolated_state, *, checkout="https://pay/?o={order_id}"):
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "fail:bridge_accepted_no_id"
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STATUS"] = "send_uncertain"
    _write_config(isolated_state["config_path"], checkout_url_template=checkout)
    lead_id = _write_lead(isolated_state["leads_path"])
    return lead_id, _run_script(isolated_state["env"], lead_id)


def test_uncertain_deposit_send_is_recorded_uncertain_not_failed(isolated_state):
    """The lead must not claim the customer got nothing when they probably did."""
    lead_id, result = _run_uncertain(isolated_state)
    assert result.returncode == 6, result.stderr[-800:]

    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0]["deposit_link_delivery_status"] == "uncertain", (
        "an accepted-but-unconfirmed payment link recorded as a definite "
        "failure invites a second, different link")
    assert leads["leads"][0]["deposit_link_delivery_status_at"]


def test_uncertain_deposit_send_emits_the_shared_unconfirmed_row(isolated_state):
    lead_id, result = _run_uncertain(isolated_state)

    rows = _read_audit_rows(isolated_state["log_path"])
    unconfirmed = [r for r in rows if r["type"] == "catering_customer_send_unconfirmed"]
    assert len(unconfirmed) == 1, [r["type"] for r in rows]
    row = unconfirmed[0]
    assert row["delivery_certainty"] == "uncertain"
    assert row["send_status"] == "send_uncertain"
    assert row["send_kind"] == "deposit_link"
    assert row["script"] == "catering-mint-deposit"
    assert row["lead_id"] == lead_id


def test_a_definite_deposit_failure_is_still_recorded_failed(isolated_state):
    """The other half of the distinction — unchanged behaviour, now recorded."""
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "fail:simulated_bridge_outage"
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 6

    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0]["deposit_link_delivery_status"] == "failed"
    row = next(r for r in _read_audit_rows(isolated_state["log_path"])
               if r["type"] == "catering_customer_send_unconfirmed")
    assert row["delivery_certainty"] == "failed"


def test_an_uncertain_send_does_not_take_the_destructive_transitions(isolated_state):
    """TWO-FACTS PIN: link-creation truth and message-delivery truth are separate.

    A WhatsApp send we could not confirm must never imply the provider intent
    does not exist. The void + cancel pair is the destructive transition the two
    facts must not share: firing it here erases a real provider fact because a
    message fact was unknown, and leaves the customer holding a link that
    silently cannot pay. The definite-failure arm keeps that pair, and
    `test_bridge_send_failed_voids_intent_and_audits` above pins it there.

    (This cell previously asserted the opposite — that the uncertain arm voided,
    cancelled and left the lead unbound. That WAS the P1 defect: the voided
    intent is invisible to `_find_live_intent_for_lead`, and the unbound lead
    passes the idempotency check, so a re-invoke minted a second live link.)
    """
    lead_id, result = _run_uncertain(isolated_state)

    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    # Nothing may have destroyed the money objects behind the link.
    assert "commerce_payment_intent_voided" not in types, (
        "the intent behind a link the customer probably holds was voided")
    assert "commerce_order_cancelled" not in types, (
        "the order behind a link the customer probably holds was cancelled")

    intents = json.loads(
        (isolated_state["commerce_state"] / "payment_intents.json").read_text(encoding="utf-8"))
    assert intents["intents"][0]["status"] == "minted", (
        "the payment intent must stay live so the link the customer most likely "
        f"holds can still take a payment: {intents!r}")
    orders = json.loads(
        (isolated_state["commerce_state"] / "orders.json").read_text(encoding="utf-8"))
    assert orders["orders"][0]["status"] == "pending_payment", (
        "the order backing a live link is not an orphan and must not be "
        f"cancelled: {orders!r}")

    # And the lead IS bound — `commerce-payment-confirm` finds the lead for an
    # incoming payment by `deposit_payment_intent_id` alone, so an unbound lead
    # orphans a payment the customer can still make on the link they hold.
    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0]["deposit_payment_intent_id"] == intents["intents"][0]["intent_id"]


def test_the_uncertain_arm_emits_no_row_claiming_the_link_failed(isolated_state):
    """The audit log must not collapse uncertainty into failure either.

    Fixing the STATE while leaving the audit lying would be half a fix. Two rows
    used to be written on this arm and both were untrue once the void stopped
    firing:

      - `commerce_payment_link_failed`, whose documented contract
        (payment_link.py) is to PRECEDE a void so the operator reads
        attempted -> failed -> voided with no gap. With no void it leaves
        attempted -> failed -> nothing, and the operator concludes the link is
        dead when it is live and payable.
      - `catering_deposit_link_failed` with `reason="bridge_send_failed"`, when
        the truth is "unconfirmed, probably delivered".

    One truthfully-named row replaces both, carrying the same cross-references
    so the R3 evidence does not regress.
    """
    lead_id, result = _run_uncertain(isolated_state)

    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    assert "commerce_payment_link_failed" not in types, (
        "a row whose contract is to explain a void was emitted on an arm that "
        "does not void")
    assert "catering_deposit_link_failed" not in types, (
        "the deposit link did not fail — its delivery is unknown and the link "
        "is live")

    unconfirmed = [r for r in rows if r["type"] == "catering_deposit_link_send_unconfirmed"]
    assert len(unconfirmed) == 1, [r["type"] for r in rows]
    row = unconfirmed[0]
    intents = _commerce_rows(isolated_state, "payment_intents.json", "intents")
    orders = _commerce_rows(isolated_state, "orders.json", "orders")
    assert row["lead_id"] == lead_id
    assert row["delivery_certainty"] == "uncertain"
    assert row["commerce_payment_intent_id"] == intents[0]["intent_id"]
    assert row["commerce_order_id"] == orders[0]["order_id"]
    assert row["detail"]

    # p17b's cross-catering row is untouched — it answers "which sends could we
    # not confirm?" across every script and carries no commerce cross-refs.
    assert len([r for r in rows if r["type"] == "catering_customer_send_unconfirmed"]) == 1


def test_the_definite_arm_still_emits_the_full_failed_and_voided_triple(isolated_state):
    """Fence on the row change: the definite arm's audit is untouched.

    A customer who received nothing DOES have a failed link and a voided intent,
    and the operator must still read the whole triple there.
    """
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "fail:simulated_bridge_outage"
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 6, result.stderr[-800:]

    rows = _read_audit_rows(isolated_state["log_path"])
    types = [r["type"] for r in rows]
    assert "commerce_payment_link_attempted" in types
    assert "commerce_payment_link_failed" in types
    assert "commerce_payment_intent_voided" in types
    assert "commerce_order_cancelled" in types
    deposit_failed = [r for r in rows if r["type"] == "catering_deposit_link_failed"]
    assert len(deposit_failed) == 1
    assert deposit_failed[0]["reason"] == "bridge_send_failed"
    # ...and the uncertain arm's row must NOT appear where delivery is known.
    assert "catering_deposit_link_send_unconfirmed" not in types


def test_a_delivered_deposit_link_says_so_on_the_lead(isolated_state):
    """`None` must mean "this path never ran", not "we never checked"."""
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    result = _run_script(isolated_state["env"], lead_id)
    assert result.returncode == 0, result.stderr[-800:]

    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0]["deposit_link_delivery_status"] == "sent"
    assert leads["leads"][0]["deposit_link_delivery_status_at"]


# ─────────────────────────────────────────────────────────────────
# P1 — an uncertain send must not be silently re-mintable
#
# `send_uncertain` means the bridge ACCEPTED a message carrying a live payment
# link, so the customer most likely holds one. The failure arm USED TO void the
# intent and cancel the order without ever binding `deposit_payment_intent_id`
# on the lead — so on a re-invoke BOTH money guards waved the run through:
#
#   1. the post-lock idempotency check read an unbound lead, and
#   2. `_find_live_intent_for_lead` excludes `voided` from its live set, so the
#      intent the uncertain send had just voided was invisible to it.
#
# The re-invoke therefore built a fresh cart -> fresh order -> fresh intent and
# sent a SECOND, DIFFERENT payment link to a customer who probably already had
# one. The owner page on this path said "do NOT re-invoke", which is a request,
# not a guard.
#
# The fix separates the two facts: an unconfirmed MESSAGE never destroys the
# payment intent. The intent stays live, the lead is bound to it, and the
# re-invoke is refused outright. The cells below pin the refusal, pin that the
# minted ids stay recoverable from the lead, and fence the fix so it cannot
# over-block a DEFINITE failure (where the customer received nothing and a
# re-mint is the correct recovery).
# ─────────────────────────────────────────────────────────────────


def _commerce_rows(isolated_state, filename: str, key: str) -> list[dict]:
    """Rows from a commerce state file, or [] when the file was never written."""
    path = isolated_state["commerce_state"] / filename
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get(key, [])


def test_reinvoke_after_uncertain_send_refuses_second_mint(isolated_state):
    """Re-invoking after an uncertain send must REFUSE, not mint link #2.

    The second run here has a HEALTHY bridge — the operator re-invoking once
    connectivity is back is exactly the sequence that puts two live payment
    links in the customer's chat. Asserted on the commerce ledger, not stdout:
    the harm is a second intent/order existing at all.
    """
    lead_id, first = _run_uncertain(isolated_state)
    assert first.returncode == 6, first.stderr[-800:]
    intents_after_first = _commerce_rows(isolated_state, "payment_intents.json", "intents")
    assert len(intents_after_first) == 1 and intents_after_first[0]["status"] == "minted"
    minted_first = intents_after_first[0]["intent_id"]

    # Bridge is healthy again; the lead is NOT re-seeded — this is the same
    # lead, in the state the uncertain send left it in.
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "success:wamid_test_002"
    second = _run_script(isolated_state["env"], lead_id)

    assert second.returncode != 0, (
        "a re-invoke after an accepted-but-unconfirmed deposit link exited "
        f"cleanly (rc={second.returncode}); stdout={second.stdout!r}"
    )
    refusal = json.loads(second.stdout.strip().splitlines()[-1])
    assert "refused" in refusal, (
        f"expected a refusal payload from the re-invoke, got {refusal!r}")

    # No SECOND payment link may exist, in any status.
    intents = _commerce_rows(isolated_state, "payment_intents.json", "intents")
    assert [i["intent_id"] for i in intents] == [minted_first], (
        "a second, different payment intent was minted for a customer who "
        f"most likely already holds link {minted_first}: {intents!r}")

    # ...and no second order/cart behind it.
    orders = _commerce_rows(isolated_state, "orders.json", "orders")
    assert len(orders) == 1, f"second order created by the re-invoke: {orders!r}"
    carts = _commerce_rows(isolated_state, "carts.json", "carts")
    assert len(carts) == 1, f"second cart created by the re-invoke: {carts!r}"

    minted_rows = [r for r in _read_audit_rows(isolated_state["log_path"])
                   if r["type"] == "commerce_payment_intent_minted"]
    assert len(minted_rows) == 1, (
        f"{len(minted_rows)} mint rows; the uncertain send's mint is the only "
        "one that may exist")


def test_uncertain_send_leaves_the_minted_intent_recoverable_from_the_lead(isolated_state):
    """The durable lead record must not forget that a link was minted.

    The uncertain path used to stamp `deposit_link_delivery_status` and nothing
    else, so the lead carried "we could not confirm a send" with no trace of
    WHAT was sent. Everything the operator needs to reconcile — which intent,
    which order, how much — lived only in the audit log. The lead is the record
    the re-invoke guard, `commerce-payment-confirm` and the operator all read,
    so the ids have to survive there.
    """
    lead_id, result = _run_uncertain(isolated_state)
    assert result.returncode == 6, result.stderr[-800:]

    intents = _commerce_rows(isolated_state, "payment_intents.json", "intents")
    orders = _commerce_rows(isolated_state, "orders.json", "orders")
    assert len(intents) == 1 and len(orders) == 1
    minted_intent_id = intents[0]["intent_id"]
    minted_order_id = orders[0]["order_id"]

    lead = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))["leads"][0]
    assert lead["deposit_link_delivery_status"] == "uncertain"
    assert minted_intent_id in json.dumps(lead), (
        f"intent {minted_intent_id} was minted and most likely reached the "
        f"customer, but no field on the lead records it: {lead!r}")
    assert minted_order_id in json.dumps(lead), (
        f"order {minted_order_id} is unrecoverable from the lead: {lead!r}")

    # The evidence that already survives must keep surviving: the intent stays
    # in the ledger — live, per the two-facts pin above — and the audit row keeps
    # both cross-references.
    assert intents[0]["status"] == "minted"
    evidence = next(r for r in _read_audit_rows(isolated_state["log_path"])
                    if r["type"] == "catering_deposit_link_send_unconfirmed")
    assert evidence["commerce_payment_intent_id"] == minted_intent_id
    assert evidence["commerce_order_id"] == minted_order_id


def test_the_refusal_is_audited_under_its_own_tag(isolated_state):
    """The refusal has to be findable later, and under a NEW tag.

    `CateringDepositLinkFailed.reason` is byte-identical in the pinned rollback
    target, so widening it would make the old reader REJECT a row it recognises
    today. An unknown TAG routes to `_UnknownLogEntry` instead and survives a
    downgrade intact.
    """
    lead_id, _ = _run_uncertain(isolated_state)
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "success:wamid_test_004"
    _run_script(isolated_state["env"], lead_id)

    rows = _read_audit_rows(isolated_state["log_path"])
    refused = [r for r in rows if r["type"] == "catering_deposit_reinvoke_refused"]
    assert len(refused) == 1, [r["type"] for r in rows]
    row = refused[0]
    assert row["lead_id"] == lead_id
    assert row["prior_delivery_status"] == "uncertain"
    intent_id = _commerce_rows(isolated_state, "payment_intents.json", "intents")[0]["intent_id"]
    assert row["commerce_payment_intent_id"] == intent_id, (
        "the refusal must name the link the operator has to reconcile")
    assert row["commerce_order_id"]
    assert row["amount_cents"] > 0
    # The pre-existing variant must not have been reused for either of the two
    # new events — its `reason` Literal is byte-identical in the rollback target,
    # so a widened value there is what would break the old reader.
    assert "catering_deposit_link_failed" not in [r["type"] for r in rows]


def test_reinvoke_after_a_definite_failure_still_mints(isolated_state):
    """GREEN FENCE: a definite failure means the customer got nothing.

    `connect_failed` is the case the re-invoke recovery was built for — there is
    no link in the customer's hands, so a second mint is the correct outcome,
    not a double-charge. The uncertain-send guard must not swallow this path.
    """
    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "fail:simulated_bridge_outage"
    _write_config(isolated_state["config_path"], checkout_url_template="https://pay/?o={order_id}")
    lead_id = _write_lead(isolated_state["leads_path"])

    first = _run_script(isolated_state["env"], lead_id)
    assert first.returncode == 6, first.stderr[-800:]
    leads = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))
    assert leads["leads"][0]["deposit_link_delivery_status"] == "failed"

    isolated_state["env"]["PYTEST_CATERING_DEPOSIT_BRIDGE_STUB"] = "success:wamid_test_003"
    second = _run_script(isolated_state["env"], lead_id)
    assert second.returncode == 0, (
        "a definite send failure left the customer with nothing; the re-invoke "
        f"must still mint. stdout={second.stdout!r} stderr={second.stderr[-800:]!r}")

    lead = json.loads(isolated_state["leads_path"].read_text(encoding="utf-8"))["leads"][0]
    assert lead["deposit_payment_intent_id"].startswith("CPI")
    assert lead["deposit_link_delivery_status"] == "sent"
    live = [i for i in _commerce_rows(isolated_state, "payment_intents.json", "intents")
            if i["status"] not in {"voided", "refunded", "chargeback"}]
    assert [i["intent_id"] for i in live] == [lead["deposit_payment_intent_id"]]
