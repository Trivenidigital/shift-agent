# Follow-up: align bare/marketing path (`bare_render.py`) referee-unavailable posture with Architecture A fail-closed standard

**Opened:** 2026-06-15 · **Severity:** P1 (pre-existing) · **Status:** OPEN — deferred from Slice 1 by operator decision. NOT a Slice 1 blocker.

## Finding (Codex #2, 2026-06-14 review of Slice 1)
The bare/marketing flyer path (`src/agents/flyer/bare_render.py`) can ship an **integrated flyer unverified**:
- The visible-contract referee (`_run_visible_contract_gate`, ~`bare_render.py:889`) **sends-anyway when its own OCR/validator fails** (returns success on `vision_error:*`, marking `visible_contract_status=unverified`), at ~lines 894/908/937.
- `FLYER_BARE_SKIP_VISUAL_QA=1` (set on main-vps) skips the broad post-render QA on this path.
Net: a generated bare-path flyer can be delivered without verification of its locked facts.

## Why it is NOT a Slice 1 blocker (operator decision, 2026-06-15)
- **Pre-existing** — not introduced by Slice 1; the diff did not touch this logic.
- **Deliberate prior decision** — the 2026-06-07 visible-contract referee was designed to send-anyway on its own infra/OCR failure ("Option 1, no new false-holds") to avoid false holds; the unverified state is recorded via a metric.
- **Unaffected by Slice 1 activation** — the bare path renders via its own `GEN_MODEL` (`FLYER_BARE_GEN_MODEL`), independent of the `config.yaml` `final/draft_image_model` flip that activates Slice 1. Merging/deploying Slice 1 does not change this path's risk.
- Slice 1 is scoped to hardening the `generate-flyer-concepts` integrated path to the fail-closed standard; the bare path is a different lane with in-flight visible-contract/marketing work.

## Scope of the follow-up
Bring the bare path to the Architecture A fail-closed standard:
1. On visible-contract OCR/validator **infra failure**, fall back to the deterministic-overlay/renderer (verified-by-construction) instead of send-anyway — matching `generate-flyer-concepts`'s referee-unavailable handling.
2. Reconsider `FLYER_BARE_SKIP_VISUAL_QA` — either run the broad QA on the bare path or document why the visible-contract referee is sufficient.
3. Make the bare path emit the same `flyer_integrated_*` observability + `integrated_referee_unavailable_fallback` markers for parity.

## Note
Slice 1's FIX 6 already extended `FLYER_INTEGRATED_KILLSWITCH` totality to all bare-path generative entry points, so the panic switch fully covers the bare path even though its routine QA posture is unchanged.
