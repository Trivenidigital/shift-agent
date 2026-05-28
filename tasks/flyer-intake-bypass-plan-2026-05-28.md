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
5. **`[net-new]`** Bypass helper evaluates (OR-of):
   - `actions.is_exact_reference_edit_request(text, has_media)` — explicit edit + media
   - `actions.should_start_new_flyer_over_active(text, has_media)` — clear new-flyer signal
   - `(customer.status in {"active","trial"}) AND classify_flyer_intent(text)[0]` — existing active-customer fast path preserved
   - AND `intake_session.status NOT in _INTAKE_PROTECTED_STATUSES` (guard: actively-collecting-brief states never bypass)
6. **`[net-new]`** If bypass: write `FlyerIntakeBypassed` audit row + `return None` so the intercept ladder proceeds to `_try_flyer_active_project_intercept` (line 303) and `_try_flyer_primary_intercept` (line 327/444) — both fully deployed.
7. `[Hermes]` If not bypass: existing intake intercept body continues — `trigger_flyer_intake` wizard flow unchanged.
8. `[Hermes]` On bypass: `_try_flyer_primary_intercept` runs the deployed classifier composition + creates project or source-edit job.
9. `[Hermes]` `create-flyer-project` writes the new project. `customer_language` defaults to `"en"` per existing schema default (`schemas.py:1557`); stored customer preference honored via existing intake → customer profile read path.
10. `[Hermes]` cf-router post-subprocess branch dispatches concept previews via `_dispatch_concept_preview_send` (P0 #2 Commit 4 substrate).
11. `[Hermes]` Customer receives draft + correction prompt.

**Step count:** 11 total. `[Hermes]`: 7. `[net-new]`: 3 (steps 4, 5, 6 cluster around the helper + the audit + the early-return wiring).

**Red-flag check:** 3/11 = 27% net-new. Comfortable under the half-threshold; matches Hermes-first norms.

---

## 4. Drift-rule self-checks (read deployed code first)

| Work type | File read | Evidence |
|---|---|---|
| Routing / dispatcher | `src/plugins/cf-router/hooks.py` lines 260-340 + 2354-2445 | Intercept ladder order pinned at lines 263-331; intake intercept at line 272 is the gate. `_try_flyer_intake_intercept` body shows the existing partial bypass at 2378-2382 (gated on `customer.status in {"active","trial"}`) and the protected-status guard set at 2367-2376. |
| Classifier surface | `src/plugins/cf-router/actions.py` lines 1244 (`classify_flyer_intent`), 1464 (`should_start_new_flyer_over_active`), 1499 (`is_exact_reference_edit_request`) | All three signatures confirmed: text + `has_media` keyword arg; pure functions. `is_exact_reference_edit_request` returns `False` for `has_media=False` early (line 1507-1508), so the helper's edit-with-media call is structurally correct. |
| Schema work | `src/platform/schemas.py` lines 666 (`FlyerLanguage`), 1127/1263/1279/1557 (`preferred_language: FlyerLanguage = "en"`) | English-as-default-language is already a deployed schema convention in 4 places. No new schema field needed for the language piece of the brainstorm — `customer_language="en"` happens automatically for complete bypass cases. |
| Schema — LogEntry variant | `src/platform/schemas.py` near the P0 #2 audit variants (`FlyerQASeverityClassified` etc.) | Pattern locked: subclass `_BaseEntry`, snake_case `type: Literal[...]`, F-pattern project_id where applicable, `ts` inherited (no separate timestamp field). `FlyerIntakeBypassed` follows the same convention. |
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


def should_bypass_intake_for_clear_intent(
    text: str,
    customer: Optional[dict],
    intake_session: Optional[dict],
    *,
    has_media: bool = False,
) -> bool:
    """Returns True when the intake wizard should be skipped because the
    customer's intent is structurally clear.

    Hermes-as-brain compliance: this helper does NOT classify on its own.
    It composes three deployed classifiers (classify_flyer_intent,
    is_exact_reference_edit_request, should_start_new_flyer_over_active)
    + the protected-status guard. Hermes/classifiers decide; helper
    composes; intercept executes.

    The setup-vs-intent principle: setup must never outrank intent when
    the user has already sent enough information to act. Brand-new
    senders + onboarding-state customers + active/trial customers ALL
    bypass when intent is clear; only the actively-collecting-brief
    protected states stay in the wizard.
    """
    # Protected statuses ALWAYS stay in the wizard (operator is mid-collection).
    status = str((intake_session or {}).get("status") or "")
    if status in _INTAKE_PROTECTED_STATUSES:
        return False

    # Edit-with-media is the most unambiguous bypass signal.
    if is_exact_reference_edit_request(text, has_media=has_media):
        return True

    # Clear new-flyer request (incl. media-backed templates/menus).
    if should_start_new_flyer_over_active(text, has_media=has_media):
        return True

    # Existing-customer fast path: active/trial + flyer intent.
    # Preserves pre-PR behavior of the lines 2378-2382 bypass.
    intent_match, _reasons = classify_flyer_intent(text)
    if (
        intent_match
        and customer
        and customer.get("status") in {"active", "trial"}
    ):
        return True

    return False
```

**Five test cases pin the behavior:**

| Scenario | text + has_media | customer | intake_session.status | bypass? |
|---|---|---|---|---|
| F0108 / 22.png — new customer + edit + media | "edit this to ..." + media | None | `choosing_mode` (or any non-protected) | **True** (via `is_exact_reference_edit_request`) |
| New customer + clear new-flyer text + media | "Create flyer for Dosa Night" + media | None | `choosing_mode` | **True** (via `should_start_new_flyer_over_active`) |
| Existing active customer + flyer intent + no media | "I want a flyer for next week" | `{status: active}` | None | **True** (existing fast path preserved) |
| Customer in protected status + clear intent | "Create flyer for Dosa Night" | any | `guided_collecting_goal` | **False** (protected) |
| Brand-new sender + vague text + no media | "hi" | None | `choosing_language` | **False** (no signal → wizard) |

---

## 6. Build sequence (3 commits, ~155 LOC source + ~220 LOC test)

### Commit 1 — `feat(flyer): FlyerIntakeBypassed audit variant`
**Files:** `src/platform/schemas.py`, `tests/test_flyer_schemas.py`.
**Source (~25 LOC):**
- `FlyerIntakeBypassed(_BaseEntry)` — snake_case type literal `"flyer_intake_bypassed"`; fields: `project_id_hint: str = Field(default="", max_length=40)` (empty pre-creation), `chat_id_hash: str = Field(min_length=1, max_length=64)`, `bypass_reason: Literal["edit_with_media", "new_flyer_request", "existing_active_customer"]`, `has_media: bool`, `customer_state: str = Field(default="", max_length=40)`, `intake_session_status: str = Field(default="", max_length=80)`. `ts` inherited.
- Add to `LogEntry` Union next to P0 #2 variants.
- Add to `__all__`.

**Tests (~40 LOC, ~6 cases):**
- Round-trip, each bypass_reason Literal value, `extra="forbid"`, discriminator routing via `TypeAdapter(LogEntry)`, `__all__` export.

### Commit 2 — `feat(cf-router): should_bypass_intake_for_clear_intent helper`
**Files:** `src/plugins/cf-router/actions.py`, `tests/test_cf_router_flyer_routing.py`.
**Source (~60 LOC):**
- `_INTAKE_PROTECTED_STATUSES` frozenset (mirrors the inline set at `hooks.py:2367-2376` — same membership).
- `should_bypass_intake_for_clear_intent(text, customer, intake_session, *, has_media=False) -> bool` — composition over the 3 deployed classifiers.

**Tests (~80 LOC, ~12 cases):**
- Five table-driven scenarios from §5 above + edge cases (empty text, both customer + intake_session None, intake_session present without status field, customer with status "expired"/"cancelled" non-fast-path).
- Pure-function invariant: helper does not mutate inputs (Hermes-as-brain defensive check — if this regresses, the helper became a brain).

### Commit 3 — `feat(cf-router): wire bypass helper into intake intercept`
**Files:** `src/plugins/cf-router/hooks.py`, `tests/test_cf_router_flyer_routing.py`.
**Source (~25 LOC):**
- Replace the inline bypass conditional at `hooks.py:2378-2382` with a call to `should_bypass_intake_for_clear_intent`.
- On bypass: emit `FlyerIntakeBypassed` audit row via existing `_audit_append` / `actions.audit_intercepted` pattern with `bypass_reason` derived from which classifier matched. Return `None`.
- The audit emit is structured so operators can grep `decisions.log` for `flyer_intake_bypassed` to see the bypass population.

**Tests (~50 LOC, ~8 cases) — cf-router replay:**
- F0108-shape: brand-new customer + edit-with-media → asserts intake intercept returns `None` + `flyer_intake_bypassed` audit row written + ladder proceeds (mock `_try_flyer_primary_intercept` to verify it's called next).
- 22.png-shape: new customer + complete request + media → asserts bypass via `should_start_new_flyer_over_active`.
- Existing active customer + flyer intent → bypass via the preserved fast path.
- Protected status + clear intent → wizard fires (no bypass).
- New sender + vague text → wizard fires (no bypass).
- Pre-PR regression: existing intake tests for `trigger_flyer_intake` continue to pass.

---

## 7. Test plan (cross-commit assertions)

| Test layer | Asserts | File |
|---|---|---|
| Pure-function | bypass helper conditions, no input mutation | `tests/test_cf_router_flyer_routing.py` |
| Schema | `FlyerIntakeBypassed` round-trip + discriminator routing | `tests/test_flyer_schemas.py` |
| cf-router replay | F0108-shape + 22.png-shape both reach primary intercept | `tests/test_cf_router_flyer_routing.py` |
| cf-router replay | Protected status + vague text still hit the wizard | `tests/test_cf_router_flyer_routing.py` |
| Smoke (deploy gate) | `shift-agent-smoke-test.sh` imports `should_bypass_intake_for_clear_intent` + `FlyerIntakeBypassed` to verify symbols load on VPS | `src/agents/shift/scripts/shift-agent-smoke-test.sh` |

**Regression discipline:** every existing test in `tests/test_cf_router_flyer_routing.py` and `tests/test_flyer_schemas.py` stays green. The bypass extends the existing line-2378 condition with two strictly-more-permissive OR clauses — no existing pass-through path becomes blocked. The non-bypass branch (line 2383+) is reached by exactly the same input set that would have failed the old condition.

---

## 8. Language policy (out of scope, already done)

The brainstorm's language piece — "complete requests without a language signal default to English; stored preferences + explicit non-English cues are honored" — is **already deployed**:

- `preferred_language: FlyerLanguage = "en"` schema default at 4 places (`schemas.py:1127, 1263, 1279, 1557`).
- `src/agents/flyer/intake.py:54` mirrors the same default.
- Stored customer preference flows automatically via `customer.preferred_language` reads in intake / primary intercept paths.

**What's NOT addressed in this PR:** explicit non-English cue DETECTION in the inbound text (no helper that says "this Hindi message implies preferred_language='hi' for the new project"). Deferred — current behavior is "first interaction in non-English keeps customer.preferred_language='en' until the next state-write touches it." If you want active detection from cue text, that's a separate ~30-LOC helper + integration. Flagged in §10.

---

## 9. Open questions for design phase

1. **Audit row enrichment.** `FlyerIntakeBypassed.project_id_hint` is empty at audit-write time (project doesn't exist yet). Should we also emit a `bypass_routed_to_project` row LATER when the primary intercept creates the project, so the bypass→project correlation is reconstructable? Lean: yes, but defer to a follow-up (today's audit covers the bypass decision; the project-creation audit row already exists).
2. **Chat-id hashing.** `FlyerIntakeBypassed.chat_id_hash` mirrors `FlyerHermesIntentDecision`'s PII-light pattern. Design phase: confirm same hashing function reused (`hashlib.sha256(chat_id.encode())[:32]` or similar — TBD by existing precedent).
3. **`bypass_reason` granularity.** Three values vs more. Current draft: `"edit_with_media"`, `"new_flyer_request"`, `"existing_active_customer"`. Could split `new_flyer_request` further (media-backed vs text-only) for finer telemetry. Lean: keep three; add granularity later if telemetry surfaces a need.

---

## 10. Out of scope

- Explicit non-English-cue detection from inbound text → separate PR if needed (~30 LOC).
- Reordering the cf-router intercept ladder → the fix lives inside the intake intercept body; no ladder reorder.
- Removing or shrinking `_INTAKE_PROTECTED_STATUSES` → preserves wizard for actively-collecting-brief states.
- Telemetry dashboard surfaces for bypass-decision distribution → audit log carries the data; dashboard is a follow-up.
- "Should we ever auto-pick mode for sparse-but-clear requests?" → out of scope; the bypass either fires (proceeds to primary intercept) or doesn't (wizard handles).
- Backfill of historical wizard-stuck customers → operator-driven; not in scope here.

---

## 11. Review section (post-PR)

(Reserved for PR-time evidence: actual LOC, test counts, replay outputs for F0108/22.png-shape, first N `flyer_intake_bypassed` audit rows observed in canary.)
