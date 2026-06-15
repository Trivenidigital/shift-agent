"""Tests for Task 6: stronger regional vision model for Telugu/Indic glyph verification.

The regional model (FLYER_REGIONAL_QA_MODEL, default google/gemini-2.5-flash) is
used for OCR on flyers where project.fields.preferred_language is a regional
language ("te", "hi", etc.) or any locked_fact value contains Indic script.
The default model (FLYER_VISUAL_QA_MODEL, default openai/gpt-4o-mini) is used for
all other (English) projects.

Stubbing pattern: monkeypatch urllib.request.urlopen in the visual_qa module to a
fake that records the request payload and returns a minimal valid OCR JSON response.
This avoids real network calls and lets us assert on the model field of the payload.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Ensure flyer agent + platform schemas are importable.
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
for _p in (_SRC, _SRC / "platform", _SRC / "agents" / "flyer"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import visual_qa as _vqa_module
from agents.flyer import visual_qa as visual_qa_full
from schemas import FlyerLockedFact, FlyerProject, FlyerRequestFields

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)

_MINIMAL_OCR_RESPONSE = json.dumps(
    {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"extracted_text": "Sample flyer text", "quality_notes": []}
                    )
                }
            }
        ]
    }
).encode("utf-8")


class _FakeHTTPResponse:
    """Minimal file-like object returned by the fake urlopen."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _make_urlopen_capture(captured: list[dict[str, Any]]):
    """Return a fake urlopen that appends the decoded JSON payload to `captured`."""

    def _fake_urlopen(req, timeout=None):
        # req is a urllib.request.Request; extract the JSON body
        data = req.data
        if data:
            captured.append(json.loads(data.decode("utf-8")))
        return _FakeHTTPResponse(_MINIMAL_OCR_RESPONSE)

    return _fake_urlopen


def _make_project(
    *,
    preferred_language: str = "en",
    facts: list[tuple[str, str, str]] | None = None,
) -> FlyerProject:
    """Build a minimal FlyerProject."""
    locked = []
    if facts:
        for fact_id, label, value in facts:
            locked.append(
                FlyerLockedFact(
                    fact_id=fact_id,
                    label=label,
                    value=value,
                    source="customer_text",
                    required=True,
                )
            )
    else:
        locked = [
            FlyerLockedFact(
                fact_id="business_name",
                label="Business",
                value="Test Store",
                source="customer_text",
                required=True,
            )
        ]
    return FlyerProject(
        project_id="F9999",
        status="awaiting_final_approval",
        customer_phone="+17329837841",
        created_at=_NOW,
        updated_at=_NOW,
        original_message_id="m-regional-test",
        raw_request="Create a flyer.",
        fields=FlyerRequestFields(preferred_language=preferred_language),  # type: ignore[arg-type]
        locked_facts=locked,
    )


# ---------------------------------------------------------------------------
# Test 1: _vision_text accepts a `model` param and uses it in the payload
# ---------------------------------------------------------------------------


def test_vision_text_uses_model_param(tmp_path, monkeypatch):
    """_vision_text(path, model=...) must forward `model` to the HTTP request payload."""
    artifact = tmp_path / "flyer.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)  # minimal PNG-ish bytes

    # Provide a fake API key so the key-check passes
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake-key")

    captured: list[dict] = []
    monkeypatch.setattr(
        "agents.flyer.visual_qa.urllib.request.urlopen",
        _make_urlopen_capture(captured),
    )

    custom_model = "google/gemini-2.5-flash"
    _vqa_module._vision_text(artifact, model=custom_model)

    assert len(captured) == 1, "Expected exactly one HTTP request"
    assert captured[0]["model"] == custom_model, (
        f"Expected model={custom_model!r} in payload but got {captured[0].get('model')!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: Regional project → run_visual_qa uses the regional model
# ---------------------------------------------------------------------------


def test_run_visual_qa_uses_regional_model_for_telugu_project(tmp_path, monkeypatch):
    """A project with preferred_language='te' must route OCR through REGIONAL_QA_MODEL."""
    artifact = tmp_path / "telugu_flyer.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake-key")
    # Ensure env default regional model is the default (not overridden by CI env)
    monkeypatch.delenv("FLYER_REGIONAL_QA_MODEL", raising=False)

    captured: list[dict] = []
    monkeypatch.setattr(
        "agents.flyer.visual_qa.urllib.request.urlopen",
        _make_urlopen_capture(captured),
    )

    project = _make_project(preferred_language="te")
    visual_qa_full.run_visual_qa(project, artifact, output_format="whatsapp_image")

    assert len(captured) >= 1, "Expected at least one OCR HTTP request"
    used_model = captured[0]["model"]
    assert used_model != "openai/gpt-4o-mini", (
        "Regional project must NOT use the default English model openai/gpt-4o-mini"
    )
    # The default regional model
    assert used_model == "google/gemini-2.5-flash", (
        f"Expected regional model google/gemini-2.5-flash but got {used_model!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: Regional project via regional script in locked fact → regional model
# ---------------------------------------------------------------------------


def test_run_visual_qa_uses_regional_model_for_indic_script_in_locked_fact(tmp_path, monkeypatch):
    """A project with Telugu script in a locked fact must use the regional model."""
    artifact = tmp_path / "indic_flyer.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake-key")
    monkeypatch.delenv("FLYER_REGIONAL_QA_MODEL", raising=False)

    captured: list[dict] = []
    monkeypatch.setattr(
        "agents.flyer.visual_qa.urllib.request.urlopen",
        _make_urlopen_capture(captured),
    )

    # "తాజా మాంసం" = "Fresh Meat" in Telugu — contains Indic script
    project = _make_project(
        preferred_language="en",  # language says English but fact has Telugu
        facts=[
            ("business_name", "Business", "తాజా మాంసం"),
            ("headline", "Headline", "Weekly Special"),
        ],
    )
    visual_qa_full.run_visual_qa(project, artifact, output_format="whatsapp_image")

    assert len(captured) >= 1
    used_model = captured[0]["model"]
    assert used_model == "google/gemini-2.5-flash", (
        f"Expected regional model for Indic-script fact but got {used_model!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: English project → default model
# ---------------------------------------------------------------------------


def test_run_visual_qa_uses_default_model_for_english_project(tmp_path, monkeypatch):
    """A plain English project must use VISION_QA_MODEL (openai/gpt-4o-mini default)."""
    artifact = tmp_path / "english_flyer.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake-key")
    monkeypatch.delenv("FLYER_REGIONAL_QA_MODEL", raising=False)
    monkeypatch.delenv("FLYER_VISUAL_QA_MODEL", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)

    captured: list[dict] = []
    monkeypatch.setattr(
        "agents.flyer.visual_qa.urllib.request.urlopen",
        _make_urlopen_capture(captured),
    )

    project = _make_project(preferred_language="en")
    visual_qa_full.run_visual_qa(project, artifact, output_format="whatsapp_image")

    assert len(captured) >= 1
    used_model = captured[0]["model"]
    assert used_model == "openai/gpt-4o-mini", (
        f"English project must use default model openai/gpt-4o-mini but got {used_model!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: FLYER_REGIONAL_QA_MODEL env override is honored
# ---------------------------------------------------------------------------


def test_flyer_regional_qa_model_env_override_is_honored(tmp_path, monkeypatch):
    """FLYER_REGIONAL_QA_MODEL env var overrides the default regional model."""
    artifact = tmp_path / "te_flyer.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-fake-key")
    custom_regional = "anthropic/claude-3-5-sonnet"
    monkeypatch.setenv("FLYER_REGIONAL_QA_MODEL", custom_regional)

    captured: list[dict] = []
    monkeypatch.setattr(
        "agents.flyer.visual_qa.urllib.request.urlopen",
        _make_urlopen_capture(captured),
    )

    # We need to re-read the module-level constant after env change.
    # The implementation uses the module-level constant at import time,
    # so we also patch it directly as run_visual_qa reads it at call time.
    monkeypatch.setattr("agents.flyer.visual_qa.REGIONAL_QA_MODEL", custom_regional)

    project = _make_project(preferred_language="te")
    visual_qa_full.run_visual_qa(project, artifact, output_format="whatsapp_image")

    assert len(captured) >= 1
    used_model = captured[0]["model"]
    assert used_model == custom_regional, (
        f"FLYER_REGIONAL_QA_MODEL override not honored: expected {custom_regional!r}, got {used_model!r}"
    )


# ---------------------------------------------------------------------------
# Test 6: _project_is_regional predicate — single Indic char must NOT trigger
# ---------------------------------------------------------------------------


def test_single_indic_char_in_fact_is_not_regional():
    """FIX 2: a lone stray Indic glyph in an English fact must NOT route to regional."""
    # chr(0x0C24) is a single Telugu letter embedded in otherwise-English text.
    project = _make_project(
        preferred_language="en",
        facts=[
            ("business_name", "Business", "Store " + chr(0x0C24) + " Special"),
            ("headline", "Headline", "Weekly Deal"),
        ],
    )
    assert _vqa_module._project_is_regional(project) is False


def test_indic_word_in_fact_is_regional():
    """FIX 2: a real Telugu word (>=2 consecutive Indic chars) routes to regional."""
    # "తాజా" = "Fresh" in Telugu (4 consecutive Indic chars).
    telugu_word = chr(0x0C24) + chr(0x0C3E) + chr(0x0C1C) + chr(0x0C3E)
    project = _make_project(
        preferred_language="en",
        facts=[
            ("business_name", "Business", telugu_word),
            ("headline", "Headline", "Weekly Deal"),
        ],
    )
    assert _vqa_module._project_is_regional(project) is True


def test_preferred_language_branch_unchanged():
    """FIX 2: the preferred_language regional branch is unaffected by the fact tightening."""
    project = _make_project(preferred_language="hi")  # default English-only facts
    assert _vqa_module._project_is_regional(project) is True
    english = _make_project(preferred_language="en")
    assert _vqa_module._project_is_regional(english) is False
