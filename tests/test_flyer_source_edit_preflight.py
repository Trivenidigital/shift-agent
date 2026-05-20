"""Pure checks for source-preserving edit readiness."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
ACTIONS = REPO / "src" / "plugins" / "cf-router" / "actions.py"


def _load_workflow_module():
    """Load `src/agents/flyer/workflow.py` so we can call
    `source_edit_provider_ready` directly (the 2-tuple layer)."""
    src = REPO / "src"
    platform_dir = REPO / "src" / "platform"
    if str(platform_dir) not in sys.path:
        sys.path.insert(0, str(platform_dir))
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    # Import via the package path so __name__ matches the deployed layout.
    from agents.flyer import workflow  # type: ignore
    return workflow


def _load_actions_module():
    spec = importlib.util.spec_from_file_location("cf_router_actions_preflight_under_test", ACTIONS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_edit_preflight_rejects_pdf_reference(tmp_path, monkeypatch):
    """PDF reference must NOT bucket as a provider outage — it's an
    unsupported-media gap the operator triages by re-uploading an image."""
    actions = _load_actions_module()
    pdf = tmp_path / "reference.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(pdf),
            "mime_type": "application/pdf",
        }]
    })

    assert ok is False
    # Detail comes from `source_edit_provider_ready` (mime check at workflow.py)
    # when mime_type='application/pdf'. The preflight classifies non-image mime
    # as `reference_unsupported` so cockpit triage groups it with media gaps,
    # not provider outages.
    assert "must be an image" in detail or "PDF" in detail
    assert reason_code == "reference_unsupported"


def test_source_edit_preflight_requires_provider_key(tmp_path, monkeypatch):
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Set OPENAI_API_KEY to a valid value to prove non-influence: this PR moves
    # source-edit off OpenAI, so OPENAI_API_KEY presence must NOT mark provider
    # as ready.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-test")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })

    assert ok is False
    assert "provider is not configured" in detail
    assert "OPENROUTER_API_KEY" in detail
    assert reason_code == "source_edit_provider_unavailable"


def test_source_edit_preflight_accepts_available_image(tmp_path, monkeypatch):
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })

    assert ok is True
    assert detail == "ready"
    assert reason_code == ""


def test_source_edit_preflight_requires_uploaded_reference(tmp_path, monkeypatch):
    """Regression: a project with no reference_image asset must NOT enter the
    source-edit provider path. Missing reference → reference_provider_unavailable
    so operators see "re-upload source flyer" not "provider outage"."""
    actions = _load_actions_module()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({"assets": []})
    assert ok is False
    assert "uploaded reference image" in detail
    assert reason_code == "reference_provider_unavailable"


def test_source_edit_preflight_rejects_placeholder_provider_key(tmp_path, monkeypatch):
    """Regression: a .env that still has the PLACEHOLDER token (typical on
    fresh customer VPSes before key provisioning) must fail-closed."""
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER-not-a-real-key")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })

    assert ok is False
    assert "provider is not configured" in detail
    assert reason_code == "source_edit_provider_unavailable"


def test_source_edit_preflight_rejects_missing_image_on_disk(tmp_path, monkeypatch):
    """Regression: a reference_image asset whose `path` no longer exists on
    disk → reference_provider_unavailable (operator action: re-upload)."""
    actions = _load_actions_module()
    missing = tmp_path / "missing.png"
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(missing),
            "mime_type": "image/png",
        }]
    })

    assert ok is False
    assert "not available" in detail
    assert reason_code == "reference_provider_unavailable"


def test_every_preflight_failure_site_in_hooks_uses_dynamic_reason_code():
    """Structural invariant: every cf-router hooks.py site that calls
    `flyer_source_edit_preflight` and short-circuits on `not ready_ok` MUST:
      - unpack the 3-tuple (`ready_ok, ready_detail, ready_reason_code`)
      - call invoke_update_flyer_project with --queue-manual-review AND
        --manual-reason-code threaded from the dynamic ready_reason_code
        (so PDF rejections, missing-reference, and missing-key each get
        their own typed code in cockpit triage)
      - capture the queue_ok return value (no silent queue failures)
      - skip the customer "queued" ack when queue_ok is False
    """
    import re
    hooks_path = REPO / "src" / "plugins" / "cf-router" / "hooks.py"
    text = hooks_path.read_text(encoding="utf-8")

    blocks = re.findall(
        r"flyer_source_edit_preflight\([^)]*\).*?(?:return\s*\{|return None)",
        text,
        flags=re.DOTALL,
    )
    assert blocks, "expected at least one preflight call block in hooks.py"

    failure_blocks = [b for b in blocks if "if not ready_ok:" in b]
    assert failure_blocks, "expected at least one preflight-failure block"

    for i, block in enumerate(failure_blocks):
        # 3-tuple unpack
        assert "ready_reason_code" in block, (
            f"preflight-failure block #{i}: must unpack the 3-tuple including ready_reason_code"
        )
        # Queue with dynamic reason code
        assert "--queue-manual-review" in block, (
            f"preflight-failure block #{i} must queue manual review"
        )
        assert "--manual-reason-code" in block, (
            f"preflight-failure block #{i} must pass --manual-reason-code"
        )
        # Must NOT hardcode source_edit_provider_unavailable as the only code —
        # the reason_code is supplied by the preflight result.
        assert "ready_reason_code" in block and "ready_reason_code," in block, (
            f"preflight-failure block #{i} must thread the dynamic reason_code "
            f"(not hardcode source_edit_provider_unavailable for every failure)"
        )
        # Queue-result capture (fix B)
        assert "queue_ok" in block and "queue_detail" in block, (
            f"preflight-failure block #{i} must capture queue_ok/queue_detail "
            f"(do not swallow invoke_update_flyer_project failures)"
        )
        # Skip ack on queue failure
        assert "if queue_ok:" in block, (
            f"preflight-failure block #{i} must guard send_flyer_manual_edit_ack on queue_ok"
        )


def test_source_branch_route_falls_through_source_edit_preflight(tmp_path, monkeypatch):
    """Regression-pin (Task 8): the new SOURCE branch added in Task 5 routes
    customers through trigger_create_flyer_project(manual_edit_required=True).
    The downstream `--manual-edit-required` path in create-flyer-project
    queues `source_edit_provider_unavailable` when OPENROUTER_API_KEY is
    absent; this test pins that the source-edit preflight returns the correct
    triad (which the create-flyer-project flow then keys on)."""
    actions = _load_actions_module()
    image = tmp_path / "ref.png"
    image.write_bytes(b"png")
    # No OPENROUTER key — provider unavailable.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })
    assert ok is False
    assert reason_code == "source_edit_provider_unavailable"


def test_source_branch_with_provider_key_passes_preflight(tmp_path, monkeypatch):
    """SOURCE branch + valid OPENROUTER key reaches the generate path
    (preflight returns ready)."""
    actions = _load_actions_module()
    image = tmp_path / "ref.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-real-test-key")

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })
    assert ok is True
    assert detail == "ready"
    assert reason_code == ""


def test_site_2_release_runs_before_ack_for_consistent_quota_ordering():
    """Fix E: in `_try_flyer_reference_scope_authorization_intercept`, the
    quota release MUST happen before the customer ack (matching site 1). If
    the ack stalls or the customer retries before release runs, the quota
    reservation could leak."""
    import re
    hooks_path = REPO / "src" / "plugins" / "cf-router" / "hooks.py"
    text = hooks_path.read_text(encoding="utf-8")

    # Find the reference-scope-authorization intercept's failure block.
    match = re.search(
        r"def _try_flyer_reference_scope_authorization_intercept.*?"
        r"flyer_source_edit_preflight.*?"
        r"if not ready_ok:(.*?)return\s*\{",
        text,
        flags=re.DOTALL,
    )
    assert match, "could not locate reference-scope-authorization preflight failure block"
    block = match.group(1)
    release_pos = block.find("_release_flyer_access")
    ack_pos = block.find("send_flyer_manual_edit_ack")
    assert 0 <= release_pos < ack_pos, (
        "fix E: _release_flyer_access must appear BEFORE send_flyer_manual_edit_ack "
        "in the reference-scope-authorization preflight failure block "
        f"(release_pos={release_pos}, ack_pos={ack_pos})"
    )


# ─── 2-tuple layer: workflow.py:source_edit_provider_ready directly ──


def _project_with_reference(path: Path) -> dict:
    return {
        "assets": [{
            "kind": "reference_image",
            "path": str(path),
            "mime_type": "image/png",
        }]
    }


def test_source_edit_provider_ready_returns_false_when_openrouter_missing(tmp_path, monkeypatch):
    """2-tuple layer: missing OPENROUTER_API_KEY (and present OPENAI_API_KEY,
    to prove non-influence) → (False, '...OPENROUTER_API_KEY missing'). Pins
    the env-key swap inside workflow.py."""
    workflow = _load_workflow_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-test")
    env_file = tmp_path / "shift-agent.env"
    env_file.write_text("# empty\n", encoding="utf-8")

    ok, detail = workflow.source_edit_provider_ready(
        _project_with_reference(image), env_path=env_file,
    )
    assert ok is False
    assert "OPENROUTER_API_KEY" in detail
    assert "provider is not configured" in detail


def test_source_edit_provider_ready_returns_false_on_placeholder(tmp_path, monkeypatch):
    """2-tuple layer: PLACEHOLDER OPENROUTER_API_KEY still fails closed."""
    workflow = _load_workflow_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER_xxxx_not_real")
    env_file = tmp_path / "shift-agent.env"
    env_file.write_text("# empty\n", encoding="utf-8")

    ok, detail = workflow.source_edit_provider_ready(
        _project_with_reference(image), env_path=env_file,
    )
    assert ok is False
    assert "OPENROUTER_API_KEY" in detail


def test_source_edit_provider_ready_returns_true_with_valid_openrouter(tmp_path, monkeypatch):
    """2-tuple layer: valid OPENROUTER_API_KEY + valid image reference →
    (True, 'ready')."""
    workflow = _load_workflow_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-real-test-key")
    env_file = tmp_path / "shift-agent.env"
    env_file.write_text("# empty\n", encoding="utf-8")

    ok, detail = workflow.source_edit_provider_ready(
        _project_with_reference(image), env_path=env_file,
    )
    assert ok is True
    assert detail == "ready"


def test_source_edit_provider_ready_reads_key_from_env_file(tmp_path, monkeypatch):
    """2-tuple layer: dual env-file search — process env empty, key resolved
    from the env file passed via `env_path`. Mirrors deployed dual-search
    (Hermes .env / shift-agent .env) covered through the explicit
    `env_path` test-injection seam."""
    workflow = _load_workflow_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env_file = tmp_path / "agent.env"
    env_file.write_text('OPENROUTER_API_KEY="sk-or-v1-from-file"\n', encoding="utf-8")

    ok, detail = workflow.source_edit_provider_ready(
        _project_with_reference(image), env_path=env_file,
    )
    assert ok is True
    assert detail == "ready"


def test_flyer_source_edit_preflight_wraps_2tuple_with_provider_unavailable_code(tmp_path, monkeypatch):
    """3-tuple wrapper: when source_edit_provider_ready returns (False,
    '...OPENROUTER_API_KEY missing'), flyer_source_edit_preflight maps it
    to reason_code='source_edit_provider_unavailable' via the existing
    substring-fallback branch in actions.py — preserved by exhaustion."""
    actions = _load_actions_module()
    image = tmp_path / "reference.png"
    image.write_bytes(b"png")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    ok, detail, reason_code = actions.flyer_source_edit_preflight({
        "assets": [{
            "kind": "reference_image",
            "path": str(image),
            "mime_type": "image/png",
        }]
    })
    assert ok is False
    assert "OPENROUTER_API_KEY" in detail
    assert reason_code == "source_edit_provider_unavailable"
