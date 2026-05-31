"""Flyer Studio recovery helpers.

Pure helpers live here so the watchdog CLI can stay small and tests can run on
Windows without touching the live WhatsApp bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
from contextlib import contextmanager
from typing import Iterable, Iterator, Literal
import os
import urllib.request


EvidenceQuality = Literal["strong", "weak", "missing"]


@dataclass(frozen=True)
class RecoverySignal:
    failure_class: str
    severity: str
    project_id: str
    chat_id: str
    detail: str
    canonical_source: str
    evidence_quality: EvidenceQuality = "weak"
    provider_message_id: str = ""
    observed_at: datetime | None = None


@dataclass(frozen=True)
class CopyLintResult:
    ok: bool
    reasons: list[str]


@dataclass(frozen=True)
class AckDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class AutoRepairDecision:
    decision: Literal["hermes_plan_eligible", "hard_stop", "manual_required"]
    reason: str
    blockers: list[str]


FORBIDDEN_CUSTOMER_TERMS = [
    "provider",
    "manual queue",
    "source-preserving",
    "operator",
    "audit",
    "stack trace",
    "traceback",
    "pytest",
    "codex",
    "hermes",
    "deploy",
]

BUNDLE_SENSITIVE_KEYS = {
    "chat_id",
    "sender_phone",
    "customer_phone",
    "phone",
    "contact_phone",
    "contact_info",
}


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


_AUTOREPAIR_TRUST_RISK_TOKENS = (
    "wrong business",
    "wrong brand",
    "visible wrong business",
    "visible wrong brand",
    "contact",
    "phone",
    "address",
    "price mismatch",
    "wrong price",
    "promotion_end",
    "promotion end",
    "schedule",
    "event_date",
    "date",
    "time",
)

_AUTOREPAIR_DEPENDENCY_TOKENS = (
    "pillow is required",
    "pillow is unavailable",
    "modulenotfounderror",
    "no module named",
    "provider_timeout",
    "bad gateway",
    "gateway timeout",
)

_UNSAFE_REPAIR_INSTRUCTION_TOKENS = (
    "change the business",
    "replace the business",
    "change business",
    "replace business",
    "change the brand",
    "replace the brand",
    "change brand",
    "replace brand",
    "change the phone",
    "replace the phone",
    "change phone",
    "replace phone",
    "change the address",
    "replace the address",
    "change address",
    "replace address",
    "change the price",
    "replace the price",
    "change price",
    "replace price",
    "change the schedule",
    "replace the schedule",
    "change schedule",
    "replace schedule",
    "change the date",
    "replace the date",
    "change date",
    "replace date",
    "change the time",
    "replace the time",
    "change time",
    "replace time",
    "change the promotion end",
    "replace the promotion end",
    "update the promotion end",
    "change promotion end",
    "replace promotion end",
    "replace ",
    "use a different business",
    "use another business",
)


def _project_has_offer_context(project: object) -> bool:
    raw = f"{getattr(project, 'raw_request', '')} {getattr(getattr(project, 'fields', None), 'notes', '')}".strip()
    if re.search(r"\b(?:price|prices|offer|offers|sale|discount|free|items?|menu|combo|biryani|snacks?)\b", raw, flags=re.IGNORECASE):
        return True
    for fact in getattr(project, "locked_facts", []) or []:
        fact_id = str(getattr(fact, "fact_id", ""))
        label = str(getattr(fact, "label", ""))
        value = str(getattr(fact, "value", ""))
        if value and (
            fact_id.startswith("detail_")
            or fact_id.startswith("offer:")
            or fact_id == "pricing_structure"
            or "item" in label.casefold()
            or "offer" in label.casefold()
        ):
            return True
    return False


def classify_flyer_qa_for_autorepair(blockers: Iterable[str], project: object) -> AutoRepairDecision:
    """Classify QA blockers into Hermes-plannable repair vs fail-closed cases."""
    normalized = [str(blocker).strip() for blocker in blockers if str(blocker).strip()]
    if not normalized:
        return AutoRepairDecision("manual_required", "no_blockers", [])

    lowered = [item.casefold() for item in normalized]
    if any(any(token in item for token in _AUTOREPAIR_TRUST_RISK_TOKENS) for item in lowered):
        return AutoRepairDecision("hard_stop", "customer_trust_risk", normalized)
    if any(any(token in item for token in _AUTOREPAIR_DEPENDENCY_TOKENS) for item in lowered):
        return AutoRepairDecision("manual_required", "provider_or_dependency_failure", normalized)

    eligible = False
    for item in lowered:
        if "missing rendered fact: detail_" in item:
            eligible = True
        elif "manifest reports missing facts:" in item and "detail_" in item:
            eligible = True
        elif re.search(r"missing required visible fact:\s*item:\d+:name", item):
            eligible = _project_has_offer_context(project)
        elif re.search(r"missing required visible fact:\s*(campaign_title|headline|pricing_structure|offer:\d+)", item):
            eligible = _project_has_offer_context(project)
        elif "duplicate rendered fact" in item and "detail_" in item:
            eligible = True
        elif "instruction text leaked into flyer copy" in item:
            eligible = True

    if eligible:
        return AutoRepairDecision("hermes_plan_eligible", "qa_visible_copy_repairable", normalized)
    return AutoRepairDecision("manual_required", "unknown_blocker_pattern", normalized)


def resolve_hermes_model(model: str, *, hermes_config_path: Path = Path("/root/.hermes/config.yaml")) -> str:
    requested = (model or "").strip()
    if requested and requested != "default_hermes_gateway":
        return requested
    if hermes_config_path.exists():
        text = hermes_config_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"(?m)^\s*default\s*:\s*['\"]?([^'\"\n#]+)", text)
        if match:
            return match.group(1).strip()
    env_model = os.environ.get("HERMES_DEFAULT_MODEL", "").strip()
    if env_model:
        return env_model
    return ""


def repair_instruction_is_safe(instruction: str) -> bool:
    lowered = (instruction or "").casefold()
    return bool(lowered.strip()) and not any(token in lowered for token in _UNSAFE_REPAIR_INSTRUCTION_TOKENS)


def plan_flyer_autorepair(*, project: object, blockers: Iterable[str], rendered_text: str = "", model: str = "default_hermes_gateway") -> dict:
    """Ask Hermes' configured LLM substrate for a bounded poster-repair plan.

    The caller treats unavailable or malformed planner output as fail-closed
    manual review. Tests monkeypatch this function; production uses the same
    OpenRouter/Hermes env convention as the renderer.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        for env_path in (Path("/root/.hermes/.env"), Path("/opt/shift-agent/.env")):
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip("'\"")
                    break
            if api_key:
                break
    if not api_key:
        return {"action": "manual_required", "reason": "planner_unavailable"}

    planner_model = resolve_hermes_model(model)
    if not planner_model:
        return {"action": "manual_required", "reason": "hermes_model_unconfigured"}
    prompt = {
        "project_id": getattr(project, "project_id", ""),
        "raw_request": getattr(project, "raw_request", ""),
        "locked_facts": [
            {"fact_id": getattr(f, "fact_id", ""), "label": getattr(f, "label", ""), "value": getattr(f, "value", "")}
            for f in (getattr(project, "locked_facts", []) or [])
        ],
        "qa_blockers": list(blockers),
        "rendered_text": rendered_text[:2000],
        "instruction": (
            "Return compact JSON only. If safe, use action regenerate_with_instruction "
            "and a repair_instruction under 500 chars. Never change business identity, "
            "contact, address, prices, schedule, or unauthorized brand. Otherwise use "
            "manual_required."
        ),
    }
    payload = {
        "model": planner_model,
        "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://triveni.local/flyer-autorepair",
            "X-Title": "Flyer Studio Autorepair",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:
        return {"action": "manual_required", "reason": f"planner_error:{exc.__class__.__name__}"}
    if parsed.get("action") != "regenerate_with_instruction":
        return {"action": parsed.get("action", "manual_required"), "reason": parsed.get("reason", "planner_declined")}
    instruction = str(parsed.get("repair_instruction") or "").strip()
    if not repair_instruction_is_safe(instruction):
        return {"action": "manual_required", "reason": "planner_unsafe_or_empty_instruction"}
    return {"action": "regenerate_with_instruction", "repair_instruction": instruction[:1000], "confidence": str(parsed.get("confidence", ""))}


def _redact_identifier(value: str) -> str:
    digest = sha256_text(value).split(":", 1)[1][:12]
    return f"[redacted:{digest}]"


def redact_text_for_bundle(text: str) -> str:
    redacted = re.sub(r"\+?\d{7,15}@s\.whatsapp\.net", lambda m: _redact_identifier(m.group(0)), text or "")
    redacted = re.sub(r"\+\d[\d .()-]{6,}\d", lambda m: _redact_identifier(m.group(0)), redacted)
    return redacted


def sanitize_for_repair_bundle(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if str(key) in BUNDLE_SENSITIVE_KEYS:
                safe[f"{key}_hash"] = sha256_text(str(item or "")) if item else ""
                continue
            safe[key] = sanitize_for_repair_bundle(item)
        return safe
    if isinstance(value, list):
        return [sanitize_for_repair_bundle(item) for item in value]
    if isinstance(value, str):
        return redact_text_for_bundle(value)
    return value


def _parse_project_id(detail: str) -> str:
    match = re.search(r"\bproject_id=(F\d{4,})\b|\b(F\d{4,})\b", detail or "")
    if not match:
        return ""
    return next(group for group in match.groups() if group)


def _canonical_detail(detail: str) -> str:
    body = detail or ""
    for marker in [
        "concept_generation_failed",
        "edit_generation_failed",
        "revision_regeneration_failed",
        "regeneration_failed",
        "visual_qa_failed",
        "ack_error",
        "source edit provider is not configured",
        "source edit reference",
        "json_parse_failed",
        "select_failed",
        "status_failed",
    ]:
        if marker in body:
            return marker
    if "exit=" in body:
        return "subprocess_exit"
    return body[:120]


def _parse_detail_field(detail: str, key: str) -> str:
    match = re.search(rf"(?:^|[;\s]){re.escape(key)}=([^;\s]+)", detail or "")
    return match.group(1).strip() if match else ""


def _parse_inbound_message_id(row: dict, detail: str) -> str:
    for key in ["message_id", "provider_message_id", "inbound_message_id", "root_message_id"]:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return _parse_detail_field(detail, "message_id") or _parse_detail_field(detail, "provider_message_id")


def _has_customer_origin_evidence(row: dict, detail: str, provider_message_id: str) -> bool:
    if provider_message_id:
        return True
    sender_role = str(row.get("sender_role") or _parse_detail_field(detail, "sender_role") or "").strip().lower()
    if sender_role in {"customer", "owner", "staff", "employee"}:
        return True
    return bool(str(row.get("chat_id") or "").strip() and str(row.get("from_me") or "").lower() in {"false", "0"})


def _has_nonblank_ack_error(detail: str) -> bool:
    match = re.search(r"(?:^|[;\s])ack_error=([^;]*)", detail or "", flags=re.IGNORECASE)
    return bool(match and match.group(1).strip())


def classify_decision(row: dict, projects: dict[str, dict]) -> RecoverySignal | None:
    if row.get("type") != "cf_router_intercepted":
        return None
    reason = str(row.get("reason") or "")
    if not reason.startswith("flyer_"):
        return None
    detail = str(row.get("detail") or "")
    project_id = _parse_project_id(detail)
    chat_id = str(row.get("chat_id") or "")
    raw_subprocess_rc = row.get("subprocess_rc")
    try:
        subprocess_rc = int(raw_subprocess_rc) if raw_subprocess_rc is not None else None
    except (TypeError, ValueError):
        subprocess_rc = None
    ack_error_present = _has_nonblank_ack_error(detail)
    if subprocess_rc == 0 and not ack_error_present:
        return None
    lower = detail.lower()
    failure_class = ""
    if any(
        marker in lower
        for marker in [
            "concept_generation_failed",
            "edit_generation_failed",
            "revision_regeneration_failed",
            "regeneration_failed",
            "visual_qa_failed",
        ]
    ):
        failure_class = "concept_generation_failed"
    elif "source edit provider" in lower or "source edit reference" in lower:
        failure_class = "provider_unavailable"
    elif ack_error_present or any(marker in lower for marker in ["bridge_send_failed", "connect_failed", "http_error"]):
        failure_class = "bridge_send_failed"
    elif any(marker in lower for marker in ["json_parse_failed", "select_failed", "status_failed", "exit="]):
        if project_id:
            failure_class = "state_transition_failed"
    if not failure_class:
        return None
    provider_message_id = _parse_inbound_message_id(row, detail)
    evidence = "strong" if _has_customer_origin_evidence(row, detail, provider_message_id) else "weak"
    return RecoverySignal(
        failure_class=failure_class,
        severity="warning",
        project_id=project_id,
        chat_id=chat_id,
        detail=detail,
        canonical_source=_canonical_detail(detail),
        evidence_quality=evidence,  # type: ignore[arg-type]
        provider_message_id=provider_message_id,
        observed_at=_parse_recovery_ts(str(row.get("ts") or "")),
    )


def _manual_reason_failure_class(reason_code: str, detail: str) -> str:
    lowered = f"{reason_code} {detail}".lower()
    if any(token in lowered for token in (
        "visual_qa_failed",
        "provider_timeout",
        "missing_required_facts",
        "dependency_missing",
        "pillow is required",
        "pillow is unavailable",
    )):
        return "concept_generation_failed"
    if any(token in lowered for token in ("provider_unavailable", "reference_provider", "source_edit_provider")):
        return "provider_unavailable"
    return "state_transition_failed"


def classify_stale_manual_project(project: dict, *, now: datetime, stale_after: timedelta) -> RecoverySignal | None:
    """Classify stale manual-review state even when no failing router row exists.

    This widens recovery from audit-only observation to durable project state:
    if a customer-visible flyer project is parked in manual review beyond the
    SLA, the repair engine should queue a bounded worker bundle.
    """
    if str(project.get("status") or "") != "manual_edit_required":
        return None
    manual = project.get("manual_review") if isinstance(project.get("manual_review"), dict) else {}
    manual_status = str(manual.get("status") or "")
    if manual_status not in {"queued", "in_progress"}:
        return None
    stale_basis = _parse_recovery_ts(str(
        manual.get("claimed_at") if manual_status == "in_progress" else manual.get("queued_at")
    or ""))
    if stale_basis is None:
        stale_basis = _parse_recovery_ts(str(manual.get("queued_at") or ""))
    if stale_basis is None or now - stale_basis < stale_after:
        return None
    project_id = str(project.get("project_id") or "").strip()
    if not project_id:
        return None
    reason_code = str(manual.get("reason_code") or manual.get("reason") or "manual_review").strip()
    detail = str(manual.get("detail") or "").strip()
    qa_blockers: list[str] = []
    for report in project.get("qa_reports") or []:
        if not isinstance(report, dict):
            continue
        for blocker in report.get("blockers") or []:
            blocker_text = str(blocker or "").strip()
            if blocker_text:
                qa_blockers.append(blocker_text)
    detail_parts = [
        f"project_id={project_id}",
        "manual_review_stale=true",
        f"manual_status={manual_status}",
        f"reason_code={reason_code}",
    ]
    claimed_by = str(manual.get("claimed_by") or "").strip()
    claimed_at = str(manual.get("claimed_at") or "").strip()
    if claimed_by:
        detail_parts.append(f"claimed_by={claimed_by}")
    if claimed_at:
        detail_parts.append(f"claimed_at={claimed_at}")
    if detail:
        detail_parts.append(f"detail={detail}")
    if qa_blockers:
        detail_parts.append("qa_blockers=" + " | ".join(qa_blockers[:5]))
    signal_detail = "; ".join(detail_parts)
    chat_id = str(project.get("chat_id") or "").strip()
    provider_message_id = str(project.get("original_message_id") or "").strip()
    return RecoverySignal(
        failure_class=_manual_reason_failure_class(reason_code, detail),
        severity="warning",
        project_id=project_id,
        chat_id=chat_id,
        detail=signal_detail,
        canonical_source=_canonical_detail(signal_detail),
        evidence_quality="strong" if chat_id and provider_message_id else "weak",
        provider_message_id=provider_message_id,
        observed_at=stale_basis,
    )


def classify_stale_manual_projects(projects: Iterable[dict], *, now: datetime, stale_after: timedelta) -> list[RecoverySignal]:
    signals: list[RecoverySignal] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        signal = classify_stale_manual_project(project, now=now, stale_after=stale_after)
        if signal is not None:
            signals.append(signal)
    return signals


def fingerprint_signal(signal: RecoverySignal) -> str:
    basis = "\0".join([
        signal.failure_class,
        signal.project_id or sha256_text(signal.chat_id),
        signal.canonical_source,
    ])
    return sha256_text(basis)


def ack_dedupe_key(signal: RecoverySignal) -> str:
    basis = "\0".join([
        sha256_text(signal.chat_id),
        signal.project_id or signal.provider_message_id or fingerprint_signal(signal),
        signal.failure_class,
        signal.canonical_source,
    ])
    return sha256_text(basis)


def incident_from_signal(signal: RecoverySignal, now: datetime) -> dict:
    observed_at = signal.observed_at or now
    source_fingerprint = fingerprint_signal(signal)
    suffix = source_fingerprint.split(":", 1)[1][:12].upper()
    return {
        "incident_id": f"FRI{observed_at:%Y%m%d}-{suffix}",
        "status": "open",
        "failure_class": signal.failure_class,
        "severity": signal.severity,
        "source_fingerprint": source_fingerprint,
        "ack_dedupe_key": ack_dedupe_key(signal),
        "project_id": signal.project_id,
        "chat_id": signal.chat_id,
        "chat_id_hash": sha256_text(signal.chat_id),
        "sender_phone_hash": "",
        "root_message_id": signal.provider_message_id,
        "provider_message_id_hash": sha256_text(signal.provider_message_id) if signal.provider_message_id else "",
        "evidence_quality": signal.evidence_quality,
        "first_seen": observed_at.isoformat(),
        "last_seen": observed_at.isoformat(),
        "ack": {"status": "none"},
        "codex": {"status": "none", "bundle_path": ""},
    }


def lint_recovery_copy(text: str, failure_class: str, followup_recorded: bool) -> CopyLintResult:
    lower = (text or "").lower()
    reasons: list[str] = []
    for term in FORBIDDEN_CUSTOMER_TERMS:
        if term in lower:
            reasons.append(f"internal_term:{term}")
    if re.search(r"\bF\d{4,}\b", text or ""):
        reasons.append("project_id")
    if re.search(r"\b\d+\s*(minutes?|hours?)\b", lower):
        reasons.append("sla_promise")
    if "follow up" in lower and not followup_recorded:
        reasons.append("followup_without_record")
    return CopyLintResult(ok=not reasons, reasons=reasons)


def render_recovery_ack(followup_recorded: bool = False) -> tuple[str, str]:
    if followup_recorded:
        return (
            "tracked_followup",
            "Flyer Studio\n------------\nI have your request. I am checking it now and will follow up here with the next step.",
        )
    return "generic_checking", "Flyer Studio\n------------\nI have your request. I am checking it now."


def ack_send_decision(
    incident: dict,
    *,
    flyer_enabled: bool,
    mode: str,
    now: datetime,
    ack_cooldown: timedelta,
) -> AckDecision:
    if mode != "customer_ack":
        return AckDecision(False, f"mode:{mode}")
    if not flyer_enabled:
        return AckDecision(False, "flyer_disabled")
    incident_status = str(incident.get("status") or "open").strip().lower()
    if incident_status != "open":
        return AckDecision(False, f"terminal_incident_status:{incident_status or 'unknown'}")
    if not str(incident.get("chat_id") or "").strip():
        return AckDecision(False, "missing_chat_id")
    ack = incident.get("ack") or {}
    status = str(ack.get("status") or "none")
    if status in {"sent", "failed", "uncertain", "suppressed"}:
        return AckDecision(False, f"terminal_ack:{status}")
    if status == "reserved":
        return AckDecision(False, "ack_reserved")
    last_seen_raw = str(incident.get("last_seen") or "").strip()
    if last_seen_raw:
        try:
            last_seen = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00"))
        except ValueError:
            return AckDecision(False, "invalid_last_seen")
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        if now - last_seen > ack_cooldown:
            return AckDecision(False, "stale_incident")
    if incident.get("evidence_quality") != "strong":
        return AckDecision(False, "missing_strong_customer_origin_evidence")
    template_id, message = render_recovery_ack(followup_recorded=False)
    lint = lint_recovery_copy(message, str(incident.get("failure_class") or ""), False)
    if not lint.ok:
        return AckDecision(False, "copy_lint:" + ",".join(lint.reasons))
    return AckDecision(True, "allowed")


def finalize_stale_reservations(state: dict, *, now: datetime, stale_after: timedelta) -> bool:
    changed = False
    for incident in state.get("incidents", []):
        ack = incident.get("ack") or {}
        if ack.get("status") != "reserved":
            continue
        try:
            reserved_at = datetime.fromisoformat(str(ack.get("reserved_at") or ""))
        except ValueError:
            reserved_at = now - stale_after - timedelta(seconds=1)
        if reserved_at.tzinfo is None:
            reserved_at = reserved_at.replace(tzinfo=timezone.utc)
        if now - reserved_at >= stale_after:
            ack["status"] = "uncertain"
            ack["status_detail"] = "stale reservation; possible crash around bridge send"
            incident["ack"] = ack
            changed = True
    return changed


def _parse_recovery_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _has_dry_run_outbound(row: dict) -> bool:
    value = row.get("outbound_message_ids")
    if value is None:
        value = row.get("outbound_message_id")
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value]
    else:
        values = []
    return any(item.strip().startswith("dry-run:") for item in values)


def _customer_visible_repair_events(rows: Iterable[dict]) -> list[dict]:
    events: list[dict] = []
    for row in rows:
        row_type = str(row.get("type") or "")
        repair_ts = _parse_recovery_ts(str(row.get("ts") or ""))
        if repair_ts is None:
            continue
        if row_type == "flyer_recovery_outcome_repaired":
            if str(row.get("status") or "") != "sent":
                continue
            chat_id_hash = str(row.get("chat_id_hash") or "").strip()
            if not chat_id_hash:
                continue
            repair_type = str(row.get("repair_type") or "outcome_repaired")
            if repair_type != "reference_scope_false_positive":
                continue
            events.append(
                {
                    "match_type": "chat_id_hash",
                    "match_value": chat_id_hash,
                    "ts": repair_ts,
                    "resolution": "outcome_repaired",
                    "detail": repair_type,
                    "project_id": "",
                    "failure_classes": ["bridge_send_failed"],
                }
            )
            continue
        if row_type == "flyer_assets_delivered":
            if _has_dry_run_outbound(row):
                continue
            project_id = str(row.get("project_id") or "").strip()
            if not project_id:
                continue
            events.append(
                {
                    "match_type": "project_id",
                    "match_value": project_id,
                    "ts": repair_ts,
                    "resolution": "customer_visible_success",
                    "detail": row_type,
                    "project_id": project_id,
                }
            )
            continue
        if row_type == "flyer_closure_customer_notified" and row.get("send_ok") is True:
            project_id = str(row.get("project_id") or "").strip()
            if not project_id:
                continue
            events.append(
                {
                    "match_type": "project_id",
                    "match_value": project_id,
                    "ts": repair_ts,
                    "resolution": "customer_visible_success",
                    "detail": row_type,
                    "project_id": project_id,
                }
            )
    return events


def resolve_incidents_from_customer_visible_repairs(
    state: dict,
    rows: Iterable[dict],
    now: datetime,
) -> list[dict]:
    """Mark older open incidents resolved after customer-visible success.

    Close only incidents whose first_seen and last_seen are at or before the
    success event, so a re-fired failure in the same chat or project remains visible.
    """
    repair_events = _customer_visible_repair_events(rows)
    resolved: list[dict] = []
    if not repair_events:
        return resolved

    def resolvable_status(incident: dict) -> bool:
        status = str(incident.get("status") or "open").lower()
        if status == "open":
            return True
        if status != "operator_action_required":
            return False
        operator_action = incident.get("operator_action") if isinstance(incident.get("operator_action"), dict) else {}
        reason = str(operator_action.get("reason") or "").strip()
        return reason in {
            "worker_completed_no_customer_visible_success",
            "worker_failed_no_customer_visible_success",
        }

    for incident in state.get("incidents", []):
        if not isinstance(incident, dict):
            continue
        if not resolvable_status(incident):
            continue
        first_seen = _parse_recovery_ts(str(incident.get("first_seen") or ""))
        last_seen = _parse_recovery_ts(str(incident.get("last_seen") or ""))
        if first_seen is None or last_seen is None:
            continue
        for event in repair_events:
            if event["match_type"] == "chat_id_hash":
                incident_value = str(incident.get("chat_id_hash") or "").strip()
            else:
                incident_value = str(incident.get("project_id") or "").strip()
            if not incident_value or incident_value != event["match_value"]:
                continue
            event_project_id = str(event.get("project_id", str(incident.get("project_id") or "")))
            if str(incident.get("project_id") or "").strip() != event_project_id:
                continue
            failure_classes = event.get("failure_classes") or []
            if failure_classes and str(incident.get("failure_class") or "") not in failure_classes:
                continue
            if first_seen > event["ts"] or last_seen > event["ts"]:
                continue
            incident["status"] = "resolved"
            incident["resolution"] = event["resolution"]
            incident["resolved_at"] = now.isoformat()
            incident["resolution_detail"] = event["detail"]
            resolved.append(incident)
            break
    return resolved


def resolve_incidents_from_outcome_repairs(
    state: dict,
    repair_rows: Iterable[dict],
    now: datetime,
) -> list[dict]:
    return resolve_incidents_from_customer_visible_repairs(state, repair_rows, now)


def escalate_unrepaired_incidents(
    state: dict,
    *,
    now: datetime,
    stale_after: timedelta,
) -> list[dict]:
    escalated: list[dict] = []
    for incident in state.get("incidents", []):
        if not isinstance(incident, dict):
            continue
        if str(incident.get("status") or "open").lower() != "open":
            continue
        codex = incident.get("codex") if isinstance(incident.get("codex"), dict) else {}
        codex_status = str(codex.get("status") or "").strip().lower()
        if codex_status not in {"completed", "failed"}:
            continue
        completed_at = _parse_recovery_ts(str(codex.get("completed_at") or codex.get("failed_at") or ""))
        last_seen = _parse_recovery_ts(str(incident.get("last_seen") or ""))
        age_anchor = completed_at or last_seen
        if age_anchor is None or now - age_anchor < stale_after:
            continue
        reason = (
            "worker_completed_no_customer_visible_success"
            if codex_status == "completed"
            else "worker_failed_no_customer_visible_success"
        )
        incident["status"] = "operator_action_required"
        incident["operator_action"] = {
            "reason": reason,
            "required_action": "verify_customer_outcome_or_repair_manually",
            "marked_at": now.isoformat(),
        }
        escalated.append(incident)
    return escalated


def _unique_incident_id(base_id: str, incidents: list[dict]) -> str:
    existing_ids = {str(item.get("incident_id") or "") for item in incidents if isinstance(item, dict)}
    if base_id not in existing_ids:
        return base_id
    for index in range(2, 1000):
        candidate = f"{base_id}-{index}"
        if candidate not in existing_ids:
            return candidate
    return f"{base_id}-{len(existing_ids) + 1}"


def merge_signals(state: dict, signals: Iterable[RecoverySignal], now: datetime) -> int:
    incidents = state.setdefault("incidents", [])
    existing = {
        item.get("source_fingerprint"): item
        for item in incidents
        if isinstance(item, dict) and str(item.get("status") or "open").lower() == "open"
    }
    resolved = {
        item.get("source_fingerprint"): item
        for item in incidents
        if isinstance(item, dict) and str(item.get("status") or "open").lower() != "open"
    }
    opened = 0
    for signal in signals:
        incident = incident_from_signal(signal, now)
        current = existing.get(incident["source_fingerprint"])
        if current is not None:
            current["last_seen"] = (signal.observed_at or now).isoformat()
            if current.get("evidence_quality") != "strong" and incident.get("evidence_quality") == "strong":
                current["evidence_quality"] = "strong"
                current["root_message_id"] = incident.get("root_message_id", "")
                current["provider_message_id_hash"] = incident.get("provider_message_id_hash", "")
                ack = current.get("ack") or {}
                if ack.get("status") == "suppressed" and ack.get("status_detail") == "missing_strong_customer_origin_evidence":
                    current["ack"] = {"status": "none"}
            continue
        previous = resolved.get(incident["source_fingerprint"])
        if previous is not None:
            previous_done_at = _parse_recovery_ts(
                str(previous.get("resolved_at") or previous.get("closed_at") or previous.get("last_seen") or "")
            )
            observed_at = signal.observed_at or now
            if previous_done_at is None or observed_at <= previous_done_at:
                continue
            incident["incident_id"] = _unique_incident_id(str(incident.get("incident_id") or ""), incidents)
        incidents.append(incident)
        existing[incident["source_fingerprint"]] = incident
        opened += 1
    state.setdefault("schema_version", 1)
    return opened


def load_recovery_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "incidents": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"recovery state corrupt: {path}: {e}") from e
    if not isinstance(doc, dict):
        return {"schema_version": 1, "incidents": []}
    doc.setdefault("schema_version", 1)
    doc.setdefault("incidents", [])
    return doc


def write_recovery_state(path: Path, state: dict) -> None:
    text = json.dumps(state, indent=2, sort_keys=True)
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    atomic_write_text(path, text)


@contextmanager
def recovery_state_lock(path: Path) -> Iterator[None]:
    try:
        from safe_io import FileLock  # type: ignore
    except Exception:
        yield
        return
    with FileLock(Path(str(path) + ".lock")):
        yield


def write_repair_bundle(
    incident: dict,
    bundle_dir: Path,
    *,
    audit_rows: list[dict] | None = None,
    project_excerpt: dict | None = None,
) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    incident_id = str(incident.get("incident_id") or "incident")
    safe_project = sanitize_for_repair_bundle(dict(project_excerpt or {}))
    safe_audit_rows = []
    for row in audit_rows or []:
        safe_row = dict(row)
        if "chat_id" in safe_row:
            safe_row["chat_id_hash"] = sha256_text(str(safe_row.pop("chat_id") or ""))
        if "sender_phone" in safe_row:
            safe_row["sender_phone_hash"] = sha256_text(str(safe_row.pop("sender_phone") or ""))
        safe_audit_rows.append(sanitize_for_repair_bundle(safe_row))
    doc = {
        "schema_version": 1,
        "incident_id": incident_id,
        "failure_class": incident.get("failure_class"),
        "severity": incident.get("severity"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sanitized_context": {
            "project_id": incident.get("project_id", ""),
            "chat_id_hash": incident.get("chat_id_hash", ""),
            "sender_phone_hash": incident.get("sender_phone_hash", ""),
            "root_message_id_hash": sha256_text(str(incident.get("root_message_id") or "")),
        },
        "audit_excerpt": safe_audit_rows,
        "project_excerpt": safe_project,
        "suspected_code_paths": [],
        "reproduction_hints": [],
        "safety_contract": [
            "Do not run live bridge sends.",
            "Use temp/copied state fixtures.",
            "Customer copy requires recovery copy lint.",
            "Production deploy requires PR review and deploy gate.",
        ],
    }
    path = bundle_dir / f"{incident_id}.json"
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    else:
        atomic_write_text(path, json.dumps(doc, indent=2, sort_keys=True))
    return path


def write_worker_queue_request(
    incident: dict,
    queue_dir: Path,
    *,
    bundle_path: Path,
    worker_mode: str,
    runner: str,
    repo_path: str,
    now: datetime,
) -> Path:
    queue_dir.mkdir(parents=True, exist_ok=True)
    incident_id = str(incident.get("incident_id") or "incident")
    doc = {
        "schema_version": 1,
        "incident_id": incident_id,
        "failure_class": incident.get("failure_class", ""),
        "severity": incident.get("severity", "warning"),
        "project_id": incident.get("project_id", ""),
        "created_at": now.isoformat(),
        "bundle_path": str(bundle_path),
        "runner": runner,
        "repo_path": repo_path,
        "worker_mode": worker_mode,
        "no_live_send_env": {"FLYER_RECOVERY_NO_LIVE_SEND": "1"},
        "required_gates": [
            "local_tests_pass",
            "human_pr_review_before_merge",
            "tarball_deploy_smoke_before_production",
        ],
        "sanitized_context": {
            "chat_id_hash": incident.get("chat_id_hash", ""),
            "sender_phone_hash": incident.get("sender_phone_hash", ""),
            "source_fingerprint": incident.get("source_fingerprint", ""),
        },
    }
    path = queue_dir / f"{incident_id}.json"
    text = json.dumps(doc, indent=2, sort_keys=True)
    try:
        from safe_io import atomic_write_text  # type: ignore
    except Exception:
        path.write_text(text, encoding="utf-8")
    else:
        atomic_write_text(path, text)
    return path
