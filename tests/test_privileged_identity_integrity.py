"""Cross-store privileged-identity invariant.

Every dangerous fixture below is validated through the REAL ``Roster`` and
``Config`` models before the security assertion runs. A schema-load failure is
not a security refusal -- that exact confusion already produced one false green
in this programme, when a probe row used an id violating ``EmployeeId`` and the
whole roster failed to load, making ``rc != 0`` pass for the wrong reason.

Every rejection is paired with a legitimate control, and every assertion names
the specific employee id and the specific conflicting identifiers. ``assert
violations`` alone would pass on a function that flagged everything.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "platform"))

from privileged_identity import check_privileged_identity_integrity  # noqa: E402

OWNER_PHONE = "+918522041562"
OWNER_LID = "211390371475536@lid"
DUAL_PHONE = "+17329837841"          # authorized identity AND roster e008
DUAL_LID = "201975216009469@lid"
ALIAS_LID = "777000111222333@lid"    # a lid recorded ON the authorized identity
ATTACK_PHONE = "+15125550199"
ATTACK_LID = "888000111222333@lid"
ORD_PHONE = "+19045550101"
ORD_LID = "555000111222333@lid"

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
PAST_FROM = (NOW - timedelta(days=800)).isoformat()
PAST_TO = (NOW - timedelta(days=400)).isoformat()
LIVE_FROM = (NOW - timedelta(days=100)).isoformat()


def _config(*, alias_lid=None) -> dict:
    alias = {"phone": DUAL_PHONE}
    if alias_lid:
        alias["lid"] = alias_lid
    return {
        "schema_version": 1,
        "customer": {"name": "T", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": OWNER_PHONE,
                  "self_chat_jid": "918522041562@s.whatsapp.net",
                  "lid": OWNER_LID, "authorized_identities": [alias]},
        "limits": {},
        "alerting": {"pushover_user_key": "k" * 30, "pushover_app_token": "t" * 30},
        "backup": {"gpg_recipient_email": "o@example.com"},
    }


def _emp(eid, phone, lid=None, history=None) -> dict:
    e = {"id": eid, "name": "Row " + eid, "role": "floor", "phone": phone,
         "status": "active", "languages": ["en"], "can_cover_roles": ["floor"]}
    if lid:
        e["lid"] = lid
    if history:
        e["phone_history"] = history
    return e


def _roster(*extra, dual_lid=DUAL_LID, dual_history=None) -> dict:
    e008 = _emp("e008", DUAL_PHONE, dual_lid, dual_history)
    e008["name"] = "Dual Principal"
    return {"location": {"id": "loc_jax_01"}, "schedule": {}, "employees": [
        _emp("e001", ORD_PHONE, ORD_LID), e008, *extra]}


def _validated(roster: dict, config: dict):
    """Fixtures MUST be schema-valid, or a violation proves nothing."""
    from schemas import Config, Roster
    Config.model_validate(config)
    r = Roster.model_validate(roster)
    r.check_referential_integrity()
    return roster, config


def _check(roster, config):
    roster, config = _validated(roster, config)
    return check_privileged_identity_integrity(roster, config["owner"], now=NOW)


def _ids(violations):
    return sorted(v.employee_id for v in violations)


# ── controls: what MUST keep working ─────────────────────────────────────────

def test_the_intentional_dual_role_is_not_a_violation():
    """e008 is ONE human with two capabilities -- the deployed configuration.

    If this ever fails, the invariant has started banning owner-as-employee,
    which would reject a working production roster.
    """
    assert _check(_roster(), _config()) == []


def test_ordinary_employees_are_not_violations():
    assert _check(_roster(_emp("e050", "+19045550150", "111000111222333@lid")),
                  _config()) == []


def test_expired_history_reassignment_is_legitimate():
    """A terminated employee's old number now held by someone else.

    The resolver skips a closed window, so this must not be flagged; doing so
    would make legitimate number recycling impossible.
    """
    hist = [{"phone": OWNER_PHONE, "effective_from": PAST_FROM,
             "effective_to": PAST_TO}]
    assert _check(_roster(_emp("e060", "+19045550160", None, hist)), _config()) == []


# ── the attacks, each with a WITNESS ─────────────────────────────────────────

def test_attack_a_attacker_phone_plus_primary_owner_lid():
    v = _check(_roster(_emp("e099", ATTACK_PHONE, OWNER_LID)), _config())
    assert _ids(v) == ["e099"]
    # WITNESS: the row is flagged for carrying an identifier not bound to the
    # principal it matched -- not merely "something was wrong".
    assert v[0].reason == "unrelated_identifier_on_privileged_row"
    assert "owner" in v[0].detail
    flat = str(v[0].conflicting)
    assert ATTACK_PHONE in flat


def test_attack_b_owner_phone_plus_attacker_lid():
    v = _check(_roster(_emp("e098", OWNER_PHONE, ATTACK_LID)), _config())
    assert _ids(v) == ["e098"]
    assert ATTACK_LID in str(v[0].conflicting)


def test_attack_c_authorized_identity_lid_is_in_scope():
    """The alias case. An invariant written against owner.phone/owner.lid
    alone would miss this entirely -- the stitch reaches owner through
    `authorized_identities`."""
    cfg = _config(alias_lid=ALIAS_LID)
    v = _check(_roster(_emp("e097", ATTACK_PHONE, ALIAS_LID), dual_lid=ALIAS_LID), cfg)
    assert "e097" in _ids(v)
    hit = next(x for x in v if x.employee_id == "e097")
    assert "authorized_identities[0]" in str(hit.conflicting) + hit.detail


def test_a_row_spanning_two_principals_is_flagged():
    """The clearest stitch: one identifier from the owner, one from the alias."""
    cfg = _config(alias_lid=ALIAS_LID)
    v = _check(_roster(_emp("e096", OWNER_PHONE, ALIAS_LID), dual_lid=ALIAS_LID), cfg)
    hit = next(x for x in v if x.employee_id == "e096")
    assert hit.reason == "identifiers_span_multiple_owner_principals"
    # WITNESS: BOTH principals are named, so a function that flagged every
    # privileged row would not satisfy this.
    labels = {c["principal"] for c in hit.conflicting}
    assert labels == {"owner", "authorized_identities[0]"}


def test_currently_effective_history_binding_is_flagged():
    """An open window is equivalent to a current phone -- the row claims both.

    MEASURED against the real resolver, not assumed: this shape is an
    ATTRIBUTION defect, not a privilege escalation. With
    row e095 {phone: attacker, history: [owner_phone, still open]}:

        attacker by their own phone      -> ["employee"]              e095
        attacker by their LID            -> ["employee"]              e095
        owner    by the owner phone      -> ["employee", "owner"]     e095

    The attacker gains nothing. What breaks is that the OWNER's inbound is
    attributed to employee e095 -- so an owner message could be recorded
    against, or bind a coverage reply to, an unrelated employee. Flagged for
    that reason; the violation detail must not overclaim escalation.
    """
    hist = [{"phone": OWNER_PHONE, "effective_from": LIVE_FROM}]
    v = _check(_roster(_emp("e095", ATTACK_PHONE, None, hist)), _config())
    assert _ids(v) == ["e095"]
    flat = str(v[0].conflicting)
    assert ATTACK_PHONE in flat, "the unrelated identifier must be named"


def test_n5_is_detectable_once_the_alias_records_a_lid():
    """The N5 stitch, in the configuration where it IS decidable.

    With `lid` recorded on the authorized identity, a stranger's LID on that
    row contradicts the principal and is caught. This is the paired positive
    for the known limitation asserted below.
    """
    cfg = _config(alias_lid=ALIAS_LID)
    v = _check(_roster(dual_lid=ATTACK_LID), cfg)
    assert _ids(v) == ["e008"]
    assert ATTACK_LID in str(v[0].conflicting)


def test_known_limitation_n5_is_invisible_without_an_alias_lid():
    """DOCUMENTED GAP, asserted so it cannot regress silently.

    With no `lid` on the authorized identity -- the deployed configuration --
    stored state cannot tell e008's own LID from a stranger's LID written onto
    e008's row. Both are "the authorized phone plus some LID". This test pins
    the gap rather than hiding it: closing it requires recording the alias's
    lid in config, which is an operator decision.
    """
    assert _check(_roster(dual_lid=ATTACK_LID), _config()) == []
    # ...and the paired positive above proves the check DOES fire once the
    # data exists, so this is a data gap and not a broken check.


# ── robustness ───────────────────────────────────────────────────────────────

def test_differently_formatted_phone_is_still_matched():
    """The resolver compares canonical phones; raw-string comparison would
    let a reformatted duplicate slip past a check the resolver still matches."""
    v = _check(_roster(_emp("e094", "+91-85220-41562", ATTACK_LID)), _config())
    assert _ids(v) == ["e094"]


def test_no_owner_config_yields_no_violations_rather_than_raising():
    assert check_privileged_identity_integrity(_roster(), {"authorized_identities": []}) == []


def test_malformed_rows_do_not_raise():
    roster = {"employees": [{"id": "e001"}, {}, {"id": "e002", "phone": None}]}
    assert check_privileged_identity_integrity(roster, _config()["owner"]) == []
