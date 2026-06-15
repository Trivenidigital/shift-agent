# Follow-up: stamp a real `approved_at` timestamp when `approved_message_id` is set

**Opened:** 2026-06-15 · **Severity:** P2 (measurement enablement) · **Status:** OPEN.

## Why
Time-to-approval cannot be measured reliably today. `FlyerProject` has no approval timestamp, so the baseline used a proxy (`updated_at − created_at`) — which is **contaminated** by bulk stale-cleanup / address-backfill operations that bump `updated_at` long after creation. Result: the 2026-06-15 baseline's 330 h median TTA is a **timestamp artifact, not customer behavior**. It must NOT be used as a baseline (only as a known caveat).

## What
When a flyer is approved (the code path that sets `approved_message_id`), also set a new `approved_at` UTC field on `FlyerProject`. Then real time-to-approval = `approved_at − created_at`.
- Schema: add `approved_at: Optional[datetime]` to `FlyerProject` (`src/platform/schemas.py`).
- Write site: wherever `approved_message_id` is assigned (approval handler / cockpit approve path) — stamp `approved_at = now(UTC)` in the same update.
- Small, additive, no migration needed (existing rows keep `approved_at=None`).

## Enables
A trustworthy **After** time-to-approval for the activation scoreboard once integrated generation is live.

## Activation scoreboard — reliable BEFORE baseline (2026-06-15)
Use these (from `docs/superpowers/baselines/flyer-acceptance-baseline-20260615.md`):
- **Accepted first draft: 87.6%**
- **Avg revisions / approved: 0.53**
- **Reference vs non-reference avg revisions: 0.22 vs 0.59**

Do NOT use the **330 h** time-to-approval baseline except as the documented timestamp-artifact caveat. Re-run `tools/flyer-acceptance-baseline.py` after activation for the After column (acceptance % and revision counts are reliable; TTA becomes reliable only after `approved_at` ships).
