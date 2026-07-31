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
AMEND = SCRIPTS / "amend-catering-lead"

OWNER_JID = "19045550100@s.whatsapp.net"
CUSTOMER_JID = "15550100777@s.whatsapp.net"

# PRE-EXISTING DEFECT, surfaced by the M5 guard below and deliberately NOT fixed
# here — neither script is M5's, and one of them is on a money path.
#
# Both alias the send chokepoint (`from safe_io import bridge_post_2tuple as
# _bridge_post`) without an entry in SAFE_IO_NULL_CONTEXT_ALLOWLIST, so at
# runtime every send they make is refused with `missing_action_context`:
#   amend-catering-lead    — M1's slot-filling loop: every customer question
#                            batch and every owner hand-off card.
#   catering-mint-deposit  — the slice-2 deposit-link customer send.
# The PR-ζ AST static gate cannot see either callsite (it matches call NAMES,
# and both call through an alias), which is why they have gone unnoticed.
#
# Recorded as a pinned set rather than dropped from the guard so the finding
# stays visible: a NEW name appearing here fails the test, and closing either
# gap also fails it (prompting the set to shrink).
KNOWN_UNALLOWLISTED_CATERING_SENDERS = {
    "amend-catering-lead",
    "catering-mint-deposit",
}


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


def _run(env, monkeypatch, script: Path, name: str, *argv, bridge_ok=True):
    """Load a script fresh, stub its bridge, run main(); returns (rc, sends)."""
    mod = load_script(name, script)
    sends: list = []

    def _fake_post(jid, message):
        sends.append((jid, message))
        return (True, "wamid.OK") if bridge_ok else (False, "connect_failed")

    monkeypatch.setattr(mod, "_bridge_post", _fake_post)
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
        stamped = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
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
        assert _store(env)[0]["status"] == "approved_sent"
        assert [r["type"] for r in _rows(env)] == ["catering_followup_sent"]


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

    @pytest.mark.parametrize("basename", ["catering-followup-sweep",
                                          "approve-catering-followup"])
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
        missing = []
        for script in sorted(SCRIPTS.iterdir()):
            if not script.is_file():
                continue
            text = script.read_text(encoding="utf-8", errors="ignore")
            if not alias_import.search(text):
                continue
            if script.name not in safe_io.SAFE_IO_NULL_CONTEXT_ALLOWLIST:
                missing.append(script.name)
        assert set(missing) == KNOWN_UNALLOWLISTED_CATERING_SENDERS, (
            f"the set of catering scripts that alias the send chokepoint without "
            f"an allowlist entry changed.\n"
            f"  now missing : {sorted(missing)}\n"
            f"  known gap   : {sorted(KNOWN_UNALLOWLISTED_CATERING_SENDERS)}\n"
            f"A NEW name here ships refusing every send it makes — add it to "
            f"SAFE_IO_NULL_CONTEXT_ALLOWLIST. A name that DISAPPEARED means the "
            f"pre-existing gap was closed; shrink the known-gap set."
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
        assert _store(env)[0]["status"] == "awaiting_owner_approval"
        assert "catering_followup_sent" not in _types(env)


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

