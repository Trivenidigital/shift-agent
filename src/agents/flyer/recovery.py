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


@dataclass(frozen=True)
class CopyLintResult:
    ok: bool
    reasons: list[str]


@dataclass(frozen=True)
class AckDecision:
    allowed: bool
    reason: str


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


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _parse_project_id(detail: str) -> str:
    match = re.search(r"\bproject_id=(F\d{4,})\b|\b(F\d{4,})\b", detail or "")
    if not match:
        return ""
    return next(group for group in match.groups() if group)


def _canonical_detail(detail: str) -> str:
    body = detail or ""
    for marker in [
        "concept_generation_failed",
        "revision_regeneration_failed",
        "regeneration_failed",
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
    lower = detail.lower()
    failure_class = ""
    if any(marker in lower for marker in ["concept_generation_failed", "revision_regeneration_failed", "regeneration_failed"]):
        failure_class = "concept_generation_failed"
    elif "source edit provider" in lower or "source edit reference" in lower:
        failure_class = "provider_unavailable"
    elif _has_nonblank_ack_error(detail) or "bridge" in lower:
        failure_class = "bridge_send_failed"
    elif any(marker in lower for marker in ["json_parse_failed", "select_failed", "status_failed", "exit="]):
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
    )


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
    source_fingerprint = fingerprint_signal(signal)
    suffix = source_fingerprint.split(":", 1)[1][:12].upper()
    return {
        "incident_id": f"FRI{now:%Y%m%d}-{suffix}",
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
        "first_seen": now.isoformat(),
        "last_seen": now.isoformat(),
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
    ack = incident.get("ack") or {}
    status = str(ack.get("status") or "none")
    if status in {"sent", "failed", "uncertain", "suppressed"}:
        return AckDecision(False, f"terminal_ack:{status}")
    if status == "reserved":
        return AckDecision(False, "ack_reserved")
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


def merge_signals(state: dict, signals: Iterable[RecoverySignal], now: datetime) -> int:
    incidents = state.setdefault("incidents", [])
    existing = {item.get("source_fingerprint"): item for item in incidents if isinstance(item, dict)}
    opened = 0
    for signal in signals:
        incident = incident_from_signal(signal, now)
        current = existing.get(incident["source_fingerprint"])
        if current is not None:
            current["last_seen"] = now.isoformat()
            if current.get("evidence_quality") != "strong" and incident.get("evidence_quality") == "strong":
                current["evidence_quality"] = "strong"
                current["root_message_id"] = incident.get("root_message_id", "")
                current["provider_message_id_hash"] = incident.get("provider_message_id_hash", "")
                ack = current.get("ack") or {}
                if ack.get("status") == "suppressed" and ack.get("status_detail") == "missing_strong_customer_origin_evidence":
                    current["ack"] = {"status": "none"}
            continue
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
    safe_project = dict(project_excerpt or {})
    safe_project.pop("customer_phone", None)
    safe_audit_rows = []
    for row in audit_rows or []:
        safe_row = dict(row)
        if "chat_id" in safe_row:
            safe_row["chat_id_hash"] = sha256_text(str(safe_row.pop("chat_id") or ""))
        if "sender_phone" in safe_row:
            safe_row["sender_phone_hash"] = sha256_text(str(safe_row.pop("sender_phone") or ""))
        safe_audit_rows.append(safe_row)
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
