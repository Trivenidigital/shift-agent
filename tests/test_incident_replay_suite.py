"""Governed frozen incident-replay suite runner (charter §9).

Loads every declarative oracle fixture under ``tests/incident_replay/fixtures/``,
enforces the fixed §9.3 oracle schema, proves the fixtures are PII-safe, and
anchors each incident to the deterministic test(s) that actually exercise the
behavior. Execution safety is inherited from ``tests/conftest.py`` (autouse
fake-bridge sink) and ``safe_io.LiveBridgeSendInTestError`` (fail-closed on any
live-bridge / unknown destination). See ``tests/incident_replay/GOVERNANCE.md``.

28-SEND SPIRAL — MUST BECOME GREEN BEFORE TRANSPORT-BUDGET ACTIVATION.
The ``test_28_send_spiral_hard_budget_installed`` case is marked
``xfail(strict=True)``: today no hard per-turn transport budget is active by
default on the live path (#643 / ``GATEWAY_TURN_SEND_BUDGET_ENABLED`` default
OFF), so the enforcing assertion FAILS and registers as an expected (xfailed)
known gap. The graduation trigger is the budget becoming DEFAULT-ON — i.e.
``turn_send_budget_enabled()`` returning True in an unconfigured environment —
NOT the mere merge of any PR. PR-3 ships the budget installable but still
DEFAULT-OFF, so this case stays xfailed after PR-3 merges; only when a later
change flips the default ON does the assertion PASS, the strict xfail turn to
XPASS ⇒ suite error, and this marker have to be removed so the incident
graduates to a normal green assertion. It must NOT be silently skipped and must
NOT be green until the protection is active by default.

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


@pytest.mark.xfail(
    strict=True,
    reason="28-send: no hard transport budget installed yet (#643 not activated); PR-3 gates this",
)
def test_28_send_spiral_hard_budget_installed():
    """The 28-send spiral is only structurally impossible once a hard per-turn
    transport budget is active BY DEFAULT. Today it is not (#643
    GATEWAY_TURN_SEND_BUDGET_ENABLED default OFF), so this fails and is a STRICT
    known failure. Graduation fires when the budget is DEFAULT-ON — not on any
    PR merge: PR-3 ships it installable but still default-OFF, so this stays
    xfailed after PR-3; only a later flip of the default to ON makes this pass →
    xpass → strict-xfail suite error, forcing the marker to be removed and the
    incident to graduate to green.

    Anchored to the fixture oracle: the budget must cap a single turn at
    ``expected_logical_sends`` (3), far below the observed 28."""
    oracle = _fixture_by_id("28-send-spiral")
    cap = oracle["expected_logical_sends"]
    assert cap < oracle["expected_final_state"]["outbound_sends_this_turn"]["observed_incident"]

    # The enforcing condition: a hard per-turn transport budget is active by
    # default (unconfigured environment). It is NOT today — this is the known gap.
    assert safe_io.turn_send_budget_enabled(), (
        "no hard per-turn transport budget is active by default (#643 OFF); a "
        "28-send spiral remains structurally possible until PR-3 installs it"
    )
