"""oserror-surfacing tests for catering writer scripts (silent-failure-hunter NEW-1).

Linux-only: depends on _b1_helpers (fcntl + hyphen-script importlib).

Tests assert that when leads.json exists but is unreadable (PermissionError →
safe_load_json returns 'oserror:...'), writer scripts exit with
EXIT_SCHEMA_VIOLATION (5) rather than silently falling through to a fresh
empty store. The latter would either lose data (create-lead minting a
duplicate L0001) or silently treat the lead as missing (apply-decision
returning EXIT_NOT_FOUND with confused operator stderr).
"""
from __future__ import annotations

import os
import platform
import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="catering scripts depend on safe_io's fcntl path (Linux only)",
)

from _b1_helpers import (  # noqa: E402
    BridgeStub, make_env_dir, run_create, run_apply,
)


@pytest.fixture
def bridge_server():
    """Per-test ephemeral HTTP bridge stub. Yields (port, BridgeStubClass)."""
    import threading
    from http.server import HTTPServer

    BridgeStub.requests = []
    server = HTTPServer(("127.0.0.1", 0), BridgeStub)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield port, BridgeStub
    finally:
        server.shutdown()
        server.server_close()


def _seed_basic_lead(env_dir, port):
    """Helper: create one lead so apply-decision tests have a real code to use."""
    r = run_create(
        env_dir, port,
        {"headcount": 50, "event_date": "2030-12-15"},
        customer_phone="+19045551234", customer_name="Test",
        message_id="MID_BASE",
    )
    assert r.returncode == 0, r.stderr
    import json as _j
    return _j.loads(r.stdout)["approval_code"]


def _skip_root_permission_probe() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("chmod(000) permission-denied probes are not valid when pytest runs as root")


def test_apply_decision_surfaces_oserror_on_unreadable_leads_file(tmp_path, bridge_server):
    """Unreadable leads.json (PermissionError) → EXIT_SCHEMA_VIOLATION (5),
    not the silent EXIT_NOT_FOUND fall-through that would happen if the
    'oserror:' status were treated as a fresh-empty-store signal."""
    env_dir = make_env_dir(tmp_path)
    port, _ = bridge_server
    code = _seed_basic_lead(env_dir, port)
    _skip_root_permission_probe()

    leads = env_dir / "state" / "catering-leads.json"
    leads.chmod(0o000)
    try:
        result = run_apply(env_dir, port, code, "reject", reason="permission probe")
        assert result.returncode == 5, (result.returncode, result.stderr)
        s = result.stderr.lower()
        assert any(k in s for k in ("oserror", "permission", "unhealthy")), s
    finally:
        leads.chmod(0o644)


def test_apply_decision_normal_path_unchanged(tmp_path, bridge_server):
    """Sanity: oserror guard doesn't break normal create+approve flow."""
    env_dir = make_env_dir(tmp_path)
    port, _ = bridge_server
    code = _seed_basic_lead(env_dir, port)
    result = run_apply(env_dir, port, code, "reject", reason="sanity")
    assert result.returncode == 0, result.stderr


def test_create_lead_surfaces_oserror_on_unreadable_leads_file(tmp_path, bridge_server):
    """Same fix on create-catering-lead. Without this, an unreadable leads.json
    would silently produce a fresh empty store and mint a duplicate L0001 on
    first attempt, then potentially overwrite real data when the file became
    readable again."""
    env_dir = make_env_dir(tmp_path)
    port, _ = bridge_server
    _skip_root_permission_probe()
    leads = env_dir / "state" / "catering-leads.json"
    leads.parent.mkdir(parents=True, exist_ok=True)
    leads.write_text('{"schema_version": 1, "leads": []}', encoding="utf-8")
    leads.chmod(0o000)
    try:
        result = run_create(
            env_dir, port,
            {"headcount": 50, "event_date": "2030-12-15"},
            customer_phone="+19045551234", customer_name="Test",
            message_id="MID_OSERR_1",
        )
        assert result.returncode == 5, (result.returncode, result.stderr)
        s = result.stderr.lower()
        assert any(k in s for k in ("oserror", "permission", "unhealthy")), s
    finally:
        leads.chmod(0o644)


def test_create_lead_normal_path_unchanged(tmp_path, bridge_server):
    """Sanity: create-lead oserror guard doesn't break normal path."""
    env_dir = make_env_dir(tmp_path)
    port, _ = bridge_server
    result = run_create(
        env_dir, port,
        {"headcount": 50, "event_date": "2030-12-15"},
        customer_phone="+19045551234", customer_name="Sanity",
        message_id="MID_SANITY",
    )
    assert result.returncode == 0, result.stderr
