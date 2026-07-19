"""F0-4 (§12b): apply-expense-decision emits expense_owner_reply_sent for the
four inline owner-reply nudges that previously fired with NO audit row:
reconcile notice, undo wrong-status, missing-decimals nudge, unrecognized
message. One row per site (reason discriminates).

Mirrors the apply-decision test fixtures (importlib load + module-attribute
injection) but stubs the _bridge_post chokepoint so no HTTP server is needed,
which lets it run in-process on Windows via an fcntl stub. All writes tmp-routed.
"""
from __future__ import annotations

import json
from pathlib import Path

from fixtures_fleet import ensure_fcntl_stub, load_script, read_log_rows, write_config

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "expense_bookkeeper" / "scripts" / "apply-expense-decision"

EXPENSE_BLOCK = {
    "enabled": True, "qbo_client_mode": "mock",
    "proposal_ttl_hours": 4, "receipt_retention_days": 30,
}


def _lead(expense_id, receipts_posix, *, status="AWAITING_OWNER_APPROVAL",
          code=None, reconcile_required=False, total_cents=23450):
    d = {
        "expense_id": expense_id,
        "original_message_id": f"msg-{expense_id}",
        "sender_phone": "+19045550123",
        "received_at": "2026-05-01T12:00:00+00:00",
        "image_path": f"{receipts_posix}/{expense_id}.jpg",
        "image_phash": "0" * 16,
        "image_byte_hash": "0" * 64,
        "status": status,
        "extracted_total_cents": total_cents,
        "reconcile_required": reconcile_required,
    }
    if code is not None:
        d["owner_approval_code"] = code
    return d


def _setup(tmp_path, monkeypatch, build_leads):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    receipts_posix = receipts.as_posix()
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", receipts_posix)
    write_config(tmp_path, expense_bookkeeper=EXPENSE_BLOCK)
    leads = build_leads(receipts_posix)
    leads_path = tmp_path / "leads.json"
    leads_path.write_text(json.dumps({"schema_version": 1, "leads": leads,
                                      "last_id": len(leads)}), encoding="utf-8")
    log = tmp_path / "decisions.log"

    mod = load_script("apply_expense_decision_reply_under_test", SCRIPT)
    mod.CONFIG_PATH = tmp_path / "config.yaml"
    mod.LEADS_PATH = leads_path
    mod.LEADS_LOCK = tmp_path / "leads.json.lock"
    mod.LOG_PATH = log
    mod.MOCK_QBO_STATE_PATH = tmp_path / "mock-qbo.json"
    monkeypatch.setattr(mod, "_bridge_post", lambda *a, **k: (True, "mid-x"))
    return mod, log


def _run(mod, monkeypatch, *, raw, sender_phone="+19045550100"):
    monkeypatch.setattr(mod.sys, "argv",
                        ["apply-expense-decision", "--raw-message", raw,
                         "--sender-phone", sender_phone])
    return mod.main()


def _reply_rows(log):
    return [r for r in read_log_rows(log) if r["type"] == "expense_owner_reply_sent"]


def test_reconcile_notice_audited(tmp_path, monkeypatch):
    mod, log = _setup(tmp_path, monkeypatch, lambda rp: [
        _lead("E0001", rp, code="#ABCDE", reconcile_required=True),
    ])
    _run(mod, monkeypatch, raw="#ABCDE 234.50")
    rows = _reply_rows(log)
    assert len(rows) == 1
    assert rows[0]["reason"] == "reconcile_notice"
    assert rows[0]["expense_id"] == "E0001"


def test_undo_wrong_status_audited(tmp_path, monkeypatch):
    mod, log = _setup(tmp_path, monkeypatch, lambda rp: [
        _lead("E0002", rp, status="AWAITING_OWNER_APPROVAL"),
    ])
    _run(mod, monkeypatch, raw="undo E0002")
    rows = _reply_rows(log)
    assert len(rows) == 1
    assert rows[0]["reason"] == "undo_wrong_status"
    assert rows[0]["expense_id"] == "E0002"


def test_missing_decimals_audited(tmp_path, monkeypatch):
    mod, log = _setup(tmp_path, monkeypatch, lambda rp: [
        _lead("E0003", rp, code="#BCDEF"),
    ])
    _run(mod, monkeypatch, raw="#BCDEF 234")
    rows = _reply_rows(log)
    assert len(rows) == 1
    assert rows[0]["reason"] == "missing_decimals"
    assert rows[0]["expense_id"] == "E0003"


def test_unrecognized_message_audited(tmp_path, monkeypatch):
    mod, log = _setup(tmp_path, monkeypatch, lambda rp: [])
    _run(mod, monkeypatch, raw="hello can you help")
    rows = _reply_rows(log)
    assert len(rows) == 1
    assert rows[0]["reason"] == "unrecognized"
    assert rows[0]["expense_id"] is None
