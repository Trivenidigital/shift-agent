"""PR-ζ 2026-05-26 — ActionExecutionContext + RegulatedSend audit-row invariants.

The chokepoint at safe_io.bridge_post depends on these invariants:
- `frozen=True` + `extra="forbid"` defends against caller-side mutation/drift
- required-field discipline matches existing Pydantic patterns elsewhere in
  the platform
- The two audit-row variants register against `LogEntry`'s discriminated union
  (verified via TypeAdapter round-trip).
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
sys.path.insert(0, str(PLATFORM_DIR))

# safe_io imports fcntl which is Linux-only; schemas itself doesn't, but the
# test conventions in this repo align on a single Windows-skip mark.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="platform schemas tests run on Linux (consistent with safe_io tests)",
)


class TestActionExecutionContext:
    def test_minimal_construction(self):
        from schemas import ActionExecutionContext
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
        )
        assert ctx.action_id == "flyer.billing.request_plan_change"
        assert ctx.is_regulated_action is True
        assert ctx.verified_action_result is False
        assert ctx.audit_row_id is None
        assert ctx.mutation_class is None

    def test_full_construction(self):
        from schemas import ActionExecutionContext
        ctx = ActionExecutionContext(
            action_id="flyer.billing.request_plan_change",
            is_regulated_action=True,
            verified_action_result=False,
            audit_row_id="evt_abc123",
            mutation_class="external_irreversible",
        )
        assert ctx.audit_row_id == "evt_abc123"
        assert ctx.mutation_class == "external_irreversible"

    def test_frozen(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        ctx = ActionExecutionContext(
            action_id="x", is_regulated_action=False, verified_action_result=False,
        )
        with pytest.raises((ValidationError, TypeError)):
            ctx.action_id = "y"  # type: ignore[misc]

    def test_extra_forbidden(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(  # type: ignore[call-arg]
                action_id="x", is_regulated_action=False, verified_action_result=False,
                unknown_field=True,
            )

    def test_missing_action_id_rejected(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(  # type: ignore[call-arg]
                is_regulated_action=True, verified_action_result=False,
            )

    def test_missing_is_regulated_action_rejected(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(  # type: ignore[call-arg]
                action_id="x", verified_action_result=False,
            )

    def test_missing_verified_action_result_rejected(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(  # type: ignore[call-arg]
                action_id="x", is_regulated_action=False,
            )

    def test_action_id_min_length(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(
                action_id="", is_regulated_action=False, verified_action_result=False,
            )

    def test_mutation_class_literal_enforcement(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(  # type: ignore[arg-type]
                action_id="x", is_regulated_action=False, verified_action_result=False,
                mutation_class="unknown_class",
            )

    def test_audit_row_id_max_length(self):
        from schemas import ActionExecutionContext
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ActionExecutionContext(
                action_id="x", is_regulated_action=False, verified_action_result=False,
                audit_row_id="x" * 201,
            )


class TestRegulatedSendLogEntryVariants:
    """Verify the two new LogEntry variants register cleanly against the
    discriminated union and can be constructed via TypeAdapter."""

    def test_missing_action_context_variant_round_trip(self):
        from datetime import datetime, timezone
        from pydantic import TypeAdapter
        from schemas import LogEntry
        adapter = TypeAdapter(LogEntry)
        ts = datetime.now(timezone.utc)
        entry = adapter.validate_python({
            "type": "regulated_send_missing_action_context",
            "ts": ts,
            "caller_script": "rogue.py",
            "jid": "16172223333@s.whatsapp.net",
            "message_preview": "Hello world",
        })
        assert entry.type == "regulated_send_missing_action_context"  # type: ignore[union-attr]
        assert entry.caller_script == "rogue.py"  # type: ignore[union-attr]

    def test_lint_violation_variant_round_trip(self):
        from datetime import datetime, timezone
        from pydantic import TypeAdapter
        from schemas import LogEntry
        adapter = TypeAdapter(LogEntry)
        ts = datetime.now(timezone.utc)
        entry = adapter.validate_python({
            "type": "regulated_send_lint_violation",
            "ts": ts,
            "action_id": "flyer.billing.request_plan_change",
            "audit_row_id": None,
            "jid": "16172223333@s.whatsapp.net",
            "verb_hits": ["upgraded", "confirmed"],
            "message_preview": "Your plan has been upgraded and confirmed.",
        })
        assert entry.type == "regulated_send_lint_violation"  # type: ignore[union-attr]
        assert entry.verb_hits == ["upgraded", "confirmed"]  # type: ignore[union-attr]

    def test_lint_violation_verb_hits_max_20(self):
        from datetime import datetime, timezone
        from pydantic import TypeAdapter, ValidationError
        from schemas import LogEntry
        adapter = TypeAdapter(LogEntry)
        ts = datetime.now(timezone.utc)
        # 21 entries — must fail validation; the chokepoint MUST cap to 20
        # before calling _emit_audit_row (verified separately in safe_io tests).
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "type": "regulated_send_lint_violation",
                "ts": ts,
                "action_id": "x",
                "audit_row_id": None,
                "jid": "x@s.whatsapp.net",
                "verb_hits": [f"verb_{i}" for i in range(21)],
                "message_preview": "x",
            })

    def test_missing_action_context_message_preview_max_120(self):
        from datetime import datetime, timezone
        from pydantic import TypeAdapter, ValidationError
        from schemas import LogEntry
        adapter = TypeAdapter(LogEntry)
        ts = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "type": "regulated_send_missing_action_context",
                "ts": ts,
                "caller_script": "x.py",
                "jid": "x@s.whatsapp.net",
                "message_preview": "x" * 121,
            })

    def test_extra_field_forbidden(self):
        from datetime import datetime, timezone
        from pydantic import TypeAdapter, ValidationError
        from schemas import LogEntry
        adapter = TypeAdapter(LogEntry)
        ts = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            adapter.validate_python({
                "type": "regulated_send_missing_action_context",
                "ts": ts,
                "caller_script": "x.py",
                "jid": "x@s.whatsapp.net",
                "message_preview": "x",
                "extra_field_not_allowed": True,
            })

    def test_known_log_entry_types_registers_both_variants(self):
        from schemas import _KNOWN_LOG_ENTRY_TYPES
        assert "regulated_send_missing_action_context" in _KNOWN_LOG_ENTRY_TYPES
        assert "regulated_send_lint_violation" in _KNOWN_LOG_ENTRY_TYPES
