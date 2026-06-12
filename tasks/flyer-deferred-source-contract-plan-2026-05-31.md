**Drift-check tag:** extends-Hermes

# Flyer Deferred Source-Contract Extraction

## New primitives introduced

- No new substrate. This slice extends the existing deferred reference extraction path in `generate-flyer-concepts` to include the already-defined `source_edit_template` role.

## Drift-rule self-checks

| Check | Evidence | Decision |
|---|---|---|
| Read routed source-edit project creation | `cf-router/actions.py` adds `--defer-reference-extraction` for reference media; `create-flyer-project` creates `source_edit_template/not_run`. | Keep deferred routing; fix deferred consumer. |
| Read synchronous create path | `create-flyer-project` already populates `FlyerSourceContract`, forbidden substrings, and `source_contract_locked_facts`. | Mirror this behavior in deferred extraction, do not invent a new source contract. |
| Read generate path | `generate-flyer-concepts` only pending-extracts `menu_reference` and `old_flyer_reference`. | Add `source_edit_template` and persist contract/facts before source-edit render. |
| Read renderer | `render_source_edit_preview` consumes project state; it should not run extraction. | Keep renderer as consumer. |

## Hermes-first analysis

Hermes/Flyer already owns media ingestion, reference extraction, source-contract schema, locked-fact projection, and audit. The missing work is a wiring gap in the deferred path.

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Reference/source extraction | Existing Flyer reference extraction provider and Hermes vision substrate. | Reuse `extract_reference`. |
| Source contract | Existing `FlyerSourceContract` and `source_contract_locked_facts`. | Reuse and persist before render. |
| Source-edit rendering | Existing `render_source_edit_preview`. | No render changes. |
| Audit | Existing `FlyerSourceContractExtracted` log entry. | Emit best-effort audit from deferred path too. |

Hermes skill-hub check: https://hermes-agent.nousresearch.com/docs/skills has no separate Flyer source-contract defer skill.

Awesome Hermes ecosystem check: https://github.com/0xNyk/awesome-hermes-agent has no applicable Flyer-specific deferred source-contract primitive.

## Build Checklist

- [x] RED generate-concepts test: deferred `source_edit_template/not_run` extracts contract and source facts before `render_source_edit_preview`.
- [x] RED failure-path test: deferred source-edit extraction provider failure queues manual review and does not render.
- [x] Add `source_edit_template` to pending roles.
- [x] Persist source contract, forbidden substrings, and source locked facts in deferred extraction.
- [x] Emit `FlyerSourceContractExtracted` audit best-effort for deferred source-edit extraction.
- [x] Subagent review.
- [x] Focused verification.
- [x] Full verification.
- [ ] PR, merge, deploy.

## Review Notes

- Hermes/safety review: approved; noted test isolation for the failure audit path, fixed by passing a temp `--audit-log-path` and asserting the failure audit row.
- Structural review: blocked the first pass because role-only `source_edit_template` projects could extract successfully and then fall into normal rendering. Fixed by making successful `source_edit_template` extraction part of source-edit render selection.

## Verification

- `python -m py_compile src/agents/flyer/scripts/generate-flyer-concepts`
- `python -m pytest tests/test_flyer_generate_concepts.py tests/test_flyer_create_project.py tests/test_flyer_reference_extract.py tests/test_flyer_schemas.py -q` -> `170 passed`
- `python -m pytest` -> `2862 passed, 867 skipped`
