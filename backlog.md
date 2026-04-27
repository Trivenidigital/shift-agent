# Shift Agent — Backlog

**Last updated:** 2026-04-27 (go-live day)

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

### BL-110: Pre-existing Phase-1 known issues (from GO-LIVE-HANDOFF.md §"Remaining known issues")
- [ ] `jq` install on VPS — required by health-check (`apt-get install -y jq`)
- [ ] `backup.sh` GPG `--trust-model always` → pin to fingerprint
- [ ] `backup.sh` YAML parsed via grep|sed — replace with `yaml.safe_load`
- [ ] `_revert_everything` uses stale proposal snapshot — Phase-1: re-read under lock
- [ ] `create-proposal` log-write happens after pending-lock release — race window; reorder

### BL-120: Cockpit Phase-2.1 follow-ups (from `tasks/cockpit-followups.md`)
- [ ] `Settings` JWT-secret startup validator (`Field(min_length=64)` so empty secret fails fast)
- [ ] `/audit` reverse-tail reader (don't load whole file into memory; will OOM in <1 yr)
- [ ] `/decisions` reverse-tail reader + bounded on-disk cache for fast paginate
- [ ] Audit log rotation procedure: simulated 1-year-traffic test under load
- [ ] Caddyfile: explicit `header_up X-Forwarded-For {remote}` to prevent client_ip spoofing
- [ ] OTP audit-log latency vs timing equalization: move 200ms wall-time floor to AFTER audit write

---

## P2 — Cockpit Phase-2.2 enhancements (within month 1)

### BL-130: Frontend↔backend type-safety
- [ ] Wire `npm run generate:types` (already in package.json) → switch `api.ts` from hand-rolled `fetch` to `openapi-fetch` typed client. Eliminates entire schema-drift bug class.

### BL-131: useSection context-promote
- [ ] Replace per-hook-instance `useSection` with `<SectionProvider>` wrapping `<App>`. Single popstate listener. (Reviewer 3 #2)

### BL-132: Bridge.js patch for raw QR data string
**File:** `/root/.hermes/hermes-agent/scripts/whatsapp-bridge/bridge.js`
**Change:** in the `connection.update` handler, also `console.log(JSON.stringify({event:"qr_data", data: qr}))` alongside the qrcode-terminal render.
**Why:** Cockpit currently scrapes the ASCII-block QR rows; brittle. With raw data emit, frontend can use `<svg>` QR (cleaner, scannable on any device).

### BL-133: TOTP fallback for cockpit auth
**Why:** Pushover-only OTP is brittle. If Pushover delivery is down or owner's Pushover account is compromised, the cockpit is locked out.
**Change:** add `pyotp` enrollment endpoint + accept TOTP code as alternative to Pushover OTP.

### BL-134: Broaden cockpit pytest coverage
**Files:** `web/backend/tests/`
**Tests to add:**
- SSE pair flow (mock subprocess + drive event_gen)
- `require_fresh_otp` enforcement on every sensitive route
- `roster_session` no-write-on-exception
- Dotted-path config patch round-trip
- Kill-switch CLI contract (mock `shift-agent-disable`)
- Audit log append + rotation under load

---

## P3 — Phase 3 (post-customer-validation)

### BL-140: Backend imports CLIs as Python functions instead of subprocess
**Why:** ~200ms fork+import tax per kill-switch click. Unifies error reporting (no stderr parsing). Eliminates the data-consistency window where `pending.cancel` writes via subprocess but `pending.list` reads JSON directly. (Reviewer 1 strategic observation)
**Risk:** breaks the security boundary if CLIs ever import unsafe modules. Audit before flipping.

### BL-141: Remote audit shipping
**Why:** `cockpit-audit.log` + `decisions.log` are on the same disk as the system being audited. `chattr +a` only prevents truncation — not arbitrary write at root level. Phase 3: stream entries to a remote sink (Loki / Pushover / syslog) as they happen.

### BL-142: JWT secret rotation procedure
**Why:** currently a manual `.env` edit + restart that invalidates all sessions. Document + automate for monthly rotation.

### BL-143: Multi-user RBAC
**Why:** v1 is single-tenant single-user (the SMB owner). If we add a manager / employee role, the auth model needs a redesign — single-row OTP store + JWT-as-only-credential won't scale.

### BL-144: Bulk roster import (CSV / Google Sheets)
**Why:** typing 45 employees into the cockpit form is friction. CSV upload with schema validation is the standard SMB UX.

### BL-145: Employee self-service (request time off, swap shifts)
**Why:** out of scope for v1. Would need a separate auth identity per employee.

### BL-146: Multi-location within one customer
**Why:** scoping complexity (`location_id` propagation through every query). Defer until customer asks.

---

## Done (recent) — for context

- ✅ 2026-04-26 — Phase-2 owner cockpit MVP: 64 files, FastAPI + React+TS, 10 sections, OTP auth, SSE re-pair, kill switch, audit log w/ chattr +a, deploy artifacts, 3 review rounds applied (commits `c2e9505` + `5a1a7fd`)
- ✅ 2026-04-26 — Phase-1 rehearsal Steps A+B with 2-phone setup: P0006 created via owner-reports-sick path, code `#75HTY`, candidate e007, real outbound to `+19802005022` at 21:04:18 UTC
- ✅ 2026-04-26 — Re-pair WhatsApp linked device via in-chat QR (after `+918522041562` Meta-blocked from linking)
- ✅ 2026-04-26 — Hermes self-chat mode flip (was `--mode bot`, dropping fromMe self-chat)
- ✅ 2026-04-25 — Pytest baseline 71/71 passing on VPS, including new dry-run E2E test
- ✅ 2026-04-24 — Phase-0 rehearsal P0004 sent end-to-end, 5 real bugs found and fixed (Step C remained blocked by one-phone setup)
