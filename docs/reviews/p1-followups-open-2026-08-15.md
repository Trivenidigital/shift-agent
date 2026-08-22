# P1 open follow-ups — recorded, deliberately NOT fixed in-branch

> **Tracked in-repo as of 2026-08-22.** This ledger existed only as an untracked
> local file, which meant the open BLOCKER/HIGH items below were one lost working
> copy away from disappearing. Committing it is the point: a follow-up that lives
> only in a reviewer's scratch directory is not recorded, it is forgotten on a
> delay. Items are struck through as they close, with the closing commit named.

Session 2026-08-15. Each was found during P1 work, ruled OUT of the two P1
branches' charters, and left for a separate decision. Nothing here blocks
either P1 branch.

## Needs an operator decision (changes owner-visible numbers)

1. **`EXPIRED` is bucketed nowhere in `catering-pattern-report`.** It falls
   through every branch of the bucketing chain, so expired sets are invisible
   in the catering health counts the owner sees in the daily brief. One-line
   fix in code the P1 branch already touches, held back deliberately: it moves
   numbers currently in the brief, and the operator should see that coming
   rather than discover it. Found by `p1-proposal-impl`.

2. **No dedicated `send_uncertain` counter in the learning summary.** Adding
   one means a field on `CateringLearningProposalHealth`, which the pinned
   rollback target validates with `extra="forbid"`; `send-daily-brief:439`
   really reads `catering-learning-summary.json` through
   `CateringLearningSummary.model_validate`, and `catering-state-downgrade`
   does NOT cover that store. Degradation would be graceful (section drops)
   but real. Proper fix needs migration coverage for a third store — its own
   ruling. Uncertain sends are meanwhile bucketed with `sent`, and nothing is
   hidden: every one pages the owner and writes a
   `catering_customer_send_unconfirmed` row with `delivery_certainty="uncertain"`.

## Engineering follow-ups

3. **Cross-suite state pollution at merged main `830a808`** — three cells in
   `tests/test_catering_studio_e2e.py`
   (`test_11_customer_stop_acks_once_then_suppresses_everything`,
   `test_15_kill_switch_drops_every_send`,
   `test_17_audit_timeline_tells_the_whole_story_without_duplicates`) fail when
   run after sibling catering suites and pass when run alone. **Proven
   pre-existing by baseline diff**, not asserted: a throwaway worktree at
   `830a808` ran the identical 11-suite combination and produced the SAME 3
   failures, with the suite green alone on both trees. Something in the
   siblings leaks state those STOP / kill-switch / audit-timeline cells depend
   on.

4. **`has_later_sent` slow-send race** (`_mark_sent_and_supersede`) tests
   `row.status == "SENT"` only, so in the race where an older set's send
   completes after a newer set went out uncertain, the older set still becomes
   SENT. Net effect is fail-closed — `_latest_proposal_for_lead` picks the
   max-sequence uncertain set and refuses — and the owner was already paged.
   Pre-existing shape, not worsened by P1.

5. **From the p17b review (`docs/reviews/p17b-send-status-review-2026-08-15.md`),
   still open:** hard-coded `outcome` literal in `select-catering-proposal:290`
   / `send-catering-ack:96`; the chokepoint detector's one-directional
   assertion; no throttle/dedup on the new priority-1 pages (sharpest instance:
   an operator-initiated `shift-agent-disable` now generates an extra
   unthrottled page per catering send on top of safe_io's own throttled drop
   page); `owner_paged` truthfulness pin-tested in only 1 of 4 paging scripts.

6. ~~**`build-deploy-tarball.sh` still prints a stale usage hint** telling the
   operator to run the INSTALLED `/usr/local/bin/shift-agent-deploy.sh`.~~
   **CLOSED 2026-08-22.** The instance was reported as one printed hint; it was
   four sites — the printed hint, a source comment, a generated fleet-upgrade
   runbook line, and `tools/canary-bulk-deploy.sh`, which did not advise the
   installed path but *executed* it across every fleet VPS. All four now prefer
   the staging entrypoint, mirroring the rule `shift-agent-deploy.sh` already
   applies to its own pre-restart gates. Pinned by
   `tests/test_deploy_entrypoint_prefers_staging.py`, which fails on any bare
   mention of the installed path without fallback framing.

## Highest-value remaining artifact — pin Literal-widening in KNOWN tags

`test_12b` (money branch) pins that every LogEntry tag added since `dc7a81a2`
degrades to `_UnknownLogEntry` — 27 tags today, anchored on the three P1
deposit tags so an emptied delta fails loudly. **It does NOT cover the
widening of a Literal inside a tag the old release already knows**, and that
is the rollback failure mode this project has actually hit — twice in this
session: `CateringDepositLinkFailed.reason` (money branch) and
`CateringProposalGenerationFailed.reason` (proposal branch). Both were caught
by hand, by a different agent each time. A widened value in a known tag makes
the old reader REJECT a row it accepts today — strictly worse than an unknown
tag — and nothing pins it.

Artifact wanted: for every tag present in BOTH releases, assert no Literal
field has gained values the old release lacks.

**Lead ruling on its shape (2026-08-15): STRICT, with an explicit
acknowledgement list — not writer-aware.** Writer-awareness would require
statically proving which values a new writer can emit; every imprecision in
that analysis fails OPEN, which is the harmful direction. Strict fails closed
at a bounded, visible cost (a false positive costs a conversation; a false
negative costs a rollback). The escape hatch is a named constant: a widened
Literal fails the pin until someone adds tag+field+value with a one-line
reason, which turns an invisible act into a reviewable one — the same device
`PROPOSAL_STATUS_DOWNGRADE` provides for statuses, and it doubles as the
record of every widening judged safe. **Document in the test that audit rows
have NO downgrade path** (`decisions.log` is append-only and never migrated,
unlike the proposals store), so the correct remedy for a needed new value is
almost always a NEW TAG. A long exceptions list is a smell.

**Second artifact, approved — mechanize the escape-hatch diagnostic.** Any
refusal/alert path whose operator copy names a command should assert that
command EXISTS and accepts the flags named. The money branch pins this for one
page by string-matching; generalizing it across catering's operator-facing
copy would catch "escape hatch documented but not wired" mechanically instead
of relying on a reviewer noticing. Known limit to state in its docstring:
matching copy against an argparse surface proves the command and flags exist,
NOT that the disposition resolves the state — that half still needs a
behavioral test. Two complementary pins, neither sufficient alone.

**Scope: LogEntry variants ONLY — and a THIRD, wholly uncovered category was
found while ruling that scope (lead-verified).** `plan_downgrade`
(`catering-state-downgrade:200-232`) reads `new_record.get("status")` — the
**top-level `status` key only**. Therefore:

| category | rollback coverage |
|---|---|
| Top-level `status` on leads / proposal sets | Covered: `LEAD_STATUS_DOWNGRADE` + `test_02c`, `PROPOSAL_STATUS_DOWNGRADE` + `test_02d` |
| LogEntry tags added since `dc7a81a2` | Covered by `test_12b` (anchor half live; see limit above) |
| LogEntry Literal widened inside a KNOWN tag | **NOT covered** — the artifact above |
| **Non-`status` Literal fields on state models** (`deposit_status`, `deposit_link_delivery_status`, `card_delivery_status`, `quote_delivery_status`, `customer_ack_status`) | **COVERED BY NOTHING** |

The last row is neither remappable (not the top-level `status`) nor strippable
(field-strip removes fields the old release does not KNOW, not a known field
carrying an unknown VALUE), so a widened value there survives to the old reader
and is rejected. This is why the earlier P1 investigation warned against
touching `deposit_status` and steered the deposit fix onto the already-wired
`deposit_link_delivery_status` field — that instinct was right, and this is the
reason. Remedy is a separate design question (per-field maps in
`plan_downgrade`, or a documented rule that those fields are append-only in
practice); deliberately NOT bundled into the test-only artifact.

Placement: both belong on ONE follow-up branch, not on the reviewed P1
branches — adding test-only scope to a completed, reviewed branch restarts its
review clock for no safety gain.

**Known limitation of `test_12b` itself, recorded so nobody over-trusts it.**
Its author mutation-tested it and reported, unprompted, that only ONE of its
two assertions can fail against this baseline. `dc7a81a2`'s
`_pick_log_entry_tag` routes anything outside its known set to
`_UnknownLogEntry` (`type: str`, `extra="allow"`) and the union discriminator
binds at class-creation, so the DEGRADATION assertion is unfalsifiable against
this baseline without rewriting the old union (i.e. mutating the test, not its
input). The ANCHOR assertion is the half that is live today; the degradation
half is forward-looking, for a future rollback baseline whose fallback differs
or a tag that becomes partially known. Do not read `test_12b` as two
independently-proven guarantees.

## Pattern worth naming (both P1 branches produced it independently)

**A guarded state ships with its escape hatch documented but not wired.** The
deposit branch built the uncertain guard and an owner page instructing "void
the intent, then re-mint" — which still refused. The proposal branch declared
a `SEND_UNCERTAIN -> SUPERSEDED` out-edge as the operator's resolution — which
no writer took. Same shape, two agents, one session. The diagnostic question
that catches it: after verifying *"no automation can take this edge"*, always
ask *"then who CAN?"* — and require a test that exercises the answer.
