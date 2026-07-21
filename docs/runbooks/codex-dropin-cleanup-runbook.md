# Runbook — Foreign Codex drop-in cleanup: flyer-recovery-watchdog (main-vps)

**Status:** PREPARED 2026-07-20. Containment executed (timer disabled); cleanup steps
below are NOT executed — each requires the named authorization. Re-enabling the
timer requires separate focused review AFTER the drop-in correction.

## 1. Incident summary

From ~2026-07-20T17:53 local, the owner received a priority-2 WhatsApp alert
("Flyer recovery watchdog failed") every ~5 minutes (~288/day). Root cause:
`flyer-recovery-watchdog.service` on main-vps carries two foreign drop-in
overrides that re-point the unit at an **OpenAI Codex CLI worker** whose refresh
token is permanently invalid ("refresh token was already used"; HTTP 401 on
`wss://chatgpt.com/backend-api/codex/responses`). `ExecStartPre=/usr/local/bin/codex-auth-guard`
exits 22 → unit fails → `OnFailure=flyer-recovery-watchdog-failure.service`
dispatches the owner alert → repeat on the 5-minute timer. Pushover leg 400s
(muted dev keys), WhatsApp fallback delivers.

Codex is outside this stack's intended tooling (standing project rule:
subagent reviewers, no external peer-vendor CLIs). No repo source ships these
drop-ins; they exist only as on-box mutations of unknown provenance.

## 2. Captured provenance (verbatim, 2026-07-20T23:1xZ)

`/etc/systemd/system/flyer-recovery-watchdog.timer` (repo-consistent):

```ini
[Unit]
Description=Flyer Studio recovery watchdog timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
AccuracySec=30s
Unit=flyer-recovery-watchdog.service
[Install]
WantedBy=timers.target
```

`/etc/systemd/system/flyer-recovery-watchdog.service` (repo-consistent base unit):

```ini
[Unit]
Description=Flyer Studio recovery watchdog
After=hermes-gateway.service
OnFailure=flyer-recovery-watchdog-failure.service
[Service]
Type=oneshot
User=shift-agent
Group=shift-agent
Environment=HOME=/opt/shift-agent
ExecStart=/usr/local/lib/hermes-agent/venv/bin/python /usr/local/bin/flyer-recovery-watchdog --text
StandardOutput=append:/opt/shift-agent/logs/flyer-recovery-watchdog.log
StandardError=append:/opt/shift-agent/logs/flyer-recovery-watchdog.log
```

**FOREIGN** `/etc/systemd/system/flyer-recovery-watchdog.service.d/10-codex-worker-root.conf`:

```ini
[Service]
User=root
Group=root
Environment=HOME=/root
```

**FOREIGN** `/etc/systemd/system/flyer-recovery-watchdog.service.d/20-codex-service-auth.conf`:

```ini
[Service]
Environment=CODEX_HOME=/var/lib/codex-service
Environment=HOME=/root
EnvironmentFile=-/etc/codex-openai.env
ExecStartPre=/usr/local/bin/codex-auth-guard
```

Associated foreign artifacts (inventory only — do not touch without ruling):
`/usr/local/bin/codex-auth-guard`, `/etc/codex-openai.env`,
`/var/lib/codex-service/`, and the Codex session content interleaved into
`/opt/shift-agent/logs/flyer-recovery-watchdog.log`.

Note: the drop-ins run the watchdog as **root**; the base unit runs it as
`shift-agent`. Root-run writers re-own state files (see the 2026-07-20
`projects.json` root-ownership incident in the P1-1 recovery record) — a second,
independent reason the drop-ins are harmful.

## 3. Containment state (executed 2026-07-20, reviewer-authorized)

- `systemctl disable --now flyer-recovery-watchdog.timer` → `disabled` /
  `inactive` (symlink `/etc/systemd/system/timers.target.wants/…` removed).
- Service and drop-ins NOT edited or deleted. No auth changes.
- Verified: zero recovery-watchdog fires after the final in-flight 23:24:45Z
  alert; gateway + unrelated watchdogs healthy; no customer/project state
  changed by containment.
- Reversal (if ever ruled): `systemctl enable --now flyer-recovery-watchdog.timer`.

## 4. Cleanup steps (HELD — each numbered step needs explicit authorization)

1. **Quarantine drop-ins** (reversible, preferred first step):
   `mkdir -p /root/quarantine/codex-dropins-20260720 && mv /etc/systemd/system/flyer-recovery-watchdog.service.d/*.conf /root/quarantine/codex-dropins-20260720/ && systemctl daemon-reload`.
   Do NOT `rm`. The base unit then stands alone (shift-agent user, real watchdog
   binary).
2. **Verify base unit sanity without enabling the timer**: one manual
   `systemctl start flyer-recovery-watchdog.service`; expect clean oneshot exit,
   a real watchdog log line (not Codex output), no state-file ownership changes
   (`ls -la /opt/shift-agent/state/flyer/projects.json` unchanged).
3. **Quarantine the wider Codex surface** (separate ruling; inventory in §2):
   `codex-auth-guard`, `/etc/codex-openai.env`, `/var/lib/codex-service/`.
   Investigate provenance (installation date, `dpkg -S`, shell history) before
   removal; preserve copies in the quarantine dir.
4. **Disposition decision for the watchdog itself** (operator ruling):
   - *Restore*: re-enable the timer running the genuine
     `flyer-recovery-watchdog` binary (step 2 must be green first), OR
   - *Formally retire*: if the recovery worker's function is superseded, remove
     timer+service via a reviewed deploy-script change (pattern: the PR #629
     artifact-aware removal), never by ad-hoc deletion.
5. **Log hygiene** (optional, after 1–4): rotate
   `flyer-recovery-watchdog.log` so Codex session content ages out; do not
   delete history.

## 5. Operational checks (after any step above)

- `systemctl list-timers | grep flyer-` — only intended timers active.
- `journalctl -u flyer-recovery-watchdog --since -1h` — no failures; no
  codex/token strings.
- Owner-alert stream: no "Flyer recovery watchdog failed" alerts for ≥ 2
  former intervals.
- `hermes-gateway` active; flyer + catering routing smoke unaffected
  (read-only replay checks only).
- State-file ownership audit: everything under `/opt/shift-agent/state/`
  owned `shift-agent:shift-agent`.

## 6. Re-enable criteria (timer stays off until ALL hold)

1. Drop-ins quarantined (step 1) and daemon reloaded.
2. Manual base-unit run green (step 2).
3. Focused review approves the restore-vs-retire disposition (step 4).
4. An explicit reviewer authorization names the re-enable.
