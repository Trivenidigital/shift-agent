# Shift Agent — Owner Cockpit

Single-page web admin for the SMB customer to manage the Shift Agent without SSH or JSON edits.

## Layout

```
web/
├── backend/      FastAPI (Python 3.11)
├── frontend/     React 19 + TS + Vite + Tailwind 4 + shadcn-style
└── deploy/       systemd + Caddy + logrotate
```

## Local development

```bash
# Backend (assumes /opt/shift-agent state is reachable; for offline dev use a mock state dir)
cd web/backend
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --reload --port 8080
```

```bash
# Frontend
cd web/frontend
npm install
npm run generate:types   # requires backend running on :8080
npm run dev              # → http://localhost:5173 (proxies /api/* → :8080)
```

## Deploy

```bash
bash web/deploy/deploy.sh main-vps
```

Idempotent. After first run, edit `/etc/caddy/Caddyfile.cockpit` to point at your real domain, then `sudo systemctl reload caddy`.

## Auth

Login is via **Pushover OTP** to the owner phone configured in `/opt/shift-agent/config.yaml`. The owner installs Pushover on their phone and uses the same user_key + app_token configured for the agent. There is no user registration — only the configured owner phone can receive OTPs.

Sessions are 24h, JWT in HttpOnly+Secure+SameSite=Strict cookie. Sensitive actions (changing owner phone, Pushover keys, GPG email, daily cap, signing disclosures, unlinking WhatsApp, exporting decisions CSV) require a **fresh** OTP — the JWT must be ≤5 min old. Re-verify via the login screen.

## Sections

- **Dashboard**: live system health, send counter, last decisions
- **Roster**: add/edit/terminate employees; phone history written automatically
- **Schedule**: weekly grid of shifts; same flock as roster (atomic)
- **Pending**: live proposal table, all 11 statuses with badge UI, cancel button (CLI is source of truth on legality)
- **Decisions**: filterable audit timeline; CSV export (fresh-OTP gated, PII)
- **Config**: limits, owner profile, customer info, masked Pushover keys
- **Safety**: kill switch with 2-step confirm + reason; test alert button
- **WhatsApp**: pairing status, in-browser SSE re-pair flow with QR rendered live, unlink
- **Disclosures**: 3 signed acknowledgments (Baileys ToS / audit immutability / employee notification)
- **Audit**: cockpit's own activity log

## Security model

- Single-user (the owner's phone). No registration → no enumeration.
- Pushover OTP → JWT 256-bit-secret HS256 → HttpOnly+Secure+SameSite=Strict cookie.
- 5-attempt lockout on OTP verify; hmac.compare_digest; ≥200 ms wall-time floor (timing-equalize).
- All shell calls go through `app/shell.py` with a strict allow-list and `--` terminator before user-supplied args.
- Audit log is `chattr +a` at deploy → cockpit can append, cannot truncate.
- Caddy enforces TLS + HSTS + CSP.
- Backend runs as `shift-agent` user — same security context as the agent itself.

## Phase-3 known gaps (deferred)

- TOTP fallback alongside Pushover OTP
- Remote audit shipping (Loki/Pushover) — currently on-disk only
- JWT secret rotation procedure (currently manual via .env edit + restart)
- Bridge log noise: bridge.js patches to emit raw QR data string in addition to ANSI render

See `tasks/frontend-design.md` for the full design + reviewer feedback log.
