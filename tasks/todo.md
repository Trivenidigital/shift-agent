# Backlog — pending items

Living checklist. Items grouped by priority; each completed item gets `✅` and a date.
For history of *completed* multi-phase initiatives (platform extract, sender-id, agent #2/4/5, etc.), see git log + `tasks/all-phases-*.md`.

Last updated: 2026-04-28 (post-PR-#22 catering edge case v3.1)

---

## P0 — Live verification (passive, blocked on real customer traffic)

Reporter floor as of 2026-04-28: **0/26 (0%)** — all 26 entries are pre-fix synthetic test injections; no real Kimi-routed inbound since dispatcher schema deployed. Floor will move once real traffic starts. Trigger: send any test message to self-chat to validate the pipeline end-to-end.

- [ ] **Verify dispatcher routing live** — next real inbound to your self-chat should produce a `dispatcher_routed` entry in `decisions.log` within ~10s of the matching `raw_inbound`. Run `sudo /usr/local/bin/dispatcher-accuracy-report --days 1` to check. Validates PR #14 + #15 end-to-end.
- [ ] **Verify menu photo upload pipeline** — auxiliary-vision auth fix (OPENROUTER/OPENAI keys mirrored into `/opt/shift-agent/.env`) is unverified live. Send a menu photo to self-chat; expect `parse-menu-photo` to extract items → owner preview reply with confirmation code.
- [ ] **Run dispatcher-accuracy-report after first real inbound** — confirm coverage % climbs above 0% as real traffic accumulates.

## P1 — Architecture review follow-ups (from reviewer thread, 2026-04-28)

### Test pyramid investments

- [ ] **Layer C — recorded replay** (medium effort, high value). Curate ~30 message fixtures from accumulated `decisions.log` (~2 weeks out) + JSONL post-mortems. Build a replay harness that runs fixtures against a containerized Hermes and asserts routing intent matches recorded baseline. The `dispatcher_routed` log entries make this self-curating.
- [ ] **Layer A — full E2E with real Kimi** (high cost, run rarely). 36-case smoke suite, ~$0.10–0.50/run, 3–6 min. Run pre-deploy and on any SKILL.md change. Build after Layer C is stable.
- [ ] **Auxiliary vision pipeline test** — synthetic image upload through the bridge stub, assert pending file gets created within N seconds. Doesn't fit cleanly into A/B/C since failure mode is auth/wiring not LLM judgment. Standalone reliability test.

### Catering test-case doc revision (reviewer's Option 1)

- [ ] **Drop "agent invents prices" failure modes** (C23, C25 in reviewer's case list). Impossible by construction — Kimi never sees prices.
- [ ] **Refocus C06–C13 dietary cases** from "did the LLM filter correctly" to "did the LLM extract dietary tags into the lead correctly" (Python deterministically filters the menu downstream).
- [ ] **Resolve C02 design** — does Catering recognize returning customers via:
  - (a) Catering SKILL Python preamble does phone-lookup against `catering-leads.json`, injects "returning customer, last booking N days ago" into Kimi context (recommended; matches menu-source pattern), OR
  - (b) Treat as unknown until self-identification (simpler, loses warm-recognition UX)?
- [ ] **Add 3–4 prompt-injection variants** to C32 (reaches 5 total).
- [ ] **Add 2 dispatcher-routing-layer cases** now that we know it's the highest-leverage testable surface.
- [ ] **Reduce 2 cases that became low-stakes** under per-VPS isolation (cross-tenant threat scope was wrong).

### Schema implications from review

- 🟡 **C23 renderer + extractor-prompt** — schema slot landed (PR #21, 2026-04-28). Field is `off_menu_items: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=20)` on `CateringLeadExtractedFields`. **Currently WRITE-ONLY**: extractor SKILL prompt + owner-approval-card renderer MUST ship together to avoid silent-drop. **Blocked on**: identify the actual owner-card-sending script in the lead-intake path (design-review surfaced that `apply-catering-owner-decision` is NOT the owner-card builder). **Estimated effort**: ~half-day with proper investigation. **Pipeline**: needs its own plan/design/review cycle since the renderer-target gap is real architectural work, not just a code change.
- [ ] **Build `lookup_prior_leads_by_phone` script** — C02-Option-C foundation per `docs/catering-edge-cases.md` (v3.1) C02 case. Half-day PR with proper tests: E164Phone canonicalization, date arithmetic for `last_seen_days_ago`, defensive handling for malformed entries in `catering-leads.json`. Return shape pinned by C02 case as authoritative interface contract. Unblocks v3.1 C02 from "design-spec-pending" to runnable.

## P2 — Routing reliability hardening (incremental)

- [ ] **Log `dispatcher_routed` for declined unknowns too** (Item 2 of original P1+P2 bundle, deferred during 2026-04-28 design-review pipeline). Currently the SKILL writes only `unknown_sender_declined` on the decline path. Uniform logging would simplify the report (no fallback by-phone matching). Small SKILL.md edit + reporter tweak — but **needs its own plan/design/review cycle** because the design review surfaced that `DispatcherRouted.message_id` is required (`Field(min_length=1)`) and `UnknownSenderDeclined` doesn't currently carry message_id; the source path in the SKILL instruction needs explicit specification + a no-op fallback warning addition in the reporter.
- [ ] **Schedule weekly cron for `dispatcher-accuracy-report`** (Item 3 of original P1+P2 bundle, deferred during 2026-04-28 design-review pipeline). Pushover summary on Sunday morning. **Substantial silent-failure-hunter findings during design** that need addressing before build: (a) OnFailure handler service for cron-itself-broken case + ConditionPath* removal so OnFailure actually fires, (b) "cron never ran" watchdog (3-week silent skip undetected today), (c) exit-code surface 0/1/2/3 over-engineered for an 80-line script, (d) `capture_output=True` swallows reporter stderr WARN, (e) empty-window `0/0 (0%)` panics owner, (f) `Persistent=true` multi-fires after weekend outage, (g) `--priority -1` is silent on Pushover. Needs its own cycle.
- [ ] **Capture interesting routing pairs to fixtures file** as they arrive — start a `tests/fixtures/dispatcher_traffic.jsonl` with manually-curated entries from `decisions.log`. Seeds Layer C.
- [ ] **Strengthen image+menu fallback** — currently Fix 3 in PR #14 catches misrouted image+menu in `handle_owner_command`. Audit other handlers for similar misroute paths once data shows where Kimi actually misroutes.

## P3 — Platform / infrastructure cleanup

See `docs/hermes-alignment.md` Part 2 for the silent-failure-ranked operational drift checklist. Items below cross-reference that doc; resolve there as the canonical tracker.

### Critical tier (silent-failure surface — from alignment doc)

- ✅ **2026-04-28** — Reconcile `shift-agent-deploy.sh` with actual VPS pattern (PR #16). Tarball-based deploy with snapshot-before-install, smoke gate, auto-rollback. End-to-end validated on VPS: deploy + rollback + rollforward + list. `tools/build-deploy-tarball.sh` runs pytest gate locally, captures `git rev-parse HEAD` into `.commit-hash`, ships ~116K tarball.
- ✅ **2026-04-28** — Pin Hermes commit hash in deploy.sh (PR #17). 3-field baseline pin (`HERMES_COMMIT`, `HERMES_VERSION`, `BRIDGE_POST_PATCH_SHA256`) verified by `tools/check-shift-agent-patch.sh` as first deploy gate. Override path with `HERMES_PIN_OVERRIDE=<full-hash>` + `HERMES_PIN_OVERRIDE_REASON` both required, dual-channel audit (pin-overrides.log + log-decision-direct), all 4 validation paths exercised live on VPS: fail-closed on drift, override-accepts-current, override-rejects-wrong-hash, override-rejects-missing-reason.
- ✅ **2026-04-28** — bridge.js patch inventory (subsumed by PR #17). Same gate covers `shift-agent-template-bypass` markers (added in PR #14, previously uncovered) + sha256 fingerprint of as-deployed bridge.js (catches in-version code drift + manual edits + partial patch reapplication).

### Hermes pin follow-ups (low priority)

- [ ] **Tighten WARN→FAIL on missing check script in `shift-agent-deploy.sh`** — per PR #17 reviewer's Low-4. After one full deploy cycle confirms tarballs ship `tools/`, change `else WARN` to `else FAIL` so future refactors can't silently bypass the gate. ~5 min.
- [ ] **Bats tests for override semantics** — per PR #17 reviewer's Low-5. Project has no bats infrastructure today; multi-day investment. Real gap: bash gate logic only validated by manual VPS run.
- [ ] **Clean up `hermes_agent.__version__` warn** — informational warn fires every deploy because import returns `unknown` (likely venv path or import-order issue in `check-shift-agent-patch.sh:5`). Doesn't affect correctness (commit-hash pin is authoritative); just noisy. ~30 min to investigate.

### High tier (active gotcha)

- ✅ **2026-04-28** — Single canonical `.env` via symlink (PR #18 + PR #19 strict-gate fix). `/opt/shift-agent/.env` is now a symlink to `/root/.hermes/.env`. Pre-flight drift detector (`tools/check-env-drift.sh`) hashes overlapping keys without leaking secrets; idempotent migration (`tools/migrate-env-to-symlink.sh`) auto-detects shift-only keys + creates timestamped backup; strict symlink-integrity gate in `shift-agent-deploy.sh` fails-closed before install_artifacts. Gate validated end-to-end: break symlink → exit 1 → restore → deploy passes.
- ✅ **2026-04-28** — Audit log rotation (subsumed). Investigation revealed the SHA-256 chain was decoration (~3% coverage, no verifier). Logrotate already configured (daily, 30-day retention, archive to `/var/log/shift-agent-archive/`). Removed the chain (Option B per review thread) rather than spending half-day building infrastructure to back up an aspirational claim. Deployed integrity story is now honest: append-only via flock + `0640` perms + off-server backups + deploy-time gates. See `docs/hermes-alignment.md` Part 1 for the architecture sketch if compliance need emerges.

### Deferred until specific need emerges

- [ ] **Cryptographic audit-log chain** (deferred 2026-04-28; see PR #20 for context). Architecture if needed: move `_append_sha_chain` into `safe_io.ndjson_append` chokepoint so all writers covered, add `verify-decisions-log` script, add daily-cron verification, run one-time backfill (with explicit "trust boundary" docs noting pre-backfill entries aren't cryptographically defensible). Total ~half-day. **Chokepoint claim audited 2026-04-28** — every `decisions.log` writer in `src/agents/*/scripts/` and `src/platform/scripts/` calls `safe_io.ndjson_append`; no raw `open(..., "a")` bypass exists. Re-introduction at the chokepoint will cover all writers. Triggers: regulator audit requirement, formal customer dispute defense, multi-tenant compliance posture.
- [ ] **Alignment-doc audit pass — next due 2026-07-28** (90 days from baseline) — pattern observed where alignment doc and deployed code drift in either direction: doc claims a feature we lack (PRs #17 Hermes pin, #18 .env consolidation, #20 audit chain), OR doc understates a feature we have (v3.1 catering-edge-cases audit-chain framing, 2026-04-28). Cheap quarterly exercise; surfaces drift before it bites. Concrete cadence (vs "~quarterly?") so the entry can't rot in the backlog. Roll the next-due date forward 90 days each time it runs.

### Deferred until informed by agent #2-style use case

- [ ] **`docs/platform-contract.md` with semver** — Medium tier in alignment doc. Enumerate `src/platform/*.py` public surface + log-entry types + script exit codes; tag v0.1.
- [ ] **Phase A.5 — `schemas.py` runtime registry split** (`register_agent_entries()`). LogEntry union now ~30 variants and growing.
- [ ] **Phase B — `/opt/shift-agent/` → `/opt/smb-agents/` rename** (~292 references including `tools/patch-hermes.py:158`). Half-day, ideally bundled with a maintenance window.
- [ ] **Phase C — cockpit modular split** (frontend section registry + backend `state.py` `_AGENT_ROOT` parameterization). Wait until agent #2 ships its own cockpit needs.

## P4 — Hygiene + housekeeping

- [ ] **Clean up scratch-file pollution in repo root** — 400+ untracked `.AA_*.txt`, `.B_*.txt`, `.ph17_*.txt` etc. from prior debugging sessions. Either extend `.gitignore` with a smarter wildcard pattern (`.[A-Z]*.txt`, `.[a-z][_a-z0-9]*.txt`) or `git clean -fd` in a careful pass.
- [ ] **Review old pending task #8** — "Re-engage safety + commit validated fixes" — has been pending since the start of session history. Likely obsolete given subsequent safety/hardening commits (021e090, 7525c22, 8c14069). Confirm and close.

---

## Process notes — pipeline cadence calibration

Observation from PR #21 (2026-04-28): the full Plan→5×Review→Design→5×Review→Build→PR→5×Review pipeline took ~15 agent calls for a single 91-line schema PR. Three observations worth carrying forward:

1. **The discipline caught real bugs.** Design review surfaced the wrong-renderer-target issue that would have cost a half-day of build+revert. PR review surfaced the silent-drop-infrastructure concern that drove the CONTRACT comment on the new field. Without the rigorous review rounds, both would have shipped.

2. **Bundle splits naturally surfaced.** The original P1+P2 bundle was 3 items; rigorous review showed Items 2 + 3 had design-blocking issues that Item 1 didn't. The bundle decoupled cleanly into "ship Item 1 as a focused schema PR, defer 2 + 3 to their own cycles." Without the rigor, the bundle would have shipped half-baked.

3. **For sub-100-line changes the pipeline cost is high.** ~15 agent calls per ~90-line PR is an expensive ratio. A lighter pipeline (e.g., Plan → Build → PR → 3×Review) would catch most issues at lower compute cost. Recommended cadence-by-PR-size:
   - **<100 lines, schema/doc/single-script:** lighter pipeline (Plan → Build → PR → 3 reviews)
   - **100-500 lines, multi-file feature with operational gates:** medium pipeline (Plan → 3 reviews → Design → 3 reviews → Build → PR → 5 reviews)
   - **>500 lines or new architectural surface:** full pipeline as established
   This is a future-process decision; not worth retrofitting prior PRs, but worth applying to upcoming work.

---

## Recently completed (this week)

- 🟡 2026-04-28 — PR #22 (in flight): catering edge case scenario library v3.1 (`docs/catering-edge-cases.md`); replaces v3 inline doc; 5 grounded corrections vs deployed code; awaits explicit merge approval
- ✅ 2026-04-28 — PR #21: C23 schema field `off_menu_items` (full pipeline: plan → 5 reviews → design → 5 reviews → bundle-split decision → build → PR → 5 reviews → 8 review fixes → merge → deploy; 162 tests passing, deploy tagged 3b83c034)
- ✅ 2026-04-28 — PR #20: SHA-256 chain decoration removed; deployed integrity story now matches reality (append-only flock + 0640 perms + logrotate + backups)
- ✅ 2026-04-28 — PR #19: symlink-integrity gate strictness fix (PR #18's gate had inverted polarity — silently passed when symlink replaced by regular file; new gate is unconditionally strict; Step-5 break-then-restore validation confirmed exit 1)
- ✅ 2026-04-28 — PR #18: `.env` symlink consolidation + Hermes pin WARN→FAIL tightening (drift detector, migration script, integrity gate, smoke-check doc)
- ✅ 2026-04-28 — PR #17: Hermes pin gate (3-field baseline, fail-closed + override + dual audit; all 4 validation paths exercised live)
- ✅ 2026-04-28 — PR #16: tarball-based deploy formalizing actual VPS pattern (`docs/deploy.md` + `tools/build-deploy-tarball.sh` + rewritten `shift-agent-deploy.sh`); end-to-end validated incl. rollback path
- ✅ 2026-04-28 — `docs/hermes-alignment.md` v1: deployed-patterns reference + silent-failure-ranked operational checklist + read-deployed-code working agreement
- ✅ 2026-04-28 — PR #15: `dispatcher-accuracy-report` Layer 0 monitor (149 tests passing)
- ✅ 2026-04-28 — PR #14: dispatcher routing reliability hardening (3 fixes: routing matrix, `DispatcherRouted` schema, image+menu fallback)
- ✅ 2026-04-28 — `.gitattributes` enforces LF line endings for VPS scripts (root-cause fix for CRLF shebang break)
- ✅ 2026-04-28 — Catering menu v0.2 photo-upload pipeline shipped + deployed
- ✅ 2026-04-28 — Tier 2 sweep: agents 6, 7, 9, 10, 12, 13, 14, 15, 16 scaffolded (opt-in disabled)
- ✅ 2026-04-28 — Tier 1 complete: agents 1–5 shipped (2 LIVE full impl, 1 was-already-LIVE, 2 ship-disabled-opt-in)
- ✅ 2026-04-28 — Platform extract: `src/platform/` + `src/agents/<name>/` repo layout (PR #11)
- ✅ 2026-04-27 — Sender-id context (Phase A→D, LID injection + lid-learn cron)
- ✅ 2026-04-27 — Owner cockpit Phase 2 + Phase 3 deployed at http://46.62.206.192:9001/ui
