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
    Tag, Discriminator, HttpUrl,
)
from typing import Literal, Annotated, Union, Optional, Any, get_args
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
import os
import re
import sys
from pathlib import Path

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
# "birthdays" added Agent #33 v0.1 — opt-in via cfg.daily_brief.sections; the
# default factory at DailyBriefConfig.sections does NOT include it (preserved
# unchanged so existing customers' briefs are unaffected until owner explicitly
# opts in).
BriefSection = Literal[
    "yesterday", "today_outlook", "alerts", "birthdays",
    "catering_learning",
]


# Agent #33 Loyalty config (Tier-2 scaffold). v0.1 covers birthday reminders
# only (Daily Brief section + record-customer-birthday CLI). v0.2 will add
# punch-card / points / WhatsApp owner-command / auto-greeting per
# tasks/todo.md follow-up.
class LoyaltyConfig(BaseModel):
    """Agent #33 Tier-2 scaffold; default off. Opt-in per customer."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False


# Per-customer birthday record (Agent #33 v0.1).
class CustomerBirthday(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_phone: E164Phone
    display_name: str = Field(min_length=1, max_length=100)
    # MM-DD only (no year — many customers don't share or won't update accurately).
    birthday: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

    @field_validator("birthday")
    @classmethod
    def _validate_calendar_date(cls, v: str) -> str:
        """Regex pattern allows 02-30, 04-31, etc. Reject illegal dates by
        attempting to parse with a leap-year pivot (2024) so 02-29 is
        accepted as a legitimate leap-day birthday (R2-B2 fix from PR review)."""
        datetime.strptime(f"2024-{v}", "%Y-%m-%d")
        return v


class CustomerBirthdayStore(BaseModel):
    """Outer container for state/customer-birthdays.json."""
    model_config = ConfigDict(extra="forbid")
    customers: list[CustomerBirthday] = Field(default_factory=list)
    schema_version: Literal[1] = 1   # R2-B1 fix: pinned for migration discipline


# ─────────────────────────────────────────────────────────────────
# Agent #41 Owner Wellbeing config (revived from retired #20)
# ─────────────────────────────────────────────────────────────────

OwnerWellbeingDay = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class OwnerWellbeingConfig(BaseModel):
    """Quiet-hours rule (Agent #41 v0.1). Suppresses non-critical Pushover /
    WhatsApp notifications during owner-configured quiet windows.

    v0.2 will add weekly owner-load summary as a Daily Brief section.

    Default enabled=False — opt-in per customer; matches Tier-2 scaffold
    convention. When False, the guard is a no-op short-circuit at line 1
    of _apply_quiet_hours_guard.
    """
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    quiet_start: str = Field(default="22:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(default="06:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_days: list[OwnerWellbeingDay] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        min_length=1,
    )
    # priority < threshold → suppressed; priority >= threshold → always send.
    # default 1: suppress -2/-1/0 (silent/quiet/normal); allow 1/2 (high/emergency).
    critical_priority_threshold: int = Field(default=1, ge=-2, le=2)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _validate_time_strptime(cls, v: str) -> str:
        from datetime import datetime as _dt
        _dt.strptime(v, "%H:%M")
        return v

    @model_validator(mode="after")
    def _reject_zero_width_window(self) -> "OwnerWellbeingConfig":
        """Zero-width window (start == end) silently never fires
        (same-day branch returns start <= now < end = always False;
        cross-midnight branch unreachable when start == end). Reject at
        validation time so the operator gets a clear error instead of a
        silent no-op."""
        if self.enabled and self.quiet_start == self.quiet_end:
            raise ValueError(
                f"quiet_start == quiet_end ({self.quiet_start!r}) is a "
                f"zero-width window — guard would never fire. Set distinct "
                f"start and end times, or set enabled=False."
            )
        return self


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
    "CUSTOMER_FINALIZED",       # PR-CF1: customer locked in selected_items
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
    "AWAITING_OWNER_APPROVAL": {
        "OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED", "STALE",
        "CUSTOMER_FINALIZED",  # PR-CF1
    },
    "CUSTOMER_FINALIZED": {  # PR-CF1
        "OWNER_APPROVED", "OWNER_EDITED", "OWNER_REJECTED", "STALE",
        # PR-CF1 review-fix R2.B1: explicit self-edge for re-finalize.
        # When a customer changes their mind and submits a different menu
        # selection (different customer_message_id), the status remains
        # CUSTOMER_FINALIZED but the script emits a status_change audit row
        # so the audit chain records the mind-change. Without this self-edge,
        # the transition guard at finalize-catering-menu rejects re-finalize.
        "CUSTOMER_FINALIZED",
    },
    "OWNER_EDITED": {
        "AWAITING_OWNER_APPROVAL", "OWNER_REJECTED",
        "CUSTOMER_FINALIZED",  # PR-CF1: customer can re-finalize after owner edits
    },
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


FlyerWorkflowStatus = Literal[
    "intake_started",
    "collecting_required_info",
    "awaiting_assets",
    "manual_edit_required",
    "generating_concepts",
    "awaiting_concept_selection",
    "revising_design",
    "awaiting_final_approval",
    "finalizing_assets",
    "delivered",
    "completed",
    "closed_no_send",
    # P0 #2 2026-05-28 — severity-tiered QA: warn-tier delivery state. Reachable
    # only from generating_concepts (the QA decision point); exits to
    # revising_design (customer revision), awaiting_final_approval (customer OK),
    # or closed_no_send (operator override). See FLYER_TRANSITIONS below.
    "delivered_with_warning",
]

FlyerOnboardingStatus = Literal[
    "collecting_business_name",
    "collecting_business_address",
    "collecting_public_phone",
    "collecting_business_whatsapp",
    "collecting_authorized_request_number",
    "collecting_business_profile",
    "choosing_plan",
    "confirming_summary",
    "payment_pending",
    "trial",
    "active",
]

FlyerLanguage = Literal[
    "en",
    "te",
    "hi",
    "ml",
    "ta",
    "kn",
    "gu",
    "mr",
    "pa",
    "es",
    "mixed",
    "other",
]

FlyerCreationMode = Literal["sample", "guided", "text"]

FlyerIntakeStatus = Literal[
    "choosing_language",
    "choosing_mode",
    "choosing_sample_idea",
    "text_awaiting_brief",
    "guided_collecting_goal",
    "guided_collecting_schedule",
    "guided_collecting_items",
    "guided_collecting_location",
    "guided_collecting_assets",
    "brief_pending_approval",
]

FlyerIntakeSource = Literal[
    "start_trial",
    "act_now",
    "quick_flyer",
    "new_flyer",
]

FlyerOutputFormat = Literal[
    "whatsapp_image",
    "instagram_post",
    "instagram_story",
    "printable_pdf",
]

FlyerImageQuality = Literal["low", "medium", "high"]
FlyerProviderQuality = Literal["low", "medium", "high", "balanced"]
FlyerModelProviderName = Literal["openrouter", "openai", "local", "manual_review"]
FlyerFactSource = Literal[
    "customer_text",
    # customer_confirmed: an inferred assumption the customer approved/edited for
    # THIS flyer/project (bounded-creative-planner contract). Project-scoped only.
    "customer_confirmed",
    "customer_profile",
    "reference_ocr",
    "reference_vision",
    "uploaded_asset",
    "operator",
    "system",
    # hermes_inferred: a planner assumption (item/headline/section). Lowest merge
    # priority — must never shadow a real fact. Surfaced to the customer as an
    # assumption; only materializes through the firewall gate (slice 3+).
    "hermes_inferred",
]
FlyerReferenceRole = Literal[
    "logo",
    "menu_reference",
    "old_flyer_reference",
    "source_edit_template",
    "inspiration",
    "unsupported",
]
FlyerReferenceExtractionStatus = Literal["not_run", "ok", "low_confidence", "provider_unavailable", "unsupported"]
FlyerVisualQAStatus = Literal["passed", "failed", "not_run", "provider_unavailable"]
FlyerVisualQASource = Literal["ocr_vision", "sidecar_test", "operator_review"]
FlyerManualReviewStatus = Literal["none", "queued", "in_progress", "completed", "break_glass_sent", "closed_no_send"]
FlyerManualReviewReason = Literal[
    "unclassified",
    "legacy_unknown",
    "reference_low_confidence",
    "reference_provider_unavailable",
    "reference_unsupported",
    "reference_not_run",
    "visual_qa_failed",
    "source_edit_provider_unavailable",
    "operator_request",
    "policy_block",
    "provider_timeout",
    "dependency_missing",
    "missing_required_facts",
]


class FlyerRenderProviderConfig(BaseModel):
    """Single Flyer Studio render provider target."""
    model_config = ConfigDict(extra="forbid")
    provider: FlyerModelProviderName
    model: str = Field(min_length=1, max_length=120)
    quality: FlyerProviderQuality = "balanced"


class FlyerTextHeavyDraftPolicy(BaseModel):
    """Rollout-safe text-heavy flyer candidates; only primary is automatic."""
    model_config = ConfigDict(extra="forbid")
    primary: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="recraft/recraft-v4.1",
        quality="balanced",
    ))
    premium: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="sourceful/riverflow-v2-pro",
        quality="high",
    ))
    fallback: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    ))


class FlyerVisualHeavyDraftPolicy(BaseModel):
    """Visual-heavy challenger policy for operator bakeoffs."""
    model_config = ConfigDict(extra="forbid")
    primary: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="black-forest-labs/flux.2-pro",
        quality="high",
    ))
    fallback: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    ))


class FlyerDraftProviderPolicy(BaseModel):
    """Provider routing for new flyer drafts. PR-1 wires only default automatic use."""
    model_config = ConfigDict(extra="forbid")
    default: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="local",
        model="deterministic-renderer",
        quality="low",
    ))
    cost_sensitive: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="openai/gpt-5-image-mini",
        quality="balanced",
    ))
    text_heavy: FlyerTextHeavyDraftPolicy = Field(default_factory=FlyerTextHeavyDraftPolicy)
    visual_heavy: FlyerVisualHeavyDraftPolicy = Field(default_factory=FlyerVisualHeavyDraftPolicy)


class FlyerFinalProviderPolicy(BaseModel):
    """Final asset policy. Default is deterministic export; model fallback is manual/operator-triggered."""
    model_config = ConfigDict(extra="forbid")
    default: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="local",
        model="deterministic-renderer",
        quality="high",
    ))
    fallback: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    ))


class FlyerSourceEditProviderPolicy(BaseModel):
    """Provider routing for source-preserving uploaded-flyer edits."""
    model_config = ConfigDict(extra="forbid")
    default: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    ))
    emergency_fallback: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="manual_review",
        model="manual_review",
        quality="high",
    ))
FlyerAssetKind = Literal[
    "logo",
    "reference_image",
    "concept_preview",
    "final_whatsapp_image",
    "final_instagram_post",
    "final_instagram_story",
    "final_printable_pdf",
]
FlyerAssetDeliveryStatus = Literal["pending", "sent", "failed", "uncertain"]

FLYER_TRANSITIONS: dict[FlyerWorkflowStatus, set[FlyerWorkflowStatus]] = {
    "intake_started": {"collecting_required_info"},
    "collecting_required_info": {"awaiting_assets", "generating_concepts"},
    "awaiting_assets": {"generating_concepts"},
    "manual_edit_required": {"generating_concepts", "revising_design", "awaiting_final_approval", "closed_no_send"},
    "generating_concepts": {"awaiting_concept_selection", "awaiting_final_approval", "manual_edit_required", "delivered_with_warning"},
    "awaiting_concept_selection": {"revising_design"},
    "revising_design": {"generating_concepts", "awaiting_final_approval"},
    "awaiting_final_approval": {"finalizing_assets", "revising_design"},
    "finalizing_assets": {"delivered", "manual_edit_required"},
    "delivered": {"completed", "revising_design"},
    "completed": set(),
    "closed_no_send": set(),
    # P0 #2 2026-05-28 — warn-tier delivery exits. NOT reachable from
    # awaiting_final_approval or revising_design (those re-run QA via
    # generating_concepts which is the single warn-tier entry point).
    "delivered_with_warning": {"revising_design", "awaiting_final_approval", "closed_no_send"},
}


def is_flyer_transition_allowed(from_s: str, to_s: str) -> bool:
    return to_s in FLYER_TRANSITIONS.get(from_s, set())  # type: ignore[arg-type]


class FlyerRecoveryConfig(BaseModel):
    """Flyer recovery watchdog settings. Default inert until explicitly enabled."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "observe", "customer_ack", "bundle", "worker_draft", "pr_ready"] = "off"
    enable_timer: bool = False
    scan_window_minutes: int = Field(default=30, ge=5, le=240)
    ack_cooldown_minutes: int = Field(default=60, ge=5, le=1440)
    ack_reservation_stale_minutes: int = Field(default=10, ge=1, le=120)
    operator_escalation_stale_minutes: int = Field(default=30, ge=5, le=1440)
    max_incidents_per_run: int = Field(default=20, ge=1, le=200)
    manual_queue_stale_minutes: int = Field(default=30, ge=5, le=1440)
    worker_runner: Literal["codex", "claude"] = "codex"
    worker_repo_path: str = Field(default="/opt/shift-agent-source", min_length=1, max_length=500)
    worker_queue_dir: str = Field(default="/opt/shift-agent/state/flyer/recovery_worker_queue", min_length=1, max_length=500)
    worker_drafts_dir: str = Field(default="/opt/shift-agent/state/flyer/recovery_worker_drafts", min_length=1, max_length=500)
    worker_auto_run: bool = False
    max_worker_runs_per_run: int = Field(default=1, ge=0, le=10)
    worker_model: str = Field(default="gpt-5.3-codex", min_length=1, max_length=120)
    worker_max_budget_usd: float = Field(default=2.0, ge=0.01, le=25.0)
    auto_repair_enabled: bool = True
    max_auto_repair_attempts: int = Field(default=1, ge=0, le=3)
    auto_repair_attempt_stale_minutes: int = Field(default=30, ge=1, le=1440)

    @field_validator("mode", mode="before")
    @classmethod
    def _yaml_bool_off(cls, v):
        if v is False:
            return "off"
        return v


class FlyerCreativePlannerConfig(BaseModel):
    """Bounded creative-planner settings (design: tasks/flyer-bounded-creative-
    planner-contract-design.md). Default OFF. Even when enabled, the planner stays
    inert until BOTH the firewall exists (slice 3) AND at least one category is
    enabled here — the per-category readiness gate an operator opens in slice 5
    after the spend-gated eval. See src/agents/flyer/creative_planner.py."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    # Per-category rollout gate (slice 5). Empty ⇒ planner inert even if enabled.
    # The operator opens categories one at a time after the creative-quality eval.
    enabled_categories: list[str] = Field(default_factory=list)


class FlyerConfig(BaseModel):
    """Hermes Flyer Studio settings. Default off; opt-in per customer."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    conversation_model: str = Field(default="default_hermes_gateway", min_length=1, max_length=120)
    prompt_model: str = Field(default="default_hermes_gateway", min_length=1, max_length=120)
    draft_image_model: str = Field(default="deterministic-renderer", min_length=1, max_length=120)
    draft_image_quality: FlyerImageQuality = "low"
    final_image_model: str = Field(default="deterministic-renderer", min_length=1, max_length=120)
    final_image_quality: FlyerImageQuality = "high"
    edit_image_model: str = Field(default="gpt-image-1", min_length=1, max_length=120)
    edit_image_quality: FlyerImageQuality = "medium"
    draft_provider_policy: FlyerDraftProviderPolicy = Field(default_factory=FlyerDraftProviderPolicy)
    final_provider_policy: FlyerFinalProviderPolicy = Field(default_factory=FlyerFinalProviderPolicy)
    source_edit_provider_policy: FlyerSourceEditProviderPolicy = Field(default_factory=FlyerSourceEditProviderPolicy)
    concept_count: int = Field(default=1, ge=1, le=3)
    max_revision_rounds: int = Field(default=6, ge=1, le=20)
    payment_provider: Literal["manual", "stripe", "razorpay", "other"] = "manual"
    payment_checkout_url_template: str = Field(default="", max_length=1000)
    quick_flyer_price_cents: int = Field(default=400, ge=1)
    quick_flyer_checkout_url_template: str = Field(default="", max_length=1000)
    plan_tiers: list["FlyerPlanTier"] = Field(default_factory=lambda: FlyerPlanTier.default_tiers(), min_length=1, max_length=10)
    final_formats: list[FlyerOutputFormat] = Field(
        default_factory=lambda: [
            "whatsapp_image",
            "instagram_post",
            "instagram_story",
            "printable_pdf",
        ],
        min_length=1,
        max_length=4,
    )
    recovery: FlyerRecoveryConfig = Field(default_factory=FlyerRecoveryConfig)
    creative_planner: FlyerCreativePlannerConfig = Field(default_factory=FlyerCreativePlannerConfig)

    @staticmethod
    def _legacy_provider_for_model(model: str) -> FlyerModelProviderName:
        return "local" if model.strip().lower() in {"", "deterministic-renderer", "pillow", "local-pillow"} else "openrouter"

    def resolve_draft_render_provider(self) -> FlyerRenderProviderConfig:
        if "draft_provider_policy" in self.model_fields_set:
            return self.draft_provider_policy.default
        return FlyerRenderProviderConfig(
            provider=self._legacy_provider_for_model(self.draft_image_model),
            model=self.draft_image_model,
            quality=self.draft_image_quality,
        )

    def resolve_final_render_provider(self) -> FlyerRenderProviderConfig:
        if "final_provider_policy" in self.model_fields_set:
            return self.final_provider_policy.default
        return FlyerRenderProviderConfig(
            provider=self._legacy_provider_for_model(self.final_image_model),
            model=self.final_image_model,
            quality=self.final_image_quality,
        )

    def resolve_source_edit_render_provider(self) -> FlyerRenderProviderConfig:
        if "source_edit_provider_policy" in self.model_fields_set:
            return self.source_edit_provider_policy.default
        if {"edit_image_model", "edit_image_quality"} & self.model_fields_set:
            return FlyerRenderProviderConfig(
                provider="openai",
                model=self.edit_image_model,
                quality=self.edit_image_quality,
            )
        return self.source_edit_provider_policy.emergency_fallback


class FlyerPlanTier(BaseModel):
    """Config-driven flyer subscription tier."""
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=120)
    monthly_price_usd: float = Field(ge=0)
    monthly_price_cents: Optional[int] = Field(default=None, ge=0)
    included_flyers: Optional[int] = Field(default=None, ge=1)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    description: str = Field(default="", max_length=300)

    def price_cents(self) -> int:
        if self.monthly_price_cents is not None:
            return self.monthly_price_cents
        return int(round(self.monthly_price_usd * 100))

    @classmethod
    def default_tiers(cls) -> list["FlyerPlanTier"]:
        return [
            cls(
                plan_id="trial",
                label="Free Trial",
                monthly_price_usd=0.00,
                monthly_price_cents=0,
                included_flyers=3,
                description="3 free sample flyers",
            ),
            cls(
                plan_id="starter",
                label="Starter",
                monthly_price_usd=49.99,
                monthly_price_cents=4999,
                included_flyers=30,
                description="30 flyers per month",
            ),
            cls(
                plan_id="growth",
                label="Growth",
                monthly_price_usd=69.99,
                monthly_price_cents=6999,
                included_flyers=60,
                description="60 flyers per month",
            ),
            cls(
                plan_id="unlimited",
                label="Unlimited",
                monthly_price_usd=199.00,
                monthly_price_cents=19900,
                included_flyers=None,
                description="Unlimited flyers per month",
            ),
        ]


class FlyerBrandAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(pattern=r"^B\d{4,}$")
    kind: Literal["logo", "template"]
    path: str = Field(min_length=1, max_length=500)
    mime_type: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    original_message_id: str = Field(min_length=1, max_length=200)
    received_at: datetime
    active: bool = True
    notes: str = Field(default="", max_length=500)

    @field_validator("path")
    @classmethod
    def _path_under_flyer_state(cls, v: str) -> str:
        if ".." in Path(v).parts:
            raise ValueError("brand asset path must not contain traversal")
        root = os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer/")
        try:
            path_resolved = Path(v).resolve()
            root_resolved = Path(root).resolve()
            path_resolved.relative_to(root_resolved)
        except ValueError:
            raise ValueError(f"brand asset path must be under {root}")
        return v


class FlyerUsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(pattern=r"^F\d{4,}$")
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    kind: Literal["reserved", "used", "released"]
    count: int = Field(default=1, ge=1, le=1)
    recorded_at: datetime
    message_id: str = Field(min_length=1, max_length=200)


class FlyerPaymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["manual", "stripe", "razorpay", "other"]
    payment_reference: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=1, max_length=40)
    amount_cents: Optional[int] = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    recorded_at: datetime


class FlyerGuestOrder(BaseModel):
    """Payment-first one-off flyer order for customers who skip onboarding."""
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^GUEST\d{4,}$")
    chat_id: str = Field(min_length=1, max_length=200)
    sender_phone: E164Phone
    status: Literal["pending_payment", "paid", "reserved", "used", "cancelled"] = "pending_payment"
    flyer_count_purchased: int = Field(default=1, ge=1, le=10)
    flyer_count_used: int = Field(default=0, ge=0, le=10)
    unit_price_cents: int = Field(default=400, ge=1)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    payment_provider: Literal["manual", "stripe", "razorpay", "other"] = "manual"
    payment_state: Literal["none", "checkout_missing", "checkout_ready", "payment_pending", "payment_confirmed", "activated"] = "payment_pending"
    payment_checkout_url: str = Field(default="", max_length=1000)
    payment_reference: str = Field(default="", max_length=200)
    payment_amount_cents: Optional[int] = Field(default=None, ge=0)
    original_message_id: str = Field(min_length=1, max_length=200)
    reserved_project_id: str = Field(default="", max_length=40)
    created_at: datetime
    updated_at: datetime
    paid_at: Optional[datetime] = None
    used_project_ids: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=500)

    def remaining(self) -> int:
        return max(0, self.flyer_count_purchased - self.flyer_count_used)

    def can_create_flyer(self) -> bool:
        return self.status == "paid" and self.remaining() > 0

    def can_finalize_project(self, project_id: str) -> bool:
        return (
            self.status == "reserved"
            and self.reserved_project_id == project_id
            and self.remaining() > 0
        )


FLYER_AUTHORIZED_REQUESTER_LIMIT = 2


class FlyerCatalogItem(BaseModel):
    """Dormant commerce seam (slice-3 backing). Catalog item identity + optional CTA /
    order-link binding to the src/platform/commerce primitives. Additive and
    default-empty in slice 1 — nothing reads it until the commerce loop ships, so it
    cannot change current behavior. See tasks/flyer-marketing-agent-design-2026-06-05.md."""
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    price_text: str = Field(default="", max_length=40)
    price_cents: Optional[int] = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    commerce_payment_link_id: str = Field(default="", max_length=120)
    order_url: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=120)
    is_featured: bool = False


class FlyerCustomerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    business_name: str = Field(min_length=1, max_length=160)
    business_address: str = Field(min_length=1, max_length=300)
    primary_chat_id: str = Field(default="", max_length=200)
    onboarded_by_phone: Optional[E164Phone] = None
    public_phone: E164Phone
    business_whatsapp_number: E164Phone
    authorized_request_numbers: list[E164Phone] = Field(
        default_factory=list,
        min_length=1,
        max_length=FLYER_AUTHORIZED_REQUESTER_LIMIT,
    )
    business_category: str = Field(default="", max_length=120)
    preferred_language: FlyerLanguage = "en"
    plan_id: str = Field(min_length=1, max_length=40)
    status: Literal["payment_pending", "trial", "active", "suspended", "cancelled"] = "payment_pending"
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime] = None
    plan_started_at: Optional[datetime] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    monthly_flyers_used: int = Field(default=0, ge=0)
    billing_provider: Literal["manual", "stripe", "razorpay", "other"] = "manual"
    payment_reference: str = Field(default="", max_length=200)
    payment_amount_cents: Optional[int] = Field(default=None, ge=0)
    payment_currency: str = Field(default="USD", min_length=3, max_length=3)
    payment_checkout_url: str = Field(default="", max_length=1000)
    pending_plan_id: str = Field(default="", max_length=40)
    pending_plan_checkout_url: str = Field(default="", max_length=1000)
    pending_plan_requested_at: Optional[datetime] = None
    pending_plan_payment_state: Literal["", "checkout_missing", "checkout_ready", "payment_pending", "payment_confirmed"] = ""
    pending_plan_amount_cents: Optional[int] = Field(default=None, ge=0)
    pending_plan_currency: str = Field(default="USD", min_length=3, max_length=3)
    pending_account_command: str = Field(default="", max_length=80)
    pending_account_value: str = Field(default="", max_length=300)
    pending_account_requested_by: Optional[E164Phone] = None
    pending_account_requested_at: Optional[datetime] = None
    notes: str = Field(default="", max_length=1000)
    allowed_location_labels: list[str] = Field(default_factory=list, max_length=25)
    location_restriction_enabled: bool = False
    trial_bonus_flyers: int = Field(default=0, ge=0, le=500)
    brand_assets: list[FlyerBrandAsset] = Field(default_factory=list, max_length=50)
    payment_records: list[FlyerPaymentRecord] = Field(default_factory=list, max_length=500)
    usage_events: list[FlyerUsageEvent] = Field(default_factory=list, max_length=5000)
    catalog: list[FlyerCatalogItem] = Field(default_factory=list, max_length=500)

    def is_authorized_sender(self, phone: Optional[str]) -> bool:
        if not phone:
            return False
        try:
            canonical = E164Phone.from_any(phone, country_code="US")
        except ValueError:
            return False
        return canonical == self.business_whatsapp_number or canonical in self.authorized_request_numbers

    def routable_phones(self) -> set[str]:
        phones: set[str] = {str(self.business_whatsapp_number), *[str(phone) for phone in self.authorized_request_numbers]}
        if self.onboarded_by_phone:
            phones.add(str(self.onboarded_by_phone))
        return phones

    def owned_phone_numbers(self) -> set[str]:
        phones = set(self.routable_phones())
        phones.add(str(self.public_phone))
        return phones

    def is_account_admin(self, phone: Optional[str], chat_id: str = "", sender_role: str = "") -> bool:
        if sender_role == "owner":
            return True
        admin_phones: set[str] = {str(self.business_whatsapp_number)}
        if self.onboarded_by_phone is not None:
            admin_phones.add(str(self.onboarded_by_phone))
        canonical = self._canonical_phone_string(phone)
        if canonical in admin_phones:
            return True
        chat_id = (chat_id or "").strip()
        if chat_id and self.primary_chat_id and chat_id == self.primary_chat_id:
            return True
        chat_phone = self._phone_from_chat_id(chat_id)
        return chat_phone in admin_phones

    @staticmethod
    def _canonical_phone_string(phone: Optional[str]) -> Optional[str]:
        if not phone:
            return None
        try:
            return str(E164Phone.from_any(phone, country_code="US"))
        except ValueError:
            return None

    @classmethod
    def _phone_from_chat_id(cls, chat_id: str) -> Optional[str]:
        if "@" not in chat_id:
            return None
        local, domain = chat_id.split("@", 1)
        if domain not in {"s.whatsapp.net", "c.us"}:
            return None
        local = local.split(":", 1)[0].strip()
        if not local.isdigit():
            return None
        return cls._canonical_phone_string(f"+{local}")

    def included_flyer_limit(self, plan_tiers: list["FlyerPlanTier"]) -> Optional[int]:
        for tier in plan_tiers:
            if tier.plan_id == self.plan_id:
                if tier.plan_id == "trial" and tier.included_flyers is not None:
                    return tier.included_flyers + self.trial_bonus_flyers
                return tier.included_flyers
        return None

    def usage_count_for_current_period(self) -> int:
        start = self.current_period_start
        end = self.current_period_end
        latest: dict[str, FlyerUsageEvent] = {}
        for event in self.usage_events:
            if start and event.recorded_at < start:
                continue
            if end and event.recorded_at >= end:
                continue
            previous = latest.get(event.reservation_id)
            if previous is None or event.recorded_at >= previous.recorded_at:
                latest[event.reservation_id] = event
        return sum(event.count for event in latest.values() if event.kind in {"reserved", "used"})

    def quota_remaining(self, plan_tiers: list["FlyerPlanTier"]) -> Optional[int]:
        limit = self.included_flyer_limit(plan_tiers)
        if limit is None:
            return None
        return max(0, limit - self.usage_count_for_current_period())

    def can_create_flyer(self, plan_tiers: list["FlyerPlanTier"]) -> bool:
        remaining = self.quota_remaining(plan_tiers)
        return remaining is None or remaining > 0


class FlyerOnboardingSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(min_length=1, max_length=200)
    sender_phone: Optional[E164Phone] = None
    status: FlyerOnboardingStatus
    started_at: datetime
    updated_at: datetime
    last_message_id: str = Field(default="", max_length=200)
    business_name: str = Field(default="", max_length=160)
    business_address: str = Field(default="", max_length=300)
    public_phone: Optional[E164Phone] = None
    business_whatsapp_number: Optional[E164Phone] = None
    authorized_request_number: Optional[E164Phone] = None
    business_category: str = Field(default="", max_length=120)
    preferred_language: FlyerLanguage = "en"
    creation_mode: str = Field(default="", max_length=20)
    plan_id: str = Field(default="", max_length=40)
    customer_id: str = Field(default="", max_length=40)
    pending_brand_assets: list[FlyerBrandAsset] = Field(default_factory=list, max_length=20)


class FlyerIntakeSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(min_length=1, max_length=200)
    sender_phone: Optional[E164Phone] = None
    status: FlyerIntakeStatus
    source: FlyerIntakeSource
    started_at: datetime
    updated_at: datetime
    last_message_id: str = Field(default="", max_length=200)
    preferred_language: FlyerLanguage = "en"
    creation_mode: str = Field(default="", max_length=20)
    mode_prompt_version: str = Field(default="", max_length=40)
    original_text: str = Field(default="", max_length=2000)
    goal: str = Field(default="", max_length=500)
    schedule: str = Field(default="", max_length=500)
    items: str = Field(default="", max_length=1500)
    location_contact: str = Field(default="", max_length=500)
    style_assets: str = Field(default="", max_length=500)
    reference_media_path: str = Field(default="", max_length=500)
    reference_media_message_id: str = Field(default="", max_length=200)
    brief_raw_request: str = Field(default="", max_length=3000)
    brief_display_request: str = Field(default="", max_length=1500)
    brief_source: Literal["", "sample", "guided", "text"] = ""
    brief_approved_at: Optional[datetime] = None
    brief_approved_message_id: str = Field(default="", max_length=200)


class FlyerCustomerStore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
    next_customer_sequence: int = Field(default=1, ge=1)
    next_brand_asset_sequence: int = Field(default=1, ge=1)
    customers: list[FlyerCustomerProfile] = Field(default_factory=list, max_length=5000)
    onboarding_sessions: list[FlyerOnboardingSession] = Field(default_factory=list, max_length=5000)
    intake_sessions: list[FlyerIntakeSession] = Field(default_factory=list, max_length=5000)
    starter_prompt_preferences: dict[str, Literal["auto", "off"]] = Field(default_factory=dict, max_length=5000)
    starter_prompt_sent_counts: dict[str, int] = Field(default_factory=dict, max_length=5000)

    def find_customer_by_phone(self, phone: Optional[str]) -> Optional[FlyerCustomerProfile]:
        if not phone:
            return None
        try:
            canonical = E164Phone.from_any(phone, country_code="US")
        except ValueError:
            return None
        matches = [customer for customer in self.customers if str(canonical) in customer.routable_phones()]
        return matches[0] if len(matches) == 1 else None

    def find_customer_by_sender(self, phone: Optional[str], chat_id: str) -> Optional[FlyerCustomerProfile]:
        customer = self.find_customer_by_phone(phone)
        if customer is not None:
            return customer
        if not chat_id:
            return None
        matches = [customer for customer in self.customers if customer.primary_chat_id == chat_id]
        return matches[0] if len(matches) == 1 else None

    def starter_prompt_mode(self, customer_id: str) -> Literal["auto", "off"]:
        return self.starter_prompt_preferences.get(customer_id, "auto")

    def set_starter_prompt_mode(self, customer_id: str, mode: Literal["auto", "off"]) -> None:
        if mode == "auto":
            self.starter_prompt_preferences.pop(customer_id, None)
            self.starter_prompt_sent_counts.pop(customer_id, None)
            return
        self.starter_prompt_preferences[customer_id] = mode

    def claim_starter_prompt_send(self, customer_id: str) -> bool:
        if self.starter_prompt_mode(customer_id) == "off":
            return False
        current = int(self.starter_prompt_sent_counts.get(customer_id, 0) or 0)
        if current > 0:
            return False
        self.starter_prompt_sent_counts[customer_id] = 1
        return True

    def release_starter_prompt_claim(self, customer_id: str) -> None:
        current = int(self.starter_prompt_sent_counts.get(customer_id, 0) or 0)
        if current <= 1:
            self.starter_prompt_sent_counts.pop(customer_id, None)
            return
        self.starter_prompt_sent_counts[customer_id] = current - 1

    def customer_ids_for_phone(self, phone: Optional[str], *, exclude_customer_id: str = "") -> list[str]:
        if not phone:
            return []
        try:
            canonical = E164Phone.from_any(phone, country_code="US")
        except ValueError:
            return []
        return [
            customer.customer_id
            for customer in self.customers
            if customer.customer_id != exclude_customer_id and str(canonical) in customer.owned_phone_numbers()
        ]

    def find_customer_by_id(self, customer_id: str) -> Optional[FlyerCustomerProfile]:
        for customer in self.customers:
            if customer.customer_id == customer_id:
                return customer
        return None

    def find_session(self, chat_id: str, phone: Optional[str]) -> Optional[FlyerOnboardingSession]:
        canonical: Optional[str] = None
        if phone:
            try:
                canonical = E164Phone.from_any(phone, country_code="US")
            except ValueError:
                canonical = None
        if canonical:
            for session in self.onboarding_sessions:
                if session.sender_phone == canonical:
                    return session
            for session in self.onboarding_sessions:
                if session.sender_phone is None and session.chat_id == chat_id:
                    return session
            return None
        for session in self.onboarding_sessions:
            if session.sender_phone is None and session.chat_id == chat_id:
                return session
        return None

    def find_intake_session(self, chat_id: str, phone: Optional[str]) -> Optional[FlyerIntakeSession]:
        canonical: Optional[str] = None
        if phone:
            try:
                canonical = E164Phone.from_any(phone, country_code="US")
            except ValueError:
                canonical = None
        if canonical:
            for session in self.intake_sessions:
                if session.sender_phone == canonical:
                    return session
            for session in self.intake_sessions:
                if session.sender_phone is None and session.chat_id == chat_id:
                    return session
            return None
        for session in self.intake_sessions:
            if session.sender_phone is None and session.chat_id == chat_id:
                return session
        return None

    def replace_intake_session(self, session: FlyerIntakeSession) -> None:
        self.intake_sessions = [
            s for s in self.intake_sessions
            if s.chat_id != session.chat_id and s.sender_phone != session.sender_phone
        ]
        self.intake_sessions.append(session)

    def discard_intake_session(self, session: FlyerIntakeSession) -> None:
        self.intake_sessions = [
            s for s in self.intake_sessions
            if s.chat_id != session.chat_id and s.sender_phone != session.sender_phone
        ]

    def new_customer(
        self,
        *,
        business_name: str,
        business_address: str,
        public_phone: str,
        business_whatsapp_number: str,
        authorized_request_number: str,
        business_category: str,
        preferred_language: str,
        plan_id: str,
        now: datetime,
        billing_provider: str = "manual",
        payment_checkout_url: str = "",
        primary_chat_id: str = "",
        onboarded_by_phone: Optional[str] = None,
    ) -> FlyerCustomerProfile:
        language = preferred_language if preferred_language in {"en", "te", "hi", "ml", "ta", "kn", "gu", "mr", "pa", "es", "mixed", "other"} else "en"
        provider = billing_provider if billing_provider in {"manual", "stripe", "razorpay", "other"} else "manual"
        candidate_phones = [
            public_phone,
            business_whatsapp_number,
            authorized_request_number,
            *([onboarded_by_phone] if onboarded_by_phone else []),
        ]
        conflicts: set[str] = set()
        for phone in candidate_phones:
            conflicts.update(self.customer_ids_for_phone(phone))
        if conflicts:
            raise ValueError(f"phone number already belongs to customer: {', '.join(sorted(conflicts))}")
        customer = FlyerCustomerProfile(
            customer_id=f"CUST{self.next_customer_sequence:04d}",
            business_name=business_name,
            business_address=business_address,
            primary_chat_id=primary_chat_id,
            onboarded_by_phone=E164Phone.from_any(onboarded_by_phone, country_code="US") if onboarded_by_phone else None,
            public_phone=E164Phone.from_any(public_phone, country_code="US"),
            business_whatsapp_number=E164Phone.from_any(business_whatsapp_number, country_code="US"),
            authorized_request_numbers=[E164Phone.from_any(authorized_request_number, country_code="US")],
            business_category=business_category,
            preferred_language=language,  # type: ignore[arg-type]
            plan_id=plan_id,
            status="payment_pending",
            created_at=now,
            updated_at=now,
            billing_provider=provider,  # type: ignore[arg-type]
            payment_checkout_url=payment_checkout_url,
        )
        self.next_customer_sequence += 1
        return customer


class FlyerGuestOrderStore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
    next_guest_order_sequence: int = Field(default=1, ge=1)
    orders: list[FlyerGuestOrder] = Field(default_factory=list, max_length=20000)

    def next_order_id(self) -> str:
        return f"GUEST{self.next_guest_order_sequence:04d}"

    def find_open_order_by_sender(self, phone: Optional[str], chat_id: str = "") -> Optional[FlyerGuestOrder]:
        if not phone:
            return None
        try:
            canonical = E164Phone.from_any(phone, country_code="US")
        except ValueError:
            return None
        statuses = {"pending_payment", "paid", "reserved"}
        matches = [
            order for order in self.orders
            if order.status in statuses and order.sender_phone == canonical and (not chat_id or order.chat_id == chat_id)
        ]
        if not matches and chat_id:
            matches = [
                order for order in self.orders
                if order.status in statuses and order.sender_phone == canonical
            ]
        if not matches:
            return None
        return max(matches, key=lambda order: order.updated_at)

    def find_paid_order_by_sender(self, phone: Optional[str], chat_id: str = "") -> Optional[FlyerGuestOrder]:
        order = self.find_open_order_by_sender(phone, chat_id)
        if order and order.can_create_flyer():
            return order
        return None

    def find_order_by_id(self, order_id: str) -> Optional[FlyerGuestOrder]:
        for order in self.orders:
            if order.order_id == order_id:
                return order
        return None

    def new_order(
        self,
        *,
        sender_phone: str,
        chat_id: str,
        message_id: str,
        now: datetime,
        unit_price_cents: int = 400,
        currency: str = "USD",
        payment_provider: str = "manual",
        checkout_url: str = "",
    ) -> FlyerGuestOrder:
        provider = payment_provider if payment_provider in {"manual", "stripe", "razorpay", "other"} else "manual"
        order = FlyerGuestOrder(
            order_id=self.next_order_id(),
            chat_id=chat_id,
            sender_phone=E164Phone.from_any(sender_phone, country_code="US"),
            unit_price_cents=unit_price_cents,
            currency=currency,
            payment_provider=provider,  # type: ignore[arg-type]
            payment_state="payment_pending" if checkout_url else "checkout_missing",
            payment_checkout_url=checkout_url,
            original_message_id=message_id,
            created_at=now,
            updated_at=now,
        )
        self.next_guest_order_sequence += 1
        return order


class FlyerRequestFields(BaseModel):
    """LLM-extracted flyer requirements. Extras are ignored by design."""
    model_config = ConfigDict(extra="ignore")
    event_or_business_name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    event_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    event_time: Optional[str] = Field(default=None, max_length=80)
    venue_or_location: Optional[str] = Field(default=None, min_length=1, max_length=240)
    contact_info: Optional[str] = Field(default=None, min_length=1, max_length=200)
    preferred_language: FlyerLanguage = "en"
    style_preference: str = Field(default="", max_length=500)
    output_formats: list[FlyerOutputFormat] = Field(default_factory=list, max_length=4)
    notes: str = Field(default="", max_length=2000)

    @field_validator("event_date")
    @classmethod
    def _validate_calendar_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            datetime.fromisoformat(v).date()
        except ValueError as e:
            raise ValueError(f"event_date must be a valid ISO date: {v!r} ({e})") from e
        return v

    def missing_required_fields(self) -> list[str]:
        if self._has_template_reference():
            return [name for name in ["event_or_business_name"] if not (getattr(self, name) or "").strip()]
        if self._has_product_or_brand_promo():
            return [
                name for name in ["event_or_business_name"]
                if not (getattr(self, name) or "").strip()
            ]
        if self._has_price_list_or_menu():
            return [
                name for name in ["event_or_business_name", "contact_info"]
                if not (getattr(self, name) or "").strip()
            ]
        required = [
            "event_or_business_name",
            "event_time",
            "venue_or_location",
            "contact_info",
        ]
        if not self._has_recurring_schedule():
            required.insert(1, "event_date")
        return [name for name in required if not (getattr(self, name) or "").strip()]

    def _has_recurring_schedule(self) -> bool:
        text = f"{self.notes} {self.style_preference}".lower()
        return any(
            marker in text
            for marker in (
                "weekend",
                "saturday",
                "sunday",
                "daily",
                "weekday",
                "weekdays",
                "every ",
                "starts from",
                "start from",
            )
        )

    def _has_price_list_or_menu(self) -> bool:
        text = f"{self.notes} {self.style_preference}".lower()
        has_price_or_menu = "$" in text or any(
            marker in text
            for marker in (
                "menu item",
                "menu items",
                "items",
                "price",
                "combo",
                "/piece",
                "/lb",
                "tray",
                "special",
                "offer",
                "deal",
            )
        )
        has_service_list = bool(re.search(
            r"\b(?:services?|social media marketing|performance marketing|seo|aeo|geo|"
            r"ai marketing|content creation|paid ads|digital marketing|marketing services?)\b",
            text,
        ))
        return has_price_or_menu or has_service_list

    def _has_product_or_brand_promo(self) -> bool:
        text = f"{self.notes} {self.style_preference}".lower()
        if not re.search(r"\b(?:flyer|flier|poster|banner)\b", text):
            return False
        return bool(re.search(
            r"\b(?:hero image|tagline|badge|badges|certified|brand|branding|"
            r"product|featuring|premium|organic-style|organic style|grocery aesthetic)\b",
            text,
        ))

    def _has_template_reference(self) -> bool:
        text = f"{self.notes} {self.style_preference}".lower()
        return any(
            marker in text
            for marker in ("uploaded template", "uploaded reference", "reference image")
        )


class FlyerLockedFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    source: FlyerFactSource
    required: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_project_id: str = Field(default="", max_length=40)
    source_asset_id: str = Field(default="", max_length=40)
    source_message_id: str = Field(default="", max_length=200)
    source_sha256: str = Field(default="", max_length=64)


class FlyerSourceContractSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(default="", max_length=160)
    items: list[str] = Field(default_factory=list, max_length=50)


class FlyerSourceContract(BaseModel):
    """Strict-shape source-contract for the F0061 exact-edit class.

    Vision/LLM raw output is parsed permissively then projected into this
    schema (extra="forbid") so downstream QA + locked-fact generation can
    rely on bounded fields.
    """
    model_config = ConfigDict(extra="forbid")
    source_business_names: list[str] = Field(default_factory=list, max_length=10)
    target_business_name: str = Field(default="", max_length=160)
    required_headings: list[str] = Field(default_factory=list, max_length=20)
    required_text: list[str] = Field(default_factory=list, max_length=100)
    sections: list[FlyerSourceContractSection] = Field(default_factory=list, max_length=20)
    requested_replacements: dict[str, str] = Field(default_factory=dict, max_length=50)
    forbidden_substrings: list[str] = Field(default_factory=list, max_length=50)
    preserve_layout: bool = False
    preserve_unmentioned_text: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=1000)


class FlyerReferenceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=40)
    role: FlyerReferenceRole
    provider: str = Field(default="", max_length=120)
    status: FlyerReferenceExtractionStatus = "not_run"
    extracted_facts: list[FlyerLockedFact] = Field(default_factory=list, max_length=100)
    detail: str = Field(default="", max_length=500)
    extracted_at: Optional[datetime] = None
    source_contract: Optional[FlyerSourceContract] = None


class FlyerVisualQAReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=40)
    asset_id: str = Field(default="", max_length=40)
    artifact_path: str = Field(min_length=1, max_length=500)
    artifact_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    project_version: int = Field(ge=1)
    output_format: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=120)
    qa_source: FlyerVisualQASource
    status: FlyerVisualQAStatus
    blockers: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    extracted_text: str = Field(default="", max_length=5000)
    checked_at: datetime
    # P0 #2 2026-05-28 — severity-tiered QA. Defaults to "pass" for
    # backward-compat on existing on-disk reports written before this PR.
    # Populated by classify_qa_severity() in visual_qa.run_visual_qa().
    severity: Literal["pass", "warn", "block"] = "pass"


class FlyerWarningSummary(BaseModel):
    """P0 #2 2026-05-28 — warn-tier delivery outcome record.

    Lifecycle: populated by generate-flyer-concepts when severity == 'warn'.
    Reflects the MOST RECENT QA outcome only; replaced (not merged) on
    re-QA per design §9 Q3. Audit log preserves history via the
    FlyerWarnTierDelivered audit row variant.

    Independent of FlyerManualReview (which stays bound to
    manual_edit_required state — operator-action-pending queue primitive).
    Warning-summary is autonomous-delivery-with-caveats — different
    consumers, different lifecycles."""
    model_config = ConfigDict(extra="forbid")
    severity: Literal["warn"]
    blockers: list[str] = Field(default_factory=list, max_length=50)
    customer_text: str = Field(default="", max_length=2000)
    customer_text_sha256: str = Field(default="", max_length=64)
    delivered_at: datetime
    asset_id: str = Field(default="", max_length=80)
    classifier_version: str = Field(default="v1", max_length=20)


class FlyerManualReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: FlyerManualReviewStatus = "none"
    reason: str = Field(default="", max_length=120)
    reason_code: FlyerManualReviewReason = "unclassified"
    detail: str = Field(default="", max_length=500)
    queued_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    operator_asset_ids: list[str] = Field(default_factory=list, max_length=20)
    break_glass_reason: str = Field(default="", max_length=500)
    # Multi-admin coordination (cockpit-set). Self-reported admin handle, not an
    # authenticated identity — the cockpit shares a single owner login, so this
    # is a coordination label ("who is working this case") to prevent two admins
    # silently working the same row. All changes go through audited claim/unclaim
    # /assign endpoints. Empty = unclaimed.
    claimed_by: str = Field(default="", max_length=60)
    claimed_at: Optional[datetime] = None


class FlyerAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(pattern=r"^A\d{4,}$")
    kind: FlyerAssetKind
    source: Literal["whatsapp", "generated", "rendered", "uploaded"]
    path: str = Field(min_length=1, max_length=500)
    mime_type: str = Field(min_length=1, max_length=120)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    original_message_id: str = Field(default="", max_length=200)
    received_at: datetime
    delivery_status: FlyerAssetDeliveryStatus = "pending"
    outbound_message_id: str = Field(default="", max_length=200)
    delivered_at: Optional[datetime] = None
    delivery_attempt_count: int = Field(default=0, ge=0)
    delivery_error: str = Field(default="", max_length=500)

    @field_validator("path")
    @classmethod
    def _path_under_flyer_state(cls, v: str) -> str:
        if ".." in Path(v).parts:
            raise ValueError("asset path must not contain traversal")
        root = os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer/")
        try:
            path_resolved = Path(v).resolve()
            root_resolved = Path(root).resolve()
            path_resolved.relative_to(root_resolved)
        except ValueError:
            raise ValueError(f"asset path must be under {root}")
        return v


class FlyerConcept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept_id: str = Field(pattern=r"^C[1-3]$")
    title: str = Field(min_length=1, max_length=120)
    style_summary: str = Field(min_length=1, max_length=500)
    preview_asset_id: str = Field(pattern=r"^A\d{4,}$")
    prompt: str = Field(default="", max_length=4000)
    created_at: datetime
    selected_at: Optional[datetime] = None


class FlyerRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(pattern=r"^R\d{3,}$")
    message_id: str = Field(min_length=1, max_length=200)
    requested_at: datetime
    request_text: str = Field(min_length=1, max_length=2000)
    applied: bool = False
    resulting_version: Optional[int] = Field(default=None, ge=1)


class FlyerRevisionPatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_updates: dict[str, str] = Field(default_factory=dict)
    notes_update: Optional[str] = None
    raw_request_update: Optional[str] = None
    changed: bool = False
    visual_only: bool = False
    ambiguous: bool = False
    unresolved_reason: str = ""
    requires_confirmation: bool = False
    confirmation_reason: str = ""
    replace_old_text: str = Field(default="", max_length=500)
    replace_new_text: str = Field(default="", max_length=500)
    price_delta_cents: int = 0
    already_applied: bool = False
    pending_confirmation_message: str = Field(default="", max_length=2000)


class FlyerPendingRevisionConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(pattern=r"^R\d{3,}$")
    created_at: datetime
    expires_at: datetime
    request_message_id: str = Field(min_length=1, max_length=200)
    request_text: str = Field(min_length=1, max_length=2000)
    proposal_summary: str = Field(min_length=1, max_length=1200)
    patch: FlyerRevisionPatchPayload


class FlyerBrandKit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    customer_phone: E164Phone
    logos: list[FlyerAsset] = Field(default_factory=list, max_length=10)
    colors: list[Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]] = Field(default_factory=list, max_length=10)
    fonts: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=10)
    recurring_contact_info: str = Field(default="", max_length=500)
    preferred_language: FlyerLanguage = "en"
    prior_style_notes: str = Field(default="", max_length=2000)
    updated_at: Optional[datetime] = None


class FlyerProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(pattern=r"^F\d{4,}$")
    status: FlyerWorkflowStatus
    customer_phone: E164Phone
    customer_id: str = Field(default="", max_length=40)
    chat_id: str = Field(default="", max_length=200)
    created_at: datetime
    updated_at: datetime
    original_message_id: str = Field(min_length=1, max_length=200)
    raw_request: str = Field(min_length=1, max_length=2000)
    fields: FlyerRequestFields = Field(default_factory=FlyerRequestFields)
    locked_facts: list[FlyerLockedFact] = Field(default_factory=list, max_length=100)
    reference_extractions: list[FlyerReferenceExtraction] = Field(default_factory=list, max_length=20)
    qa_reports: list[FlyerVisualQAReport] = Field(default_factory=list, max_length=100)
    manual_review: FlyerManualReview = Field(default_factory=FlyerManualReview)
    assets: list[FlyerAsset] = Field(default_factory=list, max_length=50)
    concepts: list[FlyerConcept] = Field(default_factory=list, max_length=3)
    selected_concept_id: Optional[str] = Field(default=None, pattern=r"^C[1-3]$")
    revisions: list[FlyerRevision] = Field(default_factory=list, max_length=50)
    pending_revision_confirmation: Optional[FlyerPendingRevisionConfirmation] = None
    last_applied_pending_revision_id: str = Field(default="", max_length=20)
    version: int = Field(default=1, ge=1)
    final_asset_ids: list[str] = Field(default_factory=list, max_length=4)
    approved_message_id: str = Field(default="", max_length=200)
    # P0 #2 2026-05-28 — warn-tier outcome payload. None for projects in
    # any state other than `delivered_with_warning`. Replaced (not merged)
    # on re-QA per design §9 Q3; cleared to None when severity returns to
    # 'pass' on the next QA pass.
    warning: Optional[FlyerWarningSummary] = None

    @model_validator(mode="after")
    def _selected_concept_must_exist(self) -> "FlyerProject":
        if self.selected_concept_id:
            concept_ids = {concept.concept_id for concept in self.concepts}
            if self.selected_concept_id not in concept_ids:
                raise ValueError("selected_concept_id must reference an existing concept")
        return self


class FlyerProjectStore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
    next_sequence: int = Field(default=1, ge=1)
    projects: list[FlyerProject] = Field(default_factory=list)


FlyerRepairMode = Literal["hermes_regenerate"]
FlyerRepairStatus = Literal["attempted", "succeeded", "exhausted", "skipped", "stale"]


class FlyerRepairAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(pattern=r"^F\d{4,}$")
    project_version: int = Field(ge=1)
    mode: FlyerRepairMode = "hermes_regenerate"
    status: FlyerRepairStatus
    qa_blocker_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    repair_instruction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    repair_instruction: str = Field(default="", max_length=1000)
    started_at: datetime
    completed_at: Optional[datetime] = None
    generated_asset_ids: list[str] = Field(default_factory=list, max_length=10)
    detail: str = Field(default="", max_length=1000)


class FlyerAutoRepairAttemptStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, ge=1)
    attempts: list[FlyerRepairAttempt] = Field(default_factory=list, max_length=20000)


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


class CateringSelectedItem(BaseModel):
    """One item in a customer-finalized catering menu (PR-CF1).

    Whole-dollar prices to mirror CateringLeadExtractedFields.budget_hint_usd
    convention and avoid float-rounding error on `qty * price` accumulation
    in finalize-catering-menu's server recompute.
    """
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    qty: int = Field(ge=1, le=500)
    price_usd: int = Field(ge=0)


class CateringLead(BaseModel):
    """One catering lead — full lifecycle from inquiry to closure.

    quote_text contract (PR-CF1): populated ONLY by apply-catering-owner-decision
    on owner approve. Pre-approval lifecycle (NEW, AWAITING_OWNER_APPROVAL,
    CUSTOMER_FINALIZED, OWNER_EDITED) uses the F14 proposal template rendered
    by create-catering-lead. finalize-catering-menu does NOT write quote_text.
    """
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

    # PR-CF1: customer-finalized menu fields. All default-empty so legacy
    # leads (pre-finalize) decode unchanged. Set only by finalize-catering-menu
    # under FileLock(LEADS_LOCK).
    selected_items: list[CateringSelectedItem] = Field(
        default_factory=list, max_length=50,
        description="Customer-curated menu at finalize time",
    )
    quote_total_usd: Optional[int] = Field(
        default=None, ge=0,
        description="Server-recomputed sum(qty*price). Source of truth; "
                    "LLM-passed total is cross-check only.",
    )
    customer_finalized_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of first finalize. Re-finalize via different "
                    "customer_message_id refreshes this; replay does not.",
    )
    last_finalize_message_id: Optional[str] = Field(
        default=None, min_length=1, max_length=200,
        description="Idempotency anchor — bridge messageId of customer's "
                    "finalize message. Same id seen twice => no-op replay.",
    )

    # Slice-2 deposit caller — orthogonal to lead.status (PR feat/commerce-
    # slice2-catering-deposit-caller). Lead.status stays SENT_TO_CUSTOMER after
    # quote+deposit are sent; deposit_status tracks the deposit lifecycle
    # independently. Slice-3 webhook receiver will flip deposit_status to "paid"
    # and the catering follow-up agent (or operator) decides when to advance
    # lead.status further (CONFIRMED in a future slice).
    #
    # Design choice: NO `catering_lead_status_change` row is emitted when these
    # fields land. `catering_deposit_link_sent` IS the canonical audit row for
    # deposit transitions. Documented per A-MEDIUM-2 deferral.
    deposit_required: bool = False
    deposit_amount_cents: int = Field(default=0, ge=0, le=10_000_000_000)
    deposit_commerce_order_id: str = Field(default="", max_length=40)
    deposit_payment_intent_id: str = Field(default="", max_length=40)
    deposit_payment_reference: str = Field(default="", max_length=200)
    deposit_status: Literal[
        "none",             # default — no deposit required for this lead
        "unconfigured",     # threshold met but checkout_url_template empty
        "awaiting_payment",
        "paid",             # slice-3 webhook will set this
        "voided",           # operator cancelled deposit
        "refunded",         # slice-3+
    ] = "none"
    deposit_minted_at: Optional[datetime] = None

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
        post_awaiting = {
            "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED",  # PR-CF1
            "OWNER_APPROVED", "OWNER_EDITED", "SENT_TO_CUSTOMER",
        }
        if status in post_awaiting and not (data.get("quote_text", "") or "").strip():
            sys.stderr.write(
                f"WARN: legacy quote_text=empty on lead_id={data.get('lead_id')!r} "
                f"status={status!r}; backfilling with sentinel.\n"
            )
            data["quote_text"] = LEGACY_QUOTE_TEXT_SENTINEL
        return data

    @model_validator(mode="after")
    def _quote_required_post_awaiting(self) -> "CateringLead":
        post_awaiting = {
            "AWAITING_OWNER_APPROVAL", "CUSTOMER_FINALIZED",  # PR-CF1
            "OWNER_APPROVED", "OWNER_EDITED", "SENT_TO_CUSTOMER",
        }
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


CateringProposalStatus = Literal[
    "DRAFT", "SENT", "SEND_FAILED", "SUPERSEDED",
    "SELECTING", "SELECTED", "SELECTED_OWNER_CARD_FAILED", "SELECT_FAILED",
]

CateringProposalTier = Literal["classic", "balanced", "premium"]


class CateringProposalOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str = Field(pattern=r"^[1-3]$")
    style_key: str = Field(min_length=1, max_length=80)
    tier: CateringProposalTier
    item_names: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=20
    )


class CateringProposalSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_set_id: str = Field(pattern=r"^CPS-L[0-9]{4,}-[0-9]{6}$")
    lead_id: str = Field(pattern=r"^L[0-9]{4,}$")
    status: CateringProposalStatus
    created_at: datetime
    sent_at: Optional[datetime] = None
    outbound_message_id: str = ""
    source_message_id: str = Field(min_length=1, max_length=200)
    request_text: str = Field(default="", max_length=1000)
    options: list[CateringProposalOption] = Field(min_length=2, max_length=3)
    selected_option_id: Optional[str] = Field(default=None, pattern=r"^[1-3]$")
    failure_reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _validate_proposal_set(self) -> "CateringProposalSet":
        option_ids = [option.option_id for option in self.options]
        option_id_set = set(option_ids)
        if len(option_ids) != len(option_id_set):
            raise ValueError("proposal set option_id values must be unique")
        if (
            self.selected_option_id is not None
            and self.selected_option_id not in option_id_set
        ):
            raise ValueError("selected_option_id must reference an existing option")
        if self.status == "SENT" and not self.outbound_message_id.strip():
            raise ValueError("SENT proposal set requires outbound_message_id")
        if self.status == "SENT" and self.sent_at is None:
            raise ValueError("SENT proposal set requires sent_at")
        return self


class CateringProposalStore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: int = Field(default=1, ge=1)
    next_sequence: int = Field(default=1, ge=1)
    sets: list[CateringProposalSet] = Field(default_factory=list)


CateringLearningSource = Literal["catering-pattern-report"]


class CateringLearningProposalHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sent: int = Field(default=0, ge=0)
    selected: int = Field(default=0, ge=0)
    send_failed: int = Field(default=0, ge=0)
    select_failed: int = Field(default=0, ge=0)


class CateringLearningSummary(BaseModel):
    """Counts-only catering learning summary for owner-facing brief rendering.

    Deliberately excludes raw/sanitized off-menu text. Those fields originate
    from LLM-extracted free text and can contain PII or price/payment terms.
    """
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    source: CateringLearningSource = "catering-pattern-report"
    generated_at: datetime
    window_days: int = Field(ge=1, le=365)
    proposal_health: CateringLearningProposalHealth = Field(
        default_factory=CateringLearningProposalHealth,
    )
    off_menu_request_count: int = Field(default=0, ge=0)
    leads_with_off_menu_count: int = Field(default=0, ge=0)
    active_missing_info_count: int = Field(default=0, ge=0)
    menu_updated_at: Optional[datetime] = None
    menu_freshness_days: Optional[int] = Field(default=None, ge=0)
    degraded_sources: list[Literal["log", "leads", "proposals", "menu"]] = Field(
        default_factory=list, max_length=4,
    )


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

    # ───── PR-Agent3-v0.1 (2026-05-04) — geo + delivery-radius fields ─────
    # All optional / defaulted so existing customer configs that don't
    # populate them continue to validate. Used by closest-location.py
    # (productivity/maps OSRM wrapper) and reserved for v0.2 service-area
    # validation. service_radius_minutes is unused in v0.1; kept now so
    # the v0.2 PR doesn't need a second migration.
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    phone: Optional[E164Phone] = None
    hours: Optional[str] = Field(default=None, max_length=200)
    service_radius_minutes: float = Field(default=30.0, ge=0.0, le=240.0)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"invalid IANA timezone {v!r}: {e}")
        return v


# ─────────────────────────────────────────────────────────────────
# Commerce primitives — slice 1 (tasks/hermes-commerce-prd-v2.md)
# Shared deterministic substrate; NOT a new agent. Callable by Catering,
# Flyer, future order/upsell/loyalty agents. Opt-in via cfg.commerce.enabled.
# ─────────────────────────────────────────────────────────────────

_COMMERCE_LOCKED_BLOCKED_CATEGORIES = frozenset({
    "alcohol", "tobacco", "age_gated", "live_animals",
})


class CommerceConfig(BaseModel):
    """Hermes Commerce primitive settings. Default off; opt-in per customer."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    # Compliance / category gates (PRD v2 §6 enforcement mechanism)
    allow_restricted_categories: bool = False
    permanently_blocked_categories: tuple[str, ...] = (
        "alcohol", "tobacco", "age_gated", "live_animals",
    )
    per_vps_excluded_categories: tuple[str, ...] = ()
    # Approval threshold — fail-closed (Reviewer B HIGH-3): None means UNCONFIGURED.
    # Callers that invoke the approval-gated path without operator config raise.
    owner_approval_amount_cents_threshold: Optional[int] = Field(default=None, ge=0)
    # Payment-link template (slice 1: placeholder substitution only).
    # Empty -> assert_payment_url_renderable raises; callers MUST emit
    # "Payment link is not configured yet" copy.
    payment_checkout_url_template: str = Field(default="", max_length=1000)
    # Slice-2 minimum-deposit floor (Reviewer B MEDIUM-2). Below this amount,
    # callers (e.g. catering deposit caller) refuse to mint a payment intent
    # rather than producing an unactionable provider link. Default $5.00 covers
    # Stripe + Razorpay + UPI minimum-charge thresholds in most regions.
    minimum_deposit_cents: int = Field(default=500, ge=0, le=10_000_000)

    # Slice-3 PR-1 provider integration (PR feat/commerce-slice3-pr1-provider-abstraction).
    # `provider="placeholder"` (default) preserves slice-2 template substitution.
    # `provider="stripe"` calls Stripe API to mint real Payment Links.
    # Other values are reserved in the schema (razorpay/upi/zelle/cashapp/manual)
    # but reject at runtime in slice-1 primitive's mint() until wired.
    provider: Literal["placeholder", "stripe", "razorpay", "upi", "zelle", "cashapp", "manual"] = "placeholder"
    # MCP vs direct SDK — slice-3 PR-1 ships SDK only; MCP path deferred to a
    # slice-3.1 PR gated on Stripe MCP tool-surface verification (Reviewer A-HIGH-1).
    provider_mode: Literal["sdk", "mcp"] = "sdk"
    # Operator-controlled webhook subscription name (for runbook + smoke gate
    # in slice-3 PR-2 — `hermes webhook list` is checked at deploy time to
    # assert this subscription is present per Reviewer A-LOW-1).
    webhook_subscription_name: str = Field(default="stripe-commerce-payments", max_length=80)
    # Customer-visible confirmation reply opt-in (PR-2 reconciler skips the
    # reply when False; useful if operator wants to handle confirmation
    # manually). Default True preserves the design's customer-friendly default.
    send_payment_confirmation_reply: bool = True
    # Live vs test mode safety gate (Reviewer B-MEDIUM-1). Operator sets this
    # explicitly per VPS. Slice-3 PR-3 runbook adds a smoke check that calls
    # stripe.Account.retrieve().livemode and asserts it matches this flag.
    # Catches the "live key in test cfg" footgun before any customer pays.
    stripe_livemode_expected: bool = False

    @model_validator(mode="after")
    def _enforce_locked_blocked_categories(self) -> "CommerceConfig":
        """Reviewer B LOW-2: locked Meta-policy categories cannot be removed.

        An operator cannot enable alcohol/tobacco/age_gated/live_animals via
        config alone — they would have to also add an explicit
        BlockedCategoryOverride model with audit row. PRD v2 §6.
        """
        missing = _COMMERCE_LOCKED_BLOCKED_CATEGORIES - set(self.permanently_blocked_categories)
        if missing:
            raise ValueError(
                f"permanently_blocked_categories must include all locked "
                f"categories; missing: {sorted(missing)}. Use a "
                f"BlockedCategoryOverride model + audit row to remove."
            )
        return self


class CommerceCartItem(BaseModel):
    """A single line in a CommerceCart. Slice 1: integer quantities only."""
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=10_000)
    unit: Literal["each", "lb", "kg", "tray", "platter", "gal", "qt"]
    unit_price_cents: int = Field(ge=1, le=10_000_000)
    line_total_cents: int = Field(ge=1, le=10_000_000_000)
    added_at: datetime


class CommerceCart(BaseModel):
    """Per-(sender, chat) cart state. 4h idle TTL refreshed on every mutation."""
    model_config = ConfigDict(extra="forbid")
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, max_length=120)
    chat_id: str = Field(min_length=1, max_length=200)
    items: list[CommerceCartItem] = Field(default_factory=list, max_length=50)
    subtotal_cents: int = Field(ge=0, le=10_000_000_000)
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR"]
    status: Literal["open", "checked_out", "expired", "cleared"]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def _require_sender_identity(self) -> "CommerceCart":
        if self.sender_phone is None and self.sender_lid is None:
            raise ValueError(
                "CommerceCart requires at least one of sender_phone or sender_lid"
            )
        return self


class CommerceCartStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carts: list[CommerceCart] = Field(default_factory=list, max_length=10_000)


CommerceOrderStatus = Literal[
    "pending_payment",
    "awaiting_approval",
    "paid",
    "preparing",
    "ready",
    "out_for_delivery",
    "completed",
    "cancelled",
    "voided",
    "refunded",
]


class CommerceOrderStatusEvent(BaseModel):
    """Append-only status-history entry; typed for slice 1 schema discipline."""
    model_config = ConfigDict(extra="forbid")
    from_status: Optional[CommerceOrderStatus] = None
    to_status: CommerceOrderStatus
    ts: datetime
    cause: str = Field(min_length=1, max_length=200)
    actor: Literal["customer", "caller", "operator", "cron", "webhook"]
    event_ref: str = Field(default="", max_length=120)


class CommerceOrder(BaseModel):
    """Order state machine instance. status_history is append-only."""
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, max_length=120)
    chat_id: str = Field(min_length=1, max_length=200)
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    line_items: list[CommerceCartItem] = Field(default_factory=list, max_length=50)
    subtotal_cents: int = Field(ge=0, le=10_000_000_000)
    tax_cents: int = Field(default=0, ge=0, le=10_000_000_000)
    fee_cents: int = Field(default=0, ge=0, le=10_000_000_000)
    total_cents: int = Field(ge=0, le=10_000_000_000)
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR"]
    status: CommerceOrderStatus
    payment_intent_id: str = Field(default="", max_length=40)
    payment_reference: str = Field(default="", max_length=200)
    status_history: list[CommerceOrderStatusEvent] = Field(default_factory=list, max_length=200)
    created_at: datetime
    updated_at: datetime
    # ── Pickup/delivery fulfillment metadata (Slice A 2026-05-30; additive,
    # default-safe, dormant). Populated by the ordering loop / Order Cockpit in
    # later slices; existing stored orders and the (config-inactive) order
    # substrate validate unchanged because every field has a default.
    # delivery_address is a free-form string for now — structured address is
    # deferred until delivery routing/geocoding matters (design §11).
    fulfillment_type: Optional[Literal["pickup", "delivery"]] = None
    customer_name: Optional[str] = Field(default=None, max_length=200)
    delivery_address: Optional[str] = Field(default=None, max_length=500)
    requested_time: Optional[datetime] = None
    order_notes: Optional[str] = Field(default=None, max_length=2000)
    pos_sync_status: Literal[
        "not_synced", "pending", "synced", "failed", "n/a"
    ] = "not_synced"

    @model_validator(mode="after")
    def _require_sender_identity(self) -> "CommerceOrder":
        if self.sender_phone is None and self.sender_lid is None:
            raise ValueError(
                "CommerceOrder requires at least one of sender_phone or sender_lid"
            )
        return self


class CommerceOrderStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders: list[CommerceOrder] = Field(default_factory=list, max_length=100_000)


class CommercePaymentIntent(BaseModel):
    """One payment intent per order_id (idempotency key). Mirrors Flyer guest_order shape."""
    model_config = ConfigDict(extra="forbid")
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    originating_message_id: str = Field(default="", max_length=200)
    amount_cents: int = Field(ge=1, le=10_000_000_000)
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR"]
    provider: Literal["placeholder", "stripe", "razorpay", "upi", "zelle", "cashapp", "manual"]
    checkout_url: str = Field(default="", max_length=1000)
    status: Literal["minted", "sent", "confirmed", "voided", "refunded", "chargeback"]
    payment_reference: str = Field(default="", max_length=200)
    created_at: datetime
    updated_at: datetime
    voided_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None
    refunded_amount_cents: int = Field(default=0, ge=0, le=10_000_000_000)
    chargeback_received_at: Optional[datetime] = None


class CommercePaymentIntentStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intents: list[CommercePaymentIntent] = Field(default_factory=list, max_length=100_000)


class CommercePaymentReferenceLedger(BaseModel):
    """Immutable cross-order dedup ledger. Reuse permanently blocked.

    Mirrors flyer/guest_order.py:108-113 + 2026-05-25 lesson.
    """
    model_config = ConfigDict(extra="forbid")
    references: dict[str, str] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────
# End commerce primitives
# ─────────────────────────────────────────────────────────────────


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
    # Bounded catchup window for legal-consequence work. If a gate's ideal
    # fire date passed by more than this many days, emit ComplianceReminderDeferred
    # + Pushover and skip rather than spam an outdated reminder.
    max_deferral_days: int = Field(default=7, ge=1, le=30)

    @field_validator("advance_warning_days")
    @classmethod
    def _sorted_unique_positive(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("advance_warning_days must not be empty")
        if any(d <= 0 for d in v):
            raise ValueError("advance_warning_days values must be positive")
        return sorted(set(v), reverse=True)


# PR-Agent13-v0.1 2026-05-04: Compliance items file schema.
# state/compliance-items.json is the mutable source of truth (operator-seeded
# + mark-done-mutated). Lives outside cfg.compliance because safe_io has no
# atomic_write_yaml — JSON is the only safely-mutable on-disk format.
class ComplianceItem(BaseModel):
    """Single recurring compliance deadline.

    id pattern note (PR-Agent13-v0.1 review H2 — 2026-05-04): underscore-only
    (no hyphen, no colon). The colon constraint is load-bearing — sentinel
    keys are formatted `<item_id>:<gate_days>` (see ComplianceLastSentFile),
    so id values containing `:` would corrupt key parsing. Hyphen forbidden
    by convention only (compliance items are operator-typed, not slug-style;
    underscore reads more naturally for "health_inspect_houston").
    """
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=100)
    category: Literal[
        "license_renewal", "tax_filing", "inspection",
        "certification", "insurance", "other",
    ]
    renewal_date: date  # the NEXT occurrence
    recurrence_days: int = Field(ge=0, le=3650)  # 0 = one-shot (deleted on mark-done)
    location_id: Optional[str] = Field(default=None, max_length=40)
    agency: Optional[str] = Field(default=None, max_length=200)
    resource_url: Optional[HttpUrl] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class ComplianceItemsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    items: list[ComplianceItem] = Field(default_factory=list, max_length=200)


class ComplianceLastSentFile(BaseModel):
    """Sentinel state at state/compliance-last-sent.json.
    Keys are '<item_id>:<gate_days>' (gate_days int as str: '30','14','7',
    '3','1','0','-N'). Values are ISO date strings (YYYY-MM-DD) of the
    last successful send for that (item, gate) pair.
    """
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    last_sent: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_keys(self):
        for k in self.last_sent:
            try:
                iid, gate_str = k.rsplit(":", 1)
                int(gate_str)
                if not iid:
                    raise ValueError("empty item_id")
            except (ValueError, IndexError) as e:
                raise ValueError(f"sentinel key {k!r} bad format: {e}")
        return self


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


# Agent #19 — Equipment & Maintenance (Tier-2 scaffold; PR-Agent19-v0.1 2026-05-04)
# Niche, low frequency — mostly a calendar with structured intake when things
# break. v0.1 ships scaffold only — full per-vendor integration deferred to
# v0.2 (gated on customer's actual equipment + vendor list). See portfolio.md
# Agent 19 spec.
class EquipmentMaintenanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    advance_warning_days: list[int] = Field(default_factory=lambda: [30, 14, 7, 3, 1])
    auto_route_to_vendor: bool = False  # v0.2; v0.1 always owner-mediated

    @field_validator("advance_warning_days")
    @classmethod
    def _sorted_unique_positive(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("advance_warning_days must not be empty")
        if any(d <= 0 for d in v):
            raise ValueError("advance_warning_days values must be positive")
        return sorted(set(v), reverse=True)


# Agent #22 — P&L Anomaly Detective (Tier-2 scaffold; PR-Agent22-v0.1 2026-05-04)
# Replaces retired Agent #17 Unit Economics. v0.1 ships scaffold only —
# anomaly-detection logic + POS integration deferred to v0.2 (gated on
# customer POS choice: clover / square / toast / other). See portfolio.md
# Agent 22 spec at lines 793-822.
class PnlAnomalyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    margin_drop_alert_pct: float = Field(default=8.0, ge=0.5, le=50.0)
    location_underperform_alert_pct: float = Field(default=15.0, ge=1.0, le=50.0)
    trailing_window_weeks: int = Field(default=4, ge=1, le=52)
    pos_provider: Optional[Literal["clover", "square", "toast", "other"]] = None


# Agent #21 — Expense Bookkeeper (v0.1; mocked QBOClient interface)
# Original design at tasks/expense-bookkeeper-v01-design.md was archived in
# the 2026-05-04 bucket-C cleanup; recover via the backup tag if needed:
#   git show pre-tasks-cleanup-2026-05-04:tasks/expense-bookkeeper-v01-design.md
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

    @field_validator(
        "sender_phone",
        "original_message_id",
        "sender_lid",
        "qbo_account",
        "rejection_reason",
    )
    @classmethod
    def _validate_required_no_whitespace_no_nullbyte(cls, v: Optional[str]) -> Optional[str]:
        """Audit-bug v1.1 fix: addresses BUGs 2 + 3 together.

        - sender_phone (BUG-2 audit): reject empty / whitespace-only.
          Field(min_length=1) alone passes "   " which would break owner
          re-auth at apply-expense-decision step where
          `sender_phone == owner_phone`.
        - original_message_id (BUG-3 audit): reject null byte / control
          char. NDJSON audit-log safety; Pydantic `model_dump_json`
          escapes these but defence-in-depth keeps log-corruption surface
          zero.
        - Optional v0.2 fields (`sender_lid`, `qbo_account`,
          `rejection_reason`) remain nullable, but when present they share
          the same blank/control-char boundary.
        """
        if v is None:
            return v
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
    flyer: FlyerConfig = Field(default_factory=FlyerConfig)
    commerce: CommerceConfig = Field(default_factory=CommerceConfig)
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
    pnl_anomaly: PnlAnomalyConfig = Field(default_factory=PnlAnomalyConfig)
    equipment_maintenance: EquipmentMaintenanceConfig = Field(default_factory=EquipmentMaintenanceConfig)
    # Agent #33 Loyalty v0.1 — birthday reminders + record-customer-birthday CLI.
    # Default enabled=False; opt-in per customer.
    loyalty: LoyaltyConfig = Field(default_factory=LoyaltyConfig)
    # Agent #41 Owner Wellbeing v0.1 — quiet-hours guard at notify-owner chokepoint.
    # Default enabled=False (opt-in); revived from retired #20 per portfolio.md:1078.
    owner_wellbeing: OwnerWellbeingConfig = Field(default_factory=OwnerWellbeingConfig)

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
# PR-ζ 2026-05-26 — ActionExecutionContext
# ─────────────────────────────────────────────────────────────────
#
# Per-action runtime context propagated through safe_io.bridge_post family.
# Carries action identity + verification state so the chokepoint applies
# forbidden-completion-verb lint (PR-γ) only when an action's result is
# unverified. frozen=True + extra=forbid defends against accidental mutation
# or unexpected field drift.
#
# NOTE on `is_regulated_action=False` defensive use:
# Setting `is_regulated_action=False` skips the lint entirely, regardless of
# message content. This is correct for system messages (healthchecks, daily
# digests). It is INCORRECT to use for a regulated business action that the
# caller wishes to bypass lint on — the right escape is to set
# `verified_action_result=True` with explicit evidence (audit-row id of the
# completion event). Mis-tagging a regulated action as non-regulated bypasses
# the entire protection.

class ActionExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(..., min_length=1, max_length=200)
    is_regulated_action: bool
    verified_action_result: bool
    audit_row_id: Optional[str] = Field(default=None, max_length=200)
    mutation_class: Optional[Literal["local_reversible", "external_irreversible"]] = None


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


class ConfigGateOverride(_BaseEntry):
    """Audit row: deploy-time `tools/check-hermes-config-yaml.sh` accepted an
    operator-supplied two-variable override (FIELD + REASON). Bypasses the
    gate for one deploy invocation; the underlying config issue must still be
    fixed before the override variable is unset or the next deploy will
    fail-close again.

    Distinct from AgentStateChange because no agent's enabled-state changed; a
    deploy-time gate was bypassed. dispatcher-accuracy-report and other audit
    queries can grep this variant separately rather than seeing override
    events conflated with actual agent enable/disable events.
    """
    type: Literal["config_gate_override"]
    field: str  # operator-attested failing field (e.g. "model.default")
    all_failures: str  # comma-joined list of ALL failing fields from JSON envelope
    reason: str  # operator's free-text rationale


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


class ValidateFailed(_BaseEntry):
    """Audit log: validate-sender-block returned valid=false OR v != 1 for an
    inbound message, so dispatch_shift_agent FAILED CLOSED — it sent the generic
    decline and delegated to NO handler. Mirrors the dispatcher SKILL step:
    "If valid=false OR v != 1: write a validate_failed audit via terminal ->
    log-decision-direct, send the fail-closed reply, STOP."

    Distinct from UnknownSenderDeclined, which is a *valid* v=1 block whose
    identity is merely unknown. validate_failed means the v=1 sender block
    itself was malformed or absent — often a malformed or injected inbound.

    Deliberately captures NO raw block content (extra='forbid' via _BaseEntry):
    a malformed/injected block must never be echoed into the audit log.
    `reason` is a short bounded category; `message_id` is optional because a
    malformed block may carry no parseable id.
    """
    type: Literal["validate_failed"]
    reason: Optional[str] = Field(default=None, max_length=200)
    message_id: Optional[str] = Field(default=None, max_length=256)


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


class OwnerNotificationSuppressed(_BaseEntry):
    """Quiet-hours guard suppressed a non-critical Pushover/WhatsApp send
    (Agent #41 v0.1). Emitted by shift-agent-notify-owner before the
    Pushover call when the priority is below the threshold AND now is
    inside the configured quiet window. Exit code remains EXIT_OK
    (success-skip semantics, mirrors BriefSkipped:already_sent)."""
    type: Literal["owner_notification_suppressed"]
    title: str = Field(max_length=250)
    priority: int = Field(ge=-2, le=2)
    quiet_start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_days: list[str] = Field(min_length=1)
    suppressed_at_local: str = Field(pattern=r"^\d{2}:\d{2}:\d{2}$")


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


# Agent #33 Loyalty v0.1 — birthday-store mutation audit
class CustomerBirthdayRecorded(_BaseEntry):
    """Audit emitted by record-customer-birthday after a successful upsert.
    operation: "created" if phone wasn't in store, "updated" if it was."""
    type: Literal["customer_birthday_recorded"]
    customer_phone: E164Phone   # R2-M1 PR review fix: canonical-form discipline matches CateringLeadCreated
    display_name: str = Field(min_length=1, max_length=100)
    birthday: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    operation: Literal["created", "updated"]


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
    # `cockpit_v01_paper` retained as a deprecated-but-readable Literal value
    # for rollback-window safety per the absorbing-shim discipline (PR-D3 /
    # catering precedent). The writer in extract-receipt is hardcoded to
    # "whatsapp" post-PR #42; this widening exists ONLY so historical
    # decisions.log rows containing the legacy value validate cleanly on
    # re-read by daily-brief / dispatcher-accuracy-report / fsck. Remove
    # this value once the rollback window has lapsed AND a grep across all
    # live VPSes confirms zero historical entries.
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


class FlyerProjectCreated(_BaseEntry):
    type: Literal["flyer_project_created"]
    project_id: str = Field(pattern=r"^F\d{4,}$")
    customer_phone: E164Phone
    original_message_id: str = Field(min_length=1, max_length=200)


class FlyerStatusChange(_BaseEntry):
    type: Literal["flyer_status_change"]
    project_id: str = Field(pattern=r"^F\d{4,}$")
    from_status: FlyerWorkflowStatus
    to_status: FlyerWorkflowStatus
    actor: Literal["system", "customer", "operator"]
    reason: str = Field(default="", max_length=500)


class FlyerAssetsDelivered(_BaseEntry):
    type: Literal["flyer_assets_delivered"]
    project_id: str = Field(pattern=r"^F\d{4,}$")
    customer_phone: E164Phone
    asset_ids: list[str] = Field(min_length=1, max_length=10)
    outbound_message_ids: list[str] = Field(default_factory=list, max_length=10)


class FlyerDeliveryFailed(_BaseEntry):
    type: Literal["flyer_delivery_failed"]
    project_id: str = Field(pattern=r"^F\d{4,}$")
    customer_phone: E164Phone
    asset_id: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=80)
    error: str = Field(default="", max_length=1000)


class FlyerCustomerCreated(_BaseEntry):
    type: Literal["flyer_customer_created"]
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    business_name: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(min_length=1, max_length=40)
    primary_chat_id: str = Field(default="", max_length=200)


class FlyerCustomerActivated(_BaseEntry):
    type: Literal["flyer_customer_activated"]
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    plan_id: str = Field(min_length=1, max_length=40)
    provider: Literal["manual", "stripe", "razorpay", "other"]
    payment_reference: str = Field(min_length=1, max_length=200)
    payment_amount_cents: Optional[int] = Field(default=None, ge=0)
    payment_currency: str = Field(default="USD", min_length=3, max_length=3)
    idempotent_replay: bool = False


class FlyerAccountUpdated(_BaseEntry):
    type: Literal["flyer_account_updated"]
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    command: str = Field(min_length=1, max_length=80)
    actor_phone: Optional[E164Phone] = None
    actor_role: str = Field(default="", max_length=40)
    allowed: bool
    reason: str = Field(default="", max_length=500)


class FlyerUsageRecorded(_BaseEntry):
    type: Literal["flyer_usage_recorded"]
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    project_id: str = Field(pattern=r"^F\d{4,}$")
    reservation_id: str = Field(min_length=1, max_length=120)
    kind: Literal["reserved", "used", "released"]
    usage_count: int = Field(default=0, ge=0)


class FlyerQuotaBlocked(_BaseEntry):
    type: Literal["flyer_quota_blocked"]
    customer_id: str = Field(pattern=r"^CUST\d{4,}$")
    project_id: str = Field(pattern=r"^F\d{4,}$")
    plan_id: str = Field(min_length=1, max_length=40)
    usage_count: int = Field(default=0, ge=0)
    limit: int = Field(ge=1)


class _FlyerAutoRepairEntry(_BaseEntry):
    attempt_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(pattern=r"^F\d{4,}$")
    project_version: int = Field(ge=1)
    mode: FlyerRepairMode = "hermes_regenerate"
    qa_blocker_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    repair_instruction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    detail: str = Field(default="", max_length=1000)
    generated_asset_ids: list[str] = Field(default_factory=list, max_length=10)


class FlyerAutoRepairAttempted(_FlyerAutoRepairEntry):
    type: Literal["flyer_autorepair_attempted"] = "flyer_autorepair_attempted"


class FlyerAutoRepairSucceeded(_FlyerAutoRepairEntry):
    type: Literal["flyer_autorepair_succeeded"] = "flyer_autorepair_succeeded"


class FlyerAutoRepairExhausted(_FlyerAutoRepairEntry):
    type: Literal["flyer_autorepair_exhausted"] = "flyer_autorepair_exhausted"


class FlyerAutoRepairSkipped(_FlyerAutoRepairEntry):
    type: Literal["flyer_autorepair_skipped"] = "flyer_autorepair_skipped"


FlyerRecoverySeverity = Literal["info", "warning", "critical"]
FlyerRecoveryEvidenceQuality = Literal["strong", "weak", "missing"]


class FlyerRecoveryIncidentOpened(_BaseEntry):
    type: Literal["flyer_recovery_incident_opened"]
    incident_id: str = Field(min_length=1, max_length=80)
    failure_class: str = Field(min_length=1, max_length=80)
    severity: FlyerRecoverySeverity
    project_id: str = Field(default="", max_length=40)
    source_fingerprint: str = Field(min_length=1, max_length=120)
    ack_dedupe_key: str = Field(default="", max_length=120)
    chat_id_hash: str = Field(default="", max_length=120)
    evidence_quality: FlyerRecoveryEvidenceQuality = "missing"
    mode: str = Field(default="", max_length=40)


class FlyerRecoveryCustomerAckAttempted(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_attempted"]
    incident_id: str = Field(min_length=1, max_length=80)
    ack_attempt_id: str = Field(min_length=1, max_length=80)
    ack_dedupe_key: str = Field(min_length=1, max_length=120)
    source_fingerprint: str = Field(min_length=1, max_length=120)
    chat_id_hash: str = Field(min_length=1, max_length=120)
    evidence_quality: FlyerRecoveryEvidenceQuality
    mode: str = Field(min_length=1, max_length=40)
    copy_policy_template_id: str = Field(min_length=1, max_length=80)
    message_sha256: str = Field(min_length=1, max_length=120)


class FlyerRecoveryCustomerAckSent(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_sent"]
    incident_id: str = Field(min_length=1, max_length=80)
    ack_attempt_id: str = Field(min_length=1, max_length=80)
    ack_dedupe_key: str = Field(min_length=1, max_length=120)
    source_fingerprint: str = Field(min_length=1, max_length=120)
    chat_id_hash: str = Field(min_length=1, max_length=120)
    evidence_quality: FlyerRecoveryEvidenceQuality
    mode: str = Field(min_length=1, max_length=40)
    outbound_message_id: str = Field(min_length=1, max_length=200)


class FlyerRecoveryCustomerAckFailed(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_failed"]
    incident_id: str = Field(min_length=1, max_length=80)
    ack_attempt_id: str = Field(min_length=1, max_length=80)
    ack_dedupe_key: str = Field(min_length=1, max_length=120)
    source_fingerprint: str = Field(min_length=1, max_length=120)
    chat_id_hash: str = Field(min_length=1, max_length=120)
    evidence_quality: FlyerRecoveryEvidenceQuality
    mode: str = Field(min_length=1, max_length=40)
    status: str = Field(min_length=1, max_length=80)
    error: str = Field(default="", max_length=1000)


class FlyerRecoveryCustomerAckUncertain(FlyerRecoveryCustomerAckFailed):
    type: Literal["flyer_recovery_customer_ack_uncertain"]


class FlyerRecoveryCustomerAckSuppressed(_BaseEntry):
    type: Literal["flyer_recovery_customer_ack_suppressed"]
    incident_id: str = Field(min_length=1, max_length=80)
    ack_dedupe_key: str = Field(default="", max_length=120)
    source_fingerprint: str = Field(default="", max_length=120)
    chat_id_hash: str = Field(default="", max_length=120)
    evidence_quality: FlyerRecoveryEvidenceQuality = "missing"
    mode: str = Field(default="", max_length=40)
    reason: str = Field(min_length=1, max_length=500)


class FlyerRecoveryRepairBundleWritten(_BaseEntry):
    type: Literal["flyer_recovery_repair_bundle_written"]
    incident_id: str = Field(min_length=1, max_length=80)
    bundle_path: str = Field(min_length=1, max_length=500)


class FlyerRecoveryOutcomeRepaired(_BaseEntry):
    type: Literal["flyer_recovery_outcome_repaired"]
    repair_type: Literal["reference_scope_false_positive"]
    status: Literal["sent", "failed"]
    chat_id_hash: str = Field(min_length=1, max_length=120)
    customer_id: str = Field(default="", max_length=40)
    business_name: str = Field(default="", max_length=160)
    scope_reason: str = Field(default="", max_length=200)
    outbound_message_id: str = Field(default="", max_length=200)
    error: str = Field(default="", max_length=500)


class FlyerRecoveryDeployGate(_BaseEntry):
    type: Literal["flyer_recovery_deploy_gate"]
    incident_id: str = Field(min_length=1, max_length=80)
    gate: str = Field(min_length=1, max_length=120)
    passed: bool
    detail: str = Field(default="", max_length=1000)


class FlyerRecoveryResolved(_BaseEntry):
    type: Literal["flyer_recovery_resolved"]
    incident_id: str = Field(min_length=1, max_length=80)
    resolution: Literal[
        "suppressed",
        "customer_ack_sent",
        "repair_queued",
        "manual_required",
        "deployed",
        "outcome_repaired",
        "customer_visible_success",
    ]


class FlyerRecoveryOperatorActionRequired(_BaseEntry):
    type: Literal["flyer_recovery_operator_action_required"]
    incident_id: str = Field(min_length=1, max_length=80)
    failure_class: str = Field(default="", max_length=80)
    project_id: str = Field(default="", max_length=40)
    reason: Literal[
        "worker_completed_no_customer_visible_success",
        "worker_failed_no_customer_visible_success",
    ]
    required_action: Literal["verify_customer_outcome_or_repair_manually"]


class FlyerRecoveryOwnerAlert(_BaseEntry):
    type: Literal["flyer_recovery_owner_alert"]
    incident_id: str = Field(min_length=1, max_length=80)
    project_id: str = Field(default="", max_length=40)
    trigger: Literal["customer_ack_suppressed", "operator_action_required"]
    outcome: Literal["sent", "failed"]
    reason: str = Field(default="", max_length=500)
    notify_source: str = Field(default="flyer-recovery-watchdog", max_length=120)


class FlyerClosureCustomerNotified(_BaseEntry):
    """Operator-driven `flyer-manual-queue --close` proactive customer push.

    Closure state write is the primary operation; this audit row records the
    outcome of the best-effort notification that follows it. `send_ok=False`
    rows mean the customer will learn via the reactive "any update?" safety
    net instead. Distinct from `FlyerAssetsDelivered` because closure pushes
    carry no asset and signal a non-completion outcome.
    """
    type: Literal["flyer_closure_customer_notified"]
    project_id: str = Field(pattern=r"^F\d{4,}$")
    customer_phone: E164Phone
    reason_code: str = Field(min_length=1, max_length=80)
    chat_id: str = Field(default="", max_length=200)
    chat_id_source: Literal["audit_log", "primary_chat_id", "none", ""] = ""
    send_ok: bool
    outbound_message_id: str = Field(default="", max_length=200)
    error: str = Field(default="", max_length=500)


class FlyerStatusResent(_BaseEntry):
    """Operator-driven proactive 'resend status' nudge from the cockpit.

    Records the outcome of the best-effort WhatsApp push that re-sends a
    waiting customer the project's CURRENT status reply (P3 safe action).
    Read-only with respect to project state — unlike
    `FlyerClosureCustomerNotified` it carries no state transition and no
    `reason_code`. `send_ok=False` rows mean the customer will learn via
    the reactive "any update?" safety net instead.
    """
    type: Literal["flyer_status_resent"] = "flyer_status_resent"
    project_id: str = Field(pattern=r"^F\d{4,}$")
    customer_phone: E164Phone
    chat_id: str = Field(default="", max_length=200)
    chat_id_source: Literal["audit_log", "primary_chat_id", "none", ""] = ""
    send_ok: bool
    outbound_message_id: str = Field(default="", max_length=200)
    error: str = Field(default="", max_length=500)


class FlyerManualQueueCustomerUpdate(_BaseEntry):
    """SLA watchdog's proactive status update to customers with stale manual rows."""
    type: Literal["flyer_manual_queue_customer_update"] = "flyer_manual_queue_customer_update"
    project_id: str = Field(pattern=r"^F\d{4,}$")
    reason_code: str = Field(default="", max_length=80)
    manual_status: str = Field(default="", max_length=40)
    age_minutes: float = Field(ge=0.0)
    outcome: Literal["sent", "failed", "skipped_no_chat_id", "suppressed_same_chat_update"]
    chat_id_source: str = Field(default="", max_length=120)
    outbound_message_id: str = Field(default="", max_length=200)
    error: str = Field(default="", max_length=500)


class FlyerSourceContractExtracted(_BaseEntry):
    """Audit row emitted once per source-contract extraction attempt.

    Records counts (not the raw contract content) so the audit log stays
    PII-light. `status="provider_unavailable"` rows are still emitted so
    operators can see when the source-edit path falls closed silently.
    """
    type: Literal["flyer_source_contract_extracted"] = "flyer_source_contract_extracted"
    project_id: str = Field(min_length=1, max_length=40)
    asset_id: str = Field(default="", max_length=40)
    asset_sha256: str = Field(default="", max_length=64)
    role: FlyerReferenceRole
    status: FlyerReferenceExtractionStatus
    headings_count: int = 0
    sections_count: int = 0
    replacements_count: int = 0
    forbidden_substrings_count: int = 0
    confidence: float = 0.0
    provider: str = Field(default="", max_length=120)


class FlyerSourceVsNewChosen(_BaseEntry):
    """Audit row when an exact-edit customer picks SOURCE vs NEW.

    `choice` values:
      - clarification_sent: prompt first issued.
      - clarification_resent: status check-in re-issued the prompt idempotently.
      - source: customer chose SOURCE; row consumed; manual-edit project queued.
      - new: customer chose NEW; row consumed; new project created.
      - expired: TTL pruning dropped the row unconsumed.
    """
    type: Literal["flyer_source_vs_new_chosen"] = "flyer_source_vs_new_chosen"
    sender_phone: str = Field(default="", max_length=32)
    customer_id: str = Field(default="", max_length=40)
    original_intent: Literal["exact_source_edit", "generic_reference", "unknown"]
    choice: Literal["source", "new", "clarification_sent", "clarification_resent", "expired"]
    pending_age_sec: int = 0
    customer_followup_instruction: str = Field(default="", max_length=500)


class FlyerSourceEditSlaAlert(_BaseEntry):
    """Operator alert audit for stale source-edit manual queue rows."""
    type: Literal["flyer_source_edit_sla_alert"] = "flyer_source_edit_sla_alert"
    outcome: Literal["alerted", "throttled", "notify_failed", "alerted_notify_failed"]
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    project_ids: list[str] = Field(default_factory=list, max_length=50)
    stale_count: int = Field(default=0, ge=0)
    alerted_count: int = Field(default=0, ge=0)
    throttled_count: int = Field(default=0, ge=0)
    oldest_age_minutes: float = Field(default=0.0, ge=0.0)
    threshold_minutes: int = Field(default=10, ge=1)
    repeat_minutes: int = Field(default=60, ge=1)
    notify_ok: bool = False


class FlyerHermesIntentDecision(_BaseEntry):
    """Read-only shadow audit for the Flyer Hermes intent contract.

    PII-light by construction: hashes instead of raw chat/message ids, route
    families instead of raw customer text, and no provider/customer copy fields.
    """
    type: Literal["flyer_hermes_intent_decision"] = "flyer_hermes_intent_decision"
    schema_version: Literal[1] = 1
    # "active" added 2026-06-06: FLYER_HERMES_INTENT_CLASSIFIER/MODE=active is a real
    # deployed runtime mode (intent.FlyerIntentMode.ACTIVE), so the shadow-audit must
    # be able to RECORD it. Previously mode_from_value("active")→ACTIVE("active") was
    # emitted but the Literal omitted it → the audit row was REJECTED on every request
    # (non-fatal, but the route decision was silently lost). "unsupported_active_mode"
    # stays for the distinct case where an active-mode request hits an unsupported path.
    mode: Literal["off", "shadow", "active", "unsupported_active_mode"]
    decision_source: Literal["none", "fixture", "deterministic_baseline", "hermes_gateway_future"]
    classifier_status: Literal[
        "off",
        "skipped_not_candidate",
        "skipped_passthrough",
        "skipped_no_gateway",
        "skipped_budget",
        "success",
        "timeout",
        "invalid",
        "error",
    ] = "off"
    classifier_latency_ms: int = Field(default=0, ge=0)
    classifier_error_kind: str = Field(default="", max_length=80)
    classifier_error_detail: str = Field(default="", max_length=300)
    message_id_hash: str = Field(min_length=1, max_length=64)
    chat_key_hash: str = Field(default="", max_length=64)
    has_media: bool = False
    validator_ok: bool
    validator_reasons: list[str] = Field(default_factory=list, max_length=20)
    advisory_intent: str = Field(default="", max_length=80)
    advisory_action: str = Field(default="", max_length=80)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    would_mutate: bool = False
    actual_route: str = Field(default="", max_length=120)
    actual_reason: str = Field(default="", max_length=200)
    actual_action: Literal[
        "new_project",
        "revision",
        "approval",
        "status",
        "manual_review",
        "account_update",
        "onboarding_or_intake",
        "passthrough",
        "failure",
        "unknown",
    ]
    route_sequence: list[str] = Field(default_factory=list, max_length=20)
    route_terminal: bool = True
    subprocess_rc: Optional[int] = None
    branch_return_reason: str = Field(default="", max_length=300)
    selected_project_id: str = Field(default="", max_length=40)
    prior_active_project_id: str = Field(default="", max_length=40)
    project_status: str = Field(default="", max_length=80)
    customer_status: str = Field(default="", max_length=80)
    intake_status: str = Field(default="", max_length=80)
    preview_source: Literal["actual", "simulated", "none"] = "actual"
    live_route_changed: Literal[False] = False
    active_customer_risk: bool = False
    risk_scope: Literal[
        "active_project",
        "active_customer",
        "active_intake",
        "pre_project_customer_visible",
        "historical_audit",
        "none",
    ] = "none"


# 2026-05-28 — intake-bypass audit pair. See plan + design at
# tasks/flyer-intake-bypass-{plan,design}-2026-05-28.md.
# `ts` (inherited from `_BaseEntry`) is the event timestamp — no separate
# `bypassed_at` / `finalized_at` field (matches deployed convention).
class FlyerIntakeBypassed(_BaseEntry):
    """Decision-time audit: intake bypass fired.

    Emitted by `_try_flyer_intake_intercept` (cf-router/hooks.py) immediately
    on bypass — the customer's intent was structurally clear, so the wizard
    was skipped + the message was allowed to flow to the rest of the
    intercept ladder.

    Pairs with `FlyerIntakeBypassOutcome` (emitted by the dispatch finally
    block via `finalize_flyer_intake_bypass_shadow`) to give operators a
    two-row decision-then-outcome trail per `chat_id_hash` without relying
    on timestamp-window correlation across logrotate boundaries."""
    type: Literal["flyer_intake_bypassed"] = "flyer_intake_bypassed"
    chat_id_hash: str = Field(min_length=1, max_length=120)
    bypass_reason: Literal[
        "edit_with_media",
        "new_flyer_text_only",
        "new_flyer_with_media",
        "existing_active_customer_intent",
        "existing_trial_customer_intent",
    ]
    has_media: bool
    customer_state: str = Field(default="", max_length=40)
    intake_session_status: str = Field(default="", max_length=80)
    # Regional-SMB telemetry — detection-and-act deferred but the script
    # signal accumulates here for the follow-up PR. Operator decision
    # 2026-05-28 #3 — prevents silent anglo-defaulting of Hindi/Telugu/Tamil
    # customers.
    inbound_script: Literal["latin", "devanagari", "tamil", "other"] = "latin"


class FlyerIntakeBypassOutcome(_BaseEntry):
    """Outcome-time audit: what happened after intake bypass fired.

    Emitted by `_pre_gateway_dispatch`'s finally block via
    `finalize_flyer_intake_bypass_shadow` — mirrors the deployed
    `finalize_flyer_intent_shadow` pattern at cf-router/actions.py.

    Outcome derivation pinned per plan §9 (post-revision): F-pattern regex
    extraction from `hook_result["reason"]`. Build-phase replay gate
    verifies derivation reliability against captured audit-log sample."""
    type: Literal["flyer_intake_bypass_outcome"] = "flyer_intake_bypass_outcome"
    chat_id_hash: str = Field(min_length=1, max_length=120)
    outcome: Literal[
        "routed_to_project",
        "unrouted",
        "intermediate_intercept_handled",
    ]
    project_id: str = Field(default="", max_length=40)
    handler_intercept: str = Field(default="", max_length=80)
    elapsed_ms: int = Field(default=0, ge=0)


# P0 #2 2026-05-28 — severity-tiered visual QA audit variants.
# `ts` (inherited from `_BaseEntry`) is the event timestamp; no separate
# classified_at / delivered_at field — matches deployed convention.
class FlyerQASeverityClassified(_BaseEntry):
    """Records the severity classification on a visual QA report.

    Emitted by generate-flyer-concepts after run_visual_qa() returns
    and classify_qa_severity() has decided pass / warn / block.
    Always fires regardless of severity — operators can grep for
    severity distribution over time."""
    type: Literal["flyer_qa_severity_classified"] = "flyer_qa_severity_classified"
    project_id: str = Field(pattern=r"^F\d{4,}$")
    asset_id: str = Field(default="", max_length=80)
    severity: Literal["pass", "warn", "block"]
    blocker_count: int = Field(ge=0, le=50)
    classifier_version: str = Field(default="v1", max_length=20)


class FlyerWarnTierDelivered(_BaseEntry):
    """Records the decision to deliver a concept preview under warn-tier severity.

    Emitted by generate-flyer-concepts immediately BEFORE the cf-router
    post-subprocess branch dispatches the warn-tier send. Captures the
    blockers list + sha256 of the customer text so audit replay can
    reconstruct exactly what was shipped without storing the raw copy
    twice (FlyerWarningSummary.customer_text is the live copy)."""
    type: Literal["flyer_warn_tier_delivered"] = "flyer_warn_tier_delivered"
    project_id: str = Field(pattern=r"^F\d{4,}$")
    asset_id: str = Field(min_length=1, max_length=80)
    severity: Literal["warn"]
    blockers: list[str] = Field(default_factory=list, max_length=50)
    customer_text_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class FlyerOperatorFlaggedWarnTier(_BaseEntry):
    """Audit-only operator flag on a delivered_with_warning project.

    Emitted by the cockpit POST /flyer/projects/{id}/flag route (P0 #2 Commit 5
    Pin D — reviewer 2 #6). NO project-state mutation; the flag exists purely
    to surface operator concern in the audit log without engaging the manual
    queue. Operators use this to mark warn-tier deliveries that look wrong
    (classifier should have escalated to block) — trend data accumulates from
    day 1 even though the full warn → manual-queue re-route stays deferred."""
    type: Literal["flyer_operator_flagged_warn_tier"] = "flyer_operator_flagged_warn_tier"
    project_id: str = Field(pattern=r"^F\d{4,}$")
    flagged_by_operator_id: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


class FlyerCreativeDirectorRouted(_BaseEntry):
    """Records, on EVERY new-flyer bare render, whether the Creative-Director path
    was taken (PR3 wiring). Emitted whether or not the flag is on, so the operator
    can PROVE the caller from the audit log BEFORE enabling the feature:

      - flag off / sender not allowlisted ⇒ creative_director_reached=False,
        status="disabled" (flag off) or "not_allowlisted" (allowlist miss);
      - enabled-for-sender ⇒ creative_director_reached=True and status mirrors the
        BriefResult ("ok" | "invalid" | "unavailable").

    ``module_version`` + ``module_file`` pin EXACTLY which code emitted the row so a
    stale deployed copy is detectable. ``resolved_sender`` is the trusted phone/LID
    bare_render resolves (never message content); ``allowlisted`` is the gate result."""
    type: Literal["flyer_creative_director_routed"] = "flyer_creative_director_routed"
    creative_director_reached: bool
    creative_director_status: Literal["disabled", "ok", "invalid", "unavailable", "not_allowlisted"]
    module_version: str = Field(min_length=1, max_length=120)
    module_file: str = Field(default="", max_length=500)
    resolved_sender: str = Field(default="", max_length=200)
    allowlisted: bool = False
    chat_id: str = Field(default="", max_length=200)
    # ── Observability (2026-06-06): WHY a non-shipping outcome happened ──────────
    # Added so a failed live retest is diagnosable from the audit row ALONE. Before
    # this, the row carried only ``creative_director_status``, which could NOT (a)
    # distinguish a SHIPPED flyer from a brief-ok-but-render-failed one (both emit
    # status="ok"), nor (b) say WHY a brief was "invalid" / "unavailable". All
    # additive + defaulted (old rows + readers unaffected); these are LOG-ONLY and
    # NEVER alter customer-facing behavior (still fail-closed, no legacy fallback).
    #   - error_summary: one compact human-readable reason for ANY non-shipping
    #     outcome ("" on a clean ship → the single grep-able "did it ship?" field).
    #   - errors: structured detail (validator errors for "invalid"; the render/QA
    #     blocker strings otherwise). Each entry truncated at the emit site.
    #   - unavailable_reason: the classified brain/gateway failure for "unavailable"
    #     (missing_key | timeout | http_4xx | transient_exhausted:* | parse_failure |
    #     skill_body_unreadable | brief_unparseable | brief_exception:* | gateway_unreachable).
    #   - render_error: the exception type when status="ok" but the textless-bg /
    #     overlay render threw (the brief validated, the flyer did NOT ship).
    error_summary: str = Field(default="", max_length=200)
    errors: list[str] = Field(default_factory=list, max_length=20)
    unavailable_reason: str = Field(default="", max_length=80)
    render_error: str = Field(default="", max_length=120)


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
    # PR-D3 hotfix: added "operator" — catering-lead-reconcile (PR-D2 commit 7)
    # writes actor="operator" but the deployed schema rejected it, crashing
    # every reconcile invocation. Surfaced on canary 2026-04-30 by synthetic
    # probe orphan cleanup. Static-only tests didn't catch (R4 H1+H2 risk).
    actor: Literal["system", "owner", "customer", "operator"]
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


class CateringProposalsGenerated(_BaseEntry):
    type: Literal["catering_proposals_generated"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = Field(min_length=1)
    option_count: int = Field(ge=2, le=3)
    outbound_message_id: str = Field(min_length=1)


class CateringProposalGenerationFailed(_BaseEntry):
    type: Literal["catering_proposal_generation_failed"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = ""
    reason: Literal[
        "unknown_menu_item", "forbidden_customer_text", "bridge_unreachable",
        "lead_not_found", "menu_missing", "invalid_options",
    ]
    detail: str = Field(default="", max_length=2000)


class CateringProposalSelected(_BaseEntry):
    type: Literal["catering_proposal_selected"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = Field(min_length=1)
    option_id: str = Field(pattern=r"^[1-3]$")
    customer_message_id: str = Field(min_length=1, max_length=200)
    finalize_exit_code: int = Field(ge=0)


class CateringProposalSelectionFailed(_BaseEntry):
    type: Literal["catering_proposal_selection_failed"]
    lead_id: str = Field(min_length=1)
    proposal_set_id: str = ""
    reason: Literal[
        "no_sent_proposal", "ambiguous_selection", "invalid_selection",
        "lead_not_found", "finalize_exit_2", "finalize_exit_4",
        "finalize_exit_11", "finalize_exit_other",
    ]
    detail: str = Field(default="", max_length=2000)


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


class CateringQuoteSkillFailed(_BaseEntry):
    """PR-B v0.4: emitted when the LLM-drafting SKILL → apply-script handoff
    fails. Three failure surfaces:

    - apply-script side: --quote-text-stdin missing OR drafted text fails
      the truth-guard regex sanity check (headcount integer / ISO event_date
      not present in the prose). Emitted via SKILL on apply-script's
      non-zero exit (apply-script writes the row best-effort itself, then
      SKILL emits a covering row so observability is on the SKILL path).
    - SKILL side: LLM call returned malformed/empty/error. SKILL emits
      directly via log-decision-direct.

    `reason` Literal kept narrow on purpose — wider taxonomy can be added
    in v0.5 if canary surfaces patterns we haven't anticipated.

    Naming: Catering<Subject><PastParticiple> pattern. Subject="QuoteSkill"
    (the SKILL responsible for drafting), past-participle="Failed".
    """
    type: Literal["catering_quote_skill_failed"]
    lead_id: str = Field(min_length=1)
    code: str = Field(
        # Review-fix M1: pattern enforced — matches the 28.6M-entry alphabet
        # used by generate_unique_code (no visually-ambiguous chars).
        # Without the pattern, a future external caller could land malformed
        # rows in decisions.log; min_length alone wouldn't catch "ABCDEF".
        pattern=r"^#[A-HJKMNPQR-Z2-9]{5}$",
        description="Owner approval code, format #XXXXX (no I/O/0/1/L)",
    )
    reason: Literal[
        "missing_quote_text",       # --quote-text-stdin not provided OR empty
        "truth_guard_failed",       # headcount/ISO-date sanity check rejected
        "apply_decision_nonzero",   # SKILL detected non-zero exit (catch-all)
        "llm_unreachable",          # Hermes gateway unavailable (SKILL-side)
        "llm_malformed_response",   # gateway returned non-text/empty/error
        "finalized_total_missing_from_quote",      # PR-CF1: soft-warn (apply-script Change 5A)
        "owner_approve_without_customer_finalize", # PR-CF1: hard-fail (apply-script Change 5B)
    ]
    detail: str = Field(default="", max_length=2000)


class CateringMenuFinalized(_BaseEntry):
    """PR-CF1: customer finalized their catering menu, owner approval card sent.

    `outcome` distinguishes success from quote-mismatch rejection so audit
    consumers can filter cleanly without numeric heuristics.

    `replay=true` is set when the same `last_finalize_message_id` is seen
    twice — state is unchanged, owner card NOT resent (rate-limited 60s).
    `suppressed=true` is set when replay AND a recent owner-card send was
    detected within the cooldown window.

    `price_drift_detected=true` is set when one or more selected items had a
    different price in the current menu than what the LLM passed; we use the
    CURRENT menu price (server-authoritative). Owner card includes a
    `price_drift_note` line so the owner is aware.

    `prior_total_usd` and `prior_item_count` are populated only on re-finalize
    (different `customer_message_id` while status was already CUSTOMER_FINALIZED
    or after OWNER_EDITED) — gives operator visibility into customer revisions.

    PR-CF2: `customer_message_id` is the bridge messageId of the customer's
    finalize message (the same value compared against `last_finalize_message_id`
    on the lead). Lets ops trace audit row → raw_inbound → dispatcher_routed
    without phone-based fuzzy matching. Optional for forward-compat with
    PR-CF1-vintage rows; new emissions always populate it.
    """
    type: Literal["catering_menu_finalized"]
    outcome: Literal["finalized", "rejected_quote_mismatch"]
    lead_id: str = Field(min_length=1)
    customer_phone: E164Phone
    item_count: int = Field(ge=0, le=50)             # 0 allowed for outcome=rejected
    server_recompute_usd: int = Field(ge=0)          # source of truth
    llm_passed_total_usd: int = Field(ge=0)          # what LLM claimed (audit-only)
    quote_total_usd: int = Field(ge=0)               # = server_recompute_usd on success; 0 on rejection
    owner_card_outbound_id: str = Field(default="", max_length=200)
    replay: bool = False
    suppressed: bool = False
    price_drift_detected: bool = False
    prior_total_usd: Optional[int] = Field(default=None, ge=0)
    prior_item_count: Optional[int] = Field(default=None, ge=0)
    customer_message_id: Optional[str] = Field(  # PR-CF2 (R1.M1, R3.OBH)
        default=None, max_length=200,
        description="Bridge messageId of the customer finalize message. Optional"
                    " for backward-compat with PR-CF1-vintage rows.",
    )


class CateringCustomerAckSent(_BaseEntry):
    """F5b 2026-05-01: emitted by send-catering-ack after successful customer
    ack POST to the bridge. Without this audit row, customer acks from
    parse_catering_inquiry were silently sent (and frequently silently
    dropped by the bridge filter when the LLM forgot the prefix) with no
    observability. Mirrors CateringQuoteSent pattern but covers the
    parse-inquiry → ack path (not the owner-decision → quote path).

    `customer_jid` is the raw JID (`<phone>@s.whatsapp.net` or `<lid>@lid`)
    rather than a phone string because parse-inquiry frequently sees
    LID-only senders (no phone resolved yet); insisting on E.164 here
    would force callers to fail closed.
    """
    type: Literal["catering_customer_ack_sent"]
    customer_jid: str = Field(min_length=1, max_length=200)
    outbound_message_id: str = Field(min_length=1)
    lead_id: str = Field(default="", description="optional lead linkage when ack follows lead creation")


class CateringCustomerAckFailed(_BaseEntry):
    """F5b 2026-05-01: emitted by send-catering-ack when bridge POST returns
    error or empty messageId. Pairs with CateringCustomerAckSent to give
    full coverage of the customer-ack outbound path.

    Note: the bridge's announcement-filter drop is NOT detectable from the
    HTTP response (filter logs to bridge stderr only) — that gap is tracked
    as PR #43 HIGH-4 follow-up. This variant covers the cases the script
    CAN observe: connection error, non-2xx, malformed response.
    """
    type: Literal["catering_customer_ack_failed"]
    customer_jid: str = Field(min_length=1, max_length=200)
    reason: Literal["bridge_unreachable", "empty_response", "bad_input"]
    detail: str = Field(default="", max_length=2000)


class CateringDispatcherWatchdogFired(_BaseEntry):
    """F7 2026-05-01: catering-dispatcher-watchdog detected a missed catering
    dispatch and triggered the fallback create-catering-lead invocation.
    Companion to CateringDispatcherWatchdogSuppressed (the no-action audit).

    Hermes WhatsApp adapter has no `auto_skill` channel-binding, so on chat
    turns 2+ the LLM may skip invoking `catering_dispatcher` SKILL → no
    `dispatcher_routed` audit, no lead, customer silence. Watchdog catches
    those out-of-band via regex content classification.

    `signals` is the classifier's accumulated evidence (e.g.
    `["primary:catering","headcount:120","food_keyword","delivery_keyword"]`).
    """
    type: Literal["catering_dispatcher_watchdog_fired"]
    chat_id: str = Field(min_length=1, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    customer_phone: E164Phone
    signals: list[str] = Field(default_factory=list, max_length=20)
    success: bool
    detail: str = Field(default="", max_length=2000)


class CateringDispatcherWatchdogSuppressed(_BaseEntry):
    """F7 2026-05-01: watchdog intentionally did NOT fire the fallback. Reasons:
    - non_customer_role: sender is owner/employee — wrong handler chain
    - text_unavailable: gateway.log had no matching inbound message line
    - not_catering: regex classifier rejected the content
    - lid_no_phone_resolution: LID-only sender with no cache hit
    """
    type: Literal["catering_dispatcher_watchdog_suppressed"]
    chat_id: str = Field(min_length=1, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    reason: Literal[
        "non_customer_role", "text_unavailable", "not_catering",
        "lid_no_phone_resolution",
    ]
    detail: str = Field(default="", max_length=2000)


class StateFileMigrated(_BaseEntry):
    """PR-CF5: a state file's on-disk shape was migrated from legacy to current schema.

    Emitted by `tools/migrate-state-files.py --apply` after successfully
    backing up a legacy-shape file and writing the current-shape replacement.

    `from_shape`: JSON-stringified sorted list of legacy keys (e.g.,
                  '["date","sent_count"]')
    `to_shape`: JSON-stringified sorted list of current keys
    `backup_path`: full path to the .pre-migrate-<epoch> backup written before
                   rewrite (matches safe_load_json corrupt-quarantine convention)
    """
    type: Literal["state_file_migrated"]
    file: str = Field(min_length=1, max_length=200)
    from_shape: str = Field(min_length=1, max_length=500)
    to_shape: str = Field(min_length=1, max_length=500)
    backup_path: str = Field(min_length=1, max_length=500)


class StateFileMigrationFailed(_BaseEntry):
    """PR-CF5: migration could not complete — operator must investigate.

    Emitted by `tools/migrate-state-files.py` on any per-file failure path.
    The operator-override case is NOT emitted via this variant — see
    StateFileMigrationOverridden instead (separate semantic event).

    Reason enum:
      - unknown_shape: file shape matches neither current schema nor known-legacy
      - load_failed_non_extra: Pydantic load failed with error other than
                                extra_forbidden (e.g., type mismatch on a
                                key-set-correct file)
      - json_decode_failed: file contains invalid JSON (corrupt at parser level,
                            distinct from schema mismatch)
      - write_failed: backup or atomic_write step failed
      - backup_failed: backup file could not be created
    """
    type: Literal["state_file_migration_failed"]
    file: str = Field(min_length=1, max_length=200)
    reason: Literal[
        "unknown_shape", "load_failed_non_extra", "json_decode_failed",
        "read_failed", "migrator_output_invalid",
        "write_failed", "backup_failed",
    ]
    detail: str = Field(default="", max_length=2000)


class CfRouterIntercepted(_BaseEntry):
    """PR-CF6: cf-router Hermes plugin intercepted an inbound message and either
    invoked a deployed script directly (skipping the LLM) or fired an alert.

    Replaces F8 (catering-owner-action-watchdog) and F9 (shift-missed-dispatch-notifier)
    rescue-layer audit variants with a single plugin-side variant.

    `reason` enum:
      - f8_owner_approve / f8_owner_reject — owner sent #XXXXX approve/reject;
        plugin invoked apply-catering-owner-decision; LLM bypassed
      - f8_menu_yes / f8_menu_no — owner sent #XXXXX yes/no on a pending menu
        update; plugin invoked apply-menu-update
      - f9_sick_call_alert — sick-call regex pattern detected from employee
        sender; plugin fired Pushover P2 to owner; LLM still ran normally
      - f7_primary_new_inquiry — PR-CF1d 2026-05-12: customer sent a
        catering-keyword inbound with NO active lead; plugin invoked
        create-catering-lead directly with customer_name=""; LLM bypassed.
        Replaces the prior F7 rescue-mode after Phase 11 adversarial test
        showed LLM violates HARD RULES under customer pressure.
      - f7_primary_followup_suppressed — PR-CF1d 2026-05-12: customer sent
        a catering-keyword or finalize-intent message AND already has a
        non-terminal lead (status in {AWAITING_OWNER_APPROVAL,
        CUSTOMER_FINALIZED, OWNER_EDITED}); plugin suppressed the follow-up
        to prevent multi-lead-creation bug + LLM proposal-invention; LLM
        bypassed. Optionally a canonical "owner is reviewing" reply is sent.
      - f7_proposal_request — active customer lead asked for proposal/menu
        options; plugin invoked create-catering-proposal-options in
        deterministic menu-grounded mode; LLM bypassed.
      - error — plugin caught an unexpected error during interception
        attempt; LLM still ran normally (plugin returns None on error)

    `code` is the #XXXXX code if applicable. `chat_id` is the WhatsApp chat
    (owner self-chat or employee chat). `subprocess_rc` is the apply-script's
    exit code if a subprocess was invoked.
    """
    type: Literal["cf_router_intercepted"]
    reason: Literal[
        "f8_owner_approve", "f8_owner_reject",
        "f8_menu_yes", "f8_menu_no",
        "f9_sick_call_alert",
        "f7_primary_new_inquiry",          # PR-CF1d 2026-05-12
        "f7_primary_followup_suppressed",  # PR-CF1d 2026-05-12
        "f7_proposal_request",
        "f7_proposal_selection",
        "flyer_primary_project_created",
        "flyer_primary_failed",
        "flyer_project_status",
        "flyer_intake_started",
        "flyer_intake",
        "flyer_intake_failed",
        "flyer_intake_cleanup_failed",
        # 2026-05-28 — intake-bypass when intent is clear. See plan + design
        # at tasks/flyer-intake-bypass-{plan,design}-2026-05-28.md.
        "flyer_intake_bypassed",
        "flyer_onboarding",
        "flyer_onboarding_failed",
        "flyer_starter_brief",
        "flyer_starter_ideas",
        "flyer_customer_not_active",
        "flyer_quota_blocked",
        "flyer_brand_asset_saved",
        "flyer_brand_asset_failed",
        "flyer_business_scope_blocked",
        "flyer_reference_manual_review_queued",
        "flyer_reference_scope_blocked",
        "flyer_reference_scope_use_reference",
        "flyer_reference_scope_authorization_requested",
        "flyer_reference_scope_authorization_followup",
        "flyer_reference_scope_authorized_generated",
        "flyer_reference_exact_edit_queued",
        "flyer_reference_exact_edit_status",
        "flyer_location_blocked",
        "flyer_account_command",
        "flyer_account_failed",
        "flyer_account_customer_not_found",
        "flyer_account_unhandled",
        "flyer_regulated_account_guard",
        "flyer_delivery_state_guard",
        "flyer_delivery_state_status_surfaced",
        "flyer_active_project_bypassed",
        "flyer_brief_approved",
        "flyer_brief_project_create_failed",
        "flyer_starter_preference_off",
        "flyer_starter_already_sent",
        "flyer_sample_prompt_requested",
        "flyer_trial_link_recovery",
        "flyer_guest_order_started",
        "flyer_guest_order_failed",
        "flyer_access_finalize_failed",
        "flyer_access_release_failed",
        "flyer_pending_revision_confirmation_reminder",
        "error",
    ]
    chat_id: str = Field(min_length=1, max_length=200)
    code: Optional[str] = Field(default=None, max_length=10)
    subprocess_rc: Optional[int] = Field(default=None)
    detail: str = Field(default="", max_length=2000)


class StateFileMigrationOverridden(_BaseEntry):
    """PR-CF5: operator used STATE_MIGRATION_OVERRIDE=skip to bypass the gate.

    Separate from StateFileMigrationFailed because the override is a
    deliberate operator action, not a failure. `file` is omitted because
    the override skips ALL files (no per-file action). Mirrors the
    HERMES_PIN_OVERRIDE audit pattern.
    """
    type: Literal["state_file_migration_overridden"]
    reason: str = Field(
        min_length=1, max_length=2000,
        description="Operator-supplied STATE_MIGRATION_OVERRIDE_REASON",
    )


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


class MultiLocationClosestLookup(_BaseEntry):
    """PR-Agent3-v0.1: a customer (sender_role=unknown) asked for the
    nearest store; closest-location.py returned top-N by drive minutes.

    Address is NOT stored (PII concern — would land in plain-text
    decisions.log). Only operationally relevant fields persisted:
    chat_id, customer geo (only when supplied as lat/lon, NOT geocoded
    from address text), nearest location id, drive minutes, n returned,
    and the routing source ('osrm' for live, 'haversine_fallback' for
    OSRM-down degraded mode, 'not_configured' when locations is empty).
    """
    type: Literal["multi_location_closest_lookup"]
    chat_id: str = Field(min_length=1, max_length=200)
    customer_lat: Optional[float] = None
    customer_lon: Optional[float] = None
    nearest_location_id: Optional[str] = Field(default=None, max_length=40)
    nearest_drive_minutes: Optional[float] = None
    n_locations_returned: int = Field(ge=0, le=50)
    source: Literal["osrm", "haversine_fallback", "not_configured"] = "osrm"
    detail: str = Field(default="", max_length=2000)


# ─────────────────────────────────────────────────────────────────
# Compliance Calendar log entries (Agent #13 — PR-Agent13-v0.1 2026-05-04)
# ─────────────────────────────────────────────────────────────────


class ComplianceReminderAttempted(_BaseEntry):
    """Idempotency anchor — written BEFORE bridge POST. Mirror of BriefAttempted.

    On crash between bridge_post success and ComplianceReminderSent log write,
    the next tick scans for orphan Attempted-without-Sent within the recovery
    window and refuses to re-fire (operator manual-verify required).
    """
    type: Literal["compliance_reminder_attempted"]
    item_id: str = Field(min_length=1, max_length=40)
    item_name: str = Field(min_length=1, max_length=100)  # snapshot for forensics
    days_until_renewal: int  # negative = overdue
    gate_days: int = Field(ge=-3650, le=3650)  # 30/14/7/3/1/0/-N self-describing; bounded to match recurrence_days ceiling (Reviewer H3)
    attempt_id: str = Field(min_length=1)  # uuid4
    catchup_for_missed_gate: Optional[int] = None  # gate_days value being caught up


class ComplianceReminderSent(_BaseEntry):
    """Written AFTER bridge 200 + non-empty messageId."""
    type: Literal["compliance_reminder_sent"]
    item_id: str = Field(min_length=1, max_length=40)
    days_until_renewal: int
    gate_days: int = Field(ge=-3650, le=3650)
    attempt_id: str = Field(min_length=1)  # links to ComplianceReminderAttempted
    outbound_message_id: str = Field(min_length=1)


class ComplianceReminderFailed(_BaseEntry):
    """Bridge unreachable / non-2xx after retry / send_uncertain."""
    type: Literal["compliance_reminder_failed"]
    item_id: str = Field(min_length=1, max_length=40)
    days_until_renewal: int
    gate_days: int = Field(ge=-3650, le=3650)
    attempt_id: str = Field(min_length=1)
    error: str  # no length cap (matches OutboundSendFailed pattern)
    retry_count: int = Field(ge=0)


class ComplianceReminderSkipped(_BaseEntry):
    """Skipped due to orphan-attempted detection (Layer 3 idempotency).
    Operator must manually verify the prior attempt in WhatsApp before
    next tick will fire."""
    type: Literal["compliance_reminder_skipped"]
    item_id: str = Field(min_length=1, max_length=40)
    gate_days: int = Field(ge=-3650, le=3650)
    reason: Literal["orphan_attempted_in_window"]
    orphan_attempt_id: str = Field(min_length=1)


class ComplianceReminderDeferred(_BaseEntry):
    """Gate window passed (>cfg.compliance.max_deferral_days late).
    Operator gets Pushover; we give up on this gate to avoid spamming
    for stale-gate work."""
    type: Literal["compliance_reminder_deferred"]
    item_id: str = Field(min_length=1, max_length=40)
    days_until_renewal: int
    gate_days: int = Field(ge=-3650, le=3650)
    days_since_ideal_fire: int  # how late the gate was
    operator_pushover_sent: bool  # whether the Pushover delivery succeeded


class ComplianceItemMarkedDone(_BaseEntry):
    """Owner marked item done; state file mutated.
    sentinel_keys_pruned: how many sentinel entries with prefix '<item_id>:'
    were dropped (one-shot deletion clears all gates; recurring renewal
    advances renewal_date — sentinel for current cycle stays until pruned
    naturally at next tick's GC)."""
    type: Literal["compliance_item_marked_done"]
    item_id: str = Field(min_length=1, max_length=40)
    completed_renewal_date: date
    next_renewal_date: Optional[date] = None  # None for recurrence_days=0 (item deleted)
    actor: Literal["owner", "operator", "system"]
    sentinel_keys_pruned: int = Field(ge=0)


# ─────────────────────────────────────────────────────────────────
# P&L Anomaly Detective audit variants (Agent #22 — PR-Agent22-v0.1 2026-05-04)
# v0.1 ships these as scaffold; v0.2 anomaly-detection logic emits PnlAnomalyDetected,
# v0.1 SKILL emits PnlAnomalyDeclined when invoked while disabled.
# ─────────────────────────────────────────────────────────────────


class PnlAnomalyDetected(_BaseEntry):
    """Anomaly detected by v0.2 detection logic. v0.1 placeholder — no
    emitter yet (cfg.pnl_anomaly.enabled defaults False)."""
    type: Literal["pnl_anomaly_detected"]
    anomaly_type: Literal["margin_drop", "location_underperform"]
    target_id: str = Field(min_length=1, max_length=100)  # product_id or location_id
    delta_pct: float
    baseline_value: float
    current_value: float
    detail: str = Field(default="", max_length=500)


class PnlAnomalyDeclined(_BaseEntry):
    """SKILL invoked but cfg.pnl_anomaly.enabled = False; declined politely."""
    type: Literal["pnl_anomaly_declined"]
    requester_role: Literal["owner", "employee", "unknown"]
    reason: Literal["agent_disabled", "no_pos_configured"]


# ─────────────────────────────────────────────────────────────────
# Equipment & Maintenance audit variants (Agent #19 — PR-Agent19-v0.1 2026-05-04)
# v0.1 scaffold: full agent v0.2 deferred until customer equipment list onboarded.
# ─────────────────────────────────────────────────────────────────


class EquipmentIssueLogged(_BaseEntry):
    """Staff reported equipment issue; structured intake (v0.2 emitter)."""
    type: Literal["equipment_issue_logged"]
    equipment_id: str = Field(min_length=1, max_length=80)
    location_id: Optional[str] = Field(default=None, max_length=40)
    issue_category: Literal["broken", "leaking", "noisy", "preventive_due", "other"]
    severity: Literal["low", "medium", "high", "critical"]
    detail: str = Field(default="", max_length=500)


class EquipmentMaintenanceDeclined(_BaseEntry):
    """SKILL invoked but cfg.equipment_maintenance.enabled = False."""
    type: Literal["equipment_maintenance_declined"]
    requester_role: Literal["owner", "employee", "unknown"]
    reason: Literal["agent_disabled"]


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


# ─────────────────────────────────────────────────────────────────
# PR-ζ 2026-05-26 — Chokepoint refusal audit variants
# ─────────────────────────────────────────────────────────────────
#
# Both rows are written by `safe_io._emit_audit_row` when the chokepoint
# refuses a send. The audit-row write must succeed for the refusal to land
# durably; the helper uses `FileLock(<path>.lock)` per ndjson_append's
# documented contract, and propagates any write failure (no swallow).


class _RegulatedSendMissingActionContext(_BaseEntry):
    """The chokepoint refused a send because action_context was None AND the
    calling script's basename is not in `SAFE_IO_NULL_CONTEXT_ALLOWLIST`."""
    type: Literal["regulated_send_missing_action_context"]
    caller_script: str = Field(..., max_length=200)
    jid: str = Field(..., max_length=200)
    message_preview: str = Field(..., max_length=120)


class _RegulatedSendLintViolation(_BaseEntry):
    """The chokepoint refused a send because the caller passed a regulated
    ActionExecutionContext with `verified_action_result=False` AND the
    message tripped one or more forbidden completion verbs from
    `customer_copy_policy.lint_no_unverified_completion`."""
    type: Literal["regulated_send_lint_violation"]
    action_id: str = Field(..., max_length=200)
    audit_row_id: Optional[str] = Field(default=None, max_length=200)
    jid: str = Field(..., max_length=200)
    # PR-ζ caps verb_hits at 20 — the chokepoint truncates before construction
    # so a pathological >20-verb message still refuses cleanly (no
    # ValidationError mid-refusal).
    verb_hits: list[str] = Field(..., max_length=20)
    message_preview: str = Field(..., max_length=120)


# ─────────────────────────────────────────────────────────────────
# Commerce primitive LogEntry variants — slice 1 (PRD v2 §8)
# Slice 1 emits: cart_started/updated/cleared/expired/checked_out,
# order_created/status_change/cancelled/create_refused_category,
# payment_intent_minted, payment_link_attempted/sent, payment_intent_voided,
# payment_dedup_blocked.
# Reserved (declared now, emitted slice 2+): confirmed, webhook_received,
# webhook_verify_failed, refunded, chargeback_received,
# owner_approval_required, owner_approval_threshold_unconfigured,
# blocked_category_override, payment_link_failed.
# ─────────────────────────────────────────────────────────────────

class CommerceCartStarted(_BaseEntry):
    type: Literal["commerce_cart_started"]
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, max_length=120)
    chat_id: str = Field(max_length=200)


class CommerceCartUpdated(_BaseEntry):
    type: Literal["commerce_cart_updated"]
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    op: Literal["add", "remove", "update_qty"]
    sku: str = Field(min_length=1, max_length=80)
    qty_before: int = Field(ge=0)
    qty_after: int = Field(ge=0)
    subtotal_cents: int = Field(ge=0)


class CommerceCartCleared(_BaseEntry):
    type: Literal["commerce_cart_cleared"]
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    reason: str = Field(max_length=200)


class CommerceCartExpired(_BaseEntry):
    type: Literal["commerce_cart_expired"]
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    expired_at: datetime


class CommerceCartCheckedOut(_BaseEntry):
    type: Literal["commerce_cart_checked_out"]
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    subtotal_cents: int = Field(ge=0)


class CommerceOrderCreated(_BaseEntry):
    type: Literal["commerce_order_created"]
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    cart_id: str = Field(pattern=r"^CC\d{5,}$")
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, max_length=120)
    total_cents: int = Field(ge=0)
    currency: str = Field(max_length=3)


class CommerceOrderStatusChange(_BaseEntry):
    type: Literal["commerce_order_status_change"]
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    prev_status: CommerceOrderStatus
    next_status: CommerceOrderStatus
    actor: Literal["customer", "caller", "operator", "cron", "webhook"]
    cause: str = Field(max_length=200)


class CommerceOrderCancelled(_BaseEntry):
    type: Literal["commerce_order_cancelled"]
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    reason: str = Field(max_length=200)
    actor: Literal["customer", "operator", "cron"]


class CommerceOrderActionRefused(_BaseEntry):
    """Audited refusal of an operator-initiated cockpit order action (Slice C).

    Emitted whenever a staff status-transition request is declined: outside the
    Slice-C allowlist, an illegal transition, a stale optimistic-concurrency
    view, or an unknown order. `order_id` is intentionally NOT pattern-bound —
    a refused action may carry a malformed/unknown id (that can be the reason
    it was refused)."""
    type: Literal["commerce_order_action_refused"]
    order_id: str = Field(min_length=1, max_length=64)
    attempted_to_status: Optional[CommerceOrderStatus] = None
    from_status: Optional[CommerceOrderStatus] = None
    reason: Literal[
        "illegal_transition",
        "stale_expected_status",
        "order_not_found",
        "not_allowed_in_slice_c",
    ]
    actor: Literal["operator"] = "operator"
    cause: str = Field(default="", max_length=200)


class CommerceRefusedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=200)


class CommerceOrderCreateRefusedCategory(_BaseEntry):
    type: Literal["commerce_order_create_refused_category"]
    sender_phone: Optional[E164Phone] = None
    sender_lid: Optional[str] = Field(default=None, max_length=120)
    refused_skus: list[str] = Field(default_factory=list, max_length=50)
    # Reviewer B MEDIUM-2: carry display_name so callers can render
    # category-agnostic customer copy without re-loading the cart.
    refused_items: list[CommerceRefusedItem] = Field(default_factory=list, max_length=50)
    reason: str = Field(max_length=80)


class CommercePaymentIntentMinted(_BaseEntry):
    type: Literal["commerce_payment_intent_minted"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    originating_message_id: str = Field(default="", max_length=200)
    amount_cents: int = Field(ge=1)
    currency: str = Field(max_length=3)
    provider: Literal["placeholder", "stripe", "razorpay", "upi", "zelle", "cashapp", "manual"]


class CommercePaymentLinkAttempted(_BaseEntry):
    type: Literal["commerce_payment_link_attempted"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")


class CommercePaymentLinkSent(_BaseEntry):
    type: Literal["commerce_payment_link_sent"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")


class CommercePaymentLinkFailed(_BaseEntry):
    type: Literal["commerce_payment_link_failed"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    reason: str = Field(max_length=200)


class CommercePaymentIntentVoided(_BaseEntry):
    type: Literal["commerce_payment_intent_voided"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    reason: str = Field(max_length=200)
    actor: str = Field(max_length=40)


class CommercePaymentConfirmed(_BaseEntry):
    type: Literal["commerce_payment_confirmed"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    payment_reference: str = Field(min_length=1, max_length=200)


class CommercePaymentDedupBlocked(_BaseEntry):
    type: Literal["commerce_payment_dedup_blocked"]
    reference: str = Field(min_length=1, max_length=200)
    attempted_order_id: str = Field(pattern=r"^CO\d{5,}$")
    original_order_id: str = Field(pattern=r"^CO\d{5,}$")


class CommercePaymentWebhookReceived(_BaseEntry):
    type: Literal["commerce_payment_webhook_received"]
    provider: Literal["stripe", "razorpay", "upi", "zelle", "cashapp", "manual"]
    intent_id_claimed: str = Field(default="", max_length=40)
    verified: bool


class CommercePaymentWebhookVerifyFailed(_BaseEntry):
    type: Literal["commerce_payment_webhook_verify_failed"]
    provider: Literal["stripe", "razorpay", "upi", "zelle", "cashapp", "manual"]
    raw_signature: str = Field(default="", max_length=500)
    computed_digest: str = Field(default="", max_length=500)


class CommercePaymentRefunded(_BaseEntry):
    type: Literal["commerce_payment_refunded"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    refund_reference: str = Field(min_length=1, max_length=200)
    amount_cents: int = Field(ge=1)
    is_partial: bool = False


class CommercePaymentChargebackReceived(_BaseEntry):
    type: Literal["commerce_payment_chargeback_received"]
    intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    provider_reference: str = Field(min_length=1, max_length=200)
    amount_cents: int = Field(ge=1)
    arrived_after_refund: bool = False


class CommerceOrderOwnerApprovalRequired(_BaseEntry):
    type: Literal["commerce_order_owner_approval_required"]
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    amount_cents: int = Field(ge=1)


class CommerceOrderOwnerApprovalThresholdUnconfigured(_BaseEntry):
    type: Literal["commerce_order_owner_approval_threshold_unconfigured"]
    order_id: str = Field(pattern=r"^CO\d{5,}$")
    amount_cents: int = Field(ge=1)


class CommerceBlockedCategoryOverride(_BaseEntry):
    type: Literal["commerce_blocked_category_override"]
    category: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=400)
    approver: str = Field(min_length=1, max_length=80)
    expires_at: datetime


# ─────────────────────────────────────────────────────────────────
# Slice-2 catering deposit caller (feat/commerce-slice2-catering-deposit-caller)
# Reconciliation invariant #4: callers MUST carry commerce_order_id +
# commerce_payment_intent_id cross-ref fields so Cash & AR can join the
# commerce_* and catering_* audit streams.
# ─────────────────────────────────────────────────────────────────

class CateringDepositLinkSent(_BaseEntry):
    """Successful mint+send of a catering deposit link.

    Emitted by catering-mint-deposit after both the slice-1 commerce_payment_link
    primitive returned OK AND the WhatsApp bridge POST returned ok=True. When
    url_status=="unconfigured" the customer received the "Payment link is not
    configured yet" copy (template empty) — the audit row still fires because
    the bridge POST succeeded; only the link itself was unactionable.
    """
    type: Literal["catering_deposit_link_sent"]
    lead_id: str = Field(min_length=1, max_length=40)
    commerce_order_id: str = Field(pattern=r"^CO\d{5,}$")
    commerce_payment_intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    amount_cents: int = Field(ge=1, le=10_000_000_000)
    url_status: Literal["configured", "unconfigured"]
    outbound_message_id: str = Field(min_length=1, max_length=200)


class CateringDepositLinkFailed(_BaseEntry):
    """Failed mint or send of a catering deposit link. NEVER rolls back the
    quote-send transaction; failure is purely a deposit-side concern.

    commerce_* fields are optional because some failure modes (zero_amount,
    below_minimum, cart_build_failed) occur before any slice-1 primitive
    returned an id.
    """
    type: Literal["catering_deposit_link_failed"]
    lead_id: str = Field(min_length=1, max_length=40)
    reason: Literal[
        "zero_amount",
        "below_minimum",
        "cart_build_failed",
        "order_create_failed",
        "intent_mint_failed",
        "bridge_send_failed",
        "subprocess_timeout",
    ]
    detail: str = Field(default="", max_length=500)
    commerce_order_id: str = Field(default="", max_length=40)
    commerce_payment_intent_id: str = Field(default="", max_length=40)


# ─────────────────────────────────────────────────────────────────
# Slice-3 PR-2 catering deposit confirmation
# Emitted by commerce-payment-confirm after Stripe webhook confirms a
# deposit payment + lead.deposit_status flipped to "paid".
# ─────────────────────────────────────────────────────────────────

class CateringDepositPaid(_BaseEntry):
    """Catering deposit payment confirmed via webhook. Carries cross-ref
    fields per reconciliation invariant #4 for Cash & AR join."""
    type: Literal["catering_deposit_paid"]
    lead_id: str = Field(min_length=1, max_length=40)
    commerce_order_id: str = Field(pattern=r"^CO\d{5,}$")
    commerce_payment_intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    payment_reference: str = Field(min_length=1, max_length=200)
    amount_cents: int = Field(ge=1, le=10_000_000_000)


class CommercePaymentConfirmationFailed(_BaseEntry):
    """Failed webhook-driven payment confirmation. The intent itself is NOT
    advanced (state stays in minted/sent). The customer's actual payment may
    have succeeded at Stripe; operator-side reconciliation required."""
    type: Literal["commerce_payment_confirmation_failed"]
    commerce_intent_id: str = Field(default="", max_length=40)
    commerce_order_id: str = Field(default="", max_length=40)
    lead_id: str = Field(default="", max_length=40)
    reason: Literal[
        "signature_invalid",
        "sdk_not_installed",                # PR-2 review B-LOW-1: separate from signature_invalid
        "empty_payment_reference",
        "missing_metadata",
        "intent_not_found",
        "currency_mismatch",
        "amount_mismatch",
        "reference_reused_other_order",     # PR-2 review A-MEDIUM-2: disambiguates from slice-1 dedup_blocked
        "mark_confirmed_failed",
        "illegal_transition",               # PR-2 review A-HIGH-1: order in cancelled/voided/refunded
        "config_load_failed",
    ]
    detail: str = Field(default="", max_length=500)


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
        # Hermes config.yaml shape gate override audit (M2 closure)
        Annotated[ConfigGateOverride, Tag("config_gate_override")],
        Annotated[UnknownSenderDeclined, Tag("unknown_sender_declined")],
        Annotated[ValidateFailed, Tag("validate_failed")],
        Annotated[InvariantViolation, Tag("invariant_violation")],
        Annotated[HealthCheckFailure, Tag("health_check_failure")],
        # Agent #41 Owner Wellbeing v0.1
        Annotated[OwnerNotificationSuppressed, Tag("owner_notification_suppressed")],
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
        # Agent #33 Loyalty v0.1 (birthday store mutations)
        Annotated[CustomerBirthdayRecorded, Tag("customer_birthday_recorded")],
        # Agent #5 EOD Reconciliation
        Annotated[EodSnapshot, Tag("eod_snapshot")],
        Annotated[EodPushoverSent, Tag("eod_pushover_sent")],
        Annotated[EodSkipped, Tag("eod_skipped")],
        # Agent #3 Multi-Location Coordinator
        Annotated[CrossLocationQuery, Tag("cross_location_query")],
        Annotated[InterLocationTransferProposed, Tag("inter_location_transfer_proposed")],
        # PR-Agent3-v0.1 2026-05-04
        Annotated[MultiLocationClosestLookup, Tag("multi_location_closest_lookup")],
        # PR-Agent13-v0.1 2026-05-04 — Compliance Calendar
        Annotated[ComplianceReminderAttempted, Tag("compliance_reminder_attempted")],
        Annotated[ComplianceReminderSent, Tag("compliance_reminder_sent")],
        Annotated[ComplianceReminderFailed, Tag("compliance_reminder_failed")],
        Annotated[ComplianceReminderSkipped, Tag("compliance_reminder_skipped")],
        Annotated[ComplianceReminderDeferred, Tag("compliance_reminder_deferred")],
        Annotated[ComplianceItemMarkedDone, Tag("compliance_item_marked_done")],
        # PR-Agent22-v0.1 2026-05-04 — P&L Anomaly Detective scaffold
        Annotated[PnlAnomalyDetected, Tag("pnl_anomaly_detected")],
        Annotated[PnlAnomalyDeclined, Tag("pnl_anomaly_declined")],
        # PR-Agent19-v0.1 2026-05-04 — Equipment & Maintenance scaffold
        Annotated[EquipmentIssueLogged, Tag("equipment_issue_logged")],
        Annotated[EquipmentMaintenanceDeclined, Tag("equipment_maintenance_declined")],
        # Agent #2 Catering Lead
        Annotated[CateringLeadCreated, Tag("catering_lead_created")],
        Annotated[CateringLeadStatusChange, Tag("catering_lead_status_change")],
        Annotated[CateringLeadRejected, Tag("catering_lead_rejected")],
        Annotated[CateringQuoteDrafted, Tag("catering_quote_drafted")],
        Annotated[CateringOwnerApprovalRequested, Tag("catering_owner_approval_requested")],
        Annotated[CateringOwnerDecision, Tag("catering_owner_decision")],
        Annotated[CateringQuoteSent, Tag("catering_quote_sent")],
        Annotated[CateringProposalsGenerated, Tag("catering_proposals_generated")],
        Annotated[CateringProposalGenerationFailed, Tag("catering_proposal_generation_failed")],
        Annotated[CateringProposalSelected, Tag("catering_proposal_selected")],
        Annotated[CateringProposalSelectionFailed, Tag("catering_proposal_selection_failed")],
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
        # PR-B v0.4: SKILL → apply-script handoff failure (LLM-drafted quote)
        Annotated[CateringQuoteSkillFailed, Tag("catering_quote_skill_failed")],
        # PR-CF1: customer-finalize-menu audit (one variant for success + rejection,
        # discriminated by `outcome` field — no Tag-union bloat)
        Annotated[CateringMenuFinalized, Tag("catering_menu_finalized")],
        # F5b 2026-05-01: customer-ack outbound observability (parse-inquiry path)
        Annotated[CateringCustomerAckSent, Tag("catering_customer_ack_sent")],
        Annotated[CateringCustomerAckFailed, Tag("catering_customer_ack_failed")],
        # F7 2026-05-01: dispatcher watchdog (missed-SKILL recovery)
        Annotated[CateringDispatcherWatchdogFired, Tag("catering_dispatcher_watchdog_fired")],
        Annotated[CateringDispatcherWatchdogSuppressed, Tag("catering_dispatcher_watchdog_suppressed")],
        # PR-CF5 2026-05-03: state-file migration audit (legacy schema → current)
        Annotated[StateFileMigrated, Tag("state_file_migrated")],
        Annotated[StateFileMigrationFailed, Tag("state_file_migration_failed")],
        Annotated[StateFileMigrationOverridden, Tag("state_file_migration_overridden")],
        # PR-CF6 2026-05-03: cf-router Hermes plugin (supersedes F8 + F9
        # watchdogs; their audit variants were removed in the 2026-05-04
        # canonical-cleanup pass — see git tag pre-srilu-cleanup-2026-05-04
        # for the deleted CateringOwnerActionWatchdog* and
        # ShiftMissedDispatch* class definitions if rollback ever needed).
        Annotated[CfRouterIntercepted, Tag("cf_router_intercepted")],
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
        # Hermes Flyer Studio
        Annotated[FlyerProjectCreated, Tag("flyer_project_created")],
        Annotated[FlyerStatusChange, Tag("flyer_status_change")],
        Annotated[FlyerAssetsDelivered, Tag("flyer_assets_delivered")],
        Annotated[FlyerDeliveryFailed, Tag("flyer_delivery_failed")],
        Annotated[FlyerCustomerCreated, Tag("flyer_customer_created")],
        Annotated[FlyerCustomerActivated, Tag("flyer_customer_activated")],
        Annotated[FlyerAccountUpdated, Tag("flyer_account_updated")],
        Annotated[FlyerUsageRecorded, Tag("flyer_usage_recorded")],
        Annotated[FlyerQuotaBlocked, Tag("flyer_quota_blocked")],
        Annotated[FlyerAutoRepairAttempted, Tag("flyer_autorepair_attempted")],
        Annotated[FlyerAutoRepairSucceeded, Tag("flyer_autorepair_succeeded")],
        Annotated[FlyerAutoRepairExhausted, Tag("flyer_autorepair_exhausted")],
        Annotated[FlyerAutoRepairSkipped, Tag("flyer_autorepair_skipped")],
        Annotated[FlyerRecoveryIncidentOpened, Tag("flyer_recovery_incident_opened")],
        Annotated[FlyerRecoveryCustomerAckAttempted, Tag("flyer_recovery_customer_ack_attempted")],
        Annotated[FlyerRecoveryCustomerAckSent, Tag("flyer_recovery_customer_ack_sent")],
        Annotated[FlyerRecoveryCustomerAckFailed, Tag("flyer_recovery_customer_ack_failed")],
        Annotated[FlyerRecoveryCustomerAckUncertain, Tag("flyer_recovery_customer_ack_uncertain")],
        Annotated[FlyerRecoveryCustomerAckSuppressed, Tag("flyer_recovery_customer_ack_suppressed")],
        Annotated[FlyerRecoveryRepairBundleWritten, Tag("flyer_recovery_repair_bundle_written")],
        Annotated[FlyerRecoveryOutcomeRepaired, Tag("flyer_recovery_outcome_repaired")],
        Annotated[FlyerRecoveryDeployGate, Tag("flyer_recovery_deploy_gate")],
        Annotated[FlyerRecoveryResolved, Tag("flyer_recovery_resolved")],
        Annotated[FlyerRecoveryOperatorActionRequired, Tag("flyer_recovery_operator_action_required")],
        Annotated[FlyerRecoveryOwnerAlert, Tag("flyer_recovery_owner_alert")],
        Annotated[FlyerClosureCustomerNotified, Tag("flyer_closure_customer_notified")],
        Annotated[FlyerStatusResent, Tag("flyer_status_resent")],
        Annotated[FlyerManualQueueCustomerUpdate, Tag("flyer_manual_queue_customer_update")],
        # NEW — source-contract observability (2026-05-20 flyer source-contract-first)
        Annotated[FlyerSourceContractExtracted, Tag("flyer_source_contract_extracted")],
        Annotated[FlyerSourceVsNewChosen, Tag("flyer_source_vs_new_chosen")],
        Annotated[FlyerSourceEditSlaAlert, Tag("flyer_source_edit_sla_alert")],
        Annotated[FlyerHermesIntentDecision, Tag("flyer_hermes_intent_decision")],
        # 2026-05-28 — intake-bypass audit pair (decision + outcome)
        Annotated[FlyerIntakeBypassed, Tag("flyer_intake_bypassed")],
        Annotated[FlyerIntakeBypassOutcome, Tag("flyer_intake_bypass_outcome")],
        # P0 #2 2026-05-28 — severity-tiered visual QA audit variants
        Annotated[FlyerQASeverityClassified, Tag("flyer_qa_severity_classified")],
        Annotated[FlyerWarnTierDelivered, Tag("flyer_warn_tier_delivered")],
        Annotated[FlyerOperatorFlaggedWarnTier, Tag("flyer_operator_flagged_warn_tier")],
        # PR3 2026-06-05 — Creative-Director wiring caller-provenance audit
        Annotated[FlyerCreativeDirectorRouted, Tag("flyer_creative_director_routed")],
        # PR-ζ 2026-05-26 — chokepoint refusal audit variants
        Annotated[_RegulatedSendMissingActionContext, Tag("regulated_send_missing_action_context")],
        Annotated[_RegulatedSendLintViolation, Tag("regulated_send_lint_violation")],
        # Commerce primitives slice 1 — PRD v2 §8
        Annotated[CommerceCartStarted, Tag("commerce_cart_started")],
        Annotated[CommerceCartUpdated, Tag("commerce_cart_updated")],
        Annotated[CommerceCartCleared, Tag("commerce_cart_cleared")],
        Annotated[CommerceCartExpired, Tag("commerce_cart_expired")],
        Annotated[CommerceCartCheckedOut, Tag("commerce_cart_checked_out")],
        Annotated[CommerceOrderCreated, Tag("commerce_order_created")],
        Annotated[CommerceOrderStatusChange, Tag("commerce_order_status_change")],
        Annotated[CommerceOrderCancelled, Tag("commerce_order_cancelled")],
        Annotated[CommerceOrderActionRefused, Tag("commerce_order_action_refused")],
        Annotated[CommerceOrderCreateRefusedCategory, Tag("commerce_order_create_refused_category")],
        Annotated[CommercePaymentIntentMinted, Tag("commerce_payment_intent_minted")],
        Annotated[CommercePaymentLinkAttempted, Tag("commerce_payment_link_attempted")],
        Annotated[CommercePaymentLinkSent, Tag("commerce_payment_link_sent")],
        Annotated[CommercePaymentLinkFailed, Tag("commerce_payment_link_failed")],
        Annotated[CommercePaymentIntentVoided, Tag("commerce_payment_intent_voided")],
        Annotated[CommercePaymentConfirmed, Tag("commerce_payment_confirmed")],
        Annotated[CommercePaymentDedupBlocked, Tag("commerce_payment_dedup_blocked")],
        Annotated[CommercePaymentWebhookReceived, Tag("commerce_payment_webhook_received")],
        Annotated[CommercePaymentWebhookVerifyFailed, Tag("commerce_payment_webhook_verify_failed")],
        Annotated[CommercePaymentRefunded, Tag("commerce_payment_refunded")],
        Annotated[CommercePaymentChargebackReceived, Tag("commerce_payment_chargeback_received")],
        Annotated[CommerceOrderOwnerApprovalRequired, Tag("commerce_order_owner_approval_required")],
        Annotated[CommerceOrderOwnerApprovalThresholdUnconfigured, Tag("commerce_order_owner_approval_threshold_unconfigured")],
        Annotated[CommerceBlockedCategoryOverride, Tag("commerce_blocked_category_override")],
        # Slice-2 catering deposit caller
        Annotated[CateringDepositLinkSent, Tag("catering_deposit_link_sent")],
        Annotated[CateringDepositLinkFailed, Tag("catering_deposit_link_failed")],
        # Slice-3 PR-2: catering deposit confirmation + commerce confirmation-failure
        Annotated[CateringDepositPaid, Tag("catering_deposit_paid")],
        Annotated[CommercePaymentConfirmationFailed, Tag("commerce_payment_confirmation_failed")],
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
    "OwnerWellbeingConfig", "OwnerWellbeingDay", "OwnerNotificationSuppressed",
    "LoyaltyConfig", "CustomerBirthday", "CustomerBirthdayStore",
    "CustomerBirthdayRecorded",
    "EodConfig",
    "LocationEntry", "MultiLocationConfig",
    "CateringConfig", "CateringLeadStatus", "CateringLeadExtractedFields",
    "CateringLead", "CateringLeadStore",
    "CateringProposalStatus", "CateringProposalTier",
    "CateringProposalOption", "CateringProposalSet", "CateringProposalStore",
    "CateringLearningSource", "CateringLearningProposalHealth",
    "CateringLearningSummary",
    "is_catering_terminal", "CATERING_TERMINAL_STATUSES",
    "FlyerConfig", "FlyerRecoveryConfig", "FlyerWorkflowStatus", "FlyerOnboardingStatus", "FlyerLanguage", "FlyerCreationMode",
    "FlyerIntakeStatus", "FlyerIntakeSource", "FlyerOutputFormat", "FlyerImageQuality",
    "FlyerConfig", "FlyerRenderProviderConfig", "FlyerDraftProviderPolicy", "FlyerFinalProviderPolicy",
    "FlyerSourceEditProviderPolicy",
    "FlyerTextHeavyDraftPolicy", "FlyerVisualHeavyDraftPolicy",
    "FlyerWorkflowStatus", "FlyerOnboardingStatus", "FlyerLanguage", "FlyerCreationMode",
    "FlyerIntakeStatus", "FlyerIntakeSource", "FlyerOutputFormat", "FlyerImageQuality", "FlyerProviderQuality",
    "FlyerFactSource", "FlyerReferenceRole", "FlyerReferenceExtractionStatus",
    "FlyerVisualQAStatus", "FlyerVisualQASource", "FlyerManualReviewStatus", "FlyerManualReviewReason",
    "FlyerAssetKind", "FLYER_TRANSITIONS", "is_flyer_transition_allowed",
    "FlyerPlanTier", "FlyerBrandAsset", "FlyerUsageEvent", "FlyerPaymentRecord", "FlyerGuestOrder",
    "FLYER_AUTHORIZED_REQUESTER_LIMIT",
    "FlyerCustomerProfile", "FlyerOnboardingSession", "FlyerIntakeSession", "FlyerCustomerStore", "FlyerGuestOrderStore",
    "FlyerRequestFields", "FlyerLockedFact", "FlyerReferenceExtraction",
    "FlyerSourceContractSection", "FlyerSourceContract",
    "FlyerSourceContractExtracted", "FlyerSourceVsNewChosen", "FlyerHermesIntentDecision",
    "FlyerIntakeBypassed", "FlyerIntakeBypassOutcome",
    "FlyerQASeverityClassified", "FlyerWarnTierDelivered", "FlyerOperatorFlaggedWarnTier",
    "FlyerCreativeDirectorRouted",
    # PR-ζ 2026-05-26 — regulated-intent runtime context + chokepoint audit variants
    "ActionExecutionContext",
    "FlyerVisualQAReport", "FlyerWarningSummary", "FlyerManualReview", "FlyerAsset", "FlyerConcept", "FlyerRevision",
    "FlyerBrandKit", "FlyerProject", "FlyerProjectStore",
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
    "FlyerProjectCreated", "FlyerStatusChange",
    "FlyerAssetsDelivered", "FlyerDeliveryFailed",
    "FlyerCustomerCreated", "FlyerCustomerActivated", "FlyerAccountUpdated",
    "FlyerUsageRecorded", "FlyerQuotaBlocked",
    "FlyerRepairMode", "FlyerRepairStatus", "FlyerRepairAttempt", "FlyerAutoRepairAttemptStore",
    "FlyerAutoRepairAttempted", "FlyerAutoRepairSucceeded", "FlyerAutoRepairExhausted", "FlyerAutoRepairSkipped",
    "FlyerRecoveryIncidentOpened", "FlyerRecoveryCustomerAckAttempted",
    "FlyerRecoveryCustomerAckSent", "FlyerRecoveryCustomerAckFailed",
    "FlyerRecoveryCustomerAckUncertain", "FlyerRecoveryCustomerAckSuppressed",
    "FlyerRecoveryRepairBundleWritten", "FlyerRecoveryOutcomeRepaired", "FlyerRecoveryDeployGate", "FlyerRecoveryResolved",
    "FlyerRecoveryOperatorActionRequired", "FlyerRecoveryOwnerAlert",
    "FlyerUsageRecorded", "FlyerQuotaBlocked", "FlyerClosureCustomerNotified",
    "FlyerStatusResent", "FlyerManualQueueCustomerUpdate",
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
    "AgentStateChange", "UnknownSenderDeclined", "ValidateFailed", "InvariantViolation", "HealthCheckFailure",
    "LidLearned", "DispatcherRouted",
    "BriefAttempted", "BriefSent", "BriefSendFailed", "BriefSkipped",
    "EodSnapshot", "EodPushoverSent", "EodSkipped",
    "CrossLocationQuery", "InterLocationTransferProposed",
    # PR-Agent3-v0.1 2026-05-04
    "MultiLocationClosestLookup",
    # PR-Agent13-v0.1 — Compliance Calendar
    "ComplianceItem", "ComplianceItemsFile", "ComplianceLastSentFile",
    "ComplianceReminderAttempted", "ComplianceReminderSent",
    "ComplianceReminderFailed", "ComplianceReminderSkipped",
    "ComplianceReminderDeferred", "ComplianceItemMarkedDone",
    # PR-Agent22-v0.1 — P&L Anomaly Detective
    "PnlAnomalyConfig", "PnlAnomalyDetected", "PnlAnomalyDeclined",
    # PR-Agent19-v0.1 — Equipment & Maintenance scaffold
    "EquipmentMaintenanceConfig", "EquipmentIssueLogged",
    "EquipmentMaintenanceDeclined",
    "CateringLeadCreated", "CateringLeadStatusChange", "CateringLeadRejected", "CateringQuoteDrafted",
    "CateringOwnerApprovalRequested", "CateringOwnerDecision", "CateringQuoteSent",
    "CateringProposalsGenerated", "CateringProposalGenerationFailed",
    "CateringProposalSelected", "CateringProposalSelectionFailed",
    # v0.3 catering audit classes
    "CateringQuoteAttempted", "CateringOwnerApprovalCardAttempted",
    "CateringOwnerApprovalCardFailed", "CateringOwnerApprovalCardSkipped",
    "CateringOwnerEdited", "CateringDeclineAttempted",
    "CateringQuoteRenderFailed",
    # PR-D1
    "CateringQuoteSentLeadMissing", "ConfigLoadFailed",
    "CateringLeadManuallyReconciled",
    # PR-B v0.4
    "CateringQuoteSkillFailed",
    # PR-CF1
    "CateringSelectedItem", "CateringMenuFinalized",
    # F5b 2026-05-01
    "CateringCustomerAckSent", "CateringCustomerAckFailed",
    # F7 2026-05-01 (catering dispatcher watchdog)
    "CateringDispatcherWatchdogFired", "CateringDispatcherWatchdogSuppressed",
    # PR-CF5 2026-05-03 (state-file migration)
    "StateFileMigrated", "StateFileMigrationFailed", "StateFileMigrationOverridden",
    # PR-CF6 2026-05-03 (cf-router Hermes plugin; supersedes F8 + F9)
    "CfRouterIntercepted",
    "MenuUpdateProposed", "MenuUpdateApplied", "MenuUpdateRejected",
]
