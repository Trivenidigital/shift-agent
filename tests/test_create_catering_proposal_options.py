"""Task 2 - create-catering-proposal-options behavior tests."""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "create-catering-proposal-options"
PLATFORM_DIR = REPO / "src" / "platform"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list[dict] = []
    response_mode = "ok"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        if self.__class__.response_mode == "down":
            self.send_response(500)
            self.end_headers()
            return
        doc = json.loads(body)
        self.__class__.requests.append(doc)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"id": f"msg_{int(time.time() * 1000)}_{len(self.__class__.requests)}"}).encode()
        )

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server():
    _BridgeStub.requests = []
    _BridgeStub.response_mode = "ok"
    server = HTTPServer(("127.0.0.1", 0), _BridgeStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, _BridgeStub
    finally:
        server.shutdown()


@pytest.fixture
def env_dir(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


DEFAULT_MENU = [
    {
        "name": "Aloo Paratha",
        "price_usd": 4.0,
        "category": "side",
        "dietary_tags": ["veg"],
        "available": True,
        "notes": "",
        "serves": None,
    },
    {
        "name": "Chicken Biryani",
        "price_usd": 15.0,
        "category": "main",
        "dietary_tags": ["non-veg"],
        "available": True,
        "notes": "",
        "serves": None,
    },
    {
        "name": "Gulab Jamun",
        "price_usd": 3.0,
        "category": "dessert",
        "dietary_tags": ["veg"],
        "available": True,
        "notes": "",
        "serves": None,
    },
]


def _seed_menu(env_dir: Path, items=None) -> None:
    menu = {
        "version": 1,
        "updated_at": "2026-04-30T10:00:00-04:00",
        "updated_by": "manual",
        "source_image_id": "test",
        "items": items or DEFAULT_MENU,
    }
    (env_dir / "state" / "catering-menu.json").write_text(json.dumps(menu), encoding="utf-8")


def _seed_lead(env_dir: Path, lead_id: str = "L0014") -> None:
    lead = {
        "lead_id": lead_id,
        "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199",
        "customer_name": "Test Customer",
        "raw_inquiry": "Need catering ideas",
        "original_message_id": "msg_orig",
        "created_at": "2026-04-30T10:00:00-04:00",
        "updated_at": "2026-04-30T10:00:00-04:00",
        "extracted": {
            "headcount": 50,
            "event_date": "2026-06-15",
            "event_time": None,
            "menu_preferences": [],
            "off_menu_items": [],
            "dietary_restrictions": [],
            "delivery_or_pickup": "delivery",
            "budget_hint_usd": None,
            "notes": "",
        },
        "quote_text": "proposal placeholder",
        "quote_version": 0,
        "owner_approval_code": "#ABCDE",
        "customer_replied": False,
        "selected_items": [],
        "quote_total_usd": None,
        "customer_finalized_at": None,
        "last_finalize_message_id": None,
    }
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"schema_version": 1, "leads": [lead]}), encoding="utf-8"
    )


def _seed_prior_sent_set(env_dir: Path) -> None:
    prior = {
        "proposal_set_id": "CPS-L0014-000001",
        "lead_id": "L0014",
        "status": "SENT",
        "created_at": "2026-04-30T10:00:00-04:00",
        "sent_at": "2026-04-30T10:01:00-04:00",
        "outbound_message_id": "old_msg",
        "source_message_id": "msg_old",
        "request_text": "two ideas",
        "options": [
            {
                "option_id": "1",
                "style_key": "classic_family",
                "tier": "classic",
                "item_names": ["Aloo Paratha"],
            },
            {
                "option_id": "2",
                "style_key": "premium_mixed",
                "tier": "premium",
                "item_names": ["Gulab Jamun"],
            },
        ],
        "selected_option_id": None,
        "failure_reason": "",
    }
    (env_dir / "state" / "catering-proposals.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": 2, "sets": [prior]}),
        encoding="utf-8",
    )


def _options(count: int = 2):
    opts = [
        {
            "option_id": "1",
            "style_key": "balanced_mixed",
            "tier": "balanced",
            "item_names": ["Aloo Paratha", "Chicken Biryani"],
        },
        {
            "option_id": "2",
            "style_key": "premium_mixed",
            "tier": "premium",
            "item_names": ["Gulab Jamun"],
        },
        {
            "option_id": "3",
            "style_key": "classic_family",
            "tier": "classic",
            "item_names": ["Aloo Paratha"],
        },
    ]
    return opts[:count]


def _run_script(env_dir: Path, bridge_port: int, *, options=None, request_text="please send two options"):
    options_json = json.dumps(options if options is not None else _options())
    sys_argv = [
        "create-catering-proposal-options",
        "--lead-id",
        "L0014",
        "--customer-jid",
        "19045550199@s.whatsapp.net",
        "--source-message-id",
        "msg_src_001",
        "--request-text",
        request_text,
        "--options-json",
        options_json,
    ]
    wrapper = f"""
import io, json, pathlib, sys
sys.argv = {sys_argv!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("ccpo_test_loaded", {str(SCRIPT)!r}).load_module()
mod.PROPOSALS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-proposals.json')!r})
mod.PROPOSALS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-proposals.json.lock')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.MENU_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-menu.json')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.LOG_LOCK = pathlib.Path({str(env_dir / 'logs' / 'decisions.log.lock')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
notify_calls = []
def fake_notify_run(argv, **kwargs):
    notify_calls.append([str(part) for part in argv])
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""
    return Result()
mod.subprocess.run = fake_notify_run
buf = io.StringIO()
sys.stdout = buf
rc = -99
try:
    rc = mod.main()
except SystemExit as se:
    rc = se.code if isinstance(se.code, int) else -1
finally:
    sys.stdout = sys.__stdout__
print(json.dumps({{"rc": rc, "stdout": buf.getvalue(), "notify_calls": notify_calls}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        timeout=15,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    parsed = json.loads(lines[-1]) if lines else {"rc": -1, "stdout": ""}
    return result, parsed


def _read_store(env_dir: Path) -> dict:
    path = env_dir / "state" / "catering-proposals.json"
    if not path.exists():
        return {"sets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_audit(env_dir: Path) -> list[dict]:
    path = env_dir / "logs" / "decisions.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_script_for_env(env_dir: Path):
    sys.path.insert(0, str(PLATFORM_DIR))
    from importlib.machinery import SourceFileLoader

    mod = SourceFileLoader("ccpo_helper_loaded", str(SCRIPT)).load_module()
    mod.PROPOSALS_PATH = env_dir / "state" / "catering-proposals.json"
    mod.PROPOSALS_LOCK = env_dir / "state" / "catering-proposals.json.lock"
    return mod


def _proposal_set(proposal_set_id: str, status: str, *, outbound_message_id: str = "") -> dict:
    sent_at = "2026-04-30T10:01:00-04:00" if status == "SENT" else None
    return {
        "proposal_set_id": proposal_set_id,
        "lead_id": "L0014",
        "status": status,
        "created_at": "2026-04-30T10:00:00-04:00",
        "sent_at": sent_at,
        "outbound_message_id": outbound_message_id,
        "source_message_id": f"msg_{proposal_set_id[-6:]}",
        "request_text": "two ideas",
        "options": [
            {
                "option_id": "1",
                "style_key": "classic_family",
                "tier": "classic",
                "item_names": ["Aloo Paratha"],
            },
            {
                "option_id": "2",
                "style_key": "premium_mixed",
                "tier": "premium",
                "item_names": ["Gulab Jamun"],
            },
        ],
        "selected_option_id": None,
        "failure_reason": "",
    }


def test_generates_sent_proposal_set_and_bridge_message(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 0, result.stderr
    store = _read_store(env_dir)
    sent = [s for s in store["sets"] if s["status"] == "SENT"]
    assert len(sent) == 1
    assert sent[0]["proposal_set_id"] == "CPS-L0014-000001"
    assert sent[0]["outbound_message_id"]
    assert len(stub.requests) == 1
    body = stub.requests[0]["message"]
    assert body.startswith("⚕ *Catering Agent*")
    assert "$" not in body
    assert "price" not in body.lower()
    assert "Option 1: Balanced Veg and Non-Veg Menu" in body
    assert "Option 2: Premium Celebration Menu" in body
    generated = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposals_generated"]
    assert generated[0]["proposal_set_id"] == "CPS-L0014-000001"


def test_unknown_item_fails_closed_without_bridge_send(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    bad_options = [
        {
            "option_id": "1",
            "style_key": "balanced_mixed",
            "tier": "balanced",
            "item_names": ["Aloo Paratha", "Ghost Curry"],
        },
        {
            "option_id": "2",
            "style_key": "premium_mixed",
            "tier": "premium",
            "item_names": ["Gulab Jamun"],
        },
    ]

    result, parsed = _run_script(env_dir, port, options=bad_options)

    assert parsed["rc"] == 2, result.stderr
    assert not [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"]
    assert stub.requests == []
    failed = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposal_generation_failed"]
    assert failed[0]["reason"] == "unknown_menu_item"
    assert parsed["notify_calls"]
    notify_call = parsed["notify_calls"][0]
    assert "--title" in notify_call
    assert notify_call[notify_call.index("--title") + 1] == "Catering proposal generation failed"
    assert "unknown_menu_item" in notify_call[-1]


def test_bridge_failure_marks_latest_set_send_failed(bridge_server, env_dir):
    port, stub = bridge_server
    stub.response_mode = "down"
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 6, result.stderr
    sets = _read_store(env_dir)["sets"]
    assert sets[-1]["status"] == "SEND_FAILED"
    assert sets[-1]["outbound_message_id"] == ""
    failed = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposal_generation_failed"]
    assert failed[0]["reason"] == "bridge_unreachable"
    assert parsed["notify_calls"]
    assert "bridge_unreachable" in parsed["notify_calls"][0][-1]


def test_missing_menu_alerts_owner_without_bridge_send(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 4, result.stderr
    assert stub.requests == []
    failed = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposal_generation_failed"]
    assert failed[0]["reason"] == "menu_missing"
    assert parsed["notify_calls"]
    assert "menu_missing" in parsed["notify_calls"][0][-1]


def test_success_supersedes_prior_sent_only_after_success(bridge_server, env_dir):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    _seed_prior_sent_set(env_dir)

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 0, result.stderr
    by_id = {row["proposal_set_id"]: row for row in _read_store(env_dir)["sets"]}
    assert by_id["CPS-L0014-000001"]["status"] == "SUPERSEDED"
    assert by_id["CPS-L0014-000002"]["status"] == "SENT"


def test_slow_older_send_does_not_supersede_newer_sent_proposal(env_dir):
    store = {
        "schema_version": 1,
        "next_sequence": 3,
        "sets": [
            _proposal_set("CPS-L0014-000001", "DRAFT"),
            _proposal_set("CPS-L0014-000002", "SENT", outbound_message_id="fast_msg"),
        ],
    }
    (env_dir / "state" / "catering-proposals.json").write_text(json.dumps(store), encoding="utf-8")
    mod = _load_script_for_env(env_dir)

    mod._mark_sent_and_supersede("L0014", "CPS-L0014-000001", "slow_msg")

    by_id = {row["proposal_set_id"]: row for row in _read_store(env_dir)["sets"]}
    assert by_id["CPS-L0014-000002"]["status"] == "SENT"
    assert by_id["CPS-L0014-000002"]["outbound_message_id"] == "fast_msg"
    assert by_id["CPS-L0014-000001"]["status"] == "SUPERSEDED"
    assert by_id["CPS-L0014-000001"]["outbound_message_id"] == "slow_msg"
    assert [row["proposal_set_id"] for row in by_id.values() if row["status"] == "SENT"] == [
        "CPS-L0014-000002"
    ]


def test_option_count_cap_rejects_three_without_explicit_request(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port, options=_options(3), request_text="send options")

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == []
    failed = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposal_generation_failed"]
    assert failed[0]["reason"] == "invalid_options"
    assert parsed["notify_calls"]


def test_option_count_requires_exact_default_two(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port, options=_options(1), request_text="send options")

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == []
    failed = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposal_generation_failed"]
    assert failed[0]["reason"] == "invalid_options"
    assert "expected exactly 2" in failed[0]["detail"]
    assert parsed["notify_calls"]


def test_schema_level_invalid_options_alert_owner(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    duplicate_options = [
        {
            "option_id": "1",
            "style_key": "balanced_mixed",
            "tier": "balanced",
            "item_names": ["Aloo Paratha"],
        },
        {
            "option_id": "1",
            "style_key": "premium_mixed",
            "tier": "premium",
            "item_names": ["Gulab Jamun"],
        },
    ]

    result, parsed = _run_script(env_dir, port, options=duplicate_options)

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == []
    failed = [row for row in _read_audit(env_dir) if row["type"] == "catering_proposal_generation_failed"]
    assert failed[0]["reason"] == "invalid_options"
    assert "option_id values must be unique" in failed[0]["detail"]
    assert parsed["notify_calls"]
    assert "invalid_options" in parsed["notify_calls"][0][-1]


def test_option_count_cap_allows_three_when_requested(bridge_server, env_dir):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port, options=_options(3), request_text="please send three options")

    assert parsed["rc"] == 0, result.stderr
    sent = [row for row in _read_store(env_dir)["sets"] if row["status"] == "SENT"]
    assert sent[0]["proposal_set_id"] == "CPS-L0014-000001"
    assert len(sent[0]["options"]) == 3


def test_no_price_regex_rejects_forbidden_customer_text(env_dir):
    wrapper = f"""
import json, pathlib, sys
sys.path.insert(0, {str(PLATFORM_DIR)!r})
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("ccpo_helper_loaded", {str(SCRIPT)!r}).load_module()
try:
    mod._assert_no_forbidden_customer_text("Option 1: pay deposit to confirm booking")
except ValueError as exc:
    print(json.dumps({{"raised": True, "message": str(exc)}}))
else:
    print(json.dumps({{"raised": False}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True,
        text=True,
        timeout=15,
    )
    parsed = json.loads(result.stdout.splitlines()[-1])
    assert parsed["raised"] is True
