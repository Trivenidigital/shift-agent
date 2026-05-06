"""Shared helpers for v3.1 B1 case pytest (`test_catering_b1_cases.py`).

Private module (leading underscore - pytest does not collect). Mirrors the
plain-function helpers in `tests/test_catering_v02_scripts.py` but keeps the
B1 doc-spec test file self-contained against future helper drift. If both
modules ever need to share a single source of truth, extract upward to
`tests/conftest.py` per design-review HIGH-C1.

Intentionally Linux-only - every function depends on `safe_io`'s fcntl path
or hyphen-named scripts that need importlib loading.

IMPORTANT - importlib pattern for hyphen-named scripts (no .py extension):
  spec_from_file_location(name, path) returns None for files without a
  recognized extension. We must construct an explicit SourceFileLoader.
  Additionally, we cannot pre-set mod.__name__ to "__main__" because the
  loader's _check_name_wrapper rejects exec_module calls where
  module.__name__ != spec.name. The correct pattern is:
    1. Build SourceFileLoader explicitly
    2. exec_module first (with __name__ = spec.name)
    3. Override module-level path attrs AFTER exec_module so they survive
    4. Apply any monkey-patches (e.g. customer_now)
    5. Call sys.exit(mod.main()) explicitly
  This pattern is what `tests/test_validate_sender_block.py` already does
  for hyphen-named scripts; the existing v02 helpers used a broken
  pattern that returned spec=None - tests written against it never
  actually executed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import yaml

# Script paths (resolved from this file's location: tests/_b1_helpers.py -> repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent
CREATE = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "create-catering-lead"
APPLY = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"
LOOKUP = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "lookup-prior-leads-by-phone"
TEMPLATES_DIR = _REPO_ROOT / "src" / "agents" / "catering" / "templates"
PLATFORM_DIR = _REPO_ROOT / "src" / "platform"


class BridgeStub(BaseHTTPRequestHandler):
    """Captures bridge POST bodies into BridgeStub.requests (class attribute).

    Class-level requests list reset by `bridge_server` fixture between tests.
    """
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except json.JSONDecodeError as e:
            # Stash raw bytes + error so bridge_post_text can surface diagnosis
            doc = {"_raw_body": body, "_decode_error": str(e)}
        self.__class__.requests.append(doc)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


def make_env_dir(tmp_path: Path, *, customer_tz: str = "America/New_York") -> Path:
    """Build per-test config + state dir + template symlinks."""
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    for f in TEMPLATES_DIR.iterdir():
        (templates / f.name).symlink_to(f.absolute())
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t", "timezone": customer_tz},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _env_for_subprocess() -> dict:
    return {
        **os.environ,
        "PYTHONPATH": str(PLATFORM_DIR),
    }


def run_create(
    env_dir: Path,
    bridge_port: int,
    fields: dict,
    *,
    customer_phone: str = "+19045550199",
    customer_name: str = "Priya",
    raw: str = "Need catering 50ppl Saturday",
    message_id: str = "msg_1",
    now_override: Optional[datetime] = None,
):
    """Invoke create-catering-lead via importlib wrapper.

    now_override (tz-aware datetime): patches mod.customer_now BEFORE main() runs.

    Pattern: SourceFileLoader (hyphen-named script) -> exec_module ->
    override module-level paths -> patch customer_now -> sys.exit(mod.main()).
    """
    now_iso = now_override.isoformat() if now_override else None
    use_now = now_override is not None
    wrapper = f"""
import sys, pathlib
import importlib.util
import importlib.machinery

sys.argv = [
    "create-catering-lead",
    "--customer-phone", {customer_phone!r},
    "--customer-name", {customer_name!r},
    "--raw-inquiry", {raw!r},
    "--message-id", {message_id!r},
    "--fields-json", {json.dumps(fields)!r},
]

# SourceFileLoader: required for hyphen-named scripts (no .py extension).
loader = importlib.machinery.SourceFileLoader("ccl", {str(CREATE)!r})
spec = importlib.util.spec_from_file_location("ccl", {str(CREATE)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(pathlib.Path({str(PLATFORM_DIR)!r})))
spec.loader.exec_module(mod)

# Override module-level paths AFTER exec_module so they survive.
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"

if {use_now!r}:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _frozen = _dt.fromisoformat({now_iso!r})
    def _patched_customer_now(tz_name):
        return _frozen.astimezone(_ZI(tz_name))
    mod.customer_now = _patched_customer_now

sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, env=_env_for_subprocess(),
        timeout=20,
    )


def run_apply(
    env_dir: Path,
    bridge_port: int,
    code: str,
    decision: str,
    *,
    edit_text: str = "",
    reason: str = "",
    menu_path: Optional[Path] = None,
    sender_role: str = "owner",
):
    """Invoke apply-catering-owner-decision via importlib wrapper.

    menu_path: if set, overrides mod.MENU_PATH for the subprocess (used by C16/C17).
    sender_role: passed through as --sender-role (privilege gate added by
        commit 02afc22 — B-021/D-013/H-008). Defaults to "owner" so all
        existing positive-path tests continue to pass.

    Pattern matches run_create: SourceFileLoader -> exec_module -> override
    paths (including MENU_PATH) -> sys.exit(mod.main()).
    """
    extra = []
    if edit_text:
        extra += ["--edit-text", edit_text]
    if reason:
        extra += ["--reason", reason]
    menu_override = ""
    if menu_path is not None:
        menu_override = f"mod.MENU_PATH = pathlib.Path({str(menu_path)!r})"
    wrapper = f"""
import sys, pathlib
import importlib.util
import importlib.machinery

sys.argv = [
    "apply-catering-owner-decision",
    "--code", {code!r},
    "--decision", {decision!r},
    "--sender-role", {sender_role!r},
] + {extra!r}

loader = importlib.machinery.SourceFileLoader("acod", {str(APPLY)!r})
spec = importlib.util.spec_from_file_location("acod", {str(APPLY)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(pathlib.Path({str(PLATFORM_DIR)!r})))
spec.loader.exec_module(mod)

# Override module-level paths AFTER exec_module so they survive.
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
{menu_override}

sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, env=_env_for_subprocess(),
        timeout=20,
    )


def lookup_prior_leads_by_phone_helper(phone: str, leads_path: Path) -> dict:
    """Load lookup-prior-leads-by-phone via importlib (hyphen-named script)
    and call the importable function directly.

    Uses SourceFileLoader explicitly (matches the pattern documented in
    test_validate_sender_block.py for hyphen-named scripts).
    """
    import importlib.util
    import importlib.machinery
    sys.path.insert(0, str(PLATFORM_DIR))
    loader = importlib.machinery.SourceFileLoader("lookup_mod", str(LOOKUP))
    spec = importlib.util.spec_from_file_location("lookup_mod", str(LOOKUP), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.lookup_prior_leads_by_phone(phone, leads_path=leads_path)


def mk_lead(
    *, lead_id: str, phone: str, status: str = "AWAITING_OWNER_APPROVAL",
    created_at: datetime, event_date: Optional[str] = None,
    dietary: Optional[list[str]] = None,
) -> dict:
    """Construct a minimal CateringLead dict matching the schema."""
    return {
        "lead_id": lead_id,
        "status": status,
        "customer_phone": phone,
        "customer_name": "Test Customer",
        "raw_inquiry": "test inquiry",
        "original_message_id": f"msg_{lead_id}",
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
        "extracted": {
            "headcount": 30,
            "event_date": event_date,
            "dietary_restrictions": dietary or [],
        },
        "quote_text": "",
        "quote_version": 0,
        "owner_approval_code": None,
        "customer_replied": False,
    }


def seed_leads(env_dir: Path, leads: list[dict]) -> Path:
    """Write a CateringLeadStore JSON file at env_dir/state/catering-leads.json."""
    state = env_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "catering-leads.json"
    path.write_text(json.dumps({"leads": leads}), encoding="utf-8")
    return path


def read_leads(env_dir: Path) -> dict:
    p = env_dir / "state" / "catering-leads.json"
    if not p.exists():
        return {"leads": []}
    return json.loads(p.read_text())


def read_audit_entries(env_dir: Path, type_filter: Optional[str] = None) -> list[dict]:
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    if type_filter:
        return [e for e in entries if e.get("type") == type_filter]
    return entries


def bridge_post_text(BridgeStubCls) -> str:
    """Most-recent bridge POST's `message` field (per create-catering-lead's
    payload structure: {"chatId": jid, "message": message}).

    Asserts loudly on schema drift via 'message' key check.
    """
    assert BridgeStubCls.requests, "no bridge POST captured"
    last = BridgeStubCls.requests[-1]
    assert "message" in last, f"unexpected payload keys: {list(last.keys())}"
    return last["message"]


def make_menu_fixture(tmp_path: Path) -> Path:
    """Build a Menu fixture via MenuItem.model_dump() (NOT hand-written JSON).

    Ensures schema-drift fails LOUDLY at fixture-construction time (per
    silent-failure-hunter MEDIUM-5). Verified against current schema:
    MenuItem fields = (name, price_usd, category, dietary_tags, available, notes, serves);
    Menu requires updated_at.
    """
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import Menu, MenuItem  # noqa: E402
    items = [
        MenuItem(name="Veg Biryani", price_usd=200.0, dietary_tags=["veg"]),
        MenuItem(name="Paneer Tikka", price_usd=180.0, dietary_tags=["veg", "spicy"]),
        MenuItem(name="Chicken Curry", price_usd=220.0, dietary_tags=["non-veg"]),
        MenuItem(name="Lamb Biryani", price_usd=280.0, dietary_tags=["non-veg", "spicy"]),
        MenuItem(name="Mango Lassi", price_usd=80.0, dietary_tags=["veg", "dairy-free"]),
    ]
    menu = Menu(items=items, updated_at=datetime.now(tz=timezone.utc))
    menu_path = tmp_path / "catering-menu.json"
    menu_path.write_text(menu.model_dump_json(), encoding="utf-8")
    return menu_path
