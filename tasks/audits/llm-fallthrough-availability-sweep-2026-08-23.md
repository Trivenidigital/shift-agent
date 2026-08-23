# LLM-fallthrough availability sweep — 2026-08-23

**Drift-check tag:** `Hermes-native` — an audit record plus one classification
table. No runtime code, schema, skill or config is introduced by this document;
the single code change it accompanies is the owner-approval reachability fix in
the same PR.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deterministic routing when the model service is unavailable | none found — Hermes' own recovery path IS the model; the disabled-toolset posture on this box means a SKILL cannot execute at all | keep the deterministic answer in the already-deployed Shift/Catering scripts and make cf-router reach them (no new code paths) |

awesome-hermes-agent ecosystem check: no ecosystem skill provides
"answer deterministically when the LLM is down" — by construction that is the
opposite of what a skill is. Verdict: **no net-new engineering; reachability
only.**

---

## Why this sweep exists

OpenRouter is currently exhausted (balance **−$0.18**, watchdog alerting). That
does not invalidate the deterministic catering happy path, which was proven
green end-to-end against copied state with no model involved. It does mean any
branch whose recovery *depends* on LLM fallthrough is **operationally
unavailable right now**.

The pattern searched:

```
deterministic branch returns None / non-handled
      -> comment says "let LLM handle / recover / explain"
      -> can this state arise in production?
      -> if yes, what happens when the model service is unavailable?
```

**Classification, not conversion.** The LLM is genuinely the correct owner of
some of these. Converting all of them would be the mirror of the defect just
fixed: replacing a judgement task with a canned string.

## The class

| # | Site | Reachable in prod? | Is the LLM the correct owner? | With the model down | Verdict |
|---|---|---|---|---|---|
| 1 | `actions.py` `invoke_apply_owner_decision` path 3 — catering approve, no quote source | **Yes — L0017/L0018/L0019 hold exactly this shape** | **No.** The answer is a fixed policy ("wait for the customer to finalize, or override via cockpit") that the apply-script already owns | Owner got nothing at all | **FIXED in this PR** |
| 2 | `hooks.py` catering `has_edit` | Yes | **Yes.** Extracting "make it 45 guests, add paneer tikka" from free text is genuine NL work with no deterministic equivalent | Owner's edit silently does nothing | Correct owner; **operationally unavailable** while credit is out. Recorded, not changed |
| 3 | `hooks.py` code matched, no clear verb (bare `#XXXXX` on a catering lead) | Yes — though the owner card shows the verb forms, so it is off the primary path | **Partly.** A deterministic "reply approve / reject / edit" hint would serve the common case; genuine ambiguity still wants the model | Owner gets nothing | **Candidate** for a deterministic hint. Deliberately not done here — out of the authorised axis |
| 4 | `hooks.py` code matched no open pool (stale/expired code) | Yes | **Partly.** "That code is no longer active" is deterministic | Owner gets nothing | **Candidate.** Same scoping note as #3 |
| 5 | `hooks.py:1326` plugin error → always return None | Yes | **Yes.** Fail-open is the safety property: a plugin bug must not swallow the turn | Degrades to normal LLM handling | **Correct as-is.** Do not convert |
| 6 | `_build_skip_or_passthrough` generic non-zero rc | Yes | **Mixed** — depends entirely on which rc | Varies by rc | Partially addressed (rc=11 for the catering non-finalized case). The remaining rcs are per-code work, not a single sweep |

### What #3 and #4 have in common, and why they were left alone
Both are cheap to make deterministic and both are outside the one axis this
change was authorised to reopen. They are recorded here so the next person
finds them as a named pair rather than rediscovering the class. Neither moves
money, neither changes state, and both currently fail in the safe direction
(the owner is not told, rather than the wrong thing happening).

---

## External / operator dependency — NOT an engineering fix

**OpenRouter balance is −$0.18.** Recorded as an operator dependency. Nothing in
this PR tops it up, and no code was changed to work around it. Two independent
facts, both verified:

- The deterministic catering lifecycle (inquiry → options → selection →
  `CUSTOMER_FINALIZED` → owner approval → `SENT_TO_CUSTOMER`) needs **no** model
  and is green.
- Row #2 above is unavailable until credit is restored.

Do not read "the happy path is green" as "the outage does not matter", and do
not read "the outage matters" as "catering is down". Both statements are true
and they are about different paths.

## Related items kept visible, deliberately NOT changed here

- **Quote scaling (`qty=1`, $51 for 50 guests).** Unchanged in this branch. The
  owner-card warning ("⚠ $51 is only $1.02/guest … Edit before approving") is
  the designed compensating control and is **armed in production** — the live
  config omits `min_per_guest_usd`, and the schema default is `3.0`, so the
  guard evaluates. Pricing policy follow-up, already noted in
  `tasks/audits/improvement-backlog-2026-07-10.md` and
  `tasks/audits/owner-experience-review-2026-07-10.md`.
- **Reject/edit silence to the customer.** `reject`/`edit` send nothing
  customer-facing by design. A customer told "we'll be in touch shortly with
  final pricing" is then never told no. This is current policy, not the
  owner-approval defect, and needs a separate product ruling. Not previously
  recorded anywhere — recorded here for the first time.

## No-go state unchanged by this work

`deposit_pct: 0` · `catering-followup-sweep.timer` disabled · TTL sweep not
armed · no live WhatsApp test · transport-evidence harness not executed.
