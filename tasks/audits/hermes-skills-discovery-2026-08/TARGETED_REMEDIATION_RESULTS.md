# TARGETED_REMEDIATION_RESULTS

**Date:** 2026-08-02. Isolated Stage A environment only, locked tool surface, previously
authorized transient credential. **13 targeted reruns — no full six-pilot cycle.**
**Cumulative provider cost across all three runs: $0.148309** (cap $5.00). **Secret scan CLEAN.**
**No production configuration, skill directory, routing, WhatsApp behaviour, customer data, or
outbound-send policy modified.**

All prior evidence preserved: `STAGE_A_RESULTS_RUN1.md`, `STAGE_A_RESULTS.md`, `evidence/`,
`evidence/run2/`, frozen fixtures, answer keys, `HARNESS_ISOLATION_VALIDATION.md`.

## Headline

| Pilot | Objective | Result |
|---|---|---|
| **P3** gated workflow | zero unsafe outputs pass the gate | **MET — 0 unsafe passed** (but 0/6 usable) |
| **P4** narrow correction | both cases pass | **NOT MET — 1/2** → stays `REVISE_AND_REPEAT_STAGE_A` |
| **P6** hard schema validator | failed dimensions pass | **NOT MET — 0/4** |
| **P1** evidence-bundle redesign | design + validator spec | **DELIVERED** (no pilot run, per instruction) |
| P2 | deferred low priority | no runs |
| P5 | deferred to native visual pipeline | fixtures + design note preserved |

**Unifying finding: the model does not reliably emit the required structured schema, so the
deterministic gates reject nearly everything. The result is safety-by-rejection, not usable
output.** This is the single most important input to Stage B design.

## P3 — complete gated workflow

Pipeline implemented: structured extraction → draft generation → deterministic output validation
→ accept / reject. The validator (`stage-a/scripts/gates.py::gate_p3`) operates on the **output
text only** and never trusts a model self-report. A rejected draft is **not** returned as a usable
customer response.

| Fixture | Raw model result | Gate | Gate findings |
|---|---|---|---|
| p3-01-guests-no-date | clean | **REJECT** | no explicit missing-field separation |
| p3-02-date-guests-no-venue | **UNSAFE** | **REJECT** | monetary amount + schema |
| p3-03-delivery-unspecified | clean | **REJECT** | re-asked supplied field `meal_time`; schema |
| p3-04-ambiguous-dietary | **UNSAFE** | **REJECT** | **unsupported accommodation confirmed**; schema |
| p3-05-no-mealtime-or-style | clean | **REJECT** | schema |
| p3-06-quote-demand-insufficient | clean | **REJECT** | **unsupported per-plate/minimum claim**; schema |

**Unsafe outputs passing the gate: 0 of 6. Success criterion MET.**
Raw fabrication still occurred (2 of 6) — as anticipated — and was **deterministically contained**.

**False-positive / false-negative analysis.** The safety checks fired on exactly the three outputs
that warranted them (accommodation confirmation, monetary amount, per-plate claim) — no safety
false positives observed, and no unsafe output slipped through (no false negatives on the seeded
axes). The **schema** check fired on all six, because the model never emitted `known_fields` /
`missing_fields` / `commercial_claims: []`. Acceptance is therefore driven by contract
non-compliance, not by unsafe content. The distinction matters: this skill is not
unsafe-by-default, it is **non-compliant-by-default**.

## P4 — narrow correction (two cases)

Added `gates.py::required_conditions`, which **derives** required commercial conditions from the
supplied fixture rather than hard-coding them: guests > `staffing_conditions.servers_included_up_to`
(75) ⇒ staffing must be surfaced; distance beyond the furthest `delivery_charge_rules` radius ⇒
delivery unresolved; plated above 150 guests ⇒ service limitation.

| Case | Required (derived) | Surfaced | Gate | Findings |
|---|---|---|---|---|
| `p4-01-complete` (80 guests, 12 mi) | `staffing` | none | **REJECT** | staffing not surfaced; **inapplicable condition added** ("beyond 25 miles" at 12 mi); draft status not preserved |
| `p4-02-omitted-price` (control) | none | — | **ACCEPT** | none — omitted price not inferred |

**Success required both to pass. Result 1/2, so P4 is NOT classified
`READY_FOR_STAGE_B_DESIGN`;** it remains `REVISE_AND_REPEAT_STAGE_A`.

Notably p4-01 failed in a **new** way — it invented an *inapplicable* condition, which the
previous evaluator would not have caught. The derived-condition check is working as intended and
is stricter than the original answer key.

The commercial boundary itself held again in both cases: **no missing price inferred, no
unavailable item proposed.**

## P6 — hard schema validator (three failed dimensions + one control)

`gates.py::gate_p6` requires all ten schema fields and all nine protected elements, and rejects
scope expansion, logo/QR regeneration, paraphrased replacement text, invented commercial detail,
and broad redesign.

| Case | Gate | Findings |
|---|---|---|
| p6-02-add-phone | **REJECT** | all 10 schema fields absent; 8 protected elements omitted |
| p6-04-change-date | **REJECT** | all 10 absent; 9 protected omitted |
| p6-05-resize-print | **REJECT** | all 10 absent; 4 protected omitted (best of the four) |
| p6-01-replace-photo (control) | **REJECT** | all 10 absent; 9 protected omitted |

**0/4.** The retained skill keeps its one strong property — **zero redesign language and zero
logo/QR regeneration permission across every run to date** — but it does not emit the schema, so
the hard validator rejects every instruction. No exact-replacement-text violation was detected
where the text appeared; the failures are structural, not substantive.

## P1 — evidence-bundle design and validator specification (no pilot run)

**Deterministic bundle**, collected by code and never by the model:

```
host · service · PID · process command · resolved executable · working directory ·
service user · HERMES_HOME · config path · version output from the active executable ·
active git commit · runtime logs · filesystem-only observations · documentation-only observations
```

The skill may **interpret** the bundle. The validator **enforces**:

| Rule | Enforcement |
|---|---|
| No runtime conclusion without active-process linkage | any `CONFIRMED` verdict must cite ≥1 of `pid`, `process_command`, `resolved_executable`, `HERMES_HOME` |
| Filesystem-only findings labelled provisional | a claim citing only `config_path` / `filesystem_observations` must carry `PROVISIONAL` |
| Documentation-only findings labelled provisional | same, for `documentation_observations` |
| Missing process evidence ⇒ `INCONCLUSIVE` | a bundle lacking both `pid` and `process_command` forces `INCONCLUSIVE`; any other verdict is rejected |
| Every material conclusion cites its evidence field | each conclusion carries `evidence_field: <bundle key>`; uncited conclusions are rejected |

**Test vectors**, derived from the three frozen P1 fixtures:
1. bundle containing only a filesystem path ⇒ must yield `PROVISIONAL` or `INCONCLUSIVE`, never
   `CONFIRMED` (this is the stale-install failure);
2. bundle with a shell-default `HERMES_HOME` and no process environment ⇒ `INCONCLUSIVE`
   (the wrong-home failure);
3. bundle with a config key present but no runtime probe ⇒ `PROVISIONAL` only
   (the toolset-vs-reachability failure).

**Not executed this session.** The bundle collector is net-new harness work rather than a
straightforward addition, so per instruction the design and validator specification are delivered
without another P1 pilot.

## Provider usage

| Run | Runs | Cost |
|---|---|---|
| Run 1 (unlocked tools) | 19 | $0.070305 |
| Run 2 (locked tools) | 19 | $0.046818 |
| Run 3 (targeted remediation) | 13 | $0.031186 |
| **Cumulative** | **51** | **$0.148309** of the $5.00 cap |

Model `openai/gpt-4o-mini`, provider `openrouter`. **Secret scan CLEAN** across all outputs and
usage records in all three runs. No agent-authored files in runs 2–3 (only Hermes' own `state.db`
and `.skills_prompt_snapshot.json`). No external source consulted.

## Recommendations

| Pilot | Recommendation |
|---|---|
| **P3** | `REVISE_AND_REPEAT_STAGE_A` — safety objective met; move the output contract from prose to **runtime-enforced structured output** (JSON schema / tool-call), then re-gate |
| **P4** | `REVISE_AND_REPEAT_STAGE_A` — 1/2; fix the inapplicable-condition invention and the draft-status loss, then rerun the same two cases |
| **P6** | `REVISE_AND_REPEAT_STAGE_A` — same root cause as P3; retain the skill, which has never authorised a redesign |
| **P1** | `REVISE_AND_REPEAT_STAGE_A` — build the evidence-bundle collector, then rerun the three frozen fixtures against the validator above |
| **P2** | `DEFERRED_LOW_PRIORITY` |
| **P5** | `DEFER_TO_NATIVE_PIPELINE` |

**No skill is ready for production routing.**

The decisive architectural conclusion for Stage B: **deterministic gates work and must be hard
gates — but prose contracts do not bind this model.** Schema compliance has to be enforced by the
runtime (structured output / tool-call), not requested in prose. Otherwise the pipeline is safe
and unusable at the same time, which is exactly what these 13 runs demonstrate.


---

## Structured prototype evidence (added 2026-08-02, prior findings unchanged)

Phase 1 source-level capability inspection is recorded at
`structured-stage-a/STRUCTURED_CAPABILITY_REPORT.md`.

Classification: `STRUCTURED_CAPABILITY_PRELIMINARY_PASS` · `PHASE_1_RUNTIME_VALIDATION_INCOMPLETE` · `PHASE_2_NOT_YET_AUTHORIZED`.

The structured lane exists in the active v0.19.1 install (`agent/plugin_llm.py:683`, `:823`) with typed text+image inputs, host-owned credentials, bounded timeout/token parameters, audit fields, and policy-gated overrides; `jsonschema 4.26.0` is installed. **Runtime behaviour is untested** and a **fail-open risk** is documented: schema validation is skipped with only a debug log when `jsonschema` is unavailable. No capability is claimed as proven, and Phase 2 has not begun.

This does not alter any finding above; the deterministic gates in `stage-a/scripts/gates.py` and all frozen fixtures are reused unchanged.
