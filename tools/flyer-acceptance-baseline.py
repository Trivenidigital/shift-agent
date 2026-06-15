#!/usr/bin/env python3
"""Flyer customer-acceptance baseline report (READ-ONLY).

Reads flyer projects.json and computes acceptance/revision KPIs. Writes NOTHING
to production state — output goes to stdout + the --out paths only. Rerun after
activation with the same definitions to compare Before vs After.

Metric definitions (kept stable for Before/After parity):
  approved        = approved_message_id non-empty OR status in
                    {completed, delivered, delivered_with_warning}
  accepted_first  = approved AND revision_count == 0
  revision_count  = len(project.revisions)
  time_to_approval= updated_at - created_at  (PROXY: no explicit approval ts in
                    schema; last-update minus creation). Excludes <0 and >30d as
                    stale-cleanup noise (counted separately).
  reference_based = reference_extractions non-empty
  menu_heavy      = (# item:N:name locked_facts) > 6
  likely_test     = raw_request/original_message_id smells like smoke/QA/E2E/trivial
"""
import json, sys, argparse, statistics, re
from datetime import datetime, timezone

APPROVED_STATUSES = {"completed", "delivered", "delivered_with_warning"}
TEST_MARKERS = ("smoke", "qa e2e", "qa-e2e", "qae2e", "quality-smoke")
TRIVIAL_REQUESTS = {"", "a", "create flyer", "create a flyer", "a flyer"}

def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None

def _is_test(p):
    raw = (p.get("raw_request") or "").lower()
    omid = (p.get("original_message_id") or "").lower()
    if any(m in raw for m in TEST_MARKERS) or "smoke" in omid:
        return True
    if raw.strip() in TRIVIAL_REQUESTS:
        return True
    return False

def _item_count(p):
    n = 0
    for f in p.get("locked_facts") or []:
        if re.match(r"item:\d+:name$", f.get("fact_id", "")):
            n += 1
    return n

def _approved(p):
    return bool(p.get("approved_message_id")) or p.get("status") in APPROVED_STATUSES

def _metrics(projects):
    total = len(projects)
    approved = [p for p in projects if _approved(p)]
    n_app = len(approved)
    accepted_first = [p for p in approved if len(p.get("revisions") or []) == 0]
    rev_counts_app = [len(p.get("revisions") or []) for p in approved]
    rev_counts_all = [len(p.get("revisions") or []) for p in projects]
    # time-to-approval (hours), proxy = updated_at - created_at on approved
    ttas, tta_excluded = [], 0
    for p in approved:
        c, u = _parse_ts(p.get("created_at")), _parse_ts(p.get("updated_at"))
        if not c or not u:
            tta_excluded += 1
            continue
        hrs = (u - c).total_seconds() / 3600.0
        if hrs < 0 or hrs > 24 * 30:
            tta_excluded += 1
            continue
        ttas.append(hrs)
    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None
    return {
        "total_flyers": total,
        "approved_flyers": n_app,
        "accepted_first_draft_n": len(accepted_first),
        "accepted_first_draft_pct_of_approved": pct(len(accepted_first), n_app),
        "avg_revisions_per_approved": round(statistics.mean(rev_counts_app), 2) if rev_counts_app else None,
        "avg_revisions_all": round(statistics.mean(rev_counts_all), 2) if rev_counts_all else None,
        "max_revisions": max(rev_counts_all) if rev_counts_all else 0,
        "time_to_approval_hrs_median": round(statistics.median(ttas), 2) if ttas else None,
        "time_to_approval_hrs_mean": round(statistics.mean(ttas), 2) if ttas else None,
        "time_to_approval_n": len(ttas),
        "time_to_approval_excluded": tta_excluded,
    }

def _seg_row(label, projects):
    m = _metrics(projects)
    return {"segment": label, "n": m["total_flyers"], "approved": m["approved_flyers"],
            "accepted_first_pct": m["accepted_first_draft_pct_of_approved"],
            "avg_revisions_approved": m["avg_revisions_per_approved"],
            "tta_median_hrs": m["time_to_approval_hrs_median"]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default="/opt/shift-agent/state/flyer/projects.json")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()
    d = json.load(open(args.projects, encoding="utf-8"))
    P = d if isinstance(d, list) else d.get("projects", d)
    P = list(P.values()) if isinstance(P, dict) else P

    real = [p for p in P if not _is_test(p)]
    report = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": args.projects,
        "phase": "BEFORE (pre-activation baseline)",
        "all": _metrics(P),
        "customer_excl_test": _metrics(real),
        "test_flyers_flagged": len(P) - len(real),
        "segments_customer": [
            _seg_row("reference-based", [p for p in real if (p.get("reference_extractions") or [])]),
            _seg_row("non-reference", [p for p in real if not (p.get("reference_extractions") or [])]),
            _seg_row("menu-heavy (>6 items)", [p for p in real if _item_count(p) > 6]),
            _seg_row("simple/promo (<=6 items)", [p for p in real if _item_count(p) <= 6]),
        ],
    }
    cust = report["customer_excl_test"]
    lines = [
        "# Flyer Customer-Acceptance Baseline (BEFORE activation)",
        "",
        f"Captured: {report['captured_at']}  ·  Source: `{args.projects}`  ·  Phase: BEFORE",
        f"Test/smoke flyers flagged & excluded from customer figures: {report['test_flyers_flagged']}",
        "",
        "## Headline (customer flyers, excl. test/smoke)",
        "| Metric | Before |",
        "|---|---|",
        f"| Total flyers analyzed | {cust['total_flyers']} (approved: {cust['approved_flyers']}) |",
        f"| **Accepted first draft** | **{cust['accepted_first_draft_pct_of_approved']}%** ({cust['accepted_first_draft_n']}/{cust['approved_flyers']} approved) |",
        f"| **Avg revisions / approved flyer** | **{cust['avg_revisions_per_approved']}** (all: {cust['avg_revisions_all']}, max: {cust['max_revisions']}) |",
        f"| **Time-to-approval (median)** | **{cust['time_to_approval_hrs_median']} h** |",
        f"| Time-to-approval (mean) | {cust['time_to_approval_hrs_mean']} h (n={cust['time_to_approval_n']}, excluded={cust['time_to_approval_excluded']}) |",
        "",
        "## Segmentation (customer flyers)",
        "| Segment | n | approved | accepted-first % | avg revisions (approved) | TTA median (h) |",
        "|---|---|---|---|---|---|",
    ]
    for s in report["segments_customer"]:
        lines.append(f"| {s['segment']} | {s['n']} | {s['approved']} | {s['accepted_first_pct']} | {s['avg_revisions_approved']} | {s['tta_median_hrs']} |")
    lines += [
        "",
        "## All flyers (incl. test/smoke), for reference",
        f"- total={report['all']['total_flyers']}, approved={report['all']['approved_flyers']}, "
        f"accepted-first={report['all']['accepted_first_draft_pct_of_approved']}%, "
        f"avg-rev(approved)={report['all']['avg_revisions_per_approved']}, "
        f"TTA median={report['all']['time_to_approval_hrs_median']}h",
        "",
        "## Definitions (keep identical for the After rerun)",
        "- approved = approved_message_id set OR status in {completed, delivered, delivered_with_warning}",
        "- accepted_first_draft = approved AND 0 revisions; pct is of APPROVED flyers",
        "- time_to_approval = updated_at - created_at (PROXY; no approval ts in schema); excludes <0h and >30d",
        "- reference_based = reference_extractions non-empty; menu_heavy = >6 item:N:name facts",
        "- Rerun: `python3 flyer_acceptance_baseline.py --out-md after.md --out-json after.json` and diff.",
    ]
    md = "\n".join(lines)
    print(md)
    print("\n=== JSON ===")
    print(json.dumps(report, indent=1))
    if args.out_json:
        open(args.out_json, "w", encoding="utf-8").write(json.dumps(report, indent=1))
    if args.out_md:
        open(args.out_md, "w", encoding="utf-8").write(md)

if __name__ == "__main__":
    main()
