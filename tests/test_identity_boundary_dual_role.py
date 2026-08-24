"""One human, two roles — the identity boundary of the Shift coverage loop.

The reference customer's operator is BOTH the owner (via
``owner.authorized_identities``) and active roster employee ``e008`` (role
``floor``). Verified read-only against 46.62.206.192 on 2026-08-24:

    config.owner.phone                    +918522041562
    config.owner.authorized_identities[0] +17329837841
    roster e008 "Srini Bangaru"  floor    +17329837841  201975216009469@lid  active

`identify-sender` already models this correctly: `roles` is the authorization
surface and is branch-independent (tests/test_identity_multirole.py). What this
file pins is what the ROUTES do with it — the two places where the answer used
to come from whichever lookup ran first rather than from the authority the
route actually exercised:

  1. ATTRIBUTION. `dispatcher_routed.sender_role` was taken from the LEGACY
     SCALAR `role`, which identify-sender documents as a compatibility
     projection ("New authorization MUST read `roles`, never the scalar").
     The scalar's precedence is employee-first by LID, so the dual principal's
     OWNER approval of a coverage proposal audited as `sender_role="employee"`
     while the SAME approval by an owner-only principal audited as
     `sender_role="owner"`. The two authorities this one human exercises —
     approving the proposal, then answering the coverage ask as its candidate —
     were indistinguishable in the audit log.

  2. AMBIGUITY. Nothing enforces that a phone or a LID names ONE employee:
     `Roster.check_referential_integrity` uniques `id` only. Two employees
     sharing a phone made `identify-sender` answer with whichever row came
     FIRST IN THE FILE, so re-ordering the same roster changed who an absence
     was recorded against and who a coverage ask was sent to.

Deliberately NOT pinned as a ban: the dual principal being selectable as a
coverage candidate, and approving a proposal naming themselves. Both are
technically representable, and whether an owner may cover a shift is a POLICY
question for the operator — not a safety invariant to encode here. The
characterization tests at the bottom pin the current behaviour so a future
silent exclusion is visible.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="identify-sender / cf-router actions import safe_io (fcntl only)",
)

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"
IDENTIFY = PLATFORM_DIR / "scripts" / "identify-sender"
SICK_CALL = REPO / "src" / "agents" / "shift" / "scripts" / "handle-shift-sick-call"

if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

# The deployed identities, copied verbatim from the box (read-only).
OWNER_PHONE = "+918522041562"
OWNER_JID = "918522041562@s.whatsapp.net"
OWNER_LID = "211390371475536@lid"
DUAL_PHONE = "+17329837841"
DUAL_JID = "17329837841@s.whatsapp.net"
DUAL_LID = "201975216009469@lid"
ABSENT_DATE = "2026-05-04"


# ─── fixtures ────────────────────────────────────────────────────────────────

def _config_doc() -> dict:
    return {
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_jax_01",
                     "timezone": "America/New_York"},
        "owner": {"name": "Srini (rehearsal owner)", "phone": OWNER_PHONE,
                  "self_chat_jid": OWNER_JID, "lid": OWNER_LID,
                  "authorized_identities": [{"phone": DUAL_PHONE}]},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
    }


def _roster_doc(*, extra_employees: list | None = None) -> dict:
    return {
        "location": {"id": "loc_jax_01"},
        "employees": [
            {"id": "e001", "name": "Ravi Kumar", "role": "cashier",
             "phone": "+19045550101", "status": "active", "languages": ["en"],
             "can_cover_roles": ["cashier", "floor"]},
            {"id": "e008", "name": "Srini Bangaru", "role": "floor",
             "phone": DUAL_PHONE, "lid": DUAL_LID, "status": "active",
             "languages": ["en"], "can_cover_roles": ["cashier", "floor"]},
        ] + (extra_employees or []),
        "schedule": {ABSENT_DATE: [
            {"employee_id": "e001", "shift": "09:00-17:00", "role": "cashier"}]},
    }


@pytest.fixture
def env(tmp_path):
    """Copied state + the env `identify-sender` reads it through."""
    def _build(roster: dict | None = None, config: dict | None = None):
        cfg_path = tmp_path / "config.yaml"
        roster_path = tmp_path / "roster.json"
        cfg_path.write_text(yaml.safe_dump(config or _config_doc()), encoding="utf-8")
        roster_path.write_text(json.dumps(roster or _roster_doc()), encoding="utf-8")
        environ = os.environ.copy()
        environ["SHIFT_AGENT_CONFIG_PATH"] = str(cfg_path)
        environ["SHIFT_AGENT_ROSTER_PATH"] = str(roster_path)
        environ["PYTHONPATH"] = str(PLATFORM_DIR)
        return SimpleNamespace(tmp=tmp_path, config=cfg_path, roster=roster_path,
                               environ=environ)
    return _build


def resolve(state, identifier: str) -> tuple[int, dict]:
    """Run the REAL identify-sender kernel against the copied state."""
    proc = subprocess.run([sys.executable, str(IDENTIFY), identifier],
                          capture_output=True, text=True, timeout=30,
                          env=state.environ)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:  # pragma: no cover — a crash, not a refusal
        doc = {"stdout": proc.stdout, "stderr": proc.stderr[-400:]}
    return proc.returncode, doc


def _load_plugin(tag: str):
    """Load cf-router hooks + actions under a synthetic package.

    Mirrors tests/test_cf_router_candidate_response.py (the plugin dir name is
    hyphenated, so it cannot be imported as a package).
    """
    pkg_name = f"cf_router_{tag}_pkg"
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]
    pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg
    mods = {}
    for name in ("actions", "hooks"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", PLUGIN_DIR / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{name}"] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["hooks"], mods["actions"]


def _wire(actions, state, monkeypatch):
    """Point the plugin at the copied state and at the REAL identify-sender.

    Only the SPAWN is substituted (the deployed binary lives at
    /usr/local/bin/identify-sender); the kernel under test is the repo's own
    script, reading the copied config/roster.
    """
    actions.CONFIG_PATH = state.config
    actions.ROSTER_PATH = state.roster
    actions.PENDING_PATH = state.tmp / "pending.json"
    actions.LEADS_PATH = state.tmp / "catering-leads.json"
    actions.LOG_PATH = state.tmp / "decisions.log"
    if not actions.PENDING_PATH.exists():
        actions.PENDING_PATH.write_text('{"proposals": {}}', encoding="utf-8")
    if not actions.LEADS_PATH.exists():
        actions.LEADS_PATH.write_text('{"leads": []}', encoding="utf-8")

    def _spawn(identifier: str):
        proc = subprocess.run([sys.executable, str(IDENTIFY), identifier],
                              capture_output=True, text=True, timeout=30,
                              env=state.environ)
        if proc.returncode != 0:
            return actions._IdentityResolution(False, {})
        try:
            return actions._IdentityResolution(True, json.loads(proc.stdout))
        except json.JSONDecodeError:
            return actions._IdentityResolution(False, {})
    monkeypatch.setattr(actions, "_invoke_identify_sender", _spawn)


def _seed_proposal(actions, *, status, proposal_id="P0001",
                   candidate="e008", code="#ABCDE"):
    doc = json.loads(actions.PENDING_PATH.read_text(encoding="utf-8"))
    doc["proposals"][proposal_id] = {
        "proposal_id": proposal_id, "code": code, "status": status,
        "candidate_employee_id": candidate, "candidate_name": "Srini Bangaru",
        "absent_employee_id": "e001", "absent_date": ABSENT_DATE,
        "absent_shift": "09:00-17:00", "absent_role": "cashier",
        "created_ts": "2026-08-24T10:00:00+00:00",
    }
    actions.PENDING_PATH.write_text(json.dumps(doc), encoding="utf-8")


def _inbound(chat_id: str, text: str):
    return SimpleNamespace(chat_id=chat_id, text=text, message_id="wamid.DUALROLE",
                           from_me=False, media_path=None)


def _routed_rows(actions) -> list[dict]:
    if not actions.LOG_PATH.exists():
        return []
    rows = [json.loads(line) for line
            in actions.LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("type") == "dispatcher_routed"]


@pytest.fixture
def router(env, monkeypatch):
    """cf-router wired to copied state, with every state-mutating call captured."""
    def _build(roster: dict | None = None, tag: str = "dualrole"):
        state = env(roster=roster)
        hooks, actions = _load_plugin(tag)
        _wire(actions, state, monkeypatch)
        calls: list[tuple] = []
        monkeypatch.setattr(actions, "invoke_update_proposal_status",
                            lambda *a, **k: calls.append(("update_proposal_status", a, k)) or 0)
        monkeypatch.setattr(actions, "invoke_send_coverage_message",
                            lambda pid: calls.append(("send_coverage_message", pid)) or 0)
        monkeypatch.setattr(actions, "invoke_shift_sick_call",
                            lambda **k: calls.append(("shift_sick_call", k)) or (0, "", ""))
        monkeypatch.setattr(actions, "fire_pushover_alert",
                            lambda *a, **k: calls.append(("pushover",)) or None)
        return SimpleNamespace(hooks=hooks, actions=actions, calls=calls, state=state)
    return _build


# ═══════════════════════════════════════════════════════════════════════════
# 1. ATTRIBUTION — the audit row names the authority the ROUTE exercised
# ═══════════════════════════════════════════════════════════════════════════

def test_owner_approval_audits_owner_authority_for_the_dual_principal(router):
    """`handle_owner_command` is an OWNER route. Being an employee too must not
    demote the row: this is the one record of a privileged act."""
    r = router()
    _seed_proposal(r.actions, status="awaiting_owner_approval")

    result = r.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "#ABCDE"))

    assert result is not None and result.get("action") == "skip"
    rows = _routed_rows(r.actions)
    assert [x["routed_to_skill"] for x in rows] == ["handle_owner_command"]
    assert rows[0]["sender_role"] == "owner", (
        "the dual principal approved a coverage proposal as OWNER; the legacy "
        "scalar's employee-first precedence must not decide the audit row")


def test_owner_approval_attribution_is_the_same_for_both_owner_principals(router):
    """The same privileged act, audited the same way, whoever performed it.

    An owner-only principal and a dual principal both approve the SAME kind of
    proposal through the SAME route. If the rows disagree, the audit log is
    describing identity precedence, not authority.
    """
    dual = router(tag="dual")
    _seed_proposal(dual.actions, status="awaiting_owner_approval")
    dual.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "#ABCDE"))

    owner_only = router(tag="owneronly")
    _seed_proposal(owner_only.actions, status="awaiting_owner_approval")
    owner_only.hooks.pre_gateway_dispatch(_inbound(OWNER_JID, "#ABCDE"))

    dual_row = _routed_rows(dual.actions)[0]
    owner_row = _routed_rows(owner_only.actions)[0]
    assert dual_row["routed_to_skill"] == owner_row["routed_to_skill"] == "handle_owner_command"
    assert dual_row["sender_role"] == owner_row["sender_role"] == "owner"


def test_candidate_reply_audits_employee_authority_for_the_dual_principal(router):
    """The RETURN leg is an EMPLOYEE act even though this human is also owner.

    The mirror of the test above: naming the owner authority here would let a
    coverage answer read as a privileged action.
    """
    r = router()
    _seed_proposal(r.actions, status="sent")

    result = r.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "YES"))

    assert result is not None and result.get("action") == "skip"
    rows = _routed_rows(r.actions)
    assert [x["routed_to_skill"] for x in rows] == ["handle_candidate_response"]
    assert rows[0]["sender_role"] == "employee"


def test_one_human_two_authorities_are_distinguishable_in_the_audit_log(router):
    """Approve the proposal, then answer its coverage ask — same person, same
    identifier, two different authorities. The log must say which was which."""
    r = router()
    _seed_proposal(r.actions, status="awaiting_owner_approval")
    r.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "#ABCDE"))
    # What send-coverage-message would have left behind.
    _seed_proposal(r.actions, status="sent")
    r.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "YES"))

    pairs = [(x["routed_to_skill"], x["sender_role"]) for x in _routed_rows(r.actions)]
    assert pairs == [("handle_owner_command", "owner"),
                     ("handle_candidate_response", "employee")]


def test_sick_call_audits_employee_authority(router):
    """The intake leg, for completeness: an absence is an employee act."""
    r = router()
    r.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "Boss I have a fever, can't come in"))

    rows = _routed_rows(r.actions)
    assert [x["routed_to_skill"] for x in rows] == ["handle_sick_call"]
    assert rows[0]["sender_role"] == "employee"


def test_audit_dispatcher_routed_authority_overrides_the_legacy_scalar(router):
    """Unit-level: the explicit authority argument is what lands in the row."""
    r = router()
    r.actions.audit_dispatcher_routed(
        message_id="wamid.UNIT", chat_id=DUAL_LID,
        routed_to_skill="handle_owner_command", message_shape="approval_code",
        authority="owner")
    r.actions.audit_dispatcher_routed(
        message_id="wamid.UNIT2", chat_id=DUAL_LID,
        routed_to_skill="handle_candidate_response", message_shape="text",
        authority="employee")
    # No authority named -> unchanged legacy-scalar behaviour for every other
    # route (the catering / expense arms are deliberately untouched).
    r.actions.audit_dispatcher_routed(
        message_id="wamid.UNIT3", chat_id=DUAL_LID,
        routed_to_skill="update_catering_menu", message_shape="text")

    assert [x["sender_role"] for x in _routed_rows(r.actions)] == [
        "owner", "employee", "employee"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. AMBIGUITY — an identifier that names two employees resolves to NEITHER
# ═══════════════════════════════════════════════════════════════════════════

_CLONE_SAME_PHONE = {"id": "e009", "name": "Phone Clone", "role": "floor",
                     "phone": DUAL_PHONE, "status": "active",
                     "can_cover_roles": ["cashier", "floor"]}
_CLONE_SAME_PHONE_REFORMATTED = {"id": "e010", "name": "Reformatted Clone",
                                 "role": "floor", "phone": "+1-732-983-7841",
                                 "status": "active",
                                 "can_cover_roles": ["cashier", "floor"]}
_CLONE_SAME_LID = {"id": "e011", "name": "Lid Clone", "role": "floor",
                   "phone": "+19045550111", "lid": DUAL_LID, "status": "active",
                   "can_cover_roles": ["cashier", "floor"]}
_CLONE_INACTIVE_SAME_PHONE = {"id": "e012", "name": "Former Holder",
                              "role": "floor", "phone": DUAL_PHONE,
                              "status": "terminated",
                              "can_cover_roles": ["floor"]}


@pytest.mark.parametrize("clone,identifier", [
    (_CLONE_SAME_PHONE, DUAL_PHONE),
    (_CLONE_SAME_PHONE, DUAL_JID),
    (_CLONE_SAME_PHONE, DUAL_LID),
    # Canonicalisation collapses the formatting difference, so this is the same
    # collision wearing a disguise — and the one a human editing roster.json
    # would not spot.
    (_CLONE_SAME_PHONE_REFORMATTED, DUAL_PHONE),
    (_CLONE_SAME_LID, DUAL_LID),
    # Status is NOT a tie-breaker: identify-sender resolves membership
    # status-neutrally on purpose (see `_employee_by_phone_any_status`), so a
    # terminated row sharing the number is just as ambiguous.
    (_CLONE_INACTIVE_SAME_PHONE, DUAL_PHONE),
])
def test_ambiguous_identifier_refuses_to_resolve(env, clone, identifier):
    state = env(roster=_roster_doc(extra_employees=[clone]))
    rc, doc = resolve(state, identifier)
    assert rc != 0, f"resolved {identifier} to {doc.get('employee_id')!r} anyway"
    assert doc.get("role") == "error"
    assert "ambiguous" in str(doc.get("error", "")).lower()
    assert doc.get("employee_id") is None


def test_ambiguous_resolution_does_not_depend_on_roster_file_order(env):
    """The defect this closes, stated as the property it violated.

    Reordering the SAME rows used to change the answer: `e008` before the
    reorder, `e009` after. Whatever the resolution is, it must be the same one.
    """
    forward = _roster_doc(extra_employees=[_CLONE_SAME_PHONE])
    reversed_ = json.loads(json.dumps(forward))
    reversed_["employees"] = [reversed_["employees"][0],
                              reversed_["employees"][2],
                              reversed_["employees"][1]]

    rc_a, doc_a = resolve(env(roster=forward), DUAL_PHONE)
    rc_b, doc_b = resolve(env(roster=reversed_), DUAL_PHONE)

    assert (rc_a, doc_a.get("employee_id")) == (rc_b, doc_b.get("employee_id"))
    assert rc_a != 0


def test_unique_roster_still_resolves_the_dual_principal(env):
    """Regression pin: the fix must not cost the deployed roster its identity."""
    state = env()
    for identifier in (DUAL_PHONE, DUAL_JID, DUAL_LID):
        rc, doc = resolve(state, identifier)
        assert rc == 0, f"{identifier}: rc={rc} {doc}"
        assert doc["employee_id"] == "e008"
        assert doc["roles"] == ["employee", "owner"]


def test_a_phone_held_earlier_by_another_employee_is_not_ambiguous(env):
    """`phone_history` reassignment stays resolvable.

    A closed history window is the SUPPORTED way for a number to have changed
    hands, and `_employee_by_phone_any_status` already honours it. Refusing
    here would break the very case the history field exists for.
    """
    former = {"id": "e013", "name": "Former Holder", "role": "floor",
              "phone": "+19045550113", "status": "active",
              "can_cover_roles": ["floor"],
              "phone_history": [{"phone": DUAL_PHONE,
                                 "effective_from": "2024-01-01T00:00:00Z",
                                 "effective_to": "2025-01-01T00:00:00Z"}]}
    rc, doc = resolve(env(roster=_roster_doc(extra_employees=[former])), DUAL_PHONE)
    assert rc == 0
    assert doc["employee_id"] == "e008"


def test_cf_router_fails_closed_on_an_ambiguous_identity(router):
    """Every consumer, not just the resolver.

    A refusal the callers ignore is not a refusal, so the three seams that
    decide what happens to the dual principal's traffic are checked directly.
    """
    r = router(roster=_roster_doc(extra_employees=[_CLONE_SAME_PHONE]))
    _seed_proposal(r.actions, status="sent")
    token = r.actions.begin_turn_identity()
    try:
        assert r.actions.has_owner_capability(DUAL_LID) is False
        assert r.actions.has_employee_capability(DUAL_LID) is False
        assert r.actions.sent_proposal_ids_for_candidate(DUAL_LID) == []
    finally:
        r.actions.reset_turn_identity(token)


def test_ambiguous_identity_does_not_move_coverage_state(router):
    """End to end: an ambiguous YES records nothing and sends nothing."""
    r = router(roster=_roster_doc(extra_employees=[_CLONE_SAME_PHONE]))
    _seed_proposal(r.actions, status="sent")

    assert r.hooks.pre_gateway_dispatch(_inbound(DUAL_LID, "YES")) is None
    assert r.calls == []
    assert _routed_rows(r.actions) == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. CHARACTERIZATION — what the boundary currently permits, deliberately
# ═══════════════════════════════════════════════════════════════════════════

def _load_sick_call():
    loader = importlib.machinery.SourceFileLoader(
        "handle_shift_sick_call_dualrole", str(SICK_CALL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_the_dual_principal_is_selectable_as_a_coverage_candidate(tmp_path):
    """POLICY, not safety: whether an owner may cover a shift is the operator's
    call. The selection kernel has no owner exclusion, and this pins that fact
    rather than encoding a decision nobody has made.
    """
    from schemas import Roster  # noqa: WPS433 — Linux-only import (fcntl)
    mod = _load_sick_call()
    mod.PENDING_PATH = tmp_path / "pending.json"
    mod.PENDING_PATH.write_text('{"proposals": {}}', encoding="utf-8")
    roster = Roster.model_validate(_roster_doc())
    absent = next(e for e in roster.employees if e.id == "e001")
    entry = roster.schedule[ABSENT_DATE][0]

    chosen = mod._best_candidate(roster, absent, entry, ABSENT_DATE)

    assert chosen is not None and chosen.id == "e008"
    assert str(chosen.phone) == DUAL_PHONE, (
        "the coverage ask is addressed to the owner's own WhatsApp number")


def test_the_dual_principal_is_excluded_only_as_their_own_replacement(tmp_path):
    """The one exclusion that DOES exist is `emp.id == absent.id`, and it is
    about the shift, not about being the owner."""
    from schemas import Roster  # noqa: WPS433
    mod = _load_sick_call()
    mod.PENDING_PATH = tmp_path / "pending.json"
    mod.PENDING_PATH.write_text('{"proposals": {}}', encoding="utf-8")
    roster = Roster.model_validate(_roster_doc())
    absent = next(e for e in roster.employees if e.id == "e008")
    entry = roster.schedule[ABSENT_DATE][0]

    assert mod._best_candidate(roster, absent, entry, ABSENT_DATE) is None


def test_owner_and_candidate_grammars_cannot_both_claim_one_message(router):
    """Handler ORDER must not be what decides the dual principal's turn.

    F8 needs a `#XXXXX` code; the candidate classifier needs the message to
    reduce to exactly one answer token. A code is a second token, so no message
    satisfies both — which is why the F8-before-candidate ordering is not
    load-bearing for this principal.
    """
    r = router()
    for text in ("#ABCDE", "yes #ABCDE", "approve #ABCDE", "#ABCDE approve",
                 "no #ABCDE", "DENY #ABCDE"):
        assert r.hooks._classify_candidate_reply(text) is None, text
    for text in ("YES", "yes", "NO", "no", "yep", "nope"):
        assert r.hooks._CODE_PATTERN.search(text) is None, text


# ═══════════════════════════════════════════════════════════════════════════
# 6. OWNER AUTHORITY IS IDENTIFIER-INDEPENDENT
#
# `is_owner_chat` used to apply the capability-aware fallback only when the
# chat_id ended in `@lid`, so ONE authorized principal was owner by LID and
# non-owner by phone-JID. `roles` is documented as branch-independent and
# `OwnerAuthorizedIdentity` grants membership to the principal, not to one of
# their identifiers -- so the two forms must agree.
#
# These tests assert MEMBERSHIP, never the identifier shape: the point is that
# no ordinary employee gains anything. Each negative case is a real principal
# the resolver knows about, not an unroutable string, so a fix that simply
# refused everything would fail them.
# ═══════════════════════════════════════════════════════════════════════════

EMPLOYEE_JID = "19045550101@s.whatsapp.net"   # e001, active, employee-only
EMPLOYEE_LID = "555000111222333@lid"
UNKNOWN_JID = "19999999999@s.whatsapp.net"


def _roster_with_employee_lid() -> dict:
    doc = _roster_doc()
    doc["employees"][0]["lid"] = EMPLOYEE_LID
    return doc


@pytest.mark.parametrize("identifier,expected,why", [
    (OWNER_JID, True, "primary owner phone-JID -- the exact self_chat_jid fast path"),
    (OWNER_LID, True, "primary owner by LID"),
    (DUAL_LID, True, "authorized dual-role identity via @lid (worked before)"),
    (DUAL_JID, True, "SAME authorized identity via phone-JID (the repair)"),
    (EMPLOYEE_JID, False, "ordinary active employee phone-JID is NOT owner"),
    (EMPLOYEE_LID, False, "ordinary active employee @lid is NOT owner"),
    (UNKNOWN_JID, False, "unknown identity is NOT owner"),
])
def test_owner_authority_by_identifier(router, identifier, expected, why):
    r = router(roster=_roster_with_employee_lid(), tag="ownerauth")
    assert r.actions.is_owner_chat(identifier) is expected, why


def test_both_identifiers_of_one_authorized_principal_agree(router):
    """The invariant, stated directly: same human, same answer, either form."""
    r = router(tag="ownerauth_agree")
    assert r.actions.is_owner_chat(DUAL_JID) == r.actions.is_owner_chat(DUAL_LID)
    assert r.actions.is_owner_chat(OWNER_JID) == r.actions.is_owner_chat(OWNER_LID)


def test_owner_authority_agrees_with_the_shared_membership_check(router):
    """`is_owner_chat` must not become a second authorization system.

    Every identifier that is not the configured `self_chat_jid` fast path has
    to give the same answer as `has_owner_capability`, which is the documented
    membership surface.
    """
    r = router(roster=_roster_with_employee_lid(), tag="ownerauth_same")
    for ident in (OWNER_LID, DUAL_JID, DUAL_LID, EMPLOYEE_JID, EMPLOYEE_LID, UNKNOWN_JID):
        assert r.actions.is_owner_chat(ident) == r.actions.has_owner_capability(ident), ident


def test_ambiguous_identifier_is_not_owner(router):
    """Fail closed: two roster rows claiming one number resolves to nobody.

    Guards the widening this repair could have introduced -- routing every
    identifier through the resolver must not let an ambiguous one pass.
    """
    clash = _roster_doc(extra_employees=[
        {"id": "e009", "name": "Impostor", "role": "floor", "phone": DUAL_PHONE,
         "status": "active", "languages": ["en"], "can_cover_roles": ["floor"]}])
    r = router(roster=clash, tag="ownerauth_ambig")
    assert r.actions.is_owner_chat(DUAL_JID) is False
    assert r.actions.is_owner_chat(DUAL_PHONE) is False


def test_resolver_failure_is_not_owner(router, monkeypatch):
    """A broken resolver denies authority; it never grants it."""
    r = router(tag="ownerauth_resolverfail")
    monkeypatch.setattr(r.actions, "_invoke_identify_sender",
                        lambda ident: r.actions._IdentityResolution(False, {}))
    assert r.actions.is_owner_chat(DUAL_JID) is False
    assert r.actions.is_owner_chat(DUAL_LID) is False
    # ...but the configured owner stays reachable with no resolver at all.
    assert r.actions.is_owner_chat(OWNER_JID) is True


def test_unreadable_config_is_not_owner(router):
    """Unchanged from before the repair: any exception returns False."""
    r = router(tag="ownerauth_badcfg")
    r.actions.CONFIG_PATH = r.state.tmp / "does-not-exist.yaml"
    assert r.actions.is_owner_chat(DUAL_JID) is False
    assert r.actions.is_owner_chat(OWNER_JID) is False


# ---- consumer 1: F8 owner approval -----------------------------------------

@pytest.mark.parametrize("identifier,tag", [(DUAL_LID, "lid"), (DUAL_JID, "jid")])
def test_f8_owner_approval_reached_by_either_identifier(router, identifier, tag):
    """`#XXXXX` fired F8 by LID and silently did nothing by phone-JID."""
    r = router(tag=f"f8_{tag}")
    _seed_proposal(r.actions, status="awaiting_owner_approval")
    r.hooks._pre_gateway_dispatch_impl(_inbound(identifier, "#ABCDE"))
    updates = [c for c in r.calls if c[0] == "update_proposal_status"]
    assert updates, f"F8 did not run for {identifier}"
    rows = _routed_rows(r.actions)
    assert [x["routed_to_skill"] for x in rows] == ["handle_owner_command"]
    assert rows[0]["sender_role"] == "owner", "owner act must audit owner authority"


def test_f8_is_not_reachable_by_an_ordinary_employee(router):
    """The repair must not turn arbitrary employees into owners."""
    r = router(roster=_roster_with_employee_lid(), tag="f8_emp")
    _seed_proposal(r.actions, status="awaiting_owner_approval")
    for ident in (EMPLOYEE_JID, EMPLOYEE_LID):
        r.hooks._pre_gateway_dispatch_impl(_inbound(ident, "#ABCDE"))
    assert not [c for c in r.calls if c[0] == "update_proposal_status"]


# ---- consumer 2: _try_automation_control -----------------------------------

@pytest.mark.parametrize("identifier,tag", [(DUAL_LID, "lid"), (DUAL_JID, "jid")])
def test_automation_control_sees_one_owner_by_either_identifier(router, identifier, tag):
    """The gap here was worse than losing the owner branch.

    `_try_automation_control` reads `is_owner = is_owner_chat(chat_id)` and
    then routes `not is_owner` into the CUSTOMER branch -- so before the
    repair the phone-JID form of an owner was treated as a customer. Asserted
    on the same seam the kernel reads, because the surrounding behaviour is
    DORMANT behind CATERING_AUTOMATION_CONTROL_ENABLED and this test must not
    arm it.
    """
    r = router(tag=f"autoctl_{tag}")
    assert r.actions.is_owner_chat(identifier) is True


def test_automation_control_still_treats_an_employee_as_non_owner(router):
    r = router(roster=_roster_with_employee_lid(), tag="autoctl_emp")
    assert r.actions.is_owner_chat(EMPLOYEE_JID) is False


# ---- the widening this repair could have introduced --------------------------
#
# identify-sender's invalid branch falls back to `E164Phone.from_any`, which
# STRIPS non-digit characters -- so `<owner-digits>@g.us` canonicalizes to the
# owner's number and resolves with roles ["owner"]. The old `endswith("@lid")`
# guard blocked that by ACCIDENT. Routing every identifier through the resolver
# without a shape allowlist would have handed owner authority to a group JID.
#
# These are the exact strings that resolved as owner in the probe. Each must be
# refused at `is_owner_chat` even though identify-sender still resolves it.

OWNER_DIGITS = OWNER_JID.split("@")[0]

@pytest.mark.parametrize("hostile", [
    f"{OWNER_DIGITS}@g.us",                 # GROUP JID carrying the owner's digits
    f"{OWNER_JID}@lid",                     # suffix confusion
    OWNER_DIGITS,                           # bare digits
    f"  {OWNER_JID}  ",                     # whitespace-padded
    OWNER_PHONE,                            # bare E164 (never a chat_id)
    f"{DUAL_JID}@g.us",                     # same trick with the dual identity
])
def test_owner_authority_is_refused_for_unsupported_identifier_shapes(router, hostile):
    r = router(tag="ownerauth_shape")
    assert r.actions.is_owner_chat(hostile) is False, hostile


def test_the_hostile_shapes_still_resolve_as_owner_downstream(router):
    """The control that makes the test above meaningful.

    If identify-sender simply refused these strings, the allowlist would be
    untested decoration. It does NOT refuse them -- it resolves the group-JID
    form to owner membership -- so the allowlist is the only thing standing
    between a group chat and owner authority.
    """
    r = router(tag="ownerauth_shape_ctl")
    assert r.actions.has_owner_capability(f"{OWNER_DIGITS}@g.us") is True
    assert r.actions.is_owner_chat(f"{OWNER_DIGITS}@g.us") is False


def test_the_two_supported_shapes_are_still_admitted(router):
    """The allowlist must not have re-broken the thing this PR fixes."""
    r = router(tag="ownerauth_shape_pos")
    assert r.actions.is_owner_chat(DUAL_JID) is True     # phone-JID
    assert r.actions.is_owner_chat(DUAL_LID) is True     # LID
    assert r.actions.is_owner_chat(OWNER_JID) is True    # fast path


# ---- owner membership is config-ANCHORED but roster-REACHABLE ---------------
#
# `_resolve_principal` widens the identifiers from the matched roster row
# before asking `_match_owner_identity`, so a roster row that pairs another
# phone with `owner.lid` resolves as owner. This is characterization, not
# endorsement: the LID direction was already live before the phone-JID repair,
# and the repair adds its mirror. Pinned so that if anyone later decides owner
# membership should be config-ONLY, these tests fail and say where to look.

def test_roster_row_holding_the_owner_lid_reaches_owner_membership(env):
    """Phone side. Reachable only AFTER the phone-JID repair."""
    roster = _roster_doc(extra_employees=[
        {"id": "e099", "name": "Holds Owner LID", "role": "floor",
         "phone": "+15125550199", "lid": OWNER_LID, "status": "active",
         "languages": ["en"], "can_cover_roles": ["floor"]}])
    rc, doc = resolve(env(roster=roster), "15125550199@s.whatsapp.net")
    assert rc == 0
    assert "owner" in (doc.get("roles") or []), (
        "roster-mediated owner reachability changed -- update the docstring in "
        "actions.is_owner_chat, which documents this as a known property")


def test_roster_row_holding_the_owner_phone_reaches_owner_membership(env):
    """LID side. Pre-existing: True before the repair as well."""
    roster = _roster_doc(extra_employees=[
        {"id": "e098", "name": "Holds Owner Phone", "role": "floor",
         "phone": OWNER_PHONE, "lid": "888000111222333@lid", "status": "active",
         "languages": ["en"], "can_cover_roles": ["floor"]}])
    rc, doc = resolve(env(roster=roster), "888000111222333@lid")
    assert rc == 0
    assert "owner" in (doc.get("roles") or [])


def test_a_roster_row_with_neither_owner_identifier_gets_nothing(env):
    """The control. Without this, the two tests above would also pass if the
    resolver simply called everybody an owner."""
    roster = _roster_doc(extra_employees=[
        {"id": "e097", "name": "Unrelated", "role": "floor",
         "phone": "+15125550199", "lid": "999000111222333@lid",
         "status": "active", "languages": ["en"], "can_cover_roles": ["floor"]}])
    rc, doc = resolve(env(roster=roster), "15125550199@s.whatsapp.net")
    assert rc == 0
    assert (doc.get("roles") or []) == ["employee"]
