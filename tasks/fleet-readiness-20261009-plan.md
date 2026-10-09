# Fleet readiness pass — 2026-10-09 (overnight autonomous)

**Drift-check tag:** `extends-Hermes` — one deterministic owner-token arm on the already-deployed
pre-gateway router (cf-router) connecting an existing kernel to the existing screened send, plus
documentation. No new store, router, scheduler, state machine, approval mechanism, importer or
notification path.

**New primitives introduced:** none. One router arm, one kernel idempotency guard, one flag-gated
hint field on an existing read tool.

**Operator brief (verbatim essentials):** "proceed and perform similar task on all other agents.
We have 20+ agents. Continue this as overnight autonomous task and make all production ready.
Goal is it should be less custom code and heavy Hermes dependent … apply all the best practices."
Standing rules from the same operator carry over: fix only reproduced blockers; never fake
OTP/approval/payment/serving facts; no bypassing guards; preserve identities; two-step SSH; state
missing human gates honestly rather than claiming readiness.

## Session bootstrap (discovered from origin/main `39c40300`)

```
Universal directive: docs/governance/engineering-directive.md v1.2.0 blob 31d7706d
Project registry:    docs/governance/project-registry.yaml v1.0.0 blob 207ad418
Affected projects:   shift-platform, compliance, shift-agent (shift-agent-read tool test pattern),
                     catering-studio (docs), expense-bookkeeper (separate PR),
                     phase0-agents (assessment only), repo-meta (this plan + audit record)
Applicable directives:
  docs/governance/shared-platform-directive.md v1.1.0 blob 9c6ce256
  docs/governance/projects/compliance.md v1.0.0 blob 1312d00e
  docs/governance/projects/shift-agent.md v1.0.0 blob fde35f3a
  docs/governance/projects/catering-studio.md v1.0.0 blob 858bd9f2
  docs/governance/projects/expense-bookkeeper.md v1.1.0 blob dc0f1783
  docs/governance/projects/phase0-agents.md v1.0.0 blob c7af0452
Shared-platform impact: WP-B touches src/plugins/cf-router (runs on every inbound) — impact section below
New subsystem proposed: no
Architecture exception: none
```

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Owner natural-language questions about deadlines | yes — Hermes Tool Search → `get_compliance_deadlines` (shift-agent-read plugin; live proof 2026-08-08 `agent.log:4495-4503`) | reuse; Hermes owns intent + wording |
| Owner mutation "mark X done" | Hermes tool-choice COULD call a mutation tool, but the deployed house pattern for owner-authorized mutations is a deterministic structured token (`#CODE approve`, `APPROVE`) parsed pre-gateway by the existing cf-router | deterministic arm on the existing router (design memo 2026-10-09: shape A; shape B — a `shift-agent-act` plugin tool — rejected on §4 grounds) |
| Fleet readiness audit | none — repo convention `tasks/audits/` | documentation only |
| Phase-0 scaffold capability mapping | shared-platform-directive §1a: bundled `productivity/*` skills, `mcp/native-mcp` bridge | assessment only; no build without promotion + customer data (phase0-agents.md rule 2) |
| QBO write (expense) | repo already ruled: review Intuit QBO MCP via `mcp/native-mcp` before a custom client | decision record only; credentials are an operator gate |
| LID↔phone learning (shift) | Baileys 7.0.0-rc13 inside the Hermes bridge persists `lid-mapping-{phone}.json` and `bridge.js:283-297` already builds the reverse map | design options for the operator; verify session-dir evidence first |

awesome-hermes-agent / skills hub: NOT re-fetched this session; the in-repo 2026-05-03 four-source
audit (`tasks/skills-roadmap.md`) and shared-platform-directive §1a are the basis.
Verdict: the only runtime change is a thin adapter from the existing router to an existing kernel;
everything else is evidence and decisions.

## Hermes-first capability checklist (per step)

| Step | Owner | net-new LOC |
|---|---|---|
| 1. Owner sends `mark <id> done` on WhatsApp | `[Hermes]` WhatsApp gateway + bridge inbound | 0 |
| 2. Pre-gateway hook sees the text before the LLM | `[Hermes]` existing cf-router `pre_gateway_dispatch` hook | 0 |
| 3. Identity + owner authorization | `[Hermes]` `identify-sender` / `is_owner_chat` kernel (sender_role gating) | 0 |
| 4. Dormant env flag + `cfg.compliance.enabled` gate | `[net-new]` arm-specific kill switch following the existing env idiom | ~10 |
| 5. Exact-token fullmatch + `dispatcher_routed` before delegation | `[net-new]` the arm itself; audit write is the existing `audit_dispatcher_routed` | ~15 |
| 6. Kernel runs under its own locks, writes `compliance_item_marked_done` | `[Hermes]` existing `mark-compliance-item-done.py` (SKILLs/arms are scripts with subprocess access) | 0 |
| 7. Kernel refuses a second mark of the same item within 15 min | `[net-new]` idempotency guard inside the kernel (HIGH from design memo) | ~20 |
| 8. Bounded owner reply via screened `bridge_post`, never falls to the LLM after routing | `[net-new]` JSON→fixed text mapping; send is substrate | ~25 |
| 9. Owner discovers ids by asking in free text | `[Hermes]` Tool Search → `get_compliance_deadlines` (+ flag-gated hint field) | ~5 |
| 10. Readiness matrix, QBO decision, Phase-0 assessments, follow-up note, operator checklist | `[Hermes]` documentation over audit-chain evidence | 0 |

4 of 10 steps net-new (~75 LOC runtime + ~14 tests). Red-flag check: below half; file reads,
audit rows, identity and the send path were re-checked and are substrate.

## Drift-rule self-checks

- ✅ Read `src/plugins/cf-router/hooks.py` (owner-command arm ~7790–7871, F8/F9 arms ~740–760, env-flag helpers 150–181) before drafting the arm
- ✅ Read `src/plugins/cf-router/actions.py` (`audit_dispatcher_routed` 2289–2347 incl. the stderr-only failure path; `fire_pushover_alert` 1206) before drafting audit/reply handling
- ✅ Read `src/agents/compliance/scripts/mark-compliance-item-done.py` (lock order, sentinel GC, exit codes, lines 60–200) before drafting the idempotency guard
- ✅ Read `src/agents/compliance/skills/compliance_owner_query/SKILL.md` (hard rule: REQUIRE explicit "mark <item> done" phrasing) before choosing the token shape
- ✅ Read `src/plugins/shift-agent-read/compliance_tool.py` + `identity.py` (zero-state binding, `register_turn_outbound_override`) before drafting the discovery hint
- ✅ Read `src/platform/catering_followups.py` (lines 160–200, `FOLLOWUP_EXTRA_ALLOWED_STATUSES` admits CLOSED for `post_event_feedback`) and `shift-agent-deploy.sh:1565-1580` before correcting the catering_followup scaffold note
- ✅ Read `docs/governance/projects/phase0-agents.md`, `compliance.md`, `expense-bookkeeper.md`, `shared-platform-directive.md` §1a/§2 before scoping assessments
- ✅ Read live `/root/.hermes/config.yaml` toolsets, `/opt/shift-agent/config.yaml` agent blocks and the 30-day decisions.log type counts on main-vps (read-only, two-step SSH) before classifying any agent

## Runtime facts verified tonight (main-vps, read-only, 2026-10-09 ~01:00Z)

- Box on `50a1daa0` (receipt 2026-10-08T15:53:03Z); `main` = `39c40300`; canonical tarball for
  39c40300 staged at `/tmp/shift-agent-deploy-39c40300.tgz`; deploy still blocked by the OpenRouter
  per-key cap (`/auth/key limit 30, remaining 0` → vision-auth gate HTTP 403).
- 0 failed units; hermes-gateway + cockpit active; bridge `/health` connected (the bridge runs inside
  the gateway unit — there is no separate `whatsapp-bridge` unit).
- Timers active: brief, eod, compliance (06:00), flyer watchdogs, catering sweeps, backup, fsck,
  openrouter-balance (14:00, logging normally), prune-expense. `catering-followup-sweep.timer`
  installed, **disabled by design** (triple-gated; unit comment + deploy script :1572).
- 30-day audit denominator: **12 `cf_router_raw_body` rows, all on 2026-10-03 00:27–02:39Z**
  (operator session); 14 `cf_router_intercepted`; **0 `dispatcher_routed`** — explained by the
  denominator: the only emitters are the shift F8/F9, owner-command, update_catering_menu and
  parse_receipt_photo arms (`hooks.py:463,753,5677,5891,7871`); flyer/catering F7 emit
  `cf_router_intercepted` instead. Not an emit failure. The emit's failure path is stderr-only
  (`actions.py:2347`) — §12b backlog item, not a blocker.
- `config.yaml`: `compliance:` block ABSENT (→ disabled), no `compliance-items.json`, no
  `equipment-items.json`; `expense_bookkeeper.enabled=true, qbo_client_mode=mock`, zero receipts
  ever; `owner.name: Srini` (+17329837841, lid 201975216009469@lid); customer Triveni /
  loc_pineville_01 (single location).
- `/opt/shift-agent/roster.json` is **root-owned** (May 26), mode 644 — readable by the service;
  any writer running as `shift-agent` would fail. Same class as R4.
- `lid-cache.json` = `{}`; `WHATSAPP_LID_CACHE_WRITE` env is vestigial. Baileys 7.0.0-rc13 in the
  live bridge (`bridge.js:283-297`) builds a LID→phone map from `lid-mapping-{phone}.json` session
  files — the native mechanism the retired custom backfill duplicated (WP-F).
- Hermes 0.19.1 hook names available in the installed gateway: `pre_llm_call`, `pre_api_request`,
  `post_api_request`, `api_request_error`, `post_tool_call`, `on_session_start`, `on_session_end`,
  `subagent_stop`; we register only `pre_gateway_dispatch` (cf-router). No MCP servers configured.
- 43 stale `.bak-*/.pre-*/codex-*` binaries in `/usr/local/bin` (quarantine candidates — provenance
  before deletion; operator decision). `ufw` inactive (hold #4 from 2026-09-02 still open).

## Agent matrix — status tonight vs 2026-09-02

| agent | 09-02 status | tonight | change |
|---|---|---|---|
| daily_brief | DEPLOYED_AWAITING_ORGANIC_E2E | same; 27 `brief_sent`/30d, 3 `brief_send_failed` | recipient is still the operator |
| eod_reconcile | DEPLOYED_AWAITING_ORGANIC_E2E | same; 30 snapshots/30d | — |
| shift | DEPLOYED_AWAITING_ORGANIC_E2E | same; 0 employee inbound/30d | LID-learning design → WP-F |
| equipment_maintenance | DEPLOYED_AWAITING_ORGANIC_E2E | same; no items file | operator seed (`add-equipment-item.py`) |
| compliance | BLOCKED_ON_REAL_DATA/CONFIG | same; cfg absent, no items | write half orphaned → WP-B |
| multi_location | DEPLOYED_AWAITING_APPLICABLE_DATA | same (single location) | — |
| catering | PARTIAL | PARTIAL; pricebook v2 PILOT since 09-03; #796–#799 merged, not deployed | see `tasks/catering-flyer-readiness-20261008.md` |
| flyer | PARTIAL | PARTIAL; same | same |
| expense_bookkeeper | PARTIAL | PARTIAL; DRAFT tier only; zero receipts | QBO = operator credentials (WP-D) |
| catering_followup | NOT_IMPLEMENTED | **CORRECTED → IMPLEMENTED_DORMANT**: M5 engine (`catering_followups.py`, `catering-followup-sweep`, `create/approve-catering-followup`, timer installed-disabled) exists; `post_event_feedback` admits CLOSED leads. The scaffold SKILL's "not yet wired" note is stale | WP-A docs fix |
| 8 Phase-0 scaffolds | NOT_REACHABLE | same; no handlers, no data | WP-E assessment only |

## Work packages

| id | scope | project(s) | deliverable | status |
|---|---|---|---|---|
| WP-A | docs | repo-meta, catering-studio | `tasks/audits/fleet-readiness-2026-10-09.md`; correct `catering_followup_dispatcher/SKILL.md` honesty note | pending |
| WP-B | runtime, dormant | shift-platform, compliance | owner `mark <id> done` arm per design memo + kernel double-mark guard + flag-gated read-tool hint + tests | in implementation |
| WP-C | finding only | shift-platform | `dispatcher_routed` emit failure stderr-only — recorded; no code (not reproduced) | done (recorded) |
| WP-D | decision record | expense-bookkeeper | DRAFT-tier reachability proof status; SUPERVISED-tier operator checklist; QBO MCP decision | research running |
| WP-E | assessment | phase0-agents | per-agent Hermes-first promotion assessment — no code | research running |
| WP-F | design options | shift-agent / shift-platform | LID-learning options from live `bridge.js` + session-dir evidence | pending |
| WP-G | human checklist | ops | roster ownership, stale bins, ufw, compliance/equipment seeds, follow-up arming, key cap | pending |

## WP-B design decision (memo 2026-10-09, deep-reasoner; adopted)

- **Shape A** — deterministic owner-token arm in cf-router, placed after the F8 `#CODE` block and
  before `_try_automation_control`. Gate order (post-review): env `COMPLIANCE_MARK_DONE_ENABLED ==
  "1"` → regex `fullmatch` `mark\s+(?P<id>[a-z0-9_]{1,40})\s+(?:as\s+)?done[.!]?` (ASCII,
  case-insensitive, id lowercased; 40 = `ComplianceItem.id` max_length) → `is_owner_chat`
  (non-owner falls through silently) → `cfg.compliance.enabled` readable AND true (else fall
  through — no claimed turn without an audit row) → **the id is a configured item** in
  `compliance-items.json` (else fall through: "mark order done" / "mark it done" reach Hermes
  untouched — a structural check instead of a stopword denylist, review F1) →
  `audit_dispatcher_routed(routed_to_skill="mark_compliance_item_done", authority="owner")` BEFORE
  invoking → kernel with exact argv → bounded reply → `{"action":"skip"}`. **Never returns None
  after the route row.**
- Replies keyed on the kernel's stdout JSON, not the exit code alone (review H1: a CPython crash
  also exits 1): OK-recurring, OK-one-shot, NOTFOUND (rc 1 + `error=item_not_found|
  items_file_missing_recreated`, TOCTOU only), RECENT (rc 3 + `error=recently_marked_done`),
  UNCERTAIN (everything else; a timeout may have mutated state, so it is not reported as failure,
  and every UNCERTAIN writes `audit_intercepted(reason="error")`).
  Success sends carry `claims_action_completed=verified_action_result=True`; everything else False.
  Reply copy avoids FORBIDDEN_COMPLETION_VERBS so an alias-owner's screened send is not rewritten.
- Audit: existing types only (`dispatcher_routed`, `audit_intercepted reason="error"` on UNCERTAIN,
  `compliance_item_marked_done` from the kernel). No new tag, no widened Literal → rollback-safe.
- **Kernel HIGH (fix before arming):** `mark-compliance-item-done.py:126-131` advances on every
  call; two distinct owner messages skip a whole cycle. Guard: refuse (rc 3) when a
  `compliance_item_marked_done` row for the same `item_id` is within 900 s in the decisions.log
  tail (reusing an existing tail reader if one exists).
- Discovery: `get_compliance_deadlines` adds a `to_mark_done` hint field ONLY when the same env
  flag is "1" (an unconditional hint while dormant would invite the LLM to answer the token).
- Arming (operator): deploy a tarball carrying #798 + this change → enable `compliance.enabled`
  AND `COMPLIANCE_MARK_DONE_ENABLED=1` (in the `.env` TARGET) in the same window → restart gateway →
  seeded witness: `add-compliance-item.py` a one-shot probe item, owner sends `mark <probe_id>
  done`, expect OK-ONESHOT reply, item deleted, `dispatcher_routed` then
  `compliance_item_marked_done` in decisions.log (an unknown id now falls through to Hermes with
  no route row, so a not-found probe proves nothing). Disarm = remove line + restart. Graduation:
  after the witness + one real owner mark, drop the env flag and gate on `cfg.compliance.enabled`.
- Shape B rejected: the mutation trigger would be model tool-choice (directive §4), the read
  preflight would page on every boot for a deliberately dormant toolset, and model retries are
  not covered by the inbound dedupe.

## Capability Reuse Map — shift-platform / compliance (WP-B)

- Requested outcome: an owner completes a tracked compliance item from WhatsApp without the operator
  running `mark-compliance-item-done.py` by hand.
- Affected projects: shift-platform (cf-router), compliance.
- Applicable directives: engineering-directive v1.2.0; shared-platform v1.1.0; compliance v1.0.0.
- Existing platform/model capabilities reused: Hermes Tool Search + `get_compliance_deadlines` for
  discovery/presentation; cf-router pre-gateway hook; identify-sender / is_owner_chat; screened
  `bridge_post`; inbound dedupe.
- Existing deterministic kernels reused: `mark-compliance-item-done.py` (locks, sentinel GC,
  `compliance_item_marked_done` audit).
- Existing stores/workflows reused: `state/compliance-items.json`, `compliance-last-sent.json`,
  decisions.log.
- Thin adapters: one router arm → exact argv → JSON → bounded owner reply.
- Custom runtime code genuinely unavoidable: the arm (~50 LOC), the kernel guard (~20 LOC), tests.
- New subsystem: none.
- Evidence existing capabilities were insufficient: SKILL dispatcher unreachable (`terminal`,
  `skills` disabled on the box); kernel has no other caller (audit 2026-09-02 §4).
- Architecture exception: none.
- Shared-platform impact: cf-router executes for every inbound; the arm returns before any I/O
  unless the env flag is "1", is owner-only and token-exact — every other agent's routing is
  byte-identical.
- Other agents affected: none at default; compliance only when armed.
- Vertical E2E proof: test — owner text `mark health_permit done` → kernel runs → store mutated →
  `compliance_item_marked_done` row after `dispatcher_routed` → bounded reply; MUTANT (kernel
  stubbed to rc 0 `{}`) → store unchanged, UNCERTAIN reply; non-owner / flag unset / disabled /
  unknown id → no mutation.

## Not in scope tonight (and why)

- Building handlers for the 8 Phase-0 scaffolds: their directive requires promotion (own directive,
  registry entry, E2E proof) and the portfolio tiers them "build after paying customers"; the pilot
  box has no inventory/invoice/POS/customer data for any of them. Building would be
  architecture-only output (directive §5) and MORE custom code — the opposite of the brief.
- Any box config change (`config.yaml`, `.env`, `platform_toolsets`), any deploy (key-cap gate), any
  arming of dormant features — operator decisions.
- Hermes hook expansion (we register 1 of the 8 hook points the installed gateway exposes): the
  turn-observability plan is on operator HOLD; recorded in the report, not acted on.
