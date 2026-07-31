"""M7 companion — the Studio read model over a FINISHED catering lifecycle.

`tests/test_catering_studio_e2e.py` (root suite) drives the whole lifecycle
through the real cf-router + catering scripts and ends with: one BOOKED lead
carrying a two-version quote ledger and a released automation-control record,
plus two deliberately-seeded leads. THIS file points the cockpit at a state dir
in that same end shape and asserts the Studio reports it faithfully.

WHY IT IS A SEPARATE FILE (and not part of the root suite): `web/backend` is an
independently-installed package with its own rootdir, conftest and dependency set
(fastapi / httpx / jose). The root `send-path-ci` job installs none of those and
its path filter excludes `web/**`, so a root-suite import of the FastAPI app
would either fail or — worse — be wrapped in a skip and hide its own result. The
split keeps each suite runnable by the job that actually installs its deps.

The end-state fixture is built by the REAL producers wherever a producer exists:
the quote-ledger versions come from `catering_quote_ledger.append_version` and
the control record from `automation_control.set_mode`, so this file cannot drift
from those schemas without failing. The stores the catering SCRIPTS own (leads,
follow-ups, decisions log) are written directly in their finished shape.

Deterministic: `catering._now` is pinned, so the month-scoped dashboard counters
do not change answer depending on the day CI runs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

# The end state the root-suite transcript leaves behind.
_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()
_BOOKED_LEAD = "L9003"
_BOOKED_PHONE = "+15550001111"
_BOOKED_CODE = "#HFR58"
_BOOKED_TOTAL_USD = 5104
_SENT_LEAD = "L9001"
_FINALIZED_LEAD = "L9002"
_MENU_VERSION = 2
_PRICEBOOK_VERSION = 4


def _fs_owner_group() -> tuple[str, str]:
    """Ids the ledger's POSIX fs check must expect for a runner-owned tmp dir."""
    if os.name == "posix":
        import grp
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    return ("shift-agent", "shift-agent")


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _selected_items() -> list[dict]:
    return [
        {"name": "Idly (3 PCS)", "qty": 12, "price_usd": 6},
        {"name": "Masala Dosa", "qty": 8, "price_usd": 10},
    ]


def _booked_lead() -> dict:
    return {
        "lead_id": _BOOKED_LEAD,
        "status": "BOOKED",
        "customer_phone": _BOOKED_PHONE,
        "customer_name": None,
        "raw_inquiry": "I need catering for a wedding reception for about 150 guests",
        "original_message_id": "wamid.M7.01",
        "created_at": _NOW_ISO,
        "updated_at": _NOW_ISO,
        "extracted": {
            "headcount": 160, "event_date": "2026-09-12", "event_time": "18:00",
            "event_type": "wedding", "service_style": "buffet",
            "dietary_restrictions": ["veg"], "menu_preferences": [], "notes": "",
        },
        "selected_items": _selected_items(),
        "quote_total_usd": _BOOKED_TOTAL_USD,
        "quote_text": "Quote for 160 guests. This quote is valid until 2026-08-14.",
        "owner_approval_code": _BOOKED_CODE,
        "customer_acceptance": "accepted",
        "acceptance_at": _NOW_ISO,
        "acceptance_message_id": "wamid.M7.15",
        "on_hold": False,
        # Deliberately absent, matching the transcript's end state: no catering
        # script ever writes CateringLead.quote_version — the ledger is the source
        # of truth for versions. See the FINDING in the lead-list cell.
    }


def _other_leads() -> list[dict]:
    base = {
        "customer_name": "Seeded", "created_at": _NOW_ISO, "updated_at": _NOW_ISO,
        "quote_text": "Seed quote", "on_hold": False,
        "extracted": {"headcount": 40, "event_date": "2026-09-30",
                      "dietary_restrictions": [], "menu_preferences": [], "notes": ""},
    }
    return [
        {**base, "lead_id": _SENT_LEAD, "status": "SENT_TO_CUSTOMER",
         "customer_phone": "+15550002222", "original_message_id": "wamid.SEED.OTHER",
         "owner_approval_code": "#QRSTV"},
        {**base, "lead_id": _FINALIZED_LEAD, "status": "CUSTOMER_FINALIZED",
         "customer_phone": "+15550003333", "original_message_id": "wamid.SEED.HELD",
         "owner_approval_code": "#WXYZ2"},
    ]


def _followups_store() -> dict:
    """The transcript's follow-ups: one owner-approved chase that went out, one
    suppressed by the customer's opt-out."""
    return {
        "schema_version": 1,
        "next_sequence": 3,
        "followups": [
            {"followup_id": "FU0001", "lead_id": _BOOKED_LEAD,
             "followup_type": "proposal_unanswered", "due_at": _NOW_ISO,
             "created_at": _NOW_ISO, "created_by": "system", "status": "approved_sent",
             "approval_code": "#JKMNP",
             "message_template_key": "catering_followup_proposal_unanswered",
             "rendered_message": "Following up on the catering quote we shared.",
             "sent_message_id": "wamid.OUT.0042", "attempt_count": 1},
            {"followup_id": "FU0002", "lead_id": _BOOKED_LEAD,
             "followup_type": "owner_reminder", "due_at": _NOW_ISO,
             "created_at": _NOW_ISO, "created_by": "owner", "status": "suppressed",
             "suppressed_reason": "automation_suppressed",
             "message_template_key": "catering_followup_owner_reminder",
             "attempt_count": 0},
        ],
    }


def _decisions_rows() -> list[dict]:
    """The lifecycle rows the Studio timeline renders for the booked lead. Each
    gets its own minute — the timeline sorts by `ts`, so identical stamps would
    make the newest-first assertion depend on file order rather than on the sort."""
    events = [
        ("catering_lead_created", {"customer_phone": _BOOKED_PHONE,
                                   "original_message_id": "wamid.M7.01"}),
        ("catering_lead_status_change", {"from_status": "NEW", "to_status": "QUALIFYING",
                                         "actor": "system", "reason": "qualification_incomplete"}),
        ("catering_amendment_applied", {"amendment_id": "A0001",
                                        "fields_changed": ["headcount"], "quote_version": 0}),
        ("catering_proposals_generated", {"proposal_set_id": "CPS-1", "option_count": 2,
                                          "outbound_message_id": "wamid.OUT.0010",
                                          "outcome": "sent"}),
        ("catering_quote_sent", {"customer_phone": _BOOKED_PHONE,
                                 "outbound_message_id": "wamid.OUT.0020", "outcome": "sent"}),
        ("catering_lead_acceptance_recorded", {"outcome": "accepted",
                                               "from_status": "SENT_TO_CUSTOMER",
                                               "to_status": "BOOKED",
                                               "customer_message_id": "wamid.M7.15"}),
    ]
    return [
        {"type": t, "ts": (_NOW - timedelta(minutes=len(events) - i)).isoformat(),
         "lead_id": _BOOKED_LEAD, **fields}
        for i, (t, fields) in enumerate(events)
    ]


def _seed_finished_lifecycle(tmp_path, monkeypatch):
    """Point the cockpit at a state dir in the transcript's END shape."""
    from app.routers import catering

    settings = catering.get_settings()
    settings.state_dir = tmp_path / "state"
    settings.decisions_path = tmp_path / "logs" / "decisions.log"
    settings.disabled_flag = settings.state_dir / "disabled.flag"
    settings.cockpit_audit_log = tmp_path / "logs" / "cockpit-audit.log"

    from app import audit as audit_mod

    audit_mod.settings.cockpit_audit_log = settings.cockpit_audit_log
    # Pin the clock: booked_this_month / lost_this_month / followups_due are all
    # month- or now-scoped, so an unpinned clock makes this suite date-dependent.
    monkeypatch.setattr(catering, "_now", lambda: _NOW)

    _write_json(settings.state_dir / "catering-leads.json",
                {"schema_version": 1, "leads": [*_other_leads(), _booked_lead()]})
    _write_json(settings.state_dir / "catering-menu.json", {
        "version": _MENU_VERSION, "updated_at": _NOW_ISO, "updated_by": "photo-ocr",
        "items": [{"name": "Idly (3 PCS)", "price_usd": 5.99, "category": "appetizer",
                   "dietary_tags": ["veg"], "available": True, "notes": ""},
                  {"name": "Masala Dosa", "price_usd": 9.99, "category": "main",
                   "dietary_tags": ["veg"], "available": True, "notes": ""}],
        "notes": "",
    })
    _write_json(settings.state_dir / "catering-pricebook.json", {
        "schema_version": 1,
        "pricebook": {
            "version": _PRICEBOOK_VERSION, "effective_date": "2026-07-01",
            "updated_at": _NOW_ISO, "updated_by": "import", "currency": "USD",
            "placeholder": False,
            "per_person_packages": [
                {"id": "veg-premium", "name": "Premium Veg Buffet",
                 "price_per_person_cents": 2900, "min_guests": 40, "description": "",
                 "dietary_profile": ["veg"], "included_sections": [], "active": True},
            ],
            "fixed_fees": [
                {"id": "delivery", "name": "Delivery", "kind": "delivery",
                 "amount_cents": 2500, "per_unit": "flat", "active": True},
            ],
            "tax_rate_bps": 825, "approved_discounts": [],
            "item_price_overrides": {}, "notes": "",
        },
    })
    _write_json(settings.state_dir / "catering-followups.json", _followups_store())
    settings.decisions_path.parent.mkdir(parents=True, exist_ok=True)
    settings.decisions_path.write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in _decisions_rows()) + "\n",
        encoding="utf-8")

    _seed_quote_ledger(settings)
    _seed_control_record(settings, monkeypatch)
    return settings


def _seed_quote_ledger(settings) -> None:
    """Two committed versions, written by the REAL ledger writer — so the shape
    the Studio reads can never drift from the shape production writes."""
    import catering_quote_ledger as ledger

    ledger_path = settings.state_dir / "catering-quote-ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    owner, group = _fs_owner_group()
    common = dict(lead_id=_BOOKED_LEAD, approval_code=_BOOKED_CODE,
                  menu_version=_MENU_VERSION, pricebook_version=_PRICEBOOK_VERSION,
                  price_status="exact", data_path=ledger_path,
                  expected_owner=owner, expected_group=group, emit_audit=False)
    first = ledger.append_version(
        quote_text="Indicative owner-side draft (2 options, 160 guests). NOT SENT.",
        quote_total_usd=_BOOKED_TOTAL_USD, selected_items=_selected_items()[:1],
        source="initial_draft", source_message_id="CPS-1", **common)
    assert first.ok, f"ledger seed v1 failed: {first.reason}"
    second = ledger.append_version(
        quote_text="Revised: buffet upgraded to include two extra desserts.",
        quote_total_usd=_BOOKED_TOTAL_USD, selected_items=_selected_items(),
        source="owner_edit", source_message_id="wamid.M7.07", **common)
    assert second.ok, f"ledger seed v2 failed: {second.reason}"


def _seed_control_record(settings, monkeypatch) -> None:
    """The customer opted out, the owner took over, then released — the record
    the Studio's control chip renders. Written by the REAL kernel."""
    import automation_control as ac

    path = settings.state_dir / "catering-automation-control.json"
    monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_STATE_PATH", str(path))
    ac._LAST_KNOWN_MODE.clear()
    ac.set_mode(_BOOKED_PHONE, "opted_out", actor="customer", reason="customer_stop",
                logical_turn_id="wamid.M7.09")
    ac.set_mode(_BOOKED_PHONE, "takeover", actor="owner", reason="owner_takeover",
                logical_turn_id="wamid.M7.11")
    ac.set_mode(_BOOKED_PHONE, "active", actor="owner", reason="owner_release",
                logical_turn_id="wamid.M7.13")
    ac._LAST_KNOWN_MODE.clear()


def _authed_client(monkeypatch):
    """TestClient carrying an owner JWT (mirrors tests/test_catering_router.py)."""
    from fastapi.testclient import TestClient
    from jose import jwt

    from app import auth as auth_mod
    from app.main import app

    issued_at = 1_700_000_000
    token = jwt.encode(
        {"sub": "+19045550100", "iat": issued_at, "exp": 1_800_000_000,
         "jti": "m7-readmodel", "auth_method": "pushover"},
        auth_mod.settings.jwt_secret, algorithm=auth_mod.settings.jwt_algo)
    client = TestClient(app)
    client.cookies.set(auth_mod.settings.cookie_name, token)
    monkeypatch.setattr(auth_mod, "_now", lambda: issued_at + 60)
    return client


# ── dashboard ────────────────────────────────────────────────────────────────
def test_dashboard_reflects_the_finished_lifecycle(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed_finished_lifecycle(tmp_path, monkeypatch)
    body = _authed_client(monkeypatch).get("/catering/dashboard").json()

    counts = body["counts"]
    assert counts["total_leads"] == 3, counts
    assert counts["proposals_sent"] == 1, "the seeded SENT_TO_CUSTOMER lead"
    assert counts["awaiting_owner_approval"] == 1, "the seeded CUSTOMER_FINALIZED lead"
    assert counts["qualifying"] == 0 and counts["new_leads"] == 0, (
        f"the transcript lead has moved on: {counts}")
    assert counts["on_hold"] == 0, "the per-lead hold was lifted before the end"
    assert counts["followups_due"] == 0, (
        f"the transcript's follow-ups are approved_sent / suppressed, not due: {counts}")

    # FINDING RESOLVED (fix-intake F10): the dashboard counts BOOKED alongside
    # CLOSED in booked_this_month, so the won state is visible. (Known residual,
    # backlogged: CLOSED still conflates declines with legacy wins.)
    assert counts["booked_this_month"] == 1, (
        f"REGRESSION: BOOKED no longer counted in booked_this_month: {counts}")
    assert counts["lost_this_month"] == 0, "a booking is certainly not a loss"

    readiness = body["readiness"]
    assert readiness["pricebook_present"] is True
    assert readiness["pricebook_placeholder"] is False, (
        "the transcript imported a REAL pricebook")
    assert readiness["pricebook_version"] == _PRICEBOOK_VERSION
    assert readiness["menu_version"] == _MENU_VERSION
    assert readiness["kill_switch_engaged"] is False, "the kill switch was lifted again"
    assert body["degraded"] == [], f"nothing should read as degraded: {body['degraded']}"


# ── lead list ────────────────────────────────────────────────────────────────
def test_lead_list_shows_the_booked_lead_with_its_latest_quote(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed_finished_lifecycle(tmp_path, monkeypatch)
    body = _authed_client(monkeypatch).get("/catering/leads").json()

    assert body["available"] is True and body["degraded"] is None
    rows = {r["lead_id"]: r for r in body["leads"]}
    assert set(rows) == {_BOOKED_LEAD, _SENT_LEAD, _FINALIZED_LEAD}, list(rows)
    booked = rows[_BOOKED_LEAD]

    assert booked["status"] == "BOOKED"
    assert booked["headcount"] == 160, (
        f"the list must show the AMENDED headcount, got {booked['headcount']}")
    assert booked["event_date"] == "2026-09-12"
    assert booked["approval_code"] == _BOOKED_CODE
    assert booked["latest_quote_total_usd"] == _BOOKED_TOTAL_USD, (
        f"the list must join the LATEST ledger total, got {booked['latest_quote_total_usd']}")

    # FINDING RESOLVED (fix-intake F9): quote_version is derived from the same
    # ledger join that produces latest_quote_total_usd — the dead lead field
    # (permanently 0) is no longer echoed.
    assert booked["quote_version"] == 2, (
        f"REGRESSION: quote_version must come from the ledger join (2 committed "
        f"versions seeded), got {booked['quote_version']}")
    assert body["status_counts"]["BOOKED"] == 1, body["status_counts"]

    # FINDING RESOLVED (fix-intake F10): BOOKED carries a real next action.
    assert booked["next_action"] == "Booked — confirm the final details", (
        f"REGRESSION: BOOKED lost its next_action mapping, got "
        f"{booked['next_action']!r}")

    assert _BOOKED_PHONE not in json.dumps(body), "a raw phone must never reach a list view"


# ── lead detail: quote versions + control history + timeline ─────────────────
def test_lead_detail_shows_quote_versions_control_and_timeline(tmp_path, monkeypatch):
    pytest.importorskip("jose")
    _seed_finished_lifecycle(tmp_path, monkeypatch)
    body = _authed_client(monkeypatch).get(f"/catering/leads/{_BOOKED_LEAD}").json()

    assert body["status"] == "BOOKED"
    assert body["quote_total_usd"] == _BOOKED_TOTAL_USD
    assert body["quote_text"].strip(), "a booked lead carries the quote it was booked on"
    assert body["quote_version"] == 2, (
        "detail headline must equal max(quote_versions[].version) from the "
        "ledger join (fix-intake F9)")
    assert [(i["name"], i["qty"]) for i in body["selected_items"]] == [
        (i["name"], i["qty"]) for i in _selected_items()]

    # Quote versions: newest first, both sources, with the diff linkage.
    assert body["quote_ledger_available"] is True
    versions = body["quote_versions"]
    assert [v["version"] for v in versions] == [2, 1], (
        f"versions render newest-first: {[v['version'] for v in versions]}")
    assert [v["source"] for v in versions] == ["owner_edit", "initial_draft"]
    assert versions[1]["from_version"] is None, (
        "the first committed version has nothing to diff against")
    assert versions[0]["from_version"] == 1, (
        f"the revision must link back to what it revised: {versions[0]}")
    for v in versions:
        assert v["pricebook_version"] == _PRICEBOOK_VERSION, v
        assert v["menu_version"] == _MENU_VERSION, v
        assert v["price_status"] == "exact", v
        assert v["ledger_entry_id"], v

    # Control chip: the conversation was opted out, taken over, and released —
    # the Studio must show it back to normal, attributed to the owner.
    control = body["control"]
    assert control["available"] is True, control
    assert control["stored_mode"] == "active" and control["effective_mode"] == "active", control
    assert control["expired"] is False
    assert control["actor"] == "owner" and control["reason"] == "owner_release", control
    assert control["since_ts"], control

    # Timeline: the lifecycle, newest first.
    events = [row["event"] for row in body["timeline"]]
    for expected in ("catering_lead_created", "catering_amendment_applied",
                     "catering_proposals_generated", "catering_quote_sent",
                     "catering_lead_acceptance_recorded"):
        assert expected in events, f"{expected} missing from the timeline: {events}"
    assert events.index("catering_lead_acceptance_recorded") < events.index(
        "catering_lead_created"), f"the timeline renders newest-first: {events}"

    # Follow-ups: what was sent and what was suppressed, both visible.
    assert body["followups_available"] is True
    by_status = {f["status"]: f for f in body["followups"]}
    assert set(by_status) == {"approved_sent", "suppressed"}, body["followups"]
    assert by_status["suppressed"]["raw"]["suppressed_reason"] == "automation_suppressed"

    assert body["degraded"] == [], f"nothing should read as degraded: {body['degraded']}"
