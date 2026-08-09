# Wave 2 · Workflow 2 — Expense Bookkeeper receipt review (PLAN ONLY)

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:** none. One deterministic cf-router arm mirroring the
deployed menu-ingestion arm, plus one config key. No new plugin, no new script, no
new SKILL, no new store, no new framework.

## Hermes-first capability checklist

| # | Step | `[Hermes]` / `[net-new]` | Net-new LOC |
|---|---|---|---|
| 1 | Owner sends a receipt photo with a caption to WhatsApp | `[Hermes]` — source origins: WhatsApp inbound media | 0 |
| 2 | Bridge delivers the inbound to `hermes-gateway` | `[Hermes]` — gateway transport | 0 |
| 3 | `pre_gateway_dispatch` hook fires | `[Hermes]` — plugin hook substrate; cf-router already registers it | 0 |
| 4 | Sender resolves to owner / verified employee | `[Hermes]` — identity + role gating via `identify-sender` | 0 |
| 5 | Local media path resolved for the image | `[Hermes]` — `_extract_media_path` deployed in cf-router | 0 |
| 6 | Recognise the inbound as a receipt ingestion | `[net-new]` — the deployed analogue is menu-specific; which caption words mean "business receipt" is per-customer meaning | ~20 |
| 7 | Write `dispatcher_routed` audit BEFORE acting | `[Hermes]` — `audit_dispatcher_routed` exists | 0 |
| 8 | Invoke `extract-receipt` deterministically by exact argv | `[net-new]` — required only because the SKILL entry is suppressed by `disabled_toolsets` | ~45 |
| 9 | Copy image to managed storage + perceptual-hash dedup | `[Hermes]` — deployed inside `extract-receipt` (SKILLs are scripts with filesystem access) | 0 |
| 10 | Vision-extract line items + total | `[Hermes]` — LLM gateway vision, deployed inside the script | 0 |
| 11 | Validate against the receipt schema | `[Hermes]` — structured output + Pydantic, deployed | 0 |
| 12 | Classify personal vs business | `[Hermes]` — deployed inside the script | 0 |
| 13 | Persist `ExpenseLead` at a truthful review-only status | `[net-new]` — the store and code pool are deployed, but no existing status describes "extracted for review, never submitted for approval"; `DRAFTED` + its transition/retention/approval-closed wiring is the correction | ~15 |
| 14 | Re-read the durable store to verify the lead exists | `[net-new]` — project false-success invariant; the script's stdout is a claim, not proof | ~15 |
| 15 | Deterministic review card at egress; **stop, no QBO** | `[net-new]` — receipt totals are money facts and must not be model-worded | ~20 |

**Net-new: 5 of 15 steps (33%)** — under the red-flag threshold. The check paid for
itself: vision, OCR, classification, dedup hashing and the code pool were all
initially assumed in scope and are all deployed. Step 13 moved from `[Hermes]` to
`[net-new]` after the operator's semantic correction — persisting a *truthful*
review-only record is not pure substrate reuse, because the deployed status set
has no state meaning "extracted, never submitted for approval". **SKILL prose:
zero.**

### Domain-level Hermes-first table

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp inbound image ingestion | yes — Hermes source origins, surfaced by `_extract_media_path` | use it |
| Vision extraction of receipt fields | yes — Hermes LLM gateway vision, invoked inside `extract-receipt` | use it |
| Structured output / schema validation | yes — Pydantic `ExpenseLead`, deployed | use it |
| Personal-vs-business classification | yes — deployed inside `extract-receipt` | use it |
| Perceptual-hash dedup | yes — deployed inside `extract-receipt` | use it |
| Approval codes | yes — `approval_code_pools`; expense already a registered pool | **deliberately NOT used** in the DRAFT tier: a code implies a reachable approval |
| Durable persistence | yes — per-VPS JSON under `state/expense-bookkeeper/` | use it |
| Audit chain | yes — `decisions.log` chokepoint | use it |
| Deterministic owner-photo routing | yes — deployed menu-ingestion arm | mirror it |
| QuickBooks write | **none** — `RealQBOClient` is a stub that raises | out of scope (§6) |

awesome-hermes-agent / optional-skills check: nothing in the ecosystem covers a
QuickBooks Online write; that finding is already recorded in `CLAUDE.md` and is
re-confirmed here. Every other domain is deployed in-tree, so this workflow
contributes **reachability and truthfulness only**.

## Drift-rule self-checks

- ✅ Read `src/plugins/cf-router/hooks.py` (`_menu_caption_cedes_to_dispatcher` at 4546, `_owner_menu_ingestion_impl` at 4668, `_extract_media_path` at 6582, expense fall-through at 1245) before proposing any routing arm — this is the routing/dispatcher work-type requirement.
- ✅ Read `src/agents/expense_bookkeeper/scripts/extract-receipt` (argv contract + exit codes 0/2/3/5/6/7/9 in the module docstring; `cfg.expense_bookkeeper.enabled` gate at 508) before proposing an invocation, so the arm matches the deployed CLI exactly.
- ✅ Read `src/platform/qbo_client.py` (`RealQBOClient.__init__` raises at 293-302; `MockQBOClient` the only concrete impl) before scoping anything QBO-adjacent.
- ✅ Read `src/platform/schemas.py` (`qbo_client_mode` default `"mock"` at 3647) before assuming a push mode.
- ✅ Read `src/agents/expense_bookkeeper/skills/expense_bookkeeper_dispatcher/SKILL.md` (routes on `image_only` / `image_with_caption`) to mirror the intended trigger and to surface the deliberate narrowing in BQ-2.
- ✅ Read `src/agents/expense_bookkeeper/templates/expense_approval_card_to_owner.txt` and `expense_pushed_confirmation.txt` before deciding where the workflow must stop.

Deployed-pattern conformance: JSON-on-disk + `safe_io` (unchanged), audit via the
existing chokepoint (unchanged), 5-char codes from the shared pool (unchanged),
dispatcher audit written **before** delegating (preserved), image copied from the
transient Hermes cache to managed storage (already done by the script). No SQLite,
no new store, no parallel approval generator.

---

## 1. Goal

Make the already-built Agent #21 receipt-review path reachable for a real owner
receipt photo, and stop at a **truthful owner-facing review card**. No QuickBooks
mutation, and no customer-facing promise the runtime cannot honour.

## 2. Repository evidence (`origin/main` @ `536dbf4`)

Agent #21 is **fully built and completely unreachable**. All of this is already
deployed on the production box:

| Artifact | Evidence |
|---|---|
| `extract-receipt` (753 LOC) | vision extract, managed-storage copy, perceptual hash, classification, approval code, lead persistence, card render |
| `apply-expense-decision` (813 LOC) | code+amount confirmation, lead transition, QBO call, undo window |
| `prune-and-expire-expenses.py` + systemd timer | retention |
| 3 SKILLs | `expense_bookkeeper_dispatcher`, `parse_receipt_photo`, `handle_expense_owner_approval` |
| 11 templates | approval card, threshold, dedup, mismatch, pushed-confirmation, undo |
| `src/platform/qbo_client.py` (338 LOC) | Protocol + `MockQBOClient` + `RealQBOClient` **stub** |
| `src/platform/approval_code_pools.py` | expense already a registered pool in cf-router's resolution order |

All three scripts are installed at `/usr/local/bin/` on the box.

## 3. Current actual reachability — **zero**

Three independent blocks, verified live:

1. **SKILL entry is suppressed.** The only documented entry is
   `expense_bookkeeper_dispatcher`, a SKILL. `agent.disabled_toolsets` lists
   `skills` (and `terminal`), applied last and globally by name. Same root cause
   as Workflow 1.
2. **cf-router has no receipt arm.** No receipt-image path exists in
   `src/plugins/cf-router/*.py`; owner photos are claimed only by the menu arm,
   which requires a menu caption trigger.
3. **The agent is not enabled.** `expense_bookkeeper` is **absent** from the live
   `/opt/shift-agent/config.yaml`, and `extract-receipt` exits 3 when
   `cfg.expense_bookkeeper.enabled` is false.

Corroborating live state: `state/expense-bookkeeper/receipts/` exists but is
**empty**, created 2026-05-03 and never written.

**The approval reply is also unreachable.** `hooks.py:1245` resolves the code
through the pool registry then explicitly falls through for expense codes — *"fall
through so the LLM/dispatcher routes them"* — and that dispatcher is the disabled
SKILL. An owner replying `#CODE 12.34` today reaches nothing. This is why the card
cannot ship with its approve/reject lines intact (§8).

`/opt/shift-agent/venv/` is absent, but this is **not** a runtime blocker:
`extract-receipt` is `#!/usr/bin/env python3` and system `python3` has pydantic
2.12.5. The absent venv gates only smoke coverage and the prune-timer check — a
**verification** gap, recorded as a follow-up.

## 4. Existing receipt ingestion / extraction path

`extract-receipt --image-path … --source-image-id … --owner-phone … [--sender-lid …]`
→ JSON `{expense_id, approval_code, approval_card_text, extraction_confidence,
image_phash, duplicate_of}`. The owner-confirmed total is documented as the source
of truth for any future push, with the extracted total advisory — an existing
prompt-injection defence this workflow preserves and does not touch.

## 5. Existing deterministic persistence / approval path

`ExpenseLead` in `schemas.py`; 5-char codes from the shared pool with cross-agent
collision detection already wired in cf-router; durable JSON under
`state/expense-bookkeeper/` via `safe_io`; audit through the `decisions.log`
chokepoint. **None of this needs building.**

## 6. Current QBO credential / runtime state — **not production-ready**

- `RealQBOClient.__init__` **raises `NotImplementedError`** (`qbo_client.py:293-302`), documented as v0.2.
- `MockQBOClient` is the only concrete implementation; `qbo_client_mode` defaults to `"mock"`.
- **No `/opt/shift-agent/.qbo-tokens.json` on the box.**

The repository therefore does **not** prove a supervised QBO path is production-ready
or authorized, so the operator's precondition for touching QuickBooks is unmet.

**Sharpest consequence:** `expense_pushed_confirmation.txt` renders *"{{expense_id}}
pushed to QuickBooks … Tx ID: {{qbo_transaction_id}}"*. Under `mock` mode that
sentence and its transaction id are **fabricated**. Wiring the approval reply today
would tell the owner their expense reached Intuit when nothing left the box. That
single fact sets this workflow's boundary.

## 7. Hermes vs deterministic ownership

| Concern | Owner | Why |
|---|---|---|
| Understanding an ordinary owner message | Hermes | no classifier, no regex intent |
| Deciding *whether* to extract | **deterministic** | media + documented caption trigger + owner role, mirroring the deployed menu arm |
| Vision extraction, classification, dedup | Hermes (inside the script) | already deployed |
| Approval code, persistence, audit | deterministic | money-adjacent, already deployed |
| Wording of the review card | **deterministic** | receipt totals and vendor are money facts; Workflow 1's precedent binds them to the turn and substitutes at egress |

## 8. Smallest deliverable authority tier

**DRAFT tier — ingestion + a genuinely review-only record. No approval, no push.**

**Correction applied (operator ruling, 2026-08-09).** An earlier draft of this
plan proposed running the extractor unchanged and hiding the approve/reject lines
from the card. That was wrong, and the defect was semantic rather than cosmetic:
on every successful extraction the deployed extractor mints an approval code,
persists `AWAITING_OWNER_APPROVAL` and emits `expense_owner_approval_requested`.
Hiding two lines of prose would have left the durable record claiming an approval
had been requested when no approval path exists — and once
`expense_bookkeeper.enabled` is true, `prune-and-expire-expenses` expires those
rows and proactively tells the owner *"An expense you were asked to approve …
expired"*. A wording fix would have become a durable state and audit lie, which is
the exact failure class Wave 1 was spent eliminating.

The tier is therefore implemented as an explicit `--review-only` mode in the
**existing** extractor (no second extractor), plus one truthful state:

`receipt image → existing media handling → existing vision extraction → existing
classification → existing dedup → persist ExpenseLead at DRAFTED → deterministic
review card → stop`

`--review-only` must not: mint an approval code; enter `AWAITING_OWNER_APPROVAL`;
emit `ExpenseOwnerApprovalRequested`; call `apply-expense-decision`; construct a
QBO client; create mock-QBO transaction state; emit `ExpensePushAttempted`; or
claim anything was posted to QuickBooks. `owner_approval_code` stays null.

`DRAFTED` is added to `ExpenseLeadStatus` with the single legal transition
`EXTRACTING → DRAFTED`, no outbound edge, and membership in
`EXPENSE_RETENTION_CANDIDATES` so review receipts prune normally instead of
becoming immortal, plus `EXPENSE_APPROVAL_CLOSED_STATUSES` so no code lookup can
resolve one. Reusing `REJECTED`, `EXPIRED`, `EXTRACTING` or
`AWAITING_OWNER_APPROVAL` was rejected as semantically false.

The review card is a **dedicated deterministic template branch**, not regex
deletion of approval prose from the existing templates — each of those states an
approval affordance in its own body, so post-processing would be fragile and could
silently regress when a template is edited. It preserves expense id, vendor, date,
top items, total, classification, rationale, low-confidence marker, and duplicate
and threshold warnings, and ends with the committed sentence
*"Review only — this expense has not been posted to QuickBooks."* Idempotency
wording is review-shaped, never "already processing approval".

Deliberately **excluded**: approval reply, lead transition, any QBO call, undo.
Those need a real QBO decision first.

## 9. Exact changed-file proposal

| File | Change | Kind |
|---|---|---|
| `src/plugins/cf-router/actions.py` | `is_receipt_caption()` predicate; `invoke_extract_receipt()` argv wrapper mirroring `invoke_parse_menu_photo` | product |
| `src/plugins/cf-router/hooks.py` | `_receipt_caption_cedes_to_dispatcher()` + `_owner_receipt_ingestion_impl()`, placed **after** the menu gate so menu precedence is unchanged | product |
| `src/platform/schemas.py` | `DRAFTED` status + `EXTRACTING → DRAFTED` transition, retention-eligible, approval-closed | product |
| `src/agents/expense_bookkeeper/scripts/extract-receipt` | `--review-only` mode: no code, no approval row, DRAFTED, review card | product |
| `src/agents/expense_bookkeeper/templates/expense_review_card_to_owner.txt` | new review-only card (no approve/reject affordance) | product |
| `tests/test_expense_receipt_draft_ingestion.py` | new (named `test_expense*` so ownership resolves to expense-bookkeeper) | test |
| `tests/test_expense_bookkeeper_state.py` | state-enumeration invariants updated for the added status | test |
| `docs/governance/projects/expense-bookkeeper.md` | v1.1.0: DRAFT authority tier, DRAFT vertical E2E, mock-QBO boundary, not-Hermes-native accuracy note | governance |

Config (operator-owned, not a repo change): add `expense_bookkeeper.enabled: true`
to the live `config.yaml`. **Not** self-applied.

**Not touched:** `apply-expense-decision`, `qbo_client.py`, all
three SKILLs, `approval_code_pools.py`, `safe_io.py`,
`shift-agent-policy`, the multi-location workflow, Hermes, model, Tool Search, MCP,
generic `skills`, generic `terminal`.

## 10. Production LOC estimate

**~100 effective production LOC** (predicate ~20, ingestion arm ~45, verified
re-read ~15, card egress ~20) plus one template. Preferred ≤100, stop at 150. Test
LOC ~250. SKILL prose: zero.

## 11. Real-data E2E definition

Owner sends a **real receipt photo** with a receipt caption from the owner number.
Required chain: inbound → cf-router receipt arm claims it → `dispatcher_routed`
audit written before invoking → `extract-receipt` exit 0 → durable lead re-read and
confirmed → deterministic review card at actual `ScreenedWhatsAppAdapter` egress,
matching the committed template.

Confirm: no fabricated vendor/total/category; no QBO call; no
`expense_pushed_confirmation` text anywhere; extraction confidence surfaced
honestly; `skills` and generic `terminal` still disabled; gateway healthy.

**No fabricated receipts.** A real photo of a real purchase, or the status stays
`DEPLOYED_AWAITING_LIVE_E2E`. Ruling on pass: `ACTIVE_RECEIPT_REVIEW`, explicitly
**not** any status implying bookkeeping delivery.

## 12. Rollback

Revert the PR: cf-router loses one arm and owner receipt photos return to
unclaimed. Set `expense_bookkeeper.enabled: false` to disable without a deploy.
`extract-receipt` and the QBO client are untouched, so there is no half-migrated
state and no durable rows of a new type.

## 13. Blockers (3)

**BQ-1 — RULED.** Review-only, but as a genuine `DRAFTED` state rather than a
cosmetically edited approval card. Implemented as `--review-only` in the existing
extractor. Closed.

**BQ-2 — RULED.** Owner + media + explicit receipt/expense caption. Image-only
owner intake deferred. Closed.

**BQ-3 — RULED.** Authorized after merge + deploy, using the minimum explicit
section (`enabled: true`, `qbo_client_mode: mock`), and only after inspecting the
existing lead store. If any non-terminal approval-bearing leads exist, STOP and
report rather than migrating or expiring them.

## 14. Explicitly rejected alternatives

1. **Rebuild receipt parsing / OCR / classification** — all deployed inside `extract-receipt`.
2. **A new expense tool in `shift-agent-read`** — that plugin is read-only, and a Hermes tool handler never receives the inbound event, so it **cannot obtain the media path**; `_extract_media_path` works only inside cf-router's `pre_gateway_dispatch` hook. This is why Workflow 1's tool shape does not transfer.
3. **Re-arm the `skills` toolset to revive the dispatcher SKILL** — forbidden wave-wide, and would re-expose every generic skill to fix one path.
4. **Wire the approval reply with `MockQBOClient`** — would render "pushed to QuickBooks" with a fabricated transaction id.
5. **Implement `RealQBOClient`** — no credentials, no authorization, out of scope for a reachability workflow.
6. **A shared "owner media ingestion" primitive** — two consumers exist (menu, receipt), but extracting one now would refactor a live, money-adjacent path for no behavioural gain. Follow-up.
7. **An intent classifier for bare owner photos** — forbidden; the caption trigger is the deployed, deterministic alternative.
