# P1 proposal-path adversarial review — `fix/catering-proposal-send-uncertain`

Reviewed at `36a6085` (5 commits on merged main `830a808`), 2026-08-15.
**Verdict: MERGE_READY, 0 BLOCKS_MERGE**, 4 FOLLOW_UP, 3 INFORMATIONAL.
Reviewer ran suites itself (Linux 157 passed; Windows rollback-compat 21
passed; +12 adjacent catering suites 257 passed / 8 skipped — no collateral)
and left the worktree byte-clean, doing every red-repro on a container copy.
It also correctly detected the mid-review commit and re-ran everything at the
new HEAD.

## Verified first-hand by the reviewer (not taken on trust)

- **The race red is real.** Deleting the `SEND_UNCERTAIN` line from
  `SUPERSEDING_PROPOSAL_STATUSES` in a container copy makes both race tests
  fail `assert 0 == 4` — rc 0 is the success path, i.e. the stale selection
  claimed, finalized and BOOKED with the owner card sent.
- **No over-blocking.** Before the fix the newer set is DRAFT for the whole
  mid-flight window and DRAFT was ALREADY in the superseding set; this closes
  only the sliver after the DRAFT→SEND_UNCERTAIN write. The one newly-refused
  case was written SEND_FAILED at baseline, for which the non-race path
  already returned None.
- **R1 automation half.** Complete inventory of every `proposal_set.status`
  writer plus all five installed timer units: only pattern-report (read-only)
  and the expiry sweep open the proposals store, and the sweep skips anything
  not exactly SENT. Nothing re-posts an uncertain set.
- **Status routing.** Executed the split against the FULL `bridge_post`
  vocabulary: `send_uncertain` → SEND_UNCERTAIN; every transport fault AND
  policy drop (refused/throttled/suppressed/disabled/"") → SEND_FAILED.
- **`test_02d` is not vacuous** — genuinely red at baseline, asserts both
  `missing=` and `stale=`, and that no target is SENT.
- **The audit row had zero runtime readers** — one writer, no consumers.
- **Old-reader argument holds**: `dc7a81a2` KNOWS
  `catering_proposal_generation_failed` and does NOT know
  `catering_customer_send_unconfirmed`.
- **R1 operator half**: the reviewer confirmed the "escape hatch documented but
  not wired" defect EXISTED on this branch at `803ccbe` and was self-corrected
  at `c8c0376` — verified by reverting and watching
  `test_a_new_set_retires_a_prior_uncertain_set` fail.

## Ruled IN-BRANCH by the lead (4 items)

1. **Page copy is FALSE on both recompose arms** (reviewer's top pick, and the
   only one it suggested fixing before merge). `_uncertain_page` is chosen on
   send status alone, but `_run_recompose` passes no `proposal_set_id`, so the
   clarify arm claims "the menu options were composed" when a clarifying
   QUESTION went out, and both arms promise "doing so also retires this set"
   with `proposal_set_id=(none)`. Suppression-in-`_fail_generation` was right
   for the ROW, wrong for set-specific COPY.
2. **The escape hatch names an action its reader cannot perform.** The page
   tells the OWNER to reissue, but generation is gated `sender_role != "owner"`
   and both entry points need a CUSTOMER inbound. Third instance of the same
   family, one layer over: not "the command dead-ends" but "the reader cannot
   invoke it."
3. **Stale registered runbook.** `docs/runbooks/catering-rollback.md`
   (registered at `project-registry.yaml:179`) still says the only proposals
   hazard is `expires_at` and lists only LEAD remaps. A runbook that
   under-describes a migration is how a 2am rollback goes wrong.
4. **`catering-pattern-report` bucketing is untested** — the branch changed
   that path with zero coverage.

## Second pass — `36a6085` proven inert, plus 3 narrative findings

The reviewer did NOT take the "docs-only" classification on trust. Four checks,
strongest last: exactly one unindented changed line (a blank line inside the
docstring); `ast.dump` identical after deleting every docstring node; exactly
one differing `str` constant in the whole tree (the docstring itself); and
compilation with `optimize=2` (which discards docstrings) followed by a full
recursive `dis` comparison — **7,799 instruction lines, byte-identical**.
Behaviorally inert; all earlier findings carry over unchanged.

Symbol hygiene clean: zero surviving references to the three renamed/deleted
symbols. Every load-bearing prose claim it spot-checked verified true (the
"three send sites" count, ASCII-only copy, the ack-body/cleared-id claims, the
Literal-not-widened argument, "SENT unreachable by construction").

Three new items, all ruled IN-BRANCH:

5. **A stale COUNT an operator sizes a rollback against.**
   `catering-state-downgrade` docstring line 6 says the MVP added "12 fields";
   `LEAD_FIELDS_UNKNOWN_TO_OLD` holds **20** (the 12 M1–M4 fields plus 8 P17
   send-status markers). Already stale at `830a808` — but `803ccbe` rewrote the
   SECOND HALF of that same sentence while leaving the stale first half. Same
   shape as the `outcome` field lying beside `reason`: the fix bounded by the
   question the author arrived with. `test_02` pins the LIST against the real
   schema delta, so the code stayed correct while the prose drifted, and
   nothing pins prose.
D. **Prose contradicting the branch's own thesis, 18 lines apart.**
   `schemas.py:2662` says an uncertain set "most likely reached the customer";
   `:2680` calls `has_later_sent` "a later-sequence set already went out" — but
   that predicate matches `SENT` only, excluding exactly the sets the first
   comment describes. Describes CURRENT behavior in words the thesis
   contradicts, which is how the next engineer resolves the asymmetry the wrong
   way.
E. **One sentence that hands a reader the wrong mental model.**
   `select-catering-proposal:522-525` invites deriving SUPERSEDING membership
   from the transition table's terminality. That derivation FAILS: three
   terminal statuses (SELECTED, SELECTED_OWNER_CARD_FAILED, SELECT_FAILED) are
   IN the superseding set. The governing rule is "could this set plausibly be
   what the customer is holding" — which is also the better justification for
   the SEND_FAILED/EXPIRED exclusions.

**Process note:** the reviewer detected the worktree going dirty mid-review
(uncommitted page-copy work) by crashing a probe on a function signature
present in no commit. Its MERGE_READY verdict covers `36a6085` ONLY and does
not extend to that unreviewed work — a targeted re-review follows the commit.

## Recorded, NOT in this branch

- **FOLLOW_UP 2 — nothing pins that an existing tag's Literal is never
  widened.** Independently found by the lead on the sibling branch; two
  independent discoveries of the same gap. Goes to the dedicated pin branch.
- **INFORMATIONAL A** — `has_later_sent` still SENT-only while the supersede
  loop now matches SEND_UNCERTAIN; contained (needs two concurrent generator
  runs for one lead), comment welcome.
- **INFORMATIONAL C** — `EXPIRED` falls into no bucket in
  `_build_learning_summary`, silently dropping expired sets from the owner's
  `sent` count. Pre-existing since M3; operator-visible numbers, so it is on
  the operator's decision list.
- Defect B's "live in prod" half is structurally verified (the EXPIRED write is
  behind no flag) but the reviewer had no box access; the on-box confirmation
  came from the earlier read-only probe.
