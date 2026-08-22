# Agent reachability matrix — 2026-08-22

**Drift-check tag:** `Hermes-native` — this is an audit of the deployed runtime; it proposes no
new infrastructure. The read-shaped ranking in §5 targets the EXISTING `shift-agent-read`
plugin and Hermes's own progressive-disclosure tool discovery.

**Hermes-first analysis** — no capability is being built here. The audit's core finding runs the
other way: several agents are unreachable precisely because they were built as SKILL.md files,
and the `skills` toolset is disabled on the deployed box, so Hermes's skill substrate is NOT
available to them. Any remediation must use a Hermes primitive that is actually armed — a
registered tool (`shift_agent_read` toolset), a `pre_gateway_dispatch` hook, or a systemd unit.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| tool registration / progressive disclosure | yes — Hermes `ctx.register_tool` + tool_search/tool_describe/tool_call | use it (this is what `shift-agent-read` already does) |
| inbound interception | yes — Hermes `pre_gateway_dispatch` plugin hook | use it (this is what `cf-router` already does) |
| SKILL.md dispatch | exists in Hermes, **DISABLED on this box** | cannot use — see §1 |

awesome-hermes-agent ecosystem check: not applicable — no new capability is proposed.

---

## 1. The governing runtime fact

Verified deployed state on `main-vps` (app root `/opt/shift-agent`, Hermes home `/root/.hermes`,
deployed commit `40064b1a`):

- `agent.disabled_toolsets: [delegation, skills, browser, clarify, terminal, code_execution, file]`
- `platform_toolsets.whatsapp: [hermes-whatsapp, shift_agent_read]`
- `plugins.enabled: [cf-router, shift-agent-policy, shift-agent-read]`
- 45 SKILL directories exist under `/root/.hermes/skills/`.

**Both the `skills` toolset and the `terminal` toolset are disabled.** Every routing row in
`src/agents/shift/skills/dispatch_shift_agent/SKILL.md` instructs the model to execute
`/usr/local/bin/...` via the `terminal` tool after `skill_view`. Neither tool is in the model's
loadout. Therefore:

> **A dispatcher SKILL row is not an execution path. A SKILL.md file on disk is not an
> execution path. An installed `/usr/local/bin` script is not an execution path unless a
> cf-router hook or a systemd unit calls it.**

Only three routes execute anything today:

- **(a) cf-router** — ONE registered hook, `pre_gateway_dispatch`
  (`src/plugins/cf-router/__init__.py:37`), whose body
  (`src/plugins/cf-router/hooks.py:369-1003`) invokes handler scripts itself as subprocesses.
- **(b) a registered tool** — `shift-agent-read` registers exactly three
  (`src/plugins/shift-agent-read/__init__.py:28-35`). `shift-agent-policy` registers no tools;
  it is an egress screen plus a sender-context hook (`src/plugins/shift-agent-policy/plugin.yaml`).
- **(c) a systemd timer** — 38 timers on the box; the agent-bearing ones are named per row below.

---

## 2. Canonical agent list and reconciliation

Four sources disagree. Reconciliation:

| Source | Says | Reconciliation |
|---|---|---|
| `docs/portfolio.md:864-878` | Solid 17 = #1-7, 9-16, 21, 22; #17/#18/#20 retired; #8/#19/#23-25 backlog | Numbering authority. Adopted. |
| `docs/portfolio.md:1116-1124` (v3, 2026-05-04) | "5 LIVE, 12 SCAFFOLDED" | **STALE and over-claiming.** Contradicted by every runtime probe below. Retained only as history. |
| `src/agents/*` | 18 directories | `flyer` has NO portfolio number (post-dates the doc) — listed as "Flyer Studio". No directory exists for #8 or #11. |
| `/root/.hermes/skills/` (45 dirs) | includes 13 generic Hermes bundles (`apple`, `autonomous-ai-agents`, `creative`, `email`, `github`, `media`, `mlops`, `note-taking`, `productivity`, `research`, `smart-home`, `social-media`, `software-development`) | Not project agents. Excluded. All project SKILL dirs are dead per §1. |

Canonical list = **20 agents**: #1-7, #9-16, #19, #21, #22, Flyer Studio.
(#8 Receiving & QA and #11 Festival & Peak Prep have no code but are real portfolio slots and
are counted; #17/#18/#20 are retired and not addressable.)

---

## 3. The matrix

| # | Agent | Status | Execution path (evidence) | Smallest change to reach | Read-shaped? |
|---|---|---|---|---|---|
| 1 | Shift Agent | **PARTIAL** | **(a)** sick-call: `hooks.py:466-513` → `actions.invoke_shift_sick_call` → `/usr/local/bin/handle-shift-sick-call`. **(c)** `shift-agent-proposal-sweep.timer` (15 min), `shift-agent-health`, `-backup`, `-fsck`, `-tail-logger`, `send-routing-accuracy-summary.timer` (weekly). **DEAD:** owner approval of a coverage proposal — `hooks.py:1349-1351` explicitly falls a `POOL_SHIFT` code through to the LLM/dispatcher, which cannot run. `handle_candidate_response` (employee reply) is SKILL-only. | (ii) add a `POOL_SHIFT` branch to `_try_f8_intercept` calling `update-proposal-status`, mirroring `hooks.py:1296-1319` | partly — §5.4 |
| 2 | Catering Lead Agent | **DEPLOYED_AWAITING_LIVE_E2E** | **(a)** F7 primary intercept `hooks.py:920-947`; acceptance arm `hooks.py:900-906`; F8 owner approve/reject `hooks.py:1296-1319`; menu ingestion `hooks.py:702-713` → `parse-menu-photo`. **(c)** `catering-lead-ttl-sweep`, `catering-proposal-expiry-sweep`, `catering-pattern-report` timers; `catering-owner-action-watchdog.service` **active running**. `cfg.catering.enabled=true`. 20 real leads on disk. | — reachable; blocker is live E2E, §4.2 | **YES — top candidate, §5.1** |
| 3 | Multi-Location Coordinator | **PARTIAL** | **(b)** `find_nearest_location` registered (`__init__.py:28-35`, `location_tool.py:217`), public, wraps installed `closest-location.py`. **But** `/opt/shift-agent/config.yaml` has NO `multi_location` block → `location_tool.py:251` returns `status="not_configured"` on every call. | operator data entry: add `multi_location.locations[]` to config. Zero code. | already built |
| 4 | Daily Brief Agent | **PRODUCTION_READY** | **(c)** `send-daily-brief.timer` (15 min; ran 18:08:14 today) → `/usr/local/bin/send-daily-brief`. Proven live: `state/last-brief-sent.json` = `{"brief_date":"2026-08-22","outbound_message_id":"3EB08A340E5FEFFF3AB4DD"}`; audit tail shows `brief_attempted` + `brief_sent`, then 43 `brief_skipped` each carrying an explicit `reason` (idempotency + truthful non-send). | — | n/a |
| 5 | EOD Reconciliation | **PRODUCTION_READY** | **(c)** `eod-reconcile.timer` (15 min) → `/usr/local/bin/eod-reconcile`. Proven: `state/eod-snapshot.json` for `2026-08-21` with `snapshot_id`, counters, `invariant_violations: 0`; audit shows `eod_snapshot` + 7 truthful `eod_skipped`. Feeds #4. | — | n/a |
| 6 | Inventory Tracker | **NOT_REACHABLE** | SKILL only (`src/agents/inventory/skills/inventory_dispatcher`). No script in `/usr/local/bin`, no tool, no timer. | (i) thin READ tool — **but no inventory data file exists on disk**, so today (v) not worth doing | no (no data) |
| 7 | Supplier Coordination | **NOT_REACHABLE** | SKILL only. No script, tool, or timer. | (v) not worth doing — no supplier roster on disk | no (no data) |
| 8 | Receiving & QA | **NOT_IMPLEMENTED** | No `src/agents/` directory. Paper spec at `docs/portfolio.md:287`. | (v) backlog per portfolio | no |
| 9 | VIP Customer Agent | **NOT_REACHABLE** | SKILL only (`vip_dispatcher`). `record-customer-birthday` is installed but belongs to #4's script set and has no caller. | (i) thin READ tool over birthdays **if** a birthday store existed — none found under `/opt/shift-agent/state/` | no (no data) |
| 10 | Catering Follow-up | **NOT_REACHABLE** | Scripts INSTALLED (`create-catering-followup`, `catering-followup-sweep`, `approve-catering-followup`). F8 already handles the `POOL_CATERING_FOLLOWUPS` code (`hooks.py:1321-1346`). **But `catering-followup-sweep.timer` is `disabled` on the box** (`systemctl list-unit-files`), so no follow-up card is ever created and the F8 branch is unreachable in practice. | **(iii) `systemctl enable --now catering-followup-sweep.timer`** — cheapest reachability win in the portfolio, but see §4.5 | no — write/send agent |
| 11 | Festival & Peak Prep | **NOT_IMPLEMENTED** | No `src/agents/` directory. Tier-promoted 2026-04-29, never scaffolded. | (v) | no |
| 12 | Hiring & Onboarding | **NOT_REACHABLE** | SKILL only. | (v) — no candidate store on disk | no (no data) |
| 13 | Compliance Calendar | **PARTIAL** | **(b)** `get_compliance_deadlines` registered, owner-only (`compliance_tool.py`, `identity.require_owner`). **(c)** `check-compliance-deadlines.timer` daily 06:00; ran today. **But** `/opt/shift-agent/state/compliance-items.json` **does not exist** and config has no `compliance` block → tool returns `disabled`/`missing`; `state/compliance-last-cron-tick.json` records `items_scanned: 0, reminders_sent_today: 0`. Writer `add-compliance-item.py` is installed and has never been used. | operator data entry via the installed `add-compliance-item.py` + a `compliance.enabled` config block. Zero code. | already built |
| 14 | Employee Document Tracker | **NOT_REACHABLE** | SKILL only. | (v) — no document store on disk | no (no data) |
| 15 | Cash & AR | **NOT_REACHABLE** | SKILL only. | (v) — no AR ledger on disk | no (no data) |
| 16 | Sales Tax Filing | **NOT_REACHABLE** | SKILL only. | (iv) blocked — state filing portals; no Hermes/MCP coverage per `CLAUDE.md` net-new list | no |
| 19 | Equipment & Maintenance | **PARTIAL** | **(b)** `get_equipment_maintenance_due` registered, owner-only (`equipment_tool.py:212-234`). **But** `/opt/shift-agent/state/equipment-items.json` **does not exist** and config has no `equipment_maintenance` block → returns `disabled`/`missing`. Writer `add-equipment-item.py` installed, never used. | operator data entry. Zero code. | already built |
| 21 | Expense Bookkeeper | **BLOCKED_UNSUPPORTED_INTEGRATION** | **(a)** owner receipt cession `hooks.py:714-719` → `_run_owner_receipt_ingestion` → `extract-receipt --review-only`, gated on owner MEMBERSHIP (`hooks.py:5444-5487`). **(c)** `prune-expense-receipts.timer` nightly; ran today. `cfg.expense_bookkeeper.enabled=true`, `qbo_client_mode='mock'`. **The vertical cannot complete:** `src/platform/qbo_client.py:293-312` — `RealQBOClient.__init__` raises `NotImplementedError`; the factory (`:337`) refuses `mode="real"`. DRAFT tier issues no approval code, so `POOL_EXPENSE` codes also dead-end at `hooks.py:1349-1351`. `state/expense-bookkeeper/leads.json` does not exist — zero receipts ever captured. | (iv) blocked on BOTH QBO sandbox credentials **and** an unwritten `RealQBOClient`. Credentials alone do not unblock it — §4.6. | no — write agent |
| 22 | P&L Anomaly Detective | **NOT_REACHABLE** | SKILL only (`pnl_anomaly_dispatcher`). No script, tool, or timer. | (iv) blocked on POS choice per `docs/portfolio.md:1122` | no (no data) |
| — | **Flyer Studio** | **DEPLOYED_AWAITING_LIVE_E2E** | **(a)** the largest surface in `hooks.py` — intake, brand-asset, quote-echo, campaign-CTA, source-edit, active-project and bare-flyer arms, `hooks.py:519-1001`. **(c)** `flyer-recovery-watchdog.timer` + `flyer-source-edit-sla-watchdog.timer` (5 min each). `cfg.flyer.enabled=true`. Live activity: `state/flyer/recovery_incidents.json` mtime 18:10 today. | — reachable; blocker is the SLA backlog, §4.4 | partly — §5.5 |

### Status counts

| Status | n | Agents |
|---|---|---|
| PRODUCTION_READY | 2 | #4, #5 |
| DEPLOYED_AWAITING_LIVE_E2E | 2 | #2, Flyer |
| PARTIAL | 4 | #1, #3, #13, #19 |
| BLOCKED_UNSUPPORTED_INTEGRATION | 1 | #21 |
| NOT_REACHABLE | 9 | #6, #7, #9, #10, #12, #14, #15, #16, #22 |
| NOT_IMPLEMENTED | 2 | #8, #11 |
| READY_TO_DEPLOY / READY_WITH_EXTERNAL_DEPENDENCY / BLOCKED_RUNTIME / BLOCKED_CREDENTIALS / INTENTIONALLY_REFUSAL_ONLY | 0 | — |

---

## 4. Non-obvious verdicts

### 4.1 All three shipped read tools are reachable and all three answer "not configured"

The single most surprising finding. `get_compliance_deadlines`, `get_equipment_maintenance_due`
and `find_nearest_location` are correctly registered under the surviving `shift_agent_read`
toolset and are genuinely discoverable. But:

- `/opt/shift-agent/state/compliance-items.json` — **absent**
- `/opt/shift-agent/state/equipment-items.json` — **absent**
- `/opt/shift-agent/config.yaml` has no `compliance`, no `equipment_maintenance`, and no
  `multi_location` block. Full key dump taken; the file holds only `schema_version, customer,
  owner, limits, alerting, backup, operations, catering, daily_brief, flyer, expense_bookkeeper`.

So agents #3, #13 and #19 are shipped, correct, reachable — and structurally incapable of
returning a non-empty answer on this box. The tools' own state discipline is what makes this
safe rather than dangerous: `compliance_tool.py:219-225` returns `source_status="missing"`,
`coverage_status="not_configured"` with **no counts and no items list**, precisely so a zero can
never be read as "nothing is due". They fail truthfully. They also deliver nothing.

**Consequence for the swarm:** a new read tool whose backing data does not exist just adds a
fourth member to this set. **Data-on-disk is the binding constraint on the §5 ranking, not tool
LOC.**

### 4.2 Catering is reachable and has been silently stuck for 74 days

`state/catering-leads.json` holds 20 leads. Three sit in `AWAITING_OWNER_APPROVAL`:

```
L0017  #4SX94  created 2026-06-09
L0018  #8D9YG  created 2026-07-21
L0019  #7GCQP  created 2026-07-23
```

The F8 intercept (`hooks.py:1296-1319`) would apply an owner `approve`/`reject` for these codes
correctly if the owner sent one. The owner never has. Nothing on the box tells the owner they
are pending: `catering-owner-action-watchdog.service` is active, but the recent audit tail
(400 rows) contains no catering owner-alert row — only `brief_skipped` (43),
`owner_alert_dispatched`/`owner_alert_delivered` pairs (20 each, flyer SLA), and
`flyer_source_edit_sla_alert` (18).

This is why catering is DEPLOYED_AWAITING_LIVE_E2E rather than PRODUCTION_READY: the approval
half of the vertical has never closed in production. It is also what makes §5.1 the top
read-shaped candidate — the owner currently has **no way to ask** "what is waiting on me?"

### 4.3 Shift's coverage loop cannot close — and an explicit code comment says why

`src/plugins/cf-router/hooks.py:1349-1351`:

```python
# Expense / shift codes are not F8's responsibility (owner self-chat handles
# only menu + catering here) — fall through so the LLM/dispatcher routes them.
return None
```

The comment's premise was true when written and is false now. There is no LLM/dispatcher to fall
through to: `dispatch_shift_agent` needs `skill_view` (skills toolset, disabled) and `terminal`
(disabled). An owner replying `#ABCDE approve` to a coverage proposal reaches nothing.
`state/pending.json` holds one proposal.

Combined with `handle_candidate_response` (the employee's "yes I can cover" reply) also being
SKILL-only, **neither the employee-reply nor the owner-approve leg of the Shift coverage loop is
reachable.** Only the inbound sick-call leg is. Hence PARTIAL rather than
DEPLOYED_AWAITING_LIVE_E2E — this is a structural gap, not merely an absence of traffic.

### 4.4 Flyer's manual queue is breaching SLA repeatedly

18 of the last 400 audit rows are `flyer_source_edit_sla_alert`, and
`state/flyer/source-edit-sla-alerts.json` was written at 18:00 today. The watchdog is doing its
job; the queue behind it is not being drained. Reachability is not the flyer's problem —
operator throughput is.

### 4.5 Agent #10 is one `systemctl enable` away from reachable

Uniquely in this matrix, #10 Catering Follow-up has **every** piece already built and installed:
three scripts in `/usr/local/bin`, a unit file at
`src/agents/catering/systemd/catering-followup-sweep.timer`, and a live F8 branch
(`hooks.py:1321-1346`) that already resolves `POOL_CATERING_FOLLOWUPS` codes and calls
`approve-catering-followup`. The timer is simply `disabled`.

Whether that was deliberate is not recorded anywhere I found. **Treat enabling it as an operator
decision, not a bug fix** — it sends outbound messages to real customers.

### 4.6 Expense Bookkeeper is blocked on code, not only on credentials

Easy to mis-classify as BLOCKED_CREDENTIALS, because `docs/portfolio.md:1121` says QBO
integration is "DEFERRED pending QBO sandbox creds (operator action)". That is now incomplete:
`src/platform/qbo_client.py:293-312` shows `RealQBOClient` is a stub whose every method raises
`NotImplementedError`, and `:337` shows the factory refuses `mode="real"` outright. Delivering
credentials tomorrow would change nothing. Hence BLOCKED_UNSUPPORTED_INTEGRATION.

The ingest half genuinely works and is well-guarded — `hooks.py:5444-5487` resolves owner
**membership** itself rather than trusting the caller-supplied `role` scalar, precisely because
this is a money record. It is the push half that does not exist.

### 4.7 Nine agents are SKILL-only scaffolds and none is worth a tool today

#6, #7, #9, #12, #14, #15, #16, #22 (and #10 modulo §4.5) exist as a single `*_dispatcher`
SKILL.md each. Beyond being unreachable, **none has a data file on disk to read**. Building a
thin read tool for any of them would produce another §4.1 "not configured" tool. Correct
sequencing is data first, tool second — not the reverse.

---

## 5. Read-shaped candidate ranking

Criteria applied: answerable by a **deterministic read over data that already exists on the
deployed box**, expressible in the ~75-LOC shape of `compliance_tool.py` / `equipment_tool.py` /
`location_tool.py`, with an authorization policy the tool owns itself via `identity.py`.

Every file named below was verified present on `main-vps`.

### 5.1 RANK 1 — `get_pending_catering_approvals` (owner-only)

- **Exact user question:** *"What catering leads are waiting on my approval?"* (also: "anything I
  need to sign off on?", "what's outstanding on catering?")
- **On-disk data (verified):** `/opt/shift-agent/state/catering-leads.json` — 33,243 bytes,
  20 leads. Status distribution: `OWNER_REJECTED` 8, `CLOSED` 4, `SENT_TO_CUSTOMER` 3,
  `AWAITING_OWNER_APPROVAL` 3, `CUSTOMER_FINALIZED` 2. Per-lead fields include `lead_id`,
  `status`, `owner_approval_code`, `created_at`, `updated_at`, `quote_total_usd`,
  `customer_name`, `customer_phone`, `deposit_status`.
- **Authorization:** **owner-only** via `identity.require_owner()`. Leads carry
  `customer_phone` and quote totals; this is not public data. The tool must not emit
  `customer_phone`.
- **Why rank 1:** the only candidate where (i) the data exists, (ii) it is rich and real, and
  (iii) the answer is *actionable* — the reply names the `#XXXXX` code, and F8
  (`hooks.py:1296-1319`) already executes that code deterministically. The tool closes the loop
  §4.2 shows is open, and it has a true, non-empty answer today: three leads, oldest 74 days.
- **Zero-state discipline required:** mirror `compliance_tool.py`'s four-state split
  (`disabled` when `cfg.catering.enabled` is false / `missing` / `empty` / `populated` with
  zero awaiting). "No leads awaiting approval" and "catering is switched off" must not collapse
  into each other. Bind the deterministic outbound text for every zero state exactly as
  `compliance_tool.py` does — this is a money-adjacent claim made to the owner.

### 5.2 RANK 2 — `get_catering_menu_items` (public)

- **Exact user question:** *"Do you cater biryani?"*, *"What veg appetizers do you have?"*,
  *"How much is the idly platter?"*, *"What's on your catering menu?"*
- **On-disk data (verified):** `/opt/shift-agent/state/catering-menu.json` — 16,631 bytes,
  `version: 2`, `updated_at: 2026-05-05`, **78 items**. Item shape verified:
  `{"name": "Idly (3 PCS)", "price_usd": 5.99, "category": "appetizer",
  "dietary_tags": ["veg"], "available": true, "notes": "", "serves": null}`.
- **Authorization:** **public** — same posture and same justification as `find_nearest_location`
  (`location_tool.py:8-10`): a published menu with published prices is customer-facing by
  design. The tool must expose only `name` / `price_usd` / `category` / `dietary_tags`, and must
  filter out `available: false`.
- **Why rank 2:** highest customer-facing value in the portfolio, and it fills a real routing
  gap. Today a customer asking "do you cater biryani?" hits the F7 catering classifier
  (`hooks.py:920-947`), which **creates a lead** rather than answering the question. A public
  menu read lets Hermes answer the pre-sales question without minting a lead.
- **Caveat the builder must handle:** a price quoted to a customer is a commitment. Bind the
  price line's exact outbound text (the `compliance_tool.py` mechanism), and surface
  `updated_at` so a 3.5-month-old menu is never presented as current without qualification.

### 5.3 RANK 3 — `get_roster_availability` (owner-only)

- **Exact user question:** *"Who can cover the tandoor shift tomorrow?"*, *"Who on the team
  speaks Telugu?"*, *"Who's currently active?"*
- **On-disk data (verified):** `/opt/shift-agent/roster.json` — 5,815 bytes, **8 employees**,
  top-level keys `location` / `employees` / `schedule`. Per-employee fields: `id`, `name`,
  `nickname`, `role`, `status`, `can_cover_roles`, `languages`, `restrictions`, `phone`, `lid`,
  `phone_history`. This is the same file `identify-sender` reads (verified by string-grep of the
  installed binary).
- **Authorization:** **owner-only**. Contains `phone` and `phone_history` — the tool must never
  emit either; return `name` / `role` / `status` / `can_cover_roles` / `languages` /
  `restrictions` only.
- **Why rank 3 and not higher:** smallest and safest read of the three, and a dead `roster_lookup`
  SKILL already documents the intent (`src/agents/shift/skills/roster_lookup`). But it is
  informational only — unlike §5.1 it does not unblock a stuck vertical, and unlike §5.2 it is
  not customer-facing revenue.

### 5.4 RANK 4 — `get_open_coverage_proposals` (owner-only)

- **Question:** *"Who have I asked to cover, and did they reply?"*
- **Data:** `/opt/shift-agent/state/pending.json` — 2,265 bytes, keys `proposals` /
  `next_proposal_seq`, **n=1 proposal**. Real but thin.
- **Note:** a genuine read, but §4.3 shows the *acting* half is what is broken. Pairing it with
  the `POOL_SHIFT` F8 branch (§3 row 1, change (ii)) would be worth more than the read alone.
  Ranked below §5.3 on data volume.

### 5.5 NOT RECOMMENDED — flyer project status

`/opt/shift-agent/state/flyer/projects.json` is **2,221,318 bytes**. That is not a thin
deterministic read, and cf-router already owns a reachable flyer status path. Skip.

### 5.6 NOT RECOMMENDED — compliance / equipment / location "improvements"

All three tools already exist and are correct (§4.1). Their gap is **operator data entry**
through already-installed writers (`add-compliance-item.py`, `add-equipment-item.py`) plus a
config block. Writing more code here would be motion, not progress.

---

## 6. Recommended next actions, in order

1. **Build §5.1 `get_pending_catering_approvals`** — closes the loop §4.2 shows is open, over
   data that already exists, with an actionable non-empty answer today.
2. **Build §5.2 `get_catering_menu_items`** — 78 items on disk, highest customer-facing value,
   removes the "a question becomes a lead" misroute.
3. **Surface to the operator; do not self-authorize:**
   - the three catering leads stuck since June/July (§4.2);
   - `catering-followup-sweep.timer` being disabled (§4.5) — enabling it sends real outbound;
   - the flyer source-edit SLA backlog (§4.4);
   - that #3 / #13 / #19 need config + data entry, not engineering (§4.1).
4. **Consider the `POOL_SHIFT` F8 branch** (§4.3) — roughly 20 lines mirroring an existing
   branch, but it mutates coverage state, so it needs its own review.

---

## 7. Method and limits

- Repo evidence read at `origin/main` `cdd89f0251aa882cb85049e3d603d3688f71a820`; the deployed
  `src/` surface is current against it (established by the orchestrator; not re-derived here).
- Box access was strictly read-only: `ls`, `cat`, `systemctl list-*` / `cat`, `journalctl`, and
  `python3 -c` reads over JSON state. No state mutated, no service restarted, no send-test run.
- **Not verified:** that the three `shift-agent-read` tools appear in a live model turn's
  tool_search results. Registration is proven from source and from `plugins.enabled` +
  `platform_toolsets.whatsapp` config; the gateway emits no plugin-load lines to journald, and
  confirming discovery end-to-end would require a live inbound, which is out of scope for a
  read-only audit. This does not affect any status above, since #3/#13/#19 are PARTIAL on the
  data gap regardless.
- **Inferred, not verified:** that `catering-followup-sweep.timer` was disabled deliberately.
  `systemctl` shows the vendor preset as `enabled` and the current state as `disabled`, which
  means someone or something disabled it; no record of who or why was found.
