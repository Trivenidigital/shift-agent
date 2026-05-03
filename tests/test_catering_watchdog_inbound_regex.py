"""PR-CF4 — catering-dispatcher-watchdog regex matches multi-word user names.

Linux-only — script imports safe_io which uses fcntl.

Loads the deployed script via SourceFileLoader (hyphen-named script pattern
documented in tests/_b1_helpers.py).
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="watchdog imports safe_io (fcntl-only)",
)

REPO = Path(__file__).resolve().parent.parent
WATCHDOG = REPO / "src" / "agents" / "catering" / "scripts" / "catering-dispatcher-watchdog"
PLATFORM_DIR = REPO / "src" / "platform"


def _load_watchdog():
    sys.path.insert(0, str(PLATFORM_DIR))
    loader = importlib.machinery.SourceFileLoader("watchdog_under_test", str(WATCHDOG))
    spec = importlib.util.spec_from_file_location(
        "watchdog_under_test", str(WATCHDOG), loader=loader
    )
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def watchdog_with_synthetic_log(tmp_path, monkeypatch):
    """Loads the watchdog module and points GATEWAY_LOG at a synthetic file."""
    mod = _load_watchdog()
    log = tmp_path / "agent.log"
    monkeypatch.setattr(mod, "GATEWAY_LOG", log)
    return mod, log


def test_regex_matches_multi_word_user_name(watchdog_with_synthetic_log):
    """User name with spaces (the actual bug from 2026-05-03 srilu)."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 17:45:54,984 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Srini Yalavarthi(Bangaru) "
        "chat=17329837841@s.whatsapp.net msg='Bro any update?'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("17329837841@s.whatsapp.net")
    assert result == "Bro any update?"


def test_regex_matches_single_word_user_name(watchdog_with_synthetic_log):
    """Backwards-compat: single-token user names still match."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 18:00:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Vizora chat=918522041562@s.whatsapp.net "
        "msg='hello'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("918522041562@s.whatsapp.net")
    assert result == "hello"


def test_regex_matches_user_with_special_chars(watchdog_with_synthetic_log):
    """User names with parens, periods, hyphens, apostrophes."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 18:30:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Mary O'Brien (Cashier - Day Shift) "
        "chat=15555551234@s.whatsapp.net msg='need coverage tomorrow'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("15555551234@s.whatsapp.net")
    assert result == "need coverage tomorrow"


def test_regex_returns_none_for_no_match(watchdog_with_synthetic_log):
    """Chat_id not in log → returns None (not silent error)."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:00:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=SomeoneElse chat=99999999@s.whatsapp.net "
        "msg='hi'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("17329837841@s.whatsapp.net")
    assert result is None


def test_regex_lid_to_jid_cross_suffix_normalization(watchdog_with_synthetic_log):
    """LID-vs-JID normalization: log line uses @s.whatsapp.net while caller
    passes @lid. Per watchdog lines 241-242, both sides strip the suffix and
    compare numeric parts. Tests the actual cross-suffix code path."""
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:30:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Anjali Iyer chat=201975216009469@s.whatsapp.net "
        "msg='accept the shift'\n",
        encoding="utf-8",
    )
    # Caller passes @lid; log has @s.whatsapp.net. Both split to '201975216009469'.
    result = mod.find_inbound_text_for("201975216009469@lid")
    assert result == "accept the shift"


def test_regex_picks_most_recent_match(watchdog_with_synthetic_log):
    """Multiple matches for same chat_id → returns the LAST (most recent).

    Test asserts deployed semantic at watchdog line 253 (`return matches[-1]`).
    If implementation switches to first-match semantics, this test fails — that
    is intentional as a guard against silent regression.
    """
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:00:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Test User chat=17329837841@s.whatsapp.net "
        "msg='first'\n"
        "2026-05-03 19:01:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Test User chat=17329837841@s.whatsapp.net "
        "msg='second'\n"
        "2026-05-03 19:02:00,000 INFO gateway.run: inbound message: "
        "platform=whatsapp user=Test User chat=17329837841@s.whatsapp.net "
        "msg='third'\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("17329837841@s.whatsapp.net")
    assert result == "third"


def test_regex_handles_double_quoted_msg(watchdog_with_synthetic_log):
    """When the message contains an apostrophe, Python's `%r` formatter
    switches to double-quoted repr. Real production lines use this form
    (estimated ~30% per design review). ast.literal_eval handles both styles
    via the existing logic at watchdog lines 244-247.
    """
    mod, log = watchdog_with_synthetic_log
    log.write_text(
        "2026-05-03 19:45:00,000 INFO gateway.run: inbound message: "
        'platform=whatsapp user=Anjali Iyer chat=15555551234@s.whatsapp.net '
        "msg=\"Hi Boss, I'm Anjali, can't come tomorrow\"\n",
        encoding="utf-8",
    )
    result = mod.find_inbound_text_for("15555551234@s.whatsapp.net")
    assert result == "Hi Boss, I'm Anjali, can't come tomorrow"
