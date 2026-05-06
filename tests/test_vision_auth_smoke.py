"""End-to-end tests for vision-auth-smoke. Linux-only (matches sibling tests).

Subprocess-invokes the smoke script against a stub HTTP server that
simulates OpenRouter, exercising the full set of exit codes.

Mirrors the BridgeStub pattern from test_catering_v02_scripts.py.
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

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="smoke script invoked via /usr/bin/env python3 — Linux test runner",
)

SMOKE = (
    Path(__file__).resolve().parent.parent
    / "src" / "agents" / "catering" / "scripts" / "vision-auth-smoke"
)


class _OpenRouterStub(BaseHTTPRequestHandler):
    """Configurable stub: status code + body driven by class attributes."""

    next_status: int = 200
    next_body: dict | None = None
    requests: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            doc = json.loads(body)
        except Exception:
            doc = {}
        self.__class__.requests.append({
            "auth": self.headers.get("Authorization", ""),
            "model": doc.get("model"),
            "messages": doc.get("messages"),
        })
        self.send_response(self.__class__.next_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body_doc = self.__class__.next_body or {
            "choices": [{"message": {"content": "ok"}}]
        }
        self.wfile.write(json.dumps(body_doc).encode())

    def log_message(self, format, *args):
        return


@pytest.fixture
def stub():
    _OpenRouterStub.requests = []
    _OpenRouterStub.next_status = 200
    _OpenRouterStub.next_body = None
    server = HTTPServer(("127.0.0.1", 0), _OpenRouterStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, _OpenRouterStub
    finally:
        server.shutdown()


def _run(stub_port: int, api_key: str = "sk-test-valid", retries: int = 0,
         env_path: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the smoke script with stub URL + given API key + retry count."""
    env = {
        **os.environ,
        "OPENROUTER_URL": f"http://127.0.0.1:{stub_port}",
        "OPENROUTER_API_KEY": api_key,
        "VISION_AUTH_SMOKE_RETRIES": str(retries),
        "VISION_AUTH_SMOKE_TIMEOUT_SEC": "3",
    }
    if env_path is not None:
        env["SHIFT_AGENT_ENV_PATH"] = env_path
    return subprocess.run(
        [sys.executable, str(SMOKE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_success_returns_zero_and_calls_openrouter(stub):
    port, stub_cls = stub
    result = _run(port)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "ok" in result.stdout
    assert len(stub_cls.requests) == 1
    req = stub_cls.requests[0]
    assert req["auth"] == "Bearer sk-test-valid"
    assert req["model"] == "openai/gpt-4o-mini"
    assert req["messages"] == [{"role": "user", "content": "ok"}]


def test_401_returns_auth_fail(stub):
    port, stub_cls = stub
    stub_cls.next_status = 401
    stub_cls.next_body = {"error": {"message": "Invalid API key"}}
    result = _run(port)
    assert result.returncode == 1, f"stderr: {result.stderr}"
    assert "AUTH FAIL" in result.stderr


def test_403_returns_auth_fail(stub):
    port, stub_cls = stub
    stub_cls.next_status = 403
    stub_cls.next_body = {"error": {"message": "Forbidden"}}
    result = _run(port)
    assert result.returncode == 1


def test_500_returns_transient_after_retries(stub):
    port, stub_cls = stub
    stub_cls.next_status = 500
    stub_cls.next_body = {"error": "upstream"}
    result = _run(port, retries=2)
    assert result.returncode == 2, f"stderr: {result.stderr}"
    # 1 initial + 2 retries = 3 total
    assert len(stub_cls.requests) == 3


def test_missing_api_key_returns_auth_fail(stub, tmp_path):
    """No env, no .env file → exit 1."""
    port, _ = stub
    fake_env = tmp_path / "empty.env"
    fake_env.write_text("# empty\n")
    result = _run(port, api_key="", env_path=str(fake_env))
    assert result.returncode == 1
    assert "missing or placeholder" in result.stderr


def test_placeholder_api_key_returns_auth_fail(stub):
    """Literal 'placeholder' string also rejected."""
    port, _ = stub
    result = _run(port, api_key="placeholder")
    assert result.returncode == 1


def test_dotenv_fallback_reads_api_key(stub, tmp_path):
    """No env var, but .env file has the key → succeeds."""
    port, stub_cls = stub
    env_file = tmp_path / "shift-agent.env"
    env_file.write_text('OPENROUTER_API_KEY="sk-from-dotenv"\n', encoding="utf-8")
    result = _run(port, api_key="", env_path=str(env_file))
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert stub_cls.requests[0]["auth"] == "Bearer sk-from-dotenv"


def test_200_with_empty_content_is_transient(stub):
    """Provider returned 200 but content is empty — treat as transient."""
    port, stub_cls = stub
    stub_cls.next_status = 200
    stub_cls.next_body = {"choices": [{"message": {"content": ""}}]}
    result = _run(port, retries=0)
    assert result.returncode == 2, f"stderr: {result.stderr}"
