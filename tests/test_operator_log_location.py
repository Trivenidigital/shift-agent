"""Operator-truth invariants for the gateway's operational-error sink.

The gateway unit sets `StandardError=append:<path>`, so the ~17
`sys.stderr.write` calls in `src/plugins/cf-router/` land in a FILE — not in
journald. `journalctl -u hermes-gateway` shows only systemd's own lifecycle
lines (Started / Stopped / "Main process exited"). Verified on main-vps
2026-08-23: a string present 5x in the file matched 0x in 7 days of journald.

An operator told to run `journalctl -u hermes-gateway` to find an application
error therefore reads an empty result and concludes nothing failed. That is a
confidently-wrong answer, which is worse than no instruction at all.

These tests mechanize the two halves of the repair so neither can rot:

1. The documented path is DERIVED from the unit file, so changing the unit
   without updating the runbook fails here rather than in production.
2. No live operator surface names `journalctl -u hermes-gateway` without
   naming the log file beside it. journalctl is still the right tool for unit
   lifecycle; it is never sufficient on its own.

Scope is `docs/` and `src/` — the surfaces an operator is pointed at today.
`tasks/` is deliberately excluded: it holds dated audits and working notes
whose value is being an accurate record of what was believed at the time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
GATEWAY_UNIT = REPO_ROOT / "src" / "platform" / "systemd" / "hermes-gateway.service"

# How far from a `journalctl -u hermes-gateway` mention the log path may sit and
# still count as "named beside it". Generous enough for a numbered runbook step
# plus its explanation; tight enough that an unrelated mention elsewhere in a
# long document does not launder an un-annotated instruction.
CONTEXT_LINES = 20

_JOURNALCTL_GATEWAY = re.compile(r"journalctl[^\n`'\"]*-u\s+hermes-gateway")


def _gateway_stderr_path() -> str:
    """The single source of truth for where gateway stderr lands."""
    unit = GATEWAY_UNIT.read_text(encoding="utf-8")
    match = re.search(r"^StandardError=append:(\S+)\s*$", unit, re.MULTILINE)
    assert match, (
        f"{GATEWAY_UNIT} has no `StandardError=append:<path>` line. If the unit "
        "moved to `StandardError=journal`, these invariants (and every doc they "
        "guard) need rewriting in the opposite direction — do that deliberately."
    )
    return match.group(1)


def _live_operator_surfaces() -> list[Path]:
    files: list[Path] = []
    for root in (REPO_ROOT / "docs", REPO_ROOT / "src"):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in {".png", ".jpg", ".jpeg", ".pdf", ".gz", ".tgz"}:
                continue
            files.append(path)
    return files


def test_the_gateway_unit_still_appends_stderr_to_a_file():
    """Anchors every other assertion here. If this flips, the trap is gone and
    so is the reason for the rest of this file."""
    path = _gateway_stderr_path()
    assert path.startswith("/opt/shift-agent/logs/"), (
        f"gateway stderr now goes to {path}, outside the managed log dir."
    )
    unit = GATEWAY_UNIT.read_text(encoding="utf-8")
    assert f"StandardOutput=append:{path}" in unit, (
        "stdout and stderr should land in the same file; an operator who has to "
        "check two paths will check one."
    )


def test_the_operator_runbook_names_the_exact_stderr_path():
    """"It is logged" is not a location. The runbook must name the file."""
    path = _gateway_stderr_path()
    runbook = (REPO_ROOT / "docs" / "operator-runbook.md").read_text(encoding="utf-8")
    assert path in runbook, (
        f"docs/operator-runbook.md does not name {path}, the path "
        f"{GATEWAY_UNIT.name} actually writes operational errors to. Derived "
        "from the unit, so this fails the moment the unit changes."
    )


def test_the_operator_runbook_says_journalctl_will_not_show_them():
    """Naming the right path is not enough while the wrong instruction is the
    one an operator already has muscle memory for."""
    runbook = (REPO_ROOT / "docs" / "operator-runbook.md").read_text(encoding="utf-8")
    assert _JOURNALCTL_GATEWAY.search(runbook), (
        "the runbook should name `journalctl -u hermes-gateway` explicitly in "
        "order to say what it does and does not show — silently omitting it "
        "leaves the wrong habit unchallenged."
    )


def test_no_live_surface_sends_an_operator_to_journalctl_alone():
    """Every `journalctl -u hermes-gateway` on a live operator surface must have
    the real log path within CONTEXT_LINES, so the reader is never left with the
    half of the answer that returns nothing."""
    path = _gateway_stderr_path()
    offenders: list[str] = []
    seen = 0

    for file in _live_operator_surfaces():
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for idx, line in enumerate(lines):
            if not _JOURNALCTL_GATEWAY.search(line):
                continue
            seen += 1
            lo = max(0, idx - CONTEXT_LINES)
            hi = min(len(lines), idx + CONTEXT_LINES + 1)
            if any(path in near for near in lines[lo:hi]):
                continue
            rel = file.relative_to(REPO_ROOT).as_posix()
            offenders.append(f"{rel}:{idx + 1}: {line.strip()[:110]}")

    # Guard against the way this test would rot: a typo in _JOURNALCTL_GATEWAY,
    # or a scope change that stops walking docs/ and src/, matches nothing and
    # reports green forever. The sweep passing is only evidence if the sweep ran
    # — so require it to still be finding the mentions it is annotating.
    assert seen >= 5, (
        f"the sweep matched only {seen} `journalctl -u hermes-gateway` mentions "
        "across docs/ and src/. It was written against 6. Either the pattern or "
        "the file walk broke, and a green result here means nothing."
    )

    assert not offenders, (
        f"these lines point an operator at `journalctl -u hermes-gateway` without "
        f"naming {path} within {CONTEXT_LINES} lines. journalctl carries systemd "
        "lifecycle only — application stderr is appended to that file, so the "
        "operator searching for an error finds nothing and concludes nothing "
        "failed:\n  " + "\n  ".join(offenders)
    )


def test_cf_router_names_its_own_error_sink():
    """The code that writes the errors should say where they were recorded, so
    the next reader of a `sys.stderr.write` call does not have to reconstruct
    the systemd unit to find out."""
    path = _gateway_stderr_path()
    actions = (REPO_ROOT / "src" / "plugins" / "cf-router" / "actions.py").read_text(
        encoding="utf-8"
    )
    assert "sys.stderr.write" in actions, (
        "cf-router no longer writes to stderr — if the operational-error sink "
        "moved, retire this invariant deliberately rather than deleting it."
    )
    assert path in actions, (
        f"src/plugins/cf-router/actions.py writes operational errors to stderr "
        f"but never names {path}, where they land. Declare it once at module "
        "level so the sink is discoverable from the code that fills it."
    )


def _health_check_alert_body() -> str:
    """The message argument shift-agent-health-check.sh hands to
    shift-agent-notify-owner — i.e. the text that actually reaches the phone."""
    script = (
        REPO_ROOT / "src" / "agents" / "shift" / "scripts" / "shift-agent-health-check.sh"
    ).read_text(encoding="utf-8")
    match = re.search(r'^\s*"(Shift Agent unhealthy:.*?)"\s*\\\s*$', script, re.MULTILINE)
    assert match, (
        "could not locate the unhealthy-alert message in "
        "shift-agent-health-check.sh. If the alert was reworded, re-anchor this "
        "test — do not delete it: an alert is the one operator surface that "
        "arrives without the repository beside it."
    )
    return match.group(1)


def test_the_unhealthy_alert_names_where_the_errors_are():
    """Asserted unconditionally, not merely 'if it mentions journalctl'.

    The first version of this test skipped itself once the journalctl reference
    was removed, which made the fix look like it had retired the invariant. What
    matters is not the absence of the wrong command but the presence of the right
    location: an alert firing at 3am must carry the path, because the operator
    reading it is holding a phone, not this repository.
    """
    path = _gateway_stderr_path()
    body = _health_check_alert_body()
    assert path in body, (
        f"the unhealthy alert does not name {path}:\n  {body}\n"
        "Name the log file in the alert text itself."
    )


def test_the_unhealthy_alert_does_not_send_the_operator_to_journalctl_unqualified():
    """Keeping journalctl in the alert is fine — sending them there for agent
    errors is not."""
    body = _health_check_alert_body()
    if not _JOURNALCTL_GATEWAY.search(body):
        return  # not mentioned at all: nothing to qualify
    assert "NOT in journalctl" in body or "not in journalctl" in body, (
        f"the alert names journalctl without saying agent errors are absent "
        f"from it:\n  {body}"
    )


def test_pushover_cannot_mangle_the_path_in_the_alert():
    """Companion to the auto-suspend lesson: an alert body containing markdown
    metacharacters renders wrong, the operator sees garbage, and the alert is
    missed while the delivery reports success. Underscores are the known one."""
    body = _health_check_alert_body()
    assert "_" not in body, (
        f"the unhealthy alert body contains an underscore:\n  {body}\n"
        "Under Markdown parse_mode Telegram/Pushover consume underscores as "
        "italics and return HTTP 200 with the text mangled. Keep the body free "
        "of markdown metacharacters, or pin parse_mode=None at the send site."
    )
