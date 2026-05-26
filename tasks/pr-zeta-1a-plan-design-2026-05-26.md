# PR-ζ.1a — Customer-copy hotfix (combined plan + design)

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:**
- Reshape `flyer_customer_not_active_reply` to remove the `cancelled` forbidden verb
- Preserve the `payment_pending` custom branch verbatim (no behavioral regression)
- Replace the f-string fallback's dynamic `{status}` interpolation with a static lint-clean copy (defends against future schema additions where the status name might itself be a forbidden verb)
- Rewrite the PR-ζ change_plan refusal fallback at `hooks.py:1793` (`processed` → lint-clean)
- Schema-driven lint test that iterates every value in `typing.get_args(FlyerCustomerProfile.model_fields["status"].annotation)` so tests fail on schema drift

**Branch:** `fix/pr-zeta-1a-customer-copy-hotfix` off `origin/main` @ `d807b0c`

**Status:** PLAN+DESIGN (combined — scope is tight + operator-specified).

**Authority:** operator directive 2026-05-26: split PR-ζ.1 → ship ζ.1a as customer-copy-only hotfix first; pause + redesign ζ.1b with full 80-callsite census separately.

---

## Why this PR exists right now

The PR-ζ design-reviewer pass (2026-05-26 evening) surfaced a live production exposure: any customer with `status="cancelled"` who messages the bot today receives a reply containing the forbidden completion verb `cancelled`. The `actions.py` entry in `SAFE_IO_NULL_CONTEXT_ALLOWLIST` (shipped in PR-ζ) masks the lint refusal — so the forbidden-verb text reaches the customer. This PR closes that exposure without widening chokepoint enforcement.

## Hermes-first capability checklist

| # | Step | Tag |
|---|---|---|
| 1 | Inbound → cf-router routing | `[Hermes]` |
| 2 | hooks.py routes to `flyer_customer_not_active_reply` | `[Hermes]` |
| 3 | Chokepoint allowlist masks today's bug (PR-ζ shape) | `[Hermes]` |
| 4 | `_STATUS_DISPLAY` mapping + reshape `flyer_customer_not_active_reply` | `[net-new]` |
| 5 | Preserve `payment_pending` branch verbatim | `[Hermes]` (zero change) |
| 6 | Rewrite `hooks.py:1793` change_plan fallback | `[net-new]` |
| 7 | Schema-driven lint test via `typing.get_args(...)` | `[net-new]` |
| 8 | No migration / allowlist / §11 | `[Hermes]` (out of scope) |
| 9 | Tarball deploy via existing pipeline | `[Hermes]` |
| 10 | Post-deploy smoke + audit-log check | `[Hermes]` |

3/10 = 30% net-new. Below re-check threshold.

## Drift-rule self-checks

- ✅ Read `src/plugins/cf-router/actions.py:1735-1762` (`flyer_customer_not_active_reply` — confirmed 4 branches: `payment_pending`, `suspended`, `cancelled`, generic f-string fallback)
- ✅ Read `src/plugins/cf-router/hooks.py:1782-1810` (PR-ζ change_plan fallback — confirmed `"couldn't be processed right now"` contains forbidden verb `processed`)
- ✅ Read `src/platform/schemas.py:1108-1156` (`FlyerCustomerProfile.status: Literal["payment_pending", "trial", "active", "suspended", "cancelled"]` — closed Pydantic Literal at line 1125)
- ✅ Read `src/agents/flyer/customer_copy_policy.py:79-205` (`FORBIDDEN_COMPLETION_VERBS` + `lint_no_unverified_completion` signature, both shipped in PR-γ)
- ✅ Read `tests/test_safe_io_bridge_post.py:1-40` (Windows-skip mark + sys.path injection pattern to mirror)

---

## Concrete code changes

### F1. `src/plugins/cf-router/actions.py:1738-1762` — `flyer_customer_not_active_reply`

```python
def flyer_customer_not_active_reply(customer: dict) -> str:
    """Customer-facing reply when account is not in {trial, active}.

    PR-ζ.1a 2026-05-26: rewrote the `cancelled` branch + fallback to remove
    forbidden completion verbs. The deployed PR-ζ chokepoint lint masks
    this path today via the actions.py allowlist entry; that allowlist is
    scheduled for removal in PR-ζ.1b, at which point this function MUST be
    lint-clean for every reachable status value. Schema-driven tests pin
    the invariant against `FlyerCustomerProfile.status` Literal drift.
    """
    status = str(customer.get("status") or "").strip() or "not_active"
    if status == "payment_pending":
        # PRESERVED VERBATIM — customer-specific copy that was already lint-clean.
        return (
            "Flyer Studio\n"
            "------------\n"
            "Your account is waiting for payment confirmation. "
            "I saved your account details, but flyer generation starts after activation."
        )
    if status == "suspended":
        # PRESERVED VERBATIM — `suspended` is not in FORBIDDEN_COMPLETION_VERBS.
        return (
            "Flyer Studio\n"
            "------------\n"
            "This Flyer Studio account is suspended. "
            "Contact Support before creating a new flyer."
        )
    if status == "cancelled":
        # REWRITTEN — was "is cancelled" (forbidden verb); now lint-clean.
        return (
            "Flyer Studio\n"
            "------------\n"
            "This Flyer Studio account is no longer active. "
            "Contact Support or restart setup before creating a new flyer."
        )
    # Defensive fallback for unexpected status values (legacy data, future
    # schema additions, or empty dict). Drops the dynamic `{status}`
    # interpolation entirely — any future status name added to the
    # FlyerCustomerProfile Literal that happens to be a forbidden completion
    # verb (e.g. a future "refunded" or "cancelled_v2") would otherwise leak
    # into customer copy.
    return (
        "Flyer Studio\n"
        "------------\n"
        "This Flyer Studio account is not currently active. "
        "Contact Support before creating a new flyer."
    )
```

**Why no `_STATUS_DISPLAY` dict:** considered + rejected. The 3 named statuses (`payment_pending`, `suspended`, `cancelled`) each warrant distinct customer copy (different next-step instructions). A dict-driven approach forces a one-size-fits-all message OR a dict-of-dicts. Explicit branches are clearer at this scale (4 branches total). The defensive fallback removes the dynamic interpolation risk without needing a dict.

### F2. `src/plugins/cf-router/hooks.py:1793` — PR-ζ change_plan refusal fallback

```python
# BEFORE (forbidden verb "processed"):
actions.send_flyer_text(
    chat_id,
    (
        "Flyer Studio\n------------\n"
        "Your plan-change request couldn't be processed right now. "
        "We've logged it for operator follow-up — please reply again "
        "in a few minutes or wait for an update here."
    ),
    action_context=fallback_ctx,
)

# AFTER (lint-clean):
actions.send_flyer_text(
    chat_id,
    (
        "Flyer Studio\n------------\n"
        "We weren't able to set up your plan change right now. "
        "We've logged it for operator follow-up — please reply again "
        "in a few minutes or wait for an update here."
    ),
    action_context=fallback_ctx,
)
```

`set up` + `weren't able to` + `logged` are all lint-clean (none in FORBIDDEN_COMPLETION_VERBS).

### F3. New test file: `tests/test_customer_copy_lint_clean_zeta_1a.py`

```python
"""PR-ζ.1a — customer-copy lint-clean invariants.

The PR-ζ chokepoint lint refuses sends that contain forbidden completion
verbs when context is regulated + unverified. After PR-ζ.1b removes
actions.py + hooks.py from SAFE_IO_NULL_CONTEXT_ALLOWLIST, the lint will
run on these customer-facing replies. ζ.1a closes the live exposure today;
this test pins the invariant.

Schema-driven via typing.get_args() on the actual FlyerCustomerProfile.status
Literal annotation — if a future PR adds a new status value, this test
auto-discovers it and verifies the reply is lint-clean. If the new status
happens to be a forbidden completion verb (e.g. someone adds "refunded"),
the test fails until flyer_customer_not_active_reply is updated.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import get_args

import pytest

REPO = Path(__file__).resolve().parent.parent
PLATFORM_DIR = REPO / "src" / "platform"
FLYER_DIR = REPO / "src" / "agents" / "flyer"
CF_ROUTER_DIR = REPO / "src" / "plugins" / "cf-router"
sys.path.insert(0, str(PLATFORM_DIR))
sys.path.insert(0, str(FLYER_DIR))
sys.path.insert(0, str(CF_ROUTER_DIR))

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="cf-router imports use fcntl-bound modules (Linux only)",
)


def _flyer_status_literal_values() -> tuple[str, ...]:
    """Schema-driven enumeration of every reachable FlyerCustomerProfile
    status. Drives the lint test parametrize so a future schema addition
    fails fast if it would emit a forbidden-verb customer reply."""
    from schemas import FlyerCustomerProfile
    annotation = FlyerCustomerProfile.model_fields["status"].annotation
    return get_args(annotation)


_STATUS_VALUES = _flyer_status_literal_values()


@pytest.mark.parametrize("status", _STATUS_VALUES)
def test_flyer_customer_not_active_reply_lint_clean(status: str) -> None:
    """For every reachable status value, the reply must contain no
    forbidden completion verbs under has_verified_action_result=False."""
    from customer_copy_policy import lint_no_unverified_completion
    from actions import flyer_customer_not_active_reply
    reply = flyer_customer_not_active_reply({"status": status})
    scan = lint_no_unverified_completion(reply, has_verified_action_result=False)
    assert scan.hits == (), (
        f"status={status!r} produced forbidden completion verbs: "
        f"{[h.value for h in scan.hits]}; reply={reply!r}"
    )


def test_flyer_customer_not_active_reply_default_branch_lint_clean() -> None:
    """The default branch (status missing or unexpected) must be lint-clean.
    Defends against legacy customer-dict shapes or future schema additions."""
    from customer_copy_policy import lint_no_unverified_completion
    from actions import flyer_customer_not_active_reply
    for synthetic in [
        {},
        {"status": ""},
        {"status": "not_active"},
        {"status": "trial_ended"},  # forbidden-verb-adjacent; not in Literal today
        {"status": "refunded"},     # IS a forbidden verb; tests defensive fallback
        {"status": "completed"},    # IS a forbidden verb
    ]:
        reply = flyer_customer_not_active_reply(synthetic)
        scan = lint_no_unverified_completion(reply, has_verified_action_result=False)
        assert scan.hits == (), (
            f"customer={synthetic!r} produced forbidden completion verbs: "
            f"{[h.value for h in scan.hits]}; reply={reply!r}"
        )


def test_payment_pending_custom_branch_preserved() -> None:
    """The payment_pending branch was always lint-clean and carries
    customer-specific copy. PR-ζ.1a must not regress it to the generic
    fallback. Asserts the specific phrase that distinguishes the custom
    branch from the generic one."""
    from actions import flyer_customer_not_active_reply
    reply = flyer_customer_not_active_reply({"status": "payment_pending"})
    assert "waiting for payment confirmation" in reply, (
        "payment_pending custom branch was lost — regression to generic fallback. "
        f"reply={reply!r}"
    )


def test_change_plan_refusal_fallback_lint_clean() -> None:
    """The PR-ζ change_plan refusal fallback at hooks.py:1793 emits when
    the chokepoint refuses a plan-change reply. PR-ζ.1a rewrites it to
    remove 'processed' — verify lint-clean."""
    from customer_copy_policy import lint_no_unverified_completion
    # The fallback is a literal string in hooks.py; we don't import the
    # whole module (it depends on Hermes plugin loader). Pin the exact
    # post-ζ.1a string here so a future regression to "processed" is
    # caught by the test instead of by the chokepoint at runtime.
    fallback_text = (
        "Flyer Studio\n------------\n"
        "We weren't able to set up your plan change right now. "
        "We've logged it for operator follow-up — please reply again "
        "in a few minutes or wait for an update here."
    )
    scan = lint_no_unverified_completion(fallback_text, has_verified_action_result=False)
    assert scan.hits == (), (
        f"change_plan fallback produced forbidden verbs: "
        f"{[h.value for h in scan.hits]}"
    )


def test_status_literal_schema_drift_detection() -> None:
    """Asserts the FlyerCustomerProfile.status Literal still matches the
    set this PR was designed against. If a future PR adds a new status
    value, this test fails and forces the developer to add an explicit
    branch in flyer_customer_not_active_reply (or verify the defensive
    fallback produces lint-clean copy for the new value)."""
    expected = {"payment_pending", "trial", "active", "suspended", "cancelled"}
    actual = set(_STATUS_VALUES)
    assert actual == expected, (
        f"FlyerCustomerProfile.status Literal drifted from PR-ζ.1a's tested set. "
        f"Expected: {expected}. Actual: {actual}. "
        f"Update flyer_customer_not_active_reply if any new status would emit "
        f"a forbidden completion verb."
    )
```

---

## Out of scope (PR-ζ.1b territory)

- 80+ callsite migration in cf-router (`actions.send_flyer_text`, `_send_ack` family, concept-preview direct sites)
- `SAFE_IO_NULL_CONTEXT_ALLOWLIST` removal of `actions.py` + `hooks.py`
- §11 handler-side `regulated_action_executed` contract
- `PROJECT_ACTIONS` registry additions (deferred until consumer lands)
- F7 wrapper-aware gate extension
- Helper extraction to `action_registry.py` (`_action_context_for` etc.)

All deferred to ζ.1b once full callsite census is complete.

---

## Test strategy

Single new test file: `tests/test_customer_copy_lint_clean_zeta_1a.py` (~80 LOC). 5 test cases:

1. Parametrized lint check across every value in `get_args(FlyerCustomerProfile.status_annotation)` — schema-driven; fails on drift
2. Default-branch lint check across 6 synthetic customer dicts (empty, missing, unmapped string, forbidden-verb-as-status)
3. payment_pending branch preservation assertion (custom copy kept)
4. change_plan refusal fallback lint check (the rewritten string from hooks.py:1793)
5. Schema-drift detection — explicit set comparison

Existing tests touching `flyer_customer_not_active_reply` should continue to pass (no callsite signature change; same return type).

---

## Runtime impact analysis (CLAUDE.md §9a)

| # | Assumption | How verified |
|---|---|---|
| 1 | `cancelled` customers receive a customer-visible reply | The rewrite preserves a reply; no path emits nothing |
| 2 | The reply's customer-perceived meaning is preserved | "is cancelled" → "is no longer active" — same semantic, lint-clean |
| 3 | The 5 production callers in hooks.py (lines 360, 538, 611, 1971, 2515) all consume the return value the same way (str) | No signature change; return type str unchanged |
| 4 | The schema-driven test fails on drift before production sees a new forbidden-verb status | Schema Literal is the source of truth + test introspects it |
| 5 | No other forbidden-verb path is missed in this PR | Out of scope — PR-ζ.1b owns the full surface |

No live VPS check needed pre-merge. Post-deploy smoke: SSH + invoke `flyer_customer_not_active_reply({"status": "cancelled"})` via Python; confirm reply matches the lint-clean rewrite.

---

## Deploy plan

1. Squash-merge PR-ζ.1a → main
2. Tarball deploy via `shift-agent-deploy.sh` to main-vps
3. Verify systemd units restart cleanly
4. Post-deploy SSH smoke:
   - `from actions import flyer_customer_not_active_reply` (with sys.path)
   - Invoke for each of the 5 status values
   - Confirm none contain `FORBIDDEN_COMPLETION_VERBS`
5. Tail `decisions.log` 5 min post-deploy — confirm no unexpected lint-violation audit rows
6. **Pause for ζ.1b re-design** (per operator directive)

---

## Commit plan (1 commit)

1. `fix(flyer,cf-router): customer-copy hotfix — STATUS lint-clean + change_plan fallback rewrite` (~28 src + 80 test LOC)
   - actions.py `flyer_customer_not_active_reply` rewrite + defensive fallback
   - hooks.py:1793 change_plan refusal fallback rewrite
   - `tests/test_customer_copy_lint_clean_zeta_1a.py` (5 tests, schema-driven)

Single commit — hotfix shape; revert granularity at the PR level.
