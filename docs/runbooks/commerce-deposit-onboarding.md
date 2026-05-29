# Catering Deposit Onboarding — Operator Runbook

**Status:** Live as of `deploy-20260529-145506-d485cdc3` (slice-2.5 baseline).
**Scope:** What an operator needs to do before the catering deposit caller starts sending real payment links to customers.
**Audience:** SMB-Agents operator (founder + on-call).

---

## Why this runbook exists

PR #324 wired Catering Agent #2 to the Commerce slice-1 primitives. After deploy, qualifying catering leads (headcount ≥ `cfg.catering.deposit_threshold_guests` AND `quote_total_usd > 0` AND `cfg.catering.deposit_pct > 0`) automatically trigger a deposit link in WhatsApp after owner approval.

**Default behaviour with no operator action:** `cfg.commerce.payment_checkout_url_template` defaults to `""`. Every qualifying lead gets the fail-closed customer copy `"Payment link is not configured yet. We'll send it when it's ready."` until the operator configures the template.

This is correct — but it's also dead-on-arrival from the customer's POV. This runbook tells the operator how to configure it.

---

## Step 1 — Decide the payment provider posture (slice-2.5 only allows manual links)

Slice 2.5 ships **placeholder template substitution only**. There is no Stripe/Razorpay/UPI API call, no webhook receiver. The operator provides a **manual hosted-payment URL template** that takes `{order_id}` / `{amount_cents}` / `{amount_usd}` / `{currency}` / `{intent_id}` / `{chat_id}` placeholders.

Three sensible postures:

| Posture | When | Template example |
|---|---|---|
| **Single static link** (recommended for pilot) | Operator creates ONE hosted payment page per quote manually, and the template just returns that page | `https://buy.stripe.com/test_<paymentlink-id>` (no placeholders — every customer sees the same link; operator manually reconciles which payment corresponds to which lead) |
| **Per-order link** (better, requires per-deposit operator click) | Operator pre-creates a payment link per deposit and feeds the URL in via state | Not template-friendly in slice 2.5 — defer to slice 3 |
| **Provider template substitution** | Provider supports URL-encoded amount + reference in the hosted-page URL | `https://pay.example.com/?amt={amount_cents}&ref={order_id}` |

For the **first paying customer**, use **Posture 1 (single static link)** with a Stripe Payment Link in test mode. This gives you end-to-end smoke without needing slice 3.

Slice 3 (real provider integration + webhook) is the path to per-order automated links + reconciliation.

---

## Step 2 — Configure `cfg.commerce.payment_checkout_url_template`

Edit `/opt/shift-agent/config.yaml` and add (or update) the `commerce` block:

```yaml
commerce:
  enabled: false   # opt-in flag; library is callable regardless of this
  payment_checkout_url_template: "https://buy.stripe.com/test_YOUR_LINK_ID"
  minimum_deposit_cents: 500   # $5.00 floor; refuses to mint below this
```

**Validation:**

```bash
# Verify the template substitutes correctly (run as root on main-vps)
python3 -c "
import sys
sys.path.insert(0, '/opt/shift-agent')
from safe_io import load_yaml_model
from schemas import Config
cfg = load_yaml_model('/opt/shift-agent/config.yaml', Config)
print('template:', repr(cfg.commerce.payment_checkout_url_template))
print('minimum_deposit_cents:', cfg.commerce.minimum_deposit_cents)
"
```

Expected output:
```
template: 'https://buy.stripe.com/test_YOUR_LINK_ID'
minimum_deposit_cents: 500
```

---

## Step 3 — Configure the deposit-trigger thresholds (optional; defaults are sane)

Default behaviour:

```yaml
catering:
  enabled: true
  deposit_threshold_guests: 50   # inclusive — 50-guest events trigger the deposit
  deposit_pct: 0.25              # 25% of quote_total_usd
```

To **disable the entire deposit hook** (kill switch — safe rollback without redeploying):

```yaml
catering:
  deposit_pct: 0   # any qualifying lead skips the deposit mint
```

To **raise the threshold** (e.g., only require deposits for ≥100-guest events):

```yaml
catering:
  deposit_threshold_guests: 100
```

After editing, no restart needed — the next `apply-catering-owner-decision` invocation re-reads `config.yaml`.

---

## Step 4 — Smoke against scratch state (no live customer touched)

Per the same pattern used in the PR #324 + PR #327 deploys:

```bash
ssh main-vps 'bash -c "
cd /tmp && rm -rf commerce-deposit-runbook-smoke
mkdir -p commerce-deposit-runbook-smoke/state/commerce commerce-deposit-runbook-smoke/logs
cd commerce-deposit-runbook-smoke
# Use prod config but override state paths
cp /opt/shift-agent/config.yaml ./config.yaml
cat > state/catering-leads.json << JSON
{
  \\\"leads\\\": [{
    \\\"lead_id\\\": \\\"LSMOKE\\\",
    \\\"status\\\": \\\"SENT_TO_CUSTOMER\\\",
    \\\"customer_phone\\\": \\\"+15550000099\\\",
    \\\"customer_name\\\": \\\"SmokeCustomer\\\",
    \\\"raw_inquiry\\\": \\\"catering for 100\\\",
    \\\"original_message_id\\\": \\\"msg_smoke\\\",
    \\\"created_at\\\": \\\"2026-05-29T14:00:00Z\\\",
    \\\"updated_at\\\": \\\"2026-05-29T14:00:00Z\\\",
    \\\"quote_text\\\": \\\"Quote for 100 guests Total \\$600\\\",
    \\\"quote_version\\\": 1,
    \\\"quote_total_usd\\\": 600,
    \\\"extracted\\\": {\\\"headcount\\\": 100, \\\"event_date\\\": \\\"2026-06-15\\\"}
  }]
}
JSON

SHIFT_AGENT_CONFIG_PATH=\$(pwd)/config.yaml \
SHIFT_AGENT_LEADS_PATH=\$(pwd)/state/catering-leads.json \
SHIFT_AGENT_LEADS_LOCK=\$(pwd)/state/catering-leads.json.lock \
SHIFT_AGENT_LOG_PATH=\$(pwd)/logs/decisions.log \
COMMERCE_CARTS_PATH=\$(pwd)/state/commerce/carts.json \
COMMERCE_ORDERS_PATH=\$(pwd)/state/commerce/orders.json \
COMMERCE_INTENTS_PATH=\$(pwd)/state/commerce/payment_intents.json \
COMMERCE_REFERENCES_PATH=\$(pwd)/state/commerce/payment_references.json \
PYTEST_CURRENT_TEST=smoke \
python3 /usr/local/bin/catering-mint-deposit --lead-id LSMOKE
"'
```

**Expected outcome (template configured):**
- Exit code 6 (`bridge_send_failed` — the fake phone `+15550000099` doesn't exist on WhatsApp, so the bridge POST fails; this is expected and proves the script reached the bridge stage)
- Audit log shows the full attempted/failed/voided/cancelled triple
- Order in `cancelled` state (slice-2.5 ledger-cleanliness fix)

**Expected outcome (template `""`):**
- Same as above but with the "Payment link is not configured yet" copy attempted instead of a real URL

If exit code is something other than 6 (e.g., 2 = invalid input, 5 = schema violation), check the audit log row + stderr for the failure reason.

**Cleanup:**
```bash
ssh main-vps 'rm -rf /tmp/commerce-deposit-runbook-smoke'
```

---

## Step 5 — Watch for the first real customer

After Steps 1-4, the next qualifying owner-approved catering lead will trigger a real deposit-link message.

**Signals to watch:**

```bash
# Operator's daily audit-log query
ssh main-vps 'grep -E "catering_deposit_link_(sent|failed)" /opt/shift-agent/logs/decisions.log | tail -20'
```

- **`catering_deposit_link_sent` with `url_status="configured"`** → happy path; customer received the link
- **`catering_deposit_link_sent` with `url_status="unconfigured"`** → template is empty; operator forgot Step 2. Customer got the "not configured yet" copy. **Action: complete Step 2 + re-invoke `catering-mint-deposit --lead-id <id>`**
- **`catering_deposit_link_failed`** → mint or send failure. Pushover P1 fires on `bridge_send_failed`. Check the `reason` field:
  - `below_minimum` → quote total too small for deposit; expected
  - `cart_build_failed` / `order_create_failed` / `intent_mint_failed` → operator-side bug; check stderr in journald
  - `bridge_send_failed` → WhatsApp bridge transient; operator should re-invoke `catering-mint-deposit --lead-id <id>` after bridge recovers
  - `subprocess_timeout` → script hung; treat as `bridge_send_failed`

---

## Step 6 — Re-invocation procedure (operator action on failure)

If a deposit-mint failed and the operator wants to retry:

```bash
ssh main-vps '/usr/local/bin/catering-mint-deposit --lead-id L0007'
```

The script is **idempotent on `lead.deposit_payment_intent_id`**: if a prior attempt already minted (rare — only happens if the bridge POST succeeded after a prior failure mid-flight), re-invocation is a no-op and reports `noop_already_minted`. Otherwise it mints a fresh intent against a new `order_id`.

**Note:** the prior failure's order was already cancelled by slice-2.5 cleanup (`bridge_send_failed_orphan_cleanup`), so the ledger stays clean across retries.

---

## Step 7 — Kill switch

To **immediately disable** the deposit hook without redeploying:

```bash
# Edit /opt/shift-agent/config.yaml and set:
catering:
  deposit_pct: 0
```

Effect: `_should_mint_deposit` returns False for every lead → hook short-circuits before any commerce primitive runs. The next `apply-catering-owner-decision` invocation picks up the new config (no restart needed; YAML re-read on each invocation).

---

## What slice 3 will add (NOT in scope here)

Per `tasks/hermes-commerce-prd-v2.md` §12:
- Real Stripe/Razorpay/UPI provider integration (per-order auto-generated links)
- Webhook receiver daemon + signature verification
- `commerce_payment_confirmed` audit row + lead `deposit_status="paid"` transition
- Cockpit "Deposit-pending leads" tab
- §12a freshness watchdog on `state/commerce/*.json`

Slice 3 requires operator decisions on provider, credentials, signature scheme. See the slice-3 entry gates in `~/.claude/projects/C--projects-sme-agents/memory/project_commerce_primitives_decision.md`.

---

## Related docs

- **Design:** `tasks/hermes-commerce-slice2-catering-deposit-caller-design.md`
- **PRD:** `tasks/hermes-commerce-prd-v2.md` §6 (compliance matrix) + §7 (money discipline) + §10 (handoff)
- **Slice-2 follow-up backlog:** `tasks/commerce-slice2-catering-deposit-followup-backlog.md`
- **PRs:**
  - PR #321 — slice 1 commerce primitives
  - PR #322 — slice 1 deploy-script install
  - PR #324 — slice 2 catering deposit caller
  - PR #327 — slice 2.5 orphan-order cleanup
