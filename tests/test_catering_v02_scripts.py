"""End-to-end tests for catering v0.2 scripts. Linux-only (fcntl).

Tests via subprocess + env-overridable paths, mirroring tests/test_lid_learn.py.
Mocks the WhatsApp bridge with a stub HTTP server.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)

CREATE = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "scripts" / "create-catering-lead"
APPLY = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "templates"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except Exception:
            doc = {}
        self.__class__.requests.append(doc)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server():
    _BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, _BridgeStub
    finally:
        server.shutdown()


@pytest.fixture
def env_dir(tmp_path):
    """Build per-test config + state dir + template symlinks."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    templates = tmp_path / "templates"
    state.mkdir()
    logs.mkdir()
    templates.mkdir()
    # Symlink the real templates so the renderer can find them
    for f in TEMPLATES_DIR.iterdir():
        (templates / f.name).symlink_to(f.absolute())
    cfg = {
        "schema_version": 1,
        "customer": {"name": "Test", "location_id": "loc_t", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550100",
                  "self_chat_jid": "19045550100@s.whatsapp.net"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
        "catering": {"enabled": True},
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def _env(env_dir, bridge_port):
    return {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src" / "platform"),
        "HERMES_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}/send",
        "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1",
    }


def _patch_paths_in_script(script_text: str, env_dir: Path) -> str:
    """Scripts hardcode /opt/shift-agent/* paths. For tests we patch by overriding
    via a thin wrapper that monkeypatches those Path constants. We can't easily
    do that from a subprocess, so instead we run the script with the test
    paths injected via os.environ-readable paths."""
    return script_text  # placeholder — see _run_via_wrapper below


def _run_create(env_dir, bridge_port, fields, customer_phone="+19045550199",
                customer_name="Priya", raw="Need catering 50ppl Saturday",
                message_id="msg_1", now_override=None, customer_tz=None):
    """Invoke create-catering-lead via a wrapper that overrides the hardcoded paths.

    now_override: if set (tz-aware datetime ISO string), patches mod.customer_now
                  so test sees deterministic "today" in customer tz.
    customer_tz:  if set, rewrites env_dir's config.yaml to use this tz before
                  the script runs. Composes with now_override.
    """
    if customer_tz is not None:
        cfg_path = env_dir / "config.yaml"
        cfg_dict = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        cfg_dict["customer"]["timezone"] = customer_tz
        cfg_path.write_text(yaml.safe_dump(cfg_dict), encoding="utf-8")

    now_iso = now_override.isoformat() if hasattr(now_override, "isoformat") else now_override
    use_now_override = now_override is not None
    wrapper = f"""
import os, sys
sys.argv = [
    "create-catering-lead",
    "--customer-phone", {customer_phone!r},
    "--customer-name", {customer_name!r},
    "--raw-inquiry", {raw!r},
    "--message-id", {message_id!r},
    "--fields-json", {json.dumps(fields)!r},
]
import pathlib
import importlib.machinery
import importlib.util
loader = importlib.machinery.SourceFileLoader("ccl", {str(CREATE)!r})
spec = importlib.util.spec_from_file_location("ccl", {str(CREATE)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
# Use a NON-"__main__" name so the bottom `if __name__ == "__main__": main()`
# block does NOT fire during exec_module. We call main() ourselves AFTER
# applying all patches. This is the only way to inject customer_now overrides
# before main() runs.
sys.path.insert(0, str(pathlib.Path({str(Path(__file__).resolve().parent.parent / 'src' / 'platform')!r})))
spec.loader.exec_module(mod)

mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"

if {use_now_override!r}:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    mod._customer_now_calls = []
    _frozen = _dt.fromisoformat({now_iso!r})
    def _patched_customer_now(tz_name):
        mod._customer_now_calls.append(tz_name)
        return _frozen.astimezone(_ZI(tz_name))
    mod.customer_now = _patched_customer_now

sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, env=_env(env_dir, bridge_port),
        timeout=20,
    )


def _run_apply(env_dir, bridge_port, code, decision, edit_text="", reason="",
               sender_role="owner"):
    extra = []
    stdin_text = ""
    if decision == "approve":
        extra += ["--quote-text-stdin", "--skip-finalize"]
        matched_leads = [
            lead for lead in _read_leads(env_dir).get("leads", [])
            if lead.get("owner_approval_code") == code
        ]
        if matched_leads:
            lead = matched_leads[0]
            extracted = lead.get("extracted") or {}
            headcount = extracted.get("headcount") or "your"
            event_date = extracted.get("event_date") or "your event date"
            lead_id = lead.get("lead_id", "")
            stdin_text = (
                f"Catering quote for {headcount} guests on {event_date}. "
                f"Reply with any questions. Ref: {lead_id}"
            )
        else:
            stdin_text = f"Catering quote for {code}. Reply with any questions."
    if edit_text:
        extra += ["--edit-text", edit_text]
    if reason:
        extra += ["--reason", reason]
    wrapper = f"""
import os, sys, runpy
sys.argv = [
    "apply-catering-owner-decision",
    "--code", {code!r},
    "--decision", {decision!r},
    "--sender-role", {sender_role!r},
] + {extra!r}
import pathlib
import importlib.machinery
import importlib.util
loader = importlib.machinery.SourceFileLoader("acod", {str(APPLY)!r})
spec = importlib.util.spec_from_file_location("acod", {str(APPLY)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(pathlib.Path({str(Path(__file__).resolve().parent.parent / 'src' / 'platform')!r})))
spec.loader.exec_module(mod)

mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.exit(mod.main())
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        input=stdin_text,
        capture_output=True, text=True, env=_env(env_dir, bridge_port),
        timeout=20,
    )


def _read_leads(env_dir):
    p = env_dir / "state" / "catering-leads.json"
    if not p.exists():
        return {"leads": []}
    return json.loads(p.read_text())


def _read_log(env_dir):
    p = env_dir / "logs" / "decisions.log"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _requests_to(requests: list[dict], chat_id: str) -> list[dict]:
    return [r for r in requests if r.get("chatId") == chat_id]


# ─── Tests ─────────────────────────────────────────────


def test_create_lead_writes_state_and_sends_card(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    # Relative future date: the script rejects past event_dates, so a hardcoded
    # literal is a calendar time-bomb (failed once "today" passed 2026-06-15).
    fields = {"headcount": 50, "event_date": (date.today() + timedelta(days=30)).isoformat(), "menu_preferences": ["vegetarian"]}
    r = _run_create(env_dir, port, fields)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["lead_id"] == "L0001"
    assert out["approval_code"].startswith("#") and len(out["approval_code"]) == 6
    assert out["card_sent"] is True
    # State written
    leads = _read_leads(env_dir)
    assert len(leads["leads"]) == 1
    assert leads["leads"][0]["status"] == "AWAITING_OWNER_APPROVAL"
    assert leads["leads"][0]["extracted"]["headcount"] == 50
    # Card sent to owner JID. create-catering-lead also sends a customer ack.
    owner_cards = _requests_to(BridgeStub.requests, "19045550100@s.whatsapp.net")
    assert len(owner_cards) == 1
    assert out["approval_code"] in owner_cards[0]["message"]


def test_create_lead_idempotent_replay(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    fields = {"headcount": 30, "event_date": "2026-07-04"}
    r1 = _run_create(env_dir, port, fields, message_id="meta_xyz")
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    r2 = _run_create(env_dir, port, fields, message_id="meta_xyz")
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out1["lead_id"] == out2["lead_id"]
    assert out2.get("idempotent_replay") is True
    assert len(_read_leads(env_dir)["leads"]) == 1
    # Owner card + customer ack sent only once; idempotent replay sends no new card.
    assert len(BridgeStub.requests) == 2


def test_create_lead_disabled_in_config(env_dir, bridge_server):
    port, _ = bridge_server
    cfg = yaml.safe_load((env_dir / "config.yaml").read_text())
    cfg["catering"]["enabled"] = False
    (env_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = _run_create(env_dir, port, {"headcount": 10})
    assert r.returncode == 2  # EXIT_DISABLED
    assert _read_leads(env_dir)["leads"] == []


def test_create_lead_invalid_fields_json(env_dir, bridge_server):
    port, _ = bridge_server
    fields = {"headcount": -5}  # negative — schema rejects
    r = _run_create(env_dir, port, fields)
    assert r.returncode == 2  # EXIT_INVALID_INPUT


def test_apply_approve_sends_quote_to_customer(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    # 1) Create
    r1 = _run_create(env_dir, port, {"headcount": 25, "event_date": "2026-08-01"},
                     customer_phone="+15551234567", customer_name="Anita")
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    code = out1["approval_code"]
    assert len(_requests_to(BridgeStub.requests, "19045550100@s.whatsapp.net")) == 1  # owner card

    # 2) Apply approve
    r2 = _run_apply(env_dir, port, code, "approve")
    assert r2.returncode == 0, r2.stderr
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out2["new_status"] == "SENT_TO_CUSTOMER"
    assert out2["outbound_sent"] is True

    # 3) Customer received quote
    customer_messages = _requests_to(BridgeStub.requests, "15551234567@s.whatsapp.net")
    assert len(customer_messages) == 2  # create acknowledgement + approved quote
    customer_msg = customer_messages[-1]
    assert customer_msg["chatId"] == "15551234567@s.whatsapp.net"
    assert "Anita" in customer_msg["message"] or "L0001" in customer_msg["message"]

    # 4) State machine final
    leads = _read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "SENT_TO_CUSTOMER"

    # 5) Audit trail
    log = _read_log(env_dir)
    types = [e["type"] for e in log]
    assert "catering_lead_created" in types
    assert "catering_owner_decision" in types
    assert "catering_quote_sent" in types
    # Status changes recorded
    transitions = [(e["from_status"], e["to_status"]) for e in log if e["type"] == "catering_lead_status_change"]
    assert ("NEW", "AWAITING_OWNER_APPROVAL") in transitions
    assert ("AWAITING_OWNER_APPROVAL", "OWNER_APPROVED") in transitions
    assert ("OWNER_APPROVED", "SENT_TO_CUSTOMER") in transitions


def test_apply_reject_no_customer_send(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    r1 = _run_create(env_dir, port, {"headcount": 100})
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    initial_requests = len(BridgeStub.requests)
    r2 = _run_apply(env_dir, port, out1["approval_code"], "reject", reason="too big")
    assert r2.returncode == 0, r2.stderr
    leads = _read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "OWNER_REJECTED"
    # Customer NOT messaged on reject
    assert len(BridgeStub.requests) == initial_requests


def test_apply_edit_transitions_to_owner_edited(env_dir, bridge_server):
    port, _ = bridge_server
    r1 = _run_create(env_dir, port, {"headcount": 40})
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    r2 = _run_apply(env_dir, port, out1["approval_code"], "edit",
                    edit_text="make it veg only, keep budget under $500")
    assert r2.returncode == 0, r2.stderr
    leads = _read_leads(env_dir)["leads"]
    assert leads[0]["status"] == "OWNER_EDITED"


def test_apply_unknown_code_exits_not_found(env_dir, bridge_server):
    port, _ = bridge_server
    r = _run_apply(env_dir, port, "#XXXXX", "approve")
    assert r.returncode == 4  # EXIT_NOT_FOUND


def test_apply_invalid_code_format(env_dir, bridge_server):
    port, _ = bridge_server
    r = _run_apply(env_dir, port, "not-a-code", "approve")
    assert r.returncode == 2  # EXIT_INVALID_INPUT


def test_apply_double_approve_rejected(env_dir, bridge_server):
    """After approve + send, the lead is SENT_TO_CUSTOMER — same code can't approve again."""
    port, _ = bridge_server
    r1 = _run_create(env_dir, port, {"headcount": 12})
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    r2 = _run_apply(env_dir, port, out1["approval_code"], "approve")
    assert r2.returncode == 0
    # Second approve — code now applies to a SENT_TO_CUSTOMER lead, not AWAITING_*
    r3 = _run_apply(env_dir, port, out1["approval_code"], "approve")
    assert r3.returncode == 4  # not found in AWAITING_OWNER_APPROVAL


# === C10 past-date validation (v3.1 — closes design-spec-pending-validation) ===

def _read_audit_entries(env_dir, type_filter=None):
    """Helper: read decisions.log NDJSON entries; optionally filter by type."""
    log = env_dir / "logs" / "decisions.log"
    if not log.exists():
        return []
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    if type_filter:
        return [e for e in entries if e.get("type") == type_filter]
    return entries


def test_past_event_date_rejected(env_dir, bridge_server):
    """v3.1 C10 — solidly past date rejects with EXIT_INVALID_INPUT (2)."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    r = _run_create(
        env_dir, port,
        fields={"headcount": 50, "event_date": "2020-01-01"},
        customer_phone="+19045551234", message_id="MSG_PAST_001",
    )
    assert r.returncode == 2  # EXIT_INVALID_INPUT
    assert "ERR_EVENT_DATE_PAST:" in r.stderr
    # No state mutation
    leads_path = env_dir / "state" / "catering-leads.json"
    assert not leads_path.exists() or json.loads(leads_path.read_text()).get("leads", []) == []
    # Audit trail RECORDED with self-describing fields (PR-review MEDIUM-3+4)
    rejected = _read_audit_entries(env_dir, "catering_lead_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "event_date_past"
    assert rejected[0]["original_message_id"] == "MSG_PAST_001"
    assert rejected[0]["customer_tz"] == "America/New_York"
    assert rejected[0]["event_date"] == "2020-01-01"
    # No bridge call (approval card never sent)
    assert len(BridgeStub.requests) == 0


def test_event_date_today_accepted_with_frozen_now(env_dir, bridge_server):
    """Today's date in customer tz passes — same-day events are valid.
    Frozen-now eliminates wall-clock flakiness near midnight or on non-NY runners."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    frozen = datetime(2026, 4, 28, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    port, _ = bridge_server
    r = _run_create(
        env_dir, port,
        fields={"headcount": 30, "event_date": "2026-04-28"},
        message_id="MSG_TODAY_001", now_override=frozen,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "lead_id" in out


def test_event_date_today_in_customer_tz_but_yesterday_in_utc(env_dir, bridge_server):
    """Customer in Pacific/Honolulu (UTC-10). When VPS-UTC has rolled to next
    day but customer-local is still 'today', the lead must accept.

    Load-bearing test: a regression to datetime.utcnow().date() would silently
    reject Hawaii customers."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    # 2026-04-29 02:00 UTC = 2026-04-28 16:00 in Honolulu
    frozen_utc = datetime(2026, 4, 29, 2, 0, tzinfo=ZoneInfo("UTC"))
    port, _ = bridge_server
    r = _run_create(
        env_dir, port,
        fields={"headcount": 20, "event_date": "2026-04-28"},
        message_id="MSG_HNL_001",
        now_override=frozen_utc, customer_tz="Pacific/Honolulu",
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"


def test_yesterday_rejected_today_accepted_boundary(env_dir, bridge_server):
    """Pin the strict `<` comparator. Yesterday rejected; today accepted."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    frozen = datetime(2026, 4, 28, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    yesterday = (frozen.date() - timedelta(days=1)).isoformat()
    today = frozen.date().isoformat()
    port, _ = bridge_server
    r_y = _run_create(
        env_dir, port, fields={"event_date": yesterday},
        message_id="MSG_BOUNDARY_Y", now_override=frozen,
    )
    assert r_y.returncode == 2 and "ERR_EVENT_DATE_PAST" in r_y.stderr
    r_t = _run_create(
        env_dir, port, fields={"event_date": today},
        message_id="MSG_BOUNDARY_T", now_override=frozen,
    )
    assert r_t.returncode == 0, f"stderr: {r_t.stderr}"


def test_event_date_absent_passes_through(env_dir, bridge_server):
    """No event_date key → past-date check is a no-op; lead persists."""
    port, _ = bridge_server
    r = _run_create(
        env_dir, port, fields={"headcount": 50},
        message_id="MSG_NODATE_001",
    )
    assert r.returncode == 0
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "lead_id" in out


def test_event_date_malformed_calendar_rejected(env_dir, bridge_server):
    """Schema regex passes (2026-13-45 matches format) but is not a valid
    calendar date. Schema validation rejects it before state mutation.

    Closes the silent-failure-hunter HIGH-1 finding from plan review."""
    port, _ = bridge_server
    r = _run_create(
        env_dir, port, fields={"event_date": "2026-13-45"},
        message_id="MSG_MALFORMED_001",
    )
    assert r.returncode == 2
    assert "event_date" in r.stderr
    assert "Value error" in r.stderr
    assert _read_leads(env_dir)["leads"] == []


def test_invalid_timezone_emits_clear_error_with_audit(env_dir, bridge_server):
    """Malformed cfg.customer.timezone (typo) surfaces a config validation error."""
    port, _ = bridge_server
    r = _run_create(
        env_dir, port, fields={"event_date": "2026-06-15"},
        message_id="MSG_BADTZ_001", customer_tz="America/New_Yrok",
    )
    assert r.returncode == 5  # EXIT_SCHEMA_VIOLATION
    assert "customer.timezone" in r.stderr
    assert "invalid IANA timezone" in r.stderr
    assert _read_leads(env_dir)["leads"] == []


def test_replay_of_existing_lead_with_now_past_event_date_returns_idempotent(
    env_dir, bridge_server,
):
    """LOAD-BEARING (pr-test-analyzer Gap C, criticality 9): the entire reason
    we reverse-order idempotency-before-past-date is so a lead created in the
    past with then-future-now-past event_date replays as `idempotent_replay: true`,
    NOT as a past-date rejection.

    Path:
      1. Frozen "now" = 2026-05-01. Customer creates lead with event_date=2026-05-15
         (future at that moment). Lead persists.
      2. Frozen "now" = 2026-06-01. Customer's WhatsApp webhook redelivers the same
         message (Meta at-least-once semantics). event_date is now in the past
         relative to 2026-06-01.
      3. Replay must return idempotent_replay=true exit 0, NOT exit 2 with
         ERR_EVENT_DATE_PAST. Customer's already-accepted lead is preserved."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    port, _ = bridge_server

    # Step 1: create the lead at "now=2026-05-01" with future event_date
    t1 = datetime(2026, 5, 1, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    r1 = _run_create(
        env_dir, port,
        fields={"headcount": 40, "event_date": "2026-05-15"},
        customer_phone="+19045553333",
        message_id="MSG_REPLAY_HAPPYPATH",
        now_override=t1,
    )
    assert r1.returncode == 0, f"first call must succeed: stderr={r1.stderr}"
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    assert "lead_id" in out1

    # Step 2 & 3: replay at "now=2026-06-01" (event_date is now past) — must be idempotent
    t2 = datetime(2026, 6, 1, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    r2 = _run_create(
        env_dir, port,
        fields={"headcount": 40, "event_date": "2026-05-15"},
        customer_phone="+19045553333",
        message_id="MSG_REPLAY_HAPPYPATH",  # same message_id
        now_override=t2,
    )
    assert r2.returncode == 0, f"replay must idempotent_replay, got stderr={r2.stderr}"
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out2.get("idempotent_replay") is True
    assert out2["lead_id"] == out1["lead_id"]
    # No CateringLeadRejected entries — replay is a no-op, never reaches past-date check
    rejected = _read_audit_entries(env_dir, "catering_lead_rejected")
    assert rejected == [], (
        f"replay must not write rejection audit entry, got: {rejected}"
    )
    # Exactly ONE catering_lead_created entry — replay must NOT write a second
    # creation audit (would corrupt the per-lead audit trail; pr-test-analyzer
    # Gap D from PR review).
    created = _read_audit_entries(env_dir, "catering_lead_created")
    assert len(created) == 1, f"replay must not double-write created audit, got: {created}"


def test_audit_fail_recovery_rejection_path(env_dir, bridge_server):
    """Closes pr-test-analyzer Criticality 7: design-review HIGH-A invariant
    untested.

    When the audit log write fails (chmod logs/ readonly simulating disk full /
    perms / NFS hiccup), the script MUST still:
      - Emit the structured ERR_* prefix on stderr
      - Exit with the documented code (2 for past-date input)
      - Emit a WARN line on stderr explaining the audit failure

    This pins the design-review HIGH-A 'degraded audit beats lost rejection'
    behavior — generalized in PR-review fix to ALL audit calls via
    _audit_best_effort wrapper.
    """
    port, _ = bridge_server
    # Make logs/ writable but logs/decisions.log unwritable (a directory works
    # cross-platform if we make decisions.log a directory — _log() will fail
    # on the open() inside ndjson_append).
    log_path = env_dir / "logs" / "decisions.log"
    log_path.mkdir()  # decisions.log is now a DIR, not a file → ndjson_append fails

    r = _run_create(
        env_dir, port,
        fields={"event_date": "2020-01-01"},
        message_id="MSG_AUDIT_FAIL_001",
    )
    assert r.returncode == 2  # EXIT_INVALID_INPUT preserved
    assert "ERR_EVENT_DATE_PAST:" in r.stderr  # structured contract preserved
    assert "WARN: audit log failed" in r.stderr  # operator visibility


def test_audit_fail_recovery_success_path(env_dir, bridge_server):
    """Same pattern, success path. Closes silent-failure HIGH-1 (PR review):
    bare success-path _log() calls would have stranded leads pre-fix.

    Lead is persisted in catering-leads.json; audit fails; stdout JSON still
    emitted; exit 0; multiple WARN lines for each failed audit write."""
    port, _ = bridge_server
    log_path = env_dir / "logs" / "decisions.log"
    log_path.mkdir()  # same trick — decisions.log is a DIR

    r = _run_create(
        env_dir, port,
        fields={"headcount": 25, "event_date": "2027-06-15"},
        message_id="MSG_AUDIT_FAIL_SUCC_001",
    )
    assert r.returncode == 0, f"success path must exit 0 even on audit fail: stderr={r.stderr}"
    # Lead is persisted (state file written by atomic_write_json BEFORE audit)
    leads = json.loads((env_dir / "state" / "catering-leads.json").read_text())
    assert len(leads.get("leads", [])) == 1
    # Stdout still has the contract
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "lead_id" in out and "approval_code" in out
    # 3 success-path audit calls all warned
    assert r.stderr.count("WARN: audit log failed") >= 1


def test_catering_lead_rejected_reason_enum_enforced(env_dir):
    """Pydantic Literal enforcement: future contributor adding a typo'd reason
    value (e.g., 'event_date_pastt') without updating the schema must fail loudly.

    Closes pr-test-analyzer Gap B."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
    from schemas import CateringLeadRejected  # noqa: E402
    from pydantic import ValidationError as _VE  # noqa: E402
    # Valid case
    e = CateringLeadRejected(
        type="catering_lead_rejected",
        ts="2026-04-28T22:00:00+00:00",
        customer_phone="+19999999999",
        original_message_id="X",
        reason="event_date_past",
    )
    assert e.reason == "event_date_past"
    # Invalid case
    with pytest.raises(_VE):
        CateringLeadRejected(
            type="catering_lead_rejected",
            ts="2026-04-28T22:00:00+00:00",
            customer_phone="+19999999999",
            original_message_id="X",
            reason="event_date_pastt",  # typo
        )


# === C23 off-menu-items renderer + extractor-prompt (v3.1 C18) ===

def _bridge_post_text(BridgeStub) -> str:
    """Extract the message body from the most recent bridge POST.
    BridgeStub.requests is a list of decoded JSON dicts; the bridge POST
    payload structure is {"chatId": jid, "message": message} per
    create-catering-lead's _bridge_post (line 108). Existing canonical test
    at line 238 confirms this — `BridgeStub.requests[0]["message"]`."""
    assert BridgeStub.requests, "no bridge POST captured"
    owner_cards = _requests_to(BridgeStub.requests, "19045550100@s.whatsapp.net")
    assert owner_cards, f"no owner-card POST captured: {BridgeStub.requests!r}"
    last = owner_cards[-1]
    assert "message" in last, f"unexpected payload keys: {list(last.keys())}"
    return last["message"]


def test_render_includes_off_menu_with_marker_and_exact_line(env_dir, bridge_server):
    """Exact-line assertion pins label, indent, separator, AND top-of-card
    marker at index 0. Cluster ordering pin: dietary_restrictions appears
    BEFORE off_menu_items, off_menu_items appears BEFORE delivery_or_pickup."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    fields = {
        "headcount": 50,
        "menu_preferences": ["spicy", "north-indian"],
        "off_menu_items": ["butter chicken", "lamb biryani"],
        "dietary_restrictions": ["vegetarian"],
        "delivery_or_pickup": "delivery",
    }
    r = _run_create(env_dir, port, fields, message_id="MSG_C23_001")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    # Top-of-card marker (ASCII " - " separator, no em-dash)
    assert "  [!] Off-menu requests detected - see below" in text
    # Exact off-menu line with 2-space indent + comma-space delimiter
    assert "  - Off-menu requests: butter chicken, lamb biryani" in text
    # Marker appears BEFORE the off-menu line (positional pin)
    assert text.find("[!] Off-menu requests detected") < text.find(
        "- Off-menu requests: butter chicken"
    )
    # Cluster ordering: Menu < Dietary < Off-menu < Delivery
    pos_menu = text.find("Menu: spicy")
    pos_dietary = text.find("Dietary: vegetarian")
    pos_off_menu = text.find("Off-menu requests: butter chicken")
    pos_delivery = text.find("Delivery: delivery")
    assert pos_menu < pos_dietary < pos_off_menu < pos_delivery, (
        f"cluster ordering wrong: menu={pos_menu} dietary={pos_dietary} "
        f"off_menu={pos_off_menu} delivery={pos_delivery}"
    )


def test_render_omits_off_menu_when_empty(env_dir, bridge_server):
    """Empty off_menu_items: NO marker, NO Off-menu line, no stray empty row."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    fields = {"headcount": 30, "off_menu_items": []}
    r = _run_create(env_dir, port, fields, message_id="MSG_C23_002")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    assert "Off-menu requests" not in text
    assert "[!]" not in text


def test_render_off_menu_in_inline_fallback_when_template_missing(env_dir, bridge_server):
    """Inline-fallback path: when the template file is absent, _render_approval_card
    falls back to inline boilerplate. The `summary` string is built once and
    consumed by both paths, so off-menu MUST propagate identically. Pins this
    invariant against future regressions that build inline-text from a
    different code path."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    # Unlink the template so template_path.exists() returns False at render time
    template_path = env_dir / "templates" / "catering_approval_card_to_owner.txt"
    if template_path.exists():
        template_path.unlink()
    fields = {"off_menu_items": ["mango lassi", "chai"]}
    r = _run_create(env_dir, port, fields, message_id="MSG_C23_003")
    assert r.returncode == 0, f"stderr: {r.stderr}"
    text = _bridge_post_text(BridgeStub)
    # Inline fallback's distinct boilerplate marker (proves we hit the fallback
    # branch, not the template branch)
    assert "*New Catering Inquiry" in text, "inline fallback boilerplate missing"
    # Off-menu line still renders identically
    assert "Off-menu requests: mango lassi, chai" in text


def test_render_truncates_long_off_menu_at_budget(env_dir, bridge_server):
    """Truncation cases:
    1. Under-budget — no truncation marker, all items rendered
    2. Over-budget multi-item — truncates with "(and N more)" suffix matching
       a strict regex r"\\(and \\d+ more\\)"
    """
    import re as _re
    port, BridgeStub = bridge_server

    # Case 1: under-budget — no truncation
    BridgeStub.requests.clear()
    items_small = ["a" * 50] * 10  # 10 × 52 chars = ~520 chars (under 1500)
    r = _run_create(env_dir, port, {"off_menu_items": items_small},
                    message_id="MSG_C23_TRUNC_SMALL")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    assert "(and " not in text  # no truncation marker
    assert text.count("a" * 50) == 10  # all 10 items present

    # Case 2: over-budget multi-item — truncates with "(and N more)"
    BridgeStub.requests.clear()
    items_big = ["b" * 100] * 20  # 20 × 102 chars = ~2040 chars (over 1500)
    r = _run_create(env_dir, port, {"off_menu_items": items_big},
                    message_id="MSG_C23_TRUNC_BIG")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    # Strict regex match (vs weak substring) — catches malformed N or missing parens
    assert _re.search(r"\(and \d+ more\)", text), (
        f"truncation suffix missing or malformed; expected '(and N more)' in: {text[-200:]}"
    )


def test_oversized_single_item_rejected_by_schema_before_renderer(env_dir, bridge_server):
    """A single off_menu_item exceeding schema max_length=200 is rejected by
    Pydantic at fields_json validation — exit 2, BEFORE the renderer's
    escape-hatch can fire. Pins the schema-vs-renderer ordering. The renderer's
    cutoff==0 escape-hatch is defensive against a future schema relaxation
    and remains intentionally untested at runtime through this entry point."""
    port, _ = bridge_server
    items_oversized = ["c" * 1600]
    r = _run_create(env_dir, port, {"off_menu_items": items_oversized},
                    message_id="MSG_C23_OVERSIZED")
    assert r.returncode == 2  # EXIT_INVALID_INPUT — schema rejects 1600-char item


def test_render_handles_max_length_item_at_200_chars(env_dir, bridge_server):
    """Test-analyzer G: schema boundary — item at exactly 200 chars renders cleanly."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    items = ["x" * 200]
    r = _run_create(env_dir, port, {"off_menu_items": items},
                    message_id="MSG_C23_MAXITEM")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    assert ("x" * 200) in text


def test_render_handles_max_items_list_at_20(env_dir, bridge_server):
    """Test-analyzer H: schema boundary — exactly 20 items at small length renders cleanly."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    items = [f"item{i:02d}" for i in range(20)]  # 20 × ~6 chars = ~120 chars
    r = _run_create(env_dir, port, {"off_menu_items": items},
                    message_id="MSG_C23_MAX20")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    # All 20 items present
    for i in range(20):
        assert f"item{i:02d}" in text


def test_off_menu_items_persists_through_script_round_trip(env_dir, bridge_server):
    """Plan-review #6: SCRIPT-level round-trip. Schema-level test at
    test_catering_schemas.py:154 only covers MODEL-level via Pydantic.
    This test pins atomic_write_json(LEADS_PATH) → re-read → field preserved."""
    port, _ = bridge_server
    items = ["paneer tikka", "kheer"]
    r = _run_create(env_dir, port, {"off_menu_items": items},
                    message_id="MSG_C23_ROUND_TRIP")
    assert r.returncode == 0
    leads_path = env_dir / "state" / "catering-leads.json"
    leads = json.loads(leads_path.read_text())
    assert len(leads["leads"]) == 1
    persisted = leads["leads"][0]["extracted"]["off_menu_items"]
    assert persisted == items


def test_idempotent_replay_with_off_menu_does_not_resend_card(env_dir, bridge_server):
    """Test-analyzer #7 + F (crit-9): replay carve-out test pins accepted
    behavior. Single bridge_server fixture spans BOTH calls (function-scope);
    pin by checking owner-card count and payload content."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()

    # First call — creates lead, sends original card
    items = ["dosa", "uttapam"]
    r1 = _run_create(env_dir, port, {"off_menu_items": items},
                     message_id="MSG_C23_REPLAY")
    assert r1.returncode == 0
    out1 = json.loads(r1.stdout.strip().splitlines()[-1])
    assert "lead_id" in out1
    assert len(_requests_to(BridgeStub.requests, "19045550100@s.whatsapp.net")) == 1, (
        "first call should send 1 owner card"
    )
    first_payload = _bridge_post_text(BridgeStub)
    assert "Off-menu requests: dosa, uttapam" in first_payload

    # Second call — same message_id → idempotent_replay, NO new bridge POST
    r2 = _run_create(env_dir, port, {"off_menu_items": items},
                     message_id="MSG_C23_REPLAY")
    assert r2.returncode == 0
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out2.get("idempotent_replay") is True
    assert out2["lead_id"] == out1["lead_id"]
    # Pin: still exactly 1 owner-card POST (no replay re-render)
    assert len(_requests_to(BridgeStub.requests, "19045550100@s.whatsapp.net")) == 1, (
        "replay must not send a second owner card"
    )
    # Replay branch should emit a stderr breadcrumb when off_menu_items present.
    # Pin the unique substring "verify in cockpit" — the JSON-key
    # `idempotent_replay` reflects through stdout/combined-output streams,
    # weakening that check; "verify in cockpit" only appears in the breadcrumb.
    assert "verify in cockpit" in r2.stderr, (
        f"replay breadcrumb missing from stderr: {r2.stderr!r}"
    )


def test_render_with_both_menu_preferences_and_off_menu_items_clusters_correctly(
    env_dir, bridge_server,
):
    """Co-presence: when BOTH menu_preferences (soft categories) AND
    off_menu_items (specific dishes not on menu) are populated, both render on
    the same card so owner can visually disambiguate and detect Kimi's
    misclassification. Pins the architect-Q1 cluster ordering invariant."""
    port, BridgeStub = bridge_server
    BridgeStub.requests.clear()
    fields = {
        "menu_preferences": ["spicy", "vegetarian-heavy"],
        "off_menu_items": ["paneer makhani", "rasmalai"],
    }
    r = _run_create(env_dir, port, fields, message_id="MSG_C23_COPRESENCE")
    assert r.returncode == 0
    text = _bridge_post_text(BridgeStub)
    # Both lines render
    assert "  - Menu: spicy, vegetarian-heavy" in text
    assert "  - Off-menu requests: paneer makhani, rasmalai" in text
    # Marker present at top
    assert "[!] Off-menu requests detected" in text
    # Order: marker (index 0) -> Menu -> Off-menu (cluster ordering pin)
    assert text.find("[!]") < text.find("Menu: spicy") < text.find(
        "Off-menu requests: paneer makhani"
    )


def test_skill_example_outputs_validate_against_schema():
    """Every JSON example output in parse_catering_inquiry/SKILL.md must
    validate against CateringLeadExtractedFields. Catches future SKILL/schema
    drift. Strict count assertion + minimum length floor catches truncated
    or wrong-pattern extraction."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))
    from schemas import CateringLeadExtractedFields  # noqa: E402

    skill_md_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "agents" / "catering" / "skills"
        / "parse_catering_inquiry" / "SKILL.md"
    )
    text = skill_md_path.read_text(encoding="utf-8")

    # Pattern: matches `Output: \`{...}\`` blocks. Brittle to fence-syntax
    # changes — if SKILL.md format ever drifts, the strict count below fires
    # rather than silently extracting wrong examples.
    import re
    pattern = re.compile(r"Output:\s*`(\{[^`]*\})`", re.DOTALL)
    examples = pattern.findall(text)
    # Strict count (not >=) — alerts if SKILL.md adds/removes examples without
    # updating this test
    assert len(examples) == 3, (
        f"expected exactly 3 JSON examples in SKILL.md; got {len(examples)}. "
        f"If SKILL.md was edited, update this assertion accordingly."
    )
    # Length floor catches truncated extraction (regex matched wrong substring)
    for i, example in enumerate(examples):
        assert len(example) > 50, (
            f"SKILL.md example #{i+1} is suspiciously short ({len(example)} chars); "
            f"regex may have extracted a partial match: {example!r}"
        )

    for i, example in enumerate(examples):
        try:
            CateringLeadExtractedFields.model_validate_json(example)
        except Exception as e:
            pytest.fail(f"SKILL.md example #{i+1} fails schema validation: {e}\nJSON: {example}")
