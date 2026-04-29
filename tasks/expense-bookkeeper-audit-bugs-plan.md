# Expense Bookkeeper v0.1 — Audit-bug Fix Plan

**Drift-check tag:** `extends-Hermes` — surgical fixes to existing surfaces (dispatcher SKILL doc, schemas, template). No new substrate. No new external systems.
**Branch:** `fix/expense-bookkeeper-v01-audit-bugs` (created from `main` at `f4dab6f`)
**Source bug list:** `tasks/expense-bookkeeper-v01-audit-report.md`
**Status:** Stage 1 of 8 (planning).

## Read-deployed-code commitment (Part 3 working agreement)

Before drafting, I read:
- `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` lines 12–135 — full routing matrix + Step-3 grep block + delegation logic
- `src/platform/schemas.py` lines 884–928 (`ExpenseLead`) + 1186–1208 (`RawInbound` precedent for sender-id pattern) + 514–518 (`CateringLead.original_message_id` precedent)
- `src/agents/expense_bookkeeper/templates/expense_pushed_confirmation.txt` (full file, 7 lines)
- `src/agents/expense_bookkeeper/scripts/apply-expense-decision` lines 565–590 (where pushed-confirmation template is rendered)

Grounded in existing patterns; no SaaS-priors imported.

---

## Bugs to fix (4 total)

| # | Severity | Bug | Source |
|---|---|---|---|
| BUG-1 | HIGH | Dispatcher Step-3 `jq` lookup missing `expense-bookkeeper/leads.json` — routing matrix amended (rows 18,19,22) but the corresponding grep code (lines 81–83) wasn't. Owner replies route wrong. | `dispatch_shift_agent/SKILL.md` |
| BUG-2 | MED | `ExpenseLead.sender_phone: str` accepts empty string. `original_message_id` already has `Field(min_length=1)` in same class — `sender_phone` should match. | `schemas.py:889` |
| BUG-3 | LOW | `ExpenseLead.original_message_id` accepts embedded `\0` null byte. `image_path` validator already rejects null bytes; idempotency key should match for log-safety. | `schemas.py:888` |
| BUG-4 | LOW | `expense_pushed_confirmation.txt` uses `✓` (U+2713) emoji. `CLAUDE.md` says "Only use emojis if the user explicitly requests it." Originally flagged by reviewer-c during PR review; never fixed. | template file |

---

## Fix specifications

### BUG-1 (HIGH) — dispatcher Step-3 grep block amendment

**File:** `src/agents/shift/skills/dispatch_shift_agent/SKILL.md`

**Current state (lines 73–84):**
```bash
grep -oE '#[A-HJ-NP-Z2-9]{5}' <<<"<message_text>" | head -1   # extract first code
# Look up across the three pools, in this priority:
jq --arg c "$CODE" '.confirmation_code == $c' /opt/shift-agent/state/catering-menu-pending.json
jq --arg c "$CODE" '.leads[] | select(.owner_approval_code == $c) | select(.status != "CLOSED" and .status != "OWNER_REJECTED" and .status != "STALE")' /opt/shift-agent/state/catering-leads.json
jq --arg c "$CODE" '.proposals[] | select(.code == $c)' /opt/shift-agent/state/pending.json
```

**Required change:** insert one new `jq` line for `expense-bookkeeper/leads.json` BETWEEN the catering-leads line and the pending.json line, matching the priority order in the routing matrix (rows 17 → 18 → 20). Update prose comment "three pools" to "four pools" (or just drop the count).

**Status filter:** must mirror `EXPENSE_APPROVAL_CLOSED_STATUSES` in `schemas.py:865-867`:
```
{"PUSHED", "REVERSED", "REJECTED", "EXPIRED"}
```

**Concrete amendment:**
```bash
# Look up across the four pools, in this priority:
jq --arg c "$CODE" '.confirmation_code == $c' /opt/shift-agent/state/catering-menu-pending.json   # menu pending → apply_catering_menu_decision
jq --arg c "$CODE" '.leads[] | select(.owner_approval_code == $c) | select(.status != "CLOSED" and .status != "OWNER_REJECTED" and .status != "STALE")' /opt/shift-agent/state/catering-leads.json   # catering lead → handle_catering_owner_approval
jq --arg c "$CODE" '.leads[] | select(.owner_approval_code == $c) | select(.status != "PUSHED" and .status != "REVERSED" and .status != "REJECTED" and .status != "EXPIRED")' /opt/shift-agent/state/expense-bookkeeper/leads.json   # expense lead → expense_bookkeeper_dispatcher
jq --arg c "$CODE" '.proposals[] | select(.code == $c)' /opt/shift-agent/state/pending.json   # shift proposal → handle_owner_command
```

The first non-empty hit wins (existing dispatcher convention). Expense slots BETWEEN catering-leads and pending — same priority order as the routing matrix.

**Also:** the dispatcher SKILL has a regex `#[A-HJ-NP-Z2-9]{5}` on line 81 that DIFFERS from canonical `[A-HJKMNPQR-Z2-9]` in `schemas.py:843`. This is a pre-existing inconsistency that reviewer-a flagged in Stage 4 design review as LOW. Out of scope for this fix.

### BUG-2 (MED) — `sender_phone` empty-string rejection

**File:** `src/platform/schemas.py` (in `ExpenseLead` class, currently line 889)

**Change:**
```python
sender_phone: str
# becomes
sender_phone: str = Field(min_length=1)
```

**Rationale:** match the `original_message_id: str = Field(min_length=1)` precedent in the same class (line 888). One-word change.

**Considered but deferred:** the deeper refactor to `Optional[E164Phone]` + `sender_lid` + at-least-one validator (mirroring `RawInbound` at line 1191–1204) is correct in spirit but out of scope — it would require updating extract-receipt's persistence path + every test fixture. Flag as v0.2 candidate.

### BUG-3 (LOW) — `original_message_id` null-byte rejection

**File:** `src/platform/schemas.py` (in `ExpenseLead` class)

**Change:** add a `field_validator` mirroring `_path_under_managed_dir`'s null-byte check shape:

```python
@field_validator("original_message_id")
@classmethod
def _no_null_byte(cls, v: str) -> str:
    if "\0" in v:
        raise ValueError("invalid original_message_id: contains null byte")
    return v
```

**Rationale:** WhatsApp message IDs in practice never contain null bytes; defensive validation is cheap and prevents log corruption / NDJSON-parsing edge cases if a malformed id ever leaks through.

### BUG-4 (LOW) — drop `✓` from pushed-confirmation template

**File:** `src/agents/expense_bookkeeper/templates/expense_pushed_confirmation.txt`

**Current state (line 1):**
```
{{expense_id}} pushed to QuickBooks ✓
```

**Change:** remove the checkmark. New line 1:
```
{{expense_id}} pushed to QuickBooks
```

**Rationale:** `CLAUDE.md` "no emojis unless explicitly requested." Owners read the message content; the checkmark adds no info. Reviewer-c flagged this during the original PR review; never fixed.

---

## Tests to add

For each bug, a focused regression test:

### Test 1 — BUG-2: empty sender_phone rejected

`tests/test_expense_bookkeeper_guardrails.py`:

```python
def test_lead_sender_phone_empty_rejected():
    """BUG-2 (audit): sender_phone="" must be rejected. Mirrors
    original_message_id min_length=1 precedent in same class."""
    base = {
        "expense_id": "E0001",
        "original_message_id": "msg",
        "sender_phone": "",  # empty
        "received_at": "2026-04-29T12:00:00+00:00",
        "image_path": "/tmp/test/E0001.jpg",
        "image_phash": "a"*16, "image_byte_hash": "a"*64,
    }
    with pytest.raises(Exception, match="sender_phone"):
        ExpenseLead.model_validate(base)
```

### Test 2 — BUG-3: null byte in original_message_id rejected

`tests/test_expense_bookkeeper_guardrails.py`:

```python
def test_lead_original_message_id_null_byte_rejected():
    """BUG-3 (audit): null byte in original_message_id rejected.
    Mirrors image_path validator's null-byte check."""
    base = {
        "expense_id": "E0001",
        "original_message_id": "msg\0null",  # embedded null byte
        "sender_phone": "+19045550000",
        "received_at": "2026-04-29T12:00:00+00:00",
        "image_path": "/tmp/test/E0001.jpg",
        "image_phash": "a"*16, "image_byte_hash": "a"*64,
    }
    with pytest.raises(Exception, match="null byte"):
        ExpenseLead.model_validate(base)
```

### Test 3 — BUG-4: pushed-confirmation template has no emoji

`tests/test_expense_bookkeeper_guardrails.py`:

```python
def test_pushed_confirmation_template_no_emojis():
    """BUG-4 (audit): pushed-confirmation template must be ASCII-clean per
    CLAUDE.md no-emoji rule. Specifically must NOT contain U+2713 (✓)."""
    p = (Path(__file__).resolve().parent.parent / "src" / "agents"
         / "expense_bookkeeper" / "templates" / "expense_pushed_confirmation.txt")
    raw = p.read_text(encoding="utf-8")
    assert "✓" not in raw, "✓ checkmark must be removed (CLAUDE.md no-emoji rule)"
    # Em-dash (U+2014) is typography, not emoji — explicitly allowed
    non_ascii = {c for c in raw if ord(c) > 127 and c != "—"}
    assert non_ascii == set(), f"unexpected non-ASCII chars: {non_ascii}"
```

### Test 4 — BUG-1 confirmation (dispatcher jq amendment)

This is a SKILL.md (not Python) change, so traditional unit-test doesn't apply. Smoke-level verification:

`tests/test_expense_bookkeeper_guardrails.py`:

```python
def test_dispatcher_skill_includes_expense_jq_lookup():
    """BUG-1 (audit): dispatch_shift_agent SKILL.md Step-3 grep block
    MUST include a jq command for state/expense-bookkeeper/leads.json
    in priority order between catering-leads and pending."""
    p = (Path(__file__).resolve().parent.parent / "src" / "agents" / "shift"
         / "skills" / "dispatch_shift_agent" / "SKILL.md")
    raw = p.read_text(encoding="utf-8")
    # Must mention the expense leads file in the Step-3 jq block
    assert "expense-bookkeeper/leads.json" in raw, (
        "BUG-1: dispatcher SKILL must include expense-bookkeeper/leads.json lookup"
    )
    # Verify priority ordering: catering-leads BEFORE expense BEFORE pending
    catering_pos = raw.find("catering-leads.json")
    expense_pos = raw.rfind("expense-bookkeeper/leads.json")  # rfind to skip the matrix mention
    pending_pos = raw.rfind("pending.json")  # rfind to skip route_to mentions
    assert catering_pos < expense_pos < pending_pos, (
        f"BUG-1 priority order broken: catering={catering_pos} "
        f"expense={expense_pos} pending={pending_pos}"
    )
```

---

## Hermes-first capability matrix (per CLAUDE.md mandatory checklist)

| Step | `[Hermes]` or `[net-new]`? |
|---|---|
| Owner WhatsApp inbound, dispatcher route by sender_role + content | `[Hermes]` (existing) |
| Step-3 jq lookup across state files | `[Hermes]` (existing pattern; just add 4th line) |
| Schema field constraint (`min_length=1`) | `[Hermes]` (Pydantic feature, existing pattern in `original_message_id`) |
| Schema field validator (null-byte rejection) | `[Hermes]` (Pydantic feature, existing pattern in `image_path`) |
| Template edit (drop checkmark) | `[Hermes]` (no logic change) |

**Net-new tally: 0.** All fixes are amendments to existing substrate; no new infrastructure, no new external systems, no new patterns introduced.

---

## File-level changes

| File | Type | Change size |
|---|---|---|
| `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` | edit | +1 jq line, 1-word comment update |
| `src/platform/schemas.py` | edit | +1 word (`Field(min_length=1)`) on sender_phone, +5 lines for null-byte validator |
| `src/agents/expense_bookkeeper/templates/expense_pushed_confirmation.txt` | edit | -1 character (`✓`) |
| `tests/test_expense_bookkeeper_guardrails.py` | edit | +4 new test functions, ~40 lines |

Total: ~50 lines of code change. Plus this plan doc.

---

## Deployment plan

1. PR `fix/expense-bookkeeper-v01-audit-bugs` → `main` (auto-merge after 5-agent PR review per current session protocol)
2. Squash-merge
3. Build tarball, scp to test VPS
4. `shift-agent-deploy.sh deploy` — Hermes pin + env symlink + snapshot + install_artifacts + restart + smoke #11 + auto-rollback
5. **Known issue:** test VPS `46.62.206.192` lacks a `config.yaml` (see prior overnight-report Stage 12). Smoke gate will fire and auto-rollback. This is correct behaviour — code lands on `main`, but the VPS deploy needs separate `config.yaml` bootstrap.

**No customer impact regardless** — agent ships `enabled=False`.

---

## Open questions for plan-review pass (Stage 2)

1. **Should `_no_null_byte` validator be on a single field (`original_message_id`) or generalised to all string fields in `ExpenseLead`?** Plan: single field, defensive minimum. Mirrors `image_path`'s targeted validator.
2. **Should the dispatcher SKILL fix also unify the regex `#[A-HJ-NP-Z2-9]` vs `#[A-HJKMNPQR-Z2-9]`?** Plan: NO — pre-existing across other agents; needs its own scoped change.
3. **Should `sender_phone` get the deeper refactor to `Optional[E164Phone]` + at-least-one-of validator?** Plan: NO — defer to v0.2; in-scope this fix is the simple `min_length=1` per-class consistency.
4. **Should we add a v0.2 follow-up note for the deeper sender_phone refactor in CLAUDE.md or `tasks/`?** Plan: yes — small line in `tasks/expense-bookkeeper-v02-followups.md` (NEW file, ~10 lines).

---

## Stage 2 review checklist (5-agent angles)

Same 5 angles as before:

- (a) Architecture & Hermes-first compliance — verify the surgical changes don't drift; verify Hermes-first matrix correctly says 0 net-new
- (b) Security — null-byte validator placement; does the dispatcher jq filter cover all approval-flow-closed states?
- (c) UX & approval discipline — pushed-confirmation message readable without `✓`?
- (d) Test coverage & edge cases — are 4 regression tests sufficient, or are there obvious additional edge cases (e.g. `sender_phone=" "` whitespace; `original_message_id` exactly 1 char; UTF-8 multi-byte chars in template)?
- (e) Deployment & ops — smoke #11 still relevant; no new install_artifacts changes; rollback path unchanged

---

## Plan v1.1 — Stage 2 review synthesis + amendments

5 parallel reviewers ran against plan v1. Verdicts:

| Reviewer | Verdict |
|---|---|
| (a) Architecture / Hermes-first | Ship-as-is for design |
| (b) Security | Security-sound (2 MED → folded below) |
| (c) UX | UX-sound (1 MED → folded below) |
| (d) Tests | **Needs additions** (3 HIGH → folded below) |
| (e) Deploy | Deploy-safe (1 LOW → folded below) |

**Real correctness gap surfaced** by both (b) and (d): `Field(min_length=1)` accepts `"   "` (whitespace-only). The simple BUG-2 fix as drafted does NOT actually solve the empty-sender_phone problem — `sender_phone="   "` would still pass and break owner re-auth identically to `""`. Revising the fix.

### Amendments to fix specifications

#### BUG-2 — sender_phone (revised)

**v1 fix:** `sender_phone: str = Field(min_length=1)` — rejected by reviewers because Pydantic doesn't trim, so whitespace-only passes.

**v1.1 fix:** add a shared field validator that handles BUGs 2 + 3 together:

```python
@field_validator("sender_phone", "original_message_id")
@classmethod
def _validate_required_no_whitespace_no_nullbyte(cls, v: str) -> str:
    """v1.1 audit-fix: address BUGs 2 + 3 together.

    - sender_phone (BUG-2): reject empty / whitespace-only (would break owner
      re-auth at apply-expense-decision step where `sender_phone == owner_phone`)
    - original_message_id (BUG-3): reject null byte / control char (NDJSON
      audit-log safety; Pydantic `model_dump_json` escapes these but defence
      in depth keeps log-corruption surface zero)
    """
    if not v.strip():
        raise ValueError("must not be empty or whitespace-only")
    if any(c in v for c in ("\0", "\r", "\n", "\t")):
        raise ValueError("must not contain null byte or control characters")
    return v
```

Drop the separate `Field(min_length=1)` on `sender_phone`. Drop the separate `_no_null_byte` validator. One shared validator covers both bugs.

#### BUG-3 — original_message_id (revised)

**v1 fix:** dedicated `_no_null_byte` validator on `original_message_id` only.

**v1.1 fix:** folded into the shared validator above. Now also rejects `\r`, `\n`, `\t` (per reviewer-b LOW: NDJSON log-safety defence in depth).

**v1.1 deferral note:** reviewer-b flagged that `sender_lid`, `qbo_account`, `rejection_reason`, `image_phash`, `image_byte_hash` are undefended. Hash fields have shape constraints already. The remaining 3 string fields go to `tasks/expense-bookkeeper-v02-followups.md` as items.

#### BUG-1 — dispatcher jq fix (clarification)

Per reviewer-c MED: clarify the cfg-gate semantics in the SKILL.md prose. The jq grep is unconditional — it always checks `expense-bookkeeper/leads.json` if the file exists. **The cfg gate is enforced ONE LEVEL UP**, in the matrix routing rule (matrix row 18 includes `AND cfg.expense_bookkeeper.enabled`) AND in the sub-dispatcher's Step 1 (declines politely if disabled). Adding a one-sentence note to SKILL.md prose adjacent to the new jq line:

```
# expense lead → expense_bookkeeper_dispatcher
# (sub-dispatcher rejects politely if cfg.expense_bookkeeper.enabled = false)
```

### Amendments to tests

| Test | v1 → v1.1 change |
|---|---|
| Test-1 sender_phone | Parametrize over `["", " ", "  ", "\t", "\n"]`; add separate test for missing-key (Pydantic "field required" error) and `None` (type error). 6 total cases. |
| Test-2 original_message_id | Parametrize over `["msg\0null", "\0", "msg\rbreak", "msg\nbreak", "msg\ttab"]`. 5 cases. |
| Test-3 no-emoji template | Parametrize over `sorted(TEMPLATE_DIR.glob("*.txt"))` — covers all 10 templates against future regression. Em-dash explicitly allowed (carve-out). |
| Test-4 dispatcher SKILL | Replace `rfind("pending.json")` brittle search with **anchored slice**: find the "Look up across the" comment, slice to next blank-line, assert ordering within the slice. Optional: pipe each jq filter through `jq -en` if jq is on PATH (Linux-only via `pytestmark.skipif`). |
| **NEW** Test-5 trailing-space | Per reviewer-c LOW: assert `expense_pushed_confirmation.txt` first line doesn't end with trailing space (subtle paste artifact). |

### Amendments to plan structure

- Add `tasks/expense-bookkeeper-v02-followups.md` (NEW, ~15 lines) with:
  - sender_lid / qbo_account / rejection_reason null-byte coverage
  - Deeper sender_phone refactor (Optional[E164Phone] + sender_lid + at-least-one validator) per RawInbound precedent
  - Pre-existing dispatcher regex inconsistency `#[A-HJ-NP-Z2-9]` vs canonical `[A-HJKMNPQR-Z2-9]`
  - Generic null-byte validator across all `ExpenseLead` string fields

### PR body additions (per reviewer-b MED)

PR description must explicitly document:
1. **Code-pool collision priority shift**: with the new jq line, an active-expense + active-shift-proposal collision now resolves to expense (was: shift). At 28.6M alphabet, observed collisions: 0/1000 in audit. Acceptable v0.1 risk; flagged for forensics.
2. **Manual reproduction step for BUG-1**: owner sends `#A47C2 234.50` against a seeded expense lead; check `decisions.log` for `dispatcher_routed → expense_bookkeeper_dispatcher` entry.

### Items DEFERRED (not addressed in v1.1)

- (e) jq-syntax-validity assertion via `jq -en` — Windows test env may not have jq; the string-presence + ordering test is sufficient for v0.1 per reviewer-e own LOW rating
- (a) validator naming convention `_no_null_byte` vs `_validate_required_no_whitespace_no_nullbyte` — v1.1 uses the descriptive longer name (matches `_path_under_managed_dir` precedent of describing the full guarantee)

### Summary of v1.1 changes vs v1

| Area | v1 → v1.1 |
|---|---|
| BUG-2 fix | `Field(min_length=1)` → shared validator that rejects empty + whitespace-only + null + `\r\n\t` |
| BUG-3 fix | Dedicated `_no_null_byte` → folded into shared validator (above) |
| BUG-1 fix | jq line only → jq line + 1-sentence cfg-gate clarification comment in SKILL.md |
| Tests | 4 tests → 5 tests, 3 of them parametrized |
| Process | n/a → add `tasks/expense-bookkeeper-v02-followups.md` (~15 lines) |
| PR body | basic → explicit collision-priority shift + manual reproduction note |

Net: ~50 lines of code change becomes ~70 lines (mostly test parametrize expansions + the v02-followups doc). Still tight scope.

---

*Plan v1.1 complete. All Stage 2 HIGH issues addressed. Ready for Stage 5 (Build) — design folded into plan since scope is small enough that a separate design doc would just restate this.*
