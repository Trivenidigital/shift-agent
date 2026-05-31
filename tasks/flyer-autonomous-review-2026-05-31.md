# Flyer Studio Autonomous Review - 2026-05-31

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Batch 1 adds no new Hermes substrate. It adds bounded Flyer safety primitives only: fail-closed QA severity for unknown blockers, typed audit coverage for an already-emitted manual-queue customer update row, project/package delivery serialization around WhatsApp sends, stale claimed manual-row recovery detection, placeholder provider-key rejection, atomic text-manifest writes, and a narrow delivered/revising media-revision routing guard.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/egress, identity, audit, state conventions | yes - existing Hermes/cf-router substrate in this repo; Hermes Skills Hub checked: https://hermes-agent.nousresearch.com/docs/skills | Use existing substrate; no parallel router, identity, audit, or messaging path. |
| Flyer intent classification | partial - `agents.flyer.intent` shadow contract exists in-tree; Hermes Skills Hub has no Flyer-specific classifier skill | Keep Batch 1 to deterministic safety fixes; treat active Hermes promotion as a later replay-gated workstream. |
| Visual QA / deterministic rendering | none found - Hermes Skills Hub reports no installable skills; no generic Hermes primitive replaces Flyer Studio's product-specific text/QA contract | Extend existing Flyer QA/render code only where customer-safety gaps exist. |
| Manual queue / cockpit operations | none found externally; existing Flyer manual queue + cockpit backend/frontend are the deployed Hermes-aligned substrate | Extend existing queue/recovery semantics; no new operator substrate. |
| Provider routing / prompt orchestration | partial - Hermes gateway/provider policy exists in production conventions, but this batch does not change provider routing | List as architecture debt; do not build new provider substrate in Batch 1. |

Awesome Hermes ecosystem check: searched current awesome-Hermes listings; no external Hermes ecosystem skill replaces Flyer Studio's product-specific deterministic text, visual QA, manual queue, or customer approval contracts. Batch 1 builds only bounded Flyer safety logic on the existing Hermes substrate.

## Comprehensive Findings

### Customer-Facing Improvements

- Smart prompt/starter library: restaurant starter ideas are too thin for a busy customer who says "make a flyer"; add richer category-specific choices and templates.
- Campaign scene library: current visual scene mapping is broad; restaurant flyers need specific scenes for buffet, catering trays, festival sweets, family packs, lunch combos, and takeout.
- Overlay design quality: deterministic text ownership is correct, but the text layer needs layout variants by flyer type so output feels like finished marketing, not a generic card over art.
- Preview approval UX: preview copy should include a compact fact checklist so customers catch price/date/language/contact mistakes before `APPROVE`.
- Vague-request recovery: after starter ideas are used once, the fallback should still offer category-aware choices instead of an open-ended "what should this promote?"
- Language support: onboarding offers many languages, but localization is partial; non-English display copy needs a clear contract that preserves names/prices/addresses exactly.
- Real-model visual confidence: static tests are strong, but spend-gated real-model visual eval cases and pass criteria should be ready to run.

### Safety / Correctness Issues

- P0: unknown visual-QA blockers can classify as `pass`; any non-empty blocker list must fail closed unless it matches an explicit warning rule.
- P0: real-model smoke can use synthetic sidecar OCR; real-model mode should exercise real visual QA where credentials exist.
- P0: source-edit/manual queue audit row `flyer_manual_queue_customer_update` is written but not typed in `LogEntry`.
- P1: send/recovery paths still contain unlocked audit/state writes where delivery evidence matters.
- P1: recovery classifies stale manual rows only when `manual_review.status=queued`, missing abandoned `in_progress` claims.
- P1: media-backed edits to an awaiting preview can bypass the active generated project and create a new source-edit job.
- P1: `manual_edit_required` is too broad in update routing; non-source manual rows can be treated as source-edit append-only.
- P1: typed replacement "already applied" can suppress needed regeneration when raw facts already contain the new value but the visible preview is stale.
- P2: OpenRouter image generation accepts placeholder API keys in one path.
- P2: text manifest sidecar write should be atomic.
- P2: project list/dashboard pagination and state-load health need better visibility.

## Autonomous Build Queue

Batch 1 is the current implementation scope. Batches 2-4 are backlog-only follow-ups from the comprehensive customer/product review; they are intentionally not part of the current safety-hardening diff.

- [x] Batch 1 - safety hardening:
  - [x] Fail closed on unknown QA blockers.
  - [x] Add typed `FlyerManualQueueCustomerUpdate` schema coverage.
  - [x] Serialize project/package delivery around pending selection, bridge send, and final commit; lock delivery/recovery audit appends.
  - [x] Detect stale claimed `in_progress` manual rows in recovery.
  - [x] Reject placeholder OpenRouter image keys.
  - [x] Make text-manifest sidecar writes atomic.
  - [x] Preserve stale-active-project bypass for pre-delivery media-backed new work while keeping delivered/revising media edits on the active project.
- [ ] Batch 2 - edit/autonomy hardening:
  - [x] Split source-edit manual rows from non-source manual rows in `update-flyer-project`.
  - [x] Tighten `already_applied` regeneration suppression using current selected-preview visual-QA evidence.
  - [ ] Pass source-edit generation failure reason codes consistently. Deferred to a separate reason-code PR because this slice is the customer-visible revision-routing lever.
- [ ] Batch 3 - product-depth improvements:
  - [ ] Expand starter prompts and campaign scene library.
  - [ ] Add preview fact checklist.
  - [ ] Add richer category-aware vague-request fallback.
  - [ ] Add real-model eval cases and rubric.
- [ ] Batch 4 - Hermes brain integration:
  - [ ] Add real shadow-only Hermes gateway adapter for existing intent schema.
  - [ ] Route semantic brief/item/localization provider calls through a Hermes-owned seam with audit.
  - [ ] Add prompt provenance sidecars/audit for repair diagnostics.

## Batch 1 Review And Verification

- Review pass 1:
  - Structural reviewer found `age_minutes` must be float because `flyer-source-edit-sla-watchdog` emits one-decimal values. Fixed with schema/test coverage.
  - Customer-safety reviewer found pre-delivery media-backed work could be swallowed by stale active projects. Fixed by excluding `awaiting_final_approval` from the delivered/revising carve-out and preserving the existing bypass.
  - Hermes/drift reviewer found delivery locking still allowed duplicate sends because the bridge call sat outside the lock. Fixed with a per-project delivery lock covering pending selection, bridge sends, and final commit, plus a concurrency test.
- Review pass 2:
  - Customer-safety reviewer: no blockers.
  - Structural/Hermes reviewer: no blockers; residual pre-existing crash window after bridge success before state persistence remains backlog risk.
- Verification:
  - Focused RED/GREEN tests: `10 passed`.
  - Changed-path broad suite: `634 passed`.
  - Adjacent Flyer generation/manual/update suites: `172 passed`.
  - Full Flyer-focused gate: `1539 passed, 1 skipped`.
  - Full repository gate: `2765 passed, 867 skipped`.

## Batch 2 Review And Verification

- Review pass 1:
  - Customer-safety reviewer found `already_applied` proof could still use a stale QA row because it matched only asset id/version/status/text. Fixed by requiring the selected preview asset SHA, `output_format="concept_preview"`, and non-sidecar QA before suppressing regeneration.
  - Hermes/drift reviewer found the same local visual-QA-contract bypass and a misleading noncanonical `source_edit_generation_failed` reason-code predicate. Fixed the proof contract and removed that reason-code predicate from this slice.
- Review pass 2:
  - Customer-safety reviewer: no findings; confirmed manual-row routing and current-preview QA proof are scoped correctly.
  - Hermes/drift reviewer: no findings; confirmed the slice stays inside existing Flyer state/QA primitives and the deferred reason-code cleanup is separate scope.
- Verification:
  - Focused RED/GREEN proof tests: invalid hash, final-format QA, and sidecar QA all failed before the fix and pass after the fix.
  - Focused update/workflow suite: `74 passed`.
  - cf-router/status adjacent suite: `372 passed`.
  - Flyer-focused gate: `1544 passed, 1 skipped`.
  - Full repository gate: `2770 passed, 867 skipped`.
  - During the Flyer-focused gate, a pre-existing test-isolation leak was reproduced and fixed: a cockpit test now stubs `safe_io` via `monkeypatch` and accepts bridge kwargs so later cf-router tests do not inherit a stale bridge stub.
