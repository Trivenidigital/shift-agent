# Operator Runbook — Hard Rules (cross-cutting)

**Drift-check tag:** Hermes-native (documents deployed operational discipline; adds no infrastructure).

## Hermes-first analysis

| Domain | Hermes skill found? | Decision |
|---|---|---|
| operational discipline / incident-derived rules | none found (introspection of our own deploy + on-box state procedures) | document in-repo |

This is the consolidated index of **hard operator rules** — the ones each earned by a
real incident. It is deliberately cross-cutting; per-feature operating detail lives in the
runbooks it points to:

- `docs/runbooks/premium-poster-v1-operations.md` — Premium Poster render branch, flag topology, /proc verification.
- `docs/runbooks/operator-ops-brief.md` — `tools/operator-brief.py` "what am I forgetting" surface.
- `docs/runbooks/flyer-model-policy.md` — model default + two-tier strategy.
- `docs/deploy.md` — full deploy mechanics.
- `tasks/readiness-packet-2026-07.md` §6 — design-partner onboarding + §5-breach preconditions (source for Rule 2).

Every rule below cites the incident that created it. The authoritative session record for all
2026-07 incidents is the memory file `project_ppv1_hardening_review_2026_07_02.md`.

---

## Rule 1 — Deploy: SHA-pin from the merge, verify the fix marker on-box, normalize CRLF

**Do:**
1. Build the deploy tarball from the **exact merge/squash SHA** (`tools/build-deploy-tarball.sh`
   runs the full pytest gate; build from the squash commit for a traceable deploy tag).
2. **Normalize line endings before the staging script runs.** The tarball packages the working
   tree; on a Windows worktree the checkout carries CRLF, which breaks shell scripts on the box.
   Run the box-side `sed` normalization (or `dos2unix`) on staged scripts before invoking them.
3. On rollback, **re-extract the tarball into `staging-new`.** Rollback *refills* `staging-new`
   with the OLD tarball — if you re-run without re-extracting you silently redeploy the old tree.
4. **Verify the fix is actually on the box** after deploy: grep the deployed file for a marker
   unique to the fix (not just "deploy succeeded"). A racing train can ship a fixless tarball.

**Incidents:**
- `#565` stale-quote fix (2026-07-06): the first deploy train **raced the merge and shipped a
  fixless tarball**; a second deploy (`eb781a40`) was needed. → SHA-pin from merge output +
  verify-fix-marker rule.
- Graduation deploy `deploy-20260704-171551-ee6126bd`: **CRLF from the Windows worktree broke
  the first attempt**; box-side `sed` normalization fixed it. The rollback-refills-`staging-new`
  trap was hit the same session.
- Extraction-v2 first deploy (2026-07-03): **deploy self-update chicken-and-egg** — the old
  `/usr/local/bin` deploy script lacked the new install lines; smoke caught it. Recovery = the
  documented Jun-30 re-extract-into-staging-new procedure.

---

## Rule 2 — On-box dispatch/routing replays: isolated store + allowlist-shaped stubs (HARD PRECONDITION)

**Before ANY on-box dispatch or routing replay — and as a HARD PRECONDITION before any
allowlist expansion:**
1. Run the replay against an **isolated store copy only** — copy the store and point
   `FLYER_PROJECTS_PATH` at a tmp path. **Never replay against the live store.**
2. Harness stub nets must be **allowlist-shaped**: stub every outbound boundary by default and
   permit specific reads explicitly. **Blocklist stubbing is prohibited** (a blocklist misses the
   one subprocess you didn't name).

**Incident:** F0213 (2026-07-06 01:04Z) — a routing replay's stub net **missed a
non-`invoke_`-prefixed subprocess** and *really finalized* the project, delivering 4 finals to
the pilot chat: EXECUTED-WITHOUT-RECORDED-APPROVAL. Reclassified as a **§5 hard-gate breach**
(unapproved customer-visible delivery), contained only by pilot allowlist scope. With a design
partner allowlisted, the same gap would have been customer-facing. (Preconditions written into
`tasks/readiness-packet-2026-07.md` §6.)

---

## Rule 3 — `.env` edit protocol (edit the target, assert the diff, verify /proc)

The on-box `/opt/shift-agent/.env` is a **symlink to `/root/.hermes/.env`**. Flags are read into
the gateway's **process environment at start** — editing `.env` does nothing until restart.

**Protocol (every time):**
1. **Never `sed -i` the symlink** — it replaces the symlink with a regular file and the deploy
   env-integrity gate fail-closes. Edit the **target** `/root/.hermes/.env`.
2. **Timestamped backup first:** `cp /root/.hermes/.env /root/.hermes/.env.bak-<tag>-<UTC>`.
3. **Assert the line-count delta** (e.g. 57→58 for a one-line add; 60→58 for a two-line delete)
   and that only the intended line(s) changed — byte-diff the rest.
4. **Check the symlink is still a symlink** afterward (`ls -l /opt/shift-agent/.env`); restore
   with `ln -sf /root/.hermes/.env /opt/shift-agent/.env` if broken.
5. **Quiet window:** confirm no in-flight renders, then restart the gateway.
6. **Verify /proc**, not the file:
   `tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value hermes-gateway)/environ | grep FLYER_`
   — confirm the new value **and** that unrelated flags survived the restart.

**Flyer feature-gate graduation (`*` wildcard, added 2026-07-11 after incident F0217):**
- Every flyer feature gate reads a `FLYER_*_ALLOWLIST` (or the cf-router `FLYER_INTENT_SHADOW_LLM_CHATS`).
  A single literal `*` entry (e.g. `FLYER_PREMIUM_OVERLAY_ALLOWLIST=*`) graduates that gate to **all**
  customers — the validated stack ships to every onboarded number without a per-customer env edit.
- Semantics are uniform and fail-closed: **empty/unset = DISABLED** (unchanged), `*` = enabled-for-all
  (explicit opt-in, composes with numbers — `*,+1732…` stays global), otherwise normalized membership.
  `*` is NOT the empty-list flip (the ledgered premium_overlay empty=global bug stays fixed).
- **Onboarding a new customer needs NO allowlist step** once the box's validated lists are `*`.
  Keep **per-number** lists only for scoped rollout of a NEW, not-yet-validated feature (original use).
- The cf-router shadow gate (`FLYER_INTENT_SHADOW_LLM_CHATS`) SUPPORTS `*` but setting it is PARKED
  pending the B1 privacy ruling (the shadow classifier sends chat text to an LLM).

**Incidents:**
- `sed -i` on `/opt/shift-agent/.env` **destroyed the symlink** → deploy env-integrity gate
  fail-closed (2026-07-02).
- `.env` line-8 mini-incident (2026-07-02): an unquoted multi-word `WHATSAPP_CANONICAL_REPLY`
  value tripped shell-sourcing; fixed with backup + exactly-one-line-changed assert + 57 lines
  preserved + symlink intact.
- Extraction-v2 activation (2026-07-03) and PARITY-flag removal (D7, 2026-07-06) both ran this
  protocol cleanly (57→58 add; 60→58 delete) with post-restart /proc verification.

---

## Rule 4 — Phone-action protocol: complete-spec SEND-THIS, staged UNQUOTED, ping-after-tick

The agent/strategy layer **never claims "sent."** It stages exact text; the human (SriniY) sends
from the phone and pings back after the tick.

**Every NEEDS-SRINIY phone action is ONE self-contained message** containing:
exact gesture/text · expected binding · expected outcome · success criteria. No follow-up
questions should be needed to execute a phone action.

**Stage briefs UNQUOTED.** Copying a brief out of a chat quote drags in blockquote-bar glyphs
(U+258E-class "tofu") that leak into the render as literal separator characters, and quoted
sends flatten into the router as fresh briefs / misbind APPROVE.

**Incidents:**
- Leak fix `#542` (2026-07-03): literal blockquote-bar glyphs copied from a chat quote leaked
  into a poster → "STAGE FINALE BRIEF UNQUOTED."
- F0211 / F0200 swipe-reply misroutes: a quoted brief flattened into the router body created a
  duplicate project. → ping-after-tick protocol tightened, recorded 2026-07-05.

---

## Rule 5 — On-box state-mutating CLIs run as the service user (`sudo -u shift-agent`)

Any CLI that mutates on-box state (project store, queue closes, backfills) **must run as the
service user**: `sudo -u shift-agent <cli>` — exactly like the smoke tests. Root-run atomic
rewrites leave files `root:root 600`; the gateway (`User=shift-agent`) then can't read them, and
a broad `except` swallows the `PermissionError` so the failure surfaces minutes later in an
unrelated-looking place.

**Incident:** Canary APPROVE double-bounce (2026-07-03 ~18:15Z) — `flyer-manual-queue --close`
run as **root** left `projects.json` `root:root 600` → the gateway's every in-process project
lookup silently returned empty → two APPROVE bounces before the ownership root cause was found.
Fix: `chown shift-agent:shift-agent` + mode 600; `find -user root` in the store now returns 0.

---

## Rule 6 — Worktree discipline (isolate before multi-commit; never touch a foreign worktree)

- **Enter a worktree (or `git worktree add`) before any multi-commit work.** The shared checkout
  `C:/projects/SME-Agents` has its HEAD switched by concurrent sessions.
- **Use explicit `cd <worktree>` prefixes** on box/local commands — cwd can be silently
  reassigned to another session's worktree.
- **Never clean or reset a foreign dirty worktree**, and establish provenance before deleting any
  branch/worktree you did not create.
- Parallel agents get **sibling worktrees off `origin/main`**, one per task; never reuse a branch.

**Incident:** During the 2026-07-02 hardening, a concurrent session **switched the shared
checkout's HEAD mid-commit** — the commit landed on a foreign branch
(`feat/live-trading-m1-multi-venue`); recovery required `git branch -f` + restoring the foreign
branch to its creator's SHA. The `#565` train also had its **cwd silently reassigned** to the
fix-batch worktree.

---

## Rule 7 — On-box process management: `pkill` foot-gun over SSH

`pkill -f <script>` over SSH matches the **ssh command's own remote shell** (the pattern appears
in its cmdline) and kills it before your target. The bracket trick (`pkill -f "[f]lyer-..."`)
also fails when the plain string appears elsewhere in the same command line (e.g. a `nohup`
path). **Put kill/check verbs in a separate SSH call that contains no path the pattern could
match**, and prefer `test -f <artifact>` over `pgrep` for liveness.

**Incident:** watcher restage races (2026-07-03) traced to `pkill -f` killing the SSH shell;
the bracket-pattern round-2 refinement recorded 2026-07-04.

---

## Rule 8 — SSH from Windows: two-step capture (redirect to file, then Read)

The Windows Bash tool **cannot capture SSH stdout** inline (every variant returns empty). The
only reliable pattern: `ssh main-vps '<cmd>' > out.txt 2>&1` in one step, then `Read` the file
in a second step. Never chain `&& cat`. (Host alias `main-vps` = 46.62.206.192; deploy is
tarball + `scp` + restart — no git checkout on the VPS.)

---

*Maintenance note: this runbook indexes rules, not runtime values. Verify live flag/state values
against `/proc` and the box per Rule 3 before acting — do not trust values quoted in any doc (§9a).*
