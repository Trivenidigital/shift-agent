# Catering Production Readiness Re-Verification — 2026-07-01

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:** none. This is a read-only re-verification against
`origin/main` @ `48fe224` + deployed main-vps state. No code or state mutated.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Catering intake/routing/quote/finalize | Deployed `catering_dispatcher` + catering scripts + LogEntry variants | Verify deployed substrate; no new primitive |
| Audit replay | Existing `LogEntry` discriminated union + `_UnknownLogEntry` forward-compat shim | Verify replay safety of deployed shim |
| Runtime observability | Existing `catering-pattern-report`, `decisions.log`, `catering-leads.json` | Read-only inspection of deployed state |

Awesome-Hermes-Agent ecosystem check: no turnkey capability replaces the repo-
native catering state/audit model; this is a verification pass, not new build.

---

## VERDICT: ✅ CORE CLEAN — deposit feature carries LOW-MEDIUM operational risks (operator-actionable, fail-closed)

The catering agent **core flow** (intake → proposal → owner approval → quote send →
finalize → operator reconcile) is **production-ready** on current `main`. Schema is
backward-compatible, the audit log is replay-safe, active quote variants are intact,
and the catering suite is green. Nothing blocks the core flow.

The **deposit feature** (slice 2) is code-correct (fail-closed, replay-safe, never
rolls back the quote-send) but carries **two coupled LOW-MEDIUM operational risks**
in its *current runtime state* — neither a core-flow blocker, both operator-actionable:
1. **Armed-but-unconfigured** (LOW-MEDIUM): deployed `deposit_pct=0.25` with no
   `payment_checkout_url_template` → next qualifying lead gets the fail-closed
   "not configured yet" copy.
2. **Unconfigured-send dead-end** (MEDIUM, reviewer-confirmed): that copy is an
   unfulfillable promise — the intent persists, re-invoke no-ops, no auto-remint. Manual
   remediation only (now documented; slice-3 tool tracked).

**Recommended operator action:** if deposits aren't ready, set `cfg.catering.deposit_pct: 0`
(kill switch) **before** the next qualifying lead. See deposit plan §2.

---

## Evidence by dimension

### 1. State / schema compatibility — CLEAN
- `CateringLead` deposit fields (`schemas.py:2088-2101`) are **all default-valued**
  (`deposit_required=False`, `deposit_amount_cents=0`, `deposit_status="none"`,
  etc.) → legacy lead JSON written before these fields existed decodes unchanged.
- `mode="before"` backfill validator (`schemas.py:2111`) protects legacy
  post-AWAITING leads with empty `quote_text` (sentinel backfill), so v0.3 strict
  validation does not reject legacy data.
- Deposit config fields present with sane defaults: `deposit_threshold_guests=50`
  (`schemas.py:582`), `deposit_pct=0.25` (`:583`), `payment_checkout_url_template=""`
  (`:899`, `:2374`).

### 2. Audit chokepoint + LogEntry replay safety — CLEAN
- PR-D4 (`48fe224`) **removed** the `CateringQuoteRenderFailed` variant + the
  obsolete PR-D3 absorbing shim. Replay of any historical
  `"type":"catering_quote_render_failed"` row is safe: the callable
  `Discriminator(_pick_log_entry_tag)` (`schemas.py:5955`) routes unknown/removed
  tags to `_UnknownLogEntry` (`:3274`, `type: str` + `extra="allow"`) instead of
  raising a `ValidationError`.
- **Zero dangling references** to the removed variant: grep
  `CateringQuoteRenderFailed|catering_quote_render_failed` across `src/` + `tests/`
  = empty. Removal is complete.
- All three deposit variants are registered in the union:
  `CateringDepositLinkSent` (`:5947`), `CateringDepositLinkFailed` (`:5948`),
  `CateringDepositPaid` (`:5950`).
- Production `decisions.log` on main-vps: **0** `catering_deposit_link_*` rows →
  no removed-tag or deposit rows to stress replay in the field anyway.

### 3. Active quote / deposit variants — CLEAN
- Active quote variants present: `CateringQuoteDrafted`, `CateringQuoteSent`,
  `CateringQuoteAttempted` (idempotency), `CateringQuoteSentLeadMissing`,
  `CateringQuoteSkillFailed`, `CateringLeadStatusChange`.
- Deposit path emitter is single + deterministic: `catering-mint-deposit`
  (subprocess-invoked by `apply-catering-owner-decision` only when
  `_should_mint_deposit(cfg, lead)` is True; hook "NEVER raises; failures are
  non-fatal" — `apply-catering-owner-decision:901,926`).
- Fail-closed unconfigured copy (`"Payment link is not configured yet…"`) is
  byte-exact and regression-locked (`test_catering_deposit_copy_invariants.py`,
  incl. no-PII-leak assertions).

### 4. Current tests + known skips — CLEAN (0 failures)
- `pytest -k "catering or deposit"`: **308 passed, 210 skipped, 0 failed** (7.1s).
- The 210 skips are **platform-gated, not logic gaps**: `safe_io`/`fcntl` is
  Linux-only → catering-script tests skip on the Windows dev box but run on the
  Linux CI (where `send-path-ci` @ `48fe224` is **green**). A handful are opt-in
  real-LLM replay tests (`HERMES_REPLAY_MODEL`/`OPENROUTER_API_KEY` unset).
- Post-merge `send-path-ci` on `origin/main` @ `48fe224` = **success** (verified
  in prior thread: run `28484423007`).

### 5. Runtime-state assumptions (read-only main-vps inspection) — 1 LOW-MEDIUM finding
- Deployed `catering.deposit_pct: 0.25` (**armed**, >0) + `deposit_threshold_guests: 50`;
  `commerce.payment_checkout_url_template` **unset** (`""`).
- Deposit hook is fully deployed + wired (`/opt/shift-agent/deposit.py`,
  `/usr/local/bin/catering-mint-deposit`, apply-script hook, all dated Jun 30
  deploy) but has **never fired** (0 audit rows, no `deposit_status` on any lead).
- **Finding:** next qualifying (≥50-guest, quoted, owner-approved) lead will mint
  an order+intent and send the fail-closed "not configured yet" copy. Non-crashing;
  a confusing customer message + orphan order per qualifying lead. **Operator
  action:** configure the template *or* set `deposit_pct=0` (kill switch). See
  `tasks/catering-deposit-backlog-plan-2026-07-01.md` §2. Not a code change; not a
  core-flow blocker.

### 6. Drift since the 2026-06-06 production-readiness note — CLEAN
- Only two catering-surface commits since 2026-06-06: `48fe224` (PR-D4 dead-code
  removal — verified clean above) and `b082b23` (test calendar time-bomb fix,
  unrelated CI hygiene). All other schema-touching commits are **Flyer-only**
  (added Flyer LogEntry variants, isolated from catering union members).
- The 2026-06-06 reconcile fix (`CUSTOMER_FINALIZED_ALLOWED_TARGETS`) is present on
  `main` (`catering-lead-reconcile`, 3 refs).
- Deployed box is at `c529876`; `main` is one commit ahead at `48fe224` (PR-D4
  dead-code removal, not yet deployed). Expected — functionally identical for the
  live path; safe to deploy whenever.

---

## Files inspected
- `src/platform/schemas.py` (CateringLead deposit fields; deposit + quote LogEntry
  variants; `_UnknownLogEntry` / `_pick_log_entry_tag` / `Discriminator` union)
- `src/agents/catering/deposit.py`
- `src/agents/catering/scripts/apply-catering-owner-decision` (deposit hook)
- `src/agents/catering/scripts/catering-mint-deposit` (emitter, tz-aware ts)
- `src/agents/catering/scripts/catering-lead-reconcile` (June-6 fix present)
- `docs/runbooks/commerce-deposit-onboarding.md`
- `tasks/commerce-slice2-catering-deposit-followup-backlog.md`,
  `tasks/catering-production-readiness-2026-06-06.md`
- Deployed main-vps (read-only): `config.yaml` deposit keys, `decisions.log`
  deposit rows, `catering-leads.json` deposit_status, deposit binary/hook presence

## Tests run
- `pytest tests/ -k "catering or deposit" -q` → 308 passed, 210 skipped, 0 failed
- Skip-reason audit (`-rs`) → all platform-gated (Linux `fcntl`) or opt-in real-LLM

## Drift found
- None functional. PR-D4 removed dead code only (replay-safe). Deposit backlog has
  drifted vs its 2026-05-29 capture — most items already done or slice-3-gated (see
  the deposit plan doc §1).

## Adversarial review (independent subagent, 25 tool-calls, files traced end-to-end)
All four core claims **HOLD** with file:line evidence:
1. **Replay safety** HOLDS — callable `_pick_log_entry_tag` returns the tag only if
   `t in _KNOWN_LOG_ENTRY_TYPES` (introspected at import), else the `"_unknown_"`
   sentinel → `_UnknownLogEntry` (`extra="allow"`, only required field `ts`, which
   every historical row carried). No path where a removed/unknown tag raises.
2. **No dangling refs** HOLDS — repo-wide grep = 0 matches.
3. **Deposit-field back-compat** HOLDS — all deposit fields defaulted; `extra="forbid"`
   rejects *extra* keys, and legacy rows have *fewer* keys, so they validate.
4. **Deposit fail-closed** HOLDS — hook runs *outside/after* the `FileLock` quote-send
   transaction, is exhaustively guarded (import/binary/subprocess/timeout/catch-all all
   non-fatal), and empty template → byte-exact `_render_unconfigured_reply`, not a
   broken URL.

**Reviewer's additional findings (verified first-hand by me):**
- **MEDIUM — unconfigured-send dead-end (CONFIRMED).** When the template is empty the
  bridge POST *succeeds*, so `mark_sent` runs and the lead is persisted with
  `deposit_payment_intent_id` set + `deposit_status="unconfigured"`
  (`catering-mint-deposit:362-379`). Re-invoke then short-circuits at
  `noop: already_minted` (`:184`). There is **no `--force`/`--remint` flag** and **no
  `unconfigured→awaiting_payment` transition**, so configuring the template later does
  NOT auto-deliver the real link — manual void+clear is required. This also made the
  onboarding runbook's Step 5/6 remediation inaccurate. **Fixed this pass** — see
  runbook Steps 5, 6, 6a + the ordering callout; design gap tracked as a slice-3
  remint/void operator tool in the deposit plan §4.
- **LOW — `extra="forbid"` not rollback-safe (new→old).** A deposit-bearing lead read
  by an older binary would `ValidationError`. Pre-existing pattern (same as
  `selected_items`/`quote_total_usd`); only matters if a post-deposit rollback is a
  real scenario. Not deposit-specific; not a blocker.
- **NIT — `_should_mint_deposit(cfg, lead)` at apply:926 is outside try/except.** Safe
  in practice (validated `Config`/`CateringLead`); the "NEVER raises" contract rests on
  the upstream typing invariant. Not a blocker.
