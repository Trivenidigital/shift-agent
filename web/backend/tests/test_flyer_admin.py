from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

pytest.importorskip("fastapi")


def _customer(
    customer_id: str,
    *,
    business_name: str = "Lakshmis Kitchen",
    phone: str = "+17329837841",
    plan_id: str = "trial",
    status: str = "trial",
    usage_events: list[dict] | None = None,
) -> dict:
    now = datetime(2026, 5, 18, tzinfo=timezone.utc).isoformat()
    return {
        "customer_id": customer_id,
        "business_name": business_name,
        "business_address": "90 Brybar Dr",
        "primary_chat_id": f"{phone.replace('+', '')}@s.whatsapp.net",
        "onboarded_by_phone": phone,
        "public_phone": phone,
        "business_whatsapp_number": phone,
        "authorized_request_numbers": [phone],
        "business_category": "Restaurant",
        "preferred_language": "en",
        "plan_id": plan_id,
        "status": status,
        "created_at": now,
        "updated_at": now,
        "activated_at": now if status in {"trial", "active"} else None,
        "plan_started_at": now,
        "current_period_start": now,
        "usage_events": usage_events or [],
    }


def _write_json(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_ndjson(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")


def _project(project_id: str, *, status: str, updated_at: str = "2000-01-01T00:00:00Z") -> dict:
    return {
        "project_id": project_id,
        "status": status,
        "customer_phone": "+17329837841",
        "created_at": "2000-01-01T00:00:00Z",
        "updated_at": updated_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Edit uploaded flyer/source artwork. Remove stale time.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+17329837841"},
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
    }


def test_flyer_summary_segments_customers_and_guest_orders(tmp_path, monkeypatch):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"

    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 4,
            "customers": [
                _customer("CUST0001", plan_id="trial", status="trial"),
                _customer("CUST0002", phone="+18479155253", plan_id="starter", status="active"),
                _customer("CUST0003", phone="+18479610344", plan_id="growth", status="payment_pending"),
            ],
        },
    )
    _write_json(
        settings.state_dir / "flyer" / "guest_orders.json",
        {
            "schema_version": 1,
            "orders": [
                {
                    "order_id": "GUEST0001",
                    "chat_id": "19802005022@s.whatsapp.net",
                    "sender_phone": "+19802005022",
                    "status": "paid",
                    "flyer_count_purchased": 1,
                    "flyer_count_used": 0,
                    "unit_price_cents": 400,
                    "currency": "USD",
                    "original_message_id": "MSG1",
                    "created_at": "2026-05-18T00:00:00Z",
                    "updated_at": "2026-05-18T00:00:00Z",
                }
            ],
        },
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 3,
            "projects": [
                _project("F9001", status="manual_edit_required"),
                _project("F9002", status="intake_started"),
            ],
        },
    )

    summary = flyer.build_summary()

    assert summary["segments"]["free_trial"] == 1
    assert summary["segments"]["paid"] == 1
    assert summary["segments"]["payment_pending"] == 1
    assert summary["segments"]["one_time"] == 1
    assert summary["active_projects"] == 2
    assert summary["manual_edit_count"] == 1
    assert summary["stuck_edit_count"] == 1
    assert summary["stuck_projects"] == 1


def test_project_rows_mark_stale_manual_edits(tmp_path):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 2,
            "projects": [_project("F9001", status="manual_edit_required")],
        },
    )

    result = asyncio.run(flyer.projects())

    assert result["projects"][0]["project_id"] == "F9001"
    assert "manual_edit_queue" in result["projects"][0]["attention"]
    assert "manual_edit_stale" in result["projects"][0]["attention"]


def test_extend_trial_increases_trial_quota_limit(tmp_path):
    from app.routers import flyer
    from schemas import FlyerConfig

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001")],
        },
    )

    result = flyer.extend_trial_quota("CUST0001", extra_flyers=2, reason="manual goodwill")

    store = flyer.load_customer_store()
    customer = store.find_customer_by_id("CUST0001")
    assert result["trial_bonus_flyers"] == 2
    assert customer is not None
    assert customer.trial_bonus_flyers == 2
    assert customer.quota_remaining(FlyerConfig().plan_tiers) == 5
    assert list((settings.state_dir / "flyer").glob("customers.json.pre-admin-*"))


def test_reset_trial_quota_releases_counted_usage(tmp_path):
    from app.routers import flyer
    from schemas import FlyerConfig

    now = "2026-05-18T00:00:00Z"
    events = [
        {
            "reservation_id": "res-F0010",
            "project_id": "F0010",
            "customer_id": "CUST0001",
            "kind": "used",
            "count": 1,
            "recorded_at": now,
            "message_id": "MSG1",
        },
        {
            "reservation_id": "res-F0011",
            "project_id": "F0011",
            "customer_id": "CUST0001",
            "kind": "reserved",
            "count": 1,
            "recorded_at": now,
            "message_id": "MSG2",
        },
    ]
    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001", usage_events=events)],
        },
    )

    result = flyer.reset_trial_quota("CUST0001", reason="testing reset")

    store = flyer.load_customer_store()
    customer = store.find_customer_by_id("CUST0001")
    assert result["released"] == 2
    assert customer is not None
    assert customer.usage_count_for_current_period() == 0
    assert customer.quota_remaining(FlyerConfig().plan_tiers) == 3


def test_deactivate_customer_soft_cancels_and_preserves_projects(tmp_path):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001", status="active", plan_id="starter")],
        },
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 2,
            "projects": [_project("F0061", status="awaiting_final_approval")],
        },
    )

    result = flyer.deactivate_customer("CUST0001", reason="customer asked to stop flyer studio")

    store = flyer.load_customer_store()
    customer = store.find_customer_by_id("CUST0001")
    projects = flyer.load_project_store().projects
    assert result["ok"] is True
    assert result["customer_id"] == "CUST0001"
    assert result["previous_status"] == "active"
    assert result["status"] == "cancelled"
    assert customer is not None
    assert customer.status == "cancelled"
    assert "Deactivated by Cockpit" in customer.notes
    assert projects and projects[0].project_id == "F0061"
    assert projects[0].status == "awaiting_final_approval"
    assert list((settings.state_dir / "flyer").glob("customers.json.pre-admin-*"))


def test_deactivate_customer_is_idempotent(tmp_path):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001", status="cancelled", plan_id="starter")],
        },
    )

    result = flyer.deactivate_customer("CUST0001", reason="repeat operator click")

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert result["already_inactive"] is True


def test_deactivate_customer_unknown_returns_404(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {"schema_version": 1, "next_customer_sequence": 1, "customers": []},
    )

    with pytest.raises(HTTPException) as exc:
        flyer.deactivate_customer("CUST9999", reason="not present")
    assert exc.value.status_code == 404


def test_deactivated_customer_moves_to_inactive_segment(tmp_path):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 3,
            "customers": [
                _customer("CUST0001", status="cancelled", plan_id="starter"),
                _customer("CUST0002", phone="+18479155253", status="trial", plan_id="trial"),
            ],
        },
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_sequence": 1, "projects": []},
    )

    inactive = asyncio.run(flyer.customers(segment="inactive", _=None))
    free_trial = asyncio.run(flyer.customers(segment="free_trial", _=None))

    assert [row["customer_id"] for row in inactive["customers"]] == ["CUST0001"]
    assert [row["customer_id"] for row in free_trial["customers"]] == ["CUST0002"]


def _build_test_client_with_fresh_otp():
    from fastapi.testclient import TestClient
    from app import auth as auth_mod
    from app.main import app

    async def _bypass_fresh():
        return {"sub": "test-operator", "iat": 9_999_999_999}

    app.dependency_overrides[auth_mod.require_fresh_otp] = _bypass_fresh

    class _Ctx:
        def __enter__(self):
            self.client = TestClient(app)
            return self.client

        def __exit__(self, *args):
            self.client.close()
            app.dependency_overrides.clear()

    return _Ctx()


def test_deactivate_customer_endpoint_requires_reason_auth_and_fresh_otp(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    from fastapi.testclient import TestClient
    from jose import jwt
    from app import auth as auth_mod
    from app.main import app
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001", status="active", plan_id="starter")],
        },
    )

    with TestClient(app) as client:
        unauth = client.post("/flyer/customers/CUST0001/deactivate", json={"reason": "operator request"})
        assert unauth.status_code == 401

        missing_reason = client.post("/flyer/customers/CUST0001/deactivate", json={"reason": ""})
        assert missing_reason.status_code in {401, 422}

        stale_claims = {
            "sub": "+19045550100",
            "iat": 1_700_000_000,
            "exp": 1_800_000_000,
            "jti": "stale-test",
            "auth_method": "pushover",
        }
        token = jwt.encode(stale_claims, auth_mod.settings.jwt_secret, algorithm=auth_mod.settings.jwt_algo)
        client.cookies.set(auth_mod.settings.cookie_name, token)
        monkeypatch.setattr(auth_mod, "_now", lambda: 1_700_000_400)
        stale = client.post("/flyer/customers/CUST0001/deactivate", json={"reason": "operator request"})
        assert stale.status_code == 403


def test_deactivate_customer_endpoint_audits_action(tmp_path):
    pytest.importorskip("jose")
    from app import audit as audit_mod
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    audit_mod.settings.cockpit_audit_log = settings.cockpit_audit_log
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {
            "schema_version": 1,
            "next_customer_sequence": 2,
            "customers": [_customer("CUST0001", status="trial", plan_id="trial")],
        },
    )

    with _build_test_client_with_fresh_otp() as client:
        missing_reason = client.post(
            "/flyer/customers/CUST0001/deactivate",
            json={"reason": ""},
        )
        assert missing_reason.status_code == 422

        resp = client.post(
            "/flyer/customers/CUST0001/deactivate",
            json={"reason": "customer requested removal"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    rows = [
        json.loads(line)
        for line in settings.cockpit_audit_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["event"] == "flyer.customer.deactivate"
    assert rows[-1]["details"]["customer_id"] == "CUST0001"
    assert rows[-1]["details"]["reason"] == "customer requested removal"


def test_flyer_customers_caps_at_300_sorted_by_updated_at(tmp_path):
    """BUG-FLYER-QA-002: /flyer/customers must cap results at 300 and sort
    by updated_at desc, matching /projects and /guest-orders.

    Seeds 305 customers with strictly-increasing updated_at; the endpoint
    must return exactly 300 rows, newest first.
    """
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    customers = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(305):
        c = _customer(f"CUST{i:04d}", phone=f"+1555010{i:04d}")
        c["created_at"] = base.isoformat()
        c["updated_at"] = (base + timedelta(minutes=i)).isoformat()
        customers.append(c)
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {"schema_version": 1, "next_customer_sequence": 306, "customers": customers},
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_project_sequence": 1, "projects": []},
    )

    result = asyncio.run(flyer.customers(query="", segment="", _=None))
    rows = result["customers"]

    assert len(rows) == 300
    assert rows[0]["customer_id"] == "CUST0304"
    assert rows[-1]["customer_id"] == "CUST0005"
    # rows[0..299] correspond to indices 304..5; verify strict desc order
    for prev, curr in zip(rows, rows[1:]):
        assert prev["updated_at"] >= curr["updated_at"]
    # Pagination metadata so the dashboard can show "showing 1-300 of 305"
    # and navigate to the next page rather than silently dropping rows.
    assert result["total"] == 305
    assert result["offset"] == 0
    assert result["limit"] == 300
    assert result["truncated"] is True


def test_flyer_customers_not_truncated_under_cap(tmp_path):
    """BUG-FLYER-QA-002 (review follow-up): under the 300-row cap the
    `truncated` field must be False and `total` matches the row count."""
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    customers = [_customer(f"CUST{i:04d}", phone=f"+1555011{i:04d}") for i in range(5)]
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {"schema_version": 1, "next_customer_sequence": 6, "customers": customers},
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_project_sequence": 1, "projects": []},
    )

    result = asyncio.run(flyer.customers(query="", segment="", _=None))
    assert len(result["customers"]) == 5
    assert result["total"] == 5
    assert result["truncated"] is False
    assert result["offset"] == 0
    assert result["limit"] == 300


def test_flyer_customers_offset_returns_next_page(tmp_path):
    """BUG-FLYER-QA-002 (P1 follow-up): rows beyond the first 300 must be
    reachable via offset, otherwise the cap silently drops customers."""
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    customers = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(305):
        c = _customer(f"CUST{i:04d}", phone=f"+1555012{i:04d}")
        c["created_at"] = base.isoformat()
        c["updated_at"] = (base + timedelta(minutes=i)).isoformat()
        customers.append(c)
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {"schema_version": 1, "next_customer_sequence": 306, "customers": customers},
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_project_sequence": 1, "projects": []},
    )

    page2 = asyncio.run(flyer.customers(query="", segment="", offset=300, limit=300, _=None))

    # Sorted desc by updated_at; the newest 300 went to page 1 (indices
    # 304..5), so page 2 contains the oldest 5 (indices 4..0).
    assert len(page2["customers"]) == 5
    assert page2["customers"][0]["customer_id"] == "CUST0004"
    assert page2["customers"][-1]["customer_id"] == "CUST0000"
    assert page2["total"] == 305
    assert page2["offset"] == 300
    assert page2["limit"] == 300
    assert page2["truncated"] is False  # no rows beyond this page


def test_flyer_customers_limit_clamped_to_300(tmp_path):
    """BUG-FLYER-QA-002 (P1 follow-up): limit > 300 must clamp to 300 to
    match the deployed `/projects` and `/guest-orders` ceiling."""
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"

    customers = [_customer(f"CUST{i:04d}", phone=f"+1555013{i:04d}") for i in range(305)]
    _write_json(
        settings.state_dir / "flyer" / "customers.json",
        {"schema_version": 1, "next_customer_sequence": 306, "customers": customers},
    )
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_project_sequence": 1, "projects": []},
    )

    result = asyncio.run(flyer.customers(query="", segment="", offset=0, limit=10000, _=None))
    assert len(result["customers"]) == 300
    assert result["limit"] == 300
    assert result["total"] == 305
    assert result["truncated"] is True


def test_campaign_target_parser_rejects_formula_and_dedupes():
    from app.routers.flyer import parse_campaign_targets

    parsed = parse_campaign_targets("+1 (732) 983-7841\n17329837841\n+1-847-915-5253")

    assert parsed["valid_targets"] == ["+17329837841", "+18479155253"]
    assert parsed["duplicate_count"] == 1

    with pytest.raises(ValueError, match="formula"):
        parse_campaign_targets("=cmd|/c calc.exe")


def test_campaign_send_dry_run_does_not_call_sender(monkeypatch):
    from app.routers import flyer

    calls: list[str] = []

    def fake_send(target: str, *, dry_run: bool) -> dict:
        calls.append(target)
        return {"ok": True, "target": target, "dry_run": dry_run}

    monkeypatch.setattr(flyer, "_send_campaign_target", fake_send)
    result = flyer.send_campaign_to_targets(
        ["+17329837841", "+18479155253"],
        dry_run=True,
        reason="preview",
    )

    assert calls == []
    assert result["sent"] == 0
    assert result["dry_run"] is True
    assert len(result["targets"]) == 2


def test_campaign_send_real_run_calls_sender(monkeypatch):
    from app.routers import flyer

    calls: list[tuple[str, bool]] = []

    def fake_send(target: str, *, dry_run: bool) -> dict:
        calls.append((target, dry_run))
        return {"ok": True, "target": target, "dry_run": dry_run}

    monkeypatch.setattr(flyer, "_send_campaign_target", fake_send)
    result = flyer.send_campaign_to_targets(
        ["+17329837841", "17329837841"],
        dry_run=False,
        reason="operator dashboard action",
    )

    assert calls == [("+17329837841", False)]
    assert result["sent"] == 1
    assert result["failed"] == 0
    assert result["dry_run"] is False


def test_campaign_sender_uses_allowlisted_cli_wrapper(monkeypatch):
    from app.routers import flyer

    calls = []

    class Result:
        returncode = 0
        stdout = '{"ok": true, "cta_message_id": "msg-1"}'
        stderr = ""

    def fake_run_cli(binary: str, args: list[str], *, timeout: float):
        calls.append((binary, args, timeout))
        return Result()

    monkeypatch.setattr(flyer, "run_cli", fake_run_cli)

    result = flyer._send_campaign_target("+17329837841", dry_run=False)

    assert result["ok"] is True
    assert calls == [
        (
            "/usr/local/bin/send-flyer-campaign",
            ["--jid", "17329837841@s.whatsapp.net"],
            60,
        )
    ]


# --- manual-queue triage / complete / break-glass (S2 P0-8b) ---

def _queued_project(
    project_id: str,
    *,
    phone: str = "+17329837841",
    reason_code: str = "source_edit_provider_unavailable",
    updated_at: str = "2026-05-18T20:00:00Z",
) -> dict:
    return {
        "project_id": project_id,
        "status": "manual_edit_required",
        "customer_phone": phone,
        "created_at": "2026-05-18T19:00:00Z",
        "updated_at": updated_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Authorized flyer/source artwork update. Replace phone number.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": phone},
        "manual_review": {
            "status": "queued",
            "reason": reason_code,
            "reason_code": reason_code,
            "detail": "legacy source-edit project queued before reason was tracked",
            "queued_at": updated_at,
        },
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
    }


def _seed_queue(tmp_path, projects: list[dict]) -> None:
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.cockpit_audit_log = tmp_path / "logs" / "audit.log"
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_sequence": len(projects) + 1, "projects": projects},
    )


def test_manual_queue_triage_groups_and_aggregates(tmp_path):
    from app.routers import flyer

    _seed_queue(tmp_path, [
        _queued_project("F0052", phone="+19045550104", reason_code="source_edit_provider_unavailable"),
        _queued_project("F0053", phone="+19045550104", reason_code="source_edit_provider_unavailable", updated_at="2026-05-18T19:30:00Z"),
        _queued_project("F0036", phone="+19803826497", reason_code="legacy_unknown", updated_at="2026-05-18T15:00:00Z"),
    ])

    summary = flyer.manual_queue_triage_action()

    assert summary["total"] == 3
    assert summary["reason_counts"] == {
        "source_edit_provider_unavailable": 2,
        "legacy_unknown": 1,
    }
    phones = [g["customer_phone"] for g in summary["groups"]]
    assert "+19045550104" in phones and "+19803826497" in phones


def _seed_operator_upload(tmp_path, filename: str, *, content: bytes = b"approved bytes") -> str:
    from app.routers import flyer
    upload_dir = flyer.get_settings().state_dir / "flyer" / "operator-uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    asset = upload_dir / filename
    asset.write_bytes(content)
    return str(asset)


def test_manual_queue_complete_attaches_operator_asset_and_backs_up(tmp_path):
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])
    asset_path = _seed_operator_upload(tmp_path, "operator_approved.png")

    result = flyer.manual_queue_complete_action(
        "F0052",
        asset_path=asset_path,
        reason="operator-approved designer asset",
    )

    assert result["ok"] is True
    assert result["status"] == "awaiting_final_approval"
    assert result["manual_status"] == "completed"
    assert result["operator_asset_ids"]
    # backup of pre-mutation state is recorded next to projects.json
    flyer_dir = flyer.get_settings().state_dir / "flyer"
    assert list(flyer_dir.glob("projects.json.pre-admin-*"))


def test_manual_queue_complete_rejects_asset_outside_upload_root(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])
    # Asset exists, is absolute, has a valid image extension — but lives
    # outside the allowed operator-uploads root.
    outside = tmp_path / "secrets" / "env.png"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"secret bytes")

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_complete_action(
            "F0052",
            asset_path=str(outside),
            reason="should fail — outside upload root",
        )
    assert ei.value.status_code == 422


def test_manual_queue_complete_rejects_disallowed_mime(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])
    asset_path = _seed_operator_upload(tmp_path, "secret.env", content=b"DB_PASSWORD=...")

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_complete_action(
            "F0052",
            asset_path=asset_path,
            reason="should fail — .env mime not in allowlist",
        )
    assert ei.value.status_code == 415


def test_manual_queue_complete_is_idempotent_failure(tmp_path):
    """Calling complete twice on the same project must 409 the second time."""
    from fastapi import HTTPException
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])
    asset_path = _seed_operator_upload(tmp_path, "first.png")

    first = flyer.manual_queue_complete_action(
        "F0052",
        asset_path=asset_path,
        reason="operator-approved first call",
    )
    assert first["ok"] is True

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_complete_action(
            "F0052",
            asset_path=asset_path,
            reason="operator-approved second call",
        )
    assert ei.value.status_code == 409


def test_manual_queue_complete_rejects_missing_asset(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_complete_action(
            "F0052",
            asset_path=str(tmp_path / "does-not-exist.png"),
            reason="operator-approved",
        )
    assert ei.value.status_code == 404


def test_manual_queue_complete_rejects_relative_asset_path(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_complete_action(
            "F0052",
            asset_path="relative/path.png",
            reason="operator-approved",
        )
    assert ei.value.status_code == 422


def test_manual_queue_complete_rejects_nonqueued_project(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    delivered = _queued_project("F0052", phone="+19045550104")
    delivered["status"] = "delivered"
    delivered["manual_review"]["status"] = "completed"
    _seed_queue(tmp_path, [delivered])
    asset_path = _seed_operator_upload(tmp_path, "approved.png")

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_complete_action(
            "F0052",
            asset_path=asset_path,
            reason="should fail",
        )
    assert ei.value.status_code == 409


def test_manual_queue_break_glass_marks_status_and_backs_up(tmp_path):
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])

    result = flyer.manual_queue_break_glass_action(
        "F0052",
        reason="customer received deliverable via designer email; logging for audit",
    )

    assert result["ok"] is True
    assert result["manual_status"] == "break_glass_sent"
    flyer_dir = flyer.get_settings().state_dir / "flyer"
    assert list(flyer_dir.glob("projects.json.pre-admin-*"))
    # project status stays manual_edit_required by design — operator is signalling
    # out-of-band resolution, not bypassing the state machine quietly.
    persisted = flyer.load_project_store().projects[0]
    assert persisted.status == "manual_edit_required"
    assert persisted.manual_review.status == "break_glass_sent"
    assert "customer received deliverable" in persisted.manual_review.break_glass_reason


def test_manual_queue_break_glass_rejects_unknown_project(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_break_glass_action("F9999", reason="not present")
    assert ei.value.status_code == 404


def test_manual_queue_break_glass_rejects_nonqueued_project(tmp_path):
    from fastapi import HTTPException
    from app.routers import flyer

    delivered = _queued_project("F0052", phone="+19045550104")
    delivered["status"] = "delivered"
    delivered["manual_review"]["status"] = "completed"
    _seed_queue(tmp_path, [delivered])

    with pytest.raises(HTTPException) as ei:
        flyer.manual_queue_break_glass_action("F0052", reason="too late, already delivered")
    assert ei.value.status_code == 409


def test_break_glass_row_is_dropped_from_triage_and_summary_counters(tmp_path):
    """Regression: after break-glass, the row must NOT remain in list_manual_queue or in
    build_summary's manual_edit_count / stuck_edit_count. The operator signal is
    "I resolved this out-of-band" — a recurring ghost in the queue/badges defeats that intent.
    """
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])

    pre = flyer.build_summary()
    assert pre["manual_edit_count"] == 1

    flyer.manual_queue_break_glass_action(
        "F0052",
        reason="customer received deliverable via designer email — out-of-band resolution",
    )

    post_triage = flyer.manual_queue_triage_action()
    assert post_triage["total"] == 0, "break-glass row must not surface in triage"
    post_summary = flyer.build_summary()
    assert post_summary["manual_edit_count"] == 0, "break-glass row must not count in manual_edit_count"
    assert post_summary["stuck_edit_count"] == 0, "break-glass row must not count in stuck_edit_count"


def _tiny_png_bytes(width: int = 2, height: int = 1) -> bytes:
    # Valid PNG header + IHDR. The metadata reader only needs the IHDR fields.
    import struct
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def test_manual_queue_detail_joins_decisions_and_cockpit_audit_logs(tmp_path):
    from app.routers import flyer

    _seed_queue(tmp_path, [_queued_project("F0052", phone="+19045550104")])
    settings = flyer.get_settings()
    settings.decisions_path = tmp_path / "logs" / "decisions.log"
    settings.cockpit_audit_log = tmp_path / "logs" / "cockpit-audit.log"
    _write_ndjson(
        settings.decisions_path,
        [
            {
                "ts": "2026-05-18T20:01:00Z",
                "type": "flyer_status_change",
                "project_id": "F0052",
                "from_status": "generating_concepts",
                "to_status": "manual_edit_required",
                "actor": "system",
                "reason": "visual qa failed",
            },
            {
                "ts": "2026-05-18T20:02:00Z",
                "type": "flyer_assets_delivered",
                "project_id": "F9999",
                "asset_ids": ["A0009"],
            },
        ],
    )
    _write_ndjson(
        settings.cockpit_audit_log,
        [
            {
                "ts": "2026-05-18T20:03:00+00:00",
                "event": "flyer.manual_queue.break_glass",
                "actor": "owner",
                "details": {"project_id": "F0052", "reason": "operator review"},
            }
        ],
    )

    detail = flyer.manual_queue_detail_action("F0052")

    events = [(row["source"], row["event"]) for row in detail["timeline"]]
    assert ("project_state", "project_created") in events
    assert ("decisions", "flyer_status_change") in events
    assert ("cockpit_audit", "flyer.manual_queue.break_glass") in events
    assert ("decisions", "flyer_assets_delivered") not in events
    timestamps = [row["ts"] for row in detail["timeline"]]
    assert timestamps == sorted(timestamps)
    status_change = next(row for row in detail["timeline"] if row["event"] == "flyer_status_change")
    assert "generating_concepts->manual_edit_required" in status_change["detail"]
    cockpit = next(row for row in detail["timeline"] if row["source"] == "cockpit_audit")
    assert "operator review" in cockpit["detail"]


def test_manual_queue_detail_exposes_final_asset_metadata_by_output_format(tmp_path, monkeypatch):
    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    flyer_root = settings.state_dir / "flyer"
    monkeypatch.setenv("FLYER_STATE_ROOT", str(flyer_root))
    image_path = flyer_root / "projects" / "F0052" / "final-whatsapp.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = _tiny_png_bytes(width=2, height=1)
    image_path.write_bytes(image_bytes)
    pdf_path = flyer_root / "projects" / "F0052" / "final-print.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% test\n")
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    pdf_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    project = _queued_project("F0052", phone="+19045550104")
    project["assets"] = [
        {
            "asset_id": "A0001",
            "kind": "final_whatsapp_image",
            "source": "rendered",
            "path": str(image_path),
            "mime_type": "image/png",
            "sha256": image_sha,
            "original_message_id": "msg-F0052",
            "received_at": "2026-05-18T20:10:00Z",
            "delivery_status": "sent",
            "outbound_message_id": "wamid.final.1",
            "delivered_at": "2026-05-18T20:15:00Z",
        },
        {
            "asset_id": "A0002",
            "kind": "final_printable_pdf",
            "source": "rendered",
            "path": str(pdf_path),
            "mime_type": "application/pdf",
            "sha256": pdf_sha,
            "original_message_id": "msg-F0052",
            "received_at": "2026-05-18T20:11:00Z",
            "delivery_status": "pending",
        },
        {
            "asset_id": "A0003",
            "kind": "final_instagram_post",
            "source": "rendered",
            "path": str(image_path),
            "mime_type": "image/png",
            "sha256": image_sha,
            "original_message_id": "msg-F0052",
            "received_at": "2026-05-18T20:12:00Z",
            "delivery_status": "pending",
        },
        {
            "asset_id": "A0004",
            "kind": "final_instagram_story",
            "source": "rendered",
            "path": str(image_path),
            "mime_type": "image/png",
            "sha256": image_sha,
            "original_message_id": "msg-F0052",
            "received_at": "2026-05-18T20:13:00Z",
            "delivery_status": "pending",
        },
    ]
    project["final_asset_ids"] = ["A0001", "A0002", "A0003", "A0004"]
    _seed_queue(tmp_path, [project])

    detail = flyer.manual_queue_detail_action("F0052")

    by_format = {asset["output_format"]: asset for asset in detail["final_assets"]}
    whatsapp = by_format["whatsapp_image"]
    assert whatsapp["asset_id"] == "A0001"
    assert whatsapp["sha256"] == image_sha
    assert whatsapp["sha256_short"] == image_sha[:16]
    assert whatsapp["width"] == 2
    assert whatsapp["height"] == 1
    assert whatsapp["size_bytes"] == len(image_bytes)
    assert whatsapp["delivery_status"] == "sent"
    assert whatsapp["source"] == "rendered"
    assert whatsapp["media_url"] == "/api/flyer/projects/F0052/assets/A0001"
    assert by_format["printable_pdf"]["sha256"] == pdf_sha
    assert by_format["printable_pdf"]["width"] is None
    assert by_format["printable_pdf"]["height"] is None
    assert by_format["instagram_post"]["asset_id"] == "A0003"
    assert by_format["instagram_story"]["asset_id"] == "A0004"
