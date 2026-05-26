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

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


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
    from app.routers import health

    monkeypatch.setattr(
        health.settings,
        "hermes_creds_json",
        tmp_path / "hermes" / "whatsapp" / "session" / "missing-creds.json",
    )


def _isolate_deploy_markers(monkeypatch, tmp_path):
    monkeypatch.setenv("SHIFT_AGENT_DEPLOY_HASH_PATH", str(tmp_path / "no-commit-hash"))
    monkeypatch.setenv("SHIFT_AGENT_DEPLOYS_DIR", str(tmp_path / "no-deploys"))


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


def _mock_flyer_config(monkeypatch, flyer_payload: dict) -> None:
    from app import state as state_mod
    from schemas import FlyerConfig

    cfg = FlyerConfig.model_validate(flyer_payload)
    monkeypatch.setattr(state_mod, "load_config", lambda: SimpleNamespace(flyer=cfg))


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
    assert set(body.keys()) >= {
        "checked_at",
        "shift_agent_deploy_tag",
        "shift_agent_commit_hash",
        "components",
        "providers",
    }
    component_names = {c["name"] for c in body["components"]}
    assert {"gateway", "whatsapp_bridge", "whatsapp_paired", "shift_agent_deploy"} <= component_names
    # Truthfulness: top-level deploy fields are named for the agent tarball,
    # not the cockpit (the cockpit deploys separately with no marker today).
    assert "shift_agent_deploy_tag" in body
    assert "shift_agent_commit_hash" in body
    assert "deploy_tag" not in body, "deploy_tag is mis-named; use shift_agent_deploy_tag"
    assert "commit_hash" not in body, "commit_hash is mis-named; use shift_agent_commit_hash"
    provider_names = {p["name"] for p in body["providers"]}
    assert provider_names == {
        "openrouter_generation_vision",
        "source_edit_provider",
        "billing_checkout_provider",
    }


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
    provider_source = next(p for p in body["providers"] if p["name"] == "source_edit_provider")
    assert provider_or["key_present"] is True
    assert provider_or["key_source"] == "process_env"
    assert provider_source["key_present"] is False
    assert provider_source["model_config"]["source_edit_provider"] == "manual_review"


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


# ─── Source-edit provider severity matrix ────────────────────────────────


def test_source_edit_default_manual_review_is_yellow_not_red(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    assert source_p["severity"] == "yellow", "manual-review source edit must be degraded, not blocking"
    assert source_p["key_present"] is False
    assert source_p["key_source"] is None
    assert source_p["model_config"]["source_edit_provider"] == "manual_review"
    assert "manual review" in source_p["detail"].lower()


def test_explicit_openai_source_edit_placeholder_is_yellow(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-PLACEHOLDER")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "edit_image_model": "gpt-image-1",
        "edit_image_quality": "medium",
    })

    from app.routers import flyer

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    assert source_p["severity"] == "yellow"
    assert source_p["key_present"] is False
    assert source_p["model_config"]["source_edit_provider"] == "openai"


def test_explicit_openai_source_edit_present_is_green(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "edit_image_model": "gpt-image-1",
        "edit_image_quality": "medium",
    })

    from app.routers import flyer

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    assert source_p["severity"] == "green"
    assert source_p["key_present"] is True


def test_explicit_openai_source_edit_policy_present_is_green(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "source_edit_provider_policy": {
            "default": {
                "provider": "openai",
                "model": "gpt-image-1",
                "quality": "high",
            },
            "emergency_fallback": {
                "provider": "manual_review",
                "model": "manual_review",
                "quality": "high",
            },
        },
    })

    from app.routers import flyer

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    assert source_p["severity"] == "green"
    assert source_p["key_present"] is True
    assert source_p["key_source"] == "process_env"
    assert source_p["model_config"]["source_edit_provider"] == "openai"


def test_explicit_openrouter_source_edit_present_is_green(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-real-openrouter-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "source_edit_provider_policy": {
            "default": {
                "provider": "openrouter",
                "model": "openai/gpt-5.4-image-2",
                "quality": "high",
            },
            "emergency_fallback": {
                "provider": "manual_review",
                "model": "manual_review",
                "quality": "high",
            },
        },
    })

    from app.routers import flyer

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    assert source_p["severity"] == "green"
    assert source_p["key_present"] is True
    assert source_p["key_source"] == "process_env"
    assert source_p["model_config"]["source_edit_provider"] == "openrouter"


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
    monkeypatch.setenv("SHIFT_AGENT_DEPLOY_HASH_PATH", str(hash_path))
    monkeypatch.setenv("SHIFT_AGENT_DEPLOYS_DIR", str(deploys_dir))

    from app.routers import flyer

    deploy_tag, commit_hash = flyer._shift_agent_deploy_tag()
    assert deploy_tag == "deploy-20260520-000424-a0e853e7"
    assert commit_hash == "a0e853e7abcdef1234567890"


def test_deploy_tag_null_when_markers_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFT_AGENT_DEPLOY_HASH_PATH", str(tmp_path / "no-hash"))
    monkeypatch.setenv("SHIFT_AGENT_DEPLOYS_DIR", str(tmp_path / "no-deploys"))

    from app.routers import flyer

    deploy_tag, commit_hash = flyer._shift_agent_deploy_tag()
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
    source_p = next(p for p in providers if p["name"] == "source_edit_provider")
    assert "draft_image_model" in or_p["model_config"]
    assert "final_image_model" in or_p["model_config"]
    assert or_p["model_config"]["draft_provider_model"] == "deterministic-renderer"
    assert or_p["model_config"]["final_provider_model"] == "deterministic-renderer"
    assert "edit_image_model" in source_p["model_config"]
    assert source_p["model_config"]["source_edit_provider"] == "manual_review"


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
    assert impact["queued_count"] == 0
    assert impact["oldest_age_hours"] is None
    assert impact["oldest_age_minutes"] is None
    assert impact["all_queued_count"] == 0
    assert impact["all_oldest_age_hours"] is None
    assert impact["all_oldest_age_minutes"] is None
    assert impact["reason_counts"] == {}
    assert impact["stale_reason_counts"] == {}
    assert impact["oldest_age_minutes_by_reason"] == {}
    assert impact["stale_count"] == 0
    assert impact["stale_minutes_threshold"] >= 5
    assert impact["source_edit_stale_count"] == 0
    assert impact["source_edit_oldest_stale_minutes"] is None


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
                _manual_edit_project("F0063", reason_code="visual_qa_failed", queued_at=queued_new),
            ],
        },
    )

    impact = flyer._source_edit_manual_queue_impact()
    assert impact["queued_count"] == 2
    assert impact["oldest_age_hours"] is not None and impact["oldest_age_hours"] >= 4
    assert impact["oldest_age_minutes"] is not None and impact["oldest_age_minutes"] >= 300
    assert impact["all_queued_count"] == 3
    assert impact["all_oldest_age_minutes"] is not None and impact["all_oldest_age_minutes"] >= 300
    assert impact["reason_counts"] == {
        "source_edit_provider_unavailable": 2,
        "visual_qa_failed": 1,
    }
    assert impact["oldest_age_minutes_by_reason"]["source_edit_provider_unavailable"] >= 300
    assert impact["oldest_age_minutes_by_reason"]["visual_qa_failed"] >= 60
    assert impact["source_edit_oldest_stale_minutes"] is not None
    assert impact["source_edit_oldest_stale_minutes"] >= 300
    assert impact["source_edit_stale_count"] >= 1


def test_manual_queue_impact_reports_stale_reason_counts(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    stale_old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    stale_mid = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=8)).isoformat()
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 5,
            "projects": [
                _manual_edit_project("F0060", reason_code="source_edit_provider_unavailable", queued_at=stale_old),
                _manual_edit_project("F0061", reason_code="visual_qa_failed", queued_at=stale_mid),
                _manual_edit_project("F0062", reason_code="visual_qa_failed", queued_at=fresh),
            ],
        },
    )

    impact = flyer._source_edit_manual_queue_impact()
    assert impact["stale_reason_counts"] == {
        "source_edit_provider_unavailable": 1,
        "visual_qa_failed": 1,
    }


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

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    assert source_p["manual_queue_impact"]["queued_count"] == 1
    assert "manual review" in source_p["detail"]
    assert "stale threshold" in source_p["detail"].lower()
    assert source_p["severity"] == "yellow"


def test_source_edit_detail_mentions_mixed_reason_backlog(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)

    from app.routers import flyer

    settings = flyer.get_settings()
    settings.state_dir = tmp_path / "state"
    queued_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _write_json(
        settings.state_dir / "flyer" / "projects.json",
        {
            "schema_version": 1,
            "next_sequence": 3,
            "projects": [
                _manual_edit_project("F0060", reason_code="visual_qa_failed", queued_at=queued_at),
                _manual_edit_project("F0061", reason_code="source_edit_provider_unavailable", queued_at=queued_at),
            ],
        },
    )

    source_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "source_edit_provider")
    detail = source_p["detail"].lower()
    assert "all manual queue blockers" in detail
    assert "source_edit_provider_unavailable=1" in detail
    assert "visual_qa_failed=1" in detail


def test_billing_checkout_provider_missing_templates_is_red(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "payment_provider": "stripe",
        "payment_checkout_url_template": "",
        "quick_flyer_checkout_url_template": "",
        "quick_flyer_price_cents": 4999,
    })

    from app.routers import flyer

    billing_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "billing_checkout_provider")
    assert billing_p["severity"] == "red"
    assert "missing" in billing_p["detail"].lower()
    assert billing_p["model_config"]["payment_provider"] == "stripe"
    assert billing_p["model_config"]["quick_flyer_price_cents"] == "4999"
    assert billing_p["model_config"]["payment_checkout_url_template_configured"] == "false"
    assert billing_p["model_config"]["quick_flyer_checkout_url_template_configured"] == "false"


def test_billing_checkout_provider_partial_template_is_yellow(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "payment_provider": "razorpay",
        "payment_checkout_url_template": "https://pay.example/sub/{customer_id}",
        "quick_flyer_checkout_url_template": "",
        "quick_flyer_price_cents": 6999,
    })

    from app.routers import flyer

    billing_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "billing_checkout_provider")
    assert billing_p["severity"] == "yellow"
    assert "partially configured" in billing_p["detail"].lower()
    assert billing_p["model_config"]["payment_provider"] == "razorpay"
    assert billing_p["model_config"]["payment_checkout_url_template_configured"] == "true"
    assert billing_p["model_config"]["quick_flyer_checkout_url_template_configured"] == "false"


def test_billing_checkout_provider_full_templates_is_green(tmp_path, monkeypatch):
    _clear_provider_env(monkeypatch)
    _isolate_env_files(monkeypatch, tmp_path)
    _isolate_deploy_markers(monkeypatch, tmp_path)
    _mock_flyer_config(monkeypatch, {
        "enabled": True,
        "payment_provider": "stripe",
        "payment_checkout_url_template": "https://pay.example/sub/{customer_id}",
        "quick_flyer_checkout_url_template": "https://pay.example/quick/{order_id}",
        "quick_flyer_price_cents": 19900,
    })

    from app.routers import flyer

    billing_p = next(p for p in flyer._flyer_provider_components() if p["name"] == "billing_checkout_provider")
    assert billing_p["severity"] == "green"
    assert "configured" in billing_p["detail"].lower()
    assert billing_p["model_config"]["payment_provider"] == "stripe"
    assert billing_p["model_config"]["payment_checkout_url_template_configured"] == "true"
    assert billing_p["model_config"]["quick_flyer_checkout_url_template_configured"] == "true"
