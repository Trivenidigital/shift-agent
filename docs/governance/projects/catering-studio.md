# Catering Studio — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: catering-studio
    Supplements: docs/governance/engineering-directive.md
                 docs/governance/shared-platform-directive.md

Governs only the paths registered to `catering-studio` in
`docs/governance/project-registry.yaml`. It does not apply to Flyer Studio,
Shift Agent, the cockpit, or any other product.

This file supplements the universal directive. It does not repeat it.

---

## Purpose

WhatsApp-first catering: capture an inbound inquiry, qualify it in
conversation, price it against the owner's menu and pricebook, present
proposal options, obtain owner approval, take a deposit, and follow up after
the event.

## Hermes / model capability — reuse, do not reimplement

Hermes owns, for this product:

- natural-language understanding of inbound inquiries;
- vision and document interpretation (menu photos, PDFs, price sheets);
- extraction into the deployed schemas;
- clarification and conversational qualification;
- recommendations and proposal *phrasing*;
- summaries;
- customer-facing language;
- owner-facing language;
- menu interpretation;
- correction conversations;
- proposal presentation;
- explanation of deterministic results.

If a step is on this list, do not write a parser, a template tree, or a
classifier for it. Prompt the existing skill.

Deployed entry points: `src/agents/catering/skills/parse_catering_inquiry/`,
`update_catering_menu/`, `creative_catering_proposals/`,
`apply_catering_menu_decision/`, `handle_catering_menu_finalize/`,
`handle_catering_owner_approval/`, `catering_dispatcher/`, and
`src/agents/catering/scripts/parse-menu-photo`.

## Deterministic kernels — reuse, do not fork

These own the safety-critical half and must not be re-derived in a skill, a
prompt, or a second module:

| Concern | Deployed owner |
|---|---|
| Identity + tenant isolation | shared platform (`identify-sender`, `validate-sender-block`) |
| Authorization / sender role | shared platform + `catering_dispatcher` role gate |
| Canonical menu + item IDs | `src/platform/schemas.py` (`Menu`), `src/agents/catering/scripts/apply-menu-update` |
| Integer-cents money | `src/platform/catering_pricing.py` (`usd_to_cents`, `format_cents`, `QuoteComputation`) |
| Pricebook import + validation | `src/agents/catering/scripts/import-catering-pricebook` |
| Pricing | `src/platform/catering_pricing.py` |
| Lead state | `src/platform/catering_paths.py`, `catering-leads.json`, `create-catering-lead`, `amend-catering-lead` |
| Proposal state | `create-catering-proposal-options`, `select-catering-proposal`, `src/platform/catering_proposal_sweep.py` |
| Immutable quote versions | `src/platform/catering_quote_ledger.py` |
| Approval enforcement | `apply-catering-owner-decision`, shared `#XXXXX` code pools |
| Idempotency | `src/platform/safe_io.py` + FileLock + sentinel state |
| Persistence | `safe_io.atomic_write_json` |
| Send eligibility, STOP, pause, takeover, kill switch | `src/platform/automation_control.py`, `transport_evidence*.py` |
| Follow-up policy | `src/platform/catering_followups.py`, `catering-followup-sweep` |
| Audit | `log-decision-direct` chokepoint |
| Rollback | `catering-state-downgrade`, `docs/runbooks/catering-rollback.md` |

> Note: `src/agents/catering_followup/` is a disabled dispatcher scaffold. The
> live follow-up runtime is the platform module and catering scripts above.
> Do not build follow-up logic in the scaffold.

## Decision boundary

**May be probabilistic:** intent detection, field extraction from free text or
media, which clarifying question to ask next, proposal narrative and tone,
menu-item name interpretation, summarization of a lead's history.

**Must remain deterministic:** who the sender is, whether they are authorized,
which canonical item ID a request maps to once confirmed, every cent, quote
versioning, lead and proposal state transitions, whether an approval code is
valid, whether a message is eligible to send, deposit and payment state,
persistence, and audit.

An extracted price or headcount is *advisory* until a deterministic kernel
accepts it. The owner-confirmed value is the source of truth.

## Reuse order for this project

1. Existing Hermes capability.
2. Existing Catering deterministic kernel.
3. Thin adapter connecting them.
4. New subsystem — only through an approved architecture exception.

## Presumed NO-GO

- a second menu-ingestion pipeline;
- a second pricebook importer;
- a second proposal lifecycle;
- a second approval workflow;
- a second owner-notification mechanism;
- a parallel lead or proposal store;
- custom clarification trees duplicating Hermes;
- hand-authored JSON where Hermes can extract or collect the data;
- a new orchestration framework.

## Permitted architecture — menu to pricebook

The sanctioned shape for turning an owner's menu into priced commerce is:

```
Hermes vision + existing menu ingestion
  → existing correction and approval workflow
    → thin deterministic menu-to-pricebook adapter
      → existing pricebook importer (import-catering-pricebook)
        → existing deterministic pricing kernel (catering_pricing.py)
```

Only the adapter is new code. Anything larger needs an exception.

## Required vertical E2E proof

A Catering change is complete when a real inbound WhatsApp message produces the
intended owner- or customer-visible outcome end to end — inquiry → qualified
lead → priced proposal → owner approval → outbound send — with the audit rows
to show it. Unit tests alone are not the proof.

## Escalation boundaries

- Money-visible changes (pricing, quote ledger, deposits) are BLOCKER-class:
  no merge on an open correctness finding.
- Anything that could send to a customer ships flag-gated and allowlist-scoped
  first, per `docs/runbooks/release.md`.
- Menu authority is fixed: owner or verified employee may upload source media;
  only the owner may apply the extracted menu with the confirmation code. Do
  not widen this in a skill.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial Catering Studio directive. |
