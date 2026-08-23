# Deploy record — `24f5ba66`

**Drift-check tag:** `Hermes-native` — a deploy record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Deploy provenance record | none found — repo convention is `tasks/audits/deploy-authorization-<sha>.md` | use the existing convention (no code) |

Verdict: **documentation only.**

---

**Tag:** `deploy-20260823-221056-24f5ba66` · **DEPLOY_EXIT=0**
**Contents:** PR #763 only — P1-B, the menu-approval owner alert now describes the
measured state.

## Why this shipped

Applying the menu correction emitted a priority-1 owner Pushover saying the
pricebook *"stays at its previous version"* and *"Quotes still use the old
prices."* Both are false on this deployment: there is no pricebook at all, and
quoting falls back to the corrected menu. The owner was told the opposite of what
happened.

## What the fix establishes

The message is now built from **two measured axes** rather than one canned story:
the `reason` activation did not happen, and `PriceSource` — what actually prices a
quote on this deployment right now. Three price-source classes were each proven
from code, and the most important finding is that **the worst state had the
mildest message**: when the pricebook is present but unreadable, no new quote can
be finalized at all, and the old prose was no more alarming than for a by-design
decline.

A second site carried the same false sentence — the `apply_catering_menu_decision`
SKILL told the model to say it in chat. stdout now carries `price_source` /
`live_pricebook_version` / `pricebook_effect` and the SKILL quotes that verbatim,
so one sentence serves two surfaces and cannot drift. Skills manifest regenerated
in the same commit.

## Runtime verification

Read-only, against the deployed `/usr/local/bin/apply-menu-update` — imported as a
module so `main()` never runs; nothing applied, nothing sent, no state opened.

| check | result |
|---|---|
| five failure reasons, each distinguishable | present and distinct |
| `"Quotes still use the old prices"` | **gone from the source** |
| `"stays at its previous version"` | survives **only in docstrings** explaining why the old message was wrong — not as emitted prose |
| activation guards (`proposal_predates_pricebook_scope`, `active_pricebook_unreadable`, `import_unrunnable`) | all still present |
| owner alerts fired during verification | **0** |

Activation **logic** is untouched: guard conditions and reason strings are
unchanged; the returns widened only to carry `PriceSource`.

## Post-deploy state

`hermes-gateway` active · `shift-agent-cockpit` active · menu unchanged at 77
items v3 (`7511e426…`) · `.commit-hash` = `24f5ba66…` · smoke checks all passed.
Pre-existing unrelated warning: Agent #21 venv absent.

## Left for a product call

Alert **priority stays 1** for every failure outcome. A by-design decline on a
menu-only deployment arguably should not page at priority 1 — that is part of the
same false-urgency story — but changing alert priority is a behaviour change
beyond message truth, so it was flagged rather than done.

Three sibling instances of the same anti-pattern (one generic body reused across
materially different states) were found in shared send-path and kill-switch code
and deliberately left for their own change:
`_page_turn_send_budget_exhausted`, `_alert_agent_disabled_send`, and
`_alert_state_corrupt` — the last of which describes a quarantine that did not
happen and a backstop that actually fails closed, i.e. backwards.

## Rollback

Anchor `deploy-20260823-060843-91d675de`. Message-only change with no schema tag,
no Literal widened, and no new state object — none of the four rollback
categories applies.

## Unchanged

No customer message · deposits `deposit_pct: 0` · no lead re-finalized · no
pricebook created · `parse-menu-photo` untouched · no timer enabled.
