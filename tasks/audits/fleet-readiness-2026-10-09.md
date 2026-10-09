# Fleet readiness pass — 2026-10-09

**Drift-check tag:** `extends-Hermes` — this record documents the pass; the one runtime change it
covers (WP-B) is a thin arm on the existing cf-router. Plan: `tasks/fleet-readiness-20261009-plan.md`.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Readiness audit record | none — repo convention `tasks/audits/` | documentation (no code) |

Verdict: documentation plus the WP-B adapter described in the plan.

**Supersedes** the per-agent statuses in `agent-inventory-authoritative-2026-09-02.md` for
`catering_followup` only; every other status is re-confirmed below with fresh evidence.

---

## 0. Current truth (main-vps, read-only, 2026-10-09 ~01:00–01:30Z)

| | |
|---|---|
| `main` | `39c40300` (#799) |
| box (`.commit-hash` + `DEPLOY_RECEIPT.json`) | `50a1daa0`, installed 2026-10-08T15:53:03Z (auto-rollback after the 8411b8c3 deploy failed closed at `vision-auth-smoke`) |
| staged release | `/tmp/shift-agent-deploy-39c40300.tgz`, sha256 `dfc2b90c…a9e9`, byte/mode-equal to Git |
| deploy gate | **blocked**: OpenRouter key `sk-or-v1-9e3…a84` `limit 30 / limit_remaining 0` → every model call AND the deploy's vision gate return HTTP 403 |
| failed units | 0 |
| gateway / cockpit / bridge | active / active / `{"status":"connected"}` on `127.0.0.1:3000` (bridge runs inside `hermes-gateway.service`) |
| Hermes | 0.19.1; `disabled_toolsets`: delegation, skills, browser, clarify, terminal, code_execution; `platform_toolsets.whatsapp`: hermes-whatsapp, shift_agent_read; plugins: cf-router, shift-agent-policy, shift-agent-read; hooks registered by us: `pre_gateway_dispatch` only (gateway exposes pre_llm_call, pre/post_api_request, api_request_error, post_tool_call, on_session_start/end, subagent_stop); no MCP servers configured |
| inbound denominator (30 d) | **12 `cf_router_raw_body` rows, all 2026-10-03 00:27–02:39Z**, all test-customer traffic (STOP/resume ×2, flyer intake/bypass/create/fail) |
| `dispatcher_routed` (30 d) | **0** — explained: only the shift F8/F9, owner-command, update_catering_menu and parse_receipt_photo arms emit it (`hooks.py:463,753,5677,5891,7871`); flyer/catering F7 emit `cf_router_intercepted` (14 rows). Not an emit failure. |
| owner-only arm fired in window | none → "owner inbound reaches cf-router" rests on the 2026-08-23 owner-approval evidence; re-proven by the WP-B arming witness |

## 1. The matrix — 18 agents, one row each

| agent | 09-02 | 2026-10-09 | evidence | gate to next status |
|---|---|---|---|---|
| daily_brief | DEPLOYED_AWAITING_ORGANIC_E2E | same | 30 `brief_attempted`, 27 `brief_sent`, 3 `brief_send_failed` in 30 d; `last-brief-sent.json` 2026-10-08 11:14Z; 0 journal errors 48 h | a paying customer as recipient; recipient today is the operator (`owner.name: Srini`) |
| eod_reconcile | DEPLOYED_AWAITING_ORGANIC_E2E | same | 30 `eod_snapshot`, 207 `eod_skipped` (15-min self-gate) | same |
| shift | DEPLOYED_AWAITING_ORGANIC_E2E | same | 0 employee inbound in 30 d; `pending.json` untouched since May 31 | a genuine sick-call; LID learning (§3) |
| equipment_maintenance | DEPLOYED_AWAITING_ORGANIC_E2E | same | read tool registered + reachable (check D 09-02); **no `equipment-items.json`** | operator seeds via `add-equipment-item.py`, then one real owner query |
| compliance | BLOCKED_ON_REAL_DATA/CONFIG | same | `compliance:` block absent from `config.yaml` → disabled; no items file; timer fires daily and self-gates | operator adds `compliance.enabled: true` + REAL dates via `add-compliance-item.py`; write half now reachable when armed (WP-B) |
| multi_location | DEPLOYED_AWAITING_APPLICABLE_DATA | same | `customer.location_id: loc_pineville_01`, no `multi_location.locations` | a real second location |
| catering | PARTIAL | PARTIAL | pricebook v2 (PILOT rates) since 09-03; menu v3 77 items, 0 `serves`; `deposit_pct: 0`; #796–#799 merged, NOT deployed | see `tasks/catering-flyer-readiness-20261008.md` §Session 2 — key cap → deploy → allowlists → genuine journeys |
| flyer | PARTIAL | PARTIAL | F0226/F0228 `manual_edit_required` still paging hourly (858 `flyer_source_edit_sla_alert` rows / 30 d); same deploy gate | same; F0226/F0228 disposition is the operator's |
| expense_bookkeeper | PARTIAL | PARTIAL | `enabled: true, qbo_client_mode: mock`; state dir holds only an empty `receipts/` — zero receipts ever | §4 (WP-D) |
| catering_followup | NOT_IMPLEMENTED | **IMPLEMENTED_DORMANT** (correction) | M5 engine on main and on the box: `catering_followups.py`, `catering-followup-sweep` + timer (installed, `disabled` by design), `create-/approve-catering-followup`; `post_event_feedback` admits `CLOSED` leads (`catering_followups.py:189-194`); env has no `CATERING_FOLLOWUP_*` keys | operator arms the three gates on purpose (unit comment) |
| cash_ar, employee_docs, hiring, inventory, pnl_anomaly, sales_tax, supplier, vip | NOT_REACHABLE | same | each dir = `SKILL.md` + `__init__.py`; no scripts, no store, no data | §5 (WP-E) — promotion packages, not builds |

`0 + 4 + 1 + 1 + 3 + 1 + 8 = 18`. **PRODUCTION_READY = 0**, unchanged. No agent has processed a
genuine paying-customer event end to end; the 30-day inbound denominator is 12 messages, all from
the authorized test customer.

## 2. WP-B — compliance "mark `<id>` done" reachability (dormant)

Decision memo (deep-reasoner, 2026-10-09): **shape A**, a deterministic owner-token arm on the
existing cf-router; shape B (`shift-agent-act` plugin tool) rejected because the mutation trigger
would be model tool-choice (universal directive §4), the read preflight would page every boot for
a deliberately dormant toolset, and model retries escape the inbound dedupe. Full spec in the plan.

**Kernel HIGH found and fixed in the same change:** `mark-compliance-item-done.py:126-131`
advanced a recurring item on every call, so a second owner message within minutes skipped a whole
cycle (and its reminders) silently. The kernel now refuses (rc 3) when the decisions.log tail holds
a `compliance_item_marked_done` row for the same item within 900 s.

Arming (operator, after a deploy that carries #798 — the owner-exempt send — and this change in
one tarball): enable `compliance.enabled: true` AND `COMPLIANCE_MARK_DONE_ENABLED=1` (in the `.env`
**target** `/root/.hermes/.env`, never `sed -i` the symlink) **in the same window** — the 06:00
reminder template already tells the owner "Reply *mark X done*", so enabling reminders without the
arm hands the owner a token only the LLM can answer → `systemctl restart hermes-gateway` →
**seeded witness** (TARGET+WITNESS, not just the not-found path): `add-compliance-item.py` a
one-shot probe item (recurrence 0, a date within the window), owner sends `mark <probe_id> done`,
expect the OK-ONESHOT reply, the item gone from `compliance-items.json`, and in decisions.log a
`dispatcher_routed(routed_to_skill=mark_compliance_item_done)` row followed by
`compliance_item_marked_done(item_id=<probe_id>)`. Disarm = remove the env line + restart.
Graduation trigger: after the seeded witness plus one real owner mark, drop the env flag and gate
on `cfg.compliance.enabled` alone (single-tenant VPS — no per-number allowlist to fossilize).
Pre-arming follow-up (review F3): the 900 s guard catches rapid double-sends only; a repeat mark
days later still advances another cycle. A cycle-keyed refusal (latest `compliance_item_marked_done`
row's `next_renewal_date == item.renewal_date` ⇒ "this cycle is already closed") needs a product
ruling on legitimate early renewals before it ships. Residual: `is_owner_chat` admits
`authorized_identities` aliases whose replies #798 does not exempt (list is empty today).

Implementation (commits `b32ea85d` + `a284b840` on `launch/fleet-readiness-20261009`):
`hooks.py` arm `_try_compliance_mark_done` (+ env `COMPLIANCE_MARK_DONE_ENABLED`, regex, pronoun
stopwords `it/as/this/that/them/all/one` that fall through before the route row), `actions.py`
`invoke_mark_compliance_item_done` (exact argv, 124/127 mapping), kernel guard
`_recent_mark_done_ts` (rc 3; 256 KiB tail; row now written inside the items lock), read-tool
DESCRIPTION sentence + flag-gated `to_mark_done` hint. Tests: `tests/test_cf_router_mark_done_arm.py`
(16, real kernel subprocess, MUTANT phase inside the vertical test), kernel double-mark /
outside-window / row-inside-lock tests, read-tool hint test. Linux docker: 472 passed, 0 skipped
(all fcntl-gated, so Windows proves nothing). **Mutants run by the lead in a Linux container:**
guard window → 0 ⇒ double-mark test FAILS; `verified=True` forced ⇒ 6 context tests FAIL;
`dispatcher_routed` skill name changed ⇒ vertical WITNESS test FAILS. Governance checker: OK.
Reviews (two independent, orthogonal lenses — structural/code and scope/prod-state): no
BLOCKER; GO-with-changes. HIGH H1: rc 1 was mapped to "not found" from the exit code alone, so a
kernel crash (also exit 1) would have told the owner the id was wrong with no audit row → mapping
now keyed on the kernel's JSON `error`, everything else UNCERTAIN + audit. MEDIUM F1: the pronoun
stopword list was a denylist in the making ("mark order done" would be captured) → replaced by a
structural known-id gate (unknown id, disabled or unreadable calendar → fall through to Hermes
before any route row; also closes M1, a claimed path with no audit). MEDIUM F5: per-agent flag-ON
passthrough tests added. M2 test gaps (one-shot double mark, crash-as-rc-1, future-dated row, >256
KiB tail) added. LOW: reply copy without "changed" (forbidden completion verb), stderr on
non-"sent" bridge status, stale comment, `re.A`, directive v1.1.0. Fixes landed in the follow-up
commit `670f4d5a` (Linux docker: 487 passed, 0 skipped across the 10 touched/neighbouring files).
Second mutant pass on 670f4d5a (lead, Linux): known-id gate disabled ⇒ 7 tests fail; rc 1 read
without the JSON key ⇒ 2 fail; `0 <= age` dropped ⇒ future-row test fails; head-read instead of
tail ⇒ large-log test fails. Expense counterpart: commits `c550b9c4` + `9580a0f7` on
`fix/expense-mock-guard-truthful-copy-20261009`, 544 passed, 12/12 mutants killed. Recorded, not changed: cycle-keyed guard (F3), `authorized_identities` alias
replies not exempted by #798, rotation/append-failure edges of the tail guard, inherited real-clock
date assertions in the compliance script tests.

## 3. WP-F — how the platform should learn phone↔LID pairings (operator decision)

Finding that reframes the 2026-09-02 "doubly dead pipeline": **Baileys 7.0.0-rc13 already
persists the pairings.** The live bridge (`/root/.hermes/scripts/whatsapp-bridge/bridge.js`,
`--session /root/.hermes/whatsapp/session`) has **180 `lid-mapping-<phone>.json` files (+90
`_reverse`)**; the owner's file contains `"201975216009469"` — exactly `owner.lid` in
`config.yaml` — and the test customer's file exists. `buildLidMap()` (`bridge.js:283-297`) reads
them into `lidToPhone`, rebuilt on `creds.update` (`:450`), **but nothing reads `lidToPhone`**:
`_shiftResolveSender` (`:217-234`) emits only `senderLid` for `@lid` senders. The retired 2026-08-01
backfill failed on exactly the key shape confirmed tonight (bare digits in the file vs `<lid>@lid`
lookups).

Options (memo by deep-reasoner; preconditions verified on the box tonight):

| option | change | LOC | layer | verdict |
|---|---|---|---|---|
| O1 | on the LID branch of `_shiftResolveSender`, resolve via Baileys (`sock.signalRepository.lidMapping.getPNForLID` — present in rc13 — or `msg.key.participantAlt`/`remoteJidAlt`, present) and emit `senderPhone` beside `senderLid`; gateway already prefers `event.senderPhone` for LID senders (`patch1_port_v0191.py:59-82`, `sender_context.py:38-80`) | 10–15 JS + patch-port edit + node test | Hermes-core patch (`HERMES_PIN_OVERRIDE`, deploy gate, bridge restart) | **recommended** — fixes it at the source, writes no state, fails closed (miss → today's behavior) |
| O2 | shift-agent job reads session files → existing `lid-learn` kernel → roster/`lid-cache.json` | 40–60 | ours | follow-up only, after O1: root-owned session dir AND root-owned `roster.json` block it; `lid-learn` replaces an existing LID with no §12b alert (`lid-learn:131-143`); arguably a new importer (needs an exception) |
| O3 | `identify-sender` reads session files at resolve time | — | ours | reject — couples the trust root to Baileys' on-disk format, same permission problem |
| O4 | status quo | 0 | — | acceptable interim: zero employee inbound in 30 d; a LID-only employee resolves `unknown` and lands on the customer path (wrong routing that fails safe) |

Trust: the mapping is server addressing metadata persisted from the authenticated session — the
same trust root as `remoteJid`; no message content is involved. Residual: a recycled number's
stale `_reverse` entry. Scope limit: O1 helps every consumer of the sender block; callers that
pass `chat_id` (e.g. `catering-owner-action-watchdog:312`) still need `roster.lid`/`owner.lid`.
Ruling needed: O1 widens owner resolution from LID-only to phone — intended, but a privilege-path
change that needs a recorded approval. Verification only with +17329837841 / +19802005023 / bot
+918522041562.

## 4. WP-D — Expense Bookkeeper (DRAFT tier live-adjacent; SUPERVISED blocked on code AND credentials)

Audit (read-only, origin/main 39c40300; box 50a1daa0):

- **DRAFT arm is complete on main — nothing raises or stubs.** Owner photo + explicit receipt
  caption (`actions.py:3751-3775`; a bare "receipt for costco" does NOT match) + owner membership
  + no flyer-edit veto → `_run_owner_receipt_ingestion` (`hooks.py:1073`, impl `:5908-6008`) →
  `extract-receipt --review-only` (always; `actions.py:3792-3797`) → `DRAFTED` lead, no code →
  store re-read → `expense_review_card_to_owner.txt`, whose last line is "Review only — this
  expense has not been posted to QuickBooks."
- **But the card is probably not owner-visible on the box today:** `50a1daa0` substitutes the
  front-brain ack for scripted owner sends; #798 (on main, not deployed) fixes that for primary
  owner identities only (`authorized_identities` aliases remain screened; the live list is empty).
- **The arm is nested under the FLYER workflow flag** (`hooks.py:870`): if flyer workflow is off,
  receipts are silently not routed. The only `expense_bookkeeper.enabled` check runs INSIDE the
  script (`extract-receipt:542`, rc 3) after cf-router has already claimed the turn.
- **Untruthful failure copy:** every non-zero rc sends "I could not read that receipt, so nothing
  was recorded" — including rc 3 (disabled), rc 6 (OpenRouter down — the key-cap 403 today) and
  rc 124 (timeout, where a DRAFTED lead may already exist).
- **No live directive violation.** Latent NO-GO: `apply-expense-decision` `_push_to_qbo`
  (`:430-533`) and `_handle_undo` (`:725-728`) never check `qbo_client_mode`; in mock mode they
  would tell the owner "pushed to QuickBooks … Tx ID MOCK-E0001-1" / "voided in QuickBooks". Unreachable
  today only because cf-router has no F8 expense branch and DRAFT mints no code — reachable the
  moment an operator runs the script by hand. Also: `pushed_at=None` counts as inside the undo
  window (`:655`); PUSH_FAILED copy promises an automatic retry that does not exist anywhere.
- **Tests:** the live-shape tests fake both `invoke_extract_receipt` and the send
  (`test_expense_receipt_draft_ingestion.py:523-535`); no test chains hook → real script → real
  template → send; failure branch, rc-9 silent skip, flyer-flag-off and timeout are untested.
- **SUPERVISED tier:** `RealQBOClient.__init__` raises AND `make_qbo_client` refuses any mode but
  `mock` (`qbo_client.py:297-302`, `:333-338`); credentials env names exist only in
  `credential_readiness.py:153-157` and nothing consumes them; no QBO-side idempotency key (a crash
  after the write is flagged by the orphan scan, not deduplicated). Repo decision of record
  (`tasks/expense-qbo-mcp-rescope-plan.md`, #585): thin adapter over the official Intuit QBO MCP
  server via `mcp/native-mcp` — **unresolved tension:** `native-mcp` exposes tools to the LLM,
  while the push must stay a deterministic subprocess, so the adapter needs its own Python MCP
  client; settle this before estimating. **No QBO sandbox onboarding runbook exists.**

**Reproduced owner-visible defect found while fixing:** since `d9e64836` (#690, 2026-08-09) the
receipt-failure send passed `mutation_class="none"` — not in the `ActionExecutionContext` Literal —
so `build_action_context` raised inside the arm's error boundary and **the owner received no reply
at all on any extraction failure** (the audit said "failed" while the chat stayed silent).

Tonight's fixes (separate PR `fix/expense-mock-guard-truthful-copy-20261009`, commit `c550b9c4` +
review follow-up): the invalid context fixed; failure copy keyed on the kernel's exit code and
stderr (disabled = rc 2 + marker; provider down = rc 6; timeout = rc 124 with a store re-read that
names the saved `expense_id` or says nothing was recorded; everything else a neutral system-error
copy — never "could not read" for a non-photo failure); a disabled agent CLAIMS the explicit owner
receipt request and refuses truthfully without a subprocess (the candidate predicate is untouched so
the flyer brand-asset arm keeps yielding — the B0009 class stays closed); `apply-expense-decision`
refuses every verb in `qbo_client_mode: mock` (exit 20; tests opt in via env); `pushed_at=None` is
outside the undo window with honest wording. Independent money/truthfulness review: GO-with-changes
(HIGH: rc 3 ≠ disabled — rc 2 is; "ask me for your pending drafts" was a false affordance) — all
HIGH/MEDIUM fixed; six mutants killed in a Linux container (invalid `mutation_class` restored,
guard removed, window revert, disabled gate removed, failure verified=True, message-id match dropped). Not fixed tonight (operator-gated or needs design): the F8 expense
branch + `DRAFTED→AWAITING` edge (supervised tier), the real client, the MCP-client decision,
credentials/sandbox, chart-of-accounts mapping, the orphan-image prune gap, PUSH_FAILED copy.

Operator gates for expense: deploy #798+; one real captioned owner receipt (DRAFT E2E proof per
the directive); Intuit developer app + QBO sandbox (client id/secret, realm id, OAuth consent →
refresh token); pin + host the MCP server; chart-of-accounts mapping; sandbox soak; then
`qbo_client_mode: real` and the smoke-test assertion update.

## 5. WP-E — Phase-0 scaffolds (8) — Hermes-first promotion assessment

Full report: `scratchpad/phase0_report.md` (339 lines; read-only, origin/main 39c40300, in-repo
Hermes evidence only — `tasks/skills-roadmap.md`, shared-platform-directive §1a,
`credential_readiness.py`, `gateway-toolset-scoping.md`). **Bottom line: none of the eight should
get new code now.** Building them would be architecture-only output against the family directive
(promotion + E2E proof required; agent-local stores presumed NO-GO) and MORE custom code.

Facts that apply to all eight: each is `SKILL.md` + empty `__init__.py` with a disabled-by-default
config class (`schemas.py:3563-3810`) and the `agent_declined` contract; the bundled Hermes
productivity skills the roadmap credits are SKILL-based and therefore not a usable carrier on a box
that disables `skills`/`terminal`/`code_execution`; whether `mcp/native-mcp` toolsets survive
`disabled_toolsets` is NOT_DETERMINED and every vendor MCP still needs credentials + a
`config.yaml` edit; the `shift-agent-read` pattern fits only slow-changing, operator-seeded data
(no owner write path from WhatsApp exists); any new tool or store is shift-platform scope.

| rank | agent | exercisable today? | smallest slice / verdict |
|---|---|---|---|
| 1 | employee_docs | **yes, zero code** | seed food-handler certs / driver licences as `certification` compliance items (`ComplianceItem.category`, `schemas.py:3657-3660`) via `add-compliance-item.py`; **exclude work-authorization/I-9 dates** (separate privacy decision); promotes nothing |
| 2 | sales_tax | **yes, zero code** | seed the pilot's state filing dates as `tax_filing` compliance items; everything else needs POS sales data (none in tree) |
| 3 | cash_ar | data exists but would mislead | leads carry `quote_total_usd`, `deposit_status/amount_cents`, `BOOKED`; **no payment-received fact exists**, so a balances tool would show every booked event as 100 % unpaid — the wrong-reminder risk the spec names. Box tonight: 20 leads, **0 `BOOKED`** (8 OWNER_REJECTED, 4 CLOSED, 3 SENT_TO_CUSTOMER, 3 AWAITING_OWNER_APPROVAL, 2 CUSTOMER_FINALIZED), `deposit_status` none ×20 — nothing to read even if built. Needs a catering-studio money-path decision first |
| 4 | supplier | unknown → **no** (box has zero expense receipts) | a "vendors + last prices from approved receipts" read is the only slice; no receipts exist, so blocked |
| 5 | hiring | cheap step already covered | roster create/patch/import exist in the cockpit roster router; the rest (paperwork, e-sign, training) has no store; assessment only |
| 6–8 | vip, pnl_anomaly, inventory | **no** | blocked on POS / loyalty / stock data; `min_orders_for_vip=10` vs ~5 leads; single location makes per-location comparison meaningless; stock goes stale without an owner write path |

Governance drift found (recorded, not changed tonight — normative edits need their own PR):
(1) `phase0-agents.md:29-30` ("no store, no script") and the registry still list
`equipment_maintenance` as a scaffold although `add-equipment-item.py` + `equipment_tool.py` exist
and the 09-02 audit rates it deployed — promote it out of the family or correct the directive;
(2) registry `phase0-agents.tests: []` while `tests/test_tier2_schemas.py` and
`tests/test_agent_22_pnl_anomaly_schemas.py` exist (absorbed by `tests/**`); (3) `pnl_anomaly`'s
SKILL logs `pnl_anomaly_declined` where family rule 1 requires `agent_declined` — ruling needed.

## 6. Platform findings carried (no code tonight)

- **`dispatcher_routed` emit failure is stderr-only** (`actions.py:2347`) while the
  routing-accuracy metric reads 0 with no watchdog — §12b backlog; not reproduced, not fixed.
- **`/opt/shift-agent/roster.json` root-owned (May 26), mode 644**: every reader works; any
  `shift-agent`-uid writer (`lid-learn`, `fsck` repairs) would fail. R4 class; operator go needed.
- **`WHATSAPP_LID_CACHE_WRITE` env is vestigial** (no code reads it) — misleading; remove on the
  next `.env` edit.
- **43 stale binaries in `/usr/local/bin`** (`*.bak-*`, `*.pre-*`, `codex-flyer-autodev-main*`,
  `create-flyer-project.pre-*`): quarantine (never delete) after provenance check — operator go.
- **`ufw` inactive** — hold #4 from 2026-09-02 still open.
- Hermes utilization: 1 of 8 gateway hook points used; `skills`/`terminal` disabled by design
  (`docs/runbooks/gateway-toolset-scoping.md`); 0 MCP servers. Expanding hooks (turn
  observability) remains on operator HOLD.

## 7. Operator checklist (everything a human must do; nothing here was faked or skipped)

1. OpenRouter: raise the per-key limit on `sk-or-v1-9e3…a84`; separately top up credits.
   Unblocks every deploy and every model call.
2. Then: deploy `39c40300` (+ this PR once merged → new tarball) via the staged script; verify
   receipt; then the catering/flyer env allowlists (held script) and the handset journeys from
   `tasks/catering-flyer-readiness-20261008.md` §7.
3. Compliance: add `compliance: {enabled: true}` to `config.yaml`, seed REAL dates with
   `add-compliance-item.py`, then (after deploy) `COMPLIANCE_MARK_DONE_ENABLED=1` and the witness
   probe in §2.
4. Equipment: seed the real equipment list with `add-equipment-item.py`, then one real owner query.
5. Catering follow-ups: decide whether to arm the three M5 gates (unit comment lists the steps).
6. LID learning: approve or defer O1 (Hermes-core patch cycle) and record the owner-resolution
   ruling (§3).
7. `roster.json` ownership → `shift-agent:shift-agent` (with backup) — go/no-go.
8. Quarantine the 43 stale `/usr/local/bin` binaries — go/no-go (list on request).
9. `ufw` — enable with the cockpit/ssh/bridge rules, or record the standing hold.
10. F0226/F0228 disposition and the catering package/serving facts — unchanged from the
    2026-10-08 report.
