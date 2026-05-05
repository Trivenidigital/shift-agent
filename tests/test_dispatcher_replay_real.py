"""Opt-in real-LLM dispatcher replay tests (v0.2).

**Skipped by default.** Activates only when both env vars are set:
  - HERMES_REPLAY_MODEL=<openrouter-model-id>  (e.g. openai/gpt-4o-mini)
  - OPENROUTER_API_KEY=<key>

Optional:
  - HERMES_REPLAY_THRESHOLD=<float>  Match-rate threshold (default 0.80)
  - HERMES_REPLAY_NO_CHEAPEST=1      Disable provider.sort=price (default on)

Run on srilu-vps where OPENROUTER_API_KEY lives in the env:
    HERMES_REPLAY_MODEL=openai/gpt-4o-mini pytest tests/test_dispatcher_replay_real.py -v -s

To compare two models in one session:
    HERMES_REPLAY_MODEL=openai/gpt-4o-mini pytest tests/test_dispatcher_replay_real.py -v -s
    HERMES_REPLAY_MODEL=moonshotai/kimi-k2-thinking pytest tests/test_dispatcher_replay_real.py -v -s

Cost: each run hits the LLM once per fixture. With 15 fixtures and gpt-4o-mini
at ~$0.001/call, expect ~$0.02 per run. With kimi-k2-thinking (~$0.005/call)
expect ~$0.10/run. Real costs land in `_REAL_LLM_COST_LOG` and print to stdout
at end of run.

Drift-check tag: extends-Hermes (uses Hermes substrate's OpenRouter
configuration via OPENROUTER_API_KEY env var; provider.sort=price honors
the production config from P2.5 B).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from _dispatcher_replay import (  # noqa: E402
    Fixture,
    NO_HANDLER_FOUND,
    get_real_llm_cost_log,
    load_dispatcher_skill,
    load_fixtures,
    openrouter_llm_caller,
    replay_one,
    reset_real_llm_cost_log,
)


REPLAY_MODEL = os.getenv("HERMES_REPLAY_MODEL", "")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
MATCH_THRESHOLD = float(os.getenv("HERMES_REPLAY_THRESHOLD", "0.80"))
USE_CHEAPEST = os.getenv("HERMES_REPLAY_NO_CHEAPEST", "") != "1"


pytestmark = pytest.mark.skipif(
    not (REPLAY_MODEL and OPENROUTER_KEY),
    reason=(
        "real-LLM tests are opt-in. Set HERMES_REPLAY_MODEL=<model-id> AND "
        "OPENROUTER_API_KEY=<key> to enable. Currently MODEL=%r KEY_SET=%s"
        % (REPLAY_MODEL, bool(OPENROUTER_KEY))
    ),
)


_FIXTURES = load_fixtures()
_SKILL_MD = load_dispatcher_skill() if REPLAY_MODEL else ""
_CALLER = (
    openrouter_llm_caller(
        model_id=REPLAY_MODEL,
        api_key=OPENROUTER_KEY,
        cheapest_provider=USE_CHEAPEST,
    )
    if (REPLAY_MODEL and OPENROUTER_KEY)
    else None
)


# Module-level results storage — populated as parametrized tests run, summarized
# in the final test_summary check.
_RESULTS: list[tuple[str, bool, str, str]] = []  # (fixture_id, match, expected, actual)


@pytest.mark.parametrize(
    "fixture",
    _FIXTURES,
    ids=[f.id for f in _FIXTURES],
)
def test_real_llm_routes_correctly(fixture: Fixture):
    """Real LLM (model from env) routes each fixture to the expected handler.

    Per-fixture failures DON'T fail the test suite — we collect results and
    enforce the threshold in test_summary at module level. This lets a single
    edge-case mismatch not abort the run before we have parity data.
    """
    # Tag the input payload with the fixture id so the cost-tracking decoration
    # can attribute spend per-fixture.
    payload = dict(fixture.input_payload)
    payload["_fixture_id_for_cost_tracking"] = fixture.id

    fixture_with_tagged_payload = Fixture(
        id=fixture.id,
        category=fixture.category,
        description=fixture.description,
        source_row=fixture.source_row,
        input_payload=payload,
        expected_handler=fixture.expected_handler,
        notes=fixture.notes,
    )

    result = replay_one(fixture_with_tagged_payload, _SKILL_MD, _CALLER)
    _RESULTS.append((
        fixture.id,
        result.match,
        result.expected_handler,
        result.actual_handler,
    ))
    # Soft-pass: log diagnostic but don't fail individual cases — threshold
    # check happens in test_summary.
    print(f"\n{result.diagnostic()}")


def test_summary():
    """Aggregate match-rate threshold check + cost report.

    Runs LAST (alphabetical order — `test_summary` after `test_real_llm_*`).
    Fails the suite if match rate < HERMES_REPLAY_THRESHOLD (default 0.80).
    Always prints cost log so the operator sees total $ spent regardless of
    pass/fail.
    """
    if not _RESULTS:
        pytest.skip("no real-LLM results — pre-conditions not met")

    total = len(_RESULTS)
    matched = sum(1 for _, ok, _, _ in _RESULTS if ok)
    rate = matched / total if total else 0.0

    # Cost summary
    cost_log = get_real_llm_cost_log()
    total_cost = sum(c for _, _, c in cost_log)
    cost_by_model: dict[str, float] = {}
    for model, _, cost in cost_log:
        cost_by_model[model] = cost_by_model.get(model, 0.0) + cost

    # Mismatches detail
    mismatches = [
        (fid, exp, act) for fid, ok, exp, act in _RESULTS if not ok
    ]

    print(
        f"\n\n══════════════════════════════════════════════════════════════"
        f"\n  REAL-LLM REPLAY SUMMARY"
        f"\n══════════════════════════════════════════════════════════════"
        f"\n  Model:        {REPLAY_MODEL}"
        f"\n  Fixtures:     {total}"
        f"\n  Matched:      {matched}"
        f"\n  Mismatched:   {total - matched}"
        f"\n  Match rate:   {rate:.1%}"
        f"\n  Threshold:    {MATCH_THRESHOLD:.1%}"
        f"\n  Cheapest provider routing: {'ON (provider.sort=price)' if USE_CHEAPEST else 'OFF'}"
        f"\n  Total cost:   ${total_cost:.4f}"
    )
    if cost_by_model:
        print("  Cost by model:")
        for model, cost in cost_by_model.items():
            print(f"    {model}: ${cost:.4f}")
    if mismatches:
        print("\n  Mismatches:")
        for fid, exp, act in mismatches:
            tag = "PARSE_FAIL" if act == NO_HANDLER_FOUND else "WRONG"
            print(f"    [{tag}] {fid}: expected={exp!r}, got={act!r}")
    print("══════════════════════════════════════════════════════════════")

    assert rate >= MATCH_THRESHOLD, (
        f"match rate {rate:.1%} below threshold {MATCH_THRESHOLD:.1%}: "
        f"{matched}/{total} matched"
    )
