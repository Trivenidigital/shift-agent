# Live catering menu contains invented and altered prices — 2026-08-23

**Drift-check tag:** `Hermes-native` — a finding record. No runtime code, schema,
skill or config is changed by this document, and **the live menu is deliberately
NOT edited here**: the menu is owner-approved via a `#XXXXX` code, so correcting
it is an operator action, not an autonomous one.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Menu extraction from a photo | Hermes vision via OpenRouter is what produced this data — it IS the incumbent | the finding is that the incumbent is wrong; replacement options are evaluated separately in the OCR lane, nothing built here |

awesome-hermes-agent ecosystem check: not applicable — this records a data
defect in already-extracted state. Verdict: **no engineering; finding only.**

---

## Summary

`/opt/shift-agent/state/catering-menu.json` — 78 items, `updated_by: photo-ocr`,
`source_image_id: "undefined"`, written 2026-05-06 — **contains a menu item that
does not exist on the source photo, and several prices that differ from the
printed ones in both directions.**

This is live data. It is what `create-catering-proposal-options
--auto-generate-from-menu` draws from, and what a customer-facing quote is
priced against.

## Verified first-hand against the source photo

The source is `/opt/shift-agent/.hermes/image_cache/img_8752e8048d17.jpg`
(1600×1112), cached 2026-05-06T00:34:36Z — 1m49s before the JSON was written.
`source_image_id` is the literal string `"undefined"`, so the link is by timing;
several near-duplicate scans bracket that window and carry identical prices, so
the comparison does not depend on which was used.

The Tiffins/Dosas column was cropped and upscaled 3× and read directly:

| live menu JSON | printed on the photo | defect |
|---|---|---|
| `Tofu Dosa` **$11.99** | **not present** — the dosa list runs Plain · Masala · Mysore Masala · Onion · Podi Karam · Guntur Karam · Amul Cheese · Nutella · Paneer · Ghee Karam · Chocolate · Egg · Chicken 65 · Chicken Tikka · Rava Onion · Rava Masala, with no Tofu | **PHANTOM ITEM** |
| `Chocolate Dosa` $10.99 | **$7.99** | **+$3.00** |
| `Poori Bhaji (3 Pcs)` $9.99 | **$10.99** | −$1.00 |
| `Chole Poori (3 Pcs)` $9.99 | **$10.99** | −$1.00 |
| `Poori Goat Curry` $12.99 | `Poori+Goat Curry` **$13.99** | −$1.00 |
| `Extra Masala` $1.99 | `Extra Masala (8 oz)` **$3.99** | took the **adjacent row's** price — `Extra Poori` is $1.99 |

The adjacent-row error is the most diagnostic one: it is not a misread glyph, it
is a row/price misalignment, which means the extractor's pairing was wrong, not
merely its character recognition.

Further, reported by the evaluation lane and consistent with the above but not
re-verified here: a garbled name (`Ghee Karam` → `Ghee Karari`), 11 further items
missing from that one section, ~78 of roughly 230 printed rows captured overall,
and the Dum Biryani sections' two-column `Single | Family` pricing collapsed to a
single price matching neither column.

## Why this is worse than a bad OCR score

The output was **fluent, schema-valid, and confident**. Every item validated
against `MenuItem`. `parser_notes` was empty. There is no confidence signal
anywhere in the record, so nothing downstream could have known to distrust it —
the defect is invisible to every consumer, including the owner-approval step,
because the card shows the extracted values and nothing to compare them against.

Two design observations about the incumbent extractor, from
`src/agents/catering/scripts/parse-menu-photo`:

- It asks the model for `dietary_tags` and `category`. Those are **inferred
  business facts, not observations** — the prompt instructs a model to decide
  from a photo whether a dish contains meat or egg, into a field an owner then
  approves with a 5-character code. That crosses the observe/reason boundary
  inside the extractor.
- `temperature=0` is commented as making extraction deterministic. That is not a
  determinism guarantee for a remotely-served model, and it is certainly not an
  accuracy one.

## Blast radius

- Two leads are `CUSTOMER_FINALIZED` with 8 `selected_items` each, priced from
  this menu.
- `deposit_pct` is **0**, so no money moves automatically, and the
  follow-up sweep timer is disabled — the exposure is a wrong quote reaching a
  customer after owner approval, not an incorrect charge.

## Deliberately NOT done

The menu was not edited. Correcting it means re-ingesting the photo or
hand-correcting prices, and the menu is owner-approved state — an operator
action. Catering was closed as production-ready on the previous ruling; this is
recorded as the "genuinely new runtime/customer evidence" exception rather than
as a reopening, and it is a **data** defect, not a code one.

## Operator options

1. **Re-ingest** the photo once a local extraction path exists that reports
   evidence and confidence (evaluated separately; the candidate that asserted
   zero wrong prices on both real menus is the ONNX PP-OCRv4 light tier).
2. **Hand-correct** the six confirmed rows now and delete the phantom item, then
   re-approve.
3. **Withdraw the menu** from auto-generation until re-ingested — proposal
   options would then need owner-supplied items.

Whichever is chosen, the phantom item should be removed regardless: an invented
dish at an invented price is the one row with no defensible reading.
