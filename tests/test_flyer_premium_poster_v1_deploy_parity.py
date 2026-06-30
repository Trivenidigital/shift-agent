"""Premium Poster v1 — deploy-readiness / import-install parity.

The flyer modules deploy FLAT on the VPS (``/opt/shift-agent/flyer_*.py``). The
premium-poster stack must therefore (a) be installed by the deploy script under
its ``flyer_`` flat name, and (b) carry try-flat/except-package import shims so the
imports resolve on the box AND in the package layout (tests). These tests prove
both, plus that the flag stays a no-op when off / not allowlisted.

The flat-layout import is exercised in a SUBPROCESS (clean isolation, no sys.path
pollution). The art-director oracle's flat import (-> flyer_visual_qa -> safe_io ->
fcntl) is Linux-only and is verified by the on-box deploy smoke, not here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")

REPO = Path(__file__).resolve().parent.parent
FLYER = REPO / "src" / "agents" / "flyer"
DEPLOY = REPO / "src" / "agents" / "shift" / "scripts" / "shift-agent-deploy.sh"


# ── deploy script installs the premium modules under their flat names ───────

def test_deploy_script_installs_premium_modules():
    script = DEPLOY.read_text(encoding="utf-8")
    for src_name, flat_name in (
        ("premium_poster_v1.py", "flyer_premium_poster_v1.py"),
        ("premium_poster_v1_director.py", "flyer_premium_poster_v1_director.py"),
        ("flyer_art_director_oracle.py", "flyer_art_director_oracle.py"),
    ):
        line = f"install -m 644 src/agents/flyer/{src_name} /opt/shift-agent/{flat_name}"
        assert line in script, f"deploy script missing install line for {src_name}"


def test_render_adapter_flat_names_are_installed():
    # the flat names render.py imports MUST be the flat names the deploy script installs
    render = (FLYER / "render.py").read_text(encoding="utf-8")
    script = DEPLOY.read_text(encoding="utf-8")
    for flat in ("flyer_visual_qa", "flyer_premium_poster_v1_director", "flyer_art_director_oracle"):
        assert f"from {flat} import" in render, f"render.py should import {flat} (flat arm)"
    # flyer_visual_qa + the 3 premium modules are all installed flat by the deploy script
    for flat_file in ("flyer_visual_qa.py", "flyer_premium_poster_v1_director.py",
                      "flyer_art_director_oracle.py", "flyer_premium_poster_v1.py",
                      "flyer_campaign_scene_prompts.py", "flyer_premium_overlay.py"):
        assert f"/opt/shift-agent/{flat_file}" in script, f"deploy script must install {flat_file}"


# ── source carries the flat-arm shims (so the box imports resolve) ──────────

def test_modules_carry_flat_import_shims():
    director = (FLYER / "premium_poster_v1_director.py").read_text(encoding="utf-8")
    composer = (FLYER / "premium_poster_v1.py").read_text(encoding="utf-8")
    oracle = (FLYER / "flyer_art_director_oracle.py").read_text(encoding="utf-8")
    assert "from flyer_premium_overlay import" in composer            # premium_poster_v1 -> overlay
    assert "from flyer_campaign_scene_prompts import" in director     # director -> scene prompts
    assert "from flyer_premium_poster_v1 import compose_premium_poster_v1" in director  # director -> composer
    assert "from flyer_art_director_oracle import" in director        # director -> oracle
    assert "from flyer_visual_qa import" in oracle                    # oracle -> visual_qa seam


# ── package import still works (the except-arm; render.py falls back to it) ──

def test_package_layout_imports_resolve():
    from agents.flyer.premium_poster_v1 import compose_premium_poster_v1  # noqa: F401
    from agents.flyer.premium_poster_v1_director import compose_best_of_n  # noqa: F401
    from agents.flyer.campaign_scene_prompts import select_food_poster_scene  # noqa: F401


# ── flat (box-style) import resolves in a clean subprocess ──────────────────

def test_flat_layout_imports_resolve(tmp_path):
    # stage the non-fcntl premium chain flat (flyer_*.py), no agents/ package on path
    import shutil
    (tmp_path / "fonts").mkdir()
    for src, flat in (
        ("premium_overlay.py", "flyer_premium_overlay.py"),
        ("campaign_scene_prompts.py", "flyer_campaign_scene_prompts.py"),
        ("premium_poster_v1.py", "flyer_premium_poster_v1.py"),
        ("premium_poster_v1_director.py", "flyer_premium_poster_v1_director.py"),
    ):
        shutil.copy2(FLYER / src, tmp_path / flat)
    for ttf in (FLYER / "fonts").glob("*.ttf"):
        shutil.copy2(ttf, tmp_path / "fonts" / ttf.name)
    code = (
        "import flyer_premium_overlay, flyer_campaign_scene_prompts;"
        "import flyer_premium_poster_v1 as p;"
        "import flyer_premium_poster_v1_director as d;"
        "assert hasattr(p,'compose_premium_poster_v1');"
        "assert hasattr(d,'compose_best_of_n');"
        # exercise the director's LAZY flat import of flyer_premium_poster_v1
        "from types import SimpleNamespace as N;"
        "F=lambda i,v: N(fact_id=i, value=v);"
        "facts=[F('business_name','K'),F('pricing_structure','$9'),F('item:0:name','A'),F('item:1:name','B'),F('item:2:name','C')];"
        "img,rep,cands=d.compose_best_of_n(facts, generator=lambda pr: None, textless_ocr=lambda im: True, critique_scorer=lambda *a: None, n=1);"
        "assert img is not None;"
        "print('FLAT_OK')"
    )
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, f"flat import failed:\n{r.stdout}\n{r.stderr}"
    assert "FLAT_OK" in r.stdout


# ── render.py premium-branch compose import resolves (package arm) ──────────

def test_render_premium_branch_compose_import_resolves():
    # render_premium_poster_v1's compose import is try-flat/except-package; on this
    # machine the package arm resolves. Inject everything else so no model call runs.
    from agents.flyer import render
    proj = SimpleNamespace(customer_phone="+17329837841", locked_facts=[
        SimpleNamespace(fact_id="business_name", value="Lakshmi's Kitchen"),
        SimpleNamespace(fact_id="pricing_structure", value="Any 2 snacks $9.99"),
        SimpleNamespace(fact_id="item:0:name", value="Punugulu"),
        SimpleNamespace(fact_id="item:1:name", value="Egg Bonda"),
        SimpleNamespace(fact_id="item:2:name", value="Aloo Bonda"),
    ])
    fixture = str(REPO / "tests" / "fixtures" / "premium_poster_v1" / "textless_food_scene.png")
    out = render.render_premium_poster_v1(
        proj, tmp := Path(REPO / "tests" / "fixtures" / "premium_poster_v1" / ".deploy_parity_tmp.png"),
        concept_id="C1", output_format="concept_preview", size=(1080, 1350), model="m", quality="low",
        generator=lambda p: fixture, textless_ocr=lambda im: True,
        critique_scorer=lambda *a: {"axes": {"appetite_appeal": {"score": 8, "critique": "x"}}, "composite": 8.0, "overall_critique": "ok"})
    try:
        assert out.delivered is True   # the compose import resolved + the path ran end-to-end
    finally:
        tmp.unlink(missing_ok=True)


# ── flag stays a no-op (off / not-allowlisted) ──────────────────────────────

def test_flag_off_remains_noop():
    from agents.flyer import render
    saved = os.environ.pop("FLYER_PREMIUM_POSTER_V1", None)
    try:
        assert render._premium_poster_v1_armed(SimpleNamespace(customer_phone="+17329837841")) is False
    finally:
        if saved is not None:
            os.environ["FLYER_PREMIUM_POSTER_V1"] = saved


def test_flag_on_not_allowlisted_remains_noop():
    from agents.flyer import render
    saved = {k: os.environ.get(k) for k in ("FLYER_PREMIUM_POSTER_V1", "FLYER_PREMIUM_POSTER_V1_ALLOWLIST")}
    try:
        os.environ["FLYER_PREMIUM_POSTER_V1"] = "1"
        os.environ.pop("FLYER_PREMIUM_POSTER_V1_ALLOWLIST", None)  # empty allowlist => DISABLED
        assert render._premium_poster_v1_armed(SimpleNamespace(customer_phone="+17329837841")) is False
        os.environ["FLYER_PREMIUM_POSTER_V1_ALLOWLIST"] = "+19998887777"  # different number
        assert render._premium_poster_v1_armed(SimpleNamespace(customer_phone="+17329837841")) is False
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
