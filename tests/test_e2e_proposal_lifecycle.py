"""End-to-end proposal lifecycle test — invokes actual helper scripts.

Requires the scripts to be installed at /usr/local/bin/ on the test machine
AND /opt/shift-agent/{config.yaml, roster.json, schemas.py, safe_io.py,
exit_codes.py, venv/} in place. Runs only if those exist; skipped otherwise
so the suite can run on a dev box without the deployed tree.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REQUIRED_BINS = [
    "/usr/local/bin/create-proposal",
    "/usr/local/bin/update-proposal-status",
    "/usr/local/bin/identify-sender",
    "/usr/local/bin/render-coverage-template",
    "/usr/local/bin/log-decision-direct",
]
REQUIRED_STATE = [
    Path("/opt/shift-agent/config.yaml"),
    Path("/opt/shift-agent/roster.json"),
    Path("/opt/shift-agent/venv/bin/python3"),
]


def _tools_deployed() -> bool:
    return all(os.access(b, os.X_OK) for b in REQUIRED_BINS) and all(p.exists() for p in REQUIRED_STATE)


pytestmark = pytest.mark.skipif(
    not _tools_deployed(),
    reason="Shift Agent not deployed at /usr/local/bin and /opt/shift-agent",
)

PENDING = Path("/opt/shift-agent/state/pending.json")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


@pytest.fixture
def clean_pending():
    """Snapshot + restore pending.json so tests don't corrupt the deploy."""
    backup = None
    if PENDING.exists():
        backup = PENDING.read_text()
    yield
    if backup is not None:
        PENDING.write_text(backup)
    else:
        PENDING.unlink(missing_ok=True)


def test_full_proposal_lifecycle(clean_pending):
    """Happy path: create → approve → reconciling → sent → accepted."""
    # Start fresh
    PENDING.unlink(missing_ok=True)

    # 1. create-proposal
    r = _run([
        "sudo", "-u", "shift-agent", "/usr/local/bin/create-proposal",
        "--absent-employee-id", "e001",
        "--absent-date", "2026-04-25",
        "--absent-shift", "09:00-17:00",
        "--absent-role", "cashier",
        "--absent-reason", "fever",
        "--input-message", "Boss, Ravi can't come tomorrow",
        "--message-id", "test-e2e-001",
        "--candidate-employee-id", "e004",
        "--candidate-name", "Anjali Iyer",
    ])
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    pid = out["proposal_id"]
    code = out["code"]
    assert pid.startswith("P")
    assert code.startswith("#") and len(code) == 6

    # 2. transitions
    for args in [
        [pid, "approved", "--cause", "test", "--actor", "owner", "--owner-input", code],
        [pid, "reconciling", "--cause", "test", "--actor", "agent", "--reconciling-pid", "12345"],
        [pid, "sent", "--cause", "test", "--actor", "agent", "--outbound-message-id", "msg-out-001"],
        [pid, "accepted", "--cause", "test", "--actor", "candidate", "--response-message", "yes"],
    ]:
        r = _run(["sudo", "-u", "shift-agent", "/usr/local/bin/update-proposal-status"] + args)
        assert r.returncode == 0, f"transition failed: {args} — {r.stderr}"

    # 3. verify final state
    with PENDING.open() as f:
        store = json.load(f)
    p = store["proposals"][pid]
    assert p["status"] == "accepted"
    assert len(p["status_history"]) == 5  # create + 4 transitions


def test_illegal_transition_rejected(clean_pending):
    """sent → approved must be refused with exit 9."""
    PENDING.unlink(missing_ok=True)
    r = _run([
        "sudo", "-u", "shift-agent", "/usr/local/bin/create-proposal",
        "--absent-employee-id", "e002",
        "--absent-date", "2026-04-25",
        "--absent-shift", "06:00-14:00",
        "--absent-role", "bakery",
        "--absent-reason", "test",
        "--input-message", "test",
        "--message-id", "test-e2e-illegal-001",
    ])
    assert r.returncode == 0
    pid = json.loads(r.stdout)["proposal_id"]

    # Walk to sent
    for args in [
        [pid, "approved", "--cause", "t", "--actor", "owner"],
        [pid, "reconciling", "--cause", "t", "--actor", "agent", "--reconciling-pid", "1"],
        [pid, "sent", "--cause", "t", "--actor", "agent", "--outbound-message-id", "x"],
    ]:
        _run(["sudo", "-u", "shift-agent", "/usr/local/bin/update-proposal-status"] + args, check=False)

    # Now try sent → approved (illegal)
    r = _run([
        "sudo", "-u", "shift-agent", "/usr/local/bin/update-proposal-status",
        pid, "approved", "--cause", "illegal_test", "--actor", "owner",
    ])
    assert r.returncode == 9, f"expected EXIT_ILLEGAL_TRANSITION=9, got {r.returncode}: {r.stderr}"


def test_identify_sender_resolves_phase0_roster():
    """A.2 regression: every Phase 0 employee phone resolves correctly."""
    for phone, expected in [
        ("+19045550101", "e001"),
        ("+1-904-555-0101", "e001"),  # dashed
        ("19045550101@s.whatsapp.net", "e001"),  # JID
        ("+19045550104", "e004"),
    ]:
        r = _run(["/usr/local/bin/identify-sender", phone])
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["role"] == "employee"
        assert out["employee_id"] == expected


def test_identify_sender_exit_2_on_garbage():
    """Priority-1 regression: garbage input exits 2."""
    r = _run(["/usr/local/bin/identify-sender", "garbage_not_a_phone"])
    assert r.returncode == 2


def test_identify_sender_blocks_shell_injection():
    r = _run(["/usr/local/bin/identify-sender", "+19045550101; rm -rf /"])
    assert r.returncode == 2


def test_render_template_blocks_path_traversal():
    r = _run([
        "sudo", "-u", "shift-agent", "/usr/local/bin/render-coverage-template",
        "../../etc/passwd",
        "--fields-json", "{}",
    ])
    assert r.returncode == 2
    assert "escapes templates dir" in r.stderr or "not found" in r.stderr


def test_log_decision_rejects_no_type_field():
    """Priority-1 regression: legacy compat path now rejects."""
    r = _run(["/usr/local/bin/log-decision", '{"no_type": "here"}'])
    assert r.returncode == 5
