# Flyer Studio — Bounded Creative Planner + Product Truth-Contract (DESIGN)

**Drift-check tag:** `extends-Hermes` — Hermes does all creative inference (it already
owns the LLM semantic call); this design adds a thin custom *contract* layer (provenance
typing + a hard-fact firewall + intent-aware QA + a clarification gate) on top of the
existing substrate. It **replaces** a brittle hardcoded creative-fill mechanism
(`FAMOUS_ITEM_SETS`) with a Hermes planner and **fixes a live provenance bug**. No new
storage/identity/messaging substrate; no model-renders-text; no change to hard-fact
extraction or the truth guarantees.

**Status:** DESIGN ONLY — **no code**. This is the stake-in-the-ground + the build
checklist. Requires Codex review (5 lenses, §14) **and** operator approval of scope +
the first slice before any implementation.

**As of:** `origin/main` `b11bf28` · 2026-06-01. (Verify gaps against `origin/main`, not
backlog docs, per `docs/flyer-studio-current-state.md`.)

---

## 0. Problem & thesis (the stake in the ground)

**The product promise:** a customer types something vague ("make me a flyer, 8 famous
South Indian breakfast items, any item $8.99, Sat/Sun 8–11am") and gets a beautiful,
commercially-usable first draft. That promise is *why customers pay* instead of pasting
into ChatGPT.

**What the system does today (verified):** it acts as a *compliance extractor*, not a
creative director. The pipeline is `vague prompt → extract ONLY source-grounded facts →
lock → render locked facts → QA locked facts`. Three independent gates suppress any
creativity:

1. **Prompt forbids it** — `semantic_brief.py:350` "Do not invent prices, dates, **items**,
   phone, address, or business identity." Items are lumped with hard facts.
2. **Deterministic post-filter strips it** — `_source_ground_brief` (`semantic_brief.py:273`)
   keeps only content literally grounded in the customer's text: `offers = [o for o in
   brief.offers if _grounded(source, o.text)]`. Creative content cannot survive even if the
   model produces it.
3. **No schema slot + temp 0** — `FlyerSemanticBrief` (`semantic_brief.py:52`) has no
   `items` field, and the provider runs at `temperature: 0.0` — architecturally an extractor.

**The live flaw, with evidence (this is not greenfield):** someone already recognized the
need and built a *hardcoded* creative-fill: `_requested_famous_item_facts`
(`facts.py:298`) matches a rigid regex (`include N famous/popular/top <category> items`)
into a static dictionary `FAMOUS_ITEM_SETS` (`facts.py:279`) that contains **exactly one
category — `indo-chinese`**. It emits the first N hardcoded names and — critically —
**tags the inferred item *names* `source="customer_text"`** (`facts.py:314`), i.e.
*system-inferred content mislabeled as customer-provided truth*. (The paired `$8.99`
at `:318` is a customer-stated value and is correctly grounded; the inferred part is the
item name + the item↔price association — see §4/F6.) So today: "8 famous **South Indian**
breakfast items" matches the regex shape but finds no category set → returns `[]` → the
weak/blank flyer. And on the one path that *does* fire (`indo-chinese`), the assumptions
silently pass the grounding filter + QA as if the customer typed them, and are never
surfaced as assumptions. This is the "custom flyer brain, done badly" anti-pattern: a
static dictionary that can't scale to restaurants/groceries/salons/tutoring/temples/…, a
brittle trigger, and a provenance violation.

**The wrong target vs the right target.** "Never invent" was a reasonable response to a
real liability — a *paid* tool that sends an SMB's marketing to real customers cannot
fabricate prices/dates/hours/claims. But it **over-applied**, banning inferred *items*
(safe to infer when asked) alongside *prices/dates/identity/claims* (never safe). The
right target is:

- **Never invent risky facts** (price, date, contact, identity, discount, legal/service claims).
- **Freely infer harmless creative content** when the customer asks for it (items, headlines, layout, mood).
- **Make every assumption visible** (provenance + customer copy).
- **Let the customer revise in one tap.**

**Thesis:** *unhandcuff Hermes* (extractor → extractor **+** bounded creative planner) plus
**one load-bearing custom contract**. The intelligence is 100% Hermes. The custom code only
defines the rules of engagement: what Hermes may infer, what it may never infer, how
inferred content is tagged, and how QA proves the flyer satisfied the request. **Not a
custom flyer brain** — the opposite: this design *supersedes* the hardcoded `FAMOUS_ITEM_SETS`
brain with Hermes (removing it at per-category enablement, §9 slice 5 — never breaking the
flag-off kill switch).

This is the **B1 non-bounded architectural effort** in `docs/flyer-studio-current-state.md`
(highest blast radius — the cf-router area took 7 Codex rounds). It is cross-cutting
(intake → brief → facts → render → QA → customer copy → audit), so it ships in flagged
slices (§9), never as a quick patch.

---

## 1. Hermes-first analysis — `[Hermes]` / `[net-new]` step table (MANDATORY)

End-to-end flow for a vague request, each step tagged. "Hermes-built" = existing substrate
to reuse; "net-new" = contract code we must write.

| # | Step | Tag | Evidence / note |
|---|------|-----|-----------------|
| 1 | Receive vague WhatsApp prompt; identity, media cache | `[Hermes]` | Hermes ingress/identity substrate |
| 2 | Extract **hard facts** (grounded, never invented) | `[Hermes-built]` | `semantic_brief.py` extractor + `_source_ground_brief`; **unchanged** |
| 3 | Classify: supported category? safe-to-infer? else clarify | `[Hermes + extend]` | `intent.py` `clarify`/`needs_clarification` already exist (`:47`,`:109`) — wire to planner |
| 4 | Infer **safe creative content** (items, headlines, section labels, layout, imagery direction) | `[Hermes]` | New planner *mode* of the existing provider call (`semantic_brief.py:64`); **replaces** `FAMOUS_ITEM_SETS` |
| 5 | Tag inferred content `source="hermes_inferred"` | `[net-new]` | Extend the source literal `{customer_text, customer_profile}` (`facts.py:22-23`) — small |
| 6 | **Hard-fact firewall** — reject creative output that writes/alters hard-fact fields OR embeds hard-fact-class claims in free text | `[net-new]` | Mirror `validate_flyer_intent_decision` (`intent.py:246`) — **the load-bearing piece** |
| 7 | Feed assumed items into the deterministic item/overlay renderer | `[Hermes-built]` | `_menu_item_lines`/`_locked_menu_item_lines` + overlay (`render.py`) already draw item rows |
| 8 | Generate background imagery | `[Hermes-built]` | existing `render.py` image path (OpenRouter) |
| 9 | **Intent-aware QA** — count/coverage + pricing reconciliation + no-fabricated-hard-fact, on top of locked-fact presence | `[net-new + reuse]` | Extend `visual_qa.py` `_item_name_present` (`:287`) / `_value_present_in` (`:244`) |
| 10 | Customer reply surfacing assumptions + one-tap revision handles | `[Hermes + extend]` | `customer_copy_policy.py` + approval/`manual_queue.py` exist; copy is Hermes |
| 11 | On customer approve/edit → provenance `hermes_inferred → customer_confirmed` | `[net-new]` | Lifecycle transition — small |
| 12 | Audit every step (decisions.log) | `[Hermes-built]` | NDJSON chokepoint substrate |

**Tally:** `[Hermes]`/`[Hermes-built]` = steps 1,2,4,7,8,10,12 (**7**) — all the intelligence
+ rendering + delivery. `[net-new]` = steps 5,6,9,11 (**4**) — *all contract enforcement*,
and 5/9/11 are extensions of existing patterns; only the firewall (6) is substantially new.
Step 3 is reuse-and-extend.

**Red-flag check:** net-new (4) < half of 12 → passes. Confirms "mostly Hermes." And the
intelligence line is **net-negative custom code**: the hardcoded brain is ultimately
superseded by a Hermes prompt (the planner is a strictly larger, smarter replacement).

**What gets superseded — and WHEN (de-drift, reconciled with the kill switch, F1).**
`FAMOUS_ITEM_SETS` (`facts.py:279`) + `_requested_famous_item_facts` (`facts.py:298`) + its
call site (`facts.py:530`) are the brittle path the planner replaces. **They are NOT deleted
in the planner slice.** While `flyer.creative_planner=off`, the hardcoded path stays
**untouched** so flag-off is byte-identical (§9). The planner is wired as an **alternate
producer** gated by the flag; the hardcoded path is removed only at **per-category
enablement** (§9 slice 5), and only once the planner demonstrably covers that category
(today: only `indo-chinese`). `_generic_item_price` (`facts.py:267`) is retained regardless —
extracting a *stated* per-item price is hard-fact extraction, not inference.

---

## 2. Drift-rule — read-deployed-code (MANDATORY)

Read on `origin/main` before drafting; each established a load-bearing fact above.

| Read `path` | What it established |
|---|---|
| Read `src/agents/flyer/semantic_brief.py` | The extractor prompt (`:350`), the deterministic grounding filter (`:273`), the `FlyerSemanticBrief` dataclass with no items field (`:52`), temp-0 provider (`:66`). The planner is a *second mode* of `SemanticBriefProvider` (`:64`). |
| Read `src/agents/flyer/facts.py` | The hardcoded `FAMOUS_ITEM_SETS` (one category, `:279`); the item-name provenance mislabel (`:314`); the **7-value** source set `ALLOWED_NEW_PROJECT_FACT_SOURCES` (`:21`) and the explicit merge-priority dict in `merge_locked_facts` (`:559`); the `_fact(... source ...)` model; the integration call site (`:530`). |
| Read `src/agents/flyer/render.py` | The deterministic item/overlay renderer (`_menu_item_lines`/`_locked_menu_item_lines`) already draws item rows — image model does **not** render text. Assumed items reuse this path. |
| Read `src/agents/flyer/visual_qa.py` | QA today checks **locked-fact presence** via OCR (`_value_present_in` `:244`, `_item_name_present` `:287`), not intent satisfaction. Intent-QA extends these. |
| Read `src/agents/flyer/intent.py` | `FlyerIntentDecision` (`:101`) already has `clarify`/`route_current` actions (`:47`), `needs_clarification`/`clarifying_question` (`:109-110`), and a validator `validate_flyer_intent_decision` (`:246`) — the firewall + clarification gate reuse these patterns. |
| Read `src/platform/schemas.py` | `FlyerLockedFact` carries a `source` field; `FlyerProject`/`FlyerVisualQAReport` are the state + QA models the new fields thread through. |

**Deployed-pattern compliance:** storage stays JSON-on-disk + audit via the NDJSON
chokepoint; provenance is a new *value* on an existing field (not a new store); the firewall
mirrors the deployed `validate_*` pattern; no SQLite/parallel substrate. Divergence to flag:
the planner runs a second LLM call (or one dual-section call) — a model-usage change, not a
substrate change; bounded by §10 cost note.

---

## 3. Architecture — the two contracts

```
                      ┌─────────────────────────────────────────────┐
vague customer  ──▶   │ (3) classify: supported category? safe?      │
prompt                │     ambiguous hard fact? → CLARIFY (intent)  │
                      └───────────────┬─────────────────────────────┘
                                      │ (supported + safe)
        ┌─────────────────────────────┴───────────────────────────┐
        ▼                                                           ▼
(2) GROUNDED EXTRACTOR  (unchanged)                 (4) BOUNDED CREATIVE PLANNER (new mode)
   temp 0, grounding-filtered                          temp > 0, NOT grounding-filtered
   → hard facts only                                   → items, headlines, section labels,
   price/date/phone/address/identity/                    layout plan, imagery direction
   discount/claims  (source=customer_text/                (source=hermes_inferred)
   customer_profile)
        │                                                           │
        └──────────────┬────────────────────────────────┬─────────┘
                       ▼                                  ▼
            (6) HARD-FACT FIREWALL  ◀── rejects creative content that writes/alters a
                       │                hard-fact field OR embeds a hard-fact-class claim
                       ▼
            (5)(11) PROVENANCE-TYPED FACT SET  (FlyerLockedFact + source + lifecycle)
                       │
                       ▼
            (7)(8) DETERMINISTIC RENDERER  (item rows + overlay; image bg)  — reused as-is
                       │
                       ▼
            (9) INTENT-AWARE QA  (locked-fact presence + count/coverage + pricing recon
                       │           + no-fabricated-hard-fact)
                       ▼
            (10) CUSTOMER COPY: surface assumptions + one-tap revision  ──▶ (11) confirm/edit
```

**Slot-in points:** the planner is a new branch at `facts.py:530` (where
`_requested_famous_item_facts` is called today) and a new prompt/mode beside
`build_hermes_semantic_brief_provider` (`semantic_brief.py`). Everything downstream of the
fact set (render, QA, copy, audit) is reused.

---

## 4. Provenance model & lifecycle (typed facts)

**Full current source model (corrected — F4).** `FlyerLockedFact.source` already spans
**seven** values with an explicit merge priority in `merge_locked_facts` (`facts.py:559`):
`customer_text(0) > operator(1) > customer_profile(2) > reference_ocr(3) > reference_vision(4)
> uploaded_asset(5) > system(6)` (lower number wins). `ALLOWED_NEW_PROJECT_FACT_SOURCES`
(`facts.py:21`) lists the same seven. The two new values **join this larger trust model** —
this is not a greenfield two-value scheme.

| source | meaning | merge priority | grounding | QA | customer copy |
|---|---|---|---|---|---|
| `customer_confirmed` **(NEW)** | assumption the customer approved/edited **for this flyer** | **0** (tier w/ customer_text) | n/a | locked | shown as fact |
| `customer_text` | literally in the customer's message | 0 | enforced | must be present | shown as fact |
| `operator` | operator-applied | 1 | n/a | must be present | shown as fact |
| `customer_profile` | business saved profile (name/phone/address) | 2 | n/a | must be present | shown as fact |
| `reference_ocr` / `reference_vision` / `uploaded_asset` | from an uploaded reference | 3–5 | n/a | must be present | shown as fact |
| `system` | system-derived | 6 | n/a | — | — |
| `hermes_inferred` **(NEW)** | **planner assumption** (item, headline, section) | **7 — lowest** | **bypasses** (allowed to be new) | count/coverage + must render | **surfaced as assumption** |

**Merge-priority placement (F2).** Both new values MUST be added to the `merge_locked_facts`
priority dict — relying on the unknown-source default (99) would mis-merge.
`hermes_inferred` = **lowest** (7): any customer/operator/profile/reference fact overrides an
assumption on the same key — an assumption never shadows a real fact. `customer_confirmed` =
**top tier with `customer_text`** (the customer validated it).

**Mixed item-name / price provenance (F2).** Item facts split provenance *by kind*: an
`item:N:name` may be `hermes_inferred` (assumption) while its paired `item:N:price` stays
`customer_text`/`customer_profile` (a real flat/structure price *applied to* the assumed
item). The firewall (§6) guarantees the creative path can never author a price or any hard
fact, so the price half always traces to the customer. `merge_locked_facts` keys items by
normalized name; the contract tracks name-provenance and price-provenance **independently per
item**.

**Lifecycle (F2 — revision).** `hermes_inferred` → (shown) → approve/edit →
`customer_confirmed`; reject → dropped; re-plan → replaced wholesale (not text-refreshed).
Today the revision refresh edits only `source == "customer_text"` facts
(`_refresh_customer_text_locked_facts`, `update-flyer-project:~180`). **Net-new:** that path
must also treat `customer_confirmed` as editable truth so a later customer correction updates
a confirmed item; `hermes_inferred` items are re-generated by the planner, not text-patched.

**Project-scoped (F3).** `customer_confirmed` confirms an assumption **for this flyer/project
only.** It MUST NOT write durable business memory (the saved item catalog or
`customer_profile`). Promoting assumptions into a persistent business menu is a *separate,
explicitly owner-approved* menu-learning path — out of scope here and gated.

**Provenance bug this fixes (F6 — precisely).** `_requested_famous_item_facts` tags inferred
**item names** as `customer_text` (`facts.py:314`) — that mislabel is the bug; under this
contract they become `hermes_inferred` and are surfaced. The **price** it pairs
(`facts.py:318`, a customer-stated `$8.99`) is genuinely customer-grounded and **stays
`customer_text`** — the only inferred part is the **item↔price association**, not the price
value. The fix must not demote customer-provided flat pricing.

---

## 5. Safe vs unsafe inference axes (the contract's spine)

| Hermes MAY infer (safe) | Hermes may NEVER infer (hard facts — extractor-only, blank if absent) |
|---|---|
| item / dish / service selection (category-appropriate) | price, per-item price, "from $X" |
| headline + tagline variants, supporting copy | dates, days, hours, "this weekend", expiry |
| section labels ("Breakfast", "Specials") | phone, address, website, email |
| layout structure / visual hierarchy | business identity / brand name |
| color / mood / style direction | discounts, percentages ("20% off") |
| food / service imagery direction | legal / service / payment / delivery claims ("lowest price", "free delivery", "open daily") |

The right edge is the firewall's jurisdiction (§6). Note a hard fact may *attach* to an
inferred item — e.g. a stated `$8.99` (hard) applied to assumed items (inferred). The price
stays a hard fact; only the item is an assumption.

---

## 6. The hard-fact firewall (load-bearing custom piece #1)

A validator the planner output passes through before it can become facts. Mirrors the shape
of `validate_flyer_intent_decision` (`intent.py:246`) — returns ok + reasons, fail-closed.

**6a. Field-level rule:** a `hermes_inferred` fact may only populate safe-axis fact_ids
(`item:*:name`, `headline`, `tagline`, `section:*`, `style`, layout hints). Any attempt to
emit a hard-fact fact_id (`*:price`, `schedule`, `promotion_end`, `contact_phone`,
`location`, `business_name`, `offer_price`, discount) from the creative path → **rejected**.

**6b. Free-text claim scanner (the #1 risk).** The dangerous leak is not a structured price
field — it's a hard-fact-class *claim smuggled into creative prose*: a headline "**Lowest
prices in town!**" (superlative/price claim), section label "**Weekend Special**" (date/
availability), tagline "**Now open daily 8–11**" (schedule), "**Free delivery**"
(service claim). Creative text (`campaign_title`, `headline`, `tagline`, section labels,
supporting copy) is scanned for hard-fact-class patterns:
- currency / price / "from $" / "%"/ "off" / "discount"
- date / day-of-week / "today"/"tonight"/"this weekend"/"daily"/time ranges
- superlatives implying a claim ("lowest", "cheapest", "best price", "guaranteed")
- service/legal/payment/delivery claims ("free delivery", "no charge", "certified", "licensed")

On match → strip the offending span or reject the creative field (fail-closed; never ship a
claim the business didn't make). A claim is only allowed if it traces to a `customer_text`/
`customer_profile` fact (i.e., the customer actually said it).

**6c. Failure mode:** firewall violation never silently passes. Either the creative field is
dropped (planner re-asked, or that field omitted) or, if a *hard fact the design needs* is
missing/ambiguous, we route to clarify (§8). The firewall is the single most review-heavy
surface (Codex truth-guard lens, §14).

---

## 7. Intent-aware QA (load-bearing custom piece #2)

Extend QA from "locked facts visible" (today) to "**intent satisfied + nothing fabricated**",
on top of the existing OCR presence checks (`visual_qa.py`).

**7a. Count / coverage.** If the request implies a count ("8 items"), QA requires N rendered
item cards. Reuses `_item_name_present` (`visual_qa.py:287`) per item; adds a count assertion.

**7b. Hard + creative reconciliation.** Customer may name some items (hard) *and* request a
count ("3 of these plus more, 8 total"). QA verifies `hard_items + inferred_items == N`, all
present.

**7c. Pricing-type model.** Pricing is not one shape; QA must branch:
- **flat structure** ("any item $8.99") → every rendered item must pair with $8.99.
- **per-item prices** → each item shows its own stated price.
- **range / discount** ("5–10% off", "from $5") → no per-item price asserted; verify the
  structure text rendered, do not demand a price on each item.

**7d. No-fabricated-hard-fact guard.** QA also asserts the rendered image contains **no
hard-fact-class value that isn't grounded** — i.e. the firewall's runtime backstop at the
pixel level (catches a price/date the image model hallucinated into the background).

**Honest limit (set expectations):** this QA proves the flyer **satisfied the literal
request** (right count, right price-pairing, no leaked/fabricated claims). It does **not**
prove the assumptions were *commercially good* (right items for *this* business). That
residual is carried by the revision loop (§10) now and the spend-gated real-model eval
later (§13) — "QA passed" ≠ "great flyer".

---

## 8. Category & clarification gate

**Supported categories (infer freely within):** restaurants, groceries, salons, tutoring,
temples, events, real estate, cleaning, tax/accounting, local services/retail.

**Rule (refined per F5 — don't over-clarify):**
- Supported category + safe axes → **plan** (infer the safe content).
- **Clarify ONLY when the customer *references* a specific hard fact but *omits its value*** —
  e.g. "X% off" with no number, "sale ends ___", "open at ___", "this weekend" with no date.
  Then ask exactly one clarification and never infer the missing value.
- **Do NOT clarify just because a hard fact is absent.** A bare "make a summer sale flyer"
  (no price/discount/date referenced) is valid → generate a festive **non-price sale flyer**
  that fabricates **no** discount/price/date. Omission ≠ a question to ask; only a
  *referenced-but-blank* hard fact is.
- Unsupported / risky category (regulated, medical/financial claims, anything outside the
  list) → **clarify or decline**, do not infer.

**Reuse:** this is the existing `intent.py` `clarify` action + `needs_clarification` +
`clarifying_question` (`:47`,`:109-110`), already running in shadow. Net-new = the decision
of *when* to clarify vs plan, fed by the category classifier. "Prefer clarify/observe"
(`intent.py:96`) is already the posture.

---

## 9. Flag rollout plan (sliced, low-blast-radius)

Each slice: own branch off `origin/main`, deterministic tests, Codex review, merge on
CI-green + Codex-CLEAN, **deploy operator-gated**. A master flag (`flyer.creative_planner`,
default **off**) gates all runtime behavior. **Kill-switch invariant (F1):** with the flag
off, the existing hardcoded path (`FAMOUS_ITEM_SETS`/`_requested_famous_item_facts`) remains
the producer and the planner is a no-op ⇒ **byte-identical to today**. The hardcoded path is
removed only in slice 5, and only for the category the planner has proven it covers.

| Slice | Scope | Risk | Behavior change |
|---|---|---|---|
| **1 — Provenance type (inert)** | Add `hermes_inferred`/`customer_confirmed` source values; **add both to the `merge_locked_facts` priority dict** (`hermes_inferred`→lowest 7, `customer_confirmed`→top tier 0); plumb `source` through facts/render/QA/copy; **no new producers** | low | none (no facts use the new values yet) |
| **2 — Creative planner (flag-gated, alternate producer)** | Add planner mode to `semantic_brief`; emit `assumed_items` tagged `hermes_inferred`; wire as an **alternate producer gated by the flag** — **`FAMOUS_ITEM_SETS`/`_requested_famous_item_facts` stay untouched while flag off** | medium | **none when flag off** (byte-identical); behind flag, vague prompts get inferred items |
| **3 — Firewall + intent-QA** | Hard-fact firewall (6a field-rule + 6b free-text claim scanner) + QA count/pricing/reconciliation/no-fabricated (7) | medium-high (truth) | enforcement only fires on inferred content |
| **4 — Assumption-aware copy + revision + lifecycle** | Customer copy surfaces assumptions; one-tap revision handles; `inferred→confirmed` transitions; **extend `_refresh_customer_text_locked_facts` to treat `customer_confirmed` as editable (F2)**; confirmation is **project-scoped only — no durable menu/profile write (F3)** | medium | customer-visible copy change (behind flag) |
| **5 — Category gate + per-category enablement + retire hardcoded path** | Wire clarification gate (§8); flip flag **per supported category** after eval; **remove `FAMOUS_ITEM_SETS`/`_requested_famous_item_facts` once the planner covers their category (`indo-chinese`)** — the only slice that touches the old path | gated | turns the loop on, one category at a time |

**Sequencing rationale:** provenance + planner land *inert/flag-gated* first so the
firewall+QA (the safety half) are merged **before** any inferred content can reach a
customer, and the hardcoded path is retired **last** (slice 5) so the kill switch holds
throughout. No slice ships inferred content to a customer until §3 firewall + §7 QA are
merged and the per-category flag is flipped (slice 5).

---

## 10. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Hard-fact-class **claim leaks** into creative prose | **highest** | §6b free-text scanner, fail-closed; §7d pixel-level backstop; Codex truth-guard lens |
| **Bad/wrong assumptions** (dosa for a North-Indian shop) | high (commercial) | revision loop §10/§4 lifecycle; spend-gated real-model eval §13; per-category enablement §9 |
| Planner infers past a **missing hard fact** (invents a price) | high | §6a field rule + §8 clarification gate (clarify, don't infer) |
| **Provenance drift** across recovery/edit (confirmed item re-treated as guess) | medium | §4 lifecycle + tests §12; recovery reads `source` |
| **Two-call latency/cost** | low-medium | single dual-section call option; existing credit-burn discipline; planner only on vague-prompt path |
| **Blast radius** (cross-cutting) | medium | flagged slices §9 + kill switch (flag off = today's behavior) |
| **Scope creep into a "flyer brain"** | medium | hard boundary: Hermes infers, custom code only enforces the contract; we *delete* `FAMOUS_ITEM_SETS` |

---

## 11. Settled decisions preserved (do NOT regress)

- **Provider split** (OpenRouter generate / OpenAI source-edit) — untouched.
- **Deterministic overlay renders text, not the image model** — the planner *feeds* the
  overlay; it does not ask the image model to render item text.
- **Deterministic routing primary; intent contract in shadow** — the clarification gate uses
  the shadow contract; this design does not flip routing to active.
- **Hard-fact extraction + truth guarantees** — unchanged. Hard facts remain
  grounding-filtered and extractor-only. The creative path can never weaken them.

---

## 12. Tests planned (deterministic; no live bridge)

- **Provenance:** lifecycle transitions (`inferred→confirmed/edited/rejected`); source plumbs through render/QA/copy.
- **Firewall (the critical suite):** field-rule rejections; free-text claim cases incl. the §6b leak examples ("Lowest prices in town", "Weekend Special", "Open daily 8–11", "Free delivery") → stripped/rejected; allowed when traceable to a customer fact.
- **Intent-QA:** count/coverage; the three pricing types (flat/per-item/range); hard+creative reconciliation; no-fabricated-hard-fact backstop.
- **Planner contract:** `assumed_items` always tagged `hermes_inferred`, never populate hard-fact fact_ids; category-appropriate selection (golden, spend-gated for real-model).
- **Clarification gate:** ambiguous/missing hard fact → clarify; unsupported category → clarify/decline.
- **Golden — F0130 class:** "8 famous South Indian breakfast items, any item $8.99, Sat/Sun 8–11" → 8 inferred items rendered, each paired $8.99, hard facts (name/phone/address/schedule) grounded, assumptions surfaced. (real-model variant spend-gated.)
- **No-regression:** existing locked-fact path + golden suite unchanged when flag off.

---

## 13. Operator gates / what needs a human

- **Greenlight this design + the first slice scope** (this doc).
- **Per-category enablement** decisions (§9 slice 5).
- **Spend-gated creative-quality eval** before flipping a category on broadly (real-model golden).
- Orthogonal (existing `current-state.md` §A): `OPENAI_API_KEY`, Hermes OCR — not required for the creative-planner loop (that's source-edit/menu-extraction), tracked separately.

---

## 14. Review gate

Codex review (main-vps), **no code until CLEAN + operator approval of scope + slice 1**:

1. **Hermes/drift** — is this `extends-Hermes`? Does the planner reuse the existing provider call + delete the hardcoded dict (no parallel substrate)?
2. **Product/scope** — is the safe/unsafe axis split (§5) correct? Is anything mis-classified (e.g. is a "section label" ever a claim)? Is the supported-category list right?
3. **Truth-guard (the heavy lens)** — is the firewall (§6) *complete*, especially the free-text claim scanner? Can any hard-fact-class claim reach a customer? Does §7d backstop hold?
4. **QA-correctness** — are the intent-QA rules (§7: count, pricing types, reconciliation) sound and non-tautological?
5. **Rollout-safety** — does the flag/kill-switch (§9) guarantee byte-identical current behavior when off? Does the slice order put the safety half before any customer-visible inference?

---

### Appendix — the F0130 worked example (target behavior)

Input: *"Flyer for Lakshmi's Kitchen, include 8 famous South Indian breakfast items, any
item $8.99, Saturday and Sunday 8–11 AM, use saved address & phone."*

- **Hard facts (extractor, grounded):** business = Lakshmi's Kitchen; price structure = any
  item $8.99; schedule = Sat & Sun 8–11 AM; address/phone = from profile. Never invented.
- **Creative assumptions (planner, `hermes_inferred`, firewall-cleared):** Idli, Medu Vada,
  Masala Dosa, Plain Dosa, Pongal, Upma, Poori, Uttapam (8, South-Indian-breakfast
  appropriate); a headline; section label "Breakfast Specials".
- **Render:** 8 item cards via the deterministic overlay, each paired with $8.99; hard facts
  placed.
- **QA:** 8 cards present (count), each shows $8.99 (flat-pricing recon), hard facts present,
  no fabricated price/date/claim.
- **Customer copy:** "Here's a draft — I picked these 8 South Indian breakfast items
  (assumptions). Reply to swap any." → one-tap revision.

Today this produces an empty/weak flyer (`South Indian` ∉ `FAMOUS_ITEM_SETS`). That delta is
the product gap this design closes.
