# OCR ground-truth measurement — the frontier does not generalise, and the bottleneck moved

**Drift-check tag:** `Hermes-native` — a measurement record. No runtime code,
schema, skill or config changed. `parse-menu-photo` untouched and still on
OpenRouter. Association layer and every threshold reused **unmodified** — nothing
was tuned to anything discovered here.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Ground-truth measurement of document extraction | none — this measures our own corpus against our own pipeline | measurement only, no code |

Verdict: **no engineering.**

---

## Headline

Ground truth grew from the prior lane's 31 items to **172 GT items / 354 price
values across 7 images**. That was the named bottleneck. It is no longer the
bottleneck.

| scope | GT N | accepted | confidently wrong | phantom | correct assoc. | coverage |
|---|---|---|---|---|---|---|
| prior lane (CAT-01B only) | 31 | 30 | 0% | 0% | 100% | **96.8%** |
| **the four NEW images** | 107 | **4** | 0% | 0% | 100% | **3.7%** |
| **enlarged (7 images)** | **172** | **34** | **0%** | **0%** | **100%** | **19.8%** |

**The prior lane's 97%-at-zero-errors frontier is a property of one
single-price, one-price-column section. It does not generalise.** On four new
layouts the same pipeline sits at 3.7%.

And the framing that would mislead worse than the low coverage: **"zero errors"
without the N.** The 0/34 rests on 34 accepted claims, **30 of which are the
prior lane's already-measured section**. The four new images contributed **four**
accepted claims. The enlarged corpus adds almost no new evidence about the error
rate; it adds decisive evidence about coverage.

## A prior claim is retracted

The earlier record cited **"29 cross-photo confirmations"** on an independent
photograph of the same menu. That is **invalid**. `CAT-01` and `CAT-01B` are the
same raster at two JPEG quality levels — identical 1600×1112, meanAbsDiff
**2.93/255**, p99 = 17, and the high-quality tier has 0.00% of pixels differing by
more than 16. No hand-held recapture registers to sub-3 grey levels.

Those 29 confirmations measure **JPEG-robustness, not independent-observer
agreement**, and therefore do not reduce the shared-misreading risk they were
cited against. I reported that figure as corroboration; it was not.

What *does* survive: 24 of 30 rows of the prior CAT-02 ground truth were
independently re-read at 3× with **0 disagreements**, including the subtle
encoding where three sections leave the *Shallow* column empty. That GT is sound.

## Corpus reality — five documents

Census: 249 cached images → **155 unique by sha256** → 71 perceptual groups. Only
17 carry ≥15 price-bearing blocks, and 13 of those are the same document. **The
entire inbound cache contains five distinct dense-price menu documents.**

Newly ground-truthed, chosen for layout diversity:

| image | layout class | GT items | price values | glyph ink height |
|---|---|---|---|---|
| CAT-05 | dense trifold, 5 sections × **2 price columns**, low contrast | 56 | 112 | 12–18 px |
| CAT-03 | **tray-rate grid, 3 price columns + `NA` cells** | 29 | 80 | 15 px |
| CAT-10 | tile grid, **price BELOW name**, package qualifier left of price | 14 | 14 | 41 px |
| CAT-07 | tile grid, **unit prices** (`$2/piece`, `$12/lb`) + tray tier | 8 | 8 | 42–50 px |

**Stated as N=0 rather than substituted:** phone-photo perspective/lighting is
**N=0** — the "person holding a printed sheet" family is a flat rectilinear
digital composite, zero page curl, razor-sharp against a blurred background. Note
EXIF proves nothing either way here: WhatsApp strips it from all 155, so the
finding rests on pixels. Inline `item — $price` in a single OCR block is
**effectively N=2** — 4.2% of blocks on CAT-02, 18.8% on CAT-08, ≤0.2% elsewhere.

## The bottleneck moved: it is model shape, not data or recognition

Recognition confidence on the *failing* rows is **0.94–1.00**. Ground truth is
5.5× larger. Neither is the constraint. Two structural failures, in two different
dimensions:

- **Mode A — ASSOCIATION, on multi-price grids** (CAT-02, CAT-03, CAT-05). Every
  item row has 2–4 equally-overlapping price candidates, so `margin_item` = 0.00
  and the row is refused. The association model is **one-price-per-item; these
  documents are n-prices-per-item.** 30/30, 26/29 and 51/56 refused on exactly
  this.
- **Mode B — LAYOUT, on tile grids** (CAT-07, CAT-08, CAT-10). The price is
  printed *below* the name while the layer hard-requires the price to the right;
  row banding degenerates too (row pitch 1.5 px — tiles are not lines).

**4 of the 5 distinct price documents in real traffic are either n-prices-per-row
or price-below-name.** That is a model-shape gap, and no threshold change reaches
it.

## The most important new finding: the accept gate is anti-correlated with recognition

On CAT-05 all four accepted claims are accepted **because RapidOCR lost the
competing Small-Tray price**, not because the pairing was unambiguous:

```
'MixedVegetable Curry:'         REVIEW  price=60   assoc=0.000  (both prices found)
'Chana Masala:'                 ACCEPT  price=130  assoc=0.972  ($60 never detected)
'Chana/aloo/mushroomSaag:oS60'  ACCEPT  price=130  assoc=0.981  ($60 swallowed into the NAME)
```

Two accepted records carry an item **name containing the string `$60`** while the
price field says `$130`. Recognition 0.94–0.98, association 0.97–0.98, geometry
perfect.

**Rows where OCR worked are refused; rows where OCR dropped a price are
accepted.** No per-row confidence can see this — only a page-level fact ("this
page has two price columns and this row produced one price") would, and that is
not recorded. It is the same shape as the earlier `9.99 → 666` finding, running
in the opposite direction.

## Two more that are provenance, not recognition

- **Two price editions of the same menu are live in the same cache.**
  `img_3d486aeb50f2` is not a second capture of the flagship — it is an **earlier
  edition**. Of 25 comparable rows, **18 disagree**: Sambar Idly 6.99↔7.99, Plain
  Dosa 9.99↔8.99, Chicken 65 Dosa 12.99↔11.99. Nothing in the pipeline selects
  between editions; ingesting the wrong one makes every quote wrong by ~$1 **with
  full confidence**. The prior lane compared against a re-encode of one file while
  a genuinely different edition sat in the same cache, uncompared.
- **`NA` consumed as an item name.** Under ablation CAT-03 emits `NA = $180`. The
  strongest refusal signal printed on the page becomes an item.
- **The package qualifier becomes the item**: `Half Tray = $75`, `100 Count = $80`
  — the real name, one line above, never bound.

## The fitted prior, measured rather than adjusted

The previous lane's post-hoc `X.99`-form / magnitude prior — declared then as
fitted-to-one-failure and unproven — was measured over 481 proposals. It **fired
14 times and was never the deciding gate on any row**, and **12 of 14 firings are
false positives**: real menu items that genuinely cost under $3 (`Water 0.99`,
`Masala Chai 2.00`, `Plain Naan 2.49`) on a page whose column median is $14.99.
Its two real catches were already refused by other gates.

**Reported, not adjusted** — its cost is coverage on the drinks/sides/extras rows
every menu has.

## Refusal is truthful but not discriminating

Of 235 review rows, 164 carry a price and **56 (34.1%) would have been wrong or
phantom if accepted** — the other 108 carried a true printed value. Nine rows were
missed silently. So the system never asserts a falsehood, but it withholds roughly
**two correct claims for every wrong one it prevents**.

Reaching usable coverage costs a lot: with the existing `--ablate` hook (three
margin gates removed, nothing retuned) coverage goes 19.8% → **73.3%** and the
phantom rate goes 0% → **24.6%**, with tray tier undetermined on 96 of 126 correct
claims. Tier selection is not even consistent across pages (CAT-02 splits 14/15,
CAT-05 splits 28/12), so it cannot be repaired post-hoc by assuming "always the
smallest tray".

## Scorer falsification

The first run reported CAT-01B as 30/30 **wrong** — a scorer bug (prior GT uses
singular `price`, this lane used `prices`), caught by disagreement with the prior
lane rather than by the number looking odd. After the fix, a negative control
corrupting every GT price by +1 flips 30/30 and 4/4 to WRONG and restores cleanly.
The 0% is a live measurement, not a dead comparator.

## What was not resolved

No second capture exists for any of the four new images, so **no cross-photo check
was possible on the new GT** — its independence rests on high-zoom transcription
plus the negative control. CAT-11 was **declined as unresolvable** at 197×256
rather than transcribed. CAT-06 — the fifth distinct price document — remains the
largest un-ground-truthed gap. On CAT-05's oldstyle-figure font, my reading and
the engine's agree but both read the same pixels: corroboration, not independence.

## What this says about the queued work

The four queued items were aimed at roughly the right place. This measurement says
**the tray-grid model and the below-name case dominate** — they are what 4 of 5
real documents need. Intra-block splitting matters far less than assumed: that
layout class is effectively N=2 in real traffic.

Engine switch remains **HOLD**. Nothing here moves it.
