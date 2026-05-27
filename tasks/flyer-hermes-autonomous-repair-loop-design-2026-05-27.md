**Drift-check tag:** extends-Hermes

# Flyer Studio Hermes Autonomous Repair Loop Design - 2026-05-27

## Purpose

Ship the first production-safe vertical slice of autonomous repair for generated-poster QA failures. The target failure class is F0105-style: render worked, customer facts existed, but QA rejected visible copy because the model duplicated/missed offer facts or leaked an unnecessary generic footer/title. Today that routes to `manual_edit_required`; after this slice, it gets one bounded Hermes/LLM-planned regeneration attempt before manual review.

## Design Review Findings Applied

- A SKILL contract alone is not Hermes-as-brain. The build must invoke an LLM/Hermes-style repair planner, or write a skipped audit row and fall back to manual review.
- F0105-style blockers must match current code: `detail_*`, visual-QA `item:N:name`, and `instruction text leaked...`, not synthetic `item:*` only.
- No new fields on `FlyerProject` in this PR. Project state survives rollback and `FlyerProject` is `extra="forbid"`, so repair attempts live in a separate rollback-safe state file.
- Deployed smoke must execute the installed `generate-flyer-concepts` CLI with temp state/assets/log. Import-only probes are insufficient.

## Hermes-First Design

Hermes remains the brain. Deterministic Python code is the executor and guardrail.

| Step | Owner | Rationale |
|---|---|---|
| Customer free-form interpretation | Hermes | Existing WhatsApp gateway, sender block, skills, and cf-router entrypoint. |
| Semantic flyer brief | Existing Flyer semantic brief / skill contract | Existing `flyer_semantic_brief.py`, locked facts, and generation skill describe account identity vs campaign/title. |
| Repair strategy for QA-failed generated poster | Hermes/LLM repair planner | The model reasons from brief + QA blockers + rendered text and chooses the repair instruction. |
| Repairability hard stops | Python | Wrong business/contact/price, missing facts, source-edit provider issues, and exhausted budget fail closed. |
| Retry budget and state | Python separate store | Prevents replay loops without mutating rollback-sensitive `projects.json` schema. |
| Render execution | Existing `render.py` | Reuses current OpenRouter/OpenAI/deterministic render path. |
| Preview/final send | Existing cf-router/send scripts | No new customer-visible send path. |

No Codex/Claude worker may send or repair customer flyers in this feature. Worker-draft/autodev is an engineering incident tool, not customer asset repair.

## Repair Attempt Store

Create a separate state file:

`/opt/shift-agent/state/flyer/autorepair_attempts.json`

Schema:

- `FlyerAutoRepairAttemptStore`
  - `schema_version: int = 1`
  - `attempts: list[FlyerRepairAttempt] = Field(default_factory=list, max_length=20000)`

- `FlyerRepairAttempt`
  - `attempt_id: str`
  - `project_id: str`
  - `project_version: int`
  - `mode: Literal["hermes_regenerate"]`
  - `status: Literal["attempted", "succeeded", "exhausted", "skipped", "stale"]`
  - `qa_blocker_hash: str`
  - `repair_instruction_hash: str`
  - `repair_instruction: str = Field(default="", max_length=1000)`
  - `started_at: datetime`
  - `completed_at: Optional[datetime]`
  - `generated_asset_ids: list[str] = Field(default_factory=list, max_length=10)`
  - `detail: str = Field(default="", max_length=1000)`

Retry budget is counted by `(project_id, project_version, qa_blocker_hash, mode)`. A pre-attempt row is written before rendering. If a process dies after `attempted`, a later run marks stale attempted rows `stale` after `auto_repair_attempt_stale_minutes` and writes an audit row before refusing or continuing according to budget.

## Hermes Repair Planner

Document the contract in `src/agents/flyer/skills/flyer_generation/SKILL.md` and implement a small planner client used by `generate-flyer-concepts`.

Planner input:

- semantic brief summary;
- controlled customer copy;
- QA blockers;
- rendered text/OCR/manifest sidecar when present;
- hard-contract flags: business identity, contact, prices, schedule, source authorization;
- previous repair attempts.

Planner output JSON:

```json
{
  "action": "regenerate_with_instruction",
  "repair_instruction": "Remove duplicate visible item lines. Show each controlled offer fact once. Do not add a generic footer title above the address.",
  "confidence": "high"
}
```

Allowed actions:

- `regenerate_with_instruction`
- `manual_required`
- `ask_customer`

Implementation uses the same provider substrate Flyer already uses for semantic/vision work: Hermes env files (`/root/.hermes/.env`, `/opt/shift-agent/.env`) and configured Flyer prompt/conversation model. If credentials or model calls are unavailable, the CLI writes `flyer_autorepair_skipped(reason="planner_unavailable")` and falls back to manual review. A deterministic planner may be used only in tests/shadow mode, never as production behavior.

## Repairability Rules

`src/agents/flyer/recovery.py` gets pure helpers:

- `classify_flyer_qa_for_autorepair(blockers, project) -> AutoRepairDecision`

Decision values:

- `hermes_plan_eligible`
- `hard_stop`
- `manual_required`

Eligible current blocker shapes:

- `missing rendered fact: detail_*`
- `manifest reports missing facts: detail_*`
- `missing required visible fact: item:N:name` when the corresponding item/offer facts exist in project notes or locked facts
- duplicate rendered fact ids for `detail_*`
- `instruction text leaked into flyer copy: ...` when it does not touch business/contact/price
- generic extra flyer title/footer leakage identified by visual QA and not overlapping protected facts

Hard stop:

- business/title identity mismatch;
- contact/address/phone mismatch;
- explicit price mismatch;
- missing facts that are absent from project fields/notes/locked facts;
- source-edit integrity mode;
- dependency/provider errors;
- no remaining repair budget.

Manual required:

- source-preserving edits;
- unknown blocker patterns;
- repeated repair failure after budget exhaustion.

## Generation Flow

`generate-flyer-concepts` remains the only generation CLI. It changes from one-shot to bounded repair:

1. Load project snapshot under state lock.
2. Render once using existing prompt.
3. If render and text/visual QA pass, persist concepts as today.
4. If render raises `FlyerRenderError`, classify the blocker.
5. If not eligible, append `flyer_autorepair_skipped` with classifier reason, then re-raise so existing caller routes to manual review.
6. If eligible but disabled/no budget, append `flyer_autorepair_skipped`, then re-raise.
7. Call the Hermes/LLM repair planner.
8. If planner returns `manual_required` or `ask_customer`, append `flyer_autorepair_skipped`, then re-raise.
9. Re-lock `projects.json`, re-read project, verify `project_id` and `version` still match. If not, delete artifacts, append `flyer_autorepair_skipped(reason="stale_project_version")`, and exit without consuming another attempt.
10. Under attempt-store lock, append `FlyerRepairAttempt(status="attempted")`.
11. Append `flyer_autorepair_attempted`.
12. Rerender with repair instruction in prompt.
13. Re-lock projects again and verify snapshot before attaching assets.
14. If QA passes, persist concepts/assets, mark attempt `succeeded`, and append `flyer_autorepair_succeeded`.
15. If QA fails, delete failed artifacts, mark attempt `exhausted`, append `flyer_autorepair_exhausted`, then re-raise.

The CLI never sends WhatsApp messages. Existing cf-router sends previews only after `trigger_generate_flyer_concepts()` returns success.

## Prompt Integration

`render.py` accepts an optional repair instruction for concept generation. `_image_prompt` adds this block only on the repair pass:

```text
Autonomous repair instruction:
- <instruction>
```

Only the prompt changes. No deterministic text removal or image paint-over happens in this slice.

## Audit

New LogEntry variants:

- `flyer_autorepair_attempted`
- `flyer_autorepair_succeeded`
- `flyer_autorepair_exhausted`
- `flyer_autorepair_skipped`

Required fields:

- `attempt_id`
- `project_id`
- `project_version`
- `mode`
- `qa_blocker_hash`
- `repair_instruction_hash`
- `detail`
- `generated_asset_ids`

`flyer_autorepair_skipped` is mandatory for disabled config, hard stop, no budget, unknown blocker, planner unavailable/malformed, stale project version, and planner action not equal to `regenerate_with_instruction`.

## Config

Add under `flyer.recovery`:

- `auto_repair_enabled: bool = True`
- `max_auto_repair_attempts: int = 1`
- `auto_repair_attempt_stale_minutes: int = 30`

Default is on because the path is bounded to one retry, does not send WhatsApp messages, and falls back to manual review when Hermes planning is unavailable or unsafe. This avoids rollback-breaking VPS config edits while making the deployed product behavior real. The retry budget is capped to one for the first PR.

## No-Live-Send Verification

Tests must not call `send_flyer_concept_previews`, `bridge_send_media`, or `bridge_post`. Verification happens by invoking `generate-flyer-concepts` with temp state, temp attempt store, temp config, temp audit log, and temp asset dir.

Post-deploy F0105-style verification must be dry-run/no-live-send first. Customer-visible delivery remains a separate operator decision after a QA-passing preview exists.

## State Safety

- Failed repair artifacts are deleted before returning failure.
- Cleanup includes preview PNG, `.text.json`, `.qa.json`, raw sibling, and any sidecar from the failed render attempt.
- No concept is attached unless QA passes.
- A successful repair writes only QA-passing assets after rechecking project version.
- A failed repair leaves the project for the existing manual-review path.
- The separate pre-attempt ledger prevents replay loops without changing `projects.json` schema.

## Tests

Focused test set:

- schema accepts `FlyerRepairAttempt` / `FlyerAutoRepairAttemptStore` and rejects unknown fields;
- rollback-safe test proves `FlyerProject` parsing is unaffected because attempts are stored separately;
- LogEntry union accepts all four new audit variants;
- repair classifier marks current F0105-style blocker strings eligible, including `detail_*` and `missing required visible fact: item:N:name`;
- repair classifier hard-stops wrong business/contact/price;
- retry flow writes attempted + succeeded to the attempt store and persists generated concept on second render;
- retry flow writes attempted + exhausted and attaches no concept after second failure;
- retry budget prevents a second retry;
- stale attempted row is marked `stale` before retry/budget decision;
- prompt includes repair instruction only on repair pass;
- malformed/unavailable planner writes skipped and does not retry;
- static/no-live-send test proves generation retry path has no bridge imports or calls.

## Deploy Verification

Smoke must run deployed-flat shape:

- execute `/usr/local/bin/generate-flyer-concepts` with temp `projects.json`, temp `autorepair_attempts.json`, temp config, temp assets, temp audit log, and no-live-send env;
- import `flyer_render` and autorepair helper from `/opt/shift-agent`;
- verify temp audit rows;
- do not invoke bridge sends.

Post-deploy health checks:

- inspect `flyer_autorepair_*` rows since deploy;
- inspect `cf_router_intercepted` details for preview `ack_error`;
- inspect `flyer_delivery_failed`;
- inspect recovery suppression/failure rows.

## Explicit Non-Goals

- No automatic final delivery.
- No source-preserving edit repair.
- No customer-facing status message changes.
- No Codex/Claude worker promotion.
- No image paint-over patching.
- No broad semantic brief parser rewrite in this PR.
