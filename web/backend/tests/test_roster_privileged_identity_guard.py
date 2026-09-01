"""The cross-store privileged-identity guard at the roster mutation boundary.

`roster_session` is the single chokepoint all four cockpit roster writers
share (add_employee, patch_employee, terminate_employee, import_csv), so the
guard is wired there rather than per-route. These tests drive the real HTTP
routes to prove the wiring actually reaches each of them.

Design decision under test: the guard compares BEFORE against AFTER and
refuses only violations the mutation INTRODUCES. A guard that rejected any
present violation would lock the operator out of fixing exactly the row that
needs fixing — `test_a_preexisting_violation_can_still_be_repaired` is the
control for that, and it would fail on the stricter design.
"""
from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("fastapi")

OWNER_PHONE = "+918522041562"
OWNER_LID = "211390371475536@lid"
DUAL_PHONE = "+17329837841"
DUAL_LID = "201975216009469@lid"
ATTACK_PHONE = "+15125550199"
ORD_PHONE = "+19045550101"


def _authed_client(monkeypatch, *, fresh: bool = True):
    from fastapi.testclient import TestClient
    from jose import jwt

    from app import auth as auth_mod
    from app.main import app

    issued_at = 1_700_000_000
    token = jwt.encode(
        {"sub": "+19045550100", "iat": issued_at, "exp": 1_800_000_000,
         "jti": "test", "auth_method": "pushover"},
        auth_mod.settings.jwt_secret, algorithm=auth_mod.settings.jwt_algo,
    )
    client = TestClient(app)
    client.cookies.set(auth_mod.settings.cookie_name, token)
    monkeypatch.setattr(auth_mod, "_now", lambda: issued_at + (60 if fresh else 400))
    return client


def _emp(eid, phone, lid=None):
    e = {"id": eid, "name": "Row " + eid, "role": "floor", "phone": phone,
         "status": "active", "languages": ["en"], "can_cover_roles": ["floor"]}
    if lid:
        e["lid"] = lid
    return e


def _roster_sha(settings) -> str:
    return hashlib.sha256(settings.roster_path.read_bytes()).hexdigest()


@pytest.fixture
def env(monkeypatch):
    """Temp config + roster carrying the intentional dual-role principal."""
    import yaml

    # The EXACT Settings instance app.state bound at import (state.py:36).
    # Calling get_settings.cache_clear() here would mint a NEW instance while
    # state.py kept the old one, so the fixture would write to a different
    # path than the code reads -- invisible when this file runs alone, and the
    # cause of every test in it failing when it runs after another module.
    from app.state import settings
    settings.config_path.parent.mkdir(parents=True, exist_ok=True)
    settings.config_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": OWNER_PHONE,
                  "self_chat_jid": "918522041562@s.whatsapp.net",
                  "lid": OWNER_LID,
                  "authorized_identities": [{"phone": DUAL_PHONE}]},
        "limits": {},
        "alerting": {"pushover_user_key": "k" * 30, "pushover_app_token": "t" * 30},
        "backup": {"gpg_recipient_email": "o@example.com"},
    }), encoding="utf-8")

    def _write(employees):
        settings.roster_path.parent.mkdir(parents=True, exist_ok=True)
        settings.roster_path.write_text(json.dumps({
            "location": {"id": "loc_jax_01"}, "schedule": {},
            "employees": employees,
        }), encoding="utf-8")

    _write([_emp("e001", ORD_PHONE, "555000111222333@lid"),
            _emp("e008", DUAL_PHONE, DUAL_LID)])
    settings._write = _write  # type: ignore[attr-defined]
    return settings


# ── the guard refuses at the chokepoint, and nothing is written ─────────────
#
# IMPORTANT, established by running it rather than assumed: the cockpit HTTP
# API cannot currently create a stitched row at all. `EmployeeIn` and
# `EmployeePatch` (app/models.py) have NO `lid` field, so a posted `lid` is
# silently dropped and every API-created row has lid=None. The stitch shapes
# need a lid, so at the HTTP layer the guard is DEFENCE IN DEPTH, not the
# active control.
#
# It is still wired at `roster_session` because that is where it becomes
# load-bearing the moment any writer gains the ability -- and because
# `roster_session` is used directly, not only via HTTP. So the refusal is
# tested where a stitch can actually be constructed, and the HTTP layer is
# tested for the property that currently prevents it.


def test_the_guard_refuses_a_stitch_at_the_roster_session_chokepoint(env):
    """The real test of the wiring: mutate through the session directly."""
    from app.state import PrivilegedIdentityViolation, roster_session
    from schemas import Employee

    before = _roster_sha(env)
    with pytest.raises(PrivilegedIdentityViolation) as ei:
        with roster_session() as (roster, commit):
            roster.employees.append(Employee(**_emp("e099", ATTACK_PHONE, OWNER_LID)))
            commit()

    # WITNESS: names the row and the reason, so an unrelated failure in the
    # session cannot be mistaken for the guard firing.
    assert ei.value.violations[0]["employee_id"] == "e099"
    assert ei.value.violations[0]["reason"] == "unrelated_identifier_on_privileged_row"
    # WITNESS: refused BEFORE persistence.
    assert _roster_sha(env) == before, "roster was written despite the refusal"


def test_the_http_api_cannot_set_a_lid_at_all(env, monkeypatch):
    """Why the HTTP layer needs no stitch test today -- and a tripwire.

    If `EmployeeIn` ever gains a `lid` field, this test fails and tells the
    next person that the guard has just become load-bearing at the HTTP layer,
    where it previously was not.
    """
    from app.models import EmployeeIn

    assert "lid" not in EmployeeIn.model_fields, (
        "EmployeeIn gained a `lid` field -- the HTTP layer can now construct a "
        "privileged-identity stitch. Add an HTTP-level refusal test."
    )

    client = _authed_client(monkeypatch)
    r = client.post("/roster/employee", json=_emp("e099", ATTACK_PHONE, OWNER_LID))
    assert r.status_code == 201, r.text
    # ...and the lid was dropped, which is the ONLY reason this is not a stitch.
    stored = json.loads(env.roster_path.read_text(encoding="utf-8"))
    row = next(e for e in stored["employees"] if e["id"] == "e099")
    assert row.get("lid") is None


def test_csv_import_cannot_carry_a_lid_either(env, monkeypatch):
    """import_csv rebuilds every row through EmployeeIn, so lid is dropped
    fleet-wide -- which is also how an import silently strips existing lids."""
    client = _authed_client(monkeypatch)
    csv = (
        "id,name,role,phone,languages,can_cover_roles,status\n"
        f"e001,Ravi,cashier,{ORD_PHONE},en,cashier,active\n"
        f"e008,Dual,floor,{DUAL_PHONE},en,floor,active\n"
    )
    r = client.post("/roster/import-csv",
                    files={"file": ("roster.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    stored = json.loads(env.roster_path.read_text(encoding="utf-8"))
    e008 = next(e for e in stored["employees"] if e["id"] == "e008")
    assert e008.get("lid") is None, (
        "e008 kept its lid through a CSV import -- if import ever preserves "
        "lids, an import can construct a stitch and needs its own refusal test"
    )


# ── controls: legitimate mutations must still work ───────────────────────────

def test_the_intentional_dual_role_roster_still_accepts_ordinary_edits(env, monkeypatch):
    """The control. A guard that refused everything would pass the tests above.

    e008 holds the authorized phone and its own LID -- the deployed shape. An
    unrelated add must still succeed against that roster.
    """
    client = _authed_client(monkeypatch)
    before = _roster_sha(env)

    r = client.post("/roster/employee",
                    json=_emp("e060", "+19045550160", "666000111222333@lid"))

    assert r.status_code == 201, r.text
    assert _roster_sha(env) != before, "an accepted mutation did not persist"


def test_terminating_an_employee_still_works(env, monkeypatch):
    client = _authed_client(monkeypatch)
    r = client.delete("/roster/employee/e001")
    assert r.status_code == 200, r.text


def test_a_preexisting_violation_can_still_be_repaired(env, monkeypatch):
    """The load-bearing control for the before/after design.

    A roster that ALREADY contains a stitched row must stay editable, or the
    operator is locked out of fixing the very row that needs fixing. This test
    fails on the stricter "reject any present violation" design.
    """
    client = _authed_client(monkeypatch)
    env._write([_emp("e001", ORD_PHONE, "555000111222333@lid"),
                _emp("e008", DUAL_PHONE, DUAL_LID),
                _emp("e099", ATTACK_PHONE, OWNER_LID)])   # pre-existing stitch
    before = _roster_sha(env)

    # terminating the offending row is a legitimate repair
    r = client.delete("/roster/employee/e099")

    assert r.status_code == 200, r.text
    assert _roster_sha(env) != before, "the repair did not persist"


def test_guard_degrades_open_when_owner_config_is_unreadable(env, monkeypatch):
    """Availability: a config read failure must not make the roster unwritable.

    The write is already auth-gated, and #773's resolver-side refusals still
    stand underneath, so failing closed here would trade a real outage for no
    additional protection.
    """
    client = _authed_client(monkeypatch)
    env.config_path.write_text("{{{ not yaml", encoding="utf-8")
    before = _roster_sha(env)

    r = client.post("/roster/employee",
                    json=_emp("e070", "+19045550170", "777000111222333@lid"))

    assert r.status_code == 201, r.text
    assert _roster_sha(env) != before
