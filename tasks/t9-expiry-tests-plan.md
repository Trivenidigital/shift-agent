# T9 — Expiry / stale-code edge case tests (plan)

**Drift-check tag:** `Hermes-native`

Pure test curation that reuses existing `_b1_helpers` infrastructure and pins
deployed behavior. No new helpers, no new fixtures, no new audit variants, no
new schemas. Deployed code unchanged — this commit only adds test coverage
for an existing flow already in production.

**Test-plan reference:** `tasks/catering-agent-comprehensive-test-plan.md`
commit #3 of 4 (T9: expiry/stale-code edge cases — A-018, B-016, B-017).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/t9-expiry-tests.json`
(timestamp 2026-05-06T02:36:54Z, drift-tag = Hermes-native, 7 [Hermes] / 1 [net-new]).

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | pytest collection of test functions | `[Hermes]` | pytest infra already in repo |
| 2 | env_dir + state/logs/templates fixture | `[Hermes]` | `_b1_helpers.make_env_dir` already builds the layout (used by `test_catering_b1_cases.py`) |
| 3 | Bridge HTTP stub server | `[Hermes]` | `_b1_helpers.BridgeStub` + standard `bridge_server` fixture |
| 4 | Lead seeding from a dict | `[Hermes]` | `_b1_helpers.mk_lead` + `seed_leads` build schema-valid CateringLead and write to leads.json |
| 5 | Subprocess invocation of hyphen-named scripts | `[Hermes]` | `_b1_helpers.run_apply` / `run_create` wrap importlib SourceFileLoader pattern |
| 6 | State assertion via read leads.json | `[Hermes]` | `_b1_helpers.read_leads` |
| 7 | **Test case bodies (3 functions)** | **`[net-new]`** | Case curation only — ~150 LOC, no new substrate |
| 8 | pytest cleanup | `[Hermes]` | tmp_path teardown built into pytest |

7/8 `[Hermes]`, 1/8 `[net-new]`. Well under the 50% red-flag threshold.

**Awesome-hermes-agent ecosystem check:** N/A — Hermes ecosystem provides
agent skills, not pytest cases for project-specific state-machine paths.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 working agreement (Test work → "1–2 existing test files"
plus relevant scripts/schemas). Files actually Read this session:

- ✅ Read `src/agents/catering/scripts/apply-catering-owner-decision` (lines 405-475 — matches-filter logic + EXIT_NOT_FOUND clarifying-stderr branch) before drafting B-017 assertions
- ✅ Read `src/agents/catering/scripts/create-catering-lead` (lines 90-105, 485-565 — code uniqueness via active_codes, idempotency on `original_message_id`, new-lead minting) before drafting A-018 assertions
- ✅ Read `src/platform/schemas.py` (lines 445-506 — CateringLeadStatus enum, CATERING_TRANSITIONS, terminal-status set) before choosing test status values
- ✅ Read `tests/_b1_helpers.py` (lines 100-340 — full helper surface) before deciding which helpers to reuse vs build new (decision: reuse all; build none)
- ✅ Read `tests/test_catering_b1_cases.py` (lines 37-103 — fixture wiring + first-test pattern) for env_dir + bridge_server pattern to mirror
- ✅ Read `tests/test_catering_v02_scripts.py` (lines 243-272 — `test_create_lead_idempotent_replay` body) — confirmed it asserts the exact case (same message_id returns same lead_id, idempotent_replay=True, single bridge POST). Therefore replay-control test in T9 would duplicate, not extend.

**Deployed-pattern compliance:**
- Storage: JSON-on-disk + atomic writes — using `_b1_helpers.seed_leads` ✓
- Schemas: pydantic v2 — using `mk_lead` whose dict shape passes validation ✓
- Sender identity: not exercised (test does not invoke dispatcher)
- Tests: deterministic, subprocess-invoke, assert on file mutations + stderr — matches deployed pattern ✓
- `--sender-role` parameter: defaults to `"owner"` in `run_apply` post-`b2dfc1c` — owner-path tests don't need to pass it explicitly ✓

---

## Scope boundary (anti-over-engineering)

### In scope (3 tests, ~150 LOC)

| Test name | Case | What it pins |
|---|---|---|
| `test_a018_second_inquiry_while_prior_awaiting_creates_new_lead` | A-018 | Same-customer re-inquiry with DIFFERENT `message_id` while prior is AWAITING_OWNER_APPROVAL → 2nd lead minted; first lead untouched |
| `test_b017a_apply_with_unknown_code_returns_not_found` | B-017a | Code completely absent → `EXIT_NOT_FOUND` (4) + stderr `"no recoverable lead with code #XXXXX"` |
| `test_b017b_apply_with_stale_lead_clarifies_status` | B-017b + B-016 | Code on lead in terminal status STALE → `EXIT_NOT_FOUND` + clarifying stderr `"in status STALE"` + lead_id |

### Explicitly out of scope (rejected for over-engineering)

| Considered | Decision | Reason |
|---|---|---|
| Idempotent-replay control test | **REJECTED** | `tests/test_catering_v02_scripts.py:243` already covers it (verified by Read this session). Duplicate is documentation, not coverage. |
| `parametrize("terminal_status", [STALE, CLOSED, OWNER_REJECTED])` for B-017b | **REJECTED** | All three statuses hit the SAME matches-filter exclusion at the SAME line in apply-catering-owner-decision. STALE alone catches any regression. The other two add zero coverage. |
| Test that approving STALE transitions back to AWAITING | **REJECTED** | STALE has empty transition set in `CATERING_TRANSITIONS` (schema line 505). Inventing this case = test fiction. |
| Test for explicit 4h `expires_at` field | **REJECTED — does not exist** | No `expires_at` on catering leads, no 4h TTL in code. B-016 honestly collapses into B-017b STALE case until/unless a real TTL field is added. |

### Deferred (separate commits)

- B-019 / B-020 (owner self-chat / different-account approval) — blocked on BSP per `tasks/todo.md` P2.6
- A-019 (customer phone == employee phone ambiguity) — separate commit
- B-016 dedicated test — only if a true `expires_at` field lands later

---

## Verification + commit shape

- **Run on srilu**: `pytest tests/test_catering_expiry_stale_codes.py -v` against `/tmp/full-test/` extracted tarball (matches the privilege-escalation pattern from 2026-05-06)
- **Pass criterion**: 3/3 pass on first run; no new pre-existing failures introduced elsewhere in the catering suite
- **Commit shape**: ONE commit, message `test(catering): T9 expiry / stale-code edge cases (A-018, B-016, B-017)`, ~150 LOC including module docstring
- **No deploy needed**: tests-only, no production code change
- **No push** until commit lands cleanly + `git log` looks right

---

## Approval needed

User must explicitly approve this plan before any code is written. If the
scope boundary above is wrong (e.g. you DO want the parametrize for
defense-in-depth, or you want a real `expires_at` field added instead of
honestly-deferring B-016), say so now — cheaper than re-trim after the fact.
