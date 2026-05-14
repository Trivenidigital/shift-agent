# Hermes SMB Operating Layer Roadmap

**Drift-check tag:** extends-Hermes

**New primitives introduced:** No runtime primitives in this roadmap. This document introduces a phased product and execution strategy that reuses deployed Hermes substrate, current SMB-Agents skills, current VPS capabilities, and connector-first MCP posture before any custom code.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp/customer messaging | yes - Hermes gateway plus deployed WhatsApp bridge and project `cf-router` | use it; customer-facing product remains WhatsApp-first |
| Owner control tower | yes - deployed Daily Brief, cron/timers, audit logs, and state files | use it; make Daily Brief the owner-facing anchor |
| Vision/document ingestion | yes - Hermes vision substrate plus `productivity/ocr-and-documents` on target VPS | use it; custom extraction only for schemas/business rules |
| Multi-agent work | yes - Hermes profiles, delegation, cron, and Kanban in current ecosystem | use it internally for operator/company workflows; do not expose fake org-chart language to SMB owners |
| Skills and self-improvement | yes - Hermes skills, memory, session search, Self-Evolution Kit ecosystem | use it in staging/evals; production self-learning remains state/memory/report-only |
| Commercial write APIs | partial - `mcp/native-mcp` plus vendor/vetted MCP candidates for QBO, Stripe, Square, PayPal, DocuSign, reviews | connector-first; custom raw APIs only after connector review fails |
| SMB vertical business logic | none found as a full off-the-shelf skill | build narrow custom business loops on top of Hermes substrate |

Awesome-Hermes-Agent ecosystem check: reviewed as of 2026-05-14; useful for tools, dashboards, Paperclip adapter, and operations patterns, but weak for ethnic SMB vertical logic. Verdict: use ecosystem for orchestration and integrations, not as a replacement for our domain agents.

## Strategic thesis

SMB-Agents should become the AI operations desk for ethnic SMBs: restaurants, groceries, food courts, and catering businesses where owners live in WhatsApp and need fewer dropped balls, not another dashboard.

The external product should feel like:

- Morning brief.
- Sick-call coverage.
- Catering lead desk.
- Menu updater.
- Compliance reminder.
- Owner approval queue.

The internal operating model can look like an AI company:

- SMB Ops CEO keeps the mission, portfolio, and customer readiness aligned.
- Customer Success Agent watches pilot health and owner friction.
- Hermes Engineer builds and deploys skills.
- Hermes Tester runs readiness, smoke, replay, and audit checks.
- Integration Scout reviews MCP/vendor connector candidates before custom API work.
- Market/Content Agent turns real use cases into sales collateral.
- Safety/Governance Agent checks approvals, audit, privacy, budgets, and silent-failure surfaces.

## Market read

The 2026 SMB market is moving from "AI curiosity" to "AI operations." Current research points in one direction: adoption is rising quickly, but governance, trust, and workflow integration are lagging. That is the opening.

Relevant signals:

- Pax8 reported in March 2026 that 62 percent of SMB leaders believe they will not remain competitive without AI within three years, while many are adopting faster than they can govern.
- Business.com reported that 57 percent of U.S. small businesses are investing in AI and that the average worker saves 5.6 hours per week, but trust and training remain core adoption constraints.
- OECD's SME AI adoption report frames the maturity path from embedded peripheral tools to AI Champions that coordinate workflows across operations and strategy.
- MCP ecosystem research shows action tools are growing rapidly, including higher-stakes domains such as financial transactions, which supports our approval-first posture.

Implication: our wedge is not "more automation." It is governed operational relief with owner-visible controls.

## Product principles

1. One agent equals one business loop, not one job title.
2. Daily Brief is the owner's control tower.
3. Every customer-facing claim must trace to state, audit, menu, roster, lead, or approval code.
4. Owner approval gates protect money, pricing, booking, staff outreach, supplier disputes, and public replies.
5. Hermes skills carry repeatable procedure; memory carries stable facts; audit logs carry history.
6. Cron/timers carry proactive value: brief, follow-up, reminder, watchdog, freshness check.
7. MCP/vendor connector first; custom API second.
8. Production self-learning is state/memory/report-only. Prompt, skill, model, deploy config, and code evolution go through staging, evals, review, PR, and tarball deploy.
9. Silent failure is the main enemy. Every new pipeline activity needs freshness or write-site alerting proportional to risk.
10. Owner UX beats agent theatrics. Internally we can run a company of agents; externally we should sound like a reliable operations desk.

## Phase 0 - Tonight: make the vision operational

Goal: convert the market thesis into durable backlog, execution order, and acceptance gates.

Status: captured as supporting strategy context in the first Phase 1 hardening PR, `feat/pilot-readiness-location-hardening`.

Scope:

- Capture this roadmap in `tasks/hermes-smb-operating-layer-roadmap-2026-05-14.md`.
- Add an active phased checklist to `tasks/todo.md`.
- Map existing pilot items into the new strategy rather than creating a parallel plan.
- Identify which later phases are blocked by live WhatsApp traffic, customer POS choice, credentials, or owner approval.

Exit criteria:

- Roadmap has drift tag, Hermes-first analysis, phase gates, and concrete backlog tasks.
- `tasks/todo.md` has a top-level active section for the operating-layer transformation.
- Any runtime code in the same overnight PR is explicitly scoped to Phase 1 pilot hardening, not Phase 0 strategy capture.

## Phase 1 - Pilot proof: make Shift + Catering + Daily Brief undeniable

Goal: turn the current pilot bundle into the reference proof that the operations desk works.

Build on existing active backlog:

- Run the live WhatsApp smoke script in `docs/runbooks/production-pilot-shift-catering-daily-brief.md`.
- Close the hidden placeholder gap by tightening `pilot-readiness-check` so config location and roster location must agree, roster location labels are production-looking, and a meaningful location-id token such as `pineville` appears in the roster location name.
- Capture owner-visible proof: screenshots/messages, audit tail, readiness output, and brief output.
- Convert the pilot smoke into a repeatable customer onboarding acceptance pack.

Unblocked now:

- Backlog/doc updates.
- Readiness-gate tightening.
- Local tests.

Blocked on operator/live interaction:

- Full WhatsApp smoke requires sending real messages through the business/self-chat.

Acceptance gates:

- `pilot-readiness-check --text` reports READY.
- WhatsApp bridge connected and `hermes-gateway` active.
- Catering inquiry/proposal/selection/final owner approval path works without premature price/payment/booking language.
- Sick-call path creates owner approval and candidate response state with audit.
- Daily Brief includes relevant activity without hallucinated numbers.

## Phase 2 - Customer-facing operating desk expansion

Goal: add the next highest-ROI SMB loops that are Hermes-native and low integration risk.

Recommended order:

1. Special Request Memory: remember no onion, Jain, extra spicy, no cilantro, allergies, and family preferences by verified contact.
2. Loyalty and Punch-Card: simple WhatsApp loyalty state with owner-approved rewards.
3. Photo Menu Curator: dish photo in, caption/metadata/menu update proposal out.
4. Local Community Broadcast: opt-in broadcast lists for festival specials and owner-approved announcements.
5. Owner Wellbeing and Quiet Hours: weekly load summary plus hard quiet-hours guard.
6. Catering Equipment and Packaging Tracker: deposits, chafers, hot boxes, serving trays, return reminders.
7. Performance and Training Coach: staff SOP quizzes and gentle coaching.

Hermes-first posture:

- Use JSON-on-disk plus `safe_io` patterns.
- Use existing WhatsApp delivery.
- Use Daily Brief for synthesis.
- Use approval codes for owner-sensitive actions.
- Use cron/timers for reminders.

Defer:

- POS/KDS-dependent agents until a customer POS is chosen and verified.
- Public review replies until Google/Facebook connector path is reviewed.
- Money movement until connector scopes and approval UX are nailed down.

Acceptance gates:

- Each new loop has deterministic state, audit, replay or script tests, and Daily Brief integration where relevant.
- Each new outbound customer/staff/supplier action has explicit owner approval unless the action is read-only or pre-approved by configuration.

## Phase 3 - Internal AI company operating system

Goal: use Hermes/Paperclip-style organization internally to increase build speed, review quality, and customer support without exposing complexity to owners.

Roles to instantiate as Hermes profiles, Paperclip agents, or Codex workflows:

- SMB Ops CEO: owns roadmap, customer readiness, and phase priority.
- Hermes Engineer: implements skills/scripts/tests.
- Hermes Tester: runs smoke, replay, readiness, and audit checks.
- Integration Scout: checks MCP/vendor connectors and credentials before custom code.
- Customer Success Agent: watches pilot outcomes and owner friction.
- Market/Content Agent: turns validated use cases into demos, pages, and outbound collateral.
- Safety/Governance Agent: reviews approval gates, auditability, freshness SLOs, privacy, and budget risk.

Initial role cards:

| Role | Mission | Allowed work | Escalates when |
|---|---|---|---|
| SMB Ops CEO | Keep product mission, pilot status, customer readiness, and build order aligned. | Triage backlog, propose phase priorities, synthesize customer proof, open plans. | Scope expands, priority conflicts, or owner/customer commitments change. |
| Hermes Engineer | Turn approved plans into small, tested Hermes-native code and SKILL changes. | Edit repo code, scripts, tests, docs, and deploy artifacts after plan approval. | Runtime state is unknown, credentials are missing, or a change touches money/audit/schema. |
| Hermes Tester | Prove behavior before claims. | Run local tests, VPS smoke, readiness checks, replay harnesses, and audit-tail verification. | Evidence is missing, flakes repeat, or a silent-failure surface appears. |
| Integration Scout | Prevent custom API debt. | Review installed skills, `mcp/native-mcp`, vendor MCPs, API scopes, and connector maturity. | Connector requires risky scopes, paid approval, unclear maintenance, or custom code remains. |
| Customer Success Agent | Watch the owner experience. | Track pilot friction, unanswered approvals, repeated owner edits, and confused staff/customer replies. | Owner burden increases or messages show trust erosion. |
| Market/Content Agent | Convert real proof into sales assets. | Draft demo scripts, landing copy, one-pagers, and vertical use-case narratives. | A claim lacks smoke/audit proof or exposes customer-private details. |
| Safety/Governance Agent | Keep autonomy bounded. | Review approval gates, audit entries, freshness checks, privacy, quiet hours, and budget ceilings. | Automated state changes, money flows, external writes, schema changes, or irreversible actions appear. |

Implementation options:

- Lightweight first: use repo docs, Codex tasks, and manual role prompts.
- Hermes-native next: create Hermes profiles with role-specific skills and shared Kanban.
- Paperclip later: evaluate hosted or self-hosted Paperclip only after internal role workflows prove repeatable.

Acceptance gates:

- Each role has a clear mission, budget expectation, allowed tools, and escalation path.
- No role can deploy, spend money, or change production behavior without explicit operator approval.
- Work is traceable to tasks, PRs, smoke evidence, and review notes.

## Phase 4 - Connector-first integration layer

Goal: expand into accounting, payments, POS, reviews, calendar, and documents without overbuilding raw APIs.

Priority connector reviews:

1. Intuit QuickBooks Online MCP for Expense Bookkeeper.
2. Stripe/Square/PayPal MCP or vendor connectors for Cash and AR.
3. Clover/Square/Toast POS path for EOD, order state, inventory, and P&L.
4. Google Business Profile/Facebook review access for review responder.
5. Google Workspace/Drive/Sheets/Calendar for rosters, compliance, docs, and hiring.
6. DocuSign or equivalent e-sign connector for onboarding.

Initial connector-review queue:

| Queue item | First question | Required evidence before build |
|---|---|---|
| QBO Expense Bookkeeper | Can Intuit QBO MCP create/read bills, expenses, vendors, and attachments with scoped OAuth? | MCP source, scopes, refresh-token path, sandbox write test plan, owner approval UX. |
| POS order/inventory | Which POS does the first paying customer actually use: Clover, Square, Toast, or other? | Runtime customer POS choice, API/webhook access, order-state freshness, read-only proof. |
| Payments and AR | Which rails matter first: Stripe, Square, PayPal, Zelle, Venmo, Cash App, Razorpay? | Connector availability, write scopes, approval thresholds, audit row design, rollback/cancel path. |
| Reviews | Which public review surfaces matter first: Google Business Profile, Facebook, Yelp? | API/connector access, owner approval before replies, no auto-public-post policy. |
| E-sign and employee docs | Is DocuSign worth early setup or should v0 stay Drive/Docs/manual? | Connector maturity, signed-document storage path, employee privacy handling. |

Acceptance gates before build:

- Verify current value, active state, path-to-lever, and expected fire rate.
- Document credential scopes and approval requirements.
- Add freshness/watchdog checks for new pipeline tables or state files.
- Prefer read-only first, then write-mode behind owner approval.

## Phase 5 - Evaluation and self-improvement loop

Goal: make agents improve safely from real traces without mutating production code or prompts directly.

Components:

- Golden scenario library for catering, shift, brief, menu, and future loops.
- Replay harness expansion from synthetic to real redacted traffic.
- LLM-as-judge rubrics for groundedness, approval safety, tone, and owner burden.
- Staging-only Self-Evolution Kit experiments for skills/prompts.
- PR generator for proposed skill improvements with metrics and diffs.

Initial eval backlog:

| Eval set | What it protects | First cases |
|---|---|---|
| Catering safety | No premature price, deposit, booking, or payment language. | New lead, proposal request, option selection, owner approval, menu item grounding, off-menu request. |
| Shift coverage | No invented employees or unaudited state changes. | Sick call, owner approval, candidate accept, candidate decline, no candidate available. |
| Daily Brief | No hallucinated stats and no overload. | Empty day, active catering lead, shift event, learning summary, timer force-send. |
| Menu update | Owner/employee upload allowed, owner apply only. | Employee proposes menu, owner applies, non-owner apply rejected, archive retained. |
| Special Request Memory | Remember preferences without leaking or over-applying them. | Jain/no onion preference, allergy caution, family/friend request, outdated preference correction. |
| Connector writes | External writes require owner approval and audit. | QBO mock write, payment link proposal, public review draft, POS read-only anomaly. |

Acceptance gates:

- No production code/SKILL/prompt/model/config mutation outside PR/deploy.
- Evals demonstrate improvement without regressing approval safety.
- Redaction guarantees no raw customer phone, address, payment, or private dietary data leaks into shared datasets.

## Phase 6 - Go-to-market proof and packaging

Goal: convert pilot wins into sales assets and a repeatable onboarding motion.

Artifacts:

- "AI operations desk for ethnic SMBs" one-page positioning.
- Demo script using the production pilot flows.
- Before/after owner workload story.
- Security and control page: approvals, audit, privacy, WhatsApp-first, per-customer VPS.
- Vertical pages for catering, groceries, restaurants, food courts.
- Pricing packaging by bundle: Starter Operations, Revenue Desk, Finance Desk, Full Ops Desk.

Initial GTM spine:

- Category: AI operations desk for ethnic SMBs.
- Wedge: WhatsApp-first operations relief for restaurants, groceries, food courts, and catering teams.
- Promise: fewer missed leads, fewer owner interruptions, fewer forgotten follow-ups, more visible control.
- Proof path: Shift + Catering + Daily Brief pilot smoke, audit evidence, owner-facing messages, readiness gate.
- Differentiator: per-customer VPS, approval-first autonomy, vertical memory, Hermes skills, and connector-first integrations.
- Avoided language: do not lead with "AI CEO" or "zero-human company" for customers. Use that only as an internal operating metaphor.
- First demo story: owner wakes up to Daily Brief, gets a catering inquiry structured, approves a proposal safely, handles a sick call, and sees the day summarized without opening a dashboard.

Acceptance gates:

- Every marketing claim has a product proof or live smoke evidence behind it.
- No customer case study uses private data without explicit permission.
- Sales story leads with owner relief and revenue retention, not "AI workforce" jargon.

## Phase dependency map

| Phase | Can start tonight? | Main blocker |
|---|---:|---|
| Phase 0 roadmap/backlog | yes | none |
| Phase 1 pilot proof | partially | live WhatsApp smoke requires operator/customer messages |
| Phase 2 customer loops | partially | needs per-agent plan approval and in some cases customer demand |
| Phase 3 internal AI company | yes, lightweight | Paperclip/Hermes profile setup decision |
| Phase 4 integrations | research yes, writes no | credentials, scopes, customer POS/payment/accounting choice |
| Phase 5 eval/self-improvement | partially | needs redacted real traces for high value |
| Phase 6 GTM | partially | needs pilot proof artifacts |

## Immediate recommended execution order

Tonight:

1. Land this roadmap and backlog section.
2. Tighten Phase 1 into a concrete customer-pilot proof checklist.
3. Draft the internal AI-company role map without installing Paperclip yet.
4. Select the first Phase 2 build candidate after pilot smoke: Special Request Memory is the best first build because it is low-risk, high-delight, and Hermes-native.

Next build cycle:

1. Finish pilot smoke and readiness hardening.
2. Build Special Request Memory with tests and Daily Brief integration.
3. Build Loyalty/Punch-Card or Photo Menu Curator depending on customer pain.
4. Start connector review for QBO/POS only after first customer operational proof is stable.

## Open decisions

- Whether to evaluate Paperclip immediately or first emulate the role structure with Hermes profiles and repo tasks.
- Whether the first Phase 2 build should be Special Request Memory, Loyalty/Punch-Card, or Photo Menu Curator.
- Whether GTM collateral should be built before or after the live WhatsApp pilot smoke completes.
- Whether the internal AI company roles should live in Hermes profiles, Paperclip, Codex prompts, or a hybrid.

## Sources

- Hermes docs: https://hermes-agent.nousresearch.com/docs/
- Hermes skills: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/
- Hermes MCP: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/
- Hermes cron: https://hermes-agent.nousresearch.com/docs/user-guide/features/cron/
- Hermes delegation: https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation
- Hermes profiles: https://hermes-agent.nousresearch.com/docs/user-guide/profiles/
- Paperclip: https://paperclip.inc/
- Paperclip docs: https://docs.paperclip.ing/start/what-is-paperclip
- Awesome Hermes Agent: https://github.com/0xNyk/awesome-hermes-agent
- Pax8 SMB AI survey: https://www.globenewswire.com/news-release/2026/03/24/3261322/0/en/new-Pax8-Research-Reveals-Small-Businesses-Are-Adopting-AI-Faster-Than-They-re-Building-Strategies-to-Manage-It.html
- Business.com SMB AI report: https://www.business.com/articles/ai-usage-smb-workplace-study/
- OECD SME AI adoption report: https://www.oecd.org/content/dam/oecd/en/publications/reports/2025/12/ai-adoption-by-small-and-medium-sized-enterprises_9c48eae6/426399c1-en.pdf
- MCP tool usage paper: https://arxiv.org/abs/2603.23802
