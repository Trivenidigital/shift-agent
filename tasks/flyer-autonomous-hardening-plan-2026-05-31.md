**Drift-check tag:** extends-Hermes

# Flyer Autonomous Hardening

## New primitives introduced

- No new substrate. This branch tightens Flyer-specific deterministic contracts around fact extraction, visual QA, final approval parsing, and delivery-report observability.

## Drift-rule self-checks

| Check | Evidence | Decision |
|---|---|---|
| Read deployed visual QA path | Read `src/agents/flyer/visual_qa.py` before adding price/pair blockers. | Extend existing locked-fact OCR validation; no new QA substrate. |
| Read deployed fact extraction path | Read `src/agents/flyer/facts.py` before changing compact item parsing. | Tighten existing item-price parser; no new intake compiler/classifier. |
| Read deployed routing helper | Read `src/plugins/cf-router/actions.py` before widening approval aliases. | Keep aliases inside existing deterministic Flyer helper and status-gate preview. |
| Read deployed delivery report | Read `src/agents/flyer/scripts/flyer-delivery-report` before manifest filtering. | Fix existing read model; no new watchdog/table. |

## Hermes-first analysis

Hermes already owns WhatsApp ingress, sender identity, media handling, skill dispatch, LLM gateway, audit conventions, and runtime orchestration. The gaps found by reviewers are not missing Hermes substrate; they are Flyer business-contract checks after Hermes/Flyer has already routed the request.

Step-level ownership:

| Step | Owner | Decision |
|---|---|---|
| Receive WhatsApp/customer text and resolve sender | [Hermes] | Reuse existing cf-router/Hermes ingress and identity. |
| Extract flyer facts from customer text | [Hermes + Flyer existing] | Reuse current Flyer extraction/locked_facts; only tighten a compact-parser edge. |
| Validate rendered customer-visible facts | [net-new] | Add Flyer-specific deterministic price and item-price checks; no LLM/Hermes substrate needed. |
| Interpret final approval replies | [net-new] | Add bounded Flyer approval aliases inside existing cf-router helpers; no new routing substrate. |
| Report final package delivery health | [net-new] | Fix existing Flyer report read-model to respect the final manifest and surface missing IDs. |
| Audit/deploy/runtime orchestration | [Hermes] | No changes; existing audit/deploy paths remain authoritative. |

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp routing / identity | Existing Hermes/cf-router ingress | Reuse existing hooks and sender parsing. |
| Semantic interpretation | Existing Flyer semantic brief + Hermes provider | Do not add a new classifier in this slice. |
| Exact customer-visible facts | Flyer locked facts + visual QA | Tighten deterministic validation in Flyer code. |
| Delivery observability | Existing Flyer delivery report + audit | Fix read-model truthfulness, no new pipeline. |

Hermes skill-hub check: https://hermes-agent.nousresearch.com/docs/skills currently lists no built-in, optional, or community skills applicable to price-pair validation, approval aliases, or final-manifest reporting.

Awesome Hermes Agent ecosystem check: searched the public awesome-Hermes listings for reusable skills; results are general workflow/integration catalogs, not Flyer-specific deterministic QA/reporting primitives. Verdict: build the small Flyer checks in-tree.

## Reviewer findings selected for this slice

- P0: Visual QA can pass swapped/wrong item prices because item names and prices are checked independently.
- P0: Compact menu shorthand such as `Idli-$1each Dosa-$2each Upma-5plate` can poison locked item facts.
- P1: Final approval aliases such as `OK`, `approved`, and `looks good` can fall into revision handling.
- P2: Delivery report counts intentionally skipped optional derivatives as pending when `final_asset_ids` already defines the deliverable package.

## Plan

- [x] Add RED tests for exact item-price pairing and price boundary matching.
- [x] Add RED tests for compact menu shorthand extraction.
- [x] Add RED tests for normal final approval aliases.
- [x] Add RED test for delivery report respecting `final_asset_ids`.
- [x] Implement minimal deterministic fixes.
- [x] Resolve reviewer blockers: currency-required price facts, table/price-leading item prices, ambiguous `pc/piece` quantities, missing manifest IDs, and approval-preview alignment.
- [x] Resolve second-pass reviewer blocker: ambiguous `pcs` quantity shorthand.
- [x] Multi-vector review before full-suite verification.
- [x] Run focused and full verification.
- [ ] PR, merge, deploy.
