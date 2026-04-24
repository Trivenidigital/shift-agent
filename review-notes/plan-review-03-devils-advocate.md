# Plan Review 3/5 — Devil's Advocate / Pre-Mortem (general-purpose)

**Verdict:** 10 risks ranked by severity. Root-cause of any spectacular failure will be in the semantic/human-ambiguity layer, not the deterministic spine.

## Top 10 risks (severity order)

### R1. Approval tracker matches owner "yes" to the wrong pending proposal
**§4.4 / Risk row 7.** Match by "recency + keyword" is ambiguous with 2+ proposals in flight during lunch rush. Single-word "yes" reads unambiguous to matcher but isn't. [DUPLICATES architect BLOCKER B1 — consensus finding]

### R2. fromMe ≠ owner when WhatsApp Web open
**§3 architectural decision #4.** `fromMe` only proves "this linked device sent it" — owner in WhatsApp Web simultaneously = both are fromMe. Owner's "yes" to spouse triggers handle_owner_command. [DUPLICATES architect MAJOR M2]

### R3. Self-chat routing unvalidated (open question #2)
**§10.** Entire owner-channel architecture depends on Baileys being able to send to + read from owner's self-chat JID. Load-bearing but marked only "verify via test." If self-chat JID ≠ owner's user JID, or messages don't round-trip, approval loop has no delivery surface on Day 1.

### R4. Phone-to-identity is brittle and silent-failure-prone
**§4.2 / Risk row 2.** Real SMBs: shared family phones (spouse texts "Ravi sick" from wife's number), number changes (new SIM Tuesday), WA number ≠ roster number, country-code variance (+1 vs 1 vs no prefix), roster typos. Silent misclassification = most dangerous class.

### R5. GDPR/PII: health data plaintext JSON indefinitely
**§3 decision #5, §9 Rollback.** `decisions.log` contains "fever / child sick / period cramps" keyed to named employees. Special-category health data under GDPR Art. 9. No retention policy, no encryption, no DPA, root-readable logs. First employee complaint = regulatory exposure.

### R6. Roster drift on Day 1 → wrong-person incident
**§4.5 / Risk row 6.** Roster collected Day 1, never touched. Day 3: someone quit, someone swapped shifts via text, new hire started. Agent proposes ex-employee as coverage OR sends message to someone who doesn't work there anymore. "Runbook teaches roster.json editing" assumes SMB owner will actually edit it — they never do.

### R7. LLM hallucinates employee_id passed to send-coverage-message
**§4.3 / Risk row 3.** Plan says "candidate phone pulled from roster.json by employee_id" — but LLM produces the employee_id. Kimi already short-circuited audit logs in Phase 0. Under load will emit `e007` when roster has `e001`-`e006`, or swap two IDs. Outbound sender MUST re-validate `employee_id ∈ roster` before POST — not specified.

### R8. Timezone / "tomorrow" ambiguity
**Plan gap.** 11:47pm local: "can't come tomorrow." VPS clock is UTC. "Tomorrow" = which day? Owner approves at 12:14am. No timezone field in roster.json spec, no business-hours field, no disambiguation prompt. §8 success criteria never test late-night / DST flow.

### R9. Fraudulent / prank sick-call — no identity challenge
**§4.1 regex + §4.5 dispatcher.** Sender phone in roster → message trusted. Disgruntled ex whose number lingers in roster.json + employee's kid messing with phone + co-worker with grudge via SIM spoof → real outbound fires. No verification step. §7 row 2 conflates phone-in-roster with identity-verified.

### R10. Side-channel coordination → duplicate coverage
**§3 data flow.** Real SMB norm: Ravi texts Anjali directly BEFORE texting owner. Agent proposes Priya instead. Owner approves. Now Anjali AND Priya show up, or neither. No "is coverage already arranged?" check.

## Areas plan is well-defended against

1. Audit completeness under LLM failure (§4.1 tail-logger + §3 dual-source) — solid design, Phase 0 lesson properly absorbed.
2. Kill-switch and rollback hygiene (§4.7 + §9) — unusually mature for 48h.
3. Unknown-sender handling (default-deny consistent across design), provided roster is current (see R6).

## Bottom line
Deterministic spine solid. Failure surface is semantic/human-ambiguity layer. Rehearsal plan §6 tests happy path on clean data; §8 success criteria exercise NONE of: multi-pending races, self-chat edges, stale roster, late-night timezone, side-channel coordination. If launch fails spectacularly, post-mortem root cause will be R1 or R2 producing a message to the wrong person, compounded by R5 turning one operational mistake into a regulatory incident.
