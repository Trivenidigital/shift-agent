# Flyer Revision Confirmation Gate Plan

**Drift-check tag:** extends-Hermes

**New primitives introduced:** Pending revision confirmation object on `FlyerProject`, plus deterministic copy for “I understood your change as … Reply APPLY”.

## Goal

Handle vague customer “change this text” messages (like “Replace ‘Price any event’ with ‘Any Item’”) without forcing an exact string match. When the system can interpret a change but cannot apply it with high confidence, send back the constructed interpretation and require an explicit customer approval before regenerating.

Non-goals:
- Do not change provider routing, model posture, or source-edit provider policies.
- Do not change payment/quota/account state.
- Do not change campaign send flows.
- Do not add new deployment/ops steps.

## Drift Check

| Existing primitive | Current behavior | Plan decision |
|---|---|---|
| `update-flyer-project --revision-text` | Parses a revision patch, applies structured edits, then clears concepts to force regeneration | Extend patch extraction to support whitespace-insensitive replacements and optional confirmation gating |
| `cf-router` flyer approval routing | `APPROVE` finalizes assets when status is `awaiting_final_approval` | Preserve `APPROVE` for finalization; use a distinct token (`APPLY`) for confirming a proposed revision |
| Text manifest sidecar (`write_text_manifest`) | Stores declared render facts for each generated artifact | Reuse as supporting evidence (optional), but do not require OCR/vision for basic text replace |
| Existing “clarification required” flow | Asks for “exact text to change” and blocks regeneration | Replace with a deterministic confirmation proposal when we have a plausible interpretation |

## Hermes-first Analysis

| Step | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/egress | yes - Hermes gateway + cf-router | Reuse |
| Sender identity | yes - `identify-sender` helpers | Reuse |
| Durable state | yes - JSON store + `safe_io` locking | Reuse |
| Revision routing | yes - cf-router interception substrate | Reuse |
| Structured extraction | yes - Hermes can do schema extraction | Prefer deterministic parsing first; reserve Hermes extraction for later if needed |
| Approval pattern | yes - “reply APPROVE” flows exist | Preserve existing finalize meaning; add a Flyer-local `APPLY` token for revision confirmation |

awesome-hermes-agent ecosystem check: no existing Flyer-specific “revision confirmation gate” skill found. Verdict: implement Flyer policy locally on Hermes substrate.

## Proposed Behavior

### 1) Better text replacement without requiring exact substring

When the customer message matches a simple replace intent:
- `Replace "<old>" with "<new>"` (and common variants)

Apply a single replacement in:
1) `project.fields.notes` if match is found
2) else `project.raw_request`

Matching should be:
- Case-insensitive
- Whitespace-insensitive (spaces/newlines/tabs treated as equivalent)
- Tolerant of punctuation between tokens (e.g. `Price any event -` still matches `Price any event`)

### 2) Confirmation gate for ambiguous/risky edits

When we interpret a change but can’t apply it with high confidence:
- multiple possible matches (appears 2+ times)
- fuzzy match needed (whitespace-insensitive/punctuation-tolerant match rather than exact)
- high-stakes target surface (price/date/time/phone/address) without exact match

Then:
- Persist a `pending_revision_confirmation` on the project with a human-readable proposal (including a short stable id for idempotency).
- Reply to the customer with:
  - “I understood your change as: … Reply APPLY to proceed (this regenerates a new preview; it does not finalize files).”
- Do not regenerate until approval.

### 3) Approval semantics

If `pending_revision_confirmation` exists:
- An inbound `APPLY` applies the pending patch, clears pending state, and triggers regeneration.
- An inbound `APPROVE` continues to mean “finalize files” (and should reply with a short reminder to reply `APPLY` first if a pending proposal exists).

If no pending confirmation exists:
- Existing behavior remains (APPROVE finalizes).

### 4) TTL + overwrite behavior

- `pending_revision_confirmation` carries an `expires_at` (target: 4h).
- If the customer sends a new revision message while a pending proposal exists, overwrite the pending proposal with the new interpretation and re-ask for `APPLY`.
- If a customer replies `CANCEL`, clear the pending proposal and return to the normal “tell me what to change” flow.

## Tests / Verification

- Unit tests for whitespace-insensitive replace success (`Price any\nevent` vs `Price any event`).
- Unit tests for “multiple match => confirmation required”.
- cf-router routing test: `APPLY` applies pending revision and regenerates.
- cf-router routing test: `APPROVE` while pending exists does not finalize; replies with “Reply APPLY to apply the pending change first.”
- Focused pytest selection for flyer + cf-router tests.
