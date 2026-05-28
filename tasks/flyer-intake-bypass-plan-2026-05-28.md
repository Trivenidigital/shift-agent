# Flyer Studio intake bypass when intent is clear — plan

**Date:** 2026-05-28
**Branch:** `plan/flyer-intake-bypass-2026-05-28` (off `origin/main` HEAD `f7ad477`)
**Drift-check tag:** `extends-Hermes`
**New primitives introduced:** None. Adds one bypass-decision helper (`should_bypass_intake_for_clear_intent`), one audit-row variant (`FlyerIntakeBypassed`), and a one-line conditional swap inside the existing intake intercept. All other substrate is deployed.

This plan addresses the **22.png class of failures**: customer attaches a flyer image + sends an explicit edit instruction during onboarding/intake, and Flyer Studio replies with `"Please choose a creation mode. ... REFERENCE: ..."` instead of routing to the source-edit path. The deployed source-edit capability exists (`hooks.py:652` `_try_flyer_primary_intercept`); the failure is the intake intercept consuming the message first.

---

## 1. The problem (customer view)

**Reproduction (22.png):**
- Customer is in intake/onboarding state — not yet `active`/`trial` (e.g., new customer who started the trial flow).
- Customer attaches a flyer image + sends "edit this to ..." instruction.
- cf-router intake intercept (`hooks.py:2354`) consumes the message and replies with the mode-selection wizard.
- The source-edit project never gets created; the explicit edit request is dropped on the floor.

**The deployed classifiers say the right thing:**
- `actions.classify_flyer_intent(text)` → `(True, [...])`
- `actions.is_exact_reference_edit_request(text, has_media=True)` → `True`
- `actions.should_start_new_flyer_over_active(text, has_media=True)` → `True`

**The intake intercept body already has bypass-like logic (`hooks.py:2378-2382`):**

```python
if customer and customer.get("status") in {"active", "trial"} and status not in protected_statuses and (
    actions.classify_flyer_intent(text)[0]
    or actions.should_start_new_flyer_over_active(text, has_media=bool(media_path))
):
    return None
```

**But the gate has two gaps:**
1. **Customer-state precondition is too narrow** — `customer.status in {"active","trial"}` excludes brand-new senders and customers in onboarding states. The 22.png case sits exactly in that gap.
2. **`is_exact_reference_edit_request` is not in the OR clause** — the explicit edit-with-media classifier is the most direct signal of source-edit intent and isn't checked.

**The principle to encode:** *setup must never outrank intent when the user has already sent enough information to act.*

---

## 2. Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp inbound media + text | yes — Hermes gateway substrate | reuse |
| Intent classification | yes — `classify_flyer_intent` already deployed at `cf-router/actions.py:1244` | reuse |
| Explicit edit-with-media classification | yes — `is_exact_reference_edit_request` at `actions.py:1499` | reuse |
| Active-project-clobber detection | yes — `should_start_new_flyer_over_active` at `actions.py:1464` | reuse |
| cf-router intercept ladder | yes — deployed `_pre_gateway_dispatch_impl` orchestrates intercept order | reuse, modify one intercept body |
| Customer profile lookup | yes — `find_flyer_customer_by_sender` + `find_flyer_intake_session_by_sender` | reuse |
| Audit chain | yes — `log-decision-direct` + `LogEntry` discriminated union | reuse; add `FlyerIntakeBypassed` variant (mirrors P0 #2 C6/C5 pattern) |
| Language default | yes — `preferred_language: FlyerLanguage = "en"` schema default in 4 places (`schemas.py:1127, 1263, 1279, 1557`) | reuse; English default is already the substrate convention |
| Source-edit project creation | yes — `_try_flyer_primary_intercept` at `hooks.py:652` invokes existing source-edit subprocess | reuse unchanged |

**Awesome Hermes Agent ecosystem check:** No external Hermes/community skill is needed. The bypass decision is project-specific routing policy composed from three deployed classifiers; all substrate (intent classification, edit-request detection, audit chain, language default) is in-tree.

---

## 3. End-to-end flow (post-PR)

1. `[Hermes]` Customer sends WhatsApp message (text + optional media) → Hermes gateway routes to cf-router.
2. `[Hermes]` `_pre_gateway_dispatch_impl` runs the intercept ladder; the intake intercept at line 272 fires.
3. `[Hermes]` Inside `_try_flyer_intake_intercept`: identify sender, lookup customer + intake_session — unchanged.
4. **`[net-new]`** Call new helper `should_bypass_intake_for_clear_intent(text, customer, intake_session, has_media)` — replaces the inline bypass conditional at lines 2378-2382.
5. **`[net-new]`** Bypass helper evaluates: protected-status guard FIRST, then customer-state precondition (brand-new sender OR `active`/`trial` — see §5b), then signal OR-of (`is_exact_reference_edit_request`, `should_start_new_flyer_over_active`, `classify_flyer_intent` AND existing-customer).
6. **`[net-new]`** If bypass: write `FlyerIntakeBypassed` audit row + open the `flyer_intake_bypass_shadow` context (mirrors `flyer_intent_shadow` pattern at `hooks.py:178-197`) + `return None` so the intercept ladder proceeds.
7. `[Hermes]` Returning `None` does NOT jump straight to active-project. The message **passes through 6 intermediate intercepts** before reaching active-project (line 303) and the `should_start_new_flyer_over_active`-gated primary intercept call (line 327): `_try_flyer_reference_scope_choice_intercept` (275), `_try_flyer_source_vs_new_choice_intercept` (283), `_try_flyer_reference_scope_authorization_intercept` (286), `_try_flyer_brand_asset_intercept` (290, when media), `_try_flyer_existing_onboarding_intercept` (293), guest-paid-flyer fast path (296-302). **Verified: none read intake-session state**, so the bypass-return-None is safe. The downstream intercept that handles the message captures the outcome via the shadow context.
8. `[Hermes]` On bypass-then-primary: `_try_flyer_primary_intercept` runs the deployed classifier composition + creates project or source-edit job. `create-flyer-project` writes the new project; `customer_language` defaults to `"en"` per existing schema default (`schemas.py:1557`); stored customer preference honored via existing intake → customer profile read path.
9. `[Hermes]` If not bypass: existing intake intercept body continues — `trigger_flyer_intake` wizard flow unchanged.
10. **`[net-new]`** End-of-dispatch finalization in `_pre_gateway_dispatch_impl` finally-block: if bypass shadow was opened, emit `FlyerIntakeBypassOutcome` audit row with `outcome ∈ {"routed_to_project", "unrouted", "intermediate_intercept_handled"}` derived from `hook_result`. Mirrors the existing `finalize_flyer_intent_shadow` pattern.
11. `[Hermes]` cf-router post-subprocess branch dispatches concept previews via `_dispatch_concept_preview_send` (existing deployed substrate).
12. `[Hermes]` Customer receives draft + correction prompt.

**Step count:** 12 total. `[Hermes]`: 8. `[net-new]`: 4 (steps 4, 5, 6, 10 — helper + decision audit + shadow context + outcome audit).

**Red-flag check:** 4/12 = 33% net-new. Within Hermes-first norms.

---

## 4. Drift-rule self-checks (read deployed code first)

| Work type | File read | Evidence |
|---|---|---|
| Routing / dispatcher | `src/plugins/cf-router/hooks.py` lines 260-340 + 2354-2445 | Intercept ladder order pinned at lines 263-331; intake intercept at line 272 is the gate. `_try_flyer_intake_intercept` body shows the existing partial bypass at 2378-2382 (gated on `customer.status in {"active","trial"}`) and the protected-status guard set at 2367-2376. |
| Classifier surface | `src/plugins/cf-router/actions.py` lines 1244 (`classify_flyer_intent`), 1464 (`should_start_new_flyer_over_active`), 1499 (`is_exact_reference_edit_request`) | All three signatures confirmed: text + `has_media` keyword arg; pure functions. `is_exact_reference_edit_request` returns `False` for `has_media=False` early (line 1507-1508), so the helper's edit-with-media call is structurally correct. |
| Schema work | `src/platform/schemas.py` lines 666 (`FlyerLanguage`), 1127/1263/1279/1557 (`preferred_language: FlyerLanguage = "en"`) | English-as-default-language is already a deployed schema convention in 4 places. No new schema field needed for the language piece of the brainstorm — `customer_language="en"` happens automatically for complete bypass cases. |
| Schema — LogEntry variant precedent (CORRECTED 2026-05-28 per reviewer 1+3) | `src/platform/schemas.py:3550-3650` (`FlyerRecovery*` family) + `schemas.py:3753` (`FlyerHermesIntentDecision`) | Plan branch is off `origin/main` HEAD `f7ad477` — P0 #2 audit variants (`FlyerQASeverityClassified` et al.) live on the P0 #2 branch stack and have NOT merged to main yet. The real deployed precedent is the `FlyerRecovery*` family (6 variants using `chat_id_hash: str = Field(min_length=1, max_length=120)`) plus `FlyerHermesIntentDecision` (uses `chat_key_hash` — one-off). Both use `_BaseEntry` parent + snake_case `type: Literal[...]` + `ts` inherited. Plan picks `chat_id_hash` as the dominant convention (6:1 ratio). |
| Tests | `tests/test_cf_router_flyer_routing.py` (existing intake/primary intercept tests) | Mirror these for the new bypass cases. Existing tests cover the ladder ordering invariants; new tests add bypass replay scenarios. |
| Intake state machine | `src/agents/flyer/intake.py` line 54 (`preferred_language: str = "en"`) | Confirms English-default precedent inside the intake module too. No structural change to the intake state machine — the bypass acts before the wizard fires. |

**No drift detected.** Every changed surface already exists. Tag stays `extends-Hermes`.

---

## 5. The bypass helper — signature + conditions

```python
# src/plugins/cf-router/actions.py — new helper

_INTAKE_PROTECTED_STATUSES = frozenset({
    "choosing_sample_idea",
    "text_awaiting_brief",
    "guided_collecting_goal",
    "guided_collecting_schedule",
    "guided_collecting_items",
    "guided_collecting_location",
    "guided_collecting_assets",
    "brief_pending_approval",
})


_CUSTOMER_BYPASS_ELIGIBLE_STATUSES = frozenset({"active", "trial"})


def should_bypass_intake_for_clear_intent(
    text: str,
    customer: Optional[dict],
    intake_session: Optional[dict],
    *,
    has_media: bool = False,
) -> Optional[str]:
    """Returns the bypass_reason string when intake should be skipped,
    else None.

    Hermes-as-brain compliance: this helper does NOT classify on its own.
    It composes three deployed classifiers + a customer-state precondition
    + a protected-status guard. Hermes/classifiers decide; helper composes;
    intercept executes.

    Two preconditions ALWAYS block bypass (evaluated first):
    1. intake_session.status ∈ _INTAKE_PROTECTED_STATUSES — operator is
       actively collecting a brief; never interrupt.
    2. customer is not None AND customer.status NOT in {"active","trial"}
       — expired/cancelled/suspended customers stay in the wizard /
       re-onboarding path (account-lifecycle boundary; operator decision
       2026-05-28). Brand-new senders (customer is None) ARE bypass-eligible.

    Then four signal branches (return the matching bypass_reason):
    - "edit_with_media": is_exact_reference_edit_request matches (edit verb
      + edit target + media). The F0108 / 22.png case.
    - "new_flyer_with_media": should_start_new_flyer_over_active AND has_media.
    - "new_flyer_text_only": should_start_new_flyer_over_active AND not has_media.
    - "existing_active_customer_intent" / "existing_trial_customer_intent":
      classify_flyer_intent matches AND customer.status is active/trial.
      Preserves pre-PR behavior of the lines 2378-2382 bypass for these states.
    """
    # Precondition 1: protected statuses block bypass.
    status = str((intake_session or {}).get("status") or "")
    if status in _INTAKE_PROTECTED_STATUSES:
        return None

    # Precondition 2: account-lifecycle boundary. Existing customers must be
    # active/trial; brand-new senders (customer is None) are bypass-eligible.
    # Expired/cancelled/suspended NEVER bypass — wizard owns re-onboarding.
    if customer is not None and customer.get("status") not in _CUSTOMER_BYPASS_ELIGIBLE_STATUSES:
        return None

    # Signal 1: edit-with-media is the most unambiguous bypass signal.
    if is_exact_reference_edit_request(text, has_media=has_media):
        return "edit_with_media"

    # Signal 2 + 3: clear new-flyer request. Split on media presence so
    # operators can replay source-edit vs new-creation routes separately.
    if should_start_new_flyer_over_active(text, has_media=has_media):
        return "new_flyer_with_media" if has_media else "new_flyer_text_only"

    # Signal 4 + 5: existing-customer fast path. Trial vs active split
    # surfaces revenue-at-risk customers in audit triage.
    intent_match, _reasons = classify_flyer_intent(text)
    if intent_match and customer:
        customer_status = customer.get("status")
        if customer_status == "trial":
            return "existing_trial_customer_intent"
        if customer_status == "active":
            return "existing_active_customer_intent"

    return None
```

**Behavior expansion vs pre-PR (made explicit per reviewer 1 finding):** Pre-PR (`hooks.py:2378-2382`) gated EVERY bypass branch on `customer.status in {"active","trial"}`. The new helper relaxes this for the two media-or-new-flyer signal branches: a brand-new sender (no customer row) hitting either `is_exact_reference_edit_request` or `should_start_new_flyer_over_active` now bypasses. **Expired/cancelled/suspended customers do NOT bypass** (precondition 2; operator decision 2026-05-28 — account-lifecycle boundary owns re-onboarding). Reviewer 3 verified the customer-store distribution: 7 trial customers, 0 active, so existing-trial behavior is preserved bit-for-bit; the measurable change concentrates on brand-new senders, which is the F0108 population.

**Six test cases pin the behavior (Row 1 phrasing corrected per reviewer 2+3 finding):**

| Scenario | text + has_media | customer | intake_session.status | bypass_reason |
|---|---|---|---|---|
| F0108 / 22.png — new customer + edit + media | "edit this to add Saturday hours" + media | None | `choosing_mode` | `"edit_with_media"` |
| New customer + clear new-flyer text + media | "Create flyer for Dosa Night" + media | None | `choosing_mode` | `"new_flyer_with_media"` |
| Existing trial customer + intent + no media | "I want a flyer for next week" | `{status: trial}` | None | `"existing_trial_customer_intent"` |
| Customer in protected status + clear intent | "Create flyer for Dosa Night" | any | `guided_collecting_goal` | None (protected) |
| Brand-new sender + vague text + no media | "hi" | None | `choosing_language` | None (no signal) |
| **Counter-example** — "edit this" alone (no edit_target) | "edit this" + media | None | `choosing_mode` | None (`is_exact_reference_edit_request` requires edit_verb AND edit_target; "edit"+"this" lacks a target word like "time"/"price"/"hours") |
| **Expired customer + edit-with-media** (operator decision 2026-05-28) | "edit this to fix time" + media | `{status: expired}` | None | None (precondition 2 — account-lifecycle boundary) |

---

## 5b. Unprotected intake states (enumerated per reviewer 1+3 finding)

The `FlyerIntakeStatus` Literal (`schemas.py:683-694`) has **10 total members**. The protected set above covers 8; the **2 explicitly-unprotected states are bypass-eligible by design**:

- `choosing_language` — wizard-front-door state. A bypass-eligible inbound (e.g., edit-with-media) skips the language prompt; new project gets `customer_language="en"` per schema default; stored preference honored if customer existed.
- `choosing_mode` — wizard-front-door state. The F0108 / 22.png case empirically hits this status (reviewer 3 confirmed via live audit row `action=choose_mode` for chat `201975216009469@lid`). Bypass here is the load-bearing fix.

A future reader who wonders "why aren't choosing_language / choosing_mode protected?" sees this section.

`src/agents/flyer/onboarding.py` has its own state machine for the customer-row lifecycle (`collecting_business_name`, `payment_pending`, etc.) — those are NOT `FlyerIntakeStatus` values and don't appear here. Onboarding state is read via `customer.status`, which feeds precondition 2 above.

---

## 6. Build sequence (3 commits, ~190 LOC source + ~280 LOC test)

### Commit 1 — `feat(flyer): FlyerIntakeBypassed + FlyerIntakeBypassOutcome audit variants`
**Files:** `src/platform/schemas.py`, `tests/test_flyer_schemas.py`.
**Source (~50 LOC):**

Two **immutable** audit-row variants per operator decision 2026-05-28 (#5 — append-only logs preclude a mutable `routed_to_project: bool` on the first row). The pair captures the decision + the downstream outcome as two separately-grepped events.

```python
class FlyerIntakeBypassed(_BaseEntry):
    """Decision-time audit: intake bypass fired. Emitted by _try_flyer_intake_intercept
    immediately on bypass. Captures WHY we bypassed; outcome row follows."""
    type: Literal["flyer_intake_bypassed"] = "flyer_intake_bypassed"
    chat_id_hash: str = Field(min_length=1, max_length=120)
    bypass_reason: Literal[
        "edit_with_media",
        "new_flyer_text_only",
        "new_flyer_with_media",
        "existing_active_customer_intent",
        "existing_trial_customer_intent",
    ]
    has_media: bool
    customer_state: str = Field(default="", max_length=40)  # "" if customer is None
    intake_session_status: str = Field(default="", max_length=80)
    # Reviewer 2 finding — capture script signal for regional-SMB customers
    # (Triveni's customer base is Hindi/Telugu/Tamil). Detection-and-act on
    # non-Latin scripts is deferred; this field accumulates the data so a
    # follow-up PR can act on it without backfill. Prevents silent anglo-defaulting.
    inbound_script: Literal["latin", "devanagari", "tamil", "other"] = "latin"


class FlyerIntakeBypassOutcome(_BaseEntry):
    """Outcome-time audit: what happened after intake-bypass fired. Emitted
    by _pre_gateway_dispatch_impl's finally block, mirroring the deployed
    flyer_intent_shadow finalization pattern (hooks.py:178-197).

    Operators reconstruct the bypass → outcome trail by correlating
    `chat_id_hash` between FlyerIntakeBypassed and FlyerIntakeBypassOutcome
    within a single dispatch (same ts to nearest second; logrotate-resilient
    because both rows are emitted in the same dispatch finally-block)."""
    type: Literal["flyer_intake_bypass_outcome"] = "flyer_intake_bypass_outcome"
    chat_id_hash: str = Field(min_length=1, max_length=120)
    outcome: Literal[
        "routed_to_project",
        "unrouted",
        "intermediate_intercept_handled",
    ]
    project_id: str = Field(default="", max_length=40)  # F-pattern populated only when outcome=routed_to_project
    handler_intercept: str = Field(default="", max_length=80)  # name of intercept that handled when intermediate_intercept_handled
    elapsed_ms: int = Field(default=0, ge=0)  # from bypass decision → outcome emit
```

- Add BOTH variants to `LogEntry` Union (right after `FlyerHermesIntentDecision` for grouping).
- Add BOTH to `__all__`.
- Field-name choice `chat_id_hash` (max_length=120) matches dominant `FlyerRecovery*` family convention (6 sites). The one-off `chat_key_hash` at `FlyerHermesIntentDecision` is acknowledged but not followed.

**Tests (~85 LOC, ~12 cases):**
- Both variants: round-trip, each Literal value, `extra="forbid"`, discriminator routing via `TypeAdapter(LogEntry)`, `__all__` export.
- `FlyerIntakeBypassed.bypass_reason`: 5 Literal values each round-trip.
- `FlyerIntakeBypassOutcome.outcome`: 3 Literal values each round-trip + the `project_id` F-pattern validation when populated.
- `inbound_script`: 4 Literal values + default "latin".

### Commit 2 — `feat(cf-router): should_bypass_intake_for_clear_intent helper`
**Files:** `src/plugins/cf-router/actions.py`, `tests/test_cf_router_flyer_routing.py`.
**Source (~70 LOC):**
- `_INTAKE_PROTECTED_STATUSES` frozenset (mirrors the inline set at `hooks.py:2367-2376` exactly — 8 members; ZERO drift; both this constant and the inline definition cite the canonical `FlyerIntakeStatus` Literal at `schemas.py:683-694`).
- `_CUSTOMER_BYPASS_ELIGIBLE_STATUSES` frozenset (`{"active", "trial"}`) — the account-lifecycle boundary per operator decision 2026-05-28 #1.
- `should_bypass_intake_for_clear_intent(text, customer, intake_session, *, has_media=False) -> Optional[str]` — returns the `bypass_reason` string when bypassing, else None. Composition over the 3 deployed classifiers + the two preconditions. **Note:** signature changed from `-> bool` to `-> Optional[str]` so callers don't need to re-classify to populate `bypass_reason` on the audit row.
- Small helper `_detect_inbound_script(text) -> Literal["latin","devanagari","tamil","other"]` (~20 LOC) — pure-function script detector using Unicode block ranges. Used by the wiring commit to populate `FlyerIntakeBypassed.inbound_script`.

**Tests (~100 LOC, ~16 cases):**
- 7 scenarios from §5 table + counter-example for the "edit this" alone case + edge cases (empty text; intake_session None; intake_session present without status field; customer present without status field; **expired/cancelled/suspended customer NEVER bypasses** regardless of signal — the operator decision 2026-05-28 #1 regression test).
- Pre-PR semantic-preservation regression: active/trial customer + `classify_flyer_intent=True` + no media + no `should_start_new_flyer_over_active` match → still bypasses (via the existing-active/trial fast path) — pins the reviewer 2 #7 trace.
- Script detector tests: 4 scripts + mixed-script (returns first non-latin run).
- Pure-function invariant: helper does NOT mutate inputs (Hermes-as-brain defensive check).

### Commit 3 — `feat(cf-router): wire bypass helper + emit two-row audit trail`
**Files:** `src/plugins/cf-router/hooks.py`, `tests/test_cf_router_flyer_routing.py`.
**Source (~70 LOC):**
- Replace the inline bypass conditional at `hooks.py:2378-2382` with a call to `should_bypass_intake_for_clear_intent`.
- On bypass:
  - Emit `FlyerIntakeBypassed` row via `_audit_append` / existing `actions.audit_intercepted` pattern. Populate `bypass_reason` (from helper return value), `has_media`, `customer_state`, `intake_session_status`, `inbound_script` (from `_detect_inbound_script`).
  - Open the `flyer_intake_bypass_shadow` context (mirrors `actions.begin_flyer_intent_shadow` / `actions.finalize_flyer_intent_shadow` / `actions.reset_flyer_intake_bypass_shadow` at `hooks.py:178-197`).
  - `return None`.
- In `_pre_gateway_dispatch_impl` finally block (line ~190-197): after `finalize_flyer_intent_shadow`, call `finalize_flyer_intake_bypass_shadow(hook_result=result)` — emits `FlyerIntakeBypassOutcome` row with `outcome` derived from `result`:
  - `result is None` → `"unrouted"`
  - `result is dict` AND originated from `_try_flyer_primary_intercept` post-bypass → `"routed_to_project"` (with `project_id` populated)
  - `result is dict` AND originated from any intermediate intercept → `"intermediate_intercept_handled"` (with `handler_intercept` populated)
- `_detect_inbound_script` helper imported from `actions.py`.

**Tests (~95 LOC, ~12 cases) — cf-router replay (extended scenarios):**
- F0108-shape (`edit_with_media` reason): asserts intake intercept returns `None`, `flyer_intake_bypassed` row written with correct fields, downstream `_try_flyer_primary_intercept` called, `flyer_intake_bypass_outcome` row written with `outcome="routed_to_project"` + `project_id` populated.
- 22.png-shape (`new_flyer_with_media` reason): same shape; reason field differs.
- Existing trial customer + flyer intent + no media (`existing_trial_customer_intent`): asserts the active/trial preservation path.
- **Expired customer + edit-with-media** (operator decision 2026-05-28 #1): asserts intake intercept does NOT bypass; wizard fires; no `flyer_intake_bypassed` row written.
- Protected status + clear intent: wizard fires; no bypass row.
- Brand-new sender + vague text: wizard fires; no bypass row.
- **Outcome=`unrouted` case** (silent-failure surface coverage): bypass fires but no downstream intercept handles → asserts `flyer_intake_bypass_outcome` row written with `outcome="unrouted"` so the silent failure surfaces in audit.
- **Outcome=`intermediate_intercept_handled` case**: bypass fires + a downstream intercept (e.g., scope_choice) handles → asserts outcome row with `handler_intercept="scope_choice"`.
- Counter-example: "edit this" alone (no edit_target) + media → asserts NOT bypass (`is_exact_reference_edit_request` requires both verb AND target).
- Pre-PR regression: existing intake tests for `trigger_flyer_intake` continue to pass.
- Script detector replay: Hindi/Devanagari edit-with-media bypass writes `inbound_script="devanagari"` for the regional-SMB telemetry use case.

---

## 7. Test plan (cross-commit assertions)

| Test layer | Asserts | File |
|---|---|---|
| Pure-function | bypass helper preconditions (protected, expired/cancelled), signal branches return correct `bypass_reason` Literal value, no input mutation | `tests/test_cf_router_flyer_routing.py` |
| Pure-function | `_detect_inbound_script` correctly identifies Latin/Devanagari/Tamil/other | `tests/test_cf_router_flyer_routing.py` |
| Schema | Both `FlyerIntakeBypassed` and `FlyerIntakeBypassOutcome` round-trip + discriminator routing + 5-value `bypass_reason` + 3-value `outcome` + `inbound_script` 4-value Literal | `tests/test_flyer_schemas.py` |
| cf-router replay | F0108-shape bypass → outcome="routed_to_project" with project_id populated | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Bypass + intermediate intercept handles → outcome="intermediate_intercept_handled" with handler_intercept populated | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Bypass + no downstream handler → outcome="unrouted" (silent-failure surface lit) | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Expired/cancelled customer + edit-with-media → NO bypass; wizard fires; zero bypass rows written | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Protected status + vague text → wizard fires (unchanged) | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Counter-example "edit this" alone (no edit_target) → NO bypass | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Hindi/Devanagari edit-with-media → bypass writes `inbound_script="devanagari"` | `tests/test_cf_router_flyer_routing.py` |
| Smoke (deploy gate) | `shift-agent-smoke-test.sh` imports `should_bypass_intake_for_clear_intent` + `FlyerIntakeBypassed` + `FlyerIntakeBypassOutcome` + `_detect_inbound_script` | `src/agents/shift/scripts/shift-agent-smoke-test.sh` |

**Regression discipline:** every existing test in `tests/test_cf_router_flyer_routing.py` and `tests/test_flyer_schemas.py` stays green. The bypass extends the existing line-2378 condition with two strictly-more-permissive signal branches (no customer-state gate) PLUS a strictly-more-restrictive expired/cancelled-blocking precondition — for any pre-PR input that bypassed, post-PR also bypasses (the new precondition only excludes expired/cancelled, which pre-PR already excluded via the active/trial gate). The non-bypass branch (line 2383+) is reached by a strict superset of pre-PR inputs.

---

## 8. Language policy (out of scope, already done)

The brainstorm's language piece — "complete requests without a language signal default to English; stored preferences + explicit non-English cues are honored" — is **already deployed**:

- `preferred_language: FlyerLanguage = "en"` schema default at 4 places (`schemas.py:1127, 1263, 1279, 1557`).
- `src/agents/flyer/intake.py:54` mirrors the same default.
- Stored customer preference flows automatically via `customer.preferred_language` reads in intake / primary intercept paths.

**What's NOT addressed in this PR:** explicit non-English cue DETECTION-AND-ACT (no helper that says "this Hindi message implies preferred_language='hi' for the new project"). Deferred — current behavior is "first interaction in non-English keeps customer.preferred_language='en' until the next state-write touches it."

**However, this PR DOES capture the raw script signal** (`FlyerIntakeBypassed.inbound_script` — operator decision 2026-05-28 #3): every bypassed message carries the inbound script in audit (`latin` / `devanagari` / `tamil` / `other`), so a follow-up PR can act on the accumulated regional-SMB data without a backfill. Reviewer 2 framing: Triveni's customer base is Hindi/Telugu/Tamil regional SMBs; silently anglo-defaulting their first edit-with-media message is a regression risk. Capturing the script keeps the door open for the detection follow-up at near-zero ship cost.

---

## 9. Open questions for design phase

1. **Chat-id hashing function.** Plan picks `chat_id_hash` matching the dominant `FlyerRecovery*` family convention. Design phase: confirm which hash function the family uses (likely `hashlib.sha256(chat_id.encode())[:N]` or similar) and reuse exactly — divergence breaks audit-log replay tooling that decodes by hash prefix.
2. **`flyer_intake_bypass_shadow` context pattern parity.** Plan §6 Commit 3 mirrors `flyer_intent_shadow` (`hooks.py:178-197`) for the outcome-row emit. Design phase: read the deployed shadow helpers (`begin_flyer_intent_shadow` + `finalize_flyer_intent_shadow` + `reset_flyer_intent_shadow`) end-to-end and confirm the same try/finally + token-passing pattern is appropriate. Specifically: does the existing shadow run inside `_pre_gateway_dispatch_impl`'s try/except/finally or somewhere else? Plan assumes inside; design phase pins.

### Pinned 2026-05-28 (was Q3): `FlyerIntakeBypassOutcome.outcome` derivation mechanism

Operator finding 2026-05-28: derivation must be pinned BEFORE build, not deferred.

**Decision: F-pattern regex extraction from `hook_result["reason"]`** + None-check. Concrete rules:

```python
_FLYER_PROJECT_ID_RE = re.compile(r"\bF\d{4,}\b")


def _derive_bypass_outcome(hook_result: Optional[dict]) -> tuple[str, str, str]:
    """Returns (outcome, project_id, handler_intercept) for the outcome row.

    Pinned per operator finding 2026-05-28. Build phase must NOT rely on
    string-pattern-matching beyond the F-pattern extraction below.
    """
    if hook_result is None:
        return ("unrouted", "", "")
    reason = str(hook_result.get("reason") or "")
    # Survey of hook_result["reason"] strings on origin/main HEAD f7ad477:
    # - Primary-intercept-success paths embed an F-pattern project_id
    #   (e.g., "cf-router flyer active: regenerated revised design for F0097").
    # - Intermediate-intercept paths return reason strings WITHOUT an
    #   F-pattern (e.g., "cf-router flyer reference scope auth_blocked",
    #   "cf-router flyer sample prompts sent",
    #   "cf-router flyer customer not active").
    # - This asymmetry is what makes the regex extraction reliable.
    m = _FLYER_PROJECT_ID_RE.search(reason)
    if m:
        return ("routed_to_project", m.group(0), "")
    # No project_id → intermediate intercept handled. Capture the reason
    # string (truncated to handler_intercept field limit) for triage.
    return ("intermediate_intercept_handled", "", reason[:80])
```

**Build-phase verification step (NEW, added per operator finding):** Before Commit 3 ships, run a replay over the last 100 `cf_router_intercepted` audit rows on main-vps and verify the derivation produces the expected outcomes — specifically, that every `flyer_primary_project_created` audit row has a corresponding `hook_result["reason"]` containing an F-pattern project_id. If any success-path reason string lacks the F-pattern, the regex misses that path → `intermediate_intercept_handled` mis-classification. Surface as a build-time test fixture loaded from the audit-log sample, not a runtime assumption.

**Risk acknowledged:** the derivation is reason-string-coupled. If a future PR changes a success path's reason string to drop the project_id, this derivation breaks silently. The build-phase replay test guards against current breakage; a follow-up could enforce via a `metadata: dict` field on `hook_result` populated explicitly. Out of scope here; logged as a known coupling.

(Decisions 2026-05-28: 5 `bypass_reason` Literal values; 2 audit-row variants — `FlyerIntakeBypassed` + `FlyerIntakeBypassOutcome` — immutable each; `inbound_script` captured; expired/cancelled customers DO NOT bypass; account-lifecycle boundary preserved; **outcome-derivation mechanism pinned to F-pattern extraction from `hook_result["reason"]`**. All operator decisions baked in.)

---

## 10. Out of scope

- **Explicit non-English-cue detection-and-act** from inbound text → separate follow-up PR (~30 LOC). This PR captures `inbound_script` for accumulation; detection-and-act is the next layer.
- **Reordering the cf-router intercept ladder** → the fix lives inside the intake intercept body; no ladder reorder.
- **Removing or shrinking `_INTAKE_PROTECTED_STATUSES`** → preserves wizard for actively-collecting-brief states.
- **Telemetry dashboard surfaces** for bypass-decision distribution → audit log carries the data; dashboard is a follow-up.
- "Should we ever auto-pick mode for sparse-but-clear requests?" → the bypass either fires (proceeds to primary intercept) or doesn't (wizard handles).
- **Backfill of historical wizard-stuck customers** → operator-driven; not in scope here.
- **Expired/cancelled/suspended customer bypass** → operator decision 2026-05-28 #1 — account-lifecycle boundary owns re-onboarding; wizard stays in control for these states. Helper precondition 2 enforces.

**Invariant pinned for build-time preservation (per reviewer 2 #8):**
- **`intake_session is None` + bypass returns None → existing line 2383-2384 `return None` preserved bit-for-bit.** The helper short-circuits via the customer-state precondition + signal-OR, then falls through; the existing `if not intake_session: return None` at line 2383 is unchanged.

**Customer-row lifecycle after bypass — VERIFIED IDEMPOTENT-FRIENDLY (per operator finding 2026-05-28):**

Earlier draft of this plan claimed that "brand-new sender bypasses ONCE; subsequent messages take the existing-customer path" — that claim is **false** and has been removed.

Reality (verified against `src/agents/flyer/scripts/create-flyer-project:614-658`):
- The post-bypass `_try_flyer_primary_intercept → create-flyer-project` path **does NOT create or activate a customer row**. It only reads customer state if present (`customer is not None and customer.status in {"trial","active"}` at line 614 hydrates profile facts) and writes `customer_id=customer.customer_id if customer is not None else ""` on the project record at line 658.
- No customer-store write occurs in `create-flyer-project`. Customer-row creation is owned by the **separate onboarding flow** (`src/agents/flyer/onboarding.py`) triggered by the trial-link click — that flow is NOT engaged by the bypass.
- Consequence: a brand-new sender (no customer row) sending repeated clear-intent messages from the same `chat_id` **will bypass every time**, each creating a new project with `customer_id=""`.

**Is this acceptable?** Yes for the F0108 / 22.png class — each message gets routed to a project (the right customer-facing outcome). Customer ends up with multiple drafts from multiple messages, which is what an operator wants when intent is clear. The customer-row creation continues to be owned by the trial-onboarding flow; the bypass doesn't disturb it.

**Operator visibility surface:** the `FlyerIntakeBypassed` row population per `chat_id_hash` over time gives operators a measurable signal — if the same chat_id bypasses N times without onboarding completing, that's the new operator-triage population. NOT a silent-failure regression because every bypass writes an audit row.

**What this means for §10 out-of-scope:** "promote brand-new senders to a customer row at bypass time" → out of scope here; that's a separate onboarding-flow concern. Surface to operator if telemetry shows repeated-bypass-without-onboarding becomes a population worth acting on.

---

## 11. Review section (post-PR)

(Reserved for PR-time evidence: actual LOC, test counts, replay outputs for F0108/22.png-shape, first N `flyer_intake_bypassed` audit rows observed in canary.)
