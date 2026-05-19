# Flyer Studio QA round-2 fixes — design

**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. Plan reference: `tasks/flyer-qa-round-2-fixes-plan.md`.

## Hermes-first capability checklist

| # | Implementation step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | Convert `_handle_session_control` back map from static dict to inline conditional that branches on `session.plan_id == "trial"` for `confirming_summary`. | `[net-new]` ~12 LOC. |
| 2 | Remove duplicate Literal entries at `schemas.py:3624` and `:3627`. | `[net-new]` ~2 LOC. |
| 3 | Add `tests/test_flyer_onboarding.py::test_trial_back_from_summary_returns_to_business_profile` + paid regression test. | `[net-new]` ~30 LOC. |
| 4 | Add `tests/test_cf_router_flyer_routing.py::test_language_menu_position_five_is_tamil` pinning the prompt order. | `[net-new]` ~15 LOC. |
| 5 | Edit four scenario tuples in `tasks/generate_flyer_qa_scenarios.py`; bump README "Generated" timestamp; regenerate `tasks/flyer-studio-qa-scenarios.xlsx`. | `[net-new]` scenario text + regen. |
| 6 | `cd web/frontend && npm run build` to regenerate `schema.ts`. | `[Hermes]` — existing build chain. |
| 7 | Existing `tests/test_catering_proposal_schemas.py::test_cf_router_reason_accepts_flyer_intercepts` confirms the dedup. | `[Hermes]` — existing test substrate. |

Awesome-Hermes-Agent ecosystem check: no installable skill applies.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/onboarding.py` (`_handle_session_control` back map at lines 446-470; trial-skips-plan flow around line 419).
- ✅ Read `src/agents/flyer/intake.py` (`LANGUAGES` at 32-44; `_language_prompt` body around 340-360).
- ✅ Read `src/platform/schemas.py` (`CfRouterIntercepted.reason` Literal at 3600-3631; duplicate entries at 3608+3624 and 3609+3627).
- ✅ Read `web/frontend/src/api/schema.ts` (lines 1770-1800; current query block for `customers_flyer_customers_get` lacks `offset`/`limit`).
- ✅ Read `web/frontend/src/generated/openapi.json` (lines 1703-1747; offset/limit declarations already present).
- ✅ Read `tasks/generate_flyer_qa_scenarios.py` (HEADERS/AREAS at 14-28, `onboarding_scenarios` + `text_mode_scenarios` + `active_project_scenarios` for FS-A1-010, FS-A1-015, FS-A2-012, FS-A4-006 tuples; `_render_readme_sheet`/`build_workbook` for "Generated" timestamp).
- ✅ Read `tests/test_flyer_onboarding.py` (fixture pattern via `handle_onboarding_message` + `FlyerCustomerStore` direct construction; no existing BACK-from-summary test).
- ✅ Read `tests/test_cf_router_flyer_routing.py` (`importlib.machinery.SourceFileLoader` pattern for cf-router actions; mirror for intake.py if needed).
- ✅ Read `tests/test_catering_proposal_schemas.py` (parametrize at 196-221 covers both reasons; dedup will not break it).

## Per-bug code sketches

### BUG-001 — Trial-path BACK from confirming_summary

`src/agents/flyer/onboarding.py` around lines 457-470, replace the static `back` dict with:

```python
    back = {
        "collecting_business_address": ("collecting_business_name", {"business_name": ""}),
        "collecting_public_phone": ("collecting_business_address", {"business_address": ""}),
        "collecting_business_whatsapp": ("collecting_public_phone", {"public_phone": None}),
        "collecting_authorized_request_number": ("collecting_business_whatsapp", {"business_whatsapp_number": None}),
        "collecting_business_profile": ("collecting_authorized_request_number", {"authorized_request_number": None}),
        "choosing_plan": ("collecting_business_profile", {"business_category": "", "preferred_language": "en"}),
    }
    # `confirming_summary` BACK depends on the session's plan path: trial
    # sessions skip `choosing_plan` entirely (see forward path around line
    # 419), so BACK must skip it on the return trip too — otherwise a trial
    # user pressing BACK at the summary loses `plan_id="trial"` and lands
    # in the paid plan chooser.
    if session.status == "confirming_summary":
        if session.plan_id == "trial":
            back["confirming_summary"] = ("collecting_business_profile", {"business_category": "", "preferred_language": "en"})
        else:
            back["confirming_summary"] = ("choosing_plan", {"plan_id": ""})
```

Tests in `tests/test_flyer_onboarding.py`:

```python
def test_trial_back_from_summary_returns_to_business_profile(tmp_path):
    # Build a trial session at confirming_summary with all required fields
    # set. Send BACK. Assert status == "collecting_business_profile",
    # plan_id is still "trial", business_category cleared. Use the existing
    # handle_onboarding_message fixture pattern.
    ...


def test_paid_back_from_summary_returns_to_choosing_plan(tmp_path):
    # Same setup but plan_id="" (paid path through choosing_plan).
    # Send BACK. Assert status == "choosing_plan", plan_id cleared.
    # Regression guard for existing paid behavior.
    ...
```

### Hygiene — Dedupe `CfRouterIntercepted.reason` Literal

`src/platform/schemas.py`: remove the two duplicate lines at 3624 (`"flyer_starter_brief"`) and 3627 (`"flyer_customer_not_active"`). The originally inserted entries at lines 3608+3609 (alphabetically/logically grouped with the onboarding reasons) remain.

No test change. Confirm `tests/test_catering_proposal_schemas.py::test_cf_router_reason_accepts_flyer_intercepts` still passes (Pydantic v2 dedupes Literal members at construct time, so the existing parametrize already exercises both names).

### BUG-002 — Pin language menu order

Add to `tests/test_flyer_onboarding.py` (NOT `test_cf_router_flyer_routing.py`). Reason: `intake.py` imports `from schemas import …` bare; `test_flyer_onboarding.py` already has `src/platform/` on `sys.path` (lines 12-13), so the import requires zero extra wiring. The cf-router test file lacks the `src/platform` path and would need an `importlib` shim or sys.path edits.

```python
def test_language_menu_pins_deployed_order_at_positions_4_through_6():
    """BUG-FLYER-QA-2026-05-19-002: pin the deployed menu order so a future
    reorder is caught at PR time, not at QA time. Workbook FS-A2-012 must
    agree with `parse_language_choice("5") == "ta"`. We pin positions 4-6
    explicitly so an accidental swap of any adjacent pair is caught (a
    single-position pin would miss e.g. a 4↔5 swap)."""
    from agents.flyer.intake import parse_language_choice, _language_prompt
    assert parse_language_choice("4") == "ml"
    assert parse_language_choice("5") == "ta"
    assert parse_language_choice("6") == "kn"
    prompt = _language_prompt()
    assert "4. Malayalam" in prompt
    assert "5. Tamil" in prompt
    assert "6. Kannada" in prompt
```

Workbook update: in `tasks/generate_flyer_qa_scenarios.py`, edit the FS-A2-012 expected result tuple from "Reply '5'" → "Kannada" to "Reply '5'" → "Tamil" (case-preserving). Bump README "Generated" timestamp to `"2026-05-19"`.

### BUG-003 — Regenerate `schema.ts`

```bash
cd web/frontend
npm run build
```

This invokes `openapi-typescript src/generated/openapi.json -o src/api/schema.ts` (verified in PR #106 build log) followed by `tsc -b && vite build`. The regenerated `schema.ts` must show the `customers_flyer_customers_get` query block expanded:

```typescript
query?: {
    query?: string;
    segment?: string;
    offset?: number;
    limit?: number;
};
```

Before committing: `git diff -- web/frontend/src/api/schema.ts` and verify the changes are limited to the `customers_flyer_customers_get` block. If any other endpoint surfaces drift, inspect and decide whether to roll it in or defer.

### Workbook scenario refresh

Scenario IDs (`FS-XX-NNN`) are not present in the source file — they're constructed at render time from `(area_code, i)` per `*_scenarios()` function. To locate a target scenario, count `out.append(...)` calls within the relevant function starting at `i=1`. Approximate source locations confirmed by reading:
- FS-A1-010 → `onboarding_scenarios()` item 10 (around lines 114-121)
- FS-A1-015 → `onboarding_scenarios()` item 15 (around lines 154-161)
- FS-A2-012 → `text_mode_scenarios()` item 12 (around lines 312+)
- FS-A4-006 → `active_project_scenarios()` item 6 (around lines 548-555)

Edits in `tasks/generate_flyer_qa_scenarios.py`:

- `onboarding_scenarios()` FS-A1-010 tuple: rewrite to a happy-path SKIP test (full 10-column tuple required — pre-rewrite was P2/Negative; the rewritten happy-path is P1/Happy):
  - **Scenario**: `"SKIP at collecting_business_whatsapp after public phone saved advances to next step"`
  - **Preconditions**: `"Onboarding at collecting_business_whatsapp; public_phone already saved"`
  - **Steps**: `"1. Reply 'SKIP' at collecting_business_whatsapp."`
  - **Expected**: `"business_whatsapp_number=None; status -> collecting_authorized_request_number; next prompt sent."`
  - **Priority**: `"P1"` (was P2)
  - **Type**: `"Happy"` (was Negative)
  - **Channel**: `"WhatsApp"`
  - **Notes**: `"Round-2 rewrite — original precondition was structurally unreachable."`

- `onboarding_scenarios()` FS-A1-015 tuple: update expected result:
  - **Expected** (was): `"Status returns to collecting_business_address; prior address cleared; address prompt re-sent."` (still applies for non-summary BACK).
  - **Expected** (now): split into two cases — for the summary-step BACK on trial sessions, add note: `"Trial path: BACK from confirming_summary returns to collecting_business_profile; plan_id stays 'trial'. Paid path: BACK from confirming_summary returns to choosing_plan; plan_id cleared."`
  - **Notes**: `"Pinned by tests/test_flyer_onboarding.py::test_trial_back_from_summary_returns_to_business_profile."`

- `text_mode_scenarios()` FS-A2-012 tuple: replace "Kannada" with "Tamil" in scenario, steps, expected, and notes.

- `active_project_scenarios()` FS-A4-006 tuple: rewrite scenario title from `"force_new: customer says 'start a new flyer'"` to `"vague start: customer says 'start a new flyer'"`; replace expected result with `"is_vague_flyer_start=True; starter brief sent; audit reason='flyer_starter_brief'. No force_new path for vague phrasing — PR #102 starter-brief gate intercepts before should_start_new_flyer_over_active."`.

- README sheet `"Generated"` field: bump to `"2026-05-19"`.

Then regenerate: `python tasks/generate_flyer_qa_scenarios.py`. Verify total scenario count stays at 117 (printed by the script).

## Build sequence (matches plan)

1. BUG-001 trial-BACK (~12 LOC + ~30 LOC tests).
2. Hygiene dedup (~2 LOC).
3. BUG-002 menu-order test (~15 LOC) + workbook scenario edit.
4. BUG-003 schema.ts regen (auto).
5. Workbook scenario refresh + xlsx regen + timestamp bump.

Final verification:
```bash
pytest tests/test_flyer_*.py tests/test_cf_router_flyer_routing.py tests/test_dispatcher_accuracy_report.py tests/test_catering_proposal_schemas.py web/backend/tests/test_flyer_admin.py -q
cd web/frontend && npm run build
git diff --check
```

## Test fixture conventions

- All onboarding tests use `tmp_path` + direct `handle_onboarding_message` invocation (matches `tests/test_flyer_onboarding.py` style).
- Language menu test mirrors `tests/test_cf_router_flyer_routing.py` `importlib`/`SimpleNamespace` pattern OR uses direct `from agents.flyer.intake import …` (simpler, since `intake.py` doesn't need cf-router fixture wiring).
- Dedup confirmation uses `tests/test_catering_proposal_schemas.py::test_cf_router_reason_accepts_flyer_intercepts` — no test addition.
