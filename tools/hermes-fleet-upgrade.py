#!/usr/bin/env python3
"""Hermes fleet daily check and weekly promotion planner.

This tool is deliberately read-only for production VPSes. It reports runtime
posture and prints a promotion checklist; it does not mutate Hermes, Shift
Agent, systemd, or customer state.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_UPSTREAM_URL = "https://github.com/NousResearch/hermes-agent.git"


@dataclass(frozen=True)
class FleetHost:
    alias: str
    label: str
    role: str
    promotion_order: int
    expects_whatsapp: bool = True


@dataclass(frozen=True)
class HostSnapshot:
    alias: str
    label: str
    role: str
    promotion_order: int
    hermes_commit: str = ""
    hermes_branch: str = ""
    gateway_status: str = "unknown"
    cockpit_status: str = "unknown"
    bridge_status: str = "unknown"
    env_symlink_status: str = "unknown"
    latest_shift_agent_deploy: str = ""
    skills_count: int = 0
    plugins_count: int = 0
    patch_gate_status: str = "unknown"
    checked_at: str = ""
    probe_error: str = ""
    changed_paths: tuple[str, ...] = ()
    expects_whatsapp: bool = True

    def replace(self, **changes: object) -> "HostSnapshot":
        return replace(self, **changes)


@dataclass(frozen=True)
class HostHealth:
    status: str
    summary: str
    blockers: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class UpstreamRisk:
    level: str
    high_risk_paths: list[str]
    skill_paths: list[str]
    low_risk_paths: list[str]
    reasons: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_fleet_hosts() -> list[FleetHost]:
    return [
        FleetHost(alias="srilu-vps", label="Srilu", role="canary", promotion_order=1),
        FleetHost(alias="main-vps", label="Main", role="production", promotion_order=2),
        FleetHost(alias="vpin-vps", label="VPIN", role="production", promotion_order=3),
    ]


def parse_hosts(raw: str | None) -> list[FleetHost]:
    if not raw:
        return default_fleet_hosts()
    labels = {"srilu": "Srilu", "main": "Main", "vpin": "VPIN"}
    hosts: list[FleetHost] = []
    for index, item in enumerate(part.strip() for part in raw.split(",") if part.strip()):
        key = item.lower().replace("-vps", "")
        label = labels.get(key, item)
        role = "canary" if index == 0 else "production"
        hosts.append(FleetHost(alias=item, label=label, role=role, promotion_order=index + 1))
    return hosts


def classify_snapshot(snapshot: HostSnapshot, upstream_commit: str = "") -> HostHealth:
    blockers: list[str] = []
    warnings: list[str] = []

    if snapshot.probe_error:
        blockers.append(f"probe failed: {snapshot.probe_error}")
    if not snapshot.hermes_commit:
        blockers.append("unknown Hermes commit")
    if snapshot.gateway_status != "active":
        blockers.append("hermes-gateway inactive")
    if snapshot.expects_whatsapp and snapshot.bridge_status != "listening":
        blockers.append("WhatsApp bridge not listening")
    if snapshot.env_symlink_status != "ok":
        blockers.append("env symlink not ok")
    if snapshot.patch_gate_status == "failed":
        blockers.append("Hermes patch gate failed")

    if snapshot.cockpit_status == "missing":
        warnings.append("shift-agent-cockpit missing")
    elif snapshot.cockpit_status not in {"active", "unknown"}:
        warnings.append(f"shift-agent-cockpit {snapshot.cockpit_status}")

    if upstream_commit and snapshot.hermes_commit and snapshot.hermes_commit != upstream_commit:
        warnings.append("Hermes upgrade available")
    upstream_risk = classify_upstream_changes(snapshot.changed_paths)
    if upstream_risk.level == "high":
        warnings.append("High-risk Hermes upstream changes")
    elif upstream_risk.level == "medium":
        warnings.append("Hermes skill/plugin updates available")
    if snapshot.patch_gate_status == "missing":
        warnings.append("Hermes patch gate unavailable on host")
    elif snapshot.patch_gate_status == "unknown":
        warnings.append("Hermes patch gate status unknown")

    if blockers:
        return HostHealth(status="red", summary="blocked", blockers=blockers, warnings=warnings)
    if warnings:
        return HostHealth(status="yellow", summary="attention", blockers=[], warnings=warnings)
    return HostHealth(status="green", summary="ready", blockers=[], warnings=[])


def classify_upstream_changes(paths: Iterable[str]) -> UpstreamRisk:
    high_risk_paths: list[str] = []
    skill_paths: list[str] = []
    low_risk_paths: list[str] = []
    reasons: list[str] = []
    high_prefixes = (
        "gateway/",
        "scripts/whatsapp-bridge/",
        "providers/",
        "agent/providers/",
        # Conservative high-risk list from Hermes Agent top-level/runtime dirs
        # observed on 2026-05-20; expand when upstream adds new runtime roots.
        "agent/vision/",
        "vision/",
        "cli/",
        "core/",
        "config/",
        "migrations/",
        "plugins/",
        "tools/",
        "pyproject.toml",
        "uv.lock",
    )
    for raw_path in paths:
        path = raw_path.strip().replace("\\", "/")
        if not path:
            continue
        if path.startswith("skills/"):
            skill_paths.append(path)
        elif path.startswith(high_prefixes):
            high_risk_paths.append(path)
        else:
            low_risk_paths.append(path)

    if high_risk_paths:
        reasons.append("core runtime, provider, plugin, dependency, or bridge surface changed")
        level = "high"
    elif skill_paths:
        reasons.append("bundled skills changed without core runtime diff")
        level = "medium"
    elif low_risk_paths:
        reasons.append("upstream changed outside tracked high-risk surfaces")
        level = "low"
    else:
        level = "none"
    return UpstreamRisk(
        level=level,
        high_risk_paths=high_risk_paths,
        skill_paths=skill_paths,
        low_risk_paths=low_risk_paths,
        reasons=reasons,
    )


def snapshot_to_report_dict(snapshot: HostSnapshot, upstream_commit: str) -> dict[str, object]:
    health = classify_snapshot(snapshot, upstream_commit)
    upstream_risk = classify_upstream_changes(snapshot.changed_paths)
    data = asdict(snapshot)
    data["health"] = asdict(health)
    data["upstream_risk"] = asdict(upstream_risk)
    return data


def render_json_report(
    snapshots: Iterable[HostSnapshot],
    upstream_commit: str,
    generated_at: str | None = None,
) -> str:
    payload = {
        "generated_at": generated_at or utc_now(),
        "upstream_commit": upstream_commit,
        "hosts": [snapshot_to_report_dict(snapshot, upstream_commit) for snapshot in snapshots],
        "stop_conditions": stop_conditions(),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_markdown_report(
    snapshots: Iterable[HostSnapshot],
    upstream_commit: str,
    generated_at: str | None = None,
) -> str:
    rows: list[str] = []
    details: list[str] = []
    for snapshot in sorted(snapshots, key=lambda item: item.promotion_order):
        health = classify_snapshot(snapshot, upstream_commit)
        commit = short_sha(snapshot.hermes_commit) if snapshot.hermes_commit else "unknown"
        upstream_risk = classify_upstream_changes(snapshot.changed_paths)
        rows.append(
            "| {label} | {alias} | {status} | {commit} | {risk} | {gateway} | {bridge} | {deploy} |".format(
                label=snapshot.label,
                alias=snapshot.alias,
                status=health.status,
                commit=commit,
                risk=upstream_risk.level,
                gateway=snapshot.gateway_status,
                bridge=snapshot.bridge_status,
                deploy=snapshot.latest_shift_agent_deploy or "unknown",
            )
        )
        if health.blockers or health.warnings:
            details.append(f"### {snapshot.label}")
            for blocker in health.blockers:
                details.append(f"- BLOCKER: {blocker}")
            for warning in health.warnings:
                details.append(f"- WARN: {warning}")
            for path in upstream_risk.high_risk_paths[:12]:
                details.append(f"- HIGH-RISK PATH: `{path}`")
            for path in upstream_risk.skill_paths[:12]:
                details.append(f"- SKILL PATH: `{path}`")

    lines = [
        "# Hermes Fleet Daily Check",
        "",
        f"- Generated: {generated_at or utc_now()}",
        f"- Upstream commit: `{upstream_commit or 'unknown'}`",
        "",
        "| Host | SSH alias | Health | Hermes | Update risk | Gateway | Bridge | Shift Agent deploy |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Stop Conditions",
        "",
    ]
    lines.extend(f"- {condition}" for condition in stop_conditions())
    if details:
        lines.extend(["", "## Details", "", *details])
    return "\n".join(lines) + "\n"


def stop_conditions() -> list[str]:
    return [
        "gateway inactive",
        "WhatsApp bridge not listening",
        "unknown Hermes commit",
        "env symlink not ok",
        "Hermes patch gate failed",
        "deploy smoke or pilot-readiness failure",
    ]


def render_skill_sync_report(
    snapshots: Iterable[HostSnapshot],
    upstream_risk: UpstreamRisk,
    generated_at: str | None = None,
) -> str:
    rows = [
        "| Host | Installed skills | Installed plugins | Notes |",
        "|---|---:|---:|---|",
    ]
    for snapshot in sorted(snapshots, key=lambda item: item.promotion_order):
        notes: list[str] = []
        if snapshot.skills_count == 0:
            notes.append("no skill directory detected")
        if snapshot.plugins_count == 0:
            notes.append("no plugin directory detected")
        if not notes:
            notes.append("installed")
        rows.append(
            f"| {snapshot.label} | {snapshot.skills_count} | {snapshot.plugins_count} | {', '.join(notes)} |"
        )

    relevant = relevant_skill_paths(upstream_risk.skill_paths)
    lines = [
        "# Hermes Skill Sync Report",
        "",
        f"- Generated: {generated_at or utc_now()}",
        "- Mode: report-only; no skill/plugin install is attempted.",
        "- Install posture: review-before-install for production VPSes.",
        "",
        "## Installed Posture",
        "",
        *rows,
        "",
        "## Upstream Skill Changes",
        "",
    ]
    if upstream_risk.skill_paths:
        for path in upstream_risk.skill_paths[:30]:
            action = "review-before-install" if path in relevant else "track"
            lines.append(f"- `{path}` - {action}")
    else:
        lines.append("- No upstream skill-path changes were detected by the daily check.")

    lines.extend(
        [
            "",
            "## Relevant Domains",
            "",
            "- Flyer: vision/OCR, creative/image, WhatsApp/media, document extraction.",
            "- Catering: OCR/document extraction, menu parsing, Google Workspace, Sheets.",
            "- Shift/Daily Brief: calendar, sheets, email, notifications, maps.",
        ]
    )
    return "\n".join(lines) + "\n"


def relevant_skill_paths(paths: Iterable[str]) -> set[str]:
    needles = (
        "ocr",
        "document",
        "vision",
        "image",
        "creative",
        "comfyui",
        "google",
        "workspace",
        "sheets",
        "calendar",
        "maps",
        "whatsapp",
    )
    return {path for path in paths if any(needle in path.lower() for needle in needles)}


def render_normalization_report(
    snapshots: Iterable[HostSnapshot],
    generated_at: str | None = None,
) -> str:
    ordered = sorted(snapshots, key=lambda item: item.promotion_order)
    main = next((snapshot for snapshot in ordered if snapshot.label.lower() == "main"), None)
    reference = main or (ordered[0] if ordered else None)
    lines = [
        "# Hermes Fleet Normalization Checklist",
        "",
        f"- Generated: {generated_at or utc_now()}",
        "- Purpose: make Srilu/Main/VPIN comparable before any execute-mode upgrade.",
        "",
        "## Main reference shape",
        "",
    ]
    if reference:
        lines.extend(
            [
                f"- Reference host: {reference.label} (`{reference.alias}`)",
                f"- Gateway: {reference.gateway_status}",
                f"- Cockpit: {reference.cockpit_status}",
                f"- WhatsApp bridge: {reference.bridge_status}",
                f"- Env symlink: {reference.env_symlink_status}",
                f"- Patch gate: {reference.patch_gate_status}",
                f"- Latest deploy: {reference.latest_shift_agent_deploy or 'unknown'}",
            ]
        )
    else:
        lines.append("- No snapshots available.")

    lines.extend(["", "## Host gaps", ""])
    for snapshot in ordered:
        if reference and snapshot.alias == reference.alias:
            continue
        gaps = normalization_gaps(snapshot, reference)
        lines.append(f"### {snapshot.label}")
        if gaps:
            lines.extend(f"- {gap}" for gap in gaps)
        else:
            lines.append("- No normalization gaps detected against the reference shape.")
        lines.append("")

    lines.extend(
        [
            "## Required before first canary upgrade",
            "",
            "- Env symlink posture is understood and intentionally aligned or documented per VPS.",
            "- WhatsApp bridge expectation is explicit: either listening on :3000 or intentionally absent.",
            "- Patch gate and baseline availability are normalized, or the host is excluded from upgrade waves.",
            "- Latest deploy marker format is present where Shift Agent is installed.",
            "- Gateway restart and smoke commands are known for each VPS.",
        ]
    )
    return "\n".join(lines) + "\n"


def load_normalization_snapshot_payload(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {"mode": "offline_snapshot", "generated_at": utc_now(), "hosts": payload}
    if not isinstance(payload, dict):
        return {"mode": "offline_snapshot", "generated_at": utc_now(), "hosts": []}
    payload.setdefault("mode", "offline_snapshot")
    payload.setdefault("generated_at", utc_now())
    payload.setdefault("hosts", [])
    return payload


def _parse_utc(value: object) -> datetime | None:
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


def _snapshot_age_hours(host: dict[str, object], generated_at: str) -> float | None:
    checked = _parse_utc(host.get("checked_at"))
    generated = _parse_utc(generated_at)
    if checked is None or generated is None:
        return None
    return (generated - checked).total_seconds() / 3600


def _host_snapshot_from_normalization(host: dict[str, object]) -> HostSnapshot:
    return HostSnapshot(
        alias=str(host.get("alias") or ""),
        label=str(host.get("label") or host.get("alias") or ""),
        role=str(host.get("role") or ""),
        promotion_order=parse_int(str(host.get("promotion_order") or "0")),
        hermes_commit=str(host.get("hermes_commit") or ""),
        hermes_branch=str(host.get("hermes_branch") or ""),
        gateway_status=str(host.get("gateway_status") or "unknown"),
        cockpit_status=str(host.get("cockpit_status") or "unknown"),
        bridge_status=str(host.get("bridge_status") or "unknown"),
        env_symlink_status=str(host.get("env_symlink_status") or "unknown"),
        latest_shift_agent_deploy=str(host.get("latest_shift_agent_deploy") or ""),
        skills_count=parse_int(str(host.get("skills_count") or "0")),
        plugins_count=parse_int(str(host.get("plugins_count") or "0")),
        patch_gate_status=str(host.get("patch_gate_status") or "unknown"),
        checked_at=str(host.get("checked_at") or ""),
        changed_paths=tuple(str(path) for path in host.get("changed_paths") or ()),
        expects_whatsapp=bool(host.get("expects_whatsapp", True)),
    )


def _backup_blockers(host: dict[str, object]) -> list[str]:
    status = str(host.get("backup_status") or "unknown").lower()
    age = host.get("backup_age_hours")
    blockers: list[str] = []
    if status != "fresh":
        blockers.append(f"backup status {status}")
    try:
        numeric_age = float(age)
    except (TypeError, ValueError):
        blockers.append("backup age unknown")
    else:
        if numeric_age > 24:
            blockers.append(f"backup stale: {numeric_age:g}h old")
    return blockers


def _normalization_host_report(host: dict[str, object], generated_at: str) -> dict[str, object]:
    snapshot = _host_snapshot_from_normalization(host)
    health = classify_snapshot(snapshot)
    blockers = list(health.blockers)
    warnings = list(health.warnings)
    age = _snapshot_age_hours(host, generated_at)
    if age is None:
        blockers.append("snapshot checked_at missing")
    elif age < 0:
        warnings.append("snapshot checked_at is newer than generated_at")
    elif age > 24:
        blockers.append(f"snapshot stale: {age:g}h old")
    blockers.extend(_backup_blockers(host))
    status = "red" if blockers else ("yellow" if warnings else "green")
    summary = "blocked" if blockers else ("attention" if warnings else "ready")
    return {
        "alias": snapshot.alias,
        "label": snapshot.label,
        "role": snapshot.role,
        "promotion_order": snapshot.promotion_order,
        "hermes_commit": snapshot.hermes_commit,
        "gateway_status": snapshot.gateway_status,
        "cockpit_status": snapshot.cockpit_status,
        "bridge_status": snapshot.bridge_status,
        "env_symlink_status": snapshot.env_symlink_status,
        "latest_shift_agent_deploy": snapshot.latest_shift_agent_deploy,
        "skills_count": snapshot.skills_count,
        "plugins_count": snapshot.plugins_count,
        "patch_gate_status": snapshot.patch_gate_status,
        "checked_at": snapshot.checked_at,
        "backup_status": str(host.get("backup_status") or "unknown"),
        "backup_age_hours": host.get("backup_age_hours"),
        "health": {
            "status": status,
            "summary": summary,
            "blockers": blockers,
            "warnings": warnings,
        },
    }


def _required_host_reasons(hosts_by_label: dict[str, dict[str, object]]) -> list[str]:
    missing = [label for label in ("srilu", "main", "vpin") if label not in hosts_by_label]
    return [f"required host missing: {label.title()}" for label in missing]


def _host_ready_for_promotion(host: dict[str, object] | None) -> tuple[bool, list[str]]:
    if host is None:
        return False, ["host missing"]
    health = host.get("health") if isinstance(host.get("health"), dict) else {}
    if health.get("status") != "green":
        reasons = [str(blocker) for blocker in health.get("blockers") or []]
        reasons.extend(str(warning) for warning in health.get("warnings") or [])
        return False, reasons or [f"host health is {health.get('status', 'unknown')}"]
    blockers = list(health.get("blockers") or [])
    if blockers:
        return False, [str(blocker) for blocker in blockers]
    return True, []


def _promotion_readiness(hosts: list[dict[str, object]]) -> dict[str, object]:
    hosts_by_label = {str(host.get("label") or "").lower(): host for host in hosts}
    required_reasons = _required_host_reasons(hosts_by_label)
    srilu_ok, srilu_reasons = _host_ready_for_promotion(hosts_by_label.get("srilu"))
    main_ok, main_reasons = _host_ready_for_promotion(hosts_by_label.get("main"))
    vpin_ok, vpin_reasons = _host_ready_for_promotion(hosts_by_label.get("vpin"))

    srilu_to_main_reasons = []
    if not srilu_ok:
        srilu_to_main_reasons.append("Srilu must be green before Main promotion")
        srilu_to_main_reasons.extend(srilu_reasons)
    if not main_ok:
        srilu_to_main_reasons.append("Main normalization contract must be green")
        srilu_to_main_reasons.extend(main_reasons)
    srilu_to_main_reasons.extend(required_reasons)

    main_to_vpin_reasons = []
    if not main_ok:
        main_to_vpin_reasons.append("Main normalization contract must be green")
        main_to_vpin_reasons.extend(main_reasons)
    if not vpin_ok:
        main_to_vpin_reasons.append("VPIN normalization contract must be green")
        main_to_vpin_reasons.extend(vpin_reasons)
    main_to_vpin_reasons.extend(required_reasons)

    return {
        "srilu_to_main": {
            "ready": not srilu_to_main_reasons,
            "reasons": srilu_to_main_reasons,
        },
        "main_to_vpin": {
            "ready": not main_to_vpin_reasons,
            "reasons": main_to_vpin_reasons,
        },
        "docker_decision": {
            "status": "deferred",
            "until": [
                "normalization contract is green",
                "one clean Srilu -> Main cycle completes",
                "backup/restore story is proven",
            ],
        },
    }


def normalization_payload(snapshot_payload: dict[str, object]) -> dict[str, object]:
    generated_at = str(snapshot_payload.get("generated_at") or utc_now())
    raw_hosts = snapshot_payload.get("hosts") if isinstance(snapshot_payload.get("hosts"), list) else []
    host_reports = [
        _normalization_host_report(host, generated_at)
        for host in raw_hosts
        if isinstance(host, dict)
    ]
    host_reports.sort(key=lambda host: parse_int(str(host.get("promotion_order") or "0")))
    return {
        "generated_at": generated_at,
        "mode": "offline_snapshot",
        "hosts": host_reports,
        "promotion_readiness": _promotion_readiness(host_reports),
    }


def render_normalization_json(snapshot_payload: dict[str, object]) -> str:
    return json.dumps(normalization_payload(snapshot_payload), indent=2, sort_keys=True) + "\n"


def render_normalization_markdown(snapshot_payload: dict[str, object]) -> str:
    payload = normalization_payload(snapshot_payload)
    rows = [
        "| Host | Role | Health | Gateway | Bridge | Patch gate | Backup |",
        "|---|---|---|---|---|---|---|",
    ]
    details: list[str] = []
    for host in payload["hosts"]:
        health = host["health"]
        rows.append(
            "| {label} | {role} | {status} - {summary} | {gateway} | {bridge} | {patch} | {backup} |".format(
                label=host["label"],
                role=host["role"],
                status=health["status"],
                summary=health["summary"],
                gateway=host["gateway_status"],
                bridge=host["bridge_status"],
                patch=host["patch_gate_status"],
                backup=host["backup_status"],
            )
        )
        for blocker in health["blockers"]:
            details.append(f"- {host['label']} BLOCKER: {blocker}")
        for warning in health["warnings"]:
            details.append(f"- {host['label']} WARN: {warning}")

    readiness = payload["promotion_readiness"]
    lines = [
        "# Hermes Fleet Normalization Report",
        "",
        f"- Generated: {payload['generated_at']}",
        "- Mode: offline snapshot; no remote host probe is performed.",
        "",
        "## Host Contract",
        "",
        *rows,
        "",
        "## Promotion Readiness",
        "",
    ]
    for key, label in (("srilu_to_main", "Srilu -> Main"), ("main_to_vpin", "Main -> VPIN")):
        item = readiness[key]
        state = "ready" if item["ready"] else "blocked"
        lines.append(f"- {label}: {state}")
        for reason in item["reasons"]:
            lines.append(f"  - {reason}")
    docker = readiness["docker_decision"]
    lines.append(f"- Docker: {docker['status']}")
    for reason in docker["until"]:
        lines.append(f"  - {reason}")
    if details:
        lines.extend(["", "## Details", "", *details])
    return "\n".join(lines) + "\n"


def normalization_gaps(snapshot: HostSnapshot, reference: HostSnapshot | None) -> list[str]:
    gaps: list[str] = []
    if snapshot.env_symlink_status != "ok":
        gaps.append("env symlink is not ok")
    if snapshot.expects_whatsapp and snapshot.bridge_status != "listening":
        gaps.append("WhatsApp bridge is not listening")
    if snapshot.patch_gate_status != "ok":
        gaps.append("patch gate is not ready")
    if not snapshot.latest_shift_agent_deploy:
        gaps.append("latest Shift Agent deploy marker is missing")
    if snapshot.gateway_status != "active":
        gaps.append("gateway is not active")
    if reference and reference.cockpit_status == "active" and snapshot.cockpit_status != "active":
        gaps.append("cockpit service differs from Main")
    return gaps


def short_sha(value: str) -> str:
    return value[:8] if len(value) >= 8 else value


def validate_candidate(candidate: str) -> str:
    candidate = (candidate or "").strip()
    if len(candidate) != 40 or any(char not in "0123456789abcdefABCDEF" for char in candidate):
        raise ValueError("candidate must be a 40-character git SHA")
    return candidate.lower()


def build_promotion_plan(
    candidate: str,
    hosts: Iterable[FleetHost],
    generated_at: str | None = None,
) -> str:
    candidate = validate_candidate(candidate)
    ordered = sorted(hosts, key=lambda item: item.promotion_order)
    lines = [
        "# Hermes Weekly Promotion Plan",
        "",
        f"- Generated: {generated_at or utc_now()}",
        f"- Candidate: `{candidate}`",
        "- Mode: reviewed, wave-based, halt-on-failure",
        "",
        "## Preflight",
        "",
        "- Confirm the candidate is a reviewed Hermes commit, not a moving branch.",
        "- Apply and verify Shift Agent patches with `tools/patch-hermes.py` in staging.",
        "- Update `tools/hermes-patch-baseline.txt` only after the patched candidate clears smoke.",
        "- Open a PR carrying the baseline update and this report.",
        "- Build the Shift Agent tarball from a clean committed tree.",
        "",
        "## Waves",
        "",
    ]
    for wave, host in enumerate(ordered, start=1):
        lines.extend(
            [
                f"### Wave {wave} - {host.label}",
                "",
                f"- Target SSH alias: `{host.alias}`",
                "- Run daily check immediately before promotion.",
                "- Install the reviewed Hermes candidate and re-apply Shift Agent patches.",
                "- Deploy through the existing tarball path and `/usr/local/bin/shift-agent-deploy.sh`.",
                "- Verify deploy smoke, pilot readiness, gateway, cockpit, bridge, and agent-specific smoke.",
                "- Capture the resulting deploy tag and Hermes commit in the promotion report.",
                "",
            ]
        )
    lines.extend(
        [
            "## Stop immediately",
            "",
            "- Any host reports an unknown Hermes commit.",
            "- `tools/check-shift-agent-patch.sh` fails or is missing where expected.",
            "- `hermes-gateway` is inactive after restart.",
            "- WhatsApp bridge is not listening on port 3000.",
            "- `/opt/shift-agent/.env` is not the canonical symlink to `/root/.hermes/.env`.",
            "- Deploy smoke, pilot readiness, or customer-critical agent smoke fails.",
            "",
            "## Manual SSH note",
            "",
            "When debugging from Windows shell tools, use the project two-step SSH capture pattern: redirect SSH output to a file, then read that file. Do not rely on inline SSH stdout.",
        ]
    )
    return "\n".join(lines) + "\n"


def fetch_upstream_commit(upstream_url: str = DEFAULT_UPSTREAM_URL) -> str:
    try:
        proc = subprocess.run(
            ["git", "ls-remote", upstream_url, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    first = proc.stdout.strip().split()
    if not first:
        return ""
    return first[0] if len(first[0]) == 40 else ""


def remote_probe_script() -> str:
    return r"""
set +e
# This probe is read-only to Hermes/Shift Agent runtime state. It does run
# `git fetch` inside the Hermes checkout so reports can classify upstream
# changed paths; no working tree files or services are modified.
trap 'rm -f /tmp/hermes_fetch.out /tmp/hermes_patch_gate.out' EXIT
HERMES_DIR=""
for d in /root/.hermes/hermes-agent /usr/local/lib/hermes-agent; do
  if [ -d "$d/.git" ]; then HERMES_DIR="$d"; break; fi
done
if [ -n "$HERMES_DIR" ]; then
  echo "hermes_commit=$(git -C "$HERMES_DIR" rev-parse HEAD 2>/dev/null)"
  echo "hermes_branch=$(git -C "$HERMES_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  git -C "$HERMES_DIR" fetch --quiet origin main >/tmp/hermes_fetch.out 2>&1
  if [ "$?" -eq 0 ]; then
    echo "changed_paths=$(git -C "$HERMES_DIR" diff --name-only HEAD..origin/main 2>/dev/null | tr '\n' '|')"
  else
    echo "changed_paths="
  fi
else
  echo "hermes_commit="
  echo "hermes_branch="
  echo "changed_paths="
fi
systemctl is-active --quiet hermes-gateway && echo "gateway_status=active" || echo "gateway_status=inactive"
if systemctl list-unit-files shift-agent-cockpit.service >/dev/null 2>&1; then
  systemctl is-active --quiet shift-agent-cockpit && echo "cockpit_status=active" || echo "cockpit_status=inactive"
else
  echo "cockpit_status=missing"
fi
(ss -ltn 2>/dev/null | grep -q ':3000 ') && echo "bridge_status=listening" || echo "bridge_status=not_listening"
if [ -L /opt/shift-agent/.env ] && [ "$(readlink /opt/shift-agent/.env)" = "/root/.hermes/.env" ]; then
  echo "env_symlink_status=ok"
else
  echo "env_symlink_status=missing"
fi
latest=$(ls -1t /opt/shift-agent/deploys/deploy-*.tgz 2>/dev/null | head -1)
echo "latest_shift_agent_deploy=$(basename "$latest" .tgz 2>/dev/null)"
echo "skills_count=$(find /root/.hermes/skills -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
echo "plugins_count=$(find /root/.hermes/plugins -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
if [ -x /opt/shift-agent/tools/check-shift-agent-patch.sh ] && [ -f /opt/shift-agent/tools/hermes-patch-baseline.txt ]; then
  /opt/shift-agent/tools/check-shift-agent-patch.sh >/tmp/hermes_patch_gate.out 2>&1
  [ "$?" -eq 0 ] && echo "patch_gate_status=ok" || echo "patch_gate_status=failed"
elif [ -x /usr/local/bin/check-shift-agent-patch.sh ] && [ -f /usr/local/bin/hermes-patch-baseline.txt ]; then
  /usr/local/bin/check-shift-agent-patch.sh >/tmp/hermes_patch_gate.out 2>&1
  [ "$?" -eq 0 ] && echo "patch_gate_status=ok" || echo "patch_gate_status=failed"
else
  echo "patch_gate_status=missing"
fi
"""


def parse_probe_output(host: FleetHost, output: str, checked_at: str) -> HostSnapshot:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return HostSnapshot(
        alias=host.alias,
        label=host.label,
        role=host.role,
        promotion_order=host.promotion_order,
        hermes_commit=values.get("hermes_commit", ""),
        hermes_branch=values.get("hermes_branch", ""),
        gateway_status=values.get("gateway_status", "unknown"),
        cockpit_status=values.get("cockpit_status", "unknown"),
        bridge_status=values.get("bridge_status", "unknown"),
        env_symlink_status=values.get("env_symlink_status", "unknown"),
        latest_shift_agent_deploy=values.get("latest_shift_agent_deploy", ""),
        skills_count=parse_int(values.get("skills_count")),
        plugins_count=parse_int(values.get("plugins_count")),
        patch_gate_status=values.get("patch_gate_status", "unknown"),
        checked_at=checked_at,
        changed_paths=parse_changed_paths(values.get("changed_paths", "")),
        expects_whatsapp=host.expects_whatsapp,
    )


def parse_int(value: str | None) -> int:
    try:
        return int(value or "0")
    except ValueError:
        return 0


def parse_changed_paths(value: str) -> tuple[str, ...]:
    return tuple(path for path in (part.strip() for part in value.split("|")) if path)


def probe_host(host: FleetHost, timeout: int = 45) -> HostSnapshot:
    checked_at = utc_now()
    probe = remote_probe_script().replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                host.alias,
                "bash -s",
            ],
            input=probe,
            capture_output=True,
            text=False,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return HostSnapshot(
            alias=host.alias,
            label=host.label,
            role=host.role,
            promotion_order=host.promotion_order,
            checked_at=checked_at,
            probe_error=str(exc),
        )
    stdout = decode_output(proc.stdout)
    stderr = decode_output(proc.stderr)
    snapshot = parse_probe_output(host, stdout, checked_at)
    if proc.returncode != 0 and not snapshot.probe_error:
        return snapshot.replace(probe_error=(stderr or f"ssh exited {proc.returncode}").strip())
    return snapshot


def decode_output(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_check(args: argparse.Namespace) -> int:
    hosts = parse_hosts(args.hosts)
    upstream_commit = args.upstream_commit or fetch_upstream_commit(args.upstream_url)
    snapshots = [probe_host(host, timeout=args.timeout) for host in hosts]
    if args.format == "json":
        output = render_json_report(snapshots, upstream_commit=upstream_commit)
    else:
        output = render_markdown_report(snapshots, upstream_commit=upstream_commit)
    write_or_print(output, args.out)
    return 0


def run_promotion_plan(args: argparse.Namespace) -> int:
    try:
        output = build_promotion_plan(args.candidate, parse_hosts(args.hosts))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_or_print(output, args.out)
    return 0


def run_skill_sync_report(args: argparse.Namespace) -> int:
    hosts = parse_hosts(args.hosts)
    snapshots = [probe_host(host, timeout=args.timeout) for host in hosts]
    changed_paths = sorted({path for snapshot in snapshots for path in snapshot.changed_paths})
    risk = classify_upstream_changes(changed_paths)
    output = render_skill_sync_report(snapshots, risk)
    write_or_print(output, args.out)
    return 0


def run_normalization_report(args: argparse.Namespace) -> int:
    if not args.snapshots_json:
        print("error: normalization-report v0.1 requires --snapshots-json", file=sys.stderr)
        return 2
    snapshots = load_normalization_snapshot_payload(args.snapshots_json)
    if args.format == "json":
        output = render_normalization_json(snapshots)
    else:
        output = render_normalization_markdown(snapshots)
    write_or_print(output, args.out)
    return 0


def write_or_print(output: str, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(output)
    else:
        print(output, end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes fleet upgrade train")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="run read-only daily fleet check")
    check.add_argument("--hosts", help="comma-separated SSH aliases; default is Srilu/Main/VPIN")
    check.add_argument("--upstream-url", default=DEFAULT_UPSTREAM_URL)
    check.add_argument("--upstream-commit", default="")
    check.add_argument("--format", choices=["markdown", "json"], default="markdown")
    check.add_argument("--timeout", type=int, default=45)
    check.add_argument("--out")
    check.set_defaults(func=run_check)

    promote = subparsers.add_parser("promotion-plan", help="render weekly promotion checklist")
    promote.add_argument("--candidate", required=True, help="40-character Hermes candidate SHA")
    promote.add_argument("--hosts", help="comma-separated SSH aliases; default is Srilu/Main/VPIN")
    promote.add_argument("--format", choices=["markdown"], default="markdown")
    promote.add_argument("--out")
    promote.set_defaults(func=run_promotion_plan)

    skill_sync = subparsers.add_parser("skill-sync-report", help="render report-only skill/plugin sync posture")
    skill_sync.add_argument("--hosts", help="comma-separated SSH aliases; default is Srilu/Main/VPIN")
    skill_sync.add_argument("--timeout", type=int, default=45)
    skill_sync.add_argument("--out")
    skill_sync.set_defaults(func=run_skill_sync_report)

    normalize = subparsers.add_parser("normalization-report", help="render Srilu/Main/VPIN posture alignment checklist")
    normalize.add_argument("--hosts", help="comma-separated SSH aliases; default is Srilu/Main/VPIN")
    normalize.add_argument("--timeout", type=int, default=45)
    normalize.add_argument("--format", choices=["markdown", "json"], default="markdown")
    normalize.add_argument("--snapshots-json", type=Path)
    normalize.add_argument("--out")
    normalize.set_defaults(func=run_normalization_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
