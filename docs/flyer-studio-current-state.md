**Drift-check tag:** Hermes-native (documents shipped state; adds no infrastructure)

# Flyer Studio — Current State (Single Source of Truth)

**As of `origin/main` `f1ff0cb` · 2026-05-30 · deployed `deploy-20260530-030230-7e524c2e` (main-vps, smoke-green).**

This document is the **single source of truth** for Flyer Studio status. The
scattered backlog docs (`tasks/todo.md`, `tasks/flyer-studio-production-readiness-backlog.md`,
and the various `tasks/flyer-*-backlog-*.md`) have **repeatedly lagged shipped
code** — the same decisions kept getting re-litigated because docs read like
live work after the code shipped. **Treat those backlog docs as historical.
Verify against `origin/main` (not backlog docs) before proposing any Flyer work.**

## Hermes-first analysis
This is a documentation consolidation — no code, no infrastructure. Hermes owns
the runtime substrate; Flyer Studio is built around it. Drift tag: **Hermes-native**.

---

## TL;DR

- Customer-safe Flyer code is **production-ready and deployed** (smoke-green).
- **No bounded, customer-safe Flyer code work remains** (verified across all
  prioritized areas + deferral markers, multiple independent surveys 2026-05-29/30).
- Remaining work is **operator/credential** + one **non-bounded architectural**
  effort. Neither is a code slice an autonomous builder should "find" by
  re-surveying — they require operator action or explicit authorization.

---

## Settled decisions — DO NOT re-open

| Decision | Status | Evidence |
|---|---|---|
| Provider split: OpenRouter for generation, OpenAI for source edits | settled in code | `render.py:1524` `_openrouter_image_bytes`, `render.py:1736` `_openai_source_edit_bytes`, `schemas.py:916-920` `draft/final/edit_image_model`. Full breakdown: `tasks/flyer-edit-provider-backlog-2026-05-30.md`. |
| "Near production ready" = customer-safe flyer behavior, NOT operator-dependent integrations (Stripe/QBO/payments) | settled | scope decision |
| Deterministic routing stays primary; Hermes intent contract runs shadow | settled in code | `intent.py` (`deterministic_baseline_decision` + `run_classifier_shadow`) |

---

## SHIPPED (verified file:line) — do NOT re-survey these

| Area | Status | Evidence |
|---|---|---|
| Production-readiness P0-1 … P0-7 | ✅ shipped | per-area below |
| Per-state customer status replies | ✅ | `hooks.py` `_select_flyer_status_reply` + state→reply table (enforced by `test_flyer_state_reply_table.py`) |
| Golden scenario suite | ✅ | `tests/test_flyer_golden_scenarios.py` (+ spend-gated `_real_model` variant) |
| Hermes intent contract + safety validator | ✅ (shadow) | `intent.py` `FlyerIntentDecision`/`validate_flyer_intent_decision`/`deterministic_baseline_decision`; `tests/test_flyer_intent_layer.py` |
| Autonomous repair loop | ✅ | `tests/test_flyer_autorepair.py` (classifier + `hard_stop` + trust-fact-mutation rejection); `autorepair_attempts.json` ledger |
| Cockpit P0-3 (format previews + dimensions + hashes) | ✅ | `_asset_summary` → `output_format`/`width`/`height`/`sha256`/`file_sha256`/`media_url`; `/projects/{id}/assets/{aid}` serve |
| Cockpit P0-4 (timeline read model) | ✅ | `manual_queue_detail_action` `timeline` + `_audit_timeline` |
| Cockpit P0-5 (customer-visible message preview) | ✅ | `/manual-queue/{id}/action-preview` (`test_flyer_admin_close_no_send.py`) |
| Cockpit P0-6 (close/no-send UI) | ✅ | `/manual-queue/{id}/close-no-send` (`test_flyer_admin_close_no_send.py`) |
| Cockpit P0-7 (provider/runtime health panel) | ✅ | `/flyer/health` (`test_flyer_health.py`) |
| Starter briefs / example prompts | ✅ | `starter_briefs.py` + `test_flyer_starter_briefs.py`. **Caveat:** the conversational **guided-intake flow** (`intake.py`) is built but **dormant — it has never fired live.** See "Vague-request handling — write-up correction" below before citing intake as a shipped capability. |
| Edit-fidelity regressions (F0023/F0024/F0029) | ✅ | `test_cf_router_flyer_routing.py`, `test_flyer_golden_scenarios.py` |
| Recovery lane | ✅ deployed | `recovery.py` + `flyer-recovery-watchdog` (smoke-passing) |
| Send-time format truthfulness + downgrade observability | ✅ | PRs #339, #351 |
| Same-business revision routing | ✅ | PR #348 (closed #341) |
| Campaign-scene prompt templates | ✅ | PR #353 (`campaign_scene_prompts.py`) |

This session shipped + deployed PRs **#339, #348, #351, #353** and de-drifted the
edit/provider backlog (**#362**).

---

## Genuinely remaining work

### A. Operator / credential (NOT code — an autonomous builder cannot close these)
1. `OPENAI_API_KEY` on main-vps → enables the (built) OpenAI source-edit path. Without it the code correctly routes to `manual_edit_required`.
2. Hermes OCR skill enabled on main-vps → enables (built) reference-menu extraction.
3. Spend-gated source-edit visual-quality smoke (5–10 cases) → the one quality gate before treating automated source edits as customer-grade.
4. Spend-gated real-model golden eval (`test_flyer_golden_scenarios_real_model.py`, skipped) → final pre-broad-launch confidence gate.

### B. Non-bounded architectural (needs explicit operator authorization + a plan first)
1. **Intent shadow→active rollout / scattered-heuristics consolidation.** The
   canonical intent contract exists and runs in shadow; the cf-router routing is
   still a large set of deterministic lexical heuristics (the documented
   fallback). Collapsing those into the contract as the *authoritative* path is
   **multi-PR, high blast radius** (cf-router routing — the area that took 7
   Codex rounds in a single slice). NOT a bounded slice; do not attempt
   incrementally without a plan + explicit go.
2. `operating_layer.py` activation — currently advisory/dead-scaffolding
   (imported only by tests). Wiring it into runtime is a rollout decision.

---

## Vague-request handling — write-up correction (2026-07-06)

*Added 2026-07-06 as the "architecture confirmation #4 / roadmap write-up correction owed"
item from the Settlement Census (`tasks/settlement-census-2026-07.md`, verdict D11). This
section corrects a long-standing overstatement: that Flyer Studio resolves vague/underspecified
briefs via a live **guided-intake** flow. It does not. Verified against `origin/main` `e908c39`
and the box decisions log the same day.*

**The bright-line rule (operator standing rule, 2026-07-04):** the customer's brief is
NEVER rewritten, enriched, or gap-filled before extraction — *vagueness resolves by ASKING,
never by inference.* The sole documented exception is the interpretive `occasion` field
(mood-only, parity-exempt, fail-neutral).

**Who actually owns "asking" today:** the **sample-prompts menu**, not the conversational
guided-intake flow.

| Mechanism | Code | Live status (verified 2026-07-06) | Verdict |
|---|---|---|---|
| **Sample-prompts menu** (self-serve example briefs on explicit menu-request text) | `src/plugins/cf-router/hooks.py:881` `_try_flyer_sample_prompt_request_intercept` → emits `flyer_sample_prompt_requested` (`:933`/`:988`) | **LIVE — the named owner.** 8 `flyer_sample_prompt_requested` rows in the decisions log (= 5 real fires + 3 shadow-classifier rows, per census A8); 0 misfires; dedup verified | **PROVEN** (census A8) |
| **Guided-intake conversational flow** (adaptive-language + missing-field Q&A) | `src/agents/flyer/intake.py:77` `handle_intake_message` (962 LOC); sole caller `src/agents/flyer/scripts/handle-flyer-intake` | **DORMANT — never fired live.** 0 intake-session fire markers in the decisions log; **546** `flyer_intake_bypassed` rows (routing consistently skips it); `intake.py` emits no audit rows, so it has no fire marker at all | **KEEP-DORMANT** (census D11) |

**Why keep a dormant module:** guided intake is the *designated* mechanism for the bright-line
rule above — deleting it orphans the rule with no replacement Q&A surface (sample-prompts hands
out examples; it does not interview for missing fields). The census kept it in-tree pending
productization. It is ~962 LOC and rebuildable if a future rollout deletes it instead.

**Honest roadmap item:** before guided intake may be described as a live capability anywhere,
it must (a) be reachable in production routing (546 bypass rows show it currently is not) and
(b) emit a fire-marker audit row at entry so "has it ever fired" is answerable from the log.
Until then: **sample-prompts is the live vague-request surface; guided intake is dormant scaffolding.**

**⚠ Directive-vs-tree discrepancy (flagged, not silently resolved):** a 2026-07-06 operator
directive referred to guided intake as *"now DELETED."* That is **not** what the tree shows.
`origin/main` `e908c39` still contains `src/agents/flyer/intake.py` (962 LOC) **and** its sole
caller `src/agents/flyer/scripts/handle-flyer-intake` — both present, imports intact. The
authoritative census verdict is **KEEP-DORMANT** (D11), and the deployed tree matches KEEP-DORMANT,
not deletion. This write-up therefore states the tree truth (dormant, present). If a later
PR-D-ladder step actually removes the module, update this section at that commit.

---

## How to use this doc (stop the thrash)

1. **This is the source of truth for Flyer Studio status.** The backlog docs are stale.
2. **Before proposing any Flyer slice, verify the gap against `origin/main`** — not against `tasks/todo.md` or the production-readiness backlog. Every "open" item surveyed in 2026-05-29/30 turned out shipped.
3. **Do not re-open settled decisions** (table above).
4. **Do not manufacture low-value code changes.** If no bounded gap proves out against `origin/main`, the correct outcome is: hand Flyer off / switch domains, or unblock an operator item (A), or authorize the architectural effort with a plan (B).
