"""F5b 2026-05-01: tests for send-catering-ack server-side prefix-prepend.

Covers the customer-ack outbound path that PR #43 did NOT fix. Mirrors the
test pattern in test_catering_v02_scripts.py — bridge stub HTTP server +
script subprocess via in-process module loader (so we can override
LOG_PATH / BRIDGE_URL constants).

Linux-only: send-catering-ack imports safe_io which uses fcntl.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="send-catering-ack imports safe_io (fcntl, Linux-only)",
)

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "agents" / "catering" / "scripts" / "send-catering-ack"
PLATFORM_DIR = Path(__file__).resolve().parent.parent / "src" / "platform"

EXPECTED_PREFIX = "⚕ *Catering Agent*\n────────────\n"


class _BridgeStub(BaseHTTPRequestHandler):
    requests: list = []
    response_mode = "ok"  # "ok" | "no_mid" | "500" | "drop_connection"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except Exception:
            doc = {}
        self.__class__.requests.append(doc)
        if self.__class__.response_mode == "500":
            self.send_response(500)
            self.end_headers()
            return
        if self.__class__.response_mode == "drop_connection":
            self.wfile.close()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.__class__.response_mode == "no_mid":
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.wfile.write(json.dumps({"id": f"msg_{int(time.time()*1000)}"}).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def bridge_server():
    _BridgeStub.requests = []
    _BridgeStub.response_mode = "ok"
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
    logs = tmp_path / "logs"
    logs.mkdir()
    return tmp_path


def _run_script(env_dir, bridge_port, customer_jid, message_text, lead_id=""):
    """Run send-catering-ack with overridden LOG_PATH + BRIDGE_URL via importlib."""
    args = [
        "send-catering-ack",
        "--customer-jid", customer_jid,
        "--message-text", message_text,
    ]
    if lead_id:
        args += ["--lead-id", lead_id]

    log_path = env_dir / "logs" / "decisions.log"
    wrapper = f"""
import sys, pathlib, json, io
sys.argv = {args!r}
sys.path.insert(0, {str(PLATFORM_DIR)!r})
import importlib.machinery, importlib.util
# Explicit SourceFileLoader: send-catering-ack is extensionless, so
# spec_from_file_location WITHOUT a loader returns None (pre-existing wrapper
# bug that prevented this send test from running at all).
loader = importlib.machinery.SourceFileLoader("sca", {str(SCRIPT)!r})
spec = importlib.util.spec_from_file_location("sca", {str(SCRIPT)!r}, loader=loader)
mod = importlib.util.module_from_spec(spec)
mod.__name__ = "sca_test_loaded"
spec.loader.exec_module(mod)
mod.LOG_PATH = pathlib.Path({str(log_path)!r})
mod.BRIDGE_URL = "http://127.0.0.1:{bridge_port}/send"
buf_out = io.StringIO()
sys.stdout = buf_out
try:
    rc = mod.main()
finally:
    sys.stdout = sys.__stdout__
print(json.dumps({{"rc": rc, "stdout": buf_out.getvalue()}}))
"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", wrapper],
        capture_output=True, text=True, timeout=15,
        # send-path-test-harness: canonical safe_io.BRIDGE_URL -> stub (via env)
        # + opt past the pytest guard. Caller resolves to the allowlisted
        # send-catering-ack script. (bridge_port=1 in the dead-port test keeps
        # its intended connect-refused behavior; never the live bridge :3000.)
        env={**os.environ,
             "HERMES_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}/send",
             "SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS": "1"},
    )
    return result


def _read_audit(env_dir):
    log_path = env_dir / "logs" / "decisions.log"
    if not log_path.exists():
        return []
    return [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- Happy path ----------

def test_phone_jid_happy_path_prepends_prefix(bridge_server, env_dir):
    port, stub = bridge_server
    res = _run_script(env_dir, port,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="Thanks — we got your inquiry, we'll be back to you shortly.",
                      lead_id="L0014")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 0, f"non-zero exit; stderr: {res.stderr}"
    assert len(stub.requests) == 1
    sent_msg = stub.requests[0]["message"]
    assert sent_msg.startswith(EXPECTED_PREFIX), f"prefix missing: {sent_msg[:80]!r}"
    assert sent_msg.endswith("we'll be back to you shortly.")
    assert stub.requests[0]["chatId"] == "17329837841@s.whatsapp.net"

    audit = _read_audit(env_dir)
    assert any(e["type"] == "catering_customer_ack_sent" and e["lead_id"] == "L0014"
               for e in audit), f"missing ack_sent audit: {audit}"


def test_lid_jid_happy_path(bridge_server, env_dir):
    port, stub = bridge_server
    res = _run_script(env_dir, port,
                      customer_jid="269612545511591@lid",
                      message_text="Thanks for reaching out. To help, can you share the date and headcount?")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 0, f"non-zero exit; stderr: {res.stderr}"
    assert stub.requests[0]["chatId"] == "269612545511591@lid"
    assert stub.requests[0]["message"].startswith(EXPECTED_PREFIX)


def test_returns_outbound_message_id_on_stdout(bridge_server, env_dir):
    port, _ = bridge_server
    res = _run_script(env_dir, port,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="hello")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 0
    inner = json.loads(parsed["stdout"].strip())
    assert inner["ok"] is True
    assert inner["outbound_message_id"].startswith("msg_")


# ---------- Bad input ----------

@pytest.mark.parametrize("bad_jid", [
    "17329837841",                           # no @suffix
    "17329837841@c.us",                       # wrong suffix
    "@s.whatsapp.net",                        # empty digits
    "user@example.com",                       # email-shaped
])
def test_bad_jid_rejected_with_exit_2(bridge_server, env_dir, bad_jid):
    port, stub = bridge_server
    res = _run_script(env_dir, port, customer_jid=bad_jid, message_text="hi")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 2, f"expected exit 2 for {bad_jid!r}; got {parsed}"
    assert len(stub.requests) == 0, "bridge must not be hit on bad input"
    audit = _read_audit(env_dir)
    assert any(e["type"] == "catering_customer_ack_failed" and e["reason"] == "bad_input"
               for e in audit), f"missing bad_input audit: {audit}"


def test_empty_message_text_rejected(bridge_server, env_dir):
    port, stub = bridge_server
    res = _run_script(env_dir, port,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="   ")  # whitespace-only
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 2
    assert len(stub.requests) == 0


def test_message_text_too_long_rejected(bridge_server, env_dir):
    port, stub = bridge_server
    res = _run_script(env_dir, port,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="a" * 3501)
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 2
    assert len(stub.requests) == 0


# ---------- Bridge failures ----------

def test_bridge_unreachable_returns_exit_6(env_dir):
    # No bridge server fixture — port 1 is unreachable
    res = _run_script(env_dir, bridge_port=1,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="hi")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 6, f"expected exit 6 (DEPENDENCY_DOWN); got {parsed}"
    audit = _read_audit(env_dir)
    assert any(e["type"] == "catering_customer_ack_failed" and e["reason"] == "bridge_unreachable"
               for e in audit), f"missing bridge_unreachable audit: {audit}"


def test_bridge_returns_no_message_id(bridge_server, env_dir):
    port, stub = bridge_server
    stub.response_mode = "no_mid"
    res = _run_script(env_dir, port,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="hi")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 6
    audit = _read_audit(env_dir)
    # Either bridge_unreachable (caught by _bridge_post returning False with "empty messageId" detail)
    # or empty_response — both are acceptable; the script's branching means
    # "False, 'empty messageId: ...'" goes to bridge_unreachable.
    failed = [e for e in audit if e["type"] == "catering_customer_ack_failed"]
    assert failed, f"missing ack_failed audit: {audit}"
    assert failed[0]["reason"] in {"bridge_unreachable", "empty_response"}


# ---------- Prefix correctness (regression guard) ----------

def test_prefix_matches_bridge_template_bypass_regex(bridge_server, env_dir):
    """The prefix MUST match bridge.js:133 regex /^⚕ \\*[A-Za-z][A-Za-z ]*\\*\\n[─\\-]+\\n/.
    If this test fails, the prefix has drifted and the bridge will drop messages.
    """
    import re
    port, stub = bridge_server
    res = _run_script(env_dir, port,
                      customer_jid="17329837841@s.whatsapp.net",
                      message_text="anything")
    parsed = json.loads(res.stdout.strip().splitlines()[-1])
    assert parsed["rc"] == 0
    sent_msg = stub.requests[0]["message"]
    bridge_regex = re.compile(r"^⚕ \*[A-Za-z][A-Za-z ]*\*\n[─\-]+\n")
    assert bridge_regex.match(sent_msg), \
        f"prefix does not match bridge template_bypass regex: {sent_msg[:80]!r}"
