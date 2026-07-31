"""M5 — the follow-up scheduling / dedup / suppression kernel (PURE).

Windows-runnable: nothing here spawns a subprocess. The kernel is the ONE place
that decides whether a follow-up happens, so the suppression matrix below is
exhaustive by construction — every reason `suppression_check` can return gets a
cell, and the boundary cases (quiet-hours edges, the cap's off-by-one) get their
own.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import catering_followups as cf  # noqa: E402
from schemas import (  # noqa: E402
    CateringFollowup, CateringFollowupConfig, CateringFollowupStore, CateringLead,
)

NOW = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)


# ── builders ─────────────────────────────────────────────────────────────────
def _lead(**over) -> CateringLead:
    data = {
        "lead_id": "L0001", "status": "SENT_TO_CUSTOMER",
        "customer_phone": "+15550100777", "customer_name": "Asha",
        "raw_inquiry": "catering for 40", "original_message_id": "m1",
        "created_at": NOW, "updated_at": NOW, "quote_text": "a quote",
        "extracted": {"headcount": 40, "event_date": "2026-09-01"},
    }
    data.update(over)
    return CateringLead.model_validate(data)


def _followup(**over) -> CateringFollowup:
    data = {
        "followup_id": "FU0009", "lead_id": "L0001",
        "followup_type": "proposal_unanswered", "due_at": NOW,
        "created_at": NOW - timedelta(days=2), "created_by": "system",
        "status": "scheduled",
        "message_template_key": "catering_followup_proposal_unanswered",
    }
    data.update(over)
    return CateringFollowup.model_validate(data)


def _prior(n: int, *, status="approved_sent", last=None, created_by="system"):
    """`n` follow-ups that already consumed attention on this lead."""
    return [
        _followup(followup_id=f"FU{i:04d}", status=status, created_by=created_by,
                  last_attempt_at=last or (NOW - timedelta(days=10)))
        for i in range(1, n + 1)
    ]


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    p = tmp_path / "state" / "catering-followups.json"
    p.parent.mkdir(parents=True)
    monkeypatch.setenv("SHIFT_AGENT_FOLLOWUPS_PATH", str(p))
    return p


# ── due-time computation ─────────────────────────────────────────────────────
class TestComputeDue:
    def test_relative_types_offset_from_now(self):
        assert cf.compute_due("proposal_unanswered", now=NOW) == NOW + timedelta(hours=48)
        assert cf.compute_due("incomplete_qualification", now=NOW) == NOW + timedelta(hours=24)

    def test_event_anchored_types_offset_from_the_event(self):
        due = cf.compute_due("event_approaching", now=NOW, event_date="2026-09-01")
        assert due == datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
        due = cf.compute_due("final_headcount_due", now=NOW, event_date="2026-09-01")
        assert due == datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
        due = cf.compute_due("post_event_feedback", now=NOW, event_date="2026-09-01")
        assert due == datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)

    def test_event_anchored_without_a_date_is_not_scheduled(self):
        """No fallback: a nudge placed against a guessed date is worse than none."""
        for t in sorted(cf.EVENT_ANCHORED_TYPES):
            assert cf.compute_due(t, now=NOW, event_date=None) is None
            assert cf.compute_due(t, now=NOW, event_date="not-a-date") is None

    def test_event_anchored_due_time_already_past_is_not_scheduled(self):
        """A lead booked five days out has no 7-days-before moment."""
        assert cf.compute_due("event_approaching", now=NOW, event_date="2026-08-05") is None
        # ...but the 3-day one still has room.
        assert cf.compute_due("final_headcount_due", now=NOW, event_date="2026-08-05") is not None

    def test_unknown_type_has_no_due_time(self):
        assert cf.compute_due("nope", now=NOW) is None


# ── scheduling + dedup ───────────────────────────────────────────────────────
class TestSchedule:
    def test_writes_a_scheduled_record(self, store_path):
        res = cf.schedule_followup("L0001", "proposal_unanswered", now=NOW)
        assert res.ok and res.followup is not None
        assert res.followup.followup_id == "FU0001"
        assert res.followup.status == "scheduled"
        assert res.followup.message_template_key == "catering_followup_proposal_unanswered"
        doc = json.loads(store_path.read_text(encoding="utf-8"))
        assert [f["followup_id"] for f in doc["followups"]] == ["FU0001"]
        assert doc["next_sequence"] == 2

    def test_ids_increment_and_never_reuse(self, store_path):
        cf.schedule_followup("L0001", "proposal_unanswered", now=NOW)
        res = cf.schedule_followup("L0002", "proposal_unanswered", now=NOW)
        assert res.followup.followup_id == "FU0002"

    def test_id_floor_survives_a_hand_edited_counter(self, store_path):
        store_path.write_text(json.dumps({
            "schema_version": 1, "next_sequence": 1,
            "followups": [_followup(followup_id="FU0042").model_dump(mode="json")],
        }), encoding="utf-8")
        res = cf.schedule_followup("L0002", "proposal_unanswered", now=NOW)
        assert res.followup.followup_id == "FU0043"

    def test_same_lead_type_and_day_is_refused(self, store_path):
        assert cf.schedule_followup("L0001", "proposal_unanswered", now=NOW).ok
        res = cf.schedule_followup("L0001", "proposal_unanswered",
                                   now=NOW + timedelta(minutes=30))
        assert not res.ok and res.reason == "duplicate"
        assert len(cf.load_store(store_path)[0].followups) == 1

    def test_different_type_same_day_is_allowed(self, store_path):
        assert cf.schedule_followup("L0001", "proposal_unanswered", now=NOW).ok
        assert cf.schedule_followup("L0001", "incomplete_qualification", now=NOW).ok

    @pytest.mark.parametrize("blocking_status", ["suppressed", "cancelled", "expired"])
    def test_a_terminal_record_still_blocks_a_duplicate(self, store_path, blocking_status):
        """Suppression is a decision about this (lead, reason, day). Re-scheduling
        it would be the reschedule-until-it-passes behaviour the design forbids."""
        first = cf.schedule_followup("L0001", "proposal_unanswered", now=NOW).followup
        store, _ = cf.load_store(store_path)
        store.followups[0] = first.model_copy(update={"status": blocking_status})
        cf.save_store(store, store_path)
        res = cf.schedule_followup("L0001", "proposal_unanswered", now=NOW)
        assert not res.ok and res.reason == "duplicate"

    def test_owner_created_overrides_the_dedup(self, store_path):
        cf.schedule_followup("L0001", "proposal_unanswered", now=NOW)
        res = cf.schedule_followup("L0001", "proposal_unanswered", now=NOW,
                                   created_by="owner")
        assert res.ok and res.followup.created_by == "owner"
        assert len(cf.load_store(store_path)[0].followups) == 2

    def test_unschedulable_type_reports_why(self, store_path):
        res = cf.schedule_followup("L0001", "event_approaching", now=NOW)
        assert not res.ok and res.reason == "no_due_time"
        res = cf.schedule_followup("L0001", "not_a_type", now=NOW)
        assert not res.ok and res.reason == "unknown_type"

    def test_unreadable_store_refuses_rather_than_overwriting(self, store_path):
        store_path.write_text("{not json", encoding="utf-8")
        res = cf.schedule_followup("L0001", "proposal_unanswered", now=NOW)
        assert not res.ok and res.reason == "store_unreadable"


class TestScheduleBestEffort:
    def test_disabled_config_writes_nothing(self, store_path):
        assert cf.schedule_best_effort("L0001", "proposal_unanswered",
                                       now=NOW, enabled=False) is None
        assert not store_path.exists()

    def test_enabled_writes_the_record_and_an_audit_row(self, store_path, tmp_path,
                                                        monkeypatch):
        log = tmp_path / "logs" / "decisions.log"
        monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(log))
        f = cf.schedule_best_effort("L0001", "proposal_unanswered", now=NOW,
                                    enabled=True, trigger="quote_sent")
        assert f is not None
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert [r["type"] for r in rows] == ["catering_followup_scheduled"]
        assert rows[0]["trigger"] == "quote_sent"
        assert rows[0]["followup_id"] == f.followup_id

    def test_a_duplicate_is_a_quiet_no_op(self, store_path, tmp_path, monkeypatch):
        log = tmp_path / "logs" / "decisions.log"
        monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(log))
        cf.schedule_best_effort("L0001", "proposal_unanswered", now=NOW, enabled=True)
        assert cf.schedule_best_effort("L0001", "proposal_unanswered",
                                       now=NOW, enabled=True) is None
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) == 1

    def test_a_raising_scheduler_is_swallowed_and_audited(self, store_path, tmp_path,
                                                          monkeypatch):
        """The host send path must survive any follow-up failure."""
        log = tmp_path / "logs" / "decisions.log"
        monkeypatch.setenv("SHIFT_AGENT_LOG_PATH", str(log))

        def _boom(*a, **k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(cf, "schedule_followup", _boom)
        assert cf.schedule_best_effort("L0001", "proposal_unanswered",
                                       now=NOW, enabled=True) is None
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert [r["type"] for r in rows] == ["health_check_failure"]
        assert rows[0]["check"] == "catering_followup_schedule"
        assert "disk on fire" in rows[0]["detail"]


# ── suppression matrix ───────────────────────────────────────────────────────
CFG = CateringFollowupConfig()


def _check(lead=None, followup=None, mode=None, now=NOW, **kw):
    return cf.suppression_check(
        _lead() if lead is None else lead,
        followup or _followup(),
        mode, now, cfg=kw.pop("cfg", CFG), **kw,
    )


class TestSuppressionMatrix:
    def test_a_clean_lead_proceeds(self):
        assert _check() == (False, "")

    def test_kill_switch(self):
        assert _check(kill_switch_engaged=True) == (True, "kill_switch")

    def test_kill_switch_outranks_every_other_reason(self):
        """The most total gate wins, so the recorded reason is the true one."""
        assert _check(lead=_lead(on_hold=True), mode="opted_out",
                      kill_switch_engaged=True) == (True, "kill_switch")

    @pytest.mark.parametrize("mode", ["paused", "opted_out", "takeover"])
    def test_suppressing_automation_modes(self, mode):
        assert _check(mode=mode) == (True, "automation_suppressed")

    @pytest.mark.parametrize("mode", [None, "active"])
    def test_non_suppressing_automation_modes_proceed(self, mode):
        assert _check(mode=mode) == (False, "")

    def test_lead_missing(self):
        """No lead, no follow-up: this is the no-cold-outreach invariant."""
        assert cf.suppression_check(None, _followup(), None, NOW, cfg=CFG) == (
            True, "lead_missing")

    def test_lead_on_hold(self):
        assert _check(lead=_lead(on_hold=True)) == (True, "lead_on_hold")

    @pytest.mark.parametrize("status", ["NOT_CATERING", "OWNER_REJECTED", "CLOSED", "STALE"])
    def test_terminal_lead_statuses(self, status):
        lead = _lead(status=status, quote_text="q")
        assert _check(lead=lead) == (True, "lead_status_not_allowed")

    @pytest.mark.parametrize("status", ["NEW", "EXTRACTING"])
    def test_pre_qualification_statuses_are_not_chased(self, status):
        assert _check(lead=_lead(status=status, quote_text="")) == (
            True, "lead_status_not_allowed")

    def test_an_unknown_future_status_suppresses_by_default(self):
        """The allowlist is defensive: a status M3 adds must not become
        silently chase-able just because nobody updated a denylist."""
        assert cf.suppression_check(
            {"status": "SOME_M3_STATUS", "lead_id": "L0001"},
            _followup(), None, NOW, cfg=CFG,
        ) == (True, "lead_status_not_allowed")

    @pytest.mark.parametrize("status", sorted(cf.FOLLOWUP_ALLOWED_LEAD_STATUSES))
    def test_every_allowed_status_proceeds(self, status):
        lead = _lead(status=status, quote_text="q")
        assert _check(lead=lead) == (False, "")

    def test_closed_is_allowed_only_for_post_event_feedback(self):
        closed = _lead(status="CLOSED", quote_text="q")
        assert _check(lead=closed) == (True, "lead_status_not_allowed")
        feedback = _followup(followup_type="post_event_feedback",
                             message_template_key="catering_followup_post_event_feedback")
        assert _check(lead=closed, followup=feedback) == (False, "")

    def test_customer_declined_read_defensively(self):
        """Written pre-M3 as a defensive getattr; M3 landed customer_acceptance
        on CateringLead, so the gate is now live on the real model too."""
        assert hasattr(_lead(), "customer_acceptance")
        declined = _lead(status="SENT_TO_CUSTOMER", quote_text="q")
        declined = declined.model_copy(update={"customer_acceptance": "declined"})
        assert _check(lead=declined) == (True, "customer_declined")
        assert cf.suppression_check(
            {"status": "SENT_TO_CUSTOMER", "customer_acceptance": "declined"},
            _followup(), None, NOW, cfg=CFG,
        ) == (True, "customer_declined")
        assert cf.suppression_check(
            {"status": "SENT_TO_CUSTOMER", "customer_acceptance": "accepted"},
            _followup(), None, NOW, cfg=CFG,
        ) == (False, "")

    def test_frequency_cap_at_the_lifetime_limit(self):
        assert _check(lead_followups=_prior(3)) == (True, "frequency_cap")

    def test_one_below_the_cap_proceeds(self):
        assert _check(lead_followups=_prior(2)) == (False, "")

    @pytest.mark.parametrize("status", ["suppressed", "cancelled", "expired", "scheduled"])
    def test_records_that_reached_nobody_do_not_consume_the_budget(self, status):
        assert _check(lead_followups=_prior(5, status=status)) == (False, "")

    def test_owner_created_followups_are_never_capped(self):
        owner = _followup(created_by="owner")
        assert _check(followup=owner, lead_followups=_prior(9)) == (False, "")

    def test_min_interval_since_the_previous_followup(self):
        recent = _prior(1, last=NOW - timedelta(hours=23))
        assert _check(lead_followups=recent) == (True, "min_interval")

    def test_just_past_the_min_interval_proceeds(self):
        assert _check(lead_followups=_prior(1, last=NOW - timedelta(hours=24))) == (False, "")

    def test_the_candidate_does_not_count_against_itself(self):
        me = _followup(followup_id="FU0009", status="awaiting_owner_approval",
                       last_attempt_at=NOW)
        assert cf.suppression_check(_lead(), me, None, NOW, cfg=CFG,
                                    lead_followups=[me]) == (False, "")

    @pytest.mark.parametrize("hour,minute,expected", [
        (20, 59, False),  # last minute before the window
        (21, 0, True),    # the window opens ON quiet_hours_start
        (8, 59, True),    # last minute inside the window
        (9, 0, False),    # the window closes ON quiet_hours_end
    ])
    def test_quiet_hours_boundaries(self, hour, minute, expected):
        suppressed, reason = _check(now=NOW.replace(hour=hour, minute=minute))
        assert suppressed is expected
        assert reason == ("quiet_hours" if expected else "")

    def test_automation_suppression_outranks_quiet_hours(self):
        """An opted-out customer during quiet hours records the opt-out — the fact
        that matters — not the incidental clock."""
        assert _check(mode="opted_out", now=NOW.replace(hour=22)) == (
            True, "automation_suppressed")


class TestQuietHoursWindow:
    def test_wrapping_window(self):
        assert [cf.in_quiet_hours(h, 21, 9) for h in (20, 21, 23, 0, 8, 9, 12)] == [
            False, True, True, True, True, False, False]

    def test_non_wrapping_window(self):
        assert [cf.in_quiet_hours(h, 9, 17) for h in (8, 9, 16, 17, 18)] == [
            False, True, True, False, False]

    def test_equal_bounds_disable_the_check(self):
        """A misconfiguration must not mute the agent forever."""
        assert all(not cf.in_quiet_hours(h, 0, 0) for h in range(24))


# ── selection helpers ────────────────────────────────────────────────────────
class TestSelection:
    def test_due_followups_are_oldest_first(self):
        store = CateringFollowupStore(followups=[
            _followup(followup_id="FU0002", due_at=NOW - timedelta(hours=1)),
            _followup(followup_id="FU0001", due_at=NOW - timedelta(days=3)),
            _followup(followup_id="FU0003", due_at=NOW + timedelta(days=1)),
            _followup(followup_id="FU0004", due_at=NOW - timedelta(days=1),
                      status="awaiting_owner_approval"),
        ])
        assert [f.followup_id for f in cf.due_followups(store, NOW)] == ["FU0001", "FU0002"]

    def test_card_expiry_needs_a_stamped_attempt(self):
        carded = _followup(status="awaiting_owner_approval",
                           last_attempt_at=NOW - timedelta(hours=5))
        assert cf.card_expired(carded, NOW)
        fresh = carded.model_copy(update={"last_attempt_at": NOW - timedelta(hours=3)})
        assert not cf.card_expired(fresh, NOW)
        unstamped = carded.model_copy(update={"last_attempt_at": None})
        assert not cf.card_expired(unstamped, NOW)
        assert not cf.card_expired(_followup(), NOW)  # still scheduled

    def test_by_code_matches_only_carded_or_sent(self):
        store = CateringFollowupStore(followups=[
            _followup(followup_id="FU0001", approval_code="#AAAAA", status="cancelled"),
            _followup(followup_id="FU0002", approval_code="#BBBBB",
                      status="awaiting_owner_approval"),
            _followup(followup_id="FU0003", approval_code="#CCCCC", status="approved_sent"),
        ])
        assert cf.by_code(store, "#AAAAA") is None
        assert cf.by_code(store, "#BBBBB").followup_id == "FU0002"
        assert cf.by_code(store, "#CCCCC").followup_id == "FU0003"

    def test_live_codes_excludes_terminal_records(self, store_path):
        store = CateringFollowupStore(followups=[
            _followup(followup_id="FU0001", approval_code="#AAAAA", status="scheduled"),
            _followup(followup_id="FU0002", approval_code="#BBBBB",
                      status="awaiting_owner_approval"),
            _followup(followup_id="FU0003", approval_code="#CCCCC", status="approved_sent"),
            _followup(followup_id="FU0004", approval_code="#DDDDD", status="suppressed"),
        ])
        cf.save_store(store, store_path)
        assert cf.live_codes(store_path) == {"#AAAAA", "#BBBBB"}

    def test_live_codes_never_raises_into_a_mint_path(self, store_path):
        store_path.write_text("{not json", encoding="utf-8")
        assert cf.live_codes(store_path) == set()


# ── store round-trip / backward compatibility ────────────────────────────────
class TestStore:
    def test_round_trip(self, store_path):
        original = CateringFollowupStore(followups=[
            _followup(approval_code="#AAAAA", rendered_message="hello",
                      status="awaiting_owner_approval", attempt_count=1,
                      last_attempt_at=NOW),
        ])
        cf.save_store(original, store_path)
        loaded, status = cf.load_store(store_path)
        assert status == "ok"
        assert loaded.model_dump(mode="json") == original.model_dump(mode="json")

    def test_a_missing_store_reads_as_empty(self, store_path):
        loaded, status = cf.load_store(store_path)
        assert status == "missing" and loaded.followups == []

    def test_unknown_top_level_keys_round_trip_without_raising(self, store_path):
        """extra='ignore', mirroring CateringLeadStore: a store written by a newer
        binary must decode on an older one rather than crashing a rollback."""
        store_path.write_text(json.dumps({
            "schema_version": 2, "next_sequence": 1, "followups": [],
            "some_future_field": {"a": 1},
        }), encoding="utf-8")
        loaded, status = cf.load_store(store_path)
        assert status == "ok" and loaded.schema_version == 2

    def test_env_override_reaches_path_resolution(self, tmp_path, monkeypatch):
        target = tmp_path / "elsewhere.json"
        monkeypatch.setenv("SHIFT_AGENT_FOLLOWUPS_PATH", str(target))
        assert cf.followups_path() == target
        assert cf.followups_lock() == Path(str(target) + ".lock")

    def test_default_path_is_the_canonical_constant(self, monkeypatch):
        import catering_paths
        monkeypatch.delenv("SHIFT_AGENT_FOLLOWUPS_PATH", raising=False)
        assert cf.followups_path() == catering_paths.CATERING_FOLLOWUPS_PATH


# ── templates ────────────────────────────────────────────────────────────────
TEMPLATE_DIR = REPO / "src" / "agents" / "catering" / "templates"


class TestTemplates:
    def test_every_type_has_a_template_file(self):
        for followup_type, key in cf.TEMPLATE_KEYS.items():
            assert (TEMPLATE_DIR / f"{key}.txt").is_file(), followup_type

    @pytest.mark.parametrize("key", sorted(cf.TEMPLATE_KEYS.values()))
    def test_customer_templates_name_no_price(self, key):
        """A nudge that quotes a number is a quote, and quotes go through the
        owner-approval path that already exists."""
        text = (TEMPLATE_DIR / f"{key}.txt").read_text(encoding="utf-8")
        assert "$" not in text and "₹" not in text
        for word in ("price", "prices", "pricing", "total", "deposit", "cost"):
            assert word not in text.lower(), f"{key} mentions {word}"

    @pytest.mark.parametrize("key", sorted(cf.TEMPLATE_KEYS.values()))
    def test_customer_templates_make_no_completion_claim(self, key):
        """Same wall the regulated-send lint enforces at the chokepoint — assert it
        here so a template edit fails a test instead of a live send."""
        sys.path.insert(0, str(REPO / "src"))
        from agents.flyer.customer_copy_policy import lint_no_unverified_completion
        text = (TEMPLATE_DIR / f"{key}.txt").read_text(encoding="utf-8")
        assert not lint_no_unverified_completion(text).hits

    @pytest.mark.parametrize("key", sorted(cf.TEMPLATE_KEYS.values()))
    def test_customer_templates_carry_no_transport_prefix(self, key):
        """The bridge prefix is prepended at send time so the owner card can embed
        the body without stacking a second header inside itself."""
        text = (TEMPLATE_DIR / f"{key}.txt").read_text(encoding="utf-8")
        assert not text.startswith("⚕")
        assert cf.with_bridge_prefix(text).startswith(cf.BRIDGE_TEMPLATE_PREFIX)

    @pytest.mark.parametrize("key", sorted(cf.TEMPLATE_KEYS.values()))
    def test_customer_templates_render_with_the_documented_fields(self, key):
        text = (TEMPLATE_DIR / f"{key}.txt").read_text(encoding="utf-8")
        rendered = text.format(customer_name="Asha", lead_id="L0001",
                               event_date="2026-09-01", headcount=40,
                               missing_fields="venue, service style",
                               note_line=cf.note_line("a note"))
        assert "{" not in rendered


class TestOwnerNote:
    def test_a_plain_note_survives_intact(self):
        assert cf.normalize_note("Ask about the Sept 14 date.") == (
            "Ask about the Sept 14 date.")

    def test_newlines_and_runs_of_space_collapse(self):
        """A note is one line inside a fixed template."""
        assert cf.normalize_note("first\nsecond\t\tthird   fourth") == (
            "first second third fourth")

    def test_markdown_delimiters_are_stripped(self):
        """The note must not restyle the message around it."""
        assert cf.normalize_note("*bold* _it_ ~s~ `c`") == "bold it s c"

    def test_control_and_format_characters_are_dropped(self):
        assert cf.normalize_note("hi‮gnimoc​ there") == "hignimoc there"

    def test_length_is_capped(self):
        assert len(cf.normalize_note("x" * 500)) == cf.NOTE_MAX_CHARS

    def test_empty_and_none_render_no_paragraph(self):
        assert cf.note_line(None) == ""
        assert cf.note_line("") == ""
        assert cf.note_line("   ") == ""

    def test_a_present_note_renders_as_its_own_paragraph(self):
        assert cf.note_line("check the date") == "\ncheck the date\n"

    def test_the_note_is_normalised_on_the_way_into_the_store(self, store_path):
        res = cf.schedule_followup("L0001", "owner_reminder", now=NOW,
                                   due_at=NOW + timedelta(days=1),
                                   created_by="owner", note="  *ask*\nagain  ")
        assert res.followup.note == "ask again"

    def test_no_note_stores_none_not_empty_string(self, store_path):
        res = cf.schedule_followup("L0001", "owner_reminder", now=NOW,
                                   due_at=NOW + timedelta(days=1),
                                   created_by="owner", note="")
        assert res.followup.note is None

    def test_a_followup_written_before_the_note_field_still_decodes(self, store_path):
        """Additive + default None: a rollback must not strand the store."""
        legacy = _followup().model_dump(mode="json")
        legacy.pop("note")
        store_path.write_text(json.dumps({
            "schema_version": 1, "next_sequence": 2, "followups": [legacy],
        }), encoding="utf-8")
        loaded, status = cf.load_store(store_path)
        assert status == "ok" and loaded.followups[0].note is None
