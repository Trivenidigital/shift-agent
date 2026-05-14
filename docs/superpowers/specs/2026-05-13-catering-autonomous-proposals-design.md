# Catering Autonomous Proposal Flow Design

**Drift-check tag:** `extends-Hermes`

**Status:** Approved direction; revised after Reviewer 1 and Reviewer 2
feedback; awaiting user review.

## Goal

Make catering inquiries feel 99% autonomous while preserving the one human gate
the user wants: final owner approval before any priced quote, booking
confirmation, deposit, or payment instruction reaches the customer.

The immediate user-facing behavior should be:

1. Customer sends a catering inquiry.
2. System creates the lead and acknowledges it.
3. Customer asks for menu proposals.
4. System sends two creative proposal options based only on the uploaded menu.
5. Customer chooses an option.
6. System sends that selected option to the owner for final approval.
7. Only after owner approval does the system send the final customer quote.

## New Primitives Introduced

- Source-controlled `creative_catering_proposals` Hermes skill, adapted from the
  live VPS skill and constrained to no pre-approval pricing.
- Sidecar JSON state file for proposal option sets:
  `/opt/shift-agent/state/catering-proposals.json`.
- Dedicated sidecar lock:
  `/opt/shift-agent/state/catering-proposals.json.lock`.
- Proposal validation/send script:
  `/usr/local/bin/create-catering-proposal-options`.
- Proposal selection/finalize script:
  `/usr/local/bin/select-catering-proposal`.
- `catering_dispatcher` route for active-lead proposal requests and proposal
  selections.
- `cf-router` Branch B carve-out so proposal workflow messages are not
  swallowed by the generic active-lead status suppression.
- Audit variants for generated/selected/failed proposal events.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Creative catering proposal copy | yes - live VPS skill `/root/.hermes/skills/catering/creative-catering-proposals/SKILL.md` | Use it as the starting skill, but source-control it and remove/override its price-range instruction. |
| Menu-backed proposal input | yes - live `/opt/shift-agent/state/catering-menu.json`, 78 uploaded items; existing `update_catering_menu` and `create-catering-lead` menu render path | Use existing menu state. Do not invent menu items. |
| Lead creation and owner approval | yes - `parse_catering_inquiry`, `create-catering-lead`, `handle_catering_owner_approval`, `apply-catering-owner-decision` | Reuse unchanged where possible. |
| Customer menu finalization | partial - `handle_catering_menu_finalize` and `finalize-catering-menu` exist, but current mode is `--auto-default` and does not map a selected proposal option | Extend with a proposal-selection script that feeds validated selected items into the existing finalize script. |
| Active-lead follow-up routing | partial - `cf-router` Branch B exists, but it suppresses proposal requests | Extend cf-router with a narrow carve-out for proposal workflow messages. |
| Official Hermes skill hub | none found for catering/restaurant proposal generation in the official skills catalog | Build as project skill on top of Hermes primitives. |
| Hermes skill substrate | yes - official docs describe skills as on-demand instruction bundles with shell/tool actions | Use a Hermes skill plus deterministic scripts, not a freeform chat-only implementation. |
| Hermes self-evolution kit | yes - `NousResearch/hermes-agent-self-evolution` exists | Defer. Useful later for improving proposal quality after the safe workflow is live. |
| Awesome Hermes ecosystem | no directly applicable maintained catering proposal workflow found | No external ecosystem adoption for this slice. |
| CLAUDE.md install-now skills + `mcp/native-mcp` | checked: google-workspace, maps, airtable, ocr-and-documents, notion, native-mcp | None apply to in-WhatsApp menu proposal generation; no external write API or document/OAuth substrate is needed for this slice. |

Awesome-hermes-agent ecosystem verdict: no drop-in catering proposal workflow
was found; the live VPS skill plus local deterministic state/scripts are the
right Hermes-first path.

## Drift Checks Performed

Relevant deployed/source primitives read before this spec:

- `src/plugins/cf-router/hooks.py`: current F7 primary path creates new leads
  and suppresses active-lead follow-ups.
- `src/plugins/cf-router/actions.py`: current canonical follow-up reply and
  owner approval invocation behavior.
- `src/agents/catering/scripts/create-catering-lead`: already loads
  `catering-menu.json`, renders a customer ack, writes lead state, and sends
  the owner card.
- `src/agents/catering/scripts/finalize-catering-menu`: validates selected
  items against the menu, writes `CUSTOMER_FINALIZED`, and sends the owner card.
- `src/agents/catering/scripts/apply-catering-owner-decision`: final customer
  quote is sent only after owner approval.
- `src/platform/schemas.py`: `CateringLead` has `extra="forbid"`, so proposal
  state should not be added directly to lead rows without a migration/rollback
  plan.
- Live VPS `/root/.hermes/skills`: confirmed `creative-catering-proposals`
  exists live but is not source-controlled in this checkout.
- Live VPS `/root/.hermes/plugins`: only `cf-router` is installed.

## Current Problem

The May 13 test showed the core lead path now works:

- employee/customer-side inquiry can create lead `L0014`
- active follow-ups no longer fall into generic LLM silence
- cf-router suppresses weak menu follow-ups and replies with a canonical
  "owner review pending" message

But that suppression is now too blunt. A customer asking for two proposal menus
is not only asking for status; they are asking the catering agent to do useful
autonomous work. Today Branch B treats that as a status follow-up and prevents
the live creative proposal skill from ever helping.

The old May 3 behavior looked better conversationally, but it was unsafe:
freeform LLM composition could invent menu items, invent prices, create
multiple leads, and move into payment/booking language. The new design keeps
the creative surface while moving all state writes and outbound customer text
through deterministic chokepoints.

## Policy

Pre-owner-approval customer messages may include:

- proposal option titles
- menu sections
- uploaded menu item names
- flavor/fit descriptions
- "choose option 1 or option 2" next step

Pre-owner-approval customer messages must not include:

- final quote totals
- per-person price ranges
- deposit amounts
- payment instructions
- booking confirmation language
- claims that date/menu/pricing is guaranteed

Owner-side cards may include internal current-menu estimates because they are
inside the approval gate. Customer-facing final pricing is sent only after
owner approval.

## Proposed Architecture

### 1. Source-Control The Live Proposal Skill

Add a source-controlled skill under `src/agents/catering/skills/`, based on
the live VPS `creative-catering-proposals` skill.

Required changes from the live copy:

- remove "include price ranges"
- require exact item names from `/opt/shift-agent/state/catering-menu.json`
- require JSON handoff to a validation script
- forbid direct `send_message`
- forbid payment/deposit/booking language
- update YAML frontmatter so the description points to the new validation
  script and no deprecated send-message/freeform-pricing behavior
- default to two proposal options unless the customer asks for three; the
  script enforces this cap deterministically

The skill's job is curation and structured drafting. The script's job is
validation, persistence, rendering, bridge send, and audit.

### 2. Store Proposal Options In A Sidecar File

Do not add proposal fields directly to `CateringLead` in v1. `CateringLead`
uses `extra="forbid"`, and storing new keys directly on lead rows would create
rollback risk for older binaries.

Use:

`/opt/shift-agent/state/catering-proposals.json`

Every read-modify-write uses `FileLock` on:

`/opt/shift-agent/state/catering-proposals.json.lock`

Suggested schema:

```json
{
  "schema_version": 1,
  "next_sequence": 2,
  "sets": [
    {
      "proposal_set_id": "CPS-L0014-000001",
      "lead_id": "L0014",
      "status": "SENT",
      "created_at": "2026-05-13T16:45:00+00:00",
      "sent_at": "2026-05-13T16:45:05+00:00",
      "outbound_message_id": "wamid...",
      "source_message_id": "wa_msg_id",
      "request_text": "She wants one mixed option and one premium option.",
      "options": [
        {
          "option_id": "1",
          "style_key": "balanced_mixed",
          "tier": "balanced",
          "item_names": ["Paneer Tikka Kebab (8 PCS)", "Chicken Biryani"]
        }
      ]
    }
  ]
}
```

Rules:

- `proposal_set_id` is allocated under `catering-proposals.json.lock` from
  the store-level `next_sequence` and formatted as
  `CPS-{lead_id}-{sequence:06d}`; never compute it from a stale unlocked
  snapshot.
- Lifecycle statuses are `DRAFT`, `SENT`, `SEND_FAILED`, `SUPERSEDED`,
  `SELECTING`, `SELECTED`, `SELECTED_OWNER_CARD_FAILED`, and `SELECT_FAILED`.
- Only `SENT` sets with a non-empty `outbound_message_id` are selectable.
- Only one `SENT` set per lead is active. A new successfully sent set marks
  previous `SENT` sets for that lead `SUPERSEDED` in the same locked write.
- `item_names` must exactly match currently available menu items.
- Customer-visible titles/summaries are rendered by the script from
  `style_key` and validated `item_names`; the script ignores any freeform
  title/summary supplied by the skill.
- No prices are stored in the proposal option itself; prices are looked up
  only when the customer selects an option and the owner approval path needs
  current menu prices.
- Superseded/failed/selected sets are retained for audit and customer support.
  V1 does not delete proposal rows on the write path. If the file grows past
  an operational threshold, a separate prune task may keep all `SELECTED` sets
  and the last 3 non-selected sets per lead; pruning is not part of the first
  implementation.

Lock order:

- Preferred shape avoids nested locks: read/copy lead under `LEADS_LOCK`, then
  release; write proposal state under `PROPOSALS_LOCK`; write audit under
  `LOG_LOCK`.
- If a future change truly needs nested locks, the order is
  `LEADS_LOCK` -> `PROPOSALS_LOCK` -> `LOG_LOCK`.
- Never hold `PROPOSALS_LOCK` while posting to the bridge or while invoking
  `finalize-catering-menu`.

### 3. Proposal Generation Script

Add `/usr/local/bin/create-catering-proposal-options`.

Inputs:

- `--lead-id`
- `--customer-jid`
- `--customer-message-id`
- `--request-text`
- `--options-json -` from stdin

Responsibilities:

- load/copy lead state under `LEADS_LOCK`, then release the lock
- load `catering-menu.json`
- validate every option item exists and is available
- enforce option count: 2 options by default; 3 only if `request_text` matches
  `(?i)\b(three|3)\b`; never more than 3
- reject customer-visible text containing price/payment/deposit language using
  the shared no-price regex below
- under `PROPOSALS_LOCK`, allocate the next `proposal_set_id` and persist a
  `DRAFT` set; do not supersede the prior active `SENT` set yet
- render the WhatsApp message from validated `style_key` + `item_names`
- send via the bridge using the same `chatId`/`message` payload convention and
  the same server-side template-bypass prefix pattern as `send-catering-ack`
- on bridge success, reacquire `PROPOSALS_LOCK`, mark the new set `SENT` with
  `outbound_message_id`, and mark prior `SENT` sets for that lead `SUPERSEDED`
- on bridge failure, reacquire `PROPOSALS_LOCK` and mark the new set
  `SEND_FAILED`; the set is not selectable
- emit audit rows

The script must not trust the skill's prose. It renders its own customer text
from validated fields. It should own the server-side bridge prefix directly or
factor a shared helper; it should not call `send-catering-ack`, because that
script emits acknowledgment audit rows rather than proposal-specific audit rows.
The customer sees the same `Catering Agent` header used by existing customer
acks for visual continuity.

No automatic LLM retry on unknown items in v1. If validation rejects an item
name after the skill read the current menu, the script fails closed, emits
`catering_proposal_generation_failed`, and alerts the owner. This avoids the
May 3 failure pattern of escalating customer pressure into repeated freeform
proposal generation.

No-price regex applied to the final rendered customer body:

```text
(?ix)
  \$\s*\d+
| \b\d+(?:\.\d{1,2})?\s*(?:usd|dollars?|bucks)\b
| \b\d+(?:\.\d{1,2})?\s*(?:/|per\s+)(?:person|plate|guest|head|pax)\b
| \b(?:price|priced|pricing|cost|costs|rate|rates|fee|fees|charge|charges)\b
| \b(?:deposit|payment|pay|paid|venmo|zelle|cash\s*app|cashapp|credit\s*card|invoice)\b
| \b(?:book|booking|booked|confirmed|confirmation)\b
```

### 4. Proposal Selection Script

Add `/usr/local/bin/select-catering-proposal`.

Inputs:

- `--lead-id` or sender identity lookup
- `--customer-message-id`
- `--selection-text`

Responsibilities:

- load latest active `SENT` proposal set for the lead; reject `DRAFT` and
  `SEND_FAILED` sets even if they are the newest rows
- resolve selection with this exact ladder:
  1. literal `option N`, `proposal N`, `menu N`, `#N`, or bare digit `N`
     wins when `N` is in the active set
  2. tier alias wins only when exactly one active option has matching
     `tier`, such as `premium`, `balanced`, or `classic`
  3. otherwise send a numbered clarification and do not finalize
- resolve the lead's `owner_approval_code` under `LEADS_LOCK`
- convert selected `item_names` into `selected_items` with current menu prices
- call the existing `finalize-catering-menu` path with explicit
  `--code`, `--customer-message-id`, `--selected-items-json`, and
  `--quote-total-usd`
- handle finalize exit codes explicitly:
  - `0`: mark proposal set `SELECTED`; send "Got it - your Option N selection
    is saved for owner approval. Final pricing comes after owner review."
  - `6`: state was persisted but owner-card bridge delivery failed; mark
    `SELECTED_OWNER_CARD_FAILED`, alert owner, and tell the customer only that
    the selection was saved for owner review
  - `2`, `4`, or `11`: mark `SELECT_FAILED`, do not claim owner review started,
    and send a short retry/clarification message
- emit `catering_proposal_selected` only for exit `0` or `6`; emit
  `catering_proposal_selection_failed` for validation/not-found/mismatch exits

If selection is ambiguous, send one short clarification listing the option
numbers. Do not create a new lead.

### 5. Routing Changes

`cf-router`:

- Add `F7_PROPOSAL_BRANCH_ENABLED = False` as a rollout flag, separate from
  `F7_ENABLED`. It stays false until schemas, scripts, skills, and dispatcher
  text are deployed and verified.
- Keep Branch A new-inquiry behavior unchanged.
- Add a proposal-workflow classifier outside the existing
  `classify_catering` / `_has_f7_followup_signal` gate. This is required
  because bare selection texts like "go with option 2" do not contain food,
  event, delivery, or headcount signals.
- In Branch B, before canonical follow-up suppression, and only when
  `F7_PROPOSAL_BRANCH_ENABLED` is true:
  - if text matches proposal selection and the sender has an active lead with
    a selectable `SENT` proposal set, invoke `select-catering-proposal`
    directly, audit `cf_router_intercepted reason=f7_proposal_selection`, and
    return `skip`
  - if text matches proposal request, return normal dispatch so Hermes routes
    it to the proposal skill instead of suppressing it
  - otherwise keep canonical status suppression

Proposal request classifier:

```text
REQUEST_VERB = (?i)\b(send|share|show|give|create|make|prepare|draft|suggest|propose|build|generate|want|wants|wanted|need|needs|needed|like|likes|request|requests)\b
REQUEST_OBJECT = (?i)\b(proposal|proposals|option|options|menu proposal|menu proposals|proposal menu|proposal menus|menu option|menu options)\b
PASSIVE_WAIT = (?i)\b(will wait|waiting|wait for|any update\??\s*$|thank you)\b
```

A proposal request is `REQUEST_VERB` within 80 chars before
`REQUEST_OBJECT`, unless the whole message only matches passive wait/status
language. This intentionally changes the old pinned behavior for actionable
messages like "She wants one veg/non-veg mixed option and another premium
option" to route to proposal generation. Passive strings such as "Will wait
for two menu proposals. Thank you!" and bare "Any update?" remain canonical
status suppression.

Proposal selection classifier:

```text
(?i)\b(?:go with|choose|select|take|pick|finalize|confirm|lock in|proceed with|we'?ll take|i'?ll take|she'?ll take)\b.{0,40}\b(?:option|proposal|menu)?\s*#?\s*([1-3])\b
| (?i)^\s*(?:option|proposal|menu)?\s*#?\s*([1-3])\s*$
| (?i)\b(?:go with|choose|select|take|pick|finalize|confirm|lock in|proceed with)\b.{0,40}\b(premium|balanced|classic)\b
```

`catering_dispatcher`:

- Owner code replies still route first.
- Active lead + proposal request routes to `creative_catering_proposals`.
- Active lead + proposal selection routes to the selection handler/script as a
  fallback if cf-router did not intercept it.
- New inquiry still routes to `parse_catering_inquiry`.

Updated `catering_dispatcher` decision matrix, in priority order:

| Condition | Route |
|---|---|
| `sender_role == owner` and `#XXXXX` matches active catering lead | `handle_catering_owner_approval` |
| non-owner, active lead, proposal-selection classifier matches, selectable `SENT` proposal set exists | `select-catering-proposal` handler/script |
| non-owner, active lead, proposal-request classifier matches | `creative_catering_proposals` |
| non-owner, active lead, existing customer-finalize terms match | `handle_catering_menu_finalize` |
| otherwise | `parse_catering_inquiry` |

`dispatch_shift_agent`:

- Do not add bare `proposal`, `option`, `premium`, or `balanced` to the global
  catering keyword list. That would misroute unrelated owner/employee texts.
- Add an active-lead-conditioned PR-CF-style addendum: when sender is non-owner
  and has an active non-terminal catering lead, route proposal-request or
  proposal-selection classifier matches to `catering_dispatcher`.
- Keep the existing global catering keyword row unchanged for new inquiries.

### 6. Final Owner Approval

The existing owner approval gate remains the final customer-facing quote gate.

After customer selection:

- lead transitions to `CUSTOMER_FINALIZED`
- owner sees selected proposal items
- owner replies `#XXXXX approve`, `edit`, or `reject`
- customer gets final quote only after approval

Before this feature ships, the owner card for proposal-generated selections
must relabel the current `Customer-confirmed total` wording to an owner-facing
warning such as: `Internal estimate from current menu item prices - review/edit
before approving final customer quote.` This avoids treating restaurant item
prices as guaranteed catering-package prices. The owner can still approve the
estimate, edit it, or reject it; the customer sees pricing only after that
owner action.

No customer payment/deposit text ships in this slice.

## Data And Audit

New schemas in `src/platform/schemas.py`:

- `CateringProposalOption`
- `CateringProposalSet`
- `CateringProposalStore`
- `CateringProposalsGenerated`
- `CateringProposalSelected`
- `CateringProposalSelectionFailed`
- `CateringProposalGenerationFailed`

The `LogEntry` discriminated union must include all new audit variants with
`Annotated[..., Tag("<type>")]`; otherwise the audit chokepoint rejects them.
`CfRouterIntercepted.reason` must also add `f7_proposal_selection` and any
proposal-generation pass-through reason strings before cf-router emits them.

Audit examples:

```json
{"type":"catering_proposals_generated","lead_id":"L0014","proposal_set_id":"CPS-L0014-000001","option_count":2,"outbound_message_id":"..."}
{"type":"catering_proposal_selected","lead_id":"L0014","proposal_set_id":"CPS-L0014-000001","option_id":"2","customer_message_id":"..."}
{"type":"catering_proposal_selection_failed","lead_id":"L0014","proposal_set_id":"CPS-L0014-000001","reason":"finalize_exit_11","detail":"..."}
{"type":"catering_proposal_generation_failed","lead_id":"L0014","reason":"unknown_menu_item","detail":"..."}
```

Because this creates a new activity state file, add an fsck/readiness check:

- active proposal sets must reference a live lead
- active proposal item names must exist in current menu or be marked drifted
- selected proposal sets must eventually have a matching
  `catering_menu_finalized` row
- `DRAFT` sets older than a short threshold and `SEND_FAILED` sets must not be
  selectable

When cf-router directly handles proposal selection and returns `skip`, the LLM
dispatcher does not emit `dispatcher_routed`. To keep routing reliability
monitoring intact, either:

- teach the dispatcher-accuracy report to treat
  `cf_router_intercepted reason=f7_proposal_selection` as a dispatcher-equivalent
  routed row for that raw inbound, or
- have the selector emit a dedicated dispatcher-equivalent audit row before
  invoking the script.

The implementation plan must pick one before coding. The preferred first slice
is updating the report to count the cf-router intercept reason because that
matches existing F7 primary-mode behavior.

## Error Handling

- Missing menu: send a canonical "menu needs owner review" ack and alert owner.
- Unknown item in LLM-generated JSON: fail closed and alert owner. No automatic
  LLM retry in v1.
- Bridge failure after proposal persistence: mark the set `SEND_FAILED`, emit
  failed audit row, and alert owner. A `SEND_FAILED` set is never selectable.
- Ambiguous customer selection: ask one numbered clarification.
- Active lead not found: fall through to new-inquiry path only if the message
  independently qualifies as a new inquiry; otherwise send a no-active-inquiry
  ack.
- Price/payment text in generated option: reject before send.
- Customer-visible prose grounding: generated text must be rendered from
  validated item names and closed `style_key` templates. Freeform title/summary
  from the LLM is not sent to the customer.

## Test Plan

Unit and subprocess tests:

- proposal schema round-trip and `extra="forbid"` behavior
- option validation rejects unknown menu items
- rendered-body validation rejects the exact no-price regex, including
  currency symbols, `usd`/`dollars`, per-person/per-plate wording, price/cost
  terms, deposit/payment rails, and booking/confirmation language
- proposal ID allocation is concurrency-safe under `PROPOSALS_LOCK`
- proposal generation writes sidecar JSON and sends exactly one bridge message
- proposal generation supersedes the prior active set for the same lead
- bridge failure leaves only `SEND_FAILED`/non-selectable state
- LLM-provided freeform title/summary cannot introduce customer-visible menu
  nouns outside validated item names
- selection by "option 2" finalizes the expected item set
- ambiguous selection asks for clarification and does not finalize
- selection resolves and passes the lead's `owner_approval_code` as
  `finalize-catering-menu --code`
- selection handles finalize exits `0`, `2`, `4`, `6`, and `11` without false
  customer claims
- cf-router proposal-selection regex fires outside the existing
  `classify_catering` gate for "go with option 2"
- cf-router proposal-request regex intentionally routes actionable strings such
  as "she wants one mixed option and one premium option" to proposal generation
- passive strings such as "Will wait for two menu proposals. Thank you!" and
  bare "Any update?" still get canonical owner-review replies
- active-lead-conditioned `dispatch_shift_agent` addendum routes proposal
  workflow texts without adding global bare `option`/`proposal` keywords
- owner card labels proposal-generated totals as internal menu-price estimates
- new audit variants are included in the `LogEntry` union

Live smoke:

1. Send customer inquiry.
2. Ask for "two proposals, one mixed veg/non-veg and one premium".
3. Verify `catering_proposals_generated` and customer receives two options with
   no prices.
4. Reply "go with premium option".
5. Verify `catering_proposal_selected` and `catering_menu_finalized`.
6. Verify owner receives approval card.
7. Owner approves.
8. Verify final customer quote is sent only after approval.

## Rollout Plan

1. Add spec-backed tests first.
2. Add schemas, `LogEntry` union entries, and proposal sidecar helpers.
3. Source-control and constrain the live proposal skill.
4. Add generation script.
5. Add selection script.
6. Update owner-card wording for proposal-generated selections.
7. Update `catering_dispatcher` and `dispatch_shift_agent`.
8. Add `F7_PROPOSAL_BRANCH_ENABLED = False` and cf-router Branch B carve-out.
9. Run focused tests locally with the flag both false and true.
10. Build tarball and deploy with the flag false.
11. Verify scripts/skills are present and smoke import checks pass.
12. Enable `F7_PROPOSAL_BRANCH_ENABLED = True` on the VPS and restart gateway.
13. Run the live smoke above.

Rollback:

- Set `F7_PROPOSAL_BRANCH_ENABLED = False` and restart gateway.
- Existing generic active-lead suppression resumes.
- Sidecar proposal state is ignored by older flow.

## Non-Goals

- No payment collection.
- No deposit requests.
- No customer-facing prices before owner approval.
- No direct booking confirmation.
- No generic freeform LLM customer conversation for active leads.
- No cross-VPS shared proposal memory.

## Open Risks

- Current menu prices may be restaurant item prices, not catering package
  pricing. V1 mitigates this by relabeling owner-card totals as internal menu
  price estimates before owner approval. A future pricing model can add a
  catering-specific multiplier/table if owners need more automation.
- The live proposal skill currently asks for price ranges. The implementation
  must source-control the skill and change that instruction before routing any
  customer traffic to it.
- Letting proposal-intent messages through cf-router increases dependence on
  dispatcher correctness. Tests must pin proposal route selection before deploy.
