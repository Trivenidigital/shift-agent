#!/usr/bin/env python3
"""Read-only Flyer Studio self-evaluation report.

This tool turns existing Flyer project state and decisions.log evidence into
operator-facing incidents and eval-candidate suggestions. It does not mutate
customer/project/VPS state and it does not send messages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROJECTS_PATH = Path("/opt/shift-agent/state/flyer/projects.json")
DEFAULT_DECISIONS_LOG = Path("/opt/shift-agent/logs/decisions.log")

INTERNAL_COPY_TERMS = (
    "queued project",
    "Project F",
    "Requested edit:",
    "Original customer request",
    "Authorized relationship",
    "source-preserving workflow",
    "source-preserving edit",
    "operator",
    "manual_edit_required",
    "provider",
    "reason_code",
)

SOURCE_EDIT_CUES = (
    "do not change anything else",
    "change only",
    "replace ",
    "uploaded flyer",
    "use this flyer",
    "source artwork",
    "exact edit",
)

STATUS_CHECK_RE = re.compile(r"\b(any update|updates?|status|ready|done|finished|eta)\b", re.I)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_utc(value: str | None) -> datetime:
    if not value:
        return utc_now()
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(value: Any, now: datetime) -> float | None:
    if not value:
        return None
    try:
        then = parse_utc(str(value))
    except (TypeError, ValueError):
        return None
    return (now - then).total_seconds() / 60.0


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"projects": [], "_load_error": f"missing: {path}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"projects": [], "_load_error": f"{type(exc).__name__}: {exc}"}
    return data if isinstance(data, dict) else {"projects": [], "_load_error": "root is not object"}


def load_decisions_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def has_reference_media(project: dict[str, Any]) -> bool:
    return any(
        isinstance(asset, dict) and asset.get("kind") == "reference_image"
        for asset in project.get("assets") or []
    )


def has_source_contract(project: dict[str, Any]) -> bool:
    for extraction in project.get("reference_extractions") or []:
        if isinstance(extraction, dict) and isinstance(extraction.get("source_contract"), dict):
            return True
    return False


def has_generated_or_final_asset(project: dict[str, Any]) -> bool:
    if project.get("final_asset_ids"):
        return True
    for asset in project.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("kind") or "").startswith("final_"):
            return True
        if asset.get("kind") == "concept_preview" and asset.get("source") in {"generated", "rendered"}:
            return True
    return project.get("status") in {"awaiting_concept_selection", "awaiting_final_approval", "delivered"}


def has_source_qa(project: dict[str, Any]) -> bool:
    reports = project.get("qa_reports") or []
    for report in reports:
        if not isinstance(report, dict):
            continue
        blockers = " ".join(str(item) for item in report.get("blockers") or [])
        warnings = " ".join(str(item) for item in report.get("warnings") or [])
        blob = f"{report.get('provider', '')} {report.get('qa_source', '')} {blockers} {warnings}".lower()
        if "source" in blob or "contract" in blob or report.get("status") == "passed":
            return True
    return False


def looks_like_exact_source_edit(project: dict[str, Any]) -> bool:
    text = str(project.get("raw_request") or "").lower()
    return has_reference_media(project) and any(cue in text for cue in SOURCE_EDIT_CUES)


def incident(
    kind: str,
    *,
    severity: str,
    project_id: str = "",
    evidence: str = "",
    suggested_action: str,
    category: str,
    count: int | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": kind,
        "severity": severity,
        "project_id": project_id,
        "evidence": evidence[:500],
        "suggested_action": suggested_action,
        "eval_category": category,
        "human_decision_required": severity in {"high", "critical"},
    }
    if count is not None:
        item["count"] = count
    return item


def project_incidents(
    projects: list[dict[str, Any]],
    *,
    now: datetime,
    manual_stale_minutes: int,
    generation_stale_minutes: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = str(project.get("project_id") or "")
        manual = project.get("manual_review") if isinstance(project.get("manual_review"), dict) else {}
        queued_age = age_minutes(manual.get("queued_at") or project.get("updated_at"), now)
        if (
            project.get("status") == "manual_edit_required"
            and manual.get("reason_code") == "source_edit_provider_unavailable"
            and manual.get("status") in {"queued", "in_progress", None, ""}
            and queued_age is not None
            and queued_age >= manual_stale_minutes
        ):
            out.append(
                incident(
                    "manual_source_edit_stale",
                    severity="high",
                    project_id=project_id,
                    evidence=f"queued_age_minutes={queued_age:.1f}; reason_code=source_edit_provider_unavailable",
                    suggested_action=(
                        "Burn down the manual queue row or finish the OpenRouter source-edit provider path; "
                        "do not let customers wait silently."
                    ),
                    category="source_edit_provider_posture",
                )
            )

        if looks_like_exact_source_edit(project) and not has_source_contract(project):
            out.append(
                incident(
                    "source_contract_missing",
                    severity="high",
                    project_id=project_id,
                    evidence="reference media + source-preservation language but no source_contract",
                    suggested_action="Add/verify a source-contract golden fixture before trusting generated output.",
                    category="source_contract_visual_qa",
                )
            )

        if has_source_contract(project) and has_generated_or_final_asset(project) and not has_source_qa(project):
            out.append(
                incident(
                    "source_contract_qa_missing",
                    severity="high",
                    project_id=project_id,
                    evidence="source_contract exists and generated/final asset exists, but no source-aware visual QA report is stored",
                    suggested_action="Harden visual QA so source-contract facts are verified before customer preview/delivery.",
                    category="source_contract_visual_qa",
                )
            )

        gen_age = age_minutes(project.get("updated_at"), now)
        if project.get("status") in {"generating_concepts", "finalizing_assets"} and gen_age is not None and gen_age >= generation_stale_minutes:
            out.append(
                incident(
                    "generation_stuck",
                    severity="medium",
                    project_id=project_id,
                    evidence=f"status={project.get('status')}; updated_age_minutes={gen_age:.1f}",
                    suggested_action="Investigate provider/runtime logs and send a customer-safe status update if needed.",
                    category="provider_runtime",
                )
            )
    return out


def customer_copy_incidents(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    text_fields = ("outbound_text", "customer_text", "message_text", "sent_text", "reply_text")
    for entry in entries:
        text = ""
        for field in text_fields:
            value = entry.get(field)
            if value:
                text = str(value)
                break
        if not text:
            continue
        matched = [term for term in INTERNAL_COPY_TERMS if term.lower() in text.lower()]
        if not matched:
            continue
        out.append(
            incident(
                "customer_copy_internal_leak",
                severity="high",
                project_id=str(entry.get("project_id") or ""),
                evidence=", ".join(matched),
                suggested_action="Add or update customer-message copy tests so WhatsApp receives outcome-only text.",
                category="customer-message copy",
            )
        )
    return out


def repeated_checkin_incidents(entries: list[dict[str, Any]], *, threshold: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        body = str(entry.get("body") or entry.get("visible_body") or entry.get("message") or "")
        reason = str(entry.get("reason") or "")
        if not STATUS_CHECK_RE.search(body) and "status" not in reason:
            continue
        key = str(entry.get("project_id") or entry.get("customer_phone") or entry.get("chat_id") or "unknown")
        grouped[key].append(entry)

    out: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        if len(rows) < threshold:
            continue
        project_id = key if re.fullmatch(r"F\d{4,}", key) else ""
        out.append(
            incident(
                "repeated_status_checkins",
                severity="medium",
                project_id=project_id,
                evidence=f"{len(rows)} status/check-in messages for {key}",
                suggested_action="Review SLA/status-copy loop; repeated check-ins are a customer-visible waiting signal.",
                category="customer_status_sla",
                count=len(rows),
            )
        )
    return out


def eval_candidates(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in incidents:
        category = str(item.get("eval_category") or "unknown")
        project_id = str(item.get("project_id") or "")
        key = (category, project_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "category": category,
                "project_id": project_id,
                "reason": item.get("type"),
                "suggested_fixture": suggested_fixture_for(category),
            }
        )
    return out


def suggested_fixture_for(category: str) -> str:
    return {
        "customer-message copy": "tests/test_flyer_manual_edit_ack_copy.py",
        "source_contract_visual_qa": "tests/fixtures/flyer_golden/live_customer_message_shapes.json",
        "source_edit_provider_posture": "tests/test_flyer_source_edit_preflight.py",
        "customer_status_sla": "tests/test_cf_router_flyer_routing.py",
        "provider_runtime": "src/agents/flyer/scripts/smoke-flyer-quality",
    }.get(category, "tasks/todo.md")


def build_report(
    *,
    projects: dict[str, Any],
    decision_entries: list[dict[str, Any]],
    now: datetime | None = None,
    manual_stale_minutes: int = 30,
    generation_stale_minutes: int = 15,
    repeated_checkin_threshold: int = 3,
) -> dict[str, Any]:
    now = now or utc_now()
    project_rows = projects.get("projects") if isinstance(projects.get("projects"), list) else []
    incidents = (
        project_incidents(
            project_rows,
            now=now,
            manual_stale_minutes=manual_stale_minutes,
            generation_stale_minutes=generation_stale_minutes,
        )
        + customer_copy_incidents(decision_entries)
        + repeated_checkin_incidents(decision_entries, threshold=repeated_checkin_threshold)
    )
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    worst = max((severity_rank.get(str(item.get("severity")), 0) for item in incidents), default=0)
    status = "red" if worst >= 3 else ("yellow" if worst >= 2 else "green")
    needs_srini = [
        f"{item['type']} {item.get('project_id') or ''}".strip()
        for item in incidents
        if item.get("human_decision_required")
    ]
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "mode": "read_only_report",
        "status": status,
        "summary": {
            "project_count": len(project_rows),
            "decision_entry_count": len(decision_entries),
            "incident_count": len(incidents),
            "high_or_critical_count": sum(1 for item in incidents if item.get("severity") in {"high", "critical"}),
        },
        "incidents": incidents,
        "eval_candidates": eval_candidates(incidents),
        "needs_srini": needs_srini,
        "boundaries": [
            "no customer messages sent",
            "no project/customer/manual-queue/payment/campaign mutation",
            "no prompt/SKILL/model/code self-modification",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Flyer Self-Evaluation",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Generated: {report.get('generated_at', 'unknown')}",
        f"- Incidents: {report.get('summary', {}).get('incident_count', 0)}",
        "",
        "## Incidents",
        "",
    ]
    incidents = report.get("incidents") or []
    if not incidents:
        lines.append("- None.")
    else:
        for item in incidents:
            project = f" ({item.get('project_id')})" if item.get("project_id") else ""
            lines.append(f"- {item.get('severity', 'unknown')}: {item.get('type')}{project} - {item.get('suggested_action')}")
    lines.extend(["", "## Eval Candidates", ""])
    candidates = report.get("eval_candidates") or []
    if not candidates:
        lines.append("- None.")
    else:
        for item in candidates:
            project = f" {item.get('project_id')}" if item.get("project_id") else ""
            lines.append(f"- {item.get('category')}{project}: {item.get('suggested_fixture')}")
    return "\n".join(lines).rstrip() + "\n"


def write_text(path: Path, content: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    platform_dir = repo_root / "src" / "platform"
    if str(platform_dir) not in sys.path:
        sys.path.insert(0, str(platform_dir))
    try:
        from safe_io import atomic_write_text  # type: ignore

        atomic_write_text(path, content)
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a read-only Flyer Studio self-evaluation report.")
    parser.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS_PATH)
    parser.add_argument("--decisions-log", type=Path, default=DEFAULT_DECISIONS_LOG)
    parser.add_argument("--now", default=None, help="UTC timestamp for deterministic tests, e.g. 2026-05-20T11:00:00Z")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--manual-stale-minutes", type=int, default=30)
    parser.add_argument("--generation-stale-minutes", type=int, default=15)
    parser.add_argument("--repeated-checkin-threshold", type=int, default=3)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(
        projects=load_json_file(args.projects),
        decision_entries=load_decisions_log(args.decisions_log),
        now=parse_utc(args.now) if args.now else utc_now(),
        manual_stale_minutes=args.manual_stale_minutes,
        generation_stale_minutes=args.generation_stale_minutes,
        repeated_checkin_threshold=args.repeated_checkin_threshold,
    )
    if args.format == "json":
        output = json.dumps(report, indent=2) + "\n"
    else:
        output = render_markdown(report)
    if args.out:
        write_text(args.out, output)
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
