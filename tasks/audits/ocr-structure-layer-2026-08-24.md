# OCR structure/provenance layer — the CAT-05 inversion is caught

**Drift-check tag:** `Hermes-native` — a research record. No runtime code, schema,
skill or config changed. `parse-menu-photo` untouched and still on OpenRouter.
No threshold tuned, no `X.99` prior work, no model shopping, RapidOCR evidence
reused rather than re-run.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Document structure / edition identity for menus | none — Hermes' path is a remote vision model that returns no coordinates and no provenance | research prototype only; nothing built into the runtime |

Verdict: **no production engineering.**

---

## The question this answered

> Can we represent and validate the real document structures **before** deciding
> which price belongs to which item?

Yes — and doing so catches the failure the confidence-based design could not see.

## The CAT-05 inversion, caught — both halves

Four rows that the shipped gate **accepted** at pairing confidence 0.97–0.98:

```
Chana Masala:                    standalone=[130]  expected=2 observed=1
Vegetable Dhanshak:              standalone=[130]  expected=2 observed=1
                                 -> INCOMPLETE -> REVIEW      (C1 + C3)

Chana/aloo/mushroomSaag:oS60     standalone=[130]  embedded=[60]
Vegetable Jalfrezi/vindaloo:o$60 standalone=[130]  embedded=[60]
                                 -> REFUSE  C2: "item name text carries $60;
                                    standalone price cells for this row are [130.0]"
```

The mechanism that fixes it is the requirement stated at the outset: **a missing
expected price lowers document completeness rather than making the remaining
price look more authoritative.** Expected arity comes from page-level facts —
tier-header span, panel repetition, local column coverage — and never from row
confidence, which is exactly why a per-row score could not have seen it.

## Provenance: the two coexisting editions are separated

Decision order matters, and two intuitive orderings are wrong:

- **Word-overlap cannot decide it.** Raw OCR word-type Jaccard scores 0.72–1.00
  between two encodings of *one raster* — overlapping the range a genuine
  revision scores. The lane's first classifier used it and called **9 of 11
  recompressions "distinct edition"**.
- **Pixel distance cannot decide it either.** A price edit moves a few hundred
  pixels on a 1.7 Mpx page. Pixel distance is used only to *confirm*
  recompression after prices already agree.

What decides it is **per-item price agreement**:

| verdict | n | evidence |
|---|---|---|
| RECOMPRESSION_DUPLICATE | 11 | 188–191 comparable rows, **0** price disagreements |
| **DISTINCT_EDITION** | 1 (`img_3d486aeb50f2`) | 98 comparable rows, **23 value-changed** |
| UNRELATED | 1 | name Jaccard 0.00 |

The changes are coherent whole-dollar steps across whole sections
(`masala dosa 10.99→9.99`, `samosa 3.99→2.99`, `limca 2.99→1.99`), not the
scatter digit-confusion produces. Arity differences (17 rows) are counted
separately and never used to assert an edition — those are layout or recognition,
not proof of a repricing.

**Authoritative edition: UNDETERMINED, and that is the finding.** Zero of 155
files carry EXIF, filenames are content-derived, and mtime reflects the scp pull.
`mtime` is stored under the field name
`mtime_utc_RECORDED_NOT_AUTHORITY` and read by no decision. **Nothing in the
cache can order the two editions — both must be surfaced to the operator.**

## Below-name layouts, without nearest-neighbour guessing

Page **orientation** is decided before any panel splitting, and that ordering is
load-bearing: on a tile grid the money columns *are* the tiles, so splitting on
them first cuts every name/price pair in half and the stacked structure becomes
invisible. Orientation is unambiguous on every document (CAT-01B 60/0 side-by-side,
CAT-07 0/2 stacked).

A stacked pair binds only when the name row is names-only, the next row is
prices-only, **both hold the same count**, and the i-th↔i-th offsets match the page
median. When a price is missing the counts differ and **the whole pair is refused**
rather than sliding the remaining prices onto the wrong names — CAT-10 refuses one
pair on a 5-names-vs-4-prices mismatch and binds 8 of 14. That is an honest
6-row refusal instead of 6 wrong answers.

The single remaining use of nearest-neighbour is attaching a *qualifier*
(`Half Tray`, `100 Count`) to a price cell — mislabelling a tier, never moving
money between items — and it is called out at the site.

## Checks: each one proven to fire *and* to discriminate

| check | fired | quiet | status |
|---|---|---|---|
| C1 arity incomplete | 3 | 444 | fires + discriminates |
| C2 name/price contradiction | 6 | 441 | fires + discriminates |
| C3 arity outlier | 6 | 441 | fires + discriminates |
| **C4 off-grid price** | **0** | 447 | silent on this corpus — **proven live by probe** |
| C5 tier unresolved | 2 | 445 | fires + discriminates |
| C6 qualifier inconsistent | 1 | 446 | fires + discriminates |

C4 never fired, which is exactly the shape that hides a dead check — so a probe
moved one money block 55 px into the inter-column gutter and C4 fired 59 times.
Its silence is a property of the corpus, not of the check.

C2 was tested for discrimination directly: appending `:S130` to `Chana Masala` —
matching its own price — does **not** fire; `:S60` does. It is a contradiction
check, not a money-in-name detector.

## Ablation monotonicity, with the residual named rather than argued away

413 single-price deletions, 408 of which measurably changed the page (so the
harness is live and the zeros are real zeros):

- **the row that lost a price never gains an accepted claim — 0 violations**
- **page recognised-price count never rises — 0 violations**
- page accepted-row count never rises — **1 violation in 413**

That one is disclosed, not fixed: C3's reference statistic is estimated from the
page, so ablating the page can move it. The first implementation used the mode and
produced **118** violations; switching to a median over a wider window cut it to 1.
**No page-derived statistic can be immune to page ablation** — the sensitivity was
reduced and the residual measured.

## The holdout was protected by a guard, not by instruction

`CAT-06` was never located, loaded, run or scored, and `HOLDOUT_SEALED/` was never
read. A hard `assert_not_holdout()` at every load path refuses the holdout's
sha-prefix, its label, and the sealed directory name — **verified firing on all
four probe strings**. The only mention of CAT-06 anywhere in the output is the
exclusion note.

## Corpus discipline

The 7-image corpus is **development data**. It shaped the representation, the
layout taxonomy, the money lexicon and every constant in the module. The
development-set figures — 142 correct, 0 silently wrong, 0 silently incomplete,
18 truthful refusals — are **not a readiness claim and must not be quoted as
one**. Holdout evaluation is pending and the implementing lane did not have
access to it.

## Deliberately not built

No coverage optimisation (step 5 of the brief is untouched). No multi-line
item-name stitching — CAT-10's `(Ramadan Style)` continuation causes a
5-vs-4 mismatch and the pair is refused rather than guessed, costing 4 of 14
items. No repair of the rows where OCR merged a whole menu into one text block;
they land in review.

## Status

Engine switch remains **HOLD**. The next gate is holdout scoring against the
sealed CAT-06 ground truth, which no implementing agent has seen.
