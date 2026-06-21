# Creative Director v2 — Composition Pass — Design

**Date:** 2026-06-21
**Status:** Design for review (no implementation until the plan is approved).
**Drift-check tag:** `extends-Hermes` — composition-layer only. Reuses the existing brain output (`request_intent`, `campaign_narrative`, `hero`, `marketing_hook`, `offer_priority`, `theme`, `mood`) and the existing deterministic premium-overlay renderer. Adds a deterministic **archetype router** + three overlay composition templates. No change to the brain, firewall, carrier mechanism, QA/referee, dispatcher routing, fact extraction, or deterministic-first routing.

## 1. Problem
The deterministic premium overlay renders ONE fixed hierarchy: **Brand → Narrative(eyebrow) → Title → Food → Menu**. The brand emblem is top + dominant; the narrative is a small eyebrow *subordinate to the title*; the title is the largest text. So the **title leads, not the message** ("eyebrow-sized," confirmed in the B3 measured re-render). And — the deeper insight — **different campaign types need different heroes:** a bucket-biryani combo flyer should lead with the offer ("FREE APPETIZER & DESSERT"); a festival dessert flyer should lead with the occasion ("FESTIVAL DESSERT CELEBRATION"); a weekend-specials menu should lead with the message ("South Indian Favorites at One Price"). One composition cannot serve all three.

Target hierarchy: **Narrative/Hook → Offer → Food → Menu → Brand** (the bucket-biryani order: lead element biggest → product → proof → brand small).

## 2. Goal & scope
**Make the message the hero — with the right hero per campaign type.** In scope: a deterministic `poster_archetype` router + three overlay composition templates (A/B/C), driven by the EXISTING brain fields. Out of scope (explicitly untouched): brain (Hermes propose), firewall (scrub/validator), carrier mechanism (`creative_direction` exclude=True + subprocess delivery), QA/referee, dispatcher routing, fact extraction, deterministic-first routing. Composition only.

## 3. The archetype router (new)
**New field `poster_archetype: "message_first" | "offer_first" | "event_first"`**, selected DETERMINISTICALLY **after the brief is resolved**, from `request_intent` + `offer_priority`. It is the composition router: the overlay reads it and renders the matching template. It is added to the carried `creative_direction` dict (the carrier *mechanism* — exclude=True + subprocess-spec — is unchanged; we add one key to the dict it already carries). Selection lives in a new deterministic component `select_poster_archetype(request_intent, offer_priority, resolved_direction)` called at carrier-build time (post-resolution) — the resolver and firewall are NOT modified.

**Selection mapping (primary = `request_intent`; `offer_priority` modulates emphasis + escalation):**
| request_intent | poster_archetype | reference |
|---|---|---|
| `event` | **event_first (C)** | festival dessert |
| `combo_offer` | **offer_first (B)** | bucket biryani |
| `menu` / `new` / `source_edit` | **message_first (A)** | weekend specials |

`offer_priority` scales the hero element WITHIN the chosen archetype (high = larger/bolder). Open decision (phase-tuned): an escalation rule (e.g. a `menu` flyer with an exceptionally dominant single shared offer + `offer_priority=high` could escalate to B) — default is the table above; no escalation in Phase 1.

## 4. The three archetypes (how each uses the 4 fields)

Shared moves across all three: **brand demotes** from dominant-top emblem to a small top-corner / footer lockup; the locked `campaign_title` becomes a small kicker (or merges into the lead); the **fail-closed fit ladder + required-fact ledger are preserved** (every locked fact still verified; emphasis that overflows degrades to a safe layout; narrative/hook are best-effort and dropped before any required fact).

**A — Message-first poster** (default; menu/new). `campaign_narrative` = the **dominant headline** (top third, largest type). `marketing_hook` = strong sub-headline; `offer_priority=high` scales it. `hero_product` drives the food backdrop. `campaign_title` → small kicker. *The message is the biggest thing.*

**B — Offer-first poster** (combo_offer; bucket-biryani). `marketing_hook` + price = the **dominant centerpiece** (oversized seal/badge, deal-poster style); `offer_priority=high` → maximal seal. `campaign_narrative` = supporting tagline above. `hero_product` = backdrop. *The offer is the hero.*

**C — Event-first poster** (event; festival). `campaign_narrative` = **event headline**, framed by `theme`/`mood` (occasion banner); **schedule elevated** (events are about *when*); `marketing_hook` = the "what's on" offer line; `hero_product` = atmospheric; brand at the very bottom. *The occasion is the hero.*

## 5. Where it plugs in (composition only)
- `select_poster_archetype(...)` (new) — called at carrier-build (`_populate_creative_direction_v2`, post-resolution); writes `poster_archetype` into the `creative_direction` dict.
- `premium_overlay` (overlay composition) — reads `poster_archetype` and routes to the A/B/C layout/draw plan; each template arranges the existing zones (kicker/headline/hook/seal/hero/menu/footer) per its hierarchy + type scale. Default (no archetype / flag off) = today's layout (byte-identical).
- No upstream component changes. Flag-gated by the existing `FLYER_CREATIVE_DIRECTOR_V2`, scoped to +17329837841, flag-off byte-identical.

## 6. Phased implementation (architect for A/B/C now; build incrementally)
- **Phase 1 — Build A (message_first) only.** Router + the archetype field + the A template. **B and C fall back to A** (or to today's layout) until built — the router exists, the templates don't yet diverge. Validate lift on a message-first campaign (e.g. weekend specials F0187) via real renders + operator eye + oracle delta.
- **Phase 2 — Add B (offer_first).** Route combo_offer → B. Validate combo/price-driven (F0186 + a bucket-biryani-style brief).
- **Phase 3 — Add C (event_first).** Route event → C. Validate seasonal/event (F0185 festival dessert).
Each phase: TDD + Codex-clean + flag-off byte-identical + real-render validation; no merge/deploy without operator approval.

## 7. Validation
"Message is the hero" pass bar (operator's eye is the arbiter; oracle is directional): the lead element (narrative for A, offer for B, occasion for C) is visually DOMINANT — larger than the title, reads first in ~2s — measured against the bucket-biryani reference. Real renders of F0185/86/87 per phase + the 8-axis oracle before/after delta (acknowledging message_clarity's ceiling).

## 8. Hermes-first analysis
| Domain | Hermes skill? | Decision |
|---|---|---|
| Campaign→layout archetype selection | none (project-internal composition grammar) | build — deterministic router over existing brief fields |
| Poster composition / typography | none (our deterministic Pillow overlay) | extend the existing premium overlay |

awesome-hermes-agent: no ecosystem skill for per-campaign poster composition; this is project-internal. Verdict: `extends-Hermes`, composition-only.

## 9. Safety / preserved guarantees
- Facts unchanged (source-backed); firewall/scrub unchanged (the narrative/hook are already firewalled before they reach composition); QA/referee unchanged; carrier mechanism unchanged (one key added to the carried dict); flag-off byte-identical; scoped to +17329837841.
- Fit ladder + required-fact ledger preserved in every template; the lead/hero element is best-effort and never displaces a required fact.

## 10. Open decisions (phase-tuned, non-blocking)
- Exact `offer_priority` escalation/tiebreaker (e.g. menu + dominant offer → B). Default: request_intent table; no escalation in Phase 1.
- Where `poster_archetype` is computed (a standalone `select_poster_archetype` component vs a field on `ResolvedCreativeDirection`). Recommended: standalone component at carrier-build (keeps the resolver focused on fact-resolution). Routine — resolved at plan time.
- Phase-1 fallback for B/C: fall back to today's layout vs to A. Recommended: today's layout (smallest surface) until each template ships.
