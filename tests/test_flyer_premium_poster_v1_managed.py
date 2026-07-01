"""Premium Poster v1 — MANAGED/studio (owner-review) path integration.

Extends the bare-path slice (#523) to the managed primary preview render in
generate-flyer-concepts. Two layers:

  A. render.py — the opt-in contextvar generalized to a PATH IDENTITY
     (None/"bare"/"managed") + the _render_model hook fires for either path,
     stays dormant/byte-identical when not armed, and never wraps a recovery rung.
  B. generate-flyer-concepts — the managed emitter helpers that turn the render
     outcome into path-distinguished decisions.log rows, ARMED-gated for dormancy.

Adapters are injected / stubbed so these run deterministically + network-free.
PIL-dependent -> test_flyer_* (excluded from send-path-ci).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "src", ROOT / "src" / "platform", ROOT / "src" / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Windows test hosts lack the Unix-only `fcntl` that safe_io imports at top level;
# these tests monkeypatch _audit_append so the lock path is never exercised.
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ModuleNotFoundError:
        import types as _types

        _fcntl_stub = _types.ModuleType("fcntl")
        _fcntl_stub.LOCK_EX, _fcntl_stub.LOCK_UN, _fcntl_stub.LOCK_NB = 2, 8, 4
        _fcntl_stub.flock = lambda *_a, **_k: None
        sys.modules["fcntl"] = _fcntl_stub

from agents.flyer import render  # noqa: E402
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "premium_poster_v1" / "textless_food_scene.png"
ALLOW = "+17329837841"
SIZE = (1080, 1350)


def _F(i, v, req=False):
    return FlyerLockedFact(fact_id=i, label=i, value=v, source="customer_profile", required=req)


def _food_facts():
    return [
        _F("business_name", "Lakshmi's Kitchen", True),
        _F("campaign_title", "Weekend Snack Specials"),
        _F("pricing_structure", "Any 2 snacks $9.99"),
        _F("item:0:name", "Punugulu"), _F("item:1:name", "Egg Bonda"), _F("item:2:name", "Aloo Bonda"),
        _F("item:3:name", "Veg Lollipop"), _F("item:4:name", "Cut Mirchi"), _F("item:5:name", "Onion Pakora"),
        _F("item:6:name", "Punjabi Samosa"),
        _F("schedule", "Saturday & Sunday"), _F("location", "90 Brybar Dr St Johns FL"), _F("contact_phone", ALLOW),
    ]


def _project(phone=ALLOW, facts=None, raw="Weekend snack specials menu for my restaurant"):
    return FlyerProject(
        project_id="F0001", status="generating_concepts", customer_phone=phone,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        original_message_id="wamid.1", raw_request=raw,
        fields=FlyerRequestFields(
            event_or_business_name="Lakshmi's Kitchen", event_date="2026-07-04",
            event_time="11:00 AM", venue_or_location="90 Brybar Dr St Johns FL",
            contact_info=ALLOW, style_preference="premium",
        ),
        locked_facts=_food_facts() if facts is None else facts,
    )


@pytest.fixture(autouse=True)
def _clean_env():
    keys = ("FLYER_PREMIUM_POSTER_V1", "FLYER_PREMIUM_POSTER_V1_ALLOWLIST",
            "FLYER_PREMIUM_POSTER_V1_N", "FLYER_PREMIUM_POSTER_V1_TIMEOUT_SEC")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v


def _arm():
    os.environ["FLYER_PREMIUM_POSTER_V1"] = "1"
    os.environ["FLYER_PREMIUM_POSTER_V1_ALLOWLIST"] = ALLOW


# ══ Part A — render.py: path-identity contextvar + _render_model hook ═════════

def test_opt_in_path_identity_bare_and_managed():
    assert render._premium_poster_v1_opt_in_path() is None
    with render.premium_poster_v1_bare_path():
        assert render._premium_poster_v1_opt_in_path() == "bare"
    with render.premium_poster_v1_managed_path():
        assert render._premium_poster_v1_opt_in_path() == "managed"
    # leaves no residue when the context exits
    assert render._premium_poster_v1_opt_in_path() is None


def test_managed_hook_not_entered_when_flag_off(tmp_path, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    # managed opt-in ON but flag OFF -> not armed -> branch not entered
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0
    assert (tmp_path / "out.png").exists()   # existing deterministic path produced output


def test_managed_hook_not_entered_when_not_allowlisted(tmp_path, monkeypatch):
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(phone="+19998887777"), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0   # only the allowlisted number arms
    assert (tmp_path / "out.png").exists()


def test_managed_hook_enters_premium_when_armed_and_opted_in(tmp_path, monkeypatch):
    _arm()

    def _stub(project, target, **k):
        Path(target).write_bytes(b"\x89PNG-managed-premium")
        return render.PremiumPosterV1Outcome(True, "delivered", "none", 1, 0, 8.0, k.get("output_format", ""))

    monkeypatch.setattr(render, "render_premium_poster_v1", _stub)
    out = tmp_path / "out.png"
    # non-deterministic model: only the premium branch delivering avoids a network call
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="google/gemini-3.1-flash-image-preview", quality="low")
    assert out.read_bytes() == b"\x89PNG-managed-premium"   # premium delivered, returned early


def test_managed_non_food_branch_not_entered(tmp_path, monkeypatch):
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    proj = _project(raw="tax filing and bookkeeping services", facts=[
        _F("business_name", "Sharma Tax", True), _F("pricing_structure", "Returns from $99"),
        _F("item:0:name", "1040 Filing"), _F("item:1:name", "Bookkeeping"), _F("item:2:name", "Payroll")])
    with render.premium_poster_v1_managed_path():
        render._render_model(proj, tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0   # non-food -> ineligible -> unchanged
    assert (tmp_path / "out.png").exists()


def test_managed_missing_facts_branch_not_entered(tmp_path, monkeypatch):
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    facts = [f for f in _food_facts() if f.fact_id != "pricing_structure"]  # no offer/price
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(facts=facts), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low")
    assert calls["n"] == 0   # missing required facts -> ineligible -> current path
    assert (tmp_path / "out.png").exists()


def test_managed_reference_image_project_never_eligible():
    # Source-edit / reference-image projects are structurally excluded from the
    # premium branch by _premium_poster_v1_eligible (whose items live in an
    # attached image, not locked facts) — so even if a caller opted them in, the
    # branch is not entered. The managed wiring additionally never opts them in.
    _arm()
    proj = _project()
    assert render._premium_poster_v1_eligible(proj) is True   # baseline food project is eligible
    # A project needing reference extraction (source-edit / reference-image) is
    # structurally NOT eligible — the premium branch is never entered for it.
    orig = render._needs_reference_extraction
    try:
        render._needs_reference_extraction = lambda p: True
        assert render._premium_poster_v1_eligible(proj) is False
    finally:
        render._needs_reference_extraction = orig


def test_managed_recovery_force_background_only_skips_premium(tmp_path, monkeypatch):
    _arm()
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    # The deterministic-recovery rung re-renders with force_background_only=True.
    # Even under a managed opt-in the premium branch must be skipped (not a rung).
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), tmp_path / "out.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="deterministic-renderer", quality="low", force_background_only=True)
    assert calls["n"] == 0


def test_managed_falls_through_on_premium_miss(tmp_path, monkeypatch):
    _arm()
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda project, target, **k: render.PremiumPosterV1Outcome(False, "fallback", "no_food_winner", 1, -1, None, ""))
    out = tmp_path / "out.png"
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="deterministic-renderer", quality="low")
    assert out.exists()   # premium missed -> existing managed render produced the output


def test_managed_delivered_poster_text_from_facts(tmp_path):
    # End-to-end (real composer, injected gen/OCR/critique): the delivered poster is
    # produced by compose_premium_poster_v1 — every visible word from locked facts,
    # NO model-rendered text is trusted. Prove the branch delivers the composed poster.
    _arm()
    out = tmp_path / "out.png"

    def _gen_ok(_p):
        return str(FIXTURE)

    orig = render.render_premium_poster_v1

    def _real_ppv1(project, target, **k):
        # Call the ORIGINAL (captured before patching) with injected adapters so the
        # real deterministic composer runs, network-free.
        return orig(
            project, target, concept_id="C1", output_format="concept_preview", size=SIZE,
            model="google/gemini-3.1-flash-image-preview", quality="medium",
            generator=_gen_ok, textless_ocr=lambda im: True,
            critique_scorer=lambda *a: {"axes": {}, "composite": 8.0, "overall_critique": "ok"})

    try:
        render.render_premium_poster_v1 = _real_ppv1
        with render.premium_poster_v1_managed_path():
            render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                                 size=SIZE, model="google/gemini-3.1-flash-image-preview", quality="medium")
    finally:
        render.render_premium_poster_v1 = orig
    from PIL import Image
    with Image.open(out) as im:
        assert im.size == SIZE   # deterministic composed poster (fact-locked text)


def test_bare_path_no_regression(tmp_path, monkeypatch):
    # The bare opt-in must still fire the branch exactly as before the generalization.
    _arm()

    def _stub(project, target, **k):
        Path(target).write_bytes(b"\x89PNG-bare")
        return render.PremiumPosterV1Outcome(True, "delivered", "none", 1, 0, 8.0, k.get("output_format", ""))

    monkeypatch.setattr(render, "render_premium_poster_v1", _stub)
    out = tmp_path / "out.png"
    with render.premium_poster_v1_bare_path():
        render._render_model(_project(), out, concept_id="C1", output_format="concept_preview",
                             size=SIZE, model="google/gemini-3.1-flash-image-preview", quality="low")
    assert out.read_bytes() == b"\x89PNG-bare"


def test_opt_in_does_not_leak_or_go_stale(tmp_path, monkeypatch):
    _arm()

    def _stub(project, target, **k):
        Path(target).write_bytes(b"\x89PNG-premium")
        return render.PremiumPosterV1Outcome(True, "delivered", "none", 1, 0, 8.0, k.get("output_format", ""))

    monkeypatch.setattr(render, "render_premium_poster_v1", _stub)
    # 1st render: managed opt-in -> branch fires + sets the outcome contextvar.
    with render.premium_poster_v1_managed_path():
        render._render_model(_project(), tmp_path / "a.png", concept_id="C1",
                             output_format="concept_preview", size=SIZE,
                             model="google/gemini-3.1-flash-image-preview", quality="low")
    assert render._premium_poster_v1_opt_in_path() is None   # path context released
    first = render.consume_premium_poster_v1_outcome()
    assert first is not None and first.delivered is True
    # 2nd render: NO opt-in (e.g. a later unrelated render) -> branch not entered,
    # and the outcome contextvar is reset to None so no stale value leaks.
    calls = {"n": 0}
    monkeypatch.setattr(render, "render_premium_poster_v1",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    render._render_model(_project(), tmp_path / "b.png", concept_id="C1",
                         output_format="concept_preview", size=SIZE,
                         model="deterministic-renderer", quality="low")
    assert calls["n"] == 0
    assert render.consume_premium_poster_v1_outcome() is None   # reset per render, no leak


# ══ Part B — generate-flyer-concepts: managed observability emitters ══════════

def _load_script():
    path = ROOT / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("genflyer_managed_mod", loader=None, origin=str(path)))
    mod.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


def _proj_ns():
    return SimpleNamespace(project_id="F0001", version=2, customer_phone=ALLOW, locked_facts=[])


def _ppv1_outcome(delivered, status="delivered", reason="none", n=1, wi=0, comp=8.0, of="concept_preview"):
    return SimpleNamespace(delivered=delivered, status=status, reason=reason, n=n,
                           winner_index=wi, winner_composite=comp, output_format=of)


def _events(rows):
    return [r.event for r in rows]


def test_emit_managed_dormant_when_not_armed(monkeypatch, tmp_path):
    mod = _load_script()
    rows, consumed = [], {"n": 0}

    def _consume():
        consumed["n"] += 1
        return _ppv1_outcome(True)   # even if an outcome exists, dormant path emits nothing

    monkeypatch.setattr(mod, "_premium_poster_v1_armed", lambda p: False)
    monkeypatch.setattr(mod, "consume_premium_poster_v1_outcome", _consume)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    out = mod._emit_premium_poster_v1_managed_outcome(tmp_path / "decisions.log", _proj_ns())
    assert rows == []                 # ZERO rows -> managed path byte-identical + audit-silent
    assert out is None
    assert consumed["n"] == 1         # still consumes so no stale value leaks


def test_emit_managed_delivered_sequence(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    monkeypatch.setattr(mod, "_premium_poster_v1_armed", lambda p: True)
    monkeypatch.setattr(mod, "consume_premium_poster_v1_outcome", lambda: _ppv1_outcome(True))
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    out = mod._emit_premium_poster_v1_managed_outcome(tmp_path / "decisions.log", _proj_ns())
    assert _events(rows) == [
        "premium_poster_v1_managed_attempted",
        "premium_poster_v1_managed_eligible",
        "premium_poster_v1_managed_selected",
    ]
    assert all(r.type == "flyer_premium_poster_v1_managed" for r in rows)
    assert rows[-1].winner_index == 0 and rows[-1].winner_composite == 8.0
    assert out is not None and out.delivered is True


def test_emit_managed_fallthrough_sequence(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    monkeypatch.setattr(mod, "_premium_poster_v1_armed", lambda p: True)
    monkeypatch.setattr(mod, "consume_premium_poster_v1_outcome",
                        lambda: _ppv1_outcome(False, status="fallback", reason="no_food_winner"))
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    mod._emit_premium_poster_v1_managed_outcome(tmp_path / "decisions.log", _proj_ns())
    assert _events(rows) == [
        "premium_poster_v1_managed_attempted",
        "premium_poster_v1_managed_eligible",
        "premium_poster_v1_managed_fallback_reason",
    ]
    assert rows[-1].reason == "no_food_winner"


def test_emit_managed_armed_but_ineligible(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    monkeypatch.setattr(mod, "_premium_poster_v1_armed", lambda p: True)
    monkeypatch.setattr(mod, "consume_premium_poster_v1_outcome", lambda: None)  # branch never fired
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    out = mod._emit_premium_poster_v1_managed_outcome(tmp_path / "decisions.log", _proj_ns())
    assert _events(rows) == [
        "premium_poster_v1_managed_attempted",
        "premium_poster_v1_managed_fallback_reason",
    ]
    assert rows[-1].reason == "ineligible"
    assert out is None


def test_emit_managed_final_pass(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    mod._emit_premium_poster_v1_managed_final(tmp_path / "decisions.log", _proj_ns(),
                                              _ppv1_outcome(True), qa_passed=True)
    assert _events(rows) == ["premium_poster_v1_managed_final_pass"]
    assert rows[0].qa_status == "passed"


def test_emit_managed_final_fail(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    mod._emit_premium_poster_v1_managed_final(tmp_path / "decisions.log", _proj_ns(),
                                              _ppv1_outcome(True), qa_passed=False)
    assert _events(rows) == ["premium_poster_v1_managed_final_fail"]
    assert rows[0].qa_status == "failed"


def test_emit_managed_final_noop_when_not_delivered(monkeypatch, tmp_path):
    mod = _load_script()
    rows = []
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    # not delivered -> no final row (the existing ladder owns recovery)
    mod._emit_premium_poster_v1_managed_final(tmp_path / "decisions.log", _proj_ns(),
                                              _ppv1_outcome(False, status="fallback"), qa_passed=False)
    # None outcome (source-edit / ineligible) -> no final row either
    mod._emit_premium_poster_v1_managed_final(tmp_path / "decisions.log", _proj_ns(), None, qa_passed=True)
    assert rows == []


def test_emit_managed_outcome_never_raises_on_audit_failure(monkeypatch, tmp_path):
    # Fail-safe: the emitter runs INSIDE the render retry try/except — an audit
    # failure must NOT propagate (else it is misclassified as a render failure and
    # downgrades a good render to manual). It must swallow, clear the outcome, and
    # return None. Simulate a construction/append blow-up.
    mod = _load_script()
    consumed = {"n": 0}

    def _consume():
        consumed["n"] += 1
        return _ppv1_outcome(True)

    monkeypatch.setattr(mod, "_premium_poster_v1_armed", lambda p: True)
    monkeypatch.setattr(mod, "consume_premium_poster_v1_outcome", _consume)
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: (_ for _ in ()).throw(RuntimeError("boom")))
    out = mod._emit_premium_poster_v1_managed_outcome(tmp_path / "decisions.log", _proj_ns())
    assert out is None                 # swallowed -> caller treats as no premium delivery
    assert consumed["n"] >= 1          # outcome still consumed (best-effort clear, no leak)


def test_emit_managed_final_never_raises_on_audit_failure(monkeypatch, tmp_path):
    # The final emitter runs OUTSIDE any render try — a raise here would hit main().
    mod = _load_script()
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: (_ for _ in ()).throw(RuntimeError("boom")))
    # must not raise
    mod._emit_premium_poster_v1_managed_final(tmp_path / "decisions.log", _proj_ns(),
                                              _ppv1_outcome(True), qa_passed=True)


def test_emit_managed_only_audit_side_effect(monkeypatch, tmp_path):
    # Lifecycle safety: the emitter's ONLY side effect is _audit_append — it never
    # touches project state, owner-review, approval, or send. Alerting is NOT wired
    # for this log-only signal.
    mod = _load_script()
    rows, alerts = [], []
    monkeypatch.setattr(mod, "_premium_poster_v1_armed", lambda p: True)
    monkeypatch.setattr(mod, "consume_premium_poster_v1_outcome", lambda: _ppv1_outcome(True))
    monkeypatch.setattr(mod, "_audit_append", lambda path, entry: rows.append(entry))
    monkeypatch.setattr(mod, "_alert_owner", lambda msg: alerts.append(msg))
    mod._emit_premium_poster_v1_managed_outcome(tmp_path / "decisions.log", _proj_ns())
    assert len(rows) == 3 and alerts == []   # audit-only, no operator alert
