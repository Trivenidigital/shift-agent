"""The employee's YES/NO reply must reach state, or she is reported unresponsive.

The coverage loop had no return leg for the candidate. `handle_candidate_response`
exists ONLY as a SKILL.md — no script, no bin, no cf-router branch — and the SKILL
dispatcher cannot run on the box (the `skills` toolset is disabled). So:

    owner approves -> candidate is messaged -> she replies -> NOTHING records it
    -> 30 minutes later shift-agent-proposal-sweep tells the owner she never
       responded.

The sweep's guard is correct and simply cannot fire: `find_stale_sent_proposals`
only considers proposals still in status `sent`, and nothing ever moved this one
out of `sent`. #734 made that newly reachable by wiring the owner's approval, so a
real employee can now be asked for her time, ignored, and then reported to her
employer as unresponsive.

These tests pin the deterministic branch that records the reply.

The grammar is not invented here. The message she receives says, verbatim:

    "Can you cover the {absent_shift} {absent_role} shift? Reply YES or NO."
        -- src/agents/shift/templates/coverage_message_to_candidate.txt
           (verified byte-identical to the deployed copy at
            /opt/shift-agent/templates/ on 2026-08-22)

so YES and NO are the words that must work, and a refusal must never be read as
consent.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import platform
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="cf-router actions imports safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "src" / "plugins" / "cf-router"
PLATFORM_DIR = REPO / "src" / "platform"


def _load_plugin_modules():
    """Load hooks + actions under a synthetic package (the dir name is hyphenated).

    Mirrors tests/test_cf_router_plugin.py, including leaving `schemas`/`safe_io`
    in sys.modules so co-resident test modules keep their bindings.
    """
    if str(PLATFORM_DIR) not in sys.path:
        sys.path.insert(0, str(PLATFORM_DIR))
    pkg_name = "cf_router_candidate_pkg"
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
            f"{pkg_name}.{name}", PLUGIN_DIR / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{name}"] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["hooks"], mods["actions"]


CANDIDATE_JID = "15550100077@s.whatsapp.net"
EMP_ID = "e0007"   # real format: schemas.EmployeeId is ^e\d{3,}$


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    hooks_mod, actions_mod = _load_plugin_modules()
    pending = tmp_path / "pending.json"
    pending.write_text('{"proposals": {}}', encoding="utf-8")
    actions_mod.PENDING_PATH = pending
    actions_mod.LOG_PATH = tmp_path / "decisions.log"
    actions_mod.PYTHON_BIN = Path(sys.executable)
    monkeypatch.setattr(
        actions_mod, "identify_sender_metadata",
        lambda ident: {"role": "employee", "employee_id": EMP_ID},
    )
    return hooks_mod, actions_mod, pending


def _seed_proposal(pending: Path, *, proposal_id="P0001", status="sent",
                   candidate=EMP_ID, code="#ABCDE"):
    doc = json.loads(pending.read_text(encoding="utf-8"))
    doc["proposals"][proposal_id] = {
        "proposal_id": proposal_id,
        "code": code,
        "status": status,
        "candidate_employee_id": candidate,
        "candidate_name": "Priya",
        "absent_employee_id": "e0001",
        "absent_date": "2026-08-23",
        "absent_shift": "evening",
        "absent_role": "server",
        "sent_ts": "2026-08-22T10:00:00+00:00",
    }
    pending.write_text(json.dumps(doc), encoding="utf-8")


# ─── the classifier ─────────────────────────────────────────────────────────
#
# Table-driven because the risk is asymmetric: reading a refusal as consent puts
# a real person on a shift she said she could not work.

_ACCEPT = [
    "YES", "yes", "Yes", "yes please", "yes, thanks", "yep", "yeah", "sure",
    "ok", "okay", "y", "👍",
]
_DECLINE = [
    "NO", "no", "No", "no thanks", "nope", "nah", "I can't", "i cant",
    "cannot", "unable", "👎",
]
_AMBIGUOUS = [
    # conditional / partial — a half-agreement is not an agreement
    "yes if my sister can babysit", "yes but only until 3", "yes only the first half",
    "maybe", "might be able to", "depends on my ride", "not sure yet",
    # questions
    "what time is it?", "can I let you know tomorrow?", "yes?",
    # both signals present
    "I can't say yes", "no I mean yes",
    # the "no problem" trap: reads as refusal token-wise, means the opposite
    "no problem I'll cover it", "no worries I can do it",
    # nothing to act on
    "", "   ", "ok so who else is working", "thanks for letting me know",
    # AFFIRMATIVE-PREFIXED REFUSALS. Every one of these was classified
    # ACCEPTED by the first implementation, which admitted an answer token
    # anywhere in the first two tokens of a <=4-token message without
    # requiring the rest to be empty. An accept is IRREVERSIBLE --
    # `LEGAL_TRANSITIONS["accepted"]` is empty, so no operator command, sweep
    # or fsck repairs it -- which makes this the worst direction to be wrong in.
    "ok im busy", "ok let me see", "ok never mind", "ok ask someone else",
    "yes im busy", "ok call me", "yes but im late", "ok i cant though",
    "no im free actually",
]


@pytest.mark.parametrize("text", _ACCEPT)
def test_affirmative_replies_classify_as_accepted(plugin, text):
    hooks_mod, _actions, _pending = plugin
    assert hooks_mod._classify_candidate_reply(text) == "accepted", text


@pytest.mark.parametrize("text", _DECLINE)
def test_refusals_classify_as_declined(plugin, text):
    hooks_mod, _actions, _pending = plugin
    assert hooks_mod._classify_candidate_reply(text) == "declined", text


@pytest.mark.parametrize("text", _AMBIGUOUS)
def test_ambiguous_replies_change_nothing(plugin, text):
    hooks_mod, _actions, _pending = plugin
    assert hooks_mod._classify_candidate_reply(text) is None, text


def test_no_refusal_is_ever_read_as_consent(plugin):
    """The asymmetric one, stated as its own invariant rather than left implicit
    in the table: nothing in the refusal set may classify as accepted."""
    hooks_mod, _actions, _pending = plugin
    for text in _DECLINE + _AMBIGUOUS:
        assert hooks_mod._classify_candidate_reply(text) != "accepted", text


# ─── exactly-one-proposal, or refuse ────────────────────────────────────────


def test_reply_with_exactly_one_sent_proposal_is_recorded(plugin, monkeypatch):
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append((a, kw)) or 0)
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)

    result = hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1")

    assert result is not None and result.get("action") == "skip"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "P0001"
    assert args[1] == "accepted"
    assert kwargs.get("actor") == "candidate"
    assert kwargs.get("response_message") == "YES"


def test_two_sent_proposals_for_one_candidate_refuse_rather_than_guess(plugin, monkeypatch):
    """SKILL.md §1 says pick the most recent by sent_ts. This branch refuses
    instead, deliberately: picking wrong records the WRONG shift as covered,
    and the sibling POOL_SHIFT branch already refuses the same ambiguity."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending, proposal_id="P0001", code="#ABCDE")
    _seed_proposal(pending, proposal_id="P0002", code="#BCDEF")
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append((a, kw)) or 0)

    assert hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1") is None
    assert calls == []


def test_no_sent_proposal_records_nothing(plugin, monkeypatch):
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending, status="accepted")
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append((a, kw)) or 0)

    assert hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1") is None
    assert calls == []


def test_a_proposal_for_a_different_candidate_is_not_touched(plugin, monkeypatch):
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending, candidate="e0099")
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append((a, kw)) or 0)

    assert hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1") is None
    assert calls == []


def test_ambiguous_reply_mutates_nothing(plugin, monkeypatch):
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append((a, kw)) or 0)

    assert hooks_mod._try_candidate_response(
        text="can I let you know tomorrow?", chat_id=CANDIDATE_JID,
        message_id="wamid.1") is None
    assert calls == []


def test_unverified_sender_records_nothing(plugin, monkeypatch):
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    monkeypatch.setattr(actions_mod, "identify_sender_metadata",
                        lambda ident: {"role": "unknown"})
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append((a, kw)) or 0)

    assert hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1") is None
    assert calls == []


def test_kernel_refusal_does_not_claim_success(plugin, monkeypatch):
    """If update-proposal-status refuses (illegal transition, lock timeout), the
    branch must fall through rather than report the reply as recorded."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: 9)
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)

    assert hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1") is None


def test_double_reply_records_once(plugin, monkeypatch):
    """Idempotency. The first reply moves the proposal out of `sent`; the second
    finds no `sent` proposal for this candidate and must record nothing —
    the same two-guard shape the owner-side branch uses."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    calls = []

    def _apply(proposal_id, new_status, **kw):
        calls.append((proposal_id, new_status))
        doc = json.loads(pending.read_text(encoding="utf-8"))
        doc["proposals"][proposal_id]["status"] = new_status
        pending.write_text(json.dumps(doc), encoding="utf-8")
        return 0

    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status", _apply)
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)

    first = hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.1")
    second = hooks_mod._try_candidate_response(
        text="YES", chat_id=CANDIDATE_JID, message_id="wamid.2")

    assert first is not None
    assert second is None
    assert calls == [("P0001", "accepted")]


def test_routing_is_audited_before_the_state_change(plugin, monkeypatch):
    """dispatcher_routed precedes delegation — skipping it is a silent
    routing-correctness regression per the deployed-pattern checklist."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    order = []
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed",
                        lambda **kw: order.append(("audit", kw.get("routed_to_skill"))))
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: order.append(("apply", a[1])) or 0)

    hooks_mod._try_candidate_response(
        text="no", chat_id=CANDIDATE_JID, message_id="wamid.1")

    assert order == [("audit", "handle_candidate_response"), ("apply", "declined")]


# ─── the whole point: the false "she never replied" alert must stop ─────────


def _real_store(status: str, sent_ts, response_ts=None):
    """A real PendingStore, parsed exactly as shift-agent-proposal-sweep parses it.

    Not a stub with `.status`/`.sent_ts` attributes: the sweep calls
    `find_stale_sent_proposals(store.proposals, ...)` on real schema objects, and
    a stub would let a schema change drift out from under this test silently.
    """
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import PendingStore  # noqa: E402

    row = {
        "proposal_id": "P0001",
        "code": "#ABCDE",
        "status": status,
        "created_ts": "2026-08-22T09:00:00+00:00",
        "last_updated_ts": "2026-08-22T09:00:00+00:00",
        "absent_employee_id": "e0001",
        "absent_date": "2026-08-23",
        "absent_shift": "evening",
        "absent_role": "server",
        "absent_reason": "sick",
        "input_message": "I am sick tomorrow",
        "message_id": "wamid.abc",
        "candidate_employee_id": EMP_ID,
    }
    if status == "sent":
        row["sent_ts"] = sent_ts.isoformat()
    else:
        row["sent_ts"] = sent_ts.isoformat()
        row["response_ts"] = (response_ts or sent_ts).isoformat()
        row["response_message"] = "YES"
        row.pop("sent_ts")
    return PendingStore.model_validate({"proposals": {"P0001": row}})


def test_recording_the_reply_makes_the_sweep_skip_the_proposal():
    """Constraint 3, verified against the real guard rather than assumed.

    `find_stale_sent_proposals` is what decides whether the owner is told she
    never replied. Drive it on real schema objects, with a `sent_ts` well past
    the TTL, so the ONLY difference between the two runs is the status this
    branch writes.
    """
    sys.path.insert(0, str(PLATFORM_DIR))
    from proposal_sweep import find_stale_sent_proposals  # noqa: E402
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(hours=5)

    before = find_stale_sent_proposals(_real_store("sent", long_ago).proposals, now, 30)
    assert before == ["P0001"], (
        "fixture is wrong: an unanswered proposal past the TTL must be flagged first, "
        "or the after-case proves nothing"
    )

    for recorded in ("accepted", "declined"):
        after = find_stale_sent_proposals(
            _real_store(recorded, long_ago).proposals, now, 30)
        assert after == [], (
            f"after recording the reply as {recorded} the sweep still reports her "
            "as unresponsive"
        )


def test_the_transition_this_branch_requests_is_legal():
    """The kernel validates transitions, but a branch that always asks for an
    illegal one would fail closed forever and look like 'no replies arrive'."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import LEGAL_TRANSITIONS  # noqa: E402

    assert "accepted" in LEGAL_TRANSITIONS["sent"]
    assert "declined" in LEGAL_TRANSITIONS["sent"]
    # And terminal, which is what makes a double reply a no-op in the kernel.
    assert LEGAL_TRANSITIONS.get("accepted", frozenset()) == frozenset()
    assert LEGAL_TRANSITIONS.get("declined", frozenset()) == frozenset()


def test_the_sweeps_own_transition_is_refused_after_a_reply_is_recorded():
    """The sweep's SECOND gate, which the finder test above does not cover.

    `_sweep_one` is transition-first: it calls
    `update-proposal-status <pid> no_response_timeout` and alerts ONLY on rc 0.
    A candidate who replied between the snapshot and the transition yields an
    illegal transition (rc 9) and no alert.

    So writing *a* status is not sufficient — it has to be a status from which
    `no_response_timeout` is illegal, or the false "she never replied" page still
    goes out through the race window the finder cannot see. Both statuses this
    branch writes are terminal, which is exactly what makes that transition
    refuse.
    """
    sys.path.insert(0, str(PLATFORM_DIR))
    from schemas import LEGAL_TRANSITIONS  # noqa: E402

    assert "no_response_timeout" in LEGAL_TRANSITIONS["sent"], (
        "fixture is wrong: the sweep must be able to time out an unanswered proposal"
    )
    for recorded in ("accepted", "declined"):
        assert "no_response_timeout" not in LEGAL_TRANSITIONS.get(recorded, frozenset()), (
            f"after recording {recorded} the sweep's own transition would still "
            "succeed and page the owner"
        )


# ─── the owner must still be told the shift is uncovered ────────────────────
#
# Recording the reply moves the proposal out of `sent`, which is what stops the
# false "she never replied" page. But that page was ALSO the only real-time
# signal that the shift still needs covering. Suppressing it without replacing
# it is a net regression on the live box: a mis-attributed page still gets a
# human to arrange coverage; silence does not.


def _run(hooks_mod, text="YES"):
    return hooks_mod._try_candidate_response(
        text=text, chat_id=CANDIDATE_JID, message_id="wamid.1")


@pytest.fixture
def wired(plugin, monkeypatch):
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    alerts = []
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status", lambda *a, **kw: 0)
    monkeypatch.setattr(
        actions_mod, "fire_pushover_alert",
        lambda title, body, priority=2: alerts.append((title, body, priority)))
    return hooks_mod, actions_mod, pending, alerts


def test_a_decline_still_pages_the_owner_that_the_shift_is_uncovered(wired):
    hooks_mod, _a, _p, alerts = wired

    assert _run(hooks_mod, "no") is not None

    assert len(alerts) == 1, "declining silently removes the only uncovered-shift signal"
    title, body, priority = alerts[0]
    assert "Priya" in body
    assert "server" in body and "2026-08-23" in body
    assert "uncovered" in body.lower()
    assert priority >= 1, "an uncovered shift is actionable, not informational"


def test_an_accept_tells_the_owner_coverage_is_arranged(wired):
    hooks_mod, _a, _p, alerts = wired

    assert _run(hooks_mod, "YES") is not None

    assert len(alerts) == 1
    title, body, priority = alerts[0]
    assert "Priya" in body
    assert "covered" in body.lower()


def test_the_owner_is_not_paged_when_nothing_was_recorded(wired, monkeypatch):
    """No page may claim an outcome the kernel refused to write."""
    hooks_mod, actions_mod, _p, alerts = wired
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status", lambda *a, **kw: 9)

    assert _run(hooks_mod, "YES") is None
    assert alerts == []


def test_an_ambiguous_reply_pages_nobody(wired):
    hooks_mod, _a, _p, alerts = wired
    assert _run(hooks_mod, "maybe later") is None
    assert alerts == []


def test_a_failed_page_does_not_undo_the_recorded_reply(plugin, monkeypatch):
    """The state change is the primary operation. Pushover being down must not
    turn a recorded reply into an unrecorded one."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status", lambda *a, **kw: 0)

    def _boom(*a, **kw):
        raise RuntimeError("pushover down")

    monkeypatch.setattr(actions_mod, "fire_pushover_alert", _boom)

    result = _run(hooks_mod, "YES")
    assert result is not None and result.get("action") == "skip"


# ─── end to end, through the real dispatch entry point ──────────────────────


def test_end_to_end_through_pre_gateway_dispatch(plugin, monkeypatch):
    """Every other test calls _try_candidate_response directly, which cannot see
    an earlier branch pre-empting it. This one enters where Hermes does."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    calls = []
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)
    monkeypatch.setattr(actions_mod, "fire_pushover_alert", lambda *a, **kw: None)
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: False)
    monkeypatch.setattr(actions_mod, "is_verified_employee_chat", lambda cid: True)
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append(a) or 0)

    event = SimpleNamespace(text="YES", chat_id=CANDIDATE_JID, message_id="wamid.e2e")
    result = hooks_mod.pre_gateway_dispatch(event)

    assert result is not None and result.get("action") == "skip"
    assert calls and calls[0][1] == "accepted"


def test_f9_preempts_sick_call_shaped_declines_end_to_end(plugin, monkeypatch):
    """KNOWN RESIDUAL, pinned so it is visible rather than assumed away.

    `_is_sick_call` + `has_pending_candidate_response` returns None BEFORE this
    branch, so the most natural refusals record nothing and the false alert
    still fires at 30 minutes. The classifier would decline all of these. It is
    not a regression — nothing recorded them before either — but the branch does
    not close the loop for them, and a test that hid that would be worse than
    the gap.
    """
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)
    monkeypatch.setattr(actions_mod, "fire_pushover_alert", lambda *a, **kw: None)
    monkeypatch.setattr(actions_mod, "is_owner_chat", lambda cid: False)
    monkeypatch.setattr(actions_mod, "is_verified_employee_chat", lambda cid: True)
    calls = []
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status",
                        lambda *a, **kw: calls.append(a) or 0)

    for text in ("cant come", "cannot come", "can't make it", "unable to work"):
        if not hooks_mod._is_sick_call(text):
            continue
        event = SimpleNamespace(text=text, chat_id=CANDIDATE_JID, message_id="wamid.x")
        hooks_mod.pre_gateway_dispatch(event)

    assert calls == [], (
        "F9 no longer pre-empts these -- good news, but update the residual note "
        "in the branch docstring and the PR body rather than leaving it stale"
    )


def test_a_hyphen_prefixed_reply_is_not_read_as_an_argparse_option():
    """`--response-message -yes` makes argparse treat the reply as an option,
    exit 2, and fail closed on a perfectly clear answer. Assert the built
    argv keeps it attached to its flag."""
    hooks_mod, actions_mod = _load_plugin_modules()
    captured = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Result()

    import subprocess as _sp
    orig = _sp.run
    actions_mod.subprocess.run = _fake_run
    try:
        rc = actions_mod.invoke_update_proposal_status(
            "P0001", "declined", cause="candidate_declined",
            actor="candidate", response_message="-yes",
        )
    finally:
        actions_mod.subprocess.run = orig

    assert rc == 0
    assert "--response-message=-yes" in captured["cmd"], captured["cmd"]
    assert "-yes" not in [c for c in captured["cmd"] if c == "-yes"], (
        "the reply is still a bare argv element argparse will read as an option"
    )


def test_a_kernel_refusal_is_audited_so_the_routed_row_is_explained(plugin, monkeypatch):
    """dispatcher_routed is written before delegating (deployed convention, and
    what the sibling branch does). When the kernel then refuses, an explaining
    row must follow, or the audit shows a route to a handler that changed
    nothing and never says why."""
    hooks_mod, actions_mod, pending = plugin
    _seed_proposal(pending)
    monkeypatch.setattr(actions_mod, "audit_dispatcher_routed", lambda **kw: None)
    monkeypatch.setattr(actions_mod, "invoke_update_proposal_status", lambda *a, **kw: 9)
    monkeypatch.setattr(actions_mod, "fire_pushover_alert", lambda *a, **kw: None)
    audited = []
    monkeypatch.setattr(actions_mod, "audit_intercepted",
                        lambda **kw: audited.append(kw))

    assert _run(hooks_mod, "YES") is None
    assert len(audited) == 1
    assert audited[0]["reason"] == "error"
    assert audited[0]["subprocess_rc"] == 9
    assert "P0001" in audited[0]["detail"]
