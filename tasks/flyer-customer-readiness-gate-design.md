**Drift-check tag:** extends-Hermes

# Flyer Studio customer-readiness stabilization gate — design

Date: 2026-05-21
Branch: `codex/flyer-customer-readiness-gate`
Plan: `tasks/flyer-customer-readiness-gate-plan.md`

## Hermes-first capability checklist

Per the approved plan, the end-to-end flow is unchanged. Per-step tagging is reproduced here so the design carries its own checklist (CLAUDE.md procedural rule: re-run at every stage transition).

| Step | Tag | Capability |
|---|---|---|
| 1. Operator runs CLI | `[Hermes]` operator-script pattern (`tools/operator-brief.py` precedent) | 0 |
| 2. Load input fixture (bridge/gateway/cockpit/deploy/source-edit/PRs) | `[net-new]` product-policy schema | ~40 LOC |
| 3. Enumerate open PRs / merged-not-deployed from fixture | `[net-new]` offline | ~20 LOC |
| 4. Read source-edit posture from fixture | `[net-new]` policy interpretation | ~15 LOC |
| 5. Scan state + decisions.log | `[Hermes]` existing `customer_copy_incidents`, `manual_source_edit_stale`, `duplicate_initial_ack_incidents` | 0 |
| 6. Replay 11 scenarios | `[Hermes]`-adjacent `pre_gateway_dispatch` harness + extracted helper | ~80 LOC test wiring |
| 7. Aggregate verdict (calls existing severity_rank) | `[net-new]` rollout-decision rules | ~100 LOC |
| 8. Render JSON + Markdown | `[Hermes]` existing `flyer-self-evaluation.py` + `operator-brief.py` | 0 |
| 9. Customer-copy guard | `[Hermes]` `scan_customer_text` already in tree | 0 |
| 10. Pytest replay assertions | `[net-new]` fixture data + assertions | ~260 LOC |
| 11. Source-edit yellow rule | `[net-new]` 5-state policy | ~25 LOC |
| 12. tasks/todo.md deferred item | `[net-new]` text | ~30 LOC |
| 13. Operator reads stdout | `[Hermes]` CLI | 0 |

No install-now Hermes skill applies. Net-new = product policy + test fixtures + report-shape work. Substrate-free.

## Drift-rule self-checks

- ✅ Read `tools/flyer-self-evaluation.py` (lines 1073-1213; confirmed `build_report` signature, severity-rank at 1099-1101, CLI args, sanitize_report flow)
- ✅ Read `src/agents/flyer/customer_copy_policy.py` (full; confirmed `scan_customer_text(text, raw_request="")` signature + `CustomerCopyScan` shape + `raw_request_echo` gating at `len(normalized_raw) >= 8`)
- ✅ Read `tests/test_flyer_incident_replay.py` (full; confirmed `_install_common_replay_mocks` returns `(hooks, actions, calls, audits, sent, identity_calls)` and harness shape, fixture JSON structure)
- ✅ Read `src/platform/schemas.py` lines 825-933 (`FlyerSourceEditProviderPolicy`, `resolve_source_edit_render_provider`, `model_config = ConfigDict(extra="forbid")` v2 form)
- ✅ Read `tools/operator-brief.py` (full; confirmed `summarize_flyer_evaluation_report` flow and `--flyer-evaluation-json` consumption)
- ✅ Read `tests/fixtures/flyer_incident_replay/flyer_incidents.json` (full; confirmed fixture key shape — id, mode, text, chat_id, resolved_identity, customer, active_project, pending, expect: {route, must_audit, must_call, forbidden_calls, must_send_contains, duplicate_initial_ack})
- ✅ Plan reviewed by 2 parallel agents (rollout-behavior + Hermes-first/drift); both APPROVE WITH CHANGES; all Critical/High/Important findings folded into the plan before this design

## Module: `src/agents/flyer/rollout_readiness.py`

### Pydantic input-fixture schema

```python
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SourceEditPosture = Literal[
    "configured_with_smoke",
    "configured_with_smoke_stale",
    "configured_no_smoke",
    "manual_review",
    "unset",
]


# Deployed `schemas.py` convention: nullable Pydantic fields use `Optional[X]`
# with `from typing import Optional`, not PEP 604 `X | None`. Timestamp fields
# use `Optional[datetime]` so Pydantic parses + validates the ISO string.
# (Folded from drift-reviewer Critical C1 + C2.)


CustomerRiskLabel = Literal[
    "customer-routing",
    "lifecycle",
    "copy",
    "payment",
    "schema-migration",   # folded from rollout-reviewer H3
    "deploy-gate",        # folded from rollout-reviewer H3
    "security",           # folded from rollout-reviewer H3
    "auth",               # folded from rollout-reviewer H3
    "none",
]


class RolloutOpenPR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    title: str = ""
    customer_risk: bool = False
    customer_risk_label: CustomerRiskLabel = "none"


class RolloutMergedNotDeployed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    title: str = ""
    customer_risk_label: CustomerRiskLabel = "none"


class RolloutReplaySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed_ids: list[str] = Field(default_factory=list)


class RolloutInputFixture(BaseModel):
    """Host-supplied posture facts the worktree cannot self-derive.

    All fields are operator-supplied truth as of the time the fixture is
    produced. The CLI never probes a live host; see plan §Risk 3.
    """

    model_config = ConfigDict(extra="forbid")

    deploy_marker: str = ""
    bridge_status: Literal["connected", "disconnected", "unknown"] = "unknown"
    gateway_status: Literal["active", "inactive", "unknown"] = "unknown"
    cockpit_status: Literal["healthy", "degraded", "unknown"] = "unknown"
    open_prs: list[RolloutOpenPR] = Field(default_factory=list)
    merged_not_deployed: list[RolloutMergedNotDeployed] = Field(default_factory=list)
    host_supplied_source_edit_posture: SourceEditPosture = "unset"
    source_edit_smoke_evidence_age_days: Optional[int] = None
    provider_routing_changed_at: Optional[datetime] = None
    replay_summary: Optional[RolloutReplaySummary] = None
```

### Source-edit posture helper

```python
def compute_source_edit_posture(
    fixture: RolloutInputFixture,
) -> tuple[SourceEditPosture, str]:
    """Return (posture, reason_text)."""

    posture = fixture.host_supplied_source_edit_posture
    if posture == "configured_with_smoke":
        return posture, ""
    if posture == "configured_with_smoke_stale":
        return (
            posture,
            "source-edit smoke evidence stale vs. latest provider-routing change",
        )
    if posture == "configured_no_smoke":
        return (
            posture,
            "source-edit policy configured but spend-gated 5-10 case smoke evidence missing",
        )
    if posture == "manual_review":
        return posture, "source-edit runs through manual_review fallback"
    # "unset"
    return (
        posture,
        "source-edit policy posture not supplied; defaulting to manual_review/yellow",
    )
```

### Verdict helper (single-sourced colors via shared SEVERITY_RANK)

`SEVERITY_RANK` + `incident_color` live in `rollout_readiness.py` as the single source of truth. `tools/flyer-self-evaluation.py` is refactored in C4 to **import** these and replace its local literal at line 1099-1101 (folded from drift-reviewer H1). A pytest assertion (`test_verdict_uses_shared_severity_rank`) imports both and asserts they reference the same constant — future re-introduction of a parallel literal fails CI.

```python
SEVERITY_RANK: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def incident_color(incidents: list[dict]) -> Literal["green", "yellow", "red"]:
    """Single-sourced incident-only color threshold.

    Imported by tools/flyer-self-evaluation.py to replace its local
    severity_rank literal. Adding a new severity (e.g., "info":0) edits
    one location.
    """
    worst = max(
        (SEVERITY_RANK.get(str(it.get("severity")), 0) for it in incidents),
        default=0,
    )
    return "red" if worst >= 3 else ("yellow" if worst >= 2 else "green")


def compute_rollout_verdict(
    *,
    incidents: list[dict],
    fixture: RolloutInputFixture | None,
    manual_stale_red_minutes: int = 30,
) -> tuple[Literal["green", "yellow", "red"], list[dict]]:
    """Compute rollout verdict + reasons (each reason = {severity, text}).

    Reasons are returned in the order red-first then yellow, to support
    Markdown rendering without re-sorting (rollout reviewer Important #2).
    """

    red_reasons: list[dict] = []
    yellow_reasons: list[dict] = []

    color = incident_color(incidents)
    if color == "red":
        red_reasons.append({"severity": "red", "text": "self-eval incident severity is red"})
    elif color == "yellow":
        yellow_reasons.append({"severity": "yellow", "text": "self-eval incident severity is yellow"})

    def _bump_red(text: str) -> None:
        red_reasons.append({"severity": "red", "text": text})

    def _bump_yellow(text: str) -> None:
        yellow_reasons.append({"severity": "yellow", "text": text})

    # Active customer-risk incident-type checks
    for inc in incidents:
        details = inc.get("evidence_details") or {}
        active = bool(details.get("active_customer_risk"))
        kind = inc.get("type")
        if kind == "customer_copy_internal_leak" and active:
            _bump_red("active customer-copy internal leak in outbound text")
        elif kind == "duplicate_initial_ack" and active:
            _bump_red("active duplicate initial acknowledgement to same customer")
        elif kind == "manual_source_edit_stale":
            age = details.get("queued_age_minutes")
            if isinstance(age, (int, float)) and age >= manual_stale_red_minutes:
                _bump_red(
                    f"manual source-edit queue stale ≥{manual_stale_red_minutes}min "
                    f"(age={age:.1f})"
                )
            else:
                _bump_yellow("manual source-edit queue rows present")
        if active and kind not in {
            "customer_copy_internal_leak",
            "duplicate_initial_ack",
            "manual_source_edit_stale",
        }:
            _bump_yellow(f"active customer-risk incident: {kind}")

    if fixture is None:
        _bump_yellow("readiness input fixture not supplied; posture unknown")
    else:
        if fixture.bridge_status in {"disconnected", "inactive"}:
            _bump_red("bridge is disconnected on the host")
        if fixture.gateway_status == "inactive":
            _bump_red("Hermes gateway is inactive on the host")
        if fixture.bridge_status == "unknown":
            _bump_yellow("bridge status posture unknown")
        if fixture.gateway_status == "unknown":
            _bump_yellow("gateway status posture unknown")
        if fixture.cockpit_status == "unknown":
            _bump_yellow("cockpit status posture unknown")
        if fixture.cockpit_status == "degraded":
            _bump_yellow("cockpit reports degraded health")

        risky_labels = {
            "customer-routing",
            "lifecycle",
            "copy",
            "payment",
            "schema-migration",
            "deploy-gate",
            "security",
            "auth",
        }
        if fixture.merged_not_deployed:
            risky = [pr for pr in fixture.merged_not_deployed if pr.customer_risk_label in risky_labels]
            if risky:
                _bump_red(
                    "merged-not-deployed includes customer-risk PRs: "
                    + ", ".join(f"#{p.number}" for p in risky)
                )
            else:
                _bump_yellow(
                    "merged-not-deployed PRs present: "
                    + ", ".join(f"#{p.number}" for p in fixture.merged_not_deployed)
                )

        risky_open = [pr for pr in fixture.open_prs if pr.customer_risk]
        if risky_open:
            _bump_yellow(
                "open PRs flagged customer-risk: "
                + ", ".join(f"#{p.number}" for p in risky_open)
            )

        if fixture.deploy_marker == "":
            _bump_yellow("deploy marker not supplied")

        posture, posture_reason = compute_source_edit_posture(fixture)
        if posture != "configured_with_smoke":
            _bump_yellow(posture_reason)

        if fixture.replay_summary is None:
            _bump_yellow("replay summary not supplied; rollout decision is unsafe without it")
        elif fixture.replay_summary.failed_ids:
            _bump_red(
                "rollout replay failed: " + ", ".join(fixture.replay_summary.failed_ids)
            )

    reasons = red_reasons + yellow_reasons
    if red_reasons:
        return "red", reasons
    if yellow_reasons:
        return "yellow", reasons
    return "green", reasons
```

### Rollout-readiness section builder

```python
def build_rollout_section(
    *,
    incidents: list[dict],
    fixture: Optional[RolloutInputFixture],
    manual_stale_red_minutes: int = 30,
) -> dict:
    """Build the `rollout.*` block injected into self-evaluation JSON."""

    # Cache the posture call (folded from rollout-reviewer I3).
    posture, posture_reason = (
        compute_source_edit_posture(fixture) if fixture is not None else ("unset", "")
    )

    verdict, reasons = compute_rollout_verdict(
        incidents=incidents,
        fixture=fixture,
        manual_stale_red_minutes=manual_stale_red_minutes,
    )

    def _count(kind: str) -> int:
        return sum(1 for it in incidents if it.get("type") == kind)

    return {
        "verdict": verdict,
        "reasons": reasons,
        "open_flyer_prs": [pr.model_dump() for pr in (fixture.open_prs if fixture else [])],
        "merged_not_deployed": [
            pr.model_dump() for pr in (fixture.merged_not_deployed if fixture else [])
        ],
        "deploy_marker": fixture.deploy_marker if fixture else "",
        "bridge_status": fixture.bridge_status if fixture else "unknown",
        "gateway_status": fixture.gateway_status if fixture else "unknown",
        "cockpit_status": fixture.cockpit_status if fixture else "unknown",
        "source_edit_posture": posture,
        "source_edit_posture_reason": posture_reason,  # folded from drift-reviewer N2
        "stale_manual_queue_incidents": _count("manual_source_edit_stale"),
        "active_customer_risk_incidents": sum(
            1
            for it in incidents
            if (it.get("evidence_details") or {}).get("active_customer_risk") is True
        ),
        "customer_copy_leak_incidents": _count("customer_copy_internal_leak"),
        "duplicate_initial_ack_incidents": _count("duplicate_initial_ack"),
        "replay_summary": (
            fixture.replay_summary.model_dump() if fixture and fixture.replay_summary else None
        ),
    }
```

## CLI surface — `tools/flyer-self-evaluation.py`

Added args (additive; no existing arg is renamed or removed):

```
--rollout-readiness                 Enable the rollout block in the report.
--rollout-input PATH                Input fixture JSON (RolloutInputFixture). Required if --rollout-readiness.
                                    (Renamed from --input per drift-reviewer H3 to avoid
                                    collision with any future generic fixture arg.)
--rollout-replay-summary-json PATH  Ad-hoc override for fixture.replay_summary (operator runs).
--manual-stale-red-minutes N        Lower the RED threshold for manual_source_edit_stale
                                    (default 30; matches detector default).
```

`build_report` gains an optional `rollout_fixture: Optional[RolloutInputFixture] = None` parameter; when present, calls `build_rollout_section` and assigns to `report["rollout"]`. `build_report` also forwards the `manual_stale_red_minutes` value into `build_rollout_section` (folded from rollout-reviewer N1 — CLI flag is threaded end-to-end). The existing `report.status` field is preserved unchanged.

Within `build_report`, the existing inline severity-rank literal at line 1099-1101 is **deleted** and replaced with `from agents.flyer.rollout_readiness import incident_color` + `status = incident_color(incidents)` — single-sourced colors (folded from drift-reviewer H1).

Markdown render gains a top banner when `report["rollout"]` exists. **Empty-reasons GREEN case drops the "— N reasons" suffix** (folded from rollout-reviewer I2 + drift-reviewer I3):

```
# Flyer Self-Evaluation

**Rollout: RED — 2 reasons**

- Status: yellow            ← existing incident status
...

## Rollout Readiness

- Verdict: RED
- Bridge: connected; Gateway: active; Cockpit: healthy
- Deploy marker: deploy-20260520-184411-ee8533a0
- Source-edit posture: manual_review (source-edit runs through manual_review fallback)
- Open Flyer PRs: #154 schedule-through-day-ranges
- Replay summary: 11/11 passed

### Reasons (RED first)

- RED: bridge is disconnected on the host
- YELLOW: source-edit runs through manual_review fallback
```

For the GREEN happy path:

```
**Rollout: GREEN**
```

(no `— 0 reasons` suffix; if reasons list is empty the renderer skips the suffix.)

## CLI surface — `tools/operator-brief.py`

`summarize_flyer_evaluation_report` gains: if `payload.get("rollout")` is present, emit the banner line as the first Flyer Studio line: `"Rollout: RED — 2 reasons"`. Existing summary lines remain unchanged. ~25 LOC.

## Helper extraction: `tests/_flyer_replay_helpers.py`

Move `_install_common_replay_mocks`, `_NoopFileLock`, `_load_plugin_modules`, `_load_create_script`, `_event`, `_write_customer_state`, `_real_create_project`, and `_assert_expected_route` from `tests/test_flyer_incident_replay.py` into the new helper module. Both `test_flyer_incident_replay.py` and `test_flyer_rollout_replay.py` import from this helper.

`_assert_expected_route` gains a new `onboarding` route branch (see fixture section above) — extension is behavior-preserving because no incident-replay fixture sets `route="onboarding"` today.

`REPO = Path(__file__).resolve().parents[1]` (line 16 in the old location) is preserved unchanged in the helper module — `tests/_flyer_replay_helpers.py` is still under `tests/`, so `parents[1]` resolves to the repo root identically (confirmed by drift-reviewer I1).

Risk surface: any change to a helper now affects both tests; both must be re-run in CI when the helper changes. Trade-off accepted (drift reviewer Important #1).

## Fixture: `tests/fixtures/flyer_rollout_replay/flyer_rollout_paths.json`

Format: **top-level JSON array** of fixture objects, identical shape to `tests/fixtures/flyer_incident_replay/flyer_incidents.json` (folded from drift-reviewer H2 — keep the deployed shape; do not invent a versioned envelope).

```jsonc
[
  { "id": "rollout-active-trial-sample-idea-approves-into-project", ... },
  ...
]
```

The cross-ref ids that the rollout-replay test resolves from the incident-replay fixture file are a **module-level constant in `tests/test_flyer_rollout_replay.py`**, NOT inside the fixture file:

```python
INCIDENT_REPLAY_CROSS_REFS = (
    "vague-create-flyer-clarifies-without-project",
    "small-revision-make-it-red-stays-revision",
    "F0063-source-choice-queues-manual-edit",
    "status-check-does-not-create-or-revise",
)
```

7 net-new fixture IDs (canonical list; total replay scenarios = 7 net-new + 4 cross-ref = **11**, matching plan §Item 3):

1. `rollout-active-trial-sample-idea-approves-into-project` (raw text ≥20)
2. `rollout-new-trial-sample-before-onboarding-into-compact-ideas`
3. `rollout-text-request-intelligent-brief-approves-into-project` (raw text ≥20)
4. `rollout-guided-flow-brief-approves` (raw text ≥20 — folded from rollout-reviewer H1; promoted to a long-text echo anchor)
5. `rollout-visible-text-removal-stays-revision` (raw text ≥20, exercises #157)
6. `rollout-lid-only-start-free-trial-into-onboarding`
7. `rollout-duplicate-phone-second-sender-recognized-as-authorized-requester` (raw text ≥20)

Long-text echo-coverage anchors: #1, #3, #4, #5, #7 (raw text ≥20). Short-text fixtures (#2, #6) assert only the 7 fixed-token categories.

**LID-only route handling (folded from rollout-reviewer I1).** The existing `_assert_expected_route` switch in `test_flyer_incident_replay.py:329` does not include an `onboarding` route token. The helper extraction (C1) adds a new `onboarding` branch to the switch:

```python
elif route == "onboarding":
    # Fixture #6: LID-only Start Free Trial -> onboarding intercept
    # The cf-router consumes the message via _try_flyer_existing_onboarding_intercept
    # or _try_flyer_intake_intercept and returns a non-None dispatch result.
    assert result is not None
    assert "trigger_create_flyer_project" not in calls
    assert "invoke_update_flyer_project" not in calls
```

The C1 commit ships this switch extension alongside the helper extraction; the existing incident-replay tests pass because none of them use the new `onboarding` route token.

## Test plan

In-process unit tests in `tests/test_flyer_rollout_readiness.py` (10 tests):

```python
def test_input_fixture_extra_forbid()
def test_verdict_green_when_all_clear()
def test_verdict_yellow_on_unset_source_edit_policy()
def test_verdict_yellow_on_configured_no_smoke()
def test_verdict_yellow_on_merged_not_deployed_low_severity()
def test_verdict_red_on_merged_not_deployed_customer_routing_label()
def test_verdict_yellow_on_bridge_unknown_no_posture()
def test_verdict_red_on_customer_copy_leak_active_risk()
def test_verdict_red_on_replay_failed()
def test_verdict_red_on_disconnected_bridge()
def test_verdict_red_on_manual_source_edit_stale_at_30_min()
def test_verdict_yellow_when_replay_summary_missing()
def test_source_edit_posture_all_five_states()
def test_verdict_uses_shared_severity_rank()  # imports SEVERITY_RANK from rollout_readiness AND asserts flyer-self-evaluation references the same object — future re-introduction of a parallel literal fails CI (folded from drift-reviewer "Test list" guidance)
```

Replay tests in `tests/test_flyer_rollout_replay.py` (11 scenario tests = 7 net-new + 4 cross-ref):

```python
@pytest.mark.parametrize("fixture", _rollout_fixtures(), ids=lambda f: f["id"])
def test_rollout_replay_fixture(fixture, tmp_path, monkeypatch):
    hooks, actions, calls, audits, sent, identity_calls = _install_common_replay_mocks(
        monkeypatch, tmp_path, fixture
    )
    # ... same per-mode setup as test_flyer_incident_replay ...
    result = hooks.pre_gateway_dispatch(_event(fixture))
    # Same route assertions (uses extracted helper):
    _assert_expected_route(fixture, fixture["expect"], result, calls, audits, sent, [])
    # Existing 7-token copy guard:
    for text in sent:
        assert not scan_customer_text(text, raw_request=fixture["text"]).hits, text
    # Explicit echo-leak assertion for long-text fixtures only.
    # CRITICAL: the outer loop is `for text in sent` so `text` is bound before
    # scan_customer_text() is called. Reviewer C1 caught the wrong loop-order
    # form which silently passes by reusing the leaked `text` from the prior
    # loop. The correct form below evaluates the scan per-message.
    if len(_normalize_for_copy_policy(fixture["text"])) >= 8:
        echo_hits = [
            hit
            for text in sent
            for hit in scan_customer_text(text, raw_request=fixture["text"]).hits
            if hit.category == "raw_request_echo"
        ]
        assert echo_hits == [], (echo_hits, sent)
```

CLI smoke (subprocess):
- `python tools/flyer-self-evaluation.py --rollout-readiness --rollout-input tests/fixtures/flyer_rollout_readiness/green.json --projects /tmp/empty.json --decisions-log /tmp/empty.log --format json` — assert `rollout.verdict == "green"` and `rollout.reasons == []`
- Same with `--rollout-input ... yellow.json` and `red.json` — assert verdicts
- `python tools/flyer-self-evaluation.py --rollout-readiness --rollout-input ... red.json --format markdown` — assert banner `**Rollout: RED — N reasons**` present
- `python tools/flyer-self-evaluation.py --rollout-readiness --rollout-input ... green.json --format markdown` — assert banner `**Rollout: GREEN**` present and no `— 0 reasons` suffix

## Verification (full command list)

```bash
python -m pytest \
  tests/test_flyer_incident_replay.py \
  tests/test_flyer_rollout_replay.py \
  tests/test_flyer_rollout_readiness.py \
  tests/test_flyer_self_evaluation.py \
  tests/test_operator_brief.py \
  tests/test_cf_router_flyer_routing.py \
  tests/test_flyer_customer_copy_policy.py \
  -q

python -m py_compile \
  tools/flyer-self-evaluation.py \
  tools/operator-brief.py \
  src/agents/flyer/rollout_readiness.py \
  tests/_flyer_replay_helpers.py \
  tests/test_flyer_rollout_replay.py \
  tests/test_flyer_rollout_readiness.py

# CLI smoke (each command writes JSON/Markdown to a temp file then asserts content via grep)
python tools/flyer-self-evaluation.py --rollout-readiness --input <green>.json ... --format json
python tools/flyer-self-evaluation.py --rollout-readiness --input <red>.json ... --format markdown

git diff --check
```

## Risks (delta from plan)

Plan risks 1–5 stand. New design-time risk:

6. **`_install_common_replay_mocks` cross-file dependency.** Once extracted into `tests/_flyer_replay_helpers.py`, any change to the mock surface must update both replay tests in the same PR. Mitigation: the helper is intentionally small (~80 LOC) and stable. The extraction commit (C1) ships ahead of any rollout-replay scenarios so the existing incident-replay test proves the extraction is behavior-preserving.

## Acceptance (mapped to user's list)

Unchanged from plan:
- Customer-readiness report tells operator green/yellow/red for rollout ✓
- ≥8 rollout replay scenarios ✓ (11 in the canonical mapping)
- Active customer-risk separated from historical/audit-only ✓
- Existing #150 #151 #152 #155 #157 #158 behavior intact ✓
- #154 / #159 sequencing documented ✓
- Focused tests pass ✓
- py_compile passes ✓
- git diff --check passes ✓
- PR summary includes files / tests / risks / deferred items / "No deploy performed." ✓

## Out of scope (single section)

No deploy. No WhatsApp sends. No mutation of customer / payment / manual-queue state. No provider routing changes. No new paid-model smoke. No dashboard UI. No broad refactor of `flyer-self-evaluation.py` (extension is additive: new helper functions + 1 mode flag).

## Intercept-harness coverage caveat (PR-review H1/H2 honest labelling)

The 4 brief-builder rollout fixtures (sample-idea / new-trial-sample-before-onboarding / text-brief / guided-brief) re-enable `_try_flyer_intake_intercept` / `_try_flyer_existing_onboarding_intercept` / `_try_flyer_account_intercept` to return a stub dispatch dict. They do **not** call PR #158's real intercept logic; they verify the cf-router contract that, when an intercept returns a dict, `pre_gateway_dispatch` returns that dict and does NOT fall through to `trigger_create_flyer_project` / `invoke_update_flyer_project`. The lifecycle / copy correctness of the brief-builder's own logic is covered by PR #158's own unit tests (e.g. `tests/test_flyer_onboarding.py`, `tests/test_flyer_routing.py` via cf-router), not by this rollout-replay set.

The intercept-with-reply variant fixture (`rollout-intercept-reply-exercises-echo-guard`) is the **one** scenario where the intake intercept produces a real outbound `sent` message and the `raw_request_echo` guard runs against non-empty text. The other intercept-consumed scenarios run the echo guard against `sent == []` (trivially clean).

Deferred follow-up: a real-intercept rollout-replay layer that calls the actual `_try_flyer_intake_intercept` after staging realistic state, so brief-builder lifecycle and copy are gated by the rollout verdict directly. Owner: next session.

## PR-review findings folded

Both PR reviewers returned APPROVE / APPROVE WITH CHANGES. Folds:

| Reviewer | Severity | Finding | Folded into |
|---|---|---|---|
| Rollout | Critical C1 | `incident_color` ignores `active_customer_risk` — rollout would flip RED on historical incidents | New `active_incident_color` helper in `rollout_readiness.py`; `compute_rollout_verdict` calls it; `report.status` keeps `incident_color` (full set) for operator-incident view |
| Rollout | H1 | Brief-builder rollout fixtures are intercept-dict smokes, not real PR #158 traversal | Honest labelling above + deferred-item for real-intercept layer |
| Rollout | H2 | Echo guard runs on empty `sent` for intercept-consumed fixtures | Added `rollout-intercept-reply-exercises-echo-guard` fixture; intake_with_reply mode records outbound reply into shared `sent` list |
| Rollout | H3 | `malformed_business_name_fact` hardcoded `active_customer_risk: True` regardless of project status | Patched `tools/flyer-self-evaluation.py:647` to use `active_customer_risk(project)` |
| Rollout | I1 | `configured_with_smoke_stale` schema fields never validated against `provider_routing_changed_at` | Deferred — conservative-bias follow-up filed in tasks/todo.md |
| Rollout | I3 | 2026-05-21 routing-preview-mirrors-live-exception-gates lesson not exercised | Deferred follow-up filed in tasks/todo.md |
| Rollout | I4 | `merged_not_deployed` RED labels expanded vs design table (schema-migration/deploy-gate/security/auth) | Acknowledged in `risky_labels` set; design risk-label Literal lists all 8 |
| Hermes-first | H1 | Conservative-bias on `configured_with_smoke` not cross-checking age | Deferred — same item as Rollout I1 |
| Hermes-first | Nit | Local `import json as _json` / `import Path as _Path` inside functions | Acceptable (no shadowing); kept |
| Hermes-first | Nit | `compute_source_edit_posture` called twice in `build_rollout_section` | Cached once; the verdict aggregator re-resolves the reason string but the cost is trivial |

## Design-review findings folded

Both design reviewers returned APPROVE WITH CHANGES; all Critical / High / Important findings have been folded into the sections above. Brief receipt:

| Reviewer | Severity | Finding | Folded into |
|---|---|---|---|
| Rollout | Critical C1 | Echo-leak comprehension loop order broken | Replay test snippet updated; comment added explaining the bug |
| Rollout | H1 | Scenario count off by 1 | Bumped to 7 net-new + 4 cross-ref = 11; `rollout-guided-flow-brief-approves` promoted to long-text anchor |
| Rollout | H3 | Missing PR risk labels | `CustomerRiskLabel` Literal + `risky_labels` extended with schema-migration / deploy-gate / security / auth |
| Rollout | I1 | `_assert_expected_route` lacks `onboarding` route | Helper extraction (C1) adds an `onboarding` branch |
| Rollout | I2 | Empty-reasons GREEN renders awkwardly | Markdown renderer drops `— 0 reasons` suffix when reasons empty |
| Rollout | I3 | Double-call to `compute_source_edit_posture` | Cached once in `build_rollout_section` |
| Rollout | N1 | `--manual-stale-red-minutes` flag thread | `build_report` forwards the value to `build_rollout_section` |
| Rollout | N2 | severity_rank doesn't gate on active_customer_risk | Accepted as conservative for next-few-days rollout per reviewer |
| Drift | Critical C1 | PEP 604 `X | None` drifts from `Optional[X]` convention | Switched to `Optional[int]` / `Optional[datetime]` / `Optional[RolloutReplaySummary]` |
| Drift | Critical C2 | `provider_routing_changed_at_iso: str` should be `datetime` | Renamed to `provider_routing_changed_at: Optional[datetime]` |
| Drift | H1 | SEVERITY_RANK double-defined | Moved into `rollout_readiness.py` + imported by `flyer-self-evaluation.py` |
| Drift | H2 | Fixture file shape drifts from deployed | Top-level JSON array; cross_refs moved to test module constant |
| Drift | H3 | `--input` collision risk | Renamed to `--rollout-input` |
| Drift | I3 | Empty-reasons GREEN | Same as Rollout I2 |
| Drift | I4 | Banner placement | Kept above incidents block per design; revisit if operator pushback |
| Drift | N1 | 6 vs 7 fixture confusion | Reconciled to 7 + 4 cross-ref = 11 |
| Drift | N2 | Posture reason discarded | Surfaced as `source_edit_posture_reason` in JSON |

## Process

This design is APPROVED WITH CHANGES by both reviewers; all foldable findings are applied above. Build proceeds in commit sequence C1 → C7.
