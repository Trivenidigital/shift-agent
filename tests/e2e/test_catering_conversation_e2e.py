"""Env-gated pytest entry for the catering conversation E2E gate.

Makes REAL OpenRouter model calls, so it is SKIPPED unless OPENROUTER_API_KEY is
set — CI never has it, so CI never makes model calls. Run it locally with:

    set -a; . scratch/.e2e-llm.env; set +a        # provides OPENROUTER_API_KEY
    python -m pytest tests/e2e/test_catering_conversation_e2e.py -q -s

or drive the full 3-session gate + artifacts directly:

    python tests/e2e/catering_conversation_harness.py --out tests/e2e/artifacts

The deterministic layer (cf-router dispatch, catering scripts incl.
--recompose-from-sent) is covered by the always-on unit/script suites
(test_catering_recompose.py, test_create_catering_proposal_options.py,
test_catering_pra_reachability.py). This E2E only adds the LLM-in-the-loop
conversation gate.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY unset — E2E makes real model calls; skipped in CI.",
)


def test_catering_conversation_gate_one_session():
    """One free-flow session on gpt-4o-mini: every turn (incl. the mix-and-match
    recompose + ambiguous clarify probe) must pass all assertions and the tone rule."""
    import catering_conversation_harness as H

    sessions, _transcripts, stability, _ = H.run_gate(sessions_n=1)
    failed = [t for t, passes in stability.items() if not all(passes)]
    if failed:
        detail = {}
        for t in failed:
            pt = next(p for p in sessions[0] if p["turn"] == t)
            detail[str(t)] = {
                "tone": pt["tone"],
                "failed_assertions": [(n, note) for n, ok, note in pt["assertions"] if not ok],
            }
        pytest.fail(f"turns failed the gate: {detail}")
