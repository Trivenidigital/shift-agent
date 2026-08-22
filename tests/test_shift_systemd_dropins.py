"""The drop-in that wires the screening gate must ship with the binary it wires.

`/usr/local/bin/shift-agent-policy-preflight` ships from this repo and is
reinstalled on every deploy. Until 2026-08-22 the drop-in that wires it as
`ExecStartPre` did not ship at all — no `.conf` was tracked here and the deploy
installed none, while the box carried 19 across 11 services. A rebuilt box would
have had the gate on disk and never invoked, and per the drop-in's own comment
that hands WhatsApp back to the stock UNSCREENED adapter with no error.

These tests hold three things:

* the wiring ships, and says what it must say;
* the install is **additive and non-destructive** — the deploy may create and may
  leave alone, but must never delete, purge, or overwrite a differing file,
  because most drop-ins on that box belong to another tool;
* the ownership reasoning stays written down, per file, including the files
  deliberately NOT tracked.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DROPIN_DIR = REPO / "src" / "platform" / "systemd" / "hermes-gateway.service.d"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
RUNBOOK = REPO / "docs" / "runbooks" / "systemd-dropins.md"
POLICY_DROPIN = DROPIN_DIR / "30-shift-agent-policy-preflight.conf"
GATEWAY_UNIT = REPO / "src" / "platform" / "systemd" / "hermes-gateway.service"


# ── the wiring ships, and does its job ───────────────────────────────────

def test_the_policy_preflight_dropin_is_tracked():
    """The gap this PR closes. The binary shipped; this did not."""
    assert POLICY_DROPIN.is_file(), (
        "shift-agent-policy-preflight ships from this repo but the drop-in that "
        "wires it as ExecStartPre does not — a rebuilt box would have the "
        "screening gate installed and never invoked")


def test_the_policy_dropin_actually_wires_the_binary():
    """A tracked file that forgot its ExecStartPre would be worse than none —
    it would look like the gap was closed."""
    body = POLICY_DROPIN.read_text(encoding="utf-8")
    assert "ExecStartPre=/usr/local/bin/shift-agent-policy-preflight" in body
    assert "[Service]" in body


def test_the_policy_dropin_is_fail_closed_no_leading_dash():
    """`ExecStartPre=-` would tell systemd to ignore the exit status, silently
    converting the screening gate into a suggestion. The read-tools preflight
    uses the dash deliberately; this one must never."""
    body = POLICY_DROPIN.read_text(encoding="utf-8")
    assert "ExecStartPre=-" not in body, (
        "a leading '-' would make systemd ignore a screening failure and start "
        "the gateway anyway")


def test_the_policy_dropin_matches_the_deployed_box_byte_for_byte():
    """Recorded so tracking it is provably a no-op on the current box.

    sha256 read from /etc/systemd/system/hermes-gateway.service.d/ at deploy
    24c1f1d5. If this changes, the repo and the box have diverged and the
    deploy will refuse to overwrite — which is the intended behaviour, but the
    divergence needs a human.
    """
    import hashlib
    digest = hashlib.sha256(POLICY_DROPIN.read_bytes()).hexdigest()
    assert digest == (
        "33c020364997da3c342bb23cb12f8d89318fc6a4ba00a50f05ce0907a0c0b805"), (
        f"drifted from the box copy recorded 2026-08-22: {digest}")


def test_every_tracked_dropin_is_for_a_unit_this_repo_defines():
    """Ownership rule 3. A drop-in for someone else's unit does not belong here."""
    for d in (REPO / "src" / "platform" / "systemd").glob("*.service.d"):
        unit = d.name[: -len(".d")]
        assert (REPO / "src" / "platform" / "systemd" / unit).is_file(), (
            f"{d.name} configures {unit}, which this repo does not define")


# ── the install is additive and non-destructive ──────────────────────────

def _deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_the_deploy_installs_tracked_dropins():
    assert "install_tracked_dropins" in _deploy_text()


def test_the_dropin_install_never_overwrites_a_differing_file():
    """Rule 2. A box copy that differs may be a deliberate hand edit, and the
    deploy cannot tell — so it reports and leaves it."""
    body = _deploy_text()
    fn = body[body.index("install_tracked_dropins() {"):]
    fn = fn[: fn.index("\n    }")]
    assert "cmp -s" in fn, "must byte-compare before deciding"
    assert "DIFFERS" in fn, "must report a differing box copy"
    assert "NOT overwriting" in fn


def test_the_dropin_install_never_deletes_or_purges():
    """Rule 1. Most drop-ins on the box are codex's. Removing another tool's
    config is out of bounds, so the installer must contain no destructive verb."""
    body = _deploy_text()
    fn = body[body.index("install_tracked_dropins() {"):]
    fn = fn[: fn.index("\n    }")]
    for verb in ("rm ", "rm -", "unlink", "shred", "find -delete", "--delete"):
        assert verb not in fn, f"destructive verb {verb!r} in the drop-in installer"


def test_the_installer_only_reads_the_repo_owned_dropin_path():
    """It must not glob the box's /etc/systemd/system/*.service.d — that would
    sweep in codex's directories."""
    body = _deploy_text()
    fn = body[body.index("install_tracked_dropins() {"):]
    fn = fn[: fn.index("\n    }")]
    assert "src/platform/systemd/*.service.d" in fn
    assert "/etc/systemd/system/*.service.d" not in fn


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="needs a git checkout")
def test_the_deploy_script_still_parses():
    """A shell syntax error here bricks every deploy, not just this feature."""
    proc = subprocess.run(["bash", "-n", str(DEPLOY)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ── the reasoning stays written down ─────────────────────────────────────

def test_the_runbook_records_ownership_per_file():
    assert RUNBOOK.is_file()
    body = RUNBOOK.read_text(encoding="utf-8")
    for name in ("30-shift-agent-policy-preflight.conf", "20-drain-timeout.conf",
                 "10-telegram-onfailure.conf", "10-codex-worker-root.conf",
                 "20-flyer-integrated-poster.conf"):
        assert name in body, f"{name} has no recorded ownership decision"


def test_the_runbook_records_the_root_escalation_finding():
    """The flyer recovery watchdog runs as root on the box while the repo unit
    says shift-agent. Deliberately not tracked — a privilege decision belongs to
    the operator — but it must not be lost."""
    body = RUNBOOK.read_text(encoding="utf-8")
    assert "10-codex-worker-root.conf" in body
    assert "User=root" in body
    assert "flyer-recovery-watchdog.service:8-10" in body, (
        "the runbook must cite where the repo unit disagrees with the box")


def test_the_repo_unit_still_says_shift_agent_not_root():
    """Pins the disagreement the runbook describes. If someone 'fixes' it by
    committing root into the unit, that is a privilege change and this fails so
    it gets reviewed as one."""
    unit = (REPO / "src" / "agents" / "flyer" / "systemd"
            / "flyer-recovery-watchdog.service").read_text(encoding="utf-8")
    assert re.search(r"^User=shift-agent$", unit, re.M), (
        "flyer-recovery-watchdog.service no longer runs as shift-agent — if the "
        "root escalation was adopted deliberately, update the runbook finding too")


def test_untracked_by_design_files_are_not_quietly_added():
    """The four drop-ins judged NOT ours must stay out of the tracked directory.
    Adding one later without updating the runbook would bury the reasoning."""
    tracked = {p.name for p in DROPIN_DIR.glob("*.conf")}
    for name in ("10-telegram-onfailure.conf", "20-flyer-integrated-poster.conf"):
        assert name not in tracked, (
            f"{name} is documented as deliberately untracked; if that changed, "
            f"update docs/runbooks/systemd-dropins.md in the same commit")


def test_the_base_unit_still_makes_the_flyer_dropin_redundant():
    """The stated reason for not tracking 20-flyer-integrated-poster.conf is
    that the base unit already sets it. If that line goes, the reason evaporates
    and the drop-in must be reconsidered."""
    unit = GATEWAY_UNIT.read_text(encoding="utf-8")
    assert "Environment=FLYER_ALLOW_INTEGRATED_POSTER=1" in unit, (
        "the base unit no longer sets FLYER_ALLOW_INTEGRATED_POSTER, so the "
        "untracked drop-in is no longer redundant — revisit the runbook")
