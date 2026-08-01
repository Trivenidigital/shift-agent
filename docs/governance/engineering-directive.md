# Universal Engineering Directive

    Version: 1.0.0
    Status:  Mandatory
    Scope:   Every file, product and agent in this repository (Level 1)
    Applies to: all agents, developers, reviewers, subagents and implementation
                sessions — human or automated

This is the authoritative repository-level rule for architecture and
implementation. It applies everywhere. It is supplemented — never replaced — by:

- `docs/governance/shared-platform-directive.md` (Level 2, shared infrastructure)
- `docs/governance/projects/<project>.md` (Level 3, per product/agent)
- `docs/governance/architecture-exceptions.yaml` (Level 4, approved departures)

Path-to-project classification is decided by
`docs/governance/project-registry.yaml`, which is authoritative. Never infer
project ownership from a filename.

---

## 1. Reuse order (mandatory, in this order)

Before writing any new runtime code:

1. **Inspect existing platform/model capabilities.** Enumerate what Hermes,
   the LLM gateway, and the deployed skills already do for each step of the
   task.
2. **Inspect existing deterministic kernels.** Enumerate the deployed modules,
   scripts and stores that already own the safety-critical parts.
3. **Prefer a thin adapter** that connects (1) to (2).
4. **Propose a new subsystem only with evidence** that (1) and (2) cannot
   satisfy the requirement — and only through an approved architecture
   exception.

Enumeration means reading the deployed code, not recalling it. See §6.

## 2. Do not duplicate

A second implementation of any of the following is presumed **NO-GO** across
the entire repository:

- stores;
- workflows;
- routers;
- schedulers;
- state machines;
- approval systems;
- importers;
- notification systems;
- orchestration frameworks.

Extending an existing one is the default. Creating a parallel one requires an
approved exception whose `subsystem_type` names it.

## 3. Do not hand-build what a capability can obtain

Do not create manual structured-data entry — hand-authored JSON, an operator
transcription step, a bespoke form — when an existing AI, ingestion, parsing or
conversation capability can obtain the information safely.

"Safely" is the limit, not an escape hatch: if the data is safety-critical per
§4, the capability may *obtain* it, but a deterministic kernel must *validate
and own* it.

## 4. Probabilistic / deterministic boundary

Probabilistic models (LLM, vision, ranking, generation) **must not own**:

- money;
- authorization;
- tenant identity;
- irreversible state transitions;
- approval enforcement;
- send eligibility;
- signing;
- persistence;
- audit;
- rollback.

Models interpret, extract, clarify, summarize, recommend and phrase. Determinis-
tic code decides, gates, mutates, records and reverses. A model may *propose* a
value in any of the categories above; a deterministic kernel must *accept or
refuse* it.

## 5. What counts as progress

- Every feature must deliver a demonstrable **vertical outcome** — an
  end-to-end path a real user or operator can exercise.
- **More custom code is not progress.**
- The **smallest working integration** is preferred over the general one.
- **Architecture-only output is not completion. Working end-to-end behavior is
  completion.**

A milestone that produced infrastructure but no exercisable vertical slice is
not done, regardless of how much was written.

## 6. Read the deployed code before proposing

Before proposing schema, test, routing or architecture work, read the relevant
deployed code. This is a precondition of §1, not advice.

| Work type | Read first |
|---|---|
| Schema work | `src/platform/schemas.py` |
| Audit-log entries | `LogEntry` discriminated union in `src/platform/schemas.py` |
| Routing / dispatcher work | `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` + one handler SKILL |
| File locking / atomic writes | `src/platform/safe_io.py` |
| New script | the closest existing script in `src/platform/scripts/` or `src/agents/*/scripts/` |
| New SKILL | one existing `SKILL.md`, to mirror frontmatter and structure |
| Test work | 1–2 existing test files in `tests/` |
| Deploy work | `src/agents/shift/scripts/shift-agent-deploy.sh` + `tools/check-shift-agent-patch.sh` |

## 7. Deployed patterns — verify, do not silently replace

These are the conventions actually deployed. Diverging is allowed, but must be
stated explicitly in the proposal and, for a new subsystem, carried by an
exception.

- **Storage:** JSON on disk via `safe_io.atomic_write_json` + `fcntl.flock`.
  SQLite/Postgres is a declared departure, not a default.
- **Audit log:** NDJSON appended through the `log-decision-direct` chokepoint
  (or a per-agent script sharing that chokepoint). New entry variants subclass
  `_BaseEntry` with `type: Literal[...]`.
- **Approval codes:** 5-char `#XXXXX` from the shared alphabet via
  `generate_unique_code`. No parallel generators. Codes share one namespace
  across agents; the dispatcher disambiguates by state-file priority.
- **Schemas:** Pydantic v2 with explicit `model_config`. `extra="forbid"` on
  state schemas, `extra="ignore"` on LLM-output shapes. Status enums use
  `Literal[...]`, not `Enum`.
- **Sender identity:** phone or LID via `identify-sender`; always parse the
  `v=1` block with `validate-sender-block` first. Never route on message
  content, WhatsApp profile name, or the informational `fromMe` flag.
- **Dispatcher routing:** amend the existing `dispatch_shift_agent` matrix in
  priority order and write the `dispatcher_routed` audit entry *before*
  delegating.
- **Per-customer VPS isolation:** every VPS is single-tenant. No cross-VPS
  state sharing.

## 8. Review economics

- **BLOCKER** and **HIGH** findings stop a milestone.
- **MEDIUM** and **LOW** findings normally enter the backlog.
- Maximum **two reviewer/fix cycles**, unless a reproducible BLOCKER or HIGH
  remains.
- A previously closed finding may not be reopened without one of: new evidence,
  a reproducible failure, or a changed reviewed head.

## 9. Required output — the Capability Reuse Map

Every substantive task and every pull request must carry a Capability Reuse Map
with these fields:

```
## Capability Reuse Map

- Requested outcome:
- Affected project(s):
- Applicable directive(s):
- Existing platform/model capability reused:
- Existing deterministic kernel reused:
- Existing store/workflow reused:
- Thin adapter introduced:
- Custom runtime code genuinely unavoidable:
- New subsystem introduced:
- Evidence existing capabilities were insufficient:
- Architecture exception ID or none:
- Shared-platform impact:
- Other agents affected:
- Vertical E2E proof:
```

For a multi-project change, repeat the section per project.

A verbal statement such as "Hermes-first" is **insufficient**. The
implementation shape must demonstrate reuse.

## 10. Session bootstrap

A session must not merely acknowledge this directive. Before acting it must
report, from the repository:

1. this directive's version and committed blob hash;
2. the project registry's version and committed blob hash;
3. the affected projects, resolved through the registry;
4. the applicable project directive versions and hashes;
5. a Capability Reuse Map per affected project;
6. shared-platform impact;
7. proposed new subsystem: yes/no;
8. architecture exception: ID or none.

Stop before implementation when project classification is ambiguous, when a new
subsystem is proposed without an approved exception, or when shared-code impact
has not been assessed.

## 11. Reviewer standing rule

A PR is **NO-GO** when it:

- duplicates an existing platform or model capability;
- creates a parallel store, workflow, importer, router, approval system or
  scheduler without an approved exception;
- implements interpretation or clarification in deterministic code when an
  applicable AI capability exists;
- permits probabilistic logic to control money, authorization, signing,
  irreversible state or send eligibility;
- changes shared infrastructure without identifying affected agents;
- applies one project's architecture rules to an unrelated product;
- adds broad infrastructure instead of delivering the requested vertical
  outcome.

A PR is normally **GO** when:

- the applicable AI/platform capabilities perform interpretation and
  interaction;
- existing deterministic kernels own the safety-critical decisions;
- existing stores and workflows are reused;
- the change is the smallest adapter needed;
- a complete vertical E2E is demonstrated;
- every materially affected project is covered.

**Reviewer-lens requirement.** When dispatching a parallel review cycle, always
include one reviewer whose explicit lens is *"could an existing capability
already do this — is the scope itself needed?"* The usual lenses (security,
drift, schema, truth-guard, deploy) take scope as given and will find BLOCKERs
*inside* a bloated scope without ever questioning the scope.

## 12. Governance is not a runtime dependency

Nothing in `docs/governance/`, `tools/check-architecture-governance.py`, or the
governance CI workflow may be imported, read or required by production runtime
code, deployment artifacts or agent prompts. Governance constrains how code is
written; it never participates in how it runs.

---

## Change control

Bump `Version` on any normative change and record the reason below.

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial universal directive. Consolidates the Hermes-first rule and drift rules previously forked between `AGENTS.md` and `CLAUDE.md` into one canonical source. |
