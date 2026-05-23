"""Flyer Studio rollout-readiness gate primitives.

This module supplies the deterministic, offline rollout-readiness layer
that sits on top of the existing self-evaluation report. It owns:

- ``SEVERITY_RANK`` + ``incident_color``: the single-sourced incident-only
  green/yellow/red helper, imported back by ``tools/flyer-self-evaluation.py``
  so the two tools never drift on color thresholds.
- ``RolloutInputFixture`` (Pydantic v2): host-supplied posture facts the
  repo cannot self-derive (bridge / gateway / cockpit / deploy / source-edit
  posture / open-PR list / merged-not-deployed / replay summary).
- ``compute_source_edit_posture``: five-state policy classification.
- ``compute_rollout_verdict``: rollout-decision verdict combining incidents
  + posture + replay summary into green/yellow/red + reasons.
- ``build_rollout_section``: returns the ``rollout.*`` JSON block.

The CLI never probes a live host. The input fixture is operator-supplied
truth as of fixture-production time. A separate ``pilot-readiness-check``-
style probe runs ON the VPS and is the canonical posture source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Single-sourced incident-color threshold.
# --------------------------------------------------------------------------- #


SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def incident_color(incidents: list[dict]) -> Literal["green", "yellow", "red"]:
    """Map a list of self-eval incidents to a single color.

    Operator-incident view: ALL incidents count, including historical /
    audit-only ones. Imported by ``tools/flyer-self-evaluation.py``; do
    not re-implement the threshold inline elsewhere.
    """
    worst = max(
        (SEVERITY_RANK.get(str(it.get("severity")), 0) for it in incidents),
        default=0,
    )
    return "red" if worst >= 3 else ("yellow" if worst >= 2 else "green")


def active_incident_color(incidents: list[dict]) -> Literal["green", "yellow", "red"]:
    """Rollout-decision view: only count active customer-risk incidents.

    The rollout verdict must not flip RED on a historical / audit-only
    high-severity incident -- e.g., a `customer_copy_static_internal_leak`
    in source-scan mode or a closed project with a poisoned business-name
    fact. Pre-filters by `evidence_details.active_customer_risk` (None or
    True both count; only explicit False is excluded) and then reuses the
    single-sourced threshold from `incident_color`.
    """
    filtered = [
        it
        for it in incidents
        if (it.get("evidence_details") or {}).get("active_customer_risk") is not False
    ]
    return incident_color(filtered)


# --------------------------------------------------------------------------- #
# Pydantic input-fixture schema.
# --------------------------------------------------------------------------- #


SourceEditPosture = Literal[
    "configured_with_smoke",
    "configured_with_smoke_stale",
    "configured_no_smoke",
    "manual_review",
    "unset",
]


CustomerRiskLabel = Literal[
    "customer-routing",
    "lifecycle",
    "copy",
    "payment",
    "schema-migration",
    "deploy-gate",
    "security",
    "auth",
    "none",
]


_RED_PR_LABELS: frozenset[str] = frozenset(
    {
        "customer-routing",
        "lifecycle",
        "copy",
        "payment",
        "schema-migration",
        "deploy-gate",
        "security",
        "auth",
    }
)


class RolloutOpenPR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    title: str = ""
    customer_risk: bool = False
    customer_risk_label: CustomerRiskLabel = "none"


class RolloutMergedNotDeployed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    title: str = ""
    customer_risk_label: CustomerRiskLabel = "none"


class RolloutReplaySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed_ids: list[str] = Field(default_factory=list)


class RolloutInputFixture(BaseModel):
    """Host-supplied posture facts the worktree cannot self-derive.

    All fields are operator-supplied truth as of the time the fixture is
    produced. The CLI never probes a live host.
    """

    model_config = ConfigDict(extra="forbid")

    deploy_marker: str = ""
    bridge_status: Literal["connected", "disconnected", "unknown"] = "unknown"
    gateway_status: Literal["active", "inactive", "unknown"] = "unknown"
    cockpit_status: Literal["healthy", "degraded", "unknown"] = "unknown"
    open_prs: list[RolloutOpenPR] = Field(default_factory=list)
    merged_not_deployed: list[RolloutMergedNotDeployed] = Field(default_factory=list)
    host_supplied_source_edit_posture: SourceEditPosture = "unset"
    source_edit_smoke_evidence_age_days: Optional[int] = None
    provider_routing_changed_at: Optional[datetime] = None
    replay_summary: Optional[RolloutReplaySummary] = None


# --------------------------------------------------------------------------- #
# Source-edit posture rule.
# --------------------------------------------------------------------------- #


def compute_source_edit_posture(
    fixture: Optional[RolloutInputFixture],
) -> tuple[SourceEditPosture, str]:
    """Return (posture, reason_text).

    ``reason_text`` is empty when the posture itself is green-eligible.
    """
    if fixture is None:
        return (
            "unset",
            "source-edit policy posture not supplied; defaulting to manual_review/yellow",
        )
    posture = fixture.host_supplied_source_edit_posture
    if posture == "configured_with_smoke":
        return posture, ""
    if posture == "configured_with_smoke_stale":
        return (
            posture,
            "source-edit smoke evidence stale vs. latest provider-routing change",
        )
    if posture == "configured_no_smoke":
        return (
            posture,
            "source-edit policy configured but spend-gated 5-10 case smoke evidence missing",
        )
    if posture == "manual_review":
        return posture, "source-edit runs through manual_review fallback"
    return (
        posture,
        "source-edit policy posture not supplied; defaulting to manual_review/yellow",
    )


# --------------------------------------------------------------------------- #
# Verdict aggregator.
# --------------------------------------------------------------------------- #


Verdict = Literal["green", "yellow", "red"]


def compute_rollout_verdict(
    *,
    incidents: list[dict],
    fixture: Optional[RolloutInputFixture],
    manual_stale_red_minutes: int = 30,
) -> tuple[Verdict, list[dict]]:
    """Compute rollout verdict + ordered reasons list.

    Reasons are returned RED first then YELLOW so the Markdown renderer
    does not need to re-sort.
    """
    red_reasons: list[dict] = []
    yellow_reasons: list[dict] = []

    # Rollout-decision color is active-only. The operator-incident `report.status`
    # field continues to use `incident_color` (full set) for visibility; the
    # rollout verdict refuses to flip RED on a historical / audit-only incident.
    color = active_incident_color(incidents)
    if color == "red":
        red_reasons.append(
            {"severity": "red", "text": "self-eval incident severity is red"}
        )
    elif color == "yellow":
        yellow_reasons.append(
            {"severity": "yellow", "text": "self-eval incident severity is yellow"}
        )

    def _bump_red(text: str) -> None:
        red_reasons.append({"severity": "red", "text": text})

    def _bump_yellow(text: str) -> None:
        yellow_reasons.append({"severity": "yellow", "text": text})

    for inc in incidents:
        details = inc.get("evidence_details") or {}
        active = bool(details.get("active_customer_risk"))
        kind = inc.get("type")
        if kind == "customer_copy_internal_leak" and active:
            _bump_red("active customer-copy internal leak in outbound text")
        elif kind == "duplicate_initial_ack" and active:
            _bump_red("active duplicate initial acknowledgement to same customer")
        elif kind == "manual_source_edit_stale":
            age = details.get("queued_age_minutes")
            if isinstance(age, (int, float)) and age >= manual_stale_red_minutes:
                _bump_red(
                    f"manual source-edit queue stale >={manual_stale_red_minutes}min "
                    f"(age={float(age):.1f}min)"
                )
            else:
                _bump_yellow("manual source-edit queue rows present")
        elif active:
            _bump_yellow(f"active customer-risk incident: {kind}")

    if fixture is None:
        _bump_yellow("readiness input fixture not supplied; posture unknown")
    else:
        if fixture.bridge_status == "disconnected":
            _bump_red("bridge is disconnected on the host")
        if fixture.gateway_status == "inactive":
            _bump_red("Hermes gateway is inactive on the host")
        if fixture.bridge_status == "unknown":
            _bump_yellow("bridge status posture unknown")
        if fixture.gateway_status == "unknown":
            _bump_yellow("gateway status posture unknown")
        if fixture.cockpit_status == "unknown":
            _bump_yellow("cockpit status posture unknown")
        if fixture.cockpit_status == "degraded":
            _bump_yellow("cockpit reports degraded health")

        if fixture.merged_not_deployed:
            risky = [
                pr for pr in fixture.merged_not_deployed
                if pr.customer_risk_label in _RED_PR_LABELS
            ]
            if risky:
                _bump_red(
                    "merged-not-deployed includes customer-risk PRs: "
                    + ", ".join(f"#{p.number}" for p in risky)
                )
            else:
                _bump_yellow(
                    "merged-not-deployed PRs present: "
                    + ", ".join(f"#{p.number}" for p in fixture.merged_not_deployed)
                )

        risky_open = [pr for pr in fixture.open_prs if pr.customer_risk]
        if risky_open:
            _bump_yellow(
                "open PRs flagged customer-risk: "
                + ", ".join(f"#{p.number}" for p in risky_open)
            )

        if fixture.deploy_marker == "":
            _bump_yellow("deploy marker not supplied")

        posture, posture_reason = compute_source_edit_posture(fixture)
        if posture != "configured_with_smoke":
            _bump_yellow(posture_reason)

        if fixture.replay_summary is None:
            _bump_yellow(
                "replay summary not supplied; rollout decision is unsafe without it"
            )
        elif fixture.replay_summary.failed_ids:
            _bump_red(
                "rollout replay failed: "
                + ", ".join(fixture.replay_summary.failed_ids)
            )

    reasons = red_reasons + yellow_reasons
    if red_reasons:
        return "red", reasons
    if yellow_reasons:
        return "yellow", reasons
    return "green", reasons


# --------------------------------------------------------------------------- #
# Section builder (called by tools/flyer-self-evaluation.py).
# --------------------------------------------------------------------------- #


def build_rollout_section(
    *,
    incidents: list[dict],
    fixture: Optional[RolloutInputFixture],
    manual_stale_red_minutes: int = 30,
) -> dict[str, Any]:
    """Build the ``rollout.*`` block injected into self-evaluation JSON."""
    posture, posture_reason = compute_source_edit_posture(fixture)

    verdict, reasons = compute_rollout_verdict(
        incidents=incidents,
        fixture=fixture,
        manual_stale_red_minutes=manual_stale_red_minutes,
    )

    def _count(kind: str) -> int:
        return sum(1 for it in incidents if it.get("type") == kind)

    return {
        "verdict": verdict,
        "reasons": reasons,
        "open_flyer_prs": (
            [pr.model_dump() for pr in fixture.open_prs] if fixture else []
        ),
        "merged_not_deployed": (
            [pr.model_dump() for pr in fixture.merged_not_deployed] if fixture else []
        ),
        "deploy_marker": fixture.deploy_marker if fixture else "",
        "bridge_status": fixture.bridge_status if fixture else "unknown",
        "gateway_status": fixture.gateway_status if fixture else "unknown",
        "cockpit_status": fixture.cockpit_status if fixture else "unknown",
        "source_edit_posture": posture,
        "source_edit_posture_reason": posture_reason,
        "stale_manual_queue_incidents": _count("manual_source_edit_stale"),
        "active_customer_risk_incidents": sum(
            1
            for it in incidents
            if (it.get("evidence_details") or {}).get("active_customer_risk") is True
        ),
        "customer_copy_leak_incidents": _count("customer_copy_internal_leak"),
        "duplicate_initial_ack_incidents": _count("duplicate_initial_ack"),
        "replay_summary": (
            fixture.replay_summary.model_dump()
            if fixture and fixture.replay_summary
            else None
        ),
    }


# --------------------------------------------------------------------------- #
# Markdown banner helpers (consumed by flyer-self-evaluation render_markdown).
# --------------------------------------------------------------------------- #


def render_rollout_banner(rollout: dict[str, Any]) -> str:
    """Render the top-of-report banner line for a rollout block."""
    verdict = str(rollout.get("verdict") or "unknown").upper()
    reasons = rollout.get("reasons") or []
    if not reasons:
        return f"**Rollout: {verdict}**"
    n = len(reasons)
    return f"**Rollout: {verdict} — {n} reason{'s' if n != 1 else ''}**"


def render_rollout_section(rollout: dict[str, Any]) -> list[str]:
    """Render the full Rollout Readiness Markdown section."""
    verdict = str(rollout.get("verdict") or "unknown").upper()
    bridge = rollout.get("bridge_status") or "unknown"
    gateway = rollout.get("gateway_status") or "unknown"
    cockpit = rollout.get("cockpit_status") or "unknown"
    deploy_marker = rollout.get("deploy_marker") or "(none)"
    posture = rollout.get("source_edit_posture") or "unset"
    posture_reason = rollout.get("source_edit_posture_reason") or ""
    posture_line = f"- Source-edit posture: {posture}"
    if posture_reason:
        posture_line += f" ({posture_reason})"
    lines: list[str] = ["", "## Rollout Readiness", ""]
    lines.append(f"- Verdict: {verdict}")
    lines.append(f"- Bridge: {bridge}; Gateway: {gateway}; Cockpit: {cockpit}")
    lines.append(f"- Deploy marker: {deploy_marker}")
    lines.append(posture_line)
    open_prs = rollout.get("open_flyer_prs") or []
    if open_prs:
        lines.append(
            "- Open Flyer PRs: "
            + ", ".join(f"#{pr.get('number')} {pr.get('title') or ''}".strip() for pr in open_prs)
        )
    merged = rollout.get("merged_not_deployed") or []
    if merged:
        lines.append(
            "- Merged-not-deployed: "
            + ", ".join(f"#{pr.get('number')} {pr.get('title') or ''}".strip() for pr in merged)
        )
    replay = rollout.get("replay_summary")
    if isinstance(replay, dict):
        lines.append(
            f"- Replay summary: {replay.get('passed', 0)}/{replay.get('total', 0)} passed"
        )
        failed = replay.get("failed_ids") or []
        if failed:
            lines.append(f"  - Failed: {', '.join(failed)}")
    else:
        lines.append("- Replay summary: not supplied")
    counts = (
        ("stale_manual_queue_incidents", "stale manual-queue incidents"),
        ("active_customer_risk_incidents", "active customer-risk incidents"),
        ("customer_copy_leak_incidents", "customer-copy leak incidents"),
        ("duplicate_initial_ack_incidents", "duplicate initial-ack incidents"),
    )
    for key, label in counts:
        value = int(rollout.get(key) or 0)
        if value:
            lines.append(f"- {label}: {value}")
    reasons = rollout.get("reasons") or []
    lines.extend(["", "### Reasons (RED first)", ""])
    if not reasons:
        lines.append("- None.")
    else:
        for reason in reasons:
            sev = str(reason.get("severity") or "").upper()
            lines.append(f"- {sev}: {reason.get('text') or ''}")
    return lines


def load_rollout_input(path: str | None) -> Optional[RolloutInputFixture]:
    """Parse a rollout-input JSON file into a Pydantic RolloutInputFixture.

    Returns ``None`` when ``path`` is falsy. Raises ``ValidationError`` from
    Pydantic if the file content does not conform (the CLI surfaces this
    as a non-zero exit).
    """
    if not path:
        return None
    import json as _json
    from pathlib import Path as _Path

    raw = _json.loads(_Path(path).read_text(encoding="utf-8"))
    return RolloutInputFixture.model_validate(raw)


def merge_replay_summary_override(
    fixture: Optional[RolloutInputFixture],
    override_path: str | None,
) -> Optional[RolloutInputFixture]:
    """Replace ``fixture.replay_summary`` with the JSON at ``override_path``."""
    if not override_path:
        return fixture
    import json as _json
    from pathlib import Path as _Path

    raw = _json.loads(_Path(override_path).read_text(encoding="utf-8"))
    summary = RolloutReplaySummary.model_validate(raw)
    if fixture is None:
        return RolloutInputFixture(replay_summary=summary)
    data = fixture.model_dump()
    data["replay_summary"] = summary.model_dump()
    return RolloutInputFixture.model_validate(data)
