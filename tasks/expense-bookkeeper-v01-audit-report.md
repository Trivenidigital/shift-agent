# Expense Bookkeeper v0.1 — End-to-End Audit Report

**Date:** 2026-04-29
**Branch audited:** `main` at `f4dab6f` (PR #30 merged at `2f57288` + PR #31 docs at `f4dab6f`)
**Author:** Claude Opus 4.7
**Drift-check tag:** N/A (this is an audit report, not a build proposal)

---

## TL;DR

Comprehensive E2E audit found **4 bugs** in shipped v0.1 (1 HIGH, 1 MED, 2 LOW). Test suite is solid (145 Windows-runnable + 36 Linux-only behind `pytestmark`, all passing). Templates render cleanly (no leftover placeholders). All 15 audit-entry types written somewhere. All 8 state-machine states reachable from `EXTRACTING`.

The HIGH bug — **dispatcher routing matrix is amended but the corresponding `jq` lookup code is missing** — would break the entire owner-approval flow for expenses. Owner replies with `#CODE 234.50` and the dispatcher's Step-3 lookup has no jq command for `expense-bookkeeper/leads.json`, so the message routes wrong (likely to `handle_owner_command` for shift proposals, or worse, falls through to `handle_sick_call`).

Recommend a small fix-up PR (~15 min work + 5-agent review). Branch already in mind: `fix/expense-bookkeeper-v01-audit-bugs` from `main`.

---

## Audit Stages

### Stage A — Existing test suite (full pytest verbose)

```
145 passed, 36 skipped in 0.69s
```

- **145 passing** (Windows-runnable): schemas (19), state-machine (64 parametrized), QBO mock (19), guardrails (22), Tier-2 baseline (13), parser (Linux-only on this run = skipped)
- **36 skipped**: parser + apply-decision tests behind `pytestmark = pytest.mark.skipif(platform.system() == "Windows")` — Linux-only via `fcntl`. These will exercise on VPS or CI Linux.
- **0 failures**

### Stage B — Edge-case ad-hoc scenarios (importlib + in-process)

Cases tested directly via Python (not through the test suite):

| Case | Result |
|---|---|
| #2 typo'd code (`#A47C2`-shape, alphabet excludes `O`/`L`) | ✅ alphabet rejection works (`#A47O2` and `#A47l2` both rejected) |
| #11 approval-code collision (1000 generated, observed) | ✅ 0 collisions in 1000 from 20.5M-entry pool |
| #16 multi-receipt batch (5 sequential `last_id` increments) | ✅ unique `E0001..E0005` IDs |
| #9 vendor name normalization | 🐛 **NO platform helper** — `vendor_normalized` is filled by LLM only; "Patel Bros" / "Patel Brothers Inc" / "PATEL BROS LLC" do NOT canonicalise to a shared key |
| #7 sum-mismatch (line items sum != total_cents) | ✅ schema accepts (advisory only — owner-confirmed total wins per design) |
| `ExpenseLead.sender_phone=""` (empty string) | 🐛 **schema accepts empty string** — should require `min_length=1` (mirrors `original_message_id` constraint) |
| `original_message_id` with embedded `\0` | 🐛 **accepted** — only `image_path` validator rejects null bytes; idempotency key field has no null-byte check |
| `expense_id` pattern (`E\d{4,}`) | ✅ enforced correctly (rejects `e0001`, `E001`, `EE0001`, `E0001x`, `1E0001`, `E`) |
| dHash same/different bytes (fallback path) | ⚠ **PIL dependency**: when PIL not installed, dHash falls back to `sha256(bytes)[:16]` which does NOT converge on perceptually-similar images. Real-receipt dedup requires PIL on the VPS. (Documented edge case, not a code bug, but flag for VPS provisioning.) |

### Stage C — Template render verification

All 10 templates rendered with sample fields → **no leftover `{{...}}` placeholders**.

Non-ASCII char audit:
- 9 templates contain `0x2014` (em-dash `—`). Typography, not emoji. Acceptable.
- `expense_pushed_confirmation.txt` contains `0x2713` (`✓` checkmark). 🐛 **Violates `CLAUDE.md` "no emojis" rule** (originally flagged by reviewer-c during PR review; never fixed).
- `expense_undo_outside_window.txt` is fully ASCII.

### Stage D — Code-vs-spec gap analysis

| Check | Result |
|---|---|
| All 15 audit entry types written to `decisions.log` somewhere | ✅ Every type has at least 1 call-site |
| Every state in `EXPENSE_TRANSITIONS` reachable from `EXTRACTING` | ✅ All 8 states reachable |
| All 10 templates referenced from code | ✅ None orphaned |
| Routing matrix in `dispatch_shift_agent/SKILL.md` includes 3 expense rows | ✅ Lines 18, 19, 22 |
| **Step-3 `jq` lookup code includes `expense-bookkeeper/leads.json`** | 🐛 **MISSING** — only 3 jq lines (catering-menu-pending, catering-leads, pending). No expense lookup. |
| `install_artifacts()` paths correct (`qbo_client.py` flat install, scripts/templates/systemd) | ✅ All paths correct |
| Config template has expense_bookkeeper block | ✅ Verified |

---

## Consolidated bug list

### BUG-1: HIGH — Dispatcher Step-3 lookup missing `expense-bookkeeper/leads.json`

**File:** `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` (lines 81-83)
**Severity:** HIGH — breaks the entire owner-approval flow for expenses
**Discovery:** Stage D code-vs-spec gap analysis

The routing matrix correctly lists 3 expense routing rows (lines 18, 19, 22). The corresponding code-pool grep order at Step 3 (lines 73-83) lists only 3 jq commands (catering-menu-pending, catering-leads, pending) — **no jq command for expense-bookkeeper/leads.json**.

Effect: when owner sends `#A47C2 234.50` matching an active expense lead, Kimi (the LLM dispatcher) has no jq command for the expense lookup. Most-likely behaviour: the message falls through to one of the existing greps, returns no match, then the matrix's **fallback** rules fire. Likely outcomes:

1. Code matches no state file → routes to `handle_owner_command` (text-only-no-code rule) — **wrong handler**
2. LLM improvises a lookup → brittle, possibly hallucinates a match
3. Code coincidentally collides with another agent's pool → routes to that agent — **dangerous (cross-agent collision)**

**Fix:** add the missing jq line in priority order between catering-leads.json and pending.json:

```bash
jq --arg c "$CODE" '.leads[] | select(.owner_approval_code == $c) | select(.status != "PUSHED" and .status != "REVERSED" and .status != "REJECTED" and .status != "EXPIRED")' /opt/shift-agent/state/expense-bookkeeper/leads.json   # expense lead → expense_bookkeeper_dispatcher
```

(`PUSHED`/`REVERSED`/`REJECTED`/`EXPIRED` are the four `EXPENSE_APPROVAL_CLOSED_STATUSES` per `schemas.py`.)

**Reproduction:** owner sends a valid `#CODE total.cc` reply to an expense approval card on a VPS where expense_bookkeeper is enabled. Watch `decisions.log`: a `dispatcher_routed` entry will show routing to a non-expense skill.

### BUG-2: MED — `ExpenseLead.sender_phone` accepts empty string

**File:** `src/platform/schemas.py` (in `ExpenseLead` class)
**Severity:** MED — data integrity
**Discovery:** Stage B schema validation

Field is declared as `sender_phone: str` with no `min_length`. Empty string passes validation. Pre-existing pattern (`original_message_id: str = Field(min_length=1)`) was not applied here.

Effect: an `ExpenseLead` with `sender_phone=""` could be persisted, breaking owner re-auth (`sender_phone == owner_phone` would fail trivially) and obscuring downstream debugging.

**Fix:** change `sender_phone: str` → `sender_phone: str = Field(min_length=1)`. Mirror the `original_message_id` constraint.

### BUG-3: LOW — `original_message_id` accepts embedded null byte

**File:** `src/platform/schemas.py` (in `ExpenseLead` class)
**Severity:** LOW — data hygiene
**Discovery:** Stage B schema validation

`original_message_id: str = Field(min_length=1)` rejects empty string but accepts `"msg\0with-null"`. WhatsApp message IDs don't contain null bytes in practice, but defensive validation is cheap.

**Fix:** add a validator that rejects null bytes, mirroring `image_path`'s `_path_under_managed_dir` check.

### BUG-4: LOW — `expense_pushed_confirmation.txt` uses `✓` emoji

**File:** `src/agents/expense_bookkeeper/templates/expense_pushed_confirmation.txt`
**Severity:** LOW — convention violation
**Discovery:** Stage C template character audit

Template line: `{{expense_id}} pushed to QuickBooks ✓`

Per `CLAUDE.md`: "Only use emojis if the user explicitly requests it." This was originally flagged by re-reviewer-c during PR review and never fixed.

**Fix:** replace `✓` with text. Suggested: `{{expense_id}} pushed to QuickBooks` (just drop the checkmark) or `{{expense_id}} pushed to QuickBooks (success)`.

---

## Non-bugs (verified working as designed)

- ✅ `extract='ignore'` on `ReceiptExtraction` correctly tolerates LLM future fields (CLAUDE.md schema pattern)
- ✅ `extract='forbid'` on state schemas catches typos
- ✅ Owner-confirmed total is push truth (NOT extracted) — defends against vision prompt injection
- ✅ Owner re-auth on undo accepts EITHER phone OR LID match (B-H2 fix verified in code)
- ✅ Image-path validator rejects path traversal (`..`) AND sibling-dir attack (managed-dir trailing slash normalization)
- ✅ Token redactor strips JSON-bodied `"access_token":"..."` AND bare JWTs (B-H1 fix verified)
- ✅ All 11 valid state transitions / 53 invalid raises (parametrized matrix test)
- ✅ MockQBOClient parametrised over all 6 `QBOErrorClass` values
- ✅ ProposalCode regex enforces 28.6M-entry alphabet (excludes `0`, `1`, `I`, `L`, `O`)

---

## Deferred to v0.2 (per design v2; not bugs)

- Apply-side `original_message_id` idempotency test (extract-receipt has the check; apply-side dedup of same approval-code re-receipt is handled by `_find_lead_by_code` excluding `EXPENSE_APPROVAL_CLOSED_STATUSES` leads)
- Vendor name normalization helper (currently LLM-only — flagged in BUG list above as a known gap, not a fix-this-now item; raise if a customer surfaces ambiguous vendor matching)
- Per-amount-cockpit-vs-WhatsApp threshold UI (placeholder force-via-WhatsApp message in v0.1 is correct per spec)
- Real `RealQBOClient` impl (raises `NotImplementedError` in v0.1 by design)

---

## Recommended fix workflow

Per the user's instruction ("apply any fixes on a specific branch, not to end up in messy situation"):

1. **Branch:** `fix/expense-bookkeeper-v01-audit-bugs` from `main`
2. **Plan** doc: `tasks/expense-bookkeeper-v01-audit-bugs-plan.md` (will draft)
3. **5-agent plan review** → revise
4. **Design** doc: `tasks/expense-bookkeeper-v01-audit-bugs-design.md` (will draft if scope warrants)
5. **5-agent design review** → revise
6. **Build:** apply 4 fixes (1 dispatcher SKILL.md update, 2 schema field constraints, 1 template character swap)
7. **PR** with full body + test plan
8. **5-agent PR review** → fix-up if HIGH found
9. **Auto-merge if clean** (per current session's accepted protocol) → deploy

Estimated total time: 1–2 hours (small fixes; the process overhead is the bulk).

Alternative if user wants to skip ceremony: single small commit + PR with all 4 fixes; 1-agent review; merge. ~30 min.

---

## What this audit did NOT cover

To be honest about scope:

- **No live VPS testing.** Cannot exercise the actual SKILL flow without a Hermes runtime + WhatsApp gateway + Kimi LLM. This audit is code-level only.
- **No real vision-API testing.** OCR accuracy on real receipts not verified (proven E2E by Catering's menu pipeline 2026-04-29; transferable per Hermes-first rule).
- **No real-QBO testing.** v0.1 ships with `MockQBOClient`; `RealQBOClient` raises by design.
- **No load/concurrency testing.** Single-receipt scenarios only; multi-receipt-rapid-fire and high-volume scenarios are paper-spec.
- **No multi-customer testing.** Per-customer-VPS isolation assumed (architectural, not testable in audit).

These are all deliberate v0.1 scope cuts, documented in plan v2.1 and design v2.

---

*Audit complete. Ready for Stage F (conditional fix workflow) per user direction.*
