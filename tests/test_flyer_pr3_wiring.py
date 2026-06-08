"""PR3 — Creative-Director wiring into the CONFIRMED live bare flyer render.

These tests prove the wiring contract offline (NO network, NO real send):

  (a) flag off                              ⇒ legacy path; build_flyer_brief NOT
                                              called; audit reached=False, status=disabled
  (b) flag on + sender NOT allowlisted      ⇒ legacy path; audit status=not_allowlisted
  (c) flag on + allowlisted + brief "ok"    ⇒ CD path (textless bg + overlay, NOT the
                                              legacy integrated poster); audit status=ok
  (d) allowlisted + status="invalid"        ⇒ fail-safe (FAILCLOSED); legacy poster NOT
                                              called; audit status=invalid
  (e) allowlisted + status="unavailable"    ⇒ fail-safe (FAILCLOSED); no legacy poster;
                                              audit status=unavailable

build_flyer_brief, image generation (CD path), the legacy poster, the overlay, and
visual QA are all monkeypatched. Path setup mirrors test_flyer_creative_director.py
(src/platform + src/agents/flyer on sys.path, the way the flat VPS modules import).
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
for _p in (_SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bare_render as br  # noqa: E402


CHAT_ID = "1732555010@s.whatsapp.net"
SENDER = "1732555010"
RAW = "Make a Memorial Day flyer for Non Veg Combo $49.99 and Veg Combo $39.99."


# ── fakes ───────────────────────────────────────────────────────────────────


class _FakeCustomer:
    status = "active"
    business_name = "Lakshmi's Kitchen"
    customer_id = "CUST0001"
    # E.164 so FlyerProject.customer_phone (a strict E164 field) accepts the routable phone.
    business_whatsapp_number = "+17325550104"
    languages = ["en"]
    preferred_language = "en"


class _FakeBriefResult:
    def __init__(self, status, brief=None, errors=None, reason=""):
        self.status = status
        self.brief = brief
        self.errors = errors or []
        self.reason = reason


class _FakeBrief:
    # only the field the CD render path reads
    background_brief = "A textless patriotic cookout background, central area clear."


def _install_common(monkeypatch, *, brief_result=None, brief_spy=None):
    """Stub out resolve_customer + locked-fact build + intake fields + QA so each test
    isolates the wiring. Returns a dict of call recorders the test asserts on."""
    rec = {"cd_render": [], "legacy_poster": [], "audits": [], "brief_calls": []}

    monkeypatch.setattr(br, "resolve_customer", lambda *a, **k: _FakeCustomer())
    # locked facts: a fixed list (the wiring is what's under test, not facts.py).
    schemas = br._schemas()
    locked = [
        schemas.FlyerLockedFact(fact_id="business_name", label="Business",
                                value="Lakshmi's Kitchen", source="customer_profile", required=True),
        schemas.FlyerLockedFact(fact_id="contact_phone", label="Contact",
                                value="+1 732 555 0104", source="customer_profile", required=True),
    ]
    monkeypatch.setattr(br, "_build_locked_facts", lambda *a, **k: locked)
    monkeypatch.setattr(br, "_load_flyer_cfg", lambda: None)

    # intake fields — a REAL FlyerRequestFields (the project model validates it). Only
    # event_or_business_name is consulted by the conflict gate (left empty ⇒ no conflict).
    fake_fields = schemas.FlyerRequestFields()
    monkeypatch.setattr(br, "_intake_fields",
                        lambda: types.SimpleNamespace(_extract_fields=lambda *a, **k: fake_fields))

    # CD render + legacy poster: record-and-return so we can prove which path ran.
    def _fake_cd_render(project, background_brief):
        rec["cd_render"].append(background_brief)
        return b"CD_PNG"

    def _fake_legacy_poster(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        rec["legacy_poster"].append(strict_note)
        rec.setdefault("scene_direction", []).append(scene_direction)
        return b"LEGACY_PNG"

    monkeypatch.setattr(br, "_render_creative_director", _fake_cd_render)
    monkeypatch.setattr(br, "_generate_poster", _fake_legacy_poster)
    # QA always allows send (isolates wiring from the QA gate).
    monkeypatch.setattr(br, "run_visual_qa", lambda png, project: (True, []))
    # never touch decisions.log / never persist sessions during tests.
    monkeypatch.setattr(br, "_emit_creative_director_audit",
                        lambda **kw: rec["audits"].append(kw))
    monkeypatch.setattr(br, "_write_session", lambda *a, **k: None)

    # the brief builder spy — records every call; returns the configured result.
    def _brief(raw_request, locked_facts, business_profile, *a, **k):
        rec["brief_calls"].append((raw_request, list(locked_facts), business_profile))
        if brief_spy is not None:
            brief_spy(raw_request, locked_facts, business_profile)
        return brief_result if brief_result is not None else _FakeBriefResult("unavailable")

    fake_cb = types.SimpleNamespace(build_flyer_brief=_brief)
    monkeypatch.setattr(br, "_context_builder", lambda: fake_cb)
    return rec


# ── (a) flag off ⇒ legacy, audit reached=False, build_flyer_brief NOT called ──


def test_flag_off_uses_legacy_and_does_not_call_brief(monkeypatch):
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, raising=False)
    rec = _install_common(monkeypatch)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="m1", sender_phone=SENDER)

    assert status == br.SEND
    assert payload == b"LEGACY_PNG"          # legacy integrated poster ran
    assert rec["legacy_poster"]              # legacy poster called
    assert rec["cd_render"] == []            # CD path NOT taken
    assert rec["brief_calls"] == []          # build_flyer_brief NOT called (spy)
    # audit emitted, reached=False, status=disabled
    assert len(rec["audits"]) == 1
    a = rec["audits"][0]
    assert a["reached"] is False
    assert a["status"] == "disabled"
    assert a["allowlisted"] is False
    assert a["resolved_sender"] == SENDER


# ── (a2) flag off + bare render error ⇒ fail-closed with a DIAGNOSABLE blocker ─


def test_flag_off_render_error_blocker_is_diagnosable(monkeypatch):
    """A bare/integrated render failure (CD off) fails closed AND the blockers name the
    exact cause + stage + fact id/lengths (→ send.log), instead of an opaque
    render_error:FlyerRenderError. Operator obs request 2026-06-06. Behavior unchanged:
    still FAILCLOSED, and the generic retry-shape blocker is still present."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, raising=False)
    rec = _install_common(monkeypatch)
    FRE = br._render_mod().FlyerRenderError

    def _boom(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        raise FRE("OpenRouter image HTTP 402: requires more credits, or fewer max_tokens")
    monkeypatch.setattr(br, "_generate_poster", _boom)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="mre", sender_phone=SENDER)

    assert status == br.FAILCLOSED
    blob = " | ".join(payload)
    assert "render_error:FlyerRenderError" in blob                 # generic retry-shape blocker kept
    assert "402" in blob and "requires more credits" in blob       # DESCRIPTIVE cause surfaced
    assert "render_detail" in blob and "stage=generate_poster" in blob
    assert "facts[" in blob                                        # fact id/length summary
    assert rec["cd_render"] == [] and rec["brief_calls"] == []     # no CD path with flag off


def test_flag_off_clean_render_has_no_render_detail(monkeypatch):
    """A successful bare render (CD off) sends and carries NO render_detail noise."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, raising=False)
    rec = _install_common(monkeypatch)
    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="mok", sender_phone=SENDER)
    assert status == br.SEND and payload == b"LEGACY_PNG"


def test_wrong_brand_qa_retries_bare_render_without_saved_brand_assets(monkeypatch):
    """A saved customer logo/template can itself contain another business. When visual
    QA catches that, the bare WhatsApp send path must retry without saved brand assets
    before failing the customer."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, raising=False)
    _install_common(monkeypatch)
    calls = []

    def _poster(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        calls.append({
            "strict_note": strict_note,
            "fact_ids": [fact.fact_id for fact in project.locked_facts],
        })
        return b"WRONG_BRAND" if len(calls) == 1 else b"CLEAN_BRAND"

    def _qa(png, project):
        if png == b"WRONG_BRAND":
            return (False, ["visible wrong business/brand: Indian Cafe & Bakery"])
        return (True, [])

    monkeypatch.setattr(br, "_generate_poster", _poster)
    monkeypatch.setattr(br, "run_visual_qa", _qa)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="mbrand", sender_phone=SENDER)

    assert status == br.SEND
    assert payload == b"CLEAN_BRAND"
    assert len(calls) == 2
    assert "render:disable_brand_assets" not in calls[0]["fact_ids"]
    assert "render:disable_brand_assets" in calls[1]["fact_ids"]
    assert "visible wrong business/brand: Indian Cafe & Bakery" in calls[1]["strict_note"]


def test_render_error_detail_never_raises_into_render_path(monkeypatch):
    """Diagnostics must NEVER raise into the render path — even a render exception with a
    pathological __str__ still fails closed, not uncaught (Codex 2026-06-06)."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, raising=False)
    _install_common(monkeypatch)

    class _BadExc(Exception):
        def __str__(self):  # noqa: D401
            raise RuntimeError("boom in __str__")

    def _boom(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        raise _BadExc()
    monkeypatch.setattr(br, "_generate_poster", _boom)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="mbad", sender_phone=SENDER)
    assert status == br.FAILCLOSED  # diagnostics swallowed the bad __str__; no propagation


# ── (b) flag on + sender NOT allowlisted ⇒ legacy, status=not_allowlisted ─────


def test_flag_on_sender_not_allowlisted_uses_legacy(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, "19998887777,othersender")
    rec = _install_common(monkeypatch)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="m2", sender_phone=SENDER)

    assert status == br.SEND
    assert payload == b"LEGACY_PNG"
    assert rec["cd_render"] == []
    assert rec["brief_calls"] == []          # gate failed → brief never built
    a = rec["audits"][0]
    assert a["reached"] is False
    assert a["status"] == "not_allowlisted"
    assert a["allowlisted"] is False


# ── (c) flag on + allowlisted + brief "ok" ⇒ CD path, audit status=ok ─────────


def test_allowlisted_ok_renders_creative_director_path(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    # allowlist carries a +-prefixed entry to prove normalization matches the bare SENDER.
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, f"+{SENDER}, 19998887777")
    rec = _install_common(monkeypatch, brief_result=_FakeBriefResult("ok", brief=_FakeBrief()))

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="m3", sender_phone=SENDER)

    assert status == br.SEND
    assert payload == b"CD_PNG"              # CD path produced the bytes
    assert rec["cd_render"] == [_FakeBrief.background_brief]  # textless bg + overlay used
    assert rec["legacy_poster"] == []        # legacy integrated poster NOT called
    assert len(rec["brief_calls"]) == 1      # build_flyer_brief invoked once
    a = rec["audits"][-1]
    assert a["reached"] is True
    assert a["status"] == "ok"
    assert a["allowlisted"] is True


# ── (c2) allowlisted + context-builder failure ⇒ fail-safe + audit (Codex BLOCKER) ──


def test_allowlisted_context_builder_failure_fails_safe_and_audits(monkeypatch):
    # Codex PR3 regression: _context_builder() is resolved INSIDE the try, so an
    # import/resolution failure on the live flat deploy still fails closed AND emits the
    # routing audit (reached=True, status="unavailable") — never an uncaught raise, never
    # a skipped audit. Guards the operator's "logs prove the caller" gate.
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(monkeypatch)

    def _boom():
        raise ImportError("flyer_context_builder unresolved")
    monkeypatch.setattr(br, "_context_builder", _boom)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="m_cb", sender_phone=SENDER)

    assert status == br.FAILCLOSED           # fail-safe, not an uncaught raise
    assert rec["legacy_poster"] == []        # legacy integrated poster NOT called
    assert rec["cd_render"] == []            # CD render never reached
    a = rec["audits"][-1]
    assert a["reached"] is True              # armed branch entered
    assert a["status"] == "unavailable"      # builder failure ⇒ unavailable
    assert a["allowlisted"] is True


# ── (P2) CD render uses the overlay WRAPPER (fallback), not Pillow-only public ──


def test_cd_render_uses_overlay_wrapper_not_pillow_only(monkeypatch):
    # Codex PR3 P2: _render_creative_director must call render._apply_critical_text_overlay
    # (the wrapper with the /usr/bin/python3 system-Pillow fallback), NOT the public
    # apply_critical_text_overlay (Pillow-only) — else an armed render fails closed on a
    # venv without Pillow even though the deployed fallback would have worked.
    from pathlib import Path as _P
    monkeypatch.setattr(br, "_generate_image", lambda prompt, *, model: b"RAWBG")
    calls = {"wrapper": 0, "public": 0}

    def _wrapper(project, source, target, *, size, output_format):
        calls["wrapper"] += 1
        _P(target).write_bytes(b"CDPNG")

    def _public(*a, **k):
        calls["public"] += 1

    fake_rmod = types.SimpleNamespace(
        _apply_critical_text_overlay=_wrapper,
        apply_critical_text_overlay=_public,
    )
    monkeypatch.setattr(br, "_render_mod", lambda: fake_rmod)

    out = br._render_creative_director(object(), "a textless patriotic cookout background")

    assert out == b"CDPNG"
    assert calls["wrapper"] == 1   # fallback-capable wrapper used
    assert calls["public"] == 0    # Pillow-only public NOT used


# ── (d) allowlisted + status="invalid" ⇒ fail-safe, legacy poster NOT called ──


def test_allowlisted_invalid_fails_safe_not_legacy(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(
        monkeypatch,
        brief_result=_FakeBriefResult("invalid", errors=["omits required fact item:0:name"]),
    )

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="m4", sender_phone=SENDER)

    assert status == br.FAILCLOSED           # fail-safe / manual route
    assert rec["cd_render"] == []            # nothing rendered
    assert rec["legacy_poster"] == []        # legacy poster NEVER called on a firewall reject
    assert any("creative_director_invalid" in b for b in payload)
    a = rec["audits"][-1]
    assert a["reached"] is True
    assert a["status"] == "invalid"


# ── (e) allowlisted + status="unavailable" ⇒ fail-safe, no legacy ────────────


def test_allowlisted_unavailable_fails_safe_no_legacy(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(monkeypatch, brief_result=_FakeBriefResult("unavailable"))

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="m5", sender_phone=SENDER)

    assert status == br.FAILCLOSED
    assert rec["cd_render"] == []
    assert rec["legacy_poster"] == []        # NEVER the legacy integrated poster
    assert any("creative_director_unavailable" in b for b in payload)
    a = rec["audits"][-1]
    assert a["reached"] is True
    assert a["status"] == "unavailable"


# ── gate/normalization units + audit-schema field mapping ────────────────────


def test_gate_requires_flag_exactly_one_and_allowlist(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    # flag unset / not "1" ⇒ not armed regardless of allowlist
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    assert br._creative_director_armed(SENDER) is False
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "true")
    assert br._creative_director_armed(SENDER) is False
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    assert br._creative_director_armed(SENDER) is True
    # an unknown sender is not armed even with the flag on.
    assert br._creative_director_armed("15550000000") is False


def test_normalization_matches_across_formats(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, "+1 (732) 555-0104")
    # resolved as a bare phone, a +-prefixed phone, and a JID all normalize-match.
    for sender in ("17325550104", "+17325550104", "17325550104@s.whatsapp.net"):
        assert br._creative_director_armed(sender) is True


def test_resolved_sender_prefers_trusted_phone_then_jid(monkeypatch):
    # passed phone wins
    assert br._resolved_sender("c@s.whatsapp.net", "17325550104") == "17325550104"
    # else the phone embedded in a whatsapp JID
    assert br._resolved_sender("17325550104@s.whatsapp.net", None) == "17325550104"
    # else the chat_id itself (e.g. a LID JID) — never message content
    assert br._resolved_sender("999@lid", None) == "999@lid"


def test_audit_emitter_builds_valid_logentry(monkeypatch, tmp_path):
    """The real emitter writes a schema-valid FlyerCreativeDirectorRouted row with the
    required fields + MODULE_VERSION + module_file — provable from the decisions.log."""
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(br, "AUDIT_LOG_PATH", log)
    br._emit_creative_director_audit(
        chat_id=CHAT_ID, resolved_sender=SENDER, reached=False,
        status="disabled", allowlisted=False,
    )
    assert log.exists()
    import json
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["type"] == "flyer_creative_director_routed"
    assert row["creative_director_reached"] is False
    assert row["creative_director_status"] == "disabled"
    assert row["module_version"] == br.MODULE_VERSION == "pr3-creative-director"
    assert row["module_file"].endswith("bare_render.py")
    assert row["resolved_sender"] == SENDER
    assert row["allowlisted"] is False
    # and it round-trips through the LogEntry discriminated union.
    from pydantic import TypeAdapter
    schemas = br._schemas()
    back = TypeAdapter(schemas.LogEntry).validate_json(log.read_text(encoding="utf-8").strip())
    assert type(back).__name__ == "FlyerCreativeDirectorRouted"


# ── observability (2026-06-06): the audit row persists status + WHY ──────────────
# Proves a failed live retest is diagnosable from the row alone: invalid carries the
# validator errors, unavailable carries the classified reason, and a brief that
# validated "ok" but failed to RENDER is distinguishable from a clean ship.


def test_invalid_audit_carries_validator_errors(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    errs = ["omits required fact item:0:name", "unknown fact id zzz"]
    rec = _install_common(monkeypatch, brief_result=_FakeBriefResult("invalid", errors=errs))

    br.render_grounded(CHAT_ID, RAW, message_id="mi", sender_phone=SENDER)

    a = rec["audits"][-1]
    assert a["status"] == "invalid"
    assert a["errors"] == errs                       # full validator detail persisted
    assert a["error_summary"].startswith("invalid: omits required fact")


def test_unavailable_audit_carries_classified_reason(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(monkeypatch,
                          brief_result=_FakeBriefResult("unavailable", reason="timeout"))

    br.render_grounded(CHAT_ID, RAW, message_id="mu", sender_phone=SENDER)

    a = rec["audits"][-1]
    assert a["status"] == "unavailable"
    assert a["unavailable_reason"] == "timeout"
    assert a["error_summary"] == "unavailable:timeout"


def test_render_error_audit_status_ok_but_render_error_set(monkeypatch):
    """The brief validated (status stays "ok") but the render threw → the row must
    reveal the render failure so it is NOT mistaken for a shipped flyer (the exact
    live 2026-06-06 failure: status=ok, nothing shipped)."""
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(monkeypatch, brief_result=_FakeBriefResult("ok", brief=_FakeBrief()))

    def _boom_render(project, background_brief):
        raise RuntimeError("overlay blew up")
    monkeypatch.setattr(br, "_render_creative_director", _boom_render)

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="mr", sender_phone=SENDER)

    assert status == br.FAILCLOSED
    a = rec["audits"][-1]
    assert a["status"] == "ok"                       # brief WAS ok
    assert a["render_error"] == "RuntimeError"
    assert a["error_summary"] == "render_error:RuntimeError"


def test_clean_ship_audit_has_empty_error_summary(monkeypatch):
    """A flyer that actually ships leaves error_summary "" — the grep-able signal
    that separates a real send from a status=ok-but-not-shipped row."""
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(monkeypatch, brief_result=_FakeBriefResult("ok", brief=_FakeBrief()))

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="ms", sender_phone=SENDER)

    assert status == br.SEND
    a = rec["audits"][-1]
    assert a["status"] == "ok"
    assert a["error_summary"] == ""
    assert a["render_error"] == ""


def test_qa_fail_audit_marks_qa_failed(monkeypatch):
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    rec = _install_common(monkeypatch, brief_result=_FakeBriefResult("ok", brief=_FakeBrief()))
    monkeypatch.setattr(br, "run_visual_qa", lambda png, project: (False, ["THURRSDAY typo"]))

    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="mq", sender_phone=SENDER)

    assert status == br.FAILCLOSED
    a = rec["audits"][-1]
    assert a["status"] == "ok"
    assert a["error_summary"] == "qa_failed"
    assert a["errors"] == ["THURRSDAY typo"]


def test_audit_emitter_persists_observability_fields(monkeypatch, tmp_path):
    """The REAL emitter serializes + persists the new fields and the row still
    round-trips through the LogEntry discriminated union."""
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(br, "AUDIT_LOG_PATH", log)
    br._emit_creative_director_audit(
        chat_id=CHAT_ID, resolved_sender=SENDER, reached=True, status="unavailable",
        allowlisted=True, error_summary="unavailable:timeout",
        errors=["x" * 999], unavailable_reason="timeout", render_error="",
    )
    import json
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["unavailable_reason"] == "timeout"
    assert row["error_summary"] == "unavailable:timeout"
    assert len(row["errors"][0]) == 200              # per-entry truncation applied
    from pydantic import TypeAdapter
    schemas = br._schemas()
    TypeAdapter(schemas.LogEntry).validate_json(log.read_text(encoding="utf-8").strip())


def _run_grounded_with_real_emit(monkeypatch, tmp_path, brief_result, msg_id):
    """Drive render_grounded with the REAL _emit_creative_director_audit (not the spy)
    writing to a tmp decisions.log → returns the single persisted row (a dict)."""
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, "1")
    monkeypatch.setenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, SENDER)
    real_emit = br._emit_creative_director_audit  # capture BEFORE _install_common spies it
    _install_common(monkeypatch, brief_result=brief_result)
    log = tmp_path / "decisions.log"
    monkeypatch.setattr(br, "AUDIT_LOG_PATH", log)
    monkeypatch.setattr(br, "_emit_creative_director_audit", real_emit)  # restore real emitter
    br.render_grounded(CHAT_ID, RAW, message_id=msg_id, sender_phone=SENDER)
    import json
    return json.loads(log.read_text(encoding="utf-8").strip())


def test_end_to_end_invalid_persists_real_row_with_errors(monkeypatch, tmp_path):
    """render_grounded → REAL emitter → a persisted decisions.log row carries
    status="invalid" + the validator errors, and round-trips through LogEntry."""
    row = _run_grounded_with_real_emit(
        monkeypatch, tmp_path,
        _FakeBriefResult("invalid", errors=["omits required fact item:0:name"]), "me2e_inv")
    assert row["creative_director_status"] == "invalid"
    assert row["errors"] == ["omits required fact item:0:name"]
    assert row["error_summary"].startswith("invalid: omits required fact")
    from pydantic import TypeAdapter
    back = TypeAdapter(br._schemas().LogEntry).validate_json(__import__("json").dumps(row))
    assert type(back).__name__ == "FlyerCreativeDirectorRouted"


def test_end_to_end_unavailable_persists_real_row_with_reason(monkeypatch, tmp_path):
    """render_grounded → REAL emitter → a persisted row carries status="unavailable"
    + the classified unavailable_reason (proves the reason survives the full chain)."""
    row = _run_grounded_with_real_emit(
        monkeypatch, tmp_path, _FakeBriefResult("unavailable", reason="timeout"), "me2e_un")
    assert row["creative_director_status"] == "unavailable"
    assert row["unavailable_reason"] == "timeout"
    assert row["error_summary"] == "unavailable:timeout"


# ── re-roll: no-change "generate again" re-renders the saved session (operator Option 1, 2026-06-07) ──


_REROLL_TEXT = "I did not like, please generate this flyer again."


def _saved_session(*, sent_at=None, business="Lakshmi's Kitchen"):
    """A committed bare-flyer session dict (the shape _write_session persists) holding a validated
    graduation project — the exact input render_reroll re-renders verbatim."""
    import json
    from datetime import datetime, timezone
    schemas = br._schemas()
    now = datetime(2026, 6, 7, tzinfo=timezone.utc)
    project = schemas.FlyerProject(
        project_id="F0145", status="awaiting_final_approval", customer_phone="+17325550104",
        created_at=now, updated_at=now, original_message_id="m0",
        raw_request="Create a flyer to reflect the graduation. 2026 graduation parties. 10% off.",
        fields=schemas.FlyerRequestFields(event_or_business_name=business),
        locked_facts=[
            schemas.FlyerLockedFact(fact_id="business_name", label="Business", value=business,
                                    source="customer_profile", required=True),
            schemas.FlyerLockedFact(fact_id="campaign_title", label="Campaign",
                                    value="2026 Graduation Parties", source="customer_text", required=True),
            schemas.FlyerLockedFact(fact_id="pricing_structure", label="Pricing",
                                    value="10% off on entire order", source="customer_text", required=True),
        ],
    )
    return {
        "chat_id": CHAT_ID,
        "sent_at": sent_at if sent_at is not None else datetime.now(timezone.utc).isoformat(),
        "brief": "Create a graduation flyer for Lakshmi's Kitchen",
        "project": json.loads(project.model_dump_json()),
        "raw_background_path": "",
        "model": "google/gemini-2.5-flash-image",
        "output_size": [1080, 1350],
    }


def _install_reroll(monkeypatch, *, session=..., poster=None):
    """Stub the session load + a poster spy that records the project it re-rendered."""
    cap = {"projects": [], "stricts": [], "wrote_session": []}
    sess = _saved_session() if session is ... else session
    monkeypatch.setattr(br, "_load_session", lambda chat_id: sess)
    monkeypatch.setattr(br, "run_visual_qa", lambda png, project: (True, []))
    monkeypatch.setattr(br, "_write_session", lambda *a, **k: cap["wrote_session"].append((a, k)))

    def _spy_poster(project, *, strict_note="", raw_bg_dest=None, scene_direction=None):
        cap["projects"].append(project)
        cap["stricts"].append(strict_note)
        if poster is not None:
            return poster(project, strict_note=strict_note, raw_bg_dest=raw_bg_dest)
        return b"REROLL_PNG"
    monkeypatch.setattr(br, "_generate_poster", _spy_poster)
    return cap


def test_pure_reroll_rerenders_saved_session(monkeypatch):
    """"generate again" + a saved session ⇒ REROLL: the saved project is re-rendered VERBATIM
    (every locked fact preserved), with no re-extraction and no resend-full-details prompt."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    cap = _install_reroll(monkeypatch)
    status, payload = br.render_grounded(CHAT_ID, _REROLL_TEXT, message_id="rr1", sender_phone=SENDER)
    assert status == br.REROLL
    assert payload == b"REROLL_PNG"
    assert len(cap["projects"]) == 1
    # the saved project's locked facts are preserved EXACTLY (no mutation, no new facts)
    facts = cap["projects"][0].locked_facts
    assert [f.fact_id for f in facts] == ["business_name", "campaign_title", "pricing_structure"]
    assert any(f.value == "2026 Graduation Parties" for f in facts)
    # first attempt carries NO strict note -> the "generate again" text cannot leak into copy
    assert cap["stricts"] == [""]
    assert len(cap["wrote_session"]) == 1   # re-persisted (pending) for the next follow-up


def test_bare_generate_again_reaches_reroll(monkeypatch):
    """A bare "generate again" (no "this flyer" reference, so _looks_like_revision's change-oriented
    patterns don't match it) must STILL route to re-roll, not fall through to the new-flyer path
    (Codex 2026-06-07: re-roll is checked BEFORE the revision gate)."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    cap = _install_reroll(monkeypatch)
    for text in ("generate again", "make another version", "can you make it again", "regenerate"):
        status, _ = br.render_grounded(CHAT_ID, text, message_id="rrb", sender_phone=SENDER)
        assert status == br.REROLL, text
    assert len(cap["projects"]) == 4   # all four re-rendered the saved session


def test_reroll_invite_copy_is_operator_approved():
    assert br.REROLL_INVITE.startswith("I made a fresh version using the same details.")
    assert "just tell me what to adjust" in br.REROLL_INVITE


def test_reroll_no_session_falls_back_to_revision_needed(monkeypatch):
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    cap = _install_reroll(monkeypatch, session=None)
    status, _ = br.render_grounded(CHAT_ID, _REROLL_TEXT, message_id="rr2", sender_phone=SENDER)
    assert status == br.REVISION_NEEDED
    assert cap["projects"] == []   # nothing rendered without a session


def test_reroll_stale_session_falls_back_to_revision_needed(monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    old = (datetime.now(timezone.utc) - timedelta(hours=br._REROLL_MAX_AGE_HOURS + 1)).isoformat()
    cap = _install_reroll(monkeypatch, session=_saved_session(sent_at=old))
    status, _ = br.render_grounded(CHAT_ID, _REROLL_TEXT, message_id="rr3", sender_phone=SENDER)
    assert status == br.REVISION_NEEDED
    assert cap["projects"] == []   # a stale session is not re-rolled


def test_reroll_flag_off_falls_back_to_revision_needed(monkeypatch):
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.setattr(br, "REVISION_APPLY_ENABLED", False)
    cap = _install_reroll(monkeypatch)
    status, _ = br.render_grounded(CHAT_ID, _REROLL_TEXT, message_id="rr4", sender_phone=SENDER)
    assert status == br.REVISION_NEEDED
    assert cap["projects"] == []


def test_reroll_render_failure_failcloses(monkeypatch):
    """A re-render that keeps raising ⇒ FAILCLOSED naming the re-roll stage — never a wrong flyer,
    never the saved flyer silently re-sent."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)

    def _boom(project, *, strict_note="", raw_bg_dest=None):
        raise br._render_mod().FlyerRenderError("boom")
    cap = _install_reroll(monkeypatch, poster=_boom)
    status, payload = br.render_grounded(CHAT_ID, _REROLL_TEXT, message_id="rr5", sender_phone=SENDER)
    assert status == br.FAILCLOSED
    assert "reroll_render_error:FlyerRenderError" in " ".join(payload)


def test_change_request_is_not_a_reroll(monkeypatch):
    """A specific-change request is NOT a pure re-roll: re-rendering the saved facts would drop the
    change, so it must route to REVISION_NEEDED (revision-merge is not built yet) — never re-roll."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    cap = _install_reroll(monkeypatch)
    for text in (
        "please regenerate with a blue background",
        "generate again but change the date to July 4",
        "redo this flyer with no Italian dishes",
        "regenerate and add our phone number",
        # Codex 2026-06-07: a change BETWEEN the verb and "again" must NOT be swallowed into a
        # re-roll (these reach _is_pure_reroll via "this flyer" -> must come back REVISION_NEEDED)
        "generate this flyer in blue again",
        "generate this flyer no Italian again",
        "generate this flyer add phone number again",
        # Codex round-3: a re-roll-signalled change with NO "this flyer"/revision keyword must still
        # route to REVISION_NEEDED via the follow-up gate — NOT fall through to a fresh render.
        "generate again but make it blue",
        "generate again but change color blue",
        "regenerate with delivery added",
    ):
        status, _ = br.render_grounded(CHAT_ID, text, message_id="rrc", sender_phone=SENDER)
        assert status == br.REVISION_NEEDED, text
    assert cap["projects"] == []   # no saved-session re-render for change requests


def test_negated_reroll_is_not_a_reroll(monkeypatch):
    """An explicit negation ("do not generate again", "don't regenerate", "stop generating") must
    NOT re-roll the saved flyer (Codex round-3). Routes to REVISION_NEEDED, never a render."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    cap = _install_reroll(monkeypatch)
    for text in ("do not generate again", "please don't regenerate", "stop generating this flyer",
                 "stop generating", "do not regenerate",
                 # Codex 2026-06-07: design/render verbs negated immediately must still be negations
                 "do not design again", "don't render again", "please do not make another"):
        assert br._is_pure_reroll(text) is False, text
        status, _ = br.render_grounded(CHAT_ID, text, message_id="rrn", sender_phone=SENDER)
        assert status == br.REVISION_NEEDED, text   # handled, never a fresh render
    assert cap["projects"] == []   # never re-rendered on a negation
    # ...but "I don't like this design, generate again" (design = NOUN) stays a pure re-roll
    assert br._is_pure_reroll("I don't like this design, generate again") is True
    assert br._is_pure_reroll("I don't like this design, make another") is True


def test_reroll_with_same_details_phrasing_reaches_reroll(monkeypatch):
    """"generate again with same details" is a pure re-roll (only filler tokens) and must re-roll,
    not fall to the new-flyer path (Codex round-3 reachability)."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    cap = _install_reroll(monkeypatch)
    status, _ = br.render_grounded(CHAT_ID, "generate again with same details",
                                   message_id="rrsd", sender_phone=SENDER)
    assert status == br.REROLL
    assert len(cap["projects"]) == 1


def test_quality_rejection_rerolls_saved_session_without_iteration_flag(monkeypatch):
    """A recent customer saying the flyer quality is unacceptable is a no-change reroll.

    The saved project facts must be preserved; rendering the complaint text as a
    fresh flyer loses the menu and overwrites the session.
    """
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.setattr(br, "ITERATION_ENABLED", False)
    cap = _install_reroll(monkeypatch)

    text = "Flyer quality is very bad, I can't accept this."

    assert br._is_pure_reroll(text) is True
    status, payload = br.render_iteration(CHAT_ID, text, message_id="rrq", sender_phone=SENDER)

    assert status == br.REROLL
    assert payload == b"REROLL_PNG"
    assert len(cap["projects"]) == 1
    assert [fact.fact_id for fact in cap["projects"][0].locked_facts] == [
        "business_name",
        "campaign_title",
        "pricing_structure",
    ]


def test_is_pure_reroll_detector():
    """Unit: the operator's exact phrase + common re-roll phrasings are pure; change requests are not."""
    for t in ("I did not like, please generate this flyer again.",
              "generate again", "regenerate", "redo it", "try again please",
              "Flyer quality is very bad, I can't accept this.",
              "This design is poor and not acceptable.",
              "can you make it again", "do it again", "make another version", "redo"):
        assert br._is_pure_reroll(t) is True, t
    for t in ("change the date to July 4", "no Italian flavour", "add the phone number",
              "make it blue", "regenerate with a blue background", "remove the price",
              "you forgot the address", "use $8.99 for all items",
              # change between the verb and "again" must not be swallowed (Codex 2026-06-07)
              "generate this flyer in blue again", "generate this flyer no Italian again",
              "generate this flyer add phone number again", "make it $8.99 again",
              "redo with delivery", "regenerate but add catering"):
        assert br._is_pure_reroll(t) is False, t


def _load_bare_script():
    """Load the no-extension dispatch script `bare-flyer-render-and-send` as a module."""
    import importlib.machinery
    import importlib.util
    path = _SRC / "agents" / "flyer" / "scripts" / "bare-flyer-render-and-send"
    loader = importlib.machinery.SourceFileLoader("bare_flyer_script", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_script_reroll_sends_invite_caption_and_audits_reroll_sent(monkeypatch):
    """Operator regression (2026-06-07): the script's REROLL branch sends the fresh variant captioned
    with the invite copy, commits the session, and audits OUTCOME=reroll_sent."""
    script = _load_bare_script()
    calls = {"images": [], "logs": [], "committed": []}
    fake_B = types.SimpleNamespace(
        SEND=br.SEND, REROLL=br.REROLL, CONFLICT=br.CONFLICT, FAILCLOSED=br.FAILCLOSED,
        REVISION_NEEDED=br.REVISION_NEEDED, UNREGISTERED=br.UNREGISTERED, REROLL_INVITE=br.REROLL_INVITE,
        render_grounded=lambda *a, **k: (br.REROLL, b"REROLL_PNG"),
        commit_session=lambda chat_id: calls["committed"].append(chat_id),
    )
    monkeypatch.setattr(script, "_bare", lambda: fake_B)
    monkeypatch.setattr(script, "send_image",
                        lambda chat_id, b, caption, action: (calls["images"].append((caption, action)) or True))
    monkeypatch.setattr(script, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(script, "mark_recent_flyer", lambda *a, **k: None)
    monkeypatch.setattr(script, "log", lambda msg: calls["logs"].append(msg))

    rc = script.main(["--chat-id", "201975216009469@lid", "--no-ack",
                      "--brief", "I did not like, please generate this flyer again."])
    assert rc == 0
    assert calls["images"] == [(br.REROLL_INVITE, "flyer.bare.image_send")]   # invite caption on the variant
    assert calls["committed"] == ["201975216009469@lid"]                       # session committed after delivery
    assert any("OUTCOME=reroll_sent" in m for m in calls["logs"])              # audited as a re-roll


# ── skill-driven scene: gate threads scene_direction to the poster (Slice 1, 2026-06-07) ──


def test_skill_driven_scene_flag_off_threads_none(monkeypatch):
    """Flag off ⇒ render_grounded passes scene_direction=None to the poster (today's Python scene)."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.SKILL_DRIVEN_SCENE_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, raising=False)
    rec = _install_common(monkeypatch)
    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="sds1", sender_phone=SENDER)
    assert status == br.SEND and payload == b"LEGACY_PNG"
    assert rec["scene_direction"] == [None]           # no skill scene when the flag is off
    assert rec["cd_render"] == []                      # CD path untouched


def test_skill_driven_scene_armed_threads_visual_direction(monkeypatch):
    """Flag on + allowlisted + skill returns a VisualDirection ⇒ it is threaded to the poster."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    rec = _install_common(monkeypatch)
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ENABLED_ENV, "1")
    monkeypatch.setenv(br.SKILL_DRIVEN_SCENE_ALLOWLIST_ENV, SENDER)
    sentinel = object()
    monkeypatch.setattr(br, "_advisory_scene_direction", lambda *a, **k: sentinel)
    status, payload = br.render_grounded(CHAT_ID, RAW, message_id="sds2", sender_phone=SENDER)
    assert status == br.SEND and payload == b"LEGACY_PNG"
    assert rec["scene_direction"] == [sentinel]        # advisory scene threaded to the integrated poster
    assert rec["cd_render"] == []                      # still NOT the CD path


def test_script_revision_routes_to_iteration_apply_not_stolen(monkeypatch):
    """Slice 3 (operator point 4): the TEXT --revision branch calls render_iteration (not the
    'resend full details' dead-end), and a source-edit --revision-apply still calls
    render_revision_apply — the iteration logic does NOT steal the source-edit path."""
    script = _load_bare_script()
    calls = {"images": [], "logs": [], "iteration": [], "revision_apply": []}
    fake_B = types.SimpleNamespace(
        SEND=br.SEND, REROLL=br.REROLL, CONFLICT=br.CONFLICT, FAILCLOSED=br.FAILCLOSED,
        REVISION_NEEDED=br.REVISION_NEEDED, UNREGISTERED=br.UNREGISTERED, REROLL_INVITE=br.REROLL_INVITE,
        ITERATION_REVISED=br.ITERATION_REVISED, ITERATION_STYLE_REUSE=br.ITERATION_STYLE_REUSE,
        ITERATION_UNCLEAR=br.ITERATION_UNCLEAR, ITERATION_UNCLEAR_REPLY=br.ITERATION_UNCLEAR_REPLY,
        render_iteration=lambda *a, **k: (calls["iteration"].append((a, k)) or (br.ITERATION_REVISED, b"REV_PNG")),
        render_revision_apply=lambda *a, **k: (calls["revision_apply"].append(a) or (br.REVISION_NEEDED, None)),
        commit_session=lambda chat_id: None,
    )
    monkeypatch.setattr(script, "_bare", lambda: fake_B)
    monkeypatch.setattr(script, "send_image",
                        lambda chat_id, b, caption, action: (calls["images"].append(caption) or True))
    monkeypatch.setattr(script, "send_text", lambda *a, **k: True)
    monkeypatch.setattr(script, "mark_recent_flyer", lambda *a, **k: None)
    monkeypatch.setattr(script, "log", lambda msg: calls["logs"].append(msg))

    # text --revision -> render_iteration -> ITERATION_REVISED -> image + audit
    rc = script.main(["--chat-id", CHAT_ID, "--no-ack", "--revision", "--brief", "make it more festive"])
    assert rc == 0
    assert len(calls["iteration"]) == 1 and calls["revision_apply"] == []
    assert calls["images"] == ["Here's your updated flyer - reply with any more changes."]
    assert any("OUTCOME=iteration_revised" in m for m in calls["logs"])

    # source-edit --revision-apply -> render_revision_apply (NOT stolen by iteration)
    calls["iteration"].clear()
    script.main(["--chat-id", CHAT_ID, "--no-ack", "--revision-apply", "--brief", "set price $8.99"])
    assert len(calls["revision_apply"]) == 1 and calls["iteration"] == []
