"""M1 — amend-catering-lead: amendment application + slot-filling answer loop.

Runs the script's real main() through argparse with the deployed path constants
repointed at a per-test sandbox and the bridge captured — the same in-process
patched-module pattern tests/test_catering_turn_arbitration_e2e.py uses, which
exercises argv parsing, exit codes, state mutation, audit rows and sends without
a subprocess. The fcntl stub (fixtures_fleet) makes safe_io importable on the
Windows dev box; advisory locking is irrelevant to single-process correctness, and
the POSIX filesystem-contract enforcement in the amendment sidecar is already
pinned by tests/test_catering_amendment_capture.py.

What is pinned here:
  * an amendment materialises onto the lead (the R2A sidecar stops being write-only)
  * re-running is a no-op — records carry an applied_at stamp
  * "no unapplied amendments" is a clean exit, not an error
  * a quote-ledger version is appended ONLY when a committed quote already exists
  * the answer loop fills nulls, never overwrites, caps rounds, and hands to the
    owner with the residual gaps flagged
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fixtures_fleet import ensure_fcntl_stub, load_script

ensure_fcntl_stub()  # before any safe_io / schemas import

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
SCRIPTS = SRC / "agents" / "catering" / "scripts"
for _p in (SRC, SRC / "platform"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import catering_amendments as ca  # noqa: E402
import catering_extraction as cx  # noqa: E402
import catering_qualification as cq  # noqa: E402
import catering_quote_ledger as ql  # noqa: E402

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ids():
    """Owner/group for the sidecar filesystem contract. The tmp dir and every file
    under it are created by THIS process, so the running uid/gid match."""
    if os.name == "posix":
        import grp
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


OWNER, GROUP = _ids()


class Sandbox:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.monkeypatch = monkeypatch
        self.root = tmp_path
        self.state = tmp_path / "state"
        self.logs = tmp_path / "logs"
        self.state.mkdir()
        self.logs.mkdir()
        self.leads = self.state / "catering-leads.json"
        self.amendments = self.state / "catering-amendments.json"
        self.ledger = self.state / "catering-quote-ledger.json"
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
        self.pages: list = []

    def write_lead(self, **overrides) -> dict:
        lead = {
            "lead_id": "L0001", "status": "AWAITING_OWNER_APPROVAL",
            "customer_phone": "+19045550199", "raw_inquiry": "need catering for 235",
            "original_message_id": "msg_1",
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
            "quote_text": "quote v1", "owner_approval_code": "#A3F2X",
            "extracted": {"headcount": 235, "event_date": "2026-09-15"},
        }
        lead.update(overrides)
        self.leads.write_text(json.dumps({"schema_version": 1, "leads": [lead]}), encoding="utf-8")
        return lead

    def read_lead(self, lead_id="L0001") -> dict:
        store = json.loads(self.leads.read_text(encoding="utf-8"))
        return next(l for l in store["leads"] if l["lead_id"] == lead_id)

    def audit_rows(self, entry_type=None) -> list[dict]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if entry_type is None or r.get("type") == entry_type]

    def capture(self, text, *, message_id, lead=None):
        """Put a real R2A record in the sidecar — the write side under test is the
        deployed capture path, not a hand-rolled fixture."""
        return ca.capture_branch_b_amendment(
            lead=lead or {"lead_id": "L0001", "extracted": {"headcount": 235}},
            text=text, chat_id="cust@lid", phone="+19045550199",
            message_id=message_id, source_transport="whatsapp",
            provider_timestamp="1722513600", now=NOW,
            data_path=self.amendments, expected_owner=OWNER, expected_group=GROUP,
            emit_audit=False,
        )


@pytest.fixture
def sb(tmp_path, monkeypatch):
    box = Sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("SHIFT_AGENT_CATERING_AMENDMENTS_PATH", str(box.amendments))
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_PATH", str(box.ledger))
    # The ledger's POSIX fs-owner contract defaults to shift-agent:shift-agent (the
    # deployed state-dir owner). The script reaches append_version without an
    # explicit expected_owner, so the tmp sandbox — owned by whoever runs the tests —
    # needs the same override test_catering_finalize_menu uses, or the append is
    # refused `parent_bad_owner` and swallowed as a non-fatal WARN. Inert on Windows,
    # where _validate_fs skips ownership entirely.
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_OWNER", OWNER)
    monkeypatch.setenv("SHIFT_AGENT_CATERING_QUOTE_LEDGER_GROUP", GROUP)
    monkeypatch.setenv("SHIFT_AGENT_CONFIG_PATH", str(box.config))
    return box


def _run(sb: Sandbox, argv, *, bridge_ok=True, stdin_text=None,
         bridge_status="connect_failed"):
    mod = load_script("amend_lead_under_test", SCRIPTS / "amend-catering-lead")
    mod.CONFIG_PATH = sb.config
    mod.LEADS_PATH = sb.leads
    mod.LEADS_LOCK = Path(str(sb.leads) + ".lock")
    mod.LOG_PATH = sb.log
    mod.AMENDMENTS_PATH = sb.amendments
    mod.LEDGER_PATH = sb.ledger

    # P17b: the script consumes the CANONICAL 4-tuple, so the stub must be the
    # 4-tuple one. Both seams are bound through monkeypatch.setattr with the
    # default raising=True: a bare assignment to a stale name (`_bridge_post`)
    # would create a DEAD attribute and the script would issue a REAL bridge POST,
    # whereas this fails test SETUP the moment the production name moves.
    def _bridge(jid, message, **_kwargs):
        sb.sends.append({"jid": jid, "message": message})
        return ((True, "wamid.OUT", "", "sent") if bridge_ok
                else (False, "", "connection refused", bridge_status))
    sb.monkeypatch.setattr(mod, "_bridge_post_4tuple", _bridge)

    def _notify(cmd, **_kwargs):
        sb.pages.append({"argv": [str(part) for part in cmd]})
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    sb.monkeypatch.setattr(mod, "subprocess", SimpleNamespace(run=_notify))

    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv = argv
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = mod.main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin
    stdout = out.getvalue().strip()
    payload = json.loads(stdout.splitlines()[-1]) if stdout else {}
    return rc, payload, err.getvalue()


def _amend(sb, **kw):
    argv = ["amend-catering-lead", "--lead-id", kw.pop("lead_id", "L0001"),
            "--mode", "amendment",
            "--expected-owner", OWNER, "--expected-group", GROUP]
    if kw.get("message_id"):
        argv += ["--message-id", kw["message_id"]]
    return _run(sb, argv, bridge_ok=kw.get("bridge_ok", True),
                bridge_status=kw.get("bridge_status", "connect_failed"))


def _answer(sb, text, *, lead_id="L0001", message_id="wamid.ANS", bridge_ok=True,
            bridge_status="connect_failed"):
    return _run(sb, ["amend-catering-lead", "--lead-id", lead_id, "--mode", "answer",
                     "--answer-text", text, "--message-id", message_id],
                bridge_ok=bridge_ok, bridge_status=bridge_status)


def _answer_via_stdin(sb, text, *, lead_id="L0001", message_id="wamid.ANS"):
    """The path the router and the cockpit actually use — the text never touches
    argv, so a reply that starts with a dash cannot be read as a flag."""
    return _run(sb, ["amend-catering-lead", "--lead-id", lead_id, "--mode", "answer",
                     "--answer-text-stdin", "--message-id", message_id],
                stdin_text=text)


# ════════════════════════════════════════════════════════════════════════════
# Amendment mode
# ════════════════════════════════════════════════════════════════════════════
def test_amendment_is_applied_to_the_lead(sb):
    """THE gap this script closes: before M1 the correction was durably captured and
    the owner still approved the pre-amendment number."""
    sb.write_lead()
    cap = sb.capture("actually make it 280 people not 235", message_id="wamid.A1")
    assert cap.ok

    rc, payload, _err = _amend(sb, message_id="wamid.A1")

    assert rc == 0
    assert payload["applied"] == [cap.amendment_id]
    assert payload["fields_changed"] == ["headcount"]
    assert sb.read_lead()["extracted"]["headcount"] == 280


def test_amendment_overwrites_only_the_fields_it_mentions(sb):
    sb.write_lead(extracted={"headcount": 235, "event_date": "2026-09-15",
                             "venue": "Old Hall", "service_style": "buffet"})
    sb.capture("change it to 280 guests", message_id="wamid.A1")

    rc, payload, _err = _amend(sb)

    assert rc == 0
    extracted = sb.read_lead()["extracted"]
    assert extracted["headcount"] == 280
    assert extracted["venue"] == "Old Hall"          # untouched
    assert extracted["service_style"] == "buffet"    # untouched
    assert extracted["event_date"] == "2026-09-15"   # untouched


def test_amendment_moves_date_and_service_style(sb):
    sb.write_lead(extracted={"headcount": 235, "event_date": "2026-09-15",
                             "service_style": "buffet"})
    sb.capture("move the date to October 12 and make it plated instead",
               message_id="wamid.A1")

    rc, payload, _err = _amend(sb)

    assert rc == 0
    extracted = sb.read_lead()["extracted"]
    assert extracted["event_date"] == "2026-10-12"
    assert extracted["service_style"] == "plated"
    assert set(payload["fields_changed"]) == {"event_date", "service_style"}


def test_reapply_is_idempotent(sb):
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")
    first_rc, first, _e = _amend(sb)
    assert first_rc == 0 and first["applied"]

    second_rc, second, _e2 = _amend(sb)

    assert second_rc == 0
    assert second["applied"] == []
    assert second["reason"] == "no_unapplied_amendments"
    assert sb.read_lead()["extracted"]["headcount"] == 280  # still the amended value
    assert len(sb.sends) == 1, "no second owner card for an already-applied amendment"


def test_applied_records_are_stamped_without_mutating_the_captured_text(sb):
    """Append-only contract: applied_at is ADDITIVE; raw_text and the R2A status
    field are left exactly as captured."""
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")
    before = json.loads(sb.amendments.read_text(encoding="utf-8"))["records"][0]

    _amend(sb)

    after = json.loads(sb.amendments.read_text(encoding="utf-8"))["records"][0]
    assert after["applied_at"]
    assert after["raw_text"] == before["raw_text"]
    assert after["status"] == before["status"] == "captured"
    assert after["raw_text_sha256"] == before["raw_text_sha256"]
    assert ca.unapplied_for_lead("L0001", data_path=sb.amendments) == []


def test_note_only_amendment_is_persisted_without_a_fresh_owner_card(sb):
    """An amendment that only adds a note must still be SAVED — marking it applied
    while dropping its content is silent data loss — but it does not warrant a new
    owner card or a new quote version."""
    sb.write_lead()
    sb.capture("could you send two sample menus", message_id="wamid.A1")

    rc, payload, _err = _amend(sb)

    assert rc == 0
    assert payload["fields_changed"] == []
    assert "requested 2 sample menu combinations" in sb.read_lead()["extracted"]["notes"]
    assert sb.sends == [], "a note-only amendment does not re-card the owner"


def test_no_amendments_is_a_clean_exit(sb):
    sb.write_lead()
    rc, payload, _err = _amend(sb)
    assert rc == 0
    assert payload["applied"] == []
    assert payload["reason"] == "no_unapplied_amendments"
    assert sb.sends == []


def test_multiple_amendments_apply_in_capture_order_last_one_wins(sb):
    sb.write_lead()
    sb.capture("make it 280 people", message_id="wamid.A1")
    sb.capture("actually 300 people", message_id="wamid.A2")

    rc, payload, _err = _amend(sb)

    assert rc == 0
    assert len(payload["applied"]) == 2
    assert sb.read_lead()["extracted"]["headcount"] == 300


def test_ledger_version_is_appended_when_a_committed_quote_exists(sb):
    sb.write_lead()
    ql.append_version(
        lead_id="L0001", quote_text="quote v1", quote_total_usd=2000,
        selected_items=[{"name": "Biryani", "qty": 1, "price_usd": 2000}],
        source="owner_edit", created_at=NOW, data_path=sb.ledger,
        expected_owner=OWNER, expected_group=GROUP, emit_audit=False,
    )
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    rc, payload, _err = _amend(sb)

    assert rc == 0
    assert payload["quote_version"] == 2
    latest = ql.latest_committed("L0001", data_path=sb.ledger)
    assert latest["source"] == "amendment_applied"
    assert latest["version"] == 2


def test_no_ledger_version_is_minted_when_the_lead_has_no_committed_quote(sb):
    """Committing v1 from an amendment would fabricate a quote nobody drafted."""
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    rc, payload, _err = _amend(sb)

    assert rc == 0
    assert payload["quote_version"] is None
    assert ql.latest_committed("L0001", data_path=sb.ledger) is None


def test_owner_card_reports_the_change_and_carries_the_approval_code(sb):
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    _amend(sb)

    assert len(sb.sends) == 1
    card = sb.sends[0]["message"]
    assert sb.sends[0]["jid"] == "19045550100@s.whatsapp.net"
    assert "headcount" in card
    assert "280" in card
    assert "#A3F2X approve" in card


def test_amendment_applied_audit_row_records_names_not_values(sb):
    """PRIVACY: the audit chain carries WHICH fields moved, never the customer's
    values or the raw amendment text."""
    sb.write_lead()
    cap = sb.capture("actually make it 280 people", message_id="wamid.A1")

    _amend(sb)

    rows = sb.audit_rows("catering_amendment_applied")
    assert len(rows) == 1
    assert rows[0]["lead_id"] == "L0001"
    assert rows[0]["amendment_id"] == cap.amendment_id
    assert rows[0]["fields_changed"] == ["headcount"]
    # Scan every field that could carry a customer value — but NOT `ts`.
    # The timestamp has microsecond precision, so roughly one run in a thousand
    # produces something like ...T02:57:27.280053+00:00, which contains "280"
    # and fails this privacy assertion on the clock rather than on a leak.
    # Observed exactly that way in CI on 2026-08-23. `ts` is machine-generated
    # and cannot carry the customer's headcount or their raw text, so excluding
    # it narrows the scan to what the assertion is actually about.
    scanned = {k: v for k, v in rows[0].items() if k != "ts"}
    assert "ts" in rows[0], "the row must still carry a timestamp"
    blob = json.dumps(scanned)
    assert "280" not in blob and "actually make it" not in blob


def test_terminal_lead_is_not_amended_in_place(sb):
    """Rewriting a quote the customer already has, with no record on their side that
    it moved, is worse than leaving the amendment for the owner."""
    sb.write_lead(status="CLOSED")
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    rc, _payload, err = _amend(sb)

    assert rc == 4
    assert "not amendable" in err
    assert sb.read_lead()["extracted"]["headcount"] == 235


def test_unknown_lead_is_not_found(sb):
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1",
               lead={"lead_id": "L0404", "extracted": {}})
    rc, _payload, _err = _run(sb, ["amend-catering-lead", "--lead-id", "L0404",
                                   "--mode", "amendment"])
    assert rc == 4


def test_bridge_failure_reports_dependency_down_with_state_persisted(sb):
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    rc, _payload, _err = _amend(sb, bridge_ok=False)

    assert rc == 6
    assert sb.read_lead()["extracted"]["headcount"] == 280, "state must survive a failed notify"


# ════════════════════════════════════════════════════════════════════════════
# Answer mode (deterministic slot-filling loop)
# ════════════════════════════════════════════════════════════════════════════
def _qualifying_lead(sb, **overrides):
    base = dict(
        status="QUALIFYING", quote_text="",
        extracted={"headcount": 120},
        pending_questions=["event_date", "event_type"],
        questions_asked=["event_date", "event_type"],
        qualification_rounds=1,
    )
    base.update(overrides)
    return sb.write_lead(**base)


def test_answer_fills_nulls_and_asks_the_next_batch(sb):
    _qualifying_lead(sb)

    rc, payload, _err = _answer(sb, "It's a wedding on June 15")

    assert rc == 0
    lead = sb.read_lead()
    assert lead["extracted"]["event_type"] == "wedding"
    assert lead["extracted"]["event_date"].endswith("-06-15")
    assert lead["status"] == "QUALIFYING"
    assert lead["qualification_rounds"] == 2
    assert set(payload["fields_filled"]) == {"event_type", "event_date"}
    assert lead["pending_questions"]  # a next batch was asked
    assert len(sb.sends) == 1, "exactly one customer message per inbound"


def test_answer_never_overwrites_a_field_the_customer_already_gave(sb):
    """Fill-nulls-only. A contradiction is the ruled fresh-vs-stale discriminator's
    business upstream, not a silent rewrite here."""
    _qualifying_lead(sb, extracted={"headcount": 120, "event_date": "2026-09-15"})

    rc, payload, _err = _answer(sb, "wedding for 300 people on June 15")

    assert rc == 0
    extracted = sb.read_lead()["extracted"]
    assert extracted["headcount"] == 120            # unchanged
    assert extracted["event_date"] == "2026-09-15"  # unchanged
    assert "headcount" not in payload["fields_filled"]


def test_bare_venue_reply_is_accepted_when_venue_is_the_only_open_question(sb):
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=["venue"])

    rc, payload, _err = _answer(sb, "Grand Ballroom")

    assert rc == 0
    assert sb.read_lead()["extracted"]["venue"] == "Grand Ballroom"
    assert payload["fields_filled"] == ["venue"]


def test_bare_reply_is_not_assigned_when_two_questions_are_open(sb):
    """With two open questions there is no deterministic way to know which one
    "Grand Ballroom" answers — a wrong assignment writes prose into the wrong field."""
    _qualifying_lead(sb, pending_questions=["venue", "service_style"],
                     questions_asked=["venue", "service_style"])

    rc, _payload, _err = _answer(sb, "Grand Ballroom")

    assert rc == 0
    assert sb.read_lead()["extracted"]["venue"] is None


def test_completion_transitions_to_owner_approval_and_fires_the_card(sb):
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=[
        "event_date", "guest_count", "event_type", "service_style", "veg_nonveg", "venue",
    ], extracted={
        "headcount": 120, "event_date": "2026-09-15", "event_type": "wedding",
        "service_style": "buffet", "dietary_restrictions": ["veg"],
    })

    rc, payload, _err = _answer(sb, "Grand Ballroom")

    assert rc == 0
    lead = sb.read_lead()
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert lead["pending_questions"] == []
    assert payload["qualification_complete"] is True
    assert payload["outcome"] == "complete"
    assert payload["card_sent"] is True
    jids = [s["jid"] for s in sb.sends]
    assert "19045550100@s.whatsapp.net" in jids           # owner card
    assert "19045550199@s.whatsapp.net" in jids           # customer confirmation
    status_rows = sb.audit_rows("catering_lead_status_change")
    assert status_rows[-1]["from_status"] == "QUALIFYING"
    assert status_rows[-1]["to_status"] == "AWAITING_OWNER_APPROVAL"
    assert status_rows[-1]["reason"] == "qualification_complete"


def test_completion_lands_the_documented_pre_draft_sentinel_not_the_legacy_one(sb):
    """The S1 invariant requires non-empty quote_text at AWAITING_OWNER_APPROVAL, and
    status is set by assignment (no validator runs). Without an explicit value the
    read-time shim would backfill LEGACY_QUOTE_TEXT_SENTINEL and warn about a
    "legacy pre-v0.3 lead" that was created today."""
    from schemas import LEGACY_QUOTE_TEXT_SENTINEL, PRE_QUOTE_DRAFT_SENTINEL

    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=list(cq.REQUIRED_FIELDS),
                     extracted={
                         "headcount": 120, "event_date": "2026-09-15",
                         "event_type": "wedding", "service_style": "buffet",
                         "dietary_restrictions": ["veg"],
                     })

    rc, _payload, err = _answer(sb, "Grand Ballroom")

    assert rc == 0
    lead = sb.read_lead()
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert lead["quote_text"] == PRE_QUOTE_DRAFT_SENTINEL
    assert lead["quote_text"] != LEGACY_QUOTE_TEXT_SENTINEL
    assert "legacy quote_text=empty" not in err


def test_round_cap_hands_to_the_owner_with_the_gaps_flagged(sb):
    _qualifying_lead(sb, qualification_rounds=cq.MAX_QUALIFICATION_ROUNDS,
                     pending_questions=["venue"], questions_asked=["venue"])

    rc, payload, _err = _answer(sb, "not sure yet")

    assert rc == 0
    lead = sb.read_lead()
    assert lead["status"] == "AWAITING_OWNER_APPROVAL"
    assert payload["outcome"] == "round_cap_reached"
    assert payload["qualification_complete"] is False
    card = next(s["message"] for s in sb.sends if s["jid"] == "19045550100@s.whatsapp.net")
    assert "Still missing" in card
    assert "venue" in card


def test_loop_stops_when_every_remaining_gap_has_already_been_asked(sb):
    """Asking a third time for something the customer twice declined to give is what
    makes a bot feel broken — hand it to the owner instead."""
    _qualifying_lead(sb, qualification_rounds=1, pending_questions=["venue"],
                     questions_asked=list(cq.REQUIRED_FIELDS))

    rc, payload, _err = _answer(sb, "no idea")

    assert rc == 0
    assert payload["outcome"] == "round_cap_reached"
    assert sb.read_lead()["status"] == "AWAITING_OWNER_APPROVAL"


def test_qualification_audit_row_records_names_not_answers(sb):
    _qualifying_lead(sb)

    _answer(sb, "It's a wedding on June 15")

    rows = sb.audit_rows("catering_lead_qualification_updated")
    assert len(rows) == 1
    assert set(rows[0]["fields_filled"]) == {"event_type", "event_date"}
    assert rows[0]["outcome"] == "asked"
    assert rows[0]["complete"] is False
    assert "It's a wedding" not in json.dumps(rows[0])


def test_answer_mode_refuses_a_lead_that_is_not_qualifying(sb):
    sb.write_lead(status="AWAITING_OWNER_APPROVAL")
    rc, _payload, err = _answer(sb, "Grand Ballroom")
    assert rc == 4
    assert "answer mode only acts on" in err


def test_answer_mode_requires_text(sb):
    _qualifying_lead(sb)
    rc, _payload, err = _answer(sb, "   ")
    assert rc == 2
    assert "--answer-text required" in err


def test_replaying_the_same_answer_leaves_the_filled_field_alone(sb):
    """Fill-nulls-only makes a replayed inbound harmless: the second pass finds the
    field already set and reports nothing filled."""
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=["venue"])
    first_rc, first, _e = _answer(sb, "Grand Ballroom")
    assert first_rc == 0 and "venue" in first["fields_filled"]
    assert sb.read_lead()["status"] == "QUALIFYING", "still gaps, so still in the loop"

    second_rc, second, _e2 = _answer(sb, "Grand Ballroom")

    assert second_rc == 0
    assert second["fields_filled"] == []
    assert sb.read_lead()["extracted"]["venue"] == "Grand Ballroom"


# ════════════════════════════════════════════════════════════════════════════
# A value the schema cannot hold must never freeze the lead
#
# The bug: "we will have 99999 vegetarians" parsed to veg_guest_count=99999,
# which CateringLeadExtractedFields bounds at 10000. setattr accepted it (the
# model does not set validate_assignment), _persist's model_validate then failed,
# and the script exited BEFORE mark_applied — so the record stayed unapplied, was
# re-read on every later run, and re-failed. Every LATER amendment and answer for
# that lead was silently dropped behind it, permanently.
#
# Three layers, each pinned below: the extractor never emits an out-of-band count;
# the merge refuses a value the field cannot hold; the record is marked
# APPLIED-WITH-ERROR so it can never block what comes after it.
# ════════════════════════════════════════════════════════════════════════════
def test_poison_amendment_does_not_block_the_amendments_behind_it(sb):
    """The reviewer's exact RUN1 / RUN2 / RUN3 sequence. RUN3 is the one that
    matters: a perfectly good "make it 280" landing behind the poison."""
    sb.write_lead()
    assert sb.capture("we will have 99999 vegetarians", message_id="wamid.P1").ok

    rc1, payload1, _e1 = _amend(sb, message_id="wamid.P1")
    assert rc1 == 0, "the poison record no longer fails the run"
    assert payload1["applied"], "and it is drained rather than left to re-fail"

    rc2, payload2, _e2 = _amend(sb, message_id="wamid.P1")
    assert rc2 == 0
    assert payload2["reason"] == "no_unapplied_amendments", "not re-processed forever"

    assert sb.capture("actually make it 280 people not 235", message_id="wamid.P2").ok
    rc3, payload3, _e3 = _amend(sb, message_id="wamid.P2")

    assert rc3 == 0
    assert payload3["fields_changed"] == ["headcount"]
    assert sb.read_lead()["extracted"]["headcount"] == 280


def test_an_out_of_band_count_is_recorded_as_a_note_not_dropped_silently(sb):
    """The owner still needs to see what the customer said — just not in the
    numeric field, where it would be a fabricated headcount split."""
    sb.write_lead()
    sb.capture("we will have 99999 vegetarians", message_id="wamid.P1")

    _amend(sb)

    extracted = sb.read_lead()["extracted"]
    assert extracted["veg_guest_count"] is None
    assert "99999" in extracted["notes"]
    assert extracted["dietary_restrictions"] == ["veg"], "the stated preference survives"


def _poison_extractor(monkeypatch, fields):
    """Force the merge layer to face a value the extractor's own band would never
    emit — defense in depth: the clamp and the merge gate are separate layers and
    each has to hold on its own."""
    monkeypatch.setattr(cx, "extract_catering_fields", lambda text, signals=None: dict(fields))


def test_a_record_whose_values_the_schema_refuses_is_applied_with_error(sb, monkeypatch):
    sb.write_lead()
    cap = sb.capture("make it 280 people", message_id="wamid.P1")
    _poison_extractor(monkeypatch, {"headcount": 280, "veg_guest_count": 99999})

    rc, payload, err = _amend(sb)

    assert rc == 0
    assert payload["applied"] == [cap.amendment_id]
    assert payload["applied_with_error"] == [cap.amendment_id]
    assert "veg_guest_count" in err
    # The value the schema CAN hold still lands — one bad field is not a reason to
    # throw away the correction the customer actually sent.
    assert sb.read_lead()["extracted"]["headcount"] == 280
    assert sb.read_lead()["extracted"]["veg_guest_count"] is None


def test_the_applied_with_error_marker_is_stamped_on_the_sidecar_record(sb, monkeypatch):
    sb.write_lead()
    sb.capture("make it 280 people", message_id="wamid.P1")
    _poison_extractor(monkeypatch, {"headcount": 280, "veg_guest_count": 99999})

    _amend(sb)

    record = json.loads(sb.amendments.read_text(encoding="utf-8"))["records"][0]
    assert record["applied_at"], "stamped applied — it can never block later records"
    assert record[ca.APPLIED_ERROR_FIELD] == "schema_rejected_values"
    assert record["raw_text"] == "make it 280 people", "the capture stays immutable"
    assert ca.unapplied_for_lead("L0001", data_path=sb.amendments) == []


def test_the_audit_row_names_the_rejected_field_without_its_value(sb, monkeypatch):
    sb.write_lead()
    sb.capture("make it 280 people", message_id="wamid.P1")
    _poison_extractor(monkeypatch, {"headcount": 280, "veg_guest_count": 99999})

    _amend(sb)

    rows = sb.audit_rows("catering_amendment_applied")
    assert len(rows) == 1
    assert rows[0]["apply_error"] == "schema_rejected_values"
    assert rows[0]["rejected_fields"] == ["veg_guest_count"]
    from conftest import privacy_blob
    assert "99999" not in privacy_blob(rows[0]), "PRIVACY: names, never values"


def test_a_later_amendment_still_applies_after_an_applied_with_error_record(sb, monkeypatch):
    """The whole point of the error marker: the record is drained, so what comes
    after it is unaffected."""
    sb.write_lead()
    sb.capture("make it 280 people", message_id="wamid.P1")
    _poison_extractor(monkeypatch, {"headcount": 280, "veg_guest_count": 99999})
    _amend(sb)

    monkeypatch.undo()
    sb.capture("move the date to October 12", message_id="wamid.P2")
    rc, payload, _err = _amend(sb)

    assert rc == 0
    assert payload["fields_changed"] == ["event_date"]
    assert sb.read_lead()["extracted"]["event_date"] == "2026-10-12"


def test_answer_mode_survives_a_value_the_schema_refuses(sb, monkeypatch):
    """The loop must keep running: the bad value is dropped, the good ones land,
    and the round still advances so the lead is never stranded mid-intake."""
    _qualifying_lead(sb, extracted={"headcount": 120},
                     pending_questions=["event_type", "veg_nonveg"],
                     questions_asked=["event_type", "veg_nonveg"])
    _poison_extractor(monkeypatch, {"event_type": "wedding", "veg_guest_count": 99999})

    rc, payload, err = _answer(sb, "a wedding, 99999 vegetarians")

    assert rc == 0
    assert payload["fields_filled"] == ["event_type"]
    assert payload["rejected_fields"] == ["veg_guest_count"]
    assert "veg_guest_count" in err
    lead = sb.read_lead()
    assert lead["extracted"]["event_type"] == "wedding"
    assert lead["extracted"]["veg_guest_count"] is None
    assert lead["qualification_rounds"] == 2, "the round still advanced"
    assert lead["pending_questions"], "and the next batch was asked"

    rows = sb.audit_rows("catering_lead_qualification_updated")
    assert rows[-1]["rejected_fields"] == ["veg_guest_count"]


def test_the_next_answer_lands_after_a_rejected_one(sb, monkeypatch):
    _qualifying_lead(sb, extracted={"headcount": 120},
                     pending_questions=["event_type", "veg_nonveg"],
                     questions_asked=["event_type", "veg_nonveg"])
    _poison_extractor(monkeypatch, {"veg_guest_count": 99999})
    first_rc, _first, _e = _answer(sb, "99999 vegetarians")
    assert first_rc == 0

    monkeypatch.undo()
    rc, payload, _err = _answer(sb, "It's a wedding")

    assert rc == 0
    assert "event_type" in payload["fields_filled"]
    assert sb.read_lead()["extracted"]["event_type"] == "wedding"


# ════════════════════════════════════════════════════════════════════════════
# M4/G4 per-lead hold — the qualification loop must obey it
#
# The hold was enforced at send-catering-ack, the owner-approved quote send, the
# acceptance writer and the follow-up engine, but NOT here: an owner held a lead,
# the customer replied, and the bot carried on interviewing them.
# ════════════════════════════════════════════════════════════════════════════
def test_a_held_lead_merges_the_answer_but_sends_the_customer_nothing(sb):
    _qualifying_lead(sb, on_hold=True, hold_reason="customer asked us to wait",
                     extracted={"headcount": 235, "event_date": "2026-09-15"},
                     pending_questions=["venue", "service_style"],
                     questions_asked=["venue", "service_style"])

    rc, payload, _err = _answer(sb, "Grand Ballroom, buffet please")

    assert rc == 0, "a withheld send is a correct outcome, not a failed notification"
    assert payload["customer_send_withheld"] is True
    # Capturing what the customer said is never the harm — only the reply is.
    assert sb.read_lead()["extracted"]["service_style"] == "buffet"
    assert [s for s in sb.sends if s["jid"].startswith("19045550199")] == []


def test_a_held_lead_audits_every_withheld_customer_send(sb):
    _qualifying_lead(sb, on_hold=True, hold_reason="customer asked us to wait")

    _answer(sb, "It's a wedding on June 15")

    rows = sb.audit_rows("catering_lead_hold_blocked_send")
    assert len(rows) == 1
    assert rows[0]["lead_id"] == "L0001"
    assert rows[0]["blocked_send_kind"] == "qualification_question"
    assert rows[0]["hold_reason"] == "customer asked us to wait"


def test_a_hold_never_withholds_the_owner_card(sb):
    """A hold pauses the CUSTOMER-facing automation, not the owner's visibility —
    the owner is exactly who needs to see where the held lead got to."""
    _qualifying_lead(sb, on_hold=True, hold_reason="waiting on the customer",
                     pending_questions=["venue"], questions_asked=list(cq.REQUIRED_FIELDS),
                     extracted={
                         "headcount": 120, "event_date": "2026-09-15",
                         "event_type": "wedding", "service_style": "buffet",
                         "dietary_restrictions": ["veg"],
                     })

    rc, payload, _err = _answer(sb, "Grand Ballroom")

    assert rc == 0
    assert payload["card_sent"] is True
    assert [s["jid"] for s in sb.sends] == ["19045550100@s.whatsapp.net"]


def test_an_unheld_lead_still_replies(sb):
    """Guard against the hold check reading as always-on."""
    _qualifying_lead(sb)

    rc, payload, _err = _answer(sb, "It's a wedding on June 15")

    assert rc == 0
    assert payload["customer_send_withheld"] is False
    assert sb.audit_rows("catering_lead_hold_blocked_send") == []
    assert [s for s in sb.sends if s["jid"].startswith("19045550199")]


# ════════════════════════════════════════════════════════════════════════════
# --answer-text-stdin: customer text is not argv
#
# A reply that merely STARTS with a dash ("-veg", "--help", "-80") was parsed by
# argparse as a flag → SystemExit 2 → the router read that as "answer mode could
# not handle it" and fell through to R2A capture, which applies in OVERWRITE mode
# instead of fill-nulls. The text now travels over stdin.
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text", ["-veg", "--help", "-80", "--answer-text", "-"])
def test_a_dash_leading_answer_reaches_answer_mode_over_stdin(sb, text):
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=["venue"])

    rc, payload, err = _answer_via_stdin(sb, text)

    assert rc == 0, f"{text!r} must not be read as a flag: {err}"
    assert payload["lead_id"] == "L0001"
    assert payload["status"] in ("QUALIFYING", "AWAITING_OWNER_APPROVAL")


def test_a_dash_leading_answer_fills_nulls_and_never_overwrites(sb):
    """The harm the argparse exit caused was not the exit itself: the router read
    it as "answer mode could not handle this" and fell through to the R2A capture
    path, which applies in OVERWRITE mode. Reaching answer mode is what keeps a
    field the customer already gave from being rewritten by a stray reply."""
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=["venue"],
                     extracted={"headcount": 120, "event_type": "wedding"})

    rc, payload, _err = _answer_via_stdin(sb, "-80 people maybe")

    assert rc == 0
    extracted = sb.read_lead()["extracted"]
    assert extracted["headcount"] == 120, "fill-nulls-only, not overwrite"
    assert extracted["event_type"] == "wedding"
    assert "headcount" not in payload["fields_filled"]


def test_ordinary_text_over_stdin_behaves_exactly_as_over_argv(sb):
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=["venue"])

    rc, payload, _err = _answer_via_stdin(sb, "Grand Ballroom")

    assert rc == 0
    assert payload["fields_filled"] == ["venue"]
    assert sb.read_lead()["extracted"]["venue"] == "Grand Ballroom"


def test_empty_stdin_is_invalid_input_not_a_crash(sb):
    _qualifying_lead(sb)
    rc, _payload, err = _answer_via_stdin(sb, "   \n")
    assert rc == 2
    assert "--answer-text required" in err


def test_argv_answer_text_still_works_for_compatibility(sb):
    """The flag is kept so an operator running the script by hand is not broken."""
    _qualifying_lead(sb, pending_questions=["venue"], questions_asked=["venue"])
    rc, payload, _err = _answer(sb, "Grand Ballroom")
    assert rc == 0
    assert payload["fields_filled"] == ["venue"]


# ── P17b: the amended card's real fate is recorded, not assumed ──────────────
# The amendment is applied and the lead persisted BEFORE the card is sent. When
# the card does not arrive, the lead sits waiting on an approval the owner may
# never have seen, and the only trace used to be a line on stderr.

def _unconfirmed(sb):
    return sb.audit_rows("catering_customer_send_unconfirmed")


def test_delivered_amended_card_is_recorded_on_the_lead(sb):
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    rc, _payload, _err = _amend(sb)

    assert rc == 0
    assert sb.read_lead()["card_delivery_status"] == "sent"
    assert _unconfirmed(sb) == []
    assert sb.pages == [], "a delivered card pages nobody"


def test_failed_amended_card_marks_the_lead_and_pages_the_owner(sb):
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    rc, _payload, _err = _amend(sb, bridge_ok=False)

    lead = sb.read_lead()
    assert rc == 6
    assert lead["extracted"]["headcount"] == 280, "the amendment IS applied"
    assert lead["card_delivery_status"] == "failed"
    assert lead["card_delivery_status_at"]
    rows = _unconfirmed(sb)
    assert len(rows) == 1
    assert rows[0]["send_kind"] == "amendment_owner_card"
    assert rows[0]["delivery_certainty"] == "failed"
    assert rows[0]["send_status"] == "connect_failed"
    assert rows[0]["script"] == "amend-catering-lead"
    assert rows[0]["owner_paged"] is True
    body = " ".join(sb.pages[-1]["argv"])
    assert "waiting on an approval the owner never saw" in body, body


def test_uncertain_amended_card_is_not_recorded_as_a_definite_failure(sb):
    """The bridge accepted the card, so it most likely reached the owner. A
    definite-failure record would send someone hunting for a card that is sitting
    in their chat — and would invite a duplicate for a quote that has moved."""
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    _amend(sb, bridge_ok=False, bridge_status="send_uncertain")

    assert sb.read_lead()["card_delivery_status"] == "uncertain"
    rows = _unconfirmed(sb)
    assert rows[0]["delivery_certainty"] == "uncertain"
    assert rows[0]["send_status"] == "send_uncertain"
    assert "most likely DID arrive" in " ".join(sb.pages[-1]["argv"])
    assert len(sb.sends) == 1, "never re-sent"


def test_card_failed_row_carries_the_real_status_not_a_hardcoded_one(sb):
    """CateringOwnerApprovalCardFailed.reason is a free-form str, so it must say
    what actually happened rather than always claiming bridge_unreachable."""
    sb.write_lead()
    sb.capture("actually make it 280 people", message_id="wamid.A1")

    _amend(sb, bridge_ok=False, bridge_status="send_uncertain")

    failed = sb.audit_rows("catering_owner_approval_card_failed")
    assert failed and failed[0]["reason"] == "send_uncertain"


def _customer_reply_rows(sb):
    return [r for r in _unconfirmed(sb) if r["send_kind"] == "amendment_customer_reply"]


def test_failed_customer_reply_pages_the_owner(sb):
    """The customer reply goes out AFTER the answer is merged and the qualification
    round advanced on disk, and nothing retries it. So a failure strands a customer
    mid-interview waiting on a question the lead's own state says was asked — that
    needs a human. It still must NOT touch card_delivery_status: that field
    describes the OWNER card, and overloading it here would corrupt the signal the
    owner-card arm depends on."""
    sb.write_lead(status="QUALIFYING", pending_questions=["venue"],
                  questions_asked=["venue"], qualification_rounds=1)

    _answer(sb, "the venue is the Grand Ballroom", bridge_ok=False)

    lead = sb.read_lead()
    assert lead["status"] == "QUALIFYING", "the owner card never entered this turn"
    assert len(sb.sends) == 1, "exactly one send was attempted"
    assert lead["extracted"]["venue"] == "the venue is the Grand Ballroom", (
        "the merge IS committed")
    assert lead["qualification_rounds"] == 2, "the round advance IS committed"
    assert lead["card_delivery_status"] is None, "the OWNER card's field is untouched"
    assert lead["card_delivery_status_at"] is None
    rows = _customer_reply_rows(sb)
    assert len(rows) == 1
    assert rows[0]["delivery_certainty"] == "failed"
    assert rows[0]["owner_paged"] is True
    assert len(sb.pages) == 1, "exactly one page per unconfirmed send, never two"
    body = " ".join(sb.pages[-1]["argv"])
    assert "did NOT go out" in body, body
    ack_failed = sb.audit_rows("catering_customer_ack_failed")
    assert ack_failed and ack_failed[0]["reason"] == "bridge_unreachable", (
        "the Literal the pinned rollback target recognises must not be widened")


def test_uncertain_customer_reply_pages_without_inviting_a_blind_resend(sb):
    """The bridge accepted the reply, so it most likely reached the customer. The
    owner still has to know — nothing retries — but the page must say so, or the
    fix becomes a second identical message to a customer who already got the
    first."""
    sb.write_lead(status="QUALIFYING", pending_questions=["venue"],
                  questions_asked=["venue"], qualification_rounds=1)

    _answer(sb, "the venue is the Grand Ballroom", bridge_ok=False,
            bridge_status="send_uncertain")

    lead = sb.read_lead()
    assert lead["extracted"]["venue"] == "the venue is the Grand Ballroom", (
        "the merge IS committed")
    assert lead["card_delivery_status"] is None, "the OWNER card's field is untouched"
    rows = _customer_reply_rows(sb)
    assert len(rows) == 1
    assert rows[0]["delivery_certainty"] == "uncertain"
    assert rows[0]["send_status"] == "send_uncertain"
    assert rows[0]["owner_paged"] is True
    assert len(sb.pages) == 1, "exactly one page per unconfirmed send, never two"
    body = " ".join(sb.pages[-1]["argv"])
    assert "most likely DID arrive" in body, body
    assert "duplicate message to the customer" in body, body
    assert len(sb.sends) == 1, "never re-sent"


def test_a_delivered_customer_reply_pages_nobody(sb):
    sb.write_lead(status="QUALIFYING", pending_questions=["venue"],
                  questions_asked=["venue"], qualification_rounds=1)

    rc, _payload, _err = _answer(sb, "the venue is the Grand Ballroom")

    assert rc == 0
    assert _customer_reply_rows(sb) == []
    assert sb.pages == []
