# Creative Director v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give the live deterministic premium render a validated marketing brief (hero / supporting / hook / offer-priority / theme / mood) — Hermes proposes, a deterministic resolver validates against locked facts with per-field fallback — and a dev-only vision-LLM art-director oracle to measure "correct premium" → "marketing poster."

**Architecture:** Extend the dormant `FlyerBrief`/`VisualDirection`/`FactRef` scaffold (`flyer_brief.py`); add a deterministic creative resolver (selection-validation with per-field defaults, separate from the strict anti-fabrication `validate`); extend the context builder so Hermes proposes the new fields; (Slice C) a standalone vision-LLM oracle. Slice B (wiring into render) is a separate later plan.

**Tech Stack:** Python 3, Pydantic v2, pytest. Worktree `C:\projects\sme-agents-cdv2`, branch `feat/flyer-creative-director-v2` off `origin/main 0912def`. Test cmd: `PYTHONPATH="src;src/platform" python -m pytest <path> -v`.

**Drift-check tag:** `extends-Hermes`. **Hermes-first:** Hermes proposes taste; deterministic resolver owns facts; oracle reuses `visual_qa` vision infra. (Full analysis in the design doc.)

**Hard boundaries (all slices):**
- Do NOT modify the strict firewall `validate` (`flyer_brief_validator.py:1156`), the QA/referee path, `visual_qa`'s verdict, or any dangerous-leak/safety classification. CD v2 adds NEW code; it never relaxes existing safety.
- All Hermes/vision calls are MOCKED/FAKE in tests — NO real OpenRouter spend.
- New brief fields are OPTIONAL/defaulted ⇒ the existing (dormant) CD path is byte-identical when they're absent.
- No render-path change in Slice A or C (that is Slice B). No deploy/activation.

---

## SLICE A — Creative Brief v2 brain (dormant)

### Task A1: Extend the brief schema

**Files:**
- Modify: `src/agents/flyer/flyer_brief.py`
- Test: `tests/test_flyer_brief.py` (create if absent; else append)

- [ ] **Step 1: Write failing tests** — assert: `VisualDirection(mood=...)` round-trips (default ""); `MarketingHook(text_ref=FactRef(fact_id="pricing_structure"), prominence="high")` validates and rejects `prominence` outside `{high,medium,low}`; `FlyerBrief(...)` accepts `hero_ref: Optional[FactRef]`, `supporting_refs: list[FactRef]` (default []), `marketing_hook: Optional[MarketingHook]` (default None), `offer_priority` ∈ `{high,medium,low}` (default "medium"); a `FlyerBrief` built WITHOUT the new fields still validates (backward-compat) and the new fields take their defaults.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — in `flyer_brief.py`: add `mood: str = Field(default="", max_length=120)` to `VisualDirection`; add a `MarketingHook(BaseModel)` (`model_config = ConfigDict(extra="forbid")`, `text_ref: FactRef`, `prominence: Literal["high","medium","low"] = "high"`); add to `FlyerBrief`: `hero_ref: Optional[FactRef] = None`, `supporting_refs: list[FactRef] = Field(default_factory=list, max_length=40)`, `marketing_hook: Optional[MarketingHook] = None`, `offer_priority: Literal["high","medium","low"] = "medium"`. Reuse the existing `FactRef` (do not duplicate it).
- [ ] **Step 4: Run → pass. Step 5: Commit** `feat(flyer): extend FlyerBrief with CD v2 creative fields (hero/supporting/hook/offer_priority/mood)`.

### Task A2: Deterministic creative resolver (the per-field firewall for creative fields)

**Files:**
- Create: `src/agents/flyer/flyer_creative_resolver.py`
- Test: `tests/test_flyer_creative_resolver.py`

Contract: `resolve_creative_direction(brief: FlyerBrief, locked_facts: Sequence[FlyerLockedFact]) -> ResolvedCreativeDirection` where `ResolvedCreativeDirection` is a frozen dataclass `{hero_name: str, supporting_names: list[str], hook_text: str, hook_prominence: str, offer_priority: str, theme_family: str, mood: str}`. Pure-deterministic, NEVER raises, NEVER invents a value (only selects from locked facts).

Per-field resolution + fallback:
- **hero_name**: if `brief.hero_ref` resolves to a locked `item:*:name` fact → its value; else default to the FIRST locked `item:*:name` value; else "".
- **supporting_names**: each `brief.supporting_refs` resolving to a locked `item:*:name` → its value (drop non-resolving); de-dup; exclude the hero.
- **hook_text / hook_prominence**: if `brief.marketing_hook.text_ref` resolves to a locked `pricing_structure` / `offer:*` / `offer_price` fact → its value + the model's prominence; else default to the `pricing_structure` value with prominence "high" if a `pricing_structure` exists; else "" / "low".
- **offer_priority**: `brief.offer_priority` if ∈ enum; else "high" when a `pricing_structure`/shared price exists else "medium".
- **theme_family / mood**: passthrough from `brief.visual_direction` (length-bounded by schema); no fact validation (pure taste).

- [ ] **Step 1: Write failing tests** — fabricated `hero_ref` (fact_id not in locked) → falls back to first item; `hero_ref` pointing at a non-item fact (e.g. `contact_phone`) → falls back to first item; supporting refs with one fabricated → that one dropped, others kept, hero excluded; fabricated `marketing_hook.text_ref` → falls back to `pricing_structure` value; no `pricing_structure` and bad hook → hook_text ""; `offer_priority="bogus"` → coerced to default; empty brief + locked facts present → all sensible defaults; empty locked facts → all "" (never raises); resolver NEVER returns a value absent from locked facts.
- [ ] **Step 2: Run → fail. Step 3: Implement** the resolver (read `FlyerLockedFact` shape from `src/platform/schemas.py`; reuse normalization helpers from `facts.py` if convenient but do not import render). **Step 4: pass. Step 5: Commit** `feat(flyer): deterministic creative resolver (per-field validate + fallback for CD v2 brief)`.

### Task A3: Hermes-propose extension (context builder emits the new fields)

**Files:**
- Modify: `src/agents/flyer/flyer_context_builder.py` (the `build_flyer_brief` brain prompt + response parsing)
- Test: `tests/test_flyer_context_builder_cdv2.py`

- [ ] **Step 1: Read** `flyer_context_builder.build_flyer_brief` (`:320`) + how it constructs the model prompt and parses the response into `FlyerBrief`, and how `creative_planner.build_creative_planner_provider` fakes a provider in tests. **Step 2: Write failing tests with a FAKE provider** (no network): a model response JSON that includes `hero_ref`/`supporting_refs`/`marketing_hook`/`offer_priority`/`visual_direction.mood` is parsed into the returned `FlyerBrief` (status "ok" path, with stubbed firewall); a response OMITTING the new fields → brief has them at defaults (None/[]/"medium"); a malformed new-field (e.g. `offer_priority="loud"`) → coerced/ignored, never raises, never blocks the whole brief. **Step 3: Implement** — extend the brain prompt to request the new optional fields and extend the parser to populate them (optional; absent → defaults). Do NOT change the strict `validate` or the four-status semantics. **Step 4: pass. Step 5: Commit** `feat(flyer): context builder proposes CD v2 creative fields (Hermes, optional, defaulted)`.

### Slice A close-out
- [ ] Full flyer test subset green (`tests/test_flyer_brief.py tests/test_flyer_creative_resolver.py tests/test_flyer_context_builder*.py` + existing `tests/test_flyer_*brief*`/`*validator*`). Then a Codex review of the Slice A diff; fix findings until CLEAN.

---

## SLICE C — Vision-LLM art-director oracle (dev tooling, standalone, non-blocking)

### Task C1: Oracle scorer module

**Files:**
- Create: `src/agents/flyer/flyer_art_director_oracle.py`
- Test: `tests/test_flyer_art_director_oracle.py`

Contract: `score_art_direction(image_path: str, *, brief_summary: str = "", provider=None) -> ArtDirectorScore` where `ArtDirectorScore` is a dataclass `{axes: dict[str, AxisScore], composite: float, overall_critique: str}` and `AxisScore = {score: int (1-10), critique: str}`. Axes (exact keys): `theme_clarity, hook_prominence, appetite_appeal, product_merchandising, offer_energy, brand_presence, would_i_post`. Reuses `visual_qa`'s image-read + vision-gateway call (read `visual_qa.py` for the existing vision-call helper; inject `provider` for tests). NEVER raises — on any error returns an `ArtDirectorScore` with empty axes + an error note in `overall_critique`.

- [ ] **Step 1: Read** `visual_qa.py` for the image-load + vision-call helper to reuse. **Step 2: Write failing tests with a FAKE provider** (no network): a well-formed model JSON (7 axes, each `{score, critique}`) → parsed into `ArtDirectorScore` with composite = mean of scores; missing/extra axes tolerated (clamp scores to 1-10, default missing critique ""); malformed JSON → safe empty score + error note, NEVER raises; scores out of range clamped. **Step 3: Implement.** **Step 4: pass. Step 5: Commit** `feat(flyer): vision-LLM art-director oracle scorer (dev-only, 7-axis + critique)`.

### Task C2: Sidecar writer + CLI

**Files:**
- Modify: `src/agents/flyer/flyer_art_director_oracle.py` (add `write_sidecar`)
- Create: `src/platform/scripts/score-flyer-art-direction` (CLI: `--image PATH [--brief-summary STR] [--out PATH]`)
- Test: `tests/test_flyer_art_director_oracle.py` (append) + a subprocess test mirroring `tests/test_catering_v02_scripts.py` style if the CLI warrants it

- [ ] **Step 1: Write failing tests** — `write_sidecar(image_path, score)` writes `<image>.artdirector.json` with the axis scores + critiques + composite (mirrors the existing `.qa.json` sidecar pattern); the CLI parses args and, given a fake provider via env/flag or an injected stub, writes the sidecar (or, if pure-network, gate the subprocess test so it does not hit the network — assert arg-parsing + that it does NOT raise without a key). **Step 2: fail. Step 3: implement** (`install -m 755` script; flat sys.path `/opt/shift-agent` style header like other scripts). **Step 4: pass. Step 5: Commit** `feat(flyer): art-director oracle sidecar writer + score-flyer-art-direction CLI`.

### Slice C close-out
- [ ] Oracle test file green; confirm the oracle imports without `PIL` issues under the flat layout (it may lazy-import like the overlay). NEVER wired into the render/QA path (standalone dev tool). Codex review of the Slice C diff; fix until CLEAN.

---

## SLICE B — wiring + scoped activation (SEPARATE later plan; handoff target, NOT built here)
Outline only: thread the resolved creative direction into the background prompt (`render.py:1306` hero string + `render.py:2052` scene seam) and `plan_premium_layout` (`premium_overlay.py:131`) behind `FLYER_CREATIVE_DIRECTOR_V2` (flag-off byte-identical); hero-emphasis mechanism chosen via oracle-guided iteration on real renders. Deploy + activation operator-gated.

## Self-review notes
- Coverage: schema (A1) ↔ resolver (A2) ↔ propose (A3) ↔ oracle (C1/C2) all map to design §4–§7. Slice B explicitly deferred.
- No placeholders: each task has a concrete contract + test intent + files. Code is written by TDD implementers against these contracts.
- Type consistency: `ResolvedCreativeDirection`, `MarketingHook`, `ArtDirectorScore`/`AxisScore` named consistently across tasks.
