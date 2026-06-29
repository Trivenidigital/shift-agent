---
name: Agent task
about: A small, PR-ready Flyer-Studio (or platform) task for an agent
title: "[agent-task] "
labels: ["agent-task"]
---

## Customer-visible goal
<!-- What the store owner can do or see after this ships. One or two sentences. -->

## Scope
<!-- The specific, small slice. Files / areas expected to change. Keep it PR-sized. -->

## Non-goals
<!-- Explicitly out of scope — prevents creep. -->
- No large Creative Director Loop implementation (unless a slice is separately approved).
- No Hermes version change (pinned 0.14), no WhatsApp migration, no community/untrusted skill install.
- No production deploy-behavior change; no production-secret change.

## Acceptance checks
<!-- Concrete + verifiable. -->
- [ ] Required locked facts preserved (price / offer / business name / date / location); none fabricated.
- [ ] Customer-supplied QR preserved (never regenerated) + decodes to the supplied target on the correct channel.
- [ ] Deterministic fallback intact; locked-fact enforcement not weakened.
- [ ] Tests added/updated + passing (paste output).
- [ ] Visual evidence attached (rendered flyer; before/after for edits).

## Release mode
<!-- Pick exactly one. See docs/runbooks/release.md -->
- [ ] dormant
- [ ] shadow
- [ ] internal (allowlist `+17329837841`)
- [ ] canary
- [ ] production

## Evidence required in PR
- [ ] Test results
- [ ] Rendered flyer / visual evidence
- [ ] Locked-fact / OCR / QR verification
- [ ] Rollback note (flag off / revert / restore config)
