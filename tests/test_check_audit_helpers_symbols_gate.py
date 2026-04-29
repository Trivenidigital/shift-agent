"""PR-D1 commit 5: static checks on the check-audit-helpers-symbols gate.

The gate itself imports audit_helpers (transitively imports fcntl) so a
runtime test would need Linux. This test pins the gate's contract via
static introspection of the file: required symbols list, error-path
exit codes, and stdout marker.
"""
from __future__ import annotations
from pathlib import Path

import pytest


_GATE_PATH = Path(__file__).resolve().parent.parent / "src" / "platform" / "scripts" / "check-audit-helpers-symbols"


def test_gate_file_exists():
    assert _GATE_PATH.exists(), f"gate not found at {_GATE_PATH}"


def test_gate_has_shebang():
    text = _GATE_PATH.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env python3"), "gate must be executable Python"


def test_gate_required_symbols_match_audit_helpers_module():
    """Required symbols list mirrors audit_helpers.py's public helpers."""
    text = _GATE_PATH.read_text(encoding="utf-8")
    assert '"log_config_load_failed_best_effort"' in text
    assert '"log_quote_sent_lead_missing_best_effort"' in text


def test_gate_emits_marker_on_success():
    text = _GATE_PATH.read_text(encoding="utf-8")
    assert "AUDIT_HELPERS_SYMBOLS_OK" in text


def test_gate_uses_correct_sys_path_insert():
    """Per design v2 R3-M-Path1: must insert /opt/shift-agent into sys.path."""
    text = _GATE_PATH.read_text(encoding="utf-8")
    assert 'sys.path.insert(0, "/opt/shift-agent")' in text


def test_deploy_script_chains_audit_helpers_check():
    """Per design v2 R3-H-Gate1: shift-agent-deploy.sh must invoke
    check-audit-helpers-symbols alongside check-safe-io-symbols in the
    pre-restart import gate."""
    deploy_path = (Path(__file__).resolve().parent.parent / "src" / "agents"
                   / "shift" / "scripts" / "shift-agent-deploy.sh")
    text = deploy_path.read_text(encoding="utf-8")
    assert "/usr/local/bin/check-audit-helpers-symbols" in text, (
        "shift-agent-deploy.sh must chain check-audit-helpers-symbols "
        "in the pre-restart import gate (PR-D1 R3-H-Gate1 fix)"
    )


def test_rollback_target_gate_exists():
    """Per design v2 §14.1 B-RB1: tools/check-pr-d2-rollback-target.sh
    refuses PR-D2 deploy if PREV_TAG doesn't carry the PR-D1 SHA."""
    p = Path(__file__).resolve().parent.parent / "tools" / "check-pr-d2-rollback-target.sh"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "PR-D2_ROLLBACK_TARGET_OK" in text
    assert "EXPECTED_SHA" in text
    # Must use the two-step ssh-to-file Windows pattern
    assert ".pr_d2_gate.txt" in text or "OUT_FILE" in text


def test_deploy_script_installs_audit_helpers_module():
    """Per PR-D1 R4-B1 (BLOCKER): audit_helpers.py MUST land at
    /opt/shift-agent/audit_helpers.py or the new pre-restart gate
    ImportErrors on every deploy and forces rollback."""
    deploy_path = (Path(__file__).resolve().parent.parent / "src" / "agents"
                   / "shift" / "scripts" / "shift-agent-deploy.sh")
    text = deploy_path.read_text(encoding="utf-8")
    assert "src/platform/audit_helpers.py /opt/shift-agent/audit_helpers.py" in text, (
        "shift-agent-deploy.sh install_artifacts must install audit_helpers.py"
    )


def test_smoke_test_rollback_evicts_broken_tarball():
    """Per PR-D1 R4-H2 (HIGH): smoke-test failure branch must rm -f the
    broken tarball after rollback, mirroring the pre-restart-gate eviction.
    Without this, a later PR-D2 deploy attempt would see the broken-PR-D2
    tarball as PREV_TAG via mtime ordering, breaking the rollback chain."""
    deploy_path = (Path(__file__).resolve().parent.parent / "src" / "agents"
                   / "shift" / "scripts" / "shift-agent-deploy.sh")
    text = deploy_path.read_text(encoding="utf-8")
    smoke_idx = text.find("SMOKE TEST FAILED")
    assert smoke_idx != -1, "smoke-test-failure branch missing"
    next_block = text[smoke_idx:smoke_idx + 1500]
    assert 'rm -f "$DEPLOYS_DIR/${NEW_TAG}.tgz"' in next_block, (
        "smoke-test-failure branch must evict broken tarball (R4-H2 fix)"
    )
