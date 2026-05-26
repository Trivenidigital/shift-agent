from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def test_flyer_regulated_seed_fixtures_cover_confirmed_active_block_list():
    fixture_dir = REPO / "tests" / "conversation_evals" / "seed" / "flyer"
    fixture_texts = {
        json.loads(path.read_text(encoding="utf-8"))["inbound_text"]
        for path in fixture_dir.glob("*.json")
    }

    assert {
        "Upgrade to Growth",
        "Move me to the 69.99 plan",
        "change plan",
        "change my plan",
        "switch to Growth",
        "start Growth",
        "downgrade to Starter",
        "cancel my plan",
        "change my phone number",
        "change my WhatsApp",
        "change business name",
        "change address",
        "where is my flyer",
        "did you send my flyer",
        "send my flyer",
        "I paid",
        "payment sent",
        "mark paid",
        "refund",
    }.issubset(fixture_texts)


def test_conversation_eval_runner_blocks_flyer_regulated_fallthrough():
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "run-conversation-evals.py"), "--agent", "flyer"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "flyer" in result.stdout
    assert "failed=0" in result.stdout
