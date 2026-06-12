# Flyer24 Manual Queue Visibility Batch Plan (2026-05-24)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. WhatsApp ingress, sender identity, and routing into Flyer project/manual-review states. **[Hermes]**
2. JSON store + lock/atomic write + audit substrate for project/manual states. **[Hermes]**
3. Compute read-only manual queue diagnostics (age precision, stale threshold, reason mix) from existing project state. **[net-new]**
4. Expose read-only Cockpit health/triage fields for operator actionability. **[net-new]**
5. Render new diagnostics in Cockpit UI without changing customer/project/payment state. **[net-new]**

Net-new scope only: steps 3-5.

## Batch issues (target 6)
1. Manual queue age is only exposed as rounded `age_hours`, hiding 1-59 minute rows as `0h`.
2. Triage payload has no stale flag tied to configured threshold, so operators cannot quickly separate fresh vs stale rows.
3. Health `manual_queue_impact` only counts `source_edit_provider_unavailable`; it omits `visual_qa_failed` and other queued reasons that still block delivery.
4. Health impact has no reason histogram, forcing operator guesswork from project-by-project drilldown.
5. Health impact omits minute precision, so near-threshold pages are hard to prioritize.
6. Cockpit table displays only `h` granularity, causing operational ambiguity for SLA triage.

## Verification plan
- Add/extend backend tests for manual queue row age/stale fields and health impact histogram/precision.
- Add frontend tests for age rendering fallback (`Xm` vs `Yh`) and stale indicator.
- Run:
  - `python3 -m py_compile` on touched Python files
  - focused `pytest` for touched backend + Flyer manual queue behavior
  - frontend test/build for touched UI
  - `git diff --check`
