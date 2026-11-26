# Fable agent improvements — plan (2026-11-25)

Operator mandate (2026-11-25): "Start working on all your proposals, full
autonomous mode. Operator is stepping away." Priority: catering, shift, other
agents; flyer deprioritised. Nothing here is committed, pushed or deployed
without an explicit operator ask.

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:** none (no store, router, scheduler, state
machine, approval mechanism or notification system, and no model call).

## Session bootstrap

- Universal directive: `docs/governance/engineering-directive.md` v1.2.0
- Shared-platform directive: `docs/governance/shared-platform-directive.md` v1.1.0
- Affected projects: `shift-platform` (shared: `src/plugins/cf-router/**`,
  `src/platform/schemas.py`), `catering-studio`
  (`src/agents/catering/scripts/apply-catering-owner-decision`), `shift-agent`
  (behavioural owner of the coverage loop)
- New subsystem proposed: no. Architecture exception: none.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Multilingual yes/no reply classification | none found (closest: a Korean/English chat router) | use the Hermes LLM gateway pattern already deployed in cf-router |
| Scripted journey / eval harness | possible lead `hermes-eval` plugin, unverified | reuse the in-tree e2e harness; no new harness |
| Outbound hallucinated-action screening | none found (little-canary screens inbound only) | already built in-tree; nothing to add |
| Shift scheduling / coverage | none found | existing in-tree kernel |

awesome-hermes-agent ecosystem check: list exists; two catalog pages were
truncated when read, so the negative result is not exhaustive. Verdict: no
ecosystem skill replaces any step; all work extends deployed in-tree code.

### Hermes-first per-step checklist

| Step | Tag | Effort |
|---|---|---|
| 1. Employee replies on WhatsApp in any language | `[Hermes]` multi-channel inbound | 0 |
| 2. Sender identity + single `sent` proposal lookup | `[Hermes]` identity gating + existing `sent_proposal_ids_for_candidate` | 0 |
| 3. Plain English YES/NO recorded | `[Hermes]` deployed deterministic classifier, unchanged | 0 |
| 4. Owner paged for replies the classifier cannot record | `[net-new]` deterministic flag-gated arm quoting the sanitised reply (no model) | ~150 + tests |
| 5. Page delivery and flood bound | `[Hermes]` existing `fire_pushover_alert` + `was_recently_alerted` | 0 |
| 6. Audit row via chokepoint | `[Hermes]` `audit_intercepted` (one Literal value) | ~7 |
| 7. Owner `#CODE` reply resolved to a verb | `[net-new]` guard: only a clear approve/reject acts; otherwise one fixed owner reprompt | ~80 + tests |
| 8. Owner approve renders quote from frozen pricing | `[net-new]` guard: refuse when frozen guest count differs from current headcount | ~40 + tests |
| 9. Catering journeys through existing e2e harness | `[net-new]` test-only characterization journeys | tests only |
| 10. Outbound screen false refusals | `[Hermes]` already built: `verified_action_result` exemption + owner exemption (#798) | 0 — closed, no build |

## Decisions

1. **Shift arm is a deterministic owner page; the model call was cut.** The
   first build added a model call to label the reply. Scope review showed the
   verdict gated nothing (every verdict paged), so it was removed along with
   its budget plumbing and env knobs. `accepted` and `declined` are terminal in
   `LEGAL_TRANSITIONS`, so the arm changes no state and sends nothing to the
   employee. Dormant behind `SHIFT_CANDIDATE_REPLY_OWNER_PAGE` +
   `SHIFT_CANDIDATE_REPLY_OWNER_PAGE_CHATS`. Bound: one page per proposal per
   5 minutes. Known residual: the 30-minute no-response sweep still fires.
   Graduation trigger: 7 days armed for an allowlist with zero duplicate or
   flood pages, then widen to `*`.
2. **Owner verbs: only a clear approve or reject acts.** A wrong approve is an
   irreversible customer send. An unclear decision gets one fixed reprompt.
3. **Headcount mismatch fails closed at approve**, reusing the existing
   exit-11 refusal + owner reprompt.
4. **Outbound screen: build nothing.** Drift check found the mechanism exists.
   Residual (not built): `bridge_post` reports `sent` after a text swap, and
   nothing alerts on `front_brain_outbound_refused`.
5. **Not built, need operator ruling:** hold blocking finalize; multi-lead
   binding; approve bound to card version; concurrent double approve;
   per-language candidate ack templates (employee-facing send).

## Branches (one per task, all off `origin/main` c2274cf1)

- `feat/shift-multilingual-candidate-reply`
- `fix/cf-router-f8-conflicting-owner-verbs`
- `fix/catering-approve-headcount-basis-mismatch`
- `test/catering-journey-characterization`

## Drift-rule self-checks

- ✅ Read `src/plugins/cf-router/hooks.py` (candidate classifier 380-601, F9 and candidate arms 794-861, `_try_f8_intercept` 1649-1790) before drafting the routing changes
- ✅ Read `docs/governance/engineering-directive.md` and `docs/governance/projects/shift-agent.md` before deciding the probabilistic/deterministic boundary
- ✅ Read `docs/governance/shared-platform-directive.md` before touching `src/plugins/**`
- ✅ Read `src/plugins/cf-router/actions.py` via research pass (amendment discriminator 2066-2300, candidate helpers 510-600) before specifying the adapter
- ✅ Read `src/platform/schemas.py` via research pass (`LEGAL_TRANSITIONS` 4225-4244, `CfRouterIntercepted` 6992) before the audit decision

## Verification

- Each branch: red→green on new tests; touched test files run on Linux
  (docker `python:3.11-slim`) because the POSIX-only suites skip on Windows.
- Independent review per branch with distinct lenses (structural; money
  boundary; "is this scope needed"), then at most two fix cycles.

## Review

2026-11-25 end of session. All four branches are uncommitted working-tree
changes on top of c2274cf1; nothing pushed or deployed.

- Verification is Windows-only. Docker's Linux engine would not start (two
  locked stale socket files under `%LOCALAPPDATA%\Docker\run`), so the
  POSIX-gated suites ran under a shim (fake `fcntl`, `platform.system()` forced
  to Linux). That is logic evidence, not Linux evidence. The five tests in
  `tests/e2e/test_catering_journeys_characterization.py` have never executed.
- One test fails under the shim on untouched main as well
  (`TestF7PrimaryMode::test_branch_b_active_lead_capture_failure_sends_retry_not_canonical`).
- Review cycle 1 (structural, safety boundary, scope): no blocker. Fixes
  applied: model call removed from the shift arm; page flood bound; reply
  sanitising; owner-verb rule tightened to "nothing but an approve" plus a
  fixed reprompt; headcount refusal text made truthful. Cycle 2 not run.
- Open, need operator ruling: item-only quotes scaled by headcount are not
  covered by the headcount gate; hold does not block finalize; newest-lead
  binding with several open leads; approve not bound to the card version;
  concurrent double approve; a way for the owner to record a candidate's
  answer; `bridge_post` reports `sent` after a text swap.
