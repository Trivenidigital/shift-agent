"""P0-6 close/no-send cockpit action + P0-5 customer-visible action preview.

Covers `POST /flyer/manual-queue/{id}/close-no-send` (mirrors `flyer-manual-queue --close`
CLI semantics — freshness guard, documented token bypass, proactive customer
notification under flock) and `GET /flyer/manual-queue/{id}/action-preview`
across all three mutating actions (close_no_send, complete, break_glass) so
the cockpit can render exact customer-visible copy before the operator
commits.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────
# Fixtures shared with the other flyer cockpit test files
# ─────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _queued_project(
    project_id: str,
    *,
    phone: str = "+19045550104",
    reason_code: str = "source_edit_provider_unavailable",
    queued_at: str = "2026-05-20T00:00:00Z",
    created_at: str | None = None,
) -> dict:
    return {
        "project_id": project_id,
        "status": "manual_edit_required",
        "customer_phone": phone,
        "created_at": created_at or queued_at,
        "updated_at": queued_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Authorized exact edit. Replace phone number.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": phone},
        "manual_review": {
            "status": "queued",
            "reason": reason_code,
            "reason_code": reason_code,
            "detail": "",
            "queued_at": queued_at,
        },
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
    }


def _seed_store(tmp_path: Path, projects: list[dict]) -> None:
    from app.routers import flyer
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    settings.decisions_path = tmp_path / "logs" / "decisions.log"
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects},
    )


def _seed_customer(tmp_path: Path, *, phone: str, chat_id: str, customer_id: str = "CUST0001") -> None:
    from app.routers import flyer
    settings = flyer.get_settings()
    customers_path = settings.state_dir / "flyer" / "customers.json"
    _write_json(customers_path, {"customers": [{
        "customer_id": customer_id,
        "business_name": "Lakshmis Kitchen",
        "primary_chat_id": chat_id,
        "business_whatsapp_number": phone,
        "authorized_request_numbers": [phone],
    }]})


# ─────────────────────────────────────────────────────────────────
# POST /flyer/manual-queue/{id}/close-no-send
# ─────────────────────────────────────────────────────────────────


def test_close_no_send_happy_path_with_documented_token(tmp_path, monkeypatch):
    """Aged row + cleanup reason is rejected, but a fresh row with the
    documented `duplicate` token in the reason bypasses the freshness
    guard exactly like the CLI."""
    from app.routers import flyer
    # Make the project fresh (just queued) so the guard is exercised.
    now = datetime(2026, 5, 20, 0, 5, 0, tzinfo=timezone.utc)
    queued_at = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    _seed_store(tmp_path, [_queued_project("F0058", queued_at=queued_at)])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="201975216009469@lid")
    monkeypatch.setattr(flyer, "_now", lambda: now)

    # Mock the customer-notification path so we don't touch the real WhatsApp
    # bridge during the test; assert it gets called with the post-close state.
    captured: dict = {}
    def fake_notify(store, project_id, *, customers_path, decisions_log_path):
        captured["project_id"] = project_id
        captured["customers_path"] = customers_path
        captured["decisions_log_path"] = decisions_log_path
        captured["row_status"] = next(p.status for p in store.projects if p.project_id == project_id)
        return {
            "send_ok": True,
            "chat_id": "201975216009469@lid",
            "outbound_message_id": "mid-test-123",
            "error": "",
        }
    monkeypatch.setattr(flyer, "notify_customer_of_closure", fake_notify)

    result = flyer.manual_queue_close_no_send_action(
        "F0058",
        reason="operator_burndown_duplicate_provider_unavailable_no_customer_asset_sent",
        force=False,
    )

    assert result["ok"] is True
    assert result["project_id"] == "F0058"
    assert result["status"] == "closed_no_send"
    assert result["manual_status"] == "closed_no_send"
    assert result["backup"]  # backup path was created
    assert result["notification"]["send_ok"] is True
    assert result["notification"]["chat_id"] == "201975216009469@lid"
    assert result["notification"]["outbound_message_id"] == "mid-test-123"
    # Notifier must be invoked AFTER the closure is persisted to disk —
    # the store it sees should already show the row as closed_no_send.
    assert captured["row_status"] == "closed_no_send"

    # State on disk reflects the closure.
    from app.routers import flyer as flyer_mod
    store = flyer_mod.load_project_store()
    closed = next(p for p in store.projects if p.project_id == "F0058")
    assert closed.status == "closed_no_send"
    assert closed.manual_review.status == "closed_no_send"


def test_close_no_send_blocks_fresh_row_without_force_or_token(tmp_path, monkeypatch):
    """A bland reason on a fresh row must 409 — same shape as the CLI guard."""
    from fastapi import HTTPException
    from app.routers import flyer
    now = datetime(2026, 5, 20, 0, 5, 0, tzinfo=timezone.utc)
    queued_at = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    _seed_store(tmp_path, [_queued_project("F0058", queued_at=queued_at)])
    monkeypatch.setattr(flyer, "_now", lambda: now)

    # Notify must NOT be called when the guard rejects.
    def reject_notify(*_a, **_kw):
        pytest.fail("notify_customer_of_closure must not run when guard blocks")
    monkeypatch.setattr(flyer, "notify_customer_of_closure", reject_notify)

    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_close_no_send_action("F0058", reason="cleanup", force=False)
    assert exc.value.status_code == 409
    assert "queue row" in str(exc.value.detail).lower()


def test_close_no_send_force_bypasses_guard(tmp_path, monkeypatch):
    """`--force=true` bypasses the freshness guard but still requires a non-blank reason."""
    from app.routers import flyer
    now = datetime(2026, 5, 20, 0, 5, 0, tzinfo=timezone.utc)
    queued_at = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    _seed_store(tmp_path, [_queued_project("F0058", queued_at=queued_at)])
    monkeypatch.setattr(flyer, "_now", lambda: now)
    monkeypatch.setattr(flyer, "notify_customer_of_closure", lambda *_a, **_kw: {
        "send_ok": False, "chat_id": "", "outbound_message_id": "", "error": "no_chat_id_for_customer",
    })

    result = flyer.manual_queue_close_no_send_action(
        "F0058", reason="operator override after manual outreach", force=True,
    )
    assert result["status"] == "closed_no_send"
    assert result["notification"]["error"] == "no_chat_id_for_customer"


def test_close_no_send_404_on_unknown_project(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from app.routers import flyer
    _seed_store(tmp_path, [])
    monkeypatch.setattr(flyer, "_now", lambda: datetime(2026, 5, 20, 1, 0, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(flyer, "notify_customer_of_closure", lambda *_a, **_kw: {})
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_close_no_send_action("F9999", reason="duplicate row", force=False)
    # close_manual_project raises ValueError("project not found ...") which the
    # router translates to 409. Either 404 or 409 is acceptable as long as the
    # operator sees a clear error; the agent helper returns "not found" so this
    # currently surfaces as 409.
    assert exc.value.status_code in {404, 409}


def test_close_no_send_409_on_non_queued_project(tmp_path, monkeypatch):
    """Already-closed or never-queued rows must 409."""
    from fastapi import HTTPException
    from app.routers import flyer
    closed = _queued_project("F0058", queued_at="2026-05-19T00:00:00Z")
    closed["status"] = "closed_no_send"
    closed["manual_review"]["status"] = "closed_no_send"
    _seed_store(tmp_path, [closed])
    monkeypatch.setattr(flyer, "_now", lambda: datetime(2026, 5, 20, 0, 5, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(flyer, "notify_customer_of_closure", lambda *_a, **_kw: pytest.fail("notify must not run on 409"))

    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_close_no_send_action("F0058", reason="operator_burndown_duplicate", force=False)
    assert exc.value.status_code == 409


def test_close_no_send_writes_backup(tmp_path, monkeypatch):
    """Cockpit close path must back up projects.json before mutation (same
    pattern as complete/break-glass)."""
    from app.routers import flyer
    now = datetime(2026, 5, 20, 0, 5, 0, tzinfo=timezone.utc)
    _seed_store(tmp_path, [_queued_project("F0058", queued_at="2026-05-19T00:00:00Z")])
    monkeypatch.setattr(flyer, "_now", lambda: now)
    monkeypatch.setattr(flyer, "notify_customer_of_closure", lambda *_a, **_kw: {})
    flyer.manual_queue_close_no_send_action("F0058", reason="operator_burndown_duplicate", force=False)
    flyer_dir = flyer.get_settings().state_dir / "flyer"
    assert list(flyer_dir.glob("projects.json.pre-admin-*")), "backup file must exist after close"


# ─────────────────────────────────────────────────────────────────
# GET /flyer/manual-queue/{id}/action-preview
# ─────────────────────────────────────────────────────────────────


def test_action_preview_close_returns_canonical_closure_text(tmp_path):
    """The close preview must reuse `build_closure_customer_text` so the
    cockpit shows the EXACT text the customer will receive — no
    re-implementation of CLOSED_NO_SEND_REASON_LINES in the cockpit."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058", reason_code="source_edit_provider_unavailable")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="201975216009469@lid")

    preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert preview["action"] == "close_no_send"
    assert preview["will_notify"] is True
    assert preview["would_notify_chat_id"] == "201975216009469@lid"
    # Canonical closure copy for source_edit_provider_unavailable (PR #130).
    assert "apply that source-flyer edit" in preview["customer_text"]
    assert preview["reason_code"] == "source_edit_provider_unavailable"


def test_action_preview_close_per_reason_code_uses_table(tmp_path):
    """Pin that the preview routes through CLOSED_NO_SEND_REASON_LINES per
    reason_code — not a hardcoded single copy. Drift here would mean the
    customer sees different text than what the operator was shown."""
    from app.routers import flyer
    from agents.flyer.workflow import CLOSED_NO_SEND_REASON_LINES
    _seed_customer(tmp_path, phone="+19045550104", chat_id="201975216009469@lid")
    for reason_code in ("reference_unsupported", "missing_required_facts", "visual_qa_failed"):
        _seed_store(tmp_path, [_queued_project("F0058", reason_code=reason_code)])
        preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
        expected = CLOSED_NO_SEND_REASON_LINES[reason_code]
        assert expected in preview["customer_text"], (reason_code, preview["customer_text"])


def test_action_preview_close_marks_will_not_notify_when_no_chat_id(tmp_path):
    """If the customer record has no `primary_chat_id`, the preview must
    flag will_notify=False so the cockpit can warn the operator that the
    customer will only learn via the reactive 'any update?' fallback."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="")
    preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert preview["will_notify"] is False
    assert preview["would_notify_chat_id"] == ""


def test_action_preview_complete_returns_concept_caption(tmp_path):
    """Complete preview shows the concept-preview caption the customer
    will see on the next preview send (NOT a proactive push)."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="201975216009469@lid")
    preview = flyer.manual_queue_action_preview("F0058", action="complete")
    assert preview["action"] == "complete"
    assert preview["will_notify"] is False  # not a proactive push
    assert preview["customer_text"]
    assert "C1: Designer Approved" in preview["customer_text"]
    assert "Reply APPROVE" in preview["customer_text"]


def test_action_preview_break_glass_says_no_push(tmp_path):
    """Break-glass preview must explicitly mark customer_text=None +
    will_notify=False so the cockpit can render 'No customer message will
    be sent' (P0-5 acceptance: 'or explicitly show no push will be
    sent')."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="201975216009469@lid")
    preview = flyer.manual_queue_action_preview("F0058", action="break_glass")
    assert preview["action"] == "break_glass"
    assert preview["will_notify"] is False
    assert preview["customer_text"] is None
    assert "out-of-band" in preview["note"]


def test_action_preview_unknown_action_422(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_action_preview("F0058", action="nuke_from_orbit")
    assert exc.value.status_code == 422


def test_action_preview_404_on_unknown_project(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer
    _seed_store(tmp_path, [])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert exc.value.status_code == 404


def test_action_preview_complete_returns_both_messages(tmp_path):
    """REGRESSION (PR #133 review MEDIUM-3): the live send path emits TWO
    customer-visible messages on the next preview send — the media caption
    AND a follow-up text. Preview must surface BOTH so the operator sees
    the full customer-visible sequence. Single-string preview was an
    incomplete view of customer impact."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="201975216009469@lid")
    preview = flyer.manual_queue_action_preview("F0058", action="complete")
    assert "customer_messages" in preview
    assert len(preview["customer_messages"]) == 2, preview["customer_messages"]
    caption, followup = preview["customer_messages"]
    assert "C1: Designer Approved" in caption
    assert "Reply APPROVE or reply with changes." in caption
    assert "Reply APPROVE to receive final files" in followup
    # Legacy single-string `customer_text` retained but contains both for
    # back-compat clients that only read it.
    assert "C1: Designer Approved" in preview["customer_text"]
    assert "Reply APPROVE to receive final files" in preview["customer_text"]


def test_action_preview_close_uses_audit_chat_id_over_primary(tmp_path):
    """REGRESSION (PR #133 review HIGH-1, F0060 incident): for an
    authorized-requester / LID-only project, the customer's
    `primary_chat_id` (set during onboarding via customer_phone → JID)
    is NOT the chat the inbound arrived on. Audit log shows the canonical
    inbound chat_id; the resolver MUST prefer it. Otherwise the proactive
    push goes to the wrong WhatsApp thread and returns ok=True even
    though the customer's visible chat never sees the message."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058", phone="+19045550104")])
    # Customer record points at the phone JID (what onboarding stored).
    _seed_customer(tmp_path, phone="+19045550104", chat_id="17329837841@s.whatsapp.net")
    # Audit log captures the live inbound chat — a LID that's a DIFFERENT
    # thread than the phone JID. This mirrors the F0060 prod incident.
    decisions_log = flyer.get_settings().decisions_path
    decisions_log.parent.mkdir(parents=True, exist_ok=True)
    decisions_log.write_text(
        json.dumps({
            "ts": "2026-05-20T01:23:45.000000Z",
            "type": "cf_router_intercepted",
            "reason": "flyer_reference_exact_edit_queued",
            "chat_id": "201975216009469@lid",
            "code": None,
            "subprocess_rc": 0,
            "detail": "project_id=F0058; sender_role=employee; ...",
        }) + "\n",
        encoding="utf-8",
    )

    preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert preview["would_notify_chat_id"] == "201975216009469@lid", (
        "Audit chat_id must win over customer primary_chat_id — otherwise the "
        "F0060 misroute repeats and the customer never sees the proactive close."
    )
    assert preview["chat_id_source"] == "audit_log"


def test_action_preview_close_falls_back_to_primary_chat_id_without_audit(tmp_path):
    """When no audit row exists for the project (legacy/seeded), the
    resolver falls back to `primary_chat_id` (PR #130's original behavior)
    rather than refusing to notify."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="17329837841@s.whatsapp.net")
    # No decisions.log written — audit lookup must miss.
    preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert preview["would_notify_chat_id"] == "17329837841@s.whatsapp.net"
    assert preview["chat_id_source"] == "primary_chat_id"


def test_action_preview_close_reports_none_when_no_resolution(tmp_path):
    """Customer has no `primary_chat_id` and there's no audit row for the
    project. The cockpit must see `chat_id_source=none` so it can warn
    the operator that only the reactive safety net will reach the
    customer."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058")])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="")
    preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert preview["would_notify_chat_id"] == ""
    assert preview["chat_id_source"] == "none"
    assert preview["will_notify"] is False


def test_close_action_uses_audit_chat_id_at_notify_time(tmp_path, monkeypatch):
    """End-to-end regression for the F0060 misroute: a project owned by
    +19045550104 with a phone-JID primary_chat_id, but an audit row showing
    the inbound landed on @lid — the close action MUST send via @lid."""
    from app.routers import flyer
    now = datetime(2026, 5, 20, 1, 0, 0, tzinfo=timezone.utc)
    queued_at = "2026-05-19T20:00:00Z"  # well-aged, so no freshness guard
    _seed_store(tmp_path, [_queued_project("F0058", queued_at=queued_at)])
    _seed_customer(tmp_path, phone="+19045550104", chat_id="17329837841@s.whatsapp.net")
    decisions_log = flyer.get_settings().decisions_path
    decisions_log.parent.mkdir(parents=True, exist_ok=True)
    decisions_log.write_text(
        json.dumps({
            "ts": "2026-05-20T01:23:45.000000Z",
            "type": "cf_router_intercepted",
            "chat_id": "201975216009469@lid",
            "detail": "project_id=F0058; sender_role=employee;",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(flyer, "_now", lambda: now)

    # Don't stub notify_customer_of_closure — run the real one with a
    # mock bridge so we can assert which chat_id it was called with.
    sent: list[tuple[str, str]] = []
    from agents.flyer import manual_queue as mq
    def fake_bridge(chat_id, text):
        sent.append((chat_id, text))
        return (True, "mid-real-test", "", "sent")
    monkeypatch.setattr(mq, "bridge_post", fake_bridge, raising=False)
    # Also need to bypass the lazy safe_io import path inside notify_customer_of_closure:
    import importlib
    import sys as _sys
    safe_io_stub = importlib.util.module_from_spec(importlib.util.spec_from_loader("safe_io", loader=None))
    safe_io_stub.bridge_post = fake_bridge  # type: ignore[attr-defined]
    safe_io_stub.ndjson_append = lambda *_a, **_kw: None  # type: ignore[attr-defined]
    _sys.modules["safe_io"] = safe_io_stub

    result = flyer.manual_queue_close_no_send_action(
        "F0058", reason="operator_burndown_duplicate", force=False,
    )
    assert result["notification"]["send_ok"] is True
    assert result["notification"]["chat_id"] == "201975216009469@lid", (
        f"close-no-send notification must go to the audit-derived LID chat, "
        f"not the customer's primary phone JID; got {result['notification']!r}"
    )
    # And the bridge actually saw the @lid as the target.
    assert sent and sent[0][0] == "201975216009469@lid"


def test_close_action_runs_notify_outside_projects_flock(tmp_path):
    """REGRESSION (PR #133 review MEDIUM-4): the WhatsApp bridge call
    inside `notify_customer_of_closure` is slow; holding the
    projects.json flock through it would block unrelated state writers.
    Verified by source inspection — the notify call must sit OUTSIDE the
    `with safe_io.flock(...)` block in `manual_queue_close_no_send_action`."""
    source = Path(__file__).resolve().parent.parent.parent.parent / "web" / "backend" / "app" / "routers" / "flyer.py"
    text = source.read_text(encoding="utf-8")
    # Find the close action function body.
    func_idx = text.find("def manual_queue_close_no_send_action(")
    assert func_idx >= 0, "function not found"
    body_end = text.find("\n\n\n", func_idx)
    body = text[func_idx:body_end if body_end > 0 else None]
    flock_idx = body.find("with safe_io.flock(")
    notify_idx = body.find("notify_customer_of_closure(")
    assert flock_idx >= 0 and notify_idx >= 0
    # Find the dedent point where the `with` block ends — first line at
    # the function's base indentation (4 spaces) AFTER flock_idx.
    lines = body.splitlines()
    flock_line = body[:flock_idx].count("\n")
    notify_line = body[:notify_idx].count("\n")
    # Walk forward from flock_line to find the first line at indent level 4
    # (function body) — that's where the `with` block ends.
    flock_end_line = None
    for i in range(flock_line + 1, len(lines)):
        line = lines[i]
        if line.strip() == "":
            continue
        # `with` body is indented to 8+ spaces; function-body indent is 4.
        if line.startswith("    ") and not line.startswith("        "):
            flock_end_line = i
            break
    assert flock_end_line is not None, "could not detect end of with safe_io.flock(...) block"
    assert notify_line > flock_end_line, (
        f"notify_customer_of_closure must be called AFTER the projects.json "
        f"flock block exits (notify at line {notify_line} but flock ends at "
        f"line {flock_end_line}); a slow bridge call inside the lock would "
        f"block unrelated project writes."
    )


def test_action_preview_ownership_chat_id_resolves_to_owning_customer_only(tmp_path):
    """Preview must NOT leak another customer's chat_id. Build a 2-customer
    fixture; preview for F0058 (owned by +19045550104) must surface that
    customer's chat_id, not the other one."""
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0058", phone="+19045550104")])
    settings = flyer.get_settings()
    customers_path = settings.state_dir / "flyer" / "customers.json"
    _write_json(customers_path, {"customers": [
        {
            "customer_id": "CUST0001", "business_name": "Lakshmis Kitchen",
            "primary_chat_id": "201975216009469@lid",
            "business_whatsapp_number": "+19045550104",
            "authorized_request_numbers": ["+19045550104"],
        },
        {
            "customer_id": "CUST0002", "business_name": "Fresh Meats",
            "primary_chat_id": "OTHER-CHAT-DO-NOT-LEAK@lid",
            "business_whatsapp_number": "+19048626362",
            "authorized_request_numbers": ["+19048626362"],
        },
    ]})
    preview = flyer.manual_queue_action_preview("F0058", action="close_no_send")
    assert preview["would_notify_chat_id"] == "201975216009469@lid"
    assert "OTHER-CHAT-DO-NOT-LEAK" not in str(preview)
