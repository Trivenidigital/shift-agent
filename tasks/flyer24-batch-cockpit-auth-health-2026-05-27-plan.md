**Drift-check tag:** extends-Hermes

# Hermes-first checklist
1. Cockpit request auth/OTP enforcement at router dependency boundary -> [Hermes]
2. Flyer health/provider/manual-queue visibility composition -> [net-new]
3. Test harness auth overrides for cockpit route tests -> [net-new]
4. Provider detail/fail-closed customer copy semantics for source-edit readiness -> [net-new]
5. Read-only operator visibility for stale/manual queue mix -> [net-new]
6. Runtime state/audit transport/storage -> [Hermes]

## Batch scope (6 related issues)
1. Fix cockpit test auth override compatibility so existing `auth.require_auth`/`require_fresh_otp` overrides apply again.
2. Restore `/flyer/health` testability under auth override (avoid false 401 in expected-shape and redaction tests).
3. Restore project/media cockpit endpoint testability under auth override (avoid false 401 masking 404/422/200 behavior).
4. Restore deactivate endpoint audit testability under fresh-OTP override (avoid false 401 masking 422 validation semantics).
5. Make source-edit provider detail include stale-threshold + queue impact text even when `source_edit_provider=manual_review`.
6. Make source-edit provider detail call out mixed manual-queue blockers for operator triage.

## Evidence
- CI run 26480835826 on open Flyer PR showed 12 backend failures clustered around auth override regressions and source-edit detail assertions.
- Failure examples: `tests/test_flyer_health.py::test_flyer_health_returns_expected_shape` (401), `tests/test_flyer_admin_cockpit_ops.py::test_project_asset_media_serves_owned_asset` (401), `tests/test_flyer_health.py::test_source_edit_detail_mentions_mixed_reason_backlog` (detail missing backlog context).

## Verification plan
- `python3 -m py_compile web/backend/app/routers/flyer.py web/backend/tests/test_flyer_health.py web/backend/tests/test_flyer_admin.py web/backend/tests/test_flyer_admin_cockpit_ops.py`
- `pytest -q web/backend/tests/test_flyer_health.py web/backend/tests/test_flyer_admin.py web/backend/tests/test_flyer_admin_cockpit_ops.py -k "flyer_health_returns_expected_shape or flyer_health_redacts_secret_values or source_edit_detail_surfaces_queue_impact_when_present or source_edit_detail_mentions_mixed_reason_backlog or deactivate_customer_endpoint_audits_action or project_asset_media_serves_owned_asset or operator_upload_media_serves_well_named_file"`
- `git diff --check`
