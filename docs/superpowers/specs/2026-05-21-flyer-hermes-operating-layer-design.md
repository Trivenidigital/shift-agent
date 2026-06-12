# Flyer Hermes Operating Layer Design

**Drift-check tag:** `extends-Hermes`

**New primitives introduced:** Flyer operating-layer fixture schema, `operating_layer` self-evaluation section, operator-brief summary lines for that section.

## Goal

Expose what Flyer Studio can safely adopt from the latest Hermes capabilities without changing runtime behavior. The design deliberately uses the existing Flyer self-evaluation report as the carrier.

## Hermes-First Analysis

| Domain | Hermes skill/tool found? | Decision |
|---|---|---|
| Memory/session search | Yes — Hermes stores sessions and supports FTS/session search | Backlog use; this PR only reports if Flyer has enough durable brand/campaign facts to benefit |
| Background tasks | Yes — Hermes background/goal/Kanban execution | Backlog use; no new queue |
| Codex runtime | Yes — Hermes Codex app-server runtime | Backlog offline/staging improvement loop; no production self-modification |
| X/Grok | Yes — xAI OAuth and X search tools | Backlog opt-in publishing/search; no customer-visible posting |
| Video generation | Yes — Hermes tool registry can expose video generation | Backlog only |
| Flyer policy | No generic Hermes skill | Keep brand memory readiness, approvals, QA, and copy policy in Flyer code |

Awesome-Hermes-Agent ecosystem check: broad orchestration exists; no Flyer-specific operating-layer policy found. Use Hermes substrate; add Flyer policy interpretation.

## Data Contract

`OperatingLayerReadinessInput` is explicit JSON supplied to `tools/flyer-self-evaluation.py --operating-layer-input`.

Pydantic contract:
- `schema_version: Literal[1]`
- `model_config = ConfigDict(extra="forbid")` on every model
- nonnegative counts via `Field(ge=0)`
- literal enums for customer status, campaign status, rollout verdict, and source-edit posture
- `collected_at` required for the fixture and per-campaign QA evidence

```json
{
  "schema_version": 1,
  "collected_at": "2026-05-21T23:30:00Z",
  "customers": [
    {
      "customer_id": "CUST0001",
      "business_name": "Lakshmis Kitchn",
      "business_category": "restaurant",
      "preferred_language": "en",
      "status": "trial",
      "active_brand_assets": 2
    }
  ],
  "campaigns": [
    {
      "project_id": "F0065",
      "customer_id": "CUST0001",
      "status": "delivered",
      "final_asset_count": 4,
      "qa_passed": true,
      "qa_checked_at": "2026-05-21T23:20:00Z"
    }
  ],
  "rollout": {
    "verdict": "yellow",
    "source_edit_posture": "manual_review",
    "reasons": [{"severity": "yellow", "text": "source-edit runs through manual_review fallback"}]
  },
  "platform_truthfulness": {
    "instagram_story_truthful": false,
    "reason": "vertical/status image is still labelled Instagram story"
  }
}
```

The fixture is intentionally derived, not a live scanner. Future work can generate it from host posture, but this PR keeps it explicit and offline.

## Output Contract

`build_operating_layer_section()` returns:

```json
{
  "status": "yellow",
  "brand_memory": {
    "status": "ready_for_at_least_one_customer|yellow",
    "ready_customer_count": 1,
    "total_customer_count": 2,
    "coverage_ratio": 0.5,
    "reasons": []
  },
  "campaign_history": {"status": "ready|yellow", "completed_campaign_count": 1},
  "capabilities": [
    {"key": "persistent_brand_memory", "status": "ready_next", "owner": "Flyer+Hermes", "guardrail": "..."}
  ],
  "rollout_guard": {
    "status": "blocked|yellow|clear",
    "reason": "Customer rollout readiness remains the higher-priority gate."
  },
  "next_action": {
    "key": "source_edit_smoke_proof",
    "status": "blocked",
    "owner": "operator",
    "text": "Run 5-10 source-edit smoke cases before enabling automated source edits."
  },
  "deferred_backlog": [
    {"key": "native_video_conversion", "title": "...", "guardrail": "..."}
  ]
}
```

## Readiness Rules

Brand memory is `ready_for_at_least_one_customer` only when at least one customer has:
- nonblank business name,
- nonblank business category,
- preferred language,
- `status` in `trial` or `active`,
- at least one active brand asset,
- at least one delivered/completed campaign with a final asset, passing QA, and `qa_checked_at`.

Otherwise it is `yellow`.

The status is deliberately not named global `ready`: partial coverage is useful evidence for a first brand-memory pilot, not proof that all customers have enough memory substrate.

Rollout guard:
- `red` rollout verdict means operating-layer work is blocked.
- `yellow` rollout verdict means operating-layer is advisory only.
- missing rollout input means yellow: "rollout posture not supplied."
- resolved rollout source is exactly one object:
  - use self-evaluation `report["rollout"]` when `--rollout-readiness` is active;
  - otherwise use `OperatingLayerReadinessInput.rollout`;
  - if both exist and disagree on `source_edit_posture` or verdict, choose the more conservative state and emit a yellow conflict reason.

Source-edit:
- anything except smoke-proven/configured posture appears as deferred.
- no source-edit provider enablement in this PR.

Platform truthfulness:
- `platform_truthfulness.instagram_story_truthful == false` creates a blocker/warning under `multi_format_export_truthfulness`.
- This keeps the existing open backlog item visible until labels or story-safe assets are fixed.

## Backlog Keys

The report and `tasks/todo.md` must include all keys:

```text
persistent_brand_memory_readiness_signal
persistent_brand_memory_activation
session_search_campaign_history
background_render_qa_exports
xai_grok_provider_posture
x_search_fetching
x_social_posting_approval
codex_offline_self_improvement
native_video_conversion
auto_kanban_operator_work
multi_format_export_truthfulness
autonomous_campaigns_with_approval
campaign_analytics_memory
publishing_engine_approval_gates
marketing_os_long_term
source_edit_smoke_proof
hybrid_layout_final_renderer
```

Tests assert this list exactly, so backlog completeness is not hand-wavy.

## Integration

### `tools/flyer-self-evaluation.py`

Add:

```text
--operating-layer-input <path>
```

If present:
- load JSON,
- resolve rollout as `report.get("rollout") or input.rollout`, with conservative conflict handling if both exist,
- call `build_operating_layer_section(input, rollout=resolved_rollout)`,
- place result at `report["operating_layer"]`,
- append Markdown lines via `render_operating_layer_markdown`.

No input means current output remains unchanged.

### `tools/operator-brief.py`

No new argument. `summarize_flyer_evaluation_report()` checks for `payload["operating_layer"]` and appends short lines to the existing Flyer Self-Evaluation section.

## Static Safety

`src/agents/flyer/operating_layer.py` must not contain:
- `subprocess`
- `requests`
- `urllib`
- `socket`
- `write_text`
- `atomic_write`
- `ndjson_append`
- `open(`
- `ssh`
- `/opt/shift-agent`
- `/root/.hermes`

This is a policy helper, not a probe.

## Deferred Items

- Generate operating-layer fixture from live host posture.
- Persist Hermes memory entries for brand/campaign facts.
- Background render/QA/export jobs.
- Kanban sync for operator work.
- X/social posting with explicit customer approval.
- Video conversion after static flyer reliability is boring.
- Source-edit 5-10 case smoke proof.
- Platform package truthfulness for story/status/export labels.
