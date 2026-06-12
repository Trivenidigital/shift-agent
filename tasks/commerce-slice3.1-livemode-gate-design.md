# Commerce slice-3.1 — Stripe livemode-match deploy gate (design)

**Drift-check tag:** `extends-Hermes` — a deploy-time gate that reads
`CommerceConfig` + the operator's `.env` and calls the Stripe REST API via raw
`urllib` (stdlib); mirrors the deployed `vision-auth-smoke` gate. No new SDK, no
new substrate.

**Status:** Item 2 of the 2026-05-29 autonomous run. Approved line of work
("activation-safe hardening"). Dormant-safe.

## Hermes-first capability checklist

| Step | Hermes? | Decision |
|---|---|---|
| Read `cfg.commerce.{enabled,provider,stripe_livemode_expected}` | `[Hermes]` — Pydantic `Config`/`CommerceConfig` (fields already exist) | use it |
| Read `STRIPE_API_KEY` from `/opt/shift-agent/.env` | `[Hermes]` — same `.env` pattern `vision-auth-smoke` uses (`SHIFT_AGENT_ENV_PATH`) | mirror it |
| Call Stripe `GET /v1/account` over HTTPS | `[Hermes]` — stdlib `urllib`, exactly as `vision-auth-smoke` calls OpenRouter (no SDK) | mirror it |
| Applicability/skip + livemode compare + exit-code contract | `[net-new]` — gate logic | build (this PR) |
| Deploy wire-in + rollback | `[Hermes]` — existing pre-restart gate framework | extend it |

Ecosystem check: no Hermes/community skill "assert my Stripe key's livemode
matches my config at deploy time" — per-agent money-safety plumbing. Verdict:
thin net-new gate over stdlib + existing config/.env. Justified.

## Drift-rule self-checks

- ✅ Read `src/agents/catering/scripts/vision-auth-smoke` (full file) — the canonical "deploy gate reads API key from `.env` + calls external API via urllib, exit 0/1/2" pattern; this gate mirrors it (incl. `_read_api_key`, placeholder detection, retry/backoff on transient).
- ✅ Read `src/platform/schemas.py` (`CommerceConfig` line 2333 — `stripe_livemode_expected: bool = False`, `provider`, `enabled`).
- ✅ Read `src/platform/commerce_webhook_gate.py` (just-merged slice-3.5 gate) — reuse its dormant-applicability shape (`enabled and provider=="stripe"` else skip exit 0) and config-load helper idiom.
- ✅ Read `docs/runbooks/commerce-stripe-onboarding.md` (lines 218, 234, 238, 310) — confirms this is the documented slice-3.1 deferral; runbook Step 8 sets `stripe_livemode_expected`. Remediation message will point here.

## Runtime-state verification (§9a)

- main-vps dormant: `commerce.enabled=False`, `provider=placeholder`, no `STRIPE_API_KEY` in `.env`, `stripe` SDK not installed. The gate's skip path applies → no key read, no network, exit 0. (Using urllib means the absent SDK is irrelevant.)
- Active path (operator has set `provider=stripe` + a real key) is the only path that reads the key / hits the network — never reachable while dormant.

## The gate

New module `src/platform/commerce_livemode_gate.py` + wrapper
`src/platform/scripts/check-commerce-stripe-livemode`:

1. Load `cfg.commerce.{enabled, provider, stripe_livemode_expected}` from
   `--config` (default `/opt/shift-agent/config.yaml`) via `CommerceConfig`.
2. **Dormant-clean:** if `not (enabled and provider == "stripe")` → print
   "not applicable, skipping" and **exit 0**.
3. **Active path:** read `STRIPE_API_KEY` from env / `.env` (mirror
   `vision-auth-smoke._read_api_key`). If missing/placeholder → **exit 2**
   (config error). Else `GET https://api.stripe.com/v1/account` with
   `Authorization: Bearer <key>`; parse `livemode` (bool) from JSON.
   - `livemode == stripe_livemode_expected` → **exit 0**.
   - `livemode != stripe_livemode_expected` → **exit 1** (fail-closed:
     "live key in test config" / "test key in live config" footgun — the exact
     money-safety failure this gate exists to catch). Message states observed vs
     expected; **never logs the key**.
   - 401/403 → **exit 2** (key invalid → config error, can't determine).
   - timeout / 5xx / network / non-JSON → **exit 2** (transient; deploy
     blocks fail-closed rather than passing on uncertainty), after the same
     retry/backoff `vision-auth-smoke` uses.

Exit codes: `0` pass/skip, `1` livemode mismatch (fail-closed), `2` config/runtime error.

Testability: inject `key_reader` + `account_fetcher` (returns `livemode` bool or
raises) so tests need no real key, no network, no `stripe` SDK. Dormant tests
touch none of it.

## Deploy wiring

Add a pre-restart gate immediately after the slice-3.5 webhook-subscription gate
in `shift-agent-deploy.sh`, same shape: prefer the staging script path, fall back
to `/usr/local/bin`, invoke via `$VENV_PY`, fail-closed via the standard rollback
path. When the gate script is absent, the same commerce-active probe used by the
webhook gate decides hard-fail (active) vs WARN-skip (dormant). Install the module
rollback-guarded; wrapper stale-cleanup.

## Tests (TDD)

`tests/test_commerce_livemode_gate.py` — in-process, cross-platform:
1. dormant (enabled False) → exit 0 skip.
2. enabled + placeholder provider → exit 0 skip.
3. active + livemode matches expected (test/test, live/live) → exit 0.
4. active + mismatch (expected test, account live) → exit 1, message names observed vs expected, no key.
5. active + mismatch (expected live, account test) → exit 1.
6. active + missing/placeholder key → exit 2.
7. active + 401/403 from account_fetcher → exit 2.
8. active + transient (timeout/5xx) after retries → exit 2.
9. malformed/invalid config → exit 2.

## Explicitly deferred (unchanged)

- PR 4 (refund/chargeback) — still deferred until Stripe activated / first paying
  customer.
- Audit-log freshness watchdog (§12a) — deferred: event-driven audit log has
  false-alarm risk on quiet pilot VPSes; needs live write-rate data + likely a
  heartbeat design. Separate, larger item.
