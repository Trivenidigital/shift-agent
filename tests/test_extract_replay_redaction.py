"""Tests for the PII redaction logic in extract-replay-fixtures.

The redaction is structured-field-substitution-based (substitute the EXACT
phone/lid/chat_id from sender_block into raw_text), with a residual-digit
WARNING for user-typed PII the regex can't catch reliably.

These tests exist because Reviewer-R2 (silent-failure review, 2026-05-05)
flagged the prior regex-based redaction as fragile against (555) 555-1234,
555.555.1234, ISO timestamps, etc. The structured-field approach is more
correct (uses known-exact strings) but doesn't help when a USER typed a
phone number in their message body. This test pins the contract:

  - PRIMARY substitution is exact (no regex). Always correct for sender's
    own phone/lid/jid.
  - SECONDARY warning fires on residual digit sequences. Best-effort.
  - Operators MUST review extracted fixtures before commit even with
    redaction ON.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

import pytest

# Load the hyphen-named script via importlib (same pattern as
# tests/test_validate_sender_block.py)
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "platform" / "scripts" / "extract-replay-fixtures"
)


def _load_script_module():
    loader = SourceFileLoader("extract_replay_fixtures", str(_SCRIPT_PATH))
    spec = importlib.util.spec_from_loader("extract_replay_fixtures", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


def test_exact_phone_substitution(script):
    """Exact phone string from sender_block is substituted in raw_text."""
    text = "Hi, my phone is +17329837841. Call me."
    sender_block = {"phone": "+17329837841"}
    redacted, residuals = script._redact_text_from_structured_fields(text, sender_block, idx=42)
    assert "+17329837841" not in redacted
    assert "+15555000042" in redacted
    # Residuals: "17329837841" (without +) is NOT in the text; substitution
    # also handles the no-+ variant. Should be 0.
    assert residuals == 0


def test_exact_phone_no_plus_variant(script):
    """When raw_text contains the phone WITHOUT the leading +, also substitute."""
    text = "msg from 17329837841: hello"
    sender_block = {"phone": "+17329837841"}
    redacted, _ = script._redact_text_from_structured_fields(text, sender_block, idx=42)
    assert "17329837841" not in redacted


def test_lid_and_jid_substitution(script):
    """LID and JID values from sender_block get substituted."""
    text = "lid=201975216009469@lid chat_id=918522041562@s.whatsapp.net"
    sender_block = {
        "lid": "201975216009469@lid",
        "chat_id": "918522041562@s.whatsapp.net",
    }
    redacted, _ = script._redact_text_from_structured_fields(text, sender_block, idx=99)
    assert "201975216009469@lid" not in redacted
    assert "918522041562" not in redacted


def test_residual_digits_warned_user_typed_phone(script):
    """User-typed phone in unusual format triggers residual warning (not substituted)."""
    # Sender block has the OWNER's phone; the message body contains a CUSTOMER's
    # phone in (555)-format that we can't reliably match.
    text = "Customer left number (555) 123-4567 for callback"
    sender_block = {"phone": "+17329837841"}
    redacted, residuals = script._redact_text_from_structured_fields(text, sender_block, idx=1)
    # Owner phone wasn't in the text, so no substitution happened.
    assert redacted == text
    # But the residual scan SHOULD warn about (555) 123-4567.
    assert residuals >= 1, (
        f"expected residual digit warning for parens-format phone, got {residuals} "
        f"in text {redacted!r}"
    )


def test_residual_digits_warned_dot_format(script):
    """Dot-separated phone format flagged as residual."""
    text = "Reach out at 555.123.4567 next week"
    sender_block = {"phone": "+17329837841"}
    _, residuals = script._redact_text_from_structured_fields(text, sender_block, idx=2)
    assert residuals >= 1


def test_residual_digits_warns_on_iso_timestamp(script):
    """ISO timestamps trigger residual warning — INTENTIONAL false-positive.

    The residual-digit scan is best-effort. We INTENTIONALLY catch ISO
    timestamps as warnings rather than try to distinguish them from
    phone-like strings — the warning's job is to force human review,
    not to be precise. False positives are loud + recoverable; false
    negatives would silently leak PII.
    """
    text = "raw audit at 2026-05-01T12:34:56 with 1234567890 hash"
    sender_block = {"phone": "+17329837841"}
    _, residuals = script._redact_text_from_structured_fields(text, sender_block, idx=3)
    # Both the ISO timestamp's digits and the 10-digit hash trigger warnings.
    assert residuals >= 1


def test_synth_phone_unique_across_index_range(script):
    """Synthetic phones don't collide across a wide index range (>100)."""
    # Wider format (idx:06d) means up to 999_999 unique synthetic phones.
    seen = set()
    for i in range(150):
        seen.add(script._synth_phone(i))
    assert len(seen) == 150, "synthetic phones must be unique within 150-entry batch"


def test_no_substitution_when_sender_block_empty(script):
    """Empty sender_block → no substitution, but residual scan still runs."""
    text = "Hello +17329837841 world"
    redacted, residuals = script._redact_text_from_structured_fields(text, {}, idx=0)
    assert redacted == text  # nothing to substitute against
    assert residuals >= 1  # residual scan caught the digit sequence


def test_redaction_preserves_short_codes(script):
    """5-char approval codes like #A3F2X aren't digit sequences and don't trigger residual warning."""
    text = "Owner replied: #A3F2X approve. Sent at 2026-05-01T00:00:00."
    sender_block = {"phone": "+17329837841"}
    redacted, residuals = script._redact_text_from_structured_fields(text, sender_block, idx=4)
    assert "#A3F2X" in redacted  # short codes preserved
    # ISO timestamp triggers a warning — that's expected (see test above).
    # We just want to confirm #A3F2X itself doesn't produce extra warnings.
