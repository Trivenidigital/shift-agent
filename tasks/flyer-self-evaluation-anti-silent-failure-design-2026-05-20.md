# Flyer Self-Evaluation Anti-Silent-Failure Design

**Drift-check tag:** extends-Hermes

**Plan reference:** `tasks/flyer-self-evaluation-anti-silent-failure-plan-2026-05-20.md`

**New primitives introduced:** source-contract incident evidence details, recursive report redaction, source-aware QA proof helper, operator-brief grouped Flyer self-evaluation lines, deterministic CLI fixtures.

## Scope Boundary

This design changes read-only reporting only:

- `tools/flyer-self-evaluation.py`
- `tools/operator-brief.py`
- focused tests/fixtures
- this task/design documentation

It does not change Flyer runtime workflow, source-edit provider routing, render dispatch, schemas, customer copy, dashboard UI, production state, WhatsApp sends, deployment, or provider config. PR #147 is open, so provider behavior is treated as pending external work and is surfaced only as report/backlog posture.

## Hermes-First Analysis

| Step | Owner | Decision |
|---|---|---|
| Read Flyer project state | Hermes/Flyer substrate | Use existing `projects.json`; no new store. |
| Read audit evidence | Hermes audit substrate | Use existing `decisions.log`; tolerate missing body fields. |
| Understand source flyer contracts | Hermes-style structured extraction already shipped in PR #137 | Inspect existing `reference_extractions[].source_contract`; do not modify extraction. |
| Check locked facts and QA evidence | Flyer-specific policy on Hermes state | Add report-side checks only. |
| Report incidents to operator | Existing operator brief | Extend current optional Flyer self-evaluation summarizer. |
| Generate eval candidates | Existing test/golden suite | Map incidents to fixture/test categories; do not auto-write golden tests at runtime. |

Awesome Hermes ecosystem check: no existing Hermes skill replaces this report-side Flyer policy. Verdict: Hermes supplies the state, audit, and operator channel; this slice adds narrow Flyer-specific detectors.

## Data Flow

1. Load `projects.json` with tolerant JSON parsing.
2. Load `decisions.log` as best-effort NDJSON.
3. For each project, derive source-contract state:
   - reference image present;
   - exact-source-edit cues in `raw_request`;
   - source contract present;
   - source-contract obligations: `required_headings`, `required_text`, `sections[].heading/items`, `requested_replacements`, `forbidden_substrings`, preserve flags;
   - source-derived locked facts present;
   - QA reports present and accepted.
4. Emit incidents with bounded evidence and stable `evidence_details`.
5. Sanitize the full report recursively before JSON/Markdown output.
6. `operator-brief.py` reads the sanitized JSON and groups top lines by action.

## Source-Aware QA Rule

`has_source_qa(project)` must stop trusting provider/warning marker text. Source-aware QA is satisfied only when:

- a passed report has `qa_source="operator_review"`; or
- a passed report has non-empty `extracted_text` and report-side evidence that every positive source-contract obligation is represented.

Positive source-contract obligations are required locked facts with prefixes:

- `source_heading:`
- `source_section:`
- `source_required_text:`
- `replacement:*:new`

Negative obligations are `replacement:*:old` and `source_contract.forbidden_substrings`. They can emit `source_contract_forbidden_text_present`, but absence of forbidden text is never sufficient by itself to satisfy `has_source_qa`.

Report-side text matching must mirror `src/agents/flyer/visual_qa.py` semantics: casefold, apostrophe-tolerant text matching, word-boundary anchoring, and phone digit matching inside contiguous OCR digit runs. Generic passed OCR/vision QA is not enough even if its provider or warning text contains `source`, `contract`, or `integrity`.

## Incident Semantics

- `manual_source_edit_stale`: queued/in-progress `manual_edit_required` source-edit row exceeds threshold.
- `source_contract_missing`: exact-source-edit-looking project has reference media but no source contract.
- `source_contract_qa_missing`: source contract and generated/final asset exist, but no accepted source-aware QA exists.
- `source_contract_locked_fact_gap`: source-contract positive obligations are missing from `locked_facts`.
- `source_contract_qa_fact_gap`: positive source-contract obligations are not evidenced in a passed source-aware QA report.
- `source_contract_forbidden_text_present`: source contract forbids text and QA extracted text still contains it.
- `customer_copy_internal_leak`: outbound body fields in decisions log expose internal copy.
- `customer_copy_static_internal_leak`: `--scan-source-copy` finds internal copy in targeted customer ack functions.
- `repeated_status_checkins`: repeated customer status/check-in messages are grouped by project/customer/chat.
- `generation_stuck`: generation/finalization status is stale.

All source-contract incidents include `evidence_details` with the plan-defined stable keys. Fields that do not apply are empty lists, `false`, `0`, or `null`.

## Redaction

Implement a field-aware recursive `sanitize_report()` pass over the completed report before rendering. Apply it to JSON and Markdown output.

Preserve structured control fields: `type`, `severity`, `project_id`, `eval_category`, counts, booleans, and numeric ages. Redact only string values/free-text fields.

Redact:

- `OPENROUTER_API_KEY=...`, `OPENAI_API_KEY=...`, and other `*_KEY=...`, `*_TOKEN=...`, `*_SECRET=...` assignments;
- `Bearer ...`, `access_token`, `refresh_token`;
- `sk-...` style API keys;
- E.164 phone numbers outside explicit `project_id` fields;
- WhatsApp chat IDs/LIDs;
- local absolute media paths such as `/opt/shift-agent/state/...` and `C:\...`.

Do not print raw `raw_request`, full outbound bodies, env file contents, or provider credential details. Evidence should be a short summary plus structured booleans/counts.

`tools/operator-brief.py` must also sanitize all emitted Flyer self-evaluation lines, including error lines for invalid/missing report JSON. It must not print absolute self-evaluation report paths supplied on the CLI.

## Operator Brief

Keep `--flyer-evaluation-json`; change only summarization:

- `Status: red; incidents=N; high_or_critical=M`
- `Manual queue: stale_source_edits=X; oldest=Ymin`
- `Source contracts: missing=X; locked_fact_gaps=Y`
- `QA gaps: missing=X; fact_gaps=Y; forbidden_text_hits=Z`
- `Customer waiting: repeated_checkins=X`
- top high/critical incidents, already redacted
- `Needs Srini: ...`

No new data source or production probe is added.

`operator-brief.py` must consume `evidence_details` for age/count grouping. It must not parse free-text `evidence`.

## Fixtures

Add deterministic fixtures:

- `tests/fixtures/flyer_self_eval/source_contract_gap_projects.json`
- `tests/fixtures/flyer_self_eval/decisions.log`

The fixture should include:

- one stale manual source-edit row;
- one exact-source-edit project with reference image and no source contract;
- one source-contract project with generic passed QA but no source-aware proof;
- one source-contract project with required text missing from locked facts;
- one source-contract project where forbidden old brand appears in QA extracted text;
- at least three decisions rows with the same `project_id` or `chat_id`, using mixed body fields (`body`, `visible_body`, `message`), plus one non-status row to prove grouping/filtering;
- secret-like strings in fields that become report output: incident `evidence`, `suggested_action`, `evidence_details`, `needs_srini`, and operator brief lines.

## Test Plan

- Existing focused suite remains green after updating obsolete marker-based source-QA expectations.
- New red/green tests:
  - generic passed QA does not count as source-aware;
  - operator review QA counts;
  - actual source obligations in extracted QA text count;
  - marker-only provider/warning QA fails, and a positive test with locked-fact evidence in `extracted_text` passes;
  - missing locked source facts emits `source_contract_locked_fact_gap`;
  - missing QA evidence emits `source_contract_qa_fact_gap`;
  - forbidden source text in QA emits `source_contract_forbidden_text_present`;
  - report output redacts secrets, phones, chat IDs, and paths;
  - operator brief redacts `OPENAI_API_KEY=...`, `Bearer ...`, E.164 phones, LIDs, and local paths from supplied JSON;
  - Markdown includes decisions.log body limitation;
  - operator brief groups stale manual queue/source contract/QA/repeated check-in lines.
  - report-side matching accepts `Lakshmi's` vs `Lakshmis` and formatted phone numbers using the same semantics as deployed visual QA.

Expected incident precedence per fixture project:

- no QA report -> `source_contract_qa_missing`
- passed QA with missing required positive source text -> `source_contract_qa_fact_gap`
- passed QA with forbidden text -> `source_contract_forbidden_text_present`
- generic marker QA -> gap/missing incidents as appropriate, never clean
- old text absent but required replacement new text absent -> `source_contract_qa_fact_gap`

## Risks

| Risk | Mitigation |
|---|---|
| False positives on legacy source-contract rows | Incidents are report-only and include evidence details; no customer behavior changes. |
| Over-redaction hides useful operator data | Keep `project_id`, incident type, counts, and bounded summaries intact. |
| Source-aware QA proof becomes too strict | Accept `operator_review` as authoritative and source-derived locked-fact/forbidden-substring proof for automated QA. |
| PR #147 conflict | Do not edit provider-routing files, provider policy schemas, or render dispatch. |

## Acceptance

- The report identifies stale manual source-edit queue rows, missing source contracts, missing source-aware QA, locked-fact gaps, QA fact gaps, forbidden text hits, copy leaks, and repeated check-ins.
- Output is deterministic, redacted, and operator-brief friendly.
- No provider, customer, dashboard, production, or runtime behavior changes.
- Focused tests, py_compile, CLI smoke, and `git diff --check` pass.
