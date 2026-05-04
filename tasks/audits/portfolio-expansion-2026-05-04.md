# Portfolio expansion — 25-agent strategic reshape (2026-05-04)

**Drift-check tag:** `Hermes-native` (doc-only; no code proposed)

User-supplied portfolio reshape (post-overnight closeout) reorganizes from Solid 17 + 5 backlog into a 25-agent target organized by 9 business domains. **16 net-new agents**, 4 mergers/reframings, 3 status corrections vs my current portfolio.md.

## User's new portfolio (verbatim domain groupings)

1. **Workforce & Scheduling**: Shift Agent + Swap & Coverage; Hiring & Onboarding; Performance & Training Coach
2. **Catering & High-Margin Revenue**: Catering Lead + Closer; Catering Equipment & Packaging Tracker
3. **Inventory, Supply & Waste**: Perishable Priority & Waste Reducer; Smart Reorder + Supplier Negotiator; Slow-Mover Liquidation
4. **Kitchen & Order Operations** (Big Gap): Order Accuracy Guardian; Kitchen Load Balancer & ETA; Special Request Memory
5. **Customer Experience & Loyalty** (Big Gap): Loyalty & Punch-Card; Menu Suggestion & Upsell; Referral & Review Responder
6. **Finance & Back-Office**: Expense Bookkeeper; P&L Anomaly Detective; Credit Customer & Temple Account Manager
7. **Multi-Location & Growth**: Multi-Location Coordinator; New Location Feasibility Scout
8. **Marketing & Community**: Local Community Broadcast; Photo Menu Curator; Competitor Price Watcher
9. **Compliance, Equipment & Owner Protection**: Equipment & Maintenance; Food Safety & Compliance Guardian (enhanced); Owner Wellbeing & Burnout Guardian

---

## Mapping new portfolio → current Solid 17 codebase

User's numbering is fresh 1-25 by domain. Our codebase uses historical 1-25 with retired slots and references in SKILL.md / commit messages / audit log type strings (`compliance_reminder_*`, `multi_location_closest_lookup`, etc.). **Decision: keep historical code numbering for shipped agents; new agents get #26+ slots.** The user's domain-grouped 1-25 is a STRATEGIC view; the code-internal numbering stays operational.

### Maps to existing agents (9 of user's 25)

| User # | User name | Maps to | Current state |
|---|---|---|---|
| 1 | Shift Agent + Swap & Coverage | Agent #1 Shift Agent | LIVE (Swap & Coverage are sub-flows already in handle_sick_call SKILL) |
| 2 | Hiring & Onboarding | Agent #12 Hiring | Scaffolded (cfg.hiring.enabled=False) |
| 4 | Catering Lead + Closer | Agent #2 Catering Lead + Agent #10 Catering Followup (combined) | Catering Lead deployed (opt-in); Followup scaffolded |
| 7 | Smart Reorder + Supplier Negotiator | Agent #6 Inventory + Agent #7 Supplier (combined) | Both scaffolded |
| 15 | Expense Bookkeeper | Agent #21 Expense Bookkeeper | Scaffolded; QBO write deferred |
| 16 | P&L Anomaly Detective | Agent #22 P&L Anomaly | Scaffolded (PR #65) |
| 18 | Multi-Location Coordinator | Agent #3 Multi-Location | **LIVE** (PR #62 v0.1; user's "scaffolded" status is stale) |
| 23 | Equipment & Maintenance | Agent #19 Equipment Maintenance | **Scaffolded** (PR #66; user's "Backlog → New" status is stale) |
| 24 | Food Safety & Compliance Guardian | Agent #13 Compliance Calendar (reframed/expanded scope) | **LIVE** (PR #63 v0.1; just expand scope to include Food Safety + ServSafe checklist + temperature logs) |

### Net-new (16 of user's 25)

| User # | User name | Proposed code # | Tier guess | Hermes-first effort |
|---|---|---|---|---|
| 3 | Performance & Training Coach | #26 | Tier-2 | LOW–MEDIUM (skill tracking + WhatsApp quizzes; Hermes substrate sufficient) |
| 5 | Catering Equipment & Packaging Tracker | #27 | Tier-2 | LOW (deposit tracking + return reminders; mirror compliance pattern) |
| 6 | Perishable Priority & Waste Reducer | #28 | Tier-2 | MEDIUM (POS depth needed; same gating as #22) |
| 8 | Slow-Mover Liquidation | #29 | Tier-3 | MEDIUM (POS + sales velocity analysis) |
| 9 | Order Accuracy Guardian (HIGH PRIORITY) | #30 | Tier-1 | MEDIUM-HIGH (KDS or POS order-state integration; same gate as #23) |
| 10 | Kitchen Load Balancer & ETA | #31 | Tier-2 | MEDIUM-HIGH (real-time POS busyness; gated on POS) |
| 11 | Special Request Memory | #32 | Tier-2 | LOW (per-customer state file; CRM-lite) |
| 12 | Loyalty & Punch-Card | #33 | Tier-2 | LOW–MEDIUM (state file + WhatsApp; no external deps) |
| 13 | Menu Suggestion & Upsell | #34 | Tier-3 | MEDIUM (POS history + LLM; gated on POS depth) |
| 14 | Referral & Review Responder | #35 | Tier-2 | MEDIUM (Google/Facebook review APIs — community MCP servers may exist; check `mcp/native-mcp` per skills-roadmap.md) |
| 17 | Credit Customer & Temple Account Manager | #36 | Tier-2 | LOW–MEDIUM (extends Cash & AR; per-account state + monthly reminders) |
| 19 | New Location Feasibility Scout | #37 | Tier-3 | HIGH (demographics + competition data — multiple external APIs) |
| 20 | Local Community Broadcast | #38 | Tier-2 | LOW (opt-in WhatsApp list + cron; matches Daily Brief pattern) |
| 21 | Photo Menu Curator | #39 | Tier-2 | LOW–MEDIUM (image management + LLM caption; reuse parse_catering_inquiry image substrate) |
| 22 | Competitor Price Watcher | #40 | Tier-3 | HIGH (web scraping + per-competitor parsers; per-customer effort) |
| 25 | Owner Wellbeing & Burnout Guardian | #41 | Tier-2 | LOW (folds into Daily Brief weekly section + quiet-hours rule; was retired #20, now revived) |

### Reframings (4)

| Agent | Old shape | New shape |
|---|---|---|
| #13 Compliance Calendar | License/inspection/tax deadlines | **Expanded** to "Food Safety & Compliance Guardian" — adds daily checklist + temperature logs + ServSafe (originally v0.2 deferred per portfolio.md:468) |
| #2 Catering Lead | Inquiry → quote → approval | **Combined** with Agent #10 Catering Followup (deposit + reminder) into single "Catering Lead + Closer" presentation |
| #6 Inventory + #7 Supplier | Two separate agents | **Combined** as "Smart Reorder + Supplier Negotiator" presentation |
| #20 Owner Wellbeing | Retired 2026-04-29 (folded into Daily Brief) | **Revived** as separate agent #41 with explicit weekly summary + quiet-hours rule |

### Retired/absorbed (no longer in user's view)

- **Agent #14 Employee Document Tracker** — not visible in user's portfolio. Likely subsumed under Hiring & Onboarding (#12). Recommend keeping as scaffolded; no action.
- **Agent #15 Cash & AR** — partially overlaps with user's #17 Credit Customer & Temple. Cash & AR is broader (all invoiced customers); Temple Account is a specific subtype. Recommend keeping #15 as parent agent; #17 = #36 specialization.
- **Agent #16 Sales Tax Filing** — not in user's view. Probably absorbed by Compliance Guardian (#24) since sales tax IS a compliance deadline. Recommend keeping #16 scaffolded; document the absorption.

### Status corrections needed

1. **User says "Catering Lead + Closer ✅ Live"** — partially correct: catering subsystem deployed on srilu (catering_dispatcher SKILL + parse_catering_inquiry + handle_catering_owner_approval), but `cfg.catering.enabled` defaults False. Status: **LIVE infrastructure, opt-in per customer**.
2. **User says "Multi-Location Coordinator ✅ Scaffolded"** — STALE. Agent #3 v0.1 went LIVE in PR #62 (2026-05-04 morning); closest-store query for customers is functional. Status: **LIVE v0.1**.
3. **User says "Equipment & Maintenance Agent (Backlog → New)"** — STALE. Agent #19 scaffold shipped in PR #66 (2026-05-04). Status: **Tier-2 scaffold opt-in**.

---

## Domain reorganization

User's 9 domains map cleanly to a portal restructure. Current portal uses Tier 1/2/3 buckets — keep tiers internally but ADD domain field for navigation. Suggested mapping of existing agents:

- **Workforce & Scheduling**: #1, #12, NEW #26
- **Catering & High-Margin Revenue**: #2 (+#10), NEW #27
- **Inventory, Supply & Waste**: #6 (+#7), NEW #28, NEW #29
- **Kitchen & Order Operations**: NEW #30, NEW #31, NEW #32
- **Customer Experience & Loyalty**: NEW #33, NEW #34, NEW #35
- **Finance & Back-Office**: #15, #21, #22, NEW #36
- **Multi-Location & Growth**: #3, NEW #37
- **Marketing & Community**: #11 (Festival), NEW #38, NEW #39, NEW #40
- **Compliance, Equipment & Owner Protection**: #13, #16, #19, NEW #41

That's all 17 + 16 = 33 unique agent slots. Some "Big Gap" domains (Kitchen, Customer Experience) have ZERO existing agents — confirms the user's framing that those are major gaps.

---

## Recommended action this session

1. ✅ **This gap analysis** (saved at `tasks/audits/portfolio-expansion-2026-05-04.md`)
2. **Update `docs/portfolio.md`**: add 16 net-new agent specs (placeholders with the user's bullet-summary as Phase-0 spec); update implementation status table; correct status of #3 / #19 / #13.
3. **Update `MEMORY.md`** + add `memory/project_portfolio_expansion_2026_05_04.md`.
4. **Update `web/portal/index.html`**: add 16 new agent entries (state="future" or "paper-spec"); add domain field for grouping; refresh counters (5 live + 12 scaffolded + 16 new + 5 backlog absorbed-or-still-deferred = ~38 total agent slots).
5. **DO NOT speculatively build any of the 16 new agents.** Per Hermes-first + the overnight closeout doc + tonight's E2E lessons: speculative builds against unverified substrate APIs are exactly the bug class we just hotfixed twice (PR #69 + #70 closest-location.py drift). Each new agent is one Plan→Design→Build cycle with ground-truthing of any external API it depends on.

---

## Build-priority recommendation (for user decision)

Per the user's "Big Gap" annotation, the highest-leverage builds to prioritize next:

1. **#30 Order Accuracy Guardian** (HIGH PRIORITY per user) — gated on KDS/POS order-state pipeline (same blocker as #23 Order Status, deferred indefinitely until customer with that pipeline)
2. **#41 Owner Wellbeing & Burnout Guardian** — pure substrate work; no external blockers; small Daily Brief patch + quiet-hours config flag. Tractable in ~1 day.
3. **#32 Special Request Memory** — pure CRM-lite state file; matches existing patterns. Tractable.
4. **#33 Loyalty & Punch-Card** — WhatsApp + JSON state; no external blockers. Tractable.
5. **#26 Performance & Training Coach** — WhatsApp quiz substrate exists (mirror Hiring agent's onboarding pattern). Tractable.
6. **#38 Local Community Broadcast** — opt-in list + cron, mirror Daily Brief. Tractable.
7. **#36 Credit Customer & Temple Account Manager** — extends existing Cash & AR scaffold. Tractable.
8. **#39 Photo Menu Curator** — reuses parse_catering_inquiry image substrate.
9. **#27 Catering Equipment & Packaging Tracker** — extends existing catering state.

**Deferred (POS-gated)**: #28, #29, #31, #34 (perishable, slow-mover, kitchen ETA, menu upsell — all need POS depth).

**Deferred (external API)**: #35 (Google/Facebook reviews — investigate MCP servers), #37 (demographics + competition), #40 (competitor scraping).

Realistic batch for next overnight: #41 + #32 + #33 (3 agents, all pure substrate, ~1 day each). User decision required to authorize.

---

## Drift-rule self-checks

- ✅ Read `docs/portfolio.md` (entire current portfolio + 2026-05-04 status update lines 884-895) before mapping
- ✅ Read `MEMORY.md` + `memory/project_portfolio_status.md` for current state vocabulary
- ✅ Read `tasks/skills-roadmap.md` for "Top 7 confirmed gaps" (DoorDash/UberEats — relates to user's #35; QBO — relates to #15) before estimating effort per new agent
- ✅ Read `tasks/overnight-2026-05-04-closeout.md` for the build-only-on-customer-demand discipline
- ✅ Read `tasks/audits/e2e-test-2026-05-04.md` for the substrate-API-drift lesson that informs "verify before building" recommendation
