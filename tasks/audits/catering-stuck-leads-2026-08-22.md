# Why nobody was told about the three 74-day-old catering leads — 2026-08-22

**Drift-check tag:** `Hermes-native` — read-only investigation. No code, no state mutation, no
send-tests. Proposes no change; §6 records two candidate fixes for ruling.

**Hermes-first analysis** — nothing is being built, so no capability check applies. Noted for
completeness: every mechanism examined here (TTL sweep, owner-action watchdog, daily brief) is
already in-tree; the finding is that one of them is disarmed and another mislabels its output.

**Verdict up front: this is a DEFECT, in two independent parts.**

| Part | What | Class | Blast radius |
|---|---|---|---|
| **A** | `catering-lead-ttl-sweep` is disarmed — the guard built for exactly this incident has never run | **operator action** (needs ruling — it mutates catering state) | 3 leads → `STALE`, 3 owner alerts |
| **B** | The daily brief attributes owner-blocked leads to the customer | **code fix, ~5 lines, outside catering** | brief wording only |

**The premise in the assignment resolves differently than expected**, and one severity factor
cuts the other way: the "2 approval rows vs 3 leads" gap is a log-retention artifact, not a
missing request (§1), and all three leads were created by the **owner's own second identity**,
so no real customer is waiting (§5). The defects are real; the customer harm is not.

---

## 1. Q1 — Was the owner ever asked? YES, for all three. The gap is log rotation.

**The discrepancy is an artifact of the audit horizon, not of a skipped code path.**

`/etc/logrotate.d/shift-agent` rotates `decisions.log` **`daily` / `rotate 30`**. The oldest
retained row is `2026-07-22T20:07:41`. So:

| Lead | Created | Inside the 30-day audit window? | `catering_owner_approval_requested` row retained? |
|---|---|---|---|
| L0017 | 2026-06-09 | **No** — 43 days before the floor | rotated away |
| L0018 | 2026-07-21 | **No** — 1 day before the floor | rotated away |
| L0019 | 2026-07-23 | Yes | **Yes** — `{"type":"catering_owner_approval_requested","lead_id":"L0019","approval_code":"#7GCQP"}` |

The two retained `catering_owner_approval_requested` rows are **L0019 and L0020** — and L0020 is
no longer one of the three, having moved to `CUSTOMER_FINALIZED` seven minutes after creation.
So exactly one of the three stuck leads falls inside the window, and it has its row.

**Why the other two were also asked, on the evidence available.** The row is not conditional
logic that could have been skipped — it is emitted in the same atomic instant as the status
transition. For both L0019 and L0020 the three rows (`catering_lead_created`,
`catering_lead_status_change NEW→AWAITING_OWNER_APPROVAL reason=extractor_completed`,
`catering_owner_approval_requested`) carry **timestamps identical to the microsecond**. L0017 and
L0018 both sit in `AWAITING_OWNER_APPROVAL` with a populated `owner_approval_code` (`#4SX94`,
`#8D9YG`) — that state is the artifact the same emission produces.

Stated honestly: **inferred, not witnessed.** The direct evidence was rotated away.

**This is itself a finding worth keeping:** the audit horizon (30 days) is shorter than the
lifecycle it audits (a lead with no TTL lives forever). Any lead older than 30 days is
permanently unauditable for how it came to be. That is a structural blind spot, not a bug — but
it means "no audit row" can never be read as "it never happened" for catering leads.

---

## 2. Q2 — What should have re-nudged? A guard exists, built for this exact lead, and it is disarmed.

### The mechanism that should have fired

`src/agents/catering/scripts/catering-lead-ttl-sweep` exists for precisely this case. Its own
docstring names the incident:

> An `AWAITING_OWNER_APPROVAL` catering lead that the owner never acts on sits open forever,
> silently capturing every later inbound as a follow-up (**the L0017 incident: a June lead still
> open on 2026-07-21**). This sweep transitions leads with no activity for
> `CATERING_LEAD_TTL_DAYS` to the legal terminal `STALE` status **and alerts the owner at the
> write site (§12b)**.

`src/platform/catering_lead_sweep.py:27-29` is equally explicit about who is blocked:

> `AWAITING_OWNER_APPROVAL` is waiting on the **OWNER**, who may legitimately take a week.

### Why it has never run

```python
if not _enabled() and not args.dry_run:
    return 0  # dormant — operator has not armed the sweep
```

`ENABLED_ENV = "CATERING_LEAD_TTL_SWEEP_ENABLED"`. The unit
`/etc/systemd/system/catering-lead-ttl-sweep.service` loads
`EnvironmentFile=/opt/shift-agent/.env` (a symlink → `/root/.hermes/.env`), and that file **does
not contain the flag**. Verified: `grep -rn CATERING_LEAD_TTL_SWEEP_ENABLED /etc/systemd/system/
/root/.hermes/.env /opt/shift-agent/config.yaml` matches only the unit's own *comment* text. A
full flag-name dump of `.env` shows 33 `FLYER_*` flags and 2 `CATERING_*` flags armed
(`CATERING_QUALIFICATION_GATE`, `CATERING_ACCEPTANCE_ARM`) — **neither sweep flag**.

So the answer to "not reaching the rows, or thresholds never trigger?" is **neither**: the script
returns at its first branch, before it reads `leads.json` at all. The timer is healthy and fires
daily at 03:20 (ran `Aug 22 03:20:13`, `Deactivated successfully`) — it is faithfully executing a
no-op.

The unit file states this openly:

> Enabling this timer changes NOTHING on its own: the sweep returns 0 without reading state
> unless `CATERING_LEAD_TTL_SWEEP_ENABLED=1` in `/opt/shift-agent/.env`.

### The thresholds would trigger comfortably

`CATERING_LEAD_TTL_DAYS = 21` (`catering_lead_sweep.py:24`). Selection is
`updated_at <= now - 21d`. All three leads have `updated_at == created_at` — never touched:

| Lead | `updated_at` | Age at 2026-08-22 | > 21d? |
|---|---|---|---|
| L0017 | 2026-06-09 | **74 days** | yes |
| L0018 | 2026-07-21 | 32 days | yes |
| L0019 | 2026-07-23 | 30 days | yes |

### The other two candidate mechanisms do not apply

- **`catering-proposal-expiry-sweep`** — also disarmed (`CATERING_PROPOSAL_SWEEP_ENABLED` absent
  from `.env`), but **out of scope regardless**: it retires `SENT` *proposal sets*, not leads. It
  would never have touched these rows. Its docstring calls itself "the money-side twin of the
  L0017 never-closing-lead incident" — a sibling, not a substitute.
- **`catering-owner-action-watchdog`** (`active running`, the one catering unit that IS live) —
  **reactive, not proactive.** Per its docstring it fires when the owner *sends* `#XXXXX
  approve|reject|edit` and the dispatcher drops it. It has no timer-driven scan for un-actioned
  leads. The owner sent nothing here, so it correctly did nothing. Its presence is easy to
  mistake for coverage; it is not.

---

## 3. Q3 — Did the leads pre-date the guard? No. The live sweep would select all three today.

This matters because "a lead created before the sweep shipped" and "a lead the live sweep is
ignoring" are different findings. It is the second.

**Selection is age-anchored, not deploy-anchored.** `catering_lead_sweep.py` is explicit:

> Anchored on `updated_at` (last lead activity) rather than `created_at`: a lead the owner or
> customer touched recently is NOT stale.

There is no deploy-date floor, no "created after" guard, and no grandfather clause anywhere in
`find_expired_awaiting_leads` or `_expired_in_status`. A 74-day-old lead is selected on exactly
the same terms as a 22-day-old one.

**Timeline:**

| Date | Event | Source |
|---|---|---|
| 2026-06-09 | L0017 created | lead record |
| 2026-07-21 | Sweep **code** ships (#635 `f3e8d402`) — citing L0017 by name | `git log` |
| 2026-07-21 | L0018 created — *the same day the guard against it shipped* | lead record |
| 2026-07-23 | L0019 created | lead record |
| 2026-08-14 | **Timer** wired (#699 `6e6017fc`), "dormant until armed" | `git log` |
| 2026-08-18 | Deployed to box; timer starts firing | `journalctl` |
| — | Flag never set | `.env` |

So the guard has existed for 32 days and been scheduled for 8, and has never once read the
leads file. **If armed today it would retire all three on its next 03:20 run** — which is what
makes this an actively-disarmed guard rather than a scope gap.

A note on the 2026-07-21 coincidence: L0018 was created the same day the sweep shipped citing
L0017. The fix landed and the failure recurred immediately, because the fix shipped off.

---

## 4. Q4 — Is the operator-visible surface truthful? NO. This is the defect that matters.

### The brief does reach the owner, and it does mention these leads

Ruling out the easy explanations first:

- `_render_catering` is **not** section-gated. `send-daily-brief:602-604` calls it
  unconditionally; only the *learning* sub-block is gated on `"catering_learning" in
  cfg.daily_brief.sections`.
- The template `src/agents/daily_brief/templates/daily_brief.txt` places `{catering_block}` in
  the body.
- The brief is genuinely delivered — 30 `brief_sent` in 30 days (§ matrix `81119c4d`).

So the owner receives a catering block every morning. The problem is what it says.

### The mislabelling

`src/agents/daily_brief/scripts/send-daily-brief:539-553`:

```python
awaiting_finalize = [l for l in leads if l.get("status") == "AWAITING_OWNER_APPROVAL"]
finalized        = [l for l in leads if l.get("status") == "CUSTOMER_FINALIZED"]
...
lines.append(f"  • Awaiting customer finalize: {len(awaiting_finalize)}")
if finalized:
    codes = ", ".join(l.get("owner_approval_code", "?") for l in finalized)
    lines.append(f"  • Awaiting your approve ({codes}): {len(finalized)}")
else:
    lines.append(f"  • Awaiting your approve: 0")
```

The list of leads whose status is `AWAITING_OWNER_APPROVAL` is printed under the label
**"Awaiting customer finalize"**.

The canonical meaning of that status is not in question. `src/platform/schemas.py:533`:

```
"AWAITING_OWNER_APPROVAL",  # quote drafted; owner needs to approve
```

and `catering_lead_sweep.py:27` — "waiting on the **OWNER**".

### Being precise: it is an undercount, not a clean inversion

`CUSTOMER_FINALIZED` **also** legitimately needs the owner — the transition table
(`schemas.py:578-587`) exits it to `OWNER_APPROVED` / `OWNER_EDITED` / `OWNER_REJECTED`. So the
"Awaiting your approve" line is not pointing at the wrong thing; it is pointing at *half* the
thing. Both statuses are owner-blocked.

**What the owner has been reading every morning** (computed from the current 20-lead file:
`AWAITING_OWNER_APPROVAL` 3, `CUSTOMER_FINALIZED` 2, `SENT_TO_CUSTOMER` 3):

```
*Catering pipeline:*
  • New leads (24h): 0
  • Awaiting customer finalize: 3        ← the 3 leads blocked on HIM, attributed to the customer
  • Awaiting your approve (<2 codes>): 2
  • Quotes sent to customers: 3
  • Active pipeline total: 8
```

Three separate failures in five lines:

1. **Wrong blocker.** Three owner-blocked leads are labelled as waiting on the customer — the one
   attribution that reliably produces owner inaction. He was told to wait.
2. **Undercount.** "Awaiting your approve" should read **5**, not 2.
3. **No age anywhere.** No line carries a duration, so a 74-day-old lead is indistinguishable
   from one created yesterday. Even fixing (1) and (2) would surface "5" with no urgency signal.
4. **No codes for the mislabelled three.** Codes are rendered only for `finalized`, so `#4SX94`,
   `#8D9YG`, `#7GCQP` never appear — the owner is not given the token that F8
   (`hooks.py:1296-1319`) would act on deterministically.

### The pattern report does not compensate

`catering-pattern-report` counts `AWAITING_OWNER_APPROVAL` inside `ACTIVE_LEAD_STATUSES`
(line 77), and today's `catering-learning-summary.json` reports `active_missing_info_count: 2`.
That is a data-quality metric ("missing basics"), not an ageing or ownership metric. Nothing in
the learning block says a lead has been waiting on the owner for 74 days.

*(Its `menu_freshness_days: 108` independently corroborates the stale-menu finding recorded in
the reachability matrix, and is correctly surfaced.)*

### §9c: three layers, one fact, three different answers

The routing layer already knew. On 2026-07-24, cf-router wrote:

```json
{"type":"cf_router_intercepted","reason":"f7_fresh_inquiry_new_lead_over_stale",
 "detail":"new L0019 over stale L0018; fresh inquiry contradicts open lead identity; LLM bypassed"}
```

and told the customer *"I've also got your earlier inquiry L0018 on file — is this a separate
event?"*

So: **routing** classified L0018 as stale a month ago and worked around it; the **lifecycle**
layer that could retire it was disarmed; the **owner surface** reported it as waiting on the
customer. Each layer behaved defensibly in isolation. Nothing carried the fact to the person who
could act. This is the §9c shape exactly — the visible lever (nobody told the owner) is not the
controlling one (the sweep is off), and a third layer had already diagnosed the condition without
any path to report it.

---

## 5. Severity control: these are the owner's own test leads

All three carry `customer_phone: "+17329837841"`. That number is **not a customer**:

```
$ identify-sender +17329837841
{"role": "employee", "roles": ["employee", "owner"], "name": "Srini Bangaru",
 "employee_id": "e008", "primary_role": "floor", ...}
```

It is also `owner.authorized_identities[0].phone` in `/opt/shift-agent/config.yaml`, is
`WHATSAPP_HOME_CHANNEL` in `/root/.hermes/config.yaml`, and appears in `roster.json`. This is the
dual owner/employee identity the cf-router membership checks were written for.

Corroborating that these are rehearsal traffic: all three have `customer_name: ""`,
`quote_version: 0`, `quote_total_usd: null`, `customer_replied: false` — no quote was ever
composed and no counterparty ever replied.

**So no real customer is waiting, and no revenue is at risk.** Both defects are real and both
should be fixed, but this is a monitoring-and-truthfulness failure caught in rehearsal, not a
live incident. It should be reported that way.

It does mean the assignment's framing — "catering has been silently sitting on three unapproved
leads" — is true of the mechanism and not of the business.

---

## 6. Smallest fix, split by who owns it

### Fix B — code, ~5 lines, **outside catering**, no ruling needed on catering grounds

`src/agents/daily_brief/scripts/send-daily-brief`, `_render_catering`:

- count `AWAITING_OWNER_APPROVAL` **and** `CUSTOMER_FINALIZED` under "Awaiting your approve";
- render `owner_approval_code` for both sets;
- add the oldest-lead age to that line (e.g. `oldest 74d`);
- keep or drop "Awaiting customer finalize" as a genuine customer-blocked line only if a status
  that actually means that is identified — on the current state machine, none of the three
  statuses rendered here is customer-blocked in the way the label claims.

This touches `src/agents/daily_brief/` only: no catering code, no catering state, no routing. It
reads catering statuses, which the merged ruling permits. **This is the fix that restores
truthfulness, and it is independent of Fix A.**

Caution for whoever implements it: the HARD RULES in the docstring (no `$` totals, no customer
names — from the 2026-05-11 hallucination finding) must survive. Codes and counts only.

### Fix A — operator action, **needs the orchestrator's ruling** (mutates catering state)

Set `CATERING_LEAD_TTL_SWEEP_ENABLED=1` in `/root/.hermes/.env` (edit the symlink *target*, never
`sed -i` the link).

Before arming, run `catering-lead-ttl-sweep --dry-run` — it is explicitly non-mutating and works
while the flag is off, and it prints the exact blast radius. Expected: precisely
`would_expire L0017`, `L0018`, `L0019`.

Consequences to accept before ruling:

1. Three leads transition to `STALE`, which is **terminal**. The sweep *retires* them; it does
   not get them approved. Given they are rehearsal leads (§5) that is the right outcome, but it
   is a one-way door.
2. Three owner alerts fire (§12b write-site alerting). Per the standing house rule, use
   `parse_mode=None` if any alert body carries underscored identifiers.
3. It arms an ongoing behaviour, not a one-off cleanup — every future lead idle 21 days will be
   retired automatically. That is the intended contract, but it is a live-behaviour change on a
   frozen agent.
4. **Check before arming:** whether `STALE` is bucketed in `catering-pattern-report`. The
   already-tracked follow-up (`docs/reviews/p1-followups-open-2026-08-15.md` item 1) records that
   `EXPIRED` is bucketed nowhere. If `STALE` shares that gap, arming the sweep would move three
   leads *out* of the counted active set and into an uncounted one — trading a mislabelled
   number for a vanished one. I did not verify `STALE`'s bucketing; it is a prerequisite, not a
   blocker.

### Recommended order

**Fix B first, then re-evaluate A.** B is smaller, reversible, outside the frozen agent, and
fixes the thing that actually failed — the owner not being told. A is a policy change on a
protected agent whose main effect is to hide the same three leads in a terminal status. Doing A
first would make the brief read "Awaiting your approve: 2" with the three leads gone, which is
tidier and no more truthful.

---

## 7. Method and limits

- Read-only throughout: `ls`, `stat`, `cat`, `grep`, `systemctl cat`, `journalctl`, `zcat` over
  rotated archives, `python3 -c` reads, and one `identify-sender` lookup (a pure read that
  mutates nothing). **No `--dry-run` was executed** — it is documented as non-mutating, but it
  executes a script against live state on a protected agent, so the selection was derived from
  source instead. No state written, no service touched, no send-test.
- **Inferred, not witnessed:** that L0017 and L0018 emitted
  `catering_owner_approval_requested`. Their rows predate the 30-day retention floor. The
  inference rests on the same-instant emission pattern observed for L0019/L0020 plus the leads'
  populated `owner_approval_code` (§1).
- **Not verified:** whether `STALE` is bucketed in `catering-pattern-report` (§6 Fix A item 4).
  Flagged as a prerequisite for arming, not resolved here.
- **Not investigated:** why the operator never armed either sweep flag. Both units ship dormant
  by an explicit house rollout convention, so "never armed" may be deliberate deferral rather
  than oversight — the record does not say, and that question belongs to the operator.
- The 2026-07-24 cf-router rows quoted in §4 are the only surviving direct evidence of L0018's
  behaviour; everything earlier has rotated away.
