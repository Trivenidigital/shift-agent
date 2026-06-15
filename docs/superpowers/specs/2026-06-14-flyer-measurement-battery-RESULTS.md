# Flyer Integrated-Generation Measurement Battery — RESULTS

**Date:** 2026-06-14 · **Model:** `google/gemini-3.1-flash-image-preview` (gen + edit) · OCR/judge `openai/gpt-4o-mini` (all via OpenRouter)
**Spend:** $3.28 generation + $0.73 revision = **$4.01** (hard stop was $15) · **Verdict: Architecture A is viable.**
Spec: `2026-06-14-flyer-integrated-generation-measurement-battery-design.md` · Plan: `../plans/2026-06-14-flyer-measurement-battery.md`
Artifacts: 42 PNGs + `results.md` + `revision_results.md` in `C:\Testing\measure\` (VPS `/tmp/flyer-measure/`).

## Generation — pass@1 (business-critical text) by tier

| Tier | pass@1 | avg gen | judge overall (anchors: gen.png=30, gpt.png=85) |
|---|---|---|---|
| T1 simple promo | 3/3 | 14.3s | 71.7 |
| T2 medium menu | 3/3 | 10.5s | 70 |
| T3 dense (16-item) | 2/3 | 13.4s | 67.5 |
| T4 Telugu (gating) | 3/3 | 10.3s | 70 |
| **T5 real customer briefs** | **20/20** | 11.8s | 70 |

**~31/32 pass@1** on locked facts. Cost ≈ **$0.068/flyer**, latency ≈ **12s** (vs gpt-5.4's 170s/$0.38). Telugu (non-gating): **4/5 glyph-exact** per sample (one recurring error వడ→పడ).

## Revision benchmark — gemini-3.1 image-edit (the moat)

9/9 edits: **100% locked-fact preservation, zero drift.** Layout-preserved ≈ 90, intent-achieved ≈ 75. One sensible no-op ("add more festive colors" on an already-festive flyer → unchanged, no drift).

## Verified by eye (not just OCR)

- **F0150** (the `ref.png`/`gen.png` snack-poster lineage): same customer brief that currently ships as `gen.png` (flat panels + pasted onion rings) now renders `ref.png`-grade. **This is the before/after that answers "why pay for Flyer Studio."**
- **F0109** (previously broke with "THURRSDAY" typo + dup brand under Pillow overlay): now renders "Every Thursday" cleanly. **Integrated generation eliminates the old overlay defects.**
- **REV_T1 "make it more premium":** genuinely more premium, all facts intact, layout held.

## Honest caveats — these DEFINE the referee's job (do not undermine A)

1. **Fabricated promo elements (the #1 risk).** T3 invented a "6 OFFERS $3.99 | $4.99" banner; T4 invented "Offer $5.99" — neither in the brief. The model adds offer/price banners that don't exist. **The referee must check for *unexpected* claims (absence-checking), not just missing facts.** Prod `visual_qa` already flags "claims not in facts" — that path is load-bearing for A. (Our eval scorer only checked presence, so it did not flag these — a gap in the eval, not the model being safe.)
2. **Telugu ~80% glyph accuracy.** Keep non-gating; Telugu-heavy menus likely need a deterministic text fallback or a stronger model for that lane.
3. **Dense menus are the edge.** 16-item T3 was the only tier under 3/3 (one char-sub on item #16).
4. **Literal label leakage.** "Business:/Offer:/Phone:" sometimes rendered as literal text — a prompt-tuning fix (strip field labels before sending).

## Implications for the build

- **A is confirmed:** integrated generation gives `gpt.png`-grade quality with ~97% first-pass text fidelity on real briefs, fast and cheap; the deterministic renderer becomes the fallback, not the primary path.
- **Referee scope is now concrete:** must verify locked facts present AND no fabricated offers/prices/claims; Telugu fallback; retry-on-fail (rare — ~1/32).
- **"Preserve approved elements" is de-risked:** image-to-image edits preserved 100% of facts across 9 revisions — the moat is achievable.
- **Production finding (separate):** `OPENAI_API_KEY` is absent on main-vps, so the configured `gpt-image-1` source-edit path is likely degraded (falling back to lossy regen) in production right now. Verify and fix independently.

## Recommended next step

Design production Architecture A: (1) integrated generation via gemini-3.1-flash-image, (2) referee = locked-fact presence + fabrication/absence check + Telugu handling + retry/fallback, (3) revision loop (image-to-image, same model). Then define "preserve approved elements" formally (now evidence-backed).
