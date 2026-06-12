# Hermes Commerce — PRD v2 (rewritten)

**Drift-check tag:** `extends-Hermes`

**Status:** Draft for Phase D parallel review (2026-05-28). Supersedes the operator-supplied "Hermes Commerce Agent" PRD which was rejected as a mega-agent / generic commerce SaaS.

**Upstream gate:** `tasks/hermes-commerce-portfolio-reconciliation.md` (Phase B output, Phase B review applied). All ownership decisions and money-moving invariants in that doc are inherited here.

**Phase ordering:** This PRD covers the design of slice 1 + the scope envelope for slice 2. The compliance matrix in §6 is the gating section — no implementation may ship beyond slice 1's prohibited-category-guarded scope until the matrix is operator-signed.

---

## 1. New primitives introduced

Four shared deterministic primitives, all under `src/platform/commerce/` (NOT a new agent dir). Each is a Python module + (optional) operator script. SKILLs may call them via Hermes' `terminal` tool; in-process callers (e.g., catering scripts) import them directly.

| Primitive | Module path | Operator script (slice 1?) |
|---|---|---|
| `commerce_catalog` | `src/platform/commerce/catalog.py` | None in slice 1 (read-only library) |
| `commerce_cart` | `src/platform/commerce/cart.py` | `src/platform/scripts/commerce-cart` (manage cart from SKILL) |
| `commerce_order_state` | `src/platform/commerce/order_state.py` | `src/platform/scripts/commerce-order` |
| `commerce_payment_link` | `src/platform/commerce/payment_link.py` | `src/platform/scripts/commerce-payment-link` |

**Slice 1 scope** (this PR): `cart` + `order_state` + placeholder `payment_link` + LogEntry variants + tests. Library-only — no dispatcher matrix change. The catering deposit caller wiring is deferred to its own slice (lets us prove the primitive shape before binding a real caller).

**Slice 2+ scope** (separate PRs, each with its own design + compliance gate):

- `commerce_catalog` slice
- Real `commerce_payment_link` provider integration (Stripe / Razorpay / UPI / Zelle / Cash App — operator choice)
- Webhook receiver daemon
- Catering deposit caller wiring (binds `commerce_payment_link` to Catering Agent #2's `send_deposit_link` Phase 2 skill)
- Cockpit Commerce view (reuses Flyer Admin pattern)
- Dispatcher matrix amendment (only if a customer flow with no other owning agent exists)

---

## 2. Hermes-first table

| Step | [Hermes] / [net-new] | Note |
|---|---|---|
| WhatsApp text/media ingest | [Hermes] | source ingestion + WhatsApp inbound |
| Identity resolution (phone/LID) | [Hermes] | `identify-sender` |
| Role gating (owner/employee/customer/unknown) | [Hermes] | `sender_role` |
| Dispatcher routing | [Hermes] | `dispatch_shift_agent` matrix; slice 1 adds no row |
| Cart state read/write | [net-new — library] | `commerce_cart` module; uses `safe_io.atomic_write_json` |
| Catalog lookup (static JSON fallback) | [net-new — library] | `commerce_catalog` (slice 2) |
| Order state machine | [net-new — library] | `commerce_order_state`; uses `safe_io.atomic_write_json` |
| Approval code (`#XXXXX`) at checkout | [Hermes] | `generate_unique_code` |
| Payment-link template substitution | [net-new — library] | `commerce_payment_link` placeholder (slice 1) |
| Payment-link provider API call (Stripe/Razorpay/UPI mint) | [net-new — external write] | deferred to slice 2 |
| Payment webhook receiver + signature verify | [net-new — daemon] | deferred to slice 2 |
| WhatsApp text reply | [Hermes] | multi-channel response |
| Audit chain | [Hermes] | NDJSON via `log-decision-direct`; slice 1 adds new `LogEntry` variants |
| Per-VPS state | [Hermes] | `state/commerce/*.json` per existing pattern |
| LLM intent classification | [Hermes] | when needed; LLM never sees prices/IDs (per `docs/hermes-alignment.md` Part 1) |
| Cockpit / admin dashboard | [Hermes pattern reuse] | new per-agent view on existing Flyer-Admin substrate (slice 2) |

**Net-new effort summary (full Commerce roadmap, all slices):** ~1,080 LOC + ~680 LOC tests + ~400 LOC SKILL prose. Slice 1 captures ~280 LOC + ~250 LOC tests; remaining ~800 LOC + ~430 LOC tests is split across slices 2+.

---

## 3. Portfolio ownership map (reference)

See `tasks/hermes-commerce-portfolio-reconciliation.md` §"Portfolio ownership map (canonical)" for the full table. Binding decisions:

- Commerce primitives own ONLY ordering substrate (catalog query, cart state, order state machine, payment link). They are libraries.
- Catering, Flyer, VIP, Cash & AR, Inventory, Multi-Location, and all backlog agents (#23/#24/#25, v3 #30/#32/#33/#34/#36) retain their named domain scope.
- Flyer `guest_order.py` does NOT migrate in slice 1; primitives learn from its shape.

---

## 4. Deterministic / LLM boundary

Per `docs/hermes-alignment.md:33-37` ("LLM never sees prices, IDs, or sensitive state"). Concrete partitioning for Commerce:

### LLM-owned (slice 2+; not slice 1)

- **Intent classification** of free-form customer messages into structured fields: e.g., "I'd like 5 lbs goat and 2 trays paneer biryani" → `[{sku_hint: "goat", qty: 5, unit: "lb"}, {sku_hint: "paneer biryani", qty: 2, unit: "tray"}]`. The LLM emits structured `CartIntent`; deterministic Python resolves `sku_hint` against catalog, applies unit conversions, computes totals.
- **Catalog synonym resolution** for natural-language SKU mentions ("paneer" matches `"PANEER-BLOCK-200G"`). LLM-extracted candidates pass through deterministic catalog match — if no match, response says "we don't carry that; here's what's similar."
- **Tone-appropriate reply drafting** (customer-facing copy after deterministic logic decides the substance).

### Deterministic-owned (slice 1 + ongoing)

- **All prices, totals, taxes, fees, surcharges.**
- **All order IDs, payment intent IDs, approval codes** (`#XXXXX`).
- **State transitions** — every transition is a Python function with typed pre/post conditions; illegal transitions raise.
- **Catalog availability resolution** — read from inventory/static JSON, no LLM involvement.
- **Payment-link URL substitution** — template `.format(...)` only; no LLM-generated URLs ever.
- **Audit row emission** — every state edge writes a `LogEntry` row deterministically.
- **Customer copy policy enforcement** — mirrors the Flyer deterministic copy-lint pattern: LLM may propose copy, deterministic policy enforces forbidden tokens (no Hermes mention; no raw URLs when CTAs available; no internal IDs leaked) before send.

Violation of this boundary in any future SKILL is a `BLOCKER` review finding by default.

---

## 5. State files and schemas

All under `state/commerce/` per-VPS (`/opt/shift-agent/state/commerce/...` on prod). Slice 1 ships the schemas in `src/platform/schemas.py` alongside the existing Pydantic v2 models.

### `state/commerce/carts.json` — `CommerceCartStore`

```python
class CommerceCartItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str                 # resolved canonical SKU; never LLM free-text
    display_name: str        # for receipt + reply rendering
    quantity: int            # integer units only in slice 1 — fractional units (Decimal) deferred to slice 2 with explicit unit-conversion module + rounding mode (Reviewer A HIGH-2)
    unit: Literal["each", "lb", "kg", "tray", "platter", "gal", "qt"]
    unit_price_cents: int    # immutable snapshot at add-time
    line_total_cents: int    # = quantity * unit_price_cents (no rounding — both ints)
    added_at: datetime

class CommerceCart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cart_id: str             # opaque short id
    sender_phone: Optional[E164Phone] = None   # mirror RawInbound shape (Reviewer A BLOCKER-2)
    sender_lid: Optional[str] = None
    chat_id: str
    items: list[CommerceCartItem]
    subtotal_cents: int      # = sum(item.line_total_cents); idempotent recompute on every write; assert invariant
    currency: Literal["USD", "INR", "CAD", "GBP", "EUR"]   # extend as needed
    status: Literal["open", "checked_out", "expired", "cleared"]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime     # = updated_at + 4h on every mutation

    @model_validator(mode="after")
    def _require_sender_identity(self) -> "CommerceCart":
        if self.sender_phone is None and self.sender_lid is None:
            raise ValueError("CommerceCart requires at least one of sender_phone or sender_lid")
        return self

class CommerceCartStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    carts: list[CommerceCart] = Field(default_factory=list)
    def find_open(self, sender_phone: Optional[str], sender_lid: Optional[str], chat_id: str) -> Optional[CommerceCart]: ...
    def next_cart_id(self) -> str: ...
```

### `state/commerce/orders.json` — `CommerceOrderStore`

```python
CommerceOrderStatus = Literal[
    "pending_payment", "awaiting_approval", "paid",
    "preparing", "ready", "out_for_delivery",
    "completed", "cancelled", "voided",
]

class CommerceOrderStatusEvent(BaseModel):
    """Append-only status-history entry; mirrors deployed list[dict] convention
    at src/platform/schemas.py:2389 but typed for slice 1 schema discipline.
    (Reviewer A BLOCKER-1)"""
    model_config = ConfigDict(extra="forbid")
    from_status: Optional[CommerceOrderStatus]  # None for the initial event
    to_status: CommerceOrderStatus
    ts: datetime
    cause: str               # e.g., "customer_checkout", "payment_confirmed", "operator_cancel"
    actor: Literal["customer", "caller", "operator", "cron", "webhook"]
    event_ref: str = ""      # optional FK to triggering audit row / intent_id / message_id

class CommerceOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str            # e.g., "CO0001"
    sender_phone: Optional[E164Phone] = None  # mirror RawInbound shape (Reviewer A BLOCKER-2)
    sender_lid: Optional[str] = None
    chat_id: str
    cart_id: str             # FK to CommerceCart at checkout
    line_items: list[CommerceCartItem]  # snapshotted at order create
    subtotal_cents: int
    tax_cents: int           # 0 in slice 1; tax module is separate
    fee_cents: int           # 0 in slice 1
    total_cents: int
    currency: str
    status: CommerceOrderStatus
    payment_intent_id: str = ""       # FK to CommercePaymentIntent (empty until minted)
    payment_reference: str = ""       # immutable once set; mirrors flyer/guest_order.py
    status_history: list[CommerceOrderStatusEvent]  # append-only; typed
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _require_sender_identity(self) -> "CommerceOrder":
        if self.sender_phone is None and self.sender_lid is None:
            raise ValueError("CommerceOrder requires at least one of sender_phone or sender_lid")
        return self

class CommerceOrderStore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orders: list[CommerceOrder] = Field(default_factory=list)
    def find_by_id(self, order_id: str) -> Optional[CommerceOrder]: ...
    def next_order_id(self) -> str: ...
```

### State machine (slice 1 enforced transitions)

Defined as a `frozenset[tuple[from, to]]` constant in `order_state.py` (Reviewer A MEDIUM-2). Tests assert against the constant rather than re-listing transitions:

```python
LEGAL_TRANSITIONS: frozenset[tuple[CommerceOrderStatus, CommerceOrderStatus]] = frozenset({
    # pending_payment → ...
    ("pending_payment", "awaiting_approval"),   # caller-side gate (slice 2 catering)
    ("pending_payment", "paid"),                # webhook confirmation (slice 2)
    ("pending_payment", "cancelled"),           # customer or operator cancel
    ("pending_payment", "voided"),              # TTL expiry OR operator void
    # awaiting_approval → ...
    ("awaiting_approval", "pending_payment"),   # owner approves; resume pay flow
    ("awaiting_approval", "cancelled"),         # owner rejects
    # paid → ...
    ("paid", "preparing"),                      # staff acknowledges
    ("paid", "refunded"),                       # post-payment refund (slice 2+)
    # preparing → ...
    ("preparing", "ready"),
    ("preparing", "cancelled"),                 # operator-only (rare)
    # ready → ...
    ("ready", "out_for_delivery"),
    ("ready", "completed"),                     # pickup
    # out_for_delivery → ...
    ("out_for_delivery", "completed"),
    # refunded/cancelled/completed/voided are TERMINAL — no outbound edges
})

TERMINAL_STATUSES: frozenset[CommerceOrderStatus] = frozenset({
    "completed", "cancelled", "voided", "refunded",
})
```

`transition(order, to_status, ...)` raises `IllegalCommerceTransition` if the edge is not in `LEGAL_TRANSITIONS`. Note: `refunded` is added to the enum here (joining `CommerceOrderStatus` Literal) since the transition table requires it — update §5 enum accordingly.

Notes:
- **Post-payment cancellation is NOT supported.** Money already moved → use `refunded` instead. Reviewer B MEDIUM-1 ordering: a chargeback that arrives after a refund is still logged as `commerce_payment_chargeback_received` but does NOT auto-flip state (operator action only).
- **`voided` and `refunded` are distinct terminal states.** Void = intent cancelled before payment; refund = money returned after payment. Auditors and Cash & AR reconciliation rely on the distinction.
- **`awaiting_approval`** is reserved for catering-deposit-like flows; slice 1 callers may not exercise it directly, but the state-machine constant accepts the transition.

### `state/commerce/payment_intents.json` — `CommercePaymentIntentStore`

```python
class CommercePaymentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: str           # e.g., "CPI00001"
    order_id: str            # idempotency key — one live intent per order_id
    originating_message_id: str  # FK to inbound that triggered mint (Reviewer A HIGH-3; mirrors flyer/guest_order.py:81)
    amount_cents: int
    currency: str
    provider: Literal["placeholder", "stripe", "razorpay", "upi", "zelle", "cashapp", "manual"]
    checkout_url: str        # "" if unconfigured; callers MUST emit unconfigured-message
    status: Literal["minted", "sent", "confirmed", "voided", "refunded", "chargeback"]
    payment_reference: str = ""  # immutable once set
    created_at: datetime
    updated_at: datetime
    voided_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None
    refunded_amount_cents: int = 0   # supports partial refunds (Reviewer B MEDIUM-1)
    chargeback_received_at: Optional[datetime] = None
```

### Concurrency / locking story (Reviewer A MEDIUM-1)

Slice 1: single-writer-per-VPS per state file. Only the operator-invoked scripts write; no cron + script race. Inherit Flyer's pattern at `src/agents/flyer/guest_order.py` (load → mutate → `safe_io.atomic_write_*` on full doc). `fcntl.flock` on `<path>.lock` is NOT required for slice 1.

Slice 2: webhook receiver daemon will need explicit locking. Add `safe_io.flock_state_path(path)` context manager at that PR; protect the read-modify-write across `payment_intents.json` updates that race with operator script invocations.

### `state/commerce/payment_references.json` — `CommercePaymentReferenceLedger`

```python
class CommercePaymentReferenceLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: dict[str, str] = Field(default_factory=dict)  # reference → first_order_id
    # immutable cross-order dedup; any re-use against a different order_id returns
    # commerce_payment_dedup_blocked (mirrors flyer/guest_order.py:108-113)
```

### `state/commerce/catalog.json` — `CommerceCatalog` (slice 2 — designed now for stability)

```python
class CommerceCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str
    display_name: str
    aliases: list[str]       # synonyms + multilingual aliases
    category: str
    unit: Literal["each", "lb", "kg", "tray", "platter", "gal", "qt"]
    unit_price_cents: int
    currency: str
    available: bool          # static fallback; inventory-backed overlay in slice 3+

class CommerceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[CommerceCatalogItem] = Field(default_factory=list)
    last_updated: datetime
    def lookup(self, query: str) -> list[CommerceCatalogItem]: ...
```

---

## 6. Compliance matrix (GATING for slice 2+; slice 1 ships under prohibited-category guard)

This section is the upstream gate for any caller wiring beyond slice 1. **Slice 1 itself is compliance-safe because it is library-only with no customer-facing copy** — but callers must respect this matrix from the first wired flow onward.

| Category | Slice 1 (library-only) | Slice 2+ (real callers) | Audit on refusal | Notes |
|---|---|---|---|---|
| Grocery (standard goods) | ✓ allowed | ✓ allowed | n/a | Default-permissive. WhatsApp Business Policy treats this as standard commerce. |
| Prepared food / restaurant takeout | ✓ allowed | ✓ allowed (with §10 24h-window discipline) | n/a | Catering deposits are this category. |
| Raw meat (goat, lamb, halal/zabiha, etc.) | ✗ filtered from catalog; orders refused | ⚠ **BLOCKED pending Meta-policy verification** | `commerce_order_create_refused_category` | Meta Commerce Policy historically restricts animal-product surfaces. Conversational ordering is not automatically exempt. **Operator must verify the specific 2026 policy clause before slice 2 wires any raw-meat flow.** Implementation behind `cfg.commerce.allow_restricted_categories=False` default. |
| Catering deposits (general cuisine) | ✗ no caller in slice 1 | ✓ allowed (canonical first caller — Catering Agent #2 `send_deposit_link`) | n/a | Money-moving discipline §7 applies. |
| Alcohol | ✗ blocked | ✗ blocked indefinitely | `commerce_order_create_refused_category` | Explicit exclusion. In `permanently_blocked_categories`; cannot be enabled by config toggle. |
| Tobacco | ✗ blocked | ✗ blocked indefinitely | `commerce_order_create_refused_category` | Same as alcohol. |
| Age-gated goods (lottery, prescription, etc.) | ✗ blocked | ✗ blocked indefinitely | `commerce_order_create_refused_category` | Out of scope for SMB-Agents. |
| **Live animals** (e.g., goat for Bakrid) | ✗ blocked | ✗ blocked indefinitely | `commerce_order_create_refused_category` | Reviewer B HIGH-2 add: Meta Commerce Policy explicitly prohibits live-animal sales. Joins `permanently_blocked_categories`. |
| **Religious-restriction SKUs** (per-VPS — e.g., beef in Hindu-vegetarian-only store; pork in halal store) | ✗ filtered via per-VPS exclusion list | ✗ filtered via per-VPS exclusion list | `commerce_order_create_refused_category` (with `reason="per_vps_exclusion"`) | Reviewer B HIGH-2 add: NOT a Meta-policy block but a per-customer-VPS catalog policy. Field: `cfg.commerce.per_vps_excluded_categories: list[str] = []` — operator configures per store. Distinct from global `RESTRICTED_CATEGORIES` which encodes Meta-policy. |
| Promotions / marketing opt-in | n/a (Commerce is inbound capture) | Marketing-agent boundary | n/a | Flyer / #11 / v3 #38 own outbound; Commerce primitives never initiate marketing. |
| 24-hour window / template rules | ✓ honored (no outbound from primitives) | ⚠ caller-side hook deferred to slice 2 | n/a | Reviewer B HIGH-1: the primitive layer does NOT currently expose `last_inbound_at`. Slice-2 hook will add it. Until then, callers MUST themselves resolve last-inbound timestamp from `decisions.log` `raw_inbound` rows for any reply that could land >24h after the customer's last inbound; commerce primitives do not provide the timestamp. Add to slice-2 design: `commerce_order_status_change` + `commerce_payment_link_sent` carry `last_inbound_at: Optional[datetime]` populated by the calling SKILL. |
| Payment links in chat | ✓ allowed (link only) | ✓ allowed (link only) | n/a | Meta policy: do not request full card numbers or financial account numbers in chat. Commerce primitives only ever send the *link* (provider-hosted page handles card capture). Any future skill that prompts for a card number in chat is a `BLOCKER`. |
| PII data retention | ✓ minimal (phone/LID/chat_id only) | ✓ minimal | n/a | `CommerceCart` and `CommerceOrder` carry phone/LID/chat_id (already in audit log) + SKU/qty/price. No name, address, or card data in commerce state files. |

### Enforcement mechanism

- `cfg.commerce.allow_restricted_categories: bool = False` — top-level kill switch. When False (default), `commerce_catalog.lookup` filters out any SKU whose category is in `RESTRICTED_CATEGORIES = ("raw_meat", "alcohol", "tobacco", "age_gated", "live_animals")` and `commerce_order_state.create()` refuses orders containing such SKUs, emitting `commerce_order_create_refused_category`.
- `cfg.commerce.permanently_blocked_categories: tuple[str, ...] = ("alcohol", "tobacco", "age_gated", "live_animals")` — Reviewer A MEDIUM-4 fix: `tuple[str, ...]` (YAML/JSON round-trippable) instead of `frozenset[str]`; coerced to `frozenset` internally via model validator. Cannot be overridden by config alone; requires explicit `BlockedCategoryOverride(reason: str, approver: str, expires_at: datetime)` model with audit row `commerce_blocked_category_override`.
- `cfg.commerce.per_vps_excluded_categories: tuple[str, ...] = ()` — operator-configured per-VPS exclusion (Reviewer B HIGH-2). Distinct from Meta-policy blocks; filters at catalog-lookup AND order-create time with `commerce_order_create_refused_category` emitting `reason="per_vps_exclusion"`.

### Deferred to §12 (not slice 1 or slice 2 scope)

- **FDA-import-flagged items** (Reviewer B HIGH-2): certain jaggery, spices, dairy from non-FDA-registered importers. Out of slice scope but appears in §12 so it isn't silently treated as standard grocery.

---

## 7. Payment-link discipline

Inherited from `tasks/hermes-commerce-portfolio-reconciliation.md` §"Money-moving invariants" (binding) + this PRD's full enumeration:

| Concern | Rule |
|---|---|
| Idempotency key | `order_id` (single key; no amount component). Re-mint against same `order_id` returns the existing live intent. |
| Amount change | Requires explicit `commerce_payment_intent_voided` row + new intent (under same or new `order_id` per caller's flow). Never two live intents per order. |
| Immutable `payment_reference` | Cross-order: a reference once stored in `payment_references.json` blocks re-use indefinitely, including against cancelled / voided / refunded orders. Mirrors `flyer/guest_order.py:108-113` + 2026-05-25 lesson. |
| TTL on `pending_payment` intent | 24h default; configurable per provider. Expired intents auto-transition to `voided` via cron (`prune-commerce-payment-intents`). |
| Dedup window | The reference-ledger lookup is the dedup mechanism (immutable; not a time window). |
| Refund / cancel transitions | `voided`: intent cancelled before payment; allowed on `pending_payment` or `awaiting_approval`. `refunded`: refund issued post-payment; allowed on `paid` or any post-paid status. `chargeback`: provider-initiated reversal; logged but does not auto-mutate state (operator action required). |
| Approval threshold for owner gate | `cfg.commerce.owner_approval_amount_cents_threshold: Optional[int] = None` — Reviewer B HIGH-3 fix: default `None` (UNCONFIGURED) is fail-closed. The primitive raises `CommerceOwnerApprovalThresholdUnconfigured` when a caller invokes the approval-gated path without the operator setting a threshold. Operator sets threshold per VPS (e.g., catering deposit caller in slice 2 will require `cfg.commerce.owner_approval_amount_cents_threshold=20000` or whatever the operator chooses). Mirrors the empty-checkout-URL fail-closed pattern. The primitive logs `commerce_order_owner_approval_required` when threshold is configured AND the order amount crosses it. |
| Audit row per state edge | Every transition writes a `LogEntry`. State edges = order status change, intent status change, reference ledger insertion. No silent state mutations. |
| Webhook signature verification | Slice 2 daemon REQUIRES provider HMAC verification before applying state. `commerce_payment_webhook_verify_failed` rows must include the raw signature header + computed digest for operator forensics (no payload secrets leaked beyond what the provider already echoed). |
| Customer-visible "send payment" copy gate | Per §6: callers MUST NOT emit payment copy for raw meat / alcohol / tobacco / age-gated until the matrix evolves. Enforced at caller layer + asserted via `commerce_catalog.lookup` filter. |
| Empty checkout URL guard | Per reconciliation invariant #2: `_checkout_url(...) == ""` → caller MUST emit "Payment link is not configured yet" copy + MUST NOT render a bare URL. `assert_payment_url_renderable()` helper in `commerce/payment_link.py` enforces. |

---

## 8. Audit `LogEntry` variants (full enumeration; slice 1 ships marked subset)

To be added to `src/platform/schemas.py` discriminated union (mirrors existing pattern at l.2535+). All inherit `_BaseEntry`, `type: Literal["..."]`, `extra="forbid"`.

| Variant | Fields | Slice 1 emits? |
|---|---|---|
| `commerce_cart_started` | cart_id, sender_key, chat_id, ts | ✓ |
| `commerce_cart_updated` | cart_id, op `Literal["add","remove","update_qty","clear"]`, sku, qty_before, qty_after, subtotal_cents, ts | ✓ |
| `commerce_cart_cleared` | cart_id, reason, ts | ✓ |
| `commerce_cart_expired` | cart_id, expired_at, ts | ✓ (via prune cron in slice 1) |
| `commerce_cart_checked_out` | cart_id, order_id, subtotal_cents, ts | ✓ |
| `commerce_order_created` | order_id, cart_id, sender_key, total_cents, currency, ts | ✓ |
| `commerce_order_status_change` | order_id, prev_status, next_status, actor `Literal["customer","caller","operator","cron"]`, ts | ✓ |
| `commerce_order_cancelled` | order_id, reason, actor, ts | ✓ |
| `commerce_order_create_refused_category` | sender_key, refused_skus, category, ts | ✓ |
| `commerce_order_owner_approval_required` | order_id, amount_cents, ts | reserved (slice 2 caller wiring) |
| `commerce_payment_intent_minted` | intent_id, order_id, originating_message_id, amount_cents, currency, provider, ts | ✓ (provider="placeholder" only) |
| `commerce_payment_link_attempted` | intent_id, order_id, ts | ✓ (Reviewer A MEDIUM-3: attempted/sent/failed triple required for reconciler-orphan-detector pattern; slice 1 emits before placeholder send too) |
| `commerce_payment_link_sent` | intent_id, order_id, ts | ✓ |
| `commerce_payment_link_failed` | intent_id, order_id, reason, ts | reserved (slice 2 — provider send failure path) |
| `commerce_payment_confirmed` | intent_id, order_id, payment_reference, ts | reserved (slice 2 webhook) |
| `commerce_payment_dedup_blocked` | reference, attempted_order_id, original_order_id, ts | reserved (slice 2 webhook) |
| `commerce_payment_webhook_received` | provider, intent_id_claimed, verified, ts | reserved (slice 2) |
| `commerce_payment_webhook_verify_failed` | provider, raw_signature, computed_digest, ts | reserved (slice 2) |
| `commerce_payment_intent_voided` | intent_id, order_id, reason, actor, ts | ✓ (manual void via operator script) |
| `commerce_payment_refunded` | intent_id, order_id, refund_reference, amount_cents, is_partial, ts | reserved (slice 2+). `is_partial: bool` supports partial-refund flows (Reviewer B MEDIUM-1). |
| `commerce_payment_chargeback_received` | intent_id, order_id, provider_reference, amount_cents, arrived_after_refund, ts | reserved (slice 2+). `arrived_after_refund: bool` flags the double-debit edge case (Reviewer B MEDIUM-1); operator-action-only state mutation. |
| `commerce_blocked_category_override` | category, reason, approver, expires_at, ts | reserved |

Adding all 20 to the discriminated union in slice 1 is intentional: later additions require schema migration of any persisted audit log entries that referenced an older closed union. Reserving them now is cheap; deferring is expensive.

---

## 9. Dispatcher routing changes

**Slice 1: NONE.** Per reconciliation §"Slice 1 inbound reachability" — primitives ship library-only.

**Slice 2 (when first customer flow lands):** A new dispatcher row may be added if and only if there is a customer flow with no other owning agent. The row must:

1. Match an explicit `cfg.commerce.enabled=True` gate.
2. Position AFTER the catering-keyword row (catering inquiries with food words must not be swallowed).
3. Position AFTER catering's PR-CF1 finalize-intent row (`SKILL.md:107`) — checkout verbs like "ready to order" overlap with finalize verbs.
4. Position AFTER `flyer_dispatcher` (active flyer project takes priority).
5. Position BEFORE the catch-all owner/employee/unknown rows.
6. Write `dispatcher_routed` audit BEFORE delegating.

**Catalog-ingest collision (Reviewer A HIGH-2):** A future `commerce_catalog` ingest of owner-supplied catalog image/CSV would collide with `SKILL.md:92` "image-no-caption, owner self-chat → update_catering_menu". If/when catalog ingest is built, the SKILL must require an explicit `catalog` caption keyword AND `cfg.commerce.catalog_ingest_enabled=True` to disambiguate.

---

## 10. Handoff workflow (not a trigger list)

Per Phase B Reviewer A guidance: handoff is a workflow, not a list of trigger conditions. The Commerce primitives define handoff state explicitly:

```
Trigger ──▶ State transition ──▶ Audit row ──▶ Customer-visible reply ──▶ Operator-visible Cockpit row ──▶ Resolution
```

### Slice 1 handoff workflow (programmatic-only — operator script entry points)

| Trigger | State transition | Audit | Customer reply | Cockpit | Resolution |
|---|---|---|---|---|---|
| Restricted category in cart | `commerce_order_state.create` refuses; cart `status` stays `open` | `commerce_order_create_refused_category` | "Some items in your cart can only be ordered by phone. Please call the store to complete this order." (Reviewer B MEDIUM-4: category-agnostic; does not leak which SKUs are restricted) | Row appears under "Refused orders" tab (slice 2 Cockpit) | Operator marks complete via Cockpit OR customer removes item + retries |
| Empty checkout URL (unconfigured template) | `commerce_payment_link.mint` returns intent with `checkout_url=""` | `commerce_payment_intent_minted` (provider=placeholder, checkout_url="") | Caller MUST send "Payment link is not configured yet. We'll send it when it's ready." | Row under "Unconfigured-payment" tab | Operator configures `payment.checkout_url_template` |
| Payment-reference dedup block | `commerce_payment_link.confirm` returns `dedup_blocked` | `commerce_payment_dedup_blocked` | "This payment confirmation matches a previous order. If this wasn't you, please contact the store immediately." (Reviewer B MEDIUM-3: removes implicit blame; safe for fraud-victim customer too) | Row under "Dedup alerts" — high priority | Operator reviews; explicit override only if confirmed legitimate |
| Cart TTL expired with items | Cart auto-cleared by cron | `commerce_cart_expired` | None (cron is silent — last activity was 4h+ ago) | Row under "Expired carts" tab | None required |
| **Order TTL voided** (24h `pending_payment` expiry → `voided`) | `commerce_order_state.transition(order, "voided", actor="cron", cause="ttl_expired")` | `commerce_order_status_change` + `commerce_payment_intent_voided` | Caller MUST send "Your order has expired. Reply to start a new one." (Reviewer B MEDIUM-2: prevents the 2026-05-25 silent-abandonment lesson) | Row under "TTL-voided orders" tab | None required; customer can restart |
| **Owner-approval threshold unconfigured** (caller invokes approval-gated path without `cfg.commerce.owner_approval_amount_cents_threshold` set) | Primitive raises `CommerceOwnerApprovalThresholdUnconfigured` | `commerce_order_owner_approval_threshold_unconfigured` (new variant; reserved) | Caller emits caller-domain fallback (NOT a commerce-primitive responsibility) | Cockpit shows configuration warning | Operator sets threshold; caller retries |
| **Partial customer reply not parseable by caller** (e.g., "what about the goat?" mid-cart) | No state change in primitives | None at primitive layer | Caller MUST send scoped recovery message; MUST NOT fall through to generic LLM (2026-05-13 lesson — Flyer-pattern bug) | Cockpit silent | Caller handles in its own SKILL prose |

### Slice 2+ handoff additions (when callers wire in)

- **Order needs owner approval** (amount > threshold): `commerce_order_status_change` from `pending_payment` → `awaiting_approval`; sends owner an approval card with `#XXXXX` code; on approval transitions to `pending_payment` proper; on rejection transitions to `cancelled`.
- **Webhook signature verification failure**: not auto-applied; operator gets a Cockpit alert; manual reconciliation only.
- **Catering deposit caller** (Catering Agent #2): catering writes its own catering-domain audit rows AND requires `commerce_order_id` + `commerce_payment_intent_id` cross-references (per reconciliation invariant #4). Cash & AR joins on these fields.

---

## 11. Test plan

Per `docs/hermes-alignment.md` Part 1 — deterministic Python scripts get pytest with subprocess-invoke + assert on file mutations + stdout. Mirrors `tests/test_catering_v02_scripts.py`.

### Slice 1 test files (new)

- `tests/test_commerce_cart.py` — cart create, item add/remove/update/clear, 4h TTL expiry, sender_key resolution (phone path + LID path), idempotent re-add, currency consistency check.
- `tests/test_commerce_order_state.py` — order create from cart, every legal state transition + every illegal transition (must raise), status_history append-only, idempotent re-create on same cart_id.
- `tests/test_commerce_payment_link.py` — intent mint with placeholder provider, idempotent re-mint returns same intent, void transition, payment_reference dedup ledger (block on cross-order re-use, allow same-order replay), `assert_payment_url_renderable` empty-URL guard.
- `tests/test_commerce_logentry_variants.py` — every new variant accepts canonical fields and rejects extras (`extra="forbid"` discipline).
- `tests/test_commerce_restricted_category_guard.py` — `cfg.commerce.allow_restricted_categories=False` filters out restricted SKUs in `commerce_catalog.lookup`; `commerce_order_state.create` refuses orders containing restricted SKUs; permanent-block categories cannot be overridden by config alone.

### Slice 1 smoke (manual; no automated E2E in slice 1)

- Operator runs `python -m platform.scripts.commerce-cart add --sender-key=+15551234567 --chat-id=test --sku=ITEM001 --qty=2`; verifies state-file mutation + audit row.
- Operator runs `commerce-order create --cart-id=<id>`; verifies order row + status_history.
- Operator runs `commerce-payment-link mint --order-id=CO0001` against an unconfigured `payment.checkout_url_template`; verifies `checkout_url=""` and audit `commerce_payment_intent_minted` row.

### Verification gates before PR

1. `pytest tests/test_commerce_*.py` all green.
2. `pytest tests/` overall green (no regressions in existing tests — schemas.py edits touch the LogEntry union).
3. Manual smoke against a scratch state dir confirms primitives behave per design.
4. **CI grep gate (Reviewer A MEDIUM-5):** `tests/test_commerce_audit_chokepoint.py` greps `src/platform/commerce/` for any direct write/append references to `decisions.log` — fails CI if commerce code bypasses the `safe_io.ndjson_append` chokepoint or the `/usr/local/bin/log-decision-direct` script.
5. State machine constant `LEGAL_TRANSITIONS` is the single source of truth; `tests/test_commerce_order_state.py` asserts that every (from, to) outside the set raises `IllegalCommerceTransition` AND every (from, to) inside succeeds (Reviewer A MEDIUM-2).
6. No customer-visible flow exists yet (library-only), so no live-WhatsApp smoke required.

### Slice 2 (deferred — for awareness)

- Real provider client tests with HTTP mocks (`responses` or similar).
- Webhook receiver E2E with signed test payloads.
- Cockpit integration test (jest + react-testing-library mirroring `test_flyer_*` patterns).
- Dispatcher-routing tests in `tests/test_cf_router_*` pattern.

---

## 12. Known deferred items / non-goals

- Real payment-provider API integration (any).
- Webhook receiver daemon.
- Catering deposit caller wiring.
- Cockpit Commerce view.
- `commerce_catalog` slice.
- Dispatcher matrix amendment.
- Tax/fee calculation (currently `tax_cents=0, fee_cents=0` in slice 1; tax module is a separate concern).
- Delivery scheduling (#25 backlog; orthogonal).
- Order status query API (#23 backlog; will provide read-only accessor when promoted).
- Loyalty/preferences integration (VIP #9 / v3 #32 / v3 #33; consume audit log via existing pattern).
- Multi-currency conversion logic (each cart fixed to one currency; cross-currency is a separate problem).
- Fractional units (1.5 lb, 0.5 tray): slice 1 requires `quantity: int`; Decimal-based fractional units with explicit rounding-mode policy deferred to slice 2's dedicated unit-conversion module (Reviewer A HIGH-2).
- **FDA-import-flagged categories**: certain jaggery, spices, dairy from non-FDA-registered importers. Out of scope but flagged here so they aren't silently treated as standard grocery (Reviewer B HIGH-2).
- **Currency mismatch handling** (intent USD, webhook reports INR): slice 2 webhook receiver must reject mismatched-currency confirmations and emit `commerce_payment_webhook_currency_mismatch`; reserve as future LogEntry variant when slice 2 lands.
- **`last_inbound_at` primitive-side population**: deferred to slice 2 (Reviewer B HIGH-1). Until then, callers must resolve last-inbound timestamp from `decisions.log` themselves for 24h-window decisions.

---

---

## 12.5 Phase D review summary (2026-05-28)

Two parallel reviewers ran with non-overlapping lenses on the PRD v2 draft:

- **Reviewer A (structural/code design vs deployed Hermes patterns):** `APPROVE_WITH_RECOMMENDATIONS` — 2 BLOCKERs (`status_history` typed, `sender_phone`/`sender_lid` split), 3 HIGHs (audit-name consistency, `line_total_cents` rounding via `quantity: int` slice-1 restriction, `originating_message_id` on intent), 5 MEDIUMs (locking story, illegal-transition enumeration, attempted/sent/failed triple, `tuple[str,...]` config field, CI chokepoint grep), 4 LOWs (module placement confirmed; slice-1 isolation confirmed; reserve `awaiting_approval`; drift-tag accurate).
- **Reviewer B (product/compliance/payment failure modes):** `APPROVE_WITH_RECOMMENDATIONS` — 3 HIGHs (`last_inbound_at` fictional → deferred to slice 2; ethnic SMB category gap → added live_animals, per_vps_excluded_categories, FDA flag in deferred; approval threshold default → `Optional[int] = None` fail-closed), 4 MEDIUMs (refund/chargeback ordering, order-void TTL customer notification, dedup-block copy revictimization, refusal copy inventory-shape leak), 3 LOWs (20-variant scope appropriate; compliance-matrix grep anchors; lessons cross-check clean).

**All BLOCKERs + HIGHs + MEDIUMs applied.** PRD is ready to gate the slice-1 build.

---

## 13. Open questions for reviewers

Inherited from reconciliation §"Open questions":

1. Is there a target first customer for Commerce primitives, or are we building speculative shared infrastructure? (Affects slice 2+ prioritization, not slice 1.)
2. For `commerce_payment_link` slice 2: which payment provider has credentials available first?
3. Resolved: catering is the canonical first caller (per reconciliation Phase B review).

New open questions specific to this PRD:

4. Is the 20-variant LogEntry expansion in slice 1 acceptable, or should slice 1 ship only the 8-9 emitted variants and add reserved ones JIT? Trade-off: schema-migration cost vs schema-bloat cost.
5. Should slice 1 ship `awaiting_approval` in the order-status enum even though no caller uses it yet, to reserve the slot? (PRD recommends yes; reviewers may push back.)
6. Customer-copy policy for the four slice-1 handoff cases — exact copy strings need operator approval before any caller wires in. Slice 1 leaves them as placeholder strings; first wiring PR finalizes.
