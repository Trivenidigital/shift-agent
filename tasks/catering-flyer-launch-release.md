# Catering and Flyer launch review — October 2, 2026

**Decision: hold customer rollout.** The repaired source candidate and synthetic Flyer package are ready for review. The full test gate passed: 10,137 passed, 56 skipped, zero failures. Live owner identity, commercial menu facts and release authorization remain unresolved. Target: 09:00 America/New_York.

**Drift-check tag:** extends-Hermes. No new subsystem, architecture exception, provider migration or production source deployment. [Implementation and reuse maps](catering-flyer-launch-plan.md).

## Recommendation on Claude's analysis

Start with a supervised pilot using the existing products once the concrete gates below pass. Prior organic traffic should not be treated as something engineering must manufacture before a pilot can start; real paid usage is evidence to collect during it.

Keep money, permissions, STOP and delivery safeguards deterministic. Defer a parallel replacement agent, broad router rewrite, new payment automation and transport migration. Those would add another system to validate before customers see value. Manual payment confirmation and a supervised manual queue are acceptable pilot operations.

Live checks corrected two stale assumptions: the F0226 watchdog already has a one-update guard, while trial-to-paid activation has an actual transition bug now repaired. F0226 itself still needs a real disposition. The menu's serving facts and owner approval route are genuine present blockers. Pricing and the value proposition against self-service ChatGPT remain customer decisions, not reasons for another architecture project.

## What changed

- **Catering quotes:** customer messages use frozen integer cents, including package prices. Drafted prose cannot alter approved money. Complete item and total details are retained.
- **Catering retries:** a definitely failed send can retry only the same terms before the original deadline. Changed/omitted discounts, expired quotes and uncertain delivery refuse.
- **Catering portions:** selected dishes scale using confirmed serving sizes and current headcount. Unknown servings stop for review. Explicit quantities, packages and replay behavior remain supported.
- **Flyer payment:** a trial customer who confirms a paid plan can use the existing operator payment-confirmation path. Amount, currency, reference and replay safeguards remain.
- **Flyer rendering:** existing single-item instructions now require one offer, consistent food imagery and no empty template rows. Existing OCR QA blocks duplicate declared headlines, with safeguards for text shared across legitimate roles. Retry budgets are unchanged.
- Test-only repairs fix expired/invalid fixtures, update the canonical quote contract, and stop shared-module replacement and an unclosed monkeypatch from contaminating later tests.

The candidate includes the original checkout's 15 pending Catering changes. Their original patch is preserved separately; these were not all authored overnight. Original checkout source remains untouched.

## Verification

- Initial focused candidate: **777 passed, 1 skipped**.
- Portion/pricing/finalization/package group: **276 passed**.
- Updated Catering lifecycle and approval-skill contract: **39 passed**, including the full 17-step journey.
- Flyer render/QA/repair group: **268 passed**.
- Test isolation: **213 passed** for shared-loader repairs; **167 passed** for the leaked monkeypatch repair. Both defects have direct failing/passing reproductions.
- Architecture governance passes for all six affected projects. Independent source and strategy reviews completed; no known introduced blocker remains in the reviewed repairs.
- **Full test gate passed:** 10,137 passed, 56 skipped, 193 warnings in 18m38s; exit 0. All 1,787 snapshot files unchanged. Frozen v3 ran in `codex-launch-final-v3-tests-20261002`, snapshot SHA256 `3d8a966cbb18bdebca8d5ebc1acc40b717d391c57b5e27d5132cf3bffe072f30`. Evidence: `../../artifacts/final-v3-suite.log` and completion file `final-v3-suite.exit`.

These groups overlap and must not be summed. The earlier completed full run failed: 115 failed, 10,005 passed, 56 skipped. Its failures led to the fixture and isolation repairs above. V2 was stopped early at 573 passed/1 skipped to include the newly identified monkeypatch fix. Earlier results are retained, not presented as a green release gate.

## Real-model Flyer rehearsal

Funding is verified through successful provider calls. Actual deployed managed generation was exercised with the live rendering flags and candidate modules loaded from isolated temporary storage. All fixtures are fictional and no customer messages were sent.

- The original managed image repeated a headline and contained empty menu rows despite QA passing. The new QA rejects that exact saved OCR/image exhibit.
- An initial repaired image removed those defects but included an unrelated poultry icon; visual review rejected it and the prompt was narrowed.
- With the final source, one image repeated an item and was correctly blocked by existing QA. The next image passed QA and two independent visual reviews. Both attempts are retained; this small sample establishes no generation success rate.
- The accepted preview produced **four final formats**, all passing existing OCR QA: WhatsApp image, Instagram post, Instagram story and printable PDF. Finalization used four OCR calls, zero image-generation calls and zero sends. Original preview evidence stayed unchanged.
- The exported PDF was independently rendered and inspected. All details remain readable. Square/story variants preserve the design with white padding rather than a new full-bleed composition.
- Delivery dry-run passed through the deployed send command with all four formats, normal QA/hash gates, and no bypass flags. Copied state reached delivered; every outbound ID was dry-run, with zero network/process attempts. Original final evidence stayed unchanged. Synthetic approval and delivery do not prove real customer approval or WhatsApp transport.

Accepted preview SHA256: `23e2c8d3328458120568b16b244421d258410c9a39443e9d50f0aa68bd69522f`.

Evidence directory: `C:/Users/srini/.codex/worktrees/catering-flyer-launch/artifacts/`:
- `managed-rehearsal/`: original defect.
- `managed-candidate-rehearsal/`: icon defect.
- `managed-candidate-v2-rehearsal/`: correctly blocked duplicate item.
- `managed-candidate-v3-rehearsal/`: accepted preview.
- `final-package-rehearsal/` and `final-rehearsal-summary.json`: final artifacts, hashes and QA.
- `send-rehearsal-summary.json`: dry-run delivery and isolation evidence.

## Live prerequisites

| Requirement | Verified state | Needed before rollout |
|---|---|---|
| Owner approval | Owner identity equals bridge account; bot mode drops owner-typed `fromMe` messages. Public intake currently uses a wildcard allowlist | Separate verified owner/test identity, or a deliberately restricted self-chat pilot with end-to-end echo/approval proof. Do not enable forwarding alone |
| STOP/takeover | Relevant controls absent from inspected live settings | Enable and prove existing controls for confirmed test identities through gateway and timer paths |
| Commercial menu | All 77 live items lack confirmed servings. Both package notes say test rates await owner replacement | Confirm a small pilot menu and its prices/servings, or use owner-specified quantities. Confirm package rates before use. All 77 items need not be catalogued for one supervised pilot; synthetic serving facts must never be copied into production |
| Paid activation | Seven trial accounts, no pending upgrades; payment provider is manual and checkout URL empty | Actual verified operator payment reference. Automated checkout is not claimed |
| Existing manual work | F0226 remains in manual edit since August 1; watchdog already sends only one update | Resolve through the existing queue with a real disposition |
| Release | Local candidate only; full test gate green | Explicit commit/release authorization under repository policy, then review, tarball deployment and rollback checks |

JEV: no configured key was found in the inspected local/remote settings or gateway environment. Awaiting the secret's location/name; no JEV invocation is claimed. JEV is optional for bounded review, not an authority for money, identity or STOP decisions.

## Operations and release path

The bridge was disconnected. With no outbound queue or active render/send jobs, the existing gateway was restarted at 02:36 UTC and reconnected. Startup checks passed; follow-ups through approximately 04:16 UTC showed it connected with queue zero (about 99 minutes uptime). Setup readiness passed 17/17, which does not establish commercial or approval readiness.

Installed application receipt remains `a98431f5`; Hermes remains `cc4cab2f592e60a197e796506de9168f74baf3ea`, matching the modern baseline. Candidate base is `98bfbd86851cb6f2aa4f565e781b9626a6b7b2f0`. No credentials were deleted, accounts activated, source committed/pushed, or source deployed.

After authorization and green checks: review/commit the exact files, create and review the PR, build the canonical tarball, preserve prior release and customer state, deploy, verify installed hashes/receipt, and rehearse controlled product journeys. Use the existing rollback procedure on failure. Broader onboarding waits for the live prerequisites above.

## Final review handoff

Review bundle: `../../artifacts/review-bundle.zip`; exact file hashes: `../../artifacts/review-manifest.json`; draft PR description: `../../artifacts/review-body.md`. The bundle includes changed/new files and a tracked-file patch. It is a review artifact, not a deployment tarball. Source and tests match the passing frozen snapshot; only the three status documents were updated afterward. Remote default HEAD was verified equal to the candidate base.
