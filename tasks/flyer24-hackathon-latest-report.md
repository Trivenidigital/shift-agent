# Flyer24 Hackathon Latest Report

Updated: 2026-05-27T22:33:00Z

## Current batch
- Branch: `codex/flyer24-batch-status-title-hardening-202605272245`
- PR: #314 `fix(flyer): harden campaign title and manual status targeting`
- Deploy: not run (PR stage)
- Scope: harden campaign-title extraction and status-target project selection so stale/manual queue status checks stay deterministic.
- Root-cause evidence:
  - `tests/test_flyer_facts.py::test_profile_facts_keep_account_business_when_request_names_campaign_flyer` failed on `main`: campaign title was `Special Biryani's Flyer` not `Special Biryani's`.
  - `tests/test_flyer_project_isolation.py::test_scenario3_status_check_on_stale_manual_edit_still_returns_manual_status` failed on `main`: status response targeted unrelated project id.
  - Active/manual status branch duplicated status-project resolution logic, increasing drift risk.
- Risk: low (deterministic normalization + routing target selection, no payment/quota mutation).
- Hermes/MCP-first: Hermes owns ingress/sender/audit substrate; net-new is Flyer-local status/copy/fact normalization only.

## Batch issue list fixed
1. Strip trailing medium words (`flyer`/`poster`/`banner`) from campaign title normalization.
2. Add regression coverage for campaign-title suffix stripping.
3. Centralize status-target project resolution for status checks.
4. Keep active `manual_edit_required` project as status target unless customer explicitly mentions another project id.
5. Reuse central resolver in both status-check branches to avoid drift.
6. Add regression coverage for generic `any update?` with a newer unrelated status row.

## PR queue classification refresh
- #299 `fix(flyer): harden billing health MCP readiness visibility` - operator-review-required (money-adjacent), open.
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - operator-review-required (money-adjacent), open.
- #312 `fix(flyer): canonicalize legacy manual-review reasons` - merged to `main`.
- #314 `fix(flyer): harden campaign title and manual status targeting` - open, low-risk, no CI checks reported yet (not merge-qualified yet).

## Running PR list (hackathon)
- #295 `fix(flyer): close status-check phrasing gaps in active project routing` - merged to `main`; deployed `deploy-20260527-102236-c858caa1`.
- #296 `fix(flyer): route sample-prompt lexical variants to starter ideas` - merged to `main`; deployed `deploy-20260527-112213-f019b345`.
- #297 `fix(flyer): route sample-request tagline/slogan variants to idea intake` - merged to `main`; deployed `deploy-20260527-121058-87db7152`.
- #298 `fix(flyer): close render dependency and recovery alert gaps` - merged to `main`; deployed in later train.
- #299 `fix(flyer): harden billing health MCP readiness visibility` - open; operator-review-required.
- #303 `fix(flyer): route sample ask-shape variants to starter ideas` - merged to `main`.
- #304 `fix(flyer): route missed sample request phrase variants to starter ideas` - merged to `main`.
- #305 `fix(flyer): normalize manual queue triage status and reason signals` - merged to `main`.
- #306 `fix(flyer): expand manual queue health backlog signals` - merged to `main`.
- #307 `fix(flyer): consolidate payment fail-closed contract and MCP parity` - open; operator-review-required.
- #312 `fix(flyer): canonicalize legacy manual-review reasons` - merged to `main`.
- #314 `fix(flyer): harden campaign title and manual status targeting` - open; low-risk, awaiting review/check signal.

## Verification for this batch
- `pytest -q tests/test_flyer_facts.py -k 'campaign_title or profile_facts_keep_account_business_when_request_names_campaign_flyer'` ✅ (2 passed)
- `pytest -q tests/test_flyer_project_isolation.py -k 'scenario3_status_check'` ✅ (2 passed)
- `pytest -q tests/test_flyer_* --maxfail=20` ✅ (945 passed, 1 skipped)
- `python3 -m py_compile src/agents/flyer/facts.py src/plugins/cf-router/hooks.py tests/test_flyer_facts.py tests/test_flyer_project_isolation.py` ✅
- `git diff --check` ✅

## Runtime checks snapshot
- `systemctl --failed`: unrelated pre-existing failed unit remains (`logrotate.service`).
- Flyer/shift timers active: `flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog`, `shift-agent-health`, `shift-agent-tail-logger`.
