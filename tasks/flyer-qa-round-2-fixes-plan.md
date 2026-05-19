# Flyer Studio QA round-2 fixes — plan

**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. Bug fixes against deployed state machine, schema dedup, openapi regeneration, workbook scenario refresh.

## Hermes-first capability checklist

| # | Step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | Trial customer presses BACK at `confirming_summary` while `session.plan_id == "trial"` (inbound WhatsApp). | `[Hermes]` — per-VPS state + WhatsApp ingress through Hermes gateway. |
| 2 | `_handle_session_control()` reads the back-chain and decides where to route. Currently routes every BACK from summary to `choosing_plan`. | `[net-new]` — Flyer-specific state machine; plan-aware branch needed. ~12 LOC. |
| 3 | `_reply_for_session()` re-renders the prior step's prompt. | `[Hermes]` — existing rendering substrate, no changes. |
| 4 | Workbook scenario FS-A2-012 contradicts deployed menu order at positions 4-8. | `[net-new]` — workbook scenario edit + add a regression test that pins the deployed order. ~25 LOC tests. |
| 5 | Typed `api.GET<paths["customers_flyer_customers_get"]>` consumer references `web/frontend/src/api/schema.ts`. | `[Hermes]` — `api.GET` is platform substrate. |
| 6 | `schema.ts` currently omits `offset` / `limit` for `/flyer/customers`; `openapi.json` already has them. | `[net-new]` — regenerate schema.ts via existing `openapi-typescript` step in `npm run build`. Auto-generated; no hand edits. |
| 7 | `CfRouterIntercepted.reason` Literal in `schemas.py` has duplicate entries for `flyer_starter_brief` and `flyer_customer_not_active`. | `[net-new]` — dedupe ~2 lines. |
| 8 | Pydantic v2 accepts duplicate Literal members; `mypy --strict` and JSON-schema export surface them as noise. | `[Hermes]` — Pydantic + Literal substrate; no change. |
| 9 | Workbook artifact regenerated via `tasks/generate_flyer_qa_scenarios.py` after scenario rewrites. | `[net-new]` — scenario text edits for FS-A1-010, FS-A1-015, FS-A2-012, FS-A4-006. ~10 LOC of cell-text edits. |
| 10 | `npm run build` runs `openapi-typescript src/generated/openapi.json -o src/api/schema.ts` then `tsc -b && vite build`. | `[Hermes]` — existing build chain. |
| 11 | Updated workbook + plan + design + audit findings checked into the same PR. | `[Hermes]` — git workflow. |

Awesome-Hermes-Agent ecosystem check: no installable skill applies. All fixes are in-tree bug fixes layering onto existing substrate.

Net-new effort: ~14 LOC hand-written + ~50 LOC tests + ~30 lines auto-regenerated + workbook scenario edits.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/onboarding.py` (`_handle_session_control` back map at lines 446-470) — confirmed `confirming_summary` → `choosing_plan` unconditional with `{"plan_id": ""}`.
- ✅ Read `src/agents/flyer/intake.py` (`LANGUAGES` list at lines 32-44) — confirmed deployed order: `1.English 2.Telugu 3.Hindi 4.Malayalam 5.Tamil 6.Kannada 7.Gujarati 8.Marathi 9.Punjabi 10.Spanish 11.Mixed/Other`.
- ✅ Read `src/platform/schemas.py` (`CfRouterIntercepted.reason` Literal at lines 3600-3631) — confirmed duplicate entries `flyer_starter_brief` (3608+3624) and `flyer_customer_not_active` (3609+3627).
- ✅ Read `web/frontend/src/api/schema.ts` (lines 1770-1800) — confirmed `customers_flyer_customers_get.parameters.query` only declares `query?` and `segment?` (no `offset`/`limit`).
- ✅ Read `web/frontend/src/generated/openapi.json` (lines 1703-1747) — confirmed openapi.json already exposes `offset` (default 0) + `limit` (default 300); only schema.ts is stale.
- ✅ Read `tasks/generate_flyer_qa_scenarios.py` (header at lines 1-30) — own authorship; will edit FS-A1-010, FS-A1-015, FS-A2-012, FS-A4-006 scenario tuples in `onboarding_scenarios()`, `text_mode_scenarios()`, `active_project_scenarios()`.
- ✅ Read `tests/test_flyer_onboarding.py` (header at lines 1-20) — fixture pattern: `tmp_path` + direct `handle_onboarding_message` invocation; no TestClient required.
- ✅ Read `tests/test_cf_router_flyer_routing.py` (header at lines 1-15) — fixture pattern: `importlib.machinery.SourceFileLoader` on `cf-router/actions.py`, exposed as module under test.
- ✅ Read `tests/test_catering_proposal_schemas.py` (existing CfRouterIntercepted parametrize at lines 196-221) — already covers `flyer_starter_brief` and `flyer_customer_not_active`; dedup will not break it (Pydantic dedupes at construct time).

## Scope (5 fixes)

### BUG-FLYER-QA-2026-05-19-001 (P1) — Trial-path BACK from confirming_summary

**Cause:** `_BACK_TRANSITIONS["confirming_summary"]` is hardcoded to `("choosing_plan", {"plan_id": ""})`. Trial sessions skip `choosing_plan` entirely; pressing BACK at the summary drops them into the paid-plan menu and clears `plan_id`.

**Fix:** Convert the static `back` dict into a callable (or inline conditional) that inspects `session.plan_id`. When `plan_id == "trial"`, BACK from `confirming_summary` routes to `collecting_business_profile`; otherwise existing behavior (paid path routes to `choosing_plan` with `plan_id=""`).

**Files touched:** `src/agents/flyer/onboarding.py`.

**Tests (`tests/test_flyer_onboarding.py`):**
- Trial session at `confirming_summary` → reply `"BACK"` → status returns to `collecting_business_profile`, `business_category` cleared, `plan_id` stays `"trial"`.
- Paid session at `confirming_summary` → reply `"BACK"` → status returns to `choosing_plan`, `plan_id` cleared (regression guard for existing paid behavior).

### BUG-FLYER-QA-2026-05-19-002 (P1) — Language menu order divergence

**Cause:** Workbook scenario FS-A2-012 expects `5=Kannada`. Deployed `LANGUAGES` in `intake.py` orders the menu `4=Malayalam, 5=Tamil, 6=Kannada, 7=Gujarati, 8=Marathi`. Code is internally consistent (rendered menu matches parse), so customers aren't misrouted relative to what they see, but the workbook is the canonical QA spec and contradicts deployed truth.

**Fix:**
- Update the workbook scenario text in `tasks/generate_flyer_qa_scenarios.py` (FS-A2-012 expected result) to match deployed menu order: "Reply '5'" → "Tamil".
- Regenerate `tasks/flyer-studio-qa-scenarios.xlsx` from the updated generator.
- Add a regression test that pins the deployed menu order so any future reordering is caught at PR time, not at QA time.

**Files touched:** `tasks/generate_flyer_qa_scenarios.py`, `tasks/flyer-studio-qa-scenarios.xlsx` (regenerated), `tests/test_cf_router_flyer_routing.py` or `tests/test_flyer_onboarding.py` (new test).

**Tests:** Build the language prompt, assert `"5. Tamil"` substring + assert `parse_language_choice("5") == "ta"`.

### BUG-FLYER-QA-2026-05-19-003 (P2) — `schema.ts` stale on `/flyer/customers` pagination

**Cause:** PR #106 added `offset` + `limit` query params to `/flyer/customers` and regenerated `openapi.json`, but the typed `schema.ts` was not regenerated. Frontend currently calls the endpoint via a raw URL string (`FlyerAdmin.tsx:160`) so there's no runtime impact, but any future typed consumer would be missing the param types — and `npm run build` dirties the worktree because the regenerator step lands the missing fields.

**Fix:** Run `npm run build` from `web/frontend/` (which runs `openapi-typescript src/generated/openapi.json -o src/api/schema.ts`), then commit the regenerated file. No hand edits. `openapi-typescript` regenerates the entire file from `openapi.json`, so verify the resulting diff is limited to the `customers_flyer_customers_get` block before committing. If any other endpoint surfaces (e.g., from drift since the last regen), inspect that delta and either roll it in or defer to a follow-up — do not commit a multi-endpoint diff silently.

**Files touched:** `web/frontend/src/api/schema.ts` (regenerated only).

**Tests:** Verify by grep that `customers_flyer_customers_get` query block now includes `offset?: number; limit?: number;`. Verify post-build `git diff --check` is clean.

**Follow-up tracked (out-of-scope but flagged):** A CI gate analogous to the existing `Verify committed openapi.json is in sync` step is needed for `schema.ts` so this staleness can't recur silently the next time a backend endpoint adds a query param. Filed as TODO in this plan's out-of-scope list; should be opened as a separate housekeeping issue after this PR merges.

### Hygiene — Dedupe `CfRouterIntercepted.reason` Literal

**Cause:** `flyer_starter_brief` and `flyer_customer_not_active` each appear twice in the Literal (lines 3608+3624 and 3609+3627). Pydantic v2 accepts duplicates without error, but they're noise in `mypy --strict`, IDE hovers, and JSON-schema export.

**Fix:** Remove the duplicate entries at lines 3624 and 3627 (keep the originals at 3608/3609 since they fit the alphabetical/logical grouping around onboarding reasons).

**Files touched:** `src/platform/schemas.py`.

**Tests:** Existing `tests/test_catering_proposal_schemas.py::test_cf_router_reason_accepts_flyer_intercepts` parametrize list already covers both reasons (Pydantic v2 dedupes Literal members at construct time). Run that test specifically to confirm green after the dedup.

### Workbook scenario refresh

**Cause:** Round-1 QA found four workbook scenarios mismatched against deployed behavior:
- FS-A1-010: precondition "at `collecting_business_whatsapp` before public phone saved" is structurally unreachable.
- FS-A1-015: expected behavior to be updated once BUG-001 lands (trial-BACK stays in trial flow).
- FS-A2-012: language menu position 5 mismatch (handled in BUG-002).
- FS-A4-006: "start a new flyer for next week" expected `force_new`; deployed routes to starter brief by design (PR #102).

**Fix:**
- Rewrite FS-A1-010 to a happy-path SKIP test at `collecting_business_whatsapp` with `public_phone` saved.
- Rewrite FS-A1-015 expected result to assert trial-BACK stays in trial flow (paired with the BUG-001 code change).
- Rewrite FS-A2-012 expected result to "Reply '5' → Tamil".
- Rewrite FS-A4-006 expected result AND scenario title (currently `"force_new: customer says 'start a new flyer'"`) to reflect "vague start ⇒ starter brief, not force_new" + note the PR #102 starter-brief gate. Bump workbook README "Generated" timestamp to 2026-05-19.

**Files touched:** `tasks/generate_flyer_qa_scenarios.py` (scenario text), `tasks/flyer-studio-qa-scenarios.xlsx` (regenerated).

## Out of scope (deliberately)

- Adding a CI gate that re-runs `openapi-typescript` and diffs `schema.ts` like the existing openapi.json gate. Useful but separate housekeeping.
- Refactoring `_BACK_TRANSITIONS` into a generic table-driven state machine. Targeted plan-aware branch is the minimal fix.
- Removing the orphan reasons `flyer_reference_scope_use_reference`, `flyer_reference_scope_authorization_requested`, etc. — those are in the Literal but never emitted; harmless, separate housekeeping pass.
- Adding workbook scenarios for new code paths beyond the four flagged. The 117-scenario inventory stays the same.

## Build sequence

Branch `codex/flyer-qa-round-2-fixes` from `main`.

1. Commit 1 (BUG-001 trial-BACK, ~12 LOC + ~25 LOC tests).
2. Commit 2 (Hygiene dedup, ~2 LOC).
3. Commit 3 (BUG-002 menu-order test, ~25 LOC tests).
4. Commit 4 (BUG-003 schema.ts regen, auto-generated).
5. Commit 5 (Workbook scenario refresh, scenario text edits + regenerated xlsx).

Final verification: `pytest tests/test_flyer_*.py tests/test_cf_router_flyer_routing.py tests/test_dispatcher_accuracy_report.py tests/test_catering_proposal_schemas.py web/backend/tests/test_flyer_admin.py -q` + `npm run build` + `git diff --check`.

## Verification matrix

| Bug | Pass criterion |
|---|---|
| 001 | Trial session BACK from summary returns to `collecting_business_profile` with `plan_id="trial"` intact; paid session BACK still goes to `choosing_plan` with `plan_id=""`. |
| 002 | Regenerated workbook FS-A2-012 says "5 → Tamil". Test asserts `parse_language_choice("5") == "ta"` AND prompt contains `"5. Tamil"`. |
| 003 | `web/frontend/src/api/schema.ts` `customers_flyer_customers_get.parameters.query` includes both `offset?: number` and `limit?: number`. `npm run build` is clean (no dirty worktree post-build). |
| Hygiene | `grep -c "\"flyer_starter_brief\"" schemas.py` returns 1; existing parametrize tests still pass. |
| Workbook | FS-A1-010, FS-A1-015, FS-A4-006 rewritten in `generate_flyer_qa_scenarios.py`; regenerated xlsx reflects updates; total stays at 117. |

## Risk

- BUG-001 plan-aware branch must not break the paid-plan BACK path. Test the paid case explicitly.
- BUG-003 schema.ts regeneration must not include unrelated drift; verify the diff is limited to the two new parameter declarations.
- Workbook regeneration must not silently lose scenario IDs or columns; re-run `generate_flyer_qa_scenarios.py` and verify total stays at 117.
