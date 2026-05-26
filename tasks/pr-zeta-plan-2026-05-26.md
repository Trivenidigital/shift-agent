# PR-ζ — ActionExecutionContext + null-context allowlist + lint hookup

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:**
- `ActionExecutionContext` Pydantic model (`src/platform/schemas.py`)
- `_RegulatedSendMissingActionContext` + `_RegulatedSendLintViolation` LogEntry variants
- `SAFE_IO_NULL_CONTEXT_ALLOWLIST` frozenset + `_resolve_caller_script_name()` introspection helper
- `tests/test_send_chokepoint_null_context_allowlist.py` static gate (callsite-AST scanner)

**Branch:** `feat/pr-zeta-action-execution-context` off `origin/main` @ `628e7d1`

**Status:** PLAN — REV 2 (post-reviewer iteration). Two reviewers landed (strategy/judgment + structural/code); their findings drive REV 2.

## REV 2 changelog (2026-05-26)

| Finding | Reviewer | Severity | Change applied |
|---|---|---|---|
| `actions.py` blanket-allowlist ships zero load-bearing protection on the Flyer billing surface PR-ζ exists for | Strategy | scope | NEW §F8: migrate the `change_plan` callsite in PR-ζ. Other cf-router callsites stay allowlisted pending PR-ζ.1. |
| `manual_queue.py:600` rebinds `bridge_post as _default_bridge`; called at line 634 — invisible to F6 AST gate, basename `manual_queue.py` ∉ allowlist → would refuse at runtime | Structural | BLOCKER | Added to §F3 allowlist with rationale |
| `send-coverage-message:96` defines its own local `def bridge_post` shadowing `safe_io.bridge_post`; the chokepoint NEVER fires for this script | Structural | BLOCKER | §F3 entry now explicitly tagged as static-gate-only; runtime lint coverage gap documented; migration to chokepoint is PR-ε.1 work, not PR-ζ |
| `path.endswith("safe_io.py")` is fragile under .pyc / namespace-package paths | Structural | MAJOR | Resolver now uses `os.path.basename(path) == "safe_io.py"` |
| F6 AST gate is blind to indirect calls (`fn = bridge_post`) | Structural | MAJOR | §F6 explicitly scoped as "regression-defense against direct callsites"; added follow-up runtime-telemetry note (post-deploy audit-log grouping by `caller_script`) |
| `_emit_audit_row` raising propagates as Python exception, not as 4-tuple — most callers crash | Structural | MINOR | §A1 documents this as intentional fail-closed; callers needing tuple-shape preservation extend their existing try/except in PR-ζ.1 |
| `_emit_audit_row` helper signature is undefined | Structural | MINOR | §F4 now shows explicit signature + body |
| Watchdog → cf-router → bridge_post masks watchdog under `actions.py` allowlist | Structural | NIT | Documented for PR-ζ.1 in §F8 follow-up notes |
| Reviewer dispatch vectors per phase | Strategy | process | Added §A5 with explicit vector assignments for design + PR phases |

---

## Hermes-first capability checklist

Per-step audit against the verified Hermes substrate capabilities (CLAUDE.md §"What Hermes natively handles"):

| # | Step | Tag | Rationale |
|---|---|---|---|
| 1 | Agent decides to send a customer-facing message | `[Hermes]` | Skill dispatch routing handles this today |
| 2 | Import `bridge_post` / `bridge_send_media` / `bridge_send_cta` from `safe_io` | `[Hermes]` | Existing chokepoint (`safe_io.py:624`, post-PR-ε) |
| 3 | Construct `ActionExecutionContext` Pydantic model | `[net-new]` | Hermes has no per-action regulated-intent context primitive. Pydantic itself is substrate; the SHAPE is net-new |
| 4 | Call site uses new `action_context=` kwarg | `[Hermes]` | Mechanical kwarg extension; default `None` preserves the existing contract |
| 5 | `validate_bridge_url` + `bridge_send_blocked_by_test_context` pre-flight | `[Hermes]` | Already deployed (`safe_io.py:588, 610`) |
| 6 | `inspect.stack()` caller resolver + `SAFE_IO_NULL_CONTEXT_ALLOWLIST` frozenset | `[net-new]` | `inspect` is stdlib substrate; the resolver + allowlist semantics + dispatch are net-new |
| 7 | Emit `regulated_send_missing_action_context` audit row | `[Hermes]` | `ndjson_append` chokepoint writes to canonical `decisions.log`; new LogEntry variant is a mechanical schemas.py addition |
| 8 | Invoke `lint_no_unverified_completion(...)` | `[Hermes]` | PR-γ shipped (`customer_copy_policy.py:169`); behavior fully tested |
| 9 | Emit `regulated_send_lint_violation` audit row on hits | `[Hermes]` | Same substrate as #7 |
| 10 | urllib POST + 4-tuple return | `[Hermes]` | Untouched (existing `bridge_post` body) |
| 11 | Static-gate test (callsite AST scan) | `[net-new]` | Mirrors PR-ε `test_send_chokepoint_singularity.py` pattern but operates on call sites, not function definitions |
| 12 | Two `LogEntry` discriminated-union variants | `[Hermes]` | Pattern is deployed (10+ existing variants); mechanical |

3 of 12 steps are `[net-new]` (steps 3, 6, 11). 25%. Well under the half-or-more re-check threshold.

**Awesome-hermes-agent ecosystem check + verdict:** the install-now skill catalogue (`productivity/google-workspace`, `maps`, `airtable`, `ocr-and-documents`, `notion`, `mcp/native-mcp`) is irrelevant to "deterministic per-action runtime context for outbound text-send chokepoint." This is regulated-intent foundation work; the Hermes ecosystem doesn't model action-context. **Verdict: build in-tree on top of PR-γ + PR-δ + PR-ε.**

---

## Drift-rule self-checks

Per CLAUDE.md drift rules, every file in the read-before-propose table was inspected during Phase 1 of this session:

- ✅ Read `src/platform/schemas.py` (for `_BaseEntry`, `LogEntry` discriminated-union pattern, `ConfigDict(extra="forbid", frozen=True)` precedent)
- ✅ Read `src/platform/safe_io.py` (lines 580-820: `bridge_post`, `bridge_send_media`, `bridge_send_cta`, `bridge_post_2tuple`, `validate_bridge_url`, `bridge_send_blocked_by_test_context`)
- ✅ Read `src/agents/flyer/customer_copy_policy.py` (lines 79-205: `FORBIDDEN_COMPLETION_VERBS` + `lint_no_unverified_completion` shape from PR-γ)
- ✅ Read `src/agents/flyer/action_registry.py` (lines 30-149: `FlyerActionMutationClass` enum + `change_plan` `external_irreversible` annotation from PR-δ)
- ✅ Read `tests/test_safe_io_bridge_post.py` (Windows-skip pytestmark, `safe_io_module` fixture, `@patch("urllib.request.urlopen")` pattern)
- ✅ Read `tests/test_send_chokepoint_singularity.py` (PR-ε's two-detector pattern; mirror for AST callsite scan in F6)
- ✅ Read `src/plugins/cf-router/actions.py` callsite samples at lines 3651, 3675, 3711, 3729, 3748, 3870, 4027 (the 7 cf-router `bridge_post(...)` calls that PR-ζ allowlists pending ζ.1 migration)
- ✅ Read `tasks/regulated-intent-gap-fill-pr-sequence-2026-05-26.md` lines 130-144 (canonical PR-ζ design table — this PR's authority)
- ✅ Read `tasks/regulated-intent-control-layer-architecture-2026-05-25.md` §11 lines 575-614 (handler-side §11 audit-fail-closed contract — deliberately deferred from PR-ζ; boundary documented in §A1 below)

`grep -rn ActionExecutionContext src/` returned 0 hits — the Pydantic model is genuinely net-new. No existing primitive collides with the proposal.

---

## Scope (in PR-ζ)

### F1: `ActionExecutionContext` Pydantic model

**Where:** `src/platform/schemas.py`.

**Shape (per spec line 137):**

```python
class ActionExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str = Field(..., min_length=1, max_length=200)
    is_regulated_action: bool
    verified_action_result: bool
    audit_row_id: str | None = Field(default=None, max_length=200)
    mutation_class: Literal["local_reversible", "external_irreversible"] | None = None
```

**Invariants:**
- `frozen=True` — the chokepoint must not mutate it.
- `extra="forbid"` — matches existing state-schema discipline.
- If `is_regulated_action=False`, the lint is bypassed regardless of `verified_action_result` (system messages, internal smoke).
- If `is_regulated_action=True`, lint passes iff `verified_action_result=True`.

### F2: Chokepoint signature extension

**Where:** `src/platform/safe_io.py`, three functions:
1. `bridge_post(jid, message, *, action_context: ActionExecutionContext | None = None)`
2. `bridge_send_media(jid, file_url, *, caption, action_context: ActionExecutionContext | None = None)`
3. `bridge_send_cta(..., action_context: ActionExecutionContext | None = None)`

**Adapter unchanged:** `bridge_post_2tuple(jid, message)` keeps its 2-param shape; passes `action_context=None` to canonical (catering/expense callers hit the null-context allowlist path).

**Keyword-only enforcement:** `action_context` is keyword-only (after `*`). Defends against positional drift.

### F3: Null-context allowlist + caller introspection

**Where:** `src/platform/safe_io.py`, top of module.

```python
SAFE_IO_NULL_CONTEXT_ALLOWLIST: frozenset[str] = frozenset({
    # System health / observability (not regulated business actions):
    "shift-agent-health-check.sh",
    "shift-agent-notify-owner",
    "shift-agent-tail-logger.py",
    "shift-agent-fsck.py",
    # Daily / EOD owner-only digests:
    "send-daily-brief",
    "eod-reconcile",
    "check-compliance-deadlines.py",
    # Flyer recovery watchdogs (system alerts):
    "flyer-recovery-watchdog",
    "flyer-source-edit-sla-watchdog",
    # Flyer closure customer-notify path — `manual_queue.py:600` does
    # `from safe_io import bridge_post as _default_bridge` then calls
    # `bridge_send(chat_id, text)` at line 634. AST gate cannot see the
    # injected callable; runtime resolver lands here. NOT a regulated
    # surface (post-closure notify is informational); allowlisting is
    # correct policy, not a workaround. (Found by REV-1 structural reviewer.)
    "manual_queue.py",
    # Catering / expense — adapter callers via bridge_post_2tuple.
    # Migrating to real ActionExecutionContext is a follow-up PR.
    "send-catering-ack",
    "apply-catering-owner-decision",
    "create-catering-lead",
    "create-catering-proposal-options",
    "finalize-catering-menu",
    "select-catering-proposal",
    "apply-expense-decision",
    # ⚠ STATIC-GATE-ONLY ENTRY. `send-coverage-message:96` defines a LOCAL
    # `def bridge_post(jid, text, timeout=15)` that bypasses
    # safe_io.bridge_post entirely (independent urllib.request POST).
    # The chokepoint NEVER fires for this script — the allowlist entry
    # ONLY satisfies the F6 AST gate. Migrating this script onto the
    # chokepoint is PR-ε.1 work (requires safe_io.bridge_post to gain a
    # `timeout` kwarg). PR-ζ does NOT close the runtime-lint gap for this
    # caller; document explicitly so reviewers don't false-attribute
    # coverage. (Found by REV-1 structural reviewer.)
    "send-coverage-message",
    # cf-router non-change_plan callsites — DEFERRED to PR-ζ.1 (see §F8 for
    # rationale on which callsite IS migrated in PR-ζ). actions.py also
    # houses `send_flyer_text` which forwards `action_context` when given;
    # the allowlist matches when context is None (un-migrated callers).
    "actions.py",
    "hooks.py",
})
```

**Caller resolution algorithm:**

```python
def _resolve_caller_script_name() -> str:
    """Walk inspect.stack() skipping safe_io frames + frozen importlib frames;
    return basename of the first user-code filename. Returns "" if no user
    frame is identifiable (e.g. import-time eval).

    REV 2: use os.path.basename() == 'safe_io.py' instead of path.endswith();
    defends against .pyc / namespace-package / frozen-module path variants
    where endswith would skip the wrong frame. (REV-1 structural reviewer.)
    """
    for frame_info in inspect.stack()[1:]:
        path = frame_info.filename
        if not path:
            continue
        if os.path.basename(path) == "safe_io.py":
            continue
        if "<frozen" in path or "importlib" in path:
            continue
        return os.path.basename(path)
    return ""
```

### F4: Lint hookup

**Where:** Inside each of the 3 bridge functions, between pre-flight checks and urllib POST.

```python
# After: validate_bridge_url + bridge_send_blocked_by_test_context.
# Before: payload assembly + urlopen.

if action_context is None:
    caller = _resolve_caller_script_name()
    if caller not in SAFE_IO_NULL_CONTEXT_ALLOWLIST:
        _emit_audit_row("regulated_send_missing_action_context", {
            "caller_script": caller,
            "jid": jid,
            "message_preview": message[:120],
        })
        return False, "", "missing_action_context", "refused"
else:
    if action_context.is_regulated_action:
        scan = lint_no_unverified_completion(
            message,
            has_verified_action_result=action_context.verified_action_result,
        )
        if scan.hits:
            _emit_audit_row("regulated_send_lint_violation", {
                "action_id": action_context.action_id,
                "audit_row_id": action_context.audit_row_id,
                "jid": jid,
                "verb_hits": [hit.value for hit in scan.hits],
                "message_preview": message[:120],
            })
            return False, "", "lint_violation", "refused"
```

**Media/CTA equivalents:** lint runs against `caption` (and `file_name` for media; `cta_text` for CTA) — mirror PR #250's `_lint_bridge_customer_copy(parts, action_context=...)` aggregator shape.

**`_emit_audit_row` helper signature (REV 2 — was undefined in REV 1):**

```python
def _emit_audit_row(entry_type: str, fields: dict) -> None:
    """Build a discriminated-union LogEntry from `entry_type` + fields and
    append via the canonical chokepoint. Raises on any failure — the
    chokepoint's audit-fail-closed contract requires the exception to
    propagate so the send is not allowed to succeed silently.

    Raises:
        ValidationError — if `fields` don't satisfy the LogEntry variant.
        OSError / FileLockError — if ndjson_append fails (disk full,
          permission, lock contention).
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # Pydantic discriminated-union construction by literal `type` field.
    entry = _build_log_entry(entry_type, ts=ts, **fields)  # uses TypeAdapter
    ndjson_append(LOG_PATH, entry.model_dump_json())
```

`LOG_PATH = Path("/opt/shift-agent/logs/decisions.log")` — canonical audit chokepoint per `reference_audit_chokepoint_canonical_path.md` memory. NO try/except around `ndjson_append` — failure must propagate (see §A1).

### F5: New `LogEntry` variants

**Where:** `src/platform/schemas.py`, added to the `LogEntry` discriminated union.

```python
class _RegulatedSendMissingActionContext(_BaseEntry):
    type: Literal["regulated_send_missing_action_context"]
    caller_script: str
    jid: str
    message_preview: str  # capped at 120 chars

class _RegulatedSendLintViolation(_BaseEntry):
    type: Literal["regulated_send_lint_violation"]
    action_id: str
    audit_row_id: str | None
    jid: str
    verb_hits: list[str]
    message_preview: str
```

**Audit write path:** via `safe_io.ndjson_append` to `/opt/shift-agent/logs/decisions.log`. **No try/except swallowing** — if the audit write raises, the refusal exception propagates (the "audit-fail-closed" interpretation; see §A1 below).

### F6: Static gate

**Where:** new `tests/test_send_chokepoint_null_context_allowlist.py`.

**Scope (REV 2 — explicit per structural-reviewer #4):** REGRESSION DEFENSE AGAINST NEW DIRECT CALLSITES ONLY. The gate detects literal `bridge_post(`, `bridge_send_media(`, `bridge_send_cta(` text in source via AST. It does NOT detect:
- Indirect calls: `fn = bridge_post; fn(...)` (e.g. `manual_queue.py:600,634`)
- `getattr(safe_io, "bridge_post")` dynamic dispatch
- Wrapper helpers like `send_flyer_text` that call `bridge_post` internally

For those, runtime detection is the safety net: an unexpected caller basename will land as a `regulated_send_missing_action_context` audit row at runtime. **Follow-up (PR-η):** add a periodic audit-log report grouping `regulated_send_missing_action_context.caller_script` to surface indirect-call escapees post-deploy.

**Algorithm:** Greps `src/` + `tools/` for direct text matches. AST-parses each. Asserts EITHER:
1. The call passes `action_context=` as a kwarg, OR
2. The containing file's basename is in `SAFE_IO_NULL_CONTEXT_ALLOWLIST`.

Mirrors PR-ε `test_send_chokepoint_singularity.py` shape but at callsite level.

### F8 (NEW IN REV 2): Migrate `flyer.billing.request_plan_change` callsite

**Rationale (strategy reviewer #1):** without this, PR-ζ ships infrastructure that catches zero real bugs on the Flyer billing surface. The cf-router send path is `hooks.py → actions.send_flyer_text(chat_id, reply) → bridge_post(chat_id, message)`. With `actions.py` allowlisted, the lint never fires on plan-change replies. Migrating just the one `external_irreversible` action makes PR-ζ load-bearing on the highest-blast-radius callsite (`change_plan` is the only `external_irreversible` action in the entire 17-agent portfolio per PR-δ).

**Where:** `src/plugins/cf-router/actions.py:4013` + `src/plugins/cf-router/hooks.py` (the dispatch site for account-update replies with `reason == "plan_change_requested"`).

**Step 1 — extend `send_flyer_text` signature:**

```python
# src/plugins/cf-router/actions.py:4013
def send_flyer_text(
    chat_id: str,
    message: str,
    *,
    action_context: "ActionExecutionContext | None" = None,
) -> tuple[bool, str, str]:
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    # ... existing dedupe + bridge_post call body ...
    ok, mid, err, status = bridge_post(chat_id, message, action_context=action_context)
    # ... existing return shape unchanged ...
```

Forward-only: callers that pass nothing get `action_context=None` → falls back to allowlist match (`actions.py` in allowlist). No behavior change for un-migrated callers.

**Step 2 — construct context at the change_plan dispatch in `hooks.py`:**

The site where `account.py`'s `AccountResult.reply` is forwarded after a `change_plan` (reason field on `_apply_account_update`'s return is `"plan_change_requested"`). At THAT site (and only that site in PR-ζ), construct:

```python
from schemas import ActionExecutionContext  # type: ignore

if reason == "plan_change_requested":
    ctx = ActionExecutionContext(
        action_id="flyer.billing.request_plan_change",
        is_regulated_action=True,
        verified_action_result=False,  # this is a REQUEST, not a completion
        mutation_class="external_irreversible",
        audit_row_id=None,  # PR-ζ.1 will plumb the audit row id through
    )
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply, action_context=ctx)
else:
    ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)
```

`verified_action_result=False` is correct here — the reply text is "Please complete payment at <checkout URL>", NOT "Your plan has been upgraded." With `is_regulated_action=True` + `verified_action_result=False`, the lint refuses if the reply text contains a forbidden completion verb. The current `_pending_plan_reply` text (per `account.py:741-755`) does NOT contain forbidden verbs, so the lint passes — but if a future change makes the reply say "Your plan has been upgraded" prematurely, lint catches it. **This is exactly the regression the Lakshmi incident would have surfaced** (strategy reviewer #2).

**Tests added in F7:**
- `test_change_plan_reply_passes_lint_today` — assert `_pending_plan_reply` output is lint-clean
- `test_change_plan_reply_with_forbidden_verb_is_refused` — fault-inject "Your plan has been upgraded" → assert refusal + audit row
- `test_other_account_reply_paths_unchanged` — assert non-`change_plan` callsites still pass through allowlist

**Out of scope (still deferred to PR-ζ.1):**
- The other ~13 `actions.send_flyer_text(...)` callers in `hooks.py` (non-`change_plan` flows: status check, manual review ack, intake ack, source-edit ack, etc.)
- The `bridge_send_media(...)` and `bridge_post(chat_id, "Reply APPROVE...")` callsites in `actions.py:3858, 3870` (concept-preview path)
- Watchdog → cf-router masking (`flyer-recovery-watchdog` → `actions.send_flyer_text` → bridge_post resolves caller as `actions.py`); PR-ζ.1 decides whether `send_flyer_text` should require context for regulated paths

### F7: Behavior tests

**Where:** extend `tests/test_safe_io_bridge_post.py` + new `tests/test_action_execution_context_schema.py`.

1. Schema: model validates with all required fields; rejects missing required; rejects extra fields.
2. Allowlisted caller + None context → send proceeds.
3. Non-allowlisted caller + None context → refused with `status="refused"`, `err="missing_action_context"`. Audit row written.
4. Regulated context + `verified_action_result=True` + forbidden-verb message → send proceeds.
5. Regulated context + `verified_action_result=False` + forbidden-verb message → refused, lint violation audit row written.
6. Regulated context + `verified_action_result=False` + clean message → send proceeds.
7. Non-regulated context + forbidden-verb → send proceeds (lint only runs when `is_regulated_action=True`).
8. Audit write raises mid-refusal → refusal exception propagates (no silent swallow).

---

## Out of scope (deferred)

- **§11 handler-side `regulated_action_executed` contract** (architecture doc §11, lines 575-614): the mandatory-rollback / `refuse_audit_unavailable` / fallback-log / operator-alert pipeline for the **handler** when an audit write fails. Separate cross-cutting PR (touches Flyer billing handler, payment_state machine, rollback scripts). PR-ζ ships chokepoint foundation only.
- **Non-`change_plan` cf-router callsite migration** (REV 2 — narrowed): the 12 other `actions.send_flyer_text(...)` callsites in `hooks.py` and the `actions.py:3858, 3870` concept-preview callsites stay allowlisted. PR-ζ.1 (follow-up) migrates them with the §11 handler-side contract bundled in (strategy reviewer #4: collapse two PRs into one).
- **Catering / expense / shift script call-site migration:** allowlisted for now (per spec non-goal).
- **`send-coverage-message` chokepoint migration:** the script's local `def bridge_post` shadow is PR-ε.1 work. PR-ζ's allowlist entry satisfies the static gate; runtime lint never fires for this script regardless.
- **Translations of forbidden verbs (Telugu/Hindi/Tamil/Kannada/Malayalam per arch §13).**
- **`bridge_post_2tuple` signature extension:** kept 2-param to preserve PR-ε consolidation contract.

---

## Ambiguities for reviewer attention

### A1: How aggressive is "audit-fail-closed" in PR-ζ?

The operator's PR-ζ scope phrase: *"ActionExecutionContext / null-context policy / lint hookup / audit-fail-closed"*. Two interpretations:

| | (a) Minimal (this plan) | (b) Broader |
|---|---|---|
| Chokepoint's own refusal audit-row write must succeed (no try/except swallow); exception propagates to caller | ✅ in scope | ✅ in scope |
| Handler-side `regulated_action_executed` fail-closed (§11): rollback, `refuse_audit_unavailable` copy, fallback log, operator alert | ❌ deferred to PR-ζ.1 (bundled with cf-router migration) | ✅ in PR-ζ |

This plan chooses **(a)** — matches the spec doc (line 130-144 lists 4 elements; §11 contract isn't one). The strategy reviewer agrees (a) is correct and that §11 should bundle into PR-ζ.1 together with the remaining cf-router callsite migration (since both touch the same handler files — splitting them would double the cf-router churn for no review benefit).

**REV 2 audit-write exception propagation note (structural reviewer #5):** when `_emit_audit_row` raises (disk full / lock contention / schema validation error), the exception bubbles up through `bridge_post` instead of returning a 4-tuple. Existing direct callers of `bridge_post`:
- `cf-router/actions.py:3651, 3675, 3711, 3729, 3748, 3870, 4027` — all wrap import in try/except but NOT the call. If `bridge_post` raises, the exception propagates up to the cf-router hook handler, which already has top-level try/except per `hooks.py` (uncaught exceptions → 500 → operator-visible via journalctl).
- `daily_brief/send-daily-brief:822, 828` — `if __name__ == "__main__": sys.exit(main())` shape; an uncaught exception exits non-zero. Acceptable (systemd retries / pages operator).
- `compliance/check-compliance-deadlines.py:403, 407` — same systemd-driven shape.
- `flyer-recovery-watchdog:226` + `flyer-source-edit-sla-watchdog:210` — same.

Net: ALL current direct callers handle uncaught exceptions adequately (top-level handler or systemd restart). **This is the intended fail-closed semantic.** No callsite changes required in PR-ζ; PR-ζ.1 may tighten with explicit try/except if operator wants a uniform tuple-return-on-audit-failure shape.

### A2: Allowlist by filename basename — collision risk?

Today there is exactly one `actions.py` under `src/` (in `cf-router/`). Two files sharing a basename across plugins would share the allowlist entry. **Reviewer Q:** acceptable, or do we need fully-qualified path matching?

### A3: Does the allowlist exempt from lint, or just from the missing-context refusal?

Per F4 dispatch logic, when `action_context is None` AND caller is allowlisted, the send proceeds — and the lint does NOT run (because lint requires a context to know `verified_action_result`). **Reviewer Q:** does the operator want a separate fallback lint (e.g. lint runs against allowlisted sends with `has_verified_action_result=False` assumed)? My read: NO, because allowlisted sends are explicitly classified as non-regulated. But surface this for review.

### A4: cf-router `actions.py` allowlist — partially mitigated in REV 2

REV 1 plan allowlisted `actions.py` blanket; REV 2 migrates the ONE `external_irreversible` callsite (`change_plan`) per F8.

**Remaining exposure:** the 12 non-`change_plan` callsites in hooks.py + 2 in actions.py concept-preview path still rely on the basename-allowlist match. The lint does not fire on these in PR-ζ. PR-ζ.1 migrates them bundled with §11 handler-side contract (strategy reviewer #4).

**Reviewer Q (resolved):** strategy reviewer confirmed migrating just `change_plan` in PR-ζ is the right cut. Foundation + load-bearing protection on the highest-blast-radius callsite. ~30-50 LOC addition (F8) vs the ~150 LOC plan total.

### A5 (NEW IN REV 2): Reviewer dispatch vectors per phase

Per strategy reviewer #5 — without explicit vectors, parallel reviewers converge on style/lint instead of orthogonal attack surfaces. Vector assignments for remaining phases:

| Phase | Reviewer 1 vector | Reviewer 2 vector |
|---|---|---|
| Plan (DONE — this iteration) | Structural / code (cascade ordering, frame resolution, audit chain) | Strategy / judgment (scope cuts, two-step plausibility) |
| Design (next) | Structural / cascade ordering (Pydantic discriminated union construction, F4 dispatch order with edge cases, lint hookup against bridge_send_media `caption + file_name` aggregation) | Statistical / static-gate coverage (F6 AST robustness, manual_queue.py + send_flyer_text indirect-call class, coverage gaps the static gate cannot defend) |
| PR review (after build) | Security / money-flow (change_plan callsite migration carries `external_irreversible`; review lint application is regression-safe + doesn't fail-close on legitimate replies) | Structural / §11-compatibility (does PR-ζ leave a clean seam for PR-ζ.1 to bolt on §11 handler-side contract?) |

The §10 "scope itself bloated/insufficient" lens (CLAUDE.md sixth lens) was applied at plan phase by the strategy reviewer; resulted in F8 addition. Don't need to re-apply at design phase unless scope visibly shifts.

---

## Runtime impact analysis (CLAUDE.md §9a hard gate)

| # | Assumption | How verified |
|---|---|---|
| 1 | `inspect.stack()` works reliably under deployed Python (3.11+ on main-vps) | Stdlib; **no live verification needed** |
| 2 | `lint_no_unverified_completion` is importable from `safe_io.py` | Repo layout: `src/platform/safe_io.py` + `src/agents/flyer/customer_copy_policy.py`. May need flat-module import shim like `intent.py:18-21`. **Verify on VPS pre-merge** |
| 3 | All currently-deployed `bridge_post` callers work after signature extension | Default `=None` preserves existing 2-arg shape; static F6 gate verifies |
| 4 | The audit chokepoint at `/opt/shift-agent/logs/decisions.log` is writable; `ndjson_append` survives logrotate | Per memory `reference_audit_chokepoint_canonical_path.md` + PR-α/β prod evidence. **No live verification needed** |
| 5 | Basename-allowlist resolves expected names under systemd execution | Systemd ExecStart= calls scripts by absolute path; `inspect.stack()` filename is resolved path. **Verify via smoke-running an allowlisted script during deploy** |
| 6 | Catering/expense scripts post-PR-ε route through `bridge_post_2tuple` → `bridge_post` with `action_context=None`; allowlist exhaustive | Verified via grep of `_bridge_post` callsites. All 7 PR-ε scripts in F3 allowlist |
| 7 | cf-router/actions.py's 7 callsites originate from cf-router; `inspect.stack()` basename = `actions.py` | The plugin loads via Hermes's plugin loader; the calling frame's `__file__` is the plugin source path. **Verify by reading 1-2 callsites' surrounding code** |
| 8 | compliance, daily-brief, flyer-watchdog callers resolve their script basename (not wrapped) | Each runs via systemd ExecStart= directly. **Verify by checking the corresponding `.service` units** |
| 9 | New `LogEntry` variants don't break existing audit-log readers | Discriminated union on `type` field. Readers ignore unknown variants. **Verify by running existing audit-reading tests after schema change** |

**Items requiring live VPS check before merge:** #2, #5, #7, #8. Action: SSH to main-vps post-build-but-pre-merge, exercise one path of each.

---

## Test strategy

### Unit (Linux-only via `pytest.mark.skipif(platform.system() == 'Windows')`)

1. `tests/test_action_execution_context_schema.py` — model invariants (8 cases)
2. `tests/test_safe_io_bridge_post.py` — add 8 cases to `TestBridgePost` covering F7
3. `tests/test_send_chokepoint_null_context_allowlist.py` — static gate (4 cases)

### Subprocess

4. Existing `tests/test_catering_v02_scripts.py` etc. — re-run unchanged. With basename allowlist matching, catering/expense paths should pass. **Asserts allowlist coverage is correct.**

### Integration on VPS (smoke)

5. Trigger `send-daily-brief` on main-vps — confirm allowlist path works.
6. Trigger a Flyer status query through cf-router — confirm `actions.py` allowlist match works (lint bypassed for now).

---

## Deploy plan

1. Squash-merge PR-ζ. New main commit.
2. **Pre-deploy gate:** verify the PR-γ + PR-δ + PR-ε bundle (currently main-only, never deployed) is part of this deploy.
3. Tarball deploy via `src/agents/shift/scripts/shift-agent-deploy.sh` to main-vps (`46.62.206.192`).
4. Verify systemd units restart cleanly.
5. Smoke: invoke `send-daily-brief --dry-run`; confirm no `regulated_send_missing_action_context` audit row.
6. Smoke: tail `/opt/shift-agent/logs/decisions.log`; trigger one Flyer status query; confirm send completes.
7. **If any unexpected refusal audit row lands within 5 minutes of deploy:** roll back via prior tarball.

**No customer messaging surface change is expected** — the refusal path is unreachable in PR-ζ because every current caller is allowlisted. PR-ζ ships the chokepoint; PR-ζ.1 turns on enforcement per-callsite.

---

## Commit plan (REV 2 — 6 commits)

1. `feat(schemas): add ActionExecutionContext model + RegulatedSend audit-row variants`
2. `feat(safe_io): extend bridge_post + bridge_send_media + bridge_send_cta with action_context kwarg`
3. `feat(safe_io): add SAFE_IO_NULL_CONTEXT_ALLOWLIST + caller-introspection refusal path`
4. `feat(safe_io): wire lint_no_unverified_completion into bridge_post chokepoint`
5. `feat(cf-router): pass ActionExecutionContext for change_plan callsite (F8 — REV 2)`
6. `test(safe_io): action-context behavior tests + schema tests + static gate`

Six commits, each green-on-tests at HEAD. Static gate (#6) lands last to defend against accidental regressions during build phase. Commit #5 (change_plan migration) is the load-bearing one — it's what makes PR-ζ catch a real bug rather than ship pure scaffolding.

---

## Open questions for reviewer pass

The four ambiguities (A1-A4) above. The runtime-impact items #2 / #5 / #7 / #8 that need live VPS verification before merge.

End of plan.
