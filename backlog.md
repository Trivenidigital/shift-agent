# Shift Agent — Backlog

**Last updated:** 2026-04-27 (post-Phase-3 overnight batch)

## Priority Legend
- **P0** — Blocking: must complete before / on go-live
- **P1** — High: production readiness within week 1
- **P2** — Medium: valuable enhancement within month 1
- **P3** — Low: future phase, post customer-validation

---

## P0 — Blocking go-live (TODAY)

### BL-100: Step C — candidate YES → owner accepted, validated end-to-end
**Status:** OPEN — Step A + Step B proven Apr 26 rehearsal (P0006 sent at 21:04:18 UTC); Step C never closed
**Files:** none (operational)
**Why:** The candidate-response code path (`handle_candidate_response`) has never been exercised with a real inbound. Without proof, go-live ships an untested critical branch.
**Action:**
- On Phone 2 (`+19802005022`), reply `YES` to the coverage message Test Cover received
- Verify `decisions.log` records `proposal_status_change sent → accepted`
- Verify owner self-chat (`+17329837841`) receives "Test Cover accepted the shift" confirmation
- Verify `pending.json` P0006 status flips to `accepted`
**Acceptance:** all 3 decisions.log transitions present + owner sees confirmation + pending.json reflects accepted

### BL-101: Customer-side go-live prerequisites
**Status:** OPEN
**Why:** Per `GO-LIVE-HANDOFF.md` §1-7, these are non-developer items the customer must complete before any live employee message can flow.
**Checklist:**
- [ ] Pushover account created + Pushover app installed on owner's phone
- [ ] User key + App token populated in `/opt/shift-agent/config.yaml` `alerting.*`
- [ ] Customer signs the 3 disclosures (Baileys ToS / audit immutability / employee notification)
- [ ] Customer sends pre-go-live notice to all 45 employees (GDPR-required)
- [ ] Customer returns roster questionnaire (45 employees: id, name, role, phone, languages, can_cover_roles)
- [ ] Owner GPG public key imported on VPS for nightly backup encryption (`gpg --import`)
- [ ] `.env` `OPENROUTER_API_KEY` set to dedicated key (not shared with Hermes)
**Acceptance:** all 7 boxes checked + screenshots / signed PDFs filed

### BL-102: Re-engage safety + final smoke test before customer goes live
**Status:** OPEN
**Files:** `/opt/shift-agent/state/disabled.flag`, `/usr/local/bin/shift-agent-smoke-test.sh`
**Why:** The Apr 26 rehearsal left the agent in active state. Before customer messages real employees, run the canonical smoke test, then unset disabled.flag, raise `max_outbound_per_day` from rehearsal value (2) to production (6 for 45-employee roster).
**Action:** smoke test must exit 0 with green checks for: identify-sender, dispatcher routing, handle_sick_call → proposal, tail-logger capturing inbound, all systemd timers active.
**Acceptance:** `bash /usr/local/bin/shift-agent-smoke-test.sh` final line reads `=== All smoke checks passed ===`

### BL-103: Restore e005 (Vikram) status to active after rehearsal
**Status:** OPEN — was set to `terminated` to force e007 candidate selection in Step A test
**File:** `/opt/shift-agent/roster.json`
**Action:** `e005.status = active`

---

## P1 — Production-readiness within week 1

### BL-110: Pre-existing Phase-1 known issues (from GO-LIVE-HANDOFF.md §"Remaining known issues") — ✅ DONE
- [x] `jq` install on VPS — `web/deploy/deploy.sh` adds idempotent `apt-get install -y jq`
- [x] `backup.sh` GPG `--trust-model always` → full 40-char fingerprint pin via new `Config.backup.gpg_fingerprint` schema field; rejects 16-char short ID (evil32-immune)
- [x] `backup.sh` YAML parsed via grep|sed — already replaced with `yaml.safe_load` in prior commit
- [x] `_revert_everything` re-read under lock — already done in prior Priority-1 commit (verified during plan review)
- [x] `create-proposal` log-write inside flock — already done in prior Priority-1 commit

### BL-120: Cockpit Phase-2.1 follow-ups — ✅ DONE
- [x] `Settings` JWT-secret startup validator — `model_validator(mode='after')` rejecting non-hex / short secrets, with explicit error pointing to `secrets.token_hex(32)` generator
- [x] `/audit` reverse-tail reader — `app.log_tail.reverse_json_entries`, O(N×8KB) regardless of file size
- [x] `/decisions` reverse-tail reader — same iterator; on-disk index dropped per design review (premature optimization until file > 5 MB)
- [x] Audit log rotation under load — `web/deploy/test-rotation-under-load.sh` writes 100k entries + verifies chattr+a survives + append-only enforcement
- [x] Caddyfile: explicit `header_up X-Forwarded-For {remote_host}` (+ X-Real-IP, X-Forwarded-Proto, X-Forwarded-Host)
- [x] OTP audit-log latency vs timing equalization — audit write moved INSIDE the timed window before the wall-time floor

---

## P2 — Cockpit Phase-2.2 enhancements (within month 1)

### BL-130: Frontend↔backend type-safety — ✅ DONE
- [x] Backend: `ProposalView.status: str` → `Literal[ProposalStatus]` (11 variants); FastAPI emits `enum:` in OpenAPI for proper TS narrowing
- [x] `web/backend/scripts/dump-openapi.py` — CI-safe spec dumper (stubs JWT secret + uses tempdir paths via `COCKPIT_TEST_MODE=1`)
- [x] `web/frontend/src/generated/openapi.json` — committed artifact, regenerated by prebuild hook
- [x] `npm run generate:types` reads the committed artifact (deterministic CI)
- [x] `src/lib/api.ts` exposes both legacy `api.GET<T>(...)` and new `typedApi` (openapi-fetch) for incremental migration

### BL-131: useSection context-promote — ✅ SUPERSEDED
- Per design review, the proposed `<SectionProvider>` was unnecessary (single call site already → no listener leak). Replaced with one-line `as const satisfies readonly Section[]` micro-fix in `useSection.ts` for compile-time exhaustive coverage check.

### BL-132: Bridge.js patch for raw QR data string — DEFERRED (vendored upstream)
**Status:** DEFERRED with reframe per plan review.
**Original intent:** Patch `bridge.js` to emit `console.log({event:"qr_data", data: qr})` so cockpit can render `<svg>` QR.
**Why deferred:** `bridge.js` lives in `/root/.hermes/hermes-agent/scripts/` — owned by Hermes upstream. Next `hermes update` overwrites the patch silently. ASCII-block QR rendering already proven to scan in this project's rehearsal flow.
**Re-open trigger:** Hermes ships an official raw-QR callback, OR we vendor + patch the bridge in the project repo with a Hermes startup-config override.

### BL-133: TOTP fallback for cockpit auth — ✅ DONE
- [x] `app/totp.py` with `pyotp` + `qrcode[pil]`; verify-before-commit enrollment (refuses to commit secret until owner submits a valid first code from their authenticator)
- [x] Endpoints: `enroll-start` (require_fresh_otp), `enroll-verify` (require_auth), `disable` (require_fresh_otp), `verify-totp` (public, mints JWT)
- [x] `verify-totp` reads ONLY `cockpit-totp-secret.json`, never the pending file (closes design-review S1)
- [x] 5-strike → 15-min lockout; failure store separate from OTP store
- [x] Login screen: `GET /auth/status` (public) → tab UI shows Pushover-only / TOTP-only / both depending on configuration
- [x] Documented as Pushover-outage fallback, NOT defense against disk compromise

### BL-134: Broaden cockpit pytest coverage — ✅ DONE
- [x] `conftest.py` autouse `_reset_settings_cache` + COCKPIT_TEST_MODE=1
- [x] `test_log_tail.py` (8 tests) — boundary cases for the reverse-tail iterator
- [x] `test_totp.py` (10 tests) — full enroll/verify/disable lifecycle + the verify-only-reads-committed security check
- [x] `test_jwt_validator.py` (4 tests) — empty / short / valid-hex / base64
- [x] `test_csv_formula_guard.py` (5 tests) — formula prefix detection + UnicodeDecodeError boundary
- [x] `test_proposal_status_literal.py` (2 tests) — 11 variants exposed in OpenAPI as enum
- Note: existing `test_shell.py`, `test_auth.py`, `test_proposal_status_coverage.py` from Phase-2 retained; total backend test count now 33+

---

## P3 — Phase 3 (post-customer-validation)

### BL-140: Backend imports CLIs as Python functions instead of subprocess — DEFERRED
**Status:** DEFERRED to post-customer-validation. ~200ms fork+import tax per kill-switch click is not yet user-noticed; the security boundary (separate process, separate args validation in `shell.py`) is load-bearing.
**Re-open trigger:** telemetry shows kill-switch latency complaints, OR a benchmark proves the tax matters for hot paths.

### BL-141: Remote audit shipping — DEFERRED-WITH-RATIONALE
**Status:** DEFERRED. Initial proposal (Pushover digest channel) was reviewed and rejected by the plan-review pass:
1. Pushover free tier = 10k messages/month per app token; sustained week-1 traffic could exhaust within 2 months
2. Pushover body limit 1024 bytes — long audit details get silently truncated
3. No delivery-acknowledgment in current `auth.py` Pushover pattern → claimed "tamper-evidence channel" silently degrades to file-only
**Recommended sinks for actual implementation:** Papertrail / Logtail (50 GB/month free, structured JSON, no per-event size limit) or syslog-over-TLS to rsyslog.
**Mitigation in v1:** `chattr +a` on `cockpit-audit.log` + nightly GPG-encrypted backup ships off-disk via the existing `shift-agent-backup.sh` flow.

### BL-142: JWT secret rotation procedure — ✅ DONE
- [x] `web/deploy/rotate-jwt-secret.sh` — touches ONLY `/opt/shift-agent/state/.cockpit-jwt-secret` (never `.env`); narrow blast radius
- [x] `systemctl restart --wait` blocks until service is ready; `/health` 5x-retry probe verifies cockpit came back up
- [x] Rolls back to previous secret if restart fails; exits 2 on health-probe fail (operator intervention)
- [x] Monthly cron template `web/deploy/jwt-rotate.cron`

### BL-143: Multi-user RBAC — DEFERRED (out of v1 scope)

### BL-144: Bulk roster import (CSV) — ✅ DONE
- [x] `POST /roster/import-csv` (require_fresh_otp), atomic full-replace inside `roster_session()` flock
- [x] Formula-injection guard: rejects cells starting with `= + - @ \t` (after `lstrip`); rejects CR/LF inside cells
- [x] UnicodeDecodeError → explicit 422 with re-save guidance (not 500)
- [x] 256 KB payload cap; per-row Pydantic validation; full Roster re-validation post-replace
- [x] Frontend `<CsvImport>` card on Roster section with `accept=".csv,text/csv"` + client-side size pre-check + ARIA label

### BL-145: Employee self-service — DEFERRED (out of v1 scope)

### BL-146: Multi-location within one customer — DEFERRED (single-location customer)

---

## Done (recent) — for context

- ✅ 2026-04-27 — Phase-3 post-cockpit work (overnight): all-feasible-items batch — BL-110/120 P1 hardening, BL-130 typed openapi-fetch, BL-131 supersede with `as const satisfies`, BL-133 TOTP fallback, BL-134 6 new pytest files (+29 tests), BL-142 JWT rotation, BL-144 CSV import. 11 commits on `feat/post-cockpit-phases`. 3-agent plan + 3-agent design + 3-agent code reviews applied.
- ✅ 2026-04-26 — Phase-2 owner cockpit MVP: 64 files, FastAPI + React+TS, 10 sections, OTP auth, SSE re-pair, kill switch, audit log w/ chattr +a, deploy artifacts, 3 review rounds applied (commits `c2e9505` + `5a1a7fd`)
- ✅ 2026-04-26 — Phase-1 rehearsal Steps A+B with 2-phone setup: P0006 created via owner-reports-sick path, code `#75HTY`, candidate e007, real outbound to `+19802005022` at 21:04:18 UTC
- ✅ 2026-04-26 — Re-pair WhatsApp linked device via in-chat QR (after `+918522041562` Meta-blocked from linking)
- ✅ 2026-04-26 — Hermes self-chat mode flip (was `--mode bot`, dropping fromMe self-chat)
- ✅ 2026-04-25 — Pytest baseline 71/71 passing on VPS, including new dry-run E2E test
- ✅ 2026-04-24 — Phase-0 rehearsal P0004 sent end-to-end, 5 real bugs found and fixed (Step C remained blocked by one-phone setup)
