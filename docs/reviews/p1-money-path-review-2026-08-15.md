# P1 money-path adversarial review — `fix/catering-send-uncertain-money-path`

Reviewed tree: `614439f` (3 commits on merged main `830a808`), 2026-08-15.
Verdict: **MERGE_READY, 0 BLOCKS_MERGE** — but see the lead ruling below, which
upgrades one FOLLOW_UP to must-fix-before-merge.

Reviewer ran the suites itself (docker 51 passed; Windows rollback-compat
20 passed) and wrote **three adversarial probes** outside the repo.

## Vectors cleared (evidence-backed)

- **V1 double-charge — NO PATH FOUND.** All six minting entries closed: the
  owner-approval hook is blocked upstream at `deposit.py:68`
  (`_should_mint_deposit` false on a bound lead) before the subprocess spawns;
  direct CLI hits the new guard; concurrency serializes under LEADS_LOCK;
  crash-between-mint-and-bind falls to `_find_live_intent_for_lead` (probe C:
  rc=2 `reinvoke_live_intent_exists`, one intent); manual-void-then-reinvoke
  still refuses; downgrade round-trip degrades to `already_minted` EXIT_OK.
  Guard ORDER verified correct — the uncertain guard first is what converts a
  silent no-op into a non-zero exit + page + audit row.
- **V2 stranded payment — FIXED, traced end-to-end.** `mark_attempted` does not
  mutate status, so the intent sits at `minted`; `mark_confirmed` refuses only
  `{voided, refunded, chargeback}`; the order stays `pending_payment` so the
  →`paid` transition is legal; and the lead match at
  `commerce-payment-confirm:465` now SUCCEEDS because of the new bind. Pre-change
  this fell to the lead-divergence branch at `:481`. No double-confirm path.
- **V3 crash atomicity — SAFE, fails toward refusing.** Both facts land in ONE
  `model_copy` + `atomic_write_json`, so "link exists" and "delivery unconfirmed"
  cannot disagree on disk. Strictly safer than pre-change, where the same crash
  left a VOIDED intent invisible to the guard plus an unbound lead — the
  original double-mint.
- **V4 definite arm — behaviorally unchanged**, same order and content;
  the page-body ternary swap is the same expression by definition
  (`:621`). Its pin test is untouched by the diff.
- **V5 audit/rollback — verified empirically, not from docstrings.**
  `CateringDepositLinkFailed.reason` byte-identical vs `dc7a81a2` (not widened);
  both new rows validate as their typed classes on HEAD and route to
  `_UnknownLogEntry` under `dc7a81a2`; no new lead field owed
  (`deposit_link_delivery_status` already exists at `schemas.py:2488` and is
  already in `LEAD_FIELDS_UNKNOWN_TO_OLD`).
- **V6 test honesty — all 6 inversions justified, none vacuous.** Every inverted
  assertion is re-pinned on the definite arm by
  `test_the_definite_arm_still_emits_the_full_failed_and_voided_triple`, which is
  also what proves the uncertain-arm negatives are non-vacuous (same row-type
  string asserted present there, absent here).

## LEAD RULING — one FOLLOW_UP upgraded to MUST-FIX

**Sticky uncertain state violates operator ruling R1.** The reviewer classified
it HIGH-but-not-blocking (reasonably: the alternative was leaving the double-mint
live, and the path is config-gated at `deposit_pct: 0`). I am ruling it
must-fix-before-merge because R1 states verbatim *"an explicit supervised
reconciliation can later resolve it… Do not make uncertainty permanently
irrecoverable"* — as implemented there is NO supported operator action that
resolves it, and the owner page instructs a resolution that dead-ends
(void the intent → re-mint still refused → re-page P1). Secondary harm: after
an operator voids, the lead asserts `deposit_status="awaiting_payment"` against
a voided ledger intent — a durable lie in a money field. Shipping this would
deliver half the ruling. Dispatched as FIX 1 (extend the existing
`catering-lead-reconcile` primitive if it fits — Hermes-first/drift rule — with
both R1 outcomes: confirmed-delivered, and authorize-fresh-attempt).

## Also dispatched in the same round

- **FIX 2 — narrow the guard to require a binding.** Probe A: a legacy lead in
  the shape `830a808` leaves (uncertain + voided intent + unbound) is refused
  permanently while the page and audit row claim a LIVE link exists, naming
  `intent ""`, `$0.00` — both false. Exposure is currently ZERO (`830a808`
  landed after the `d6f9ba8c` deploy, so `uncertain` has never been written in
  production), and the one-line narrowing removes the deploy-ordering
  constraint entirely. Reviewer verified it reopens nothing.
- **FIX 3 — union-validation coverage for both new rows.** `commerce/audit.py`
  deliberately does not validate and the new tests read raw dicts, so a future
  field-shape drift would go uncaught until a reader hit the row.

## INFORMATIONAL (recorded, not actioned)

- A downgrade→upgrade round-trip silently disarms the refusal (marker stripped,
  binding preserved) and degrades it to an `already_minted` EXIT_OK no-op. Not
  a double-mint; the sidecar preserves the value.
- The refusal pages P1 on every re-invoke; only the manual CLI reaches it (the
  hook is blocked upstream), so no page storm.
- The bridge stub is entirely test-side (sitecustomize shim); no test hook
  exists in the money script.
