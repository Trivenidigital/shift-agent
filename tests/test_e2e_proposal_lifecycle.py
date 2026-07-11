"""End-to-end proposal lifecycle test — invokes actual helper scripts.

Requires the scripts to be installed at /usr/local/bin/ on the test machine
AND /opt/shift-agent/{config.yaml, roster.json, schemas.py, safe_io.py,
exit_codes.py, venv/} in place. Runs only if those exist; skipped otherwise
so the suite can run on a dev box without the deployed tree.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
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
]

def _tools_deployed() -> bool:
    return (
        all(os.access(b, os.X_OK) for b in REQUIRED_BINS)
        and all(p.exists() for p in REQUIRED_STATE)
    )


pytestmark = pytest.mark.skipif(
    not _tools_deployed(),
    reason="Shift Agent not deployed at /usr/local/bin and /opt/shift-agent",
)

PENDING = Path("/opt/shift-agent/state/pending.json")
DISABLED_FLAG = Path("/opt/shift-agent/state/disabled.flag")
SEND_COUNTER = Path("/opt/shift-agent/state/send-counter.json")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# census C1 2026-07-11: the deployed binaries run as user shift-agent via sudo,
# which strips the parent env — so the pytest process' SHIFT_AGENT_DECISIONS_LOG_PATH
# (set by conftest) never reaches them and their audit rows historically landed
# in the production chokepoint (209 dry-run proposal rows). Redirect them to a
# shift-agent-writable tmp file by passing the var explicitly through `env`.
# The holder is filled by the autouse fixture below.
_TEST_DECISIONS_LOG = [None]


@pytest.fixture(autouse=True)
def _redirect_agent_audit_log():
    """World-writable tmp decisions.log so the sudo'd (shift-agent-user)
    binaries don't write to the production audit chokepoint. The test process
    is root; chmod 0777 lets the shift-agent user create + append the file."""
    d = tempfile.mkdtemp(prefix="shift-agent-e2e-audit-")
    os.chmod(d, 0o777)
    _TEST_DECISIONS_LOG[0] = os.path.join(d, "decisions.log")
    try:
        yield
    finally:
        shutil.rmtree(d, ignore_errors=True)
        _TEST_DECISIONS_LOG[0] = None


def _agent(bin_and_args, extra_env=()):
    """Build a `sudo -u shift-agent env SHIFT_AGENT_DECISIONS_LOG_PATH=... <bin>`
    command so the deployed binary's audit rows land in the test tmp log."""
    env = ["env", f"SHIFT_AGENT_DECISIONS_LOG_PATH={_TEST_DECISIONS_LOG[0]}", *extra_env]
    return ["sudo", "-u", "shift-agent"] + env + list(bin_and_args)


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


@pytest.fixture
def fresh_send_counter():
    """Snapshot + zero send-counter so cap-bound tests run hermetically.

    Needed by any test that exercises send-coverage-message — including dry-run —
    because the counter persists across runs and a real day's sends would leave
    it at cap, causing EXIT_CAP_EXCEEDED for subsequent test invocations.
    """
    backup = SEND_COUNTER.read_text() if SEND_COUNTER.exists() else None
    if SEND_COUNTER.exists():
        SEND_COUNTER.unlink()
    try:
        yield
    finally:
        if backup is not None:
            SEND_COUNTER.write_text(backup)


@pytest.fixture
def temporarily_enabled():
    """Snapshot + remove disabled.flag for the test window, then restore.

    Lets dry-run tests execute the real enable-path without leaving the VPS
    permanently enabled after the test. Restore is unconditional (finally) so
    a crash mid-test still puts the flag back.
    """
    backup = DISABLED_FLAG.read_text() if DISABLED_FLAG.exists() else None
    backup_mode = DISABLED_FLAG.stat().st_mode if DISABLED_FLAG.exists() else None
    if backup is not None:
        DISABLED_FLAG.unlink()
    try:
        yield
    finally:
        if backup is not None:
            DISABLED_FLAG.write_text(backup)
            if backup_mode is not None:
                os.chmod(str(DISABLED_FLAG), backup_mode)


def test_full_proposal_lifecycle(clean_pending):
    """Happy path: create → approve → reconciling → sent → accepted."""
    # Start fresh
    PENDING.unlink(missing_ok=True)

    # 1. create-proposal
    r = _run(_agent([
        "/usr/local/bin/create-proposal",
        "--absent-employee-id", "e001",
        "--absent-date", "2026-04-25",
        "--absent-shift", "09:00-17:00",
        "--absent-role", "cashier",
        "--absent-reason", "fever",
        "--input-message", "Boss, Ravi can't come tomorrow",
        "--message-id", "test-e2e-001",
        "--candidate-employee-id", "e004",
        "--candidate-name", "Anjali Iyer",
    ]))
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
        r = _run(_agent(["/usr/local/bin/update-proposal-status"] + args))
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
    r = _run(_agent([
        "/usr/local/bin/create-proposal",
        "--absent-employee-id", "e002",
        "--absent-date", "2026-04-25",
        "--absent-shift", "06:00-14:00",
        "--absent-role", "bakery",
        "--absent-reason", "test",
        "--input-message", "test",
        "--message-id", "test-e2e-illegal-001",
    ]))
    assert r.returncode == 0
    pid = json.loads(r.stdout)["proposal_id"]

    # Walk to sent
    for args in [
        [pid, "approved", "--cause", "t", "--actor", "owner"],
        [pid, "reconciling", "--cause", "t", "--actor", "agent", "--reconciling-pid", "1"],
        [pid, "sent", "--cause", "t", "--actor", "agent", "--outbound-message-id", "x"],
    ]:
        _run(_agent(["/usr/local/bin/update-proposal-status"] + args), check=False)

    # Now try sent → approved (illegal)
    r = _run(_agent([
        "/usr/local/bin/update-proposal-status",
        pid, "approved", "--cause", "illegal_test", "--actor", "owner",
    ]))
    assert r.returncode == 9, f"expected EXIT_ILLEGAL_TRANSITION=9, got {r.returncode}: {r.stderr}"


def test_identify_sender_resolves_phase0_roster():
    """A.2 regression: every Phase 0 employee phone resolves correctly.

    Reads the live roster.json to avoid breaking when phones are rotated for
    rehearsals. Only asserts e001 in canonical, dashed, and JID forms — those
    are stable across config changes.
    """
    roster = json.loads(Path("/opt/shift-agent/roster.json").read_text())
    e001 = next(e for e in roster["employees"] if e["id"] == "e001")
    e001_phone = e001["phone"]  # e.g., "+19045550101"
    digits = e001_phone.lstrip("+")
    dashed = e001_phone[:2] + "-" + e001_phone[2:5] + "-" + e001_phone[5:8] + "-" + e001_phone[8:]
    for phone in [e001_phone, dashed, f"{digits}@s.whatsapp.net"]:
        r = _run(["/usr/local/bin/identify-sender", phone])
        assert r.returncode == 0, f"{phone}: {r.stderr}"
        out = json.loads(r.stdout)
        assert out["role"] == "employee"
        assert out["employee_id"] == "e001"


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


def test_dry_run_skips_bridge_but_still_transitions(clean_pending, temporarily_enabled, fresh_send_counter):
    """Priority-4: SHIFT_AGENT_DRY_RUN=1 suppresses bridge POST, still advances pending."""
    PENDING.unlink(missing_ok=True)

    # 1. create → approved
    r = _run(_agent([
        "/usr/local/bin/create-proposal",
        "--absent-employee-id", "e001",
        "--absent-date", "2026-04-25",
        "--absent-shift", "09:00-17:00",
        "--absent-role", "cashier",
        "--absent-reason", "dry-run-test",
        "--input-message", "dry run",
        "--message-id", "test-dryrun-001",
        "--candidate-employee-id", "e004",
        "--candidate-name", "Anjali Iyer",
    ]))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    pid = out["proposal_id"]
    code = out["code"]

    r = _run(_agent([
        "/usr/local/bin/update-proposal-status",
        pid, "approved", "--cause", "dry-run-test", "--actor", "owner",
        "--owner-input", code,
    ]))
    assert r.returncode == 0, r.stderr

    # 2. send with SHIFT_AGENT_DRY_RUN=1 — must succeed even though bridge is down/disabled
    r = subprocess.run(
        _agent(["/usr/local/bin/send-coverage-message", pid],
               extra_env=["SHIFT_AGENT_DRY_RUN=1"]),
        capture_output=True, text=True,
    )
    assert r.returncode == 0, f"dry-run send failed: stdout={r.stdout} stderr={r.stderr}"
    assert "DRY-RUN" in r.stdout, f"expected DRY-RUN marker in stdout: {r.stdout}"
    assert "DRY-RUN-" in r.stdout, "expected DRY-RUN- prefixed synthetic msg_id"

    # 3. pending advanced to 'sent' and audit log has OutboundSent with DRY-RUN- msg_id
    with PENDING.open() as f:
        store = json.load(f)
    p = store["proposals"][pid]
    assert p["status"] == "sent", f"expected sent, got {p['status']}"
    assert p["outbound_message_id"].startswith("DRY-RUN-"), (
        f"expected DRY-RUN- prefix, got {p['outbound_message_id']}"
    )
