"""Batch 1 — safety-hardening invariants (BL-SEC-07, BL-SHIFT-05, BL-SHIFT-10).

Text-based, cross-platform (no fcntl/subprocess) — these run everywhere.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHIFT = REPO / "src" / "agents" / "shift"
BACKUP_SVC = SHIFT / "systemd" / "shift-agent-backup.service"
CAND_TMPL = SHIFT / "templates" / "coverage_message_to_candidate.txt"
OWNER_TMPL = SHIFT / "templates" / "proposal_to_owner.txt"
OWNER_CMD = SHIFT / "skills" / "handle_owner_command" / "SKILL.md"


def _svc_directives() -> list[str]:
    return [ln.strip() for ln in BACKUP_SVC.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


# ── BL-SEC-07: root backup service pins CWD ───────────────────────────────────

def test_backup_service_pins_working_directory_root():
    # backup.sh runs `python3 -c` as root; CWD-on-sys.path could be hijacked without this pin.
    assert "WorkingDirectory=/" in _svc_directives()


# ── BL-SHIFT-05: coworker health reason not leaked to the candidate ───────────

def test_candidate_message_does_not_leak_absent_reason():
    # The coworker asked to cover must NOT receive the absent employee's reason.
    assert "absent_reason_short" not in CAND_TMPL.read_text(encoding="utf-8")


def test_owner_proposal_still_shows_reason():
    # The owner (and only the owner) still sees the reason — we only dropped it from the candidate.
    assert "absent_reason_short" in OWNER_TMPL.read_text(encoding="utf-8")


# ── BL-SHIFT-10: KILL requires confirmation + not advertised in routine footer ─

def test_proposal_footer_does_not_advertise_kill():
    # A single-word KILL must not sit in every routine proposal (fat-finger / forwarded-quote hazard).
    assert "KILL" not in OWNER_TMPL.read_text(encoding="utf-8")


def test_owner_command_requires_kill_confirm():
    t = OWNER_CMD.read_text(encoding="utf-8")
    assert "KILL CONFIRM" in t
    # bare-KILL handling must be documented as a guard (no disable on bare KILL)
    assert "do NOT disable" in t
