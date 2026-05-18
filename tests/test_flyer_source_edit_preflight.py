"""Pure checks for source-preserving edit readiness."""
from __future__ import annotations

import importlib.util
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
ACTIONS = REPO / "src" / "plugins" / "cf-router" / "actions.py"


def _load_actions_module():
    spec = importlib.util.spec_from_file_location("cf_router_actions_preflight_under_test", ACTIONS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_edit_preflight_rejects_pdf_reference(tmp_path, monkeypatch):
    actions = _load_actions_module()
    pdf = tmp_path / "reference.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ok, detail = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(pdf),
            "mime_type": "application/pdf",
        }]
    })

    assert ok is False
    assert "must be an image" in detail


def test_source_edit_preflight_requires_provider_key(tmp_path, monkeypatch):
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    ok, detail = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })

    assert ok is False
    assert "provider is not configured" in detail


def test_source_edit_preflight_accepts_available_image(tmp_path, monkeypatch):
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ok, detail = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })

    assert ok is True
    assert detail == "ready"
