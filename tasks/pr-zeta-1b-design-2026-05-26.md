# PR-ζ.1b — design doc (REV 3 — post-build-halt callsite re-enumeration)

**Drift-check tag:** `extends-Hermes` (adds safety-rail context propagation on top of Hermes `bridge_post` substrate).

**Hermes-check receipt:** `tasks/.hermes-check-receipts/pr-zeta-1b-design-2026-05-26.json` (written 2026-05-26T20:28:00Z).

**Authoritative plan:** `tasks/pr-zeta-1b-plan-2026-05-26.md` (REV 2).

**Why REV 3 exists:** At build phase, the first 3 rows of REV 2 §2.1 were found to reference a hallucinated enclosing function (`_apply_flyer_account_onboarding`) — grep returned 0 matches. Per operator constraint #5 ("halt with file:line evidence if semantics unclear"), build was halted. Operator directed full re-enumeration from enclosing-function inspection, not grep-adjacent inference. Lesson captured at `tasks/lessons.md` 2026-05-26 entry. REV 3 contains the rebuilt callsite tables from three parallel forensic walks (one per scope), each cross-validated by `grep '^def <name>'`.

**Build status:** RESOLVED. Operator §13 decisions captured 2026-05-26 (see §13 final answers). All §2.1, §2.2, §2.3 rows resolved. Commit-1 augmented for new registry entries + helper signature change (LANDED locally, 20/20 tests pass). Ready to resume build from commit 3.

---

## Hermes-first capability checklist (per-step `[Hermes]` / `[net-new]` table)

(Preserved from REV 2. No new behaviors introduced in REV 3 — REV 3 is corrections to enumeration tables, not new scope.)

| # | Step / behavior | Tag |
|---|---|---|
| 1 | HTTP POST to local bridge for outbound WhatsApp text | `[Hermes]` |
| 2 | Discriminated-union audit-row write | `[Hermes]` |
| 3 | Pydantic v2 + `extra="forbid"` model | `[Hermes]` |
| 4 | `Literal` enum on `FlyerActionDomain` | `[Hermes]` |
| 5 | Final deploy via tarball + rsync | `[Hermes]` |
| 6 | `PROJECT_ACTIONS` registry (19 entries) | `[net-new]` |
| 7 | `build_action_context()` + `build_action_context_for_command()` helpers | `[net-new]` |
| 8 | `SAFE_IO_NULL_CONTEXT_ALLOWLIST` basename fix + removal of cf-router entries | `[net-new]` |
| 9 | Required `action_context` kwarg on `send_flyer_text` + 5 ack-style functions (hooks.py-resident wrappers handled per §13.F) | `[net-new]` |
| 10 | ~86 callsite migrations | `[net-new]` |

---

## Drift-rule self-checks

REV 3 evidence rows (all post-walk verifications):

- ✅ Read `src/plugins/cf-router/hooks.py` (3,451 lines post-import-insertion) and walked each of 52 `send_flyer_text(` callsites with enclosing-function inspection. Every enclosing function name cross-validated by `grep '^def <name>' src/plugins/cf-router/hooks.py` returning exactly 1 match. See §2.1.
- ✅ Read `src/plugins/cf-router/hooks.py` and walked each of 26 wrapper-invocation callsites. See §2.2.
- ✅ Read `src/plugins/cf-router/actions.py` (4,349 lines) at lines 3635-3900 + 4030-4060; verified all 7 chokepoint callsites and 1 def-signature change site. Function name `send_flyer_concept_previews` (NOT `_finalize_flyer_concept_previews` as REV 2 stated) confirmed by grep. See §2.3.
- ✅ Read `src/agents/flyer/action_registry.py` (479 lines after commit-1 edits in worktree).
- ✅ Read `src/platform/safe_io.py:863-900` (`bridge_post` def) to confirm `is_regulated_action` semantic: True → lint runs on message; False → pass-through.
- ✅ Read `tests/test_flyer_action_registry.py` + `tests/test_flyer_project_actions.py` — 18 tests pass; no regressions.

---

## §1 — corrections to the compaction-summary + REV 2 mental model

1. **The 5 functions at `actions.py:3640, 3674, 3715, 3734, 3752` call `safe_io.bridge_post(chat_id, message)` directly** — NOT wrappers of `send_flyer_text`. Confirmed in §2.3.

2. **`FlyerActionDefinition.mutation_class` is now Optional** per REV 2 §3.1. Commit-1 edit landed in worktree. Tests pass.

3. **Concept-preview function name is `send_flyer_concept_previews`** at `actions.py:3821`. Confirmed in §2.3.

4. **Lines 1652/1660 of REV 2 §2.4 were a misread.** The actual structure is `_send_generation_failure_customer_update(...)` (def at 1664) which contains 2 internal callsites at lines 1673 + 1681. The 2 hooks.py-resident wrappers are `_send_flyer_regeneration_failed_ack` (def 1689) + `_send_flyer_finalization_failed_ack` (def 1702). See §2.2 + §13.E + §13.F.

5. **The 52 hooks.py direct `send_flyer_text(` callsites span 17 distinct enclosing functions**, not 1-2 as REV 1/2's intent buckets implied. Function-distribution census:
   - `_pre_gateway_dispatch_impl` (183) — 3 sites
   - `_try_flyer_sample_prompt_request_intercept` (516) — 3 sites
   - `_try_flyer_primary_intercept` (602) — 6 sites
   - `_try_flyer_reference_scope_choice_intercept` (960) — 3 sites
   - `_try_flyer_source_vs_new_choice_intercept` (1122) — 1 site
   - `_try_flyer_reference_scope_authorization_intercept` (1401) — 1 site
   - `_reserve_flyer_access_or_reply` (1539) — 2 sites
   - `_send_flyer_regeneration_failed_ack` (1689) — 1 site (wrapper body)
   - `_send_flyer_finalization_failed_ack` (1702) — 1 site (wrapper body)
   - `_try_flyer_account_intercept` (1713) — 5 sites
   - `_try_flyer_regulated_account_guard` (1831) — 1 site
   - `_try_flyer_delivery_state_guard` (1894) — 2 sites
   - `_try_flyer_campaign_cta_intercept` (1977) — 1 site
   - `_start_flyer_intake` (2006) — 2 sites
   - `_try_flyer_intake_intercept` (2052) — 5 sites
   - `_send_flyer_active_customer_ready` (2245) — 1 site
   - `_send_flyer_active_customer_trial_link_recovery` (2266) — 1 site
   - `_try_flyer_onboarding_intercept` (2289) — 2 sites
   - `_try_flyer_brand_asset_intercept` (2398) — 1 site
   - `_try_flyer_active_project_intercept` (2484) — 10 sites

---

## §2.1 — `src/plugins/cf-router/hooks.py` direct `send_flyer_text` callsite table (52 rows)

(Authoritative — replaces REV 2 §2.1 entirely. Source: Walk 1 forensic enumeration. **49 RESOLVED + 3 UNRESOLVED.**)

| line | enclosing_function | intent | proposed_action_id | reg | notes |
|---|---|---|---|---|---|
| 346 | `_pre_gateway_dispatch_impl` | starter-prompt ideas intake reply | `for_command(PROJECT_ACTIONS, "intake.acknowledged")` | T | After `trigger_flyer_intake(sample_idea)` ok |
| 361 | `_pre_gateway_dispatch_impl` | vague-request clarification | `flat("flyer.project.vague_request_clarification", reg=F)` | F | Vague-flyer-start, starter off/already sent |
| 381 | `_pre_gateway_dispatch_impl` | customer-not-active status warning | `flat("flyer.account.customer_not_active", reg=F)` | F | Customer status not in {trial,active} |
| 546 | `_try_flyer_sample_prompt_request_intercept` | sample-prompt intake (new customer) | `for_command(PROJECT_ACTIONS, "intake.acknowledged")` | T | trigger_flyer_intake ok |
| 559 | `_try_flyer_sample_prompt_request_intercept` | customer-not-active (sample-prompt path) | `flat("flyer.account.customer_not_active", reg=F)` | F | Same as 381 |
| 589 | `_try_flyer_sample_prompt_request_intercept` | sample-prompt intake (active customer) | `for_command(PROJECT_ACTIONS, "intake.acknowledged")` | T | Same shape as 546 |
| 632 | `_try_flyer_primary_intercept` | customer-not-active status | `flat("flyer.account.customer_not_active", reg=F)` | F | Same as 381 |
| 646 | `_try_flyer_primary_intercept` | business-scope refusal | `flat("flyer.scope.business_scope_blocked", reg=F)` | F | `flyer_business_scope_block_message` truthy |
| 695 | `_try_flyer_primary_intercept` | missing-info clarification (resume) | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | `flyer_project_missing_info_reply(active_project)` |
| 714 | `_try_flyer_primary_intercept` | location-mismatch refusal | `flat("flyer.scope.location_blocked", reg=F)` | F | `flyer_location_block_message` truthy |
| 775 | `_try_flyer_primary_intercept` | reference-scope clarify/block | `flat("flyer.scope.reference_scope_blocked", reg=F)` | F | scope decision ∈ {block, clarify} |
| 944 | `_try_flyer_primary_intercept` | missing-info clarification (new) | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | Same shape as 695 |
| 996 | `_try_flyer_reference_scope_choice_intercept` | SOURCE-vs-NEW clarification | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | choice=use_reference + exact_source_edit |
| 1029 | `_try_flyer_reference_scope_choice_intercept` | authorization-relationship clarification | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | choice=authorized |
| 1105 | `_try_flyer_reference_scope_choice_intercept` | missing-info clarification (use-as-reference) | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | Same as 695/944 |
| 1154 | `_try_flyer_source_vs_new_choice_intercept` | SOURCE-vs-NEW clarification re-send | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | status-check on pending |
| 1526 | `_try_flyer_reference_scope_authorization_intercept` | brand-detail clarification | `for_command(PROJECT_ACTIONS, "clarification.request")` | T | choice ≠ use_account_details |
| 1560 | `_reserve_flyer_access_or_reply` | guest-order reservation failure | `flat("flyer.guest_order.reserve_failed", reg=F)` | F | paid_guest_order found + reserve failed |
| 1586 | `_reserve_flyer_access_or_reply` | quota-blocked / payment-required | `flat("flyer.quota.blocked", reg=F)` | F | reserve quota failed |
| 1690 | `_send_flyer_regeneration_failed_ack` (wrapper body) | regeneration-failed fallback | `flat("flyer.project.regeneration_fallback", reg=F)` | F | See §13.F for wrapper-signature decision |
| 1703 | `_send_flyer_finalization_failed_ack` (wrapper body) | finalization-failed fallback | `flat("flyer.project.finalization_fallback", reg=F)` | F | See §13.F |
| 1723 | `_try_flyer_account_intercept` | preference-cmd requires-account | `flat("flyer.account.command_failed_fallback", reg=F)` | F | customer=None + is_preference_command |
| 1752 | `_try_flyer_account_intercept` | preference-cmd failed fallback | `flat("flyer.account.command_failed_fallback", reg=F)` | F | trigger_flyer_account_command failed |
| 1766 | `_try_flyer_account_intercept` | preference-cmd unhandled fallback | `flat("flyer.account.command_failed_fallback", reg=F)` | F | result.handled False |
| 1792 | `_try_flyer_account_intercept` | account-command reply (change_plan / command_reply) | `is_change_plan` ? `for_command(ACCOUNT_ACTIONS, "change_plan")` : `for_command(ACCOUNT_ACTIONS, "command_reply")` | T | Refactor: replaces inline ActionExecutionContext at 1782-1791 |
| 1809 | `_try_flyer_account_intercept` | change_plan refused fallback | `for_command(ACCOUNT_ACTIONS, "change_plan_fallback")` | T | Both entries exist post-commit-1 |
| 1881 | `_try_flyer_regulated_account_guard` | regulated-account fail-closed scope-block | `flat("flyer.account.regulated_account_guard", reg=F)` | F | is_flyer_regulated_account_intent |
| 1936 | `_try_flyer_delivery_state_guard` | flyer project status surfaced | `flat("flyer.project.status_surfaced", reg=F)` | F | status_project resolves |
| 1964 | `_try_flyer_delivery_state_guard` | delivery-state fail-closed scope-block | `flat("flyer.delivery.delivery_state_guard", reg=F)` | F | No status_project |
| 1992 | `_try_flyer_campaign_cta_intercept` | customer-not-active (campaign-CTA) | `flat("flyer.account.customer_not_active", reg=F)` | F | Same as 381/559/632/2536 |
| 2034 | `_start_flyer_intake` | intake-start failure fallback | `flat("flyer.project.intake_failed_fallback", reg=F)` | F | trigger_flyer_intake not ok |
| 2039 | `_start_flyer_intake` | intake-started reply | `for_command(PROJECT_ACTIONS, "intake.acknowledged")` | T | trigger_flyer_intake ok |
| 2095 | `_try_flyer_intake_intercept` | intake-continuation failure fallback | `flat("flyer.project.intake_failed_fallback", reg=F)` | F | Same as 2034 |
| 2145 | `_try_flyer_intake_intercept` | brief-saved gen-failed fallback | `flat("flyer.project.brief_saved_generation_failed_fallback", reg=F)` | F | _try_flyer_primary_intercept returned None |
| 2165 | `_try_flyer_intake_intercept` | guest-order phone-required fallback | `flat("flyer.guest_order.phone_required_fallback", reg=F)` | F | start_guest_order + not phone |
| 2187 | `_try_flyer_intake_intercept` | guest-order intake ack | `for_command(PROJECT_ACTIONS, "guest_order.intake_acknowledged")` | T | trigger_start_flyer_guest_order ok |
| 2203 | `_try_flyer_intake_intercept` | intake-progress reply | `for_command(PROJECT_ACTIONS, "intake.processing")` | T | Generic intake mid-flow |
| 2253 | `_send_flyer_active_customer_ready` | active-customer-ready status | `flat("flyer.account.active_customer_ready", reg=F)` | F | Already active + campaign-CTA / vague-start |
| 2276 | `_send_flyer_active_customer_trial_link_recovery` | trial-link recovery status | `flat("flyer.account.trial_link_recovery", reg=F)` | F | Already trial-active, stale start_trial |
| 2310 | `_try_flyer_onboarding_intercept` | onboarding failure fallback | `flat("flyer.account.onboarding_failed_fallback", reg=F)` | F | trigger_flyer_onboarding not ok |
| **2326** | `_try_flyer_onboarding_intercept` | onboarding-progress reply | **UNRESOLVED — §13.A** | ? | Multi-step copy; some may resemble completion |
| **2471** | `_try_flyer_brand_asset_intercept` | brand-asset-saved acknowledgement | **UNRESOLVED — §13.B** | ? | No registry entry exists today |
| 2518 | `_try_flyer_active_project_intercept` | project status surfaced (no-active) | `flat("flyer.project.status_surfaced", reg=F)` | F | active_project=None + status_request |
| 2536 | `_try_flyer_active_project_intercept` | customer-not-active (active-project path) | `flat("flyer.account.customer_not_active", reg=F)` | F | Same as 381 etc. |
| 2553 | `_try_flyer_active_project_intercept` | business-scope refusal (active-project) | `flat("flyer.scope.business_scope_blocked", reg=F)` | F | Same as 646 |
| 2642 | `_try_flyer_active_project_intercept` | project status reply (active path) | `flat("flyer.project.status_surfaced", reg=F)` | F | Same as 1936/2518 |
| 2726 | `_try_flyer_active_project_intercept` | concept-selection acknowledgement | `for_command(PROJECT_ACTIONS, "concept_preview.cta_text")` | T | Set status + select-concept ok |
| 2776 | `_try_flyer_active_project_intercept` | manual-edit / status reply | `for_command(PROJECT_ACTIONS, "manual_review.status_replied")` | T | status=manual_edit_required + status_request |
| 2853 | `_try_flyer_active_project_intercept` | manual-edit additional-correction reply | `for_command(PROJECT_ACTIONS, "manual_edit.acknowledged")` | T | Non-status revision text |
| 2884 | `_try_flyer_active_project_intercept` | pending-revision-confirmation reminder | `flat("flyer.project.pending_revision_confirmation_reminder", reg=F)` | F | pending_revision_id truthy + approve/send-now |
| **3027** | `_try_flyer_active_project_intercept` | revision-text ack (3 sub-shapes) | **UNRESOLVED — §13.C** | ? | clarification / regen-now / revision-noted |
| 3070 | `_try_flyer_active_project_intercept` | active-project intake-resume reply | `for_command(PROJECT_ACTIONS, "intake.processing")` | T | status ∈ {intake_started, collecting_required_info, awaiting_assets} |

**Legend:** `for_command(R, "k")` = `build_action_context_for_command(R, "k")`. `flat("id", reg=F)` = `build_action_context(action_id="id", is_regulated_action=False)`. `reg` = is_regulated_action flag. T/F = True/False/UNRESOLVED.

---

## §2.2 — `src/plugins/cf-router/hooks.py` wrapper-invocation callsites (26 rows)

(Authoritative — replaces REV 2 §2.2. Source: Walk 2. Count corrected from REV 2's 23 → 26 after fresh census surfaced 3 additional callers of the 2 hooks.py-resident wrappers.)

| line | ack_function | enclosing_function | intent | proposed_action_id |
|---|---|---|---|---|
| 669 | `actions.send_flyer_processing_ack` | `_try_flyer_primary_intercept` | intake.processing (resumed) | `for_command(PROJECT_ACTIONS, "intake.processing")` |
| 808 | `actions.send_flyer_manual_review_ack` | `_try_flyer_primary_intercept` | manual_review queued (reference path) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` |
| 850 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_primary_intercept` | manual_edit queued (preflight failed) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 877 | `actions.send_flyer_edit_processing_ack` | `_try_flyer_primary_intercept` | edit.processing | `for_command(PROJECT_ACTIONS, "edit.processing")` |
| 896 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_primary_intercept` | manual_edit queued (gen failed) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 925 | `actions.send_flyer_processing_ack` | `_try_flyer_primary_intercept` | intake.processing (new project) | `for_command(PROJECT_ACTIONS, "intake.processing")` |
| 1063 | `actions.send_flyer_manual_review_ack` | `_try_flyer_reference_scope_choice_intercept` | manual_review queued (use_reference) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` |
| 1086 | `actions.send_flyer_processing_ack` | `_try_flyer_reference_scope_choice_intercept` | intake.processing (use_reference) | `for_command(PROJECT_ACTIONS, "intake.processing")` |
| 1191 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_source_vs_new_choice_intercept` | manual_edit queued (idempotent SOURCE retry) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 1268 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_source_vs_new_choice_intercept` | manual_edit queued (SOURCE preflight failed) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 1295 | `actions.send_flyer_edit_processing_ack` | `_try_flyer_source_vs_new_choice_intercept` | edit.processing (SOURCE happy) | `for_command(PROJECT_ACTIONS, "edit.processing")` |
| 1315 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_source_vs_new_choice_intercept` | manual_edit queued (SOURCE gen failed) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 1379 | `actions.send_flyer_manual_review_ack` | `_try_flyer_source_vs_new_choice_intercept` | manual_review queued (NEW choice) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` |
| 1386 | `actions.send_flyer_intake_ack` | `_try_flyer_source_vs_new_choice_intercept` | intake.acknowledged (NEW choice) | `for_command(PROJECT_ACTIONS, "intake.acknowledged")` |
| 1455 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_reference_scope_authorization_intercept` | manual_edit queued (authorized preflight failed) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 1482 | `actions.send_flyer_edit_processing_ack` | `_try_flyer_reference_scope_authorization_intercept` | edit.processing (authorized happy) | `for_command(PROJECT_ACTIONS, "edit.processing")` |
| 1492 | `actions.send_flyer_manual_edit_ack` | `_try_flyer_reference_scope_authorization_intercept` | manual_edit queued (authorized gen failed) | `for_command(PROJECT_ACTIONS, "manual_edit.queued")` |
| 1673 | `actions.send_flyer_manual_review_ack` | `_send_generation_failure_customer_update` (1664) | manual_review queued (gen-failure helper) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` — see §13.E |
| 1681 | `actions.send_flyer_intake_ack` | `_send_generation_failure_customer_update` (1664) | intake.acknowledged (helper fall-through) | `for_command(PROJECT_ACTIONS, "intake.acknowledged")` — see §13.E |
| 2453 | `actions.send_flyer_manual_review_ack` | `_try_flyer_brand_asset_intercept` | manual_review queued (brand-asset regen) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` |
| 2682 | `actions.send_flyer_processing_ack` | `_try_flyer_active_project_intercept` | intake.processing (active intake ready) | `for_command(PROJECT_ACTIONS, "intake.processing")` |
| 2914 | `actions.send_flyer_manual_review_ack` | `_try_flyer_active_project_intercept` | manual_review queued (approval regen) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` |
| **2921** | `_send_flyer_regeneration_failed_ack` (hooks.py 1689) | `_try_flyer_active_project_intercept` | regen-failed (approval branch) | **§13.D + §13.F** |
| **2969** | `_send_flyer_finalization_failed_ack` (hooks.py 1702) | `_try_flyer_active_project_intercept` | finalize-failed | **§13.D + §13.F** |
| 3043 | `actions.send_flyer_manual_review_ack` | `_try_flyer_active_project_intercept` | manual_review queued (revision regen) | `for_command(PROJECT_ACTIONS, "manual_review.queued")` |
| **3050** | `_send_flyer_regeneration_failed_ack` (hooks.py 1689) | `_try_flyer_active_project_intercept` | regen-failed (revision branch) | **§13.D + §13.F** |

**23 of 26 rows RESOLVED to a registry entry. 3 rows (2921, 2969, 3050) require §13.D + §13.F decision. All 23 resolved rows depend on §13.G for `is_regulated_action` semantics.**

---

## §2.3 — `src/plugins/cf-router/actions.py` callsites (7 chokepoint + 1 def, 8 rows)

(Authoritative — replaces REV 2 §2.3. Source: Walk 3. All 8 RESOLVED.)

| line | call/def | enclosing_function | current code | proposed migration | action_id |
|---|---|---|---|---|---|
| 3640 | call | `send_flyer_manual_edit_ack` (def 3640) | `bridge_post(chat_id, message)` at 3668 | add `*, action_context: ActionExecutionContext` to signature; pass through at 3668 | passed by caller |
| 3674 | call | `send_flyer_manual_review_ack` (def 3674) | `bridge_post(...)` at 3692 | same | passed by caller |
| 3715 | call | `send_flyer_edit_processing_ack` (def 3715) | `bridge_post(...)` at 3728 | same | passed by caller |
| 3734 | call | `send_flyer_intake_ack` (def 3734) | `bridge_post(...)` at 3746 | same | passed by caller |
| 3752 | call | `send_flyer_processing_ack` (def 3752) | `bridge_post(...)` at 3765 | same | passed by caller |
| 3875 | call | `send_flyer_concept_previews` (def 3821) | `bridge_send_media(chat_id, asset.path, caption=caption)` | build inline + pass `action_context` | `for_command(PROJECT_ACTIONS, "concept_preview.media_send")` |
| 3887 | call | `send_flyer_concept_previews` (def 3821) | `bridge_post(chat_id, "Reply APPROVE…")` | build inline + pass | `for_command(PROJECT_ACTIONS, "concept_preview.cta_text")` |
| 4030 | def | `send_flyer_text` (def 4030) | `def send_flyer_text(... action_context: Optional[ActionExecutionContext] = None)` | drop `= None` default — required kwarg | N/A (def) |

---

## §3-§10 — preserved from REV 2

(No substantive changes — registry/helper/allowlist/SSH-check code shape is unchanged. REV 3 is enumeration corrections, not new code surfaces. See git history for REV 2 bodies.)

**EXCEPTION — §6 helper-imports placement:** REV 2 §6 placed `from agents.flyer.action_registry import (...)` inline inside `_try_flyer_account_intercept`. REV 3 moves these imports to MODULE TOP of `hooks.py` (already done in worktree commit-prep) with try/except ImportError fallback. The helpers are used across ~17 enclosing functions, so module-top is correct.

---

## §7 — commit-by-commit build sequence (REV 3 — refined to 10 commits)

| # | Commit | LOC (est) |
|---|---|---|
| 1 | `feat(flyer): PROJECT_ACTIONS registry + helpers + mutation_class Optional + 2 new ACCOUNT_ACTIONS entries` (LANDED in worktree) | +210 / +30 test |
| 2 | `fix(safe_io): allowlist basename fix + DEPLOYED_FLAT_RENAMES exemption + smoke-test extension` (LANDED in worktree) | +30 |
| 3 | `refactor(cf-router): module-top helper imports + migrate _pre_gateway_dispatch_impl + sample_prompt + primary_intercept (~13 sites)` | +30 |
| 4 | `refactor(cf-router): migrate reference-scope + source-vs-new + auth-scope + reserve_quota (~7 sites)` | +20 |
| 5 | `refactor(cf-router): migrate account_intercept + regulated_account_guard + delivery_state_guard + campaign_cta (~9 sites) + inline refactor at 1792/1809` | +30 |
| 6 | `refactor(cf-router): migrate intake_intercept + active_customer helpers + onboarding + brand_asset (~10 sites)` | +25 |
| 7 | `refactor(cf-router): migrate active_project_intercept (~10 sites) + 5 actions.py ack signatures + 2 concept_preview sites + helper migration` | +60 |
| 8 | `refactor(cf-router): migrate wrapper-callsites (~26 sites) + _send_generation_failure_customer_update body + 2 hooks.py-resident wrappers per §13.E/F` | +50 |
| 9 | `feat(cf-router): drop = None default on send_flyer_text + 5 actions.py acks — required kwarg` | +15 |
| 10 | `fix(safe_io): remove actions.py + hooks.py from SAFE_IO_NULL_CONTEXT_ALLOWLIST + static gate assert removal` | -2 / +5 test |

**Total:** ~290 src + ~65 test = ~355 LOC. Within REV 2's ~370 estimate.

---

## §11 — resume contract for Phase 6 (BUILD STILL PAUSED)

**Next operator action required:** resolve §13 (UNRESOLVED rows + design questions). After resolution:
1. Update §2.1 + §2.2 rows that depend on the decision.
2. Resume build from commit 3.

**Worktree state at this pause:**
- Branch: `feat/pr-zeta-1b-full-cf-router-migration` off `origin/main` @ `fa5f230` (fast-forwarded).
- Commits 1 + 2 work LANDED locally (not git-committed): action_registry.py + tests + safe_io.py allowlist edit + smoke-test extension + static gate exemption.
- hooks.py has only module-top helper imports added (+17 lines).
- No callsite migrations attempted.

**No further code changes pending operator §13 decision.**

---

## §12 — what changed vs. plan REV 2

| Plan REV 2 | Design REV 3 | Reason |
|---|---|---|
| ~370 LOC code | ~355 LOC code | held; slightly under |
| 8 commits | 10 commits | finer split per callsite bucket (accurate row count) |
| (no callsite-level enumeration) | 86 rows enumerated (52+26+8) | walk forensic enumeration |
| REV 2 hallucinated `_apply_flyer_account_onboarding` etc. | All function names grep-validated | per build-halt lesson |
| REV 2 said 23 wrapper callsites | 26 wrapper callsites | walk 2 found 3 additional (2921, 2969, 3050) |
| REV 2 said 4 hooks.py-resident wrappers | 2 hooks.py-resident wrappers (1689, 1702) + 1 helper (1664) | walk 2 corrected REV 2's mis-reading of lines 1652/1660 |

---

## §13 — UNRESOLVED rows + design questions (GATING BUILD)

Operator decision required on the following 7 items before build resumes.

### §13.A — Line 2326: onboarding-progress reply (`_try_flyer_onboarding_intercept`)

**Context:** `trigger_flyer_onboarding` succeeded; `result.handled` is True. The reply text varies by `result.next_status` — could be "What's your business name?" (intake-progress) OR "Your Flyer Studio account is now set up under {business_name}" (state-transition completion).

**Choices:**
1. **Flat non-regulated** — `build_action_context(action_id="flyer.account.onboarding_progress", is_regulated_action=False)`. Conservative; lint skipped; works regardless of which onboarding step fired.
2. **Add `onboarding_completed` to ACCOUNT_ACTIONS** as regulated entry; lint runs on message. Cleaner attribution, but if trial-activation copy contains lint-trip verbs, sends would refuse.
3. **Compute action_id at the callsite** based on `result.next_status` — split into 2 branches with different contexts.

**Recommendation:** **Choice 1** for ζ.1b (smallest scope); defer choice 2 to ζ.2 with concrete evidence of what `trigger_flyer_onboarding` actually emits across states.

### §13.B — Line 2471: brand-asset-saved reply (`_try_flyer_brand_asset_intercept`)

**Context:** Brand asset (logo/template) stored on customer's account state. Reply is a confirmation.

**Choices:**
1. **Flat non-regulated** — `build_action_context(action_id="flyer.account.brand_asset_saved", is_regulated_action=False)`. Conservative.
2. **Add `update_brand_asset` to ACCOUNT_ACTIONS** as regulated `write` action with `mutation_class="local_reversible"` (asset re-uploadable). Treats save same as other account JSON-state updates.

**Recommendation:** **Choice 2** — brand-asset save IS a regulated account-state mutation (same shape as `update_phone`, `update_business_name`). One-entry addition; semantically correct.

### §13.C — Line 3027: revision-text fallback (3 sub-branches)

**Context:** `_try_flyer_active_project_intercept` revision-text fallback has 3 sub-shapes:
- (a) `revision_requires_clarification == True` — asks for clarification
- (b) `ok and needs_regen` — "I'm regenerating the design now"
- (c) else — "Revision noted. I will keep it"

**Choices:**
1. **Split into 3 send_flyer_text calls** — each sub-branch gets its own send with its own action_id (clarification.request / edit.processing / project.reply).
2. **Compute action_id at runtime** above the single send via the same if/elif/else.
3. **Use `edit.processing` for all 3** (dominant) — accept semantic imprecision for (a) and (c).

**Recommendation:** **Choice 2** — preserves single-callsite shape, no behavior change, action_id reflects actual sub-branch. ~5 LOC delta.

### §13.D — Lines 2921, 2969, 3050: regen/finalize failure replies

**Context:** 3 callers of 2 hooks.py-resident wrappers (1689 + 1702). Each wrapper emits a fixed failure-notice via `actions.send_flyer_text(chat_id, <inline literal>)`.

**Choices:**
1. **Flat helper with descriptive action_id** — `flyer.project.regeneration_fallback` + `flyer.project.finalization_fallback` (same as §2.1 rows 1690 + 1703).
2. **Add `regeneration.failed` + `finalization.failed` to PROJECT_ACTIONS** as registry entries. Extends registry to cover failure-acknowledgement claims.

**Recommendation:** **Choice 1** — failures are informational; registry today is "completion claims" only. Extending to failure-acks is its own design discussion; out of scope for ζ.1b.

### §13.E — `_send_generation_failure_customer_update` helper (def 1664): threading strategy

**Context:** Helper has 2 internal callsites (1673 → manual_review_ack, 1681 → intake_ack). Caller can't know which branch fires (depends on `flyer_generation_queued_manual_review(gen_detail)`).

**Choices:**
1. **Build context per-branch inside the helper** — helper signature unchanged; helper owns the branch→action_id mapping.
2. **Thread `action_context: ActionExecutionContext` through the helper** — caller passes a generic context; helper ignores branch semantics.
3. **Take both contexts as kwargs** — `manual_review_ctx` + `intake_ctx`; caller pre-builds both.

**Recommendation:** **Choice 1** — cleanest. Helper already knows the branch logic; pushing context decision to caller forces caller to know it twice.

### §13.F — 2 hooks.py-resident wrappers (1689, 1702): signature change strategy

**Context:** `_send_flyer_regeneration_failed_ack` and `_send_flyer_finalization_failed_ack` each emit a FIXED message (no branching). Each has 1 outbound `actions.send_flyer_text` call.

**Choices:**
1. **Add `*, action_context: ActionExecutionContext` required kwarg** to each wrapper signature; require callers (2921, 2969, 3050) to pass it. Forces explicit context at every site.
2. **Build context inside the wrapper** — wrapper signature unchanged. Each wrapper has only one possible reply, so context is fixed.

**Recommendation:** **Choice 2** — these wrappers have no branch ambiguity. Inside-build is cleaner. The "required kwarg as forcing function" pattern is over-engineered when there's only one possible reply per wrapper.

**Trade-off:** Choice 2 means commit 9 (drop `= None` default) does NOT apply to these 2 wrappers (no kwarg to drop). Their migration is just the body change in commit 8.

### §13.G — `is_regulated_action` for queue-state acks (cross-walk inconsistency)

**Context:** The two walks classified PROJECT_ACTIONS-helper rows differently:
- Walk 1 (direct `send_flyer_text`): PROJECT_ACTIONS lookups marked `regulated=True` (matches `build_action_context_for_command` default)
- Walk 2 (wrapper-invocation): PROJECT_ACTIONS lookups marked `regulated=False` (treats queue-state as non-completion)

The helper `build_action_context_for_command` hardcodes `is_regulated_action=True`. Walk 2's classification isn't achievable as-stated.

**Empirical safety check:** Chokepoint's lint scans for FORBIDDEN_COMPLETION_VERBS (e.g. "done", "completed", "processed", "approved", "delivered"). The §2.2 ack messages ("Got it. I'm creating your flyer now..." / "I'm updating your flyer now...") use future-tense framing — do NOT obviously trip these verbs. But PR-ζ.1a's hotfix REMOVED "processed" from an ack message — evidence that ack messages CAN trip the lint.

**Choices:**
1. **Extend `build_action_context_for_command` to accept `is_regulated_action: bool = True` param** — caller can opt False for queue-state. ~3 LOC change to helper; ~20 rows in §2.2 become `for_command(..., is_regulated_action=False)`.
2. **Use the flat helper with action_id from the registry lookup** at queue-state sites — bypass registry-helper's True default. More verbose; same result.
3. **Accept `is_regulated_action=True` for all PROJECT_ACTIONS sites** — let lint run on queue-state messages. Audit and adjust copy if lint trips at smoke. Highest build-phase risk.

**Recommendation:** **Choice 1** — extending the helper is cleanest. Lint should only run when message is a COMPLETION CLAIM; queue-state acks are explicitly NOT completion claims. Helper should support this distinction.

**Impact on commit-1 work in worktree:** helper currently doesn't have this param. Choice 1 requires an Edit to `action_registry.py`'s `build_action_context_for_command` signature + 1 new test. ~5 LOC change to commit 1.

---

## §14 — operator §13 decisions (received 2026-05-26)

The operator selected the conservative path with these specific choices. Each item is now RESOLVED; the table below captures the binding outcome.

| § | Operator decision | Resulting design |
|---|---|---|
| 13.A | Add ACCOUNT_ACTIONS entry `flyer.account.onboarding_progress` (account setup state, not flat ad-hoc). | New ACCOUNT_ACTIONS["onboarding_progress"] entry — `effect="write"`, `mutation_class="local_reversible"` (onboarding state re-walkable). §2.1 row 2326 → `for_command(ACCOUNT_ACTIONS, "onboarding_progress")`. **LANDED in commit 1.** |
| 13.B | Add `update_brand_asset` / `flyer.account.update_brand_asset` to ACCOUNT_ACTIONS. | New ACCOUNT_ACTIONS["update_brand_asset"] entry — `effect="write"`, `mutation_class="local_reversible"` (asset re-uploadable). §2.1 row 2471 → `for_command(ACCOUNT_ACTIONS, "update_brand_asset")`. **LANDED in commit 1.** |
| 13.C | Split the 3 sub-branches into distinct sends with distinct contexts. | §2.1 row 3027 becomes 3 sub-rows: (a) `clarification.request`, (b) `edit.processing`, (c) `project.reply` (revision noted). Commit 7 splits the call into 3 explicit branches. |
| 13.D | Add formal PROJECT_ACTIONS entries `flyer.generation.failed_ack` + `flyer.finalization.failed_ack`, non-regulated, local-reversible/metadata-only. | New PROJECT_ACTIONS entries — `effect="read"`, `mutation_class="local_reversible"`. §2.1 rows 1690, 1703 + §2.2 rows 2921, 2969, 3050 all use these. Callers pass `is_regulated_action=False` via the §13.G-extended helper. **LANDED in commit 1.** |
| 13.E | Threaded context, not inside-build. Caller knows the flow. | `_send_generation_failure_customer_update` (def 1664) gets `*, action_context: ActionExecutionContext` required kwarg. All 4 callers (685, 934, 1095, 2698) pass `for_command(PROJECT_ACTIONS, "generation.failed_ack", is_regulated_action=False)`. Helper threads context to BOTH internal acks (1673, 1681) — both audit-row as `flyer.generation.failed_ack`. The walk-1's branch-specific action_ids for 1673/1681 are SUPERSEDED by the threaded `generation.failed_ack`. |
| 13.F | Kwarg flip for wrappers; only inside-build for single-semantic wrappers. | Both `_send_flyer_regeneration_failed_ack` (1689) and `_send_flyer_finalization_failed_ack` (1702) get `*, action_context: ActionExecutionContext` required kwarg. Callers (2921, 2969, 3050) pass the registry context. Wrapper bodies (1690, 1703) pass through to `actions.send_flyer_text(..., action_context=action_context)`. |
| 13.G | Extend `build_action_context_for_command(..., is_regulated_action: bool = True)`. | Helper signature extended; default True preserves regulated-action posture. Queue-state acks in §2.2 (20 rows) pass `is_regulated_action=False` explicitly. **LANDED in commit 1.** |

**Net effect on §2.1 (3 UNRESOLVED rows now resolved):**
- 2326 → `for_command(ACCOUNT_ACTIONS, "onboarding_progress")` — regulated=True (default)
- 2471 → `for_command(ACCOUNT_ACTIONS, "update_brand_asset")` — regulated=True (default)
- 3027 → SPLIT into 3 sends:
  - (a) clarification: `for_command(PROJECT_ACTIONS, "clarification.request")` — regulated=True
  - (b) regen-now: `for_command(PROJECT_ACTIONS, "edit.processing")` — regulated=True
  - (c) revision-noted: `for_command(PROJECT_ACTIONS, "project.reply")` — regulated=True

**Net effect on §2.2 (3 §13.D/F rows now resolved + 20 §13.G rows reclassified):**
- 1673, 1681 → both pass through helper kwarg; audit as `flyer.generation.failed_ack` (collapsed per §13.E)
- 2921, 3050 → callers pass `for_command(PROJECT_ACTIONS, "generation.failed_ack", is_regulated_action=False)` to `_send_flyer_regeneration_failed_ack`
- 2969 → caller passes `for_command(PROJECT_ACTIONS, "finalization.failed_ack", is_regulated_action=False)` to `_send_flyer_finalization_failed_ack`
- 20 other queue-state rows (669, 808, 850, 877, 896, 925, 1063, 1086, 1191, 1268, 1295, 1315, 1379, 1386, 1455, 1482, 1492, 2453, 2682, 2914, 3043) all add `, is_regulated_action=False` to their `for_command(PROJECT_ACTIONS, ...)` calls. Walk-1's `regulated=True` default for these rows is SUPERSEDED.

**Net effect on commit-1 work in worktree (LANDED + tested 20/20):**
- ACCOUNT_ACTIONS: 4 new entries total (change_plan_fallback, command_reply, onboarding_progress, update_brand_asset). 10 → 14 entries (ground-truth verified: `len(ACCOUNT_ACTIONS) == 14` in worktree post-commit-1).
- PROJECT_ACTIONS: 21 entries (19 lifecycle + 2 failure-ack).
- `build_action_context_for_command` signature: now accepts `is_regulated_action: bool = True`.
- `mutation_class`: Optional on the dataclass, set explicitly to `local_reversible` on the 4 new ACCOUNT entries + 2 failure-ack PROJECT entries; remains None on the other 19 PROJECT entries.

**§13 gate satisfied:** every row in §2.1, §2.2, §2.3 has a concrete `proposed_action_id` resolved. 0 UNRESOLVED rows remain. Build resumes from commit 3.

---

## §15 — ζ.1b scope discipline: explicit non-goals + follow-up

Operator instruction 2026-05-26 (pre-commit-3): make these explicit BEFORE migration starts to keep ζ.1b's scope honest. The migration is mechanical context-threading + allowlist debt removal. Anything beyond that is a follow-up PR, not in ζ.1b scope.

### §15.1 — In scope for ζ.1b

- Thread `ActionExecutionContext` through every `bridge_post*` callsite so the PR-ζ chokepoint can attribute audit rows correctly.
- Remove `actions.py` + `hooks.py` from `SAFE_IO_NULL_CONTEXT_ALLOWLIST` (the diagnostic-only allowlist debt PR-ζ left behind).
- Fix the `manual_queue.py` → `flyer_manual_queue.py` allowlist basename mismatch surfaced by the 2026-05-26 18:41:45Z dev-VPS smoke evidence.
- Add 4 new ACCOUNT_ACTIONS entries + 2 new PROJECT_ACTIONS entries needed for accurate audit attribution at specific callsites (per §13.A/B/D).
- Extend `build_action_context_for_command` to accept `is_regulated_action` kwarg so queue-state acks can opt out of the chokepoint lint (per §13.G).

### §15.2 — Explicit NON-goals for ζ.1b

- **No new field validators** for customer message text. ζ.1b does not introduce brittle regex/keyword/whitelist matchers for free-form customer intent. The 4 new ACCOUNT_ACTIONS entries + 2 new PROJECT_ACTIONS entries exist solely to give existing callsites a stable audit-row `action_id`; they do NOT impose new acceptance criteria on customer input.
- **No new intent-classification logic.** The `domain` field on existing registry entries is descriptive, not gating. ζ.1b does not add intent-router code that branches on registry fields.
- **No message-text rewrites** beyond what existing PR-ζ.1a forbidden-verb lint already requires. The migration adds `action_context=` kwargs to existing send calls; the message text stays verbatim unless it already trips PR-γ's `FORBIDDEN_COMPLETION_VERBS` lint.
- **No new MutationClass semantics beyond what's already declared.** The 2 failure-ack PROJECT_ACTIONS entries declare `mutation_class="local_reversible"` to capture the "no rollback needed" semantic; they do NOT add new audit-fail-closed branches in safe_io.
- **No changes to the dispatcher matrix** in `dispatch_shift_agent/SKILL.md` or anywhere else. ζ.1b is internal cf-router code threading; the upstream routing stays as-is.

### §15.3 — Follow-up PR (post-ζ.1b)

**Hermes-first semantic flyer brief contract.** The deterministic-regex routing in cf-router/hooks.py is what PR-α through PR-ζ have been incrementally tightening. The next durable improvement is to move free-form customer-intent classification (vague flyer starts, revision text, status check-ins, source-vs-new disambiguation, etc.) into a Hermes intent layer with replay/validator coverage, per the existing project memory at `tasks/lessons.md` §"2026-05-22 - Flyer Hermes-first pivot".

ζ.1b's mechanical context threading is a PRE-condition for that follow-up — once every callsite has an explicit `action_context`, the Hermes intent layer can be wired in by reshaping `_try_flyer_*_intercept` functions without losing audit-row attribution.

This follow-up is NOT in ζ.1b's scope. ζ.1b ships when the 8 commits in §7 land + 20/20 commit-1 tests pass + the static gate + the cf-router integration tests pass. The semantic-brief contract is its own design/plan/build cycle.

### §15.4 — How this protects scope during migration

When migrating the 75+26+7 callsites, the rule is:
- **Add `action_context=...` kwarg.** Leave everything else unchanged.
- **Do NOT rewrite reply text** unless PR-γ lint forces it (and even then, scope-cut to the smallest change that passes lint).
- **Do NOT add new conditionals** to disambiguate intents. If the existing branch logic produces the right `action_id`, that's enough. If it doesn't, document it as a §13-style UNRESOLVED row and halt — don't invent new logic on the fly.
- **Do NOT add new validators on the customer-facing input.** ζ.1b is outbound-discipline; inbound discipline is the follow-up PR.

If during migration any callsite tempts a refactor beyond "add kwarg", halt with file:line evidence and surface to operator. The migration is supposed to be smaller than the design doc, not larger.
