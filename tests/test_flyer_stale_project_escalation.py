"""Stale customer-turn flyer projects must reach the owner.

The worker-unavailable escalation arm (tests/test_flyer_recovery_watchdog.py)
only fires for projects that ALREADY have a recovery incident. A project parked
in a CUSTOMER-turn status never opens one: `classify_stale_manual_project` gates
on ``status == "manual_edit_required"`` (src/agents/flyer/recovery.py), and the
only other incident source is a failing audit row inside the 240-minute scan
window. A project that simply stops moving emits neither.

Live proof at the time of writing (main-vps, 2026-08-22): F0217 and F0222 sat in
``awaiting_final_approval`` for 42 days -- 6x the 168h TTL that
``ttl_observe.NON_DELIVERED_TTL_HOURS`` already declares for that status -- with
zero incidents, zero owner alerts, and no timer anywhere on the box running the
TTL-0 observer that knows those TTLs. Meanwhile each held its sender inside the
cf-router active-project intercept, the same condition that swallowed a live
catering inquiry in the 2026-07-20 P1-1 incident.

These tests pin the escalation arm that closes that gap.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "flyer-recovery-watchdog"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "platform"))

from agents.flyer import recovery  # noqa: E402
from agents.flyer import ttl_observe  # noqa: E402


# Mirrors the deployed main-vps shape: worker_draft mode with worker_auto_run
# false, i.e. the configuration under which nothing auto-repairs anything.
_STALE_TURN_CONFIG = """
schema_version: 1
customer: {name: Triveni, location_id: loc_pineville_01, timezone: America/New_York}
owner: {name: Owner, phone: '+19045550000'}
limits: {}
alerting: {pushover_user_key: k, pushover_app_token: t}
backup: {gpg_recipient_email: owner@example.com}
flyer:
  enabled: true
  recovery:
    mode: worker_draft
    enable_timer: true
    scan_window_minutes: 240
    operator_escalation_stale_minutes: 30
    worker_auto_run: false
"""


def _parked_project(project_id: str, status: str, *, age_days: float) -> dict:
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(days=age_days)).isoformat().replace("+00:00", "Z")
    created = (now - timedelta(days=age_days + 0.1)).isoformat().replace("+00:00", "Z")
    return {
        "project_id": project_id,
        "customer_id": "CUST0007",
        "status": status,
        "chat_id": "15550100077@s.whatsapp.net",
        "created_at": created,
        "updated_at": stamp,
    }


class _Harness:
    """One tmp_path watchdog installation the test can run repeatedly.

    Re-running matters: the timer fires every 5 minutes in production, so
    "does it alert once or 288 times a day" is a property of repeated runs.
    """

    def __init__(self, tmp_path: Path, config_body: str = _STALE_TURN_CONFIG) -> None:
        self.log = tmp_path / "decisions.log"
        self.projects = tmp_path / "projects.json"
        self.recovery_state = tmp_path / "recovery.json"
        config = tmp_path / "config.yaml"
        customers = tmp_path / "customers.json"
        config.write_text(config_body.strip(), encoding="utf-8")
        self.log.write_text("", encoding="utf-8")
        customers.write_text(
            '{"customers":[],"onboarding_sessions":[],"intake_sessions":[]}', encoding="utf-8"
        )
        self.projects.write_text('{"projects":[]}', encoding="utf-8")
        self._argv = [
            sys.executable, str(SCRIPT),
            "--config-path", str(config),
            "--log-path", str(self.log),
            "--project-state-path", str(self.projects),
            "--customer-state-path", str(customers),
            "--recovery-state-path", str(self.recovery_state),
            "--bundle-dir", str(tmp_path / "bundles"),
            "--worker-queue-dir", str(tmp_path / "queue"),
            "--text",
        ]

    def seed(self, rows: list[dict]) -> None:
        self.projects.write_text(json.dumps({"projects": rows}), encoding="utf-8")

    def run(self) -> subprocess.CompletedProcess:
        result = subprocess.run(self._argv, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return result

    def incidents(self) -> list[dict]:
        if not self.recovery_state.exists():
            return []
        return json.loads(self.recovery_state.read_text(encoding="utf-8")).get("incidents", [])

    def rows(self, row_type: str | None = None) -> list[dict]:
        rows = [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [r for r in rows if row_type is None or r.get("type") == row_type]


def test_stale_customer_turn_project_escalates_to_owner(tmp_path):
    """F0217 reproducer: 42 days in awaiting_final_approval must page the owner."""
    harness = _Harness(tmp_path)
    harness.seed([_parked_project("F0217", "awaiting_final_approval", age_days=42)])

    harness.run()

    incidents = harness.incidents()
    assert len(incidents) == 1, f"no incident opened for a 42-day parked project: {incidents}"
    incident = incidents[0]
    assert incident["project_id"] == "F0217"
    assert incident["status"] == "operator_action_required"
    assert incident["operator_action"]["reason"] == "stale_customer_turn"

    escalations = harness.rows("flyer_recovery_stale_project_escalated")
    assert len(escalations) == 1, f"no escalation row: {[r['type'] for r in harness.rows()]}"
    assert escalations[0]["project_id"] == "F0217"
    assert escalations[0]["project_status"] == "awaiting_final_approval"
    assert escalations[0]["ttl_hours"] == 168

    alerts = harness.rows("flyer_recovery_owner_alert")
    assert [(r["trigger"], r["reason"]) for r in alerts] == [
        ("operator_action_required", "stale_customer_turn")
    ]


def test_stale_customer_turn_escalation_does_not_queue_repair_worker(tmp_path):
    """A customer who has not replied is not a defect the repair worker can fix.

    Queueing one would burn the single per-run worker slot on an unfixable
    incident and starve a real failure behind it.
    """
    harness = _Harness(tmp_path)
    harness.seed([_parked_project("F0217", "awaiting_final_approval", age_days=42)])

    result = harness.run()

    assert "queued=0" in result.stdout
    assert "worker_runs=0" in result.stdout
    assert harness.incidents()[0]["codex"]["status"] == "none"


def test_project_within_ttl_is_not_escalated(tmp_path):
    """A 6-day-old awaiting_final_approval project is still legitimately the
    customer's turn -- the 168h TTL is the contract, not "any non-terminal"."""
    harness = _Harness(tmp_path)
    harness.seed([_parked_project("F0300", "awaiting_final_approval", age_days=6)])

    harness.run()

    assert harness.incidents() == []
    assert harness.rows("flyer_recovery_stale_project_escalated") == []


def test_terminal_projects_are_never_escalated(tmp_path):
    """Positive control for the negative case: the 223 completed/closed projects
    on the live box must not produce 223 owner pages on the first run."""
    harness = _Harness(tmp_path)
    harness.seed([
        _parked_project("F0001", "completed", age_days=200),
        _parked_project("F0002", "closed_no_send", age_days=200),
        _parked_project("F0003", "delivered_with_warning", age_days=200),
    ])

    harness.run()

    assert harness.incidents() == []
    assert harness.rows("flyer_recovery_stale_project_escalated") == []


def test_machine_active_statuses_are_left_to_the_existing_arms(tmp_path):
    """manual_edit_required is owned by the manual queue + source-edit SLA
    watchdog, and generating_concepts / revising_design / finalizing_assets are
    machine-active. Escalating them here would double-page the owner."""
    harness = _Harness(tmp_path)
    harness.seed([
        _parked_project("F0400", "generating_concepts", age_days=200),
        _parked_project("F0401", "revising_design", age_days=200),
        _parked_project("F0402", "finalizing_assets", age_days=200),
    ])

    harness.run()

    assert harness.rows("flyer_recovery_stale_project_escalated") == []


def test_stale_customer_turn_alerts_once_not_every_five_minutes(tmp_path):
    """The timer fires every 5 minutes; the owner must be paged once per project."""
    harness = _Harness(tmp_path)
    harness.seed([_parked_project("F0217", "awaiting_final_approval", age_days=42)])

    for _ in range(3):
        harness.run()

    escalations = harness.rows("flyer_recovery_stale_project_escalated")
    assert len(escalations) == 1, f"escalated {len(escalations)} times across 3 runs"


def test_stale_customer_turn_incident_resolves_when_project_moves_on(tmp_path):
    """Once the customer replies (or the operator closes it) the incident must
    clear, or the owner's queue accumulates rows nothing can ever retire."""
    harness = _Harness(tmp_path)
    harness.seed([_parked_project("F0217", "awaiting_final_approval", age_days=42)])
    harness.run()
    assert harness.incidents()[0]["status"] == "operator_action_required"

    harness.seed([_parked_project("F0217", "closed_no_send", age_days=42)])
    harness.run()

    incident = harness.incidents()[0]
    assert incident["status"] == "resolved"
    assert incident["resolution"] == "project_left_stale_status"


def test_reescalates_if_the_project_parks_again(tmp_path):
    """Resolution must not be a permanent mute.

    A project that goes stale, is dealt with, then goes stale a SECOND time is a
    second incident the owner still needs. Real time cannot be advanced inside a
    test, so the first cycle is aged by backdating the resolution: the second
    parking's last activity (30d ago) is genuinely later than the first cycle's
    resolution (60d ago), which is exactly the ordering merge_signals requires.
    """
    harness = _Harness(tmp_path)
    harness.seed([_parked_project("F0217", "awaiting_final_approval", age_days=30)])
    harness.run()
    first = harness.incidents()
    assert len(first) == 1 and first[0]["status"] == "operator_action_required"

    state = json.loads(harness.recovery_state.read_text(encoding="utf-8"))
    state["incidents"][0]["status"] = "resolved"
    state["incidents"][0]["resolution"] = "project_left_stale_status"
    state["incidents"][0]["resolved_at"] = (
        datetime.now(timezone.utc) - timedelta(days=60)
    ).isoformat()
    harness.recovery_state.write_text(json.dumps(state), encoding="utf-8")

    harness.run()

    open_again = [i for i in harness.incidents() if i["status"] == "operator_action_required"]
    assert len(open_again) == 1, "a project that parks a second time never pages the owner again"
    assert open_again[0]["incident_id"] != first[0]["incident_id"]


def test_corrupt_timestamps_do_not_crash_or_silently_pass(tmp_path):
    """A row whose timestamps are missing/unparseable must not be inferred into
    eligibility, and must not take the whole sweep down with it."""
    harness = _Harness(tmp_path)
    broken = _parked_project("F0500", "awaiting_final_approval", age_days=42)
    broken["updated_at"] = "not-a-timestamp"
    missing = _parked_project("F0501", "awaiting_final_approval", age_days=42)
    missing.pop("updated_at")
    good = _parked_project("F0217", "awaiting_final_approval", age_days=42)

    harness.seed([broken, missing, good])
    harness.run()

    escalated = {r["project_id"] for r in harness.rows("flyer_recovery_stale_project_escalated")}
    assert escalated == {"F0217"}


def test_ttl_table_matches_ttl_observe_exactly():
    """recovery duplicates ttl_observe's TTLs on purpose (ttl_observe is not
    installed on the box, so importing it would crash the live watchdog). This
    is the guard that makes the duplication safe: the two tables must stay
    byte-identical, and every status one module monitors the other must not
    silently exclude."""
    assert recovery.STALE_CUSTOMER_TURN_TTL_HOURS == ttl_observe.NON_DELIVERED_TTL_HOURS
    assert recovery.STALE_CUSTOMER_TURN_CLOCK_SKEW.total_seconds() / 3600.0 == ttl_observe.CLOCK_SKEW_HOURS
    overlap = set(recovery.STALE_CUSTOMER_TURN_TTL_HOURS) & set(ttl_observe.EXCLUDED_STATUSES)
    assert overlap == set(), f"status both monitored and excluded: {overlap}"


def test_last_activity_agrees_with_ttl_observe_over_a_generated_matrix():
    """Metamorphic parity for the duplicated timestamp logic: across a matrix of
    well-formed, missing and corrupt timestamp shapes, recovery's trustworthiness
    verdict must match ttl_observe's disposition. Divergence here is how a
    duplicated helper silently drifts into escalating rows a human should see."""
    now = datetime.now(timezone.utc)
    ok = (now - timedelta(days=40)).isoformat().replace("+00:00", "Z")
    later = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    future = (now + timedelta(days=5)).isoformat().replace("+00:00", "Z")
    before_creation = (now - timedelta(days=90)).isoformat().replace("+00:00", "Z")

    shapes: list[dict] = []
    for updated in (ok, "garbage", "", None):
        for created in (ok, "garbage", None):
            for extra in (
                {},
                {"assets": [{"delivered_at": later}]},
                {"assets": [{"delivered_at": "garbage"}]},
                {"manual_review": {"queued_at": later}},
                {"manual_review": {"queued_at": "garbage"}},
                {"assets": [{"delivered_at": before_creation}]},
                {"updated_at_future": True},
            ):
                row = {
                    "project_id": "F9999",
                    "status": "awaiting_final_approval",
                    "created_at": created,
                    "updated_at": future if extra.get("updated_at_future") else updated,
                }
                row.update({k: v for k, v in extra.items() if k != "updated_at_future"})
                if created is None:
                    row.pop("created_at")
                if row.get("updated_at") is None:
                    row.pop("updated_at", None)
                shapes.append(row)

    for row in shapes:
        mine = recovery._stale_turn_last_activity(row, now=now)
        theirs, integrity = ttl_observe._timestamp_disposition(row, as_of=now)
        trusted_theirs = integrity is None and theirs is not None
        assert (mine is not None) == trusted_theirs, (
            f"trust verdict diverged for {row}: recovery={mine} ttl_observe=({theirs}, {integrity})"
        )
        if mine is not None:
            assert mine == theirs, f"last_activity diverged for {row}: {mine} != {theirs}"


def test_every_ttl_status_in_the_shared_table_is_escalatable():
    """Property-style over the enumerable rule class. The TTL table in
    ttl_observe is the single source of truth; adding a status there must not
    silently create a blind spot here, and the boundary must hold on both sides.
    """
    now = datetime.now(timezone.utc)
    assert ttl_observe.NON_DELIVERED_TTL_HOURS, "TTL table is empty -- nothing is monitored"

    for status, ttl_hours in ttl_observe.NON_DELIVERED_TTL_HOURS.items():
        stale = recovery.classify_stale_customer_turn_project(
            _parked_project("F9999", status, age_days=(ttl_hours / 24.0) + 1), now=now
        )
        assert stale is not None, f"status {status} (ttl={ttl_hours}h) is not escalatable"
        assert stale.failure_class == recovery.STALE_CUSTOMER_TURN_FAILURE_CLASS
        assert stale.project_id == "F9999"

        fresh = recovery.classify_stale_customer_turn_project(
            _parked_project("F9999", status, age_days=(ttl_hours / 24.0) - 0.5), now=now
        )
        assert fresh is None, f"status {status} escalated before its {ttl_hours}h TTL"


def test_excluded_statuses_stay_excluded():
    """The complement of the TTL table. Every status ttl_observe declares
    EXCLUDED must classify to None, so the two modules cannot drift apart."""
    now = datetime.now(timezone.utc)
    for status in ttl_observe.EXCLUDED_STATUSES:
        signal = recovery.classify_stale_customer_turn_project(
            _parked_project("F9999", status, age_days=400), now=now
        )
        assert signal is None, f"excluded status {status} was escalated"
