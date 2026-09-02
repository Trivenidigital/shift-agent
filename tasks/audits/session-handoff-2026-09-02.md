# Session handoff — 2026-09-02

**Drift-check tag:** `Hermes-native` — a handoff record; no runtime code,
schema, skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Session handoff record | none found — repo convention is `tasks/audits/session-handoff-<date>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

## CURRENT TRUTH

| | |
|---|---|
| `main` | `385a6ee5` |
| shift-agent deployed | `a98431f5` |
| cockpit released | `a98431f5` |
| open PRs | 0 |
| `hermes-gateway` / `shift-agent-cockpit` | active / active |
| failed systemd units | **0** |

## P0 — CLOSED. The live step-up bypass is fixed in production

The deployed cockpit ran `sensitive = settings.sensitive_config_fields &
body.fields.keys()` with `jwt_ttl_hours = 24`. Any session with a valid `hjwt`
cookie up to 24h old — no freshness, no OTP — could `PATCH /config {"fields":
{"owner": {...}}}`. `"owner"` does not intersect `"owner.phone"`, so the
step-up was never demanded and the whole owner block was replaced.
`is_owner_chat()` fast-paths on `owner.self_chat_jid` with no roster
cross-check, so that handed WhatsApp-side owner authority to an
attacker-controlled chat id.

Verified independently after release, by reading the deployed file rather than
trusting the release script: `config.py:79` now runs
`_sensitive_touched(body.fields.keys())`, and all four owner identity fields
are in the live sensitive set.

### Why the cockpit was really three months stale

Three independent failures, none visible to CI, **each sufficient alone**:

1. `rsync: command not found` — the script assumed a Linux/Mac dev box.
2. An apostrophe in a comment at `deploy.sh:40`, inside the single-quoted ssh
   block, since commit `5df57702` (2026-04-27). `bash -n` fails — **the script
   could not parse, let alone run.**
3. `privileged_identity.py` was never installed.

That fully explains the hand-maintained drift: `backend.old/`,
`backend.backup-pr166-*/`, a hand-edited `config.py`, and the CRLF logrotate
config were all symptoms of one unrunnable path.

### The release transaction earned itself on its first run

It refused to cut over:

```
ModuleNotFoundError: No module named 'privileged_identity'
VALIDATION FAILED — live tree untouched, staged copy removed.
```

Without staging, the cockpit would have raised ImportError on startup —
taking down the operator's control surface **during a security deploy**.

The guard that exists for exactly this class
(`test_deploy_platform_install_completeness.py`, written after an identical
2026-07-21 incident) did not fire. Proven three ways:

| run | result |
|---|---|
| fix + widened scope | 10 passed |
| defect + widened scope | 1 failed |
| **defect + old scope** | **10 passed** |

It was **blind, not broken** — its scan roots never included `web/`.

Verification refused `/health 200` as evidence: eleven assertions against the
live tree, including two paired controls (`owner.name` and `customer.name`
still patchable) that a blanket-refusing gate would fail. A defect in the gate
itself was caught before first use — `$?` after a pipeline reports `sed`, not
python, so it could never have failed.

## THE MATRIX — 18 agents, each counted exactly once

The prior audit said 18 while its counts totalled 20: `multi_location` is one
directory with two `SKILL.md` files and was split into two rows, while
`catering` (8 skills), `shift` (5) and `flyer` (3) each got one. Rule now
consistent: **one directory = one agent**.

| agent | reachable | deployed | runtime verified | organic E2E | status | top blocker | next action |
|---|---|---|---|---|---|---|---|
| daily_brief | Y (timer) | Y | Y — real send 2026-09-01 | **N** | DEPLOYED_AWAITING_ORGANIC_E2E | recipient is the rehearsal owner | point at a real customer |
| eod_reconcile | Y (timer) | Y | Y — clean since 08-26 | **N** | DEPLOYED_AWAITING_ORGANIC_E2E | same | same |
| shift | Y (cf-router + read tools) | Y | Y | **N** | DEPLOYED_AWAITING_ORGANIC_E2E | no real employee traffic | await a genuine sick-call |
| equipment_maintenance | **Y — preflight check D, 2026-09-02** | Y | tool registered + armed | **N** | DEPLOYED_AWAITING_ORGANIC_E2E | zero invocations ever | seed + one real query |
| compliance | **Y — preflight check D, 2026-09-02** | Y | 2 real tool calls 2026-08-08 | N | BLOCKED_ON_REAL_DATA/CONFIG | `cfg.compliance` absent; items file absent | operator sets flag + seeds REAL dates |
| multi_location | Y (`customer_location_query`) | Y | code-complete | N | DEPLOYED_AWAITING_APPLICABLE_DATA | single-location customer | await a real second location |
| catering | Y (cf-router F7) | Y | Y — menu-apply works (v2→v3, 77 items); price-sync correctly fail-closed | NOT_DETERMINED | PARTIAL | `deposit_pct=0` (deliberate) + **no pricebook has ever existed on this box** → 2 of 3 critical-path capabilities never exercised, none defective | activate a pricebook + nonzero `deposit_pct` deliberately, then observe one full cycle |
| flyer | Y (cf-router) | Y | partial | N | PARTIAL | open P0/P1s + one stuck project | flyer backlog |
| expense_bookkeeper | intake Y | Y | partial | N | PARTIAL | QBO push raises `NotImplementedError` | build `RealQBOClient` (money-adjacent) |
| catering_followup | N | Y | — | N | NOT_IMPLEMENTED | trigger hook self-declared absent | wire the CLOSED transition |
| cash_ar, employee_docs, hiring, inventory, pnl_anomaly, sales_tax, supplier, vip | N | Y | — | N | NOT_REACHABLE (8) | **no `scripts/` directory exists** | write handlers first |

`0 + 4 + 1 + 1 + 3 + 1 + 8 = 18`.

**PRODUCTION_READY = 0.** `daily_brief`/`eod_reconcile` run and send cleanly,
but `config.yaml:13` reads `owner: name: "Srini (rehearsal owner)"` — both the
recipient and the summarised activity are the rehearsal identity.

> **No agent in this fleet has processed a genuine paying-customer event end to
> end.** Four real inbound WhatsApp messages in 30 days; 1,940 of 4,050 archive
> rows are cron noise.

## P1 — LID reachability: bounded, and the fix is upstream

**Corrected: 4 of 6 active employees lack a LID, not 5 of 6.** And **zero show
a demonstrated production failure** — `e001`–`e004` have never contacted the
bridge under any identifier in ~4 months of retained logs.

The pipeline is dead in two independent places:

- **Upstream (decisive):** the live `bridge.js` has **zero** occurrences of
  `_shiftWriteLidCache`/`lidCache`. Line 212 records the backfill was retired
  2026-08-01, *"proven never to have fired"* — a key-format mismatch meant it
  could never match. Removed, not disabled.
- **Downstream:** `lid-learn` has no cron, no unit, no log, zero audit rows
  ever, while a correct install template sits unused in-tree.

Fixing only the downstream achieves nothing — hand-run today it exits 5.

**Closed this session:** `patch-hermes.py` still generated the retired dead
code and `shift-agent-deploy.sh:3083` requires it in staging, so a future patch
cycle would have silently reintroduced it. Removed (#793).

**Open operator decision:** *how* the bridge should learn a phone↔LID pairing
now. That is a new design question, not a restoration — the prior approach
never worked.

## P3 — the dispatcher answer: don't build one

**A dispatcher fix unblocks 0 of the 8 NOT_REACHABLE agents.** Each contains
exactly `SKILL.md` + `__init__.py` with **no `scripts/` directory**, and their
specified Phase-0 behaviour *is* the self-decline. Their blocker is that their
handlers were never written.

The real gap is **two orphaned write kernels**:
`mark-compliance-item-done.py` and `apply-expense-decision` (the latter
live-adjacent — `cfg.expense_bookkeeper.enabled` is `true`).

The safe surface already exists and is proven in production —
`agent.log:4495-4503` shows `tool_search` → `tool_describe` →
`get_compliance_deadlines` completing with `terminal`, `skills` and `file` all
disabled. **Generic `terminal` remains disabled and no proposal changes that.**

Recommended first increment: a sibling plugin `shift-agent-act` exposing one
tool, `mark_compliance_item_done` — the only orphaned write that is
*recoverable*. It must be a sibling: `shift-agent-read`'s preflight requires
all its declared tools under one toolset. **Kill switch needs no new
machinery** — `/root/.hermes/config.yaml` is not repo-managed, so a new toolset
ships dormant until an operator adds one line.

## VERIFIED THIS SESSION, not carried forward

`shift-agent-read-preflight` run read-only on the box today:

```
ok A: plugin enabled and loaded
ok B: manifest declares 5 tools
ok C: all 5 registered under toolset shift_agent_read
ok D: toolset is in platform_toolsets.whatsapp and NOT disabled
```

Check D is the reachability proof for `compliance` and
`equipment_maintenance`, and independently confirms the two-layer kill switch.
The preflight is itself honest: *"registration proven, live discovery still
unproven."*

## DEFECT CLASSES — the reusable part

**"Correct in the repo" ≠ "reaches the box."** Three instances today, on top of
two yesterday: the cockpit logrotate config, the deploy artifact roots, and
`privileged_identity.py`. Each was invisible until something ran against the
box.

**A guard can be blind rather than broken.** The completeness guard passed
10/10 with the defect present, because its scan roots excluded `web/`. Scope is
part of a guard's correctness.

**Gates that cannot fail.** `$?` after a pipeline reports the last command. A
deploy gate that always passes is worse than none, because it makes the cutover
look verified.

**A script that has never been parsed has never been tested.** `deploy.sh`
failed `bash -n` for four months while everyone assumed it merely was not being
run.

## HOLDS — genuine operator decisions

1. **How the bridge learns phone↔LID pairings.** New design, not a
   restoration.
2. **`cfg.compliance.enabled` + real seed dates.** Cannot be fabricated.
3. **A genuinely multi-location customer** before `multi_location` can leave
   `DEPLOYED_AWAITING_APPLICABLE_DATA`.
4. **`ufw` is inactive.** Every cockpit finding is bounded by loopback binding
   alone, with no second layer.
5. **`apply-expense-decision`** — money plus a QBO push; its own PR and review.

## NEXT 24 HOURS, ordered by expected gain

1. Land `shift-agent-act` increment 1 (`mark_compliance_item_done`), shipped
   dormant.
2. Decide the bridge LID-learning design.
3. Investigate **zero `dispatcher_routed` rows in the entire retained archive**
   — from any source. Denominator or silent emit failure is NOT_DETERMINED;
   cf-router's emit sits in a bare `except Exception` writing only to stderr.
4. `ufw`.
5. Real organic traffic is the gate on every `DEPLOYED_AWAITING_ORGANIC_E2E`
   row — four inbound messages in 30 days is the true constraint on this
   fleet's readiness, not code.
