#!/usr/bin/env python3
"""Render a repo-backed daily operator brief.

The brief is intentionally read-only. It summarizes existing sources of truth
instead of creating a second task system or probing production hosts directly.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


SECTION_NAMES = {
    "needs your decision": "needs_decision",
    "waiting on you": "waiting_on_you",
    "active risks": "active_risks",
    "handoffs and promises": "handoffs",
    "parking lot": "parking_lot",
}
SECRET_ASSIGNMENT_RE = re.compile(r"\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET)\s*=\s*['\"]?[^'\"\s,;]+", re.I)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I)
ACCESS_TOKEN_RE = re.compile(r"\b(?:access_token|refresh_token)\s*[:=]\s*['\"]?[^'\"\s,;]+", re.I)
SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
E164_RE = re.compile(r"\+\d{10,15}\b")
US_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}|\d{10})(?!\w)")
CHAT_ID_RE = re.compile(r"\b[\w.+-]+@(?:s\.whatsapp\.net|lid)\b")
UNIX_ABS_PATH_RE = re.compile(r"(?<!\w)/(?:opt|var|tmp|home|root|Users)/[^\s,'\")]+")
WINDOWS_ABS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s,'\")]+")


@dataclass
class OperatorDecisions:
    needs_decision: list[str] = field(default_factory=list)
    waiting_on_you: list[str] = field(default_factory=list)
    active_risks: list[str] = field(default_factory=list)
    handoffs: list[str] = field(default_factory=list)
    parking_lot: list[str] = field(default_factory=list)
    missing: bool = False


@dataclass
class GitSummary:
    branch: str = "unknown"
    changes: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class Brief:
    generated_date: str
    decisions: OperatorDecisions
    todo_signals: list[str]
    fleet_lines: list[str]
    flyer_train_lines: list[str]
    flyer_evaluation_lines: list[str]
    fleet_normalization_lines: list[str]
    automation_lines: list[str]
    git: GitSummary | None


def normalize_heading(raw: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", raw.strip().lower()).strip()


def clean_checklist_item(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("-"):
        return None
    match = re.match(r"^-\s+\[([ xX])\]\s+(.*)$", stripped)
    if match:
        if match.group(1).lower() == "x":
            return None
        return match.group(2).strip()
    return stripped[1:].strip() or None


def clean_open_checklist_item(line: str) -> str | None:
    match = re.match(r"^\s*-\s+\[\s\]\s+(.*)$", line)
    if not match:
        return None
    return match.group(1).strip() or None


def redact_text(text: str) -> str:
    value = str(text)
    value = SECRET_ASSIGNMENT_RE.sub("[redacted-secret]", value)
    value = BEARER_RE.sub("Bearer [redacted-secret]", value)
    value = ACCESS_TOKEN_RE.sub("[redacted-token]", value)
    value = SK_KEY_RE.sub("[redacted-secret]", value)
    value = CHAT_ID_RE.sub("[redacted-chat-id]", value)
    value = E164_RE.sub("[redacted-phone]", value)
    value = US_PHONE_RE.sub("[redacted-phone]", value)
    value = UNIX_ABS_PATH_RE.sub(lambda m: "[redacted-path]/" + Path(m.group(0)).name, value)
    value = WINDOWS_ABS_PATH_RE.sub(lambda m: "[redacted-path]\\" + Path(m.group(0)).name, value)
    return value


def load_operator_decisions(path: Path) -> OperatorDecisions:
    if not path.exists():
        return OperatorDecisions(missing=True)

    values = OperatorDecisions()
    current_attr: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current_attr = SECTION_NAMES.get(normalize_heading(line[3:]))
            continue
        if current_attr is None:
            continue
        item = clean_checklist_item(line)
        if item:
            getattr(values, current_attr).append(item)
    return values


def load_todo_signals(path: Path, limit: int = 8) -> list[str]:
    if not path.exists():
        return []

    signals: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        item = clean_open_checklist_item(line)
        if item:
            signals.append(item)
        if len(signals) >= limit:
            break
    return signals


def summarize_fleet_report(path: Path | None) -> list[str]:
    if path is None:
        return ["No fleet report provided."]
    if not path.exists():
        return [f"Fleet report file not found: {path}"]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Fleet report is not valid JSON: {exc}"]

    hosts = payload.get("hosts")
    if not isinstance(hosts, list) or not hosts:
        return ["Fleet report contains no hosts."]

    lines: list[str] = []
    for host in hosts:
        if not isinstance(host, dict):
            continue
        label = str(host.get("label") or host.get("alias") or "unknown")
        health = host.get("health") if isinstance(host.get("health"), dict) else {}
        status = str(health.get("status") or "unknown")
        summary = str(health.get("summary") or "unknown")
        lines.append(f"{label}: {status} - {summary}")
        for blocker in health.get("blockers") or []:
            lines.append(f"  BLOCKER: {blocker}")
        for warning in health.get("warnings") or []:
            lines.append(f"  WARN: {warning}")
    return lines or ["Fleet report contains no readable hosts."]


def _item_label(item: object) -> str:
    if not isinstance(item, dict):
        return str(item)
    number = item.get("number")
    title = str(item.get("title") or item.get("id") or "")
    if number:
        return f"#{number} {title}".strip()
    return title or str(item)


def summarize_flyer_train_report(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.exists():
        return [f"Flyer train report file not found: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Flyer train report is not valid JSON: {exc}"]

    lines = [f"Status: {payload.get('status', 'unknown')}"]
    open_prs = payload.get("open_autonomous_prs") or []
    if open_prs:
        lines.append("Open autonomous PRs: " + ", ".join(_item_label(item) for item in open_prs))
    merged = payload.get("merged_not_deployed") or []
    if merged:
        lines.append("Merged-not-deployed: " + ", ".join(_item_label(item) for item in merged))
    for item in payload.get("blocked_candidates") or []:
        if isinstance(item, dict):
            lines.append(f"Blocked: {item.get('id', 'unknown')} - {item.get('reason', 'unknown')}")
    for item in payload.get("needs_srini") or []:
        lines.append(f"Needs Srini: {item}")
    return lines


def summarize_flyer_evaluation_report(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.exists():
        return ["Flyer self-evaluation report file not found."]
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [redact_text(f"Flyer self-evaluation report is not valid JSON: {exc}")]

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    lines: list[str] = []
    rollout = payload.get("rollout") if isinstance(payload.get("rollout"), dict) else None
    if rollout is not None:
        verdict = str(rollout.get("verdict") or "unknown").upper()
        reasons = rollout.get("reasons") or []
        if reasons:
            n = len(reasons)
            lines.append(
                f"Rollout: {verdict} - {n} reason{'s' if n != 1 else ''}"
            )
            top_reasons = [str(r.get("text") or "").strip() for r in reasons[:2]]
            for text in top_reasons:
                if text:
                    lines.append(f"  - {text}")
        else:
            lines.append(f"Rollout: {verdict}")
    lines.append(
        "Status: "
        f"{payload.get('status', 'unknown')}; "
        f"incidents={summary.get('incident_count', 0)}; "
        f"high_or_critical={summary.get('high_or_critical_count', 0)}"
    )
    incidents = [item for item in (payload.get("incidents") or []) if isinstance(item, dict)]
    active_risk = 0
    historical = 0
    for item in incidents:
        details = item.get("evidence_details") if isinstance(item.get("evidence_details"), dict) else {}
        if details.get("active_customer_risk") is True:
            active_risk += 1
        elif details.get("active_customer_risk") is False:
            historical += 1
    if active_risk or historical:
        lines.append(f"Customer risk: active={active_risk}; historical_or_audit={historical}")
    operating_layer = payload.get("operating_layer") if isinstance(payload.get("operating_layer"), dict) else None
    if operating_layer is not None:
        brand = operating_layer.get("brand_memory") if isinstance(operating_layer.get("brand_memory"), dict) else {}
        source_edit = operating_layer.get("source_edit") if isinstance(operating_layer.get("source_edit"), dict) else {}
        next_action = operating_layer.get("next_action") if isinstance(operating_layer.get("next_action"), dict) else {}
        lines.append(
            "Operating layer: "
            f"{operating_layer.get('status', 'unknown')}; "
            f"brand_memory={brand.get('status', 'unknown')} "
            f"({brand.get('ready_customer_count', 0)}/{brand.get('total_customer_count', 0)}); "
            f"source_edit={source_edit.get('status', 'unknown')} ({source_edit.get('posture', 'unknown')})"
        )
        if next_action.get("summary"):
            lines.append(redact_text(str(next_action.get("summary"))))
        blocked = [
            item
            for item in operating_layer.get("deferred_backlog", [])
            if isinstance(item, dict) and item.get("status") == "blocked"
        ]
        for item in blocked[:5]:
            lines.append(
                redact_text(
                    f"Blocked: {item.get('key', 'unknown')} - {item.get('guardrail', 'review before rollout')}"
                )
            )

    def active_state(item: dict) -> bool | None:
        details = item.get("evidence_details") if isinstance(item.get("evidence_details"), dict) else {}
        value = details.get("active_customer_risk")
        return value if isinstance(value, bool) else None

    def active_suffix(items: list[dict]) -> str:
        active = sum(1 for item in items if active_state(item) is True)
        old = sum(1 for item in items if active_state(item) is False)
        if active or old:
            return f"; active={active}; historical_or_audit={old}"
        return ""

    stale = [item for item in incidents if item.get("type") == "manual_source_edit_stale"]
    if stale:
        ages = [
            float(details.get("queued_age_minutes"))
            for item in stale
            for details in [item.get("evidence_details") if isinstance(item.get("evidence_details"), dict) else {}]
            if isinstance(details.get("queued_age_minutes"), (int, float))
        ]
        oldest = max(ages) if ages else 0
        lines.append(f"Manual queue: stale_source_edits={len(stale)}; oldest={oldest:g}min{active_suffix(stale)}")
    source_items = [
        item for item in incidents
        if item.get("type") in {"source_contract_missing", "source_contract_locked_fact_gap"}
    ]
    source_missing = sum(1 for item in source_items if item.get("type") == "source_contract_missing")
    locked_gaps = sum(1 for item in source_items if item.get("type") == "source_contract_locked_fact_gap")
    if source_missing or locked_gaps:
        lines.append(f"Source contracts: missing={source_missing}; locked_fact_gaps={locked_gaps}{active_suffix(source_items)}")
    qa_items = [
        item for item in incidents
        if item.get("type") in {
            "source_contract_qa_missing",
            "source_contract_qa_fact_gap",
            "source_contract_forbidden_text_present",
        }
    ]
    qa_missing = sum(1 for item in qa_items if item.get("type") == "source_contract_qa_missing")
    qa_gaps = sum(1 for item in qa_items if item.get("type") == "source_contract_qa_fact_gap")
    forbidden = sum(1 for item in qa_items if item.get("type") == "source_contract_forbidden_text_present")
    if qa_missing or qa_gaps or forbidden:
        lines.append(f"QA gaps: missing={qa_missing}; fact_gaps={qa_gaps}; forbidden_text_hits={forbidden}{active_suffix(qa_items)}")
    checkin_items = [item for item in incidents if item.get("type") == "repeated_status_checkins"]
    checkins = len(checkin_items)
    if checkins:
        lines.append(f"Customer waiting: repeated_checkins={checkins}{active_suffix(checkin_items)}")
    routing_items = [
        item for item in incidents
        if item.get("type") in {"new_flyer_routed_as_revision", "latest_request_not_reflected"}
    ]
    routed_as_revision = sum(1 for item in routing_items if item.get("type") == "new_flyer_routed_as_revision")
    not_reflected = sum(1 for item in routing_items if item.get("type") == "latest_request_not_reflected")
    if routed_as_revision or not_reflected:
        lines.append(
            "Routing tripwires: "
            f"new_as_revision={routed_as_revision}; "
            f"latest_not_reflected={not_reflected}"
            f"{active_suffix(routing_items)}"
        )
    preview_items = [item for item in incidents if item.get("type") == "preview_approved_final_qa_failed"]
    if preview_items:
        lines.append(f"Preview/final QA: approved_then_failed={len(preview_items)}{active_suffix(preview_items)}")
    hermes_items = [
        item for item in incidents
        if str(item.get("type") or "").startswith("hermes_intent_")
        or str(item.get("type") or "").startswith("flyer_intent_training_export_")
    ]
    if hermes_items:
        rejected = sum(1 for item in hermes_items if item.get("type") == "hermes_intent_rejected_by_validator")
        disagree = sum(1 for item in hermes_items if item.get("type") == "hermes_intent_live_route_disagreement")
        clarify = sum(1 for item in hermes_items if item.get("type") == "hermes_intent_would_clarify_but_router_mutated")
        coverage = sum(1 for item in hermes_items if item.get("type") == "hermes_intent_shadow_coverage_missing")
        unsupported = sum(1 for item in hermes_items if item.get("type") == "hermes_intent_unsupported_active_mode")
        training = sum(1 for item in hermes_items if str(item.get("type") or "").startswith("flyer_intent_training_export_"))
        lines.append(
            "Hermes intent: "
            f"rejected={rejected}; disagreements={disagree}; clarify_vs_mutate={clarify}; "
            f"coverage_missing={coverage}; unsupported_active_mode={unsupported}; training_export={training}"
            f"{active_suffix(hermes_items)}"
        )
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    top_incidents = sorted(
        incidents,
        key=lambda item: (
            severity_rank.get(str(item.get("severity") or "").lower(), 4),
            {True: 0, None: 1, False: 2}[active_state(item)],
            str(item.get("type") or ""),
        ),
    )[:5]
    for item in top_incidents:
        severity = str(item.get("severity") or "unknown").upper()
        kind = str(item.get("type") or "unknown")
        project = f" {item.get('project_id')}" if item.get("project_id") else ""
        action = str(item.get("suggested_action") or "review")
        lines.append(redact_text(f"{severity}: {kind}{project} - {action}"))
    for item in (payload.get("eval_candidates") or [])[:5]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "unknown")
        project = f" {item.get('project_id')}" if item.get("project_id") else ""
        fixture = str(item.get("suggested_fixture") or "tests/fixtures/flyer_golden/")
        lines.append(redact_text(f"Eval: {category}{project} -> {fixture}"))
    for item in payload.get("needs_srini") or []:
        lines.append(redact_text(f"Needs Srini: {item}"))
    return lines


def summarize_fleet_normalization_report(path: Path | None) -> list[str]:
    if path is None:
        return []
    lines = summarize_fleet_report(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return lines
    readiness = payload.get("promotion_readiness")
    if isinstance(readiness, dict):
        for key, label in (("srilu_to_main", "Srilu -> Main"), ("main_to_vpin", "Main -> VPIN")):
            item = readiness.get(key) if isinstance(readiness.get(key), dict) else {}
            state = "ready" if item.get("ready") else "blocked"
            lines.append(f"{label}: {state}")
            for reason in item.get("reasons") or []:
                lines.append(f"  {reason}")
        docker = readiness.get("docker_decision") if isinstance(readiness.get("docker_decision"), dict) else {}
        if docker:
            lines.append(f"Docker: {docker.get('status', 'unknown')}")
    return lines


def parse_toml_scalar(line: str) -> tuple[str, str] | None:
    match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$", line)
    if not match:
        return None
    value = match.group(2).strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return match.group(1), value


def load_automations(automations_dir: Path) -> list[str]:
    if not automations_dir.exists():
        return ["No automation configs found."]

    configs = sorted(automations_dir.glob("*/automation.toml"))
    if not configs:
        return ["No automation configs found."]

    lines: list[str] = []
    for config in configs:
        values: dict[str, str] = {}
        for line in config.read_text(encoding="utf-8").splitlines():
            parsed = parse_toml_scalar(line)
            if parsed:
                values[parsed[0]] = parsed[1]
        name = values.get("name") or config.parent.name
        status = values.get("status") or "unknown"
        lines.append(f"{name}: {status}")
    return lines


def load_git_summary(repo_root: Path) -> GitSummary:
    try:
        status = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        )
        log = subprocess.run(
            ["git", "log", "-5", "--oneline", "--decorate"],
            cwd=repo_root,
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return GitSummary(error=str(exc))

    status_lines = [line for line in status.stdout.splitlines() if line.strip()]
    branch = status_lines[0].removeprefix("## ").strip() if status_lines else "unknown"
    changes = status_lines[1:]
    commits = [line.strip() for line in log.stdout.splitlines() if line.strip()]
    return GitSummary(branch=branch, changes=changes, recent_commits=commits)


def build_brief(
    *,
    repo_root: Path,
    decisions_path: Path | None = None,
    todo_path: Path | None = None,
    fleet_json_path: Path | None = None,
    flyer_train_json_path: Path | None = None,
    flyer_evaluation_json_path: Path | None = None,
    fleet_normalization_json_path: Path | None = None,
    automations_dir: Path | None = None,
    generated_date: str | None = None,
    include_git: bool = True,
) -> Brief:
    decisions_file = decisions_path or repo_root / "tasks" / "operator-decisions.md"
    todo_file = todo_path or repo_root / "tasks" / "todo.md"
    automation_root = automations_dir or Path.home() / ".codex" / "automations"
    return Brief(
        generated_date=generated_date or date.today().isoformat(),
        decisions=load_operator_decisions(decisions_file),
        todo_signals=load_todo_signals(todo_file),
        fleet_lines=summarize_fleet_report(fleet_json_path),
        flyer_train_lines=summarize_flyer_train_report(flyer_train_json_path),
        flyer_evaluation_lines=summarize_flyer_evaluation_report(flyer_evaluation_json_path),
        fleet_normalization_lines=summarize_fleet_normalization_report(fleet_normalization_json_path),
        automation_lines=load_automations(automation_root),
        git=load_git_summary(repo_root) if include_git else None,
    )


def append_section(lines: list[str], title: str, items: list[str], empty: str) -> None:
    lines.extend(["", f"## {title}", ""])
    if items:
        for item in items:
            lines.append(f"- {item}")
    else:
        lines.append(f"- {empty}")


def render_markdown(brief: Brief) -> str:
    decisions = brief.decisions
    lines = [f"# Ops Brief - {brief.generated_date}"]

    missing_decision_msg = ["No operator decisions file found."] if decisions.missing else []
    append_section(
        lines,
        "Needs Your Decision",
        missing_decision_msg + decisions.needs_decision,
        "No decisions listed.",
    )
    append_section(lines, "Waiting On You", decisions.waiting_on_you, "No waiting-on-you items listed.")
    append_section(lines, "Active Risks", decisions.active_risks, "No active risks listed.")
    append_section(lines, "Open Todo Signals", brief.todo_signals, "No unchecked todo signals found.")
    append_section(lines, "Fleet Status", brief.fleet_lines, "No fleet status available.")
    if brief.flyer_train_lines:
        append_section(lines, "Flyer Autonomous Train", brief.flyer_train_lines, "No Flyer train report provided.")
    if brief.flyer_evaluation_lines:
        append_section(lines, "Flyer Self-Evaluation", brief.flyer_evaluation_lines, "No Flyer self-evaluation report provided.")
    if brief.fleet_normalization_lines:
        append_section(lines, "Fleet Normalization", brief.fleet_normalization_lines, "No fleet normalization report provided.")
    append_section(lines, "Automations", brief.automation_lines, "No automation configs found.")

    if brief.git is not None:
        git_lines = []
        if brief.git.error:
            git_lines.append(f"Git status unavailable: {brief.git.error}")
        else:
            git_lines.append(f"Branch: {brief.git.branch}")
            if brief.git.changes:
                git_lines.extend(f"Dirty: {change}" for change in brief.git.changes[:8])
            else:
                git_lines.append("Working tree clean.")
            git_lines.extend(f"Recent: {commit}" for commit in brief.git.recent_commits[:3])
        append_section(lines, "Git State", git_lines, "Git state not checked.")

    recommended = (
        decisions.needs_decision[:2]
        + decisions.waiting_on_you[:1]
        + brief.todo_signals[: max(0, 3 - min(3, len(decisions.needs_decision[:2] + decisions.waiting_on_you[:1])))]
    )
    append_section(lines, "Recommended Next 3", recommended[:3], "No recommended actions.")
    append_section(lines, "Handoffs And Promises", decisions.handoffs, "No handoffs listed.")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the daily operator ops brief.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--decisions", type=Path, default=None)
    parser.add_argument("--todo", type=Path, default=None)
    parser.add_argument("--fleet-json", type=Path, default=None)
    parser.add_argument("--flyer-train-json", type=Path, default=None)
    parser.add_argument("--flyer-evaluation-json", type=Path, default=None)
    parser.add_argument("--fleet-normalization-json", type=Path, default=None)
    parser.add_argument("--automations-dir", type=Path, default=None)
    parser.add_argument("--date", default=None, help="Brief date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--out", type=Path, default=None, help="Write Markdown to this path instead of stdout.")
    parser.add_argument("--no-git", action="store_true", help="Skip git status/log checks.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    brief = build_brief(
        repo_root=args.repo_root,
        decisions_path=args.decisions,
        todo_path=args.todo,
        fleet_json_path=args.fleet_json,
        flyer_train_json_path=args.flyer_train_json,
        flyer_evaluation_json_path=args.flyer_evaluation_json,
        fleet_normalization_json_path=args.fleet_normalization_json,
        automations_dir=args.automations_dir,
        generated_date=args.date,
        include_git=not args.no_git,
    )
    markdown = render_markdown(brief)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        sys.stderr.write(f"Wrote {args.out}\n")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
