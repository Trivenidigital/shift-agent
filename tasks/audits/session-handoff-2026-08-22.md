# Session handoff — 2026-08-22

**Production SHA: `6a1f128fe2abc9a9edd265b3111ad8eab6b7720f`** — `origin/main` and
the deployed box are identical. No deploy debt. Gateway, cockpit and bridge
healthy.

27 PRs merged (#723-#749), five production deploys, all runtime-verified.

**Drift-check tag:** `Hermes-native` — this document records completed work. It
adds no runtime code, no schema, no skill and no config; it is a Markdown audit
record under `tasks/audits/`.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Session/audit record-keeping | none found — Hermes has no audit-document primitive; this repo's own convention is a Markdown file under `tasks/audits/` alongside `deploy-authorization-*.md` | use the existing repo convention (no code) |

awesome-hermes-agent ecosystem check: no ecosystem skill authors project
handoff documents, and one would be the wrong shape regardless — the content is
this repo's runtime evidence, not a reusable capability. Verdict: **no net-new
engineering; documentation only.**


---

## 1. Deploys, and what each was proved by

| Tag | SHA | Verified by |
|---|---|---|
| `deploy-20260818-005157` | `40064b1a` | brand-asset routing P0; 13/13 behavioural probe on the live plugin |
| `deploy-20260822-201150` | `6809bd07` | `register()` on the deployed tree returns 5 tools; the tool derives **5** open owner decisions from the live store |
| `deploy-20260822-205012` | `24c1f1d5` | **live positive control** — tick 1 gave exactly 2 escalations + 2 owner alerts `sent`, tick 2 gave zero |
| `deploy-20260822-222919` | `44b27021` | drop-in installer matched its pre-deploy SHA prediction in both directions; both preflights logged pass |
| `deploy-20260822-224523` | `6a1f128f` | candidate-response leg live; both preflights pass; bridge connected |

The strongest single piece of evidence in the session is the third: **F0217 and
F0222 — two real customer projects invisibly parked for 42 and 41 days — were
surfaced to the owner for the first time**, and the second tick added nothing,
proving both the escalation and the no-repeat property on production data.

## 2. Agent matrix

Authoritative counts, from `agent-reachability-matrix-2026-08-22.md`:

**PRODUCTION_READY 2 · DEPLOYED_AWAITING_LIVE_E2E 2 · PARTIAL 4 ·
BLOCKED_UNSUPPORTED_INTEGRATION 1 · NOT_REACHABLE 9 · NOT_IMPLEMENTED 2**

**The counts did not move.** Four rows gained capability today — #1, #2, #4 and
Flyer — and none was promoted. That is the matrix measuring outcomes rather than
activity, and it is the honest result.

The governing rule: a SKILL.md is not an execution path. The `skills` toolset is
disabled on this box, so an agent is reachable only via a cf-router hook, a
registered `shift_agent_read` tool, or a systemd timer.

### The vocabulary has a gap, and it needs a ruling
`DEPLOYED_AWAITING_LIVE_E2E` currently means two opposite things:

- **#2 Catering, #21 Expense** — never had a live end-to-end run. Proof unstarted, risk unknown.
- **Flyer** — 145 completed projects, then stopped completing. Proof existed and **lapsed**.

The second is a regression, and it is the worse of the two: something that worked
and quietly stopped is stronger evidence of an active fault than something never
exercised. A `REGRESSED` status would fix it, but the vocabulary was specified
with "keep it strict" — an agent widening it means no future reader can tell
which statuses were agreed and which were invented. Recorded, not taken.

## 3. Flyer verdict — NOT `FLYER_STUDIO_FULL_PRODUCTION_READY`

Two of four gaps are closed and deployed with live proof. The verdict stands
because **the instrumentation now works and the outcomes have not changed**:
three projects are still parked, and the pipeline has **completed nothing in a
month** (created May 122 → Aug 1; completed May 7 → Aug 0; the last completions
were five projects inside four seconds on 2026-07-20 — a batch, not delivery).

**One real request has entered the pipeline in 22 days. It failed into the manual
queue and is still there.**

Promotion criteria are data thresholds, not calendar: inbound volume answered;
at least 3 real organic completions after `24c1f1d5`; the three parked projects
dispositioned; an escalation contract ruled. The first is the actual gate and
**neither engineering nor the operator can close it by deciding** — it needs
inbound cf-router traffic data nobody has sampled. Caveat kept: n=1, and creation
counts alone cannot separate no-demand from pipeline-degraded.

## 4. Catering — closed, and now more visible than it was

No catering write path, schema or routing was changed. Two read-only surfaces
were added and one lie was removed:

- `get_pending_catering_approvals` — deployed, owner-only.
- The daily brief no longer prints leads that wait on the **owner** under
  "Awaiting customer finalize". He now sees **5 open decisions**, their codes and
  the oldest age (74d), where before he saw "2".
- An `OWNER_EDITED` lead was invisible in every line and in the pipeline total,
  and the TTL sweep cannot retire it. It now appears on its own count-only line —
  **without** a code, because the executor refuses approve, reject and edit from
  that state and offering one would be a false instruction.

Regression protection: the deterministic half is gated; the LLM-in-the-loop
conversation E2E is exempted with a **self-invalidating** reason (it asserts the
file really skips on a missing `OPENROUTER_API_KEY`, so removing the env gate
breaks the exemption).

## 5. Requires an operator (product/business) decision

1. **Three agents are one config block away from working.** #3 Multi-Location,
   #13 Compliance, #19 Equipment are built, deployed and returning
   "not configured". Verified: all three config blocks and both data files are
   absent. Each has an installed writer waiting. **Zero engineering.**
2. **Five catering decisions are waiting**, oldest 74 days. They are rehearsal
   leads — no real customer is waiting and no revenue is at risk — but the
   mechanism failure was real and the brief now shows them.
3. **`catering-followup-sweep.timer` is disabled.** Enabling it sends real
   customer outbound. Nothing records whether the disabled state was deliberate.
4. **`CATERING_LEAD_TTL_SWEEP_ENABLED` is unset.** `STALE` is terminal and
   one-way, and it arms ongoing behaviour, not a one-off cleanup. Unverified
   prerequisite: whether `STALE` is bucketed in `catering-pattern-report` at all.
5. **Escalation policy for two watchdogs that are wrong in opposite directions**
   — recovery pages once ever; source-edit SLA pages hourly forever, page #170
   byte-identical to page #1, 74ms apart on the same project. One policy, not two.
6. **`flyer-recovery-watchdog` runs as `root`** on the box (codex-authored
   drop-in, 2026-05-24) while the repo unit says `shift-agent`. Verified live.
   Left in place; adopting root is a privilege decision, not config tracking.
7. **SQLite 3.50.4 in the Hermes venv is vulnerable to the WAL-reset corruption
   bug**, warned every gateway start for `state.db` (which holds
   **`delivery_ledger`**) and `kanban.db`. The named remedy is `hermes update` —
   the operation this project pinned Hermes to avoid after a prior bump broke
   WhatsApp. Needs a plan, not a command.
8. **A customer asking "what is on your catering menu?" gets a lead minted
   instead of an answer.** The verb "cater" does not match the classifier, but
   the noun does. Product decision.
9. **CI cannot cover the catering LLM conversation gate** without a funded
   OpenRouter key.

## 6. Known-open, non-blocking

- **#1 Shift is complete in code but has never executed.** Both legs are now
  wired and deployed; intake has never fired in production (zero
  `dispatcher_routed` rows all-time, and cf-router writes that row on the
  sick-call path itself). Whatever gates intake is upstream and unexamined.
- Non-English candidate replies classify as ambiguous — safe direction, and once
  the owner page landed it degrades gracefully: the sweep still fires the
  mis-attributed page, so the owner is still told. English-only is narrower, not
  a hole.
- `ApprovalCodeCollisionDetected.pools` is `max_length=4` against **five** pools,
  on the working cross-pool path. One line to fix, but the old reader **rejects**
  rather than degrades, so it needs a two-phase reader-first deploy. Parked
  deliberately; it is not a quick win.
- `approve-catering-followup` bypasses the code registry via first-match-wins.
- `/opt/shift-agent/DEPLOY_RECEIPT.json` is 78 days stale while
  `/opt/shift-agent/deploys/` is current — a confidently wrong artifact at the
  name an auditor reaches for first.
- `20-drain-timeout.conf` differs from the repo by line endings only; the deploy
  reports it every run and correctly refuses to overwrite.
- 12 `sys.stderr.write` calls in cf-router land in a file no runbook names.

## 7. Methodology — the finding that outlived the defects

One mechanism produced most of this session's bugs, in our own work as much as in
the code we inherited:

> **We keep verifying the thing we thought to check, and treating the unchecked
> remainder as sound. Finding a flaw in a decision is not the same as re-opening
> the decision.**

What makes it hard to catch from inside: **in every instance the verification
that did happen made stopping feel earned.** The checkable form is not "did I
verify?" but **"what did I decide not to look at, and why was that safe?"**

Three supporting rules, each earned:

- **A stated invariant sitting next to code that violates it** is greppable in
  review; "read more carefully" is not. Both the qualified-consent bypass (#734)
  and the reply classifier (#744) were documented correctly and implemented
  wrongly, adjacently.
- **Copy the whole record; never assemble a fixture from the fields you think
  matter.** Five measurements were invalidated this way — the omitted field is
  chosen by the same mental model that wrote the code, so it omits exactly what
  the code fails on. Corollary: if the property is "across cycles", the test must
  produce the cycles, not hand-stamp the end state.
- **"It is logged" is not a location.** A traceability claim that does not name
  where the operator looks is unfalsifiable, and here it was also wrong — our own
  runbooks say `journalctl -u hermes-gateway`, and the gateway does not write
  there.

A fourth, from a near-miss: **a text-slice delete needs a bound assertion** — a
patch assuming two functions were adjacent would have removed ~7,200 lines.

## 8. Errors I made, and what caught them

Recorded because the pattern matters more than the individual fixes.

- **Two of my own PRs were blocked in review, and the second was worse than the
  bug it fixed.** `[ -x ]` on a file tracked `100644` silently selected the
  previous release's deploy script; my first fix (`bash "$S"`) set `$0` to a
  non-executable path, which would have killed **auto-rollback** on exactly the
  deploy that already failed a gate. The correct fix was `git update-index
  --chmod=+x`. I also had to **invert** my own test — it pinned the wrong premise
  and would have blocked the real fix.
- **My first test for that fix was trivially satisfiable**: demoting the staging
  line to a comment while restoring the bare installed invocation passed it.
- **My governance heuristic blocked another lane's test file** — a new
  `tests/test_*.py` read as "a new subsystem", and the only way past was to
  assert something false. Fixed at the class in #745 (`_is_test_only_path`).
- **My brief for #744 caused its own BLOCKER**: I said the sweep's guard must
  fire and never said what must replace the page it suppresses, so the PR deleted
  the only real-time uncovered-shift alert. My review lens for the same PR also
  pointed the wrong way — I warned about polite declines reading as accepts; the
  live bug was affirmative-prefixed **refusals** reading as irreversible accepts.
- **Three reporting errors, all caught by lanes rather than by me**: an `ls | head`
  truncation that made a present SKILL.md look like an empty directory; reading
  timer idempotency (`already_sent`) as a broken agent; and telling a lane "three
  leads" when the state machine says **five** — a 40% under-report that would
  have propagated into shipped copy.

None of these reached production. All five classes were caught by an independent
reader with a different lens, which is the argument for the lane structure.

## 9. Merged PR ledger (#723-#749)

| # | Title |
|---|---|
| #723 | fix(governance): a filled-in Reuse Map label is not an answer |
| #724 | ci(flyer): a gate must verify on push what it verifies on a PR |
| #725 | fix(deploy): the entrypoint an operator runs must come from staging |
| #726 | ci: gate identity — per-commit on push, per-file in what it wakes, and nothing unrun |
| #727 | docs(portal): three of five LIVE claims had no execution path |
| #728 | docs(audit): runtime-reconciled agent reachability matrix |
| #729 | fix(flyer): page the owner when a project parks past its status TTL |
| #730 | fix(flyer): install the module the deployed TTL-0 CLI imports |
| #731 | test(flyer): enforce the panic switch's "lockstep" comment |
| #732 | feat(shift-agent-read): owner catering-approval + owner roster-capability read tools |
| #733 | docs(audit): why nobody was told about the 74-day-old catering leads |
| #734 | fix(shift): an owner approving a coverage code reaches the Shift kernel |
| #735 | fix(daily-brief): name the OWNER as blocker on leads that wait on him |
| #736 | test(flyer): the panic switch must also ROUTE to the same renderer |
| #737 | fix(ci): a missing git must not read as a missing test file |
| #738 | docs(deploy): record 6809bd07 + correct the hint my own chmod falsified |
| #739 | ci(governance): the gate that reads the PR body must wake when it changes |
| #740 | docs(deploy): record 24c1f1d5 and its live positive control |
| #741 | docs(audit): matrix refreshed against the deployed box, with the operator/engineering/external split |
| #742 | fix(pools): a code matching two rows in one pool must fail closed too |
| #743 | docs(audit): one label is covering two opposite conditions |
| #744 | fix(shift): record the candidate's YES/NO so she is not reported unresponsive |
| #745 | fix(governance): a test file is not a new subsystem |
| #746 | feat(shift): prove the read tools registered, without blocking the gateway |
| #747 | docs(audit): §8 looked in journald; the gateway does not write there |
| #748 | fix(deploy): the drop-in that wires the screening gate must be reproducible |
| #749 | docs(deploy): record 44b27021 and the SQLite warning it surfaced |

Shape of the work: 12 fixes to code that was already deployed and wrong, 6 audit
records, 4 CI/governance gates, 3 tests pinning an existing invariant, 2 new
capabilities. **Only two of twenty-seven added a feature.** The rest closed gaps
between what the repo claimed and what the box did.
