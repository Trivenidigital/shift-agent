"""Systemd unit-key invariant (census C-4).

`ConditionPathIsExecutable` is NOT a real systemd key — systemd logs "Unknown
key name" every run and the intended executable-presence guard is silently a
no-op. The real key is `ConditionFileIsExecutable`. Guards the two units the
census flagged (send-daily-brief, catering-pattern-report) against regression.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UNITS = [
    REPO / "src" / "agents" / "daily_brief" / "systemd" / "send-daily-brief.service",
    REPO / "src" / "agents" / "catering" / "systemd" / "catering-pattern-report.service",
]


@pytest.mark.parametrize("unit", UNITS, ids=lambda p: p.name)
def test_unit_uses_valid_condition_key(unit):
    text = unit.read_text(encoding="utf-8")
    # The invalid key must not appear as a directive (line-start, not a comment).
    for line in text.splitlines():
        assert not line.lstrip().startswith("ConditionPathIsExecutable="), (
            f"{unit.name} uses the invalid systemd key ConditionPathIsExecutable="
        )
    assert "ConditionFileIsExecutable=" in text, (
        f"{unit.name} should guard on ConditionFileIsExecutable="
    )
