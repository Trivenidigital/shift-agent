# Shared-Platform Directive

    Version: 1.0.0
    Status:  Mandatory
    Scope:   Level 2 — shared Hermes / Shift / gateway / transport / routing /
             state / deployment infrastructure used by more than one product
    Supplements: docs/governance/engineering-directive.md

Applies to the projects marked `shared_platform: true` in
`docs/governance/project-registry.yaml`. At the time of writing those are
`shift-platform` and `commerce-platform`. The registry is authoritative — read
it rather than this list.

This directive adds obligations. It never relaxes the universal directive.

---

## 1. Capability ownership

### Hermes and model capabilities own

- language understanding;
- interpretation;
- extraction;
- clarification;
- vision and document understanding;
- summarization;
- recommendations;
- user-facing presentation;
- owner/operator-facing explanations;
- tool selection, where safely permitted.

### Shared deterministic infrastructure owns

- identity;
- tenant isolation;
- authorization;
- tool permissions;
- routing invariants;
- idempotency;
- outbound policy;
- transport safety;
- audit;
- state mutation;
- persistence;
- scheduling policy;
- kill switches;
- deployment;
- rollback;
- runtime closure.

Deployed anchors for the deterministic half (read before proposing):

| Concern | Module / script |
|---|---|
| Sender identity | `src/platform/scripts/identify-sender`, `src/platform/scripts/validate-sender-block`, `src/platform/sender_context.py` |
| Routing invariants | `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` |
| Pre-gateway interception | `src/plugins/cf-router/` |
| Atomic state + locking | `src/platform/safe_io.py` |
| Audit chokepoint | `src/platform/scripts/log-decision-direct`, `src/platform/audit_helpers.py` |
| Approval-code namespace | `src/platform/approval_code_pools.py` |
| Transport safety / send evidence | `src/platform/transport_evidence*.py` |
| Automation + kill switches | `src/platform/automation_control.py` |
| Schemas / `LogEntry` union | `src/platform/schemas.py` |
| Deploy + pin gate | `src/agents/shift/scripts/shift-agent-deploy.sh`, `tools/check-shift-agent-patch.sh` |

## 2. Shared changes must not silently change behavior for every agent

A shared-infrastructure change is, by default, a change to every product on the
VPS. Any shared-platform PR must include:

1. **Affected-agent analysis** — which registered projects the change reaches,
   and why the others are untouched.
2. **Compatibility proof** — evidence existing callers keep working.
3. **Default behavior** — what happens for a product that does nothing.
4. **Activation posture** — dormant / shadow / internal-allowlist / canary /
   production.
5. **Rollback** — flag, kill switch, revert or config restore.
6. **Per-agent impact** — one line per materially affected product.
7. **Tests for every materially affected product.**

The Capability Reuse Map's `Shared-platform impact` and `Other agents affected`
fields carry (1) and (7); they may not be `none` on a shared-runtime change.

## 3. Two-way boundary

- **A shared component must not absorb product-specific business policy merely
  for convenience.** If logic is meaningful to exactly one product, it belongs
  to that product even when a shared directory is closer to hand.
- **A product must not reimplement shared Hermes/Shift functionality locally.**
  Identity, audit, approval codes, locking, send policy and routing are reached
  through the shared chokepoints, never re-derived per agent.

> **Known standing deviation.** Several product-specific modules physically live
> under `src/platform/` — `catering_*.py` (Catering Studio), `flyer_*.py`
> (Flyer Studio), `qbo_client.py` (Expense Bookkeeper). The registry classifies
> them by *owner*, not by directory, so governance treats them as product code.
> This is recorded here so it is not mistaken for the shared-component rule
> above being satisfied by location. Relocating them is a future cleanup, not a
> governance prerequisite, and is explicitly out of scope for governance-only
> changes.

## 4. Scope discipline for shared work

- Prefer extending an existing chokepoint over adding a parallel one. A second
  router, a second audit path, a second approval-code generator or a second
  send gate is presumed NO-GO under the universal directive §2, and the
  presumption is strongest here — shared duplicates fan out to every product.
- Shared work still owes a vertical outcome: a shared capability with no
  product exercising it end-to-end is architecture-only output, which §5 of the
  universal directive does not count as completion.
- An architecture exception scoped to shared platform **must identify every
  affected agent** in its `scope` and `affected_paths`.

## 5. Deployment and runtime closure

Deployment, the Hermes pin gate, env-symlink integrity, audit-log rotation and
runtime-closure verification are shared deterministic concerns. Do not weaken
them, and do not introduce a change that requires them to be bypassed, without
an approved exception describing the compensating control.

---

## Change control

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-01 | Initial shared-platform directive. |
