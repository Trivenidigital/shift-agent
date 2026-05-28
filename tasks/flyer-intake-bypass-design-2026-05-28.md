# Flyer Studio intake bypass — design

**Date:** 2026-05-28
**Builds on:** `tasks/flyer-intake-bypass-plan-2026-05-28.md` (commit `94eba70`)
**Branch:** `plan/flyer-intake-bypass-2026-05-28`
**Drift-check tag:** `extends-Hermes` (unchanged from plan)

This design pins the implementation details for the 3-commit build sequence: concrete code shapes for the helper + shadow context + audit variants, resolved values for the plan's remaining design-phase questions, test fixtures from live audit-log samples, deploy-gate additions, and operational runbook.

The plan describes WHAT changes; this design describes HOW — at function-body level.

---

## 0. Workspace + module-path conventions

**Workspace.** This design + plan live at `C:\projects\sme-agents-pr-zeta-1b\` on branch `plan/flyer-intake-bypass-2026-05-28` (off `origin/main` HEAD `f7ad477`). The sibling worktree `C:\projects\sme-agents\` is on the stale branch `codex/flyer-full-autonomous-recovery` (396 commits behind main) where the code shape is different — reading from there reproduces a known false-alarm pattern. Read code from the plan-branch worktree.

To verify: `cd C:/projects/sme-agents-pr-zeta-1b && git log --oneline HEAD -1` should show a HEAD ahead of `f7ad477`.

**Module-path conventions** carry forward from P0 #2 (deploy renames `src/agents/flyer/<name>.py` → `/opt/shift-agent/flyer_<name>.py`). Tests local-import via `from agents.flyer.<name>`; runtime cf-router imports via `from flyer_<name>` (with deployed-flat-fallback). Schemas always flat-import (`from schemas import ...` after `sys.path.insert`).

---

## 1. Hermes-first delta from plan

No new domains. The plan §2 analysis (9 reuse rows, 0 new primitives) holds. This design adds one concrete reuse confirmation:

- **Hash function**: `_short_hash(value)` at `cf-router/actions.py:526` = `hashlib.sha256(value.encode()).hexdigest()[:32]` — 32 hex chars. Plan §9 Q1 was open; now pinned to this exact function. Both new variants use the same `_short_hash(chat_id)` value for `chat_id_hash`.
- **Shadow context primitives**: `contextvars.Token`-based pattern at `cf-router/actions.py:543-602` and `:658+`. Plan §9 Q2 was open; now pinned — new `begin_flyer_intake_bypass_shadow` / `reset_flyer_intake_bypass_shadow` / `finalize_flyer_intake_bypass_shadow` mirror the deployed `*_intent_shadow` triple line-for-line in structure.

---

## 2. Q1 RESOLVED — `chat_id_hash` derivation

**Decision: reuse `_short_hash` directly.**

```python
# Pre-existing at cf-router/actions.py:526
def _short_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:32]
```

Both audit-row variants populate `chat_id_hash` via `_short_hash(chat_id)`. Produces 32 hex chars (well under the `max_length=120` constraint inherited from the `FlyerRecovery*` family convention).

**Why 32 hex (truncated SHA-256) rather than the full 64?** Matches deployed precedent at line 527 used by `FlyerHermesIntentDecision.chat_key_hash` and the cf-router shadow context. Shorter prefix keeps audit-log rows compact; collision risk on 32 hex (128 bits) is negligible at our row volume.

**Field-name divergence from precedent (acknowledged, intentional):** plan §4 picks `chat_id_hash` matching the dominant 6-site `FlyerRecovery*` family convention over `chat_key_hash` (1-site, `FlyerHermesIntentDecision`). The hash *function* is the same; only the *field name* follows the family with more callsites.

---

## 3. Q2 RESOLVED — `flyer_intake_bypass_shadow` context pattern parity

**Decision: mirror the deployed `flyer_intent_shadow` pattern line-for-line in structure.**

The deployed pattern at `cf-router/actions.py:543-700`:

```python
# Module-level
_FLYER_INTENT_CONTEXT: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "flyer_intent_context", default=None,
)


def begin_flyer_intent_shadow(*, text, chat_id, message_id, has_media, ...) -> contextvars.Token | None:
    # Build context dict; return token from _FLYER_INTENT_CONTEXT.set(context)
    ...

def reset_flyer_intent_shadow(token: contextvars.Token | None) -> None:
    if token is not None:
        _FLYER_INTENT_CONTEXT.reset(token)

def finalize_flyer_intent_shadow(*, hook_result, error, gateway) -> None:
    context = _FLYER_INTENT_CONTEXT.get()
    if context is None:
        return
    # Emit FlyerHermesIntentDecision audit row.
    ...
```

Wrapped in `_pre_gateway_dispatch_impl` (`hooks.py:167-197`):

```python
def pre_gateway_dispatch(event, gateway=None, session_store=None, **_kwargs):
    token = None
    result = None
    error = None
    try:
        # ... begin shadow if applicable ...
        token = actions.begin_flyer_intent_shadow(...)
        result = _pre_gateway_dispatch_impl(...)
        return result
    except Exception as exc:
        error = exc
        raise
    finally:
        try:
            actions.finalize_flyer_intent_shadow(hook_result=result, error=error, gateway=gateway)
        finally:
            actions.reset_flyer_intent_shadow(token)
```

**Design for the bypass mirror** — adds three new helpers + one new ContextVar:

```python
# cf-router/actions.py — new module-level
_FLYER_INTAKE_BYPASS_CONTEXT: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "flyer_intake_bypass_context", default=None,
)


def begin_flyer_intake_bypass_shadow(
    *,
    chat_id: str,
    message_id: str,
    bypass_reason: str,
    has_media: bool,
    customer_state: str,
    intake_session_status: str,
    inbound_script: str,
) -> contextvars.Token | None:
    """Open a bypass-tracking context that survives until finalize. Returns
    a Token the dispatch wrapper uses in finally for cleanup.

    Returns None when the bypass doesn't fire — caller passes None token
    through to reset, which is a no-op."""
    context = {
        "chat_id_hash": _short_hash(chat_id),
        "message_id_hash": _short_hash(message_id),
        "bypass_reason": str(bypass_reason or ""),
        "has_media": bool(has_media),
        "customer_state": str(customer_state or ""),
        "intake_session_status": str(intake_session_status or ""),
        "inbound_script": str(inbound_script or "latin"),
        "begin_ts": datetime.now(timezone.utc),
    }
    return _FLYER_INTAKE_BYPASS_CONTEXT.set(context)


def reset_flyer_intake_bypass_shadow(token: contextvars.Token | None) -> None:
    if token is not None:
        _FLYER_INTAKE_BYPASS_CONTEXT.reset(token)


def finalize_flyer_intake_bypass_shadow(*, hook_result: Optional[dict]) -> None:
    """Read the active bypass context (if any) + emit FlyerIntakeBypassOutcome.
    No-op when no bypass fired during the dispatch (context is None)."""
    context = _FLYER_INTAKE_BYPASS_CONTEXT.get()
    if context is None:
        return
    outcome, project_id, handler = _derive_bypass_outcome(hook_result)
    elapsed = datetime.now(timezone.utc) - context["begin_ts"]
    elapsed_ms = max(0, int(elapsed.total_seconds() * 1000))
    try:
        _emit_log_entry(FlyerIntakeBypassOutcome(
            ts=datetime.now(timezone.utc),
            chat_id_hash=context["chat_id_hash"],
            outcome=outcome,
            project_id=project_id,
            handler_intercept=handler,
            elapsed_ms=elapsed_ms,
        ))
    except Exception as exc:
        # Audit-emit MUST NOT raise into the dispatch finally. Mirrors the
        # existing finalize_flyer_intent_shadow exception-suppression at
        # actions.py:687+.
        sys.stderr.write(
            f"cf-router: flyer_intake_bypass_outcome emit failed (non-fatal): "
            f"{type(exc).__name__}: {exc}\n"
        )


def _derive_bypass_outcome(hook_result: Optional[dict]) -> tuple[str, str, str]:
    """Pinned per plan §9 (post-revision). F-pattern regex extraction from
    hook_result.reason. Returns (outcome, project_id, handler_intercept)."""
    if hook_result is None:
        return ("unrouted", "", "")
    reason = str(hook_result.get("reason") or "")
    m = _FLYER_PROJECT_ID_RE.search(reason)
    if m:
        return ("routed_to_project", m.group(0), "")
    return ("intermediate_intercept_handled", "", reason[:80])


_FLYER_PROJECT_ID_RE = re.compile(r"\bF\d{4,}\b")
```

**Wrapping change in `pre_gateway_dispatch`** (`hooks.py:167-197`):

```python
def pre_gateway_dispatch(event, gateway=None, session_store=None, **_kwargs):
    intent_token = None
    bypass_token = None  # NEW
    result = None
    error = None
    try:
        # ... existing intent shadow begin (line 178-184) ...
        intent_token = actions.begin_flyer_intent_shadow(...)
        result = _pre_gateway_dispatch_impl(event, gateway, session_store, **_kwargs)
        return result
    except Exception as exc:
        error = exc
        raise
    finally:
        try:
            actions.finalize_flyer_intent_shadow(hook_result=result, error=error, gateway=gateway)
        except Exception as shadow_exc:
            sys.stderr.write(f"... existing handler ...\n")
        # NEW — finalize bypass shadow AFTER intent shadow (intent shadow's
        # finalize doesn't consume the bypass-context state):
        try:
            actions.finalize_flyer_intake_bypass_shadow(hook_result=result)
        except Exception as bypass_exc:
            sys.stderr.write(
                f"cf-router: bypass shadow finalize failed (non-fatal): "
                f"{type(bypass_exc).__name__}: {bypass_exc}\n"
            )
        try:
            actions.reset_flyer_intent_shadow(intent_token)
        finally:
            actions.reset_flyer_intake_bypass_shadow(bypass_token)
```

**Where `bypass_token` gets set:** inside `_try_flyer_intake_intercept` on the bypass branch — the token returned by `begin_flyer_intake_bypass_shadow` flows back out to `pre_gateway_dispatch` via a small refactor (either stash it on a module-level variable like the intent shadow, OR return it from the intercept call). Build-phase decides; lean = stash via module-level helper because the intercept body returns `Optional[dict]` not a tuple.

---

## 4. Outcome derivation — build-time verification gate

Plan §9 pinned the F-pattern regex mechanism. Design adds the verification gate as a concrete test:

```python
# tests/test_cf_router_flyer_routing.py — new test
def test_outcome_derivation_against_recent_audit_sample(monkeypatch):
    """Build-phase gate: load a recent sample of cf_router_intercepted
    rows captured from main-vps; verify every flyer_primary_project_created
    row has a hook_result.reason containing an F-pattern project_id.

    If this test fails, a success path's reason string drifted and the
    F-pattern regex no longer reliably distinguishes routed_to_project
    from intermediate_intercept_handled. Surface the broken row + reason
    in the assertion message so the maintainer can pin the new pattern."""
    fixture_path = TESTS_DIR / "fixtures" / "intake_bypass_audit_sample.jsonl"
    rows = [json.loads(line) for line in fixture_path.read_text().splitlines() if line.strip()]
    project_created_rows = [r for r in rows if r.get("reason") == "flyer_primary_project_created"]
    assert project_created_rows, "fixture must contain at least one success row"
    bad: list[dict] = []
    for row in project_created_rows:
        # The audit-log's `detail` field contains the hook_result-shaped string.
        detail = str(row.get("detail") or "")
        if not _FLYER_PROJECT_ID_RE.search(detail):
            bad.append(row)
    assert not bad, (
        f"{len(bad)} flyer_primary_project_created row(s) without F-pattern in detail: {bad}"
    )
```

**How to populate the fixture:** during the build sequence (between Commits 2 and 3), pull the last 100 `cf_router_intercepted` rows where `reason == "flyer_primary_project_created"` from main-vps via the existing audit-log replay pattern (operator handed me the SSH two-step path). Store as `tests/fixtures/intake_bypass_audit_sample.jsonl`. The test fails fast if the fixture is missing — preventing the regex assumption from going to canary unverified.

---

## 5. Concrete code patterns per commit

### Commit 1 — schema variants

```python
# src/platform/schemas.py — appended near FlyerHermesIntentDecision (line 3753)

class FlyerIntakeBypassed(_BaseEntry):
    """Decision-time audit: intake bypass fired. Emitted by
    _try_flyer_intake_intercept immediately on bypass.

    Pairs with FlyerIntakeBypassOutcome (emitted by the dispatch finally
    block) to give operators a two-row decision-then-outcome trail per
    chat_id_hash without timestamp-window correlation."""
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
    customer_state: str = Field(default="", max_length=40)
    intake_session_status: str = Field(default="", max_length=80)
    inbound_script: Literal["latin", "devanagari", "tamil", "other"] = "latin"


class FlyerIntakeBypassOutcome(_BaseEntry):
    """Outcome-time audit: what happened after intake-bypass fired. Emitted
    by _pre_gateway_dispatch's finally block via the bypass shadow's
    finalize call (mirrors flyer_intent_shadow's finalize)."""
    type: Literal["flyer_intake_bypass_outcome"] = "flyer_intake_bypass_outcome"
    chat_id_hash: str = Field(min_length=1, max_length=120)
    outcome: Literal[
        "routed_to_project",
        "unrouted",
        "intermediate_intercept_handled",
    ]
    project_id: str = Field(default="", max_length=40)
    handler_intercept: str = Field(default="", max_length=80)
    elapsed_ms: int = Field(default=0, ge=0)
```

LogEntry Union update (append next to `FlyerHermesIntentDecision` at line ~4685+):

```python
Annotated[FlyerHermesIntentDecision, Tag("flyer_hermes_intent_decision")],
# P0 #2-adjacent — intake bypass decision + outcome audit pair (2026-05-28)
Annotated[FlyerIntakeBypassed, Tag("flyer_intake_bypassed")],
Annotated[FlyerIntakeBypassOutcome, Tag("flyer_intake_bypass_outcome")],
```

`__all__` additions:

```python
"FlyerIntakeBypassed", "FlyerIntakeBypassOutcome",
```

### Commit 2 — helper + script detector

```python
# cf-router/actions.py — appended near other intent helpers

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
    """Plan §5 helper. Returns the bypass_reason Literal value when bypassing,
    else None. Pure-function; composes deployed classifiers; never mutates
    inputs. Operator-pinned 2026-05-28 decisions baked in: expired/cancelled
    customers do NOT bypass (account-lifecycle boundary)."""
    # Precondition 1: protected statuses block bypass.
    status = str((intake_session or {}).get("status") or "")
    if status in _INTAKE_PROTECTED_STATUSES:
        return None

    # Precondition 2: account-lifecycle boundary. Expired/cancelled/suspended
    # customers stay in wizard; brand-new senders (customer is None) are eligible.
    if customer is not None and customer.get("status") not in _CUSTOMER_BYPASS_ELIGIBLE_STATUSES:
        return None

    # Signal 1: edit-with-media is unambiguous.
    if is_exact_reference_edit_request(text, has_media=has_media):
        return "edit_with_media"

    # Signals 2 + 3: clear new-flyer request. Split on media for replay.
    if should_start_new_flyer_over_active(text, has_media=has_media):
        return "new_flyer_with_media" if has_media else "new_flyer_text_only"

    # Signals 4 + 5: existing-customer fast path. Trial vs active split for triage.
    intent_match, _reasons = classify_flyer_intent(text)
    if intent_match and customer:
        customer_status = customer.get("status")
        if customer_status == "trial":
            return "existing_trial_customer_intent"
        if customer_status == "active":
            return "existing_active_customer_intent"

    return None


# Inbound-script detector — operator decision 2026-05-28 #3 (regional-SMB telemetry)

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_TAMIL_RE = re.compile(r"[஀-௿]")
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")


def _detect_inbound_script(text: str) -> str:
    """Pure-function script detector. Returns the dominant non-latin script
    name from {"latin","devanagari","tamil","other"}. Default "latin" when
    the text is pure ASCII or empty.

    Used by the bypass wiring to populate FlyerIntakeBypassed.inbound_script
    so a follow-up PR can act on accumulated regional-SMB data without
    backfill."""
    body = str(text or "")
    if not body or not _NON_ASCII_RE.search(body):
        return "latin"
    if _DEVANAGARI_RE.search(body):
        return "devanagari"
    if _TAMIL_RE.search(body):
        return "tamil"
    return "other"
```

### Commit 3 — wiring (intake intercept + dispatch wrapper)

```python
# cf-router/hooks.py — replacing lines 2378-2382 inside _try_flyer_intake_intercept

# Plan §5 + §5b — replace the inline bypass with the named helper.
bypass_reason = actions.should_bypass_intake_for_clear_intent(
    text=text,
    customer=customer,
    intake_session=intake_session,
    has_media=bool(media_path),
)
if bypass_reason is not None:
    inbound_script = actions._detect_inbound_script(text)
    # Decision-time audit row.
    try:
        actions.audit_intercepted(
            reason="flyer_intake_bypassed",
            chat_id=chat_id,
            subprocess_rc=0,
            detail=(
                f"bypass_reason={bypass_reason}; has_media={'1' if media_path else '0'}; "
                f"customer_state={(customer or {}).get('status') or ''}; "
                f"intake_session_status={(intake_session or {}).get('status') or ''}; "
                f"inbound_script={inbound_script}; sender_role={role}"
            ),
        )
    except Exception:
        # Audit-emit must not block the bypass.
        pass
    # Open bypass-tracking shadow — finalize emits the outcome row.
    actions.note_flyer_intake_bypass_active(
        chat_id=chat_id,
        message_id=message_id,
        bypass_reason=bypass_reason,
        has_media=bool(media_path),
        customer_state=str((customer or {}).get("status") or ""),
        intake_session_status=str((intake_session or {}).get("status") or ""),
        inbound_script=inbound_script,
    )
    return None
```

**`note_flyer_intake_bypass_active` helper** — module-level stash so the intercept body doesn't need to plumb the token back to the dispatch wrapper. Mirrors how the intent-shadow stash works at `actions.py:543+`:

```python
# cf-router/actions.py — wires into the existing pattern

_PENDING_BYPASS_TOKEN: contextvars.ContextVar[contextvars.Token | None] = contextvars.ContextVar(
    "pending_flyer_intake_bypass_token", default=None,
)


def note_flyer_intake_bypass_active(
    *,
    chat_id: str,
    message_id: str,
    bypass_reason: str,
    has_media: bool,
    customer_state: str,
    intake_session_status: str,
    inbound_script: str,
) -> None:
    token = begin_flyer_intake_bypass_shadow(
        chat_id=chat_id,
        message_id=message_id,
        bypass_reason=bypass_reason,
        has_media=has_media,
        customer_state=customer_state,
        intake_session_status=intake_session_status,
        inbound_script=inbound_script,
    )
    _PENDING_BYPASS_TOKEN.set(token)


def consume_pending_flyer_intake_bypass_token() -> contextvars.Token | None:
    token = _PENDING_BYPASS_TOKEN.get()
    _PENDING_BYPASS_TOKEN.set(None)
    return token
```

Dispatch wrapper (`pre_gateway_dispatch` at `hooks.py:167-197`) consumes the pending token at finally time:

```python
finally:
    try:
        actions.finalize_flyer_intent_shadow(...)
    except Exception:
        ...
    try:
        actions.finalize_flyer_intake_bypass_shadow(hook_result=result)
    except Exception:
        ...
    try:
        actions.reset_flyer_intent_shadow(intent_token)
    finally:
        actions.reset_flyer_intake_bypass_shadow(
            actions.consume_pending_flyer_intake_bypass_token()
        )
```

---

## 6. Test fixtures

**Helper test fixtures** (Commit 2 tests):

```python
def _customer(status: str = "trial") -> dict:
    return {"customer_id": "CUST0001", "status": status, "preferred_language": "en"}

def _intake(status: str = "choosing_mode") -> dict:
    return {"status": status}

@pytest.mark.parametrize("text,customer,intake_session,has_media,expected", [
    # F0108 / 22.png canonical
    ("edit this to add Saturday hours", None, _intake("choosing_mode"), True,
     "edit_with_media"),
    # New customer + new-flyer + media
    ("Create flyer for Dosa Night", None, _intake("choosing_mode"), True,
     "new_flyer_with_media"),
    # Existing trial customer + intent + no media
    ("I want a flyer for next week", _customer("trial"), None, False,
     "existing_trial_customer_intent"),
    # Existing active customer + intent + no media
    ("I want a flyer for next week", _customer("active"), None, False,
     "existing_active_customer_intent"),
    # Protected — wizard
    ("Create flyer for Dosa Night", _customer("trial"), _intake("guided_collecting_goal"), True,
     None),
    # Brand-new + vague — wizard
    ("hi", None, _intake("choosing_language"), False, None),
    # Counter-example: "edit this" alone (no edit_target) — NOT bypass
    ("edit this", None, _intake("choosing_mode"), True, None),
    # Operator decision 2026-05-28 #1 — expired customer never bypasses
    ("edit this to fix the time", _customer("expired"), None, True, None),
    ("edit this to fix the time", _customer("cancelled"), None, True, None),
    ("edit this to fix the time", _customer("suspended"), None, True, None),
])
def test_should_bypass_intake_for_clear_intent(text, customer, intake_session, has_media, expected):
    actions = _load_actions()
    assert actions.should_bypass_intake_for_clear_intent(
        text, customer, intake_session, has_media=has_media,
    ) == expected
```

**Script detector test fixtures** (Commit 2 tests):

```python
@pytest.mark.parametrize("text,expected", [
    ("Create flyer for Dosa Night", "latin"),
    ("", "latin"),
    ("Diwali के लिए flyer बनाओ", "devanagari"),  # Hindi
    ("தோசை இரவுக்கான flyer", "tamil"),  # Tamil
    ("¡Crea un volante!", "other"),  # Spanish — non-ASCII but not Hindi/Tamil
    ("Mixed Latin + देवनागरी", "devanagari"),  # mixed prefers Devanagari
])
def test_detect_inbound_script(text, expected):
    actions = _load_actions()
    assert actions._detect_inbound_script(text) == expected
```

**cf-router replay fixtures** (Commit 3 tests):

```python
def test_intake_bypass_writes_decision_and_outcome_pair(monkeypatch, tmp_path):
    """F0108-shape end-to-end: helper returns "edit_with_media" →
    intake intercept emits decision row, returns None → downstream
    primary intercept creates project → dispatch finalize emits
    outcome row with project_id."""
    # ... seed projects.json + customers.json ...
    # ... monkeypatch _try_flyer_primary_intercept to return
    #     {"action": "skip", "reason": "cf-router flyer primary project created F0108"} ...
    # ... invoke pre_gateway_dispatch with mock event ...
    # Assert:
    audit_rows = read_audit_log(tmp_path)
    assert audit_rows[0]["type"] == "flyer_intake_bypassed"
    assert audit_rows[0]["bypass_reason"] == "edit_with_media"
    assert audit_rows[0]["inbound_script"] == "latin"
    assert audit_rows[-1]["type"] == "flyer_intake_bypass_outcome"
    assert audit_rows[-1]["outcome"] == "routed_to_project"
    assert audit_rows[-1]["project_id"] == "F0108"
    assert audit_rows[-1]["chat_id_hash"] == audit_rows[0]["chat_id_hash"]


def test_intake_bypass_unrouted_when_downstream_declines(monkeypatch, tmp_path):
    """Silent-failure surface: bypass fires; no downstream intercept handles;
    finalize emits outcome=unrouted so operator sees the gap in audit."""
    # ... monkeypatch ALL downstream intercepts to return None ...
    audit_rows = read_audit_log(tmp_path)
    assert audit_rows[-1]["type"] == "flyer_intake_bypass_outcome"
    assert audit_rows[-1]["outcome"] == "unrouted"
    assert audit_rows[-1]["project_id"] == ""


def test_intake_bypass_intermediate_intercept_handles(monkeypatch, tmp_path):
    """Edge: bypass fires; scope_choice intercept handles → outcome row
    has handler_intercept populated with the truncated reason."""
    # ... monkeypatch scope_choice to return {"action":"skip","reason":"cf-router flyer reference scope auth_blocked"} ...
    audit_rows = read_audit_log(tmp_path)
    assert audit_rows[-1]["outcome"] == "intermediate_intercept_handled"
    assert "scope" in audit_rows[-1]["handler_intercept"]
```

---

## 7. Deploy gates — `shift-agent-smoke-test.sh` additions

```bash
# In src/agents/shift/scripts/shift-agent-smoke-test.sh (after the existing flyer probes)

python3 -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
sys.path.insert(0, '/opt/shift-agent/platform')
from schemas import FlyerIntakeBypassed, FlyerIntakeBypassOutcome
# cf-router actions deploy flat under /opt/shift-agent/cf_router_actions.py per
# existing convention; if naming differs, the import surfaces the convention
# mismatch immediately.
from cf_router_actions import (
    should_bypass_intake_for_clear_intent,
    _detect_inbound_script,
    begin_flyer_intake_bypass_shadow,
    finalize_flyer_intake_bypass_shadow,
)
print('intake-bypass symbols loadable')
" || { echo 'FAIL: intake-bypass symbol import'; exit 1; }
```

**Cross-check before merge:** confirm `shift-agent-deploy.sh` includes the cf-router actions.py in its tarball-staging step; verify the import path on the VPS matches what the smoke test uses.

---

## 8. Operational runbook — what operator sees on first bypass

**Real-time event sequence:**

1. Customer (new sender, no customer row) sends "edit this to add Saturday hours" + attached flyer image to the bot.
2. cf-router intercept ladder fires; intake intercept reaches the bypass helper.
3. Helper returns `"edit_with_media"` — bypass fires.
4. Decision audit row written via `audit_intercepted(reason="flyer_intake_bypassed", ...)`:
   ```
   {ts, type: "cf_router_intercepted", reason: "flyer_intake_bypassed",
    chat_id, detail: "bypass_reason=edit_with_media; has_media=1; ...; inbound_script=latin"}
   ```
   Plus the structured `FlyerIntakeBypassed` row in the discriminated-union LogEntry surface.
5. Pending bypass token stashed in `_PENDING_BYPASS_TOKEN`.
6. Intake intercept returns `None`. Dispatch ladder continues.
7. 6 intermediate intercepts pass through (verified none read intake state).
8. `_try_flyer_active_project_intercept` (no active project) → None.
9. `should_start_new_flyer_over_active`-gated call (line 326) → `_try_flyer_primary_intercept(force_new=True)` creates project F0108 + dispatches the source-edit subprocess.
10. Primary intercept returns `{"action": "skip", "reason": "cf-router flyer primary project created F0108"}`.
11. Dispatch wrapper's finally runs: `finalize_flyer_intake_bypass_shadow(hook_result=result)` reads the context, derives outcome via `_derive_bypass_outcome` → `("routed_to_project", "F0108", "")`, emits `FlyerIntakeBypassOutcome` row.

**Where to look:**

- Audit log: `grep "flyer_intake_bypass" /opt/shift-agent/logs/decisions.log`
- Sample correlation: each chat_id_hash should have a `flyer_intake_bypassed` row followed by a `flyer_intake_bypass_outcome` row within the same dispatch (ts within seconds).
- Healthy distribution: `outcome=routed_to_project` should dominate; `outcome=intermediate_intercept_handled` should be rare; `outcome=unrouted` should be near-zero (it's the silent-failure surface — investigate any spike).

---

## 9. Risk register + rollback

| Risk | Impact | Mitigation |
|---|---|---|
| Regex outcome-derivation breaks if a success-path reason string drops the F-pattern | `outcome=intermediate_intercept_handled` mis-classification; bypass→project trail breaks | Build-phase replay test against captured audit sample (§4); future PR can promote to `metadata: dict` on `hook_result` |
| Bypass fires repeatedly for brand-new senders without onboarding ever completing | Operator triage population grows | Audit-row population per chat_id_hash gives measurable signal; "promote at bypass time" is an explicit follow-up |
| Inbound-script detector misclassifies mixed-script text | Wrong telemetry; doesn't affect routing | Test fixtures cover mixed-script cases; detection is non-blocking; data accumulates for follow-up; misclassification = data noise, not customer regression |
| `_PENDING_BYPASS_TOKEN` not consumed by finally (token leaks) | Stale context in next dispatch | `consume_pending_flyer_intake_bypass_token()` always sets None even on error; reset always runs in finally |
| Shadow-context exception propagates into dispatch flow | Plugin crash blocks LLM dispatch | All audit-emit calls are wrapped in `try/except`; exceptions logged to stderr only (mirrors existing `finalize_flyer_intent_shadow` pattern at actions.py:687) |

**Rollback plan:**
1. `git revert <commit 3 SHA>` (intake intercept + dispatch wrapper changes).
2. Optionally revert Commits 2 + 1 if the helper / schema additions are also rolled back — but they're additive-only and safe to leave.
3. After revert, behavior reverts to pre-PR (line 2378-2382 inline conditional restored). Brand-new senders again hit the wizard for edit-with-media; F0108 / 22.png class re-stuck.

---

## 10. Implementation order (locked)

1. **Commit 1** (schema variants) — `FlyerIntakeBypassed` + `FlyerIntakeBypassOutcome`. Lands first because Commit 3 emits these.
2. **Commit 2** (helper + script detector) — `should_bypass_intake_for_clear_intent` + `_detect_inbound_script`. Depends on Commit 1's variants only for the build-time symbol-import smoke; functionally independent.
3. **Commit 3** (wiring) — intake intercept body replacement + shadow context helpers + dispatch wrapper finally update. Depends on both prior commits.

Each commit ships green tests independently. Commit 3 includes the live-audit-sample fixture pull as a build-time gate.

---

## 11. Open items deferred to build phase

- **Exact `_emit_log_entry` helper location** in `cf-router/actions.py` for the outcome emit. The pattern mirrors `finalize_flyer_intent_shadow`'s emit call but may need refactoring. Confirm at build-start.
- **Deploy-tarball naming for cf-router actions.py.** Smoke test assumes `cf_router_actions.py` flat-deploy; if the actual deploy uses a different convention, smoke test needs adjustment. Quick check via `ls /opt/shift-agent/` on main-vps.
- **`audit_intercepted` vs structured `LogEntry` emit.** Plan §6 Commit 3 uses both: `audit_intercepted` for the existing `cf_router_intercepted` row (unchanged shape) PLUS a structured `FlyerIntakeBypassed` emit via the same `_audit_append` chokepoint. Verify both surfaces are populated at build time to avoid double-write (or single-write with bridging).
- **Audit-sample fixture content** — the replay gate at §4 needs ~100 real audit rows. Build phase pulls + commits the sample.

---

## 12. Review section (post-PR)

(Reserved for PR-time evidence: actual LOC, test counts, first N `flyer_intake_bypassed` / `flyer_intake_bypass_outcome` row pairs observed in canary, distribution of `bypass_reason` Literal values, distribution of `inbound_script` values, any `outcome=unrouted` incidents triaged.)
