from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc).isoformat()
_PHONE = "+17329837841"


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_ndjson(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )


def _lead(
    lead_id: str = "L0001",
    *,
    status: str = "AWAITING_OWNER_APPROVAL",
    on_hold: bool = False,
    approval_code: str | None = "#A3F2X",
    extracted: dict | None = None,
) -> dict:
    lead = {
        "lead_id": lead_id,
        "status": status,
        "customer_phone": _PHONE,
        "customer_name": "Priya",
        "raw_inquiry": "Need catering for 120 guests on the 12th",
        "original_message_id": f"msg-{lead_id}",
        "created_at": _NOW,
        "updated_at": _NOW,
        "extracted": extracted if extracted is not None else {
            "headcount": 120,
            "event_date": "2026-09-12",
            "event_type": "wedding",
            "venue": "Grand Ballroom",
            "service_style": "buffet",
            "veg_guest_count": 90,
            "nonveg_guest_count": 30,
            "dietary_restrictions": [],
            "menu_preferences": [],
            "notes": "",
        },
        "quote_text": "Quote for 120 guests",
        "quote_version": 1,
        "owner_approval_code": approval_code,
        "on_hold": on_hold,
        "hold_reason": "waiting on venue confirmation" if on_hold else None,
        "hold_set_at": _NOW if on_hold else None,
    }
    if status in ("NEW", "EXTRACTING", "QUALIFYING", "NOT_CATERING"):
        lead["quote_text"] = ""
    return lead


def _leads_store(*leads: dict) -> dict:
    return {"schema_version": 1, "leads": list(leads)}


def _menu_doc() -> dict:
    return {
        "version": 3,
        "updated_at": _NOW,
        "updated_by": "manual",
        "items": [
            {"name": "Paneer Tikka", "price_usd": 12.0, "category": "appetizer",
             "dietary_tags": ["veg"], "available": True, "notes": ""},
            {"name": "Goat Curry", "price_usd": 18.0, "category": "main",
             "dietary_tags": [], "available": False, "notes": "seasonal"},
        ],
        "notes": "48h lead time",
    }


def _pricebook_doc(*, placeholder: bool = False) -> dict:
    return {
        "schema_version": 1,
        "pricebook": {
            "version": 2,
            "effective_date": "2026-07-01",
            "updated_at": _NOW,
            "updated_by": "manual",
            "currency": "USD",
            "placeholder": placeholder,
            "per_person_packages": [
                {"id": "buffet-std", "name": "Standard Buffet",
                 "price_per_person_cents": 2500, "min_guests": 25,
                 "description": "", "dietary_profile": [], "included_sections": [],
                 "active": True},
            ],
            "fixed_fees": [
                {"id": "delivery", "name": "Delivery", "kind": "delivery",
                 "amount_cents": 7500, "per_unit": "flat", "active": True},
            ],
            "tax_rate_bps": 825,
            "approved_discounts": [
                {"id": "repeat-5", "name": "Repeat customer", "kind": "percent",
                 "value": 500, "max_amount_cents": None,
                 "requires_owner_code": True, "active": True},
            ],
            "item_price_overrides": {"Paneer Tikka": 1100},
            "notes": "",
        },
    }


def _seed(tmp_path, *, leads=None, menu=True, pricebook=True, placeholder=False):
    """Point every catering path at a fixture state dir and populate it."""
    from app.routers import catering

    settings = catering.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.decisions_path = tmp_path / "logs" / "decisions.log"
    settings.disabled_flag = settings.state_dir / "disabled.flag"
    settings.cockpit_audit_log = tmp_path / "logs" / "cockpit-audit.log"

    from app import audit as audit_mod

    audit_mod.settings.cockpit_audit_log = settings.cockpit_audit_log

    if leads is None:
        leads = _leads_store(_lead())
    if leads is not False:
        _write_json(settings.state_dir / "catering-leads.json", leads)
    if menu:
        _write_json(settings.state_dir / "catering-menu.json", _menu_doc())
    if pricebook:
        _write_json(
            settings.state_dir / "catering-pricebook.json",
            _pricebook_doc(placeholder=placeholder),
        )
    return settings


def _authed_client(monkeypatch=None, *, fresh: bool = True):
    """TestClient carrying an owner JWT. `fresh=False` mints a stale one so the
    require_fresh_otp step-up can be exercised."""
    from fastapi.testclient import TestClient
    from jose import jwt

    from app import auth as auth_mod
    from app.main import app

    issued_at = 1_700_000_000
    token = jwt.encode(
        {"sub": "+19045550100", "iat": issued_at, "exp": 1_800_000_000,
         "jti": "test", "auth_method": "pushover"},
        auth_mod.settings.jwt_secret,
        algorithm=auth_mod.settings.jwt_algo,
    )
    client = TestClient(app)
    client.cookies.set(auth_mod.settings.cookie_name, token)
    if monkeypatch is not None:
        monkeypatch.setattr(
            auth_mod, "_now",
            lambda: issued_at + (60 if fresh else 400),
        )
    return client


class _Result:
    """Stand-in for shell.CliResult — the agent scripts are never executed."""

    def __init__(self, returncode: int = 0, stdout: str = "ok", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = False


def _capture_run_cli(monkeypatch, result: _Result | None = None) -> list[dict]:
    from app.routers import catering

    calls: list[dict] = []

    def fake_run_cli(binary, args, *, timeout, user_args=None, stdin_data=None):
        calls.append({"binary": binary, "args": args, "timeout": timeout,
                      "user_args": user_args, "stdin_data": stdin_data})
        return result or _Result()

    monkeypatch.setattr(catering, "run_cli", fake_run_cli)
    return calls


# ── Auth ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/catering/dashboard"),
        ("get", "/catering/leads"),
        ("get", "/catering/leads/L0001"),
        ("get", "/catering/menu"),
        ("get", "/catering/pricebook"),
        ("get", "/catering/quote-preview"),
        ("get", "/catering/controls"),
        ("get", "/catering/followups"),
        ("post", "/catering/leads/L0001/decision"),
        ("post", "/catering/leads/L0001/hold"),
        ("post", "/catering/leads/L0001/amend-apply"),
        ("post", "/catering/pricebook/import"),
    ],
)
def test_every_endpoint_requires_auth(tmp_path, method, path):
    from fastapi.testclient import TestClient

    from app.main import app

    _seed(tmp_path)
    with TestClient(app) as client:
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401


@pytest.mark.parametrize(
    "path,body",
    [
        ("/catering/leads/L0001/decision", {"decision": "approve"}),
        ("/catering/leads/L0001/hold", {"on": True}),
        ("/catering/pricebook/import", {"document": {}}),
    ],
)
def test_sensitive_endpoints_reject_stale_otp(tmp_path, monkeypatch, path, body):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch, fresh=False)
    assert client.post(path, json=body).status_code == 403
    assert calls == [], "a stale-OTP request must never reach the agent script"


def test_amend_apply_needs_auth_but_not_fresh_otp(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch, fresh=False)
    response = client.post("/catering/leads/L0001/amend-apply", json={"mode": "amendment"})
    assert response.status_code == 200
    assert len(calls) == 1


# ── Reads against fixture state ─────────────────────────────────────────────
def test_dashboard_counts_and_readiness(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path, leads=_leads_store(
        _lead("L0001", status="AWAITING_OWNER_APPROVAL"),
        _lead("L0002", status="QUALIFYING"),
        _lead("L0003", status="SENT_TO_CUSTOMER"),
        _lead("L0004", status="CLOSED"),
        _lead("L0005", status="OWNER_REJECTED"),
        _lead("L0006", status="NEW", on_hold=True),
    ))
    settings.disabled_flag.parent.mkdir(parents=True, exist_ok=True)
    settings.disabled_flag.write_text("disabled", encoding="utf-8")

    client = _authed_client(monkeypatch)
    body = client.get("/catering/dashboard").json()

    assert body["counts"]["total_leads"] == 6
    assert body["counts"]["awaiting_owner_approval"] == 1
    assert body["counts"]["qualifying"] == 1
    assert body["counts"]["proposals_sent"] == 1
    assert body["counts"]["booked_this_month"] == 1
    assert body["counts"]["lost_this_month"] == 1
    assert body["counts"]["on_hold"] == 1
    assert body["readiness"]["kill_switch_engaged"] is True
    assert body["readiness"]["pricebook_present"] is True
    assert body["readiness"]["pricebook_placeholder"] is False
    assert body["readiness"]["menu_version"] == 3
    assert body["degraded"] == []


def test_dashboard_reports_placeholder_pricebook(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, placeholder=True)
    client = _authed_client(monkeypatch)
    readiness = client.get("/catering/dashboard").json()["readiness"]
    assert readiness["pricebook_present"] is True
    assert readiness["pricebook_placeholder"] is True


def test_dashboard_on_empty_state_dir(tmp_path, monkeypatch):
    """No catering state at all is a legitimate pre-pilot condition, not a 500."""
    pytest.importorskip("jose")
    _seed(tmp_path, leads=False, menu=False, pricebook=False)
    client = _authed_client(monkeypatch)
    body = client.get("/catering/dashboard").json()
    assert body["counts"]["total_leads"] == 0
    assert body["readiness"]["pricebook_present"] is False
    assert body["readiness"]["menu_present"] is False
    assert body["degraded"] == []


def test_lead_list_masks_phone_and_computes_qualification(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, leads=_leads_store(
        _lead("L0001"),
        _lead("L0002", status="QUALIFYING", extracted={"headcount": 40}),
    ))
    client = _authed_client(monkeypatch)
    body = client.get("/catering/leads").json()

    rows = {row["lead_id"]: row for row in body["leads"]}
    assert _PHONE not in json.dumps(body), "raw phone must never reach a list view"
    assert rows["L0001"]["customer_phone_masked"].startswith("+17")
    assert rows["L0001"]["customer_phone_masked"].endswith("41")
    assert "•" in rows["L0001"]["customer_phone_masked"]

    assert rows["L0001"]["qualification"]["complete"] is True
    assert rows["L0001"]["qualification"]["missing"] == []

    partial = rows["L0002"]["qualification"]
    assert partial["complete"] is False
    assert "event_date" in partial["missing"]
    assert partial["missing_labels"], "gap labels feed the owner-facing checklist"
    assert "Waiting on customer" in rows["L0002"]["next_action"]
    assert body["status_counts"] == {"AWAITING_OWNER_APPROVAL": 1, "QUALIFYING": 1}


def test_lead_list_status_filter(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, leads=_leads_store(
        _lead("L0001"), _lead("L0002", status="QUALIFYING"),
    ))
    client = _authed_client(monkeypatch)
    body = client.get("/catering/leads", params={"status": "QUALIFYING"}).json()
    assert [row["lead_id"] for row in body["leads"]] == ["L0002"]
    # Counts stay whole-store so the filter chips show real totals.
    assert body["status_counts"]["AWAITING_OWNER_APPROVAL"] == 1


def test_held_lead_next_action_outranks_status(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, leads=_leads_store(_lead("L0001", on_hold=True)))
    client = _authed_client(monkeypatch)
    row = client.get("/catering/leads").json()["leads"][0]
    assert row["on_hold"] is True
    assert "On hold" in row["next_action"]


def test_lead_detail_assembles_ledger_amendments_and_timeline(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path)
    _write_json(settings.state_dir / "catering-quote-ledger.json", {
        "schema_version": 1,
        "next_seq": 3,
        "records": [
            {"ledger_entry_id": "Q0001", "lead_id": "L0001", "version": 1,
             "quote_text": "v1", "quote_total_usd": 1000,
             "selected_items": [{"name": "Paneer Tikka", "qty": 10, "price_usd": 12}],
             "source": "customer_finalize", "created_at": _NOW,
             "menu_version": 3, "pricebook_version": 2, "price_status": "priced"},
            {"ledger_entry_id": "Q0002", "lead_id": "L0001", "version": 2,
             "quote_text": "v2", "quote_total_usd": 1240,
             "selected_items": [
                 {"name": "Paneer Tikka", "qty": 10, "price_usd": 12},
                 {"name": "Goat Curry", "qty": 5, "price_usd": 18},
             ],
             "source": "owner_edit", "created_at": _NOW,
             "menu_version": 3, "pricebook_version": 2, "price_status": "priced"},
        ],
    })
    _write_json(settings.state_dir / "catering-amendments.json", {
        "schema_version": 1,
        "next_seq": 2,
        "records": [
            {"amendment_id": "A0001", "lead_id": "L0001", "raw_text": "make it 130 guests",
             "captured_at": _NOW, "source": "f7_branch_b", "source_transport": "whatsapp",
             "status": "captured", "raw_text_truncated": False},
            {"amendment_id": "A0002", "lead_id": "L0001", "raw_text": "add dessert",
             "captured_at": _NOW, "source": "f7_branch_b", "source_transport": "whatsapp",
             "status": "applied", "raw_text_truncated": False, "applied_at": _NOW},
        ],
    })
    _write_ndjson(settings.decisions_path, [
        {"ts": "2026-07-30T09:00:00+00:00", "type": "catering_lead_created", "lead_id": "L0001"},
        {"ts": "2026-07-30T10:00:00+00:00", "type": "catering_lead_status_change",
         "lead_id": "L0001", "from_status": "NEW", "to_status": "AWAITING_OWNER_APPROVAL"},
        {"ts": "2026-07-30T11:00:00+00:00", "type": "flyer_status_change", "project_id": "F0001"},
        {"ts": "2026-07-30T12:00:00+00:00", "type": "catering_lead_created", "lead_id": "L0999"},
    ])

    client = _authed_client(monkeypatch)
    body = client.get("/catering/leads/L0001").json()

    versions = body["quote_versions"]
    assert [v["version"] for v in versions] == [2, 1], "newest version first"
    assert versions[0]["items_added"] == ["Goat Curry"]
    assert versions[0]["total_delta_usd"] == 240
    assert "total +$240" in versions[0]["diff_summary"]
    assert versions[1]["diff_summary"] == "", "first version has nothing to diff against"

    assert [a["amendment_id"] for a in body["amendments"]] == ["A0002", "A0001"]
    assert body["amendments"][0]["applied"] is True
    assert body["amendments"][1]["applied"] is False

    events = [row["event"] for row in body["timeline"]]
    assert events == ["catering_lead_status_change", "catering_lead_created"], (
        "only this lead's catering rows, newest first"
    )
    assert body["customer_phone_masked"].count("•") > 0
    assert body["degraded"] == []


def test_lead_detail_404_for_unknown_lead(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    client = _authed_client(monkeypatch)
    assert client.get("/catering/leads/L9999").status_code == 404


@pytest.mark.parametrize("lead_id", ["../../etc/passwd", "L0001/../L0002", "L1", "%2e%2e", "L0001;rm"])
def test_lead_id_pattern_rejects_traversal_and_injection(tmp_path, monkeypatch, lead_id):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)
    assert client.get(f"/catering/leads/{lead_id}").status_code in (404, 422)
    assert client.post(f"/catering/leads/{lead_id}/hold", json={"on": True}).status_code in (404, 422)
    assert calls == [], "a malformed lead_id must never reach argv"


def test_menu_and_pricebook_reads(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    client = _authed_client(monkeypatch)

    menu = client.get("/catering/menu").json()
    assert menu["available"] is True
    assert menu["version"] == 3
    assert [i["name"] for i in menu["items"]] == ["Paneer Tikka", "Goat Curry"]
    assert menu["items"][1]["available"] is False

    pricebook = client.get("/catering/pricebook").json()
    assert pricebook["available"] is True
    assert pricebook["version"] == 2
    assert pricebook["tax_rate_bps"] == 825
    assert pricebook["per_person_packages"][0]["id"] == "buffet-std"
    assert pricebook["fixed_fees"][0]["amount_cents"] == 7500
    assert pricebook["approved_discounts"][0]["value"] == 500
    assert pricebook["item_price_override_count"] == 1


def test_menu_and_pricebook_absent_report_unavailable(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, menu=False, pricebook=False)
    client = _authed_client(monkeypatch)
    assert client.get("/catering/menu").json() == {
        "available": False, "version": None, "updated_at": None, "updated_by": "",
        "notes": "", "items": [], "degraded": None,
    }
    assert client.get("/catering/pricebook").json()["available"] is False


def test_quote_preview_prices_from_lead_headcount(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    client = _authed_client(monkeypatch)
    body = client.get(
        "/catering/quote-preview",
        params={"lead_id": "L0001", "package_id": "buffet-std"},
    ).json()

    assert body["available"] is True
    assert body["guest_count"] == 120
    # 120 × $25.00 = $3,000.00, plus the $75 flat delivery fee.
    assert body["per_person_subtotal_cents"] == 300_000
    assert body["fees_subtotal_cents"] == 7_500
    assert body["subtotal_cents"] == 307_500
    assert body["price_status"] == "exact"
    assert body["deliverable"] is True
    assert body["total_cents"] > body["subtotal_cents"], "tax applies"


def test_quote_preview_placeholder_pricebook_is_not_deliverable(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, placeholder=True)
    client = _authed_client(monkeypatch)
    body = client.get(
        "/catering/quote-preview",
        params={"guest_count": 50, "package_id": "buffet-std"},
    ).json()
    assert body["price_status"] == "pending_owner_review"
    assert body["deliverable"] is False


def test_quote_preview_without_guest_count_is_explicit(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, leads=_leads_store(_lead("L0001", status="QUALIFYING", extracted={})))
    client = _authed_client(monkeypatch)
    body = client.get("/catering/quote-preview", params={"lead_id": "L0001"}).json()
    assert body["available"] is False
    assert "guest count unknown" in body["unavailable_reason"]


def test_quote_preview_without_pricebook_is_explicit(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path, pricebook=False)
    client = _authed_client(monkeypatch)
    body = client.get("/catering/quote-preview", params={"guest_count": 50}).json()
    assert body["available"] is False
    assert "pricebook" in body["unavailable_reason"]


def test_controls_snapshot(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path, leads=_leads_store(
        _lead("L0001"), _lead("L0002", status="QUALIFYING", on_hold=True),
    ))
    _write_json(settings.state_dir / "catering-automation-control.json", {
        "conversations": {
            "17329837841": {"mode": "opted_out", "since_ts": _NOW,
                            "actor": "customer", "reason": "STOP"},
            "19995551234": {"mode": "paused", "since_ts": _NOW, "actor": "owner",
                            "reason": "owner pause",
                            "pause_expires_ts": "2020-01-01T00:00:00+00:00"},
        },
    })
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "+17329837841")
    monkeypatch.setenv("HERMES_ENV_PATH", str(tmp_path / "absent-hermes.env"))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(tmp_path / "absent-agent.env"))

    client = _authed_client(monkeypatch)
    body = client.get("/catering/controls").json()

    assert body["conversations_available"] is True
    assert len(body["conversations"]) == 2
    assert _PHONE not in json.dumps(body), "control rows carry hashes, not identifiers"
    assert "17329837841" not in json.dumps(body)
    expired = [c for c in body["conversations"] if c["stored_mode"] == "paused"][0]
    assert expired["effective_mode"] == "active"
    assert expired["expired"] is True, "a lapsed pause window reads as active"
    assert body["suppressed_count"] == 1

    assert body["kernel_flags"]["CATERING_AUTOMATION_CONTROL_ENABLED"]["value"] == "1"
    assert body["kernel_allowlists"]["CATERING_AUTOMATION_CONTROL_ALLOWLIST"]["mode"] == "scoped"
    # A flag the cockpit cannot read anywhere is reported unknown, never "off".
    unread = body["containment_flags"]["FRONT_BRAIN_OUTBOUND_ENFORCE"]
    assert unread["known"] is False and unread["source"] is None

    assert [lead["lead_id"] for lead in body["held_leads"]] == ["L0002"]


def test_controls_tolerate_absent_automation_store(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    client = _authed_client(monkeypatch)
    body = client.get("/catering/controls").json()
    assert body["conversations"] == []
    assert body["degraded"] == []


# ── Tolerant parsing (M3 / M5 land in parallel) ─────────────────────────────
def test_followups_absent_is_an_empty_state_not_an_error(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    client = _authed_client(monkeypatch)
    body = client.get("/catering/followups").json()
    assert body["available"] is False
    assert body["followups"] == []
    assert "not present" in body["unavailable_reason"]
    assert client.get("/catering/leads/L0001").json()["followups_available"] is False


def test_followups_parsed_tolerantly_with_unknown_keys(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path)
    _write_json(settings.state_dir / "catering-followups.json", {
        "schema_version": 1,
        "followups": [
            {"lead_id": "L0001", "followup_type": "thank_you",
             "due_at": "2020-01-01T00:00:00+00:00", "status": "scheduled",
             "approval_code": "#B7K2M", "some_future_m5_field": {"nested": True}},
            {"lead_id": "L0001", "followup_type": "feedback",
             "due_at": "2999-01-01T00:00:00+00:00", "status": "scheduled"},
        ],
    })
    client = _authed_client(monkeypatch)
    body = client.get("/catering/followups").json()
    assert body["available"] is True
    assert body["due_count"] == 1, "only the past-due one counts"
    assert body["followups"][0]["raw"]["some_future_m5_field"] == {"nested": True}
    assert client.get("/catering/leads/L0001").json()["followups_available"] is True


def test_unknown_lead_fields_do_not_500(tmp_path, monkeypatch):
    """M3 may add fields to CateringLead. A read that parsed leads through the
    strict store model would break the whole studio on the next merge."""
    pytest.importorskip("jose")
    lead = _lead("L0001")
    lead["m3_proposal_bundle"] = {"options": [1, 2, 3]}
    lead["extracted"]["m3_new_field"] = "value"
    _seed(tmp_path, leads=_leads_store(lead))
    client = _authed_client(monkeypatch)
    assert client.get("/catering/leads").status_code == 200
    detail = client.get("/catering/leads/L0001").json()
    assert detail["extracted"]["m3_new_field"] == "value"


def test_malformed_leads_store_degrades_not_500(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path)
    (settings.state_dir / "catering-leads.json").write_text("{not json", encoding="utf-8")
    client = _authed_client(monkeypatch)
    listing = client.get("/catering/leads").json()
    assert listing["available"] is False
    assert "malformed" in listing["degraded"]
    assert client.get("/catering/dashboard").json()["degraded"], "dashboard says so too"


def test_malformed_quote_ledger_degrades_detail_view(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path)
    (settings.state_dir / "catering-quote-ledger.json").write_text("nope", encoding="utf-8")
    client = _authed_client(monkeypatch)
    body = client.get("/catering/leads/L0001").json()
    assert body["quote_ledger_available"] is False
    assert body["quote_versions"] == []
    assert any("quote ledger" in d for d in body["degraded"])


# ── Owner actions: exact argv + stdin handed to the agent scripts ───────────
def test_decision_approve_pipes_quote_text_via_stdin(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)

    response = client.post("/catering/leads/L0001/decision", json={
        "decision": "approve", "quote_text": "Your quote: $3,000 for 120 guests",
    })
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == [{
        "binary": "/usr/local/bin/apply-catering-owner-decision",
        "args": ["--code", "#A3F2X", "--decision", "approve",
                 "--sender-role", "owner", "--quote-text-stdin"],
        "timeout": 60,
        "user_args": None,
        "stdin_data": "Your quote: $3,000 for 120 guests",
    }]


def test_decision_approve_without_text_renders_from_lead_state(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)
    client.post("/catering/leads/L0001/decision", json={"decision": "approve"})
    assert calls[0]["args"][-1] == "--quote-from-lead-state"
    assert calls[0]["stdin_data"] is None


def test_decision_edit_passes_edit_text(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)
    response = client.post("/catering/leads/L0001/decision", json={
        "decision": "edit", "edit_text": "drop the goat curry", "reason": "customer is veg",
    })
    assert response.status_code == 200
    assert calls[0]["args"] == [
        "--code", "#A3F2X", "--decision", "edit", "--sender-role", "owner",
        "--edit-text", "drop the goat curry", "--reason", "customer is veg",
    ]


def test_decision_edit_requires_text(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)
    response = client.post("/catering/leads/L0001/decision",
                           json={"decision": "edit", "edit_text": "   "})
    assert response.status_code == 422
    assert calls == []


def test_decision_refused_when_lead_has_no_approval_code(tmp_path, monkeypatch):
    """The code comes from lead state, never from the client — a lead without
    one has no owner decision to apply."""
    pytest.importorskip("jose")
    _seed(tmp_path, leads=_leads_store(
        _lead("L0001", status="QUALIFYING", approval_code=None),
    ))
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)
    response = client.post("/catering/leads/L0001/decision", json={"decision": "approve"})
    assert response.status_code == 409
    assert calls == []


def test_decision_surfaces_script_failure(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    _capture_run_cli(monkeypatch, _Result(returncode=14, stdout="", stderr="lead is on hold"))
    client = _authed_client(monkeypatch)
    response = client.post("/catering/leads/L0001/decision", json={"decision": "reject"})
    assert response.status_code == 409
    assert "lead is on hold" in response.json()["detail"]


def test_hold_on_and_off_argv(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)

    client.post("/catering/leads/L0001/hold", json={"on": True, "reason": "venue unconfirmed"})
    client.post("/catering/leads/L0001/hold", json={"on": False})

    assert calls[0]["binary"] == "/usr/local/bin/set-catering-lead-hold"
    assert calls[0]["args"] == [
        "--lead-id", "L0001", "--on", "--actor", "operator",
        "--reason", "venue unconfirmed",
    ]
    assert calls[1]["args"] == ["--lead-id", "L0001", "--off", "--actor", "operator"]


def test_amend_apply_argv(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)

    client.post("/catering/leads/L0001/amend-apply", json={"mode": "amendment"})
    client.post("/catering/leads/L0001/amend-apply",
                json={"mode": "answer", "answer_text": "Grand Ballroom"})

    assert calls[0]["binary"] == "/usr/local/bin/amend-catering-lead"
    assert calls[0]["args"] == ["--lead-id", "L0001", "--mode", "amendment"]
    assert calls[1]["args"] == [
        "--lead-id", "L0001", "--mode", "answer", "--answer-text", "Grand Ballroom",
    ]


def test_pricebook_import_validates_before_shelling_out(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    calls = _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)

    response = client.post("/catering/pricebook/import", json={
        "document": {"version": 1, "currency": "INR", "effective_date": "2026-07-01",
                     "updated_at": _NOW},
    })
    assert response.status_code == 422
    assert "USD-only" in response.json()["detail"]
    assert calls == [], "an invalid paste never reaches the import script"


def test_pricebook_import_writes_temp_file_and_cleans_up(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed(tmp_path)
    seen: dict = {}

    from app.routers import catering

    def fake_run_cli(binary, args, *, timeout, user_args=None, stdin_data=None):
        seen["binary"] = binary
        seen["args"] = args
        # The script reads the file, so it must exist for the duration of the call.
        path = args[args.index("--file") + 1]
        seen["document"] = json.loads(open(path, encoding="utf-8").read())
        seen["path"] = path
        return _Result()

    monkeypatch.setattr(catering, "run_cli", fake_run_cli)
    client = _authed_client(monkeypatch)

    document = _pricebook_doc()
    response = client.post("/catering/pricebook/import",
                           json={"document": document, "dry_run": True})

    assert response.status_code == 200
    assert seen["binary"] == "/usr/local/bin/import-catering-pricebook"
    assert seen["args"][0] == "--file"
    assert seen["args"][2:] == ["--sender-role", "owner", "--updated-by", "cockpit", "--dry-run"]
    assert seen["document"] == document
    from pathlib import Path as _Path

    assert not _Path(seen["path"]).exists(), "temp pricebook file is removed after the call"


def test_missing_agent_script_is_503(tmp_path, monkeypatch):
    """run_cli raises ValueError when the binary is absent — that means the
    cockpit is deployed where the agent is not, which is a 503 not a 500."""
    pytest.importorskip("jose")
    _seed(tmp_path)
    from app.routers import catering

    def boom(binary, args, *, timeout, user_args=None, stdin_data=None):
        raise ValueError(f"binary missing on disk: {binary!r}")

    monkeypatch.setattr(catering, "run_cli", boom)
    client = _authed_client(monkeypatch)
    response = client.post("/catering/leads/L0001/hold", json={"on": True})
    assert response.status_code == 503


def test_owner_actions_are_audited(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    settings = _seed(tmp_path)
    _capture_run_cli(monkeypatch)
    client = _authed_client(monkeypatch)
    client.post("/catering/leads/L0001/hold", json={"on": True, "reason": "venue"})

    rows = [
        json.loads(line)
        for line in settings.cockpit_audit_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    hold_rows = [r for r in rows if r.get("event") == "catering.hold"]
    assert len(hold_rows) == 1
    assert hold_rows[0]["details"]["lead_id"] == "L0001"
    assert hold_rows[0]["details"]["returncode"] == 0
