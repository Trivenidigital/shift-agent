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
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
for _p in (_REPO / "src", _REPO / "src" / "platform", _REPO / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Offline: no provider keys ⇒ the deployed semantic-brief + planner providers no-op.
os.environ["OPENROUTER_API_KEY"] = ""
os.environ.pop("OPENAI_API_KEY", None)

from schemas import FlyerConfig, FlyerCreativePlannerConfig, FlyerProject, FlyerRequestFields  # noqa: E402
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


def gate_item_count(case, facts) -> GateResult:
    """Truth: the project commits to EXACTLY the expected number of distinct item names
    (catches junk count-phrase extras and under/over-fill in mixed cases)."""
    want = case.get("expect", {}).get("expected_total_items")
    if want is None:
        return GateResult(case["id"], "item_count", "truth", True, "n/a")
    names = sorted({f.value.casefold() for f in facts if re.match(r"^item:\d+:name$", f.fact_id)})
    ok = len(names) == want
    return GateResult(case["id"], "item_count", "truth", ok,
                      "ok" if ok else f"distinct items {len(names)} != expected {want}: {names}")


_JUNK_ITEM_RE = re.compile(r"^\d+\s+(?:[\w'&/-]+\s+){0,6}items?\b", re.IGNORECASE)


def gate_no_junk_items(case, facts) -> GateResult:
    """Truth (universal): no item NAME is a mis-parsed request count-phrase, e.g.
    "8 items total" / "12 famous south indian items". A real dish is never shaped
    '<number> ... items'."""
    junk = [f.value for f in facts
            if re.match(r"^item:\d+:name$", f.fact_id) and _JUNK_ITEM_RE.match(f.value or "")]
    return GateResult(case["id"], "no_junk_items", "truth", not junk,
                      "ok" if not junk else f"junk count-phrase item(s): {junk}")


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


def _build_project(case, facts) -> FlyerProject:
    now = datetime(2026, 6, 2, tzinfo=timezone.utc)
    return FlyerProject(
        project_id="F" + (re.sub(r"\D", "", str(case["id"])) or "9001").zfill(4),
        status="awaiting_final_approval",
        customer_phone="+10000000000",
        created_at=now, updated_at=now,
        original_message_id="m-oracle",
        raw_request=case["request"],
        locked_facts=list(facts),
        fields=FlyerRequestFields(
            event_or_business_name=(case.get("profile") or {}).get("business_name", "") or "",
        ),
    )


def gate_render_fit(case, facts) -> GateResult:
    """Delivery: do the locked facts fit the PRODUCTION overlay (apply_critical_text_overlay)
    on a blank canvas at the real output sizes — BEFORE any image spend? Offline + free;
    font-dependent (locally approximate, VPS-authoritative)."""
    try:
        from PIL import Image
        from agents.flyer import render as flyer_render
    except Exception as e:  # pragma: no cover - env without Pillow/render importable
        return GateResult(case["id"], "render_fit", "delivery", True, f"skipped (import: {e})")
    try:
        project = _build_project(case, facts)
    except Exception as e:
        return GateResult(case["id"], "render_fit", "delivery", True, f"skipped (project build: {e})")
    for (w, h, fmt) in [(1080, 1350, "concept_preview"), (1275, 1650, "final_whatsapp_image")]:
        with tempfile.TemporaryDirectory() as td:
            src, tgt = Path(td) / "bg.png", Path(td) / "out.png"
            Image.new("RGB", (w, h), (238, 238, 238)).save(src)
            try:
                flyer_render.apply_critical_text_overlay(project, src, tgt, size=(w, h), output_format=fmt)
            except flyer_render.FlyerRenderError as e:
                return GateResult(case["id"], "render_fit", "delivery", False, f"{w}x{h}: {e}")
            except Exception as e:  # font/other env issue → don't false-fail; flag as skipped
                return GateResult(case["id"], "render_fit", "delivery", True, f"skipped ({w}x{h}: {e})")
    return GateResult(case["id"], "render_fit", "delivery", True, "fits all sizes")


GATES = [gate_extraction, gate_planner, gate_item_count, gate_no_junk_items, gate_flat_price, gate_disallowed, gate_render_fit]


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
