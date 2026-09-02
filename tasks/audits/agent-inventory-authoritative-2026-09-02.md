# Authoritative agent inventory, LID reachability, and the dispatcher question

**Date:** 2026-09-02
**Drift-check tag:** `Hermes-native` — an audit record; no runtime code, schema,
skill or config is introduced by this document.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Readiness / reachability audit | none found — repo convention is `tasks/audits/` | use the existing convention (no code) |

Verdict: **documentation only.**

**Supersedes** `agent-matrix-and-severity-correction-2026-09-01.md` for the
agent count, all statuses, and the LID finding. Two numbers in that record are
corrected below.

---

## 1. The count defect, resolved — 18 agents

The prior audit stated "18 agents" while its status counts totalled **20**.

**Cause:** `multi_location` is ONE directory containing TWO `SKILL.md` files
(`customer_location_query`, `multi_location_query`). It was split into two
matrix rows while every other multi-skill agent got one — `catering` ships 8
skills, `shift` 5, `flyer` 3, each on a single row. Plus an unlabelled sixth
`PARTIAL` slot.

**Rule applied consistently:** one directory = one agent. A second `SKILL.md`
inside one directory is a second *capability*, not a second agent. This matches
how `cfg.<agent>` keys and deploy gating are already scoped.

Registry authority: `git ls-tree -d origin/main:src/agents` → 18 directories.
`tools/skills-manifest.txt` (32 entries) is authoritative for *skill-content
integrity*, not agent count — `daily_brief` and `eod_reconcile` ship zero
SKILL.md and are systemd-timer scripts.

## 2. PRODUCTION_READY drops from 2 to 0

Under the tightened definition — deployed + gates proven + route reachable +
real entry point + **at least one genuine organic business E2E** + no
unresolved P0/P1 in the critical path — nothing qualifies.

`daily_brief` and `eod_reconcile` were previously PRODUCTION_READY on the
strength of "runs and sends without crashing," which is true and was
reconfirmed (a real send on 2026-09-01 with an `outbound_message_id`, clean
`journalctl` since 08-26, clean `--dry-run`). But `config.yaml:13` reads
`owner: name: "Srini (rehearsal owner)"` — the digest's recipient *and* the
activity it summarises are both the rehearsal identity.

> **No agent in this 18-agent fleet has processed a genuine paying-customer
> event end-to-end.** That is the honest floor.

| status | count | agents |
|---|---|---|
| PRODUCTION_READY | **0** | — |
| DEPLOYED_AWAITING_ORGANIC_E2E | 4 | daily_brief, eod_reconcile, shift, equipment_maintenance |
| BLOCKED_ON_REAL_DATA/CONFIG | 1 | compliance |
| DEPLOYED_AWAITING_APPLICABLE_DATA | 1 | multi_location |
| PARTIAL | 3 | catering, flyer, expense_bookkeeper |
| NOT_IMPLEMENTED | 1 | catering_followup |
| NOT_REACHABLE | 8 | cash_ar, employee_docs, hiring, inventory, pnl_anomaly, sales_tax, supplier, vip |

`0+4+1+1+3+1+8 = 18`, matching the directory count.

## 3. LID reachability — the prior finding was wrong in one number and incomplete in its diagnosis

**Correction: 4 of 6 active employees lack a LID, not 5 of 6.** Verified by
reading the live roster: `e001`–`e004` have `lid=None`; `e006` and `e008` both
carry values. (`e005`/`e007` also lack one but are `terminated`, so they do not
bear on the employee gate.)

**Zero of the six show a demonstrated production failure.** `e001`–`e004` have
never contacted the bridge under *any* identifier across ~4 months of retained
logs — latent risk, not a live incident. `e008` is the one account with
confirmed-working LID resolution, and it visibly migrated from phone-JID to LID
presentation between May and August, which is the drift phenomenon in question
caught in the act.

### The pipeline is doubly dead, and the upstream break is decisive

**Upstream — the bridge emits nothing.** Verified on the live
`bridge.js`: **zero** occurrences of `_shiftWriteLidCache`, `lidCache` or
`lid_cache` in the whole file. Line 212 carries its own retirement note:

> *"The LID -> phone cache backfill was retired 2026-08-01 (proven never to
> have fired: buildLidMap keyed bare LID digits, lookups used `<lid>@lid`)."*

So the mechanism was not merely disabled — it was **deliberately removed as
proven-dead code**, after a key-format mismatch meant it could never have
matched. `lid-cache.json` is literally `{}`, mtime 2026-05-01, predating the
removal — consistent with never having received a real write.

**Downstream — lid-learn is not wired.** Verified: no `/etc/cron.d/` entry, no
systemd unit, no `lid-learn.log`, zero `lid_learned` audit rows across 29
archived days. A correct install template exists in-tree at
`web/deploy/jobs/shift-agent-lid-learn.cron` and was never installed — the same
"correct in the repo, nothing installs it" class as the cockpit logrotate
config and `privileged_identity.py`.

**Fixing only the downstream accomplishes nothing.** Even hand-run today,
lid-learn would exit 5 (`EXIT_BAD_VERSION`) — `{}` has no `schema_version` key.

> The open question is **how the bridge should learn a phone↔LID pairing now**,
> given the previous approach is proven broken by design. That is a new
> decision, not a restoration of prior behaviour, and it is an operator call.

`WHATSAPP_LID_CACHE_WRITE=1` remains in `/root/.hermes/.env` and is now
vestigial — no code left to gate. Misleading to anyone reading it as "on."

### A live trap worth closing

`tools/patch-hermes.py` **still generates** `_shiftWriteLidCacheImpl` (4
occurrences on `origin/main`), while the newer
`tools/hermes-patch-port-v0191/patch1_port_v0191.py` correctly omits it and
carries the retirement comment. `shift-agent-deploy.sh:3083` requires
`patch-hermes.py` to be present in staging. **A future patch cycle run through
the stale generator would silently reintroduce code already proven broken and
deliberately removed.**

## 4. The dispatcher question — answered, and the answer is "don't build one"

**A dispatcher fix unblocks 0 of the 8 NOT_REACHABLE agents.** Every one of
`cash_ar`, `employee_docs`, `hiring`, `inventory`, `pnl_anomaly`, `sales_tax`,
`supplier`, `vip` contains exactly two files — `skills/<name>_dispatcher/
SKILL.md` and `__init__.py`. **No `scripts/` directory exists for any of them.**

Worse: their specified Phase-0 behaviour *is* the decline. `vip`'s SKILL.md
states `cfg.vip.enabled = False. Self-declines.` Routing to them successfully
would deliver a polite refusal. **Their blocker is that their handlers were
never written.**

**The genuine gap is two orphaned write kernels**, both installed on the box
with no in-tree caller outside a dead SKILL:

- `mark-compliance-item-done.py`
- `apply-expense-decision` — note `cfg.expense_bookkeeper.enabled` is **`true`**
  on the box, so this one is live-adjacent

The safe surface already exists and has shipped three times:
`src/plugins/shift-agent-read/` registers 5 tools under its own toolset name,
which survives `agent.disabled_toolsets` because that list is subtracted by
plain name. Semantic selection without `terminal` is **proven in production** —
`agent.log:4495-4503` shows `tool_search` → `tool_describe` →
`get_compliance_deadlines` completing with `terminal`, `skills` and `file` all
disabled.

Recommended first increment: a sibling plugin `shift-agent-act` exposing
**one** tool, `mark_compliance_item_done`, chosen because it is the only
orphaned write that is *recoverable*. It must be a sibling, not an extension of
`shift-agent-read` — that plugin's preflight requires all its declared tools
under one toolset, so merging would break the check or destroy the separate
kill switch.

**Kill switch needs no new machinery:** `/root/.hermes/config.yaml` is not
repo-managed, so a new toolset **ships dormant by construction** — the code
deploys and registers but is unreachable until an operator adds one line to
`platform_toolsets.whatsapp`. Disarm is deleting that line.

## 5. Two facts that qualify everything above

**Four real inbound WhatsApp messages in 30 days.** The retained archive holds
4,050 rows, of which 1,940 are `brief_skipped` cron noise and exactly 4 are
`cf_router_raw_body`. Every readiness statement in this document rests on that
denominator.

**Zero `dispatcher_routed` rows in the entire retained archive** — none from
the dead SKILL matrix, none from cf-router's own
`audit_dispatcher_routed` either. The routing-reliability metric that row feeds
reads zero and has no watchdog on it. Whether that reflects the traffic
denominator or a silent emit failure is **NOT_DETERMINED**; cf-router's emit
sits inside a bare `except Exception` that writes only to stderr.

## 6. NOT_DETERMINED, carried honestly

- Whether catering's 2026-08-23 event came from a real customer or the operator.

  **Settled separately: that event was NOT a half-failure.**
  `proposal_predates_pricebook_scope` is a deliberate fail-closed refusal — the
  proposal predated pricebook activation, so the owner's card never showed a
  price diff and they never consented to a price change. The menu applied
  correctly (v2→v3, 77 items) and the owner was notified with the remedy. It
  logs under `*_sync_failed` only because the `LogEntry` union has exactly two
  variants and no "not applicable" one — an audit-naming wart, not a defect.

  **Catering's PARTIAL therefore rests on absence of exercise, not on a
  defect.** Verified on the box: `catering.deposit_pct: 0` (deliberately off,
  operator-owned) and **no pricebook has ever existed** — only
  `catering-pricebook-template.json`. So two of three critical-path
  capabilities, deposit collection and price sync, have zero exercise history
  in either direction. Only menu-item application has ever run.
- Provenance of `e006`'s LID value — lid-learn never ran, so it was seeded some
  other way; `roster.json` has a single mtime for the whole file.
- Whether `/root/.hermes/skills/` is byte-identical to `skills-manifest.txt`
  (name-matched only this pass, not re-hashed).
- The tool-count at which the catalog listing degrades to names-only (~15 by
  token budget); needs a live turn to measure, which needs traffic.
