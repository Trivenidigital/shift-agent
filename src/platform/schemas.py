"""
Shift Agent — central Pydantic schemas.

Imported by every script. Validates on read; writes go through .model_dump().

Conventions:
- All datetimes are timezone-aware (ZoneInfo).
- All phones are E.164 canonical via E164Phone type.
- All data files (config, roster, pending, send-counter, seen-ids) have a schema here.
- decisions.log entries are a discriminated union on `type`.
- Proposals are a discriminated union on `status`; terminal statuses are enumerated.
"""

from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict, constr, model_validator, field_validator
from typing import Literal, Annotated, Union, Optional, Any
from datetime import datetime
from zoneinfo import ZoneInfo
import re


# ─────────────────────────────────────────────────────────────────
# Phone canonicalization
# ─────────────────────────────────────────────────────────────────

_PHONE_E164 = re.compile(r"^\+\d{10,15}$")


class E164Phone(str):
    """Canonical E.164 phone. Constructor handles dashed, @jid, 00-prefix variants.

    P2-FIX: was using Pydantic v1 `__get_validators__` API; switched to v2
    `__get_pydantic_core_schema__` so validators actually run. The old version
    silently passed through unvalidated strings, breaking canonicalization.
    """

    @classmethod
    def validate(cls, v: Any) -> "E164Phone":
        if isinstance(v, E164Phone):
            return v
        if not isinstance(v, str):
            raise TypeError("E164Phone requires a string")
        canonical = cls.from_any(v)
        if not _PHONE_E164.match(canonical):
            raise ValueError(f"not a valid E.164 phone: {v!r} (got canonical {canonical!r})")
        return cls(canonical)

    @classmethod
    def from_any(cls, raw: str) -> str:
        """Canonicalize: strip @jid suffix, dashes, spaces; add + if missing; convert 00- prefix."""
        if "@" in raw:
            raw = raw.split("@", 1)[0]
        s = re.sub(r"[\s\-().]", "", raw)
        if s.startswith("00"):
            s = "+" + s[2:]
        if not s.startswith("+"):
            # bare digits — assume already includes country code
            s = "+" + s
        return s

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        """Pydantic v2 integration. Ensures .validate() runs on every assignment."""
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(cls.validate)

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        return {"type": "string", "pattern": _PHONE_E164.pattern}


# ─────────────────────────────────────────────────────────────────
# Roles and employees
# ─────────────────────────────────────────────────────────────────

Role = Literal[
    "cashier", "bakery", "meat_counter", "sweets", "floor",
    "prep", "cook", "server", "dishwasher", "manager"
]

EmployeeId = Annotated[str, Field(pattern=r"^e\d{3,}$")]


class PhoneAssignment(BaseModel):
    phone: E164Phone
    effective_from: datetime
    effective_to: Optional[datetime] = None


class Employee(BaseModel):
    # extra="forbid" preserved: `lid` is now a properly-typed Optional
    # field, so older code reading newer rosters (post-rollback) only
    # rejects unknown OTHER fields — typo detection in hand-edited
    # rosters stays valuable. Rollback procedure (see RUNBOOK.md) strips
    # `lid` from roster.json BEFORE reverting schemas.py.
    model_config = ConfigDict(extra="forbid")

    id: EmployeeId
    name: str
    nickname: Optional[str] = None
    role: Role
    phone: E164Phone
    languages: list[str] = []
    can_cover_roles: list[Role] = []  # frozenset unsupported in stdlib json; dedup on load
    status: Literal["active", "inactive", "terminated"] = "active"
    phone_history: list[PhoneAssignment] = []
    restrictions: Optional[dict] = None
    # BEGIN shift-agent-sender-id
    lid: Optional[str] = Field(default=None, pattern=r"^\d{6,20}@lid$")
    # END shift-agent-sender-id

    @model_validator(mode="after")
    def dedup_can_cover(self):
        self.can_cover_roles = sorted(set(self.can_cover_roles))
        return self


class ScheduleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: EmployeeId
    shift: Annotated[str, Field(pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")]
    role: Role


class Roster(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate _meta and other future fields

    location: dict
    employees: list[Employee]
    schedule: dict[str, list[ScheduleEntry]] = {}

    @model_validator(mode="after")
    def check_referential_integrity(self):
        ids = {e.id for e in self.employees}
        for date, entries in self.schedule.items():
            for entry in entries:
                if entry.employee_id not in ids:
                    raise ValueError(
                        f"schedule[{date}] references unknown employee_id {entry.employee_id}"
                    )
        # unique ids
        if len({e.id for e in self.employees}) != len(self.employees):
            raise ValueError("duplicate employee.id in roster")
        return self

    def find_by_phone(self, phone: str, now: Optional[datetime] = None) -> Optional[Employee]:
        """P8-FIX: previous version had `or True` making effective_to check a no-op.
        Now correctly honors phone_history effective windows."""
        canonical = E164Phone.from_any(phone)
        if now is None:
            from datetime import timezone as _tz
            now = datetime.now(_tz.utc)
        for e in self.employees:
            if e.status != "active":
                continue
            if e.phone == canonical:
                return e
            for h in e.phone_history:
                if h.phone != canonical:
                    continue
                # Only match if the phone assignment was active at `now`
                if h.effective_from and h.effective_from > now:
                    continue
                if h.effective_to is not None and h.effective_to < now:
                    continue
                return e
        return None


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

class OwnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    phone: E164Phone
    self_chat_jid: str = ""  # populated on first run
    # BEGIN shift-agent-sender-id
    lid: Optional[str] = Field(default=None, pattern=r"^\d{6,20}@lid$")
    # END shift-agent-sender-id


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_outbound_per_day: int = 6
    max_outbound_per_minute: int = 30
    pending_proposal_ttl_hours: int = 4
    per_message_timeout_sec: int = 120
    send_failure_retry_count: int = 1


class AlertingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pushover_user_key: str          # REQUIRED — validated non-empty
    pushover_app_token: str         # REQUIRED — validated non-empty
    healthchecks_io_url: str = ""
    email: str = ""

    @model_validator(mode="after")
    def require_pushover(self):
        if not self.pushover_user_key or not self.pushover_app_token:
            raise ValueError(
                "pushover_user_key and pushover_app_token are REQUIRED. "
                "Out-of-band alerts cannot be optional for a customer-facing agent."
            )
        return self


class CustomerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    location_id: str
    timezone: str
    languages: list[str] = []

    @field_validator("timezone")
    @classmethod
    def valid_tz(cls, v):
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"invalid IANA timezone {v!r}: {e}")
        return v


class BackupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Email is informational only — humans use it to recall whose key. The actual
    # encryption recipient is gpg_fingerprint (full 40 hex chars). Email-based
    # recipient resolution is rogue-key vulnerable (SKS-style substitution).
    gpg_recipient_email: str
    gpg_fingerprint: str = Field(
        default="",
        pattern=r"^([0-9A-Fa-f]{40})?$",
        description="Full 40-char GPG primary-key fingerprint (uppercase or lowercase hex). Required for backups to encrypt; empty disables nightly backup.",
    )
    s3_bucket: str = ""
    retention_days: int = 30


class OperationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_hours_local: str = "08:00-22:00"


# Daily Brief sub-config + section alias (Agent #4)
BriefSection = Literal["yesterday", "today_outlook", "alerts"]


class DailyBriefConfig(BaseModel):
    """Owner-configurable morning brief settings (Agent #4)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    # Regex rejects 24:00, 25:99 etc. (the v1 plan's `^[0-2]\d:[0-5]\d$` was buggy).
    brief_time: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_words: int = Field(default=150, ge=50, le=500)
    sections: list[BriefSection] = Field(
        default_factory=lambda: ["yesterday", "today_outlook", "alerts"],
        min_length=1,
    )
    # Catch-up: if VPS was down at brief_time, fire the brief anyway up to N min later.
    # Past this window, BriefSkipped(catchup_expired) + Pushover instead of stale brief.
    catchup_window_minutes: int = Field(default=180, ge=15, le=720)

    @field_validator("brief_time")
    @classmethod
    def _validate_brief_time_strptime(cls, v: str) -> str:
        # Belt-and-suspenders — the regex catches structure; strptime catches semantics.
        from datetime import datetime as _dt
        _dt.strptime(v, "%H:%M")
        return v


# Agent #2 Catering Lead config + state-machine
CateringLeadStatus = Literal[
    "NEW",                      # raw inquiry just arrived
    "EXTRACTING",               # extractor running (LLM)
    "NOT_CATERING",             # classifier said no (terminal)
    "AWAITING_OWNER_APPROVAL",  # quote drafted; owner needs to approve
    "OWNER_APPROVED",           # owner said go
    "OWNER_EDITED",             # owner sent edits; re-draft pending
    "OWNER_REJECTED",           # owner declined (terminal)
    "SENT_TO_CUSTOMER",         # quote sent to inquirer
    "CLOSED",                   # terminal — booked or customer-declined
    "STALE",                    # terminal — silent for too long
]

CATERING_TERMINAL_STATUSES = frozenset({
    "NOT_CATERING", "OWNER_REJECTED", "CLOSED", "STALE",
})


def is_catering_terminal(status: str) -> bool:
    return status in CATERING_TERMINAL_STATUSES


class CateringConfig(BaseModel):
    """Catering Lead (Agent #2) settings."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False  # default OFF — opt-in (offshore work pending integration)
    deposit_threshold_guests: int = Field(default=50, ge=1)
    deposit_pct: float = Field(default=0.25, ge=0.0, le=1.0)
    stale_after_hours: int = Field(default=14 * 24, ge=1)  # 14 days default
    # Per-customer pricing knobs land in v0.2 (would otherwise bloat config).


class CateringLeadExtractedFields(BaseModel):
    """LLM-extracted structure from a catering inquiry. All optional — owner
    fills in gaps via edit flow."""
    model_config = ConfigDict(extra="ignore")  # extractor may emit extras
    headcount: Optional[int] = Field(default=None, ge=1, le=10000)
    event_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    event_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    menu_preferences: list[str] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)
    delivery_or_pickup: Optional[Literal["delivery", "pickup", "unknown"]] = None
    budget_hint_usd: Optional[int] = Field(default=None, ge=0)
    notes: str = ""
    # Items the customer asked about that aren't on the current menu —
    # LLM-extracted from message text. Empty list = no off-menu requests
    # detected.
    #
    # CONTRACT: this field is currently WRITE-ONLY — populated by the LLM
    # extractor but not yet rendered on the owner-approval card. The catering
    # extractor SKILL prompt + the owner-approval-card builder MUST be updated
    # together; updating only the extractor produces silent drops (owner never
    # sees what the customer asked for). Verify both sides ship in the same PR.
    off_menu_items: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=20)


class CateringLead(BaseModel):
    """One catering lead — full lifecycle from inquiry to closure."""
    model_config = ConfigDict(extra="forbid")
    lead_id: str = Field(min_length=1)
    status: CateringLeadStatus
    customer_phone: E164Phone
    customer_name: Optional[str] = None
    raw_inquiry: str
    original_message_id: str = Field(min_length=1)  # idempotency key (Meta msg id)
    created_at: datetime
    updated_at: datetime
    extracted: CateringLeadExtractedFields = Field(default_factory=CateringLeadExtractedFields)
    quote_text: str = ""                    # drafted by Sonnet/Kimi in v0.2
    quote_version: int = Field(default=0, ge=0)
    owner_approval_code: Optional[ProposalCode] = None  # reuses Shift's pattern
    customer_replied: bool = False


class CateringLeadStore(BaseModel):
    """Per-customer catering leads (lives at /opt/shift-agent/state/catering-leads.json)."""
    model_config = ConfigDict(extra="forbid")
    leads: list[CateringLead] = Field(default_factory=list)


# Catering menu (Agent #2 v0.2 — photo-upload menu management)
DietaryTag = Literal[
    "veg", "non-veg", "vegan", "jain", "halal", "kosher",
    "gluten-free", "nut-free", "dairy-free", "egg-free", "spicy",
]
MenuCategory = Literal[
    "appetizer", "soup", "salad", "main", "side",
    "dessert", "beverage", "special", "package",
]


class MenuItem(BaseModel):
    """One item in the catering menu."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    price_usd: Optional[float] = Field(default=None, ge=0, le=10000)
    category: MenuCategory = "main"
    dietary_tags: list[DietaryTag] = Field(default_factory=list)
    available: bool = True
    notes: str = Field(default="", max_length=500)
    serves: Optional[int] = Field(default=None, ge=1, le=1000,
                                  description="Approx servings per unit (e.g. 'tray serves 10')")


class Menu(BaseModel):
    """The current menu — single source of truth, replaced on each update."""
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)
    updated_at: datetime
    updated_by: str = Field(default="", max_length=200,
                            description="Owner phone or 'photo-ocr' or 'manual'")
    source_image_id: Optional[str] = Field(default=None, max_length=200,
                                           description="WhatsApp message id of the source photo, if from photo-OCR")
    items: list[MenuItem] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000,
                       description="Catering-specific terms (delivery zone, lead time, etc.)")


class MenuPendingUpdate(BaseModel):
    """A proposed menu update awaiting owner confirmation."""
    model_config = ConfigDict(extra="forbid")
    update_id: str = Field(min_length=1, max_length=64)
    proposed_at: datetime
    source_image_id: Optional[str] = None
    extracted_items: list[MenuItem]
    confirmation_code: str = Field(pattern=r"^#[A-HJ-NP-Z2-9]{5}$",
                                   description="reuses Shift's #X9X9X code alphabet")
    parser_notes: str = Field(default="", max_length=2000)


# Agent #3 Multi-Location Coordinator config
class LocationEntry(BaseModel):
    """One location in a multi-location operator config (Agent #3).

    For single-location customers this is unused — Customer.location_id is
    the canonical id and CustomerConfig holds the timezone. Multi-location
    customers (e.g. Triveni's 9 locations TX/MD/NC/SC/OH/VA) populate this
    list and Agent #3 routes queries by location_id.
    """
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)              # canonical id (e.g. "loc_jax_01")
    name: str = Field(min_length=1)            # owner-friendly ("Jacksonville")
    timezone: str                              # IANA tz; may differ across locations
    owner_jid: str = ""                        # optional per-location owner (defaults to global)
    address_short: str = ""                    # e.g. "Jacksonville, FL"

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"invalid IANA timezone {v!r}: {e}")
        return v


class MultiLocationConfig(BaseModel):
    """Multi-location coordinator settings (Agent #3).

    v0.1: schema scaffolding + cross-location query routing.
    v0.2: inter-location coverage transfers, consolidated briefs.
    Single-location customers leave `locations: []` — Agent #3 self-disables.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    locations: list[LocationEntry] = Field(default_factory=list)
    require_owner_approval_for_transfers: bool = True

    @field_validator("locations")
    @classmethod
    def _unique_ids(cls, v: list[LocationEntry]) -> list[LocationEntry]:
        ids = [loc.id for loc in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate location ids in multi_location.locations: {ids}")
        return v


# ─────────────────────────────────────────────────────────────────
# Tier 2 agents — schemas-only scaffolding (v0.1)
# ─────────────────────────────────────────────────────────────────
# Per portfolio.md.txt, Tier 2 = "build after Tier 1 has paying customers".
# We ship schemas + SKILL stubs now so customers can opt-in agent-by-agent
# as their needs solidify. Full implementations land in v0.2 per-agent
# triggered by pilot customer onboarding.

# Agent #6 — Inventory Tracker (High complexity; needs POS integration)
class InventoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    low_stock_threshold_days: int = Field(default=7, ge=1)  # alert when fewer than N days of supply
    expiry_warning_days: int = Field(default=3, ge=1)


# Agent #7 — Supplier Coordination (Medium)
class SupplierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    follow_up_after_hours: int = Field(default=24, ge=1)  # chase orders past expected delivery
    require_owner_approval_for_outbound: bool = True


# Agent #9 — VIP Customer (Medium)
class VipConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    min_orders_for_vip: int = Field(default=10, ge=1)
    at_risk_silent_days: int = Field(default=60, ge=1)  # flag VIP after N silent days


# Agent #10 — Catering Follow-up (Low-Medium; depends on Agent #2)
class CateringFollowupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    thank_you_delay_hours: int = Field(default=24, ge=1)
    feedback_request_delay_hours: int = Field(default=48, ge=1)
    anniversary_nudge_days_before: int = Field(default=14, ge=1)


# Agent #12 — Hiring & Onboarding (Medium)
class HiringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    paperwork_overdue_days: int = Field(default=7, ge=1)


# Agent #13 — Compliance Calendar (Low-Medium; calendar-only logic)
class ComplianceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    advance_warning_days: list[int] = Field(default_factory=lambda: [30, 14, 7, 3, 1])

    @field_validator("advance_warning_days")
    @classmethod
    def _sorted_unique_positive(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("advance_warning_days must not be empty")
        if any(d <= 0 for d in v):
            raise ValueError("advance_warning_days values must be positive")
        return sorted(set(v), reverse=True)


# Agent #14 — Employee Document Tracker (Low; pure date logic)
class EmployeeDocsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    advance_warning_days: list[int] = Field(default_factory=lambda: [90, 60, 30, 14])

    @field_validator("advance_warning_days")
    @classmethod
    def _sorted_unique_positive(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("advance_warning_days must not be empty")
        if any(d <= 0 for d in v):
            raise ValueError("advance_warning_days values must be positive")
        return sorted(set(v), reverse=True)


# Agent #15 — Cash & AR (Medium; invoice tracking)
class CashArConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    reminder_cadence_days: list[int] = Field(default_factory=lambda: [7, 14, 30, 45])  # days overdue
    escalate_threshold_days: int = Field(default=60, ge=1)
    require_owner_approval_for_outbound: bool = True


# Agent #16 — Sales Tax Filing (Medium-High; per-state rules)
class SalesTaxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    advance_warning_days: list[int] = Field(default_factory=lambda: [14, 7, 3, 1])

    @field_validator("advance_warning_days")
    @classmethod
    def _sorted_unique_positive(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("advance_warning_days must not be empty")
        if any(d <= 0 for d in v):
            raise ValueError("advance_warning_days values must be positive")
        return sorted(set(v), reverse=True)


# Agent #5 EOD Reconciliation config
class EodConfig(BaseModel):
    """End-of-day reconciliation snapshot settings (Agent #5)."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    eod_time: str = Field(default="22:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    catchup_window_minutes: int = Field(default=120, ge=15, le=720)
    pushover_priority: int = Field(default=0, ge=-2, le=2)
    pushover_only_if_unresolved: bool = True

    @field_validator("eod_time")
    @classmethod
    def _validate_eod_time(cls, v: str) -> str:
        from datetime import datetime as _dt
        _dt.strptime(v, "%H:%M")
        return v


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    customer: CustomerConfig
    owner: OwnerConfig
    limits: LimitsConfig
    alerting: AlertingConfig
    backup: BackupConfig
    operations: OperationsConfig = OperationsConfig()
    daily_brief: DailyBriefConfig = Field(default_factory=DailyBriefConfig)
    eod: EodConfig = Field(default_factory=EodConfig)
    multi_location: MultiLocationConfig = Field(default_factory=MultiLocationConfig)
    catering: CateringConfig = Field(default_factory=CateringConfig)
    # Tier 2 agents (all default enabled=False; opt-in per customer)
    inventory: InventoryConfig = Field(default_factory=InventoryConfig)
    supplier: SupplierConfig = Field(default_factory=SupplierConfig)
    vip: VipConfig = Field(default_factory=VipConfig)
    catering_followup: CateringFollowupConfig = Field(default_factory=CateringFollowupConfig)
    hiring: HiringConfig = Field(default_factory=HiringConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    employee_docs: EmployeeDocsConfig = Field(default_factory=EmployeeDocsConfig)
    cash_ar: CashArConfig = Field(default_factory=CashArConfig)
    sales_tax: SalesTaxConfig = Field(default_factory=SalesTaxConfig)

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.customer.timezone)


# ─────────────────────────────────────────────────────────────────
# Proposal (discriminated union on status)
# ─────────────────────────────────────────────────────────────────

ProposalId = Annotated[str, Field(pattern=r"^P\d{4,}$")]
# 5-char code, uppercase alphanumeric, excluding visually ambiguous 0/O/1/I/L.
# Test-suite caught drift: previous regex `[A-HJ-NPR-Z2-9]` included L (inside J-N)
# AND excluded Q; generator alphabet is `ABCDEFGHJKMNPQRSTUVWXYZ23456789`
# (31 chars excluding I/L/O/0/1). Regex now matches generator exactly.
ProposalCode = Annotated[str, Field(pattern=r"^#[A-HJKMNPQR-Z2-9]{5}$")]


class _BaseProp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: ProposalId
    code: ProposalCode
    created_ts: datetime
    last_updated_ts: datetime
    absent_employee_id: EmployeeId
    absent_date: str
    absent_shift: str
    absent_role: Role
    absent_reason: str
    input_message: Annotated[str, Field(max_length=4000)]
    message_id: str
    candidate_employee_id: Optional[EmployeeId] = None
    candidate_name: Optional[str] = None
    proposed_message_rendered: Optional[str] = None
    status_history: list[dict] = []  # {from, to, ts, cause, actor, event_ref}


class AwaitingProposal(_BaseProp):
    status: Literal["awaiting_owner_approval"]


class ApprovedProposal(_BaseProp):
    status: Literal["approved"]
    approved_ts: datetime
    owner_input: str


class ReconcilingProposal(_BaseProp):
    """Transient state held by send-coverage-message during the POST."""
    status: Literal["reconciling"]
    reconciling_started_ts: datetime
    reconciling_pid: int


class SentProposal(_BaseProp):
    status: Literal["sent"]
    sent_ts: datetime
    outbound_message_id: Optional[str] = None


class SendFailedProposal(_BaseProp):
    status: Literal["send_failed"]
    last_error: str
    retry_count: int
    failed_ts: datetime


class AcceptedProposal(_BaseProp):
    status: Literal["accepted"]
    response_ts: datetime
    response_message: str


class DeclinedProposal(_BaseProp):
    status: Literal["declined"]
    response_ts: datetime
    response_message: str


class DeniedByOwnerProposal(_BaseProp):
    status: Literal["denied_by_owner"]
    denied_ts: datetime
    owner_input: str


class ExpiredProposal(_BaseProp):
    status: Literal["expired"]
    expired_ts: datetime


class CancelledProposal(_BaseProp):
    status: Literal["cancelled"]
    cancelled_ts: datetime
    cancel_reason: str


class NoResponseTimeoutProposal(_BaseProp):
    status: Literal["no_response_timeout"]
    timeout_ts: datetime


Proposal = Annotated[
    Union[
        AwaitingProposal, ApprovedProposal, ReconcilingProposal, SentProposal,
        SendFailedProposal, AcceptedProposal, DeclinedProposal, DeniedByOwnerProposal,
        ExpiredProposal, CancelledProposal, NoResponseTimeoutProposal,
    ],
    Field(discriminator="status"),
]

TERMINAL_STATUSES = frozenset({
    "accepted", "declined", "denied_by_owner", "expired", "cancelled", "no_response_timeout"
})

LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "awaiting_owner_approval": frozenset({"approved", "denied_by_owner", "expired", "cancelled"}),
    "approved": frozenset({"reconciling", "cancelled"}),
    # Test-suite caught bug: `cancelled` was missing from reconciling's allowed set
    # → owner couldn't CANCEL a proposal mid-send. Now included.
    "reconciling": frozenset({"sent", "send_failed", "approved", "cancelled"}),
    "sent": frozenset({"accepted", "declined", "no_response_timeout", "cancelled"}),
    "send_failed": frozenset({"approved", "cancelled"}),  # owner RETRY → approved
    # terminal states have no outgoing transitions
    "accepted": frozenset(),
    "declined": frozenset(),
    "denied_by_owner": frozenset(),
    "expired": frozenset(),
    "cancelled": frozenset(),
    "no_response_timeout": frozenset(),
}


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_legal_transition(from_status: str, to_status: str) -> bool:
    return to_status in LEGAL_TRANSITIONS.get(from_status, frozenset())


class PendingStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposals: dict[str, Proposal] = {}
    next_proposal_seq: int = 1


# ─────────────────────────────────────────────────────────────────
# send-counter, seen-ids
# ─────────────────────────────────────────────────────────────────

class SendCounter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: str  # YYYY-MM-DD in customer tz
    count: int = 0
    last_send_ts: Optional[datetime] = None


class SeenIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seen_message_ids: list[str] = []
    max_size: int = 10000
    last_offset_bytes: int = 0
    agent_log_inode: int = 0

    def remember(self, mid: str) -> None:
        if mid in self.seen_message_ids:
            return
        self.seen_message_ids.append(mid)
        if len(self.seen_message_ids) > self.max_size:
            # drop oldest half
            self.seen_message_ids = self.seen_message_ids[self.max_size // 2:]

    def has(self, mid: str) -> bool:
        return mid in self.seen_message_ids


# ─────────────────────────────────────────────────────────────────
# decisions.log entries (discriminated union on type)
# ─────────────────────────────────────────────────────────────────

class _BaseEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ts: datetime


class RawInbound(_BaseEntry):
    type: Literal["raw_inbound"]
    message_id: str
    # BEGIN shift-agent-sender-id (was: sender_phone required E164Phone;
    # added sender_lid Optional[str]; at-least-one model_validator)
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, pattern=r"^\d{6,20}@lid$")
    # END shift-agent-sender-id
    employee_id: Optional[EmployeeId] = None
    input_message: str

    # BEGIN shift-agent-sender-id
    @model_validator(mode="after")
    def _at_least_one_sender_id(self):
        if not self.sender_phone and not self.sender_lid:
            raise ValueError("RawInbound: at least one of sender_phone, sender_lid required")
        return self
    # END shift-agent-sender-id


class ProposalCreated(_BaseEntry):
    type: Literal["proposal_created"]
    proposal_id: ProposalId
    code: ProposalCode
    absent_employee_id: EmployeeId
    candidate_employee_id: Optional[EmployeeId] = None


class ProposalStatusChange(_BaseEntry):
    type: Literal["proposal_status_change"]
    proposal_id: ProposalId
    from_status: str
    to_status: str
    cause: str
    actor: Literal["owner", "agent", "timer", "reconciler", "fsck", "candidate"]


class OutboundAttempted(_BaseEntry):
    """Written BEFORE the POST to /send. Used by reconciler to detect "attempted but not confirmed"."""
    type: Literal["outbound_attempted"]
    proposal_id: ProposalId
    recipient_employee_id: EmployeeId
    attempt_id: str


class OutboundSent(_BaseEntry):
    type: Literal["outbound_sent"]
    proposal_id: ProposalId
    recipient_employee_id: EmployeeId
    outbound_message_id: str
    rendered: str


class OutboundSendFailed(_BaseEntry):
    type: Literal["outbound_send_failed"]
    proposal_id: ProposalId
    recipient_employee_id: EmployeeId
    error: str
    retry_count: int


class OutboundResponse(_BaseEntry):
    type: Literal["outbound_response"]
    proposal_id: ProposalId
    from_employee_id: EmployeeId
    response: Literal["yes", "no", "unknown"]
    response_message: str


class OutboundCapExceeded(_BaseEntry):
    type: Literal["outbound_cap_exceeded"]
    proposal_id: ProposalId
    reason: str


class OutboundRefusedDisabled(_BaseEntry):
    type: Literal["outbound_refused_disabled"]
    proposal_id: ProposalId


class AgentStateChange(_BaseEntry):
    type: Literal["agent_state_change"]
    to_state: Literal["enabled", "disabled"]
    reason: str


class UnknownSenderDeclined(_BaseEntry):
    type: Literal["unknown_sender_declined"]
    # BEGIN shift-agent-sender-id (was: sender_phone required E164Phone)
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, pattern=r"^\d{6,20}@lid$")
    # END shift-agent-sender-id
    input_message_truncated: str

    # BEGIN shift-agent-sender-id
    @model_validator(mode="after")
    def _at_least_one_sender_id(self):
        if not self.sender_phone and not self.sender_lid:
            raise ValueError("UnknownSenderDeclined: at least one of sender_phone, sender_lid required")
        return self
    # END shift-agent-sender-id


class DispatcherRouted(_BaseEntry):
    """Audit log: dispatch_shift_agent SKILL classified an inbound message and
    handed it to a downstream handler. Lets us measure routing-reliability
    drift over time without parsing Hermes JSONL transcripts.

    Written by the dispatcher SKILL via /usr/local/bin/log-decision-direct
    immediately after the routing decision, BEFORE delegating. If a raw_inbound
    has no matching dispatcher_routed entry within ~10s, that's itself the
    signal that Kimi skipped the dispatcher and pattern-matched directly.
    """
    type: Literal["dispatcher_routed"]
    message_id: str = Field(min_length=1)
    sender_role: Literal["owner", "employee", "unknown", "error"]
    message_shape: Literal[
        "text",                # plain text message
        "approval_code",       # 5-char #XXXXX, optionally with verb (yes/no/approve/deny/retry/cancel)
        "image_only",          # image attachment with no caption
        "image_with_caption",  # image attachment with text caption
        "media_other",         # audio / document / video / sticker
    ]
    routed_to_skill: str = Field(min_length=1, max_length=64)
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, pattern=r"^\d{6,20}@lid$")


# BEGIN shift-agent-sender-id
class LidLearned(_BaseEntry):
    """Audit-log entry: a phone↔LID mapping was newly learned or updated.
    Written by shift-agent-lid-learn cron after applying lid-cache.json
    pairs to roster.json or config.yaml."""
    type: Literal["lid_learned"]
    target: Literal["owner", "employee"]
    phone: E164Phone
    employee_id: Optional[EmployeeId] = None  # set when target == "employee"
    old_lid: Optional[str] = Field(default=None, pattern=r"^\d{6,20}@lid$")
    new_lid: str = Field(pattern=r"^\d{6,20}@lid$")

    @model_validator(mode="after")
    def _target_employee_id_consistency(self):
        if self.target == "employee" and not self.employee_id:
            raise ValueError("LidLearned: target='employee' requires employee_id")
        if self.target == "owner" and self.employee_id:
            raise ValueError("LidLearned: target='owner' must NOT carry employee_id")
        if self.old_lid is not None and self.old_lid == self.new_lid:
            raise ValueError("LidLearned: old_lid == new_lid (no learning happened)")
        return self
# END shift-agent-sender-id


class InvariantViolation(_BaseEntry):
    type: Literal["invariant_violation"]
    check: str
    detail: str


class HealthCheckFailure(_BaseEntry):
    type: Literal["health_check_failure"]
    check: str
    detail: str


# ─────────────────────────────────────────────────────────────────
# Daily Brief log entries (Agent #4)
# ─────────────────────────────────────────────────────────────────

# brief_date is YYYY-MM-DD in customer tz (matches SendCounter.day shape).
_BRIEF_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


class BriefAttempted(_BaseEntry):
    """Written BEFORE bridge POST. Idempotency anchor — mirrors OutboundAttempted.

    On crash between bridge_post and BriefSent append, the next run sees this
    entry within the last 30 min and refuses to auto-resend (operator must
    verify manually via WhatsApp + Pushover alert).
    """
    type: Literal["brief_attempted"]
    brief_date: str = Field(pattern=_BRIEF_DATE_RE)
    attempt_id: str = Field(min_length=1)         # uuid4 per attempt
    word_count: int = Field(ge=0)
    sections_included: list[BriefSection]
    source_count: int = Field(ge=0)               # number of LogSource entries scanned
    degraded_mode: bool = False                   # any data source unavailable?
    catchup_minutes_late: int = Field(default=0, ge=0)


class BriefSent(_BaseEntry):
    """Written AFTER bridge 200 + non-empty messageId."""
    type: Literal["brief_sent"]
    brief_date: str = Field(pattern=_BRIEF_DATE_RE)
    attempt_id: str = Field(min_length=1)         # links to BriefAttempted
    outbound_message_id: str = Field(min_length=1)
    self_chat_jid: str = Field(min_length=1)


class BriefSendFailed(_BaseEntry):
    """Bridge unreachable or returned non-2xx after retry."""
    type: Literal["brief_send_failed"]
    brief_date: str = Field(pattern=_BRIEF_DATE_RE)
    attempt_id: str = Field(min_length=1)
    error: str                                    # no length cap (matches OutboundSendFailed)
    retry_count: int = Field(ge=0)


class BriefSkipped(_BaseEntry):
    """Brief intentionally not sent. NOTE: outside_window fires aren't logged
    (would generate 95+ noise entries/day); script exits 0 silently for those.
    NOTE: no_activity removed — we always send a 'quiet day' brief instead."""
    type: Literal["brief_skipped"]
    brief_date: str = Field(pattern=_BRIEF_DATE_RE)
    reason: Literal[
        "already_sent",
        "data_unavailable",
        "disabled",
        "catchup_expired",
        "dependency_down",
        "send_uncertain",  # crash between send and log; manual verification needed
    ]


# ─────────────────────────────────────────────────────────────────
# Catering menu log entries (Agent #2 v0.2 — photo-upload UX)
# ─────────────────────────────────────────────────────────────────


class MenuUpdateProposed(_BaseEntry):
    """Owner uploaded a menu photo; vision parser extracted items; preview
    sent to owner self-chat awaiting confirmation."""
    type: Literal["menu_update_proposed"]
    update_id: str = Field(min_length=1)
    confirmation_code: str = Field(pattern=r"^#[A-HJ-NP-Z2-9]{5}$")
    item_count: int = Field(ge=0)
    source_image_id: Optional[str] = None


class MenuUpdateApplied(_BaseEntry):
    """Owner approved the proposed update; catering-menu.json replaced."""
    type: Literal["menu_update_applied"]
    update_id: str = Field(min_length=1)
    new_version: int = Field(ge=1)
    item_count: int = Field(ge=0)
    prev_version: int = Field(ge=0,
                              description="0 if no prior menu existed")


class MenuUpdateRejected(_BaseEntry):
    """Owner declined or ignored the proposed update; pending file cleared."""
    type: Literal["menu_update_rejected"]
    update_id: str = Field(min_length=1)
    reason: Literal["owner_no", "owner_edit_aborted", "ttl_expired"]


# ─────────────────────────────────────────────────────────────────
# Catering Lead log entries (Agent #2)
# ─────────────────────────────────────────────────────────────────


class CateringLeadCreated(_BaseEntry):
    type: Literal["catering_lead_created"]
    lead_id: str = Field(min_length=1)
    customer_phone: E164Phone
    original_message_id: str = Field(min_length=1)


class CateringLeadStatusChange(_BaseEntry):
    type: Literal["catering_lead_status_change"]
    lead_id: str = Field(min_length=1)
    from_status: CateringLeadStatus
    to_status: CateringLeadStatus
    actor: Literal["system", "owner", "customer"]
    reason: str = ""


class CateringLeadRejected(_BaseEntry):
    """Lead creation rejected pre-state (no state mutation, no lead row).

    Intentionally has no lead_id — rejection happens before mint, so no lead
    exists. customer_tz + event_date are carried so the audit entry is
    self-describing (operator triaging a rejection doesn't need to JOIN against
    catering-leads.json or config.yaml at query time).

    Reasons are pinned by the discriminated `reason` field. Adding a new reason
    requires updating BOTH this Literal AND the REASON_TO_ERR_PREFIX dict in
    create-catering-lead — kept tight on purpose so future drift is loud.
    """
    type: Literal["catering_lead_rejected"]
    customer_phone: E164Phone
    original_message_id: str = Field(min_length=1)
    reason: Literal[
        "event_date_past",
        "event_date_invalid_calendar",
        "timezone_invalid",
    ]
    detail: str = Field(default="", max_length=500)
    customer_tz: str = Field(default="", max_length=64)
    event_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class CateringQuoteDrafted(_BaseEntry):
    type: Literal["catering_quote_drafted"]
    lead_id: str = Field(min_length=1)
    quote_version: int = Field(ge=0)
    word_count: int = Field(ge=0)


class CateringOwnerApprovalRequested(_BaseEntry):
    type: Literal["catering_owner_approval_requested"]
    lead_id: str = Field(min_length=1)
    approval_code: str = Field(min_length=1)


class CateringOwnerDecision(_BaseEntry):
    type: Literal["catering_owner_decision"]
    lead_id: str = Field(min_length=1)
    decision: Literal["approve", "reject", "edit"]
    edit_text: str = ""


class CateringQuoteSent(_BaseEntry):
    type: Literal["catering_quote_sent"]
    lead_id: str = Field(min_length=1)
    customer_phone: E164Phone
    outbound_message_id: str = Field(min_length=1)


# ─────────────────────────────────────────────────────────────────
# Multi-Location Coordinator log entries (Agent #3)
# ─────────────────────────────────────────────────────────────────


class CrossLocationQuery(_BaseEntry):
    """Owner asked a cross-location question (e.g. 'who's at Houston tomorrow?')."""
    type: Literal["cross_location_query"]
    query_id: str = Field(min_length=1)
    raw_query: str
    location_ids_resolved: list[str]
    answer_summary: str = ""


class InterLocationTransferProposed(_BaseEntry):
    """Agent #3 proposed an employee transfer between locations to cover a gap."""
    type: Literal["inter_location_transfer_proposed"]
    transfer_id: str = Field(min_length=1)
    from_location_id: str
    to_location_id: str
    employee_id: str
    proposed_date: str = Field(pattern=_BRIEF_DATE_RE)
    reason: str = ""
    requires_owner_approval: bool = True


# ─────────────────────────────────────────────────────────────────
# EOD Reconciliation log entries (Agent #5)
# ─────────────────────────────────────────────────────────────────


class EodSnapshot(_BaseEntry):
    """End-of-day snapshot — written when EOD agent completes today's
    reconciliation. Daily Brief consumes this snapshot tomorrow morning.
    """
    type: Literal["eod_snapshot"]
    eod_date: str = Field(pattern=_BRIEF_DATE_RE)
    snapshot_id: str = Field(min_length=1)
    sick_calls: int = Field(ge=0)
    proposals_created: int = Field(ge=0)
    proposals_resolved: int = Field(ge=0)        # accepted + declined + denied + expired + cancelled
    proposals_unresolved: int = Field(ge=0)      # awaiting + approved + reconciling + sent + send_failed
    outbound_sent: int = Field(ge=0)
    outbound_send_failed: int = Field(ge=0)
    invariant_violations: int = Field(ge=0)


class EodPushoverSent(_BaseEntry):
    """EOD agent sent a Pushover summary to owner."""
    type: Literal["eod_pushover_sent"]
    eod_date: str = Field(pattern=_BRIEF_DATE_RE)
    snapshot_id: str = Field(min_length=1)
    unresolved_count: int = Field(ge=0)
    pushover_priority: int = Field(ge=-2, le=2)


class EodSkipped(_BaseEntry):
    """EOD reconciliation was skipped."""
    type: Literal["eod_skipped"]
    eod_date: str = Field(pattern=_BRIEF_DATE_RE)
    reason: Literal[
        "already_done",
        "disabled",
        "catchup_expired",
        "data_unavailable",
    ]


LogEntry = Annotated[
    Union[
        RawInbound, ProposalCreated, ProposalStatusChange,
        OutboundAttempted, OutboundSent, OutboundSendFailed,
        OutboundResponse, OutboundCapExceeded, OutboundRefusedDisabled,
        AgentStateChange, UnknownSenderDeclined, InvariantViolation,
        HealthCheckFailure,
        # BEGIN shift-agent-sender-id
        LidLearned,
        # END shift-agent-sender-id
        # Dispatcher routing audit (added with Fix 2 of dispatcher-routing-fixes)
        DispatcherRouted,
        # Agent #4 Daily Brief
        BriefAttempted, BriefSent, BriefSendFailed, BriefSkipped,
        # Agent #5 EOD Reconciliation
        EodSnapshot, EodPushoverSent, EodSkipped,
        # Agent #3 Multi-Location Coordinator
        CrossLocationQuery, InterLocationTransferProposed,
        # Agent #2 Catering Lead
        CateringLeadCreated, CateringLeadStatusChange, CateringLeadRejected,
        CateringQuoteDrafted,
        CateringOwnerApprovalRequested, CateringOwnerDecision, CateringQuoteSent,
        MenuUpdateProposed, MenuUpdateApplied, MenuUpdateRejected,
    ],
    Field(discriminator="type"),
]


__all__ = [
    "E164Phone", "Role", "EmployeeId", "Employee", "PhoneAssignment", "ScheduleEntry", "Roster",
    "Config", "CustomerConfig", "OwnerConfig", "LimitsConfig", "AlertingConfig", "BackupConfig", "OperationsConfig",
    "DailyBriefConfig", "BriefSection",
    "EodConfig",
    "LocationEntry", "MultiLocationConfig",
    "CateringConfig", "CateringLeadStatus", "CateringLeadExtractedFields",
    "CateringLead", "CateringLeadStore",
    "is_catering_terminal", "CATERING_TERMINAL_STATUSES",
    "MenuItem", "Menu", "MenuPendingUpdate", "DietaryTag", "MenuCategory",
    # Tier 2 configs
    "InventoryConfig", "SupplierConfig", "VipConfig", "CateringFollowupConfig",
    "HiringConfig", "ComplianceConfig", "EmployeeDocsConfig", "CashArConfig", "SalesTaxConfig",
    "Proposal", "ProposalId", "ProposalCode",
    "AwaitingProposal", "ApprovedProposal", "ReconcilingProposal", "SentProposal",
    "SendFailedProposal", "AcceptedProposal", "DeclinedProposal", "DeniedByOwnerProposal",
    "ExpiredProposal", "CancelledProposal", "NoResponseTimeoutProposal",
    "TERMINAL_STATUSES", "LEGAL_TRANSITIONS", "is_terminal_status", "is_legal_transition",
    "PendingStore", "SendCounter", "SeenIds",
    "LogEntry", "RawInbound", "ProposalCreated", "ProposalStatusChange",
    "OutboundAttempted", "OutboundSent", "OutboundSendFailed",
    "OutboundResponse", "OutboundCapExceeded", "OutboundRefusedDisabled",
    "AgentStateChange", "UnknownSenderDeclined", "InvariantViolation", "HealthCheckFailure",
    "LidLearned", "DispatcherRouted",
    "BriefAttempted", "BriefSent", "BriefSendFailed", "BriefSkipped",
    "EodSnapshot", "EodPushoverSent", "EodSkipped",
    "CrossLocationQuery", "InterLocationTransferProposed",
    "CateringLeadCreated", "CateringLeadStatusChange", "CateringLeadRejected", "CateringQuoteDrafted",
    "CateringOwnerApprovalRequested", "CateringOwnerDecision", "CateringQuoteSent",
    "MenuUpdateProposed", "MenuUpdateApplied", "MenuUpdateRejected",
]
