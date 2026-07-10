"""Invariants for the D2 skills-audit watchdog trust-domain hardening (PR #583 follow-up).

These encode the security property: the watchdog must run OUTSIDE the shift-agent (gateway)
trust domain, or a compromised gateway could poison its config/inputs/checker and evade it.
Each assertion maps to a specific #583 security-review bypass. Directive checks parse the
active (non-comment) systemd lines so they don't trip on words used in explanatory comments.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SVC = REPO / "src" / "agents" / "shift" / "systemd" / "shift-agent-skills-audit.service"
SH = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-skills-audit.sh"


def _svc_directives() -> list[str]:
    return [ln.strip() for ln in SVC.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def _sh() -> str:
    return SH.read_text(encoding="utf-8")


def test_service_runs_as_root_not_shift_agent():
    d = _svc_directives()
    assert "User=root" in d, "watchdog must run as root, not the gateway's uid"
    assert "User=shift-agent" not in d


def test_service_does_not_source_env():
    # Sourcing /opt/shift-agent/.env (shift-agent-owned) would let a compromised gateway
    # inject config like SKILLS_MANIFEST_FILE=/dev/null — bypass #1 in the #583 review.
    assert not any(x.startswith("EnvironmentFile") for x in _svc_directives())


def test_service_uses_root_owned_state_dir():
    # Root-owned throttle dir (systemd StateDirectory) — bypass #2 (throttle poisoning).
    assert "StateDirectory=shift-agent-skills-audit" in _svc_directives()


def test_service_pins_working_directory_root():
    # Inline `python3 -c` helpers prepend CWD to sys.path; a shift-agent-writable CWD could
    # shadow stdlib `import json` and hijack this ROOT process. Pinning / (root-owned) prevents
    # a future edit from reintroducing the hijack.
    assert "WorkingDirectory=/" in _svc_directives()


def test_watchdog_reads_root_owned_inputs_not_opt_or_env():
    t = _sh()
    assert "SHARE=/usr/local/share/shift-agent" in t, "inputs must come from the root-owned dir"
    # Must NOT read the adversary-writable staging/opt manifest — bypass #3 (manifest poisoning).
    assert "/opt/shift-agent/staging-new/tools/skills-manifest.txt" not in t
    # No env override for the security-critical manifest path.
    assert "SKILLS_MANIFEST_FILE" not in t


def test_watchdog_runs_root_owned_module_directly():
    # Must run the root-owned self-contained module, NOT the /opt-importing CLI wrapper
    # (else the adversary rewrites /opt/shift-agent/skills_manifest.py to neuter the checker).
    t = _sh()
    assert 'MODULE="$SHARE/skills_manifest.py"' in t
    assert "/usr/local/bin/check-skills-manifest" not in t


def test_watchdog_drops_privilege_for_alert_delivery():
    # notify-owner imports adversary-writable /opt code; running it as ROOT would be a root-RCE
    # vector. The root watchdog must drop to shift-agent (runuser) for DELIVERY, and must not
    # invoke notify-owner bare as root.
    t = _sh()
    assert "runuser -u shift-agent" in t
    assert "$DROP /usr/local/bin/shift-agent-notify-owner" in t
