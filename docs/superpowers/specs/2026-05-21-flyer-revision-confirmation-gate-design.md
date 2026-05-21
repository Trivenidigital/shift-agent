# Flyer Revision Confirmation Gate Design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `FlyerPendingRevisionConfirmation` persisted on `FlyerProject` to gate ambiguous revisions behind an explicit `APPLY <revision_id>` reply.

## Problem

Customers reply to preview messages with vague edits like:

- `Replace "Price any event" with "Any Item"`
- `Change the green badge line that reads "Price any event" to "Any Item"`

Today, `update-flyer-project --revision-text` only applies high-confidence structured edits (date/time/price/phone/etc.) and relies on exact substring matching for replacements. Whitespace/punctuation differences (or multi-occurrence) trigger "could not match ... send exact text" even when intent is clear.

## Goals

- Accept "replace text" edits robustly (whitespace-insensitive, punctuation-tolerant) without requiring exact matches.
- When the system can interpret a change but it's ambiguous/risky, send back the constructed interpretation and require `APPLY <revision_id>` before regeneration.
- Preserve `APPROVE` semantics: `APPROVE` always means "finalize files", never "apply change".

Non-goals:
- No provider/model posture changes.
- No source-edit provider changes.
- No new payment/quota/account mutations.

## Drift Check

Authoritative state store: `/opt/shift-agent/state/flyer/projects.json` (`FlyerProjectStore` / `FlyerProject`, `extra="forbid"`).

| Area | Current | Change |
|---|---|---|
| Revision patch extraction | `agents.flyer.workflow.extract_revision_patch` | Extend with replace-text parsing + confidence classification |
| Project state | `FlyerProject` forbids extras | Add one optional `pending_revision_confirmation` field |
| Approval routing | cf-router finalizes on `APPROVE` | Add `APPLY <revision_id>` handling for pending confirmations + safe reminders |

## Hermes-first Analysis

| Step | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/egress | yes | Reuse cf-router/Hermes substrate |
| Durable state | yes | Reuse JSON store + `safe_io.FileLock` |
| Revision routing | yes | Reuse existing flyer active-project intercept |
| Structured extraction | yes | Deterministic parsing first; optional future Hermes schema-extract fallback only when deterministic parse yields "unknown" |
| Approval patterns | yes | Reuse explicit reply gates; introduce Flyer-local `APPLY <revision_id>` token to avoid redefining `APPROVE` |

awesome-hermes-agent ecosystem check: no existing skill for Flyer "revision confirmation proposals". Verdict: implement a minimal Flyer policy layer atop Hermes substrate.

## Data Model

### New schema: `FlyerPendingRevisionConfirmation`

Add to `C:/Users/srini/.config/superpowers/worktrees/SME-Agents/automation-main/src/platform/schemas.py`:

- `revision_id: str` (reuse `FlyerRevision.revision_id`, e.g. `R0123`; stable id for idempotency)
- `created_at: datetime`
- `expires_at: datetime` (default 4 hours)
- `request_message_id: str`
- `request_text: str`
- `proposal_summary: str` (customer-visible "I understood ..." block)
- `patch: RevisionPatchResult` (typed patch payload we would apply)

Add to `FlyerProject`:

- `pending_revision_confirmation: Optional[FlyerPendingRevisionConfirmation] = None`
- `last_applied_pending_revision_id: str = ""` (idempotency guard; no-op if already applied)

Backward compatibility:
- Existing state files lacking the new fields validate (optional / defaults).
- Deploy is atomic, so old binaries reading new state is not supported; acceptable under current deploy model.

## Revision Parsing + Confidence

### Replace-text intent parsing

Add a deterministic extractor in `agents.flyer.workflow`:

Supported patterns (case-insensitive):
- `replace "<old>" with "<new>"`
- `replace '<old>' with '<new>'`
- `change "<old>" to "<new>"`
- `change the ... that reads "<old>" to "<new>"`

### Matching algorithm

Prefer a deterministic normalize+index-map matcher.

Implement `replace_once_normalized(source, old, new)`:

- Normalize `source` and `old` by:
  - lowercasing
  - collapsing whitespace to single spaces
  - dropping most punctuation (keep letters/digits/spaces)
- Keep an index map from normalized positions back to original positions so a normalized match can be replaced in the original string.
- Guardrails:
  - Require at least 3 tokens for normalized (fuzzy) matching (short phrases are too risky).
  - Cap normalized match span length (e.g., 200 chars).
- Match count:
  - 0 matches => not found
  - 1 match => replace only the mapped original span
  - >1 matches => ambiguous (clarification required in v0.1)

### Confidence policy

Classify the replacement attempt:
- `exact_applied`: exact substring match succeeded in notes/raw_request => apply immediately.
- `fuzzy_applied`: normalized match succeeded uniquely => confirmation required (safer).
- `ambiguous`: multiple fuzzy matches => confirmation required.
- `not_found`: no match => keep existing clarification path ("send exact text") with better guidance.

High-stakes surfaces (always confirmation required unless exact):
- price, date, time, phone, address/location

## State Transitions and Routing

### Pending proposal lifecycle (update-flyer-project)

On inbound revision text (status in `revising_design` / `awaiting_final_approval` / `delivered`):

1) If `pending_revision_confirmation` exists:
   - If inbound matches `APPLY <revision_id>` (case-insensitive; ignore punctuation) and not expired:
     - Apply stored patch
     - Set `last_applied_pending_revision_id`
     - Clear pending
     - Mark revision applied
     - Clear concepts to force regeneration
   - If inbound matches `CANCEL`: clear pending; no regeneration.
   - Else: overwrite pending with new interpreted proposal and return a new proposal message.

2) If no pending exists:
   - Extract patch (including replace-text).
   - If patch is confirmation-required: create a `FlyerRevision` record, persist `pending_revision_confirmation`, and do not clear concepts.
   - If patch is safe: apply immediately and clear concepts to force regeneration.

### cf-router behavior changes

When an active flyer project has `pending_revision_confirmation`:

- `APPROVE`: never finalize; reply with a reminder to reply `APPLY <revision_id>` first.
- `APPLY <revision_id>`: apply pending via `update-flyer-project`, then regenerate concepts and send previews.
- Other text: treat as a new revision request (which overwrites pending).

## Router precedence matrix (encode + test)

| pending exists? | inbound | outcome |
|---|---|---|
| yes | `APPROVE` | never finalize; send reminder "APPLY <revision_id> first" |
| yes | `APPLY <revision_id>` | apply pending patch; regenerate previews |
| yes | `CANCEL` | clear pending; do not regenerate |
| yes | other text | overwrite pending with new proposal or clarification |
| no | `APPROVE` | existing finalize flow |
| no | other text | existing revision parsing flow |

## Customer Copy

Proposal message (deterministic):

```text
Flyer Studio
------------
I understood your change as:
<proposal_summary>

Reply APPLY <revision_id> to regenerate a new preview (this does not send final files), or reply with corrections.
```

## Tests

- `agents.flyer.workflow` unit tests:
  - normalized replace success for whitespace/punctuation variations
  - multi-match ambiguous => confirmation required
  - short `old` (fewer than 3 tokens) => not eligible for fuzzy replace
- cf-router integration tests:
  - pending exists + `APPROVE` does not finalize and returns reminder copy
  - pending exists + `APPLY <revision_id>` applies and triggers regeneration path
  - duplicate `APPLY <revision_id>` is idempotent (no double regen)
