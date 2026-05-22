from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


BACKLOG_KEYS: tuple[str, ...] = (
    "source_edit_smoke_proof",
    "persistent_brand_memory_activation",
    "session_search_campaign_history",
    "background_render_qa_exports",
    "xai_grok_provider_posture",
    "x_search_fetching",
    "x_social_posting_approval",
    "codex_offline_self_improvement",
    "native_video_conversion",
    "auto_kanban_operator_work",
    "multi_format_export_truthfulness",
    "autonomous_campaigns_with_approval",
    "campaign_analytics_memory",
    "publishing_engine_approval_gates",
    "hybrid_layout_final_renderer",
    "marketing_os_long_term",
)

_ROLLUP_RANK = {"green": 0, "yellow": 1, "red": 2}
_SOURCE_EDIT_READY = "configured_with_smoke"
_COMPLETED_CAMPAIGN_STATUSES = {"delivered", "completed", "closed_sent"}
_ACTIVE_CUSTOMER_STATUSES = {"trial", "active", "payment_pending"}

_BACKLOG_META: dict[str, tuple[str, str, str]] = {
    "source_edit_smoke_proof": (
        "Source-edit smoke proof",
        "operator",
        "Run a spend-gated 5-10 case source-preservation smoke before any automated exact-edit reliance.",
    ),
    "persistent_brand_memory_activation": (
        "Persistent brand memory activation",
        "product",
        "Use the readiness signal only after at least one QA-passed delivered asset exists for a customer.",
    ),
    "session_search_campaign_history": (
        "Session search and campaign history",
        "Hermes",
        "Use Hermes memory/session search as retrieval substrate; Flyer owns brand/campaign policy.",
    ),
    "background_render_qa_exports": (
        "Background render, QA, and export jobs",
        "Hermes",
        "Use Hermes background work for orchestration; keep Flyer state transitions explicit and replay-tested.",
    ),
    "xai_grok_provider_posture": (
        "xAI/Grok provider posture",
        "operator",
        "Treat as orchestration-provider evaluation only; no customer routing until credentials and tests exist.",
    ),
    "x_search_fetching": (
        "X search/fetching for marketing research",
        "Hermes",
        "Use Hermes X integration for approved research; do not auto-post or mutate customer channels.",
    ),
    "x_social_posting_approval": (
        "X/social posting approval gates",
        "operator",
        "Require explicit customer/operator approval before publishing to any external account.",
    ),
    "codex_offline_self_improvement": (
        "Codex offline self-improvement loop",
        "engineering",
        "Keep code/prompt/model evolution behind tests, reviewers, PR, and deploy; no runtime self-modification.",
    ),
    "native_video_conversion": (
        "Native video conversion",
        "product",
        "Design as a new approved media export lane; no paid video generation without explicit operator gate.",
    ),
    "auto_kanban_operator_work": (
        "Auto Kanban operator work",
        "operator",
        "Use task decomposition for operator work only; keep production changes PR-gated.",
    ),
    "multi_format_export_truthfulness": (
        "Multi-format export truthfulness",
        "engineering",
        "Do not claim Instagram story/post/PDF formats until generated artifacts match those shapes.",
    ),
    "autonomous_campaigns_with_approval": (
        "Autonomous campaigns with approval",
        "product",
        "Campaign automation must stop at approval gates before publishing, spending, or messaging customers.",
    ),
    "campaign_analytics_memory": (
        "Campaign analytics memory",
        "product",
        "Store campaign outcomes separately from generation state before ranking creative performance.",
    ),
    "publishing_engine_approval_gates": (
        "Publishing engine approval gates",
        "operator",
        "Publishing connectors need identity, approval, and audit gates before any live post action.",
    ),
    "hybrid_layout_final_renderer": (
        "Hybrid layout final renderer",
        "engineering",
        "Prefer deterministic text placement for final assets where possible; keep image models in draft/visual roles.",
    ),
    "marketing_os_long_term": (
        "Autonomous local marketing OS",
        "product",
        "Long-term direction; depends on brand memory, exports, approvals, analytics, and publishing proof.",
    ),
}


class CustomerSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    business_name: str | None = None
    business_category: str | None = None
    preferred_language: str | None = None
    status: Literal["trial", "active", "payment_pending", "suspended", "cancelled", "inactive"] = "trial"
    active_brand_assets: int = Field(default=0, ge=0)


class CampaignSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    customer_id: str
    status: str
    final_asset_count: int = Field(default=0, ge=0)
    qa_passed: bool = False
    qa_checked_at: datetime | None = None


class RolloutSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["green", "yellow", "red"] = "yellow"
    source_edit_posture: Literal[
        "configured_with_smoke",
        "configured_with_smoke_stale",
        "configured_no_smoke",
        "manual_review",
        "unset",
    ] = "unset"
    reasons: list[dict[str, Any]] = Field(default_factory=list)


class PlatformTruthfulnessSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instagram_story_truthful: bool = False
    reason: str = ""


class OperatingLayerReadinessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    collected_at: datetime
    customers: list[CustomerSnapshot] = Field(default_factory=list)
    campaigns: list[CampaignSnapshot] = Field(default_factory=list)
    rollout: RolloutSnapshot = Field(default_factory=RolloutSnapshot)
    platform_truthfulness: PlatformTruthfulnessSnapshot = Field(default_factory=PlatformTruthfulnessSnapshot)


def _external_rollout(raw: dict[str, Any] | None) -> RolloutSnapshot | None:
    if not isinstance(raw, dict):
        return None
    return RolloutSnapshot(
        verdict=str(raw.get("verdict") or raw.get("status") or "yellow").lower(),
        source_edit_posture=str(raw.get("source_edit_posture") or raw.get("source_edit", {}).get("posture") or "unset"),
        reasons=raw.get("reasons") if isinstance(raw.get("reasons"), list) else [],
    )


def _conservative_rollout(
    input_rollout: RolloutSnapshot,
    external_rollout: RolloutSnapshot | None,
) -> tuple[RolloutSnapshot, list[str]]:
    if external_rollout is None:
        return input_rollout, []
    reasons: list[str] = []
    verdict = max((input_rollout.verdict, external_rollout.verdict), key=lambda item: _ROLLUP_RANK.get(item, 1))
    source_edit_posture = input_rollout.source_edit_posture
    if input_rollout.source_edit_posture != external_rollout.source_edit_posture:
        reasons.append(
            "rollout conflict: operating-layer input reports "
            f"{input_rollout.source_edit_posture}, self-eval rollout reports {external_rollout.source_edit_posture}"
        )
        source_edit_posture = (
            _SOURCE_EDIT_READY
            if input_rollout.source_edit_posture == external_rollout.source_edit_posture == _SOURCE_EDIT_READY
            else input_rollout.source_edit_posture
            if input_rollout.source_edit_posture != _SOURCE_EDIT_READY
            else external_rollout.source_edit_posture
        )
    if input_rollout.verdict != external_rollout.verdict:
        reasons.append(
            f"rollout conflict: operating-layer verdict {input_rollout.verdict}, self-eval verdict {external_rollout.verdict}"
        )
    return (
        RolloutSnapshot(
            verdict=verdict,
            source_edit_posture=source_edit_posture,
            reasons=input_rollout.reasons + external_rollout.reasons,
        ),
        reasons,
    )


def _ready_campaigns_by_customer(campaigns: list[CampaignSnapshot]) -> dict[str, list[CampaignSnapshot]]:
    ready: dict[str, list[CampaignSnapshot]] = {}
    for campaign in campaigns:
        if (
            campaign.status in _COMPLETED_CAMPAIGN_STATUSES
            and campaign.final_asset_count > 0
            and campaign.qa_passed
            and campaign.qa_checked_at is not None
        ):
            ready.setdefault(campaign.customer_id, []).append(campaign)
    return ready


def _brand_memory_section(model: OperatingLayerReadinessInput) -> dict[str, Any]:
    active_customers = [customer for customer in model.customers if customer.status in _ACTIVE_CUSTOMER_STATUSES]
    ready_campaigns = _ready_campaigns_by_customer(model.campaigns)
    ready_ids: list[str] = []
    reasons: list[str] = []
    for customer in active_customers:
        missing: list[str] = []
        if not customer.business_name:
            missing.append("business name")
        if not customer.business_category:
            missing.append("business category")
        if not customer.preferred_language:
            missing.append("preferred language")
        if customer.active_brand_assets <= 0:
            missing.append("active brand asset")
        customer_campaigns = [campaign for campaign in model.campaigns if campaign.customer_id == customer.customer_id]
        has_timestamp_gap = any(
            campaign.status in _COMPLETED_CAMPAIGN_STATUSES
            and campaign.final_asset_count > 0
            and campaign.qa_passed
            and campaign.qa_checked_at is None
            for campaign in customer_campaigns
        )
        if customer.customer_id not in ready_campaigns:
            missing.append("delivered/completed QA-passed campaign")
        if has_timestamp_gap:
            missing.append("QA timestamp")
        if missing:
            reasons.append(f"{customer.customer_id} missing " + ", ".join(missing))
        else:
            ready_ids.append(customer.customer_id)
    total = len(active_customers)
    return {
        "status": "ready_for_at_least_one_customer" if ready_ids else "yellow",
        "ready_customer_count": len(ready_ids),
        "total_customer_count": total,
        "coverage_ratio": round(len(ready_ids) / total, 3) if total else 0.0,
        "ready_customer_ids": ready_ids,
        "reasons": reasons,
    }


def _campaign_history_section(model: OperatingLayerReadinessInput) -> dict[str, Any]:
    completed = [
        campaign
        for campaign in model.campaigns
        if campaign.status in _COMPLETED_CAMPAIGN_STATUSES and campaign.final_asset_count > 0
    ]
    return {
        "completed_campaign_count": len(completed),
        "qa_passed_count": sum(1 for campaign in completed if campaign.qa_passed),
    }


def _backlog_items(source_edit_ready: bool, platform_truthful: bool) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key in BACKLOG_KEYS:
        title, owner, guardrail = _BACKLOG_META[key]
        status = "deferred"
        if key == "source_edit_smoke_proof" and not source_edit_ready:
            status = "blocked"
        if key == "multi_format_export_truthfulness" and not platform_truthful:
            status = "blocked"
            guardrail = "Instagram story/post/export claims remain blocked until generated artifacts match those formats."
        items.append(
            {
                "key": key,
                "title": title,
                "status": status,
                "owner": owner,
                "guardrail": guardrail,
            }
        )
    return items


def _next_action(backlog: list[dict[str, str]]) -> dict[str, str]:
    for item in backlog:
        if item["status"] == "blocked":
            return {
                "key": item["key"],
                "owner": item["owner"],
                "summary": f"Next: {item['key']} - {item['owner']} - {item['guardrail']}",
            }
    first = backlog[0]
    return {
        "key": first["key"],
        "owner": first["owner"],
        "summary": f"Next: {first['key']} - {first['owner']} - {first['guardrail']}",
    }


def build_operating_layer_section(payload: dict[str, Any], rollout: dict[str, Any] | None = None) -> dict[str, Any]:
    model = OperatingLayerReadinessInput.model_validate(payload)
    resolved_rollout, conflict_reasons = _conservative_rollout(model.rollout, _external_rollout(rollout))
    source_edit_ready = resolved_rollout.source_edit_posture == _SOURCE_EDIT_READY
    platform_truthful = model.platform_truthfulness.instagram_story_truthful
    brand_memory = _brand_memory_section(model)
    campaign_history = _campaign_history_section(model)
    backlog = _backlog_items(source_edit_ready=source_edit_ready, platform_truthful=platform_truthful)
    yellow_reasons = list(conflict_reasons)
    if resolved_rollout.verdict != "green":
        yellow_reasons.append(f"rollout verdict is {resolved_rollout.verdict}")
    if not source_edit_ready:
        yellow_reasons.append(f"source-edit posture is {resolved_rollout.source_edit_posture}")
    if brand_memory["status"] == "yellow":
        yellow_reasons.extend(brand_memory["reasons"])
    if not platform_truthful:
        yellow_reasons.append(model.platform_truthfulness.reason or "multi-format export truthfulness not proven")
    status = "green" if not yellow_reasons else "yellow"
    if resolved_rollout.verdict == "red":
        status = "red"
    return {
        "schema_version": model.schema_version,
        "collected_at": model.collected_at.isoformat().replace("+00:00", "Z"),
        "status": status,
        "brand_memory": brand_memory,
        "campaign_history": campaign_history,
        "source_edit": {
            "status": "ready" if source_edit_ready else "deferred",
            "posture": resolved_rollout.source_edit_posture,
            "reason": "configured_with_smoke" if source_edit_ready else f"source-edit remains {resolved_rollout.source_edit_posture}",
        },
        "platform_truthfulness": {
            "instagram_story_truthful": platform_truthful,
            "reason": model.platform_truthfulness.reason,
        },
        "rollout_guard": {
            "status": "clear" if not conflict_reasons and resolved_rollout.verdict == "green" else status,
            "verdict": resolved_rollout.verdict,
            "reasons": yellow_reasons,
        },
        "capabilities": [
            {
                "key": "persistent_brand_memory_readiness_signal",
                "status": brand_memory["status"],
                "guardrail": "Readiness signal only; activation remains a deferred backlog item.",
            }
        ],
        "deferred_backlog": backlog,
        "next_action": _next_action(backlog),
    }


def render_operating_layer_markdown(section: dict[str, Any]) -> list[str]:
    brand = section.get("brand_memory") if isinstance(section.get("brand_memory"), dict) else {}
    source_edit = section.get("source_edit") if isinstance(section.get("source_edit"), dict) else {}
    next_action = section.get("next_action") if isinstance(section.get("next_action"), dict) else {}
    lines = [
        "",
        "## Flyer Hermes Operating Layer",
        "",
        f"- Status: {section.get('status', 'unknown')}",
        (
            "- Brand memory: "
            f"{brand.get('status', 'unknown')} "
            f"({brand.get('ready_customer_count', 0)}/{brand.get('total_customer_count', 0)} customers ready)"
        ),
        f"- Source edit: {source_edit.get('status', 'unknown')} ({source_edit.get('posture', 'unknown')})",
        f"- Next: {next_action.get('summary', 'none')}",
    ]
    blockers = [
        item
        for item in section.get("deferred_backlog", [])
        if isinstance(item, dict) and item.get("status") == "blocked"
    ]
    if blockers:
        lines.append("- Blocked:")
        lines.extend(f"  - {item.get('key')}: {item.get('guardrail')}" for item in blockers[:5])
    return lines
