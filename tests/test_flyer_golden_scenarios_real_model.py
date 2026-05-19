"""Spend-gated Flyer Studio golden smoke.

This file is intentionally inert in normal CI. It only spends model credits
when the operator sets FLYER_GOLDEN_ALLOW_SPEND=1, and even then it runs the
existing smoke-flyer-quality path with an isolated output directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SMOKE = REPO / "src" / "agents" / "flyer" / "scripts" / "smoke-flyer-quality"


def test_real_model_golden_smoke_requires_allow_spend_flag():
    """The real-model smoke must fail closed if someone forgets --allow-spend."""
    proc = subprocess.run(
        [sys.executable, str(SMOKE), "--real-model"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "--real-model requires --allow-spend" in payload["error"]


@pytest.mark.skipif(
    os.environ.get("FLYER_GOLDEN_ALLOW_SPEND") != "1",
    reason="set FLYER_GOLDEN_ALLOW_SPEND=1 to run spend-gated real-model flyer golden smoke",
)
def test_real_model_golden_smoke_allow_spend(tmp_path):
    """Operator-only smoke: real image model + final package + dry-run send."""
    proc = subprocess.run(
        [
            sys.executable,
            str(SMOKE),
            "--real-model",
            "--allow-spend",
            "--final-package",
            "--output-dir",
            str(tmp_path / "real-model-golden"),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "real-model"
    assert payload["send_dry_run"]["ok"] is True
    assert payload["stale_sidecar_check"]["ok"] is True
