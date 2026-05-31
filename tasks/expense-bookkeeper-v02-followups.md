# Expense Bookkeeper — v0.2 Follow-up Items

**Drift-check tag:** `extends-Hermes` — backlog of substrate extensions; each item flagged YAGNI / DEFER / CUT in `tasks/expense-bookkeeper-resume-audit.md` (2026-04-30).

Items deferred during v0.1 audit-fix (PR for `fix/expense-bookkeeper-v01-audit-bugs`).
None are blockers for the v0.1 ship; each has a stated rationale for deferral.

## From audit-bug fix Stage 2 reviewers

### V02-1 — Generic null-byte / control-char defense across `ExpenseLead` string fields

**Status:** done in PR #393. `ExpenseLead` now applies the shared blank/control-char validator to `sender_lid`, `qbo_account`, and `rejection_reason` when present, while preserving `None`.

**From:** reviewer-b MED (Stage 2 plan review of audit-bug fix)
**Currently defended:** `original_message_id`, `sender_phone` (BUG-3 + BUG-2 v1.1 shared validator)
**Currently UNdefended:** `sender_lid`, `qbo_account`, `rejection_reason`
**Already-defended via shape constraint:** `image_phash` (16-hex), `image_byte_hash` (64-hex)
**Defended via dedicated validator:** `image_path` (`_path_under_managed_dir` rejects `\0`, `..`)

**Why deferred:** `sender_lid` and `qbo_account` come from sources the agent already trusts (Hermes-resolved LID; LLM classifier output). `rejection_reason` is operator-set. None are owner-set free-text. Defence-in-depth would reject control chars in all 5 fields, but the audit's primary concern (NDJSON log-safety) is already covered by Pydantic's `model_dump_json` JSON-escaping `\0`/`\r`/`\n`/`\t` automatically.

**Action for v0.2:** extend the shared `_validate_required_no_whitespace_no_nullbyte` validator to cover `sender_lid` (when present), `qbo_account` (when present), `rejection_reason` (when present). Test parametrize.

### V02-2 — Deeper `sender_phone` refactor to `Optional[E164Phone]` + at-least-one-of validator

**From:** reviewer-a MED (Stage 4 design review of original v0.1 PR; flagged again in audit-fix Stage 2)
**Current:** `sender_phone: str = Field(min_length=1)` — plain string, not E.164-typed
**Precedent:** `RawInbound` (`schemas.py:1186-1208`) uses `Optional[E164Phone]` + `sender_lid` + `model_validator` requiring at least one

**Why deferred:** refactoring requires updating extract-receipt's persistence path + every `ExpenseLead` test fixture (currently passes plain strings) + `apply-expense-decision`'s comparison logic. Not a 50-line scope; closer to 200 lines.

**Action for v0.2:** mirror `RawInbound` exactly. Remove the BUG-2 `Field(min_length=1)` constraint (becomes redundant). Update fixtures to use canonical E.164 strings. Update `_validate_required_no_whitespace_no_nullbyte` to handle Optional inputs.

### V02-3 — DRY `_check_orphans` helper between `extract-receipt` and `apply-expense-decision`

**From:** reviewer-a MED (Stage 10 fix-up review of original v0.1 PR)
**Current:** ~70-line duplicate in both scripts
**Why deferred:** clean lift to `src/platform/expense_orphan.py` is straightforward but expands install_artifacts surface and was deferred from the v0.1 fix-up commit explicitly.

**Action for v0.2:** lift `_check_orphans` + `_scan_audit_for_push_completion` to platform module; both scripts import.

### V02-4 — Token-redactor: `state=` / `code_verifier=` outside URL context

**From:** reviewer-b MED (Stage 10 fix-up review)
**Current:** `redact_qbo_error` strips `access_token`/`refresh_token`/JWT/URL-query patterns. Misses bare OAuth `state=...` / PKCE `code_verifier=...` if they appear outside a URL.

**Why deferred:** v0.1 ships with MockQBOClient which never produces real OAuth payloads; risk surface is zero in v0.1. v0.2 RealQBOClient impl will need this.

**Action for v0.2:** add patterns to `_TOKEN_PATTERNS`:
```python
re.compile(r'\bstate=[A-Za-z0-9_\-\.]{8,}', re.IGNORECASE),
re.compile(r'\bcode_verifier=[A-Za-z0-9_\-\.]{16,}', re.IGNORECASE),
```

### V02-5 — `image_path` `os.path.realpath` symlink-resolve

**From:** reviewer-b MED (Stage 10 fix-up review)
**Current:** validator does prefix-match after trailing-slash normalization. Symlink TOCTOU still possible if attacker can write inside the receipts dir.

**Why deferred:** single-tenant VPS architecture means only the agent process writes there; no attacker has write access. Becomes relevant if multi-tenant sharing of receipts dir ever happens (currently impossible per per-customer-VPS isolation).

**Action for v0.2 multi-tenant scenario:** `os.path.realpath(v).startswith(realpath(managed))`.

### V02-6 — `expense_lookup` SKILL (analog of catering's `lookup-prior-leads-by-phone`)

**From:** plan v2 §9 deferral list
**Current:** no way for owner to query past expenses ("show me what I expensed at Costco last month")
**Action for v0.2:** mirror catering's lookup script + SKILL.

### V02-7 — Pre-existing dispatcher regex inconsistency

**From:** reviewer-a (multiple stages)
**Current:** `dispatch_shift_agent/SKILL.md:79` uses `#[A-HJ-NP-Z2-9]{5}` while canonical alphabet in `schemas.py:843` is `#[A-HJKMNPQR-Z2-9]{5}`. Both are functionally restrictive enough; the dispatcher's regex is stricter near the seam (excludes `K`/`M`).

**Why deferred:** needs a scoped pass across all dispatcher mentions + handler skills + tests, not piggybacking on a 1-line jq fix.

**Action for v0.2:** unify to canonical regex everywhere; one PR, ~5 file edits, mostly tests.

### V02-8 — jq syntax-validity assertion in audit test

**Status:** done. `tests/test_expense_bookkeeper_guardrails.py` now extracts each dispatcher Step-3 jq lookup and runs it against a minimal matching JSON fixture on Linux, skipping when `jq` is unavailable.

**From:** reviewer-e LOW (Stage 2 audit-fix plan review)
**Current:** `test_audit_bug1_dispatcher_skill_includes_expense_jq_lookup` is a string-presence + ordering test. A subtle filter typo (missing paren) would pass the test but fail at runtime.

**Why deferred:** v0.1 audit-fix is a correctness landing, not test-tooling expansion. `jq -en` would need to be available in CI/test env (Linux only).

**Action for v0.2:** add a Linux-only test (`pytestmark.skipif(platform.system() == "Windows")`) that pipes each jq filter from the SKILL through `subprocess.run(["jq", "-en", filter])` and asserts exit 0.

---

## From original v0.1 PR review (already documented in overnight-report)

(Listed for completeness; no action needed in this fix branch.)

- Plan §4g edge cases NOT yet covered: #2 typo'd code (silent), #7 sum-mismatch resolution, #9 vendor name normalization, #11 approval-code collision regenerate, #16 multi-receipt batch
- Apply-side `original_message_id` idempotency runtime test (vs schema-only)
- Cockpit web UI for above-threshold review (paper spec only in v0.1)
- Real `RealQBOClient` impl (raises `NotImplementedError` in v0.1)

---

*Last updated: 2026-04-29, audit-fix Stage 5 (build).*
