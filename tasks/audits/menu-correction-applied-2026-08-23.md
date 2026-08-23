# Menu correction APPLIED — six extraction-fidelity repairs

**Drift-check tag:** `Hermes-native` — applied through the deployed
`apply-menu-update` path. No code, schema or config changed; this records a
data correction to owner-approved state, authorised 2026-08-23.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Menu correction | the deployed `apply-menu-update` CLI is the canonical owner-decision handler | used it — no hand-editing of production JSON, no new code |

Verdict: **no engineering; canonical path only.**

---

## Result

| | before | after |
|---|---|---|
| sha256 | `ea07c4c87e7e1726…4058b` | `7511e426f4373cd9…babac` |
| items | 78 | **77** |
| version | 2 | **3** |

Snapshot retained at
`/opt/shift-agent/_archive/catering-menu.pre-fidelity-fix-20260823.json`, and the
script's own archive at `state/catering-menu-archive/menu-v2-1787512327.json`.

## The six, verified after the fact

| item | before | after |
|---|---|---|
| `Tofu Dosa` | $11.99 | **removed** (phantom — not on the source photo) |
| `Chocolate Dosa` | 10.99 | **7.99** |
| `Poori Bhaji (3 Pcs)` | 9.99 | **10.99** |
| `Chole Poori (3 Pcs)` | 9.99 | **10.99** |
| `Poori Goat Curry` | 12.99 | **13.99** |
| `Extra Masala` | 1.99 | **3.99**, `notes` now carries the printed `8 oz` |

**Nothing else moved.** Removed: exactly `[Tofu Dosa]`. Added: none. Five rows
changed, and only in `price_usd` — plus `notes` on `Extra Masala`, which was the
authorised way to preserve the printed `8 oz`. Menu validates against the `Menu`
model.

## Rehearsed on copied state first

The whole mutation was run end-to-end against a throwaway copy of the production
menu before production was touched. That rehearsal caught two things that would
otherwise have hit live state:

- `apply-menu-update` requires `--sender-role`; omitting it exits 2.
- A hand-picked confirmation code `#MFIX2` is invalid — `I` is not in the
  approval alphabet. The production run mints through `generate_unique_code()`
  instead, **inside** `code_generation_lock`, because that function's docstring
  is explicit that uniqueness only holds if the caller writes its own store while
  still holding the lock.

The production script also fail-closes: it aborts unless the live menu is
byte-identical to the artifact the authorisation was granted against.

## The pricebook question — answered, and the answer is "nothing created"

`apply-menu-update` documents "ONE approval, TWO effects" and activates a
pricebook. No pricebook exists on this box, so the concern was that the
correction would silently create a money-adjacent object that eight modules read
and whose absence currently *refuses* some paths.

It did not. Both in rehearsal and in production the activation declined:

> `proposal_predates_pricebook_scope` — this menu proposal was created before
> pricebook activation existed, so its approval card never showed a price diff.
> The menu is applied; prices are NOT.

`catering-pricebook.json` remains absent. Failure isolation worked as documented:
the menu persisted first and was never rolled back.

## Lead snapshots — unchanged, as predicted

| lead | status | quote_total | `Poori Goat Curry` snapshot |
|---|---|---|---|
| L0014 | CLOSED | 76 | 13 |
| L0016 | CUSTOMER_FINALIZED | 76 | 13 |
| L0020 | CUSTOMER_FINALIZED | 76 | 13 |

Status distribution identical before and after. The frozen per-row prices are
untouched, confirming the pre-mutation finding that `_render_quote_from_lead_state`
reads frozen provenance rather than the menu. **Neither `CUSTOMER_FINALIZED` lead
was re-finalized** — that remains a separate operator decision, as does the
`Ghee Karari Idly` / printed `Ghee Karam Idly` naming issue, which was
deliberately excluded from this change set.

## An owner notification fired, and I did not predict it

Audit trail: `menu_update_applied` → `catering_menu_pricebook_sync_failed` →
`owner_alert_dispatched` → `owner_alert_delivered` (pushover, priority 1),
titled **"Catering menu applied, pricebook NOT updated"**.

Before applying, I searched `apply-menu-update` for `notify-owner` and reported
that no notification path existed. The call is `notify_owner_with_fallback` at
line 235 — underscores, not a hyphen. **My grep matched one spelling and I read
absence of a match as absence of the behaviour.** No customer message was sent,
so the authorisation was not exceeded, but the prediction was wrong and the
owner received an unanticipated push.

Worth flagging separately: that alert's body says *"Quotes still use the old
prices."* On this box quotes are priced from the menu by
`finalize-catering-menu`, and there is no pricebook at all — so the sentence is
generic to the sync-failure path rather than accurate here. Not changed; recorded.

## Scope held

No customer message sent · deposits remain `deposit_pct: 0` · no money movement ·
no lead re-finalized · no pricebook created · `parse-menu-photo` untouched and
still on OpenRouter · no timer enabled.

## One process note against myself

The copied-state rehearsal used the **real** production `config.yaml`, which
carries live Pushover credentials, inside a network-enabled container running
production scripts. Nothing fired — verified afterwards: zero owner-alert rows on
the box in that window — but that was luck rather than design. Rehearsal configs
should have credentials redacted.
