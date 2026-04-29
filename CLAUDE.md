# CLAUDE.md — SMB-Agents Project

## ⚠️ CRITICAL RULE — Check Hermes capabilities BEFORE writing code

**Hermes owns the substrate.** Before designing any agent, skill, spec, or implementation plan, enumerate what Hermes already does for the task. Only write custom code for what Hermes provably cannot do.

### How to apply (mandatory checklist before any code/spec)

1. **List every step** the agent / feature takes (e.g. "receive image → extract → classify → respond → push to QBO")
2. **For each step**, ask: *"Does Hermes already do this for Catering, Shift, or Daily Brief?"*
3. **Mark each step** as `[Hermes]` or `[net-new]`
4. **Effort estimate = net-new steps only.** Steps marked `[Hermes]` cost ~zero engineering — skill scaffold + config wiring at most
5. **Red flag**: if the spec marks most steps `[net-new]`, you almost certainly missed a Hermes capability — go re-check before continuing

### What Hermes natively handles (verified in production for Catering Agent as of 2026-04-29)

- **Source ingestion across formats:** image (JPEG/PNG), PDF, Excel/CSV, Word, plain text
- **Source origins:** WhatsApp inbound media, mounted filesystems, Google Drive (point-to-folder), URLs
- **Vision extraction:** complex layouts, multi-column, low-quality scans, multi-language
- **Structured output:** JSON-schema-conformant extraction
- **Skill chaining:** extract → classify → respond, with audit at each step
- **Approval workflows:** `#XXXXX` 5-char codes, 4h proposal TTL, dead-man escalation
- **Identity + role gating:** `sender_role` check (owner / staff / customer)
- **Audit chain:** `decisions.log` discriminated-union entries
- **Multi-channel response:** WhatsApp text/image/document, Telegram, email
- **Skill dispatch:** routing by `sender_role` + `media_type` + content
- **Per-VPS state:** JSON / SQLite + encrypted backups
- **LLM gateway:** text + vision, swappable provider

**Canonical reference (the test that proves the loop):** Owner sends menu image to their WhatsApp → Hermes extracts → structured menu created → customer-facing reply with menu items. End-to-end, all inside Hermes. This is the template for "image/document-in → structured-out → response-out" — works for receipts, invoices, supplier price sheets, with only the schema swapping.

### What is genuine net-new engineering (NOT Hermes substrate)

- **External write APIs:** QuickBooks OAuth + write scope, Stripe charges, e-sign services, calendar invites, etc. Hermes consumes externals; writing to them is per-agent work
- **Money-moving UX discipline:** code+amount approval format, perceptual-hash dedup, per-amount cockpit-vs-WhatsApp thresholds, reversibility windows
- **Per-customer business logic:** chart-of-accounts mapping, supplier roster matching, festival-calendar regional variants, etc.
- **Specialised classifiers** beyond what a prompt-engineered LLM call can do
- **Cross-agent coordination logic:** state-machine handoffs between agents (rare; usually a skill chain handles this)

### Why this rule exists

The Stage 1 decision doc for Expense Bookkeeper (drafted before this rule landed) described 4 "architectural surfaces" as if greenfield infrastructure. Reality: Hermes already handled vision extraction, WhatsApp media routing, structured output, audit chain, approval codes, skill chaining. The genuinely net-new surfaces shrank from 4 to 1.5 (QBO write API + money-moving discipline) once Hermes was credited honestly. This rule prevents the same failure mode from repeating.

---

## Project context

- **Project:** SMB-Agents — autonomous AI agents for ethnic SMBs (restaurants, groceries, food courts, catering)
- **Architecture:** Per-customer Hetzner VPS (~$7/mo) + central operator VPS for fleet management
- **Stack:** Hermes Agent (skills + gateway + delegation) + per-customer JSON/SQLite data layer + WhatsApp/Telegram messaging
- **Reference customer:** Triveni Supermarket — 9 locations across TX/MD/NC/SC/OH/VA
- **Portfolio:** Solid 17 (consolidated 2026-04-29) — see `docs/portfolio.md`

## Key paths

- **Portfolio master spec:** `docs/portfolio.md`
- **Agent code:** `src/agents/<agent>/skills/<skill>/SKILL.md`
- **Platform schemas (Pydantic config):** `src/platform/schemas.py`
- **Tests:** `tests/`
- **Portal source:** `web/portal/index.html`
- **Portal live:** `http://46.62.206.192:8080/portal/` (served from `/var/www/triveni/portal/` on VPS)
- **Consolidation plan (active):** `tasks/solid17-consolidation-plan.md`

## Workflow reminders

- **Plan-first:** non-trivial work → write `tasks/<feature>-plan.md`, get user approval before any code
- **Memory:** check `C:\Users\srini\.claude\projects\C--projects-SME-Agents\memory\` at session start
- **Commits:** never auto-commit; wait for explicit user request
- **SSH from Windows:** two-step pattern (`ssh ... > file 2>&1` then `Read` the file); never inline-capture SSH stdout — it always returns empty
- **Tarball deploy:** no git checkout on VPS; build artifact + `scp` + restart

## Active scope this session

- **Solid 17 consolidation:** documented in `docs/portfolio.md` and live at portal
- **Pending user go:** schema scaffolds for Agent #21 Expense Bookkeeper and Agent #22 P&L Anomaly Detective (see `tasks/solid17-consolidation-plan.md` Phase 2/3)
- **Stage 1 decision doc** for #21 Expense Bookkeeper: under review; gating investigations have shrunk from 3 to 2 (OCR fully removed after 2026-04-29 menu E2E test)
