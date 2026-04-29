# Backlog — pending items

Living checklist. Items grouped by priority; each completed item gets `✅` and a date.
For history of *completed* multi-phase initiatives (platform extract, sender-id, agent #2/4/5, etc.), see git log + `tasks/all-phases-*.md`.

Last updated: 2026-04-29 (Catering omnibus PR-A: hardening + lookup SKILL preamble; v0.4 deferred to PR-B with reviewer-flagged corrections)

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

- ✅ **2026-04-28** — **C23 renderer + extractor-prompt** — shipped via commit `46780e8`. Renderer in `create-catering-lead._render_approval_card` lines 152-203; extractor prompt in `parse_catering_inquiry/SKILL.md` line 24. Both halves shipped in the same commit; no silent-drop window. Backlog tracking only (re-confirmed by 2026-04-29 plan-review pass).
- ✅ **2026-04-28** — **Past-date validation in `create-catering-lead`** — shipped via commit `8f4e6ea`. `_validate_event_date` covers past-date / invalid-calendar / timezone-invalid with `CateringLeadRejected` audit + `REASON_TO_ERR_PREFIX` dispatch. v3.1 C10 transitions from design-spec-pending to RUNNABLE.
- ✅ **2026-04-28** — **Build `lookup-prior-leads-by-phone` script** — C02-Option-C foundation per `docs/catering-edge-cases.md` (v3.1) C02 case. Shipped via PR #26 (squash-merged). 22 tests; full Plan→5→Design→5→Build→PR→5 pipeline applied.
- ✅ **2026-04-29** — **SKILL preamble integration for `lookup-prior-leads-by-phone`** — shipped via PR-A (catering omnibus). `parse_catering_inquiry/SKILL.md` Step 0 invokes the script subprocess-style and feeds the dict back into Kimi's prompt context as soft priors for extraction. v3.1 C02 transitions from "shipped-but-unwired" to RUNNABLE end-to-end. 8 SKILL static tests pin the contract (script invocation, all `LOOKUP_STATUS_*` constants branched, default-row for unparseable output, sender_phone provenance documented, no privacy-leak phrasing).
- ✅ **2026-04-29** — **Hardening: `oserror:` status handling in `safe_load_json` consumers** — shipped via PR-A. New `safe_io.assert_load_status_clean` helper centralizes the contract; `apply-catering-owner-decision` (initial load + post-bridge re-load with stricter status!=ok check) and `create-catering-lead` use it. Closes silent-failure-hunter NEW-1.
- ✅ **2026-04-29** — **Lock target migration: writer-side flock pattern** — shipped via PR-A. New `safe_io.try_acquire_filelock_with_retry` (raise-on-exhaustion `LockUnavailable` contract); `lookup-prior-leads-by-phone` now targets the SAME `.lock` sibling that writers use. Cross-script convention test asserts all 3 scripts agree on `LEADS_LOCK`. Closes silent-failure-hunter NEW-5.

## P1.4 — PR-A follow-ups (deferred during 2026-04-29 design-review + PR-review pipelines)

These items were dropped from PR-A (catering omnibus) when 5-agent reviews surfaced enough specific concerns that they warranted their own focused cycles. Each has a known design path; none is blocking.

### From design review (round 2)

- [ ] **Catering edge-case doc revision (v3.2)** — drop unreachable cases (Hermes never sees prices), refocus C06–C13 dietary cases (extraction-target not filter-target), formally mark C02 RUNNABLE end-to-end, add 4 prompt-injection variants to C32, add 2 dispatcher-routing cases. **Reviewer-R5 finding:** the prior plan referenced case IDs that don't exist in `docs/catering-edge-cases.md` v3.1 (caps at C22; no C23/C25/C32/C40/C41/cross-tenant cases). Before drafting: read the actual deployed doc, identify accurate insertion points, decide whether to introduce in-place tombstone convention OR keep using existing "Deferred cases" table at line ~524. C32 prompt-injection variants split between dispatcher-layer (v=1 spoof, code-fence subprocess) and parse-skill-layer (markdown link, Unicode normalization) — target the right SKILL.
- [ ] **`lookup_invoked` LogEntry variant for observability** — PR-A's SKILL preamble runs `lookup-prior-leads-by-phone` on every catering inquiry but produces NO `decisions.log` entry. Soak monitoring for `lookup_status=lock_timeout` rate is currently a manual journald grep, disjoint from `dispatcher_routed` correlation. Add a new `_BaseEntry` subclass with `type: Literal["lookup_invoked"]`, `lookup_status`, `prior_lead_count`, `last_seen_days_ago`. Either the SKILL emits via `log-decision-direct` after parsing the JSON, OR the script writes the entry itself. Pair with a follow-up `lookup-status-distribution-report` cron mirroring `send-routing-accuracy-summary`. **Reviewer-R3 finding from PR-A design review.** ~half-day.
- [ ] **Test fixture conftest hoist** — `tests/_b1_helpers.py` docstring says fixtures should hoist to `tests/conftest.py` per design-review HIGH-C1. PR-A skipped this to avoid scope creep; new tests in PR-A used `_b1_helpers` directly. The hoist remains valuable for future tests (no fourth copy of `BridgeStub`). Watch out for: Windows-portability of conftest-collection-time imports (lazy-import inside fixture body), and the broken `mod.__name__ = "__main__"` pattern in `tests/test_catering_v02_scripts.py` which `_b1_helpers.py` claims was always no-op'ing. **Reviewer-R1+R3 findings from PR-A design review.** ~half-day.
- [ ] **`tests/_b1_helpers.py` "v02 tests no-op'd" investigation** — `_b1_helpers.py` line 13-26 claims the v02 helpers' importlib pattern (`spec_from_file_location` without `SourceFileLoader` for hyphen-named files, plus pre-set `mod.__name__ = "__main__"`) "never actually executed". Pre-conftest-hoist, run a smoke probe injecting `assert False` into one v02 test body to confirm whether tests run today. If they DO run, the docstring claim was overstated; if they DON'T, the conftest hoist commit will surface real bugs. ~30 minutes, removes ambiguity. **Reviewer-R1 finding.**

### From PR review (round 3, 2026-04-29 PR #33)

- [ ] **Two-phase smoke gate (deploy-order pre-restart import check)** — `shift-agent-deploy.sh` runs smoke-test AFTER `systemctl restart hermes-gateway`. A missing safe_io symbol means traffic hits the new code in the ~5s+smoke-window before rollback fires. Split: run a fast `python3 -c "from safe_io import ..."` import-only gate BEFORE the restart, then full smoke (Pushover + systemd checks) after restart. **Reviewer-R5 medium finding.** ~1 hour.
- [ ] **Rollback path re-runs smoke** — `shift-agent-deploy.sh` rollback case extracts prior tarball + install + restart, but doesn't re-run smoke. If the prior tarball is itself broken (e.g. someone manually edited `/opt/shift-agent/safe_io.py` between deploys), rollback completes silently. After rollback install: run smoke; if it fails, Pushover priority 2 ("Rollback FAILED smoke — agent in uncertain state, SSH"). **Reviewer-R5 low finding.** ~30 min.
- [ ] **Pre-existing audit-log-wrong-lead bug at apply-script post-bridge re-load** — the `for i, l in enumerate(store.leads): if l.lead_id == lead_id_for_output: ... break` loop has no `else` branch. If the lead is somehow absent from the re-loaded store (status=ok but lead removed externally), the audit-log entry references `store.leads[i]` which is the WRONG lead's customer_phone, and atomic_write writes the store with no SENT_TO_CUSTOMER transition. **NOT introduced by PR-A** but adjacent to the post-bridge gap closure. **Reviewer-R1 info finding.** ~15 min: detect lead-not-found and fail with EXIT_SCHEMA_VIOLATION + BUG stderr similar to the new missing/empty path.
- [ ] **`from contextlib import contextmanager` mid-file in safe_io.py** — Python style is to top-load imports. Move from line 109 to the top imports block. **Reviewer-R2 low finding.** ~1 LOC change.
- [ ] **`tasks/todo.md` P1.6 numbering** — P1.6 sits AFTER P2 (Routing reliability) in file order but the numeric tag implies priority order. Either renumber P1.6 to P3.5/P5 OR move it above P2 to match. **Reviewer-R2 low finding.** ~5 min.
- [ ] **Test gaps from R3 PR-review** — (a) `assert_load_status_clean` empty-string + leading-whitespace status; (b) `try_acquire_filelock_with_retry` negative attempts/sleep clamps; (c) integration test for corrupt: status path through writer scripts (currently only unit-tested in test_safe_io_load_status); (d) post-bridge re-load BUG path via BridgeStub side-effect (delete or chmod leads.json after writing the response); (e) lock-parent-dir auto-creation; (f) ast-based LOOKUP_STATUS_* enumeration (replaces fragile regex). **Reviewer-R3 medium findings.** ~half-day to add all six.
- [ ] **PR body / soak-monitoring instructions inconsistency** — PR #33 description says watch `decisions.log` for `oserror`/`lock_timeout` paths, but PR-A's new error paths only emit to stderr→journald. After deploy: soak instructions for operators should be `journalctl -u hermes-gateway -f | grep -E 'unhealthy load|LockUnavailable|BUG: leads.json|lookup_status='` for the new branches AND `tail -f /opt/shift-agent/logs/decisions.log` for existing audit signals. **Reviewer-R5 medium finding.** Reflect in any future ops runbook.
- [ ] **`safe_io_pure` cross-platform split** — 4 of 5 new test files skip on Windows because safe_io.py imports fcntl unconditionally. Splitting `assert_load_status_clean` + `LoadStatusError` into a fcntl-free `safe_io_pure` module would let those tests run cross-platform. **Reviewer-R3 low finding.** Out of scope for PR-A; track for future safe_io refactor.

## P1.5 — Catering Lead v0.4 — LLM-drafted customer quote (DEFERRED to PR-B with reviewer-flagged corrections)

**Status (2026-04-29):** Originally bundled into a single "catering omnibus" plan; 5-agent plan-review consensus was to split into PR-A (hardening + lookup wiring + hygiene) and PR-B (v0.4 LLM-drafted quote). PR-A merged 2026-04-29; PR-B remains its own full-pipeline cycle.

**Reviewer-flagged corrections that PR-B MUST address (do not re-implement the prior plan as-is):**

- **`extra="ignore"` rollback narrative was invalid** — flipping `CustomerConfig` and `CateringLead` from `extra="forbid"` to `extra="ignore"` does NOT actually provide v0.4→v0.3 rollback safety. Rollback runs the v0.3 BINARY which still has `extra="forbid"` baked in. The deployed convention for forward-compat is `mode="before"` validators with sentinels (see `_backfill_legacy_quote_text` at `schemas.py:535`). PR-B should use that pattern OR re-tag as `drifts-from-Hermes` with explicit operational rationale.
- **`catering-lead-context` helper that doesn't exist** — prior plan/design's `handle_catering_owner_approval/SKILL.md` Step 2.5 referenced `/usr/local/bin/catering-lead-context` with a "fallback to direct jq queries if the helper doesn't exist yet" branch. Either build the helper as a sub-task of PR-B (small read-only context bundler) OR drop the reference and inline the exact `jq` queries in the SKILL.md. Don't ship a SKILL that conditionally calls a non-existent binary.
- **`CateringQuoteSkillFailed` audit class needs `original_message_id`** — v0.3 idempotency anchors (`CateringQuoteAttempted`, `CateringDeclineAttempted`) all carry `original_message_id` for replay correlation. The new failure variant must too.
- **SKILL→`log-decision-direct` audit-write path is too vague** — prior plan said "the SKILL logs `catering_quote_skill_failed` via `log-decision-direct`". Either inline the exact CLI invocation in the SKILL prompt, or move the audit-write into `apply-catering-owner-decision` via a `--skill-failure-reason` flag. Option (b) matches the deployed script-as-chokepoint convention better.
- **Truth-preservation guard substring check is exploit-trivial** — `str(headcount)` in `qt` passes for `headcount=50` if the quote contains `"150 people"` or `"the 50% off promotion"`. Use word-boundary regex like `re.search(rf"\b{re.escape(str(hc))}\b", qt)` and similar for `event_date`.
- **`headcount=None AND event_date=None` defense gap** — guard skips both checks when both fields are None. PR-B should either (a) require non-empty `--quote-text` minimum length and emit a WARN when neither truth field exists, or (b) explicitly test the "guard skips" behavior so it's pinned not accidental.
- **`CateringQuoteAttempted` v0.3 idempotency anchor was never actually written** by deployed code despite docstring claim. v0.4 inherits this gap; PR-B should write the anchor BEFORE the bridge POST under the same lock, and on retry-entry check its presence to short-circuit duplicate sends.
- **WhatsApp markdown injection** — drafted text goes straight to `_bridge_post(jid, message)`. PR-B should normalize: strip zero-width chars (`​-‏`, `‪-‮`, `﻿`), enforce single-line CRLF→LF, cap length at 600 chars. Add 1 test for malicious-zero-width-LRO inquiry → drafted text → apply-script strips them.
- **YAGNI: `voice_quality` field** — `bad-tone` parser is deferred to v0.5; `voice_quality` is dead code in v0.4. PR-B should drop it AND drop the bad-voice filter from `recent_sent_quotes()`. Reintroduce both together in v0.5.
- **Active-traffic deploy runbook missing** — paradigm change (template→LLM-orchestrated draft) needs explicit runbook for in-flight `AWAITING_OWNER_APPROVAL` leads during the deploy window.
- **`menu_filter.py` extraction location** — prior plan invented `src/agents/catering/menu_filter.py` with no peers. PR-B should pick a justified home: inline into `lookup-prior-leads-by-phone` (only runtime caller) OR `src/platform/` (since it depends on platform schemas). Don't create a new per-agent helper-module convention for one ~30-line function.
- **Branch divergence rationale** — `fix/catering-comprehensive` doesn't *delete* expense-bookkeeper code; it predates PR #30. Cherry-pick onto a fresh branch off main is operationally simpler than rebasing 12 v0.3-hardening commits over the merged expense work. State the reason accurately.

**Hermes capability checklist (per CLAUDE.md):**

| Step | Hermes? | Net-new? |
|---|---|---|
| Owner WhatsApp inbound | [Hermes] | — |
| Skill dispatch on approval-code reply | [Hermes] dispatcher | — |
| Parse code + verb (approve / reject / edit) | [Hermes] LLM in SKILL | — |
| Read lead context from `catering-leads.json` | [Hermes-adjacent] tiny preamble or existing helper | minor |
| Draft customer quote in owner's voice | [Hermes] LLM-orchestrated SKILL | prompt only |
| Persist quote_text on lead + transition status | [existing] `apply-catering-owner-decision` | tiny `--quote-text` flag |
| Bridge POST → customer | [existing] apply-script's `_bridge_post` | — |
| Audit (`CateringQuoteSent`, `CateringLeadStatusChange`) | [existing] | — |

Genuinely net-new: tone-sample plumbing + `--quote-text` flag + small schema additions. ≤ ~150 LOC + ~15 tests. v2 design was sized at ~615 LOC + 78 tests; ~75% of that was over-engineering.

**Read deployed code first** (per drift-rule §Part 3):
- `src/agents/catering/skills/handle_catering_owner_approval/SKILL.md` — current v0.2 verb classifier; gets one new step ("draft a quote in owner's voice")
- `src/agents/catering/scripts/apply-catering-owner-decision` — current approve flow renders quote via template; gets one new flag (`--quote-text`)
- `src/agents/catering/templates/catering_quote_to_customer.txt` — gets removed
- `src/platform/schemas.py` — `CateringLead`, `CustomerConfig`, `CateringLeadStore`
- `tools/catering-state-migrate.py` — only modified if voice-sample backfill is genuinely needed; the `recent_sent_quotes()` method reads leads.json directly so backfill may be unnecessary

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

## P1.6 — Expense Bookkeeper v0.2 follow-ups

**Context:** v0.1 shipped 2026-04-29 via PR #30 (schema + mock QBO + 3 SKILLs + 10 templates). PR #32 closed 4 audit-found bugs (1 HIGH dispatcher routing, 1 MED whitespace validator, 2 LOW). Feature is opt-in (`cfg.expense_bookkeeper.enabled = false` by default); no real QBO write path until v0.2 ships `RealQBOClient`.

**Drift-check tag:** `extends-Hermes` — Hermes substrate handles vision-extract / structured output / approval-code dispatch / audit chain. Genuine net-new: QBO write API, money-moving UX (code+amount approval, perceptual-hash dedup, per-amount thresholds, reversibility window).

**Authoritative deferral list:** `tasks/expense-bookkeeper-v02-followups.md` — full rationale + suggested action for each item below.

### From audit-fix Stage 2 reviewer thread (defence-in-depth + DRY)

- [ ] **V02-1 — Extend whitespace/null-byte validator** to `sender_lid`, `qbo_account`, `rejection_reason` (when present). Currently only `sender_phone` + `original_message_id` are guarded by the shared `_validate_required_no_whitespace_no_nullbyte`. Defence-in-depth — primary NDJSON safety is already covered by Pydantic's `model_dump_json` JSON-escaping, but rejecting at the schema boundary closes the gap. Parametrize the test; ~30 min.
- [ ] **V02-2 — Refactor `sender_phone` to `Optional[E164Phone]` + at-least-one-of validator** (mirrors `RawInbound` `schemas.py:1186-1208`). Drops the BUG-2 `Field(min_length=1)` constraint as redundant. ~200 LOC scope: extract-receipt persistence path, every `ExpenseLead` test fixture (currently plain strings), `apply-expense-decision` comparison logic. Pipeline: medium cadence.
- [ ] **V02-3 — DRY `_check_orphans` helper** — lift the ~70-line duplicate from `extract-receipt` + `apply-expense-decision` to `src/platform/expense_orphan.py`. Companion: `_scan_audit_for_push_completion`. Both scripts import. Expands install_artifacts surface; deferred from v0.1 fix-up explicitly. ~half-day with tests.
- [ ] **V02-4 — Token-redactor: bare OAuth `state=` / PKCE `code_verifier=` patterns** outside URL context. v0.1 risk surface is zero (MockQBOClient never produces real OAuth payloads). Add to `_TOKEN_PATTERNS` when `RealQBOClient` lands:
  ```python
  re.compile(r'\bstate=[A-Za-z0-9_\-\.]{8,}', re.IGNORECASE),
  re.compile(r'\bcode_verifier=[A-Za-z0-9_\-\.]{16,}', re.IGNORECASE),
  ```
- [ ] **V02-5 — `image_path` `os.path.realpath` symlink resolve** — only relevant if multi-tenant sharing of receipts dir ever happens. Currently impossible per per-customer-VPS isolation. Track but do not ship until that scenario emerges.

### From plan v2 §9 deferral list

- [ ] **V02-6 — `expense_lookup` SKILL** — analog of catering's `lookup-prior-leads-by-phone` (PR #26). Owner can query past expenses ("show me what I expensed at Costco last month"). Mirror the catering script + SKILL pattern. ~half-day.

### Cross-cutting (not strictly v0.2 but surfaced in audit-fix review)

- [ ] **V02-7 — Pre-existing dispatcher regex inconsistency** — `dispatch_shift_agent/SKILL.md:79` uses `#[A-HJ-NP-Z2-9]{5}` while canonical alphabet in `schemas.py:843` is `#[A-HJKMNPQR-Z2-9]{5}`. Both are functionally restrictive enough; the dispatcher's regex is stricter near the seam (excludes `K`/`M`). Unify to canonical regex everywhere — one PR, ~5 file edits, mostly tests. ~1 hour.
- [ ] **V02-8 — jq syntax-validity assertion in audit test** — `test_audit_bug1_dispatcher_skill_includes_expense_jq_lookup` is string-presence + ordering only. A subtle filter typo (missing paren) would pass the test but fail at runtime. Add a Linux-only test (`pytestmark.skipif(platform.system() == "Windows")`) that pipes each jq filter through `subprocess.run(["jq", "-en", filter])` and asserts exit 0.

### From original v0.1 PR review (overnight-report carry-forward)

- [ ] **Plan §4g edge cases not yet covered:** #2 typo'd code (silent), #7 sum-mismatch resolution, #9 vendor name normalization, #11 approval-code collision regenerate, #16 multi-receipt batch. Each is its own 1-2 day ticket once the v0.2 scope is concrete.
- [ ] **Apply-side `original_message_id` idempotency runtime test** (currently schema-only). Subprocess invoke `apply-expense-decision` twice with the same `original_message_id`; assert second invocation no-ops + emits the right audit class.
- [ ] **Cockpit web UI for above-threshold review** — v0.1 ships paper spec only. Owner currently has no GUI surface; reviews happen via WhatsApp approval codes. Cockpit-web extension is a separate platform-level project; sequence after V02-6 (lookup SKILL) so the cockpit has data to render.
- [ ] **Real `RealQBOClient` impl** — currently raises `NotImplementedError` in `src/platform/qbo_client.py`. Genuinely net-new (Hermes does not own external write APIs). Bundle with V02-4 token-redactor patterns. Pipeline: full cadence (>500 LOC, new architectural surface — OAuth + write scope + reversibility window).

**Total v0.2 scope estimate (excluding `RealQBOClient` which is its own arc):** V02-1 + V02-2 + V02-3 + V02-6 + V02-7 + V02-8 ≈ ~1.5 weeks elapsed. Pipeline cadence per matrix: medium for V02-2/V02-3/V02-6, light for V02-1/V02-7/V02-8.

## P4 — Hygiene + housekeeping

- [ ] **Clean up scratch-file pollution in repo root** — 400+ untracked `.AA_*.txt`, `.B_*.txt`, `.ph17_*.txt` etc. from prior debugging sessions. Either extend `.gitignore` with a smarter wildcard pattern (`.[A-Z]*.txt`, `.[a-z][_a-z0-9]*.txt`) or `git clean -fd` in a careful pass.
- [ ] **Review old pending task #8** — "Re-engage safety + commit validated fixes" — has been pending since the start of session history. Likely obsolete given subsequent safety/hardening commits (021e090, 7525c22, 8c14069). Confirm and close.
- [ ] **VPS `/opt/shift-agent/config.yaml` provisioning gap** — surfaces every deploy as a smoke-gate failure → auto-rollback. Current VPS state: `config.yaml` was renamed to `config.yaml.corrupt-1777465716` at some prior point; smoke test (`config.yaml does not validate against Config schema`) trips because the file is missing. Auto-rollback works correctly (verified PR #30 + PR #32 deploys), but no new code lands until `config.yaml` is restored from `config.yaml.template` + populated with the live owner phone/customer config + chmod-protected. Hermes-gateway + cockpit remain active on prior code throughout — not a service outage, just a code-freeze. ~30 min on VPS to fix; pure ops work, no PR needed. Flag: also surfaced via `WARN: Hermes version drift expected=0.11.0 current=unknown` (informational; commit-hash pin is authoritative).

---

## Process notes — pipeline cadence calibration

Three observations from review-pipeline experience worth carrying forward:

1. **The discipline catches real bugs at the design phase.** In one observed cycle, design review surfaced a wrong-target issue that would have cost a half-day of build+revert; PR review separately surfaced a silent-drop concern that drove a CONTRACT comment on a new field. Without the rigorous review rounds, both would have shipped silently.

2. **Bundle splits naturally surface under rigor.** A 3-item bundled cycle decoupled cleanly into "ship Item 1 focused, defer Items 2 + 3 to own cycles" once design review found design-blocking issues unique to Items 2 + 3. Without the rigor, the bundle would have shipped half-baked.

3. **Pipeline cost-per-line is high for small changes.** A representative observation point: ~15 agent calls per ~90-line schema PR. The recommendation below balances discipline against compute cost by sizing the pipeline to the PR.

**Recommended cadence-by-PR-size:**
   - **<100 lines, schema/doc/single-script:** lighter pipeline (Plan → Build → PR → 3 reviews)
   - **100-500 lines, multi-file feature with operational gates:** medium pipeline (Plan → 3 reviews → Design → 3 reviews → Build → PR → 5 reviews)
   - **>500 lines or new architectural surface:** full pipeline as established (Plan → 5 reviews → Design → 5 reviews → Build → PR → 5 reviews)

This is a future-process decision; not worth retrofitting prior PRs, but worth applying to upcoming work. Re-evaluate the matrix periodically (e.g., as part of the alignment-doc audit pass) — if observed-vs-recommended cadence diverges meaningfully, the matrix needs recalibration.

---

## Recently completed (this week)

- ✅ 2026-04-29 — **PR-A: catering omnibus** — `feat/catering-omnibus-pra`. 7 commits: (1) `safe_io.assert_load_status_clean` helper for writer load chokepoints; (2) oserror surfacing in `apply-catering-owner-decision` + `create-catering-lead` (silent-failure-hunter NEW-1) + post-bridge state-loss strict-status check; (3) `safe_io.try_acquire_filelock_with_retry` (raise-on-exhaustion `LockUnavailable` — no bool footgun); (4) lookup-script lock-target migration to unified `.lock` sibling (NEW-5) + cross-script convention assertion test; (5) `parse_catering_inquiry/SKILL.md` Step 0 invokes lookup script with R4 design-review fixes (Hard rule + MUST framing, default-row for unparseable output, no privacy-leak phrasing, sender_phone provenance documented) — v3.1 C02 RUNNABLE end-to-end + 8 SKILL static tests; (6) smoke-gate import roundtrip for new safe_io chokepoint symbols; (7) backlog hygiene + PR-B + follow-up entries. Full Plan→5-agent-review→Design→5-agent-review→Build pipeline. v0.4 LLM-drafted quote split to PR-B follow-up cycle with 12 reviewer-flagged corrections.
- ✅ 2026-04-29 — PR #32: expense-bookkeeper audit-fix (4 bugs — 1 HIGH dispatcher routing, 1 MED whitespace validator on `sender_phone`+`original_message_id`, 2 LOW). Full ceremony: audit → plan v1.1 → 5-agent plan review → design folded → 5-agent design review → build → PR → 5-agent PR review → merge → deploy gate (auto-rolled-back on pre-existing config.yaml provisioning gap, feature stays opt-in so no behavior delta). 168/168 tests on PR head; 317/317 on merged main. 8 v0.2 follow-ups documented in `tasks/expense-bookkeeper-v02-followups.md` (now backlog P1.6).
- ✅ 2026-04-29 — PR #31: CLAUDE.md DRIFT RULES section (read deployed code BEFORE proposing). Companion to Hermes-first rule. Authority: `docs/hermes-alignment.md` Parts 1+3. Drift-check tags introduced (Hermes-native | extends-Hermes | drifts-from-Hermes) — every new plan/spec/design doc carries one. Memory file mirrored at `~/.claude/projects/.../memory/feedback_drift_rules.md`.
- ✅ 2026-04-29 — PR #30: Agent #21 Expense Bookkeeper v0.1 — schema + mock QBO + Solid 17 docs. Schema additions: `ExpenseBookkeeperConfig`, `ExpenseLead`/`ExpenseLeadStore`, 15 audit-entry classes, `EXPENSE_TRANSITIONS` table. Mock `QBOClient` Protocol + `MockQBOClient` + `RealQBOClient` stub (raises `NotImplementedError`). 3 SKILLs + 3 scripts + 10 templates + 2 systemd units. Feature ships **opt-in** (`enabled: false`). Full ceremony: plan → 5-review → design → 5-review → build → PR → 5-review → merge.
- ✅ 2026-04-29 — Solid 17 portfolio consolidation: 17 active + 5 backlog (was 20-agent commitment). Retired: #17, #18, #20. Live portal at http://46.62.206.192:8080/portal/ updated to terracotta+navy chess-board styling. Master spec at `docs/portfolio.md` (v2).
- ✅ 2026-04-28 — PR #22: catering edge case scenario library v3.1 (`docs/catering-edge-cases.md`); replaces v3 inline doc; 5 grounded corrections vs deployed code + 3-agent code-review round (must-fix `_normalize` accuracy bug + Bucket A count drift + claim-rot patterns); merged as 94177d2
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
