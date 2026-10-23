"""fleet_readiness — read-only per-agent readiness model for the 18-agent fleet.

PR2 "Fleet Sentinel" (tasks/fleet-helper-agents-plan.md, 2026-10-23). One
declarative row per `src/agents/*` directory (`FLEET`) states HOW the agent is
reachable on this loadout and WHAT evidence proves it ran. `collect_facts()`
reads config.yaml, the state dir, systemd unit state and the last 7 days of
decisions.log (live + rotated archive); `evaluate()` turns one row + the facts
into a status; `compare()` diffs against the previous run.

Read-only: nothing here writes. The CLI (`fleet-readiness-report`) owns the
single report file + the single audit row.

Statuses:
  active                 enabled and reachable; for inbound-driven agents zero
                         traffic is still `active` (absence of traffic is not
                         failure — `traffic` carries the count).
  dormant_by_config      the enabled flag is off (or the agent's own terminal
                         row says `disabled`).
  awaiting_data          enabled, but the seed data it needs is absent.
  stale                  a timer agent missed its terminal row for a past
                         business date, logged a degraded skip, or its
                         heartbeat is older than its SLO.
  unreachable_by_design  skills-only agent: SKILL.md cannot execute on this
                         loadout (skills + terminal toolsets disabled).
  error                  config unreadable, the live audit log unreadable, or a
                         required timer not enabled/active while the flag is on.

Privacy: every evidence string is metadata (type names, counts, dates, unit and
file names, enum reasons). No row content — phones, LIDs, chat ids, message
text — is ever copied out of decisions.log.
"""
from __future__ import annotations

import gzip
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

REACHABILITY = ("cf_router_arm", "read_tool", "timer", "skill_only")
STATUSES = (
    "active",
    "dormant_by_config",
    "awaiting_data",
    "stale",
    "unreachable_by_design",
    "error",
)
# Report-level mode: the first run has no previous report to diff against.
MODE_BASELINE = "baseline"
MODE_COMPARE = "compare"

WINDOW_DAYS = 7
# Grace after a daily agent's scheduled time + catch-up window before a missing
# terminal row counts as stale (timer AccuracySec + run time).
TERMINAL_GRACE_MINUTES = 30
AUDIT_EMIT_FAILED_TYPE = "audit_emit_failed"  # schema lands in a sibling PR

DEFAULT_CONFIG_PATH = Path("/opt/shift-agent/config.yaml")
DEFAULT_STATE_DIR = Path("/opt/shift-agent/state")
DEFAULT_DECISIONS_LOG = Path("/opt/shift-agent/logs/decisions.log")
DEFAULT_ARCHIVE_DIR = Path("/var/log/shift-agent-archive")
ARCHIVE_GLOB = "decisions.log-*"
REPORT_FILENAME = "fleet-readiness-last.json"

# Skip reasons. `disabled` is benign but renders dormant_by_config. Any reason
# that is neither benign nor known-degraded is treated as degraded (fail loud).
DEGRADED_SKIP_REASONS = frozenset(
    {"catchup_expired", "data_unavailable", "dependency_down", "send_uncertain"}
)
_SAFE_TOKEN_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class DailyTerminal:
    """A timer agent that must leave exactly-one terminal row per business date."""
    date_field: str                 # row field carrying the business date
    done_types: tuple[str, ...]     # success terminal types
    skip_type: str                  # skip terminal type (carries `reason`)
    failure_types: tuple[str, ...]  # non-terminal failure evidence
    schedule_path: str              # dotted Config path of the local HH:MM
    catchup_path: str               # dotted Config path of catch-up minutes


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    reachability: str
    enabled_flag: Optional[str]                  # dotted Config path; None = always on
    state_files: tuple[str, ...] = ()            # REQUIRED seed files, relative to state dir
    timers: tuple[str, ...] = ()                 # systemd units that must be enabled+active
    expected_types: tuple[str, ...] = ()         # audit types that prove activity
    heartbeat: Optional[str] = None              # file relative to state dir
    heartbeat_max_age_hours: Optional[int] = None
    benign_skip_reasons: tuple[str, ...] = ()
    daily_terminal: Optional[DailyTerminal] = None
    seed_config_path: Optional[str] = None       # dotted Config path that must be non-empty


_BRIEF = DailyTerminal(
    date_field="brief_date",
    done_types=("brief_sent",),
    skip_type="brief_skipped",
    failure_types=("brief_send_failed",),
    schedule_path="daily_brief.brief_time",
    catchup_path="daily_brief.catchup_window_minutes",
)
_EOD = DailyTerminal(
    date_field="eod_date",
    done_types=("eod_snapshot",),
    skip_type="eod_skipped",
    failure_types=(),
    schedule_path="eod.eod_time",
    catchup_path="eod.catchup_window_minutes",
)

# One row per src/agents/* directory (tests/test_fleet_readiness.py pins this).
# Facts derived from code on 094a6583: cf-router arms (src/plugins/cf-router/
# hooks.py _pre_gateway_dispatch_impl), read tools (src/plugins/shift-agent-read/
# plugin.yaml), timers (src/agents/*/systemd, deploy.sh enable list), flags
# (schemas.Config).
FLEET: tuple[AgentSpec, ...] = (
    # ── inbound-driven: cf-router arms ────────────────────────────────────────
    AgentSpec(
        agent_id="shift",
        reachability="cf_router_arm",  # sick-call, candidate YES/NO, F8 shift codes
        enabled_flag=None,
        expected_types=("proposal_created", "proposal_status_change", "sick_call_send"),
    ),
    AgentSpec(
        agent_id="catering",
        reachability="cf_router_arm",  # F7 primary + F8 owner codes
        enabled_flag="catering.enabled",
        state_files=("catering-menu.json",),
        timers=("catering-lead-ttl-sweep.timer", "catering-proposal-expiry-sweep.timer"),
        expected_types=("catering_lead_created", "catering_owner_decision", "catering_quote_sent"),
    ),
    AgentSpec(
        agent_id="flyer",
        reachability="cf_router_arm",
        enabled_flag="flyer.enabled",
        timers=("flyer-source-edit-sla-watchdog.timer",),
        expected_types=("flyer_project_created", "flyer_status_change", "flyer_assets_delivered"),
    ),
    AgentSpec(
        agent_id="expense_bookkeeper",
        reachability="cf_router_arm",  # owner receipt intake
        enabled_flag="expense_bookkeeper.enabled",
        timers=("prune-expense-receipts.timer",),
        expected_types=("expense_receipt_received", "expense_owner_decision"),
    ),
    # ── inbound-driven: shift-agent-read tools ────────────────────────────────
    AgentSpec(
        agent_id="equipment_maintenance",
        reachability="read_tool",  # get_equipment_maintenance_due
        enabled_flag="equipment_maintenance.enabled",
        state_files=("equipment-items.json",),
        expected_types=("equipment_item_upserted", "equipment_issue_logged"),
    ),
    AgentSpec(
        agent_id="multi_location",
        reachability="read_tool",  # find_nearest_location
        enabled_flag="multi_location.enabled",
        seed_config_path="multi_location.locations",
        expected_types=("multi_location_closest_lookup", "cross_location_query"),
    ),
    # ── timer-driven ──────────────────────────────────────────────────────────
    AgentSpec(
        agent_id="daily_brief",
        reachability="timer",
        enabled_flag="daily_brief.enabled",
        timers=("send-daily-brief.timer",),
        expected_types=("brief_sent", "brief_skipped", "brief_send_failed"),
        benign_skip_reasons=("already_sent", "disabled"),
        daily_terminal=_BRIEF,
    ),
    AgentSpec(
        agent_id="eod_reconcile",
        reachability="timer",
        enabled_flag="eod.enabled",
        timers=("eod-reconcile.timer",),
        expected_types=("eod_snapshot", "eod_skipped", "eod_pushover_sent"),
        benign_skip_reasons=("already_done", "disabled"),
        daily_terminal=_EOD,
    ),
    AgentSpec(
        agent_id="compliance",
        reachability="timer",  # check-compliance-deadlines (+ read tool, default-off mark-done arm)
        enabled_flag="compliance.enabled",
        state_files=("compliance-items.json",),
        timers=("check-compliance-deadlines.timer",),
        expected_types=(
            "compliance_reminder_sent", "compliance_reminder_failed",
            "compliance_reminder_deferred", "compliance_item_marked_done",
            "compliance_item_upserted",
        ),
        heartbeat="compliance-last-cron-tick.json",
        heartbeat_max_age_hours=28,  # check-compliance-deadlines HEARTBEAT_STALE_THRESHOLD_HOURS
    ),
    AgentSpec(
        # Dormant engine: catering_followups.py + catering-followup-sweep.timer
        # behind default-off gates (deploy deliberately leaves the timer off).
        agent_id="catering_followup",
        reachability="timer",
        enabled_flag="catering_followup.enabled",
        timers=("catering-followup-sweep.timer",),
        expected_types=(
            "catering_followup_scheduled", "catering_followup_card_sent",
            "catering_followup_sent",
        ),
    ),
    # ── skills-only: SKILL.md cannot execute on this loadout ──────────────────
    AgentSpec(agent_id="cash_ar", reachability="skill_only", enabled_flag="cash_ar.enabled"),
    AgentSpec(agent_id="employee_docs", reachability="skill_only", enabled_flag="employee_docs.enabled"),
    AgentSpec(agent_id="hiring", reachability="skill_only", enabled_flag="hiring.enabled"),
    AgentSpec(agent_id="inventory", reachability="skill_only", enabled_flag="inventory.enabled"),
    AgentSpec(
        agent_id="pnl_anomaly", reachability="skill_only", enabled_flag="pnl_anomaly.enabled",
        expected_types=("pnl_anomaly_detected", "pnl_anomaly_declined"),
    ),
    AgentSpec(agent_id="sales_tax", reachability="skill_only", enabled_flag="sales_tax.enabled"),
    AgentSpec(agent_id="supplier", reachability="skill_only", enabled_flag="supplier.enabled"),
    AgentSpec(agent_id="vip", reachability="skill_only", enabled_flag="vip.enabled"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Facts
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TerminalRow:
    type: str
    date: str
    reason: Optional[str]


@dataclass
class FleetFacts:
    now: datetime
    config: Any = None                               # schemas.Config or None
    config_error: Optional[str] = None
    state_files_present: dict[str, bool] = field(default_factory=dict)
    heartbeat_age_hours: dict[str, Optional[float]] = field(default_factory=dict)
    # unit -> (is-enabled, is-active); None = systemctl unavailable
    units: dict[str, Optional[tuple[str, str]]] = field(default_factory=dict)
    type_counts: Counter = field(default_factory=Counter)
    terminal_rows: list[TerminalRow] = field(default_factory=list)
    audit_emit_failed_count: int = 0
    sources_read: list[str] = field(default_factory=list)
    sources_unreadable: list[str] = field(default_factory=list)
    live_log_unreadable: bool = False


Runner = Callable[[list[str]], Optional[tuple[int, str]]]


def systemctl_runner(args: list[str]) -> Optional[tuple[int, str]]:
    """Default runner. None means systemctl is unavailable (not an error)."""
    try:
        proc = subprocess.run(
            ["systemctl", *args], capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.returncode, proc.stdout.strip()


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _open_log(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def window_log_files(
    live_log: Path, archive_dir: Optional[Path], since: datetime,
) -> list[Path]:
    """Live log + every rotated archive file that can hold a row at/after `since`.

    A rotated file's mtime is its last write, so a file older than `since`
    holds only older rows and is skipped without being opened. gzip keeps the
    source mtime; if it did not, the file is merely read (safe direction).
    """
    files: list[Path] = []
    if archive_dir is not None:
        try:
            candidates = sorted(archive_dir.glob(ARCHIVE_GLOB))
        except OSError:
            candidates = []
        cutoff = since.timestamp()
        for p in candidates:
            try:
                if p.stat().st_mtime < cutoff:
                    continue
            except OSError:
                pass  # unreadable metadata -> still try; failure is recorded
            files.append(p)
    files.append(live_log)
    return files


def iter_window_rows(
    files: list[Path], since: datetime, until: datetime,
    *, read: list[str], unreadable: list[str],
) -> Iterator[dict[str, Any]]:
    """Stream rows with since <= ts <= until. Bad lines are skipped; an
    unreadable/corrupt file is recorded (path + error class) and skipped."""
    for path in files:
        if not path.exists():
            continue
        try:
            with _open_log(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    ts = _parse_ts(row.get("ts"))
                    if ts is None or ts < since or ts > until:
                        continue
                    yield row
            read.append(str(path))
        except (OSError, EOFError, UnicodeError, gzip.BadGzipFile) as exc:
            unreadable.append(f"{path} ({type(exc).__name__})")


def _resolve(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _terminal_specs() -> dict[str, DailyTerminal]:
    out: dict[str, DailyTerminal] = {}
    for spec in FLEET:
        dt = spec.daily_terminal
        if dt is None:
            continue
        for t in (*dt.done_types, dt.skip_type, *dt.failure_types):
            out[t] = dt
    return out


def collect_facts(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_dir: Path = DEFAULT_STATE_DIR,
    decisions_log: Path = DEFAULT_DECISIONS_LOG,
    archive_dir: Optional[Path] = DEFAULT_ARCHIVE_DIR,
    now: Optional[datetime] = None,
    runner: Runner = systemctl_runner,
    fleet: tuple[AgentSpec, ...] = FLEET,
    window_days: int = WINDOW_DAYS,
) -> FleetFacts:
    now = (now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    facts = FleetFacts(now=now)

    # config.yaml via the canonical schema
    try:
        import yaml
        from schemas import Config
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        facts.config = Config.model_validate(raw)
    except FileNotFoundError:
        facts.config_error = "config.yaml missing"
    except Exception as exc:  # noqa: BLE001 - any failure is an `error` fact
        facts.config_error = f"config.yaml invalid ({type(exc).__name__})"

    # state dir listing (existence only; contents are never read)
    for spec in fleet:
        for rel in spec.state_files:
            facts.state_files_present[rel] = (state_dir / rel).is_file()
        if spec.heartbeat:
            hb = state_dir / spec.heartbeat
            try:
                age = (now.timestamp() - hb.stat().st_mtime) / 3600.0
                facts.heartbeat_age_hours[spec.heartbeat] = max(age, 0.0)
            except OSError:
                facts.heartbeat_age_hours[spec.heartbeat] = None

    # systemd unit state
    for spec in fleet:
        for unit in spec.timers:
            if unit in facts.units:
                continue
            enabled = runner(["is-enabled", unit])
            active = runner(["is-active", unit])
            if enabled is None or active is None:
                facts.units[unit] = None
            else:
                facts.units[unit] = (enabled[1] or "unknown", active[1] or "unknown")

    # decisions.log 7-day window (live + archive)
    since = now - timedelta(days=window_days)
    terminal_by_type = _terminal_specs()
    files = window_log_files(decisions_log, archive_dir, since)
    for row in iter_window_rows(
        files, since, now, read=facts.sources_read, unreadable=facts.sources_unreadable,
    ):
        rtype = row.get("type")
        if not isinstance(rtype, str):
            continue
        facts.type_counts[rtype] += 1
        if rtype == AUDIT_EMIT_FAILED_TYPE:
            facts.audit_emit_failed_count += 1
        dt = terminal_by_type.get(rtype)
        if dt is not None:
            date = row.get(dt.date_field)
            if isinstance(date, str) and _DATE_RE.match(date):
                reason = row.get("reason") if rtype == dt.skip_type else None
                facts.terminal_rows.append(TerminalRow(
                    type=rtype, date=date,
                    reason=reason if isinstance(reason, str) else None,
                ))
    if any(u.startswith(f"{decisions_log} (") for u in facts.sources_unreadable):
        facts.live_log_unreadable = True
    return facts


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentStatus:
    agent_id: str
    reachability: str
    status: str
    reason: str
    traffic: int = 0
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "reachability": self.reachability,
            "status": self.status,
            "reason": self.reason,
            "traffic": self.traffic,
            "evidence": list(self.evidence),
        }


def _safe_token(value: Optional[str]) -> str:
    if value and _SAFE_TOKEN_RE.match(value):
        return value
    return "unrecognized"


def expected_business_date(
    now: datetime, tz: Any, schedule_hhmm: str, catchup_minutes: int,
) -> str:
    """Latest local business date whose scheduled run + catch-up + grace is past."""
    now_local = now.astimezone(tz)
    hh, mm = (int(x) for x in schedule_hhmm.split(":"))
    for back in range(0, 3):
        day = now_local.date() - timedelta(days=back)
        deadline = datetime.combine(day, dtime(hh, mm), tzinfo=tz) + timedelta(
            minutes=catchup_minutes + TERMINAL_GRACE_MINUTES,
        )
        if deadline <= now_local:
            return day.isoformat()
    return (now_local.date() - timedelta(days=2)).isoformat()


def _evaluate_daily_terminal(
    spec: AgentSpec, facts: FleetFacts,
) -> tuple[str, str, list[str]]:
    dt = spec.daily_terminal
    assert dt is not None
    cfg = facts.config
    date = expected_business_date(
        facts.now, cfg.tz(), _resolve(cfg, dt.schedule_path), _resolve(cfg, dt.catchup_path),
    )
    rows = [r for r in facts.terminal_rows if r.date == date and
            (r.type in dt.done_types or r.type == dt.skip_type or r.type in dt.failure_types)]
    evidence = [f"business_date={date}"]
    done = [r for r in rows if r.type in dt.done_types]
    skips = [r for r in rows if r.type == dt.skip_type]
    failures = [r for r in rows if r.type in dt.failure_types]
    if done:
        evidence.append(f"{done[0].type} for {date}")
        return "active", f"{done[0].type} for {date}", evidence
    reasons = sorted({_safe_token(r.reason) for r in skips})
    benign = [x for x in reasons if x in spec.benign_skip_reasons and x != "disabled"]
    if benign:
        evidence.append(f"{dt.skip_type}{{{','.join(reasons)}}} for {date}")
        return "active", f"{dt.skip_type}:{benign[0]} for {date}", evidence
    if reasons == ["disabled"]:
        evidence.append(f"{dt.skip_type}{{disabled}} for {date}")
        return "dormant_by_config", f"{dt.skip_type}:disabled for {date}", evidence
    degraded = [x for x in reasons if x != "disabled"]
    if degraded:
        evidence.append(f"{dt.skip_type}{{{','.join(reasons)}}} for {date}")
        return "stale", f"{dt.skip_type}:{degraded[0]} for {date}", evidence
    if failures:
        evidence.append(f"{len(failures)} {failures[0].type} for {date}")
        return "stale", f"{failures[0].type} for {date} with no terminal row", evidence
    return "stale", f"no terminal row for {date}", evidence


def evaluate(spec: AgentSpec, facts: FleetFacts) -> AgentStatus:
    traffic = sum(facts.type_counts.get(t, 0) for t in spec.expected_types)
    evidence: list[str] = []
    if spec.expected_types:
        evidence.append(f"traffic_{WINDOW_DAYS}d={traffic}")

    def out(status: str, reason: str, extra: Optional[list[str]] = None) -> AgentStatus:
        return AgentStatus(
            agent_id=spec.agent_id, reachability=spec.reachability, status=status,
            reason=reason, traffic=traffic, evidence=evidence + (extra or []),
        )

    if spec.reachability == "skill_only":
        flag = None
        if facts.config is not None and spec.enabled_flag:
            try:
                flag = bool(_resolve(facts.config, spec.enabled_flag))
            except AttributeError:
                flag = None
        if flag is not None:
            evidence.append(f"{spec.enabled_flag}={str(flag).lower()}")
        return out("unreachable_by_design", "skills-only: no deterministic execution path")

    if facts.config is None:
        return out("error", facts.config_error or "config.yaml unavailable")

    if spec.enabled_flag:
        try:
            enabled = bool(_resolve(facts.config, spec.enabled_flag))
        except AttributeError:
            return out("error", f"unknown config flag {spec.enabled_flag}")
        evidence.append(f"{spec.enabled_flag}={str(enabled).lower()}")
        if not enabled:
            return out("dormant_by_config", f"{spec.enabled_flag} is false")

    for unit in spec.timers:
        state = facts.units.get(unit)
        if state is None:
            evidence.append(f"{unit}: systemctl unavailable")
            continue
        is_enabled, is_active = state
        evidence.append(f"{unit}: {is_enabled}/{is_active}")
        if is_enabled not in ("enabled", "static", "enabled-runtime") or is_active != "active":
            return out("error", f"{unit} is {is_enabled}/{is_active} while enabled")

    missing = [rel for rel in spec.state_files if not facts.state_files_present.get(rel, False)]
    if missing:
        return out("awaiting_data", f"seed data missing: {', '.join(missing)}")
    if spec.seed_config_path:
        seed = _resolve(facts.config, spec.seed_config_path)
        evidence.append(f"{spec.seed_config_path}: {len(seed) if seed else 0} configured")
        if not seed:
            return out("awaiting_data", f"{spec.seed_config_path} is empty")

    if spec.daily_terminal is not None:
        if facts.live_log_unreadable:
            return out("error", "decisions.log unreadable")
        status, reason, extra = _evaluate_daily_terminal(spec, facts)
        return out(status, reason, extra)

    if spec.heartbeat:
        age = facts.heartbeat_age_hours.get(spec.heartbeat)
        if age is None:
            return out("stale", f"heartbeat {spec.heartbeat} missing")
        evidence.append(f"heartbeat_age_h={age:.1f}")
        if spec.heartbeat_max_age_hours is not None and age > spec.heartbeat_max_age_hours:
            return out("stale", f"heartbeat {spec.heartbeat} older than {spec.heartbeat_max_age_hours}h")

    if spec.reachability in ("cf_router_arm", "read_tool"):
        return out("active", f"reachable; traffic={traffic}")
    return out("active", "enabled and scheduled")


# ─────────────────────────────────────────────────────────────────────────────
# Report + comparison
# ─────────────────────────────────────────────────────────────────────────────

def compare(previous: Optional[dict[str, Any]], current: list[AgentStatus]) -> tuple[str, list[str]]:
    """(mode, regressions). Regression = active -> stale|error, or any -> error.
    No usable previous report -> baseline mode, no regressions."""
    if not isinstance(previous, dict) or not isinstance(previous.get("agents"), list):
        return MODE_BASELINE, []
    prev = {
        a.get("agent_id"): a.get("status")
        for a in previous["agents"] if isinstance(a, dict)
    }
    regressions: list[str] = []
    for row in current:
        before = prev.get(row.agent_id)
        if before is None:
            continue
        if before == "active" and row.status in ("stale", "error"):
            regressions.append(row.agent_id)
        elif row.status == "error" and before != "error":
            regressions.append(row.agent_id)
    return MODE_COMPARE, regressions


def build_report(
    facts: FleetFacts,
    previous: Optional[dict[str, Any]],
    fleet: tuple[AgentSpec, ...] = FLEET,
) -> dict[str, Any]:
    rows = [evaluate(spec, facts) for spec in fleet]
    mode, regressions = compare(previous, rows)
    by_status = {s: 0 for s in STATUSES}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "generated_at": facts.now.isoformat(),
        "mode": mode,
        "window_days": WINDOW_DAYS,
        "agents_total": len(rows),
        "by_status": by_status,
        "regressions": regressions,
        "audit_emit_failed_count": facts.audit_emit_failed_count,
        "sources": {
            "read": list(facts.sources_read),
            "unreadable": list(facts.sources_unreadable),
        },
        "agents": [r.to_dict() for r in rows],
    }


def render_text(report: dict[str, Any]) -> str:
    counts = "  ".join(f"{k}={v}" for k, v in report["by_status"].items() if v)
    lines = [
        f"Fleet readiness: {report['agents_total']} agents (mode={report['mode']}, "
        f"window={report['window_days']}d)",
        counts,
        "Regressions: " + (", ".join(report["regressions"]) or "none"),
        f"audit_emit_failed rows: {report['audit_emit_failed_count']}",
    ]
    if report["sources"]["unreadable"]:
        lines.append("Unreadable audit files: " + "; ".join(report["sources"]["unreadable"]))
    lines.append("")
    for a in report["agents"]:
        lines.append(
            f"{a['status'].upper():<22} {a['agent_id']:<22} {a['reachability']:<14} {a['reason']}"
        )
    return "\n".join(lines)
