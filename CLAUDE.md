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

## ⚠️ DRIFT RULES — Read deployed code BEFORE proposing

Authoritative source: `docs/hermes-alignment.md` (Parts 1 and 3 are binding). This section summarises; the doc is canonical.

### The rule (Part 3 working agreement)

**Before proposing schema, test, or architecture work, READ the relevant deployed code first.**

| Work type | Read first (mandatory before drafting) |
|---|---|
| Schema work | `src/platform/schemas.py` — grep for the relevant model first |
| Test work | 1–2 existing test files (e.g. `tests/test_catering_v02_scripts.py`, `tests/test_catering_b1_cases.py`) |
| Routing / dispatcher work | `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` + at least one handler SKILL |
| New script proposal | grep `src/platform/scripts/` + `src/agents/*/scripts/` for the closest similar |
| Audit-log entries | `LogEntry` discriminated union in `src/platform/schemas.py` |
| Deploy work | `src/agents/shift/scripts/shift-agent-deploy.sh` + `tools/check-shift-agent-patch.sh` |
| New SKILL | one existing SKILL.md to mirror frontmatter + structure |
| File-locking / atomic writes | `src/platform/safe_io.py` — see what helpers exist |

This rule eliminates ~80% of corrections at zero infrastructure cost. Most "drift" in this codebase comes from importing a SaaS-style frame before grounding in this codebase's specific shape.

### Drift-check tag (mandatory at top of every plan/spec/design doc)

Every plan, spec, or design document MUST carry one tag at the top:

- **`Hermes-native`** — uses Hermes primitives without modification
- **`extends-Hermes`** — adds custom infrastructure on top (most platform work falls here)
- **`drifts-from-Hermes`** — explicitly fights Hermes conventions; MUST explain operationally what compensating infrastructure exists

Self-disclosure mechanism. Surfaces deviation at proposal time so reviewers can engage with it explicitly. Does NOT replace the read-deployed-code rule.

### Deployed pattern checklist (Part 1 — verify, do NOT silently import alternatives)

- **Storage:** JSON-on-disk + `safe_io.atomic_write_json` + `fcntl.flock`. Do NOT introduce SQLite/Postgres without explicit `drifts-from-Hermes` tag + reason.
- **NDJSON audit log:** append via `safe_io.ndjson_append` through the `log-decision-direct` chokepoint (used by SKILLs) or via per-agent scripts that share the same chokepoint. Add new variants to `LogEntry` discriminated union (subclass `_BaseEntry`, set `type: Literal["..."]`).
- **Approval codes:** 5-char `#XXXXX` from the 28.6M-entry alphabet via `generate_unique_code` helper. Do NOT invent parallel generators. Codes share a namespace across agents; the dispatcher disambiguates by state-file priority.
- **Schemas:** Pydantic v2 with explicit `model_config`. `extra="forbid"` on state schemas; `extra="ignore"` on LLM-output shapes (extractor outputs may emit unmodelled fields). Status enums use `Literal[...]` not `Enum`.
- **Sender identity:** phone OR LID via `identify-sender`; NEVER trust message content or WhatsApp profile name for routing. ALWAYS call `validate-sender-block` to parse the v=1 block before downstream logic. The `fromMe` flag is informational only.
- **Tests:** Deterministic Python scripts get pytest with subprocess-invoke + assert on file mutations + stdout (matches `test_catering_v02_scripts.py`). Pure-function units (parsers, hash, state-machine table) get in-process tests. SKILL.md interpretation is observability + manual smoke (not unit-tested).
- **Dispatcher routing:** amend the existing `dispatch_shift_agent` matrix in priority order; write `dispatcher_routed` audit entry BEFORE delegating to a handler. Skipping dispatcher = silent routing-correctness regression.
- **Image inputs:** Hermes provides transient `/opt/shift-agent/.hermes/image_cache/img_*.jpg`; agents copy to managed `/opt/shift-agent/state/<agent>/...` for retention.
- **Per-customer-VPS isolation:** each VPS is single-tenant. Don't propose cross-VPS state sharing.

### Operational drift checklist (Part 2)

`docs/hermes-alignment.md` Part 2 lists silent-failure surfaces (Hermes pin gate via `tools/check-shift-agent-patch.sh`, env symlink integrity, audit-log rotation, etc.). DO NOT propose changes that compromise these without an explicit `drifts-from-Hermes` tag and described compensating infrastructure.

### How to apply (mandatory checklist before any plan/spec/code)

1. Identify the work type in the table above
2. Read the listed file(s) — `Read` tool, ~1 second each
3. Apply the drift-check tag at the top of the doc you draft
4. Verify your proposal against the deployed-pattern checklist; flag ANY divergence in the doc explicitly
5. If your proposal silently violates a Part-1 pattern, the reviewer will catch it — better to surface it yourself first

### Why this rule exists (separate from Hermes-first)

**Hermes-first** says: default to Hermes substrate; only write custom code for what it cannot do.
**Drift rules** say: before drafting anything, read the actual deployed code so your proposal is grounded.

Together they prevent: (a) reinventing substrate that exists, (b) proposing patterns that contradict deployed conventions, (c) wasting reviewer time on corrections that the contributor could have caught with a 1-second `Read`.

The Expense Bookkeeper plan v2 (2026-04-29) had 10 drift items that the 5-agent review missed — they emerged only after I read `dispatch_shift_agent/SKILL.md`, `safe_io.py`, and the catering scripts that the plan claimed to mirror. Read-before-propose would have caught all 10 upstream.

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
