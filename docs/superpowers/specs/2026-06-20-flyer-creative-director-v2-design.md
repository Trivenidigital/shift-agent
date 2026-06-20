# Flyer Creative Director v2 — Marketing-Brief → Render Wiring — Design

**Date:** 2026-06-20
**Status:** Design for review (no implementation until the plan is approved).
**Branches from:** `origin/main 0912def` (the deployed flyer code; the local `feat/flyer-premium-whatsapp-quality` working tree has the flyer files deleted — an unrelated in-flight branch — so CD v2 work happens in the worktree `C:\projects\sme-agents-cdv2` off `origin/main`).
**Scope:** customer `+17329837841` (Lakshmi's Kitchen, CUST0001), behind a scoped flag.

**Drift-check tag:** `extends-Hermes` — reuses the existing dormant `FlyerBrief`/`VisualDirection`/`FactRef` contract (`flyer_brief.py`), the existing firewall (`flyer_brief_validator.py`), the existing context builder (`flyer_context_builder.build_flyer_brief`), the existing `scene_direction` prompt seam (`render.py:2052`), and the existing deterministic premium overlay (`premium_overlay.py`). No new render path; no schema migration of `FlyerProject`; QA/referee/dangerous-leak path untouched.

**New primitives introduced:** (1) extend `FlyerBrief`/`VisualDirection` with `hero_ref`, `supporting_refs`, `marketing_hook`, `offer_priority`, `mood`; (2) extend the firewall to validate them → per-field deterministic defaults; (3) extend the context builder so Hermes proposes them; (4) thread the validated brief into the premium background prompt + `plan_premium_layout`; (5) a dev-only vision-LLM art-director oracle (`flyer_art_director_oracle.py`) that scores the delivered PNG.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Marketing judgment (hero/hook/theme/mood selection) | Hermes vision+text gateway is the substrate "brain"; no canned skill for *flyer* hero/hook selection | **use Hermes** — Hermes proposes the brief (taste); deterministic firewall owns facts. Matches the project "Hermes proposes → Python referees" rule + the existing `creative_planner` propose-then-firewall pattern. |
| Fact authority / anti-fabrication | none (project-internal `flyer_brief_validator.py`) | **reuse** — extend the existing firewall to validate the new refs |
| Vision read-back of a rendered image | in-tree `visual_qa` already reads the rendered PNG via the vision gateway | **reuse** — the oracle reuses the same image-read + vision-call infra |
| Structured-JSON model output | Hermes gateway native (used by `creative_planner`) | **reuse** — the CD v2 provider mirrors `build_creative_planner_provider` |

awesome-hermes-agent ecosystem check: per-flyer marketing direction + art-direction scoring is a project-internal creative concern over our own locked-fact grammar; no Hermes/ecosystem skill overlaps. Verdict: extend in-tree scaffold → `extends-Hermes`.

---

## 1. Problem (the bottleneck has moved)

The 3-brief validation set (post deterministic-first + reconciliation deploy `deploy-20260620-201819-0912def3`) proved facts are now trustworthy and premium renders deliver. It also relocated the bottleneck:

- **Dessert** worked — hero selection was obvious (Gulab Jamun) and matched the offer.
- **Combo** worked — the hero naturally represented the offer.
- **Weekend Specials** was weakest — the background hero represented *a dish*, not *the offer*; nothing decided which item is the hero or that "ANY ITEM $7.99" is the headline.

Root cause (grounded): the live mode-2 premium path is **direction-blind**. The background directive is a fixed string (`render.py:1306-1320`) that says "ONE hero dish" but never names which; the overlay planner (`premium_overlay.py:131 plan_premium_layout`) chooses layout purely from item count + price structure — no hero, no offer priority, no theme. There is no marketing hook, hero selection, or offer-priority logic anywhere (`render.py`/`premium_overlay.py` grep: none found).

This is a **product-design** problem (marketing judgment), not an infrastructure problem. The fix is to give the premium render a *creative brief* and let it influence composition.

## 2. Goal & success metric

Move the delivered flyer from **"correct premium"** to **"marketing poster"** — measurably. Success is tracked by the art-director oracle (§7) across iterations: composite rubric score rises, with hero/hook/offer axes leading. Hard constraints unchanged: facts stay source-backed, dangerous-leak = 0, QA remains the delivery gate.

**In scope:** Creative Brief v2 (schema + Hermes-propose + firewall) and its wiring into (a) background generation and (b) deterministic overlay composition; the dev-only oracle.
**Out of scope:** the integrated render path; the old separate CD render path in `bare_render.py` (`FLYER_CREATIVE_DIRECTOR_ENABLED`); QA/referee changes; schema migration; global rollout.

## 3. Current State (grounded)

**Fields that already exist** (`src/agents/flyer/flyer_brief.py`, dormant behind `FLYER_CREATIVE_DIRECTOR_ENABLED=1`):
- `VisualDirection` (`flyer_brief.py:39-50`): `theme_family`, `palette[]`, `motifs[]`, `visual_subjects[]` — visual taste only.
- `FactRef` (`flyer_brief.py:53-91`): exactly one of `fact_id` / `raw_span`; provenance derived; the anti-fabrication reference mechanism.
- `OfferGroup` (`flyer_brief.py:94-114`): typed offer structure by locked-fact id.
- `FlyerBrief` (`flyer_brief.py:117-139`): `request_intent`, `offer_structure`, `visual_direction`, `layout_strategy`, `grouping`, `must_not_add`, `background_brief`, `fact_refs[]`, `offer_groups[]`.

**What is unused / discarded:**
- The whole brief is dormant. On the armed CD path (`bare_render.py:1115 _render_creative_director_grounded`) the renderer consumes **only `background_brief`** (`bare_render.py:1171`); `visual_direction`/`offer_groups`/`layout_strategy` are dropped.
- `visual_direction` reaches a prompt only via the advisory `FLYER_SKILL_DRIVEN_SCENE` path (`_scene_block_from_visual_direction`, `render.py:1998-2058`), which is `None` on the premium/deterministic path.

**What reaches the renderer on the live mode-2 premium path (`+17329837841`):**
- Background = fixed string `render.py:1306-1320` (consumes no brief field).
- Overlay = `render_premium_overlay` (`premium_overlay.py:366`); `plan_premium_layout` (`premium_overlay.py:131-161`) picks `menu_mode` from item count, `offer_mode = "seal" if shared_price else "inline"/"none"` (`premium_overlay.py:160`) — no hero/priority/theme; palette hard-coded (`premium_overlay.py:334-336`).

**Reusable infrastructure that already exists:** firewall (`flyer_brief_validator.py`, 82KB — fact-ref validation + commercial grounding `_commercial_value_hit`/`_first_ungrounded_commercial` + open-claim checks); context builder (`flyer_context_builder.build_flyer_brief` `:320`, `advise_scene_direction` `:396`); the `scene_direction` injection seam (`render.py:2052`); `select_campaign_scene` (`campaign_scene_prompts.py:114`, deterministic 3-template campaign classifier — the default theme source); `visual_qa` image read-back (for the oracle).

## 4. Creative Brief v2 (schema extension)

Extend the existing models. **Every content-bearing field is a `FactRef`** so the firewall guarantees no fabrication — names/prices remain the source-backed `FlyerLockedFact`s the reconciliation work hardened.

`VisualDirection` (add one field):
- `mood: str = Field(default="", max_length=120)` — e.g. "Warm Restaurant Promo".

`FlyerBrief` (add four fields):
- `hero_ref: Optional[FactRef] = None` — must resolve to a locked `item:*:name`.
- `supporting_refs: list[FactRef] = Field(default_factory=list, max_length=40)` — each resolves to a locked `item:*:name`.
- `marketing_hook: Optional[MarketingHook] = None` (new sub-model): `{ text_ref: FactRef, prominence: Literal["high","medium","low"] = "high" }` — the displayed hook is the **value of the referenced locked fact** (a `pricing_structure` / `offer:*` / `offer_price`), never an inline string.
- `offer_priority: Literal["high","medium","low"] = "medium"` — drives overlay offer emphasis.

Note on "Campaign": the operator's example lists `Campaign: Weekend Specials`. The campaign **name** is the already-extracted `campaign_title` locked fact (no new field needed); `request_intent` is the campaign **class** (menu/combo/event/…); `select_campaign_scene` supplies the default **scene/theme** when Hermes proposes none. CD v2 adds no separate campaign field — it reuses `campaign_title` + `request_intent`.

Example (Weekend Specials), all refs pointing at locked facts:
```
request_intent:      menu                        (campaign class)
campaign_title:      "Weekend Specials"          (existing locked fact — the campaign name)
hero_ref:            item:1:name  -> "Dosa"
supporting_refs:     item:0:name "Idli", item:2:name "Vada", item:3:name "Uttapam"
marketing_hook:      text_ref=pricing_structure ("ANY ITEM $7.99"), prominence="high"
offer_priority:      high
visual_direction:    theme_family="South Indian Weekend Feast", mood="Warm Restaurant Promo"
```

## 5. Architecture: Hermes proposes → firewall validates → render consumes

Data flow on the live mode-2 premium path (no new render path — an enrichment step):

1. Mode-2 premium reached (deterministic-first + `_premium_overlay_enabled` + food/grocery) — unchanged.
2. **NEW gate** `_creative_director_v2_enabled(project)` = `FLYER_CREATIVE_DIRECTOR_V2=="1"` AND phone ∈ `FLYER_PREMIUM_OVERLAY_ALLOWLIST` (mirrors `_premium_overlay_enabled`/`_deterministic_first_enabled`). Off → today's direction-blind render (byte-identical).
3. **Hermes proposes** the brief: extend `build_flyer_brief` (a CD v2 provider mirroring `build_creative_planner_provider`) to emit `hero_ref`, `supporting_refs`, `marketing_hook`, `offer_priority`, `theme_family`, `mood` over the request + locked_facts.
4. **Firewall validates** (extend `flyer_brief_validator.py`), per field, with **deterministic fallback** (operator-set — never block, degrade):
   - `hero_ref` must resolve to a locked `item:*:name` → else default to the first locked item name (or none).
   - `supporting_refs` filtered to those resolving to locked item names; non-resolving dropped.
   - `marketing_hook.text_ref` must resolve to a locked `pricing_structure`/`offer:*`/`offer_price` AND pass the existing commercial-grounding check (no fabricated price) → else default to the `pricing_structure` value if present, else no hook.
   - `offer_priority` ∈ enum → else default `high` when a shared price/`pricing_structure` is present, else `medium`.
   - `theme_family`/`mood`/`palette` = visual taste, length-bounded only (no fact risk) → else default theme from `select_campaign_scene`.
   - Hermes unavailable/timeout/invalid JSON → the **all-defaults brief** (render still enriched by deterministic hero/hook/priority), never a hard block.
5. **Render consumes** the validated brief through the two seams (§6).
6. **Dev-only** art-director oracle scores the delivered PNG (§7) — after send, non-blocking.

Facts never flow through Hermes as values — only as `FactRef`s the firewall resolves; this preserves the source-backed invariant end-to-end.

## 6. Wiring (the two seams — smallest change)

**Seam 1 — background generation.** When CD v2 is enabled and a brief exists:
- Inject the resolved `hero_ref` name into the hero-dish string (`render.py:1306-1320`): "…ONE single mouth-watering hero dish — **{hero}** — as the bold subject…".
- Supply `scene_direction = brief.visual_direction` to the existing seam (`render.py:2052`) so `theme_family`/`mood`/`motifs`/`palette` render into the scene block (`_scene_block_from_visual_direction`). Currently `None` on the premium path; CD v2 populates it.
- Flag-off / no brief → the fixed string + `scene_direction=None` (byte-identical).

**Seam 2 — overlay composition.** Extend `plan_premium_layout` (`premium_overlay.py:131`) and the draw sites with **optional** direction:
- `hero` (name) → the hero item gets visual emphasis (top position and/or larger card / accent rule). Exact mechanism chosen during build + tuned via the oracle.
- `offer_priority` → scales the offer seal (`draw_offer_seal`, `premium_overlay.py:245-294`): `high` = larger/bolder seal; `low` = inline/smaller.
- `marketing_hook` (prominence `high`) → becomes the dominant kicker/seal text (the headline element), not buried in the menu.
- **Default `None` for all = today's uniform layout (byte-identical).** Emphasis must respect the existing fail-closed fit/coverage check — a larger hero card that overflows degrades to the current layout rather than overflowing.

## 7. Vision-LLM art-director oracle (dev-only measurement)

A new module `flyer_art_director_oracle.py`, **strictly development/iteration tooling — never a customer-facing gate, never part of QA/dangerous-leak**.

- Reuses `visual_qa`'s image-read + vision-gateway call. After a render, prompts a vision model with the 7-axis rubric and returns structured JSON: per axis `{score: 1–10, critique: "<one short sentence>"}` plus `composite` and `overall_critique`.
- **Axes:** 1) Theme clarity 2) Hook prominence 3) Appetite appeal 4) Product merchandising 5) Offer energy 6) Brand presence 7) Would-I-post-this?
- **Persists with the artifact:** writes a sidecar `<preview>.artdirector.json` beside the rendered PNG (mirrors the existing `.qa.json` / `.text.json` sidecars) so versions are comparable across iterations.
- **Non-blocking + isolated:** gated by its own dev flag `FLYER_ART_DIRECTOR_ORACLE=1` (OFF in prod by default; ON for CD v2 dev). Any oracle error is logged and ignored — it never affects delivery, never reads or mutates the QA verdict. Dangerous-leak / fact-correctness remain entirely in the QA path.

## 8. Flagging & scoping

- New flag `FLYER_CREATIVE_DIRECTOR_V2` + shared `FLYER_PREMIUM_OVERLAY_ALLOWLIST` (scoped `+17329837841`). Distinct from the dormant `FLYER_CREATIVE_DIRECTOR_ENABLED` (which gates the *other* render path).
- New dev flag `FLYER_ART_DIRECTOR_ORACLE` (default OFF).
- Flag-off ⇒ byte-identical render (verified by regression test). Deploy lands dormant; scoped activation is a separate operator-gated step (as with every prior flyer slice).

## 9. Safety / preserved guarantees

- Facts are never invented: Hermes proposes only `FactRef`s; the firewall resolves them against locked_facts; the reconciliation-hardened source-backed facts are the only values rendered.
- Per-field deterministic fallback ⇒ a weak/failed Hermes brief degrades gracefully (weaker direction), never blocks or fabricates.
- QA/referee/dangerous-leak path untouched and remains the sole delivery gate; the oracle is aesthetic-only and isolated.
- Flag-off + no-brief paths byte-identical; scoped to one number; reversible by unsetting the flag.

## 10. Testing strategy

- **Schema:** pydantic round-trip for the new fields; `MarketingHook` validation; `FactRef` reuse.
- **Firewall:** fabricated hero (not a locked item) → dropped → default hero; fabricated hook price → dropped (commercial-grounding) → default/none; non-resolving supporting refs filtered; `offer_priority` enum coercion; Hermes-unavailable → all-defaults brief (no block).
- **Wiring seam 1:** brief present ⇒ hero name appears in the background prompt + scene block carries theme/mood; flag-off ⇒ prompt byte-identical.
- **Wiring seam 2:** `plan_premium_layout` responds to hero/`offer_priority`/hook; `None` args ⇒ byte-identical `PremiumLayout`; hero emphasis that would overflow degrades via the fit check.
- **Oracle:** parses model JSON → sidecar written; oracle exception does NOT fail the render; oracle never touches the QA verdict; flag-off ⇒ oracle not invoked.
- **Regression:** existing flyer suite green; flag-off byte-identical; CI send-path pytest at PR.
- **Codex** at each slice; full suite green.

## 11. Build slices (decomposition — one reviewed slice at a time)

- **Slice A — Creative Brief v2 brain (dormant):** extend schema + firewall + context builder (Hermes-propose). No render change. TDD + Codex. Ships dormant.
- **Slice B — wiring (scoped activation):** thread the validated brief into background + overlay composition behind `FLYER_CREATIVE_DIRECTOR_V2`; flag-off byte-identical. TDD + Codex + deploy-gated. This is the slice that changes delivered flyers (scoped).
- **Slice C — art-director oracle (dev tooling):** the non-blocking vision scorer + sidecar. TDD + Codex. Used to measure A→B impact.

Recommended order A → C → B (build the brain, stand up measurement, then wire + measure the lift). Each slice is operator-gated for deploy.

## 12. Residual risks & open decisions

- **Hermes hero/hook judgment quality** (the whole bet) — measured by the oracle across iterations; if weak, tighten the propose prompt (not the firewall).
- **Oracle scoring variance** (vision-LLM) — fixed prompt + low temperature + persisted sidecars for apples-to-apples version comparison; treat as directional, not absolute.
- **Overlay emphasis vs fit** — hero/seal emphasis must respect the existing fail-closed fit/coverage ladder; never overflow.
- **Open decision (flag name):** new `FLYER_CREATIVE_DIRECTOR_V2` (recommended) vs reuse dormant `FLYER_CREATIVE_DIRECTOR_ENABLED` — recommend new, to avoid colliding with the old CD render path.
- **Open decision (hero emphasis mechanism):** top-position vs larger card vs accent treatment — defer to Slice B build + oracle-guided visual iteration on real renders.
