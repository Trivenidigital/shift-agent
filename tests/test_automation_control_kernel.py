"""PR-5 automation-control kernel — pure state + policy unit tests.

Exercises ``src/platform/automation_control.py`` directly: mode get/set,
pause auto-expiry, the one-deterministic-ack guard, the read-error last-known
fail behavior, restart durability, the flag/allowlist + sub-flag gating, and
the deterministic command detectors. Windows-runnable via the fcntl stub (the
FileLock advisory lock is a no-op in a single test process).
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fixtures_fleet import ensure_fcntl_stub

ensure_fcntl_stub()

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src" / "platform",):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import automation_control as ac  # noqa: E402

CUSTOMER = "15550100001@lid"
PHONE = "+15550100001"


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Per-test state file + a cleared last-known cache (module-global)."""
    monkeypatch.setenv(
        "CATERING_AUTOMATION_CONTROL_STATE_PATH",
        str(tmp_path / "catering-automation-control.json"),
    )
    ac._LAST_KNOWN_MODE.clear()
    ac._last_state_alert_monotonic = 0.0  # reset the §12b once/hour throttle
    yield
    ac._LAST_KNOWN_MODE.clear()
    ac._last_state_alert_monotonic = 0.0


def _state_file() -> Path:
    import os
    return Path(os.environ["CATERING_AUTOMATION_CONTROL_STATE_PATH"])


# ── mode transitions ─────────────────────────────────────────────────────────

class TestModeTransitions:
    def test_absent_record_is_active(self):
        assert ac.get_mode(CUSTOMER) == "active"
        assert ac.is_suppressed(CUSTOMER) is False

    def test_opt_out_persists_and_suppresses(self):
        frm, to, _ = ac.set_mode(
            CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
            logical_turn_id="wamid.1")
        assert (frm, to) == ("active", "opted_out")
        assert ac.get_mode(CUSTOMER) == "opted_out"
        assert ac.is_suppressed(CUSTOMER) is True

    def test_state_survives_simulated_restart(self):
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        # A restart loses in-process state; the on-disk record must remain.
        ac._LAST_KNOWN_MODE.clear()
        assert ac.get_mode(CUSTOMER) == "takeover"

    def test_rollback_preserves_takeover(self):
        """§5.5 rollback-preserves-takeover: the record lives under state/ (never
        in the deploy tarball). A simulated rollback that does NOT touch the state
        file leaves takeover intact on re-read."""
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        before = _state_file().read_text(encoding="utf-8")
        # (rollback swaps code, not state) — state file untouched:
        ac._LAST_KNOWN_MODE.clear()
        assert ac.get_mode(CUSTOMER) == "takeover"
        assert _state_file().read_text(encoding="utf-8") == before

    def test_explicit_resume_re_enables(self):
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        frm, to, _ = ac.set_mode(CUSTOMER, "active", actor="customer", reason="customer_resume")
        assert (frm, to) == ("opted_out", "active")
        assert ac.is_suppressed(CUSTOMER) is False

    def test_idempotent_takeover_rewrite(self):
        ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        frm, to, _ = ac.set_mode(CUSTOMER, "takeover", actor="owner", reason="owner_takeover")
        assert (frm, to) == ("takeover", "takeover")
        assert ac.get_mode(CUSTOMER) == "takeover"

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            ac.set_mode(CUSTOMER, "bogus", actor="system", reason="x")

    def test_canonical_convergence_phone_and_phone_jid(self):
        """A phone-JID and its E.164 form key to the SAME record (one customer,
        one mode)."""
        ac.set_mode(PHONE, "opted_out", actor="customer", reason="customer_stop")
        assert ac.get_mode("15550100001@s.whatsapp.net") == "opted_out"


# ── pause auto-expiry ────────────────────────────────────────────────────────

class TestPauseExpiry:
    def test_pause_suppresses_until_expiry(self):
        ac.set_mode(CUSTOMER, "paused", actor="customer", reason="customer_pause",
                    pause_seconds=3600)
        assert ac.get_mode(CUSTOMER) == "paused"

    def test_expired_pause_reads_as_active(self):
        ac.set_mode(CUSTOMER, "paused", actor="customer", reason="customer_pause",
                    pause_seconds=1)
        # Force the stored expiry into the past.
        p = _state_file()
        doc = json.loads(p.read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "2000-01-01T00:00:00+00:00"
        p.write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()
        assert ac.get_mode(CUSTOMER) == "active"
        assert ac.is_suppressed(CUSTOMER) is False

    def test_malformed_expiry_keeps_pause(self):
        """A malformed expiry fails toward the customer's chosen suppression."""
        ac.set_mode(CUSTOMER, "paused", actor="customer", reason="customer_pause")
        p = _state_file()
        doc = json.loads(p.read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "not-a-date"
        p.write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()
        assert ac.get_mode(CUSTOMER) == "paused"


# ── ack_sent guard ───────────────────────────────────────────────────────────

class TestAckGuard:
    def test_ack_sent_preserved_on_repeat_opt_out(self):
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
                    ack_sent=True)
        # A repeat opt_out without an explicit ack_sent preserves the prior True.
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        assert ac.get_record(CUSTOMER)["ack_sent"] is True

    def test_ack_sent_reset_on_active(self):
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
                    ack_sent=True)
        ac.set_mode(CUSTOMER, "active", actor="customer", reason="customer_resume")
        assert ac.get_record(CUSTOMER)["ack_sent"] is False


class TestAckCompareAndSet:
    """must-fix b: the ack guard is an atomic compare-and-set INSIDE the lock;
    only the call that flips ack_sent False→True returns ack_flipped=True."""

    def test_first_flips_second_does_not(self):
        _, _, flipped1 = ac.set_mode(
            CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
            ack_compare_and_set=True)
        assert flipped1 is True
        assert ac.get_record(CUSTOMER)["ack_sent"] is True
        # A second distinct stop-family message flips nothing (no second ack).
        _, _, flipped2 = ac.set_mode(
            CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
            ack_compare_and_set=True)
        assert flipped2 is False

    def test_flip_survives_restart(self):
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
                    ack_compare_and_set=True)
        ac._LAST_KNOWN_MODE.clear()  # cold cache
        _, _, flipped = ac.set_mode(
            CUSTOMER, "opted_out", actor="customer", reason="customer_stop",
            ack_compare_and_set=True)
        assert flipped is False  # the on-disk ack_sent=True is read under the lock


# ── read-error fail behavior ─────────────────────────────────────────────────

class TestFailBehavior:
    def test_never_read_corrupt_defaults_active(self, monkeypatch):
        monkeypatch.setattr(ac, "notify_owner_with_fallback", lambda *a, **k: True)
        _state_file().parent.mkdir(parents=True, exist_ok=True)
        _state_file().write_text("{ not json", encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()
        assert ac.get_mode(CUSTOMER) == "active"

    def test_read_error_preserves_last_known(self, monkeypatch):
        monkeypatch.setattr(ac, "notify_owner_with_fallback", lambda *a, **k: True)
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        assert ac.get_mode(CUSTOMER) == "opted_out"  # primes the last-known cache

        def _boom(*a, **k):
            return {}, "oserror:boom"
        monkeypatch.setattr(ac, "safe_load_json", _boom)
        # A read error now returns the last successfully-read mode, not active.
        assert ac.get_mode(CUSTOMER) == "opted_out"

    def test_read_mode_and_status_surfaces_error_status(self, monkeypatch):
        """The outbound backstop needs the raw status to fail closed (must-fix a)."""
        monkeypatch.setattr(ac, "notify_owner_with_fallback", lambda *a, **k: True)
        monkeypatch.setattr(ac, "safe_load_json", lambda *a, **k: ({}, "corrupt:x"))
        ac._LAST_KNOWN_MODE.clear()
        mode, status = ac.read_mode_and_status(CUSTOMER)
        assert status == "corrupt:x"
        assert status not in ac.HEALTHY_LOAD_STATUSES
        assert mode == "active"  # cold cache defaults active; status flags the fault


class TestStateCorruptionAlert:
    """§12b: a corrupt state read silently wipes suppression (safe_load_json
    quarantines the file → later reads are 'missing' → every convo reads active),
    so it MUST page the operator at detection."""

    def test_corrupt_read_pages_operator_once(self, monkeypatch):
        pages = []
        monkeypatch.setattr(
            ac, "notify_owner_with_fallback",
            lambda *a, **k: pages.append((a, k)) or True)
        monkeypatch.setattr(ac, "safe_load_json", lambda *a, **k: ({}, "corrupt:boom"))
        ac._LAST_KNOWN_MODE.clear()

        mode, status = ac.read_mode_and_status(CUSTOMER)
        assert status == "corrupt:boom"
        # Mutation-sensitive: remove the _alert_state_corrupt call and this fails.
        assert len(pages) == 1
        title, body = pages[0][0][0], pages[0][0][1]
        assert "automation-control" in title.lower()
        # Plain-text §12b body carries the underscore-bearing mode names unmangled.
        assert "opt out" in body.lower() or "opted out" in body.lower() or "opt-out" in body.lower()
        # Throttle delegated to the notify same-message dedup (once/hour).
        assert pages[0][1].get("dedup_window_min") == 60

    def test_persistent_fault_throttled_to_once(self, monkeypatch):
        pages = []
        monkeypatch.setattr(
            ac, "notify_owner_with_fallback",
            lambda *a, **k: pages.append(1) or True)
        monkeypatch.setattr(ac, "safe_load_json", lambda *a, **k: ({}, "oserror:denied"))
        ac._LAST_KNOWN_MODE.clear()
        for _ in range(4):
            ac.read_mode_and_status(CUSTOMER)
        assert len(pages) == 1  # in-process once/hour throttle holds

    def test_healthy_read_does_not_page(self, monkeypatch):
        pages = []
        monkeypatch.setattr(
            ac, "notify_owner_with_fallback", lambda *a, **k: pages.append(1) or True)
        ac.set_mode(CUSTOMER, "opted_out", actor="customer", reason="customer_stop")
        ac._LAST_KNOWN_MODE.clear()
        mode, status = ac.read_mode_and_status(CUSTOMER)
        assert status in ac.HEALTHY_LOAD_STATUSES
        assert pages == []


# ── flag / allowlist + sub-flag gating ───────────────────────────────────────

class TestGating:
    def test_master_off_disabled(self, monkeypatch):
        monkeypatch.delenv("CATERING_AUTOMATION_CONTROL_ENABLED", raising=False)
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        assert ac.enabled(CUSTOMER) is False

    def test_empty_allowlist_disabled(self, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "")
        assert ac.enabled(CUSTOMER) is False

    def test_wildcard_admits_all(self, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", "*")
        assert ac.enabled(CUSTOMER) is True

    def test_explicit_membership_on_canonical_key(self, monkeypatch):
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ENABLED", "1")
        monkeypatch.setenv("CATERING_AUTOMATION_CONTROL_ALLOWLIST", PHONE)
        # phone-JID form of the same number is admitted (canonical convergence).
        assert ac.enabled("15550100001@s.whatsapp.net") is True
        assert ac.enabled("15559999999@s.whatsapp.net") is False

    def test_sub_flags_independent(self, monkeypatch):
        monkeypatch.setenv("CATERING_STOP_ENABLED", "1")
        monkeypatch.delenv("CATERING_TAKEOVER_ENABLED", raising=False)
        assert ac.stop_enabled() is True
        assert ac.takeover_enabled() is False


# ── deterministic command detection ──────────────────────────────────────────

class TestCommandDetection:
    @pytest.mark.parametrize("text", ["STOP", "stop", "unsubscribe", "opt out",
                                       "please opt out", "stop messaging", "  STOP. "])
    def test_stop_detected(self, text):
        assert ac.detect_customer_command(text) == "stop"

    @pytest.mark.parametrize("text", ["pause", "hold off", "PAUSE"])
    def test_pause_detected(self, text):
        assert ac.detect_customer_command(text) == "pause"

    @pytest.mark.parametrize("text", ["resume", "RESUME", "unstop", "unpause", "resubscribe"])
    def test_resume_detected(self, text):
        assert ac.detect_customer_command(text) == "resume"

    @pytest.mark.parametrize("text", ["start", "START", "restart", "subscribe",
                                       "confirm", "continue"])
    def test_dropped_resume_words_not_detected(self, text):
        """BLOCKER-1b: catering-colliding words are NOT resume tokens — an ACTIVE
        customer typing 'confirm'/'continue' must route to proposal-selection."""
        assert ac.detect_customer_command(text) is None

    @pytest.mark.parametrize("text", ["stop by the store later", "can you pause the music",
                                       "hello there", "I want to start a catering order"])
    def test_non_command_not_detected(self, text):
        assert ac.detect_customer_command(text) is None

    def test_owner_takeover_requires_code(self):
        assert ac.detect_owner_command("takeover this lead") is None
        assert ac.detect_owner_command("#A3F9K takeover") == ("takeover", "#A3F9K")

    def test_owner_release_and_resume_map_to_active(self):
        assert ac.detect_owner_command("release #A3F9K") == ("active", "#A3F9K")
        assert ac.detect_owner_command("#A3F9K resume") == ("active", "#A3F9K")

    def test_owner_approve_is_not_an_automation_command(self):
        # #XXXXX approve is F8 territory, not automation-control.
        assert ac.detect_owner_command("approve #A3F9K") is None


# ── M4/G3: owner pause verb + bounded takeover window ────────────────────────

class TestOwnerPauseCommand:
    @pytest.mark.parametrize("text", [
        "pause #A3F9K", "#A3F9K pause", "hold #A3F9K", "#A3F9K hold",
        "please pause #A3F9K for now",
    ])
    def test_owner_pause_and_hold_detected(self, text):
        assert ac.detect_owner_command(text) == ("paused", "#A3F9K")

    def test_owner_pause_requires_a_code(self):
        """Same rule as takeover: the code is what identifies WHICH customer."""
        assert ac.detect_owner_command("pause everything") is None
        assert ac.detect_owner_command("hold off") is None

    def test_release_wins_over_pause_when_both_appear(self):
        """The un-suppress verb must never be shadowed — the same precedence the
        customer detector applies to `resume`."""
        assert ac.detect_owner_command("resume #A3F9K, no need to pause") == ("active", "#A3F9K")

    def test_takeover_still_wins_over_pause(self):
        assert ac.detect_owner_command("takeover #A3F9K and pause it") == ("takeover", "#A3F9K")

    def test_owner_pause_suppresses_and_expires(self):
        ac.set_mode(CUSTOMER, ac.MODE_PAUSED, actor="owner", reason="owner_pause",
                    pause_seconds=3600)
        assert ac.get_mode(CUSTOMER) == "paused"
        assert ac.is_suppressed(CUSTOMER) is True

        doc = json.loads(_state_file().read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["pause_expires_ts"] = "2000-01-01T00:00:00+00:00"
        _state_file().write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()

        assert ac.get_mode(CUSTOMER) == "active"

    def test_owner_pause_uses_the_seven_day_default(self):
        before = datetime.now(tz=timezone.utc)
        ac.set_mode(CUSTOMER, ac.MODE_PAUSED, actor="owner", reason="owner_pause")
        rec = ac.get_record(CUSTOMER)
        expires = datetime.fromisoformat(rec["pause_expires_ts"])
        delta = (expires - before).total_seconds()
        assert abs(delta - ac.DEFAULT_PAUSE_SECONDS) < 60


class TestTakeoverExpiry:
    def test_takeover_records_an_expiry(self):
        before = datetime.now(tz=timezone.utc)
        ac.set_mode(CUSTOMER, ac.MODE_TAKEOVER, actor="owner", reason="owner_takeover")
        rec = ac.get_record(CUSTOMER)
        expires = datetime.fromisoformat(rec["takeover_expires_ts"])
        delta = (expires - before).total_seconds()
        assert abs(delta - ac.TAKEOVER_DEFAULT_SECONDS) < 60
        assert ac.get_mode(CUSTOMER) == "takeover"

    def test_expired_takeover_reads_active(self):
        ac.set_mode(CUSTOMER, ac.MODE_TAKEOVER, actor="owner", reason="t",
                    takeover_seconds=3600)
        assert ac.get_mode(CUSTOMER) == "takeover"

        doc = json.loads(_state_file().read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["takeover_expires_ts"] = "2000-01-01T00:00:00+00:00"
        _state_file().write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()

        assert ac.get_mode(CUSTOMER) == "active"
        assert ac.is_suppressed(CUSTOMER) is False

    def test_reissuing_takeover_extends_the_window(self):
        ac.set_mode(CUSTOMER, ac.MODE_TAKEOVER, actor="owner", reason="t",
                    takeover_seconds=60)
        first = ac.get_record(CUSTOMER)["takeover_expires_ts"]
        ac.set_mode(CUSTOMER, ac.MODE_TAKEOVER, actor="owner", reason="t")
        second = ac.get_record(CUSTOMER)["takeover_expires_ts"]
        assert datetime.fromisoformat(second) > datetime.fromisoformat(first)

    def test_legacy_takeover_without_expiry_never_expires(self):
        """Records written before M4 carry no takeover_expires_ts. Silently
        un-muting a conversation a human is handling is the failure this kernel
        exists to prevent, so a missing expiry keeps the old never-expires
        behavior until the owner re-issues the takeover."""
        ac.set_mode(CUSTOMER, ac.MODE_TAKEOVER, actor="owner", reason="t")
        doc = json.loads(_state_file().read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        del doc["conversations"][key]["takeover_expires_ts"]
        _state_file().write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()

        assert ac.get_mode(CUSTOMER) == "takeover"

    def test_malformed_expiry_is_not_expired(self):
        ac.set_mode(CUSTOMER, ac.MODE_TAKEOVER, actor="owner", reason="t")
        doc = json.loads(_state_file().read_text(encoding="utf-8"))
        key = next(iter(doc["conversations"]))
        doc["conversations"][key]["takeover_expires_ts"] = "not-a-timestamp"
        _state_file().write_text(json.dumps(doc), encoding="utf-8")
        ac._LAST_KNOWN_MODE.clear()

        assert ac.get_mode(CUSTOMER) == "takeover"


class TestOwnerConfirmationText:
    def test_each_suppressing_verb_states_its_window(self):
        pause = ac.owner_confirmation(ac.MODE_PAUSED, "#A3F9K")
        takeover = ac.owner_confirmation(ac.MODE_TAKEOVER, "#A3F9K")
        active = ac.owner_confirmation(ac.MODE_ACTIVE, "#A3F9K")
        assert "7 days" in pause and "#A3F9K" in pause and "resume" in pause
        assert "3 days" in takeover and "#A3F9K" in takeover
        assert "resumed" in active and "#A3F9K" in active

    def test_confirmations_are_deterministic(self):
        assert ac.owner_confirmation(ac.MODE_PAUSED, "#A3F9K") == \
            ac.owner_confirmation(ac.MODE_PAUSED, "#A3F9K")
