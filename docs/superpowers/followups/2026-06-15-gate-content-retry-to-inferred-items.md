# Follow-up: gate content-miss retry to inferred-item cases only (avoid double-retry on pure missing-facts)

**Opened:** 2026-06-15 · **Severity:** P3 (cost/latency, NON-safety) · **Status:** OPEN — operator-accepted for the 2026-06-15 tuning redeploy (`deploy-20260615-170312-7c17bb62`).

## What Codex flagged (accepted, not a blocker)
For a **pure** `missing required visible fact` block (`item:N:name`, `campaign_title`, `headline`, `pricing_structure`, `offer:N`) — which `recovery.py` legacy autorepair already classifies as Hermes-plan-eligible and retries — the **new content-miss corrective retry also runs**. So such a case can take: legacy-autorepair render(s) (own budget) + content-retry (×1) + deterministic-overlay fallback = **more than one corrective render** before settling.

- **Bounded** (legacy has its own attempt budget; content-retry is ×1) — no loop.
- **Safe** (fail-closed preserved; `content_recovery_unresolved` forces manual; nothing ships unverified).
- Purely a **cost/latency redundancy**, not a customer-facing or dangerous failure. Codex invariants 2/3/4 (no-unverified-ship, negation-notes, unchanged-safety) all PASS.

## Fix
Gate the content-miss corrective retry in `generate-flyer-concepts` to fire **only when `failed_qa` contains an `inferred item not rendered:` blocker** (the class legacy autorepair does NOT handle). Pure `missing required visible fact` cases then rely on legacy autorepair + the (now-enabled) deterministic-overlay fallback — no double retry. Verify pure-missing-fact still recovers end-to-end (legacy → fallback → pass/manual).

## Companion existing follow-ups
- `2026-06-15-bare-path-referee-unavailable-posture.md` (Codex #2)
- `2026-06-14-openai-key-gpt-image-source-edit-degradation.md`
- `2026-06-15-approved-at-timestamp.md` (trustworthy time-to-approval)
