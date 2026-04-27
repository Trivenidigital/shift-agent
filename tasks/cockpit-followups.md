# Cockpit Phase-2 — Deferred Items

These items came from the 3-agent PR review of commit c2e9505 (feat/owner-cockpit). They are **not blockers** for first-deploy but should be addressed before week-2 traffic. Listed in priority order.

## Critical fixes already applied (this commit)
1. ✅ `StickyFooter.tsx` — TS/JSX parse error in generic call removed
2. ✅ `PairSession` — Pydantic v2 `model_config = ConfigDict(...)` instead of `class Config:`
3. ✅ All `systemctl` calls in routers use absolute path + `shell=False`; mutating ones go via `sudo -n` with the deploy.sh-installed sudoers rule
4. ✅ SSE `event_gen` wrapped in `try…finally _kill_session(sid)` — closes the QR-leak vector when tab closes mid-pair
5. ✅ `App.tsx` schedules logout 60s before JWT `expires_at`
6. ✅ `Pending.tsx` uses imported `TERMINAL_STATUSES`, not inline string array
7. ✅ QR rendering: backend forwards ASCII-block QR rows as `qr_line` SSE events; frontend renders with `<pre>` (proven approach from rehearsal). Removed `qrcode.react` dep.
8. ✅ `ConfigPatch.fields` `max_length=20`; `EmployeeIn`/`EmployeePatch`/`DisclosureSign` field bounds added
9. ✅ Bridge `Popen` uses `os.open` fd tracked in `PairSession.fout_fd`; `_kill_session` closes it
10. ✅ `deploy.sh` provisions `/etc/sudoers.d/shift-agent` granting NOPASSWD systemctl stop/start/restart for hermes-gateway only

## Phase-2.1 (week 1 post-deploy)
- **`Settings()` JWT-secret validator**: add `Field(min_length=64)` so empty secret fails at startup, not at signing time. (Reviewer 2 H1)
- **Audit log reader (`/audit`) reverse-tail**: don't read entire file into memory; `seek(end) → reverse iterate`. Already a known production-bite predictor. (Reviewer 1 S6)
- **`/decisions` log reader**: same — bounded reverse-tail with on-disk JSON cache for fast paginate. (Reviewer 1 S5)
- **Audit log rotation procedure**: documented in `web/deploy/logrotate.conf` with chattr toggle, but never tested under load. Schedule a simulated 1-year-of-traffic test before week 4.
- **Caddy `header_up X-Forwarded-For {remote}`**: explicit forwarding so backend `client_ip()` isn't spoofable. Add to Caddyfile. (Reviewer 1 S7)
- **OTP audit-log latency vs timing equalization**: `safe_io.ndjson_append` happens before the wall-time floor enforcement. Re-measure `started → just before raise` and apply the floor *after* the audit write. (Reviewer 1 S2)

## Phase-2.2 (week 2-3)
- **OpenAPI-driven type generation actually wired**: the `package.json` script is there but the frontend uses a hand-rolled `api.ts`. Run `npm run generate:types`, switch `api.ts` to `openapi-fetch`. Removes whole class of frontend↔backend drift bugs. (Reviewer 1 N1)
- **`useSection` context-promoted**: currently each hook instance attaches its own `popstate` listener. Wrap in a single `<SectionProvider>`. (Reviewer 3 #2)
- **Bridge.js patch to emit raw QR data string** (in addition to qrcode-terminal output) so the frontend can render a real `<svg>` QR. Cleaner than ASCII art, scannable on any client.
- **TOTP fallback alongside Pushover OTP**: if Pushover delivery fails or is compromised, allow a TOTP authenticator. (Reviewer Plan-2 + Design-1)
- **Test coverage**: cover SSE flow, fresh-OTP gating, `roster_session` no-write-on-exception, dotted-path config patch, kill-switch CLI contract, audit log append. (Reviewer 1 S10)

## Phase-3 (post-customer-validation)
- **Backend imports CLIs as Python functions** instead of subprocess. ~200 ms saved per kill-switch click; unifies error reporting. (Reviewer 1 strategic observation)
- **Remote audit shipping**: ship cockpit-audit.log entries to a remote sink (Loki / Pushover / syslog) as they happen — closes the local-tamper window between writes and nightly backup.
- **JWT secret rotation procedure**: documented + automated. Currently a manual `.env` edit + restart that invalidates all sessions.
- **Multi-user RBAC**: not in scope for v1 (single-tenant SMB), but if we add a manager role, redesign auth.

## Issues acknowledged but deliberately NOT fixed
- **Hand-rolled UI primitives** (Button/Card/Input) instead of shadcn-CLI: shortcut taken because Radix deps + CVA are already in `package.json`; running `npx shadcn@latest init` after Tailwind 4 is straightforward when next dev wants to extend.
- **No client-side form validation in Roster/Schedule**: server validates Pydantic-rigorously; rejecting at the form is UX polish, not safety. (Reviewer 3 #6)
- **Mobile table layout for Roster**: the `<table>` works on phones with horizontal scroll; conversion to card stack is Phase-2.2.
- **`X-Frame-Options DENY` + Caddy CSP** are sufficient embedded-context defense for v1; per-route framing options not needed.
