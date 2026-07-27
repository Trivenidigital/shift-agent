"""Governed frozen incident-replay suite runner (charter §9).

Loads every declarative oracle fixture under ``tests/incident_replay/fixtures/``,
enforces the fixed §9.3 oracle schema, proves the fixtures are PII-safe, and
anchors each incident to the deterministic test(s) that actually exercise the
behavior. Execution safety is inherited from ``tests/conftest.py`` (autouse
fake-bridge sink) and ``safe_io.LiveBridgeSendInTestError`` (fail-closed on any
live-bridge / unknown destination). See ``tests/incident_replay/GOVERNANCE.md``.

28-SEND SPIRAL — GRADUATED to a two-mode assertion (no xfail).
Rather than a single strict-xfail whose meaning flips with an env default, the
incident is now proven in BOTH transport-budget configurations by exercising the
REAL ``safe_io.turn_send_budget_gate``:
  * ``test_28_send_spiral_baseline_unprotected_when_budget_off`` — with the hard
    per-turn budget OFF (the current production default), the gate performs NO
    enforcement (returns ``None``), so a 28-send single-turn spiral is NOT
    bounded. This asserts the known-unsafe BASELINE explicitly and must never be
    read as protection.
  * ``test_28_send_spiral_bounded_when_budget_enabled`` — with the budget
    installed AND enabled, a single inbound turn is bounded to the configured cap
    (exactly LIMIT finalized sends admitted, the rest suppressed; drafts don't
    consume the finalized budget; retries past exhaustion re-suppressed). This is
    the PROTECTED behavior and is a real green PASS.
Both configurations are green; there is no remaining xfail for the protected
configuration. The incident oracle is unchanged (not weakened) — only the
assertion graduated from "known failure" to "proven in both budget states".

Windows: ``fcntl`` is Linux-only; ``ensure_fcntl_stub()`` (mirrors the repo
pattern in tests/fixtures_fleet.py) makes safe_io importable here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()
import safe_io  # noqa: E402  (after the fcntl stub is installed)


# ── fixed, version-controlled suite location (charter §9.2) ──────────────────
FIXTURE_DIR = Path(__file__).resolve().parent / "incident_replay" / "fixtures"

# The exact §9.3 oracle key set. A fixture MUST carry these keys and no others.
REQUIRED_ORACLE_KEYS = frozenset({
    "incident_id",
    "description",
    "pseudonymized_transcript",
    "initial_state",
    "expected_route",
    "expected_mutations",
    "forbidden_mutations",
    "expected_logical_sends",
    "expected_audit_identities",
    "expected_final_state",
    "pii_safe",
    "additive_only",
})

# The 11 charter §9.1 incidents. The frozen suite is additive-only: this set may
# GROW (append new incidents) but an incident is never removed or renamed.
CHARTER_INCIDENTS = frozenset({
    "stale-flyer-swallows-fresh-inquiry",
    "28-send-spiral",
    "180-guest-wedding-two-menus",
    "duplicate-event-ambiguity",
    "distinct-event-creation",
    "option2-plus-quote-compound",
    "redundant-option2",
    "branchB-amendment-success",
    "branchB-amendment-failure",
    "missing-outbound-id",
    "recovery-watchdog-timer-reversal",
})

# incident_id -> the deterministic test module(s) that exercise the behavior on
# the exact release. The suite asserts each mapped module exists so the frozen
# oracles stay anchored to executable proof (charter §9: "for the incidents that
# map to existing deterministic tests — assert consistency"). Deleting a mapped
# test turns this suite red.
INCIDENT_TO_DETERMINISTIC_TESTS = {
    "stale-flyer-swallows-fresh-inquiry": ["test_cf_router_catering_escape_gate.py"],
    "28-send-spiral": ["test_gateway_send_throttle.py", "test_turn_send_budget.py"],
    "180-guest-wedding-two-menus": ["test_catering_turn_arbitration_e2e.py"],
    "duplicate-event-ambiguity": ["test_catering_pra_reachability.py"],
    "distinct-event-creation": ["test_catering_turn_arbitration_e2e.py"],
    "option2-plus-quote-compound": [
        "test_catering_turn_arbitration_e2e.py",
        "test_select_catering_proposal.py",
    ],
    "redundant-option2": ["test_catering_turn_arbitration_e2e.py"],
    "branchB-amendment-success": [
        "test_catering_amendment_capture.py",
        "test_catering_turn_arbitration_e2e.py",
    ],
    "branchB-amendment-failure": ["test_catering_amendment_capture.py"],
    "missing-outbound-id": ["test_catering_turn_arbitration_e2e.py"],
    "recovery-watchdog-timer-reversal": ["test_deploy_timer_state_preservation.py"],
}

# Incidents intentionally WITHOUT a deterministic-test anchor (e.g. a pure
# known-gap incident whose protection does not exist yet). Empty today — every
# current incident is anchored. A newly appended fixture MUST be added either to
# INCIDENT_TO_DETERMINISTIC_TESTS or here, or test_every_fixture_is_anchored_or_
# exempt fails — so an un-anchored fixture cannot sit silently green.
ANCHOR_EXEMPT: "frozenset[str]" = frozenset()

# Real identifiers that MUST NEVER appear in a frozen fixture (charter §9.2).
# The fail-closed PII scan below asserts none is present. Aliases (555-01xx,
# 1000000000000xx@lid, "Sample Caterer", "100 Example Rd, Testville") are the
# only identifiers a fixture may carry.
FORBIDDEN_REAL_IDENTIFIERS = (
    "17329837841",
    "201975216009469",
    "918522041562",
    "918985741562",
    "17043243322",
    "19803826497",
    "15713830763",
    "Lakshmi",
    "Brybar",
    "Saint Johns",
    "St Johns",
)

_TESTS_DIR = Path(__file__).resolve().parent


def _load_fixtures() -> list[tuple[str, dict]]:
    """(filename, oracle-dict) for every fixture; sorted for determinism."""
    files = sorted(FIXTURE_DIR.glob("*.json"))
    out = []
    for f in files:
        out.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
    return out


_FIXTURES = _load_fixtures()
_FIXTURE_IDS = [name for name, _ in _FIXTURES]


def test_fixture_directory_is_populated():
    """The fixed suite location exists and holds the 11 charter incidents."""
    assert FIXTURE_DIR.is_dir(), f"missing fixed suite location {FIXTURE_DIR}"
    assert len(_FIXTURES) >= len(CHARTER_INCIDENTS), (
        f"expected >= {len(CHARTER_INCIDENTS)} fixtures, found {len(_FIXTURES)}"
    )


def test_all_charter_incidents_present():
    """Every charter §9.1 incident has a fixture (additive-only: set may grow)."""
    present = {oracle["incident_id"] for _, oracle in _FIXTURES}
    missing = CHARTER_INCIDENTS - present
    assert not missing, f"charter incidents with no fixture: {sorted(missing)}"


@pytest.mark.parametrize("name,oracle", _FIXTURES, ids=_FIXTURE_IDS)
def test_oracle_schema_complete(name, oracle):
    """Fail if any oracle key is missing OR unexpected (fixed §9.3 shape)."""
    keys = set(oracle.keys())
    missing = REQUIRED_ORACLE_KEYS - keys
    extra = keys - REQUIRED_ORACLE_KEYS
    assert not missing, f"{name}: missing oracle keys {sorted(missing)}"
    assert not extra, f"{name}: unexpected oracle keys {sorted(extra)}"

    # Type/shape contract for the fields the runner reasons about.
    assert isinstance(oracle["incident_id"], str) and oracle["incident_id"]
    assert isinstance(oracle["description"], str) and oracle["description"]
    assert isinstance(oracle["pseudonymized_transcript"], list) and oracle["pseudonymized_transcript"]
    for turn in oracle["pseudonymized_transcript"]:
        assert set(turn.keys()) == {"from", "text"}, f"{name}: transcript turn keys {turn.keys()}"
        assert isinstance(turn["from"], str) and isinstance(turn["text"], str)
    assert isinstance(oracle["expected_route"], str) and oracle["expected_route"]
    assert isinstance(oracle["expected_mutations"], list)
    assert isinstance(oracle["forbidden_mutations"], list)
    assert isinstance(oracle["expected_logical_sends"], int) and not isinstance(
        oracle["expected_logical_sends"], bool
    ), f"{name}: expected_logical_sends must be an int"
    assert oracle["expected_logical_sends"] >= 0
    assert isinstance(oracle["expected_audit_identities"], list)
    assert isinstance(oracle["expected_final_state"], dict)
    assert oracle["pii_safe"] is True, f"{name}: pii_safe must be true"
    assert oracle["additive_only"] is True, f"{name}: additive_only must be true"


@pytest.mark.parametrize("name,oracle", _FIXTURES, ids=_FIXTURE_IDS)
def test_fixture_is_pii_safe(name, oracle):
    """FAIL CLOSED: no real identifier may appear anywhere in a fixture (§9.2).

    Operationalises ``pii_safe: true`` — a fixture claiming PII-safety that still
    embeds a real phone, LID, business name, or address fails here rather than
    shipping. Aliased identifiers only."""
    blob = json.dumps(oracle)
    hits = [tok for tok in FORBIDDEN_REAL_IDENTIFIERS if tok in blob]
    assert not hits, f"{name}: real identifier(s) present in a PII-safe fixture: {hits}"


@pytest.mark.parametrize("name,oracle", _FIXTURES, ids=_FIXTURE_IDS)
def test_fixture_destinations_are_aliased(name, oracle):
    """Fixture data must never contain a real customer destination (§9.3).

    Every ``from`` identifier that looks like a phone number must be a 555 test
    alias; any other numeric destination is an unknown/real destination and the
    harness fails closed."""
    for turn in oracle["pseudonymized_transcript"]:
        frm = turn["from"]
        if frm.startswith("system:"):
            continue  # non-transport system actor label
        digits = "".join(ch for ch in frm if ch.isdigit())
        if not digits:
            continue
        # LID aliases are 1000000000000xx; phone aliases are +1 555 01xx xxxx.
        assert digits.startswith("1555010") or digits.startswith("100000000000"), (
            f"{name}: non-aliased destination {frm!r} — fail closed on unknown destination"
        )


@pytest.mark.parametrize(
    "incident_id,test_files",
    sorted(INCIDENT_TO_DETERMINISTIC_TESTS.items()),
    ids=sorted(INCIDENT_TO_DETERMINISTIC_TESTS),
)
def test_incident_anchored_to_deterministic_tests(incident_id, test_files):
    """Each incident's declarative oracle stays anchored to executable proof:
    the mapped deterministic test module(s) must exist in the repo."""
    present = {oracle["incident_id"] for _, oracle in _FIXTURES}
    assert incident_id in present, f"no fixture for mapped incident {incident_id}"
    for tf in test_files:
        assert (_TESTS_DIR / tf).is_file(), (
            f"{incident_id}: mapped deterministic test {tf} is missing — "
            f"frozen oracle is no longer anchored to executable proof"
        )


def test_every_fixture_is_anchored_or_exempt():
    """Completeness guard: every fixture incident must be anchored to a
    deterministic test OR explicitly exempted. Prevents a future appended
    fixture from staying silently green with no executable proof behind it.

    (Anchoring is by module EXISTENCE, not semantics — see GOVERNANCE.md; a
    green anchor is not behavioral verification, only that the proof still
    exists in the tree.)"""
    unanchored = [
        oracle["incident_id"]
        for _, oracle in _FIXTURES
        if oracle["incident_id"] not in INCIDENT_TO_DETERMINISTIC_TESTS
        and oracle["incident_id"] not in ANCHOR_EXEMPT
    ]
    assert not unanchored, (
        f"fixtures with no deterministic anchor and no exemption: {unanchored} — "
        f"add each to INCIDENT_TO_DETERMINISTIC_TESTS or ANCHOR_EXEMPT"
    )


def test_transport_defaults_to_fake_sink():
    """§9.3 execution safety: transport defaults to the fake sink (conftest
    autouse), never the live bridge."""
    from conftest import FAKE_BRIDGE_SINK

    assert safe_io.BRIDGE_URL == FAKE_BRIDGE_SINK, (
        f"bridge URL {safe_io.BRIDGE_URL!r} is not the fake sink — transport is "
        f"not defaulting to fake"
    )


def test_harness_fails_closed_on_live_destination():
    """§9.3 fail-closed: a pytest-context send to the live bridge is refused, and
    even with the explicit test opt-in a live-bridge destination RAISES
    (LiveBridgeSendInTestError) rather than leaking."""
    # Refuse-by-default under pytest (no opt-in).
    assert safe_io.bridge_send_blocked_by_test_context() is not None

    # Opt-in present, but a LIVE-bridge destination still fails closed.
    import os

    prev = os.environ.get("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS")
    os.environ["SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS"] = "1"
    try:
        with pytest.raises(safe_io.LiveBridgeSendInTestError):
            safe_io.bridge_send_blocked_by_test_context("http://127.0.0.1:3000/send")
        # A non-live (fake) destination under opt-in is allowed (returns None).
        assert safe_io.bridge_send_blocked_by_test_context(
            "http://127.0.0.1:1/__fake_test_sink__"
        ) is None
    finally:
        if prev is None:
            os.environ.pop("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", None)
        else:
            os.environ["SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS"] = prev


def _fixture_by_id(incident_id: str) -> dict:
    for _, oracle in _FIXTURES:
        if oracle["incident_id"] == incident_id:
            return oracle
    raise AssertionError(f"fixture {incident_id} not found")


def _drive_28_send_turn_through_gate(monkeypatch, *, budget_enabled, limit=5):
    """Drive the REAL safe_io per-inbound-turn budget gate exactly as the 28-send
    spiral would: begin one inbound turn, then attempt 28 finalized sends through
    ``turn_send_budget_gate``. Returns the list of gate decisions
    (True=admitted, False=suppressed, None=not enforced). Isolated: resets the
    turn ContextVar before and after so no state leaks between configurations."""
    import importlib
    live = importlib.import_module("safe_io")
    live._TURN_SEND_BUDGET.set(None)
    if budget_enabled:
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", str(limit))
    else:
        monkeypatch.delenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", raising=False)
        monkeypatch.delenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", raising=False)
    live.begin_inbound_turn_send_budget()
    chat = "15550100001@s.whatsapp.net"
    decisions = [live.turn_send_budget_gate(chat, "reply") for _ in range(28)]
    live._TURN_SEND_BUDGET.set(None)
    return decisions


def test_28_send_spiral_baseline_unprotected_when_budget_off(monkeypatch):
    """OFF mode (budget unavailable / DEFAULT-OFF, the current production state):
    the 28-send spiral is NOT structurally prevented — this asserts the known
    unsafe baseline EXPLICITLY and must NOT be read as protection. With the hard
    per-turn budget off, ``turn_send_budget_gate`` performs NO enforcement
    (returns ``None`` for every attempt), so all 28 sends of a single inbound
    turn would proceed unbounded. This is the documented gap the incident
    tracks; it graduates only when the budget is installed AND enabled (see the
    ON-mode test)."""
    oracle = _fixture_by_id("28-send-spiral")
    observed = oracle["expected_final_state"]["outbound_sends_this_turn"]["observed_incident"]
    decisions = _drive_28_send_turn_through_gate(monkeypatch, budget_enabled=False)
    # Baseline: no enforcement — every attempt is un-gated (None), so nothing
    # bounds a spiral. 28 un-gated attempts == the unsafe incident shape.
    assert all(d is None for d in decisions), (
        "expected NO budget enforcement when the hard per-turn budget is OFF "
        "(the known-unsafe baseline)"
    )
    assert len(decisions) == 28 and observed >= 28


def test_28_send_spiral_bounded_when_budget_enabled(monkeypatch):
    """ON mode (budget installed AND enabled in this isolated environment): the
    protected behavior — a single inbound turn is bounded to the configured cap,
    far below the observed 28. The spiral is structurally impossible. This is a
    real PASS (no xfail): the graduated assertion of the incident oracle.

    Accounts for the full send taxonomy the oracle cares about: finalized sends
    consume budget and are capped at LIMIT; a progressive draft/edit
    (``reserve_budget=False``) does NOT consume the finalized budget (a streamed
    reply costs ONE finalized unit); retries re-hit the exhausted gate and are
    likewise suppressed. Transport splits share the one per-turn counter."""
    oracle = _fixture_by_id("28-send-spiral")
    cap = oracle["expected_logical_sends"]
    observed = oracle["expected_final_state"]["outbound_sends_this_turn"]["observed_incident"]
    assert cap < observed  # the oracle invariant: the cap is far below the incident

    limit = 5
    decisions = _drive_28_send_turn_through_gate(monkeypatch, budget_enabled=True, limit=limit)
    # Bounded: exactly LIMIT finalized sends admitted, the remaining 28-LIMIT
    # suppressed. The spiral cannot exceed the per-turn cap.
    assert decisions.count(True) == limit, "exactly LIMIT finalized sends admitted"
    assert decisions.count(False) == 28 - limit, "all sends past the cap suppressed"
    assert limit < observed  # bounded far below the observed 28-send incident

    # Draft/edit + retry accounting on a fresh turn: drafts do not consume the
    # finalized budget; a retry after exhaustion is re-suppressed (still bounded).
    import importlib
    live = importlib.import_module("safe_io")
    live._TURN_SEND_BUDGET.set(None)
    live.begin_inbound_turn_send_budget()
    chat = "15550100001@s.whatsapp.net"
    assert all(live.turn_send_budget_gate(chat, "draft", reserve_budget=False) is True
               for _ in range(10)), "progressive drafts do not consume the finalized budget"
    finals = [live.turn_send_budget_gate(chat, "final") for _ in range(limit + 3)]
    assert finals.count(True) == limit and finals.count(False) == 3, (
        "finalized sends capped at LIMIT; retries past exhaustion re-suppressed"
    )
    live._TURN_SEND_BUDGET.set(None)
