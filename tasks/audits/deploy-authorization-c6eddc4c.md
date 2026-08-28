# Deploy record — `c6eddc4c`

**Drift-check tag:** `Hermes-native` — a deploy record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — repo convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Tag:** `deploy-20260824-031700-c6eddc4c` · **DEPLOY_EXIT=0**
**Rollback target:** `deploy-20260824-004017-a159296d.tgz`
**Gateway:** active, `ActiveEnterTimestamp=2026-08-24 03:17:24 UTC`
**Contents:** #773 (`6e8159b7`) + #774 (`c6eddc4c`), deployed together in one
window as ruled. Plus audit docs #771/#772, which carry no runtime.

## Authorization

Operator ruling, this session:

> `#774` = MERGE GO once the remaining `send-path-ci` shard is green.
> `#773 + #774` = DEPLOY GO together after merge.
> The roster→owner reachability issue is P1 hardening, but not a blocker for
> this deployment given the verified current state.

Merge preconditions met and checked, not assumed: 8/8 CI SUCCESS on `9d60e78e`
with HEAD unchanged between the check and the merge. One `registry integrity`
run showed CANCELLED — verified as a concurrency supersede (a run started 51s
later succeeded, and the same check also passed at 02:57), **not** a waived
failure.

## What shipped

`a159296d` was confirmed an ancestor of `origin/main`, and the full-tree
replace was confirmed to carry exactly three commits — #771 and #772 (docs) and
#773 — plus #774. Non-docs files in the range: `identify-sender`,
`cf-router/actions.py`, `cf-router/hooks.py`, and two test files. No surprise
payload, which is the failure mode a full-tree deploy invites.

| change | effect |
|---|---|
| #773 | routes audit **authority**, not identity precedence; `identify-sender` refuses an identifier claimed by ≥2 employees instead of guessing |
| #774 | owner authority is a property of the principal, not of which identifier arrived; plus an anchored identifier-shape allowlist |

## Pre-deploy census — all seven abort conditions clear

Read-only, immediately before deploying. Any non-clear result was an abort.

| check | result |
|---|---|
| rows carrying primary `owner.phone` | `[]` |
| rows carrying primary `owner.lid` | `[]` |
| rows carrying an authorized phone | `['e008']` — the intended dual-role principal only |
| `phone_history` touching an owner identifier | `[]` |
| duplicate phones | `{}` |
| duplicate LIDs | `{}` |
| "stitched" rows (identifiers from two principals) | `[]` |

`phone_history` was added to the census beyond the ruled list: an effective
historical entry reaches the same identifier-widening path as a current phone,
so omitting it would have left the census incomplete against its own purpose.

## Post-deploy verification

`/health -> 200` is not verification. Both verifiers drive the **deployed**
plugin and binary against copied, credential-sterile state, with transport and
state-mutating calls stubbed. No WhatsApp send.

**Identity / authority — 25 checks, 0 failed.** Dual principal holds identical
`["employee","owner"]` membership by phone-JID and by LID; primary owner
resolves owner; six ordinary-employee negatives by both identifier forms;
unknown identity holds nothing; ambiguity fails closed with rc=2
`ambiguous_identifier`.

Boundary refusals, each of which **resolves as owner through identify-sender**
and must still be refused at `is_owner_chat`: group JID carrying owner digits,
malformed double-suffix JID, bare digits, whitespace-padded JID, broadcast
address. And the paired admissions: primary owner JID, authorized phone-JID,
authorized LID.

**Route-level — 15 checks, 0 failed.**

| route | result |
|---|---|
| F8 owner approval via **LID** | `handle_owner_command`, `sender_role=owner`, status update invoked |
| F8 owner approval via **phone-JID** | identical — this is the repair, proven on the box |
| candidate YES/NO via **either** form | `handle_candidate_response`, `sender_role=employee` |

**Production untouched, asserted by sha rather than claimed:** proposal store
`479bb58641a3287e` before and after; dedupe store `68dd15d7450e44da` before and
after.

## The verification tooling was wrong five times, and that is the useful part

Every defect below produced a PASS that meant nothing. All were found by
running against the real box, none by review. Fixes in #775.

1. **Wrong roster path** — failed loudly, the only cheap one.
2. **Sterility guard passed vacuously.** Exact key matching missed the real
   `alerting.pushover_user_key` / `pushover_app_token`, so it redacted nothing
   and then asserted an empty set had not leaked, printing `0 secrets
   redacted`. No leak occurred — box-local temp dir, no outbound call from
   `identify-sender`, dir removed — but the guarantee was not delivered. The
   negative control had tested the redactor with keys matching my own list,
   never the real ones.
3. **Ambiguity check passed for the wrong reason.** Probe id
   `zzz_verify_probe` violates `EmployeeId = ^e\d{3,}$`, so the roster failed to
   load (rc=5) and `rc != 0` was satisfied by a load error, not a refusal.
4. **The route verifier wrote to production state** while its docstring said it
   never would — `CF_ROUTER_INBOUND_DEDUPE_PATH` was not among the four paths I
   redirected. Two synthetic entries, provably mine, 2 of 2 in a 2048-entry
   cache so nothing real was evicted; removed, and the store is byte-identical
   across the corrected run. It now enumerates every module-level path under
   SHIFT_ROOT.
5. **Reused `message_id`** — cf-router dedupes on `(chat_id, message_id)`, so
   every route after the first returned `duplicate inbound` and looked dead.
   This masqueraded convincingly as a candidate-response regression.

The through-line: **four of five were greens produced by the check never
actually running.** The same shape as the CAT-06 completeness finding closed
earlier today — a mechanism that looks like it controls an outcome while being
structurally bypassed.

## Known and accepted, carried forward

**Owner authority is config-anchored but roster-reachable.**
`_resolve_principal` matches an employee first and fills `eff_phone` /
`eff_lid` from that roster row *before* calling `_match_owner_identity`, so a
row can supply the identifier that reaches the config anchor. The LID direction
was already live before #774; #774 adds its phone-side mirror. Bounded today by
the census above, and `shift-agent-lid-learn` cannot manufacture the pairing —
it sets `lid` only on a row whose phone already matches the observed pairing,
never creates rows, never edits phones. Residual is conditional on a bridge
defect. Ruled P1 hardening, not a deploy blocker.

**`E164Phone.from_any` is on the authorization path** and was written as a
lenient human-input parser. The anchored allowlist shields `is_owner_chat`
today, but every future leniency added there silently widens who counts as
owner. The durable rule, per the operator: authorization inputs use a strict
protocol parser; human-entered business data may use a forgiving normalizer.

## Status

`#773`/`#774` identity boundary = **DEPLOYED / CLOSED**.
Next: P1 cross-store privileged-identity integrity (class B — employee ↔
privileged owner-identity collision), which #773's runtime refusal does not
cover.
