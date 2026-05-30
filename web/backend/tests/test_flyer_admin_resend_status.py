"""P3 safe-action: proactive 'resend status' cockpit nudge.

Covers `POST /flyer/manual-queue/{id}/resend-status` (re-sends the customer's
CURRENT status reply so an operator working a stalled manual-queue row can
proactively reassure a waiting customer instead of waiting for their next
'any update?' inbound) plus the `resend_status` branch of
`GET /flyer/manual-queue/{id}/action-preview`.

The send reuses `build_project_status_reply` (single source of truth shared
with the reactive reply) and is a pure read-and-send — NO project-state
mutation, NO transition. Best-effort: never raises, audits every attempt
with a `flyer_status_resent` row.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────
# Fixtures (mirrors test_flyer_admin_close_no_send.py)
# ─────────────────────────────────────────────────────────────────


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _queued_project(
    project_id: str,
    *,
    phone: str = "+19045550104",
    status: str = "manual_edit_required",
    manual_status: str = "queued",
    reason_code: str = "source_edit_provider_unavailable",
    queued_at: str = "2026-05-20T00:00:00Z",
) -> dict:
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": phone,
        "created_at": queued_at,
        "updated_at": queued_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Authorized exact edit. Replace phone number.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": phone},
        "manual_review": {
            "status": manual_status,
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


def _seed_customer(tmp_path: Path, *, phone: str = "+19045550104",
                   chat_id: str = "17329837841@s.whatsapp.net") -> None:
    from app.routers import flyer
    settings = flyer.get_settings()
    customers_path = settings.state_dir / "flyer" / "customers.json"
    _write_json(customers_path, {"customers": [{
        "customer_id": "CUST0001",
        "business_name": "Lakshmis Kitchen",
        "primary_chat_id": chat_id,
        "business_whatsapp_number": phone,
        "authorized_request_numbers": [phone],
    }]})


def _store_from(projects: list[dict]):
    from schemas import FlyerProjectStore
    return FlyerProjectStore.model_validate(
        {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects}
    )


# ─────────────────────────────────────────────────────────────────
# Helper-level: resend_status_to_customer (manual_queue.py)
# ─────────────────────────────────────────────────────────────────


def test_resend_status_sends_current_status_reply(tmp_path):
    from agents.flyer.manual_queue import resend_status_to_customer
    from agents.flyer.workflow import build_project_status_reply

    store = _store_from([_queued_project("F0070")])
    customers = tmp_path / "customers.json"
    _write_json(customers, {"customers": [{
        "customer_id": "CUST0001",
        "business_name": "Lakshmis Kitchen",
        "primary_chat_id": "17329837841@s.whatsapp.net",
        "business_whatsapp_number": "+19045550104",
        "authorized_request_numbers": ["+19045550104"],
    }]})
    decisions = tmp_path / "decisions.log"
    decisions.write_text("", encoding="utf-8")

    sent: list[tuple[str, str]] = []

    def fake_bridge(chat_id, text):
        sent.append((chat_id, text))
        return (True, "MID1", "", 200)

    appended: list[str] = []

    entry = resend_status_to_customer(
        store, "F0070",
        customers_path=customers,
        decisions_log_path=decisions,
        bridge_send=fake_bridge,
        audit_append=lambda _p, line: appended.append(line),
    )

    expected_text = build_project_status_reply(store.projects[0])
    assert len(sent) == 1
    assert sent[0][0] == "17329837841@s.whatsapp.net"
    assert sent[0][1] == expected_text  # single source of truth, no drift
    assert entry["type"] == "flyer_status_resent"
    assert entry["send_ok"] is True
    assert entry["chat_id"] == "17329837841@s.whatsapp.net"
    assert len(appended) == 1
    assert json.loads(appended[0])["type"] == "flyer_status_resent"


def test_resend_status_no_chat_id_audits_without_send(tmp_path):
    from agents.flyer.manual_queue import resend_status_to_customer

    store = _store_from([_queued_project("F0070")])
    customers = tmp_path / "customers.json"
    _write_json(customers, {"customers": []})  # no matching customer → no chat_id
    decisions = tmp_path / "decisions.log"
    decisions.write_text("", encoding="utf-8")

    def reject_bridge(_chat_id, _text):
        pytest.fail("bridge must not be called when no chat_id resolves")

    appended: list[str] = []
    entry = resend_status_to_customer(
        store, "F0070",
        customers_path=customers,
        decisions_log_path=decisions,
        bridge_send=reject_bridge,
        audit_append=lambda _p, line: appended.append(line),
    )

    assert entry["send_ok"] is False
    assert entry["error"] == "no_chat_id_for_customer"
    assert len(appended) == 1  # attempt is still audited


def test_resend_status_bridge_failure_never_raises(tmp_path):
    from agents.flyer.manual_queue import resend_status_to_customer

    store = _store_from([_queued_project("F0070")])
    customers = tmp_path / "customers.json"
    _write_json(customers, {"customers": [{
        "customer_id": "CUST0001",
        "business_name": "Lakshmis Kitchen",
        "primary_chat_id": "17329837841@s.whatsapp.net",
        "business_whatsapp_number": "+19045550104",
        "authorized_request_numbers": ["+19045550104"],
    }]})
    decisions = tmp_path / "decisions.log"
    decisions.write_text("", encoding="utf-8")

    def failing_bridge(_chat_id, _text):
        return (False, "", "bridge exploded", 500)

    entry = resend_status_to_customer(
        store, "F0070",
        customers_path=customers,
        decisions_log_path=decisions,
        bridge_send=failing_bridge,
        audit_append=lambda _p, _line: None,
    )
    assert entry["send_ok"] is False
    assert "500" in entry["error"]


def test_resend_status_unknown_project_skips(tmp_path):
    from agents.flyer.manual_queue import resend_status_to_customer

    store = _store_from([_queued_project("F0070")])
    entry = resend_status_to_customer(
        store, "F9999",
        customers_path=tmp_path / "customers.json",
        decisions_log_path=tmp_path / "decisions.log",
        bridge_send=lambda *_a: pytest.fail("no send for unknown project"),
        audit_append=lambda *_a: None,
    )
    assert entry["skipped"] is True
    assert entry["error"] == "project_not_found"


# ─────────────────────────────────────────────────────────────────
# Endpoint-level: manual_queue_resend_status_action (router)
# ─────────────────────────────────────────────────────────────────


def test_resend_status_action_happy_path(tmp_path, monkeypatch):
    from app.routers import flyer
    _seed_store(tmp_path, [_queued_project("F0070")])

    monkeypatch.setattr(flyer, "resend_status_to_customer", lambda *_a, **_kw: {
        "send_ok": True, "chat_id": "17329837841@s.whatsapp.net",
        "outbound_message_id": "MID1", "error": "",
    })
    result = flyer.manual_queue_resend_status_action("F0070")
    assert result["ok"] is True
    assert result["status"] == "manual_edit_required"
    assert result["manual_status"] == "queued"
    assert result["notification"]["send_ok"] is True


def test_resend_status_action_404_unknown(tmp_path, monkeypatch):
    from app.routers import flyer
    from fastapi import HTTPException
    _seed_store(tmp_path, [_queued_project("F0070")])
    monkeypatch.setattr(flyer, "resend_status_to_customer",
                        lambda *_a, **_kw: pytest.fail("no send on 404"))
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_resend_status_action("F9999")
    assert exc.value.status_code == 404


def test_resend_status_action_409_not_in_queue(tmp_path, monkeypatch):
    from app.routers import flyer
    from fastapi import HTTPException
    _seed_store(tmp_path, [_queued_project(
        "F0070", status="closed_no_send", manual_status="closed_no_send")])
    monkeypatch.setattr(flyer, "resend_status_to_customer",
                        lambda *_a, **_kw: pytest.fail("no send when not in manual queue"))
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_resend_status_action("F0070")
    assert exc.value.status_code == 409


# ─────────────────────────────────────────────────────────────────
# Preview-before-action: resend_status branch
# ─────────────────────────────────────────────────────────────────


def test_action_preview_resend_status(tmp_path):
    from app.routers import flyer
    from agents.flyer.workflow import build_project_status_reply
    _seed_store(tmp_path, [_queued_project("F0070")])
    _seed_customer(tmp_path)

    preview = flyer.manual_queue_action_preview("F0070", action="resend_status")
    store = flyer.load_project_store()
    expected = build_project_status_reply(store.projects[0])

    assert preview["action"] == "resend_status"
    assert preview["customer_text"] == expected
    assert preview["will_notify"] is True
    assert preview["would_notify_chat_id"] == "17329837841@s.whatsapp.net"


def test_action_preview_rejects_unknown_action(tmp_path):
    from app.routers import flyer
    from fastapi import HTTPException
    _seed_store(tmp_path, [_queued_project("F0070")])
    with pytest.raises(HTTPException) as exc:
        flyer.manual_queue_action_preview("F0070", action="nonsense")
    assert exc.value.status_code == 422
