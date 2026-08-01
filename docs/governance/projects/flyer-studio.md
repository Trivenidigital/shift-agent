# Flyer Studio — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: flyer-studio
    Supplements: docs/governance/engineering-directive.md
                 docs/governance/shared-platform-directive.md

Governs only the paths registered to `flyer-studio` in
`docs/governance/project-registry.yaml`. It does not apply to Catering Studio,
Shift Agent, the cockpit, or any other product.

---

## Purpose

WhatsApp-first marketing creative for store owners: an owner sends an offer,
menu, photo, logo or QR code and receives a high-quality flyer plus a
share-ready package, **with locked facts preserved and never fabricated**.

Current commercial scope is `docs/product/live-scope.md`; read it before
proposing product work.

## Model capability — reuse, do not reimplement

Models own, for this product:

- visual generation;
- layout exploration;
- copy interpretation;
- creative alternatives;
- image understanding;
- style recommendations.

Deployed entry points: `src/agents/flyer/skills/flyer_intake/`,
`flyer_generation/`, `flyer_dispatcher/`, plus
`src/agents/flyer/extraction_v2.py`, `semantic_brief.py`,
`flyer_creative_resolver.py`, `flyer_art_director_oracle.py`,
`campaign_scene_prompts.py`, `premium_poster_v1_director.py`.

Do not write a bespoke image-understanding or copy-interpretation
implementation that duplicates these.

## Deterministic systems — reuse, do not fork

| Concern | Deployed owner |
|---|---|
| Exact dimensions / aspect ratio | `src/agents/flyer/render.py`, `bare_render.py` |
| Original QR preservation | `src/agents/flyer/facts.py`, `visible_contract.py`, brand-asset state scripts |
| Fabrication detection | `src/agents/flyer/creative_firewall.py` |
| Price + promotion validation | `src/agents/flyer/facts.py`, `flyer_brief_validator.py`, `customer_copy_policy.py` |
| Brand constraints | `src/agents/flyer/derive-flyer-brand-style`, `style_registers.py`, `premium_overlay.py` |
| File-format checks | `src/agents/flyer/render.py`, `finalize-flyer-assets` |
| OCR validation / visual QA | `src/agents/flyer/visual_qa.py` |
| Retry / fallback policy | `src/agents/flyer/repair.py`, `recovery.py`, `flyer-recovery-watchdog` |
| Quarantine + manual queue | `src/agents/flyer/quarantine.py`, `manual_queue.py` |
| Approval state | `src/agents/flyer/workflow.py`, `src/plugins/cf-router/` (shared pre-gateway interception) |
| Export requirements | `finalize-flyer-assets`, `send-flyer-package` |
| Identity / accounts | `src/platform/flyer_identity.py`, `src/agents/flyer/account.py` |
| Audit | `log-decision-direct` chokepoint |

## Decision boundary

**May be probabilistic:** what the flyer looks like, which layout to try, how
copy is phrased, which creative alternative to offer, style and register
choice, interpretation of a vague owner request.

**Must remain deterministic:** every locked fact (price, offer, business name,
date, location, phone), whether a customer-supplied QR is preserved and where
it lands, whether output passes visual QA and the fabrication firewall, whether
a retry is permitted and how many, whether a job falls back to the
deterministic overlay, whether an asset may be sent, approval state, and audit.

A model may propose a locked-fact value; only the deterministic fact and QA
layers may accept it. Weakening locked-fact enforcement to make generation
pass is never an acceptable fix.

## Reuse order for this project

1. Existing model capability (generation / interpretation).
2. Existing Flyer Studio deterministic kernel (facts, firewall, visual QA,
   repair/recovery, workflow).
3. Thin adapter connecting them.
4. New subsystem — only through an approved architecture exception.

The existing generation → validation → retry → fallback → approval pipeline
must be reused before any new infrastructure is proposed.

## Presumed NO-GO

- a second flyer-generation pipeline;
- a parallel approval store;
- a new QR regeneration path where original-QR preservation is required;
- an alternate fabrication detector;
- a separate retry or orchestration framework;
- a custom image-understanding implementation duplicating the model;
- bypassing the existing OCR / visual-QA and approval gates.

## Required vertical E2E proof

A Flyer change is complete when an owner's WhatsApp request produces a rendered
artifact that passes visual QA with locked facts intact, reaches owner
approval, and is delivered — demonstrated on a real render, not only in unit
tests. Customer-visible render changes require visual evidence
(before/after).

## Escalation boundaries

- Locked-fact, QR-preservation and fabrication-firewall findings are
  BLOCKER-class.
- Every customer-facing change ships flag-gated and allowlist-scoped first
  (`+17329837841`), kill-switchable, per `docs/runbooks/release.md` and
  `docs/runbooks/rollback.md`.
- No Hermes version change (pinned 0.14), no WhatsApp migration, no
  community-skill install as part of a Flyer change.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Flyer Studio directive. |
