# Flyer Text QA Phase 3 Design

**Drift-check tag:** extends-Hermes

**Goal:** Add deterministic proof that Flyer Studio previews and final packages contain the current approved customer facts before any WhatsApp media send.

**New primitives introduced:** text-manifest sidecar format, deterministic fact collection, manifest validation at render/send chokepoints.

**Hermes-first analysis**

| Domain | Hermes skill found? | Decision |
|---|---|---|
| WhatsApp ingress/delivery | yes - existing Hermes gateway, cf-router, and bridge media endpoint | use it |
| Image generation | yes - existing Flyer renderer calls OpenRouter image models through Hermes-compatible runtime config | use it |
| OCR/vision | yes - Hermes supports vision and bundled OCR/document skills | do not add mandatory OCR in Phase 3; manifest QA is deterministic and cheaper |
| Exact flyer fact QA | none found | build local deterministic gate |

Awesome Hermes Agent ecosystem check: no ready-made exact-fact flyer QA skill was identified; this remains product logic layered on Hermes.

## Manifest Contract

Every customer-facing flyer artifact gets a sidecar next to the file:

`<artifact>.text.json`

For `F0001-whatsapp_image.png`, the sidecar is `F0001-whatsapp_image.png.text.json`. For PDF, it is `F0001-printable_pdf.pdf.text.json`.

Fields:

- `schema_version`: `1`
- `project_id`
- `project_version`
- `selected_concept_id`
- `output_format`
- `artifact_path`
- `artifact_sha256`
- `source_sha256`: raw/model background hash if available, otherwise empty
- `expected_facts`: canonical facts independently collected from the project
- `rendered_facts`: facts passed to the renderer/compositor
- `missing_fact_labels`
- `warnings`
- `ok`
- `created_at`

The gate passes only when:

- sidecar exists,
- project id/version/output match,
- artifact hash matches current bytes,
- every expected fact id appears exactly once in rendered facts,
- normalized rendered fact text equals normalized expected fact text,
- no rendered fact has been truncated,
- `ok` is true.

## Canonical Fact Collection

The fact collector lives in `render.py` to avoid a second runtime module unless implementation pressure proves otherwise. It returns ordered facts with labels and text.

Always include:

- title from `event_or_business_name`
- contact from `contact_info` when present or required
- location from `venue_or_location` when present

Include date/time when present. If no date exists but `_schedule_hint()` finds a recurring schedule, include schedule instead of inventing a date.

Include bounded detail facts from notes/raw request:

- price-bearing clauses such as `$16.99`, `$2/piece`, `14.99`
- phone-bearing clauses not already captured as contact
- menu/offer clauses up to the renderer capacity

If more price/menu clauses exist than fit, rendering fails with `FlyerRenderError("critical text facts do not fit")` instead of sending a partial flyer. Each fact has a stable `fact_id` such as `title`, `date`, `time`, `location`, `contact`, `schedule`, or `detail_001` so stale labels with old text cannot pass.

## Render Path

`_critical_lines()` is replaced by a fact-driven renderer input:

- `expected_facts = collect_text_facts(project)`
- `rendered_facts = fit_text_facts(expected_facts, size)`
- if any expected fact is omitted, raise `FlyerRenderError`
- render the `rendered_facts` lines
- write the sidecar after the artifact file exists
- validate the sidecar immediately

Both deterministic renderer and real-model overlay path call the same fact collection and sidecar writer. Deterministic previews no longer have a separate facts list.

PDF final generation writes a durable PDF sidecar derived from the same final artifact metadata, even when the overlaid source PNG is temporary.

## State And Send Gates

`finalize-flyer-assets` produces validated final assets but does not mark the project `delivered`. It leaves the project in `finalizing_assets` with `final_asset_ids` populated. `send-flyer-package` marks the project `delivered` only after all bridge sends succeed. If text QA or bridge delivery fails, the project remains recoverable and logs `flyer_delivery_failed` when a project is present.

This avoids the current false-terminal-state risk where finalized-but-unsent projects look delivered.

`send-flyer-package` validates every project final asset before bridge send. Direct `--asset` sends must also have a valid sidecar unless `--allow-unverified-asset` is passed. The bypass exists for operator break-glass only and is not used by normal approval flow.

Concept preview sending is checked before the bridge call. If a manifest is missing or stale, the preview send fails rather than presenting a bad design for approval.

Direct `--asset` break-glass has three guards:

- `--allow-unverified-asset`
- `--break-glass-reason`
- environment token `FLYER_TEXT_QA_BREAK_GLASS=1`

The bypass is not documented in Flyer SKILL instructions. It is path-contained under `/opt/shift-agent/state/flyer/` unless an operator runs it locally with explicit environment access. No new structured audit variant is added in Phase 3; project sends continue to use existing delivered/failed audit entries, while break-glass direct-asset sends print a structured JSON warning to stdout/stderr for operator capture.

## Smoke

`smoke-flyer-quality` keeps the existing concept smoke and adds:

- `text_qa` result per generated concept
- optional `--final-package` deterministic mode that renders all four final formats and validates sidecars
- stale-sidecar negative self-check by mutating one manifest in a temp directory and verifying validation fails

Deploy smoke uses deterministic rendering only but must call `smoke-flyer-quality --final-package` so all four final sidecars, including PDF, are checked. Real-model smoke remains opt-in behind `--real-model --allow-spend`.

## Rollback And Runtime

If all helpers stay in `render.py`, deploy already installs the updated module. If a helper module is introduced during implementation, deploy must install `/opt/shift-agent/flyer_text_qa.py`, smoke-test import it, and remove it on rollback when absent from older tarballs.

No persisted state data migration is required if manifests remain sidecars and no new state fields/statuses are added. If implementation adds a new audit variant, it must be added to `LogEntry`; Phase 3 avoids that by using existing delivery audit entries for project sends.

## Design Review Fixes

- Implementation review required exact `fact_id` plus normalized-text equality, not label-only matching.
- Implementation review required the render path to fail when facts are omitted or truncated, instead of writing a self-affirming manifest.
- Runtime review required moving the terminal `delivered` write from finalization to successful send.
- Runtime review required deploy smoke to exercise final-package text QA, not concept preview only.
- Runtime review narrowed break-glass direct asset sending with an env token, reason, and path containment.

## Test Plan

- Unit: fact collector includes revised date/time/price/title/location/contact.
- Unit: bounded menu facts fail when omitted.
- Unit: sidecar validation fails on stale project version, output hash mismatch, and missing fact.
- Render: deterministic concept creates valid image and sidecar.
- Render: final package creates four artifacts and four valid sidecars.
- Script: `smoke-flyer-quality --final-package` emits `text_qa.ok=true`.
- Script: `send-flyer-package` refuses an asset with no sidecar unless break-glass bypass is explicit.
