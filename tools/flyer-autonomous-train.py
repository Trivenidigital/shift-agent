#!/usr/bin/env python3
"""Offline policy report for the Flyer Studio improvement train."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ALLOWED_CATEGORIES = {
    "golden_fixture_tests",
    "flyer_parser_routing",
    "source_contract_visual_qa",
    "customer_message_copy",
    "backlog_docs_cleanup",
}

BLOCKED_CATEGORIES = {
    "deploy_change",
    "payment_quota_account_state",
    "campaign_send",
    "provider_model_posture",
    "broad_non_flyer_cf_router",
    "manual_queue_closure",
    "customer_state_repair",
    "vps_runtime_mutation",
}

BLOCKED_PATH_PREFIXES = (
    "web/deploy/",
    "tools/build-deploy-tarball.sh",
    "src/agents/flyer/scripts/send-flyer-campaign",
    "src/agents/flyer/manual_queue.py",
    "tasks/flyer-source-edit-provider-posture",
)

BLOCKED_PATH_NEEDLES = (
    "provider-posture",
    "provider_posture",
    "payment",
    "quota",
    "campaign",
)

CATEGORY_ALLOWED_PREFIXES = {
    "golden_fixture_tests": (
        "tests/fixtures/flyer_golden/",
        "tests/test_flyer_golden_scenarios.py",
        "tasks/",
        "docs/",
    ),
    "backlog_docs_cleanup": ("tasks/", "docs/"),
    "customer_message_copy": (
        "src/agents/flyer/workflow.py",
        "tests/test_flyer_state_reply_table.py",
        "tasks/",
        "docs/",
    ),
    "source_contract_visual_qa": (
        "src/agents/flyer/facts.py",
        "src/agents/flyer/reference_extract.py",
        "src/agents/flyer/visual_qa.py",
        "src/agents/flyer/render.py",
        "src/platform/schemas.py",
        "src/plugins/cf-router/actions.py",
        "src/plugins/cf-router/hooks.py",
        "tests/test_flyer_",
        "tests/test_cf_router_flyer_routing.py",
        "tests/fixtures/flyer_golden/",
        "tasks/",
        "docs/",
    ),
    "flyer_parser_routing": (
        "src/plugins/cf-router/actions.py",
        "src/plugins/cf-router/hooks.py",
        "tests/test_cf_router_flyer_routing.py",
        "tests/test_cf_router_plugin.py",
        "tasks/",
        "docs/",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_path(path: object) -> str:
    return str(path or "").replace("\\", "/").strip()


def is_canonical_git_path(path: str) -> bool:
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return False
    parts = path.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def is_valid_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", value or ""))


def parse_utc(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def metadata_is_fresh(value: object, *, max_age_hours: int = 24) -> bool:
    collected = parse_utc(value)
    if collected is None:
        return False
    age_hours = (datetime.now(timezone.utc) - collected).total_seconds() / 3600
    return 0 <= age_hours <= max_age_hours


def is_truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"true", "yes", "1"}


def count_current_autonomous_approvals(
    reviewers: object,
    *,
    head_sha: str,
    author: str,
) -> int:
    if not isinstance(reviewers, list):
        return 0
    seen: set[str] = set()
    for reviewer in reviewers:
        if not isinstance(reviewer, dict):
            continue
        login = str(reviewer.get("login") or reviewer.get("name") or "")
        if not login or login in seen:
            continue
        if reviewer.get("role") != "autonomous":
            continue
        if reviewer.get("state") != "approved":
            continue
        if reviewer.get("commit_sha") != head_sha:
            continue
        if is_truthy(reviewer.get("is_stale")) or is_truthy(reviewer.get("dismissed")):
            continue
        if is_truthy(reviewer.get("is_author")) or (author and login == author):
            continue
        seen.add(login)
    return len(seen)


def has_unresolved_high_or_medium(findings: object, *, head_sha: str) -> bool:
    if not isinstance(findings, list):
        return False
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").lower()
        status = str(finding.get("status") or "open").lower()
        commit_sha = str(finding.get("commit_sha") or head_sha)
        if severity in {"high", "medium", "blocker"} and status not in {"resolved", "dismissed", "fixed"}:
            if not head_sha or commit_sha == head_sha:
                return True
    return False


def has_missing_or_failing_verification(verification: object, *, head_sha: str) -> bool:
    if not isinstance(verification, list) or not verification:
        return True
    for check in verification:
        if not isinstance(check, dict):
            return True
        if check.get("state") != "passed":
            return True
        if check.get("commit_sha") != head_sha:
            return True
    return False


def derive_touched_subsystems(changed_files: Iterable[object]) -> list[str]:
    systems: set[str] = set()
    for raw_path in changed_files:
        path = normalize_path(raw_path)
        if path == "src/plugins/cf-router/hooks.py":
            systems.add("cf-router-hooks")
        elif path.startswith("src/plugins/cf-router/"):
            systems.add("cf-router")
        elif path.startswith("tests/fixtures/flyer_golden/"):
            systems.add("flyer-golden-fixtures")
        elif path.startswith("src/agents/flyer/"):
            systems.add("flyer-agent")
        elif path.startswith("tasks/") or path.startswith("docs/"):
            systems.add("docs")
    return sorted(systems)


def blocked_path_reasons(changed_files: object, category: str) -> list[str]:
    if not isinstance(changed_files, list) or not changed_files:
        return ["changed_files missing or malformed"]
    files = [normalize_path(path) for path in changed_files]
    reasons: list[str] = []
    allowed_prefixes = CATEGORY_ALLOWED_PREFIXES.get(category, ())
    for path in files:
        if not path:
            continue
        if not is_canonical_git_path(path):
            reasons.append(f"non-canonical changed path: {path}")
            continue
        if any(path.startswith(prefix) for prefix in BLOCKED_PATH_PREFIXES):
            reasons.append(f"blocked path: {path}")
            continue
        lowered = path.lower()
        if any(needle in lowered for needle in BLOCKED_PATH_NEEDLES):
            reasons.append(f"blocked path: {path}")
            continue
        if path.startswith("src/plugins/cf-router/") and category != "flyer_parser_routing" and category != "source_contract_visual_qa":
            reasons.append(f"category/path mismatch: {path}")
            continue
        if allowed_prefixes and not any(path.startswith(prefix) or path == prefix for prefix in allowed_prefixes):
            reasons.append(f"category/path mismatch: {path}")
    return reasons


def violates_risky_subsystem_cooldown(metadata: dict[str, object]) -> bool:
    changed_files = metadata.get("changed_files") if isinstance(metadata.get("changed_files"), list) else []
    current = set(metadata.get("touched_subsystems") or derive_touched_subsystems(changed_files))
    previous = set(metadata.get("previous_run_touched_subsystems") or [])
    if metadata.get("urgent_customer_visible") is True:
        return False
    return "cf-router-hooks" in current and "cf-router-hooks" in previous


def evaluate_pr(metadata: dict[str, object]) -> dict[str, object]:
    reasons: list[str] = []
    head_sha = str(metadata.get("head_sha") or "")
    author = str(metadata.get("author") or "")
    approvals = count_current_autonomous_approvals(
        metadata.get("reviewers", []),
        head_sha=head_sha,
        author=author,
    )
    if not metadata.get("metadata_source"):
        reasons.append("metadata source missing")
    if not metadata.get("collected_at"):
        reasons.append("metadata collected_at missing")
    elif not metadata_is_fresh(metadata.get("collected_at")):
        reasons.append("metadata collected_at is stale")
    if not is_valid_sha(head_sha):
        reasons.append("head_sha must be a 40-character hex SHA")
    if metadata.get("base") not in {None, "main"}:
        reasons.append("base branch must be main")
    if approvals < 2:
        reasons.append("requires at least 2 current autonomous reviewer approvals")
    if not metadata.get("is_open", True):
        reasons.append("PR is not open")
    if metadata.get("behind_origin_main"):
        reasons.append("branch is behind origin/main")
    if has_unresolved_high_or_medium(metadata.get("findings", []), head_sha=head_sha):
        reasons.append("unresolved high/medium review finding")
    if has_missing_or_failing_verification(metadata.get("verification", []), head_sha=head_sha):
        reasons.append("missing/failing verification")
    category = str(metadata.get("category") or "")
    if category in BLOCKED_CATEGORIES:
        reasons.append(f"blocked category: {category}")
    elif category not in ALLOWED_CATEGORIES:
        reasons.append(f"category requires human decision: {category or 'missing'}")
    reasons.extend(blocked_path_reasons(metadata.get("changed_files", []), category))
    if violates_risky_subsystem_cooldown(metadata):
        reasons.append("risky subsystem cooldown: cf-router hooks touched in back-to-back runs")
    eligible = not reasons
    trusted = (
        metadata.get("metadata_trusted_for_merge") is True
        and "metadata collected_at is stale" not in reasons
        and "head_sha must be a 40-character hex SHA" not in reasons
    )
    return {
        "eligible": eligible,
        "decision": "policy_eligible_no_action" if eligible else "blocked",
        "autonomous_merge_enabled": False,
        "advisory_only": True,
        "metadata_trusted_for_merge": trusted,
        "would_be_auto_merge_eligible_if_live_runner_enabled": eligible and trusted,
        "reasons": reasons,
        "required_reviewers": 2,
        "approvals": approvals,
        "allowed_category": category if category in ALLOWED_CATEGORIES else "",
        "touched_subsystems": metadata.get("touched_subsystems") or derive_touched_subsystems(metadata.get("changed_files", [])),
    }


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_report_state(repo_root: Path) -> dict[str, object]:
    todo = repo_root / "tasks" / "todo.md"
    decisions = repo_root / "tasks" / "operator-decisions.md"
    needs: list[str] = []
    if decisions.exists():
        for line in decisions.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("- [ ]"):
                needs.append(line.split("]", 1)[1].strip())
                if len(needs) >= 3:
                    break
    residual = ["F0061/source-contract residuals"]
    if todo.exists() and "source-contract" in todo.read_text(encoding="utf-8").lower():
        residual.append("Source-contract backlog present in tasks/todo.md")
    return {
        "status": "attention" if needs or residual else "green",
        "generated_at": utc_now(),
        "open_autonomous_prs": [],
        "merged_not_deployed": [],
        "blocked_candidates": [],
        "needs_srini": needs,
        "skipped": [],
        "residual_backlog": residual,
        "backlog": [],
    }


def render_item_label(item: object) -> str:
    if not isinstance(item, dict):
        return str(item)
    number = item.get("number")
    title = str(item.get("title") or item.get("id") or "")
    if number:
        return f"#{number} {title}".strip()
    reason = item.get("reason")
    ident = str(item.get("id") or title)
    return f"{ident} - {reason}" if reason else ident


def render_pr_number(item: object) -> str:
    if isinstance(item, dict) and item.get("number"):
        return f"#{item['number']}"
    return render_item_label(item)


def render_report(state: dict[str, object], *, repo_root: Path, output_format: str = "markdown") -> str:
    payload = dict(default_report_state(repo_root))
    payload.update(state)
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"
    lines = [
        "# Flyer Autonomous Train Report",
        "",
        f"- Status: {payload.get('status', 'unknown')}",
        f"- Generated: {payload.get('generated_at', utc_now())}",
        "",
        "## Open Autonomous PRs",
    ]
    open_prs = payload.get("open_autonomous_prs") or []
    lines.extend(f"- {render_item_label(item)}" for item in open_prs) if open_prs else lines.append("- None")
    lines.extend(["", "## Merged-not-deployed"])
    merged = payload.get("merged_not_deployed") or []
    lines.extend(f"- PR {render_pr_number(item)}: merged" for item in merged) if merged else lines.append("- None")
    lines.extend(["", "## Blocked Candidates"])
    blocked = payload.get("blocked_candidates") or []
    lines.extend(f"- {render_item_label(item)}" for item in blocked) if blocked else lines.append("- None")
    lines.extend(["", "## Needs Srini"])
    needs = payload.get("needs_srini") or []
    lines.extend(f"- {item}" for item in needs) if needs else lines.append("- None")
    lines.extend(["", "## Skipped"])
    skipped = payload.get("skipped") or []
    lines.extend(f"- {render_item_label(item)}" for item in skipped) if skipped else lines.append("- None")
    lines.extend(["", "## Residual Backlog"])
    residual = payload.get("residual_backlog") or []
    lines.extend(f"- {item}" for item in residual) if residual else lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def choose_next_candidate(state: dict[str, object]) -> dict[str, object]:
    first_blocked: dict[str, object] | None = None
    for item in state.get("backlog") or []:
        if not isinstance(item, dict):
            continue
        normalized_id = re.sub(r"[^a-z0-9]+", "", str(item.get("id") or "").lower())
        if item.get("status") == "merged" or item.get("number") == 137 or normalized_id.startswith("pr137"):
            continue
        category = str(item.get("category") or "")
        if category in BLOCKED_CATEGORIES or category not in ALLOWED_CATEGORIES:
            if first_blocked is None:
                first_blocked = {
                    "status": "human_decision_required",
                    "id": item.get("id", ""),
                    "reason": f"category requires human decision: {category}",
                }
            continue
        if item.get("status") not in {None, "open"}:
            continue
        return {
            "status": "candidate",
            "id": item.get("id", ""),
            "category": category,
        }
    if first_blocked is not None:
        return first_blocked
    return {"status": "human_decision_required", "reason": "no safe autonomous candidate"}


def write_or_print(output: str, path: Path | None) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(output)
    else:
        sys.stdout.write(output)


def require_offline(args: argparse.Namespace) -> bool:
    if not getattr(args, "offline", False):
        print("--offline is required for this v0.1 command", file=sys.stderr)
        return False
    return True


def run_eligibility(args: argparse.Namespace) -> int:
    payload = evaluate_pr(load_json(args.metadata))
    write_or_print(json.dumps(payload, indent=2, sort_keys=True) + "\n", args.out)
    return 0


def run_report(args: argparse.Namespace) -> int:
    if not require_offline(args):
        return 2
    state = load_json(args.state_json) if args.state_json else {}
    output = render_report(state, repo_root=args.repo_root, output_format=args.format)
    write_or_print(output, args.out)
    return 0


def run_next_candidate(args: argparse.Namespace) -> int:
    if not require_offline(args):
        return 2
    state = load_json(args.state_json) if args.state_json else default_report_state(args.repo_root)
    output = json.dumps(choose_next_candidate(state), indent=2, sort_keys=True) + "\n"
    write_or_print(output, args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Flyer Studio autonomous train reports")
    subparsers = parser.add_subparsers(dest="command", required=True)

    eligibility = subparsers.add_parser("eligibility", help="evaluate offline PR metadata")
    eligibility.add_argument("--metadata", type=Path, required=True)
    eligibility.add_argument("--format", choices=["json"], default="json")
    eligibility.add_argument("--out", type=Path)
    eligibility.set_defaults(func=run_eligibility)

    report = subparsers.add_parser("report", help="render offline Flyer train report")
    report.add_argument("--repo-root", type=Path, required=True)
    report.add_argument("--offline", action="store_true")
    report.add_argument("--state-json", type=Path)
    report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    report.add_argument("--out", type=Path)
    report.set_defaults(func=run_report)

    next_candidate = subparsers.add_parser("next-candidate", help="choose one safe backlog candidate")
    next_candidate.add_argument("--repo-root", type=Path, required=True)
    next_candidate.add_argument("--offline", action="store_true")
    next_candidate.add_argument("--state-json", type=Path)
    next_candidate.add_argument("--format", choices=["json"], default="json")
    next_candidate.add_argument("--out", type=Path)
    next_candidate.set_defaults(func=run_next_candidate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
