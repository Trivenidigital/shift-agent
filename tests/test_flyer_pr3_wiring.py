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

    def _fake_legacy_poster(project, *, strict_note="", raw_bg_dest=None):
        rec["legacy_poster"].append(strict_note)
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

    def _boom(project, *, strict_note="", raw_bg_dest=None):
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


def test_render_error_detail_never_raises_into_render_path(monkeypatch):
    """Diagnostics must NEVER raise into the render path — even a render exception with a
    pathological __str__ still fails closed, not uncaught (Codex 2026-06-06)."""
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ENABLED_ENV, raising=False)
    monkeypatch.delenv(br.CREATIVE_DIRECTOR_ALLOWLIST_ENV, raising=False)
    _install_common(monkeypatch)

    class _BadExc(Exception):
        def __str__(self):  # noqa: D401
            raise RuntimeError("boom in __str__")

    def _boom(project, *, strict_note="", raw_bg_dest=None):
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
