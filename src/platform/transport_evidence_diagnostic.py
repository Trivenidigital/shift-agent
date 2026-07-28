"""Diagnostic run for the transport-budget evidence harness.

Feeds a deterministic 28-segment response plan through the EXISTING gateway
response-dispatch path (``_handle_message_with_agent -> _send_with_retry``) —
the SAME dispatcher / identity / retry / failure / persistence a real reply
uses. There is NO separate ``adapter.send`` / ``bridge_post`` loop.

The per-segment transport call is an INJECTABLE seam (``dispatch_segment``) so
the whole runner is exercised in-process with a pure fake; the gateway patch
binds it to a wrapper of ``_send_with_retry`` whose provider-entry point (a) writes
the durable provider-entry marker (correction #2) and (b) trips the provider-entry
canary that makes the denial proof non-vacuous.

State-machine ordering (each step durable + fsync, correction #2):

  1. ``mark_dispatch_pending``  (before the transport call)
  2. ``mark_provider_entry``    (the durable marker, written by ``on_provider_entry``
     IMMEDIATELY before the real provider submit — only when the budget admits)
  3. provider submit
  4. reject/fail before acceptance -> ``mark_dispatch_failed``
  5. acceptance / message id       -> ``mark_sent``
  6. budget denies at the gate      -> ``mark_denied`` (``on_provider_entry`` never
     fires; no marker; the provider is never entered)

Five crash-injection checkpoints (frozen spec §50) prove ZERO duplicate provider
submission on restart; there is NO auto-continuation — a fresh runner over a
started ledger sends nothing.

Repository implementation only — default-OFF, never executed on the box.
"""
from __future__ import annotations

import contextvars
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from transport_evidence_ledger import AttemptLedger, LedgerError

# ── send outcomes the transport seam reports ─────────────────────────────────
KIND_DENIED = "denied"
KIND_SENT = "sent"
KIND_DISPATCH_FAILED = "dispatch_failed"
KIND_DELIVERY_UNKNOWN = "delivery_unknown"
SEND_KINDS = frozenset({KIND_DENIED, KIND_SENT, KIND_DISPATCH_FAILED, KIND_DELIVERY_UNKNOWN})

# ── the five crash-injection checkpoints (frozen spec §50) ───────────────────
CP_BEFORE_PROVIDER_MARKER = "before_provider_marker"        # (1)
CP_AFTER_PROVIDER_MARKER = "after_provider_marker"          # (2) — fired by the seam
CP_AFTER_PROVIDER_ENTRY = "after_provider_entry"            # (3) — fired by the seam
CP_AFTER_ACK = "after_ack_before_sent_record"              # (4)
CP_AFTER_SENT = "after_sent"                                # (5)


class CrashInjected(BaseException):
    """A simulated hard process death at a checkpoint. Subclasses ``BaseException``
    so ordinary ``except Exception`` handlers in the runner never swallow it —
    it must propagate out of ``run`` exactly like a real crash."""


def build_segment_plan(n: int = 28) -> list[str]:
    """A deterministic n-segment plan, each segment short (well below any
    auto-split threshold) so one segment == one finalized send."""
    return [f"transport-evidence probe segment {i:02d} of {n}" for i in range(1, n + 1)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class RunSummary:
    run_id: str
    continued: bool
    halted: bool = False
    halt_reason: str = ""
    accepted: int = 0
    denied: int = 0
    dispatch_failed: int = 0
    delivery_unknown: int = 0
    provider_ids: list[str] = field(default_factory=list)


class DiagnosticRunner:
    """Drives the 28-segment plan through the transport seam, writing the durable
    per-attempt ledger in correction-#2 order.

    ``dispatch_segment(attempt, segment, on_provider_entry) -> (kind, provider_id)``
      * consults the REAL budget gate;
      * on denial returns ``(KIND_DENIED, None)`` WITHOUT calling
        ``on_provider_entry`` (the provider is never entered);
      * on admission calls ``on_provider_entry()`` (which writes the durable
        provider-entry marker) IMMEDIATELY before entering the provider, then
        returns ``(KIND_SENT, id)`` / ``(KIND_DISPATCH_FAILED, None)`` /
        ``(KIND_DELIVERY_UNKNOWN, None)``.
      * may raise ``CrashInjected`` (checkpoints 2/3 live inside the seam).

    ``checkpoints(name, attempt)`` is the shared crash hook (default no-op); the
    seam calls the SAME hook for checkpoints 2 and 3.
    """

    def __init__(
        self,
        *,
        run_id: str,
        plan: list[str],
        attempt_ledger: AttemptLedger,
        dispatch_segment: Callable[[int, str, Callable[[], None]], tuple[str, Optional[str]]],
        checkpoints: Optional[Callable[[str, int], None]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self.run_id = run_id
        self.plan = plan
        self.ledger = attempt_ledger
        self.dispatch_segment = dispatch_segment
        self._checkpoint = checkpoints or (lambda name, attempt: None)
        self._now = clock or _now_iso

    def run(self) -> RunSummary:
        # No auto-continuation: a ledger that already has ANY attempt row is a
        # crashed/completed run. A fresh process (new epoch) NEVER resumes — the
        # operator inspects the terminal state. Zero provider submissions here.
        if self.ledger.started():
            return RunSummary(self.run_id, continued=False, halt_reason="already_started")

        summary = RunSummary(self.run_id, continued=True)
        for i, segment in enumerate(self.plan, start=1):
            # (step 1) reserve the attempt — an audit-write failure HALTS.
            try:
                self.ledger.mark_dispatch_pending(i, ts=self._now())
            except LedgerError as e:
                summary.halted = True
                summary.halt_reason = f"audit_write_failed:dispatch_pending:{e}"
                break

            self._checkpoint(CP_BEFORE_PROVIDER_MARKER, i)  # crash point (1)

            entry_id = "pe_" + secrets.token_hex(6)

            def _on_provider_entry(_i=i, _entry_id=entry_id) -> None:
                # (step 2) durable provider-entry marker written IMMEDIATELY
                # before the real provider submit. A failure here raises out of
                # the seam BEFORE it enters the provider → HALT, no submission.
                self.ledger.mark_provider_entry(_i, _entry_id, ts=self._now())

            try:
                kind, provider_id = self.dispatch_segment(i, segment, _on_provider_entry)
            except LedgerError as e:
                # marker (or an in-seam audit) write failed → HALT, no further sends.
                summary.halted = True
                summary.halt_reason = f"audit_write_failed:provider_entry:{e}"
                break

            if kind not in SEND_KINDS:
                summary.halted = True
                summary.halt_reason = f"illegal_send_kind:{kind}"
                break

            try:
                if kind == KIND_DENIED:
                    self.ledger.mark_denied(i, ts=self._now())
                    summary.denied += 1
                elif kind == KIND_SENT:
                    self._checkpoint(CP_AFTER_ACK, i)  # crash point (4)
                    if not provider_id:
                        summary.halted = True
                        summary.halt_reason = "sent_without_provider_id"
                        break
                    self.ledger.mark_sent(i, provider_id, ts=self._now())
                    self._checkpoint(CP_AFTER_SENT, i)  # crash point (5)
                    summary.accepted += 1
                    summary.provider_ids.append(provider_id)
                elif kind == KIND_DISPATCH_FAILED:
                    self.ledger.mark_dispatch_failed(i, ts=self._now())
                    summary.dispatch_failed += 1
                elif kind == KIND_DELIVERY_UNKNOWN:
                    # Live ack-loss AFTER provider entry. Leave the marker in
                    # place (already durable) and do NOT write a terminal outcome
                    # — reconciliation derives delivery_unknown_after_send. HALT:
                    # evidence-capture uncertainty never continues sending.
                    summary.delivery_unknown += 1
                    summary.halted = True
                    summary.halt_reason = "delivery_unknown_after_send"
                    break
            except LedgerError as e:
                summary.halted = True
                summary.halt_reason = f"audit_write_failed:{kind}:{e}"
                break

        return summary


# ── real gateway path: async inline driver + provider-entry observer ─────────
#
# The REAL diagnostic runs INLINE on the gateway's turn task (each segment awaited
# via the real ``adapter._send_with_retry``) so the per-inbound-turn send-budget
# ContextVar set by ``safe_io.begin_inbound_turn_send_budget`` propagates to
# ``send()`` and the REAL cap=5 gate enforces. The durable provider-entry marker +
# canary fire INSIDE the patched ``send()``, IMMEDIATELY before the bridge POST
# ``/send`` (the provider boundary), via the ``_ACTIVE_ATTEMPT`` ContextVar — so a
# budget-denied send (which returns before the POST) never trips the canary
# (non-vacuous denial proof).


@dataclass
class ProviderEntryContext:
    attempt: int
    on_provider_entry: Callable[[], None]  # durable marker writer (idempotent)
    entered: int = 0  # provider-boundary canary: # of POSTs reached for this attempt


_ACTIVE_ATTEMPT: "contextvars.ContextVar[Optional[ProviderEntryContext]]" = (
    contextvars.ContextVar("shift_te_active_attempt", default=None)
)


def provider_entry_observer(chat_id: Any = None) -> None:
    """Called by the patched ``send()`` IMMEDIATELY before the bridge POST /send.

    If a diagnostic attempt is active (set by the async driver), write the durable
    provider-entry marker (once) and trip the provider-entry canary; else a plain
    no-op (default-OFF passthrough). The marker is written BEFORE the POST so a
    crash from here on reconciles to ``delivery_unknown_after_send``."""
    pec = _ACTIVE_ATTEMPT.get()
    if pec is None:
        return
    pec.on_provider_entry()  # durable marker (idempotent) — BEFORE the POST
    pec.entered += 1  # canary: reached the provider boundary


async def run_diagnostic_async(
    *,
    chat_id: str,
    run_id: str,
    attempt_ledger: AttemptLedger,
    send_with_retry: Callable[..., Any],
    begin_turn_budget: Optional[Callable[[], Any]] = None,
    plan: Optional[list[str]] = None,
    clock: Optional[Callable[[], str]] = None,
) -> RunSummary:
    """Drive the 28-segment plan through the REAL ``send_with_retry`` inline on the
    current task. Classifies each attempt by the provider-entry canary + the send
    result: canary 0 → ``denied`` (budget suppressed before the boundary); canary
    1 + success+id → ``sent``; canary 1 + clean failure → ``dispatch_failed``;
    canary 1 + raised-after-entry → ``delivery_unknown_after_send`` (HALT); canary
    >1 → unexpected extra provider op / auto-split (HALT). Audit-write failure →
    HALT. No auto-continuation."""
    now = clock or _now_iso
    plan = plan or build_segment_plan()
    if attempt_ledger.started():
        return RunSummary(run_id, continued=False, halt_reason="already_started")

    # Freeze the per-turn send budget for THIS diagnostic turn (the REAL primitive)
    # so the same cap=5 gate a real reply hits enforces here.
    if begin_turn_budget is not None:
        begin_turn_budget()

    summary = RunSummary(run_id, continued=True)
    for i, segment in enumerate(plan, start=1):
        try:
            attempt_ledger.mark_dispatch_pending(i, ts=now())
        except LedgerError as e:
            summary.halted = True
            summary.halt_reason = f"audit_write_failed:dispatch_pending:{e}"
            break

        entry_id = "pe_" + secrets.token_hex(6)
        marked = {"done": False}

        def _on_entry(_i=i, _eid=entry_id, _m=marked) -> None:
            if not _m["done"]:
                attempt_ledger.mark_provider_entry(_i, _eid, ts=now())
                _m["done"] = True

        pec = ProviderEntryContext(attempt=i, on_provider_entry=_on_entry)
        token = _ACTIVE_ATTEMPT.set(pec)
        result = None
        raised: Optional[BaseException] = None
        audit_failed = False
        try:
            result = await send_with_retry(chat_id, segment)
        except LedgerError as e:  # marker write failed inside the observer → HALT
            audit_failed = True
            raised = e
        except Exception as e:  # noqa: BLE001 — a send failure after entry is data
            raised = e
        finally:
            _ACTIVE_ATTEMPT.reset(token)

        if audit_failed:
            summary.halted = True
            summary.halt_reason = f"audit_write_failed:provider_entry:{raised}"
            break

        entered = pec.entered
        try:
            if entered == 0:
                # Never reached the provider boundary → budget denied at the gate.
                attempt_ledger.mark_denied(i, ts=now())
                summary.denied += 1
            elif entered == 1 and result is not None and getattr(result, "success", False) and getattr(result, "message_id", None):
                attempt_ledger.mark_sent(i, str(result.message_id), ts=now())
                summary.accepted += 1
                summary.provider_ids.append(str(result.message_id))
            elif entered == 1 and raised is not None:
                # Entered the provider once, then lost certainty → derived
                # delivery_unknown_after_send at restart. Leave the marker; HALT.
                summary.delivery_unknown += 1
                summary.halted = True
                summary.halt_reason = "delivery_unknown_after_send"
                break
            elif entered == 1:
                # Provider rejected before acceptance (clean failure result).
                attempt_ledger.mark_dispatch_failed(i, ts=now())
                summary.dispatch_failed += 1
            else:
                # entered > 1 → auto-split / extra provider op → fail the run.
                summary.halted = True
                summary.halt_reason = f"unexpected_provider_ops:{entered}"
                break
        except LedgerError as e:
            summary.halted = True
            summary.halt_reason = f"audit_write_failed:{e}"
            break

    return summary
