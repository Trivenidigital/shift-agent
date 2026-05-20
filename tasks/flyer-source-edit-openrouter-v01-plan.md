# Flyer Source-Edit OpenRouter v0.1 — implementation plan

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `_openrouter_source_edit_bytes` (replaces `_openai_source_edit_bytes`); preflight env-key swap to `OPENROUTER_API_KEY`; optional `FLYER_SOURCE_EDIT_MODEL` env; `FlyerStudioConfig.edit_image_model` default flip from `"gpt-image-1"` to the OpenRouter Gemini slug. **No** new schema fields (default value only), **no** new state, **no** new audit variant, **no** new customer-copy string.

## Plan-review pass-1 corrections (2026-05-20)

Two parallel reviewers on orthogonal vectors (Hermes-first/scope + provider-correctness) returned the following BLOCKERS during plan review; all are folded into the sections below:

- **Caller model arg pollution.** `tools/generate-flyer-concepts:239` passes `model=cfg.flyer.edit_image_model` (default `"gpt-image-1"` at `schemas.py:784`) to `render_source_edit_preview` and onward. As-written, `_openrouter_source_edit_bytes` would receive `model="gpt-image-1"` and OpenRouter would 400 on every customer SOURCE edit. Fixed: schema default flips to the Gemini slug AND the helper resolves `FLYER_SOURCE_EDIT_MODEL` env (env > caller arg, with explicit legacy-sentinel substitution as a safety net).
- **`source_edit_provider_ready` return-shape mismatch.** Deployed code at `workflow.py:298` returns `tuple[bool, str]` (2-tuple). The 3-tuple `(False, "...", "source_edit_provider_unavailable")` lives one layer up at `actions.py:flyer_source_edit_preflight`. Earlier draft conflated them. Fixed: the test assertions in §1.1 target the 2-tuple for `source_edit_provider_ready` and the 3-tuple separately for `flyer_source_edit_preflight`.
- **Model slug verification.** `google/gemini-2.5-flash-image-preview` is plausible but unverified. Added pre-merge operator checkbox.
- **Error taxonomy gap.** Content-policy refusal (200-OK + text-only + no images) was bundled with the generic "no images" case. Now distinct.
- **Retry policy.** `HTTPError` raised immediately on first attempt. Transient 429/502/503/504 now retry within the existing 3-attempt budget.
- **Stale-OPENAI-references audit.** `credential_readiness.py:556` and `smoke-flyer-quality:154` still surface `OPENAI_API_KEY` posture; explicitly listed under Deferred Items so the operator sees the residual noise.
- **Co-resident plan coordination.** `tasks/flyer-cockpit-p0-7-health-panel-plan.md` assumes OpenAI is the source-edit provider; flagged in Risks.
- **Static guard scope.** Repo-wide grep would false-positive on residual readiness/smoke refs. Now scoped to named functions only.

## Goal

Make automated exact source-edit reachable on `main-vps` today.

The runtime blocker: PR #137 wired the SOURCE/NEW clarification and routes SOURCE-chosen requests through `flyer_source_edit_preflight`, but preflight reads `OPENAI_API_KEY` which is `PLACEHOLDER` on `main-vps`. Every customer-chosen SOURCE edit therefore queues for a designer instead of running. `OPENROUTER_API_KEY` is already populated and read by 6 other vision call sites. Swap source-edit to use it via Gemini 2.5 Flash Image (reference-image-conditioned generation).

## Scope

**In scope:**
- `src/agents/flyer/workflow.py:source_edit_provider_ready` — env key from `OPENAI_API_KEY` → `OPENROUTER_API_KEY`. **Return shape unchanged** (`tuple[bool, str]`). The 3-tuple `(ok, detail, reason_code)` lives in `cf-router/actions.py:flyer_source_edit_preflight` and is preserved as-is.
- `src/agents/flyer/render.py` — add `_openrouter_source_edit_bytes(project, *, size, model, quality) -> bytes`; replace single caller at line 1795 (`render_source_edit_preview`). Delete `_openai_source_edit_bytes`, `_openai_edit_size`, `_multipart_form_data`, `OPENAI_IMAGE_EDIT_URL`, `OPENAI_IMAGE_EDIT_TIMEOUT_SEC`.
- `src/platform/schemas.py:784` — flip `FlyerStudioConfig.edit_image_model` default from `"gpt-image-1"` to `"google/gemini-2.5-flash-image-preview"`. One-line change. Field shape unchanged. Operator-set values continue to win.
- `tests/test_flyer_source_edit_preflight.py` — preflight env-key swap + PLACEHOLDER fail-closed (2-tuple shape).
- `tests/test_flyer_renderer.py` — mock OpenRouter response shape, error taxonomy (including content-filter refusal), retry policy, manual-queue fallback chain, model-resolution precedence (env > caller arg > schema default).

**Out of scope (per operator guardrails):**
- `web/backend/` or `web/frontend/` — `FlyerAdmin.tsx`, health endpoints. Provider choice is server-side; the UI does not need to know.
- `src/platform/credential_readiness.py` — its job is "is the env populated"; key-name shifts there are a separate concern.
- `src/agents/flyer/scripts/smoke-flyer-quality` — preserve existing CLI surface.
- New schema fields — provider is env, not project state.
- Cockpit/UI changes.
- Structured-contract regeneration path (deferred to v0.2; touches source-contract QA + prompt policy + golden scenarios).

## Hermes-first capability checklist

Canonical per-step `[Hermes]` / `[net-new]` table. Receipt: `tasks/.hermes-check-receipts/flyer-source-edit-openrouter-v01.json`.

| # | Implementation step | `[Hermes]` or `[net-new]` |
|---|---|---|
| 1 | WhatsApp inbound + media cache | `[Hermes]` — cf-router + gateway |
| 2 | Scope intercept + SOURCE/NEW routing | `[Hermes]` — PR #137 substrate (not touched) |
| 3 | Quota reserve + project creation | `[Hermes]` — existing |
| 4 | Preflight env-key swap (OPENAI → OPENROUTER) | `[net-new]` ~10 LOC + ~30 LOC tests |
| 5 | Manual-queue fallback on provider-unavailable | `[Hermes]` — PR #137 substrate (behavior preserved) |
| 6 | Processing ack + concept-generation trigger | `[Hermes]` — existing |
| 7 | OpenRouter source-edit POST + response parse + error taxonomy | `[net-new]` ~110 LOC + ~180 LOC tests |
| 8 | Visual QA, text manifest, customer preview send | `[Hermes]` — PR #137 substrate (not touched) |

2 of 8 net-new (25%). Total: ~120 LOC code + ~210 LOC tests. Well under PR #138's +299/-313 across 21 files.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/render.py:1234-1288` (`_openrouter_image_bytes`) — exact request/response shape to mirror, including 3-retry with backoff and `image_url.url` data-URL parse.
- ✅ Read `src/agents/flyer/render.py:1224-1232` (`_decode_data_url`) — reuse unchanged.
- ✅ Read `src/agents/flyer/render.py:1359-1423` (`_openai_source_edit_bytes`) — function being replaced; preserve `(project, *, size, model, quality) -> bytes` signature.
- ✅ Read `src/agents/flyer/render.py:1291-1320` (`_source_edit_reference_asset`, `_source_edit_prompt`) — reused unchanged.
- ✅ Read `src/agents/flyer/render.py:1795` — confirmed sole caller of `_openai_source_edit_bytes` (`render_source_edit_preview`). One swap point, no other call sites.
- ✅ Read `src/agents/flyer/workflow.py:259-316` (`_read_env_value`, `source_edit_provider_ready`) — env-store reader already searches `/root/.hermes/.env` then `/opt/shift-agent/.env`; OpenRouter key already populated under both.
- ✅ Read `src/agents/flyer/reference_extract.py` (OpenRouter parity) — same `OPENROUTER_API_KEY` env name + same `_openrouter_key` lookup convention.
- ✅ Read `tests/test_flyer_source_edit_preflight.py` — pattern for monkeypatching `_read_env_value`; preserved.
- ✅ Read `tests/test_flyer_renderer.py` — pattern for mocking `urllib.request.urlopen` and `FakeResponse`; mirrored for the new tests.

## OpenRouter request contract (Gemini 2.5 Flash Image)

```python
POST https://openrouter.ai/api/v1/chat/completions
Authorization: Bearer <OPENROUTER_API_KEY>
Content-Type: application/json
HTTP-Referer: https://github.com/Trivenidigital/SME-Agents
X-Title: Hermes Flyer Studio

{
  "model": "google/gemini-2.5-flash-image-preview",   # FLYER_SOURCE_EDIT_MODEL overrides
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "<_source_edit_prompt(project) output>"},
      {"type": "image_url", "image_url": {"url": "data:image/<mime>;base64,<source-bytes>"}}
    ]
  }],
  "modalities": ["image", "text"],
  "stream": false,
  "image_config": {
    "aspect_ratio": "<_aspect_ratio(size)>",
    "image_size": "2K" if quality == "high" else "1K"
  }
}
```

Response parse: `choices[0].message.images[0].image_url.url` → must startswith `data:image/` → `_decode_data_url` → bytes.

Mirrors `_openrouter_image_bytes` exactly EXCEPT the `messages[0].content` is multimodal (text + image_url) instead of text-only, so the reference image is conditioning input.

## Error taxonomy (all must become `FlyerRenderError` → manual-queue fallback)

**Two-tuple/three-tuple separation:**
- `workflow.py:source_edit_provider_ready` returns `(bool, detail_str)` — 2-tuple.
- `actions.py:flyer_source_edit_preflight` returns `(bool, detail_str, reason_code_str)` — 3-tuple. This is the function the cf-router calls. Its `reason_code` mapping is preserved by exhaustion: the new detail `"...OPENROUTER_API_KEY missing"` doesn't contain `"uploaded reference image"` or `"must be an image"` (the existing substring-match branches in `actions.py:2137-2141`), so it lands in the `else` → `"source_edit_provider_unavailable"`. Confirmed without code change.

| Failure mode | How detected | Customer-visible result |
|---|---|---|
| `OPENROUTER_API_KEY` missing | `_read_env_value` returns "" | `source_edit_provider_ready` → `(False, "...OPENROUTER_API_KEY missing")`; `flyer_source_edit_preflight` wraps as 3-tuple `(False, detail, "source_edit_provider_unavailable")` → `--queue-manual-review` |
| `OPENROUTER_API_KEY` is `PLACEHOLDER` | `"PLACEHOLDER" in api_key` | Same as above |
| HTTP 4xx (non-retriable) | `urllib.error.HTTPError` with `e.code not in (429, 502, 503, 504)` | Raise `FlyerRenderError` immediately → existing `gen_ok=False` branch in `hooks.py` → `--queue-manual-review` + manual-edit ack |
| HTTP 429 / 502 / 503 / 504 (retriable) | `urllib.error.HTTPError` with code in retriable set | Retry within 3-attempt budget with exponential backoff; raise `FlyerRenderError` only after all 3 attempts fail |
| Connection timeout / URLError / IncompleteRead | `URLError`/`TimeoutError`/`IncompleteRead` | 3-retry with backoff, then `FlyerRenderError` |
| Response missing `choices` | `not choices` | `FlyerRenderError` → manual queue |
| **Content-policy refusal** (200-OK, text-only) | `not images` AND `choices[0].finish_reason in {"content_filter", "safety"}` OR `choices[0].message.content` non-empty + `images` empty | `FlyerRenderError("OpenRouter source-edit refused (likely content policy): ...")` → manual queue. Distinct message string from generic `not images` so operator triage can disambiguate refusal vs malformed response. |
| Response missing `images` (generic) | `not images` (no finish_reason hint) | `FlyerRenderError("OpenRouter source-edit response had no images: ...")` → manual queue |
| Data URL malformed (no comma, not `data:image/...`) | startswith check + `_decode_data_url` raises | `FlyerRenderError` → manual queue |
| Base64 decode failure | `_decode_data_url` raises | `FlyerRenderError` → manual queue |

In every case the customer sees the existing `MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"]` ack — **no new customer-visible strings**.

## Model resolution precedence

`_openrouter_source_edit_bytes(project, *, size, model, quality)` resolves the effective model as follows, in order:

1. **`FLYER_SOURCE_EDIT_MODEL` env** — if set and non-empty, wins unconditionally. Lets the operator switch models on a single VPS without a deploy.
2. **Caller `model` arg** — used IF it does NOT match the legacy OpenAI sentinel set (`{"gpt-image-1", "dall-e-2", "dall-e-3"}`). This protects against unflipped schema defaults / stale configs from forwarding an OpenAI model name into an OpenRouter request.
3. **Hard-coded default** — `"google/gemini-2.5-flash-image-preview"` if the caller arg is empty or matches a legacy sentinel.

A test pins each precedence level. Without #2's sentinel substitution, a stale `cfg.flyer.edit_image_model="gpt-image-1"` from a customer VPS that doesn't pick up the new schema default would 400 on every inbound. The schema default is flipped in this PR (in scope), but the substitution is belt-and-braces for operator VPSes that override.

## Acceptance criteria

### Behavior

1. With `OPENROUTER_API_KEY` populated and valid: an exact source-edit request reaches `_openrouter_source_edit_bytes`, receives a generated image, and flows through `visual_qa` and customer-preview send unchanged.
2. With `OPENROUTER_API_KEY` missing: preflight returns False; customer gets the existing manual-queue ack; no `_openrouter_source_edit_bytes` call attempted.
3. With `OPENROUTER_API_KEY=PLACEHOLDER`: same as missing (fail-closed).
4. With valid key but provider returns HTTP error/timeout/malformed response: `FlyerRenderError` raised; hooks.py `gen_ok=False` branch fires; customer gets the existing manual-queue ack via existing `--queue-manual-review` invocation.
5. **Zero references to `OPENAI_API_KEY` remain in the source-edit code path** (`workflow.py:source_edit_provider_ready`, `render.py:render_source_edit_preview`, `render.py:_openrouter_source_edit_bytes`). Pinned by a static guard test.

### Guardrails (from operator)

- ✅ Upstream signature unchanged: `_openrouter_source_edit_bytes(project, *, size, model, quality) -> bytes` matches `_openai_source_edit_bytes` exactly.
- ✅ No `web/backend/`, `web/frontend/`, cockpit changes.
- ✅ No `credential_readiness.py` changes.
- ✅ No smoke script changes.
- ✅ Test: no runtime `OPENAI_API_KEY` dependency remains in source-edit preflight/render.
- ✅ Test: placeholder `OPENROUTER_API_KEY` fails closed to manual queue.
- ✅ Test: provider response data URL shape (good + each malformed variant).
- ✅ Document: spend-gated VPS smoke required before declaring automated exact-edit customer-grade.
- ✅ No auto-deploy after merge (OpenRouter is already populated on main-vps; merge alone changes live behavior on the next inbound).

## Task 1: RED tests (TDD)

**Files:**
- Modify: `tests/test_flyer_source_edit_preflight.py`
- Modify: `tests/test_flyer_renderer.py`

### 1.1 Preflight env-key swap

Two layers to test — `workflow.py:source_edit_provider_ready` (2-tuple) and the cf-router wrapper `actions.py:flyer_source_edit_preflight` (3-tuple).

- [ ] **2-tuple layer**: `source_edit_provider_ready` with `OPENROUTER_API_KEY=""` (and `OPENAI_API_KEY=valid` to prove non-influence) → returns `(False, "source edit provider is not configured: OPENROUTER_API_KEY missing")`.
- [ ] **2-tuple layer**: `source_edit_provider_ready` with `OPENROUTER_API_KEY="PLACEHOLDER_xxxx"` → returns `(False, ...)` (PLACEHOLDER substring detection still applies).
- [ ] **2-tuple layer**: `source_edit_provider_ready` with `OPENROUTER_API_KEY="sk-or-v1-..."` + valid image reference → returns `(True, "ready")`.
- [ ] **2-tuple layer**: `source_edit_provider_ready` reads `OPENROUTER_API_KEY` from `/root/.hermes/.env` AND `/opt/shift-agent/.env` (parity with reference_extract).
- [ ] **3-tuple layer**: `flyer_source_edit_preflight` (cf-router) wraps the 2-tuple correctly — when `source_edit_provider_ready` returns `(False, "...OPENROUTER_API_KEY missing")`, `flyer_source_edit_preflight` returns `(False, detail, "source_edit_provider_unavailable")`. Proves the existing substring-fallback `else` branch in `actions.py:2137-2141` still routes the new detail string to the right reason_code (preserved by exhaustion).

### 1.2 Render-path OpenRouter contract

- [ ] Test: mock `urllib.request.urlopen` returning the documented OpenRouter response shape (`choices[0].message.images[0].image_url.url=data:image/png;base64,<valid>`) → `_openrouter_source_edit_bytes` returns the decoded bytes.
- [ ] Test: mock request body — assert outgoing JSON has `model="google/gemini-2.5-flash-image-preview"` (default), `modalities=["image","text"]`, `messages[0].content` includes both `type=text` and `type=image_url` with the source flyer's data URL.
- [ ] **Model precedence — env wins**: with `FLYER_SOURCE_EDIT_MODEL="other/model-id"` env AND caller passes `model="anything"` → outgoing payload has `"model":"other/model-id"`.
- [ ] **Model precedence — caller non-legacy arg wins over default**: no env, caller passes `model="some/non-openai-model"` → outgoing payload has `"model":"some/non-openai-model"`.
- [ ] **Model precedence — legacy sentinel substitution**: no env, caller passes `model="gpt-image-1"` (the unflipped schema default) → outgoing payload has `"model":"google/gemini-2.5-flash-image-preview"`. Belt-and-braces against operator VPSes that override the schema default to a stale OpenAI string.
- [ ] **Model precedence — default applies when caller passes empty**: no env, caller passes `model=""` → outgoing payload has `"model":"google/gemini-2.5-flash-image-preview"`.
- [ ] **Schema default flip**: `FlyerStudioConfig().edit_image_model == "google/gemini-2.5-flash-image-preview"`. Pins the default change.

### 1.3 Error taxonomy

- [ ] Test: HTTP 400 (non-retriable) from OpenRouter → `FlyerRenderError` raised on first attempt; mock confirms no retry.
- [ ] **HTTP retriable codes**: 429, 502, 503, 504 each retry within the 3-attempt budget with exponential backoff; only raise `FlyerRenderError` after all 3 attempts fail. Parametrize over the 4 codes.
- [ ] Test: `TimeoutError` from urlopen → 3 retries with backoff, then `FlyerRenderError`.
- [ ] Test: response with empty `choices` → `FlyerRenderError`.
- [ ] **Content-filter refusal**: response with `choices[0].finish_reason="content_filter"` and no `images` → distinct `FlyerRenderError` with `"refused (likely content policy)"` in the message. Operator triage can grep this string in audit logs.
- [ ] **Safety refusal**: response with `choices[0].finish_reason="safety"` and no `images` → same distinct message as content_filter.
- [ ] Test: response with `choices` and `message.content` non-empty BUT `images` empty → distinct refusal `FlyerRenderError` (heuristic: text-only response with no image means refusal even when finish_reason is missing).
- [ ] Test: response with `choices` but no `images` AND no `message.content` AND no finish_reason hint → generic `"had no images"` `FlyerRenderError`.
- [ ] Test: response with malformed data URL (no comma, wrong prefix, invalid base64) → `FlyerRenderError`.

### 1.4 Static guard

- [ ] Test: extract the source of EXACTLY these three named functions (`workflow.py:source_edit_provider_ready`, `render.py:_openrouter_source_edit_bytes`, `render.py:render_source_edit_preview`) and assert `OPENAI_API_KEY` is absent from each. **Function-scoped, not repo-wide** — `credential_readiness.py:556` and `smoke-flyer-quality:154` still contain the string for unrelated readiness/posture reporting, and those are explicitly out of scope (see Deferred Items). A repo-wide grep would false-positive on those.
- [ ] Test: also assert `_openai_source_edit_bytes`, `_openai_edit_size`, `_multipart_form_data` are NOT importable from `render.py` (deletions are real, not commented-out).

Run:
```powershell
python -m pytest tests/test_flyer_source_edit_preflight.py tests/test_flyer_renderer.py -q
```

Expected: tests fail (no implementation yet).

## Task 2: Implementation

**Files:**
- Modify: `src/agents/flyer/workflow.py`
- Modify: `src/agents/flyer/render.py`

### 2.1 Preflight env-key swap

- [ ] `workflow.py:source_edit_provider_ready` — swap `OPENAI_API_KEY` → `OPENROUTER_API_KEY`. Keep PLACEHOLDER substring check. Keep `env_path` test-injection.
- [ ] Update detail string to `"source edit provider is not configured: OPENROUTER_API_KEY missing"` so logs match. The cockpit-side `reason_code="source_edit_provider_unavailable"` stays the same so the manual-queue triage view is undisturbed.

### 2.2 OpenRouter source-edit helper

- [ ] Add `_openrouter_source_edit_bytes(project, *, size, model, quality) -> bytes` next to `_openai_source_edit_bytes`. Signature identical so the caller swap is mechanical.
- [ ] Add `OPENAI_LEGACY_MODEL_SENTINELS = {"gpt-image-1", "dall-e-2", "dall-e-3"}` module constant.
- [ ] Add `FLYER_SOURCE_EDIT_DEFAULT_MODEL = "google/gemini-2.5-flash-image-preview"` module constant.

```python
OPENAI_LEGACY_MODEL_SENTINELS = {"gpt-image-1", "dall-e-2", "dall-e-3"}
FLYER_SOURCE_EDIT_DEFAULT_MODEL = "google/gemini-2.5-flash-image-preview"
RETRIABLE_HTTP_STATUSES = {429, 502, 503, 504}


def _resolve_source_edit_model(caller_model: str) -> str:
    """Resolve effective OpenRouter source-edit model.

    Precedence:
      1. FLYER_SOURCE_EDIT_MODEL env (operator override, wins unconditionally).
      2. caller_model arg (from cfg.flyer.edit_image_model upstream), IF it
         doesn't match a known legacy OpenAI sentinel.
      3. Hard-coded default (Gemini Flash Image preview).

    The legacy-sentinel substitution at step 2 protects against operator VPSes
    whose config.yaml still carries the pre-flip schema default `"gpt-image-1"`
    even after the schema update lands. Without it, those VPSes would 400 on
    every customer SOURCE inbound.
    """
    env_override = os.environ.get("FLYER_SOURCE_EDIT_MODEL", "").strip()
    if env_override:
        return env_override
    if caller_model and caller_model not in OPENAI_LEGACY_MODEL_SENTINELS:
        return caller_model
    return FLYER_SOURCE_EDIT_DEFAULT_MODEL


def _classify_openrouter_no_images(body: str, doc: dict) -> str:
    """Pick the most informative FlyerRenderError message for a 200-OK
    response that came back without an image. Refusals (content policy /
    safety) need a distinct message string so operator triage can disambiguate
    refusal-vs-malformed in audit logs.
    """
    choices = doc.get("choices") or [{}]
    first = choices[0] if choices else {}
    finish_reason = str(first.get("finish_reason") or "").lower()
    content_text = ""
    message = first.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        content_text = content
    elif isinstance(content, list):
        # Multimodal content shape; join text parts.
        content_text = " ".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if finish_reason in {"content_filter", "safety"} or content_text.strip():
        return f"OpenRouter source-edit refused (likely content policy): {body[:500]}"
    return f"OpenRouter source-edit response had no images: {body[:500]}"


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
    # 3-retry with backoff for transient transport AND retriable HTTP errors.
    # Non-retriable HTTPError (4xx-non-429) raises on the first attempt because
    # those won't change on retry. Mirrors policy from review pass-1.
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

### 2.3 Caller swap + cleanup

- [ ] `render.py:1795` — replace `_openai_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)` with `_openrouter_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)`. The `model` is the caller's arg (from `cfg.flyer.edit_image_model`); the helper resolves the effective model via `_resolve_source_edit_model` internally (precedence in §"Model resolution precedence").
- [ ] **Schema default flip** in `src/platform/schemas.py:784`:
  ```diff
  -    edit_image_model: str = Field(default="gpt-image-1", min_length=1, max_length=120)
  +    edit_image_model: str = Field(default="google/gemini-2.5-flash-image-preview", min_length=1, max_length=120)
  ```
- [ ] Delete `_openai_source_edit_bytes`. Delete `_openai_edit_size`. Delete `_multipart_form_data` (only-used-here). Delete `OPENAI_IMAGE_EDIT_URL` and `OPENAI_IMAGE_EDIT_TIMEOUT_SEC` constants.
- [ ] Verify deletions cleanly via `grep -rn "_openai_source_edit_bytes\|_openai_edit_size\|_multipart_form_data\|OPENAI_IMAGE_EDIT_URL\|OPENAI_IMAGE_EDIT_TIMEOUT_SEC" src/` → zero hits. Any non-zero is a missed call site.

Run:
```powershell
python -m pytest tests/test_flyer_source_edit_preflight.py tests/test_flyer_renderer.py -q
python -m compileall -q src\agents\flyer
git diff --check
```

Expected: all tests pass; compile clean.

## Task 3: Full focused verification

Run:
```powershell
python -m pytest tests/test_flyer_source_edit_preflight.py tests/test_flyer_renderer.py tests/test_flyer_workflow.py tests/test_cf_router_flyer_routing.py -q
python -m compileall -q src\agents\flyer src\plugins\cf-router src\platform
git diff --check
git status --short
```

Expected: green; only the intended files touched; no `web/` or `credential_readiness.py` in diff.

## Task 4: PR

- [ ] Open PR titled `feat(flyer): route source edits through OpenRouter (v0.1)`.
- [ ] PR summary must explicitly include:
  - Files changed (3 source + 2 tests + this plan + receipt — narrow by construction).
  - Tests run.
  - Risks (all 8 from §Risks above, especially the kill-switch criterion).
  - Deferred items (all 8 from §Deferred items above, especially the residual `credential_readiness.py:556` + `smoke-flyer-quality:154` OPENAI_API_KEY references that this PR explicitly does NOT touch).
  - **"No deploy performed."**
  - **"Do not auto-deploy after merge. `OPENROUTER_API_KEY` is already populated on main-vps so merge alone changes live exact-edit behavior on the next inbound. Require operator green-light for the deploy."**
  - **"Spend-gated VPS smoke is required before declaring automated exact-edit customer-grade."**

### Pre-merge operator checklist (must complete before merge approval)

- [ ] Operator confirms `google/gemini-2.5-flash-image-preview` is a live OpenRouter slug. Reviewer can verify externally; the operator confirms before merge:
  ```bash
  curl -s -H "Authorization: Bearer $OPENROUTER_API_KEY" \
    https://openrouter.ai/api/v1/models | jq -r '.data[].id' | grep -i gemini.*image
  ```
  If the slug returned differs, the operator either updates `FLYER_SOURCE_EDIT_DEFAULT_MODEL` in render.py to match OR sets the `FLYER_SOURCE_EDIT_MODEL` env on main-vps before deploy. Either is acceptable; the slug must exist on OpenRouter at deploy time or every customer SOURCE inbound 400s.
- [ ] Operator confirms `OPENROUTER_API_KEY` is populated on main-vps (already true per the PR #137 context) and is NOT a `PLACEHOLDER` value.
- [ ] Operator commits to running 1 controlled SOURCE edit via VPS smoke BEFORE green-lighting unrestricted customer traffic. The kill-switch criterion (Risks #1) is N≥3/5 regenerations → revert this PR.

## Risks

1. **Gemini 2.5 Flash Image fidelity unknown for source-preserving edits.** OpenAI's `images/edits` endpoint with `input_fidelity=high` was tuned for "preserve source, change X." Gemini's reference-image-conditioned generation is closer to "inspired by." MAY produce visually different output for the same prompt. **Kill-switch criterion**: if the post-merge spend-gated VPS smoke (deferred item) shows N≥3/5 source-edits regenerating the layout instead of editing in-place, revert this PR (no config flip; revert is the rollback path). Customer-preview-then-APPROVE stays in place as the second line of defense — customer can reject and trigger the revision path.
2. **F0061-class regression risk.** PR #137 closed F0061 at the *routing* layer (SOURCE/NEW clarification preserves customer intent). This PR adds a new vector: if Gemini regenerates instead of editing, the customer's "preserve everything else" promise breaks at the *rendering* layer even though routing is correct. Kill-switch above (Risk #1) also covers this.
3. **First customer SOURCE edit after merge consumes OpenRouter credit.** Acceptable — `OPENROUTER_API_KEY` is already paying for vision reads via 6 other call sites. Marginal cost per source-edit is one Gemini image generation. Pre-deploy: operator should run 1 controlled SOURCE edit via VPS smoke BEFORE green-lighting unrestricted customer traffic, to confirm the model slug works and fidelity is acceptable.
4. **Provider rate-limit / outage now reachable.** Previously SOURCE edits queued manual at preflight because `OPENAI_API_KEY=PLACEHOLDER` short-circuited before any urllib call. Now a 429/503/timeout reaches the wire and falls back to manual via the retriable-HTTP-status / 3-retry budget → manual-queue ack. Net-equivalent customer experience.
5. **Latency.** OpenAI `images/edits` typically returned in 5-15s; Gemini reference-image-conditioned can take 10-30s. Existing `send_flyer_edit_processing_ack` already tells the customer "5-6 minutes" so latency variance is absorbed.
6. **Response shape drift.** OpenRouter has rotated `images[]` field shape in the past. Mitigation: error taxonomy covers missing-images, content-filter refusal, and bad-data-URL cases by failing closed to manual queue.
7. **Co-resident plan coordination.** `tasks/flyer-cockpit-p0-7-health-panel-plan.md` (P0-7 health panel) reads `OPENAI_API_KEY` posture for the "provider asymmetry" framing in the health panel. If this PR lands first, P0-7's framing becomes stale (OpenAI is no longer the source-edit provider). Mitigation: flag for the P0-7 author to rebase before re-review; not a blocker for THIS PR.
8. **Vision-client chokepoint debt.** Per PR #137 deferred items, six OpenRouter call sites (Flyer reference_extract, Flyer visual_qa, Flyer check-flyer-reference-scope, Catering parse-menu-photo, Catering vision-auth-smoke, Expense extract-receipt) plus the existing `_openrouter_image_bytes` could collapse into a shared `src/platform/vision_client.py`. This PR adds a 7th near-clone (`_openrouter_source_edit_bytes`). Acceptable for v0.1 — the vision-client extraction is a separate hardening PR and absorbing this helper alongside the others is the right shape for that work.

## Deferred items

1. **Structured-contract regeneration path** (the (b) option). v0.2: extract source contract via vision, regenerate from the structured contract, run source-contract QA against the result. Touches `_extract_source_contract` + QA + golden scenarios; out of scope here.
2. **Multi-provider abstraction** (`FLYER_SOURCE_EDIT_PROVIDER` env). v0.1 ships single-provider (OpenRouter). If we add a third provider later, the abstraction lands then.
3. **`credential_readiness.py:556` cleanup.** Today this line still registers `CredentialRequirement("OPENAI_API_KEY", "api_key", "Flyer Studio source-preserving image edit gate.")`. After this PR lands, that requirement is stale — source-edit no longer uses the OpenAI key. The credential readiness output will continue to flag missing `OPENAI_API_KEY` as a Flyer Studio gate failure, creating operator-visible noise. Out of scope here per the operator's "no `credential_readiness.py` changes" guardrail; cleanup belongs in a posture-cleanup follow-up.
4. **`smoke-flyer-quality:154` cleanup.** Today the script reports `"openai_source_edit_key": _key_posture("OPENAI_API_KEY")` in its health output. Same stale-signal class as #3. Out of scope here per the operator's "no smoke script changes" guardrail; cleanup belongs in the same posture-cleanup follow-up as #3.
5. **`web/backend/` health endpoint changes.** Server-side provider abstraction means the UI does not need to know. Same cleanup wave as #3 and #4.
6. **Spend-gated VPS smoke runbook.** Before declaring automated exact-edit customer-grade, operator runs N source-edits through main-vps with controlled OpenRouter spend and visually inspects fidelity against the kill-switch criterion (Risks #1). Tracked separately.
7. **PR #138 closure.** Once this lands, the operator can close #138 as superseded. This PR is narrower (+~330 LOC vs +299/-313 across 21 files in #138, focused on the runtime swap only).
8. **`tasks/flyer-cockpit-p0-7-health-panel-plan.md` rebase.** Co-resident plan reads `OPENAI_API_KEY` posture for the source-edit provider; framing becomes stale after this PR. P0-7 author should rebase before re-review.

## Self-Review Checklist

- ✅ All 8 operator guardrails encoded as explicit acceptance criteria.
- ✅ Net-new is 2 of 8 steps (25%), well under the 50% red-flag.
- ✅ No `web/`, no schema, no audit variant, no customer-copy invented.
- ✅ Manual-queue fallback path is `[Hermes]` substrate (PR #137), not re-touched.
- ✅ Static guard test pins the OpenAI-removal.
- ✅ No-auto-deploy-after-merge is in the PR summary template.
