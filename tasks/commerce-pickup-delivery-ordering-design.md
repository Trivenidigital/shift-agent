# Design — Commerce Pickup/Delivery Ordering + Order Cockpit V1

**Status:** DESIGN ONLY — not approved for implementation. Requires Codex-CLEAN (Hermes/drift · product/scope · runtime-state/operator-gate · money/payment/compliance) **and** operator approval before any build.

**Drift-check tag:** extends-Hermes

This extends the already-built (dormant) Commerce order/cart/payment substrate and the existing read-only Cockpit pattern (`web/backend` FastAPI routers + `web/frontend` React sections). It introduces **no** new runtime substrate, no dispatcher change in the first slices, and no provider/POS activation.

**Live-deploy note (runtime-verified on main-vps 2026-05-30, authoritative over docs):** production runs `deploy-20260530-034606-f1ff0cb9` (SHA `f1ff0cb`) — confirmed as the newest tarball in `/opt/shift-agent/deploys/`, with the deployed `pilot-readiness-check` carrying the `alerting.pushover` check (so **#358 IS deployed**) and the deployed `safe_io.py` carrying **no** `LiveBridgeSendInTestError` (pre-#367, consistent with `f1ff0cb`). Prod is **15 commits behind** origin/main `43fceab`. The only customer-facing undeployed runtime change is **#374** (`send-catering-ack` empty-JID fail-closed) — it rides the next normal deploy; the undeployed `safe_io.py` send-path tripwire is pytest-gated (inert in prod). Commerce is **dormant** (`enabled=False, provider=placeholder`). NOTE: `docs/runbooks/pilot-readiness-report-2026-05-30.md` (which names `deploy-…-7e524c2e` / "#358 not yet deployed") is an **earlier-in-the-day snapshot, now stale** — superseded by the runtime evidence above. None of this design depends on deploying anything; it builds on origin/main.

---

## 1. Context / product decision (authoritative)

First Commerce target = **restaurant pickup/delivery orders via WhatsApp**, surfaced to staff through a **Hermes Commerce Order Cockpit** (read-only first). **Not** a POS replacement; POS sync is a **later per-customer adapter** after the WhatsApp order loop proves demand. Source-of-truth: `tasks/hermes-commerce-prd-v2.md`, `tasks/hermes-commerce-portfolio-reconciliation.md`, `docs/portfolio.md`, this repo's runtime state.

This doc does **not** re-decide what PRD v2 already specifies (cart/order/payment/catalog schemas §5, compliance matrix §6, payment-link discipline §7, audit variants §8, dispatcher routing §9, non-goals §12). It **builds on** PRD v2 and adds the net-new pickup/delivery-specific pieces: fulfillment metadata reconciliation + the Order Cockpit + the POS-sync-status placeholder + the slice sequencing.

## 2. Hermes-first capability checklist

End-to-end flow: customer WhatsApp order → cart → order record → staff sees it in the Cockpit → staff advances status → (later) payment link → (later) POS sync.

| Step | Tag | Note |
|---|---|---|
| 1. Receive WhatsApp inbound (text/media) | `[Hermes]` | Hermes ingress (verified for Catering/Shift/Flyer) |
| 2. Identify sender (phone/LID) + role-gate | `[Hermes]` | `identify-sender` / `validate-sender-block` substrate |
| 3. Cart + order state (priced line items, status machine) | `[Hermes-built, dormant]` | `src/platform/commerce/{cart,order_state}.py` already exist |
| 4. Append-only audit | `[Hermes]` | `commerce/audit.py` → canonical `decisions.log` chokepoint |
| 5. Payment link mint (when active) | `[Hermes-built, dormant]` | `commerce/payment_link.py` + livemode/webhook gates |
| 6. Pickup/delivery fulfillment metadata on the order | `[net-new]` | additive optional fields on `CommerceOrder` (Slice A) |
| 7. Read-only staff Order Cockpit | `[net-new]` | new FastAPI router + React section mirroring `FlyerAdmin` (Slice B) |
| 8. Staff status actions (preparing/ready/…) | `[net-new]` | thin operator-script wrappers over `order_state.transition` (Slice C) |
| 9. WhatsApp ordering dispatcher route + catalog | `[net-new]` | PRD v2 §9 design; behind compliance + catalog + operator gates (Slice D) |
| 10. POS sync | `[net-new, deferred]` | per-customer adapter after a POS is chosen (Slice F) |

Most of the substrate (steps 1-5) is Hermes or already-built-dormant; the net-new is fulfillment metadata + Cockpit + (gated) ordering flow + (deferred) POS adapter. `mcp/native-mcp` is the likely path for any future POS adapter (Slice F) — investigated only when a specific customer/POS is chosen.

## 3. Drift-rule self-checks (read-deployed-code done)

- ✅ Read `src/platform/schemas.py` (`CommerceOrder` 2470, `CommerceOrderStatus` 2445 = `pending_payment→awaiting_approval→paid→preparing→ready→out_for_delivery→completed/cancelled/voided/refunded`, `CommerceCart` 2416, `CommerceCartItem` 2404, `CommercePaymentIntent` 2505, `CommerceConfig` 2340) before drafting the schema reconciliation.
- ✅ Read `src/platform/commerce/order_state.py` (`create`/`transition`/`cancel`/`get` + `CommerceOrderStore`) and `src/platform/commerce/cart.py` (`add_item`/`remove_item`/`update_qty`) before scoping Slices A–C.
- ✅ Read `src/platform/commerce/payment_link.py` (`mint`, `assert_payment_url_renderable`) + the deployed gates (`commerce_webhook_gate.py`, `commerce_livemode_gate.py`) before scoping Slice E.
- ✅ Read `tasks/hermes-commerce-prd-v2.md` (§5 schemas, §6 compliance, §9 dispatcher) + `tasks/hermes-commerce-portfolio-reconciliation.md` (ownership map, Flyer `guest_order.py` migration posture) before reconciling.
- ✅ Read `web/frontend/src/sections/FlyerAdmin.tsx` + `web/backend/app/routers/flyer.py` (+ `main.py` router-include) before designing the read-only Cockpit.
- ✅ Read `src/agents/shift/skills/dispatch_shift_agent/SKILL.md` — confirmed **no** order/commerce/checkout route exists today (catering keywords include `"pickup for event"`; an ordering route must not collide).

Deployed-pattern compliance: JSON-on-disk + `safe_io` atomic writes + `flock`; NDJSON audit via the `decisions.log` chokepoint; Pydantic v2 `extra="forbid"` state schemas; per-VPS single-tenant state; Cockpit = FastAPI read-only router + React section. No SQLite, no parallel send/audit/state substrate.

## 4. Current-state reconciliation (existing primitives ↔ pickup/delivery order loop)

**Finding:** the Commerce *state machine* is already general-purpose and pickup/delivery-**capable**, but its existing *callers/primitives* are payment/catering-deposit oriented, and the order record **lacks fulfillment metadata**.

| Concern | Exists today (dormant) | Gap for pickup/delivery |
|---|---|---|
| Cart | `CommerceCart`/`CommerceCartItem` (sku, qty, unit, price); `cart.py` add/remove/update_qty | none structural; needs a catalog to source SKUs (PRD v2 §5 catalog = Slice 2/D) |
| Order lifecycle | `CommerceOrderStatus` already has `preparing/ready/out_for_delivery/completed` | **no `fulfillment_type` (pickup\|delivery)** field |
| Order contact/logistics | `sender_phone`/`sender_lid`/`chat_id` | **no `customer_name`, `delivery_address`, `requested_time`/`scheduled_for`, `order_notes`** |
| Payment | `payment_intent_id`, `payment_reference`, `payment_link.mint`, livemode/webhook gates | activation is operator-gated (Stripe) — Slice E |
| POS | none | **no `pos_sync_status`** placeholder; real sync deferred (Slice F) |
| Audit | `commerce/audit.py` + `Commerce*` LogEntry variants | extend variants for new fulfillment/cockpit-action events (additive) |
| Dispatcher | **no ordering route** | PRD v2 §9 design; gated (Slice D) |
| Cockpit | none for Commerce | net-new read-only section (Slice B) |

**Reconciliation decision:** treat the existing `commerce/*` order/cart/payment substrate as the foundation. Pickup/delivery is **additive**: optional fulfillment fields on `CommerceOrder` (Slice A, `extra="forbid"`-compatible, with safe defaults — `None` for the optional metadata fields, `not_synced` for `pos_sync_status` — so existing dormant callers and already-stored orders validate unchanged) + a `pos_sync_status` placeholder, then a read-only Cockpit over the existing `orders.json`/`carts.json`/`decisions.log`. The catering-deposit caller (`commerce_payment_confirmed`, deposit gates) is **not** modified. The Flyer `guest_order.py` migration posture from the reconciliation doc remains as-is (out of scope here).

## 5. New primitives introduced

- **Schema (additive, Slice A):** optional fields on `CommerceOrder` — `fulfillment_type: Optional[Literal["pickup","delivery"]]`, `customer_name`, `delivery_address` (structured, optional), `requested_time`/`scheduled_for`, `order_notes`, `pos_sync_status: Literal["not_synced","pending","synced","failed","n/a"] = "not_synced"`. Defaults are `None` for the optional metadata fields and `"not_synced"` for `pos_sync_status`, so dormant callers and already-stored orders validate unchanged; `extra="forbid"` preserved.
- **Cockpit backend (Slice B):** new read-only FastAPI router `web/backend/app/routers/commerce.py` (GET orders list + GET order detail, reading `state/commerce/orders.json` + joined `decisions.log` audit), included in `main.py` like `flyer.router`.
- **Cockpit frontend (Slice B):** new React section `web/frontend/src/sections/CommerceOrders.tsx` mirroring `FlyerAdmin.tsx` (useQuery, Cards, tabs, evidence/transcript drawer).
- **Staff-action operator scripts (Slice C, gated):** thin deterministic wrappers over `order_state.transition` (e.g., `commerce-order-advance`), audit-logged, operator/role-gated — design-only until reviewed.
- **Audit LogEntry variants (additive):** new `Commerce*` variants for fulfillment-set / cockpit-status-action, subclassing `_BaseEntry`, registered in the `LogEntry` union.

No new storage engine, messaging path, approval-code generator, or identity mechanism.

## 6. POS-first analysis (Square / Clover / Toast / Shopify)

Per CLAUDE.md Hermes-first + the `mcp/native-mcp` escape hatch, the question is whether to integrate any POS in v1.

| POS | API exists? | Hermes skill / MCP today? | v1 verdict |
|---|---|---|---|
| Square | Yes (Orders/Catalog/Payments REST + OAuth) | No in-house Hermes skill; community MCP servers exist (unvetted) | **Adapter later** — needs a customer on Square + OAuth onboarding |
| Clover | Yes (REST + OAuth; merchant-scoped) | None vetted | **Adapter later** — per-merchant OAuth, device-tied |
| Toast | Partner-gated API (approval + partner agreement) | None | **Adapter later** — highest onboarding friction; do not gate v1 on it |
| Shopify | Yes (Admin API + OAuth; Shopify MCP exists) | Shopify MCP exists (unvetted) | **Adapter later** — strongest API/MCP story, but still per-store OAuth |

**Verdict: adapter-later for all four.** No POS integration in v1. Rationale: each is per-customer OAuth/credential onboarding (operator-gated, irreversible-ish), Toast is partner-gated, and the product decision is explicit that the WhatsApp order loop must prove demand before per-customer POS adapters. v1 carries a `pos_sync_status` field (default `not_synced`/`n/a`) so the Cockpit can display sync state, but **no real sync code ships**. When a specific customer + POS is chosen, the adapter is investigated via `mcp/native-mcp` first (Slice F), in its own design+review cycle.

## 7. Cockpit-first decision

**First slice that ships customer/operator value = a read-only Commerce Order Cockpit** (Slice B), built on the existing FastAPI-router + React-section pattern, reading the already-built (dormant) `orders.json`/`carts.json` + `decisions.log`. It needs no provider activation, no dispatcher change, no customer messaging — only the Slice-A fulfillment fields to render meaningfully. POS sync is a **status field only**, real sync deferred to a later per-customer adapter. This sequences value (operators can see/track orders) ahead of risk (payment activation, customer-facing ordering, POS).

## 8. Operator-gated items (STOP-and-hand-off)

- **Stripe activation** — provider flip `placeholder → stripe`, `enabled=true`, livemode flag, webhook subscription, payment-link template. (Slice E precondition.)
- **POS credentials / OAuth** — per-customer Square/Clover/Toast/Shopify onboarding. (Slice F precondition.)
- **Provider flips** of any kind.
- **Real customer messaging** / operator-owned WhatsApp test numbers for any live ordering smoke. (Slice D validation.)
- **Business-policy decisions** — tax/fee rules, delivery radius/fees, prohibited-category list sign-off (PRD v2 §6 compliance), tip handling.
- **Pushover real keys** (existing pilot-readiness blocker) before any customer pilot.

## 9. Explicit non-goals

- **No full POS replacement.**
- **No KDS (kitchen display) replacement.**
- **No inventory decrement / stock tracking.**
- **No automatic refunds or chargebacks** (refund/chargeback PR-4 stays deferred per the commerce decision; any refund is operator-initiated, reviewed).
- **No age-gated / raw-meat / alcohol ordering** before explicit compliance sign-off (PRD v2 §6 prohibited-category guard governs).
- **No new generic Commerce mega-agent** — primitives called by existing agents + a Cockpit, per the standing decision.
- **No dispatcher/customer-facing ordering** until compliance + catalog + operator gates are cleared (Slice D and beyond).

## 10. Proposed implementation slices

Each slice = its own fresh branch → build → test → Codex review → merge (Codex-CLEAN) → operator-approved deploy if runtime-material. Slices gate forward; do not skip ahead.

- **Slice A — schema/state reconciliation (test-only-ish, dormant):** additive optional fulfillment fields + `pos_sync_status` on `CommerceOrder`; deterministic schema + `order_state` round-trip tests; back-compat tests proving existing dormant callers + stored orders still validate. No behavior change (fields unused until B/D). *Smallest, lowest-risk; likely first build if approved.*
- **Slice B — read-only Order Cockpit:** FastAPI router (GET list/detail over `orders.json` + audit join) + React section (mirror `FlyerAdmin`): customer/items/notes/pickup-delivery/requested-time/address/payment-status/transcript/audit, simple totals, `pos_sync_status` display. **No state transitions** (read-only). CI/tests for the router; frontend follows cockpit conventions.
- **Slice C — staff actions:** operator-script wrappers over `order_state.transition` (preparing/ready/out_for_delivery/completed/cancel), audit-logged, role-gated; Cockpit buttons wired. **Design-only until the transition matrix + authority model are fully reviewed** (money/operator-gate lens).
- **Slice D — WhatsApp ordering dispatcher flow:** catalog (`CommerceCatalog`, PRD v2 §5) + dispatcher ordering route (PRD v2 §9) + customer-facing cart/checkout. **Behind:** compliance sign-off (§6), catalog provisioning (operator), and live-number validation. Not started until A–C land + gates clear.
- **Slice E — payment link:** only after **operator** activates Stripe (provider flip + livemode + webhook + template). Uses existing `payment_link.mint` + deployed gates. No activation in this design's scope.
- **Slice F — POS adapter:** only after a specific **customer + POS** is chosen; investigate `mcp/native-mcp` first; per-customer OAuth onboarding is operator-gated. Own design+review cycle.

## 11. Open questions for review / operator

1. Slice-A fulfillment fields: structured `delivery_address` (object) vs single string — preference? (Design proposes optional structured object with a flattened display string for the Cockpit.)
2. Cockpit auth/visibility: reuse the existing cockpit auth (`routers/auth.py`) + a new section gate — confirm acceptable.
3. Tax/fee computation ownership for v1 Cockpit display: show stored `tax_cents`/`fee_cents` only (no computation) until business-policy rules are set — confirm.
4. Is a read-only Cockpit (Slice B) the desired first build, or schema-only (Slice A) first? (Design recommends A then B; A unblocks B's rendering.)

## 12. Review gate

Send this doc to Codex (lenses: Hermes/drift · product/scope · runtime-state/operator-gate · money/payment/compliance). **Do not implement until Codex-CLEAN and the operator approves implementation + the first slice.**
