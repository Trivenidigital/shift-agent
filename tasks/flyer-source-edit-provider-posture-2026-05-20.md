**Drift-check tag:** extends-Hermes

# Flyer Studio — Source-Edit Provider Posture (design / backlog note)

## Context

Flyer Studio uses **two independent providers** for image work:

- **OpenRouter** — generation (draft + final) and vision extraction. `OPENROUTER_API_KEY`.
- **OpenAI** — exact source-preserving edits via the Images Edits API. `OPENAI_API_KEY`.

This asymmetry surfaced operationally on 2026-05-19 (lessons.md line 91-94):
"*Flyer Studio provider assumptions must distinguish OpenRouter generation/vision from the direct OpenAI source-edit path currently hardcoded in `render_source_edit_preview`. If the product posture is OpenRouter-only, source-edit provider selection/preflight is a backlog bug, not an operator misunderstanding.*"

P0-7 (this PR) makes the asymmetry **visible to the operator** via the new health panel. The posture *decision* — keep OpenAI for source-edit vs migrate to OpenRouter-only — is intentionally **not** taken here. This note captures the trade-off so the next deciding session has a grounded artifact.

## Hermes-first capability checklist

| # | Step | Tag |
|---|---|---|
| 1 | Image generation (text-to-image) | `[Hermes]` — Hermes LLM gateway already routes via OpenRouter |
| 2 | Vision extraction (image → structured) | `[Hermes]` — Hermes vision skill already routes via OpenRouter |
| 3 | Exact source-preserving image edit (input image → edited image, keeping the source layout) | `[net-new]` today — neither Hermes substrate nor OpenRouter has a verified `images/edits`-equivalent endpoint with source-image fidelity. The flyer codebase calls OpenAI's `/v1/images/edits` directly in `src/agents/flyer/render.py::_openai_source_edit_bytes`. |
| 4 | Manual-review fallback when provider is unavailable | `[Hermes]` — already implemented; `source_edit_provider_unavailable` `FlyerManualReviewReason` routes to cockpit manual queue |
| 5 | Operator observability for posture | `[net-new]` — newly added in P0-7 cockpit health panel |

**Verdict:** today's posture (OpenAI for step 3, OpenRouter for the rest) is **already what Hermes substrate supports**. Migration would require either a new OpenRouter endpoint or a different model that preserves source-image fidelity through normal generation. Both need verification before scoping.

## Drift-rule self-checks

- ✅ Read `src/agents/flyer/render.py` (`_openai_source_edit_bytes` at L1336, `render_source_edit_preview` at L1768, `_openrouter_image_bytes` at L1211) to confirm the asymmetry is real and where the provider gate lives.
- ✅ Read `src/agents/flyer/workflow.py` (`source_edit_provider_ready` at L298, `_read_env_value` at L259) to confirm the provider-ready gate already exists and routes via Hermes-managed env stores.
- ✅ Read `src/agents/flyer/manual_queue.py` (queueing helpers used by `source_edit_provider_unavailable` rows) to confirm the manual-review fallback path is live and tested.
- ✅ Read `src/platform/schemas.py` (`FlyerManualReviewReason` at L729, `source_edit_provider_unavailable` literal at L737) to confirm the reason code is canonical.

## Current reality (as deployed on `a0e853e`)

| Flyer Studio operation | Provider | Code path |
|---|---|---|
| Normal flyer generation (draft + final) | **OpenRouter** | `src/agents/flyer/render.py::_openrouter_image_bytes` (L1211) |
| Vision extraction (parse reference flyer) | **OpenRouter** (via Hermes vision) | `src/agents/flyer/visual_qa.py` + Hermes gateway |
| Exact source-preserving edit ("change date in this flyer") | **OpenAI Images Edits API** | `src/agents/flyer/render.py::_openai_source_edit_bytes` (L1336) |
| Source-edit fallback when provider unavailable | Manual review (operator) | `FlyerManualReviewReason.source_edit_provider_unavailable` → cockpit manual queue |

The OpenAI dependency for source-edit is **not currently configurable** — it is hardcoded in `_openai_source_edit_bytes`. The only switch today is "OPENAI_API_KEY present" vs "absent" (the latter routes to manual review with the existing reason code).

## Options (no decision yet)

### Option A — keep as-is, surface as operational signal (status quo, what this PR ships)

- Source-edit stays hardcoded to OpenAI.
- The new `/flyer/health` endpoint + cockpit panel make the dependency unmissable.
- When `OPENAI_API_KEY` is missing or expired, the cockpit shows yellow with manual-queue impact; operators can provision/rotate the key without code change.

**Cost:** zero engineering. **Risk:** OpenAI Images Edits remains a single-vendor dependency on the source-edit lane.

### Option B — migrate source-edit to OpenRouter Image Edits (if available)

Prereq: verify OpenRouter has a capability equivalent to `images/edits` that preserves source-image layout. Last-checked status as of 2026-05-20: **not verified**.

If/when verified:

1. Add a config gate `flyer.source_edit_provider` in `FlyerConfig` (`"openai" | "openrouter"`, default `"openai"`).
2. Add a provider switch inside `_openai_source_edit_bytes` (rename → `_source_edit_bytes`) that picks the right URL + auth based on config.
3. Keep `OPENAI_API_KEY` as a fallback during transition; the gate determines which key is required.
4. Update `source_edit_provider_ready` to honor the gate.
5. Add visual-QA regression on a real-flyer dataset to verify source fidelity is comparable.

**Cost:** ~150 LOC + visual QA regression dataset + provider-capability verification. **Risk:** silent degradation if OpenRouter's edits endpoint doesn't match OpenAI's fidelity.

### Option C — build a designer-asset fallback so source-edit-unavailable is never a customer wait

Today, `source_edit_provider_unavailable` rows queue to manual review. The operator must upload a designer-prepared asset and complete the row. This is the right safety net but it makes provider outage a human-cost event.

Future: a deterministic "near-source" path that uses Pillow + parsed locked facts to render a minimal-diff variant when the source-edit provider is down. Always a downgrade in quality vs a true source-edit, but bounded in operator labor.

**Cost:** ~300 LOC + visual QA bar + UX copy. **Risk:** customers may not accept the quality drop; needs clear messaging ("we made an automated edit, here is the safer version — reply to ask for a custom designer edit").

## Trigger to revisit this note

- Any customer with a `source_edit_provider_unavailable` queue row waiting **> 30 minutes** without operator action.
- The next time `OPENAI_API_KEY` rotates or expires.
- OpenRouter announces an `images/edits`-equivalent endpoint with verified source fidelity.
- The health panel shows yellow for ≥ 48h continuously.

## Out of scope

- Provider switch code path — see Option B. Not in this PR.
- Visual-QA regression dataset for source-edit — needed for Option B; not built yet.
- Designer-asset fallback path — see Option C. Not in this PR.
- **Real cockpit deploy marker** (see "Related follow-up" below).

## Related follow-up — real cockpit deploy marker

The P0-7 health panel surfaces a deploy marker labeled `shift_agent_deploy`
(top-level `shift_agent_deploy_tag` + `shift_agent_commit_hash`). The values
come from `/opt/shift-agent/.commit-hash` + the newest tarball in
`/opt/shift-agent/deploys/`, which describe **the agent tarball**, not the
cockpit. The cockpit (FastAPI + React) deploys separately via its own path;
if cockpit code is fresh but the agent tarball is stale (or vice versa), the
agent-deploy marker can be green while the cockpit is actually old.

This is correctly labeled now (post-review-fix). A future enhancement should
add a **real cockpit deploy marker** so the panel can surface both deploy
states distinctly:

- Cockpit deploy writes `/opt/shift-agent/web/.cockpit-commit-hash` (or
  equivalent) at install time.
- Health endpoint reads the cockpit marker into a second component
  (`cockpit_deploy`) alongside `shift_agent_deploy`.
- A mismatch warning surfaces when the two deploy hashes diverge by more
  than N hours (P2-3 in the cockpit backlog).

Trigger to schedule: any incident where the operator was confused about
which side (agent vs cockpit) was current.

## Acceptance for this note (P0-7 scope only)

- ✅ Health panel surfaces OpenRouter vs OpenAI distinction.
- ✅ Source-edit-missing renders as yellow/degraded (not red) with manual-queue impact when active.
- ✅ Detail string explicitly references "Exact flyer edits are falling back to manual review" when queued rows exist.
- ✅ Operator can see why exact edits are stuck without SSH or reading code.
