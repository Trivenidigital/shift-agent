"""Retry-safety contracts for Flyer final package delivery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib.machinery
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "platform"))

from schemas import FlyerAsset, FlyerCustomerStore, FlyerProject, FlyerProjectStore, FlyerRequestFields, FlyerUsageEvent, FlyerVisualQAReport  # noqa: E402


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "send-flyer-package"
REPORT_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "flyer-delivery-report"
FINALIZE_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "src" / "agents" / "flyer" / "scripts" / "finalize-flyer-assets"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("send_flyer_package_script", str(SCRIPT_PATH))
    return loader.load_module()


def _load_report_script():
    loader = importlib.machinery.SourceFileLoader("flyer_delivery_report_script", str(REPORT_SCRIPT_PATH))
    return loader.load_module()


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
        safe_io.bridge_post = lambda *_args, **_kwargs: (True, "dry-run:text", "", 200)  # type: ignore[attr-defined]
        safe_io.bridge_send_media = lambda *_args, **_kwargs: (True, "dry-run:media", "", 200)  # type: ignore[attr-defined]
        safe_io.ndjson_append = lambda path, line: Path(path).open("a", encoding="utf-8").write(line + "\n")  # type: ignore[attr-defined]
        sys.modules["safe_io"] = safe_io
    loader = importlib.machinery.SourceFileLoader("finalize_flyer_assets_script", str(FINALIZE_SCRIPT_PATH))
    return loader.load_module()


def _asset(asset_id: str, kind: str, path: str, *, status: str = "pending", mid: str = "") -> FlyerAsset:
    return FlyerAsset(
        asset_id=asset_id,
        kind=kind,  # type: ignore[arg-type]
        source="rendered",
        path=path,
        mime_type="application/pdf" if path.endswith(".pdf") else "image/png",
        sha256="a" * 64,
        original_message_id="approve-1",
        received_at=datetime.now(timezone.utc),
        delivery_status=status,  # type: ignore[arg-type]
        outbound_message_id=mid,
        delivered_at=datetime.now(timezone.utc) if status == "sent" else None,
    )


def _png_header_bytes(width: int, height: int) -> bytes:
    """Minimal valid PNG header (8-byte signature + IHDR chunk) carrying the
    given width/height. Enough for the Pillow-free dimension reader; no pixel
    data is needed because the reader only consumes the IHDR header."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big") + b"IHDR"
        + int(width).to_bytes(4, "big") + int(height).to_bytes(4, "big")
    )


def _project(tmp_path: Path) -> FlyerProject:
    kinds = [
        ("A0001", "final_whatsapp_image", "wa.png"),
        ("A0002", "final_instagram_post", "post.png"),
        ("A0003", "final_instagram_story", "story.png"),
        ("A0004", "final_printable_pdf", "print.pdf"),
    ]
    assets = []
    for asset_id, kind, name in kinds:
        path = tmp_path / name
        path.write_bytes(b"asset")
        assets.append(_asset(asset_id, kind, str(path)))
    return FlyerProject(
        project_id="F0001",
        status="finalizing_assets",
        customer_phone="+19045550123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        original_message_id="request-1",
        raw_request="Need flyer",
        fields=FlyerRequestFields(
            event_or_business_name="Daily Lunch Specials",
            contact_info="+1 704 324 3322",
            notes="Item 1 $1.99",
        ),
        assets=assets,
        final_asset_ids=[asset.asset_id for asset in assets],
    )


def _write_visual_qa(
    asset_path: Path,
    *,
    project_id: str = "F0001",
    project_version: int = 1,
    output_format: str = "whatsapp_image",
    qa_source: str = "ocr_vision",
) -> None:
    from agents.flyer.visual_qa import write_visual_qa_report

    report = FlyerVisualQAReport(
        project_id=project_id,
        asset_id="A0001",
        artifact_path=str(asset_path),
        artifact_sha256="a" * 64,
        project_version=project_version,
        output_format=output_format,
        provider="test",
        qa_source=qa_source,
        status="passed",
        checked_at=datetime.now(timezone.utc),
    )
    write_visual_qa_report(report, asset_path)


def test_flyer_asset_delivery_fields_are_backward_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    asset = FlyerAsset(
        asset_id="A0001",
        kind="final_whatsapp_image",
        source="rendered",
        path=str(tmp_path / "wa.png"),
        mime_type="image/png",
        sha256="a" * 64,
        received_at=datetime.now(timezone.utc),
    )

    assert asset.delivery_status == "pending"
    assert asset.outbound_message_id == ""
    assert asset.delivery_attempt_count == 0


def test_delivery_retry_selects_only_unsent_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    sent_asset = project.assets[0].model_copy(update={
        "delivery_status": "sent",
        "outbound_message_id": "wamid.sent.1",
        "delivered_at": datetime.now(timezone.utc),
        "delivery_attempt_count": 1,
    })
    project = project.model_copy(update={"assets": [sent_asset, *project.assets[1:]]})

    pending = mod._pending_project_assets(project)

    assert [asset.asset_id for asset in pending] == ["A0002", "A0003", "A0004"]


def test_final_asset_captions_label_each_customer_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    specs = [
        ("A0001", "final_whatsapp_image", "wa.png", (1080, 1350)),
        ("A0002", "final_instagram_post", "post.png", (1080, 1080)),
        ("A0003", "final_instagram_story", "story.png", (1080, 1920)),
        ("A0004", "final_printable_pdf", "print.pdf", None),
    ]
    assets = []
    for asset_id, kind, name, shape in specs:
        path = tmp_path / name
        path.write_bytes(b"%PDF-1.4 minimal pdf" if shape is None else _png_header_bytes(*shape))
        assets.append(_asset(asset_id, kind, str(path)))

    labels = [mod._caption_for_asset(asset) for asset in assets]

    assert labels == [
        "WhatsApp flyer - ready to forward in WhatsApp.",
        "Instagram post - square feed version.",
        "Instagram story - vertical story/status version.",
        "Printable PDF - best for printing or sharing as a document.",
    ]


def test_png_pixel_dimensions_reads_shape_without_pillow(tmp_path):
    from agents.flyer.render import png_pixel_dimensions

    path = tmp_path / "story.png"
    path.write_bytes(_png_header_bytes(1080, 1920) + b"\x00" * 16)

    assert png_pixel_dimensions(path) == (1080, 1920)


def test_png_pixel_dimensions_returns_none_for_non_png(tmp_path):
    from agents.flyer.render import png_pixel_dimensions

    path = tmp_path / "not.png"
    path.write_bytes(b"asset")

    assert png_pixel_dimensions(path) is None


def test_caption_keeps_channel_claim_when_asset_matches_format_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    story = tmp_path / "story.png"
    story.write_bytes(_png_header_bytes(1080, 1920))
    asset = _asset("A0003", "final_instagram_story", str(story))

    assert mod._caption_for_asset(asset) == "Instagram story - vertical story/status version."


def test_caption_downgrades_when_asset_shape_mismatches_format(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    # A 1080x1080 square mislabeled as an instagram story (which must be 1080x1920):
    # the truthfulness gate must not let the "story" claim reach the customer.
    story = tmp_path / "story.png"
    story.write_bytes(_png_header_bytes(1080, 1080))
    asset = _asset("A0003", "final_instagram_story", str(story))

    caption = mod._caption_for_asset(asset)

    assert "story" not in caption.lower()
    assert caption == "Your final flyer package is ready."


def test_caption_keeps_pdf_claim_regardless_of_file_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    # PDF has no fixed pixel shape (expected_shape is None), so the truthfulness
    # gate must never downgrade its caption even when the bytes are not a PNG.
    pdf = tmp_path / "print.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a png")
    asset = _asset("A0004", "final_printable_pdf", str(pdf))

    assert mod._caption_for_asset(asset) == "Printable PDF - best for printing or sharing as a document."


def test_asset_format_shape_mismatch_predicate(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    good = tmp_path / "story_ok.png"
    good.write_bytes(_png_header_bytes(1080, 1920))
    bad = tmp_path / "story_bad.png"
    bad.write_bytes(_png_header_bytes(1080, 1080))  # square mislabeled as a story
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    assert mod._asset_format_shape_mismatch(_asset("A0001", "final_instagram_story", str(good))) is False
    assert mod._asset_format_shape_mismatch(_asset("A0002", "final_instagram_story", str(bad))) is True
    # PDF has no fixed pixel shape — never a mismatch.
    assert mod._asset_format_shape_mismatch(_asset("A0003", "final_printable_pdf", str(pdf))) is False


def test_send_result_reports_format_truthfulness_downgrades(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    # Distinct filenames so the _project() helper (which writes placeholder bytes
    # to wa.png/story.png) doesn't clobber these shaped PNGs.
    wa = tmp_path / "wa_final.png"
    wa.write_bytes(_png_header_bytes(1080, 1350))  # correct whatsapp shape
    story = tmp_path / "story_final.png"
    story.write_bytes(_png_header_bytes(1080, 1080))  # wrong shape for a story
    assets = [
        _asset("A0001", "final_whatsapp_image", str(wa)),
        _asset("A0003", "final_instagram_story", str(story)),
    ]
    project = _project(tmp_path).model_copy(update={"assets": assets, "final_asset_ids": ["A0001", "A0003"]})
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_a, **_k: type("R", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(mod, "validate_visual_qa_report", lambda *_a, **_k: type("R", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
        "--dry-run-bridge",
    ])

    assert mod.main() == 0

    captured = capsys.readouterr()
    result = json.loads([line for line in captured.out.splitlines() if line.strip().startswith("{")][-1])
    assert result["sent"] == 2
    assert result["format_truthfulness_downgrades"] == ["A0003"]
    # Operator-visible structured log line on stderr for journalctl traceability.
    assert "flyer_format_truthfulness_downgrade" in captured.err
    assert "A0003" in captured.err


def test_record_asset_delivery_persists_success_immediately(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    state_path = tmp_path / "projects.json"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    updated = mod._record_asset_delivery(
        state_path,
        project.project_id,
        "A0001",
        status="sent",
        outbound_message_id="dry-run:wa.png",
        error="",
    )

    asset = next(a for a in updated.assets if a.asset_id == "A0001")
    assert asset.delivery_status == "sent"
    assert asset.outbound_message_id == "dry-run:wa.png"
    assert asset.delivery_attempt_count == 1
    persisted = FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8")).projects[0]
    assert next(a for a in persisted.assets if a.asset_id == "A0001").delivery_status == "sent"


def test_uncertain_delivery_blocks_blind_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    uncertain_asset = project.assets[0].model_copy(update={
        "delivery_status": "uncertain",
        "delivery_error": "ack_parse_failed",
        "delivery_attempt_count": 1,
    })
    project = project.model_copy(update={"assets": [uncertain_asset, *project.assets[1:]]})

    with pytest.raises(SystemExit, match="uncertain delivery"):
        mod._pending_project_assets(project)


def test_uncertain_delivery_block_is_audited_before_retry_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    project = project.model_copy(update={
        "assets": [
            project.assets[0].model_copy(update={
                "delivery_status": "uncertain",
                "delivery_error": "ack_parse_failed",
                "delivery_attempt_count": 1,
            }),
            *project.assets[1:],
        ]
    })
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    class DummyLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_ndjson_append(path: Path, line: str) -> None:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    monkeypatch.setattr(
        mod,
        "_load_runtime_io",
        lambda: (
            DummyLock,
            lambda path, text: Path(path).write_text(text, encoding="utf-8"),
            None,
            None,
            fake_ndjson_append,
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
    ])

    with pytest.raises(SystemExit, match="uncertain delivery"):
        mod.main()

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{
        "type": "flyer_delivery_failed",
        "ts": rows[0]["ts"],
        "project_id": project.project_id,
        "customer_phone": "+19045550123",
        "asset_id": "A0001",
        "status": "uncertain",
        "error": "blocked retry: ack_parse_failed",
    }]


def test_project_delivery_blocks_without_passing_visual_qa(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
    ])

    assert mod.main() == 2

    out = json.loads(capsys.readouterr().out)
    assert out["visual_qa_failed"].endswith("wa.png")
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["error"].startswith("visual_qa_failed")


def test_finalize_keeps_core_whatsapp_final_deliverable_when_optional_formats_fail_qa(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_finalize_script()
    from agents.flyer.render import RenderedAssetSpec

    project = _project(tmp_path)
    state_path = tmp_path / "projects.json"
    final_dir = tmp_path / "finals"
    config_path = tmp_path / "config.yaml"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")
    config_path.write_text("flyer: {}\n", encoding="utf-8")

    class DummyLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

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
            specs.append(RenderedAssetSpec(path=path, kind=kind, output_format=output_format, width=size[0], height=size[1]))
        return specs

    def fake_run_visual_qa(project, artifact_path, *, output_format, asset_id):
        failed = output_format != "whatsapp_image"
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(artifact_path),
            artifact_sha256="a" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="test",
            qa_source="ocr_vision",
            status="failed" if failed else "passed",
            blockers=["optional derivative OCR missed customer facts"] if failed else [],
            checked_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(mod, "FileLock", DummyLock)
    monkeypatch.setattr(mod, "atomic_write_text", lambda path, text: Path(path).write_text(text, encoding="utf-8"))
    monkeypatch.setattr(mod, "load_yaml_model", lambda *_args, **_kwargs: SimpleNamespace(
        flyer=SimpleNamespace(resolve_final_render_provider=lambda: SimpleNamespace(model="deterministic-renderer", quality="medium"))
    ))
    monkeypatch.setattr(mod, "render_final_package", fake_render_final_package)
    monkeypatch.setattr(mod, "run_visual_qa", fake_run_visual_qa)
    monkeypatch.setattr(mod, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "finalize-flyer-assets",
        "--project-id", project.project_id,
        "--approved-message-id", "approve-live",
        "--state-path", str(state_path),
        "--final-dir", str(final_dir),
        "--config-path", str(config_path),
    ])

    assert mod.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["final_asset_count"] == 1
    assert out["skipped_optional_final_asset_count"] == 3
    persisted = FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8")).projects[0]
    assert persisted.status == "finalizing_assets"
    deliverable_assets = [asset for asset in persisted.assets if asset.asset_id in persisted.final_asset_ids]
    assert len(deliverable_assets) == 1
    assert [asset.kind for asset in deliverable_assets] == ["final_whatsapp_image"]
    assert len(persisted.qa_reports) == 4
    assert persisted.manual_review.reason_code == "unclassified"


def test_project_delivery_sends_partial_core_final_package(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path).model_copy(update={"final_asset_ids": ["A0001"]})
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(mod, "validate_visual_qa_report", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
        "--dry-run-bridge",
    ])

    assert mod.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["sent"] == 1
    persisted = FlyerProjectStore.model_validate_json(state_path.read_text(encoding="utf-8")).projects[0]
    assert persisted.status == "delivered"
    assert next(asset for asset in persisted.assets if asset.asset_id == "A0001").delivery_status == "sent"
    assert next(asset for asset in persisted.assets if asset.asset_id == "A0002").delivery_status == "pending"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    delivered = next(row for row in rows if row["type"] == "flyer_assets_delivered")
    assert delivered["asset_ids"] == ["A0001"]


def test_project_delivery_rejects_sidecar_visual_qa_even_when_env_allows_sidecar(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FLYER_QA_ALLOW_SIDECAR", "1")
    mod = _load_script()
    project = _project(tmp_path)
    _write_visual_qa(tmp_path / "wa.png", qa_source="sidecar_test")
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
    ])

    assert mod.main() == 2

    out = json.loads(capsys.readouterr().out)
    assert "sidecar visual QA is disabled" in out["blockers"]


def test_dry_run_project_delivery_can_explicitly_allow_sidecar_visual_qa(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path)
    for asset, output_format in zip(project.assets, ["whatsapp_image", "instagram_post", "instagram_story", "printable_pdf"]):
        _write_visual_qa(Path(asset.path), output_format=output_format, qa_source="sidecar_test")
    state_path = tmp_path / "projects.json"
    log_path = tmp_path / "decisions.log"
    state_path.write_text(FlyerProjectStore(projects=[project]).model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--project-id", project.project_id,
        "--state-path", str(state_path),
        "--log-path", str(log_path),
        "--dry-run-bridge",
        "--allow-sidecar-visual-qa",
    ])

    assert mod.main() == 0


def test_direct_asset_delivery_requires_break_glass(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    asset = tmp_path / "manual.png"
    asset.write_bytes(b"asset")
    monkeypatch.setattr(mod, "validate_text_manifest_file", lambda *_args, **_kwargs: type("Result", (), {"ok": True, "blockers": []})())
    monkeypatch.setattr(sys, "argv", [
        "send-flyer-package",
        "--jid", "15551234567@c.us",
        "--asset", str(asset),
        "--dry-run-bridge",
    ])

    with pytest.raises(SystemExit, match="direct --asset sends require --allow-unverified-asset"):
        mod.main()


def test_delivery_report_summarizes_actionable_project_status(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_report_script()
    project = _project(tmp_path)
    project = project.model_copy(update={
        "assets": [
            project.assets[0].model_copy(update={"delivery_status": "sent", "outbound_message_id": "wamid.1"}),
            project.assets[1].model_copy(update={"delivery_status": "failed", "delivery_error": "bridge down"}),
            project.assets[2].model_copy(update={"delivery_status": "uncertain", "delivery_error": "ack_parse_failed"}),
            project.assets[3],
        ]
    })
    delivered = project.model_copy(update={
        "project_id": "F0002",
        "status": "delivered",
        "assets": [asset.model_copy(update={"delivery_status": "sent", "outbound_message_id": f"wamid.{asset.asset_id}"}) for asset in project.assets],
    })
    store = FlyerProjectStore(projects=[project, delivered])

    report = mod.build_delivery_report(store)

    assert report["projects_total"] == 2
    assert report["blocked_projects"] == 1
    assert report["ready_to_retry_projects"] == 0
    assert report["failed_assets"] == 1
    assert report["uncertain_assets"] == 1
    assert report["pending_assets"] == 1
    assert report["issues"][0]["project_id"] == "F0001"
    assert report["issues"][0]["uncertain_asset_ids"] == ["A0003"]
    assert report["issues"][0]["failed_asset_ids"] == ["A0002"]
    assert report["issues"][0]["pending_asset_ids"] == ["A0004"]


def test_delivery_report_ignores_legacy_delivered_assets_without_delivery_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_report_script()
    legacy_delivered = _project(tmp_path).model_copy(update={
        "status": "delivered",
        "updated_at": datetime.now(timezone.utc),
    })

    report = mod.build_delivery_report(FlyerProjectStore(projects=[legacy_delivered]))

    assert report["ok"] is True
    assert report["issues_total"] == 0
    assert report["pending_assets"] == 0


def test_trial_delivery_upsell_message_tracks_remaining_samples(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    now = datetime.now(timezone.utc)
    store = FlyerCustomerStore()
    customer = store.new_customer(
        business_name="Lakshmi Kitchen",
        business_address="123 Main St",
        public_phone="+17045550199",
        business_whatsapp_number="+17045550199",
        authorized_request_number="+19045550188",
        business_category="restaurant",
        preferred_language="en",
        plan_id="trial",
        now=now,
    ).model_copy(update={
        "status": "trial",
        "current_period_start": now.replace(day=1),
        # robust ~1-month period end: naive now.replace(month=now.month+1) builds an
        # invalid date on month-ends (e.g. May 31 -> June 31) and on December.
        "current_period_end": now + timedelta(days=31),
        "usage_events": [FlyerUsageEvent(
            reservation_id="CUST0001:F0001",
            project_id="F0001",
            customer_id="CUST0001",
            kind="used",
            count=1,
            recorded_at=now,
            message_id="m1",
        )],
    })

    first = mod._trial_upsell_message(customer)
    assert "2 free sample flyers left" in first
    assert "Create another flyer" in first
    assert "CREATE%20ANOTHER%20FLYER" in first
    assert "UPGRADE%20PLAN" in first
    assert "START%20FREE%20TRIAL" not in first
    assert "Start Free Trial" not in first

    used_events = []
    for index in range(1, 4):
        reservation_id = f"CUST0001:F000{index}"
        used_events.append({
            "reservation_id": reservation_id,
            "project_id": f"F000{index}",
            "customer_id": "CUST0001",
            "kind": "used",
            "count": 1,
            "recorded_at": now.isoformat(),
            "message_id": f"m{index}",
        })
    customer = FlyerCustomerStore.model_validate({
        "customers": [customer.model_dump(mode="json") | {"usage_events": used_events}]
    }).customers[0]
    third = mod._trial_upsell_message(customer)
    assert "3 free sample flyers are complete" in third
    assert "$49.99/month" in third
    assert "UPGRADE%20PLAN" in third
    assert "START%20FREE%20TRIAL" not in third


def test_trial_upsell_does_not_block_delivery_on_bad_customer_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    mod = _load_script()
    project = _project(tmp_path).model_copy(update={"customer_phone": "+19045550188"})
    customer_state = tmp_path / "customers.json"
    customer_state.write_text("{not valid json", encoding="utf-8")

    assert mod._trial_upsell_for_project(project, customer_state) == ""
