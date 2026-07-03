#!/usr/bin/env python3
"""WS1 shadow watcher (v2 spec amendment A4) — READ-ONLY, zero production forks.

Tails the production project store for NEW flyer projects, replays each brief
through extraction_v2 OFFLINE, and quarantines a legacy-vs-v2 comparison. No
counters, no state mutation, no delivery surface — the customer's-path
validation demanded by the Class 2 rule, implemented as a watcher (the
complexity-budget alternative to an inline pipeline fork).

Run ON THE BOX (needs /opt/shift-agent on sys.path + extraction_v2 staged
alongside this script + the ws0 key):
    venv/bin/python flyer-extraction-shadow-watch.py --once
    venv/bin/python flyer-extraction-shadow-watch.py --watch 60
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/shift-agent")
sys.path.insert(0, str(Path(__file__).resolve().parent))  # extraction_v2 staged here

PROJECTS = Path("/opt/shift-agent/state/flyer/projects.json")
OUT = Path("/tmp/ws1-shadow")
STATE = OUT / "state.json"
KEY_FILE = Path("/root/.hermes/ws0-openrouter.key")


def _load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"seen": []}


def _item_names(facts):
    return [str(f.get("value") if isinstance(f, dict) else f.value).strip()
            for f in facts
            if (f.get("fact_id") if isinstance(f, dict) else f.fact_id).startswith("item:")
            and (f.get("fact_id") if isinstance(f, dict) else f.fact_id).endswith(":name")]


def run_once() -> int:
    os.environ.setdefault("OPENROUTER_API_KEY", KEY_FILE.read_text().strip())
    from extraction_v2 import ExtractionV2Error, extract_text_facts_v2  # staged copy
    from schemas import FlyerRequestFields

    OUT.mkdir(exist_ok=True)
    state = _load_state()
    store = json.loads(PROJECTS.read_text(encoding="utf-8"))
    new = 0
    for p in store.get("projects", []):
        pid = p.get("project_id", "")
        if pid in state["seen"] or not p.get("raw_request"):
            continue
        state["seen"].append(pid)
        raw = p["raw_request"]
        legacy_items = _item_names(p.get("locked_facts", []))
        row = {"project_id": pid, "created_at": p.get("created_at"),
               "observed_at": datetime.now(timezone.utc).isoformat(),
               "brief": raw[:500], "legacy_items": legacy_items}
        try:
            facts, report = extract_text_facts_v2(FlyerRequestFields(), raw)
            v2_items = [f.value for f in facts
                        if f.fact_id.startswith("item:") and f.fact_id.endswith(":name")]
            row.update({
                "v2_items": v2_items,
                "v2_scalars": {f.fact_id: f.value for f in facts
                               if not f.fact_id.startswith("item:")},
                "v2_report": report.summary_line(),
                "dropped_by_parity": report.dropped_by_parity,
                "divergence": sorted(set(x.lower() for x in v2_items)
                                     ^ set(x.lower() for x in legacy_items)),
            })
        except ExtractionV2Error as exc:  # fail-closed evidence, quarantined
            row["v2_error"] = str(exc)[:200]
        (OUT / f"{pid}.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
        print(json.dumps({k: row[k] for k in row if k != "brief"}))
        new += 1
    STATE.write_text(json.dumps(state), encoding="utf-8")
    return new


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--watch", type=int, metavar="SECONDS")
    args = ap.parse_args()
    if args.once:
        print(f"new_projects_compared={run_once()}")
        return
    while True:
        try:
            n = run_once()
            if n:
                print(f"[watch] compared {n} new project(s)")
        except Exception as exc:  # watcher must survive transient store reads
            print(f"[watch] transient error: {type(exc).__name__}: {str(exc)[:120]}")
        time.sleep(max(15, args.watch))


if __name__ == "__main__":
    main()
