"""PR-D2 commit 7: catering-lead-reconcile operator script tests.

Static checks pin design v2 §8 + §9.1 B-1 + §9.3 R3-M-1 contract:
- Whitelist: SENT_TO_CUSTOMER, OWNER_REJECTED, CLOSED (per B-1 fix).
- Same-state refusal (R3-M-1).
- Two audit rows per reconcile: CateringLeadStatusChange (operator)
  + CateringLeadManuallyReconciled (PR-D1 schema).
- Forbidden-from-status check.
- --dry-run flag.

Subprocess integration tests (actual reconcile against a tmp leads.json)
defer to Linux CI — needs fcntl.
"""
from __future__ import annotations
from pathlib import Path

import pytest


_RECONCILE = (Path(__file__).resolve().parent.parent / "src" / "agents"
              / "catering" / "scripts" / "catering-lead-reconcile")


@pytest.fixture(scope="module")
def script_text() -> str:
    return _RECONCILE.read_text(encoding="utf-8")


def test_script_exists_and_executable(script_text: str):
    assert _RECONCILE.exists()
    assert script_text.startswith("#!/usr/bin/env python3")


def test_target_status_whitelist(script_text: str):
    """Per design v2 §9.1 B-1: SENT_TO_CUSTOMER, OWNER_REJECTED, CLOSED only."""
    assert 'ALLOWED_TARGETS = ("SENT_TO_CUSTOMER", "OWNER_REJECTED", "CLOSED")' in script_text


def test_no_deleted_status_target(script_text: str):
    """Per design v2 §9.1 B-1: DELETED is NOT in whitelist (not in
    CateringLeadStatus Literal). Use CLOSED instead."""
    # The argparse --target-status uses choices=ALLOWED_TARGETS
    assert '"DELETED"' not in script_text


def test_safe_from_statuses_defined(script_text: str):
    """Reconcile refuses from terminal/unsafe statuses (NEW, EXTRACTING,
    NOT_CATERING, STALE — these are not safe operator-recoverable starts)."""
    assert "SAFE_FROM_STATUSES" in script_text
    # AWAITING_OWNER_APPROVAL + OWNER_APPROVED + OWNER_EDITED + OWNER_REJECTED + SENT_TO_CUSTOMER
    assert "AWAITING_OWNER_APPROVAL" in script_text
    assert "OWNER_APPROVED" in script_text


def test_same_state_refusal_present(script_text: str):
    """Per design v2 §9.3 R3-M-1: refuse same-state target to avoid
    zero-delta audit-log churn."""
    assert "already in target status" in script_text
    assert "from_status == args.target_status" in script_text


def test_emits_two_audit_rows(script_text: str):
    """Per design v2 §8: two audit rows per reconcile invocation:
    CateringLeadStatusChange (actor='operator') + CateringLeadManuallyReconciled."""
    assert 'CateringLeadStatusChange(' in script_text
    assert 'actor="operator"' in script_text
    assert 'CateringLeadManuallyReconciled(' in script_text
    assert 'operator_uid=' in script_text


def test_dry_run_flag_present(script_text: str):
    assert '"--dry-run"' in script_text
    assert 'action="store_true"' in script_text


def test_lock_acquisition_present(script_text: str):
    """Reconcile holds LEADS_LOCK during the entire mutate-and-audit sequence."""
    assert "with FileLock(LEADS_LOCK):" in script_text


def test_uses_load_yaml_model(script_text: str):
    """Reconcile uses PR-D1 chokepoint for config load."""
    assert "load_yaml_model" in script_text
    assert "log_config_load_failed_best_effort" in script_text


# ─────────────── Harness + canary script static checks ───────────────


def test_synthetic_retry_harness_exists():
    p = (Path(__file__).resolve().parent.parent / "tools" / "synthetic-retry-harness.py")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    # Per design v2 §9.2 R4-H-2: NOT installed by deploy.sh, lives in tools/
    assert "monkey-patch" in text or "monkey_patch" in text or "_bridge_post_mock" in text
    # Per R5-H-3: NANP-reserved phone range
    assert "+15555550199" in text or "555-0199" in text
    # Per design §6.2: cleanup via reconcile --target-status CLOSED
    assert "CLOSED" in text
    # Per R3-H-1 + R4-M-1: synthetic message_id prefix
    assert "_synthetic_" in text


def test_canary_bulk_deploy_script_exists():
    p = (Path(__file__).resolve().parent.parent / "tools" / "canary-bulk-deploy.sh")
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    # Per R5-H1: halt-on-failure (set -e) + per-VPS smoke gate before next launch
    assert "set -euo pipefail" in text
    assert "ABORT" in text
    # Per design §6.2: 2-min cooldown AFTER smoke clear
    assert "sleep 120" in text


def test_harness_not_in_deploy_install_globs():
    """Per R4-H-2: tools/synthetic-retry-harness.py must NOT land at
    /usr/local/bin/. Confirm install_artifacts in deploy.sh does NOT
    glob tools/*."""
    deploy_path = (Path(__file__).resolve().parent.parent / "src" / "agents"
                   / "shift" / "scripts" / "shift-agent-deploy.sh")
    text = deploy_path.read_text(encoding="utf-8")
    # tools/* should not appear in any install line
    for line in text.splitlines():
        if line.strip().startswith("install ") and "tools/" in line:
            pytest.fail(f"deploy.sh installs from tools/: {line}")
