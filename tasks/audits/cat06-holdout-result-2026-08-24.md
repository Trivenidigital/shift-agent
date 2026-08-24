# CAT-06 sealed holdout — result

**Date:** 2026-08-24
**Companion:** `tasks/audits/cat06-holdout-commitments-2026-08-24.md` (#771, `290c65bf`) — commitments recorded *before* unsealing
**Drift-check tag:** `Hermes-native` — evaluation of a scratchpad prototype. No runtime, no deployed code, no state touched.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Document extraction (incumbent) | yes — production OpenRouter vision path, live | **Unchanged. Remains the sole production extractor.** |
| Candidate replacement (RapidOCR + structure layer) | n/a — local prototype | **Rejected for promotion by this result.** |

The holdout existed to decide whether a locally-built extractor earned a promotion path. It did not. Nothing is adopted; the Hermes-first position is unchanged from before the experiment.

---

## VERDICT: **HOLDOUT FAIL** · **OCR ENGINE SWITCH = HOLD**

The prototype does **not** advance to shadow, read-only, or any other promotion tier. The promotion path was gated on a pass. OpenRouter remains the untouched production incumbent.

## What decided it

Prediction **row 43**, `decision: ACCEPT`, `completeness: COMPLETE`, `checks_fired: []`:

```
item_name : "3 Types of Chaat Any Flavour Cake (1 LB)"
price_cell: tier="Long Deep/Full Tray"  value=30.0  confidence=0.9969  flags=[]
```

Ground truth holds these as **two entities in two different panels**:

| GT entity | panel | price |
|---|---|---|
| `3 Types of Chaat` | Live Chaat | `null` (minimum-spend only) |
| `Any Flavour Cake (1 LB)` | Cakes | `$30` |

This trips two hard-failure conditions simultaneously — **a silently invented item**, and **a price belonging to one item attached to another**. The tier is fabricated too: the Cakes panel is a self-contained box with no tray columns, so `Long Deep/Full Tray [20-25 People]` on a flat cake price is a grid tier invented from x-coincidence (`$30` at x=1004 → nearest validated column centre 981.5).

It was accepted at **0.9969 confidence with no check firing**. High confidence on a fabricated row is worse than a low-confidence one, because nothing downstream has a signal to act on.

### The near-miss makes it worse, not better

The structurally identical row 41 (`Pani Puri` + `Choc/Gulabjamun/Rasmalai`, `$35`) *was* refused — but only because its name text happened to carry `$700`/`$10`, tripping `C2_NAME_PRICE_CONTRADICTION`. The discriminator was money-in-name, not panel-merge. Row 43's left-panel clause sits on the next y-band, so nothing fired.

> **The safety on row 41 was accidental, not designed.** Two instances of one defect; one was caught by an unrelated check. Counting that as a save would have been the "green for the wrong reason" error this programme keeps hitting.

---

## Score

```
GT items                36   (30 grid + 6 live-counter)
GT price values         74   (72 grid + 2 live counter)

ACCEPT                  15    -> 14 correct, 1 phantom (row 43), 0 wrong values
REVIEW                   5    -> all values correct
REFUSE                   3
NO_CLAIM                25

coverage   28/74 = 37.8%  of GT price values correctly accepted
           14/36 = 38.9%  of GT items correctly accepted
           39/74 = 52.7%  of GT price values surfaced in any bucket
```

**No wrong price value was accepted anywhere.** All 44 emitted cells trace to a real OCR token — the failure mode is fabricated *structure*, not fabricated *numbers*.

### Failures by dimension

| dimension | count |
|---|---|
| recognition | 35 price values dropped (one root cause) + 6 name-space losses |
| layout | 1 root cause → 6 cross-panel merged rows; 1 unresolved column legend |
| association | **1 ACCEPTED (row 43)** + 5 clause tokens promoted to price cells (all REFUSED) |
| completeness | 5 false-positive `INCOMPLETE`; **0 true positives** |
| provenance | 0 |

---

## The 83-vs-44 asymmetry — resolved, and it is *not* a large silent miss

The open question from Phase A closes exactly:

```
72  grid price tokens        == GT total_printed_price_tokens_in_grid
 2  live-counter prices
 9  money-shaped NON-price text (4 minimum-spend clauses x2 + 1 customization line)
--
83
```

GT's 74 printed prices + 9 clause tokens = 83. The provenance census counts money-*shaped strings*, correctly and by design — it was never a price count. No double-counting, no phantom.

The 44 emitted cells decompose as 39 real GT prices + 5 clause tokens misread as prices — and **all 5 landed in REFUSE**. That is the system working.

The real finding is the other 35.

### Root cause, confirmed at source (not inferred)

> **All 35 missing prices are exactly the 35 money tokens carrying an inline bracketed tier superscript. Zero bare tokens were dropped. One-to-one, no residual.**

`struct_doc.py:145-148` — `classify("$70[HT]")` strips the money span, leaving residual `HT`. `QUALIFIER_VOCAB` (`struct_doc.py:64-69`) holds `piece/lb/tray/half/full/per/people…` but **no `st/ht/lm/ft`**. So it falls through to `return "text", vals, ""` — values computed, then discarded by the caller.

`struct_doc.py:583-586` — the `"text"` branch keeps a block as a *name* only if it has ≥3 alpha chars. `"$70[HT]"` has 2. There is **no `else`**: the block vanishes. Not a price, not a name, no warning.

`struct_doc.py:738` / `:766` build `page_money` / `money_blocks` from the same predicate, so `estimate_money_columns` never saw those 35 tokens — which is why column 0 has only 16 members and the header tier never bound to it.

Affected rows (all → `NO_CLAIM`, 0 cells): Punugulu, Onion Pakora, Upma, Pongal, Double Ka Meeta, Carrot Halwa, Fruit Custard, Fruit Custard + Ice Cream, Mango Delight, Apricot Delight, Saffron Malai Delight. The document's **entire DESSERTS block** and both premium BREAKFAST tray rows silently priced at nothing.

**Correct refusal in form, a 47% coverage hole in substance.** No value was claimed, so no hard condition is tripped by this defect — it is not what failed the holdout.

### The comment states the opposite of what the code does

Directly above `QUALIFIER_VOCAB`:

```
# ...any other residual means the block is an item name that CONTAINS money --
# which is the anomaly this lane is built to surface.
```

The fall-through surfaces nothing. It discards the block silently. **An invariant stated next to the code that violates it** — the defect class already recorded against this repo. The comment must be corrected in the same change as the vocabulary, or it will re-mislead the next reader.

---

## The structural finding that matters more than the bug

`C1_ARITY_INCOMPLETE` derives `expected_arity` from `local_column_coverage` over neighbouring **priced** rows at a `cov >= 0.5` threshold (`struct_doc.py:915-925`) — the same channel the recognition bug depleted. And `observed == 0` routes to `NO_PRICE`, never `INCOMPLETE` (`struct_doc.py:944-950`).

> **The completeness check calibrates its expectation on the survivors, so a systematic recognition failure is invisible to it by construction.**

Evidence, not argument: C1 fired **6 times — 5 of them false positives on rows GT confirms were complete** (Mysore Bonda, Tamarind Rice, Sambar, Roti Pachadi, Rasmalai), and **0 times on the 11 rows that lost 100% of their prices.** Zero true positives. Punugulu had 4 GT columns available, 2 printed, and got `expected_arity: 1`.

**Fixing the vocabulary alone would raise the score and leave the system exactly as blind.** Priority order is therefore: (2) before or with (1), never (1) alone.

---

## Controls — run before any number was reported

| control | result |
|---|---|
| **NEG-1** corrupt every GT price (+7) | 14 correct → **0**. **PASS — scorer live** |
| **NEG-2** rotate the tier map ST→HT→LM→FT→ST | 28 correct → **9**; the 9 survivors are exactly the unlabeled single-price rows GT declined to tier. **PASS — tier axis live where GT asserts, inert where GT refuses** |
| **NEG-3** mangle every GT name | 15 accepted → **15 phantom**. **PASS — name axis live** |

NEG-2 also demonstrates the eleven rows where GT *deliberately declined* to assign a tier were scored **value-only** — the system's `COL1` is an unnamed positional placeholder, not a tier claim. Penalising it for an inference GT itself refused would have been scoring against a fiction.

NEG-1 was mandatory: a singular/plural key mismatch produced a spurious 30/30-wrong in an earlier lane this session.

---

## Integrity — all five commitments MATCH

| artifact | committed | recomputed |
|---|---|---|
| prediction | `59ca4f1c…d2114f` | **MATCH** |
| ground truth | `ed290429…ce49a5` | **MATCH** |
| implementation (9 members) | `c3123307…6f4ecf` | **MATCH** |
| CAT-06 image | `f391c03e…8927ac` | **MATCH** |
| OCR evidence (155 files) | `1fa8d893…ef921a` | **MATCH** |

Per-member digests reproduce individually. `implementation_commitment_after_run` equals the pre-run value — the parser was not touched between prediction and scoring.

### Two adjudicated items, both concurred with by the independent evaluator

**1. Interlock bypass — ACCEPTED.** Phase A's own `assert_not_holdout` guard refuses `CAT-06`; the prediction was produced by presenting byte-identical bytes under a neutral id, and **the agent flagged this rather than burying it**. The guard is a development-time contamination device and Phase A was the sanctioned evaluation; input sha256 == sealed image, implementation commitment unchanged.

The evaluator was explicitly authorised to dissent and score the alternative reading. It scored it: **"refused by interlock" = 0 rows, 0 accepted, coverage 0% — and no hard condition can trip, because nothing is accepted.** A vacuous pass. The accepted reading is strictly more informative *and* is the one producing the FAIL, which removes any incentive concern about the bypass.

**2. 154-vs-155 comparators — ACCEPTED, with the check that would have mattered.** All 154 verdicts returned `UNRELATED`, which a permanently-broken comparator would also produce. The positive control lives inside the frozen pool run (`struct_provenance.json`, a committed member): **11 `RECOMPRESSION_DUPLICATE` + 1 `DISTINCT_EDITION`.** The comparator demonstrably fires. Excluding a document from its own duplicate search is correct methodology.

### GT audit — no dissent

GT was checked against pixels wherever it could move the score: the two-panel structure and the `$35`/`$30`/`null` assignments that decide the verdict; and the digit-confusable cases `$165[LM]`, `$160[LM]`, `$110[LM]`, `$130[FT]`, `$65[HT]`. GT's `total_printed_price_tokens_in_grid: 72` matches an independent count from the OCR evidence by a completely different route.

One coincidence flagged so it is not later read as a bug: `n_word_types: 83` and `n_price_tokens: 83` were recomputed from separate counters and are genuinely coincidental.

---

## What this licenses, and what it does not

- **OCR engine switch remains HOLD.** OpenRouter is the untouched production incumbent. Not shadow, not read-only.
- **Do not repair the bracket lexicon and rerun CAT-06 to claim a pass.** The experiment had value exactly once.
- **CAT-06 is development data permanently from this moment.** Any future number on this document is training performance, not evidence.
- Even a pass would have been **falsification evidence on one document, never population performance.**
- **This was an easy document** — GT records 18-19px price glyphs at high contrast. The failure is not a legibility failure, which removes the "try a better engine" reading. Both root causes are in our own structure layer, not in recognition quality.

## Carried forward, in priority order

1. **The completeness check calibrates on survivors** and therefore cannot see a systematic recognition failure — this one or its successor. Structural; fixing it is what makes any later score trustworthy.
2. **`QUALIFIER_VOCAB` silently discards any price carrying an inline tier superscript** — 47% of this page — and the adjacent comment claims the opposite. Fix the comment in the same change.
3. **Cross-panel row merging** produced the accepted phantom; the only thing that caught its twin was an unrelated money-in-name check.

Neither (1) nor (2) may be validated on CAT-06.
