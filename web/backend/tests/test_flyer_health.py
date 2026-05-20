"""Tests for GET /flyer/health and its private helpers (P0-7).

Covers acceptance from `tasks/flyer-cockpit-p0-7-health-panel-plan.md`:
- Auth-gated.
- No secrets in response body.
- Placeholder keys count as missing/degraded.
- OpenRouter missing/placeholder = red.
- OpenAI source-edit missing/placeholder = yellow (degraded).
- key_source reports which env file/process_env matched.
- Manual-queue impact (queued_count + oldest_age_hours) for
  source_edit_provider_unavailable rows.
- Deploy tag surfaced when markers exist; null when absent.
- Model config reflects deployed FlyerConfig values.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest


# ─── Helpers to write env files + project state ──────────────────────────


def _write_env_file(path, kvs: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{k}="{v}"' for k, v in kvs.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _manual_edit_project(
    project_id: str,
    *,
    reason_code: str = "source_edit_provider_unavailable",
    manual_status: str = "queued",
    queued_at: str = "2026-05-18T00:00:00Z",
) -> dict:
    return {
        "project_id": project_id,
        "status": "manual_edit_required",
        "customer_phone": "+17329837841",
        "created_at": "2026-05-18T00:00:00Z",
        "updated_at": queued_at,
        "original_message_id": f"msg-{project_id}",
        "raw_request": "Edit uploaded flyer/source artwork.",
        "fields": {"event_or_business_name": "Lakshmis Kitchen", "contact_info": "+17329837841"},
        "assets": [],
        "concepts": [],
        "selected_concept_id": None,
        "revisions": [],
        "version": 1,
        "final_asset_ids": [],
        "approved_message_id": "",
        "manual_review": {
            "status": manual_status,
            "reason": reason_code,
            "reason_code": reason_code,
            "detail": "",
            "queued_at": queued_at,
        },
    }


def _clear_provider_env(monkeypatch) -> None:
    """Strip provider env vars + force empty env-file overrides."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _isolate_env_files(monkeypatch, tmp_path):
    """Point HERMES_ENV_PATH + SHIFT_AGENT_ENV_PATH at non-existent files
    so tests aren't poisoned by whatever the host has on disk."""
    monkeypatch.setenv("HERMES_ENV_PATH", str(tmp_path / "hermes" / "missing.env"))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(tmp_path / "agent" / "missing.env"))


def _isolate_deploy_markers(monkeypatch, tmp_path):
    monkeypatch.setenv("COCKPIT_DEPLOY_HASH_PATH", str(tmp_path / "no-commit-hash"))
    monkeypatch.setenv("COCKPIT_DEPLOYS_DIR", str(tmp_path / "no-deploys"))


def _build_test_client():
    from fastapi.testclient import TestClient
    from app import auth as auth_mod
    from app.main import app

    async def _bypass_auth():
        return {"sub": "test-operator", "iat": 9_999_999_999}

    app.dependency_overrides[auth_mod.require_auth] = _bypass_auth

    class _Ctx:
        def __enter__(self):
            self.client = TestClient(app)
            return self.client

        def __exit__(self, *args):
            self.client.close()
            app.dependency_overrides.clear()

    return _Ctx()


# ─── Auth + shape ────────────────────────────────────────────────────────


def test_flyer_health_requires_auth(tmp_path, monkeypatch):
    """Without auth override, /flyer/health returns 401."""
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/flyer/health")
    assert resp.status_code == 401


def test_flyer_health_returns_expected_shape(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    with _build_test_client() as client:
        resp = client.get("/flyer/health")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"checked_at", "deploy_tag", "commit_hash", "components", "providers"}
    component_names = {c["name"] for c in body["components"]}
    assert {"gateway", "whatsapp_bridge", "whatsapp_paired", "cockpit_service"} <= component_names
    provider_names = {p["name"] for p in body["providers"]}
    assert provider_names == {"openrouter_generation_vision", "openai_source_edit"}


# ─── Secret redaction ────────────────────────────────────────────────────


def test_flyer_health_redacts_secret_values(tmp_path, monkeypatch):
    secret = "sk-realsecretvalue1234567890abcdef"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    monkeypatch.setenv("OPENAI_API_KEY", secret + "OPENAI")
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    with _build_test_client() as client:
        resp = client.get("/flyer/health")

    raw = resp.text
    assert secret not in raw, "Secret OPENROUTER value leaked into health response body"
    assert (secret + "OPENAI") not in raw, "Secret OPENAI value leaked into health response body"
    # Quick sanity: known-safe substrings should still be there.
    assert "OPENROUTER_API_KEY" in raw  # used in detail text without value
    assert "key_present" in raw

    body = resp.json()
    provider_or = next(p for p in body["providers"] if p["name"] == "openrouter_generation_vision")
    provider_oa = next(p for p in body["providers"] if p["name"] == "openai_source_edit")
    assert provider_or["key_present"] is True
    assert provider_or["key_source"] == "process_env"
    assert provider_oa["key_present"] is True
    assert provider_oa["key_source"] == "process_env"


# ─── OpenRouter severity matrix ──────────────────────────────────────────


def test_openrouter_missing_is_red(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    providers = flyer._flyer_provider_components()
    or_p = next(p for p in providers if p["name"] == "openrouter_generation_vision")
    assert or_p["severity"] == "red"
    assert or_p["key_present"] is False
    assert or_p["key_source"] is None
    assert "missing" in or_p["detail"].lower() or "blocked" in or_p["detail"].lower()


def test_openrouter_placeholder_is_red(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "PLACEHOLDER")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    or_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "openrouter_generation_vision")
    assert or_p["severity"] == "red"
    assert or_p["key_present"] is False
    assert "placeholder" in or_p["detail"].lower()


def test_openrouter_present_is_green(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-real-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    or_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "openrouter_generation_vision")
    assert or_p["severity"] == "green"
    assert or_p["key_present"] is True
    assert or_p["key_source"] == "process_env"
    assert "sk-real-key" not in or_p["detail"]


# ─── OpenAI source-edit severity matrix ──────────────────────────────────


def test_openai_source_edit_missing_is_yellow_not_red(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    oa_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "openai_source_edit")
    assert oa_p["severity"] == "yellow", "source-edit missing must be degraded, not blocking"
    assert oa_p["key_present"] is False
    assert oa_p["key_source"] is None
    assert "manual review" in oa_p["detail"].lower()


def test_openai_source_edit_placeholder_is_yellow(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-PLACEHOLDER")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    oa_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "openai_source_edit")
    assert oa_p["severity"] == "yellow"
    assert oa_p["key_present"] is False


def test_openai_source_edit_present_is_green(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    oa_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "openai_source_edit")
    assert oa_p["severity"] == "green"
    assert oa_p["key_present"] is True


# ─── key_source layered-env reader ───────────────────────────────────────


def test_key_source_reports_hermes_env(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    hermes_env = tmp_path / "hermes" / "h.env"
    _write_env_file(hermes_env, {"OPENROUTER_API_KEY": "sk-from-hermes"})
    monkeypatch.setenv("HERMES_ENV_PATH", str(hermes_env))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(tmp_path / "agent" / "missing.env"))
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    value, source = flyer._read_env_layered("OPENROUTER_API_KEY")
    assert value == "sk-from-hermes"
    assert source == "hermes_env"


def test_key_source_reports_agent_env_when_hermes_empty(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    agent_env = tmp_path / "agent" / "a.env"
    _write_env_file(agent_env, {"OPENROUTER_API_KEY": "sk-from-agent"})
    monkeypatch.setenv("HERMES_ENV_PATH", str(tmp_path / "hermes" / "missing.env"))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(agent_env))
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    value, source = flyer._read_env_layered("OPENROUTER_API_KEY")
    assert value == "sk-from-agent"
    assert source == "agent_env"


def test_process_env_wins_over_files(tmp_path, monkeypatch):
    hermes_env = tmp_path / "hermes" / "h.env"
    _write_env_file(hermes_env, {"OPENROUTER_API_KEY": "sk-from-hermes"})
    monkeypatch.setenv("HERMES_ENV_PATH", str(hermes_env))
    monkeypatch.setenv("SHIFT_AGENT_ENV_PATH", str(tmp_path / "agent" / "missing.env"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-process")
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    value, source = flyer._read_env_layered("OPENROUTER_API_KEY")
    assert value == "sk-from-process"
    assert source == "process_env"


def test_no_env_anywhere_returns_none_source(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    value, source = flyer._read_env_layered("OPENROUTER_API_KEY")
    assert value == ""
    assert source is None


# ─── Deploy tag resolution ───────────────────────────────────────────────


def test_deploy_tag_resolves_from_markers(tmp_path, monkeypatch):
    hash_path = tmp_path / ".commit-hash"
    hash_path.write_text("a0e853e7abcdef1234567890\n", encoding="utf-8")
    deploys_dir = tmp_path / "deploys"
    deploys_dir.mkdir()
    (deploys_dir / "deploy-20260520-000424-a0e853e7.tgz").write_bytes(b"x")
    (deploys_dir / "deploy-20260519-120000-aaaaaaaa.tgz").write_bytes(b"x")
    monkeypatch.setenv("COCKPIT_DEPLOY_HASH_PATH", str(hash_path))
    monkeypatch.setenv("COCKPIT_DEPLOYS_DIR", str(deploys_dir))

    from app.routers import flyer

    deploy_tag, commit_hash = flyer._cockpit_deploy_tag()
    assert deploy_tag == "deploy-20260520-000424-a0e853e7"
    assert commit_hash == "a0e853e7abcdef1234567890"


def test_deploy_tag_null_when_markers_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("COCKPIT_DEPLOY_HASH_PATH", str(tmp_path / "no-hash"))
    monkeypatch.setenv("COCKPIT_DEPLOYS_DIR", str(tmp_path / "no-deploys"))

    from app.routers import flyer

    deploy_tag, commit_hash = flyer._cockpit_deploy_tag()
    assert deploy_tag is None
    assert commit_hash is None


# ─── Model config + manual-queue impact ──────────────────────────────────


def test_model_config_present_in_provider_block(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    providers = flyer._flyer_provider_components()
    or_p = next(p for p in providers if p["name"] == "openrouter_generation_vision")
    oa_p = next(p for p in providers if p["name"] == "openai_source_edit")
    assert "draft_image_model" in or_p["model_config"]
    assert "final_image_model" in or_p["model_config"]
    assert "edit_image_model" in oa_p["model_config"]


def test_manual_queue_impact_zero_by_default(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {"schema_version": 1, "next_sequence": 1, "projects": []},
    )

    impact = flyer._source_edit_manual_queue_impact()
    assert impact == {"queued_count": 0, "oldest_age_hours": None}


def test_manual_queue_impact_counts_source_edit_unavailable_rows(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    # Two queued source-edit-unavailable rows; one completed (should not count);
    # one with a different reason code (should not count).
    queued_old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    queued_new = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 5,
            "projects": [
                _manual_edit_project("F0060", manual_status="queued", queued_at=queued_old),
                _manual_edit_project("F0061", manual_status="in_progress", queued_at=queued_new),
                _manual_edit_project("F0062", manual_status="completed", queued_at=queued_new),
                _manual_edit_project("F0063", reason_code="reference_unsupported", queued_at=queued_new),
            ],
        },
    )

    impact = flyer._source_edit_manual_queue_impact()
    assert impact["queued_count"] == 2
    assert impact["oldest_age_hours"] is not None and impact["oldest_age_hours"] >= 4


def test_source_edit_detail_surfaces_queue_impact_when_present(tmp_path, monkeypatch):
    """When source-edit is missing AND queued_count > 0, detail must call out manual review."""
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    queued_at = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 2,
            "projects": [_manual_edit_project("F0060", queued_at=queued_at)],
        },
    )

    oa_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "openai_source_edit")
    assert oa_p["manual_queue_impact"]["queued_count"] == 1
    assert "falling back to manual review" in oa_p["detail"]
    assert oa_p["severity"] == "yellow"
