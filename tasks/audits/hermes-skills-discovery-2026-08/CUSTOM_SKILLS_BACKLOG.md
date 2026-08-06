# CUSTOM_SKILLS_BACKLOG

Proposed organization skills, ordered by value. **Consolidated from the brief's ~25 candidates
to 13** — the brief invited better boundaries, and a smaller set directly reduces the routing
ambiguity identified in Part 11.

**Namespace** (proposed, not created): `org/shared/`, `org/shift/`, `org/catering/`,
`org/flyer/`, `org/ops/`. Gecko and Vizora namespaces are owned by those workstreams.

**Universal prohibitions** — every skill below is forbidden from: setting or inventing prices,
menu items, availability, taxes, minimums; asserting approval state; sending outbound messages;
mutating persistent state; performing destructive operations; bypassing identity or authorization.
All of these remain deterministic. Skills **report and recommend**; deterministic code decides
and acts.

---

## Tier 1 — highest value, author first

### 1. `org/catering/inquiry-intake`
- **Purpose** Warm acknowledgment plus **at most three** high-value, easily-answered questions.
  Never a premature or fabricated quote.
- **Consolidates** the brief's `catering-inquiry-intake` + `catering-requirements-completeness`
  (the completeness model is what *selects* the questions — one skill, not two).
- **Target** catering-agent (`parse_catering_inquiry`, `catering_dispatcher`)
- **Inputs** inbound message, prior-lead context, the deterministic requirements checklist
  (date, location, guest count, serving time, delivery vs on-site, buffet vs plated, dietary,
  budget band, staffing/equipment)
- **Outputs** structured `missing_fields[]` + a ranked question set + suggested ack copy
- **Permitted tools** read-only lead/menu lookup
- **Prohibited** any price, package, minimum, or availability claim
- **Evidence contract** must emit which fields are known vs missing and why each question was
  chosen
- **Test plan** replay the existing catering fixtures; assert no price token appears in a
  first response and that ≤3 questions are asked
- **Overlap** extends `parse_catering_inquiry`; does not replace it

### 2. `org/flyer/exact-edit`
- **Purpose** When one element is requested, change **only** that element; enumerate untouched
  regions as evidence.
- **Consolidates** `flyer-exact-edit` + `flyer-reference-preservation` (both are "don't drift
  from what was given")
- **Target** flyer-studio (`flyer_generation`, source-edit path)
- **Outputs** edit-scope declaration + post-edit diff report
- **Evidence contract** explicit before/after region manifest
- **Test plan** the audit's recurring-failure fixtures: single-element edit must leave logo, QR,
  footer, dimensions provably unchanged
- **Overlap** complements existing `visual_qa` / `creative_firewall`; consumes their output

### 3. `org/flyer/asset-integrity`
- **Purpose** Assert logo and QR identity across an edit.
- **Consolidates** `logo-and-qr-integrity`
- **Critical boundary** the **comparison is deterministic** (hash/perceptual diff); the skill
  only interprets and reports the result. It must never *judge* whether a QR "looks right".
- **Test plan** deliberately corrupted-logo and swapped-QR fixtures must fail closed

### 4. `org/shared/runtime-effective-diagnosis`
- **Purpose** Encode the discipline that corrected three wrong conclusions in this very
  engagement: **a config value's name, file contents, or path never establish its effect — only
  the consuming code path on the runtime-loaded process does.**
- **Consolidates** the brief's `runtime-effective-diagnosis` + `hermes-fleet-validation` +
  `shift-production-diagnosis` + `production-incident-investigation`
- **Procedure** derive the active process → read `/proc/<pid>/environ` and `cmdline` → resolve
  the interpreter and `HERMES_HOME`/package root → locate the consuming code → only then
  conclude
- **Authoring route** `/learn` from this session's transcript — it contains three worked
  examples with the failure and the correction
- **Test plan** replay: given a `.env` claim, the skill must refuse to conclude without process
  evidence

## Tier 2

### 5. `org/catering/dietary-clarification`
Jain / no-onion-no-garlic / allergen clarification. Must ask, never assert what the kitchen can
accommodate — capability is deterministic business data.

### 6. `org/catering/proposal-from-approved-data`
Compose proposals **strictly** from approved pricebook records, each line carrying provenance.
**Fails closed** when a price is absent rather than estimating. Directly encodes the existing
"provenance not reconstruction" pricing-kernel rule.

### 7. `org/flyer/fabrication-check`
Every price, offer, product, and claim traceable to approved data or blocked. Complements the
deployed fact-safety layer rather than replacing it.

### 8. `org/shift/routing-audit`
Sender × state × intent grid audit. The practice exists (`tasks/audits/routing-validation-2026-07.md`)
but is not encoded; re-running it is currently manual.

### 9. `org/shift/gateway-and-plugin-validation`
- **Consolidates** `shift-plugin-review` + `whatsapp-gateway-validation` +
  `composite-toolset-enforcement-check` — all three are "prove the gateway is loading what we
  think, with the constraints we think"
- Encodes the `shift-agent-policy-preflight` A–D pattern as a reusable investigation

### 10. `org/ops/incident-evidence-report`
Interpret a watchdog/timer failure and produce an operator-facing evidence report.
**This is the single skill that serves the ~8 deterministic workflows with indirect need** —
authored once rather than per-timer.

## Tier 3

### 11. `org/shared/evidence-based-completion`
Refuse to claim completion without named artifacts and commands run. Guards the failure mode
this engagement repeatedly hit.

### 12. `org/shared/pr-review-and-ruling`
Encode the multi-vector review + explicit ruling format already practised.

### 13. `org/shared/skill-lifecycle`
- **Consolidates** `skill-security-review` + `skill-promotion-and-rollback`
- **Purpose** the inspection checklist plus an **enforceable** quarantine/promotion state —
  directly addresses Finding S-1 (zero disabled skills fleet-wide) and the unenforced
  `rest-graphql-debug` ruling
- **Highest structural value**, deliberately placed last because it should be authored *after*
  Wave 0 has exercised the process manually

---

## Deliberately NOT proposed

| Brief candidate | Disposition |
|---|---|
| `org/catering-follow-up`, `org/catering-human-escalation` | Fold into one `org/catering/follow-up-and-escalation` **only if** the deployed follow-up engine (currently OFF) is activated; otherwise premature |
| `org/flyer-and-menu-qa` | Overlaps deployed `visual_qa` — extend that rather than add a skill |
| `org/print-dimension-validation`, `org/brand-compliance-review` | Deterministic assertions (aspect ratio, DPI, bleed, brand palette). Implement as **code**, not a skill; a skill would only re-report them |
| `org/shift-production-diagnosis` | Absorbed into `org/shared/runtime-effective-diagnosis` |
