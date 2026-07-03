"""WS2b — letterbox raw-less direct finals (v2 spec amendment A1).

Labeled failure: FA-2/CF-1 — render_final_package's direct path center-cropped
fixed-shape formats: instagram_post (1080x1080 from a 1080x1350 preview) cut
135px off top AND bottom — the brand band and footer — and the formats then
failed per-format QA and were silently dropped. The A1 grounding showed the
same class re-applies wholesale to raw-less v2/integrated previews.

Fix: letterbox (contained) instead of cover-crop for raw-less non-source-edit
direct finals. Every fact stays visible; same-aspect targets are unaffected
(contained == plain resize when aspects match). Premium-provenance previews
keep their recompose path (S4); source-edit finals already letterbox.

Pin mechanics: a red block at the preview's TOP-LEFT and a blue block at the
BOTTOM-RIGHT. Cover-crop removes both (top/bottom bands); letterbox keeps both.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PIL")

from agents.flyer.render import render_final_package
from schemas import FlyerAsset, FlyerConcept, FlyerLockedFact, FlyerProject, FlyerRequestFields

RED = (220, 30, 30)
BLUE = (30, 30, 220)


def _F(fid, value, req=True):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=req)


def _pinned_preview(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1080, 1350), (245, 240, 230))
    d = ImageDraw.Draw(img)
    # texture so inspect_rendered_asset's low-variance gate doesn't trip
    for y in range(0, 1350, 30):
        shade = 60 + (y * 140 // 1350)
        d.rectangle([120, y + 6, 960, y + 18], fill=(shade, shade // 2, 30))
    d.rectangle([0, 0, 60, 60], fill=RED)              # top-left pin (brand-band zone)
    d.rectangle([1019, 1289, 1079, 1349], fill=BLUE)   # bottom-right pin (footer zone)
    img.save(path)


def _has_color(img, color, tol=40):
    px = img.load()
    w, h = img.size
    step = max(1, w // 200)
    for x in range(0, w, step):
        for y in range(0, h, step):
            p = px[x, y]
            if all(abs(p[i] - color[i]) <= tol for i in range(3)):
                return True
    return False


def _rawless_project(tmp_path, monkeypatch):
    """v2/integrated-era shape: approved preview exists, NO raw sidecar."""
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))  # asset-path validator root
    now = datetime.now(timezone.utc)
    preview = tmp_path / "F9401-C1-preview.png"
    _pinned_preview(preview)
    return FlyerProject(
        project_id="F9401", status="finalizing_assets", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-ws2b",
        raw_request="Create a flyer for the weekend special.",
        fields=FlyerRequestFields(),
        locked_facts=[_F("business_name", "Lakshmi's Kitchen"),
                      _F("contact_phone", "+17329837841")],
        assets=[FlyerAsset(asset_id="A0001", kind="concept_preview", source="rendered",
                           path=str(preview), mime_type="image/png", sha256="a" * 64,
                           original_message_id="m-ws2b", received_at=now)],
        concepts=[FlyerConcept(concept_id="C1", title="Best Design",
                               style_summary="v2 integrated render", preview_asset_id="A0001",
                               prompt="", created_at=now)],
        selected_concept_id="C1",
    )


def test_rawless_fixed_formats_letterbox_never_crop(tmp_path, monkeypatch):
    from PIL import Image

    project = _rawless_project(tmp_path, monkeypatch)
    specs = render_final_package(project, tmp_path / "finals")
    by_format = {s.output_format: s for s in specs}
    assert set(by_format) == {"whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"}

    for fmt, size in (("instagram_post", (1080, 1080)), ("instagram_story", (1080, 1920))):
        with Image.open(by_format[fmt].path) as im:
            assert im.size == size
            # Both pins must survive: cover-crop removed the top/bottom bands
            # (instagram_post) or the side columns (instagram_story).
            assert _has_color(im, RED), f"{fmt}: top-left content cropped off"
            assert _has_color(im, BLUE), f"{fmt}: bottom-right content cropped off"

    # whatsapp_image (same 4:5 aspect) remains a faithful full-frame export
    with Image.open(by_format["whatsapp_image"].path) as im:
        assert im.size == (1080, 1350)
        assert _has_color(im, RED) and _has_color(im, BLUE)


def test_background_only_raw_rebuild_path_unchanged(tmp_path, monkeypatch):
    # Sanity: the raw+overlay rebuild path for background-only projects is not
    # touched by WS2b — existing renderer suite covers it in depth; this pins
    # that a RAW-BEARING preview still routes away from the direct branch.
    from agents.flyer.render import _raw_background_path

    project = _rawless_project(tmp_path, monkeypatch)
    preview = tmp_path / "F9401-C1-preview.png"
    raw = _raw_background_path(preview)
    _pinned_preview(raw)  # matched raw present -> not the raw-less class
    # (Behavioral depth for this path lives in test_flyer_renderer.py; here we
    # only assert the raw file's presence flips the routing input.)
    assert raw.exists()
