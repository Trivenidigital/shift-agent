**Drift-check tag:** extends-Hermes

# Flyer Edit Flow Hardening Design

**Goal:** Make Flyer Studio edit requests safe, reliable, and customer-honest without replacing Hermes substrate.

**New primitives introduced:** source-edit provider readiness helper, revision no-op preservation rule, stricter edit patch parser, admin edit-queue/stuck-work visibility fields.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress, media, sender identity | yes - deployed Hermes gateway/cf-router and sender helpers | use it |
| State/audit/delivery | yes - JSON stores, `safe_io`, bridge helpers, decisions log | use it |
| Installed VPS skills | partial - `flyer_generation`, `productivity/ocr-and-documents`, `productivity/nano-pdf`, `creative/comfyui`, `mlops/*` are installed | use as adjacent substrate; keep Flyer account/quota/edit loop in repo |
| Source-preserving image editing | partial - ComfyUI/img workflows exist in ecosystem, but no installed Flyer-specific edit approval loop | keep current provider path, add readiness/fallback |
| PDF text editing | yes - `nano-pdf` adjacent | defer PDF exact-edit support; fail closed with clear copy |

Awesome-Hermes ecosystem check: official/bundled catalog and Awesome Hermes show adjacent creative/OCR/PDF skills, but none own the Flyer WhatsApp account, quota, preview/final package, and approval state machine. Verdict: narrow custom Flyer hardening is justified.

## Current Failure Shape

The edit pipeline currently breaks in four visible ways:

1. Source-preserving edits can send “editing now” copy before the runtime proves the edit provider is usable.
2. Unclear edits can move a project into `revising_design` and clear current concepts/finals even when no actionable patch exists.
3. Common customer edits like “remove extra 08:00” and “change Kheema Dosa price to $9.99” are not parsed reliably.
4. Manual/source edit queue state is not prominent enough for operator follow-up, so “queued” can become a silent dead end.

## Design Principles

**Do not lose the last good flyer.** A clarification/no-op edit must preserve the prior `status`, `concepts`, `selected_concept_id`, and `final_asset_ids`. Only an actionable patch or visual-only revision can invalidate the current preview/finals.

**Do not promise active generation until preflight passes.** Source-edit routes must check provider readiness before quota reservation and before the 5-6 minute processing acknowledgement. Missing `OPENAI_API_KEY`, unsupported PDF source artwork, or missing reference media should go straight to designer-assisted/manual queue copy.

**Use deterministic patching only where confidence is high.** The parser should convert common edit language into explicit instructions in `notes` and `raw_request`. If old price/item context is absent or repeated, it should return an actionable clarification instead of guessing.

**Make queued work visible.** Manual edit work remains represented by the existing Flyer project row, but admin summary and project list should surface `manual_edit_required` and stuck `revising_design` projects with age/updated age so the operator can triage.

**Quota must not silently leak.** When source-edit generation fails after a reservation, release result must be checked. If release fails, audit detail must include it and customer copy must avoid implying the credit/sample is available.

## Components

### Revision Parser

`src/agents/flyer/workflow.py` continues to return `RevisionPatchResult`. It gains patterns for:

- removing duplicate/extra time text without changing structured event time;
- add/remove item instructions;
- item-specific price-to-new-price updates when the item exists exactly once;
- swap instructions that include prices.

Expected output is append-only instructions in `notes_update` and `raw_request_update`, not destructive OCR-style editing. The renderer/prompt layer then has clear directives.

For item-specific price changes without an old price, the parser searches `fields.notes` first, then `raw_request`. It matches the requested item name case-insensitively, finds the nearest price token in the same sentence or comma-delimited item segment, and replaces only that segment's price. If the item appears zero times, appears multiple times, or appears once without an adjacent price, it returns `ambiguous=true` with an unresolved reason naming the item.

### Update Script State Guard

`src/agents/flyer/scripts/update-flyer-project` parses the patch before committing a state transition. If `revision_requires_clarification` is true, it leaves the prior persisted project record intact except for the response envelope; status, version, updated_at, revisions, fields, raw_request, assets, concepts, selected concept, and final asset ids remain unchanged. It may return the same JSON envelope with `revision_requires_clarification=true`, but it must not clear concepts/finals or trap approval in `revising_design`.

Actionable patches keep the existing behavior: append revision, clear concepts/finals, move to `revising_design`, and require regeneration before approval.

Manual/source-edit queue projects are a carve-out: follow-up corrections on `manual_edit_required` append the correction to the source-edit request, keep status `manual_edit_required`, do not clear any assets/concepts, and do not auto-regenerate. The customer receives the designer-assisted queue copy again.

### Source-Edit Provider Preflight

The source-edit readiness check lives where the customer promise is made: cf-router/actions/hooks, and it is a shared helper used by every source-edit generation entry point before quota reservation or active processing copy:

- new exact media edit branch;
- reference-scope authorized edit branch;
- manual/source-edit follow-up branch if it ever attempts generation;
- revision/regeneration branch when the active project is a source-edit project.

It uses the same conditions as the renderer:

- source edit requires image reference media, not PDF;
- OpenAI edit endpoint requires nonblank `OPENAI_API_KEY` for the configured edit model;
- missing provider or unsupported media returns a structured reason.

When preflight fails, router sends designer-assisted/manual queue copy and returns `skip`; it must not fall through to the generic LLM/dispatcher. When preflight passes, router can reserve quota and send “editing now, 5-6 minutes.” The fallback Flyer dispatcher/SKILL text should not promise source-edit regeneration without this same helper path.

### Router Copy And Quota Release

Manual queue copy should be honest:

`I saved this as a designer-assisted source edit. Support will review it and send the corrected flyer here.`

Follow-up corrections on `manual_edit_required` projects are saved as additional notes and repeat the same honest expectation. No copy should imply an automatic worker exists unless generation actually started.

`_release_flyer_access` becomes result-bearing for both `quota` and `guest` access. All call sites check the result: source-edit generation failure, preview delivery failure, and access-finalize failure. The customer still receives a useful response, but the audit detail distinguishes normal queueing from queueing with a stuck reservation or guest-order release failure.

### Admin Visibility

Backend summary includes:

- `manual_edit_count`;
- `stuck_edit_count`;
- existing `stuck_projects` remains for compatibility and may include the new stuck edit count or continue as the broader count;
- per-project `age_minutes` and `updated_age_minutes` or equivalent ISO timestamps already sufficient for frontend display.

Frontend uses existing Projects tab/table when possible. If no new visible controls are necessary, avoid adding mutation UI in this PR.

Silent-dead-end prevention uses a dashboard SLO flag in this PR, not a new notification daemon. `manual_edit_required` older than 30 minutes and `revising_design` with zero concepts older than 10 minutes are marked as stale/stuck in admin summary. A Daily Brief or push alert for these SLOs is a follow-up backlog item unless an existing alert path can be reused without expanding scope.

## Testing Strategy

Tests are red-first:

- no-op/unclear revision preserves full project state;
- manual-edit follow-up keeps `manual_edit_required` and appends correction without regeneration;
- actionable natural edits produce expected patch fields and invalidate designs;
- missing provider/PDF source edit does not send active processing acknowledgement across exact-edit and authorized-reference branches;
- generation-failure, preview-failure, and finalize-failure access release is checked/audited for quota and guest paths where applicable;
- admin summary surfaces manual/stuck edit counts;
- subprocess/state-file tests are authoritative for update behavior; static script tests only enforce that conditional invalidation exists;
- existing Flyer routing, renderer, delivery retry, backend admin, and script-static tests still pass.

Linux-only cf-router tests remain allowed to skip on Windows, but any behavior added there must have either a pure helper test or be covered in CI/reviewer Linux runs.

## Non-Goals

- Build a full human designer work queue with assignment, SLA, upload final, and payment handling.
- Add PDF source-preserving edit support.
- Replace the current image-edit provider with ComfyUI or a new Hermes skill.
- Fix Instagram story/platform-specific creative truthfulness in this PR.
