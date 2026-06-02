"""Flyer acceptance oracle — offline gate harness (Phase-2 core: Truth-scoreboard gates).

Design: tasks/flyer-acceptance-oracle-design.md. Drives the deployed flyer modules
deterministically with NO network: the semantic-brief + planner providers return
None without API keys; the planner provider is stubbed from each case's
`planner_offers` (simulating the LLM). The oracle ONLY observes — it never mutates
product behavior. Run: `python tests/flyer_oracle/oracle.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
for _p in (_REPO / "src", _REPO / "src" / "platform", _REPO / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Offline: no provider keys ⇒ the deployed semantic-brief + planner providers no-op.
os.environ["OPENROUTER_API_KEY"] = ""
os.environ.pop("OPENAI_API_KEY", None)

from schemas import FlyerConfig, FlyerCreativePlannerConfig, FlyerRequestFields  # noqa: E402
from agents.flyer import facts as flyer_facts  # noqa: E402
from agents.flyer import creative_planner as cp  # noqa: E402

CORPUS_DIR = _HERE.parent / "corpus"


@dataclass
class GateResult:
    case_id: str
    gate: str
    scoreboard: str  # truth | delivery | commercial
    passed: bool
    detail: str = ""


def load_corpus() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(CORPUS_DIR.glob("*.json"))]


def _cfg(case: dict) -> FlyerConfig:
    c = (case.get("config") or {}).get("creative_planner") or {}
    return FlyerConfig(creative_planner=FlyerCreativePlannerConfig(
        enabled=bool(c.get("enabled", False)),
        enabled_categories=list(c.get("enabled_categories", [])),
    ))


def run_pipeline(case: dict):
    """Deterministic offline run of the text-fact pipeline for one case (extraction +
    planner gate). Stubs the planner provider from `planner_offers`."""
    offers = case.get("planner_offers")
    cp.build_creative_planner_provider = (
        (lambda o=offers: (lambda fields, raw: list(o))) if offers else (lambda: None)
    )
    fields = FlyerRequestFields(
        event_or_business_name=(case.get("profile") or {}).get("business_name", "") or "",
        notes=case.get("notes", "") or "",
    )
    return flyer_facts.extract_text_facts(fields, case["request"], cfg=_cfg(case))


def _by_id(facts):
    return {f.fact_id: f for f in facts}


def _inferred_names(facts):
    return [f for f in facts if re.match(r"^item:\d+:name$", f.fact_id) and f.source == "hermes_inferred"]


def gate_extraction(case, facts) -> GateResult:
    by = _by_id(facts)
    miss = []
    for exp in case.get("expect", {}).get("locked_facts", []):
        got = by.get(exp["fact_id"])
        if got is None or got.value.casefold() != str(exp["value"]).casefold() or got.source != exp["source"]:
            miss.append(f"{exp['fact_id']}={exp['value']}/{exp['source']}→{(got.value + '/' + got.source) if got else 'ABSENT'}")
    return GateResult(case["id"], "extraction", "truth", not miss, "ok" if not miss else "; ".join(miss))


def gate_planner(case, facts) -> GateResult:
    exp = case.get("expect", {})
    inferred = _inferred_names(facts)
    detail, ok = [], True
    allowed = exp.get("allowed_inferred")
    if allowed is not None and len(inferred) > allowed:
        ok = False
        detail.append(f"inferred {len(inferred)} > allowed {allowed} ({[f.value for f in inferred]})")
    want = exp.get("inferred_item_names")
    if want is not None:
        got = {f.value.casefold() for f in inferred}
        missn = [n for n in want if n.casefold() not in got]
        if missn:
            ok = False
            detail.append(f"missing inferred {missn}")
    return GateResult(case["id"], "planner", "truth", ok, "ok" if ok else "; ".join(detail))


def gate_flat_price(case, facts) -> GateResult:
    exp = case.get("expect", {})
    if exp.get("pricing_model") != "flat":
        return GateResult(case["id"], "flat_price", "truth", True, "n/a")
    by = _by_id(facts)
    price, src = exp.get("flat_price"), exp.get("flat_price_source", "customer_text")
    bad = []
    for nf in _inferred_names(facts):
        idx = nf.fact_id.split(":")[1]
        pf = by.get(f"item:{idx}:price")
        if pf is None or pf.value != price or pf.source != src:
            bad.append(f"{nf.value}:{(pf.value + '/' + pf.source) if pf else 'NO_PRICE'}")
    return GateResult(case["id"], "flat_price", "truth", not bad,
                      "ok" if not bad else "wrong/missing flat price: " + "; ".join(bad))


def gate_disallowed(case, facts) -> GateResult:
    detail, ok = [], True
    for d in case.get("expect", {}).get("disallowed_facts", []):
        if d.get("hard_fact_class") == "price" and "max_count_not_grounded" in d:
            prices = [f for f in facts if re.match(r"^item:\d+:price$", f.fact_id)]
            if len(prices) > d["max_count_not_grounded"]:
                ok = False
                detail.append(f"{len(prices)} ungrounded price facts > {d['max_count_not_grounded']} ({[p.value for p in prices]})")
    return GateResult(case["id"], "disallowed", "truth", ok, "ok" if ok else "; ".join(detail))


GATES = [gate_extraction, gate_planner, gate_flat_price, gate_disallowed]


def run_all() -> list[GateResult]:
    results = []
    for case in load_corpus():
        facts = run_pipeline(case)
        for g in GATES:
            results.append(g(case, facts))
    return results


def print_scoreboard(results: list[GateResult]) -> None:
    boards: dict[str, list[GateResult]] = {"truth": [], "delivery": [], "commercial": []}
    for r in results:
        boards.setdefault(r.scoreboard, []).append(r)
    for board, rs in boards.items():
        if not rs:
            continue
        p = sum(1 for r in rs if r.passed)
        print(f"\n=== {board.upper()} scoreboard: {p}/{len(rs)} pass ===")
        for r in rs:
            print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.case_id} {r.gate}: {r.detail}")
    passed = sum(1 for r in results if r.passed)
    print(f"\nTOTAL: {passed}/{len(results)} gate-checks pass")


if __name__ == "__main__":
    print_scoreboard(run_all())
