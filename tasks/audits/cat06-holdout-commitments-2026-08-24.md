# CAT-06 holdout — commitments, recorded BEFORE unsealing

**Drift-check tag:** `Hermes-native` — a commitment record. No runtime code,
schema, skill or config changed. **Contains commitments and metadata only. The
transcription is deliberately NOT in this repo** — publishing it would end the
experiment.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Pre-registration / commitment of an evaluation | none — Hermes has no experiment-integrity primitive, and this is a two-line hash discipline | record digests in the repo's existing audit convention; no code |

Verdict: **no engineering.**

---

## Why both sides are frozen

A ground-truth commitment proves the answers were not edited after the fact. It
does **not** prove the implementation was not modified after learning them. So
both sides are committed before anything is unsealed.

| commitment | value |
|---|---|
| **ground_truth_commitment** | `ed290429e50787cdff851abf70473dce4c0b9708b3eb873f9a72777f3ace49a5` |
| **implementation_commitment** | `c3123307c9c5cf3ebc4c088834103a0a39dff9d63fccd9ec792efa26e56f4ecf` |
| **ocr_evidence_commitment** | `1fa8d8930d2450a5a54c502fc2c887885db6469340f847d7efc77ac55cef921a` (155 files) |
| **CAT-06 image sha256** | `f391c03e4de466aa9a270dfe7ef46a7d53fbaa900535fe4a9959ca8ec38927ac` |

The implementation commitment is content-addressed over nine files —
`struct_checks.py`, `struct_diag_ablate.py`, `struct_doc.py`, `struct_falsify.py`,
`struct_provenance.py`, `struct_run_provenance.py`, `struct_run_structure.py`,
plus the two frozen JSON outputs. It is order-independent and mtime-independent:
renaming a member or changing any byte moves the digest, touching a timestamp
does not. Per-member digests are retained in `scratchpad/FREEZE/commitments.json`.

The OCR evidence is committed too, because it is an **input**. Freezing the
parser while leaving its input free would leave the obvious hole open.

## Provenance of the document

`img_bc4aef6a4768.jpg`, 1131×1600, the sole copy in the cache — nearest non-self
neighbour meanAbsDiff **37.30/255** across 155 distinct images, so there is no
higher-quality twin and no second edition of this document. That check was run
*before* transcription, because the trap is live in this same cache for a
different menu, where two files differing only by JPEG quality were once taken
for two photographs.

Environment: RapidOCR PP-OCRv4 ONNX (rapidocr-onnxruntime 1.4.4 / onnxruntime
1.29.0), repo `origin/main` at `a159296d`.

## The protocol

1. **Phase A** — the frozen implementation predicts CAT-06 with **no access to
   the transcription**. The prediction artifact is hashed *before* anything is
   unsealed.
2. **Phase B** — a *separate* evaluator unseals the transcription and scores the
   **already-frozen** prediction. The parser is not rerun or altered after ground
   truth is seen, for the headline result.

**Repairing and rerunning CAT-06 does not produce a holdout pass.** Once the
answers are revealed, CAT-06 becomes development data permanently.

## Hard failure conditions

Any one of these means **HOLDOUT = FAIL** and **OCR SWITCH = HOLD**:

- a silently wrong price
- a silently invented item
- a structurally incomplete row accepted as complete
- a price from one item attached to another
- a wrong edition presented as authoritative

## Even a pass is not a switch

One sealed document is strong *falsification* evidence, not evidence of
population performance. A pass promotes the system to **shadow / read-only**
alongside the untouched OpenRouter incumbent — storing evidence and proposed
structure separately, never influencing a customer or owner proposal, scored
independently later. That yields prospective evidence instead of more tuning on
the historical cache.

## Engine disposition, unchanged

RapidOCR leading candidate · Tesseract baseline · PaddleOCR-VL barred from
money-bearing extraction · Unlimited-OCR security HOLD · **OpenRouter remains the
production incumbent**. No model shopping, no new tuned price heuristics, no
`X.99` work.
