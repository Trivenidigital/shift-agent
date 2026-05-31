**Drift-check tag:** extends-Hermes

# Flyer Generation Failure Audit Truth - 2026-05-31

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Flyer routing/generation | Existing cf-router hooks own deterministic generation orchestration. | Extend existing audit outcome logic; do not add routing or orchestration substrate. |
| Audit | Existing `audit_intercepted` reasons and rc values already represent `flyer_primary_failed`. | Reuse existing audit variants; no schema change. |
| Customer messaging | Existing `_send_generation_failure_customer_update` owns deterministic fallback copy. | Keep customer copy unchanged; only classify the audit row truthfully. |

Awesome Hermes Agent ecosystem check: this is an existing audit-classification bug in cf-router. Verdict: extend current hook behavior.

## Drift findings

- SOURCE/NEW `NEW` now audits generation failure as `flyer_primary_failed` / rc=2.
- Older primary-create, active-intake, and reference-scope `use as reference` paths still classify generation failure as `flyer_primary_project_created` when the customer fallback send succeeds.
- That makes cockpit/operator analytics disagree across equivalent failure paths.

## Slice

- [x] Add routing tests for active-intake, primary-create, and reference-scope generation failure audit truthfulness.
- [x] Reuse the existing `flyer_primary_failed` audit reason / rc=2 when `trigger_generate_flyer_concepts` fails, while preserving customer fallback copy.
- [x] Run focused routing tests, reviewer pass, and broad/full tests.
- [ ] Commit, PR, merge, and deploy.

## Verification so far

- RED focused: `python -m pytest tests/test_cf_router_flyer_routing.py::test_active_intake_generation_failure_does_not_send_duplicate_initial_ack tests/test_cf_router_flyer_routing.py::test_primary_create_generation_failure_audits_failed_even_when_customer_update_sent -q` -> `1 failed, 1 passed`; primary-create failure still audited as `flyer_primary_project_created`.
- GREEN focused: `python -m pytest tests/test_cf_router_flyer_routing.py::test_active_intake_generation_failure_does_not_send_duplicate_initial_ack tests/test_cf_router_flyer_routing.py::test_primary_create_generation_failure_audits_failed_even_when_customer_update_sent tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_generation_failure_releases_access -q` -> `3 passed`.
- RED reference-scope focused: `python -m pytest tests/test_cf_router_flyer_routing.py::test_reference_scope_use_reference_generation_failure_audits_failed -q` -> `1 failed`; reference-scope failure still audited as `flyer_primary_project_created`.
- GREEN expanded focused: `python -m pytest tests/test_cf_router_flyer_routing.py::test_reference_scope_use_reference_generation_failure_audits_failed tests/test_cf_router_flyer_routing.py::test_active_intake_generation_failure_does_not_send_duplicate_initial_ack tests/test_cf_router_flyer_routing.py::test_primary_create_generation_failure_audits_failed_even_when_customer_update_sent tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_generation_failure_releases_access -q` -> `4 passed`.
- Reviewer RED adjacent send-failure rc: `python -m pytest tests/test_cf_router_flyer_routing.py::test_active_intake_preview_delivery_failure_audits_send_failure_rc -q` -> `1 failed`; successful generation with preview delivery failure audited rc=2.
- GREEN reviewed focused set: `python -m pytest tests/test_cf_router_flyer_routing.py::test_active_intake_preview_delivery_failure_audits_send_failure_rc tests/test_cf_router_flyer_routing.py::test_reference_scope_use_reference_generation_failure_audits_failed tests/test_cf_router_flyer_routing.py::test_active_intake_generation_failure_does_not_send_duplicate_initial_ack tests/test_cf_router_flyer_routing.py::test_primary_create_generation_failure_audits_failed_even_when_customer_update_sent tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_generation_failure_releases_access -q` -> `5 passed`.
- Routing file: `python -m pytest tests/test_cf_router_flyer_routing.py -q` -> `315 passed`.
- Full suite first run: `python -m pytest -q` -> `1 failed, 2803 passed, 867 skipped`; replay fixture still expected old `flyer_primary_project_created` audit for a mocked generation failure.
- Replay fixture updated to express `primary_audit_reason`; `python -m pytest tests/test_flyer_incident_replay.py -q` -> `12 passed`.
- Full suite final: `python -m pytest -q` -> `2804 passed, 867 skipped, 40 warnings`.
- Diff whitespace: `git diff --check` -> exit 0.
