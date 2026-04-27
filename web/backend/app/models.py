"""API I/O models — request bodies, response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ─── Auth ──────────────────────────────────────────────────────────────


class OtpRequestResponse(BaseModel):
    token: str
    expires_in_seconds: int


class OtpVerifyBody(BaseModel):
    token: str
    code: str


class MeResponse(BaseModel):
    owner_phone: str
    owner_name: str
    issued_at: int
    expires_at: int


# ─── Health / Dashboard ────────────────────────────────────────────────


class PublicHealth(BaseModel):
    ok: bool


class ComponentStatus(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class DashboardResponse(BaseModel):
    components: list[ComponentStatus]
    send_counter: dict[str, Any] | None
    counter_resets_at: str | None  # ISO local time at midnight customer-tz
    disabled: bool
    pending_active_count: int
    last_decisions: list[dict[str, Any]]


# ─── Roster ────────────────────────────────────────────────────────────


class EmployeeIn(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=200)
    nickname: str | None = Field(default=None, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=4, max_length=40)
    languages: list[str] = Field(default_factory=lambda: ["en"], max_length=10)
    can_cover_roles: list[str] = Field(max_length=20)
    status: Literal["active", "inactive", "terminated"] = "active"


class EmployeePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    nickname: str | None = Field(default=None, max_length=80)
    role: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, min_length=4, max_length=40)
    languages: list[str] | None = Field(default=None, max_length=10)
    can_cover_roles: list[str] | None = Field(default=None, max_length=20)
    status: Literal["active", "inactive", "terminated"] | None = None


# ─── Schedule ──────────────────────────────────────────────────────────


class ScheduleEntryIn(BaseModel):
    employee_id: str
    shift: str
    role: str


class ScheduleDayPut(BaseModel):
    entries: list[ScheduleEntryIn]


# ─── Pending ───────────────────────────────────────────────────────────


class CancelBody(BaseModel):
    reason: str = Field(min_length=5, max_length=200)


# All 11 proposal status literals — must match schemas.ProposalStatus.
# Using Literal here (instead of plain str) makes FastAPI emit `enum: [...]`
# in OpenAPI; openapi-typescript renders this as a TS string-literal union
# enabling exhaustive narrowing in the frontend.
ProposalStatus = Literal[
    "awaiting_owner_approval",
    "approved",
    "reconciling",
    "sent",
    "send_failed",
    "accepted",
    "declined",
    "denied_by_owner",
    "expired",
    "cancelled",
    "no_response_timeout",
]


class ProposalView(BaseModel):
    proposal_id: str
    code: str
    status: ProposalStatus
    absent_employee_id: str
    candidate_employee_id: str | None = None
    absent_date: str
    absent_shift: str
    absent_role: str
    absent_reason: str
    created_ts: str
    last_updated_ts: str
    outbound_message_id: str | None = None


# ─── Config ────────────────────────────────────────────────────────────


class ConfigPatch(BaseModel):
    """Flat dotted-path patch.

    e.g. {"limits.max_outbound_per_day": 6, "owner.phone": "+1..."}.
    Sensitive paths require fresh OTP.
    """

    fields: dict[str, Any] = Field(max_length=20)  # bound to prevent payload-amplification DoS


# ─── Safety ────────────────────────────────────────────────────────────


class SafetyToggleBody(BaseModel):
    reason: str = Field(min_length=5, max_length=200)


# ─── WhatsApp ──────────────────────────────────────────────────────────


class WhatsAppStatus(BaseModel):
    paired: bool
    me_id: str | None = None
    self_chat_jid: str | None = None
    bridge_uptime_seconds: float | None = None
    bridge_status: str | None = None
    last_seen_at: str | None = None


class PairSessionResponse(BaseModel):
    session_id: str
    expires_at: str


# ─── Disclosures ───────────────────────────────────────────────────────


class DisclosureSign(BaseModel):
    disclosure_id: Literal["baileys_tos", "audit_immutability", "employee_notification"]
    signed_by_name: str = Field(min_length=1, max_length=200)
    signed_at: str | None = None  # set server-side


# ─── Decisions ─────────────────────────────────────────────────────────


class DecisionEntry(BaseModel):
    ts: str
    type: str
    proposal_id: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
