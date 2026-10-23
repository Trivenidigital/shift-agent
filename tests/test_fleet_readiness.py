"""PR2 Fleet Sentinel — fleet_readiness library + fleet-readiness-report CLI.

Fixture rows (tests/fixtures/fleet_readiness_rows.jsonl) are COPIED shapes: the
first line is copied verbatim from tests/fixtures/intake_bypass_audit_sample.jsonl
(a redacted production cf_router_intercepted row); the rest carry the exact field
sets of the deployed variants with redacted 555 numbers / synthetic LIDs, and
test_fixture_rows_are_valid_log_entries pins that they parse as LogEntry.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import fleet_readiness as fr
from fixtures_fleet import build_config_dict
from schemas import Config, FleetReadinessReport, LogEntry, _KNOWN_LOG_ENTRY_TYPES, _UnknownLogEntry

REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / "src" / "agents"
SCRIPT = REPO / "src" / "agents" / "shift" / "scripts" / "fleet-readiness-report"
FIXTURE_ROWS = Path(__file__).resolve().parent / "fixtures" / "fleet_readiness_rows.jsonl"

# 2026-10-23 07:10 UTC == 03:10 America/New_York: yesterday (10-22) is the
# latest business date whose brief (07:00 + 180 + 30 grace) and EOD (22:00 +
# 120 + 30 grace) deadlines have passed.
NOW = datetime(2026, 10, 23, 7, 10, tzinfo=timezone.utc)
BUSINESS_DATE = "2026-10-22"

SKILL_ONLY = {"cash_ar", "employee_docs", "hiring", "inventory",
              "pnl_anomaly", "sales_tax", "supplier", "vip"}

_ADAPTER = TypeAdapter(LogEntry)
_PRIVACY_PATTERNS = (re.compile(r"@lid"), re.compile(r"@s\.whatsapp\.net"),
                     re.compile(r"\+1\d{10}"))


def _spec(agent_id: str) -> fr.AgentSpec:
    return next(s for s in fr.FLEET if s.agent_id == agent_id)


def _cfg(**blocks) -> Config:
    return Config.model_validate(build_config_dict(**blocks))


def _facts(cfg: Config | None = None, **kw) -> fr.FleetFacts:
    facts = fr.FleetFacts(now=NOW, config=cfg if cfg is not None else _cfg())
    for k, v in kw.items():
        setattr(facts, k, v)
    return facts


def _all_units_ok() -> dict:
    return {u: ("enabled", "active") for s in fr.FLEET for u in s.timers}


def _fixture_rows() -> list[dict]:
    return [json.loads(ln) for ln in FIXTURE_ROWS.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── FLEET table conformance ─────────────────────────────────────────────────

def test_every_agent_dir_has_exactly_one_fleet_row():
    dirs = sorted(p.name for p in AGENTS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")))
    ids = [s.agent_id for s in fr.FLEET]
    dupes = sorted(a for a, n in Counter(ids).items() if n > 1)
    assert not dupes, f"duplicate FLEET rows: {dupes}"
    assert sorted(ids) == dirs, (
        f"FLEET rows without an agent dir: {sorted(set(ids) - set(dirs))}; "
        f"agent dirs without a FLEET row: {sorted(set(dirs) - set(ids))}"
    )
    assert len(dirs) == 18


def test_fleet_rows_are_well_formed():
    default_cfg = _cfg()
    unit_files = {p.name for p in REPO.glob("src/agents/*/systemd/*")} | {
        p.name for p in REPO.glob("src/platform/systemd/*")}
    for spec in fr.FLEET:
        assert spec.reachability in fr.REACHABILITY, spec.agent_id
        if spec.enabled_flag:
            assert isinstance(fr._resolve(default_cfg, spec.enabled_flag), bool), spec.enabled_flag
        for t in spec.expected_types:
            assert t in _KNOWN_LOG_ENTRY_TYPES, f"{spec.agent_id}: unknown audit type {t}"
        for unit in spec.timers:
            assert unit in unit_files, f"{spec.agent_id}: no unit file {unit}"
        if spec.seed_config_path:
            fr._resolve(default_cfg, spec.seed_config_path)
        dt = spec.daily_terminal
        if dt is not None:
            assert spec.reachability == "timer"
            for t in (*dt.done_types, dt.skip_type, *dt.failure_types):
                assert t in _KNOWN_LOG_ENTRY_TYPES, t
            fr._resolve(default_cfg, dt.schedule_path)
            fr._resolve(default_cfg, dt.catchup_path)


def test_skill_only_set_and_catering_followup_is_a_dormant_timer():
    assert {s.agent_id for s in fr.FLEET if s.reachability == "skill_only"} == SKILL_ONLY
    cf = _spec("catering_followup")
    assert cf.reachability == "timer"
    assert cf.enabled_flag == "catering_followup.enabled"
    assert "catering-followup-sweep.timer" in cf.timers


# ── evaluate() rules ────────────────────────────────────────────────────────

@pytest.mark.parametrize("agent_id", sorted(SKILL_ONLY))
def test_skill_only_is_unreachable_even_when_flag_on(agent_id):
    cfg = _cfg(**{agent_id: {"enabled": True}})
    row = fr.evaluate(_spec(agent_id), _facts(cfg))
    assert row.status == "unreachable_by_design"
    assert f"{agent_id}.enabled=true" in row.evidence


def test_flag_off_is_dormant_by_config():
    row = fr.evaluate(_spec("catering_followup"), _facts(units=_all_units_ok()))
    assert row.status == "dormant_by_config"
    assert row.reason == "catering_followup.enabled is false"


def test_config_unavailable_is_error_for_reachable_agents_only():
    facts = fr.FleetFacts(now=NOW, config=None, config_error="config.yaml missing")
    assert fr.evaluate(_spec("shift"), facts).status == "error"
    assert fr.evaluate(_spec("shift"), facts).reason == "config.yaml missing"
    assert fr.evaluate(_spec("vip"), facts).status == "unreachable_by_design"


def test_required_timer_not_enabled_is_error_and_systemctl_absence_is_not():
    cfg = _cfg(catering_followup={"enabled": True})
    disabled = fr.evaluate(_spec("catering_followup"), _facts(
        cfg, units={"catering-followup-sweep.timer": ("disabled", "inactive")}))
    assert disabled.status == "error"
    assert "catering-followup-sweep.timer is disabled/inactive" in disabled.reason
    unknown = fr.evaluate(_spec("catering_followup"), _facts(
        cfg, units={"catering-followup-sweep.timer": None}))
    assert unknown.status == "active"
    assert "catering-followup-sweep.timer: systemctl unavailable" in unknown.evidence


def test_enabled_without_seed_data_is_awaiting_data():
    cfg = _cfg(compliance={"enabled": True}, equipment_maintenance={"enabled": True})
    facts = _facts(cfg, units=_all_units_ok(),
                   state_files_present={"compliance-items.json": False, "equipment-items.json": False})
    assert fr.evaluate(_spec("compliance"), facts).status == "awaiting_data"
    assert fr.evaluate(_spec("equipment_maintenance"), facts).status == "awaiting_data"
    # multi_location is enabled by default but has no locations configured.
    ml = fr.evaluate(_spec("multi_location"), facts)
    assert ml.status == "awaiting_data"
    assert ml.reason == "multi_location.locations is empty"


def test_inbound_agent_with_zero_traffic_is_active():
    row = fr.evaluate(_spec("shift"), _facts())
    assert row.status == "active"
    assert row.traffic == 0
    assert "traffic_7d=0" in row.evidence


def test_inbound_agent_traffic_counts_expected_types():
    row = fr.evaluate(_spec("shift"), _facts(type_counts=Counter(
        {"proposal_created": 2, "sick_call_send": 1, "raw_inbound": 9})))
    assert (row.status, row.traffic) == ("active", 3)


def _brief_facts(*rows: fr.TerminalRow) -> fr.FleetFacts:
    return _facts(units=_all_units_ok(), terminal_rows=list(rows))


def test_daily_brief_sent_for_business_date_is_active():
    row = fr.evaluate(_spec("daily_brief"), _brief_facts(
        fr.TerminalRow("brief_sent", BUSINESS_DATE, None)))
    assert row.status == "active"
    assert f"business_date={BUSINESS_DATE}" in row.evidence


def test_daily_brief_benign_skip_already_sent_is_active():
    row = fr.evaluate(_spec("daily_brief"), _brief_facts(
        fr.TerminalRow("brief_skipped", BUSINESS_DATE, "already_sent")))
    assert row.status == "active"


def test_skip_disabled_renders_dormant_by_config():
    row = fr.evaluate(_spec("daily_brief"), _brief_facts(
        fr.TerminalRow("brief_skipped", BUSINESS_DATE, "disabled")))
    assert row.status == "dormant_by_config"
    eod = fr.evaluate(_spec("eod_reconcile"), _brief_facts(
        fr.TerminalRow("eod_skipped", BUSINESS_DATE, "disabled")))
    assert eod.status == "dormant_by_config"


@pytest.mark.parametrize("reason", sorted(fr.DEGRADED_SKIP_REASONS))
def test_degraded_skip_reasons_render_stale_with_reason(reason):
    row = fr.evaluate(_spec("daily_brief"), _brief_facts(
        fr.TerminalRow("brief_skipped", BUSINESS_DATE, reason)))
    assert row.status == "stale"
    assert reason in row.reason


def test_eod_benign_and_degraded_skips():
    ok = fr.evaluate(_spec("eod_reconcile"), _brief_facts(
        fr.TerminalRow("eod_skipped", BUSINESS_DATE, "already_done")))
    assert ok.status == "active"
    bad = fr.evaluate(_spec("eod_reconcile"), _brief_facts(
        fr.TerminalRow("eod_skipped", BUSINESS_DATE, "catchup_expired")))
    assert (bad.status, "catchup_expired" in bad.reason) == ("stale", True)


def test_missing_terminal_row_for_past_business_date_is_stale():
    # A row for an OLDER date does not satisfy the expected business date.
    row = fr.evaluate(_spec("eod_reconcile"), _brief_facts(
        fr.TerminalRow("eod_snapshot", "2026-10-21", None)))
    assert row.status == "stale"
    assert row.reason == f"no terminal row for {BUSINESS_DATE}"


def test_send_failure_without_terminal_row_is_stale():
    row = fr.evaluate(_spec("daily_brief"), _brief_facts(
        fr.TerminalRow("brief_send_failed", BUSINESS_DATE, None)))
    assert row.status == "stale"
    assert "brief_send_failed" in row.reason


def test_unrecognized_skip_reason_is_degraded_and_redacted():
    row = fr.evaluate(_spec("daily_brief"), _brief_facts(
        fr.TerminalRow("brief_skipped", BUSINESS_DATE, "call +15555550100")))
    assert row.status == "stale"
    assert "unrecognized" in row.reason
    assert "+1555" not in json.dumps(row.to_dict())


def test_live_log_unreadable_is_error_for_daily_agents():
    facts = _brief_facts()
    facts.live_log_unreadable = True
    assert fr.evaluate(_spec("daily_brief"), facts).status == "error"


def test_compliance_heartbeat_fresh_stale_missing():
    cfg = _cfg(compliance={"enabled": True})
    base = dict(units=_all_units_ok(), state_files_present={"compliance-items.json": True})
    hb = "compliance-last-cron-tick.json"
    assert fr.evaluate(_spec("compliance"), _facts(cfg, heartbeat_age_hours={hb: 3.0}, **base)).status == "active"
    stale = fr.evaluate(_spec("compliance"), _facts(cfg, heartbeat_age_hours={hb: 40.0}, **base))
    assert stale.status == "stale"
    missing = fr.evaluate(_spec("compliance"), _facts(cfg, heartbeat_age_hours={hb: None}, **base))
    assert (missing.status, missing.reason) == ("stale", f"heartbeat {hb} missing")


# ── compare() ───────────────────────────────────────────────────────────────

def _row(agent_id: str, status: str) -> fr.AgentStatus:
    return fr.AgentStatus(agent_id=agent_id, reachability="timer", status=status, reason="")


def test_compare_first_run_is_baseline_without_regressions():
    current = [_row("daily_brief", "stale"), _row("shift", "error")]
    assert fr.compare(None, current) == (fr.MODE_BASELINE, [])
    assert fr.compare({"garbage": 1}, current) == (fr.MODE_BASELINE, [])


def test_compare_flags_active_to_stale_and_any_to_error():
    previous = {"agents": [
        {"agent_id": "daily_brief", "status": "active"},
        {"agent_id": "eod_reconcile", "status": "active"},
        {"agent_id": "catering", "status": "dormant_by_config"},
        {"agent_id": "flyer", "status": "error"},
        {"agent_id": "compliance", "status": "awaiting_data"},
    ]}
    current = [
        _row("daily_brief", "stale"),       # active -> stale: regression
        _row("eod_reconcile", "active"),    # unchanged
        _row("catering", "error"),          # any -> error: regression
        _row("flyer", "error"),             # error -> error: not new
        _row("compliance", "stale"),        # awaiting -> stale: not a regression
        _row("shift", "error"),             # no previous row: not a regression
    ]
    assert fr.compare(previous, current) == (fr.MODE_COMPARE, ["daily_brief", "catering"])


# ── 7-day reader over live log + archive ────────────────────────────────────

def _write_rows(path: Path, rows: list[dict], *, gz: bool = False) -> None:
    text = "".join(json.dumps(r) + "\n" for r in rows)
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _stamped(row: dict, ts: datetime, **over) -> dict:
    out = dict(row)
    out["ts"] = ts.isoformat()
    out.update(over)
    return out


def _set_mtime(path: Path, when: datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


@pytest.fixture
def fleet_box(tmp_path):
    """tmp box: config, state dir, live log + archive (one .gz, one plain rotated)."""
    rows = {r["type"]: r for r in _fixture_rows()}
    state = tmp_path / "state"
    state.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()
    config = tmp_path / "config.yaml"
    import yaml
    config.write_text(yaml.safe_dump(build_config_dict()), encoding="utf-8")

    gz = archive / "decisions.log-20261020.gz"
    _write_rows(gz, [
        _stamped(rows["raw_inbound"], NOW - timedelta(days=8)),       # before cutoff
        _stamped(rows["proposal_created"], NOW - timedelta(days=4)),
        _stamped(rows["cf_router_intercepted"], NOW - timedelta(days=4)),
    ], gz=True)
    _set_mtime(gz, NOW - timedelta(days=3))
    plain = archive / "decisions.log-20261022"
    _write_rows(plain, [
        _stamped(rows["raw_inbound"], NOW - timedelta(days=2)),
        _stamped(rows["brief_sent"], NOW - timedelta(days=2), brief_date="2026-10-21"),
        _stamped(rows["eod_snapshot"], NOW - timedelta(days=1, hours=5), eod_date="2026-10-21"),
    ])
    _set_mtime(plain, NOW - timedelta(days=1))
    old = archive / "decisions.log-20261001.gz"
    _write_rows(old, [_stamped(rows["proposal_created"], NOW - timedelta(days=3))], gz=True)
    _set_mtime(old, NOW - timedelta(days=20))  # mtime says: nothing in-window here
    live = logs / "decisions.log"
    _write_rows(live, [
        _stamped(rows["brief_skipped"], NOW - timedelta(hours=20), brief_date=BUSINESS_DATE),
        _stamped(rows["flyer_project_created"], NOW - timedelta(hours=10)),
        _stamped(rows["audit_emit_failed"], NOW - timedelta(hours=5)),
        _stamped(rows["raw_inbound"], NOW + timedelta(hours=1)),       # future: excluded
    ])
    return {"config": config, "state": state, "live": live, "archive": archive, "gz": gz}


def _no_systemctl(_args):
    return None


def test_reader_spans_gz_rotated_and_live_with_ts_cutoff(fleet_box):
    facts = fr.collect_facts(
        config_path=fleet_box["config"], state_dir=fleet_box["state"],
        decisions_log=fleet_box["live"], archive_dir=fleet_box["archive"],
        now=NOW, runner=_no_systemctl,
    )
    assert facts.config_error is None
    assert facts.type_counts["proposal_created"] == 1       # .gz in-window row only
    assert facts.type_counts["raw_inbound"] == 1            # 8d-old and future rows cut
    assert facts.type_counts["cf_router_intercepted"] == 1
    assert facts.type_counts["brief_sent"] == 1             # plain rotated file
    assert facts.type_counts["flyer_project_created"] == 1  # live file
    assert facts.audit_emit_failed_count == 1
    assert sorted(Path(p).name for p in facts.sources_read) == [
        "decisions.log", "decisions.log-20261020.gz", "decisions.log-20261022"]
    assert facts.sources_unreadable == []
    assert fr.TerminalRow("brief_skipped", BUSINESS_DATE, "already_sent") in facts.terminal_rows


def test_reader_records_corrupt_archive_and_keeps_going(fleet_box):
    bad = fleet_box["archive"] / "decisions.log-20261021.gz"
    bad.write_bytes(b"not gzip at all")
    _set_mtime(bad, NOW - timedelta(days=2))
    facts = fr.collect_facts(
        config_path=fleet_box["config"], state_dir=fleet_box["state"],
        decisions_log=fleet_box["live"], archive_dir=fleet_box["archive"],
        now=NOW, runner=_no_systemctl,
    )
    assert len(facts.sources_unreadable) == 1
    assert "decisions.log-20261021.gz" in facts.sources_unreadable[0]
    assert facts.live_log_unreadable is False
    assert facts.type_counts["brief_sent"] == 1  # other files still read


def test_runner_feeds_unit_state(fleet_box):
    calls = []

    def runner(args):
        calls.append(args)
        return (0, "enabled") if args[0] == "is-enabled" else (0, "active")

    facts = fr.collect_facts(
        config_path=fleet_box["config"], state_dir=fleet_box["state"],
        decisions_log=fleet_box["live"], archive_dir=fleet_box["archive"],
        now=NOW, runner=runner,
    )
    assert facts.units["send-daily-brief.timer"] == ("enabled", "active")
    assert ["is-enabled", "send-daily-brief.timer"] in calls


# ── fixtures are copied shapes ──────────────────────────────────────────────

def test_fixture_rows_are_valid_log_entries():
    rows = _fixture_rows()
    assert rows[0] == json.loads(
        (FIXTURE_ROWS.parent / "intake_bypass_audit_sample.jsonl").read_text(
            encoding="utf-8").splitlines()[0])
    for row in rows:
        entry = _ADAPTER.validate_python(row)
        if row["type"] == "audit_emit_failed":
            # schema lands in a sibling PR; until then it is a forward-compat row
            assert isinstance(entry, _UnknownLogEntry) or entry.type == "audit_emit_failed"
        else:
            assert not isinstance(entry, _UnknownLogEntry), row["type"]


# ── CLI end to end ──────────────────────────────────────────────────────────

def _fake_systemctl_path(box: dict) -> str:
    """A deterministic `systemctl` on PATH (CI runners have a real one with
    none of our units): every unit reports enabled/active."""
    bin_dir = box["state"].parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "systemctl"
    fake.write_text(
        '#!/bin/sh\ncase "$1" in is-enabled) echo enabled;; is-active) echo active;; esac\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _run_cli(box: dict, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--config", str(box["config"]), "--state-dir", str(box["state"]),
         "--decisions-log", str(box["live"]), "--archive-dir", str(box["archive"]),
         "--now", NOW.isoformat(), *extra],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "PATH": _fake_systemctl_path(box)},
    )


def test_cli_writes_report_and_one_valid_audit_row(fleet_box):
    before = fleet_box["live"].read_text(encoding="utf-8").splitlines()
    proc = _run_cli(fleet_box)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["mode"] == "baseline"
    assert report["regressions"] == []
    assert report["agents_total"] == 18
    assert sum(report["by_status"].values()) == 18
    assert report["by_status"]["unreachable_by_design"] == 8
    assert report["audit_emit_failed_count"] == 1

    last = fleet_box["state"] / "fleet-readiness-last.json"
    assert json.loads(last.read_text(encoding="utf-8"))["agents"] == report["agents"]

    after = fleet_box["live"].read_text(encoding="utf-8").splitlines()
    assert len(after) == len(before) + 1
    entry = _ADAPTER.validate_json(after[-1])
    assert isinstance(entry, FleetReadinessReport)
    assert entry.by_status == report["by_status"]
    assert entry.report_path == str(last)
    assert entry.generated_at.tzinfo is not None

    by_id = {a["agent_id"]: a for a in report["agents"]}
    assert by_id["daily_brief"]["status"] == "active"        # benign skip for 10-22
    # witness: the systemctl runner path actually ran (fake on PATH)
    assert "send-daily-brief.timer: enabled/active" in by_id["daily_brief"]["evidence"]
    assert by_id["eod_reconcile"]["status"] == "stale"       # snapshot only for 10-21
    assert by_id["shift"]["status"] == "active"
    assert by_id["catering_followup"]["status"] == "dormant_by_config"

    # Second run diffs against the first: compare mode, nothing regressed.
    proc2 = _run_cli(fleet_box)
    assert proc2.returncode == 0, proc2.stderr
    report2 = json.loads(proc2.stdout)
    assert (report2["mode"], report2["regressions"]) == ("compare", [])


def test_cli_no_write_is_a_dry_run(fleet_box):
    before = fleet_box["live"].read_bytes()
    proc = _run_cli(fleet_box, "--no-write", "--text")
    assert proc.returncode == 0, proc.stderr
    assert "Fleet readiness: 18 agents (mode=baseline" in proc.stdout
    assert fleet_box["live"].read_bytes() == before
    assert not (fleet_box["state"] / "fleet-readiness-last.json").exists()


def test_cli_surfaces_regression_against_previous_report(fleet_box):
    previous = {"agents": [{"agent_id": "eod_reconcile", "status": "active"}]}
    (fleet_box["state"] / "fleet-readiness-last.json").write_text(json.dumps(previous), encoding="utf-8")
    proc = _run_cli(fleet_box)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["regressions"] == ["eod_reconcile"]
    row = _ADAPTER.validate_json(fleet_box["live"].read_text(encoding="utf-8").splitlines()[-1])
    assert row.regressions == ["eod_reconcile"]


def test_cli_report_and_audit_row_carry_no_identifiers(fleet_box):
    # The fixture rows themselves DO carry LIDs, JIDs and +1 numbers.
    raw = FIXTURE_ROWS.read_text(encoding="utf-8")
    for pat in _PRIVACY_PATTERNS:
        assert pat.search(raw), f"fixture must contain {pat.pattern} for this test to mean anything"
    proc = _run_cli(fleet_box)
    assert proc.returncode == 0, proc.stderr
    report_text = (fleet_box["state"] / "fleet-readiness-last.json").read_text(encoding="utf-8")
    audit_row = fleet_box["live"].read_text(encoding="utf-8").splitlines()[-1]
    for blob in (report_text, audit_row, proc.stdout):
        for pat in _PRIVACY_PATTERNS:
            assert not pat.search(blob), f"identifier pattern {pat.pattern} leaked"
