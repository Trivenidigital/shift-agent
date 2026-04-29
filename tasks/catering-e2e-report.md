# Catering Agent — End-to-End Test Report (2026-04-29)

**Scope:** All catering surfaces — 5 scripts, 12 schemas + 9 audit-entry classes, 5 SKILLs, 2 templates, 4 test files, live VPS state.
**Method:** baseline pytest run on VPS Linux, 4 parallel deep-audit subagents (silent-failure / architect lenses), scenario harness (15 edge cases beyond pytest), live-state read-only audit.
**Verdict:** **63 findings** across 4 audit lenses. **10 CRITICAL** are real production risks affecting Triveni live customer flows today. **63 existing tests are broken** (cannot run anywhere — same `spec_from_file_location` returning None bug PR #28 fixed in `_b1_helpers.py`).

---

## 1. Test infrastructure status

| Test file | Tests | On Linux | On Windows | Notes |
|---|---|---|---|---|
| `test_catering_b1_cases.py` | 19 | ✅ all pass | skip (fcntl) | PR #28 — uses fixed `SourceFileLoader` pattern |
| `test_catering_schemas.py` | ~25 | ✅ all pass | ✅ all pass | Pure schema; no script execution |
| `test_catering_v02_scripts.py` | 30 | ❌ all fail | skip (fcntl) | Broken `spec_from_file_location` — never actually ran |
| `test_lookup_prior_leads.py` | 33 | ❌ all fail | skip (fcntl) | Same broken pattern |

**Cumulative**: 44 of 107 catering tests actually validate behavior. **63 tests are decoration** (pass on Windows by skipping; fail on Linux because no spec). PRs #25, #26, #27 added tests that have never executed.

---

## 2. Live state audit (read-only)

| Surface | State | Notes |
|---|---|---|
| `catering-leads.json` | 9 leads (5 AWAITING, 4 SENT_TO_CUSTOMER) | Healthy distribution |
| `catering-menu.json` | 39 items, 100% `["veg"]` | Triveni context (vegetarian grocery) — not a bug |
| Menu archive | 0 entries | Either initial-set never archives (correct) or archive logic broken (B1.C1 below) |
| Pending menu update | absent | Clean |
| `catering_lead_rejected` audit entry | 1 entry, `lead_id` field absent | By design — rejection precedes lead creation |
| Most recent flows | clean: created → status_change → owner_approval_requested → owner_decision → quote_sent | Healthy |

---

## 3. Bug list — by severity

### CRITICAL (10) — real production risk

| ID | Surface | One-liner |
|---|---|---|
| **A1** | apply-script | Approve-path crashes between two locks → lead orphaned in `OWNER_APPROVED`; customer received quote, state says they didn't (line 299–334) |
| **A2** | apply-script | No idempotency on bridge POST → retry sends duplicate quote to customer (line 323, 334–343) |
| **A3** | apply-script | `_load_menu_filtered` swallows ALL exceptions silently → customer receives quote with no menu section (line 105–110) |
| **A4** | apply-script | Template format `KeyError` silently falls through to inline stub fallback → customer receives generic "let's discuss" instead of quote (line 191–196) |
| **C1** | create-script | `customer_phone` ValidationError raises uncaught traceback inside lock — emits stack trace, not `EXIT_INVALID_INPUT` (line 432) |
| **C2** | create-script | Idempotency keyed only on `original_message_id` — replay with different phone for same message_id silently returns wrong lead (line 374–377) |
| **M1** | apply-menu-update | Bare `except Exception` on prior-menu read → on corruption, archive is skipped AND menu file overwritten with v1; only trace is `safe_io`'s quarantine rename (line 122–146) |
| **S1** | schemas | `CateringLead` has no validator coupling `status=SENT_TO_CUSTOMER` → non-empty `quote_text`. A bug or fixture can write `status=SENT_TO_CUSTOMER, quote_text=""` and downstream sends blank quote (line 332–348) |
| **S2** | schemas | `CateringLeadStatus` Literal has NO transition enforcement anywhere — `NEW → SENT_TO_CUSTOMER` (skipping extraction) is structurally valid (line 332) |
| **T1** | tests | 63 catering tests (`test_catering_v02_scripts.py` + `test_lookup_prior_leads.py`) are non-functional decoration. Same `spec_from_file_location → None` bug fixed in PR #28's `_b1_helpers.py` |

### HIGH (19)

| ID | Surface | One-liner |
|---|---|---|
| **A5** | apply-script | `off_menu_items` from extractor are NEVER rendered into customer quote (only owner card has them — schema docstring at 323–328 explicitly warns about this coupling) |
| **A6** | apply-script | Reject path with `--reason` NEVER messages the customer (docstring claims "optionally sends decline message" but only approve branches send) |
| **A7** | apply-script | State-write + audit-log not atomic — log append failure leaves lead in mutated state with no audit entry |
| **A8** | apply-script | `dietary_restrictions` filter silently drops unknown tags (`"halaal"`, capitalized Unicode) — empty filter returns "didn't find" message even when veg menu has 39 items |
| **A9** | apply-script | Code-collision branch logs to stderr only; no `InvariantViolation` audit / Pushover (line 263–268) |
| **C3** | create-script | `_bridge_post` no retry — single bridge hiccup loses the owner's approval card (compare `send-coverage-message` which retries) |
| **C4** | create-script | Bridge timeout: no `CateringOwnerApprovalCardFailed` audit emitted; post-mortem cannot distinguish "card sent" from "card failed" |
| **C5** | create-script | `self_chat_jid` empty → emits `CateringOwnerApprovalRequested` claiming approval was requested, when card was never even attempted |
| **C6** | create-script | Off-menu running-budget over-counts separators by 2 → drops items unnecessarily at exactly-equal-to-budget boundary |
| **PM1** | parse-menu-photo | Silently overwrites a still-pending update — owner's previous code is invalidated with no notification |
| **PM2** | parse-menu-photo | Phone canonicalization: 10-digit US local `9045551234` becomes `+9045551234` (NOT `+19045551234`) and silently passes E.164 regex — returning customers misidentified as new |
| **PM3** | apply-menu-update | Hard-coded `len(code) != 6` duplicates schema regex; valid edge codes silently rejected with EXIT_INVALID_INPUT |
| **S3** | schemas | `_BaseEntry.ts: datetime` accepts naive datetimes → audit replay ambiguity if server tz changes |
| **S4** | schemas | `OWNER_EDITED` has no dedicated audit class; `edit_text` allows empty when `decision="edit"` → re-draft agent can't recover edit intent after crash |
| **S5** | schemas | `price_usd: float` — accumulates rounding error (200 × $9.99 = `1997.9999999999998`); cosmetic in current text rendering, breaks if downstream does arithmetic |
| **S6** | schemas | `ProposalCode` regex (`^#[A-HJKMNPQR-Z2-9]{5}$`) excludes L; `MenuPendingUpdate.confirmation_code` regex (`^#[A-HJ-NP-Z2-9]{5}$`) accepts L → forged-code detection weakened |
| **L1** | lookup-script | (subsumed by PM2 above) Phone canonicalization edge cases for US 10-digit |
| **L2** | lookup-script | `_normalize_aware` warn goes to stderr only — SKILL preambles capturing stdout miss tz drift |

### MEDIUM (19) and LOW (15)

Catalogued in subagent output files (see Appendix A); summary themes:
- Generic `except Exception` across multiple scripts (5 instances)
- Configurable port / `BRIDGE_URL` hardcoded (3 places)
- Code-format normalization (`#` prefix, lowercase) inconsistent between scripts
- Test coverage gaps: `MenuUpdateProposed`/`Applied`/`Rejected`, `CateringLeadRejected`, `MenuPendingUpdate` constructors not exercised
- Hardcoded `/opt/shift-agent` paths (no `SHIFT_AGENT_ROOT` env var)
- Counter race in `parse-menu-photo._next_update_id` (no lock around RMW)
- Markdown-fence stripping in parse-menu-photo brittle for single-line LLM responses
- 401/403 indistinguishable from 5xx in parse-menu-photo
- `headcount=0` and `-5` correctly rejected by Pydantic ge=1 ✓ (verified by scenario harness)
- Calendar-invalid dates correctly rejected (verified by scenario harness)

### Scenario harness — 15 edge cases tested live on VPS sandbox

| Scenario | Expected | Actual | Pass/Fail |
|---|---|---|---|
| S1: corrupt leads.json | rc=5 (SCHEMA_VIOLATION) | rc=5 ✓ | PASS — but corrupt file gets overwritten (covered by M1) |
| S2: replay with mutated args | idempotent (no mutation) | rc=0 idempotent_replay=true ✓ | PASS — original lead preserved |
| S6: invalid date `2026-02-30` | rc=2 | rc=2 ✓ | PASS |
| S7: `2030-12-25` (>1yr) | accepted | rc=0 ✓ | PASS — no upper bound on event_date (acceptable) |
| S8: headcount=10000 | accepted | rc=0 ✓ | PASS — no upper bound on headcount (acceptable for now) |
| S8b: headcount=0 | rejected | rc=2 (Pydantic ge=1) ✓ | PASS |
| S8c: headcount=-5 | rejected | rc=2 (Pydantic ge=1) ✓ | PASS |
| S15: malformed phones | ValueError | ValueError raised cleanly ✓ | PASS |
| S16: 3000-char notes | rejected (max=2000) | not validated, fell through to bridge — needs verify | NEEDS-VERIFY |
| S17: 30 off_menu_items | rejected (max=20) | rc=2 ✓ | PASS |
| S20: catering disabled | rc=2 | rc=2 ✓ | PASS |
| S21: missing config.yaml | rc=5 | rc=5 ✓ | PASS |

---

## 4. Recommendation — fix priorities

**Tier-1 fix bundle (must-ship before next release)** — addresses customer-facing risk and audit integrity:

1. **A1+A2 idempotency**: introduce `CateringQuoteAttempted` audit row at lock-1 entry; check on retry to prevent double-send (mirror `OutboundAttempted` pattern from `schemas.py:822`)
2. **A3+A4 silent fallbacks**: replace bare `except Exception` with narrowed catches; emit `InvariantViolation` audit + non-zero exit on menu/template load failure
3. **A5 off-menu in customer quote**: include `off_menu_items` in `_render_quote` substitution dict (mirrors approval card)
4. **A6 reject path**: implement `--decision reject --reason ...` → customer message, OR remove the docstring claim
5. **C1+C2 create-script idempotency**: catch `ValidationError` in lead constructor; compare phone in idempotency lookup
6. **M1 menu archive**: narrow exception in `apply-menu-update`; abort on corruption rather than overwrite
7. **S1 quote-text invariant**: add `model_validator` requiring non-empty `quote_text` for post-AWAITING statuses
8. **PM2 phone canonicalization**: detect 10-digit US-local input; either prepend `1` or reject
9. **T1 broken test infrastructure**: port `_b1_helpers.py` SourceFileLoader pattern to `test_catering_v02_scripts.py` + `test_lookup_prior_leads.py`. Resurrect 63 dormant tests.

**Tier-2 (next sprint)** — robustness and observability:
- C3+C4+C5: bridge retry + audit gap closure
- A7+A8+A9: state-write atomicity + dietary tag whitelist + code-collision alerts
- PM1: pending-update collision handling
- S2: status transition table (run-time guard)
- S3+S4+S6: tz-aware ts validator + OWNER_EDITED audit class + regex unification

**Tier-3 (technical debt)** — defer:
- S5 Decimal money types (no current bug, prevent future)
- Configurability (env-var paths, port)
- Markdown-fence regex robustness, 401 distinguishing exit code

---

## 5. Proposed scope for the bundled fix PR

Given user's directive ("create a branch and follow this process Plan → 5 reviews → Design → 5 reviews → Build → PR → 5 reviews → merge and deploy"):

**Recommended scope: Tier-1 only (9 items)** — keeps the PR focused and reviewable. ~600–900 LOC change estimate. Tier-2 and Tier-3 file as follow-up tickets.

**NOT recommended for this PR**:
- All 53 Tier-2 + Tier-3 items (would balloon to 2000+ LOC, 10+ subsystem changes, slow review cycle)
- Test-infrastructure resurrection of 63 dormant tests (item T1) — that's its own focused PR

**Branch name suggestion**: `fix/catering-tier1-bundle`

---

## Appendix A — Per-audit findings

Full subagent outputs in:
- `tasks/catering-e2e-report.md` (this file)
- audit transcripts in `~/.claude/projects/.../tasks/<agent-id>.output` (4 files)

---

## Appendix B — Method

**Phase 1**: Surface inventory via `find` + `grep` — 5 scripts, 12 schemas, 9 audit classes, 5 SKILLs.
**Phase 2**: 4 parallel subagents (silent-failure-hunter for create/apply/menu-trio; architect for schemas).
**Phase 3**: Full pytest baseline on VPS sandbox `/tmp/catering_e2e/` with `src/` symlinked to `staging-new`. 63 fail / 44 pass.
**Phase 4**: 15-scenario harness covering corruption / replay / boundary / disabled-config / missing-config.
**Phase 5**: Live state audit on `/opt/shift-agent/state/catering-*` and `decisions.log`.

Total compute: ~10 minutes wall-clock; ~250K tokens across 4 subagents.
