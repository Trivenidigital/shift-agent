# Shift Agent

WhatsApp-based sick-call coverage agent for SMBs (ethnic grocery / restaurant / similar).

An employee messages the owner "sick, can't come tomorrow." The agent:

1. Confirms who/when/why (respecting code-switched Telugu / Hindi / Tamil / Gujarati phrasing)
2. Looks up the roster + next-day schedule
3. Identifies eligible coverage candidates (`can_cover_roles` match, not already scheduled, language match preferred)
4. Proposes coverage to the owner in their WhatsApp self-chat with a 5-character approval code
5. When the owner approves with the code, sends the coverage message to the candidate on the owner's behalf
6. Tracks candidate YES/NO response and notifies the owner

Every step is logged to an append-only NDJSON audit trail (`decisions.log`), guaranteed via a deterministic tail-logger independent of LLM behavior.

## Directory structure

```
PLAN.md              — rollout plan (v2, post-review)
DESIGN.md            — detailed implementation design (v2, post-review)
review-notes/        — 5-agent review findings + synthesis
src/
├── schemas.py       — Pydantic models (single source of truth for every data file)
├── safe_io.py       — atomic I/O, flock, corruption-recovery helpers
├── exit_codes.py    — shared exit-code constants
├── config.yaml.template
├── scripts/         — helper binaries + systemd-invoked scripts
├── skills/          — Hermes SKILL.md files (dispatch + handlers)
├── templates/       — outbound message templates (never LLM free-text)
├── systemd/         — unit files for all services + timers
├── logrotate/       — log rotation config
└── runbook.md       — customer-facing ops manual
```

## Quick architectural overview

- **Runtime:** Hermes Agent (Nous Research) + Kimi K2-thinking via OpenRouter, running as `shift-agent` systemd service (non-root) on a single Ubuntu VPS.
- **State:** JSON-on-disk with `fcntl.flock` concurrency protection + `os.replace`/`fsync` atomic writes + Pydantic validation on every read.
- **Audit:** Dual-source — LLM-enriched entries via helper scripts + guaranteed raw_inbound entries via tail-logger timer.
- **Out-of-band alerts:** Pushover (required) + optional healthchecks.io external heartbeat.
- **Approval flow:** owner replies to self-chat with a 5-character code (e.g., `#A3F2X`) — eliminates ambiguity with multiple pending proposals.
- **Outbound safety:** hardened `send-coverage-message <proposal_id>` script re-resolves candidate phone from roster, enforces daily cap under lock, writes `outbound_attempted` before POST for idempotency, supports `RETRY` on failure.

See `DESIGN.md` for full spec.

## Deploy

After customer data is populated and sign-offs collected, see `DESIGN.md §14` for the build order and `scripts/shift-agent-deploy.sh` for the git-tagged deploy flow.
