"""`check_quota` — the read-only probe the router runs BEFORE creating a project.

Its whole reason to exist is that `reserve_quota` could only answer "are you
over your limit?" by writing a reservation, so the router had to create the
project row first and only then discover the request was blocked. The row was
never cleaned up and it silently disabled catering routing for that sender.

Two properties carry that weight and are pinned here against the REAL account
module rather than a stub:

  * it writes nothing — same customers.json bytes before and after, so calling
    it on the hot path cannot consume, roll or reorder anything;
  * it answers exactly what `reserve_quota` would have answered, with the same
    reply text, so moving the check earlier is invisible to the customer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from agents.flyer.account import check_quota, reserve_quota  # noqa: E402
from schemas import FlyerCustomerStore, FlyerUsageEvent  # noqa: E402

NOW = datetime(2026, 5, 15, tzinfo=timezone.utc)
REQUESTER = "+19045550104"


def _store_path(tmp_path: Path, *, used: int, status: str = "trial",
                plan_id: str = "trial", now: datetime = NOW) -> Path:
    """A customer with `used` flyers already consumed in the current period.

    `now` is a parameter because `_roll_period` advances the billing period to
    wall-clock time: usage recorded in a period that has since rolled counts
    zero, so the CLI cell (which cannot inject a clock) has to build its events
    at real `now` to describe an exhausted customer.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path = tmp_path / "customers.json"
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Triveni",
        business_address="300 S Polk St",
        public_phone="+15550100003",
        business_whatsapp_number="+15550100003",
        authorized_request_number=REQUESTER,
        business_category="restaurant",
        preferred_language="en",
        plan_id=plan_id,
        now=now,
    ).model_copy(update={
        "status": status,
        "current_period_start": now,
        "current_period_end": now + timedelta(days=31),
        "usage_events": [
            FlyerUsageEvent(
                reservation_id=f"CUST0001:F{i:04d}", project_id=f"F{i:04d}",
                customer_id="CUST0001", kind="used", recorded_at=now,
                message_id=f"m{i}",
            )
            for i in range(1, used + 1)
        ],
    })
    store.customers.append(customer)
    state_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    return state_path


def test_check_quota_writes_nothing_when_it_blocks(tmp_path):
    """The property that makes it safe to call ahead of project creation."""
    state_path = _store_path(tmp_path, used=3)
    before = state_path.read_bytes()

    result = check_quota(state_path=state_path, customer_phone=REQUESTER, now=NOW)

    assert result.quota_allowed is False
    assert state_path.read_bytes() == before, "check_quota mutated customers.json"


def test_check_quota_writes_nothing_when_it_allows(tmp_path):
    state_path = _store_path(tmp_path, used=0)
    before = state_path.read_bytes()

    result = check_quota(state_path=state_path, customer_phone=REQUESTER, now=NOW)

    assert result.ok is True and result.quota_allowed is True
    assert state_path.read_bytes() == before, "check_quota mutated customers.json"


def test_check_quota_consumes_nothing_so_the_reservation_still_succeeds(tmp_path):
    """Pre-checking must not cost the customer the flyer they are allowed."""
    state_path = _store_path(tmp_path, used=0)

    assert check_quota(state_path=state_path, customer_phone=REQUESTER,
                       now=NOW).quota_allowed is True
    reserved = reserve_quota(state_path=state_path, customer_phone=REQUESTER,
                             project_id="F0009", message_id="m9", now=NOW)

    assert reserved.ok is True and reserved.quota_allowed is True
    final = FlyerCustomerStore.model_validate_json(
        state_path.read_text(encoding="utf-8")).customers[0]
    assert final.usage_count_for_current_period() == 1, (
        "exactly one unit consumed — the precheck must not have taken one too")


def test_the_blocked_reply_is_identical_to_what_reserve_would_have_sent(tmp_path):
    """The customer must not be able to tell the check moved earlier."""
    checked = check_quota(state_path=_store_path(tmp_path / "a", used=3),
                          customer_phone=REQUESTER, now=NOW)
    reserved = reserve_quota(state_path=_store_path(tmp_path / "b", used=3),
                             customer_phone=REQUESTER, project_id="F0009",
                             message_id="m9", now=NOW)

    assert reserved.quota_allowed is False
    assert checked.quota_allowed is False
    assert checked.reply_text == reserved.reply_text
    assert checked.reply_text != "", "a blocked customer must still be told why"


def test_the_cli_exposes_check_quota_and_still_writes_nothing(tmp_path):
    """cf-router reaches this through a subprocess, so the CLI mode is the real
    seam — an account-module-only test would leave the wiring unproven."""
    state_path = _store_path(tmp_path, used=3, now=datetime.now(timezone.utc))
    before = state_path.read_bytes()

    proc = subprocess.run(
        [sys.executable,
         str(Path(__file__).resolve().parent.parent
             / "src" / "agents" / "flyer" / "scripts" / "manage-flyer-account"),
         "--check-quota",
         "--customer-phone", REQUESTER,
         "--state-path", str(state_path),
         # Absent on purpose: the CLI falls back to FlyerPlanTier.default_tiers()
         # so this cell asserts the same trial limit the in-process cells do,
         # instead of whatever plan config happens to sit in the tree.
         "--config-path", str(tmp_path / "no-such-config.yaml"),
         "--audit-log-path", str(tmp_path / "decisions.log")],
        capture_output=True, text=True, timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["quota_allowed"] is False
    assert doc["reply_text"], "the CLI must return the customer-visible reply"
    assert state_path.read_bytes() == before, "the CLI mode mutated customers.json"


def test_an_unknown_customer_is_reported_as_not_found_not_as_blocked(tmp_path):
    """The router treats only a DEFINITE block as an early-out.

    `ok is False` here is what makes an unknown or inactive customer fall
    through to the unchanged path instead of being refused by the new check.
    """
    result = check_quota(state_path=_store_path(tmp_path, used=0),
                         customer_phone="+19999999999", now=NOW)

    assert result.ok is False
    assert result.quota_allowed is False
    assert result.detail == "active_customer_not_found"
