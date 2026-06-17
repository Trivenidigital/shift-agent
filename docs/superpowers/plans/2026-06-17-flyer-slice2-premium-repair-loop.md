# Flyer Slice 2 — Premium Repair Loop Implementation Plan

> Execute with TDD. Codex review at each checkpoint. Flag default OFF; flag-OFF path
> byte-identical to today. Safety classification UNCHANGED (reuse the existing gate).

**Goal:** image-to-image repair of the prior premium render when the referee flags a
*recoverable* text defect → ship `gpt.png`-grade premium instead of the flat overlay.

**Branch:** `feat/flyer-premium-repair-loop` off `origin/main`.

**Drift tag:** extends-Hermes. **Safety:** referee authoritative; fabrication +
wrong/unverified phone hard-block (never repaired); deterministic overlay = floor.

---

### Task 1 — Generic OpenRouter image-edit helper (render.py)

Refactor `_openrouter_source_edit_bytes` to delegate the API call to a new
`_openrouter_image_edit_bytes(*, base_image_path: Path, mime: str, prompt: str, size,
model, quality) -> bytes` (move the payload build + 3-retry urlopen + response parsing
there). `_openrouter_source_edit_bytes` becomes: resolve reference asset + build
`_source_edit_prompt`, then call the helper. **No behavior change.**

- Tests: existing source-edit tests still pass (mock urlopen); add a direct test of
  `_openrouter_image_edit_bytes` (mock urlopen → returns decoded bytes; HTTP error →
  FlyerRenderError).

### Task 2 — Repair-edit render mode + flag (render.py)

- `_openrouter_repair_edit_bytes(project, *, base_image_path, repair_instruction, size,
  model, quality) -> bytes` → calls `_openrouter_image_edit_bytes` with the prior render
  as base and the repair instruction as prompt.
- `render_repair_edit(project, base_png, output_dir, *, repair_instruction, model,
  quality="high") -> RenderedAssetSpec` → writes the edited bytes as `<id>-C1-preview.png`
  (the model's premium text, **NO `_apply_critical_text_overlay`**), `inspect_rendered_asset`
  quality-check (FlyerRenderError on fail + cleanup), `write_text_manifest`, return spec.
- Flag: `PREMIUM_REPAIR_ENABLED_ENV="FLYER_PREMIUM_REPAIR"`,
  `PREMIUM_REPAIR_ALLOWLIST_ENV="FLYER_PREMIUM_REPAIR_ALLOWLIST"`,
  `_premium_repair_enabled(project)` (mirror `_lean_prompt_enabled`: OFF unless "1";
  allowlist-scoped when set, else global).
- Tests: flag gating (off→False, on→True, allowlist scopes by sender); render_repair_edit
  returns a spec, does NOT overlay (the written bytes == the edit bytes); quality-check
  fail → FlyerRenderError + no orphan file.

### Task 3 — Scoped minimal-edit instruction (repair.py)

`build_premium_repair_instruction(blockers: list[str], locked: dict[str,str]) -> str` →
"Edit this exact flyer. Change ONLY: <clauses>. Keep every other element identical —
layout, colours, photography, fonts, and all other text. Do not recompose or restyle."
Clauses derived per blocker class:
- `inferred item not rendered: X` / `missing required visible fact: item:N:name` → "add the menu item 'X'(+ ' — $price' if locked)".
- `visible text defect … 'A' … 'B'` (misspelling) → "fix the spelling to '<locked name>'".
- `missing required visible fact: business_name` → "add the business name '<locked>' as the brand header".
- `missing required visible fact: schedule` → "add the schedule '<locked>'".
- Unknown/dangerous prefixes → omit (defensive; dangerous never reaches here).
- Tests: each class → expected clause; empty blockers → empty/no-op; never emits a
  fabricated value (only locked values).

### Task 4 — Additive first-rung wiring (scripts/generate-flyer-concepts)

After the initial render + `failed_qa`, BEFORE the existing recovery rungs, insert
(flag-gated):
```
if (failed_qa and not source_edit_requested and _premium_repair_enabled(current)
    and not _qa_failed_has_fabrication(failed_qa)
    and _qa_failed_exact_text_recoverable(failed_qa, locked_fact_ids={...})):
    emit FlyerPremiumRepairAttempted
    base_png = specs[0].path
    for attempt in 1..2:
        instruction = build_premium_repair_instruction(blockers, locked_required+items)
        try: repair_specs = render_repair_edit(current, base_png, asset_dir, repair_instruction=instruction, model=draft_model, quality=high)
        except FlyerRenderError: break  (generation error → fall through)
        re-QA repair_specs
        if repair passes:
            cleanup old specs; specs=repair_specs; qa_reports=repair_qa; failed_qa=[]
            emit FlyerPremiumRepairSucceeded; integrated passes downstream; break
        else if repair still recoverable & not fabrication: base_png=repair; blockers=residual; continue
        else: discard repair; break  (introduced dangerous / non-recoverable → fall through to existing ladder/hard-block)
    if still failed_qa: emit FlyerPremiumRepairExhausted  (fall through to existing ladder)
```
Invariants: dangerous never enters (gate excludes fabrication/wrong-phone); a repair that
introduces a dangerous blocker is caught on re-QA → discarded → existing hard-block path;
bounded ×2; flag-OFF → block skipped entirely (byte-identical). Version-snapshot guard +
artifact cleanup as elsewhere.

- Tests (integration, mock render_concept_previews/render_repair_edit/run_visual_qa):
  recoverable defect + flag on → repair runs → pass → awaiting_final_approval + repaired
  asset + FlyerPremiumRepairSucceeded; repair fails ×2 → FlyerPremiumRepairExhausted +
  falls through to existing fallback (overlay); flag off → no repair calls, byte-identical;
  fabrication → repair NEVER called (hard-block preserved); repair that introduces a
  fabrication on re-QA → discarded → manual (no fabricated ship).

### Task 5 — Observability LogEntry types (schemas.py)

Add `FlyerPremiumRepairAttempted` / `Succeeded` / `Exhausted` (subclass `_BaseEntry`,
`type: Literal[...]`, project_id, project_version, attempts, reason). Tests: validate.

### Task 6 — Suite + review + deploy + validate + merge

- Full flyer suite + generate-concepts + schemas tests green.
- Confirm no deploy-install change needed (all in render.py/repair.py/schemas → already
  installed flat). If repair.py signature unchanged, no install edit.
- Codex review (read-only) — checkpoint after Tasks 1-3, and after Task 4-5.
- Deploy dormant (flag OFF) → smoke.
- Validate: offline battery on would-be-fallback renders (budget-aware: ~$4.7 headroom →
  if a full battery exceeds it, PAUSE and ask for top-up — billing stop condition).
  Then flag flip scoped to +17329837841 + operator-sent real cases (Claude cannot send
  WhatsApp → operator drives the live cases).
- Merge once production == main + numbers meet the pre-registered bar (≥60% would-be-
  fallback → premium, 0 dangerous reaching approval, visual ≥ flat baseline).
