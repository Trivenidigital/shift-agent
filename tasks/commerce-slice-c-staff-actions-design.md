# Design — Commerce Slice C: staff actions / order status transitions

**Status:** DESIGN ONLY — not approved for implementation. This is the **first write surface** for Commerce orders. Requires Codex-CLEAN (Hermes/drift · product/scope · runtime-state/operator-gate · money/payment/compliance · **write-safety/concurrency**) **and** operator approval before any build.

**Drift-check tag:** extends-Hermes — adds a mutating cockpit route + UI on top of the existing `commerce.order_state.transition` primitive and the deployed cockpit auth; closes a verified concurrency gap by wrapping the read-modify-write in **both `create()` and `transition()`** with one shared `FileLock` (the deployed `safe_io.flock` convention, routed through `_io_shim` for Windows dev/test parity); and adds one additive failure-audit `LogEntry` variant. No new storage/identity/messaging substrate; no dispatcher; no provider/POS activation.

---

## 0. Smallest-safe recommendation (the headline)

**Ship only owner-initiated _fulfillment-progress_ transitions + pre-payment cancel.** Concretely the allowed Slice-C actions are: `paid→preparing`, `preparing→ready`, `ready→out_for_delivery`, `out_for_delivery→completed`, `ready→completed` (pickup), and `cancel` (**pre-payment only**: `pending_payment`/`awaiting_approval`→`cancelled`). **DEFER** everything money/provider-touching: manual `→paid` (mark-paid), `→refunded`, `pos_sync_status` edits, and any customer notification. Rationale: the kitchen/fulfillment flow is operationally safe and money-neutral; the payment/refund/POS states are the source-of-truth of an external provider and must stay provider/operator-gated (Slices E/F).

This keeps Slice C: owner-only, money-neutral, customer-silent, and dormant-in-practice (it only acts on orders that exist — none until Slice D/activation; testable via seeded orders).

## 1. Hermes-first capability checklist

| Step | Tag | Note |
|---|---|---|
| 1. Validate + apply a legal status transition (matrix, idempotent, terminal) | `[Hermes-built]` | `commerce.order_state.transition` already enforces `LEGAL_TRANSITIONS`/`TERMINAL_STATUSES`, idempotent noop, `IllegalCommerceTransition` |
| 2. Append-only audit of the change | `[Hermes-built]` | `transition` emits `commerce_order_status_change`; `cancel` emits `commerce_order_cancelled` (both existing `LogEntry` variants) |
| 3. Authn/step-up for a sensitive write | `[Hermes-built]` | cockpit `require_auth` + `require_fresh_otp` (used by flyer mutating endpoints) |
| 4. Mutating cockpit route `POST /commerce/orders/{id}/transition` | `[net-new]` | thin wrapper over `transition`; mirrors flyer POST pattern |
| 5. File-lock the load→write critical section (close the race) | `[net-new]` | `safe_io.FileLock` on `orders.json.lock` (deployed locking convention) |
| 6. Optimistic-concurrency guard (`expected_from_status`) | `[net-new]` | reject stale-view actions; no silent overwrite |
| 7. Failure-audit variant for refused cockpit actions | `[net-new]` | additive `LogEntry` variant `commerce_order_action_refused` |
| 8. Cockpit UI action buttons (disabled-aware, confirm-on-destructive) | `[net-new]` | detail-drawer actions in `CommerceOrders.tsx` |

## 2. Drift-rule self-checks (read-deployed-code done)

- ✅ Read `src/platform/commerce/order_state.py` (`transition` lines 218-272, `cancel` 275-311, `LEGAL_TRANSITIONS` 29-54, `TERMINAL_STATUSES` 56-58, `OrderOpResult`). Confirmed: idempotent noop on same-status; raises `IllegalCommerceTransition`; emits audit; **no `FileLock`/`flock` around load→write** (last-writer-wins risk).
- ✅ Read `src/platform/commerce/audit.py` (`emit()` — raw NDJSON append via `safe_io.ndjson_append`; caller builds a dict conforming to a `commerce_*` `LogEntry` variant; not re-validated on the hot path).
- ✅ Read `src/platform/schemas.py` (`CommerceOrderStatusChange` 5181 — order_id/prev_status/next_status/actor/cause; `CommerceOrderCancelled` 5190 — order_id/reason/actor; actor Literals include `operator`). No existing *refused/denied* commerce-action variant.
- ✅ Read `web/backend/app/auth.py` — **single-user model**: `owner.phone` is the only user; JWT `sub`=owner_phone; `require_auth` + `require_fresh_otp` step-up. **No admin/staff roles, no org/tenant model.**
- ✅ Read `web/backend/app/routers/commerce.py` (Slice B read-only) + `flyer.py` (mutating POST pattern: `require_fresh_otp` + `audit_log` + state write).

Deployed-pattern compliance: JSON-on-disk + `safe_io` atomic write **+ `FileLock`** (Slice C adds the missing lock); NDJSON audit via the commerce `emit` chokepoint; Pydantic v2 `extra="forbid"`; single-tenant per VPS; mutating routes gate on `require_fresh_otp`.

## 3. Transition matrix (Slice C subset of `LEGAL_TRANSITIONS`)

Source of truth stays `order_state.LEGAL_TRANSITIONS`; Slice C exposes a **subset** to the cockpit (an allowlist of operator-safe transitions), not the whole machine.

| From | Allowed (Slice C) | Forbidden in Slice C (why) |
|---|---|---|
| `pending_payment` | `cancelled` | `paid` (manual mark-paid = money claim → DEFER), `awaiting_approval`/`voided` (caller/webhook-driven) |
| `awaiting_approval` | `cancelled` | `paid` (provider/webhook), `pending_payment` (caller-driven) |
| `paid` | `preparing` | `refunded` (money → DEFER) |
| `preparing` | `ready` | `cancelled` (post-payment cancel without a refund path = money/state mismatch → route via the deferred refund path, not Slice C) |
| `ready` | `out_for_delivery`, `completed` | — |
| `out_for_delivery` | `completed` | — |
| terminal (`completed`/`cancelled`/`voided`/`refunded`) | none | all (terminal) |

- **Idempotent same-state:** re-applying the current status → success no-op (`noop_already_in_status`), no new audit row, no error (matches `transition`).
- **Illegal transition:** rejected with a clear error (HTTP 409) + a `commerce_order_action_refused` audit row; never mutates.
- **Terminal:** no outbound transitions; UI disables all actions.
- **Rollback/cancel:** `cancel` is **pre-PAYMENT only** in Slice C (`pending_payment`/`awaiting_approval` → `cancelled`). `preparing→cancelled` is **EXCLUDED** (preparing is post-`paid`; cancelling a paid order without a defined refund leaves an order/money-state mismatch). Any post-payment reversal goes through the **DEFERRED** refund path (`→refunded`, provider/operator-gated). No "un-complete" / backward transitions (not in `LEGAL_TRANSITIONS`).

## 4. Authority matrix (against the ACTUAL cockpit auth)

**Reconciliation/flag:** the shift-agent cockpit is **single-user** (`owner.phone`; JWT `sub`=owner). There is **no admin/staff role tier and no org/tenant model** in this cockpit. (The "Vizora auth/org model" referenced in the task belongs to a *different* project; its multi-org/role model does **not** exist here and must not be invented.)

| Concern | Slice C decision |
|---|---|
| Who can act | The single authenticated **owner** only (`require_auth`). |
| Step-up for writes | **`require_fresh_otp`** (mirrors flyer's sensitive mutations) — a transition is a state write. |
| Roles (owner/admin/staff) | N/A — one role exists (owner). No differentiation. If multi-staff is ever wanted, that's a separate cockpit-auth project (out of scope; flag). |
| Org/tenant boundary | **Single-tenant per VPS.** The route reads/writes only `settings.state_dir/commerce/orders.json` — no cross-tenant surface exists or is added. |
| `actor` recorded in audit | `"operator"` (the owner acting via cockpit). |

## 5. Audit (exact row shapes)

- **Success →** existing `CommerceOrderStatusChange` (`type=commerce_order_status_change`, `order_id`, `prev_status`, `next_status`, `actor="operator"`, `cause`, `ts` tz-aware) — emitted by `transition`. Cancel additionally emits existing `CommerceOrderCancelled` (`order_id`, `reason`, `actor="operator"`).
- **`cause`/comment:** the cockpit passes an operator-supplied `cause` (≤200 chars; required for cancel as the reason, optional-with-default for progress, e.g. `"cockpit: mark ready"`).
- **Actor identity:** `actor="operator"` in the order audit; **additionally** the cockpit's own `audit_log(...)` (cockpit-audit.log) records the JWT `sub` (owner phone), IP, UA — so the human identity + request context is captured in the cockpit audit chain (as flyer mutations do), while the commerce decisions.log stays schema-clean with `actor="operator"`.
- **Before/after:** `prev_status`/`next_status` (success) — full per-order trail also lives in the order's embedded `status_history`.
- **Timestamp source:** `datetime.now(timezone.utc)` (tz-aware; `_BaseEntry` enforces).
- **Failure audit (NEW, additive variant):** `commerce_order_action_refused` `_BaseEntry` — `type`, `order_id`, `attempted_to_status: Optional[CommerceOrderStatus]`, `from_status: Optional[CommerceOrderStatus]`, `reason: Literal["illegal_transition","stale_expected_status","order_not_found","not_allowed_in_slice_c"]`, `actor="operator"`, `cause`. Registered in the `LogEntry` union; covers refused cockpit actions so denials are auditable.

## 6. State / write safety (the core risk)

**Problem (verified):** `order_state.transition` does `load_order_store → mutate → write_order_store` with **no lock** — concurrent writers (double-click, owner + future cron/webhook) can lost-update (last-writer-wins, dropping a status_history entry). **Same gap in `create()`** (`order_state.py:128-131` load → `189-190` mutate/write, also unlocked).

**Slice C remedy (smallest safe):**
1. **One shared FileLock across ALL `orders.json` writers (decided).** A lock that covers only `transition()` does **not** prevent lost updates — a concurrent `create()` (customer checkout) and `transition()` (operator action) would still clobber each other because `create()` does the same unguarded read-modify-write. The fix wraps the `load→…→write` section in **both `create()` and `transition()`** with the **same** lock keyed on the orders-store path (the deployed `flock(path)` → `<path>.lock` sibling convention, so every writer serializes on one lock file regardless of entry point). `cancel()` (which wraps `transition`) is covered automatically.
   - **Shim parity (drift fix).** Commerce primitives write through `src/platform/commerce/_io_shim.py`, which on Windows (`os.name=="nt"`) deliberately falls back to a lockless atomic write because `fcntl` is Unix-only. So the lock MUST be obtained through the shim, **not** by importing `safe_io.FileLock` directly (that import would break Windows dev/test). Add `_io_shim.file_lock(path)`: returns the real `safe_io.flock(path)` on Linux (production VPS) and a **no-op context manager** on Windows dev/test — mirroring the shim's existing real-on-Linux / fallback-on-Windows pattern. Dev/test is single-process, so the no-op is safe there; production gets the real advisory lock. Both `create()` and `transition()` use `with file_lock(state_path):` around their RMW.
   - It lightly touches the primitive (`create` + `transition`) + the shim + their tests (acceptable, and re-reviewed at build).
2. **Optimistic-concurrency guard `expected_from_status`.** The cockpit sends the status it rendered; the handler rejects (HTTP 409 + `commerce_order_action_refused: stale_expected_status`) if `order.status != expected_from_status`. Prevents acting on a stale view; **no silent overwrite.**
3. **Malformed state:** reuse Slice B's graceful read — if `orders.json` is unreadable/malformed, the mutation is refused (HTTP 409/`degraded`), never a partial write.
4. **Atomicity:** `write_order_store` already does `atomic_write_json` (temp+rename); the FileLock closes the read-modify-write window around it.
5. **Version field:** NOT added in Slice C — `updated_at` + `expected_from_status` give sufficient optimistic concurrency for a single-owner cockpit; a monotonic `version` int is a later option if multi-writer concurrency grows (flag, not now).

## 7. Customer-visible effects

**None in Slice C.** No WhatsApp/customer message is sent on any transition. (A future "notify customer when ready/out-for-delivery" is explicitly **DEFERRED** — it needs the customer-messaging path + opt-in + copy review + send-safety; out of scope and gated.)

## 8. Payment / POS boundaries

- **Manual mark-paid (`→paid`): DEFERRED.** Marking an order paid is a money claim; the source of truth is provider-confirmed payment (Stripe webhook, `actor="webhook"`, Slice E). Slice C does not expose `→paid`. (If an operator ever needs a manual override, that is a separate, explicitly money-reviewed action with its own audit + warning — not Slice C.)
- **Refund (`→refunded`): DEFERRED.** No auto-refunds/chargebacks; refunds are provider/operator-gated (Slice E+), never a cockpit button in Slice C.
- **`pos_sync_status`: read-only in Slice C.** No manual edit; it is set only by the future POS adapter (Slice F). The Cockpit displays it (Slice B).
- **Provider confirmation stays separate:** payment state changes enter via the webhook path, not cockpit staff actions.

## 9. UI plan (Cockpit, Slice C)

- **Where:** the Slice-B order **detail drawer** gains an "Actions" block.
- **Actions:** context-sensitive buttons for the *allowed-from-current-status* transitions only, computed from the Slice-C allowlist + current status. Concretely: a `preparing` order shows **only "Mark ready"** (no Cancel — `preparing` is post-`paid`, and `preparing→cancelled` is excluded from Slice C); "Cancel" appears **only on pre-payment statuses** (`pending_payment` / `awaiting_approval`). This matches the §3 transition matrix exactly — the UI never offers a transition the allowlist forbids.
- **Disabled states:** terminal orders → no actions (show "Order is final"); illegal/deferred transitions → not rendered; degraded/missing state → actions hidden + banner.
- **Destructive confirmation:** `Cancel` requires a confirm dialog + a reason (free-text → `cause`/`reason`). Progress actions may use a lightweight confirm.
- **Step-up:** a transition triggers the existing fresh-OTP flow if the OTP is stale (reuse flyer's `require_fresh_otp` UX).
- **Optimistic concurrency UX:** on `409 stale_expected_status`, show "Order changed — refreshing" and re-fetch (no silent clobber).
- **Empty/error/degraded:** unchanged from Slice B (useful empty state; degraded banner; actions suppressed when degraded).

## 10. Tests planned

- **Backend transition route:** each allowed transition succeeds + emits `commerce_order_status_change`; each forbidden/deferred transition → 409 + `commerce_order_action_refused`; idempotent same-status → 200 no-op no-audit; terminal → 409; cancel pre-payment → success + `commerce_order_cancelled`; `→paid`/`→refunded` not exposed (route rejects).
- **Auth/tenant:** unauthenticated → 401; authenticated-but-stale-OTP → fresh-OTP required; reads/writes only this VPS's state.
- **Concurrency/idempotency:** two concurrent transitions under `FileLock` → exactly one applies, the other sees the post-lock state (no lost status_history); `expected_from_status` mismatch → 409 (stale), no write; the read-only **no-write-on-refusal** invariant holds.
- **Audit assertions:** exact `commerce_order_status_change` / `commerce_order_cancelled` / `commerce_order_action_refused` shapes; cockpit-audit.log records owner `sub`+IP+UA.
- **Frontend:** (no unit harness in repo — verified via backend contract + cockpit-ci typecheck/build) enabled/disabled action computation documented; manual smoke.

## 11. Open questions (for review / operator)

- **O1 — lock placement: DECIDED → shared lock across ALL `orders.json` writers.** One `FileLock` (keyed on the orders-store path via the `flock(path)` → `<path>.lock` convention) wraps the read-modify-write in **both `create()` and `transition()`** (cancel inherits via transition) — locking only `transition` would still allow a concurrent `create` to lost-update. Obtained through a new `_io_shim.file_lock(path)` (real `safe_io.flock` on Linux, no-op CM on Windows dev/test) so the shim's cross-platform contract holds. Touches `create` + `transition` + the shim + their tests; re-reviewed at build. (See §6.)
- **O2 — cancel scope: DECIDED → pre-payment only.** `preparing→cancelled` is excluded from Slice C (post-payment cancel without a refund path = money/state mismatch); all post-`paid` reversal routes through the deferred refund path.
- **O3 — `cause` requiredness: DECIDED.** `cause` is REQUIRED for `cancel` (the operator's reason); OPTIONAL for progress transitions, defaulting to `"cockpit: <action>"`.

## 12. Operator gates

- **Ships dormant-safe (owner-only, money-neutral, customer-silent):** fulfillment-progress transitions + pre-payment cancel. Acts only on orders that exist (none until Slice D/activation) → testable via seeded test orders; harmless in production while Commerce is dormant.
- **Requires payment activation + explicit money review:** manual mark-paid, refunds.
- **Requires POS activation:** `pos_sync_status` writes (Slice F).
- **Requires customer-messaging + opt-in + send-safety review:** any customer notification on transition.
- **Requires live test data / operator-owned numbers:** end-to-end validation against real WhatsApp-originated orders (Slice D dependency).

## 13. Review gate

Codex review (Hermes/drift · product/scope · runtime-state/operator-gate · money/payment/compliance · **write-safety/concurrency**). **No implementation until Codex-CLEAN and the operator approves the build + the smallest-safe scope (§0).**
