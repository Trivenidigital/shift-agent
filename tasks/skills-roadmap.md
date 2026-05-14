# Skills Roadmap — Hermes Ecosystem ↔ Solid 17 Portfolio

**Drift-check tag:** `Hermes-native` (pure roadmap document; no code; leverages existing Hermes substrate documentation).

**Date:** 2026-05-03
**Researcher:** sub-agent dispatched against 4 sources (official hub, awesome-hermes-agent, HermesHub, awesome-openclaw-skills)
**Hermes-first context:** This roadmap operationalizes CLAUDE.md's binding "check Hermes capabilities BEFORE writing code" rule by enumerating which existing skills already cover which agents in the Solid 17 portfolio — so future per-agent build effort estimates are grounded in actual ecosystem coverage, not assumption.

---

## TL;DR for the impatient

1. **The "671 Hermes skills" headline is misleading for SMB use cases** — fewer than 1% are SMB business integrations. Ecosystem skews heavily to dev tools, AI/ML ops, creative, and crypto.
2. **5 productivity skills cover 6 of our 17 prioritized agents** at near-zero effort. Install proactively.
3. **Hermes-first principle holds for ingestion (in-side); write-side commercial APIs still require credentials, but the connector market moved.** QBO, Stripe, Square, PayPal, Airtable, Notion, and DocuSign now have credible MCP/vendor connector candidates. DoorDash/UberEats/Grubhub and tax filing remain connected/custom surfaces.
4. **MCP is the strategic escape hatch** for missing integrations — `mcp/native-mcp` bridges to external MCP servers. Default posture as of 2026-05-14: vendor MCP or vetted MCP first, custom raw API only after connector review fails.
5. **Existing Stage 1 estimate for #21 Expense Bookkeeper must be re-scoped** — Hermes still does not ship a QBO business skill, but the Intuit QuickBooks Online MCP server should be reviewed before raw custom QBO API work.

---

## Sources surveyed

| Source | Status | Relevant skill count | Notes |
|---|---|---|---|
| Official Hermes Skills Hub (`hermes-agent.nousresearch.com/docs/skills`) | Verified live | ~60 bundled + community submissions claimed at 671 (mostly inaccessible per-category) | Authoritative for what ships in-box |
| Bundled catalog (raw): `NousResearch/hermes-agent/website/docs/reference/skills-catalog.md` | Verified raw via `gh api` | ~60 across 19 categories | The actual deployable list |
| Awesome-Hermes-Agent (`0xNyk/awesome-hermes-agent`) | Verified raw README, 2.3k stars | ~80 community projects | Mostly dev/AI infra; **zero SMB integrations** |
| HermesHub (`amanning3390/hermeshub`, hermeshub.xyz) | Verified raw README | 22 verified skills | Security-scanned registry; ZERO accounting/payments/POS |
| Awesome-OpenClaw-Skills (`VoltAgent/awesome-openclaw-skills`) | Verified, all 30 categories | 5,400+ skills (auto-migratable to Hermes per upstream) | Has the only relevant SMB hits — exhaustively searched |
| skilldock.io (`chigwell/skilldock.io`) | Verified README; site JS-only | Marketplace SDK only | Catalog requires login; not actionable |
| `wondelai/skills` | Verified | ~40 book-based knowledge frameworks | Not integrations |
| `agentcash-skills` (Merit-Systems) | Verified | 12 wrappers via x402 gateway | Relevant: `email`, `phone-calls`, `local-search`, `data-enrichment` |

---

## Top 5 install-now wins

These cover **6 of 17 prioritized agents** at near-zero setup cost. Each saves substantial scaffold LOC by replacing what would otherwise be hand-rolled API code.

| Rank | Skill | Source | Agents served | Est. LOC saved | Setup effort |
|---|---|---|---|---|---|
| 1 | `productivity/google-workspace` | Official bundled | #1, #4, #10, #12, #13, #14 | ~500 across 6 agents | One-time Google Cloud OAuth (5 steps) |
| 2 | `productivity/maps` | Official bundled | #3, future routing | ~150 | Zero auth, zero deps (OSRM + Nominatim, free, 1 req/s) |
| 3 | `productivity/airtable` | Official bundled | #6, #7, #17, #22 | ~250 | One Airtable PAT |
| 4 | `productivity/ocr-and-documents` | Official bundled | #6, #7, #14, #21, #8 | ~200 (PDF path; complements Hermes vision for image path) | pymupdf instant; marker-pdf needs ~5GB models |
| 5 | `productivity/notion` | Official bundled | #6, #12 (Airtable alt) | ~100 | One Notion integration token |
| **Bonus** | `mcp/native-mcp` | Official bundled | Generic — bridges to any MCP server (8,600+ exist) | Variable; opens entire MCP ecosystem incl. likely QBO/Stripe paths | Per-server config |

**Total estimated savings: ~1,200 LOC across 6 agents from 5 skills.**

---

## Per-agent mapping

### Tier 1 — LIVE / must-build (6)

| Agent | Hermes skill | Source | Coverage | Replaces custom code? | Notes |
|---|---|---|---|---|---|
| **#1 Shift Agent** (LIVE) | `productivity/google-workspace` (Calendar, Sheets) | Official | Calendar reads for shift schedule; Sheets for roster | PARTIAL (~50 LOC if owner uses Sheets) | Already shipped on JSON+WhatsApp; google-workspace is additive only for Sheets-based rosters |
| **#1 Shift Agent** | `email/himalaya` | Official | IMAP/SMTP fallback if WhatsApp degraded | NO — adds capability only | Useful as backup channel |
| **#2 Catering Lead** (LIVE) | (none found) | — | — | NO | Hermes vision + WhatsApp media + LLM gateway already cover the full inquiry-to-quote loop |
| **#4 Daily Brief** (LIVE) | `productivity/google-workspace` (Gmail) | Official | OAuth send/read with HTML, threading, labels | PARTIAL (~80 LOC for email channel fallback) | Currently sends WhatsApp; google-workspace adds email backup |
| **#4 Daily Brief** | `email/himalaya` | Official | IMAP/SMTP, lighter than google-workspace | PARTIAL alternative | Works with any IMAP/SMTP, no OAuth |
| **#5 EOD Reconciliation** (LIVE) | `clovercli` (OpenClaw) | OpenClaw | Clover POS API — inventory, orders, payments, employees, discounts, analytics | PARTIAL (~150 LOC) — only if customer uses Clover | Verify migration cleanness; conditional on POS choice. **Action**: confirm Triveni's POS system. |
| **#21 Expense Bookkeeper** | Intuit QuickBooks Online MCP candidate + `mcp/native-mcp`; `productivity/ocr-and-documents` for receipt/PDF intake | Vendor MCP + official Hermes | QBO write/read candidate plus Hermes OCR substrate | **CONNECTED — review MCP before custom raw API** | The old `bookkeeper` meta-skill remains a trap (Xero, paid deps, suspicious flag), but Intuit's QBO MCP changes the build order. |
| **#21 Expense Bookkeeper** | `productivity/ocr-and-documents` (marker-pdf) | Official | PDF receipt extraction, 90+ languages, table support | PARTIAL (~50 LOC saved; complements Hermes vision for non-image PDF receipts) | Hermes vision already handles image receipts |
| **#21 Expense Bookkeeper** | `documents-ai` (Veryfi, OpenClaw) | OpenClaw | Real-time receipt OCR + structured extraction (commercial) | PARTIAL — paid alternative | Only if Hermes vision accuracy proves insufficient at scale |
| **#22 P&L Anomaly Detective** | `data-science/jupyter-live-kernel` | Official | Iterative Python via live Jupyter kernel for variance analysis | PARTIAL — useful for development/exploration, not runtime detection | Runtime variance detection is bespoke statistics |
| **#22 P&L Anomaly Detective** | `productivity/airtable` | Official | Read historical P&L data | PARTIAL — only if customer stores P&L in Airtable | More likely reads from QBO once #21 ships |

### Tier 2 — opt-in scaffolds (11)

| Agent | Hermes skill | Source | Coverage | Replaces custom code? | Notes |
|---|---|---|---|---|---|
| **#3 Multi-Location Coordinator** | `productivity/maps` | Official | Geocode 9 Triveni locations, distance/time matrix, OSRM routing, free | YES (~100 LOC) | Zero API keys; 1 req/s Nominatim cap auto-enforced. Use cases: closest-location lookup, driver routing |
| **#6 Inventory Tracker** | `productivity/airtable`, `productivity/notion`, `productivity/ocr-and-documents` | Official | Airtable/Notion CRUD for SKU lists; OCR for supplier price sheets | YES (~200 LOC) | Hermes vision already extracts price sheets; OCR skill adds non-image PDF path |
| **#7 Supplier Coordination** | `productivity/airtable`, `productivity/google-workspace` (Sheets), `productivity/ocr-and-documents` | Official | Airtable + Gmail thread mgmt for supplier comms | YES (~150 LOC) | |
| **#9 VIP Customer Agent** | (none found for sentiment) | — | — | NO — sentiment via Hermes LLM gateway prompt is sufficient | OpenClaw `sentiment-priority-scorer` is real-estate-specific, too narrow |
| **#10 Catering Follow-up** | `productivity/google-workspace` (Gmail), `email/himalaya` | Official | Email follow-up template send | PARTIAL (~50 LOC if email channel needed alongside WhatsApp) | WhatsApp-first is the deployed pattern |
| **#11 Festival & Event Outreach** | (none found) | — | — | **GAP — must build custom** | NO Hindu/regional/Diwali/festival calendar skill exists in any source. Only `islamic-skills` (prayer times). Hardcode or use external API like Calendarific |
| **#12 Hiring & Onboarding** | `productivity/google-workspace` (Drive, Sheets, Docs) + DocuSign MCP candidate | Official + vendor MCP | Resume storage in Drive, applicant tracker in Sheets, e-sign candidate | PARTIAL | E-sign is still connected-mode and approval-gated; review DocuSign MCP before raw custom e-sign work. |
| **#13 Compliance Calendar** | `productivity/google-workspace` (Calendar) | Official | Recurring calendar events for license renewals, health inspections, sales tax filings | YES (~120 LOC) | Combined with Hermes cron, gives full reminder loop natively |
| **#14 Employee Document Tracker** | `productivity/google-workspace` (Drive), `productivity/ocr-and-documents` | Official | Drive storage + OCR for I-9, W-4 expiry extraction | YES (~150 LOC) | Drive search by MIME type + folders for per-employee filing |
| **#15 Cash & AR Agent** | Stripe MCP, Square MCP, PayPal MCP candidates via `mcp/native-mcp` | Vendor MCP | Payment/reconciliation candidates | **CONNECTED — MCP/vendor connector first** | Venmo/Zelle/Cash App/Razorpay remain rail-specific; money-moving actions require owner approval and scoped credentials. |
| **#16 Sales Tax Filing** | (none found) | — | — | **GAP — must build custom** | No state/county tax-filing skill exists anywhere. Custom build with state-specific APIs (TX Comptroller, NC DOR, etc.) required |
| **#17 Unit Economics** | `productivity/airtable`, `data-science/jupyter-live-kernel` | Official | Airtable for cost data; Jupyter for ad-hoc P&L analysis | PARTIAL (~100 LOC) | Better candidate: leverage existing platform schemas + simple aggregations |
| **#20 Owner Wellbeing** | (none found) | — | — | NO — sentiment + wellbeing checks via Hermes LLM gateway prompt sufficient | No wellness skill in any catalog |

### Backlog (5)

| Agent | Skill found | Notes |
|---|---|---|
| **#8 Receiving & QA** | `productivity/ocr-and-documents` | PDF supplier-invoice receipt parsing |
| **#19 Equipment Maintenance** | `farmos-equipment` (OpenClaw) | Farm-equipment-specific; **minimal fit** for restaurant equipment (Hobart, True, Manitowoc all need custom) |
| **#23 Order Status** | (none) | Bespoke — needs POS integration |
| **#24 Upsell** | (none) | Bespoke prompt engineering |
| **#25 Third-Party Delivery** | (none) | DoorDash/UberEats/GrubHub all require custom OAuth + webhook code; no Hermes skill exists |

---

## Confirmed gaps — agents that MUST build custom

These agents have **zero suitable Hermes/OpenClaw/community skill** in any of the 4 sources surveyed. Future Hermes-first checklists for these agents can skip the research step and proceed directly to net-new estimate:

| Agent | Gap | Custom-LOC estimate (rough) |
|---|---|---|
| **#11 Festival & Event Outreach** | No Hindu/Indian regional festival calendar skill | ~150 LOC (hardcoded calendar JSON OR Calendarific API) |
| **#15 Cash & AR Agent** | Stripe/Square/PayPal have credible MCP/vendor candidates; Venmo/Zelle/Cash App/Razorpay remain rail-specific | Review MCP/vendor connector first; custom only after connector review fails |
| **#16 Sales Tax Filing** | No state/county tax skills anywhere | ~500 LOC per state (custom scraping or commercial API) |
| **#21 Expense Bookkeeper** | Intuit QuickBooks Online MCP candidate now exists | Review Intuit MCP + approval guardrails before custom QBO OAuth/write API |
| **#23 Order Status / #25 Third-Party Delivery** | No DoorDash/UberEats/GrubHub skills | ~300 LOC each, OAuth + webhook |
| **#19 Equipment Maintenance** | Only farm-equipment skill exists; no restaurant equipment vendor APIs | Custom per-vendor (Hobart/True/Manitowoc), low priority |
| **#12 Hiring & Onboarding e-sign step** | DocuSign MCP candidate now exists | Review DocuSign MCP before raw custom e-sign integration |

**Total custom LOC for confirmed gaps: ~2,300+ across 7 agent-features.** This is genuine net-new engineering that no skill ecosystem coverage can shrink.

---

## Trap skills to AVOID

| Skill | Why avoid |
|---|---|
| `bookkeeper` meta-skill (h4gen, OpenClaw) | Name suggests Agent #21 solution, but: (a) writes to **Xero** (not QBO), (b) requires paid Maton API + DeepRead OCR ($), (c) **VirusTotal flagged "Suspicious"** |
| `sentiment-priority-scorer` (OpenClaw) | Real-estate-domain-specific; misleading for #9/#20. Use Hermes LLM gateway prompt instead |
| `cognify-skills` (referenced as "19 business ops skills") | **Repo returns 404** — does not exist. Don't waste time searching |
| `farmos-equipment` for #19 | Farm-equipment-specific; unlikely fit for restaurant equipment without significant adaptation |

---

## Strategic recommendations

### This week (immediate)
1. **Install the 5 productivity skills on srilu**: `google-workspace`, `maps`, `airtable`, `ocr-and-documents`, `notion`. Zero-cost; unblocks #6, #7, #12, #13, #14 scaffolding.
2. **Confirm Triveni's POS system**. If Clover, install `clovercli` for ~150 LOC saved on #5 EOD Reconciliation. If anything else, add to gap list.
3. **Update CLAUDE.md Hermes-first section** to mention the 5 productivity skills + MCP escape hatch — so future per-step `[Hermes]/[net-new]` checklists check those FIRST.

### Before #21 Expense Bookkeeper build
4. **Review Intuit QBO MCP before #21 writeback.** Install/use `mcp/native-mcp` only after source/scopes are reviewed; owner approval remains mandatory before writes.
5. **Same for #15 Cash & AR.** Stripe, Square, and PayPal have credible vendor MCP candidates; payment rails remain connected-mode with strict owner approval and audit.

### Process
6. **Document gaps in `docs/portfolio.md`** per-agent (this PR does that).
7. **Update memory** with the "<1% SMB coverage" insight + MCP escape hatch + trap-skill list (this PR does that).
8. **Skip the awesome lists for SMB-vertical research** — zero return on time invested. Stick with the official hub + OpenClaw migration path + targeted MCP-server search.

---

## Honest meta-findings

1. **The Hermes-first principle still holds** — but with refinement:
   - **In-side / ingestion** (sources, vision, structured output, audit, approvals): Hermes substrate covers ~95%. Default to using it.
   - **Out-side / write to commercial APIs** (QBO, Stripe, Square, PayPal, DocuSign, payroll, tax filings): ecosystem coverage is now connector-dependent. Default to vendor MCP or vetted MCP first; expect custom work only when connector review fails or when the target system has no credible connector.

2. **The Stage 1 estimate for Agent #21 (~400 LOC for QBO write) is now stale.** Hermes still covers the in-side fully, but Intuit QuickBooks Online MCP means the out-side must be re-estimated after connector review.

3. **Quick wins are real**: installing the 5 productivity skills covers substrate work for 6 of 17 prioritized agents at near-zero effort and saves ~1,200 LOC. Should be installed proactively before scaffolding #6, #7, #12, #13, #14.

4. **MCP is the strategic answer for missing integrations**. Rather than waiting for Hermes-native QBO/Stripe/Square/PayPal/DocuSign skills, review vendor/vetted MCP servers through `mcp/native-mcp`. This is likely the lowest-effort path to filling the SMB integration gap going forward, but it does not remove OAuth/API credentials.

5. **The awesome-list ecosystem (2.3k stars on `0xNyk/awesome-hermes-agent`) has zero SMB coverage**. Future research time is better spent on the official hub + targeted MCP search than browsing community lists.

---

## Files this PR touches

- `tasks/skills-roadmap.md` — this document (new)
- `docs/portfolio.md` — annotates "Hermes skill availability" line per agent (sections 1-25)
- `CLAUDE.md` — Hermes-first section refined to mention 5 productivity skills + MCP escape hatch
- Memory: `feedback_hermes_skills_landscape.md` (new) + `MEMORY.md` index update

---

## Sources

- [Hermes Agent Skills Hub](https://hermes-agent.nousresearch.com/docs/skills) — official; 671 skills claimed
- [Hermes Bundled Skills Catalog](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/skills-catalog.md) — ~60 in-box
- [Awesome-Hermes-Agent](https://github.com/0xNyk/awesome-hermes-agent) — 2.3k stars, weak SMB coverage
- [HermesHub registry](https://github.com/amanning3390/hermeshub) — 22 verified, no SMB
- [HermesHub site](https://www.hermeshub.xyz/) — security-scanned
- [Awesome-OpenClaw-Skills](https://github.com/VoltAgent/awesome-openclaw-skills) — 5,400+ skills, OpenClaw → Hermes auto-migrate
- [Bookkeeper meta-skill detail](https://clawskills.sh/skills/h4gen-bookkeeper) — VirusTotal "Suspicious"-flagged
- [agentcash-skills](https://github.com/Merit-Systems/agentcash-skills) — x402 gateway wrappers
