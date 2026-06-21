# CD v2 Brain-Output + Carrier-Safety Fix — Design/Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Why:** B3 (2026-06-21) proved CD v2's infrastructure works but the **Hermes brain emits no CD v2 fields** — it returns only the old-schema `theme_family`; `campaign_narrative`/`hero_ref`/`marketing_hook`/`offer_priority` come back empty, so nothing new renders. Root cause: the Creative Director SKILL output schema (`skills/flyer_generation/SKILL.md` lines 24-45) does not list the CD v2 fields, and the user-message describes them *nested* under `optional_creative_fields` while the parser reads them *top-level*. Separately, the `FlyerProject.creative_direction` carrier persisted into `projects.json` and broke rollback to `extra="forbid"` old code (migration-class hazard).

**Scope (operator-set):** (1) brain-output fix, (2) carrier/rollback safety, (3) live brain validation. **NO layout/composition work. No merge. No deploy.** Branches from `feat/flyer-creative-director-v2` HEAD `b51148b`.

**Drift-check tag:** `extends-Hermes`. **Hermes-first:** the brain is the Hermes SKILL.md system prompt; the fix is prompt-schema alignment + a deterministic carrier change.

---

## Part 1 — Brain-output fix

**1a. Extend the SKILL output schema** (`src/agents/flyer/skills/flyer_generation/SKILL.md`). In the "output exactly ONE FlyerBrief (JSON)" schema (lines 24-45) add, at the SAME nesting the parser/`FlyerBrief` use:
- top-level `"hero_ref": {"fact_id": "<id>"}` — the single most prominent item/offer, by fact id (or `{"raw_span": "..."}`).
- top-level `"supporting_refs": [{"fact_id": "<id>"}, ...]` — secondary items/offers.
- top-level `"marketing_hook": {"text_ref": {"fact_id": "<id>"}, "prominence": "high|medium|low"}` — the headline offer angle, text by ref.
- top-level `"offer_priority": "high|medium|low"`.
- top-level `"campaign_narrative": "<short grounded marketing message, evocative, NO prices/%/discounts/claims>"` (e.g. "South Indian Favorites at One Price").
- `"mood"` inside `visual_direction` (next to `theme_family`).
Add usage rules consistent with the existing rule #3 (no commercial VALUES in `campaign_narrative` — it's evocative-but-grounded; the deterministic scrub enforces this downstream). Update the worked EXAMPLE (lines ~140+) to populate the new fields so the model has a concrete pattern.

**1b. Flatten the user-message prompt** (`flyer_context_builder._build_user_message`). Replace the nested `"optional_creative_fields"` description with a flat note that the brief MAY include the new TOP-LEVEL fields (names matching 1a), referencing facts by id. The authority is the SKILL schema (1a); the user note just reinforces. Keep it short.

**1c. Tests** (`tests/test_flyer_context_builder_cdv2.py` + resolver test):
- A fake provider returning a brief with the new fields **at top level** (hero_ref/supporting_refs/marketing_hook/offer_priority/campaign_narrative + visual_direction.mood) → `propose_creative_brief_v2` parses them onto the `FlyerBrief` (not dropped).
- A **guard test** that the SKILL.md schema places these fields at the SAME level `_sanitize_cdv2_fields` reads them (so the prompt↔parser mismatch cannot regress): assert the SKILL.md output-schema block contains top-level `hero_ref`/`campaign_narrative`/`marketing_hook`/`offer_priority`/`supporting_refs` and `mood` under `visual_direction` (string-scan the SKILL.md).
- `resolve_creative_direction` consumes the parsed fields → ResolvedCreativeDirection carries hero/hook/narrative/priority (existing resolver tests stay green).

---

## Part 2 — Carrier / rollback safety (migration-class)

**Decision: make `creative_direction` non-persisted (`exclude=True`) + pass it to the overlay subprocess via the subprocess spec dict.** Rationale: the rollback hazard was the field being WRITTEN to `projects.json` where `extra="forbid"` old code rejects it. Excluding it from serialization means it is NEVER written → rollback-safe; in-memory reads (bg prompt + in-process overlay) are unaffected; the overlay subprocess (which reconstructs the project from `model_dump_json`) instead receives the direction through the existing premium-overlay subprocess spec.

**2a.** `src/platform/schemas.py`: `creative_direction: Optional[dict] = Field(default=None, exclude=True)`.

**2b.** `render.py` premium-overlay subprocess (`_render_premium_overlay_with_fallback` / `PREMIUM_OVERLAY_RENDERER`): add `creative_direction` to the subprocess spec dict (serialized separately) and have the subprocess renderer read it back and attach it to the reconstructed project before `render_premium_overlay`. In-process path unchanged (reads `project.creative_direction`).

**2c. Tests:**
- `model_dump_json(project_with_creative_direction)` does **NOT** contain `creative_direction` (rollback-safe); a strict-`extra="forbid"` shim loads that dump without error.
- In-memory `project.creative_direction` is still readable (exclude affects only serialization).
- The overlay **subprocess** still receives the direction via the spec dict (the narrative/seal render in the subprocess path) — unit-test the spec dict carries it + the renderer reads it.

**No deploy until Part 2 lands** (operator gate). Also note for the eventual redeploy: verify the SKILL.md deploys to the path `SKILL_MD_PATH` resolves to on the box (the deploy `rsync`s `skills/` to `/root/.hermes/skills/` but the brain reads `<module_dir>/skills/...`) — confirm before any redeploy (flagged, not in this pass's runtime since live-validation controls the path).

---

## Part 3 — Live brain validation (capped spend; NO deploy)

Run the FIXED brain against F0185/F0186/F0187 via a box scratch dir (scp the fixed `flyer_context_builder.py` + `SKILL.md` + `flyer_brief.py` + `flyer_brief_validator.py` + `flyer_creative_resolver.py` + the `visual_qa` shim), invoking `propose_creative_brief_v2` + `resolve_creative_direction` per project, printing the generated brief (campaign_narrative, hero_ref→hero_name, marketing_hook→hook_text, offer_priority, theme, mood). **Small capped OpenRouter spend (≈3 brain calls).** Acceptance — the brain produces useful, grounded fields, e.g.:
- Dessert → narrative ≈ "A Festive Dessert Celebration", hero ≈ Gulab Jamun.
- Combo → narrative ≈ "Weekend Combo Feast", hero ≈ a combo.
- Weekend → narrative ≈ "South Indian Favorites at One Price", hook ≈ "ANY ITEM $7.99", priority high.
If narratives are weak/empty, iterate the SKILL wording (still capped) before declaring success.

---

## Deliverables back to operator
Revised design/plan (this doc) · carrier/rollback strategy (Part 2) · sample generated briefs for F0185/86/87 (Part 3) · Codex review of the fix diff. **Then** (operator-gated) fix the SKILL.md deploy-path + carrier-subprocess, redeploy, re-run B3, judge composition.

## Boundaries
No layout/composition changes · no merge · no deploy · resolver/scrub/firewall behavior unchanged (only the brain SCHEMA + the carrier serialization change) · all unit tests fake-provider (no spend); only Part 3 spends, capped.
