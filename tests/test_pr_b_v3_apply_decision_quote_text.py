"""PR-B v3 commit 2 — apply-decision --quote-text-stdin + truth-guard + normalize.

Tests the three new helpers added to apply-catering-owner-decision:
  _normalize_quote_text     — Cc/Cf strip + markdown strip + length cap
  _truth_guard_check        — headcount integer + ISO event_date sanity check
  _emit_quote_skill_failed_best_effort — best-effort audit emission

Hyphen-named-script load pattern matches tests/test_validate_sender_block.py.
Unit-test scope only; Linux-only subprocess flow tested at PR-review time
via runtime probe (synthetic-retry-harness extension).

Linux-only: apply-script transitively imports safe_io which imports fcntl
at module level. Source-only checks (template-machinery deleted, flag
present) are split into a separate Windows-runnable test class.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APPLY = _REPO_ROOT / "src" / "agents" / "catering" / "scripts" / "apply-catering-owner-decision"

# Module-level skip for the import-dependent tests.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="apply-script imports safe_io which imports fcntl (Linux-only)",
)

# Source-only tests (no import) live in test_pr_b_v3_static.py — not in this file.

# Add platform path so the apply-script's `from schemas import ...` works.
sys.path.insert(0, str(_REPO_ROOT / "src" / "platform"))

if platform.system() != "Windows":
    _loader = importlib.machinery.SourceFileLoader("apply_decision_v3", str(_APPLY))
    _spec = importlib.util.spec_from_loader("apply_decision_v3", _loader)
    apply_mod = importlib.util.module_from_spec(_spec)
    _loader.exec_module(apply_mod)
else:
    apply_mod = None  # type: ignore[assignment]

def _load_apply_module_with_env(monkeypatch, **env: str):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module_name = f"apply_decision_env_{abs(hash(tuple(sorted(env.items()))))}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(_APPLY))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ──────────────── _normalize_quote_text ────────────────


def test_normalize_strips_zero_width_chars():
    """Cf category — zero-width space, joiner, non-joiner stripped."""
    s = "Hello​World‌‍!"  # ZWSP + ZWNJ + ZWJ
    assert apply_mod._normalize_quote_text(s) == "HelloWorld!"


def test_normalize_strips_rtl_override():
    """Cf category — RTL-override (Trojan-Source class) stripped."""
    s = "$10‮0‬00 deposit"  # RLO embedded
    out = apply_mod._normalize_quote_text(s)
    assert "‮" not in out
    assert "‬" not in out


def test_normalize_strips_control_chars():
    """Cc category — NUL/BEL/ESC stripped; \\n preserved."""
    s = "Line1\x00\x07\x1bLine2\nLine3"
    out = apply_mod._normalize_quote_text(s)
    assert "\x00" not in out
    assert "\x07" not in out
    assert "\x1b" not in out
    assert "\n" in out
    assert "Line2" in out


def test_normalize_normalizes_crlf():
    s = "Line1\r\nLine2\rLine3\nLine4"
    out = apply_mod._normalize_quote_text(s)
    assert "\r" not in out
    assert out.count("\n") == 3


def test_normalize_strips_markdown_delimiters():
    s = "Hello *world* this is _emphasized_ and ~struck~ and `code` too"
    out = apply_mod._normalize_quote_text(s)
    assert "*" not in out
    assert "_" not in out
    assert "~" not in out
    assert "`" not in out
    assert "Hello world" in out


def test_normalize_caps_length_at_600():
    s = "x" * 1000
    out = apply_mod._normalize_quote_text(s)
    assert len(out) == 600


def test_normalize_plain_text_passthrough():
    s = "Hi Anjali, for 50 guests on 2026-05-10 we'll prepare a buffet. Thanks!"
    out = apply_mod._normalize_quote_text(s)
    assert out == s


def test_normalize_unicode_passthrough():
    """Non-Latin scripts pass through untouched (only Cc/Cf stripped, not Lo etc.)."""
    s = "Namaste! 50 अतिथि के लिए"  # Devanagari
    out = apply_mod._normalize_quote_text(s)
    assert "अतिथि" in out


# ──────────────── _truth_guard_check ────────────────


def test_leads_path_override_derives_matching_lock_path(monkeypatch, tmp_path):
    leads_path = tmp_path / "state" / "catering-leads.json"
    module = _load_apply_module_with_env(
        monkeypatch,
        SHIFT_AGENT_LEADS_PATH=str(leads_path),
    )
    assert module.LEADS_PATH == leads_path
    assert module.LEADS_LOCK == Path(str(leads_path) + ".lock")


def test_config_failure_audit_uses_overridden_log_path(monkeypatch, tmp_path):
    log_path = tmp_path / "logs" / "decisions.log"
    module = _load_apply_module_with_env(
        monkeypatch,
        SHIFT_AGENT_LOG_PATH=str(log_path),
    )
    captured = []

    def fail_load_config(*_args, **_kwargs):
        raise RuntimeError("bad config")

    def capture_config_failure(config_path, exc, *, log_path=None):
        captured.append(log_path)

    monkeypatch.setattr(module, "load_yaml_model", fail_load_config)
    monkeypatch.setattr(module, "log_config_load_failed_best_effort", capture_config_failure)
    monkeypatch.setattr(sys, "argv", [
        "apply-catering-owner-decision",
        "--code", "#ABCDE",
        "--decision", "approve",
        "--sender-role", "owner",
    ])

    assert module.main() == module.EXIT_SCHEMA_VIOLATION
    assert captured == [log_path]


def test_state_divergence_audit_callsite_passes_overridden_log_path():
    source = _APPLY.read_text(encoding="utf-8")
    assert "log_quote_sent_lead_missing_best_effort(" in source
    assert "log_path=LOG_PATH" in source


def _make_lead(headcount=None, event_date=None):
    """Minimal duck-typed lead for truth-guard tests."""
    return SimpleNamespace(
        extracted=SimpleNamespace(
            headcount=headcount,
            event_date=event_date,
        ),
    )


def test_truth_guard_accepts_clean_quote_with_headcount_and_date():
    lead = _make_lead(headcount=50, event_date="2026-05-10")
    quote = "For 50 guests on 2026-05-10, we'll prepare a buffet. Thanks!"
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert passed
    assert reason == ""


def test_truth_guard_accepts_for_50_comma_prose():
    """R4 H-TG-2: 'for 50, ...' must NOT be rejected as substring of 50,000."""
    lead = _make_lead(headcount=50, event_date=None)
    quote = "For 50, our buffet style spread covers all dietary needs."
    passed, _ = apply_mod._truth_guard_check(quote, lead)
    assert passed


def test_truth_guard_rejects_substring_of_larger_number():
    """50,000 must NOT count as headcount=50."""
    lead = _make_lead(headcount=50, event_date=None)
    quote = "We've served over 50,000 customers and would love to host you for a similar event."
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert not passed
    assert "headcount=50" in reason


def test_truth_guard_rejects_decimal_lookalike():
    """50.5 must NOT count as headcount=50."""
    lead = _make_lead(headcount=50, event_date=None)
    quote = "Our minimum order is 50.5 lbs of biryani per event."
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert not passed
    assert "headcount=50" in reason


def test_truth_guard_rejects_wrong_headcount():
    lead = _make_lead(headcount=50, event_date=None)
    quote = "For 150 guests, we'll prepare a buffet. Thanks!"
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert not passed
    assert "50" in reason


def test_truth_guard_rejects_missing_iso_date():
    """LLM emitted prose date but no ISO — design v3 mandates ISO presence."""
    lead = _make_lead(headcount=50, event_date="2026-05-10")
    quote = "For 50 guests on Saturday May 10, we'll prepare a buffet. Thanks!"
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert not passed
    assert "2026-05-10" in reason


def test_truth_guard_accepts_iso_in_parens():
    """SKILL prompt instructs '(YYYY-MM-DD)' — verify that pattern accepts."""
    lead = _make_lead(headcount=50, event_date="2026-05-10")
    quote = "For 50 guests on Saturday, May 10 (2026-05-10), buffet ready. Thanks!"
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert passed
    assert reason == ""


def test_truth_guard_skips_checks_when_lead_fields_none():
    """No headcount + no event_date in lead = no constraints to violate."""
    lead = _make_lead(headcount=None, event_date=None)
    quote = "Thank you for your inquiry. We'll be in touch shortly!"
    passed, reason = apply_mod._truth_guard_check(quote, lead)
    assert passed


def test_truth_guard_only_checks_event_date_when_set():
    lead = _make_lead(headcount=50, event_date=None)
    quote = "For 50 guests, we'll prepare a buffet. No date specified yet."
    passed, _ = apply_mod._truth_guard_check(quote, lead)
    assert passed


# ──────────────── _emit_quote_skill_failed_best_effort ────────────────


def test_emit_skill_failed_writes_audit_row(tmp_path):
    """Happy path: writes a valid CateringQuoteSkillFailed NDJSON row."""
    log_path = tmp_path / "decisions.log"
    with patch.object(apply_mod, "LOG_PATH", log_path):
        apply_mod._emit_quote_skill_failed_best_effort(
            "L0042", "#ABCDE", "truth_guard_failed",
            "headcount=50 not present in drafted prose",
        )
    assert log_path.exists()
    import json as _json
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = _json.loads(line)
    assert entry["type"] == "catering_quote_skill_failed"
    assert entry["lead_id"] == "L0042"
    assert entry["code"] == "#ABCDE"
    assert entry["reason"] == "truth_guard_failed"
    assert "headcount=50" in entry["detail"]


def test_emit_skill_failed_does_not_raise_on_invalid_reason(tmp_path, capsys):
    """Best-effort contract: bad reason → WARN to stderr, no raise."""
    log_path = tmp_path / "decisions.log"
    with patch.object(apply_mod, "LOG_PATH", log_path):
        # Intentionally pass an invalid reason — Pydantic validates.
        apply_mod._emit_quote_skill_failed_best_effort(
            "L1", "#ABCDE", "some_invalid_reason", "detail",
        )
    err = capsys.readouterr().err
    assert "WARN" in err
    assert "CateringQuoteSkillFailed" in err


def test_emit_skill_failed_does_not_raise_on_disk_error(tmp_path, capsys):
    """Best-effort contract: write failure → WARN, no raise."""
    log_path = tmp_path / "decisions.log"
    with (
        patch.object(apply_mod, "LOG_PATH", log_path),
        patch.object(apply_mod, "ndjson_append", side_effect=OSError("disk full")),
    ):
        apply_mod._emit_quote_skill_failed_best_effort(
            "L1", "#ABCDE", "truth_guard_failed", "test",
        )
    err = capsys.readouterr().err
    assert "WARN" in err


def test_emit_skill_failed_caps_detail_at_2000_chars(tmp_path):
    """Pydantic max_length=2000 on detail; helper truncates before emit."""
    log_path = tmp_path / "decisions.log"
    long_detail = "x" * 5000
    with patch.object(apply_mod, "LOG_PATH", log_path):
        apply_mod._emit_quote_skill_failed_best_effort(
            "L1", "#ABCDE", "truth_guard_failed", long_detail,
        )
    import json as _json
    entry = _json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert len(entry["detail"]) == 2000


# ──────────────── argparse + flag wiring ────────────────


    # source-only tests moved to test_pr_b_v3_static.py (Windows-runnable).
