# P0 #2 Design — Severity-tiered Flyer visual QA + warn-only draft delivery path

**Date:** 2026-05-28
**Builds on:** `tasks/p0-2-severity-tiered-qa-plan-2026-05-28.md` (commit `a3f64fa`)
**Branch:** `plan/p0-2-severity-tiered-qa-2026-05-28`
**Drift-check tag:** `extends-Hermes` (unchanged from plan)
**New primitives introduced:** none beyond plan.

This design pins the implementation details for the 6-commit build sequence — concrete function signatures, the three §9 design-phase question resolutions, test fixtures from live F0108/F0109 sidecars, deploy-gate additions, and operational runbook. The plan describes WHAT changes; this design describes HOW.

---

## 0. Workspace + module-path conventions (note for future reviewers)

**Workspace.** This design + plan live at `C:\projects\sme-agents-pr-zeta-1b\` on branch `plan/p0-2-severity-tiered-qa-2026-05-28` (off `origin/main` HEAD `f7ad477`). The sibling workspace `C:\projects\sme-agents\` is on the stale branch `codex/flyer-full-autonomous-recovery` (HEAD `ca41a84`, 396 commits behind main). **Read code from the plan-branch worktree.** On the stale branch, recently-added flyer modules including `visual_qa.py`, `customer_copy_policy.py`, `semantic_brief.py`, `action_registry.py`, and `manual_queue.py` are not present — reading from there reproduces the false-alarm pattern that affected reviewer 1 round 1 and Finding 3 of the design review.

To verify locally: `cd C:/projects/sme-agents-pr-zeta-1b && git log --oneline HEAD -1` should show this branch's HEAD; `ls src/agents/flyer/*.py | wc -l` should report 21.

**Module-path conventions.** Two contexts; the schemas import is identical between them (only the sys.path source differs), but the flyer modules use different forms because of the flat-deploy rename.

| Module type | Local (pytest) | Deployed (smoke test, runtime cf-router) |
|---|---|---|
| `schemas` | `from schemas import ...` — `tests/conftest.py:21` inserts `<repo>/src/platform/` into `sys.path` | `from schemas import ...` — smoke test inserts `/opt/shift-agent/platform/` into `sys.path` |
| `safe_io` | `from safe_io import ...` — same conftest path setup | `from safe_io import ...` — same smoke setup |
| flyer modules | `from agents.flyer.<name> import ...` — conftest inserts `<repo>/src/`, so `agents/` is a package | `from flyer_<name> import ...` — `shift-agent-deploy.sh` renames `src/agents/flyer/<name>.py` → `/opt/shift-agent/flyer_<name>.py` flat |

`shift-agent-deploy.sh` is the source of the rename. Anything that runs on the VPS — smoke tests and runtime cf-router code — uses the flat `flyer_<name>` names. The dual-mode `try flyer_X / except: try agents.flyer.X` pattern at `actions.py:4003-4011` exists for runtime code that may execute in either context.

Neither context uses `from platform.schemas import ...` — that would only work if `src/` were on `sys.path` AND `platform/` were a package (it isn't — `platform.py` is a stdlib module). The pattern is always "insert the schemas directory directly, then flat-import."

---

## 1. Hermes-first delta from plan

No new domains. The §2 plan analysis (7 reuse rows, 0 new primitives) holds. This design adds one concrete reuse: the cf-router reply-classifier surface (`is_flyer_send_now_intent`, `is_flyer_revision_intent`, `is_flyer_delivery_state_intent`, bare-`approve` match at `actions.py:2806`) is re-used for `delivered_with_warning` source status. No new classifier; only source-status allowlist extension at the intercept site.

---

## 2. §9 Q1 RESOLVED — Customer reply parsing for warn-tier delivery

**Question:** "OK" / "looks good" / "approve" replies on `delivered_with_warning` projects — transition to `awaiting_final_approval` then approval, or directly to approved?

**Resolution:** Route via `awaiting_final_approval`. The FLYER_TRANSITIONS edge `delivered_with_warning → awaiting_final_approval` (plan Commit 1) is the load-bearing change; the existing approval-classifier path then takes over without modification.

**Why via `awaiting_final_approval`:** the existing `awaiting_final_approval → delivered` transition is gated by `finalize-flyer-assets` (4-asset packaging + bridge dispatch). Skipping that gate would mean warn-tier "OK" replies ship concept previews as final assets without packaging, which breaks the asset-versioning contract. Two-step preserves the contract.

**Concrete reply-classifier extension (no new function; allowlist add).** The intercept lives in `_try_flyer_active_project_intercept` in `src/plugins/cf-router/hooks.py`. Today's source-status allowlist for the approval path covers `revising_design` and `awaiting_final_approval` (see `hooks.py:2808` per the docstring at `actions.py:2770`). Extension:

```python
# hooks.py — _try_flyer_active_project_intercept finalization gate
FINALIZATION_SOURCE_STATUSES = (
    "revising_design",
    "awaiting_final_approval",
    "delivered_with_warning",  # NEW — warn-tier "OK" reply routes here
)
```

**Conflict resolution — approval + revision in the same message.** Reviewer 2 #3 flagged the risk that a customer might reply "OK looks great just fix the spelling" (joint approval + revision). Existing precedent at `actions.py:2774-2777` already documents the resolution: revision-intent wins over send-now-intent when both classify true. Apply the same precedent to `delivered_with_warning`. Specifically:

```python
# hooks.py — intercept body for delivered_with_warning source status
if status == "delivered_with_warning":
    if is_flyer_revision_intent(body):
        # Revision wins. Route to revising_design.
        # FLYER_TRANSITIONS edge: delivered_with_warning → revising_design (Commit 1)
        return _transition_to_revising_design(...)
    if is_flyer_send_now_intent(body) or _is_bare_approve(body):
        # Approval-equivalent. Route to awaiting_final_approval.
        return _transition_to_awaiting_final_approval(...)
    # Neither — fall through to existing PR-β guard / generic handling.
```

**Audit-row implications.** The reply-classifier transition writes existing `FlyerProjectStatusChanged` audit rows; no new variant needed for the reply itself. Commit 1's `FlyerWarnTierDelivered` covers the original warn-tier send; downstream transitions reuse existing audit shape.

**Test additions to Commit 4:**

- Replay: `delivered_with_warning` + body "OK" → asserts transition to `awaiting_final_approval` (NOT `delivered`).
- Replay: `delivered_with_warning` + body "approve" (bare) → same as above.
- Replay: `delivered_with_warning` + body "OK looks great just fix the spelling" → revision wins; transition to `revising_design`.
- Replay: `delivered_with_warning` + body "👍" / "thanks" / "noted" → no transition (falls through to generic).

---

## 3. §9 Q2 RESOLVED — Backfill of currently-stuck `manual_edit_required` projects

**Question:** Run a one-shot job to re-classify currently-`manual_edit_required` projects under the new severity rules + auto-deliver warn-tier ones?

**Resolution:** Defer to a follow-up PR. Out of scope for this build.

**Reasoning:**
1. The new classifier ships fresh; running it against historical projects is a separate risk surface (false-positive deliveries on stale projects whose customers have moved on).
2. The classifier needs ≥10 warn-tier deliveries of fresh observations before backfill thresholds are defensible (already noted in plan §10 for SLA watchdog; same logic applies here).
3. Operators can manually action stuck projects today via the existing manual-queue UI; this PR doesn't worsen that surface.

**Follow-up PR sketch (not in scope):** a `replay-warn-tier-on-stuck-projects` script that reads each stuck project's last `qa_report`, runs `classify_qa_severity`, and either (a) auto-transitions to `delivered_with_warning` + dispatches via cf-router, or (b) logs the would-be classification for operator dry-run review. Recommend (b) first, then graduate to (a) only after manual review confirms the classifier matches operator intuition on the historical sample.

**Runbook for operators in the interim:** currently-stuck projects stay in the manual queue; operator manual workflow unchanged.

---

## 4. §9 Q3 RESOLVED — Warning-summary clearance on re-QA

**Question:** When a `delivered_with_warning` project re-enters QA (via `revising_design` revision), and the re-QA produces a different set of blockers (some old ones resolved, new ones present), does the `project.warning` payload get replaced or merged?

**Resolution:** **Replace.** Each successful re-QA pass writes a fresh `FlyerWarningSummary` reflecting only the current QA outcome. The audit log preserves the history (`FlyerWarnTierDelivered` rows accumulate per delivery), so the operator can review prior warnings via the cockpit's audit timeline.

**Reasoning:**
1. `project.warning` answers "what's the current state of the most recent delivery?" — a merge would conflate resolved and current blockers and confuse the cockpit display.
2. The audit log is the historical record; `project.warning` is the live operational state. Same separation-of-concerns as `project.manual_review` (current queue state) vs the audit log's `_FlyerManualReviewQueued` history.
3. If a re-QA produces ZERO warnings (severity `pass`), `project.warning` is cleared to `None` — same semantics as "the warning is resolved."

**Concrete implementation point.** In `generate-flyer-concepts` (plan Commit 3), the warn-tier write block:

```python
# Severity-branch warn path
store.projects[snapshot_idx] = current.model_copy(update={
    "status": "delivered_with_warning",
    "warning": FlyerWarningSummary(
        severity="warn",
        blockers=list(blockers),                   # CURRENT re-QA blockers only
        customer_text=customer_text,
        customer_text_sha256=hashlib.sha256(customer_text.encode()).hexdigest(),
        delivered_at=now,
        asset_id=warn_asset_id,
        classifier_version="v1",
    ),
    # other fields unchanged
})
```

Replaces any prior `warning` payload outright. The model_copy `update={}` mechanism guarantees this (it overwrites named keys; doesn't merge nested fields).

**Severity-pass clearance.** In the same script's pass branch:

```python
# Severity-branch pass path (also covers re-QA after warn-tier revision)
store.projects[snapshot_idx] = current.model_copy(update={
    "status": "awaiting_concept_selection" if not one_shot else "awaiting_final_approval",
    "warning": None,                              # CLEAR prior warning on successful re-QA
    # other fields unchanged
})
```

**Cockpit display rule.** The Projects-tab panel (plan Commit 5) reads `project.warning` for "current warning." Clicking through to audit timeline shows all historical `FlyerWarnTierDelivered` rows for the project_id. Two surfaces, two responsibilities.

**Test additions to Commit 3:**

- Replay: project enters `revising_design` from `delivered_with_warning` with a prior `warning` payload → revision triggers re-QA → fresh warn-tier blockers → asserts `project.warning` REPLACED with new blockers, not merged.
- Replay: project enters `revising_design` from `delivered_with_warning` → revision triggers re-QA → severity `pass` → asserts `project.warning = None` (cleared).
- Replay: project goes warn → revision → warn → revision → pass. Asserts audit log contains TWO `FlyerWarnTierDelivered` rows; `project.warning` final state = None.

---

## 5. Concrete code patterns per commit

### Commit 1 — visual_qa.py classifier interfaces (plan §7 Commit 1)

```python
# src/agents/flyer/visual_qa.py — new exports

from dataclasses import dataclass

@dataclass(frozen=True)
class WarnTierBlockerSpec:
    pattern: re.Pattern[str]
    label: str
    is_core_promise: bool = False
    is_brand_identity: bool = False
    is_event_essential: bool = False

BLOCK_TIER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^placeholder text is visible in generated flyer$"), "placeholder"),
    (re.compile(r"^English-only flyer contains regional/non-English script$"), "regional_script"),
    (re.compile(r"^unrequested operational claim visible: "), "unrequested_claim"),
    (re.compile(r"^ocr/vision text unavailable for generated artifact"), "ocr_unavailable"),
    (re.compile(r"^replaced source text still visible: "), "source_text_visible"),
    (re.compile(r"^missing required visible fact: business_name$"), "missing_business_name"),
    # quality_notes substring matches (already forwarded as blockers via line 493 substring filter):
    (re.compile(r"placeholder|unreadable|garbled", re.IGNORECASE), "quality_note_corruption"),
)

WARN_TIER_PATTERNS: tuple[WarnTierBlockerSpec, ...] = (
    WarnTierBlockerSpec(
        re.compile(r"^visible wrong business/brand: (?P<name>.+)$"),
        "brand_variant",
        is_brand_identity=True,
    ),  # severity decided by _is_brand_typo gate, not pattern alone
    WarnTierBlockerSpec(re.compile(r"^missing required visible fact: location$"),
                        "missing_location", is_event_essential=True),
    WarnTierBlockerSpec(re.compile(r"^missing required visible fact: contact_info$"),
                        "missing_contact_info"),
    WarnTierBlockerSpec(re.compile(r"^missing required visible fact: schedule$"),
                        "missing_schedule", is_event_essential=True),
    WarnTierBlockerSpec(re.compile(r"^missing required visible fact: promotion_end$"),
                        "missing_promotion_end", is_event_essential=True),
    WarnTierBlockerSpec(re.compile(r"^missing required visible fact: item:\d+:name$"),
                        "missing_item_name", is_core_promise=True),
)

WARN_TIER_COMBINATION_LIMIT: int = 3
CORE_PROMISE_ESCALATION_LIMIT: int = 2

def classify_qa_severity(
    report: FlyerVisualQAReport,
    *,
    project: FlyerProject,
) -> Literal["pass", "warn", "block"]:
    """Pure classifier over report.blockers + project.business_name.
    Returns one of pass/warn/block per the plan's rule order."""
    block_hits: list[str] = []
    warn_specs: list[WarnTierBlockerSpec] = []
    for blocker in report.blockers:
        for pattern, _ in BLOCK_TIER_PATTERNS:
            if pattern.search(blocker):
                block_hits.append(blocker)
                break
        else:
            for spec in WARN_TIER_PATTERNS:
                m = spec.pattern.search(blocker)
                if m:
                    if spec.label == "brand_variant":
                        if not _is_brand_typo(m.group("name"), project.business_name):
                            block_hits.append(blocker)  # not-a-typo → block
                            break
                    warn_specs.append(spec)
                    break

    if block_hits:
        return "block"
    if sum(1 for s in warn_specs if s.is_core_promise) >= CORE_PROMISE_ESCALATION_LIMIT:
        return "block"
    if any(s.is_brand_identity for s in warn_specs) and any(s.is_event_essential for s in warn_specs):
        return "block"
    if len(warn_specs) >= WARN_TIER_COMBINATION_LIMIT:
        return "block"
    if warn_specs:
        return "warn"
    return "pass"

def _is_brand_typo(extracted: str, project_brand: str) -> bool:
    """AND-of-3 gate: distance ≤2, token overlap ≥0.5, prefix ≥4 OR overlap ≥0.75.
    All comparisons use >= per plan boundary-operator pin."""
    e = _normalize_brand_for_match(extracted)
    p = _normalize_brand_for_match(project_brand)
    if _edit_distance(e, p) > 2:
        return False
    et, pt = _brand_tokens(e), _brand_tokens(p)
    if not pt:
        return False
    overlap = len(et & pt) / len(pt)
    if overlap < 0.5:
        return False
    prefix_len = sum(1 for c1, c2 in zip(e, p) if c1 == c2)  # stops at first diff
    return prefix_len >= 4 or overlap >= 0.75
```

### Commit 4 — cf-router helper extraction (X2 architecture; Finding 1 fix)

**Why X2:** Reviewer Finding 1 (operator 2026-05-28) confirmed `send_flyer_concept_previews` has TWO hard-fail QA gates inside the per-concept loop — `validate_text_manifest_file:4027-4034` AND `validate_visual_qa_report:4035-4043`. The second gate trips on every warn-tier project (`report.status="failed"` by construction). The fix extracts a private helper with a `qa_policy` parameter; text-manifest QA stays strict in both policies (substrate-correctness), visual-QA-report gate flips warn-tolerant on the warn path.

```python
# src/plugins/cf-router/actions.py

def _send_concept_preview_media(
    chat_id: str,
    project: dict,
    concepts: list[dict],
    assets: dict[str, dict],
    qa_policy: Literal["strict", "warn_tolerant"],
    customer_text: Optional[str] = None,
) -> tuple[bool, str, str]:
    """Canonical concept-preview send. Extracted from the existing
    send_flyer_concept_previews body. Text-manifest QA always strict
    (substrate-correctness; template-parse failures aren't warn-tier-
    recoverable). Visual-QA-report status check is strict-only; warn-tolerant
    accepts report.status=='failed' because the upstream classifier
    (classify_qa_severity) already determined warn-tier is acceptable and
    project.warning captures the visible blockers for audit."""
    _ensure_platform_path()
    try:
        from safe_io import bridge_post, bridge_send_media  # type: ignore
    except Exception as e:
        return False, "", f"safe_io_import_failed: {type(e).__name__}: {e}"
    try:
        from flyer_render import validate_text_manifest_file  # type: ignore
    except Exception as e:
        return False, "", f"flyer_render_import_failed: {type(e).__name__}: {e}"
    try:
        from flyer_visual_qa import validate_visual_qa_report  # type: ignore
    except Exception:
        try:
            _ensure_local_src_path()
            from agents.flyer.visual_qa import validate_visual_qa_report  # type: ignore
        except Exception as e:
            return False, "", f"flyer_visual_qa_import_failed: {type(e).__name__}: {e}"

    outbound_ids: list[str] = []
    for concept in concepts:
        asset = assets.get(concept.get("preview_asset_id"))
        if not asset:
            continue

        # Text-manifest QA — ALWAYS strict in both policies
        qa = validate_text_manifest_file(
            asset.get("path", ""),
            project_id=project["project_id"],
            project_version=project.get("version"),
            output_format="concept_preview",
        )
        if not qa.ok:
            return False, "", "text_qa_failed: " + "; ".join(qa.blockers)

        # Visual-QA-report gate — strict in pass-tier, warn-tolerant in warn-tier
        visual = validate_visual_qa_report(
            asset.get("path", ""),
            project_id=project["project_id"],
            project_version=int(project.get("version") or 1),
            output_format="concept_preview",
            allow_sidecar=False,
        )
        if not visual.ok and qa_policy == "strict":
            return False, "", "visual_qa_failed: " + "; ".join(visual.blockers)
        # warn_tolerant: proceed; blockers are already captured in project.warning

        caption = (
            f"{concept.get('concept_id')}: {concept.get('title')}\n"
            f"{concept.get('style_summary')}\n\n"
            "Reply APPROVE or reply with changes."
        )
        try:
            from agents.flyer.action_registry import PROJECT_ACTIONS, build_action_context_for_command  # type: ignore
        except ImportError:
            from flyer_action_registry import PROJECT_ACTIONS, build_action_context_for_command  # type: ignore
        ok, mid, err, status = bridge_send_media(
            chat_id, asset.get("path", ""), caption=caption,
            action_context=build_action_context_for_command(PROJECT_ACTIONS, "concept_preview.media_send"),
        )
        if not ok:
            if status == "send_uncertain":
                return False, ",".join(outbound_ids), f"partial_delivery_uncertain: {status}: {err}"
            return False, "", f"{status}: {err}"
        try:
            _record_flyer_concept_preview_delivery(project["project_id"], str(asset.get("asset_id") or ""), mid)
        except Exception as e:
            return False, ",".join(outbound_ids + [mid]), f"delivery_persist_failed: {type(e).__name__}: {e}"
        outbound_ids.append(mid)

    if not outbound_ids:
        return False, "", "no concept previews to send"

    # Trailing CTA — Pin C: customer_text override site
    try:
        from agents.flyer.action_registry import PROJECT_ACTIONS as _PA_CTA, build_action_context_for_command as _bac_cta  # type: ignore
    except ImportError:
        from flyer_action_registry import PROJECT_ACTIONS as _PA_CTA, build_action_context_for_command as _bac_cta  # type: ignore
    cta_text = customer_text if customer_text is not None else "Reply APPROVE to receive final files, or reply with changes."
    ok, mid, err, status = bridge_post(
        chat_id, cta_text,
        action_context=_bac_cta(_PA_CTA, "concept_preview.cta_text"),
    )
    if not ok:
        return False, ",".join(outbound_ids), f"cta_send_failed: {status}: {err}"
    return True, ",".join(outbound_ids + [mid]), ""


def send_flyer_concept_previews(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Pass-tier concept-preview send. Signature unchanged from pre-PR
    (7 existing callers in hooks.py + 1 in actions.py preserved bit-for-bit).
    Now a thin wrapper over _send_concept_preview_media with strict QA policy."""
    project = _load_flyer_project_dict(project_id)
    if not project:
        return False, "", f"project_not_found: {project_id}"
    assets = {a.get("asset_id"): a for a in project.get("assets", [])}
    return _send_concept_preview_media(
        chat_id, project, project.get("concepts", []), assets,
        qa_policy="strict",
    )


def send_warn_tier_concept_previews(
    chat_id: str,
    project_id: str,
    customer_text: str,
) -> tuple[bool, str, str]:
    """Warn-tier concept-preview send. Reachable only via _dispatch_concept_preview_send.
    Relaxes visual-QA-report status check; keeps text-manifest QA strict.
    customer_text is REQUIRED (no default) — warn-tier delivery always needs
    correction-prompt copy, never the pass-tier APPROVE CTA."""
    project = _load_flyer_project_dict(project_id)
    if not project:
        return False, "", f"project_not_found: {project_id}"
    assets = {a.get("asset_id"): a for a in project.get("assets", [])}
    return _send_concept_preview_media(
        chat_id, project, project.get("concepts", []), assets,
        qa_policy="warn_tolerant",
        customer_text=customer_text,
    )


def _dispatch_concept_preview_send(chat_id: str, project_id: str) -> tuple[bool, str, str]:
    """Single point of change for the warn-tier branch. Reads project state,
    picks the right send wrapper. Replaces the 7 direct callers of
    send_flyer_concept_previews at hooks.py:746, 1848, 2795, 3081, 3310, 3486
    plus the one in actions.py."""
    project = _load_flyer_project_dict(project_id)
    if (
        project is not None
        and project.get("status") == "delivered_with_warning"
        and project.get("warning") is not None
    ):
        warning = project["warning"]
        warn_text = build_warn_tier_customer_text(warning["blockers"], project)
        return send_warn_tier_concept_previews(chat_id, project_id, warn_text)
    return send_flyer_concept_previews(chat_id, project_id)
```

**Notes:**
- `_load_flyer_project_dict` is the existing pattern at `actions.py:4015-4020` extracted into a helper for reuse across the three new wrappers + dispatcher. Open item §11: confirm if the helper already exists or needs adding (lean: add it once at build time; existing inline duplications in actions.py can be left untouched).
- The helper preserves the existing per-concept loop ordering: text-manifest QA → visual QA → send. Order matters because text-manifest gate is the cheapest (no network call); visual-QA-report is cheapest network-free; send is the expensive operation.
- Customer-text type signature: `Optional[str]` in helper, REQUIRED `str` in `send_warn_tier_concept_previews`. Asymmetric on purpose — warn-tier callers can't accidentally ship default APPROVE copy.

### Commit 5 — Cockpit backend route (plan §7 Commit 5 Pin D)

```python
# web/backend/app/routers/flyer.py — new audit-only mutation

from pydantic import BaseModel, Field

class FlagWarnTierBody(BaseModel):
    note: str = Field(default="", max_length=500)

@router.post("/flyer/projects/{project_id}/flag")
def flag_warn_tier_project(
    project_id: str,
    body: FlagWarnTierBody,
    operator: OperatorPrincipal = Depends(require_operator),
) -> dict:
    """Audit-only operator flag on a delivered_with_warning project.
    Writes FlyerOperatorFlaggedWarnTier via log-decision-direct.
    Does NOT mutate project state. No transitions, no manual queue."""
    project = read_flyer_project_or_404(project_id)
    if project.status != "delivered_with_warning":
        raise HTTPException(409, "project not in delivered_with_warning state")
    _log_decision_direct({
        "type": "FlyerOperatorFlaggedWarnTier",
        "project_id": project_id,
        "flagged_by_operator_id": operator.id,
        "flagged_at": _now_utc_iso(),
        "note": body.note,
    })
    return {"ok": True, "project_id": project_id}
```

### Commit 6 — LogEntry variants (plan §7 Commit 6 Pin B)

```python
# src/platform/schemas.py — three new audit row classes (public names)

class FlyerQASeverityClassified(_BaseEntry):
    type: Literal["FlyerQASeverityClassified"] = "FlyerQASeverityClassified"
    project_id: str = Field(min_length=1, max_length=40)
    asset_id: str = Field(default="", max_length=40)
    severity: Literal["pass", "warn", "block"]
    blocker_count: int = Field(ge=0, le=50)
    classifier_version: str = Field(default="v1", max_length=20)
    classified_at: datetime

class FlyerWarnTierDelivered(_BaseEntry):
    type: Literal["FlyerWarnTierDelivered"] = "FlyerWarnTierDelivered"
    project_id: str = Field(min_length=1, max_length=40)
    asset_id: str = Field(default="", max_length=40)
    severity: Literal["warn"]
    blockers: list[str] = Field(default_factory=list, max_length=50)
    customer_text_sha256: str = Field(default="", max_length=64)
    delivered_at: datetime

class FlyerOperatorFlaggedWarnTier(_BaseEntry):
    type: Literal["FlyerOperatorFlaggedWarnTier"] = "FlyerOperatorFlaggedWarnTier"
    project_id: str = Field(min_length=1, max_length=40)
    flagged_by_operator_id: str = Field(min_length=1, max_length=80)
    flagged_at: datetime
    note: str = Field(default="", max_length=500)

# In the Annotated[Union[...]] LogEntry alias at ~schemas.py:4685, append:
#   Annotated[FlyerQASeverityClassified, Tag("FlyerQASeverityClassified")],
#   Annotated[FlyerWarnTierDelivered, Tag("FlyerWarnTierDelivered")],
#   Annotated[FlyerOperatorFlaggedWarnTier, Tag("FlyerOperatorFlaggedWarnTier")],
```

---

## 6. Test fixtures (from live F0108/F0109 sidecars on main-vps)

Reviewer 3 confirmed actual blocker strings at `/opt/shift-agent/state/flyer/assets/F010{8,9}-C1-preview.png.qa.json`.

**F0108 warn-tier fixture:**

```json
{
  "project_id": "F0108",
  "blockers": ["visible wrong business/brand: Laksmi'S Kitchen"],
  "extracted_text": "...Lakshmi's Kitchen... LAKSMI'S KITCHEN ...",
  "status": "failed"
}
```

Classifier expectations:
- `_is_brand_typo("Laksmi'S Kitchen", "Lakshmi's Kitchen")` → distance 1, tokens `{laksmis, kitchen}` ∩ `{lakshmis, kitchen}` = `{kitchen}` overlap=0.5, prefix=`Laks`=4 → **True**
- `classify_qa_severity(...)` → 1 warn (brand-typo), no core-promise, no event-essential → `"warn"`
- `build_warn_tier_customer_text(...)` short summary: `"the spelling near the bottom"`
- Pass: `scan_customer_text` + `lint_no_unverified_completion` (peer)

**F0109 block-tier fixture:**

```json
{
  "project_id": "F0109",
  "blockers": [
    "missing required visible fact: location",
    "missing required visible fact: item:4:name",
    "missing required visible fact: item:5:name"
  ],
  "extracted_text": "...ANY ISALA... BENNE... BENNE DOSA... 90 BRYBAR DR ...",
  "status": "failed"
}
```

Classifier expectations:
- 2 core-promise warns (item:4, item:5) → CORE_PROMISE_ESCALATION_LIMIT triggers → `"block"`. (Count cap 3 ALSO triggers — either rule arrives at block.)

**Reviewer 2 combo-escalation fixture (hypothetical):**

```json
{
  "blockers": [
    "visible wrong business/brand: Laksmi'S Kitchen",
    "missing required visible fact: schedule"
  ]
}
```

- 1 brand-identity warn + 1 event-essential warn → `"block"` via combo escalation.

**Pass-shape fixture (regression):**

```json
{"blockers": []}
```

- `classify_qa_severity(...)` → `"pass"`. Existing pass-path tests must remain green.

---

## 7. Deploy gates — `shift-agent-smoke-test.sh` additions

Adds one symbol-import probe (per plan §8 smoke row). **Finding 2 (operator 2026-05-28)**: the deployed module-path convention is `sys.path.insert + from <flat_module> import`, NOT `from agents.flyer.<module>` or `from platform.schemas`. Modules deploy flat under `/opt/shift-agent/` as `flyer_<name>.py` (matching the existing pattern at `actions.py:4003` for `flyer_render` and `actions.py:4007` for `flyer_visual_qa`). Schemas deploy at `/opt/shift-agent/platform/schemas.py` and are imported as `from schemas import ...` after path insertion.

```bash
# In src/agents/shift/scripts/shift-agent-smoke-test.sh (mirrors deployed pattern at lines 170-174)

python3 -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
sys.path.insert(0, '/opt/shift-agent/platform')
from schemas import FlyerWarningSummary, FlyerQASeverityClassified, FlyerWarnTierDelivered, FlyerOperatorFlaggedWarnTier
from flyer_visual_qa import classify_qa_severity, _is_brand_typo
from flyer_customer_copy_policy import build_warn_tier_customer_text, format_warn_recovery_revision_ack
print('warn-tier symbols loadable')
" || { echo 'FAIL: warn-tier symbol import'; exit 1; }
```

**Cross-check before merge:** `shift-agent-deploy.sh` already handles the `src/agents/flyer/<name>.py → /opt/shift-agent/flyer_<name>.py` rename in its tarball-staging step. Confirm at build time that `visual_qa.py` and `customer_copy_policy.py` are included in the deploy manifest (existing pattern; deploy script should pick them up via wildcards, but worth a build-time grep).

**Rollout posture:** ship green tests + smoke-gate locally + deploy to main-vps. No env flag / feature gate — the severity branch is on by default because:
1. New code path only fires when QA `status="failed"`. Pre-PR behavior on `failed` was 100% manual queue; post-PR it's `warn` (auto-deliver) or `block` (manual queue). Block branch preserves prior behavior bit-for-bit.
2. Severity dictionary defaults conservative (every blocker has an explicit warn classification; everything else block).
3. Rollback is a single `git revert` of the 6-commit series.

---

## 8. Operational runbook — what operator sees on first warn-tier delivery

**Real-time:**
1. Audit log row written: `FlyerQASeverityClassified` (severity=warn) + `FlyerWarnTierDelivered`.
2. cf-router post-subprocess branch dispatches preview send + warn customer text. Customer receives concept previews + the warn-tier copy.
3. Cockpit Projects tab shows new project with `status=delivered_with_warning` + amber badge with blocker count.

**Operator first encounter:**
1. Filters Projects tab on `delivered_with_warning` → sees the new row.
2. Clicks to expand warning panel → reads blockers + exact customer copy delivered.
3. If satisfied (classifier did the right thing): no action needed. Project sits in `delivered_with_warning` until customer reply.
4. If concerned (e.g., classifier flagged something operator wouldn't have): clicks "Flag for follow-up" button → audit-only log written. No state mutation. Operator can then decide to manually queue the project via existing manual queue workflow (out of scope here).

**Customer reply paths from `delivered_with_warning`:**
- "Reply OK if you've checked..." → routes to `awaiting_final_approval` (§2). Existing finalize path.
- "Reply with the correction" → routes to `revising_design`. Existing revision path. Warn-recovery ack copy sent (plan Commit 2).
- Mixed signal → revision wins (§2 precedent).
- No reply → project sits in `delivered_with_warning`. No SLA watchdog yet (plan §10 deferred).

**Where to look for things going wrong:**
- Audit log: `grep -E "FlyerQASeverityClassified|FlyerWarnTierDelivered|FlyerOperatorFlaggedWarnTier" /opt/shift-agent/logs/decisions.log`
- Project state: `cat /opt/shift-agent/state/flyer/projects.json | jq '.projects[] | select(.status == "delivered_with_warning")'`
- Cockpit: filter by `delivered_with_warning` on Projects tab.

---

## 9. Risk register + rollback

**Known risks (post-build):**

| Risk | Impact | Mitigation |
|---|---|---|
| Classifier mis-classifies a real defect as warn | Customer sees embarrassing draft | Conservative dictionary (everything not explicitly warn → block); operator flag mutation surfaces mis-classifications to audit |
| Stuck `delivered_with_warning` project (no customer reply) | Audit row says "delivered" but customer never engaged | Plan §10 explicit defer — SLA watchdog follow-up PR once ≥10 warn-tier deliveries observed |
| Customer reply ambiguity (approval + revision) | Wrong transition fires | Revision-wins precedent already in cf-router; tests cover the joint case |
| `customer_text` parameter accidentally None on warn path | Warn-tier project ships with default "Reply APPROVE..." text instead of warn copy | Helper `_dispatch_concept_preview_send` is the single decision point; unit test asserts the warn path always builds + passes customer_text |
| Cockpit "Flag for follow-up" double-click | Two audit rows written | Backend route is idempotent at the audit-write level (operator + timestamp + project_id key); duplicate rows acceptable; not a state-mutation issue |

**Rollback plan:**
1. `git revert <6 commit SHAs>` on origin/main.
2. Redeploy via `shift-agent-deploy.sh`.
3. Existing manual-queue path resumes for all newly-failed QAs.
4. Projects currently in `delivered_with_warning` need a follow-up sweep: transition them either to `revising_design` (if customer still expects a fix) or `closed_no_send` (if conversation died). Operator-driven, ~5min per project.

---

## 10. Implementation order (locked)

1. **Commit 6** (`FlyerQASeverityClassified` + `FlyerWarnTierDelivered` LogEntry variants) — must land first so Commit 3 can write the audit rows.
2. **Commit 1** (severity classifier + `FlyerWorkflowStatus` extension + `FLYER_TRANSITIONS` matrix + `FlyerWarningSummary` schema).
3. **Commit 2** (warn-tier customer copy template + correction summary formatter + warn-recovery revision ack variant).
4. **Commit 3** (warn-tier severity branch in `generate-flyer-concepts` — writes state only).
5. **Commit 4** (`_dispatch_concept_preview_send` helper in cf-router + `customer_text` override on `send_flyer_concept_previews` + reply-classifier source-status allowlist).
6. **Commit 5** (Projects-tab filter row + warning panel + backend POST flag route + `FlyerOperatorFlaggedWarnTier` LogEntry variant).

Each commit ships green tests. Deploy after Commit 5 lands; smoke-gate verifies symbol load.

---

## 11. Open items deferred to build phase

- Exact regex for `_FLYER_SEND_NOW_PATTERN` / `_FLYER_DELIVERY_STATE_PATTERN` interactions with `delivered_with_warning` source status (plan §3 step 11 + §9 Q1 resolution; details land at coding time).
- `_load_flyer_project_dict` helper — design assumes it can be extracted at build time from the existing inline pattern at `actions.py:4015-4020`. Confirm at build start that the extraction is clean (no other call sites depend on the inline form); land it as a precursor commit if needed, OR inline the read in the three wrappers if extraction is messy.
- Customer-text byte length cap: `FlyerWarningSummary.customer_text` is capped at 2000 chars in schema; warn-tier template (~140 chars + correction summary ~50 chars) sits comfortably under. Verify in Commit 2 test fixture.
- Operator identity propagation through `FlagWarnTierBody` route: which `OperatorPrincipal` field is the audit-log identity (`id` vs `email` vs `username`)? Match the convention used by the existing cockpit mutation routes (e.g., manual-queue completion). Build-time grep + reuse.
- `shift-agent-deploy.sh` deploy-manifest inclusion: confirm at build time that `visual_qa.py` and `customer_copy_policy.py` are picked up by the tarball-staging step (existing wildcards should cover them, but worth a grep before merge to avoid post-deploy ImportError).

---

## 12. Review section (post-PR)

(Reserved for PR-time evidence: actual LOC, test counts, audit-row examples from canary, customer-completion delta, first 10 warn-tier deliveries breakdown.)
