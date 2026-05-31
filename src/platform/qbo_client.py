"""QuickBooks Online client — Protocol + MockQBOClient + RealQBOClient stub.

Lives in src/platform/ because the Protocol is substrate (multiple agents may
consume), not agent-specific business logic.

v0.1: MockQBOClient is the only concrete implementation. RealQBOClient is a
stub that refuses to instantiate without a validated OAuth token (runtime
guard, not config-time). v0.2 wires the actual Intuit Developer SDK.

The mock honours the same Protocol so all guardrails + tests are real.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
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
    return f"[{err.error_class}] {msg}"[:max_chars]


# ─────────────────────────────────────────────────────────────────
# MockQBOClient — v0.1 default. Deterministic, parametrised failure injection.
# ─────────────────────────────────────────────────────────────────

_MOCK_STATE_SCHEMA_VERSION = 1


class MockQBOClient:
    """Deterministic mock. fail_mode injects parametrised error_class.

    Tests parametrise over every QBOErrorClass via @pytest.mark.parametrize
    to exercise both branches of RETRYABLE_ERROR_CLASSES.

    Real-shape conformance pinned in docs/qbo-protocol-v1.md (TBD); mock
    returns transaction_id of the form MOCK-<expense_id>-<seq>, amount_cents
    matching owner_confirmed_total_cents, ISO8601 pushed_at in customer tz.

    state_path (optional): when set, persists pushed-transaction state +
    seq counter to a JSON file via safe_io.atomic_write_json. Required for
    cross-process undo (E2E Layer B finding 2026-05-01: each
    apply-expense-decision invocation creates a fresh MockQBOClient; without
    persistence, void_transaction always fails because the in-memory _pushed
    dict is empty in process 2). When state_path is None, behaviour matches
    the pre-fix in-memory mock (used by unit tests that exercise push+void
    in the same process).
    """

    def __init__(
        self,
        timezone: str = "UTC",
        fail_mode: Optional[QBOErrorClass] = None,
        push_void_fail_mode: Optional[QBOErrorClass] = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self.fail_mode = fail_mode
        # Allow void to fail independently from push (e.g. push succeeds, then
        # owner tries to undo and the void fails).
        self.push_void_fail_mode = push_void_fail_mode
        self._state_path: Optional[Path] = Path(state_path) if state_path else None
        self._seq, self._pushed = self._load_state()

    def _load_state(self) -> tuple[int, Dict[str, QBOPushResult]]:
        """Read persisted state from JSON file. Returns (seq, transactions).
        Empty defaults if state_path is None or the file doesn't exist yet.

        Raises loudly (not silently quarantine like safe_io.safe_load_json)
        on corruption — divergent from precedent on purpose: the mock state
        file is mock-only and shouldn't ever be operator-edited; if it's
        corrupted, an operator misconfigured something and a loud failure
        surfaces it immediately instead of silently re-minting transactions.
        """
        if self._state_path is None or not self._state_path.exists():
            return 0, {}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            # Wrap with context — raw decoder error doesn't include the path.
            raise json.JSONDecodeError(
                f"corrupted mock-qbo state at {self._state_path}: {e.msg}",
                e.doc,
                e.pos,
            ) from e
        if raw.get("schema_version") != _MOCK_STATE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported mock-qbo state schema_version="
                f"{raw.get('schema_version')!r} at {self._state_path}; "
                f"expected {_MOCK_STATE_SCHEMA_VERSION}"
            )
        seq = int(raw.get("seq", 0))
        transactions = {
            tid: QBOPushResult(**row)
            for tid, row in raw.get("transactions", {}).items()
        }
        return seq, transactions

    def _save_state(self) -> None:
        """Atomic write with fsync — mirrors `safe_io.atomic_write_text`
        semantics inline because safe_io has a module-level `import fcntl`
        that breaks Windows test imports (and `atomic_write_text` itself
        doesn't actually use fcntl). Portable: skips the parent-dir fsync
        on Windows where `os.open(dir, O_RDONLY)` raises PermissionError.

        No flock — mock is single-tenant per VPS; concurrent push during
        void = last-writer-wins. RealQBOClient (v0.2) MUST add proper
        locking when it lands; this divergence is mock-only.

        No-op when state_path is None (in-memory mode for unit tests).
        """
        if self._state_path is None:
            return
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _MOCK_STATE_SCHEMA_VERSION,
            "seq": self._seq,
            "transactions": {
                tid: r.model_dump(mode="json")
                for tid, r in self._pushed.items()
            },
        }
        content = json.dumps(payload, indent=2).encode("utf-8")
        tmp = path.with_name(
            path.name + f".tmp-{os.getpid()}-{int(time.time() * 1000)}"
        )
        # Write + fsync the data file. Cleanup tmp on partial-write failure
        # so a stale .tmp-* sibling doesn't accumulate.
        wrote = False
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, content)
                os.fsync(fd)
                wrote = True
            finally:
                os.close(fd)
        finally:
            if not wrote:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        os.replace(str(tmp), str(path))
        # Fsync the parent dir so the rename entry is durable. POSIX-only;
        # NTFS rename durability is implementation-defined and the standard
        # `os.open(dir, O_RDONLY)` path raises PermissionError on Windows.
        if os.name == "posix":
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

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
        # Re-load only when persisting — otherwise we'd reset in-memory seq
        # to 0 between calls on the same instance (breaks unit-test
        # contract that two consecutive pushes mint distinct sequence
        # numbers).
        if self._state_path is not None:
            self._seq, self._pushed = self._load_state()
        self._seq += 1
        result = QBOPushResult(
            transaction_id=f"MOCK-{lead.expense_id}-{self._seq}",
            amount_cents=lead.owner_confirmed_total_cents,
            pushed_at=datetime.now(self._tz).isoformat(),
        )
        self._pushed[result.transaction_id] = result
        self._save_state()
        return result

    def void_transaction(self, transaction_id: str) -> None:
        if self.push_void_fail_mode is not None:
            raise QBOPushError(
                self.push_void_fail_mode,
                f"mock-injected error during void: {self.push_void_fail_mode}",
            )
        # Re-load only when persisting — see push_expense for rationale.
        if self._state_path is not None:
            self._seq, self._pushed = self._load_state()
        if transaction_id not in self._pushed:
            raise QBOPushError(
                "invalid_request",
                f"unknown transaction_id (already voided or never pushed)",
            )
        del self._pushed[transaction_id]
        self._save_state()

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

def make_qbo_client(
    cfg,
    customer_timezone: str = "UTC",
    state_path: Optional[Path] = None,
) -> QBOClient:
    """cfg is ExpenseBookkeeperConfig (typed loosely to avoid circular import).

    state_path (optional): forwarded to MockQBOClient for cross-process
    persistence. Production scripts pass it (mirrors LEADS_PATH pattern);
    unit tests omit it for in-memory behaviour. Has no effect when
    cfg.qbo_client_mode == "real" since RealQBOClient owns its own
    server-side ledger.
    """
    if cfg.qbo_client_mode == "mock":
        return MockQBOClient(timezone=customer_timezone, state_path=state_path)
    raise QBOPushError(
        "invalid_request",
        "RealQBOClient is not enabled in v0.1; configure qbo_client_mode=mock",
    )
