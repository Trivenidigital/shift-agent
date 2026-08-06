# SKILLS_ADOPTION_PLAN

**Date:** 2026-08-02. **Nothing installed, removed, enabled, disabled, or modified in producing
this plan.** No `WAVE_0_BLOCKERS.md` was created — no fresh runtime evidence established a
genuine blocker to skills adoption. See `DISPUTED_OR_NONBLOCKING_FINDINGS`.

## Guiding conclusion

**The best near-term return is not installing new skills. It is (a) removing routing-surface
noise and (b) using what is already installed.** main-vps carries 122 enabled skills, none
disabled; several skills the brief asks us to go find — `subagent-driven-development`,
`writing-plans`, `architecture-diagram`, `claude-design`, `segment-anything-model`, `dspy`,
`native-mcp` — are already present and apparently unused. External registry search returned
mostly community-trust, low-signal, mutually-forked results for our domains.

---

## Wave 0 — hygiene and routing surface (no new capability)

Reduces ambiguity and closes state/ruling mismatches. All actions are *disable* or *inspect* —
none add capability, so risk is bounded.

| # | Action | Host | Rationale |
|---|---|---|---|
| 0.1 | Disable `godmode` | main, vpin | Jailbreak skill on customer-facing production; zero fleet value (`SKILL_SECURITY_REVIEW` §1) |
| 0.2 | Disable `pokemon-player`, `minecraft-modpack-server`, `spotify`, `heartmula` | main, vpin | Routing-surface noise on business hosts |
| 0.3 | Consolidate 3 kanban skills → 1; 4 coding-agent skills → 1 | main | Direct routing ambiguity |
| 0.4 | Audit the 9 registered-inactive dispatchers; disable those confirmed dormant | main | Each adds routing surface with no demonstrated use |
| 0.5 | Per-skill `hermes skills inspect` for every Wave-1/2 candidate | all | Closes the inventory gap: version, deps, network, credentials |
| 0.6 | Confirm whether `hermes skills audit` mutates; run it if not | all | Deferred all engagement as a mutation risk |
| 0.7 | **Gecko-owned:** align `rest-graphql-debug` / `solana` / `evm` state with the rulings of record | srilu | Ruling exists; enforcement does not |

**Exit criterion** enabled-skill count per host materially reduced; every Wave-1 candidate
source-inspected.

## Wave 1 — read-only, already installed (no installation required)

| Skill | Agent | Validation |
|---|---|---|
| `subagent-driven-development` | shift | A bounded investigation completes with a written result |
| `writing-plans` | shift | Produces a plan conforming to the repo's plan convention |
| `debugging-hermes-tui-commands` | shift | Used successfully in one gateway triage |
| `architecture-diagram` | shift | Renders the dispatcher topology |
| `claude-design`, `design-md` | flyer | Critique pass on a known-bad flyer identifies the real defect |

**Rollback** disable the skill; nothing installed, nothing to uninstall.

## Wave 2 — supervised, capability-adding

| Skill | Agent | Gate |
|---|---|---|
| `segment-anything-model` | flyer | Source-inspected; region masking bounds an edit to the requested area on the failure fixtures |
| `comfyui` (scoped inpainting) | flyer | Demonstrated scoped edit that leaves logo/QR/footer provably unchanged |
| `baoyu-infographic` | flyer | Layout suggestion accepted by a human reviewer |
| `dspy` | catering/flyer | Used **offline** for prompt tuning only — never in the live reply path |
| one coding agent (`claude-code` **or** `codex`) | shift | Worktree isolation; no direct main-branch write |

## Wave 3 — organization skills

Author Tier 1 then Tier 2 from `CUSTOM_SKILLS_BACKLOG.md`, in that order:
`org/catering/inquiry-intake` → `org/flyer/exact-edit` → `org/flyer/asset-integrity` →
`org/shared/runtime-effective-diagnosis` → Tier 2.

Each ships with: an evidence contract, a replay test against existing fixtures, and a
disable-to-rollback path. `org/shared/skill-lifecycle` is authored last, after Wave 0 has
exercised the process by hand.

## Wave 4 — higher impact, evidence-gated

`native-mcp` per-server evaluation (the QBO/Stripe path named in `CLAUDE.md`) ·
`himalaya` for catering CRM/email handoff · `org/catering/proposal-from-approved-data` ·
`webhook-subscriptions` for ops. Each requires demonstrated Wave-2/3 success plus its own
security review.

## Deferred / rejected

Community WhatsApp-send skills (`whatsapp-messaging`, `oo-whatsapp`, skills.sh `whatsapp`) —
**permanently rejected**, deterministic-boundary violation. Community customer-support skills —
`REFERENCE_ONLY`. `browse-sh/*` — not applicable. Print-dimension and brand-palette validation —
implement as deterministic code, not skills.

---

## DISPUTED_OR_NONBLOCKING_FINDINGS

Each carries `OBSERVED FACT / INTERPRETATION / RISK / RECOMMENDED ACTION / BLOCKING STATUS`.

### D-1. Alleged srilu Hermes 0.14.0 divergence — **STALE_FINDING, withdrawn**

**OBSERVED FACT** Active service ExecStart is
`/home/gecko-agent/.hermes/hermes-agent/venv/bin/python`; PID 1912637 cwd
`/home/gecko-agent/.hermes/hermes-agent`; `importlib.metadata.version("hermes-agent")` from that
interpreter = **0.19.1**; pyproject at that root = 0.19.1. The 0.14.0 string came from
`/usr/local/lib/hermes-agent` — root-owned, mtime May 22, **not used by the service**.
**INTERPRETATION** A dormant reference install, not a runtime divergence. The operator's stated
baseline was correct.
**RISK** None to adoption. Residual: a stale install on disk can mislead future audits (it
misled this one).
**RECOMMENDED ACTION** Fleet session may remove or clearly mark `/usr/local/lib/hermes-agent` on
srilu. Cosmetic.
**BLOCKING STATUS** **NOT BLOCKING — finding withdrawn.**

### D-2. Pin/deploy-gate integrity — **CURRENT_NONBLOCKING_DEFECT**

**OBSERVED FACT** `tools/hermes-patch-baseline.txt` on app main declares `HERMES_VERSION=0.14.0`
/ commit `1e71b718`; main-vps and vpin-vps run 0.19.1 at `cc4cab2f5`; two app deploys landed
2026-08-01. The bridge that executes (`/root/.hermes/scripts/whatsapp-bridge/bridge.js`,
inode 1030775) is a different, untracked file from the git-tracked copy (inode 1538829), content
currently identical.
**INTERPRETATION** A real release-integrity defect in the **application deploy** contract.
**RISK** Wrong-artifact execution risk for *shift-agent application deploys*.
**Demonstrated causal impact on skill loading, skill routing, or skill installation: none.**
Hermes skills resolve from `HERMES_HOME` and the installed package, neither of which is governed
by the app pin gate.
**RECOMMENDED ACTION** Fleet session closes per `tasks/fleet-escalation-hermes-pin-deploy-gate.md`.
**BLOCKING STATUS** **NOT BLOCKING for skills adoption.** It blocks *application deployment*
work, which Waves 0–3 do not require. Wave 4 items that ship application code should wait on it.

### D-3. Cockpit exposure — **CURRENT_NONBLOCKING_DEFECT**

**OBSERVED FACT** nginx `0.0.0.0:8080`, no proxy auth, ufw inactive; `/api/docs` and
`/api/openapi.json` return 200 unauthenticated; `/api/roster` returns 401; 18/18 authenticated
GET routes return 401; `COCKPIT_AUTH_BYPASS` disables OTP **freshness** only, primary auth holds.
**INTERPRETATION** Confirmed public information exposure plus a production step-up-auth bypass.
Unrelated to Hermes skill loading — the Cockpit is a separate FastAPI service.
**RISK** Real, and separately owned.
**RECOMMENDED ACTION** Fleet/security session per
`tasks/production-access-control-verification.md`; containment already authorized.
**BLOCKING STATUS** **NOT BLOCKING** for read-only skills research or Waves 0–1. No proposed
skill touches the Cockpit.

### D-4. `godmode` — **QUARANTINE_CANDIDATE**

Measured: enabled on all three hosts, jailbreak description, 4 scripts, **0 evidence of ever
being selected**. Explicitly **not** `ACTIVE_SECURITY_DEFECT` — no proof of meaningful runtime
reachability or exercised dangerous capability. **BLOCKING STATUS: NOT BLOCKING**; Wave-0 item
0.1.

### D-5. Duplicate timers on srilu and main — **INCONCLUSIVE, not escalated**

**OBSERVED FACT** `eod-reconcile`, `send-daily-brief`, `check-compliance-deadlines` timer units
appear on both hosts.
**INTERPRETATION** Insufficient evidence. I did **not** verify enabled/active state, last-run
timestamps, tenant/recipient scope, leader election, or idempotency watermarks.
**RISK** *If* both fire for the same logical workload with the same recipient, duplicate owner
notifications. Unproven.
**RECOMMENDED ACTION** `systemctl list-timers --all` + last-run + recipient scope on both hosts.
Cheap.
**BLOCKING STATUS** **NOT BLOCKING.** Not escalated, per the standard that name similarity is not
duplicate execution.

### D-6. Zero disabled skills fleet-wide (S-1) — **CURRENT_NONBLOCKING_DEFECT**

316 enabled entries, no disable or quarantine state in use. Means documented quarantine rulings
have no enforcement. Addressed by Wave 0 and `org/shared/skill-lifecycle`. **NOT BLOCKING** —
it is the thing Wave 0 fixes.

---

## Known gaps in this research

Stated plainly rather than papered over: per-agent **routing bindings** and **invocation
telemetry** were not obtained (`skills list` does not expose them and I enabled no tracing), so
"underused" is an inference from agent SKILL.md chains, not a measurement. Per-skill version,
dependency, network-destination, and scan-verdict data are absent (Wave 0.5). Only four skills
were source-inspected. External research was conducted primarily through the Hermes-supported
registries rather than an exhaustive sweep of every catalog named in the brief.

## FINAL RULING

**`READY_FOR_SELECTIVE_SKILL_ADOPTION`**

Justification: the only alleged platform blocker that would have mattered (srilu version
divergence) is withdrawn on runtime evidence. D-2 and D-3 are real but have **no demonstrated
causal path** to skill loading, routing, or installation, and the standard for
`READY_AFTER_SPECIFIC_REMEDIATIONS` — naming a remediation and demonstrating how it blocks the
first wave — is not met by either. Wave 0 is hygiene *internal* to this plan, not an external
precondition, and consists entirely of disable/inspect actions that cannot add risk.

Adoption should begin at Wave 0 and must not skip Wave 0.5 (source inspection), since no
recommendation above Wave 1 rests on an inspected skill today.
