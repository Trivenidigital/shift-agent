# Stripe / live-payment enablement checklist

**Drift-check tag:** `extends-Hermes` — aggregates deploy-time safety gates on top of the existing tarball-deploy + `cfg.commerce.provider` flag flow. No Hermes-convention divergence; this is a runbook, not new substrate.

**Purpose.** These are the gates that must ALL be satisfied before enabling any live payment provider on a customer VPS — i.e. before setting `cfg.commerce.provider="stripe"` (or configuring a live `payment_checkout_url_template`). They previously lived only in PR descriptions and session memory; deploy-time safety gates belong in a checklist, not a commit message. Same discipline as the onboarding-runbook sub-rules. **Do not flip the provider flag until every box is checked.**

## Hermes-first capability checklist (per-step)

This is a documentation/runbook artifact, not agent code — Hermes owns none of it. Rows tagged per hook format:

| Gate | Tag | Note |
|---|---|---|
| G1. Confirm S1-1 guard deployed on target VPS | `[net-new]` | in-repo money-path guard; Hermes has no equivalent |
| G2. Confirm S1-1 full auto-resend recovery landed | `[net-new]` | in-repo; blocking prerequisite |
| G3. Confirm approval-code / deposit-link TTL driver exists (S2-14) | `[net-new]` | may be Hermes-owned cf-router `.pyc` — MUST verify, not assume |
| G4. Confirm provisioning `install.sh` SHA-pinned (S2-12) | `[net-new]` | in-repo provisioning discipline |

Red-flag check: all `[net-new]` because this is a runbook aggregating existing gates, not a build — no missed Hermes capability.

## Drift-rule self-checks (read-deployed-code evidence)

- ✅ Read `src/agents/catering/scripts/catering-mint-deposit` (S1-1 guard `_find_live_intent_for_lead` at line 153; `mint(originating_message_id=f"catering_deposit_{lead_id}")` at line 314; lead binding persisted at line 431) before drafting G1/G2.
- ✅ Read `tasks/commerce-slice2-catering-deposit-followup-backlog.md` (§12a watchdog trigger :36) and `tasks/commerce-slice1-followup-backlog.md` (`flock_state_path` :30) before drafting the slice-3 cross-references.

---

## The gates (ALL must be ✅ before `provider="stripe"`)

### G1 — S1-1 fail-closed double-charge guard is DEPLOYED (not just merged)
- [ ] The `reinvoke_live_intent_exists` guard (`catering-mint-deposit`, merged PR #578 / `f271204`) is present on the target VPS's deployed artifact — verify with the deploy smoke, not just `git log`.
- **Why:** without it, a crash between the customer-facing deposit-link send and the lead-binding persist causes a re-invocation to mint a **second, different** live payment link → the customer can pay both → double-charge. Merged ≠ deployed; `main` is not live until the tarball ships.

### G2 — S1-1 FULL auto-resend recovery has landed (BLOCKING prerequisite, not "if observed")
- [ ] The interim guard has been replaced (or paired) with **lead-keyed get-or-create reuse**: on re-invoke, reuse the existing non-terminal intent and **re-send its link** instead of refusing. Re-send MUST be status-aware — never void an already-`sent` intent on a transient re-send failure.
- **Why:** the shipped guard is interim and fail-CLOSED — it refuses the second mint and pages the operator, which is correct while the path is dormant, but on a live weekly path it converts every crash-window re-invoke into a manual-reconcile ticket. This is a **correctness bug's full fix and must NOT wait to be observed in canary.** Co-locate with the already-triggered §12a watchdog (`commerce-slice2-...backlog.md:36`) + `flock_state_path` (`commerce-slice1-...backlog.md:30`) items so the money-path hardening lands as one co-reviewed slice-3 unit.

### G3 — Approval-code / deposit-link TTL expiry driver is CONFIRMED to exist (S2-14) — this is a Stripe-gate, not a someday-item
- [ ] Verify — by reading the actual driver, not assuming — that something transitions aged approval codes / deposit links to `expired` / `no_response_timeout`. The candidate is the cf-router `.pyc`-only watchdog (`catering-owner-action-watchdog` / `catering-dispatcher-watchdog`, referenced in `shift-agent-deploy.sh` + `cf-router/actions.py` but present only as compiled bytecode with no in-tree source). Confirm it drives deposit-link TTL, or ship an expiry reconciler + timer before Stripe.
- **Why:** if no driver fires, **a catering deposit payment link tied to a lead never expires** — indefinite live-payment exposure. CLAUDE.md/portfolio advertise "4h TTL + dead-man escalation" as live; at HEAD that is an unverified state-machine leg. Under a placeholder provider this is harmless; under Stripe it is an open-ended liability.

### G4 — Provisioning `install.sh` is SHA-pinned (S2-12) — hard checklist item on the provisioning PR
- [ ] If this VPS is newly provisioned: the `curl … /hermes-agent/main/scripts/install.sh | bash` step in `docs/fleet-provisioning.md:95` has been changed to pin a specific commit/tag, download-to-file, verify a known SHA256, then execute — never pipe a mutable-branch URL into a root shell.
- **Why:** it installs the substrate the whole VPS runs on, as root, at the most privileged point in the lifecycle. Spec-stage today, but it becomes fleet-wide RCE exposure the moment provisioning executes as written. When the provisioning PR is authored, this is a **blocking** review item.

---

## Cross-references
- Disposition + approvals log: `tasks/audit-remediation-2026-07-plan.md`.
- S1-1 shipped guard: PR #578 (`f271204`). Backlog triggers: `tasks/commerce-slice1-followup-backlog.md`, `tasks/commerce-slice2-catering-deposit-followup-backlog.md`.
- G2/G3 are BLOCKING for Stripe; G4 is blocking for the provisioning PR; G1 is a per-deploy verification.
