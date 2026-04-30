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
from pydantic import (
    BaseModel, Field, ConfigDict, constr, model_validator, field_validator,
    Tag, Discriminator,
)
from typing import Literal, Annotated, Union, Optional, Any, get_args
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import re
import sys

# v0.3 catering hardening — single source of truth for code-generation alphabet.
# Excludes I, O, 0, 1, L (visually confusing chars). Used by ProposalCode,
# MenuPendingUpdate.confirmation_code, and runtime code generators.
_CODE_BODY_PATTERN = r"[A-HJKMNPQR-Z2-9]{5}"
_CODE_FULL_PATTERN = rf"^#{_CODE_BODY_PATTERN}$"

# ─────────────────────────────────────────────────────────────────
# Lifecycle sentinels — quote_text / edit_text by stage (review M1)
# ─────────────────────────────────────────────────────────────────
# All sentinels share the "<...-v0.3-...>" shape: searchable in audit logs,
# unmistakable for real owner content, and grep-friendly. Defined at module
# scope (NOT class-level) to avoid Pydantic v2's ModelPrivateAttr treatment
# of leading-underscore class attributes.
#
# Lifecycle of CateringLead.quote_text:
#   1. create-catering-lead mints lead with quote_text=PRE_QUOTE_DRAFT_SENTINEL
#      (extractor stage doesn't draft; S1 invariant requires non-empty)
#   2. apply-catering-owner-decision approve flow renders the real quote and
#      overwrites quote_text via model_copy (Q1 fix — Commit 3a)
#   3. Legacy pre-v0.3 leads with empty quote_text are backfilled on READ
#      by CateringLead's mode="before" shim with LEGACY_QUOTE_TEXT_SENTINEL.
#      The shim is a safety net; tools/catering-state-migrate.py is the
#      proper pre-deploy fix.
#
# Lifecycle of CateringOwnerDecision.edit_text:
#   1. New decisions with decision="edit" require non-empty edit_text
#      (mode="after" strict validator)
#   2. Legacy pre-v0.3 audit entries with decision="edit" + empty edit_text
#      are backfilled on READ with LEGACY_EDIT_TEXT_SENTINEL.
PRE_QUOTE_DRAFT_SENTINEL = "<v0.3-pre-quote-draft>"
LEGACY_QUOTE_TEXT_SENTINEL = "<legacy-pre-v0.3-no-quote-persisted>"
LEGACY_EDIT_TEXT_SENTINEL = "<legacy-pre-v0.3-no-edit-text-recorded>"

# PR-D3: forward-compat absorption of v0.4 PR-B reserved keys.
# atomic_write_json round-trips full models via model_dump_json() (no
# exclude_defaults), so a future PR-B1+ binary that defaults voice_quality /
# quote_source / tone_profile / tone_examples will materialize them on every
# store write. On rollback to PR-D3-line, extra="forbid" on CateringLead /
# CustomerConfig would crash reads. The mode='before' validators below
# strip these keys silently (after a one-shot WARN per key per process)
# so PR-D3 binaries remain readers of PR-B1+ writes.
_PR_B_RESERVED_LEAD_KEYS = frozenset({"voice_quality", "quote_source"})
_PR_B_RESERVED_CONFIG_KEYS = frozenset({"tone_profile", "tone_examples"})

# Process-local memo: warn once per (model, key) pair to avoid log spam
# during the rollback window. Subsequent strips are silent.
_PR_B_WARNED: set[tuple[str, str]] = set()


def _warn_pr_b_reserved_key_once(model_name: str, key: str) -> None:
    pair = (model_name, key)
    if pair in _PR_B_WARNED:
        return
    _PR_B_WARNED.add(pair)
    sys.stderr.write(
        f"WARN: PR-D3 absorbing-shim stripped {key!r} from {model_name} on read "
        f"(rollback window from PR-B1+ to PR-D3). Once-per-process; subsequent "
        f"strips silent.\n"
    )


# ─────────────────────────────────────────────────────────────────
# Phone canonicalization
# ─────────────────────────────────────────────────────────────────

_PHONE_E164 = re.compile(r"^\+\d{10,15}$")

# Public alias for the E.164 regex (review M3) — avoids cross-package
# private imports (e.g. lookup-prior-leads-by-phone). Callers should use
# this constant or is_valid_e164() instead of the underscore-prefixed
# _PHONE_E164 module attribute.
PHONE_E164_PATTERN = r"^\+\d{10,15}$"


def is_valid_e164(s: Any) -> bool:
    """Public predicate — True if s is a string matching the E.164 canonical
    pattern. Safe on non-string input (returns False rather than raising)."""
    return isinstance(s, str) and bool(_PHONE_E164.match(s))


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
    def from_any(cls, raw: str, *, country_code: Optional[str] = None) -> str:
        """Canonicalize: strip @jid suffix, dashes, spaces; convert 00- prefix.

        v0.3 (PM2 / L0 fix): bare 10-digit input is no longer auto-prepended
        with `+` (the historical bug that produced `+9045551234` from US local
        input). Behavior:
          - +XXXXXXXXXX...      : passed through if matches E.164 (10-15 digits)
          - 1XXXXXXXXXX (11d)   : prepended with `+` (US 11-digit)
          - 10-15 digits        : prepended with `+` (international with code)
          - exactly 10 digits   : if country_code='US', prepend `+1`; else raise
          - `00XXX...`          : convert to `+XXX...`

        country_code: ISO-3166 alpha-2 (case-insensitive — coerced to upper).
        Currently 'US' is honored; future country codes can be added as
        customers expand.

        Always validates the result against PHONE_E164_PATTERN before
        returning (review L7). Raises ValueError on any failure mode:
          - 10-digit bare-no-country input when no country_code provided
          - non-digit input
          - canonical form fails E.164 length bounds (10-15 digits with +)
        """
        if not isinstance(raw, str):
            raise ValueError(f"phone must be str, got {type(raw).__name__}")
        if country_code is not None:
            country_code = country_code.upper()  # review L6: case-insensitive
        if "@" in raw:
            raw = raw.split("@", 1)[0]
        s = re.sub(r"[\s\-().]", "", raw)
        if s.startswith("00"):
            s = "+" + s[2:]

        if s.startswith("+"):
            canonical = s
        elif s.isdigit():
            n = len(s)
            if n == 10:
                # US local format (no country code). Default-prepend +1 if
                # cfg tells us this is a US customer; otherwise reject.
                if country_code == "US":
                    canonical = "+1" + s
                else:
                    raise ValueError(
                        f"phone {raw!r} is bare 10-digit without country "
                        f"code. Set cfg.customer.country_code='US' or "
                        f"include the country prefix."
                    )
            elif n == 11 and s.startswith("1"):
                canonical = "+" + s  # US 11-digit
            elif 10 <= n <= 15:
                canonical = "+" + s  # International with country code
            else:
                raise ValueError(
                    f"phone {raw!r} has {n} digits; E.164 requires 10-15."
                )
        else:
            raise ValueError(
                f"phone {raw!r} contains non-digit characters after canonicalization"
            )

        # Always validate the canonical form (review L7 — close fail-open hole).
        if not _PHONE_E164.match(canonical):
            raise ValueError(
                f"canonical form {canonical!r} does not match E.164 pattern "
                f"(input was {raw!r})"
            )
        return canonical

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
    # v0.3 (review L6): ISO-3166 alpha-2 country code, case-insensitive.
    # Used by E164Phone.from_any to default-prepend country prefix for
    # bare 10-digit US input. Optional — if absent, bare 10-digit phones
    # are rejected (safer than guessing). Coerced to upper-case so
    # operators can write "us" or "US" in config.yaml without confusion.
    country_code: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{2}$")

    @model_validator(mode="before")
    @classmethod
    def _strip_pr_b_reserved_keys(cls, data: Any) -> Any:
        # PR-D3 absorbing shim — see module-level docstring near
        # _PR_B_RESERVED_CONFIG_KEYS for rationale.
        # Defensive shallow copy so the caller's input dict is never
        # mutated (review #38 MEDIUM). The precedent
        # _backfill_legacy_quote_text mutates in place; we deviate here
        # because new code should be defensive even if existing code is
        # consistent in the other direction.
        if not isinstance(data, dict):
            return data
        if not any(key in data for key in _PR_B_RESERVED_CONFIG_KEYS):
            return data  # fast-path: no copy when nothing to strip
        data = dict(data)
        for key in _PR_B_RESERVED_CONFIG_KEYS:
            if key in data:
                _warn_pr_b_reserved_key_once("CustomerConfig", key)
                data.pop(key, None)
        return data

    @field_validator("country_code", mode="after")
    @classmethod
    def _country_code_uppercase(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if isinstance(v, str) else v

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


# v0.3: state-machine transition table. Single source of truth, used by
# scripts at every status change. Forbidden transitions raise via
# is_catering_transition_allowed; the schema layer does NOT enforce
# (would break replay of historic leads).
#
# Review L9: typed against the CateringLeadStatus Literal so mypy catches
# typos (a misspelled status as key OR value would be a type error).
CATERING_TRANSITIONS: dict[CateringLeadStatus, set[CateringLeadStatus]] = {
    "NEW": {"EXTRACTING", "NOT_CATERING"},
    "EXTRACTING": {"AWAITING_OWNER_APPROVAL", "NOT_CATERING"},
    "NOT_CATERING": set(),                                                # terminal
    "AWAITING_OWNER_APPROVAL": {"OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED", "STALE"},
    "OWNER_EDITED": {"AWAITING_OWNER_APPROVAL", "OWNER_REJECTED"},
    # Review L8: OWNER_APPROVED → AWAITING_OWNER_APPROVAL covers
    # retry-from-failure. If apply-script approve flow crashes after the
    # state-write but before customer-quote send (or the bridge POST
    # returns 5xx), retrying needs to re-enter AWAITING so the new approve
    # attempt can be re-evaluated. Without this, the lead is stuck in
    # OWNER_APPROVED with no quote sent, and the operator can't recover
    # without manual state surgery. The CateringQuoteAttempted idempotency
    # anchor prevents duplicate sends on legit retries.
    "OWNER_APPROVED": {"SENT_TO_CUSTOMER", "AWAITING_OWNER_APPROVAL"},
    "SENT_TO_CUSTOMER": {"CLOSED", "STALE"},
    "OWNER_REJECTED": set(),                                              # terminal
    "CLOSED": set(),                                                      # terminal
    "STALE": set(),                                                       # terminal
}


def is_catering_transition_allowed(from_s: str, to_s: str) -> bool:
    """v0.3: returns True only for allowed transitions. False for unknown
    statuses. Accepts plain str (not Literal) for runtime ergonomics —
    callers pass strings extracted from log entries and disk JSON.
    """
    return to_s in CATERING_TRANSITIONS.get(from_s, set())  # type: ignore[arg-type]


def assert_rejection_reason_complete(reason_dict: dict) -> None:
    """v0.3: at create-script main() entry, assert REASON_TO_ERR_PREFIX
    matches CateringLeadRejected.reason Literal exactly (==, not subset).
    Drift is loud rather than silent."""
    schema_reasons = set(get_args(CateringLeadRejected.model_fields["reason"].annotation))
    runtime_reasons = set(reason_dict.keys())
    if runtime_reasons != schema_reasons:
        missing_in_dict = schema_reasons - runtime_reasons
        missing_in_schema = runtime_reasons - schema_reasons
        raise RuntimeError(
            f"REASON drift: missing in dict {missing_in_dict}, "
            f"missing in schema {missing_in_schema}"
        )


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

    @field_validator("event_date")
    @classmethod
    def _validate_calendar_date(cls, v: Optional[str]) -> Optional[str]:
        """v0.3: regex passes 2026-13-99 etc.; this catches calendar-invalid dates."""
        if v is None:
            return v
        try:
            datetime.fromisoformat(v).date()
        except ValueError as e:
            raise ValueError(f"event_date must be a valid ISO date: {v!r} ({e})") from e
        return v


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

    # v0.3: post-AWAITING statuses require non-empty quote_text. Legacy data
    # (pre-v0.3 leads with empty quote_text) is backfilled with sentinel by
    # mode="before" shim, then strict validator runs. Migration tool fixes
    # legacy leads pre-deploy; shim is a safety net.
    # NOTE: sentinel is defined at module scope (LEGACY_QUOTE_TEXT_SENTINEL)
    # to avoid Pydantic v2's ModelPrivateAttr treatment of leading-underscore
    # class attributes.

    @model_validator(mode="before")
    @classmethod
    def _strip_pr_b_reserved_keys(cls, data: Any) -> Any:
        # PR-D3 absorbing shim — see module-level docstring near
        # _PR_B_RESERVED_LEAD_KEYS for rationale.
        # Declared before _backfill_legacy_quote_text; key sets are disjoint
        # ({voice_quality, quote_source} vs {quote_text, status}) so the
        # ordering is incidental — both validators always run, and neither
        # touches the other's fields.
        # Defensive shallow copy so the caller's input dict is never
        # mutated (review #38 MEDIUM). Fast-path skips the copy when the
        # dict has no reserved keys (the steady-state case post-soak).
        if not isinstance(data, dict):
            return data
        if not any(key in data for key in _PR_B_RESERVED_LEAD_KEYS):
            return data
        data = dict(data)
        for key in _PR_B_RESERVED_LEAD_KEYS:
            if key in data:
                _warn_pr_b_reserved_key_once("CateringLead", key)
                data.pop(key, None)
        return data

    @model_validator(mode="before")
    @classmethod
    def _backfill_legacy_quote_text(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        status = data.get("status")
        post_awaiting = {"AWAITING_OWNER_APPROVAL", "OWNER_APPROVED", "OWNER_EDITED",
                         "SENT_TO_CUSTOMER"}
        if status in post_awaiting and not (data.get("quote_text", "") or "").strip():
            sys.stderr.write(
                f"WARN: legacy quote_text=empty on lead_id={data.get('lead_id')!r} "
                f"status={status!r}; backfilling with sentinel.\n"
            )
            data["quote_text"] = LEGACY_QUOTE_TEXT_SENTINEL
        return data

    @model_validator(mode="after")
    def _quote_required_post_awaiting(self) -> "CateringLead":
        post_awaiting = {"AWAITING_OWNER_APPROVAL", "OWNER_APPROVED", "OWNER_EDITED",
                         "SENT_TO_CUSTOMER"}
        if self.status in post_awaiting and not self.quote_text.strip():
            raise ValueError(
                f"status={self.status!r} requires non-empty quote_text"
            )
        return self


class CateringLeadStore(BaseModel):
    """Per-customer catering leads (lives at /opt/shift-agent/state/catering-leads.json).

    v0.3: extra='ignore' (was 'forbid') for rollback safety. New `schema_version`
    field allows future migrations; old code drops it cleanly on rollback.
    """
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
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

    @field_validator("updated_by")
    @classmethod
    def _validate_updated_by(cls, v: str) -> str:
        """v0.3: must be empty, 'photo-ocr', 'manual', or an E.164 phone."""
        if v == "" or v in ("photo-ocr", "manual"):
            return v
        if not re.match(r"^\+\d{10,15}$", v):
            raise ValueError(
                f"updated_by must be 'photo-ocr', 'manual', or E.164 phone: {v!r}"
            )
        return v


class MenuPendingUpdate(BaseModel):
    """A proposed menu update awaiting owner confirmation."""
    model_config = ConfigDict(extra="forbid")
    update_id: str = Field(min_length=1, max_length=64)
    proposed_at: datetime
    source_image_id: Optional[str] = None
    extracted_items: list[MenuItem]
    # v0.3: unified to _CODE_FULL_PATTERN (excludes I, O, 0, 1, L). Pre-deploy
    # scan verified no L-bearing codes in production catering-menu-pending.json.
    confirmation_code: str = Field(pattern=_CODE_FULL_PATTERN,
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


# Agent #21 — Expense Bookkeeper (v0.1; mocked QBOClient interface)
# See tasks/expense-bookkeeper-v01-design.md for full design.
class ExpenseBookkeeperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    cockpit_threshold_cents: int = Field(default=5000, gt=0)
    auto_categorize_threshold: float = Field(default=0.85, ge=0.5, le=1.0)
    require_owner_approval_for_personal_flag: bool = True
    reversibility_window_hours: int = Field(default=24, ge=1, le=168)
    dedup_hash_distance_threshold: int = Field(default=4, ge=0, le=20)
    receipt_retention_days: int = Field(default=90, ge=7, le=2555)
    proposal_ttl_hours: int = Field(default=72, ge=1, le=336)
    qbo_client_mode: Literal["mock", "real"] = "mock"


# Expense Bookkeeper domain models
class ExpenseLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=200)
    amount_cents: int  # cents only; never float
    quantity: Optional[float] = Field(default=None, ge=0)
    unit_price_cents: Optional[int] = None


class ExpenseClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_business: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=300)
    qbo_account: str = Field(min_length=1, max_length=100)


class ReceiptExtraction(BaseModel):
    """Vision-extractor output. extracted totals are ADVISORY ONLY;
    owner-confirmed total is the source of truth for the QBO push (defends
    against prompt injection in receipt text).

    extra='ignore' matches CateringLeadExtractedFields precedent — LLM-output
    shapes tolerate unmodelled future fields per docs/hermes-alignment.md
    Part 1 schema pattern."""
    model_config = ConfigDict(extra="ignore")
    vendor_name: Optional[str] = Field(default=None, max_length=200)
    vendor_normalized: Optional[str] = None
    receipt_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    line_items: list[ExpenseLineItem] = Field(default_factory=list, max_length=200)
    subtotal_cents: Optional[int] = None
    tax_cents: Optional[int] = None
    total_cents: Optional[int] = None  # ADVISORY — not the push truth
    payment_method: Optional[str] = Field(default=None, max_length=20)
    extraction_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    raw_text_for_audit: str = Field(default="", max_length=4000)


ExpenseLeadStatus = Literal[
    "EXTRACTING",
    "AWAITING_OWNER_APPROVAL",
    "APPROVED_PENDING_PUSH",
    "PUSHED",
    "PUSH_FAILED",
    "REVERSED",
    "REJECTED",
    "EXPIRED",
]

EXPENSE_TERMINAL_STATUSES: frozenset[str] = frozenset({"REVERSED", "REJECTED", "EXPIRED"})
"""Strict no-outbound-transitions terminals. PUSHED is NOT here because
owner can still `undo` to REVERSED within the reversibility window."""

EXPENSE_RETENTION_CANDIDATES: frozenset[str] = frozenset(
    {"PUSHED", "REVERSED", "REJECTED", "EXPIRED"}
)
"""Statuses whose receipt JPEGs are eligible for retention-based pruning.
Used by prune-and-expire-expenses.py."""

EXPENSE_APPROVAL_CLOSED_STATUSES: frozenset[str] = frozenset(
    {"PUSHED", "REVERSED", "REJECTED", "EXPIRED"}
)
"""Statuses where owner approval flow is no longer active. Used by
_find_lead_by_code to skip leads that already completed approval."""

EXPENSE_TRANSITIONS: dict[str, frozenset[str]] = {
    "EXTRACTING":              frozenset({"AWAITING_OWNER_APPROVAL", "REJECTED", "EXPIRED"}),
    "AWAITING_OWNER_APPROVAL": frozenset({"APPROVED_PENDING_PUSH", "REJECTED", "EXPIRED"}),
    "APPROVED_PENDING_PUSH":   frozenset({"PUSHED", "PUSH_FAILED"}),
    "PUSH_FAILED":             frozenset({"APPROVED_PENDING_PUSH", "REJECTED"}),
    "PUSHED":                  frozenset({"REVERSED"}),
    "REVERSED":                frozenset(),
    "REJECTED":                frozenset(),
    "EXPIRED":                 frozenset(),
}


def is_expense_transition_allowed(src: str, tgt: str) -> bool:
    return tgt in EXPENSE_TRANSITIONS.get(src, frozenset())


class ExpenseLead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expense_id: str = Field(pattern=r"^E\d{4,}$")
    # min_length is enforced by the shared field-validator below (which also
    # rejects whitespace-only and control chars). Keeping a separate Field
    # constraint would race with the validator and produce a different error
    # message on the empty-string case (see audit-bug v1.1).
    original_message_id: str
    sender_phone: str
    sender_lid: Optional[str] = None
    received_at: datetime
    image_path: str
    image_phash: str = Field(min_length=16, max_length=16)
    image_byte_hash: str = Field(min_length=64, max_length=64)
    extraction: Optional[ReceiptExtraction] = None
    classification: Optional[ExpenseClassification] = None
    qbo_account: Optional[str] = None
    owner_approval_code: Optional[ProposalCode] = None
    owner_approval_received_at: Optional[datetime] = None
    owner_confirmed_total_cents: Optional[int] = None
    extracted_total_cents: Optional[int] = None
    qbo_pushed_total_cents: Optional[int] = None
    qbo_transaction_id: Optional[str] = None
    pushed_at: Optional[datetime] = None
    status: ExpenseLeadStatus = "EXTRACTING"
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
    duplicate_of: Optional[str] = None
    reconcile_required: bool = False  # set by orphan detection; blocks new owner actions until cleared

    @field_validator("sender_phone", "original_message_id")
    @classmethod
    def _validate_required_no_whitespace_no_nullbyte(cls, v: str) -> str:
        """Audit-bug v1.1 fix: addresses BUGs 2 + 3 together.

        - sender_phone (BUG-2 audit): reject empty / whitespace-only.
          Field(min_length=1) alone passes "   " which would break owner
          re-auth at apply-expense-decision step where
          `sender_phone == owner_phone`.
        - original_message_id (BUG-3 audit): reject null byte / control
          char. NDJSON audit-log safety; Pydantic `model_dump_json`
          escapes these but defence-in-depth keeps log-corruption surface
          zero.
        """
        if not v.strip():
            raise ValueError("must not be empty or whitespace-only")
        if any(c in v for c in ("\0", "\r", "\n", "\t")):
            raise ValueError("must not contain null byte or control characters")
        return v

    @field_validator("image_path")
    @classmethod
    def _path_under_managed_dir(cls, v: str) -> str:
        """Reject path traversal + sibling-dir attacks. B-H3 fix: ensure
        managed dir has trailing separator before prefix-match (defends
        against EXPENSE_RECEIPTS_DIR='/opt/.../receipts' (no slash) →
        sibling '/opt/.../receipts-evil/foo.jpg' passing startswith)."""
        managed = os.environ.get(
            "EXPENSE_RECEIPTS_DIR",
            "/opt/shift-agent/state/expense-bookkeeper/receipts/",
        )
        if not managed.endswith("/"):
            managed = managed + "/"
        if ".." in v or "\0" in v:
            raise ValueError("invalid image_path: contains path traversal")
        if not v.startswith(managed):
            raise ValueError(f"image_path must be under {managed!r}")
        return v


class ExpenseLeadStore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
    leads: list[ExpenseLead] = Field(default_factory=list)
    last_id: int = Field(default=0, ge=0)


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
    expense_bookkeeper: ExpenseBookkeeperConfig = Field(default_factory=ExpenseBookkeeperConfig)

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

    @field_validator("ts", mode="before")
    @classmethod
    def _ensure_tz_aware(cls, v: Any) -> Any:
        """v0.3: tz-aware-only invariant. Naive datetimes auto-converted to UTC
        with WARN — preserves backward compat for any historic naive entries
        while new writes are tz-aware. Audit log replay never raises."""
        if isinstance(v, datetime):
            if v.tzinfo is None:
                sys.stderr.write(
                    f"WARN: naive ts {v.isoformat()!r} auto-converted to UTC\n"
                )
                return v.replace(tzinfo=timezone.utc)
        elif isinstance(v, str):
            try:
                parsed = datetime.fromisoformat(v)
                if parsed.tzinfo is None:
                    sys.stderr.write(
                        f"WARN: naive ts string {v!r} auto-converted to UTC\n"
                    )
                    return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass  # Pydantic surfaces its own clear error
        return v


class _UnknownLogEntry(_BaseEntry):
    """Forward-compat passthrough for unrecognized LogEntry `type` values.

    Old binaries reading rows written by newer binaries downgrade unknown
    `type` values to this model rather than raising ValidationError. The
    raw payload is captured (extra="allow") so audit-replay tooling can
    still inspect the row even though no isinstance branch matches.

    Convention departure (validated for Pydantic 2.10+):
    - Other LogEntry variants use `type: Literal[...]` + `extra="forbid"`.
    - `_UnknownLogEntry` uses `type: str` + `extra="allow"` to absorb any
      future variant. The picker `_pick_log_entry_tag` routes any tag that
      isn't in `_KNOWN_LOG_ENTRY_TYPES` here, including `""` and unknown
      strings (intentional capture-and-preserve; missing-key returns None
      from `dict.get` and also routes here).
    - Type validation discipline is preserved: a known type with bad
      fields still raises ValidationError; only UNKNOWN types pass through.

    Tag(``_unknown_``) on the union member is the routing handle; the
    `type: str` field stores the original tag value (e.g. `"future_xyz"`)
    so round-trips are lossless.
    """
    model_config = ConfigDict(extra="allow")  # OVERRIDES _BaseEntry's extra="forbid"
    type: str  # NOT Literal — accepts any string the discriminator routes here


def _pick_log_entry_tag(v: Any) -> str:
    """LogEntry discriminator picker (Pydantic v2 callable form, validated
    on Pydantic 2.12.5).

    Returns the value of `type` if it matches a known variant's Tag, else
    the sentinel `"_unknown_"` which routes the row to _UnknownLogEntry.

    Empty string `""`, missing-key (None from dict.get), and any unknown
    string ALL route to `"_unknown_"` — capture-and-preserve is the design
    intent for forward-compat. Non-string `type` values (int, etc.) ALSO
    route to `"_unknown_"`; Pydantic's `_UnknownLogEntry.type: str` validation
    will then raise on those, which is the correct discrimination
    (a recognized literal + bad fields raises; only truly unknown tags
    pass through).
    """
    if isinstance(v, dict):
        t = v.get("type")
    else:
        t = getattr(v, "type", None)
    if isinstance(t, str) and t in _KNOWN_LOG_ENTRY_TYPES:
        return t
    return "_unknown_"


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
    confirmation_code: str = Field(pattern=_CODE_FULL_PATTERN)
    item_count: int = Field(ge=0)
    source_image_id: Optional[str] = None
    # v0.3: count of items dropped during validation. Surfaces extraction
    # quality regressions (e.g., LLM started emitting bad dietary_tags).
    extraction_dropped_count: int = Field(default=0, ge=0)


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
# Agent #21 Expense Bookkeeper log entries (15 types)
# ─────────────────────────────────────────────────────────────────

class ExpenseReceiptReceived(_BaseEntry):
    type: Literal["expense_receipt_received"]
    expense_id: str
    sender_phone: str
    image_path: str
    image_phash: str
    original_message_id: str


class ExpenseDuplicateDetected(_BaseEntry):
    type: Literal["expense_duplicate_detected"]
    expense_id: str
    matched_expense_id: str
    phash_distance: int
    owner_override: bool = False


class ExpenseExtractionCompleted(_BaseEntry):
    type: Literal["expense_extraction_completed"]
    expense_id: str
    extraction_confidence: float
    line_item_count: int
    extracted_total_cents: Optional[int] = None


class ExpenseClassificationProposed(_BaseEntry):
    type: Literal["expense_classification_proposed"]
    expense_id: str
    is_business: bool
    classification_confidence: float
    qbo_account: str


class ExpenseOwnerApprovalRequested(_BaseEntry):
    type: Literal["expense_owner_approval_requested"]
    expense_id: str
    owner_approval_code: ProposalCode
    extracted_total_cents: int
    routed_to: Literal["whatsapp", "cockpit_v01_paper"]


class ExpenseOwnerDecision(_BaseEntry):
    type: Literal["expense_owner_decision"]
    expense_id: str
    decision: Literal[
        "approved", "rejected", "force_approved",
        "amount_mismatch", "force_required",
    ]
    raw_message: str = Field(max_length=500)
    code_matched: bool
    amount_matched: bool
    force_context: Literal["threshold", "dedup", "both", "none"] = "none"


class ExpenseLeadStatusChange(_BaseEntry):
    type: Literal["expense_lead_status_change"]
    expense_id: str
    from_status: ExpenseLeadStatus
    to_status: ExpenseLeadStatus
    reason: Optional[str] = Field(default=None, max_length=200)


class ExpensePushAttempted(_BaseEntry):
    type: Literal["expense_push_attempted"]
    expense_id: str
    qbo_client_mode: Literal["mock", "real"]
    extracted_total_cents: Optional[int] = None
    owner_confirmed_total_cents: int
    push_total_cents: int


class ExpensePushed(_BaseEntry):
    type: Literal["expense_pushed"]
    expense_id: str
    qbo_transaction_id: str
    qbo_amount_cents: int
    push_attempt_no: int = 1


class ExpensePushFailed(_BaseEntry):
    type: Literal["expense_push_failed"]
    expense_id: str
    error_class: Literal[
        "token_expired", "rate_limit", "bad_account",
        "server", "network", "invalid_request",
    ]
    error_message_redacted: str = Field(max_length=200)


class ExpenseReversalRequested(_BaseEntry):
    type: Literal["expense_reversal_requested"]
    expense_id: str
    requested_by_phone: str
    requested_by_role: str
    within_window: bool
    hours_since_push: float


class ExpenseReversed(_BaseEntry):
    type: Literal["expense_reversed"]
    expense_id: str
    qbo_transaction_id: str
    void_method: Literal["api_void", "manual_flag"]


class ExpenseReceiptPruned(_BaseEntry):
    type: Literal["expense_receipt_pruned"]
    expense_id: str
    vendor_normalized: Optional[str] = None
    extracted_total_cents: Optional[int] = None
    reason: Literal["retention_expired", "manual"] = "retention_expired"


class ExpenseNonOwnerUndoDeclined(_BaseEntry):
    type: Literal["expense_non_owner_undo_declined"]
    expense_id: str
    requested_by_phone: Optional[str] = None
    requested_by_lid: Optional[str] = None


class ExpenseOrphanDetected(_BaseEntry):
    type: Literal["expense_orphan_detected"]
    expense_id: str
    last_known_status: ExpenseLeadStatus
    detected_by: Literal["startup_scan", "manual"]


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
        "message_id_phone_mismatch",  # v0.3: idempotency-key collision with mismatched phone
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
    edit_text: str = Field(default="", max_length=2000)

    @model_validator(mode="before")
    @classmethod
    def _backfill_legacy_edit_text(cls, data: Any) -> Any:
        """v0.3: legacy edit decisions had empty edit_text. Backfill on read
        with sentinel + WARN. New writes hit strict mode='after' validator."""
        if not isinstance(data, dict):
            return data
        if data.get("decision") == "edit" and not (data.get("edit_text") or "").strip():
            sys.stderr.write(
                f"WARN: legacy edit_text=empty on lead_id={data.get('lead_id')!r}; "
                f"backfilling with sentinel.\n"
            )
            data["edit_text"] = LEGACY_EDIT_TEXT_SENTINEL
        return data

    @model_validator(mode="after")
    def _edit_text_required_for_edit(self) -> "CateringOwnerDecision":
        if self.decision == "edit" and not self.edit_text.strip():
            raise ValueError("decision='edit' requires non-empty edit_text")
        return self


class CateringQuoteSent(_BaseEntry):
    type: Literal["catering_quote_sent"]
    lead_id: str = Field(min_length=1)
    customer_phone: E164Phone
    outbound_message_id: str = Field(min_length=1)


# v0.3 NEW audit classes — idempotency anchors + state-transition coverage

class CateringQuoteAttempted(_BaseEntry):
    """v0.3 idempotency anchor for customer-quote send (apply-script approve flow).

    Written BEFORE bridge POST in the SAME lock as state-mutation. On retry,
    presence of this row → script returns idempotent without re-POSTing,
    preventing duplicate quotes to the customer.

    PR-D1 extension (design v2 §3.3 / H8 / R2 HIGH-2): added
    `bridge_post_outcome` so retries can distinguish 'anchor-then-success'
    (skip bridge POST) from 'anchor-then-failed/unknown' (retry bridge POST).
    Without this field, an anchor + failed bridge POST would create a
    stuck-loop where retries see the anchor and never re-attempt.

    Two-step write contract:
      1. First anchor row written BEFORE bridge POST with outcome="unknown".
      2. After bridge POST returns, second anchor row is appended with
         outcome="success" or "failed" — supersedes step-1 row via tail-scan.
    Field has default `"unknown"` so legacy rows (pre-PR-D1) read cleanly.
    """
    type: Literal["catering_quote_attempted"]
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
    bridge_post_outcome: Literal["success", "failed", "unknown"] = "unknown"


class CateringOwnerApprovalCardAttempted(_BaseEntry):
    """v0.3 idempotency anchor for owner-approval card send (create-script flow).

    Written BEFORE bridge POST in same lock as state-mutation. Mirror of
    CateringQuoteAttempted — prevents duplicate owner card on retry.
    """
    type: Literal["catering_owner_approval_card_attempted"]
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)


class CateringOwnerApprovalCardFailed(_BaseEntry):
    """v0.3: bridge POST for owner-approval card failed (timeout, 5xx, etc.).
    Distinguishes 'card sent' from 'card failed' in the audit log."""
    type: Literal["catering_owner_approval_card_failed"]
    lead_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    bridge_error: str = Field(default="", max_length=2000)


class CateringOwnerApprovalCardSkipped(_BaseEntry):
    """v0.3: card not even attempted (config issue). Distinct from CardFailed."""
    type: Literal["catering_owner_approval_card_skipped"]
    lead_id: str = Field(min_length=1)
    reason: Literal["self_chat_jid_empty", "config_disabled"]


class CateringOwnerEdited(_BaseEntry):
    """v0.3: explicit audit class for OWNER_EDITED transition. Separates
    'owner intent' from CateringOwnerDecision (which is a generic decision row)."""
    type: Literal["catering_owner_edited"]
    lead_id: str = Field(min_length=1)
    edit_text: str = Field(min_length=1, max_length=2000)


class CateringDeclineAttempted(_BaseEntry):
    """v0.3 idempotency anchor for decline-with-reason customer message
    (apply-script reject path). Symmetric to CateringQuoteAttempted."""
    type: Literal["catering_decline_attempted"]
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)


class ConfigLoadFailed(_BaseEntry):
    """PR-D1: emitted best-effort by audit_helpers.log_config_load_failed_best_effort
    when a config file fails to load (parse error, validation error,
    FileNotFoundError, OSError). Captures the error class + path so
    operators can correlate a missing-config bug with the specific
    script that hit it.

    Helper uses datetime.now(timezone.utc) always — when config fails
    to load, customer_now() has no tz source. UTC is the only safe ts
    (design v2 §4.2 / M4).
    """
    type: Literal["config_load_failed"]
    path: str = Field(min_length=1)
    error_class: str = Field(min_length=1, max_length=80)
    error_detail: str = Field(default="", max_length=2000)
    script_name: str = Field(min_length=1, max_length=80)


class CateringLeadManuallyReconciled(_BaseEntry):
    """PR-D1: emitted by catering-lead-reconcile script (PR-D2). Distinguishes
    operator intervention from automated state advance (which uses
    CateringLeadStatusChange with actor='system' or 'owner').

    Naming: design v2 §14.2 R5-H-2 — verb form `Reconcile` violates
    Catering<Subject><PastParticiple> pattern; renamed to past-participle
    `Reconciled`.
    """
    type: Literal["catering_lead_manually_reconciled"]
    lead_id: str = Field(min_length=1)
    from_status: CateringLeadStatus
    to_status: CateringLeadStatus
    reason: str = Field(min_length=1, max_length=2000)
    operator_uid: int  # os.getuid() — captures who ran the script


class CateringQuoteSentLeadMissing(_BaseEntry):
    """PR-D1: emitted by apply-catering-owner-decision when the post-bridge
    re-load of leads.json finds the lead absent (matched_idx is None).

    Customer demonstrably received the quote (bridge POST succeeded), but
    SENT_TO_CUSTOMER could not be persisted because the lead vanished
    between the two LEADS_LOCK windows. Operator must reconcile via
    catering-lead-reconcile (PR-D2 §8).

    Naming: design v2 §14.2 R5-H-2 — `Catering<Subject><PastParticiple>`
    pattern. Subject="QuoteSent" (the past-tense fact), Past-participle
    qualifier="LeadMissing" (the deviation that triggered audit).
    """
    type: Literal["catering_quote_sent_lead_missing"]
    lead_id: str = Field(min_length=1)
    original_message_id: str = Field(min_length=1)
    customer_phone_at_approve: E164Phone
    outbound_message_id: str = Field(min_length=1)
    detail: str = Field(default="", max_length=500)


class CateringQuoteRenderFailed(_BaseEntry):
    """v0.3 (review M2): emitted when apply-catering-owner-decision approve
    flow fails to render the customer quote (template KeyError, OSError,
    or unexpected validation issue). Without this audit row, an
    approve-blocked-on-render-error left the lead at AWAITING_OWNER_APPROVAL
    with no durable trace of why retries kept failing — operators saw stderr
    only.
    """
    type: Literal["catering_quote_render_failed"]
    lead_id: str = Field(min_length=1)
    code: str = Field(pattern=_CODE_FULL_PATTERN)
    error_class: str = Field(min_length=1, max_length=80,
                             description="Python exception class name (e.g. 'KeyError')")
    detail: str = Field(default="", max_length=2000)


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


# PR-D1: callable Discriminator + Tag-wrapped union members + _UnknownLogEntry
# forward-compat shim. Replaces `Field(discriminator="type")` which raised
# `union_tag_invalid` on unknown tags BEFORE any validator could run.
#
# Pattern (validated on Pydantic 2.12.5):
#   - Each known variant wrapped Annotated[Variant, Tag("type_literal")].
#   - _UnknownLogEntry wrapped Annotated[_UnknownLogEntry, Tag("_unknown_")].
#   - Discriminator(_pick_log_entry_tag) routes by callable; unknown tags
#     return "_unknown_" → captured by _UnknownLogEntry (extra="allow").
#
# CONVENTION DEPARTURE: every other variant subclasses _BaseEntry with
# extra="forbid" + type: Literal[...]. _UnknownLogEntry deliberately uses
# extra="allow" + type: str. The picker confines this exception to
# unknown-tag routing only; known-but-malformed rows still raise
# ValidationError (test asserts this in test_log_entry_forward_compat.py).
LogEntry = Annotated[
    Union[
        Annotated[RawInbound, Tag("raw_inbound")],
        Annotated[ProposalCreated, Tag("proposal_created")],
        Annotated[ProposalStatusChange, Tag("proposal_status_change")],
        Annotated[OutboundAttempted, Tag("outbound_attempted")],
        Annotated[OutboundSent, Tag("outbound_sent")],
        Annotated[OutboundSendFailed, Tag("outbound_send_failed")],
        Annotated[OutboundResponse, Tag("outbound_response")],
        Annotated[OutboundCapExceeded, Tag("outbound_cap_exceeded")],
        Annotated[OutboundRefusedDisabled, Tag("outbound_refused_disabled")],
        Annotated[AgentStateChange, Tag("agent_state_change")],
        Annotated[UnknownSenderDeclined, Tag("unknown_sender_declined")],
        Annotated[InvariantViolation, Tag("invariant_violation")],
        Annotated[HealthCheckFailure, Tag("health_check_failure")],
        # BEGIN shift-agent-sender-id
        Annotated[LidLearned, Tag("lid_learned")],
        # END shift-agent-sender-id
        # Dispatcher routing audit (added with Fix 2 of dispatcher-routing-fixes)
        Annotated[DispatcherRouted, Tag("dispatcher_routed")],
        # Agent #4 Daily Brief
        Annotated[BriefAttempted, Tag("brief_attempted")],
        Annotated[BriefSent, Tag("brief_sent")],
        Annotated[BriefSendFailed, Tag("brief_send_failed")],
        Annotated[BriefSkipped, Tag("brief_skipped")],
        # Agent #5 EOD Reconciliation
        Annotated[EodSnapshot, Tag("eod_snapshot")],
        Annotated[EodPushoverSent, Tag("eod_pushover_sent")],
        Annotated[EodSkipped, Tag("eod_skipped")],
        # Agent #3 Multi-Location Coordinator
        Annotated[CrossLocationQuery, Tag("cross_location_query")],
        Annotated[InterLocationTransferProposed, Tag("inter_location_transfer_proposed")],
        # Agent #2 Catering Lead
        Annotated[CateringLeadCreated, Tag("catering_lead_created")],
        Annotated[CateringLeadStatusChange, Tag("catering_lead_status_change")],
        Annotated[CateringLeadRejected, Tag("catering_lead_rejected")],
        Annotated[CateringQuoteDrafted, Tag("catering_quote_drafted")],
        Annotated[CateringOwnerApprovalRequested, Tag("catering_owner_approval_requested")],
        Annotated[CateringOwnerDecision, Tag("catering_owner_decision")],
        Annotated[CateringQuoteSent, Tag("catering_quote_sent")],
        # v0.3: idempotency anchors + state-transition coverage
        Annotated[CateringQuoteAttempted, Tag("catering_quote_attempted")],
        Annotated[CateringOwnerApprovalCardAttempted, Tag("catering_owner_approval_card_attempted")],
        Annotated[CateringOwnerApprovalCardFailed, Tag("catering_owner_approval_card_failed")],
        Annotated[CateringOwnerApprovalCardSkipped, Tag("catering_owner_approval_card_skipped")],
        Annotated[CateringOwnerEdited, Tag("catering_owner_edited")],
        Annotated[CateringDeclineAttempted, Tag("catering_decline_attempted")],
        # v0.3 (review M2): render-failure observability
        Annotated[CateringQuoteRenderFailed, Tag("catering_quote_render_failed")],
        # PR-D1: post-bridge state-vs-outbound divergence audit
        Annotated[CateringQuoteSentLeadMissing, Tag("catering_quote_sent_lead_missing")],
        # PR-D1: config load observability + operator reconcile audit
        Annotated[ConfigLoadFailed, Tag("config_load_failed")],
        Annotated[CateringLeadManuallyReconciled, Tag("catering_lead_manually_reconciled")],
        Annotated[MenuUpdateProposed, Tag("menu_update_proposed")],
        Annotated[MenuUpdateApplied, Tag("menu_update_applied")],
        Annotated[MenuUpdateRejected, Tag("menu_update_rejected")],
        # Agent #21 Expense Bookkeeper (15 entry types)
        Annotated[ExpenseReceiptReceived, Tag("expense_receipt_received")],
        Annotated[ExpenseDuplicateDetected, Tag("expense_duplicate_detected")],
        Annotated[ExpenseExtractionCompleted, Tag("expense_extraction_completed")],
        Annotated[ExpenseClassificationProposed, Tag("expense_classification_proposed")],
        Annotated[ExpenseOwnerApprovalRequested, Tag("expense_owner_approval_requested")],
        Annotated[ExpenseOwnerDecision, Tag("expense_owner_decision")],
        Annotated[ExpenseLeadStatusChange, Tag("expense_lead_status_change")],
        Annotated[ExpensePushAttempted, Tag("expense_push_attempted")],
        Annotated[ExpensePushed, Tag("expense_pushed")],
        Annotated[ExpensePushFailed, Tag("expense_push_failed")],
        Annotated[ExpenseReversalRequested, Tag("expense_reversal_requested")],
        Annotated[ExpenseReversed, Tag("expense_reversed")],
        Annotated[ExpenseReceiptPruned, Tag("expense_receipt_pruned")],
        Annotated[ExpenseNonOwnerUndoDeclined, Tag("expense_non_owner_undo_declined")],
        Annotated[ExpenseOrphanDetected, Tag("expense_orphan_detected")],
        # PR-D1 forward-compat shim — UNKNOWN tags route here
        Annotated[_UnknownLogEntry, Tag("_unknown_")],
    ],
    Discriminator(_pick_log_entry_tag),
]


def _build_known_log_entry_types() -> frozenset[str]:
    """Computed once at module-import via introspection of LogEntry union args.

    Excludes the `_unknown_` sentinel: the picker uses this set to decide
    whether to ROUTE to a known variant or fall through to the sentinel.
    Sentinel is the FALLBACK return, not a routable known type.

    Eliminates drift between the LogEntry union and the picker's
    known-tags set: adding a new Tag-wrapped variant to the union
    automatically extends this set.
    """
    union_arg = get_args(LogEntry)[0]  # Union[Annotated[Model, Tag(...)], ...]
    tags: set[str] = set()
    for member in get_args(union_arg):  # each is Annotated[Model, Tag(...)]
        for meta in get_args(member):
            if isinstance(meta, Tag):
                tags.add(meta.tag)
    return frozenset(tags - {"_unknown_"})


_KNOWN_LOG_ENTRY_TYPES: frozenset[str] = _build_known_log_entry_types()


__all__ = [
    # v0.3 phone helpers
    "PHONE_E164_PATTERN", "is_valid_e164",
    # v0.3 lifecycle sentinels
    "PRE_QUOTE_DRAFT_SENTINEL", "LEGACY_QUOTE_TEXT_SENTINEL", "LEGACY_EDIT_TEXT_SENTINEL",
    "E164Phone", "Role", "EmployeeId", "Employee", "PhoneAssignment", "ScheduleEntry", "Roster",
    "Config", "CustomerConfig", "OwnerConfig", "LimitsConfig", "AlertingConfig", "BackupConfig", "OperationsConfig",
    "DailyBriefConfig", "BriefSection",
    "EodConfig",
    "LocationEntry", "MultiLocationConfig",
    "CateringConfig", "CateringLeadStatus", "CateringLeadExtractedFields",
    "CateringLead", "CateringLeadStore",
    "is_catering_terminal", "CATERING_TERMINAL_STATUSES",
    # v0.3 status-machine + helpers
    "CATERING_TRANSITIONS", "is_catering_transition_allowed",
    "assert_rejection_reason_complete",
    # v0.3 code-pattern constants
    "_CODE_BODY_PATTERN", "_CODE_FULL_PATTERN",
    "MenuItem", "Menu", "MenuPendingUpdate", "DietaryTag", "MenuCategory",
    # Tier 2 configs
    "InventoryConfig", "SupplierConfig", "VipConfig", "CateringFollowupConfig",
    "HiringConfig", "ComplianceConfig", "EmployeeDocsConfig", "CashArConfig", "SalesTaxConfig",
    # Agent #21 Expense Bookkeeper
    "ExpenseBookkeeperConfig", "ExpenseLineItem", "ExpenseClassification", "ReceiptExtraction",
    "ExpenseLeadStatus", "EXPENSE_TERMINAL_STATUSES", "EXPENSE_TRANSITIONS",
    "EXPENSE_RETENTION_CANDIDATES", "EXPENSE_APPROVAL_CLOSED_STATUSES",
    "is_expense_transition_allowed",
    "ExpenseLead", "ExpenseLeadStore",
    "ExpenseReceiptReceived", "ExpenseDuplicateDetected",
    "ExpenseExtractionCompleted", "ExpenseClassificationProposed",
    "ExpenseOwnerApprovalRequested", "ExpenseOwnerDecision",
    "ExpenseLeadStatusChange",
    "ExpensePushAttempted", "ExpensePushed", "ExpensePushFailed",
    "ExpenseReversalRequested", "ExpenseReversed",
    "ExpenseReceiptPruned", "ExpenseNonOwnerUndoDeclined",
    "ExpenseOrphanDetected",
    "Proposal", "ProposalId", "ProposalCode",
    "AwaitingProposal", "ApprovedProposal", "ReconcilingProposal", "SentProposal",
    "SendFailedProposal", "AcceptedProposal", "DeclinedProposal", "DeniedByOwnerProposal",
    "ExpiredProposal", "CancelledProposal", "NoResponseTimeoutProposal",
    "TERMINAL_STATUSES", "LEGAL_TRANSITIONS", "is_terminal_status", "is_legal_transition",
    "PendingStore", "SendCounter", "SeenIds",
    "LogEntry", "_UnknownLogEntry", "_KNOWN_LOG_ENTRY_TYPES",
    "RawInbound", "ProposalCreated", "ProposalStatusChange",
    "OutboundAttempted", "OutboundSent", "OutboundSendFailed",
    "OutboundResponse", "OutboundCapExceeded", "OutboundRefusedDisabled",
    "AgentStateChange", "UnknownSenderDeclined", "InvariantViolation", "HealthCheckFailure",
    "LidLearned", "DispatcherRouted",
    "BriefAttempted", "BriefSent", "BriefSendFailed", "BriefSkipped",
    "EodSnapshot", "EodPushoverSent", "EodSkipped",
    "CrossLocationQuery", "InterLocationTransferProposed",
    "CateringLeadCreated", "CateringLeadStatusChange", "CateringLeadRejected", "CateringQuoteDrafted",
    "CateringOwnerApprovalRequested", "CateringOwnerDecision", "CateringQuoteSent",
    # v0.3 catering audit classes
    "CateringQuoteAttempted", "CateringOwnerApprovalCardAttempted",
    "CateringOwnerApprovalCardFailed", "CateringOwnerApprovalCardSkipped",
    "CateringOwnerEdited", "CateringDeclineAttempted",
    "CateringQuoteRenderFailed",
    # PR-D1
    "CateringQuoteSentLeadMissing", "ConfigLoadFailed",
    "CateringLeadManuallyReconciled",
    "MenuUpdateProposed", "MenuUpdateApplied", "MenuUpdateRejected",
]
