"""Log rotation invariants for units that append to a file.

Three defects on 2026-08-31 motivated this, all found on the live box:

1. `/etc/logrotate.d/shift-agent-cockpit` was hand-installed on 2026-05-31 with
   CRLF line endings. logrotate cannot parse those ("lines must begin with a
   keyword or a filename"), so cockpit-audit.log and cockpit.log had not
   rotated for three months and logrotate.service reported FAILED nightly --
   which desensitises anything watching `systemctl --failed`.

2. That file was never installed by any deploy path, so the repo's correct LF
   copy at web/deploy/logrotate.conf was not reaching the box at all.

3. `flyer-recovery-watchdog.log` (105 MB and still growing) and
   `flyer-source-edit-sla-watchdog.log` were covered by NO stanza whatsoever.

Defect 3 is the one worth a test, because it recurs: every time a systemd unit
gains a `StandardOutput=append:` target, a new unrotated log appears and
nothing notices until it is large. `test_every_append_target_is_rotated` fails
the moment that happens again.

A NOTE ON WHAT THIS FILE DELIBERATELY DOES NOT ASSERT. The first draft
required `copytruncate` for every `StandardOutput=append:` target, reasoning
that such a unit holds its fd open across rotation. That is false here: every
one of these units is `Type=oneshot` fired by a timer, so it exits between
runs and `create` -- the convention every existing stanza already uses -- is
correct. The test found that itself by failing on send-daily-brief,
eod-reconcile and prune-expense-receipts, which had always been fine.

So the mode assertion below only requires that SOME mode is chosen
explicitly. Demanding a specific one would encode an invariant this codebase
has not demonstrated, and the paired decisions.log control shows why a blanket
rule would be wrong in the other direction too.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOGROTATE_FILES = [
    REPO / "src" / "agents" / "shift" / "logrotate" / "shift-agent",
    REPO / "web" / "deploy" / "logrotate.conf",
]
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"

_STANZA_RE = re.compile(r"^(?P<paths>(?:\s*/\S+\s*)+)\{(?P<body>[^}]*)\}", re.M)
_APPEND_RE = re.compile(r"^Standard(?:Output|Error)=append:(?P<path>\S+)", re.M)


def _stanzas():
    """{log path -> stanza body} across every logrotate config in the repo."""
    out: dict[str, str] = {}
    for f in LOGROTATE_FILES:
        assert f.exists(), f"missing logrotate config: {f}"
        for m in _STANZA_RE.finditer(f.read_text(encoding="utf-8")):
            for p in m.group("paths").split():
                out[p.strip()] = m.group("body")
    return out


def _append_targets():
    """{log path -> [unit names]} for every systemd unit that appends."""
    out: dict[str, list[str]] = {}
    for unit in sorted(REPO.glob("src/agents/*/systemd/*.service")) + sorted(
        REPO.glob("src/platform/systemd/*.service")
    ):
        text = unit.read_text(encoding="utf-8")
        for m in _APPEND_RE.finditer(text):
            out.setdefault(m.group("path"), []).append(unit.name)
    return out


# ── 1. line endings ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", LOGROTATE_FILES, ids=lambda p: p.name)
def test_logrotate_config_has_no_crlf(path):
    """logrotate rejects a CRLF file outright; the box proved it for 3 months.

    Asserted on the BYTES, not on a text read -- Python's universal newlines
    would translate CRLF away and this test would pass on a broken file.
    """
    raw = path.read_bytes()
    assert b"\r" not in raw, (
        f"{path} contains CR bytes. logrotate fails with 'lines must begin "
        f"with a keyword or a filename' and skips the whole file."
    )


# ── 2. coverage: the invariant that catches the NEXT one ─────────────────────

def test_every_append_target_is_rotated():
    """Every `StandardOutput=append:` log must have a logrotate stanza."""
    targets = _append_targets()
    assert targets, "found no append: targets -- the unit scan is broken"
    covered = _stanzas()
    missing = {p: u for p, u in targets.items() if p not in covered}
    assert not missing, (
        "systemd units append to logs with no logrotate stanza; they grow "
        f"unbounded: { {p: u for p, u in sorted(missing.items())} }"
    )


def test_append_targets_declare_a_rotation_mode():
    """Every covered log must choose `create` or `copytruncate` explicitly.

    Not which one -- see the module docstring. logrotate's default when
    neither is given depends on the global config, so an omission makes the
    behaviour depend on a file this repo does not own.
    """
    covered = _stanzas()
    undeclared = [
        p for p in sorted(_append_targets())
        if p in covered
        and "copytruncate" not in covered[p]
        and "create" not in covered[p]
    ]
    assert not undeclared, (
        "rotation mode left to the global default for: %s" % undeclared
    )


def test_decisions_log_does_not_use_copytruncate():
    """The paired control: copytruncate is NOT globally correct.

    decisions.log is the audit chokepoint and is appended by short-lived
    processes that reopen it, so it uses `create` -- and copytruncate there
    would race a writer and lose audit rows. Without this test, a well-meaning
    'add copytruncate everywhere' change would pass the two tests above.
    """
    body = _stanzas().get("/opt/shift-agent/logs/decisions.log")
    assert body is not None, "decisions.log lost its logrotate stanza"
    assert "copytruncate" not in body, "decisions.log must rotate with create"
    assert "create" in body


# ── 3. the config actually reaches the box ───────────────────────────────────

@pytest.mark.parametrize(
    "source,target",
    [
        ("src/agents/shift/logrotate/shift-agent", "/etc/logrotate.d/"),
        ("web/deploy/logrotate.conf", "/etc/logrotate.d/shift-agent-cockpit"),
    ],
)
def test_deploy_installs_the_logrotate_config(source, target):
    """A correct config in the repo is worth nothing if nothing installs it.

    The cockpit config was correct in-repo the whole time it was broken on the
    box, because only a hand-placed copy existed there.
    """
    script = DEPLOY.read_text(encoding="utf-8")
    assert source in script, f"deploy script never references {source}"
    line = next(
        (ln for ln in script.splitlines() if source in ln and "install " in ln), None
    )
    assert line is not None, f"{source} is mentioned but never installed"
    assert target in line, f"{source} is installed somewhere other than {target}"
