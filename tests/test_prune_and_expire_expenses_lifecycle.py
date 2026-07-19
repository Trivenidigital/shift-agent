"""prune-and-expire-expenses lifecycle (Agent #21) — NEW coverage.

Exercises main() in-process (fcntl stub, tmp-routed paths) so it runs on Windows:
  - AWAITING_OWNER_APPROVAL past proposal_ttl_hours -> EXPIRED + status-change row
  - the §12b expiry alert fires once per auto-expired AWAITING lead (notify stub)
  - retention-candidate (PUSHED) receipt older than retention_days -> JPEG deleted
    + expense_receipt_pruned row (and NO owner alert — only pending reversals alert)
  - disabled agent -> silent no-op (no writes, no log rows)

Complements the existing subprocess suite (test_prune_and_expire_logic.py,
Linux-only) with an in-process path that can assert the monkeypatched §12b alert.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fixtures_fleet import (
    ensure_fcntl_stub, load_script, read_log_rows, write_config,
    expense_lead, write_expense_lead_store,
)

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "expense_bookkeeper" / "scripts" / "prune-and-expire-expenses.py"


def _block(*, enabled=True, ttl_hours=1, retention_days=7):
    return {"enabled": enabled, "qbo_client_mode": "mock",
            "proposal_ttl_hours": ttl_hours, "receipt_retention_days": retention_days}


def _setup(tmp_path, monkeypatch, leads, *, block):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    monkeypatch.setenv("EXPENSE_RECEIPTS_DIR", receipts.as_posix())
    write_config(tmp_path, expense_bookkeeper=block)
    write_expense_lead_store(tmp_path, leads)
    log = tmp_path / "decisions.log"

    mod = load_script("prune_expire_lifecycle_under_test", SCRIPT)
    monkeypatch.setattr(mod.sys, "argv", ["prune-and-expire-expenses"])
    mod.CONFIG_PATH = tmp_path / "config.yaml"
    mod.LEADS_PATH = tmp_path / "leads.json"
    mod.RECEIPTS_DIR = receipts
    mod.LOG_PATH = log

    alerts = []
    monkeypatch.setattr(mod, "notify_owner_with_fallback",
                        lambda title, msg, **k: alerts.append((title, msg)) or True)
    return mod, log, receipts, alerts


def _make_receipt(receipts: Path, name: str, *, age_days: float) -> str:
    p = receipts / name
    p.write_bytes(b"\xff\xd8\xff\xe0jpeg")
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(p, (ts, ts))
    return name


def test_stale_awaiting_expires_and_fires_expiry_alert(tmp_path, monkeypatch):
    receipts = tmp_path / "receipts"
    lead = expense_lead("E0001", "AWAITING_OWNER_APPROVAL", receipts_dir=receipts,
                        received_ago_hours=3, total_cents=1500, vendor="Costco")
    mod, log, receipts, alerts = _setup(tmp_path, monkeypatch, [lead], block=_block(ttl_hours=1))
    _make_receipt(receipts, "receipt.jpg", age_days=0)

    rc = mod.main()
    assert rc == 0
    changes = [r for r in read_log_rows(log)
               if r["type"] == "expense_lead_status_change" and r["expense_id"] == "E0001"]
    assert changes and changes[0]["to_status"] == "EXPIRED"
    # §12b: exactly one owner alert for the auto-expired pending lead.
    assert len(alerts) == 1
    assert "E0001" in alerts[0][1]


def test_retention_prune_deletes_receipt_without_owner_alert(tmp_path, monkeypatch):
    receipts = tmp_path / "receipts"
    lead = expense_lead("E0002", "PUSHED", receipts_dir=receipts,
                        image_name="old.jpg", total_cents=8800, vendor="Sysco")
    mod, log, receipts, alerts = _setup(tmp_path, monkeypatch, [lead], block=_block(retention_days=7))
    img = receipts / "old.jpg"
    _make_receipt(receipts, "old.jpg", age_days=30)

    rc = mod.main()
    assert rc == 0
    assert not img.exists()  # JPEG pruned
    pruned = [r for r in read_log_rows(log)
              if r["type"] == "expense_receipt_pruned" and r["expense_id"] == "E0002"]
    assert pruned and pruned[0]["vendor_normalized"] == "Sysco"
    # Retention prune of an already-terminal lead is NOT an owner-pending reversal.
    assert alerts == []


def test_disabled_is_silent_noop(tmp_path, monkeypatch):
    receipts = tmp_path / "receipts"
    lead = expense_lead("E0003", "AWAITING_OWNER_APPROVAL", receipts_dir=receipts,
                        received_ago_hours=99)
    mod, log, receipts, alerts = _setup(tmp_path, monkeypatch, [lead],
                                        block=_block(enabled=False))
    _make_receipt(receipts, "receipt.jpg", age_days=0)

    rc = mod.main()
    assert rc == 0
    assert read_log_rows(log) == []   # no status-change / prune rows
    assert alerts == []
