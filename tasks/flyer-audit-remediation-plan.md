# Flyer Studio E2E Audit — Remediation Plan (2026-07-13)

**Drift-check tag:** `extends-Hermes` — all fixes tune net-new flyer business logic
(routing classifiers, the regex extractor, the visual-QA gate, brand-asset audit)
sitting on top of Hermes substrate. No Hermes primitive is modified; no storage/audit
convention is changed. One fix adds a `Literal` variant to an existing schema enum.

Source of findings: `tasks/audits/flyer-studio-e2e-adversarial-audit-2026-07-13.md`
(20 findings, read-only audit). Base: `origin/main` @ `2711436`. Worktree:
`.claude/worktrees/flyer-audit-remediation-20260713`, branch
`feat/flyer-audit-remediation-20260713`.

## Hermes-first capability checklist

| Step | Hermes or net-new? | Note |
|---|---|---|
| 1. Inbound WhatsApp / media / identity | `[Hermes]` — source ingestion + `sender_role` | untouched |
| 2. Route reply → approval/revision/new/echo | `[net-new]` cf-router classifiers | BC-3/4/5, AN-2/3 tune existing pure funcs |
| 3. Extract menu facts from text | `[net-new]` legacy regex extractor | BC-1/2, SW-2/3/4 — **and** `[Hermes-LLM]` v2 is the long-term substrate (deferred track) |
| 4. Vision extraction of uploaded flyer | `[Hermes]` vision gateway | SW-1 fix is about the **default role** we hand Hermes, not the vision itself |
| 5. Decide identity-source vs style-only | `[net-new]` brand-safety default | SW-1a — flips an existing default |
| 6. Visual-QA gate (fabrication/brand block) | `[net-new]` QA gate | SW-1b backstop, IN-1 |
| 7. Brand-asset state + audit chain | `[Hermes]` audit chokepoint | AN-4 wires an audit row through `log-decision-direct` |
| 8. Customer closure message on failure | `[Hermes]` outbound | IN-3 sends through existing send path |

**Verdict:** 5 `[Hermes]` / 5 `[net-new]` — passes the red-flag test (not majority net-new).
The net-new items are per-customer flyer classifiers, extractor rules, and QA rules — not
re-implemented substrate. The one place Hermes *is* the answer (LLM extraction of rupee/bare/
code-mix menus) is the deferred **v2 graduation track**, deliberately NOT bundled here.

## Drift-rule self-checks (read deployed code before drafting)

- ✅ Read `src/platform/schemas.py` (`FlyerManualReviewReason` Literal at lines 718-732) — confirmed no `price_conflict` member exists; SW-4 adds one variant.
- ✅ Read `src/plugins/cf-router/actions.py` (`_FLYER_APPROVAL_ALIASES` at 2049, `_FLYER_INTENT` at 1531, `is_flyer_approval_text` at 2064) — confirmed exact-match 9-token allowlist and single-regex intent gate.
- ✅ Read `src/agents/flyer/facts.py` (item/price loop 300-403, `category_suffix` append at 347) — confirmed `$`-only price anchoring and the `Dosa → Dosa Biryani` append.
- ✅ Read `src/agents/flyer/semantic_brief.py` (`visible_wrong_brand_blockers` masthead scan 675-714, `_ORG_SUFFIX_RE` escape at 705) — confirmed org-suffix requirement lets no-suffix competitor names through.
- ✅ Read `src/agents/flyer/extraction_seam.py` (fallback path 62-86) — confirmed legacy is v2's fail-closed fallback, so legacy patches are permanent floor-safety.
- ✅ Read `src/agents/flyer/extraction_v2.py` (`value_has_source_parity` 134-142) — confirmed token-SET (not adjacency) parity → v2 does not auto-fix SW-2 recombination.

## Operator decisions (2026-07-13, recorded)

- **Fork 1 (extraction):** Patch legacy **now** as the deterministic, pytest-provable floor.
  v2 graduation is a **separate track** with its own validation. Rationale (operator):
  `extraction_seam.py:62-86` makes legacy the **fail-closed fallback** for v2 — legacy is not
  the path being replaced, it is the path that runs whenever v2 times out / parity-rejects.
  A broken safety net is not a safety net → legacy patches are mandatory regardless.
- **Fork 2 (wrong-brand):** Root-cause at **ingest** — invert the default so a theme/reference
  upload is **style-only unless it is the owner's own registered identity or explicit confirm**;
  keep the QA backstop but make it **fail-closed on any unexplained masthead-shaped line**
  (drop the org-suffix escape). Broader detection *strategy* (curated lists / proxy-blur) stays
  a separate decision.
- **Fork 3 (scope):** One deterministic batch (below). No LLM-substrate creep into a
  "fix the typos" PR (PR-B2 scaffolding-scope-creep lesson).

## Parity-guard audit (done this session — de-risks the v2 track)

`extraction_v2.value_has_source_parity` (134-142) is **token-SET membership**, not adjacency:
every alphanumeric token of a fact value must appear *somewhere* in the brief as a whole token.
Consequences:
- **SW-2 is NOT auto-fixed by v2:** "Dosa Biryani" passes parity when the brief contains "dosa"
  and "biryani" separately (recombination, not hallucination). → legacy SW-2 fix stays the floor.
- **Numeric parity is weak** ("$120" passes if "120" appears anywhere); **occasion is not
  parity-checked** (IN-4 trust surface). QA cannot backstop a mis-locked price.
→ Confirms sequencing: legacy = deterministic floor now; v2 = eyes-open, corpus-validated later.
The "10/10 vs 5/10" benchmark is not taken on its word.

---

## Batch 1 — deterministic pilot-safety (this session, TDD, one branch)

Every item ships a failing-first test (project rule: every documented invariant gets a test).
Clusters are file-isolated so they can be built in parallel without conflict.

### Cluster A — Routing & approval  (`src/plugins/cf-router/actions.py`)
- **BC-3** Broaden `_FLYER_APPROVAL_ALIASES` to natural approvals (perfect, looks great, great,
  yes please, yep, yeah, okay, love it, that works, ship it, good to go, send it out, 👍/🙏, done).
  Keep **exact-match** semantics so "looks good but change the date" is NOT approval (won't match) —
  edit-request safety falls out for free.
- **AN-2** Normalize decorated approvals before match: strip curly quotes `“ ”`, WhatsApp bold
  `*…*`, trailing emoji → `*APPROVE*`, `“APPROVE”`, `looks good 👍` all approve.
- **AN-3** Broaden `classify_flyer_quote_echo_choice` → "make a new one"/"new one"/"the second
  one"/"1"/"2"/"option 2" map to new/approve/choice.
- **BC-4** Bounded typo tolerance on flyer-intent: edit-distance-1 match against core nouns
  (flyer/flier/poster/banner) so `flyr`/`postr` route to Flyer. Bounded, deterministic.
- **BC-5** Add a festival keyword list (diwali, holi, pongal, sankranti, ugadi, navratri,
  dussehra, onam, eid, ramadan/ramzan, ganesh chaturthi, christmas, new year, thanksgiving,
  july 4) to `_FRESH_FLYER_BRIEF_DETAIL` so "new flyer for diwali" reads as fresh new work.

### Cluster B — Legacy extraction  (`src/agents/flyer/facts.py`)
- **BC-1** Accept `₹` / `Rs` / `Rs.` / `rupees` and **bare** numeric prices (currency preserved
  as written; QA already understands `[$₹]` at visual_qa.py:63). Today everything is `$`-anchored.
- **BC-2** Extract bare name-only menu lines (newline/comma-separated short noun phrases, ≤5
  words, no sentence punctuation/verbs) under a menu context — **bounded** to avoid turning every
  line into an item; heavy false-positive test coverage.
- **SW-2** Narrow the category-suffix append (facts.py:347) to a **known-modifier allowlist**
  (chicken/mutton/goat/lamb/veg/paneer/egg/prawn/fish/…) instead of "any non-complete-dish name."
  Kills `Dosa → Dosa Biryani`; keeps `Chicken → Chicken Biryani`.
- **SW-3** Fix the greedy `price_before_name` capture so a multi-price offer
  (`everything $5.99 and also $7.99…`) does not fabricate a phantom `also` item and drop the real
  ones. Reject connector words (also/and/plus) as names; fail-closed to no-item over wrong-item.

### Cluster C — Price-conflict signal  (`src/platform/schemas.py` + `facts.py`)
- **SW-4** Add `"price_conflict"` to `FlyerManualReviewReason`; at the last-wins reconcile
  (facts.py ~987) flag same-item conflicting prices so it surfaces as manual review instead of
  silently shipping the last price.

### Cluster D — Wrong-brand ingest default + QA backstop  (`render.py` + `semantic_brief.py`)
- **SW-1a (ingest default):** invert `_style_only_reference_requested` (render.py:1920) / its
  upstream role assignment so reference/theme uploads default to **style-only** (the existing
  correct prompt path at render.py:2567-2568) unless the upload is the owner's registered identity
  or she explicitly opts into identity-use. No new machinery — flips a default.
- **SW-1b (QA backstop):** drop the org-suffix escape at `semantic_brief.py:705` so any
  unexplained masthead-shaped line (2-3 words, title/high-upper case, ≥4 letters, not a campaign
  title / requested label / allowed identity — existing exclusions at 707-711 stay) → block →
  manual review. Operator accepts extra manual-review for a pilot.

### Cluster E — Brand-asset audit row  (`src/agents/flyer/onboarding.py`)
- **AN-4** Write an audit row through the existing chokepoint when a same-kind re-upload flips a
  prior active asset to inactive (§12b: automated reversal of owner-applied state must be audited).

### Cluster F — QA gate + recovery  (`visual_qa.py` + `hooks.py`)
- **IN-1** Screen a small denylist of internal fact-key/spec literals (`item:0:name`,
  `business_name`, `contact_phone`, `locked_facts`, `sender_role`, `raw_request`) so a schema key
  painted into the art is blocked.
- **IN-3** In `_send_generation_failure_customer_update` (hooks.py), send a generic closure
  message when generation fails for an unclassified reason *after* a processing-ack — today the
  customer gets silence + an audit row only.

### Cluster G — Intake sample choice  (`src/agents/flyer/intake.py`)
- **BC-6** Broaden `_parse_sample_choice` (intake.py:870) to accept "first"/"the first one"/
  "second"/"option 1"/word-numbers, not just a literal digit.

---

## Deferred — own tracks (NOT this batch; each its own gated decision)

- **v2 graduation track.** (1) parity-guard audit — DONE (§ above). (2) Build a persona-corpus
  fixture set (rupee/bare/code-mix/Dosa-next-to-Biryani/multi-price) + run through v2 on the box
  (needs `OPENROUTER_API_KEY`; unprovable offline). (3) Graduate `FLYER_EXTRACTION_V2` **behind the
  patched legacy floor**, pilot-number-scoped, with shadow soak. v2 fixes BC-1/BC-2 at the substrate
  and IN-4 occasion + SW-5 headline; SW-2 stays on the legacy floor (parity recombination gap).
- **SW-5** (code-mix garbled headline) — LLM-classifiable; regex-patching = treadmill → v2 track.
  Offline mitigation (generic-title fallback on low confidence) optional; default **defer**.
- **IN-4** (occasion on deterministic path) — fixed by v2. Optional bounded deterministic
  festival→occasion map reusing BC-5's exact festival names (non-treadmill); default **include**
  as a cheap safety net — confirm.
- **Broader wrong-brand strategy** (curated ethnic-brand list / proxy-blur preprocessing) — separate.

## Confirm-as-design (no code unless you say otherwise)

- **AN-5** address-less flyer ships with a **warning**, not a hard block — keep as warn (design).
- **AN-1** exact APPROVE before the preview exists → revision — optional "still working" reply;
  default **leave**.
- **IN-2** uniform-price column screened only under the typeset marker — optional extend to legacy
  renders; default **leave**.

## Test strategy

- Pure classifiers/extractors (actions.py, facts.py, intake.py, semantic_brief.py) → in-process
  pytest asserting on returned values. Scripts (create-flyer-project, QA gate) → subprocess-invoke
  + assert on stdout/file mutations, matching `tests/test_catering_v02_scripts.py`.
- Every finding gets a test that **fails on origin/main and passes after the fix**.
- Full flyer test suite stays green.

## Delivery boundary

- All work on `feat/flyer-audit-remediation-20260713` (worktree, off origin/main). Tests green.
- **No merge, no deploy, no send** — no-auto-commit + recorded-approval + never-send-on-live-VPS.
- Deliver a **box-verification checklist** for the box-only surfaces (render fidelity/formats/PDF,
  live-LLM extraction rescue, swipe-reply binding, voice/photo intake) — the audit §4 Layer-2
  script tightened to these fixes.

## Open question before coding

Confirm the two **default-include** deferred items: **IN-4** (deterministic festival→occasion map)
folded into Batch 1, and whether **AN-1/IN-2** small adds are in or out. Everything else follows
your three fork decisions exactly.
