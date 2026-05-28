# Hermes Commerce — Portfolio Reconciliation

**Drift-check tag:** `extends-Hermes`

**Status:** Draft for review (Phase B of the Hermes Commerce rewrite, 2026-05-28).

**Predecessor docs:** This reconciliation is the upstream gate for any rewritten PRD. The original "Hermes Commerce Agent" PRD (drafted as a generic commerce-SaaS app) was rejected; the decision is at `~/.claude/projects/C--projects-sme-agents/memory/project_commerce_primitives_decision.md`.

---

## Hermes-first analysis

This doc proposes **no new code** — it's the upstream scoping doc that decides what later docs/PRs may propose. It is itself a Hermes-first artifact: most "commerce" capabilities the PRD wanted are already owned by Hermes substrate or by existing portfolio agents that already use Hermes substrate.

| Capability the PRD wanted | Already covered by | New code needed? |
|---|---|---|
| WhatsApp text/media ingest | [Hermes] source ingestion + WhatsApp inbound origin | No |
| Sender identity (phone/LID, role) | [Hermes] `identify-sender` + `sender_role` gating | No |
| Skill dispatch by content + role | [Hermes] dispatcher SKILL pattern (`dispatch_shift_agent`) | Matrix amendment only |
| Per-VPS state | [Hermes] JSON+`safe_io.atomic_write_json`+`fcntl.flock` | No |
| Audit chain | [Hermes] NDJSON `decisions.log` + `log-decision-direct` chokepoint + `LogEntry` discriminated union | New LogEntry variants only |
| Approval workflows | [Hermes] `#XXXXX` 5-char codes + `generate_unique_code` + 4h TTL | No |
| WhatsApp/Telegram/email reply | [Hermes] multi-channel response | No |
| LLM gateway (text + vision) | [Hermes] swappable provider | No |
| Catalog / availability | **#6 Inventory Tracker** (scaffolded; SoT for SKU + stock) | No (Commerce reads via existing inventory state) |
| Customer memory / preferences | **#9 VIP** + **v3 #32 Special Request Memory** + **v3 #33 Loyalty** | No |
| Catering inquiry → quote → booking → fulfillment → payment | **#2 Catering Lead** (LIVE infra, opt-in) | No |
| Deposit links for catering | **#2 Catering Lead** `send_deposit_link` Phase 2 skill — *already named in portfolio.md:96* | No (Commerce primitive provides the link generator; Catering remains the caller) |
| One-off paid orders | **Flyer guest_order.py** (already in production for Flyer Studio) | No (canonical precedent for Commerce primitives) |
| Payment reconciliation | **#15 Cash & AR** (scaffolded) | No (Commerce primitive emits paid-event; Cash & AR consumes) |
| Order status query ("where's my order?") | **#23 Order Status & Pickup** (BACKLOG, gated on KDS/POS) | No (deferred per backlog gate) |
| Upsell at order time | **#24 Upsell** (BACKLOG) + **v3 #34 Menu Suggestion** | No (deferred per backlog gate) |
| Third-party delivery (DoorDash etc.) | **#25 Third-Party Delivery** (BACKLOG, gated on integration absence) | No (deferred per backlog gate) |
| Multi-store routing | **#3 Multi-Location Coordinator** (LIVE v0.1) | No (Commerce primitives are per-VPS — single tenant matches current architecture) |
| Marketing / promotions | **#11 Festival & Peak Prep** + **Flyer** + **v3 #38 Local Community Broadcast** | No |
| Admin dashboard | Existing Cockpit pattern (Flyer Admin in `web/frontend/src/sections/FlyerAdmin.tsx`) | New per-agent view only |
| Payment provider write (Stripe/Razorpay/UPI link mint) | **[net-new]** External write API; CLAUDE.md explicitly lists as net-new | Yes — `commerce_payment_link` primitive |
| Payment provider webhook receiver | **[net-new]** No existing HTTP receiver in repo | Yes — `commerce_payment_link` daemon |

**Result:** of ~22 capabilities the original PRD enumerated, **20 are already owned somewhere in the deployed system or portfolio**. Only **2 are genuinely net-new** (payment-link mint + webhook receive), and both belong to a single primitive (`commerce_payment_link`). The other three locked primitives (`commerce_catalog`, `commerce_cart`, `commerce_order_state`) are **deterministic-state helpers** generalized from existing Flyer `guest_order.py` patterns — they are not new agents, they are SKILL-callable scripts.

---

## Portfolio ownership map (canonical)

This is the binding decision: which agent owns which slice after Commerce primitives land. Future SKILL/script proposals must respect this map.

### What Commerce primitives own

Four shared primitives, all under `src/platform/commerce/` (NOT under a new `src/agents/commerce/`):

| Primitive | What it does | Owns state file | New LogEntry variants |
|---|---|---|---|
| `commerce_catalog` | Deterministic product lookup. Reads catalog JSON, returns matches with synonyms/multilingual aliases. Stateless query — no mutation. | Reads `state/commerce/catalog.json` (per-VPS); for restaurants/groceries with #6 Inventory active, defers to inventory's stock state for availability. | `commerce_catalog_queried` (optional; high-volume — consider sampling) |
| `commerce_cart` | Deterministic cart state per `(sender_phone_or_lid, chat_id)` — keying matches `identify-sender`'s either-or contract so LID-only customers don't collide on `(null, chat_id)` (Reviewer A LOW-1). Add / remove / update qty / unit conversion / clear. TTL: 4h idle → auto-clear. Mirrors approval-code TTL convention. | `state/commerce/carts.json` | `commerce_cart_started`, `commerce_cart_updated`, `commerce_cart_cleared`, `commerce_cart_expired` |
| `commerce_order_state` | Order ID + status state machine: `pending_payment` → `awaiting_approval` → `paid` → `preparing` → `ready` → `out_for_delivery` → `completed` / `cancelled`. Strictly typed transitions enforced in code (illegal transitions raise). Idempotent. | `state/commerce/orders.json` | `commerce_order_created`, `commerce_order_status_change`, `commerce_order_cancelled` |
| `commerce_payment_link` | Provider-link mint + webhook receive. **Generic template substitution today** (mirrors Flyer `_checkout_url` template pattern); direct Stripe/Razorpay API integration deferred to a separate slice with its own credential and compliance review. Idempotency key = `order_id` (NOT `(order_id, amount_cents)` — Reviewer B HIGH-2: a price-typo re-mint would otherwise leave two live links at different amounts). Any amount change requires an explicit `commerce_payment_intent_voided` row + new intent. Immutable `payment_reference` once stored. | `state/commerce/payment_intents.json` + `state/commerce/payment_references.json` (immutable history) | `commerce_payment_intent_minted`, `commerce_payment_link_sent`, `commerce_payment_confirmed`, `commerce_payment_dedup_blocked`, `commerce_payment_webhook_received`, `commerce_payment_webhook_verify_failed`, `commerce_payment_intent_voided`, `commerce_payment_refunded`, `commerce_payment_chargeback_received` (Reviewer B MEDIUM-2: reserve refund/void/chargeback variants now even if slice 1 emits none — schema migration later is expensive) |

### Money-moving invariants (binding on all `commerce_payment_link` callers)

These four invariants are inherited from `src/agents/flyer/guest_order.py` and the 2026-05-25 lesson on payment references. They are binding on slice 1 even before any compliance matrix lands:

1. **Immutable payment_reference across all orders.** A `payment_reference` previously stored against any order — including cancelled, completed, or voided — must block re-use indefinitely. Mirrors `flyer/guest_order.py:108-113` `payment_reference_already_used` block.
2. **No bare URL on unconfigured template.** If `_checkout_url(...)` returns `""`, callers MUST emit an explicit "Payment link is not configured yet" reply (mirrors `flyer/guest_order.py:231`) and MUST NOT render a bare URL, an empty `<a>`, or a substring like `Pay here: ` followed by whitespace. Misformatted templates (e.g., missing host) must not silently ship a clickable-looking string. Slice-1 caller-side helper `assert_payment_url_renderable(url)` enforces this.
3. **Idempotency key = `order_id` only.** Re-minting against the same `order_id` returns the existing intent. Amount change requires `commerce_payment_intent_voided` + new `order_id` (or new intent under same order with explicit void of prior).
4. **Caller-agent cross-reference field on all callers' audit rows.** Any agent that calls `commerce_payment_link` (catering deposit, future flyer-via-commerce, etc.) MUST carry `commerce_order_id` and `commerce_payment_intent_id` in its own audit row so Cash & AR (Agent #15) can join `commerce_*` events to caller-domain events without operator-eyeball reconciliation. Reviewer B MEDIUM-3.

**Crucially: these primitives do NOT own a dispatcher.** They are libraries called by existing dispatchers (catering_dispatcher for catering deposits; flyer_dispatcher for flyer payment; future commerce_dispatcher only if a customer flow truly has no owning agent).

### What Commerce primitives do NOT own (stays with existing agent)

| Capability | Stays with | Why |
|---|---|---|
| Catering inquiry capture, quote drafting, booking, fulfillment | **#2 Catering Lead** | Catering's domain logic (dietary, headcount tiering, capacity check, follow-up cadence) is non-portable. Catering CALLS `commerce_payment_link` to mint the deposit link, then writes `catering_payment_deposit_minted` via its own existing audit pattern. |
| Customer preferences / loyalty / special-request memory | **#9 VIP** + **v3 #32** + **v3 #33** | Loyalty + memory is a multi-event cross-order concern; Commerce primitives operate on single-order state only. Order-state primitive may emit `commerce_order_completed` for downstream consumers; loyalty/memory subscribe via existing audit-read pattern. |
| Catalog SoT / stock levels | **#6 Inventory Tracker** (today: v0.1 stub; declines when `cfg.inventory.enabled=False` which is the default) | Inventory is the *intended* SoT for "what's in stock right now." `commerce_catalog` will read inventory's state once that agent ships beyond v0.1 stub. For slice 1, `commerce_catalog` reads only the static JSON fallback path (`state/commerce/catalog.json`) with no availability join — Reviewer A MEDIUM-1 softening. Future migration to inventory-backed availability is a separate PR. |
| Payment reconciliation (cash, AR, refunds, dispute tracking) | **#15 Cash & AR** | Payment confirmation event flows from `commerce_payment_link` → audit → Cash & AR consumes from audit log for reconciliation. Commerce does not own the ledger. |
| Flyer one-off paid orders | **Flyer `guest_order.py`** | Already in production. The PR-D extraction is to *generalize* its pattern into `commerce_*` primitives so future agents can reuse — Flyer remains the caller and continues to own its specific guest-order flow until/unless we choose to migrate. See "Migration posture for Flyer" below. |
| Order status query ("where's my order?") | **#23 Order Status & Pickup (BACKLOG)** | Stays backlog until first KDS/POS-integrated customer. `commerce_order_state` will provide the read API when #23 is promoted. |
| Upsell at order time | **#24 Upsell / v3 #34 Menu Suggestion (BACKLOG)** | Stays backlog. `commerce_cart` will provide the cart-aware read API when promoted. |
| Third-party delivery feed | **#25 Third-Party Delivery (BACKLOG)** | Separate problem (delivery-platform aggregation, not WhatsApp). Stays backlog. |
| Multi-store routing | **#3 Multi-Location Coordinator** | Already LIVE. Commerce primitives operate per-VPS (single-tenant); multi-location routing is orthogonal. |
| Marketing/promotional outbound | **Flyer + #11 + v3 #38** | These are outbound campaign systems. Commerce is inbound order capture. |
| Admin dashboard | Existing Cockpit (e.g. `FlyerAdmin.tsx`) extended with a Commerce view | Reuses the deployed admin-dashboard pattern; not a new app. Commerce-Cockpit reads `commerce/*` state + audit log via the same SSE/poll pattern as Flyer Admin. |

### Migration posture for Flyer `guest_order.py`

**Do not migrate now.** The Flyer guest-order implementation is in production with idempotency, replay protection, and lesson-validated edge cases (`BUG-FLYER-QA-001`). The Commerce primitives should *learn from* its shape — same state-machine, same `payment_reference` immutability discipline, same template-based checkout-URL pattern — but Flyer keeps using its own module until a future migration PR consolidates with explicit test parity.

This is the same posture as #15 Cash & AR holding payment reconciliation: extracted shared primitive does not mandate immediate caller migration.

---

## Dispatcher routing impact

`dispatch_shift_agent` matrix (`src/agents/shift/skills/dispatch_shift_agent/SKILL.md:80-101`) is the single front door. New rows for commerce intent — if/when an order-capture flow lands — must:

1. **Match keywords explicitly + gate on a `cfg.commerce.enabled` flag** (opt-in disabled by default, like every other Tier-2 agent).
2. **Position AFTER catering keywords** (catering inquiries containing food words must not get swallowed by a broader "order" intent).
3. **Position AFTER `flyer_dispatcher`** (active-flyer-project sender must keep routing to flyer until that project is terminal).
4. **Position BEFORE the catch-all `handle_owner_command` / `handle_sick_call` rows** (otherwise commerce intent from owner gets swallowed by the owner-command catch-all).
5. **Write `dispatcher_routed` audit BEFORE delegating** (existing pattern, non-negotiable).

**Slice 1 inbound reachability (Reviewer A HIGH-1):** with primitives as library-only and no matrix amendment, commerce intent has *zero inbound surface* in slice 1 by design. Today's matrix has no "place an order" row at all; a customer/unknown-role text like "I'd like to order goat" or "Do you have basmati?" falls through to `SKILL.md:100` "DECLINE politely, log `unknown_sender_declined`." This is intentional — the primitives ship as deterministic libraries first, available only via internal callers (catering deposit). Inbound traffic stays declined until a real first-customer flow exists; that flow gets its own design + dispatcher row in slice 2.

**Routing collision risk (current + future):** the original "Hermes Commerce Agent" mega-agent would have claimed `sender_role=customer + media_type=text` blanket, colliding with:

- **catering_dispatcher** — already claims "any text with catering keywords"
- **catering_dispatcher PR-CF1 finalize-intent words** (`SKILL.md:107`: "ready to book", "lock it in", "proceed with this menu") — pre-flag (Reviewer A MEDIUM-2): future `commerce_cart` checkout intent ("ready to order", "lock in my order") shares verb shape. Slice-2 commerce dispatcher row MUST position AFTER catering's PR-CF1 row.
- **update_catering_menu image-no-caption row** (`SKILL.md:92`: "Image OR document attachment, no caption, in owner's self-chat → update_catering_menu (assume menu intent)") — pre-flag (Reviewer A HIGH-2): future `commerce_catalog` ingest of owner-supplied catalog image/CSV would collide. Slice-2+ catalog-ingest flow must add caption requirement OR explicit `cfg.commerce.catalog_ingest_enabled` gate.
- **flyer_dispatcher** — already claims "any text from sender with active flyer project"
- **customer_location_query** — claims "store locator regex"
- **handle_owner_command / handle_sick_call / handle_candidate_response** — claim non-coded text by role

The four primitives shape avoids this entirely: **no new top-level dispatcher row is added at all** in the first slice. The primitives are called from inside existing handlers (catering calls `commerce_payment_link` for deposits; flyer continues using `guest_order.py` directly). A dedicated `commerce_dispatcher` ships only when a customer flow exists that has no other owning agent — and is scoped narrowly (e.g., explicit "place order" intent for a grocery store with no catering/flyer flow).

---

## Compliance gating (placeholder for Phase C output)

This reconciliation does not resolve compliance. The full compliance matrix lands in the rewritten PRD v2 as a gating section before any SKILL design. Categories that must be addressed:

- Grocery (standard goods)
- Prepared food / restaurant takeout
- Raw meat (Meta Commerce Policy restriction surface — even conversational ordering is a risk surface)
- Catering deposits (money-moving + future contractual obligation)
- Alcohol / tobacco (explicit exclusion category)
- Age-gated goods
- Promotions / marketing opt-in (24-hour window + template rules)
- Payment links in chat (Meta policy on not requesting full card or financial account numbers in chat)

**No SKILL design proceeds until that matrix is signed off.**

**Slice 1 prohibited-category guard (Reviewer B MEDIUM-1):** slice 1 callers MUST NOT introduce any customer-facing "send payment" copy for raw meat, alcohol, tobacco, or age-gated SKUs until the compliance matrix lands. Catering deposits for general-cuisine events remain in scope (they are the canonical first caller); category-specific guards are enforced at the caller layer, not at the primitive layer.

---

## Build sequence implied by this reconciliation

Slice 1 (first PR, the one this autonomous run is targeting):

- `commerce_cart` — generalize from Flyer `guest_order.py` cart-add pattern; per-(sender_phone, chat_id) state file; 4h idle TTL.
- `commerce_order_state` — strict state-machine; idempotent transitions; `pending_payment` initial state.
- **Placeholder `commerce_payment_link`** — template-based checkout-URL only (mirror Flyer `_checkout_url`); no Stripe/Razorpay API call yet. Returns a static `commerce_payment_link_unconfigured` if no template configured, mirroring the Flyer `"Payment link is not configured yet"` reply.
- New LogEntry variants (only the ones above; no payment-confirm yet since no real provider).
- No dispatcher matrix change yet — primitives are library-only.
- Test pattern: subprocess-invoke + assert on state-file mutations + assert on audit-log rows.

Slice 2 (separate PR, deferred to its own design cycle):

- `commerce_catalog` (requires either Inventory-active customer to wire against, or a customer-provided static catalog file — both are operator-input dependencies).
- Real `commerce_payment_link` provider integration (gated on credentials + compliance sign-off).
- Webhook receiver daemon.
- Dispatcher matrix amendment (only if there is a customer flow with no other owning agent).
- Cockpit Commerce view.

Slice 1 is intentionally minimal and verifiable. It establishes the primitive shape without taking on any net-new external-API or compliance risk.

---

## Open questions for operator (NOT blockers for the rewritten PRD — flagged for awareness)

1. Is there a target first customer for Commerce primitives, or are we building speculative shared infrastructure? If speculative, slice 1's minimal scope is correctly calibrated; if there is a specific customer, their needs may shrink or shift the slice.
2. For `commerce_payment_link` slice 2: which payment provider has credentials available first? (Affects which provider's webhook signature scheme + idempotency convention drives the design.)
3. **Resolved as of Phase B review:** catering is the canonical first caller of `commerce_payment_link` (per `docs/portfolio.md:96` `send_deposit_link` Phase 2 skill). The primitive remains caller-agnostic; the caller-agent cross-reference field (invariant #4 above) makes the catering linkage auditable without coupling the primitive to catering specifics.

These can be answered in the rewritten PRD v2 review without blocking this reconciliation.

---

## Phase B review summary (2026-05-28)

Two parallel reviewers ran with non-overlapping lenses:

- **Reviewer A (Hermes/portfolio collision + dispatcher routing):** `APPROVE_WITH_RECOMMENDATIONS` — ownership map materially correct; two HIGH (dispatcher reachability statement, image-no-caption catalog-ingest collision), two MEDIUM (Inventory/VIP/Cash & AR are stubs not active SoT; catering finalize-intent collision), two LOW (sender-key shape, state-path collision check confirmed clean).
- **Reviewer B (compliance/payment/money-moving):** `APPROVE_WITH_RECOMMENDATIONS` — money-moving posture structurally sound; three HIGH (unconfigured-link UX contract, idempotency-key narrowing, dedup invariant inlining), three MEDIUM (prohibited-category guard, refund/void/chargeback variant reservation, catering-caller cross-reference), one LOW (resolve open Q#3).

**All HIGH and MEDIUM findings applied in this revision.** LOW findings either applied (sender-key shape) or noted (Open Q#3 resolved). No blockers; reconciliation ready to gate the PRD v2 draft.
