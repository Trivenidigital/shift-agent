**Drift-check tag:** extends-Hermes

# Hermes-first checklist

1. Receive WhatsApp media + text for source edit: [Hermes]
2. Run Flyer reference-scope check script on extracted OCR fields: [net-new]
3. Decide allow/block/clarify for exact source edit continuation: [net-new]
4. Persist scope-pending state + route follow-up response: [Hermes + existing Flyer plumbing]
5. Audit decision rows and customer reply dispatch: [Hermes]

Net-new scope for this batch: only step 2-3 decision heuristics and regression tests.
No new infrastructure, no provider/payment changes, no runtime state model changes.

## Batch issues (target 5)

1. Prevent false `block` when visible organization names are generic flyer headings (e.g., "Weekend Specials").
2. Allow account continuation when customer request text clearly names the account business, even if extracted names are noisy.
3. Improve name normalization for connector words like `and` / `&` in visible names.
4. Keep strict unrelated-business `block` behavior when a distinct real organization name is visible.
5. Add regression tests proving these paths to stop ownership/authorization clarification loops on legitimate exact edits.
