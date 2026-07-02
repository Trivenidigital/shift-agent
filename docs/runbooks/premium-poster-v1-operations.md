# Premium Poster v1 — Operations Runbook

**Drift-check tag:** Hermes-native (documents deployed behavior; adds no infrastructure).

Source of truth for operating the Premium Poster v1 render branch. Written from
the 2026-07-02 production-grade architecture review
(`tasks/flyer-premium-poster-v1-architecture-review-2026-07-02.md`).

## Current production posture (verify, don't assume — §9a)

```
FLYER_PREMIUM_POSTER_V1=1
FLYER_PREMIUM_POSTER_V1_ALLOWLIST=+17329837841     # scoped: ONE number
FLYER_PREMIUM_POSTER_V1_N=1
# FLYER_PREMIUM_POSTER_V1_TIMEOUT_SEC unset → 120s (clamp 30..180)
```

- Flags live in `/opt/shift-agent/.env` (systemd `EnvironmentFile`). They are read
  from the PROCESS environment — **editing `.env` does nothing until the gateway
  process restarts.** Verify what the running process actually sees:
  `tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value hermes-gateway)/environ | grep FLYER_PREMIUM`
- Empty/unset allowlist **disables** the branch (scoped-rollout guard — it never
  goes global like the older overlay gates).

## Env symlink topology + restart blast radius (added 2026-07-02 after incident)

- `/opt/shift-agent/.env` is a **SYMLINK** to `/root/.hermes/.env` (one shared env
  for the Hermes runtime and shift-agent). **`sed -i` on the symlink path DESTROYS
  the symlink** (writes a divergent regular file); the deploy env-integrity gate
  then fail-closes (correct). Always edit the TARGET (`/root/.hermes/.env`);
  restore with `rm /opt/shift-agent/.env && ln -s /root/.hermes/.env /opt/shift-agent/.env`.
  Convention for change backups: `cp /root/.hermes/.env /root/.hermes/.env.bak-<tag>-<UTCts>` first.
- **Gateway restart blast radius:** `systemctl restart hermes-gateway` takes the
  WhatsApp bridge down for the restart window. In-flight flyer renders are killed
  unless drained — the DEPLOY script drains `generate-flyer-concepts`/`finalize-
  flyer-assets`/`send-flyer-package` (not the bare renderer); a MANUAL restart
  drains nothing (check `pgrep -f generate-flyer-concepts` first). Inbound
  WhatsApp during the window is held by WhatsApp multi-device sync and delivered
  on bridge reconnect — verify `raw_inbound` continuity in decisions.log after
  any restart. Other bots on the multi-tenant box are separate systemd services
  and are unaffected.

## First real-brief E2E monitoring checklist (premium, managed path)

Pre-send baseline:
1. `wc -l /opt/shift-agent/logs/decisions.log` and
   `grep -c premium_poster_v1_managed_attempted /opt/shift-agent/logs/decisions.log` (baseline counts).
2. Confirm process env (`/proc/<pid>/environ`): flag=1, allowlist `+17329837841`, N=1.

Send one real flyer brief (food/grocery, business name + one single-price offer +
3-12 items) from the allowlisted number, then verify IN ORDER:
3. Routing rows for a NEW F0xxx project (dispatcher/cf-router + `flyer_project_created`).
4. Premium ladder for that project_id: `premium_poster_v1_managed_attempted` ->
   `_eligible` -> `_selected` -> `_final_pass` (a `_fallback_reason` instead of
   `_selected` = fell to legacy; the reason string says why — see taxonomy table).
5. Provenance sidecars exist next to the preview:
   `<preview>.ppv1.json` + `<preview>.ppv1-bg.png` in the project asset dir.
6. **Owner-notification vs customer-send marker:** the preview goes to the OWNER
   for review — at this point there must be NO `flyer_assets_delivered` row and
   the project sits at `awaiting_final_approval` with `final_asset_ids` unset.
   A customer send exists ONLY after APPROVE: `finalize-flyer-assets` rows, 4
   formats in `final_asset_ids`, `flyer_assets_delivered`, status `delivered`.
7. After APPROVE: all FOUR formats present in `final_asset_ids` (instagram_post +
   instagram_story must NOT be dropped — that was the pre-fix crop bug); verify
   the instagram_post artifact visually shows brand band + footer.
8. No dangling `selected` (every `_selected` paired with a `final_*` row); no new
   `/tmp/ppv1-*` files; no owner alert unless a reason was infra-shaped.

## Kill-switch checklist (something unsafe is happening)

1. `sed -i 's/^FLYER_PREMIUM_POSTER_V1=1/FLYER_PREMIUM_POSTER_V1=0/' /opt/shift-agent/.env`
2. `systemctl restart hermes-gateway` (deploy script drains flyer renders first;
   a manual restart during an in-flight render kills it — check
   `pgrep -f generate-flyer-concepts` first if you can wait).
3. Verify: the `/proc/<pid>/environ` check above shows the flag `0`.
4. Verify dormancy: next flyer render writes **zero** `premium_poster_v1_*` rows
   to `/opt/shift-agent/logs/decisions.log`.
5. Preserve evidence: copy `decisions.log` premium rows + any
   `*.ppv1.json` / `*.ppv1-bg.png` sidecars + `/tmp/ppv1-*` files before cleanup.
6. `FLYER_INTEGRATED_KILLSWITCH=1` is the harder stop (all generative render
   paths → pure deterministic Pillow). Since the 2026-07-02 hardening the premium
   branch honors it (deterministic model ⇒ branch not entered).

## Reading the observability (decisions.log)

Managed path (`flyer_premium_poster_v1_managed` rows): `attempted` (denominator,
every armed primary render incl. exception exits) → `eligible` → `selected`
(delivered) or `fallback_reason` → `final_pass`/`final_fail` (paired downstream
QA verdict for delivered posters). Bare path mirrors these as
`flyer_premium_poster_v1_bare` rows keyed by `chat_id`.

Fallback `reason` taxonomy (post 2026-07-02 hardening):

| reason | meaning | action |
|---|---|---|
| `ineligible` | armed but non-food / missing facts / composer-unfit (multi-price, >12 items, regional script) | none — working as designed |
| `no_food_winner:image_has_text=N` | model painted text on all candidates; OCR gate rejected | none — fail-closed by design; watch rate |
| `no_food_winner:check_error=N` | **vision OCR outage** — candidates could not be verified | **pages owner**; check vision provider / key |
| `no_food_winner:generation_failed=N` | image generation failed (detail carries `generator_error:<T>:<msg>`; TimeoutError = budget exhausted) | **pages owner**; check OpenRouter key/quota |
| `exception:<T>:<msg>` | unexpected error in the premium branch | **pages owner**; investigate |
| `unsupported_size` | non-preview size requested premium (should not occur on the primary path) | investigate wiring |

Health probes:
- Fire-vs-outcome pairing: every `selected` should be followed by a
  `final_pass`/`final_fail`. A dangling `selected` = crash/SystemExit between
  render and QA (version-guard race, supervisor kill).
- `grep -c 'premium_poster_v1_managed_attempted' decisions.log` vs `_selected`
  gives the delivery rate. Sustained 0% delivery with infra-shaped reasons =
  the premium path is dead while customers silently get the legacy design.

## Finals / approval fidelity (2026-07-02 fix)

A premium delivery writes `<preview>.ppv1.json` (provenance) +
`<preview>.ppv1-bg.png` (OCR-verified winner background) and removes any stale
`.raw.png` sibling. `render_final_package` uses provenance — never mtime — to
derive finals: whatsapp_image/PDF direct from the approved preview;
instagram_post/story recomposed at the target aspect from the saved background,
letterboxed if the composer refuses. **Never approve-and-send from a box running
pre-fix code for a premium preview** — the old path center-cropped the brand
band + footer off both Instagram formats and silently dropped them.

## Broadening runbook (adding ANY number beyond +17329837841 — operator decision)

Do **not** broaden until every box below is checked:

- [ ] Finals fidelity fix deployed AND one premium project has gone through
      APPROVE → finalize → send with all 4 formats delivered.
- [ ] Bare-path telemetry deployed (`flyer_premium_poster_v1_bare` rows appear
      for a bare-path test) — or the bare path confirmed unreachable
      (`cfg.flyer.enabled=true` keeps traffic managed).
- [ ] Owner-review path only: decide per-path arming policy first if the bare
      path could receive broadened traffic (bare has NO owner gate; QA +
      visible-contract are the only nets, and `FLYER_BARE_SKIP_VISUAL_QA=1` is
      set on the box today).
- [ ] Verify running-process env (`/proc/<pid>/environ`), not `.env` content.
- [ ] `cfg.flyer.concept_count` is 1 (premium fires on C1 only, but budget/UX
      math assumed one concept).
- [ ] Grocery-brief scene mismatch accepted or fixed (scene families are
      restaurant/Indian-biased; the flagship account is a supermarket —
      review finding PQ-1).
- [ ] Fallback-reason distribution over the scoped period reviewed: infra-shaped
      reasons ≈ 0; `image_has_text` rate acceptable (each costs a generation).
- [ ] Latency accepted: worst case ≈ budget (120s) + one OCR/critique overrun
      (≤60s) + full legacy ladder after a premium miss.
- [ ] Update `FLYER_PREMIUM_POSTER_V1_ALLOWLIST` (comma-separated, `+`-prefixed
      or LID), restart gateway, verify via `/proc`, send one internal test.

## Production-readiness checklist (before calling the feature production-grade)

- [x] Fact safety: composer never paints truncated/partial/multi-price facts
      (fail-closed, 2026-07-02).
- [x] Kill-switch totality, repair-note guard, C1-only guard (2026-07-02).
- [x] OCR schema-drift fail-closed (`extracted_text` missing ⇒ outage).
- [x] Precise fallback reasons + owner alert on infra-shaped failures
      (alert fires on the MANAGED path only; the bare path emits audit rows
      but does not page — pair with the B2 watchdog before broadening bare).
- [x] Bare-path audit rows; managed denominator on exception exits.
- [x] Temp hygiene (`ppv1-bg-*`, critique PII PNGs).
- [x] Finals derivation provenance-aware (no crop, no stale raw).
- [x] Blocking CI for the premium suite (`flyer-premium-ci.yml`).
- [ ] Paired-count watchdog (selected-without-final, attempted-vs-armed) in a
      periodic report (§12a — review B2; backlog).
- [ ] Owner-facing premium/fallback caption + premium-aware revision story
      (review B3/C2; backlog).
- [ ] Critique sidecar persistence at N=1 or drop decision (review B4; backlog).
- [ ] Grocery scene family (review B5; backlog).
