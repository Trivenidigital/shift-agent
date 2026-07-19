"""Shared tmp-path fixture factories for the production-readiness test batch.

Plain factory functions (imported by the new tests) rather than conftest
fixtures — several factories take arguments (proposal set, lead statuses, config
blocks) so a function API is cleaner than parametrized fixtures, and one module
keeps the F0-* regression tests + SKILL-md / prune / commerce tests reusing one
set of builders instead of per-test bespoke harnesses.

Windows note: several agent scripts `import fcntl` at module top (via safe_io),
which is Linux-only. `ensure_fcntl_stub()` injects a no-op stub so the count /
audit logic can be exercised in-process on Windows too (no-op on Linux, where
fcntl is real). Advisory locking is irrelevant to correctness in a single test
process, so a no-op flock is sound here.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path


# ── Windows import shim ──────────────────────────────────────────────────────
def ensure_fcntl_stub() -> None:
    """Make safe_io importable where `fcntl` is absent (Windows). No-op on Linux."""
    try:
        import fcntl  # noqa: F401
        return
    except ImportError:
        pass
    if "fcntl" in sys.modules:
        return
    stub = types.ModuleType("fcntl")
    stub.LOCK_EX = 2
    stub.LOCK_SH = 1
    stub.LOCK_UN = 8
    stub.LOCK_NB = 4
    stub.flock = lambda *a, **k: None
    stub.lockf = lambda *a, **k: None
    sys.modules["fcntl"] = stub


def load_script(module_name: str, path: Path):
    """SourceFileLoader an extensionless agent script into an importable module.
    Mirrors the repo pattern (test_handle_shift_sick_call_script.py)."""
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    loader.exec_module(mod)
    return mod


def read_log_rows(path: Path) -> list[dict]:
    """Parse an NDJSON decisions.log into a list of dict rows ([] if absent)."""
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


# ── config (base + arbitrary agent blocks: eod=, expense_bookkeeper=, ...) ───
def build_config_dict(**blocks) -> dict:
    """Minimum-valid Config dict; pass agent blocks as kwargs (e.g. eod={...},
    expense_bookkeeper={...}, daily_brief={...}) to compose per-agent configs."""
    cfg = {
        "schema_version": 1,
        "customer": {
            "name": "Test Store", "location_id": "loc_test_01",
            "timezone": "America/New_York", "languages": ["en"],
        },
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
    }
    cfg.update(blocks)
    return cfg


def write_config(dir_path: Path, **blocks) -> Path:
    import yaml
    p = dir_path / "config.yaml"
    p.write_text(yaml.safe_dump(build_config_dict(**blocks)), encoding="utf-8")
    return p


def build_brief_config(**overrides) -> dict:
    """Config carrying a daily_brief block (brief-config factory)."""
    brief = {"enabled": True, "brief_time": "07:00", "catchup_window_minutes": 60}
    brief.update(overrides)
    return build_config_dict(daily_brief=brief)


# ── pending-store (dict[str, Proposal]) + proposal builders ──────────────────
def _base_proposal(pid: str, code: str, status: str) -> dict:
    return {
        "proposal_id": pid, "code": code,
        "created_ts": _iso(), "last_updated_ts": _iso(),
        "absent_employee_id": "e001", "absent_date": "2026-04-25",
        "absent_shift": "09:00-17:00", "absent_role": "cashier",
        "absent_reason": "health", "input_message": "out sick",
        "message_id": f"m-{pid}", "status": status,
    }


def awaiting_proposal(pid: str, code: str) -> dict:
    return _base_proposal(pid, code, "awaiting_owner_approval")


def sent_proposal(pid: str, code: str) -> dict:
    d = _base_proposal(pid, code, "sent")
    d["sent_ts"] = _iso()
    return d


def accepted_proposal(pid: str, code: str) -> dict:
    d = _base_proposal(pid, code, "accepted")
    d["response_ts"] = _iso()
    d["response_message"] = "yes"
    return d


def expired_proposal(pid: str, code: str) -> dict:
    d = _base_proposal(pid, code, "expired")
    d["expired_ts"] = _iso()
    return d


def write_pending_store(dir_path: Path, proposals: list[dict]) -> Path:
    """proposals: list of proposal dicts (from the builders above)."""
    by_id = {p["proposal_id"]: p for p in proposals}
    p = dir_path / "pending.json"
    p.write_text(json.dumps({"proposals": by_id, "next_proposal_seq": len(by_id) + 1}),
                 encoding="utf-8")
    return p


# ── roster ───────────────────────────────────────────────────────────────────
def write_roster(dir_path: Path, employees: list[dict] | None = None,
                 schedule: dict | None = None) -> Path:
    p = dir_path / "roster.json"
    p.write_text(json.dumps({
        "location": {"id": "loc_test_01", "name": "Test", "timezone": "America/New_York"},
        "employees": employees or [{
            "id": "e001", "name": "Ravi Kumar", "role": "cashier",
            "phone": "+19045550101", "languages": ["en"],
            "can_cover_roles": ["cashier", "floor"], "status": "active",
            "phone_history": [], "restrictions": None, "lid": None,
        }],
        "schedule": schedule or {},
    }), encoding="utf-8")
    return p


# ── expense lead store + lead builder ────────────────────────────────────────
def expense_lead(expense_id: str, status: str, *, receipts_dir: Path,
                 image_name: str = "receipt.jpg", received_ago_hours: float = 0.0,
                 total_cents: int | None = None, vendor: str | None = None) -> dict:
    from datetime import timedelta
    lead = {
        "expense_id": expense_id,
        "original_message_id": f"msg-{expense_id}",
        "sender_phone": "+19045550123",
        "received_at": _iso(datetime.now(timezone.utc) - timedelta(hours=received_ago_hours)),
        # Forward slashes: the ExpenseLead.image_path validator matches against
        # EXPENSE_RECEIPTS_DIR with a literal "/" (not os.sep), so posix form is
        # required for the fixture to validate on Windows too.
        "image_path": f"{Path(receipts_dir).as_posix()}/{image_name}",
        "image_phash": "0" * 16,
        "image_byte_hash": "0" * 64,
        "status": status,
    }
    if total_cents is not None:
        lead["extracted_total_cents"] = total_cents
    if vendor is not None:
        lead["extraction"] = {"vendor_normalized": vendor}
    return lead


def write_expense_lead_store(dir_path: Path, leads: list[dict]) -> Path:
    p = dir_path / "leads.json"
    p.write_text(json.dumps({"schema_version": 1, "leads": leads, "last_id": len(leads)}),
                 encoding="utf-8")
    return p


# ── catering menu (provided per spec; minimal structured stand-in) ───────────
def build_catering_menu() -> dict:
    return {
        "menu_id": "M0001",
        "items": [
            {"name": "Veg Biryani", "unit": "tray"},
            {"name": "Paneer Tikka", "unit": "tray"},
        ],
    }
