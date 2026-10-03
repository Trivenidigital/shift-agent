# Flyer managed-render quality repair

**Drift-check tag:** extends-Hermes

Authorized by the overnight launch request and delegated implementation, 2026-10-02.
**New primitives introduced:** none.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Generation and image understanding | Existing deployed Flyer generation skill and model gateway | Reuse managed generation, prompt assembly and OCR/vision QA |
| Fact validation and recovery | Existing visual_qa.py and generate-flyer-concepts | Extend existing checks and retain recovery |

Prior drift investigation found the full capability in tree. External Hermes/awesome-hermes-agent expansion is unnecessary: this repairs its existing prompt and QA contract, with no new subsystem.

## Governance and Capability Reuse Map — flyer-studio

- Universal directive v1.2.0 blob 31d7706d42b5a9477ce236f4dcb3841223b74a6f.
- Registry v1.0.0 blob 207ad4183e4b0fd37b74eeec29e7dc215546c4cd.
- Flyer directive v1.0.0 blob fe45ce8094eb666057e8393be1e989ec64f7c324.
- Requested outcome: prevent repeated declared headlines passing QA and remove competing layout instructions for a single food item.
- Affected projects: flyer-studio (runtime/tests), repo-meta (this plan). Applicable directives: universal, Flyer Studio and repo-meta; shared directive read for context, no shared code changes.
- Existing platform/model capabilities reused: deployed image model and OCR/vision model.
- Existing deterministic kernels reused: render._resolve_style_directives, _poster_layout_requirements, visual_qa.run_visual_qa and severity classification.
- Existing stores/workflows reused: locked facts, typeset artifact marker, managed generation/recovery.
- Thin adapters: none. Custom runtime code unavoidable: narrow prompt condition and OCR duplicate-line guard.
- New subsystem / architecture exception: none.
- Evidence existing capabilities insufficient: isolated Cedar render OCR contains two exact Dosa Night lines yet QA passed; prompt mixes single-item exception with menu-list scaffolding.
- Shared-platform impact / other agents affected: none.
- Vertical E2E proof: previous isolated managed generation reproduced defect; this change gets offline prompt/QA regressions. The parent will execute the already-authorized isolated paid rerender after review.

## Capability Reuse Map — repo-meta

- Requested outcome: record scope, reuse and verification for the existing Flyer repair.
- Affected projects / applicable directives: repo-meta v1.1.0 blob eecd75a7bf500579b85a67ba7f748bf6bb39fc20, universal directive above.
- Existing platform/model capabilities, deterministic kernels and stores/workflows: reference the Flyer map above; documentation has no runtime.
- Thin adapters / custom runtime / new subsystem / architecture exception: none.
- Evidence existing capabilities were insufficient: existing plan needed to record this newly observed render defect and measured repair.
- Shared-platform impact / other agents affected: none; Flyer evidence documented without changing any other agent.
- Vertical E2E proof: documentation accompanies executable tests and recorded results below.

## Scope and verification

- [x] Add failing regression for repeated declared headline with captured OCR; test substring, shared-role and legacy-marker exclusions.
- [x] Add failing single-item prompt regression and retain multi-item prompt behavior.
- [x] Extend existing typeset copy count contract and single-item layout branch.
- [x] Extend existing OCR QA, scoped to typeset artifacts; do not add aesthetic classifier.
- [x] Run focused tests, diff check, export incremental patch for independent review.

Empty menu geometry is a model-observed n=1 defect. Prompt prohibits empty placeholders; adding an automatic visual blocker without calibrated evidence is outside this narrow patch.

## Verification and review

- Red: two intended failures and five passes before implementation; duplicate headline incorrectly passed and count contract was missing.
- Green: 267 tests passed in Linux with network disabled (readiness, typeset assembly, QA hardening, visual QA, uniform price, style registers, repair instructions and autorepair).
- Captured Cedar PNG recheck: original SHA matched, original OCR reused with provider disabled, existing typeset sidecar read. Previously passed; now duplicate headline blocker, severity block. Existing autorepair reports manual_required and premium repair instruction is empty; CLI exact-text recovery gate regression also refuses it.
- No provider calls, installed source changes or commits in this patch. Repeated wrapped headings remain outside conservative exact-line matching. Other declared fact roles sharing identical text are exempt to avoid false positives.
