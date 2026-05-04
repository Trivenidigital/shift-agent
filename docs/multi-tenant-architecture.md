**Drift-check tag:** `extends-Hermes` — uses Hermes substrate per-VPS unchanged; adds an operator-VPS coordination layer that consumes per-VPS audit logs and routes cross-location queries. No Hermes convention is fought; the architecture aligns with Hermes' single-tenant design grain.

# Multi-Tenant Architecture — SMB-Agents

**Status:** v1, 2026-05-03 — canonical fleet architecture for the 20+ portfolio at 100-customer scale.
**Supersedes:** Implicit "per-customer VPS" assumption in `docs/portfolio.md` line 5 + `MultiLocationConfig` scaffolding in `schemas.py:800`.
**Audience:** Operators planning Triveni's 9-location bring-up; engineering planning the Q1 fleet platform investment.

## Read-deployed-code commitment

Before drafting, I read:
- `docs/portfolio.md:5` — original "Per-customer Hetzner VPS (~$7/mo) + central operator VPS for fleet management" architecture statement
- `src/platform/schemas.py:776-800` — `LocationEntry` + `MultiLocationConfig` scaffolding (Agent #3 Multi-Location Coordinator)
- `docs/deploy.md` — current per-VPS tarball deploy flow (`build-deploy-tarball.sh` + `shift-agent-deploy.sh` + Hermes pin gate + auto-rollback)
- `tools/build-deploy-tarball.sh` — what's currently in the tarball (`src/` + `tools/` + `.commit-hash`)
- The "Hermes 90% scenario" + watchdog work (overnight 2026-05-01 morning report)

Two things change the implicit "per-customer VPS" architecture into something needing more specification:
1. Each Triveni location has a **distinct WhatsApp number** (not one shared across 9). Baileys is hard-limited to 1 session per number.
2. Pricing is **per-location**, so charging matches the unit of value AND the unit of cost.

This doc captures the architecture once, so future agents/operators don't re-derive it from scratch.

## Hermes-first checklist

| Step | `[Hermes]` / `[net-new]` |
|---|---|
| Per-location WhatsApp inbound, dispatcher, skill-runtime | `[Hermes]` (one Hermes install per VPS, exactly the upstream model) |
| Per-location state files + audit chain | `[Hermes]` (existing `safe_io` + `decisions.log` chokepoint) |
| Per-location ops (deploy, restart, smoke) | `[Hermes]` extended (existing `shift-agent-deploy.sh` per VPS) |
| Cross-location aggregation (consolidated Daily Brief, owner cross-location queries) | `[net-new]` — operator-VPS service that reads per-location `decisions.log` snapshots |
| Fleet ops (deploy commit X to N VPSes with canary + rollback) | `[net-new]` — adopt `mission-control` (3.7k stars, awesome-list production-tag) rather than build from scratch |
| WhatsApp number provisioning + Baileys pairing | `[net-new]` — operator runbook + automation |
| Per-customer billing rollup | `[net-new]` — operator service consuming per-location audit logs |

**Net-new tally:** 4 surfaces (cross-location aggregation, fleet ops, WhatsApp pairing automation, billing rollup). Three of them are operator-VPS services; one is a runbook with templated automation. **Zero changes to per-location-VPS substrate.**

## The architecture

```
                    ┌────────────────────────────┐
                    │     OPERATOR VPS           │
                    │                            │
                    │  ┌──────────────────────┐  │
                    │  │ Mission-control      │  │
                    │  │ (fleet deploy/ops)   │  │
                    │  └──────────────────────┘  │
                    │  ┌──────────────────────┐  │
                    │  │ Per-customer         │  │
                    │  │ aggregator services  │  │
                    │  │ (1 service per       │  │
                    │  │  customer, reads     │  │
                    │  │  N location VPSes)   │  │
                    │  └──────────────────────┘  │
                    │  ┌──────────────────────┐  │
                    │  │ Cross-location skill │  │
                    │  │ evolution (SkillClaw │  │
                    │  │ or hermes-agent-     │  │
                    │  │ self-evolution),     │  │
                    │  │ anonymized inputs    │  │
                    │  └──────────────────────┘  │
                    │  ┌──────────────────────┐  │
                    │  │ Billing + audit      │  │
                    │  │ rollup, per-cust     │  │
                    │  └──────────────────────┘  │
                    └─────────────┬──────────────┘
                                  │
                  read-only audit │ deploy commands
                  log snapshots   │ (mission-control SSH)
                                  │
        ┌─────────────┬───────────┼──────────────┬─────────────┐
        ▼             ▼           ▼              ▼             ▼
   [TX-Plano]   [TX-Frisco]   [MD-Rockville] ... [VA-Tysons]   ← N location VPSes
   ┌─────────┐  ┌─────────┐   ┌─────────┐       ┌─────────┐
   │Hermes   │  │Hermes   │   │Hermes   │       │Hermes   │
   │+shift-  │  │+shift-  │   │+shift-  │       │+shift-  │
   │agent    │  │agent    │   │agent    │       │agent    │
   │1 WA #   │  │1 WA #   │   │1 WA #   │       │1 WA #   │
   │1 owner  │  │1 owner  │   │1 owner  │       │1 owner  │
   │  thread │  │  thread │   │  thread │       │  thread │
   └─────────┘  └─────────┘   └─────────┘       └─────────┘
```

### Per-location VPS — what runs there

Each location-VPS is a **complete, independent Hermes-Agent install** owning ALL agent execution for that location:

| Component | Owner | Notes |
|---|---|---|
| Hermes runtime | `/root/.hermes/` | Per-location auth.json, sessions/, .env. One Baileys session bound to the location's WhatsApp number. |
| `/opt/shift-agent/` | shift-agent user | All schemas, scripts, templates, state, audit log. Same as today's main-vps + srilu-vps layout. |
| Agents enabled (location-aware tier) | Per `config.yaml` | Catering, Shift, Daily Brief, EOD, Expense Bookkeeper (if location does its own books), Inventory, Supplier, Catering Followup, VIP — all per-location |
| Agents NOT here (customer-aware tier) | — | Multi-Location Coordinator (#3), Compliance Calendar (#13), Cash & AR (#15), Sales Tax Filing (#16), Unit Economics (#17), P&L Anomaly (#22) — these run on operator VPS or a designated "primary" location |
| Owner WhatsApp thread | This location's number | Owner sees one chat thread per location. With 9 Triveni locations the owner has 9 chats. Acceptable: each chat is staff-context-specific. |
| State files | `/opt/shift-agent/state/` | Per-location: roster.json (this location's staff), catering-leads.json (this location's leads), expense-bookkeeper/ (this location's receipts if enabled here), pending.json |
| Audit log | `/opt/shift-agent/logs/decisions.log` | Append-only, per-location. Operator VPS reads snapshots for aggregation. |

**Location-VPS specs:** Hetzner CCX13 (4 vCPU shared, 16GB RAM, 80GB disk, ~$7-9/mo) is the working baseline per main-vps + srilu-vps experience. Memory headroom matters — Hermes + bridge.js + cockpit + 4-8 watchdog daemons sit at ~250MB resident.

### Operator VPS — what runs there

The operator VPS is the **fleet's central nervous system**. It does NOT execute customer agent flows; it **orchestrates and aggregates**:

| Component | Purpose | Inputs | Outputs |
|---|---|---|---|
| `mission-control` | Fleet deploy + canary + rollback | Tarball + target list | Per-VPS deploy status |
| Per-customer aggregator (1 instance per customer) | Reads N location VPSes' `decisions.log` snapshots; produces consolidated Daily Brief, multi-location summaries, owner cross-location queries (Agent #6) | SSH read of per-VPS audit logs | Owner-facing aggregate WhatsApp messages (sent via... this is open — see §Open Questions) |
| Cross-customer skill evolution | Runs DSPy+GEPA or SkillClaw on **anonymized** session data; produces SKILL.md PRs to the central repo | Anonymized session traces from all customer VPSes (opt-in per customer) | Pull requests to `Trivenidigital/shift-agent` |
| Billing + usage rollup | Per-customer monthly usage report | Per-VPS audit log + send-counter.json | Stripe invoicing input |
| Customer-onboarding tooling | Provision + bootstrap + pair new location-VPS | Customer + location metadata | New running VPS in fleet |
| Fleet observability dashboard | At-a-glance fleet health | Per-VPS health endpoints + audit-log tail | Dashboard UI |

**Operator-VPS specs:** Larger. CCX23 or CCX33 (~$25-50/mo) — needs to hold mission-control DB + per-customer aggregator processes + skill-evolution sandboxes. Single point of failure for fleet ops; should have a runbook for re-bootstrap on a fresh box.

**Operator-VPS does NOT proxy customer messages.** WhatsApp inbound for a Triveni-Plano location goes directly from WhatsApp → that location's VPS bridge. Operator VPS is read-mostly: it pulls audit-log snapshots, doesn't intercept the message path.

### Where the boundary sits

The architecture's load-bearing rule:

> **Per-location VPS owns execution. Operator VPS owns orchestration + aggregation.**

Concrete consequences:

- **Customer-facing message flow** never touches operator VPS. If operator VPS is down, agents still serve customers correctly per location.
- **Aggregation is best-effort, eventually-consistent.** Owner's consolidated Daily Brief is "all 9 locations as of last poll" — not synchronously gathered.
- **Cross-location queries** ("who's at Houston tomorrow?") run on operator VPS by reading rosters from each location-VPS read-only.
- **Skill evolution** runs on operator VPS; outputs PRs to repo; deployed to all location-VPSes via mission-control. **Never silently mutates a customer's running SKILL files.**
- **Billing** is computed from per-location audit logs. The operator VPS owns the source of truth for "how much each customer used this month."

### What stays in `docs/portfolio.md` agent definitions

The Solid 17 agent inventory in `portfolio.md` doesn't change. What changes is **which VPS each agent runs on**:

| Agent | Default VPS | Notes |
|---|---|---|
| #1 Shift | per-location | Each location has its own roster/schedule |
| #2 Catering Lead | per-location | Each location has its own menu + leads |
| #3 Multi-Location Coordinator | **operator** | By definition cross-location |
| #4 Daily Brief | per-location AND **operator** (consolidated rollup) | Per-location brief sent to that location's owner thread; operator generates the cross-location summary |
| #5 EOD Reconciliation | per-location | Each location has its own POS reconcile |
| #6 Inventory Tracker | per-location | Stock counts are physical-location-bound |
| #7 Supplier Coordination | per-location | Supplier roster + price sheets are location-specific |
| #9 VIP Customer | per-location | Customer interaction history is per-location |
| #10 Catering Followup | per-location | Tied to per-location catering leads |
| #11 Festival & Event Outreach | **operator** + per-location | Festival calendar is regional (operator), prep tasks are location-specific. **Custom-build gap per skills-roadmap** — no Hindu/regional calendar skill exists |
| #12 Hiring & Onboarding | **operator** OR per-location (customer choice) | Pipeline is usually customer-wide; some prefer per-location. **e-sign step is custom-build gap** |
| #13 Compliance Calendar | **operator** | Permits, insurance, multi-state filings are customer-wide |
| #14 Employee Doc Tracker | per-location | Staff documents are location-bound |
| #15 Cash & AR | **operator** | Multi-location cash flow rolled up. **Custom-build gap** — no Stripe/Square skill in any source; investigate community MCP server |
| #16 Sales Tax Filing | **operator** | Multi-state filings (Triveni: 6 states) — definitionally cross-location. **Custom-build gap** — no state tax skills anywhere |
| #17 Unit Economics | **operator** | Customer-wide P&L modeling and unit-cost analysis |
| #20 Owner Wellbeing | per-location | Owner interaction tone tracked per location-thread |
| #21 Expense Bookkeeper | **operator** OR per-location | Per-location receipts; consolidated QBO push from operator (TBD per customer's books layout). **Custom-build gap** — no QBO skill anywhere; investigate community MCP server |
| #22 P&L Anomaly Detective | **operator** | POS data flows up from locations, anomalies detected on rollup |

This matrix gets re-confirmed per customer at onboarding (different customers have different ops models).

**Per `tasks/skills-roadmap.md` (PR #54)**: 5 of these agents have install-now Hermes ecosystem coverage that shrinks their LOC budget — see roadmap for per-agent skill mapping. 7 have confirmed custom-build gaps (no skill exists in any of 4 audited sources): #11, #12-esign, #15, #16, #19, #21, #23/#25. These gaps are per-location for #11/#12/#19/#21, operator-VPS for #15/#16, and backlog for #23/#25.

## Pricing model alignment

The architecture supports per-location billing naturally:

- **Each location-VPS is an SKU.** Customer sees "Triveni-Plano Catering+Shift agent: $X/mo." Bill per location.
- **Operator-side agents (#3, #13, #15, #16, #22)** are billed once per customer — they're not location-multiplexed.
- **Daily Brief consolidation (#4 operator-side)** is a thin add-on or bundled with multi-location.
- **Skill evolution** is operator-side overhead, included in any tier (cost is shared across customers, ~$2-10/run × ~70 SKILLs/month).

Pricing levers if needed later:
- Tier by agent count (3-agent / 5-agent / "all 17") at the location level
- Tier by location count (1 location free / 2-5 standard / 6+ enterprise)
- Add-ons: cross-location coordinator, multi-state sales tax, cross-customer skill evolution opt-in

## "Merge to common agent later" — the path

The architecture makes merging into a common agent a **software-only** change, not a data migration:

- **Today:** per-location-VPS execution; per-customer aggregator on operator VPS.
- **Later (if a customer wants it):** move agent execution from per-location VPSes onto the operator-side per-customer aggregator. Per-location VPSes shrink to bridge-only (WhatsApp number termination + outbound), or are decommissioned entirely with WhatsApp numbers re-pointed to one shared bridge running multiple Baileys sessions.
- **The data substrate is already cross-location-aware** because the operator VPS already aggregates. No data migration needed; just relocate compute.

Realistically: most customers will NOT want this. Per-location WhatsApp numbers are how staff ALREADY communicate — staff-vs-bot UX is identical. Owners getting per-location threads is a feature, not a bug.

## Risks (architecture-level)

### High — WhatsApp at scale

At 100 customers × 3 locations avg = **300 Baileys sessions** running across the fleet. Baileys is unofficial; WhatsApp's anti-bot enforcement is real:

- Per-number bans (single-customer outage)
- IP-range bans on Hetzner datacenter ranges (fleet outage)
- Pattern-detection bans (catastrophic — looks like a bot fleet)

**Mitigations to plan in parallel with the fleet build, not after:**

1. **WhatsApp Business API path** — official, Meta-blessed, $$/template-message. Switch outbound owner-cards (templated) to Business API; keep Baileys for inbound + free-form responses.
2. **Customer-provided phone numbers (BYO-phone)** — customer holds the WhatsApp account; we connect via their session. Pushes ban risk to customer.
3. **IP diversity** — spread VPSes across Hetzner Hel1 + Falkenstein + Nuremberg + AWS + DigitalOcean. Looks less bot-fleet-like.
4. **Per-number warmup** — slow message-volume ramp on new numbers (first week: receive only; week 2: small outbound; week 3+: normal).
5. **Catch-and-retire** — operator-side detector for "number X is silent for >24h" → auto-page operator → human investigates whether banned.

This risk is **bigger than the VPS architecture choice** and warrants its own work stream.

### Medium — Operator-VPS as single point of failure

If operator VPS dies:
- Customer agents keep running ✓
- Consolidated Daily Brief stops ✗
- Cross-location queries return error ✗
- Fleet deploys can't run ✗
- Skill evolution stops ✗
- Billing rollup pauses ✗

**Mitigation:** the operator VPS state should be entirely re-bootstrappable from (a) repo (mission-control config in version control, per-customer aggregator code in repo), (b) per-VPS audit logs (regenerable rollups), (c) snapshot of mission-control DB. Target: operator VPS recovery in <2h on a fresh box.

### Medium — Mission-control adoption risk

Adopting `mission-control` (3.7k stars, builderz-labs) ties our fleet ops to an external project. Risks:
- Project goes unmaintained → we own a fork
- Breaking changes → migration cost across customers
- Security advisory → fleet-wide patch urgency

**Mitigation:** evaluate the project's bus factor + release cadence before adopting; have a "build minimal in-house alternative" backup plan. The minimal alternative is ~500 LOC of bash + Hetzner CLI + parallel SSH; not a hard fall-back.

### Low — Per-location VPS deploy storm

300 deploys per release. Hetzner API or our deploy automation could rate-limit. Mission-control handles this with canary + rolling deploy. If we hand-roll, we need similar pacing.

## Open questions

1. **Where do operator-aggregator outbound messages send from?** Owner sees per-location threads on N WhatsApp numbers. Consolidated Daily Brief lands... where? Options: (a) one location's number (designated "primary"), (b) a customer-level operator number (BYO-WhatsApp-Business-account), (c) push notification (Pushover, email) instead of WhatsApp for cross-location summaries. Worth a separate decision per customer.
2. **Which agents run operator-side at customer #1 (Triveni)?** The matrix above is a starting point but needs Triveni-specific confirmation before bring-up.
3. **Skill-evolution opt-in default?** Per CLAUDE.md customer-VPS isolation, cross-customer skill learning needs explicit opt-in with anonymization. Default = OFF, surface as feature in pricing.
4. **Mission-control adoption decision** is its own evaluation track — out of scope for this doc.
5. **Operator-VPS HA?** Single-VPS with documented re-bootstrap is the v1; later v2 considers active-passive pair if operator-side downtime becomes painful.
6. **MCP server placement — operator-shared vs per-VPS?** Per `tasks/skills-roadmap.md`, several confirmed gaps (#15 Stripe write, #21 QBO write, #12 e-sign) will likely route through community MCP servers via `mcp/native-mcp` rather than Hermes-native skills. Two options: (a) install MCP servers per-location-VPS (each catering/expense agent talks to its own QBO/Stripe MCP — simple but N tokens to manage), (b) host MCP servers on operator-VPS as shared bridges (one OAuth, but adds operator-side dep + breaks the "operator dies → agents still serve customers" rule for write-side flows). Default = (a) per-VPS until the OAuth-token-sprawl pain becomes real; revisit at customer #5+.

---

*Companion docs: `docs/fleet-provisioning.md` for the per-location VPS provisioning lifecycle. `tasks/skills-roadmap.md` for per-agent Hermes ecosystem coverage (verified 2026-05-03; <1% SMB integration coverage; 5 install-now skills cover 6 of 17 agents; 7 confirmed custom-build gaps).*
