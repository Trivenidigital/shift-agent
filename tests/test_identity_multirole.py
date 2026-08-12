"""Multi-role identity — `identify-sender` membership resolution.

Linux-only (identify-sender imports safe_io, which uses fcntl). Pattern mirrors
tests/test_agent_13_compliance_script.py: subprocess invocation against fixture
files via the env-overridable SHIFT_AGENT_CONFIG_PATH / SHIFT_AGENT_ROSTER_PATH.

The contract under test:

  * `roles` is the authorization surface and is INDEPENDENT of whether the
    principal was looked up by phone or by LID.
  * `role` is a LEGACY COMPATIBILITY PROJECTION that preserves each branch's
    original precedence (employee-first by LID, primary-owner-first by phone),
    so the ~38 deployed scalar consumers are unaffected — in particular the
    ~20 that use `!= "owner"` as shorthand for "customer".
  * Owner membership NEVER synthesizes an employee_id.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="identify-sender imports safe_io which uses fcntl (Linux only)",
)

REPO = Path(__file__).resolve().parent.parent
IDENTIFY = REPO / "src" / "platform" / "scripts" / "identify-sender"
PLATFORM_DIR = REPO / "src" / "platform"

OWNER_PHONE = "+919000000001"
OWNER_LID = "900000000000001@lid"
# The dual-role principal: an active roster employee who is ALSO granted owner
# authorization via owner.authorized_identities.
DUAL_PHONE = "+17329837841"
DUAL_LID = "201975216009469@lid"
EMP_PHONE = "+19045550101"
EMP_LID = "100000000000001@lid"
INACTIVE_PHONE = "+19045550102"
INACTIVE_LID = "100000000000002@lid"
# e003's former number, reassigned away — its phone_history window is closed.
RECYCLED_PHONE = "+19045550177"
CURRENT_E003_PHONE = "+19045550103"
STRANGER_PHONE = "+19045559999"
STRANGER_LID = "999999999999999@lid"


def _config(*, authorized: bool, alias_lid: bool = True) -> dict:
    owner = {
        "name": "Owner",
        "phone": OWNER_PHONE,
        "self_chat_jid": "919000000001@s.whatsapp.net",
        "lid": OWNER_LID,
    }
    if authorized:
        alias = {"phone": DUAL_PHONE}
        if alias_lid:
            alias["lid"] = DUAL_LID
        owner["authorized_identities"] = [alias]
    return {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": owner,
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "x@y"},
    }


def _roster() -> dict:
    return {
        "location": {"name": "Triveni", "timezone": "America/New_York"},
        "employees": [
            {"id": "e008", "name": "Dual Person", "role": "floor",
             "phone": DUAL_PHONE, "lid": DUAL_LID, "status": "active"},
            {"id": "e001", "name": "Plain Employee", "role": "cashier",
             "phone": EMP_PHONE, "lid": EMP_LID, "status": "active"},
            {"id": "e002", "name": "Former Employee", "role": "cashier",
             "phone": INACTIVE_PHONE, "lid": INACTIVE_LID, "status": "terminated"},
            # Held RECYCLED_PHONE until 2025; the window is closed, so that
            # number must no longer resolve to anyone.
            {"id": "e003", "name": "Renumbered Employee", "role": "cashier",
             "phone": CURRENT_E003_PHONE, "status": "active",
             "phone_history": [{"phone": RECYCLED_PHONE,
                                "effective_from": "2024-01-01T00:00:00Z",
                                "effective_to": "2025-01-01T00:00:00Z"}]},
        ],
    }


@pytest.fixture
def env(tmp_path):
    """Write config + roster fixtures; return the env for identify-sender."""
    def _build(*, authorized: bool, alias_lid: bool = True):
        cfg_path = tmp_path / f"config-{int(authorized)}{int(alias_lid)}.yaml"
        roster_path = tmp_path / "roster.json"
        cfg_path.write_text(
            yaml.safe_dump(_config(authorized=authorized, alias_lid=alias_lid)),
            encoding="utf-8")
        roster_path.write_text(json.dumps(_roster()), encoding="utf-8")
        import os
        e = os.environ.copy()
        e["SHIFT_AGENT_CONFIG_PATH"] = str(cfg_path)
        e["SHIFT_AGENT_ROSTER_PATH"] = str(roster_path)
        e["PYTHONPATH"] = str(PLATFORM_DIR)
        return e
    return _build


def resolve(env_build, identifier: str, *, authorized: bool = True,
            alias_lid: bool = True) -> dict:
    proc = subprocess.run(
        [sys.executable, str(IDENTIFY), identifier],
        capture_output=True, text=True, timeout=30,
        env=env_build(authorized=authorized, alias_lid=alias_lid),
    )
    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr}"
    return json.loads(proc.stdout)


# ---------------------------------------------------------------- regression pins

def test_owner_only_principal_unchanged(env):
    """Owner-only: scalar `owner`, owner membership, and NO employee_id."""
    for ident in (OWNER_PHONE, OWNER_LID):
        doc = resolve(env, ident)
        assert doc["role"] == "owner", ident
        assert doc["roles"] == ["owner"], ident
        assert "employee_id" not in doc, (
            f"owner membership must never synthesize an employee_id ({ident})")


def test_owner_by_phone_still_reports_owner_lid(env):
    """PRE-#692 output parity: owner BY PHONE must still carry `owner.lid`.

    The original phone branch emitted
    `_emit_owner(cfg.owner, phone=canonical, lid=cfg.owner.lid)`, so resolving
    the primary owner by phone reported the configured LID. Rebuilding the emit
    around widened identifiers silently dropped it to null, because an
    owner-only principal has no roster row to widen from.

    It is not cosmetic: `audit_dispatcher_routed` reads `sender_lid` directly
    from this field, so a null would degrade routing audit rows. The earlier
    regression test asserted role/roles/employee_id and never looked at `lid`,
    which is why it passed.
    """
    by_phone = resolve(env, OWNER_PHONE)
    by_lid = resolve(env, OWNER_LID)
    assert by_phone["lid"] == OWNER_LID, (
        f"owner-by-phone must report owner.lid, got {by_phone['lid']!r}")
    assert by_lid["lid"] == OWNER_LID
    assert by_phone["phone_normalized"] == by_lid["phone_normalized"] == OWNER_PHONE


def test_employee_only_principal_unchanged(env):
    """Plain employee: scalar `employee` by BOTH identifiers, no owner membership."""
    for ident in (EMP_PHONE, EMP_LID):
        doc = resolve(env, ident)
        assert doc["role"] == "employee", ident
        assert doc["roles"] == ["employee"], ident
        assert doc["employee_id"] == "e001", ident


def test_unknown_principal_has_empty_roles(env):
    for ident in (STRANGER_PHONE, STRANGER_LID):
        doc = resolve(env, ident)
        assert doc["role"] == "unknown", ident
        assert doc["roles"] == [], ident


def test_legacy_scalar_frozen_for_dual_principal(env):
    """THE compatibility pin.

    The dual principal must keep resolving to scalar `employee` on BOTH
    branches. If this flips to `owner`, the ~20 deployed `role != "owner"`
    sites stop treating this number as a customer and its customer-facing
    flyer workflows silently disappear.
    """
    for ident in (DUAL_PHONE, DUAL_LID):
        doc = resolve(env, ident)
        assert doc["role"] == "employee", (
            f"legacy scalar MUST stay `employee` for {ident} — see the 20 "
            f"`role != \"owner\"` customer-shorthand sites")


def test_scalar_identical_with_and_without_owner_grant(env):
    """Granting owner authorization must not perturb the legacy scalar."""
    for ident in (DUAL_PHONE, DUAL_LID, EMP_PHONE, EMP_LID, OWNER_PHONE, OWNER_LID):
        before = resolve(env, ident, authorized=False)["role"]
        after = resolve(env, ident, authorized=True)["role"]
        assert before == after, f"scalar changed for {ident}: {before} -> {after}"


# ------------------------------------------------------------------ new behavior

def test_dual_principal_holds_both_memberships(env):
    doc = resolve(env, DUAL_PHONE)
    assert doc["roles"] == ["employee", "owner"]
    assert doc["employee_id"] == "e008"


def test_membership_is_branch_independent(env):
    """Acceptance case #4 — phone and LID forms must agree.

    Pins the pre-change defect directly: the LID branch checked employees
    first and the phone branch checked owner first, so the same principal
    answered differently depending on which identifier the caller held.
    """
    by_phone = resolve(env, DUAL_PHONE)
    by_lid = resolve(env, DUAL_LID)
    assert by_phone["roles"] == by_lid["roles"] == ["employee", "owner"]
    assert by_phone["employee_id"] == by_lid["employee_id"] == "e008"
    assert by_phone["phone_normalized"] == by_lid["phone_normalized"] == DUAL_PHONE


def test_owner_membership_absent_without_the_grant(env):
    """Negative pin: employee membership alone NEVER confers owner capability."""
    doc = resolve(env, DUAL_PHONE, authorized=False)
    assert doc["roles"] == ["employee"]
    assert "owner" not in doc["roles"]


def test_plain_employee_never_gains_owner(env):
    """The safety rule: granting owner to ONE employee must not leak to others."""
    for ident in (EMP_PHONE, EMP_LID):
        doc = resolve(env, ident)
        assert "owner" not in doc["roles"], ident


def test_inactive_employee_still_reports_membership(env):
    """identify-sender reports membership regardless of roster status.

    Active-status enforcement is the CONSUMER's job
    (`has_employee_capability` ANDs `is_employee_chat`). This pins the split so
    a later refactor does not quietly move status filtering into the resolver
    and change what `roles` means.
    """
    doc = resolve(env, INACTIVE_LID)
    assert doc["roles"] == ["employee"]
    assert doc["employee_id"] == "e002"


def test_inactive_employee_converges_across_phone_and_lid(env):
    """HARD REGRESSION — branch-independence for a NON-active employee.

    `Roster.find_by_phone` skips `status != "active"` (schemas.py), so routing
    the membership lookup through it made an inactive employee answer
    `roles=["employee"]` by LID and `roles=[]` by phone. The earlier test
    exercised only the LID side and could not see the contradiction.

    Membership is the relationship; ACTIVE status is an authorization condition
    enforced by the consumer. Both identifiers must therefore agree.
    """
    by_lid = resolve(env, INACTIVE_LID)
    by_phone = resolve(env, INACTIVE_PHONE)
    assert by_lid["roles"] == by_phone["roles"] == ["employee"]
    assert by_lid["employee_id"] == by_phone["employee_id"] == "e002"
    assert by_lid["phone_normalized"] == by_phone["phone_normalized"] == INACTIVE_PHONE


def test_owner_alias_with_phone_only_still_resolves_via_lid(env):
    """The production alias should need PHONE ONLY.

    An inbound arriving by LID resolves the employee row, which supplies the
    canonical phone; owner membership is then derived from that phone. Storing
    the LID in config too would create a second pair-consistency surface to
    keep correct, so this pins that it is unnecessary.
    """
    doc = resolve(env, DUAL_LID, alias_lid=False)
    assert doc["roles"] == ["employee", "owner"], (
        "LID inbound must widen through the employee identity to the canonical "
        "phone and pick up the phone-only owner alias")
    assert doc["employee_id"] == "e008"
    assert doc["phone_normalized"] == DUAL_PHONE


def test_expired_historical_phone_does_not_confer_membership(env):
    """A recycled number must not inherit its former holder's identity.

    The status filter was dropped from the phone lookup, but `phone_history`
    effective windows are preserved exactly. `RECYCLED_PHONE` was e003's number
    until it was reassigned, so it must resolve to nobody now.
    """
    doc = resolve(env, RECYCLED_PHONE)
    assert doc["roles"] == [], doc
    assert doc["role"] == "unknown", doc
    assert "employee_id" not in doc, doc


def test_phone_jid_form_matches_e164_form(env):
    """`<digits>@s.whatsapp.net` and `+E164` are the same principal."""
    jid = resolve(env, DUAL_PHONE.lstrip("+") + "@s.whatsapp.net")
    e164 = resolve(env, DUAL_PHONE)
    assert jid["roles"] == e164["roles"]
    assert jid["role"] == e164["role"]
