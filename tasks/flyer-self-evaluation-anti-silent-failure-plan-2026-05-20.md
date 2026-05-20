# Flyer Self-Evaluation Anti-Silent-Failure Plan

**Drift-check tag:** extends-Hermes

**Goal:** Strengthen Flyer Studio's read-only self-evaluation and operator-brief reporting so source-edit/source-contract silent failures surface before customers notice.

**Context date:** 2026-05-20

## Drift Check

- Read `AGENTS.md`, `tasks/lessons.md`, `tasks/todo.md`, and `docs/hermes-alignment.md`.
- Verified PR #137 is merged: `855b1613eb5b662f7c345a7d1ba08b252056d299`, "Flyer source contract first: prevent F0061 downgrade".
- Verified PR #147 is still open, not merged: `feat/flyer-source-edit-provider-config`, "Fix Flyer source-edit provider config routing".
- Avoid touching PR #147-owned files while it is open:
  - `src/agents/flyer/workflow.py` provider readiness logic
  - `src/agents/flyer/render.py` source-edit provider dispatch
  - `src/platform/schemas.py` `source_edit_provider_policy`
  - provider-routing tests except read-only context
- Read current source-contract/self-eval surfaces:
  - `src/platform/schemas.py` for `FlyerSourceContract`, `FlyerReferenceExtraction.source_contract`, `FlyerVisualQAReport`, and Flyer audit variants.
  - `src/agents/flyer/reference_extract.py` for source-contract extraction posture.
  - `src/agents/flyer/visual_qa.py` for source-contract forbidden-substring QA.
  - `tools/flyer-self-evaluation.py` for current incident rules.
  - `tools/operator-brief.py` for optional Flyer self-evaluation brief integration.
  - `tests/test_flyer_self_evaluation.py`, `tests/test_operator_brief.py`, and source-contract/visual-QA tests for current coverage style.

## Hermes-First Analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Inbound media/source capture | yes - Hermes WhatsApp ingress and Flyer project JSON already retain reference assets | Read existing project state only; no new ingestion path. |
| Vision/OCR/source extraction pattern | yes - PR #137 added `FlyerSourceContract` using the existing Hermes-style structured extraction pattern | Report missing or incomplete source contracts; do not rewrite the extractor in this slice. |
| Structured JSON extraction | yes - Pydantic Flyer schemas and JSON state are already in tree | Inspect existing `reference_extractions[].source_contract` and `locked_facts`; no schema changes planned. |
| Scheduled/read-only self-evaluation | yes - `tools/flyer-self-evaluation.py` already emits read-only JSON/Markdown | Harden report semantics and redaction; keep deterministic and offline. |
| Operator-facing incident reporting | yes - `tools/operator-brief.py` already accepts optional Flyer self-eval JSON | Enrich summary lines only if the change is tiny and read-only. |
| Eval-candidate generation | yes - existing report maps incidents to fixture categories | Add source-contract readiness/evidence details and deferred backlog candidates. |
| Source-edit provider routing | covered by pending PR #147 | Do not implement; mention provider posture only as report/backlog evidence. |

Awesome Hermes Agent ecosystem check: no drop-in Flyer source-contract evaluator or source-edit SLA monitor is present in the checked Hermes ecosystem notes. Verdict: reuse Hermes state/audit/brief substrate and build only Flyer-specific report rules.

## No-Deploy / No-Mutation Boundary

- No deploy, merge, VPS mutation, customer state mutation, manual-queue mutation, WhatsApp send, provider routing change, or dashboard change.
- No runtime code/prompt/model/SKILL self-modification.
- Offline/mocked tests only.
- `tools/flyer-self-evaluation.py` remains report-only. Any `--out` write is an operator-requested local report write, not production state mutation.

## Current Capability Snapshot

`tools/flyer-self-evaluation.py` already detects:

- `manual_source_edit_stale`
- `source_contract_missing`
- `source_contract_qa_missing`
- customer-copy leaks from outbound body fields when present
- static copy leaks when `--scan-source-copy` is requested
- repeated status check-ins
- stuck generation/finalizing states

Known gaps for this slice:

- Secret-like values in loaded project/log evidence are not redacted before output.
- Source-aware QA evidence is marker-based and should distinguish generic passed QA from actual source-contract/integrity/operator-review evidence.
- Missing source contract and source QA incidents do not expose enough source-contract readiness detail for operator triage.
- `required_text` and source-contract fields need report-side gap checks against locked facts and QA evidence.
- Operator brief can surface top incidents, but source-contract/QA gaps and stale manual rows are not summarized as first-class lines.

## Report Shape Additions

Each source-contract incident must include an `evidence_details` object with stable keys where relevant:

- `has_reference_media`
- `exact_source_edit_cues`
- `has_source_contract`
- `source_contract_fields_present`
- `locked_fact_missing`
- `qa_report_count`
- `accepted_qa_source`
- `qa_missing_required_text`
- `forbidden_text_hits`
- `queued_age_minutes`
- `customer_impact`

New incident types in this slice:

- `source_contract_locked_fact_gap`: contract-required headings/text/sections/replacements are missing from `locked_facts`.
- `source_contract_qa_fact_gap`: contract-required headings/text/sections are not evidenced in a passed source-aware QA report.
- `source_contract_forbidden_text_present`: forbidden source text still appears in QA extracted text.

## Implementation Tasks

- [ ] Write failing tests in `tests/test_flyer_self_evaluation.py` for secret redaction, explicit source-aware QA evidence, `required_text` not represented in locked facts/QA, and richer readiness details.
- [ ] Write/update `tests/test_operator_brief.py` for grouped Flyer self-evaluation lines.
- [ ] Harden `tools/flyer-self-evaluation.py`:
  - implement one recursive report-sanitization pass before JSON/Markdown output, covering incidents, `evidence_details`, suggested actions, eval candidates, `needs_srini`, boundaries, and operator-brief summary lines;
  - redact API keys/tokens/Bearer values/`sk-*` strings, secret env var assignments, E.164 phones, chat IDs/LIDs, and local absolute media paths unless the field is explicitly intended as a project identifier;
  - do not emit raw `raw_request`, full outbound bodies, env files, or provider credential details; emit bounded summaries instead;
  - tighten `has_source_qa()` so only `qa_source="operator_review"` OR a passed QA report plus report-side verification of actual `FlyerSourceContract` obligations counts; provider names, warning text, and generic marker strings must not satisfy source-aware QA by themselves;
  - treat source-aware QA obligations as represented only by existing Hermes/Flyer fields: source-derived required locked facts with `fact_id` prefixes `source_heading:`, `source_section:`, or `source_required_text:` and/or `source_contract.forbidden_substrings`;
  - add source-contract readiness details for exact-source-edit projects with reference image + preservation language;
  - flag `source_contract_locked_fact_gap`, `source_contract_qa_fact_gap`, and `source_contract_forbidden_text_present`;
  - keep decisions.log outbound-body limitation explicit in JSON/Markdown boundaries.
- [ ] Update `tools/operator-brief.py` only within existing Flyer self-evaluation JSON summarization:
  - group lines by operator action: stale manual queue, source-contract gaps, QA gaps, repeated customer check-ins, and Needs Srini;
  - include stale manual source-edit count/oldest age and source-contract/QA gap counts when present;
  - keep each line one-screen friendly and redacted;
  - add no new data sources or production probes.
- [ ] If needed, append one scoped follow-up section to this plan or `tasks/todo.md`; do not edit unrelated backlog/docs and do not touch PR #147-owned provider-routing files/tests.
- [ ] Run focused verification:
  - `python -m pytest tests/test_flyer_self_evaluation.py tests/test_operator_brief.py -q`
  - `python -m py_compile tools/flyer-self-evaluation.py tools/operator-brief.py`
  - `python tools/flyer-self-evaluation.py --format json --projects tests/fixtures/flyer_self_eval/source_contract_gap_projects.json --decisions-log tests/fixtures/flyer_self_eval/decisions.log`
  - `python tools/flyer-self-evaluation.py --format markdown --scan-source-copy --projects tests/fixtures/flyer_self_eval/source_contract_gap_projects.json --decisions-log tests/fixtures/flyer_self_eval/decisions.log`
  - `git diff --check`

## Acceptance Criteria

- Deterministic read-only report surfaces the real source-edit/source-contract silent-failure classes.
- Generic passed QA does not satisfy source-aware QA.
- Source-aware QA is accepted when `qa_source=operator_review` or explicit source-contract/source-integrity evidence exists.
- Exact source-edit projects with reference images and no source contract are reported.
- Stale manual source-edit rows are reported after threshold.
- Secret-like values are redacted from report output.
- decisions.log outbound-body limitation is documented in report boundaries and Markdown.
- `source_contract_locked_fact_gap`, `source_contract_qa_fact_gap`, and `source_contract_forbidden_text_present` have deterministic tests.
- Operator brief includes Flyer self-eval status, grouped top incidents, stale manual queue summary, source-contract/QA gap summary, repeated check-ins, and Needs Srini.
- No provider behavior changes, no customer behavior changes, no production mutation.

## Deferred Items

- Source-edit provider routing validation and production smoke for PR #147 or its merged successor.
- Actual Hermes vision/OCR source-contract extractor improvement.
- Report/audit legacy or malformed projects where `reference_extractions[].source_contract` was not projected into source-derived `locked_facts`; do not add a new projection path in this slice.
- Production delivery-gate enforcement that requires a source-aware QA report before preview/delivery across every source-edit path.
- Hermes cron/automation wiring for periodic self-eval alerts after report semantics are stable.
