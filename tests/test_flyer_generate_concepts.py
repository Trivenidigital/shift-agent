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
