"""M5 — the three follow-up CLIs plus the two trigger sites.

Windows-runnable: the scripts are loaded in-process via fixtures_fleet.load_script
with the fcntl stub installed (same harness as tests/test_catering_lead_hold.py),
so the whole owner-supervised loop is exercised on any platform rather than being
skipped off Linux.

The loop under test: sweep cards a due follow-up to the OWNER -> the owner replies
with the code -> approve sends the stored text to the CUSTOMER. Nothing reaches a
customer without that middle step.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from fixtures_fleet import ensure_fcntl_stub, load_script, write_config

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "src" / "agents" / "catering" / "scripts"
TEMPLATES = REPO / "src" / "agents" / "catering" / "templates"
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

SWEEP = SCRIPTS / "catering-followup-sweep"
APPROVE = SCRIPTS / "approve-catering-followup"
STATUS = SCRIPTS / "catering-followup-status"
CREATE = SCRIPTS / "create-catering-followup"
AMEND = SCRIPTS / "amend-catering-lead"

OWNER_JID = "19045550100@s.whatsapp.net"
CUSTOMER_JID = "15550100777@s.whatsapp.net"

# Every scripted run below is pinned to this instant. The engine reads the
# CUSTOMER's local clock, and quiet hours are a gate this suite has to exercise
# deliberately rather than inherit from whenever CI happens to run — before the
# clock was pinned, the whole approve suite would have failed between 21:00 and
# 09:00 New York (the fixture config's timezone) because every send deferred.
# 14:00 is comfortably outside the 21:00->09:00 default window.
DAYTIME = datetime(2026, 7, 31, 14, 0, tzinfo=ZoneInfo("America/New_York"))


def _at(hour: int, minute: int = 0) -> datetime:
    """The pinned day at a customer-local hour."""
    return DAYTIME.replace(hour=hour, minute=minute)

# Catering scripts that alias the send chokepoint WITHOUT an allowlist entry, and
# are therefore refused at runtime with missing_action_context.
#
# EMPTY, and it must stay that way. It briefly held amend-catering-lead (M1's
# slot-filling loop) and catering-mint-deposit (the slice-2 deposit send), both
# surfaced by the scan below and both since allowlisted. The set is kept as a
# pinned expectation rather than deleted so the guard reports a CHANGE in either
# direction: a new name means a script ships refusing every send it makes, and a
# name disappearing means a gap was closed and this set should shrink.
KNOWN_UNALLOWLISTED_CATERING_SENDERS: set[str] = set()

# EVERY catering script that reaches the send chokepoint through an alias. Each
# needs a SAFE_IO_NULL_CONTEXT_ALLOWLIST entry, and each gets a direct policy
# assertion below — the AST static gate cannot see any of these callsites, so
# this list plus the scan that keeps it honest is the whole guard.
#
# The last four are the ones this work touched (the two M5 scripts, plus the two
# pre-existing gaps it surfaced); the first six were already allowlisted and are
# asserted here so a future allowlist edit cannot quietly drop one.
ALIASED_CATERING_SENDERS = [
    "send-catering-ack",
    "apply-catering-owner-decision",
    "create-catering-lead",
    "create-catering-proposal-options",
    "finalize-catering-menu",
    "select-catering-proposal",
    "catering-followup-sweep",
    "approve-catering-followup",
    "amend-catering-lead",
    "catering-mint-deposit",
]


def _lead(**over) -> dict:
    lead = {
        "lead_id": "L0001", "status": "SENT_TO_CUSTOMER",
        "customer_phone": "+15550100777", "customer_name": "Asha",
        "raw_inquiry": "catering for 40", "original_message_id": "m1",
        "created_at": "2026-07-01T10:00:00+00:00",
        "updated_at": "2026-07-01T10:00:00+00:00",
        "quote_text": "a quote",
        "extracted": {"headcount": 40, "event_date": "2026-09-01"},
    }
    lead.update(over)
    return lead


def _followup(**over) -> dict:
    f = {
        "followup_id": "FU0001", "lead_id": "L0001",
        "followup_type": "proposal_unanswered",
        "due_at": "2026-07-30T14:00:00+00:00",
        "created_at": "2026-07-28T14:00:00+00:00",
        "created_by": "system", "status": "scheduled",
        "message_template_key": "catering_followup_proposal_unanswered",
    }
    f.update(over)
    return f


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config + leads + follow-ups + audit log. The scripts read every
    path from the environment at import time, so load_script must run AFTER this."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir()
    logs.mkdir()
    leads = state / "catering-leads.json"
    followups = state / "catering-followups.json"
    log = logs / "decisions.log"
    cfg_path = write_config(tmp_path, catering_followup={"enabled": True})

    leads.write_text(json.dumps({"schema_version": 1, "leads": [_lead()]}), encoding="utf-8")
    followups.write_text(json.dumps({
        "schema_version": 1, "next_sequence": 2, "followups": [_followup()],
    }), encoding="utf-8")

    for key, value in {
        "SHIFT_AGENT_CONFIG_PATH": str(cfg_path),
        "SHIFT_AGENT_LEADS_PATH": str(leads),
        "SHIFT_AGENT_LEADS_LOCK": str(leads) + ".lock",
        "SHIFT_AGENT_FOLLOWUPS_PATH": str(followups),
        "SHIFT_AGENT_LOG_PATH": str(log),
        "SHIFT_AGENT_DECISIONS_LOG_PATH": str(log),
        "SHIFT_AGENT_STATE_DIR": str(state),
        "SHIFT_AGENT_DISABLED_FLAG": str(state / "disabled.flag"),
        "SHIFT_AGENT_TEMPLATE_DIR": str(TEMPLATES),
        "CATERING_FOLLOWUP_ENABLED": "1",
        "CATERING_FOLLOWUP_ALLOWLIST": "*",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("CATERING_FOLLOWUP_AUTOSEND", "CATERING_AUTOMATION_CONTROL_ENABLED"):
        monkeypatch.delenv(key, raising=False)

    return {"leads": leads, "followups": followups, "log": log, "state": state,
            "tmp": tmp_path}


def _write_followups(env, *followups):
    env["followups"].write_text(json.dumps({
        "schema_version": 1, "next_sequence": len(followups) + 1,
        "followups": list(followups),
    }), encoding="utf-8")


def _write_leads(env, *leads):
    env["leads"].write_text(json.dumps(
        {"schema_version": 1, "leads": list(leads)}), encoding="utf-8")


def _store(env) -> list[dict]:
    return json.loads(env["followups"].read_text(encoding="utf-8"))["followups"]


def _rows(env) -> list[dict]:
    if not env["log"].exists():
        return []
    return [json.loads(l) for l in env["log"].read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _types(env) -> list[str]:
    return [r["type"] for r in _rows(env)]


def _run(env, monkeypatch, script: Path, name: str, *argv, bridge_ok=True,
         now: datetime = DAYTIME, post=None, notify=None):
    """Load a script fresh, pin its clock, stub its bridge, run main().

    Returns (rc, sends). `post` replaces the bridge stub outright (the
    concurrency tests re-enter the script from inside the send); `notify`
    replaces the owner-alert helper.
    """
    mod = load_script(name, script)
    sends: list = []

    def _fake_post(jid, message):
        sends.append((jid, message))
        return (True, "wamid.OK") if bridge_ok else (False, "connect_failed")

    monkeypatch.setattr(mod, "_bridge_post", post or _fake_post)
    monkeypatch.setattr(mod, "customer_now", lambda tz: now)
    if notify is not None:
        monkeypatch.setattr(mod.safe_io, "notify_owner_with_fallback", notify)
    old = sys.argv
    sys.argv = [script.name, *argv]
    try:
        return mod.main(), sends
    finally:
        sys.argv = old


# ── sweep: arming ────────────────────────────────────────────────────────────
class TestSweepGates:
    def test_flag_off_is_inert(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_FOLLOWUP_ENABLED", "0")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_off")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "scheduled"
        assert _types(env) == []

    def test_empty_allowlist_is_inert(self, env, monkeypatch):
        """Fail-closed: an empty allowlist disables rather than admitting all."""
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_noallow")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "scheduled"

    def test_scoped_allowlist_admits_the_named_number(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "+15550100777")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_scoped")
        assert rc == 0 and len(sends) == 1

    def test_a_lead_outside_the_allowlist_stays_scheduled(self, env, monkeypatch):
        """Not suppressed — graduating the allowlist later must pick it up rather
        than find a graveyard."""
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "+19998887777")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_other")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "scheduled"
        assert "catering_followup_suppressed" not in _types(env)

    def test_dry_run_mutates_nothing_even_when_armed(self, env, monkeypatch):
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_dry", "--dry-run")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "scheduled"


# ── sweep: carding ───────────────────────────────────────────────────────────
class TestSweepCards:
    def test_due_followup_becomes_an_owner_card(self, env, monkeypatch):
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_card")
        assert rc == 0
        assert len(sends) == 1
        jid, message = sends[0]
        assert jid == OWNER_JID, "the CARD goes to the owner, never the customer"
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert record["approval_code"].startswith("#") and len(record["approval_code"]) == 6
        assert record["attempt_count"] == 1
        assert record["last_attempt_at"]
        assert record["approval_code"] in message
        assert record["rendered_message"] in message
        assert "Asha" in record["rendered_message"]

        row = [r for r in _rows(env) if r["type"] == "catering_followup_card_sent"][-1]
        assert row["followup_id"] == "FU0001"
        assert row["approval_code"] == record["approval_code"]
        assert row["attempt"] == 1

    def test_a_followup_not_yet_due_is_left_alone(self, env, monkeypatch):
        _write_followups(env, _followup(due_at="2099-01-01T00:00:00+00:00"))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_notdue")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "scheduled"

    def test_rerun_does_not_recard_an_open_card(self, env, monkeypatch):
        _run(env, monkeypatch, SWEEP, "sweep_c1")
        code = _store(env)[0]["approval_code"]
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_c2")
        assert rc == 0 and sends == []
        assert _store(env)[0]["approval_code"] == code
        assert _store(env)[0]["attempt_count"] == 1

    def test_minted_codes_avoid_a_live_lead_code(self, env, monkeypatch):
        """Cross-pool uniqueness: the sweep must not hand the owner a code that
        already means something else."""
        taken = "#ABCDE"
        _write_leads(env, _lead(owner_approval_code=taken,
                                status="AWAITING_OWNER_APPROVAL"))
        for _ in range(6):
            _write_followups(env, _followup())
            _run(env, monkeypatch, SWEEP, "sweep_uniq")
            assert _store(env)[0]["approval_code"] != taken


class TestSweepCardExpiry:
    def _carded(self, hours_ago: int, attempt: int) -> dict:
        stamped = DAYTIME - timedelta(hours=hours_ago)
        return _followup(status="awaiting_owner_approval", approval_code="#AAAAA",
                         rendered_message="hi", attempt_count=attempt,
                         last_attempt_at=stamped.isoformat())

    def test_a_lapsed_card_returns_to_scheduled_and_is_recarded(self, env, monkeypatch):
        _write_followups(env, self._carded(hours_ago=5, attempt=1))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_recard")
        assert rc == 0
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert record["attempt_count"] == 2
        assert record["approval_code"] != "#AAAAA", "a re-card mints a fresh code"
        assert len(sends) == 1

    def test_a_fresh_card_is_left_alone(self, env, monkeypatch):
        _write_followups(env, self._carded(hours_ago=1, attempt=1))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_fresh")
        assert rc == 0 and sends == []
        assert _store(env)[0]["approval_code"] == "#AAAAA"

    def test_the_second_lapse_expires_the_followup(self, env, monkeypatch):
        """An owner who let two cards lapse has answered."""
        _write_followups(env, self._carded(hours_ago=5, attempt=2))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_expire")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "expired"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_expired"][-1]
        assert row["followup_id"] == "FU0001" and row["attempts"] == 2


class TestSweepSuppression:
    def test_kill_switch_suppresses_and_audits(self, env, monkeypatch):
        (env["state"] / "disabled.flag").write_text("", encoding="utf-8")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_kill")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "suppressed"
        assert _store(env)[0]["suppressed_reason"] == "kill_switch"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_suppressed"][-1]
        assert row["reason"] == "kill_switch"

    def test_a_held_lead_suppresses(self, env, monkeypatch):
        _write_leads(env, _lead(on_hold=True, hold_reason="customer asked us to wait"))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_hold")
        assert rc == 0 and sends == []
        assert _store(env)[0]["suppressed_reason"] == "lead_on_hold"

    def test_a_missing_lead_suppresses_rather_than_going_cold(self, env, monkeypatch):
        _write_leads(env)
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_orphan")
        assert rc == 0 and sends == []
        assert _store(env)[0]["suppressed_reason"] == "lead_missing"

    def test_an_opted_out_conversation_suppresses(self, env, monkeypatch):
        control = env["state"] / "catering-automation-control.json"
        control.write_text(json.dumps({"schema_version": 1, "conversations": {}}),
                           encoding="utf-8")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(control))
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        import automation_control
        automation_control.set_mode(CUSTOMER_JID, "opted_out",
                                    actor="customer", reason="stop")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_optout")
        assert rc == 0 and sends == []
        assert _store(env)[0]["suppressed_reason"] == "automation_suppressed"

    def test_a_suppressed_followup_is_never_retried(self, env, monkeypatch):
        _write_leads(env, _lead(on_hold=True))
        _run(env, monkeypatch, SWEEP, "sweep_s1")
        assert _store(env)[0]["status"] == "suppressed"
        _write_leads(env, _lead())  # hold lifted
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_s2")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "suppressed"


class TestSweepQuietHours:
    """Quiet hours used to be TERMINAL. With whole-day +24h/+48h offsets that made
    21:00-09:00 a deterministic 12-hours-a-day kill zone: anything whose due
    moment landed there died, and the config comment promised the opposite."""

    def test_an_owner_card_is_sent_inside_quiet_hours(self, env, monkeypatch):
        """The card goes to the OWNER, who reads on their own schedule."""
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_quiet_card", now=_at(22, 30))
        assert rc == 0 and len(sends) == 1 and sends[0][0] == OWNER_JID
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert record["suppressed_reason"] is None
        assert "catering_followup_suppressed" not in _types(env)

    @pytest.mark.parametrize("hour,minute,quiet", [
        (20, 59, False),  # last minute before the window
        (21, 0, True),    # the window opens ON quiet_hours_start
        (8, 59, True),    # last minute inside the window
        (9, 0, False),    # the window closes ON quiet_hours_end
    ])
    def test_autosend_defers_inside_the_window_and_sends_outside_it(
            self, env, monkeypatch, hour, minute, quiet):
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        rc, sends = _run(env, monkeypatch, SWEEP, f"sweep_quiet_{hour}_{minute}",
                         now=_at(hour, minute))
        assert rc == 0
        record = _store(env)[0]
        if not quiet:
            assert len(sends) == 1 and sends[0][0] == CUSTOMER_JID
            assert record["status"] == "approved_sent"
            return
        assert sends == []
        assert record["status"] == "scheduled", "deferred, never terminal"
        assert record["suppressed_reason"] is None
        assert "catering_followup_suppressed" not in _types(env)
        row = [r for r in _rows(env) if r["type"] == "catering_followup_deferred"][-1]
        assert row["reason"] == "quiet_hours"
        assert record["due_at"] == row["to_due_at"]
        assert datetime.fromisoformat(record["due_at"]).hour == 9, (
            "the record moves ONTO the boundary where the window reopens"
        )

    def test_a_deferred_followup_cards_when_the_window_reopens(self, env, monkeypatch):
        """The whole point: deferred at 23:00, alive at 09:00 the next morning."""
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        _run(env, monkeypatch, SWEEP, "sweep_defer_a", now=_at(23, 0))
        assert _store(env)[0]["status"] == "scheduled"
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_defer_b",
                         now=_at(9, 30) + timedelta(days=1))
        assert rc == 0 and len(sends) == 1 and sends[0][0] == CUSTOMER_JID
        assert _store(env)[0]["status"] == "approved_sent"

    def test_deferral_does_not_multiply_the_record(self, env, monkeypatch):
        """Same record, moved. A defer implemented as suppress-then-reschedule
        would either duplicate the nudge or be swallowed by the dedup."""
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        _run(env, monkeypatch, SWEEP, "sweep_defer_1", now=_at(22, 0))
        _run(env, monkeypatch, SWEEP, "sweep_defer_2", now=_at(23, 0))
        assert len(_store(env)) == 1
        assert _store(env)[0]["followup_id"] == "FU0001"


class TestSweepAutosend:
    def test_autosend_off_by_default_even_when_armed(self, env, monkeypatch):
        _, sends = _run(env, monkeypatch, SWEEP, "sweep_noauto")
        assert sends[0][0] == OWNER_JID
        assert "catering_followup_sent" not in _types(env)

    def test_autosend_flag_sends_the_customer_directly(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_auto")
        assert rc == 0
        jid, message = sends[0]
        assert jid == CUSTOMER_JID
        assert message.startswith("⚕ *Catering Agent*"), "customer sends carry the prefix"
        record = _store(env)[0]
        assert record["status"] == "approved_sent"
        assert record["sent_message_id"] == "wamid.OK"
        assert record["claimed_at"] is None
        assert [r["type"] for r in _rows(env)] == ["catering_followup_sent"]

    def test_the_claim_is_on_disk_before_the_bridge_is_called(self, env, monkeypatch):
        """`approved_sent` may only ever mean "the customer HAS this". Writing it
        first made the store lie for the whole duration of the send — and stay
        lying if the send failed."""
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        seen: dict = {}

        def _post(jid, message):
            seen["record"] = _store(env)[0]
            return True, "wamid.OK"

        _run(env, monkeypatch, SWEEP, "sweep_auto_claim", post=_post)
        assert seen["record"]["status"] == "sending"
        assert seen["record"]["claimed_at"]

    def test_a_failed_autosend_is_retryable_not_terminal(self, env, monkeypatch):
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_auto_fail", bridge_ok=False)
        assert rc == 0 and len(sends) == 1
        record = _store(env)[0]
        assert record["status"] == "scheduled"
        assert record["claimed_at"] is None
        assert record["approval_code"] is None, "the next cycle re-renders and re-mints"
        assert record["attempt_count"] == 1
        row = [r for r in _rows(env) if r["type"] == "catering_followup_send_failed"][-1]
        assert row["released_to"] == "scheduled" and row["error"] == "connect_failed"
        assert "catering_followup_sent" not in _types(env)

    def test_the_retry_is_bounded_and_the_owner_hears_about_the_end_of_it(
            self, env, monkeypatch):
        monkeypatch.setenv("CATERING_FOLLOWUP_AUTOSEND", "1")
        paged: list = []

        def _notify(title, message, **kw):
            paged.append(message)
            return True

        _run(env, monkeypatch, SWEEP, "sweep_auto_f1", bridge_ok=False, notify=_notify)
        rc, _ = _run(env, monkeypatch, SWEEP, "sweep_auto_f2", bridge_ok=False,
                     notify=_notify)
        assert rc == 0
        record = _store(env)[0]
        assert record["status"] == "expired" and record["attempt_count"] == 2
        assert len(paged) == 1 and "L0001" in paged[0]


class TestSweepReclaimsStaleClaims:
    """A process killed between the claim and the confirm must not strand the
    follow-up in a status nothing else reads."""

    def _claimed(self, *, claimed_minutes_ago: int, carded_hours_ago: int = 1) -> dict:
        return _followup(
            status="sending", approval_code="#AAAAA", rendered_message="hi",
            attempt_count=1,
            last_attempt_at=(DAYTIME - timedelta(hours=carded_hours_ago)).isoformat(),
            claimed_at=(DAYTIME - timedelta(minutes=claimed_minutes_ago)).isoformat(),
        )

    def test_a_stale_claim_returns_to_the_card_it_was_claimed_from(self, env, monkeypatch):
        _write_followups(env, self._claimed(claimed_minutes_ago=30))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_reclaim")
        assert rc == 0 and sends == []
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert record["claimed_at"] is None
        assert record["approval_code"] == "#AAAAA", "the owner's card is untouched"

    def test_a_live_claim_is_left_alone(self, env, monkeypatch):
        """This process cannot tell "crashed" from "slower than me"."""
        _write_followups(env, self._claimed(claimed_minutes_ago=5))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_reclaim_fresh")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "sending"

    def test_a_reclaimed_card_that_also_lapsed_is_re_carded_the_same_cycle(
            self, env, monkeypatch):
        _write_followups(env, self._claimed(claimed_minutes_ago=30,
                                            carded_hours_ago=5))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_reclaim_lapsed")
        assert rc == 0 and len(sends) == 1 and sends[0][0] == OWNER_JID
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert record["attempt_count"] == 2
        assert record["approval_code"] != "#AAAAA", "a re-card mints a fresh code"


class TestExpiryNotifiesTheOwner:
    """Every other automated retirement in catering announces itself; this one
    was silent, so a lead the owner meant to chase could go quiet with the only
    record of it in an audit file nobody reads."""

    def _lapsed(self, attempt: int) -> dict:
        return _followup(status="awaiting_owner_approval", approval_code="#AAAAA",
                         rendered_message="hi", attempt_count=attempt,
                         last_attempt_at=(DAYTIME - timedelta(hours=5)).isoformat())

    def test_the_final_lapse_pages_the_owner(self, env, monkeypatch):
        paged: list = []

        def _notify(title, message, **kw):
            paged.append((title, message))
            return True

        _write_followups(env, self._lapsed(attempt=2))
        rc, _ = _run(env, monkeypatch, SWEEP, "sweep_expire_page", notify=_notify)
        assert rc == 0 and _store(env)[0]["status"] == "expired"
        assert len(paged) == 1
        title, message = paged[0]
        assert "L0001" in message and "expired" in message.lower()
        assert "proposal_unanswered" in message, (
            "plain text: an underscore-bearing type must survive to the owner intact"
        )

    def test_a_re_card_does_not_page(self, env, monkeypatch):
        """Only the RETIREMENT is news; a re-card is the loop working."""
        paged: list = []
        _write_followups(env, self._lapsed(attempt=1))
        rc, _ = _run(env, monkeypatch, SWEEP, "sweep_recard_nopage",
                     notify=lambda *a, **k: paged.append(a) or True)
        assert rc == 0 and paged == []

    def test_a_paging_failure_never_fails_the_cycle(self, env, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("pushover on fire")

        _write_followups(env, self._lapsed(attempt=2))
        rc, _ = _run(env, monkeypatch, SWEEP, "sweep_page_boom", notify=_boom)
        assert rc == 0, "a timer-driven sweep never fails its unit"
        assert _store(env)[0]["status"] == "expired"


class TestSweepResilience:
    def test_a_bridge_failure_leaves_the_card_for_the_ttl_to_retry(self, env, monkeypatch):
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_bridgefail", bridge_ok=False)
        assert rc == 0, "a timer-driven sweep never fails its unit"
        assert len(sends) == 1
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert "catering_followup_card_sent" not in _types(env)

    def test_an_unreadable_store_skips_the_cycle_without_writing_a_new_one(
            self, env, monkeypatch):
        """safe_load_json quarantines a corrupt file as .corrupt-<epoch>. The sweep
        must stop there — writing a fresh empty store over it would silently drop
        every scheduled follow-up and look like a clean cycle."""
        env["followups"].write_text("{not json", encoding="utf-8")
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_corrupt")
        assert rc == 0 and sends == []
        assert not env["followups"].exists()
        quarantined = list(env["state"].glob("catering-followups.json.corrupt-*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_text(encoding="utf-8") == "{not json"


class TestSendChokepointAdmitsTheFollowupScripts:
    """The bug the stubbed-bridge tests above CANNOT catch.

    Every other test in this file monkeypatches `_bridge_post`, so it proves the
    script's own logic and nothing about the chokepoint the send actually goes
    through. Both follow-up scripts call `bridge_post_2tuple` via a module-level
    alias, which the PR-ζ AST static gate cannot see (its own docstring admits
    the indirect-call blind spot) — so the RUNTIME caller-basename match against
    SAFE_IO_NULL_CONTEXT_ALLOWLIST is the only thing admitting them. Without the
    allowlist entries every card and every approved send is refused with
    `missing_action_context` and the engine ships dead.
    """

    @pytest.mark.parametrize("basename", ALIASED_CATERING_SENDERS)
    def test_the_caller_basename_is_admitted_by_the_policy(self, basename, monkeypatch):
        import safe_io
        monkeypatch.setattr(safe_io, "_resolve_caller_script_name", lambda: basename)
        assert safe_io._enforce_action_context_policy(
            message_parts=["hello"], jid=CUSTOMER_JID, action_context=None,
        ) is None, f"{basename} would be refused with missing_action_context"

    def test_an_unlisted_caller_is_still_refused(self, monkeypatch):
        """Evidence the assertion above is not vacuous."""
        import safe_io
        monkeypatch.setattr(safe_io, "_resolve_caller_script_name",
                            lambda: "not-a-real-script")
        refusal = safe_io._enforce_action_context_policy(
            message_parts=["hello"], jid=CUSTOMER_JID, action_context=None,
        )
        assert refusal is not None and refusal[2] == "missing_action_context"

    def test_why_no_end_to_end_variant_of_this_test_exists(self):
        """Pins the reason the assertions above are indirect.

        bridge_post checks BRIDGE_URL before it checks the action-context policy.
        Under pytest the autouse fixture points BRIDGE_URL at a closed sink, so
        every in-test send short-circuits to 'connect_failed' and NEVER reaches
        the policy. An end-to-end 'run the sweep and inspect the status' test
        would therefore pass whether or not the caller is allowlisted — false
        confidence. If this ordering ever changes, the guard above is the one to
        extend, not to replace with an e2e.
        """
        import inspect
        import safe_io
        src = inspect.getsource(safe_io.bridge_post)
        url_check = src.index("validate_bridge_url")
        policy_check = src.index("_enforce_action_context_policy")
        assert url_check < policy_check, (
            "bridge_post now checks the action-context policy before the bridge "
            "URL — an end-to-end allowlist test has become possible; add one."
        )

    def test_every_catering_script_that_aliases_the_chokepoint_is_allowlisted(self):
        """The generalised guard: the PR-ζ AST gate matches call NAMES, so a
        script that does `from safe_io import bridge_post_2tuple as _bridge_post`
        is invisible to it and is admitted at runtime by basename alone. Any
        catering script importing a bridge_post* helper under an alias must
        therefore carry an allowlist entry, or it ships refusing every send.
        """
        import re
        import safe_io
        alias_import = re.compile(
            r"^from safe_io import (?:bridge_post|bridge_post_2tuple|"
            r"bridge_send_media|bridge_send_cta) as (\w+)", re.MULTILINE)
        aliasing = {
            script.name for script in SCRIPTS.iterdir()
            if script.is_file()
            and alias_import.search(script.read_text(encoding="utf-8", errors="ignore"))
        }
        missing = aliasing - set(safe_io.SAFE_IO_NULL_CONTEXT_ALLOWLIST)
        assert missing == KNOWN_UNALLOWLISTED_CATERING_SENDERS, (
            f"the set of catering scripts that alias the send chokepoint without "
            f"an allowlist entry changed.\n"
            f"  now missing : {sorted(missing)}\n"
            f"  expected    : {sorted(KNOWN_UNALLOWLISTED_CATERING_SENDERS)}\n"
            f"A NEW name here ships refusing every send it makes — add it to "
            f"SAFE_IO_NULL_CONTEXT_ALLOWLIST. A name that DISAPPEARED means a gap "
            f"was closed; shrink KNOWN_UNALLOWLISTED_CATERING_SENDERS to match."
        )

    def test_the_per_script_assertions_cover_every_aliasing_script(self):
        """Keeps ALIASED_CATERING_SENDERS honest. Without this, a new aliasing
        script could be allowlisted (satisfying the scan above) while never
        getting its own policy assertion — the list would silently rot."""
        import re
        alias_import = re.compile(
            r"^from safe_io import (?:bridge_post|bridge_post_2tuple|"
            r"bridge_send_media|bridge_send_cta) as (\w+)", re.MULTILINE)
        aliasing = {
            script.name for script in SCRIPTS.iterdir()
            if script.is_file()
            and alias_import.search(script.read_text(encoding="utf-8", errors="ignore"))
        }
        assert aliasing == set(ALIASED_CATERING_SENDERS), (
            f"ALIASED_CATERING_SENDERS is stale.\n"
            f"  scripts aliasing the chokepoint : {sorted(aliasing)}\n"
            f"  listed for policy assertion     : {sorted(ALIASED_CATERING_SENDERS)}"
        )


# ── approve / cancel ─────────────────────────────────────────────────────────
def _card(env, monkeypatch) -> str:
    """Run the sweep once and return the minted approval code."""
    _run(env, monkeypatch, SWEEP, f"sweep_seed_{id(env)}")
    return _store(env)[0]["approval_code"]


class TestApprove:
    def test_approve_sends_the_stored_text_to_the_customer(self, env, monkeypatch):
        code = _card(env, monkeypatch)
        stored = _store(env)[0]["rendered_message"]
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_ok",
                         "--code", code, "--decision", "approve",
                         "--sender-role", "owner")
        assert rc == 0
        jid, message = sends[0]
        assert jid == CUSTOMER_JID
        assert message == f"⚕ *Catering Agent*\n────────────\n{stored}", (
            "the customer receives exactly the body the owner approved"
        )
        record = _store(env)[0]
        assert record["status"] == "approved_sent"
        assert record["sent_message_id"] == "wamid.OK"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_sent"][-1]
        assert row["approval_code"] == code

    def test_replay_after_success_sends_nothing(self, env, monkeypatch):
        code = _card(env, monkeypatch)
        _run(env, monkeypatch, APPROVE, "appr_r1", "--code", code,
             "--decision", "approve", "--sender-role", "owner")
        before = len([r for r in _rows(env) if r["type"] == "catering_followup_sent"])
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_r2", "--code", code,
                         "--decision", "approve", "--sender-role", "owner")
        assert rc == 0 and sends == []
        after = len([r for r in _rows(env) if r["type"] == "catering_followup_sent"])
        assert after == before

    def test_cancel_drops_the_followup_without_sending(self, env, monkeypatch):
        code = _card(env, monkeypatch)
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_cancel", "--code", code,
                         "--decision", "cancel", "--sender-role", "owner",
                         "--reason", "already spoke to them")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "cancelled"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_cancelled"][-1]
        assert row["actor"] == "owner" and row["reason"] == "already spoke to them"

    def test_unknown_code_is_not_found(self, env, monkeypatch):
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_unknown",
                         "--code", "#ZZZZZ", "--decision", "approve",
                         "--sender-role", "owner")
        assert rc == 4 and sends == []

    def test_a_scheduled_followup_has_no_code_to_approve(self, env, monkeypatch):
        """Only a carded follow-up is approvable — there is nothing to consent to
        before the sweep has rendered and shown the text."""
        rc, _ = _run(env, monkeypatch, APPROVE, "appr_sched", "--code", "#ABCDE",
                     "--decision", "approve", "--sender-role", "owner")
        assert rc == 4

    @pytest.mark.parametrize("role", ["customer", "employee", "unknown"])
    def test_non_owner_is_refused_before_any_state_read(self, env, monkeypatch, role):
        code = _card(env, monkeypatch)
        rc, sends = _run(env, monkeypatch, APPROVE, f"appr_role_{role}",
                         "--code", code, "--decision", "approve",
                         "--sender-role", role)
        assert rc == 12 and sends == []
        assert _store(env)[0]["status"] == "awaiting_owner_approval"

    def test_malformed_code_is_rejected(self, env, monkeypatch):
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_bad", "--code", "ABCDE",
                         "--decision", "approve", "--sender-role", "owner")
        assert rc == 2 and sends == []

    def test_suppression_is_rechecked_at_approval_time(self, env, monkeypatch):
        """A card can be hours old. A lead put on hold in between must not receive
        the message just because the owner tapped approve."""
        code = _card(env, monkeypatch)
        _write_leads(env, _lead(on_hold=True, hold_reason="paused"))
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_hold", "--code", code,
                         "--decision", "approve", "--sender-role", "owner")
        assert rc == 14 and sends == []
        assert _store(env)[0]["status"] == "suppressed"
        assert _store(env)[0]["suppressed_reason"] == "lead_on_hold"

    def test_a_bridge_failure_leaves_the_card_open(self, env, monkeypatch):
        code = _card(env, monkeypatch)
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_fail", "--code", code,
                         "--decision", "approve", "--sender-role", "owner",
                         bridge_ok=False)
        assert rc == 6 and len(sends) == 1
        record = _store(env)[0]
        assert record["status"] == "awaiting_owner_approval"
        assert record["claimed_at"] is None, "the claim is released, not stranded"
        assert record["approval_code"] == code, "the owner can simply approve again"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_send_failed"][-1]
        assert row["released_to"] == "awaiting_owner_approval"
        assert row["error"] == "connect_failed"
        assert "catering_followup_sent" not in _types(env)

    def test_a_failed_claim_does_not_restart_the_owners_approval_window(
            self, env, monkeypatch):
        """last_attempt_at anchors the 4h card TTL. If the claim stamped it, a
        flapping bridge would extend the window every time it failed."""
        code = _card(env, monkeypatch)
        before = _store(env)[0]["last_attempt_at"]
        _run(env, monkeypatch, APPROVE, "appr_ttl", "--code", code, "--decision",
             "approve", "--sender-role", "owner", bridge_ok=False)
        assert _store(env)[0]["last_attempt_at"] == before


class TestApproveIsSingleSend:
    """The interleave the lock never covered: check under the lock, RELEASE it,
    send, re-lock to record. Two owner taps both passed the check."""

    def test_two_approvals_of_one_code_send_exactly_once(self, env, monkeypatch):
        code = _card(env, monkeypatch)
        sends: list = []
        loser: dict = {}

        def _reentrant_post(jid, message):
            # The second tap lands while the first invocation is at the bridge.
            if not loser:
                loser["rc"], _ = _run(
                    env, monkeypatch, APPROVE, "appr_race_b", "--code", code,
                    "--decision", "approve", "--sender-role", "owner",
                    post=_reentrant_post)
            sends.append((jid, message))
            return True, "wamid.OK"

        rc, _ = _run(env, monkeypatch, APPROVE, "appr_race_a", "--code", code,
                     "--decision", "approve", "--sender-role", "owner",
                     post=_reentrant_post)
        assert rc == 0
        assert loser["rc"] == 0, "the loser reports success — the send IS happening"
        assert len(sends) == 1, "the customer must receive exactly one copy"
        assert len([r for r in _rows(env) if r["type"] == "catering_followup_sent"]) == 1
        record = _store(env)[0]
        assert record["status"] == "approved_sent"
        assert record["sent_message_id"] == "wamid.OK"
        assert record["claimed_at"] is None

    def test_an_already_claimed_code_is_a_no_op(self, env, monkeypatch, capsys):
        _write_followups(env, _followup(
            status="sending", approval_code="#AAAAA", rendered_message="hi",
            attempt_count=1, last_attempt_at=DAYTIME.isoformat(),
            claimed_at=(DAYTIME - timedelta(minutes=1)).isoformat()))
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_claimed", "--code",
                         "#AAAAA", "--decision", "approve", "--sender-role", "owner")
        assert rc == 0 and sends == []
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["in_flight"] is True and payload["idempotent_replay"] is True
        assert _store(env)[0]["status"] == "sending"

    def test_a_claim_cannot_be_cancelled_out_from_under_the_sender(
            self, env, monkeypatch):
        _write_followups(env, _followup(
            status="sending", approval_code="#AAAAA", rendered_message="hi",
            attempt_count=1, last_attempt_at=DAYTIME.isoformat(),
            claimed_at=(DAYTIME - timedelta(minutes=1)).isoformat()))
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_claimed_cancel", "--code",
                         "#AAAAA", "--decision", "cancel", "--sender-role", "owner")
        assert rc == 0 and sends == []
        assert _store(env)[0]["status"] == "sending"
        assert "catering_followup_cancelled" not in _types(env)


class TestApproveNamesTheRuleThatFired:
    """EXIT_LEAD_ON_HOLD (14) means "clear the hold". Returning it for a kill
    switch or an opt-out sent the owner to `set-catering-lead-hold --off` for a
    hold nobody ever set, and the skill explained the wrong reason."""

    def _payload(self, capsys) -> dict:
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    def test_a_hold_keeps_the_hold_code(self, env, monkeypatch, capsys):
        code = _card(env, monkeypatch)
        _write_leads(env, _lead(on_hold=True, hold_reason="paused"))
        rc, _ = _run(env, monkeypatch, APPROVE, "appr_code_hold", "--code", code,
                     "--decision", "approve", "--sender-role", "owner")
        assert rc == 14
        assert self._payload(capsys)["suppressed_reason"] == "lead_on_hold"

    def test_the_kill_switch_is_not_a_hold(self, env, monkeypatch, capsys):
        code = _card(env, monkeypatch)
        (env["state"] / "disabled.flag").write_text("", encoding="utf-8")
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_code_kill", "--code", code,
                         "--decision", "approve", "--sender-role", "owner")
        assert rc == 16 and sends == []
        assert self._payload(capsys)["suppressed_reason"] == "kill_switch"

    def test_a_closed_lead_is_not_a_hold(self, env, monkeypatch, capsys):
        code = _card(env, monkeypatch)
        _write_leads(env, _lead(status="CLOSED"))
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_code_closed", "--code", code,
                         "--decision", "approve", "--sender-role", "owner")
        assert rc == 16 and sends == []
        assert self._payload(capsys)["suppressed_reason"] == "lead_status_not_allowed"

    def test_a_vanished_lead_is_not_a_hold(self, env, monkeypatch, capsys):
        code = _card(env, monkeypatch)
        _write_leads(env)
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_code_gone", "--code", code,
                         "--decision", "approve", "--sender-role", "owner")
        assert rc == 16 and sends == []
        assert self._payload(capsys)["suppressed_reason"] == "lead_missing"


class TestApproveQuietHours:
    """The owner approving at 23:00 is not a reason to message a customer at
    23:00 — and not a reason to destroy the follow-up either."""

    def test_an_approval_inside_quiet_hours_defers_the_send(self, env, monkeypatch,
                                                            capsys):
        code = _card(env, monkeypatch)
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_quiet", "--code", code,
                         "--decision", "approve", "--sender-role", "owner",
                         now=_at(23, 0))
        assert rc == 0 and sends == []
        record = _store(env)[0]
        assert record["status"] == "scheduled", "deferred, never terminal"
        assert record["suppressed_reason"] is None
        assert record["approval_code"] is None, (
            "the owner should see the card at the hour it will actually send"
        )
        row = [r for r in _rows(env) if r["type"] == "catering_followup_deferred"][-1]
        assert row["reason"] == "quiet_hours"
        assert record["due_at"] == row["to_due_at"]
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["deferred_reason"] == "quiet_hours"
        assert payload["deferred_until"] == record["due_at"]

    def test_the_deferred_followup_is_carded_again_when_the_window_reopens(
            self, env, monkeypatch):
        code = _card(env, monkeypatch)
        _run(env, monkeypatch, APPROVE, "appr_quiet_defer", "--code", code,
             "--decision", "approve", "--sender-role", "owner", now=_at(23, 0))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_after_defer",
                         now=_at(9, 15) + timedelta(days=1))
        assert rc == 0 and len(sends) == 1 and sends[0][0] == OWNER_JID
        assert _store(env)[0]["status"] == "awaiting_owner_approval"

    @pytest.mark.parametrize("hour,minute,quiet", [
        (20, 59, False), (21, 0, True), (8, 59, True), (9, 0, False)])
    def test_the_window_boundaries(self, env, monkeypatch, hour, minute, quiet):
        code = _card(env, monkeypatch)
        rc, sends = _run(env, monkeypatch, APPROVE, f"appr_q_{hour}_{minute}",
                         "--code", code, "--decision", "approve",
                         "--sender-role", "owner", now=_at(hour, minute))
        assert rc == 0
        if quiet:
            assert sends == [] and _store(env)[0]["status"] == "scheduled"
        else:
            assert len(sends) == 1 and _store(env)[0]["status"] == "approved_sent"


class TestApproveRechecksTheAllowlist:
    """The sweep gates on CATERING_FOLLOWUP_ALLOWLIST; approve did not. A card in
    flight when the operator narrowed the rollout still sent."""

    def test_a_card_does_not_outlive_a_narrowing_of_the_rollout(
            self, env, monkeypatch, capsys):
        code = _card(env, monkeypatch)          # minted under "*"
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "")
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_allow_empty", "--code",
                         code, "--decision", "approve", "--sender-role", "owner")
        assert rc == 16 and sends == []
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["suppressed_reason"] == "not_in_allowlist"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_suppressed"][-1]
        assert row["reason"] == "not_in_allowlist"

    def test_the_refusal_is_a_gate_not_a_retirement(self, env, monkeypatch):
        """Same posture the sweep already takes: widening the allowlist again
        must find the card, not a graveyard."""
        code = _card(env, monkeypatch)
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "")
        _run(env, monkeypatch, APPROVE, "appr_allow_gate", "--code", code,
             "--decision", "approve", "--sender-role", "owner")
        assert _store(env)[0]["status"] == "awaiting_owner_approval"
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "*")
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_allow_rewide", "--code",
                         code, "--decision", "approve", "--sender-role", "owner")
        assert rc == 0 and len(sends) == 1
        assert _store(env)[0]["status"] == "approved_sent"

    def test_a_still_listed_number_is_admitted(self, env, monkeypatch):
        code = _card(env, monkeypatch)
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "+15550100777")
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_allow_named", "--code",
                         code, "--decision", "approve", "--sender-role", "owner")
        assert rc == 0 and len(sends) == 1 and sends[0][0] == CUSTOMER_JID

    def test_an_orphaned_followup_reports_the_missing_lead_not_the_allowlist(
            self, env, monkeypatch, capsys):
        """An orphan has no conversation to gate; `lead_missing` is the truthful
        reason and the one that retires it."""
        code = _card(env, monkeypatch)
        _write_leads(env)
        monkeypatch.setenv("CATERING_FOLLOWUP_ALLOWLIST", "")
        rc, sends = _run(env, monkeypatch, APPROVE, "appr_allow_orphan", "--code",
                         code, "--decision", "approve", "--sender-role", "owner")
        assert rc == 16 and sends == []
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["suppressed_reason"] == "lead_missing"
        assert _store(env)[0]["status"] == "suppressed"


# ── create (owner_reminder) ──────────────────────────────────────────────────
def _run_create(env, monkeypatch, *argv):
    mod = load_script(f"create_fu_{len(argv)}_{id(argv)}", CREATE)
    old = sys.argv
    sys.argv = ["create-catering-followup", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old


class TestCreate:
    def test_relative_due_at_schedules_an_owner_reminder(self, env, monkeypatch):
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+2d") == 0
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"]
        assert len(created) == 1
        assert created[0]["created_by"] == "owner"
        assert created[0]["status"] == "scheduled"
        assert created[0]["note"] is None
        row = [r for r in _rows(env) if r["type"] == "catering_followup_scheduled"][-1]
        assert row["created_by"] == "owner" and row["trigger"] == "owner_cli"

    def test_iso_due_at_is_accepted(self, env, monkeypatch):
        assert _run_create(env, monkeypatch, "--lead-id", "L0001",
                           "--due-at", "2099-08-14T10:00:00+00:00") == 0
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"][0]
        assert created["due_at"].startswith("2099-08-14T10:00")

    def test_the_note_is_stored_normalised(self, env, monkeypatch):
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+3h",
                           "--note", "  *Ask* about\nthe 14th  ") == 0
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"][0]
        assert created["note"] == "Ask about the 14th"

    def test_the_note_reaches_the_rendered_message(self, env, monkeypatch):
        """End of the chain: --note -> store -> sweep render -> owner card."""
        _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+1h",
                    "--note", "Ask about the 14th")
        # Make it due, and clear the fixture's own follow-up so only ours cards.
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"][0]
        created["due_at"] = "2026-07-30T14:00:00+00:00"
        _write_followups(env, created)
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_note")
        assert rc == 0 and len(sends) == 1
        assert "Ask about the 14th" in sends[0][1]
        assert "Ask about the 14th" in _store(env)[0]["rendered_message"]

    def test_a_reminder_without_a_note_renders_cleanly(self, env, monkeypatch):
        _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+1h")
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"][0]
        created["due_at"] = "2026-07-30T14:00:00+00:00"
        _write_followups(env, created)
        rc, _ = _run(env, monkeypatch, SWEEP, "sweep_nonote")
        assert rc == 0
        rendered = _store(env)[0]["rendered_message"]
        assert "{" not in rendered and "\n\n\n" not in rendered

    def test_unknown_lead_is_refused(self, env, monkeypatch):
        assert _run_create(env, monkeypatch, "--lead-id", "L9999", "--due-at", "+2d") == 4
        assert not [f for f in _store(env) if f["followup_type"] == "owner_reminder"]

    @pytest.mark.parametrize("status", ["CLOSED", "OWNER_REJECTED", "STALE",
                                        "NOT_CATERING"])
    def test_a_lead_in_a_terminal_status_is_refused(self, env, monkeypatch, status):
        _write_leads(env, _lead(status=status))
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+2d") == 4
        assert not [f for f in _store(env) if f["followup_type"] == "owner_reminder"]

    @pytest.mark.parametrize("raw", ["", "tomorrow", "+0d", "+2w", "2d", "+d",
                                     "not-a-date", "+-3h"])
    def test_malformed_due_at_is_refused(self, env, monkeypatch, raw):
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", raw) == 2
        assert not [f for f in _store(env) if f["followup_type"] == "owner_reminder"]

    def test_a_due_time_in_the_past_is_refused(self, env, monkeypatch):
        """It would fire on the very next sweep — almost certainly a typo."""
        assert _run_create(env, monkeypatch, "--lead-id", "L0001",
                           "--due-at", "2020-01-01T00:00:00+00:00") == 2
        assert not [f for f in _store(env) if f["followup_type"] == "owner_reminder"]

    def test_the_owner_may_schedule_the_same_day_twice(self, env, monkeypatch):
        """created_by='owner' bypasses the dedup: a human asking again is not the
        system re-litigating a decision it already made."""
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+2d") == 0
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+2d") == 0
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"]
        assert len(created) == 2
        assert len({f["followup_id"] for f in created}) == 2

    def test_an_unreadable_leads_store_refuses_rather_than_scheduling_blind(
            self, env, monkeypatch):
        env["leads"].write_text("{not json", encoding="utf-8")
        assert _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+2d") == 5
        assert not [f for f in _store(env) if f["followup_type"] == "owner_reminder"]

    def test_owner_created_reminders_are_not_capped_at_sweep_time(self, env, monkeypatch):
        """The lifetime budget stops the SYSTEM pestering; it does not overrule
        the owner."""
        for _ in range(5):
            assert _run_create(env, monkeypatch, "--lead-id", "L0001",
                               "--due-at", "+2d") == 0
        due = []
        for f in _store(env):
            if f["followup_type"] == "owner_reminder":
                f["due_at"] = "2026-07-30T14:00:00+00:00"
                due.append(f)
        _write_followups(env, *due)
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_ownercap")
        assert rc == 0
        assert len(sends) == 5
        assert all(f["status"] == "awaiting_owner_approval" for f in _store(env))

    def test_an_owner_reminder_is_still_subject_to_the_customer_protections(
            self, env, monkeypatch):
        """Owner-created bypasses dedup and the cap — never the kill switch, a
        hold, or an opt-out."""
        _run_create(env, monkeypatch, "--lead-id", "L0001", "--due-at", "+1h")
        created = [f for f in _store(env) if f["followup_type"] == "owner_reminder"][0]
        created["due_at"] = "2026-07-30T14:00:00+00:00"
        _write_followups(env, created)
        _write_leads(env, _lead(on_hold=True, hold_reason="customer asked us to wait"))
        rc, sends = _run(env, monkeypatch, SWEEP, "sweep_ownerhold")
        assert rc == 0 and sends == []
        assert _store(env)[0]["suppressed_reason"] == "lead_on_hold"


class TestDueAtParsing:
    """Pure parser cells — the argument an owner is most likely to fat-finger."""

    @pytest.fixture
    def parse(self):
        return load_script("create_fu_parse", CREATE).parse_due_at

    def test_hours_and_days(self, parse):
        now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
        assert parse("+6h", now=now) == now + timedelta(hours=6)
        assert parse("+3d", now=now) == now + timedelta(days=3)
        assert parse("+2D", now=now) == now + timedelta(days=2)

    def test_naive_iso_is_read_as_utc(self, parse):
        now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
        assert parse("2026-08-14T10:00", now=now) == datetime(
            2026, 8, 14, 10, 0, tzinfo=timezone.utc)

    def test_an_offset_bearing_iso_keeps_its_offset(self, parse):
        now = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
        parsed = parse("2026-08-14T10:00:00-04:00", now=now)
        assert parsed.utcoffset() == timedelta(hours=-4)

    @pytest.mark.parametrize("raw", ["", "   ", "+0h", "+0d", "2 days", "+2w",
                                     "next friday", "++3h"])
    def test_rejects_what_it_cannot_read(self, parse, raw):
        assert parse(raw, now=datetime(2026, 7, 31, tzinfo=timezone.utc)) is None


# ── status ───────────────────────────────────────────────────────────────────
class TestStatus:
    def test_reports_the_queue_without_mutating_it(self, env, monkeypatch, capsys):
        before = env["followups"].read_text(encoding="utf-8")
        mod = load_script("status_ro", STATUS)
        sys.argv = ["catering-followup-status", "--json"]
        assert mod.main() == 0
        report = json.loads(capsys.readouterr().out)
        assert report["store_readable"] is True
        assert report["total"] == 1
        assert report["counts"] == {"scheduled": 1}
        assert [r["followup_id"] for r in report["due_now"]] == ["FU0001"]
        assert env["followups"].read_text(encoding="utf-8") == before
        assert _types(env) == [], "a read-only report emits no audit rows"

    def test_reports_arming_flags(self, env, monkeypatch, capsys):
        mod = load_script("status_flags", STATUS)
        sys.argv = ["catering-followup-status", "--json"]
        mod.main()
        report = json.loads(capsys.readouterr().out)
        assert report["flags"]["CATERING_FOLLOWUP_ENABLED"] == "1"
        assert report["flags"]["allowlist"]["mode"] == "wildcard"

    def test_an_unreadable_store_is_reported_not_raised(self, env, monkeypatch, capsys):
        env["followups"].write_text("{not json", encoding="utf-8")
        mod = load_script("status_corrupt", STATUS)
        sys.argv = ["catering-followup-status", "--json"]
        assert mod.main() == 0
        report = json.loads(capsys.readouterr().out)
        assert report["store_readable"] is False
        assert report["total"] == 0

    def test_an_in_flight_claim_is_reported_as_open(self, env, monkeypatch, capsys):
        """A claimed send is neither finished nor idle; an operator looking for it
        must not find it in neither bucket."""
        _write_followups(env, _followup(status="sending", approval_code="#AAAAA",
                                        claimed_at=DAYTIME.isoformat()))
        mod = load_script("status_sending", STATUS)
        sys.argv = ["catering-followup-status", "--json"]
        assert mod.main() == 0
        report = json.loads(capsys.readouterr().out)
        assert [r["followup_id"] for r in report["open"]] == ["FU0001"]
        assert report["open"][0]["claimed_at"]

    def test_lead_filter(self, env, monkeypatch, capsys):
        _write_followups(env, _followup(), _followup(followup_id="FU0002",
                                                     lead_id="L0002"))
        mod = load_script("status_filter", STATUS)
        sys.argv = ["catering-followup-status", "--json", "--lead-id", "L0002"]
        mod.main()
        report = json.loads(capsys.readouterr().out)
        assert [r["followup_id"] for r in report["followups"]] == ["FU0002"]


# ── trigger wiring ───────────────────────────────────────────────────────────
class TestQualificationTrigger:
    """M1's loop exhausting its rounds schedules ONE nudge — and a scheduling
    failure must never break the handoff that hosts it."""

    def _qualifying_lead(self) -> dict:
        return _lead(
            status="QUALIFYING", quote_text="",
            extracted={"headcount": 40},
            pending_questions=["venue"],
            questions_asked=["event_date", "guest_count", "event_type", "venue",
                             "service_style", "veg_nonveg"],
            qualification_rounds=3,
        )

    def _run_answer(self, env, monkeypatch, *, patch=None):
        _write_leads(env, self._qualifying_lead())
        mod = load_script("amend_trigger", AMEND)
        monkeypatch.setattr(mod, "LEADS_PATH", env["leads"])
        monkeypatch.setattr(mod, "LEADS_LOCK", Path(str(env["leads"]) + ".lock"))
        monkeypatch.setattr(mod, "LOG_PATH", env["log"])
        monkeypatch.setattr(mod, "_bridge_post", lambda jid, msg: (True, "wamid.OK"))
        if patch is not None:
            monkeypatch.setattr(mod.catering_followups, "schedule_followup", patch)
        old = sys.argv
        sys.argv = ["amend-catering-lead", "--lead-id", "L0001", "--mode", "answer",
                    "--answer-text", "not sure yet"]
        try:
            return mod.main()
        finally:
            sys.argv = old

    def test_round_cap_handoff_schedules_one_followup(self, env, monkeypatch):
        assert self._run_answer(env, monkeypatch) == 0
        records = _store(env)
        scheduled = [f for f in records
                     if f["followup_type"] == "incomplete_qualification"]
        assert len(scheduled) == 1
        assert scheduled[0]["status"] == "scheduled"
        row = [r for r in _rows(env) if r["type"] == "catering_followup_scheduled"][-1]
        assert row["trigger"] == "qualification_round_cap"

    def test_config_disabled_schedules_nothing(self, env, monkeypatch, tmp_path):
        monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH",
                           str(write_config(tmp_path / "state",
                                            catering_followup={"enabled": False})))
        assert self._run_answer(env, monkeypatch) == 0
        assert not [f for f in _store(env)
                    if f["followup_type"] == "incomplete_qualification"]

    def test_a_scheduling_failure_does_not_break_the_handoff(self, env, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("store on fire")

        assert self._run_answer(env, monkeypatch, patch=_boom) == 0, (
            "the qualification handoff is the product; the nudge is a courtesy"
        )
        lead = json.loads(env["leads"].read_text(encoding="utf-8"))["leads"][0]
        assert lead["status"] == "AWAITING_OWNER_APPROVAL"
        assert "health_check_failure" in _types(env)


class TestQuoteSentTrigger:
    """apply-catering-owner-decision's marked M5 block."""

    def _helper(self, monkeypatch, env):
        mod = load_script("apply_trigger", SCRIPTS / "apply-catering-owner-decision")
        return mod

    def test_schedules_the_four_quote_anchored_followups(self, env, monkeypatch):
        from schemas import CateringLead, Config
        from safe_io import load_yaml_model
        mod = self._helper(monkeypatch, env)
        cfg = load_yaml_model(Path(os.environ["SHIFT_AGENT_CONFIG_PATH"]), Config)
        lead = CateringLead.model_validate(_lead())
        mod._schedule_followups_after_quote_sent(
            cfg=cfg, lead=lead, now=datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc))
        by_type = {f["followup_type"]: f for f in _store(env)}
        assert set(by_type) >= {"proposal_unanswered", "event_approaching",
                                "final_headcount_due", "post_event_feedback"}
        assert by_type["event_approaching"]["due_at"].startswith("2026-08-25")
        assert by_type["final_headcount_due"]["due_at"].startswith("2026-08-29")
        assert by_type["post_event_feedback"]["due_at"].startswith("2026-09-02")

    def test_without_an_event_date_only_the_unanswered_nudge_is_scheduled(
            self, env, monkeypatch):
        from schemas import CateringLead, Config
        from safe_io import load_yaml_model
        mod = self._helper(monkeypatch, env)
        cfg = load_yaml_model(Path(os.environ["SHIFT_AGENT_CONFIG_PATH"]), Config)
        lead = CateringLead.model_validate(_lead(extracted={"headcount": 40}))
        mod._schedule_followups_after_quote_sent(
            cfg=cfg, lead=lead, now=datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc))
        added = [f for f in _store(env) if f["followup_id"] != "FU0001"]
        assert [f["followup_type"] for f in added] == ["proposal_unanswered"], (
            "the three event-anchored types need a date and must not be guessed"
        )

    def test_config_disabled_schedules_nothing(self, env, monkeypatch, tmp_path):
        from schemas import CateringLead, Config
        from safe_io import load_yaml_model
        mod = self._helper(monkeypatch, env)
        cfg_path = write_config(tmp_path / "state", catering_followup={"enabled": False})
        cfg = load_yaml_model(cfg_path, Config)
        mod._schedule_followups_after_quote_sent(
            cfg=cfg, lead=CateringLead.model_validate(_lead()),
            now=datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc))
        assert len(_store(env)) == 1  # only the fixture's own record

    def test_a_raising_scheduler_never_propagates(self, env, monkeypatch):
        from schemas import CateringLead, Config
        from safe_io import load_yaml_model
        import catering_followups
        mod = self._helper(monkeypatch, env)
        cfg = load_yaml_model(Path(os.environ["SHIFT_AGENT_CONFIG_PATH"]), Config)

        def _boom(*a, **k):
            raise RuntimeError("store on fire")

        monkeypatch.setattr(catering_followups, "schedule_followup", _boom)
        mod._schedule_followups_after_quote_sent(
            cfg=cfg, lead=CateringLead.model_validate(_lead()),
            now=datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc))
        assert "health_check_failure" in _types(env)

