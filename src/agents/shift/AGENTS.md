# Shift Agent (scheduling) — Instructions

Read and obey, in this order:

- `../../../AGENTS.md` (repository root — mandatory session entry point)
- `../../../docs/governance/engineering-directive.md` (universal, Level 1)
- `../../../docs/governance/project-registry.yaml` (authoritative path map)
- `../../../docs/governance/projects/shift-agent.md` (this project's directive)

> **Boundary warning.** Only the non-dispatcher skills, `templates/`,
> `config.yaml.template` and `runbook.md` under this directory belong to the
> Shift Agent product. `skills/dispatch_shift_agent/`, `scripts/` and
> `systemd/` are SHARED PLATFORM — changing them also requires
> `../../../docs/governance/shared-platform-directive.md` and its
> affected-agent obligations.

Do not duplicate the policy text. These files point at the canonical
directives; the directives are authoritative.

Produce a Capability Reuse Map for this project before implementing
(schema: universal directive §9).
