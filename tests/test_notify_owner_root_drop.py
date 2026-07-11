"""BL-SEC-08: shift-agent-notify-owner must drop root BEFORE importing /opt code.

notify-owner is invoked from ROOT contexts (shift-agent-backup.service runs User=root and calls
it via shift-agent-backup.sh). It does `sys.path.insert(0, "/opt/shift-agent")` then imports
safe_io / schemas / exit_codes. Because /opt/shift-agent is shift-agent-WRITABLE (dir
shift-agent:755), importing that code AS ROOT is a root-RCE vector — a planted safe_io.py runs as
root at import time. The script must re-exec as the shift-agent user BEFORE that import.

Text-invariant (the behavioral path needs actual root + the shift-agent user, so it can't run in
CI); mirrors tests/test_skills_audit_hardening.py, the #584 analog. This is the "every documented
invariant gets a test that fails if it's violated" pattern.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOTIFY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-notify-owner"

_OPT_IMPORT = 'sys.path.insert(0, "/opt/shift-agent")'


def test_notify_owner_guards_on_root_before_opt_import():
    t = NOTIFY.read_text(encoding="utf-8")
    guard = t.find("geteuid")
    opt_import = t.find(_OPT_IMPORT)
    assert guard != -1, "notify-owner must check geteuid()==0 to drop root (BL-SEC-08)"
    assert opt_import != -1, "expected the /opt sys.path.insert to still exist"
    assert guard < opt_import, (
        "the root-drop guard MUST run BEFORE the /opt sys.path.insert — otherwise the "
        "adversary-writable module is imported as root before the drop"
    )


def test_notify_owner_reexecs_as_shift_agent_user():
    t = NOTIFY.read_text(encoding="utf-8")
    assert "runuser" in t and "shift-agent" in t, "must re-exec as the shift-agent user"
    # The re-exec must also precede the /opt import.
    assert t.find("runuser") < t.find(_OPT_IMPORT)
