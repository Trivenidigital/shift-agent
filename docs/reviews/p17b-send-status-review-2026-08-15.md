# p17b independent review record — fix/catering-send-status-remaining

Date: 2026-08-15. Reviewed tree: `88e06da` (worktree `C:/projects/sme-agents-wt/catering-p17b`).
Verdict: **MERGE_READY, 0 BLOCKS_MERGE**, subject to the two gates below.
Reviewer: independent adversarial subagent (4 lenses: structural reach, test-seam
honesty, per-arm failure semantics, page-subprocess blast radius). Load-bearing
findings re-verified by the lead against the code directly.

## Gates before merge
1. **Linux green on the POSIX-only test files — CLOSED for `88e06da`, must
   re-run after any further commit touching them.** Verified via saved artifact
   (`scratchpad/p17b_linux_posix_run.txt`): docker `python:3.11-slim`,
   `platform.system() = Linux`, **119 passed, zero skips** on
   test_select_catering_proposal + test_create_catering_proposal_options +
   test_catering_mint_deposit_script, mounted on the live worktree. Runner
   recipe recorded in memory
   (`reference_linux_test_runner_docker_trusted_host.md`): `--network host` +
   pip `--trusted-host` (the earlier "no PyPI access" was a TLS-interception
   cert failure). Broad sweep (branch 2412/3 vs main-baseline 2382/3, same 3
   pre-existing cross-file failures) reported from terminal output, no saved
   artifact. Rollback-compat suite is Windows-sourced (20/20) — acceptable:
   it is git-dependent, not POSIX-gated, so Windows evidence is real evidence.
2. **Follow-up #1 FIXED in-branch as `aea027e`** (ruled in-scope by the lead,
   verified by direct read of both the defect and the fix):
   `_notify_owner_generation_failed` now returns `proc.returncode == 0`
   (False on raise, WARN on non-zero exit); the unconfirmed row moved from
   `_post` (which returns before the page attempt and could only guess) into
   `_fail_generation`, emitted AFTER the page with the real `paged` value via
   new `_record_send_unconfirmed`; all three `_post` failure sites pass
   `send=(jid, status)`; non-send failure paths unchanged. Red-first on Linux
   (4 new cases red pre-fix at the `owner_paged is False` assertion, green
   post-fix). **Both gates re-closed on the final tree `aea027e`**: lead-run
   Linux container sweep of all six p17b suites = 204 passed, 0 failed
   (artifact `scratchpad/p17b_linux_posix_run_aea027e.txt`); Windows five
   suites = 81 passed / 104 skipped.

## PR-level review of the pushed tree (PR #717, second independent reviewer)

Verdict: **MERGE_READY, 0 BLOCKS_MERGE.** Authenticity proven (remote SHA =
local HEAD = PR headRefOid; `gh pr diff` content-identical to local diff, 8
commits / 15 files). Reviewer independently reproduced Windows 81/104 and
Linux 204/0, and ran its own adversarial probe (notifier raises / exits 6 in
amend answer mode: state stays committed, exit code unchanged,
`owner_paged=False`). All six operator lenses held with file:line evidence:
19 send sites enumerated, none status-blind; every `owner_paged` derives from
the real notifier result (send-catering-ack's hard-coded False is structurally
guaranteed — script cannot page); no double-page path (convergence points
checked); no page/audit failure can alter exit codes or roll back committed
state (fd-scoped FileLock nesting specifically ruled out); no live stub-escape
(detector AST logic re-run: 5/5 sites bound); P1 money-path byte-unchanged and
visibly deferred, with the mint-deposit page now correctly warning "Do NOT
re-invoke".

Additional FOLLOW_UPs from PR-level review (none block):
- **10.** `owner_paged` truthfulness is pin-tested in only 1 of 4 paging
  scripts (cpo). Code verified correct in all four (+probe for amend), but a
  regression to hard-coded True would only be caught in cpo. Cheap:
  parametrize the existing notify stubs (amend `:166`, select `:183`,
  mint-deposit) — fold into the P1 branch or a test-only PR.
- **11.** Kill-switch page storm — sharpest instance of follow-up #5:
  `disabled/suppressed/throttled/refused` all map to certainty "failed" and
  now page unthrottled on select-ack + both amend paths, while
  `safe_io._agent_disabled_drop` already emits its own throttled §12b page. An
  operator-initiated `shift-agent-disable` would generate an extra priority-1
  page per catering send.
- **12.** mint-deposit's unconfirmed row goes through `commerce.audit.emit`
  (no re-validation) unlike the other four scripts' Pydantic construction —
  schema-valid today (field-checked), typo would surface only at read time.
- **13.** e2e harness stubs `_notify_owner_generation_failed` as `None`-return;
  post-PR `paged=None` would fail bool validation and drop the row silently —
  reachable only in OPENROUTER-gated e2e failure path; cosmetic.
- **14.** `_fail_generation:622` calls `_audit_failure` unwrapped before the
  page (audit fault would pre-empt the page) — pre-existing ordering.

## Verified-true claims (spot-checked by lead)
- Rollback safety: pinned rollback target `dc7a81a2` routes the new
  `catering_customer_send_unconfirmed` tag to `_UnknownLogEntry`; rollback
  compat suite 20/20.
- All amend sends occur outside `FileLock(LEADS_LOCK)`; marker re-acquires
  non-nested; corrupt store cannot be clobbered.
- `card_delivery_status` never written from any customer-reply path (pinned by
  test).
- Page failure can never convert a durable success into a script failure
  (upstream-retry / duplicate-send safe).
- No leftover status-blind `_bridge_post(` call sites in the five scripts.

## FOLLOW_UPs recorded, NOT in p17b scope
2. **Hard-coded `outcome` literal survives in 2 of 5 scripts** —
   `select-catering-proposal:290` and `send-catering-ack:96` write the literal
   `bridge_unreachable` into `CateringCustomerAckFailed.outcome` (free-form
   str, unlike `reason`) for every non-sent status incl. `send_uncertain`.
   Truth recoverable from the paired unconfirmed row. Fix opportunistically or
   fold into the send_uncertain P1 branch.
3. **Chokepoint detector is one-directional** —
   `tests/test_send_chokepoint_singularity.py:921-947` asserts every patched
   seam is called, not every called seam is patched; a new send-helper name
   would run unintercepted in e2e. ~2-line inverse assertion; the `called`
   list already exists at `:937`.
4. **MONEY PATH — deposit re-mint can double-charge after an uncertain send.**
   The uncertain-deposit page says "Do NOT re-invoke catering-mint-deposit"
   but nothing enforces it: the double-charge guard at
   `catering-mint-deposit:300` keys off a live intent, the uncertain path
   voids the intent, so a re-invoke mints a second link.
   `deposit_link_delivery_status` is the write-only marker that would refuse
   it (same shape as #708's `card_delivery_status` reader gap). Belongs with
   `tasks/catering-send-uncertain-truthfulness-p1.md` — same invariant family
   (uncertain must not become retryable). Highest-priority follow-up.
5. **No throttle/dedup on the new priority-1 pages** — branch roughly doubles
   catering Pushover sites; answer-mode hand-off with bridge down fires two
   pages per turn (intended, documented) but unbounded across leads in a
   sustained outage. No reusable owner-page throttle helper in
   `src/platform/`.

## INFORMATIONAL
6. `shift-agent-notify-owner:307` falls back to the WhatsApp bridge when
   Pushover fails — `owner_paged=True` can mean the page went over the channel
   in doubt (harmless when bridge fully down: both fail, paged=False).
7. Quiet-hours suppression returns EXIT_OK → `owner_paged=True` for a
   suppressed page; unreachable at default config
   (`enabled=False`, threshold 1 vs priority-1 pages).
8. `catering-mint-deposit` still sends inside `FileLock(LEADS_LOCK)`
   (`:267`…`:444`) — pre-existing; branch adds marker write + audit emit
   inside that critical section.
9. `catering-mint-deposit` `_notify_owner_best_effort:143-146` passes body
   positionally without `--` separator; inert today.

## Excluded by design
The `send_uncertain -> SEND_FAILED` proposal-set collapse is the separate P1
(`tasks/catering-send-uncertain-truthfulness-p1.md`); deliberately not
reported as a branch finding.
