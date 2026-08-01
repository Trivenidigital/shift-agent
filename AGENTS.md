# Project Instructions — SMB-Agents monorepo

**This file is the mandatory starting instruction for every session — human or
agent — in this repository.**

Canonical policy lives in `docs/governance/`. This file routes you to it; it
does not restate it. If this file and a directive ever disagree, the directive
wins.

---

## Always read, before planning, coding, reviewing or dispatching subagents

1. `docs/governance/engineering-directive.md` — the universal rules (Level 1)
2. `docs/governance/project-registry.yaml` — authoritative path-to-project map

## Then, for the task at hand

1. **Determine which project paths the task affects**, using the registry.
   Classification is longest-literal-prefix-wins. Never infer ownership from a
   filename.
2. **Read the directive for every affected project**
   (`docs/governance/projects/<id>.md`).
3. **Read `docs/governance/shared-platform-directive.md`** when the task touches
   any project marked `shared_platform: true`.
4. **Produce one Capability Reuse Map per affected project** (schema in the
   universal directive §9).
5. **For cross-project work, produce an impact section for every affected
   agent.**
6. **Stop before implementation when:**
   - project classification is ambiguous;
   - a new subsystem is proposed without an approved architecture exception;
   - shared-code impact has not been assessed.

**Do not read all project directives.** Load only the universal directive, the
registry, and the directives applicable to the paths you are touching.

## Session bootstrap — demonstrate compliance, don't acknowledge it

Open the session by reporting, discovered from the repository:

```
Universal directive: docs/governance/engineering-directive.md v<version> blob <sha>
Project registry:    docs/governance/project-registry.yaml v<version> blob <sha>
Affected projects:   <ids>
Applicable directives: <path> v<version> blob <sha> (per project)
Shared-platform impact: <none | description>
New subsystem proposed: <no | what>
Architecture exception: <none | HERMES-EX-###>

Capability Reuse Map — <project id>
- Requested outcome:
- Existing platform/model capability reused:
- Existing deterministic kernel reused:
- Existing store/workflow reused:
- Thin adapter introduced:
- Custom runtime code genuinely unavoidable:
- Evidence existing capabilities were insufficient:
- Vertical E2E proof:
```

Blob hashes: `git rev-parse HEAD:<path>`.

A verbal statement such as "Hermes-first" is insufficient. The implementation
shape must demonstrate reuse.

## Pull requests

Every PR carries the Capability Reuse Map and the architecture drift checklist
from `.github/pull_request_template.md`. A new store, state machine, workflow
engine, router, scheduler, importer, approval mechanism or custom
interpretation layer requires an approved entry in
`docs/governance/architecture-exceptions.yaml`.

`tools/check-architecture-governance.py` enforces the mechanical parts;
`.github/workflows/architecture-governance.yml` runs it on every PR.

---

## Governance map

| Level | File |
|---|---|
| 1 — universal | `docs/governance/engineering-directive.md` |
| 2 — shared platform | `docs/governance/shared-platform-directive.md` |
| 3 — per project | `docs/governance/projects/<id>.md` |
| 4 — exceptions | `docs/governance/architecture-exceptions.yaml` |
| path map | `docs/governance/project-registry.yaml` |
| checker | `tools/check-architecture-governance.py` |

Nested `AGENTS.md` pointers exist at `src/agents/catering/`,
`src/agents/flyer/`, `src/agents/shift/`, `src/agents/expense_bookkeeper/`,
`src/platform/commerce/` and `web/`. They point; they never fork the policy.

---

## Project context (orientation, not policy)

- **Project:** SMB-Agents — autonomous AI agents for ethnic SMBs (restaurants,
  groceries, food courts, catering)
- **Architecture:** per-customer Hetzner VPS (~$7/mo) + a central operator VPS
  for fleet management
- **Stack:** Hermes Agent (skills + gateway + delegation) + per-customer
  JSON/SQLite data layer + WhatsApp/Telegram messaging
- **Reference customer:** Triveni Supermarket — 9 locations across
  TX/MD/NC/SC/OH/VA
- **Portfolio:** `docs/portfolio.md`
- **Inventory:** 18 agents under `src/agents/`, plus the shared platform,
  commerce, and the operator cockpit. Gecko Alpha is **not** in this
  repository.

### Key paths

- Agent code: `src/agents/<agent>/skills/<skill>/SKILL.md`
- Platform schemas: `src/platform/schemas.py`
- Tests: `tests/`, `web/backend/tests/`
- Cockpit: `web/backend/`, `web/frontend/`; portal source `web/portal/index.html`

### Workflow reminders

- **Plan-first:** non-trivial work → write `tasks/<feature>-plan.md` and get
  approval before code. The plan carries the reuse pass (universal directive
  §1), not just a design.
- **Commits:** never auto-commit; wait for an explicit request.
- **Tarball deploy:** no git checkout on the VPS — build artifact, `scp`,
  restart.
- **SSH from Windows:** two-step pattern (`ssh ... > file 2>&1`, then read the
  file); never inline-capture SSH stdout.
- **Production pilot readiness:** for Shift + Catering + Daily Brief, run
  `pilot-readiness-check --text` before calling a customer VPS
  production-ready. Treat it as blocking for onboarding decisions even though
  deploy smoke reports it non-blocking.
- **Self-learning boundary:** production agents may update state/memory only
  (menus, LIDs, customer notes, lead history, roster facts). Code, SKILL,
  prompt, model and deploy-config evolution go through tests, review, PR and
  tarball deploy.
- **Catering menu authority:** owner or verified employee may upload menu
  source media; only the owner may apply the extracted menu with the
  confirmation code.
