"""Structural assertions on expense_bookkeeper_dispatcher/SKILL.md.

Pins the routing contract the dispatcher documents: enabled-gate, owner-only,
image -> parse_receipt_photo, #code / undo -> handle_expense_owner_approval.
Content-integrity only (SKILL interpretation is not unit-tested behavior).
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "src" / "agents" / "expense_bookkeeper" / "skills" / "expense_bookkeeper_dispatcher" / "SKILL.md"


def test_enabled_gate_documented():
    text = SKILL.read_text(encoding="utf-8")
    assert "expense_bookkeeper.enabled" in text
    assert "Check enabled" in text
    assert "If `false`" in text


def test_owner_only():
    text = SKILL.read_text(encoding="utf-8")
    assert 'sender_role != "owner"' in text
    assert "re-check" in text.lower() or "re-verify" in text.lower()


def test_routes_image_to_parse_receipt_photo():
    text = SKILL.read_text(encoding="utf-8")
    assert "parse_receipt_photo" in text
    assert "Image inbound" in text


def test_routes_code_and_undo_to_owner_approval():
    text = SKILL.read_text(encoding="utf-8")
    assert "handle_expense_owner_approval" in text
    assert "#XXXXX" in text or "#[A-HJKMNPQR-Z2-9]{5}" in text
    assert "undo E####" in text
