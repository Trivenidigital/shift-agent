# Flyer recovery safety net — operator Option 4 (2026-06-15)

**Drift-check tag:** extends-Hermes (tunes the existing recovery orchestration; no new substrate).
**Status:** IMPLEMENTED on `fix/flyer-recovery-safety-net` (off deployed `7c17bb6`). Tests green (flyer suite 2563 passed). Pending Codex review + deploy.

## Problem (live evidence, 2026-06-15)
Integrated funnel since activation: 20 attempted → **8 passed (40%), 3 fell-back (15%), 9 manual (45%)**. Nearly half of flyers ended in *"I couldn't finish this automatically"* → no flyer reaches the customer → churn. The flyers themselves looked good (premium posters); the problem was the **hold rate**, driven by the recovery gate being too narrow:

- F0160 (weekend specials): `inferred item not rendered: Uttapam/Pongal` + `visible text defect reported by QA: 'Uttap' misspelling` + `missing required visible fact: contact_phone/location`. The misspelling blocker was **not** in the recoverable whitelist → `_qa_failed_exact_text_recoverable` returned False → neither the content-retry nor the deterministic-overlay fallback fired → straight to manual.
- F0162 (Memorial Day combos): `visible text defect reported by QA: 'Bihcken' misspelling` + `missing required visible fact: offer:0` → same path → manual.

## Operator decision (Option 4 — safety net now, repair loop later)
> Integrated → Referee → Recover → Deterministic fallback → Ship (instead of → Manual).
> - Dangerous failures (wrong price/phone, fabricated offer) **remain hard-blocks**.
> - Recoverable text defects (missing items, misspellings, dropped entries, schedule omissions) **attempt recovery then fall back to deterministic** rather than go straight to manual.

## Change (single chokepoint: `_qa_failed_exact_text_recoverable` in `generate-flyer-concepts`)
The gate decides both the content-miss corrective retry AND the deterministic-overlay fallback. Two edits to its `recoverable_prefixes` whitelist:

1. **ADD `"visible text defect reported by QA:"`** (misspellings/duplications). The deterministic overlay redraws every name/price/fact from `locked_facts`, so it cannot reproduce a misspelling — overlay-recoverable. **This converts F0160 + F0162 from manual → recover→fallback→ship.**
2. **REMOVE `"fabricated price visible:"`, `"fabricated offer claim visible:"`, `"unverified phone number visible:"`** → these become hard-blocks (return False → fall through to manual). Honors "wrong price/phone, fabricated offer remain hard-blocks."

Severity coupling (verified): all three dangerous prefixes classify as **block**-tier in `classify_qa_severity` (`fabricated_*` are explicit block patterns; `unverified phone` falls through to the `if blockers: return "block"` default). So a surviving dangerous blocker routes to `manual_edit_required` + `FlyerIntegratedManualReview` — never `delivered_with_warning`.

The bounded fabrication ×2 corrective retry is unchanged (it can still convert a fabrication into a clean premium ship); only its **exhaustion** outcome changed from overlay-ship to manual.

## Judgment call the operator can correct
Before today, fabrication was **overlay-recoverable** (the overlay strips fabrication by drawing only locked facts, so it ships a de-fabricated flyer — Codex-reviewed 2026-06-14 "4a"). The operator's Option-4 wording ("fabricated offer remain hard-blocks", contrasted with "rather than go straight to manual") reads as: **dangerous → manual**, not overlay-ship. I implemented that (fabrication → manual). If the operator instead wants fabrication auto-recovered via the de-fabricating overlay (lower manual rate, still never ships the fabricated claim), revert edit #2's fabrication prefixes — a one-line change.

## Validation blocked then unblocked (2026-06-16)
First re-send of the weekend-specials brief was **hijacked**: cf-router routed it as `flyer_reference_exact_edit_queued` (an edit followup) onto **F0163**, which was stuck in `revising_design` since 23:31 because the **flyer-recovery-watchdog.service is crashing** (exit 22, failing since ≥2026-06-15 16:04 — PRE-EXISTING, not the safety-net deploy). The safety net never ran (no new generation). Operator-authorized narrow unblock (no watchdog/routing code touched):

**F0163 manual close — recorded:** project_id=F0163; old_status=`revising_design`; new_status=`closed_no_send`; old manual_review=`queued`; new manual_review=`closed_no_send`; ts=`2026-06-16T02:08:58Z`; reason=stuck revising_design due to pre-existing watchdog crash / unblock validation. Done via one locked atomic edit of projects.json (`find_active_flyer_project_by_sender` excludes `{completed, closed_no_send}` → F0163 no longer hijacks new requests).

## Deferred follow-up (not in this change)
- **Recovery watchdog crash (NEW, P1):** `flyer-recovery-watchdog.service` exits 22 every 5 min since ≥2026-06-15 16:04 — stuck flyers (revising_design/generating past SLA) are NOT being recovered, which both leaves dead projects AND feeds the routing hijack. Pre-existing; the main worktree has uncommitted `flyer-recovery-watchdog` + `.flyer-recovery-worker-draft` (possible in-flight work — do not collide). Diagnose the exit-22 cause separately.
- **Routing hijack (confirmed actively blocking):** a flyer stuck in `revising_design` with a queued exact-edit captures subsequent new-flyer briefs as followups (`flyer_reference_exact_edit_queued`) instead of force-new. cf-router `_try_flyer_active_project_intercept` / hooks.py exact-edit-queued path. Separate branch.
- **Promo-vocab precision (F0161):** `_PROMO_PHRASE_RE` flags bare labels like "special combo" as a fabricated offer even with no price/discount. Under this change such a (possibly benign) flag now hard-blocks to manual. Tighten the detector to require a discount/price signal before flagging a fabricated *offer*. Tracked separately (referee precision, has false-negative risk).
- **Routing bug:** new flyer requests arriving while a flyer is queued get misrouted as an *edit* ("I could not match that change to the queued edit"). Separate subsystem (cf-router intake / stale-session hijack), separate branch.
- **Image-to-image repair loop (premium recovery):** the long-term answer per the operator — keep the premium look AND fix text. Build after the funnel is healthy.
