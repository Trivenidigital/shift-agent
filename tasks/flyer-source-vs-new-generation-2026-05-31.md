**Drift-check tag:** extends-Hermes

# Flyer SOURCE/NEW New-Path Generation - 2026-05-31

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| SOURCE/NEW routing | Existing cf-router hook owns SOURCE/NEW choice consumption and project creation. | Extend the existing `NEW` branch; do not add a new router or state store. |
| Flyer generation | Existing Flyer actions own quota reservation, processing ack, concept generation, preview delivery, and access release. | Reuse the same primary-path helpers in the `NEW` branch. |
| Audit | Existing `audit_intercepted` and `audit_source_vs_new` own traceability. | Reuse existing audit reasons/details. |

Awesome Hermes Agent ecosystem check: this is a local hook wiring gap in the existing SOURCE/NEW workflow. Verdict: extend the current cf-router hook.

## Drift findings

- The primary create path generates immediately when the created project has required fields.
- The SOURCE branch already mirrors preflight/generation/manual fallback behavior.
- The NEW branch creates a project but only sends an intake/manual ack, so a complete `NEW` choice stalls until another customer message.

## Slice

- [x] Add RED hook test: complete `NEW` choice reserves access, sends processing ack, generates, and dispatches preview.
- [x] Wire the `NEW` branch to reuse the primary-path generate/send logic for complete projects, while preserving the existing intake/manual ack for incomplete/manual rows.
- [x] Run focused routing tests, reviewer pass, and broad/full tests.
- [ ] PR, merge, deploy.

## Verification so far

- RED focused: `python -m pytest tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_generates_when_project_is_ready -q` -> failed because the ready `NEW` branch called `send_flyer_intake_ack`.
- GREEN focused: `python -m pytest tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_generates_when_project_is_ready tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_generation_failure_releases_access tests/test_cf_router_flyer_routing.py::test_source_vs_new_new_choice_creates_project_without_manual_edit -q` -> `3 passed`.
- Reviewer pass: local Claude Code routing, quota/safety/customer messaging, and Hermes/drift reviewers approved. Noted follow-up: primary-create audit still classifies some generation failures less precisely than this new `NEW` branch.
- GREEN routing file: `python -m pytest tests/test_cf_router_flyer_routing.py -q` -> `312 passed`.
- GREEN full suite: `python -m pytest -q` -> `2801 passed, 867 skipped, 40 warnings`.
- Diff hygiene: `git diff --check` -> clean.
