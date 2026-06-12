# PR-α production runtime fingerprint — 2026-05-26

First captured production evidence of PR-α (PR #251) catching natural-language plan-change phrases and routing them through the deterministic structured account path (NOT the generic LLM fallback that prompted the regulated-intent control layer).

## What this evidence proves

| Claim | Evidence |
|---|---|
| PR-α's regex extension caught "Upgrade to Growth Plan" in production | `cf_router_intercepted` audit row with `reason=flyer_account_command` for this exact message |
| Customer-visible reply was deterministic + role-aware (NOT a generic "I've processed your upgrade" claim) | Screenshot from operator + ack_message_id `3EB0A8EEA033064F70D729` ties to a successful send (`ack_error=""`) |
| Routing took the PREFERRED structured account path, not the regulated_account_guard fallback | Audit reason is `flyer_account_command`, NOT `flyer_regulated_account_guard`. The guard is fallback; this message took the upstream `_try_flyer_account_intercept` route exactly as PR-α designed. |
| Role-aware billing CTA from PR #246/248 composed correctly on top of PR-α | `flyer_account_updated` row shows `allowed=false reason=admin_required` because the sender (`+19045550104`) is an `authorized_request_numbers` entry, NOT the business owner (`+17329837841`). |
| Classifier behavior matches on the deployed runtime | Inline no-send check on `/root/.hermes/plugins/cf-router/actions.py:is_flyer_account_command` + `is_flyer_regulated_account_intent` returns True for both `"UPGRADE PLAN - show Flyer Studio plans"` and `"Upgrade to Growth Plan"` |

## Audit fingerprint (verbatim from `/opt/shift-agent/logs/decisions.log`)

Three rows document the full interaction, in time order:

```
2026-05-26T13:09:35.005633Z  cf_router_intercepted  reason="flyer_account_command"
                             chat_id="201975216009469@lid"
                             detail="customer_id=CUST0001; status=trial; sender_role=employee;
                                     ack_message_id=3EB089AE5006BBE8AD5CEB; ack_error="
```
→ First message ("UPGRADE PLAN - show Flyer Studio plans") landed at cf-router, classified as account_command (deterministic plan-menu request), routed via `_try_flyer_account_intercept`, replied successfully.

```
2026-05-26T13:09:52.203772Z  flyer_account_updated  customer_id="CUST0001"
                             command="change_plan"
                             actor_phone="+19045550104"
                             actor_role="employee"
                             allowed=false  reason="admin_required"
```
→ Second message ("Upgrade to Growth Plan") was classified as `change_plan` account-command (via PR-α's extended `ACCOUNT_COMMAND_RE`). The account handler's role check failed because `+19045550104` is in `authorized_request_numbers` but NOT in `admin_phones = {business_whatsapp_number, onboarded_by_phone} = {'+17329837841'}`. The handler returned `_plan_change_admin_required_reply` per the 2026-05-25 lesson (`tasks/lessons.md` line 156).

```
2026-05-26T13:09:52.278895Z  cf_router_intercepted  reason="flyer_account_command"
                             chat_id="201975216009469@lid"
                             detail="customer_id=CUST0001; status=trial; sender_role=employee;
                                     ack_message_id=3EB0A8EEA033064F70D729; ack_error="
```
→ Outbound reply (role-aware denial copy + redirect to business owner) sent successfully.

## Customer record state at the time of the interaction

```
customer_id:                  CUST0001
business_name:                Lakshmi's Kitchen
status:                       trial
plan_id:                      trial
business_whatsapp_number:     '+17329837841'   ← admin
onboarded_by_phone:           '+17329837841'   ← admin
public_phone:                 '+17329837841'   ← admin
primary_chat_id:              '17329837841@s.whatsapp.net'
authorized_request_numbers:   ['+17329837841', '+19045550104']
```

The screenshot sender `+19045550104` is in `authorized_request_numbers` (can request flyers) but not in `admin_phones` (cannot change billing) — exactly the case the role-aware billing CTA was designed for.

## Customer-visible behavior (from operator's screenshot)

Two messages from `+19045550104` (chat_id `201975216009469@lid`):

1. **Customer:** `UPGRADE PLAN - show Flyer Studio plans`
   **Bot reply:**
   ```
   Flyer Studio
   ------------
   Plans for Lakshmi's Kitchen:
   Starter - $49.99/month - 30 flyers/month
   Growth - $69.99/month - 60 flyers/month
   Unlimited - $199/month - unlimited flyers/month

   Current plan: trial.
   Plan changes must be requested from the business WhatsApp number +17329837841 or the account owner.
   This chat can still request flyers for the business.
   ```

2. **Customer:** `Upgrade to Growth Plan`
   **Bot reply:**
   ```
   Flyer Studio
   ------------
   Plan changes must be requested from the business WhatsApp number +17329837841 or the account owner.

   This chat can still request flyers for the business.
   ```

Neither reply contains any forbidden completion verb (`processed`, `upgraded`, `changed`, `confirmed`, `applied`). Both replies explicitly redirect to the business owner. Four-part invariant honored.

## Classifier behavior on the deployed runtime (no-send recheck)

Run on `main-vps` against `/root/.hermes/plugins/cf-router/actions.py` via the Hermes venv Python:

| Phrase | `is_flyer_account_command` | `is_flyer_regulated_account_intent` |
|---|---|---|
| `UPGRADE PLAN - show Flyer Studio plans` | True | True |
| `Upgrade to Growth Plan` | **True (PR-α regex extension)** | True |

Both phrases match the upstream `is_flyer_account_command` regex — so the routing takes the preferred structured account path. The regulated_account_guard fallback (`_try_flyer_regulated_account_guard`) does NOT fire because account-intercept already handled the message — exactly the order PR-α designed.

## What this evidence does NOT prove

- The business owner upgrading from `+17329837841` — that interaction has not occurred in this audit window.
- PR-β's delivery-state guard firing — no `where is my flyer` / `did you send my flyer` / `send my flyer` traffic from non-active-project senders observed.
- PR-β.1's `send now` path firing — no `send now` traffic observed.
- The `_try_flyer_regulated_account_guard` fallback firing — by design, since `is_flyer_account_command` caught both messages upstream. The guard is reserved for regulated-intent text that does NOT match a deterministic account command.

## File inventory

| File | Captures |
|---|---|
| `audit_probe.txt` | Read-only SSH probe output — the 3 audit rows in time order + the no-send classifier recheck on the deployed runtime |

## Cross-references

- PR #251 (PR-α): https://github.com/Trivenidigital/shift-agent/pull/251 (merged 2026-05-26T01:08:41Z, deployed as `deploy-20260526-014612-6e0ffeb6`)
- PR-α deploy evidence: `tasks/evidence/2026-05-26-pr251-deploy/README.md`
- 2026-05-25 lesson on role-aware billing CTAs: `tasks/lessons.md` line ~156 ("Flyer Studio billing CTAs must be role-aware...")
- Architecture doc: `tasks/regulated-intent-control-layer-architecture-2026-05-25.md`

## Audit-trail note

This evidence is committed on the orphaned local branch `fix/flyer-send-now-deterministic` (remote deleted post PR #260 merge). Per operator direction, the evidence is intended to fold into the next PR-γ rather than open a separate docs-only PR. Cherry-pick the evidence commit when PR-γ starts.
