"""Catering Business Studio — owner cockpit APIs (M6).

Surfaces the catering agent's four state stores (leads, menu, pricebook,
quote ledger) plus the amendment sidecar, the automation-control kernel and
the decisions.log audit trail in one operator view.

Two hard rules shape everything here:

1. READS are direct + tolerant. Every store is read straight off disk under
   ``settings.state_dir`` (mirrors ``commerce.py``), never through a lock, and
   an absent or malformed file degrades to an explicit empty state carrying
   ``available: false`` — never a 500 and never invented data. M3 and M5 are
   landing in parallel, so additive unknown fields must round-trip untouched.

2. WRITES go through the agent scripts. ``apply-catering-owner-decision``,
   ``set-catering-lead-hold``, ``amend-catering-lead`` and
   ``import-catering-pricebook`` own the locking, the audit row and the
   idempotency key. The cockpit resolves ids, enforces the OTP step-up, and
   shells out through the ``app.shell`` allow-list. It never writes catering
   state itself and never touches the provider/bridge.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..audit import log as audit_log
from ..auth import require_auth, require_fresh_otp
from ..config import get_settings
from ..deps import client_ip, client_ua
from ..shell import run_cli

# Same path-injection as commerce.py / flyer.py so the agent's catering
# primitives + schemas import in production (/opt/shift-agent) and on a fresh
# clone (conftest / dump-openapi prepend src + src/platform).
_AGENT_ROOT = Path("/opt/shift-agent")
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import automation_control  # noqa: E402
import catering_amendments  # noqa: E402
import catering_pricing  # noqa: E402
import catering_quote_ledger  # noqa: E402
from catering_qualification import GAP_LABELS, REQUIRED_FIELDS, qualification_state  # noqa: E402
from schemas import Menu  # noqa: E402

router = APIRouter(prefix="/catering", tags=["catering"])

# Lead ids are minted by create-catering-lead as f"L{n:04d}". Validated before
# the id reaches any path join or argv slot — the same defence flyer applies by
# looking ids up in the store, made explicit here because these ids DO reach a
# subprocess.
_LEAD_ID_RE = re.compile(r"^L[0-9]{4,9}$")
# Pricebook / package / fee / discount ids (schemas._PRICEBOOK_ID_PATTERN).
_PRICEBOOK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

_DECISION_BIN = "/usr/local/bin/apply-catering-owner-decision"
_HOLD_BIN = "/usr/local/bin/set-catering-lead-hold"
_AMEND_BIN = "/usr/local/bin/amend-catering-lead"
_PRICEBOOK_IMPORT_BIN = "/usr/local/bin/import-catering-pricebook"

_AUDIT_PREFIXES = ("catering_", "menu_update_", "outbound_")
_AUDIT_ROW_LIMIT = 200
_AUDIT_SCAN_LINES = 20_000

# Statuses that count as an open, still-workable lead for the dashboard.
_OPEN_STATUSES = frozenset({
    "NEW", "EXTRACTING", "QUALIFYING", "AWAITING_OWNER_APPROVAL",
    "CUSTOMER_FINALIZED", "OWNER_APPROVED", "OWNER_EDITED", "SENT_TO_CUSTOMER",
})
_LOST_STATUSES = frozenset({"NOT_CATERING", "OWNER_REJECTED", "STALE"})

# Env flags reported verbatim, mirroring catering-control-status's contract.
_KERNEL_FLAGS = (
    "CATERING_AUTOMATION_CONTROL_ENABLED",
    "CATERING_STOP_ENABLED",
    "CATERING_TAKEOVER_ENABLED",
)
_ALLOWLIST_FLAGS = (
    "CATERING_AUTOMATION_CONTROL_ALLOWLIST",
    "FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST",
)
_CONTAINMENT_FLAGS = (
    "FRONT_BRAIN_OUTBOUND_ENFORCE",
    "GATEWAY_TRANSPORT_EVIDENCE_ENABLED",
    "GATEWAY_TURN_SEND_BUDGET_ENABLED",
    "BRIDGE_CONVERSATION_THROTTLE_ENABLED",
    "GATEWAY_SEND_THROTTLE_ENABLED",
)
_BUDGET_FLAGS = (
    "GATEWAY_TURN_SEND_BUDGET_LIMIT",
    "GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT",
    "FRONT_BRAIN_CHAT_DAILY_CAP",
)


# ── Paths (resolved per call so tests can repoint state_dir) ─────────────────
def _leads_path() -> Path:
    return get_settings().state_dir / "catering-leads.json"


def _menu_path() -> Path:
    return get_settings().state_dir / "catering-menu.json"


def _pricebook_path() -> Path:
    return get_settings().state_dir / "catering-pricebook.json"


def _quote_ledger_path() -> Path:
    return get_settings().state_dir / "catering-quote-ledger.json"


def _amendments_path() -> Path:
    return get_settings().state_dir / "catering-amendments.json"


def _followups_path() -> Path:
    return get_settings().state_dir / "catering-followups.json"


def _automation_control_path() -> Path:
    return get_settings().state_dir / "catering-automation-control.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Tolerant readers (never raise, never write) ──────────────────────────────
def _read_json(path: Path) -> tuple[Any, Optional[str]]:
    """Return (document, unavailable_reason). A missing file is (None, None) —
    a legitimate empty state, not a fault. Anything unreadable or malformed is
    (None, reason) so the UI can say which."""
    if not path.exists():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"unreadable ({type(e).__name__})"
    if not raw.strip():
        return None, None
    try:
        return json.loads(raw), None
    except (json.JSONDecodeError, ValueError) as e:
        return None, f"malformed JSON ({type(e).__name__})"


def _read_leads() -> tuple[list[dict[str, Any]], Optional[str]]:
    """Leads as tolerant dicts. Deliberately NOT parsed through
    CateringLeadStore: one malformed lead must not blank the whole studio, and
    an M3/M5 field this build has never heard of must survive the read."""
    doc, reason = _read_json(_leads_path())
    if reason:
        return [], reason
    if doc is None:
        return [], None
    leads = doc.get("leads") if isinstance(doc, dict) else None
    if not isinstance(leads, list):
        return [], "malformed store (missing 'leads' list)"
    return [lead for lead in leads if isinstance(lead, dict)], None


def _read_records(path: Path) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Sidecar stores (amendments, quote ledger) share one preservation-safe
    shape: {"records": [...]} of tolerant dicts."""
    doc, reason = _read_json(path)
    if reason:
        return [], reason
    if doc is None:
        return [], None
    records = doc.get("records") if isinstance(doc, dict) else None
    if not isinstance(records, list):
        return [], "malformed store (missing 'records' list)"
    return [r for r in records if isinstance(r, dict)], None


def _find_lead(lead_id: str) -> tuple[dict[str, Any], Optional[str]]:
    leads, reason = _read_leads()
    lead = next((entry for entry in leads if entry.get("lead_id") == lead_id), None)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return lead, reason


def _validated_lead_id(lead_id: str) -> str:
    if not _LEAD_ID_RE.match(lead_id):
        raise HTTPException(status_code=422, detail="invalid lead_id")
    return lead_id


def _mask_phone(value: Any) -> str:
    """Keep the country/area prefix and the last two digits, mask the middle.
    List views are the highest-exposure surface in the cockpit (screen-shares,
    shoulder-surfing); the detail drawer shows the same masked value because
    the operator reaches the customer through the agent, not by dialling."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    digits = [i for i, ch in enumerate(raw) if ch.isdigit()]
    if len(digits) <= 6:
        return raw
    masked = list(raw)
    for i in digits[3:-2]:
        masked[i] = "•"
    return "".join(masked)


def _read_env_layered(name: str) -> tuple[str, Optional[str]]:
    """Return (value, source) where source is process_env|hermes_env|agent_env
    or None when the flag could not be read anywhere. Mirrors
    flyer._read_env_layered — the cockpit process does NOT inherit the agent's
    EnvironmentFile, so "unset" and "unreadable" must stay distinguishable."""
    value = os.environ.get(name, "").strip()
    if value:
        return value, "process_env"
    candidates = (
        ("hermes_env", Path(os.environ.get("HERMES_ENV_PATH", "/root/.hermes/.env"))),
        ("agent_env", Path(os.environ.get("SHIFT_AGENT_ENV_PATH", "/opt/shift-agent/.env"))),
    )
    for source, env_path in candidates:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                if key.strip() == name:
                    raw = raw.strip().strip('"').strip("'")
                    if raw:
                        return raw, source
        except OSError:
            continue
    return "", None


# ── Response models ─────────────────────────────────────────────────────────
class DashboardCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_leads: int = 0
    new_leads: int = 0
    qualifying: int = 0
    awaiting_owner_approval: int = 0
    proposals_sent: int = 0
    booked_this_month: int = 0
    lost_this_month: int = 0
    on_hold: int = 0
    followups_due: int = 0


class ReadinessFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pricebook_present: bool = False
    pricebook_placeholder: bool = False
    pricebook_version: Optional[int] = None
    menu_present: bool = False
    menu_version: Optional[int] = None
    menu_item_count: int = 0
    kill_switch_engaged: bool = False
    automation_kernel_armed: bool = False
    automation_kernel_armed_source: Optional[str] = None
    followups_enabled: Optional[bool] = None


class CateringDashboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    counts: DashboardCounts
    readiness: ReadinessFlags
    degraded: list[str] = Field(default_factory=list)


class QualificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: bool
    missing: list[str] = Field(default_factory=list)
    missing_labels: list[str] = Field(default_factory=list)
    asked: list[str] = Field(default_factory=list)
    answered_count: int = 0
    required_count: int = 0
    rounds: int = 0


class LeadRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_id: str
    status: str
    customer_phone_masked: str
    customer_name: Optional[str] = None
    event_date: Optional[str] = None
    headcount: Optional[int] = None
    event_type: Optional[str] = None
    on_hold: bool = False
    hold_reason: Optional[str] = None
    approval_code: Optional[str] = None
    quote_version: int = 0
    latest_quote_total_usd: Optional[int] = None
    qualification: QualificationSummary
    next_action: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class LeadListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leads: list[LeadRow] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
    available: bool = True
    degraded: Optional[str] = None


class QuoteVersionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    # None for the first committed version. `items_added` then lists the whole
    # basket (diff_versions has nothing to subtract), which reads as a change
    # when it is not — callers must gate the added/removed display on this.
    from_version: Optional[int] = None
    ledger_entry_id: Optional[str] = None
    quote_total_usd: Optional[int] = None
    source: Optional[str] = None
    price_status: Optional[str] = None
    pricebook_version: Optional[int] = None
    menu_version: Optional[int] = None
    created_at: Optional[str] = None
    item_count: int = 0
    diff_summary: str = ""
    items_added: list[str] = Field(default_factory=list)
    items_removed: list[str] = Field(default_factory=list)
    quote_text_changed: bool = False
    total_delta_usd: int = 0


class AmendmentRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amendment_id: str
    source: Optional[str] = None
    source_transport: Optional[str] = None
    status: Optional[str] = None
    raw_text_prefix: str = ""
    raw_text_truncated: bool = False
    captured_at: Optional[str] = None
    applied: bool = False
    applied_at: Optional[str] = None
    disposition: Optional[str] = None


class TimelineRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: str
    event: str
    detail: str
    source: str


class ControlChip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    stored_mode: Optional[str] = None
    effective_mode: Optional[str] = None
    expired: bool = False
    since_ts: str = ""
    actor: str = ""
    reason: str = ""
    pause_expires_ts: str = ""
    takeover_expires_ts: str = ""
    note: str = ""


class FollowupRow(BaseModel):
    """Tolerant projection of an M5 followup record. M5 is landing in parallel,
    so the shape is read defensively and the untouched record is echoed in
    `raw` rather than being forced through a schema this build invented."""
    model_config = ConfigDict(extra="forbid")
    lead_id: str = ""
    followup_type: str = ""
    due_at: str = ""
    status: str = ""
    approval_code: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class LeadDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_id: str
    status: str
    customer_phone_masked: str
    customer_name: Optional[str] = None
    raw_inquiry: str = ""
    original_message_id: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    extracted: dict[str, Any] = Field(default_factory=dict)
    qualification: QualificationSummary
    pending_questions: list[str] = Field(default_factory=list)
    quote_text: str = ""
    quote_version: int = 0
    quote_total_usd: Optional[int] = None
    approval_code: Optional[str] = None
    on_hold: bool = False
    hold_reason: Optional[str] = None
    hold_set_at: Optional[str] = None
    deposit_status: str = "none"
    deposit_amount_cents: int = 0
    selected_items: list[dict[str, Any]] = Field(default_factory=list)
    quote_versions: list[QuoteVersionRow] = Field(default_factory=list)
    quote_ledger_available: bool = True
    amendments: list[AmendmentRow] = Field(default_factory=list)
    amendments_available: bool = True
    control: ControlChip
    timeline: list[TimelineRow] = Field(default_factory=list)
    followups: list[FollowupRow] = Field(default_factory=list)
    followups_available: bool = False
    degraded: list[str] = Field(default_factory=list)


class MenuItemRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    price_usd: Optional[float] = None
    category: str = "main"
    dietary_tags: list[str] = Field(default_factory=list)
    available: bool = True
    serves: Optional[int] = None
    notes: str = ""


class MenuResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    version: Optional[int] = None
    updated_at: Optional[str] = None
    updated_by: str = ""
    notes: str = ""
    items: list[MenuItemRow] = Field(default_factory=list)
    degraded: Optional[str] = None


class PackageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    price_per_person_cents: int
    min_guests: int = 1
    description: str = ""
    dietary_profile: list[str] = Field(default_factory=list)
    active: bool = True


class FeeRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    kind: str = "other"
    amount_cents: int = 0
    per_unit: Optional[str] = None
    active: bool = True


class DiscountRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    kind: str
    value: int
    max_amount_cents: Optional[int] = None
    requires_owner_code: bool = True
    active: bool = True


class PricebookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    version: Optional[int] = None
    effective_date: Optional[str] = None
    updated_at: Optional[str] = None
    updated_by: str = ""
    currency: str = "USD"
    placeholder: bool = False
    tax_rate_bps: int = 0
    per_person_packages: list[PackageRow] = Field(default_factory=list)
    fixed_fees: list[FeeRow] = Field(default_factory=list)
    approved_discounts: list[DiscountRow] = Field(default_factory=list)
    item_price_override_count: int = 0
    notes: str = ""
    degraded: Optional[str] = None


class QuotePreviewLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    qty: int
    unit_cents: Optional[int] = None
    extended_cents: Optional[int] = None
    source: Optional[str] = None


class QuotePreviewFee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fee_id: str
    name: str
    kind: str
    per_unit: Optional[str] = None
    unit_cents: int = 0
    units: Optional[int] = None
    extended_cents: Optional[int] = None


class QuotePreviewResponse(BaseModel):
    """Read-only calculator output. Runs the same deterministic kernel the
    agent uses (catering_pricing.compute_quote) so the number the owner sees
    here is the number a quote would carry — nothing is written or sent."""
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    guest_count: int = 0
    package_id: Optional[str] = None
    package_name: Optional[str] = None
    price_per_person_cents: Optional[int] = None
    per_person_subtotal_cents: int = 0
    lines: list[QuotePreviewLine] = Field(default_factory=list)
    items_subtotal_cents: int = 0
    fees: list[QuotePreviewFee] = Field(default_factory=list)
    fees_subtotal_cents: int = 0
    subtotal_cents: int = 0
    discount_id: Optional[str] = None
    discount_name: Optional[str] = None
    discount_cents: int = 0
    taxable_cents: int = 0
    tax_rate_bps: int = 0
    tax_cents: int = 0
    total_cents: int = 0
    total_per_guest_cents: int = 0
    currency: str = "USD"
    price_status: str = "pending_owner_review"
    deliverable: bool = False
    flags: list[str] = Field(default_factory=list)
    pricebook_version: Optional[int] = None
    menu_version: Optional[int] = None
    unavailable_reason: Optional[str] = None


class AllowlistSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    entry_count: int
    effect: str
    source: Optional[str] = None


class FlagValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = ""
    source: Optional[str] = None
    known: bool = False


class ConversationControlRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_key_hash: str
    stored_mode: str
    effective_mode: str
    expired: bool
    since_ts: str = ""
    actor: str = ""
    reason: str = ""
    pause_expires_ts: str = ""
    takeover_expires_ts: str = ""


class HeldLeadRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_id: str
    status: str
    hold_reason: str = ""
    hold_set_at: str = ""


class ControlsResponse(BaseModel):
    """Mirrors catering-control-status's JSON contract. Env-derived flags carry
    a `source`; a flag the cockpit genuinely cannot read reports known=false
    rather than a fabricated "off"."""
    model_config = ConfigDict(extra="forbid")
    generated_at: str
    kill_switch_engaged: bool = False
    kill_switch_set_at: str = ""
    kernel_flags: dict[str, FlagValue] = Field(default_factory=dict)
    kernel_allowlists: dict[str, AllowlistSummary] = Field(default_factory=dict)
    containment_flags: dict[str, FlagValue] = Field(default_factory=dict)
    budget_flags: dict[str, FlagValue] = Field(default_factory=dict)
    conversations: list[ConversationControlRow] = Field(default_factory=list)
    conversations_available: bool = False
    suppressed_count: int = 0
    held_leads: list[HeldLeadRow] = Field(default_factory=list)
    followups_enabled: Optional[bool] = None
    degraded: list[str] = Field(default_factory=list)


class FollowupsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    followups: list[FollowupRow] = Field(default_factory=list)
    due_count: int = 0
    unavailable_reason: Optional[str] = None
    degraded: Optional[str] = None


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""


# ── Request bodies ──────────────────────────────────────────────────────────
class DecisionBody(BaseModel):
    """`quote_text` is the owner-authored customer quote. On approve it is
    piped to the script's stdin; when omitted the script renders from lead
    state (--quote-from-lead-state), which needs a finalized lead."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "edit"]
    quote_text: str = Field(default="", max_length=4000)
    edit_text: str = Field(default="", max_length=2000)
    reason: str = Field(default="", max_length=300)
    skip_finalize: bool = False


class HoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool
    reason: str = Field(default="", max_length=200)


class AmendApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["amendment", "answer"] = "amendment"
    answer_text: str = Field(default="", max_length=2000)


class PricebookImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: dict[str, Any]
    dry_run: bool = False


# ── Derivations ─────────────────────────────────────────────────────────────
def _qualification(lead: dict[str, Any]) -> QualificationSummary:
    state = qualification_state(lead)
    missing = [f for f in state.get("missing", []) if isinstance(f, str)]
    return QualificationSummary(
        complete=bool(state.get("complete")),
        missing=missing,
        missing_labels=[GAP_LABELS.get(f, f) for f in missing],
        asked=[a for a in state.get("asked", []) if isinstance(a, str)],
        answered_count=len(REQUIRED_FIELDS) - len(missing),
        required_count=len(REQUIRED_FIELDS),
        rounds=_safe_int(lead.get("qualification_rounds")),
    )


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text or None


def _next_action(lead: dict[str, Any], qualification: QualificationSummary) -> str:
    """The one thing the owner would do next. Hold and kill-switch style blocks
    win over lifecycle state — a held lead is not "awaiting your approval", it
    is waiting on the owner to lift the hold."""
    if lead.get("on_hold") is True:
        return "On hold — lift the hold to resume"
    status = str(lead.get("status") or "")
    if status == "QUALIFYING":
        gaps = ", ".join(qualification.missing_labels[:3])
        return f"Waiting on customer: {gaps}" if gaps else "Qualification in progress"
    return {
        "NEW": "Awaiting extraction",
        "EXTRACTING": "Extraction running",
        "AWAITING_OWNER_APPROVAL": "Review and approve the quote",
        "CUSTOMER_FINALIZED": "Customer finalized — approve to send",
        "OWNER_APPROVED": "Approved — quote delivery in flight",
        "OWNER_EDITED": "Edits captured — re-draft pending",
        "SENT_TO_CUSTOMER": "Sent — awaiting customer reply",
        # M3's won state. Without an entry here a lead the customer just ACCEPTED
        # read "No action" — the one row in the list that most needs one.
        "BOOKED": "Booked — confirm the final details",
        "OWNER_REJECTED": "Rejected (closed)",
        "NOT_CATERING": "Not a catering inquiry (closed)",
        "CLOSED": "Closed",
        "STALE": "Went silent (closed)",
    }.get(status, "No action")


def _quote_versions(lead_id: str) -> tuple[list[QuoteVersionRow], Optional[str]]:
    records, reason = _read_records(_quote_ledger_path())
    if reason:
        return [], reason
    mine = [r for r in records if r.get("lead_id") == lead_id]
    mine.sort(key=lambda r: _safe_int(r.get("version")))
    rows: list[QuoteVersionRow] = []
    previous: Optional[dict[str, Any]] = None
    for record in mine:
        from_version: Optional[int] = None
        try:
            diff = catering_quote_ledger.diff_versions(previous, record)
            summary, added, removed = (
                diff.summary_line(), list(diff.items_added), list(diff.items_removed),
            )
            text_changed, delta = diff.quote_text_changed, diff.total_delta_usd
            from_version = diff.from_version
        except (TypeError, ValueError, KeyError):
            # A hand-edited or future-shaped record must not blank the history.
            summary, added, removed, text_changed, delta = "", [], [], False, 0
        items = record.get("selected_items")
        rows.append(QuoteVersionRow(
            version=_safe_int(record.get("version")),
            from_version=from_version,
            ledger_entry_id=_opt_str(record.get("ledger_entry_id")),
            quote_total_usd=_opt_int(record.get("quote_total_usd")),
            source=_opt_str(record.get("source")),
            price_status=_opt_str(record.get("price_status")),
            pricebook_version=_opt_int(record.get("pricebook_version")),
            menu_version=_opt_int(record.get("menu_version")),
            created_at=_opt_str(record.get("created_at")),
            item_count=len(items) if isinstance(items, list) else 0,
            diff_summary=summary,
            items_added=added,
            items_removed=removed,
            quote_text_changed=text_changed,
            total_delta_usd=delta,
        ))
        previous = record
    rows.reverse()  # newest first
    return rows, None


def _amendments(lead_id: str) -> tuple[list[AmendmentRow], Optional[str]]:
    records, reason = _read_records(_amendments_path())
    if reason:
        return [], reason
    mine = [r for r in records if r.get("lead_id") == lead_id]
    mine.sort(key=lambda r: str(r.get("amendment_id") or ""), reverse=True)
    applied_field = catering_amendments.APPLIED_MARK_FIELD
    rows = [
        AmendmentRow(
            amendment_id=str(r.get("amendment_id") or ""),
            source=_opt_str(r.get("source")),
            source_transport=_opt_str(r.get("source_transport")),
            status=_opt_str(r.get("status")),
            raw_text_prefix=str(r.get("raw_text") or "")[:500],
            raw_text_truncated=bool(r.get("raw_text_truncated")),
            captured_at=_opt_str(r.get("captured_at")),
            applied=bool(r.get(applied_field)),
            applied_at=_opt_str(r.get(applied_field)),
            disposition=_opt_str(r.get("disposition")),
        )
        for r in mine
    ]
    return rows, None


def _control_for(lead: dict[str, Any]) -> ControlChip:
    """The automation-control record for this lead's conversation. The store is
    keyed by canonical identity, so the lead's phone has to be canonicalized the
    same way the kernel does it."""
    phone = str(lead.get("customer_phone") or "")
    if not phone:
        return ControlChip(available=False, note="lead has no customer phone")
    path = _automation_control_path()
    doc, reason = _read_json(path)
    if reason:
        return ControlChip(available=False, note=f"automation-control store {reason}")
    if doc is None:
        return ControlChip(available=False, note="automation-control store absent — no suppression recorded")
    conversations = doc.get("conversations") if isinstance(doc, dict) else None
    if not isinstance(conversations, dict):
        return ControlChip(available=False, note="automation-control store malformed")
    key = automation_control.canonical_key(phone, phone)
    record = conversations.get(key)
    if not isinstance(record, dict):
        return ControlChip(
            available=True, stored_mode="active", effective_mode="active",
            note="no record — conversation runs normally",
        )
    stored = str(record.get("mode") or "active")
    effective = automation_control._effective_mode(record)
    return ControlChip(
        available=True,
        stored_mode=stored,
        effective_mode=effective,
        expired=stored != effective,
        since_ts=str(record.get("since_ts") or ""),
        actor=str(record.get("actor") or ""),
        reason=str(record.get("reason") or ""),
        pause_expires_ts=str(record.get("pause_expires_ts") or ""),
        takeover_expires_ts=str(record.get("takeover_expires_ts") or ""),
    )


def _ndjson_rows(path: Path, *, max_lines: int = _AUDIT_SCAN_LINES) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(doc, dict):
            rows.append(doc)
    return rows


def _entry_detail(entry: dict[str, Any]) -> str:
    parts = []
    for key in ("from_status", "to_status", "actor", "reason", "decision",
                "amendment_id", "version", "quote_total_usd", "price_status"):
        value = entry.get(key)
        if value not in ("", None, []):
            parts.append(f"{key}={value}")
    if parts:
        return "; ".join(str(p) for p in parts)[:500]
    details = entry.get("details")
    if isinstance(details, dict):
        return json.dumps(details, default=str, separators=(",", ":"))[:500]
    if isinstance(details, str):
        return details[:500]
    return ""


def _audit_timeline(lead_id: str, phone: str) -> list[TimelineRow]:
    """Catering + outbound rows referencing this lead, newest first. Best-effort
    by construction: the authoritative per-lead trail is the lead record itself,
    and an unreadable decisions.log must not 500 the detail view."""
    rows: list[TimelineRow] = []
    for entry in _ndjson_rows(get_settings().decisions_path):
        entry_type = str(entry.get("type") or "")
        if not entry_type.startswith(_AUDIT_PREFIXES):
            continue
        matches = entry.get("lead_id") == lead_id
        if not matches and phone:
            matches = entry.get("customer_phone") == phone or entry.get("to") == phone
        if not matches:
            continue
        rows.append(TimelineRow(
            ts=str(entry.get("ts") or ""),
            event=entry_type,
            detail=_entry_detail(entry),
            source="decisions",
        ))
    rows.sort(key=lambda r: r.ts, reverse=True)
    return rows[:_AUDIT_ROW_LIMIT]


def _read_followups() -> tuple[list[FollowupRow], Optional[str], Optional[str]]:
    """Return (rows, unavailable_reason, degraded). M5 owns this file and may
    not have shipped it yet — an absent file is an empty state, not an error."""
    path = _followups_path()
    if not path.exists():
        return [], "followup store not present (feature not yet enabled)", None
    doc, reason = _read_json(path)
    if reason:
        return [], None, reason
    if doc is None:
        return [], None, None
    raw = doc.get("followups") if isinstance(doc, dict) else doc
    if isinstance(raw, dict):
        raw = raw.get("records")
    if not isinstance(raw, list):
        return [], None, "malformed store (no followup list found)"
    rows = [
        FollowupRow(
            lead_id=str(r.get("lead_id") or ""),
            followup_type=str(r.get("followup_type") or r.get("type") or ""),
            due_at=str(r.get("due_at") or ""),
            status=str(r.get("status") or ""),
            approval_code=_opt_str(r.get("approval_code")),
            raw=r,
        )
        for r in raw if isinstance(r, dict)
    ]
    return rows, None, None


def _load_menu() -> tuple[Optional[Menu], Optional[str]]:
    doc, reason = _read_json(_menu_path())
    if reason or doc is None:
        return None, reason
    try:
        return Menu.model_validate(doc), None
    except Exception as e:  # noqa: BLE001 — pydantic ValidationError + friends
        return None, f"menu failed validation ({type(e).__name__})"


def _load_pricebook() -> tuple[Optional[Any], Optional[str]]:
    try:
        return catering_pricing.load_pricebook(_pricebook_path()), None
    except catering_pricing.PricingError as e:
        return None, str(e)[:300]


def _followups_enabled() -> Optional[bool]:
    """From config.yaml's catering_followup block. None when config is not
    readable — the cockpit says "unknown" rather than guessing "off"."""
    try:
        from ..state import load_config

        return bool(load_config().catering_followup.enabled)
    except Exception:  # noqa: BLE001 — missing/invalid config is an unknown, not a fault
        return None


# ── Read endpoints ──────────────────────────────────────────────────────────
@router.get("/dashboard")
async def get_dashboard(_=Depends(require_auth)) -> CateringDashboard:
    settings = get_settings()
    degraded: list[str] = []

    leads, leads_reason = _read_leads()
    if leads_reason:
        degraded.append(f"leads: {leads_reason}")

    month_prefix = _now().strftime("%Y-%m")

    def _in_month(lead: dict[str, Any]) -> bool:
        return str(lead.get("updated_at") or "").startswith(month_prefix)

    counts = DashboardCounts(
        total_leads=len(leads),
        new_leads=sum(1 for lead in leads if lead.get("status") in ("NEW", "EXTRACTING")),
        qualifying=sum(1 for lead in leads if lead.get("status") == "QUALIFYING"),
        awaiting_owner_approval=sum(
            1 for lead in leads
            if lead.get("status") in ("AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED")
        ),
        proposals_sent=sum(
            1 for lead in leads
            if lead.get("status") in ("SENT_TO_CUSTOMER", "OWNER_APPROVED")
        ),
        # BOOKED is M3's won state (record-catering-acceptance on an accept,
        # mark-catering-lead-outcome --outcome won). Counting CLOSED alone made
        # every conversion invisible until it was also closed out.
        # KNOWN RESIDUAL: CLOSED is overloaded — a DECLINE also lands there
        # (SENT_TO_CUSTOMER -> CLOSED), so it still inflates this count. The lead
        # carries `customer_acceptance` ("accepted"/"declined"), which would
        # separate them; changing what CLOSED means here moves a number the
        # operator already reads, so it is left for a ruled follow-up.
        booked_this_month=sum(
            1 for lead in leads
            if lead.get("status") in ("BOOKED", "CLOSED") and _in_month(lead)
        ),
        lost_this_month=sum(
            1 for lead in leads if lead.get("status") in _LOST_STATUSES and _in_month(lead)
        ),
        on_hold=sum(1 for lead in leads if lead.get("on_hold") is True),
    )

    followups, _unavailable, followups_degraded = _read_followups()
    if followups_degraded:
        degraded.append(f"followups: {followups_degraded}")
    now_iso = _now().isoformat()
    counts.followups_due = sum(
        1 for f in followups
        if f.status in ("", "scheduled", "due", "pending") and f.due_at and f.due_at <= now_iso
    )

    menu, menu_reason = _load_menu()
    if menu_reason:
        degraded.append(f"menu: {menu_reason}")
    pricebook, pricebook_reason = _load_pricebook()
    if pricebook_reason:
        degraded.append(f"pricebook: {pricebook_reason}")

    kernel_value, kernel_source = _read_env_layered("CATERING_AUTOMATION_CONTROL_ENABLED")
    readiness = ReadinessFlags(
        pricebook_present=pricebook is not None,
        pricebook_placeholder=bool(pricebook is not None and pricebook.placeholder),
        pricebook_version=pricebook.version if pricebook is not None else None,
        menu_present=menu is not None,
        menu_version=menu.version if menu is not None else None,
        menu_item_count=len(menu.items) if menu is not None else 0,
        kill_switch_engaged=settings.disabled_flag.exists(),
        automation_kernel_armed=kernel_value.lower() in ("1", "true", "yes"),
        automation_kernel_armed_source=kernel_source,
        followups_enabled=_followups_enabled(),
    )
    return CateringDashboard(
        generated_at=now_iso, counts=counts, readiness=readiness, degraded=degraded,
    )


@router.get("/leads")
async def list_leads(
    status: Optional[str] = Query(default=None, max_length=40),
    _=Depends(require_auth),
) -> LeadListResponse:
    leads, reason = _read_leads()
    status_counts: dict[str, int] = {}
    for lead in leads:
        key = str(lead.get("status") or "UNKNOWN")
        status_counts[key] = status_counts.get(key, 0) + 1

    # The quote ledger is the ONLY place a version number exists. CateringLead
    # carries a `quote_version` field that nothing has ever written, so reading it
    # showed "0 versions" for a lead with a full committed history — while
    # latest_quote_total_usd, joined from this same ledger, was right beside it and
    # correct. Both columns now come from the one join.
    ledger_records, _ledger_reason = _read_records(_quote_ledger_path())
    latest_total: dict[str, tuple[int, Optional[int]]] = {}
    for record in ledger_records:
        lead_id = str(record.get("lead_id") or "")
        version = _safe_int(record.get("version"))
        if lead_id and version >= latest_total.get(lead_id, (0, None))[0]:
            latest_total[lead_id] = (version, _opt_int(record.get("quote_total_usd")))

    selected = [lead for lead in leads if status is None or lead.get("status") == status]
    rows: list[LeadRow] = []
    for lead in selected:
        qualification = _qualification(lead)
        extracted = lead.get("extracted")
        extracted = extracted if isinstance(extracted, dict) else {}
        lead_id = str(lead.get("lead_id") or "")
        rows.append(LeadRow(
            lead_id=lead_id,
            status=str(lead.get("status") or "UNKNOWN"),
            customer_phone_masked=_mask_phone(lead.get("customer_phone")),
            customer_name=_opt_str(lead.get("customer_name")),
            event_date=_opt_str(extracted.get("event_date")),
            headcount=_opt_int(extracted.get("headcount")),
            event_type=_opt_str(extracted.get("event_type")),
            on_hold=lead.get("on_hold") is True,
            hold_reason=_opt_str(lead.get("hold_reason")),
            approval_code=_opt_str(lead.get("owner_approval_code")),
            quote_version=latest_total.get(lead_id, (0, None))[0],
            latest_quote_total_usd=latest_total.get(lead_id, (0, None))[1],
            qualification=qualification,
            next_action=_next_action(lead, qualification),
            created_at=_opt_str(lead.get("created_at")),
            updated_at=_opt_str(lead.get("updated_at")),
        ))
    rows.sort(key=lambda r: r.updated_at or "", reverse=True)
    return LeadListResponse(
        leads=rows,
        status_counts=status_counts,
        available=reason is None,
        degraded=reason,
    )


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, _=Depends(require_auth)) -> LeadDetail:
    lead_id = _validated_lead_id(lead_id)
    lead, store_reason = _find_lead(lead_id)
    degraded: list[str] = []
    if store_reason:
        degraded.append(f"leads: {store_reason}")

    versions, versions_reason = _quote_versions(lead_id)
    if versions_reason:
        degraded.append(f"quote ledger: {versions_reason}")
    amendments, amendments_reason = _amendments(lead_id)
    if amendments_reason:
        degraded.append(f"amendments: {amendments_reason}")
    followups, followups_unavailable, followups_degraded = _read_followups()
    if followups_degraded:
        degraded.append(f"followups: {followups_degraded}")

    extracted = lead.get("extracted")
    extracted = extracted if isinstance(extracted, dict) else {}
    selected_items = lead.get("selected_items")
    pending = lead.get("pending_questions")

    return LeadDetail(
        lead_id=lead_id,
        status=str(lead.get("status") or "UNKNOWN"),
        customer_phone_masked=_mask_phone(lead.get("customer_phone")),
        customer_name=_opt_str(lead.get("customer_name")),
        raw_inquiry=str(lead.get("raw_inquiry") or ""),
        original_message_id=str(lead.get("original_message_id") or ""),
        created_at=_opt_str(lead.get("created_at")),
        updated_at=_opt_str(lead.get("updated_at")),
        extracted=extracted,
        qualification=_qualification(lead),
        pending_questions=[q for q in (pending or []) if isinstance(q, str)],
        quote_text=str(lead.get("quote_text") or ""),
        # From the ledger, not the lead: `CateringLead.quote_version` is never
        # written by anything, so it is permanently 0. `versions` is already this
        # lead's committed history; max() rather than an end index so the answer
        # does not depend on _quote_versions' newest-first render order.
        quote_version=max((v.version for v in versions), default=0),
        quote_total_usd=_opt_int(lead.get("quote_total_usd")),
        approval_code=_opt_str(lead.get("owner_approval_code")),
        on_hold=lead.get("on_hold") is True,
        hold_reason=_opt_str(lead.get("hold_reason")),
        hold_set_at=_opt_str(lead.get("hold_set_at")),
        deposit_status=str(lead.get("deposit_status") or "none"),
        deposit_amount_cents=_safe_int(lead.get("deposit_amount_cents")),
        selected_items=[i for i in (selected_items or []) if isinstance(i, dict)],
        quote_versions=versions,
        quote_ledger_available=versions_reason is None,
        amendments=amendments,
        amendments_available=amendments_reason is None,
        control=_control_for(lead),
        timeline=_audit_timeline(lead_id, str(lead.get("customer_phone") or "")),
        followups=[f for f in followups if f.lead_id == lead_id],
        followups_available=followups_unavailable is None and followups_degraded is None,
        degraded=degraded,
    )


@router.get("/menu")
async def get_menu(_=Depends(require_auth)) -> MenuResponse:
    menu, reason = _load_menu()
    if menu is None:
        return MenuResponse(available=False, degraded=reason)
    return MenuResponse(
        available=True,
        version=menu.version,
        updated_at=menu.updated_at.isoformat(),
        updated_by=menu.updated_by,
        notes=menu.notes,
        items=[
            MenuItemRow(
                name=item.name,
                price_usd=item.price_usd,
                category=item.category,
                dietary_tags=list(item.dietary_tags),
                available=item.available,
                serves=item.serves,
                notes=item.notes,
            )
            for item in menu.items
        ],
    )


@router.get("/pricebook")
async def get_pricebook(_=Depends(require_auth)) -> PricebookResponse:
    pricebook, reason = _load_pricebook()
    if pricebook is None:
        return PricebookResponse(available=False, degraded=reason)
    return PricebookResponse(
        available=True,
        version=pricebook.version,
        effective_date=pricebook.effective_date.isoformat(),
        updated_at=pricebook.updated_at.isoformat(),
        updated_by=pricebook.updated_by,
        currency=pricebook.currency,
        placeholder=pricebook.placeholder,
        tax_rate_bps=pricebook.tax_rate_bps,
        per_person_packages=[
            PackageRow(
                id=p.id, name=p.name,
                price_per_person_cents=p.price_per_person_cents,
                min_guests=p.min_guests, description=p.description,
                dietary_profile=list(p.dietary_profile), active=p.active,
            )
            for p in pricebook.per_person_packages
        ],
        fixed_fees=[
            FeeRow(
                id=f.id, name=f.name, kind=f.kind, amount_cents=f.amount_cents,
                per_unit=f.per_unit, active=f.active,
            )
            for f in pricebook.fixed_fees
        ],
        approved_discounts=[
            DiscountRow(
                id=d.id, name=d.name, kind=d.kind, value=d.value,
                max_amount_cents=d.max_amount_cents,
                requires_owner_code=d.requires_owner_code, active=d.active,
            )
            for d in pricebook.approved_discounts
        ],
        item_price_override_count=len(pricebook.item_price_overrides),
        notes=pricebook.notes,
    )


@router.get("/quote-preview")
async def get_quote_preview(
    lead_id: Optional[str] = Query(default=None, max_length=16),
    guest_count: Optional[int] = Query(default=None, ge=1, le=10000),
    package_id: Optional[str] = Query(default=None, max_length=40),
    discount_id: Optional[str] = Query(default=None, max_length=40),
    _=Depends(require_auth),
) -> QuotePreviewResponse:
    """Price one hypothetical event with the deterministic kernel. READ-ONLY:
    nothing is written, no version is committed, no message is sent."""
    for value in (package_id, discount_id):
        if value and not _PRICEBOOK_ID_RE.match(value):
            raise HTTPException(status_code=422, detail="invalid pricebook id")

    guests = guest_count
    if lead_id is not None:
        lead, _reason = _find_lead(_validated_lead_id(lead_id))
        extracted = lead.get("extracted")
        if guests is None and isinstance(extracted, dict):
            guests = _opt_int(extracted.get("headcount"))
    if guests is None:
        return QuotePreviewResponse(
            available=False,
            unavailable_reason="guest count unknown — supply guest_count or qualify the lead",
        )

    pricebook, pricebook_reason = _load_pricebook()
    if pricebook is None:
        return QuotePreviewResponse(
            available=False,
            guest_count=guests,
            unavailable_reason=pricebook_reason or "no pricebook imported yet",
        )
    menu, _menu_reason = _load_menu()

    try:
        computation = catering_pricing.compute_quote(
            guests, package_id, None, discount_id, pricebook, menu,
        )
    except catering_pricing.PricingError as e:
        return QuotePreviewResponse(
            available=False, guest_count=guests, unavailable_reason=str(e)[:300],
        )

    return QuotePreviewResponse(
        available=True,
        guest_count=computation.guest_count,
        package_id=computation.package_id,
        package_name=computation.package_name,
        price_per_person_cents=computation.price_per_person_cents,
        per_person_subtotal_cents=computation.per_person_subtotal_cents,
        lines=[
            QuotePreviewLine(
                name=line.name, qty=line.qty, unit_cents=line.unit_cents,
                extended_cents=line.extended_cents, source=line.source,
            )
            for line in computation.lines
        ],
        items_subtotal_cents=computation.items_subtotal_cents,
        fees=[
            QuotePreviewFee(
                fee_id=fee.fee_id, name=fee.name, kind=fee.kind,
                per_unit=fee.per_unit, unit_cents=fee.unit_cents,
                units=fee.units, extended_cents=fee.extended_cents,
            )
            for fee in computation.fees
        ],
        fees_subtotal_cents=computation.fees_subtotal_cents,
        subtotal_cents=computation.subtotal_cents,
        discount_id=computation.discount_id,
        discount_name=computation.discount_name,
        discount_cents=computation.discount_cents,
        taxable_cents=computation.taxable_cents,
        tax_rate_bps=computation.tax_rate_bps,
        tax_cents=computation.tax_cents,
        total_cents=computation.total_cents,
        total_per_guest_cents=computation.total_per_guest_cents(),
        currency=computation.currency,
        price_status=computation.price_status,
        deliverable=computation.is_deliverable(),
        flags=list(computation.flags),
        pricebook_version=computation.pricebook_version,
        menu_version=computation.menu_version,
    )


def _allowlist_summary(raw: str, source: Optional[str]) -> AllowlistSummary:
    """ppv1/#612 semantics: empty DISABLES fail-closed, `*` graduates every
    chat. Entry counts only — this surface never prints raw phone numbers."""
    entries = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if "*" in entries:
        return AllowlistSummary(mode="wildcard", entry_count=len(entries),
                                effect="every chat admitted", source=source)
    if not entries:
        return AllowlistSummary(
            mode="empty", entry_count=0,
            effect="DISABLED (fail-closed — empty never means global-on)",
            source=source,
        )
    return AllowlistSummary(mode="scoped", entry_count=len(entries),
                            effect=f"{len(entries)} explicit chat(s) admitted",
                            source=source)


def _flag_values(names: tuple[str, ...]) -> dict[str, FlagValue]:
    out: dict[str, FlagValue] = {}
    for name in names:
        value, source = _read_env_layered(name)
        out[name] = FlagValue(value=value, source=source, known=source is not None)
    return out


@router.get("/controls")
async def get_controls(_=Depends(require_auth)) -> ControlsResponse:
    settings = get_settings()
    degraded: list[str] = []

    kill_switch_engaged = settings.disabled_flag.exists()
    kill_switch_set_at = ""
    if kill_switch_engaged:
        try:
            kill_switch_set_at = datetime.fromtimestamp(
                settings.disabled_flag.stat().st_mtime, tz=timezone.utc,
            ).isoformat()
        except OSError:
            degraded.append("kill switch flag stat failed")

    conversations: list[ConversationControlRow] = []
    conversations_available = False
    doc, reason = _read_json(_automation_control_path())
    if reason:
        degraded.append(f"automation-control: {reason}")
    else:
        conversations_available = True
        raw = doc.get("conversations") if isinstance(doc, dict) else {}
        if isinstance(raw, dict):
            for key, record in sorted(raw.items()):
                if not isinstance(record, dict):
                    continue
                stored = str(record.get("mode") or "active")
                effective = automation_control._effective_mode(record)
                conversations.append(ConversationControlRow(
                    # The store is already keyed by canonical identity; hash it
                    # so this surface never prints a raw customer identifier.
                    chat_key_hash=automation_control.chat_key_hash(key),
                    stored_mode=stored,
                    effective_mode=effective,
                    expired=stored != effective,
                    since_ts=str(record.get("since_ts") or ""),
                    actor=str(record.get("actor") or ""),
                    reason=str(record.get("reason") or ""),
                    pause_expires_ts=str(record.get("pause_expires_ts") or ""),
                    takeover_expires_ts=str(record.get("takeover_expires_ts") or ""),
                ))
        elif doc is not None:
            degraded.append("automation-control: malformed conversations block")

    leads, leads_reason = _read_leads()
    if leads_reason:
        degraded.append(f"leads: {leads_reason}")
    held = [
        HeldLeadRow(
            lead_id=str(lead.get("lead_id") or ""),
            status=str(lead.get("status") or ""),
            hold_reason=str(lead.get("hold_reason") or ""),
            hold_set_at=str(lead.get("hold_set_at") or ""),
        )
        for lead in leads if lead.get("on_hold") is True
    ]

    allowlists = {}
    for name in _ALLOWLIST_FLAGS:
        value, source = _read_env_layered(name)
        allowlists[name] = _allowlist_summary(value, source)

    return ControlsResponse(
        generated_at=_now().isoformat(),
        kill_switch_engaged=kill_switch_engaged,
        kill_switch_set_at=kill_switch_set_at,
        kernel_flags=_flag_values(_KERNEL_FLAGS),
        kernel_allowlists=allowlists,
        containment_flags=_flag_values(_CONTAINMENT_FLAGS),
        budget_flags=_flag_values(_BUDGET_FLAGS),
        conversations=conversations,
        conversations_available=conversations_available,
        suppressed_count=sum(1 for c in conversations if c.effective_mode != "active"),
        held_leads=held,
        followups_enabled=_followups_enabled(),
        degraded=degraded,
    )


@router.get("/followups")
async def get_followups(_=Depends(require_auth)) -> FollowupsResponse:
    rows, unavailable, degraded = _read_followups()
    now_iso = _now().isoformat()
    return FollowupsResponse(
        available=unavailable is None and degraded is None,
        followups=sorted(rows, key=lambda r: r.due_at or ""),
        due_count=sum(
            1 for r in rows
            if r.status in ("", "scheduled", "due", "pending") and r.due_at and r.due_at <= now_iso
        ),
        unavailable_reason=unavailable,
        degraded=degraded,
    )


# ── Owner actions (every write goes through an allow-listed agent script) ────
def _script_result(result: Any, *, action: str, request: Request, details: dict[str, Any]) -> ActionResult:
    audit_log(
        f"catering.{action}",
        ip=client_ip(request),
        ua=client_ua(request),
        details={**details, "returncode": result.returncode},
    )
    if result.timed_out:
        raise HTTPException(status_code=504, detail=f"{action} timed out")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()[:300]
        raise HTTPException(
            status_code=409, detail=message or f"{action} failed (rc={result.returncode})",
        )
    return ActionResult(
        ok=True,
        returncode=result.returncode,
        stdout=(result.stdout or "")[:4000],
        stderr=(result.stderr or "")[:2000],
    )


def _run_or_503(binary: str, args: list[str], *, timeout: float, stdin_data: Optional[str] = None) -> Any:
    """Invoke an allow-listed agent script. A missing binary means the cockpit
    is running somewhere the agent is not deployed — a 503, not a 500."""
    try:
        return run_cli(binary, args, timeout=timeout, stdin_data=stdin_data)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=f"agent script unavailable: {e}")


@router.post("/leads/{lead_id}/decision")
async def post_decision(
    lead_id: str,
    body: DecisionBody,
    request: Request,
    _=Depends(require_fresh_otp),
) -> ActionResult:
    """Approve / reject / request-revision on a lead's owner-approval code.

    The cockpit resolves the code from the lead rather than accepting one from
    the client: a code typed into a browser is exactly the screenshot-forwarded
    code the script's role gate exists to refuse."""
    lead_id = _validated_lead_id(lead_id)
    lead, _reason = _find_lead(lead_id)
    code = str(lead.get("owner_approval_code") or "")
    if not code:
        raise HTTPException(status_code=409, detail="lead has no owner approval code")

    if body.decision == "edit" and not body.edit_text.strip():
        raise HTTPException(status_code=422, detail="edit_text required for a revision request")

    args = ["--code", code, "--decision", body.decision, "--sender-role", "owner"]
    stdin_data: Optional[str] = None
    if body.decision == "edit":
        args += ["--edit-text", body.edit_text.strip()]
    if body.reason.strip():
        args += ["--reason", body.reason.strip()]
    if body.decision == "approve":
        if body.quote_text.strip():
            args.append("--quote-text-stdin")
            stdin_data = body.quote_text
        else:
            # No owner-authored text: let the script render from the customer's
            # finalized selection instead of sending an empty quote.
            args.append("--quote-from-lead-state")
        if body.skip_finalize:
            args.append("--skip-finalize")

    result = _run_or_503(_DECISION_BIN, args, timeout=60, stdin_data=stdin_data)
    return _script_result(
        result, action="decision", request=request,
        details={"lead_id": lead_id, "decision": body.decision,
                 "quote_text_supplied": bool(stdin_data)},
    )


@router.post("/leads/{lead_id}/hold")
async def post_hold(
    lead_id: str,
    body: HoldBody,
    request: Request,
    _=Depends(require_fresh_otp),
) -> ActionResult:
    lead_id = _validated_lead_id(lead_id)
    _find_lead(lead_id)
    args = ["--lead-id", lead_id, "--on" if body.on else "--off", "--actor", "operator"]
    if body.reason.strip():
        args += ["--reason", body.reason.strip()]
    result = _run_or_503(_HOLD_BIN, args, timeout=30)
    return _script_result(
        result, action="hold", request=request,
        details={"lead_id": lead_id, "on": body.on},
    )


@router.post("/leads/{lead_id}/amend-apply")
async def post_amend_apply(
    lead_id: str,
    body: AmendApplyBody,
    request: Request,
    _=Depends(require_fresh_otp),
) -> ActionResult:
    """Materialise captured amendments / apply one qualification answer.

    Steps up to a fresh OTP like the sibling write endpoints. It mutates the lead's
    extracted facts, can transition it to AWAITING_OWNER_APPROVAL and fires both the
    owner card and a customer-facing message — strictly more impactful than
    ``/hold``, which already required the step-up."""
    lead_id = _validated_lead_id(lead_id)
    _find_lead(lead_id)
    if body.mode == "answer" and not body.answer_text.strip():
        raise HTTPException(status_code=422, detail="answer_text required in answer mode")
    args = ["--lead-id", lead_id, "--mode", body.mode]
    stdin_data: Optional[str] = None
    if body.mode == "answer":
        # Over stdin, not argv: an answer that starts with a dash is otherwise
        # parsed by the script's argparse as a flag (same reason the decision
        # endpoint pipes its quote text).
        args.append("--answer-text-stdin")
        stdin_data = body.answer_text.strip()
    result = _run_or_503(_AMEND_BIN, args, timeout=60, stdin_data=stdin_data)
    return _script_result(
        result, action="amend_apply", request=request,
        details={"lead_id": lead_id, "mode": body.mode},
    )


@router.post("/pricebook/import")
async def post_pricebook_import(
    body: PricebookImportBody,
    request: Request,
    _=Depends(require_fresh_otp),
) -> ActionResult:
    """Import a pricebook document. Validated here first so an obviously-bad
    paste fails with a field-level message instead of a subprocess exit code,
    then handed to import-catering-pricebook which owns the archive + audit."""
    from schemas import CateringPricebook, CateringPricebookStore

    document = body.document
    try:
        if "pricebook" in document:
            CateringPricebookStore.model_validate(document)
        else:
            CateringPricebook.model_validate(document)
    except Exception as e:  # noqa: BLE001 — surfaced verbatim to the operator
        raise HTTPException(status_code=422, detail=f"pricebook invalid: {str(e)[:600]}")

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="cockpit-pricebook-", delete=False, encoding="utf-8",
    )
    try:
        json.dump(document, handle)
        handle.close()
        args = [
            "--file", handle.name, "--sender-role", "owner", "--updated-by", "cockpit",
        ]
        if body.dry_run:
            args.append("--dry-run")
        result = _run_or_503(_PRICEBOOK_IMPORT_BIN, args, timeout=60)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    return _script_result(
        result, action="pricebook_import", request=request,
        details={"dry_run": body.dry_run},
    )


__all__ = ["router"]
