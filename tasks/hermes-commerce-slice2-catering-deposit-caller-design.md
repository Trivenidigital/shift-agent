# Hermes Commerce slice 2 — Catering Deposit Caller (design)

**Drift-check tag:** `extends-Hermes`

**Status:** Draft for 2-vector parallel design review (2026-05-29).

**Upstream gates satisfied:**
- PR #321 merged + deployed (slice-1 primitives live on main-vps as of `deploy-20260529-124014-5ef74ebc`)
- PR #322 merged + deployed (deploy-script installs `commerce/` package)
- This is the **first slice-2 caller**; per PRD v2 §6 + reconciliation, catering deposits for general cuisine are explicitly allowed in slice 2+. No raw-meat / restricted-category exposure in this caller.

**Hermes-check receipt:** `tasks/.hermes-check-receipts/hermes-commerce-slice2-catering-deposit-caller-design.json`

---

## Drift-rule self-checks (CLAUDE.md §"DRIFT RULES" compliance)

- ✅ Read `src/agents/catering/scripts/apply-catering-owner-decision` (deployed approval flow, 884 lines; identified line 864 `atomic_write_json(LEADS_PATH, store)` as the post-`SENT_TO_CUSTOMER` insertion point for the deposit hook)
- ✅ Read `src/agents/catering/skills/handle_catering_owner_approval/SKILL.md` (verified the SKILL's terminal-tool invocation chain stays unchanged; deposit hook fires inside the existing apply-script, not via a new SKILL step)
- ✅ Read `src/platform/schemas.py` (`CateringConfig` at l.627 has `deposit_threshold_guests=50` + `deposit_pct=0.25` as deployed config knobs; `CateringLead` at l.1944 has `quote_total_usd: Optional[int]` which is the deposit-amount basis; `CateringLeadStatusChange` at l.4017 accepts actor in `{system, owner, customer, operator}` — no `caller` or `auto` literal yet)
- ✅ Read `src/platform/commerce/payment_link.py` (slice-1 primitive contract: `mint(order_id, ...)`, `mark_attempted`, `mark_sent`, `void`, `assert_payment_url_renderable`, `register_reference` — all callable in-process from a Python script)
- ✅ Read `src/platform/commerce/order_state.py` (slice-1 primitive contract: `create(cart, ..., cart_state_path=...)` auto-marks cart as `checked_out` when path provided — clean for the synthetic-cart pattern below)
- ✅ Read `src/platform/commerce/cart.py` (slice-1 primitive contract: `add_item(sender_phone=..., sender_lid=..., chat_id=..., sku=..., display_name=..., quantity=..., unit_price_cents=...)` accepts phone-or-LID per the model_validator on `CommerceCart`)
- ✅ Read `tasks/hermes-commerce-prd-v2.md` (caller invariants binding on this PR per §7+§10: idempotency-key=order_id, immutable payment_reference, empty-URL guard, caller-agent cross-reference field for Cash & AR join)
- ✅ Read `tasks/hermes-commerce-portfolio-reconciliation.md` (Money-moving invariants section: 4 invariants applied — cross-order ref immutability, empty-URL guard, idempotency on order_id, caller-agent cross-reference field)

Drift-check tag rationale: this PR **extends Hermes substrate** by adding a slice-1-primitive caller, a new script, two LogEntry variants, and a small schema extension. It does NOT violate any Part-1 deployed pattern (storage, audit, approval-code, sender-identity, dispatcher, schemas, tests, image-input, per-VPS isolation). No `drifts-from-Hermes` justification required.

---

## 1. Hermes-first capability checklist (per-step)

| # | Step | Tag |
|---|---|---|
| 1 | WhatsApp inbound + dispatcher routing to `handle_catering_owner_approval` | `[Hermes]` — skill dispatch by sender_role + content |
| 2 | SKILL dispatch to existing handler | `[Hermes]` — existing routing |
| 3 | Existing `apply-catering-owner-decision` runs through approve flow | `[Hermes]` — deployed deterministic-script pattern |
| 4 | **Threshold check + subprocess invocation of `catering-mint-deposit` AFTER successful `SENT_TO_CUSTOMER`** | `[net-new]` — small edit to existing script |
| 5 | **`catering-mint-deposit` script: synthetic cart + primitive calls + lead mutation** | `[net-new]` — new deterministic Python script |
| 6 | `commerce_order_state.create()` — slice-1 primitive | `[Hermes]` — slice-1 substrate |
| 7 | `commerce_payment_link.mint()` — slice-1 primitive | `[Hermes]` — slice-1 substrate |
| 8 | `commerce_payment_link.mark_attempted()` — slice-1 primitive | `[Hermes]` — slice-1 substrate |
| 9 | `assert_payment_url_renderable()` + WhatsApp text reply on empty | `[Hermes]` — slice-1 guard + multi-channel response |
| 10 | WhatsApp text+link reply to customer via `_bridge_post` | `[Hermes]` — multi-channel response |
| 11 | **New `catering_deposit_link_sent` + `catering_deposit_link_failed` LogEntry variants** | `[net-new]` — schema additions |
| 12 | **`CateringLead` schema fields: `deposit_*` set** | `[net-new]` — schema extension |
| 13 | Customer pays on external provider page (out of scope here) | external |
| 14 | (Slice 3) webhook receiver → reconcile lead | reserved |

**Net-new: 4 of 14 (29%).** No red flag.

---

## 2. Design overview

### Insertion point in the deployed approval flow

**Reviewer A BLOCKER-1 fix:** the subprocess MUST fire **OUTSIDE** the parent's `FileLock(LEADS_LOCK)` block to avoid self-deadlock (parent holds the flock; subprocess re-acquires same flock → blocks indefinitely). `apply-catering-owner-decision` lines 749-877 hold LEADS_LOCK; the deposit hook fires **AFTER line 875 dedent** (after the lock releases) and **BEFORE the success `print(json.dumps(...))` at line 877**:

```python
# (new code, after the `with FileLock(LEADS_LOCK):` block dedents at line 875,
# BEFORE the JSON output line 877. Lock has been released — safe to invoke a
# subprocess that will re-acquire LEADS_LOCK on its own.)
if _should_mint_deposit(cfg, store.leads[matched_idx]):
    _invoke_catering_mint_deposit(
        lead_id=lead_id_for_output,
        log_path=LOG_PATH,
        timeout_sec=30,  # Reviewer A HIGH-1: bridge POST can take seconds; cap total time
    )  # subprocess; failure is best-effort + audited, never rolls back the quote send
```

`_invoke_catering_mint_deposit` is a thin helper that runs the subprocess with `timeout=30`, captures stdout/stderr, branches on exit code:

```python
def _invoke_catering_mint_deposit(lead_id: str, log_path: Path, timeout_sec: int = 30) -> None:
    """Best-effort post-send hook. NEVER raises. Subprocess + exit-code routing."""
    try:
        result = subprocess.run(
            ["/usr/local/bin/catering-mint-deposit", "--lead-id", lead_id],
            capture_output=True, text=True, timeout=timeout_sec,
            check=False,  # we route on exit code ourselves
        )
        if result.returncode == EXIT_OK:
            return  # success or in-script no-op (threshold not met / already minted)
        # Non-zero: the script already wrote its own catering_deposit_link_failed
        # audit row best-effort. We log to stderr for journald visibility.
        sys.stderr.write(
            f"catering-mint-deposit lead_id={lead_id} exit={result.returncode} "
            f"stderr={result.stderr[:500]}\n"
        )
    except subprocess.TimeoutExpired:
        # Best-effort audit; if this fails we still don't raise.
        sys.stderr.write(f"catering-mint-deposit lead_id={lead_id} TIMED OUT after {timeout_sec}s\n")
        _emit_deposit_link_failed_best_effort(
            log_path, lead_id, reason="subprocess_timeout",
            detail=f"timeout_sec={timeout_sec}",
        )
    except FileNotFoundError:
        # Binary missing — operator hasn't deployed catering-mint-deposit yet.
        # Apply-script can still complete successfully; deposit just doesn't happen.
        sys.stderr.write(f"catering-mint-deposit binary missing; skipping deposit for {lead_id}\n")
```

**Why post-send, not pre-send:** the customer has already received the catering quote at this point. The deposit-mint is a **follow-up**, not part of the approval transaction. A mint failure does NOT mean the approval failed — the quote went; the deposit link is a separate message. This separation:

- Matches the natural conversation flow ("here's your quote" → then later "to confirm, please pay the deposit")
- Prevents a deposit-link bug from blocking quote delivery (BLAST RADIUS: a slice-2 caller bug can never break the existing slice-1 catering happy path)
- Lets the deposit-mint run idempotently — if the script crashes mid-mint, a retry checks `lead.deposit_payment_intent_id` (re-loaded under LEADS_LOCK inside the mint script) and skips if already minted

### Threshold logic (deterministic)

```python
def _should_mint_deposit(cfg, lead) -> bool:
    if cfg.catering.deposit_pct <= 0:
        return False
    if lead.extracted.headcount is None:
        return False
    if lead.extracted.headcount < cfg.catering.deposit_threshold_guests:
        return False
    if lead.quote_total_usd is None or lead.quote_total_usd <= 0:
        return False
    if lead.deposit_payment_intent_id:  # already minted — idempotent skip
        return False
    return True
```

`cfg.catering.deposit_threshold_guests=50` and `cfg.catering.deposit_pct=0.25` are existing config defaults — no operator action required to enable. Customers who don't want deposits set `deposit_pct=0` (which the function checks first).

### `catering-mint-deposit` script (~250 LOC)

New script at `src/agents/catering/scripts/catering-mint-deposit`. Invoked as:

```
catering-mint-deposit --lead-id L0007
```

Pseudocode:

```python
def main():
    # 1. Load config + lead under LEADS_LOCK
    cfg = load_yaml_model(CONFIG_PATH, Config)
    with FileLock(LEADS_LOCK):
        store = load_model(LEADS_PATH, CateringLeadStore)
        lead = next((l for l in store.leads if l.lead_id == args.lead_id), None)
        if lead is None:
            return EXIT_NOT_FOUND
        # Idempotency: skip if already minted (re-invocation safe)
        if lead.deposit_payment_intent_id:
            print(json.dumps({"lead_id": lead.lead_id, "noop": "already_minted"}))
            return EXIT_OK
        # Threshold check (defense-in-depth — caller should have checked too)
        if not _should_mint_deposit(cfg, lead):
            return EXIT_OK  # not an error; just doesn't apply

        deposit_amount_cents = round(lead.quote_total_usd * 100 * cfg.catering.deposit_pct)
        # Reviewer B MEDIUM-2: minimum-deposit floor (defaults to $5.00).
        # A $5 deposit on a $20 catering quote is below most provider minimum-
        # charge thresholds and produces an unactionable payment link.
        if deposit_amount_cents <= 0:
            _emit_deposit_link_failed(lead, reason="zero_amount")
            return EXIT_INVALID_INPUT
        if deposit_amount_cents < cfg.commerce.minimum_deposit_cents:
            _emit_deposit_link_failed(
                lead, reason="below_minimum",
                detail=f"{deposit_amount_cents}<min={cfg.commerce.minimum_deposit_cents}",
            )
            return EXIT_INVALID_INPUT

        # 2. Build synthetic CommerceCart with single CATERING-DEPOSIT line item.
        # Reviewer B HIGH-1 + A MEDIUM-1: use a SYNTHETIC per-lead chat_id so the
        # cart is isolated from any concurrent commerce flow that uses the customer's
        # real WhatsApp chat_id. Without this, a future slice-3 commerce caller
        # (e.g., grocery order) could share a cart with the catering deposit and
        # an order.create would mix line items. Synthetic chat_id keys the cart
        # to (sender_phone, "catering_deposit_L0007@s.whatsapp.net") which never
        # collides with any real WhatsApp chat.
        from commerce import cart as commerce_cart
        from commerce import order_state as commerce_order
        from commerce import payment_link as commerce_payment_link
        synthetic_chat_id = f"catering_deposit_{lead.lead_id}@s.whatsapp.net"
        cart_result = commerce_cart.add_item(
            state_path=COMMERCE_CARTS_PATH,
            decisions_log_path=LOG_PATH,
            sender_phone=str(lead.customer_phone),
            sender_lid=None,
            chat_id=synthetic_chat_id,
            sku=f"CATERING-DEPOSIT-{lead.lead_id}",
            display_name=f"Catering deposit for {lead.customer_name or 'event'}",
            quantity=1,
            unit="each",
            unit_price_cents=deposit_amount_cents,
            currency="USD",
        )
        if not cart_result.ok:
            _emit_deposit_link_failed(lead, reason="cart_build_failed", detail=cart_result.detail)
            return EXIT_SCHEMA_VIOLATION

        # 3. Create order from cart (auto-marks cart checked_out via slice-1 cart_state_path)
        order_result = commerce_order.create(
            state_path=COMMERCE_ORDERS_PATH,
            decisions_log_path=LOG_PATH,
            cart=cart_result.cart,
            cart_state_path=COMMERCE_CARTS_PATH,
        )
        if not order_result.ok:
            _emit_deposit_link_failed(lead, reason="order_create_failed", detail=order_result.detail)
            return EXIT_SCHEMA_VIOLATION

        # 4. Mint payment intent
        intent_result = commerce_payment_link.mint(
            intent_state_path=COMMERCE_INTENTS_PATH,
            decisions_log_path=LOG_PATH,
            order_id=order_result.order.order_id,
            originating_message_id=f"catering_deposit_{lead.lead_id}",
            amount_cents=deposit_amount_cents,
            currency="USD",
            chat_id=f"{lead.customer_phone.lstrip('+')}@s.whatsapp.net",
            checkout_url_template=cfg.commerce.payment_checkout_url_template,
        )
        if not intent_result.ok:
            _emit_deposit_link_failed(lead, reason="intent_mint_failed", detail=intent_result.detail)
            return EXIT_SCHEMA_VIOLATION

        # 5. Mark attempted (PR-#321 reviewer A MEDIUM-3 attempted/sent/failed triple)
        commerce_payment_link.mark_attempted(
            intent_state_path=COMMERCE_INTENTS_PATH,
            decisions_log_path=LOG_PATH,
            intent_id=intent_result.intent.intent_id,
        )

        # 6. Compose customer-visible reply (deposit/payment outcome only;
        # NO internal Commerce terminology; PRD v2 §10 + operator confirmation)
        target_jid = f"{lead.customer_phone.lstrip('+')}@s.whatsapp.net"
        url = intent_result.intent.checkout_url
        try:
            commerce_payment_link.assert_payment_url_renderable(url)
            reply = _render_customer_reply(lead, deposit_amount_cents, url)
            url_status = "configured"
        except CommerceCheckoutUrlUnrenderable:
            # Operator hasn't configured payment_checkout_url_template; emit
            # the "not configured yet" copy and the failed-audit row.
            reply = _render_unconfigured_reply(lead)
            url_status = "unconfigured"

        # 7. Send via the existing bridge chokepoint (same _bridge_post used by
        # apply-catering-owner-decision; ⚕ *Catering Agent* prefix preserved).
        # Target the customer's REAL WhatsApp JID (not the synthetic cart chat_id).
        ok, mid_or_err = _bridge_post(target_jid, _PREFIX + reply)
        if not ok:
            # Reviewer A BLOCKER-2: emit commerce_payment_link_failed BEFORE
            # voiding the intent so the attempted/sent/failed triple invariant
            # holds. Operator looking at the intent's audit trail sees
            # attempted → failed → voided (clean), not attempted → voided (gap).
            commerce_payment_link._emit_payment_link_failed(  # see commit-2 impl
                decisions_log_path=LOG_PATH,
                intent_id=intent_result.intent.intent_id,
                order_id=order_result.order.order_id,
                reason=mid_or_err[:200],
            )
            commerce_payment_link.void(  # hygiene: don't leave an orphan minted intent
                intent_state_path=COMMERCE_INTENTS_PATH,
                decisions_log_path=LOG_PATH,
                intent_id=intent_result.intent.intent_id,
                reason="customer_send_failed",
                actor="caller",  # Reviewer A HIGH-3 + B HIGH-3: actor IS `str max_length=40` per schemas.py CommercePaymentIntentVoided; "caller" is the cleanest semantic fit (the deposit-mint IS the caller of the slice-1 primitive)
            )
            _emit_deposit_link_failed(lead, reason="bridge_send_failed", detail=mid_or_err)
            # Reviewer B HIGH-2: bridge_send_failed on a money-adjacent path
            # is too important to be journald-only. Operator gets Pushover P1
            # (not P2 — the quote already delivered, business risk is recoverable
            # by re-invoking catering-mint-deposit manually).
            _notify_owner_best_effort(
                priority=1,
                title=f"Catering deposit bridge fail (lead {lead.lead_id})",
                body=(
                    f"Deposit link mint succeeded for lead {lead.lead_id} "
                    f"(${deposit_amount_cents/100:.2f}) but WhatsApp bridge POST "
                    f"failed: {mid_or_err[:200]}. Intent voided. "
                    f"Re-invoke: catering-mint-deposit --lead-id {lead.lead_id}"
                ),
            )
            return EXIT_DEPENDENCY_DOWN

        # 8. mark_sent (only on successful WhatsApp delivery)
        commerce_payment_link.mark_sent(
            intent_state_path=COMMERCE_INTENTS_PATH,
            decisions_log_path=LOG_PATH,
            intent_id=intent_result.intent.intent_id,
        )

        # 9. Update lead with deposit cross-reference fields
        now = customer_now(cfg.customer.timezone)
        new_lead = lead.model_copy(update={
            "deposit_required": True,
            "deposit_amount_cents": deposit_amount_cents,
            "deposit_commerce_order_id": order_result.order.order_id,
            "deposit_payment_intent_id": intent_result.intent.intent_id,
            "deposit_status": "awaiting_payment" if url_status == "configured" else "unconfigured",
            "deposit_minted_at": now,
            "updated_at": now,
        })
        store.leads[matched_idx] = new_lead
        atomic_write_json(LEADS_PATH, store)

        # 10. Emit catering_deposit_link_sent with cross-ref fields (reconciliation
        # invariant #4: callers MUST carry commerce_order_id + commerce_payment_intent_id
        # so Cash & AR can join the streams)
        _emit_deposit_link_sent(
            lead=new_lead,
            commerce_order_id=order_result.order.order_id,
            commerce_payment_intent_id=intent_result.intent.intent_id,
            amount_cents=deposit_amount_cents,
            url_status=url_status,
            outbound_message_id=mid_or_err,
        )
    return EXIT_OK
```

### Customer-visible copy (operator confirmation: PRD-aligned, no Commerce terminology)

**Reviewer B BLOCKER-1 fix + LOW-1 fix:** the customer has never seen the internal `lead_id` (e.g. `L0007`) anywhere in the prior conversation, so referencing it in a money-asking message would read as a meaningless string. Anchor the message on **details the customer will recognize from their own quote**: event date + headcount when present, customer_name as fallback. Amount-first; percentage as parenthetical:

**Configured-template happy path** (when `event_date` AND `headcount` are both known):
```
⚕ *Catering Agent*
────────────
To confirm your 100-guest event on 2026-06-15, please pay $150.00
(25% of total): https://pay.example.com/?o=CO00042
```

**Configured-template fallback** (when event_date or headcount is missing, but customer_name is known):
```
⚕ *Catering Agent*
────────────
Thanks, Lakshmi! To confirm your booking, please pay $150.00
(25% of total): https://pay.example.com/?o=CO00042
```

**Configured-template last-resort** (neither anchor available):
```
⚕ *Catering Agent*
────────────
To confirm your catering booking, please pay $150.00
(25% of total): https://pay.example.com/?o=CO00042
```

**Unconfigured-template fail-closed path** (mirrors Flyer's "Payment link is not configured yet" style — byte-exact):
```
⚕ *Catering Agent*
────────────
Payment link is not configured yet. We'll send it when it's ready.
```

The prefix `⚕ *Catering Agent*\n────────────\n` matches the deployed `_bridge_post` bypass pattern at apply-catering-owner-decision:719 (the WhatsApp bridge filter at bridge.js:133 lets these through).

**Forbidden in copy** (operator confirmation + lessons):
- No raw URLs presented as bare text (always inside a sentence with context)
- No mention of "Commerce", "intent ID", "primitive", or any internal terminology
- No mention of provider name (Stripe/Razorpay/etc.) — slice 2 is placeholder template only
- No order ID, intent ID, or lead ID exposed (server-side identifiers; customer doesn't need them — Reviewer B BLOCKER-1)
- Deposit percentage included as a parenthetical so customer understands the fraction (amount-first per Reviewer B LOW-1)

### `CateringLead` schema additions (Commit 1)

```python
class CateringLead(BaseModel):
    # ... existing fields ...

    # Slice 2 deposit caller — orthogonal to lead.status to avoid bloating
    # the existing state machine. Lead.status stays SENT_TO_CUSTOMER after
    # the quote+deposit are sent; deposit_status tracks the deposit lifecycle
    # independently. Slice 3 webhook receiver flips deposit_status to "paid"
    # and the catering follow-up agent (or operator) decides when to advance
    # lead.status further (CONFIRMED in a future slice).
    deposit_required: bool = False
    deposit_amount_cents: int = Field(default=0, ge=0, le=10_000_000_000)
    deposit_commerce_order_id: str = Field(default="", max_length=40)
    deposit_payment_intent_id: str = Field(default="", max_length=40)
    deposit_payment_reference: str = Field(default="", max_length=200)
    deposit_status: Literal[
        "none",            # default — no deposit required for this lead
        "unconfigured",    # threshold met but checkout_url_template empty
        "awaiting_payment",
        "paid",            # slice-3 webhook will set this
        "voided",          # operator cancelled deposit
        "refunded",        # slice-3+
    ] = "none"
    deposit_minted_at: Optional[datetime] = None
```

All defaults preserve legacy-lead decode (no migration script required). The `lead.status` enum is **NOT extended** — deposit is orthogonal.

### New `LogEntry` variants (Commit 1)

```python
class CateringDepositLinkSent(_BaseEntry):
    type: Literal["catering_deposit_link_sent"]
    lead_id: str = Field(min_length=1)
    # Reconciliation invariant #4: cross-ref to commerce primitives for Cash & AR join
    commerce_order_id: str = Field(pattern=r"^CO\d{5,}$")
    commerce_payment_intent_id: str = Field(pattern=r"^CPI\d{5,}$")
    amount_cents: int = Field(ge=1)
    url_status: Literal["configured", "unconfigured"]
    outbound_message_id: str = Field(min_length=1, max_length=200)


class CateringDepositLinkFailed(_BaseEntry):
    type: Literal["catering_deposit_link_failed"]
    lead_id: str = Field(min_length=1)
    reason: Literal[
        "zero_amount",
        "below_minimum",          # Reviewer B MEDIUM-2: minimum-deposit floor
        "cart_build_failed",
        "order_create_failed",
        "intent_mint_failed",
        "bridge_send_failed",
        "subprocess_timeout",     # Reviewer A HIGH-1: parent-side subprocess timeout
    ]
    detail: str = Field(default="", max_length=500)
    # commerce_* fields optional because failure may occur before any commerce
    # primitive returned an id
    commerce_order_id: str = Field(default="", max_length=40)
    commerce_payment_intent_id: str = Field(default="", max_length=40)
```

Both added to the `LogEntry` discriminated union.

---

## 3. State-file & locking story

- `state/catering-leads.json` — existing. Lock: `FileLock(LEADS_LOCK)` per deployed pattern.
- `state/commerce/carts.json` — slice-1 primitive owns; `catering-mint-deposit` calls `commerce_cart.add_item` which uses `_io_shim.atomic_write_json`. **No lock contention** because slice-1 primitives are single-writer per-VPS and the deposit caller is the only commerce caller in this slice.
- `state/commerce/orders.json` — same.
- `state/commerce/payment_intents.json` — same.
- `state/commerce/payment_references.json` — empty in slice 2; populated by slice-3 webhook on payment confirmation.

**Lock-acquisition order:** `catering-mint-deposit` acquires `LEADS_LOCK` for the entire mint sequence (steps 1–10 in the pseudocode), then releases. Commerce primitive calls happen inside that lock. There's no nested lock on commerce state files because slice-1 primitives don't currently use `fcntl.flock`. If/when a slice-3 webhook daemon contends, it acquires its own commerce-side lock (per PRD v2 §5 locking story).

**The deposit-mint runs INSIDE the lock** because we want the deposit fields persisted atomically with the commerce primitive calls. If the bridge send fails after order/intent mint, we void the intent (within the same lock) and emit `catering_deposit_link_failed` — no orphan state.

---

## 4. Failure-mode matrix

| Scenario | Behaviour | Customer copy | Audit | Operator alert |
|---|---|---|---|---|
| `quote_total_usd` is None or 0 | skip deposit (threshold check returns False) | none | none | none |
| `headcount < deposit_threshold_guests` | skip deposit | none | none | none |
| `deposit_pct == 0` (kill switch) | skip deposit | none | none | none |
| Deposit amount rounds to 0 cents | refuse | none (operator-visible) | `catering_deposit_link_failed` reason=`zero_amount` | journald only |
| Deposit amount below `minimum_deposit_cents` | refuse | none | `catering_deposit_link_failed` reason=`below_minimum` | journald only |
| Cart build fails | refuse | none (operator-visible bug) | `catering_deposit_link_failed` reason=`cart_build_failed` | journald only |
| Order create fails | refuse | none | `catering_deposit_link_failed` reason=`order_create_failed` | journald only |
| Intent mint fails | refuse | none | `catering_deposit_link_failed` reason=`intent_mint_failed` | journald only |
| Empty `payment_checkout_url_template` | send "Payment link is not configured yet" | "Payment link is not configured yet. We'll send it when it's ready." | `catering_deposit_link_sent` with `url_status="unconfigured"` | journald (Cockpit warning landing in slice-2.5+) |
| Bridge send fails (network/transient) | **emit `commerce_payment_link_failed` → void intent → emit `catering_deposit_link_failed` → Pushover P1** | none (customer never saw it) | `commerce_payment_link_failed` + `commerce_payment_intent_voided` + `catering_deposit_link_failed` reason=`bridge_send_failed` | **Pushover P1** — money-adjacent, operator must know |
| Subprocess timeout (parent-side) | best-effort audit + journald log; no roll-back of quote send | none | `catering_deposit_link_failed` reason=`subprocess_timeout` (best-effort emit by parent) | journald only — slice-2.5 watchdog could escalate |
| Re-invoke after success | idempotent no-op (skip via `lead.deposit_payment_intent_id` check) | none | none | none |
| Approval flow itself failed before reaching SENT_TO_CUSTOMER | deposit hook never invoked | (no deposit) | none | none |

**Key invariant:** a deposit failure **NEVER** rolls back the quote send. The customer has the quote; they just don't have a deposit link yet. Operator can re-invoke `catering-mint-deposit --lead-id L0007` manually, OR a future slice-2.5 watchdog can retry.

---

## 5. Customer-copy invariant (binding)

Per operator confirmation + 2026-05-15 lesson on no-internal-terminology + PRD v2 §10 + the slice-2 reconciliation invariant #2 (empty-URL guard):

1. **MUST NOT** mention "Commerce", "intent", "primitive", "Hermes", or any internal terminology
2. **MUST** include the dollar amount **first** (e.g., "$150.00"); deposit percentage as parenthetical (e.g., "(25% of total)")
3. **MUST NOT** include the commerce_order_id, commerce_payment_intent_id, **or `lead_id`** (Reviewer B BLOCKER-1: internal identifiers the customer hasn't seen elsewhere)
4. **MUST** anchor on customer-recognizable context — preferred: event date + headcount when both present; fallback: customer_name; last-resort: generic "your catering booking"
5. **MUST** use the existing `⚕ *Catering Agent*\n────────────\n` prefix so the WhatsApp bridge filter doesn't drop the message (bridge.js:133)
6. **MUST NOT** render a bare URL on empty `checkout_url_template`. The unconfigured-template fallback message is exact text: `"Payment link is not configured yet. We'll send it when it's ready."`

A test in `test_catering_deposit_copy_invariants.py` asserts the rendered copy against these constraints (regex for "Commerce" / "intent" / "primitive" / "Hermes" — must NOT match; substring for "$" — must match; literal `lead_id` value — must NOT appear).

---

## 6. Test plan

### Pure-function unit tests (`tests/test_catering_deposit_helpers.py`)

- `_should_mint_deposit` truth table: every combination of `(headcount, threshold, deposit_pct, quote_total_usd, deposit_payment_intent_id)`
- `_render_customer_reply` produces copy that satisfies §5 invariants
- `_render_unconfigured_reply` produces exact "Payment link is not configured yet" copy
- Threshold edge case: `headcount == deposit_threshold_guests` boundary (inclusive per `>=`)

### Subprocess-invoke tests (`tests/test_catering_mint_deposit_script.py`)

Pattern mirrors `tests/test_catering_v02_scripts.py` (subprocess + state-mutation + audit-row assertions). Cases:

- **Happy path** (configured template): lead with headcount=100, quote_total_usd=600, deposit_pct=0.25 → mint, send, lead deposit_status="awaiting_payment", commerce_order_created + commerce_payment_intent_minted + commerce_payment_link_attempted + commerce_payment_link_sent + catering_deposit_link_sent audit rows.
- **Unconfigured template**: same setup but `cfg.commerce.payment_checkout_url_template=""` → mint succeeds, send happens with "not configured yet" copy, lead deposit_status="unconfigured", catering_deposit_link_sent emitted with `url_status="unconfigured"`.
- **Below threshold**: headcount=10 (below 50) → no-op, no commerce primitives called.
- **deposit_pct == 0**: → no-op.
- **quote_total_usd is None**: → no-op.
- **Idempotent re-invocation**: run twice → second run is a no-op (already minted check).
- **Cart build fails** (simulate by passing an invalid currency override): `catering_deposit_link_failed` emitted, lead unchanged.
- **Bridge send fails** (mock `_bridge_post` to return `False`): intent voided, `catering_deposit_link_failed` emitted with reason=`bridge_send_failed`.

### Integration test (`tests/test_catering_apply_owner_decision_deposit_hook.py`)

- Invoke `apply-catering-owner-decision` end-to-end with a lead at AWAITING_OWNER_APPROVAL, headcount=100, quote_total_usd=600, mock-bridge success.
- Assert: lead reaches SENT_TO_CUSTOMER + deposit_status="awaiting_payment" + commerce primitives state populated + both `catering_quote_sent` AND `catering_deposit_link_sent` audit rows present.
- Variant: same flow with `cfg.catering.deposit_pct=0` → deposit hook silently skipped, no commerce state created.

### LogEntry variant tests (extend `tests/test_commerce_logentry_variants.py`)

- `CateringDepositLinkSent` round-trip via `LogEntry` adapter
- `CateringDepositLinkFailed` round-trip
- Both reject extras (extra="forbid")
- `commerce_order_id` / `commerce_payment_intent_id` pattern validation

### Customer-copy lint test (`tests/test_catering_deposit_copy_invariants.py`)

- For 5 distinct lead fixtures (varying customer_name, headcount, total): rendered configured-template copy MUST NOT contain "Commerce" / "intent" / "primitive" / "Hermes" / "stripe" / "razorpay"; MUST contain "deposit" / "%" / "$" / lead_id.
- Unconfigured-template copy is **byte-exact** to the operator-approved string.

---

## 7. Build sequence (3 commits, single PR `feat/commerce-slice2-catering-deposit-caller`)

### Commit 1 — schema + audit variant additions (~95 LOC + ~80 LOC tests)

- Extend `CateringLead` with the 7 deposit fields (all default-friendly for legacy decode)
- Add `CateringDepositLinkSent` + `CateringDepositLinkFailed` to schemas.py + LogEntry union
- Tests: `test_catering_lead_deposit_field_defaults`, `test_commerce_logentry_variants` extension
- **No caller wired yet.** Schemas land library-only.

### Commit 2 — `catering-mint-deposit` script + tests (~270 LOC + ~250 LOC tests)

- New script under `src/agents/catering/scripts/catering-mint-deposit`
- Helper functions in a new `src/agents/catering/deposit.py` module (testable in-process)
- Subprocess tests + pure-function tests
- **Still no caller wired.** Operator can invoke `catering-mint-deposit --lead-id L0007` manually for testing.

### Commit 3 — wire `apply-catering-owner-decision` post-send hook (~30 LOC + ~80 LOC tests)

- Threshold check + subprocess invocation INSIDE the existing success branch (post-`SENT_TO_CUSTOMER` write)
- Integration test (`test_catering_apply_owner_decision_deposit_hook.py`)
- **This is the customer-affecting commit.** Reviewer focus belongs here.

---

## 8. What this PR does NOT touch

Carried from PRD v2 §12 + slice-1 follow-up backlog:

- ❌ Real Stripe/Razorpay/UPI provider integration
- ❌ Webhook receiver daemon
- ❌ `commerce_payment_confirmed` flow (slice 3)
- ❌ Refund/void/chargeback automatic transitions
- ❌ Cockpit Commerce view
- ❌ `commerce_catalog` primitive
- ❌ Dispatcher matrix amendment (deposit is auto-fired by the existing approval flow; no new inbound surface)
- ❌ Other Commerce callers (flyer migration of `guest_order.py`, etc.)
- ❌ Lead-status enum extension (deposit_status is orthogonal)
- ❌ Catering Cockpit view of deposit-pending leads (operator dashboard work, deferred)
- ❌ Per-lead deposit override via WhatsApp (operator command — deferred)

---

## 9. Risks + open questions

### Risks

1. **Customer expectation gap**: customer who never had a deposit before suddenly receives a deposit-link message. **Mitigation**: this is the first paying customer's flow and operator controls onboarding; the deposit threshold (50 guests) is high enough that small events are unaffected.
2. **Empty-template default**: `payment_checkout_url_template` defaults to `""` in slice 1's `CommerceConfig`. If operator forgets to configure, every qualifying lead gets the "not configured yet" message. **Mitigation**: lead audit row carries `url_status="unconfigured"` so the operator dashboard can surface a "you need to configure the template" warning. Doc in the runbook.
3. **Intent voided on bridge fail**: if the bridge send fails after intent mint, we void the intent. A retry then mints a fresh intent (different `order_id`). This is intentional — we don't want to re-send the same payment link in a way that would let two intents go live. **Cost**: minor — one extra void audit row per retry.

### Open questions for reviewers

1. **Should the operator be able to override the per-lead deposit fraction?** Currently we use `cfg.catering.deposit_pct` globally. A future enhancement could allow per-lead override via WhatsApp ("set deposit 30%"). Out of scope for this PR.
2. **Should `deposit_required: bool` exist on the schema, or is it derivable from `deposit_payment_intent_id != ""`?** Current design: explicit bool for clarity in cockpit + reconciliation tools. Alternative: derive-only.
3. **`bridge_send_failed` recovery**: should slice 2 include a retry watchdog (similar to `catering-lead-reconcile`) for failed deposit-mints? Current design says no — operator manually invokes the script. Reviewer judgment on whether to add minimal watchdog in this PR.
4. **`actor` value for the void path**: I used `actor="cron"` because none of the existing actor literals (`customer/caller/operator/cron/webhook`) cleanly fit "auto-fired by post-send hook." Reviewer judgment on whether to add `actor="auto"` to the slice-1 LogEntry union or stick with "cron".

---

## 9.5 Design review applied (2026-05-29)

Two parallel reviewers ran with non-overlapping lenses:

- **Reviewer A (Hermes pattern + slice-1 primitive contract):** `BLOCK` → resolved. 2 BLOCKERs (subprocess-inside-parent-flock deadlock, missing `commerce_payment_link_failed` audit row); 3 HIGHs (subprocess timeout missing, idempotency hole on concurrent invocations, `actor="cron"` wrong); 3 MEDIUMs (synthetic cart isolation, missing `CateringLeadStatusChange` for deposit, no §12a watchdog story); 3 LOWs.
- **Reviewer B (catering domain + money + customer copy):** `APPROVE_WITH_RECOMMENDATIONS`. 1 BLOCKER (customer copy exposes internal `lead_id`); 3 HIGHs (cross-process retry-idempotency, silent bridge_send_failed, `actor="cron"` wrong); 3 MEDIUMs (threshold inclusivity doc, minimum-deposit floor, catering-followup integration); 3 LOWs.

**Applied in this revision:**

- A-BLOCKER-1 — subprocess invocation moved AFTER `with FileLock(LEADS_LOCK):` dedent (line 875), BEFORE the success `print` (line 877). No parent/child flock deadlock.
- A-BLOCKER-2 — `commerce_payment_link_failed` audit row emitted BEFORE the `void()` call so the attempted/sent/failed triple invariant holds.
- B-BLOCKER-1 — customer copy reworked: anchor on event date + headcount (preferred) / customer_name (fallback) / generic (last resort). `lead_id` removed entirely from customer-visible text. Copy-invariant test updated to assert `lead_id` does NOT appear.
- A-HIGH-1 — subprocess `timeout=30`, exit-code routing, `FileNotFoundError` handling, `TimeoutExpired` handling, all best-effort.
- A-HIGH-2 + B-HIGH-1 — synthetic chat_id `f"catering_deposit_{lead.lead_id}@s.whatsapp.net"` isolates the cart per-lead so concurrent operator-manual + auto-fire invocations don't share state. Lead-level guard (`deposit_payment_intent_id`) re-checked inside LEADS_LOCK in the mint script.
- A-HIGH-3 + B-HIGH-3 — `actor="caller"` for the void path (CommercePaymentIntentVoided.actor is `str max_length=40`, not Literal — verified in schemas.py).
- B-HIGH-2 — Pushover P1 notification on `bridge_send_failed` (money-adjacent silent-failure §12b compliance).
- B-MEDIUM-2 — `cfg.commerce.minimum_deposit_cents` (default 500 = $5) check + new `below_minimum` failure reason.
- A-MEDIUM-1 — synthetic chat_id (same as B-HIGH-1 fix above).
- Subprocess hardening also added `subprocess_timeout` to the failed-reason Literal.

**Deferred to slice-2.5 follow-up backlog:**

- A-MEDIUM-2 — emit `CateringLeadStatusChange` with `from_status=to_status="SENT_TO_CUSTOMER"` + `actor="system"` + `reason="deposit_link_minted"` for audit completeness. (Design choice for now: `CateringDepositLinkSent` IS the canonical audit row; no `lead_status_change` emission. Documented in schema docstring.)
- A-MEDIUM-3 — §12a freshness watchdog for new state files: defer; will land in slice-3 alongside webhook receiver which is when fire-rate gets meaningful.
- B-MEDIUM-1 — runbook entry documenting threshold inclusivity (`headcount >= 50` triggers deposit). Add to operator runbook in a separate doc PR.
- B-MEDIUM-3 — catering-followup agent #10 integration with `deposit_status` field. Verified via grep that #10 currently doesn't reference `deposit_*` fields; slice-2.5 ticket added.

**LOWs (deferred to slice-2.5):**

- A-LOW-1 — Hermes-first table row 9 tag refinement (cosmetic)
- A-LOW-2 — deploy ordering guard: commit 1 (schema) MUST deploy before commit 2 (script). Standard tarball deploy ships all commits atomically.
- B-LOW-1 — copy phrasing applied above (amount-first; "(25% of total)" parenthetical).
- A-LOW-3 — `actor="caller"` applied above.

---

## 10. References

- PR #321 (slice 1): https://github.com/Trivenidigital/shift-agent/pull/321
- PR #322 (deploy install): https://github.com/Trivenidigital/shift-agent/pull/322
- `tasks/hermes-commerce-prd-v2.md` — slice 1+2 design, money-moving invariants §7, customer-copy §10
- `tasks/hermes-commerce-portfolio-reconciliation.md` — caller-agent cross-reference invariant #4
- `tasks/commerce-slice1-followup-backlog.md` — slice-2 entry gates (this PR satisfies)
- `docs/portfolio.md:96` — Catering Agent #2 Phase 2 `send_deposit_link` paper-spec entry (now implemented)
- 2026-05-25 lesson: payment_reference immutability
- 2026-05-15 lesson: payment_reference immutability + fail-closed on missing
