"""Task 2 - create-catering-proposal-options behavior tests."""
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
        # P17b: 200 with no id is safe_io's `send_uncertain` — the bridge ACCEPTED
        # the menu, so it most likely reached the customer.
        if self.__class__.response_mode == "empty_id":
            self.wfile.write(json.dumps({"accepted": True}).encode())
            return
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


def _seed_lead(env_dir: Path, lead_id: str = "L0014", dietary=None,
               raw_inquiry: str = "Need catering ideas") -> None:
    lead = {
        "lead_id": lead_id,
        "status": "AWAITING_OWNER_APPROVAL",
        "customer_phone": "+19045550199",
        "customer_name": "Test Customer",
        "raw_inquiry": raw_inquiry,
        "original_message_id": "msg_orig",
        "created_at": "2026-04-30T10:00:00-04:00",
        "updated_at": "2026-04-30T10:00:00-04:00",
        "extracted": {
            "headcount": 50,
            "event_date": "2026-06-15",
            "event_time": None,
            "menu_preferences": [],
            "off_menu_items": [],
            "dietary_restrictions": dietary or [],
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


def _run_script(
    env_dir: Path,
    bridge_port: int,
    *,
    options=None,
    request_text="please send two options",
    auto_generate: bool = False,
    notify_rc: int | None = 0,
):
    """notify_rc drives the shift-agent-notify-owner stub's exit status: 0 for a
    page that landed, non-zero for one the notifier rejected (exit 6 = every
    channel failed), None to make the call raise. The unconfirmed row's
    `owner_paged` has to follow it."""
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
    ]
    if auto_generate:
        sys_argv.append("--auto-generate-from-menu")
    else:
        options_json = json.dumps(options if options is not None else _options())
        sys_argv.extend(["--options-json", options_json])
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
notify_rc = {notify_rc!r}
def fake_notify_run(argv, **kwargs):
    notify_calls.append([str(part) for part in argv])
    if notify_rc is None:
        raise OSError("notify-owner unavailable")
    class Result:
        stdout = ""
        stderr = ""
    result = Result()
    result.returncode = notify_rc
    return result
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
        # send-path-test-harness: canonical safe_io.BRIDGE_URL -> stub (via env)
        # + opt past the pytest guard. The wrapper loads the real
        # create-catering-proposal-options script (allowlisted caller); stub
        # port (not :3000) keeps the live-bridge tripwire dormant.
        env={**os.environ,
             "HERMES_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}/send",
             "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1"},
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


def test_auto_generates_two_grounded_options_from_menu(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(
        env_dir,
        port,
        request_text="Please send two proposal menus: one balanced mixed veg/non-veg option and one premium option.",
        auto_generate=True,
    )

    assert parsed["rc"] == 0, result.stderr
    store = _read_store(env_dir)
    sent = [s for s in store["sets"] if s["status"] == "SENT"]
    assert len(sent) == 1
    assert len(sent[0]["options"]) == 2
    item_names = {name for option in sent[0]["options"] for name in option["item_names"]}
    assert item_names <= {item["name"] for item in DEFAULT_MENU}
    assert "Chicken Biryani" in item_names
    assert "Aloo Paratha" in item_names
    body = stub.requests[0]["message"]
    assert "Option 1: Balanced Veg and Non-Veg Menu" in body
    assert "Option 2: Premium Celebration Menu" in body
    assert "$" not in body
    assert "price" not in body.lower()


def test_auto_generate_sample_menus_never_invents_off_menu_western_items(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(
        env_dir,
        port,
        request_text="Can you create two sample menus mix n match.",
        auto_generate=True,
    )

    assert parsed["rc"] == 0, result.stderr
    sent = [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"]
    assert len(sent) == 1
    item_names = {name for option in sent[0]["options"] for name in option["item_names"]}
    assert item_names <= {item["name"] for item in DEFAULT_MENU}
    body = stub.requests[0]["message"]
    for invented in [
        "Stuffed Mushrooms",
        "Spring Rolls",
        "Grilled Salmon",
        "Vegetarian Tacos",
        "Beef",
        "Panna Cotta",
    ]:
        assert invented not in body


def test_auto_generation_allows_three_only_when_requested(bridge_server, env_dir):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(
        env_dir,
        port,
        request_text="Please send 3 proposal menus.",
        auto_generate=True,
    )

    assert parsed["rc"] == 0, result.stderr
    sent = [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"]
    assert len(sent[0]["options"]) == 3


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


# ── Turn-arbitration 2026-07-26: menu-quality section balance ────────────────

# A wedding-scale menu: appetizers (incl. the idli/dosa trap items), mains, sides
# (rice + bread), desserts, across BOTH veg and non-veg.
WEDDING_MENU = [
    {"name": "Idli", "price_usd": 2.0, "category": "appetizer",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Masala Dosa", "price_usd": 4.0, "category": "appetizer",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Vegetable Samosa", "price_usd": 3.0, "category": "appetizer",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Chicken 65", "price_usd": 9.0, "category": "appetizer",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Paneer Butter Masala", "price_usd": 11.0, "category": "main",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Vegetable Biryani", "price_usd": 10.0, "category": "main",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Chicken Biryani", "price_usd": 15.0, "category": "main",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Goat Curry", "price_usd": 16.0, "category": "main",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Garlic Naan", "price_usd": 3.0, "category": "side",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Jeera Rice", "price_usd": 4.0, "category": "side",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Gulab Jamun", "price_usd": 3.0, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Gajar Halwa", "price_usd": 4.0, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
]

_NON_VEG_NAMES = {"Chicken 65", "Chicken Biryani", "Goat Curry"}


def _cat_of(name: str) -> str:
    return next(item["category"] for item in WEDDING_MENU if item["name"] == name)


def test_auto_generate_mixed_wedding_menu_is_section_balanced(bridge_server, env_dir):
    """A mixed veg/non-veg wedding proposal MUST be balanced — each option spans
    multiple sections (appetizer + main + dessert, plus a side) AND includes BOTH a
    vegetarian and a non-vegetarian item — NOT idli/dosa-dominated."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["veg", "non-veg"],
               raw_inquiry="Wedding for 180 guests, 90 non-vegetarian and 90 vegetarian")
    _seed_menu(env_dir, WEDDING_MENU)

    result, parsed = _run_script(
        env_dir, port,
        request_text="Please send two sample menus for a mixed veg and non-veg wedding.",
        auto_generate=True,
    )
    assert parsed["rc"] == 0, result.stderr
    sent = [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"]
    assert len(sent) == 1 and len(sent[0]["options"]) == 2
    for option in sent[0]["options"]:
        names = option["item_names"]
        cats = {_cat_of(n) for n in names}
        has_non_veg = any(n in _NON_VEG_NAMES for n in names)
        has_veg = any(n not in _NON_VEG_NAMES for n in names)
        assert has_veg and has_non_veg, f"option {option['option_id']} not veg/non-veg balanced: {names}"
        assert len(cats) >= 3, f"option {option['option_id']} spans too few sections: {sorted(cats)}"
        assert "main" in cats, f"option {option['option_id']} has no main course: {names}"
        assert cats != {"appetizer"}, "must not be idli/dosa (appetizer) dominated"


def test_mixed_lead_degenerate_options_fail_closed_no_send(bridge_server, env_dir):
    """A mixed-diet event with owner-supplied SINGLE-DIET options fails closed (no
    incoherent send) rather than delivering a veg-only menu when the menu has non-veg."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["veg", "non-veg"])
    _seed_menu(env_dir, WEDDING_MENU)
    veg_only = [
        {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
         "item_names": ["Idli", "Masala Dosa"]},           # appetizer-only, veg-only
        {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
         "item_names": ["Gulab Jamun", "Gajar Halwa"]},    # dessert-only, veg-only
    ]

    result, parsed = _run_script(env_dir, port, options=veg_only)

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == [], "no incoherent menu is sent"
    failed = [row for row in _read_audit(env_dir)
              if row["type"] == "catering_proposal_generation_failed"]
    assert failed and failed[0]["reason"] == "insufficient_section_balance"
    assert parsed["notify_calls"], "owner is alerted on fail-closed"


def test_unknown_diet_lead_skips_section_balance_guard(bridge_server, env_dir):
    """The guard is a no-op for a lead that states no diet — a single-diet menu is a
    legitimate delivery when nothing is known, and the owner reviews before send."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=[])  # no diet signal at all
    _seed_menu(env_dir, WEDDING_MENU)
    veg_only = [
        {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
         "item_names": ["Idli", "Gulab Jamun"]},
        {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
         "item_names": ["Masala Dosa", "Gajar Halwa"]},
    ]

    result, parsed = _run_script(env_dir, port, options=veg_only)

    assert parsed["rc"] == 0, result.stderr
    assert len(stub.requests) == 1, "single-diet menu delivered for an unstated-diet event"


def test_unknown_diet_lead_auto_generate_keeps_both_diets(bridge_server, env_dir):
    """An unstated diet must NOT be guessed either way — generation keeps the full
    menu pool, so the non-veg items still appear."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=[])
    _seed_menu(env_dir, WEDDING_MENU)

    result, parsed = _run_script(env_dir, port, auto_generate=True)

    assert parsed["rc"] == 0, result.stderr
    sent = [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"]
    assert len(sent) == 1
    for option in sent[0]["options"]:
        assert any(n in _NON_VEG_NAMES for n in option["item_names"]), (
            f"option {option['option_id']} dropped non-veg for an unstated-diet lead")


# ── Diet-aware generation: a stated all-veg / Jain / temple event ─────────────

def test_veg_only_lead_auto_generate_excludes_all_non_veg(bridge_server, env_dir):
    """A stated all-vegetarian event must receive ZERO non-veg items in EVERY option,
    even though the menu offers non-veg — and the options must stay coherent
    (multi-section, with a main) after the exclusion."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["veg"],
               raw_inquiry="Temple event for 120 guests, pure vegetarian only")
    _seed_menu(env_dir, WEDDING_MENU)

    result, parsed = _run_script(
        env_dir, port,
        request_text="Please send two sample menus for our temple event.",
        auto_generate=True,
    )

    assert parsed["rc"] == 0, result.stderr
    sent = [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"]
    assert len(sent) == 1 and len(sent[0]["options"]) == 2
    for option in sent[0]["options"]:
        names = option["item_names"]
        non_veg = [n for n in names if n in _NON_VEG_NAMES]
        assert not non_veg, f"option {option['option_id']} forced non-veg {non_veg}: {names}"
        cats = {_cat_of(n) for n in names}
        assert len(cats) >= 3, f"option {option['option_id']} spans too few sections: {sorted(cats)}"
        assert "main" in cats, f"option {option['option_id']} has no main course: {names}"
    body = stub.requests[0]["message"]
    for non_veg_name in _NON_VEG_NAMES:
        assert non_veg_name not in body, f"{non_veg_name} reached a vegetarian-only customer"


def test_veg_only_lead_non_veg_options_fail_closed_no_send(bridge_server, env_dir):
    """Owner-supplied options bypass the generation-side pool filter, so the guard is
    the backstop: a non-veg item smuggled into a vegetarian-only lead's options fails
    closed (owner alerted, nothing sent) rather than reaching the customer."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["jain"], raw_inquiry="Jain wedding, no onion no garlic")
    _seed_menu(env_dir, WEDDING_MENU)
    smuggled = [
        {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
         "item_names": ["Idli", "Paneer Butter Masala", "Gulab Jamun"]},
        {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
         "item_names": ["Masala Dosa", "Chicken Biryani", "Gajar Halwa"]},
    ]

    result, parsed = _run_script(env_dir, port, options=smuggled)

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == [], "no non-veg menu is sent to a vegetarian-only event"
    failed = [row for row in _read_audit(env_dir)
              if row["type"] == "catering_proposal_generation_failed"]
    assert failed and failed[0]["reason"] == "insufficient_section_balance"
    assert "Chicken Biryani" in failed[0]["detail"]
    assert parsed["notify_calls"], "owner is alerted on fail-closed"


def _fake_lead(dietary=None, raw_inquiry: str = "", notes: str = ""):
    """Minimal stand-in for the diet classifier, which reads only these three fields."""
    from types import SimpleNamespace

    return SimpleNamespace(
        raw_inquiry=raw_inquiry,
        extracted=SimpleNamespace(dietary_restrictions=dietary or [], notes=notes),
    )


@pytest.mark.parametrize(
    "dietary,raw_inquiry,expected",
    [
        (["veg"], "", "veg_only"),
        (["vegetarian"], "", "veg_only"),
        (["jain"], "", "veg_only"),
        (["vegan"], "", "veg_only"),
        (["pure veg"], "", "veg_only"),
        ([], "Temple lunch, pure veg only please", "veg_only"),
        ([], "Jain family gathering for 40", "veg_only"),
        ([], "Office lunch, no meat", "veg_only"),
        ([], "Meat-free menu for the whole event", "veg_only"),
        (["halal"], "", "non_veg_only"),
        (["non-veg"], "", "non_veg_only"),
        ([], "Non-veg biryani party for 60", "non_veg_only"),
        (["veg", "non-veg"], "", "mixed"),
        ([], "Wedding: 90 veg and 90 non-veg guests", "mixed"),
        ([], "Need catering ideas", "unknown"),
        ([], "", "unknown"),
        # ── the asymmetry that silently dropped meat ────────────────────────
        # `veg` was unbounded while the meat side knew only the literal
        # "non-veg", so each of these read veg_only and the chicken/goat the
        # customer asked for was excluded from generation — and a recompose of
        # their OWN Goat Curry was refused.
        ([], "half veg half chicken", "mixed"),
        ([], "50 veg meals and 50 chicken meals", "mixed"),
        ([], "We want Veg Biryani and Chicken Biryani", "mixed"),
        ([], "Mutton curry and veg starters", "mixed"),
        # Meat named, no vegetarian REQUIREMENT stated: "vegetables" describes a
        # dish, not a diet, so it must not read as one.
        ([], "plenty of vegetables and chicken tikka", "non_veg_only"),
        ([], "lots of vegetables please", "unknown"),
        ([], "veggie platter for the table", "unknown"),
        # "eggless" is not an egg.
        ([], "eggless cake for the table", "unknown"),
        # ── v3: a NEGATED flesh word is a vegetarian requirement ────────────
        # The meat detector matched flesh words unconditionally while the
        # negation strip knew only three literals (no meat / meat-free /
        # meatless). So "no chicken" read as a MEAT signal, the lead came out
        # `mixed`, and under mixed the guard refuses all-veg options — i.e. it
        # REQUIRED meat at a stated-vegetarian event. That is the original P0
        # in its sharpest form: every one of these was served chicken + goat.
        ([], "all vegetarian, no chicken", "veg_only"),
        ([], "pure veg, no eggs please", "veg_only"),
        ([], "strictly veg, without any meat", "veg_only"),
        ([], "vegetarian only, absolutely no mutton", "veg_only"),
        ([], "temple event, no fish or meat", "veg_only"),
        ([], "vegan, no dairy or eggs", "veg_only"),
        # No vegetarian word at all — the negation alone carries the meaning.
        ([], "no eggs please", "veg_only"),
        # COUNTER-CASE: the negation must strip ONLY what it negates. "chicken"
        # is negated, "lamb" is not, so the event genuinely serves both.
        # Without this, a broader strip would read the whole clause as
        # vegetarian and drop the lamb the customer asked for.
        ([], "no chicken but lamb is fine", "mixed"),
    ],
)
def test_lead_diet_profile_classification(env_dir, dietary, raw_inquiry, expected):
    mod = _load_script_for_env(env_dir)

    assert mod._lead_diet_profile(_fake_lead(dietary, raw_inquiry)) == expected


# ── PR-D mix-and-match recomposition (deterministic combine of SENT sections) ──

_RECOMPOSE_MENU = [
    {"name": "Samosa", "price_usd": 3.0, "category": "appetizer",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Chicken Biryani", "price_usd": 15.0, "category": "main",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Goat Curry", "price_usd": 16.0, "category": "main",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Gulab Jamun", "price_usd": 3.0, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
]


def _seed_recompose_sent_set(env_dir: Path) -> None:
    """SENT set: option 1 = appetizer + main; option 2 = main + dessert (no appetizer)."""
    sent = {
        "proposal_set_id": "CPS-L0014-000001", "lead_id": "L0014", "status": "SENT",
        "created_at": "2026-04-30T10:00:00-04:00", "sent_at": "2026-04-30T10:01:00-04:00",
        "outbound_message_id": "old_msg", "source_message_id": "msg_old",
        "request_text": "two ideas",
        "options": [
            {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
             "item_names": ["Samosa", "Chicken Biryani"]},
            {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
             "item_names": ["Goat Curry", "Gulab Jamun"]},
        ],
        "selected_option_id": None, "failure_reason": "",
    }
    (env_dir / "state" / "catering-proposals.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": 2, "sets": [sent]}), encoding="utf-8")


def _run_recompose(env_dir: Path, bridge_port: int, request_text: str):
    sys_argv = [
        "create-catering-proposal-options", "--lead-id", "L0014",
        "--customer-jid", "19045550199@s.whatsapp.net",
        "--source-message-id", "msg_src_recompose",
        "--request-text", request_text, "--recompose-from-sent",
    ]
    wrapper = f"""
import io, json, pathlib, sys
sys.argv = {sys_argv!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("ccpo_recompose_loaded", {str(SCRIPT)!r}).load_module()
mod.PROPOSALS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-proposals.json')!r})
mod.PROPOSALS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-proposals.json.lock')!r})
mod.LEADS_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json')!r})
mod.LEADS_LOCK = pathlib.Path({str(env_dir / 'state' / 'catering-leads.json.lock')!r})
mod.MENU_PATH = pathlib.Path({str(env_dir / 'state' / 'catering-menu.json')!r})
mod.LOG_PATH = pathlib.Path({str(env_dir / 'logs' / 'decisions.log')!r})
mod.LOG_LOCK = pathlib.Path({str(env_dir / 'logs' / 'decisions.log.lock')!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
buf = io.StringIO(); sys.stdout = buf; rc = -99
try:
    rc = mod.main()
except SystemExit as se:
    rc = se.code if isinstance(se.code, int) else -1
finally:
    sys.stdout = sys.__stdout__
print(json.dumps({{"rc": rc, "stdout": buf.getvalue()}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", wrapper], capture_output=True, text=True, timeout=15,
        env={**os.environ, "HERMES_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}/send",
             "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1"})
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    return result, (json.loads(lines[-1]) if lines else {"rc": -1, "stdout": ""})


def test_recompose_clean_merge_sends_exact_sections(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir)
    _seed_recompose_sent_set(env_dir)
    result, parsed = _run_recompose(env_dir, port, "option 1 starters with the option 2 mains")
    assert parsed["rc"] == 0, result.stderr
    out = json.loads(parsed["stdout"].splitlines()[-1])
    assert out["mode"] == "recomposed" and out["sent"] is True
    body = stub.requests[-1]["message"]
    # Exactly the requested sections: starters from opt1 (Samosa), mains from opt2 (Goat Curry).
    assert "Appetizer:" in body and "- Samosa" in body
    assert "Main:" in body and "- Goat Curry" in body
    assert "Dessert:" not in body and "Chicken Biryani" not in body  # opt1 main NOT pulled
    # No proposal SET created (recomposition is a single fulfilled menu).
    store = _read_store(env_dir)
    assert [s["proposal_set_id"] for s in store["sets"]] == ["CPS-L0014-000001"]
    audit = _read_audit(env_dir)
    assert any(e["type"] == "catering_recomposed_menu_sent" and e["section_count"] == 2 for e in audit)


def test_recompose_missing_section_clarifies_no_merge(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir)
    _seed_recompose_sent_set(env_dir)
    # Option 2 has no appetizer → clarify, never a best-guess merge.
    result, parsed = _run_recompose(env_dir, port, "option 2 starters with option 1 mains")
    assert parsed["rc"] == 0, result.stderr
    out = json.loads(parsed["stdout"].splitlines()[-1])
    assert out["mode"] == "clarify" and out["reason"] == "missing_section"
    body = stub.requests[-1]["message"]
    assert "starters" in body.lower()
    audit = _read_audit(env_dir)
    assert any(e["type"] == "catering_recompose_clarify_sent" and e["reason"] == "missing_section"
               for e in audit)
    # No recomposed-menu audit, no proposal set mutation.
    assert not any(e["type"] == "catering_recomposed_menu_sent" for e in audit)


def test_recompose_unknown_option_clarifies(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir)
    _seed_recompose_sent_set(env_dir)
    # Only options 1 and 2 were sent — "option 3" cannot resolve.
    result, parsed = _run_recompose(env_dir, port, "can we mix in option 3's desserts with option 1 starters?")
    assert parsed["rc"] == 0, result.stderr
    out = json.loads(parsed["stdout"].splitlines()[-1])
    assert out["mode"] == "clarify" and out["reason"] == "unknown_option"
    assert "options 1 and 2" in stub.requests[-1]["message"]


def test_recompose_veg_only_lead_refuses_non_veg_pull_forward(bridge_server, env_dir):
    """Recompose pulls items VERBATIM from options sent earlier, which may pre-date
    the lead's stated diet. A veg_only lead must never receive a non-veg item this
    way: the merge fails closed (owner alerted, nothing sent) rather than quietly
    dropping a dish the customer explicitly named."""
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir, dietary=["jain"],
               raw_inquiry="Actually please make the whole event pure vegetarian")
    _seed_recompose_sent_set(env_dir)

    # Option 2's mains are Goat Curry — non-veg, and sent before the diet was stated.
    result, parsed = _run_recompose(env_dir, port, "option 1 starters with the option 2 mains")

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == [], "no non-veg menu is sent to a vegetarian-only event"
    audit = _read_audit(env_dir)
    failed = [e for e in audit if e["type"] == "catering_proposal_generation_failed"]
    assert failed and failed[0]["reason"] == "insufficient_section_balance"
    assert "Goat Curry" in failed[0]["detail"]
    assert not any(e["type"] == "catering_recomposed_menu_sent" for e in audit)


def test_recompose_veg_only_lead_sends_all_veg_combination(bridge_server, env_dir):
    """The refusal is item-specific, not a blanket block: a veg_only lead's
    all-vegetarian combination still goes out."""
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir, dietary=["veg"], raw_inquiry="Temple lunch, pure vegetarian")
    veg_sent = {
        "proposal_set_id": "CPS-L0014-000001", "lead_id": "L0014", "status": "SENT",
        "created_at": "2026-04-30T10:00:00-04:00", "sent_at": "2026-04-30T10:01:00-04:00",
        "outbound_message_id": "old_msg", "source_message_id": "msg_old",
        "request_text": "two ideas",
        "options": [
            {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
             "item_names": ["Samosa", "Gulab Jamun"]},
            {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
             "item_names": ["Samosa", "Gulab Jamun"]},
        ],
        "selected_option_id": None, "failure_reason": "",
    }
    (env_dir / "state" / "catering-proposals.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": 2, "sets": [veg_sent]}), encoding="utf-8")

    result, parsed = _run_recompose(env_dir, port, "option 1 starters with the option 2 desserts")

    assert parsed["rc"] == 0, result.stderr
    body = stub.requests[-1]["message"]
    assert "- Samosa" in body and "- Gulab Jamun" in body
    assert any(e["type"] == "catering_recomposed_menu_sent" for e in _read_audit(env_dir))


def test_recompose_mixed_lead_keeps_non_veg_combination(bridge_server, env_dir):
    """A mixed-diet lead's recompose is unchanged — the non-veg section still ships,
    and the guard adds no section/main requirement to a mix-and-match merge (a
    customer may legitimately ask for just starters + mains)."""
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir, dietary=["veg", "non-veg"],
               raw_inquiry="Wedding, 90 veg and 90 non-veg")
    _seed_recompose_sent_set(env_dir)

    result, parsed = _run_recompose(env_dir, port, "option 1 starters with the option 2 mains")

    assert parsed["rc"] == 0, result.stderr
    body = stub.requests[-1]["message"]
    assert "- Samosa" in body and "- Goat Curry" in body
    assert any(e["type"] == "catering_recomposed_menu_sent" and e["section_count"] == 2
               for e in _read_audit(env_dir))


def test_recompose_price_free_on_combined_menu(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir)
    _seed_recompose_sent_set(env_dir)
    result, parsed = _run_recompose(env_dir, port, "option 1 starters with option 2 mains")
    assert parsed["rc"] == 0
    body = stub.requests[-1]["message"]
    import re
    assert not re.search(r"\$\s*\d|\bprice|\bcost|\bdeposit|\bpayment", body, re.I)


def test_notes_do_not_override_the_meat_the_inquiry_asked_for(env_dir):
    """Notes contamination. A cooking instruction in `notes` ("use veg stock") used
    to outvote the chicken in the inquiry and classify the lead veg_only, dropping
    the meat. Both signals present => mixed."""
    mod = _load_script_for_env(env_dir)

    lead = _fake_lead([], "Chicken biryani for 80 guests", notes="use veg stock")

    assert mod._lead_diet_profile(lead) == "mixed"


def test_mixed_text_lead_keeps_the_meat_it_asked_for(bridge_server, env_dir):
    """End-to-end for the regression: "half veg half chicken" states BOTH diets, so
    generation must keep the non-veg items rather than filter them out."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=[], raw_inquiry="half veg half chicken, 100 guests")
    _seed_menu(env_dir, WEDDING_MENU)

    result, parsed = _run_script(env_dir, port, auto_generate=True)

    assert parsed["rc"] == 0, result.stderr
    sent = [s for s in _read_store(env_dir)["sets"] if s["status"] == "SENT"][0]
    for option in sent["options"]:
        assert any(n in _NON_VEG_NAMES for n in option["item_names"]), (
            f"option {option['option_id']} dropped the chicken the customer asked "
            f"for: {option['item_names']}")


def test_mixed_text_lead_recompose_is_not_refused(bridge_server, env_dir):
    """The cruellest form of the regression: the customer asks to keep the Goat
    Curry from an option we already sent them, and the diet guard REFUSES it as a
    non-veg item in a "vegetarian-only" event."""
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir, dietary=[],
               raw_inquiry="half veg half chicken for the reception")
    _seed_recompose_sent_set(env_dir)

    result, parsed = _run_recompose(env_dir, port, "option 1 starters with the option 2 mains")

    assert parsed["rc"] == 0, result.stderr
    assert "- Goat Curry" in stub.requests[-1]["message"]


# ── veg_only options must still be a MEAL, not just meat-free ────────────────

def test_veg_only_lead_dessert_only_options_fail_closed(bridge_server, env_dir):
    """Meat-free is not the same as complete. A dessert-only "menu" contains no
    non-veg item at all, so the diet check passes it — the section coverage check
    is what stops it reaching a temple event."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["jain"], raw_inquiry="Jain wedding for 200")
    _seed_menu(env_dir, WEDDING_MENU)
    dessert_only = [
        {"option_id": "1", "style_key": "balanced_mixed", "tier": "balanced",
         "item_names": ["Gulab Jamun"]},
        {"option_id": "2", "style_key": "premium_mixed", "tier": "premium",
         "item_names": ["Gajar Halwa"]},
    ]

    result, parsed = _run_script(env_dir, port, options=dessert_only)

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == [], "a plate of sweets is not a catering menu"
    failed = [row for row in _read_audit(env_dir)
              if row["type"] == "catering_proposal_generation_failed"]
    assert failed and failed[0]["reason"] == "insufficient_section_balance"


_ONLY_MAIN_IS_NON_VEG_MENU = [
    {"name": "Vegetable Samosa", "price_usd": 3.0, "category": "appetizer",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Chicken Biryani", "price_usd": 15.0, "category": "main",
     "dietary_tags": ["non-veg"], "available": True, "notes": "", "serves": None},
    {"name": "Jeera Rice", "price_usd": 4.0, "category": "side",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Gulab Jamun", "price_usd": 3.0, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
]


def test_veg_only_lead_fails_closed_when_every_main_is_non_veg(bridge_server, env_dir):
    """The menu HAS mains; none of them can be served to this event. Sending a
    side-and-dessert spread to a temple booking is worse than telling the owner
    their menu has no vegetarian main course."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["veg"], raw_inquiry="Temple lunch, pure vegetarian")
    _seed_menu(env_dir, _ONLY_MAIN_IS_NON_VEG_MENU)

    result, parsed = _run_script(env_dir, port, auto_generate=True)

    assert parsed["rc"] == 2, result.stderr
    assert stub.requests == [], "no main-less menu is sent"
    failed = [row for row in _read_audit(env_dir)
              if row["type"] == "catering_proposal_generation_failed"]
    assert failed and failed[0]["reason"] == "insufficient_section_balance"
    assert "main" in failed[0]["detail"]


_SMALL_ALL_VEG_MENU = [
    {"name": "Vegetable Samosa", "price_usd": 3.0, "category": "appetizer",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
    {"name": "Gulab Jamun", "price_usd": 3.0, "category": "dessert",
     "dietary_tags": ["veg"], "available": True, "notes": "", "serves": None},
]


def test_veg_only_lead_small_all_veg_menu_still_sends(bridge_server, env_dir):
    """The coverage check is menu-relative: a two-section all-veg menu must not be
    rejected for lacking variety it never offered, and it has no mains to miss."""
    port, stub = bridge_server
    _seed_lead(env_dir, dietary=["jain"], raw_inquiry="Small jain lunch")
    _seed_menu(env_dir, _SMALL_ALL_VEG_MENU)

    result, parsed = _run_script(env_dir, port, auto_generate=True)

    assert parsed["rc"] == 0, result.stderr
    assert len(stub.requests) == 1


# ── P17b: the proposal set's delivery status is recorded ────────────────────
# All three send sites already PAGE the owner via _fail_generation. What was
# missing was the STATUS: the failure row hardcodes reason="bridge_unreachable"
# (a schema Literal), so a send_uncertain — bridge accepted, menu most likely
# delivered — was recorded as a definite non-delivery.

def _unconfirmed(env_dir):
    return [r for r in _read_audit(env_dir)
            if r["type"] == "catering_customer_send_unconfirmed"]


def test_failed_proposal_send_records_the_real_status(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    stub.response_mode = "down"

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 6, result.stderr
    rows = _unconfirmed(env_dir)
    assert len(rows) == 1
    assert rows[0]["send_kind"] == "proposal_options"
    assert rows[0]["script"] == "create-catering-proposal-options"
    assert rows[0]["delivery_certainty"] == "failed"
    assert rows[0]["send_status"] == "http_error"
    assert rows[0]["owner_paged"] is True, (
        "_fail_generation already pages; the row reflects that page rather than "
        "adding a second one for the same event")


def test_uncertain_proposal_send_is_not_recorded_as_a_definite_failure(bridge_server, env_dir):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    stub.response_mode = "empty_id"

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 6, result.stderr
    rows = _unconfirmed(env_dir)
    assert rows[0]["delivery_certainty"] == "uncertain"
    assert rows[0]["send_status"] == "send_uncertain"
    assert len(stub.requests) == 1, "never re-sent"
    # The pre-existing failure row still says bridge_unreachable — its `reason` is
    # a schema Literal that cannot carry the real status without a schemas.py
    # change. The new row is where the truth lives.
    failed = [r for r in _read_audit(env_dir)
              if r["type"] == "catering_proposal_generation_failed"]
    assert failed and failed[-1]["reason"] == "bridge_unreachable"


def test_uncertain_proposal_send_lands_send_uncertain_not_send_failed(bridge_server, env_dir):
    """P1 defect A. The audit row already told the truth; the STATE ROW was the
    liar — the send-failure writer recorded a bridge-accepted send as definite
    non-delivery, and that row is what every later reader consults."""
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    stub.response_mode = "empty_id"

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 6, result.stderr
    row = _read_store(env_dir)["sets"][-1]
    assert row["status"] == "SEND_UNCERTAIN", (
        "a bridge-accepted send whose ack was unparseable is NOT a definite failure")
    # The evidence is preserved verbatim: the bridge's own ack body, unchanged.
    assert row["failure_reason"].startswith(("ack_parse_failed:", "empty_message_id:")), (
        f"the ack evidence was dropped: {row['failure_reason']!r}")
    assert "accepted" in row["failure_reason"], "the ack body itself must survive"
    # Unchanged from the SEND_FAILED path and deliberately so: the bridge returned
    # no id in either uncertain sub-case, so there is none to record.
    assert row["outbound_message_id"] == ""
    assert len(stub.requests) == 1, "never re-sent"


def test_a_later_successful_send_does_not_supersede_an_uncertain_set(bridge_server, env_dir):
    """P1/R1 — SEND_UNCERTAIN is terminal to AUTOMATION. The table allows
    SEND_UNCERTAIN -> SUPERSEDED so an operator-driven resolution exists, and this
    pins that no automated path can take it: `_mark_sent_and_supersede` supersedes
    rows selected by `row.status == "SENT"`, so a later success walks past it."""
    store = {
        "schema_version": 1,
        "next_sequence": 2,
        "sets": [_proposal_set("CPS-L0014-000001", "SEND_UNCERTAIN")],
    }
    store["sets"][0]["failure_reason"] = "empty_message_id: {\"accepted\": true}"
    (env_dir / "state" / "catering-proposals.json").write_text(
        json.dumps(store), encoding="utf-8")
    port, _stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 0, result.stderr
    by_id = {row["proposal_set_id"]: row for row in _read_store(env_dir)["sets"]}
    assert by_id["CPS-L0014-000002"]["status"] == "SENT"
    uncertain = by_id["CPS-L0014-000001"]
    assert uncertain["status"] == "SEND_UNCERTAIN", (
        "automation moved a SEND_UNCERTAIN row; only an operator may resolve one")
    assert uncertain["failure_reason"] == "empty_message_id: {\"accepted\": true}", (
        "the ack evidence must survive a later send untouched")


def test_successful_proposal_send_emits_no_unconfirmed_row(bridge_server, env_dir):
    port, _stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 0, result.stderr
    assert _unconfirmed(env_dir) == []


@pytest.mark.parametrize("response_mode", ["down", "empty_id"])
def test_a_failed_generation_send_pages_the_owner_exactly_once(
    bridge_server, env_dir, response_mode
):
    """Every failure path in this script converges on `_fail_generation`, which
    already pages. `_post` therefore records owner_paged=True WITHOUT paging again,
    and the row is only honest while that stays true — a refactor that adds a page
    at the send site would alert twice for one failure, which is how an owner
    learns to ignore the channel."""
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    stub.response_mode = response_mode

    result, parsed = _run_script(env_dir, port)

    assert parsed["rc"] == 6, result.stderr
    assert _unconfirmed(env_dir)[0]["owner_paged"] is True
    assert len(parsed["notify_calls"]) == 1, parsed["notify_calls"]


@pytest.mark.parametrize(
    "notify_rc", [6, None],
    ids=["notify_owner_exits_nonzero", "notify_owner_raises"],
)
@pytest.mark.parametrize("response_mode", ["down", "empty_id"])
def test_a_page_that_never_landed_is_not_claimed_as_paged(
    bridge_server, env_dir, response_mode, notify_rc
):
    """owner_paged is the row's claim that a HUMAN was told. shift-agent-notify-owner
    exits non-zero when every channel failed, so a hard-coded True turns the one
    row that exists to prove the failure surfaced into the reason nobody goes
    looking. Still exactly one page attempt — the fix is truthfulness, not retry."""
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    stub.response_mode = response_mode

    result, parsed = _run_script(env_dir, port, notify_rc=notify_rc)

    assert parsed["rc"] == 6, result.stderr
    rows = _unconfirmed(env_dir)
    assert len(rows) == 1
    assert rows[0]["owner_paged"] is False
    assert len(parsed["notify_calls"]) == 1, parsed["notify_calls"]


def test_failed_recompose_clarify_records_its_status(bridge_server, env_dir):
    """The clarify arm is a third send site with its own early return."""
    port, stub = bridge_server
    _seed_menu(env_dir, _RECOMPOSE_MENU)
    _seed_lead(env_dir)
    _seed_recompose_sent_set(env_dir)
    stub.response_mode = "down"

    result, parsed = _run_recompose(env_dir, port, "option 2 starters with option 1 mains")

    assert parsed["rc"] == 6, result.stderr
    rows = _unconfirmed(env_dir)
    assert len(rows) == 1 and rows[0]["delivery_certainty"] == "failed"
