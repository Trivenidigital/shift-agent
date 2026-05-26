**Drift-check tag:** extends-Hermes

# Flyer24 Batch - Manual Queue Visibility and Fail-Closed Status (2026-05-26)

## Hermes-first checklist
1. Parse queued manual-review projects and age/reason metadata -> [Hermes] existing JSON state + schema + lock-safe IO.
2. Present operator triage view with clearer grouping/severity/action hints -> [net-new] Flyer policy/read-model only.
3. Return customer-safe status copy for queued manual-review reasons -> [Hermes] existing status reply plumbing, [net-new] narrow reason-specific wording updates.
4. Keep source-edit provider/readiness failures fail-closed without generation promises -> [Hermes] existing preflight + queueing path, [net-new] clearer surfaced reason fields.
5. Validate with deterministic tests and no runtime mutation -> [Hermes] existing pytest harness.

Net-new scope: read-only visibility and deterministic status-copy hardening only. No payment/account/quota/provider API mutation.

## Batch issue list (6 related fixes)
1. Manual queue rows lack normalized root-cause family for `source_edit_provider_unavailable` vs `visual_qa_failed` triage.
2. Manual queue rows lack deterministic operator next-action hints by reason code.
3. Queue rows do not expose age severity bands, making SLA sorting noisy.
4. Queue rows do not expose whether customer update nudges are due for stale rows.
5. `flyer_manual_edit_status_reply` fallback is source-edit-specific only; non-source reasons can leak generic copy if import fallback triggers.
6. No focused tests pinning the new queue visibility fields and fallback status-reply behavior.

## Planned files
- `src/agents/flyer/manual_queue.py`
- `src/plugins/cf-router/actions.py`
- `tests/test_flyer_manual_queue.py`
- `tests/test_cf_router_plugin.py`
- `tasks/flyer24-hackathon-latest-report.md`

## Verification
- `python3 -m py_compile src/agents/flyer/manual_queue.py src/plugins/cf-router/actions.py`
- `pytest -q tests/test_flyer_manual_queue.py tests/test_cf_router_plugin.py -k "manual_queue or manual_edit_status_reply"`
- `git diff --check`

## Risk
- Low: read-only queue/status visibility plus tests; no payment/account/quota/runtime state mutation.
