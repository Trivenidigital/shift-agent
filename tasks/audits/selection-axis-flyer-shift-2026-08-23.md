# Selection-axis measurement — Flyer and Shift, 2026-08-23

**Drift-check tag:** `Hermes-native` — a read-only measurement record. No runtime
code, schema, skill or config is introduced or changed by this work. Every
number below came from the deployed audit chain and the deployed plugin source.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Funnel / traffic analytics over the audit chain | none found — Hermes has no analytics primitive, and the audit chain is this repo's own NDJSON chokepoint | read the existing `decisions.log` + rotated archives directly; no code added |

awesome-hermes-agent ecosystem check: nothing in the ecosystem measures this
project's own funnel, and a generic analytics skill could not know which row
types imply an arrival here. Verdict: **no net-new engineering; measurement only.**

---

## Why this exists

The open question for Flyer was never implementation, it was the **denominator**:

```
completed nothing recently
      = no organic demand?
      OR inbound reaches cf-router but the pipeline fails?
      OR upstream traffic never reaches Flyer?
```

Creation count alone cannot separate those. This measures arrivals.

## What counts as an arrival, and why that definition is trustworthy

`actions.audit_raw_body()` is called from `_pre_gateway_dispatch_impl`
(`hooks.py:685`) **before any routing**, for every inbound that has text or
media, has a `chat_id`, is not `fromMe`, and is not a duplicate. It is therefore
an *arrivals* counter, not an *admissions* counter.

That distinction is the whole point. `cf_router_intercepted` only fires when the
plugin actually handles a turn — a turn that falls through to the LLM writes no
such row — so its absence would have measured nothing.

Window: the full retained audit chain, `decisions.log` plus 30 rotated archives,
**2026-07-23 → 2026-08-23** (4,090 rows).

## The funnel

| stage | count | last seen |
|---|---|---|
| **eligible inbound arrivals** (`cf_router_raw_body`) | **19** | 2026-08-18 |
| distinct sender identities | **2** | — |
| flyer intent classifications (`flyer_hermes_intent_decision`) | 15 | 2026-08-18 |
| cf-router admissions (`cf_router_intercepted`, all agents) | 21 | 2026-08-12 |
| flyer-specific admissions | 11 | 2026-08-12 |
| projects created | 122 May · 70 Jun · 33 Jul · **1 Aug** | 2026-08-01 |
| projects completed | 145 of 226 all-time | — |
| **arrivals since `24c1f1d5`** | **0** | — |
| **organic completions since `24c1f1d5`** | **0** | — |

### Who is actually talking
Two identities in thirty-one days. **18 of 19 arrivals are one identity**, which
is simultaneously a **roster employee identifier** (present in `roster.json`) and
the Stage-A allowlist candidate whose ownership was never confirmed. The
remaining single arrival (2026-08-18) is an unattributed identity; its flyer
classifier returned `classifier_status: skipped_passthrough`, so nothing
downstream ran.

The owner's own identity appears in **zero** arrivals — expected, because
`fromMe` returns before the arrivals counter, so owner self-chat is excluded by
construction.

## Verdict — Lane 1

**No organic demand.** Not pipeline failure, not upstream loss. Flyer stays
evidence-limited and **NOT** `FLYER_STUDIO_FULL_PRODUCTION_READY`; production
readiness awaits organic traffic that does not currently exist. No code was
changed to make Flyer look active.

### Alternate mechanisms, named and falsified
Per the standing rule, a decisive green (or in this case a decisive *empty*)
needs its rivals ruled out:

1. *"The instrumentation died, so arrivals stopped being recorded."* **Falsified** —
   the arrivals counter still fired on 2026-08-18, and other row types write
   every single day through 2026-08-23.
2. *"Traffic arrives but is never admitted, so it is invisible."* **Falsified by
   construction** — the counter is pre-routing, so it sees arrivals regardless of
   whether anything admits them.
3. *"Recent `front_brain_reply_composed` rows prove turns are still arriving."*
   **Falsified — and I had this wrong first.** All six are the same canned
   apology to the same chat, emitted by the `flyer-source-edit-sla-watchdog`
   timer. They are **outbound**, not arrivals. Counting them would have
   manufactured demand that does not exist.
4. *"Retention hid the traffic."* **Falsified** — 30 archives, unbroken
   2026-07-25 → 2026-08-23.

## New finding — a real customer is being apologised to indefinitely

`flyer-source-edit-sla-watchdog` runs on a ~5-minute timer. It has sent **the
same customer the same message six times over ten days** (08-14, 08-15, 08-16,
08-18, 08-22, 08-23, still firing):

> *"Quick update: This is taking longer than expected, and I'm sorry for the
> delay. Your flyer edit is still in progress…"*

The project is **F0226**, created 2026-08-01, status `manual_edit_required` —
stuck **22 days**. There is a throttle (roughly daily) but **no terminal bound**:
it will keep apologising for as long as the project sits there.

**Not fixed here, deliberately.** This is the same watchdog already named in the
open operator decision about escalation ladders (the one that also pages the
owner — 207 `flyer_source_edit_sla_alert` rows since 2026-07-24). What is new is
that the loop is **customer-visible**, not just owner-visible, which materially
sharpens that pending decision. Changing what a customer is told, and how often,
is a product ruling, not an autonomous repair.

## Smaller finding — completion timing is unreadable

145 projects carry status `completed`, but **no project in the store carries a
`completed_at`**. Completion *timing* therefore cannot be derived from the
project store at all; only status can. Any future claim about "when Flyer last
completed something" has to come from somewhere else, and the honest answer
today is that the store cannot say.

---

## Lane 2 — Shift intake upstream reachability

Traced read-only: `real inbound → bridge → pre_gateway_dispatch → sick-call
admission → handle-shift-sick-call`.

**Zero** shift-, sick-, dispatcher-, coverage- or proposal-shaped audit rows
exist in the entire retained history (the only near-matches are three
`catering_proposals_generated` and one `catering_proposal_selected`).

**The first point where evidence disappears is the arrival itself.** There has
never been a sick-call-shaped inbound. The single employee identity that does
message has only ever sent flyer- and catering-shaped turns (brand-asset saves,
reference edits, proposal requests). This is not a routing or config defect.

### The admission path itself is functional
Verified without sending anything: the deployed `_is_sick_call` predicate and its
five compiled patterns were executed directly from
`/root/.hermes/plugins/cf-router/hooks.py` against fixed strings —

- admits 5/5 sick-call phrasings ("calling out sick tomorrow", "I am ill and
  cannot work tonight", …)
- rejects 4/4 non-sick controls ("can you send me the flyer", "I am running 10
  minutes late", …)

So intake would fire on a qualifying message. It has simply never received one.

**Verdict:** #1 Shift stays `DEPLOYED_AWAITING_LIVE_E2E`. No synthetic WhatsApp
message was sent to manufacture coverage.

---

## Matrix impact

**None.** No row moves. Nothing about deployed reachability, runtime evidence,
external dependencies or vertical behaviour changed — what changed is that the
Flyer verdict is now backed by a measured denominator instead of an inference
from creation counts. That is a stronger reason for the same status, not a
different status.

## Holds observed

No live WhatsApp send · transport-evidence harness not executed · no timer
enabled or disabled · no OpenRouter credit added · watchdog runtime user
untouched · `hermes update` not run · no customer-facing policy changed.
