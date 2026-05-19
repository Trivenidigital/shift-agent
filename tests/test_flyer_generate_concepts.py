"""Contracts for Flyer concept generation preflight."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agents" / "flyer" / "scripts" / "generate-flyer-concepts"
PLATFORM = REPO / "src" / "platform"
SRC = REPO / "src"


class _NoopFileLock:
    def __init__(self, _path: Path) -> None:
        pass

    def __enter__(self) -> "_NoopFileLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _load_script(monkeypatch: pytest.MonkeyPatch):
    sys.path.insert(0, str(PLATFORM))
    sys.path.insert(0, str(SRC))
    from schemas import Config  # noqa: E402

    fake_safe_io = types.ModuleType("safe_io")
    fake_safe_io.FileLock = _NoopFileLock
    fake_safe_io.atomic_write_text = lambda path, text: Path(path).write_text(text, encoding="utf-8")
    fake_safe_io.load_yaml_model = lambda *_args, **_kwargs: Config.model_validate({
        "schema_version": 1,
        "customer": {"name": "Triveni", "location_id": "loc_pineville_01", "timezone": "America/New_York"},
        "owner": {"name": "Owner", "phone": "+19045550000"},
        "limits": {},
        "alerting": {"pushover_user_key": "k", "pushover_app_token": "t"},
        "backup": {"gpg_recipient_email": "owner@example.com"},
        "flyer": {"enabled": True, "draft_image_model": "deterministic-renderer", "concept_count": 1},
    })
    monkeypatch.setitem(sys.modules, "safe_io", fake_safe_io)
    module_name = "generate_flyer_concepts_under_test"
    sys.modules.pop(module_name, None)
    loader = importlib.machinery.SourceFileLoader(module_name, str(SCRIPT))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _project_with_failed_reference() -> dict:
    now = datetime(2026, 5, 19, tzinfo=timezone.utc).isoformat()
    return {
        "project_id": "F0001",
        "status": "intake_started",
        "customer_phone": "+17329837841",
        "created_at": now,
        "updated_at": now,
        "original_message_id": "m-ref",
        "raw_request": "Create menu flyer. Extract item names and prices from attached sample flyer.",
        "reference_extractions": [{
            "asset_id": "A0001",
            "role": "menu_reference",
            "provider": "unavailable",
            "status": "provider_unavailable",
            "detail": "reference OCR/vision provider unavailable",
            "extracted_at": now,
        }],
        "assets": [{
            "asset_id": "A0001",
            "kind": "reference_image",
            "source": "whatsapp",
            "path": "/opt/shift-agent/state/flyer/assets/F0001-reference.png",
            "mime_type": "image/png",
            "sha256": "a" * 64,
            "original_message_id": "m-ref",
            "received_at": now,
        }],
    }


def _project_with_pending_reference(asset_path: Path) -> dict:
    project = _project_with_failed_reference()
    project["reference_extractions"][0]["provider"] = "not_run"
    project["reference_extractions"][0]["status"] = "not_run"
    project["reference_extractions"][0]["detail"] = "reference extraction pending"
    project["assets"][0]["path"] = str(asset_path)
    return project


def test_generate_refuses_unextracted_required_reference_before_render(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [_project_with_failed_reference()],
    }), encoding="utf-8")

    def explode_render(*_args, **_kwargs):
        raise AssertionError("render must not run when required reference extraction failed")

    monkeypatch.setattr(module, "render_concept_previews", explode_render)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 2
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]

    assert out["project_id"] == "F0001"
    assert out["reference_extraction_failed"][0]["status"] == "provider_unavailable"
    assert persisted["status"] == "manual_edit_required"
    assert persisted["manual_review"]["status"] == "queued"
    assert persisted["manual_review"]["reason"] == "reference_provider_unavailable"


def test_generate_extracts_pending_reference_facts_before_render(monkeypatch, tmp_path, capsys):
    module = _load_script(monkeypatch)
    from schemas import FlyerVisualQAReport  # noqa: E402

    monkeypatch.setenv("FLYER_STATE_ROOT", str(tmp_path))
    state_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    reference = asset_dir / "F0001-reference.png"
    reference.write_bytes(b"fake image bytes")
    rendered = asset_dir / "F0001-C1.png"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "next_sequence": 2,
        "projects": [_project_with_pending_reference(reference)],
    }), encoding="utf-8")

    class FakeProvider:
        provider_name = "fake_vision"

        def extract_text(self, _asset, _raw_request):
            return "Idly $7\nDosa $8", "ok"

    def fake_render(project, _asset_dir, **_kwargs):
        by_value = {fact.value for fact in project.locked_facts}
        assert {"Idly", "$7", "Dosa", "$8"}.issubset(by_value)
        rendered.write_bytes(b"rendered")
        return [types.SimpleNamespace(
            path=rendered,
            kind="concept_preview",
            output_format="concept_preview",
            width=1080,
            height=1350,
            concept_id="C1",
        )]

    def fake_qa(project, path, *, output_format, asset_id):
        return FlyerVisualQAReport(
            project_id=project.project_id,
            asset_id=asset_id,
            artifact_path=str(path),
            artifact_sha256="b" * 64,
            project_version=project.version,
            output_format=output_format,
            provider="test",
            qa_source="sidecar_test",
            status="passed",
            checked_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(module, "build_reference_extraction_provider", lambda: FakeProvider())
    monkeypatch.setattr(module, "render_concept_previews", fake_render)
    monkeypatch.setattr(module, "run_visual_qa", fake_qa)
    monkeypatch.setattr(module, "write_visual_qa_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "generate-flyer-concepts",
        "--project-id", "F0001",
        "--state-path", str(state_path),
        "--asset-dir", str(asset_dir),
        "--config-path", str(tmp_path / "config.yaml"),
    ])

    assert module.main() == 0
    out = json.loads(capsys.readouterr().out)
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["projects"][0]
    values = {fact["value"] for fact in persisted["locked_facts"]}

    assert out["project_id"] == "F0001"
    assert {"Idly", "$7", "Dosa", "$8"}.issubset(values)
    assert persisted["reference_extractions"][0]["status"] == "ok"
    assert persisted["status"] == "awaiting_final_approval"
