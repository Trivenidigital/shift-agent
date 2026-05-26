**Drift-check tag:** extends-Hermes

# Hermes-first checklist

1. Receive inbound WhatsApp CTA / flyer text / onboarding confirm -> [Hermes]
2. Resolve sender identity (phone/LID/role) -> [Hermes]
3. Route CTA/account/onboarding/active-project flow in cf-router -> [net-new]
4. Create Flyer project / onboarding / intake via existing scripts -> [Hermes]
5. Emit audit rows and customer replies via existing helpers -> [Hermes]
6. Build self-eval report CLI output -> [Hermes]
7. Stabilize test contract for expanded incident surfaces -> [net-new]

Net-new scope for this batch:
- Restore deterministic CTA routing contract for active/trial vs payment-pending/suspended/new senders.
- Prevent active-project intercept from stealing fresh explicit flyer intents and specific CTA intents.
- Remove duplicate paid-guest-order lookup in one request path.
- Restore expected intake ack behavior for explicit flyer project creation.
- Keep compound CONFIRM trailing-request behavior to a single onboarding acknowledgment.
- Relax brittle self-eval CLI incident-count assertion to verify the intended signal without assuming exact global incident cardinality.

Evidence basis:
- `pytest -q tests -k flyer --maxfail=20` -> 15 failures (14 in `tests/test_cf_router_plugin.py`, 1 in `tests/test_flyer_self_evaluation.py`).
