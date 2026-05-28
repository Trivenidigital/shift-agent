**Drift-check tag:** extends-Hermes

# Hermes-first checklist
1. Inbound WhatsApp ingest + sender identity resolution -> [Hermes]
2. Existing active-project lookup + route interception framework -> [Hermes]
3. New-vs-active Flyer intent decision for explicit flyer asks -> [net-new]
4. Campaign CTA/account-ready routing observability reason contract -> [net-new]
5. Regression assertions for current customer-safe behavior -> [net-new]

Net-new scope in this batch: only steps 3-5.

## Batch scope (5-6 related issues)
1. Explicit new Flyer requests like `Need flyer for ...` are over-classified as vague and attach to old active intake rows.
2. Active-project revision capture tests still expect pass-through to F7 for revision text, conflicting with current Flyer-first behavior.
3. New-project creation test still expects deprecated `send_flyer_intake_ack` call when missing-info flow now uses `send_flyer_text` clarification.
4. Campaign CTA route reason drift (`ready_prompt` naming) broke deterministic observability assertions.
5. Campaign CTA route reason drift (`account_prompt` naming) broke deterministic observability assertions.
6. Sender-block CTA variant now uses same recovery path but test still expects old reason label.

## Implementation notes
- Keep Hermes substrate untouched.
- Minimal code change: narrow `is_vague_flyer_start` so explicit `... flyer for ...` asks are not treated as vague.
- Preserve current customer-facing CTA copy and revision behavior; align deterministic tests/reason contracts to deployed behavior.
