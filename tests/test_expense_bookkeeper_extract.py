"""extract-receipt unit tests for Agent #21.

Covers two plan §4g edge cases the original v0.1 build deferred:
  #11 approval-code collision regenerate — _generate_unique_code retries
      on collision against the cross-state-file active-code pool
  #16 multi-receipt batch independence — 5 sequential receipts generate
      5 distinct codes (no silent collision, no shared state corruption)

Tests load extract-receipt via importlib + attribute injection — same
pattern as test_expense_bookkeeper_apply_decision.py (and parser).
Linux-only via pytestmark — fcntl is not on Windows.
"""
from __future__ import annotations

import os
os.environ.setdefault("EXPENSE_RECEIPTS_DIR", "/tmp/test/")

import importlib.util
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="extract-receipt imports fcntl-using safe_io",
)


EXTRACT_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "expense_bookkeeper" / "scripts"
    / "extract-receipt"
)


@pytest.fixture(scope="module")
def extract_mod():
    """Load extract-receipt as a module; suppress __main__ block.

    Uses SourceFileLoader explicitly because the script has no .py extension
    — Python 3.12 spec_from_file_location returns None for unrecognised
    suffixes, which is what blocked Linux test execution pre-fix (E2E
    Layer A finding 2026-05-01).
    """
    from importlib.machinery import SourceFileLoader
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
    loader = SourceFileLoader("extract_receipt_test", str(EXTRACT_SCRIPT))
    spec = importlib.util.spec_from_loader("extract_receipt_test", loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "extract_receipt_test"
    loader.exec_module(mod)
    return mod


def _empty_store(extract_mod):
    """Build an empty ExpenseLeadStore for code-generator input."""
    return extract_mod.ExpenseLeadStore(leads=[], last_id=0)


def _write_config(tmp_path):
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {"enabled": True, "qbo_client_mode": "mock"},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


# ───────────────────────────────────────────────────
# Edge case #11 — approval-code collision regenerate
# ───────────────────────────────────────────────────

def test_collision_regenerate_returns_non_colliding_code(extract_mod, monkeypatch):
    """When _generate_unique_code's first candidate collides with an active
    code in the pool, the retry loop must return a DIFFERENT non-colliding
    code rather than returning the colliding one or raising prematurely.

    Mocks secrets.choice with a deterministic char sequence: first 5 chars
    produce a collision; next 5 chars produce a unique code. Mocks
    _collect_active_codes to return a known seeded pool."""
    seeded_active = {"#AAAAA", "#BBBBB", "#CCCCC"}
    monkeypatch.setattr(extract_mod, "_collect_active_codes", lambda store: seeded_active)

    # secrets.choice will be called in groups of 5 per candidate.
    # Round 1 → "#AAAAA" (collides with seeded), Round 2 → "#XYZ23" (unique).
    sequence = iter("AAAAA" + "XYZ23")
    monkeypatch.setattr(extract_mod.secrets, "choice", lambda alpha: next(sequence))

    result = extract_mod._generate_unique_code(_empty_store(extract_mod))
    assert result == "#XYZ23"
    assert result not in seeded_active


def test_collision_regenerate_raises_after_100_consecutive_collisions(extract_mod, monkeypatch):
    """If 100 consecutive candidates all collide (pathological / pool
    nearly full), the function must raise rather than loop forever."""
    # Force every candidate to be the same code; seed pool contains it.
    monkeypatch.setattr(
        extract_mod, "_collect_active_codes", lambda store: {"#AAAAA"}
    )
    monkeypatch.setattr(extract_mod.secrets, "choice", lambda alpha: "A")

    with pytest.raises(RuntimeError, match="could not generate unique"):
        extract_mod._generate_unique_code(_empty_store(extract_mod))


# ───────────────────────────────────────────────────
# Edge case #16 — multi-receipt batch independence
# ───────────────────────────────────────────────────

def test_multi_receipt_batch_generates_distinct_codes(extract_mod, monkeypatch):
    """5 sequential receipts (the rapid-fire batch case in plan §4g #16)
    must produce 5 distinct approval codes — including under deterministic
    intra-batch collision pressure.

    Reviewer-d MED + reviewer-f 6th-lens: the original implementation only
    asserted set-math under real-entropy `secrets.choice`. This version
    forces a deterministic collision on the 3rd iteration (its first
    candidate matches the 1st iteration's code) so the assertion actually
    exercises the retry-on-existing-code path WITHIN a batch — proving the
    cross-state-file scan sees prior batch leads and the retry loop fires.
    Without this, the test would pass even if `_generate_unique_code`
    silently dropped its store-collision check."""
    # Force the cross-state-file pool to reflect ONLY our growing store
    # (avoid leaking real catering/menu/pending state files into the test).
    monkeypatch.setattr(
        extract_mod, "_collect_active_codes",
        lambda s: {l.owner_approval_code for l in s.leads
                   if l.owner_approval_code},
    )

    # Deterministic char sequence: 5 chars per candidate, 5 candidates total.
    # Round 3 first-candidate is forced to equal round 1's code (collision);
    # round 3's second candidate is unique. Rounds 1, 2, 4, 5 are each unique.
    chars = (
        "AAAAA"        # round 1 → #AAAAA
        "BBBBB"        # round 2 → #BBBBB
        "AAAAA"        # round 3 first try → COLLIDES with round 1
        "CCCCC"        # round 3 retry → #CCCCC
        "DDDDD"        # round 4 → #DDDDD
        "EEEEE"        # round 5 → #EEEEE
    )
    char_iter = iter(chars)
    monkeypatch.setattr(extract_mod.secrets, "choice",
                        lambda alpha: next(char_iter))

    store = _empty_store(extract_mod)
    codes: list[str] = []
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", "/tmp/test/")
    for i in range(1, 6):
        code = extract_mod._generate_unique_code(store)
        # Mint a fake AWAITING lead with the generated code so the next
        # iteration sees it in the active pool (mirrors what extract-receipt's
        # main() does when it appends the lead to store).
        lead = extract_mod.ExpenseLead(
            expense_id=f"E000{i}",
            original_message_id=f"wa_msg_batch_{i}",
            sender_phone="+19045550100",
            received_at="2026-04-30T12:00:00+00:00",
            image_path="/tmp/test/" + f"E000{i}.jpg",
            image_phash="a" * 16,
            image_byte_hash="b" * 64,
            owner_approval_code=code,
            status="AWAITING_OWNER_APPROVAL",
        )
        store.leads.append(lead)
        codes.append(code)

    # 5 distinct codes (round-3 retry succeeded with a non-colliding candidate)
    assert codes == ["#AAAAA", "#BBBBB", "#CCCCC", "#DDDDD", "#EEEEE"], (
        f"expected explicit code sequence proving round-3 retried past "
        f"the AAAAA collision; got: {codes}"
    )
    assert len(set(codes)) == 5


def test_orphan_flags_persist_before_vision_error_early_return(extract_mod, monkeypatch, tmp_path):
    state = tmp_path / "state" / "expense-bookkeeper"
    receipts = state / "receipts"
    logs = tmp_path / "logs"
    receipts.mkdir(parents=True)
    logs.mkdir()
    image = tmp_path / "receipt.jpg"
    # Reach the injected vision failure through real image validation.
    # Valid 32x32 JPEG, so this fixture works with optional Pillow absent.
    image.write_bytes(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912"
        "130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffdb0043010909"
        "090c0b0c180d0d1832211c21323232323232323232323232323232323232323232323232323232323232323232323232"
        "3232323232323232323232323232ffc00011080020002003012200021101031101ffc4001f0000010501010101010100"
        "000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203000411051221"
        "31410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a"
        "434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a9293949596979899"
        "9aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1"
        "f2f3f4f5f6f7f8f9faffc4001f0100030101010101010101010000000000000102030405060708090a0bffc400b51100"
        "020102040403040705040400010277000102031104052131061241510761711322328108144291a1b1c109233352f015"
        "6272d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a63646566676869"
        "6a737475767778797a82838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4"
        "c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9faffda000c03010002110311003f00f7"
        "fa28a2800a28a2800a28a2800a28a2803fffd9"
    ))

    extract_mod.CONFIG_PATH = _write_config(tmp_path)
    extract_mod.LEADS_PATH = state / "leads.json"
    extract_mod.LOG_PATH = logs / "decisions.log"
    extract_mod.RECEIPTS_DIR = receipts
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", str(receipts) + "/")
    monkeypatch.setattr(extract_mod, "_call_vision", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("vision down")))

    old_approved = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    extract_mod.LEADS_PATH.write_text(json.dumps({
        "schema_version": 1,
        "last_id": 1,
        "leads": [{
            "expense_id": "E0001",
            "original_message_id": "wa_old",
            "sender_phone": "+19045550100",
            "received_at": "2026-04-29T12:00:00+00:00",
            "image_path": str(receipts / "E0001.jpg"),
            "image_phash": "a" * 16,
            "image_byte_hash": "b" * 64,
            "owner_approval_code": "#A47C2",
            "extracted_total_cents": 23450,
            "owner_confirmed_total_cents": 23450,
            "owner_approval_received_at": old_approved,
            "status": "APPROVED_PENDING_PUSH",
        }],
    }), encoding="utf-8")

    sys.argv = [
        str(EXTRACT_SCRIPT),
        "--image-path", str(image),
        "--source-image-id", "wa_new",
        "--owner-phone", "+19045550100",
    ]
    rc = extract_mod.main()

    assert rc == extract_mod.EXIT_DEPENDENCY_DOWN
    data = json.loads(extract_mod.LEADS_PATH.read_text(encoding="utf-8"))
    assert data["leads"][0]["reconcile_required"] is True
    orphan_count = sum(
        1 for line in extract_mod.LOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["type"] == "expense_orphan_detected"
    )
    assert orphan_count == 1

    copied = receipts / "E0002.jpg"
    if copied.exists():
        copied.unlink()
    rc = extract_mod.main()
    assert rc == extract_mod.EXIT_DEPENDENCY_DOWN
    orphan_count_after = sum(
        1 for line in extract_mod.LOG_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["type"] == "expense_orphan_detected"
    )
    assert orphan_count_after == orphan_count
