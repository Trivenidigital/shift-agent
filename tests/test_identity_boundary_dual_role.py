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
