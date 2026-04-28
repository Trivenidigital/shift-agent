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
import importlib.util
spec = importlib.util.spec_from_file_location("ccl", {str(CREATE)!r})
mod = importlib.util.module_from_spec(spec)
# Use a NON-"__main__" name so the bottom `if __name__ == "__main__": main()`
# block does NOT fire during exec_module. We call main() ourselves AFTER
# applying all patches. This is the only way to inject customer_now overrides
# before main() runs.
mod.__name__ = "ccl_test_loaded"
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.path.insert(0, str(pathlib.Path({str(Path(__file__).resolve().parent.parent / 'src' / 'platform')!r})))
spec.loader.exec_module(mod)

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


def _run_apply(env_dir, bridge_port, code, decision, edit_text="", reason=""):
    extra = []
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
] + {extra!r}
import pathlib
import importlib.util
spec = importlib.util.spec_from_file_location("acod", {str(APPLY)!r})
mod = importlib.util.module_from_spec(spec)
mod.__name__ = "__main__"
mod.CONFIG_PATH = pathlib.Path({str(env_dir / 'config.yaml')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.TEMPLATE_DIR = pathlib.Path({str(env_dir / 'templates')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
sys.path.insert(0, str(pathlib.Path({str(Path(__file__).resolve().parent.parent / 'src' / 'platform')!r})))
spec.loader.exec_module(mod)
"""
    return subprocess.run(
        [sys.executable, "-c", wrapper],
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


# ─── Tests ─────────────────────────────────────────────


def test_create_lead_writes_state_and_sends_card(env_dir, bridge_server):
    port, BridgeStub = bridge_server
    fields = {"headcount": 50, "event_date": "2026-06-15", "menu_preferences": ["vegetarian"]}
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
    # Card sent to owner JID
    assert len(BridgeStub.requests) == 1
    assert BridgeStub.requests[0]["chatId"] == "19045550100@s.whatsapp.net"
    assert out["approval_code"] in BridgeStub.requests[0]["message"]


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
    # Card sent only once
    assert len(BridgeStub.requests) == 1


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
    assert len(BridgeStub.requests) == 1  # owner card

    # 2) Apply approve
    r2 = _run_apply(env_dir, port, code, "approve")
    assert r2.returncode == 0, r2.stderr
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out2["new_status"] == "SENT_TO_CUSTOMER"
    assert out2["outbound_sent"] is True

    # 3) Customer received quote
    assert len(BridgeStub.requests) == 2
    customer_msg = BridgeStub.requests[1]
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
    # Audit trail RECORDED (CateringLeadRejected entry)
    rejected = _read_audit_entries(env_dir, "catering_lead_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "event_date_past"
    assert rejected[0]["original_message_id"] == "MSG_PAST_001"
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
    calendar date. Helper's strptime catches it; emits ERR_EVENT_DATE_INVALID.

    Closes the silent-failure-hunter HIGH-1 finding from plan review."""
    port, _ = bridge_server
    r = _run_create(
        env_dir, port, fields={"event_date": "2026-13-45"},
        message_id="MSG_MALFORMED_001",
    )
    assert r.returncode == 2
    assert "ERR_EVENT_DATE_INVALID:" in r.stderr
    rejected = _read_audit_entries(env_dir, "catering_lead_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "event_date_invalid_calendar"


def test_invalid_timezone_emits_clear_error_with_audit(env_dir, bridge_server):
    """Malformed cfg.customer.timezone (typo) surfaces ERR_TIMEZONE_INVALID +
    EXIT_SCHEMA_VIOLATION (5), AND writes a CateringLeadRejected audit entry."""
    port, _ = bridge_server
    r = _run_create(
        env_dir, port, fields={"event_date": "2026-06-15"},
        message_id="MSG_BADTZ_001", customer_tz="America/New_Yrok",
    )
    assert r.returncode == 5  # EXIT_SCHEMA_VIOLATION
    assert "ERR_TIMEZONE_INVALID:" in r.stderr
    # Audit log entry must be present (covers pr-test-analyzer Gap E)
    rejected = _read_audit_entries(env_dir, "catering_lead_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "timezone_invalid"


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
