# Owner Cockpit — Frontend Plan v1

**Status:** plan, awaiting parallel-agent review
**Author:** Claude Opus 4.7 / 2026-04-26
**Drives:** end-to-end self-service for the SMB customer to operate Shift Agent without SSH or JSON edits

---

## 1. Goal

Single web app that lets the customer (a non-technical SMB owner) do **everything** they currently need a runbook + SSH for, plus emergency control. Removes the developer-on-call dependency for routine ops.

---

## 2. Personas + jobs-to-be-done

| Persona | Top-3 jobs |
|---------|-----------|
| **Owner** (primary) | (1) approve/cancel proposals; (2) edit roster + schedule; (3) hit kill switch when things go wrong |
| **Owner during onboarding** | (1) link WhatsApp device via QR; (2) populate roster from a CSV/form; (3) sign 3 disclosures |
| **Developer (Srini)** during beta | (1) audit decisions; (2) flip dry-run mode; (3) view health |

Out of scope (Phase 3): multi-user, RBAC, multi-location, employee-self-service.

---

## 3. Scope (in)

| # | Section | Functions |
|---|---------|-----------|
| **3.1** | **Auth** | Login by owner-phone OTP via Pushover (one-time code sent to owner's Pushover) → 24h session cookie. Single user. |
| **3.2** | **Dashboard** | Live status: gateway/bridge/WA-paired/Pushover/disk/cap. Today's send-counter (1/2). Last-3 decisions. Big red kill-switch toggle. |
| **3.3** | **Roster** | Table of employees (name, role, phone, can_cover, status). Add / edit / terminate / reactivate. Phone canonicalized (E164Phone) before save. Phone-history written when phone changes. |
| **3.4** | **Schedule** | Weekly grid (employee × day). Click to add shift, drag to move, click X to delete. Validate: shift fits role, no double-booking. |
| **3.5** | **Owner profile** | Name, phone, self-chat JID (auto-detected from creds.json). Phone change triggers re-pair recommendation. |
| **3.6** | **WhatsApp pairing** | Status card (paired number, registered, uptime, last inbound ts). "Re-pair device" button → SSE stream → QR rendered as `<canvas>` from server-side data → user scans → success poll → updated status. "Unlink device" button. |
| **3.7** | **Pending proposals** | Live table (proposal_id, code, absent, candidate, status, age). Cancel button per row (POST /pending/{id}/cancel). Hide terminal-status proposals older than 24h. |
| **3.8** | **Decisions log** | Filterable timeline (by date, type, proposal_id, employee). Tail-mode toggle (poll every 10s). Export CSV. |
| **3.9** | **Config** | `max_outbound_per_day`, `max_outbound_per_minute`, `pending_proposal_ttl_hours`, languages, timezone, business_hours. Pushover keys (masked, "rotate" button). GPG recipient email. SHIFT_AGENT_DRY_RUN toggle. |
| **3.10** | **Safety** | Kill switch (touch/remove disabled.flag). Health pulses (gateway, bridge, openrouter credit, disk, last-backup). Manual "send test alert" button. |
| **3.11** | **Disclosures** | 3 acknowledgments (Baileys ToS / audit immutability / employee notification). Stores signature + ts + IP. Show date last signed. |
| **3.12** | **Audit log** | Who-did-what-when on the cockpit itself (login, roster edits, kill-switch toggles). Append-only NDJSON. |

---

## 4. Out of scope (Phase 3+)

- Multi-user with roles (manager, owner, employee)
- Employee self-service (request time off, swap shifts)
- CSV/Google Sheets bulk import
- Real-time chat with the agent (chat UI to LLM)
- Mobile-native app (React Native)
- Internationalization (English only for v1)

---

## 5. Tech stack

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | **React 19 + TypeScript + Vite + Tailwind 4 + shadcn/ui** | Modern stack, owner-grade polish, fast cold dev cycle |
| State | TanStack Query for server state, useState for local | No Redux complexity needed |
| Forms | React Hook Form + Zod | Validation matches backend schemas.py |
| Charts | recharts (only for decisions timeline) | Light, composable |
| Backend | **FastAPI (Python 3.11) on the same VPS** | Wraps existing CLIs (`create-proposal`, etc) directly via `subprocess`; reads/writes state via existing `safe_io` module |
| Auth | Pushover-OTP → JWT (HS256) in HttpOnly cookie | No new infra (Pushover already wired); owner already has app installed |
| WhatsApp QR | Server-Sent Events stream from FastAPI; client renders QR from base64-encoded text using `qrcode.react` | Keeps the existing Baileys QR data path; no extra deps |
| Reverse proxy | **Caddy** (auto Let's Encrypt) on `cockpit.<customer-domain>` | TLS w/o manual cert mgmt |
| Process | systemd unit `shift-agent-cockpit.service` running as `shift-agent` user | Same security model as existing services |

---

## 6. API surface (FastAPI)

All authenticated except `/auth/*` and `/health`.

```
POST /auth/request-otp          → triggers Pushover OTP to owner.phone, rate-limited
POST /auth/verify-otp           → returns JWT cookie, 24h
POST /auth/logout
GET  /auth/me

GET  /health                    → public, returns {gateway, bridge, wa_status, openrouter, disk, last_backup}
GET  /dashboard                 → aggregated dashboard payload
POST /safety/disable            → touches disabled.flag, sends Pushover "kill engaged"
POST /safety/enable             → removes disabled.flag, sends Pushover "kill released"
POST /safety/test-alert         → sends test Pushover

GET  /roster                    → full roster
POST /roster/employee           → add employee (validates via schemas.Roster)
PATCH /roster/employee/{id}     → update (handles phone_history)
DELETE /roster/employee/{id}    → soft-terminate (sets status=terminated, keeps history)

GET  /schedule?from=&to=
PUT  /schedule/{date}           → replace day's shifts
POST /schedule/{date}/shift     → add one shift
DELETE /schedule/{date}/shift/{idx}

GET  /config                    → minus secrets
PATCH /config                   → guarded by extra Pushover-OTP for sensitive fields

GET  /whatsapp/status           → me.id, registered, last_seen, paired_at
POST /whatsapp/repair           → SSE stream: events qr|connected|error|complete
POST /whatsapp/unlink           → wipes session/, restarts bridge

GET  /pending                   → all proposals, filterable
POST /pending/{id}/cancel       → owner-initiated cancel

GET  /decisions?from=&to=&type=&proposal_id=&employee_id=
GET  /decisions.csv             → export

GET  /disclosures               → status + last-signed
POST /disclosures/sign          → records signature

GET  /audit                     → cockpit's own audit log
```

---

## 7. Page layout (single page, sectioned)

```
┌──────────────────────────────────────────────────┐
│ ⚕ Shift Agent Cockpit       [● healthy] [logout] │
├─────────────┬────────────────────────────────────┤
│ Dashboard   │                                    │
│ Roster      │      <main content>                │
│ Schedule    │                                    │
│ Pending     │                                    │
│ Decisions   │                                    │
│ Config      │                                    │
│ WhatsApp    │                                    │
│ Disclosures │                                    │
│ Audit       │                                    │
├─────────────┴────────────────────────────────────┤
│ [🔴 KILL SWITCH]  Counter 1/2   Last sync: 2s ago │
└──────────────────────────────────────────────────┘
```

Single SPA, no router-driven page reloads. Section is `useState`. Bottom bar is sticky and always visible (kill switch + send counter + sync indicator).

---

## 8. Security model

1. **Auth**: Pushover-OTP → JWT (HS256, server-side secret in /opt/shift-agent/.env). Cookie HttpOnly + Secure + SameSite=Strict.
2. **Rate-limit OTP**: max 3/15min per IP, max 5/hour per owner phone (slowloris guard).
3. **Single-user**: owner.phone in config.yaml is the only valid OTP target. No registration.
4. **CSRF**: SameSite=Strict + double-submit token on state-mutating routes.
5. **CORS**: same-origin only (Caddy serves static + API on same domain).
6. **Sensitive-action MFA**: re-prompt OTP for `/whatsapp/unlink`, `/config (Pushover/GPG)`, `/disclosures/sign`.
7. **Audit**: every authenticated mutation appends to `/opt/shift-agent/logs/cockpit-audit.log` (NDJSON).
8. **Backend runs as `shift-agent` user**: same as existing services. No new privilege.
9. **Frontend never holds secrets**: backend masks Pushover keys etc.
10. **TLS**: Caddy auto-provisions Let's Encrypt; HSTS enabled.

---

## 9. Deployment

- New systemd unit: `shift-agent-cockpit.service` (FastAPI/uvicorn on `127.0.0.1:8080`)
- Caddy reverse-proxy: `cockpit.<domain>` → `localhost:8080` (API) + serves `dist/` static (frontend build)
- Frontend built locally, `dist/` `scp`'d to `/opt/shift-agent/cockpit/static/`
- DNS: add `cockpit.<domain>` A record pointing to VPS IP
- Backup: cockpit-audit.log included in nightly tarball

---

## 10. Risks + mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Owner OTP delivery fails (Pushover down) | High | Fallback: SMS via Twilio (Phase 3); for now, document SSH escape hatch |
| Frontend bug locks out owner | High | Backend remains controllable via existing CLIs over SSH; cockpit failure ≠ system failure |
| QR render fails in browser | Med | Same-as-CLI fallback: cockpit shows the QR data string for `ssh main-vps "sudo cat /tmp/pair_out.txt"` flow |
| Concurrent edits | Low | All state files use `safe_io.flock`; backend acquires lock before write |
| Owner accidentally hits kill | Low | Kill button has 2-step confirm + reason text input |
| Frontend stack drift | Low | Shadcn/ui pinned, Tailwind 4 + React 19 LTS-ish for v1 lifetime |

---

## 11. Build sequence

1. Backend skeleton: FastAPI app, JWT, dotenv loader, sit on port 8080
2. Auth flow (OTP via Pushover) + middleware
3. Endpoints in order: `/health`, `/dashboard`, `/roster` (R), `/safety`, `/whatsapp/status` (R)
4. Frontend skeleton: Vite, Tailwind, shadcn, layout, auth UI
5. Wire dashboard + roster (R) + safety
6. Roster CRUD (write paths)
7. Schedule + pending + decisions (R)
8. WhatsApp pair flow (SSE)
9. Config + disclosures + audit
10. Caddy + systemd configs
11. PR + review

---

## 12. Acceptance criteria

- Owner can log in with their phone via Pushover OTP
- Owner can view live dashboard with health + counter + last 3 decisions
- Owner can add/edit/terminate an employee, change persists, identify-sender resolves correctly after
- Owner can edit a day's schedule, change persists in roster.json
- Owner can re-pair WhatsApp via QR rendered in browser without SSH
- Owner can engage/release kill switch from the cockpit
- Owner can view + filter decisions log for last 30 days
- Pytest smoke for every backend endpoint passes
- Cockpit doesn't break existing CLI flows (regression: rerun `pytest tests/`)
