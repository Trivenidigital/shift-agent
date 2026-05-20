# Flyer Source-Edit OpenRouter v0.1 — implementation plan

**Drift-check tag:** extends-Hermes

**New primitives introduced:** `_openrouter_source_edit_bytes` (replaces `_openai_source_edit_bytes`); preflight env-key swap to `OPENROUTER_API_KEY`; optional `FLYER_SOURCE_EDIT_MODEL` env. **No** new schema, **no** new state, **no** new audit variant, **no** new customer-copy string.

## Goal

Make automated exact source-edit reachable on `main-vps` today.

The runtime blocker: PR #137 wired the SOURCE/NEW clarification and routes SOURCE-chosen requests through `flyer_source_edit_preflight`, but preflight reads `OPENAI_API_KEY` which is `PLACEHOLDER` on `main-vps`. Every customer-chosen SOURCE edit therefore queues for a designer instead of running. `OPENROUTER_API_KEY` is already populated and read by 6 other vision call sites. Swap source-edit to use it via Gemini 2.5 Flash Image (reference-image-conditioned generation).

## Scope

**In scope:**
- `src/agents/flyer/workflow.py:source_edit_provider_ready` — env key from `OPENAI_API_KEY` → `OPENROUTER_API_KEY`.
- `src/agents/flyer/render.py` — add `_openrouter_source_edit_bytes(project, *, size, model, quality) -> bytes`; replace single caller at line 1795 (`render_source_edit_preview`). Delete `_openai_source_edit_bytes`.
- `tests/test_flyer_source_edit_preflight.py` — preflight env-key swap + PLACEHOLDER fail-closed.
- `tests/test_flyer_renderer.py` — mock OpenRouter response shape, error taxonomy, manual-queue fallback chain.

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

| Failure mode | How detected | Customer-visible result |
|---|---|---|
| `OPENROUTER_API_KEY` missing | `_read_env_value` returns "" | Preflight returns `(False, "...OPENROUTER_API_KEY missing", "source_edit_provider_unavailable")` → `--queue-manual-review` |
| `OPENROUTER_API_KEY` is `PLACEHOLDER` | `"PLACEHOLDER" in api_key` | Same as above |
| HTTP 4xx/5xx | `urllib.error.HTTPError` | Raise `FlyerRenderError` → existing `gen_ok=False` branch in `hooks.py` → `--queue-manual-review` + manual-edit ack |
| Connection timeout / IncompleteRead | `URLError`/`TimeoutError`/`IncompleteRead` (3-retry then raise) | Same as above |
| Response missing `choices` | `not choices` | `FlyerRenderError` → manual queue |
| Response missing `images` in `choices[0].message` | `not images` | `FlyerRenderError` → manual queue |
| Data URL malformed (no comma, not `data:image/...`) | startswith check + `_decode_data_url` raises | `FlyerRenderError` → manual queue |
| Base64 decode failure | `_decode_data_url` raises | `FlyerRenderError` → manual queue |

In every case the customer sees the existing `MANUAL_REVIEW_REASON_LINES["source_edit_provider_unavailable"]` ack — **no new customer-visible strings**.

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

- [ ] Test: preflight with `OPENROUTER_API_KEY=""` (and `OPENAI_API_KEY=valid`) → returns `(False, "...OPENROUTER_API_KEY missing", "source_edit_provider_unavailable")`.
  - Proves the swap actually happened — OpenAI key is now irrelevant.
- [ ] Test: preflight with `OPENROUTER_API_KEY=PLACEHOLDER_xxxx` → returns False (PLACEHOLDER substring detection still applies).
- [ ] Test: preflight with `OPENROUTER_API_KEY=sk-or-v1-...` (valid shape) + valid image reference → returns `(True, "ready", "")`.
- [ ] Test: preflight reads `OPENROUTER_API_KEY` from `/root/.hermes/.env` AND `/opt/shift-agent/.env` (parity with reference_extract).

### 1.2 Render-path OpenRouter contract

- [ ] Test: mock `urllib.request.urlopen` returning the documented OpenRouter response shape (`choices[0].message.images[0].image_url.url=data:image/png;base64,<valid>`) → `_openrouter_source_edit_bytes` returns the decoded bytes.
- [ ] Test: mock request body — assert outgoing JSON has `model="google/gemini-2.5-flash-image-preview"` (default), `modalities=["image","text"]`, `messages[0].content` includes both `type=text` and `type=image_url` with the source flyer's data URL.
- [ ] Test: `FLYER_SOURCE_EDIT_MODEL=other/model-id` env → that model id appears in the outgoing payload.

### 1.3 Error taxonomy

- [ ] Test: HTTP 500 from OpenRouter → `FlyerRenderError` raised; mock confirms no infinite retry.
- [ ] Test: `TimeoutError` from urlopen → 3 retries with backoff, then `FlyerRenderError`.
- [ ] Test: response with empty `choices` → `FlyerRenderError`.
- [ ] Test: response with `choices` but no `images` in `message` → `FlyerRenderError`.
- [ ] Test: response with malformed data URL (no comma, wrong prefix, invalid base64) → `FlyerRenderError`.

### 1.4 Static guard

- [ ] Test: grep the source-edit code path (`workflow.py:source_edit_provider_ready`, `render.py:_openrouter_source_edit_bytes`, `render.py:render_source_edit_preview`) and assert `OPENAI_API_KEY` is absent. (Defense-in-depth: a future refactor that re-introduces the OpenAI dependency must fail this test.)

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
    reference = _source_edit_reference_asset(project)
    reference_path = Path(reference.path)
    mime = reference.mime_type or mimetypes.guess_type(str(reference_path))[0] or "image/png"
    source_b64 = base64.b64encode(reference_path.read_bytes()).decode("ascii")
    source_data_url = f"data:{mime};base64,{source_b64}"
    payload = {
        "model": model,
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
    # 3-retry with backoff for transient transport errors (mirrors _openrouter_image_bytes).
    body = ""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=OPENROUTER_TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:1000]
            raise FlyerRenderError(f"OpenRouter source-edit HTTP {e.code}: {err}") from e
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
        raise FlyerRenderError(f"OpenRouter source-edit response had no images: {body[:500]}")
    url = images[0].get("image_url", {}).get("url") or ""
    if not url.startswith("data:image/"):
        raise FlyerRenderError("OpenRouter source-edit response did not include base64 image data")
    return _decode_data_url(url)
```

### 2.3 Caller swap + cleanup

- [ ] `render.py:1795` — replace `_openai_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)` with `_openrouter_source_edit_bytes(project, size=(1080, 1350), model=model, quality=quality)`.
- [ ] Add `FLYER_SOURCE_EDIT_MODEL` env handling — if set, the caller (or `render_source_edit_preview`) substitutes for the default. Default: `"google/gemini-2.5-flash-image-preview"`.
- [ ] Delete `_openai_source_edit_bytes`. Delete `_openai_edit_size`. Delete `_multipart_form_data` (only-used-here). Delete `OPENAI_IMAGE_EDIT_URL` and `OPENAI_IMAGE_EDIT_TIMEOUT_SEC` constants.
- [ ] Remove the OpenAI key fallback path from any cross-referenced helper (none expected; double-check via grep).

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
  - Risks.
  - Deferred items.
  - **"No deploy performed."**
  - **"Do not auto-deploy after merge. `OPENROUTER_API_KEY` is already populated on main-vps so merge alone changes live exact-edit behavior on the next inbound. Require operator green-light for the deploy."**
  - **"Spend-gated VPS smoke is required before declaring automated exact-edit customer-grade."**

## Risks

1. **Gemini 2.5 Flash Image fidelity unknown for source-preserving edits.** OpenAI's `images/edits` endpoint with `input_fidelity=high` was tuned for this task. Gemini's reference-image-conditioned generation MAY produce visually different output for the same prompt. Mitigation: customer-preview-then-APPROVE pattern stays in place; if the customer rejects, the existing revision path runs. The spend-gated VPS smoke (deferred item) verifies fidelity empirically before declaring customer-grade.
2. **First customer SOURCE edit after merge consumes OpenRouter credit.** Acceptable — `OPENROUTER_API_KEY` is already paying for vision reads via 6 other call sites. The cost per source-edit is one image generation, not a substrate change.
3. **Provider rate-limit / outage now affects exact edits.** Previously they queued manual (because the OpenAI provider was never reachable). Now a 429/503 fails the render and falls back to manual — net-equivalent customer experience.
4. **Latency.** OpenAI `images/edits` typically returned in 5-15s; Gemini reference-image-conditioned can take 10-30s. Existing `send_flyer_edit_processing_ack` already tells the customer "5-6 minutes" so latency variance is absorbed.
5. **Response shape drift.** OpenRouter has rotated `images[]` field shape in the past. Mitigation: error taxonomy covers missing-images and bad-data-URL cases by failing closed to manual queue, not crashing the customer flow.

## Deferred items

1. **Structured-contract regeneration path** (the (b) option). v0.2: extract source contract via vision, regenerate from the structured contract, run source-contract QA against the result. Touches `_extract_source_contract` + QA + golden scenarios; out of scope here.
2. **Multi-provider abstraction** (`FLYER_SOURCE_EDIT_PROVIDER` env). v0.1 ships single-provider (OpenRouter). If we add a third provider later, the abstraction lands then.
3. **`credential_readiness.py` updates.** Its job is "env populated"; the OpenAI key removal from the readiness gate is a separate sweep.
4. **`web/backend/` health endpoint changes.** Server-side provider abstraction means the UI does not need to know.
5. **`smoke-flyer-quality` script.** CLI surface preserved; if a future script needs to assert provider-specific output, it lands in a follow-up.
6. **Spend-gated VPS smoke runbook.** Before declaring automated exact-edit customer-grade, operator runs N source-edits through main-vps with controlled OpenRouter spend and visually inspects fidelity. Tracked separately.
7. **PR #138 closure.** Once this lands, the operator can close #138 as superseded.

## Self-Review Checklist

- ✅ All 8 operator guardrails encoded as explicit acceptance criteria.
- ✅ Net-new is 2 of 8 steps (25%), well under the 50% red-flag.
- ✅ No `web/`, no schema, no audit variant, no customer-copy invented.
- ✅ Manual-queue fallback path is `[Hermes]` substrate (PR #137), not re-touched.
- ✅ Static guard test pins the OpenAI-removal.
- ✅ No-auto-deploy-after-merge is in the PR summary template.
