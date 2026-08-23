# Proposed remediation — six verified catering menu corrections

**Drift-check tag:** `Hermes-native` — a proposed-remediation record. **No
production data was mutated.** Every step below is read-only analysis; the
correction itself is staged, not applied.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Menu correction | the deployed `apply-menu-update` CLI is the canonical mechanism and is present on the box | use it if the correction is authorised — do NOT hand-edit the JSON |

Verdict: **no net-new engineering; the canonical path already exists.**

---

## Status: PRE-MUTATION COMPLETE, MUTATION NOT PERFORMED

Steps 1–3 of the operator's pre-mutation checklist are done and are recorded
below. The mutation is deliberately not applied: correcting owner-approved
production business data was placed outside the autonomous mandate.

## 1. Before-hashes

| object | sha256 (first 32) | note |
|---|---|---|
| `state/catering-menu.json` | `ea07c4c87e7e1726bab646ad9a41b6c8` | 78 items, `updated_by: photo-ocr` |
| `state/catering-proposals.json` | `5bc9727b6966ab4fa7ed498bdfb816ae` | |
| `state/catering-leads.json` | `6b238f899bdfe31681b0cdee67bc424b` | |
| `state/catering-quote-ledger.json` | `809f6cc66b685a34cbd9181e49c9444d` | |

`catering-pricebook.json` — **absent**. `catering-menu-pending.json` — absent,
so the pending slot is free for a canonical update.

## 2. Downstream objects derived from these entries

`catering-menu.json` is the **only** state file naming any of the six items. No
pricebook exists to regenerate. The derivations that consume menu prices are:

- `create-catering-proposal-options --auto-generate-from-menu` — builds **future**
  proposals from the live menu.
- `finalize-catering-menu` — prices a customer's selection **at finalize time**
  (`current_price = menu_index[item.name]`), freezing the result.

## 3. The two `CUSTOMER_FINALIZED` leads — and a third

Three leads carry an affected item, all the same one, `Poori Goat Curry`:

| lead | status | items | quote_total | affected |
|---|---|---|---|---|
| L0014 | CLOSED | 8 | 76 | `Poori Goat Curry` |
| **L0016** | **CUSTOMER_FINALIZED** | 8 | 76 | `Poori Goat Curry` |
| **L0020** | **CUSTOMER_FINALIZED** | 8 | 76 | `Poori Goat Curry` |

All three hold the identical 8-item selection and identical total — they are
rehearsal duplicates, not three different customers.

### The correction does NOT alter these leads — established, not assumed

`selected_items` stores a **price snapshot per row** taken at finalize
(`Poori Goat Curry qty=1 price=13` — already rounded), and the approve path
renders from *"the provenance frozen at finalize … and NEVER from
`selected_items`"*. `_render_quote_from_lead_state` emits `it.price_usd` from
that frozen record; it does not read the menu. The only code that consults
`menu_index` is `finalize-catering-menu`, which has already run for all three.

**So operator guardrail 5 does not fire.** Correcting the menu changes what
*future* proposals price at; it cannot retroactively change an existing lead's
quote.

Guardrail 4 is likewise satisfied: of the seven leads in `SENT_TO_CUSTOMER` or
`CLOSED`, **zero** have `quote_text` naming a corrected item (all carry the
`<legacy …>` sentinel), so no customer-visible quote text would be rewritten.

### A separate issue these leads do raise

Their frozen snapshots were priced off the **defective** menu — `Poori Goat
Curry` at 13 (from the erroneous 12.99) and `Ghee Karari Idly (3 Pcs)` at 8
(from the erroneous 7.99, and under a garbled name; the printed item is `Ghee
Karam Idly`). Correcting the menu neither fixes nor worsens them. Whether those
two live `CUSTOMER_FINALIZED` leads should be re-finalized is a **separate
operator question**, and per guardrail 4 nothing here touches them.

## The six corrections, as currently stored

| item | stored | → | category / tags as stored |
|---|---|---|---|
| `Tofu Dosa` | 11.99 | **REMOVE** | appetizer, `['veg']` — the whole row is invented |
| `Chocolate Dosa` | 10.99 | **7.99** | appetizer, `['veg']` |
| `Poori Bhaji (3 Pcs)` | 9.99 | **10.99** | appetizer, `['veg']` |
| `Chole Poori (3 Pcs)` | 9.99 | **10.99** | appetizer, `['veg']` |
| `Poori Goat Curry` | 12.99 | **13.99** | main, `['non-veg']` |
| `Extra Masala` | 1.99 | **3.99** | side, `[]` — printed name is `Extra Masala (8 oz)` |

`MenuItem` has a `notes` field, which is where the printed `8 oz` belongs; the
`name` is left alone so the row stays joinable to anything already referencing it.

## Canonical mechanism

`apply-menu-update` is installed, and the pending-menu slot is free. The
correction should go through it rather than editing JSON, so the change mints an
approval code and lands an audit row — the menu's own audit history currently
holds exactly **one** row (`catering_menu_finalized`, 2026-07-25).

## The one question that is not mine to answer

Everything above establishes the corrections are **faithful to the source photo**
the menu was extracted from. It does not establish that the photo reflects
today's prices. Two readings:

- **Extraction-fidelity** (recommended): the stored menu should faithfully
  represent the document it was derived from. Under this reading the six
  corrections are unambiguous and the phantom row is indefensible regardless.
- **Current-price truth**: if the restaurant's prices have moved since the photo,
  correcting toward it would encode stale values — a different, larger job than
  fixing an extraction defect.

The phantom `Tofu Dosa` should be removed under either reading.

## Not done, deliberately

The menu was not mutated. No lead was touched. No outbound message was sent.
Deposits remain disarmed (`deposit_pct: 0`) and the follow-up sweep timer remains
disabled — neither is affected by this record either way.
