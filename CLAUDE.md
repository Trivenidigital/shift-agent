# CLAUDE.md — pointer

Read and obey, in this order, before doing any work in this repository:

1. `AGENTS.md` (repository root) — the mandatory session entry point
2. `docs/governance/engineering-directive.md` — universal rules (Level 1)
3. `docs/governance/project-registry.yaml` — authoritative path-to-project map

These are mandatory project-level instructions. They override any preference
for custom frameworks or generalized infrastructure.

**Then:**

- Classify the paths your task affects through the registry
  (longest-literal-prefix-wins). Never infer ownership from a filename.
- Load **only** the applicable project directives from
  `docs/governance/projects/`, plus
  `docs/governance/shared-platform-directive.md` if any affected project is
  marked `shared_platform: true`. Do not read all project directives.
- Produce a **Capability Reuse Map** per affected project before implementing
  (schema: universal directive §9; bootstrap format: `AGENTS.md`).
- Do **not** introduce a new store, router, workflow engine, scheduler, state
  machine, approval mechanism, importer, notification system or orchestration
  framework without an `approved` entry in
  `docs/governance/architecture-exceptions.yaml`.
- Stop before implementation if classification is ambiguous, a new subsystem is
  proposed without an exception, or shared-code impact is unassessed.

This file intentionally contains no policy of its own. Canonical policy lives
in `docs/governance/`; duplicating it here is how the previous
`AGENTS.md`/`CLAUDE.md` pair silently forked.
