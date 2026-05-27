# PR-ζ.1b — Full cf-router callsite migration + allowlist removal + manual_queue basename fix

**Drift-check tag:** `extends-Hermes`

**New primitives introduced (REV 2):**
- 9 formal `FlyerActionDefinition` entries in new `PROJECT_ACTIONS` dict in `src/agents/flyer/action_registry.py` (NO `mutation_class` field — dropped per operator audit; deferred to ζ.2 with consumer)
- `build_action_context(action_id, *, is_regulated_action=False, verified_action_result=False)` helper in `action_registry.py` (single source — NOT duplicated)
- **Required-kwarg signature** on `send_flyer_text` + 5 `_send_ack` wrappers — Python TypeError is the forcing function (replaces the dropped F7 gate widening + 80-row parametrized test)
- `caller_chain: list[str]` field on `_RegulatedSendMissingActionContext` + `_RegulatedSendLintViolation` audit-row variants — operator visibility into the actual hooks.py callsite (resolver returns `actions.py` for wrapper-routed calls; chain shows the full path)
- `manual_queue.py` → `flyer_manual_queue.py` allowlist correction + simple deployed-path-exists smoke test (no bash-script parsing)
- Removal of `actions.py` + `hooks.py` from `SAFE_IO_NULL_CONTEXT_ALLOWLIST`

**Branch:** `feat/pr-zeta-1b-full-cf-router-migration` off `origin/main` @ `369c7ff`

**Status:** PLAN — REV 2 (operator Hermes-effectiveness audit + 2 reviewer findings applied).

## REV 2 changelog (2026-05-26)

Three feedback sources converged on substantial simplifications. Net effect: ~200 LOC less code than REV 1, 0 new gate files, 0 new parametrized test files, 0 new bash-parsing infrastructure — matches operator's "less custom code after it lands."

| Finding | Source | Severity | Change applied |
|---|---|---|---|
| §11 audit-row variant + parametrized 80-row test + F7 wrapper-gate widening = custom mini-framework around Hermes | Operator audit | scope | DROP all three. Python required-kwarg signature on `send_flyer_text` + `_send_ack` wrappers becomes the forcing function. Existing PR-ζ gate catches direct chokepoint callers; Python TypeError catches missed wrapper kwargs at TEST RUN TIME. |
| `mutation_class` field on `PROJECT_ACTIONS` has zero consumers (premature scaffolding repeating ζ.1's F2 mistake) | Op + scope #6 | MAJOR | DROP field. Defer to ζ.2 / PR-D with first consumer. |
| Bash-script-parsing regression test = custom-policy mini-framework | Op audit + struct #3 | MAJOR | REPLACE with smoke check: post-deploy, assert each `.py` allowlist entry exists at `/opt/shift-agent/<entry>`. ~20 LOC vs. ~60 LOC bash parsing. |
| `caller_script` resolves to `actions.py` (the wrapper), not the actual hooks.py callsite — operator can't tell which of 80 hooks.py callsites is broken | Struct #4 | HIGH | ADD `caller_chain: list[str]` field to refusal audit-row variants (top 3 stack-frame basenames). Operator-visibility win. ~10 LOC schema + ~5 LOC resolver. |
| `test_cf_router_send_flyer_text_callsites_pass_context` DOESN'T EXIST in repo — REV 1 was editing a phantom file. From-scratch authoring is ~150 LOC | Struct #5 | HIGH | DROPPED with F7 gate widening (operator audit). No phantom file to fix. |
| Commit 5 (52 callsites in one commit) is too big for bisect-friendliness | Scope #2 | MAJOR | SPLIT commit 5 into 3 sub-commits by intent bucket (intake / manual-review / status+remaining). 75 callsite migrations across 3 commits. |
| Indirection ban breaks existing `hooks.py:1772-1773` inline `ActionExecutionContext` (change_plan path) | Scope #3 | MAJOR | RESOLVED by dropping parametrized test (which had the ban). Add small refactor: `hooks.py:1772, 1789` use `build_action_context()` for consistency (~6 LOC). |
| Onboarding + guest-order replies categorized as `flyer.intake.received` is a category error (audit row lies about intent) | Scope #7 | MAJOR | ADD `flyer.account.onboarding_acknowledged` + `flyer.guest_order.reply`. PROJECT_ACTIONS = 9 entries (was 7). |
| §11 deferral defensible but needs explicit tracking | Scope #1 | MINOR | ADD note: "ζ.2 §11 work touches `hooks.py:1772` only; will NOT re-migrate any ζ.1b callsite." |
| Hooks.py-resident wrappers need migration in 3 places: signature, function body, callers | Struct #6 | HIGH | DESIGN REV 1 will enumerate explicitly. The 2 wrappers (`_send_flyer_regeneration_failed_ack` at 1669, `_send_flyer_finalization_failed_ack` at 1682) each need: required-kwarg signature; body passes kwarg to `actions.send_flyer_text`; 3 caller sites pass context. |
| Required-kwarg on `send_flyer_text` may break test monkeypatches | Struct #2 | MEDIUM (verified-contained) | Verified: 30+ monkeypatches in `test_cf_router_flyer_routing.py` use 2-arg lambdas (unaffected). Real calls in tests need updating to pass `action_context=` — mechanical. Document in design. |
| `tasks/audits/pr-zeta-1b-blockers-2026-05-26.md` doesn't exist in ζ.1b worktree | Scope finding | MINOR | The file lives in the main repo (`C:/projects/sme-agents/`). Copy or symlink into worktree at start of build phase. |

---

## Hermes-first capability checklist (REV 2)

| # | Step | Tag | Rationale |
|---|---|---|---|
| 1 | Inbound WhatsApp → cf-router pre-gateway | `[Hermes]` | Plugin loader substrate |
| 2 | hooks.py routes to handler | `[Hermes]` | cf-router plugin deployed |
| 3 | Construct ActionExecutionContext via helper | `[Hermes]` | Pydantic model shipped in PR-ζ; helper is one-line factory |
| 4 | Required-kwarg signature on `send_flyer_text` + 5 `_send_ack` wrappers — Python TypeError is the forcing function | `[net-new]` | Signature changes + 75 callsite migrations |
| 5 | Chokepoint lint dispatch | `[Hermes]` | Shipped in PR-ζ commit 4 |
| 6 | `manual_queue.py` → `flyer_manual_queue.py` allowlist fix + deployed-path-exists smoke | `[net-new]` | 1-line allowlist edit + ~20 LOC smoke test |
| 7 | Allowlist removal of actions.py + hooks.py | `[net-new]` (removes code) | Static-config edit; pure win |
| 8 | 9 `FlyerActionDefinition` entries + `PROJECT_ACTIONS` dict (no mutation_class) | `[net-new]` | Formal registry entries replace stringly-typed action_ids |
| 9 | `build_action_context()` helper | `[net-new]` | Single-source factory in `action_registry.py` |
| 10 | `caller_chain: list[str]` field on refusal audit-row variants | `[net-new]` | Operator-visibility extension to existing `_RegulatedSend*` variants |
| 11 | Tarball deploy + post-deploy `decisions.log` tail | `[Hermes]` | Existing pipeline |

4/11 = 36% `[net-new]` (was 54% in REV 1). The drop reflects the operator-audit simplifications: dropped F7 gate widening, dropped parametrized test, dropped bash-parsing regression test. Below the half-threshold; no re-check needed.

Operator-audit verdict: ζ.1b ships **fewer wrappers, fewer test files, fewer registry fields, and one clearer Hermes send path**. Allowlist shrinks by 1 entry (manual_queue.py removed) + corrects 1 (flyer_manual_queue.py added) + removes 2 (actions.py, hooks.py). Net allowlist size: 22 → 20.

---

## Drift-rule self-checks

- ✅ Read `src/plugins/cf-router/hooks.py` (80 callsites enumerated; 4 ambiguous multi-line callsites spot-read and confirmed Category 1)
- ✅ Read `src/plugins/cf-router/actions.py:3640-3766, 3858-3890` (5 `_send_ack` definitions + 2 concept-preview direct callsites)
- ✅ Read `src/agents/flyer/action_registry.py:55-181` (verified `FlyerActionDomain` already includes `"project"` — cascade reviewer #4's finding; `get_account_action_definition` shape to mirror)
- ✅ Read `src/platform/safe_io.py:636-688` (current `SAFE_IO_NULL_CONTEXT_ALLOWLIST`: 22 entries) + `:800-862` (chokepoint dispatch)
- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` install_artifacts() block (verified the `install -m 644 src/agents/flyer/X.py /opt/shift-agent/flyer_X.py` pattern that drives the rename map)
- ✅ Read `tests/test_send_chokepoint_null_context_allowlist.py:24-180` (F7 gate to extend)
- ✅ Read `tasks/audits/pr-zeta-1b-blockers-2026-05-26.md` (tracked blocker + regression requirement from ζ.1a deploy verification)
- ✅ Reviewed ζ.1 plan REV 2 + design REV 1 + 4 reviewer outputs (historical input from `C:/projects/sme-agents-pr-zeta-1/`)

`grep -rn "PROJECT_ACTIONS\|build_action_context" src/` → **0 hits**. Both genuinely net-new.

---

## Scope (in PR-ζ.1b)

### F1 — `PROJECT_ACTIONS` dict (9 entries, no `mutation_class`) + `build_action_context` helper

**Location:** `src/agents/flyer/action_registry.py`, after the existing `get_account_action_definition` helper (around line 180).

**REV 2 changes (per operator audit + scope reviewer #7):**
- DROPPED `mutation_class` field from all entries — no consumer in PR-ζ.1b; will be added in PR-ζ.2 with the first consumer. Repeats ζ.1's F2 premature-scaffolding mistake if kept.
- ADDED 2 entries: `flyer.account.onboarding_acknowledged` (covers `_try_flyer_onboarding` reply at hooks.py:2306) + `flyer.guest_order.reply` (covers the guest-order ack path). Defaulting these to `flyer.intake.received` would be a category error in audit-row grouping.

`FlyerActionDefinition` is the deployed dataclass. The `mutation_class` field is required on `FlyerActionDefinition` per PR-δ — so PROJECT_ACTIONS entries MUST pass it. **Workaround:** pass `mutation_class="local_reversible"` for now (consistent with all 7 informational acks — they don't mutate external state), but DO NOT plumb it through to `ActionExecutionContext` in the helper. `build_action_context` will explicitly set `mutation_class=None` on the resulting context unless the caller overrides. This decouples the registry's required field from the chokepoint's downstream use.

```python
PROJECT_ACTIONS: dict[str, FlyerActionDefinition] = {
    "flyer.intake.received": FlyerActionDefinition(
        action_id="flyer.intake.received", command="", domain="project",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.processing.started": FlyerActionDefinition(
        action_id="flyer.processing.started", command="", domain="project",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.manual_review.queued": FlyerActionDefinition(
        action_id="flyer.manual_review.queued", command="", domain="project",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.source_edit.processing": FlyerActionDefinition(
        action_id="flyer.source_edit.processing", command="", domain="project",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.delivery.concept_preview": FlyerActionDefinition(
        action_id="flyer.delivery.concept_preview", command="", domain="project",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.delivery.approve_request": FlyerActionDefinition(
        action_id="flyer.delivery.approve_request", command="", domain="project",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.account.status_warning": FlyerActionDefinition(
        action_id="flyer.account.status_warning", command="", domain="account",
        effect="read", mutation_class="local_reversible",
    ),
    # REV 2 — added per scope reviewer #7 (audit-row intent accuracy):
    "flyer.account.onboarding_acknowledged": FlyerActionDefinition(
        action_id="flyer.account.onboarding_acknowledged", command="", domain="account",
        effect="read", mutation_class="local_reversible",
    ),
    "flyer.guest_order.reply": FlyerActionDefinition(
        action_id="flyer.guest_order.reply", command="", domain="guest_order",
        effect="read", mutation_class="local_reversible",
    ),
}


def build_action_context(
    action_id: str,
    *,
    is_regulated_action: bool = False,
    verified_action_result: bool = False,
) -> "ActionExecutionContext":
    """Single-source factory for ActionExecutionContext.

    Looks up `action_id` in ACCOUNT_ACTIONS (by action_id, not command) or
    PROJECT_ACTIONS. The `mutation_class` is inferred from the registry; the
    `is_regulated_action` + `verified_action_result` flags are caller-decided.

    Raises ValueError on unknown action_id — the caller is forced to either
    use a registered action or extend the registry.
    """
    # Mirror intent.py:18-21 dual-import pattern for deployed-flat-module compat.
    try:
        from agents.flyer.action_registry import ACCOUNT_ACTIONS  # type: ignore
        from schemas import ActionExecutionContext  # type: ignore
    except Exception:
        from flyer_action_registry import ACCOUNT_ACTIONS  # type: ignore
        from schemas import ActionExecutionContext  # type: ignore
    # ACCOUNT_ACTIONS is keyed by command, not action_id. Build a reverse index
    # once per process for action_id-based lookup. (Or accept that callers know
    # which dict their action_id lives in.) Cascade reviewer #8 caught the
    # original chain-fallback was dead because of the key mismatch.
    by_action_id = {d.action_id: d for d in ACCOUNT_ACTIONS.values()}
    definition = PROJECT_ACTIONS.get(action_id) or by_action_id.get(action_id)
    if definition is None:
        raise ValueError(f"unknown action_id: {action_id!r}")
    return ActionExecutionContext(
        action_id=definition.action_id,
        is_regulated_action=is_regulated_action,
        verified_action_result=verified_action_result,
        mutation_class=definition.mutation_class,
    )
```

`FlyerActionDomain` is already `Literal["account", "billing", "quota", "guest_order", "project", "preference"]` in deployed code (cascade reviewer #4 confirmed). No domain extension needed.

### F2 — `manual_queue.py` allowlist correction + simplified smoke (REV 2)

**Allowlist edit** in `src/platform/safe_io.py:636`:

```python
# REMOVE: "manual_queue.py"  (source-tree basename — never matches at runtime)
# ADD:    "flyer_manual_queue.py"  (deployed-flat-module basename)
```

**REV 2 (per operator audit + struct #3):** the bash-script-parsing regression test was custom mini-framework. Replaced with a simpler smoke check that runs at POST-DEPLOY time, not in CI: assert each `.py` entry in `SAFE_IO_NULL_CONTEXT_ALLOWLIST` resolves to an existing file at `/opt/shift-agent/<entry>` on the deployed VPS. Catches the same class of bug (source-tree basename in allowlist that doesn't exist on the deployed VPS) without parsing bash.

**Post-deploy smoke** (added to deploy plan step 5):

```bash
ssh root@46.62.206.192 "
cd /opt/shift-agent && python3 -c \"
import sys
sys.path.insert(0, '/opt/shift-agent')
from safe_io import SAFE_IO_NULL_CONTEXT_ALLOWLIST
import os
missing = [e for e in SAFE_IO_NULL_CONTEXT_ALLOWLIST if e.endswith('.py') and not os.path.exists('/opt/shift-agent/' + e)]
assert not missing, f'allowlist references nonexistent deployed files: {missing}'
print(f'OK — {len(SAFE_IO_NULL_CONTEXT_ALLOWLIST)} allowlist entries, all .py entries resolve')
\"
"
```

Below was the REV 1 bash-parsing regression test (DROPPED — kept for reference only):

```python
# DROPPED IN REV 2 — bash-script parsing was custom-policy mini-framework.
# Replaced with the simpler post-deploy smoke above.
def test_allowlist_uses_deployed_basenames():  # ← NOT IMPLEMENTED in REV 2

```python
"""Catch the structural class of bug from PR-ζ.1a deploy: source-tree
basenames in SAFE_IO_NULL_CONTEXT_ALLOWLIST that don't match the deployed
basename after shift-agent-deploy.sh's flat-rename pass.

Evidence: 2 refusal audit rows fired between PR-ζ deploy + ζ.1a deploy
(2026-05-26 18:41:45Z) because `inspect.stack()` returned
`flyer_manual_queue.py` but the allowlist had `manual_queue.py`. See
tasks/audits/pr-zeta-1b-blockers-2026-05-26.md."""

import re
from pathlib import Path


def _parse_deploy_renames() -> dict[str, str]:
    """Parse shift-agent-deploy.sh for `install -m 644 src/X.py /opt/shift-agent/Y.py`
    patterns. Returns {source_basename: deployed_basename} for entries where Y != X."""
    deploy_sh = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"
    text = deploy_sh.read_text(encoding="utf-8")
    renames: dict[str, str] = {}
    # Capture: `install -m 644 src/.../X.py /opt/shift-agent/Y.py`
    pattern = re.compile(
        r"install\s+-m\s+\d+\s+src/[\w/.-]+/(?P<src>\w+)\.py\s+/opt/shift-agent/(?P<dst>\w+)\.py"
    )
    for m in pattern.finditer(text):
        src_basename = m.group("src") + ".py"
        dst_basename = m.group("dst") + ".py"
        if src_basename != dst_basename:
            renames[src_basename] = dst_basename
    return renames


def test_allowlist_uses_deployed_basenames():
    """Every .py entry in SAFE_IO_NULL_CONTEXT_ALLOWLIST must use the DEPLOYED
    basename (not source-tree basename) if the deploy script renames it."""
    from safe_io import SAFE_IO_NULL_CONTEXT_ALLOWLIST
    renames = _parse_deploy_renames()
    py_entries = [e for e in SAFE_IO_NULL_CONTEXT_ALLOWLIST if e.endswith(".py")]
    offenders: list[tuple[str, str]] = []
    for entry in py_entries:
        if entry in renames:
            # Source-tree basename in allowlist → won't match at runtime!
            offenders.append((entry, renames[entry]))
    if offenders:
        lines = [
            f"  {src} → should be {dst} (deploy.sh renames it via install)"
            for src, dst in offenders
        ]
        raise AssertionError(
            "SAFE_IO_NULL_CONTEXT_ALLOWLIST contains source-tree basenames that "
            "don't match the deployed names after shift-agent-deploy.sh's "
            "flat-rename pass:\n" + "\n".join(lines)
        )
```

Plus a positive test that the rename map is non-empty (catches a future deploy.sh restructure that removes the flat-rename pattern).

### F3 — Wrapper signature changes (5 `_send_ack` functions in actions.py)

**Location:** `src/plugins/cf-router/actions.py:3640, 3674, 3715, 3734, 3752`

Pattern for each:

```python
def send_flyer_intake_ack(
    chat_id: str,
    project_id: str,
    *,
    action_context: "ActionExecutionContext",  # REQUIRED — no default
) -> tuple[bool, str, str]:
    _ensure_platform_path()
    try:
        from safe_io import bridge_post  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    message = (
        "Flyer Studio\n------------\n"
        "Got it. I have your flyer request and will send an update here shortly."
    )
    ok, message_id, err, status = bridge_post(chat_id, message, action_context=action_context)
    if ok:
        return True, message_id, ""
    return False, message_id, f"{status}: {err}"
```

**No default value** — coverage reviewer #5 rejected the default-context pattern (masks caller intent). Every caller must construct + pass.

### F4 — 75 callsite migrations in hooks.py

**Categorization rule (mechanical for ALL hooks.py callsites):** every cf-router-resident `send_flyer_text` or `_send_ack` callsite emits an INFORMATIONAL ack (intake, processing, manual review, source-edit, status warning, clarification, refusal). All map to Category 1: `is_regulated_action=False`, `verified_action_result=False`.

The ONE exception is the `change_plan` callsite at hooks.py:1772, which PR-ζ already migrated as Category 3 (regulated + unverified). PR-ζ.1b does NOT touch it.

**Migration mechanic per callsite:**

```python
# BEFORE:
ack_ok, mid, err = actions.send_flyer_text(chat_id, reply)

# AFTER:
ctx = build_action_context("flyer.intake.received")  # or appropriate action_id
ack_ok, mid, err = actions.send_flyer_text(chat_id, reply, action_context=ctx)
```

**Action-id assignment (mechanical mapping by intent) — REV 2 (9 buckets per scope reviewer #7):**

| Calling intent | action_id |
|---|---|
| Intake / starter / sample-prompt ack, intake-failed clarification | `flyer.intake.received` |
| "Creating now" processing ack | `flyer.processing.started` |
| Manual-review ack, manual-edit ack, regeneration-failed ack, finalization-failed ack | `flyer.manual_review.queued` |
| Source-edit processing ack | `flyer.source_edit.processing` |
| Account-not-active (`flyer_customer_not_active_reply`) status warning | `flyer.account.status_warning` |
| Onboarding-reply path (`_try_flyer_onboarding`, `next_status ∈ {trial, active}`) | `flyer.account.onboarding_acknowledged` (REV 2 ADDED — was incorrectly defaulted to `intake.received`) |
| Guest-order ack/reply | `flyer.guest_order.reply` (REV 2 ADDED — was incorrectly defaulted to `intake.received`) |
| Concept-preview send | `flyer.delivery.concept_preview` |
| "Reply APPROVE" CTA | `flyer.delivery.approve_request` |
| Generic clarification, account-command-result (non-onboarding) | `flyer.intake.received` (true default — only for replies that don't fit a more specific bucket) |

The full per-callsite mapping table (75 rows) lives in the **design doc (REV 1)** — NOT this plan. Plan documents the rule; design enumerates.

**hooks.py-resident wrappers (`_send_flyer_regeneration_failed_ack` at 1669, `_send_flyer_finalization_failed_ack` at 1682):** these themselves call `actions.send_flyer_text` and need the same migration. Wrappers accept the same REQUIRED `action_context` kwarg.

### F5 — 2 concept-preview direct callsites in actions.py

**Location:** `src/plugins/cf-router/actions.py:3875, 3887`

- 3875: `bridge_send_media(chat_id, asset.get("path", ""), caption=caption)` → add `action_context=build_action_context("flyer.delivery.concept_preview")`
- 3887: `bridge_post(chat_id, "Reply APPROVE...")` → add `action_context=build_action_context("flyer.delivery.approve_request")`

### F6 — REV 2 — DROPPED (was: F7 wrapper-aware gate widening)

**Reason for drop:** the wrapper-aware gate file referenced by REV 1 (`test_cf_router_send_flyer_text_callsites_pass_context`) DOES NOT EXIST in the repo — REV 1 was editing a phantom file (structural reviewer #5). Authoring it from scratch would be ~150 LOC of custom test infrastructure. The operator audit identified this as parallel custom mini-framework around Hermes.

**Replacement:** make `send_flyer_text` + the 5 `_send_ack` wrappers require `action_context` as a keyword-only argument with NO default. Python's `TypeError: missing required keyword-only argument: 'action_context'` becomes the forcing function at every test run. A missed callsite cannot ship to production because pytest fails before the PR can land.

The existing PR-ζ gate (`test_send_chokepoint_null_context_allowlist.py`) continues to enforce that DIRECT `bridge_post*` callsites pass context or are in the allowlist. Combined with Python's signature enforcement on wrappers, this covers the full callsite footprint without new gate infrastructure.

### F6-bis — REV 2 ADDED — `caller_chain` audit-row field

**Reason for add:** structural reviewer #4 found that `_resolve_caller_script_name` returns `actions.py` (the wrapper) for hooks.py-resident callsites that route through `send_flyer_text`. After PR-ζ.1b removes `actions.py` from the allowlist, refusal audit rows for hooks.py bugs would show `caller_script="actions.py"` — the operator cannot tell which of the 80 hooks.py callsites is broken.

**Location:** `src/platform/schemas.py` (extend two existing variants); `src/platform/safe_io.py` (extend resolver to capture the top-3 stack frame basenames).

```python
# schemas.py — extend the existing PR-ζ refusal variants:
class _RegulatedSendMissingActionContext(_BaseEntry):
    type: Literal["regulated_send_missing_action_context"]
    caller_script: str = Field(..., max_length=200)
    caller_chain: list[str] = Field(default_factory=list, max_length=10)  # NEW
    jid: str = Field(..., max_length=200)
    message_preview: str = Field(..., max_length=120)

class _RegulatedSendLintViolation(_BaseEntry):
    type: Literal["regulated_send_lint_violation"]
    action_id: str = Field(..., max_length=200)
    audit_row_id: Optional[str] = Field(default=None, max_length=200)
    caller_chain: list[str] = Field(default_factory=list, max_length=10)  # NEW
    jid: str = Field(..., max_length=200)
    verb_hits: list[str] = Field(..., max_length=20)
    message_preview: str = Field(..., max_length=120)
```

```python
# safe_io.py — extend the resolver:
def _resolve_caller_chain() -> list[str]:
    """Top-3 non-safe_io non-frozen frame basenames. Operator-visibility
    aid: refusal audit rows show the full call path, not just the
    immediate non-safe_io frame."""
    chain: list[str] = []
    for frame_info in inspect.stack()[1:]:
        path = frame_info.filename
        if not path:
            continue
        if os.path.basename(path) == "safe_io.py":
            continue
        if "<frozen" in path or "importlib" in path:
            continue
        chain.append(os.path.basename(path))
        if len(chain) >= 3:
            break
    return chain or ["<unidentifiable>"]
```

Existing `_resolve_caller_script_name()` continues to return `chain[0]` for the allowlist match. The new function is called once when constructing the audit row.

### F7 — Allowlist removal (the load-bearing piece — UNCHANGED from REV 1)

**Location:** `src/platform/safe_io.py:636-688`

Remove `"actions.py"` + `"hooks.py"` entries. After PR-ζ.1b, every cf-router callsite either passes `action_context=` explicitly (Python TypeError if missed via wrapper signature) OR is a direct `bridge_post*` call caught by the existing PR-ζ gate.

### F8 — REV 2 — DROPPED (was: parametrized per-callsite test)

**Reason for drop:** 80-row parametrized test using `ast.unparse` to extract kwarg expressions = parallel custom audit substrate (operator audit). The action_id label is metadata for audit-row grouping; a wrong label causes no customer impact (chokepoint behavior depends on `is_regulated_action` boolean, not the action_id string).

**Replacement:** none needed. Python's required-kwarg signature catches missed callsites at every pytest run. The action_id assignment is reviewer-verifiable via the design doc's enumeration table (visual inspection, not automated test). If a wrong action_id ships, post-deploy audit-log analysis surfaces it (grouping by action_id reveals mis-tagged callsites).

Two changes to the existing F7 sibling test (`test_cf_router_send_flyer_text_callsites_pass_context`):

1. **Extend `WRAPPER_FUNCS`** to include all `_send_ack` family names + hooks.py-resident wrappers:

   ```python
   WRAPPER_FUNCS = {
       "send_flyer_text",
       "send_flyer_intake_ack",
       "send_flyer_processing_ack",
       "send_flyer_manual_edit_ack",
       "send_flyer_manual_review_ack",
       "send_flyer_edit_processing_ack",
       "_send_flyer_regeneration_failed_ack",
       "_send_flyer_finalization_failed_ack",
   }
   ```

2. **Reject `action_context=None` literal** — currently a callsite passing `action_context=None` explicitly passes the gate (kwarg present) but refuses at runtime. AST walk: for each kwarg, if value is `ast.Constant(value=None)`, treat as offender.

### F7 — Allowlist removal (the load-bearing piece)

**Location:** `src/platform/safe_io.py:636-688`

Remove `"actions.py"` + `"hooks.py"` entries. Result: 20 entries remain in the allowlist; cf-router callsites must thread context explicitly or refuse at runtime.

Static gate (F6) ensures this is green-on-tests at commit-7 HEAD.

### F8 — Parametrized per-callsite test

**Location:** new `tests/test_cf_router_action_context_migration.py`

Parametrize over the full (file, line, action_id, is_regulated_action) tuple table from the design doc REV 1 (~80 entries). For each: AST-walk the file, locate the call at the specified line, parse the `action_context=` kwarg, resolve the `build_action_context("...")` literal arg, assert match.

**Indirection policy (per cascade reviewer #6):** ban variable-indirection. The test FAILS if `action_context=` is a `Name` node (variable) instead of a literal `Call` to `build_action_context("...")`. This keeps the gate decidable without dataflow analysis.

---

## Out of scope (deferred to PR-ζ.2 / PR-D)

- **§11 handler-side `regulated_action_executed` contract** (mandatory rollback for `local_reversible`, fallback log at `state/.audit-fallback.ndjson`, operator alert via `notify-owner-with-fallback`). Scope: handler-side audit-row variant + rollback wiring + payment_state machine. Per architecture spec §11: belongs in PR-D alongside `PaymentState` enum.
- **`_FlyerRegulatedActionExecuted` LogEntry variant + emit helper** — dropped from ζ.1 per coverage reviewer #6 (premature scaffolding without consumer). Will ship in PR-ζ.2 / PR-D with the first consumer.
- **`mutation_class` field on `PROJECT_ACTIONS` entries** — included for forward-compat per PR-δ pattern but unused by any ζ.1b code. Acceptable scaffolding (each entry has the field declared).
- **Translation of forbidden verbs (Telugu/Hindi/Tamil/Kannada/Malayalam per arch §13).**
- **Migration of `bridge_post_2tuple` callers** (catering, expense) — separate from cf-router; the adapter remains 2-param.

---

## Ambiguities for reviewer attention

### A1: `ACCOUNT_ACTIONS` is keyed by command, not action_id

`build_action_context()` works around this by building a reverse index from `ACCOUNT_ACTIONS.values()` (cascade reviewer #8). Alternative: refactor `ACCOUNT_ACTIONS` to be keyed by action_id (breaking change for `get_account_action_definition`). **Lean:** keep the reverse-index — smaller blast radius.

### A2: Should the helper build the reverse index lazily?

Building `by_action_id` once per `build_action_context` call is O(11) — trivial. Lazy module-level cache is over-engineering for 11 entries. **Lean:** don't cache; build per-call.

### A3: What about `flyer.billing.request_plan_change` (the existing change_plan path)?

`hooks.py:1772` (PR-ζ change_plan callsite) and `hooks.py:1789` (PR-ζ fallback) BOTH construct `ActionExecutionContext` inline currently. After PR-ζ.1b lands `build_action_context()`, these two callsites COULD be refactored to use the helper for consistency. **Lean:** refactor — single source for context construction. ~6 LOC change.

### A4: How aggressive is the parametrized test enumeration?

80 rows × ~5 LOC per row = ~400 test LOC. Worth it for the structural guarantee. **Lean:** ship the full table.

### A5: Does the regression test correctly parse all rename patterns?

The deploy script also has `if [ -f src/agents/flyer/X.py ]; then install -m 644 src/agents/flyer/X.py /opt/shift-agent/flyer_X.py; fi` patterns (conditional installs). Need to handle the conditional shape too. Verified during design phase by re-reading `shift-agent-deploy.sh:50-150`.

---

## Runtime impact analysis (CLAUDE.md §9a hard gate)

| # | Assumption | How verified |
|---|---|---|
| 1 | `inspect.stack()` resolves correctly for the renamed flat-module under cf-router runtime | Pre-deploy SSH check: invoke a function in `flyer_manual_queue.py` that calls `bridge_post`; assert chokepoint sees `caller_script="flyer_manual_queue.py"` |
| 2 | All 80 callsites are reachable from production traffic shapes (no dead code) | Spot-check 5 random callsites have inbound message types that produce them |
| 3 | `build_action_context("...")` raises ValueError on unknown action_id (forces caller correctness) | Unit test in F1 |
| 4 | The static gate goes RED if any callsite is missed | F8 parametrized test + F6 wrapper-aware gate — both must be green at commit 7 |
| 5 | The allowlist removal doesn't accidentally break a non-cf-router caller | Grep: only cf-router files reference `actions` / `hooks` basename. No other consumers |
| 6 | After deploy, no `regulated_send_missing_action_context` audit rows fire in normal traffic | Post-deploy 15-min audit-log tail; any new rows → investigate immediately |
| 7 | The `manual_queue.py` blocker fix actually resolves the dev-VPS smoke refusals | Smoke: invoke `flyer_manual_queue.py` closure path; confirm no refusal row |

**Items requiring live VPS check pre-merge:** #1, #6, #7. Action: SSH to main-vps post-build-but-pre-merge, exercise one path of each.

---

## Test strategy — REV 2

1. `tests/test_action_registry.py` (extend) — 9 `PROJECT_ACTIONS` entries well-formed; `build_action_context` correctness including ValueError on unknown action_id + no mutation_class leakage to ActionExecutionContext
2. `tests/test_safe_io_bridge_post.py` (extend) — assert allowlist invariants: `"actions.py" not in SAFE_IO_NULL_CONTEXT_ALLOWLIST` + `"hooks.py" not in SAFE_IO_NULL_CONTEXT_ALLOWLIST` + `"flyer_manual_queue.py" in SAFE_IO_NULL_CONTEXT_ALLOWLIST` + `"manual_queue.py" not in SAFE_IO_NULL_CONTEXT_ALLOWLIST`. Plus: `caller_chain` field on audit-row variants populated correctly (3-frame deep)
3. Existing wrapper + send_flyer_text tests need `action_context=` kwarg added — mechanical; documented in commit 4's message
4. Post-deploy smoke (NOT a unit test): assert each `.py` allowlist entry exists at `/opt/shift-agent/<entry>` on the deployed VPS

**DROPPED in REV 2 (per operator audit + struct #5):**
- `tests/test_allowlist_deployed_basename_resolution.py` — bash-parsing infrastructure
- `tests/test_cf_router_action_context_migration.py` — 80-row parametrized AST test
- F7 wrapper-aware gate extension to `test_send_chokepoint_null_context_allowlist.py`

The forcing function is now: Python's TypeError on missed required-kwarg + the existing PR-ζ direct-callsite gate. No new test infrastructure beyond what's listed above.

---

## Deploy plan

1. Squash-merge PR-ζ.1b → main
2. **Pre-deploy SSH check (critical, per ζ.1a lesson):**
   - SSH to main-vps
   - Invoke `flyer_manual_queue.py`'s closure-notify path (`build_closure_customer_text(p)` + `bridge_post(...)` with no context)
   - Assert chokepoint allows the send (allowlist match via `flyer_manual_queue.py` basename)
   - Tail `decisions.log` for absence of `regulated_send_missing_action_context` rows
3. Tarball deploy via `shift-agent-deploy.sh`
4. Verify systemd units restart cleanly
5. Smoke: trigger inbound flyer status query through cf-router; confirm chokepoint allows (context threaded)
6. Smoke: trigger a synthetic forbidden-verb send via a non-cf-router caller (e.g. test fixture); confirm chokepoint refuses cleanly
7. **15-min audit-log tail post-deploy:** any new `regulated_send_*` row with unexpected caller → operator alert
8. If unexpected refusals → roll back via prior tarball

---

## Commit plan — REV 2 (8 commits)

1. `feat(action_registry): PROJECT_ACTIONS (9 entries, no mutation_class field) + build_action_context helper` (~55 src + 30 test LOC) — single-source factory
2. `feat(schemas,safe_io): caller_chain field on RegulatedSend audit variants + _resolve_caller_chain() resolver` (~25 src + 25 test LOC) — operator visibility into hooks.py callsite for wrapper-routed refusals
3. `fix(safe_io): manual_queue.py → flyer_manual_queue.py allowlist + deployed-path-exists smoke (CI assertion)` (~5 src + 25 test LOC) — closes the ζ.1a blocker
4. `feat(safe_io,cf-router): require action_context kwarg on send_flyer_text + 5 _send_ack wrappers; update test monkeypatches/calls` (~40 src + 30 test LOC) — Python TypeError is the forcing function
5. `feat(cf-router): migrate intake/onboarding/starter-prompt callsites (~15 sites)` (~30 src) — bisect-friendly intent bucket
6. `feat(cf-router): migrate manual-review/regeneration/finalization callsites (~17 sites) + 2 hooks.py-resident wrappers` (~40 src)
7. `feat(cf-router): migrate status-warning + clarification + guest-order + onboarding + remaining callsites + 2 concept-preview direct sites` (~50 src) — final batch + refactor hooks.py:1772, 1789 to use build_action_context for consistency
8. `feat(safe_io): remove actions.py + hooks.py from SAFE_IO_NULL_CONTEXT_ALLOWLIST` (~5 src + 15 test LOC) — load-bearing forcing function via existing PR-ζ gate; if any callsite missed in commits 4-7, the existing direct-callsite gate goes RED at commit 8

Each commit green-on-tests at HEAD. Net effect vs. REV 1:
- 8 commits (was 7), but commits 5-7 split commit 5 by intent bucket per scope reviewer #2 — bisect-friendly
- ~200 src + ~120 test LOC (was ~240 + ~280) — **~200 LOC less code**
- 0 new gate files (was 1 phantom + 1 widening)
- 0 new parametrized test files (was 1 with 80 rows)
- 0 new bash-parsing infrastructure
- Adds: `caller_chain` field — small operator-visibility win, NOT a new substrate

---

## Open Qs for reviewer pass — REV 2 status

| Ambiguity | REV 2 resolution |
|---|---|
| A1 (`ACCOUNT_ACTIONS` keyed by command, not action_id) | RESOLVED — `build_action_context` builds a one-time reverse-index `by_action_id = {d.action_id: d for d in ACCOUNT_ACTIONS.values()}` per call. O(11). |
| A2 (lazy vs cached reverse index) | RESOLVED — don't cache (operator audit: don't over-engineer). |
| A3 (refactor `hooks.py:1772, 1789` to use helper) | RESOLVED — refactor lands in commit 7 for consistency. ~6 LOC. |
| A4 (parametrized test enumeration burden) | RESOLVED — parametrized test DROPPED per operator audit. |
| A5 (bash regex robustness — original A5 said "re-read 50-150" but renames are at 247-330) | RESOLVED — bash parsing DROPPED entirely. Replaced with post-deploy smoke. |
| Hooks.py-resident wrappers (1670, 1683) need migration in signature + body + 3 callers (struct #6) | DOCUMENTED — design REV 1 will enumerate explicitly. |
| Required-kwarg breaks test monkeypatches (struct #2) | RESOLVED — verified-contained: monkeypatches use 2-arg lambdas (unaffected). Real-function calls in tests need `action_context=` added — mechanical, lands in commit 4. |
| `tasks/audits/pr-zeta-1b-blockers-2026-05-26.md` missing from worktree | TODO at build phase: copy from main repo or move to evidence/ |
| §11 deferral tracking (scope #1) | DOCUMENTED — ζ.2 §11 touches `hooks.py:1772` only; will NOT re-migrate any ζ.1b callsite. |

Runtime-impact items #1 (basename resolution for renamed module), #6 (post-deploy 15-min audit-log tail), #7 (manual_queue blocker fix smoke) still need live VPS check post-deploy.

End of plan REV 2.

---

## Resume contract for the next session (post-/compact)

**Current state at REV 2 lock:**

- `origin/main` at `369c7ff` (ζ.1a deployed; ζ.1b ready to build)
- ζ.1b worktree: `C:/projects/sme-agents-pr-zeta-1b` on branch `feat/pr-zeta-1b-full-cf-router-migration`
- Plan REV 2 at `tasks/pr-zeta-1b-plan-2026-05-26.md` (this doc)
- Receipt at `tasks/.hermes-check-receipts/pr-zeta-1b-plan-2026-05-26.json`
- 8-commit shape captured in §"Commit plan — REV 2"
- 9 PROJECT_ACTIONS entries enumerated; bucket-by-intent mapping settled

**Next phase:** Phase 4 — write the design doc at `tasks/pr-zeta-1b-design-2026-05-26.md` with:
- Full per-callsite enumeration table (75 hooks.py callsites + 2 hooks.py-resident wrappers + 5 actions.py `_send_ack` definitions + 2 concept-preview direct callsites)
- Concrete code for `build_action_context()` helper
- Concrete code for `caller_chain` field + `_resolve_caller_chain()`
- Per-wrapper migration enumeration (signature + body + caller sites)
- Concrete code for the `hooks.py:1772, 1789` refactor to use `build_action_context()`
- Pre-deploy SSH check shape

**Then:** Phase 5 (2 design reviewers along orthogonal vectors) → Phase 6 (build, 8 commits) → Phase 7 (PR + 2 reviewers) → Phase 8 (merge) → Phase 9 (deploy + smoke).

**Operator-audit guardrails to enforce throughout build:**

1. Every behavior must be Hermes-native, extends-Hermes, or have a justification why Hermes cannot already cover it
2. No parallel send path / parallel audit path / parallel routing substrate
3. Prefer metadata/context on existing primitives over new wrappers
4. PR shape should feel like LESS custom code after it lands
5. If design produces growing surface area instead of shrinking, STOP and cut scope
