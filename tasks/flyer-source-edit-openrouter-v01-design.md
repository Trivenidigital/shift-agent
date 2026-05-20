# Flyer Source-Edit OpenRouter v0.1 — design

**Drift-check tag:** extends-Hermes

**Plan reference:** `tasks/flyer-source-edit-openrouter-v01-plan.md` (commit `d71f197`).

**New primitives introduced:** `_openrouter_source_edit_bytes` (replaces `_openai_source_edit_bytes`); `_resolve_source_edit_model` precedence helper; `_classify_openrouter_no_images` refusal-vs-malformed disambiguator; preflight env-key swap to `OPENROUTER_API_KEY`; `FlyerStudioConfig.edit_image_model` default value flip from `"gpt-image-1"` to `"google/gemini-2.5-flash-image-preview"`. **No** new schema fields (default value only), **no** new state, **no** new audit variant, **no** new customer-copy string.

## Hermes-first capability checklist

Receipt: `tasks/.hermes-check-receipts/flyer-source-edit-openrouter-v01.json`. 2 of 11 steps net-new (18%, well under the 50% red-flag threshold).

| # | Implementation step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | WhatsApp inbound + media cache | `[Hermes]` — cf-router + gateway |
| 2 | Scope intercept + SOURCE/NEW routing | `[Hermes]` — PR #137 substrate, not touched here |
| 3 | Quota reserve + project creation | `[Hermes]` — existing |
| 4 | Preflight env-key swap (OPENAI → OPENROUTER) | `[net-new]` ~10 LOC + ~30 LOC tests |
| 5 | Manual-queue fallback on provider-unavailable | `[Hermes]` — PR #137 substrate (behavior preserved) |
| 6 | Processing ack + concept-generation trigger | `[Hermes]` — existing |
| 7 | OpenRouter source-edit POST request + response parse | `[net-new]` ~80 LOC (request shape mirrors `_openrouter_image_bytes` but adds reference-image attachment via multimodal `content` list) |
| 8 | Error taxonomy: retriable HTTP, refusal disambiguation, malformed response → `FlyerRenderError` | `[net-new]` ~30 LOC (overlaps with #7) + ~120 LOC tests |
| 9 | Model resolution precedence (env > caller arg with slug check > default) | `[net-new]` ~15 LOC + ~30 LOC tests |
| 10 | Schema default flip `edit_image_model` → Gemini slug | `[net-new]` 1 LOC + ~5 LOC tests |
| 11 | Visual QA, text manifest, customer preview send | `[Hermes]` — PR #137 substrate, not touched |

Awesome-Hermes-Agent ecosystem check: no installable skill provides reference-image-conditioned image generation; building on the existing `_openrouter_image_bytes` pattern is the right shape.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/render.py:1234-1288` (`_openrouter_image_bytes`) — exact request/response shape to mirror, including 3-retry policy and `images[0].image_url.url` data-URL parse.
- ✅ Read `src/agents/flyer/render.py:1224-1232` (`_decode_data_url`) — reuse unchanged.
- ✅ Read `src/agents/flyer/render.py:1359-1423` (`_openai_source_edit_bytes`) — function being replaced; preserve `(project, *, size, model, quality) -> bytes` signature.
- ✅ Read `src/agents/flyer/render.py:1291-1320` (`_source_edit_reference_asset`, `_source_edit_prompt`) — reused unchanged.
- ✅ Read `src/agents/flyer/render.py:1795` — confirmed sole caller of `_openai_source_edit_bytes` (`render_source_edit_preview`). One swap point.
- ✅ Read `src/agents/flyer/workflow.py:259-316` (`_read_env_value`, `source_edit_provider_ready`) — **2-tuple return shape** `tuple[bool, str]` confirmed; env-store reader already searches `/root/.hermes/.env` then `/opt/shift-agent/.env` (parity with reference_extract).
- ✅ Read `src/plugins/cf-router/actions.py:2071-2141` (`flyer_source_edit_preflight`) — **3-tuple wrapper** confirmed; reason-code mapping at `:2137-2141` falls through to `"source_edit_provider_unavailable"` when detail doesn't match `"uploaded reference image"` or `"must be an image"` (the new OpenRouter detail string contains neither, so mapping is preserved by exhaustion).
- ✅ Read `src/agents/flyer/reference_extract.py` — `OPENROUTER_API_KEY` env-key name + dual env-file search pattern; matches what this PR adds.
- ✅ Read `src/platform/schemas.py:780-790` (`FlyerStudioConfig`) — `edit_image_model: str = Field(default="gpt-image-1", min_length=1, max_length=120)`; field shape stays the same, only the default literal flips.
- ✅ Read `src/agents/flyer/scripts/generate-flyer-concepts:239` — caller passes `model=cfg.flyer.edit_image_model` into `render_source_edit_preview`; the legacy-sentinel substitution at the helper is required because a stale operator config can keep `"gpt-image-1"` even after schema default flips.
- ✅ Read `tests/test_flyer_source_edit_preflight.py` — pattern for monkeypatching `_read_env_value`.
- ✅ Read `tests/test_flyer_renderer.py` — pattern for mocking `urllib.request.urlopen` with `FakeResponse`.
- ✅ Read `tests/test_flyer_generate_concepts.py:272` — `render_source_edit_preview` raising `FlyerRenderError` is caught by `generate-flyer-concepts`; the chain to `hooks.py:1058` (`gen_ok=False`) and `--queue-manual-review` is intact.

## Schema details — final shape

### `FlyerStudioConfig.edit_image_model` (modify default at `schemas.py:784`)

One-line change. Field shape unchanged. Operator-supplied values in `config.yaml` continue to win because Pydantic only uses `default=` when the key is absent.

```diff
- edit_image_model: str = Field(default="gpt-image-1", min_length=1, max_length=120)
+ edit_image_model: str = Field(default="google/gemini-2.5-flash-image-preview", min_length=1, max_length=120)
```

Backward compatibility: existing customer VPSes with `flyer.edit_image_model: "gpt-image-1"` explicitly set in `config.yaml` will be overridden by the legacy-sentinel substitution in `_resolve_source_edit_model` (defense-in-depth). New deploys with no override pick up the new default cleanly.

## Module constants (modify `render.py`)

Add near the existing `OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/chat/completions"` constant block:

```python
FLYER_SOURCE_EDIT_DEFAULT_MODEL = "google/gemini-2.5-flash-image-preview"
OPENAI_LEGACY_MODEL_SENTINELS = frozenset({"gpt-image-1", "dall-e-2", "dall-e-3"})
RETRIABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})
```

`frozenset` is intentional — immutable, hashable, and signals "these are policy-fixed values; mutating at runtime is wrong."

## Model resolution — explicit, boring, three branches

Per operator emphasis: **"FLYER_SOURCE_EDIT_MODEL wins, then caller arg only if it is already an OpenRouter slug and not a legacy OpenAI sentinel, then the hard default."**

OpenRouter slug convention: namespace-prefixed (`provider/model[:variant]`). Anything without a `/` is not an OpenRouter model id.

```python
def _resolve_source_edit_model(caller_model: str) -> str:
    """Resolve the effective OpenRouter source-edit model id.

    Precedence (boring, no clever fallbacks):
      1. `FLYER_SOURCE_EDIT_MODEL` env — operator override; wins unconditionally
         when set and non-empty.
      2. `caller_model` arg — ACCEPTED ONLY IF it looks like an OpenRouter slug
         (contains "/") AND is not in `OPENAI_LEGACY_MODEL_SENTINELS`. This is
         tighter than "not a legacy sentinel" because a future caller could
         pass a bare model name like "gpt-4o" or "claude-3-opus" that isn't
         in the sentinel list but still isn't a valid OpenRouter id.
      3. Hard default `FLYER_SOURCE_EDIT_DEFAULT_MODEL`.

    The schema default at `schemas.py:784` is also flipped to the same Gemini
    slug in this PR, so under normal config the caller_model already IS the
    slug and step 2 returns it. Steps 1 and 3 are belt-and-braces for env
    override / stale-config substitution respectively.
    """
    env_override = os.environ.get("FLYER_SOURCE_EDIT_MODEL", "").strip()
    if env_override:
        return env_override
    if caller_model and "/" in caller_model and caller_model not in OPENAI_LEGACY_MODEL_SENTINELS:
        return caller_model
    return FLYER_SOURCE_EDIT_DEFAULT_MODEL
```

### Test surface for model resolution (each branch separately)

Per operator: "tests should cover all three branches plus sentinel substitution."

| Test | env | caller arg | Expected effective model | Branch hit |
|---|---|---|---|---|
| `test_model_resolution_env_wins` | `"custom/model-x"` | `"google/gemini-2.5-flash-image-preview"` | `"custom/model-x"` | 1 |
| `test_model_resolution_caller_slug_used_when_no_env` | unset | `"anthropic/claude-3-opus"` | `"anthropic/claude-3-opus"` | 2 |
| `test_model_resolution_default_when_caller_lacks_slash` | unset | `"gpt-4o"` | default (Gemini) | 3 (no `/`) |
| `test_model_resolution_legacy_sentinel_substituted` | unset | `"gpt-image-1"` | default (Gemini) | 3 (sentinel hit) |
| `test_model_resolution_legacy_dall_e_2_substituted` | unset | `"dall-e-2"` | default (Gemini) | 3 (sentinel hit) |
| `test_model_resolution_legacy_dall_e_3_substituted` | unset | `"dall-e-3"` | default (Gemini) | 3 (sentinel hit) |
| `test_model_resolution_empty_caller_uses_default` | unset | `""` | default (Gemini) | 3 (empty arg) |
| `test_model_resolution_env_overrides_legacy_caller` | `"custom/model-x"` | `"gpt-image-1"` | `"custom/model-x"` | 1 (env still wins) |

8 tests; one per branch + edge cases. Parametrize where possible.

## Refusal-vs-malformed disambiguation

OpenRouter 200-OK with no image can mean two distinct things:
- **Content policy refusal**: model declined to edit (e.g., copyrighted content, NSFW concern). `choices[0].finish_reason` is `"content_filter"` or `"safety"`; `message.content` is non-empty text explaining the refusal; `images` is absent or empty.
- **Malformed response / model error**: response shape unexpected, model returned text instead of image due to bug/regression.

Operator triage in audit logs needs to disambiguate. Distinct `FlyerRenderError` message strings give them a greppable handle.

```python
def _classify_openrouter_no_images(body: str, doc: dict) -> str:
    """Pick the most informative FlyerRenderError message for a 200-OK
    response that came back without an image.

    Heuristic order:
      1. `finish_reason in {content_filter, safety}` → distinct refusal message.
      2. `message.content` is non-empty (text response with no image) → also
         treat as refusal (older models don't always set finish_reason).
      3. Generic "no images" message — model error or response shape drift.

    Both refusal cases route to manual-queue through the same FlyerRenderError
    path; only the human-readable message differs.
    """
    choices = doc.get("choices") or [{}]
    first = choices[0] if choices else {}
    finish_reason = str(first.get("finish_reason") or "").lower()
    content_text = ""
    message = first.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        content_text = content.strip()
    elif isinstance(content, list):
        # Multimodal content shape; join text parts.
        content_text = " ".join(
            str(part.get("text", "")) for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    if finish_reason in {"content_filter", "safety"} or content_text:
        return f"OpenRouter source-edit refused (likely content policy): {body[:500]}"
    return f"OpenRouter source-edit response had no images: {body[:500]}"
```

## Helper shape — final

```python
def _openrouter_source_edit_bytes(
    project: FlyerProject,
    *,
    size: tuple[int, int] | None,
    model: str,
    quality: str,
) -> bytes:
    api_key = _read_env_value("OPENROUTER_API_KEY")
    if not api_key or "PLACEHOLDER" in api_key:
        raise FlyerRenderError("OPENROUTER_API_KEY is missing or placeholder")
    effective_model = _resolve_source_edit_model(model)
    reference = _source_edit_reference_asset(project)
    reference_path = Path(reference.path)
    mime = reference.mime_type or mimetypes.guess_type(str(reference_path))[0] or "image/png"
    source_b64 = base64.b64encode(reference_path.read_bytes()).decode("ascii")
    source_data_url = f"data:{mime};base64,{source_b64}"
    payload = {
        "model": effective_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _source_edit_prompt(project)},
                {"type": "image_url", "image_url": {"url": source_data_url}},
            ],
        }],
        "modalities": ["image", "text"],
        "stream": False,
        "image_config": {
            "aspect_ratio": _aspect_ratio(size),
            "image_size": "2K" if quality == "high" else "1K",
        },
    }
    req = urllib.request.Request(
        OPENROUTER_IMAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Trivenidigital/SME-Agents",
            "X-Title": "Hermes Flyer Studio",
        },
        method="POST",
    )
    body = ""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:1000]
            if e.code in RETRIABLE_HTTP_STATUSES and attempt < 2:
                last_error = e
                time.sleep(2 * (attempt + 1))
                continue
            raise FlyerRenderError(f"OpenRouter source-edit HTTP {e.code}: {err_body}") from e
        except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError) as e:
            last_error = e
            if attempt == 2:
                raise FlyerRenderError(f"OpenRouter source-edit response failed: {type(e).__name__}: {e}") from e
            time.sleep(2 * (attempt + 1))
    if not body and last_error is not None:
        raise FlyerRenderError(f"OpenRouter source-edit response failed: {type(last_error).__name__}: {last_error}") from last_error
    doc = json.loads(body)
    choices = doc.get("choices") or []
    if not choices:
        raise FlyerRenderError(f"OpenRouter source-edit response had no choices: {body[:500]}")
    images = choices[0].get("message", {}).get("images") or []
    if not images:
        raise FlyerRenderError(_classify_openrouter_no_images(body, doc))
    url = images[0].get("image_url", {}).get("url") or ""
    if not url.startswith("data:image/"):
        raise FlyerRenderError("OpenRouter source-edit response did not include base64 image data")
    return _decode_data_url(url)
```

## `workflow.py:source_edit_provider_ready` — 2-tuple unchanged

```diff
 def source_edit_provider_ready(project_or_asset, *, env_path: Path | None = None) -> tuple[bool, str]:
-    key = _read_env_value("OPENAI_API_KEY", env_path=env_path)
+    key = _read_env_value("OPENROUTER_API_KEY", env_path=env_path)
     if not key or "PLACEHOLDER" in key:
-        return False, "source edit provider is not configured: OPENAI_API_KEY missing"
+        return False, "source edit provider is not configured: OPENROUTER_API_KEY missing"
     # ... rest unchanged
```

Return shape `tuple[bool, str]` preserved. The cf-router wrapper at `actions.py:2071+` (`flyer_source_edit_preflight`) keeps its 3-tuple return; its reason-code mapping at `actions.py:2137-2141` falls through to `"source_edit_provider_unavailable"` for the new detail string by exhaustion (the new detail contains neither `"uploaded reference image"` nor `"must be an image"`).

## Caller swap at `render.py:1795`

```diff
-    raw = _openai_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)
+    raw = _openrouter_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)
```

`render_source_edit_preview` does not need any other change. The `model` arg passes through verbatim; precedence resolution happens inside the helper.

## Deletions (after green tests)

- `_openai_source_edit_bytes` (entire function, render.py:1359-1423)
- `_openai_edit_size` (helper only used by the deleted function)
- `_multipart_form_data` (helper only used by the deleted function)
- `OPENAI_IMAGE_EDIT_URL` constant
- `OPENAI_IMAGE_EDIT_TIMEOUT_SEC` constant

Verification: `grep -rn "_openai_source_edit_bytes\|_openai_edit_size\|_multipart_form_data\|OPENAI_IMAGE_EDIT_URL\|OPENAI_IMAGE_EDIT_TIMEOUT_SEC" src/` → must be zero hits before commit.

## Error taxonomy — final table

All routes terminate in the existing `MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"]` customer-facing ack. **No new customer copy.**

| Failure mode | Detection | Distinct `FlyerRenderError` message | Customer-visible result |
|---|---|---|---|
| Key missing/placeholder | `not api_key or "PLACEHOLDER" in api_key` | "OPENROUTER_API_KEY is missing or placeholder" | Preflight short-circuits; manual-queue ack |
| Non-retriable HTTP (4xx-non-429) | `e.code not in RETRIABLE_HTTP_STATUSES` | "OpenRouter source-edit HTTP {code}: {body[:1000]}" | Manual queue |
| Retriable HTTP (429/502/503/504) | `e.code in RETRIABLE_HTTP_STATUSES`, exhausts 3 attempts | "OpenRouter source-edit HTTP {code}: {body[:1000]}" | Manual queue |
| Transport timeout / URLError / IncompleteRead | exhausts 3 retries | "OpenRouter source-edit response failed: {Type}: {e}" | Manual queue |
| Empty `choices` | `not choices` | "OpenRouter source-edit response had no choices: {body[:500]}" | Manual queue |
| Refusal — content_filter / safety | `finish_reason in {content_filter, safety}` | "OpenRouter source-edit refused (likely content policy): {body[:500]}" | Manual queue |
| Refusal — text-only response | `message.content` non-empty + `images` empty | "OpenRouter source-edit refused (likely content policy): {body[:500]}" | Manual queue |
| Malformed — no images, no refusal hint | `not images` + no `finish_reason` + no `content` | "OpenRouter source-edit response had no images: {body[:500]}" | Manual queue |
| Bad data URL | not startswith `data:image/` | "OpenRouter source-edit response did not include base64 image data" | Manual queue |
| Base64 decode fail | `_decode_data_url` raises | "image response base64 decode failed: {e}" (existing helper) | Manual queue |

Operator-greppable disambiguators:
- "refused (likely content policy)" — content/safety filter
- "HTTP {429,502,503,504}" — retriable failures (after 3 attempts)
- "HTTP {400,401,403,...}" — non-retriable
- "response had no choices" — provider-side bug
- "did not include base64 image data" — response shape drift

## Pre-merge model-slug check (REQUIRED, not optional)

Per operator: **pre-merge slug verification is required, not optional**. If `google/gemini-2.5-flash-image-preview` is not present in OpenRouter `/api/v1/models`, pause and choose a live slug rather than merge a known-400.

Reviewer-runnable command:

```bash
curl -s -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  https://openrouter.ai/api/v1/models \
  | jq -r '.data[].id' \
  | grep -E "gemini.*image"
```

Expected output: at least one line matching the slug we plan to use. If the slug returned differs (e.g., `google/gemini-2.0-flash-exp:image-generation`), the operator picks one of two acceptable resolutions BEFORE merge:

1. Update `FLYER_SOURCE_EDIT_DEFAULT_MODEL` constant in `render.py` AND `FlyerStudioConfig.edit_image_model` default in `schemas.py` to the live slug. Re-run tests. Re-merge.
2. Set `FLYER_SOURCE_EDIT_MODEL=<live-slug>` env on main-vps before deploy. Schema/constants unchanged. Env wins at runtime.

Both paths are acceptable; the operator picks one. **Merge is blocked until one of (1) or (2) is committed.** The plan-review-pass-2 reviewer should verify this checkbox is unchecked at PR-open time and require it before approving.

## Test surface — final shape

### `tests/test_flyer_source_edit_preflight.py` — extend

**2-tuple layer (`workflow.py:source_edit_provider_ready`):**
- `OPENROUTER_API_KEY=""` (and `OPENAI_API_KEY=valid` to prove non-influence) → returns `(False, "source edit provider is not configured: OPENROUTER_API_KEY missing")`.
- `OPENROUTER_API_KEY="PLACEHOLDER_xxxx"` → returns `(False, ...)`.
- `OPENROUTER_API_KEY="sk-or-v1-..."` + valid image reference → returns `(True, "ready")`.
- Dual env-file search: writes `OPENROUTER_API_KEY=K1` to `/root/.hermes/.env` shim, leaves shift-agent .env empty → key resolved.
- Conversely: leaves Hermes .env empty, writes key to shift-agent .env → key resolved.

**3-tuple wrapper layer (`actions.py:flyer_source_edit_preflight`):**
- 2-tuple `(False, "...OPENROUTER_API_KEY missing")` from `source_edit_provider_ready` → 3-tuple `(False, detail, "source_edit_provider_unavailable")`. Pins the reason-code-by-exhaustion mapping.
- 2-tuple `(False, "source edit needs an uploaded reference image")` → 3-tuple with `"reference_unsupported"` (existing branch, unchanged).
- 2-tuple `(True, "ready")` → 3-tuple `(True, "ready", "")`.

### `tests/test_flyer_renderer.py` — extend

**Model resolution (8 tests per the table above):**
- env wins
- caller slug used when no env
- default when caller lacks `/`
- legacy `gpt-image-1` substituted
- legacy `dall-e-2` substituted
- legacy `dall-e-3` substituted
- empty caller arg → default
- env overrides legacy caller

**Schema default flip:**
- `FlyerStudioConfig().edit_image_model == "google/gemini-2.5-flash-image-preview"`. Pins the default change so a future schema-cleanup PR can't silently revert.

**Request shape:**
- Mock `urlopen`, capture payload. Assert `model`, `modalities=["image","text"]`, `messages[0].content` has both `type=text` and `type=image_url` with data-URL prefix matching the source flyer mime.
- `image_config.aspect_ratio` matches `_aspect_ratio(size)`.
- `image_config.image_size` is `"2K"` when `quality="high"` else `"1K"`.

**Response parsing — happy path:**
- Mock response with `choices[0].message.images[0].image_url.url="data:image/png;base64,..."` → `_openrouter_source_edit_bytes` returns decoded bytes.

**Error taxonomy (one test per row of the table above):**
- HTTP 400 → raises immediately on first attempt (mock confirms `urlopen` called exactly once).
- HTTP 429 → 3 attempts with backoff, then raises (mock confirms 3 calls + 2 sleeps).
- HTTP 502 → 3 attempts.
- HTTP 503 → 3 attempts.
- HTTP 504 → 3 attempts.
- HTTP 500 (non-retriable) → raises on first attempt (5xx that ISN'T in the retriable set).
- `TimeoutError` → 3 attempts.
- `URLError` → 3 attempts.
- Empty `choices` → distinct "no choices" message.
- `finish_reason="content_filter"` + no images → distinct "refused" message.
- `finish_reason="safety"` + no images → distinct "refused" message.
- text-only `message.content` + no images + no finish_reason → distinct "refused" message (text-only heuristic).
- `not images` + no finish_reason + no content → generic "had no images" message.
- Malformed data URL (no comma) → distinct message.
- Bad base64 → distinct message (via `_decode_data_url`).

**Static guard:**
- Inspect the source of EXACTLY these three functions (`workflow.py:source_edit_provider_ready`, `render.py:_openrouter_source_edit_bytes`, `render.py:render_source_edit_preview`) via AST or string-extraction → assert `"OPENAI_API_KEY"` substring is absent from each. Function-scoped, not repo-wide (out-of-scope readiness/smoke references remain per the deferred items).
- Assert `_openai_source_edit_bytes`, `_openai_edit_size`, `_multipart_form_data` are NOT attributes of the render module after the deletion.

## Fail modes / risks (mirrors plan §Risks)

See plan doc for full list. Design-time additions:

| Risk | Mitigation in design |
|---|---|
| Gemini regenerates instead of edits | Design preserves existing `_source_edit_prompt` (instructs "preserve original layout, colors, logo, food/product imagery, typography style, contact area"). If Gemini doesn't honor that, the kill-switch criterion (N≥3/5 regenerations → revert this PR) catches it in spend-gated smoke. |
| Model slug 400s at runtime | Pre-merge `/api/v1/models` check is REQUIRED. Merge blocked otherwise. |
| Content-policy refusal looks like malformed response | `_classify_openrouter_no_images` heuristic + distinct message string. |
| Transient 5xx blocks customer flow | `RETRIABLE_HTTP_STATUSES = {429, 502, 503, 504}` retries within 3-attempt budget. Other 5xx (e.g., 500) raise immediately — those usually mean provider-side bug, not transient. |
| Stale operator config keeps `"gpt-image-1"` | Legacy-sentinel substitution in `_resolve_source_edit_model` overrides at the helper before the OpenRouter call. |
| Caller passes bare model name (`"gpt-4o"`) instead of slug | Step 2 of resolution requires `/` in caller arg; falls through to default. |
| Schema default revert in a future cleanup PR | `test_schema_default_is_openrouter_gemini_slug` pins it. |
| Vision-client chokepoint debt grows | Acknowledged; v0.2+. Helper structured for easy absorption later. |

## Out of scope — documented deferred drift (NOT surprise edits in this PR)

Per operator: keep these as documented deferred drift, not surprise edits.

- `src/platform/credential_readiness.py:556` — still registers `OPENAI_API_KEY` as "Flyer Studio source-preserving image edit gate." Stale post-merge. Listed in plan §Deferred items #3.
- `src/agents/flyer/scripts/smoke-flyer-quality:154` — still reports `_key_posture("OPENAI_API_KEY")`. Stale post-merge. Listed in plan §Deferred items #4.
- `web/backend/` health endpoints — still surface OpenAI key posture. Listed in plan §Deferred items #5.
- `tasks/flyer-cockpit-p0-7-health-panel-plan.md` — co-resident plan assumes OpenAI is the source-edit provider. Will need rebase. Listed in plan §Deferred items #8.

The static guard test is **scoped to the three named functions** (`workflow.py:source_edit_provider_ready`, `render.py:_openrouter_source_edit_bytes`, `render.py:render_source_edit_preview`) precisely so it does NOT false-fail on those out-of-scope references.

## Build sequence (mirrors plan §Build sequence)

Two commits, ordered:

1. `test(flyer): pin source-edit OpenRouter contract + error taxonomy + model precedence` (~210 LOC tests, red against current code).
2. `fix(flyer): route source edits through OpenRouter (v0.1)` (~120 LOC code, makes the red tests green).

Schema default flip lands in commit 2 alongside the helper.

## Acceptance criteria

1. Customer SOURCE-edit reaches `_openrouter_source_edit_bytes`, generates image via Gemini, customer sees preview through unchanged visual_qa + customer-send pipeline.
2. Missing/PLACEHOLDER `OPENROUTER_API_KEY` → manual-queue ack (existing copy).
3. Provider error (HTTP / timeout / refusal / malformed) → `FlyerRenderError` → manual-queue ack (existing copy). No customer-visible new strings.
4. Zero `OPENAI_API_KEY` references in the three named source-edit functions. Static guard test pins this.
5. `_openai_source_edit_bytes`, `_openai_edit_size`, `_multipart_form_data` deletions are real (not commented-out); module no longer exposes them.
6. Schema default flip pinned by `test_schema_default_is_openrouter_gemini_slug`.
7. Model resolution exercises all 3 precedence branches + sentinel substitution + empty caller. 8 tests.
8. Pre-merge model-slug check completed by operator BEFORE merge approval (manual checkbox in PR body).
9. `credential_readiness.py:556`, `smoke-flyer-quality:154`, web/backend health, and P0-7 plan untouched. Documented as deferred drift in PR body.
10. No deploy after merge until operator green-light. PR body says so explicitly.
