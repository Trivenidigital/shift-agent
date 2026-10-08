# Catering + Flyer Studio — supervised pilot operator runbook

**Status:** 2026-10-08, written against deploy `50a1daa0`; the pending releases are
`8411b8c3` (PR #798, blocked at the deploy's vision-auth gate by the OpenRouter key cap)
and the gap branch `launch/catering-flyer-gaps-20261008`. Supplements
`production-pilot-shift-catering-daily-brief.md` (smoke script), `release.md`,
`rollback.md`, `catering-rollback.md`, `codex-dropin-cleanup-runbook.md`. It does not
repeat them.

Identities (authoritative): owner `+17329837841` (LID `201975216009469@lid`, also
employee e008); test customer `+19802005023` (Flyer account `CUST0005`); bot
`+918522041562` (LID `211390371475536@lid`, **never** an owner). Only these may be
messaged during rehearsals.

## 0. Before any rehearsal (5 minutes)

```
ssh main-vps 'bash -s' <<'EOF' > .pre.txt 2>&1
cat /opt/shift-agent/.commit-hash; head -3 /opt/shift-agent/DEPLOY_RECEIPT.json
systemctl is-active hermes-gateway shift-agent-cockpit catering-owner-action-watchdog
curl -s http://127.0.0.1:3000/health; echo
pid=$(systemctl show hermes-gateway -p MainPID --value); tr '\0' '\n' </proc/$pid/environ | grep -E '^(FRONT_BRAIN_OUTBOUND_ENFORCE|CATERING_(STOP|TAKEOVER|AUTOMATION_CONTROL)|FLYER_STYLE_REGISTERS_ALLOWLIST)'
runuser -u shift-agent -- /usr/local/bin/identify-sender +17329837841 | head -c 200; echo
runuser -u shift-agent -m -- /usr/local/bin/pilot-readiness-check --text | head -3
/usr/local/bin/check-openrouter-balance --notify-bin /bin/true
EOF
```
Go only if: `.commit-hash` label and receipt `commit` both equal the expected release; three units active; bridge `connected` with
`queueLength: 0`; owner resolves with `"owner"` in roles; readiness READY; both
`openrouter_balance_ok` and `openrouter_key_limit_ok` (a key at its cap returns HTTP 403
on every model call — nothing in Flyer works, and the symptom in state is
`provider_unavailable` / `reference_low_confidence`, not an error you will see); the
deploy's own `vision-auth-smoke` exits 0 (`/usr/local/bin/vision-auth-smoke`; HTTP 403 =
key cap exhausted and NO deploy can complete).
`flyer-recovery-watchdog.service` must show `User=shift-agent`
(`systemctl cat flyer-recovery-watchdog.service | grep ^User`; the root drop-in was
quarantined to `/root/quarantine/codex-dropins-20261008T151914Z/` on 2026-10-08).

Always run product CLIs as the service account with the gateway's environment
(`runuser -u shift-agent -m -- …`). Root-run writers re-own state files and break the
watchdogs (incident 2026-10-03; `codex-dropin-cleanup-runbook.md` §2).

## 1. Flyer — customer journey (text brief; no reference image)

1. Customer (`+19802005023`) sends one message containing business name, address,
   phone, **exactly one** offer with a price, a schedule, and the words
   `TEST ONLY - NOT A REAL OFFER` during rehearsals. The deterministic router
   (`cf-router`) creates the project; the customer gets a processing ack, then one
   preview (`flyer.concept_count: 1`).
2. Inspect the preview (`state/flyer/finals/` or the WhatsApp image): business name,
   phone, price, offer count = 1, no duplicated headline, no invented items.
3. Customer replies exactly `APPROVE` (also accepted: `approved`, `looks good`,
   `go ahead`, `send it`, `finalize`, `ship it`, `good to go`, or a bare 👍). Anything
   else in `awaiting_final_approval` is treated as a revision request.
4. Four finals are produced without a second image-model call and sent:
   WhatsApp image, Instagram post, Instagram story, printable PDF. Project status →
   `delivered`; audit row `flyer_assets_delivered`.
5. "delivered" means the bridge accepted each send (`outbound_message_id` per asset);
   there is no read-receipt handling (`sendReadReceipts:false`). Handset receipt is a
   human check.
6. Duplicate `APPROVE` after delivery: the router replies with the project status and
   does not resend. A replayed native message id is dropped.

Menu photo without prices: since the gap release, a `menu_reference` whose items carry no
prices is accepted for customers listed in `FLYER_PRICELESS_MENU_ALLOWLIST` (comma-separated
E.164 phones or `*`; unset = previous behaviour, i.e. `manual_edit_required /
reference_low_confidence` with detail `price-less menus not enabled for this customer`).
The flyer renders the item names with NO prices; the prompt forbids prices and visual QA
treats any visible currency amount as `fabricated price visible` → manual review. To enable
for the pilot identities set `FLYER_PRICELESS_MENU_ALLOWLIST=+17329837841,+19802005023` in
`/root/.hermes/.env` (edit the symlink TARGET, back it up first) and restart
`hermes-gateway`. Recovering a project already queued (F0226, F0228):
`runuser -u shift-agent -m -- flyer-manual-queue --retry-extraction F0228` resets the failed
extraction and returns the project to `generating_concepts` (sends nothing); then
`runuser -u shift-agent -m -- /usr/local/bin/generate-flyer-concepts --project-id F0228`
renders previews into `state/flyer/` — NOTE: previews rendered this way are NOT delivered to
the customer (only the router's inbound path sends them); use it to inspect the result, and
have the customer re-send the photo for an end-to-end delivery.

Trial/quota: `CUST0005` (+19802005023, `primary_chat_id 269612545511591@lid`) is a trial
account: 3 flyers per period, 1 used; the period rolls forward automatically
(`account._roll_period`) so a trial never expires by date — it is eligible for the journey
today. Quota-blocked trial reply: "Your free trial has used N/M sample flyers… reply CHANGE
PLAN STARTER/GROWTH/UNLIMITED". Legitimate trial → paid path (never fake it): (1) an admin
number of the account (business WhatsApp / onboarding phone) sends `CHANGE PLAN STARTER`;
(2) the same number replies `CONFIRM UPDATE` (sets `pending_plan_id`, audit
`flyer_account_updated`); (3) after a REAL payment, operator runs
`runuser -u shift-agent -m -- manage-flyer-account --activate-customer CUST0005 --provider manual --payment-reference <real reference> --expected-plan starter --amount-cents 4999`.
`--expected-plan` must equal the pending plan or the CLI refuses (`no_pending_activation`).
A manual reference is recorded, not verified against a processor — only enter one you hold.

## 2. Catering — customer journey (explicit quantities; automatic sizing held)

Facts: menu v3 has 77 items and **no confirmed serving sizes**; pricebook v2 packages
are pilot/test rates (`updated_by: manual`). `deposit_pct = 0`;
`CATERING_ACCEPTANCE_ARM = 0` (customer "we accept" is not booked automatically).
Pricebook packages: 'Vegetarian Buffet' $14.99/person and 'Mixed Veg and Non-Veg Buffet'
$19.99/person (min 25) are PILOT values per the pricebook notes; `tax_rate_bps=0` is not a
real tax rate — do not quote a paying customer on them.

1. Customer sends an inquiry with date, headcount and event type, e.g.
   `Catering for a birthday on November 21 for 40 guests, pickup 6pm. Please send menu options. TEST ONLY - NOT A REAL ORDER.`
   → lead created (owner card sent to `owner.self_chat_jid`), proposal options sent.
2. Customer states explicit quantities, e.g. `10 trays of Idly, 7 Masala Dosa, 5 Chicken Biryani`.
   The explicit-quantity arm (gap release; `CATERING_EXPLICIT_QTY_ENABLED` default 1 AND
   sender in `CATERING_AUTOMATION_CONTROL_ALLOWLIST`) matches each phrase to exactly one menu
   name, prices it with the pricebook kernel, runs `finalize-catering-menu` WITHOUT headcount
   scaling, sends the owner card and acks the customer ("…saved for owner approval. Final
   pricing comes after owner review."). A phrase that matches no single menu item gets ONE
   clarification listing up to 3 exact menu names; nothing is saved. Headcount unknown → the
   arm stands down (quote would be un-approvable) and the message follows the previous path.
   `I'll take Option 1` (tier/option pick) is still refused with "needs restaurant review of
   serving sizes" while no item has a confirmed `serves` (fail-closed guard, PR #796). Beware:
   before the gap release "I'll take 2 trays of Idly" was read as Option 2; the explicit arm
   now runs first.
3. Operator fallback (only if the arm stood down):
   `runuser -u shift-agent -m -- finalize-catering-menu --code <#CODE> --customer-message-id <id> --selected-items-json '[{"name":"Idly (3 PCS)","qty":10,"price_usd":6}]' --quote-total-usd 60`
   — `price_usd` and `--quote-total-usd` are WHOLE DOLLARS (ints; `5.99` is rejected); the
   kernel recomputes cents from the pricebook and the total must be within min(5%, $25) of
   the item subtotal.
4. Owner (`+17329837841`) replies `#CODE approve`. `apply-catering-owner-decision` sends
   the frozen integer-cent quote to the customer; lead → `SENT_TO_CUSTOMER`. The
   OTP-protected cockpit (`POST /leads/{id}/decision`) is the alternative route.
5. End of the pilot journey. Deposits (`catering-mint-deposit`) stay off until
   `deposit_pct > 0` and a real payment template exist.

Controls: customer `STOP` / `PAUSE` / `RESUME` (whole message); owner
`#CODE takeover`, `#CODE release`, `#CODE hold`. Automation control is scoped by
`CATERING_AUTOMATION_CONTROL_ALLOWLIST`.

## 3. Recovery

| Symptom | Where to look | Action |
|---|---|---|
| Owner receives "Thanks for your message! I'm here to help…" instead of the brief/card | `decisions.log` `front_brain_outbound_refused` | Scripted `bridge_post` sends to the primary owner (`owner.self_chat_jid`/`phone`/`lid`) are exempt from the front-brain screen since the 2026-10 fix; the owner's free-form LLM chat and any `authorized_identities` alias are still screened by design. If the row names a scripted owner send, check `config.yaml` `owner.*` matches the live owner. |
| Hourly Pushover "Flyer manual queue SLA breach" | `flyer-manual-queue --triage` | Dispose the row: `--complete <id> --asset <path>` or `--close <id> --reason … [--no-notify]` |
| Flyer stuck `manual_edit_required` with `reference_low_confidence` | project `reference_extractions.detail` | Price-less menu photo; enable `FLYER_PRICELESS_MENU_ALLOWLIST` for the customer, then `flyer-manual-queue --retry-extraction <id>` (see §1) |
| Owner receives the brief/card — verify content | `decisions.log` `front_brain_owner_exempt_send.message_text` (gap release) | The exact body delivered to the owner; absence of `front_brain_outbound_refused` alone is not evidence |
| Deploy aborts `vision-auth-smoke: AUTH FAIL — HTTP 403` and auto-rolls back | `check-openrouter-balance` → `openrouter_key_limit_*` | Raise the per-key cap at openrouter.ai/settings/keys (separate from account credits); no override exists for this gate |
| Customer quantities answered with a clarification | `audit_intercepted` detail `explicit_qty … clarification_sent` | Expected when a phrase matches no single menu name; customer repeats with exact names |
| Model calls fail, state shows `provider_unavailable` | `check-openrouter-balance` events | Raise key cap / top up credits (openrouter.ai) |
| Root-owned files under `state/` | `find /opt/shift-agent/state -user root` | A writer ran as root; `chown shift-agent:shift-agent` (modes preserved) and find the root runner (drop-ins!) |
| Catering send suppressed `automation_suppressed:read_error` | `catering_automated_send_suppressed` rows | state file unreadable by the service account — ownership |

## 4. Rollback

- Config/flag: restore the timestamped backup (`/root/.hermes/.env.before-*`,
  `/opt/shift-agent/config.yaml.before-*`), restart `hermes-gateway shift-agent-cockpit
  catering-owner-action-watchdog`, verify `/proc/<pid>/environ`.
- Code: `shift-agent-deploy rollback <deploy-tag>` (targets: `ls /opt/shift-agent/deploys`);
  current prior tag `deploy-20261003-013123-50a1daa0` (live).
  `deploys/deploy-20261008-155259-8411b8c3.tgz` is the staging snapshot of a FAILED attempt,
  not a release.
- Then the mandatory checks in `rollback.md` (locked facts, QR, fallback) and
  `catering-rollback.md`.

## 5. Monitoring that must stay green

`alert-integrity-watchdog` (decisions.log freshness), `shift-agent-health` (5 min),
`openrouter-balance-check` (daily 14:00 UTC — balance **and** key cap),
`flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog` (must run as `shift-agent`; verified 2026-10-08 15:21Z after the
drop-in quarantine),
`catering-owner-action-watchdog` (service). Owner pages go through
`shift-agent-notify-owner` (Pushover, plain text).
