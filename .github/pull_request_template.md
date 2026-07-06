<!-- Flyer-Studio / platform PR. Keep it small and PR-ready. Delete N/A rows. -->

## Summary
<!-- What changed + why. Link the issue. -->

## Customer impact
<!-- What the store owner sees/gets. "None (dormant/shadow/internal)" is a valid answer. -->

## Release mode
<!-- One of: dormant / shadow / internal (allowlist +17329837841) / canary / production. See docs/runbooks/release.md -->

## Tests run
<!-- Commands + results. NOTE: send-path-ci runs `tests/test_*.py ! -name 'test_flyer*'` — it does NOT
     run flyer tests. Paste local flyer-test output for any flyer change. -->

## Visual evidence
<!-- Rendered flyer preview (before/after for edits). REQUIRED for any customer-visible render change. -->

## Locked facts / OCR / QR checks (where applicable)
- [ ] No fabricated price / offer / business name / date / location.
- [ ] Every required locked fact visible + correct (OCR read-back).
- [ ] Customer-supplied QR preserved (never regenerated) + decodes to the supplied target on the correct channel.
- [ ] Deterministic fallback intact; locked-fact enforcement not weakened.
- [ ] N/A (no render / fact / QR surface touched)

## Rollback
<!-- Flag off / kill-switch / revert PR / restore config. See docs/runbooks/rollback.md -->

## Risk
<!-- Deploy risk + blast radius. Confirm: no Hermes version change (pinned 0.14), no WhatsApp migration,
     no community-skill install, no production-secret change, no production deploy-behavior change, NOT self-merged. -->
