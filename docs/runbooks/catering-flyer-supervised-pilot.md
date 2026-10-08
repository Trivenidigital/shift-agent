# Catering + Flyer Studio — supervised pilot operator runbook

**Status:** 2026-10-08, written against deploy `50a1daa0` on main-vps. Supplements
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
cat /opt/shift-agent/DEPLOY_RECEIPT.json | head -3
systemctl is-active hermes-gateway shift-agent-cockpit catering-owner-action-watchdog
curl -s http://127.0.0.1:3000/health; echo
pid=$(systemctl show hermes-gateway -p MainPID --value); tr '\0' '\n' </proc/$pid/environ | grep -E '^(FRONT_BRAIN_OUTBOUND_ENFORCE|CATERING_(STOP|TAKEOVER|AUTOMATION_CONTROL)|FLYER_STYLE_REGISTERS_ALLOWLIST)'
runuser -u shift-agent -- /usr/local/bin/identify-sender +17329837841 | head -c 200; echo
runuser -u shift-agent -m -- /usr/local/bin/pilot-readiness-check --text | head -3
/usr/local/bin/check-openrouter-balance --notify-bin /bin/true
EOF
```
Go only if: receipt commit = expected; three units active; bridge `connected` with
`queueLength: 0`; owner resolves with `"owner"` in roles; readiness READY; both
`openrouter_balance_ok` and `openrouter_key_limit_ok` (a key at its cap returns HTTP 403
on every model call — nothing in Flyer works, and the symptom in state is
`provider_unavailable` / `reference_low_confidence`, not an error you will see).

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

Known limitation (open): a **menu photo without prices** is rejected as
`reference_low_confidence` (`reference_extract.py:736`) and lands in the manual queue —
both genuine attempts to date (F0226, F0228) failed this way. Ask customers for a text
brief, or a menu with prices, until that rule is revisited.

Trial/quota: `CUST0005` is a trial account (3 flyers, 1 used). Quota blocks return
"Please complete payment first" — do not fake activation; the operator path is
`manage-flyer-account --activate-customer <CUSTnnnn> --provider manual --payment-reference <real ref> --amount-cents <n>`.

## 2. Catering — customer journey (explicit quantities; automatic sizing held)

Facts: menu v3 has 77 items and **no confirmed serving sizes**; pricebook v2 packages
are pilot/test rates (`updated_by: manual`). `deposit_pct = 0`;
`CATERING_ACCEPTANCE_ARM = 0` (customer "we accept" is not booked automatically).

1. Customer sends an inquiry with date, headcount and event type, e.g.
   `Catering for a birthday on November 21 for 40 guests, pickup 6pm. Please send menu options. TEST ONLY - NOT A REAL ORDER.`
   → lead created (owner card sent to `owner.self_chat_jid`), proposal options sent.
2. Customer replies `I'll take Option 1`. With no serving facts the selection is refused
   with "needs restaurant review of serving sizes" and the owner is paged — this is the
   fail-closed guard from PR #796, not a defect.
3. Operator converts the request to explicit quantities (menu names must match exactly):
   ```
   runuser -u shift-agent -m -- finalize-catering-menu --code <#CODE> \
     --customer-message-id <id> \
     --selected-items-json '[{"name":"Idly (3 PCS)","qty":10,"price_usd":5.99}]' \
     --quote-total-usd 60
   ```
   (`--quote-total-usd` is an integer; the kernel recomputes cents from the items.)
   This finalizes the customer side and sends the owner card ending
   `Reply: #CODE approve / #CODE edit <changes> / #CODE reject <reason>`.
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
| Owner receives "Thanks for your message! I'm here to help…" instead of the brief/card | `decisions.log` `front_brain_outbound_refused` | Owner identities must not be in `FRONT_BRAIN_OUTBOUND_ENFORCE_ALLOWLIST` (post-fix: owner-directed sends are exempt in `safe_io`) |
| Hourly Pushover "Flyer manual queue SLA breach" | `flyer-manual-queue --triage` | Dispose the row: `--complete <id> --asset <path>` or `--close <id> --reason … [--no-notify]` |
| Flyer stuck `manual_edit_required` with `reference_low_confidence` | project `reference_extractions.detail` | Price-less menu photo; ask for a text brief or prices |
| Model calls fail, state shows `provider_unavailable` | `check-openrouter-balance` events | Raise key cap / top up credits (openrouter.ai) |
| Root-owned files under `state/` | `find /opt/shift-agent/state -user root` | A writer ran as root; `chown shift-agent:shift-agent` (modes preserved) and find the root runner (drop-ins!) |
| Catering send suppressed `automation_suppressed:read_error` | `catering_automated_send_suppressed` rows | state file unreadable by the service account — ownership |

## 4. Rollback

- Config/flag: restore the timestamped backup (`/root/.hermes/.env.before-*`,
  `/opt/shift-agent/config.yaml.before-*`), restart `hermes-gateway shift-agent-cockpit
  catering-owner-action-watchdog`, verify `/proc/<pid>/environ`.
- Code: `shift-agent-deploy rollback <deploy-tag>` (targets: `ls /opt/shift-agent/deploys`);
  current prior tag `deploy-20261003-004544-0ea5af38`.
- Then the mandatory checks in `rollback.md` (locked facts, QR, fallback) and
  `catering-rollback.md`.

## 5. Monitoring that must stay green

`alert-integrity-watchdog` (decisions.log freshness), `shift-agent-health` (5 min),
`openrouter-balance-check` (daily 14:00 UTC — balance **and** key cap),
`flyer-source-edit-sla-watchdog`, `flyer-recovery-watchdog` (must run as `shift-agent`),
`catering-owner-action-watchdog` (service). Owner pages go through
`shift-agent-notify-owner` (Pushover, plain text).
