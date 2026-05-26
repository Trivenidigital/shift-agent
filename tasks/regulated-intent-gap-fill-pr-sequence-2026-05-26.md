**Drift-check tag:** extends-Hermes

# Regulated-Intent Gap-Fill PR Sequence — 2026-05-26

## Context

- **Architecture doc** (portfolio direction + safety contract, NOT implementation map): `tasks/regulated-intent-control-layer-architecture-2026-05-25.md`
- **Drift-check finding D1** (2026-05-26): ~50–60% of per-agent Flyer infrastructure already on `origin/main` (shipped by `codex-flyer-autodev-main.timer`). See Drift-correction note in the architecture doc for the module inventory.
- **This doc** is the current implementation map. Status board at the bottom; update as PRs land.
- **Working branch:** `docs/regulated-intent-control-layer` off `origin/main`, in worktree `C:/projects/sme-agents-regulated-intent`. No commits without explicit operator approval.

## Sequencing rule

PRs land in order **α → β → γ → δ → ε → ζ → η → θ**. α and β together close the customer-visible generic-fallback risks; γ adds the cheap lint shim across all sends; δ adds the typed-discipline field; ε–θ are the platform-foundation pieces. Order is partially driven by dependency, partially by smallest-high-value-first.

---

## PR-α — Regulated-intent regex gap-fill (billing/payment/account)

**Order: 1st (active).**

| Aspect | Detail |
|---|---|
| Files | `src/plugins/cf-router/actions.py`, `tests/test_cf_router_flyer_routing.py` |
| Patterns added to `_FLYER_REGULATED_ACCOUNT_PATTERN` | verb-anchored account changes: `(change|update|set|edit|modify|remove|delete)\s+(?:my\|the\|our)?\s*(?:flyer\|business\|account\|public\|contact)?\s*(?:phone(?:\s+number)?|address|email|number)` |
| Patterns added to `_FLYER_REGULATED_PAYMENT_PATTERN` | `i\s+(?:have\|just\|already)?\s*paid` + `mark(?:ed\|ing)?\s+(?:as\s+)?paid` |
| Phrases that must fail closed (positive tests) | `I paid`, `I have paid`, `I just paid`, `I already paid`, `mark paid`, `marked paid`, `marked as paid`, `cancel my plan` (already caught via `plan` keyword — add explicit test), `change phone`, `change my phone number`, `update my phone`, `change address`, `change my address`, `update address` |
| Phrases that must NOT match (false-positive guards) | `Create a flyer with our phone number 555-1234`, `Make a poster showing our address`, `Design includes email for inquiries`, `Show the paid plans on the flyer`, `Highlight the paid section` |
| Non-goals | NO chokepoint consolidation; NO `ActionExecutionContext`; NO lint module changes; NO new audit row types; NO cf-router routing-shape changes (the existing `_try_flyer_regulated_account_guard` already handles the routing — α only extends the patterns it matches against) |
| Dependency | None |
| Risk | False positives from broader patterns (e.g. "I paid attention to your last flyer" would match the new `i paid` pattern). Mitigation: false-positive cost is a clarification message ("No plan, payment, or account change has been made"), not a destructive action — acceptable per the four-part invariant ("may clarify or refuse, but cannot claim completion") |
| Known deferred follow-up | `src/plugins/cf-router/actions.py:1830 find_active_flyer_project_by_sender` has its own phone-required early return (`if not phone or not FLYER_PROJECTS_PATH.exists(): return None`). For LID-only customers (phone=None), the function returns None even when a project exists. PR-α's yield logic is forward-compatible (passes `phone=None` through without adding a second gate) but the upstream fix to make project-lookup chat_id/primary_chat_id aware for LID-only senders is **out of scope of PR-α**. Recurring lesson in `tasks/lessons.md` line 92-95. File as PR-α.1 follow-up after PR-α merges. |

## PR-β — Delivery-state guard

**Order: 2nd.** Customer-visible generic-fallback risk #2 — completion claims about flyer delivery.

| Aspect | Detail |
|---|---|
| Status | **IN PROGRESS 2026-05-26** — scope verified, implementation underway on branch `docs/regulated-intent-control-layer` |
| Files | `src/plugins/cf-router/actions.py`, `src/plugins/cf-router/hooks.py`, `src/platform/schemas.py` (+1 line for new `flyer_delivery_state_guard` reason in `CfRouterIntercepted`), `tests/test_cf_router_flyer_routing.py` |
| Confirmed in-scope phrases | `where is my flyer`, `did you send my flyer`, `send my flyer`, `approve` (bare), `I approve` |
| Confirmed deterministic ownership (verified) | `is_flyer_approval_text` (`actions.py:1358`) + active-project intercept (`hooks.py:2700, 2718`) own bare `approve` + `I approve`. `is_flyer_project_status_request` + active-project intercept own `where is my flyer` / `did you send` style queries. |
| Deferred to PR-β.1 | `send now` — no specific deterministic handler found in active-project path. Defer rather than add a new handler mid-PR-β. |
| Pattern shape (NOT bare-token per #250 lesson) | `_FLYER_DELIVERY_STATE_PATTERN` uses tight phrase patterns: `where(?:'s\|\s+is)\s+(?:my\s+\|the\s+)?flyer`, `did\s+you\s+send\s+(?:me\s+\|us\s+)?(?:my\s+\|the\s+)?flyer`, `send\s+(?:me\s+\|us\s+)?(?:my\s+\|the\s+)?flyer`, `\bi\s+approve\b`. Bare `approve` handled via the existing `is_flyer_approval_text` semantics (entire-body equality after stripping). |
| New function | `is_flyer_delivery_state_intent(text)` |
| Wired into | `_try_flyer_delivery_state_guard()` in `cf-router/hooks.py`, runs **AFTER** `_try_flyer_active_project_intercept` (not BEFORE per the original draft). Reasoning: active-project intercept already handles all delivery-state phrases when a project (active or closed_no_send) resolves; PR-β fires only when no project resolves and the message would otherwise reach generic Hermes. **No yield logic needed** in PR-β — placement-after means no hijack risk. |
| Behavior | When no active or recent flyer project resolves for the sender, fail-closed clarification copy explicitly says "No delivery action has been taken" and "I don't see an active or recent flyer for [business] to deliver right now." NEVER claims "I sent it" / "your flyer is done." |
| Phrases that must fail closed (with no active project) | All 5 in-scope phrases above |
| Phrases that must NOT match | `Where can I show my flyer to customers?` (where + can ≠ where + is), `approve this concept` (not bare approve), `send to customers Friday` (send + to ≠ send + flyer), `Did you receive my flyer?` (receive ≠ send), `I approve of the colors` (allowed broader match by design — fail-closes harmlessly per the four-part invariant) |
| Non-goals | NO chokepoint, NO lint, NO action-registry changes, NO active-project yield helper needed (placement-after handles it). The existing `_try_flyer_active_project_intercept` is NOT modified. |
| Dependency | None structurally; sequenced after α as agreed |
| Basis from closed PR #250 | **3 of 19 seed fixtures** in `codex/regulated-intent-pr0-foundation` branch are PR-β scope: `regulated_delivery_did_send.json`, `regulated_delivery_send_my_flyer.json`, `regulated_delivery_where.json`. May cherry-pick when PR-η builds the eval harness. DO NOT lift #250's broader cf-router regex changes wholesale. |
| Discipline inherited from #251 | (a) Tight phrase-anchored regex (no bare-token matching like #250); (b) False-positive negative tests REQUIRED in the test file alongside positive cases; (c) LID-only test for the no-active-project guard path (no second phone gate); (d) Active-project case tested via the dispatch-order integration check (placement-after means guard never sees active-project cases). |

### PR-β.1 — `send now` delivery-state intent (deferred)

`send now` (and possibly `send it now`, `please send`, etc.) has no specific deterministic handler in the active-project intercept today. Adding one mid-PR-β would expand scope. Deferring to a follow-up PR-β.1 once the deterministic ownership question is resolved (either: existing active-project intercept's approval path is extended to cover `send now`, OR a dedicated send-now route is added).

## PR-γ — Forbidden-completion-verbs lint in customer_copy_policy.py

**Order: 3rd.** The cheap shim that prevents fake-completion claims across ALL agents' customer copy.

| Aspect | Detail |
|---|---|
| Files | `src/agents/flyer/customer_copy_policy.py` (extend), `tests/test_flyer_customer_copy_policy.py` |
| New constant | `FORBIDDEN_COMPLETION_VERBS = frozenset({"processed", "completed", "upgraded", "downgraded", "changed", "confirmed", "sent", "approved", "paid", "posted", "pushed", "applied", "scheduled", "booked", "cancelled", "refunded"})` |
| New API | `lint_no_unverified_completion(text: str, has_verified_action_result: bool = False) -> tuple[bool, list[str]]` — returns `(passed, violations)`. When `has_verified_action_result=False`, presence of any verb in the frozenset is a violation. When `True`, lint passes regardless. |
| Wired into | Existing `scan_customer_text` and `scan_outbound_entry` functions; lint runs alongside the existing internal-term / project-ID checks; new `CustomerCopyHit` category `unverified_completion_verb` |
| Tests | Positive: each verb triggers a violation when `has_verified_action_result=False`. Negative: same verbs pass when `has_verified_action_result=True`. Round-trip: `scan_outbound_entry` on a synthetic entry with "I have processed your upgrade" returns the violation. |
| Non-goals | NO chokepoint consolidation (that's PR-ε); NO `ActionExecutionContext` plumbing (that's PR-ζ); the lint runs but is not yet wired to refuse sends — that wiring lands in PR-ζ. PR-γ ships the LINT FUNCTION; PR-ζ uses it. |
| Dependency | None |
| Basis from closed PR #250 | `src/platform/customer_copy_policy.py` (+95 new file on `codex/regulated-intent-pr0-foundation`) is ~80% of what PR-γ needs. Already includes `FORBIDDEN_COMPLETION_VERBS` tuple (17 verbs), `lint_customer_copy(text, action_context) -> CopyLintResult` API, `_find_forbidden_verbs` + `_normalise_action_context` helpers. Plus `tests/test_customer_copy_policy.py` (+72) and the minor `src/agents/flyer/account.py` "confirmed → verified" copy adjustment. **Note:** #250 also defines `ActionExecutionContext` Pydantic model in this file — split decision deferred to PR-ζ (keep colocated or lift to a dedicated module). PR-γ's scope ends at the lint module + tests. Lift content via cherry-pick from the closed branch. |

## PR-δ — `mutation_class` field on `FlyerActionDefinition`

**Order: 4th.** Per E5 (rollback discipline depends on knowing whether the mutation is reversible).

| Aspect | Detail |
|---|---|
| Files | `src/agents/flyer/action_registry.py`, `tests/test_flyer_action_registry.py` (new file) |
| Schema change | Add `mutation_class: Literal["local_reversible", "external_irreversible"]` to `FlyerActionDefinition` as a required field |
| Annotations on existing 11 actions | `status`/`help`/`plan_menu` → `local_reversible` (read-only); `starter_prompt_mode`/`update_business_name`/`add_authorized`/`remove_authorized`/`update_phone`/`update_whatsapp` → `local_reversible` (file write only); `change_plan` → `external_irreversible` (Stripe/Razorpay/manual charge is external) |
| Tests | Schema completeness (every entry declares `mutation_class`); enum values are valid; the dataclass refuses missing `mutation_class` argument |
| Non-goals | NO rollback handler wiring; NO audit-fail-closed behavior (that's PR-ζ + a later wiring PR); PR-δ ships the FIELD; later PRs USE it |
| Dependency | None |

## PR-ε — Single send chokepoint consolidation

**Order: 5th.** Largest PR in the sequence (~200 LOC consolidation across 8 scripts).

| Aspect | Detail |
|---|---|
| Files | `src/platform/safe_io.py` (no signature change yet — signature changes in PR-ζ), 8 agent scripts: `src/agents/catering/scripts/{apply-catering-owner-decision,create-catering-lead,finalize-catering-menu,create-catering-proposal-options,select-catering-proposal,send-catering-ack}`, `src/agents/expense_bookkeeper/scripts/apply-expense-decision`, `src/agents/shift/scripts/send-coverage-message`. Plus new `tests/test_send_chokepoint_singularity.py`. |
| Change pattern (per script) | Replace local `def _bridge_post(...)` with `from safe_io import bridge_post as _bridge_post`. Preserve any per-script logging/audit-row behavior at the call sites — only the HTTP POST + retry shape changes. |
| Tests | `test_send_chokepoint_singularity.py`: greps source tree for `def\s+_?bridge_post\s*\(` + `def\s+_?bridge_send_media\s*\(` + `def\s+_?bridge_send_cta\s*\(` — asserts the only match is `src/platform/safe_io.py`. Per-script smoke tests that the new import-based call site still works. |
| Logrotate invariant | `safe_io.bridge_post` already uses open-append-close (no fd cache). DO NOT add any fd cache or "performance optimization" in this PR. Add a code comment marking the invariant. |
| Non-goals | NO `ActionExecutionContext` (PR-ζ); NO lint hookup (PR-ζ); NO null-context allowlist (PR-ζ); PR-ε ONLY does the consolidation. The chokepoint signature stays unchanged. |
| Dependency | None structurally. Coordinate with the codex automation timer to avoid merge conflicts on the 8 scripts. |
| Risk | Each script touches money-moving or audit-emitting paths. Per-script regression risk is real. Mitigation: each script consolidation lands as a separate commit within PR-ε so revert is per-script. |

## PR-ζ — `ActionExecutionContext` + null-context allowlist + lint hookup

**Order: 6th.** Brings together PR-γ (lint) + PR-ε (chokepoint) into the full F1+E2 contract.

| Aspect | Detail |
|---|---|
| Files | `src/platform/schemas.py` (new `ActionExecutionContext` Pydantic model), `src/platform/safe_io.py` (extend `bridge_post`, `bridge_send_media`, `bridge_send_cta` signatures to accept `action_context: ActionExecutionContext \| None`; add `SAFE_IO_NULL_CONTEXT_ALLOWLIST` frozenset + runtime caller check via `inspect.stack()`; call `lint_no_unverified_completion` from PR-γ before each HTTP POST), `tests/test_send_chokepoint_null_context_allowlist.py`, `tests/test_action_execution_context_schema.py` |
| `ActionExecutionContext` shape | `action_id: str`, `is_regulated_action: bool`, `verified_action_result: bool`, `audit_row_id: str \| None`, `mutation_class: Literal["local_reversible", "external_irreversible"] \| None` |
| Allowlist | `SAFE_IO_NULL_CONTEXT_ALLOWLIST = frozenset({"shift-agent-health-check.sh", "send-daily-brief", "eod-reconcile", "shift-agent-notify-owner", ...})` — explicit enumeration of scripts that may legitimately send with `None` context. |
| Runtime check | If `action_context is None` AND caller-script is NOT in the allowlist, emit `regulated_send_missing_action_context` audit row and refuse the send. |
| Static gate | `tests/test_send_chokepoint_null_context_allowlist.py` greps callers + asserts each either passes non-null `action_context` OR is on the allowlist with a justification comment. |
| Tests | Action-context flow (regulated action passes context, gets through lint when `verified_action_result=True`, blocked when `False` + has forbidden verb); allowlist enforcement (non-allowlisted caller with `None` is refused); lint refusal (forbidden verb without verified action result is refused). |
| Non-goals | NO mass call-site updates — existing callers can pass `None` initially as long as they're on the allowlist; per-script call-site migration happens in follow-up PRs once the schema is stable. |
| Dependency | PR-ε (chokepoint consolidation must be done first so the signature change reaches all callers), PR-γ (lint function must exist). |
| Basis from closed PR #250 | `src/platform/safe_io.py` (+33 on `codex/regulated-intent-pr0-foundation`) already ships the `bridge_post` signature extension to accept keyword `action_context: object \| None` + the `_lint_bridge_customer_copy` helper that calls `lint_customer_copy` from `customer_copy_policy.py`. Also `src/platform/schemas.py` (+10) and `tests/test_safe_io_bridge_post.py` (+55). **NOT in #250:** the `SAFE_IO_NULL_CONTEXT_ALLOWLIST` frozenset + runtime caller check via `inspect.stack()` + the static-gate test `test_send_chokepoint_null_context_allowlist.py`. PR-ζ adds those on top of the #250-derived signature plumbing. The `ActionExecutionContext` model lift/colocate decision is also made in PR-ζ. |

## PR-η — Conversation eval harness scaffold

**Order: 7th.**

| Aspect | Detail |
|---|---|
| Files | New: `tests/conversation_evals/` directory structure (`seed/flyer/`, `seed/shift/`, `seed/catering/`, `seed/expense/`, `proposed/`), `tools/run-conversation-evals.sh`, `tests/conversation_evals/seed/flyer/billing_001_upgrade_to_growth.json` (and ~30 more seed fixtures) |
| Fixture shape (JSON per fixture) | `{ "inbound_text": "...", "sender_role": "...", "agent": "flyer", "expected_classification": {...}, "expected_response_class": "success\|clarify\|refuse", "expected_audit_rows": [...], "expected_forbidden_verb_violations_if_any": [...] }` |
| Seed corpus origin | Per operator Q2: `/opt/shift-agent/logs/decisions.log` (canonical audit log) for Flyer failure rows + recent `codex/flyer-*` branch commit messages + `tasks/lessons.md` 2026-05-15+ + operator screenshots, in priority order. Initial seed: ~30 Flyer fixtures including the operator's 24-pattern active-block list (1 fixture per pattern). |
| Wired into | `src/agents/shift/scripts/shift-agent-deploy.sh` as a pre-deploy gate. Initial mode: `CONVERSATION_EVAL_GATE_MODE=warn` (logs failures, does not block deploy). Promoted to `block` once the corpus is fully seeded — likely in a follow-up PR-η.b. |
| Tests | Harness self-test (runs against an empty corpus, exits clean); harness against a known-failing fixture (exits non-zero); harness against a known-passing fixture (exits zero). |
| Non-goals | NO self-evolution loop (clustering, automatic fixture proposal — that's a much later PR after PR-θ); NO promote-to-block automatic (operator-triggered). |
| Dependency | None structurally. |
| Basis from closed PR #250 | **PR #250's harness work is ~90% of PR-η.** Already shipped on `codex/regulated-intent-pr0-foundation`: `tools/run-conversation-evals.py` (+123, Python harness with `--agent flyer` flag, exit-non-zero on failure), `tools/run-conversation-evals.sh` (+4, shell wrapper), `tests/test_conversation_evals.py` (+53, harness self-tests), and **19 seed fixtures** under `tests/conversation_evals/seed/flyer/`. The 19 break down as: 12 account (`regulated_account_*`), 4 payment (`regulated_payment_*`), 3 delivery (`regulated_delivery_*` — those 3 belong to PR-β per the Basis row above). Lift via cherry-pick from the closed branch. Operator's verification at #250 head `700ab88` showed harness `19 failed=0` — code works. |

## PR-θ — Audit-log freshness watchdog

**Order: 8th.** The §12a gap identified during Path B verification (2026-05-25).

| Aspect | Detail |
|---|---|
| Files | New: `tools/check-decisions-log-freshness.sh`, `src/agents/shift/systemd/decisions-log-freshness-watchdog.{service,timer}` units. Extend: `src/platform/schemas.py` (new `_DecisionsLogStaleWarning` + `_DecisionsLogStaleAlert` `LogEntry` variants), `src/agents/shift/scripts/shift-agent-deploy.sh` (install the unit + assert presence). |
| Canonical audit log path | `/opt/shift-agent/logs/decisions.log` (per `reference_audit_chokepoint_canonical_path.md` memory) |
| Freshness threshold | Warn at 15 min idle, alert at 60 min idle. Configurable via env in the timer unit. |
| Alert path | `notify-owner-with-fallback` + Pushover with `parse_mode=None` (per CLAUDE.md §12b lesson). |
| Tests | Synthetic stale-condition test (touch the file, fake-advance time, assert watchdog fires); alert format test (assert alert text passes the `customer_copy_policy.py` lint — no unverified completion verbs in operator alerts either). |
| Non-goals | NO external monitoring integration (Grafana, Prometheus). NO audit-chain verification (signed-hash trail is in CLAUDE.md as a deferred compliance item; PR-θ only checks freshness, not integrity). |
| Dependency | None structurally; benefits from PR-γ lint being available for the alert-copy assertion. |

---

## Status board (update as PRs land)

| PR | Title | Branch | Status |
|---|---|---|---|
| α | Regulated-intent regex gap-fill | `docs/regulated-intent-control-layer` (merged + deleted) | **MERGED 2026-05-26 — PR [#251](https://github.com/Trivenidigital/shift-agent/pull/251), squash commit `6e0ffeb`** |
| α.1 | LID-only `find_active_flyer_project_by_sender` upstream fix | — | pending — out of PR-α scope; tracked in PR-α row "Known deferred follow-up" |
| α.2 | GitHub PR checks absent | — | pending — see "Follow-ups" section below |
| (250) | Codex PR-0 scaffold attempt | `codex/regulated-intent-pr0-foundation` | **CLOSED 2026-05-26 (unmerged)** — PR [#250](https://github.com/Trivenidigital/shift-agent/pull/250). Closed as superseded after PR-α merged because #250's account regex used bare tokens (`address`, `whatsapp`, `phone number`) that would have reintroduced false-positives PR-α explicitly tests against. Content decomposed into PR-β / γ / ζ / η below. Branch kept as cherry-pick source. |
| β | Delivery-state guard | — | pending α (now unblocked — α merged); MUST inherit #251 verb-anchor + active-project-yield discipline |
| γ | Forbidden-completion-verbs lint | — | pending β |
| δ | `mutation_class` field | — | pending γ |
| ε | Send chokepoint consolidation | — | pending δ |
| ζ | `ActionExecutionContext` + allowlist + lint hookup | — | pending ε |
| η | Eval harness scaffold | — | pending ζ |
| θ | Audit-log freshness watchdog | — | pending η |

## Follow-ups (not in the main sequence)

### PR-α.2 — GitHub PR checks absent

PR #251 merged on `mergeStateStatus: CLEAN` + local verification only. GitHub `statusCheckRollup` was empty — no Actions workflow ran on the PR. Repo has been relying on local verification successfully but the CI gap is real.

Investigate:
- Whether the repo has any Actions workflows configured at all
- If yes, whether they trigger on `docs/*` / `fix/*` branches or only on `main` pushes
- If yes, whether path filters exclude the touched files
- Minimum useful coverage: run `pytest tests/test_cf_router_flyer_routing.py` (and the broader flyer test files) on every PR open + push

Out of scope for the regulated-intent gap-fill program; tracking here so future PR reviews against this sequence don't have to re-derive the gap. Not blocking PR-β / γ / ...

---

## Non-goals across the whole sequence

- NO deletion of existing Flyer modules (`intent.py`, `action_registry.py`, `payment_state.py`, `customer_copy_policy.py`, `intent_training.py`).
- NO Flyer recovery lane interference (`codex-flyer-autodev-main.timer`, `codex/flyer-full-autonomous-recovery` branch, related `codex/flyer-*` PRs all stay untouched).
- NO live deploy without explicit operator request.
- NO autodeploy expansion (operator approval required for every deploy).
- NO commit unless explicitly approved.
- NO horizontal generalization of PR-α / PR-β patterns to Shift/Catering — per R1b, per-agent active-block lists are scoped from each agent's own bounded-smoke evidence, NOT copied from Flyer.

## Cross-references

- `tasks/regulated-intent-control-layer-architecture-2026-05-25.md` (portfolio direction + safety contract)
- `tasks/flyer-hermes-intent-operating-layer-backlog-2026-05-22.md` (H0 source backlog, now partially shipped as `intent.py`)
- `tasks/hermes-claude-codex-autonomous-architecture-2026-05-23.md` (autonomy modes; PR work is `worker_draft`/`pr_ready` ceiling)
- Memories: `project_regulated_intent_arch_doc_state.md`, `feedback_drift_check_module_names_before_architecture.md`, `reference_audit_chokepoint_canonical_path.md`, `feedback_flyer_isolation_during_shift_catering.md`, `feedback_no_auto_commit_this_repo.md`
