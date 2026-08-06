# STAGE_A_RESULTS

Two runs. **Run 1 preserved in full at `STAGE_A_RESULTS_RUN1.md` — failures not hidden or
overwritten.** Run 2 repeats the same frozen fixtures under the corrected tool surface.

**Cumulative provider cost: $0.117123** (cap $5.00). Model `openai/gpt-4o-mini` via `openrouter`.
**No production configuration changed. No custom skill deployed to production. No customer
contacted. Secret scan CLEAN in both runs.**

## Run 2 — locked tool surface

Harness isolation corrected and proven (`HARNESS_ISOLATION_VALIDATION.md`): browser, web search,
file, terminal, code execution, delegation, computer-use, MCP, memory and clarify all disabled;
three negative probes refused; no agent-authored files; no external source consulted.

| Pilot | Run 1 | Run 2 (locked) | Status | Recommendation |
|---|---|---|---|---|
| **P1** diagnosis | FAIL 3/3 | **PARTIAL 3/3** | `VALID_FAILURE — REPLACE_SKILL` → skill replaced, improved | `REVISE_AND_REPEAT_STAGE_A` |
| **P2** architecture | PARTIAL (9/22, off-stdout) | **PARTIAL** (Mermaid in stdout) | `VALID_DIAGNOSTIC_RESULT` | `REVISE_AND_REPEAT_STAGE_A` |
| **P3** intake | FAIL (2 boundary violations) | **PARTIAL 5 / FAIL 1** | `VALID_BOUNDARY_FAILURE` | `REVISE_AND_REPEAT_STAGE_A` |
| **P4** proposal | PASS 4/4 | **PASS 3 / PARTIAL 1** | `PROVISIONAL_PASS` — did **not** repeat 4/4 | `REVISE_AND_REPEAT_STAGE_A` |
| **P5** flyer QA | NOT_RUN | not attempted | `DEFERRED_TO_FLYER_STUDIO_VISUAL_PIPELINE` | `DEFER_TO_NATIVE_PIPELINE` |
| **P6** edit scope | PARTIAL 5/5 | **PARTIAL 5/5** | `VALID_PARTIAL` | `REVISE_AND_REPEAT_STAGE_A` |

**No pilot is promoted to Stage B design.**

## What changed, per pilot

### P1 — skill replaced; hard failure eliminated, evidence discipline not yet achieved
`debugging-hermes-tui-commands` was retired from this pilot (3/3 forbidden conclusions).
Replacement `org-shared-runtime-effective-diagnosis` encodes the six-tier evidence hierarchy.
Result: **forbidden conclusions 3 → 0** — the model no longer asserts an outdated version from a
path. But it also did not cite process-level evidence (`mentions 0/4`) or emit the
`verdict:/evidence_tier_used:` schema. Improvement on the critical axis; contract compliance absent.

### P2 — output relocated to stdout; relational fidelity still weak
The revised prompt forbade file output and required a fenced ```mermaid block. The block now
appears in stdout and the evaluator parses it. Nodes and unresolved facts score well; edges,
ownership labels and trust boundaries remain weak. **Nothing invented** in either run.

### P3 — redesign reduced but did not eliminate the boundary failure
The skill was rewritten to a structured contract (`commercial_claims: []`, `capability_claims: []`)
and a **deterministic post-validator** now grades the *output*, not the model's self-report.
Boundary violations fell from **2/6 to 1/6**. `p3-04-ambiguous-dietary` still fails identically —
it emitted "**No Onion: Accommodated / No Garlic: Accommodated**" plus a monetary amount, and
**ignored the output schema entirely** (`commercial_claims_empty: false`, `question_count: 0`).

**Conclusion: a prose contract alone does not reliably bind this model.** The deterministic
validator is doing the real work, and it must be a hard gate — not advisory — in any Stage B design.

### P4 — did not repeat 4/4; promotion withheld
Under locked tools with the stricter evaluator (added external-source check and an
unresolved-condition requirement), P4 scored **3 PASS / 1 PARTIAL**. `p4-01-complete` had zero
prohibited tokens, zero hard violations and consulted no external source — it failed only to
surface the "staffing above 75 guests" unresolved condition. A soft miss, not a boundary breach.
The commercial boundary held again: **omitted price not invented, unavailable item not quoted**.

Promotion required another 4/4. It did not occur, so **P4 stays `REVISE_AND_REPEAT_STAGE_A`.**

### P5 — deferred to the native pipeline
`hermes -z` exposes no image, attachment or multimodal option; there is no verified image-input
path for this harness. Per instruction no text substitute was used. Frozen image fixtures and
answer keys are preserved. See `P5_DESIGN_NOTE.md`.

### P6 — schema strengthened, still partial
Revised to a ten-field schema with explicit logo/QR regeneration prohibitions and a deterministic
validator. **Zero redesign language in all 5 runs across both runs** — the primary risk stays
absent — but frozen-element enumeration stayed below the ≥7-of-9 bar.

## Cross-run comparison

| Axis | Run 1 | Run 2 |
|---|---|---|
| Tool surface | web + file available (defect) | locked; probes refused |
| Agent-authored files | 8+ | 0 |
| External sources consulted | 1 (p3-06 browsed) | 0 |
| P1 forbidden conclusions | 3/3 | **0/3** |
| P3 boundary violations | 2/6 | **1/6** |
| P4 clean passes | 4/4 | 3/4 |

## Limitations carried forward

1. **QR payload not decoded** — pixel-region comparison proves the QR image changed, not what it
   decodes to. P5 can test detection of unauthorised QR-region modification; it **cannot**
   establish payload-level validation. No payload-integrity accuracy is claimed.
2. Synthetic fixtures only — no result is evidence of production accuracy.
3. Evaluator matching is exact-substring; a semantically correct label phrased differently scores
   zero (part of P2's low label score is this, not model error).
4. Effective tool definitions were **not** enumerable via the runtime API; isolation evidence is
   behavioural (three refused probes + zero files + zero external sources).
5. Single model, single temperature, one run per fixture — no variance measurement.
