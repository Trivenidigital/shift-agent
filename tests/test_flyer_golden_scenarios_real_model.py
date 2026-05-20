"""Spend-gated Flyer Studio golden smoke.

This file is intentionally inert in normal CI. It only spends model credits
when the operator sets BOTH:
  FLYER_GOLDEN_ALLOW_SPEND=1
  FLYER_GOLDEN_SPEND_PROFILE=isolated

and even then it runs the existing smoke-flyer-quality path with an isolated
output directory. P0-7 added the SPEND_PROFILE gate + CI-refusal so an
accidental copy of "allow spend" envs into a build secret cannot trigger
a real-model spend.
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

_SPEND_GATE_REASON = (
    "set FLYER_GOLDEN_ALLOW_SPEND=1 AND FLYER_GOLDEN_SPEND_PROFILE=isolated to run "
    "spend-gated real-model flyer golden smoke. The SPEND_PROFILE gate is a P0-7 "
    "addition that forces explicit acknowledgement of non-production credentials."
)


def _spend_enabled() -> bool:
    return (
        os.environ.get("FLYER_GOLDEN_ALLOW_SPEND") == "1"
        and os.environ.get("FLYER_GOLDEN_SPEND_PROFILE") == "isolated"
    )


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


def test_real_model_smoke_refuses_in_ci_env(tmp_path, monkeypatch):
    """Even with --allow-spend, the smoke must refuse if CI env vars are set.
    Belt-and-suspenders against accidentally spending in a build pipeline."""
    env = {
        **os.environ,
        "GITHUB_ACTIONS": "true",
        # Defense-in-depth: turn off the override even if a stale local env has it
        "ALLOW_SPEND_IN_CI": "0",
    }
    # Tell the smoke to use a model arg, but it should never get there.
    proc = subprocess.run(
        [sys.executable, str(SMOKE), "--real-model", "--allow-spend",
         "--output-dir", str(tmp_path / "ci-refusal")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 3, f"expected CI-refusal exit 3, got {proc.returncode}; stderr={proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "CI environment" in payload["error"]
    assert "GITHUB_ACTIONS" in payload["error"]


def test_real_model_smoke_ci_refusal_can_be_overridden(tmp_path, monkeypatch):
    """ALLOW_SPEND_IN_CI=1 lets the operator bypass the CI guard when intentional.
    We only check that the gate proceeds PAST the CI refusal; we stop the run
    by passing an invalid config path so we don't actually spend."""
    env = {
        **os.environ,
        "GITHUB_ACTIONS": "true",
        "ALLOW_SPEND_IN_CI": "1",
    }
    proc = subprocess.run(
        [sys.executable, str(SMOKE), "--real-model", "--allow-spend",
         "--config-path", str(tmp_path / "no-such-config.yaml"),
         "--output-dir", str(tmp_path / "ci-override")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The CI guard returns 3 with a JSON refusal payload. We expect to be
    # past that — the smoke now fails for a different reason (missing config).
    if proc.returncode == 3:
        try:
            payload = json.loads(proc.stdout)
            assert "CI environment" not in payload.get("error", ""), (
                "ALLOW_SPEND_IN_CI did not override the CI refusal"
            )
        except json.JSONDecodeError:
            pass  # different failure shape — also acceptable


@pytest.mark.skipif(
    not _spend_enabled(),
    reason=_SPEND_GATE_REASON,
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
    # P0-7: posture block surfaces provider + key presence + source for
    # operator visibility. Never echoes the secret value.
    assert payload["posture"]["provider"] == "openrouter"
    assert "present" in payload["posture"]["openrouter_key"]
    assert "source" in payload["posture"]["openrouter_key"]
    assert "present" in payload["posture"]["openrouter_source_edit_key"]
