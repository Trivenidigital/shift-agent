#!/usr/bin/env python3
"""Catering quote-drafting prose parity test (step 4 gate c).

Validates that gpt-4o-mini drafts customer-facing catering quotes that
satisfy the deployed truth-guard requirements (headcount integer present,
ISO event_date parenthetical present, length ≤600 chars, no markdown).
Compares against the current production model (kimi-k2-thinking).

Usage:
    OPENROUTER_API_KEY=... python3 run-catering-prose-parity.py \\
        --skill /path/to/handle_catering_owner_approval/SKILL.md \\
        --model openai/gpt-4o-mini \\
        --model moonshotai/kimi-k2-thinking

Drift-check tag: extends-Hermes (consumes deployed SKILL.md + OpenRouter
substrate; honors P2.5 B provider.sort=price).

Truth-guard rules from SKILL Step 3b + PR-B v3 design:
  1. Drafted text contains the literal headcount integer (word-bounded,
     not as substring of a larger number)
  2. Drafted text contains the ISO event_date as parenthetical (YYYY-MM-DD)
  3. Length ≤600 chars (apply-script's normalizer cap)
  4. No markdown delimiters: * _ ~ ` (apply-script normalizer strips them
     but the LLM should produce clean prose to begin with)
  5. Includes customer name (or "Hi there" if name absent)
  6. Includes a polite CTA + lead reference (Ref: ...)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    print("ERROR: this script requires `openai` package.", file=sys.stderr)
    sys.exit(2)


# Synthetic catering leads — variety of customer types, headcounts, dates, dietary.
SYNTHETIC_LEADS = [
    {
        "id": "lead-A-wedding-vegetarian",
        "owner_message": "approve",
        "lead": {
            "lead_id": "L-A001",
            "customer_name": "Priya Sharma",
            "owner_approval_code": "#A3F2X",
            "extracted": {
                "event_date": "2026-06-15",
                "event_time": "18:00",
                "headcount": 80,
                "dietary_restrictions": ["vegetarian", "no_eggs"],
                "venue": "Hotel banquet hall",
            },
            "menu_summary": "Vegetarian Indian buffet — paneer tikka, dal makhani, biryani, naan",
        },
    },
    {
        "id": "lead-B-corporate-small",
        "owner_message": "approve",
        "lead": {
            "lead_id": "L-B002",
            "customer_name": "Acme Corp HR",
            "owner_approval_code": "#B7K3M",
            "extracted": {
                "event_date": "2026-05-22",
                "event_time": "12:00",
                "headcount": 25,
                "dietary_restrictions": ["gluten_free"],
                "venue": "Office",
            },
            "menu_summary": "Gluten-free office lunch — quinoa salad, grilled chicken, fruit platter",
        },
    },
    {
        "id": "lead-C-birthday-large",
        "owner_message": "approve",
        "lead": {
            "lead_id": "L-C003",
            "customer_name": "Raj Patel",
            "owner_approval_code": "#C4Q9P",
            "extracted": {
                "event_date": "2026-07-04",
                "event_time": "19:00",
                "headcount": 150,
                "dietary_restrictions": [],
                "venue": "Backyard",
            },
            "menu_summary": "Mixed Indian — biryani, butter chicken, kebabs, naan, gulab jamun",
        },
    },
    {
        "id": "lead-D-anonymous-customer",
        "owner_message": "approve",
        "lead": {
            "lead_id": "L-D004",
            "customer_name": None,  # no name captured
            "owner_approval_code": "#D1H8R",
            "extracted": {
                "event_date": "2026-08-01",
                "event_time": "13:00",
                "headcount": 40,
                "dietary_restrictions": ["vegan"],
                "venue": "Park pavilion",
            },
            "menu_summary": "Vegan menu — chana masala, vegetable biryani, samosa, rice pudding",
        },
    },
    {
        "id": "lead-E-headcount-50-collision-trap",
        "owner_message": "approve",
        "lead": {
            "lead_id": "L-E005",
            "customer_name": "Suresh Kumar",
            "owner_approval_code": "#E5N2K",
            "extracted": {
                "event_date": "2026-09-10",
                "event_time": "17:00",
                "headcount": 50,
                "dietary_restrictions": ["jain"],
                "venue": "Community center",
            },
            "menu_summary": "Jain-friendly — no onion/garlic. Includes dal, rice, roti, sabzi.",
            # Truth-guard trap: model might draft "for 150 guests" or "the 50% promotion" — both
            # contain "50" as substring but neither is word-bounded headcount=50.
        },
    },
]


def build_user_prompt(lead: dict, owner_message: str) -> str:
    """User-side prompt feeding the SKILL its inputs."""
    return (
        f"Owner replied: '{owner_message}' for catering approval code "
        f"{lead['owner_approval_code']}.\n\n"
        f"State files (already read inline per Step 3a of the SKILL):\n\n"
        f"```json\n"
        f"LEAD_JSON = {json.dumps(lead, indent=2, ensure_ascii=False)}\n"
        f"```\n\n"
        f"Per the SKILL Step 3b, draft the customer-facing quote in plain prose. "
        f"Output ONLY the drafted quote text — no preamble, no markdown, no "
        f"backticks. The drafted text will be piped via stdin to the apply-script.\n\n"
        f"Return your final drafted quote text directly. If you would write "
        f"shell commands, omit them — only the quote text is needed."
    )


def truth_guard_check(drafted: str, lead: dict) -> dict:
    """Apply the truth-guard rules from the SKILL + apply-script normalizer."""
    extracted = lead["extracted"]
    headcount = extracted.get("headcount")
    event_date = extracted.get("event_date")

    checks = {
        "headcount_word_bounded": False,
        "iso_date_parenthetical": False,
        "length_under_600": len(drafted) <= 600,
        "no_markdown": True,
        "customer_name_or_greeting": False,
        "lead_ref": False,
    }

    if headcount is not None:
        # Word-boundary match: \b50\b matches "50 guests" but not "150 people" or "50,000"
        pattern = rf"(?<!\d){re.escape(str(headcount))}(?!\d)"
        checks["headcount_word_bounded"] = bool(re.search(pattern, drafted))

    if event_date:
        # ISO date parenthetical: literal "(YYYY-MM-DD)"
        checks["iso_date_parenthetical"] = f"({event_date})" in drafted

    # No markdown delimiters
    for delim in ("**", "__", "~~", "`"):
        if delim in drafted:
            checks["no_markdown"] = False
            break

    customer_name = lead.get("customer_name") or ""
    if customer_name:
        checks["customer_name_or_greeting"] = customer_name.split()[0] in drafted
    else:
        checks["customer_name_or_greeting"] = "Hi there" in drafted or "Hello" in drafted

    lead_id = lead.get("lead_id", "")
    checks["lead_ref"] = lead_id in drafted or "(Ref:" in drafted

    return checks


def call_one(client: OpenAI, model: str, skill_md: str, lead: dict, owner_msg: str, cheapest: bool):
    user_msg = build_user_prompt(lead, owner_msg)
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": skill_md},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,  # slight variation OK for prose; truth-guard catches errors
        "max_tokens": 800,
    }
    if cheapest:
        kwargs["extra_body"] = {"provider": {"sort": "price"}}

    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        return ("", 0.0, time.monotonic() - t0, f"ERROR: {type(e).__name__}: {e}")
    latency = time.monotonic() - t0

    cost = 0.0
    try:
        cost = float(getattr(resp.usage, "cost", 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError):
        pass

    drafted = resp.choices[0].message.content or ""
    drafted = drafted.strip()
    # Strip enclosing code fences if model added them
    if drafted.startswith("```"):
        lines = drafted.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        drafted = "\n".join(lines).strip()
    return (drafted, cost, latency, "")


def run_model(client: OpenAI, model: str, skill_md: str, leads: list[dict], cheapest: bool):
    print(f"\n{'═' * 80}")
    print(f"  MODEL: {model}  (cheapest_provider={cheapest})")
    print(f"{'═' * 80}")
    pass_count = 0
    total_cost = 0.0
    total_latency = 0.0
    detail = []
    for lead_entry in leads:
        lead = lead_entry["lead"]
        owner_msg = lead_entry["owner_message"]
        lead_id = lead_entry["id"]
        drafted, cost, latency, err = call_one(client, model, skill_md, lead, owner_msg, cheapest)
        total_cost += cost
        total_latency += latency
        if err:
            print(f"  ✗ {lead_id:40s}  {err}")
            detail.append({"id": lead_id, "passed": False, "error": err, "drafted": ""})
            continue
        checks = truth_guard_check(drafted, lead)
        all_passed = all(checks.values())
        if all_passed:
            pass_count += 1
        symbol = "✓" if all_passed else "✗"
        print(f"  {symbol} {lead_id:40s}  cost=${cost:.5f} latency={latency:.2f}s len={len(drafted)}")
        for k, v in checks.items():
            mark = "✓" if v else "✗"
            print(f"      {mark} {k}")
        if not all_passed:
            print(f"      drafted: {drafted[:300]!r}")
        detail.append({"id": lead_id, "passed": all_passed, "checks": checks, "drafted": drafted, "cost": cost})

    total = len(leads)
    rate = pass_count / total if total else 0.0
    print(f"\n  Result:    {pass_count}/{total} = {rate:.1%}")
    print(f"  Cost:      ${total_cost:.4f}")
    print(f"  Latency:   {total_latency:.1f}s total, {total_latency/total:.2f}s avg")
    return {
        "model": model,
        "passed": pass_count,
        "total": total,
        "rate": rate,
        "cost": total_cost,
        "latency": total_latency,
        "detail": detail,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skill", required=True, type=Path,
                   help="Path to handle_catering_owner_approval/SKILL.md")
    p.add_argument("--model", action="append", required=True, dest="models")
    p.add_argument("--threshold", type=float, default=0.80)
    p.add_argument("--no-cheapest", action="store_true")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY env var required", file=sys.stderr)
        sys.exit(2)
    if not args.skill.exists():
        print(f"ERROR: SKILL.md not found: {args.skill}", file=sys.stderr)
        sys.exit(2)

    skill_md = args.skill.read_text(encoding="utf-8")
    cheapest = not args.no_cheapest

    print(f"Loaded SKILL.md ({len(skill_md)} chars)")
    print(f"Synthetic leads:   {len(SYNTHETIC_LEADS)}")
    print(f"Threshold:         {args.threshold:.1%}")
    print(f"Cheapest provider: {'ON' if cheapest else 'OFF'}")

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1", timeout=60.0)
    results = [run_model(client, m, skill_md, SYNTHETIC_LEADS, cheapest) for m in args.models]

    # Comparison
    print(f"\n{'═' * 80}")
    print("  CATERING PROSE PARITY SUMMARY")
    print(f"{'═' * 80}")
    print(f"  {'Model':40s}  {'Pass':>10s}  {'Cost':>10s}  {'Latency':>10s}")
    print(f"  {'-'*40}  {'-'*10}  {'-'*10}  {'-'*10}")
    for r in results:
        rate_str = f"{r['passed']}/{r['total']} {r['rate']:.0%}"
        print(f"  {r['model']:40s}  {rate_str:>10s}  ${r['cost']:>8.4f}  {r['latency']:>8.1f}s")
    print(f"{'═' * 80}\n")

    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"Results written to {args.out}")

    failed = [r for r in results if r["rate"] < args.threshold]
    if failed:
        names = ", ".join(r["model"] for r in failed)
        print(f"FAIL: {names} below threshold {args.threshold:.1%}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: all {len(results)} models meet threshold {args.threshold:.1%}")
    sys.exit(0)


if __name__ == "__main__":
    main()
