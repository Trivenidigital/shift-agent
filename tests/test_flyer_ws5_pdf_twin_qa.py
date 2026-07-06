"""WS5 — PDF rasterize-before-QA via the generation-time PNG twin.

Gap (PR #566 design note): the vision-QA provider rejects ``application/pdf``
sent as an image, so printable_pdf finals were never content-screened — QA came
back provider_unavailable and the format was silently dropped (or, pre-QA-loop,
delivered unscreened — the F0203 class). Fix: every PDF-write site also saves a
PNG twin from the SAME raster; finalize screens the twin and projects the
verdict onto the PDF (re-bound to the PDF's sha so the send gate can bind it),
stamping the delivery row with ``pdf_qa_status``.
"""
from __future__ import annotations

import importlib.machinery
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from schemas import (  # noqa: E402
    FlyerAsset,
    FlyerConcept,
    FlyerLockedFact,
    FlyerProject,
    FlyerProjectStore,
    FlyerRequestFields,
    FlyerVisualQAReport,
)

FINALIZE_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "finalize-flyer-assets"


def _F(fid, value, req=True):
    return FlyerLockedFact(fact_id=fid, label=fid, value=value,
                           source="customer_text", required=req)


def _load_finalize_script():
    if "safe_io" not in sys.modules:
        safe_io = ModuleType("safe_io")

        class FileLock:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        safe_io.FileLock = FileLock  # type: ignore[attr-defined]
        safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")  # type: ignore[attr-defined]
        safe_io.load_yaml_model = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
        # Full parity with test_flyer_delivery_retry's stub: whichever module
        # installs safe_io first serves every script loaded later in the same
        # session (send-flyer-package needs the bridge callables).
        safe_io.bridge_post = lambda *_args, **_kwargs: (True, "dry-run:text", "", 200)  # type: ignore[attr-defined]
        safe_io.bridge_send_media = lambda *_args, **_kwargs: (True, "dry-run:media", "", 200)  # type: ignore[attr-defined]
        safe_io.ndjson_append = lambda path, line: Path(path).open("a", encoding="utf-8").write(line + "\n")  # type: ignore[attr-defined]
        sys.modules["safe_io"] = safe_io
    loader = importlib.machinery.SourceFileLoader("finalize_flyer_assets_ws5_script", str(FINALIZE_SCRIPT_PATH))
    return loader.load_module()


# ---------------------------------------------------------------------------
# Render-side: every PDF write leaves a PNG twin of the same raster.
# ---------------------------------------------------------------------------

def test_pdf_png_twin_path_is_a_sidecar():
    from agents.flyer.render import pdf_png_twin_path

    assert pdf_png_twin_path("/x/F1-printable_pdf.pdf") == Path("/x/F1-printable_pdf.pdf.qapng.png")


def test_export_from_source_image_pdf_writes_twin(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from agents.flyer.render import _export_from_source_image, pdf_png_twin_path

    source = tmp_path / "source.png"
    Image.new("RGB", (400, 500), (200, 40, 40)).save(source)
    pdf = tmp_path / "out.pdf"
    _export_from_source_image(source, pdf, size=None)

    twin = pdf_png_twin_path(pdf)
    assert pdf.exists() and twin.exists()
    with Image.open(twin) as im:
        # The twin IS the PDF's raster: same (unresized) dimensions, same content.
        assert im.size == (400, 500)
        assert im.getpixel((10, 10)) == (200, 40, 40)


def test_write_generated_image_pdf_writes_twin(tmp_path):
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    from agents.flyer.render import _write_generated_image, pdf_png_twin_path

    buf = io.BytesIO()
    Image.new("RGB", (300, 380), (30, 160, 90)).save(buf, format="PNG")
    pdf = tmp_path / "gen.pdf"
    _write_generated_image(buf.getvalue(), pdf, size=None)

    twin = pdf_png_twin_path(pdf)
    assert twin.exists()
    with Image.open(twin) as im:
        assert im.size == (300, 380)
        assert im.getpixel((5, 5)) == (30, 160, 90)


def test_render_final_package_direct_path_writes_pdf_twin(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    from agents.flyer.render import pdf_png_twin_path, render_final_package

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    now = datetime.now(timezone.utc)
    preview = tmp_path / "F9501-C1-preview.png"
    img = Image.new("RGB", (1080, 1350), (245, 240, 230))
    d = ImageDraw.Draw(img)
    for y in range(0, 1350, 30):  # texture for the low-variance gate
        shade = 60 + (y * 140 // 1350)
        d.rectangle([120, y + 6, 960, y + 18], fill=(shade, shade // 2, 30))
    img.save(preview)

    project = FlyerProject(
        project_id="F9501", status="finalizing_assets", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-ws5",
        raw_request="Create a flyer for the weekend special.",
        fields=FlyerRequestFields(),
        locked_facts=[_F("business_name", "Lakshmi's Kitchen"),
                      _F("contact_phone", "+17329837841")],
        assets=[FlyerAsset(asset_id="A0001", kind="concept_preview", source="rendered",
                           path=str(preview), mime_type="image/png", sha256="a" * 64,
                           original_message_id="m-ws5", received_at=now)],
        concepts=[FlyerConcept(concept_id="C1", title="Best Design",
                               style_summary="v2 integrated render", preview_asset_id="A0001",
                               prompt="", created_at=now)],
        selected_concept_id="C1",
    )

    specs = render_final_package(project, tmp_path / "finals")
    pdf_spec = next(s for s in specs if s.output_format == "printable_pdf")
    twin = pdf_png_twin_path(pdf_spec.path)
    assert twin.exists(), "PDF render must leave its PNG twin sidecar"
    with Image.open(twin) as im:
        # Direct path exports the approved preview unresized into the PDF, so
        # the twin carries the preview's dimensions and content.
        assert im.size == (1080, 1350)


# ---------------------------------------------------------------------------
# QA-side: the twin's verdict is projected onto the PDF for the send gate.
# ---------------------------------------------------------------------------

def _twin_report(twin: Path, *, status: str = "passed") -> FlyerVisualQAReport:
    from agents.flyer.visual_qa import sha256_file

    return FlyerVisualQAReport(
        project_id="F9502",
        asset_id="A0010",
        artifact_path=str(twin),
        artifact_sha256=sha256_file(twin),
        project_version=1,
        output_format="printable_pdf",
        provider="test",
        qa_source="ocr_vision",
        status=status,  # type: ignore[arg-type]
        checked_at=datetime.now(timezone.utc),
    )


def test_derive_pdf_twin_report_rebinds_sha_and_satisfies_send_gate(tmp_path):
    from agents.flyer.visual_qa import (
        derive_pdf_twin_report,
        sha256_file,
        validate_visual_qa_report,
        write_visual_qa_report,
    )

    pdf = tmp_path / "F9502-printable_pdf.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    twin = tmp_path / "F9502-printable_pdf.pdf.qapng.png"
    twin.write_bytes(b"png-bytes")

    derived = derive_pdf_twin_report(_twin_report(twin), pdf)
    assert derived.artifact_path == str(pdf)
    assert derived.artifact_sha256 == sha256_file(pdf)
    assert any("PNG twin" in w for w in derived.warnings)
    # Verdict fields are untouched.
    assert derived.status == "passed" and derived.blockers == []

    write_visual_qa_report(derived, pdf)
    result = validate_visual_qa_report(
        pdf, project_id="F9502", project_version=1, output_format="printable_pdf",
    )
    assert result.ok, result.blockers


def test_finalize_routes_pdf_qa_to_twin_and_stamps_pdf_qa_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_finalize_script()
    from agents.flyer.render import RenderedAssetSpec

    now = datetime.now(timezone.utc)
    project = FlyerProject(
        project_id="F9503", status="finalizing_assets", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-ws5-fin",
        raw_request="Need flyer",
        fields=FlyerRequestFields(event_or_business_name="Daily Lunch Specials"),
    )
    state_path = tmp_path / "projects.json"
    final_dir = tmp_path / "finals"
    config_path = tmp_path / "config.yaml"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")
    config_path.write_text("flyer: {}\n", encoding="utf-8")

    def fake_render_final_package(_project, output_dir, *, model, quality):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        specs = []
        for output_format, kind, name, size in [
            ("whatsapp_image", "final_whatsapp_image", "wa.png", (1080, 1350)),
            ("instagram_post", "final_instagram_post", "post.png", (1080, 1080)),
            ("instagram_story", "final_instagram_story", "story.png", (1080, 1920)),
            ("printable_pdf", "final_printable_pdf", "print.pdf", (1275, 1650)),
        ]:
            path = output_dir / name
            path.write_bytes(b"asset")
            if output_format == "printable_pdf":
                # WS5: the PDF render leaves its PNG twin sidecar.
                Path(str(path) + ".qapng.png").write_bytes(b"twin-raster")
            specs.append(RenderedAssetSpec(path=path, kind=kind, output_format=output_format, width=size[0], height=size[1]))
        return specs

    qa_targets: dict[str, str] = {}

    def fake_run_visual_qa(project, artifact_path, *, output_format, asset_id):
        qa_targets[output_format] = str(artifact_path)
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="a" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="passed",
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(mod, "load_yaml_model", lambda *_args, **_kwargs: SimpleNamespace(
        flyer=SimpleNamespace(resolve_final_render_provider=lambda: SimpleNamespace(model="deterministic-renderer", quality="medium"))
    ))
    monkeypatch.setattr(mod, "render_final_package", fake_render_final_package)
    monkeypatch.setattr(mod, "run_visual_qa", fake_run_visual_qa)
    monkeypatch.setattr(mod, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "finalize-flyer-assets",
        "--project-id", project.project_id,
        "--approved-message-id", "approve-ws5",
        "--state-path", str(state_path),
        "--final-dir", str(final_dir),
        "--config-path", str(config_path),
    ])

    assert mod.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["final_asset_count"] == 4

    # The QA screen ran against the twin, not the PDF.
    assert qa_targets["printable_pdf"].endswith("print.pdf.qapng.png")

    persisted = FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8")).projects[0]
    pdf_asset = next(a for a in persisted.assets if a.kind == "final_printable_pdf")
    assert pdf_asset.pdf_qa_status == "passed"
    assert pdf_asset.asset_id in persisted.final_asset_ids
    # The stored report is the PDF-facing projection: bound to the PDF's path
    # and its real sha (send-gate contract), provenance warning attached.
    pdf_report = next(r for r in persisted.qa_reports if r.output_format == "printable_pdf")
    assert pdf_report.artifact_path.endswith("print.pdf")
    import hashlib
    assert pdf_report.artifact_sha256 == hashlib.sha256(b"asset").hexdigest()
    assert any("PNG twin" in w for w in pdf_report.warnings)


def test_finalize_without_twin_keeps_legacy_dropped_pdf_and_marks_twin_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_finalize_script()
    from agents.flyer.render import RenderedAssetSpec

    now = datetime.now(timezone.utc)
    project = FlyerProject(
        project_id="F9504", status="finalizing_assets", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-ws5-legacy",
        raw_request="Need flyer",
        fields=FlyerRequestFields(event_or_business_name="Daily Lunch Specials"),
    )
    state_path = tmp_path / "projects.json"
    config_path = tmp_path / "config.yaml"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")
    config_path.write_text("flyer: {}\n", encoding="utf-8")

    def fake_render_final_package(_project, output_dir, *, model, quality):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        specs = []
        for output_format, kind, name, size in [
            ("whatsapp_image", "final_whatsapp_image", "wa.png", (1080, 1350)),
            ("printable_pdf", "final_printable_pdf", "print.pdf", (1275, 1650)),
        ]:
            path = output_dir / name
            path.write_bytes(b"asset")
            specs.append(RenderedAssetSpec(path=path, kind=kind, output_format=output_format, width=size[0], height=size[1]))
        return specs

    def fake_run_visual_qa(project, artifact_path, *, output_format, asset_id):
        # Legacy PDF-direct QA: the provider rejects application/pdf.
        pdf_direct = str(artifact_path).endswith(".pdf")
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="a" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="unavailable" if pdf_direct else "test",
            qa_source="ocr_vision",
            status="provider_unavailable" if pdf_direct else "passed",
            blockers=["unsupported OCR media type: application/pdf"] if pdf_direct else [],
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(mod, "load_yaml_model", lambda *_args, **_kwargs: SimpleNamespace(
        flyer=SimpleNamespace(resolve_final_render_provider=lambda: SimpleNamespace(model="deterministic-renderer", quality="medium"))
    ))
    monkeypatch.setattr(mod, "render_final_package", fake_render_final_package)
    monkeypatch.setattr(mod, "run_visual_qa", fake_run_visual_qa)
    monkeypatch.setattr(mod, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "finalize-flyer-assets",
        "--project-id", project.project_id,
        "--approved-message-id", "approve-legacy",
        "--state-path", str(state_path),
        "--final-dir", str(tmp_path / "finals"),
        "--config-path", str(config_path),
    ])

    assert mod.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["final_asset_count"] == 1
    assert out["skipped_optional_final_asset_count"] == 1

    persisted = FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8")).projects[0]
    pdf_asset = next(a for a in persisted.assets if a.kind == "final_printable_pdf")
    assert pdf_asset.pdf_qa_status == "twin_missing"
    assert pdf_asset.asset_id not in persisted.final_asset_ids


# ---------------------------------------------------------------------------
# tools/backfill-flyer-pdf-qa.py — F0203-class retroactive QA (Linux-only:
# the tool takes real safe_io file locks).
# ---------------------------------------------------------------------------

BACKFILL_TOOL = Path(__file__).resolve().parent.parent / "tools" / "backfill-flyer-pdf-qa.py"


def _backfill_store(tmp_path: Path, *, with_preview: bool) -> Path:
    now = datetime.now(timezone.utc)
    assets = []
    concepts = []
    selected = None
    if with_preview:
        preview = tmp_path / "F9505-C1-preview.png"
        preview.write_bytes(b"preview-raster-bytes")
        assets.append(FlyerAsset(asset_id="A0001", kind="concept_preview", source="rendered",
                                 path=str(preview), mime_type="image/png", sha256="a" * 64,
                                 original_message_id="m-bf", received_at=now))
        concepts.append(FlyerConcept(concept_id="C1", title="Best", style_summary="s",
                                     preview_asset_id="A0001", prompt="", created_at=now))
        selected = "C1"
    pdf = tmp_path / "F9505-printable_pdf.pdf"
    pdf.write_bytes(b"%PDF-1.4 delivered")
    assets.append(FlyerAsset(asset_id="A0002", kind="final_printable_pdf", source="rendered",
                             path=str(pdf), mime_type="application/pdf", sha256="b" * 64,
                             original_message_id="m-bf", received_at=now,
                             delivery_status="sent", outbound_message_id="wamid-1",
                             delivered_at=now))
    project = FlyerProject(
        project_id="F9505", status="delivered", customer_phone="+17329837841",
        created_at=now, updated_at=now, original_message_id="m-bf",
        raw_request="Create flyer. Headline: Premium Clean Chicken.",
        fields=FlyerRequestFields(),
        locked_facts=[
            _F("business_name", "Fresh Meats"),
            _F("headline", "Premium Clean Chicken"),
            _F("tagline", "Clean bird. Strong life."),
            _F("item:0:price", "$13.99"),
        ],
        assets=assets, concepts=concepts, selected_concept_id=selected,
    )
    state_path = tmp_path / "projects.json"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")
    return state_path


def _run_backfill(tmp_path: Path, state_path: Path, work_dir: Path) -> tuple[int, dict, list[dict]]:
    import os
    import subprocess

    log_path = tmp_path / "decisions.log"
    env = dict(os.environ)
    env["FLYER_STATE_ROOT"] = str(tmp_path)
    env["FLYER_QA_ALLOW_SIDECAR"] = "1"
    proc = subprocess.run(
        [sys.executable, str(BACKFILL_TOOL),
         "--project-id", "F9505",
         "--state-path", str(state_path),
         "--log-path", str(log_path),
         "--work-dir", str(work_dir)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    rows = []
    if log_path.exists():
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    return proc.returncode, out, rows


# NOTE: platform gate, not importorskip("fcntl") — other test modules stub
# fcntl into sys.modules, which would un-skip these on Windows while the
# SUBPROCESS (a clean interpreter) still fails on the real fcntl import.
@pytest.mark.skipif(sys.platform == "win32", reason="tool takes real safe_io fcntl locks")
def test_backfill_tool_qas_preview_standin_and_writes_only_the_audit_row(tmp_path):
    state_path = _backfill_store(tmp_path, with_preview=True)
    store_bytes_before = state_path.read_bytes()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    # Sidecar OCR for the twin copy the tool will create in work_dir.
    (work_dir / "F9505-printable_pdf.pdf.qapng.png.ocr.txt").write_text(
        "Fresh Meats Premium Clean Chicken Clean bird. Strong life. $13.99",
        encoding="utf-8",
    )

    code, out, rows = _run_backfill(tmp_path, state_path, work_dir)

    assert code == 0, out
    assert out["qa_status"] == "passed", out
    assert out["raster_source"] == "selected_preview"
    row = next(r for r in rows if r["type"] == "flyer_pdf_qa_backfill")
    assert row["qa_status"] == "passed"
    assert row["raster_exactness"] == "upstream_equivalent"
    assert row["asset_id"] == "A0002"
    # Read-only contract: the live store is byte-identical; the QA report went
    # to the work dir, not next to the delivered PDF.
    assert state_path.read_bytes() == store_bytes_before
    assert (work_dir / "F9505-printable_pdf.pdf.backfill-qa.json").exists()
    assert not (tmp_path / "F9505-printable_pdf.pdf.qa.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="tool takes real safe_io fcntl locks")
def test_backfill_tool_records_raster_missing_when_nothing_on_disk(tmp_path):
    state_path = _backfill_store(tmp_path, with_preview=False)
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    code, out, rows = _run_backfill(tmp_path, state_path, work_dir)

    assert code == 0, out
    assert out["qa_status"] == "raster_missing"
    row = next(r for r in rows if r["type"] == "flyer_pdf_qa_backfill")
    assert row["raster_source"] == "none"
