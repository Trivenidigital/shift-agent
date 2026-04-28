# Backlog — pending items

Living checklist. Items grouped by priority; each completed item gets `✅` and a date.
For history of *completed* multi-phase initiatives (platform extract, sender-id, agent #2/4/5, etc.), see git log + `tasks/all-phases-*.md`.

Last updated: 2026-04-28

---

## P0 — Live verification (passive, blocks "is it working?")

- [ ] **Verify dispatcher routing live** — next real inbound to your self-chat should produce a `dispatcher_routed` entry in `decisions.log` within ~10s of the matching `raw_inbound`. Run `sudo /usr/local/bin/dispatcher-accuracy-report --days 1` to check. Validates PR #14 + #15 end-to-end.
- [ ] **Verify menu photo upload pipeline** — yesterday's auxiliary-vision auth fix (OPENROUTER/OPENAI keys mirrored into `/opt/shift-agent/.env`) is unverified live. Send a menu photo to self-chat; expect `parse-menu-photo` to extract items → owner preview reply with confirmation code.
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

- [ ] **C23 mango-lassi case** — add `requested_off_menu_items: list[str]` field to `CateringLead` schema. Owner approval flow surfaces the off-menu request explicitly at quote time.

## P2 — Routing reliability hardening (incremental)

- [ ] **Log `dispatcher_routed` for declined unknowns too** — currently the SKILL writes only `unknown_sender_declined` for that path. Uniform logging would simplify the report (no fallback by-phone matching). Edit `dispatch_shift_agent` SKILL.md Step 4.
- [ ] **Schedule weekly cron** for `dispatcher-accuracy-report` (Pushover summary on Sunday morning). Separate PR.
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

- [ ] **Single canonical `.env` (or sync mechanism)** — two `.env` files drift; auth-key gotcha cost hours on 2026-04-28. 1–2h to fix.
- [ ] **Audit log rotation policy with SHA-256 chain handling** — `decisions.log` grows unbounded; naive logrotate would silently break chain continuity. Half-day for postrotate hook OR explicit ADR documenting per-rotation chain.

### Deferred until informed by agent #2-style use case

- [ ] **`docs/platform-contract.md` with semver** — Medium tier in alignment doc. Enumerate `src/platform/*.py` public surface + log-entry types + script exit codes; tag v0.1.
- [ ] **Phase A.5 — `schemas.py` runtime registry split** (`register_agent_entries()`). LogEntry union now ~30 variants and growing.
- [ ] **Phase B — `/opt/shift-agent/` → `/opt/smb-agents/` rename** (~292 references including `tools/patch-hermes.py:158`). Half-day, ideally bundled with a maintenance window.
- [ ] **Phase C — cockpit modular split** (frontend section registry + backend `state.py` `_AGENT_ROOT` parameterization). Wait until agent #2 ships its own cockpit needs.

## P4 — Hygiene + housekeeping

- [ ] **Clean up scratch-file pollution in repo root** — 400+ untracked `.AA_*.txt`, `.B_*.txt`, `.ph17_*.txt` etc. from prior debugging sessions. Either extend `.gitignore` with a smarter wildcard pattern (`.[A-Z]*.txt`, `.[a-z][_a-z0-9]*.txt`) or `git clean -fd` in a careful pass.
- [ ] **Review old pending task #8** — "Re-engage safety + commit validated fixes" — has been pending since the start of session history. Likely obsolete given subsequent safety/hardening commits (021e090, 7525c22, 8c14069). Confirm and close.

---

## Recently completed (this week)

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
