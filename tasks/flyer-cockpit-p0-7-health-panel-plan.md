**Drift-check tag:** extends-Hermes

# Flyer Cockpit P0-7 — Provider / Runtime Health Panel + Source-Edit Posture Clarity

## Goal

Make the operator able to answer **"why are exact edits stuck before customers wait?"** from one screen, and make the OpenRouter (generation/vision) vs OpenAI (source-edit) provider distinction impossible to miss. Tighten the spend-gated real-model smoke so it cannot accidentally spend.

This is the **P0-7** slice from `tasks/flyer-cockpit-ops-dashboard-backlog-2026-05-19.md`.

## Hermes-first capability checklist

| # | Step | Tag |
|---|---|---|
| 1 | Operator opens Flyer admin section in Cockpit (FastAPI + React shell) | `[Hermes]` — `web/backend/app/routers/flyer.py` + `require_auth` already provide the section |
| 2 | Frontend issues `GET /flyer/health` via cookie-auth `useQuery` pattern | `[Hermes]` — `@tanstack/react-query` + `api.get` already used in `FlyerAdmin.tsx` |
| 3 | Backend probes platform runtime (gateway, bridge, paired WhatsApp) | `[Hermes]` — `routers/health.py::_gateway_active`/`_bridge_health`/`_wa_paired` already implemented |
| 4 | Backend reads `OPENROUTER_API_KEY` from layered env stores | `[Hermes]` — `agents/flyer/workflow.py::_read_env_value` is the canonical layered reader |
| 5 | Backend reads `OPENAI_API_KEY` and calls `source_edit_provider_ready` for the canonical reason string | `[Hermes]` — both helpers already exist in `agents/flyer/workflow.py` |
| 6 | Backend reads `FlyerConfig.draft_image_model` / `final_image_model` / `edit_image_model` from config.yaml | `[Hermes]` — `Config` + `load_yaml_model` already deployed |
| 7 | Backend resolves deploy tag (commit hash + newest deploy-*.tgz) | `[net-new]` — deploy script writes `/opt/shift-agent/.commit-hash` and `/opt/shift-agent/deploys/deploy-*.tgz` but no cockpit-side helper exposes them today. ~20 LOC. |
| 8 | Backend assembles response with severity mapping + Pydantic model | `[net-new]` — `_flyer_provider_health` + `_platform_runtime_health` + `FlyerHealthResponse`. ~80 LOC + 7 tests. |
| 9 | Frontend renders provider/runtime health panel with OpenRouter vs OpenAI visually distinct | `[net-new]` — `<FlyerHealthPanel>` component in `FlyerAdmin.tsx` overview tab. ~120 LOC TSX + types. |
| 10 | Spend-gated smoke refuses CI env even with `--allow-spend` | `[net-new]` — CI-env guard + `FLYER_GOLDEN_SPEND_PROFILE=isolated` guard on `smoke-flyer-quality` and the pytest. ~30 LOC + 2 tests. |
| 11 | Operator responds to red/yellow signal before customers wait | `[Hermes]` — human workflow, not code |

**Awesome-Hermes-Agent ecosystem check:** not applicable — provider posture + cockpit observability is per-customer operator UI, not a missing external capability.

**Red-flag check:** 4 of 11 steps `[net-new]`, well under half. Every net-new item was re-examined against the verified capability list and confirmed it cannot be folded into Hermes substrate without inventing it: deploy-marker reading is per-customer-VPS observability, severity policy is Flyer-specific UX, the panel is per-product, and CI-env guards are operator discipline. No substrate misses.

## Drift-rule self-checks

- ✅ Read `web/backend/app/routers/health.py` (existing `/dashboard` `ComponentStatus` pattern at L80–141) before drafting the new endpoint shape.
- ✅ Read `web/backend/app/models.py` (`ComponentStatus(name, ok, detail)` at L36–39) before drafting the Pydantic response model — I extend, not duplicate.
- ✅ Read `web/backend/app/routers/flyer.py` (`require_auth` decorator usage + `_AGENT_ROOT` sys.path precedent at L27–54, summary route at L408–410) before drafting the new route.
- ✅ Read `web/backend/app/config.py` (`state_dir`, `bridge_health_url`, COCKPIT_TEST_MODE handling at L21–46) before assuming any new settings were needed — none are.
- ✅ Read `src/agents/flyer/workflow.py` (`_read_env_value` at L259–280, `source_edit_provider_ready` at L298–316) so the new helper composes the deployed env reader, not a parallel one.
- ✅ Read `src/agents/flyer/render.py` (`_openrouter_image_bytes` at L1211, `_openai_source_edit_bytes` at L1336) to verify the provider asymmetry that the operator-message lesson on 2026-05-19 flagged.
- ✅ Read `src/platform/schemas.py` (`FlyerConfig.draft_image_model`/`final_image_model`/`edit_image_model` at L774–784) so the model-config block surfaces canonical fields.
- ✅ Read `src/agents/flyer/scripts/smoke-flyer-quality` (existing `--allow-spend` gate at L69–71) so the CI-env guard layers on top of the existing discipline rather than reinventing it.
- ✅ Read `tests/test_flyer_golden_scenarios_real_model.py` (existing `FLYER_GOLDEN_ALLOW_SPEND=1` skipif at L37–40) so the new spend-profile guard composes with the deployed gate.
- ✅ Read `src/agents/shift/scripts/shift-agent-deploy.sh` (`.commit-hash` write at L491 + deploys tarball naming at L500–504) so the deploy-tag resolver matches actual on-disk shape.
- ✅ Read `web/frontend/src/sections/FlyerAdmin.tsx` (Card/CardContent/useQuery pattern in the first 120 lines) so the new panel mirrors the existing visual + data idiom.

**Deployed-pattern checklist (Part 1):** all conventions honored — no SQLite, no parallel env reader, no new audit-log variants, no per-VPS cross-state, no new approval-code generator, no `extra="forbid"` violations.

## Read-deployed-code summary table

| File | Why I read it |
|---|---|
| `web/backend/app/routers/health.py` | Existing `/dashboard` returns `ComponentStatus` list. Pattern: subprocess + httpx, `(ok, detail)` per check. |
| `web/backend/app/models.py` | `ComponentStatus(name, ok, detail)` is the shared shape; extend with richer `FlyerHealthComponent` + `severity` while staying additive. |
| `web/backend/app/routers/flyer.py` | `/flyer/summary` is the existing overview endpoint pattern. New route `GET /flyer/health` mirrors it under `require_auth`. |
| `web/backend/app/config.py` | `state_dir` + `bridge_health_url` already wired. No new settings needed. |
| `src/agents/flyer/workflow.py` | `_read_env_value` is the canonical layered env reader; `source_edit_provider_ready` is the canonical reason-string source. |
| `src/agents/flyer/render.py` | Confirms OpenRouter for generation + OpenAI for source-edit are independent keys. This **is** the posture asymmetry the panel must surface. |
| `src/platform/schemas.py` | `FlyerConfig` exposes the image model fields surfaced read-only in the panel. |
| `src/agents/flyer/scripts/smoke-flyer-quality` | Already gates `--real-model` on `--allow-spend` (exit 2 if missing). |
| `tests/test_flyer_golden_scenarios_real_model.py` | Already gated by `FLYER_GOLDEN_ALLOW_SPEND=1`; new CI-env + spend-profile guards layer on top. |
| `src/agents/shift/scripts/shift-agent-deploy.sh` | `/opt/shift-agent/.commit-hash` is the canonical deploy-marker file (8-char SHA prefix used in deploy tag). |
| `web/frontend/src/sections/FlyerAdmin.tsx` | Card/useQuery pattern reused for the new panel. |

## Scope (this PR)

### 1. Backend: `GET /flyer/health` (auth-gated, read-only)

In `web/backend/app/routers/flyer.py`, add one new route returning a structured payload:

```json
{
  "checked_at": "2026-05-20T03:14:15Z",
  "deploy_tag": "deploy-20260520-000424-a0e853e7",
  "commit_hash": "a0e853e7",
  "components": [
    {"name": "gateway",         "severity": "green", "detail": "active",                     "checked_at": "..."},
    {"name": "whatsapp_bridge", "severity": "green", "detail": "connected",                  "checked_at": "..."},
    {"name": "whatsapp_paired", "severity": "green", "detail": "<jid>",                      "checked_at": "..."},
    {"name": "cockpit_service", "severity": "green", "detail": "deploy-...-a0e853e7",        "checked_at": "..."}
  ],
  "providers": [
    {
      "name": "openrouter_generation_vision",
      "purpose": "Image generation + vision extraction (normal Flyer Studio path)",
      "severity": "green",
      "detail": "OPENROUTER_API_KEY present",
      "key_present": true,
      "key_source": "hermes_env" | "agent_env" | "process_env" | null,
      "model_config": {"draft_image_model": "...", "final_image_model": "..."}
    },
    {
      "name": "openai_source_edit",
      "purpose": "Exact source-preserving flyer edits (OpenAI Images Edits API)",
      "severity": "yellow" | "red" | "green",
      "detail": "source edit provider is not configured: OPENAI_API_KEY missing" | "ready",
      "key_present": false,
      "key_source": null,
      "model_config": {"edit_image_model": "..."},
      "operator_note": "Source edit is an explicit OpenAI dependency, not OpenRouter. See backlog `tasks/flyer-source-edit-provider-posture-2026-05-20.md`."
    }
  ]
}
```

**Severity rules** (no false-positive red):

- `gateway` / `bridge` / `paired` / `cockpit_service` — `green` if ok, `red` otherwise.
- `openrouter_generation_vision` — `green` if key present, **`red`** if missing (hard block on normal generation).
- `openai_source_edit` — `green` if key present, **`yellow`** if missing. Yellow because source-edit-unavailable is a known degraded posture that routes to the manual queue (`source_edit_provider_unavailable` reason exists today); it's not a customer-blocking outage for generation.

**No secrets in response.** Only `key_present: bool` and `key_source: "hermes_env" | "agent_env" | "process_env" | null` — never the value, never a prefix, never length. `key_source` tells the operator which file to inspect without revealing anything sensitive.

Helper functions (private in `routers/flyer.py`):

```python
def _read_env_layered(name: str) -> tuple[str, str | None]:
    """(value, source) where source ∈ {'process_env','hermes_env','agent_env'} or None.
    Mirrors src/agents/flyer/workflow.py::_read_env_value but exposes which file matched."""

def _flyer_provider_health() -> list[dict]:
    """Inspects OPENROUTER_API_KEY + OPENAI_API_KEY; calls source_edit_provider_ready({})."""

def _platform_runtime_health() -> list[dict]:
    """Wraps existing health.py helpers (gateway, bridge, paired) + cockpit deploy tag."""

def _cockpit_deploy_tag() -> tuple[str | None, str | None]:
    """Reads /opt/shift-agent/.commit-hash + scans deploys/ for newest deploy-*.tgz tag."""
```

### 2. Frontend: Health panel in `FlyerAdmin.tsx` overview tab

`FlyerHealthPanel` component rendered above the existing overview cards. `useQuery` with `queryKey: ["flyer-health"]` and a 30s `refetchInterval`. Two visually-distinct provider blocks so OpenRouter vs OpenAI is the dominant signal. Red/yellow/green colors come from backend `severity`, not derived in the frontend.

### 3. Source-edit posture clarification — focused backlog note

New file `tasks/flyer-source-edit-provider-posture-2026-05-20.md`:

- Today's state: source-edit hardcoded to OpenAI Images Edits API; generation/vision = OpenRouter. Two independent keys.
- Product question: is OpenRouter-only the desired posture, or is OpenAI-as-source-edit-dependency the long-term shape?
- Three options scoped (no decision yet):
  1. **Keep OpenAI for source-edit, surface as operational signal** (this PR).
  2. **Migrate to OpenRouter Image Edits** if/when the model supports `images/edits` semantics — verify capability before scoping.
  3. **Build a designer-asset fallback path** so source-edit-provider-unavailable is never a customer wait, just manual-queue routing.
- Trigger to revisit: any customer with a source-edit request waiting > 30 min, or the next `OPENAI_API_KEY` rotation/expiry.

**No code change to the provider gate in this PR.** The user's brief said "create a focused backlog/design note **and/or** guarded config path" — defaulting to the backlog note alone because flipping the provider is a product decision that needs operator input, not an implementation default.

### 4. Spend-gated real-model smoke hardening

`tests/test_flyer_golden_scenarios_real_model.py`:

- Add CI-env guard: even with `FLYER_GOLDEN_ALLOW_SPEND=1`, **skip with a loud reason** if `GITHUB_ACTIONS=true`, `CI=true`, `BUILDKITE=true`, or `JENKINS_URL` are set. Belt-and-suspenders against "operator copies env vars into a CI secret and forgets."
- Add credential-isolation guard: require `FLYER_GOLDEN_SPEND_PROFILE=isolated` alongside `FLYER_GOLDEN_ALLOW_SPEND=1`. Forces explicit acknowledgement of non-production credentials. Zero blast radius if forgotten — just refuses.
- Keep regression test that the smoke script refuses `--real-model` without `--allow-spend`.

`src/agents/flyer/scripts/smoke-flyer-quality`:

- Add the same CI-env detection inside the script: if `--real-model --allow-spend` is set but `CI`-like env vars are also set, refuse with exit code 3 and JSON `{"ok": false, "error": "refusing --real-model in CI environment"}`. Defense-in-depth.

**No new "production-quality image smoke path" is added** — the existing `--real-model --allow-spend` path already does this. Adding a duplicate would just be a second name for the same thing. If the operator wants higher visual-QA strictness, that's a separate Visual QA threshold change. Documented as deferred.

## Out of scope (deferred)

- **Source-edit migration to OpenRouter.** Captured in the new backlog note.
- **Real-time bridge ping graph / historical health.** Health panel is a current-state snapshot.
- **Visual QA strictness tuning for real-model smoke.** Separate Visual QA backlog item.
- **Pushover / Telegram alerts on provider degradation.** Read-only surface only.
- **OpenAPI schema regeneration.** Frontend types this PR adds are hand-typed `interface`s in `FlyerAdmin.tsx` (matching existing pattern) rather than auto-derived — keeps diff bounded.

## File ownership

| File | Why touched |
|---|---|
| `web/backend/app/routers/flyer.py` | New `GET /flyer/health` route + 4 private helpers |
| `web/backend/tests/test_flyer_admin.py` | New tests: auth gate, secret leakage assertion, provider-status shape, severity mapping, key-source, deploy-tag presence/absence |
| `web/frontend/src/sections/FlyerAdmin.tsx` | New `<FlyerHealthPanel>` component in overview tab |
| `src/agents/flyer/scripts/smoke-flyer-quality` | CI-env guard for `--real-model --allow-spend` |
| `tests/test_flyer_golden_scenarios_real_model.py` | CI-env guard + spend-profile guard |
| `tasks/flyer-source-edit-provider-posture-2026-05-20.md` | NEW — design/backlog note |
| `tasks/flyer-cockpit-p0-7-health-panel-plan.md` | NEW — this plan |

**Intentionally NOT touched:**

- `src/agents/flyer/workflow.py` — we **read** `source_edit_provider_ready` and `_read_env_value`, we don't modify them.
- `src/agents/flyer/render.py` — hardcoded `_openai_source_edit_bytes` stays as-is. Posture clarification belongs in the backlog note.
- Manual queue action UI — explicitly excluded per brief.
- Existing `/dashboard` route — we add a new `/flyer/health` rather than extending it.

## Tests

### Backend (`web/backend/tests/test_flyer_admin.py`)

- `test_flyer_health_requires_auth` — 401 without JWT cookie.
- `test_flyer_health_redacts_secrets` — even when `OPENROUTER_API_KEY=sk-...real...` is set, response body must not contain `sk-` (or the literal value). Hard assertion against raw JSON string.
- `test_flyer_health_openrouter_missing_is_red` — no key → `severity="red"`.
- `test_flyer_health_openai_missing_is_yellow_not_red` — no key → `severity="yellow"` (degraded, not hard block).
- `test_flyer_health_key_source_reported` — fake `/root/.hermes/.env` file with key → `key_source="hermes_env"`; same key in process env → `key_source="process_env"` (process env wins).
- `test_flyer_health_includes_model_config` — `draft_image_model`/`final_image_model`/`edit_image_model` exposed.
- `test_flyer_health_returns_deploy_tag_when_marker_present` / `..._is_null_when_marker_missing`.

### Smoke (`tests/test_flyer_golden_scenarios_real_model.py`)

- `test_real_model_smoke_refuses_in_ci_env` — set `GITHUB_ACTIONS=true` + `FLYER_GOLDEN_ALLOW_SPEND=1` + `FLYER_GOLDEN_SPEND_PROFILE=isolated` → smoke still refuses (exit 3).
- `test_real_model_smoke_requires_spend_profile` — `FLYER_GOLDEN_ALLOW_SPEND=1` without `FLYER_GOLDEN_SPEND_PROFILE=isolated` → pytest skips with loud reason.

### Frontend

- TypeScript build (`npm run build` or `npx tsc --noEmit`) — ensures the new component + types compile.
- No new unit-test infra (the repo doesn't have a jsdom/vitest baseline that's exercised today; matches existing FlyerAdmin pattern).

### Verification commands (final gate)

1. `pytest tests/test_flyer_golden_scenarios_real_model.py -v`
2. `pytest web/backend/tests/test_flyer_admin.py -v`
3. `python -m py_compile web/backend/app/routers/flyer.py src/agents/flyer/scripts/smoke-flyer-quality tests/test_flyer_golden_scenarios_real_model.py`
4. `cd web/frontend && npm run build` (or `npx tsc --noEmit` if available)
5. `git diff --check`
6. Optional sanity: `pytest tests/test_flyer_source_edit_preflight.py -v` (we read its helpers, regression check is cheap).

## Risks

| Risk | Mitigation |
|---|---|
| Health endpoint accidentally returns secret prefix in `detail` | Explicit redaction test. `key_present`/`key_source` are the only signals; no value substrings. |
| Subprocess to `/usr/bin/systemctl` fails on the test host | Existing `health.py::_gateway_active` already wraps in `try/except` → returns False. Mirror that. |
| Reading `/root/.hermes/.env` requires cockpit-user file permissions | Already true today — `workflow.py::_read_env_value` runs from the same user and reads both files. If unreadable, returns "" and reports `key_present=False`/`key_source=None`. Fail-closed, never raises. |
| Yellow vs red threshold for OpenAI source-edit contested | Surfaced clearly with `operator_note` linking to the backlog doc. Operator can change to red after using it. |
| Frontend re-renders too often | 30s `refetchInterval` is conservative. No streaming. No background polling outside the tab. |
| CI-env guard blocks a legitimate isolated runner | Gated on `GITHUB_ACTIONS`/`CI`/`BUILDKITE`/`JENKINS_URL` — none set on a local laptop. Operator can `unset CI` if certain (documented in smoke error message). |

## Deferred items (NOT in this PR)

- **Pushover/Telegram on provider degradation** — needs delta detection + notification audit. Separate PR after 1–2 weeks of read-only use.
- **Provider migration to OpenRouter-only** — see new backlog note.
- **Real-time bridge metrics / historical SLO** — out of scope for read-only first cut.
- **OpenAPI schema regeneration on the cockpit** — done as part of CI/build, not committed by hand.
- **Cockpit deploy-tag vs agent deploy-tag mismatch warning** — brief says "deploy tag", not "drift warning". P2-3 in the cockpit backlog covers reconciliation.

## Approval gate

This plan is awaiting operator approval before any code change. Once approved I will:

1. Implement backend route + frontend panel.
2. Write the new tests + smoke hardening.
3. Write the source-edit posture backlog note.
4. Run all verification commands and paste output before opening the PR.
5. Open PR with files changed / tests run / risks / deferred items per project rules.
6. Stop. No merge, no deploy.
