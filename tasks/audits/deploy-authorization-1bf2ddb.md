# Deployment-Authorization Record — main `1bf2ddb` (2026-08-08)

**Status:** DEPLOYED. `deploy-20260808-043243-1bf2ddb9`, all smoke checks green,
gateway active since 2026-08-08T04:33:04Z. Recorded after the fact per the
standing "recorded approval or it didn't happen" rule.

## 1. Authorization source

Operator instruction, this session, verbatim intent: *"Proceed with the normal
full-tree deploy from merged main + #676, but first do one bounded pre-deploy
delta check of the 37 commits… If there is no BLOCKS_DEPLOY: merge #676, run the
normal full-tree deploy, verify the owner-menu workflow, and finish."* Explicitly
forbidden and NOT done: plugin-only hot copy, any production state matching no
commit, a new architecture review, a new HOLD absent a concrete unsafe change.

## 2. SHAs

- **Live before:** `d01c88a` — `deploy-20260801-201411-d01c88a6` (verified via
  `shift-agent-deploy.sh list`; `staging-new/.commit-hash` also `d01c88a`).
- **Target / live after:** `1bf2ddb` = origin/main tip after merging PR #676.
- **Range deployed:** `d01c88a..1bf2ddb` = 39 commits (37 pre-existing on main +
  the 2 from #676). Artifact sha256 `81712d74f79c36ac62c475849adb05bc8ab94b0405f2361f1f9cccd95a3bd2c9`,
  verified identical after SCP.

## 3. Pre-deploy delta check (8 points, all cleared)

1. **Diff obtained** — 27 files, +3169/−127 across `src/` + `tools/`.
2. **Destructive deletes** — NONE. Zero deletions and zero renames under `src/`
   (`git diff --name-status --find-renames`). The 127 deletions are confined to
   `tools/` (pin-gate rewrite + baseline), which is gate input, not runtime.
3. **Migrations / irreversible state** — NONE. No migration, `ALTER`, `DROP`,
   backfill, `rmtree` or `unlink` in any added runtime line.
4. **Config / env / secrets** — NONE. Only `src/plugins/shift-agent-policy/plugin.yaml`,
   which hash-matches the already-live file. No `config.yaml`, no `.env`, no
   credential surface.
5. **systemd enable/disable** — ONE: `systemctl enable --now hermes-version-check.timer`.
   Verified already `enabled` + `active` on the box beforehand, so idempotent — a
   genuine no-op, not a state change.
6. **Credential / auth / tenant / money behavior** — NONE. Keyword scan of added
   runtime lines returned only comment text from the #676 diff itself.
7. **Live-vs-tracked blob identity** — cf-router `hooks.py` / `actions.py`
   hash-matched their `d01c88a` blobs exactly (no un-versioned drift to lose), and
   all three `shift-agent-policy/` files plus `/usr/local/bin/shift-agent-policy-preflight`
   (`e9ffe2cb…`) hash-matched their tracked blobs — so `rsync -a --delete
   src/plugins/ → /root/.hermes/plugins/` removed nothing live and changed no gate.
8. **Rollback available** — `deploy-20260801-201411-d01c88a6.tgz` present in
   `/opt/shift-agent/deploys/` (plus 4 older releases), nightly encrypted state
   backups through `2026-08-08-0200.tar.gz.gpg`, and the deploy script's own
   smoke-failure auto-rollback.

**Classification of changed runtime-reachable artifacts**

| Artifact | Class |
|---|---|
| `cf-router/{hooks,actions}.py` | EXPECTED_BUT_NEEDS_RESTART (gateway loads plugins at startup) |
| `platform/{safe_io,schemas}.py` | EXPECTED_BUT_NEEDS_RESTART (gateway import; scripts pick up per-invocation) |
| `flyer/action_registry.py` | EXPECTED_SAFE (additive passthrough, defaults False) |
| `flyer/flyer_copy_archetypes.py` | EXPECTED_SAFE (F0190 CCA shared_price fix, additive) |
| `shift-agent-policy/*`, `shift-agent-policy-preflight` | EXPECTED_SAFE — byte-identical to live; no-op |
| `hermes-version-check` + 3 systemd units | EXPECTED_SAFE — timer already enabled+active; no-op |
| `shift-agent-deploy.sh` | EXPECTED_SAFE (named preflight install present; idempotent timer enable) |
| `tools/check-shift-agent-patch.sh`, `hermes-patch-baseline.txt` | EXPECTED_SAFE — fail-closed gate; failure aborts BEFORE mutation |

**BLOCKS_DEPLOY: none.**

## 4. Deploy execution + verification

Hermes pin gate **PASSED** (`OK: shift-agent patches verified against pinned
Hermes cc4cab2f`); policy preflight **PASSED** (`screening live: plugin loaded,
hook registered, ScreenedWhatsAppAdapter resolved`). Two informational WARNs,
both pre-existing and unrelated: Hermes `version=unknown` (commit-hash pin is
authoritative) and venv lacking `stripe` (commerce not armed on this VPS).
`=== All smoke checks passed ===`.

Post-deploy integrity — live files hash-match their `1bf2ddb` blobs exactly:

| File | sha256 |
|---|---|
| `/root/.hermes/plugins/cf-router/hooks.py` | `749a56c4…` |
| `/root/.hermes/plugins/cf-router/actions.py` | `6e5f506c…` |
| `/opt/shift-agent/safe_io.py` | `108485f7…` |
| `/opt/shift-agent/schemas.py` | `8783d5c5…` |

Both plugin dirs survived `rsync --delete`; `shift-agent-policy/policy.py` still
`db0d762e…` (unchanged). `/opt/shift-agent/.commit-hash` = `1bf2ddb9…`.

## 5. Live behavior proof (read-only, no sends, no audit writes)

Probe against the DEPLOYED tree with `_emit_audit_row` neutralised:

- `claims_action_completed` present on the deployed `ActionExecutionContext`, default `False`.
- unverified completion claim → **REFUSED** (`__regulated_lint_fallback__`).
- verified completion claim → allowed.
- non-claiming unverified send → allowed (keeps honest failure copy deliverable).
- `"Menu saved."` (contains no listed verb) → **REFUSED**, and the deployed
  `lint_no_unverified_completion` does **not** flag the live 2026-08-01 phrase —
  together proving the refusal is context-bound, not a keyword blacklist.
- `find_menu_pending_by_update_id` returns `None` for a bogus and an empty id.
- `/usr/local/bin/parse-menu-photo` present; route wired in the deployed hooks.

## 6. Residual / not done

`menu_update_proposed` = 0 and `catering-menu-pending.json` absent — a clean
pre-proof baseline. The live owner-menu trace requires one real menu photo sent
from the owner's phone, which is operator-owned and still outstanding; broad
autonomous Catering remains disabled and `FRONT_BRAIN_CONVERSE_CHATS` untouched.
