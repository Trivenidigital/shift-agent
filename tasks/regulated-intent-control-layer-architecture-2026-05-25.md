**Drift-check tag:** extends-Hermes

# Regulated-Intent Control Layer — Architecture (PR A-E)

**Date:** 2026-05-25 (revised 2026-05-25 evening per operator review findings F1–F6; further revised 2026-05-26 morning per drift-check finding D1)
**Status:** portfolio-direction + safety contract, NOT a greenfield implementation map. Implementation proceeds via small targeted gap-fill PRs against the existing modules listed in the Drift-correction note below. See [`tasks/regulated-intent-gap-fill-pr-sequence-2026-05-26.md`](regulated-intent-gap-fill-pr-sequence-2026-05-26.md) for the current implementation map.
**Authors:** drafted by Claude Code on operator request; revised by Claude Code after operator-provided F1–F6, E1–E5, R1+R1b, and D1 findings.
**Supersedes / folds in:** `tasks/flyer-hermes-intent-operating-layer-backlog-2026-05-22.md` (H0 task description). H0 covered semantic routing + validator for Flyer; this doc generalizes the same pattern into a 6-PR control layer (PR-0 foundation + PR A-E verticals) applied horizontally across all SMB-Agents, with Flyer as the first vertical landing zone.

## Drift-correction note (2026-05-26 morning, finding D1)

A drift-check on `origin/main` 2026-05-26 morning found that ~50–60% of the per-agent Flyer infrastructure this doc planned is **already shipped** by the `codex-flyer-autodev-main.timer` autonomous lane. The existing Flyer implementations:

- `src/agents/flyer/intent.py` — PR B Semantic Account Router, shadow-deployed (`FlyerIntentMode` StrEnum with off/shadow/active states, 604 `flyer_hermes_intent_decision` rows/week of runtime evidence)
- `src/agents/flyer/action_registry.py` — PR C Action Registry, 11 actions declared (`FlyerActionDefinition` dataclass with action_id, command, domain, effect, requires_admin/confirmation/payment fields, `ACCOUNT_ACTIONS` dict + `normalize_account_command_text` semantic intent matcher)
- `src/agents/flyer/payment_state.py` — PR D Payment State Machine, 6-state Literal (`none/checkout_missing/checkout_ready/payment_pending/payment_confirmed/activated`)
- `src/agents/flyer/customer_copy_policy.py` — customer-copy policy foundation, internal-term focus (does NOT yet implement the PR-0 forbidden-completion-verbs lint)
- `src/agents/flyer/intent_training.py` + `src/agents/flyer/scripts/flyer-intent-training-export` — PR E training-data export, partial
- `src/plugins/cf-router/{actions,hooks}.py` — PR A regulated-intent firewall, partial (commit `0e431b8` shipped the F3 cf-router stronger-contract pattern + ~10 of the operator's 24 active-block patterns; tests `test_natural_upgrade_to_growth_routes_to_account_handler` and `test_regulated_billing_language_is_guarded_before_generic_passthrough` cover the F3 invariant)

**This doc is now portfolio-direction + safety contract, not a greenfield implementation map. Implementation proceeds via small targeted gap-fill PRs against the existing modules above.**

## Revisions per operator review

**First-round (2026-05-25 afternoon) — six findings F1–F6:**

| # | Finding | Severity | Where addressed |
|---|---|---|---|
| F1 | Copy lint at `safe_io.bridge_post` won't cover sends through 8 local `_bridge_post` helpers (verified grep: catering ×6, expense ×1, shift ×1) | BLOCKER | §7 PR-0 §7a — single linted send chokepoint + static gate (subsequently extended in second round to media + CTA) |
| F2 | Default `shadow` leaves the known unsafe path live for already-known failure patterns | BLOCKER | §8 PR A — tiered defaults: high-confidence regulated patterns active-fail-closed from day one; broader semantic surface stays shadow |
| F3 | cf-router "defer to gateway" preserves the bug; generic LLM speech is the unsafe actor | HIGH | §8 PR A — cf-router contract strengthened from "defer" to "route to regulated pipeline OR emit deterministic clarification/refusal; generic LLM is FORBIDDEN from speaking on regulated-intent text" |
| F4 | Action Registry `required_payment_state="paid OR payment_link_generated"` as a string invites drift | HIGH | §10 PR C — typed predicates (`frozenset[PaymentState]` + `CompletionSignalPredicate` ADT with `.evaluate(context) -> bool`) + tests proving every action computes all four invariant booleans deterministically |
| F5 | Audit failure for regulated actions must fail-closed; existing dispatcher policy is too soft | MEDIUM | §11 PR D — regulated-action execution treats audit-write failure as refusal trigger (subsequently tightened in second round with `mutation_class` + irreversible-vs-reversible copy split) |
| F6 | Evals as PR E means PR A-D ship without regression coverage of the known failure corpus | MEDIUM | §7 PR-0 §7c — eval seed corpus + harness shipped FIRST. PR E becomes only the self-evolution loop layer |

**Second-round (2026-05-25 evening) — five additional findings E1–E5, applied on top:**

| # | Finding | Severity | Where addressed |
|---|---|---|---|
| E1 | PR-0 as written has a temporary unsafe window (lint `measure`, eval gate `warn`, static-gate `xfail`); calling that state "safety foundation" risks merging PR A on top of unfinished safety | HIGH | §7d split — **PR-0 = instrumentation/scaffold; PR-0b = safety foundation** (load-bearing). Hard merge gate: no PR A merges until PR-0b's `active`+`block`+singularity+null-context-allowlist are all green on the base branch. Enforced by `tests/test_safety_foundation_load_bearing.py` |
| E2 | `action_context=None` can become the new bypass — every regulated send could "forget" to plumb context and silently pass the lint | HIGH | §7a #5–#6 — `None` from regulated pipeline is a runtime error; `None` allowed only from `SAFE_IO_NULL_CONTEXT_ALLOWLIST`; runtime caller check + static gate `tests/test_send_chokepoint_null_context_allowlist.py` |
| E3 | Media sends not covered by chokepoint rule — `bridge_send_media` + `bridge_send_cta` carry captions/labels that can hold completion claims | MEDIUM | §7a #2–#4 — chokepoint + lint extended to all three send functions (text body + media caption + CTA labels + media filename) |
| E4 | §3 architecture diagram is stale (says cf-router "must defer" and labels deploy gate as PR E) | MEDIUM | §3 diagram rewritten — cf-router terminal block on `regulated_active`; deploy gate labeled `PR-0 §7c`; explicit "generic LLM forbidden at this layer" |
| E5 | Rollback language too permissive for money/account actions ("rollback OR alert manual reconciliation") | MEDIUM | §11 — `mutation_class: Literal["local_reversible", "external_irreversible"]` added as required registry field. Rollback MANDATORY for `local_reversible`; `external_irreversible` gets a different refusal template forcing customer copy to say "under operator review", NOT "unchanged" |

The core invariant in §2 stands unchanged across both rounds. The revisions close implementation escape hatches around: send paths (F1+E3), shadow-mode fallback (F2), cf-router (F3), registry types (F4), audit-evidence enforcement (F5+E5), eval ordering (F6), foundation safety naming (E1), and the `None` bypass (E2).

**Third-round (2026-05-25 reframe) — one architectural finding R1, then operator correction R1b:**

| # | Finding | Severity | Where addressed |
|---|---|---|---|
| R1 | Original draft framed PR A-E as a uniform horizontal landing (Flyer first, Shift/Catering second, etc.). On honest review of deployed code, the "claim completion without action" failure mode emerged in Flyer's specific architectural split; Shift/Catering/Expense have a different deployed split (dispatcher matrix + identify-sender + 5-char `#XXXXX` approval codes + deterministic apply-scripts) and do not exhibit the same failure today. | ARCHITECTURAL | First-attempt fix narrowed PR A-E to Flyer-only-until-evidence, which **over-corrected** (see R1b). |
| R1b | Operator correction 2026-05-25: narrowing PR A-E to "Flyer-only-until-evidence" is the wrong response. The portfolio (Flyer, Shift, Catering, future 24 agents in the 2026-05-04 expansion) shares VPS, Hermes gateway, cf-router, bridge send paths, audit substrate, and deployment gates. The architecture should be **portfolio-wide with two distinct layers**, and Shift/Catering should stay as **first-class verticals** whose specifics are evidence-gated, NOT excluded. | ARCHITECTURAL | §6 relabeled (per-agent surfaces are "active-block list + registry entries TBD pending bounded smoke evidence", not "speculative — evidence required"); §14 reframed as portfolio-wide architecture with the layer split; §15 promotes Shift/Catering through the same PR A-E pattern but evidence-gates the active-block list and registry contents; §17 non-goals replaced "PR A-E not horizontal" with "PR A-E specifics are evidence-gated per agent"; §18 Q5 accepts the 24-pattern active-block list **for Flyer only**. |

**Net framing after R1+R1b (the canonical reading):**

> **Comprehensive portfolio architecture for Flyer Studio, Shift, Catering, and future SMB agents.**
>
> **Layer 1 — PR-0 / PR-0b: platform-wide, mandatory foundation.** Ships once. All agents inherit:
> - single send chokepoint (text + media + CTA)
> - forbidden completion/action-claim lint
> - `ActionExecutionContext` plumbing
> - `action_context=None` allowlist
> - conversation eval harness + deploy gate + regression corpus structure
> - audit fail-closed rules for regulated actions (with `mutation_class`)
> - cf-router/gateway rule: generic LLM cannot claim regulated completion
>
> **Layer 2 — PR A-E: repeatable per-agent vertical pattern.** Same architecture for every agent. Per-agent CONTENTS are evidence-gated:
> - regulated-intent surfaces (declared per agent)
> - active-block patterns (Flyer-confirmed today; Shift/Catering scoped from their own bounded-smoke + audit-chokepoint evidence; future 24 agents the same)
> - semantic classifier Hermes skill (per agent)
> - action registry entries (per agent)
> - state machine (when payment/external-irreversible mutations exist; not required when only `local_reversible` 5-char-code mutations exist)
> - fixtures (per agent)
> - smoke/live evidence gates
>
> **Landing order:** Flyer first because the failure corpus is already confirmed and live. Shift/Catering proceed through the same architecture, but their active-block list, registry entries, and fixtures are finalized from their own bounded-smoke + audit-chokepoint evidence — NOT copied from Flyer.
>
> **Future 24 agents** (from the 2026-05-04 portfolio expansion: Kitchen Ops, Customer Experience, additional verticals): must onboard through this control layer before production. PR A-E becomes the production-onboarding gate for every new agent in the portfolio.

---

## 1. Context — why this is a trunk-level fix, not a leaf patch

Flyer Studio accumulated 100+ PRs of "screenshot → parser patch → deploy" work over ~10 days. The pattern still loses, most recently when a vague *"Upgrade to Growth"* missed the exact-command parser, fell into generic Hermes/chat, and produced the dangerous reply *"I've processed your request to upgrade to Growth..."* — with no actual plan change, no payment, no validated authority.

The root cause is structural, not phrase-specific. The agent's split today is:

```
exact command matches    → deterministic action            (safe)
everything else          → generic assistant / LLM         (UNSAFE — can claim completion)
```

The same vulnerability exists wherever an agent has (a) regulated intents (money, identity, schedule, delivery state) AND (b) a generic LLM fallback that can speak. That is Flyer, Shift, Catering, and Expense Bookkeeper — every SMB-Agents agent except Daily Brief (which is read-only and cron-driven).

This doc is the architecture for converting that split into a safe control layer, applied horizontally.

## 2. The core invariant

> **No customer message receives a confident business/action response unless the system has all four of:**
> 1. **Structured intent** — Hermes-classified, with confidence + domain + action
> 2. **Validated authority** — sender role + ownership + permission for this action
> 3. **Valid state transition** — current state + target state + transition is legal
> 4. **Verified action result** — the deterministic mutation succeeded AND any external dependency (payment, write API, etc.) returned the required signal
>
> If any of the four is missing, the system may **clarify** or **refuse**. It may NOT acknowledge **completion**.

This is the production-readiness bar for any agent surface that touches money, identity, schedule, or delivery state. It is **stronger** than `pilot-readiness-check --text` PASS. Static-gate green is necessary but not sufficient.

## 3. The architectural shift

**Today's split (unsafe):**

```
inbound message
  └─ cf-router (pre-gateway) ─── may answer directly with generic LLM ─┐
                                                                       │ UNSAFE
  └─ dispatch_shift_agent ─── if matrix matches → safe handler         │
                          └── if not → handle_owner_command / LLM ─────┘
```

**Target split (safe):**

```
inbound message
  └─ cf-router (pre-gateway)
       ├─ classify with regulated-intent firewall (terminal here)
       │    on regulated_active  → emit deterministic clarification/refusal directly
       │                           via safe_io.bridge_post WITHOUT invoking any LLM
       │                           (cf-router returns terminal; gateway never sees it)
       │    on regulated_shadow  → audit + hand to dispatcher (gateway invokes firewall
       │                           active-mode at dispatcher Step 2.5; LLM still denied
       │                           on regulated text downstream)
       │    on unregulated       → existing cf-router routes proceed unchanged
       └─ generic LLM speech on regulated-intent text is FORBIDDEN at this layer

  └─ dispatch_shift_agent (gateway)
       └─ Step A: Regulated-Intent Firewall (PR A) — active-mode at gateway
            ├─ classify: regulated_active | regulated_shadow | unregulated
            └─ if regulated → must hand to Semantic Account Router (PR B)
                              (generic chat is denied the right to speak)
       └─ Step B: Semantic Account Router (PR B)
            └─ produce {domain, intent, params, confidence}
       └─ Step C: Action Registry lookup (PR C)
            └─ resolve action metadata: roles, required state, payment state,
              confirmation requirement, success/failure/clarify copy
       └─ Step D: Validation (existing deterministic code + PR D state machine)
            ├─ authority check
            ├─ state-transition legality check
            └─ payment-state check (PR D)
       └─ Step E: Response selection
            ├─ all four invariants satisfied → success copy + deterministic mutation + verified action result
            ├─ confidence/state insufficient    → clarification copy (one minimal question)
            └─ authority/payment/state denied   → refusal copy (explicit "no change has been made")

  └─ Customer-send chokepoint (PR-0 §7a)
       └─ ALL sends — bridge_post (text), bridge_send_media (media + caption),
          bridge_send_cta (CTA + text) — funnel through safe_io with the
          forbidden-verbs lint applied to text bodies AND media captions
          AND CTA labels

  └─ Deploy gate: Conversation Eval Harness (PR-0 §7c)
       └─ every prior failure is a fixture; deploy blocks on regression
       └─ PR E grows the fixture set over time from production signals
```

## 4. Hermes-first analysis

Per CLAUDE.md, per-step `[Hermes]` / `[net-new]` audit. Cross-checked against the 50+ skills installed at `/root/.hermes/skills/` on main-vps (verified 2026-05-25 22:36 UTC) + Awesome-Hermes-Agent ecosystem + the 2026-05-03 4-source skill audit (`tasks/skills-roadmap.md`).

| # | Step | Tag | Notes |
|---|---|---|---|
| 1 | WhatsApp inbound + sender-block prepend | `[Hermes]` | Hermes gateway, bridge, identify-sender |
| 2 | cf-router pre-gateway routing | `[Hermes]` | existing plugin; needs deny-list edit for regulated-intent text (no new substrate) |
| 3 | Semantic intent classification from messy customer text | `[Hermes]` | Hermes LLM/gateway + JSON-schema-conformant extraction; this is exactly what Hermes is good at |
| 4 | Audit emission per classification decision | `[Hermes]` | `safe_io.ndjson_append` + `log-decision-direct` chokepoint + new `LogEntry` discriminated-union variants |
| 5 | Regulated-Intent Firewall gate logic (deterministic allow/deny) | `[net-new]` | Project-specific safety policy; cannot live in Hermes — it's the contract between Hermes and our deterministic code |
| 6 | Action Registry (declarative metadata for each customer-visible action) | `[net-new]` | Project-specific; no Hermes skill provides "this agent's regulated action catalog" |
| 7 | Validation: authority + state + payment | `[net-new]` mostly | Authority lookup uses Hermes `identify-sender` `[Hermes]`; state-machine legality + payment gate is project deterministic code |
| 8 | Payment State Machine | `[net-new]` (+ Hermes pre-checks) | Hermes can hold conversation context; the durable state + completion gate is project code. `mcp/native-mcp` may bridge to Stripe MCP server later; not in scope for PR D skeleton |
| 9 | Conversation Eval Harness | `[net-new]` glue, `[Hermes]` replay | Replay infra (PRs #72/#73/#74) is deployed. Net-new: failure-to-fixture conversion, regression rule, deploy-gate wiring |
| 10 | Self-evolution loop (cluster, classify, propose PR) | `[Hermes]` orchestration, `[net-new]` policy | Hermes Skills Hub has `claude-code`, `codex`, `kanban-codex-lane` — same pattern as `codex-flyer-autodev-main.timer`, generalized. The autonomy gate stays at `worker_draft` / `pr_ready` per `tasks/hermes-claude-codex-autonomous-architecture-2026-05-23.md` |
| 11 | Forbidden-completion-verbs lint | `[net-new]` | Trivial regex/string lint; cheap and high-value. Belongs in deterministic copy-policy module alongside existing `flyer_customer_copy_policy.py` |

**Awesome-Hermes-Agent ecosystem check:** no turnkey "regulated-intent control layer for SMB agents" exists. Found: Hermes delegation primitives, intent-classification skill patterns, self-evolution-kit at `github.com/NousResearch/hermes-agent-self-evolution`. Verdict: reuse Hermes for what it owns (semantic routing, memory, classification, orchestration); build only the deterministic gating + registry + state-machine + eval-harness glue. ~7 of 11 surfaces use Hermes substrate; ~4 are genuine net-new project code.

## 5. Read-deployed-code grounding

Per CLAUDE.md drift rules, code read before drafting:

- ✅ `tasks/flyer-hermes-intent-operating-layer-backlog-2026-05-22.md` (the H0 backlog this generalizes from) — lines 1–187
- ✅ `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` — full file (routing matrix at lines 82–101, mandatory tool sequence at lines 14–35, hard rules at 224–232). The regulated-intent firewall + semantic router land as Step 3.5 between identity-resolution (Step 2) and matrix classification (Step 3)
- ✅ `src/platform/schemas.py` — grep for `LogEntry` + `Literal[]` discriminated unions (lines 2562+, plus FlyerRecoveryConfig mode at line 740 — same `off/observe/.../pr_ready` mode-flag shape we'll reuse for `FLYER_HERMES_INTENT_MODE` and `SHIFT_HERMES_INTENT_MODE` etc.)
- ✅ `src/plugins/cf-router/` exists at `hooks.py` + `actions.py` (Hermes pre-gateway plugin) — confirmed as the layer where generic LLM gets to speak today
- ✅ `tasks/hermes-claude-codex-autonomous-architecture-2026-05-23.md` — Hermes/Claude/Codex role split + autonomy modes (`off`/`observe`/`bundle`/`worker_draft`/`pr_ready`/`deploy_proposal`/`autodeploy`). Self-evolution loop in PR E reuses this exact gating
- ✅ Memory: `feedback_dont_overengineer_llm_intent.md` — 2026-05-06 correction (Hermes classifies free-form intent; deterministic code validates + acts). This vision is the systematic application of that rule
- ✅ Memory: `feedback_hermes_skills_landscape.md` — 2026-05-03 4-source audit confirmed no off-the-shelf regulated-action registry skill
- ✅ Send-chokepoint audit (added per F1 review finding 2026-05-25 evening) — `Grep` for `def _bridge_post|def bridge_post`:
  - 1 canonical: `src/platform/safe_io.py:624 def bridge_post(jid, message)`
  - 8 local re-implementations that bypass the canonical chokepoint:
    - `src/agents/expense_bookkeeper/scripts/apply-expense-decision:176`
    - `src/agents/shift/scripts/send-coverage-message:96`
    - `src/agents/catering/scripts/apply-catering-owner-decision:99`
    - `src/agents/catering/scripts/create-catering-lead:237`
    - `src/agents/catering/scripts/finalize-catering-menu:221`
    - `src/agents/catering/scripts/create-catering-proposal-options:233`
    - `src/agents/catering/scripts/select-catering-proposal:128`
    - `src/agents/catering/scripts/send-catering-ack:76`
  - Additional callers/references in `src/agents/flyer/scripts/flyer-recovery-watchdog`, `src/plugins/cf-router/actions.py`, `src/agents/flyer/scripts/send-flyer-package`, `src/agents/daily_brief/scripts/send-daily-brief`, `src/agents/compliance/scripts/check-compliance-deadlines.py`, plus SKILL.md references in catering + expense
  - **Implication:** any lint applied only to `safe_io.bridge_post` reaches at most 1 of 9 send paths. PR-0 (§7) consolidates this to a single chokepoint + adds a static gate (test or pre-commit) that fails the build on any new `def _bridge_post`-style helper outside `safe_io.py`

## 6. Per-agent regulated-intent surfaces

**Per R1+R1b reframe (2026-05-25):** the firewall scope is per-agent. PR A-E is a **portfolio-wide repeatable pattern**; every agent in the portfolio (Flyer, Shift, Catering, Expense, future 24 agents) eventually onboards through it. What differs per agent is the EVIDENCE that finalizes each agent's active-block list, registry entries, and fixtures. Flyer has confirmed evidence today and lands the first vertical. Shift/Catering/Expense have first-class status in the architecture; their per-agent contents below are starting points, finalized from each agent's bounded-smoke + audit-chokepoint evidence per §15.

### Flyer  **[evidence: CONFIRMED — first vertical lands here]**

Source: the dangerous reply *"I've processed your request to upgrade to Growth..."* observed 2026-05-25, plus ~100 phrase-fix PRs documented in `tasks/lessons.md` 2026-05-15+ and recent `codex/flyer-*` branch commits. This is the evidence corpus that drives the Flyer PR A active-block list (§18 Q5 — 24 patterns operator-confirmed).

| Domain | Example regulated intents |
|---|---|
| billing | upgrade plan / downgrade plan / change plan / cancel plan / refund / pause billing |
| payment | payment link / mark paid / pay later / dispute / "I paid" / "processed my payment" / Stripe/Razorpay reference |
| account | change business name / change WhatsApp number / change address / change phone / change owner / add authorized requester / remove authorized requester |
| business identity | claim/transfer business ownership / merge accounts / duplicate-phone recovery |
| delivery state | "did you send the flyer" / "where is my flyer" / "approve" / "I approve" / "send to customer" / "send now" |
| brand kit | change logo / change template / replace reference / delete brand asset |

### Shift  **[evidence: pending bounded smoke + audit-chokepoint verification per §15]**

The Shift agent uses dispatcher matrix + `identify-sender` + 5-char `#XXXXX` approval codes + deterministic apply-scripts. Failure modes in the existing corpus are routing-correctness (false content-match before identity, missing `dispatcher_routed` audit row). The surfaces below are the candidate scope for Shift's PR A vertical; the FINAL active-block list, registry entries, and fixtures are scoped from Shift's bounded-smoke evidence (audit row inventory, sender-identity edge cases, state-machine assertion mismatches) — NOT copied from Flyer's list.

| Domain | Candidate regulated intents (final list scoped from Shift smoke evidence) |
|---|---|
| schedule | swap with X / cover me on Y / time off next week / I'm sick today / sick tomorrow / call out |
| identity | change employee phone / change my name / I'm not at this store anymore |
| compliance | "marked X done" / inspection complete / license renewed (existing `compliance_owner_query` already gates this — confirms the pattern) |
| owner overrides | approve absence / deny swap / override roster |

### Catering  **[evidence: pending bounded smoke + audit-chokepoint verification per §15]**

Catering uses dispatcher + state-aware routing + `parse_catering_inquiry` (LLM does EXTRACTION, gated by deterministic apply-scripts) + 5-char approval codes + deterministic apply-script (`apply-catering-owner-decision`). Failure modes in the existing corpus are state/routing errors (stale-lead-swallowing-new-inquiry, compound-confirm-alias-routing, employee-vs-customer identity gating). The surfaces below are the candidate scope; the FINAL active-block list, registry entries, and fixtures are scoped from Catering's bounded-smoke evidence — NOT copied from Flyer's list.

| Domain | Candidate regulated intents (final list scoped from Catering smoke evidence) |
|---|---|
| quote / proposal | accept this / select option 2 / lock it in / proceed / finalize / I'll go with X (existing PR-CF1/CF2 keyword routing covers some) |
| deposit / payment | I paid the deposit / send invoice / refund deposit / pay later (deposit flow is operator-side today, not customer-WhatsApp; mutation_class = `external_irreversible` when this surfaces customer-side) |
| menu | apply menu update / replace menu / update prices / change items (existing image-with-caption + code-confirm flow handles this) |
| event details | change headcount / change date / cancel order / add dietary req |

### Daily Brief

Read-only. No regulated-intent surface. Inherits PR-0/PR-0b foundation; does NOT receive PR A-E per-agent scoping (nothing to scope).

### Expense Bookkeeper  **[evidence: pending bounded smoke per §15]**

Expense uses 5-char approval codes + `apply-expense-decision` deterministic script. No customer-facing money path today (QBO is operator-side). Candidate surfaces below; final scope from Expense's bounded-smoke evidence:

| Domain | Candidate regulated intents (final list scoped from Expense smoke evidence) |
|---|---|
| QBO push | push to QBO / mark posted / undo last / reverse |
| receipt classification | this is a personal expense / change category / change vendor |
| approval | approve E0001 / undo E0001 / reject (already deterministic) |

### Future agents (from 2026-05-04 portfolio expansion: Kitchen Ops, Customer Experience, +14 others)

Per R1b, every new agent must onboard through PR-0/PR-0b foundation + PR A-E per-agent vertical BEFORE production. The control layer is the canonical production-onboarding gate for the portfolio. Per-agent contents (regulated surfaces, active-block list, classifier, registry, fixtures, mutation_class declarations) are scoped from each new agent's pre-production smoke + audit evidence — same pattern as Flyer/Shift/Catering.

## 7. PR-0 — Foundation: Send Chokepoint + Forbidden-Verbs Lint + Eval Seed Corpus + Harness

**Added per operator review findings F1 + F6 (2026-05-25 evening).** This PR ships BEFORE any of PR A-E and is a prerequisite for them. It addresses two structural concerns the original draft underweighted: (a) the safety primitives must reach every customer send (not just the canonical chokepoint), and (b) the safety contract must be defined by regression fixtures BEFORE any code that implements the contract.

### 7a. Universal send chokepoint enforcement

**Problem (F1 + F3-revision-Medium per 2026-05-25 evening review):** there are TWO send families that reach customers:

- **Text family:** `safe_io.bridge_post` (1 canonical) + 8 local `_bridge_post` re-implementations.
- **Media family:** `safe_io.bridge_send_media` (1 canonical) — used by `cf-router/actions.py:2308`, `send-flyer-package:281`, `send-flyer-campaign:65`. Plus `safe_io.bridge_send_cta` for WhatsApp CTA buttons (CTA labels + body text are customer-visible).

A lint scoped only to `bridge_post` would miss media captions and CTA labels — a completion claim can move from text body into a caption ("Your upgrade is processed — see the new plan flyer attached") and bypass the safety contract. **Both families must be chokepointed.**

**Scope:**

1. **Consolidate text family.** All 8 local `_bridge_post` helpers replaced with a single import of `safe_io.bridge_post`. Each script keeps any per-script logging or audit row it already emits, but the actual HTTP POST + pre-send lint funnel through the canonical function.
2. **Confirm media/CTA families.** `safe_io.bridge_send_media` and `safe_io.bridge_send_cta` are already the only implementations (grep verified). The lint extension below makes the chokepoint property load-bearing rather than incidental.
3. **Static gate (extended).** Test `tests/test_send_chokepoint_singularity.py` greps the source tree and asserts:
   - `def\s+_?bridge_post\s*\(` matches only `src/platform/safe_io.py`
   - `def\s+_?bridge_send_media\s*\(` matches only `src/platform/safe_io.py`
   - `def\s+_?bridge_send_cta\s*\(` matches only `src/platform/safe_io.py`
   - Direct `requests.post(...)` to `/send`, `/send-media`, or `/send-cta` endpoints matches only `src/platform/safe_io.py`
   Build fails on any new offender. Wired into existing `tools/check-shift-agent-patch.sh` deploy gate.
4. **Pre-send lint hook on ALL three functions.** Each of `bridge_post`, `bridge_send_media`, `bridge_send_cta` calls `safe_io.lint_customer_copy(payload, action_context)` immediately before the HTTP POST. The `payload` argument carries:
   - for text: the body text
   - for media: the caption text + media filename (filename matters because Flyer file paths can leak project IDs / internal queue language)
   - for CTA: all button labels + body text
   Lint failure = refuse to send + emit `customer_copy_lint_rejected` audit row (with which payload field tripped the lint) + raise a typed exception the caller must handle (no silent suppression).
5. **Action-context plumbing.** All three send functions extended to accept `action_context: ActionExecutionContext | None` (Pydantic, declared in `src/platform/schemas.py`). When present, the lint MAY require `verified_action_result=True` for forbidden-verbs to pass. **When `None` — see the next item, this is the dangerous default.**
6. **`action_context=None` allowlist enforcement (per F2-revision-High 2026-05-25 evening review).** Allowing `None` unconditionally lets `action_context=None` become the new bypass — every regulated send could "forget" to plumb context and silently pass the lint. The rule:
   - **`None` is a regulated-pipeline error.** Any code path that originated from the regulated pipeline (firewall verdict was `regulated_active` or `regulated_shadow`, OR action_registry resolved an entry, OR PR D payment-state-machine fired) MUST pass a non-null `ActionExecutionContext`. Passing `None` from a regulated pipeline is a runtime error: the send is rejected, a `regulated_send_missing_action_context` audit row lands, and the operator is alerted.
   - **`None` is allowed only from an explicit allowlist.** A const set `SAFE_IO_NULL_CONTEXT_ALLOWLIST: frozenset[str] = frozenset({"shift-agent-health", "send-daily-brief", "eod-reconcile", "shift-agent-notify-owner", ...})` enumerates the scripts that may legitimately send without a context (system health, cron heartbeats, fail-closed system messages). The allowlist lives in `safe_io.py` alongside the chokepoint functions.
   - **Runtime check:** the chokepoint inspects `sys.argv[0]` (or an equivalent caller identifier — `inspect.stack()` frame above the chokepoint call) and verifies it is on the allowlist when `action_context is None`. Failure rejects the send.
   - **Static gate:** test `tests/test_send_chokepoint_null_context_allowlist.py` greps for chokepoint callers and asserts every caller either (a) passes a non-null `action_context`, or (b) is on the allowlist with a justification comment.
   - **Defense in depth:** even within the allowlist, the lint still runs against forbidden completion verbs. The allowlist exempts the `verified_action_result=True` requirement, NOT the verb list. A system message saying "Daily brief sent" still has to pass forbidden-verbs lint (it would fail; the helper would need to say "Daily brief delivered to owner" or use a non-completion verb).

**Net-new code estimate:** ~200 LOC text-family consolidation + ~80 LOC static gate (singularity + null-context allowlist) + ~120 LOC lint hook across three send functions + ~120 LOC `ActionExecutionContext` schema + ~60 LOC allowlist enforcement. ~600 LOC, mostly mechanical.

### 7b. Forbidden-completion-verbs lint module

**Problem (F1 + the cheap-shim rationale from §13):** the lint module needs to exist before the chokepoint can call it.

**Scope:**

- `src/platform/customer_copy_policy.py` (new, generalizes the existing Flyer-only `src/agents/flyer/flyer_customer_copy_policy.py`).
- `FORBIDDEN_COMPLETION_VERBS = frozenset({"processed", "completed", "upgraded", "downgraded", "changed", "confirmed", "sent", "approved", "paid", "posted", "pushed", "applied", "scheduled", "booked", "cancelled", "refunded"})`.
- `lint_customer_copy(text: str, action_context: ActionExecutionContext | None) -> LintResult` — returns `Allow` or `RejectForbiddenCompletion(verbs_found=[...], required=verified_action_result_true)`.
- Language-aware fold-in: same list extended with translations Hermes-curated for Telugu / Hindi / Tamil / Kannada / Malayalam (per the 2026-05-15 multilingual lesson in `tasks/lessons.md`).
- Existing `src/agents/flyer/flyer_customer_copy_policy.py` keeps its other policies (banned terms, project IDs, internal queue language) and delegates the forbidden-verbs check to the new platform module.

### 7c. Eval seed corpus + harness

**Problem (F6):** PR A-D are safety-sensitive. Their TDD contract IS the corpus of known failures. Ship the corpus first, then build A-D against it.

**Scope:**

- `tests/conversation_evals/` new directory structure:
  - `tests/conversation_evals/seed/flyer/` — billing / payment / account / business-identity / delivery-state / brand-kit (~30 fixtures from the known Flyer failure corpus including the "Upgrade to Growth" / "Move me to the 69.99 plan" / "I want the middle plan" trio)
  - `tests/conversation_evals/seed/shift/` — sick-call ambiguity, swap, time-off (~10 fixtures)
  - `tests/conversation_evals/seed/catering/` — proposal acceptance vague-text, deposit, menu apply (~10 fixtures)
  - `tests/conversation_evals/seed/expense/` — QBO push intent, undo (~5 fixtures)
- Each fixture is a YAML/JSON file: `inbound_text`, `sender_role`, `agent`, `expected_classification`, `expected_response_class` (`success | clarify | refuse`), `expected_audit_rows[]`, `expected_forbidden_verb_violations_if_any`.
- `tools/run-conversation-evals.sh` (new) — runs all fixtures, fails non-zero on any deviation.
- Wired into existing `shift-agent-deploy.sh` pre-deploy gate.
- Harness extends the existing dispatcher-replay infrastructure (PRs #72/#73/#74 already shipped).

**Where the corpus comes from:** combination of (a) operator screenshots, (b) `/opt/shift-agent/state/flyer/*/decisions.log` failure cases, (c) recent codex/flyer-* branch commit messages naming the specific regression, (d) `tasks/lessons.md` 2026-05-15+ entries. Operator to confirm seeding source in Open Question §18 Q2.

### 7d. PR-0 ship contract — two-PR split per F1-revision-High (2026-05-25 evening review)

The original draft called PR-0 the "safety foundation." That is **misnamed**: PR-0 as scoped ships in measure-only mode with `xfail` on the static-gate test until the 8 local `_bridge_post` helpers are consolidated. While in that state, the safety properties (single chokepoint, active lint, blocking eval gate) do NOT hold. Calling that state "foundation" risks merging PR A on top of a foundation that isn't load-bearing yet.

**The fix: split into two PRs with a hard merge gate between them.**

| PR | Name | Scope | Safety properties | Mode flags |
|---|---|---|---|---|
| **PR-0** | **Instrumentation / scaffold** | Lands the chokepoint signature changes (`bridge_post` / `bridge_send_media` / `bridge_send_cta` accept `action_context: ActionExecutionContext \| None`), the lint module (`customer_copy_policy.py`), the `ActionExecutionContext` schema, the eval harness infrastructure (`tools/run-conversation-evals.sh`), and an empty seed corpus directory structure. The 8 local `_bridge_post` helpers are NOT yet consolidated. The static-gate test is `xfail`. The lint runs in `measure` mode. The eval gate runs in `warn` mode. | None — this is scaffolding only. The doc treats PR-0 as plumbing, not safety. | `CUSTOMER_COPY_LINT_MODE=measure`, `CONVERSATION_EVAL_GATE_MODE=warn` |
| **PR-0b** | **Safety foundation** (load-bearing) | Consolidates the 8 local `_bridge_post` helpers into `safe_io.bridge_post`. De-xfails the static-gate test (singularity AND null-context allowlist tests both green). Promotes lint to `active`. Promotes eval gate to `block`. Seeds the conversation-evals corpus with the operator-curated set (~30 Flyer + ~10 Shift + ~10 Catering + ~5 Expense). | THIS PR is the safety foundation. After PR-0b merges, the four chokepoint+lint+eval+singularity properties hold simultaneously. | `CUSTOMER_COPY_LINT_MODE=active`, `CONVERSATION_EVAL_GATE_MODE=block` |

**Hard merge gate:**

- **No PR A merge is allowed until PR-0b has landed.** This includes the active-block patterns that PR A introduces — they cannot land before the foundation is load-bearing.
- A repo-level CI check enforces this: `tests/test_safety_foundation_load_bearing.py` verifies (a) `CUSTOMER_COPY_LINT_MODE=active` is the configured default, (b) `CONVERSATION_EVAL_GATE_MODE=block` is the configured default, (c) the static-gate singularity tests are NOT `xfail`, (d) at least one fixture exists per agent's regulated domain. PR A's CI fails if any of these is false on the base branch.

**After PR-0b ships**, every subsequent PR A-D MUST be developed with the harness green for the relevant fixtures. PR A cannot merge if any Flyer billing/account/delivery/account-identity fixture would regress.

### 7e. Mode flags (post-split)

- `CUSTOMER_COPY_LINT_MODE=measure | active` — `measure` in PR-0, **`active` in PR-0b** (load-bearing). Cannot be downgraded back to `measure` without explicit operator + a documented incident.
- `CONVERSATION_EVAL_GATE_MODE=warn | block` — `warn` in PR-0, **`block` in PR-0b**. Same one-way ratchet.

### 7f. Mapping to operator's PR A-E framing

The original framing ("draft PR A-E architecture") stands. PR-0 + PR-0b together form the foundation/scaffold the operator's PR A-E rest on; they are the practical embodiment of the operator's F1+F6 corrections. PR-0 is the wiring; PR-0b is the safety contract going live. PR A-E follow.

### 7f. Mapping to operator's PR A-E framing

The original framing ("draft PR A-E architecture") stands. PR-0 is the foundation/scaffold the operator's PR A-E rest on; it is the practical embodiment of the operator's F1+F6 corrections.

---

## 8. PR A — Regulated Intent Firewall

**Scope:** the gatekeeper. Inserted between dispatcher Step 2 (identify-sender) and Step 3 (message-shape classification). Two enforcement layers: (a) deterministic high-confidence active-block patterns deployed fail-closed from day one; (b) broader semantic surface shadow-measured then promoted. Also a stronger cf-router contract per F3 review.

**What it does:**

1. After identity resolution, hand the message text + sender role to a deterministic classifier (regex + keyword + per-agent regulated-intent vocabulary table) that returns one of three verdicts:
   - `regulated_active` — text matches a HIGH-CONFIDENCE active-block pattern (e.g. exact known-failure phrases from the seed corpus: "upgrade to <plan>", "change phone", "where is my flyer", "I'll go with option <N>"). Generic chat is **denied immediately** — fail-closed. Path proceeds to PR B router → PR C registry → response selection. If router/registry are not yet shipped for this vertical, emit a deterministic clarification copy.
   - `regulated_shadow` — text matches a BROADER semantic surface (any keyword in the per-agent regulated vocabulary). Audit is written, but routing falls through to the existing matrix until measurement-mode promotion criteria are met.
   - `unregulated` — existing dispatcher matrix path continues unchanged.
2. Emit canonical audit row `regulated_intent_firewall_decision` with `{ts, message_id, sender_role, sender_phone, sender_lid, verdict, matched_pattern_id, domains[], agent, path_selected}`.
3. The active-block list is a versioned file (`src/platform/regulated_intent_active_patterns/<agent>.yaml`); every active pattern has a fixture in the §7c corpus before it can land in active.

**Cf-router contract (revised per F3):** the original "defer to gateway" wording is too weak — generic gateway/LLM is the unsafe actor. The strengthened contract is:

> When cf-router processes an inbound where the regulated-intent classifier returns `regulated_active` OR `regulated_shadow`:
> - The plugin MUST EITHER route the message directly to the regulated pipeline (firewall → router → registry → response) without invoking a generic LLM at any stage,
> - OR emit a deterministic clarification/refusal copy that does NOT pass through any LLM.
>
> **"Defer to gateway" without strengthening is forbidden**, because the gateway itself can hand the message to a generic LLM. The deny must be specific: generic LLM speech on regulated-intent text is FORBIDDEN at every layer (cf-router, gateway, dispatcher matrix, downstream skill).

Practical implementation: cf-router/hooks.py adds a regulated-classifier check BEFORE its existing deterministic branches; if the verdict is `regulated_active`, cf-router emits the clarification copy directly via `safe_io.bridge_post` (with the PR-0 lint enforcing forbidden-verbs) and returns terminal — no gateway hand-off. If the verdict is `regulated_shadow` and cf-router has no deterministic response that fits, hand-off to the gateway is allowed BUT the gateway must invoke the firewall in active mode (the dispatcher Step 2.5 insertion enforces this).

**Where it lands:**

- `src/platform/regulated_intent_firewall.py` (new) — classifier + per-agent active-pattern loader + shadow-vocabulary loader.
- `src/platform/regulated_intent_active_patterns/<agent>.yaml` (new, one per agent) — versioned active patterns with their seed-corpus fixture ID references.
- Edit to `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` — add Step 2.5 calling the firewall + conditional Step 3 path on the three verdicts.
- Edit to `src/plugins/cf-router/hooks.py` — pre-gateway active-block + shadow-measure routing per the strengthened contract above.
- New `LogEntry` variants in `src/platform/schemas.py`: `_RegulatedIntentFirewallDecision` (with verdict enum), `_RegulatedIntentFirewallBypass` (operator break-glass), `_RegulatedIntentGenericLLMBlocked` (cf-router or gateway prevented generic LLM from speaking on regulated text — observability for the safety contract).

**Tests:**

- Unit: 30+ classifier fixtures (positive + negative for `regulated_active` / `regulated_shadow` / `unregulated`).
- Integration: dispatcher-replay harness extension — every prior dispatcher fixture re-runs with the firewall inserted; no existing unregulated route should be broken.
- Cf-router test: synthetic regulated-active inbound asserts cf-router emits clarification copy + does NOT invoke any LLM (mock the gateway and assert it is never called).
- Subprocess: at least one E2E test per agent for each of the three verdicts.
- §7c corpus regression: every fixture passes the new firewall path.

**Mode flags (revised per F2):** TWO independent flags per agent (not one):

- `<AGENT>_FIREWALL_ACTIVE_BLOCK=enabled | disabled` — controls the high-confidence active-block list. **Default `enabled` on first deploy for Flyer** (known failure corpus exists; per F2 we will not leave the known unsafe path live). Default `disabled` for Shift/Catering/Expense until per-agent corpus seeds + their active patterns are reviewed.
- `<AGENT>_FIREWALL_SHADOW_MEASURE=enabled | disabled` — controls the broader semantic-surface shadow measurement. Default `enabled` for all agents at PR A ship time.

Promotion from shadow to active for a given pattern requires: (a) pattern added to active-patterns YAML, (b) at least one seed-corpus fixture for it, (c) 7 days of shadow-measure with no unexpected blocks. Per-pattern promotion, not all-or-nothing per agent.

## 9. PR B — Semantic Account Router

**Scope:** the brain. Hermes turns vague regulated-intent text into structured intent. Folds in H0 from `tasks/flyer-hermes-intent-operating-layer-backlog-2026-05-22.md`.

**Decision schema:**

```json
{
  "domain": "billing | payment | account | business_identity | delivery_state | brand_kit | schedule | quote | deposit | menu | event_details | qbo_push | classification | approval | ...",
  "intent": "change_plan | upgrade_plan | downgrade_plan | refund | mark_paid | change_business_name | swap_shift | time_off | accept_quote | apply_menu | push_to_qbo | ...",
  "params": { "plan_id": "growth", "amount_usd": 69.99, "target_date": "2026-06-01", ... },
  "confidence": 0.0–1.0,
  "evidence_terms": ["upgrade", "Growth", "$69.99"]
}
```

**Where it lands:**

- One canonical Hermes skill: `flyer_account_intent` (already named in H0). Generalize to `regulated_intent_classifier_<agent>` per vertical (`shift_regulated_intent_classifier`, `catering_regulated_intent_classifier`, etc.) — one Hermes skill per agent's regulated surface; not one global classifier (per CLAUDE.md anti-umbrella-skill discipline).
- Strict `RegulatedIntentDecision` Pydantic schema in `src/platform/schemas.py`. `extra="forbid"` on the decision shape (state-side); the Hermes skill output may emit unmodelled fields and the schema applies `extra="ignore"`.
- Deterministic `RegulatedIntentValidator` rejecting any decision the classifier emits that fails the schema. Validator failure = clarification path.
- Fallback when Hermes is unavailable: existing deterministic route continues + `regulated_intent_hermes_unavailable` audit row + clarification copy (do not silently let generic chat answer).

**Mode flag:** `<AGENT>_HERMES_INTENT_MODE=off | shadow | low_risk_active | active`. Same shape as `FlyerRecoveryConfig.mode` already in schemas.py. Default `shadow` for first deploy. Per-agent independent flag; one agent's promotion does not promote another.

**Promotion criteria** (per the 2026-05-22 H0 backlog + this generalization):

- 20+ replay scenarios green for that agent
- zero validator bypasses across the replay set
- 5–10 live shadow messages with no high-risk disagreement
- low-risk intents (status check, clarification) promoted before high-risk (plan change, payment, identity, delivery)

## 10. PR C — Action Registry

**Scope:** the contract. Every regulated action declares its metadata in one place so behavior is registry-driven, not code-branch-driven.

**Action declaration shape (revised per F4 — typed predicates, no string expressions):**

```python
# All conditions are TYPED PREDICATES with .evaluate(context: InvariantContext) -> bool.
# No string-expression DSLs. No "OR" parsing at runtime. Enums + ADT predicates.

class PaymentState(StrEnum):
    pending_intent = "pending_intent"
    link_generated = "link_generated"
    awaiting_webhook = "awaiting_webhook"
    paid = "paid"
    cancelled = "cancelled"
    refunded = "refunded"

class AccountState(StrEnum):
    payment_pending = "payment_pending"
    trial = "trial"
    active = "active"
    suspended = "suspended"
    cancelled = "cancelled"

class CompletionSignal(Pydantic-discriminated-union):
    # exactly one of:
    PaymentWebhookReceived(provider: Literal["stripe", "razorpay", "manual"])
    OperatorMarkedPaid(operator_id: str, marked_at: datetime)
    DeterministicHandlerSuccess(handler: str, exit_code: Literal[0], audit_row_id: str)

class CompletionPredicate(ABC):
    @abstractmethod
    def evaluate(self, ctx: InvariantContext) -> bool: ...

class AnyOf(CompletionPredicate):       # at least one child satisfied
    children: list[CompletionPredicate]

class AllOf(CompletionPredicate):       # every child satisfied
    children: list[CompletionPredicate]

class HasSignal(CompletionPredicate):   # leaf — context contains a specific signal type
    signal_type: type[CompletionSignal]

# An action's registry entry uses these typed objects directly:

ActionRegistryEntry(
    action_id="flyer.billing.upgrade_plan",
    agent="flyer",
    domain="billing",
    intent="upgrade_plan",
    allowed_roles=frozenset({SenderRole.owner, SenderRole.authorized_requester}),
    required_account_state=frozenset({AccountState.trial, AccountState.active, AccountState.payment_pending}),
    forbidden_account_state=frozenset({AccountState.cancelled, AccountState.suspended}),
    required_payment_state=frozenset({PaymentState.paid, PaymentState.link_generated}),    # set membership, not string parse
    requires_confirmation=True,
    confirmation_token_format=re.compile(r"^CONFIRM UPGRADE [A-Z]+$"),
    success_copy_template_id="upgrade_plan_success",
    failure_copy_template_id="upgrade_plan_failure",
    clarification_copy_template_id="upgrade_plan_clarify",
    refusal_copy_template_id_by_reason={                                                    # enum-keyed, not string-keyed
        RefusalReason.no_payment_state: "upgrade_plan_refuse_no_payment",
        RefusalReason.wrong_role: "upgrade_plan_refuse_wrong_role",
        RefusalReason.wrong_state: "upgrade_plan_refuse_wrong_state",
    },
    deterministic_handler="apply-plan-change",
    completion_signal_required=AnyOf([                                                      # ADT, not string
        HasSignal(PaymentWebhookReceived),
        HasSignal(OperatorMarkedPaid),
    ]),
    audit_row_type=LogEntryType.regulated_action_executed,
)
```

**Why this matters (F4 rationale):** the four-part invariant in §2 demands four booleans computable for every action. String-expression fields like `"paid OR payment_link_generated"` push parsing to runtime, invite typos, and silently drift when the underlying enum changes. Typed predicates let the type system enforce that every action computes all four invariant booleans before merge time.

**Mandatory tests:**

- `test_action_registry_invariant_completeness.py` — for every registered action, construct a `InvariantContext` representative for each of `success / clarify / refuse-by-reason` and assert the four invariant booleans (`has_structured_intent`, `has_validated_authority`, `has_valid_state_transition`, `has_verified_action_result`) are deterministically computable.
- `test_action_registry_no_string_predicates.py` — static scan asserts no registry field is a free-form string for the predicate-typed fields (`required_payment_state`, `completion_signal_required`, `forbidden_account_state`, etc.).
- `test_action_registry_copy_template_existence.py` — every template ID resolves to a real copy-policy template.
- `test_action_registry_handler_existence.py` — every `deterministic_handler` resolves to a real script in `/usr/local/bin/` or `src/agents/<agent>/scripts/`.

**Where it lands:**

- `src/platform/action_registry.py` (new) — registry struct + per-agent registration calls.
- `src/agents/flyer/action_registry_entries.py` (new) — per-agent declarations.
- `src/agents/shift/action_registry_entries.py` (new) — same pattern.
- `src/agents/catering/action_registry_entries.py` (new).
- `src/agents/expense_bookkeeper/action_registry_entries.py` (new).
- Each declares its agent's regulated actions.

**Copy policy integration:** the registry references template names; actual copy lives in the agent's existing copy-policy module (`flyer_customer_copy_policy.py` already exists). PR C does NOT rewrite copy; it makes copy registry-driven.

**Tests:**

- Registry-shape validation per agent.
- Test that every regulated intent identified in §6 has a registry entry.
- Static check: every registry entry references a real copy-policy template + real deterministic-handler script.
- Round-trip test: classifier → router → registry-lookup → all four invariants computable.

## 11. PR D — Payment State Machine

**Scope:** the safety lock for money-moving actions. Forbid "processed / completed / upgraded / changed / confirmed / sent / approved" for billing/payment actions unless a deterministic completion signal has been verified.

**State shape** (extends `FlyerCustomer` payment status at schemas.py line 953):

```
PaymentState (per customer × plan-change-request):
  pending_intent          → operator/customer expressed intent, no payment artifact yet
  link_generated          → payment link/invoice exists, sent to customer
  awaiting_webhook        → customer claims paid, webhook not yet received
  paid                    → webhook confirmed OR operator marked paid
  cancelled               → customer or operator cancelled before payment
  refunded                → terminal — money returned
```

**Transitions:**

- `pending_intent` → `link_generated` (deterministic: link-generation script returned ok)
- `link_generated` → `awaiting_webhook` (customer says "paid" — but not promoted to `paid` yet)
- `awaiting_webhook` → `paid` (webhook received OR operator command `mark-paid #XXXXX`)
- `link_generated` / `awaiting_webhook` → `cancelled` (customer cancels)
- `paid` → `refunded` (operator command)

**Completion gate:**

The action registry's `completion_signal_required` (PR C) MUST be satisfied before the response template can emit a "completion" verb. Concretely: when a `billing.upgrade_plan` action runs, the deterministic handler MUST observe `payment_state IN {paid}` AND a `regulated_action_executed` audit row MUST be written WITH `result=success` BEFORE the success copy template fires.

Otherwise the handler emits the clarification/refusal copy template with explicit `"No plan change has been made. Your current plan remains <X>."`

**Audit-write-must-precede-success (revised per F5):**

The existing dispatcher policy ("if `log-decision-direct` exits non-zero, log to stderr but proceed with the delegation — the routing decision matters more than the audit entry") is the right policy for OBSERVABILITY audit rows (`dispatcher_routed`, `raw_inbound`). It is the WRONG policy for EVIDENCE audit rows on regulated-action execution. The four-part invariant treats the `regulated_action_executed` row as part of the "verified action result" evidence; if the row never lands, the evidence is missing and the system has no durable trail of the state mutation.

The rule for regulated actions specifically:

> **For any action whose registry entry has `audit_row_type=LogEntryType.regulated_action_executed`:**
> 1. The handler script computes the deterministic mutation result (state file write + any external API call return value). The handler MUST classify the mutation as either `local_reversible` (state-file write that can be undone via the existing rollback path) or `external_irreversible` (external API call already committed — Stripe charge, QBO push, WhatsApp send already returned ok). This classification is declared in the action registry entry as `mutation_class: Literal["local_reversible", "external_irreversible"]`.
> 2. The handler attempts to write the `regulated_action_executed` audit row via `safe_io.ndjson_append` (which writes to the canonical chokepoint).
> 3. **If the audit write fails (exception, disk full, lock contention, missing path):**
>    - The handler MUST NOT emit the success copy.
>    - **Rollback discipline depends on `mutation_class` (revised per F5-revision-Medium 2026-05-25 evening review):**
>      - **`local_reversible`:** rollback is **MANDATORY**. The handler MUST invoke the registered rollback (e.g. `apply-payment-state-transition --rollback`) before emitting any customer copy. If rollback itself fails, escalate to the `external_irreversible` path. Customer copy: `refuse_audit_unavailable` template with body *"I attempted to <action>, but I couldn't record the change durably. Your <prior state> is unchanged. The operator has been alerted."*
>      - **`external_irreversible`:** rollback is impossible. The handler MUST emit a DIFFERENT refusal template `refuse_audit_unavailable_external_committed` with body *"I attempted to <action>. The external step appears to have completed but I couldn't record it durably. The operator has been alerted and your account is under review. Do not retry until you hear back."* The customer copy must NOT say "unchanged" because the external state IS changed; saying "unchanged" would be a second false claim on top of the audit failure. The state is "under operator review."
>      - **Rollback path failure within `local_reversible` escalates to `external_irreversible` copy + escalation:** if the rollback script itself fails or partially succeeds, the handler escalates to the `external_irreversible` template and adds `rollback_failed=true` to the alert payload.
>    - The handler MUST trigger an operator alert via `notify-owner-with-fallback` AND `Pushover`-equivalent fallback (parse_mode=None per CLAUDE.md §12b lesson). The alert payload includes `mutation_class`, the rollback outcome (if `local_reversible`), and a link to the failed audit-row attempt.
>    - Emit `regulated_action_audit_failed` audit row to a SECOND independent log target (file-on-disk fallback `state/.audit-fallback.ndjson` + journalctl); this gives the operator a recovery trail even if the primary chokepoint is wedged.
> 4. Only after the `regulated_action_executed` row lands AND the state mutation succeeded AND any required external signal arrived (per `completion_signal_required` predicate evaluating to true on the new context) MAY the handler emit the success copy through `safe_io.bridge_post`.

**Action registry implication:** PR C's `ActionRegistryEntry` MUST include `mutation_class: Literal["local_reversible", "external_irreversible"]` as a required field. The §10 test `test_action_registry_invariant_completeness.py` is extended to verify every registered action declares this classification AND, for `local_reversible`, references a real rollback handler.

**Why this matters (F5 rationale):** the four-part invariant claims the audit row IS evidence. If the row can be silently absent while the success copy fires, the invariant is paper. The fail-closed-on-audit-failure rule makes the audit row's existence a precondition for any completion claim, which is what the invariant requires.

**Tests:**

- `test_regulated_action_audit_fail_closed.py` — fault-inject `safe_io.ndjson_append` to raise; assert the handler emits the refusal copy and does NOT fire the success copy.
- `test_regulated_action_state_rollback.py` — fault-inject audit failure AFTER a state mutation; assert rollback fires and the state file returns to the prior shape.
- `test_audit_fallback_log_written.py` — assert the second-target fallback log captures the failed audit attempt.

**Where it lands:**

- New `PaymentState` Pydantic enum in `src/platform/schemas.py`.
- New `FlyerPaymentStateMachine` class in `src/agents/flyer/payment_state.py`.
- New script `/usr/local/bin/apply-payment-state-transition` enforcing legal transitions + atomic writes via `safe_io.atomic_write_json`.
- LogEntry variants: `_PaymentStateTransitioned`, `_PaymentStateTransitionRejected`.
- Mock provider mode (`payment_provider="manual"` already exists at schemas.py line 778) keeps PR D skeleton-only — no live Stripe/Razorpay webhooks required for the first ship. Real-provider wiring is a follow-up, gated behind explicit operator + the §7c eval harness + §12 self-evolution loop signals.

## 12. PR E — Self-Evolution Loop With Teeth

**Scope:** the closing of the loop. Hermes converts production failures into new eval fixtures; the deploy gate (already shipped in PR-0 §7c) enforces no regression.

**Moved out per F6:** the seed corpus + harness infrastructure now lives in PR-0 (§7c) so PR A-D can be developed test-first against the regression corpus. What remains in PR E is the ongoing loop that grows the corpus over time from live production signals.

**Pipeline:**

1. Inbound message processed → `regulated_intent_firewall_decision` audit row written (PR A).
2. Periodic timer (`hourly`) runs `tools/cluster-recent-firewall-failures.py` — looks for clusters of low-confidence classifications, validator rejections, repeated clarification loops, copy lint rejections, regulated-action-audit-failed events.
3. Cluster passes threshold → Hermes generates a candidate eval fixture (proposed inbound + expected outcome). The fixture is constructed using the **existing** harness from PR-0 §7c; no new harness substrate.
4. Fixture file written to `tests/conversation_evals/proposed/` with audit row `eval_fixture_proposed`.
5. Codex worker (per existing `tasks/hermes-claude-codex-autonomous-architecture-2026-05-23.md` `worker_draft` mode) reviews + promotes to `tests/conversation_evals/seed/<agent>/` via PR.
6. Deploy gate (PR-0 §7c) blocks on regression — established once in PR-0, no per-PR rewiring needed.

**Autonomy ceiling:** `pr_ready` per the autonomous architecture doc. No autodeploy. Operator review on every fixture promotion. This is the "loop with teeth" — Hermes proposes, Codex implements, deploy gate enforces, operator approves.

**Regression rule (now the PR-0 deploy gate's responsibility):** A deploy is BLOCKED if any prior-failed fixture would fail again with the proposed code. This is CLAUDE.md §12a + §12b silent-failure-prevention applied at the conversation layer. The rule exists from PR-0 onward; PR E grows the fixture set.

## 13. Cross-cutting — Forbidden Completion Verbs (foundation reference)

**Note:** the primitive itself lives in PR-0 §7b (`src/platform/customer_copy_policy.py` + `lint_customer_copy()` + chokepoint enforcement via §7a). This section documents the cross-cutting contract for reference; the implementation slot is PR-0.

**The list** (verbs forbidden in customer-visible copy without a verified action result):

`processed`, `completed`, `upgraded`, `downgraded`, `changed`, `confirmed`, `sent`, `approved`, `paid`, `posted`, `pushed`, `applied`, `scheduled`, `booked`, `cancelled`, `refunded` (plus Telugu/Hindi/Tamil/Kannada/Malayalam translations per PR-0 §7b).

**Contract** (enforced by PR-0 §7b lint via §7a chokepoint):

- Every customer send through `safe_io.bridge_post` carries an optional `action_context: ActionExecutionContext | None`.
- If `action_context` is `None`, the send is treated as non-regulated (system messages, smoke); the lint passes through.
- If `action_context.is_regulated_action=True`, the lint requires `action_context.verified_action_result=True` for any forbidden-verb to pass. Otherwise the lint rejects, the bridge refuses to send, and an `customer_copy_lint_rejected` audit row lands.
- The single-chokepoint rule from PR-0 §7a means this lint reaches all 9 send paths, not just the canonical one. The static gate prevents future drift.

This is the **lowest-effort, highest-value** primitive in the whole architecture. It is the rationale for PR-0 shipping first — once the chokepoint + lint exist, even a half-finished PR A-D set has a structural defense against "I've processed your request to upgrade to Growth..." style replies.

## 14. Horizontal generalization — Flyer first, Shift/Catering second

**Reframed per R1+R1b (2026-05-25 — operator-confirmed Option C):** comprehensive portfolio architecture, NOT "Flyer-only-until-evidence." The portfolio shares VPS, Hermes gateway, cf-router, bridge send paths, audit substrate, and deployment gates — therefore the architecture is portfolio-wide. Two distinct layers:

### Layer 1 — Platform foundation (PR-0 / PR-0b). Mandatory, comprehensive, ships once.

All agents inherit. The chokepoint, lint, harness, audit discipline, `ActionExecutionContext` plumbing, null-context allowlist, `mutation_class` discipline, cf-router/gateway rule (generic LLM cannot claim regulated completion), and deployment gate apply uniformly across Flyer, Shift, Catering, Expense, Daily Brief, and the future 24 agents.

| Foundation piece | Why portfolio-wide |
|---|---|
| Single send chokepoint (§7a) | Currently 8 local `_bridge_post` helpers across Catering, Shift, Expense bypass the canonical chokepoint. Consolidation is genuine deduplication. |
| Forbidden completion/action-claim lint at chokepoint (§7b) | Cheap defense for any send. Catches false-completion in any agent's customer copy. |
| `ActionExecutionContext` plumbing + null-context allowlist (§7a #5–6) | Forces every send to declare regulated/unregulated status. Discipline portfolio-wide. |
| Eval harness + deploy gate (§7c) | Useful for any agent with a failure corpus, including operator-controlled bounded-smoke fixtures. |
| Audit fail-closed + `mutation_class` (§11) | Mechanism applies to every regulated_action_executed row across the portfolio. Catering proposal-accept = `local_reversible`; future Catering deposit = `external_irreversible`. |
| cf-router / gateway rule: generic LLM cannot claim regulated completion (§3, §8) | Same rule applies at the same chokepoints across all agents. |
| Regression corpus structure (`tests/conversation_evals/seed/<agent>/`) (§7c) | Per-agent fixture directory layout same for every agent. |

### Layer 2 — Per-agent regulated verticals (PR A-E). Repeatable pattern. Ships per-agent. Contents evidence-gated.

Same PR A-E architecture for every agent. What differs per agent is the EVIDENCE that finalizes the per-agent contents:

| Per-agent piece | Source of evidence |
|---|---|
| Regulated-intent surfaces (§6) | Per-agent declared, audited from agent's deployed flow + failure corpus |
| Active-block patterns (PR A) | Per-agent failure corpus + bounded-smoke evidence (Flyer = §18 Q5 24-pattern list; Shift/Catering finalize from their bounded-smoke per §15) |
| Semantic classifier Hermes skill (PR B) | Per-agent regulated surface (per CLAUDE.md anti-umbrella-skill rule) |
| Action registry entries (PR C) | Per-agent regulated actions + `mutation_class` declarations |
| Payment state machine (PR D) | Only required when agent has customer-side payment/external-irreversible mutations (Flyer yes; Catering future deposit yes; Shift/Expense no today) |
| Per-agent fixtures (§7c growth via PR E) | Per-agent failure corpus + smoke fixtures + operator screenshots |
| Smoke + live evidence gates (§15) | Per-agent execution, same gate shape across all agents |

### Landing order

| Vertical | Order | Why | Status |
|---|---|---|---|
| **Flyer** | first | Confirmed failure corpus (the "I've processed your request to upgrade to Growth" + ~100 PRs since 2026-05-15) | PR A-E scoped against Flyer corpus; active-block list operator-confirmed at §18 Q5 |
| **Shift** | follows audit-chokepoint verification + bounded smoke per §15 | Same PR A-E architecture; active-block/registry/fixtures finalized from Shift's smoke evidence | Pending Shift bounded smoke per §15 |
| **Catering** | follows audit-chokepoint verification + bounded smoke per §15 | Same PR A-E architecture; active-block/registry/fixtures finalized from Catering's smoke evidence | Pending Catering bounded smoke per §15 |
| **Expense** | follows Catering | Same PR A-E architecture; smoke-gated | Lower priority — no customer-facing money path today |
| **Daily Brief** | n/a | Read-only, no customer surface; inherits PR-0/PR-0b only | No PR A-E vertical needed |
| **Future 24 agents** | per agent, before production | PR A-E becomes the canonical production-onboarding gate for the portfolio | Will scope each from pre-production bounded smoke + audit evidence |

**Key discipline (per R1b):** the architecture is portfolio-wide. The CONTENTS of each per-agent vertical are evidence-gated. Do NOT copy Flyer's active-block list to Shift/Catering — finalize each agent's list from its own bounded-smoke + audit evidence per §15.

### Cost discipline

- **PR-0 / PR-0b** (foundation, ships once, all agents benefit): ~600–900 LOC. Chokepoint consolidation (8 local helpers) + `customer_copy_policy.py` + `ActionExecutionContext` schema + null-context allowlist + static gates + eval-harness infrastructure + initial seed corpus structure.
- **PR A-E for Flyer** (first vertical, evidence confirmed): ~1,800–2,500 LOC across firewall + classifier + registry + payment state machine + Flyer-specific fixtures.
- **PR A-E for Shift / Catering / Expense / future 24 agents**: per-agent budget once that agent's bounded-smoke evidence finalizes its active-block list + registry contents. Repeatable pattern; per-agent cost typically ~600–1,200 LOC (smaller than Flyer because most agents already have 5-char-code gates on regulated actions and won't need the full payment state machine).

## 15. Promotion criteria — Shift / Catering live customer-traffic resumption

Per operator direction 2026-05-25: Shift/Catering are blocked from broader live customer-traffic resumption until the control-layer minimum exists. Minimum is defined as (in order — earlier gates must close before later gates apply):

**Foundation (project-wide, shipped via PR-0b before any per-agent vertical — PR-0 alone is NOT sufficient per E1):**

1. **Single send chokepoint** enforced (PR-0b consolidates §7a) — `safe_io.bridge_post`, `safe_io.bridge_send_media`, `safe_io.bridge_send_cta` are the only customer-send functions in the source tree; static gate prevents drift; all 8 prior local `_bridge_post` helpers consolidated. Singularity + null-context allowlist tests both green (no `xfail`).
2. **Forbidden-completion-verbs lint active at all three chokepoint functions** (PR-0b promotes §7b) — `CUSTOMER_COPY_LINT_MODE=active`; covers text bodies + media captions + CTA labels + media filenames. `action_context=None` allowlist enforced.
3. **Eval seed corpus + harness blocking** (PR-0b promotes §7c) — `tests/conversation_evals/seed/<agent>/` populated with the per-agent failure corpus; `tools/run-conversation-evals.sh` wired into the deploy gate; `CONVERSATION_EVAL_GATE_MODE=block`.

**Operational verification (per-agent — applies to EVERY agent in the portfolio before broader live customer-traffic resumption, even before that agent's PR A-E vertical lands):**

4. **Audit chokepoint** verified per `feedback_audit_chokepoint_before_live_smoke.md` — confirm `safe_io.ndjson_append` writes land at a known canonical location with freshness/watchdog coverage. Per the 2026-05-25 review the canonical `/opt/shift-agent/decisions.log` was MISSING on main-vps — that gap MUST close before any operational step proceeds.
5. **Bounded smoke** per `feedback_pilot_readiness_vs_production_ready.md` — explicit test sender identity, expected audit rows for that agent's existing flow, cleanup contract, dry-run vs prod call-out. Smoke MUST include negative/vague regulated-intent tests (per agent type), NOT only deterministic happy paths. **Expected audit rows are per-agent and per-flow (correction per R1b-clarification 2026-05-25 evening):** do NOT require `regulated_action_executed` for Shift/Catering smoke unless the smoked path actually goes through PR C's action registry. For now, require:
   - **Universal** (all agents): `raw_inbound`, `dispatcher_routed`, `bridge_send_ok` or equivalent bridge outcome row.
   - **Plus PR-0b send/lint rows once the foundation lands**: `customer_copy_lint_evaluated` (when lint runs), `customer_copy_lint_rejected` (when lint rejects).
   - **Shift-specific** (existing flow rows): `proposal_created`, `proposal_status_change`, `candidate_responded`, `dispatcher_routed → handle_sick_call`.
   - **Catering-specific** (existing flow rows): `catering_lead_created`, `catering_lead_status_change`, `catering_menu_apply_pending`, `catering_menu_apply_committed`, `catering_proposal_*` (per current discriminated-union variants in schemas.py).
   - **Flyer-specific** (when Flyer PR A-E lands): `regulated_intent_firewall_decision`, `regulated_action_executed`.
   - **Expense-specific** (existing flow rows): `expense_lead_created`, `expense_status_change`, `apply_expense_decision_*`.
6. **Per-agent eval fixtures** (per agent's relevant failure corpus) green in `tests/conversation_evals/seed/<agent>/`. For Flyer, this is the §7c failure corpus + the 24 active-block patterns (one fixture each). For Shift/Catering, fixtures are seeded from each agent's bounded-smoke evidence + existing failure history (Shift: routing-correctness fixtures; Catering: state/routing fixtures).
7. Existing `pilot-readiness-check --text` PASS (static gate).
8. **Recent live-traffic evidence per Q6 (operator-confirmed 2026-05-25 evening): 14 days per agent**. At least ONE real regulated-intent or near-regulated-intent customer round-trip must land all expected audit rows from gate 5. If no organic traffic occurs in 14 days, use a bounded operator-controlled smoke instead and label it explicitly as "smoke evidence" — NOT "customer evidence."

**Per-agent vertical (PR A-E for an agent — applies only after gates 4–8 are green for that agent AND the agent has the confirmed failure corpus):**

9. **PR A (Regulated Intent Firewall)** shipped for the agent with `<AGENT>_FIREWALL_ACTIVE_BLOCK=enabled` for the operator-confirmed active-block patterns + `<AGENT>_FIREWALL_SHADOW_MEASURE=enabled` for the broader semantic surface. Active-block list is per-agent and operator-confirmed (Flyer: §18 Q5 24 patterns; Shift/Catering: scoped from gate 5 bounded-smoke evidence).
10. **PR C (Action Registry) entries** declared for every regulated-intent surface enumerated in §6 for that agent (scoped from the same evidence as gate 9). `test_action_registry_invariant_completeness.py` passes for the agent's actions.

**The split is:**

- Gates **1–3** are foundation, shipped via PR-0b, apply portfolio-wide.
- Gates **4–8** are operational + smoke + traffic, applied per-agent, run on the agent's own production-readiness track — these are what Shift/Catering need NEXT (independent of PR A-E).
- Gates **9–10** are per-agent PR A-E specifics, scoped from gate-5 bounded-smoke evidence. Flyer's gate-9 active-block list is already confirmed (§18 Q5); Shift/Catering's gate-9 lists are scoped after their gate-5 smoke completes.

**Live customer-traffic resumption rule:**

- Shift/Catering can resume broader live customer traffic once gates 1–8 are green for that agent. Gates 9–10 (PR A-E for that agent) are a separate program that runs in parallel based on the agent's own evidence.
- Flyer requires all 10 gates because the failure corpus is the trigger for gates 9–10.
- Future 24 agents must clear gates 1–10 before production launch (gates 9–10 scoped from each new agent's pre-production smoke evidence).

## 16. Pause policy

Per operator direction 2026-05-25:

- **PAUSE:** small phrase-fix PRs (regex patches, keyword adders, one-off copy tweaks) except critical production blockers.
- **CONTINUE:** existing in-flight Flyer recovery work (the `codex/flyer-full-autonomous-recovery` lane + `codex-flyer-autodev-main.timer`) until operator explicitly redirects it. Per `feedback_flyer_isolation_during_shift_catering.md` the Flyer lane is not touched as part of this architecture work.
- **NEXT:** PR-0 (foundation) ships first, then PR A-E as one architectural set landing per-vertical. Operator decides whether to redirect the `codex-flyer-autodev-main.timer` to draft individual slices from this doc or to keep it on its current recovery work while a human-driven PR-0 ships separately.

## 17. Non-goals

- No deletion of existing deterministic routing in PR A. Existing matrix remains as fallback.
- No removal of cf-router's existing routing. The firewall is additive — but the cf-router contract is strengthened per F3 so generic LLM cannot speak on regulated-intent text.
- No Hermes-direct state writes. Hermes proposes; deterministic code validates and acts.
- No blanket-shadow defaults. Per F2 revision: the high-confidence active-block patterns ship fail-closed from day one; only the broader semantic surface stays shadow. The doc no longer claims "every per-agent mode flag defaults to shadow or off."
- No string-expression DSLs in the action registry. Per F4 revision: typed predicates everywhere; tests prove the four invariant booleans are computable.
- No "audit-write failure is soft" policy for regulated actions. Per F5 revision: regulated-action audit failure fails the success path; the audit row IS evidence.
- No PR A-D landing before the eval seed corpus exists. Per F6 revision: PR-0 ships the harness, PR-0b promotes it to blocking with the seeded corpus; PR A-D each merge only with their fixtures green.
- No PR A merge before PR-0b lands. Per E1 revision: PR-0 (scaffold) is not the safety foundation; PR-0b is. Hard merge gate enforced by `tests/test_safety_foundation_load_bearing.py`.
- No `action_context=None` sends from regulated pipeline code. Per E2 revision: `None` is allowed only from the `SAFE_IO_NULL_CONTEXT_ALLOWLIST`; any regulated-pipeline call site passing `None` is a runtime error.
- No "completion claim in caption" escape. Per E3 revision: the lint covers media captions and CTA labels equally with text bodies.
- No "alert and call it unchanged" copy for `external_irreversible` actions whose audit failed. Per E5 revision: the customer copy must say "under operator review" and forbid retry, not "unchanged."
- No copying Flyer's active-block list to Shift, Catering, Expense, or future agents. Per R1b: each per-agent active-block list, action registry, and fixture set is scoped from THAT AGENT's bounded-smoke + audit evidence per §15 gate 5. The architecture is portfolio-wide; the contents are evidence-gated.
- No new agent reaching production without clearing §15 gates 1–10 for that agent. Per R1b: PR A-E becomes the canonical production-onboarding gate for every new agent in the 2026-05-04 portfolio expansion (+24 future agents).
- No Shift/Catering broader live customer-traffic resumption blocked on a Flyer-specific PR A-E vertical. Per R1b clarification: Shift/Catering's immediate path is §15 gates 4–8 (audit chokepoint + bounded smoke + 14-day traffic) on their own track; their per-agent PR A-E vertical lands as a separate program based on their evidence.
- No autodeploy expansion. `autodeploy` remains disabled for all SMB agents per the autonomous-architecture doc.
- No customer-visible Hermes-drafted copy without copy-policy lint passing through the §7a chokepoint.
- No replacement of the existing `pilot-readiness-check`. The new criteria in §15 ADD to the existing gate; do not replace.
- No new substrate. Reuse `safe_io`, `log-decision-direct`, dispatcher, Hermes gateway, identify-sender, cf-router, mode-flag pattern. The architecture is one large extension of existing patterns, not a parallel framework.

## 18. Open questions for operator

**Resolved 2026-05-25 evening (operator answers folded in):**

1. ✅ **Redirect the autonomous-loop timer?** — **RESOLVED: keep `codex-flyer-autodev-main.timer` on its current Flyer recovery lane for now.** PR-0/PR-0b will be human-driven. Per `feedback_flyer_isolation_during_shift_catering.md`, the Flyer recovery autonomous lane stays untouched while PR-0/PR-0b ship in a separate branch off `origin/main`.
2. ✅ **Seed corpus origin** — **RESOLVED: seed from VPS state + recent `codex/flyer-*` branches + `tasks/lessons.md` 2026-05-15+ entries + operator screenshots.** That is the corpus authority for PR-0 §7c. The PR-0 implementation pulls from these four sources in that order.
3. ✅ **Per-agent classifier consolidation** — **RESOLVED: keep per-agent.** One Hermes skill per agent's regulated surface, per the CLAUDE.md anti-umbrella-skill rule + Hermes auto-curator silent-regression memory.
4. ✅ **PR D scope on first ship** — **RESOLVED: state machine + transitions + completion gate + audit-fail-closed (per F5 + E5), with `manual` provider stub. Real Stripe/Razorpay webhook wiring deferred to a follow-up PR.**
5. ✅ **Flyer active-block patterns — RESOLVED (2026-05-25 evening, operator-confirmed).** The Flyer PR A initial active-block list is the following **24 patterns**:

   - Billing: `upgrade to <plan>`, `downgrade to <plan>`, `change plan`, `change my plan`, `move me to <plan>`, `I want the <plan> plan`, `start <plan>`, `switch to <plan>`, `cancel my plan`
   - Payment: `refund`, `I paid`, `mark paid`, `processed my payment`
   - Account: `change phone`, `change my phone number`, `change WhatsApp number`, `change business name`, `change address`
   - Delivery state: `where is my flyer`, `did you send my flyer`, `send my flyer`, `approve`, `I approve`, `send now`

   These active-block ONLY into deterministic clarify/refuse/route behavior — never claim completion. Broader semantic detection stays in shadow mode.

   **Per R1b (portfolio framing):** this 24-pattern list is **for Flyer only**. Shift/Catering need their own active-block lists scoped from their gate-5 bounded-smoke evidence (per §15) — do NOT copy this list to other agents.

6. ✅ **Promotion-criteria threshold for §15 gate 8 — RESOLVED (2026-05-25 evening).** Operator-confirmed: **14 days per agent**. Stricter rule: at least one real regulated-intent or near-regulated-intent round-trip must land all expected audit rows for that agent. If no organic traffic occurs in the window, use a bounded operator-controlled smoke instead and label it explicitly as "smoke evidence" — NOT "customer evidence."
7. ✅ **Branch + commit ownership — RESOLVED (2026-05-25 evening).** Operator-confirmed: cut a clean branch off `origin/main` for docs/implementation work — `git checkout -b docs/regulated-intent-control-layer origin/main`. Do NOT commit unless explicitly approved. Stage edits, do not push, do not open PR until operator asks.

**Implementation greenlight status (operator 2026-05-25 evening):** ✅ **PR-0 then PR-0b only, human-driven, with Flyer recovery lane left untouched. PR A waits until the active-block list is confirmed (now done at Q5) AND PR-0b is load-bearing.**

**All open questions resolved.** Doc is implementation-ready pending one more verification pass after the R1+R1b reframe lands.

## 19. Cross-references

- Operator vision sources (verbatim in conversation 2026-05-25): the two messages diagnosing the Flyer `"I've processed your request to upgrade to Growth..."` regression and proposing the PR A-E shift.
- Memories: `project_regulated_intent_firewall_vision.md` (the vision itself), `feedback_pilot_readiness_vs_production_ready.md`, `feedback_audit_chokepoint_before_live_smoke.md`, `feedback_flyer_isolation_during_shift_catering.md`, `feedback_provenance_before_deletion.md`, `feedback_no_auto_commit_this_repo.md`, `feedback_dont_overengineer_llm_intent.md`, `feedback_hermes_skills_landscape.md`, `feedback_hermes_first.md`, `feedback_drift_rules.md`, `feedback_runtime_state_verification.md`.
- Project docs: `tasks/flyer-hermes-intent-operating-layer-backlog-2026-05-22.md` (H0 source, generalized here), `tasks/hermes-claude-codex-autonomous-architecture-2026-05-23.md` (autonomy modes + role split), `docs/hermes-alignment.md` Parts 1+2 (deployed-pattern checklist), `docs/portfolio.md` (Solid 17 agents).
- CLAUDE.md sections directly invoked: Hermes-first rule, drift rules, §9 runtime-state verification, §10 discipline-as-heuristic, §12a freshness/watchdog, §12b operator-state-reversal alerts.

---

**END.** Awaiting operator review. No code, no branch, no commit per `feedback_no_auto_commit_this_repo.md`.
