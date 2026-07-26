# Frozen incident-replay suite — governance

Authoritative rules for the frozen incident-replay suite required by
`docs/reviews/catering-agent-review-charter-v1.2.md` §9. This file is binding;
the runner `tests/test_incident_replay_suite.py` enforces the mechanical parts.

## Fixed, version-controlled location (§9.2)

The suite lives at a single fixed location, under version control:

- **Fixtures:** `tests/incident_replay/fixtures/*.json` — one declarative oracle
  per incident.
- **Runner:** `tests/test_incident_replay_suite.py`.
- **This governance file:** `tests/incident_replay/GOVERNANCE.md`.

The suite is enforceable only because its location and contents are frozen here.
Do not scatter incident fixtures across other test files or fixture directories.

## Fixture oracle format (§9.3)

Each fixture is a JSON object with **exactly** these keys (the runner fails on a
missing *or* unexpected key):

| Key | Meaning |
|---|---|
| `incident_id` | Stable kebab-case id; never renamed. |
| `description` | What the incident is and which deterministic test proves it. |
| `pseudonymized_transcript` | List of `{from, text}` — **aliased identifiers only**. |
| `initial_state` | Pre-incident state relevant to routing. |
| `expected_route` | The route the turn must take. |
| `expected_mutations` | State changes that must happen. |
| `forbidden_mutations` | State changes that must NOT happen. |
| `expected_logical_sends` | Integer count of customer-visible outbound sends. |
| `expected_audit_identities` | Sender identities that must appear in the audit chain. |
| `expected_final_state` | Post-incident state. |
| `pii_safe` | Always `true`; asserted by the PII-safety scan. |
| `additive_only` | Always `true`; declares the fixture under this policy. |

**What the runner enforces vs. what policy governs.** The runner mechanically
checks the oracle's SHAPE — key presence (missing/unexpected), field types, the
PII-safety scan, destination aliasing, and anchor existence. It does **not**
verify that an oracle's VALUES (the specific `expected_route`, mutation lists,
send count, final state) are correct — those values are governed by this policy
(reviewer + product-owner approval under §9.3) and are proven by the anchored
deterministic tests, not by this runner. A green runner means "the oracles are
well-formed, PII-safe, and anchored," not "the documented behavior is correct."

**Anchoring is by module EXISTENCE, not semantics.** `test_incident_anchored_to_
deterministic_tests` and `test_every_fixture_is_anchored_or_exempt` assert that
each incident's mapped deterministic-test module(s) exist in the tree — so a
green anchor guarantees the executable proof still EXISTS, not that it currently
passes or that it exercises this exact oracle. Behavioral verification lives in
those deterministic tests themselves, run as part of the full suite.

## Additive-only policy (§9.2)

The suite grows for the life of the product.

- **New incidents are appended**, never used to replace existing ones.
- **Every confirmed production incident must add a replay fixture before its
  corrective PR merges.**
- **Transcripts are never rewritten to make a test easier.** If an oracle looks
  too strict, the correct response is to fix the product or add a new fixture —
  not to weaken the frozen one.
- **Oracle integrity:** expected outcomes may not be removed or weakened without
  **product-owner and reviewer approval** (§9.3).

## Pseudonymization rule (§9.2)

Real transcripts are pseudonymized before they enter the suite. Strip customer
names, phone numbers, LIDs, business names and street addresses; **preserve
routing-relevant structure** — message ordering, timing, phrasing patterns and
compound-intent shape. Only the identifiers are swapped.

Structure-preserving alias map used across this suite and the existing fixtures:

| Real (never commit) | Alias |
|---|---|
| `+17329837841` / `17329837841` (+ JID forms keep their `@…` suffix) | `+15550100001` / `15550100001` |
| `201975216009469@lid` | `100000000000001@lid` |
| customer conversational line | `+15550100003` (LID `100000000000003@lid`) |
| India owner self-chat number `918522041562` | `15550100002` |
| India **customer** number `918985741562` (a CUSTOMER, not the owner) | `15550100006` |
| other real numbers `+17043243322` / `+19803826497` / `+15713830763` | `+15550100003` / `+15550100004` / `+15550100005` |
| near-miss "wrong phone" test doubles `732-983-7842` / `732-983-7899` | `555-010-0002` / `555-010-0099` |
| business name `Lakshmi's Kitchen` (exact business phrases only) | `Sample Caterer` |
| street address `90 Brybar Dr, Saint Johns FL` (+ `Houston TX` variant) | `100 Example Rd, Testville` |

**Owner ≠ customer:** `918522041562` (owner) and `918985741562` (customer) map
to DISTINCT aliases on purpose. They previously collided on one alias, masked
only by a test mock; distinct aliases keep a future un-mock from silently making
customer == owner.

**Intentional many→one collisions are NOT bugs.** Some distinct real values map
to the SAME alias when the tests treat them as one entity — e.g. the "customer
conversational line" and `+17043243322` both alias to `+15550100003`. A
maintainer must NOT "fix" these to be unique: the collision is deliberate, and
only the owner/customer pair above is required to stay distinct (that one
carries a security-hygiene invariant).

The runner’s fail-closed PII scan (`test_fixture_is_pii_safe`) rejects any
fixture that still contains a real identifier token; `test_fixture_destinations_
are_aliased` rejects any non-alias phone/LID destination.

## Privacy / security replacement process (§9.2 exception)

Additive-only has **one** exception. A fixture may be replaced or redacted
**only** to correct a privacy, security, legal, or factual defect — never to
make a test pass. Exposed PII is never preserved merely to satisfy additive-only.

The process for a replacement:

1. **Reviewer approval** is required and recorded.
2. **Routing-relevant structure is preserved** (message ordering, timing,
   phrasing patterns, compound-intent shape) — only the defective content
   changes.
3. **A repository record links the superseded and replacement fixture hashes.**
   Append a row to the ledger below with the `sha256` of the superseded file
   content and of the replacement, plus the reviewer and reason. This makes the
   redaction auditable without retaining the exposed data.

### Superseded ⇄ replacement ledger

| Date | incident_id | Superseded sha256 | Replacement sha256 | Reviewer | Reason |
|---|---|---|---|---|---|
| _(none yet)_ | | | | | |

## Execution safety (§9.3)

- **Fake transport by default.** `tests/conftest.py` autouse-forces
  `HERMES_BRIDGE_URL` to a closed loopback fake sink for every test; the runner
  asserts this (`test_transport_defaults_to_fake_sink`).
- **Fail closed on unknown / real destinations.** `safe_io.py` defines
  `LiveBridgeSendInTestError`, raised for any pytest-context send targeting the
  live bridge even with the explicit opt-in; refuse-by-default holds without it.
  The runner proves both (`test_harness_fails_closed_on_live_destination`).
- **Fixtures never contain a real customer destination** — enforced by the PII
  scan and the destination-alias check.
- **Live-parity execution** may use **only an explicitly allowlisted operator
  test number**; that path is out of scope for this default-fake suite.

## Exact-release runner note (§1.5.1)

Every routing, orchestration, model, prompt or toolset change must run this
suite, and it must run **against the exact release candidate**. The runner uses
only repo-relative paths and imports (`fixtures_fleet.ensure_fcntl_stub` + the
conftest path shim), so it runs on an arbitrary checkout / release worktree with
no path edits. On Windows the `fcntl` stub makes `safe_io` importable.

Run:

```
python -m pytest tests/test_incident_replay_suite.py -v
```

The `28-send-spiral` case is `xfail(strict=True)` on purpose: it encodes a
missing protection (no hard per-turn transport budget active by default on the
live path, #643 `GATEWAY_TURN_SEND_BUDGET_ENABLED` default OFF). It **must not be
green** until the budget is active by default. The graduation trigger is the
budget being **DEFAULT-ON** — `turn_send_budget_enabled()` returning True in an
unconfigured environment — **not** the mere merge of a PR. PR-3 ships the budget
installable but still **default-OFF**, so this case stays xfailed after PR-3
merges; only when a later change flips the default ON does the strict xfail
become an XPASS ⇒ suite error, forcing the marker to be removed and the incident
to graduate to a normal green assertion.
