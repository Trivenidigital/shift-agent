# Creative Director v2 — Slice B Implementation Plan (narrative/hook-led render wiring)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Wire the validated CD v2 brief into the live mode-2 premium render so the delivered flyer leads with the **marketing message** (Campaign Narrative + Hook) over the food — lifting Message Clarity, Offer Energy, Product Merchandising, Hook Prominence — behind `FLYER_CREATIVE_DIRECTOR_V2`, scoped to `+17329837841`, flag-off byte-identical.

**Architecture:** Build + resolve the brief once in `_render_model`; carry the `ResolvedCreativeDirection` to both seams; (1) background — hero/theme/mood via the existing `scene_direction` seam + hero-name interpolation; (2) overlay — a narrative/hook-led composition (`plan_premium_layout` + `draw_offer_seal` + title/top-zone). Measure with the 8-axis oracle vs the re-baselined before-state.

**Tech stack:** Python 3, Pydantic v2, pytest. Worktree `C:\projects\sme-agents-cdv2`, branch `feat/flyer-creative-director-v2` (HEAD `04cff21`). Test: `PYTHONPATH="src;src/platform" python -m pytest <path> -v`. Commit specific files (never `-A`; 29 phantom-deleted long-path docs must never stage).

**Drift-check tag:** `extends-Hermes`. **Hermes-first:** Hermes proposes narrative/hook/hero (taste); deterministic resolver + scoped-scrub firewall own facts; oracle measures. Reuses the existing `scene_direction` seam, `plan_premium_layout`, `draw_offer_seal`, the validator's claim scanners, and the premium-overlay subprocess.

## Hard boundaries (ALL tasks)
- **No new render path.** No QA/referee/`visual_qa`-verdict changes. No fact-extraction changes (`facts.py` untouched). No global rollout. Behind `FLYER_CREATIVE_DIRECTOR_V2` + scoped allowlist; **flag-off byte-identical** (regression-tested).
- The strict firewall `validate` + 4 BriefStatus stay unchanged. Facts stay source-backed; dangerous-leak path untouched; oracle stays dev-only/non-blocking.
- All Hermes/vision calls MOCKED in tests (no OpenRouter spend until the operator-gated measurement run, B3).
- **Flag-off guard:** when the flag is off, NEVER call `build_flyer_brief` (it mutates `locked_facts` in place — `flyer_context_builder.py:513-514`); the carrier stays empty → existing bg string + payload-only overlay.
- **⚠ Gate flags for operator review of this plan:** Task B2.2 adds an optional field to `FlyerProject` (schema-additive, mirrors the existing `deterministic_recovery`, NO data migration). Task B3 is a render + vision-spend + scoped run (deploy/activation/spend) — **operator-gated, NOT executed without explicit approval.**

---

## PHASE B0 — Brief delta: Campaign Narrative (mostly dormant)

### Task B0.1: Schema — add `campaign_narrative`
- Modify `src/agents/flyer/flyer_brief.py`; test `tests/test_flyer_brief.py`.
- [ ] TDD: `FlyerBrief` accepts `campaign_narrative: str = Field(default="", max_length=200)`; backward-compatible (absent → ""); `extra="forbid"` preserved.
- [ ] Commit `feat(flyer): add campaign_narrative to FlyerBrief`.

### Task B0.2: Context builder proposes `campaign_narrative`
- Modify `src/agents/flyer/flyer_context_builder.py`; test `tests/test_flyer_context_builder_cdv2.py`.
- [ ] TDD (fake provider, no network): a model response with `campaign_narrative` → parsed into the brief (via the existing `_sanitize_cdv2_fields` path — string field, length-capped to 200, non-str dropped); omitted → ""; over-length → truncated. Add a concise prompt instruction requesting a short grounded marketing narrative.
- [ ] Commit `feat(flyer): context builder proposes campaign_narrative`.

### Task B0.3: Narrative scoped-scrub firewall (operator-approved Option B)
- Modify `src/agents/flyer/flyer_brief_validator.py` (add a public helper next to the existing scanners) + `src/agents/flyer/flyer_creative_resolver.py`; test `tests/test_flyer_creative_resolver.py`.
- Contract: `scrub_campaign_narrative(narrative, *, allowed_values, campaign_title) -> str`. Reuse existing scanners — reject (→ return `campaign_title`) if the narrative contains ANY of:
  - an **ungrounded commercial value** (price/%/discount not in `allowed_values`) — `_first_ungrounded_commercial`;
  - a **fabricated operational claim** (delivery, etc.) — the validator's operational-claim check;
  - a **fabricated scheduling / time-pressure claim** ("today only", "limited time", hours not grounded) — the scheduling-claim check;
  - an **ungrounded superlative/award/ranking** ("best", "#1", "award-winning", "voted", "number one") — the open-claim check + an explicit superlative set.
  - ALLOW soft evocative grounded language (feast / favorites / celebration / weekend treats / family favorites / classic flavors / one-price specials / authentic flavors / festive desserts). Empty narrative → "".
- [ ] TDD with the operator's allow/reject lists: each ALLOW phrase (over grounded facts) survives; each REJECT class (`"$5 off"`, `"50% off"`, `"today only"`, `"limited time"`, `"free delivery"`, `"award-winning"`, `"#1 biryani"`, `"best in town"`) → returns `campaign_title`; never raises.
- [ ] Wire it into the resolver so `ResolvedCreativeDirection` carries the validated narrative (Task B0.4).
- [ ] Commit `feat(flyer): scoped-scrub firewall for campaign_narrative (evocative-but-grounded; reject→campaign_title)`.

### Task B0.4: Resolver returns `campaign_narrative`
- Modify `src/agents/flyer/flyer_creative_resolver.py`; test `tests/test_flyer_creative_resolver.py`.
- [ ] TDD: add `campaign_narrative: str` to `ResolvedCreativeDirection`; `resolve_creative_direction` populates it via `scrub_campaign_narrative(brief.campaign_narrative, allowed_values=[locked values], campaign_title=<campaign_title fact value>)`. Pure, never raises, never invents. Existing resolver tests stay green.
- [ ] Commit `feat(flyer): resolver returns validated campaign_narrative`.

---

## PHASE B1 — Measurement delta: oracle 8th axis + re-baseline

### Task B1.1: Add Message Clarity axis to the oracle
- Modify `src/agents/flyer/flyer_art_director_oracle.py`; test `tests/test_flyer_art_director_oracle.py`.
- [ ] TDD (fake provider): `AXES` gains `"message_clarity"` (8 axes); the prompt asks "can a customer understand the primary offer within ~2 seconds?" as axis 1; parsing/clamp/composite over 8 axes; missing-axis tolerance preserved; never raises.
- [ ] Commit `feat(flyer): art-director oracle adds message_clarity axis (8-axis rubric)`.

### Task B1.2: Re-baseline F0185/F0186/F0187 on the 8-axis rubric  *(OPERATOR-GATED — small authorized vision spend)*
- [ ] RUN (not code): score the 3 existing baseline PNGs with the 8-axis oracle on the box (same dev-run mechanism: scratch dir + `visual_qa` shim + `gpt-4o-mini`), write sidecars, pull locally as `.cdv2-baseline8-F018x.png.artdirector.json`. This establishes the message-clarity before-state. ~3 vision calls.

---

## PHASE B2 — Render-path wiring (flag-gated; flag-off byte-identical)

### Task B2.1: New gate `_creative_director_v2_enabled`
- Modify `src/agents/flyer/render.py`; test `tests/test_flyer_renderer.py` (or a focused test).
- [ ] TDD: `_creative_director_v2_enabled(project)` mirrors `_premium_overlay_enabled`/`_deterministic_first_enabled` — `FLYER_CREATIVE_DIRECTOR_V2=="1"` AND phone ∈ shared `_premium_overlay_allowlist()`; const `CREATIVE_DIRECTOR_V2_ENV="FLYER_CREATIVE_DIRECTOR_V2"`. Off → False; scoped → True only for the allowlisted number.
- [ ] Commit `feat(flyer): FLYER_CREATIVE_DIRECTOR_V2 scoped gate`.

### Task B2.2: Carrier for the resolved direction (survives the overlay subprocess)
- Modify `src/platform/schemas.py` (FlyerProject) + a transient setter; test `tests/test_flyer_schemas*`/focused.
- [ ] TDD: add `creative_direction: Optional[dict] = None` (or a small typed sub-model) to `FlyerProject`, mirroring `deterministic_recovery` (`schemas.py:1971`) — optional, default None, **no data migration** (existing rows load with the default). It serializes through `model_dump_json()` so it reaches the `/usr/bin/python3` premium-overlay subprocess (`render.py:3068-3099`). Flag-off ⇒ stays None.
- [ ] Commit `feat(flyer): FlyerProject.creative_direction transient carrier (additive, default None)`.
  - *(If the operator prefers no FlyerProject field at plan review: alternative is to extend the premium-overlay subprocess spec dict (`render.py:3072-3079`) + `PREMIUM_OVERLAY_RENDERER` with a `creative_direction_json` arg + a ContextVar for the in-process bg side — no schema change, more plumbing. Default to the additive field unless told otherwise.)*

### Task B2.3: Build + resolve the brief once in `_render_model`
- Modify `src/agents/flyer/render.py`; test focused renderer test.
- [ ] TDD (fake `build_flyer_brief`): in `_render_model` (`render.py:4136`), when `_creative_director_v2_enabled(project)`, call `build_flyer_brief(...)` → `resolve_creative_direction(brief, project.locked_facts)` → store the `ResolvedCreativeDirection` on the carrier (B2.2). Set with a token / `finally`-reset semantics if any ContextVar is used. **Flag-off ⇒ build_flyer_brief NOT called** (assert the in-place `locked_facts.extend` never runs when off). Resolver/brief failure → carrier empty → render proceeds direction-blind (never blocks).
- [ ] Commit `feat(flyer): build+resolve CD v2 brief in _render_model (scoped, flag-off no-op)`.

### Task B2.4: Background seam — hero + theme/mood
- Modify `src/agents/flyer/render.py`; test focused.
- [ ] TDD: when the carrier holds a direction, (a) pass its theme/mood as the existing `scene_direction` kwarg through `_render_model`→`_openrouter_image_bytes` (`render.py:4142`) (reusing `_image_prompt`/`_scene_block_from_visual_direction`); (b) interpolate `hero_name` into the premium hero string in `_poster_layout_requirements` (`render.py:1306-1320`) — assert the hero name appears in the prompt. Flag-off / empty carrier ⇒ `scene_direction=None` + fixed hero literal (prompt byte-identical — regression assert).
- [ ] Commit `feat(flyer): CD v2 background — hero name + theme/mood into the textless-bg prompt`.

### Task B2.5: Overlay — narrative/hook-led composition + offer/hero emphasis
- Modify `src/agents/flyer/premium_overlay.py` (+ `render.py:_menu_overlay_payload` to surface the direction); test `tests/test_flyer_renderer.py` / overlay tests.
- Scope (tight — the 4 primary axes; full 6-level hierarchy fidelity is a follow-up):
  - **Narrative + hook as the dominant TOP element** (message clarity + hook prominence): render `campaign_narrative` and/or `hook_text` as a prominent top-zone message above the menu; sized to dominate. `render_premium_overlay` reads the direction off the carrier.
  - **`offer_priority` drives the offer seal** (offer energy): scale `draw_offer_seal` radius/weight + placement by priority (`high` = larger/bolder); thread `offer_priority` into `plan_premium_layout` + `draw_offer_seal` (`premium_overlay.py:127, 245`).
  - **Hero emphasis in the menu** (product merchandising): mark the `hero_name` row for emphasis (position/weight) above supporting items.
  - Respect the existing fail-closed fit/coverage ladder — emphasis that would overflow degrades to current layout (assert).
- [ ] TDD: direction present → narrative/hook drawn prominently, seal scales with priority, hero row emphasized; **all default/empty ⇒ byte-identical `PremiumLayout` + overlay (regression assert)**; over-emphasis that doesn't fit → falls back, never overflows; required-fact ledger (`premium_overlay.py:444-452`) still verifies every locked fact (no fact dropped for layout).
- [ ] Commit `feat(flyer): CD v2 overlay — narrative/hook-led composition + offer/hero emphasis (flag-off byte-identical)`.

### Phase B2 close-out
- [ ] Full flyer test sweep green (excl. the pre-existing contextvar flake); **flag-off byte-identical confirmed** end-to-end (a render with the flag off produces an identical prompt + overlay vs origin/main). Codex review of the Slice B diff; fix until CLEAN.

---

## PHASE B3 — Measurement & validation  *(OPERATOR-GATED: deploy + scoped activation + vision spend)*
- [ ] Deploy dormant → scoped-activate `FLYER_CREATIVE_DIRECTOR_V2=1` for `+17329837841` (mirrors prior flag activations; reversible).
- [ ] Re-render F0185/F0186/F0187 via `generate-flyer-concepts --project-id <id>` with the flag on.
- [ ] 8-axis oracle on the new renders; compare to the B1.2 re-baseline.
- [ ] **Success = meaningful lift in Message Clarity + Offer Energy + Product Merchandising (and Hook Prominence) with NO regression in fact correctness or dangerous-leak (QA verdict unchanged; leak=0).** Treat absolute oracle scores cautiously — use before/after deltas + operator's visual judgment as final arbiter.
- [ ] Operator reviews the rendered posters + the delta table → decide on widening / hero-emphasis iteration / Slice B follow-ups.

## Self-review
- Coverage: campaign_narrative (B0) ↔ message_clarity (B1) ↔ render wiring (B2) ↔ measurement (B3) map to design §2A + §4 + §6 + §7. Narrative firewall = operator-approved Option B.
- No placeholders; each task has a contract + test intent + files. Code via TDD implementers.
- Boundaries: no new render path / QA / fact-extraction / global rollout; flag-off byte-identical; schema-additive carrier + measurement run flagged for operator gates.
- Type consistency: `ResolvedCreativeDirection.campaign_narrative`, `scrub_campaign_narrative`, `_creative_director_v2_enabled`, `creative_direction` carrier, `message_clarity` axis named consistently.
