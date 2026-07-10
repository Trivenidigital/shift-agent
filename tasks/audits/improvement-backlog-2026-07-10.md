# SMB-Agents — Comprehensive Improvement Backlog (2026-07-10)

*Synthesized from 7 parallel deep analyses (Hermes/platform · Flyer · Shift · Catering · partial
portfolio agents · observability/testing · security/money/deploy-ops), each grounded in the code at
`origin/main` (ae033f7) and building on this session's owner-experience review (#586) and QBO/Stripe-MCP
finding (#585). Read-only analysis — nothing here is implemented.*

**How to read:** each item keeps its source ID (`BL-<AREA>-NN`) traceable to the detailed per-domain
analysis. **Severity:** P0 (blocker / dangerous / money-loss) · P1 (high) · P2 (medium) · P3 (low).
**Effort:** S / M / L / XL. Cross-lens duplicates are merged (see §7 dedup map). This is a triage
artifact — prioritize by picking from §3 (top items), §5 (triage views), or the per-area tables (§6).

---

## 1. Executive summary

The portfolio's engineering is strongest exactly where it's loud (fail-closed deploy gates, fact-safety
OCR verification, owner-only privilege checks, idempotency anchors) and weakest exactly where failure is
quiet. **One failure-class recurs in every agent:** the *happy path is wired, every branch off it isn't*
— stale/no-reply/unactioned states have no owner, and copy sometimes promises monitoring that doesn't
exist. The money path is **safe only by default** (catering deposit is hardwired to a placeholder
provider), but the disciplines that must be live before Stripe (headcount-scaled totals, per-lead deposit
confirmation, TTL expiry, a mechanical enablement gate) are unbuilt — and a wrong-amount intent is already
being minted in unconfigured mode. Deploy/ops can silently bypass its own new gates (the two-pass deploy
bug, confirmed live this session). Underneath, three hand-maintained monoliths (`install_artifacts`,
cf-router, the 167-variant `LogEntry`) are the shared root of the deploy trap and routing fragility.

**Closest to real value:** Expense Bookkeeper #21 (one PR — the QBO MCP adapter) and the shared
"stale-state sweep" primitive (the shift no-response sweep, #587, is the first instance — generalize it).

## 2. Cross-cutting themes (the meta-patterns)

| # | Theme | Where it shows up |
|---|---|---|
| **T1** | **Unowned stale states** — happy-path-only lifecycle; needs a shared "stuck-in-state-X > TTL → transition + alert" primitive | Shift (4h expiry, decline/timeout dead-ends), Catering (no stale-lead re-nudge), Flyer (recovery/SLA watchdogs), Observability (G3/G7) |
| **T2** | **Decision/send levers ride on the LLM, not the deterministic gateway** | Shift owner-approval + candidate-reply (BL-SHIFT-01/11), cf-router approve-before-reject (BL-HERMES-02) |
| **T3** | **Money-safety is not enforced** — the Stripe go-live blocker cluster | Catering headcount total, per-lead deposit confirm, TTL driver, mechanical Stripe gate (BL-CATER-01/02/10, BL-SEC-01/02/03/06, BL-PORT-02/03/04) |
| **T4** | **Silent-failure surfaces without watchdogs (§12a/§12b)** | Audit log, ALL commerce state, expense auto-expiry, alert-delivery, corrupt-state, skills-wipe |
| **T5** | **Deploy/ops bypasses its own gates** — two-pass deploy, `extra="forbid"` rollback-brick, untested gates | BL-HERMES-01/05/06/16, BL-SEC-13/14/15 |
| **T6** | **Guards facts, abandons brand** | Flyer aesthetics off-by-default + regional-glyph tofu (BL-FLYER-01/02); catering brand/currency |
| **T7** | **Monolith tech-debt with no registry** — root cause of T5 + routing fragility | `install_artifacts`, cf-router, `LogEntry` (BL-HERMES-08/09/10) |
| **T8** | **Portfolio: 1 PR from ROI, 1 primitive unblocks 5** | Expense QBO-MCP (BL-PORT-01); one POS adapter → #22/#5/#4/#6/#16 (BL-PORT-05) |
| **T9** | **CI doesn't gate the safety nets** | Flyer incident-replay in no CI, full suite not enforced, documented invariants untested (BL-HERMES-11/17, BL-FLYER-05, BL-SHIFT-15) |
| **T10** | **Security residuals** — root CWD-hijack, notify-owner RCE-if-root, skills-integrity D2 disabled/evadable | BL-SEC-07/08/09/10 |

## 3. Top items — the must-address list (portfolio-wide, ranked)

**Money / correctness (do before any Stripe flip):**
1. **BL-CATER-01 / BL-SEC-01 (P0)** — catering `quote_total_usd` never scaled to headcount (qty=1 baskets) → a 200-guest event yields a ~$13 deposit. *Root money bug.*
2. **BL-CATER-02 / BL-SEC-03 (P0)** — deposit auto-fires with no per-lead owner confirmation of the dollar amount.
3. **BL-CATER-10 / BL-PORT-03 / BL-SEC-06 (P0)** — wrong-amount intent already minted+bound in unconfigured mode; deposit-link TTL-expiry driver exists only as `.pyc` (unverified) → won't re-mint / never expires once live.
4. **BL-SEC-02 / BL-PORT-04 (P0)** — make Stripe enablement a *mechanical* hard gate (not a prose checklist) blocking `provider=stripe` until 1–3 are resolved.
5. **BL-CATER-03 (P1, quick)** — interim `<$8/guest` deposit-plausibility guard (ships in a day while 1 is designed).

**Correctness / silent-failure:**
6. **BL-SHIFT-03 (P2→treat P1)** — unrecognized absence date silently defaults to *today* → wrong shift covered, real shift left uncovered.
7. **BL-HERMES-02 (P1)** — cf-router approve-before-reject precedence: "go ahead and *reject* #ABCDE" fires the irreversible customer *send*.
8. **BL-HERMES-01 / BL-SEC-13 (P1)** — two-pass deploy: new gates/install-lines don't run on the deploy that ships them (confirmed live).
9. **BL-HERMES-03 / BL-SEC-18 / OBS-G4 (P1)** — audit chokepoint `decisions.log` has no write-rate watchdog + failures swallowed.
10. **BL-SEC-16 (P1)** — owner-alert silent-drop (muted Pushover invisible to API; quiet-hours) with no delivery-ack — *directly relevant to the live box, which is Pushover-muted.*

**Security:**
11. **BL-SEC-07 (P1, quick)** — `shift-agent-backup.service` (root) runs `python3 -c` with no `WorkingDirectory=/` → CWD sys.path-hijack → root RCE.
12. **BL-HERMES-04 (P1)** — regulated-send chokepoint fails open for flat-renamed modules (sends escape the audit/flock guard).
13. **BL-SEC-08 (P1)** — `notify-owner` `/opt` imports = latent root-RCE; invariant enforced by convention, not a test.

**Brand / trust:**
14. **BL-FLYER-01 (P1)** — regional-script text silently renders as tofu for advertised non-Telugu languages (no fonts in-tree) — brand-critical for the diaspora base.
15. **BL-FLYER-02 (P1)** — aesthetic critique off-by-default *and* never gates; a "would_i_post=1.0" ugly poster ships.
16. **BL-FLYER-05 (P1, quick)** — routing incident-replay regression net runs in **no** blocking CI.

**Value unlocks:**
17. **BL-PORT-01 (P0-value)** — Expense #21 QBO MCP adapter = one PR to hard-dollar ROI (authorized via #585).
18. **BL-PORT-05 (P1, XL)** — one POS adapter unblocks P&L #22, EOD reconcile, demand-forecast, inventory, sales-tax.

## 4. (Full per-area backlog follows in §6; triage cuts in §5.)

## 5. Triage views

### 5a. Quick wins — small, low-risk, high-trust-ROI (a focused sprint)
BL-CATER-03 (deposit-plausibility guard) · BL-CATER-05 (retitle "FINALIZED"→"Draft basket") · BL-CATER-08 (strip retail prices from pre-approval send) · BL-CATER-15 (⚕ emoji) · BL-SHIFT-05 (stop leaking coworker health reason) · BL-SHIFT-10 (KILL out of routine footer) · BL-SHIFT-04 (4h expiry — extend the sweep scaffold) · BL-SHIFT-14 (surface "escalation DISABLED") · BL-FLYER-04 (reconcile approval-alias vs "APPROVE") · BL-FLYER-05 (add incident-replay to CI) · BL-FLYER-07 (say *what's* wrong in delivered-with-warning) · BL-SEC-04/BL-SHIFT-13 (cross-pool code-uniqueness) · BL-SEC-07 (`WorkingDirectory=/` on backup.service) · BL-OBS-G3 (one-line notify on expense auto-expiry) · BL-PORT-08 (OSRM routing) · BL-PORT-10 (weekly owner-load brief section).

### 5b. Foundation / unblockers — build once, unlocks many
- **Shared stale-state sweep primitive** (generalize #587) → closes T1 across shift/catering/flyer (BL-CATER-07, BL-SHIFT-04/16, OBS-G1/G3/G7).
- **Deterministic owner/candidate cf-router intercept** → closes BL-SHIFT-01+11 together + enables their tests (BL-SHIFT-15).
- **Manifest-driven `install_artifacts`** (BL-HERMES-08) → fixes the two-pass deploy (BL-HERMES-01) + rollback-hygiene drift at the root.
- **QBO MCP adapter** (BL-PORT-01) → Expense #21 to value; **POS adapter** (BL-PORT-05) → 5 agents.
- **A real CI pipeline** (BL-HERMES-11/17, BL-FLYER-05) → gates every safety net.

### 5c. Hard gates — MUST precede the relevant go-live
- **Before `provider=stripe` (any catering VPS):** BL-CATER-01, -02, -03, -10, -13 + BL-SEC-02/06 + BL-PORT-02/03. (Mechanical gate = BL-SEC-02.)
- **Before enabling D2 skills-audit:** populate the foundation allowlist from the live box (BL-SEC-09).
- **Before enabling any new agent per-customer:** a per-agent readiness matrix (BL-SEC-17).

### 5d. Big bets — XL, strategic, need their own plan + explicit go
Hermes 0.14→0.17 upgrade / patch-port / official WhatsApp Business API (BL-HERMES-07) · cf-router consolidation + intent-contract to ACTIVE (BL-HERMES-09, BL-FLYER-13) · `LogEntry`/schemas decomposition (BL-HERMES-10) · POS adapter (BL-PORT-05) · flyer owner-consented broadcast (BL-FLYER-12).

---

## 6. Full backlog by area

### 6a. Catering (money + customer-facing)
| ID | Title | Type | Sev | Effort | Risk |
|---|---|---|---|---|---|
| BL-CATER-01 | `quote_total_usd` never headcount-scaled → wrong deposit | bug/money | P0 | L | high |
| BL-CATER-02 | Deposit auto-fires, no per-lead owner $-confirm | money-safety | P0 | M | high |
| BL-CATER-03 | `<$8/guest` deposit-plausibility guard (interim) | money-safety | P1 | S | low |
| BL-CATER-04 | Customer quote has no price-correctness check | bug/money | P1 | M | med |
| BL-CATER-05 | "Customer FINALIZED" card lists items customer never chose | ux-trust | P1 | S | low |
| BL-CATER-06 | Owner `edit` path is a WhatsApp dead-end | partial-impl | P1 | M | med |
| BL-CATER-07 | No stale-lead sweep → silent lost booking | observability | P1 | M | low |
| BL-CATER-08 | Retail prices auto-sent pre-approval (invariant violation) | bug/money | P1 | S | low |
| BL-CATER-09 | INR→USD currency landmine (no currency field) | bug/money | P2 | M | med |
| BL-CATER-10 | Wrong-amount intent minted in unconfigured mode; no re-mint | ops/money | P2 | M | med |
| BL-CATER-11 | Customer-finalize is an auto-default stub | partial-impl | P2 | L | med |
| BL-CATER-12 | Menu preview truncated 8/cat → owner approves unseen prices | ux-trust | P2 | S | low |
| BL-CATER-13 | Missing regression test for the money invariant | test | P2 | S | low |
| BL-CATER-14 | `lookup-prior-leads-by-phone` unused-but-deployed | tech-debt | P3 | S | low |
| BL-CATER-15 | ⚕ caduceus emoji brand header | ux-trust | P3 | S | low |

### 6b. Shift (staff + coverage)
| ID | Title | Type | Sev | Effort | Risk |
|---|---|---|---|---|---|
| BL-SHIFT-01 | Owner approval rides on LLM (no deterministic intercept) | partial-impl | P1 | M | med |
| BL-SHIFT-02 | Decline/timeout dead-end; no NEXT; late-accept unrecoverable | feature | P1 | L | med |
| BL-SHIFT-03 | Unrecognized absence date silently → today (wrong shift) | bug | P2* | M | low |
| BL-SHIFT-04 | "Expires 4h" decorative — no expiry transition, dead knob | partial-impl | P2 | S | low |
| BL-SHIFT-05 | Coverage msg leaks coworker's health reason | security/privacy | P2 | S | low |
| BL-SHIFT-06 | Sweep alert priority-1: quiet-hours-suppressible, no retry | ops | P2 | S | low |
| BL-SHIFT-07 | Zero-coverage approval dead-ends w/ phantom "See Pushover" | bug | P2 | S | low |
| BL-SHIFT-08 | No fairness/rotation ledger; same person every time | feature | P2 | L | med |
| BL-SHIFT-09 | Candidate + owner-confirm copy English-only | feature | P2 | M | low |
| BL-SHIFT-10 | `KILL` in every proposal footer (fat-finger) | ux-trust | P2 | S | low |
| BL-SHIFT-11 | Candidate YES/NO on LLM; late reply mis-routes to sick-call | partial-impl | P2 | M | med |
| BL-SHIFT-12 | Owner-previewed vs sent message can diverge (re-render) | tech-debt | P3 | S | low |
| BL-SHIFT-13 | Cross-agent approval-code collision (no cross-pool check) | tech-debt | P3 | M | low |
| BL-SHIFT-14 | Escalation ships OFF with no "it's disabled" signal | observability | P3 | S | low |
| BL-SHIFT-15 | No tests for approve/candidate decision paths + 4h invariant | test | P2 | M | low |
| BL-SHIFT-16 | `send_failed` proposals have no auto-escalation | partial-impl | P3 | S | low |
*BL-SHIFT-03 rated P2 by the analyst but treat as P1 (silent wrong-shift).*

### 6c. Flyer Studio (brand + routing)
| ID | Title | Type | Sev | Effort | Risk |
|---|---|---|---|---|---|
| BL-FLYER-01 | Regional-script text renders as tofu (no fonts in-tree) | bug/brand | P1 | M | med |
| BL-FLYER-02 | Aesthetic critique off-by-default and never gates | partial-impl | P1 | M | med |
| BL-FLYER-03 | Preview-approved → final-QA-fail surprise (F0065) | bug/ux-trust | P1 | M | med |
| BL-FLYER-04 | 9 approval aliases vs "exact APPROVE"; bare ok/yes finalizes | ux-trust | P1 | S | low |
| BL-FLYER-05 | Incident-replay routing net in no blocking CI | test | P1 | S | low |
| BL-FLYER-06 | Jargon/leak guard runs only in tests, not at send chokepoint | observability | P2 | M | med |
| BL-FLYER-07 | `delivered_with_warning` ships defects w/ vague "small note" | ux-trust | P2 | S | low |
| BL-FLYER-08 | Fuzzy LID/phone identity; silent session-match fallbacks | tech-debt/bug | P2 | L | high |
| BL-FLYER-09 | No "why this concept"; hardcoded descriptors; default C1 | feature | P2 | M | low |
| BL-FLYER-10 | `extraction_v2` silently degrades to legacy on any exception | silent-failure | P2 | S | low |
| BL-FLYER-11 | Recovery + SLA watchdog timers enablement unverifiable | ops | P2 | S | low |
| BL-FLYER-12 | Maker not sender; `send-flyer-campaign` misleadingly named | feature | P3/P2 | XL/S | high |
| BL-FLYER-13 | Intent contract shadow-only; dual router standing debt | tech-debt | P3 | XL | high |
| BL-FLYER-14 | `operating_layer.py` dead scaffolding (403 LOC) | tech-debt | P3 | S | low |
| BL-FLYER-15 | Guided-intake dormant (546 bypass rows), no fire-telemetry | feature | P3 | M | low |
| BL-FLYER-16 | Large modules under-tested; `openrouter_env.py` zero coverage | test | P3 | M | low |

### 6d. Hermes / platform substrate
| ID | Title | Type | Sev | Effort | Risk |
|---|---|---|---|---|---|
| BL-HERMES-01 | Two-pass deploy: new install lines skip pass 1 (confirmed live) | bug/ops | P1 | M | med |
| BL-HERMES-02 | cf-router approve-before-reject → irreversible send on "reject" | bug/security | P1 | S | low |
| BL-HERMES-03 | Audit `decisions.log` no freshness watchdog; failures swallowed | observability | P1 | M | low |
| BL-HERMES-04 | Regulated-send chokepoint fails open for flat-renamed modules | security | P1 | M | med |
| BL-HERMES-05 | `extra="forbid"` money/proposal stores brick on rollback | bug/tech-debt | P1 | M | med |
| BL-HERMES-06 | `Proposal` union has no unknown-tag fallback (raises on core state) | bug | P1 | S-M | med |
| BL-HERMES-07 | Hermes 0.14 pin dead-end; no official-API fallback | tech-debt | P1 | XL | high |
| BL-HERMES-08 | `install_artifacts` ~740-line hand-maintained pile (no registry) | tech-debt | P2 | L | med |
| BL-HERMES-09 | cf-router routing = source-order in 400-800-line fns, no table | tech-debt | P2 | XL | high |
| BL-HERMES-10 | `LogEntry` 167 hand-written variants + drifting sidecars | tech-debt | P2 | L | low |
| BL-HERMES-11 | Full pytest not enforced in CI; build gate skippable | test/ops | P2 | M | low |
| BL-HERMES-12 | `credential_readiness` fails open on the foundation gate | security | P2 | M | low |
| BL-HERMES-13 | `safe_io` un-importable on Windows (unguarded fcntl) | tech-debt | P2 | S | low |
| BL-HERMES-14 | `safe_io` write-primitive hardening (fd leak, blocking lock, mode) | bug/security | P2 | M | low |
| BL-HERMES-15 | MCP path for external writes is design-only (unbuilt substrate) | partial-impl | P2 | L | med |
| BL-HERMES-16 | `Config` extra=forbid + `schema_version:Literal[1]` reject forward-roll | tech-debt | P2 | M | med |
| BL-HERMES-17 | Deploy gates + smoke untested, flyer-biased, probe scripts not SKILLs | test | P2 | M | low |
| BL-HERMES-18 | Rollback `PREV_TAG` by mtime, not lineage; guard bypassable | ops | P3 | M | med |
| BL-HERMES-19 | No `platform-contract.md` / semver for the substrate surface | tech-debt | P3 | M | low |

### 6e. Partial portfolio agents
| ID | Title | Type | Sev | Effort | Blocking |
|---|---|---|---|---|---|
| BL-PORT-01 | Expense #21 — wire RealQBOClient via Intuit QBO MCP adapter | partial-impl | P0-value | M | Intuit MCP + OAuth |
| BL-PORT-02 | Commerce — lead-keyed get-or-create resend (replace interim guard) | bug | P0* | M | money-discipline |
| BL-PORT-03 | Commerce — verify/build deposit-link TTL driver (only `.pyc`) | observability | P0* | S-M | money-discipline |
| BL-PORT-04 | Commerce Stripe go-live (gated on G1-G4 + deposit template) | partial-impl | P1 | M | money+owner-config |
| BL-PORT-05 | POS adapter primitive (unblocks 5 agents) | feature | P1 | L-XL | POS API + customer choice |
| BL-PORT-06 | P&L Anomaly #22 — build detectors on POS | partial-impl | P1 | M | POS |
| BL-PORT-07 | EOD #5 — add register/POS money reconciliation | partial-impl | P1 | M | POS |
| BL-PORT-08 | Multi-location #3 — real OSRM routing (always Haversine now) | bug | P2 | S | none |
| BL-PORT-09 | Multi-location #3 — inter-location transfer skill | feature | P2 | M | none |
| BL-PORT-10 | Daily Brief #4 — weekly owner-load section (buildable now) | feature | P2 | S | none |
| BL-PORT-11 | Employee Docs #14 — clone compliance cron (easiest tier-2) | partial-impl | P2 | S-M | owner-config |
| BL-PORT-12 | Cash & AR #15 — invoice-aging on catering data | partial-impl | P2 | M | internal data |
| BL-PORT-13 | Expense #21 — token-redactor gap for real OAuth (V02-4) | security | P2 | S | none (P1 when 01 lands) |
| BL-PORT-14 | Compliance #13 — fix stale "stub" text + ServSafe prefill | bug+feature | P2 | S+M | owner-config |
| BL-PORT-15 | Equipment #19 — intake + PM cron (mirror compliance) | partial-impl | P3 | M | owner-config |
| BL-PORT-16 | Catering Followup #10 — lead→CLOSED trigger | partial-impl | P3 | M | catering lifecycle hook |
| BL-PORT-17 | Sales Tax #16 & Inventory #6 — POS + jurisdiction/SKU content | partial-impl | P3 | L | POS + content |
| BL-PORT-18 | Supplier #7 / VIP #9 / Hiring #12 are pre-build stubs (tracking) | tracking | P3 | — | owner-config/data |
*BL-PORT-02/03 are P0 only as hard-gates for a live Stripe flip; harmless while dormant.*

### 6f. Cross-cutting — security / money / deploy-ops / observability
| ID | Title | Type | Sev | Effort | Risk |
|---|---|---|---|---|---|
| BL-SEC-07 | `backup.service` (root) `python3 -c` no `WorkingDirectory=/` → RCE | security | P1 | S | low |
| BL-SEC-08 | `notify-owner` /opt imports = latent root-RCE (convention-only) | security | P1 | M | med |
| BL-SEC-13 | Deploy-twice: deploy.sh installs itself but never re-execs | deploy | P1 | M | med |
| BL-SEC-14 | `extra=forbid` bricks startup on forward-config rollback | deploy/ops | P1 | M | med |
| BL-SEC-16 | Owner-alert silent-drop (muted Pushover / quiet-hours), no ack | go-live/ops | P1 | M | med |
| BL-SEC-04 | Catering code generator ignores cross-agent namespace (collision) | bug/security | P1 | S | low |
| BL-SEC-09 | D2 skills-audit ships DISABLED (empty foundation allowlist) | security | P2 | M | low |
| BL-SEC-10 | Skills-integrity residuals: namespaced evasion, dir-hashing, D3 | security | P2 | L | low |
| BL-SEC-11 | `parse_catering_inquiry`/`parse-menu-photo` no injection guard | security | P2 | S | low |
| BL-SEC-12 | Approval scripts don't re-derive owner identity in-process | security | P2 | M | med |
| BL-SEC-15 | Env-symlink integrity gate has no automated regression test | ops/test | P2 | S | low |
| BL-SEC-17 | pilot-readiness ≠ production-ready: per-agent go-live matrix | go-live | P2 | M | low |
| BL-OBS-G1 | ALL commerce state has zero standing freshness watchdog (money) | observability | P1 | M | low |
| BL-OBS-G2 | Commerce webhook/livemode gates deploy-time only (post-deploy drift) | observability | P2 | M | med |
| BL-OBS-G3 | Expense auto-expiry reverses owner-approval with no alert (§12b) | observability | P2 | S | low |
| BL-OBS-G5 | Corrupt-state + skills `--delete` wipe alert via backstop, not write-site | observability | P2 | M | low |
| BL-OBS-G6 | Compliance heartbeat written but never consumed (no reader) | observability | P3 | S | low |
| BL-OBS-G8 | Core shift state only nightly fsck + 4h proposal-age | observability | P3 | M | low |

*(Merged into other rows: BL-SEC-01/02/03/05/06 → Catering §6a; BL-SEC-18/OBS-G4 → BL-HERMES-03; OBS-G7 → BL-SHIFT-14; BL-SEC-13/14 mirror BL-HERMES-01/16.)*

### 6g. Testing / CI coverage
*The 5 GitHub workflows are the entire CI surface; there is no all-encompassing pytest gate (extends BL-HERMES-11/17, BL-FLYER-05, BL-SHIFT-15).*
| ID | Title | Type | Sev | Effort | Risk |
|---|---|---|---|---|---|
| BL-CI-01 | 69 of 93 flyer test files run in NO CI → delivery/creative/golden/incident-replay regressions merge green | test | P1 | S-M | low |
| BL-CI-02 | `test_flyer_ws5_pdf_twin_qa` real-fcntl subprocess tests skip on Windows AND in no CI → never run anywhere | test | P2 | S | low |
| BL-CI-03 | No `_BaseEntry.__subclasses__()` reachability test → a new `LogEntry` variant can be defined yet unregistered with no failing test | test | P2 | S | low |
| BL-CI-04 | Deploy-gate patch baseline + `check-safe-io-symbols` have no enforcing pytest (only fail-closed bash at deploy) | test | P2 | S | low |
| BL-CI-05 | 8 Tier-2 agents ship a dispatcher skill with only config-schema coverage (no routing/behavioral test) | test | P3 | M | low |
| BL-CI-06 | No consolidated blocking pytest gate; `hermes-drift-check` full run is weekly + non-blocking; build-tarball gate is `--skip-pytest`-able | test/ops | P2 | M | low |

## 7. Dedup / merge map (cross-lens duplicates)
- **Catering headcount total:** BL-CATER-01 = BL-SEC-01 (kept CATER-01).
- **Per-lead deposit confirm:** BL-CATER-02 = BL-SEC-03.
- **Deposit TTL / re-mint:** BL-CATER-10 = BL-PORT-03 = BL-SEC-06.
- **Two-pass deploy:** BL-HERMES-01 = BL-SEC-13.
- **Audit-log watchdog:** BL-HERMES-03 = BL-SEC-18 = BL-OBS-G4.
- **Config forward-roll / rollback brick:** BL-HERMES-16 ≈ BL-SEC-14; BL-HERMES-05/06 related.
- **Approval-code collision:** BL-SHIFT-13 = BL-SEC-04 (kept both views; one fix).
- **Sweep-disabled visibility:** BL-SHIFT-14 = BL-OBS-G7.
- **Skills-integrity D2 disabled/residuals:** BL-SEC-09/10 (this session's #583/#584 known residuals).

## 8. Notes / caveats
- Several items' severity depends on **live-box facts not verifiable from the repo**: whether real catering leads use auto-default vs owner-curated baskets (gates BL-CATER-01 active-vs-latent); the live foundation-skill layout (BL-SEC-09); the 0.17 flag names (BL-SEC-10, box is pinned 0.14); the deposit-link TTL `.pyc` behavior (BL-PORT-03). Confirm on-box before scoping those.
- The **"Hermes 0.17 anchors gone"** premise (BL-HERMES-07) is from operator memory, not the tree — verify against a real 0.17 checkout before scoping the port.
- Nothing here proposes flipping an `enabled` flag or moving money — all items are read-first / build-first; live flips remain operator-gated.
