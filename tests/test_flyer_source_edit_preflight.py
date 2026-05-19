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


def test_source_edit_preflight_requires_uploaded_reference(tmp_path, monkeypatch):
    """Regression: a project with no reference_image asset must NOT enter the
    source-edit provider path. Source-edit semantically requires an uploaded
    source flyer to edit; without it the right action is manual review or
    re-prompting the customer."""
    actions = _load_actions_module()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ok, detail = actions.flyer_source_edit_preflight({"assets": []})
    assert ok is False
    assert "uploaded reference image" in detail


def test_source_edit_preflight_rejects_placeholder_provider_key(tmp_path, monkeypatch):
    """Regression: a .env that still has the PLACEHOLDER token (typical on
    fresh customer VPSes before key provisioning) must fail-closed, not
    proceed to the OpenAI image-edit call and surface a 401 mid-customer-flow."""
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENAI_API_KEY", "PLACEHOLDER-not-a-real-key")

    ok, detail = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })

    assert ok is False
    assert "provider is not configured" in detail


def test_source_edit_preflight_rejects_missing_image_on_disk(tmp_path, monkeypatch):
    """Regression: a reference_image asset whose `path` no longer exists on
    disk (e.g. cleaned by retention, never copied during failover) must fail
    preflight so the operator gets a clear blocker rather than an opaque
    404-from-OpenAI later."""
    actions = _load_actions_module()
    missing = tmp_path / "missing.png"
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    ok, detail = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(missing),
            "mime_type": "image/png",
        }]
    })

    assert ok is False
    assert "not available" in detail


def test_every_preflight_failure_site_in_hooks_queues_typed_reason_code():
    """Structural invariant: every cf-router hooks.py site that calls
    `flyer_source_edit_preflight` and short-circuits on `not ready_ok` MUST
    update the project to manual_edit_required with `--manual-reason-code
    source_edit_provider_unavailable`. Pre-S6 the second site (reference-
    scope-authorized flow) sent the customer ack but never queued the project
    — the cockpit triage view had no row to surface, "any update?" checks had
    no manual_review to read.

    Static analysis: scan hooks.py for each `flyer_source_edit_preflight(...)`
    call followed within ~30 lines by `if not ready_ok:` and verify the same
    branch references both `--queue-manual-review` and
    `--manual-reason-code` ... `source_edit_provider_unavailable`.
    """
    import re
    hooks_path = REPO / "src" / "plugins" / "cf-router" / "hooks.py"
    text = hooks_path.read_text(encoding="utf-8")

    # Match each `if not ready_ok:` block — body is everything until the next
    # de-dented line (start of return/etc at less-than the if's indent).
    # Use a loose regex: capture from `if not ready_ok:` up to the next
    # function-level return that ends the block.
    blocks = re.findall(
        r"flyer_source_edit_preflight\([^)]*\)\s*\n\s*if not ready_ok:.*?return\s*\{",
        text,
        flags=re.DOTALL,
    )
    assert blocks, "expected at least one preflight-failure block in hooks.py"

    for i, block in enumerate(blocks):
        assert "--queue-manual-review" in block, (
            f"preflight-failure block #{i} in hooks.py must queue manual review"
        )
        assert "--manual-reason-code" in block, (
            f"preflight-failure block #{i} in hooks.py must pass --manual-reason-code"
        )
        assert "source_edit_provider_unavailable" in block, (
            f"preflight-failure block #{i} in hooks.py must use reason_code "
            f"source_edit_provider_unavailable, not a free-form --manual-reason only"
        )
