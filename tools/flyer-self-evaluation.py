#!/usr/bin/env python3
"""Read-only Flyer Studio self-evaluation report.

This tool turns existing Flyer project state and decisions.log evidence into
operator-facing incidents and eval-candidate suggestions. It does not mutate
customer/project/VPS state and it does not send messages.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROJECTS_PATH = Path("/opt/shift-agent/state/flyer/projects.json")
DEFAULT_DECISIONS_LOG = Path("/opt/shift-agent/logs/decisions.log")
CF_ROUTER_ACTIONS_PATH = Path(__file__).resolve().parents[1] / "src" / "plugins" / "cf-router" / "actions.py"
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.flyer.customer_copy_policy import (  # noqa: E402
    BANNED_CUSTOMER_COPY_TERMS,
    OUTBOUND_TEXT_FIELDS,
    STATIC_CUSTOMER_COPY_FUNCTIONS,
    classify_initial_ack,
    extract_customer_copy_literals as policy_extract_customer_copy_literals,
    extract_function_block as policy_extract_function_block,
    extract_send_call_literals,
    scan_outbound_entry,
)
from agents.flyer.rollout_readiness import (  # noqa: E402
    SEVERITY_RANK,
    RolloutInputFixture,
    build_rollout_section,
    incident_color,
    load_rollout_input,
    merge_replay_summary_override,
    render_rollout_banner,
    render_rollout_section,
)
from agents.flyer.operating_layer import (  # noqa: E402
    build_operating_layer_section,
    render_operating_layer_markdown,
)

DEFAULT_SOURCE_FILES = (
    Path("src/plugins/cf-router/actions.py"),
    Path("src/plugins/cf-router/hooks.py"),
    Path("src/agents/flyer/workflow.py"),
)
STATIC_COPY_SCAN_FUNCTIONS = STATIC_CUSTOMER_COPY_FUNCTIONS
INTERNAL_COPY_TERMS = BANNED_CUSTOMER_COPY_TERMS
SOURCE_QA_MARKERS = ("source", "contract", "integrity", "operator_review")
SOURCE_POSITIVE_FACT_PREFIXES = ("source_heading:", "source_section:", "source_required_text:")
REPLACEMENT_NEW_FACT_RE = re.compile(r"^replacement:\d+:new$")

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
FRESH_FLYER_OBJECT_RE = re.compile(r"\b(flyer|flier|poster|banner)\b", re.I)
FRESH_FLYER_START_RE = re.compile(r"\b(help\s+me\s+with|help\s+me\s+(make|create|design)|create|make|design|build|generate|need)\b", re.I)
FRESH_FLYER_DETAIL_RE = re.compile(r"\b(event|sale|special|offer|promo|menu|items?|snacks?|breakfast|lunch|dinner|south\s+indian)\b", re.I)
FRESH_FLYER_SCHEDULE_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}\s*(am|pm)|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.I,
)
FRESH_FLYER_REVISION_RE = re.compile(
    r"\b(change|replace|update|edit|revise|modify|swap|remove|delete|fix|correct|approve)\b"
    r"|\b(current|existing|old|previous|same|this|that|attached|uploaded)\s+"
    r"(flyer|flier|poster|banner|design|image|artwork)\b",
    re.I,
)
MALFORMED_BUSINESS_NAME_RE = re.compile(
    r"\b(i(?:'|’)?d like|help me with|create (?:a )?flyer|make (?:a )?flyer|flier from|flyer from|include)\b",
    re.I,
)
PHONE_DIGITS_RE = re.compile(r"\D+")
PHONE_RUN_RE = re.compile(r"[\d\s\-().+/]{8,}")
SECRET_ASSIGNMENT_RE = re.compile(r"\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET)\s*=\s*['\"]?[^'\"\s,;]+", re.I)
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I)
ACCESS_TOKEN_RE = re.compile(r"\b(?:access_token|refresh_token)\s*[:=]\s*['\"]?[^'\"\s,;]+", re.I)
SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
E164_RE = re.compile(r"\+\d{10,15}\b")
US_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}|\d{10})(?!\w)")
CHAT_ID_RE = re.compile(r"\b[\w.+-]+@(?:s\.whatsapp\.net|lid)\b")
UNIX_ABS_PATH_RE = re.compile(r"(?<!\w)/(?:opt|var|tmp|home|root|Users)/[^\s,'\")]+")
WINDOWS_ABS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s,'\")]+")
_CF_ROUTER_ACTIONS: Any | None = None
_CF_ROUTER_ACTIONS_LOAD_ATTEMPTED = False


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


def source_contracts(project: dict[str, Any]) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for extraction in project.get("reference_extractions") or []:
        if not isinstance(extraction, dict):
            continue
        contract = extraction.get("source_contract")
        if isinstance(contract, dict):
            contracts.append(contract)
    return contracts


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


def normalize_text_for_match(text: str) -> str:
    lowered = re.sub(r"\s+", " ", str(text or "")).casefold()
    for ch in ("‘", "’", "ʼ", "`", "'"):
        lowered = lowered.replace(ch, "")
    return lowered


def looks_like_phone(value: str) -> bool:
    digits = PHONE_DIGITS_RE.sub("", value)
    return 10 <= len(digits) <= 15


def phone_value_present_in(text: str, fact_value: str) -> bool:
    value_digits = PHONE_DIGITS_RE.sub("", fact_value)
    for run in PHONE_RUN_RE.findall(text):
        run_digits = PHONE_DIGITS_RE.sub("", run)
        if value_digits in run_digits:
            return True
    return False


def text_value_present_in(normalized_text: str, normalized_value: str) -> bool:
    if not normalized_value:
        return False
    left = r"\b" if normalized_value[:1].isalnum() else ""
    right = r"\b" if normalized_value[-1:].isalnum() else ""
    return re.search(left + re.escape(normalized_value) + right, normalized_text) is not None


def value_present_in(extracted_text: str, fact_value: str) -> bool:
    if looks_like_phone(fact_value):
        return phone_value_present_in(extracted_text, fact_value)
    return text_value_present_in(normalize_text_for_match(extracted_text), normalize_text_for_match(fact_value))


def is_positive_source_fact(fact: dict[str, Any]) -> bool:
    fact_id = str(fact.get("fact_id") or "")
    if not fact.get("required"):
        return False
    return fact_id.startswith(SOURCE_POSITIVE_FACT_PREFIXES) or REPLACEMENT_NEW_FACT_RE.match(fact_id) is not None


def positive_source_facts(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [fact for fact in project.get("locked_facts") or [] if isinstance(fact, dict) and is_positive_source_fact(fact)]


def contract_expected_positive_facts(contract: dict[str, Any]) -> dict[str, str]:
    expected: dict[str, str] = {}
    require = bool(contract.get("preserve_layout") or contract.get("preserve_unmentioned_text"))
    if require:
        for idx, heading in enumerate(contract.get("required_headings") or []):
            if str(heading).strip():
                expected[f"source_heading:{idx}"] = str(heading)
        for idx, text in enumerate(contract.get("required_text") or []):
            if str(text).strip():
                expected[f"source_required_text:{idx}"] = str(text)
        for section_idx, section in enumerate(contract.get("sections") or []):
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "").strip()
            if heading:
                expected[f"source_section:{section_idx}:heading"] = heading
            for item_idx, item in enumerate(section.get("items") or []):
                if str(item).strip():
                    expected[f"source_section:{section_idx}:item:{item_idx}"] = str(item)
    for repl_idx, (_old, new) in enumerate((contract.get("requested_replacements") or {}).items()):
        if str(new).strip():
            expected[f"replacement:{repl_idx}:new"] = str(new)
    return expected


def expected_source_facts(project: dict[str, Any]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for contract in source_contracts(project):
        expected.update(contract_expected_positive_facts(contract))
    return expected


def forbidden_substrings(project: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for contract in source_contracts(project):
        for item in contract.get("forbidden_substrings") or []:
            text = str(item).strip()
            if text and text not in values:
                values.append(text)
    return values


def qa_missing_required_text(project: dict[str, Any], report: dict[str, Any]) -> list[str]:
    extracted = str(report.get("extracted_text") or "")
    missing: list[str] = []
    for fact in positive_source_facts(project):
        value = str(fact.get("value") or "")
        fact_id = str(fact.get("fact_id") or "")
        if value and not value_present_in(extracted, value):
            missing.append(fact_id)
    return missing


def forbidden_text_hits(project: dict[str, Any], report: dict[str, Any]) -> list[str]:
    extracted = str(report.get("extracted_text") or "")
    hits: list[str] = []
    for forbidden in forbidden_substrings(project):
        if value_present_in(extracted, forbidden):
            hits.append(forbidden)
    return hits


def accepted_source_qa_report(project: dict[str, Any]) -> dict[str, Any] | None:
    for report in project.get("qa_reports") or []:
        if not isinstance(report, dict) or report.get("status") != "passed":
            continue
        if not qa_report_matches_current_project(project, report):
            continue
        if report.get("qa_source") == "operator_review":
            return report
        extracted = str(report.get("extracted_text") or "")
        if not extracted.strip():
            continue
        facts = positive_source_facts(project)
        if not facts:
            continue
        if qa_missing_required_text(project, report):
            continue
        if forbidden_text_hits(project, report):
            continue
        return report
    return None


def report_checked_after_project_update(project: dict[str, Any], report: dict[str, Any]) -> bool:
    project_updated_at = project.get("updated_at")
    checked_at = report.get("checked_at")
    if not checked_at:
        return False
    if not project_updated_at:
        return True
    try:
        return parse_utc(str(checked_at)) >= parse_utc(str(project_updated_at))
    except (TypeError, ValueError):
        return False


def qa_report_matches_current_project(project: dict[str, Any], report: dict[str, Any]) -> bool:
    project_id = str(project.get("project_id") or "")
    report_project_id = str(report.get("project_id") or "")
    if project_id and report_project_id != project_id:
        return False
    project_version = project.get("version")
    report_version = report.get("project_version")
    if project_version is not None:
        if report_version is None:
            return False
        try:
            if int(project_version) != int(report_version):
                return False
        except (TypeError, ValueError):
            return False
    if not report_checked_after_project_update(project, report):
        return False
    generated_assets = [
        asset for asset in project.get("assets") or []
        if isinstance(asset, dict)
        and (
            str(asset.get("kind") or "").startswith("final_")
            or asset.get("kind") == "concept_preview"
            or str(asset.get("asset_id") or "") in {str(item) for item in project.get("final_asset_ids") or []}
        )
    ]
    if not generated_assets:
        return True
    report_asset_id = str(report.get("asset_id") or "")
    if not report_asset_id:
        return False
    if report_asset_id:
        matches = [asset for asset in generated_assets if str(asset.get("asset_id") or "") == report_asset_id]
        if not matches:
            return False
        report_sha = str(report.get("artifact_sha256") or "")
        current_shas = {str(asset.get("sha256") or "") for asset in matches if asset.get("sha256")}
        if current_shas:
            return bool(report_sha) and report_sha in current_shas
    return True


def has_source_qa(project: dict[str, Any]) -> bool:
    return accepted_source_qa_report(project) is not None


def source_contract_evidence_details(
    project: dict[str, Any],
    *,
    queued_age_minutes: float | None = None,
    customer_impact: str = "source preservation risk before customer preview/delivery",
) -> dict[str, Any]:
    contracts = source_contracts(project)
    expected = expected_source_facts(project)
    locked_ids = {
        str(fact.get("fact_id") or "")
        for fact in project.get("locked_facts") or []
        if isinstance(fact, dict)
    }
    accepted = accepted_source_qa_report(project)
    qa_reports = [r for r in project.get("qa_reports") or [] if isinstance(r, dict)]
    missing_from_qa: list[str] = []
    forbidden_hits: list[str] = []
    accepted = accepted_source_qa_report(project)
    reports_for_gap = [accepted] if accepted else [
        report for report in qa_reports
        if report.get("status") == "passed" and qa_report_matches_current_project(project, report)
    ]
    for report in reports_for_gap:
        if not report:
            continue
        missing_from_qa.extend(item for item in qa_missing_required_text(project, report) if item not in missing_from_qa)
        forbidden_hits.extend(item for item in forbidden_text_hits(project, report) if item not in forbidden_hits)
    fields_present: list[str] = []
    for field in ("required_headings", "required_text", "sections", "requested_replacements", "forbidden_substrings"):
        if any(contract.get(field) for contract in contracts):
            fields_present.append(field)
    return {
        "project_status": str(project.get("status") or ""),
        "active_customer_risk": active_customer_risk(project),
        "has_reference_media": has_reference_media(project),
        "exact_source_edit_cues": looks_like_exact_source_edit(project),
        "has_source_contract": bool(contracts),
        "source_contract_fields_present": fields_present,
        "locked_fact_missing": [fact_id for fact_id in expected if fact_id not in locked_ids],
        "qa_report_count": len(qa_reports),
        "accepted_qa_source": str(accepted.get("qa_source") or "") if accepted else "",
        "qa_missing_required_text": missing_from_qa,
        "forbidden_text_hits": forbidden_hits,
        "queued_age_minutes": round(queued_age_minutes, 1) if queued_age_minutes is not None else None,
        "customer_impact": customer_impact,
    }


def active_customer_risk(project: dict[str, Any]) -> bool:
    status = str(project.get("status") or "")
    return status not in {"delivered", "completed", "closed_no_send", "cancelled", "archived"}


def looks_like_exact_source_edit(project: dict[str, Any]) -> bool:
    text = str(project.get("raw_request") or "").lower()
    return has_reference_media(project) and any(cue in text for cue in SOURCE_EDIT_CUES)


def cf_router_actions() -> Any | None:
    global _CF_ROUTER_ACTIONS, _CF_ROUTER_ACTIONS_LOAD_ATTEMPTED
    if _CF_ROUTER_ACTIONS_LOAD_ATTEMPTED:
        return _CF_ROUTER_ACTIONS
    _CF_ROUTER_ACTIONS_LOAD_ATTEMPTED = True
    if not CF_ROUTER_ACTIONS_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("flyer_self_eval_cf_router_actions", CF_ROUTER_ACTIONS_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    _CF_ROUTER_ACTIONS = module
    return module


def fallback_fresh_flyer_request(text: str) -> bool:
    body = " ".join(str(text or "").split())
    if not body or STATUS_CHECK_RE.search(body) or FRESH_FLYER_REVISION_RE.search(body):
        return False
    return bool(
        FRESH_FLYER_OBJECT_RE.search(body)
        and FRESH_FLYER_START_RE.search(body)
        and FRESH_FLYER_DETAIL_RE.search(body)
        and FRESH_FLYER_SCHEDULE_RE.search(body)
    )


def looks_like_fresh_flyer_request(text: str) -> bool:
    actions = cf_router_actions()
    helper = getattr(actions, "should_start_new_flyer_over_active", None) if actions else None
    if callable(helper):
        return bool(helper(text, has_media=False))
    return fallback_fresh_flyer_request(text)


def latest_revision_request(project: dict[str, Any]) -> tuple[str, str]:
    revisions = [item for item in project.get("revisions") or [] if isinstance(item, dict)]
    if not revisions:
        return "", ""
    latest = revisions[-1]
    for key in ("request_text", "revision_text", "text", "body", "message"):
        value = str(latest.get(key) or "").strip()
        if value:
            return value, str(latest.get("message_id") or latest.get("customer_message_id") or "")
    return "", str(latest.get("message_id") or latest.get("customer_message_id") or "")


def latest_concept_text(project: dict[str, Any]) -> str:
    chunks = [str(project.get("raw_request") or "")]
    for concept in project.get("concepts") or []:
        if not isinstance(concept, dict):
            continue
        for key in ("prompt", "title", "style_summary", "rationale"):
            value = concept.get(key)
            if value:
                chunks.append(str(value))
    return " ".join(chunks)


def salient_request_terms(text: str) -> list[str]:
    normalized = normalize_text_for_match(text)
    terms: list[str] = []
    for phrase in ("evening snacks", "south indian", "snack items", "wednesday", "saturday"):
        if phrase in normalized:
            terms.append(phrase)
    for match in re.finditer(r"\b\d{1,2}\s*(?:am|pm)\b", normalized):
        term = " ".join(match.group(0).split())
        if term not in terms:
            terms.append(term)
    return terms


def project_reflection_incidents(project: dict[str, Any]) -> list[dict[str, Any]]:
    text, message_id = latest_revision_request(project)
    if not looks_like_fresh_flyer_request(text):
        return []
    project_id = str(project.get("project_id") or "")
    haystack = normalize_text_for_match(latest_concept_text(project))
    terms = salient_request_terms(text)
    missing = [term for term in terms if term and term not in haystack]
    details = {
        "project_status": str(project.get("status") or ""),
        "active_customer_risk": active_customer_risk(project),
        "latest_message_id": message_id,
        "salient_terms": terms,
        "missing_terms": missing,
        "customer_impact": "fresh flyer request appears attached to old project context",
    }
    incidents = [
        incident(
            "new_flyer_routed_as_revision",
            severity="high",
            project_id=project_id,
            evidence="fresh flyer request appears in revision history",
            suggested_action="Bypass active-project revision routing and create a new Flyer project from the latest request.",
            category="routing_tripwire",
            evidence_details=details,
        )
    ]
    if len(missing) >= max(2, min(3, len(terms))):
        incidents.append(
            incident(
                "latest_request_not_reflected",
                severity="high",
                project_id=project_id,
                evidence="latest request terms missing from raw_request/concept prompt",
                suggested_action="Regenerate from the latest customer request before sending or approving another preview.",
                category="routing_tripwire",
                evidence_details=details,
            )
        )
    return incidents


def incident(
    kind: str,
    *,
    severity: str,
    project_id: str = "",
    evidence: str = "",
    suggested_action: str,
    category: str,
    count: int | None = None,
    evidence_details: dict[str, Any] | None = None,
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
    if evidence_details is not None:
        item["evidence_details"] = evidence_details
    return item


def projects_by_id(projects: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(project.get("project_id") or "").upper(): project
        for project in projects
        if isinstance(project, dict) and project.get("project_id")
    }


def entry_active_customer_risk(
    entry: dict[str, Any],
    project_index: dict[str, dict[str, Any]] | None,
) -> bool:
    project_id = str(entry.get("project_id") or decision_project_id(entry) or "").upper()
    project = (project_index or {}).get(project_id)
    if project:
        return active_customer_risk(project)
    historical = str(entry.get("historical") or entry.get("audit_only") or "").lower()
    if historical in {"1", "true", "yes"}:
        return False
    return True


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
        out.extend(project_reflection_incidents(project))
        business_fact = next(
            (
                fact for fact in project.get("locked_facts") or []
                if isinstance(fact, dict) and fact.get("fact_id") == "business_name"
            ),
            {},
        )
        business_value = str(business_fact.get("value") or "")
        if business_value and MALFORMED_BUSINESS_NAME_RE.search(business_value):
            out.append(
                incident(
                    "malformed_business_name_fact",
                    severity="high",
                    project_id=project_id,
                    evidence=f"business_name={business_value[:120]}",
                    suggested_action="Repair Flyer fact extraction so profile identity wins over natural-language request text.",
                    category="flyer_fact_contract",
                    evidence_details={
                        "fact_id": "business_name",
                        "source": str(business_fact.get("source") or ""),
                        "active_customer_risk": active_customer_risk(project),
                    },
                )
            )
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
                    evidence_details=source_contract_evidence_details(
                        project,
                        queued_age_minutes=queued_age,
                        customer_impact="customer is waiting on a source-preserving edit",
                    ),
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
                    evidence_details=source_contract_evidence_details(project),
                )
            )

        if has_source_contract(project):
            details = source_contract_evidence_details(project)
            if details["locked_fact_missing"]:
                out.append(
                    incident(
                        "source_contract_locked_fact_gap",
                        severity="high",
                        project_id=project_id,
                        evidence="source_contract exists but source-derived required locked facts are missing",
                        suggested_action="Repair legacy/malformed source-contract rows so source obligations reach locked facts.",
                        category="source_contract_visual_qa",
                        evidence_details=details,
                    )
                )
            if details["forbidden_text_hits"]:
                out.append(
                    incident(
                        "source_contract_forbidden_text_present",
                        severity="high",
                        project_id=project_id,
                        evidence="forbidden source text appears in passed QA extracted text",
                        suggested_action="Block delivery and regenerate/manual-fix the source-preserving edit.",
                        category="source_contract_visual_qa",
                        evidence_details=details,
                    )
                )
            if has_generated_or_final_asset(project) and details["qa_report_count"] and details["qa_missing_required_text"]:
                out.append(
                    incident(
                        "source_contract_qa_fact_gap",
                        severity="high",
                        project_id=project_id,
                        evidence="passed QA report does not evidence required source-contract facts",
                        suggested_action="Harden source-aware QA so required source facts are verified before customer preview/delivery.",
                        category="source_contract_visual_qa",
                        evidence_details=details,
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
                    evidence_details=source_contract_evidence_details(project),
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
                    evidence_details={
                        "project_status": str(project.get("status") or ""),
                        "active_customer_risk": True,
                        "updated_age_minutes": round(gen_age, 1),
                        "customer_impact": "customer may be waiting on generation/finalization",
                    },
                )
            )
    return out


def customer_copy_incidents(
    entries: list[dict[str, Any]],
    *,
    project_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries:
        scan = scan_outbound_entry(entry)
        if not scan.text:
            continue
        matched = list(scan.matched_values)
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
                evidence_details={
                    "active_customer_risk": entry_active_customer_risk(entry, project_index),
                    "matched_categories": [hit.category for hit in scan.hits],
                    "customer_impact": "customer-facing copy exposed internal workflow language",
                },
            )
        )
    return out


def duplicate_initial_ack_incidents(
    entries: list[dict[str, Any]],
    *,
    project_index: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"processing": 0, "intake": 0, "project_id": "", "entries": []})
    for entry in entries:
        text = ""
        for field in OUTBOUND_TEXT_FIELDS:
            if entry.get(field):
                text = str(entry.get(field) or "")
                break
        if not text:
            continue
        inbound_message_id = str(
            entry.get("inbound_message_id")
            or entry.get("source_message_id")
            or entry.get("trigger_message_id")
            or ""
        )
        key = "|".join(
            part for part in [
                inbound_message_id,
                str(entry.get("project_id") or ""),
                str(entry.get("chat_id") or entry.get("customer_phone") or ""),
            ]
            if part
        )
        if not key:
            continue
        group = groups[key]
        if re.fullmatch(r"F\d{4,}", key.upper()):
            group["project_id"] = key.upper()
        elif entry.get("project_id"):
            group["project_id"] = str(entry.get("project_id") or "").upper()
        markers = classify_initial_ack(text)
        group["entries"].append(entry)
        if "processing" in markers:
            group["processing"] += 1
        if "intake" in markers:
            group["intake"] += 1
    out: list[dict[str, Any]] = []
    for key, group in groups.items():
        if group["processing"] and group["intake"]:
            out.append(
                incident(
                    "duplicate_initial_ack",
                    severity="high",
                    project_id=str(group.get("project_id") or ""),
                    evidence=f"key={key}; processing_ack={group['processing']}; intake_ack={group['intake']}",
                    suggested_action="Keep one initial Flyer customer ack before preview/fallback; leave project details in audit/Cockpit.",
                    category="customer-message copy",
                    count=int(group["processing"]) + int(group["intake"]),
                    evidence_details={
                        "active_customer_risk": any(
                            entry_active_customer_risk(entry, project_index)
                            for entry in group.get("entries", [])
                        ),
                        "customer_impact": "customer received more than one initial lifecycle acknowledgement for the same inbound",
                    },
                )
            )
    return out


def static_customer_copy_incidents(source_files: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        scanned = "\n".join(
            [
                "\n".join(
                    extract_customer_copy_literals(extract_function_block(text, name))
                    for name in STATIC_COPY_SCAN_FUNCTIONS
                ),
                extract_send_call_literals(text),
            ]
        )
        if not scanned:
            continue
        scan = scan_outbound_entry({"outbound_text": scanned})
        matched = list(scan.matched_values)
        if not matched:
            continue
        out.append(
            incident(
                "customer_copy_static_internal_leak",
                severity="medium",
                evidence=f"{path}: " + ", ".join(matched[:5]),
                suggested_action=(
                    "Review source-code customer ack scan; decisions.log may not contain outbound bodies for this path."
                ),
                category="customer-message copy",
                evidence_details={
                    "active_customer_risk": False,
                    "matched_categories": [hit.category for hit in scan.hits],
                    "customer_impact": "static source contains customer-copy policy violations",
                },
            )
        )
    return out


def extract_function_block(source: str, function_name: str) -> str:
    return policy_extract_function_block(source, function_name)


def extract_customer_copy_literals(function_block: str) -> str:
    return policy_extract_customer_copy_literals(function_block)


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
                evidence_details={
                    "active_customer_risk": True,
                    "customer_impact": "customer has repeatedly asked for status",
                },
            )
        )
    return out


PROJECT_ID_IN_TEXT_RE = re.compile(r"\b(F\d{4,})\b", re.I)


def decision_project_id(entry: dict[str, Any]) -> str:
    direct = str(entry.get("project_id") or "").upper()
    if direct:
        return direct
    for field in ("detail", "evidence", "message", "body"):
        match = PROJECT_ID_IN_TEXT_RE.search(str(entry.get(field) or ""))
        if match:
            return match.group(1).upper()
    return ""


def preview_final_qa_mismatch_incidents(projects: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    project_by_id = {
        str(project.get("project_id") or "").upper(): project
        for project in projects
        if isinstance(project, dict) and project.get("project_id")
    }
    preview_sent: set[str] = set()
    approved_final_failed: dict[str, str] = {}
    for entry in entries:
        project_id = decision_project_id(entry)
        if not project_id:
            continue
        reason = str(entry.get("reason") or "")
        detail = str(entry.get("detail") or "")
        text = f"{reason} {detail}".lower()
        if "ack_message_id" in text and ("flyer_primary_project_created" in text or "preview" in text):
            preview_sent.add(project_id)
        if "approve=true" in text and (
            "visual_qa_failed" in text
            or "text_qa_failed" in text
            or "finalize-flyer-assets" in text
            or "finalization" in text
        ):
            approved_final_failed[project_id] = detail[:300]

    out: list[dict[str, Any]] = []
    for project_id in sorted(preview_sent & set(approved_final_failed)):
        project = project_by_id.get(project_id) or {}
        manual = project.get("manual_review") if isinstance(project.get("manual_review"), dict) else {}
        status = str(project.get("status") or "")
        reason_code = str(manual.get("reason_code") or "")
        if status != "manual_edit_required" and reason_code not in {"visual_qa_failed", "missing_required_facts"}:
            continue
        out.append(
            incident(
                "preview_approved_final_qa_failed",
                severity="high",
                project_id=project_id,
                evidence="preview sent, approval observed, final QA/finalization failed",
                suggested_action="Review the failed finalization before the customer sees a stale approval loop.",
                category="preview_final_qa",
                evidence_details={
                    "project_status": status,
                    "manual_reason_code": reason_code,
                    "active_customer_risk": active_customer_risk(project) if project else True,
                    "failure_detail": approved_final_failed[project_id],
                    "customer_impact": "customer approved a preview but final files did not pass QA",
                },
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
        "routing_tripwire": "tests/test_cf_router_flyer_routing.py",
        "preview_final_qa": "tests/test_flyer_self_evaluation.py",
    }.get(category, "tasks/todo.md")


def redact_text(text: str) -> str:
    value = str(text)
    value = SECRET_ASSIGNMENT_RE.sub("[redacted-secret]", value)
    value = BEARER_RE.sub("Bearer [redacted-secret]", value)
    value = ACCESS_TOKEN_RE.sub("[redacted-token]", value)
    value = SK_KEY_RE.sub("[redacted-secret]", value)
    value = CHAT_ID_RE.sub("[redacted-chat-id]", value)
    value = E164_RE.sub("[redacted-phone]", value)
    value = US_PHONE_RE.sub("[redacted-phone]", value)

    def repl_unix(match: re.Match[str]) -> str:
        return "[redacted-path]/" + Path(match.group(0)).name

    def repl_windows(match: re.Match[str]) -> str:
        return "[redacted-path]\\" + Path(match.group(0)).name

    value = UNIX_ABS_PATH_RE.sub(repl_unix, value)
    value = WINDOWS_ABS_PATH_RE.sub(repl_windows, value)
    return value


def sanitize_value(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: sanitize_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item, key=key) for item in value]
    if isinstance(value, str):
        if re.search(r"(?:key|token|secret)$|(?:api_key|access_token|refresh_token)", key, flags=re.I):
            return "[redacted-secret]"
        if key in {"type", "severity", "project_id", "eval_category", "category", "reason", "suggested_fixture"}:
            return value
        return redact_text(value)
    return value


def sanitize_report(report: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_value(report)
    return sanitized if isinstance(sanitized, dict) else report


def build_report(
    *,
    projects: dict[str, Any],
    decision_entries: list[dict[str, Any]],
    now: datetime | None = None,
    manual_stale_minutes: int = 30,
    generation_stale_minutes: int = 15,
    repeated_checkin_threshold: int = 3,
    source_files: list[Path] | None = None,
    rollout_mode: bool = False,
    rollout_fixture: RolloutInputFixture | None = None,
    operating_layer_input: dict[str, Any] | None = None,
    manual_stale_red_minutes: int = 30,
) -> dict[str, Any]:
    now = now or utc_now()
    project_rows = projects.get("projects") if isinstance(projects.get("projects"), list) else []
    project_index = projects_by_id(project_rows)
    incidents = (
        project_incidents(
            project_rows,
            now=now,
            manual_stale_minutes=manual_stale_minutes,
            generation_stale_minutes=generation_stale_minutes,
        )
        + customer_copy_incidents(decision_entries, project_index=project_index)
        + duplicate_initial_ack_incidents(decision_entries, project_index=project_index)
        + static_customer_copy_incidents(source_files or [])
        + repeated_checkin_incidents(decision_entries, threshold=repeated_checkin_threshold)
        + preview_final_qa_mismatch_incidents(project_rows, decision_entries)
    )
    # Color threshold is single-sourced via incident_color (from rollout_readiness).
    status = incident_color(incidents)
    needs_srini = [
        f"{item['type']} {item.get('project_id') or ''}".strip()
        for item in incidents
        if item.get("human_decision_required")
    ]
    report = {
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
            "customer-copy log scan only sees decisions.log rows with outbound text fields; use --scan-source-copy for metadata-only cf-router send paths",
        ],
    }
    if rollout_mode:
        report["rollout"] = build_rollout_section(
            incidents=incidents,
            fixture=rollout_fixture,
            manual_stale_red_minutes=manual_stale_red_minutes,
        )
    sanitized = sanitize_report(report)
    if operating_layer_input is not None:
        rollout_section = sanitized.get("rollout") if isinstance(sanitized.get("rollout"), dict) else None
        sanitized["operating_layer"] = build_operating_layer_section(
            operating_layer_input,
            rollout=rollout_section,
        )
    return sanitized


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = ["# Flyer Self-Evaluation", ""]
    rollout = report.get("rollout") if isinstance(report.get("rollout"), dict) else None
    if rollout is not None:
        lines.append(render_rollout_banner(rollout))
        lines.append("")
    lines.extend(
        [
            f"- Status: {report.get('status', 'unknown')}",
            f"- Generated: {report.get('generated_at', 'unknown')}",
            f"- Incidents: {report.get('summary', {}).get('incident_count', 0)}",
            "",
            "## Incidents",
            "",
        ]
    )
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
    lines.extend(["", "## Boundaries", ""])
    for item in report.get("boundaries") or []:
        lines.append(f"- {item}")
    if rollout is not None:
        lines.extend(render_rollout_section(rollout))
    operating_layer = report.get("operating_layer") if isinstance(report.get("operating_layer"), dict) else None
    if operating_layer is not None:
        lines.extend(render_operating_layer_markdown(operating_layer))
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
    parser.add_argument(
        "--scan-source-copy",
        action="store_true",
        help="Also scan known source files for customer-copy internal terms when audit rows do not include outbound bodies.",
    )
    parser.add_argument(
        "--rollout-readiness",
        action="store_true",
        help="Emit the rollout-readiness block layered on top of incident detection.",
    )
    parser.add_argument(
        "--rollout-input",
        type=Path,
        default=None,
        help="Path to a RolloutInputFixture JSON file (host-supplied posture).",
    )
    parser.add_argument(
        "--rollout-replay-summary-json",
        type=Path,
        default=None,
        help="Replace fixture.replay_summary with the JSON at this path (ad-hoc operator runs).",
    )
    parser.add_argument(
        "--manual-stale-red-minutes",
        type=int,
        default=30,
        help="RED threshold for manual_source_edit_stale rollout reason (default 30 min, matches detector).",
    )
    parser.add_argument(
        "--operating-layer-input",
        type=Path,
        default=None,
        help="Path to a Flyer operating-layer readiness JSON fixture.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    rollout_fixture = None
    if args.rollout_readiness:
        rollout_fixture = load_rollout_input(str(args.rollout_input) if args.rollout_input else None)
        if args.rollout_replay_summary_json:
            rollout_fixture = merge_replay_summary_override(
                rollout_fixture, str(args.rollout_replay_summary_json)
            )
    report = build_report(
        projects=load_json_file(args.projects),
        decision_entries=load_decisions_log(args.decisions_log),
        now=parse_utc(args.now) if args.now else utc_now(),
        manual_stale_minutes=args.manual_stale_minutes,
        generation_stale_minutes=args.generation_stale_minutes,
        repeated_checkin_threshold=args.repeated_checkin_threshold,
        source_files=[Path.cwd() / path for path in DEFAULT_SOURCE_FILES] if args.scan_source_copy else [],
        rollout_mode=args.rollout_readiness,
        rollout_fixture=rollout_fixture,
        operating_layer_input=load_json_file(args.operating_layer_input) if args.operating_layer_input else None,
        manual_stale_red_minutes=args.manual_stale_red_minutes,
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
