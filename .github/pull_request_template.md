<!-- Keep it small and PR-ready. Delete N/A rows.
     Governance: AGENTS.md → docs/governance/engineering-directive.md →
     docs/governance/project-registry.yaml. Load only the applicable
     project directives. -->

## Summary
<!-- What changed + why. Link the issue. -->

## Capability Reuse Map
<!-- One section per affected project. For multi-project changes, repeat the
     whole block under a `### <project-id>` heading. A verbal "Hermes-first"
     is insufficient — the implementation shape must demonstrate reuse. -->

- Requested outcome:
- Affected projects:
- Applicable directives:
- Existing platform/model capabilities reused:
- Existing deterministic kernels reused:
- Existing stores/workflows reused:
- Thin adapters:
- New subsystem:
- Evidence existing capabilities were insufficient:
- Architecture exception:
- Shared-platform impact:
- Vertical E2E proof:

## Architecture drift check

- [ ] Changed paths are classified in the project registry
- [ ] Applicable project directives were reviewed
- [ ] Existing capabilities were reused first
- [ ] No parallel store/workflow/router/importer/approval system was added
- [ ] Probabilistic logic does not control money, authorization or irreversible state
- [ ] Shared-platform changes list all affected agents
- [ ] This is the smallest viable vertical integration
- [ ] Any exception is approved and path-scoped

## Customer impact
<!-- What the store owner sees/gets. "None (dormant/shadow/internal)" is valid. -->

## Release mode
<!-- dormant / shadow / internal (allowlist +17329837841) / canary / production.
     See docs/runbooks/release.md -->

## Tests run
<!-- Commands + results. NOTE: send-path-ci runs `tests/test_*.py ! -name 'test_flyer*'` —
     it does NOT run flyer tests. Paste local flyer-test output for any flyer change. -->

## Rollback
<!-- Flag off / kill-switch / revert PR / restore config. See docs/runbooks/rollback.md -->

## Risk
<!-- Deploy risk + blast radius. Confirm: no Hermes version change (pinned 0.14),
     no WhatsApp migration, no community-skill install, no production-secret change,
     no production deploy-behavior change, NOT self-merged. -->

<!-- ─── Conditional, product-specific. Keep only the sections your changed
     paths actually touch; the governance checker reports which projects are
     affected. Do not require Catering fields on a Flyer-only PR. ───

## Flyer Studio — locked facts / OCR / QR
- [ ] No fabricated price / offer / business name / date / location.
- [ ] Every required locked fact visible + correct (OCR read-back).
- [ ] Customer-supplied QR preserved (never regenerated) + decodes to the supplied target.
- [ ] Deterministic fallback intact; locked-fact enforcement not weakened.
- [ ] Visual evidence attached (before/after) for any customer-visible render change.

## Catering Studio — money / state
- [ ] Money stays integer cents through the pricing kernel.
- [ ] Quote versions immutable; no parallel lead/proposal store.
- [ ] Menu authority unchanged (owner applies with the confirmation code).

## Commerce — money movement
- [ ] Amounts, order-state transitions and payment links stay deterministic.
- [ ] Live-mode and webhook gates unchanged or strengthened.

## Shared platform — per-agent impact
- [ ] Affected-agent analysis, compatibility proof, default behavior,
      activation posture, rollback, and tests for every affected product.
-->
