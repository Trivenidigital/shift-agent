"""Task 3 - select-catering-proposal behavior tests."""
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
    platform.system() != "Linux",
    reason="catering scripts depend on safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "catering" / "scripts" / "select-catering-proposal"
PLATFORM_DIR = REPO / "src" / "platform"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.requests.append(json.loads(body))
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


def _seed_lead(env_dir: Path, lead_id: str = "L0014", code: str | None = "#ABCDE") -> None:
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
        "owner_approval_code": code,
        "customer_replied": False,
        "selected_items": [],
        "quote_total_usd": None,
        "customer_finalized_at": None,
        "last_finalize_message_id": None,
    }
    (env_dir / "state" / "catering-leads.json").write_text(
        json.dumps({"schema_version": 1, "leads": [lead]}), encoding="utf-8"
    )


def _option(option_id: str, tier: str, item_names: list[str] | None = None) -> dict:
    return {
        "option_id": option_id,
        "style_key": f"{tier}_style",
        "tier": tier,
        "item_names": item_names or [f"Item {option_id}"],
    }


def _proposal_set(
    proposal_set_id: str,
    status: str,
    *,
    lead_id: str = "L0014",
    outbound_message_id: str = "proposal_msg",
    options: list[dict] | None = None,
) -> dict:
    return {
        "proposal_set_id": proposal_set_id,
        "lead_id": lead_id,
        "status": status,
        "created_at": "2026-04-30T10:00:00-04:00",
        "sent_at": "2026-04-30T10:01:00-04:00" if status == "SENT" else None,
        "outbound_message_id": outbound_message_id if status == "SENT" else "",
        "source_message_id": f"msg_{proposal_set_id[-6:]}",
        "request_text": "two ideas",
        "options": options
        or [
            _option("1", "balanced", ["Aloo Paratha", "Chicken Biryani"]),
            _option("2", "premium", ["Gulab Jamun"]),
        ],
        "selected_option_id": None,
        "failure_reason": "",
    }


def _seed_proposals(env_dir: Path, sets: list[dict]) -> None:
    (env_dir / "state" / "catering-proposals.json").write_text(
        json.dumps({"schema_version": 1, "next_sequence": len(sets) + 1, "sets": sets}),
        encoding="utf-8",
    )


def _seed_menu(env_dir: Path, items: list[dict] | None = None) -> None:
    (env_dir / "state" / "catering-menu.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-04-30T10:00:00-04:00",
                "updated_by": "manual",
                "source_image_id": None,
                "items": items
                or [
                    {"name": "Gulab Jamun", "price_usd": 3, "category": "dessert", "available": True},
                    {"name": "Chicken Biryani", "price_usd": 15, "category": "main", "available": True},
                    {"name": "Aloo Paratha", "price_usd": 5, "category": "main", "available": True},
                    {"name": "Paneer Tikka", "price_usd": 12, "category": "appetizer", "available": True},
                    {"name": "Samosa", "price_usd": 2, "category": "appetizer", "available": True},
                ],
                "notes": "",
            }
        ),
        encoding="utf-8",
    )


def _load_script(
    env_dir: Path,
    bridge_port: int,
    monkeypatch,
    finalize_rc: int = 0,
    notify_rc: int = 0,
    finalize_exc: Exception | None = None,
    finalize_probe=None,
):
    sys.path.insert(0, str(PLATFORM_DIR))
    from importlib.machinery import SourceFileLoader

    mod = SourceFileLoader(f"scp_test_loaded_{time.time_ns()}", str(SCRIPT)).load_module()
    mod.PROPOSALS_PATH = env_dir / "state" / "catering-proposals.json"
    mod.PROPOSALS_LOCK = env_dir / "state" / "catering-proposals.json.lock"
    mod.LEADS_PATH = env_dir / "state" / "catering-leads.json"
    mod.LEADS_LOCK = env_dir / "state" / "catering-leads.json.lock"
    mod.MENU_PATH = env_dir / "state" / "catering-menu.json"
    mod.LOG_PATH = env_dir / "logs" / "decisions.log"
    mod.LOG_LOCK = env_dir / "logs" / "decisions.log.lock"
    mod.BRIDGE_URL = f"http://127.0.0.1:{bridge_port}/send"
    mod.NOTIFY_OWNER_BIN = env_dir / "notify-owner"

    # send-path-test-harness: this script runs IN-PROCESS (SourceFileLoader),
    # so override the CANONICAL safe_io.BRIDGE_URL to the stub + opt past the
    # pytest bridge guard here. The loaded module's frame basename resolves to
    # the allowlisted select-catering-proposal script, so the action-context
    # chokepoint passes for its null-context sends. Stub port (not :3000) keeps
    # the live-bridge tripwire dormant.
    import safe_io as _safe_io
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    monkeypatch.setattr(_safe_io, "BRIDGE_URL", f"http://127.0.0.1:{bridge_port}/send")

    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if str(mod.FINALIZE_BIN) in [str(part) for part in argv]:
            if finalize_probe is not None:
                finalize_probe(real_run)
            if finalize_exc is not None:
                raise finalize_exc
            return subprocess.CompletedProcess(argv, finalize_rc)
        if str(mod.NOTIFY_OWNER_BIN) in [str(part) for part in argv]:
            return subprocess.CompletedProcess(argv, notify_rc)
        raise AssertionError(f"unexpected subprocess: {argv}")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return mod, calls


def _run_main(mod, selection_text: str = "Option 2") -> int:
    old_argv = sys.argv
    sys.argv = [
        "select-catering-proposal",
        "--lead-id",
        "L0014",
        "--customer-jid",
        "19045550199@s.whatsapp.net",
        "--customer-message-id",
        "msg2",
        "--selection-text",
        selection_text,
    ]
    try:
        return mod.main()
    finally:
        sys.argv = old_argv


def _read_store(env_dir: Path) -> dict:
    return json.loads((env_dir / "state" / "catering-proposals.json").read_text(encoding="utf-8"))


def _read_audit(env_dir: Path) -> list[dict]:
    path = env_dir / "logs" / "decisions.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_option_number_selection_calls_finalize_with_code(bridge_server, env_dir, monkeypatch):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(env_dir, [_proposal_set("CPS-L0014-000001", "SENT")])
    _seed_menu(env_dir)
    mod, calls = _load_script(env_dir, port, monkeypatch)

    rc = _run_main(mod, "Let's go with option 2")

    assert rc == 0
    assert len(calls) == 1
    argv = calls[0]
    assert "--code" in argv
    assert argv[argv.index("--code") + 1] == "#ABCDE"
    assert "--customer-message-id" in argv
    assert argv[argv.index("--customer-message-id") + 1] == "msg2"
    assert "--selected-items-json" in argv
    selected = json.loads(argv[argv.index("--selected-items-json") + 1])
    assert selected == [{"name": "Gulab Jamun", "qty": 1, "price_usd": 3}]
    assert "--quote-total-usd" in argv
    assert argv[argv.index("--quote-total-usd") + 1] == "3"
    selected_audit = _read_audit(env_dir)[-1]
    assert selected_audit["type"] == "catering_proposal_selected"
    assert selected_audit["option_id"] == "2"
    assert selected_audit["finalize_exit_code"] == 0


def test_finalize_runs_after_proposals_lock_is_released(bridge_server, env_dir, monkeypatch):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(env_dir, [_proposal_set("CPS-L0014-000001", "SENT")])
    _seed_menu(env_dir)
    probe_results: list[subprocess.CompletedProcess] = []

    def probe(real_run):
        code = (
            "import fcntl, pathlib, sys\n"
            f"p = pathlib.Path({str(env_dir / 'state' / 'catering-proposals.json.lock')!r})\n"
            "p.touch(exist_ok=True)\n"
            "fh = p.open('a+')\n"
            "try:\n"
            "    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n"
            "    sys.exit(17)\n"
            "sys.exit(0)\n"
        )
        probe_results.append(real_run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5))

    mod, _ = _load_script(env_dir, port, monkeypatch, finalize_probe=probe)

    rc = _run_main(mod, "Let's go with option 2")

    assert rc == 0
    assert probe_results
    assert probe_results[0].returncode == 0, probe_results[0].stderr


def test_newer_proposal_during_finalize_blocks_stale_selection(bridge_server, env_dir, monkeypatch):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(env_dir, [_proposal_set("CPS-L0014-000001", "SENT")])
    _seed_menu(env_dir)

    def create_newer_proposal(_real_run):
        store = _read_store(env_dir)
        store["sets"].append(
            _proposal_set(
                "CPS-L0014-000002",
                "SENT",
                outbound_message_id="newer_proposal_msg",
            )
        )
        store["next_sequence"] = 3
        (env_dir / "state" / "catering-proposals.json").write_text(
            json.dumps(store), encoding="utf-8"
        )

    mod, calls = _load_script(env_dir, port, monkeypatch, finalize_probe=create_newer_proposal)

    rc = _run_main(mod, "Let's go with option 2")

    assert rc == 4
    assert len(calls) == 1
    by_id = {row["proposal_set_id"]: row for row in _read_store(env_dir)["sets"]}
    assert by_id["CPS-L0014-000001"]["status"] == "SELECT_FAILED"
    assert by_id["CPS-L0014-000001"]["selected_option_id"] is None
    assert by_id["CPS-L0014-000002"]["status"] == "SENT"
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] == "no_sent_proposal"
    assert "superseded during finalize" in audit["detail"]


def test_non_action_numeric_mention_asks_clarification(bridge_server, env_dir, monkeypatch):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(env_dir, [_proposal_set("CPS-L0014-000001", "SENT")])
    _seed_menu(env_dir)
    mod, calls = _load_script(env_dir, port, monkeypatch)

    rc = _run_main(mod, "can you tell me about option 2?")

    assert rc == 2
    assert calls == []
    assert len(stub.requests) == 1
    assert "Please reply" in stub.requests[0]["message"]
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] == "invalid_selection"


def test_send_failed_set_is_not_selectable(bridge_server, env_dir, monkeypatch):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    _seed_proposals(
        env_dir,
        [
            _proposal_set("CPS-L0014-000001", "SENT"),
            _proposal_set("CPS-L0014-000002", "SEND_FAILED"),
        ],
    )
    mod, calls = _load_script(env_dir, port, monkeypatch)

    rc = _run_main(mod, "Option 1")

    assert rc in {2, 4}
    assert calls == []


def test_stale_proposal_target_does_not_finalize_or_mark_selected(bridge_server, env_dir, monkeypatch):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    stale = _proposal_set("CPS-L0014-000001", "SENT")
    newer = _proposal_set("CPS-L0014-000002", "DRAFT")
    _seed_proposals(env_dir, [stale, newer])
    mod, calls = _load_script(env_dir, port, monkeypatch)
    stale_snapshot = mod.CateringProposalSet.model_validate(stale)
    monkeypatch.setattr(mod, "_latest_proposal_for_lead", lambda lead_id: stale_snapshot)

    rc = _run_main(mod, "Option 2")

    assert rc in {2, 4}
    assert calls == []
    store = _read_store(env_dir)
    older = next(row for row in store["sets"] if row["proposal_set_id"] == "CPS-L0014-000001")
    assert older["status"] == "SENT"
    assert older["selected_option_id"] is None
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] in {"no_sent_proposal", "invalid_selection"}
    assert audit["proposal_set_id"] == "CPS-L0014-000001"


def test_already_selecting_proposal_does_not_finalize_or_overwrite_selection(
    bridge_server, env_dir, monkeypatch
):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    sent_snapshot = _proposal_set("CPS-L0014-000001", "SENT")
    claimed = _proposal_set("CPS-L0014-000001", "SELECTING")
    claimed["selected_option_id"] = "1"
    _seed_proposals(env_dir, [claimed])
    mod, calls = _load_script(env_dir, port, monkeypatch)
    proposal_snapshot = mod.CateringProposalSet.model_validate(sent_snapshot)
    monkeypatch.setattr(mod, "_latest_proposal_for_lead", lambda lead_id: proposal_snapshot)

    rc = _run_main(mod, "Option 2")

    assert rc in {2, 4}
    assert calls == []
    selected_set = _read_store(env_dir)["sets"][0]
    assert selected_set["status"] == "SELECTING"
    assert selected_set["selected_option_id"] == "1"
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] in {"no_sent_proposal", "invalid_selection"}
    assert audit["proposal_set_id"] == "CPS-L0014-000001"


def test_already_selected_proposal_does_not_finalize_or_overwrite_selection(
    bridge_server, env_dir, monkeypatch
):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    sent_snapshot = _proposal_set("CPS-L0014-000001", "SENT")
    selected = _proposal_set("CPS-L0014-000001", "SELECTED")
    selected["selected_option_id"] = "1"
    _seed_proposals(env_dir, [selected])
    mod, calls = _load_script(env_dir, port, monkeypatch)
    proposal_snapshot = mod.CateringProposalSet.model_validate(sent_snapshot)
    monkeypatch.setattr(mod, "_latest_proposal_for_lead", lambda lead_id: proposal_snapshot)

    rc = _run_main(mod, "Option 2")

    assert rc in {2, 4}
    assert calls == []
    selected_set = _read_store(env_dir)["sets"][0]
    assert selected_set["status"] == "SELECTED"
    assert selected_set["selected_option_id"] == "1"
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] in {"no_sent_proposal", "invalid_selection"}
    assert audit["proposal_set_id"] == "CPS-L0014-000001"


def test_ambiguous_tier_alias_asks_clarification(bridge_server, env_dir, monkeypatch):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    _seed_proposals(
        env_dir,
        [
            _proposal_set(
                "CPS-L0014-000001",
                "SENT",
                options=[
                    _option("1", "premium", ["Paneer Tikka"]),
                    _option("2", "premium", ["Gulab Jamun"]),
                ],
            )
        ],
    )
    mod, calls = _load_script(env_dir, port, monkeypatch)

    rc = _run_main(mod, "premium")

    assert rc == 2
    assert calls == []
    assert len(stub.requests) == 1
    message = stub.requests[0]["message"]
    assert "Option 1" in message
    assert "Option 2" in message
    assert "Please reply" in message


@pytest.mark.parametrize(
    "finalize_rc,expected_status,expect_selected",
    [
        (0, "SELECTED", True),
        (6, "SELECTED_OWNER_CARD_FAILED", True),
        (2, "SELECT_FAILED", False),
        (4, "SELECT_FAILED", False),
        (11, "SELECT_FAILED", False),
    ],
)
def test_finalize_exit_code_handling(
    bridge_server, env_dir, monkeypatch, finalize_rc, expected_status, expect_selected
):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(env_dir, [_proposal_set("CPS-L0014-000001", "SENT")])
    _seed_menu(env_dir)
    notify_rc = 1 if finalize_rc == 6 else 0
    mod, calls = _load_script(env_dir, port, monkeypatch, finalize_rc=finalize_rc, notify_rc=notify_rc)

    rc = _run_main(mod, "Option 2")

    assert rc == finalize_rc
    expected_call_count = 2 if finalize_rc == 6 else 1
    assert len(calls) == expected_call_count
    finalize_call = next(call for call in calls if str(mod.FINALIZE_BIN) in call)
    selected_set = _read_store(env_dir)["sets"][0]
    assert selected_set["status"] == expected_status
    if expect_selected:
        assert selected_set["selected_option_id"] == "2"
        selected_audit = _read_audit(env_dir)[-1]
        assert selected_audit["type"] == "catering_proposal_selected"
        assert selected_audit["finalize_exit_code"] == finalize_rc
        if finalize_rc == 6:
            notify_call = next(call for call in calls if str(mod.NOTIFY_OWNER_BIN) in call)
            assert "--title" in notify_call
            assert notify_call[notify_call.index("--title") + 1] == "Catering proposal owner card failed"
            notify_message = notify_call[-1]
            assert "L0014" in notify_message
            assert "CPS-L0014-000001" in notify_message
            assert "option 2" in notify_message
    else:
        assert selected_set["selected_option_id"] is None
        assert _read_audit(env_dir)[-1]["type"] == "catering_proposal_selection_failed"
        assert _read_audit(env_dir)[-1]["reason"] == f"finalize_exit_{finalize_rc}"


@pytest.mark.parametrize(
    "finalize_exc,expected_reason",
    [
        (subprocess.TimeoutExpired(cmd=["finalize-catering-menu"], timeout=60), "finalize_exit_other"),
        (FileNotFoundError("missing finalize-catering-menu"), "finalize_exit_other"),
    ],
)
def test_finalize_exception_marks_failed_audits_and_notifies_customer(
    bridge_server, env_dir, monkeypatch, finalize_exc, expected_reason
):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(env_dir, [_proposal_set("CPS-L0014-000001", "SENT")])
    _seed_menu(env_dir)
    mod, calls = _load_script(env_dir, port, monkeypatch, finalize_exc=finalize_exc)

    rc = _run_main(mod, "Option 2")

    assert rc in {2, 6}
    assert len(calls) == 1
    selected_set = _read_store(env_dir)["sets"][0]
    assert selected_set["status"] == "SELECT_FAILED"
    assert selected_set["selected_option_id"] is None
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] == expected_reason
    assert type(finalize_exc).__name__ in audit["detail"]
    assert len(stub.requests) == 1
    assert "I could not lock that option in" in stub.requests[0]["message"]
    assert "Option 1" in stub.requests[0]["message"]
    assert "Option 2" in stub.requests[0]["message"]


@pytest.mark.parametrize("finalize_rc", [2, 11])
def test_option_three_finalize_failure_lists_option_three(
    bridge_server, env_dir, monkeypatch, finalize_rc
):
    port, stub = bridge_server
    _seed_lead(env_dir)
    _seed_menu(env_dir)
    _seed_proposals(
        env_dir,
        [
            _proposal_set(
                "CPS-L0014-000001",
                "SENT",
                options=[
                    _option("1", "classic", ["Samosa"]),
                    _option("2", "balanced", ["Paneer Tikka"]),
                    _option("3", "premium", ["Gulab Jamun"]),
                ],
            )
        ],
    )
    mod, calls = _load_script(env_dir, port, monkeypatch, finalize_rc=finalize_rc)

    rc = _run_main(mod, "Option 3")

    assert rc == finalize_rc
    assert len(calls) == 1
    assert len(stub.requests) == 1
    message = stub.requests[0]["message"]
    assert "Option 1" in message
    assert "Option 2" in message
    assert "Option 3" in message


def test_selection_uses_current_menu_prices_for_multi_item_option(bridge_server, env_dir, monkeypatch):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(
        env_dir,
        [
            _proposal_set(
                "CPS-L0014-000001",
                "SENT",
                options=[
                    _option("1", "classic", ["Samosa"]),
                    _option("2", "balanced", ["Gulab Jamun", "Chicken Biryani"]),
                ],
            )
        ],
    )
    _seed_menu(env_dir)
    mod, calls = _load_script(env_dir, port, monkeypatch)

    rc = _run_main(mod, "Option 2")

    assert rc == 0
    finalize_call = next(call for call in calls if str(mod.FINALIZE_BIN) in call)
    selected = json.loads(finalize_call[finalize_call.index("--selected-items-json") + 1])
    assert selected == [
        {"name": "Gulab Jamun", "qty": 1, "price_usd": 3},
        {"name": "Chicken Biryani", "qty": 1, "price_usd": 15},
    ]
    assert finalize_call[finalize_call.index("--quote-total-usd") + 1] == "18"


@pytest.mark.parametrize(
    "menu_items,expected_detail",
    [
        (None, "menu load failed"),
        ([{"name": "Gulab Jamun", "price_usd": 3, "category": "dessert", "available": True}], "missing menu item"),
        (
            [
                {"name": "Gulab Jamun", "price_usd": 3, "category": "dessert", "available": True},
                {"name": "Chicken Biryani", "price_usd": 15, "category": "main", "available": False},
            ],
            "unavailable menu item",
        ),
        (
            [
                {"name": "Gulab Jamun", "price_usd": 3, "category": "dessert", "available": True},
                {"name": "Chicken Biryani", "price_usd": None, "category": "main", "available": True},
            ],
            "missing price",
        ),
    ],
)
def test_menu_problem_blocks_finalize_and_audits_invalid_selection(
    bridge_server, env_dir, monkeypatch, menu_items, expected_detail
):
    port, _ = bridge_server
    _seed_lead(env_dir)
    _seed_proposals(
        env_dir,
        [
            _proposal_set(
                "CPS-L0014-000001",
                "SENT",
                options=[
                    _option("1", "classic", ["Samosa"]),
                    _option("2", "balanced", ["Gulab Jamun", "Chicken Biryani"]),
                ],
            )
        ],
    )
    if menu_items is not None:
        _seed_menu(env_dir, menu_items)
    mod, calls = _load_script(env_dir, port, monkeypatch)

    rc = _run_main(mod, "Option 2")

    assert rc == 2
    assert calls == []
    selected_set = _read_store(env_dir)["sets"][0]
    assert selected_set["status"] == "SENT"
    assert selected_set["selected_option_id"] is None
    audit = _read_audit(env_dir)[-1]
    assert audit["type"] == "catering_proposal_selection_failed"
    assert audit["reason"] == "invalid_selection"
    assert expected_detail in audit["detail"]
