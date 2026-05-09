# Agent #32 v0.1 — extend lookup-prior-leads-by-phone (design doc)

**Drift-check tag:** `extends-Hermes`

This is the design phase for the approved v2 plan at
`tasks/agent-32-extend-lookup-prior-leads-with-notes-plan.md`. v2 plan
emerged from the original-plan reviewers' BLOCKERs (option A pivot per
2026-05-09 user choice).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/agent-32-extend-lookup-prior-leads-with-notes-design.json`
(timestamp 2026-05-09T20:02:51Z, drift-tag = extends-Hermes, 8 [Hermes] / 3 [net-new]).

3/11 [net-new] — same logical scope as the v2 plan (which counted 2/11);
the difference is design-granularity decomposition of `_empty_result`
field-add vs ok-branch derivation as separate sub-steps. No additional
substrate use missed.

---

## Hermes-first per-step checklist (design granularity)

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | Customer inquiry → bridge | `[Hermes]` | Source ingestion |
| 2 | Dispatcher routing | `[Hermes]` | Already deployed |
| 3 | catering_dispatcher → parse_catering_inquiry | `[Hermes]` | Skill chaining |
| 4 | Step 0 lookup invocation | `[Hermes]` | subprocess call already deployed |
| 5 | **`_empty_result()` field addition** | **`[net-new]`** | 1 LOC, dictionary key |
| 6 | **`ok`-branch derivation of `most_recent_notes`** | **`[net-new]`** | ~3 LOC inside the existing `lookup_prior_leads_by_phone` function |
| 7 | **SKILL.md Step 0 prose patch + new Hard Rule** | **`[net-new]`** | ~10 LOC SKILL prose |
| 8 | LLM extraction with priors | `[Hermes]` | LLM gateway |
| 9 | LLM honors prose Hard Rule (no leak) | `[Hermes]` | substrate behavior; SKILL is contract layer |
| 10 | `create-catering-lead` write | `[Hermes]` | Already deployed |
| 11 | Tests in existing test file | `[Hermes]` | pytest infra; case curation only |

8/11 `[Hermes]`, 3/11 `[net-new]`. Below the 50% threshold.

---

## Drift-rule self-checks

All required reads done at plan + design time:

- ✅ Read `src/agents/catering/scripts/lookup-prior-leads-by-phone` lines 1-180 + lines 180-275 — verified the `ok` branch returns a dict LITERAL (lines 236-243), not a call to `_empty_result()`. So design needs to add the field in BOTH places. `most_recent` lead is computed at line 222-223 via sort-descending-by-created_at; `most_recent.extracted.notes` is the natural source.
- ✅ Read `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` lines 63-145 — confirmed Step 0 prose convention + existing "Hard rule" at lines 95-98 ("priors NEVER appear in any string sent to the customer or written to `--raw-inquiry`. They never leave this SKILL's reasoning."). Design's NEW Hard Rule will explicitly extend this to the Step-1 `notes` extraction output.
- ✅ Read `src/platform/schemas.py` lines 594-605 — `CateringLeadExtractedFields.notes: str = ""` has NO `max_length` cap; design accepts unbounded source field per the v2-plan rejection of truncation. The LLM-output bound is the de facto cap.
- ✅ Read `tests/test_lookup_prior_leads.py` lines 1-50 — confirmed `_seed_leads` helper writes `CateringLeadStore` JSON; design's new test cases will use this helper unchanged.

**Deployed-pattern compliance:**
- Return shape: dict-key addition, no schema migration ✓
- `ok` branch: same single-most-recent-lead semantics that drive the existing fields ✓
- SKILL prose: Hard Rule pattern matches lines 95-98 verbatim ✓
- Tests: extension of existing file, no new infra ✓

---

## Code-level design

### 1. `src/agents/catering/scripts/lookup-prior-leads-by-phone` — two edits

**Edit (a)** — `_empty_result()` at lines 107-115. Add `most_recent_notes: ""` between `most_recent_dietary_restrictions` and `last_seen_days_ago` (same alphabetical-by-prefix grouping):

```python
def _empty_result(status: str) -> dict:
    return {
        "lookup_status": status,
        "prior_lead_count": 0,
        "most_recent_status": None,
        "most_recent_event_date": None,
        "most_recent_dietary_restrictions": [],
        "most_recent_notes": "",   # NEW v0.1 — empty string mirrors empty-list convention for dietary
        "last_seen_days_ago": None,
    }
```

**Edit (b)** — `ok`-branch dict literal at lines 236-243. Add `most_recent_notes` derived from the most-recent lead's `extracted.notes`, **truncated at 500 chars** per R1-MEDIUM:

```python
return {
    "lookup_status": LOOKUP_STATUS_OK,
    "prior_lead_count": len(matches),
    "most_recent_status": str(most_recent.status),
    "most_recent_event_date": most_recent.extracted.event_date,
    "most_recent_dietary_restrictions": list(most_recent.extracted.dietary_restrictions),
    "most_recent_notes": (most_recent.extracted.notes or "")[:500],   # NEW v0.1; cap prevents prompt-context inflation since schemas.py:605 has no max_length
    "last_seen_days_ago": days_ago,
}
```

The `or ""` guard is defensive: `extracted.notes` defaults to `""` per
`schemas.py:605`, but a hand-edited or future-schema-evolved leads.json could
have `None` — the `or ""` collapses to empty string in either case.

The `[:500]` truncation (R1-MEDIUM) bounds prompt-context inflation. The
source field has no `max_length` cap; capping in the lookup return keeps
the LLM context small even if a prior customer wrote a 5-paragraph
backstory. No ellipsis appended (the LLM can infer truncation from
context); add `... [truncated]` in v0.2 if reviewers prefer.

**No `most_recent` filtering change.** The existing logic at lines 218-223
already sorts by `created_at` descending and picks `matches[0]`. For v0.1 we
return the most-recent lead's `notes` regardless of status (terminal or
non-terminal). Plan §"Approval needed" decision-3 surfaced this; choosing
"most-recent of any status" because:
- The existing `most_recent_status` field already returns terminal statuses
  too (lines 233-235 enumeration includes `OWNER_REJECTED`, `CLOSED`, `STALE`)
- Filtering only-non-terminal would create asymmetry within the return dict
- Customer's stated preferences in their last actual message (even if that
  inquiry got CLOSED or STALE) is still useful prior-context for the new one

If reviewers flip this decision, the change is a one-line `if most_recent.status not in TERMINAL_STATUSES` filter.

### 2. `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` — two prose patches

**Patch (a)** — extend the lookup_status table at line 88. The `ok` row currently lists `most_recent_status, last_seen_days_ago, most_recent_dietary_restrictions`; add `most_recent_notes`:

```markdown
| `ok` | Phone matched ≥1 prior lead | Use `most_recent_status`, `last_seen_days_ago`, `most_recent_dietary_restrictions`, AND `most_recent_notes` as **soft priors** for Step 1 extraction (e.g., if the prior `most_recent_notes` mentions "extra-spicy preference" you MAY treat that as soft-prior context for the new inquiry's interpretation, but NEVER override explicit current-message content). DO NOT echo any prior detail to the customer. |
```

**Patch (b)** — add a new "Hard rule" line after the existing block at lines 95-98, BEFORE Step 1 begins (around line 105). This is the R1-BLOCKER-2 leak guard:

```markdown
**Hard rule (NEW v0.1):** `most_recent_notes` is for Step 1's extraction
CONTEXT only. The LLM MUST NOT echo any portion of `most_recent_notes`
back into its Step 1 extraction output's `notes` field — that would
persist priors to lead state via `--raw-inquiry`, violating the "priors
NEVER leave this SKILL's reasoning" rule above. If the prior says
"regular customer prefers extra-spicy", that may shape your
understanding of "we want it like usual" in the new message, but does
NOT appear in the new lead's `notes` field. The new lead's `notes`
reflects ONLY what THIS message says.
```

### 3. `tests/test_lookup_prior_leads.py` — extend `_mk_lead` + 4 new test cases

**R2-BLOCKER fix**: the existing `_mk_lead()` helper at lines 55-79 produces
a fully-populated `CateringLead`-shaped dict (with required fields:
`customer_name`, `raw_inquiry`, `original_message_id`, `quote_text`,
`quote_version`, `owner_approval_code`, `customer_replied`). The design's
test cases MUST use `_mk_lead` rather than passing truncated dicts directly
to `_seed_leads` — `CateringLead` is `extra="forbid"` and rejects
incomplete dicts at lookup time (load_model validation), producing
`lookup_status="corrupt"` instead of the expected `ok`.

**Edit `_mk_lead`** to accept a `notes: str = ""` kwarg:

```python
def _mk_lead(
    *, lead_id: str, phone: str, status: str = "AWAITING_OWNER_APPROVAL",
    created_at: datetime, event_date: str | None = None,
    dietary: list[str] | None = None,
    notes: str = "",   # NEW v0.1 — passes through to extracted.notes
) -> dict:
    """Construct a minimal CateringLead dict matching the schema."""
    return {
        ...
        "extracted": {
            "headcount": 30,
            "event_date": event_date,
            "dietary_restrictions": dietary or [],
            "notes": notes,   # NEW v0.1
        },
        ...
    }
```

**Cases:**

```python
# Case 1: most_recent_notes returned for the most-recent lead (sort-direction pin)
def test_most_recent_notes_returned_for_recent_lead(env_dir):
    """most_recent_notes is the notes string from the most-recent lead by created_at."""
    _seed_leads(env_dir, [
        _mk_lead(lead_id="L0001", phone="+15555550100",
                 created_at=datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
                 status="CLOSED", notes="old note"),
        _mk_lead(lead_id="L0002", phone="+15555550100",
                 created_at=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
                 status="AWAITING_OWNER_APPROVAL",
                 notes="wants extra-spicy + no-onion"),
    ])
    mod = _load_script()
    result = mod.lookup_prior_leads_by_phone(
        "+15555550100",
        leads_path=env_dir / "state" / "catering-leads.json",
    )
    assert result["most_recent_notes"] == "wants extra-spicy + no-onion"


# Case 2: empty string when most-recent lead has empty notes (default)
def test_most_recent_notes_empty_when_lead_has_no_notes(env_dir):
    _seed_leads(env_dir, [
        _mk_lead(lead_id="L0001", phone="+15555550100",
                 created_at=datetime(2026, 5, 1, 12, tzinfo=timezone.utc)),
        # notes default = "" via _mk_lead default
    ])
    mod = _load_script()
    result = mod.lookup_prior_leads_by_phone(
        "+15555550100",
        leads_path=env_dir / "state" / "catering-leads.json",
    )
    assert result["most_recent_notes"] == ""


# Case 3: empty string in _empty_result no-match path (covers field presence in fallback)
def test_most_recent_notes_empty_when_no_match(env_dir):
    _seed_leads(env_dir, [
        _mk_lead(lead_id="L0001", phone="+15555550999",  # different phone
                 created_at=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
                 notes="different customer"),
    ])
    mod = _load_script()
    result = mod.lookup_prior_leads_by_phone(
        "+15555550100",
        leads_path=env_dir / "state" / "catering-leads.json",
    )
    assert result["lookup_status"] == "no_match"
    assert result["most_recent_notes"] == ""


# Case 4: returned even when most-recent lead is in terminal status
def test_most_recent_notes_returned_for_terminal_lead(env_dir):
    """v0.1 design pin: most_recent_notes follows the most-recent lead by
    created_at regardless of status. Symmetric with how most_recent_status
    itself surfaces terminal statuses (CLOSED/STALE/etc.)."""
    _seed_leads(env_dir, [
        _mk_lead(lead_id="L0001", phone="+15555550100",
                 created_at=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
                 status="STALE", notes="had asked for jain food"),
    ])
    mod = _load_script()
    result = mod.lookup_prior_leads_by_phone(
        "+15555550100",
        leads_path=env_dir / "state" / "catering-leads.json",
    )
    assert result["most_recent_status"] == "STALE"
    assert result["most_recent_notes"] == "had asked for jain food"


# Case 5 (NEW per R1-MEDIUM): truncation at 500 chars
def test_most_recent_notes_truncated_at_500_chars(env_dir):
    """Source field has no max_length; lookup output caps at 500 to bound
    LLM-prompt context inflation."""
    long_note = "x" * 2000
    _seed_leads(env_dir, [
        _mk_lead(lead_id="L0001", phone="+15555550100",
                 created_at=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
                 notes=long_note),
    ])
    mod = _load_script()
    result = mod.lookup_prior_leads_by_phone(
        "+15555550100",
        leads_path=env_dir / "state" / "catering-leads.json",
    )
    assert len(result["most_recent_notes"]) == 500
    assert result["most_recent_notes"] == "x" * 500
```

**LOC estimate**: 5 cases × ~15-20 LOC + `_mk_lead` 1-line extension ≈ 80-100 LOC of test additions. Still under original 480-LOC scope by 4-6×.

---

## Risks identified at design time

| Risk | Mitigation |
|---|---|
| `most_recent.extracted.notes` is `None` in some legacy lead | `or ""` guard at the assignment site collapses to empty string |
| LLM ignores the new Hard Rule and echoes prior into Step 1 `notes` extraction output | Behavioral contract via SKILL prose; no compile-time enforcement. Mitigation: spot-check first 5-10 production catering inquiries post-deploy to confirm the LLM honors the rule. P1.4 follow-up could add audit-emit for the `notes` field's pre/post-extraction content if leakage becomes a real problem. |
| Long `notes` strings inflate the LLM prompt context | Source field has no `max_length` cap (schemas.py:605), but LLM extraction is bounded by its output cap; in practice notes are short. Plan rejected truncation; design respects. If a customer with a 5K-char `notes` history surfaces, v0.2 can add truncation. |
| The most-recent lead being terminal-status surfaces stale preferences | Design decision: include terminal-status leads. Symmetric with `most_recent_status` already returning terminals. SKILL prose doesn't claim recency of preferences anyway. |
| Existing 22 tests in `test_lookup_prior_leads.py` rely on dict-shape that doesn't have `most_recent_notes` | They use `result["most_recent_status"]` etc. — they don't assert on the dict's COMPLETE keys. Adding a key is backward-compatible; existing tests should pass unchanged. Verify on first srilu run. |

---

## Verification + commit shape

- Run on srilu: `pytest tests/test_lookup_prior_leads.py -v` against tarballed working tree
- Pass criterion: 26 tests pass (22 existing + 4 new); zero regression on existing
- Commit shape: ONE commit, message `feat(agent-32): extend lookup-prior-leads-by-phone with most_recent_notes (v0.1, option A pivot)`, ~50-80 LOC across 3 files
- Pre-flight: verify the Step 0 SKILL prose patch flows correctly through deploy (rsync of SKILL.md preserves the new content)
- Deploy: tarball + scp + `shift-agent-deploy.sh deploy` — script + SKILL propagate via existing install/rsync lines

---

## Approval needed

Design reviewers must approve before build. Specific decisions to challenge:

1. **`most_recent.extracted.notes or ""` guard** — design choice for `None`-tolerance. Reviewers may prefer letting `None` propagate into the dict (and have SKILL prose treat it as no-prior).
2. **Most-recent regardless of status** vs filter to non-terminal only — chose include-terminal for symmetry with `most_recent_status`. Reviewers can flip.
3. **No truncation of `most_recent_notes`** — relies on LLM extraction bounding; v0.2 can add cap if needed. Reviewers can challenge.
4. **Hard Rule placement** — added as a NEW separate "Hard rule" block immediately after the existing leak-guard at lines 95-98. Could be folded into the existing rule for prose conciseness; design chose separation for emphasis on the v0.1 addition.
5. **4 test cases vs more** — design covers 4 paths (recent-with-notes, recent-empty-notes, no-match, terminal-status). Reviewers may want one more for the LLM-leak-guard verification (an integration test that runs Step 1 against a context with priors and asserts the resulting `--raw-inquiry` doesn't contain prior content) — but that's full LLM-call integration, expensive, and arguably v0.2 territory.
