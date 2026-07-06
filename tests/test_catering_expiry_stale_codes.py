"""T9 — expiry / stale-code edge cases on apply-catering-owner-decision +
create-catering-lead (cases A-018, B-016, B-017).

Test plan: tasks/catering-agent-comprehensive-test-plan.md commit #3 of 4.
Plan doc:  tasks/t9-expiry-tests-plan.md (Hermes-native, approved 2026-05-06).

Cases:
  A-018 — Inquiry while customer's PRIOR lead is still AWAITING_OWNER_APPROVAL
          → 2nd lead allowed (different message_id mints a new lead with new
          approval_code). Pins current deployed behavior: idempotency in
          create-catering-lead is per original_message_id, NOT per
          customer_phone, so concurrent leads from the same customer are
          allowed and the dispatcher's #XXXXX disambiguation handles which
          one an owner-reply applies to.

  B-017 — Owner approves with stale code that doesn't match any active lead
          → EXIT_NOT_FOUND (4) with helpful stderr.
          - B-017a: code is completely absent from the leads file.
          - B-017b: code exists on a lead in terminal status STALE
                    → EXIT_NOT_FOUND with a clarifying stderr line naming
                    the lead's status, so the agent can echo *why* the
                    code stopped working.

  B-016 — Test plan describes "approves AFTER 4h proposal expiry," but no
          `expires_at` field or 4h TTL is enforced on catering leads in the
          deployed code. The closest mechanism IS the STALE terminal-status
          transition (operator-driven via catering-lead-reconcile, future
          time-based reconciler). B-016 therefore collapses into the
          B-017b STALE case. A dedicated B-016 test should be added if a
          true `expires_at` field lands later.

Out of scope (rejected for over-engineering at plan time, see
tasks/t9-expiry-tests-plan.md §"Scope boundary"):
  - Idempotent-replay control (already at test_catering_v02_scripts.py:243)
  - parametrize over [STALE, CLOSED, OWNER_REJECTED] (same code path)
  - approving STALE → AWAITING transition (STALE has empty transition set)
"""
from __future__ import annotations

import platform
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import HTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _b1_helpers import (  # noqa: E402
    BridgeStub, make_env_dir, mk_lead, read_leads, run_apply, run_create,
    seed_leads,
)

# Mirror /opt/shift-agent/exit_codes.py so the test pins the contract.
EXIT_NOT_FOUND = 4


@pytest.fixture
def env_dir(tmp_path):
    return make_env_dir(tmp_path)


@pytest.fixture
def bridge_server():
    BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), BridgeStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, BridgeStub
    finally:
        server.shutdown()


# ─────────────────────────────────────────────────────────────────
# A-018 — concurrent leads from same customer
# ─────────────────────────────────────────────────────────────────


def test_a018_second_inquiry_while_prior_awaiting_creates_new_lead(
    env_dir, bridge_server,
):
    """A-018 — second inquiry from same customer (different message_id) while
    the prior lead is still AWAITING_OWNER_APPROVAL mints a NEW lead with a
    fresh lead_id and approval_code; the original lead is untouched.

    Idempotency in create-catering-lead is keyed on original_message_id (not
    customer_phone), so the apply path can have multiple active leads
    sharing one phone; the dispatcher's #XXXXX disambiguation handles
    which lead an owner-reply applies to.
    """
    port, _ = bridge_server
    fields1 = {"headcount": 50, "event_date": (date.today() + timedelta(days=30)).isoformat(),
               "dietary_restrictions": ["vegetarian"]}
    r1 = run_create(
        env_dir, port, fields1,
        customer_phone="+15551234567", customer_name="Alex",
        message_id="MSG_A018_FIRST",
    )
    assert r1.returncode == 0, f"first create failed: {r1.stderr}"

    leads_after_first = read_leads(env_dir)["leads"]
    assert len(leads_after_first) == 1
    first_lead = leads_after_first[0]
    assert first_lead["status"] == "AWAITING_OWNER_APPROVAL"
    first_code = first_lead["owner_approval_code"]
    first_id = first_lead["lead_id"]

    # Second inquiry, same phone, DIFFERENT message_id → not an idempotent replay.
    fields2 = {"headcount": 75, "event_date": (date.today() + timedelta(days=31)).isoformat(),
               "dietary_restrictions": ["vegetarian"]}
    r2 = run_create(
        env_dir, port, fields2,
        customer_phone="+15551234567", customer_name="Alex",
        message_id="MSG_A018_SECOND",
    )
    assert r2.returncode == 0, f"second create failed: {r2.stderr}"
    # Pin the new-lead branch (not the idempotent-replay branch). Both
    # return rc=0; only the replay branch prints "idempotent_replay".
    assert "idempotent_replay" not in r2.stdout, (
        f"second create unexpectedly took replay branch: {r2.stdout}"
    )

    leads_after_second = read_leads(env_dir)["leads"]
    assert len(leads_after_second) == 2
    # Look up by message_id rather than positional index — survives any
    # future reordering of store.leads (e.g. sort-by-updated_at).
    first_after = next(
        l for l in leads_after_second
        if l["original_message_id"] == "MSG_A018_FIRST"
    )
    second_lead = next(
        l for l in leads_after_second
        if l["original_message_id"] == "MSG_A018_SECOND"
    )
    assert second_lead["lead_id"] != first_id
    assert second_lead["owner_approval_code"] != first_code
    assert second_lead["status"] == "AWAITING_OWNER_APPROVAL"
    # First lead untouched: same status, same approval code.
    assert first_after["status"] == "AWAITING_OWNER_APPROVAL"
    assert first_after["owner_approval_code"] == first_code


# ─────────────────────────────────────────────────────────────────
# B-017a — code completely absent
# ─────────────────────────────────────────────────────────────────


def test_b017a_apply_with_unknown_code_returns_not_found(env_dir, bridge_server):
    """B-017a — owner replies with a code that's not present in the leads
    file at all (typo, days-old code, screenshot from another customer).
    Result: EXIT_NOT_FOUND (4) + stderr explaining the code was not
    recognized. Leads file untouched.
    """
    port, _ = bridge_server
    # Seed an unrelated lead so the leads file exists and is non-empty —
    # confirms the not-found path triggers on no-match, not on empty store.
    # mk_lead's owner_approval_code defaults to None, so #GHST5 cannot
    # collide with the seeded lead.
    seed_leads(env_dir, [mk_lead(
        lead_id="L0001", phone="+15550000111",
        created_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )])

    # Use a code from the deployed alphabet ([A-HJKMNPQR-Z2-9]) — matches
    # what create-catering-lead would mint. Apply doesn't enforce the
    # alphabet at arg-time (only the format `#XXXXX` length), but using
    # a real-shape code keeps the test honest about what production
    # behavior it pins.
    r = run_apply(env_dir, port, code="#GHST5", decision="reject")
    assert r.returncode == EXIT_NOT_FOUND, (
        f"expected EXIT_NOT_FOUND (4), got {r.returncode}\nstderr: {r.stderr}"
    )
    assert "no recoverable lead with code #GHST5" in r.stderr, (
        f"expected helpful stderr, got: {r.stderr}"
    )

    leads = read_leads(env_dir)["leads"]
    assert len(leads) == 1
    assert leads[0]["lead_id"] == "L0001"


# ─────────────────────────────────────────────────────────────────
# B-017b / B-016 — stale code on terminal-status (STALE) lead
# ─────────────────────────────────────────────────────────────────


def test_b017b_apply_with_stale_lead_clarifies_status(env_dir, bridge_server):
    """B-017b / B-016 — owner replies #XXXXX for a code whose lead is in
    terminal status STALE. EXIT_NOT_FOUND (4) with two stderr lines:
    "no recoverable lead with code …" plus a clarifying line naming the
    lead's status, so the agent can echo *why* the code is dead back to
    the owner.

    CLOSED and OWNER_REJECTED hit the same matches-filter exclusion in
    apply-catering-owner-decision and are not separately parametrized
    (same code path, no additional coverage gain).

    B-016 mapping: the test plan describes this as "approves AFTER 4h
    proposal expiry," but the deployed expiry mechanism IS the STALE
    transition (no `expires_at` field, no 4h TTL). Test pinned to
    deployed reality.
    """
    port, _ = bridge_server
    # Valid code per the script's alphabet ([A-HJKMNPQR-Z2-9]).
    code = "#STARE"
    base_lead = mk_lead(
        lead_id="L0001", phone="+15551234567", status="STALE",
        created_at=datetime.now(tz=timezone.utc) - timedelta(hours=24),
    )
    base_lead["owner_approval_code"] = code
    seed_leads(env_dir, [base_lead])

    r = run_apply(env_dir, port, code=code, decision="reject")
    assert r.returncode == EXIT_NOT_FOUND, (
        f"expected EXIT_NOT_FOUND (4), got {r.returncode}\nstderr: {r.stderr}"
    )
    assert f"no recoverable lead with code {code}" in r.stderr
    assert "in status STALE" in r.stderr, (
        f"expected status STALE in stderr, got: {r.stderr}"
    )
    assert "L0001" in r.stderr

    # Lead must NOT have transitioned out of STALE (terminal).
    leads = read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "STALE"
