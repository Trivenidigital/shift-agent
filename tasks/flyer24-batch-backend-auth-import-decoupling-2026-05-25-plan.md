# Flyer24 batch plan: backend auth import decoupling (2026-05-25)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Load Flyer Cockpit router/test modules. **[Hermes]**
2. Authenticate Cockpit endpoints. **[Hermes]**
3. Execute pure Flyer admin helpers/tests without auth runtime deps. **[net-new]**
4. Keep auth-gated endpoint tests deterministic when optional deps are missing. **[net-new]**
5. Preserve all existing runtime auth behavior and fail-closed semantics. **[Hermes]**

## Scope
- Decouple `web/backend/app/routers/flyer.py` from import-time auth dependency.
- Keep endpoint auth enforcement unchanged by resolving auth dependencies lazily.
- Make `web/backend/tests/test_flyer_admin.py` dependency-aware for environments missing `fastapi`/`jose`.
- Verify with py_compile + focused Flyer pytest suites.

## Out of scope
- No payment/checkout/webhook/runtime billing changes.
- No live deploy/runtime state mutation.
