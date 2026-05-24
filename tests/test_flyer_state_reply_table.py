"""S7 P0-6: state→reply table coverage and routing safety.

Locks down the invariant that every FlyerWorkflowStatus and every
FlyerManualReviewReason has a deterministic customer-facing copy line,
and that cf-router routing selects the right helper per reason_code.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args
from pathlib import Path

import pytest

from agents.flyer.workflow import (
    CLOSED_NO_SEND_REASON_LINES,
    MANUAL_REVIEW_REASON_LINES,
    STATUS_LINES,
    build_project_status_reply,
)
from schemas import (
    FlyerLockedFact,
    FlyerManualReview,
    FlyerManualReviewReason,
    FlyerProject,
    FlyerWorkflowStatus,
)


def _now() -> datetime:
    return datetime(2026, 5, 19, tzinfo=timezone.utc)


def _project(
    *,
    status: str = "intake_started",
    manual_status: str = "none",
    reason_code: str = "unclassified",
    business_name: str = "Lakshmis Kitchen",
    contact: str = "+17329837841",
) -> FlyerProject:
    now = _now()
    return FlyerProject(
        project_id="F9001",
        status=status,
        customer_phone=contact,
        created_at=now,
        updated_at=now,
        original_message_id="m-1",
        raw_request="Create a flyer.",
        fields={
            "event_or_business_name": business_name,
            "contact_info": contact,
            "notes": "test",
        },
        manual_review=FlyerManualReview(
            status=manual_status,
            reason=reason_code,
            reason_code=reason_code,
            queued_at=now if manual_status in {"queued", "in_progress"} else None,
        ),
    )


# ---------- structural coverage: no missing keys ----------

def test_status_lines_covers_every_flyer_workflow_status():
    """Every FlyerWorkflowStatus value MUST have an entry in STATUS_LINES.
    A missing key would fall through to the generic "I have this flyer project
    open." default — silently losing the per-state customer signal."""
    all_statuses = set(get_args(FlyerWorkflowStatus))
    table_keys = set(STATUS_LINES.keys())
    missing = all_statuses - table_keys
    extra = table_keys - all_statuses
    assert not missing, f"STATUS_LINES missing entries for: {sorted(missing)}"
    assert not extra, f"STATUS_LINES has entries for non-existent statuses: {sorted(extra)}"


def test_manual_review_reason_lines_covers_every_flyer_manual_review_reason():
    """Every FlyerManualReviewReason value MUST have an entry in
    MANUAL_REVIEW_REASON_LINES so customers don't get a generic catch-all when
    their project lands at manual_edit_required."""
    all_reasons = set(get_args(FlyerManualReviewReason))
    table_keys = set(MANUAL_REVIEW_REASON_LINES.keys())
    missing = all_reasons - table_keys
    extra = table_keys - all_reasons
    assert not missing, f"MANUAL_REVIEW_REASON_LINES missing entries for: {sorted(missing)}"
    assert not extra, f"MANUAL_REVIEW_REASON_LINES has entries for non-existent reasons: {sorted(extra)}"


def test_closed_no_send_reason_lines_covers_every_flyer_manual_review_reason():
    """Mirror of the manual-review-reason coverage gate, for closed_no_send
    projects. After operator close, the customer's status reply must carry
    reason-specific copy with a concrete next step — no silent fall-through
    to the generic 'closed by the operator' line."""
    all_reasons = set(get_args(FlyerManualReviewReason))
    table_keys = set(CLOSED_NO_SEND_REASON_LINES.keys())
    missing = all_reasons - table_keys
    extra = table_keys - all_reasons
    assert not missing, f"CLOSED_NO_SEND_REASON_LINES missing entries for: {sorted(missing)}"
    assert not extra, f"CLOSED_NO_SEND_REASON_LINES has entries for non-existent reasons: {sorted(extra)}"


# ---------- per-status determinism ----------

@pytest.mark.parametrize("status", sorted(get_args(FlyerWorkflowStatus)))
def test_every_status_produces_deterministic_reply(status: str):
    """For each workflow status, the reply is deterministic outcome-only
    STATUS_LINES copy. No state falls through to a generic line."""
    project = _project(status=status, manual_status="none")
    reply = build_project_status_reply(project)
    assert "Flyer Studio" in reply
    assert "F9001" not in reply
    assert "project F" not in reply.lower()
    # The status-specific line must be present in the reply body.
    assert STATUS_LINES[status] in reply, (
        f"status {status!r}: expected STATUS_LINES[{status}] in reply, got {reply!r}"
    )


# ---------- per-reason-code determinism ----------

@pytest.mark.parametrize("reason_code", sorted(get_args(FlyerManualReviewReason)))
def test_every_manual_review_reason_produces_specific_reply(reason_code: str):
    """A manual_edit_required project with each reason_code returns the
    reason-specific copy from MANUAL_REVIEW_REASON_LINES — NOT the generic
    STATUS_LINES['manual_edit_required'] line."""
    project = _project(
        status="manual_edit_required",
        manual_status="queued",
        reason_code=reason_code,
    )
    reply = build_project_status_reply(project)
    expected_line = MANUAL_REVIEW_REASON_LINES[reason_code]
    assert expected_line in reply, (
        f"reason_code {reason_code!r}: expected MANUAL_REVIEW_REASON_LINES"
        f"[{reason_code}] in reply, got {reply!r}"
    )


@pytest.mark.parametrize("reason_code", sorted(get_args(FlyerManualReviewReason)))
def test_every_closed_no_send_reason_produces_specific_reply(reason_code: str):
    """A closed_no_send project with each reason_code returns the
    reason-specific copy from CLOSED_NO_SEND_REASON_LINES — NOT the generic
    STATUS_LINES['closed_no_send'] line. Otherwise the customer gets a
    closure notice with no guidance on what to do next."""
    project = _project(
        status="closed_no_send",
        manual_status="closed_no_send",
        reason_code=reason_code,
    )
    reply = build_project_status_reply(project)
    expected_line = CLOSED_NO_SEND_REASON_LINES[reason_code]
    assert expected_line in reply, (
        f"reason_code {reason_code!r}: expected CLOSED_NO_SEND_REASON_LINES"
        f"[{reason_code}] in reply, got {reply!r}"
    )


def test_closed_no_send_with_none_manual_status_falls_back_to_generic_line():
    """Legacy/pre-S1 projects at closed_no_send have manual_review.status
    that isn't 'closed_no_send' (e.g., 'none'). The reason-code branch
    must NOT fire — fall back to STATUS_LINES['closed_no_send']."""
    project = _project(
        status="closed_no_send",
        manual_status="none",
        reason_code="source_edit_provider_unavailable",
    )
    reply = build_project_status_reply(project)
    assert STATUS_LINES["closed_no_send"] in reply
    assert CLOSED_NO_SEND_REASON_LINES["source_edit_provider_unavailable"] not in reply


# ---------- proactive close-time push copy parity ----------


@pytest.mark.parametrize("reason_code", sorted(get_args(FlyerManualReviewReason)))
def test_build_closure_customer_text_matches_reactive_reply(reason_code: str):
    """SINGLE-SOURCE-OF-TRUTH INVARIANT: the proactive close-time WhatsApp
    push and the reactive 'any update?' reply MUST produce identical text.
    Drift would surface as customers seeing two different stories about the
    same closure — defeats the explicit PR design choice to share one copy."""
    from agents.flyer.manual_queue import build_closure_customer_text
    project = _project(
        status="closed_no_send",
        manual_status="closed_no_send",
        reason_code=reason_code,
    )
    proactive = build_closure_customer_text(project)
    reactive = build_project_status_reply(project)
    assert proactive == reactive, (
        f"reason_code {reason_code!r}: proactive vs reactive copy drift\n"
        f"proactive: {proactive!r}\nreactive: {reactive!r}"
    )
    assert CLOSED_NO_SEND_REASON_LINES[reason_code] in proactive


# ---------- manual_review status-aware routing ----------

def test_manual_edit_required_with_none_manual_status_falls_back_to_generic_line():
    """Legacy/pre-S1 projects at manual_edit_required have
    manual_review.status='none' (not queued). The reason-code branch must
    NOT fire — fall back to the generic STATUS_LINES['manual_edit_required']
    so we don't make up a reason the operator didn't record."""
    project = _project(
        status="manual_edit_required",
        manual_status="none",
        reason_code="unclassified",
    )
    reply = build_project_status_reply(project)
    assert STATUS_LINES["manual_edit_required"] in reply


def test_manual_edit_required_with_break_glass_sent_does_not_use_queued_branch():
    """A break_glass_sent project is operator-resolved out-of-band (S2 fix).
    Its customer-facing reply must reflect that resolution — the
    reason-code-specific 'queued for designer' line would be a lie."""
    project = _project(
        status="manual_edit_required",
        manual_status="break_glass_sent",
        reason_code="source_edit_provider_unavailable",
    )
    reply = build_project_status_reply(project)
    # The generic manual_edit_required line is the fallback — acceptable here
    # because break_glass_sent semantics aren't a "still queued" state.
    assert MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"] not in reply
    assert STATUS_LINES["manual_edit_required"] in reply


def test_manual_edit_required_with_completed_review_uses_generic_status_line():
    """Same as break_glass_sent: a completed manual_review is no longer a
    customer-blocking signal. Use the status line, not the queued copy."""
    project = _project(
        status="manual_edit_required",
        manual_status="completed",
        reason_code="visual_qa_failed",
    )
    reply = build_project_status_reply(project)
    assert MANUAL_REVIEW_REASON_LINES["visual_qa_failed"] not in reply
    assert STATUS_LINES["manual_edit_required"] in reply


# ---------- reason-code routing into cf-router helpers ----------

def test_cf_router_status_reply_dispatch_uses_source_edit_helper_only_for_source_edit_reason():
    """cf-router routing must use `flyer_manual_edit_status_reply` (the
    source-preserving-edit-specific copy) ONLY when reason_code is
    source_edit_provider_unavailable. All other reason codes route through
    `flyer_project_status_reply` so they pick up the reason-specific
    customer copy from MANUAL_REVIEW_REASON_LINES rather than the
    "source-preserving edit queue" text."""
    import importlib.machinery
    import importlib.util
    import sys

    repo = Path(__file__).resolve().parent.parent
    plugin_dir = repo / "src" / "plugins" / "cf-router"

    pkg_name = "cf_router_state_reply_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(plugin_dir)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    actions_full = f"{pkg_name}.actions"
    actions_loader = importlib.machinery.SourceFileLoader(actions_full, str(plugin_dir / "actions.py"))
    actions_spec = importlib.util.spec_from_loader(actions_full, actions_loader)
    actions_mod = importlib.util.module_from_spec(actions_spec)
    sys.modules[actions_full] = actions_mod
    actions_loader.exec_module(actions_mod)
    setattr(pkg_mod, "actions", actions_mod)

    hooks_full = f"{pkg_name}.hooks"
    hooks_loader = importlib.machinery.SourceFileLoader(hooks_full, str(plugin_dir / "hooks.py"))
    hooks_spec = importlib.util.spec_from_loader(hooks_full, hooks_loader)
    hooks_mod = importlib.util.module_from_spec(hooks_spec)
    sys.modules[hooks_full] = hooks_mod
    hooks_loader.exec_module(hooks_mod)

    # Static-text inspection of hooks.py — verify the routing pattern.
    hooks_text = Path(hooks_loader.path).read_text(encoding="utf-8")
    assert 'manual_reason_code == "source_edit_provider_unavailable"' in hooks_text, (
        "cf-router routing must branch on reason_code, not just status"
    )
    # Verify both status-check sites have the routing pattern. The string can
    # appear additional times for audit-reason classification (S7 fix to
    # avoid mis-tagging non-source-edit status checks as source-edit traffic).
    source_edit_route_count = hooks_text.count('manual_reason_code == "source_edit_provider_unavailable"')
    assert source_edit_route_count >= 2, (
        f"expected at least 2 reason_code routing sites in hooks.py "
        f"(one per status-check branch), got {source_edit_route_count}"
    )


def test_source_edit_status_helper_uses_canonical_reason_line():
    """The source-edit-specific cf-router helper must not drift from
    MANUAL_REVIEW_REASON_LINES. Operators compare queue/status copy across
    cockpit and WhatsApp; two hand-written paths create confusing divergence.
    """
    import importlib.machinery
    import importlib.util
    import sys

    repo = Path(__file__).resolve().parent.parent
    name = "cf_router_actions_for_source_edit_status_reply_test"
    sys.modules.pop(name, None)
    loader = importlib.machinery.SourceFileLoader(
        name,
        str(repo / "src" / "plugins" / "cf-router" / "actions.py"),
    )
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)

    project = _project(
        status="manual_edit_required",
        manual_status="queued",
        reason_code="source_edit_provider_unavailable",
        business_name="Lakshmis Kitchen",
    )
    reply = mod.flyer_manual_edit_status_reply(project.model_dump(mode="json"))

    assert MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"] in reply
    assert "source-preserving edit queue" not in reply


# ---------- status-request semantic coverage ----------

def test_status_request_classifier_recognizes_common_check_in_phrases():
    """Pin the broad set of "is it ready?" intents that must route to the
    status table, not to revision parsing or generic LLM fallback."""
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    import sys

    REPO = Path(__file__).resolve().parent.parent
    name = "cf_router_actions_for_status_request_test"
    sys.modules.pop(name, None)
    loader = SourceFileLoader(name, str(REPO / "src" / "plugins" / "cf-router" / "actions.py"))
    spec = spec_from_loader(name, loader)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)

    for text in [
        "status",
        "any update",
        "any updates?",
        "where are the updates?",
        "any update?",
        "is it ready",
        "is it ready yet?",
        "is the flyer ready?",
        "ready yet?",
        "is my flyer done",
        "what is the status",
        "what's the status?",
        "eta?",
        "ready?",
    ]:
        assert mod.is_flyer_project_status_request(text), (
            f"expected {text!r} to classify as a status check"
        )

    # And conversely, real revision/correction text must NOT route to status.
    for edit_text in [
        "change the date to next Saturday",
        "update the phone to +1 732 983 7841",
        "replace Idly with Dosa",
        "remove extra 08:00",
    ]:
        assert not mod.is_flyer_project_status_request(edit_text), (
            f"expected {edit_text!r} to NOT classify as a status check"
        )
