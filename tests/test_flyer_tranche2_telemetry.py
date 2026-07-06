"""Tranche-2 telemetry emitters (census top-3 observability gaps, 2026-07-06).

Three previously-silent flyer surfaces now emit auditable decisions.log rows:

  1. CD-v2 applied  — FlyerCreativeDirectionV2Applied, emitted by the managed
     concept emitter when the CD-v2 gate ran a render (census D10 delete-gate:
     the ``consumed`` bool proves whether the carrier shapes a primary render).
  2. revision_apply — FlyerRevisionApplyOutcome, emitted by the LIVE default-ON
     bare uniform-price revision-apply handler (previously fully silent).
  3. visual_qa_skipped + semantic_brief_outcome — the FLYER_BARE_SKIP_VISUAL_QA
     break-glass and the legacy Hermes semantic-brief provider (used vs fell-back).

Every test asserts BOTH that the row lands AND that it round-trips through the
LogEntry discriminated union (the strongest row-landing guarantee). PIL-guarded
+ flyer-named (registered in flyer-premium-ci; excluded from send-path-ci).
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("PIL")  # render.py imports PIL at module scope

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "src", ROOT / "src" / "platform", ROOT / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Windows test hosts lack the Unix-only `fcntl` that safe_io imports at top level.
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ModuleNotFoundError:
        import types as _types

        _fcntl_stub = _types.ModuleType("fcntl")
        _fcntl_stub.LOCK_EX, _fcntl_stub.LOCK_UN, _fcntl_stub.LOCK_NB = 2, 8, 4
        _fcntl_stub.flock = lambda *_a, **_k: None
        sys.modules["fcntl"] = _fcntl_stub

from pydantic import TypeAdapter  # noqa: E402

from agents.flyer import bare_render, render, semantic_brief  # noqa: E402
from agents.flyer.extraction_seam import extract_text_facts_seam  # noqa: E402
from schemas import FlyerRequestFields, LogEntry  # noqa: E402

_LOG_ENTRY = TypeAdapter(LogEntry)


def _rows(log: Path):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _assert_parses(row: dict, expected_type: str):
    """Row-landing guarantee: the emitted row validates back through LogEntry to
    the expected variant (not the _UnknownLogEntry fallback)."""
    parsed = _LOG_ENTRY.validate_json(json.dumps(row))
    assert parsed.type == expected_type, (parsed.type, expected_type)
    return parsed


def _load_gen_script():
    path = ROOT / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("genflyer_tranche2_mod", loader=None, origin=str(path)))
    mod.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


# ── 1. CD-v2 applied ──────────────────────────────────────────────────────────

@dataclass
class _FakeResolved:
    offer_priority: str = "high"
    hero_name: str = "biryani"
    theme_family: str = ""
    mood: str = ""


def _cd_project():
    return SimpleNamespace(raw_request="Any item $5.99 this week",
                           fields=SimpleNamespace(notes=""), locked_facts=[],
                           creative_direction=None)


def test_cdv2_populate_sets_outcome(monkeypatch):
    render.consume_creative_direction_v2_outcome()  # clear any leftover
    monkeypatch.setattr(render, "propose_creative_brief_v2",
                        lambda *a, **k: SimpleNamespace(request_intent="new"))
    monkeypatch.setattr(render, "resolve_creative_direction", lambda brief, facts: _FakeResolved())
    monkeypatch.setattr(render, "select_poster_archetype", lambda ri, op="medium": "offer_forward")
    proj = _cd_project()
    render._populate_creative_direction_v2(proj)
    assert isinstance(proj.creative_direction, dict)
    out = render.consume_creative_direction_v2_outcome()
    assert out is not None
    assert out.populated is True and out.consumed is False
    assert out.request_intent == "new"
    assert out.poster_archetype == "offer_forward"
    assert out.offer_priority == "high"
    # consume clears -> no stale value leaks to the next render
    assert render.consume_creative_direction_v2_outcome() is None


def test_cdv2_populate_failure_records_not_populated(monkeypatch):
    render.consume_creative_direction_v2_outcome()
    monkeypatch.setattr(render, "propose_creative_brief_v2",
                        lambda *a, **k: SimpleNamespace(request_intent="new"))

    def _boom(*a, **k):
        raise RuntimeError("resolver down")

    monkeypatch.setattr(render, "resolve_creative_direction", _boom)
    proj = _cd_project()
    render._populate_creative_direction_v2(proj)
    assert proj.creative_direction is None  # carrier stays None; render never blocked
    out = render.consume_creative_direction_v2_outcome()
    assert out is not None and out.populated is False and out.consumed is False


def test_cdv2_emit_dormant_when_gate_did_not_run(monkeypatch, tmp_path):
    mod = _load_gen_script()
    rows = []
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "consume_creative_direction_v2_outcome", lambda: None)
    mod._emit_creative_direction_v2_applied(
        tmp_path / "d.log", SimpleNamespace(project_id="F0001", version=2))
    assert rows == []  # None outcome => byte-identical + audit-silent


def test_cdv2_emit_populated_not_consumed_row(monkeypatch, tmp_path):
    mod = _load_gen_script()
    rows = []
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "consume_creative_direction_v2_outcome",
                        lambda: SimpleNamespace(populated=True, consumed=False, request_intent="new",
                                                poster_archetype="offer_forward", offer_priority="high"))
    mod._emit_creative_direction_v2_applied(
        tmp_path / "d.log", SimpleNamespace(project_id="F0001", version=2))
    assert len(rows) == 1
    row = json.loads(rows[0].model_dump_json())
    parsed = _assert_parses(row, "flyer_creative_direction_v2_applied")
    assert parsed.populated is True and parsed.consumed is False
    assert parsed.project_id == "F0001" and parsed.request_intent == "new"


def test_cdv2_emit_consumed_row(monkeypatch, tmp_path):
    mod = _load_gen_script()
    rows = []
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "consume_creative_direction_v2_outcome",
                        lambda: SimpleNamespace(populated=True, consumed=True, request_intent="offer",
                                                poster_archetype="", offer_priority="medium"))
    mod._emit_creative_direction_v2_applied(
        tmp_path / "d.log", SimpleNamespace(project_id="F0002", version=1))
    parsed = _assert_parses(json.loads(rows[0].model_dump_json()), "flyer_creative_direction_v2_applied")
    assert parsed.consumed is True


# ── 2. revision_apply outcome ─────────────────────────────────────────────────

def test_revision_apply_dormant_when_flag_off(tmp_path, monkeypatch):
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    monkeypatch.setattr(bare_render, "REVISION_APPLY_ENABLED", False)
    status, payload = bare_render.render_revision_apply("123@s.whatsapp.net", "$8.99 header")
    assert status == bare_render.REVISION_NEEDED and payload is None
    assert _rows(log) == []  # flag off => byte-identical + audit-silent


def test_revision_apply_no_session_emits_revision_needed(tmp_path, monkeypatch):
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    monkeypatch.setattr(bare_render, "REVISION_APPLY_ENABLED", True)
    monkeypatch.setattr(bare_render, "_load_session", lambda chat_id: None)
    status, payload = bare_render.render_revision_apply("123@s.whatsapp.net", "$8.99 header")
    assert status == bare_render.REVISION_NEEDED and payload is None
    rows = _rows(log)
    assert len(rows) == 1
    parsed = _assert_parses(rows[0], "flyer_revision_apply_outcome")
    assert parsed.handler == "revision_apply"
    assert parsed.status == "revision_needed"
    assert parsed.reason == "resend_full_details"
    assert parsed.chat_id == "123@s.whatsapp.net"


def test_revision_apply_failclosed_carries_blockers(tmp_path, monkeypatch):
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    monkeypatch.setattr(bare_render, "REVISION_APPLY_ENABLED", True)
    monkeypatch.setattr(bare_render, "_render_revision_apply_impl",
                        lambda chat_id, raw_text: (bare_render.FAILCLOSED, ["reoverlay_error:ValueError"]))
    status, payload = bare_render.render_revision_apply("c1", "$8.99 header")
    assert status == bare_render.FAILCLOSED
    parsed = _assert_parses(_rows(log)[0], "flyer_revision_apply_outcome")
    assert parsed.status == "failclosed"
    assert "reoverlay_error:ValueError" in parsed.reason


def test_revision_apply_send_emits_send(tmp_path, monkeypatch):
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    monkeypatch.setattr(bare_render, "REVISION_APPLY_ENABLED", True)
    monkeypatch.setattr(bare_render, "_render_revision_apply_impl",
                        lambda chat_id, raw_text: (bare_render.SEND, b"\x89PNG"))
    status, payload = bare_render.render_revision_apply("c1", "$8.99 header")
    assert status == bare_render.SEND and payload == b"\x89PNG"
    parsed = _assert_parses(_rows(log)[0], "flyer_revision_apply_outcome")
    assert parsed.status == "send" and parsed.reason == ""


# ── 3a. visual_qa_skipped break-glass ─────────────────────────────────────────

def test_visual_qa_skipped_emits_row(tmp_path, monkeypatch):
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    monkeypatch.setattr(bare_render, "_skip_visual_qa_enabled", lambda: True)
    proj = SimpleNamespace(project_id="F0001", customer_phone="")
    ok, blockers = bare_render.run_visual_qa(b"\x89PNG", proj)
    assert ok is True and blockers == ["visual_qa_disabled"]
    parsed = _assert_parses(_rows(log)[0], "flyer_visual_qa_skipped")
    assert parsed.project_id == "F0001" and parsed.reason == "break_glass_flag"


def test_visual_qa_skipped_dormant_when_flag_off(monkeypatch):
    monkeypatch.setattr(bare_render, "_skip_visual_qa_enabled", lambda: False)
    calls = []
    monkeypatch.setattr(bare_render, "_emit_visual_qa_skipped", lambda p: calls.append(p))

    class _VQStub:
        @staticmethod
        def run_visual_qa(*a, **k):
            raise RuntimeError("boom")  # forces the caught qa_error branch

    monkeypatch.setattr(bare_render, "_visual_qa_mod", lambda: _VQStub)
    proj = SimpleNamespace(project_id="F0001", customer_phone="")
    bare_render.run_visual_qa(b"x", proj)
    assert calls == []  # emitter never reached when the break-glass is off


# ── 3b. semantic_brief provenance ─────────────────────────────────────────────

def test_semantic_provenance_provider_absent():
    prov: dict = {}
    semantic_brief.build_semantic_flyer_brief(
        FlyerRequestFields(notes="Any item $5.99"), "Any item $5.99",
        provider=None, provenance=prov)
    assert prov == {"status": "fell_back", "reason": "provider_absent", "provider_present": False}


def test_semantic_provenance_provider_used():
    prov: dict = {}
    semantic_brief.build_semantic_flyer_brief(
        FlyerRequestFields(notes="Any item $5.99 this week"), "Any item $5.99 this week",
        provider=lambda f, r: {"pricing_structure": "Any item $5.99"}, provenance=prov)
    assert prov["status"] == "provider_used" and prov["provider_present"] is True


def test_semantic_provenance_provider_empty():
    prov: dict = {}
    semantic_brief.build_semantic_flyer_brief(
        FlyerRequestFields(notes="hello there"), "hello there",
        provider=lambda f, r: None, provenance=prov)
    assert prov["status"] == "fell_back" and prov["reason"] == "provider_empty"
    assert prov["provider_present"] is True


def test_seam_emits_semantic_brief_outcome_on_legacy(monkeypatch):
    monkeypatch.delenv("FLYER_EXTRACTION_V2", raising=False)  # v2 off => legacy path runs
    events = []
    extract_text_facts_seam(
        FlyerRequestFields(notes="Any item $5.99"), "Any item $5.99",
        audit=lambda e, p: events.append((e, p)), seam="bare_render")
    sb = [p for e, p in events if e == "semantic_brief_outcome"]
    assert len(sb) == 1
    assert sb[0]["status"] in ("provider_used", "fell_back")
    assert "provider_present" in sb[0]


def test_bare_build_locked_facts_lands_semantic_brief_row(tmp_path, monkeypatch):
    monkeypatch.delenv("FLYER_EXTRACTION_V2", raising=False)
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(bare_render, "AUDIT_LOG_PATH", log)
    customer = SimpleNamespace(status="", business_name="")
    fields = FlyerRequestFields(notes="Any item $5.99")
    bare_render._build_locked_facts(customer, fields, "Any item $5.99", "m1", None)
    sb = [r for r in _rows(log) if r["type"] == "flyer_semantic_brief_outcome"]
    assert len(sb) == 1
    parsed = _assert_parses(sb[0], "flyer_semantic_brief_outcome")
    assert parsed.seam == "bare_render"
    assert parsed.status in ("provider_used", "fell_back")
