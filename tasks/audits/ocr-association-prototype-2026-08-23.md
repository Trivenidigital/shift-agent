# RapidOCR association prototype — findings, and why the engine does not switch yet

**Drift-check tag:** `Hermes-native` — a research record. No runtime code, schema,
skill or config is changed. `parse-menu-photo` is **untouched** and still uses
OpenRouter. The prototype lives outside the repo; this document preserves what it
established, because the findings outlive the scratch directory.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Local document extraction with positional evidence | none — Hermes' path is a remote vision model, which is the incumbent being measured | evaluate a local CPU engine; nothing built into the runtime |

Verdict: **research only; no net-new production engineering.**

---

## Verdict: NOT READY to switch `parse-menu-photo`

The success criterion is met where ground truth exists, and the negative control
genuinely declines — but three things block the switch, and two of them are
findings that matter beyond OCR.

## What was built

An **evidence layer** (`text`, `bbox` in original image pixels, confidence, page,
`source_image_sha256`, engine + version, per-block ids) feeding a **separate
proposal layer**. Nothing writes `MenuItem` directly. Every downstream claim
references the evidence block id it came from.

Price↔item association is a **first-class asserted property** with its own
confidence, computed as the **minimum** of five components — overlap, item-side
margin, price-side margin, row-pitch fit, column fit — so one weak component
sinks that item alone and the binding constraint is recorded. Hard gates force
review below fixed thresholds set before the fixtures ran.

Source identity confirmed: the corpus image is **byte-identical** to the box's
`img_8752e8048d17.jpg` (sha256 `f6e9e6af…d518`, 433,387 B), independently
re-hashed against the copy pulled during the menu-defect verification.

## The six historical failures — 6/6 pass

| fixture | required | produced | assoc |
|---|---|---|---|
| Tofu Dosa | **absent** | absent | – |
| Chocolate Dosa | 7.99 | 7.99 | 0.86 |
| **Extra Masala (8 oz)** | **3.99, not the adjacent 1.99** | **3.99** | 0.78 |
| Poori Bhaji (3 Pcs) | 10.99 | 10.99 | 0.90 |
| Chole Poori (3 Pcs) | 10.99 | 10.99 | 0.76 |
| Poori+Goat Curry | 13.99 | 13.99 | 0.71 |

`Extra Poori` separately gets its own 1.99, and `Extra Sambar` — whose inline
prices the engine dropped — goes to review rather than stealing a neighbour's.
One-to-one assignment is what prevents the theft.

## Finding 1 — on a clean image, the confidence math is not what fixes the bug

A baseline with **columns but no gates** passes all six fixtures too. What
actually repaired the historical failures is **coordinate-based column
segmentation**: the old vision model had no coordinates and OCR does.

The gates still earn their place, but elsewhere — that same ungated baseline
already admits **2 confident phantoms** on the unperturbed page, one of them
`ExtraSambar-` taking Extra Masala's $3.99, which is literally the original
defect's failure class.

Stating this plainly matters because the tempting claim — "the confidence triple
fixed it" — is false on the evidence.

## Finding 2 — the triple is orthogonal to character error (`$666.00`)

On a **second, independent photograph of the same printed menu**, `Plain Dosa`
was accepted at **$666.00**. RapidOCR read `9.99` as `666` at **confidence
0.968**, and the association was geometrically perfect: overlap 1.00, both
margins 1.00.

**All three confidence components were high and the answer was garbage.** Item
confidence, price confidence and association confidence cannot see a
character-recognition error — they measure whether the right *box* was paired,
not whether the *glyphs* were read correctly.

A column-level prior does catch it (162 of 164 accepted prices on that page are
`X.99`-form; `666` is both a minority form and a 74× magnitude outlier), and it
now routes to review. **Declared: those two thresholds were added after observing
the failure, so they are fitted to it and generalisation is unproven.**

## Finding 3 — the layer detects ambiguity, not incorrectness

Negative control: translate the price sub-column down by `dy` pixels in the real
JPEG and re-run end to end.

| dy | gated accepted | gated **wrong** | ungated accepted | ungated **wrong** |
|---|---|---|---|---|
| 0 | 30 | **0** | 32 | 2 |
| 6 | 7 | **0** | 61 | 32 |
| **9** (≈ half a row pitch) | **0** | **0** | 63 | **47** |
| **12** | **23** | **17** | 63 | 52 |

At `dy=9` the pipeline **accepts nothing** — it declined rather than guessed,
which is the required behaviour. The rival explanation, "the perturbation just
broke OCR", is falsified: block counts (517/516/517/516) and mean confidences
(≈0.95) are flat across every `dy`. Only the geometry changed.

But at `dy=12` confidence **recovers** and produces **17 confidently wrong
prices**, uniformly off by one row — the prices now align cleanly with the *next*
row, so the reading is internally consistent and unambiguous. A skew or
systematic row-offset defeats this design entirely. A landmark anchor on the
first/last row would be the defence; it is not built.

## Coverage — the number that most blocks the switch

Across 13 real images plus one degenerate: **914 proposed, 316 accepted, on 4 of
14 images.** Most of the zeros are *correct* refusals — CAT-02's four tray-price
columns give every row four equally plausible prices, so all 38 rows go to review
(the same page where PaddleOCR-VL confidently mis-priced 16 of 30). Others fail
closed because the price sits inside the same OCR block as the name
(`'chicken -$15.99'`), which is a real and fixable limitation.

**Switching today would move Catering from "sometimes confidently wrong" to
"usually silent."** That may be the right trade, but it is a product decision,
not a readiness claim.

## Dual-engine corroboration is worthless here

Cross-engine agreement covers **2 of 31 items (6%)**. On 28 items only RapidOCR
is confident and Tesseract's silence carries no information. Worse, the `666`
case shows the shape of the danger: a second engine agreeing would have made a
wrong answer *look safer*. Combined with the prior lane's finding that both
engines share the `I→1` confusion, agreement is negatively informative on exactly
the glyph class most likely to corrupt a price.

Also recorded: **preprocessing that helps character recognition destroys the
geometry the layout stage needs.** The preprocessed variant produced 1631 boxes
with no zero-coverage x-gaps, collapsing a 4-column page to one region.

Degenerate input reproduced: RapidOCR emits 0 blocks on the all-black image;
Tesseract emits 249 — and the gates contain it to **0 accepted** even when the
engine does not.

## What is unverified

- **~257 of the 316 accepted items have no ground truth.** The criterion rests on
  31 pixel-verified items plus 29 cross-photo confirmations. This is the largest
  gap by far, and it is GT-limited rather than algorithm-limited.
- Only uniform vertical translation was perturbed; rotation, shear and
  perspective are untested.
- Single-price-per-item only; tray grids fail closed.
- **Expense / Compliance / Equipment remain N=0 real samples**, re-confirmed on
  the box: the expense receipts directory exists and is **empty** (created
  2026-05-03, never written), there is no equipment state directory, and every
  one of the 249 cached images is a menu or flyer. Those agents stay
  **unvalidated**; the synthetic receipts are not evidence.

## Recommended order — measurement before more engineering

1. **Pixel-verify 3–4 more real menus.** Every number above is limited by ground
   truth, not by the algorithm.
2. Intra-block price splitting — the largest coverage win.
3. A multi-price-column model for tray grids.
4. A landmark anchor against the `dy=12` class.
5. Only then re-measure.

The engine switch stays a separate, later, gated change. OpenRouter is designed
*for* as a governed escalation path but **is not wired**, and must never
overwrite contradictory OCR evidence.

## Holds observed

PaddleOCR-VL not wired into any money-bearing path · Tesseract kept as baseline
only · **Unlimited-OCR never downloaded, imported or executed** (its `eval()` on
model output remains unreviewed) · no production change · box read-only.
