# Flyer Customer-Acceptance Baseline (BEFORE activation)

Captured: 2026-06-15T02:12:49.593604+00:00  ·  Source: `/opt/shift-agent/state/flyer/projects.json`  ·  Phase: BEFORE
Test/smoke flyers flagged & excluded from customer figures: 9

## Headline (customer flyers, excl. test/smoke)
| Metric | Before |
|---|---|
| Total flyers analyzed | 147 (approved: 129) |
| **Accepted first draft** | **87.6%** (113/129 approved) |
| **Avg revisions / approved flyer** | **0.53** (all: 0.47, max: 44) |
| **Time-to-approval (median)** | **330.17 h** |
| Time-to-approval (mean) | 288.02 h (n=129, excluded=0) |

## Segmentation (customer flyers)
| Segment | n | approved | accepted-first % | avg revisions (approved) | TTA median (h) |
|---|---|---|---|---|---|
| reference-based | 40 | 23 | 87.0 | 0.22 | 242.5 |
| non-reference | 107 | 106 | 87.7 | 0.59 | 337.99 |
| menu-heavy (>6 items) | 14 | 1 | 100.0 | 0 | 6.45 |
| simple/promo (<=6 items) | 133 | 128 | 87.5 | 0.53 | 332.58 |

## All flyers (incl. test/smoke), for reference
- total=156, approved=138, accepted-first=87.7%, avg-rev(approved)=0.5, TTA median=332.58h

## Definitions (keep identical for the After rerun)
- approved = approved_message_id set OR status in {completed, delivered, delivered_with_warning}
- accepted_first_draft = approved AND 0 revisions; pct is of APPROVED flyers
- time_to_approval = updated_at - created_at (PROXY; no approval ts in schema); excludes <0h and >30d
- reference_based = reference_extractions non-empty; menu_heavy = >6 item:N:name facts
- Rerun: `python3 flyer_acceptance_baseline.py --out-md after.md --out-json after.json` and diff.