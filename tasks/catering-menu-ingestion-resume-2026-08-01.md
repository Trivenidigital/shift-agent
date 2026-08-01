# Catering menu ingestion — RESUME STATE (2026-08-01)

**Drift-check tag:** `extends-Hermes` — everything below reuses deployed
primitives (cf-router precedence chain, `update_catering_menu` SKILL,
`parse-menu-photo`, the `#XXXXX` pending-proposal approval, the M2 pricebook
importer, the deterministic pricing kernel). No new subsystem is proposed.

**Read this first when resuming.** It is the single source of truth for where
the owner-menu → pricebook workflow stopped and what the next action is.

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| Vision menu extraction | yes — `update_catering_menu` SKILL + `parse-menu-photo` | reuse unchanged (INSTALLED, never yet executed in prod) |
| Owner approval of a menu change | yes — pending proposal + `#XXXXX` verb path | reuse unchanged |
| Conversational turn handling | yes — Hermes agent + dispatcher matrix | reuse; the open blocker lives HERE |
| Deterministic pricing/import | in-tree (M2 kernel + importer) | reuse unchanged |
| Routing precedence | in-tree (cf-router) | extended once, already shipped (PR #667) |

awesome-hermes-agent ecosystem check: no skill covers "make the agent's tool
selection provable" — the open work is instrumentation + an outbound-claim
invariant in our own tree, not a missing Hermes capability.

## Production state at hand-off

```
Release      d01c88a669fa58d2374341a7dc02d976006e29b6
Deployment   deploy-20260801-201411-d01c88a6
Artifact     78adb78b26834c8175a4f3859606acb9cf48fd0b9c5e49721ae02d184aea7eda
             (retained at C:/projects/_artifacts/shift-agent-d01c88a-78adb78b.tgz)
Rollback     deploy-20260801-161742-35cfac5f
Snapshot     /root/pre-d01c88a-state-20260801-201309.tgz  (sha cfd97e1d…, 1907 entries)
Posture      DARK — GATE=0, ARM=0, follow-up OFF, budget OFF, TE socket absent,
             deposit_pct 0, gateway active, bridge connected, queue 0
Commercial   catering-menu.json ea07c4c8…58e (v2) · pricebook ABSENT ·
             pending proposals 0 · #codes 0 · customer messages 0
```

**Operator-applied HOLD (do not silently revert):**
`FRONT_BRAIN_CONVERSE_CHATS` was emptied (both owner aliases removed) to test a
hypothesis; the empty value is fail-closed by code. Backup:
`/root/.hermes/.env.bak-pre-converse-bypass-1785616758`. It stays empty until
the false-success invariant below ships — restoring it re-arms a path that
demonstrably tells the owner an action succeeded when it did not.

## What is PROVEN working

- **PR #667 menu-caption cession.** Fired 3/3 live (20:18:03, 20:27:49,
  20:40:48) with `sender_role=employee; has_media=true;
  skill=update_catering_menu`. Flyer claimed none (project count 226 throughout).
- **PR #664 adapter + PR #665 legacy-pending guard** — merged, CI-green, not yet
  exercised live (they sit downstream of the blocker).
- All deployed infrastructure verified present: SKILL installed at
  `/root/.hermes/skills/update_catering_menu/`, `parse-menu-photo` installed
  `-rwxr-xr-x`, dispatcher matrix row present ("Image OR document attachment +
  caption mentions 'menu' | owner OR employee | update_catering_menu").

## THE OPEN BLOCKER — the agent turn never invokes the SKILL

**Symptom:** a correctly-ceded owner menu photo produces a conversational reply
describing the image, and `parse-menu-photo` never runs.
**Hard evidence:** `menu_update_proposed` count across the ENTIRE production
audit log is **0** — the extractor has never executed on this box in any
configuration.

**Hypothesis already TESTED AND DISPROVEN:** front-brain CONVERSE absorbing the
turn. Both owner aliases were removed from `FRONT_BRAIN_CONVERSE_CHATS`, the
gateway restarted, the photo re-sent — identical behaviour. Converse is
exonerated; do not re-litigate it.

**Remaining candidates (untested):** the model's skill-selection for media turns;
whether the dispatcher SKILL is consulted at all on a media inbound; a
frontmatter/tool-permission mismatch that silently prevents the SKILL's bash
step. None can be distinguished from the audit log, because the gateway journal
carries only systemd session lines — **there is no per-turn skill-selection or
tool-call logging**. That absence is itself the first thing to fix.

**Next action (needs authorization — invokes the vision LLM, writes nothing):**
run the deployed extractor directly against the cached image, dry-run:

```
/usr/local/bin/parse-menu-photo \
  --image-path /opt/shift-agent/.hermes/image_cache/img_a6b168b1d361.jpg \
  --owner-phone +17329837841 --dry-run
```

Exit 0 with items ⇒ extractor healthy, defect is model tool-selection.
Non-zero ⇒ defect is in the extractor (vision creds / schema / model access),
and the model's silence was a symptom, not the cause.

## HIGH — ungrounded operational-success claim (blocks converse restore + pilot)

The model twice told the owner an action had completed when nothing had:

- 20:18:26 — *"I've sent a message asking for the key items…"* → outbound screen
  REFUSED it (`invented_operational_claim`, hit value `sent`), template fallback.
- 20:28:13 and 20:41:16 — *"The menu update … has been **successfully
  recorded**"* → **verdict `passed`, no fallback** — reached the owner intact,
  with converse disabled on the third occurrence.

**Required invariant (not a phrase blacklist):** a model-generated statement
that an operational action succeeded must be backed by a verified action result
or durable receipt (audit row / state mutation / action-execution context).
Implement by extending the existing outbound-claim validation hook if it can
carry it; otherwise it is the next standalone safety PR. Blocks: restoring the
owner chat to converse mode, and customer pilot activation.

## Ordered plan to resume

1. Authorize + run the `--dry-run` extractor probe above; classify the blocker.
2. Add per-turn skill-selection/tool-call observability (currently zero) — the
   blocker cannot be diagnosed twice without it.
3. Fix whichever layer the probe implicates (extractor vs tool-selection).
4. Ship the false-success invariant; only then restore
   `FRONT_BRAIN_CONVERSE_CHATS` to `+17329837841,201975216009469@lid`.
5. Re-run the authorized staging test: one photo → one pending proposal → one
   `#code` → menu + pricebook byte-identical → zero customer sends.
6. Only then: `#code` approval → first pricebook version (lands
   `placeholder=True` by design, since no pricebook exists) → hand-calculated
   quote verification.
7. Stage A identities → containment → qualification for the supervised pilot.

## Standing gates (unchanged)

`#code` approval NOT authorized · pricebook activation NOT authorized · Stage A
HOLD · qualification/acceptance/follow-up OFF · customer pilot NO-GO ·
Pushover owner-alert delivery still unproven (owner-waived; no active device —
license lapsed, so §12b pages are accepted-and-dropped and the WhatsApp
fallback never fires).
