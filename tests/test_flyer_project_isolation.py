"""S3 P0-1: project context isolation + stale-state guard regressions.

Locks down the invariant that distinct new flyer requests cannot be swallowed
by stale active/manual projects, and that corrections/status checks route to
the correct latest project.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"


def _load_actions():
    """Standalone actions.py loader (mirrors test_cf_router_flyer_routing.py)."""
    module_name = "cf_router_actions_isolation_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(PLUGIN_DIR / "actions.py"))
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    loader.exec_module(mod)
    return mod


def _load_plugin_modules():
    """Load actions + hooks as a package so hooks can import .actions."""
    pkg_name = "cf_router_flyer_isolation_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]

    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg_spec.submodule_search_locations = [str(PLUGIN_DIR)]
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg_mod

    actions_full = f"{pkg_name}.actions"
    actions_loader = importlib.machinery.SourceFileLoader(actions_full, str(PLUGIN_DIR / "actions.py"))
    actions_spec = importlib.util.spec_from_loader(actions_full, actions_loader)
    actions_mod = importlib.util.module_from_spec(actions_spec)
    sys.modules[actions_full] = actions_mod
    actions_loader.exec_module(actions_mod)
    setattr(pkg_mod, "actions", actions_mod)

    hooks_full = f"{pkg_name}.hooks"
    hooks_loader = importlib.machinery.SourceFileLoader(hooks_full, str(PLUGIN_DIR / "hooks.py"))
    hooks_spec = importlib.util.spec_from_loader(hooks_full, hooks_loader)
    hooks_mod = importlib.util.module_from_spec(hooks_spec)
    sys.modules[hooks_full] = hooks_mod
    hooks_loader.exec_module(hooks_mod)
    setattr(pkg_mod, "hooks", hooks_mod)

    return hooks_mod, actions_mod


# ---------- pure helper tests ----------

def test_is_stale_helper_returns_false_when_recently_updated():
    actions = _load_actions()
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    project = {
        "project_id": "F1001",
        "status": "manual_edit_required",
        "updated_at": (now - timedelta(hours=1)).isoformat(),
    }
    assert actions.is_stale_for_new_request(project, now=now) is False


def test_is_stale_helper_returns_true_past_status_threshold():
    actions = _load_actions()
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    # awaiting_final_approval threshold is 6h
    project = {
        "project_id": "F1002",
        "status": "awaiting_final_approval",
        "updated_at": (now - timedelta(hours=7)).isoformat(),
    }
    assert actions.is_stale_for_new_request(project, now=now) is True


def test_is_stale_helper_returns_true_for_long_manual_review():
    actions = _load_actions()
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    # manual_edit_required threshold is 24h
    project = {
        "project_id": "F1003",
        "status": "manual_edit_required",
        "updated_at": (now - timedelta(hours=25)).isoformat(),
    }
    assert actions.is_stale_for_new_request(project, now=now) is True
    # but 23h is still fresh enough
    project["updated_at"] = (now - timedelta(hours=23)).isoformat()
    assert actions.is_stale_for_new_request(project, now=now) is False


def test_is_stale_helper_handles_terminal_status_as_not_stale():
    """`completed` and unknown statuses have no threshold; never stale."""
    actions = _load_actions()
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    project = {
        "project_id": "F1004",
        "status": "completed",
        "updated_at": (now - timedelta(days=30)).isoformat(),
    }
    assert actions.is_stale_for_new_request(project, now=now) is False


def test_is_stale_helper_overrides():
    """Per-call overrides let callers tighten/loosen thresholds without a config redeploy."""
    actions = _load_actions()
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    project = {
        "project_id": "F1005",
        "status": "manual_edit_required",
        "updated_at": (now - timedelta(hours=2)).isoformat(),
    }
    assert actions.is_stale_for_new_request(project, now=now) is False  # default 24h
    assert actions.is_stale_for_new_request(project, now=now, overrides={"manual_edit_required": 1.0}) is True


# ---------- the 6 user-spec scenarios ----------

def _stale_project(*, project_id: str, status: str, hours_old: float, raw_request: str = "old request", **extra) -> dict:
    now = datetime.now(timezone.utc)
    updated = (now - timedelta(hours=hours_old)).isoformat()
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": "+17329837841",
        "raw_request": raw_request,
        "fields": {"event_or_business_name": "Old Project", "contact_info": "+17329837841"},
        "concepts": [],
        "revisions": [],
        "updated_at": updated,
        "created_at": updated,
    }


def _patch_basic_lookups(hooks, actions, monkeypatch, active_project: dict | None):
    monkeypatch.setattr(actions, "lid_to_phone_via_identify_sender", lambda _chat_id: ("+17329837841", "customer"))
    monkeypatch.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: {"customer_id": "CUST0001", "status": "trial"})
    monkeypatch.setattr(actions, "find_active_flyer_project_by_sender", lambda _phone, _chat_id: active_project)
    monkeypatch.setattr(actions, "find_paid_flyer_guest_order", lambda _phone, _chat_id: None)
    monkeypatch.setattr(actions, "find_reserved_flyer_guest_order", lambda _phone, _chat_id, _project_id: None)
    monkeypatch.setattr(actions, "flyer_project_has_required_fields", lambda _project: True)
    monkeypatch.setattr(actions, "audit_intercepted", lambda **_kwargs: None)


def test_scenario1_old_awaiting_approval_does_not_swallow_complete_new_request(monkeypatch):
    """An awaiting_final_approval project >6h old MUST NOT swallow a distinct new-flyer request."""
    hooks, actions = _load_plugin_modules()
    stale = _stale_project(
        project_id="F0900",
        status="awaiting_final_approval",
        hours_old=8,
        raw_request="Create flyer for Diwali sale",
    )
    _patch_basic_lookups(hooks, actions, monkeypatch, stale)

    result = hooks._try_flyer_active_project_intercept(
        "Create flyer for Eid grocery special, Saturday 2pm-9pm, $20 off",
        "17329837841@s.whatsapp.net",
        {"message_id": "new-eid-1"},
    )
    # None means: bail out of active-project intercept; the new-project path takes over.
    assert result is None


def test_scenario2_old_manual_edit_required_does_not_swallow_distinct_poster_request(monkeypatch):
    """A manual_edit_required project >24h old MUST NOT swallow a distinct new poster request."""
    hooks, actions = _load_plugin_modules()
    stale = _stale_project(
        project_id="F0901",
        status="manual_edit_required",
        hours_old=30,
        raw_request="Edit uploaded flyer/source artwork",
    )
    _patch_basic_lookups(hooks, actions, monkeypatch, stale)

    result = hooks._try_flyer_active_project_intercept(
        "I need a flyer for the youth temple event next month",
        "17329837841@s.whatsapp.net",
        {"message_id": "new-temple"},
    )
    assert result is None


def test_scenario3_status_check_on_stale_manual_edit_still_returns_manual_status(monkeypatch):
    """Status check ('any update?') on a stale manual_edit_required project still routes to the manual-queue status reply, not bypassed.

    Locks in source-edit reason_code routing — `flyer_manual_edit_status_reply`
    is now reserved for reason_code=source_edit_provider_unavailable (S7 P0-6);
    other reason codes flow through `flyer_project_status_reply` which now
    consults MANUAL_REVIEW_REASON_LINES."""
    hooks, actions = _load_plugin_modules()
    stale = _stale_project(
        project_id="F0902",
        status="manual_edit_required",
        hours_old=30,
    )
    # Set reason_code so the source-edit-specific reply path is taken.
    stale["manual_review"] = {
        "status": "queued",
        "reason": "source_edit_provider_unavailable",
        "reason_code": "source_edit_provider_unavailable",
        "detail": "stale source-edit project",
        "queued_at": stale["updated_at"],
    }
    _patch_basic_lookups(hooks, actions, monkeypatch, stale)
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "flyer_manual_edit_status_reply", lambda _project: "Manual review queued; designer is working on it.")
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: "Status: ...")

    result = hooks._try_flyer_active_project_intercept(
        "any update?",
        "17329837841@s.whatsapp.net",
        {"message_id": "status-check"},
    )
    assert result == {"action": "skip", "reason": "cf-router flyer exact edit status for F0902"}
    assert sent and "Manual review" in sent[0]


def test_scenario4_correction_after_delivery_targets_latest_active_project(monkeypatch):
    """`find_active_flyer_project_by_sender` returns max-updated_at non-completed project.
    A correction on a customer with multiple non-terminal projects routes to the LATEST one.
    """
    actions = _load_actions()
    # Stub the projects-on-disk source: oldest=delivered yesterday, newest=manual_edit_required 1h ago.
    # The helper opens FLYER_PROJECTS_PATH; we monkeypatch the resolved path instead.
    now = datetime.now(timezone.utc)
    older_iso = (now - timedelta(days=1)).isoformat()
    newer_iso = (now - timedelta(hours=1)).isoformat()
    fake_store = {
        "projects": [
            {
                "project_id": "F0800",
                "status": "delivered",
                "customer_phone": "+17329837841",
                "updated_at": older_iso,
                "created_at": older_iso,
            },
            {
                "project_id": "F0801",
                "status": "manual_edit_required",
                "customer_phone": "+17329837841",
                "updated_at": newer_iso,
                "created_at": newer_iso,
            },
        ],
    }
    import json as _json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        _json.dump(fake_store, fh)
        path = Path(fh.name)
    try:
        monkeypatch_ctx = pytest.MonkeyPatch()
        try:
            monkeypatch_ctx.setattr(actions, "FLYER_PROJECTS_PATH", path)
            monkeypatch_ctx.setattr(actions, "find_flyer_customer_by_sender", lambda _phone, _chat_id: None)
            monkeypatch_ctx.setattr(actions, "_canonical_phone", lambda v: v)
            picked = actions.find_active_flyer_project_by_sender("+17329837841", "17329837841@s.whatsapp.net")
            assert picked is not None and picked["project_id"] == "F0801"
        finally:
            monkeypatch_ctx.undo()
    finally:
        path.unlink(missing_ok=True)


def test_closed_no_send_project_is_not_active_for_sender(monkeypatch):
    """Operator-closed no-send projects are terminal and must not swallow new work."""
    actions = _load_actions()
    now = datetime.now(timezone.utc).isoformat()
    fake_store = {
        "projects": [
            {
                "project_id": "F0802",
                "status": "closed_no_send",
                "customer_phone": "+17329837841",
                "updated_at": now,
                "created_at": now,
            },
        ],
    }
    import json as _json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        _json.dump(fake_store, fh)
        path = Path(fh.name)
    monkeypatch.setattr(actions, "FLYER_PROJECTS_PATH", path)
    try:
        assert actions.find_active_flyer_project_by_sender("+17329837841", "17329837841@s.whatsapp.net") is None
    finally:
        path.unlink(missing_ok=True)


def test_scenario5_authorized_request_phone_resolves_to_same_account_project(monkeypatch):
    """A sender on one of the customer's authorized_request_numbers must resolve to the
    account's active project, not start a new one as a stranger."""
    actions = _load_actions()
    customer_phone = "+19045550104"
    authorized_phone = "+17329837841"  # different physical handset, same account
    now = datetime.now(timezone.utc)
    updated_iso = (now - timedelta(minutes=10)).isoformat()
    fake_store = {
        "projects": [
            {
                "project_id": "F0600",
                "status": "awaiting_final_approval",
                "customer_phone": customer_phone,
                "updated_at": updated_iso,
                "created_at": updated_iso,
            },
        ],
    }
    import json as _json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        _json.dump(fake_store, fh)
        path = Path(fh.name)
    try:
        monkeypatch_ctx = pytest.MonkeyPatch()
        try:
            monkeypatch_ctx.setattr(actions, "FLYER_PROJECTS_PATH", path)
            monkeypatch_ctx.setattr(
                actions,
                "find_flyer_customer_by_sender",
                lambda _phone, _chat_id: {
                    "customer_id": "CUST0001",
                    "public_phone": customer_phone,
                    "business_whatsapp_number": customer_phone,
                    "onboarded_by_phone": customer_phone,
                    "authorized_request_numbers": [authorized_phone],
                },
            )
            monkeypatch_ctx.setattr(actions, "_canonical_phone", lambda v: v)
            picked = actions.find_active_flyer_project_by_sender(authorized_phone, "17329837841@s.whatsapp.net")
        finally:
            monkeypatch_ctx.undo()
    finally:
        path.unlink(missing_ok=True)
    assert picked is not None and picked["project_id"] == "F0600"


def test_scenario6_fresh_active_project_still_attaches_revision_correction(monkeypatch):
    """Regression guard: when the active project is FRESH (within threshold), a revision
    correction continues to attach to it as before — the stale guard must not over-fire."""
    hooks, actions = _load_plugin_modules()
    fresh = _stale_project(
        project_id="F0903",
        status="manual_edit_required",
        hours_old=2,  # well under the 24h manual_edit threshold
    )
    _patch_basic_lookups(hooks, actions, monkeypatch, fresh)
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "flyer_manual_edit_status_reply", lambda _project: "manual reply")
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: "status reply")

    # "change the date" is a revision intent (`is_flyer_revision_intent` matches "change").
    # Even on a fresh manual_edit project, revisions should be handled by the existing flow
    # (status reply for manual queue, because the project IS in the manual queue).
    result = hooks._try_flyer_active_project_intercept(
        "change the date to next Saturday",
        "17329837841@s.whatsapp.net",
        {"message_id": "revision-1"},
    )
    # On fresh manual_edit_required, the intercept handles the message — does NOT return None.
    # (Either status reply, since the manual-queue flow doesn't auto-revise. Either way: not None.)
    assert result is not None


def test_stale_guard_does_not_drop_concept_selection_after_threshold(monkeypatch):
    """Regression for review BLOCKER: concept selection "1"/"2"/"3"/"C1" past the
    awaiting_concept_selection 6h threshold must NOT trip the stale guard. The selection_map
    handler runs further down the intercept and must still be reachable.
    """
    hooks, actions = _load_plugin_modules()
    stale_awaiting_selection = _stale_project(
        project_id="F0910",
        status="awaiting_concept_selection",
        hours_old=10,
    )
    _patch_basic_lookups(hooks, actions, monkeypatch, stale_awaiting_selection)
    # Downstream selection handler invokes update-flyer-project + sends a confirmation.
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (True, "mid", ""))

    result = hooks._try_flyer_active_project_intercept(
        "1",
        "17329837841@s.whatsapp.net",
        {"message_id": "select-concept-1"},
    )
    assert result is not None, "concept selection text must not be dropped by stale guard"
    assert "selected C1" in result.get("reason", ""), result


def test_stale_guard_does_not_drop_approval_text_after_threshold(monkeypatch):
    """Regression for review BLOCKER: "approve" on stale awaiting_final_approval must NOT
    trip the stale guard. The approval flow runs further down the intercept."""
    hooks, actions = _load_plugin_modules()
    stale_awaiting_approval = _stale_project(
        project_id="F0911",
        status="awaiting_final_approval",
        hours_old=10,
        raw_request="Create flyer for Diwali",
    )
    stale_awaiting_approval["concepts"] = [{"concept_id": "C1"}]
    stale_awaiting_approval["selected_concept_id"] = "C1"
    _patch_basic_lookups(hooks, actions, monkeypatch, stale_awaiting_approval)
    monkeypatch.setattr(actions, "invoke_update_flyer_project", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(actions, "finalize_and_send_flyer", lambda *_a, **_kw: (True, "finalized"))
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, _text: (True, "mid", ""))

    result = hooks._try_flyer_active_project_intercept(
        "approve",
        "17329837841@s.whatsapp.net",
        {"message_id": "approve-stale"},
    )
    # The result is whatever the approval handler returns; the key invariant is "not None"
    # (i.e. the stale guard did NOT short-circuit the approval flow).
    assert result is not None, "approval text must not be dropped by stale guard"


def test_stale_guard_does_not_drop_non_english_reply(monkeypatch):
    """Regression for review HIGH: Hindi/Telugu/Hinglish replies on stale projects must
    NOT bail to new-project path. The regex helpers are English-only; the corrected guard
    uses positive evidence (should_start_new_flyer_over_active) which is also English-only
    but only fires on confident new-flyer matches, so non-English short replies attach
    normally."""
    hooks, actions = _load_plugin_modules()
    stale = _stale_project(
        project_id="F0912",
        status="manual_edit_required",
        hours_old=30,
    )
    _patch_basic_lookups(hooks, actions, monkeypatch, stale)
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "flyer_manual_edit_status_reply", lambda _project: "manual reply")
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: "status reply")

    # Telugu transliteration of "any update?" — neither English regex matches; with the
    # negative-evidence design this would have been dropped. With positive-evidence, the
    # short non-English reply has no should_start_new signal, so the intercept attaches
    # it to the existing project for downstream forwarding.
    result = hooks._try_flyer_active_project_intercept(
        "edaina update unda?",
        "17329837841@s.whatsapp.net",
        {"message_id": "non-english-status"},
    )
    assert result is not None, "non-English short reply must not be dropped by stale guard"


def test_stale_guard_lets_status_check_through_on_stale_project(monkeypatch):
    """A status check on a stale project must still route to the status-check handler,
    not bail out as a new-project case."""
    hooks, actions = _load_plugin_modules()
    stale = _stale_project(
        project_id="F0904",
        status="awaiting_final_approval",
        hours_old=10,
    )
    _patch_basic_lookups(hooks, actions, monkeypatch, stale)
    sent: list[str] = []
    monkeypatch.setattr(actions, "send_flyer_text", lambda _chat_id, text: sent.append(text) or (True, "mid", ""))
    monkeypatch.setattr(actions, "flyer_manual_edit_status_reply", lambda _project: "manual")
    monkeypatch.setattr(actions, "flyer_project_status_reply", lambda _project: "Project F0904 status: awaiting approval")

    result = hooks._try_flyer_active_project_intercept(
        "any update?",
        "17329837841@s.whatsapp.net",
        {"message_id": "status-stale"},
    )
    assert result is not None
    assert result.get("action") == "skip"
    assert "status" in result.get("reason", "")


# ---------- isolation invariant for create-flyer-project ----------

def test_isolation_invariant_create_flyer_project_uses_only_current_message_context(tmp_path, monkeypatch, capsys):
    """A new project's locked_facts, raw_request, and fields must derive ONLY from the
    current message + media + customer profile — never from a prior project's state.

    Today this is structurally true (create-flyer-project doesn't read prior projects).
    This test pins the invariant: seed the store with a prior project containing distinctive
    facts, then create a new project from a fresh message that does NOT mention those facts;
    verify none of the prior project's content appears in the new project.
    """
    import json
    import importlib.util
    import importlib.machinery as _machinery
    import sys
    import types as _types
    # Loader pattern used by test_flyer_create_project.py: stub safe_io to dodge
    # the fcntl import on Windows, then SourceFileLoader the extensionless script.
    class _NoopFileLock:
        def __init__(self, _path):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_exc):
            return None
    fake_safe_io = _types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    sys.path.insert(0, str(REPO / "src" / "platform"))
    script_path = REPO / "src" / "agents" / "flyer" / "scripts" / "create-flyer-project"
    module_name = "create_flyer_project_isolation_test"
    sys.modules.pop(module_name, None)
    loader = _machinery.SourceFileLoader(module_name, str(script_path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    customers_path = tmp_path / "customers.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()

    prior_project = {
        "project_id": "F0700",
        "status": "delivered",
        "customer_phone": "+17329837841",
        "created_at": "2026-05-18T00:00:00+00:00",
        "updated_at": "2026-05-18T00:00:00+00:00",
        "original_message_id": "prior-msg",
        "raw_request": "Create flyer for Diwali sweets sale at Lakshmi Kitchen, $9.99 each",
        "fields": {
            "event_or_business_name": "Lakshmi Kitchen",
            "venue_or_location": "Pineville",
            "contact_info": "+17329837841",
            "notes": "Diwali sweets",
        },
        "locked_facts": [
            {"fact_id": "business_name", "label": "Business", "value": "Lakshmi Kitchen", "source": "customer_text", "required": True},
            {"fact_id": "promo_item", "label": "Item", "value": "Diwali sweets $9.99", "source": "customer_text", "required": False},
        ],
        "assets": [],
        "concepts": [],
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
    }
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 701,
        "projects": [prior_project],
    }), encoding="utf-8")
    customers_path.write_text(json.dumps({
        "schema_version": 1,
        "next_customer_sequence": 1,
        "customers": [],
    }), encoding="utf-8")

    distinct_request = "Create flyer for Eid biryani special, Saturday 2pm-9pm"
    monkeypatch.setattr(sys, "argv", [
        "create-flyer-project",
        "--customer-phone", "+17329837841",
        "--message-id", "new-eid-msg",
        "--raw-request", distinct_request,
        "--state-path", str(state_path),
        "--customer-state-path", str(customers_path),
        "--asset-dir", str(asset_dir),
    ])

    assert module.main() == 0
    new_project = json.loads(capsys.readouterr().out)

    # Invariant: distinctive prior-project tokens must NOT appear in the new project.
    assert new_project["project_id"] != "F0700"
    assert "Lakshmi Kitchen" not in new_project.get("raw_request", "")
    assert "Diwali" not in new_project.get("raw_request", "")
    new_locked = new_project.get("locked_facts") or []
    for fact in new_locked:
        assert "Lakshmi Kitchen" not in (fact.get("value") or "")
        assert "Diwali" not in (fact.get("value") or "")
    new_fields = new_project.get("fields") or {}
    assert "Lakshmi Kitchen" not in (new_fields.get("event_or_business_name") or "")
    assert "Lakshmi Kitchen" not in (new_fields.get("venue_or_location") or "")
    # The prior project must still exist; distinctive content must be unchanged.
    # (Pydantic re-validates the store on write and may normalize formatting —
    # ISO datetime suffix, default-filled fields, locked_fact.confidence — so an
    # exact dict equality is too strict. The invariant is "no content mutation",
    # not "no formatting normalization".)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    prior_after = next(p for p in persisted["projects"] if p["project_id"] == "F0700")
    assert prior_after["raw_request"] == prior_project["raw_request"]
    assert prior_after["fields"]["event_or_business_name"] == prior_project["fields"]["event_or_business_name"]
    assert prior_after["status"] == prior_project["status"]
    prior_locked_values = {f["value"] for f in prior_after.get("locked_facts") or []}
    assert "Lakshmi Kitchen" in prior_locked_values
    assert "Diwali sweets $9.99" in prior_locked_values
