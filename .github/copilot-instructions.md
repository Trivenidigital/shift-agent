# Copilot instructions — pointer

The authoritative development rules for this repository are:

1. `AGENTS.md` (repository root)
2. `docs/governance/engineering-directive.md` — universal rules (Level 1)
3. `docs/governance/project-registry.yaml` — authoritative path-to-project map

Apply the reuse order from the universal directive before generating or
reviewing any code:

1. inspect existing platform/model capabilities;
2. inspect existing deterministic kernels;
3. prefer a thin adapter connecting the two;
4. propose a new subsystem only with evidence that reuse cannot satisfy the
   requirement — and only under an approved architecture exception.

Before suggesting an implementation:

- classify the affected paths through the registry
  (longest-literal-prefix-wins), and load **only** the applicable directives
  from `docs/governance/projects/`;
- load `docs/governance/shared-platform-directive.md` when any affected project
  is marked `shared_platform: true`;
- include a **Capability Reuse Map** (universal directive §9) in the change
  description;
- never let probabilistic output own money, authorization, tenant identity,
  irreversible state transitions, approval enforcement, send eligibility,
  signing, persistence, audit or rollback;
- never add a parallel store, router, workflow engine, scheduler, state
  machine, approval mechanism, importer, notification system or orchestration
  framework without an `approved` entry in
  `docs/governance/architecture-exceptions.yaml`.

Do not restate policy here. This file points at the canonical directives.
