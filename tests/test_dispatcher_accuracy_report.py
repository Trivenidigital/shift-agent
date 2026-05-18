"""Tests for src/platform/scripts/dispatcher-accuracy-report — Layer 0 routing
reliability monitor. Pairs raw_inbound entries with dispatcher_routed (or
unknown_sender_declined) entries and reports unpaired = Kimi-skipped-dispatch."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Load the script as a module — no .py extension, mirrors existing pattern.
SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "platform" / "scripts" / "dispatcher-accuracy-report"
)
loader = importlib.machinery.SourceFileLoader("dispatcher_accuracy_report", str(SCRIPT))
spec = importlib.util.spec_from_loader("dispatcher_accuracy_report", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)


def _ts(base: datetime, delta_seconds: int = 0) -> str:
    return (base + timedelta(seconds=delta_seconds)).isoformat()


def _write_log(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# Pairing logic
# ─────────────────────────────────────────────────────────────────


def test_pair_by_message_id_happy_path(now):
    """raw_inbound + dispatcher_routed with same message_id pair cleanly."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "wa:abc",
         "sender_phone": "+918522041562", "input_message": "#X9KRV yes"},
        {"type": "dispatcher_routed", "ts": _ts(now, 2), "message_id": "wa:abc",
         "sender_role": "owner", "message_shape": "approval_code",
         "routed_to_skill": "handle_owner_command",
         "sender_phone": "+918522041562"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 1
    assert len(unpaired) == 0
    inb, match, kind = paired[0]
    assert kind == "dispatcher_routed"
    assert match["routed_to_skill"] == "handle_owner_command"


def test_unpaired_when_no_dispatcher_routed(now):
    """raw_inbound with no matching dispatcher_routed = Kimi skipped dispatch."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "wa:abc",
         "sender_phone": "+918522041562", "input_message": "Bro can't come"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 0
    assert len(unpaired) == 1
    assert unpaired[0]["message_id"] == "wa:abc"


def test_unknown_sender_declined_pairs_by_phone_within_window(now):
    """When a sender is declined, no dispatcher_routed is written; pair via
    unknown_sender_declined within the time window."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "wa:xyz",
         "sender_phone": "+15551234567", "input_message": "hello"},
        {"type": "unknown_sender_declined", "ts": _ts(now, 3),
         "sender_phone": "+15551234567",
         "input_message_truncated": "hello"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 1
    assert len(unpaired) == 0
    _inb, match, kind = paired[0]
    assert kind == "unknown_sender_declined"


def test_unknown_sender_declined_outside_window_does_not_pair(now):
    """A decline 60 seconds later for the same phone is NOT a match."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "wa:xyz",
         "sender_phone": "+15551234567", "input_message": "hello"},
        {"type": "unknown_sender_declined", "ts": _ts(now, 60),
         "sender_phone": "+15551234567",
         "input_message_truncated": "hello"},
    ]
    paired, unpaired = mod.pair_inbounds(entries, pair_window_seconds=10)
    assert len(unpaired) == 1


def test_unknown_sender_declined_pairs_by_lid(now):
    """LID-only senders (no phone) pair via sender_lid match."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "wa:lid1",
         "sender_lid": "201975216009469@lid", "input_message": "hi"},
        {"type": "unknown_sender_declined", "ts": _ts(now, 1),
         "sender_lid": "201975216009469@lid",
         "input_message_truncated": "hi"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 1
    assert paired[0][2] == "unknown_sender_declined"


def test_cf_router_proposal_selection_pairs_like_dispatcher_routed(now):
    rows = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "m-prop", "sender_lid": "201@lid"},
        {
            "type": "cf_router_intercepted",
            "ts": _ts(now, 1),
            "reason": "f7_proposal_selection",
            "chat_id": "201@lid",
            "subprocess_rc": 0,
        },
    ]
    paired, unpaired = mod.pair_inbounds(rows)
    assert len(paired) == 1
    assert unpaired == []
    assert paired[0][2] == "cf_router_intercepted"


def test_cf_router_proposal_selection_pairs_phone_jid_like_dispatcher_routed(now):
    rows = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "m-phone",
         "sender_phone": "+15551234567"},
        {
            "type": "cf_router_intercepted",
            "ts": _ts(now, 1),
            "reason": "f7_proposal_selection",
            "chat_id": "15551234567@s.whatsapp.net",
            "subprocess_rc": 0,
        },
    ]
    paired, unpaired = mod.pair_inbounds(rows)
    assert len(paired) == 1
    assert unpaired == []
    assert paired[0][2] == "cf_router_intercepted"


def test_cf_router_proposal_request_pairs_like_dispatcher_routed(now):
    rows = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "m-prop-request",
         "sender_lid": "201@lid"},
        {
            "type": "cf_router_intercepted",
            "ts": _ts(now, 1),
            "reason": "f7_proposal_request",
            "chat_id": "201@lid",
            "subprocess_rc": 0,
        },
    ]
    paired, unpaired = mod.pair_inbounds(rows)
    assert len(paired) == 1
    assert unpaired == []
    assert paired[0][2] == "cf_router_intercepted"


def test_non_proposal_cf_router_intercept_does_not_pair(now):
    rows = [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "m-status",
         "sender_phone": "+15551234567"},
        {
            "type": "cf_router_intercepted",
            "ts": _ts(now, 1),
            "reason": "status_check",
            "chat_id": "15551234567@s.whatsapp.net",
            "subprocess_rc": 0,
        },
    ]
    paired, unpaired = mod.pair_inbounds(rows)
    assert paired == []
    assert len(unpaired) == 1
    assert unpaired[0]["message_id"] == "m-status"


def test_orphan_dispatcher_routed_does_not_appear_in_either_list(now):
    """A dispatcher_routed with no preceding raw_inbound is ignored — the
    report counts inbounds, not routing decisions."""
    entries = [
        {"type": "dispatcher_routed", "ts": _ts(now), "message_id": "wa:orphan",
         "sender_role": "owner", "message_shape": "text",
         "routed_to_skill": "handle_owner_command"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 0
    assert len(unpaired) == 0


def test_pair_inbounds_pairs_flyer_starter_brief(now):
    """BUG-FLYER-QA-003b: flyer cf-router intercepts must pair against
    raw_inbound. Pre-fix, only catering F7 reasons were whitelisted, so
    every Flyer-routed inbound was counted as 'Kimi skipped dispatcher'."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now, 0), "message_id": "wa:fly1",
         "sender_phone": "+15551234567", "input_message": "Create flyer"},
        {"type": "cf_router_intercepted", "ts": _ts(now, 2),
         "reason": "flyer_starter_brief",
         "chat_id": "15551234567@s.whatsapp.net"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 1
    assert len(unpaired) == 0
    _inb, _match, kind = paired[0]
    assert kind == "cf_router_intercepted"


def test_pair_inbounds_does_not_pair_flyer_failure_reasons(now):
    """BUG-FLYER-QA-003b negative: *_failed reasons indicate the LLM still
    ran, so they must NOT be treated as dispatcher-equivalent routes."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now, 0), "message_id": "wa:fly2",
         "sender_phone": "+15551234567", "input_message": "broken"},
        {"type": "cf_router_intercepted", "ts": _ts(now, 2),
         "reason": "flyer_primary_failed",
         "chat_id": "15551234567@s.whatsapp.net"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 0
    assert len(unpaired) == 1


def test_json_report_emits_back_compat_and_new_cf_router_keys(now):
    """BUG-FLYER-QA-003b: format_json_report emits BOTH the legacy
    cf_router_proposal_selection_count AND the new
    cf_router_intercepted_count, identical values, for back-compat."""
    paired = [
        (
            {"type": "raw_inbound", "ts": _ts(now), "message_id": "m1"},
            {"type": "cf_router_intercepted", "reason": "flyer_starter_brief",
             "chat_id": "15551234567@s.whatsapp.net"},
            "cf_router_intercepted",
        ),
        (
            {"type": "raw_inbound", "ts": _ts(now, 5), "message_id": "m2"},
            {"type": "cf_router_intercepted", "reason": "f7_proposal_request",
             "chat_id": "15551234567@s.whatsapp.net"},
            "cf_router_intercepted",
        ),
    ]
    out = mod.format_json_report(
        paired, unpaired=[],
        since=now - timedelta(days=1), until=now,
    )
    parsed = json.loads(out)
    assert parsed["cf_router_proposal_selection_count"] == 2
    assert parsed["cf_router_intercepted_count"] == 2


def test_text_report_uses_intercepts_label_post_fix(now):
    """BUG-FLYER-QA-003b: text-report label updated from 'CF router
    proposal selections' to 'CF router intercepts' since the counter now
    aggregates ALL whitelisted intercept reasons, not just F7 proposals."""
    paired = [
        (
            {"type": "raw_inbound", "ts": _ts(now), "message_id": "m1"},
            {"type": "cf_router_intercepted", "reason": "flyer_starter_brief",
             "chat_id": "15551234567@s.whatsapp.net"},
            "cf_router_intercepted",
        ),
    ]
    out = mod.format_text_report(
        paired, unpaired=[],
        since=now - timedelta(days=1), until=now,
    )
    assert "CF router intercepts: 1" in out
    assert "CF router proposal selections" not in out


def test_mixed_traffic_realistic_scenario(now):
    """3 inbounds: 2 paired, 1 skipped — exact mix observed in production
    JSONL post-mortem (~57% first-attempt accuracy floor)."""
    entries = [
        {"type": "raw_inbound", "ts": _ts(now, 0), "message_id": "m1",
         "sender_phone": "+918522041562", "input_message": "#A1B2C yes"},
        {"type": "dispatcher_routed", "ts": _ts(now, 1), "message_id": "m1",
         "sender_role": "owner", "message_shape": "approval_code",
         "routed_to_skill": "handle_owner_command"},
        {"type": "raw_inbound", "ts": _ts(now, 30), "message_id": "m2",
         "sender_phone": "+17329837841", "input_message": "do you do catering?"},
        {"type": "dispatcher_routed", "ts": _ts(now, 32), "message_id": "m2",
         "sender_role": "employee", "message_shape": "text",
         "routed_to_skill": "catering_dispatcher"},
        # m3 has no dispatcher_routed — Kimi skipped dispatch on this one
        {"type": "raw_inbound", "ts": _ts(now, 60), "message_id": "m3",
         "sender_phone": "+918522041562", "input_message": "I can't come"},
    ]
    paired, unpaired = mod.pair_inbounds(entries)
    assert len(paired) == 2
    assert len(unpaired) == 1
    assert unpaired[0]["message_id"] == "m3"


# ─────────────────────────────────────────────────────────────────
# Report formatting
# ─────────────────────────────────────────────────────────────────


def test_text_report_has_coverage_line(now):
    paired = [
        (
            {"type": "raw_inbound", "ts": _ts(now), "message_id": "m1"},
            {"type": "dispatcher_routed", "sender_role": "owner",
             "message_shape": "approval_code",
             "routed_to_skill": "handle_owner_command"},
            "dispatcher_routed",
        ),
    ]
    unpaired = [{"type": "raw_inbound", "ts": _ts(now, 5),
                 "message_id": "m2", "input_message": "test"}]
    out = mod.format_text_report(
        paired, unpaired,
        since=now - timedelta(days=1), until=now,
    )
    assert "Routing coverage:   1/2 (50.0%)" in out
    assert "Unpaired (skipped): 1" in out
    assert "handle_owner_command" in out


def test_json_report_machine_readable(now):
    paired = [
        (
            {"type": "raw_inbound", "ts": _ts(now), "message_id": "m1"},
            {"type": "dispatcher_routed", "sender_role": "owner",
             "message_shape": "text", "routed_to_skill": "handle_owner_command"},
            "dispatcher_routed",
        ),
    ]
    unpaired = [{"type": "raw_inbound", "ts": _ts(now, 5), "message_id": "m2"}]
    out = mod.format_json_report(
        paired, unpaired,
        since=now - timedelta(days=1), until=now,
    )
    parsed = json.loads(out)
    assert parsed["total_raw_inbound"] == 2
    assert parsed["paired_count"] == 1
    assert parsed["unpaired_count"] == 1
    assert parsed["coverage_pct"] == 50.0
    assert parsed["by_routed_to_skill"]["handle_owner_command"] == 1
    assert parsed["unpaired"][0]["message_id"] == "m2"


def test_text_report_zero_traffic_renders_safely(now):
    """When the log window has no inbounds, render without ZeroDivisionError."""
    out = mod.format_text_report(
        paired=[], unpaired=[],
        since=now - timedelta(days=1), until=now,
    )
    assert "Inbound traffic:    0" in out
    assert "Routing coverage:   0/0 (0.0%)" in out


# ─────────────────────────────────────────────────────────────────
# Log loading + window filtering
# ─────────────────────────────────────────────────────────────────


def test_load_entries_filters_by_since_and_until(tmp_path, now):
    log = tmp_path / "decisions.log"
    _write_log(log, [
        {"type": "raw_inbound", "ts": _ts(now, -7200), "message_id": "old"},
        {"type": "raw_inbound", "ts": _ts(now, 0), "message_id": "now"},
        {"type": "raw_inbound", "ts": _ts(now, 7200), "message_id": "future"},
    ])
    entries = mod.load_entries(log,
                               since=now - timedelta(seconds=3600),
                               until=now + timedelta(seconds=3600))
    ids = [e["message_id"] for e in entries]
    assert ids == ["now"]


def test_load_entries_handles_legacy_naive_timestamps(tmp_path, now):
    log = tmp_path / "decisions.log"
    _write_log(log, [
        {"type": "menu_update_applied", "ts": "2026-04-28T11:59:00"},
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "now"},
    ])

    entries = mod.load_entries(
        log,
        since=now - timedelta(minutes=5),
        until=now + timedelta(minutes=5),
    )

    assert [e["type"] for e in entries] == ["menu_update_applied", "raw_inbound"]


def test_load_entries_skips_malformed_lines(tmp_path, now):
    log = tmp_path / "decisions.log"
    log.write_text(
        json.dumps({"type": "raw_inbound", "ts": _ts(now), "message_id": "ok"})
        + "\n{not valid json\n"
        + "\n"  # blank line
        + json.dumps({"type": "raw_inbound", "ts": _ts(now, 1), "message_id": "ok2"})
        + "\n",
        encoding="utf-8",
    )
    entries = mod.load_entries(log)
    assert [e["message_id"] for e in entries] == ["ok", "ok2"]


def test_main_exits_2_when_log_missing(tmp_path):
    rc = mod.main(["--log", str(tmp_path / "nonexistent.log")])
    assert rc == 2


def test_main_renders_json_to_stdout(tmp_path, now, capsys):
    log = tmp_path / "decisions.log"
    _write_log(log, [
        {"type": "raw_inbound", "ts": _ts(now), "message_id": "m1",
         "sender_phone": "+918522041562", "input_message": "test"},
        {"type": "dispatcher_routed", "ts": _ts(now, 1), "message_id": "m1",
         "sender_role": "owner", "message_shape": "text",
         "routed_to_skill": "handle_owner_command"},
    ])
    rc = mod.main([
        "--log", str(log),
        "--format", "json",
        "--since", _ts(now, -3600),
        "--until", _ts(now, 3600),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["coverage_pct"] == 100.0
