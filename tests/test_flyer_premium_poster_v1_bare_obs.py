"""Premium Poster v1 — bare/WhatsApp-direct path observability (2026-07-02).

The bare path opts into the premium branch but previously consumed the outcome
NOWHERE (review finding SF-1/PR-B2/FM-2/FA-4): a premium fire on the
customer-direct path wrote zero decisions.log rows. These tests pin the new
bare emitter: zero rows unless armed; attempted/eligible/selected/fallback
mirror the managed ladder; the QA verdict pairs only with a DELIVERED poster.
PIL-independent but flyer-named (excluded from send-path-ci by policy).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic")

from agents.flyer import bare_render  # noqa: E402


def _outcome(delivered, status="delivered", reason="none", n=1, wi=0, comp=8.0, of="concept_preview"):
    return SimpleNamespace(delivered=delivered, status=status, reason=reason, n=n,
                           winner_index=wi, winner_composite=comp, output_format=of)


def _rmod_stub(*, armed, outcome):
    return SimpleNamespace(
        consume_premium_poster_v1_outcome=lambda: outcome,
        _premium_poster_v1_armed=lambda p: armed,
    )


def _rows(log: Path):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def audit_log(tmp_path, monkeypatch):
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    return log


def test_not_armed_emits_zero_rows(audit_log, monkeypatch):
    monkeypatch.setattr(bare_render, "_render_mod",
                        lambda: _rmod_stub(armed=False, outcome=_outcome(True)))
    out = bare_render._consume_and_emit_premium_poster_v1_bare(SimpleNamespace(), "123@s.whatsapp.net")
    assert out is None
    assert _rows(audit_log) == []  # dormant: byte-identical AND audit-silent


def test_armed_delivered_emits_attempted_eligible_selected(audit_log, monkeypatch):
    monkeypatch.setattr(bare_render, "_render_mod",
                        lambda: _rmod_stub(armed=True, outcome=_outcome(True)))
    out = bare_render._consume_and_emit_premium_poster_v1_bare(SimpleNamespace(), "123@s.whatsapp.net")
    assert out is not None and out.delivered is True
    events = [r["event"] for r in _rows(audit_log)]
    assert events == [
        "premium_poster_v1_bare_attempted",
        "premium_poster_v1_bare_eligible",
        "premium_poster_v1_bare_selected",
    ]
    assert all(r["type"] == "flyer_premium_poster_v1_bare" for r in _rows(audit_log))
    assert all(r["chat_id"] == "123@s.whatsapp.net" for r in _rows(audit_log))


def test_armed_fallback_emits_reason(audit_log, monkeypatch):
    monkeypatch.setattr(bare_render, "_render_mod", lambda: _rmod_stub(
        armed=True, outcome=_outcome(False, status="fallback", reason="no_food_winner:check_error=1")))
    out = bare_render._consume_and_emit_premium_poster_v1_bare(SimpleNamespace(), "123@s.whatsapp.net")
    assert out is not None and out.delivered is False
    rows = _rows(audit_log)
    assert [r["event"] for r in rows] == [
        "premium_poster_v1_bare_attempted",
        "premium_poster_v1_bare_eligible",
        "premium_poster_v1_bare_fallback_reason",
    ]
    assert rows[-1]["reason"] == "no_food_winner:check_error=1"


def test_armed_but_branch_not_entered_emits_ineligible(audit_log, monkeypatch):
    monkeypatch.setattr(bare_render, "_render_mod", lambda: _rmod_stub(armed=True, outcome=None))
    out = bare_render._consume_and_emit_premium_poster_v1_bare(SimpleNamespace(), "123@s.whatsapp.net")
    assert out is None
    rows = _rows(audit_log)
    assert [r["event"] for r in rows] == [
        "premium_poster_v1_bare_attempted",
        "premium_poster_v1_bare_fallback_reason",
    ]
    assert rows[-1]["reason"] == "ineligible"


def test_final_pairs_only_with_delivered(audit_log):
    bare_render._emit_premium_poster_v1_bare_final(_outcome(True), "c@x", qa_passed=True)
    bare_render._emit_premium_poster_v1_bare_final(_outcome(True), "c@x", qa_passed=False)
    bare_render._emit_premium_poster_v1_bare_final(_outcome(False, status="fallback"), "c@x", qa_passed=True)
    bare_render._emit_premium_poster_v1_bare_final(None, "c@x", qa_passed=True)
    rows = _rows(audit_log)
    assert [r["event"] for r in rows] == [
        "premium_poster_v1_bare_final_pass",
        "premium_poster_v1_bare_final_fail",
    ]
    assert rows[0]["qa_status"] == "passed" and rows[1]["qa_status"] == "failed"


def test_schema_round_trips_through_log_entry_union():
    # The new bare variant must parse back through the LogEntry discriminated union
    # (decisions.log readers use it) — additive, never breaks existing rows.
    from datetime import datetime, timezone

    import schemas as platform_schemas

    entry = platform_schemas.FlyerPremiumPosterV1Bare(
        ts=datetime.now(timezone.utc), chat_id="123@s.whatsapp.net",
        event="premium_poster_v1_bare_selected", n=1, winner_index=0)
    line = entry.model_dump_json()
    from pydantic import TypeAdapter
    parsed = TypeAdapter(platform_schemas.LogEntry).validate_json(line)
    assert parsed.type == "flyer_premium_poster_v1_bare"
    assert parsed.event == "premium_poster_v1_bare_selected"
