# T7 — Multilingual catering-inquiry fixtures (plan)

**Drift-check tag:** `Hermes-native`

Pure data curation. Append 5 multilingual JSONL rows to
`tests/fixtures/dispatcher_traffic.jsonl`. The existing replay harness
auto-loads any new row, validates schema, and routes via the existing
priority-9 catering-keyword mock. No helper changes, no schema changes,
no new test files, no new fixtures format.

**Test-plan reference:** `tasks/catering-agent-comprehensive-test-plan.md`
commit #4 of 4 (T7: multilingual fixtures — I-001, I-002, I-005, I-006,
plus a Tamil bonus).

**`/hermes-check` receipt:** `tasks/.hermes-check-receipts/t7-multilingual-fixtures.json`
(timestamp 2026-05-06T03:20:53Z, drift-tag = Hermes-native, 10 [Hermes] / 1 [net-new]).

---

## Hermes-first per-step checklist

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | WhatsApp inbound from customer | `[Hermes]` | Source ingestion / origins capability |
| 2 | validate-sender-block parses v=1 block | `[Hermes]` | Already deployed; fixtures encode its output as test data |
| 3 | identify-sender returns role=unknown | `[Hermes]` | Same; fixture-encoded |
| 4 | dispatch_shift_agent SKILL prompt receives inbound + state | `[Hermes]` | Skill dispatch capability; SKILL unchanged |
| 5 | LLM dispatcher routes via priority matrix | `[Hermes]` | LLM gateway capability — multilingual classification verified for catering 2026-04-29 |
| 6 | Deterministic priority-order mock matches catering keyword | `[Hermes]` | Already in `_dispatcher_replay.py` lines 382-388; recognizes "catering"/"wedding"/"guests" etc. |
| 7 | Replay test parametrize loop | `[Hermes]` | `test_dispatcher_replay.py` auto-loads any new JSONL row |
| 8 | Required-fields validation | `[Hermes]` | Already in `test_fixtures_have_required_fields` |
| 9 | Known-handlers check | `[Hermes]` | Already in `test_expected_handlers_are_known` (catering_dispatcher in KNOWN_HANDLERS) |
| 10 | Min-count floor check | `[Hermes]` | Already in `test_fixtures_load` (EXPECTED_MIN_FIXTURES = 10, currently 16, will be 21) |
| 11 | **Authoring 5 multilingual case texts** | **`[net-new]`** | Pure case curation; ~5 dense JSONL lines |

10/11 `[Hermes]`, 1/11 `[net-new]`. Far below the 50% red-flag threshold.

**Awesome-hermes-agent ecosystem check:** N/A — Hermes ecosystem provides
agent skills, not multilingual fixture authoring for project-specific
state-machine paths.

---

## Drift-rule self-checks

Per CLAUDE.md Part 3 working agreement (Test work + dispatcher work +
closest-similar). Files actually Read this session:

- ✅ Read `tests/fixtures/dispatcher_traffic.jsonl` (lines 1-8 — schema + closest-similar `synth-008-catering-keyword-customer` for English-only catering inquiry) before drafting new fixture shapes
- ✅ Read `tests/_dispatcher_replay.py` (lines 1-75 — fixture format spec; lines 375-388 — priority-9 catering-keyword mock with full keyword list `cater, catering, headcount, guests, event, wedding, reception, banquet, birthday, anniversary, party, drop off, pickup for event, do you do catering`) before choosing words to embed in case texts so the deterministic mock continues to pass
- ✅ Read `tests/test_dispatcher_replay.py` (lines 1-75 — load + min-floor; lines 88-127 — required-fields + known-handlers + skill-md hash gate) before deciding fixture count and confirming SKILL.md hash gate is unaffected
- ✅ Read `src/agents/catering/skills/parse_catering_inquiry/SKILL.md` (lines 1-145 — extraction contract, examples) for context on what extraction tests would look like (decision: out of scope, see below)

**Deployed-pattern compliance:**
- Fixture format: dense single-line JSON with `id`, `category`, `description`, `source_row`, `input.{raw_text, sender_block, identity, state_files, config?, media_type?}`, `expected_handler` ✓
- Sender-block convention: `[shift-agent-sender v=1 platform=whatsapp phone="..." lid="..." fromMe=false chat_id="..."]\n` followed by message body ✓
- Customer fixtures use `role: "unknown"`, `fromMe: false`, `lid: null` (matches `synth-008` precedent) ✓
- expected_handler `catering_dispatcher` already in KNOWN_HANDLERS ✓

---

## Scope boundary (anti-over-engineering)

### In scope (5 fixtures, ~5 LOC of JSONL data)

| Fixture id | Case | Language mix | English keywords used |
|---|---|---|---|
| `synth-017-catering-telugu-codeswitched` | I-001 | Telugu-dominant + English | `catering`, `wedding` |
| `synth-018-catering-hindi-codeswitched` | I-002 | Hindi (Devanagari + Roman) + English | `catering`, `party` |
| `synth-019-catering-eng-telugu-mix` | I-005 | English-dominant + Telugu | `catering`, `wedding`, `guests` |
| `synth-020-catering-eng-hindi-mix` | I-006 | English-dominant + Hindi | `catering`, `guests`, `vegetarian` |
| `synth-021-catering-tamil-codeswitched` | bonus | Tamil + English | `catering`, `reception` |

All fixtures: `role=unknown` (customer), `expected_handler=catering_dispatcher`, `cfg.catering.enabled=true`, no state files needed for routing match (priority 9 fires on keyword + role, no state lookup).

### Explicitly out of scope (rejected for over-engineering)

| Considered | Decision | Reason |
|---|---|---|
| Pure single-language fixtures (Telugu only / Hindi only without ANY English keyword) | **REJECTED** | Would fail the deterministic priority mock (line 387 of `_dispatcher_replay.py` — keyword scan is English-only). Real Triveni customer messages are virtually always code-switched per CLAUDE.md customer profile; "pure" cases are an edge that's also a substrate-change request, not data curation. If we ever want pure-language fixtures, that's a separate commit that adds a `bypass_priority_mock` flag to the harness. |
| I-009 date format ambiguity ("5/8/26" US vs EU) | **REJECTED** | This is an EXTRACTION-correctness test on `parse_catering_inquiry`, not a routing test. Different pattern (real-LLM call to OpenRouter, nondeterministic output, requires fixture-extraction harness work). Defer to T7-b if/when extraction tests are scoped. |
| `parse_catering_inquiry` extraction assertions on multilingual texts | **REJECTED** | Same reason as I-009 — extraction tests need the v0.2 OpenRouter caller pattern, cost ~$0.01 per call × 5 = $0.05/run, nondeterministic. Routing test catches the high-value gap (misroute = customer never reaches the flow); extraction-correctness on Telugu/Hindi is a separate correctness concern, separate commit. |
| Add `bypass_priority_mock: true` field to fixture schema | **REJECTED** | Would let me include pure single-language fixtures, but this is harness substrate change (adds a Fixture model field + skip logic in mock test). The all-code-switched alternative covers the I-001/002/005/006 P0 cases without touching substrate. |
| EXPECTED_MIN_FIXTURES bump from 10 → 20 | **OPTIONAL** | Current floor is 10; will be 21 after this PR. Not strictly necessary to bump (the floor only catches accidental file truncation). Skip unless reviewer flags it. |

### Deferred (separate commits if ever needed)

- T7-b: extraction correctness on multilingual text (requires OpenRouter calls, real-LLM v0.2 harness extension)
- I-009 date ambiguity (extraction test, not routing)
- Pure single-language fixtures (requires harness `bypass_priority_mock` flag)

---

## Verification + commit shape

- **Run on srilu**: `pytest tests/test_dispatcher_replay.py -v` (NOT the real-LLM variant; that costs OpenRouter credits)
- **Pass criterion**: 21 fixtures load (was 16); all 5 new fixtures route to `catering_dispatcher` via priority-9 mock; schema validation passes; SKILL.md hash gate stays green (no SKILL.md change)
- **Commit shape**: ONE commit, message `test(catering): T7 multilingual catering-inquiry fixtures (I-001, I-002, I-005, I-006 + Tamil)`, ~5 lines of JSONL added
- **No deploy needed for correctness** (tests-only, no production code), but full pipeline includes deploy to verify gates

---

## Approval needed

User must explicitly approve before any code is written. If you want me to:
- Include pure-language fixtures (would require harness `bypass_priority_mock` flag)
- Include I-009 / extraction tests in scope (would expand to ~200 LOC + OpenRouter cost)
- Skip the Tamil bonus fixture
- Change any of the keyword-coverage choices

Say so now — cheaper than re-trim after the fact.
