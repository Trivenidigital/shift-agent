# Production-Readiness Sprint — Adapted Plan (2026-07-18)

**Drift-check tag:** `extends-Hermes` — the sprint is predominantly read-only audit +
tests following deployed conventions; any remediation code extends existing chokepoints
(safe_io, decisions.log, dispatcher matrix, outbound screens). No new storage, no new
audit paths, no new approval generators.

**New primitives introduced:** none by default. Gap fixes must reuse existing
primitives; any exception requires its own mini-plan with this same header.

**Source:** operator brief "SME-Agents Production Readiness Sprint" (2026-07-18),
adopted for **principles only**. The brief's literal bindings (skill names like
review-responder/ad-copywriter, Hello2India, docker, Telegram approvals, Sonnet/Gemini
per-skill pins, "CLAIMS GATE" as a new pattern) do not match this repo and are
**explicitly replaced** below. Operator confirmed: "apply these principles to our
agents, not word for word."

## Hermes-first capability checklist

Per-step tagging of the sprint's end-to-end flow (receipt:
`tasks/.hermes-check-receipts/production-readiness-sprint.json`):

| # | Step | Tag | Net-new LOC |
|---|---|---|---|
| 1 | Enumerate fleet (16 agents / 33 SKILLs) + carry census verdicts | `[Hermes]` — SKILLs-as-scripts + per-VPS state; read-only | 0 |
| 2 | Record each skill's dispatch path (matrix row / cf-router / timer) | `[Hermes]` — skill dispatch by sender_role+media_type+content; read-only | 0 |
| 3 | Query decisions.log audit coverage per external action | `[Hermes]` — audit chain via log-decision-direct; read-only | 0 |
| 4 | Check approval-gate wiring on irreversible actions + claims screening on free-text sends | `[Hermes]` — approval workflows + role gating; read-only audit | 0 |
| 5 | Phase 0 matrix doc + tier assignments; STOP for approval | `[net-new]` — analyst doc | 0 (doc) |
| 6 | Shared pytest fixtures (roster/menu/lead/expense/brief) | `[net-new]` — test scaffolding, mirrors existing conftest style | ~100–150 |
| 7 | Structural tests, Tier A/B (schema/output/garbage-input) | `[net-new]` — tests only | ~300–500 |
| 8 | Hostile-fixture tests, Tier A riskiest surfaces | `[net-new]` — tests only | ~200–300 |
| 9 | Smallest-diff gap wiring (missing audit row / approval gate / claims screen) | `[net-new]` — wiring only, through existing chokepoints; budget-capped | ≤150 total |
| 10 | Tier C smoke on fixture inputs | `[Hermes]` — run the SKILL/script | 0 |
| 11 | KEEP/DEPRECATE calls for zero-traffic tail | `[net-new]` — judgment doc | 0 (doc) |
| 12 | Full suite + DEFERRED.md for unrelated debt | `[Hermes]`/convention — pytest+CI exist | 0 |
| 13 | DEPLOY_CHECKLIST.md + HUMAN_PUNCHLIST.md | `[net-new]` — operator docs | 0 (doc) |

Red-flag check: 6/13 net-new, but all net-new is tests + analyst docs except step 9,
the single bounded product-code surface (≤150 LOC or re-plan). No substrate rebuilt.

## Drift-rule self-checks

- ✅ Read `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` (routing matrix rows 86–93: `#XXXXX` code-match rows in state-file priority order) before defining the matrix's dispatch-path column
- ✅ Read `src/platform/safe_io.py` (atomic_write_json:249, ndjson_append:316, and `_refuse_prod_write_under_pytest`:283 — EVERY safe_io write chokepoint refuses `/opt/shift-agent` paths under pytest) before scoping step-9 wiring and Phase-1 fixture design; all fixtures MUST route writes to tmp paths or they fail loudly by design
- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (install_artifacts flat-module layout, per-module rollback guards, smoke gate + auto-rollback) before drafting DEPLOY_CHECKLIST scope
- ✅ Read `tasks/audits/feature-liveness-census-2026-07.md` (fleet-anomalies table: dispatcher near-zero, brief 98.6% skip, alert spam, QA skip flag) before scoping Phase 0 as a delta
- ✅ Read `tests/test_flyer_audit_remediation_clusterA.py` (SourceFileLoader plugin-import pattern, parametrized adversarial fixtures) and `tests/test_catering_proposal_skill_md.py` (SKILL.md structural assertions) before proposing test conventions

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Skill dispatch/routing | yes — dispatcher matrix + cf-router F7 primary-mode, live | audit coverage, do not rebuild |
| Approval workflows | yes — `#XXXXX` codes, TTL, dead-man escalation, live | audit per-agent wiring only |
| Audit trail | yes — decisions.log via safe_io/`log-decision-direct` chokepoint | audit per-agent coverage; no new logs |
| Outbound claim/fact safety | yes (in-tree) — flyer fact-safety QA, wrong-brand blockers, outbound lint, front-brain screen | audit which free-text sends bypass all screens; wire gaps into EXISTING screens |
| Model routing | yes — ONE global default + task-type overrides (recorded taxonomy); no per-skill pins | verify live config matches recorded strategy; do NOT build pinning |
| Test harness | yes (in-tree) — pytest subprocess-invoke + file-mutation conventions, GitHub CI | follow conventions; shared fixtures mirror existing conftest |
| Deploy/rollback | yes (in-tree) — tarball deploy + smoke gate + auto-rollback + pin gate | reference, don't rebuild |
| Liveness/verdict data | yes (in-tree) — census 2026-07-11, pilot-readiness-check, flyer audit cycle | Phase 0 is a DELTA, not a fresh audit |

awesome-hermes-agent ecosystem check: no external skills/deps needed — the sprint
consumes only in-tree machinery. Verdict: everything the source brief calls "build/wire"
already exists here in some form; the sprint measures coverage and closes gaps with the
smallest possible diff.

## Principles adopted from the source brief (kept in spirit)

1. Investigation before implementation — Phase 0 read-only, stop for operator approval.
2. Never rebuild what the substrate provides — extended to in-tree machinery.
3. Complexity budget — prefer delete/consolidate/configure; prompt-change over code.
4. Custom code only for: durable audit, reversibility, structured invariants,
   policy-boundary enforcement — and only on money/external-state/customer-visible paths.
5. Local first; no VPS deploy inside the sprint; deploy is a separate operator-gated step.
6. Tier-appropriate rigor; do not gold-plate Tier C.
7. Human-only blockers tracked separately from code blockers.
8. Source-text-is-truth where an agent consumes operator/customer briefs (flyer already
   enforces this; audit others).
9. Three-file operator interface as definition of done.

## Explicit divergences from the source brief

| Brief says | This plan does | Why |
|---|---|---|
| Skills: invoice-agent, review-responder, ad-copywriter… | Real fleet: 16 agents / 33 SKILLs (catering 8, shift 5, flyer 3, expense 3, multi_location 2, compliance 2, + 10 single-skill) | those skills don't exist here; review-responder is backlog gated on MCP (portfolio.md:1024) |
| Catering customer "Hello2India" | Triveni (live box config, verified in deploy smoke 2026-07-18) | deployed reality |
| CLAIMS GATE (new two-layer pattern) | Coverage audit of EXISTING fact/claims machinery; wire gaps into existing screens | sweep-for-preexisting-X rule; flyer fact-safety QA is the hardened reference implementation |
| Sonnet/Gemini per-skill pins | Verify live routing matches recorded strategy (global default + task-type overrides) | per-skill pins don't exist as a mechanism; strategy is a recorded decision |
| Telegram approval flow | WhatsApp `#XXXXX` approval codes | deployed reality |
| Docker services in deploy checklist | tarball + systemd + smoke gate + auto-rollback | deployed reality |
| Fresh 34-row audit | Delta on census 2026-07-11 + pilot-readiness-check + flyer audit | census already has per-subsystem verdicts + approvals log |
| flyer-designer dedup vs Flyer Studio v2 | n/a — flyer IS Flyer Studio; freshly audited + remediated (PR #621, deployed 2026-07-18) | flyer is the quality bar, excluded from re-audit |

## Tier definitions (assignments are a Phase 0 OUTPUT, evidence-based)

- **Tier A — money / external-state / customer-visible sends:** structural tests green,
  claims/fact screening verified with hostile fixtures on free-text sends, every external
  action audited in decisions.log (§12a/§12b compliant), approval gate on irreversible
  actions, smoke-tested. Expected members: catering, expense_bookkeeper, cash_ar,
  eod_reconcile, shift (employee-facing sends), sales_tax, commerce paths. Flyer already
  meets this bar (2026-07 audit cycle) — carried as reference, not re-worked.
- **Tier B — owner/customer-deliverable content, no money/external state:** structural
  tests green, one quality spot-check, claims screening only if output asserts factual
  business claims. Expected: daily_brief, catering_followup, vip, hiring, compliance.
- **Tier C — internal utility:** runs clean on a fixture input; nothing more. Expected:
  multi_location, pnl_anomaly, equipment_maintenance, inventory, supplier, employee_docs.

"Majority production ready" = all Tier A green, all Tier B green, Tier C smoke-tested.

## Phases

**Phase 0 — Audit delta (read-only; STOP for operator approval).**
Produce `tasks/audits/production-status-2026-07.md`: one row per skill — agent/skill,
purpose, proposed tier, dispatch path, external actions + audit/approval coverage,
claims-screen coverage for free-text sends, tests exist/pass, census verdict carried
forward, verdict (READY / NEEDS-FIX / UNTESTED / DEPRECATE-CANDIDATE), human-only
blockers. Census's open fleet anomalies (shift dispatcher near-zero, daily-brief 98.6%
skip, health-check spam, SLA alert spam, `FLYER_BARE_SKIP_VISUAL_QA=1`) get explicit
carry-forward rows — they are production-readiness blockers, not side notes.

**Phase 1 — Fixtures + structural tests (Tier A/B).**
Shared fixture set mirroring existing conftest style; every fixture writes ONLY to tmp
paths (safe_io's `_refuse_prod_write_under_pytest` guard fails any /opt/shift-agent
write loudly — this is load-bearing test infrastructure, not a suggestion). Per Tier
A/B skill: input schema respected, output format asserted, no unhandled exception on
empty/garbage input. Conventions: subprocess-invoke + file-mutation asserts for
scripts; in-process for pure functions; SKILL.md structural assertions
(test_catering_proposal_skill_md.py is the template); SourceFileLoader pattern for
plugin modules (test_flyer_audit_remediation_clusterA.py is the template).

**Phase 2 — Tier A hardening.**
Hostile fixtures per agent on its riskiest surface (catering: money floors / deposit
quotes / apostrophe-class inputs; expense: dedup + threshold + undo-window; shift:
coverage-blast bounds; cash_ar: dunning tone + amounts-from-state-only). Verify every
external action writes an audit row and irreversible ones sit behind an approval code.
Add ONLY missing wiring, smallest diff, through existing chokepoints (≤150 LOC budget).

**Phase 3 — Untested tail + deprecation calls.**
Single-skill tail (vip, supplier, inventory, equipment_maintenance, pnl_anomaly,
employee_docs, hiring, sales_tax, multi_location, cash_ar, catering_followup, compliance)
gets its tier treatment plus an explicit KEEP / DEPRECATE-CANDIDATE recommendation
grounded in census liveness data. No deletions without operator sign-off.

**Phase 4 — Validation + operator interface.**
Full local suite green at tier; unrelated debt → `tasks/DEFERRED.md`, not fixed.
Deliverables: updated `production-status-2026-07.md`, `tasks/DEPLOY_CHECKLIST.md`
(tarball build → scp → staging-new → pin-override deploy → smoke → rollback tag;
env-var deltas; systemd units), `tasks/HUMAN_PUNCHLIST.md` (credentials, paperwork,
operator decisions — e.g. Pushover un-mute, coverage rehearsal, deposit template, QA
skip-flag intent). Deploy itself: separate operator-authorized step.

## Non-goals

No Hermes upgrade or core-patch changes; no touching gecko-alpha/Vizora; no flyer rework
(fresh from audit cycle); no new frameworks/rewrites/speculative features; no per-skill
model routing; no deploy within the sprint; no cross-VPS anything.

## Approvals log

- 2026-07-18: operator directed "apply these principles to our agents" (session
  dd4a8de7) — authorized drafting this plan. Phase 0 execution: PENDING operator
  approval of this plan. Phases 1–4: gated on Phase 0 matrix approval.
