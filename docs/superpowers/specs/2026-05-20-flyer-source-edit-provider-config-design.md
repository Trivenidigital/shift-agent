**Drift-check tag:** extends-Hermes

# Flyer Source-Edit Provider Config Design

Date: 2026-05-20
Plan: `docs/superpowers/plans/2026-05-20-flyer-source-edit-provider-config.md`

## Goal

Wire exact uploaded-flyer source edits through Flyer provider configuration so OpenRouter can be used with `OPENROUTER_API_KEY` and no separate `OPENAI_API_KEY`. This PR is no-merge/no-deploy and does not make a customer-grade fidelity claim until spend-gated real source-edit smoke runs.

## New primitives introduced

- `FlyerSourceEditProviderPolicy`
- `FlyerConfig.resolve_source_edit_render_provider()`
- OpenRouter source-edit image request helper

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Source media ingress | Yes - Hermes WhatsApp bridge and Flyer `reference_image` assets already retain uploaded source flyers | Reuse assets; no new media path |
| Credential lookup | Yes - Hermes env and agent env are existing operator credential stores | Align render lookup with workflow lookup |
| Image provider route | Partial - Hermes/OpenRouter substrate exists, but no Flyer-specific source-preserving edit policy exists | Add Flyer-side dispatch only |
| Manual fallback / audit | Yes - Flyer manual-review state and reason taxonomy already exist | Preserve existing `source_edit_provider_unavailable` behavior |

Awesome Hermes ecosystem check: image generation skills and OpenRouter integrations exist, but none own Flyer source-contract checks, project state, or manual-review triage. Build the small Flyer dispatch layer on top of Hermes substrate.

## Source-Edit Provider Resolution

Add:

```python
class FlyerSourceEditProviderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="openrouter",
        model="openai/gpt-5.4-image-2",
        quality="high",
    ))
    emergency_fallback: FlyerRenderProviderConfig = Field(default_factory=lambda: FlyerRenderProviderConfig(
        provider="manual_review",
        model="manual_review",
        quality="high",
    ))
```

Add `source_edit_provider_policy: FlyerSourceEditProviderPolicy = Field(default_factory=...)` to `FlyerConfig`.

Add resolver:

```python
def resolve_source_edit_render_provider(self) -> FlyerRenderProviderConfig:
    if "source_edit_provider_policy" in self.model_fields_set:
        return self.source_edit_provider_policy.default
    if {"edit_image_model", "edit_image_quality"} & self.model_fields_set:
        return FlyerRenderProviderConfig(
            provider="openai",
            model=self.edit_image_model,
            quality=self.edit_image_quality,
        )
    return self.source_edit_provider_policy.emergency_fallback
```

Rationale:

- Fresh configs with no explicit source-edit policy fail closed to manual review; OpenRouter is active only when `source_edit_provider_policy` is present in config.
- Existing configs that explicitly set legacy edit fields retain direct OpenAI.
- Explicit policy always wins.
- `manual_review` is the default no-policy sentinel; render dispatch must fail closed if selected.

## Config Loading In Router Preflight

`actions.flyer_source_edit_preflight(project)` will resolve provider before calling workflow readiness.

Implementation shape:

```python
def _resolve_flyer_source_edit_provider_for_preflight():
    _ensure_platform_path()
    from schemas import Config
    from safe_io import load_yaml_model
    cfg = load_yaml_model(CONFIG_PATH, Config)
    return cfg.flyer.resolve_source_edit_render_provider()
```

Failure policy:

- Import order is production platform path first, local repo `src` fallback second, matching existing `source_edit_provider_ready` import behavior.
- If imports fail, keep current `source edit readiness helper unavailable...` failure.
- If `config.yaml` read/parse/schema validation fails, return `False, "source edit provider config unavailable: ...", "source_edit_provider_unavailable"`.
- Do not fall back to schema defaults after production config load failure.

Tests must patch `CONFIG_PATH` to an explicit minimal valid `Config` fixture for every non-config-error preflight case. No production mutation.

## Readiness Helper

Current `source_edit_provider_ready(project_or_asset, env_path=None)` remains callable, but adds an optional `provider` argument:

```python
def source_edit_provider_ready(project_or_asset, *, provider=None, env_path=None) -> tuple[bool, str]:
```

Provider normalization accepts:

- `FlyerRenderProviderConfig`-like object
- dict with `provider` and `model`
- string provider name
- `None`, defaulting to `manual_review` so old callers cannot activate provider traffic without an explicit policy-derived provider

Credential mapping:

| Provider | Required env |
|---|---|
| `openrouter` | `OPENROUTER_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `manual_review` | fail closed with `source edit provider configured for manual review` |
| any other | fail closed with `source edit provider is unsupported: <provider>` |

Credential check treats empty or containing `PLACEHOLDER` as missing. Reference image and MIME validation remain after provider readiness. This intentionally preserves today's fail-closed precedence: if provider credentials are missing, the returned reason is provider-unavailable even if the reference also has an issue. Tests with a valid provider key still verify PDF/no-reference/missing-file reason-code fidelity.

Success detail:

`source edit provider configured: <provider>/<model>`

## Env Lookup Parity

Update `render._read_env_value(name)` to match workflow:

1. process env
2. `HERMES_ENV_PATH` or `/root/.hermes/.env`
3. `SHIFT_AGENT_ENV_PATH` or `/opt/shift-agent/.env`

This prevents preflight passing from Hermes env while render fails from checking only agent env.

## Renderer Dispatch

Change signature compatibly:

```python
def render_source_edit_preview(project, output_dir, *, model, quality="medium", provider: str | None = None):
```

Dispatch:

- `provider is None` -> manual-review failure. Callers must pass the resolved provider from config; direct OpenAI requires explicit `provider="openai"`.
- `provider == "openrouter"` -> `_openrouter_source_edit_bytes(...)`
- `provider == "openai"` -> `_openai_source_edit_bytes(...)`
- `provider == "manual_review"` -> `FlyerRenderError("source edit provider configured for manual review")`
- else -> `FlyerRenderError("unsupported source edit provider: ...")`

OpenRouter request:

- URL: existing `OPENROUTER_IMAGE_URL`
- auth: `OPENROUTER_API_KEY`
- method: POST JSON
- body:

```json
{
  "model": "<configured model>",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "<_source_edit_prompt(project)>"},
      {"type": "image_url", "image_url": {"url": "data:<mime>;base64,<reference bytes>"}}
    ]
  }],
  "modalities": ["image", "text"],
  "stream": false,
  "image_config": {
    "aspect_ratio": "4:5",
    "image_size": "2K" if quality == "high" else "1K"
  }
}
```

Response handling mirrors `_openrouter_image_bytes(...)`:

- `choices[0].message.images[0].image_url.url` must be a `data:image/` URL.
- Decode and return bytes.
- Remote URL-only response fails closed. Do not fetch arbitrary remote image URLs in this PR.
- HTTP/URL/timeout/incomplete-read errors become `FlyerRenderError`.
- Invalid JSON/shape becomes `FlyerRenderError`.

Existing `_openai_source_edit_bytes(...)` remains for explicit direct OpenAI.

## Generation Script

In source-edit branch of `generate-flyer-concepts`:

```python
source_edit_provider = cfg.flyer.resolve_source_edit_render_provider()
specs = [render_source_edit_preview(
    project,
    asset_dir,
    provider=source_edit_provider.provider,
    model=source_edit_provider.model,
    quality=source_edit_provider.quality,
)]
```

Error classifier should treat `openrouter`, `api_key`, `provider`, and `manual review` as `source_edit_provider_unavailable`; quality failures remain `visual_qa_failed`; other transient provider failures remain `provider_timeout`.

## Tests

Write tests first and run a red pass before implementation.

Schema:

- Defaults include source-edit policy default `openrouter/openai/gpt-5.4-image-2`.
- Resolver returns explicit policy when present.
- Resolver preserves direct OpenAI when legacy `edit_image_model` is explicitly present and policy is absent.
- Existing draft/final resolver tests remain unchanged.

Workflow:

- OpenRouter key present and OpenAI absent passes readiness.
- Missing/placeholder OpenRouter key fails with `OPENROUTER_API_KEY missing`.
- Explicit direct OpenAI provider still requires `OPENAI_API_KEY`.
- Env lookup checks Hermes env before shift-agent env.

Router preflight:

- Temp config with OpenRouter source policy + `OPENROUTER_API_KEY` + no `OPENAI_API_KEY` passes.
- Missing/placeholder OpenRouter key maps to `source_edit_provider_unavailable`.
- Malformed/missing config fails closed with `source_edit_provider_unavailable`.
- Existing PDF/no-reference/missing-file reason mapping still holds when the configured provider key is valid.

Renderer:

- OpenRouter source edit request includes configured model, prompt, source image data URL, `modalities`, and no OpenAI auth.
- OpenRouter success data URL writes preview and source-edit integrity manifest.
- OpenRouter HTTP/URL/invalid JSON/no images/remote URL-only fail as `FlyerRenderError`.
- Explicit OpenAI provider still sends the current multipart OpenAI request.
- Placeholder OpenRouter key does not call network.

Generation / static:

- `generate-flyer-concepts` source-edit branch calls `resolve_source_edit_render_provider()`.
- Focused source-edit generation test or static assertion verifies provider is passed to `render_source_edit_preview`.
- Existing hook preflight blocks still thread dynamic reason code and guard ack on queue success.

Scope guards:

- `git diff --name-only` must not include `web/`.
- No customer copy helper bodies should change.
- Draft/final provider resolver tests remain in the focused run.

Operator posture cleanup:

- `src/platform/credential_readiness.py` should no longer describe `OPENAI_API_KEY` as the only Flyer source-edit gate; it should mention direct-OpenAI source-edit fallback only.
- `smoke-flyer-quality` real-model posture should expose `source_edit_provider` and the credential posture for the resolved provider without requiring OpenAI when source-edit policy is OpenRouter.
- Existing real-model posture tests should assert the new provider-key posture and still never expose secret values.
- Dashboard backend is out of scope unless a tiny stale health field is touched. Do not modify `web/` in this PR unless implementation discovers a failing backend-only test that directly references stale source-edit provider posture.

## Verification Commands

```powershell
python -m pytest tests/test_flyer_source_edit_preflight.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_flyer_schemas.py -q
python -m pytest tests/ -k "flyer and source_edit" -q
python -m pytest tests/test_flyer_generate_concepts.py tests/test_cf_router_flyer_routing.py -k "source_edit or preflight" -q
python -m py_compile src/platform/schemas.py src/agents/flyer/workflow.py src/agents/flyer/render.py src/plugins/cf-router/actions.py
python -m py_compile src/agents/flyer/scripts/generate-flyer-concepts
git diff --check
```

## PR Notes

PR body must include:

- Files changed.
- Tests run.
- Risks.
- Deferred items.
- `No deploy performed`.
- Customer-grade source-edit still requires spend-gated real OpenRouter smoke on VPS before relying on it operationally.
