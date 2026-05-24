"""Manual review queue helpers for Flyer Studio."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import mimetypes
import os
import re
import shutil

from schemas import (
    FlyerAsset,
    FlyerConcept,
    FlyerManualReview,
    FlyerManualReviewReason,
    FlyerProject,
    FlyerProjectStore,
    is_flyer_transition_allowed,
)


# Rows younger than this can only be closed with --force OR with a reason
# whose tokens include one of CLOSE_FRESH_OK_REASON_TOKENS. Silently closing
# a freshly-queued source-edit row (~9 min old) is the failure shape this
# guard prevents: the customer hasn't had time to even notice the queue ack
# before the row vanishes.
CLOSE_FRESH_MIN_AGE_MINUTES = 30

# Reason tokens that justify closing a fresh row without --force. Matched as
# discrete tokens delimited by non-alphanumeric characters on the lowercased
# reason string (NOT loose substring containment). So:
#   - "operator_burndown_duplicate_..." passes (duplicate delimited by `_`)
#   - "...provider_unavailable_after_retry" passes (exact multi-word token)
#   - "...provider_unavailable..." alone does NOT pass — it is a substring
#     of provider_unavailable_after_retry but not the documented token.
# Custom boundary `[^a-z0-9]` is used instead of `\b` because `\b` treats `_`
# as a word character; that would prevent `_duplicate_` from matching.
CLOSE_FRESH_OK_REASON_TOKENS = (
    "duplicate",
    "test",
    "superseded",
    "provider_unavailable_after_retry",
)
_CLOSE_FRESH_REASON_TOKEN_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:"
    + "|".join(re.escape(t) for t in CLOSE_FRESH_OK_REASON_TOKENS)
    + r")(?:[^a-z0-9]|$)",
)


def reason_has_fresh_ok_token(reason: str) -> bool:
    """Return True if `reason` (lowercased) contains an exact-token match for
    one of CLOSE_FRESH_OK_REASON_TOKENS. The custom non-alphanumeric boundary
    treats `_`, `-`, whitespace, and punctuation as token separators so the
    operator-burndown reason format `operator_burndown_DATE_TOKEN_...` is
    parsed token-wise rather than as one giant string."""
    return bool(_CLOSE_FRESH_REASON_TOKEN_RE.search(reason.lower()))


def _queue_row_age_minutes(project: FlyerProject, *, now: datetime) -> float:
    """Minutes since this project entered the manual queue.

    The freshness guard protects fresh QUEUE ROWS, not fresh projects: an
    old project (created_at days ago) that JUST transitioned to
    manual_edit_required has a queued_at of seconds-ago and must still be
    guarded. Resolution order:
      1. `manual_review.queued_at` — the row's queue entry time. Authoritative.
      2. `updated_at` — fallback when queued_at is missing (legacy rows).
      3. `created_at` — last-resort fallback for stores without timestamps.
    """
    manual = project.manual_review
    ts: datetime | None = None
    if manual is not None:
        ts = getattr(manual, "queued_at", None)
    if ts is None:
        ts = project.updated_at or project.created_at
    if ts is None:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max((now - ts).total_seconds() / 60.0, 0.0)


def enforce_close_freshness_guard(
    store: FlyerProjectStore,
    project_id: str,
    *,
    reason: str,
    force: bool,
    now: datetime,
) -> None:
    """Reject `--close` of a fresh row unless explicitly justified.

    Raises `ValueError` (translated to `SystemExit` by the calling script)
    when a young project is being closed without `--force` AND without a
    documented reason token. Applies ONLY to `--close`; `--complete` and
    break-glass paths are customer-visible operations with their own audit
    and do not need this guard.
    """
    if force:
        return
    target = next((p for p in store.projects if p.project_id == project_id), None)
    if target is None:
        # Closure helper will raise the canonical "not found" error.
        return
    age_minutes = _queue_row_age_minutes(target, now=now)
    if age_minutes >= CLOSE_FRESH_MIN_AGE_MINUTES:
        return
    if reason_has_fresh_ok_token(reason):
        return
    accepted = ", ".join(CLOSE_FRESH_OK_REASON_TOKENS)
    raise ValueError(
        f"--close of {project_id} blocked: queue row is only {age_minutes:.1f} min old "
        f"(< {CLOSE_FRESH_MIN_AGE_MINUTES} min). Pass --force, or use --reason "
        f"containing one of: {accepted}."
    )


def make_manual_review(
    *,
    reason_code: FlyerManualReviewReason,
    detail: str = "",
    reason: str = "",
    queued_at: datetime | None = None,
) -> FlyerManualReview:
    """Build a FlyerManualReview for a new queued manual-review event.

    `reason_code` is the structured code; `reason` is optional human-readable text
    (defaults to the code). `detail` is operator/user-facing context.
    """
    now = queued_at or datetime.now(timezone.utc)
    return FlyerManualReview(
        status="queued",
        reason=(reason or reason_code)[:120],
        reason_code=reason_code,
        detail=detail[:500],
        queued_at=now,
    )


def _verification_modes(project: FlyerProject) -> list[str]:
    modes: set[str] = set()
    for asset in project.assets:
        if asset.kind not in {"concept_preview", "final_whatsapp_image", "final_instagram_post", "final_instagram_story", "final_printable_pdf"}:
            continue
        sidecar = Path(f"{asset.path}.text.json")
        try:
            doc = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mode = str(doc.get("verification_mode") or "").strip()
        if mode:
            modes.add(mode)
    return sorted(modes)


def list_manual_queue(
    store: FlyerProjectStore,
    *,
    now: datetime | None = None,
    stale_minutes_threshold: int = 30,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows: list[dict] = []
    for project in store.projects:
        manual = project.manual_review
        # Operator terminal dispositions should not keep accumulating as
        # ghost stuck rows in the queue counters.
        if manual.status in {"break_glass_sent", "closed_no_send"}:
            continue
        has_failed_qa = any(report.status != "passed" for report in project.qa_reports)
        if project.status != "manual_edit_required" and manual.status not in {"queued", "in_progress"} and not has_failed_qa:
            continue
        queued_at = manual.queued_at or project.updated_at
        age_minutes = max(int((now - queued_at).total_seconds() // 60), 0)
        age_hours = age_minutes // 60
        rows.append({
            "project_id": project.project_id,
            "customer_phone": str(project.customer_phone),
            "status": project.status,
            "manual_status": manual.status,
            "manual_reason": manual.reason,
            "manual_reason_code": manual.reason_code,
            "manual_detail": manual.detail,
            "age_minutes": age_minutes,
            "age_hours": max(age_hours, 0),
            "is_stale": age_minutes >= max(stale_minutes_threshold, 1),
            "asset_ids": [asset.asset_id for asset in project.assets],
            "verification_modes": _verification_modes(project),
            "locked_facts": [fact.model_dump(mode="json") for fact in project.locked_facts],
            "qa_blockers": [blocker for report in project.qa_reports for blocker in report.blockers],
        })
    return rows


def triage_summary(
    store: FlyerProjectStore,
    *,
    now: datetime | None = None,
    stale_minutes_threshold: int = 30,
) -> dict:
    """Triage-oriented view: groups by customer_phone, sorts by oldest age, with a reason histogram."""
    rows = list_manual_queue(store, now=now, stale_minutes_threshold=stale_minutes_threshold)
    groups: dict[str, list[dict]] = defaultdict(list)
    reason_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        groups[row["customer_phone"]].append(row)
        reason_counts[row["manual_reason_code"]] += 1
    ordered_groups: list[dict] = []
    for phone, items in groups.items():
        items.sort(key=lambda r: r["age_hours"], reverse=True)
        ordered_groups.append({
            "customer_phone": phone,
            "count": len(items),
            "stale_count": sum(1 for r in items if bool(r.get("is_stale"))),
            "oldest_age_hours": items[0]["age_hours"] if items else 0,
            "oldest_age_minutes": items[0]["age_minutes"] if items else 0,
            "projects": items,
        })
    ordered_groups.sort(key=lambda g: g["oldest_age_hours"], reverse=True)
    return {
        "total": len(rows),
        "stale_total": sum(1 for r in rows if bool(r.get("is_stale"))),
        "stale_minutes_threshold": stale_minutes_threshold,
        "reason_counts": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "groups": ordered_groups,
    }


def classify_legacy_reason(project: FlyerProject) -> tuple[FlyerManualReviewReason, str]:
    """Heuristic classifier for legacy manual-review projects without a reason_code.

    Used by the backfill CLI. Returns (reason_code, detail).
    """
    has_failed_qa = any(report.status != "passed" for report in project.qa_reports)
    if has_failed_qa:
        blockers = [b for report in project.qa_reports for b in report.blockers]
        detail = "; ".join(blockers)[:500] if blockers else "legacy QA-failed project (no blockers recorded)"
        return "visual_qa_failed", detail
    for extraction in project.reference_extractions:
        if extraction.status in {"low_confidence", "provider_unavailable", "unsupported", "not_run"}:
            code: FlyerManualReviewReason = f"reference_{extraction.status}"  # type: ignore[assignment]
            return code, (extraction.detail or f"legacy reference extraction status={extraction.status}")[:500]
    raw = (project.raw_request or "").lower()
    if "edit uploaded flyer/source artwork" in raw or "authorized flyer/source artwork update" in raw:
        return "source_edit_provider_unavailable", "legacy source-edit project queued before reason was tracked"
    return "legacy_unknown", (project.raw_request or "")[:500]


def backfill_manual_reasons(
    store: FlyerProjectStore,
    *,
    apply: bool,
    now: datetime | None = None,
) -> dict:
    """Backfill manual_review.reason_code for legacy projects.

    Eligible: status=='manual_edit_required' (or QA-failed) AND reason_code=='unclassified'.
    Idempotent — re-applying finds zero candidates.
    """
    now = now or datetime.now(timezone.utc)
    candidates: list[dict] = []
    for idx, project in enumerate(store.projects):
        if project.manual_review.reason_code != "unclassified":
            continue
        has_failed_qa = any(report.status != "passed" for report in project.qa_reports)
        if project.status != "manual_edit_required" and not has_failed_qa:
            continue
        reason_code, detail = classify_legacy_reason(project)
        queued_at = project.manual_review.queued_at or project.updated_at or now
        candidate = {
            "project_id": project.project_id,
            "customer_phone": str(project.customer_phone),
            "current_status": project.status,
            "current_reason_code": project.manual_review.reason_code,
            "proposed_reason_code": reason_code,
            "proposed_reason": reason_code,
            "proposed_detail": detail,
            "proposed_queued_at": queued_at.isoformat(),
        }
        candidates.append(candidate)
        if apply:
            current_manual_status = project.manual_review.status
            new_manual = project.manual_review.model_copy(update={
                "status": current_manual_status if current_manual_status in {"queued", "in_progress"} else "queued",
                "reason": reason_code,
                "reason_code": reason_code,
                "detail": detail,
                "queued_at": queued_at,
            })
            store.projects[idx] = project.model_copy(update={
                "manual_review": new_manual,
                "updated_at": now,
            })
    return {
        "applied": apply,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _next_asset_id(project) -> str:
    max_id = 0
    for asset in project.assets:
        try:
            max_id = max(max_id, int(asset.asset_id[1:]))
        except Exception:
            continue
    return f"A{max_id + 1:04d}"


def complete_manual_project(
    store: FlyerProjectStore,
    project_id: str,
    approved_asset_path: Path | str,
    *,
    reason: str,
) -> FlyerProjectStore:
    source = Path(approved_asset_path)
    now = datetime.now(timezone.utc)
    for idx, project in enumerate(store.projects):
        if project.project_id != project_id:
            continue
        manual = project.manual_review
        if project.status != "manual_edit_required" or manual.status not in {"queued", "in_progress"}:
            raise ValueError(f"project not queued for manual completion: {project_id}")
        if not is_flyer_transition_allowed(project.status, "awaiting_final_approval"):
            raise ValueError(f"invalid transition {project.status}->awaiting_final_approval")
        if not source.exists() or not source.is_file():
            raise ValueError(f"approved asset not found: {source}")
        asset_id = _next_asset_id(project)
        root = Path(os.environ.get("FLYER_STATE_ROOT", "/opt/shift-agent/state/flyer")).resolve()
        dest_dir = root / "manual" / project_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{project_id}-{asset_id}{source.suffix or '.png'}"
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
        data = dest.read_bytes()
        asset = FlyerAsset(
            asset_id=asset_id,
            kind="concept_preview",
            source="uploaded",
            path=str(dest),
            mime_type=mimetypes.guess_type(str(dest))[0] or "image/png",
            sha256=hashlib.sha256(data).hexdigest(),
            original_message_id=project.original_message_id,
            received_at=now,
        )
        concept = FlyerConcept(
            concept_id="C1",
            title="Designer Approved",
            style_summary="Operator-approved manual review asset",
            preview_asset_id=asset.asset_id,
            prompt=project.raw_request,
            created_at=now,
        )
        manual = project.manual_review.model_copy(update={
            "status": "completed",
            "detail": reason,
            "completed_at": now,
            "operator_asset_ids": [asset.asset_id],
        })
        project_after = project.model_copy(update={
            "status": "awaiting_final_approval",
            "assets": [*project.assets, asset],
            "concepts": [concept],
            "selected_concept_id": "C1",
            "manual_review": manual,
            "updated_at": now,
        })
        # The downstream send path (`send_flyer_concept_previews`) gates
        # delivery on `validate_text_manifest_file` + `validate_visual_qa_report`.
        # Without sidecars, an operator-completed row reaches
        # awaiting_final_approval but the preview send will fail with
        # `text_qa_failed: text manifest missing` — the customer never sees
        # the approved asset. Reviewer caught this on PR #131; write both
        # sidecars at completion so the send path clears.
        #
        # Verification mode is `source_edit_integrity_only` (the validator
        # already accepts that as an integrity-only manifest where the
        # customer's APPROVE is the final visual/text gate). Visual QA is
        # attributed to `operator_review` — distinct from `sidecar_test`
        # (which is dev-only) — because the operator's fresh-OTP + reason
        # IS the QA assertion.
        _write_operator_qa_sidecars(project_after, dest, asset)
        store.projects[idx] = project_after
        return FlyerProjectStore.model_validate(store.model_dump())
    raise ValueError(f"project not found: {project_id}")


def _write_operator_qa_sidecars(
    project: "FlyerProject",
    asset_path: Path,
    asset: "FlyerAsset",
) -> None:
    """Write integrity-only text manifest + operator-attributed visual QA
    report for a manually-completed asset, so the downstream preview send
    clears the QA validators. Best-effort: a sidecar-write failure is
    surfaced as a ValueError so the caller (cockpit complete endpoint)
    rolls back the operator-completion rather than leaving the row in a
    send-failing state."""
    try:
        try:
            from flyer_render import write_text_manifest  # type: ignore
        except ImportError:
            from agents.flyer.render import write_text_manifest  # type: ignore
        try:
            from flyer_visual_qa import write_visual_qa_report  # type: ignore
        except ImportError:
            from agents.flyer.visual_qa import write_visual_qa_report  # type: ignore
        from schemas import FlyerVisualQAReport  # type: ignore
    except Exception as e:
        raise ValueError(f"operator QA sidecar imports failed: {type(e).__name__}: {e}")
    try:
        write_text_manifest(
            project,
            asset_path,
            output_format="concept_preview",
            selected_concept_id=project.selected_concept_id or "C1",
            source_path=asset_path,
            verification_mode="source_edit_integrity_only",
        )
    except Exception as e:
        raise ValueError(f"operator text manifest write failed: {type(e).__name__}: {e}")
    report = FlyerVisualQAReport(
        project_id=project.project_id,
        asset_id=asset.asset_id,
        artifact_path=str(asset_path),
        artifact_sha256=asset.sha256,
        project_version=project.version,
        output_format="concept_preview",
        provider="operator-cockpit",
        qa_source="operator_review",
        status="passed",
        blockers=[],
        warnings=[],
        extracted_text="",
        checked_at=datetime.now(timezone.utc),
    )
    try:
        write_visual_qa_report(report, asset_path)
    except Exception as e:
        raise ValueError(f"operator visual QA write failed: {type(e).__name__}: {e}")


def close_manual_project(
    store: FlyerProjectStore,
    project_id: str,
    *,
    reason: str,
) -> FlyerProjectStore:
    """Close a queued manual-review project without sending customer assets."""
    now = datetime.now(timezone.utc)
    for idx, project in enumerate(store.projects):
        if project.project_id != project_id:
            continue
        manual = project.manual_review
        if project.status != "manual_edit_required" or manual.status not in {"queued", "in_progress"}:
            raise ValueError(f"project not queued for manual close: {project_id}")
        if not is_flyer_transition_allowed(project.status, "closed_no_send"):
            raise ValueError(f"invalid transition {project.status}->closed_no_send")
        new_manual = project.manual_review.model_copy(update={
            "status": "closed_no_send",
            "detail": reason[:500],
            "completed_at": now,
        })
        store.projects[idx] = project.model_copy(update={
            "status": "closed_no_send",
            "manual_review": new_manual,
            "updated_at": now,
        })
        return FlyerProjectStore.model_validate(store.model_dump())
    raise ValueError(f"project not found: {project_id}")


# ─────────────────────────────────────────────────────────────────
# Proactive closure customer-notification helpers (PR follow-up to PR #129)
#
# At operator-close time we push a customer-visible WhatsApp message so the
# customer learns about the closure immediately, rather than only on their
# next "any update?" inbound. The reactive PR #129 path remains the safety
# net for sends that fail or for customers without a known chat_id.
# ─────────────────────────────────────────────────────────────────


def build_closure_customer_text(project: FlyerProject) -> str:
    """Customer-visible text for a closed_no_send project.

    Delegates to `build_project_status_reply` so the proactive close-time
    push and the reactive "any update?" reply CANNOT drift. Single source
    of truth lives in `agents.flyer.workflow.CLOSED_NO_SEND_REASON_LINES`.

    Import order matters: the deployed VPS layout has Flyer modules at
    `/opt/shift-agent/flyer_*.py` (flat, no `agents.flyer` package), so the
    flat alias MUST be tried first. The packaged path is the dev/test
    fallback. Without this dual-path pattern the function raises
    ModuleNotFoundError in production, which `notify_customer_of_closure`
    would swallow as an audited send failure — silent failure mode that
    PR #130's first reviewer caught.
    """
    try:
        from flyer_workflow import build_project_status_reply  # type: ignore
    except ImportError:
        from agents.flyer.workflow import build_project_status_reply
    return build_project_status_reply(project)


def notify_customer_of_closure(
    store: FlyerProjectStore,
    project_id: str,
    *,
    customers_path: Path,
    decisions_log_path: Path,
    bridge_send=None,
    audit_append=None,
    now_fn=None,
) -> dict:
    """Best-effort proactive WhatsApp send for a freshly-closed_no_send row.

    The closure state write is the PRIMARY operation — the caller must
    invoke this AFTER `close_manual_project` succeeds and the store is
    persisted. This helper NEVER raises into the caller: operators must
    not see a notification failure surface as a non-zero exit code,
    because the closure itself succeeded. PR #129's reactive "any update?"
    path is the safety net for any failure here.

    Audit invariant: a `flyer_closure_customer_notified` row is appended
    for EVERY closure attempt (success, missing chat_id, bridge failure)
    so operators can grep the audit log for traceability. The only case
    where no audit is written is a catastrophic post-close store
    inconsistency (project missing) — impossible in practice because the
    caller just wrote the store; if it ever happens stderr surfaces it.

    Dependencies are injected so the helper is fully testable without
    touching the WhatsApp bridge or the live decisions log. Defaults wire
    up `safe_io.bridge_post` + `safe_io.ndjson_append` lazily so this
    module stays importable on Windows where `fcntl` is absent.

    Returns the audit entry dict that was (or would have been) appended,
    so the caller can log to stderr or aggregate without re-reading the
    log file.
    """
    if bridge_send is None or audit_append is None:
        # Lazy import: keeps manual_queue.py importable on platforms
        # where safe_io cannot load (e.g., Windows test runs).
        from safe_io import bridge_post as _default_bridge  # type: ignore
        from safe_io import ndjson_append as _default_append  # type: ignore
        if bridge_send is None:
            bridge_send = _default_bridge
        if audit_append is None:
            audit_append = _default_append
    if now_fn is None:
        now_fn = lambda: datetime.now(timezone.utc)
    project = next((p for p in store.projects if p.project_id == project_id), None)
    if project is None:
        return {
            "type": "flyer_closure_customer_notified",
            "project_id": project_id,
            "skipped": True,
            "error": "project_not_found_post_close",
        }
    customer_phone = str(project.customer_phone)
    manual = project.manual_review
    reason_code = str(getattr(manual, "reason_code", "") or "unclassified")
    chat_id = ""
    chat_id_source = "none"
    send_ok = False
    outbound_mid = ""
    error = ""
    try:
        chat_id, chat_id_source = resolve_proactive_chat_id_for_project(
            project,
            customers_path=customers_path,
            decisions_log_path=decisions_log_path,
        )
        if not chat_id:
            error = "no_chat_id_for_customer"
        else:
            text = build_closure_customer_text(project)
            ok, mid, err, status = bridge_send(chat_id, text)
            send_ok = bool(ok)
            outbound_mid = mid or ""
            if not ok:
                error = f"{status}: {err}"[:500]
    except Exception as e:
        # Best-effort: unexpected errors become audited send failures
        # rather than caller-level exceptions. Closure stays committed.
        error = f"unexpected: {type(e).__name__}: {e}"[:500]
    entry = {
        "ts": now_fn().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "type": "flyer_closure_customer_notified",
        "project_id": project_id,
        "customer_phone": customer_phone,
        "reason_code": reason_code,
        "chat_id": chat_id,
        "chat_id_source": chat_id_source,
        "send_ok": send_ok,
        "outbound_message_id": outbound_mid,
        "error": error,
    }
    try:
        audit_append(decisions_log_path, json.dumps(entry, separators=(",", ":")))
    except Exception as e:
        entry["audit_append_failed"] = f"{type(e).__name__}: {e}"
    return entry


def find_recent_inbound_chat_id_for_project(
    decisions_log_path: Path, project_id: str,
    *, max_lines: int = 50_000,
) -> str:
    """Most recent inbound `chat_id` the agent associated with this project.

    Scans the agent decisions log (newest first) for `cf_router_intercepted`
    or `raw_inbound` rows whose serialized form mentions this project_id;
    returns the row's top-level `chat_id` or an empty string when no
    evidence exists.

    Why this exists (PR #133 review finding HIGH-1):
    For LID-only or authorized-requester projects, the customer's
    `primary_chat_id` (set during onboarding via `customer_phone → JID`)
    can be a different WhatsApp thread than the one the inbound actually
    arrived on. The bridge accepts the phone JID and returns ok=True even
    when the customer's visible chat never sees the message — silent
    misroute. The 2026-05-19 F0060 incident is the canonical example:
    project phone `+19045550104`, customer primary chat
    `17329837841@s.whatsapp.net`, but the live inbound thread was
    `201975216009469@lid`; proactive notification went to the phone JID
    and the customer's chat received nothing.

    Audit-derived chat_id is the canonical "where did the inbound for this
    project arrive" answer; falling back to `primary_chat_id` is only safe
    when no audit evidence exists (legacy/seeded rows).
    """
    if not decisions_log_path or not Path(decisions_log_path).exists():
        return ""
    try:
        lines = Path(decisions_log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    needle = f"project_id={project_id}"
    # Walk from newest to oldest; first qualifying match wins.
    for line in reversed(lines[-max_lines:]):
        if needle not in line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        if doc.get("type") not in {"cf_router_intercepted", "raw_inbound"}:
            continue
        chat_id = str(doc.get("chat_id") or "")
        if chat_id:
            return chat_id
    return ""


def resolve_proactive_chat_id_for_project(
    project: FlyerProject,
    *,
    customers_path: Path,
    decisions_log_path: Path,
) -> tuple[str, str]:
    """Safest WhatsApp chat_id for a proactive customer push.

    Resolution order (PR #133 HIGH-1 fix):
      1. `find_recent_inbound_chat_id_for_project` — audit evidence from
         the agent decisions log. Canonical answer when present.
      2. `resolve_customer_chat_id_by_phone` — customer record's
         `primary_chat_id` looked up via `customer_phone`. Only safe when
         the customer's onboarded thread is the same thread the project's
         inbound arrived on (NOT the LID/authorized-requester case).
      3. Empty string — operator must rely on the reactive "any update?"
         safety net.

    Returns `(chat_id, source)` so callers (cockpit preview, audit row)
    can show the operator which path matched.
    """
    audit_chat_id = find_recent_inbound_chat_id_for_project(
        decisions_log_path, project.project_id,
    )
    if audit_chat_id:
        return audit_chat_id, "audit_log"
    fallback = resolve_customer_chat_id_by_phone(customers_path, str(project.customer_phone)) or ""
    if fallback:
        return fallback, "primary_chat_id"
    return "", "none"


def resolve_customer_chat_id_by_phone(
    customers_path: Path, customer_phone: str,
) -> str | None:
    """Look up the customer record by phone, return `primary_chat_id` or None.

    `primary_chat_id` is the customer's last-known WhatsApp chat identifier
    (set during onboarding and refreshed on inbound). For proactive close-time
    sends the script has only `customer_phone` from the project record, so we
    consult the customers store to find a workable chat_id.

    Returns None when:
      - the store file is missing
      - no customer matches the phone (across primary/whatsapp/onboarding
        numbers and authorized_request_numbers)
      - the matching customer has no `primary_chat_id`

    Multiple-match safety: returns None if more than one customer claims the
    same phone — operator must disambiguate (matches the existing
    `find_flyer_customer_by_sender` behavior).
    """
    if not customer_phone or not customers_path.exists():
        return None
    try:
        store = json.loads(customers_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    target = str(customer_phone).strip()
    if not target:
        return None
    matches = []
    for customer in store.get("customers", []) or []:
        if not isinstance(customer, dict):
            continue
        numbers = set(customer.get("authorized_request_numbers") or [])
        for key in ("business_whatsapp_number", "onboarded_by_phone", "public_phone"):
            value = customer.get(key)
            if value:
                numbers.add(str(value))
        if target in numbers:
            matches.append(customer)
    if len(matches) != 1:
        return None
    chat_id = str(matches[0].get("primary_chat_id") or "").strip()
    return chat_id or None
