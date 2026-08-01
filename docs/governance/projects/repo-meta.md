# Repository Docs and Planning — Project Directive

    Version: 1.0.0
    Status:  Mandatory
    Level:   3 (project)
    Project id: repo-meta
    Supplements: docs/governance/engineering-directive.md

Covers cross-product planning and documentation that no single product owns:
`docs/**` (except product-owned docs and `docs/governance/**`), `tasks/**`,
`review-notes/**`, the root `README.md` / `PLAN.md` / `DESIGN.md` /
`backlog.md` / `GO-LIVE-HANDOFF.md`, and repository-level CI/config.

---

## Purpose

Keep planning artifacts honest and classifiable. These paths carry no runtime
behavior, so the governance burden here is deliberately light.

## Rules

1. **A plan is not an implementation.** Architecture-only output does not
   satisfy a milestone (universal directive §5). A plan that lands without the
   vertical slice it describes is not progress.
2. **Plans must carry a reuse pass.** A plan, spec or design doc that proposes
   runtime work must include the per-step reuse enumeration from the universal
   directive §1 — which steps an existing capability covers, which are
   genuinely new — before the effort estimate. An estimate over unreviewed
   steps is not an estimate.
3. **Read the deployed code first** (universal directive §6). Most drift in
   this repository comes from importing an external frame before grounding in
   what is actually deployed.
4. **Point, do not fork.** Documentation must reference the canonical
   directives rather than restating policy. A doc that paraphrases a directive
   becomes a competing version of it — this is precisely what
   `AGENTS.md`/`CLAUDE.md` did before governance was consolidated.
5. **Product-owned docs belong to the product.** Runbooks, edge-case notes and
   scope docs registered to a product carry that product's directive. Check the
   registry rather than assuming `docs/` means repo-meta.

## Drift-check tag on plan / spec / design docs

Every plan, spec or design document under `tasks/` must carry one tag at the
top:

- **`Hermes-native`** — uses existing primitives without modification;
- **`extends-Hermes`** — adds custom infrastructure on top (most platform work);
- **`drifts-from-Hermes`** — explicitly fights deployed conventions, and must
  say operationally what compensating infrastructure exists.

This is a self-disclosure mechanism: it surfaces deviation at proposal time so
reviewers can engage with it explicitly. It does not replace the
read-the-deployed-code rule (universal directive §6).

> **Operational note.** A `PreToolUse` hook may be configured in the author's
> local Claude Code settings (`hermes-first-check.py`) that blocks writes to
> `tasks/` plan/design/spec docs lacking both a `**Drift-check tag:**` line and
> a reuse-checklist heading. It is an author-side convenience, not a repository
> gate — the requirement above stands whether or not the hook is installed.

## Decision boundary

Not applicable — no runtime. A change here must not alter runtime behavior; if
it does, the change is misclassified and belongs to the owning product.

## Presumed NO-GO

- adding a second canonical policy source;
- a doc that instructs an agent to bypass a directive;
- introducing a runtime dependency on any file in these paths.

---

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial repo-meta directive. |
