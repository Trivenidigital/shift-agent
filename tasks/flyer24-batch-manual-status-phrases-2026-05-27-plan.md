# Flyer24 Batch Plan - Manual Status Phrase Coverage (2026-05-27)

**Drift-check tag:** extends-Hermes

## Hermes-first checklist
1. Receive inbound WhatsApp status text -> [Hermes]
2. Resolve sender identity/account phones -> [Hermes + existing Flyer helpers]
3. Detect flyer status intent phrase -> [net-new: Flyer regex phrase coverage]
4. Route to deterministic status reply/manual queue status surface -> [existing Flyer]
5. Emit audit rows + reply transport -> [Hermes/existing Flyer]

Net-new scope in this batch: step 3 only (phrase coverage hardening), plus tests.

## Batch issues (6)
1. `status for project: F####` not recognized as status intent.
2. `status on project F####` not recognized.
3. `where is the update for project F####` not recognized.
4. `need status of F####` not recognized.
5. `status about F####` not recognized.
6. `status update for project F####` not recognized.

## Root-cause hypothesis
`is_flyer_project_status_request()` accepts only narrow `for/of` patterns for project-id forms and misses punctuation/preposition variants common in WhatsApp typing.

## Verification plan
- Add RED parser tests for the 6 phrases.
- Keep edit-intent guard tests green.
- Run focused pytest on cf-router plugin status tests.
- Run `python3 -m py_compile` for touched files.
- Run `git diff --check`.

## Risk / merge policy
Low risk: deterministic phrase parsing only, no payment/account/quota/provider/manual-close mutations.
If CI and focused checks are green, this batch is merge/deploy eligible under low-risk policy.
