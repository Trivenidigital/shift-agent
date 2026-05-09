# Agent #32 v0.1 — extend lookup-prior-leads-by-phone with `most_recent_notes` (plan)

**Drift-check tag:** `extends-Hermes`

**This is the v2 plan** for Agent #32 v0.1. The original plan
(`tasks/audits/agent-32-original-plan-rejected.md`) proposed a parallel
`SpecialRequestMemoryStore` + new `lookup-special-request` script (~480 LOC).
2 parallel plan reviewers raised 2 BLOCKERs (R1: extend existing lookup
instead; R2: notes name-collision risk) + 5 MEDIUMs that all collapsed under
R1's pivot proposal. User approved option A (the pivot) on 2026-05-09.

**v2 scope: ~50 LOC**, no new schema, no new audit variant, no new config
flag, no new lookup script. Extend the deployed
`lookup-prior-leads-by-phone` to surface `most_recent_notes` from the
most-recent non-terminal CateringLead, and patch
`parse_catering_inquiry/SKILL.md` Step 0 to consume it as a soft-prior
alongside dietary inheritance.

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-32-extend-lookup-prior-leads-with-notes.json`
(timestamp 2026-05-09T19:59:17Z, drift-tag = extends-Hermes, 9 [Hermes] / 2 [net-new]).

**v0.2 promoted to "when there's a real consumer":** the agent-shape work
(own state store + write path + dispatcher row + owner WhatsApp command)
is deferred until Order Accuracy (#30) or Kitchen Load Balancer (#31) is
scoped, both currently POS-blocked per
`memory/project_portfolio_status.md`.

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Customer WhatsApp inquiry | `[Hermes]` | Source ingestion |
| 2 | Dispatcher routing | `[Hermes]` | Already deployed |
| 3 | catering_dispatcher → parse_catering_inquiry | `[Hermes]` | Skill chaining |
| 4 | Step 0 `lookup-prior-leads-by-phone` invocation | `[Hermes]` | Already deployed |
| 5 | **Extend lookup return dict with `most_recent_notes`** | **`[net-new]`** | ~5 LOC: extract `extracted.notes` from the most-recent non-terminal lead, add to `_empty_result()` shape + `ok` branch |
| 6 | **SKILL.md Step 0 prose patch: consume `most_recent_notes` as soft-prior** | **`[net-new]`** | ~10 LOC: add row to `lookup_status` table, add merge-convention prose with explicit "Hard rule: NEVER emit priors back into Step 1 `notes` extraction output" guard against the R1-BLOCKER-2 leak risk |
| 7 | Step 1 LLM extraction (with prior in context) | `[Hermes]` | LLM gateway |
| 8 | `create-catering-lead` state write | `[Hermes]` | Already deployed |
| 9 | Tests: extend `tests/test_lookup_prior_leads.py` with notes coverage | `[Hermes]` | pytest infra; ~30-40 LOC of test additions, no new fixtures |
| 10 | Tarball + scp + deploy | `[Hermes]` | Existing pipeline; script + SKILL propagate via existing rsync/install lines |
| 11 | v0.2 owner-command + own-store | DEFERRED | Out of scope until kitchen-ops materializes |

9/11 `[Hermes]`, 2/11 `[net-new]`. Far below the 50% red-flag threshold.

**Awesome-hermes-agent ecosystem check:** N/A — no upstream skill provides
per-customer free-form notes inheritance for catering. The deployed
`lookup-prior-leads-by-phone` is already the "Hermes substrate" for this
class of soft-prior; we extend it.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 (script extension + SKILL prose work + test work).
Files Read this session before drafting:

- ✅ Read `src/agents/catering/scripts/lookup-prior-leads-by-phone` lines 1-180 — confirmed `_empty_result(status)` shape (lines 107-115) is the exact structure to extend; the `ok` branch is the natural insertion point for the `most_recent_notes` derivation; no new lock convention or import needed
- ✅ Read `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` lines 63-145 — Step 0 lookup_status table at lines 86-93 is the natural insertion point for the new field row; the existing "Hard rule" at lines 95-98 ("priors NEVER appear in any string sent to the customer or written to `--raw-inquiry`") is the precedent for v2-plan's required leak-guard hardening; Step 1 extracts a structural `notes` field at line 122 — the prose patch must explicitly forbid the LLM from emitting priors into that output
- ✅ Read `src/platform/schemas.py` lines 1670-1706 (`_BaseEntry` patterns), lines 416-437 (DailyBriefConfig — used as reference even though no config block ships in v2)
- ✅ Read `src/platform/safe_io.py` lines 255-309 (`load_model`, `customer_now`) — no changes needed, existing helpers cover the extension
- ✅ Read `tests/test_lookup_prior_leads.py` lines 1-50 — confirmed importlib `spec_from_file_location` + `module_from_spec` + `exec_module` pattern (lines 36-43); `_seed_leads` helper at lines 46-50 already supports extracting `extracted.notes` from `CateringLead.extracted` field — no new helper required, just additional test cases

**Deployed-pattern compliance:**
- Storage: no new state file ✓
- Schemas: no new schema ✓ (the `most_recent_notes` field on the return dict mirrors the existing `most_recent_dietary_restrictions` pattern at the dict level)
- Lock convention: no new lock ✓
- SKILL.md prose: "Hard rule" framing, lookup_status table extension, soft-prior merge convention, and explicit no-leak guard ✓
- Audit chain: no new audit variant ✓ (matches deployed convention of zero-audit on lookup; future P1.4 `lookup_invoked` will cover this lookup uniformly)
- Test pattern: extension to existing test file ✓ (no new test infra)

---

## Scope boundary (anti-over-engineering)

### In scope (~50 LOC across 3 files)

| File | Change | LOC |
|---|---|---|
| `src/agents/catering/scripts/lookup-prior-leads-by-phone` | Add `most_recent_notes: str` field to `_empty_result()` shape; in the `ok` branch, set it to the most-recent non-terminal lead's `extracted.notes` (or empty string when absent) | ~5 |
| `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` | Step 0 patch: add `most_recent_notes` row to lookup_status table; add merge-convention prose with explicit "Hard rule: NEVER emit priors back into Step 1 `notes` extraction output" guard | ~10 |
| `tests/test_lookup_prior_leads.py` | Add ~3-4 test cases: most_recent_notes returned correctly when most-recent lead has notes; empty string when absent; ignored when most-recent lead is in terminal status; long-notes truncation behavior (if any) | ~30-40 |
| **No new schema file**, **no new lookup script**, **no new audit variant**, **no new config block**, **no deploy.sh change** | | 0 |

### Schema/return-shape change (locked at plan time)

```python
# In _empty_result() at src/agents/catering/scripts/lookup-prior-leads-by-phone:107-115
def _empty_result(status: str) -> dict:
    return {
        "lookup_status": status,
        "prior_lead_count": 0,
        "most_recent_status": None,
        "most_recent_event_date": None,
        "most_recent_dietary_restrictions": [],
        "most_recent_notes": "",   # NEW — empty string, never None, matches the dietary list-not-None convention
        "last_seen_days_ago": None,
    }
```

**SKILL.md Step 0 patch shape:**

```markdown
| `lookup_status` | What it means | What to do |
|---|---|---|
| `ok` | Phone matched ≥1 prior lead | Use `most_recent_status`, `last_seen_days_ago`, `most_recent_dietary_restrictions`, AND `most_recent_notes` as **soft priors**... |

**Hard rule (already present, reinforced):** the prior fields...
**Hard rule (NEW v2 — R1-BLOCKER-2):** `most_recent_notes` is for Step 1's
extraction CONTEXT only. The LLM MUST NOT echo any portion of
`most_recent_notes` back into its Step 1 extraction output's `notes`
field — that would persist priors to lead state via `--raw-inquiry`,
violating the "priors NEVER leave this SKILL's reasoning" rule. If the
prior contains "regular customer prefers extra-spicy", that influences
the extraction's understanding but does NOT appear in the new lead's
`notes` field. The new lead's `notes` reflects only what THIS message
says.
```

### Explicitly out of scope (rejected at plan time)

| Considered | Decision | Reason |
|---|---|---|
| `SpecialRequestMemoryStore` + own state file | **REJECTED** | R1-BLOCKER-1: existing `lookup-prior-leads-by-phone` is the substrate. Parallel store is over-engineering. |
| `lookup-special-request` standalone script | **REJECTED** | R1-BLOCKER-1: extension is ~5 LOC; standalone is ~250 LOC. |
| `SpecialRequestMemoryConfig` + opt-in flag | **REJECTED** | No new behavior to gate; the lookup either returns the field or it doesn't. SKILL.md consumes optionally (empty string is a no-op prior). |
| `CustomerPreference` schema with structured `preferences: list[str]` | **REJECTED** | Catering inquiry's free-form `notes` already captures "no-onion / extra-spicy" intent organically; no need for a structured per-preference enum until #30/#31 require machine-readable preference matching at order time. |
| `SpecialRequestLookup` audit variant | **REJECTED** | Matches deployed convention (no audit on `lookup-prior-leads-by-phone`). P1.4 will add `lookup_invoked` for both lookups uniformly. |
| Aggregate ALL prior leads' notes into one merged-notes string | **REJECTED** | `most_recent_notes` only — keeps semantics simple; aggregation is reviewer-baited (deduplication, ordering, recency-weighting). v0.2/v0.3 if needed. |

### Deferred (separate commits if ever needed)

- v0.2: own state store + write script + owner WhatsApp command (when `#30`/`#31` materializes)
- v0.2: machine-readable `preferences: list[str]` field on `CateringLead.extracted` for downstream agents
- P1.4 follow-up: `lookup_invoked` audit variant for both lookups
- v0.3: aggregated multi-lead notes / TTL / cleanup

---

## Verification + commit shape

- **Run on srilu**: `pytest tests/test_lookup_prior_leads.py -v` (existing 22 tests + ~3-4 new) against tarballed working tree
- **Pass criterion**: 25-26 tests pass on first run; existing 22 still 100% green (zero regression)
- **Commit shape**: ONE commit, message `feat(agent-32): extend lookup-prior-leads-by-phone with most_recent_notes (v0.1, option A pivot)`, ~50 LOC across 3 files
- **Deploy notes**:
  - `shift-agent-deploy.sh:202-204` install glob picks up the modified script
  - SKILL.md propagates via existing rsync
  - No new schema export, no new install line, no new systemd unit
  - Default behavior is unchanged for catering inquiries with no prior leads (empty string returned, prose merge is a no-op)
  - For inquiries with prior leads: the LLM gets one extra context field; behavior change is small and bounded by SKILL.md "Hard rule" guard

---

## Approval needed

Plan reviewers approval was already granted via the user's "option A" choice
(2026-05-09) following the original-plan review cycle. Proceeding directly
to design phase.

Specific decisions for design reviewers to challenge:

1. **`most_recent_notes` empty-string default vs `None`** — chose empty string to match the `most_recent_dietary_restrictions: []` (empty list, not None) convention. Reviewers can flip if `None` is preferred.
2. **Source: `extracted.notes` field vs synthesized "summary"** — chose raw `extracted.notes` for v0.1 (no LLM-synthesis of multi-lead summaries). v0.2 can add aggregation if useful.
3. **Filter: most-recent NON-TERMINAL lead vs most-recent of any status** — chose non-terminal (matches `most_recent_status` semantics already in the script). Reviewers can flip if including terminal leads gives better priors.
4. **Truncation: should `most_recent_notes` be capped (e.g., 200 chars) before returning?** Plan-time decision: no truncation. The schema's `extracted.notes` already has a max, so the field is bounded upstream. Reviewers can challenge.
5. **SKILL.md leak-guard placement** — added as a NEW "Hard rule" line, not folded into the existing one. Reviewers can suggest folding for prose conciseness.
