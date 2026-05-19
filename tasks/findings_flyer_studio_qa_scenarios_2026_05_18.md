**Drift-check tag:** extends-Hermes

**New primitives introduced:** None. This was a QA execution pass over the existing Flyer Studio workbook scenarios.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/routing | yes - existing Hermes gateway, `dispatch_shift_agent`, and installed `cf-router` plugin | use existing runtime paths; no custom router for QA |
| Flyer state and asset workflow | yes - existing Flyer Studio scripts/skills in tree and deployed skill pattern | test current implementation; backlog only confirmed gaps |
| Admin dashboard | none specific in Hermes skill hub for Flyer Studio operator UI | test in-tree FastAPI/React dashboard |
| Spreadsheet scenario source | none needed | read workbook as QA source; no new workflow primitive |

Awesome Hermes Agent ecosystem check: no purpose-built Flyer Studio QA executor or flyer-dashboard primitive was used; existing Hermes gateway/state/audit conventions remain the substrate.

## Scope

Source workbook: `tasks/flyer-studio-qa-scenarios.xlsx`

Scenario inventory:

| Area | Count |
|---|---:|
| A1 Onboarding | 26 |
| A2 Text Mode + Starter Briefs | 15 |
| A3 Image / Reference Scope | 15 |
| A4 Active Project / Revisions / Source-Edit | 15 |
| A5 Guest Orders | 14 |
| A6 Admin Dashboard | 17 |
| A7 cf-router Routing | 15 |
| **Total** | **117** |

Execution evidence:

- Extracted all 117 workbook rows from `All Scenarios`.
- Ran focused Flyer/admin regression suite with Windows directory-fsync shim: `186 passed`.
- Ran row-specific probes for workbook expectations not fully covered by the focused suite.
- Ran frontend build from `web/frontend`: passed.
- Root `npm run build` is not valid for this repo because `package.json` lives under `web/frontend`.

## Confirmed Bugs / Non-Working Areas

### BUG-FLYER-QA-001 - Guest order consume is not idempotent for the same project

Workbook scenario: `FS-A5-013`

Expected: replaying `consume_guest_order` for the same `project_id` is idempotent.

Observed: first consume succeeds; second consume returns `reserved_guest_order_not_found`.

Evidence:

```text
first=True/used/
second=False//reserved_guest_order_not_found
```

Root cause: `consume_guest_order` can only find orders with `status == "reserved"` via `_find_reserved_guest_order`; after the first consume it clears `reserved_project_id` and moves the order to `used` or `paid`, so the idempotency branch at [guest_order.py](/C:/projects/sme-agents/src/agents/flyer/guest_order.py:188) is unreachable on replay. The strict reserved lookup is at [guest_order.py](/C:/projects/sme-agents/src/agents/flyer/guest_order.py:245).

Impact: payment/order delivery retries can report failure even when the first delivery consume succeeded.

### BUG-FLYER-QA-002 - Admin customer list has no max-300 cap or pagination

Workbook scenario: `FS-A6-003`

Expected: `/flyer/customers?segment=...` returns only filtered customers; pagination works; max 300 results.

Observed: direct endpoint call with 305 customers returned 305 rows.

Evidence:

```text
returned=305
```

Root cause: `/flyer/customers` appends all matching rows and returns `{"customers": rows}` without slicing or pagination metadata at [flyer.py](/C:/projects/sme-agents/web/backend/app/routers/flyer.py:357).

Impact: operator dashboard can become slow/noisy and violates the workbook contract; unlike `/projects` and `/guest-orders`, this list is unbounded.

### BUG-FLYER-QA-003 - cf-router-classified Flyer inbounds do not emit `dispatcher_routed`

Workbook scenario: `FS-A7-015`

Expected: audit log `dispatcher_routed` entry written for every classified inbound.

Observed: Flyer cf-router skip path emits `cf_router_intercepted`, not `dispatcher_routed`.

Evidence:

```text
result={'action': 'skip', 'reason': 'cf-router flyer starter brief sent'}
audit_types=['cf_router_intercepted']
```

Root cause: cf-router audit helper explicitly writes `CfRouterIntercepted(type="cf_router_intercepted")` at [actions.py](/C:/projects/sme-agents/src/plugins/cf-router/actions.py:487). Existing dispatcher monitoring scans only `dispatcher_routed` rows at [actions.py](/C:/projects/sme-agents/src/plugins/cf-router/actions.py:2156).

Impact: Flyer messages classified and handled before LLM dispatch are visible as cf-router audit rows, but any report that treats `dispatcher_routed` as universal will mark them missing or lose route-level attribution.

### BUG-FLYER-QA-004 - Generated flyer assets expose internal `Hermes` branding

Workbook scenarios: `FS-A4-010`, `FS-A4-011`

Expected: customer preview/final assets should be Flyer Studio branded and customer-facing.

Observed: renderer contains customer-visible footer `Send APPROVE to finalize - Hermes Flyer Studio`.

Evidence:

- Footer in preview renderer: [render.py](/C:/projects/sme-agents/src/agents/flyer/render.py:1548)
- Footer in final asset script template: [render.py](/C:/projects/sme-agents/src/agents/flyer/render.py:1616)

Impact: customer-facing flyers leak the internal platform name and conflict with the project rule that Hermes stays internal.

## QA Infrastructure Finding

### BUG-FLYER-QA-005 - Local Windows focused tests fail without a directory-fsync shim

Expected: local focused Flyer suite should run in the declared Windows workspace.

Observed: unshimmed focused run failed 37 tests before reaching product behavior with:

```text
PermissionError: [Errno 13] Permission denied: ... safe_io.atomic_write_text -> os.open(path.parent, os.O_RDONLY)
```

Root cause: `safe_io.atomic_write_text` fsyncs the parent directory using POSIX semantics at [safe_io.py](/C:/projects/sme-agents/src/platform/safe_io.py:232), which fails on Windows. With a test-only shim for directory fsync, the same focused suite passed: `186 passed`.

Impact: Windows-based QA sessions get false red results unless the tester knows to use Linux/VPS or a shim.

## Verification Commands

```text
$env:PYTHONPATH=.tmp_pytest_site; python -m pytest tests/test_flyer_onboarding.py tests/test_flyer_guest_order.py tests/test_flyer_create_project.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_starter_briefs.py tests/test_cf_router_flyer_routing.py tests/test_flyer_scripts_static.py tests/test_flyer_delivery_retry.py web/backend/tests/test_flyer_admin.py -q
Result: 186 passed, 9 warnings

npm run build
cwd: web/frontend
Result: passed
```
