#!/usr/bin/env python3
"""Standalone parity-test runner for dispatcher-replay harness.

Designed to run on a VPS (or local machine) where OPENROUTER_API_KEY is in env,
without needing the full pytest infrastructure or repo checkout.

Usage:
    OPENROUTER_API_KEY=... python3 run-replay-parity.py \\
        --fixtures /path/to/dispatcher_traffic.jsonl \\
        --skill /path/to/dispatch_shift_agent/SKILL.md \\
        --model openai/gpt-4o-mini

Multiple --model flags run sequentially and produce a comparison table.

Exit codes:
    0 = all models meet --threshold (default 0.80)
    1 = at least one model below threshold
    2 = input error

Drift-check tag: extends-Hermes (uses Hermes substrate's OpenRouter config
via OPENROUTER_API_KEY env var; honors P2.5 B's provider.sort=price).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: this script requires the `openai` package.", file=sys.stderr)
    print("Install with: pip install openai", file=sys.stderr)
    sys.exit(2)


KNOWN_HANDLERS = sorted({
    "apply_catering_menu_decision",
    "handle_catering_owner_approval",
    "expense_bookkeeper_dispatcher",
    "handle_owner_command",
    "update_catering_menu",
    "catering_dispatcher",
    "compliance_owner_query",
    "customer_location_query",
    "handle_candidate_response",
    "handle_sick_call",
    "unknown_sender_declined",
})

NO_HANDLER_FOUND = "<no-handler-found>"


def load_fixtures(path: Path) -> list[dict]:
    fixtures = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                fixtures.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"line {n} malformed: {e}") from e
    return fixtures


def build_routing_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "route_to_handler",
            "description": (
                "Pick exactly one downstream handler skill based on the "
                "dispatch_shift_agent priority matrix. The matrix is in "
                "priority order — earlier rows fire first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "handler": {"type": "string", "enum": KNOWN_HANDLERS},
                    "matched_priority": {"type": "integer"},
                    "reasoning": {"type": "string"},
                },
                "required": ["handler", "matched_priority"],
            },
        },
    }


def call_one(client: OpenAI, model: str, skill_md: str, payload: dict, cheapest: bool) -> tuple[str, str, float, float]:
    """Call OpenRouter once. Returns (raw_response, handler, cost_usd, latency_s)."""
    user_msg = (
        "Inbound message context (validate-sender-block + identify-sender + "
        "state-file lookups already done):\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n\n"
        "Walk the routing matrix in priority order and call route_to_handler "
        "with the first matching row's handler. Use 'unknown_sender_declined' "
        f"if no row matches. Available handlers: {KNOWN_HANDLERS}."
    )
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": skill_md},
            {"role": "user", "content": user_msg},
        ],
        "tools": [build_routing_tool()],
        "tool_choice": {"type": "function", "function": {"name": "route_to_handler"}},
        "temperature": 0,
    }
    if cheapest:
        kwargs["extra_body"] = {"provider": {"sort": "price"}}

    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        latency = time.monotonic() - t0
        return (f"ERROR: {type(e).__name__}: {e}", NO_HANDLER_FOUND, 0.0, latency)
    latency = time.monotonic() - t0

    cost = 0.0
    try:
        cost = float(getattr(resp.usage, "cost", 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError):
        pass

    try:
        tool_calls = resp.choices[0].message.tool_calls or []
        if not tool_calls:
            content = resp.choices[0].message.content or ""
            return (f"NO_TOOL_CALL: {content!r}", NO_HANDLER_FOUND, cost, latency)
        args = json.loads(tool_calls[0].function.arguments)
        handler = args.get("handler", NO_HANDLER_FOUND)
        if handler not in KNOWN_HANDLERS:
            return (f"INVALID_HANDLER: {handler!r}", NO_HANDLER_FOUND, cost, latency)
        prio = args.get("matched_priority", "?")
        reason = args.get("reasoning", "")
        return (f"priority={prio} → {handler} ({reason!r})", handler, cost, latency)
    except (json.JSONDecodeError, AttributeError, IndexError) as e:
        return (f"PARSE_ERROR: {type(e).__name__}: {e}", NO_HANDLER_FOUND, cost, latency)


def run_model(client: OpenAI, model: str, skill_md: str, fixtures: list[dict], cheapest: bool):
    print(f"\n{'═' * 72}")
    print(f"  MODEL: {model}  (cheapest_provider={cheapest})")
    print(f"{'═' * 72}")
    matched = 0
    total_cost = 0.0
    total_latency = 0.0
    mismatches = []
    parse_fails = []
    for fx in fixtures:
        fid = fx["id"]
        expected = fx["expected_handler"]
        raw, actual, cost, latency = call_one(client, model, skill_md, fx["input"], cheapest)
        total_cost += cost
        total_latency += latency
        ok = (actual == expected)
        if ok:
            matched += 1
            print(f"  ✓ {fid:50s} → {actual} (${cost:.5f}, {latency:.2f}s)")
        else:
            tag = "PARSE_FAIL" if actual == NO_HANDLER_FOUND else "WRONG"
            print(f"  ✗ {fid:50s} expected={expected}, got={actual} [{tag}] (${cost:.5f}, {latency:.2f}s)")
            print(f"      raw: {raw[:160]}")
            if actual == NO_HANDLER_FOUND:
                parse_fails.append((fid, expected, raw))
            else:
                mismatches.append((fid, expected, actual))

    total = len(fixtures)
    rate = matched / total if total else 0.0
    print(f"\n  Result:    {matched}/{total} = {rate:.1%}")
    print(f"  Cost:      ${total_cost:.4f}")
    print(f"  Latency:   {total_latency:.1f}s total, {total_latency/total:.2f}s avg")
    return {
        "model": model,
        "matched": matched,
        "total": total,
        "rate": rate,
        "cost": total_cost,
        "latency": total_latency,
        "mismatches": mismatches,
        "parse_fails": parse_fails,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", required=True, type=Path)
    p.add_argument("--skill", required=True, type=Path)
    p.add_argument("--model", action="append", required=True, dest="models",
                   help="OpenRouter model id; pass multiple to compare")
    p.add_argument("--threshold", type=float, default=0.80,
                   help="Minimum match rate (default 0.80)")
    p.add_argument("--no-cheapest", action="store_true",
                   help="Disable provider.sort=price (default: ON to honor P2.5 B)")
    p.add_argument("--out", type=Path, default=None,
                   help="Write structured results JSON to this path")
    args = p.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY env var required", file=sys.stderr)
        sys.exit(2)
    if not args.fixtures.exists():
        print(f"ERROR: fixtures file not found: {args.fixtures}", file=sys.stderr)
        sys.exit(2)
    if not args.skill.exists():
        print(f"ERROR: SKILL.md not found: {args.skill}", file=sys.stderr)
        sys.exit(2)

    fixtures = load_fixtures(args.fixtures)
    skill_md = args.skill.read_text(encoding="utf-8")
    cheapest = not args.no_cheapest

    print(f"Loaded {len(fixtures)} fixtures from {args.fixtures}")
    print(f"Loaded SKILL.md ({len(skill_md)} chars) from {args.skill}")
    print(f"Threshold:                  {args.threshold:.1%}")
    print(f"Cheapest-provider routing:  {'ON' if cheapest else 'OFF'}")

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1", timeout=60.0)

    results = []
    for model in args.models:
        results.append(run_model(client, model, skill_md, fixtures, cheapest))

    # Comparison table
    print(f"\n{'═' * 72}")
    print("  PARITY SUMMARY")
    print(f"{'═' * 72}")
    print(f"  {'Model':40s}  {'Match':>10s}  {'Cost':>10s}  {'Latency':>10s}")
    print(f"  {'-' * 40}  {'-' * 10}  {'-' * 10}  {'-' * 10}")
    for r in results:
        rate_str = f"{r['matched']}/{r['total']} {r['rate']:.0%}"
        print(f"  {r['model']:40s}  {rate_str:>10s}  ${r['cost']:>8.4f}  {r['latency']:>8.1f}s")
    print(f"{'═' * 72}\n")

    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"Results written to {args.out}")

    # Exit code
    failed = [r for r in results if r["rate"] < args.threshold]
    if failed:
        names = ", ".join(r["model"] for r in failed)
        print(f"FAIL: {names} below threshold {args.threshold:.1%}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: all {len(results)} models meet threshold {args.threshold:.1%}")
    sys.exit(0)


if __name__ == "__main__":
    main()
