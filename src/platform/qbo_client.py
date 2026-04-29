"""QuickBooks Online client — Protocol + MockQBOClient + RealQBOClient stub.

Lives in src/platform/ because the Protocol is substrate (multiple agents may
consume), not agent-specific business logic.

v0.1: MockQBOClient is the only concrete implementation. RealQBOClient is a
stub that refuses to instantiate without a validated OAuth token (runtime
guard, not config-time). v0.2 wires the actual Intuit Developer SDK.

The mock honours the same Protocol so all guardrails + tests are real.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol, Literal, Optional, Dict
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

# We don't import ExpenseLead here to avoid circular dependency — typed in
# Protocol method signatures via TYPE_CHECKING.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from schemas import ExpenseLead


# ─────────────────────────────────────────────────────────────────
# Protocol surface
# ─────────────────────────────────────────────────────────────────

class QBOPushResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(min_length=1, max_length=64)
    amount_cents: int  # what QBO recorded; should match owner_confirmed_total_cents
    pushed_at: str  # ISO8601


QBOErrorClass = Literal[
    "token_expired", "rate_limit", "bad_account",
    "server", "network", "invalid_request",
]


class QBOPushError(Exception):
    """Base for QBO failures. error_class drives retry policy."""

    def __init__(self, error_class: QBOErrorClass, message_redacted: str) -> None:
        super().__init__(f"{error_class}: {message_redacted}")
        self.error_class = error_class
        self.message_redacted = message_redacted


RETRYABLE_ERROR_CLASSES: frozenset[str] = frozenset({"rate_limit", "server", "network"})


class QBOClient(Protocol):
    """v0.1 mock + v0.2 real share this Protocol. RealQBOClient.__init__ MUST
    refuse to instantiate without a validated OAuth token (runtime guard)."""

    def push_expense(self, lead: "ExpenseLead") -> QBOPushResult: ...
    def void_transaction(self, transaction_id: str) -> None: ...
    def health_check(self) -> bool: ...


# ─────────────────────────────────────────────────────────────────
# Error sanitiser — strips token / URL-query patterns before audit-write.
# Used by apply-expense-decision when serialising QBOPushError into
# ExpensePushFailed.error_message_redacted.
# ─────────────────────────────────────────────────────────────────

_TOKEN_PATTERNS = [
    # URL-encoded forms (key=value)
    re.compile(r"access_token=[^&\s\"']+", re.IGNORECASE),
    re.compile(r"refresh_token=[^&\s\"']+", re.IGNORECASE),
    re.compile(r"code=[A-Za-z0-9_\-\.]{16,}", re.IGNORECASE),
    re.compile(r"Authorization:\s*Bearer\s+[^\s\"']+", re.IGNORECASE),
    # B-H1 fix: JSON-bodied tokens — QBO error responses sometimes echo the
    # request payload, which may contain "access_token":"..." or
    # "refresh_token":"...". URL-pattern alone misses these.
    re.compile(r'"(access_token|refresh_token|id_token)"\s*:\s*"[^"]*"', re.IGNORECASE),
    # Bare JWT: 3 base64url segments separated by dots, leading "eyJ" header.
    # Catches Bearer tokens that leaked into log messages without the
    # "Bearer " prefix or "key=" wrapping.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    # URL query strings — strip wholesale; tokens often live inside
    re.compile(r"\?[^\s\"']*"),
]


def redact_qbo_error(err: QBOPushError, max_chars: int = 200) -> str:
    """Whitelist error_class + sanitise message. NEVER returns raw API response.

    Strips known token / OAuth / query-string patterns, then truncates to
    max_chars. Used by apply-expense-decision to populate
    ExpensePushFailed.error_message_redacted (max_length=200).
    """
    msg = err.message_redacted
    for pattern in _TOKEN_PATTERNS:
        msg = pattern.sub("<REDACTED>", msg)
    return f"[{err.error_class}] {msg[:max_chars]}"


# ─────────────────────────────────────────────────────────────────
# MockQBOClient — v0.1 default. Deterministic, parametrised failure injection.
# ─────────────────────────────────────────────────────────────────

class MockQBOClient:
    """Deterministic mock. fail_mode injects parametrised error_class.

    Tests parametrise over every QBOErrorClass via @pytest.mark.parametrize
    to exercise both branches of RETRYABLE_ERROR_CLASSES.

    Real-shape conformance pinned in docs/qbo-protocol-v1.md (TBD); mock
    returns transaction_id of the form MOCK-<expense_id>-<seq>, amount_cents
    matching owner_confirmed_total_cents, ISO8601 pushed_at in customer tz.
    """

    def __init__(
        self,
        timezone: str = "UTC",
        fail_mode: Optional[QBOErrorClass] = None,
        push_void_fail_mode: Optional[QBOErrorClass] = None,
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self.fail_mode = fail_mode
        # Allow void to fail independently from push (e.g. push succeeds, then
        # owner tries to undo and the void fails).
        self.push_void_fail_mode = push_void_fail_mode
        self._seq = 0
        self._pushed: Dict[str, QBOPushResult] = {}

    def push_expense(self, lead: "ExpenseLead") -> QBOPushResult:
        if self.fail_mode is not None:
            raise QBOPushError(
                self.fail_mode,
                f"mock-injected error during push: {self.fail_mode}",
            )
        if lead.owner_confirmed_total_cents is None:
            raise QBOPushError(
                "invalid_request",
                "owner_confirmed_total_cents is required for push",
            )
        self._seq += 1
        result = QBOPushResult(
            transaction_id=f"MOCK-{lead.expense_id}-{self._seq}",
            amount_cents=lead.owner_confirmed_total_cents,
            pushed_at=datetime.now(self._tz).isoformat(),
        )
        self._pushed[result.transaction_id] = result
        return result

    def void_transaction(self, transaction_id: str) -> None:
        if self.push_void_fail_mode is not None:
            raise QBOPushError(
                self.push_void_fail_mode,
                f"mock-injected error during void: {self.push_void_fail_mode}",
            )
        if transaction_id not in self._pushed:
            raise QBOPushError(
                "invalid_request",
                f"unknown transaction_id (was it pushed by this client?)",
            )
        del self._pushed[transaction_id]

    def health_check(self) -> bool:
        return self.fail_mode != "network" and self.push_void_fail_mode != "network"


# ─────────────────────────────────────────────────────────────────
# RealQBOClient — v0.2 stub. Refuses to instantiate without validated token.
# ─────────────────────────────────────────────────────────────────

class RealQBOClient:
    """Refuses to instantiate without validated OAuth token. v0.1 stub raises;
    v0.2 wires actual Intuit Developer SDK with token-refresh hygiene."""

    def __init__(self, token_path: str = "/opt/shift-agent/.qbo-tokens.json") -> None:
        raise NotImplementedError(
            "RealQBOClient is a v0.2 feature. v0.1 ships with MockQBOClient only. "
            "When QBO sandbox creds onboard, this constructor will validate the "
            "token at token_path before allowing any API calls."
        )

    # Placeholder Protocol methods — never reached because __init__ raises.
    def push_expense(self, lead: "ExpenseLead") -> QBOPushResult:
        raise NotImplementedError

    def void_transaction(self, transaction_id: str) -> None:
        raise NotImplementedError

    def health_check(self) -> bool:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────
# Factory — config-driven. v0.1 always Mock; v0.2 flips on cfg.qbo_client_mode
# after RealQBOClient.__init__ guard validates token.
# ─────────────────────────────────────────────────────────────────

def make_qbo_client(cfg, customer_timezone: str = "UTC") -> QBOClient:
    """cfg is ExpenseBookkeeperConfig (typed loosely to avoid circular import)."""
    if cfg.qbo_client_mode == "mock":
        return MockQBOClient(timezone=customer_timezone)
    return RealQBOClient()  # raises in v0.1
