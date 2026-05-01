"""Regression guards for the overnight watchdog classifiers (F7, F8, F9).

Pure-Python tests — no fcntl, no bridge stub, no subprocess. Cross-platform.
Just import the classifier functions and assert on inputs/outputs. Catches
regression if a future SKILL.md edit or content-classifier tweak shifts
the boundary in a way that would re-break production.

Loads the watchdog scripts via SourceFileLoader because they have no .py
extension (deployed as /usr/local/bin/* on production VPS, source lives in
src/agents/*/scripts/).
"""
from __future__ import annotations

from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DISPATCHER_WATCHDOG = REPO / "src" / "agents" / "catering" / "scripts" / "catering-dispatcher-watchdog"
OWNER_ACTION_WATCHDOG = REPO / "src" / "agents" / "catering" / "scripts" / "catering-owner-action-watchdog"
SHIFT_NOTIFIER = REPO / "src" / "agents" / "shift" / "scripts" / "shift-missed-dispatch-notifier"


def _load(path: Path, mod_name: str):
    """Load a script with no .py extension into a module object."""
    return SourceFileLoader(mod_name, str(path)).load_module()


@pytest.fixture(scope="module")
def dispatcher_mod():
    return _load(DISPATCHER_WATCHDOG, "_dispatcher_watchdog_test")


@pytest.fixture(scope="module")
def owner_action_mod():
    return _load(OWNER_ACTION_WATCHDOG, "_owner_action_watchdog_test")


@pytest.fixture(scope="module")
def shift_notifier_mod():
    return _load(SHIFT_NOTIFIER, "_shift_notifier_test")


# ============================================================================
# F7 — catering dispatcher watchdog: classify_catering()
# ============================================================================

class TestCateringClassifierPositive:
    """Cases that MUST be classified as catering. If any of these regress to
    False, the watchdog will silently miss real customer inquiries."""

    @pytest.mark.parametrize("text,min_signals", [
        # The canonical customer inquiry that started the overnight saga
        ("Hello This is an inquiry about catering on May 8th evening, "
         "food must be delivered by 7 PM. 120 people, 80 nonveg and 40 veg.",
         3),
        # Wedding catering with explicit headcount
        ("Cater my wedding for 200 guests next month",
         2),
        # Headcount + food + delivery
        ("Can you do delivery of food for 75 people on Saturday",
         3),
        # Catering keyword + headcount only
        ("I want catering for 100",
         2),
        # Headcount + food + event
        ("Need biryani and 50 vegetarian meals for office event Friday",
         3),
        # Anniversary + headcount + food
        ("Need food for 80 people, anniversary dinner next Saturday",
         3),
        # Reception
        ("Catering for 150 person reception next Saturday",
         2),
    ])
    def test_must_classify_positive(self, dispatcher_mod, text, min_signals):
        is_catering, signals = dispatcher_mod.classify_catering(text)
        actual_signals = [s for s in signals if not s.startswith("rejected")]
        assert is_catering, f"FALSE NEGATIVE on {text!r}; signals={signals}"
        assert len(actual_signals) >= min_signals, \
            f"Expected ≥{min_signals} signals, got {actual_signals!r}"


class TestCateringClassifierNegative:
    """Cases that MUST NOT be classified as catering. False positives create
    spurious leads that need manual cleanup."""

    @pytest.mark.parametrize("text", [
        "Boss I am sick today, can't come in",
        "Thanks for your service",
        "ok thanks",
        "100 people",                         # headcount alone — insufficient
        "menu has biryani",                    # food alone
        "Can you cover Anjali's shift tomorrow?",  # shift coverage, not catering
        "What time do you open?",              # general inquiry
        "Sorry running late",                  # employee message
        "",                                    # empty
        "ok",                                  # too short
    ])
    def test_must_classify_negative(self, dispatcher_mod, text):
        is_catering, signals = dispatcher_mod.classify_catering(text)
        assert not is_catering, f"FALSE POSITIVE on {text!r}; signals={signals}"


class TestCateringHeadcountExtraction:
    """The headcount integer must extract correctly across phrasing variants."""

    @pytest.mark.parametrize("text,expected_hc", [
        ("for 50 people",        50),
        ("100 guests",           100),
        ("80 ppl",               80),
        ("40 vegetarian",        40),
        ("50 meals",             50),
        ("75 plates",            75),
        ("for 200",              200),
        ("serving 30",           30),
        ("feed 60",              60),
    ])
    def test_extract_headcount(self, dispatcher_mod, text, expected_hc):
        _is_c, signals = dispatcher_mod.classify_catering(
            f"catering for the team — {text}, food included delivered Saturday"
        )
        hc_signals = [s for s in signals if s.startswith("headcount:")]
        assert hc_signals, f"No headcount signal for {text!r}; signals={signals}"
        extracted = int(hc_signals[0].split(":")[1])
        assert extracted == expected_hc, f"Expected {expected_hc}, got {extracted}"


# ============================================================================
# F8 — catering owner-action watchdog: parse_owner_action()
# ============================================================================

class TestOwnerActionParser:
    """The #XXXXX (approve|reject|edit|wait) regex MUST handle all
    realistic owner phrasing without false positives."""

    @pytest.mark.parametrize("text,expected", [
        ("#993HY approve",                ("#993HY", "approve", "")),
        ("#A3F2X approved",               ("#A3F2X", "approve", "")),
        ("#KE6DB reject too short notice", ("#KE6DB", "reject", "too short notice")),
        ("#C9HSR rejected",                ("#C9HSR", "reject", "")),
        ("#993HY edit reduce headcount",   ("#993HY", "edit", "reduce headcount")),
        ("#A3F2X wait",                    ("#A3F2X", "wait", "")),
        # Mixed case
        ("#a3f2x APPROVE",                 ("#A3F2X", "approve", "")),
        # Trailing whitespace
        ("#993HY approve ",                ("#993HY", "approve", "")),
    ])
    def test_parse_positive(self, owner_action_mod, text, expected):
        result = owner_action_mod.parse_owner_action(text)
        assert result == expected, f"Got {result!r}, expected {expected!r}"

    @pytest.mark.parametrize("text", [
        "approve #993HY",          # action before code — wrong order
        "thanks for the lead",     # no code
        "#XYZ12 approve",          # Z (1) and 1 not in alphabet
        "#993HY confirm",          # unrecognized action verb
        "#993HY",                  # code without action
        "approve",                 # action without code
        "",                        # empty
        "Hello!",                  # generic chat
    ])
    def test_parse_negative(self, owner_action_mod, text):
        result = owner_action_mod.parse_owner_action(text)
        assert result is None, f"Expected None for {text!r}, got {result!r}"


# ============================================================================
# F9 — shift sick-call notifier: is_shift_relevant()
# ============================================================================

class TestShiftClassifierPositive:
    """Cases that MUST be flagged for owner notification."""

    @pytest.mark.parametrize("text,min_pats", [
        ("Boss I am sick today, can't come for evening shift", 1),
        ("I have fever, won't be able to work tomorrow",        1),
        ("Family emergency, cannot come in",                    1),
        ("Not feeling well, sorry",                              1),
        ("Need to skip my morning shift, food poisoning",       1),
        ("Hospital — can't make it tonight",                    1),
        ("Boss, I am sick",                                      1),
        ("Stomach issue, won't make it",                         1),
        ("Migraine, cannot work today",                          1),
        ("Cover my evening shift please",                        1),
    ])
    def test_must_flag_sick(self, shift_notifier_mod, text, min_pats):
        is_shift, signals = shift_notifier_mod.is_shift_relevant(text)
        actual = [s for s in signals if s.startswith("pat:")]
        assert is_shift, f"FALSE NEGATIVE on {text!r}; signals={signals}"
        assert len(actual) >= min_pats


class TestShiftClassifierNegative:
    """Cases that MUST NOT be flagged. False positives create owner-Pushover spam."""

    @pytest.mark.parametrize("text", [
        "Need catering for 50 people",
        "ok thanks",
        "Shift swap with Anjali ok?",     # asking about swap, not declining
        "What time is my shift tomorrow?", # asking about schedule
        "Hello!",
        "",
        "ok",
        "Can you send the schedule",
    ])
    def test_must_not_flag(self, shift_notifier_mod, text):
        is_shift, _signals = shift_notifier_mod.is_shift_relevant(text)
        assert not is_shift, f"FALSE POSITIVE on {text!r}"


# ============================================================================
# Schema round-trip — ensure new LogEntry variants serialize cleanly
# ============================================================================

class TestSchemaRoundTrip:
    """If any variant fails to round-trip, ndjson_append will silently corrupt
    decisions.log. These tests catch breakage at the validator level."""

    def test_dispatcher_fired_round_trip(self):
        import sys
        sys.path.insert(0, str(REPO / "src" / "platform"))
        from schemas import CateringDispatcherWatchdogFired
        e = CateringDispatcherWatchdogFired(
            ts="2026-05-01T03:38:04.061105Z",
            type="catering_dispatcher_watchdog_fired",
            chat_id="15558675311@s.whatsapp.net",
            message_id="bridge_notify_X",
            customer_phone="+15558675311",
            signals=["primary:catering", "headcount:90", "event_keyword"],
            success=True,
            detail="L0017 created",
        )
        round_trip = CateringDispatcherWatchdogFired.model_validate_json(e.model_dump_json())
        assert round_trip.signals == e.signals
        assert round_trip.success is True
        assert round_trip.customer_phone == "+15558675311"

    def test_owner_action_fired_round_trip(self):
        import sys
        sys.path.insert(0, str(REPO / "src" / "platform"))
        from schemas import CateringOwnerActionWatchdogFired
        e = CateringOwnerActionWatchdogFired(
            ts="2026-05-01T03:45:37Z",
            type="catering_owner_action_watchdog_fired",
            chat_id="918522041562@s.whatsapp.net",
            message_id="bridge_owner_X",
            code="#993HY",
            action="approve",
            lead_id="L0015",
            success=True,
            detail="ok",
        )
        round_trip = CateringOwnerActionWatchdogFired.model_validate_json(e.model_dump_json())
        assert round_trip.code == "#993HY"
        assert round_trip.action == "approve"

    def test_shift_notified_round_trip(self):
        import sys
        sys.path.insert(0, str(REPO / "src" / "platform"))
        from schemas import ShiftMissedDispatchNotified
        e = ShiftMissedDispatchNotified(
            ts="2026-05-01T03:50:41Z",
            type="shift_missed_dispatch_notified",
            chat_id="17329837841@s.whatsapp.net",
            message_id="bridge_emp_X",
            employee_id="e004",
            employee_name="Anjali Iyer",
            signals=["pat:0", "pat:1", "pat:2", "pat:5"],
            success=True,
            detail="notify ok",
        )
        round_trip = ShiftMissedDispatchNotified.model_validate_json(e.model_dump_json())
        assert round_trip.employee_id == "e004"

    def test_owner_action_code_pattern_validation(self):
        """Codes outside the visually-unambiguous alphabet must reject."""
        import sys
        sys.path.insert(0, str(REPO / "src" / "platform"))
        from pydantic import ValidationError
        from schemas import CateringOwnerActionWatchdogFired
        with pytest.raises(ValidationError):
            CateringOwnerActionWatchdogFired(
                ts="2026-05-01T03:45:37Z",
                type="catering_owner_action_watchdog_fired",
                chat_id="x", message_id="y",
                code="#XYZ12",  # contains Z, 1 — not in alphabet
                action="approve", lead_id="L0001",
                success=True, detail="",
            )

    def test_dispatcher_suppressed_reason_enum(self):
        import sys
        sys.path.insert(0, str(REPO / "src" / "platform"))
        from pydantic import ValidationError
        from schemas import CateringDispatcherWatchdogSuppressed
        # Valid reason
        CateringDispatcherWatchdogSuppressed(
            ts="2026-05-01T00:00:00Z",
            type="catering_dispatcher_watchdog_suppressed",
            chat_id="x", message_id="y",
            reason="not_catering", detail="",
        )
        # Invalid reason should reject
        with pytest.raises(ValidationError):
            CateringDispatcherWatchdogSuppressed(
                ts="2026-05-01T00:00:00Z",
                type="catering_dispatcher_watchdog_suppressed",
                chat_id="x", message_id="y",
                reason="some_random_reason", detail="",
            )
