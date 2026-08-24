"""Operator alerts must describe the MEASURED state, not a generic story reused
across materially different outcomes (the #763 semantic-truth class).

Three sibling sites in shared platform code paged one body for several states,
and for four of those states the body was BACKWARDS — an operator who believed
it formed the opposite model of the system:

  1. ``safe_io._page_turn_send_budget_exhausted`` — one body for ``exhausted`` /
     ``draft_exhausted`` / ``config_failed``. ``config_failed`` never READ the
     config yet the body quoted a limit and asserted a spiral; ``draft_exhausted``
     claimed every further send is dropped while finalized sends still go.
  2. ``safe_io._alert_agent_disabled_send`` — claimed "customers and staff are
     getting silence" for BOTH the bridge chokepoint (a true drop) and the
     gateway seam (a template SUBSTITUTION — the seam is contractually ``-> str``).
  3. ``automation_control._alert_state_corrupt`` — claimed the file "was
     quarantined" and suppression "may have reset to active" for statuses where
     NOTHING was renamed and the outbound backstop fails CLOSED.

Every test here pins a message to the STATE it describes AND asserts a different
state yields a different message, so a future behaviour change cannot leave stale
prose behind. The negative assertions name the exact old phrases, so restoring
the old body fails these tests rather than leaving them vacuously green.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform", REPO / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import automation_control as ac  # noqa: E402
import safe_io  # noqa: E402

CHAT = "15550100333@c.us"


@pytest.fixture(autouse=True)
def _live_safe_io():
    """Bind the LIVE safe_io (an earlier suite's loader may have popped and
    re-imported it) and reset the per-turn ContextVar + the two §12b in-process
    throttles so each test starts from a known state."""
    global safe_io
    safe_io = sys.modules.get("safe_io") or safe_io
    safe_io._TURN_SEND_BUDGET.set(None)
    safe_io._last_disabled_alert_monotonic = 0.0
    ac._last_state_alert_monotonic = 0.0
    yield
    safe_io._TURN_SEND_BUDGET.set(None)
    safe_io._last_disabled_alert_monotonic = 0.0
    ac._last_state_alert_monotonic = 0.0


def _capture_pages(monkeypatch, module):
    pages: list = []
    monkeypatch.setattr(
        module, "notify_owner_with_fallback",
        lambda title, message, *a, **k: (pages.append((title, message, k)), True)[1],
    )
    return pages


# ── Site 1: per-turn send budget (safe_io) ───────────────────────────────────

class TestTurnSendBudgetPageMatchesState:
    """`turn_send_budget_gate` pages for three states with different truths."""

    def _page_for_config_failed(self, monkeypatch) -> tuple:
        def _boom():
            raise RuntimeError("config machinery broken")
        monkeypatch.setattr(safe_io, "turn_send_budget_enabled", _boom)
        pages = _capture_pages(monkeypatch, safe_io)
        safe_io.begin_inbound_turn_send_budget()
        assert safe_io.turn_send_budget_gate(CHAT, "x") is False
        assert len(pages) == 1
        return pages[0]

    def _page_for_exhausted(self, monkeypatch) -> tuple:
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "2")
        pages = _capture_pages(monkeypatch, safe_io)
        safe_io.begin_inbound_turn_send_budget()
        assert safe_io.turn_send_budget_gate(CHAT, "a") is True
        assert safe_io.turn_send_budget_gate(CHAT, "b") is True
        assert safe_io.turn_send_budget_gate(CHAT, "c") is False  # finalized cap
        assert len(pages) == 1
        return pages[0]

    def _page_for_draft_exhausted(self, monkeypatch) -> tuple:
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_ENABLED", "1")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_LIMIT", "5")
        monkeypatch.setenv("GATEWAY_TURN_SEND_BUDGET_DRAFT_LIMIT", "2")
        pages = _capture_pages(monkeypatch, safe_io)
        safe_io.begin_inbound_turn_send_budget()
        for _ in range(2):
            assert safe_io.turn_send_budget_gate(
                CHAT, "d", reserve_budget=False) is True
        # draft ceiling hit while the FINALIZED cap still has room (0 of 5 used)
        assert safe_io.turn_send_budget_gate(
            CHAT, "d3", reserve_budget=False) is False
        assert len(pages) == 1
        return pages[0]

    def test_config_failed_does_not_claim_a_cap_was_hit_or_a_spiral(self, monkeypatch):
        """The config was never READ: nothing was counted, no configured limit is
        known, and nothing observed suggests a send loop."""
        title, body, _ = self._page_for_config_failed(monkeypatch)
        low = body.lower()
        assert "hit its per-turn send cap" not in low        # old, false
        assert "send loop is likely spiraling" not in low    # old, false
        assert "config" in low and "could not be read" in low
        # the fail-closed placeholder limit (DEFAULT_TURN_SEND_BUDGET_LIMIT) must
        # not be quoted as if it were the configured cap
        assert f"({safe_io.DEFAULT_TURN_SEND_BUDGET_LIMIT})" not in body
        assert "config" in title.lower()

    def test_exhausted_states_the_finalized_cap_and_the_full_stop(self, monkeypatch):
        """`exhausted` IS the state the old body described: the finalized cap is
        hit and reserve() then refuses drafts too."""
        title, body, kw = self._page_for_exhausted(monkeypatch)
        low = body.lower()
        assert "finalized" in low and "cap" in low
        assert "2" in body                       # the REAL configured limit
        assert "drop" in low
        assert kw.get("priority") == 1
        assert "exhaust" in title.lower()

    def test_draft_exhausted_says_finalized_sends_still_go(self, monkeypatch):
        """Only the SEPARATE draft-transport ceiling tripped; the finalized cap
        still has room, so finalized sends this turn are still admitted."""
        title, body, _ = self._page_for_draft_exhausted(monkeypatch)
        low = body.lower()
        assert "every further send this turn is dropped" not in low  # old, false
        assert "draft" in low
        assert "still" in low and "finalized" in low
        assert "not exhausted" in low or "still has room" in low
        assert "draft" in title.lower()

    def test_draft_exhausted_quotes_the_draft_counters_not_the_finalized_ones(
        self, monkeypatch,
    ):
        """The bound that TRIPPED is the draft ceiling (2), not the finalized cap
        (5) — quoting 0/5 would describe a cap that was never reached."""
        _, body, _ = self._page_for_draft_exhausted(monkeypatch)
        assert "2" in body
        assert "0/5" not in body and "(5)" not in body

    def _all_three(self) -> dict:
        """One ISOLATED monkeypatch context per state — `config_failed` patches
        `turn_send_budget_enabled` to raise, which would otherwise poison the two
        states after it and make the distinctness check pass vacuously."""
        out = {}
        for name, getter in (
            ("config_failed", self._page_for_config_failed),
            ("exhausted", self._page_for_exhausted),
            ("draft_exhausted", self._page_for_draft_exhausted),
        ):
            with pytest.MonkeyPatch.context() as mp:
                safe_io._TURN_SEND_BUDGET.set(None)
                out[name] = getter(mp)
        safe_io._TURN_SEND_BUDGET.set(None)
        return out

    def test_the_three_states_produce_three_distinct_messages(self):
        seen = self._all_three()
        assert len({b for _t, b, _k in seen.values()}) == 3, seen
        assert len({t for t, _b, _k in seen.values()}) == 3, seen

    def test_bodies_fit_the_pushover_cap_and_carry_no_markdown(self):
        for name, (title, body, _kw) in self._all_three().items():
            # shift-agent-notify-owner truncates the Pushover message at 1024
            assert len(body) <= 1024, (name, len(body))
            # plain text only: no Markdown emphasis markers that a renderer would
            # consume around the underscore-bearing identifiers
            assert "*" not in body and "`" not in body, name
            assert "_" not in title, name


# ── Site 2: operator kill switch (safe_io) ───────────────────────────────────

def _ctx():
    from schemas import ActionExecutionContext
    return ActionExecutionContext(
        action_id="msg-truth-test", is_regulated_action=False,
        verified_action_result=False,
    )


@pytest.fixture
def disabled_flag(tmp_path, monkeypatch):
    flag = tmp_path / "state" / "disabled.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("disabled-for-test", encoding="utf-8")
    monkeypatch.setenv("SHIFT_AGENT_DISABLED_FLAG", str(flag))
    monkeypatch.setenv("SHIFT_AGENT_ALLOW_BRIDGE_IN_TESTS", "1")
    return flag


class TestKillSwitchPageMatchesTheSeam:
    """The kill switch refuses at two seams with OPPOSITE customer outcomes."""

    def test_gateway_seam_page_says_template_substituted_not_silence(
        self, monkeypatch, disabled_flag,
    ):
        pages = _capture_pages(monkeypatch, safe_io)
        out = safe_io.front_brain_screen_gateway_send(CHAT, "a composed reply")
        # the ground truth this message must match: a SUBSTITUTION reached the
        # customer, so silence is exactly what did NOT happen
        assert out == safe_io.FRONT_BRAIN_SAFE_GENERIC_ACK
        assert len(pages) == 1
        title, body, _ = pages[0]
        low = body.lower()
        assert "getting silence" not in low          # old, false at this seam
        assert "template" in low
        assert "substitut" in low
        assert "shift-agent-enable" in body          # the remedy stays
        assert "DISABLED" in title

    @patch("urllib.request.urlopen")
    def test_bridge_chokepoint_page_says_dropped_true_silence(
        self, urlopen, monkeypatch, disabled_flag,
    ):
        resp = MagicMock()
        resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = resp
        pages = _capture_pages(monkeypatch, safe_io)

        ok, _mid, err, status = safe_io.bridge_post(
            CHAT, "a routine reply", action_context=_ctx())
        # ground truth: nothing reached transport, the send is gone
        assert ok is False and status == "disabled" and err == "agent_disabled"
        assert urlopen.call_count == 0
        assert len(pages) == 1
        _title, body, _ = pages[0]
        low = body.lower()
        assert "drop" in low
        assert "never delivered" in low or "silence" in low
        assert "substitut" not in low  # a bridge send is NOT substituted

    @patch("urllib.request.urlopen")
    def test_the_two_seams_produce_different_messages(
        self, urlopen, monkeypatch, disabled_flag,
    ):
        resp = MagicMock()
        resp.read.return_value = b'{"id": "wamid.OK"}'
        urlopen.return_value.__enter__.return_value = resp
        pages = _capture_pages(monkeypatch, safe_io)

        safe_io.bridge_post(CHAT, "x", action_context=_ctx())
        safe_io._last_disabled_alert_monotonic = 0.0  # past the once/hour throttle
        safe_io.front_brain_screen_gateway_send(CHAT, "y")

        assert len(pages) == 2
        assert pages[0][1] != pages[1][1]

    def test_every_send_kind_the_callers_pass_is_classified(self):
        """A new send_kind must not silently inherit the wrong outcome story."""
        for kind in ("bridge_post", "bridge_send_media", "bridge_send_cta"):
            assert kind not in safe_io._DISABLED_SUBSTITUTING_SEND_KINDS
        assert "gateway_send" in safe_io._DISABLED_SUBSTITUTING_SEND_KINDS


# ── Site 3: automation-control state read failure (automation_control) ───────

@pytest.fixture
def ac_state(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "CATERING_AUTOMATION_CONTROL_STATE_PATH",
        str(tmp_path / "catering-automation-control.json"),
    )
    ac._LAST_KNOWN_MODE.clear()
    yield
    ac._LAST_KNOWN_MODE.clear()


def _page_for_status(monkeypatch, status: str) -> tuple:
    pages = _capture_pages(monkeypatch, ac)
    monkeypatch.setattr(ac, "safe_load_json", lambda *a, **k: ({}, status))
    ac._last_state_alert_monotonic = 0.0
    ac.read_mode_and_status(CHAT)
    assert len(pages) == 1, status
    return pages[0]


class TestStateReadFailurePageMatchesWhatActuallyHappened:
    """safe_load_json renames aside ONLY on `corrupt:`. `corrupt_unrenamed:` and
    `oserror:` move nothing, keep failing, and the outbound backstop fails CLOSED
    — the opposite of "quarantined" and "may have reset to active"."""

    def test_corrupt_status_is_the_one_that_really_quarantines(
        self, monkeypatch, ac_state,
    ):
        title, body, _ = _page_for_status(monkeypatch, "corrupt:Expecting value")
        low = body.lower()
        assert "renamed aside" in low or "quarantined" in low
        assert "opt" in low  # opt-out suppression is the state that gets wiped
        assert "active" in low
        assert "quarantin" in title.lower()

    def test_corrupt_unrenamed_says_nothing_was_quarantined(
        self, monkeypatch, ac_state,
    ):
        title, body, _ = _page_for_status(
            monkeypatch, "corrupt_unrenamed:bad json (rename_err=EACCES)")
        low = body.lower()
        assert "failed to read and was quarantined" not in low   # old, backwards
        assert "may have reset to active" not in low             # old, backwards
        assert "nothing was quarantined" in low
        assert "has not reset to active" in low
        assert "blocked" in low and "not leaked" in low
        assert "closed" in low  # the backstop fails CLOSED → blocked, not leaked
        assert "fail" in title.lower() or "unreadable" in title.lower()

    def test_oserror_says_the_file_was_not_touched(self, monkeypatch, ac_state):
        _title, body, _ = _page_for_status(monkeypatch, "oserror:[Errno 13] denied")
        low = body.lower()
        assert "failed to read and was quarantined" not in low
        assert "may have reset to active" not in low
        assert "nothing was quarantined" in low
        assert "not modified" in low
        assert "has not reset to active" in low
        assert "closed" in low

    def test_unknown_status_does_not_invent_a_quarantine(self, monkeypatch, ac_state):
        _title, body, _ = _page_for_status(monkeypatch, "future_status:whatever")
        low = body.lower()
        assert "failed to read and was quarantined" not in low
        assert "may have reset to active" not in low
        assert "not known from here" in low

    def test_the_status_classes_produce_distinct_messages(self, monkeypatch, ac_state):
        bodies = [
            _page_for_status(monkeypatch, "corrupt:x")[1],
            _page_for_status(monkeypatch, "corrupt_unrenamed:x (rename_err=y)")[1],
            _page_for_status(monkeypatch, "oserror:x")[1],
            _page_for_status(monkeypatch, "weird:x")[1],
        ]
        assert len(set(bodies)) == 4

    def test_audit_detail_carries_the_same_truth_as_the_page(
        self, monkeypatch, ac_state,
    ):
        """The audit row is the second place this fact is reported; the old
        `may have reset to active` lived there too."""
        rows: list = []
        monkeypatch.setattr(ac, "_emit", lambda t, f: rows.append((t, f)))
        monkeypatch.setattr(ac, "notify_owner_with_fallback", lambda *a, **k: True)
        monkeypatch.setattr(ac, "safe_load_json", lambda *a, **k: ({}, "oserror:denied"))
        ac._last_state_alert_monotonic = 0.0
        ac.read_mode_and_status(CHAT)
        assert len(rows) == 1
        detail = rows[0][1]["detail"].lower()
        assert "may have reset to active" not in detail  # old, backwards
        assert "closed" in detail or "not modified" in detail

    def test_bodies_fit_the_pushover_cap(self, monkeypatch, ac_state):
        for status in ("corrupt:x", "corrupt_unrenamed:x (rename_err=y)",
                       "oserror:x", "weird:x"):
            _title, body, _ = _page_for_status(monkeypatch, status)
            assert len(body) <= 1024, (status, len(body))
