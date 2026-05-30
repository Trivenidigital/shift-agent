"""prune-and-expire-expenses core loop (Agent #21) — expiration + pruning logic.

Covers the prune/expire loop body (script lines ~88-134) that the existing
test_prune_and_expire_dry_run.py explicitly does NOT exercise ("scope is
config-load-only"):
  - AWAITING_OWNER_APPROVAL leads older than proposal_ttl_hours -> EXPIRED
    (+ expense_lead_status_change audit row).
  - retention-candidate (PUSHED/REVERSED/REJECTED/EXPIRED) leads whose receipt
    JPEG is older than receipt_retention_days -> JPEG deleted (+ vendor/total
    preserved in an expense_receipt_pruned audit row).
  - boundary, non-candidate-status, idempotency, and stdout-count contract.

Linux-only: the subprocess imports safe_io (fcntl), mirroring
test_prune_and_expire_dry_run.py. The script's path constants are hardcoded, so
we override them in an importlib wrapper exactly as that test does. Receipt
image_path passes the ExpenseLead validator via EXPENSE_RECEIPTS_DIR.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="prune script depends on safe_io which imports fcntl",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
PRUNE_SCRIPT = _REPO_ROOT / "src" / "agents" / "expense_bookkeeper" / "scripts" / "prune-and-expire-expenses.py"
PLATFORM_DIR = _REPO_ROOT / "src" / "platform"


def _make_config(tmp_path: Path, *, enabled: bool = True, ttl_hours: int = 1, retention_days: int = 7) -> None:
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "L1", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100", "self_chat_jid": ""},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "expense_bookkeeper": {
            "enabled": enabled,
            "qbo_client_mode": "mock",
            "proposal_ttl_hours": ttl_hours,
            "receipt_retention_days": retention_days,
        },
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _iso_ago(*, hours: float = 0, days: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours, days=days)).isoformat()


def _lead(expense_id, status, *, received_ago_hours=0.0, receipts_dir, image_name, total=None, vendor=None):
    d = {
        "expense_id": expense_id,
        "original_message_id": f"msg-{expense_id}",
        "sender_phone": "+19045550123",
        "received_at": _iso_ago(hours=received_ago_hours),
        "image_path": f"{receipts_dir}/{image_name}",
        "image_phash": "0" * 16,
        "image_byte_hash": "0" * 64,
        "status": status,
    }
    if total is not None:
        d["extracted_total_cents"] = total
    if vendor is not None:
        d["extraction"] = {"vendor_normalized": vendor}
    return d


def _write_leads(tmp_path: Path, leads: list[dict]) -> None:
    store = {"schema_version": 1, "leads": leads, "last_id": len(leads)}
    (tmp_path / "leads.json").write_text(json.dumps(store), encoding="utf-8")


def _make_receipt(receipts_dir: Path, name: str, *, age_days: float) -> Path:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    p = receipts_dir / name
    p.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(p, (ts, ts))
    return p


def _run(tmp_path: Path):
    receipts_dir = tmp_path / "receipts"
    wrapper = f"""
import sys, pathlib
import importlib.util, importlib.machinery
sys.argv = ["prune-and-expire-expenses"]
loader = importlib.machinery.SourceFileLoader("prune", {str(PRUNE_SCRIPT)!r})
spec = importlib.util.spec_from_file_location("prune", {str(PRUNE_SCRIPT)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(pathlib.Path({str(PLATFORM_DIR)!r})))
spec.loader.exec_module(mod)
mod.CONFIG_PATH = pathlib.Path({str(tmp_path / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(tmp_path / 'leads.json')!r})
mod.RECEIPTS_DIR = pathlib.Path({str(receipts_dir)!r})
mod.LOG_PATH = pathlib.Path({str(tmp_path / 'decisions.log')!r})
sys.exit(mod.main())
"""
    env = dict(os.environ)
    env["EXPENSE_RECEIPTS_DIR"] = str(receipts_dir)
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15, env=env,
    )


def _read_leads(tmp_path: Path) -> dict[str, str]:
    store = json.loads((tmp_path / "leads.json").read_text(encoding="utf-8"))
    return {lead["expense_id"]: lead["status"] for lead in store["leads"]}


def _audit_rows(tmp_path: Path) -> list[dict]:
    log = tmp_path / "decisions.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _counts(result) -> dict:
    return json.loads(result.stdout.strip().splitlines()[-1])


# ── expiration ──────────────────────────────────────────────────────────────

def test_stale_awaiting_lead_expires(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, ttl_hours=1)
    _write_leads(tmp_path, [_lead("E0001", "AWAITING_OWNER_APPROVAL", received_ago_hours=2, receipts_dir=rec, image_name="e1.jpg")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    assert _read_leads(tmp_path)["E0001"] == "EXPIRED"
    assert _counts(r)["expired"] == 1
    changes = [a for a in _audit_rows(tmp_path) if a["type"] == "expense_lead_status_change"]
    assert any(c["expense_id"] == "E0001" and c["to_status"] == "EXPIRED" for c in changes)


def test_fresh_awaiting_lead_not_expired(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, ttl_hours=72)
    _write_leads(tmp_path, [_lead("E0002", "AWAITING_OWNER_APPROVAL", received_ago_hours=1, receipts_dir=rec, image_name="e2.jpg")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    assert _read_leads(tmp_path)["E0002"] == "AWAITING_OWNER_APPROVAL"
    assert _counts(r)["expired"] == 0


def test_non_awaiting_status_never_expired(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, ttl_hours=1)
    # APPROVED_PENDING_PUSH is old but not AWAITING -> must NOT be force-expired.
    _write_leads(tmp_path, [_lead("E0003", "APPROVED_PENDING_PUSH", received_ago_hours=999, receipts_dir=rec, image_name="e3.jpg")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    assert _read_leads(tmp_path)["E0003"] == "APPROVED_PENDING_PUSH"
    assert _counts(r)["expired"] == 0


# ── pruning ─────────────────────────────────────────────────────────────────

def test_terminal_lead_old_receipt_pruned(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, retention_days=7)
    img = _make_receipt(rec, "e4.jpg", age_days=10)
    _write_leads(tmp_path, [_lead("E0004", "PUSHED", received_ago_hours=0, receipts_dir=rec, image_name="e4.jpg", total=1599, vendor="costco")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    assert not img.exists(), "old receipt JPEG for a PUSHED lead should be deleted"
    assert _counts(r)["pruned"] == 1
    pruned = [a for a in _audit_rows(tmp_path) if a["type"] == "expense_receipt_pruned"]
    assert pruned and pruned[0]["expense_id"] == "E0004"
    # metadata preserved in the audit row
    assert pruned[0]["extracted_total_cents"] == 1599
    assert pruned[0]["vendor_normalized"] == "costco"


def test_terminal_lead_fresh_receipt_kept(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, retention_days=7)
    img = _make_receipt(rec, "e5.jpg", age_days=1)
    _write_leads(tmp_path, [_lead("E0005", "REJECTED", received_ago_hours=0, receipts_dir=rec, image_name="e5.jpg")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    assert img.exists(), "fresh receipt JPEG must be kept"
    assert _counts(r)["pruned"] == 0


def test_non_retention_status_receipt_not_pruned(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, retention_days=7)
    # EXTRACTING is not a retention candidate -> keep its (old) receipt.
    img = _make_receipt(rec, "e6.jpg", age_days=99)
    _write_leads(tmp_path, [_lead("E0006", "EXTRACTING", received_ago_hours=0, receipts_dir=rec, image_name="e6.jpg")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    assert img.exists(), "non-retention-candidate receipt must not be pruned"
    assert _counts(r)["pruned"] == 0


# ── idempotency + contracts ──────────────────────────────────────────────────

def test_idempotent_second_run_is_noop(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, ttl_hours=1, retention_days=7)
    _make_receipt(rec, "e7.jpg", age_days=10)
    _write_leads(tmp_path, [
        _lead("E0007", "AWAITING_OWNER_APPROVAL", received_ago_hours=5, receipts_dir=rec, image_name="e7b.jpg"),
        _lead("E0008", "PUSHED", received_ago_hours=0, receipts_dir=rec, image_name="e7.jpg"),
    ])
    first = _run(tmp_path)
    assert first.returncode == 0, (first.stderr, first.stdout)
    assert _counts(first)["expired"] == 1
    assert _counts(first)["pruned"] == 1
    second = _run(tmp_path)
    assert second.returncode == 0, (second.stderr, second.stdout)
    assert _counts(second) == {"expired": 0, "pruned": 0}


def test_disabled_is_silent_noop(tmp_path):
    rec = tmp_path / "receipts"
    _make_config(tmp_path, enabled=False, ttl_hours=1)
    _write_leads(tmp_path, [_lead("E0009", "AWAITING_OWNER_APPROVAL", received_ago_hours=999, receipts_dir=rec, image_name="e9.jpg")])
    r = _run(tmp_path)
    assert r.returncode == 0, (r.stderr, r.stdout)
    # disabled returns before the loop AND before the json.dumps count line
    assert r.stdout.strip() == ""
    assert _read_leads(tmp_path)["E0009"] == "AWAITING_OWNER_APPROVAL"
