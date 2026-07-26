# Catering Agent Review Charter — Consolidated (v1.2 — FINAL)

**Status:** FINAL — all reviewer and product-owner amendments incorporated; pending signatures
**Charter decision of record:** The architecture, evidence standard, readiness hierarchy, safety posture, and replay/rollback requirements are accepted. This approval authorizes the Part 1 investigation only. **No product implementation or pilot activation is authorized by this charter approval.**
**Product owner:** Srini (SriniY)
**Reviewer:** (designated at kickoff)
**Version history:** v1.0 — initial consolidation. v1.1 — fourteen reviewer amendments (evidence-enablement exception, session structure, evidence-record schema, audit-identity minimum, tool-reachability definition, STOP semantics, replay privacy exception, PR-queue flexibility, logical-reply definition, measured-vs-projected economics, parity definition, fixture enumeration, BSP assumption labeling, engagement protocol). v1.2 — final sign-off amendments (shared transport dependency, exact-release evidence, tenant-isolation gate, human-takeover gate, replay oracle format and execution safety, ten Catering business invariants, Shift capability matrix, digest demoted to non-gating, WhatsApp dependency evidence).

---

## 0. Purpose and product vision

The goal is not merely to build a WhatsApp chatbot. The goal is a state-of-the-art Catering Business Growth Agent that helps catering businesses generate qualified leads, convert more inquiries, increase booking value, reduce missed follow-ups, protect margins and retain customers.

The agent should eventually function as:

1. A lead-generation agent.
2. A catering sales concierge.
3. A menu and proposal specialist.
4. A sales-pipeline and follow-up manager.
5. A business-intelligence system.
6. A post-booking operational handoff assistant.
7. A repeat-business and referral engine.

The owner retains control over pricing, discounts, date commitments, contracts, refunds and other consequential decisions at all times.

---

## 1. Governing principles

### 1.1 Architecture boundary (established, preserved)

> **Hermes owns language, interpretation, clarification, extraction and customer-facing presentation. Deterministic kernels own identity, authorization, state transitions, pricing, idempotency, mutation, outbound policy and audit.**

Hermes may propose an action. The deterministic layer validates and executes it. Hermes never constructs raw state-file changes, never invents prices, and never authorizes an irreversible mutation.

### 1.2 Hermes-first does not mean LLM-everywhere

The program goal is effective use of Hermes and reduction of unnecessary custom code. It is **not** a presumption that every deterministic classifier or routing guard should be replaced. The current deterministic-first posture (F7 primary routing) exists deliberately, post-incident, because the root cause of the 28-send spiral was a model-capability floor (gpt-4o-mini using `delegate_task` as an escape hatch), not the Hermes concept. The review adjudicates whether and where that posture should be relaxed, based on evidence — it does not assume relaxation is the goal.

The target is not "no custom code." The target is:

> The smallest deterministic kernel necessary to guarantee correctness and safety, with Hermes handling the broad conversational and reasoning surface.

### 1.3 Evidence standard (CLAIMS GATE)

Every capability classification must cite at least one of: repository file and function; test; production probe; state artifact; audit event; operational command result.

If no supporting evidence exists, the capability is labeled **UNVERIFIED**. Intended or documented behavior is never classified as implemented behavior. Green CI, merge status, documentation or local tests alone do not satisfy any gate.

**UNVERIFIED decision rules:**

* Any GO criterion resting on UNVERIFIED evidence resolves to **NO-GO** for the affected gate.
* UNVERIFIED status on a non-gating capability is acceptable; that capability is simply **excluded from pilot scope**.

### 1.4 Evidence-record schema (mandatory for all gating claims)

Every gating claim must be returned as a structured evidence record — never as prose such as "verified in production":

| Field | Required content |
|---|---|
| Claim ID | Stable identifier (e.g. `SP-GO-01`, `CAT-INV-05`) |
| Claim | Exact behavior being asserted |
| Environment | Production or named production-parity environment |
| Deployed SHA | Exact code/config revision (per §1.5.1 exact-release rules) |
| Timestamp | UTC verification time |
| Evidence type | Probe, replay, audit row, state artifact, command |
| Procedure | Exact command or reproducible steps |
| Expected result | Predeclared pass condition |
| Actual result | Observed output |
| Artifact location | Repository path, run ID, log reference, or hash |
| Reviewer status | VERIFIED / FAILED / UNVERIFIED |

**Evidence freshness:** safety-critical live evidence is tied to the deployed SHA at verification time. It **expires** — reverting the affected claim to UNVERIFIED — when the relevant code, configuration, adapter, tool permissions or deployment topology changes.

### 1.5 Live-environment and production-parity standard

All safety-critical claims must be verified in production or a named production-parity environment. "Reality-faithful" means the actual deployed gateway process drives the test.

**Production parity is defined as:** the actual gateway, adapter, model tier, tool schemas, permission configuration, persistence layer, transport policy and deployed code path. **Mock transport is not parity** for transport-budget or outbound-identity gates.

For risky exercises (rollback, kill switch), an **allowlisted production-parity tenant or operator test number** is permitted and preferred over disrupting real customer traffic.

#### 1.5.1 Exact-release and deployment evidence

All live-readiness claims must be established on the **exact deployable release candidate and deployment artifact**. Tests against `main`, a synthetic merge ref containing additional commits, or a different artifact do **not** prove the deployed candidate.

For every pilot release, record:

* exact release SHA;
* immutable tag or protected release ref;
* diff from current production;
* included and excluded commits;
* exact-release CI result;
* deployment-artifact hash;
* rollback target and backup artifact;
* post-deployment hashes and markers;
* gateway and bridge process count;
* WhatsApp connection and queue state;
* restart count;
* preserved systemd/operator state;
* before/after production-state counts.

The **recovery-watchdog timer reversal** is a permanent deployment-state-preservation regression fixture in the frozen replay suite (§9.1).

### 1.6 Evidence-enablement exception

The no-implementation rule (§1.7) does not create a deadlock against producing gating evidence:

> **Before Part 1 approval, narrowly scoped test fixtures, read-only probes, observability, audit markers, documentation, and default-OFF verification seams may be added only when necessary to evaluate an existing capability. Such changes may not add customer-facing capability, alter routing or business behavior, activate dormant tools, or begin a broader implementation stream.**

Each evidence-enablement change must be **separately labeled** (e.g. `evidence-enablement:` prefix in the PR title) and **separately approved** by the product owner before merge. The dev session must return these as a distinct list, never mixed into product implementation.

### 1.7 Prohibitions during this review

Do not:

* begin product implementation (evidence-enablement changes per §1.6 excepted);
* enable outbound marketing, payments, Stripe, commerce, multi-location, autonomous pricing or irreversible booking actions;
* authorize autonomous bulk outreach;
* contact the previously affected customer (directly, or via replay — the §9.3 harness rules make this structurally impossible);
* rewrite incident transcripts to make tests easier (privacy/security redaction per §9.2 excepted);
* recommend removing F7 merely because it is custom code.

---

## 2. Three-level readiness model

The review returns **three separate GO/NO-GO rulings**:

| Level | Gate | Depends on |
|---|---|---|
| 1 | **Shared Platform** GO/NO-GO | — |
| 2 | **Catering** GO/NO-GO | Shared Platform GO |
| 3 | **Shift** GO/NO-GO | Shared Platform GO |

**Dependency rules:**

* Catering GO **requires** Shared Platform GO. Shift GO **requires** Shared Platform GO.
* Catering GO does **not** require Shift GO. Shift-specific capabilities are not prerequisites for Catering.
* Shared gateway, transport, audit, rollback and operational controls are assessed once at the Shared Platform level. Agent-specific capabilities are assessed at their own level.

A ruling of Catering GO / Shift NO-GO (or the reverse) is valid and operationally meaningful: the agent with GO may proceed to supervised pilot on the shared platform; the agent with NO-GO may not.

### 2.1 Shared transport dependency (explicit product-safety decision)

The hard outbound transport budget is a **Shared Platform requirement**. Because Catering and Shift use the same gateway and adapter seam, **neither agent may receive GO while the shared transport budget is absent or unverified.**

A supervised Catering pilot before the hard budget is installed requires a **separately approved temporary Shared Platform profile** with equivalent live-proven transport protection, approved by the product owner and reviewer as its own gated decision. It may not be inferred from Catering-specific readiness alone.

---

## 3. Review structure and governance

### 3.1 Two-part structure

* **Part 1 — Pilot readiness.** Blocks the supervised client pilot. Returned first.
* **Part 2 — Growth and simplification roadmap.** Returned after Part 1 review. Does not block the pilot unless it identifies a new safety-critical defect.

### 3.2 Timebox, session structure and priority rule

Part 1 is timeboxed to **three focused investigation sessions**, structured as:

1. **Session 1 — Evidence inventory and gate map.** Enumerate the §1.5.1 deployment ground truth; map every GO criterion to existing or missing evidence.
2. **Session 2 — Live/parity verification of gating claims.** Execute probes, replay runs, kill-switch, takeover and rollback exercises against the exact release candidate; capture evidence records per §1.4.
3. **Session 3 — Unresolved-risk review and final rulings.** Close or classify remaining gates; produce the three rulings and the PR queue.

**Priority rule:** gating evidence (transport, tenant isolation, rollback, audit identity, kill switch, takeover, STOP) takes precedence over exhaustive capability documentation. Anything non-gating not verified within the timebox moves to Part 2 or is marked UNVERIFIED. Do not spend the timebox documenting low-priority growth capabilities while transport or rollback gates remain unresolved.

Only the product owner (Srini) may approve a review-budget extension.

### 3.3 Approvals

* **Product owner:** Srini. Approves: model floors, evidence-enablement changes, temporary Shared Platform profiles (§2.1), review-budget extensions, pilot scope, GO/NO-GO acceptance, replay-oracle changes (§9.3), digest promotion (§8.3), and activation of any gated capability.
* **Reviewer:** a second review pass (designated at kickoff) signs off on Part 1 before any implementation stream opens, and co-approves replay-oracle changes and temporary Shared Platform profiles.
* No product implementation begins until Part 1 receives both product-owner and reviewer approval.

---

# PART 1 — PILOT READINESS

## 4. Shared Platform assessment

Assess the shared Hermes/shift-agent runtime, gateway and transport layer using production evidence.

### 4.1 Shared Platform capability review

For each item, classify as: Production-ready / Working but incomplete / Implemented but dormant / Unreliable or unsafe / Not implemented — with a §1.4 evidence record.

* Gateway routing and the real adapter seam
* Hard transport send budget
* Send and progressive-edit caps
* Transport deduplication
* Outbound audit-identity infrastructure
* Kill switches
* Rollback machinery
* Tool permission enforcement and held/dark toolset reachability
* skill_manage disarmament (runtime-level control)
* Tenant isolation and permissions (per §4.5)
* Idempotency infrastructure
* Cron/scheduling infrastructure used by follow-ups
* Deployment-state preservation (systemd/operator state, watchdog behavior)

### 4.2 One-logical-reply definition (machine-verifiable)

The "one logical reply per inbound" rule means, auditable end to end:

* One `logical_turn_id` per inbound customer message.
* One customer-response plan per logical turn.
* Deterministic transport splitting may create multiple provider messages, **all linked to the same logical response** — a physical transport split preserves one logical outbound identity.
* Progressive edits count against an explicit edit cap.
* Retries must reuse the same logical-send identity.
* Owner notifications are separate from customer replies, carry **separate outbound identities**, and do not count against the customer send.

Multiple sends without a concrete audit relationship to a single logical response are a violation — "it was one logical reply" is not claimable without the linkage.

### 4.3 Outbound audit-identity minimum

> Every successful outbound send must carry: a stable internal send-attempt ID; customer/tenant identity; logical-turn ID; transport destination; timestamp; outcome; and the transport (provider) message ID when the provider returns one. A provider message ID may be recorded as explicitly unavailable **only when the provider genuinely supplies none**; internal audit identity remains mandatory in all cases.

A metadata-only "incomplete" record does **not** satisfy GO when the internal send identity itself is missing. The incomplete status covers provider-ID absence only.

### 4.4 Held/dark tool reachability definition

> A held or dark tool **fails the gate** if the production runtime identity can expose it to the model, authorize it, invoke it, or cause its external effect through any customer-controlled path. Merely existing in the repository is **not** a failure.

For every held tool, evidence is required at four boundaries: **schema** (not discoverable by the model), **permission** (not authorized for the tenant), **invocation** (gateway rejects the call), **effect** (no external effect producible). One boundary alone is insufficient for VERIFIED.

### 4.5 Data governance and tenant isolation

Shared Platform cannot receive GO unless tenant and customer data isolation are VERIFIED. Review:

* lead, conversation, menu, pricing and proposal separation by tenant;
* Hermes context and memory scoping;
* secrets and credentials;
* access logging;
* PII storage;
* retention;
* export;
* deletion and offboarding;
* backups;
* incident-evidence handling.

> **Any path capable of exposing one tenant's information or tools to another tenant resolves Shared Platform to NO-GO.**

### 4.6 Shared Platform GO criteria

Shared Platform cannot receive GO unless all of the following carry VERIFIED evidence records on the exact release candidate (§1.5.1):

| ID | Criterion |
|---|---|
| SP-GO-01 | Hard transport budget installed and marker-verified at the real adapter seam |
| SP-GO-02 | Send and progressive-edit caps enforced in the live/parity environment |
| SP-GO-03 | 28-send incident is a permanent frozen replay fixture and passes |
| SP-GO-04 | Kill switch exercised live (allowlisted parity tenant/operator number permitted) |
| SP-GO-05 | Rollback exercised live and satisfies the §10.2 proof standard |
| SP-GO-06 | Outbound audit identity meets the §4.3 minimum on all successful sends |
| SP-GO-07 | skill_manage disarmed at the runtime level |
| SP-GO-08 | No held tool, commerce or multi-location action reachable per the §4.4 definition through the shared gateway |
| SP-GO-09 | Tenant and customer data isolation VERIFIED per §4.5; no cross-tenant exposure path exists |
| SP-GO-10 | Exact-release deployment evidence per §1.5.1 recorded for the pilot candidate, including deployment-state preservation |

## 5. Catering assessment

### 5.1 Evidence-backed capability matrix

Classify each capability (Production-ready / Working but incomplete / Implemented but dormant / Unreliable or unsafe / Not implemented / UNVERIFIED), with evidence:

Inbound lead capture; lead deduplication and event identity; lead qualification; event-detail extraction; menu generation; vegetarian/non-vegetarian balance; proposal generation; pricing and quote workflow; owner approval; customer selection handling; amendments; follow-up automation; lead prioritization; pipeline visibility; outbound lead generation; referral and partner tracking; campaign management; upselling; post-booking operational handoff; repeat-business automation; reviews and referrals; revenue attribution; reporting and analytics; audit completeness; human takeover (per §5.5); STOP/opt-out handling.

### 5.2 Pilot scope definition

Define the smallest safe and valuable version for the supervised client pilot, prioritizing: reliable inquiry capture; zero duplicate-lead errors; concise qualification; coherent event-specific menus; deterministic proposal generation; selection plus quote handling; owner approval; one logical response per customer turn (§4.2); follow-up visibility; complete outbound-message audit identities (§4.3); live-verified human takeover (§5.5); safe rollback and kill switch.

Identify: what is ready; what is deployed; what is merged but not deployed; what blocks pilot readiness; which issues are safety-critical; which quality issues may be improved during a supervised pilot.

### 5.3 Menu and proposal quality (pilot-relevant assessment)

Assess whether menu generation produces coherent catering packages rather than random catalog combinations. A mixed wedding menu should support: welcome drinks; vegetarian appetizers; non-vegetarian appetizers; vegetarian mains; non-vegetarian mains; rice or biryani; breads; sides; desserts; optional live stations. Determine which parts must remain deterministic, which may use Hermes for interpretation or presentation, and where owner-approved business rules are required. (Package tiering — value/balanced/premium — is Part 2.)

### 5.4 STOP/opt-out semantics

> After a customer opts out or asks to pause, the system may send **at most one deterministic acknowledgment** confirming the suppression. After that acknowledgment, no automated customer-facing message may be sent unless the customer initiates a new conversation and policy permits re-entry. Owner messages and internal operational alerts do not count as customer sends.

The Part 1 assessment must additionally define:

* whether "pause" is temporary and, if so, its duration semantics and how it differs from opt-out;
* how re-entry occurs when the customer returns;
* owner override behavior — **owner override must not silently reactivate automation**; a human owner may message the customer manually, but automated sending resumes only through an explicit, audited re-enable action.

### 5.5 Human takeover requirements

Catering cannot receive GO unless human takeover is **live-verified**. When takeover is active:

* automated customer replies stop;
* queued automated follow-ups are suppressed;
* owner and agent messages cannot interleave;
* takeover and release are audited and idempotent;
* customer context is preserved;
* rollback preserves the takeover state.

### 5.6 Catering GO criteria

Catering cannot receive GO unless Shared Platform is GO **and** all of the following carry VERIFIED evidence records on the exact release candidate:

| ID | Criterion |
|---|---|
| CAT-GO-01 | Zero duplicate-lead errors across the approved replay corpus |
| CAT-GO-02 | Each inbound creates at most one logical customer reply per the §4.2 definition |
| CAT-GO-03 | Proposal and selection workflows are idempotent |
| CAT-GO-04 | Final pricing and commitments remain deterministic and owner-approved; Hermes cannot fabricate pricing |
| CAT-GO-05 | Customer and owner sends meet the §4.3 audit-identity minimum, with separate outbound identities |
| CAT-GO-06 | All known Catering incident transcripts pass as frozen replay fixtures |
| CAT-GO-07 | No held tool, commerce or multi-location action reachable from the Catering path per §4.4 |
| CAT-GO-08 | Supervised phone testing passes on the deployed exact release candidate |
| CAT-GO-09 | STOP/opt-out behavior per §5.4 verified in replay and live-parity testing |
| CAT-GO-10 | Human takeover live-verified per §5.5 |
| CAT-GO-11 | All Catering business invariants CAT-INV-01…10 (§5.7) VERIFIED |

### 5.7 Catering business invariants (each requires its own evidence record)

| ID | Invariant |
|---|---|
| CAT-INV-01 | An ambiguous duplicate-event inquiry creates neither a lead nor a proposal set before clarification |
| CAT-INV-02 | A deterministically distinct event creates exactly one lead without a contradictory warning |
| CAT-INV-03 | Mixed-event menus contain complete vegetarian and non-vegetarian sections |
| CAT-INV-04 | Incomplete menu composition fails closed without unrelated catalog filler |
| CAT-INV-05 | Selection plus pricing selects once and advances the quote workflow once without resending menus |
| CAT-INV-06 | Redundant selection causes no reselection, refinalization, second proposal set or duplicate owner card |
| CAT-INV-07 | Exactly one owner card is created for each approval transition |
| CAT-INV-08 | Final pricing, availability and date commitments require deterministic validation and owner approval |
| CAT-INV-09 | Customer and owner sends retain separate outbound identities |
| CAT-INV-10 | A physical transport split preserves one logical outbound identity |

## 6. Shift assessment

### 6.1 Shift capability matrix

Provide an evidence-backed matrix (same classification scale and evidence-record standard) covering:

* interpretation and orchestration;
* tool access;
* external mutations;
* turn-budget enforcement;
* paging;
* audit;
* human takeover;
* kill switch;
* rollback.

### 6.2 Shift fixture enumeration (required)

The eight-turn gate and the seven dark toolsets must be pinned to a **fixed, version-controlled repository location** containing:

* the eight-turn transcript;
* expected turn-by-turn outcomes;
* the seven toolset identifiers, enumerated by name;
* the exact denial evidence expected for each toolset at each §4.4 boundary.

References to "the eight-turn gate" or "seven dark toolsets" without this enumeration do not satisfy the claims gate.

### 6.3 Shift GO criteria

Shift cannot receive GO unless Shared Platform is GO **and** all of the following carry VERIFIED evidence records on the exact release candidate:

| ID | Criterion |
|---|---|
| SHF-GO-01 | The real-gateway eight-turn gate passes against the enumerated fixture |
| SHF-GO-02 | All seven enumerated dark toolsets inaccessible from the Shift path per §4.4 |
| SHF-GO-03 | skill_manage disarmament confirmed specifically for the Shift runtime path |
| SHF-GO-04 | Shift-specific kill-switch and rollback behavior exercised live |
| SHF-GO-05 | Shift supervised testing passes on the deployed exact release candidate |

Shift-specific criteria are **not** prerequisites for Catering.

## 7. F7 versus Hermes-first adjudication

### 7.1 Per-turn-category adjudication

For every customer-turn category, report: current routing owner; current model, if any; minimum model tier required; whether the decision is linguistic, safety-critical or mutation-authorizing; whether ambiguity can safely result in clarification; whether Hermes-first, deterministic-first or a hybrid path is appropriate; evidence required before changing the current behavior.

The final architecture question — "Are we using Hermes as the primary conversational intelligence layer, or building a traditional rules engine around it?" — must be answered honestly, acknowledging that the current deterministic-first posture is deliberate and post-incident, and specifying what evidence would justify relaxing it per turn category.

### 7.2 Hermes migration requirements

Every migration from deterministic-first to Hermes-first must state, before approval:

* an explicit **model tier / model floor, approved by the product owner (Srini)** — never self-certified by the PR author;
* estimated **cost** per turn at that tier;
* expected **latency**;
* measured routing **accuracy** (shadow comparison);
* deterministic **fallback** path;
* replay testing against known incidents;
* send-count comparison;
* duplicate-state comparison;
* feature flag;
* rollback path;
* live activation gate.

### 7.3 Model economics — measured versus projected

**Measurable in Part 1 (may be VERIFIED):** tokens and model cost per turn type; cost per qualification; cost per proposal; latency; owner intervention rate by model tier.

**Projection-only before pilot volume exists (must be labeled PROJECTED, never VERIFIED):** cost per converted booking; conversion-impact estimates. These become measurable only after an attributed pilot cohort matures.

Recommend the least expensive model tier that meets the required reliability floor. Do not optimize line count while ignoring inference cost, latency or conversion impact.

## 8. WhatsApp pilot-policy mapping (Part 1 scope)

### 8.1 Mapping dimensions

For every outbound message or follow-up state within pilot scope, document: inside or outside the 24-hour customer-service window; free-form messaging vs. approved template required; consent basis; opt-out and STOP handling; template category; frequency limit; quality-rating and account-health risk; BSP or template-approval dependency; owner approval requirement.

### 8.2 Scope split

**Part 1 (pilot) mapping covers:** proposal follow-up; decision-deadline reminders; owner-action reminders; any message the agent could send during a supervised pilot; STOP/opt-out mechanics per §5.4.

**Deferred to Part 2:** follow-up-later scheduling at scale; seasonal re-engagement; abandoned-inquiry recovery; annual-event reminders; review requests; referral requests; promotional campaigns.

### 8.3 Owner daily digest — placement

The owner WhatsApp digest is a **non-gating early-pilot enhancement**. It appears in the Part 2 owner-experience design (§14) and is excluded from Part 1 gating **unless explicitly promoted into Part 1 by the product owner**, in which case it must then satisfy the §8.1 policy mapping, §4.3 audit identity, deduplication and owner opt-out requirements before pilot use. Pilot supervision does not depend on the digest; owner visibility during the supervised pilot is provided by direct oversight and the takeover mechanism (§5.5).

### 8.4 WhatsApp dependency evidence

Do not carry a fixed BSP/Meta approval estimate. Instead, record as review evidence:

* the actual BSP;
* account verification status;
* template approval status;
* provider-confirmed or measured lead time;
* current go-to-market blockers.

Autonomous bulk outreach is not authorized at any point in this review.

## 9. Incident replay coverage

### 9.1 Frozen replay suite — minimum contents

* stale flyer project swallowing a fresh wedding inquiry;
* 28-send gateway spiral;
* mixed 180-guest wedding inquiry requesting two menus;
* duplicate-event ambiguity;
* distinct-event creation;
* "Option 2 + quote and prices" compound intent;
* redundant "Option 2" follow-up;
* branch-B amendment capture success;
* branch-B amendment capture failure;
* missing outbound-message ID;
* recovery-watchdog timer reversal (deployment-state-preservation fixture, per §1.5.1).

Every routing, orchestration, model, prompt or toolset change must run this suite. **Every confirmed production incident must add a replay fixture before its corrective PR merges.** The suite must run against the exact release candidate (§1.5.1).

### 9.2 Suite governance

* **Fixed, version-controlled location** in the repository; the frozen suite is enforceable only if its location and contents are under version control.
* **Additive-only for behavioral purposes:** new incidents are appended; the suite grows for the life of the product; transcripts are never rewritten to make tests easier.
* **Privacy/security exception:** a fixture may be replaced or redacted **only** to correct a privacy, security, legal, or factual defect. Such a change requires reviewer approval, preservation of routing-relevant structure, and a repository record linking the superseded and replacement fixture hashes. Exposed PII is never preserved merely to satisfy additive-only governance.
* **PII-safe pseudonymization:** real transcripts are pseudonymized to strip customer names, phone numbers and identifying details while preserving routing-relevant structure — message ordering, timing, phrasing patterns and compound-intent shape.

### 9.3 Fixture oracle format and execution safety

Each frozen replay fixture must contain:

* pseudonymized transcript;
* initial state;
* expected route;
* expected mutations;
* forbidden mutations;
* expected logical-send count;
* expected audit identities;
* expected final state.

**Execution safety:**

* The suite **defaults to fake transport**.
* Live-parity execution may use **only an explicitly allowlisted operator test number**.
* Fixture data must **never contain a real customer destination**, and the harness must **fail closed on unknown destinations**.

**Oracle integrity:** expected outcomes may not be removed or weakened without **product-owner and reviewer approval**.

## 10. Rollback and kill-switch evidence

### 10.1 In-flight conversation behavior

Define rollback behavior for: conversations mid-qualification; leads created under the new version; proposals awaiting selection; selections awaiting owner approval; queued messages; pending follow-ups; audit values introduced by the new release; feature-flagged or allowlisted conversations; **active takeover state (§5.5)**.

### 10.2 Rollback proof standard

Rollback must be proven — not asserted — to not: duplicate leads or proposal sets; resend previous responses; lose selections; regress quote status; make persisted state unreadable; restart the customer journey; activate held capabilities; **discard or reset an active human-takeover state**.

**Proof method (required, because these are negative claims):**

1. Before/after **state diff** across a rollback exercise;
2. The full frozen replay suite run **against the rolled-back version**;
3. Both performed in a **production-parity environment** per the §1.5 definition (allowlisted parity tenant/operator number permitted);
4. Rollback target and backup artifact identified per §1.5.1.

## 11. Part 1 deliverables

1. Executive assessment.
2. Deployment ground truth per §1.5.1: exact release SHA, tag/ref, diff from production, artifact hashes, process counts, WhatsApp connection/queue state, systemd/operator state, before/after production-state counts, model floors, tenant configuration, feature flags.
3. Shared Platform gate ledger (evidence records for SP-GO-01…10).
4. Catering evidence-backed capability matrix, gate ledger (CAT-GO-01…11) and invariant ledger (CAT-INV-01…10).
5. Shift capability matrix (§6.1), fixture enumeration (§6.2) and gate ledger (SHF-GO-01…05).
6. Immediate pilot blockers (per gate level), with the smallest evidence plan for each unresolved gate.
7. F7 versus Hermes-first adjudication table.
8. WhatsApp pilot-policy mapping and dependency evidence (§8.4).
9. Incident replay coverage report, including fixture-oracle completeness (§9.3).
10. Rollback, kill-switch and takeover evidence.
11. Safety findings — any current path where Hermes can: create duplicate business records; fabricate pricing; bypass workflow state; make irreversible commitments; trigger repeated sends; lose audit identity; access a held or dark toolset; or cross a tenant boundary — with focused corrections proposed.
12. Evidence-enablement change list (per §1.6), separated from product implementation.
13. **Three explicit GO/NO-GO rulings:** Shared Platform, Catering, Shift.
14. **One prioritized queue of up to five immediately actionable PRs.** If more than five are identified, rank the remainder as deferred backlog. Do not combine unrelated safety changes merely to satisfy the limit; do not pad the queue to reach five. Each PR states: business outcome; architecture change; files/components affected; safety risk; replay tests; rollout control; activation gate; rollback; measurement plan.

---

# PART 2 — GROWTH AND SIMPLIFICATION

Part 2 is returned after Part 1 review. It does not block the supervised pilot unless it identifies a new safety-critical defect.

## 12. Custom-code inventory

Inventory all Catering-specific code: intent classifiers; keyword and regex matchers; conversation routers; fresh-versus-follow-up logic; compound-intent handling; menu assemblers; proposal composers; response templates; duplicate-event detection; amendment handling; follow-up scheduling; owner notifications; outbound-message composition; audit wrappers; tool adapters; agent-specific scripts.

For each component: file and function; approximate LOC; responsibility; why it exists; whether Hermes can replace it; whether it should remain deterministic; whether it duplicates another component; replacement risk; recommendation (retain / simplify / consolidate / replace with Hermes / delete).

### 12.1 Code-reduction principles

* **Replace custom interpretation logic with Hermes** when the decision is linguistic or contextual; the output fits a typed schema; incorrect interpretation can safely result in clarification; and the decision does not directly authorize an irreversible mutation. Do not add more regex rules per new customer phrase.
* **Retain deterministic validation.** Hermes output passes through typed schemas and deterministic policy (intent; event details; missing fields; selected proposal; requested action; confidence; clarification requirement). Deterministic code decides whether the action is allowed and what state transition occurs.
* **Consolidate business operations** behind generic deterministic operations: `create_or_match_event`, `record_customer_requirements`, `generate_proposal_request`, `record_proposal_selection`, `request_quote`, `capture_amendment`, `schedule_follow_up`, `request_owner_approval`. Hermes maps customer language into these stable operations.
* **Reduce response templates.** Deterministic code returns structured facts and outcomes; Hermes presents them naturally while preserving prices, dates, guest counts, menu items, approval status, reference numbers and required disclaimers. Fixed templates only for safety-critical, failure, compliance or retry messages.
* **Eliminate duplicate orchestration.** One inbound message → one Hermes interpretation → one deterministic action plan → one mutation boundary → one composed customer response (per the §4.2 definition).
* **Prefer reusable platform capabilities** before adding Catering-specific code.

### 12.2 Deterministic-kernel responsibilities (never migrated to model reasoning)

Customer, tenant, lead and event identity; duplicate detection; event-key construction; authorization; tenant isolation; workflow state transitions; idempotency; persistence; audit records; owner approvals; pricing calculations; discounts and margin rules; availability commitments; payment and contract actions; menu-policy validation; required-section validation; customer-send budgets; transport deduplication; kill switches; rollback; tool permissions; irreversible business mutations; outbound volume limits.

### 12.3 Measurement

Baseline and targets: total Catering-specific production LOC; custom classifiers and routers; regex/keyword rules; hard-coded customer templates; independent intercept paths; number of Hermes tools; duplicate business operations; test burden by subsystem; and model cost per turn (measured) with cost per converted lead (projected until pilot cohort matures, per §7.3). Raw line-count reduction is never the sole success metric — a smaller system that is less safe is not an improvement.

## 13. Hermes toolset redesign

Assess the current Catering toolset. The ideal surface is small, typed and business-oriented. No low-level file, database or mutation tools exposed to Hermes. Prefer: read customer and event context; search approved menu catalog; validate proposed menu structure; create or update a lead via authorized command; request owner pricing approval; record customer selection; capture an amendment; schedule a follow-up; retrieve proposal status; escalate to a human.

Each tool must have: narrow typed input schema; typed result; explicit authorization; idempotency; tenant scoping; audit identity; deterministic error behavior.

Also assess Hermes reasoning context quality: system instructions; tenant business profile; menu catalog; service policies; pricing-policy boundaries; event history; prior customer messages; current lead and proposal state; owner preferences; allowed tools; prohibited actions. Where Hermes performs weakly, first determine whether the cause is incomplete context, weak tool descriptions, poor schemas, missing examples, conflicting prompts, excessive tool access, incorrect memory retrieval or premature deterministic interception — before adding routing code.

## 14. Owner experience

**Early pilot enhancement (non-gating):** the owner WhatsApp digest per §8.3 — new leads; qualified and high-priority leads; proposals requiring approval; customer selections; follow-ups due; events at risk; pipeline value; failures requiring owner attention. Before activation it must satisfy the §8.1 policy mapping, §4.3 audit identity, deduplication and owner opt-out requirements. The owner must be able to open the relevant lead or take over the conversation (§5.5).

**Longer term:** a daily operating view covering new leads; qualified leads; urgent opportunities; proposals awaiting approval; customer decisions; follow-ups due; high-value events; events at risk; potential pipeline revenue; booked revenue; lost opportunities and reasons; actions requiring owner attention. Do not build an extensive dashboard before confirming which information owners actually use.

## 15. Lead generation

**Inbound growth:** WhatsApp; website forms; Google Business Profile; Instagram and Facebook; QR campaigns; referrals; venue and event-planner partners.

**Proactive growth:** re-engaging past customers; following up on abandoned inquiries; seasonal campaigns; annual-event reminders; corporate recurring-event outreach; referral requests; owner-approved opportunity outreach.

For all proactive outreach, identify consent, platform-policy, anti-spam, owner-approval and reputation risks, and complete the full WhatsApp lifecycle mapping (per §8.1 dimensions) for the deferred states. Autonomous bulk outreach is not authorized by this review.

## 16. Follow-up expansion

Design the full controlled follow-up lifecycle: proposal delivered; proposal viewed/acknowledged; customer requested changes; customer selected an option; pricing requested; tasting requested; owner action required; decision deadline approaching; customer declined; follow up later; booked; lost.

The agent stops following up per the §5.4 STOP semantics (also Catering GO criterion CAT-GO-09). Identify which follow-ups can be automated safely and which require owner approval, with each state carrying its WhatsApp policy classification.

## 17. Pricing and margin roadmap

Assess the pricing architecture required to support: per-person pricing; minimum order quantities; delivery; staffing; setup and cleanup; rentals and equipment; live stations; taxes and service charges; holiday or weekend premiums; approved discounts; target margins; low-margin escalation.

Hermes must not invent prices. Final pricing and exceptional discounts remain governed by deterministic rules and owner approval. Pricing implementation remains separately gated unless already authorized.

## 18. Analytics and attribution

Pilot measurement plan — at minimum: first-response time; qualified-lead rate; duplicate-lead rate; average customer turns to proposal; proposal acceptance rate; inquiry-to-booking conversion; owner interventions per lead; follow-ups completed; average event value; response failures; repeated-response incidents; abandoned inquiries recovered; revenue influenced by the agent; customer opt-outs or complaints.

**Metrics rules:**

* Do not treat WhatsApp read receipts as a reliable proposal-view metric. Use supported proxies: customer acknowledgment; reply after proposal; selection; change request; pricing request; tasting request; owner-recorded outcome.
* Every metric must identify: exact evidence source; baseline period; attribution logic; known limitations; VERIFIED / PROJECTED / UNVERIFIED status per §7.3.
* Do not claim revenue impact without attribution evidence.

## 19. Deprecation sequence

Propose a ranked (by value and risk) sequence of small, measurable migrations — not a large rewrite. Examples: replace one deterministic linguistic classifier with Hermes plus typed output; consolidate response-producing branches into one turn coordinator; replace hard-coded response construction with structured outcome plus Hermes presentation; consolidate duplicate scripts behind one deterministic business operation; delete a superseded path only after shadow comparison and replay tests prove equivalence.

Every replacement must include: replay tests from real (pseudonymized) customer conversations against §9.3-format fixtures; shadow-mode comparison where appropriate; no increase in duplicate records; no increase in customer-facing sends; deterministic fallback; feature flag; rollback path; explicit activation gate; and the full §7.2 migration statement (model tier, cost, latency, accuracy, fallback, product-owner approval).

## 20. Long-term phased roadmap

* **Phase A — Safe client pilot:** only the capabilities required to handle and convert inbound catering inquiries reliably.
* **Phase B — Sales operations:** pipeline, follow-up, lead scoring, owner dashboard, conversion reporting.
* **Phase C — Growth:** past-customer reactivation, referral programs, campaigns, partner lead channels.
* **Phase D — Fulfillment and retention:** operational event handoff, repeat bookings, reviews, referrals, customer-lifetime-value workflows.

Each phase includes: customer or owner problem; proposed capability; existing reusable components; gaps; risks; dependencies; tests; rollout controls; success metrics; explicit activation gate.

## 21. Part 2 deliverables

1. Full custom-code inventory with retain/simplify/consolidate/replace/delete recommendations.
2. Hermes responsibility matrix and deterministic-kernel responsibility matrix.
3. Current and proposed minimal Hermes toolset.
4. Prompt and context-quality assessment.
5. Duplicate routing and orchestration findings.
6. Code-reduction opportunities ranked by value and risk, with before/after code, complexity and cost metrics.
7. Owner-experience proposal (digest-first, per §8.3 placement).
8. Lead-generation strategy with full WhatsApp lifecycle mapping.
9. Follow-up lifecycle expansion.
10. Pricing and margin architecture assessment.
11. Analytics and attribution plan.
12. Deprecation sequence with test and rollback strategy per replacement.
13. Long-term phased roadmap (A–D).

---

## Appendix A — Dev-session engagement protocol

The Part 1 investigation session opens with a strict instruction: **do not produce a broad architecture essay and do not begin coding. The first output is a gate ledger, not recommendations.**

Return, in this order:

1. **Deployment ground truth per §1.5.1:** exact release SHA and ref, diff from production, artifact hashes, gateway/bridge process counts, WhatsApp connection and queue state, systemd/operator state, model floors, tenant configuration, feature flags.
2. **Shared Platform gate ledger:** every SP-GO criterion (01–10) marked VERIFIED, FAILED, or UNVERIFIED with §1.4 evidence records.
3. **Smallest evidence plan** for each unresolved Shared Platform gate, distinguishing pure investigation from §1.6 evidence-enablement changes.
4. Only after the Shared Platform assessment: **Catering gate and invariant ledgers (CAT-GO-01…11, CAT-INV-01…10) and Shift matrix and gate ledger (SHF-GO-01…05).**
5. **Evidence-enablement change list**, separately labeled, separated from product implementation, awaiting product-owner approval.
6. **Three preliminary GO/NO-GO rulings.**
7. **Up to five proposed PRs** (plus deferred backlog if applicable), with no PR started until product-owner and reviewer approve Part 1.

The first session does not attempt the Part 2 inventory. Its priority is discovering whether the supervised pilot has a safe platform on which to run.

---

## Sign-off

| Role | Name | Approves | Decision | Date |
|---|---|---|---|---|
| Product owner | Srini | Charter, model floors, evidence-enablement changes, temporary Shared Platform profiles, replay-oracle changes, digest promotion, pilot scope, GO/NO-GO acceptance, budget extensions | | |
| Reviewer | (designated at kickoff) | Part 1 findings before implementation streams open; co-approves replay-oracle changes and temporary Shared Platform profiles | APPROVE WITH AMENDMENTS (v1.0 → v1.1 → v1.2 final) | |

**This charter approval authorizes the Part 1 investigation only. No product implementation or pilot activation is authorized. No product implementation begins until Part 1 receives product-owner and reviewer approval.**
